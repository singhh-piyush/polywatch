"""Indexes finished short-market windows: every fill, each wallet's exact result, and the price before close.

It works newest first, so a fresh backfill makes the last 24 hours usable first, and windows that just closed
always come before older history.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..api.data_api import DataApi
from ..api.gamma import GammaApi, json_list, winner_of
from ..config import Settings
from .markets import INTERVALS, Window, windows_between
from .pnl import COPY_DELAY_S, LEAD_S, dedupe, price_before, window_results
from .store import ShortStore

log = logging.getLogger(__name__)

BATCH = 40          # slugs per Gamma lookup
SETTLE_S = 60       # wait this long after a window closes before indexing it
UNSETTLED_RETRY_S = 300  # a window with no result yet (hourly markets settle slowly) is retried after this
MISSING_GRACE_S = 3600   # a market still not found this long after closing doesn't exist


@dataclass(frozen=True)
class IndexProgress:
    done: int         # windows indexed within the backfill range
    total: int        # windows in the backfill range
    pending: int
    oldest_ts: int | None


class ShortIndexer:
    def __init__(self, data: DataApi, gamma: GammaApi, store: ShortStore, cfg: Settings, *,
                 clock: Callable[[], float] = time.time, on_indexed: Callable[[IndexProgress], None] | None = None,
                 coins: Callable[[], tuple[str, ...]] | None = None) -> None:
        self.data = data
        self.gamma = gamma
        self.store = store
        self.cfg = cfg
        self.clock = clock
        self.on_indexed = on_indexed or (lambda _p: None)
        self.coins = coins or (lambda: tuple(cfg.short_coins))
        self._unsettled: dict[str, float] = {}  # slug -> when to try again
        self._semaphore = asyncio.Semaphore(cfg.short_index_concurrency)
        self.progress = IndexProgress(0, 0, 0, None)

    def pending(self, now: int) -> tuple[list[Window], int]:
        """Windows still to index, newest first, and how many windows the backfill range holds."""
        since = int(now - self.cfg.short_backfill_days * 86_400)
        windows = windows_between(self.coins(), tuple(INTERVALS), since, now - SETTLE_S)
        done = self.store.indexed_slugs(since)
        waiting = [w for w in windows if w.slug not in done and self._unsettled.get(w.slug, 0) <= now]
        return waiting, len(windows)

    async def run(self) -> None:
        while True:
            try:
                worked = await self.step()
            except Exception:
                log.exception("short-market indexing failed")
                worked = False
            if not worked:
                await asyncio.sleep(15)

    async def step(self) -> bool:
        """Index one batch. Returns False when there was nothing to do."""
        now = int(self.clock())
        waiting, total = self.pending(now)
        if not waiting:
            self._report(total, 0)
            if now % 3600 < 30:
                await self.store.write(lambda: self.store.prune(now))
            return False
        batch = waiting[:BATCH]
        markets = await self.gamma.markets_by_slug([w.slug for w in batch])
        await asyncio.gather(*(self._index(w, markets.get(w.slug), now) for w in batch))
        self._report(total, len(waiting) - len(batch))
        return True

    def _report(self, total: int, pending: int) -> None:
        coverage = self.store.coverage()
        self.progress = IndexProgress(total - pending, total, pending, coverage[1] if coverage else None)
        self.on_indexed(self.progress)

    async def _index(self, window: Window, market: dict[str, Any] | None, now: int) -> None:
        if market is None:
            if now - window.end_ts > MISSING_GRACE_S:
                await self.store.write(lambda: self.store.save_missing(window, now))  # no market for that interval
            else:
                self._unsettled[window.slug] = now + UNSETTLED_RETRY_S
            return
        winner = winner_of(market)
        outcomes = tuple(str(o) for o in json_list(market.get("outcomes")))
        if winner is None or len(outcomes) != 2:
            self._unsettled[window.slug] = now + UNSETTLED_RETRY_S
            return
        condition_id = str(market.get("conditionId") or "")
        async with self._semaphore:
            try:
                rows, capped = await self.data.market_trades(condition_id)
                if capped:  # too many fills to page through at once: fetch buys and sells separately
                    buys, capped_buys = await self.data.market_trades(condition_id, side="BUY")
                    sells, capped_sells = await self.data.market_trades(condition_id, side="SELL")
                    rows, capped = dedupe(rows + buys + sells), capped_buys or capped_sells
            except Exception as exc:
                log.warning("could not fetch trades for %s: %s", window.slug, exc)
                self._unsettled[window.slug] = now + UNSETTLED_RETRY_S
                return
        pair = (outcomes[0], outcomes[1])
        self._unsettled.pop(window.slug, None)

        def save() -> None:  # thousands of fills: worked out and written off the event loop
            results = window_results(rows, pair, winner, window.end_ts)
            self.store.save_window(window, condition_id=condition_id, outcomes=pair, winner=winner,
                                   price_before=price_before(rows, pair, window.end_ts), fills=len(rows),
                                   truncated=capped, results=results.values(), now=now,
                                   price_copy=price_before(rows, pair, window.end_ts, lead_s=LEAD_S - COPY_DELAY_S))

        await self.store.write(save)
