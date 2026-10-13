"""Bluetooth LE sources: discovery, protocol detection, and per-protocol sessions.

Every BLE protocol except the legacy Viatom one shares BleSession's
connect / subscribe / reconnect-with-backoff loop and only supplies the
protocol-specific parts. The legacy protocol (Checkme O2 Max, O2Ring,
etc.) keeps running through viatom-ble's own ViatomClient, the code path
verified against a real O2 Max.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from bleak import BleakClient, BleakScanner
from viatom_ble import Reading as ViatomReading
from viatom_ble import ViatomClient
from viatom_ble.protocol import SERVICE_UUID as VIATOM_SERVICE_UUID
from viatom_ble.scanner import COMPATIBLE_NAME_PREFIXES as VIATOM_NAME_PREFIXES

from . import merge
from .protocols import (
    BATTERY_LEVEL_UUID,
    HRS_MEASUREMENT_UUID,
    HRS_SERVICE_UUID,
    NUS_NOTIFY_UUID,
    NUS_SERVICE_UUID,
    OXYII_NOTIFY_UUID,
    OXYII_OP_LIVE,
    OXYII_SERVICE_UUID,
    OXYII_WRITE_UUID,
    PLX_CONTINUOUS_UUID,
    PLX_SERVICE_UUID,
    PLX_SPOT_CHECK_UUID,
    VIATOM_MANUFACTURER_ID,
    OxyIIDecoder,
    PC60Decoder,
    Vitals,
    oxyii_encode,
    parse_heart_rate_measurement,
    parse_oxyii_live,
    parse_plx_measurement,
    valid_hr,
    valid_spo2,
)

log = logging.getLogger("bridge.ble")

EmitFn = Callable[[Vitals], None]
StateFn = Callable[[str], None]

VIATOM = "viatom"
OXYII = "oxyii"
PC60 = "pc60"
PLX = "plx"
HRS = "hrs"
BLE_KINDS = (VIATOM, OXYII, PC60, PLX, HRS)
# A Viatom device whose advertisement doesn't say which generation it is.
# Resolved by connecting once and looking at its services (see probe()).
VIATOM_FAMILY = "viatom-family"

KIND_DESCRIPTIONS = {
    VIATOM: "Viatom/Wellue ring, original protocol (Checkme O2 Max, O2Ring, ...)",
    OXYII: "Viatom/Wellue ring, second-generation protocol (O2Ring S, ...)",
    PC60: "Viatom PC-60FW-family fingertip oximeter",
    PLX: "Bluetooth pulse oximeter (standard PLX service)",
    HRS: "Bluetooth heart-rate sensor (standard service: straps, watches broadcasting heart rate, phone relay apps)",
    VIATOM_FAMILY: "Viatom/Wellue device; protocol is detected when connecting",
}

# Advertised-name prefixes, lowercase, matched with startswith().
OXYII_NAME_PREFIXES = ("shqo2pro", "s8-aw", "t8520")
PC60_NAME_PREFIXES = ("pc-60", "pc60", "oxysmart")
# viatom-ble's prefix list includes "po", which also matches Polar heart
# rate straps. Those are HRS devices and are classified as such first
# when they advertise the HRS service; this catches them when they don't.
_NOT_VIATOM_PREFIXES = ("polar",)


def classify(name: str | None, service_uuids: Sequence[str], manufacturer_ids: Sequence[int]) -> str | None:
    """Pick the protocol for an advertisement, or None if unsupported.

    Specific signals beat generic ones: a protocol's own service UUID
    first, then Viatom's manufacturer ID, then the standard PLX/HRS
    services, and loose name prefixes last. Viatom rings of both
    generations advertise the standard heart-rate service too, but their
    own protocols add SpO2. A real Checkme O2 Max advertises only the
    heart-rate service and the Viatom manufacturer ID -- not its protocol
    service -- so the generation often has to come from probe() instead.
    """
    services = {u.lower() for u in service_uuids}
    lname = (name or "").lower()
    if VIATOM_SERVICE_UUID in services:
        return VIATOM
    if OXYII_SERVICE_UUID in services:
        return OXYII
    if lname.startswith(PC60_NAME_PREFIXES):
        return PC60
    if VIATOM_MANUFACTURER_ID in manufacturer_ids or lname.startswith(OXYII_NAME_PREFIXES):
        return VIATOM_FAMILY
    if PLX_SERVICE_UUID in services:
        return PLX
    if HRS_SERVICE_UUID in services:
        return HRS
    if lname.startswith(VIATOM_NAME_PREFIXES) and not lname.startswith(_NOT_VIATOM_PREFIXES):
        return VIATOM_FAMILY
    return None


def classify_gatt(service_uuids: Sequence[str]) -> str | None:
    """Pick the protocol from a connected device's full service list."""
    services = {u.lower() for u in service_uuids}
    for uuid, kind in (
        (VIATOM_SERVICE_UUID, VIATOM),
        (OXYII_SERVICE_UUID, OXYII),
        (NUS_SERVICE_UUID, PC60),
        (PLX_SERVICE_UUID, PLX),
        (HRS_SERVICE_UUID, HRS),
    ):
        if uuid in services:
            return kind
    return None


