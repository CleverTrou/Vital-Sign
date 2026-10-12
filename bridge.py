#!/usr/bin/env python3
"""Vitals bridge: Bluetooth oximeters, heart-rate sensors and watches -> local WebSocket -> OBS overlay.

Reads one or more sources (a Viatom/Wellue ring or fingertip oximeter, any
standard Bluetooth heart-rate or pulse-oximeter device, or the Health Data
Server watch app), merges them by priority, and re-broadcasts the result as
JSON to any WebSocket clients (i.e. overlay.html running as an OBS Browser
Source). Run this instead of connecting the device to its phone app -- BLE
peripherals only accept one central connection at a time.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import math
import signal

import websockets

from vitalsign import merge
from vitalsign.hds import DEFAULT_ALLOWED_NETWORKS
from vitalsign.protocols import HDS_DEFAULT_PORT, Vitals
from vitalsign.sources import DEMO, HDS, SOURCE_HELP, SourceSpec, resolve_specs

log = logging.getLogger("bridge")

# All currently-connected overlay.html clients, so a reading can be fanned
# out to every open browser source (normally just one, but nothing stops
# you from adding the overlay to multiple scenes at once).
CLIENTS: set = set()

# overlay.html is meant to be loaded as a local file (OBS Browser Source ->
# "Local file"). A generic browser would send a literal Origin: null header
# for a file:// page, but OBS's embedded CEF browser sends "http://absolute"
# instead for local-file sources specifically -- confirmed by testing
# against a real running OBS instance, whose Browser Source repeatedly
# retried and got rejected with this exact Origin until it was added here.
# `None` covers the absent-header case (plain WebSocket test clients, curl,
# etc). Restricting to these means a webpage open in a *real* browser tab
# can't quietly open a WebSocket to this port and read your live vitals in
# the background; see the Origin allowlist docs at
# https://websockets.readthedocs.io/en/stable/topics/broadcast.html
ALLOWED_ORIGINS = ["null", "http://absolute", None]


class Hub:
    """Merges source output and fans it out to overlay clients."""

    def __init__(self, source_count: int, fresh_seconds: float) -> None:
        self.merger = merge.Merger(source_count, fresh_seconds=fresh_seconds)
        self.latest_state: dict = {"type": "state", "state": merge.WAITING}

    def emitter(self, index: int):
        def emit(label: str, vitals: Vitals) -> None:
            payload = self.merger.update(index, merge.Reading(label, vitals))
            if payload is not None:
                self._send(payload)

        return emit

    def state_setter(self, index: int):
        def set_state(state: str) -> None:
            aggregate = self.merger.set_state(index, state)
            if aggregate is not None:
                log.info(f"State: {aggregate}")
                self.latest_state = {"type": "state", "state": aggregate}
                self._send(self.latest_state)

        return set_state

    def replay(self) -> list[dict]:
        """What a newly connected overlay should see first."""
        current = self.merger.payload()
        return [self.latest_state] + ([current] if current else [])

    def _send(self, payload: dict) -> None:
        if CLIENTS:
            websockets.broadcast(CLIENTS, json.dumps(payload))


def build_source(spec: SourceSpec, hub: Hub, index: int, args: argparse.Namespace):
    emit, set_state = hub.emitter(index), hub.state_setter(index)
    if spec.kind == DEMO:
        from vitalsign.demo import DemoSource

        return DemoSource(emit, set_state)
    if spec.kind == HDS:
        from vitalsign.hds import HdsSource

        return HdsSource(
            emit,
            set_state,
            port=spec.port or HDS_DEFAULT_PORT,
            allowed_networks=[*DEFAULT_ALLOWED_NETWORKS, *args.hds_allow],
            fresh_seconds=args.fresh_seconds,
        )
    from vitalsign.ble import BleSource

    return BleSource(
        emit,
        set_state,
        kind=None if spec.kind == "auto" else spec.kind,
        address=spec.address,
        scan_duration=args.scan_duration,
        read_period=args.read_period,
    )


async def run_source(spec: SourceSpec, source, set_state) -> None:
    """Run one source; a failing source is logged, not fatal to the others."""
    try:
        await source.run()
    except asyncio.CancelledError:
        raise
    except OSError as e:
        log.error(f"Source {spec} stopped: {e}")
    except Exception:
        log.exception(f"Source {spec} crashed")
    # Sources run until cancelled, so getting here means this one is gone;
    # don't leave its last state (e.g. "waiting") standing in the overlay.
    set_state(merge.DISCONNECTED)


async def list_devices(scan_duration: float, as_json: bool = False) -> None:
    from vitalsign.ble import KIND_DESCRIPTIONS, VIATOM_FAMILY, probe, scan

    say = (lambda *_: None) if as_json else print
    say(f"Scanning {scan_duration:.0f}s for supported Bluetooth devices...")
    found = await scan(scan_duration)
    if not found:
        say("None found. Make sure the device is on/worn and not connected to its phone app.")
    devices = []
    for f in found:
        kind = f.kind
        if kind == VIATOM_FAMILY:
            # The advertisement can't tell the ring generations apart;
            # connecting briefly to look at its services can.
            try:
                kind = await probe(f.address) or kind
            except Exception as e:  # noqa: BLE001 -- report and keep listing
                say(f"  ({f.label}: couldn't connect to identify it: {e})")
        # An unidentified Viatom device still works as auto:ADDRESS, which
        # identifies it when the bridge connects.
        shown = "auto" if kind == VIATOM_FAMILY else kind
        devices.append(
            {
                "name": f.name,
                "source": f"{shown}:{f.address}",
                "kind": kind,
                "description": KIND_DESCRIPTIONS[kind],
                "rssi": f.rssi,
            }
        )
        say(f"  {f.label:<24} --source {shown}:{f.address}   RSSI={f.rssi}")
        say(f"  {'':<24} {KIND_DESCRIPTIONS[kind]}")
    if as_json:
        print(json.dumps(devices))


def _positive_finite_seconds(raw: str) -> float:
    value = float(raw)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError(f"must be a positive, finite number of seconds (got {raw!r})")
    return value


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="SOURCE values:\n" + SOURCE_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--source",
        action="append",
        metavar="SOURCE",
        help="Where readings come from; repeat to add fallbacks (see SOURCE values below). Default: auto",
    )
    parser.add_argument(
        "--address",
        default=None,
        help="Shorthand for --source auto:ADDRESS. BLE address (Linux/Windows) or "
        "CoreBluetooth UUID (macOS).",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="Scan once, print every supported device in range with the --source value for it, and exit",
    )
    parser.add_argument("--json", action="store_true", help="With --list-devices: print the result as JSON")
    parser.add_argument(
        "--scan-duration",
        type=_positive_finite_seconds,
        default=10.0,
        help="Seconds per Bluetooth discovery scan",
    )
    parser.add_argument("--ws-port", type=int, default=8765)
    parser.add_argument(
        "--read-period",
        type=_positive_finite_seconds,
        default=2.0,
        help="Seconds between requests to devices that need polling",
    )
    parser.add_argument(
        "--fresh-seconds",
        type=_positive_finite_seconds,
        default=merge.DEFAULT_FRESH_SECONDS,
        help="How long a source's last reading counts as live before the next source takes over",
    )
    parser.add_argument(
        "--hds-allow",
        action="append",
        default=[],
        metavar="CIDR",
        help="Extra network allowed to send HDS readings, beyond private/LAN/Tailscale ranges",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    try:
        args.specs = resolve_specs(args.source, args.address)
    except ValueError as e:
        parser.error(str(e))
    return args


async def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.list_devices:
        await list_devices(args.scan_duration, as_json=args.json)
        return

    hub = Hub(len(args.specs), args.fresh_seconds)
    sources = [build_source(spec, hub, i, args) for i, spec in enumerate(args.specs)]
    log.info("Sources, highest priority first: " + ", ".join(str(s) for s in args.specs))

    async def handler(websocket) -> None:
        """Per-connection handler: register, replay current state, idle."""
        CLIENTS.add(websocket)
        log.info(f"Overlay connected ({len(CLIENTS)} total)")
        try:
            for payload in hub.replay():
                await websocket.send(json.dumps(payload))
            async for _ in websocket:
                pass  # overlay.html never sends anything; just keep the socket open
        except websockets.ConnectionClosed:
            pass  # e.g. OBS dropping the browser source without a close frame
        finally:
            CLIENTS.discard(websocket)
            log.info(f"Overlay disconnected ({len(CLIENTS)} total)")

    # Bound to localhost only -- never reachable from the network -- plus
    # the Origin allowlist above as defense in depth against other local
    # browser tabs. Heart rate/SpO2 aren't secret once they're on stream,
    # but there's no reason to let an unrelated tab read them quietly.
    server = await websockets.serve(handler, "localhost", args.ws_port, origins=ALLOWED_ORIGINS)
    log.info(f"Overlay WebSocket listening on ws://localhost:{args.ws_port}")

    tasks = [
        asyncio.create_task(run_source(spec, src, hub.state_setter(i)))
        for i, (spec, src) in enumerate(zip(args.specs, sources, strict=True))
    ]

    # SIGTERM is how stop_bridge.sh asks us to shut down; SIGINT covers
    # Ctrl-C during a manual foreground run. loop.add_signal_handler is
    # Unix-only (CPython docs say so explicitly) -- on Windows this whole
    # loop is a no-op via the NotImplementedError below, so stop_bridge.ps1
    # force-terminates the process instead of requesting this clean
    # shutdown, and a foreground Ctrl-C there just hits Python's default
    # KeyboardInterrupt handling. Neither skips anything load-bearing: the
    # OS reclaims the socket/BLE connection either way.
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop_event.set)

    await stop_event.wait()
    log.info("Shutting down...")
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    server.close()
    await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
