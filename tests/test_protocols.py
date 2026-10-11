import unittest

from vitalsign.protocols import (
    OXYII_OP_LIVE,
    OxyIIDecoder,
    PC60Decoder,
    crc8_07,
    crc8_maxim,
    oxyii_encode,
    parse_hds_body,
    parse_heart_rate_measurement,
    parse_oxyii_live,
    parse_plx_measurement,
    sfloat,
    valid_hr,
    valid_spo2,
)

# Frames captured from a real PC-60FW, published in
# github.com/pedasmith/BluetoothDeviceController
# (Viatom_PulseOximeter_PC60FW_Factory.cs). Every one passes CRC-8/MAXIM.
PC60_LIVE_97_64 = bytes.fromhex("AA550F080161400051 00C05B".replace(" ", ""))
PC60_BATTERY_FULL = bytes.fromhex("AA55F0030303F6")
PC60_WAVEFORM = bytes.fromhex("AA550F07026E5FCC3722ED")
PC60_WAVEFORM_ZERO = bytes.fromhex("AA550F07020000000000 28".replace(" ", ""))


class Pc60Test(unittest.TestCase):
    def test_captured_frames_pass_crc(self):
        for frame in (PC60_LIVE_97_64, PC60_BATTERY_FULL, PC60_WAVEFORM, PC60_WAVEFORM_ZERO):
            self.assertEqual(crc8_maxim(frame[:-1]), frame[-1], frame.hex())

    def test_live_frame(self):
        [v] = PC60Decoder().feed(PC60_LIVE_97_64)
        self.assertEqual((v.spo2, v.hr, v.pi), (97, 64, 8.1))
        self.assertTrue(v.worn)
        self.assertFalse(v.calibrating)

    def test_waveform_frames_are_skipped(self):
        self.assertEqual(PC60Decoder().feed(PC60_WAVEFORM + PC60_WAVEFORM_ZERO), [])

    def test_concatenated_and_split_across_notifications(self):
        stream = PC60_WAVEFORM + PC60_LIVE_97_64 + PC60_WAVEFORM + PC60_LIVE_97_64
        decoder = PC60Decoder()
        out = []
        for i in range(0, len(stream), 5):  # 5-byte chunks split every frame
            out += decoder.feed(stream[i : i + 5])
        self.assertEqual([v.hr for v in out], [64, 64])

    def test_split_inside_sync_word(self):
        decoder = PC60Decoder()
        self.assertEqual(decoder.feed(PC60_LIVE_97_64[:1]), [])
        [v] = decoder.feed(PC60_LIVE_97_64[1:])
        self.assertEqual(v.spo2, 97)

    def test_corrupt_frame_dropped_then_resyncs(self):
        corrupt = bytearray(PC60_LIVE_97_64)
        corrupt[5] = 0x50  # SpO2 byte changed, CRC no longer matches
        [v] = PC60Decoder().feed(bytes(b"\x00\x13" + corrupt + PC60_LIVE_97_64))
        self.assertEqual(v.spo2, 97)

    def test_battery_is_attached_to_next_reading(self):
        decoder = PC60Decoder()
        self.assertEqual(decoder.feed(PC60_BATTERY_FULL), [])
        [v] = decoder.feed(PC60_LIVE_97_64)
        self.assertEqual(v.battery, 100)

    def test_zeros_mean_settling(self):
        body = bytes.fromhex("AA550F08010000000000C0")
        [v] = PC60Decoder().feed(body + bytes([crc8_maxim(body)]))
        self.assertTrue(v.calibrating)
        self.assertIsNone(v.hr)


