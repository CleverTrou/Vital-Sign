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
  range and the exact `--source` value for each.
- Apple Watch heart rate without Pulsoid, two ways: the Health Data Server
  watch app sends it straight to the bridge over your network
  (`--source hds`), or an iPhone relay app makes the watch look like a
  Bluetooth heart-rate strap (`--source hrs`).
- Fallbacks: give `--source` more than once and each number comes from the
  first source with a live reading. With `--source viatom --source hds`, the
  watch's heart rate takes over when the ring is off. `SOURCES` in
  `config.sh`/`config.ps1` does the same for the start scripts.
- `--source demo` shows simulated readings for setting up the overlay
  without hardware.

### Changed

- The overlay shows `--` for SpO₂ when the current source only measures
  heart rate, and its status messages no longer assume a ring.
- With no `--source`, the bridge keeps scanning until a device shows up
  instead of exiting. If several are in range, it lists them and waits
  for you to pick one.
- Requires Python 3.11 or newer.

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
