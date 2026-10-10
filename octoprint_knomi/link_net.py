# coding=utf-8
"""The KNOMI's own WiFi network (KNOMI firmware OP58+, OctoPrint build).

The KNOMI doesn't join your WiFi any more: it runs a small network of its own and the Pi joins it on a spare WiFi
interface (the built-in one when the Pi is on Ethernet, or a USB adapter). The KNOMI hands over the name and password
over the paired, encrypted Bluetooth link; this sets up a NetworkManager connection for it, marked so it's never the
Pi's way to the internet, and keeps it up. The password is never logged.
"""
import getpass
import hashlib
import os
import shutil
import subprocess
import threading
import time

CON_NAME = "knomi-link"
LINK_IP = "192.168.77.1"
CHECK_S = 30.0
# what NetworkManager needs to allow the OctoPrint user (it runs as a service, with no desktop session to ask)
NM_ACTIONS = ("org.freedesktop.NetworkManager.settings.modify.system",
              "org.freedesktop.NetworkManager.network-control")
# (that lets OctoPrint's user change and switch the Pi's network connections, which is what joining needs)


def permission_command(user=None):
    """The one-time command that lets OctoPrint's user manage the KNOMI's connection."""
    user = user or getpass.getuser()
    actions = ",".join('"{}"'.format(a) for a in NM_ACTIONS)
    rule = ('polkit.addRule(function(a,s){{if(s.user=="{u}"&&[{acts}].indexOf(a.id)>=0)return polkit.Result.YES;}});'
            .format(u=user, acts=actions))
    # named to sort first: rules run in filename order and the first answer wins, and Raspberry Pi OS ships one
    # that said no to changing system connections on a Pi 5 (so a rule named 50-… was never asked)
    return "echo '{}' | sudo tee /etc/polkit-1/rules.d/00-octoprint-knomi.rules".format(rule)


def parse_devices(text):
    """`nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device` -> [(device, state, connection)] for WiFi devices."""
    out = []
    for line in text.splitlines():
        parts = line.replace("\\:", "\0").split(":")
        parts = [p.replace("\0", ":") for p in parts]
        if len(parts) >= 4 and parts[1] == "wifi":
            out.append((parts[0], parts[2], parts[3]))
    return out


def parse_profiles(text):
    """`nmcli -t -f NAME,TYPE,AUTOCONNECT,DEVICE connection show` -> [(name, autoconnect, active device)] for WiFi."""
    out = []
    for line in text.splitlines():
        parts = [p.replace("\0", ":") for p in line.replace("\\:", "\0").split(":")]
        if len(parts) >= 4 and parts[1] in ("802-11-wireless", "wifi"):
            out.append((parts[0], parts[2] == "yes", parts[3] if parts[3] not in ("", "--") else ""))
    return out


def pick_interface(devices, wanted="", profiles=(), default_dev=""):
    """(interface, why-not). Prefers the one already on the KNOMI's network, then the configured one, then a free one.

    Picking on its own it's careful: the Pi may reach your network over WiFi, and taking that interface would cut it
    off. So it never takes an interface that has (or is getting) another connection, carries the default route, or
    could be joined by one of the Pi's own saved WiFi networks; for those, name the interface in the settings."""
    def busy(con):
        return con not in ("", "--", CON_NAME)
    for dev, state, con in devices:
        if con == CON_NAME:
            return dev, ""
    if wanted:
        for dev, state, con in devices:
            if dev == wanted:
                if busy(con):
                    return "", "{} is on {} (the Pi uses it for that)".format(dev, con)
                return dev, ""
        return "", "there's no WiFi interface called {}".format(wanted)
    # the Pi's own saved WiFi networks that would connect by themselves and aren't connected anywhere yet
    waiting = [n for n, auto, active in profiles if auto and not active and n != CON_NAME]
    reasons = []
    for dev, state, con in devices:
        if busy(con):
            reasons.append("{} is on {}".format(dev, con))
        elif state in ("unavailable", "unmanaged"):
            reasons.append("{} is {}".format(dev, state))
        elif dev == default_dev:
            reasons.append("{} is the Pi's way to the internet".format(dev))
        elif waiting:
            reasons.append("{} could join the Pi's saved network {}".format(dev, waiting[0]))
        else:
            return dev, ""
    if not devices:
        return "", "the Pi has no WiFi interface"
    return "", "no free WiFi interface ({})".format("; ".join(reasons))


