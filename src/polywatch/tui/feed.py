"""Live feed: one row per order, newest first, updated in place as more fills arrive."""
from __future__ import annotations

from typing import Any

from textual.widgets import ListItem, ListView, Static

from ..fmt import feed_text
from ..models import FeedItem, WatchedTrader


class FeedRow(ListItem):
    DEFAULT_CSS = """
    FeedRow { height: auto; padding: 0 1 1 1; }
    """

    def __init__(self, item: FeedItem, trader: WatchedTrader, price: float | None, conviction_multiple: float) -> None:
        rendered = feed_text(item, trader, price, conviction_multiple)
        body = Static(rendered)
        super().__init__(body)
        self._body = body
        self.feed_item = item
        self.trader = trader
        self.price = price
        self.conviction_multiple = conviction_multiple
        self.rendered = rendered

    def redraw(self, price: float | None = None) -> None:
        if price is not None:
            self.price = price
        self.rendered = feed_text(self.feed_item, self.trader, self.price, self.conviction_multiple)
        self._body.update(self.rendered)


class FeedList(ListView):
    MAX_ROWS = 300

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.rows: dict[str, FeedRow] = {}

    def upsert(self, item: FeedItem, trader: WatchedTrader, price: float | None, conviction_multiple: float) -> None:
        row = self.rows.get(item.key)
        if row is not None:
            row.feed_item, row.trader = item, trader
            row.redraw(price)
            return
        row = FeedRow(item, trader, price, conviction_multiple)
        self.rows[item.key] = row
        newer = sum(1 for c in self.children if isinstance(c, FeedRow) and c.feed_item.first_ts > item.first_ts)
        self.insert(newer, [row])
        self._trim()
        if self.index is None:
            self.call_after_refresh(self._select_first)

    def _select_first(self) -> None:
        if self.index is None and self.children:
            self.index = 0

    def _trim(self) -> None:
        while len(self.rows) > self.MAX_ROWS:
            oldest = min(self.rows.values(), key=lambda r: r.feed_item.first_ts)
            del self.rows[oldest.feed_item.key]
            oldest.remove()

    def selected_item(self) -> FeedItem | None:
        child = self.highlighted_child
        return child.feed_item if isinstance(child, FeedRow) else None

    def visible_assets(self, limit: int = 50) -> list[str]:
        assets: list[str] = []
        for child in list(self.children)[:limit]:
            if isinstance(child, FeedRow) and child.feed_item.asset not in assets:
                assets.append(child.feed_item.asset)
        return assets

    def refresh_prices(self, prices: dict[str, float]) -> None:
        for row in self.rows.values():
            price = prices.get(row.feed_item.asset)
            if price is not None and price != row.price:
                row.redraw(price)
