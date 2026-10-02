"""
OctoPrint-KNOMI

Exposes GET /api/plugin/knomi -> {"homing", "probing", "qgling",
"heating_nozzle", "heating_bed", "shaping", "pid_tuning", "cleaning",
"filament", "paused"} for the KNOMI firmware's OctoPrint backend, and pushes the same
dict as a plugin message on the websocket whenever it changes.

Two sources, OR'd together:
  1. Commands OctoPrint itself sends (G28, QUAD_GANTRY_LEVEL, M109, ...):
     flag is raised when the command is sent and cleared on its "ok".
  2. Marker lines emitted by Klipper macros ("// KNOMI homing=1"), so steps
     that run *inside* macros like PRINT_START are visible too. See
     knomi_octoprint.cfg for the _KNOMI_SET helper that emits them.
"""
import json
import re
import threading
import time

import flask
import octoprint.plugin
from octoprint.events import Events

from .ble_link import BleLink
from .pairing import Pairer
from . import layers

# KNOMI Moonraker-style command paths (what the KNOMI UI queues)
GCODE_PREFIX = "/printer/gcode/script?script="
PRINT_PREFIX = "/printer/print/start?filename="

FLAGS = ("homing", "probing", "qgling", "heating_nozzle", "heating_bed",
         "shaping", "pid_tuning", "cleaning", "filament", "paused", "runout")

COMMAND_FLAGS = {
    "G28": "homing",
    "G29": "probing",
    "BED_MESH_CALIBRATE": "probing",
    "PROBE": "probing",
    "PROBE_ACCURACY": "probing",
    "PROBE_CALIBRATE": "probing",
    "CALIBRATE_Z": "probing",
    "QUAD_GANTRY_LEVEL": "qgling",
    "G32": "qgling",
    "Z_TILT_ADJUST": "qgling",
    "M109": "heating_nozzle",
    "M190": "heating_bed",
    "SHAPER_CALIBRATE": "shaping",
    "TEST_RESONANCES": "shaping",
    "PID_CALIBRATE": "pid_tuning",
    "M303": "pid_tuning",
    "CLEAN_NOZZLE": "cleaning",
    "NOZZLE_CLEAN": "cleaning",
    "WIPE_NOZZLE": "cleaning",
    "NOZZLE_WIPE": "cleaning",
    "LOAD_FILAMENT": "filament",
    "UNLOAD_FILAMENT": "filament",
    "M701": "filament",
    "M702": "filament",
}

# "paused" is sticky: set by these, cleared by the resume/cancel ones or print events.
# Covers pauses OctoPrint doesn't see as its own (M600 filament change, MMU/ERCF, macros).
PAUSE_COMMANDS = {"PAUSE", "M600", "M601", "M0", "M1"}
RESUME_COMMANDS = {"RESUME", "M602", "M108", "CANCEL_PRINT"}
PAUSE_ACTIONS = ("action:paused", "action:pause")
# "runout" is sticky like "paused": out of filament until the print resumes or ends
RUNOUT_COMMANDS = {"M600"}
RUNOUT_WORDS = ("runout", "filament_runout", "out of filament")
RESUME_ACTIONS = ("action:resumed", "action:resume", "action:cancel")

# Safety net: a flag from a sent command never outlives this (missed "ok")
COMMAND_FLAG_TIMEOUT = 30 * 60

DISPLAY_TEXT_RE = re.compile(r"MSG=(.*)$", re.IGNORECASE)
MARKER_RE = re.compile(r"KNOMI\s+(\w+)\s*=\s*(\w+)", re.IGNORECASE)
TRUE_VALUES = ("1", "true", "yes", "on")


# pages that don't change for a given firmware version: kept after the first fetch over Bluetooth
CACHEABLE_PAGES = {"/binnacle.css", "/coaster", "/layout", "/log", "/favicon.ico"}

