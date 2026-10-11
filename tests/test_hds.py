import asyncio
import json
import unittest

from vitalsign import merge
from vitalsign.hds import HdsSource


class HdsServerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.emitted = []
        self.states = []
        await self.start()

    async def start(self, **kwargs):
        self.source = HdsSource(
            lambda label, v: self.emitted.append((label, v)),
            self.states.append,
            host="127.0.0.1",
            port=0,
            fresh_seconds=0.2,
            **kwargs,
        )
        self.task = asyncio.create_task(self.source.run())
        async with asyncio.timeout(2):
            while self.source.bound_port is None:
                await asyncio.sleep(0.01)

    async def asyncTearDown(self):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)

    async def request(self, method="PUT", body=b'{"data":"heartRate:81"}', extra=b""):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.source.bound_port)
        writer.write(
            f"{method} / HTTP/1.1\r\nHost: mac\r\nContent-Type: application/json\r\n".encode()
            + extra
            + f"Content-Length: {len(body)}\r\n\r\n".encode()
            + body
        )
        await writer.drain()
        response = await reader.read()
        writer.close()
        return response.split(b"\r\n", 1)[0]

    async def test_put_heart_rate(self):
        self.assertEqual(await self.request(), b"HTTP/1.1 200 OK")
        [(label, vitals)] = self.emitted
        self.assertEqual((label, vitals.hr), ("HDS watch", 81))
        self.assertEqual(self.states[-1], merge.CONNECTED)

    async def test_goes_back_to_waiting_when_quiet(self):
        await self.request()
        await asyncio.sleep(0.3)
        self.assertEqual(self.states[-1], merge.WAITING)

    async def test_other_metrics_acknowledged_but_ignored(self):
        body = json.dumps({"data": "calories:30"}).encode()
        self.assertEqual(await self.request(body=body), b"HTTP/1.1 200 OK")
        self.assertEqual(self.emitted, [])

    async def test_expect_continue(self):
        status = await self.request(extra=b"Expect: 100-continue\r\n")
        self.assertEqual(status, b"HTTP/1.1 100 Continue")
        self.assertEqual(self.emitted[0][1].hr, 81)

    async def test_rejects_requests_a_web_page_could_send(self):
        # A plain-text POST needs no CORS preflight, so it must not count.
        self.assertEqual(await self.request(method="POST"), b"HTTP/1.1 405 Method Not Allowed")
        self.assertEqual(await self.request(extra=b"Origin: https://evil.example\r\n"), b"HTTP/1.1 403 Forbidden")
        self.assertEqual(await self.request(extra=b"Origin: null\r\n"), b"HTTP/1.1 403 Forbidden")
        self.assertEqual(self.emitted, [])

    async def test_rejects_get_and_oversize(self):
        self.assertEqual(await self.request(method="GET", body=b""), b"HTTP/1.1 405 Method Not Allowed")
        self.assertEqual(await self.request(body=b"x" * 5000), b"HTTP/1.1 413 Payload Too Large")
        self.assertEqual(self.emitted, [])

    async def test_rejects_networks_not_allowed(self):
        await self.asyncTearDown()
        await self.start(allowed_networks=["10.0.0.0/8"])
        with self.assertLogs("bridge.hds", "WARNING"):
            self.assertEqual(await self.request(), b"HTTP/1.1 403 Forbidden")
        self.assertEqual(self.emitted, [])


if __name__ == "__main__":
    unittest.main()
