"""Pure functions turning raw Data API rows into trader metrics."""
from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from ..markets import is_excluded_market
from ..models import ResolvedBet

# A market that is cancelled or can't be settled (a retirement, a forfeit) resolves 50/50: every outcome pays 50¢.
VOID_PRICE = 0.5


def _f(row: dict[str, Any], key: str) -> float:
    try:
        return float(row.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def parse_ts(value: Any) -> int | None:
    """Unix seconds from '2026-04-21' or '2026-09-23T17:35:00Z' (naive values are UTC)."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp())


def _bet(row: dict[str, Any], pnl: float, ts: int) -> ResolvedBet | None:
    avg, bought = _f(row, "avgPrice"), _f(row, "totalBought")
    if avg <= 0 or bought <= 0:
        return None
    return ResolvedBet(asset=str(row.get("asset") or ""), slug=str(row.get("slug") or ""), avg_price=avg,
                       cost=avg * bought, pnl=pnl, resolved_ts=ts, voided=_f(row, "curPrice") == VOID_PRICE)


def resolved_bets(closed: list[dict[str, Any]], positions: list[dict[str, Any]], since_ts: int) -> list[ResolvedBet]:
    """Every bet resolved inside the window.

    Losing positions are often never redeemed, so they never reach /closed-positions; they sit in
    /positions with redeemable=true and curPrice=0. Both sources are merged by asset. The /positions row
    wins because it carries realized PnL (partial sells) plus the unrealized remainder.
    """
    by_asset: dict[str, ResolvedBet] = {}
    for row in closed:
        ts = int(_f(row, "timestamp"))
        if ts >= since_ts and (bet := _bet(row, _f(row, "realizedPnl"), ts)):
            by_asset[bet.asset] = bet
    for row in positions:
        if not row.get("redeemable"):
            continue
        ts = parse_ts(row.get("endDate"))
        if ts is None or ts < since_ts:
            continue
        if bet := _bet(row, _f(row, "realizedPnl") + _f(row, "cashPnl"), ts):
            by_asset[bet.asset] = bet
    return [b for b in by_asset.values() if not is_excluded_market(b.slug)]


def bet_fields(bets: list[ResolvedBet], shrink_k: int) -> dict[str, Any]:
    """Scoring fields. Voided bets are left out: they pay back 50¢ whatever was predicted."""
    all_bets = bets
    bets = [b for b in all_bets if not b.voided]
    void_share = (len(all_bets) - len(bets)) / len(all_bets) if all_bets else 0.0
    n = len(bets)
    if n == 0:
        return {"n": 0, "wins": 0, "win_rate": 0.0, "mean_price": 0.0, "edge": 0.0, "roi": 0.0, "pnl": 0.0,
                "staked": 0.0, "top_share": 0.0, "median_bet": 0.0, "void_share": void_share}
    wins = sum(1 for b in bets if b.pnl > 0)
    pnl = sum(b.pnl for b in bets)
    cost = sum(b.cost for b in bets)
    win_rate = wins / n
    mean_price = sum(b.avg_price for b in bets) / n
    return {
        "n": n,
        "wins": wins,
        "win_rate": win_rate,
        "mean_price": mean_price,
        # Beat-the-odds edge, shrunk toward zero so a lucky 3-for-3 can't top the list.
        "edge": (win_rate - mean_price) * n / (n + shrink_k),
        "roi": pnl / cost if cost else 0.0,
        "pnl": pnl,
        "staked": cost,
        "top_share": max(b.pnl for b in bets) / pnl if pnl > 0 else 0.0,
        "median_bet": statistics.median(b.cost for b in bets),
        "void_share": void_share,
    }


def activity_fields(trade_rows: list[dict[str, Any]], min_trades_for_gap: int) -> dict[str, Any]:
    stamps = [int(_f(r, "timestamp")) for r in trade_rows]
    short = sum(1 for r in trade_rows if is_excluded_market(str(r.get("slug") or "")))
    return {
        "last_trade_ts": max(stamps, default=0),
        "short_share": short / len(trade_rows) if trade_rows else 0.0,
        "quiet_gap_h": longest_quiet_gap(stamps) if len(stamps) >= min_trades_for_gap else None,
    }


def fast_share(trade_rows: list[dict[str, Any]], *, snipe_price: float, flip_s: int, min_buys: int) -> float | None:
    """Share of recent buys that a person copying by hand could not follow, or None with under min_buys buys.

    Counted: buys at snipe_price or more (the outcome is all but settled, leaving a few cents), buys sold again within
    flip_s (scalps, and snipes on news like a retirement), and buys with the other outcome of the same market bought
    within flip_s (arbitrage). A high win rate on its own is not counted: a patient favourite-backer is copyable.
    """
    buys = [r for r in trade_rows if r.get("side") == "BUY"]
    if len(buys) < max(min_buys, 1):
        return None
    sells: dict[str, list[int]] = defaultdict(list)
    market_buys: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for r in trade_rows:
        ts, asset = int(_f(r, "timestamp")), str(r.get("asset") or "")
        if r.get("side") == "SELL":
            sells[asset].append(ts)
        else:
            market_buys[str(r.get("conditionId") or "")].append((ts, asset))

    def too_fast(buy: dict[str, Any]) -> bool:
        ts, asset = int(_f(buy, "timestamp")), str(buy.get("asset") or "")
        return (_f(buy, "price") >= snipe_price
                or any(0 <= sold - ts <= flip_s for sold in sells[asset])
                or any(other != asset and abs(t - ts) <= flip_s
                       for t, other in market_buys[str(buy.get("conditionId") or "")]))

    return sum(1 for b in buys if too_fast(b)) / len(buys)


def rebate_total(rows: list[dict[str, Any]]) -> float:
    return sum(_f(r, "usdcSize") for r in rows)


def longest_quiet_gap(timestamps: list[int], quiet_fraction: float = 0.01) -> int:
    """Longest run of consecutive UTC hours (wrapping midnight) holding at most `quiet_fraction` of trades.

    People sleep, so their history has a multi-hour quiet stretch. Bots often don't.
    """
    hist = [0] * 24
    for ts in timestamps:
        hist[(ts // 3600) % 24] += 1
    threshold = len(timestamps) * quiet_fraction
    quiet = [count <= threshold for count in hist]
    if all(quiet):
        return 24
    best = run = 0
    for is_quiet in quiet + quiet:
        run = run + 1 if is_quiet else 0
        best = max(best, run)
    return best
