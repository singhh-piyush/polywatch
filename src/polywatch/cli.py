"""Command-line entry point."""
from __future__ import annotations

import argparse
import asyncio
import logging
from collections import Counter
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.text import Text

from .api.data_api import DataApi
from .api.gamma import GammaApi
from .api.http import Http
from .config import Settings, load_settings
from .discovery.pipeline import Scanner, ScanProgress
from .fmt import short_wallet, trader_cells, usd_compact
from .markets import profile_url
from .models import RankedTrader
from .store import Store


def setup_logging(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=path, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)


def ranked_table(ranked: list[RankedTrader], top: int) -> Table:
    table = Table(title=f"Top {min(top, len(ranked))} of {len(ranked)} ranked traders")
    for label in ("#", "Trader", "Score", "Win%", "Edge", "ROI", "PnL 90d", "Bets", "Flags", "Profile"):
        table.add_column(label)
    for t in ranked[:top]:
        table.add_row(*(Text(cell) for cell in trader_cells(t, None)), Text(profile_url(t.stats.wallet)))
    return table


def excluded_summary(traders: list[RankedTrader]) -> Counter[str]:
    return Counter((t.verdict.excluded or "").split(":")[0] for t in traders if not t.verdict.eligible)


def excluded_table(traders: list[RankedTrader]) -> Table:
    table = Table(title="Excluded traders")
    for label in ("Trader", "Reason", "Leaderboard PnL"):
        table.add_column(label)
    for t in traders:
        if not t.verdict.eligible:
            table.add_row(Text(t.stats.username or short_wallet(t.stats.wallet)), Text(t.verdict.excluded or ""),
                          Text(usd_compact(t.stats.lb_pnl)))
    return table


async def discover(cfg: Settings, *, limit: int | None, show_excluded: bool, top: int,
                   console: Console | None = None) -> None:
    console = console or Console()
    http = Http()
    store = Store(cfg.db_path)
    scanner = Scanner(DataApi(http), GammaApi(http), store, cfg)
    traders: list[RankedTrader] = []
    try:
        with console.status("Fetching leaderboards…") as status:
            def progress(p: ScanProgress) -> None:
                status.update(f"Scanning traders {p.done}/{p.total}")
                traders.append(RankedTrader(p.stats, p.verdict))

            ranked = await scanner.run(limit=limit, on_progress=progress)
    finally:
        await http.aclose()
        store.close()
    console.print(ranked_table(ranked, top))
    counts = excluded_summary(traders)
    breakdown = ", ".join(f"{reason} {n}" for reason, n in counts.most_common())
    console.print(f"{len(ranked)} ranked, {sum(counts.values())} excluded ({breakdown})")
    if show_excluded:
        console.print(excluded_table(traders))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="polywatch",
                                     description="Find sharp Polymarket traders and follow their bets live.")
    sub = parser.add_subparsers(dest="command")
    d = sub.add_parser("discover", help="scan and rank traders without the TUI")
    d.add_argument("--limit", type=int, default=None, help="only scan the first N leaderboard candidates")
    d.add_argument("--top", type=int, default=50, help="how many ranked traders to print")
    d.add_argument("--show-excluded", action="store_true", help="also list excluded traders and why")
    args = parser.parse_args(argv)
    cfg = load_settings()
    setup_logging(cfg.log_path)
    if args.command == "discover":
        asyncio.run(discover(cfg, limit=args.limit, show_excluded=args.show_excluded, top=args.top))
        return
    from .tui.app import PolywatchApp

    PolywatchApp(cfg).run()
