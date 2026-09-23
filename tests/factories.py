"""Builders for synthetic API rows and domain objects used across the test suite."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from polywatch.config import Settings
from polywatch.models import Trade, TraderStats, WatchedTrader

DAY = 86_400
NOW = 1_790_000_000  # 2026-09-21 UTC
FIXTURES = Path(__file__).parent / "fixtures"

# Thresholds pinned for tests, so tuning the defaults in config.py (Task 10) never breaks them.
PINNED = Settings(
    window_days=90, active_days=14, min_resolved=15, max_resolved=2000, max_lifetime_markets=20_000,
    max_top_share=0.50, min_account_age_d=30, mm_min_volume=5_000_000, mm_max_margin=0.02,
    max_rebate_rate=0.005, max_short_share=0.50, flag_top_share=0.40, flag_account_age_d=60,
    flag_quiet_gap_h=3, flag_rebate_rate=0.0025, flag_mm_margin=0.04, flag_min_roi=0.02, flag_min_roi_bets=200,
    quiet_gap_min_trades=200, shrink_k=10,
    candidate_depths=(("MONTH", 1000), ("WEEK", 250), ("ALL", 500)),
)


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def iso_date(ts: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def closed_row(asset: str, pnl: float, *, avg_price: float = 0.5, bought: float = 100.0,
               ts: int = NOW - DAY, slug: str = "some-market") -> dict[str, Any]:
    return {
        "asset": asset, "avgPrice": avg_price, "totalBought": bought, "realizedPnl": pnl,
        "curPrice": 1 if pnl > 0 else 0, "timestamp": ts, "slug": slug, "eventSlug": slug,
        "title": slug, "outcome": "Yes",
    }


def position_row(asset: str, *, avg_price: float = 0.5, size: float = 100.0, cur_price: float = 0.0,
                 redeemable: bool = True, end_ts: int = NOW - 10 * DAY, realized: float = 0.0,
                 slug: str = "some-market") -> dict[str, Any]:
    initial = avg_price * size
    current = cur_price * size
    return {
        "asset": asset, "avgPrice": avg_price, "totalBought": size, "size": size,
        "initialValue": initial, "currentValue": current, "cashPnl": current - initial,
        "realizedPnl": realized, "curPrice": cur_price, "redeemable": redeemable,
        "endDate": iso_date(end_ts), "slug": slug, "eventSlug": slug, "title": slug, "outcome": "Yes",
    }


def trade_row(ts: int, *, wallet: str = "0xsharp", slug: str = "some-market", side: str = "BUY",
              price: float = 0.5, size: float = 100.0, asset: str = "a1", tx: str | None = None,
              name: str = "sharp") -> dict[str, Any]:
    return {
        "proxyWallet": wallet, "side": side, "asset": asset, "conditionId": "c1", "price": price,
        "size": size, "usdcSize": price * size, "timestamp": ts, "title": "Some market",
        "outcome": "Yes", "slug": slug, "eventSlug": "some-event",
        "transactionHash": tx or f"0x{ts}{asset}{size}", "name": name, "type": "TRADE",
    }


def stats(**overrides: Any) -> TraderStats:
    """A clean trader that passes every filter with no flags."""
    base: dict[str, Any] = dict(
        wallet="0xsharp", username="sharp", lb_pnl=50_000.0, lb_volume=400_000.0, markets_traded=300,
        last_trade_ts=NOW - DAY, short_share=0.0, quiet_gap_h=6, n=40, wins=26, win_rate=0.65,
        mean_price=0.45, edge=0.16, roi=0.25, pnl=20_000.0, staked=80_000.0, top_share=0.2, median_bet=500.0,
        truncated=False, maker_rebates=0.0, account_age_d=400.0,
    )
    base.update(overrides)
    return TraderStats(**base)


def trade(ts: int, *, wallet: str = "0xsharp", asset: str = "a1", side: str = "BUY", price: float = 0.5,
          size: float = 100.0, tx: str | None = None, slug: str = "some-market") -> Trade:
    return Trade(
        wallet=wallet, side=side, asset=asset, condition_id="c1", price=price, size=size, ts=ts,
        title="Some market", outcome="Yes", slug=slug, event_slug="some-event",
        tx_hash=tx or f"0x{ts}{asset}{size}", name="sharp",
    )


def watched(wallet: str = "0xsharp", *, name: str = "sharp", rank: int | None = 1,
            win_rate: float | None = 0.65, median_bet: float = 100.0, pinned: bool = False) -> WatchedTrader:
    return WatchedTrader(wallet=wallet, name=name, rank=rank, win_rate=win_rate,
                         median_bet=median_bet, pinned=pinned)
