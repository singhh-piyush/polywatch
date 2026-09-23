"""Ranked traders table."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rich.text import Text
from textual.widgets import DataTable

from ..fmt import trader_cells
from ..models import RankedTrader, TraderStats, Verdict

COLUMNS = (("rank", "#"), ("name", "Trader"), ("score", "Score"), ("win", "Win%"), ("edge", "Edge"),
           ("roi", "ROI"), ("pnl", "PnL 90d"), ("n", "Bets"), ("flags", "Flags"))

SORT_KEYS: dict[str, Callable[[RankedTrader], Any]] = {
    "rank": lambda t: (not t.rank, t.rank),
    "name": lambda t: t.stats.username.lower(),
    "score": lambda t: -t.score,
    "win": lambda t: -t.stats.win_rate,
    "edge": lambda t: -t.stats.edge,
    "roi": lambda t: -t.stats.roi,
    "pnl": lambda t: -t.stats.pnl,
    "n": lambda t: -t.stats.n,
    "flags": lambda t: (len(t.verdict.flags), not t.rank, t.rank),
}


class TradersTable(DataTable):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(cursor_type="row", zebra_stripes=True, **kwargs)
        self.sort_column = "rank"
        self._traders: list[RankedTrader] = []
        self._overrides: dict[str, str] = {}
        self._show_flagged = True
        self._order: list[str] = []

    def show(self, traders: list[RankedTrader], overrides: dict[str, str], *, show_flagged: bool = True) -> None:
        self._traders = list(traders)
        self._overrides = dict(overrides)
        self._show_flagged = show_flagged
        self._render_rows()

    def sort_by(self, column: str) -> None:
        self.sort_column = column
        self._render_rows()

    def add_provisional(self, stats: TraderStats, verdict: Verdict) -> None:
        """Show a trader while a scan is still running (no score yet)."""
        if stats.wallet in self._order:
            return
        trader = RankedTrader(stats, verdict)
        self._traders.append(trader)
        self._add(trader)

    def selected_wallet(self) -> str | None:
        return self._order[self.cursor_row] if self._order else None

    def row_wallets(self) -> list[str]:
        return list(self._order)

    def on_mount(self) -> None:
        self._ensure_columns()

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        self.sort_by(str(event.column_key.value))

    def _ensure_columns(self) -> None:
        # Columns need a running app to measure labels, so they're added lazily rather than in __init__.
        if not self.columns:
            for key, label in COLUMNS:
                self.add_column(label, key=key)

    def _visible(self, t: RankedTrader) -> bool:
        return self._show_flagged or not t.verdict.flags or self._overrides.get(t.stats.wallet) == "pin"

    def _add(self, t: RankedTrader) -> None:
        if not self._visible(t):
            return
        self._ensure_columns()
        cells = trader_cells(t, self._overrides.get(t.stats.wallet))
        self.add_row(*(Text(cell) for cell in cells), key=t.stats.wallet)
        self._order.append(t.stats.wallet)

    def _render_rows(self) -> None:
        keep = self.selected_wallet()
        self.clear()
        self._order = []
        for t in sorted(self._traders, key=SORT_KEYS[self.sort_column]):
            self._add(t)
        if keep in self._order:
            self.move_cursor(row=self._order.index(keep))
