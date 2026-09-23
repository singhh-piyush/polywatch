"""Shared async HTTP client: client-side rate limiting and retries with backoff."""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

DATA_API = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

RETRYABLE = {429, 500, 502, 503, 504}


class ApiError(Exception):
    pass


class RateLimiter:
    """Token bucket allowing at most `rate` acquisitions per `per` seconds."""

    def __init__(self, rate: int, per: float) -> None:
        self.capacity = float(rate)
        self.tokens = float(rate)
        self.fill_rate = rate / per
        self.updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.fill_rate)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                await asyncio.sleep((1 - self.tokens) / self.fill_rate)


class Http:
    def __init__(self, client: httpx.AsyncClient | None = None, *, max_tries: int = 5, backoff: float = 0.5) -> None:
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(20.0), headers={"User-Agent": "polywatch/0.1"})
        self.max_tries = max_tries
        self.backoff = backoff
        # Documented limits: 150 req/10s for (closed-)positions, 1000 req/10s overall. Stay below both.
        self._positions = RateLimiter(120, 10)
        self._default = RateLimiter(600, 10)

    def _limiter(self, path: str) -> RateLimiter:
        return self._positions if "positions" in path else self._default

    async def get_json(self, base: str, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{base}{path}"
        err: Exception = ApiError("no attempts made")
        for attempt in range(1, self.max_tries + 1):
            await self._limiter(path).acquire()
            try:
                resp = await self.client.get(url, params=params)
            except httpx.TransportError as exc:
                err = exc
            else:
                if resp.status_code < 400:
                    try:
                        return resp.json()
                    except ValueError:  # an overload or proxy page instead of JSON
                        err = ApiError(f"GET {url} -> {resp.status_code} with a non-JSON body: {resp.text[:100]}")
                elif resp.status_code not in RETRYABLE:
                    raise ApiError(f"GET {url} {params} -> {resp.status_code}: {resp.text[:200]}")
                else:
                    err = ApiError(f"GET {url} -> {resp.status_code}")
            if attempt < self.max_tries:
                delay = self.backoff * 2 ** (attempt - 1) * (1 + random.random())
                log.debug("retrying %s in %.2fs: %s", url, delay, err)
                await asyncio.sleep(delay)
        raise ApiError(f"GET {url} failed after {self.max_tries} tries: {err}") from err

    async def aclose(self) -> None:
        await self.client.aclose()
