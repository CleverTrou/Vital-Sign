"""BLE protocol detection and session logic, against a fake Bleak client."""

import asyncio
import unittest

from vitalsign import ble, merge
from vitalsign.protocols import (
    HRS_MEASUREMENT_UUID,
    HRS_SERVICE_UUID,
    NUS_NOTIFY_UUID,
    NUS_SERVICE_UUID,
    OXYII_NOTIFY_UUID,
    OXYII_OP_LIVE,
    OXYII_SERVICE_UUID,
    OXYII_WRITE_UUID,
    PLX_SERVICE_UUID,
    oxyii_encode,
)
from viatom_ble.protocol import SERVICE_UUID as VIATOM_SERVICE_UUID


class ClassifyTest(unittest.TestCase):
    def test_service_uuids(self):
        self.assertEqual(ble.classify(None, [VIATOM_SERVICE_UUID], []), ble.VIATOM)
        self.assertEqual(ble.classify(None, [OXYII_SERVICE_UUID.upper()], []), ble.OXYII)
        self.assertEqual(ble.classify("Some Oximeter", [PLX_SERVICE_UUID], []), ble.PLX)
        self.assertEqual(ble.classify("HRM-Pro", [HRS_SERVICE_UUID], []), ble.HRS)

    # What a real Checkme O2 Max ("O2M 3994") advertised, and the services
    # it exposed once connected: no protocol service in the advertisement,
    # only the standard heart-rate service and Viatom's manufacturer ID.
    O2MAX_ADV = ("O2M 3994", [HRS_SERVICE_UUID], [0xF34E])
    O2MAX_GATT = [
        HRS_SERVICE_UUID,
        "0000180f-0000-1000-8000-00805f9b34fb",
        "0000180a-0000-1000-8000-00805f9b34fb",
        "0000fe59-0000-1000-8000-00805f9b34fb",
        VIATOM_SERVICE_UUID,
    ]

    def test_viatom_advert_needs_a_probe(self):
        self.assertEqual(ble.classify(*self.O2MAX_ADV), ble.VIATOM_FAMILY)
        self.assertEqual(ble.classify("SHQO2Pro 0897", [HRS_SERVICE_UUID], [0xF34E]), ble.VIATOM_FAMILY)

    def test_probe_result(self):
        self.assertEqual(ble.classify_gatt(self.O2MAX_GATT), ble.VIATOM)
        self.assertEqual(ble.classify_gatt([HRS_SERVICE_UUID, OXYII_SERVICE_UUID]), ble.OXYII)
        self.assertEqual(ble.classify_gatt([HRS_SERVICE_UUID]), ble.HRS)
        self.assertIsNone(ble.classify_gatt(["0000180a-0000-1000-8000-00805f9b34fb"]))

    def test_kind_matches(self):
        self.assertTrue(ble.kind_matches(None, ble.HRS))
        self.assertTrue(ble.kind_matches(ble.VIATOM, ble.VIATOM_FAMILY))
        self.assertTrue(ble.kind_matches(ble.HRS, ble.VIATOM_FAMILY))
        self.assertTrue(ble.kind_matches(ble.HRS, ble.OXYII))
        self.assertFalse(ble.kind_matches(ble.PC60, ble.HRS))
        self.assertFalse(ble.kind_matches(ble.VIATOM, ble.HRS))

    def test_lookalikes_are_not_viatom(self):
        # viatom-ble's "po" prefix would otherwise claim these.
        self.assertEqual(ble.classify("Polar H10 ABCD1234", [HRS_SERVICE_UUID], []), ble.HRS)
        self.assertIsNone(ble.classify("Polar H10 ABCD1234", [], []))
        self.assertIsNone(ble.classify("AirPods Pro", [], [0x4C]))
        # Nordic UART alone is far too common to mean "PC-60".
        self.assertIsNone(ble.classify("Some Gadget", [NUS_SERVICE_UUID], []))


class FakeChar:
    def __init__(self, uuid):
        self.uuid = uuid


class FakeService:
    def __init__(self, uuid, chars=()):
        self.uuid = uuid
        self.characteristics = [FakeChar(c) for c in chars]


