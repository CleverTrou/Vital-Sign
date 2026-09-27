<!-- markdownlint-configure-file { "MD024": { "siblings_only": true } } -->

# Changelog

All notable changes to Vital-Sign are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
[0.1.0]: https://github.com/CleverTrou/Vital-Sign/releases/tag/v0.1.0
