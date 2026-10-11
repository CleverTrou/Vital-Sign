import unittest

from vitalsign import merge
from vitalsign.merge import Merger, Reading
from vitalsign.protocols import Vitals


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class MergerTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.m = Merger(2, fresh_seconds=10, clock=self.clock)

    def reading(self, source, **vitals):
        return Reading(source, Vitals(**vitals), at=self.clock.now)

    def test_primary_wins_when_live(self):
        self.m.update(1, self.reading("watch", hr=90))
        p = self.m.update(0, self.reading("ring", hr=70, spo2=97))
        self.assertEqual((p["hr"], p["spo2"], p["hr_source"]), (70, 97, "ring"))

    def test_fallback_when_primary_not_worn(self):
        self.m.update(1, self.reading("watch", hr=90))
        p = self.m.update(0, self.reading("ring", worn=False))
        self.assertEqual((p["hr"], p["spo2"], p["worn"]), (90, None, True))
        self.assertEqual(p["hr_source"], "watch")

    def test_fallback_when_primary_goes_quiet(self):
        self.m.update(0, self.reading("ring", hr=70, spo2=97))
        self.clock.now += 11
        p = self.m.update(1, self.reading("watch", hr=88))
        self.assertEqual((p["hr"], p["spo2"]), (88, None))

    def test_metrics_can_come_from_different_sources(self):
        self.m.update(0, self.reading("strap", hr=120))
        p = self.m.update(1, self.reading("ring", hr=118, spo2=95))
        self.assertEqual((p["hr"], p["hr_source"], p["spo2"], p["spo2_source"]), (120, "strap", 95, "ring"))

    def test_nothing_live_reports_primary_status(self):
        self.m.update(1, self.reading("watch", worn=True, calibrating=True))
        p = self.m.update(0, self.reading("ring", worn=False))
        self.assertFalse(p["worn"])
        self.assertIsNone(p["hr"])

    def test_nothing_fresh(self):
        self.m.update(0, self.reading("ring", hr=70))
        self.clock.now += 11
        self.assertIsNone(self.m.payload())

    def test_state_aggregate_takes_best(self):
        self.assertEqual(self.m.set_state(0, merge.IDLE), merge.WAITING)
        self.assertEqual(self.m.set_state(1, merge.CONNECTED), merge.CONNECTED)
        self.assertIsNone(self.m.set_state(0, merge.DISCONNECTED))  # still connected via 1
        self.assertEqual(self.m.set_state(1, merge.IDLE), merge.IDLE)


if __name__ == "__main__":
    unittest.main()