def default_route_dev():
    try:
        out = subprocess.run(["ip", "-4", "route", "show", "default"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return ""
    parts = out.split()
    return parts[parts.index("dev") + 1] if "dev" in parts[:-1] else ""


def subnet_taken(ip, iface):
    """Another interface already uses the KNOMI network's addresses (your LAN is 192.168.77.x): routing would break."""
    prefix = ".".join(ip.split(".")[:3]) + "."
    try:
        out = subprocess.run(["ip", "-4", "-o", "addr", "show"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return ""
    for line in out.splitlines():
        f = line.split()
        if len(f) >= 4 and f[1] != iface and f[3].startswith(prefix):
            return f[1]
    return ""


class LinkNet(object):
    STATES = ("off", "waiting", "no networkmanager", "no interface", "needs permission", "joining", "up", "error")

    def __init__(self, logger, data_folder, iface_setting=lambda: ""):
        self._logger = logger
        self._data_folder = data_folder
        self._iface_setting = iface_setting
        self.state = "off"
        self.detail = ""
        self.iface = ""
        self.ssid = ""
        self._want = None          # (ssid, psk, ip) from the KNOMI
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._thread = None
        self._stop = False
        self._up_since = 0.0
        self._stale_addr = ""      # BlueZ keeps an old list of the KNOMI's characteristics (no network details)

    # ---- from the plugin ----------------------------------------------------

    def start(self):
        if self._thread:
            return
        self._stop = False
        self.state = "waiting"
        self.detail = "for the KNOMI's network details (they come over Bluetooth)"
        self._thread = threading.Thread(target=self._run, daemon=True, name="knomi-link-net")
        self._thread.start()

    def stop(self, down=False):
        """down: also leave the KNOMI's network (Bluetooth turned off in the settings)."""
        self._stop = True
        self._wake.set()
        t, self._thread = self._thread, None
        if t:
            t.join(timeout=5)
        self.state, self.detail = "off", ""
        if down and shutil.which("nmcli"):
            try:
                self._deactivate()
            except Exception:
                pass

    def offer(self, ssid, psk, ip=LINK_IP):
        """The KNOMI's network details, read over Bluetooth."""
        if not ssid or not psk or len(psk) < 8:
            return
        with self._lock:
            changed = self._want != (ssid, psk, ip)
            self._want = (ssid, psk, ip)
        if changed:
            self._logger.info("KNOMI network: the KNOMI offers %s", ssid)
            self._wake.set()

    def note_stale_cache(self, address):
        self._stale_addr = address or ""

    def is_up(self):
        return self.state == "up"

    def ip(self):
        w = self._want
        return w[2] if w else LINK_IP

    def check_soon(self):
        self._wake.set()

    def status(self):
        d = {"state": self.state, "detail": self.detail, "iface": self.iface, "ssid": self.ssid}
        if self.state == "needs permission":
            d["fix"] = permission_command()
        elif self.state == "waiting" and self._stale_addr:
            d["detail"] = ("the Pi's Bluetooth still has the KNOMI's list from before its update, without the network "
                           "details. Clear it once (the Pi re-reads it when it reconnects):")
            d["fix"] = ("sudo sh -c 'rm -v /var/lib/bluetooth/*/cache/{}' && sudo systemctl restart bluetooth"
                        .format(self._stale_addr.upper()))
        return d

    # ---- worker -----------------------------------------------------------

    def _run(self):
        while not self._stop:
            try:
                self._ensure()
            except Exception as e:
                self.state, self.detail = "error", str(e) or e.__class__.__name__
                self._logger.exception("KNOMI network")
            self._wake.wait(CHECK_S if self.state == "up" else 10.0)
            self._wake.clear()

    def _nmcli(self, *args, timeout=20):
        return subprocess.run(("nmcli",) + args, capture_output=True, text=True, timeout=timeout)

    def _fail(self, r, what):
        err = (r.stderr or r.stdout or "").strip()
        low = err.lower()
        if "not authorized" in low or "insufficient privileges" in low or "permission" in low:
            self.state = "needs permission"
            self.detail = "NetworkManager won't let OctoPrint's user set up the connection. Run the command below once on the Pi."
        else:
            self.state, self.detail = "error", "{}: {}".format(what, err[:200] or "failed")
        self._logger.info("KNOMI network: %s", self.detail)

    def _secret_tag(self, ssid, psk):
        # remembers which details the saved connection has, without keeping the password itself
        return hashlib.sha256((ssid + "\0" + psk).encode("utf-8")).hexdigest()

    def _tag_path(self):
        return os.path.join(self._data_folder, "link.tag")

    def _permitted(self):
        """True/False from NetworkManager's answer for OctoPrint's user, None if it couldn't be asked."""
        r = self._nmcli("-t", "-f", "PERMISSION,VALUE", "general", "permissions")
        if r.returncode != 0:
            return None
        got = dict(l.split(":", 1) for l in r.stdout.splitlines() if ":" in l)
        return all(got.get(a) == "yes" for a in NM_ACTIONS)

    def _ensure(self):
        with self._lock:
            want = self._want
        if not want:
            # nothing to join yet, but say now what will be needed (so it can be done before the KNOMI's details come)
            if not shutil.which("nmcli"):
                self.state = "no networkmanager"
                self.detail = "the KNOMI network needs NetworkManager (Raspberry Pi OS Bookworm and newer); Bluetooth works without it"
            elif self._permitted() is False:
                self.state = "needs permission"
                self.detail = "NetworkManager won't let OctoPrint's user set up the KNOMI's network yet. Run the command below once on the Pi."
            else:
                self.state = "waiting"
                self.detail = "for the KNOMI's network details (they come over Bluetooth from firmware OP58)"
            return
        ssid, psk, ip = want
        self.ssid = ssid
        if not shutil.which("nmcli"):
            self.state = "no networkmanager"
            self.detail = "this needs NetworkManager (Raspberry Pi OS Bookworm and newer have it)"
            return
        r = self._nmcli("-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device")
        if r.returncode != 0:
            return self._fail(r, "listing interfaces")
        devices = parse_devices(r.stdout)
        profiles = parse_profiles(self._nmcli("-t", "-f", "NAME,TYPE,AUTOCONNECT,DEVICE", "connection", "show").stdout)
        iface, why = pick_interface(devices, (self._iface_setting() or "").strip(), profiles, default_route_dev())
        if not iface:
            self.state, self.iface = "no interface", ""
            home = [n for n, auto, active in profiles if n != CON_NAME and (auto or active)]
            self.detail = why + ". Put the Pi on Ethernet (frees its built-in WiFi) or add a USB WiFi adapter."
            if home:
                # on Ethernet now, but its WiFi still joins (or would join) your network: that has to stop first
                self.detail += (" If the Pi is on Ethernet already, stop its WiFi joining {n}: "
                                "sudo nmcli connection modify \"{n}\" connection.autoconnect no && sudo nmcli connection down \"{n}\""
                                .format(n=home[0]))
            return
        self.iface = iface
        clash = subnet_taken(ip.split(":")[0], iface)
        if clash:
            self.state = "error"
            self.detail = "{} already uses {}.x addresses, the same as the KNOMI's network".format(clash, ".".join(ip.split(".")[:3]))
            return

        tag = self._secret_tag(ssid, psk)
        try:
            with open(self._tag_path()) as f:
                saved = f.read().strip()
        except OSError:
            saved = ""
        exists = self._nmcli("-t", "-f", "NAME", "connection", "show").stdout.splitlines()
        exists = CON_NAME in exists
        settings = ["connection.interface-name", iface, "802-11-wireless.ssid", ssid,
                    "802-11-wireless.mode", "infrastructure", "802-11-wireless.powersave", "2",
                    "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", psk,
                    "ipv4.method", "auto", "ipv4.never-default", "yes", "ipv4.ignore-auto-dns", "yes",
                    "ipv6.method", "disabled",
                    # never by itself: this worker brings it up, on the interface it checked is free
                    "connection.autoconnect", "no", "connection.autoconnect-priority", "-100"]
        # (the password is an argument for the moment nmcli runs: NetworkManager has no other way in for it)
        if not exists:
            self.state, self.detail = "joining", "setting up {} on {}".format(ssid, iface)
            r = self._nmcli("connection", "add", "type", "wifi", "con-name", CON_NAME, "ifname", iface, "ssid", ssid, *settings)
            if r.returncode != 0:
                return self._fail(r, "setting up the connection")
            saved = ""
        elif saved != tag or self._con_iface() != iface:
            r = self._nmcli("connection", "modify", CON_NAME, *settings)
            if r.returncode != 0:
                return self._fail(r, "updating the connection")
            saved = ""
        if saved != tag:
            try:
                with open(self._tag_path(), "w") as f:
                    f.write(tag)
            except OSError:
                pass
            self._deactivate()   # new details: reconnect with them

        if not self._active():
            self.state, self.detail = "joining", "{} on {}".format(ssid, iface)
            r = self._nmcli("connection", "up", CON_NAME, "ifname", iface, timeout=45)
            if r.returncode != 0:
                return self._fail(r, "joining " + ssid)
        if self._reachable(ip):
            if self.state != "up":
                self._logger.info("KNOMI network: up on %s, the KNOMI is at %s", iface, ip)
                self._up_since = time.time()
            self.state, self.detail = "up", "on {} ({})".format(iface, ssid)
        else:
            self.state, self.detail = "joining", "joined {} on {}, but the KNOMI doesn't answer at {} yet".format(ssid, iface, ip)

    def _con_iface(self):
        r = self._nmcli("-t", "-g", "connection.interface-name", "connection", "show", CON_NAME)
        return r.stdout.strip() if r.returncode == 0 else ""

    def _active(self):
        r = self._nmcli("-t", "-g", "GENERAL.STATE", "connection", "show", CON_NAME)
        return r.returncode == 0 and "activated" in r.stdout

    def _deactivate(self):
        if self._active():
            self._nmcli("connection", "down", CON_NAME)

    def _reachable(self, ip):
        import requests
        try:
            return requests.get("http://{}/log/info".format(ip), timeout=3).status_code == 200
        except requests.RequestException:
            return False
