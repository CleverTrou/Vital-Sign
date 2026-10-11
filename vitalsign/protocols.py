"""Wire formats for every supported device family.

Pure byte-level decoding with no Bluetooth or network I/O, so it can be
unit-tested on any machine against captured frames (see tests/).

Families:

- Viatom/Wellue PC-60FW fingertip oximeters (and OEM twins such as the
  OxySmart): a push protocol over the Nordic UART service.
- Viatom "OxyII", the second-generation ring protocol (O2Ring S and its
  OEM variants): request/response frames on a Lepu-specific service.
- Bluetooth SIG Heart Rate Service: chest straps, many watches, and every
  "turn your Apple Watch into a Bluetooth HR monitor" relay app.
- Bluetooth SIG Pulse Oximeter Service (PLX).
- The Health Data Server (HDS) watch app's HTTP payload.

The original Checkme O2 / O2Ring / O2 Max ("legacy" Viatom) protocol is
not here: it is decoded by the viatom-ble library, which bridge.py has
used since the first version.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Vitals:
    """One decoded measurement, before it is tagged with its source.

    None means "this device doesn't report that value" or "the value in
    this packet was the device's own invalid marker" -- either way, there
    is nothing to show for it.
    """

    hr: int | None = None
    spo2: int | None = None
    worn: bool = True
    calibrating: bool = False
    battery: int | None = None
    pi: float | None = None
    movement: int | None = None


def valid_hr(value: float | None) -> int | None:
    """Round to a whole BPM, or None for a device's "no reading" marker."""
    if value is None or not math.isfinite(value):
        return None
    bpm = round(value)
    return bpm if 1 <= bpm <= 300 else None


def valid_spo2(value: float | None) -> int | None:
    """Round to a whole percent, or None for a device's "no reading" marker."""
    if value is None or not math.isfinite(value):
        return None
    pct = round(value)
    return pct if 1 <= pct <= 100 else None


def _uuid16(short: int) -> str:
    return f"0000{short:04x}-0000-1000-8000-00805f9b34fb"


# ---------------------------------------------------------------------------
# PC-60FW family (Nordic UART service, device pushes ~1 Hz)
# ---------------------------------------------------------------------------

NUS_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_NOTIFY_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

_PC60_SYNC = b"\xaa\x55"
_PC60_MAX_LEN = 64  # real frames are <= 0x08; anything huge is a false sync


