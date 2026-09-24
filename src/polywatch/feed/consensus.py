"""Common trades: outcomes that several tracked traders bought recently."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from ..models import CommonBet, FeedItem


def common_bets(items: Iterable[FeedItem], now: float, *, window_s: int = 86_400,
                min_traders: int = 2) -> list[CommonBet]:
    """Outcomes at least min_traders tracked traders bought within window_s; most traders first, then most recent.

    Dimmed buys (95c+, both sides) don't count: they aren't worth copying.
    """
    recent = sorted((i for i in items if i.side == "BUY" and not i.fast and i.last_ts >= now - window_s),
                    key=lambda i: i.first_ts)
    by_asset: dict[str, list[FeedItem]] = defaultdict(list)
    buyers: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))  # market -> asset -> wallets
    for i in recent:
        by_asset[i.asset].append(i)
        if i.condition_id:
            buyers[i.condition_id][i.asset].add(i.wallet)

    bets = []
    for asset, group in by_asset.items():
        names = {i.wallet: i.name for i in reversed(group)}  # first name seen for each wallet
        wallets = tuple(dict.fromkeys(i.wallet for i in group))
        if len(wallets) < min_traders:
            continue
        first = group[0]
        others: set[str] = set()
        for other_asset, other_wallets in buyers[first.condition_id].items() if first.condition_id else ():
            if other_asset != asset:
                others |= other_wallets
        bets.append(CommonBet(
            asset=asset, condition_id=first.condition_id, title=first.title, outcome=first.outcome, slug=first.slug,
            event_slug=first.event_slug, wallets=wallets, names=tuple(names[w] for w in wallets),
            usd=sum(i.usd for i in group), shares=sum(i.shares for i in group),
            last_ts=max(i.last_ts for i in group), against=len(others - set(wallets)),
        ))
    bets.sort(key=lambda b: (-len(b.wallets), -b.last_ts))
    return bets
