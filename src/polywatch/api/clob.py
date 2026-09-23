"""Polymarket CLOB API: current midpoint prices (cached briefly)."""
from __future__ import annotations

import time
from collections.abc import Callable

from .http import CLOB_API, ApiError, Http


class ClobApi:
    def __init__(self, http: Http, ttl: float = 15.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.http = http
        self.ttl = ttl
        self.clock = clock
        self._cache: dict[str, tuple[float, float | None]] = {}

    async def midpoint(self, token_id: str) -> float | None:
        now = self.clock()
        cached = self._cache.get(token_id)
        if cached is not None and now - cached[0] < self.ttl:
            return cached[1]
        try:
            data = await self.http.get_json(CLOB_API, "/midpoint", {"token_id": token_id})
            value: float | None = float(data["mid"])
        except (ApiError, KeyError, TypeError, ValueError):
            value = None
        self._cache[token_id] = (now, value)
        return value
