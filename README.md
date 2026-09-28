# OctoPrint-KNOMI

Companion plugin for the **[KNOMI OctoPrint edition firmware](https://github.com/Binnacle-Tech/KNOMI/tree/octoprint)**. It lets the BTT KNOMI display show what an OctoPrint + Klipper (OctoKlipper) printer is doing.

## What it does

- **Status flags** at `GET /api/plugin/knomi`: `homing`, `probing`, `qgling`, `heating_nozzle`, `heating_bed`, `shaping`, `pid_tuning`, `cleaning`, `filament`, `paused`. The same data is pushed over OctoPrint's websocket whenever it changes. The flags come from:
  - commands OctoPrint sends (G28, QUAD_GANTRY_LEVEL, BED_MESH_CALIBRATE, M109/M190, SHAPER_CALIBRATE, PID_CALIBRATE, CLEAN_NOZZLE, LOAD/UNLOAD_FILAMENT, PAUSE/RESUME, M600, and so on). Each flag is raised when the command is sent and cleared on its `ok`.
  - `// KNOMI <flag>=<0|1>` lines from Klipper macros, so steps inside `PRINT_START` show up too. See [`knomi_octoprint.cfg`](knomi_octoprint.cfg).
  - `// action:paused` / `// action:resumed` lines.
- **Bluetooth LE link** (optional). The plugin connects to the KNOMI, pushes status and the file list, and runs the KNOMI's buttons inside OctoPrint. It needs no API key, and the KNOMI can keep its WiFi off while the link is up.

## Install

OctoPrint → Settings → Plugin Manager → **Get More** → *from URL*:

```
https://github.com/Binnacle-Tech/OctoPrint-KNOMI/archive/refs/heads/main.zip
```

Restart OctoPrint when asked. Bluetooth support pulls in [`bleak`](https://github.com/hbldh/bleak), which is installed automatically.

## Bluetooth setup

1. Turn Bluetooth on in the KNOMI's web settings, then restart the KNOMI.
2. Pair once from the Pi. The KNOMI shows a 6-digit code:
   ```
   bluetoothctl
   scan on
   pair  <KNOMI address>
   trust <KNOMI address>
   ```
   If `/boot/config.txt` has `dtoverlay=disable-bt`, remove it and reboot first.
3. Settings → KNOMI: tick **Connect to the KNOMI over Bluetooth**.

Full firmware-side setup is in [OCTOPRINT.md](https://github.com/Binnacle-Tech/KNOMI/blob/octoprint/OCTOPRINT.md).

## License

AGPL-3.0. See [LICENSE](LICENSE).
