"""Common trades: outcomes that several tracked traders bought recently."""
from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.widgets import ListItem, ListView, Static

from ..models import CommonBet


class CommonRow(ListItem):
    DEFAULT_CSS = """
    CommonRow { height: auto; padding: 0 1; }
    """

    def __init__(self, bet: CommonBet, text: Text) -> None:
        self._body = Static(text)
        super().__init__(self._body)
        self.bet = bet
        self.rendered = text

    def set(self, bet: CommonBet, text: Text) -> None:
        self.bet, self.rendered = bet, text
        self._body.update(text)


class CommonList(ListView):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.border_title = "Common trades"
        self.border_subtitle = "none in the last 24 h"
        self._shown: list[tuple[str, str]] = []

    def show(self, rows: list[tuple[CommonBet, Text]]) -> None:
        """Replace the rows, keeping the same outcome selected. Skipped when nothing changed."""
        shown = [(bet.asset, text.plain) for bet, text in rows]
        if shown == self._shown:
            return
        self._shown = shown
        keep = self.selected_bet()
        # Update rows in place rather than clearing: removal is asynchronous, so a rebuilt list briefly holds both.
        existing = [child for child in self.children if isinstance(child, CommonRow)]
        for row, (bet, text) in zip(existing, rows, strict=False):
            row.set(bet, text)
        if len(rows) > len(existing):
            self.extend([CommonRow(bet, text) for bet, text in rows[len(existing):]])
        for row in existing[len(rows):]:
            row.remove()
        self.border_subtitle = str(len(rows)) if rows else "none in the last 24 h"
        if rows:
            self.index = next((i for i, (bet, _) in enumerate(rows) if keep and bet.asset == keep.asset), 0)
        else:
            self.index = None

    def bets(self) -> list[CommonBet]:
        return [child.bet for child in self.children if isinstance(child, CommonRow)]

    def selected_bet(self) -> CommonBet | None:
        child = self.highlighted_child
        return child.bet if isinstance(child, CommonRow) else None