def crc8_maxim(data: bytes) -> int:
    """CRC-8/MAXIM (Dallas 1-Wire): reflected poly 0x31, init 0."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8C if crc & 1 else crc >> 1
    return crc


class PC60Decoder:
    """Reassembles PC-60FW frames from BLE notifications.

    Frame: ``AA 55 | type | len | payload (len - 1 bytes) | crc8_maxim``,
    where ``len`` counts every byte after itself, CRC included. One
    notification can hold several frames, or part of one, so bytes are
    buffered until a whole frame is present.

    Payloads used here:

    - type 0x0F, function 0x01 -- live values:
      ``01 | SpO2 | PR | ? | PI x10 | ? | ?``
    - type 0xF0, function 0x03 -- battery level 0..3

    Waveform frames (function 0x02, ~50/s) and the rest are skipped. The
    battery level is remembered and attached to the next live reading
    rather than emitted on its own.
    """

    def __init__(self) -> None:
        self._buf = bytearray()
        self.battery: int | None = None

    def feed(self, data: bytes) -> list[Vitals]:
        self._buf += data
        out: list[Vitals] = []
        while True:
            start = self._buf.find(_PC60_SYNC)
            if start < 0:
                # Keep a trailing 0xAA: it may be the first half of a sync
                # word whose 0x55 arrives in the next notification.
                del self._buf[: max(0, len(self._buf) - 1)]
                return out
            del self._buf[:start]
            if len(self._buf) < 4:
                return out
            length = self._buf[3]
            if length < 2 or length > _PC60_MAX_LEN:
                del self._buf[:2]  # not a real frame; resync past this sync word
                continue
            total = 4 + length
            if len(self._buf) < total:
                return out
            frame = bytes(self._buf[:total])
            if crc8_maxim(frame[:-1]) != frame[-1]:
                del self._buf[:2]
                continue
            del self._buf[:total]
            vitals = self._decode(frame[2], frame[4:-1])
            if vitals is not None:
                out.append(vitals)

    def _decode(self, frame_type: int, payload: bytes) -> Vitals | None:
        func = payload[0]
        if frame_type == 0x0F and func == 0x01 and len(payload) >= 5:
            spo2 = valid_spo2(payload[1])
            hr = valid_hr(payload[2])
            # PC-60FW sends zeros while it is still settling on a finger.
            # It powers itself off a few seconds after the finger is
            # removed, so "not worn" shows up as a dropped connection
            # rather than as a packet.
            return Vitals(
                hr=hr,
                spo2=spo2,
                worn=True,
                calibrating=hr is None and spo2 is None,
                battery=self.battery,
                pi=payload[4] / 10 if payload[4] else None,
            )
        if frame_type == 0xF0 and func == 0x03 and len(payload) >= 2:
            self.battery = round(min(payload[1], 3) / 3 * 100)
        return None


# ---------------------------------------------------------------------------
# Viatom OxyII (O2Ring S and other second-generation rings)
# ---------------------------------------------------------------------------

OXYII_SERVICE_UUID = "e8fb0001-a14b-98f9-831b-4e2941d01248"
OXYII_WRITE_UUID = "e8fb0002-a14b-98f9-831b-4e2941d01248"
OXYII_NOTIFY_UUID = "e8fb0003-a14b-98f9-831b-4e2941d01248"
# Manufacturer ID in the advertisements of both ring generations (seen
# with payload 01 on a Checkme O2 Max and 00 on an OxyII ring), so it
# identifies "a Viatom device", not which protocol it speaks.
VIATOM_MANUFACTURER_ID = 0xF34E

OXYII_OP_LIVE = 0x04

_OXYII_LEAD = 0xA5
_OXYII_HEADER_LEN = 7
_OXYII_MAX_PAYLOAD = 1024


def crc8_07(data: bytes) -> int:
    """CRC-8 with poly 0x07, init 0, no reflection, no final xor."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def oxyii_encode(op: int, seq: int, payload: bytes = b"", flag: int = 0) -> bytes:
    """Build ``A5 | op | ~op | flag | seq | len_le16 | payload | crc8``."""
    header = bytes(
        [
            _OXYII_LEAD,
            op & 0xFF,
            ~op & 0xFF,
            flag & 0xFF,
            seq & 0xFF,
            len(payload) & 0xFF,
            (len(payload) >> 8) & 0xFF,
        ]
    )
    body = header + payload
    return body + bytes([crc8_07(body)])


@dataclass(frozen=True)
class OxyIIFrame:
    op: int
    flag: int
    seq: int
    payload: bytes