class FakeServices:
    def __init__(self, services):
        self._services = services

    def get_service(self, uuid):
        return next((s for s in self._services if s.uuid == uuid.lower()), None)

    def get_characteristic(self, uuid):
        for s in self._services:
            for c in s.characteristics:
                if c.uuid == uuid:
                    return c
        return None

    def __iter__(self):
        return iter(self._services)


class FakeClient:
    """Stands in for BleakClient; tests drive notifications by hand."""

    def __init__(self, services, on_write=None):
        self.services = FakeServices(services)
        self.callbacks = {}
        self.writes = []
        self.on_write = on_write
        self.connected = False
        self._disconnected_callback = None

    def factory(self, _device, disconnected_callback=None, timeout=None):
        self._disconnected_callback = disconnected_callback
        return self

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    def drop(self):
        self._disconnected_callback(self)

    async def start_notify(self, uuid, callback):
        self.callbacks[uuid] = callback

    async def write_gatt_char(self, uuid, data, response=None):
        self.writes.append((uuid, bytes(data), response))
        if self.on_write:
            self.on_write(self, uuid, bytes(data))

    async def read_gatt_char(self, uuid):
        return bytearray([77])

    def notify(self, uuid, data):
        self.callbacks[uuid](None, bytearray(data))


async def stop(task):
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def _found(_address, _timeout):
    return object()


async def _not_found(_address, _timeout):
    return None


class SessionHarness:
    def __init__(self, session_cls, client, **kwargs):
        self.emitted = []
        self.states = []
        self.session = session_cls(
            "ADDR",
            self.emitted.append,
            self.states.append,
            read_period=0.01,
            client_factory=client.factory,
            find_device=kwargs.pop("find_device", _found),
            **kwargs,
        )

    async def until(self, predicate, timeout=2.0):
        async with asyncio.timeout(timeout):
            while not predicate():
                await asyncio.sleep(0.005)


class SessionTest(unittest.IsolatedAsyncioTestCase):
    async def test_hrs_streams_heart_rate_with_battery(self):
        client = FakeClient([FakeService(HRS_SERVICE_UUID, [HRS_MEASUREMENT_UUID]), FakeService(
            "0000180f-0000-1000-8000-00805f9b34fb", ["00002a19-0000-1000-8000-00805f9b34fb"])])
        h = SessionHarness(ble.HrsSession, client)
        task = asyncio.create_task(h.session.run())
        await h.until(lambda: HRS_MEASUREMENT_UUID in client.callbacks)
        client.notify(HRS_MEASUREMENT_UUID, [0x00, 66])
        await h.until(lambda: h.emitted)
        self.assertEqual((h.emitted[0].hr, h.emitted[0].battery), (66, 77))
        self.assertIn(merge.CONNECTED, h.states)
        await stop(task)

    async def test_pc60_push(self):
        client = FakeClient([FakeService(NUS_SERVICE_UUID, [NUS_NOTIFY_UUID])])
        h = SessionHarness(ble.PC60Session, client)
        task = asyncio.create_task(h.session.run())
        await h.until(lambda: NUS_NOTIFY_UUID in client.callbacks)
        client.notify(NUS_NOTIFY_UUID, bytes.fromhex("AA550F08016140005100C05B"))
        await h.until(lambda: h.emitted)
        self.assertEqual((h.emitted[0].spo2, h.emitted[0].hr), (97, 64))
        await stop(task)

    async def test_stall_forces_reconnect(self):
        client = FakeClient([FakeService(HRS_SERVICE_UUID, [HRS_MEASUREMENT_UUID])])
        h = SessionHarness(ble.HrsSession, client)
        h.session.stall_seconds = 0.05
        with self.assertLogs("bridge.ble", "WARNING"):
            task = asyncio.create_task(h.session.run())
            await h.until(lambda: merge.IDLE in h.states)
        self.assertIn(merge.DISCONNECTED, h.states)
        self.assertFalse(client.connected)
        await stop(task)

    async def test_peripheral_disconnect_is_noticed(self):
        client = FakeClient([FakeService(HRS_SERVICE_UUID, [HRS_MEASUREMENT_UUID])])
        h = SessionHarness(ble.HrsSession, client)
        task = asyncio.create_task(h.session.run())
        await h.until(lambda: HRS_MEASUREMENT_UUID in client.callbacks)
        with self.assertLogs("bridge.ble", "WARNING"):
            client.drop()
            await h.until(lambda: merge.IDLE in h.states)
        self.assertIn(merge.DISCONNECTED, h.states)
        await stop(task)

    async def test_wrong_protocol_hints_at_the_right_one(self):
        client = FakeClient([FakeService(HRS_SERVICE_UUID, [HRS_MEASUREMENT_UUID])])
        h = SessionHarness(ble.PC60Session, client)
        with self.assertLogs("bridge.ble", "ERROR") as logs:
            task = asyncio.create_task(h.session.run())
            await h.until(lambda: merge.IDLE in h.states)
        self.assertIn("--source hrs:ADDR", logs.output[0])
        await stop(task)

    async def test_device_not_found_backs_off(self):
        client = FakeClient([])
        h = SessionHarness(ble.HrsSession, client, find_device=_not_found)
        with self.assertLogs("bridge.ble", "WARNING"):
            task = asyncio.create_task(h.session.run())
            await h.until(lambda: merge.IDLE in h.states)
        self.assertNotIn(merge.CONNECTED, h.states)
        await stop(task)


