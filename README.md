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

## Platform support

- **macOS** — **Verified** against a real Checkme O2 Max, first try, no code
  changes needed.
- **Windows 10/11** — Implemented (auto-discovery, `.ps1` start/stop scripts)
  but **not yet run against real hardware** — see the platform-specific notes
  throughout this README and the comments in `start_bridge.ps1`/`stop_bridge.ps1`.
- **Linux** — Untested, but `viatom-ble`/Bleak's primary development target is
  actually a Raspberry Pi, so this is likely the best-supported platform
  underneath — just use the `.sh` scripts and adjust the Bluetooth-permission
  step below.

If you test on Windows or Linux, opening an issue (or a PR) with what did or
didn't work is genuinely useful — this table reflects what's been confirmed,
not just what should theoretically work.

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
- No address to hunt down and paste anywhere: if you don't pin one, `bridge.py`
  scans for a compatible device on startup and connects to it automatically.
  This only needs pinning (see below) if you own more than one Viatom/Wellue/Checkme
  device, or want to skip the ~10s scan on every start. (This does its own
  stricter name matching rather than reusing `viatom-ble`'s built-in filter —
  see the `_looks_like_viatom` docstring in `bridge.py` for why: the library's
  own filter substring-matches "po" against device names, which false-positives
  on "AirPods Pro" — caught during testing with a real AirPods case nearby.)
- [`overlay.html`](overlay.html) is a self-contained page (no external
  requests, no build step) that connects to that WebSocket and renders a
  beating heart icon + BPM + SpO2, with a transparent background sized for
  compositing in OBS. It fades out and shows a status line if the bridge
  disconnects or the ring comes off your finger.

## Setup

**macOS / Linux:**

```bash
cd /path/to/vital-sign   # wherever you cloned this repo
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

On macOS, the first BLE scan will prompt for Bluetooth permission for your
terminal — grant it in *System Settings → Privacy & Security → Bluetooth*.
On Linux, add your user to the `bluetooth` group so BLE doesn't need `sudo`
(`sudo usermod -aG bluetooth $USER`, then log out and back in).

**Windows 10/11** (PowerShell; unverified against real hardware — see
[Platform support](#platform-support)):

```powershell
cd C:\path\to\vital-sign   # wherever you cloned this repo
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

If `Activate.ps1` refuses to run, PowerShell's default execution policy is
blocking it — see the execution-policy note in
[Automatic startup](#automatic-startup).

### 1. Run the bridge

**Make sure the ring is NOT currently connected to ViHealth or
O2Insight_Pro** — BLE only allows one active connection, so quit/disconnect
those apps first (fine, since you'd use this for streaming, not overnight
CPAP-sync sessions). Then, for a one-off manual run (useful the first time,
to watch the logs and confirm readings look sane):

```bash
python3 bridge.py -v          # macOS/Linux
python bridge.py -v           # Windows
```

With no `--address`, it scans for a compatible device and connects to
whichever one it finds — see it happen in the verbose log. If you own more
than one such device, pass one explicitly instead
(`--address <ADDRESS>`; find it with `viatom-ble --scan-interactive`).

For everyday use, use the start/stop scripts instead (idempotent — safe to
call repeatedly, tracks their own PID, logs to `bridge.log`):

```bash
./start_bridge.sh   # macOS/Linux: launches bridge.py in the background if not already running
./stop_bridge.sh    # macOS/Linux: stops it if running; no-ops otherwise
```

```powershell
.\start_bridge.ps1  # Windows equivalents
.\stop_bridge.ps1
```

These are what the automatic-startup section below wires into OBS. If you
want to pin an address or a non-default port for these scripts specifically,
copy `config.sh.example`/`config.ps1.example` to `config.sh`/`config.ps1`
and edit it — otherwise skip this, the defaults (auto-discover, port 8765)
just work.

### 2. Add the overlay to OBS

In OBS: **Sources → + → Browser Source**

- Check **Local file**, point it at the full path to `overlay.html` in
  wherever you cloned this repo
- Width/height: **`600x140`** as a starting box — measured, the panel's
  natural width is ~485px at typical 2-digit readings and ~525px at the
  worst case (3-digit HR + a severity marker), so `600` leaves real margin.
  A narrower box won't clip the heart/HR (the layout is left-anchored,
  see "Customizing the overlay" below) but will start cutting into the
  SpO2 side, so don't go much below this unless you also shrink the fonts.
- Check **Shutdown source when not visible** OFF (so it keeps its WebSocket
  connection warm across scene switches)
- The background is transparent — no chroma key needed

