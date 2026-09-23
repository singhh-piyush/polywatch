"""Where feed trades come from: the live websocket plus a per-wallet /activity poll."""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Iterable
from typing import Any

from ..api.stream import stream_trades
from ..config import Settings
from ..models import Trade

log = logging.getLogger(__name__)


class ActivityPoller:
    """Polls /activity for each watched wallet.

    The websocket only carries taker fills, so a trader who mostly uses limit orders would be invisible
    without this. It also serves as the startup backfill and fills the gap after a reconnect.
    """

    def __init__(self, data: Any, *, backfill_s: int, overlap_s: int = 600, concurrency: int = 8) -> None:
        self.data = data
        self.backfill_s = backfill_s
        self.overlap_s = overlap_s
        self.cursor: dict[str, int] = {}
        self._semaphore = asyncio.Semaphore(concurrency)

    async def _poll_wallet(self, wallet: str, now: int) -> list[Trade]:
        start = self.cursor.get(wallet, now - self.backfill_s)
        async with self._semaphore:
            rows = await self.data.activity(wallet, type_="TRADE", start=start)
        trades = [t for t in map(Trade.from_api, rows) if t is not None]
        # Next time, start a little before the newest trade seen, by the server's clock: /activity can lag, the local
        # clock can drift, and the aggregator drops the duplicates the overlap brings back.
        newest = max((t.ts for t in trades), default=None)
        self.cursor[wallet] = start if newest is None else max(start, newest - self.overlap_s)
        return trades

    async def poll(self, wallets: list[str], now: int) -> list[Trade]:
        results = await asyncio.gather(*(self._poll_wallet(w, now) for w in wallets), return_exceptions=True)
        trades: list[Trade] = []
        for wallet, result in zip(wallets, results):
            if isinstance(result, BaseException):
                log.warning("activity poll failed for %s: %s", wallet, result)
                continue
            trades.extend(result)
        return sorted(trades, key=lambda t: t.ts)


class FeedService:
    def __init__(self, data: Any, cfg: Settings, *, on_trade: Callable[[Trade], None],
                 on_status: Callable[[str], None], stream: Callable[..., Any] = stream_trades,
                 clock: Callable[[], float] = time.time) -> None:
        self.cfg = cfg
        self.on_trade = on_trade
        self.on_status = on_status
        self.stream = stream
        self.clock = clock
        self.poller = ActivityPoller(data, backfill_s=cfg.backfill_hours * 3600)
        self.watched: frozenset[str] = frozenset()
        self.trades_seen = 0

    def set_watched(self, wallets: Iterable[str]) -> None:
        self.watched = frozenset(wallets)

    def _deliver(self, trade: Trade) -> None:
        try:
            self.on_trade(trade)
        except Exception:
            log.exception("feed handler failed for %s", trade)

    async def run_stream(self) -> None:
        async for trade in self.stream(on_status=self.on_status):
            self.trades_seen += 1
            # Some websocket fills come without market details; the poller delivers them complete within seconds.
            if trade.wallet in self.watched and trade.slug:
                self._deliver(trade)

    async def poll_once(self) -> None:
        for trade in await self.poller.poll(sorted(self.watched), int(self.clock())):
            self._deliver(trade)

    async def run_poller(self) -> None:
        while True:
            try:
                await self.poll_once()
            except Exception:
                log.exception("activity poll failed")
            await asyncio.sleep(self.cfg.poll_interval_s)
