"""Live feed panes: one row per order, best to copy or newest first, following the top unless you're browsing."""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from rich.text import Text
from textual import events
from textual.binding import Binding
from textual.widgets import ListItem, ListView, Static

from ..fmt import feed_text
from ..models import FeedItem, Holding, WatchedTrader


class FeedRow(ListItem):
    DEFAULT_CSS = """
    FeedRow { height: auto; padding: 0 1 1 1; }
    """

    def __init__(self, item: FeedItem, trader: WatchedTrader, price: float | None, conviction_multiple: float, *,
                 resolves: str = "", holding: Holding | None = None, copy_score: int | None = None) -> None:
        self.feed_item = item
        self.trader = trader
        self.price = price
        self.conviction_multiple = conviction_multiple
        self.resolves = resolves
        self.holding = holding
        self.copy_score = copy_score
        self.rendered = self._text()
        self._body = Static(self.rendered)
        super().__init__(self._body)

    def _text(self) -> Text:
        return feed_text(self.feed_item, self.trader, self.price, self.conviction_multiple,
                         resolves=self.resolves, holding=self.holding, copy_score=self.copy_score)

    def redraw(self, price: float | None = None) -> None:
        if price is not None:
            self.price = price
        self.rendered = self._text()
        self._body.update(self.rendered)


class FeedList(ListView):
    MAX_ROWS = 300
    RESUME_AFTER_S = 15.0
    BINDINGS = [Binding("home", "follow", "Follow newest", show=False)]

    def __init__(self, title: str = "", *, by_score: bool = False, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.rows: dict[str, FeedRow] = {}
        self.by_score = by_score  # best to copy first; otherwise newest first
        self.label = title
        self.following = True
        self.paused_at: float | None = None
        self._show_title()

    # --- following -------------------------------------------------------------------------------

    def set_title(self, title: str) -> None:
        self.label = title
        self._show_title()

    def _show_title(self) -> None:
        if self.label:
            self.border_title = f"{self.label} · {'following' if self.following else 'paused'}"

    def pause(self, now: float | None = None) -> None:
        """Stop jumping to new rows while you look around; tick() resumes after RESUME_AFTER_S idle."""
        self.following = False
        self.paused_at = time.monotonic() if now is None else now
        self._show_title()

    def resume(self) -> None:
        self.following = True
        self.paused_at = None
        self._show_title()
        self.resort()

    def tick(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        if not self.following and self.paused_at is not None and now - self.paused_at >= self.RESUME_AFTER_S:
            self.resume()

    def action_follow(self) -> None:
        self.resume()

    def action_cursor_up(self) -> None:
        self.pause()
        super().action_cursor_up()

    def action_cursor_down(self) -> None:
        self.pause()
        super().action_cursor_down()

    def on_mouse_scroll_up(self, _event: events.MouseScrollUp) -> None:
        self.pause()

    def on_mouse_scroll_down(self, _event: events.MouseScrollDown) -> None:
        self.pause()

    # --- order -----------------------------------------------------------------------------------

    def set_order(self, by_score: bool) -> None:
        self.by_score = by_score
        self.resume()

    def _sort_key(self, row: FeedRow) -> tuple[Any, ...]:
        item = row.feed_item
        if self.by_score:
            return (item.fast is not None, -(row.copy_score or 0), -item.first_ts)
        return (-item.first_ts,)

    def set_scores(self, scores: dict[str, int]) -> None:
        for key, score in scores.items():
            row = self.rows.get(key)
            if row is not None and row.copy_score != score:
                row.copy_score = score
                row.redraw()
        self.resort()

    def resort(self) -> None:
        """Put the rows in order and select the top. Only while following, so rows never move under you."""
        if not self.following:
            return
        rows = [c for c in self.children if isinstance(c, FeedRow)]
        for i, row in enumerate(sorted(rows, key=self._sort_key)):
            if self._nodes[i] is not row:
                self.move_child(row, before=i)
        if rows:
            self.index = 0
            self.scroll_home(animate=False)
        self._sync_highlight()

    def _sync_highlight(self) -> None:
        # ListView only moves the highlight when the index changes, not when rows move around it.
        for i, child in enumerate(self._nodes):
            if isinstance(child, ListItem):
                child.highlighted = i == self.index

    # --- rows ------------------------------------------------------------------------------------

    def upsert(self, item: FeedItem, trader: WatchedTrader, price: float | None, conviction_multiple: float, *,
               resolves: str = "", holding: Holding | None = None, score: int | None = None) -> None:
        row = self.rows.get(item.key)
        if row is not None:
            row.feed_item, row.trader = item, trader
            row.resolves, row.holding = resolves, holding
            if score is not None:
                row.copy_score = score
            row.redraw(price)
            return
        row = FeedRow(item, trader, price, conviction_multiple, resolves=resolves, holding=holding, copy_score=score)
        self.rows[item.key] = row
        key = self._sort_key(row)
        above = sum(1 for c in self.children if isinstance(c, FeedRow) and self._sort_key(c) < key)
        self.insert(above, [row])
        self._trim()
        if self.index is None:
            self.call_after_refresh(self._select_first)
        elif self.following:
            if above == 0:
                self.index = 0
                self.scroll_home(animate=False)
        elif above <= self.index:
            self.index += 1  # keep the row you're looking at selected
        self._sync_highlight()

    def _select_first(self) -> None:
        if self.index is None and self.children:
            self.index = 0

    def _trim(self) -> None:
        while len(self.rows) > self.MAX_ROWS:
            oldest = min(self.rows.values(), key=lambda r: r.feed_item.first_ts)
            del self.rows[oldest.feed_item.key]
            oldest.remove()

    def remove_where(self, doomed: Callable[[FeedItem], bool]) -> int:
        rows = [row for row in self.rows.values() if doomed(row.feed_item)]
        for row in rows:
            del self.rows[row.feed_item.key]
            row.remove()
        if rows:
            self.call_after_refresh(self._fix_index)
        return len(rows)

    def _fix_index(self) -> None:
        count = len(self.children)
        if count == 0:
            self.index = None
        elif self.index is None or self.index >= count:
            self.index = 0 if self.following else count - 1
        self._sync_highlight()

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
