# OctoPrint-KNOMI: BTT KNOMI display plugin for OctoPrint

**OctoPrint plugin for the BigTreeTech KNOMI / KNOMI 2 round display** (the Voron Stealthburner screen). With the **[KNOMI for OctoPrint firmware](https://github.com/Binnacle-Tech/KNOMI)**, it makes the KNOMI show what an OctoPrint printer is doing, including OctoPrint + Klipper (OctoKlipper) setups. Mainsail/Moonraker isn't needed.

<p align="center"><img src="docs/images/knomi-screens.png" alt="KNOMI 2 screens driven by OctoPrint: idle, homing, printing with time left, tinted idle face" width="100%"></p>

> [!IMPORTANT]
> **Only tested on a KNOMI 2 with OctoPrint on a Raspberry Pi 5.** Other setups should work but are untested. Reports are welcome.

## What it adds

- **Homing, probing / bed mesh, QGL, input shaper, PID tuning, nozzle cleaning, filament change and pause animations** on the KNOMI, driven by what OctoPrint is actually doing:
  - commands OctoPrint sends (`G28`, `QUAD_GANTRY_LEVEL`, `BED_MESH_CALIBRATE`, `M109`/`M190`, `SHAPER_CALIBRATE`, `PID_CALIBRATE`, `CLEAN_NOZZLE`, `LOAD_FILAMENT`, `PAUSE`/`M600`, and so on)
  - `// KNOMI <flag>=1` lines from your Klipper macros, so steps inside `PRINT_START` show up too ([`knomi_octoprint.cfg`](knomi_octoprint.cfg))
  - `// action:paused` / `// action:resumed`
- **Signals for Coaster, the KNOMI firmware's mascot:** filament runout (`M600` or a runout message), the part cooling fan speed (`M106`/`M107`) and the speed factor (`M220`), so it can look hungry, squint into the wind or hang on.
- **Display messages.** `M117`, `SET_DISPLAY_TEXT MSG=…` and `// action:notification` lines are passed to the KNOMI, where Coaster says them in a speech bubble (also available as `{msg}` in the print screen designer).
- **Coaster in your OctoPrint sidebar.** A live mirror of the KNOMI's Coaster: its mood, how it's feeling, its seasonal hat and the last print's report card (screams, peak g, dizzy spells). While the panel is open the KNOMI also sends where its head and pupils are a few times a second, and which quirk it's doing, so the sidebar moves with the real one (close, not frame-exact). Needs KNOMI firmware OP16 or newer (feelings: OP23, live motion, quirks and decorations: OP25).
- **Filament type for the KNOMI.** Read from the slicer's settings in the file (`filament_type`, the filament preset) or the file name, so Coaster can react to PLA, PETG, ABS, ASA, TPU, nylon or PC. Needs KNOMI firmware OP28 or newer.
- **Current layer and Z for the KNOMI.** OctoPrint doesn't report layers, so the plugin reads the file being printed, finds where each layer starts (slicer layer comments, or Z moves followed by extrusion) and follows OctoPrint's file position, the same way the G-code viewer does. This drives the KNOMI's layer triggers, `{layer}` and `{z}`. Files printed from OctoPrint's own storage only. Needs KNOMI firmware OP20 or newer.
- **Instant updates.** Changes are pushed to the KNOMI over OctoPrint's websocket.
- **Bluetooth LE link (optional).** The plugin connects to the KNOMI 2 directly, pushes status and the file list, and runs the KNOMI's touchscreen buttons inside OctoPrint. No API key, and the KNOMI can even run with WiFi off.
- No G28 or bed-mesh macro overrides are needed, so it avoids the "Macro G28 called recursively" and KAMP problems of the stock KNOMI macros.

## Install

OctoPrint → Settings → Plugin Manager → **Get More…** → **…from URL**:

```
https://github.com/Binnacle-Tech/OctoPrint-KNOMI/archive/refs/heads/main.zip
```

Restart OctoPrint when asked, then flash the **[KNOMI for OctoPrint firmware](https://github.com/Binnacle-Tech/KNOMI)** on the KNOMI and set its backend to OctoPrint.

<p align="center"><img src="docs/images/octoprint-settings.png" alt="OctoPrint-KNOMI plugin settings: tool, Bluetooth link, KNOMI address, status" width="75%"></p>

## Updates

OctoPrint's **Software Update** keeps the plugin current: it checks GitHub for new commits on `main` and shows
**Update now** under Settings › Software Update (and a notification). After the first install, updates are one click.
The first check after installing may offer an update even if you're current; installing it records the version.

## Bluetooth setup (optional)

1. On the KNOMI's web settings, set **Bluetooth → On**, then restart the KNOMI.
2. In OctoPrint, Settings › KNOMI › Bluetooth: **Find KNOMI**, **Pair**, and type the 6-digit code the KNOMI shows.
   The plugin pairs, trusts and remembers it, then connects. No `bluetoothctl` needed.
3. OctoPrint → Settings → **KNOMI** → tick **Connect to the KNOMI over Bluetooth**.

Settings › KNOMI › **KNOMI's own settings** opens all of the KNOMI's web pages inside OctoPrint, over WiFi or, with the KNOMI's WiFi off, over Bluetooth (KNOMI firmware OP41+).

Once connected, the KNOMI can turn its WiFi off. It comes back on its own if Bluetooth drops. The full guide is in [OCTOPRINT.md](https://github.com/Binnacle-Tech/KNOMI/blob/octoprint/OCTOPRINT.md#bluetooth).

## FAQ

**Does the KNOMI need Mainsail or Moonraker with this?** No. With this plugin and firmware, the KNOMI works with OctoPrint alone.

**Does it work with OctoKlipper?** Yes, that's the tested setup: OctoPrint + OctoKlipper on a Raspberry Pi 5.

**What's the API endpoint?** `GET /api/plugin/knomi` returns the status flags (`homing`, `probing`, `qgling`, `heating_nozzle`, `heating_bed`, `shaping`, `pid_tuning`, `cleaning`, `filament`, `paused`, `runout`) plus `fan` (part cooling fan %), `speed` (speed factor %), `msg`/`msg_id` (last display message) and, during a print, `layer`, `layers` and `z` (µm). Changes are also sent as plugin messages on the websocket.

## License

AGPL-3.0, see [LICENSE](LICENSE). Not affiliated with BIGTREETECH.
