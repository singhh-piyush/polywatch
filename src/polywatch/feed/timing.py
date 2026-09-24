"""When feed markets settle, looked up once and re-checked so resolved markets drop out."""
from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from typing import Any

from ..models import MarketTiming


class MarketTimes:
    def __init__(self, gamma: Any, *, refresh_s: int = 300, clock: Callable[[], float] = time.time) -> None:
        self.gamma = gamma
        self.refresh_s = refresh_s
        self.clock = clock
        self._checked: dict[str, float] = {}
        self._timing: dict[str, MarketTiming] = {}

    def cached(self, slug: str) -> MarketTiming | None:
        return self._timing.get(slug)

    def take_due(self, slugs: Iterable[str]) -> list[str]:
        """Slugs to (re)fetch: never looked up, or still open and not checked for refresh_s.

        They are marked as checked now, so a burst of fills on one market triggers a single lookup.
        """
        now = self.clock()
        due = []
        for slug in dict.fromkeys(slugs):
            timing = self._timing.get(slug)
            checked = self._checked.get(slug)
            if checked is None or (not (timing and timing.closed) and now - checked >= self.refresh_s):
                self._checked[slug] = now
                due.append(slug)
        return due

    async def fetch(self, slug: str) -> MarketTiming | None:
        timing = await self.gamma.market_timing(slug)
        self._checked[slug] = self.clock()
        if timing is not None:  # a failed lookup keeps the last answer
            self._timing[slug] = timing
        return self._timing.get(slug)