def kind_matches(wanted: str | None, advertised: str) -> bool:
    """Whether a scanned device can serve a source asking for ``wanted``."""
    if wanted is None or wanted == advertised:
        return True
    if advertised == VIATOM_FAMILY:
        return wanted in (VIATOM, OXYII, PC60, HRS)
    # Every Viatom ring also offers the standard heart-rate service.
    return wanted == HRS and advertised in (VIATOM, OXYII)


@dataclass(frozen=True)
class Found:
    address: str
    name: str | None
    kind: str
    rssi: int | None

    @property
    def label(self) -> str:
        return self.name or f"{self.kind} {self.address}"


# Bleak backends handle one scan at a time much more reliably than
# several overlapping ones, and several sources may want to scan at once.
_scan_lock = asyncio.Lock()


async def scan(duration: float) -> list[Found]:
    """Scan once and return every supported device, strongest signal first."""
    async with _scan_lock:
        discovered = await BleakScanner.discover(timeout=duration, return_adv=True)
    found = []
    for device, adv in discovered.values():
        name = adv.local_name or device.name
        kind = classify(name, adv.service_uuids or (), list(adv.manufacturer_data or {}))
        if kind:
            found.append(Found(device.address, name, kind, adv.rssi))
    found.sort(key=lambda f: f.rssi if f.rssi is not None else -999, reverse=True)
    return found


class DeviceNotFound(RuntimeError):
    pass


async def probe(address: str, timeout: float = 15.0) -> str | None:
    """Connect briefly and return the protocol its services indicate."""
    async with _scan_lock:
        device = await BleakScanner.find_device_by_address(address, timeout=timeout)
    if device is None:
        raise DeviceNotFound("stopped advertising before it could be identified")
    async with BleakClient(device, timeout=timeout) as client:
        return classify_gatt([s.uuid for s in client.services])


class WrongProtocol(RuntimeError):
    pass


def _backoff(failures: int, base: float = 1.0, cap: float = 60.0) -> float:
    return min(base * (2 ** max(failures - 1, 0)), cap)