PROXY_ERROR_PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>KNOMI</title></head>
<body style="background:#0E1419;color:#E7EEF4;font:15px system-ui,sans-serif;padding:24px">
<h2 style="margin-top:0">Can't reach the KNOMI</h2><p style="color:#93A4B2">{error}</p>
<p><a style="color:#4FD1C5" href="javascript:location.reload()">Try again</a></p></body></html>"""

# Added to every KNOMI page shown through OctoPrint: OctoPrint wants its CSRF token on POSTs, which the
# KNOMI's plain forms and fetch() calls don't send. It also marks how the page came (WiFi / Bluetooth).
PROXY_SCRIPT = """<script>(function(){
var m=document.cookie.match(/(?:^|; )csrf_token[^=]*=([^;]+)/),tok=m?decodeURIComponent(m[1]):"";
function post(m){return m&&m.toUpperCase()!=="GET"}
var of=window.fetch;window.fetch=function(u,o){o=o||{};if(post(o.method)){var h=new Headers(o.headers||{});h.set("X-CSRF-Token",tok);o.headers=h}o.credentials="same-origin";return of.call(window,u,o)};
var xo=XMLHttpRequest.prototype.open,xs=XMLHttpRequest.prototype.send;
XMLHttpRequest.prototype.open=function(m){this._km=m;return xo.apply(this,arguments)};
XMLHttpRequest.prototype.send=function(){if(post(this._km))this.setRequestHeader("X-CSRF-Token",tok);return xs.apply(this,arguments)};
function send(f,b){var a=(b&&b.getAttribute("formaction"))||f.getAttribute("action")||location.href,fd=new FormData(f);
if(b&&b.name)fd.append(b.name,b.value||"");document.body.style.opacity=".6";
of.call(window,new URL(a,location.href),{method:(f.getAttribute("method")||"POST").toUpperCase(),body:fd,credentials:"same-origin",headers:{"X-CSRF-Token":tok}})
.then(function(r){var u=r.url;return r.text().then(function(h){try{history.replaceState(null,"",u)}catch(e){}document.open();document.write(h);document.close()})})
.catch(function(e){document.body.style.opacity="";alert("Couldn't reach the KNOMI: "+e)})}
HTMLFormElement.prototype.submit=function(){send(this,null)};
document.addEventListener("submit",function(e){if(e.defaultPrevented)return;e.preventDefault();send(e.target,e.submitter)});
window.KNOMI_VIA="%VIA%";})();</script>"""


def inject_proxy_script(content, via):
    """Put PROXY_SCRIPT first thing in <head> (before the page's own scripts run)."""
    script = PROXY_SCRIPT.replace("%VIA%", via).encode("utf-8")
    low = content[:4096].lower()
    i = low.find(b"<head>")
    if i >= 0:
        i += len(b"<head>")
        return content[:i] + script + content[i:]
    return script + content


def latest_release_tag(repo):
    """The latest release's tag, from github.com's redirect. Not api.github.com: that allows 60 requests an hour
    per home IP, shared with OctoPrint's own update checks, and then answers 403."""
    import requests
    r = requests.head("https://github.com/{}/releases/latest".format(repo), allow_redirects=False, timeout=20)
    loc = r.headers.get("Location", "")
    if "/releases/tag/" not in loc:
        raise IOError("GitHub answered {} for the latest release".format(r.status_code))
    return loc.rsplit("/releases/tag/", 1)[1]


def request_body(req):
    """(body bytes, content type) of a POST to pass on to the KNOMI. If something already parsed the form
    (OctoPrint's CSRF check reads request.form), the raw body is gone: build it again from what was parsed."""
    ctype = req.headers.get("Content-Type", "")
    body = req.get_data(cache=True)
    if body or not (req.form or req.files):
        return body, ctype
    if not req.files:
        from urllib.parse import urlencode
        return urlencode(list(req.form.items(multi=True))).encode("utf-8"), "application/x-www-form-urlencoded"
    import uuid
    b = "knomi" + uuid.uuid4().hex
    out = bytearray()
    for k, v in req.form.items(multi=True):
        out += '--{}\r\nContent-Disposition: form-data; name="{}"\r\n\r\n'.format(b, k).encode("utf-8") + v.encode("utf-8") + b"\r\n"
    for k, f in req.files.items(multi=True):
        out += ('--{}\r\nContent-Disposition: form-data; name="{}"; filename="{}"\r\nContent-Type: {}\r\n\r\n'.format(
            b, k, (f.filename or "file").replace('"', ""), f.mimetype or "application/octet-stream")).encode("utf-8")
        out += f.read() + b"\r\n"
    out += "--{}--\r\n".format(b).encode()
    return bytes(out), "multipart/form-data; boundary=" + b


def parse_http_response(raw):
    """(status, [(header, value)], body) from raw HTTP/1.1 response bytes (chunked or not)."""
    import http.client
    import io

    class _Sock(object):
        def __init__(self, data):
            self._f = io.BytesIO(data)

        def makefile(self, *args, **kwargs):
            return self._f

    r = http.client.HTTPResponse(_Sock(raw))
    r.begin()
    body = r.read()
    return r.status, r.getheaders(), body


class KnomiPlugin(octoprint.plugin.SimpleApiPlugin,
                  octoprint.plugin.BlueprintPlugin,
                  octoprint.plugin.EventHandlerPlugin,
                  octoprint.plugin.SettingsPlugin,
                  octoprint.plugin.TemplatePlugin,
                  octoprint.plugin.AssetPlugin,
                  octoprint.plugin.StartupPlugin,
                  octoprint.plugin.ShutdownPlugin):

    def __init__(self):
        self._lock = threading.Lock()
        self._cmd_flags = {}                     # flag -> time raised
        self._marker_flags = dict.fromkeys(FLAGS, False)
        self._fan = 0        # part cooling fan, % (M106/M107)
        self._speed = 100    # speed factor, % (M220)
        self._msg = ""       # last M117 / SET_DISPLAY_TEXT / action:notification
        self._msg_id = 0
        self._coaster = {}   # what the KNOMI's Coaster is doing (sidebar mirror)
        self._coaster_watch_until = 0
        self._knomi = {"ip": "", "seen": 0.0, "via": "", "fw": ""}   # the last time the KNOMI checked in
        self._wifi_seen = 0.0       # last check-in over WiFi (the proxy tries WiFi first if recent)
        self._wifi_failed = 0.0     # the proxy couldn't reach it over WiFi (skip WiFi for a while)
        self._page_cache = {}       # (fw, path) -> response, static pages fetched over Bluetooth
        self._pairer = None
        self._last_pushed = None
        self._ble = None
        self._layer_map = None   # layers.LayerMap of the file being printed
        self._layer_job = 0      # which print a layer scan belongs to
        self._push_lock = threading.RLock()
        self._layer = (0, None)  # (layer, Z mm) at OctoPrint's file position
        self._layer_timer = None
        self._fw = {"state": "idle"}   # firmware install: state, msg, pct
        self._fw_latest = ""           # newest KNOMI firmware release on GitHub, e.g. "OP46"
        self._fw_told = ""             # the release Coaster already announced

    # ---- helpers ---------------------------------------------------------

    def _flag_for_command(self, cmd):
        parts = cmd.strip().upper().split()
        if not parts:
            return None
        word = parts[0]
        if word == "TEMPERATURE_WAIT":
            sensor = next((p.split("=", 1)[1] for p in parts[1:] if p.startswith("SENSOR=")), "")
            if sensor.startswith("EXTRUDER"):
                return "heating_nozzle"
            if sensor == "HEATER_BED":
                return "heating_bed"
            return None
        return COMMAND_FLAGS.get(word)

    def _reset(self):
        with self._lock:
            self._cmd_flags.clear()
            self._marker_flags = dict.fromkeys(FLAGS, False)
            self._fan = 0
            self._speed = 100
        self._push()

    def _push(self):
        """Send status to websocket clients (the KNOMI) when it changed."""
        with self._push_lock:   # called from the comm, event, timer and BLE threads
            status = self._status()
            if status == self._last_pushed:
                return
            self._last_pushed = status
            try:
                self._plugin_manager.send_plugin_message(self._identifier, status)
            except Exception:
                self._logger.exception("Could not push KNOMI status")

    def _status(self):
        now = time.monotonic()
        with self._lock:
            for flag, since in list(self._cmd_flags.items()):
                if now - since > COMMAND_FLAG_TIMEOUT:
                    del self._cmd_flags[flag]
            status = {f: bool(f in self._cmd_flags or self._marker_flags.get(f)) for f in FLAGS}
        status["fan"] = self._fan
        status["speed"] = self._speed
        status["msg"] = self._msg
        status["msg_id"] = self._msg_id
        # tells the KNOMI which progress OctoPrint's dashboard shows
        status["time_progress"] = self._time_progress()
        status["tz"] = int(time.localtime().tm_gmtoff // 60)   # the KNOMI's clock: the Pi's time zone
        # layer and Z from the file position, the way OctoPrint's G-code viewer follows a print
        lm = self._layer_map
        if lm is not None and lm.material:
            status["mat"] = lm.material
        if lm is not None and self._layer[0] > 0:
            status["layer"] = self._layer[0]
            status["layers"] = max(lm.total, self._layer[0])
            status["z"] = int(round(self._layer[1] * 1000)) if self._layer[1] is not None else None
        return status

    # ---- layers ------------------------------------------------------------

    def _start_layer_tracking(self, payload):
        self._stop_layer_tracking()
        self._layer_job += 1
        job = self._layer_job
        origin, path = payload.get("origin"), payload.get("path")
        if origin != "local" or not path:
            self._logger.info("KNOMI: no layer tracking for %s files", origin)
            return
        try:
            disk = self._file_manager.path_on_disk("local", path)
        except Exception:
            self._logger.exception("KNOMI: can't find %s on disk", path)
            return

        def work():
            try:
                t0 = time.monotonic()
                lm = layers.scan(disk)
                self._logger.info("KNOMI: %s has %d layers (scanned in %.1f s)", path, lm.total, time.monotonic() - t0)
                if job == self._layer_job:   # still the same print (a cancelled one's scan can finish late)
                    self._layer_map = lm
            except Exception:
                self._logger.exception("KNOMI: couldn't scan %s for layers", path)

        threading.Thread(target=work, name="knomi-layers", daemon=True).start()
        from octoprint.util import RepeatedTimer
        self._layer_timer = RepeatedTimer(1.0, self._poll_layer, run_first=False)
        self._layer_timer.start()

    def _stop_layer_tracking(self):
        self._layer_job += 1
        if self._layer_timer:
            self._layer_timer.cancel()
            self._layer_timer = None
        self._layer_map = None
        if self._layer != (0, None):
            self._layer = (0, None)
            self._push()

    def _poll_layer(self):
        lm = self._layer_map
        if lm is None:
            return
        try:
            pos = (self._printer.get_current_data().get("progress") or {}).get("filepos")
        except Exception:
            return
        now = lm.at(pos)
        if now != self._layer:
            self._layer = now
            self._push()

    def _time_progress(self):
        """True when PrintTimeGenius is enabled: it turns the dashboard bar time-based."""
        try:
            return self._plugin_manager.get_plugin("PrintTimeGenius") is not None
        except Exception:
            return False

    # ---- gcode hooks -----------------------------------------------------

    def _set_paused(self, value):
        with self._lock:
            self._marker_flags["paused"] = value
            if not value:
                self._marker_flags["runout"] = False
        self._push()

    def _set_runout(self):
        with self._lock:
            self._marker_flags["runout"] = True
        self._push()

    def _set_message(self, text):
        """A display message for the KNOMI (Coaster says it in a speech bubble)."""
        text = " ".join((text or "").split())[:64]
        with self._lock:
            self._msg = text
            self._msg_id += 1
        self._push()

    def _track_fan_speed(self, parts):
        """Part fan from M106/M107 (fan 0 only), speed factor from M220."""
        word = parts[0]
        args = {p[0]: p[1:] for p in parts[1:] if len(p) > 1}
        changed = False
        with self._lock:
            if word == "M106" and args.get("P", "0") == "0":
                try:
                    self._fan = max(0, min(100, round(float(args.get("S", "255")) * 100 / 255)))
                    changed = True
                except ValueError:
                    pass
            elif word == "M107" and args.get("P", "0") == "0":
                self._fan = 0
                changed = True
            elif word == "M220" and "S" in args:
                try:
                    self._speed = max(1, min(999, round(float(args["S"]))))
                    changed = True
                except ValueError:
                    pass
        if changed:
            self._push()

    def on_gcode_sent(self, comm_instance, phase, cmd, cmd_type, gcode, *args, **kwargs):
        parts = cmd.strip().upper().split()
        word = parts[0] if parts else ""
        if word == "M117":
            self._set_message(cmd.strip()[4:].strip())
        elif word == "SET_DISPLAY_TEXT":
            m = DISPLAY_TEXT_RE.search(cmd)
            self._set_message(m.group(1).strip('"\'') if m else "")
        if word in ("M106", "M107", "M220"):
            self._track_fan_speed(parts)
        if word in RUNOUT_COMMANDS:
            self._set_runout()
        if word in PAUSE_COMMANDS:
            self._set_paused(True)
        elif word in RESUME_COMMANDS:
            self._set_paused(False)
        flag = self._flag_for_command(cmd)
        if flag:
            with self._lock:
                self._cmd_flags[flag] = time.monotonic()
            self._push()

    def on_gcode_received(self, comm_instance, line, *args, **kwargs):
        stripped = line.strip()
        if stripped.startswith("ok"):
            # OctoPrint keeps one command in flight, so this ok belongs to the
            # command that raised the flag.
            if self._cmd_flags:
                with self._lock:
                    self._cmd_flags.clear()
                self._push()
        elif "action:" in stripped or "runout" in stripped.lower():
            low = stripped.lower()
            i = low.find("action:notification")
            if i >= 0:
                self._set_message(stripped[i + len("action:notification"):].strip())
            if any(w in low for w in RUNOUT_WORDS):
                self._set_runout()
            if any(a in low for a in PAUSE_ACTIONS):
                self._set_paused(True)
            elif any(a in low for a in RESUME_ACTIONS):
                self._set_paused(False)
        elif "KNOMI" in stripped:
            m = MARKER_RE.search(stripped)
            if m and m.group(1).lower() in self._marker_flags:
                with self._lock:
                    self._marker_flags[m.group(1).lower()] = m.group(2).lower() in TRUE_VALUES
                self._push()
        return line

    # ---- events ----------------------------------------------------------

    # ---- settings / lifecycle --------------------------------------------

    def get_settings_defaults(self):
        return {"ble_enabled": False, "ble_address": "", "tool": "tool0",
                "knomi_ip": ""}   # last WiFi address the KNOMI had (kept across restarts, for its pages)

    def is_template_autoescaped(self):
        return True

    def get_template_configs(self):
        return [{"type": "settings", "custom_bindings": True},
                {"type": "sidebar", "name": "Coaster", "icon": "smile-o", "custom_bindings": True,
                 "template": "knomi_sidebar.jinja2"}]

    def get_assets(self):
        return {"js": ["js/knomi_device.js", "js/knomi.js", "js/coaster.js"]}

    def on_after_startup(self):
        self._ble = BleLink(self, self._logger)
        self._pairer = Pairer(self._logger, self._on_paired, self._on_pair_done)
        if self._settings.get_boolean(["ble_enabled"]):
            self._ble.start()
        threading.Thread(target=self._fw_watch, daemon=True, name="knomi-fw-watch").start()
        # OctoPrint restarted mid-print: pick the layers up again
        try:
            if self._printer.is_printing() or self._printer.is_paused():
                f = (self._printer.get_current_job() or {}).get("file") or {}
                self._start_layer_tracking({"origin": f.get("origin"), "path": f.get("path")})
        except Exception:
            self._logger.exception("KNOMI: couldn't resume layer tracking")

    # ---- new KNOMI firmware: Coaster says so, the settings tab offers it ----

    FW_REPO = "Binnacle-Tech/KNOMI"

    def _fw_watch(self):
        last_check = 0.0
        while True:
            if time.time() - last_check > 6 * 3600:
                try:
                    m = re.search(r"op(\d+)", latest_release_tag(self.FW_REPO), re.I)
                    if m:
                        self._fw_latest = "OP" + m.group(1)
                    last_check = time.time()
                except Exception as e:
                    self._logger.debug("KNOMI: couldn't check for new firmware: %s", e)
                    last_check = time.time() - 5 * 3600   # try again in an hour
            self._fw_announce()
            time.sleep(600)

    def _fw_newer(self):
        """The newer release ("OP46") if the KNOMI runs something older, else ""."""
        have = re.search(r"OP(\d+)", self._knomi.get("fw") or "", re.I)
        latest = re.search(r"OP(\d+)", self._fw_latest or "", re.I)
        if have and latest and int(latest.group(1)) > int(have.group(1)):
            return self._fw_latest
        return ""

    def _fw_announce(self):
        new = self._fw_newer()
        if not new or new == self._fw_told:
            return
        try:
            if self._printer.is_printing() or self._printer.is_paused():
                return   # not in the middle of a print; next time
        except Exception:
            return
        self._fw_told = new
        self._set_message("KNOMI firmware {} is out. Update in Settings > KNOMI".format(new))

    def ble_info(self, fw):
        """The KNOMI's firmware version, read when Bluetooth connects."""
        self._knomi["fw"] = fw[:24]
        self._fw_announce()

    def on_shutdown(self):
        if self._ble:
            self._ble.stop()

    def on_settings_save(self, data):
        old = (self._settings.get_boolean(["ble_enabled"]), self._settings.get(["ble_address"]))
        octoprint.plugin.SettingsPlugin.on_settings_save(self, data)
        new = (self._settings.get_boolean(["ble_enabled"]), self._settings.get(["ble_address"]))
        if self._ble and new != old:
            self._ble.stop()
            if new[0]:
                self._ble.start()

    # ---- BLE link callbacks (called from the link thread) -------------------

    def ble_configured_address(self):
        return (self._settings.get(["ble_address"]) or "").strip()

    def ble_remember_address(self, address):
        if not self.ble_configured_address():
            self._settings.set(["ble_address"], address)
            self._settings.save()

    def ble_status(self, wifi=False):
        status = build_ble_status(self._printer.get_current_data(),
                                  self._printer.get_current_temperatures(),
                                  self._status(),
                                  self._settings.get(["tool"]) or "tool0",
                                  wifi, self._time_progress())
        if self.coaster_watched():
            status["cw"] = 1
        return status

    def ble_file_list(self):
        from octoprint.filemanager.destinations import FileDestinations
        files = self._file_manager.list_files(FileDestinations.LOCAL, recursive=True)
        return collect_paths(files.get(FileDestinations.LOCAL, {}))

    def ble_command(self, path):
        handle_knomi_command(self, path)

    def on_event(self, event, payload):
        if self._ble and event in (Events.FILE_ADDED, Events.FILE_REMOVED, Events.UPDATED_FILES):
            self._ble.files_changed()
        if event == Events.PRINT_STARTED:
            self._start_layer_tracking(payload or {})
        elif event in (Events.PRINT_DONE, Events.PRINT_CANCELLED, Events.PRINT_FAILED,
                       Events.DISCONNECTED, Events.ERROR):
            self._stop_layer_tracking()
        if event in (Events.DISCONNECTED, Events.ERROR, Events.CONNECTED,
                     Events.PRINT_CANCELLED, Events.PRINT_FAILED):
            self._reset()
        elif event in (Events.PRINT_RESUMED, Events.PRINT_STARTED, Events.PRINT_DONE):
            if event != Events.PRINT_RESUMED:
                with self._lock:
                    self._speed = 100
            self._set_paused(False)

    # ---- API -------------------------------------------------------------

    def is_api_protected(self):
        return True

    def get_api_commands(self):
        return {"wifi_on": [], "coaster": [], "coaster_watch": [], "ble_scan": [], "ble_pair": ["address"],
                "ble_code": ["code"], "ble_cancel": [], "ble_forget": [], "ble_reconnect": [],
                "fw_install": ["repo", "asset"]}

    def _on_paired(self, address):
        """Pairing worked (pairing thread): remember the KNOMI and connect."""
        self._settings.set(["ble_address"], address)
        self._settings.set_boolean(["ble_enabled"], True)
        self._settings.save()
        if self._ble:
            self._ble.stop()
            self._ble.start()

    def _on_pair_done(self, ok):
        """A pairing attempt ended (pairing thread). If it didn't work, bring the link back as it was."""
        if not ok and self._ble and self._settings.get_boolean(["ble_enabled"]):
            self._ble.start()

    def coaster_watched(self):
        """Someone has the sidebar open: the KNOMI then sends head motion ~3x a second."""
        return time.monotonic() < self._coaster_watch_until

    def coaster_update(self, data):
        """The KNOMI reports Coaster's mood, quirk, feeling, decorations, head motion and last report card."""
        if data.get("fw"):
            self._knomi["fw"] = str(data["fw"])[:24]
            self._fw_announce()
        ip = str(data.get("ip") or "")
        if ip and re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", ip):   # sent by OP41+, also over Bluetooth
            self._remember_ip(ip)
        def num(k, lo, hi):
            try:
                return max(lo, min(hi, float(data.get(k, 0) or 0)))
            except (TypeError, ValueError):
                return 0
        clean = {k: str(data.get(k, ""))[:24] for k in ("mood", "feel", "q", "deco", "lights", "anim", "c", "mat")}
        if data.get("say"):
            clean["say"] = str(data["say"])[:64]
        if data.get("mu"):
            clean["mu"] = str(data["mu"])[:24]
        clean.update(hat=int(num("hat", 0, 9)), qs=int(num("qs", -1, 1)), shades=int(num("shades", 0, 1)),
                     act=int(num("act", 0, 20)), south=int(num("south", 0, 1)), pr=int(num("pr", 0, 1)),
                     h=num("h", -1, 1), heat=num("heat", 0, 1), hf=num("hf", 0, 1), wx=int(num("wx", -1, 1)), sig=int(num("sig", -1, 9)))
        if "hx" in data:
            clean.update(hx=num("hx", -40, 40), hy=num("hy", -40, 40), hs=num("hs", -1, 1),
                         px=num("px", -20, 20), py=num("py", -20, 20), look=num("look", -40, 40))
        rep = data.get("report")
        if isinstance(rep, dict):
            clean["report"] = {k: rep.get(k) for k in ("done", "progress", "screams", "dizzies", "jolts", "peak", "secs")}
        self._coaster = clean
        try:
            self._plugin_manager.send_plugin_message(self._identifier, {"coaster": clean})
        except Exception:
            self._logger.exception("Could not push Coaster state")

    def on_api_command(self, command, data):
        if command == "coaster":
            self._knomi.update(ip=flask.request.remote_addr or "", seen=time.time(), via="WiFi")
            self._remember_ip(flask.request.remote_addr or "")
            self._wifi_seen = time.time()
            self.coaster_update(data)
            return flask.jsonify(ok=True, watch=self.coaster_watched())
        if command == "coaster_watch":
            self._coaster_watch_until = time.monotonic() + 12
            return flask.jsonify(ok=True)
        if command == "wifi_on" and self._ble:
            self._ble.request_wifi()
            return flask.jsonify(ok=self._ble.state == "connected")
        # pairing from the settings page
        if command == "ble_scan" and self._pairer:
            return flask.jsonify(ok=self._pairer.scan())
        if command == "ble_pair" and self._pairer:
            if self._pairer.busy():
                return flask.jsonify(ok=False)
            if self._ble:
                self._ble.stop()   # the link would get in the way of pairing; _on_pair_done restarts it if it fails
            ok = self._pairer.pair(str(data.get("address", "")))
            if not ok and self._ble and self._settings.get_boolean(["ble_enabled"]):
                self._ble.start()
            return flask.jsonify(ok=ok)
        if command == "ble_code" and self._pairer:
            return flask.jsonify(ok=self._pairer.code(data.get("code", "")))
        if command == "ble_cancel" and self._pairer:
            busy = self._pairer.busy()
            self._pairer.cancel()   # a running attempt stops and restarts the link itself (_on_pair_done)
            if not busy and self._ble and self._settings.get_boolean(["ble_enabled"]):
                self._ble.start()
            return flask.jsonify(ok=True)
        if command == "ble_forget" and self._pairer:
            address = (self._settings.get(["ble_address"]) or self._ble.address or "").strip()
            if self._ble:
                self._ble.stop()
            self._settings.set(["ble_address"], "")
            self._settings.set_boolean(["ble_enabled"], False)
            self._settings.save()
            ok = self._pairer.forget(address) if address else True
            return flask.jsonify(ok=ok)
        if command == "fw_install":
            from octoprint.access.permissions import Permissions
            if not Permissions.SETTINGS.can():
                return flask.make_response(flask.jsonify(error="Not allowed"), 403)
            repo, asset = str(data.get("repo", "")), str(data.get("asset", ""))
            if not re.match(r"^[\w.-]+/[\w.-]+$", repo) or not re.match(r"^[\w.-]+\.bin$", asset):
                return flask.make_response(flask.jsonify(error="Bad release name"), 400)
            if not self._fw_start(lambda: self._fw_download(repo, asset)):
                return flask.make_response(flask.jsonify(error="An update is already running"), 409)
            return flask.jsonify(ok=True)
        if command == "ble_reconnect" and self._ble:
            self._ble.stop()
            if self._settings.get_boolean(["ble_enabled"]):
                self._ble.start()
            return flask.jsonify(ok=True)

    # ---- updates -----------------------------------------------------------

    # ---- firmware: the plugin fetches it and sends it to the KNOMI (WiFi or Bluetooth) ----

    def _fw_start(self, get_image):
        if self._fw.get("state") in ("downloading", "sending"):
            return False
        self._fw = {"state": "downloading", "msg": "Getting the firmware"}
        threading.Thread(target=self._fw_run, args=(get_image,), daemon=True, name="knomi-fw").start()
        return True

    def _fw_download(self, repo, asset):
        import requests
        tag = latest_release_tag(repo)
        self._fw["msg"] = "Downloading " + tag
        r = requests.get("https://github.com/{}/releases/download/{}/{}".format(repo, tag, asset), timeout=120)
        if r.status_code == 404:
            raise IOError("the latest release ({}) has no {}".format(tag, asset))
        r.raise_for_status()
        return r.content, tag

    def _fw_run(self, get_image):
        import hashlib
        try:
            image, name = get_image()
            if len(image) < 100000 or image[:1] != b"\xe9":
                raise IOError("that isn't an ESP32 firmware image")
            md5 = hashlib.md5(image).hexdigest()
            boundary = "knomi" + md5[:16]
            body = ("--{b}\r\nContent-Disposition: form-data; name=\"MD5\"\r\n\r\n{m}\r\n"
                    "--{b}\r\nContent-Disposition: form-data; name=\"firmware\"; filename=\"firmware.bin\"\r\n"
                    "Content-Type: application/octet-stream\r\n\r\n").format(b=boundary, m=md5).encode()
            body += image + "\r\n--{}--\r\n".format(boundary).encode()
            self._fw.update(state="sending", msg="Sending {} to the KNOMI".format(name or "the firmware"), pct=0)

            def progress(sent):
                self._fw["pct"] = min(100, int(sent * 100 / len(body)))
            status, _h, content, via = self._knomi_fetch("POST", "/update", "multipart/form-data; boundary=" + boundary,
                                                          body, progress=progress)
            if status != 200:
                raise IOError("the KNOMI said {}: {}".format(status, content[:200].decode("utf-8", "replace")))
            self._fw = {"state": "done", "msg": "Installed {} over {}. The KNOMI is restarting.".format(name or "the firmware", via)}
            self._page_cache.clear()
        except Exception as e:
            self._logger.info("KNOMI firmware install failed: %s", e)
            self._fw = {"state": "error", "msg": "Not installed: {}".format(e)}

    @octoprint.plugin.BlueprintPlugin.route("/fw_upload", methods=["POST"])
    def fw_upload(self):
        from octoprint.access.permissions import Permissions
        if not Permissions.SETTINGS.can():
            return flask.make_response(flask.jsonify(error="Not allowed"), 403)
        f = flask.request.files.get("firmware")
        if not f:
            return flask.make_response(flask.jsonify(error="No file"), 400)
        image, name = f.read(), f.filename or "the .bin"
        if not self._fw_start(lambda: (image, name)):
            return flask.make_response(flask.jsonify(error="An update is already running"), 409)
        return flask.jsonify(ok=True)

    # ---- the KNOMI's own web pages, through OctoPrint -------------------------
    # /plugin/knomi/k/<path> forwards to the KNOMI: over WiFi when it's on, otherwise over Bluetooth
    # (firmware OP41+). The KNOMI's pages use relative links, so they work under this prefix.

    def is_blueprint_protected(self):
        return True

    def is_blueprint_csrf_protected(self):
        return True

    @octoprint.plugin.BlueprintPlugin.route("/k/", methods=["GET", "POST"], defaults={"path": ""})
    @octoprint.plugin.BlueprintPlugin.route("/k/<path:path>", methods=["GET", "POST"])
    def knomi_proxy(self, path):
        from octoprint.access.permissions import Permissions
        if not Permissions.SETTINGS.can():
            return flask.make_response("Only users who may change settings can open the KNOMI's pages.", 403)
        req = flask.request
        target = "/" + path + ("?" + req.query_string.decode("latin-1") if req.query_string else "")
        body, ctype = request_body(req) if req.method == "POST" else (b"", "")
        try:
            status, headers, content, via = self._knomi_fetch(req.method, target, ctype, body)
        except Exception as e:
            self._logger.info("KNOMI proxy %s %s failed: %s", req.method, target, e)
            from markupsafe import escape
            return flask.make_response(PROXY_ERROR_PAGE.format(error=escape(str(e))), 502)
        prefix = req.script_root + "/plugin/" + self._identifier + "/k/"
        out_headers = {}
        for k, v in headers:
            kl = k.lower()
            if kl == "location" and v.startswith("/"):
                v = prefix + v[1:]
            if kl in ("content-type", "location", "cache-control", "content-disposition"):
                out_headers[k] = v
        ct = out_headers.get("Content-Type", out_headers.get("content-type", ""))
        if "text/html" in ct:
            content = inject_proxy_script(content, via)
        return flask.Response(content, status=status, headers=out_headers)

    def _remember_ip(self, ip):
        if not ip:
            return
        self._knomi["ip"] = ip
        if self._settings.get(["knomi_ip"]) != ip:
            self._settings.set(["knomi_ip"], ip)
            self._settings.save()

    def _knomi_fetch(self, method, target, ctype, body, progress=None):
        """(status, headers, body, "WiFi"/"Bluetooth") from the KNOMI."""
        ip = self._knomi.get("ip") or self._settings.get(["knomi_ip"])
        now = time.time()
        tried = []
        if ip and now - self._wifi_failed > 30:
            import requests
            try:
                r = requests.request(method, "http://{}{}".format(ip, target), data=body or None,
                                     headers={"Content-Type": ctype} if ctype else {},
                                     timeout=(2, 60 + len(body) / 50000.0), allow_redirects=False)
                return r.status_code, list(r.headers.items()), r.content, "WiFi"
            except requests.RequestException as e:
                self._wifi_failed = now   # WiFi is probably off: Bluetooth for a while
                tried.append("WiFi ({}): {}".format(ip, e.__class__.__name__))
        elif not ip:
            tried.append("WiFi: the KNOMI's address isn't known yet")
        else:
            tried.append("WiFi ({}): didn't answer a moment ago".format(ip))
        if not self._ble or not self._ble.tunnel_ready():
            if not self._ble or self._ble.state != "connected":
                tried.append("Bluetooth: the link isn't connected")
            else:
                tried.append("Bluetooth: connected, but the KNOMI doesn't offer its pages yet (firmware older than "
                             "OP43 on a Pi paired before OP41; it reconnects a few times to refresh)")
            raise IOError(" · ".join(tried))
        key = (self._knomi.get("fw"), target)
        if method == "GET" and key in self._page_cache:
            return self._page_cache[key] + ("Bluetooth",)
        raw = None
        for attempt in range(2):
            try:
                raw = self._ble.request(method, target, ctype, body, progress=progress)
                break
            except IOError as e:
                if method != "GET" or attempt or "lost" not in str(e):
                    raise
        status, headers, content = parse_http_response(raw)
        if method == "GET" and status == 200 and target.split("?")[0] in CACHEABLE_PAGES:
            self._page_cache[key] = (status, headers, content)
        return status, headers, content, "Bluetooth"

    def bodysize_hook(self, current_max_body_sizes, *args, **kwargs):
        # GIF uploads (1.5 MB) and firmware files (3 MB+) go through the proxy
        return [("POST", r"/k/.*", 8 * 1024 * 1024), ("POST", r"/fw_upload", 8 * 1024 * 1024)]

    def get_update_information(self):
        """OctoPrint's Software Update checks GitHub for new commits on main and offers
        a one-click update (Settings > Software Update)."""
        return {
            "knomi": {
                "displayName": "KNOMI",
                "displayVersion": self._plugin_version,
                "type": "github_commit",
                "user": "Binnacle-Tech",
                "repo": "OctoPrint-KNOMI",
                "branch": "main",
                "pip": "https://github.com/Binnacle-Tech/OctoPrint-KNOMI/archive/{target_version}.zip",
            }
        }

    def on_api_get(self, request):
        # the KNOMI polls this over WiFi (its HTTP client says ESP32): that counts as checking in
        if "ESP32" in (request.headers.get("User-Agent") or ""):
            self._knomi.update(ip=request.remote_addr or "", seen=time.time(), via="WiFi")
            self._remember_ip(request.remote_addr or "")
            self._wifi_seen = time.time()
        result = self._status()
        result["coaster"] = self._coaster
        if self._ble:
            result.update(ble_state=self._ble.state, ble_address=self._ble.address,
                          ble_error=self._ble.last_error)
        if self._pairer:
            result["pair"] = self._pairer.state()
        k = dict(self._knomi)
        k["seen_s"] = round(time.time() - k.pop("seen")) if self._knomi["seen"] else None
        result["knomi"] = k
        result["plugin_version"] = self._plugin_version
        result["fw_update"] = self._fw
        result["fw_latest"] = self._fw_latest
        result["fw_new"] = self._fw_newer()
        return flask.jsonify(result)


# ---- helpers (module level so they can be tested without OctoPrint) --------

def ui_progress(prog, time_based=False):
    """Progress as OctoPrint's dashboard shows it.

    With PrintTimeGenius enabled (time_based) the dashboard bar is
    elapsed / (elapsed + remaining) whenever a time-left estimate exists;
    otherwise it is the file position ("completion").
    """
    left = prog.get("printTimeLeft")
    if left and time_based:
        t = prog.get("printTime") or 0
        return max(0.0, min(100.0, t * 100.0 / (t + left)))
    return prog.get("completion")


def build_ble_status(data, temps, knomi_flags, tool="tool0", wifi=False, time_based=False):
    """OctoPrint state -> compact status for the KNOMI (keys decoded in knomi_ble.cpp)."""
    flags = (data.get("state") or {}).get("flags") or {}
    prog = data.get("progress") or {}
    job_file = ((data.get("job") or {}).get("file") or {}).get("name") or ""
    t = temps.get(tool) or {}
    b = temps.get("bed") or {}

    def r(v):
        return int(round(v or 0))

    status = {
        "o": int(bool(flags.get("operational"))),
        "p": int(any(flags.get(k) for k in ("printing", "cancelling", "resuming", "finishing"))),
        "pa": int(bool(flags.get("paused") or flags.get("pausing"))),
        "g": r(ui_progress(prog, time_based)),
        "t": r(prog.get("printTime")),
        "l": -1 if prog.get("printTimeLeft") is None else r(prog.get("printTimeLeft")),
        "n": job_file.rsplit("/", 1)[-1].encode("utf-8")[:31].decode("utf-8", errors="ignore"),
        "nt": [r(t.get("actual")), r(t.get("target"))],
        "bt": [r(b.get("actual")), r(b.get("target"))],
        "k": sum(1 << i for i, f in enumerate(FLAGS) if knomi_flags.get(f)),
        "f": int(knomi_flags.get("fan", 0)),
        "sp": int(knomi_flags.get("speed", 100)),
        "m": knomi_flags.get("msg", ""),
        "mi": int(knomi_flags.get("msg_id", 0)),
    }
    if data.get("currentZ") is not None:
        status["z"] = int(round(data["currentZ"] * 1000))
    elif knomi_flags.get("z") is not None:
        status["z"] = knomi_flags["z"]
    if knomi_flags.get("layer"):
        status["ly"] = [knomi_flags["layer"], knomi_flags.get("layers", 0)]
    if knomi_flags.get("mat"):
        status["mt"] = knomi_flags["mat"]
    if wifi:
        status["w"] = 1
    return status


def collect_paths(entries):
    out = []
    for entry in (entries or {}).values():
        if entry.get("type") == "folder":
            out.extend(collect_paths(entry.get("children")))
        elif entry.get("type") == "machinecode":
            out.append(entry.get("path") or entry.get("name"))
    return sorted(out, key=str.lower)


def handle_knomi_command(plugin, path):
    """Run a KNOMI command path (sent over BLE) inside OctoPrint."""
    printer = plugin._printer
    if path.startswith("/coaster?"):
        try:
            plugin._knomi.update(seen=time.time(), via="Bluetooth")
            plugin.coaster_update(json.loads(path[len("/coaster?"):]))
        except (ValueError, TypeError):
            pass
        return
    if path.startswith(GCODE_PREFIX):
        printer.commands(path[len(GCODE_PREFIX):])
    elif path.startswith(PRINT_PREFIX):
        from octoprint.filemanager.destinations import FileDestinations
        name = path[len(PRINT_PREFIX):]
        full = plugin._file_manager.path_on_disk(FileDestinations.LOCAL, name)
        printer.select_file(full, False, printAfterSelect=True)
    elif path == "/printer/print/cancel":
        printer.cancel_print()
    elif path == "/printer/print/pause":
        printer.pause_print()
    elif path == "/printer/print/resume":
        if printer.is_paused():
            printer.resume_print()
        else:
            printer.commands("RESUME")  # paused inside Klipper (M600, MMU...)
    elif path == "/printer/restart":
        printer.commands("RESTART")
    elif path == "/printer/firmware_restart":
        printer.commands("FIRMWARE_RESTART")
    elif path in ("/machine/reboot", "/machine/shutdown") or path.startswith("/machine/services/restart"):
        key = {"/machine/reboot": "systemRestartCommand",
               "/machine/shutdown": "systemShutdownCommand"}.get(path, "serverRestartCommand")
        cmd = plugin._settings.global_get(["server", "commands", key])
        if cmd:
            import sarge
            sarge.run(cmd, async_=True)
        else:
            plugin._logger.warning("KNOMI asked for %s but server.commands.%s isn't configured", path, key)
    else:
        plugin._logger.info("KNOMI command not supported over BLE: %s", path)


__plugin_name__ = "KNOMI"
__plugin_description__ = "Status, animations and an optional Bluetooth link for the KNOMI display (OctoPrint edition firmware)"
__plugin_license__ = "AGPLv3"
__plugin_url__ = "https://github.com/Binnacle-Tech/OctoPrint-KNOMI"
__plugin_pythoncompat__ = ">=3,<4"


def __plugin_load__():
    global __plugin_implementation__, __plugin_hooks__
    __plugin_implementation__ = KnomiPlugin()
    __plugin_hooks__ = {
        "octoprint.comm.protocol.gcode.sent": __plugin_implementation__.on_gcode_sent,
        "octoprint.comm.protocol.gcode.received": __plugin_implementation__.on_gcode_received,
        "octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information,
        "octoprint.server.http.bodysize": __plugin_implementation__.bodysize_hook,
    }
