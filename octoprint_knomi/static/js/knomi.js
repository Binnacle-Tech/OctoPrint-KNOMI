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
            return OctoPrint.simpleApiCommand("knomi", name, data || {}).always(self.refresh);
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

        var timer = null;
        self.onSettingsShown = function () {
            self.refresh();
            if (!timer) timer = setInterval(self.refresh, 1500);
        };
        self.onSettingsHidden = function () {
            if (timer) { clearInterval(timer); timer = null; }
        };
    }

    OCTOPRINT_VIEWMODELS.push({
        construct: KnomiViewModel,
        dependencies: ["settingsViewModel"],
        elements: ["#settings_plugin_knomi"]
    });
});