class BleSession:
    """Connect to one device and stream its readings, reconnecting forever.

    Subclasses implement start() (find characteristics, subscribe) and,
    for request/response protocols, poll(). Notification handlers call
    self.received() with decoded Vitals.
    """

    kind = ""
    service_uuid = ""
    # For polled devices: a reply is expected at least this often, and
    # longer silence means the link is dead even if the OS hasn't noticed.
    # None for push devices, which go quiet legitimately (a strap without
    # skin contact, a Checkme O2 Max's heart-rate service when it can't
    # sense a finger) and whose real drops arrive as disconnect events.
    stall_seconds: float | None = None
    # Seconds of continuous "not worn" before disconnecting so the device
    # can power down, and how long to stay away afterwards. None = never.
    inactivity_timeout: float | None = None
    inactivity_delay = 130.0

    def __init__(
        self,
        address: str,
        emit: EmitFn,
        set_state: StateFn,
        *,
        read_period: float = 2.0,
        connect_timeout: float = 15.0,
        client_factory: Callable[..., BleakClient] = BleakClient,
        find_device: Callable[[str, float], Awaitable[object]] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.address = address
        self._emit = emit
        self._set_state = set_state
        self.read_period = read_period
        self.connect_timeout = connect_timeout
        self._client_factory = client_factory
        self._find_device = find_device or self._default_find_device
        self._clock = clock or (lambda: asyncio.get_running_loop().time())
        self._last_rx = 0.0
        self._not_worn_since: float | None = None

    @staticmethod
    async def _default_find_device(address: str, timeout: float) -> object:
        async with _scan_lock:
            return await BleakScanner.find_device_by_address(address, timeout=timeout)

    # -- protocol hooks ---------------------------------------------------

    async def start(self, client: BleakClient) -> None:
        raise NotImplementedError

    async def poll(self, client: BleakClient) -> None:
        """Called every read_period. Push protocols leave this empty."""

    # -- shared machinery -------------------------------------------------

    def received(self, vitals: Vitals) -> None:
        now = self._clock()
        self._last_rx = now
        if vitals.worn:
            self._not_worn_since = None
        elif self._not_worn_since is None:
            self._not_worn_since = now
        self._emit(vitals)

    def require_service(self, client: BleakClient, uuid: str):
        service = client.services.get_service(uuid)
        if service is None:
            other = classify_gatt([s.uuid for s in client.services])
            hint = f" It looks like a '{other}' device; try --source {other}:{self.address}." if other else ""
            raise WrongProtocol(f"{self.address} has no {self.kind} service ({uuid}).{hint}")
        return service

    async def run(self) -> None:
        failures = 0
        while True:
            try:
                rested = await self._session()
                failures = 0
                if rested:
                    continue
                delay = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 -- any failure means "reconnect"
                failures += 1
                delay = _backoff(failures)
                level = logging.ERROR if isinstance(e, WrongProtocol) else logging.WARNING
                log.log(level, f"{self.kind} {self.address}: {type(e).__name__}: {e}")
            self._set_state(merge.IDLE)
            log.info(f"{self.kind} {self.address}: reconnecting in {delay:.0f}s")
            await asyncio.sleep(delay)

    async def _session(self) -> bool:
        """One connection. Returns True if it ended by resting the device."""
        self._set_state(merge.CONNECTING)
        device = await self._find_device(self.address, self.connect_timeout)
        if device is None:
            raise DeviceNotFound("not advertising (asleep, out of range, or connected to its phone app)")

        disconnected = asyncio.Event()
        client = self._client_factory(
            device,
            disconnected_callback=lambda _c: disconnected.set(),
            timeout=self.connect_timeout,
        )
        try:
            await asyncio.wait_for(client.connect(), timeout=self.connect_timeout)
            log.info(f"{self.kind} {self.address}: connected")
            self._set_state(merge.CONNECTED)
            self._last_rx = self._clock()
            self._not_worn_since = None
            await self.start(client)
            while not disconnected.is_set():
                await self.poll(client)
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(disconnected.wait(), timeout=self.read_period)
                now = self._clock()
                if self.stall_seconds is not None and now - self._last_rx > self.stall_seconds:
                    raise TimeoutError(f"no data for {now - self._last_rx:.0f}s")
                if (
                    self.inactivity_timeout is not None
                    and self._not_worn_since is not None
                    and now - self._not_worn_since >= self.inactivity_timeout
                ):
                    break
            else:
                raise ConnectionError("device disconnected")
        finally:
            with contextlib.suppress(Exception):
                await client.disconnect()
            self._set_state(merge.DISCONNECTED)

        log.info(f"{self.kind} {self.address}: not worn; disconnecting for {self.inactivity_delay:.0f}s to save its battery")
        self._set_state(merge.IDLE)
        await asyncio.sleep(self.inactivity_delay)
        return True


class PC60Session(BleSession):
    kind = PC60
    service_uuid = NUS_SERVICE_UUID

    async def start(self, client: BleakClient) -> None:
        self.require_service(client, NUS_SERVICE_UUID)
        decoder = PC60Decoder()

        def on_notify(_char, data: bytearray) -> None:
            for vitals in decoder.feed(bytes(data)):
                self.received(vitals)

        await client.start_notify(NUS_NOTIFY_UUID, on_notify)


class HrsSession(BleSession):
    kind = HRS
    service_uuid = HRS_SERVICE_UUID

    async def start(self, client: BleakClient) -> None:
        self.require_service(client, HRS_SERVICE_UUID)
        battery = await _read_battery(client)

        def on_notify(_char, data: bytearray) -> None:
            vitals = parse_heart_rate_measurement(bytes(data))
            if vitals is not None:
                self.received(_with_battery(vitals, battery))

        await client.start_notify(HRS_MEASUREMENT_UUID, on_notify)


class PlxSession(BleSession):
    kind = PLX
    service_uuid = PLX_SERVICE_UUID

    async def start(self, client: BleakClient) -> None:
        service = self.require_service(client, PLX_SERVICE_UUID)
        available = {c.uuid.lower() for c in service.characteristics}
        # Continuous measurements stream ~1/s; spot-checks only arrive
        # when a measurement completes.
        if PLX_CONTINUOUS_UUID in available:
            char = PLX_CONTINUOUS_UUID
        elif PLX_SPOT_CHECK_UUID in available:
            char = PLX_SPOT_CHECK_UUID
        else:
            raise WrongProtocol(f"{self.address}: PLX service has no measurement characteristic")
        battery = await _read_battery(client)

        def on_notify(_char, data: bytearray) -> None:
            vitals = parse_plx_measurement(bytes(data))
            if vitals is not None:
                self.received(_with_battery(vitals, battery))

        await client.start_notify(char, on_notify)


class OxyIISession(BleSession):
    """Second-generation Viatom rings: poll the live-values command.

    The ring answers the live request without the authentication
    handshake that file downloads need. Should a firmware ever stop
    doing that, the session falls back to the ring's standard
    heart-rate service, so heart rate keeps working without SpO2.
    """

    kind = OXYII
    service_uuid = OXYII_SERVICE_UUID
    inactivity_timeout = 300.0
    _MAX_UNANSWERED = 3

    async def start(self, client: BleakClient) -> None:
        self.require_service(client, OXYII_SERVICE_UUID)
        self._decoder = OxyIIDecoder()
        self._seq = 0
        self._unanswered = 0
        self._fallback = False
        self.stall_seconds = max(self.read_period * 3, 10.0)

        def on_notify(_char, data: bytearray) -> None:
            for frame in self._decoder.feed(bytes(data)):
                if frame.op != OXYII_OP_LIVE:
                    continue
                vitals = parse_oxyii_live(frame.payload)
                if vitals is not None:
                    self._unanswered = 0
                    self.received(vitals)

        await client.start_notify(OXYII_NOTIFY_UUID, on_notify)

    async def poll(self, client: BleakClient) -> None:
        if self._fallback:
            return
        if self._unanswered >= self._MAX_UNANSWERED:
            await self._fall_back_to_hrs(client)
            return
        self._unanswered += 1
        frame = oxyii_encode(OXYII_OP_LIVE, self._seq)
        self._seq = (self._seq + 1) & 0xFF
        await client.write_gatt_char(OXYII_WRITE_UUID, frame, response=False)

    async def _fall_back_to_hrs(self, client: BleakClient) -> None:
        self._fallback = True
        # Heart-rate notifications are pushed, and go quiet legitimately
        # while the ring can't sense a finger (see BleSession.stall_seconds).
        self.stall_seconds = None
        self._last_rx = self._clock()
        if client.services.get_service(HRS_SERVICE_UUID) is None:
            raise WrongProtocol(f"{self.address}: ring didn't answer live-value requests and has no heart-rate service")
        log.warning(
            f"{self.kind} {self.address}: ring didn't answer live-value requests; "
            "using its standard heart-rate service instead (no SpO2)"
        )

        def on_hr(_char, data: bytearray) -> None:
            vitals = parse_heart_rate_measurement(bytes(data))
            if vitals is not None:
                self.received(vitals)

        await client.start_notify(HRS_MEASUREMENT_UUID, on_hr)


async def _read_battery(client: BleakClient) -> int | None:
    with contextlib.suppress(Exception):
        if client.services.get_characteristic(BATTERY_LEVEL_UUID) is not None:
            value = await client.read_gatt_char(BATTERY_LEVEL_UUID)
            return value[0] if value and value[0] <= 100 else None
    return None


def _with_battery(vitals: Vitals, battery: int | None) -> Vitals:
    if battery is None or vitals.battery is not None:
        return vitals
    return Vitals(**{**vitals.__dict__, "battery": battery})


class ViatomSession:
    """Original-protocol Viatom rings via viatom-ble's ViatomClient."""

    kind = VIATOM

    def __init__(self, address: str, emit: EmitFn, set_state: StateFn, *, read_period: float = 2.0, **_ignored) -> None:
        self.address = address
        self._emit = emit
        self._set_state = set_state
        self.read_period = read_period

    async def run(self) -> None:
        def on_reading(r: ViatomReading) -> None:
            self._emit(viatom_to_vitals(r))

        client = ViatomClient(
            address=self.address,
            read_period=self.read_period,
            on_reading=on_reading,
            on_state_change=self._set_state,
        )
        try:
            await client.run_forever()
        finally:
            await client.stop()


def viatom_to_vitals(r: ViatomReading) -> Vitals:
    worn = r.worn
    return Vitals(
        hr=valid_hr(r.heart_rate) if worn else None,
        spo2=valid_spo2(r.spo2) if worn else None,
        worn=worn,
        calibrating=r.calibrating,
        battery=r.battery if r.battery <= 100 else None,
        # A raw pulse-strength byte rather than a PI percentage; passed
        # through unscaled, as the bridge always has.
        pi=r.perfusion_index,
        movement=r.movement,
    )


SESSIONS: dict[str, type] = {
    VIATOM: ViatomSession,
    OXYII: OxyIISession,
    PC60: PC60Session,
    PLX: PlxSession,
    HRS: HrsSession,
}

# Addresses already taken by a source, so two sources never fight over
# one device (a BLE peripheral accepts a single central connection).
_claimed: set[str] = set()


class BleSource:
    """A --source entry for a Bluetooth device.

    ``kind`` and ``address`` are both optional:

    - both given: connect straight to that device with that protocol;
    - address only: wait until the device advertises, detect its protocol;
    - kind only, or neither: scan until exactly one matching device is in
      range. More than one is ambiguous, so the source keeps waiting and
      logs the candidates for you to pin one with ``kind:ADDRESS``.
    """

    def __init__(
        self,
        emit: Callable[[str, Vitals], None],
        set_state: StateFn,
        *,
        kind: str | None = None,
        address: str | None = None,
        scan_duration: float = 10.0,
        rescan_seconds: float = 20.0,
        read_period: float = 2.0,
    ) -> None:
        self.kind = kind
        self.address = address
        self._emit = emit
        self._set_state = set_state
        self.scan_duration = scan_duration
        self.rescan_seconds = rescan_seconds
        self.read_period = read_period
        self._last_complaint: tuple | None = None

    @property
    def description(self) -> str:
        what = self.kind or "any supported device"
        return f"{what} at {self.address}" if self.address else what

    async def run(self) -> None:
        if self.kind and self.address:
            label = self.address
        else:
            found = await self._discover()
            self.kind, self.address, label = self.kind or found.kind, found.address, found.label
        # Claimed before any probe, so a second auto source can't pick the
        # same device while this one is still identifying it.
        _claimed.add(self.address)
        try:
            if self.kind == VIATOM_FAMILY:
                self.kind = await self._identify(found)
            if label != self.address:
                log.info(f"Using {label} ({self.address}) -- {KIND_DESCRIPTIONS[self.kind]}")
            session = SESSIONS[self.kind](
                self.address,
                lambda vitals: self._emit(label, vitals),
                self._set_state,
                read_period=self.read_period,
            )
            await session.run()
        finally:
            _claimed.discard(self.address)

    async def _discover(self) -> Found:
        while True:
            self._set_state(merge.WAITING)
            log.info(f"Scanning {self.scan_duration:.0f}s for {self.description}...")
            candidates = [
                f
                for f in await scan(self.scan_duration)
                if f.address not in _claimed
                and kind_matches(self.kind, f.kind)
                and (self.address is None or f.address == self.address)
            ]
            if len(candidates) == 1:
                return candidates[0]
            self._complain(candidates)
            await asyncio.sleep(self.rescan_seconds)

    async def _identify(self, found: Found) -> str:
        """Settle a Viatom device's protocol by looking at its services."""
        while True:
            self._set_state(merge.CONNECTING)
            try:
                kind = await probe(found.address)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 -- retried below
                log.warning(f"Couldn't identify {found.label} yet ({type(e).__name__}: {e}); retrying")
            else:
                if kind is not None:
                    return kind
                log.error(f"{found.label} has none of the supported services; retrying")
            self._set_state(merge.IDLE)
            await asyncio.sleep(self.rescan_seconds)

    def _complain(self, candidates: list[Found]) -> None:
        """Log why discovery is still waiting, once per distinct situation."""
        key = tuple(sorted(f.address for f in candidates))
        if key == self._last_complaint:
            return
        self._last_complaint = key
        if not candidates:
            log.warning(
                f"No {self.description} found yet; still looking. Make sure it's on/worn and not "
                "connected to its phone app (BLE allows one connection at a time)."
            )
            return
        listing = "\n".join(f"  - {f.label:<24} {f.kind:<6} {f.address}  RSSI={f.rssi}" for f in candidates)
        log.warning(
            f"Several matching devices in range; pin one with --source KIND:ADDRESS "
            f"(or SOURCES in config.sh/config.ps1). Still waiting:\n{listing}"
        )
