"""Text lists on the right: common trades, and your own positions."""
from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.widgets import ListItem, ListView, Static

from ..models import CommonBet


class TextRow(ListItem):
    DEFAULT_CSS = """
    TextRow { height: auto; padding: 0 1; }
    """

    def __init__(self, value: Any, text: Text) -> None:
        self._body = Static(text)
        super().__init__(self._body)
        self.value = value  # has .asset, .slug and .event_slug
        self.rendered = text

    def set(self, value: Any, text: Text) -> None:
        self.value, self.rendered = value, text
        self._body.update(text)


class TextList(ListView):
    def __init__(self, title: str, *, empty: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.border_title = title
        self.empty = empty
        self.border_subtitle = empty
        self._shown: list[tuple[str, str]] = []

    def show(self, rows: list[tuple[Any, Text]], subtitle: str | None = None) -> None:
        """Replace the rows, keeping the same outcome selected. Skipped when nothing changed."""
        self.border_subtitle = subtitle if subtitle is not None else str(len(rows)) if rows else self.empty
        shown = [(value.asset, text.plain) for value, text in rows]
        if shown == self._shown:
            return
        self._shown = shown
        keep = self.selected()
        # Update rows in place rather than clearing: removal is asynchronous, so a rebuilt list briefly holds both.
        existing = [child for child in self.children if isinstance(child, TextRow)]
        for row, (value, text) in zip(existing, rows, strict=False):
            row.set(value, text)
        if len(rows) > len(existing):
            self.extend([TextRow(value, text) for value, text in rows[len(existing):]])
        for row in existing[len(rows):]:
            row.remove()
        if rows:
            self.index = next((i for i, (value, _) in enumerate(rows) if keep and value.asset == keep.asset), 0)
        else:
            self.index = None

    def values(self) -> list[Any]:
        return [child.value for child in self.children if isinstance(child, TextRow)]

    def selected(self) -> Any | None:
        child = self.highlighted_child
        return child.value if isinstance(child, TextRow) else None


class CommonList(TextList):
    """Outcomes that several tracked traders bought recently."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("Common trades", empty="none in the last 24 h", **kwargs)

    def bets(self) -> list[CommonBet]:
        return self.values()

    def selected_bet(self) -> CommonBet | None:
        return self.selected()
