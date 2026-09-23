"""Turns individual fills into one feed item per order."""
from __future__ import annotations

from collections import defaultdict, deque

from ..config import Settings
from ..fmt import cents
from ..markets import is_excluded_market
from ..models import FeedItem, Trade, WatchedTrader

MAX_ITEMS_PER_GROUP = 20


class Aggregator:
    """One order often fills in many pieces within seconds; this merges them into a single FeedItem."""

    def __init__(self, cfg: Settings, watched: dict[str, WatchedTrader] | None = None, max_seen: int = 50_000) -> None:
        self.cfg = cfg
        self.watched = watched or {}
        self._groups: dict[tuple[str, str, str], list[FeedItem]] = defaultdict(list)
        self._market_buys: dict[tuple[str, str], list[FeedItem]] = defaultdict(list)
        self._changed: list[FeedItem] = []
        self._seen: set[tuple[str, str, str, str, float, float]] = set()
        self._seen_order: deque[tuple[str, str, str, str, float, float]] = deque()
        self._max_seen = max_seen

    def set_watched(self, watched: dict[str, WatchedTrader]) -> None:
        self.watched = watched

    def add(self, trade: Trade) -> FeedItem | None:
        trader = self.watched.get(trade.wallet)
        if trader is None or is_excluded_market(trade.slug):
            return None
        if not self._first_sighting(trade.dedupe_key):
            return None  # the websocket and the /activity poller both report taker fills
        item = self._find_or_open(trade, trader)
        item.shares += trade.size
        item.usd += trade.usd
        item.fills += 1
        item.first_ts = min(item.first_ts, trade.ts)
        item.last_ts = max(item.last_ts, trade.ts)
        if trade.tx_hash and trade.tx_hash not in item.tx_hashes:
            item.tx_hashes.append(trade.tx_hash)
        # Conviction is about new money going in; a big exit is not a bet.
        item.conviction = item.usd / trader.median_bet if trade.side == "BUY" and trader.median_bet > 0 else None
        if trade.side == "BUY":
            self._mark_fast(item, trade.condition_id)
        return item if item.usd >= self.cfg.feed_min_usd else None

    def take_changed(self) -> list[FeedItem]:
        """Earlier items changed by the last add (the first leg of a both-sides trade), to redraw."""
        changed, self._changed = self._changed, []
        return changed

    def _mark_fast(self, item: FeedItem, condition_id: str) -> None:
        """Mark buys a hand-copier gains nothing from: near-certain outcomes, and either leg of a both-sides trade."""
        window = self.cfg.flip_window_s
        buys = self._market_buys[(item.wallet, condition_id)]
        if item not in buys:
            buys.append(item)
            if len(buys) > MAX_ITEMS_PER_GROUP:
                del buys[0]
        for other in buys:
            near = other.first_ts - window <= item.last_ts and item.first_ts <= other.last_ts + window
            if other.asset != item.asset and near:
                item.fast = "both sides"
                if other.fast != "both sides":
                    other.fast = "both sides"
                    self._changed.append(other)
        if item.fast != "both sides":
            item.fast = f"{cents(self.cfg.snipe_price)}+" if item.avg_price >= self.cfg.snipe_price else None

    def _first_sighting(self, key: tuple[str, str, str, str, float, float]) -> bool:
        if key in self._seen:
            return False
        self._seen.add(key)
        self._seen_order.append(key)
        if len(self._seen_order) > self._max_seen:
            self._seen.discard(self._seen_order.popleft())
        return True

    def _find_or_open(self, trade: Trade, trader: WatchedTrader) -> FeedItem:
        group = self._groups[(trade.wallet, trade.asset, trade.side)]
        gap, span = self.cfg.merge_gap_s, self.cfg.merge_max_s
        for item in group:
            within_gap = item.first_ts - gap <= trade.ts <= item.last_ts + gap
            within_span = max(item.last_ts, trade.ts) - min(item.first_ts, trade.ts) <= span
            if within_gap and within_span:
                return item
        item = FeedItem(
            key=f"{trade.wallet}:{trade.asset}:{trade.side}:{trade.ts}",
            wallet=trade.wallet, name=trader.name, side=trade.side, asset=trade.asset, title=trade.title,
            outcome=trade.outcome, slug=trade.slug, event_slug=trade.event_slug,
            first_ts=trade.ts, last_ts=trade.ts,
        )
        group.append(item)
        if len(group) > MAX_ITEMS_PER_GROUP:
            del group[0]
        return item


def is_alert_worthy(item: FeedItem, trader: WatchedTrader, cfg: Settings) -> bool:
    if item.fast:
        return False
    high_conviction = item.conviction is not None and item.conviction >= cfg.conviction_multiple
    return trader.pinned or high_conviction
