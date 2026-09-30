"""Pair the Pi with a KNOMI from OctoPrint's settings, no bluetoothctl needed.

The KNOMI can only show a code (display-only), so the Pi has to type it in. BlueZ asks
for the code through an "agent"; this module registers one on the system D-Bus, starts the
pairing and hands BlueZ the code the user typed on the settings page.

Everything runs on its own thread with its own asyncio loop; the settings page polls
`state()` and calls `scan()`, `pair()`, `code()`, `cancel()` and `forget()`.
"""
import asyncio
import threading
import time

AGENT_PATH = "/octoprint/knomi/agent"
BLUEZ = "org.bluez"
SERVICE_UUID = "4b4e4f4d-4900-4c69-6e6b-000000000001"


def _dev_path(address, adapter="hci0"):
    return "/org/bluez/%s/dev_%s" % (adapter, address.upper().replace(":", "_"))


class Pairer:
    def __init__(self, logger, on_paired, on_done=None):
        self._logger = logger
        self._on_paired = on_paired        # called with the address when pairing worked
        self._on_done = on_done            # called with True/False when a pairing attempt ends, any way
        self._cancelled = False
        self._lock = threading.Lock()
        self._thread = None
        self._loop = None
        self._code_future = None
        self.status = "idle"               # idle, scanning, found, pairing, code, done, error
        self.message = ""
        self.found = []                    # [{"address", "name", "rssi", "paired"}]
        self.target = ""

    # ---- called from the web thread ----------------------------------------

    def state(self):
        with self._lock:
            return {"status": self.status, "message": self.message, "found": list(self.found), "target": self.target}

    def busy(self):
        return self._thread is not None and self._thread.is_alive()

    def scan(self):
        return self._start(self._scan())

    def pair(self, address):
        if self.busy():
            return False
        self.target = address.upper()
        self._cancelled = False
        return self._start(self._pair(self.target), done=True)

    def forget(self, address):
        return self._start(self._forget(address.upper()))

    def code(self, code):
        code = "".join(ch for ch in str(code) if ch.isdigit())
        if len(code) != 6 or not self._loop or not self._code_future:
            return False
        fut, loop = self._code_future, self._loop
        loop.call_soon_threadsafe(lambda: fut.done() or fut.set_result(int(code)))
        return True

    def cancel(self):
        self._cancelled = True   # the pairing thread checks this between steps
        if self._loop and self._code_future:
            fut = self._code_future
            self._loop.call_soon_threadsafe(lambda: fut.done() or fut.cancel())
        self._set("idle", "")

    # ---- internals ------------------------------------------------------------

    def _set(self, status, message=""):
        with self._lock:
            self.status, self.message = status, message

    def _start(self, coro, done=False):
        if self.busy():
            coro.close()
            return False

        def run():
            loop = asyncio.new_event_loop()
            self._loop = loop
            ok = False
            try:
                ok = bool(loop.run_until_complete(coro))
            except asyncio.CancelledError:
                pass
            except Exception as e:  # pragma: no cover
                self._logger.exception("KNOMI pairing failed")
                self._set("error", str(e) or e.__class__.__name__)
            finally:
                self._code_future = None
                self._loop = None
                loop.close()
                if done and self._on_done:
                    try:
                        self._on_done(ok)
                    except Exception:  # pragma: no cover
                        self._logger.exception("KNOMI pairing: done callback failed")

        self._thread = threading.Thread(target=run, name="knomi-pair", daemon=True)
        self._thread.start()
        return True

    async def _paired_addresses(self, bus):
        """Addresses BlueZ has paired, from its object tree."""
        from dbus_fast import Message
        reply = await bus.call(Message(destination=BLUEZ, path="/", interface="org.freedesktop.DBus.ObjectManager",
                                       member="GetManagedObjects"))
        out = {}
        for path, ifaces in (reply.body[0] if reply.body else {}).items():
            dev = ifaces.get("org.bluez.Device1")
            if dev:
                name = dev.get("Name") or dev.get("Alias")
                out[dev["Address"].value.upper()] = {
                    "path": path, "paired": bool(dev.get("Paired") and dev["Paired"].value),
                    "name": name.value if name else "", "connected": bool(dev.get("Connected") and dev["Connected"].value),
                }
        return out

    async def _bus(self):
        from dbus_fast import BusType
        from dbus_fast.aio import MessageBus
        return await MessageBus(bus_type=BusType.SYSTEM).connect()

    async def _scan(self):
        from bleak import BleakScanner
        self._set("scanning", "Looking for KNOMIs nearby...")
        found = {}
        try:
            devices = await BleakScanner.discover(timeout=8.0, service_uuids=[SERVICE_UUID], return_adv=True)
            for d, adv in devices.values():
                found[d.address.upper()] = {"address": d.address.upper(), "name": d.name or adv.local_name or "KNOMI",
                                            "rssi": adv.rssi, "paired": False}
        except Exception as e:
            self._set("error", "Bluetooth scan failed: %s" % (str(e) or e.__class__.__name__))
            return
        # KNOMIs the Pi already paired with (a connected one doesn't show up in a scan)
        try:
            bus = await self._bus()
            for addr, info in (await self._paired_addresses(bus)).items():
                if info["name"].startswith("KNOMI") and (info["paired"] or addr in found):
                    item = found.setdefault(addr, {"address": addr, "name": info["name"], "rssi": None, "paired": False})
                    item["paired"] = info["paired"]
            bus.disconnect()
        except Exception:
            pass
        with self._lock:
            self.found = sorted(found.values(), key=lambda x: -(x["rssi"] or -999))
        if found:
            self._set("found", "")
        else:
            self._set("error", "No KNOMI found. Turn Bluetooth on in the KNOMI's settings (it restarts), keep it "
                               "within a few meters, and try again.")

    async def _pair(self, address):
        from dbus_fast import Variant
        from dbus_fast.service import ServiceInterface, method

        pairer = self

        class Agent(ServiceInterface):
            def __init__(self):
                super().__init__("org.bluez.Agent1")

            @method()
            def Release(self):
                pass

            @method()
            async def RequestPasskey(self, device: "o") -> "u":  # noqa: F821
                loop = asyncio.get_running_loop()
                pairer._code_future = loop.create_future()
                pairer._set("code", "Type the 6-digit code the KNOMI is showing.")
                return await asyncio.wait_for(pairer._code_future, timeout=90)

            @method()
            def RequestPinCode(self, device: "o") -> "s":  # noqa: F821
                return "000000"

            @method()
            def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):  # noqa: F821
                pass

            @method()
            def DisplayPinCode(self, device: "o", pincode: "s"):  # noqa: F821
                pass

            @method()
            def RequestConfirmation(self, device: "o", passkey: "u"):  # noqa: F821
                pass   # numeric comparison: accept

            @method()
            def RequestAuthorization(self, device: "o"):  # noqa: F821
                pass

            @method()
            def AuthorizeService(self, device: "o", uuid: "s"):  # noqa: F821
                pass

            @method()
            def Cancel(self):
                pairer._set("error", "The KNOMI cancelled pairing. Try again.")

        self._set("pairing", "Connecting to the KNOMI...")
        bus = await self._bus()
        agent = Agent()
        bus.export(AGENT_PATH, agent)
        root = bus.get_proxy_object(BLUEZ, "/org/bluez", await bus.introspect(BLUEZ, "/org/bluez"))
        manager = root.get_interface("org.bluez.AgentManager1")
        await manager.call_register_agent(AGENT_PATH, "KeyboardOnly")
        try:
            known = await self._paired_addresses(bus)
            adapter_path = "/org/bluez/hci0"
            adapter = bus.get_proxy_object(BLUEZ, adapter_path, await bus.introspect(BLUEZ, adapter_path)).get_interface("org.bluez.Adapter1")
            if address in known:   # start clean: an old pairing on either side makes the new one fail
                try:
                    await adapter.call_remove_device(known[address]["path"])
                except Exception:
                    pass
            # BlueZ has to see it advertise before it can pair
            self._set("pairing", "Waiting for the KNOMI to show up...")
            await adapter.call_start_discovery()
            path = None
            for _ in range(30):
                if self._cancelled:
                    return False
                known = await self._paired_addresses(bus)
                if address in known:
                    path = known[address]["path"]
                    break
                await asyncio.sleep(0.5)
            try:
                await adapter.call_stop_discovery()
            except Exception:
                pass
            if not path:
                self._set("error", "The KNOMI didn't show up. Is it advertising (Bluetooth on, not connected to "
                                   "anything else)? Use Forget on the KNOMI's Bluetooth settings if it was paired before.")
                return
            dev_obj = bus.get_proxy_object(BLUEZ, path, await bus.introspect(BLUEZ, path))
            device = dev_obj.get_interface("org.bluez.Device1")
            props = dev_obj.get_interface("org.freedesktop.DBus.Properties")
            if self._cancelled:
                return False
            self._set("pairing", "Pairing... the KNOMI will show a code.")
            try:
                await asyncio.wait_for(device.call_pair(), timeout=120)
            except asyncio.TimeoutError:
                self._set("error", "Pairing timed out. Try again, and type the code within a minute.")
                return
            except Exception as e:
                msg = str(e) or e.__class__.__name__
                if "AuthenticationFailed" in msg or "Authentication" in msg:
                    msg = "Wrong code, or the KNOMI still remembers an old pairing (Forget it on the KNOMI's settings page)."
                elif "AlreadyExists" in msg:
                    msg = ""
                if msg:
                    self._set("error", "Pairing failed: " + msg)
                    return
            if self._cancelled:
                return False
            await props.call_set("org.bluez.Device1", "Trusted", Variant("b", True))
            try:
                await device.call_disconnect()   # let the plugin's link connect fresh
            except Exception:
                pass
            self._set("done", "Paired with the KNOMI. Connecting...")
            self._logger.info("KNOMI BLE: paired with %s", address)
            self._on_paired(address)
            return True
        finally:
            try:
                await manager.call_unregister_agent(AGENT_PATH)
            except Exception:
                pass
            bus.disconnect()

    async def _forget(self, address):
        self._set("pairing", "Forgetting the KNOMI...")
        bus = await self._bus()
        try:
            known = await self._paired_addresses(bus)
            if address in known:
                adapter_path = "/org/bluez/hci0"
                adapter = bus.get_proxy_object(BLUEZ, adapter_path, await bus.introspect(BLUEZ, adapter_path)).get_interface("org.bluez.Adapter1")
                await adapter.call_remove_device(known[address]["path"])
            self._set("idle", "Forgotten on the Pi. Also press Forget paired devices on the KNOMI's settings page before pairing again.")
        finally:
            bus.disconnect()
