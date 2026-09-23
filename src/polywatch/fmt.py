"""Pure text formatting shared by the CLI, the TUI and desktop alerts."""
from __future__ import annotations

import time

from rich.text import Text

from .markets import market_url
from .models import FeedItem, RankedTrader, WatchedTrader

INDENT = " " * 10


def cents(price: float) -> str:
    c = price * 100
    return f"{c:.0f}¢" if 1 <= c <= 99 else f"{c:.1f}¢"


def payout(price: float) -> str:
    return f"{1 / price:.2f}x" if price > 0 else "—"


def delta_cents(entry: float, now: float) -> str:
    # Difference of the rounded prices, so "@ 90¢ … now 92¢" reads "+2¢", never "+3¢".
    d = round(now * 100) - round(entry * 100)
    return "±0¢" if d == 0 else f"{d:+d}¢"


def usd(amount: float) -> str:
    return f"-${abs(amount):,.0f}" if amount < 0 else f"${amount:,.0f}"


def usd_compact(amount: float) -> str:
    sign = "-" if amount < 0 else ""
    a = abs(amount)
    if a >= 1_000_000:
        body = f"{a / 1_000_000:.1f}M"
    elif a >= 10_000:
        body = f"{a / 1_000:.0f}k"
    else:
        body = f"{a:,.0f}"
    return f"{sign}${body}"


def pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def edge_pp(x: float) -> str:
    return f"{x * 100:+.1f}pp"


def short_wallet(wallet: str) -> str:
    return f"{wallet[:6]}…{wallet[-4:]}" if len(wallet) > 12 else wallet


def ago(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def short_url(url: str, width: int = 60) -> str:
    bare = url.removeprefix("https://")
    return bare if len(bare) <= width else bare[: width - 1] + "…"


def feed_text(item: FeedItem, trader: WatchedTrader, now_price: float | None, conviction_multiple: float) -> Text:
    text = Text()
    text.append(time.strftime("%H:%M:%S", time.localtime(item.last_ts)), style="dim")
    text.append("  ")
    text.append(f"{item.side:<4}", style="bold green" if item.side == "BUY" else "bold red")
    text.append(" ")
    text.append(trader.name or short_wallet(item.wallet), style="bold")
    meta = []
    if trader.rank:
        meta.append(f"#{trader.rank}")
    if trader.win_rate is not None:
        meta.append(f"{pct(trader.win_rate)} win")
    if trader.pinned:
        meta.append("★")
    if meta:
        text.append("  " + " · ".join(meta), style="dim")
    text.append(f"\n{INDENT}{item.title} — {item.outcome}")
    text.append(f"\n{INDENT}")
    if item.side == "BUY":
        text.append(f"@ {cents(item.avg_price)} → pays {payout(item.avg_price)}", style="bold")
    else:
        text.append(f"@ {cents(item.avg_price)} (exit)", style="bold")
    text.append(f"   {usd(item.usd)}")
    if item.conviction is not None and item.conviction >= conviction_multiple:
        text.append(f" 🔥{item.conviction:.1f}x", style="bold yellow")
    if now_price is not None:
        text.append(f"   now {cents(now_price)} ({delta_cents(item.avg_price, now_price)})", style="dim")
    text.append(f"\n{INDENT}")
    text.append(short_url(market_url(item.event_slug, item.slug)), style="dim underline")
    return text


def trader_cells(t: RankedTrader, mode: str | None) -> tuple[str, ...]:
    """Cells for the traders table: rank, name, score, win%, edge, ROI, PnL, bets, flags."""
    s, v = t.stats, t.verdict
    marker = {"pin": "★ ", "ban": "✕ "}.get(mode or "", "")
    rank = f"{marker}{t.rank}" if t.rank else f"{marker}—"
    name = s.username or short_wallet(s.wallet)
    if not v.eligible:
        return (rank, name, "—", "—", "—", "—", "—", "—", f"excluded: {v.excluded}")
    score = f"{t.score:.0f}" if t.rank else "…"
    return (rank, name, score, pct(s.win_rate), edge_pp(s.edge), pct(s.roi), usd_compact(s.pnl), str(s.n),
            " ".join(v.flags))


def alert_text(item: FeedItem, trader: WatchedTrader, conviction_multiple: float) -> tuple[str, str]:
    title = f"{trader.name}: {item.side} {item.outcome} @ {cents(item.avg_price)}"
    body = f"{item.title}\n{usd(item.usd)}"
    if item.conviction is not None and item.conviction >= conviction_multiple:
        body += f" · 🔥 {item.conviction:.1f}x usual size"
    return title, body
