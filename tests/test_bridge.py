"""End to end: run bridge.py with the demo source and read its WebSocket."""

import asyncio
import json
import socket
import sys
import unittest
from pathlib import Path

import websockets

BRIDGE = Path(__file__).resolve().parent.parent / "bridge.py"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]


class BridgeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.port = free_port()
        self.proc = await asyncio.create_subprocess_exec(
            sys.executable, str(BRIDGE), "--source", "demo", "--ws-port", str(self.port),
            stderr=asyncio.subprocess.DEVNULL,
        )

    async def asyncTearDown(self):
        self.proc.terminate()
        await self.proc.wait()

    async def connect(self, **kwargs):
        async with asyncio.timeout(10):
            while True:
                try:
                    return await websockets.connect(f"ws://localhost:{self.port}", **kwargs)
                except OSError:
                    await asyncio.sleep(0.1)

    async def test_demo_readings_reach_the_overlay_socket(self):
        ws = await self.connect()
        async with asyncio.timeout(5):
            messages = [json.loads(await ws.recv()) for _ in range(3)]
        await ws.close()
        readings = [m for m in messages if m["type"] == "reading"]
        self.assertTrue(readings)
        r = readings[-1]
        self.assertEqual(r["source"], "Demo")
        self.assertTrue(40 <= r["hr"] <= 140 and 85 <= r["spo2"] <= 100)
        for key in ("worn", "calibrating", "battery", "movement", "pi", "ts", "hr_source", "spo2_source"):
            self.assertIn(key, r)

    async def test_foreign_web_origin_is_refused(self):
        with self.assertRaises(websockets.InvalidStatus):
            await self.connect(origin="https://example.com")

    async def test_bad_source_is_a_usage_error(self):
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(BRIDGE), "--source", "pulsoid", stderr=asyncio.subprocess.PIPE
        )
        _, err = await proc.communicate()
        self.assertEqual(proc.returncode, 2)
        self.assertIn(b"unknown source 'pulsoid'", err)


if __name__ == "__main__":
    unittest.main()
