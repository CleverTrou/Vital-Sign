#!/usr/bin/env python3
"""BLE bridge: Checkme O2 Max (Viatom protocol family) -> local WebSocket -> OBS overlay.

Connects to the pulse oximeter over Bluetooth LE using viatom-ble, and
re-broadcasts every reading as JSON to any WebSocket clients (i.e. overlay.html
running as an OBS Browser Source). Run this instead of pairing the ring to
ViHealth/O2Insight_Pro -- BLE peripherals only accept one central connection
at a time.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import math
import signal
from collections.abc import Sequence

import websockets
from viatom_ble import Reading, ViatomClient, scan_devices
from viatom_ble.protocol import SERVICE_UUID
from viatom_ble.scanner import COMPATIBLE_NAME_PREFIXES

log = logging.getLogger("bridge")

# All currently-connected overlay.html clients, so a reading can be fanned
# out to every open browser source (normally just one, but nothing stops
# you from adding the overlay to multiple scenes at once).
CLIENTS: set = set()

# The most recent broadcast payload. Sent immediately to each new client on
# connect so a freshly (re)loaded overlay doesn't sit blank until the next
# ~2s poll tick.
LATEST: dict = {"type": "state", "state": "idle"}

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


def reading_to_payload(reading: Reading) -> dict:
    """Flatten a viatom_ble.Reading into the JSON shape overlay.html expects."""
    return {
        "type": "reading",
        "spo2": reading.spo2,
        "hr": reading.heart_rate,
        "battery": reading.battery,
        "movement": reading.movement,
        "pi": reading.perfusion_index,
        "worn": reading.worn,
        "calibrating": reading.calibrating,
        "ts": reading.received_at.isoformat(),
    }


async def broadcast(payload: dict) -> None:
    """Send payload to every connected client and remember it as LATEST.

    return_exceptions=True so one client's dead/closing socket doesn't stop
    delivery to the others.
    """
    global LATEST
    LATEST = payload
    if not CLIENTS:
        return
    message = json.dumps(payload)
    await asyncio.gather(
        *(client.send(message) for client in list(CLIENTS)),
        return_exceptions=True,
    )


def _looks_like_viatom(name: str | None, service_uuids: Sequence[str]) -> bool:
    """Stricter stand-in for viatom_ble.scanner's own name filter.

    That filter checks `prefix in name.lower()` -- a substring test --
    despite the constant being named COMPATIBLE_NAME_*PREFIXES*. In
    practice that means "po" also matches inside "AirPods Pro", which
    showed up as a false positive during testing the very first time an
    AirPods case was open nearby. We reuse the library's own prefix list
    (so it stays in sync if upstream adds a model) but apply a real
    `.startswith()` check, plus the service-UUID fallback the library
    documents for rings that drop their name from later advertisements.
    """
    if name and name.lower().startswith(COMPATIBLE_NAME_PREFIXES):
        return True
    return any(u.lower() == SERVICE_UUID.lower() for u in service_uuids)


async def resolve_address(explicit_address: str | None, scan_duration: float) -> str:
    """Return the BLE address to connect to, scanning for it if not given.

    Most people own exactly one Viatom/Wellue/Checkme device, so requiring
    a manually copy-pasted address for every setup is friction with no
    payoff -- we can just ask Bleak who's nearby. Pinning an address (via
    --address or config.sh/config.ps1's ADDRESS) is still supported for
    anyone with more than one such device in range, or who wants to skip
    the scan for a faster start.
    """
    if explicit_address:
        return explicit_address

    log.info(f"No --address given; scanning for a Viatom-family device ({scan_duration:.0f}s)...")
    # name_filter=False: do our own filtering below instead of the library's,
    # per the _looks_like_viatom docstring.
    all_found = await scan_devices(duration=scan_duration, name_filter=False)
    found = [
        (device, adv)
        for device, adv in all_found
        if _looks_like_viatom(adv.local_name or device.name, adv.service_uuids or ())
    ]

    if not found:
        raise RuntimeError(
            "No Viatom/Wellue/Checkme device found. Make sure the ring is worn/awake "
            "and not already connected to ViHealth/O2Insight_Pro (BLE only allows one "
            "connection at a time), then try again -- or pass --address explicitly."
        )

    if len(found) > 1:
        listing = "\n".join(
            f"  - {adv.local_name or device.name or '<unnamed>':<24} {device.address}  RSSI={adv.rssi}"
            for device, adv in found
        )
        raise RuntimeError(
            f"Multiple matching devices found; pin one explicitly with --address "
            f"(or ADDRESS in config.sh/config.ps1):\n{listing}"
        )

    device, adv = found[0]
    log.info(f"Found {adv.local_name or device.name or '<unnamed>'} at {device.address}")
    return device.address


async def handler(websocket) -> None:
    """Per-connection handler: register, replay the last known state, idle."""
    CLIENTS.add(websocket)
    log.info(f"Overlay connected ({len(CLIENTS)} total)")
    try:
        await websocket.send(json.dumps(LATEST))
        async for _ in websocket:
            pass  # overlay.html never sends anything; just keep the socket open
    finally:
        CLIENTS.discard(websocket)
        log.info(f"Overlay disconnected ({len(CLIENTS)} total)")


def _positive_finite_seconds(raw: str) -> float:
    value = float(raw)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError(f"must be a positive, finite number of seconds (got {raw!r})")
    return value


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--address",
        default=None,
        help="BLE address (Linux/Windows) or CoreBluetooth UUID (macOS). Omit to "
        "auto-discover -- fine as long as exactly one compatible device is in range.",
    )
    parser.add_argument(
        "--scan-duration",
        type=_positive_finite_seconds,
        default=10.0,
        help="Seconds to scan for a device when --address is omitted",
    )
    parser.add_argument("--ws-port", type=int, default=8765)
    parser.add_argument("--read-period", type=float, default=2.0, help="Seconds between poll requests")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    address = await resolve_address(args.address, args.scan_duration)

    async def on_reading(reading: Reading) -> None:
        await broadcast(reading_to_payload(reading))

    async def on_state_change(state: str) -> None:
        log.info(f"BLE state: {state}")
        await broadcast({"type": "state", "state": state})

    client = ViatomClient(
        address=address,
        read_period=args.read_period,
        on_reading=on_reading,
        on_state_change=on_state_change,
    )

    # Bound to localhost only -- never reachable from the network -- plus
    # the Origin allowlist above as defense in depth against other local
    # browser tabs. Heart rate/SpO2 aren't secret once they're on stream,
    # but there's no reason to let an unrelated tab read them quietly.
    server = await websockets.serve(
        handler,
        "localhost",
        args.ws_port,
        origins=ALLOWED_ORIGINS,
    )
    log.info(f"Overlay WebSocket listening on ws://localhost:{args.ws_port}")

    client_task = asyncio.create_task(client.run_forever())

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
    await client.stop()
    client_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await client_task
    server.close()
    await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
