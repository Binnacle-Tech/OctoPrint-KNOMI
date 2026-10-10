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
              "org.freedesktop.NetworkManager.network-control",
              "org.freedesktop.NetworkManager.wifi.share.protected")


def permission_command(user=None):
    """The one-time command that lets OctoPrint's user manage the KNOMI's connection."""
    user = user or getpass.getuser()
    actions = ",".join('"{}"'.format(a) for a in NM_ACTIONS)
    rule = ('polkit.addRule(function(a,s){{if(s.user=="{u}"&&[{acts}].indexOf(a.id)>=0)return polkit.Result.YES;}});'
            .format(u=user, acts=actions))
    return "echo '{}' | sudo tee /etc/polkit-1/rules.d/50-octoprint-knomi.rules".format(rule)


def parse_devices(text):
    """`nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device` -> [(device, state, connection)] for WiFi devices."""
    out = []
    for line in text.splitlines():
        parts = line.replace("\\:", "\0").split(":")
        parts = [p.replace("\0", ":") for p in parts]
        if len(parts) >= 4 and parts[1] == "wifi":
            out.append((parts[0], parts[2], parts[3]))
    return out


def pick_interface(devices, wanted=""):
    """(interface, why-not). Prefers the one already on the KNOMI's network, then the configured one, then a free one.
    A WiFi interface that's connected to another network is never taken (it may be how the Pi reaches your LAN)."""
    for dev, state, con in devices:
        if con == CON_NAME:
            return dev, ""
    if wanted:
        for dev, state, con in devices:
            if dev == wanted:
                if state.startswith("connected") and con not in ("", "--", CON_NAME):
                    return "", "{} is connected to {} (the Pi needs it for that)".format(dev, con)
                return dev, ""
        return "", "there's no WiFi interface called {}".format(wanted)
    busy = []
    for dev, state, con in devices:
        if state.startswith("connected") and con not in ("", "--"):
            busy.append("{} is on {}".format(dev, con))
            continue
        if state in ("unavailable", "unmanaged"):
            busy.append("{} is {}".format(dev, state))
            continue
        return dev, ""
    if not devices:
        return "", "the Pi has no WiFi interface"
    return "", "no free WiFi interface ({})".format("; ".join(busy))


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

    # ---- from the plugin ----------------------------------------------------

    def start(self):
        if self._thread:
            return
        self._stop = False
        self.state = "waiting"
        self.detail = "for the KNOMI's network details (they come over Bluetooth)"
        self._thread = threading.Thread(target=self._run, daemon=True, name="knomi-link-net")
        self._thread.start()

    def stop(self):
        self._stop = True
        self._wake.set()

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

    def _ensure(self):
        with self._lock:
            want = self._want
        if not want:
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
        iface, why = pick_interface(parse_devices(r.stdout), (self._iface_setting() or "").strip())
        if not iface:
            self.state, self.iface = "no interface", ""
            self.detail = why + ". Put the Pi on Ethernet (frees its built-in WiFi) or add a USB WiFi adapter."
            return
        self.iface = iface

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
                    "ipv6.method", "disabled", "connection.autoconnect", "yes"]
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