def _oxyii_ring(reply_state=0x01):
    """A fake OxyII ring that answers every live request."""

    def on_write(client, uuid, data):
        if uuid == OXYII_WRITE_UUID and data[1] == OXYII_OP_LIVE:
            payload = bytes([0, 0, 0, 0, 0, reply_state, 98, 1, 61, 0, 0, 0, 0, 90])
            reply = oxyii_encode(OXYII_OP_LIVE, data[4], payload)
            # Real rings split long replies across notifications.
            asyncio.get_running_loop().call_soon(client.notify, OXYII_NOTIFY_UUID, reply[:9])
            asyncio.get_running_loop().call_soon(client.notify, OXYII_NOTIFY_UUID, reply[9:])

    return on_write


class OxyIISessionTest(unittest.IsolatedAsyncioTestCase):
    def ring(self, on_write, with_hrs=True):
        services = [FakeService(OXYII_SERVICE_UUID, [OXYII_WRITE_UUID, OXYII_NOTIFY_UUID])]
        if with_hrs:
            services.append(FakeService(HRS_SERVICE_UUID, [HRS_MEASUREMENT_UUID]))
        return FakeClient(services, on_write=on_write)

    async def test_polls_live_values(self):
        client = self.ring(_oxyii_ring())
        h = SessionHarness(ble.OxyIISession, client)
        task = asyncio.create_task(h.session.run())
        await h.until(lambda: len(h.emitted) >= 2)
        self.assertEqual((h.emitted[0].spo2, h.emitted[0].hr, h.emitted[0].battery), (98, 61, 90))
        _uuid, frame, response = client.writes[0]
        self.assertEqual(frame[:2], bytes([0xA5, OXYII_OP_LIVE]))
        self.assertFalse(response)
        await stop(task)

    async def test_falls_back_to_heart_rate_service_when_unanswered(self):
        client = self.ring(on_write=None)
        h = SessionHarness(ble.OxyIISession, client)
        with self.assertLogs("bridge.ble", "WARNING"):
            task = asyncio.create_task(h.session.run())
            await h.until(lambda: HRS_MEASUREMENT_UUID in client.callbacks)
        client.notify(HRS_MEASUREMENT_UUID, [0x00, 59])
        await h.until(lambda: h.emitted)
        self.assertEqual((h.emitted[0].hr, h.emitted[0].spo2), (59, None))
        await stop(task)

    async def test_rests_after_inactivity(self):
        client = self.ring(_oxyii_ring(reply_state=0x00))
        h = SessionHarness(ble.OxyIISession, client)
        h.session.inactivity_timeout = 0.05
        h.session.inactivity_delay = 60
        task = asyncio.create_task(h.session.run())
        await h.until(lambda: merge.IDLE in h.states)
        self.assertFalse(client.connected)
        self.assertFalse(h.emitted[0].worn)
        await stop(task)


if __name__ == "__main__":
    unittest.main()
