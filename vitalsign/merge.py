"""Combine readings from several sources into the one the overlay shows.

Sources are listed in priority order. For each metric (heart rate, SpO2)
the overlay gets the value from the highest-priority source that has a
recent, usable reading for it. That is what makes a fallback work: with
``--source viatom --source hds``, the ring supplies both numbers while
it's on your finger, and the watch's heart rate takes over the moment
the ring is off, asleep or out of battery.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from .protocols import Vitals

# Per-source connection states, as reported by each source.
CONNECTING = "connecting"  # actively trying to reach a device
CONNECTED = "connected"
WAITING = "waiting"  # nothing to connect to yet (scanning, or listening for a watch)
IDLE = "idle"  # backing off before the next attempt
DISCONNECTED = "disconnected"

# Aggregate state priority: the best state among all sources wins, so one
# connected source keeps the overlay from saying "Disconnected" just
# because a lower-priority fallback is between attempts.
_STATE_RANK = {CONNECTED: 4, CONNECTING: 3, WAITING: 2, IDLE: 1, DISCONNECTED: 0}

DEFAULT_FRESH_SECONDS = 15.0


@dataclass(frozen=True)
class Reading:
    """Vitals tagged with the source that produced them and when."""

    source: str
    vitals: Vitals
    at: float = field(default_factory=time.monotonic)
    wall: datetime = field(default_factory=lambda: datetime.now().astimezone())

    @property
    def usable(self) -> bool:
        return self.vitals.worn and not self.vitals.calibrating


class Merger:
    """Holds the latest reading and state per source; builds overlay payloads."""

    def __init__(
        self,
        source_count: int,
        fresh_seconds: float = DEFAULT_FRESH_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._latest: list[Reading | None] = [None] * source_count
        self._states: list[str] = [WAITING] * source_count
        self._fresh = fresh_seconds
        self._clock = clock
        self._aggregate: str | None = None

    def update(self, index: int, reading: Reading) -> dict | None:
        self._latest[index] = reading
        return self.payload()

    def payload(self) -> dict | None:
        """The composite reading to broadcast, or None if nothing is fresh."""
        now = self._clock()
        fresh = [r for r in self._latest if r is not None and now - r.at <= self._fresh]
        if not fresh:
            return None

        hr_from = next((r for r in fresh if r.usable and r.vitals.hr is not None), None)
        spo2_from = next((r for r in fresh if r.usable and r.vitals.spo2 is not None), None)
        primary = hr_from or spo2_from or fresh[0]
        v = primary.vitals
        live = hr_from is not None or spo2_from is not None
        return {
            "type": "reading",
            "hr": hr_from.vitals.hr if hr_from else None,
            "spo2": spo2_from.vitals.spo2 if spo2_from else None,
            "worn": True if live else v.worn,
            "calibrating": False if live else v.calibrating,
            "battery": v.battery,
            "movement": v.movement,
            "pi": v.pi,
            "source": primary.source,
            "hr_source": hr_from.source if hr_from else None,
            "spo2_source": spo2_from.source if spo2_from else None,
            "ts": primary.wall.isoformat(),
        }

    def set_state(self, index: int, state: str) -> str | None:
        """Record one source's state; return the new aggregate if it changed."""
        self._states[index] = state
        aggregate = max(self._states, key=lambda s: _STATE_RANK.get(s, -1))
        if aggregate == self._aggregate:
            return None
        self._aggregate = aggregate
        return aggregate

    @property
    def aggregate_state(self) -> str:
        return self._aggregate or WAITING
