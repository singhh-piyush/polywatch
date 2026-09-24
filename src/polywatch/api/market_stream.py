"""Live prices from the CLOB market websocket, for exactly the outcomes on screen.

Prices arrive as the order book changes, so nothing is polled. The subscription is the whole asset set; when it
changes the socket reconnects with the new set, at most once every RESUBSCRIBE_S.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import Callable, Iterable
from typing import Any

from websockets.asyncio.client import connect

log = logging.getLogger(__name__)

MARKET_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
RESUBSCRIBE_S = 5.0
PING_S = 10.0
SHARD = 100  # outcomes per socket
WIDE_SPREAD = 0.10  # Polymarket shows the last trade instead of the midpoint when the spread is wider than this


def _f(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def shown_price(bid: float | None, ask: float | None, last: float | None) -> float | None:
    """The price Polymarket displays: the midpoint, or the last trade when the spread is wide."""
    if bid is not None and ask is not None and ask > 0:
        if ask - bid <= WIDE_SPREAD or last is None:
            return (bid + ask) / 2
    return last


class PriceBook:
    """Best bid, best ask and last trade per asset, updated from socket messages."""

    def __init__(self) -> None:
        self.bid: dict[str, float] = {}
        self.ask: dict[str, float] = {}
        self.last: dict[str, float] = {}

    def price(self, asset: str) -> float | None:
        return shown_price(self.bid.get(asset), self.ask.get(asset), self.last.get(asset))

    def apply(self, msg: dict[str, Any]) -> set[str]:
        """Update from one message; returns the assets whose shown price may have changed."""
        kind = msg.get("event_type")
        changed: set[str] = set()
        if kind == "book":
            asset = str(msg.get("asset_id") or "")
            bids = [p for p in (_f(level.get("price")) for level in msg.get("bids") or []) if p is not None]
            asks = [p for p in (_f(level.get("price")) for level in msg.get("asks") or []) if p is not None]
            if bids:
                self.bid[asset] = max(bids)
            if asks:
                self.ask[asset] = min(asks)
            if (last := _f(msg.get("last_trade_price"))) is not None:
                self.last[asset] = last
            changed.add(asset)
        elif kind == "price_change":
            for change in msg.get("price_changes") or []:
                asset = str(change.get("asset_id") or "")
                if (bid := _f(change.get("best_bid"))) is not None:
                    self.bid[asset] = bid
                if (ask := _f(change.get("best_ask"))) is not None:
                    self.ask[asset] = ask
                changed.add(asset)
        elif kind == "last_trade_price":
            asset = str(msg.get("asset_id") or "")
            if (last := _f(msg.get("price"))) is not None:
                self.last[asset] = last
                changed.add(asset)
        return changed


class MarketStream:
    """Runs on a thread of its own with its own event loop: busy books send hundreds of messages a second, and the
    server drops a connection that isn't read promptly ("slow consumer"). Prices are handed to the main loop."""

    def __init__(self, on_prices: Callable[[dict[str, float]], None], *, url: str = MARKET_WS,
                 on_status: Callable[[str], None] | None = None) -> None:
        self.on_prices = on_prices
        self.on_status = on_status or (lambda _s: None)
        self.url = url
        self.book = PriceBook()
        self.assets: frozenset[str] = frozenset()
        self.connected = False
        self._version = 0      # bumped when the asset set changes
        self._live: list[bool] = []
        self._stop = threading.Event()
        self._main: asyncio.AbstractEventLoop | None = None

    def watch(self, assets: Iterable[str]) -> None:
        wanted = frozenset(a for a in assets if a)
        if wanted != self.assets:
            self.assets = wanted
            self._version += 1

    async def run(self) -> None:
        self._main = asyncio.get_running_loop()
        self._stop.clear()
        try:
            await asyncio.to_thread(lambda: asyncio.run(self._run()))
        finally:
            self._stop.set()

    def stop(self) -> None:
        self._stop.set()

    def _deliver(self, batch: dict[str, float]) -> None:
        if self._main is not None and not self._main.is_closed():
            self._main.call_soon_threadsafe(self.on_prices, batch)

    async def _run(self) -> None:
        """One socket per SHARD outcomes: the server drops connections that subscribe to too many at once."""
        while not self._stop.is_set():
            if not self.assets:
                await asyncio.sleep(0.5)
                continue
            version = self._version
            ordered = sorted(self.assets)
            shards = [ordered[i:i + SHARD] for i in range(0, len(ordered), SHARD)]
            self._live = [False] * len(shards)
            await asyncio.gather(*(self._shard(n, chunk, version) for n, chunk in enumerate(shards)))

    async def _shard(self, n: int, assets: list[str], version: int) -> None:
        """Keep one socket up for these outcomes until the asset set changes."""
        backoff = 1.0
        while not self._stop.is_set() and self._version == version:
            try:
                async with connect(self.url, open_timeout=15, max_size=None, max_queue=None) as ws:
                    await ws.send(json.dumps({"assets_ids": assets, "type": "market"}))
                    self._live[n] = True
                    self.connected = all(self._live)
                    backoff = 1.0
                    await self._read(ws, version)
            except Exception as exc:
                log.warning("price stream disconnected: %s", exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
            finally:
                self._live[n] = False
                self.connected = False

    async def _read(self, ws: Any, version: int) -> None:
        """Read until the asset set changes (then return to resubscribe) or the socket drops (raise)."""
        pending: set[str] = set()
        closing = False

        async def housekeeping() -> None:
            nonlocal closing
            loop = asyncio.get_running_loop()
            last_ping = changed_at = loop.time()
            while True:
                await asyncio.sleep(0.5)
                if pending:
                    batch = {a: p for a in pending if (p := self.book.price(a)) is not None}
                    pending.clear()
                    if batch:
                        self._deliver(batch)
                if loop.time() - last_ping >= PING_S:
                    await ws.send("PING")
                    last_ping = loop.time()
                if self._version == version:
                    changed_at = loop.time()
                if self._stop.is_set() or loop.time() - changed_at >= RESUBSCRIBE_S:
                    closing = True
                    await ws.close()
                    return

        side = asyncio.create_task(housekeeping())
        try:
            async for raw in ws:
                for msg in _messages(raw):
                    pending.update(self.book.apply(msg))
        except Exception:
            if not closing:
                raise
        finally:
            side.cancel()
        if not closing:
            raise ConnectionError("price stream closed")


def _messages(raw: str | bytes) -> list[dict[str, Any]]:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if not raw.startswith(("{", "[")):
        return []  # "PONG"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        return [data]
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []
