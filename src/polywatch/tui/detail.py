"""Trader detail: why a trader ranks where they do, plus what they hold right now."""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from ..config import Settings
from ..discovery.filters import describe_flag
from ..fmt import ago, cents, edge_pp, pct, short_wallet, usd, usd_compact
from ..markets import profile_url
from ..models import RankedTrader


def detail_text(trader: RankedTrader, cfg: Settings, now: float | None = None) -> Text:
    s, v = trader.stats, trader.verdict
    now = time.time() if now is None else now
    text = Text()
    text.append(s.username or short_wallet(s.wallet), style="bold")
    text.append(f"  {s.wallet}\n{profile_url(s.wallet)}\n\n", style="dim")
    if v.eligible:
        text.append(f"Rank #{trader.rank} · score {trader.score:.0f}/100\n\n")
    else:
        text.append(f"Not ranked: {v.excluded}\n\n")
    age = f"{s.account_age_d:.0f}d" if s.account_age_d is not None else "unknown"
    last = f"{ago(now - s.last_trade_ts)} ago" if s.last_trade_ts else "unknown"
    for label, value in (
        (f"Resolved bets ({cfg.window_days}d)", f"{s.n} ({s.wins} won)"),
        ("Win rate", pct(s.win_rate)),
        ("Avg odds paid", cents(s.mean_price)),
        ("Edge vs odds", edge_pp(s.edge)),
        ("ROI", pct(s.roi)),
        (f"PnL ({cfg.window_days}d)", usd_compact(s.pnl)),
        (f"Staked ({cfg.window_days}d)", usd_compact(s.staked)),
        ("Typical bet", usd(s.median_bet)),
        ("Biggest win share", pct(s.top_share)),
        ("Too fast to copy", pct(s.fast_share) if s.fast_share is not None else "not enough buys"),
        ("Voided bets", pct(s.void_share)),
        ("Account age", age),
        ("Last trade", last),
    ):
        text.append(f"{label:<22}{value}\n")
    if v.flags:
        text.append("\nFlags\n", style="bold")
        for flag in v.flags:
            text.append(f"  {flag}: {describe_flag(flag, s, cfg)}\n")
    return text


def positions_text(rows: list[dict[str, Any]], limit: int = 10) -> Text:
    def num(row: dict[str, Any], key: str) -> float:
        return float(row.get(key) or 0)

    open_rows = [r for r in rows if not r.get("redeemable") and num(r, "currentValue") > 0]
    open_rows.sort(key=lambda r: num(r, "currentValue"), reverse=True)
    text = Text("Open positions\n", style="bold")
    if not open_rows:
        text.append("none")
        return text
    for r in open_rows[:limit]:
        pnl = num(r, "cashPnl")
        text.append(f"{r.get('title', '')} — {r.get('outcome', '')}\n")
        text.append(f"   {cents(num(r, 'avgPrice'))} → {cents(num(r, 'curPrice'))}   {usd(num(r, 'currentValue'))} "
                    f"({'+' if pnl >= 0 else ''}{usd(pnl)})\n", style="dim")
    if len(open_rows) > limit:
        text.append(f"… and {len(open_rows) - limit} more", style="dim")
    return text


class TraderDetail(ModalScreen[None]):
    DEFAULT_CSS = """
    TraderDetail { align: center middle; }
    #detail { width: 90; height: auto; max-height: 90%; border: thick $primary; background: $surface; padding: 1 2; }
    """
    BINDINGS = [Binding("escape", "dismiss", "Close"), Binding("o", "open_profile", "Open profile")]

    def __init__(self, trader: RankedTrader, cfg: Settings, data: Any, opener: Callable[[str], Any]) -> None:
        super().__init__()
        self.trader = trader
        self._cfg = cfg
        self._data = data
        self._opener = opener

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="detail"):
            yield Static(detail_text(self.trader, self._cfg), id="summary")
            yield Static("Loading open positions…", id="positions")

    def on_mount(self) -> None:
        self.load_positions()

    @work(exclusive=True, exit_on_error=False)
    async def load_positions(self) -> None:
        target = self.query_one("#positions", Static)
        try:
            rows = await self._data.positions(self.trader.stats.wallet, max_pages=2)
        except Exception as exc:
            target.update(Text(f"Couldn't load open positions: {exc}"))
            return
        target.update(positions_text(rows))

    def action_open_profile(self) -> None:
        self._opener(profile_url(self.trader.stats.wallet))