class OxyIITest(unittest.TestCase):
    def test_crc_fixture(self):
        # Fixture from SomnoTrace's spec/0003-o2ring-ble-sync.md.
        self.assertEqual(crc8_07(bytes.fromhex("A5E11E00020000")), 0xBF)

    def test_encode_live_request(self):
        frame = oxyii_encode(OXYII_OP_LIVE, seq=7)
        self.assertEqual(frame[:7], bytes([0xA5, 0x04, 0xFB, 0x00, 0x07, 0x00, 0x00]))
        self.assertEqual(frame[-1], crc8_07(frame[:-1]))

    def _live_reply(self, state, spo2, motion, hr, battery=80):
        payload = bytes([0, 0, 0, 0, 0, state, spo2, motion, hr, 0, 0, 0, 0, battery])
        return oxyii_encode(OXYII_OP_LIVE, seq=3, payload=payload)

    def test_decode_split_reply(self):
        reply = self._live_reply(0x01, 96, 2, 58)
        decoder = OxyIIDecoder()
        frames = decoder.feed(b"\x00" + reply[:10]) + decoder.feed(reply[10:])
        self.assertEqual(len(frames), 1)
        v = parse_oxyii_live(frames[0].payload)
        self.assertEqual((v.spo2, v.hr, v.movement, v.battery, v.worn), (96, 58, 2, 80, True))

    def test_corrupt_crc_rejected(self):
        reply = bytearray(self._live_reply(0x01, 96, 2, 58))
        reply[-1] ^= 0xFF
        self.assertEqual(OxyIIDecoder().feed(bytes(reply)), [])

    def test_no_finger(self):
        frame = OxyIIDecoder().feed(self._live_reply(0x00, 255, 0, 255))[0]
        v = parse_oxyii_live(frame.payload)
        self.assertFalse(v.worn)
        self.assertIsNone(v.hr)

    def test_worn_but_no_values_yet(self):
        frame = OxyIIDecoder().feed(self._live_reply(0x01, 255, 0, 255))[0]
        v = parse_oxyii_live(frame.payload)
        self.assertTrue(v.worn and v.calibrating)


class HeartRateServiceTest(unittest.TestCase):
    def test_uint8_without_contact_support(self):
        v = parse_heart_rate_measurement(bytes([0x00, 72]))
        self.assertEqual(v.hr, 72)
        self.assertTrue(v.worn)

    def test_uint16(self):
        self.assertEqual(parse_heart_rate_measurement(bytes([0x01, 0x2C, 0x01])).hr, 300)

    def test_contact_lost(self):
        v = parse_heart_rate_measurement(bytes([0x04, 0]))
        self.assertFalse(v.worn)

    def test_contact_present_with_rr_intervals(self):
        v = parse_heart_rate_measurement(bytes([0x16, 61, 0x10, 0x04, 0x20, 0x04]))
        self.assertEqual(v.hr, 61)
        self.assertTrue(v.worn)

    def test_truncated(self):
        self.assertIsNone(parse_heart_rate_measurement(b"\x01\x50"))


class PlxTest(unittest.TestCase):
    def test_sfloat(self):
        self.assertEqual(sfloat(0x0062), 98)
        self.assertAlmostEqual(sfloat(0xF3D4), 98.0)  # 980 x 10^-1
        self.assertEqual(sfloat(0x0FFF), -1.0)  # negative mantissa: -1 x 10^0
        for special in (0x07FF, 0x0800, 0x07FE, 0x0802, 0x0801):
            self.assertIsNone(sfloat(special))

    def test_measurement(self):
        v = parse_plx_measurement(bytes([0x00, 0x61, 0x00, 0x48, 0x00]))
        self.assertEqual((v.spo2, v.hr), (97, 72))

    def test_nan_values(self):
        v = parse_plx_measurement(bytes([0x00, 0xFF, 0x07, 0xFF, 0x07]))
        self.assertTrue(v.calibrating)


class HdsTest(unittest.TestCase):
    def test_heart_rate(self):
        self.assertEqual(parse_hds_body(b'{"data":"heartRate:84"}').hr, 84)
        self.assertEqual(parse_hds_body(b'{"data":"heartRate:84.0"}').hr, 84)

    def test_ignored_and_invalid(self):
        for body in (
            b'{"data":"oxygenSaturation:0.97"}',
            b'{"data":"calories:12"}',
            b'{"data":"heartRate:abc"}',
            b'{"data":"heartRate:0"}',
            b'{"data":5}',
            b"[]",
            b"not json",
        ):
            self.assertIsNone(parse_hds_body(body), body)


class ValidatorsTest(unittest.TestCase):
    def test_ranges(self):
        self.assertIsNone(valid_hr(0))
        self.assertIsNone(valid_hr(float("nan")))
        self.assertEqual(valid_hr(71.6), 72)
        self.assertIsNone(valid_spo2(127))
        self.assertEqual(valid_spo2(100), 100)


if __name__ == "__main__":
    unittest.main()
