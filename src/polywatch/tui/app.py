"""The polywatch terminal app: ranked traders on the left; common trades, buys, your trades and sells on the right."""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from rich.markup import escape
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
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
from ..feed.consensus import common_bets
from ..feed.holdings import holdings_from_positions, live_price, my_positions, ordered, totals
from ..feed.notifier import Notifier
from ..feed.ranking import copy_score, copy_scores
from ..feed.sources import FeedService
from ..feed.timing import MarketTimes
from ..fmt import (ago, alert_text, common_text, exit_alert_text, portfolio_text, position_text, resolves_text,
                   short_wallet)
from ..markets import market_url, profile_url
from ..models import FeedItem, Holding, MyPosition, RankedTrader, Trade, TraderStats, Verdict, WatchedTrader
from ..store import Store
from .add import AddTrader
from .common import CommonList, TextList, TextRow
from .detail import TraderDetail
from .feed import FeedList, FeedRow
from .mine import MyTrades
from .traders import TradersTable

log = logging.getLogger(__name__)

STATUS_DOT = {"live": "[green]●[/]", "connecting": "[yellow]●[/]", "reconnecting": "[yellow]●[/]"}
# Alerts are for bets you can still act on. Backfills and newly watched traders bring in older ones.
ALERT_MAX_AGE_S = 180
COMMON_WINDOW_S = 86_400
RESCORE_S = 10  # prices move and bets age, so the buys are re-ranked this often


