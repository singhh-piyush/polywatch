"""Discovery scan: leaderboard candidates → per-wallet data → metrics → verdict → ranking."""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from ..api.data_api import DataApi
from ..api.gamma import GammaApi
from ..config import Settings
from ..models import LeaderboardEntry, RankedTrader, TraderStats, Verdict
from ..store import Store
from .filters import early_exclusion, evaluate
from .metrics import activity_fields, bet_fields, fast_share, rebate_total, resolved_bets
from .scoring import rank_traders

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScanProgress:
    done: int
    total: int
    stats: TraderStats
    verdict: Verdict


def _name_from_rows(rows: list[dict[str, Any]]) -> str:
    return next((str(r["name"]) for r in rows if r.get("name")), "")


class Scanner:
    def __init__(self, data: DataApi, gamma: GammaApi, store: Store, cfg: Settings,
                 clock: Callable[[], int] = lambda: int(time.time())) -> None:
        self.data = data
        self.gamma = gamma
        self.store = store
        self.cfg = cfg
        self.clock = clock

    async def candidates(self, pins: set[str], limit: int | None = None) -> dict[str, LeaderboardEntry | None]:
        pool: dict[str, LeaderboardEntry | None] = {}
        for period, depth in self.cfg.candidate_depths:
            for entry in await self.data.leaderboard(period, depth):
                pool.setdefault(entry.wallet, entry)
        if limit is not None:
            pool = dict(list(pool.items())[:limit])
        for wallet in sorted(pins):
            pool.setdefault(wallet, None)
        return pool

    async def scan_wallet(self, wallet: str, lb: LeaderboardEntry | None, name: str = "") -> tuple[TraderStats, Verdict]:
        cfg, now = self.cfg, self.clock()
        markets_traded, trade_rows = await asyncio.gather(
            self.data.traded(wallet), self.data.activity(wallet, type_="TRADE"))
        stats = TraderStats(
            wallet=wallet,
            username=(lb.username if lb else "") or name or _name_from_rows(trade_rows),
            lb_pnl=lb.pnl if lb else 0.0,
            lb_volume=lb.volume if lb else 0.0,
            markets_traded=markets_traded,
            **activity_fields(trade_rows, cfg.quiet_gap_min_trades),
            fast_share=fast_share(trade_rows, snipe_price=cfg.snipe_price, flip_s=cfg.flip_window_s,
                                  min_buys=cfg.fast_min_buys),
        )
        if reason := early_exclusion(stats, now, cfg):
            return stats, Verdict(excluded=reason)

        since = now - cfg.window_days * 86_400
        (closed, truncated), positions, rebates, created = await asyncio.gather(
            self.data.closed_positions(wallet, since, cfg.closed_positions_max_pages),
            self.data.positions(wallet),
            self.data.activity(wallet, type_="MAKER_REBATE", start=since),
            self.gamma.account_created_ts(wallet),
        )
        stats = replace(
            stats,
            **bet_fields(resolved_bets(closed, positions, since), cfg.shrink_k),
            truncated=truncated,
            maker_rebates=rebate_total(rebates),
            account_age_d=(now - created) / 86_400 if created else None,
        )
        return stats, evaluate(stats, now, cfg)

    async def run(self, *, limit: int | None = None,
                  on_progress: Callable[[ScanProgress], None] | None = None) -> list[RankedTrader]:
        overrides = self.store.overrides()
        names = self.store.override_names()
        pins = {w for w, mode in overrides.items() if mode == "pin"}
        pool = await self.candidates(pins, limit)
        scan_id = self.store.start_scan(self.clock())
        semaphore = asyncio.Semaphore(self.cfg.scan_concurrency)
        results: list[tuple[TraderStats, Verdict]] = []

        async def scan_one(wallet: str, lb: LeaderboardEntry | None) -> None:
            async with semaphore:
                try:
                    stats, verdict = await self.scan_wallet(wallet, lb, names.get(wallet, ""))
                except Exception as exc:  # one bad wallet must never abort the scan
                    log.warning("scan failed for %s: %s", wallet, exc)
                    stats = TraderStats(wallet=wallet, username=(lb.username if lb else names.get(wallet, "")))
                    verdict = Verdict(excluded=f"error: {exc}")
            self.store.save_trader(scan_id, stats, verdict)
            results.append((stats, verdict))
            if on_progress is not None:
                on_progress(ScanProgress(done=len(results), total=len(pool), stats=stats, verdict=verdict))

        await asyncio.gather(*(scan_one(w, lb) for w, lb in pool.items()))
        ranked = rank_traders(results, self.cfg)
        self.store.finish_scan(scan_id, ranked, candidates=len(pool), now=self.clock())
        return ranked
