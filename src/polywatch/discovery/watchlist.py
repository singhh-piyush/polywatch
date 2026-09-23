"""Who the live feed follows: top unflagged traders, plus pins, minus bans."""
from __future__ import annotations

from ..fmt import short_wallet
from ..models import RankedTrader, WatchedTrader


def select_watchlist(ranked: list[RankedTrader], size: int, pins: set[str], bans: set[str]) -> list[str]:
    auto = [t.stats.wallet for t in sorted(ranked, key=lambda t: t.rank)
            if not t.verdict.flags and t.stats.wallet not in bans][:size]
    extra = sorted(w for w in pins if w not in auto and w not in bans)
    return auto + extra


def build_watchlist(traders: list[RankedTrader], size: int, overrides: dict[str, str],
                    names: dict[str, str]) -> dict[str, WatchedTrader]:
    pins = {w for w, mode in overrides.items() if mode == "pin"}
    bans = {w for w, mode in overrides.items() if mode == "ban"}
    by_wallet = {t.stats.wallet: t for t in traders}
    eligible = [t for t in traders if t.verdict.eligible]
    watched: dict[str, WatchedTrader] = {}
    for wallet in select_watchlist(eligible, size, pins, bans):
        t = by_wallet.get(wallet)
        name = names.get(wallet) or (t.stats.username if t else "") or short_wallet(wallet)
        watched[wallet] = WatchedTrader(
            wallet=wallet,
            name=name,
            rank=(t.rank or None) if t else None,
            win_rate=t.stats.win_rate if t and t.stats.n else None,
            median_bet=t.stats.median_bet if t else 0.0,
            pinned=wallet in pins,
        )
    return watched
