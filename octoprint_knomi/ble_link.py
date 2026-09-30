"""
Bluetooth LE link to the KNOMI (the KNOMI is the peripheral, the Pi connects).

Runs its own asyncio loop in a daemon thread. While connected it writes a compact
status JSON to the KNOMI (on change, and at least every 2 s as a heartbeat), sends
the file list when asked or when files change, and receives the KNOMI's commands
(Moonraker-style paths, the same strings the KNOMI UI queues) as notifications.

Pairing is done once with bluetoothctl (the KNOMI shows the passkey). After that
BlueZ reuses the bond automatically.
"""
import asyncio
import json
import threading
import time

SERVICE_UUID = "4b4e4f4d-4900-4c69-6e6b-000000000001"
STATUS_UUID = "4b4e4f4d-4900-4c69-6e6b-000000000002"
FILES_UUID = "4b4e4f4d-4900-4c69-6e6b-000000000003"
CMD_UUID = "4b4e4f4d-4900-4c69-6e6b-000000000004"

HEARTBEAT_S = 2.0
POLL_S = 0.25
RETRY_S = 5.0
FILES_MAX = 1023      # KNOMI roller buffer
FILES_CHUNK = 400


def encode_files(paths):
    """File list -> BLE frames: [flags][utf-8 text], flags 1 = start, 2 = end."""
    text = ""
    for p in paths:
        line = p + "\n"
        if len((text + line).encode("utf-8")) > FILES_MAX:
            break
        text += line
    data = text.encode("utf-8")
    chunks = [data[i:i + FILES_CHUNK] for i in range(0, len(data), FILES_CHUNK)] or [b""]
    frames = []
    for i, c in enumerate(chunks):
        flags = (1 if i == 0 else 0) | (2 if i == len(chunks) - 1 else 0)
        frames.append(bytes([flags]) + c)
    return frames


def bluez_device(address):
    """A bleak device for a KNOMI BlueZ already knows (paired), so bleak doesn't have to see it
    advertise first. On BlueZ, bleak connects straight to the D-Bus object path."""
    from bleak.backends.device import BLEDevice
    details = {"path": "/org/bluez/hci0/dev_" + address.upper().replace(":", "_"),
               "props": {"Alias": "KNOMI", "Adapter": "/org/bluez/hci0", "Address": address.upper()}}
    try:
        return BLEDevice(address, "KNOMI", details)
    except TypeError:   # older bleak also wants an RSSI
        return BLEDevice(address, "KNOMI", details, -60)


def paired_knomi():
    """Address of a KNOMI the Pi has already paired with (bluetoothctl), or None."""
    import subprocess
    for args in (["bluetoothctl", "devices", "Paired"], ["bluetoothctl", "paired-devices"]):
        try:
            out = subprocess.run(args, capture_output=True, text=True, timeout=5).stdout
        except Exception:
            continue
        for line in out.splitlines():
            parts = line.split(None, 2)   # "Device CC:BA:97:07:9C:D5 KNOMI-KNOMI"
            if len(parts) == 3 and parts[0] == "Device" and parts[2].startswith("KNOMI"):
                return parts[1]
    return None


