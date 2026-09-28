$(function () {
    function KnomiViewModel(parameters) {
        var self = this;
        self.settings = parameters[0];
        self.bleState = ko.observable("");

        self.refresh = function () {
            OctoPrint.simpleApiGet("knomi").done(function (r) {
                if (!r.ble_state || r.ble_state === "off") { self.bleState("Bluetooth off"); return; }
                var s = r.ble_state;
                if (r.ble_address) s += " (" + r.ble_address + ")";
                if (r.ble_error && r.ble_state !== "connected") s += " - " + r.ble_error;
                self.bleState(s);
            });
        };
        self.wifiOn = function () {
            OctoPrint.simpleApiCommand("knomi", "wifi_on").done(function (r) {
                self.bleState(r && r.ok ? "WiFi on requested" : "Not connected over Bluetooth");
            });
        };
        self.onSettingsShown = self.refresh;
    }

    OCTOPRINT_VIEWMODELS.push({
        construct: KnomiViewModel,
        dependencies: ["settingsViewModel"],
        elements: ["#settings_plugin_knomi"]
    });
});