Reload the browser source (right-click → *Interact*/*Refresh*) any time you
restart `bridge.py`.

## Automatic startup

Stock OBS has no "run a command when X happens" hook, so this uses the free
[Advanced Scene Switcher](https://obsproject.com/forum/resources/advanced-scene-switcher.395/)
plugin instead of an OBS Python script — it avoids matching OBS's embedded
Python version to a venv containing `bleak`/`websockets`, which is a common
source of "script failed to load" errors unrelated to your actual code.

Advanced Scene Switcher can trigger on more than just scene changes; pick
whichever condition actually matches when you want the ring connected:

- **Streaming/Recording condition (recommended default)** — starts the
  bridge exactly when you go live or start recording, stops it when you
  stop, regardless of how many scenes you have or switch between. This is
  usually the better fit even if you *do* have multiple scenes, since you
  typically want the ring connected for the whole stream, not just one
  particular scene.
- **Scene condition** — only if you specifically want the overlay/BLE
  connection tied to one particular scene rather than the whole
  stream/recording (e.g. it only makes sense during a "workout" scene, not
  during a "chatting" scene). Requires an actual scene *transition* to
  fire — if the overlay's scene is your only scene, or you never leave it,
  this condition never triggers, since there's nothing to transition
  from/to. Use the Streaming/Recording condition instead in that case.

**Install:**

- macOS: download the `.pkg` from the
  [releases page](https://github.com/WarmUpTill/SceneSwitcher/releases),
  right-click it and choose *Open* to bypass Gatekeeper on an unnotarized
  installer, and follow the prompts.
- Windows: download the `-windows-x64-Installer.exe` from the same
  [releases page](https://github.com/WarmUpTill/SceneSwitcher/releases), run
  it, and click *More info → Run anyway* if SmartScreen blocks it (same
  unnotarized-installer situation as macOS's Gatekeeper, different dialog).

Either way, restart OBS afterward — *Advanced Scene Switcher* will appear
under the **Tools** menu.

**Configure** (Tools → Advanced Scene Switcher → Macro tab):

1. **New macro** — name it e.g. `vital-sign`.
2. **Condition** — pick one:
   - *Streaming/Recording (recommended)*: add a **Streaming** condition,
     state = **Stream running**. If you also want it running for local
     recordings without streaming, add a *second*, separate macro the same
     way but with a **Recording** condition, state = **Recording running**,
     pointing at the same Action/Else Action below — two macros both
     starting/stopping the same idempotent scripts is safe.
   - *Scene*: add a **Scene** condition, set to the scene that contains your
     `overlay.html` browser source. Only do this instead of (not in addition
     to) the above if you specifically want it tied to one scene rather than
     the whole stream — see the note above on why this needs an actual scene
     transition to fire.
3. **Action** → add a **Run** action (System category) pointing at your
   start script (see platform notes below for the exact path/arguments).
4. **Else Action** → add a **Run** action pointing at your stop script the
   same way.

   (Advanced Scene Switcher runs the main actions while the condition is
   true and the "Else Actions" once it becomes false — so e.g. going live
   starts the bridge, ending the stream stops it.)
5. Enable **"Run on change"** on the macro — without it, the start/stop
   actions would fire on every condition re-check instead of once per
   transition. (The scripts are idempotent either way, so this mostly just
   avoids log spam and unnecessary PID checks.)
6. Check the box to enable the macro, and hit **Start** in the General tab
   if the plugin isn't already running.

Exact field names may differ slightly by version — the "Run" action under
System is what you want.

**macOS Action (start) — use the wrapper app, not `start_bridge.sh` directly:**

- Path: `/usr/bin/open`
- Arguments: `-a "/path/to/vital-sign/VitalSignBridge.app"`

This is not optional cosmetics — pointing the Action directly at
`start_bridge.sh` will crash with a macOS privacy (TCC) error the moment it
tries to touch Bluetooth. When OBS spawns a Bluetooth-touching process as a
direct subprocess, macOS holds *OBS* responsible for that access, and
OBS.app's own `Info.plist` has no Bluetooth usage description — so instead
of prompting, macOS hard-crashes the process (you'll see `Responsible: OBS`
and `Namespace TCC` in the crash report if this happens). `VitalSignBridge.app`
is a minimal wrapper (tracked in this repo) with its own `Info.plist`
declaring Bluetooth usage; launching it via `open -a` makes it its own
independent process as far as macOS's privacy system is concerned, so its
own permission is what gets checked instead of OBS's. It just execs
`start_bridge.sh` — see `VitalSignBridge.app/Contents/MacOS/VitalSignBridge`.
Confirmed working end-to-end against a real running OBS instance (BLE
connect + the actual overlay browser source both came up clean).

**macOS Else Action (stop)** — `stop_bridge.sh` directly, full path, no
wrapper needed (stopping never touches Bluetooth, so TCC doesn't care who's
responsible for it).

**Linux path**: the full path to `start_bridge.sh`/`stop_bridge.sh`, no
arguments needed — the shebang line makes them directly executable, and
Linux doesn't have anything equivalent to the macOS TCC issue above.

**Windows path**: `.ps1` files aren't directly executable the way `.sh`
files are (no shebang-equivalent, and PowerShell's default execution policy
blocks scripts outright), so point the Run action at `powershell.exe`
itself and pass the script as an argument:

- If the Run action has one combined command-line field:
  `powershell.exe -ExecutionPolicy Bypass -File "C:\path\to\start_bridge.ps1"`
  (and the `stop_bridge.ps1` equivalent for the Else Action)
- If it has separate Path/Arguments fields: Path = `powershell.exe`,
  Arguments = `-ExecutionPolicy Bypass -File "C:\path\to\start_bridge.ps1"`

`-ExecutionPolicy Bypass` only affects this one invocation — it does not
change your system's execution policy permanently, so there's no lasting
security trade-off from using it here.

Check `bridge.log` (and, on Windows, `bridge.out.log`) if the overlay
doesn't light up after the condition triggers — it'll show whether the BLE
connection attempt happened at all.

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
- **Multiple devices found** (auto-discovery error listing more than one):
  you own more than one Viatom/Wellue/Checkme device, or another one is in
  range. Pin the one you want with `--address`, or `ADDRESS` in
  `config.sh`/`config.ps1`.
- **(Windows) `start_bridge.ps1` does nothing / "running scripts is
  disabled"**: PowerShell's execution policy is blocking it. Run it directly
  via `powershell.exe -ExecutionPolicy Bypass -File start_bridge.ps1` to
  confirm that's the cause — see the execution-policy note above for the
  permanent fix when wiring it into Advanced Scene Switcher.
- **(Windows) `bridge.log` looks empty after a run**: check
  `bridge.out.log` too — Start-Process splits stdout/stderr into separate
  files there, and both get overwritten (not appended) on every start,
  unlike the `.sh` scripts.
- **(macOS) OBS/Python crashes with a "Namespace TCC" / privacy error
  mentioning `NSBluetoothAlwaysUsageDescription` and `Responsible: OBS`**:
  you've pointed Advanced Scene Switcher's Action directly at
  `start_bridge.sh` instead of the `VitalSignBridge.app` wrapper — see the
  macOS Action instructions above for why that specific combination
  (OBS as parent, no wrapper) always crashes on Bluetooth access, and isn't
  something a code fix on our end can work around.
- **Overlay shows "Bridge offline" specifically inside OBS, but a plain
  browser tab pointed at the same `overlay.html` connects fine**: check
  `bridge.log` for `InvalidOrigin` — OBS's embedded browser can send a
  different `Origin` header for local-file sources than a regular browser
  does (this happened once already; see the `ALLOWED_ORIGINS` comment in
  `bridge.py`). Add whatever origin shows up in the rejection to that list.

## Customizing the overlay

`overlay.html` is a single file — edit the CSS variables at the top
(`--hr-color`, `--spo2-good/warn/bad`) or the layout directly. No build step;
just refresh the OBS browser source after saving.

The panel is left-anchored (`body { justify-content: flex-start }`), not
centered, on purpose: if the box you set is narrower than the panel's
natural content width, overflow only clips the right (SpO2) side, never
the heart/HR. Don't change this back to `center` without checking a
narrow box afterward — see the git history on this line for what
happened last time (real vitals sitting at 92% is what surfaced it).

## Security & privacy

- **Nothing leaves your machine.** `bridge.py` binds its WebSocket server to
  `localhost` only — it's never reachable from your network, let alone the
  internet. There's no cloud service, account, or API key involved anywhere
  in this project.
- **The WebSocket also checks the `Origin` header**, restricted to what
  `overlay.html` actually sends when loaded as a local file — both `null`
  (what a generic browser sends for `file://`) and `http://absolute` (what
  OBS's embedded browser sends for local-file browser sources specifically
  — confirmed against a real running OBS instance) — plus no-Origin-header
  clients (plain scripts/CLI tools). This stops an unrelated webpage open in
  a regular browser tab from quietly opening a WebSocket to this port and
  reading your live vitals in the background — see the `ALLOWED_ORIGINS`
  comment in `bridge.py`.
- **What's excluded from git** (see `.gitignore`): `config.sh`/`config.ps1`
  (contain your ring's BLE address — a device identifier, not a secret, but
  no reason to publish it), `bridge.log`/`bridge.out.log` (may contain
  historical readings), and `.bridge.pid`. Only the `.example` templates
  are tracked.
- **BLE pairing**: connecting means the ring briefly can't be reached by
  ViHealth/O2Insight_Pro (BLE allows one central connection at a time). No
  pairing credentials are stored beyond what `viatom-ble`/Bleak need to
  maintain the connection for that session.
- If you fork this and add your own integrations (e.g. pushing to a cloud
  service), audit what you're sending — heart rate and SpO2 are health data
  even in a casual streaming context.

## Accessibility

`overlay.html` doesn't rely on color alone to signal SpO2 severity — low
readings get a small ▲/⚠ marker badge next to the number, in addition to
the color change, so the state is legible without color perception. The
heartbeat pulse animation respects
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
