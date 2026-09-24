"""The short-market leaderboard: profit, consistency and bot badges per wallet, from the indexed windows."""
from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass

from ..config import Settings
from ..discovery.scoring import percentile_ranks

PERIODS = {"24h": 86_400, "7d": 7 * 86_400, "30d": 30 * 86_400}
BOT_BADGES = frozenset({"ARB", "SNIPE", "LAST-SEC", "HFT"})  # MAKER is shown but doesn't count as a bot
BADGE_HELP = {
    "ARB": "often buys both Up and Down in the same window",
    "SNIPE": "most of its money goes in at 95¢ or more",
    "LAST-SEC": "usually still buying in the last seconds of a window",
    "HFT": "dozens of fills per window, like an automated strategy",
    "MAKER": "often sells pairs it minted, like a market maker",
}


@dataclass(slots=True)
class BoardRow:
    wallet: str
    name: str
    windows: int
    pnl: float
    cost: float
    volume: float
    wins: int
    directional: int      # windows where it bought a side
    win_rate: float
    avg_price: float      # average price it paid for its side
    edge: float           # win rate minus the price paid, shrunk toward 0 for few windows
    roi: float
    median_bet: float
    badges: tuple[str, ...]
    consistency: float = 0.0  # percentile of edge among ranked traders, 0-100
    rank: int = 0

    @property
    def is_bot(self) -> bool:
        return any(b in BOT_BADGES for b in self.badges)

    def to_json(self) -> dict[str, object]:
        data = asdict(self)
        data["is_bot"] = self.is_bot
        return data


def _where(since_ts: int, until_ts: int | None, coins: tuple[str, ...],
           intervals: tuple[str, ...]) -> tuple[str, list[object]]:
    clauses, args = ["end_ts >= ?"], [since_ts]
    if until_ts is not None:
        clauses.append("end_ts < ?")
        args.append(until_ts)
    if coins:
        clauses.append(f"coin IN ({','.join('?' * len(coins))})")
        args += coins
    if intervals:
        clauses.append(f"interval IN ({','.join('?' * len(intervals))})")
        args += intervals
    return " AND ".join(clauses), args


def board(db: sqlite3.Connection, cfg: Settings, *, since_ts: int, until_ts: int | None = None,
          coins: tuple[str, ...] = (), intervals: tuple[str, ...] = (), min_windows: int | None = None) -> list[BoardRow]:
    """Every wallet with at least min_windows windows, ranked by profit, with consistency as a percentile."""
    min_windows = cfg.short_min_windows if min_windows is None else min_windows
    where, args = _where(since_ts, until_ts, coins, intervals)
    rows = db.execute(f"""
        SELECT w.wallet, w.name, COUNT(*), SUM(pnl), SUM(cost), SUM(buy_usd),
               SUM(COALESCE(won, 0)), SUM(side IS NOT NULL), SUM(COALESCE(side_price, 0)),
               SUM(both_sides), SUM(snipe_usd), SUM(minted > 0), SUM(fills),
               SUM(last_buy_s IS NOT NULL AND last_buy_s <= ?)
        FROM short_results r JOIN short_wallets w ON w.id = r.wallet_id
        WHERE {where}
        GROUP BY r.wallet_id HAVING COUNT(*) >= ?""", [cfg.badge_last_sec_s, *args, min_windows]).fetchall()
    medians = _median_bets(db, where, args, {r[0] for r in rows})
    out = []
    for (wallet, name, windows, pnl, cost, volume, wins, directional, price_sum, both, snipe, minted, fills,
         late) in rows:
        win_rate = wins / directional if directional else 0.0
        avg_price = price_sum / directional if directional else 0.0
        edge = (win_rate - avg_price) * directional / (directional + cfg.shrink_k)
        badges = []
        if both / windows >= cfg.badge_arb_share:
            badges.append("ARB")
        if volume and snipe / volume >= cfg.badge_snipe_share:
            badges.append("SNIPE")
        if directional and late / directional >= cfg.badge_last_sec_share:
            badges.append("LAST-SEC")
        if fills / windows > cfg.badge_hft_fills:
            badges.append("HFT")
        if minted / windows >= cfg.badge_maker_share:
            badges.append("MAKER")
        out.append(BoardRow(wallet=wallet, name=name, windows=windows, pnl=pnl, cost=cost, volume=volume, wins=wins,
                            directional=directional, win_rate=win_rate, avg_price=avg_price, edge=edge,
                            roi=pnl / cost if cost else 0.0, median_bet=medians.get(wallet, 0.0),
                            badges=tuple(badges)))
    for row, pct in zip(out, percentile_ranks([r.edge for r in out])):
        row.consistency = round(100 * pct, 1)
    out.sort(key=lambda r: r.pnl, reverse=True)
    for i, row in enumerate(out, start=1):
        row.rank = i
    return out


def _median_bets(db: sqlite3.Connection, where: str, args: list[object], wallets: set[str]) -> dict[str, float]:
    """Each wallet's median dollars in per window, for sizing its live bets."""
    per: dict[str, list[float]] = {}
    for wallet, usd in db.execute(f"SELECT w.wallet, r.buy_usd FROM short_results r JOIN short_wallets w "
                                  f"ON w.id = r.wallet_id WHERE {where} AND r.buy_usd > 0", args):
        if wallet in wallets:
            per.setdefault(wallet, []).append(usd)
    out = {}
    for wallet, values in per.items():
        values.sort()
        mid = len(values) // 2
        out[wallet] = values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2
    return out
