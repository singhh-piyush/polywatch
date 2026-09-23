"""The polywatch terminal app: ranked traders on the left, their live bets on the right."""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import DataTable, Footer, Header, ListView, ProgressBar, Static

from ..api.clob import ClobApi
from ..api.data_api import DataApi
from ..api.gamma import GammaApi
from ..api.http import Http
from ..api.stream import stream_trades
from ..config import Settings
from ..discovery.pipeline import Scanner, ScanProgress
from ..discovery.watchlist import build_watchlist
from ..feed.aggregator import Aggregator, is_alert_worthy
from ..feed.notifier import Notifier
from ..feed.sources import FeedService
from ..fmt import ago, alert_text, short_wallet
from ..markets import market_url, profile_url
from ..models import RankedTrader, Trade, TraderStats, Verdict, WatchedTrader
from ..store import Store
from .add import AddTrader
from .detail import TraderDetail
from .feed import FeedList, FeedRow
from .traders import TradersTable

STATUS_DOT = {"live": "[green]●[/]", "connecting": "[yellow]●[/]", "reconnecting": "[yellow]●[/]"}


class PolywatchApp(App):
    TITLE = "polywatch"
    CSS = """
    #panes { height: 1fr; }
    #traders { width: 55%; }
    #feed { width: 45%; border-left: solid $primary; }
    #scan-progress { display: none; height: 1; }
    #scan-progress.active { display: block; }
    #status { height: 1; padding: 0 1; background: $boost; }
    """
    BINDINGS = [
        Binding("d", "rescan", "Rescan"),
        Binding("p", "pin", "Pin"),
        Binding("b", "ban", "Ban"),
        Binding("a", "add_trader", "Add"),
        Binding("f", "toggle_flagged", "Flagged"),
        Binding("n", "toggle_alerts", "Alerts"),
        Binding("o", "open", "Open"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, cfg: Settings, *, store: Store | None = None, http: Http | None = None,
                 stream: Callable[..., Any] = stream_trades, opener: Callable[[str], Any] | None = None,
                 notifier: Any = None, autostart: bool = True) -> None:
        super().__init__()
        self.cfg = cfg
        self._owns_store = store is None
        self._owns_http = http is None
        self.store = store or Store(cfg.db_path)
        self.http = http or Http()
        self.data = DataApi(self.http)
        self.gamma = GammaApi(self.http)
        self.clob = ClobApi(self.http)
        self.scanner = Scanner(self.data, self.gamma, self.store, cfg)
        self.aggregator = Aggregator(cfg)
        self.feed_service = FeedService(self.data, cfg, on_trade=self.handle_trade,
                                        on_status=self.set_stream_status, stream=stream)
        self.notifier = notifier or Notifier(enabled=cfg.alerts)
        self.opener = opener or self.open_url
        self.autostart = autostart
        self.traders: list[RankedTrader] = []
        self.watched: dict[str, WatchedTrader] = {}
        self.prices: dict[str, float] = {}
        self.show_flagged = True
        self.stream_status = "offline"
        self.started_ts = int(time.time())
        self._rate_mark = (time.monotonic(), 0)

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="panes"):
            yield TradersTable(id="traders")
            yield FeedList(id="feed")
        yield ProgressBar(id="scan-progress", show_eta=False)
        yield Static(id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(TradersTable).focus()
        self.reload_traders()
        self.update_status()
        self.set_interval(1.0, self.update_status)
        if not self.autostart:
            return
        self.run_worker(self.feed_service.run_stream(), group="feed", exit_on_error=False)
        self.run_worker(self.feed_service.run_poller(), group="feed", exit_on_error=False)
        self.set_interval(self.cfg.price_refresh_s, self.refresh_prices)
        scan = self.store.latest_scan()
        if scan is None:
            self.action_rescan()
        elif time.time() - scan[1] > self.cfg.stale_scan_hours * 3600:
            self.notify(f"Last scan was {ago(time.time() - scan[1])} ago. Press d to rescan.")

    async def on_unmount(self) -> None:
        if self._owns_http:
            await self.http.aclose()
        if self._owns_store:
            self.store.close()

    # --- traders ---------------------------------------------------------------------------------

    def reload_traders(self) -> None:
        scan = self.store.latest_scan()
        all_traders = self.store.load_scan(scan[0]) if scan else []
        overrides = self.store.overrides()
        names = self.store.override_names()
        self.watched = build_watchlist(all_traders, self.cfg.watchlist_size, overrides, names)
        self.aggregator.set_watched(self.watched)
        self.feed_service.set_watched(self.watched)
        known = {t.stats.wallet for t in all_traders}
        pinned = {w for w, mode in overrides.items() if mode == "pin"}
        rows = [t for t in all_traders if t.verdict.eligible]
        rows += [t for t in all_traders if not t.verdict.eligible and t.stats.wallet in pinned]
        rows += [RankedTrader(TraderStats(wallet=w, username=names.get(w, "")), Verdict(excluded="not scanned yet"))
                 for w in sorted(pinned - known)]
        self.traders = rows
        self.query_one(TradersTable).show(rows, overrides, show_flagged=self.show_flagged)

    def action_rescan(self) -> None:
        self.run_scan()

    @work(exclusive=True, group="scan", exit_on_error=False)
    async def run_scan(self) -> None:
        bar = self.query_one("#scan-progress", ProgressBar)
        table = self.query_one(TradersTable)
        live_fill = table.row_count == 0
        bar.add_class("active")
        bar.update(total=None, progress=0)
        self.notify("Scanning traders. The first scan takes a few minutes.")

        def progress(p: ScanProgress) -> None:
            bar.update(total=p.total, progress=p.done)
            if live_fill and p.verdict.eligible:
                table.add_provisional(p.stats, p.verdict)

        try:
            ranked = await self.scanner.run(on_progress=progress)
        except Exception as exc:
            self.notify(f"Scan failed: {exc}", severity="error", markup=False)
            return
        finally:
            bar.remove_class("active")
        self.reload_traders()
        self.notify(f"Scan complete: {len(ranked)} traders ranked, watching {len(self.watched)}")

    def _selected_wallet(self) -> str | None:
        if isinstance(self.focused, FeedList):
            item = self.focused.selected_item()
            return item.wallet if item else None
        return self.query_one(TradersTable).selected_wallet()

    def _name_for(self, wallet: str) -> str:
        for t in self.traders:
            if t.stats.wallet == wallet and t.stats.username:
                return t.stats.username
        watched = self.watched.get(wallet)
        return watched.name if watched else short_wallet(wallet)

    def _toggle_override(self, mode: str) -> None:
        wallet = self._selected_wallet()
        if wallet is None:
            return
        name = self._name_for(wallet)
        active = self.store.overrides().get(wallet) == mode
        self.store.set_override(wallet, None if active else mode, name=name, now=int(time.time()))
        self.reload_traders()
        done, undone = {"pin": ("Pinned", "Unpinned"), "ban": ("Banned", "Unbanned")}[mode]
        self.notify(f"{undone if active else done} {name}", markup=False)

    def action_pin(self) -> None:
        self._toggle_override("pin")

    def action_ban(self) -> None:
        self._toggle_override("ban")

    def action_toggle_flagged(self) -> None:
        self.show_flagged = not self.show_flagged
        self.reload_traders()
        self.notify("Showing flagged traders" if self.show_flagged else "Hiding flagged traders")

    def action_toggle_alerts(self) -> None:
        self.notifier.enabled = not self.notifier.enabled
        self.notify(f"Desktop alerts {'on' if self.notifier.enabled else 'off'}")

    def action_open(self) -> None:
        if isinstance(self.focused, FeedList):
            item = self.focused.selected_item()
            if item:
                self.opener(market_url(item.event_slug, item.slug))
            return
        wallet = self.query_one(TradersTable).selected_wallet()
        if wallet:
            self.opener(profile_url(wallet))

    def action_add_trader(self) -> None:
        def added(result: tuple[str, str] | None) -> None:
            if not result:
                return
            wallet, name = result
            self.store.set_override(wallet, "pin", name=name, now=int(time.time()))
            self.reload_traders()
            self.notify(f"Pinned {name or short_wallet(wallet)}. Full stats arrive with the next scan.", markup=False)

        self.push_screen(AddTrader(self.gamma), added)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        trader = next((t for t in self.traders if t.stats.wallet == event.row_key.value), None)
        if trader is not None:
            self.push_screen(TraderDetail(trader, self.cfg, self.data, self.opener))

    # --- feed ------------------------------------------------------------------------------------

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, FeedRow):
            item = event.item.feed_item
            self.opener(market_url(item.event_slug, item.slug))

    def set_stream_status(self, status: str) -> None:
        self.stream_status = status

    def handle_trade(self, trade: Trade) -> None:
        item = self.aggregator.add(trade)
        if item is None:
            return
        trader = self.watched.get(item.wallet)
        if trader is None:
            return
        self.query_one(FeedList).upsert(item, trader, self.prices.get(item.asset), self.cfg.conviction_multiple)
        self.store.save_feed_item(item)
        fresh = item.last_ts >= self.started_ts - 60  # never alert on backfilled history
        if fresh and not item.notified and is_alert_worthy(item, trader, self.cfg):
            item.notified = True
            title, body = alert_text(item, trader, self.cfg.conviction_multiple)
            self.notifier.notify(title, body, market_url(item.event_slug, item.slug))
        if item.asset not in self.prices:
            self.fetch_price(item.asset)

    @work(group="prices", exit_on_error=False)
    async def fetch_price(self, asset: str) -> None:
        price = await self.clob.midpoint(asset)
        if price is not None:
            self.prices[asset] = price
            self.query_one(FeedList).refresh_prices(self.prices)

    @work(exclusive=True, group="price-refresh", exit_on_error=False)
    async def refresh_prices(self) -> None:
        feed = self.query_one(FeedList)
        for asset in feed.visible_assets():
            price = await self.clob.midpoint(asset)
            if price is not None:
                self.prices[asset] = price
        feed.refresh_prices(self.prices)

    def update_status(self) -> None:
        now, seen = time.monotonic(), self.feed_service.trades_seen
        last_time, last_seen = self._rate_mark
        rate = (seen - last_seen) / max(now - last_time, 1e-6)
        self._rate_mark = (now, seen)
        scan = self.store.latest_scan()
        scan_text = f"scan {ago(time.time() - scan[1])} ago" if scan else "no scan yet"
        dot = STATUS_DOT.get(self.stream_status, "[red]●[/]")
        alerts = "on" if self.notifier.enabled else "off"
        self.query_one("#status", Static).update(
            f"{dot} {self.stream_status} · {rate:.0f} trades/s · watching {len(self.watched)} · {scan_text} · alerts {alerts}")
