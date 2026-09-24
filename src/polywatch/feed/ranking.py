"""How worth copying each buy is right now: who made it, how big it is, who agrees, its age and the price since."""
from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable

from ..models import FeedItem, WatchedTrader

HALF_LIFE_S = 6 * 3600  # a bet counts half as much every 6 hours
MAX_CROWD = 3           # other traders beyond this many add nothing more
DIP_CAP = 1.15          # a cheaper price than the trader paid helps, but only a little
UNSCORED_QUALITY = 0.5  # a pinned trader with no scan score yet
W_QUALITY, W_SIZE, W_CROWD = 0.50, 0.25, 0.25


def upside_kept(entry: float, now: float | None) -> float:
    """The share of the trader's upside still on offer at today's price: 1 unchanged, less once the price has run."""
    if now is None or not 0 < entry < 1:
        return 1.0
    if now >= 1:
        return 0.0
    if now < entry / 2:
        return 0.5  # the market has turned hard against the bet
    kept = ((1 - now) / now) / ((1 - entry) / entry)
    return min(kept, DIP_CAP)


def copy_score(item: FeedItem, trader: WatchedTrader, *, now: float, price: float | None, others: int = 0,
               against: int = 0) -> int:
    """0-100. `others` tracked traders bought the same outcome; `against` bought another outcome of the market."""
    if item.fast:
        return 0
    quality = trader.score / 100 if trader.score is not None else UNSCORED_QUALITY
    size = min(max(math.log10(item.conviction), 0.0), 1.0) if item.conviction else 0.0
    crowd = max(min(others - against, MAX_CROWD), -MAX_CROWD) / MAX_CROWD
    base = W_QUALITY * quality + W_SIZE * size + W_CROWD * crowd
    freshness = 0.5 ** (max(now - item.last_ts, 0) / HALF_LIFE_S)
    score = 100 * max(base, 0.0) * freshness * upside_kept(item.avg_price, price)
    return round(min(score, 100.0))


def copy_scores(items: Iterable[FeedItem], context: Iterable[FeedItem], watched: dict[str, WatchedTrader],
                prices: dict[str, float], now: float, *, window_s: int = 86_400) -> dict[str, int]:
    """Scores for the buys in `items`, counting agreement among the recent, copyable buys in `context`."""
    buyers: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))  # market -> asset -> wallets
    for i in context:
        if i.side == "BUY" and not i.fast and i.last_ts >= now - window_s:
            buyers[i.condition_id or i.asset][i.asset].add(i.wallet)
    scores: dict[str, int] = {}
    for item in items:
        trader = watched.get(item.wallet)
        if item.side != "BUY" or trader is None:
            continue
        market = buyers.get(item.condition_id or item.asset, {})
        others = market.get(item.asset, set()) - {item.wallet}
        against: set[str] = set()
        for asset, wallets in market.items():
            if asset != item.asset:
                against |= wallets
        against -= {item.wallet}
        scores[item.key] = copy_score(item, trader, now=now, price=prices.get(item.asset), others=len(others),
                                      against=len(against))
    return scores
