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


class KnomiPlugin(octoprint.plugin.SimpleApiPlugin,
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
        self._last_pushed = None
        self._ble = None

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
            status = {f: bool(f in self._cmd_flags or self._marker_flags[f]) for f in FLAGS}
        status["fan"] = self._fan
        status["speed"] = self._speed
        status["msg"] = self._msg
        status["msg_id"] = self._msg_id
        # tells the KNOMI which progress OctoPrint's dashboard shows
        status["time_progress"] = self._time_progress()
        return status

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
        return {"ble_enabled": False, "ble_address": "", "tool": "tool0"}

    def is_template_autoescaped(self):
        return True

    def get_template_configs(self):
        return [{"type": "settings", "custom_bindings": True},
                {"type": "sidebar", "name": "Coaster", "icon": "smile-o", "custom_bindings": True,
                 "template": "knomi_sidebar.jinja2"}]

    def get_assets(self):
        return {"js": ["js/knomi.js", "js/coaster.js"]}

    def on_after_startup(self):
        self._ble = BleLink(self, self._logger)
        if self._settings.get_boolean(["ble_enabled"]):
            self._ble.start()

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
        return build_ble_status(self._printer.get_current_data(),
                                self._printer.get_current_temperatures(),
                                self._status(),
                                self._settings.get(["tool"]) or "tool0",
                                wifi, self._time_progress())

    def ble_file_list(self):
        from octoprint.filemanager.destinations import FileDestinations
        files = self._file_manager.list_files(FileDestinations.LOCAL, recursive=True)
        return collect_paths(files.get(FileDestinations.LOCAL, {}))

    def ble_command(self, path):
        handle_knomi_command(self, path)

    def on_event(self, event, payload):
        if self._ble and event in (Events.FILE_ADDED, Events.FILE_REMOVED, Events.UPDATED_FILES):
            self._ble.files_changed()
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
        return {"wifi_on": [], "coaster": []}

    def coaster_update(self, data):
        """The KNOMI reports Coaster's mood (and the last print's report card)."""
        clean = {"mood": str(data.get("mood", ""))[:24], "hat": int(data.get("hat", 0) or 0)}
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
            self.coaster_update(data)
            return flask.jsonify(ok=True)
        if command == "wifi_on" and self._ble:
            self._ble.request_wifi()
            return flask.jsonify(ok=self._ble.state == "connected")

    def on_api_get(self, request):
        result = self._status()
        result["coaster"] = self._coaster
        if self._ble:
            result.update(ble_state=self._ble.state, ble_address=self._ble.address,
                          ble_error=self._ble.last_error)
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
    }