class BleLink:
    def __init__(self, plugin, logger):
        self._plugin = plugin
        self._logger = logger
        self._thread = None
        self._stop = threading.Event()
        # Each start() is a new generation; a thread from an older one (still stuck in a 20 s connect when
        # stop() gave up waiting) sees it has been replaced and quits instead of fighting the new one.
        self._gen = 0
        self._local = threading.local()
        self._wifi_request = False
        self._files_dirty = True
        self.state = "off"
        self.address = ""
        self.last_error = ""
        self._use_path = False

    # ---- control (any thread) ----------------------------------------

    def start(self):
        if self._thread and self._thread.is_alive() and not self._stop.is_set():
            return
        self._gen += 1
        self._stop.clear()
        self.state = "starting"
        self._thread = threading.Thread(target=self._run, args=(self._gen,), name="knomi-ble", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._gen += 1   # whatever is running now is done, even if it doesn't notice within the join
        if self._thread:
            self._thread.join(timeout=2)
        self._thread = None
        self.state = "off"
        self.last_error = ""

    def request_wifi(self):
        self._wifi_request = True

    def files_changed(self):
        self._files_dirty = True

    # ---- link thread ------------------------------------------------------

    def _halted(self):
        return self._stop.is_set() or getattr(self._local, "gen", None) != self._gen

    def _run(self, gen):
        self._local.gen = gen
        try:
            asyncio.run(self._main())
        except Exception as e:  # pragma: no cover
            self._logger.exception("KNOMI BLE thread died")
            if not self._halted():
                self.last_error = str(e)
                self.state = "error"

    async def _find(self):
        from bleak import BleakScanner
        self.state = "searching"
        devices = await BleakScanner.discover(timeout=8.0, service_uuids=[SERVICE_UUID])
        if devices:
            return devices[0].address
        # A KNOMI that's already connected (for example, left connected by bluetoothctl after pairing)
        # stops advertising, so a scan can't see it. Look for one the Pi has already paired with.
        address = paired_knomi()
        if address:
            self._logger.info("KNOMI BLE: not advertising, using the paired KNOMI at %s", address)
        return address

    async def _send_files(self, client):
        for frame in encode_files(self._plugin.ble_file_list()):
            await client.write_gatt_char(FILES_UUID, frame, response=True)

    def _on_cmd(self, _sender, data):
        try:
            path = bytes(data).decode("utf-8", errors="replace")
        except Exception:
            return
        if path == "/knomi/files":
            self._files_dirty = True
            return
        try:
            self._plugin.ble_command(path)
        except Exception:
            self._logger.exception("KNOMI BLE command failed: %s", path)

    async def _main(self):
        try:
            from bleak import BleakClient
        except ImportError:
            self.state = "error"
            self.last_error = "bleak is not installed"
            return

        while not self._halted():
            try:
                address = self._plugin.ble_configured_address() or await self._find()
                if not address:
                    if self.state != "not found":
                        self._logger.info("KNOMI BLE: no KNOMI found. Is Bluetooth on in the KNOMI's settings, and has the Pi paired with it?")
                    self.state = "not found"
                    await self._sleep(10)
                    continue
                self.state = "connecting"
                self.address = address
                gone = asyncio.Event()
                loop = asyncio.get_running_loop()
                # a KNOMI that's already connected (bluetoothctl) doesn't advertise, and bleak won't
                # connect to an address it hasn't seen; point it at the paired device instead
                target = bluez_device(address) if self._use_path else address
                async with BleakClient(target, timeout=20.0,
                                       disconnected_callback=lambda _c: loop.call_soon_threadsafe(gone.set)) as client:
                    await client.start_notify(CMD_UUID, self._on_cmd)
                    self.state = "connected"
                    self.last_error = ""
                    self._files_dirty = True
                    self._logger.info("KNOMI BLE connected to %s", address)
                    self._plugin.ble_remember_address(address)
                    last, last_t = None, 0.0
                    while not self._halted() and not gone.is_set():
                        if self._files_dirty:
                            self._files_dirty = False
                            await self._send_files(client)
                        wifi = self._wifi_request
                        self._wifi_request = False
                        st = self._plugin.ble_status(wifi=wifi)
                        payload = json.dumps(st, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
                        while len(payload) > 500 and st.get("m"):   # one BLE write holds 512 bytes: shorten the message
                            st["m"] = st["m"][:-8]
                            payload = json.dumps(st, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
                        now = time.monotonic()
                        if payload != last or now - last_t >= HEARTBEAT_S or wifi:
                            await client.write_gatt_char(STATUS_UUID, payload, response=True)
                            last, last_t = payload, now
                        await asyncio.sleep(POLL_S)
            except (FileNotFoundError, ConnectionRefusedError):
                msg = "Bluetooth service not available (is bluetoothd running, and is Bluetooth enabled on the Pi?)"
                self.last_error = msg
                self._logger.info("KNOMI BLE: %s", msg)
            except Exception as e:
                msg = str(e) or e.__class__.__name__
                if "not found" in msg.lower() and not self._use_path:
                    self._use_path = True   # try the paired device directly, right away
                    self._logger.info("KNOMI BLE: %s, trying the paired device directly", msg)
                    continue
                if "auth" in msg.lower() or "encrypt" in msg.lower() or "not paired" in msg.lower():
                    msg += " (pair once with bluetoothctl; the KNOMI shows the code)"
                self.last_error = msg
                self._logger.info("KNOMI BLE: %s", msg)
            if not self._halted():
                self.state = "disconnected"
                await self._sleep(RETRY_S)
        if getattr(self._local, "gen", None) == self._gen:
            self.state = "off"

    async def _sleep(self, seconds):
        end = time.monotonic() + seconds
        while not self._halted() and time.monotonic() < end:
            await asyncio.sleep(0.25)
