<!-- markdownlint-configure-file { "MD024": { "siblings_only": true } } -->

# Changelog

All notable changes to Vital-Sign are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- More devices: second-generation Viatom rings such as the O2Ring S, PC-60FW
  fingertip oximeters, and any Bluetooth heart-rate strap, watch or pulse
  oximeter that uses the standard services. `--list-devices` shows what's in
  range and the exact `--source` value for each. ([#7](https://github.com/CleverTrou/Vital-Sign/pull/7))
- Smartwatch heart rate without Pulsoid, two ways: any watch that
  broadcasts heart rate over Bluetooth (Garmin's built-in broadcast, a
  broadcast app on Wear OS, or an iPhone relay app for Apple Watch;
  `--source hrs`), or the Health Data Server app for Apple Watch and
  Wear OS, which sends it over your network (`--source hds`). ([#7](https://github.com/CleverTrou/Vital-Sign/pull/7))
- Fallbacks: give `--source` more than once and each number comes from the
  first source with a live reading. With `--source viatom --source hds`, the
  watch's heart rate takes over when the ring is off. `SOURCES` in
  `config.sh`/`config.ps1` does the same for the start scripts. ([#7](https://github.com/CleverTrou/Vital-Sign/pull/7))
- `--source demo` shows simulated readings for setting up the overlay
  without hardware. ([#7](https://github.com/CleverTrou/Vital-Sign/pull/7))
- A control app for starting and stopping the bridge, watching the live
  readings, and choosing sources without editing files: `Vital Sign.app` on
  macOS, `Vital Sign.cmd` on Windows. It edits the same `config.sh`/`config.ps1`
  that OBS automation uses. ([#8](https://github.com/CleverTrou/Vital-Sign/pull/8))
- `--list-devices --json` for machine-readable scan results. ([#8](https://github.com/CleverTrou/Vital-Sign/pull/8))
- Setup steps for the ECHO iPhone relay app. ([#8](https://github.com/CleverTrou/Vital-Sign/pull/8))
- An app icon: a heart inside a green ring. On macOS 26 and later it follows
  the system's Dark, Clear and Tinted icon styles; Windows gets a matching
  `.ico`, and `create_shortcut.ps1` makes shortcuts that use it. ([#9](https://github.com/CleverTrou/Vital-Sign/pull/9))

### Changed

- When no SpO₂ has arrived for 30 seconds -- a watch or heart-rate strap, or
  a ring that's been taken off -- the overlay and the control window drop
  their SpO₂ half and narrow to heart rate alone, at the same size. SpO₂
  comes back as soon as a value arrives. ([#10](https://github.com/CleverTrou/Vital-Sign/pull/10))
- The overlay shows `--` for SpO₂ when the current source only measures
  heart rate, and its status messages no longer assume a ring. ([#7](https://github.com/CleverTrou/Vital-Sign/pull/7))
- With no `--source`, the bridge keeps scanning until a device shows up
  instead of exiting. If several are in range, it lists them and waits
  for you to pick one. ([#7](https://github.com/CleverTrou/Vital-Sign/pull/7))
- Requires Python 3.11 or newer. ([#7](https://github.com/CleverTrou/Vital-Sign/pull/7))
- New look for the control window: a copy of the overlay panel as OBS shows
  it, with the connection status beside it, one Start/Stop button, a sources
  table, and a log that stays collapsed until you need it. ([#9](https://github.com/CleverTrou/Vital-Sign/pull/9))
- The control window, the overlay's numbers and the wordmark use Atkinson
  Hyperlegible, a typeface designed for legibility. It's bundled and loaded
  only by Vital Sign; nothing is installed system-wide. ([#9](https://github.com/CleverTrou/Vital-Sign/pull/9))
- The heart-rate trend spans the full width from its first readings, then
  settles into a rolling 5-minute window. It used to start squeezed against
  the right edge until 5 minutes had passed. ([#9](https://github.com/CleverTrou/Vital-Sign/pull/9))

### Fixed

- On macOS, the control window could hang on open after it had been
  force-quit, waiting on a window-restore prompt that never appeared. ([#9](https://github.com/CleverTrou/Vital-Sign/pull/9))
- Start and Restart in the control window now check that `config.sh` still
  matches the sources the window shows. A deleted file is restored from the
  window, and a file edited elsewhere is shown before anything starts. A
  window left open used to start the bridge with no sources, so it scanned
  for a Bluetooth device instead of listening for the watch. ([#PR](https://github.com/CleverTrou/Vital-Sign/pull/PR))

## [0.2.0] - 2026-07-26

### Added

- BPM trend sparkline covering the last 5 minutes. The recommended OBS Browser
  Source size is now 600×180. ([#2](https://github.com/CleverTrou/Vital-Sign/pull/2))
- README preview screenshot. ([#4](https://github.com/CleverTrou/Vital-Sign/pull/4))

### Fixed

- The Advanced Scene Switcher guidance is a single macro with Or logic.
  Stopping the stream during a recording no longer kills the bridge.
  ([#1](https://github.com/CleverTrou/Vital-Sign/pull/1))
- `--scan-duration` rejects non-positive and non-finite values with a clear
  error. ([#1](https://github.com/CleverTrou/Vital-Sign/pull/1))

## [0.1.0] - 2026-07-25

First version.

### Added

- `bridge.py`, which reads a Checkme/Viatom O2 Max ring over BLE and broadcasts
  its readings on `ws://localhost:8765`.
- `overlay.html`, an OBS browser source showing heart rate and SpO₂.
- BLE auto-discovery, Windows (PowerShell) start/stop scripts, and
  `VitalSignBridge.app` for macOS.
- Guidance for starting and stopping the bridge automatically with Advanced
  Scene Switcher.

### Fixed

- A macOS crash when Advanced Scene Switcher launches the bridge.
- The SpO₂ severity marker was never styled.
- The heart icon was clipped in OBS.

[Unreleased]: https://github.com/CleverTrou/Vital-Sign/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/CleverTrou/Vital-Sign/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/CleverTrou/Vital-Sign/tree/v0.1.0
