$(function () {
    function KnomiViewModel(parameters) {
        var self = this;
        self.settings = parameters[0];

        // the KNOMI
        self.knomiStateText = ko.observable("…");
        self.knomiBadge = ko.observable("");
        self.knomiDetail = ko.observable("");
        self.knomiUrl = ko.observable("");
        self.coasterLine = ko.observable("");
        // the KNOMI's own pages through the plugin's proxy (/plugin/knomi/k/)
        self.pagesUrl = ko.observable((window.BASEURL || "/") + "plugin/knomi/k/");
        self.device = null;   // the KNOMI's own settings (knomi_device.js)
        self.pluginVersion = ko.observable("");
        // Bluetooth link
        self.bleStateText = ko.observable("off");
        self.bleBadge = ko.observable("");
        self.bleDetail = ko.observable("");
        self.bleAddress = ko.observable("");
        self.bleConnected = ko.observable(false);
        // pairing
        self.pairStatus = ko.observable("idle");
        self.pairMessage = ko.observable("");
        self.found = ko.observableArray([]);
        self.code = ko.observable("");
        self.pairBusy = ko.pureComputed(function () {
            var s = self.pairStatus();
            return s === "scanning" || s === "pairing" || s === "code";
        });

        function ago(s) {
            if (s === null || s === undefined) return "";
            if (s < 5) return "just now";
            if (s < 90) return s + " s ago";
            if (s < 5400) return Math.round(s / 60) + " min ago";
            return Math.round(s / 3600) + " h ago";
        }
        self.signal = function (rssi) {
            if (rssi === null || rssi === undefined) return "";
            return rssi > -60 ? "strong signal" : rssi > -75 ? "good signal" : "weak signal";
        };

        self.update = function (r) {
            self.pluginVersion(r.plugin_version || "");
            // the KNOMI
            var k = r.knomi || {};
            if (k.seen_s === null || k.seen_s === undefined) {
                self.knomiStateText("not seen yet"); self.knomiBadge("");
                self.knomiDetail("The KNOMI hasn't checked in since OctoPrint started. Set its backend to OctoPrint, or pair it over Bluetooth below.");
            } else if (k.seen_s < 60) {
                self.knomiStateText("online"); self.knomiBadge("label-success");
                self.knomiDetail("over " + k.via + (k.ip && k.via === "WiFi" ? " (" + k.ip + ")" : "") + " · " + ago(k.seen_s) + (k.fw ? " · " + k.fw : ""));
            } else {
                self.knomiStateText("offline"); self.knomiBadge("label-important");
                self.knomiDetail("last seen " + ago(k.seen_s) + (k.via ? " over " + k.via : ""));
            }
            self.knomiUrl(k.ip ? "http://" + k.ip + "/" : "");
            var c = r.coaster || {};
            self.coasterLine(c.mood ? "Coaster is " + c.mood + (c.feel ? ", feeling " + c.feel : "") : "");
            // Bluetooth
            var st = r.ble_state || "off";
            self.bleAddress(r.ble_address || self.settings.settings.plugins.knomi.ble_address() || "");
            self.bleConnected(st === "connected");
            var names = {off: "off", starting: "starting", searching: "searching", "not found": "not found",
                         connecting: "connecting", connected: "connected", disconnected: "retrying", error: "error"};
            self.bleStateText(names[st] || st);
            self.bleBadge(st === "connected" ? "label-success" : (st === "error" || st === "not found") ? "label-important" :
                          st === "off" ? "" : "label-warning");
            var d = "";
            if (st === "connected") d = "to " + r.ble_address;
            else if (st === "off") d = self.settings.settings.plugins.knomi.ble_enabled() ? "save the settings to start it" : "turned off";
            else if (r.ble_error) d = r.ble_error;
            else if (st === "not found") d = "no paired KNOMI found. Pair one below.";
            self.bleDetail(d);
            self.fwState = (r.fw_update || {}).state;
            // pairing
            var p = r.pair || {};
            if (p.status) {
                if (p.status !== self.pairStatus() && p.status === "code") self.code("");
                self.pairStatus(p.status);
                self.pairMessage(p.message || "");
                self.found(p.found || []);
            }
        };

        self.refresh = function () {
            return OctoPrint.simpleApiGet("knomi").done(self.update);
        };
        function cmd(name, data) {
            return OctoPrint.simpleApiCommand("knomi", name, data || {}).always(function () {
                if (on) { if (timer) { clearTimeout(timer); timer = null; } tick(); }   // and poll fast while it matters
                else self.refresh();
            });
        }
        self.scan = function () { self.pairStatus("scanning"); self.pairMessage("Looking for KNOMIs nearby..."); cmd("ble_scan"); };
        self.pair = function (item) { self.pairStatus("pairing"); self.pairMessage("Connecting to the KNOMI..."); cmd("ble_pair", {address: item.address}); };
        self.sendCode = function () { if (self.code().length === 6) { self.pairStatus("pairing"); self.pairMessage("Checking the code..."); cmd("ble_code", {code: self.code()}); } };
        self.codeKey = function (d, e) { if (e.keyCode === 13) self.sendCode(); return true; };
        self.cancel = function () { cmd("ble_cancel"); };
        self.reconnect = function () { cmd("ble_reconnect"); };
        self.forget = function () {
            self.pairStatus("pairing"); self.pairMessage("Forgetting...");
            cmd("ble_forget");
        };
        self.wifiOn = function () {
            OctoPrint.simpleApiCommand("knomi", "wifi_on").done(function (r) {
                self.bleDetail(r && r.ok ? "asked the KNOMI to turn WiFi on" : "not connected over Bluetooth");
            });
        };

        // Only while the KNOMI tab itself is open (onSettingsShown fires for the whole Settings dialog, and the
        // KNOMI's settings may come over Bluetooth): every 5 s, every 1.5 s while pairing or installing.
        var timer = null, on = false;
        function tabOpen() {
            var pane = document.getElementById("settings_plugin_knomi");
            return !!(pane && $(pane).hasClass("active") && pane.offsetParent);
        }
        function tick() {
            timer = null;
            if (!on) return;
            var p = (document.hidden ? null : self.refresh());
            var again = function () {
                if (!on || timer) return;
                var fast = self.pairBusy() || /^(downloading|sending)$/.test(self.fwState || "");
                timer = setTimeout(tick, fast ? 1500 : 5000);
            };
            if (p && p.always) p.always(again); else again();
        }
        function start() {
            if (on) return;
            on = true;
            tick();
            try {   // the KNOMI's own settings; a problem there mustn't take the link controls with it
                if (!self.device) self.device = new window.KnomiDevice(document.getElementById("knomi_device_settings"));
                self.device.load();
            } catch (e) { console.error("KNOMI settings:", e); }
        }
        function stop() {
            on = false;
            if (timer) { clearTimeout(timer); timer = null; }
        }
        function check() { if (tabOpen()) start(); else stop(); }
        $(document).on("shown", 'a[data-toggle="tab"]', function () { setTimeout(check, 0); });
        self.onSettingsShown = function () { setTimeout(check, 0); };
        self.onSettingsHidden = stop;
    }

    OCTOPRINT_VIEWMODELS.push({
        construct: KnomiViewModel,
        dependencies: ["settingsViewModel"],
        elements: ["#settings_plugin_knomi"]
    });
});
