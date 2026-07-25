# Vital Sign

Live heart rate + SpO2 overlay for OBS, sourced directly from a Viatom/Wellue
Checkme Pulse O2 Max over Bluetooth LE — no phone app, no cloud service, no
Pulsoid account required.

```text
Checkme O2 Max --BLE--> bridge.py --WebSocket--> overlay.html (OBS Browser Source)
```

> This is a hobby project for putting vitals on a stream overlay, not a
> medical device or diagnostic tool. The Checkme O2 Max itself is
> consumer-grade, not clinical-grade — don't use readings from this (or the
> ring) to make health decisions.

## Why not Pulsoid?

Pulsoid's public API is read-only (widgets pull *from* your HR stream); pushing
a custom third-party source in requires emailing their support for OAuth
client credentials, and Pulsoid has no concept of SpO2 at all. Since the
Checkme's BLE protocol is already reverse-engineered and there's a
battle-tested Python library for it, a small local bridge gets both metrics
with lower latency and no external dependency.

## How it works

- [`bridge.py`](bridge.py) uses [`viatom-ble`](https://github.com/ecostech/viatom-ble)
  (MIT, cross-platform via [Bleak](https://github.com/hbldh/bleak)) to connect
  to the ring and poll it every 2 seconds. `viatom-ble` lists "Checkme O2" as
  a supported device family alongside the O2Ring/KidsO2 — same Viatom BLE
  service (`14839ac4-...`), same 0x17 "read sensors" command, same
  notification packet layout. **Confirmed working against a real Checkme O2
  Max on the first try** — no byte-offset changes needed.
- It re-broadcasts every reading as JSON over a local WebSocket
  (`ws://localhost:8765`).
- [`overlay.html`](overlay.html) is a self-contained page (no external
  requests, no build step) that connects to that WebSocket and renders a
  beating heart icon + BPM + SpO2, with a transparent background sized for
  compositing in OBS. It fades out and shows a status line if the bridge
  disconnects or the ring comes off your finger.

## Setup

```bash
cd /path/to/vital-sign   # wherever you cloned this repo
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.sh.example config.sh
```

On macOS, the first BLE scan will prompt for Bluetooth permission for your
terminal — grant it in *System Settings → Privacy & Security → Bluetooth*.

### 1. Find your device address

**Make sure the ring is NOT currently connected to ViHealth or
O2Insight_Pro** — BLE only allows one active connection, so quit/disconnect
those apps first (fine, since you'd use this for streaming, not overnight
CPAP-sync sessions).

```bash
viatom-ble --scan-interactive
```

Wear the ring and pick it from the list. Copy the address/UUID it prints —
you'll pass it to `bridge.py` next.

### 2. Run the bridge

For a one-off manual run (useful the first time, to watch the logs):

```bash
python3 bridge.py --address <ADDRESS_FROM_STEP_1> -v
```

For everyday use, put the address in the `config.sh` you copied from
`config.sh.example` during setup:

```bash
# edit config.sh:
ADDRESS="<ADDRESS_FROM_STEP_1>"
```

then use the start/stop scripts (idempotent — safe to call repeatedly,
tracks its own PID in `.bridge.pid`, logs to `bridge.log`):

```bash
./start_bridge.sh   # launches bridge.py in the background if not already running
./stop_bridge.sh    # stops it if running; no-ops otherwise
```

These are what the automatic-startup section below wires into OBS.

### 3. Add the overlay to OBS

In OBS: **Sources → + → Browser Source**

- Check **Local file**, point it at the full path to `overlay.html` in
  wherever you cloned this repo
- Width/height: `400x120` is a reasonable starting box; resize freely
- Check **Shutdown source when not visible** OFF (so it keeps its WebSocket
  connection warm across scene switches)
- The background is transparent — no chroma key needed

Reload the browser source (right-click → *Interact*/*Refresh*) any time you
restart `bridge.py`.

## Automatic startup tied to an OBS scene

Stock OBS has no "run a command when this scene activates" hook, so this
uses the free [Advanced Scene Switcher](https://obsproject.com/forum/resources/advanced-scene-switcher.395/)
plugin instead of an OBS Python script — it avoids matching OBS's embedded
Python version to a venv containing `bleak`/`websockets`, which is a common
source of "script failed to load" errors unrelated to your actual code.

**Install** (macOS): download the `.pkg` from the
[releases page](https://github.com/WarmUpTill/SceneSwitcher/releases),
right-click it and choose *Open* to bypass Gatekeeper on an unnotarized
installer, and follow the prompts. Restart OBS — *Advanced Scene Switcher*
will appear under the **Tools** menu.

**Configure** (Tools → Advanced Scene Switcher → Macro tab):

1. **New macro** — name it e.g. `vital-sign`.
2. **Condition** → add a **Scene** condition, set to the scene that contains
   your `overlay.html` browser source.
3. **Action** → add a **Run** action (System category), pointing at
   `start_bridge.sh` in wherever you cloned this repo (use the full path)
4. **Else Action** → add a **Run** action pointing at `stop_bridge.sh`
   the same way

   (Advanced Scene Switcher runs the main actions while the condition is
   true and the "Else Actions" once it becomes false — so switching *to*
   the scene starts the bridge, switching *away* stops it.)
5. Enable **"Run on change"** on the macro — without it, the start/stop
   actions would fire on every condition re-check instead of once per
   transition. (The scripts are idempotent either way, so this mostly just
   avoids log spam and unnecessary PID checks.)
6. Check the box to enable the macro, and hit **Start** in the General tab
   if the plugin isn't already running.

Exact field names may differ slightly by version — the "Run" action under
System is what you want; if there's a separate Path vs. Arguments field,
the full script path goes in Path with no arguments needed.

Check `bridge.log` if the overlay doesn't light up after a scene switch —
it'll show whether the BLE connection attempt happened at all.

## Troubleshooting

- **Overlay stuck on "Bridge offline"**: `bridge.py` isn't running, or the
  port doesn't match — both default to `8765`.
- **"Connecting to ring…" forever**: the ring is probably still paired to
  ViHealth/O2Insight_Pro. Force-quit that app/disconnect Bluetooth from it and
  restart `bridge.py`.
- **Readings look off (stuck at 0, or don't match the ring's own display)**:
  the O2 Max may use a slightly different byte layout than the O2Ring/Checkme
  O2 this library was built against. Run with `-v`, and if values look wrong,
  the fix is adjusting the byte offsets in the installed `viatom_ble/protocol.py`
  (`_IDX_SPO2` etc.) against the raw bytes logged — share a verbose log and
  I can help adjust them.
- **Ring battery draining fast while streaming**: expected — continuous BLE
  polling keeps the radio active the whole session, same as the official app
  would.

## Customizing the overlay

`overlay.html` is a single file — edit the CSS variables at the top
(`--hr-color`, `--spo2-good/warn/bad`) or the layout directly. No build step;
just refresh the OBS browser source after saving.

## Security & privacy

- **Nothing leaves your machine.** `bridge.py` binds its WebSocket server to
  `localhost` only — it's never reachable from your network, let alone the
  internet. There's no cloud service, account, or API key involved anywhere
  in this project.
- **The WebSocket also checks the `Origin` header**, restricted to what
  `overlay.html` actually sends when loaded as a local file (`Origin: null`)
  plus no-Origin-header clients (plain scripts/CLI tools). This stops an
  unrelated webpage open in a regular browser tab from quietly opening a
  WebSocket to this port and reading your live vitals in the background —
  see the `ALLOWED_ORIGINS` comment in `bridge.py`.
- **What's excluded from git** (see `.gitignore`): `config.sh` (contains
  your ring's BLE address — a device identifier, not a secret, but no
  reason to publish it), `bridge.log` (may contain historical readings),
  and `.bridge.pid`. Only `config.sh.example` (a placeholder template) is
  tracked.
- **BLE pairing**: connecting means the ring briefly can't be reached by
  ViHealth/O2Insight_Pro (BLE allows one central connection at a time). No
  pairing credentials are stored beyond what `viatom-ble`/Bleak need to
  maintain the connection for that session.
- If you fork this and add your own integrations (e.g. pushing to a cloud
  service), audit what you're sending — heart rate and SpO2 are health data
  even in a casual streaming context.

## Accessibility

`overlay.html` doesn't rely on color alone to signal SpO2 severity — low
readings get a text/symbol marker (▲/⚠) and a bold+underline style in
addition to the color change, so the state is legible without color
perception. The heartbeat pulse animation respects
`prefers-reduced-motion`. `aria-live` regions and labels are included for
the (secondary) case of opening the file directly in a browser tab rather
than through OBS, which renders to video and isn't accessible to assistive
tech regardless of the page's own markup.

## Credits

- [`viatom-ble`](https://github.com/ecostech/viatom-ble) (MIT) — the
  reverse-engineered Viatom/Wellue BLE protocol and client this project is
  built on.
- [Bleak](https://github.com/hbldh/bleak) (MIT) — the cross-platform BLE
  library `viatom-ble` uses under the hood.
- [Advanced Scene Switcher](https://github.com/WarmUpTill/SceneSwitcher) —
  the OBS plugin used for scene-triggered automation.

## License

[MIT](LICENSE).
