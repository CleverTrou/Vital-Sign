"""Simulated readings, for laying out the overlay without any hardware."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Callable

from . import merge
from .protocols import Vitals


class DemoSource:
    label = "Demo"

    def __init__(
        self,
        emit: Callable[[str, Vitals], None],
        set_state: Callable[[str], None],
        *,
        period: float = 1.0,
        rng: random.Random | None = None,
    ) -> None:
        self._emit = emit
        self._set_state = set_state
        self.period = period
        self._rng = rng or random.Random()

    async def run(self) -> None:
        self._set_state(merge.CONNECTED)
        hr = 72.0
        spo2 = 97.0
        while True:
            # A slow random walk pulled back toward resting values, so the
            # trend graph has something plausible to draw.
            hr += self._rng.gauss(0, 1.5) + (72 - hr) * 0.05
            spo2 += self._rng.gauss(0, 0.4) + (97 - spo2) * 0.1
            self._emit(self.label, Vitals(hr=round(hr), spo2=min(100, round(spo2)), battery=88))
            await asyncio.sleep(self.period)