class PolywatchApp(App):
    TITLE = "polywatch"
    CSS = """
    #panes { height: 1fr; }
    #traders { width: 25%; }
    #right { width: 75%; }
    #common { height: 25%; border: round $primary; }
    #feeds { height: 1fr; }
    #buys { width: 2fr; border: round $success; }
    #side { width: 1fr; }
    #mine { height: 1fr; border: round $warning; }
    #sells { height: 1fr; border: round $error; }
    #scan-progress { display: none; height: 1; }
    #scan-progress.active { display: block; }
    #status { height: 1; padding: 0 1; background: $boost; }
    """
    BINDINGS = [
        Binding("d", "rescan", "Rescan"),
        Binding("p", "pin", "Pin"),
        Binding("b", "ban", "Ban"),
        Binding("a", "add_trader", "Add"),
        Binding("m", "set_account", "My account"),
        Binding("s", "toggle_order", "Sort buys"),
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
        self.times = MarketTimes(self.gamma)
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
        self.items: dict[str, FeedItem] = {}  # every feed item from the last day, for common trades
        self.holdings: dict[str, Holding] = {}
        self.positions: list[MyPosition] = []
        self.best_first = self.store.get_pref("buy_order") != "newest"
        self.my_wallet = self.store.get_pref("my_wallet")
        self.my_name = self.store.get_pref("my_name") or ""
        self.show_flagged = True
        self.stream_status = "offline"
        self._common_dirty = False
        self._mine_dirty = False
        self._rescore_due = False
        self._rate_mark = (time.monotonic(), 0)

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="panes"):
            yield TradersTable(id="traders")
            with Vertical(id="right"):
                yield CommonList(id="common")
                with Horizontal(id="feeds"):
                    yield FeedList(self._buys_label(), id="buys", by_score=self.best_first)
                    with Vertical(id="side"):
                        yield MyTrades(id="mine")
                        yield FeedList("Sells", id="sells")
        yield ProgressBar(id="scan-progress", show_eta=False)
        yield Static(id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(TradersTable).focus()
        self._show_sells_title()
        self.refresh_mine()
        self.reload_traders()
        self.update_status()
        self.set_interval(1.0, self.tick)
        self.set_interval(RESCORE_S, self.rescore)
        if not self.autostart:
            return
        self.run_worker(self.feed_service.run_stream(), group="feed", exit_on_error=False)
        self.run_worker(self.feed_service.run_poller(), group="feed", exit_on_error=False)
        self.set_interval(self.cfg.price_refresh_s, self.refresh_prices)
        self.set_interval(self.cfg.price_refresh_s, self.poll_holdings)
        self.set_interval(60, self.refresh_minute)
        self.poll_holdings()
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

    def tick(self) -> None:
        self.update_status()
        now = time.monotonic()
        for feed in self._feeds():
            feed.tick(now)
        if self._common_dirty:
            self.refresh_common()
        if self._rescore_due:
            self.rescore()
        if self._mine_dirty:
            self.refresh_mine()

    def refresh_minute(self) -> None:
        """Countdowns and resolved markets."""
        self.redraw_countdowns()
        slugs = [item.slug for item in self.items.values()] + [p.slug for p in self.positions]
        for slug in self.times.take_due(slugs):
            self.fetch_timing(slug)

    # --- traders ---------------------------------------------------------------------------------

    def reload_traders(self) -> None:
        scan = self.store.latest_scan()
        all_traders = self.store.load_scan(scan[0]) if scan else []
        overrides = self.store.overrides()
        names = self.store.override_names()
        self.watched = build_watchlist(all_traders, self.cfg.watchlist_size, overrides, names,
                                       min_edge=self.cfg.watch_min_edge)
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
        if isinstance(self.focused, TextList):
            return None
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
        if isinstance(self.focused, TextList):
            value = self.focused.selected()
            if value:
                self.opener(market_url(value.event_slug, value.slug))
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

    # --- your account ----------------------------------------------------------------------------

    def action_set_account(self) -> None:
        def chosen(result: tuple[str, str] | None) -> None:
            if not result:
                return
            wallet, name = result
            self.store.set_pref("my_wallet", wallet)
            self.store.set_pref("my_name", name)
            self.my_wallet, self.my_name = wallet, name
            self._show_sells_title()
            self.positions = []
            self._mine_dirty = True
            self.set_holdings({})
            self.refresh_holdings()
            self.notify(f"Following sells of what {name or short_wallet(wallet)} holds", markup=False)

        self.push_screen(AddTrader(self.gamma, prompt="Your Polymarket account: username, profile URL or wallet"),
                         chosen)

    def _show_sells_title(self) -> None:
        self._sells().set_title("Sells · your holdings" if self.my_wallet else "Sells · all (press m: your account)")

    def poll_holdings(self) -> None:
        if self.my_wallet:
            self.refresh_holdings()

    @work(exclusive=True, group="holdings", exit_on_error=False)
    async def refresh_holdings(self) -> None:
        if not self.my_wallet:
            return
        try:
            rows = await self.data.positions(self.my_wallet)
        except Exception as exc:
            log.warning("could not load your positions: %s", exc)
            return
        self.positions = my_positions(rows)
        self.set_holdings(holdings_from_positions(rows))
        self._mine_dirty = True
        for slug in self.times.take_due(p.slug for p in self.positions):
            self.fetch_timing(slug)

    def refresh_mine(self) -> None:
        """My trades: each position's value and P&L, and what tracked traders did on it today."""
        self._mine_dirty = False
        mine = self.query_one(MyTrades)
        if not self.my_wallet:
            mine.show([], subtitle="press m: your account")
            return
        now = time.time()
        held = {p.asset for p in self.positions}
        moves: dict[str, list[FeedItem]] = defaultdict(list)
        for item in self.items.values():
            if item.asset in held:
                moves[item.asset].append(item)
        rows = [(p, position_text(p, live_price(p, self.prices), self._resolves(p.slug), moves[p.asset], now))
                for p in ordered(self.positions, self.prices)]
        value, cost = totals(self.positions, self.prices)
        redeem = sum(p.redeemable for p in self.positions)
        subtitle = portfolio_text(len(self.positions) - redeem, redeem, value, cost) if self.positions else None
        mine.show(rows, subtitle=subtitle)

    def set_holdings(self, holdings: dict[str, Holding]) -> None:
        """Show sells of what you now hold, hide sells of what you no longer hold, and retag rows."""
        changed = {a for a in set(holdings) | set(self.holdings) if holdings.get(a) != self.holdings.get(a)}
        self.holdings = holdings
        sells = self._sells()
        if self.my_wallet:
            sells.remove_where(lambda item: item.asset not in holdings)
            for item in sorted(self.items.values(), key=lambda i: i.first_ts):
                trader = self.watched.get(item.wallet)
                if item.side == "SELL" and item.asset in holdings and item.key not in sells.rows and trader:
                    self._show_item(item, trader)
        for feed in self._feeds():
            for row in feed.rows.values():
                if row.feed_item.asset in changed:
                    row.holding = holdings.get(row.feed_item.asset)
                    row.redraw()

    # --- feed ------------------------------------------------------------------------------------

    def _buys(self) -> FeedList:
        return self.query_one("#buys", FeedList)

    def _sells(self) -> FeedList:
        return self.query_one("#sells", FeedList)

    def _feeds(self) -> tuple[FeedList, FeedList]:
        return self._buys(), self._sells()

    def _buys_label(self) -> str:
        return "Buys · best first" if self.best_first else "Buys · newest first"

    def action_toggle_order(self) -> None:
        self.best_first = not self.best_first
        self.store.set_pref("buy_order", "best" if self.best_first else "newest")
        buys = self._buys()
        buys.set_title(self._buys_label())
        buys.set_order(self.best_first)
        self.notify("Buys: best to copy first" if self.best_first else "Buys: newest first")

    def rescore(self) -> None:
        """Re-rank the buys: prices move, bets age, and other traders pile in or take the other side."""
        self._rescore_due = False
        buys = self._buys()
        items = [row.feed_item for row in buys.rows.values()]
        buys.set_scores(copy_scores(items, self.items.values(), self.watched, self.prices, time.time()))

    def _resolves(self, slug: str) -> str:
        return resolves_text(self.times.cached(slug), time.time())

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, FeedRow):
            item = event.item.feed_item
            self.opener(market_url(item.event_slug, item.slug))
        elif isinstance(event.item, TextRow):
            self.opener(market_url(event.item.value.event_slug, event.item.value.slug))

    def set_stream_status(self, status: str) -> None:
        self.stream_status = status

    def handle_trade(self, trade: Trade) -> None:
        item = self.aggregator.add(trade)
        for other in self.aggregator.take_changed():  # e.g. the first leg of a both-sides trade, now dimmed
            for feed in self._feeds():
                if other.key in feed.rows:
                    feed.rows[other.key].redraw()
            self._common_dirty = True
        if item is None:
            return
        trader = self.watched.get(item.wallet)
        if trader is None:
            return
        self.store.save_feed_item(item)
        timing = self.times.cached(item.slug)
        if timing is not None and timing.closed:
            return  # resolved: nothing left to copy
        self.items[item.key] = item
        if item.asset in self.holdings:
            self._mine_dirty = True  # a tracked trader moved on something you hold
        self._show_item(item, trader)
        for slug in self.times.take_due([item.slug]):
            self.fetch_timing(slug)
        self._maybe_alert(item, trader)
        if item.asset not in self.prices:
            self.fetch_price(item.asset)

    def _show_item(self, item: FeedItem, trader: WatchedTrader) -> None:
        holding = self.holdings.get(item.asset)
        score = None
        if item.side == "BUY":
            feed = self._buys()
            self._common_dirty = self._rescore_due = True
            if item.key not in feed.rows:
                # Placed by the trader alone for now; the next tick adds who else is in and re-sorts.
                score = copy_score(item, trader, now=time.time(), price=self.prices.get(item.asset))
        elif self.my_wallet is None or holding is not None:
            feed = self._sells()
        else:
            return  # a sell of something you don't hold
        feed.upsert(item, trader, self.prices.get(item.asset), self.cfg.conviction_multiple,
                    resolves=self._resolves(item.slug), holding=holding, score=score)

    def _maybe_alert(self, item: FeedItem, trader: WatchedTrader) -> None:
        if item.notified or item.last_ts < time.time() - ALERT_MAX_AGE_S:
            return
        holding = self.holdings.get(item.asset)
        if item.side == "SELL" and self.my_wallet:
            if holding is None:
                return
            title, body = exit_alert_text(item, trader, holding)
        elif is_alert_worthy(item, trader, self.cfg):
            title, body = alert_text(item, trader, self.cfg.conviction_multiple)
        else:
            return
        item.notified = True
        self.notifier.notify(title, body, market_url(item.event_slug, item.slug))

    def refresh_common(self) -> None:
        now = time.time()
        self.items = {key: item for key, item in self.items.items() if item.last_ts >= now - COMMON_WINDOW_S}
        rows = [(bet, common_text(bet, self.prices.get(bet.asset), self._resolves(bet.slug)))
                for bet in common_bets(self.items.values(), now, window_s=COMMON_WINDOW_S)]
        self.query_one(CommonList).show(rows)
        self._common_dirty = False

    def redraw_countdowns(self) -> None:
        for feed in self._feeds():
            for row in feed.rows.values():
                text = self._resolves(row.feed_item.slug)
                if text != row.resolves:
                    row.resolves = text
                    row.redraw()
        self.refresh_common()
        self.refresh_mine()

    @work(group="timing", exit_on_error=False)
    async def fetch_timing(self, slug: str) -> None:
        timing = await self.times.fetch(slug)
        if timing is None:
            return
        if timing.closed:
            self.drop_market(slug)
            return
        text = resolves_text(timing, time.time())
        for feed in self._feeds():
            for row in feed.rows.values():
                if row.feed_item.slug == slug and row.resolves != text:
                    row.resolves = text
                    row.redraw()
        self._common_dirty = self._mine_dirty = True

    def drop_market(self, slug: str) -> None:
        """A resolved market: its bets can't be copied any more."""
        for feed in self._feeds():
            feed.remove_where(lambda item: item.slug == slug)
        self.items = {key: item for key, item in self.items.items() if item.slug != slug}
        self._common_dirty = self._mine_dirty = True

    @work(group="prices", exit_on_error=False)
    async def fetch_price(self, asset: str) -> None:
        price = await self.clob.midpoint(asset)
        if price is not None:
            self.prices[asset] = price
            for feed in self._feeds():
                feed.refresh_prices(self.prices)
            self._common_dirty = self._rescore_due = True

    @work(exclusive=True, group="price-refresh", exit_on_error=False)
    async def refresh_prices(self) -> None:
        assets = [a for feed in self._feeds() for a in feed.visible_assets()]
        assets += [bet.asset for bet in self.query_one(CommonList).bets()]
        assets += [p.asset for p in self.positions if not p.redeemable]
        for asset in dict.fromkeys(assets):
            price = await self.clob.midpoint(asset)
            if price is not None:
                self.prices[asset] = price
        for feed in self._feeds():
            feed.refresh_prices(self.prices)
        self._common_dirty = self._mine_dirty = self._rescore_due = True

    def update_status(self) -> None:
        now, seen = time.monotonic(), self.feed_service.trades_seen
        last_time, last_seen = self._rate_mark
        rate = (seen - last_seen) / max(now - last_time, 1e-6)
        self._rate_mark = (now, seen)
        scan = self.store.latest_scan()
        scan_text = f"scan {ago(time.time() - scan[1])} ago" if scan else "no scan yet"
        dot = STATUS_DOT.get(self.stream_status, "[red]●[/]")
        alerts = "on" if self.notifier.enabled else "off"
        you = ""
        if self.my_wallet:
            held = len(self.holdings)
            you = f" · you: {escape(self.my_name or short_wallet(self.my_wallet))} · {held} position{'' if held == 1 else 's'}"
        self.query_one("#status", Static).update(
            f"{dot} {self.stream_status} · {rate:.0f} trades/s · watching {len(self.watched)} · {scan_text}"
            f" · alerts {alerts}{you}")
