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
import signal

import websockets
from viatom_ble import Reading, ViatomClient

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
# "Local file"), which browsers send as a literal Origin: null header --
# not the same as an absent header. `None` here covers the absent-header
# case (plain WebSocket test clients, curl, etc). Restricting to these two
# means a webpage open in a *real* browser tab can't quietly open a
# WebSocket to this port and read your live vitals in the background; see
# the "same-origin/localhost" note in the Origin header docs at
# https://websockets.readthedocs.io/en/stable/topics/broadcast.html
ALLOWED_ORIGINS = ["null", None]


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


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--address",
        required=True,
        help="BLE address (Linux/Windows) or CoreBluetooth UUID (macOS). "
        "Find it with: viatom-ble --scan-interactive",
    )
    parser.add_argument("--ws-port", type=int, default=8765)
    parser.add_argument("--read-period", type=float, default=2.0, help="Seconds between poll requests")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    async def on_reading(reading: Reading) -> None:
        await broadcast(reading_to_payload(reading))

    async def on_state_change(state: str) -> None:
        log.info(f"BLE state: {state}")
        await broadcast({"type": "state", "state": state})

    client = ViatomClient(
        address=args.address,
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
    # Ctrl-C during a manual foreground run.
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