class OxyIIDecoder:
    """Reassembles OxyII frames from BLE notifications (see oxyii_encode)."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[OxyIIFrame]:
        self._buf += data
        out: list[OxyIIFrame] = []
        while True:
            start = self._buf.find(bytes([_OXYII_LEAD]))
            if start < 0:
                self._buf.clear()
                return out
            del self._buf[:start]
            if len(self._buf) < _OXYII_HEADER_LEN:
                return out
            op = self._buf[1]
            plen = self._buf[5] | (self._buf[6] << 8)
            if self._buf[2] != (~op & 0xFF) or plen > _OXYII_MAX_PAYLOAD:
                del self._buf[:1]
                continue
            total = _OXYII_HEADER_LEN + plen + 1
            if len(self._buf) < total:
                return out
            frame = bytes(self._buf[:total])
            if crc8_07(frame[:-1]) != frame[-1]:
                del self._buf[:1]
                continue
            del self._buf[:total]
            out.append(OxyIIFrame(op=op, flag=frame[3], seq=frame[4], payload=frame[_OXYII_HEADER_LEN:-1]))


def parse_oxyii_live(payload: bytes) -> Vitals | None:
    """Decode the reply to OXYII_OP_LIVE.

    Payload offsets: [5] contact state (0x00 no finger, 0x01 finger),
    [6] SpO2, [7] motion, [8] HR, [13] battery %. SpO2/HR of 255 mean
    "no value yet".
    """
    if len(payload) < 9:
        return None
    state = payload[5]
    spo2 = valid_spo2(payload[6]) if payload[6] != 0xFF else None
    hr = valid_hr(payload[8]) if payload[8] != 0xFF else None
    # 0x03 ("file handle open") has been seen too; treat any state other
    # than an explicit 0x00 as worn when the ring is reporting real values.
    worn = state == 0x01 or (state != 0x00 and (hr is not None or spo2 is not None))
    battery = payload[13] if len(payload) > 13 and payload[13] <= 100 else None
    return Vitals(
        hr=hr if worn else None,
        spo2=spo2 if worn else None,
        worn=worn,
        calibrating=worn and hr is None and spo2 is None,
        battery=battery,
        movement=payload[7],
    )


# ---------------------------------------------------------------------------
# Bluetooth SIG Heart Rate Service (0x180D)
# ---------------------------------------------------------------------------

HRS_SERVICE_UUID = _uuid16(0x180D)
HRS_MEASUREMENT_UUID = _uuid16(0x2A37)
BATTERY_SERVICE_UUID = _uuid16(0x180F)
BATTERY_LEVEL_UUID = _uuid16(0x2A19)


def parse_heart_rate_measurement(data: bytes) -> Vitals | None:
    """Decode a Heart Rate Measurement (0x2A37) notification.

    Flags bit 0: 16-bit value instead of 8-bit. Bits 1-2: sensor contact
    (bit 2 = the sensor reports contact at all, bit 1 = contact now).
    Energy-expended and RR-interval fields that may follow are ignored.
    """
    if len(data) < 2:
        return None
    flags = data[0]
    if flags & 0x01:
        if len(data) < 3:
            return None
        raw = data[1] | (data[2] << 8)
    else:
        raw = data[1]
    contact_supported = bool(flags & 0x04)
    contact_detected = bool(flags & 0x02)
    worn = contact_detected or not contact_supported
    hr = valid_hr(raw) if worn else None
    return Vitals(hr=hr, worn=worn, calibrating=worn and hr is None)


# ---------------------------------------------------------------------------
# Bluetooth SIG Pulse Oximeter Service (0x1822)
# ---------------------------------------------------------------------------

PLX_SERVICE_UUID = _uuid16(0x1822)
PLX_SPOT_CHECK_UUID = _uuid16(0x2A5E)
PLX_CONTINUOUS_UUID = _uuid16(0x2A5F)

# IEEE-11073 16-bit SFLOAT special values.
_SFLOAT_SPECIAL = {0x07FF, 0x0800, 0x07FE, 0x0802, 0x0801}


def sfloat(raw: int) -> float | None:
    """Decode an IEEE-11073 SFLOAT; None for NaN/NRes/+-INF/reserved."""
    raw &= 0xFFFF
    if raw in _SFLOAT_SPECIAL:
        return None
    mantissa = raw & 0x0FFF
    exponent = raw >> 12
    if mantissa >= 0x0800:
        mantissa -= 0x1000
    if exponent >= 0x8:
        exponent -= 0x10
    return mantissa * (10.0**exponent)


def parse_plx_measurement(data: bytes) -> Vitals | None:
    """Decode a PLX Continuous (0x2A5F) or Spot-check (0x2A5E) measurement.

    Both start with ``flags | SpO2 (SFLOAT) | PR (SFLOAT)``; the optional
    fields after that vary by flags and aren't needed here.
    """
    if len(data) < 5:
        return None
    spo2 = valid_spo2(sfloat(data[1] | (data[2] << 8)))
    hr = valid_hr(sfloat(data[3] | (data[4] << 8)))
    return Vitals(hr=hr, spo2=spo2, worn=True, calibrating=hr is None and spo2 is None)


# ---------------------------------------------------------------------------
# Health Data Server (HDS) watch app -> HTTP PUT {"data": "heartRate:72"}
# ---------------------------------------------------------------------------

HDS_DEFAULT_PORT = 3476


def parse_hds_body(body: bytes) -> Vitals | None:
    """Decode one HDS watch-app message.

    HDS sends one metric per request as ``"<dataType>:<value>"``. Only
    heartRate is used: the watch's oxygenSaturation values are occasional
    background spot checks, not a live reading, and showing one as live
    SpO2 would be misleading.
    """
    try:
        message = json.loads(body)
    except ValueError:
        return None
    data = message.get("data") if isinstance(message, dict) else None
    if not isinstance(data, str) or ":" not in data:
        return None
    kind, _, value = data.partition(":")
    if kind != "heartRate":
        return None
    try:
        hr = valid_hr(float(value))
    except ValueError:
        return None
    if hr is None:
        return None
    return Vitals(hr=hr)
