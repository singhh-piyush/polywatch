"""The live state behind the website: tracked traders' bets, prices, common trades and your positions.

It does what the terminal app does, without widgets. State changes are batched and published on the EventBus
once a second, so the browser gets a steady trickle of small updates rather than a flood.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from dataclasses import asdict
from typing import Any

from ..api.clob import ClobApi
from ..api.data_api import DataApi
from ..api.gamma import GammaApi
from ..api.http import Http
from ..api.market_stream import MarketStream
from ..api.stream import stream_trades
from ..config import Settings
from ..discovery.filters import describe_flag
from ..discovery.pipeline import Scanner, ScanProgress
from ..discovery.watchlist import build_watchlist
from ..feed.aggregator import Aggregator, is_alert_worthy
from ..feed.consensus import common_bets
from ..feed.holdings import holdings_from_positions, my_positions
from ..feed.notifier import Notifier
from ..feed.ranking import copy_score, copy_scores
from ..feed.sources import FeedService
from ..feed.timing import MarketTimes
from ..fmt import alert_text, exit_alert_text, short_wallet
from ..markets import market_url, parse_trader_ref, profile_url
from ..models import FeedItem, Holding, MarketTiming, MyPosition, RankedTrader, Trade, TraderStats, Verdict, WatchedTrader
from ..store import Store
from .bus import EventBus

log = logging.getLogger(__name__)

WINDOW_S = 86_400        # bets kept on the page
ALERT_MAX_AGE_S = 180    # alerts are for bets you can still act on
RESCORE_S = 10
POSITIONS_S = 30
MAX_PRICE_ASSETS = 450   # one price socket carries this many outcomes at most


def item_json(item: FeedItem) -> dict[str, Any]:
    return {
        "key": item.key, "wallet": item.wallet, "name": item.name, "side": item.side, "asset": item.asset,
        "title": item.title, "outcome": item.outcome, "slug": item.slug, "event_slug": item.event_slug,
        "condition_id": item.condition_id, "first_ts": item.first_ts, "last_ts": item.last_ts,
        "shares": item.shares, "usd": item.usd, "avg_price": item.avg_price, "fills": item.fills,
        "conviction": item.conviction, "fast": item.fast, "url": market_url(item.event_slug, item.slug),
    }


def trader_json(t: RankedTrader, cfg: Settings, mode: str | None, watched: bool) -> dict[str, Any]:
    s = t.stats
    return {
        **asdict(s), "name": s.username or short_wallet(s.wallet), "score": round(t.score, 1), "rank": t.rank,
        "excluded": t.verdict.excluded, "flags": list(t.verdict.flags), "mode": mode, "watched": watched,
        "flag_help": {f: describe_flag(f, s, cfg) for f in t.verdict.flags}, "url": profile_url(s.wallet),
    }


def watched_json(w: WatchedTrader) -> dict[str, Any]:
    return asdict(w)


def timing_json(t: MarketTiming) -> dict[str, Any]:
    return asdict(t)


def position_json(p: MyPosition) -> dict[str, Any]:
    return {**asdict(p), "url": market_url(p.event_slug, p.slug)}


class LiveEngine:
    def __init__(self, cfg: Settings, *, store: Store, http: Http, bus: EventBus,
                 stream: Callable[..., Any] = stream_trades, notifier: Any = None,
                 on_any_trade: Callable[[Trade], None] | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.cfg = cfg
        self.store = store
        self.http = http
        self.bus = bus
        self.clock = clock
        self.data = DataApi(http)
        self.gamma = GammaApi(http)
        self.clob = ClobApi(http)
        self.times = MarketTimes(self.gamma)
        self.scanner = Scanner(self.data, self.gamma, store, cfg)
        self.aggregator = Aggregator(cfg)
        self.feed = FeedService(self.data, cfg, on_trade=self.handle_trade, on_status=self.set_stream_status,
                                stream=stream, on_any_trade=on_any_trade)
        self.prices_stream = MarketStream(self.on_prices)
        self.notifier = notifier or Notifier(enabled=cfg.alerts)
        self.traders: list[RankedTrader] = []
        self.overrides: dict[str, str] = {}
        self.watched: dict[str, WatchedTrader] = {}
        self.items: dict[str, FeedItem] = {}
        self.prices: dict[str, float] = {}
        self.holdings: dict[str, Holding] = {}
        self.positions: list[MyPosition] = []
        self.scores: dict[str, int] = {}
        self.my_wallet = store.get_pref("my_wallet")
        self.my_name = store.get_pref("my_name") or ""
        self.stream_status = "offline"
        self.scan: dict[str, int] | None = None
        self._dirty: set[str] = set()
        self._removed: set[str] = set()
        self._common_dirty = False
        self._tasks: list[asyncio.Task[Any]] = []
        self._rate = (time.monotonic(), 0, 0)
        self.rates = {"trades": 0.0, "requests": 0.0}

    # --- lifecycle -------------------------------------------------------------------------------

    def start(self, *, network: bool = True) -> None:
        self.reload_traders()
        if not network:
            return
        self._spawn(self.feed.run_stream())
        self._spawn(self.feed.run_poller())
        self._spawn(self.prices_stream.run())
        self._spawn(self._watch_lag())
        self._spawn(self._every(1.0, self.tick))
        self._spawn(self._every(RESCORE_S, self.rescore))
        self._spawn(self._every(POSITIONS_S, self.refresh_positions))
        self._spawn(self._every(60, self.refresh_minute))
        self._spawn(self._every(self.cfg.price_refresh_s, self.fallback_prices))
        scan = self.store.latest_scan()
        if scan is None:
            self.rescan()

    async def stop(self) -> None:
        self.prices_stream.stop()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _watch_lag(self) -> None:
        """Log when something blocks the event loop: the live sockets fall behind when it does."""
        loop = asyncio.get_running_loop()
        while True:
            start = loop.time()
            await asyncio.sleep(0.25)
            lag = loop.time() - start - 0.25
            if lag > 0.3:
                log.warning("event loop blocked for %.2fs", lag)

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro)
        self._tasks.append(task)
        task.add_done_callback(lambda t: self._tasks.remove(t) if t in self._tasks else None)
        return task

    async def _every(self, seconds: float, fn: Callable[[], Any]) -> None:
        while True:
            try:
                result = fn()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                log.exception("periodic task %s failed", getattr(fn, "__name__", fn))
            await asyncio.sleep(seconds)

    # --- state for a new browser tab -------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        slugs = {i.slug for i in self.items.values()} | {p.slug for p in self.positions}
        return {
            "items": [item_json(i) for i in self.items.values()],
            "scores": self.scores,
            "prices": self.prices,
            "markets": {s: timing_json(t) for s in slugs if (t := self.times.cached(s)) is not None},
            "watched": {w: watched_json(t) for w, t in self.watched.items()},
            "traders": self.traders_json(),
            "common": self.common_json(),
            "positions": self.positions_json(),
            "status": self.status(),
            "settings": {"conviction_multiple": self.cfg.conviction_multiple, "snipe_price": self.cfg.snipe_price,
                         "window_days": self.cfg.window_days},
        }

    def status(self) -> dict[str, Any]:
        scan = self.store.latest_scan()
        return {
            "stream": self.stream_status, "prices": "live" if self.prices_stream.connected else "polling",
            "watching": len(self.watched), "scan_ts": scan[1] if scan else None, "scan": self.scan,
            "alerts": self.notifier.enabled, "account": {"wallet": self.my_wallet, "name": self.my_name},
            "rates": self.rates,
        }

    # --- traders ---------------------------------------------------------------------------------

    def reload_traders(self) -> None:
        scan = self.store.latest_scan()
        all_traders = self.store.load_scan(scan[0]) if scan else []
        self.overrides = self.store.overrides()
        names = self.store.override_names()
        self.watched = build_watchlist(all_traders, self.cfg.watchlist_size, self.overrides, names,
                                       min_edge=self.cfg.watch_min_edge)
        self.aggregator.set_watched(self.watched)
        self.feed.set_watched(self.watched)
        known = {t.stats.wallet for t in all_traders}
        pinned = {w for w, mode in self.overrides.items() if mode == "pin"}
        rows = list(all_traders)
        rows += [RankedTrader(TraderStats(wallet=w, username=names.get(w, "")), Verdict(excluded="not scanned yet"))
                 for w in sorted(pinned - known)]
        self.traders = rows
        self.bus.publish("traders", self.traders_json())
        self.bus.publish("watched", {w: watched_json(t) for w, t in self.watched.items()})

    def traders_json(self) -> list[dict[str, Any]]:
        return [trader_json(t, self.cfg, self.overrides.get(t.stats.wallet), t.stats.wallet in self.watched)
                for t in self.traders]

    def name_for(self, wallet: str) -> str:
        for t in self.traders:
            if t.stats.wallet == wallet and t.stats.username:
                return t.stats.username
        watched = self.watched.get(wallet)
        return watched.name if watched else short_wallet(wallet)

    def set_override(self, wallet: str, mode: str | None, name: str = "") -> None:
        self.store.set_override(wallet, mode, name=name or self.name_for(wallet), now=int(self.clock()))
        self.reload_traders()

    async def resolve_trader(self, text: str) -> list[tuple[str, str]]:
        """(name, wallet) matches for a wallet, profile URL or username."""
        kind, value = parse_trader_ref(text)
        if not value:
            return []
        if kind == "wallet":
            return [("", value)]
        return await self.gamma.search_profiles(value)

    def rescan(self) -> bool:
        if self.scan is not None:
            return False
        self.scan = {"done": 0, "total": 0}
        self._spawn(self._run_scan())
        self.bus.publish("status", self.status())
        return True

    async def _run_scan(self) -> None:
        def progress(p: ScanProgress) -> None:
            self.scan = {"done": p.done, "total": p.total}

        try:
            ranked = await self.scanner.run(on_progress=progress)
        except Exception as exc:
            log.exception("scan failed")
            self.bus.publish("toast", {"text": f"Scan failed: {exc}", "kind": "error"})
            return
        finally:
            self.scan = None
        self.reload_traders()
        self.bus.publish("toast", {"text": f"Scan complete: {len(ranked)} traders ranked, watching {len(self.watched)}"})

    # --- your account ----------------------------------------------------------------------------

    def set_account(self, wallet: str | None, name: str = "") -> None:
        self.store.set_pref("my_wallet", wallet)
        self.store.set_pref("my_name", name if wallet else None)
        self.my_wallet, self.my_name = wallet, name if wallet else ""
        self.positions, self.holdings = [], {}
        self.bus.publish("positions", self.positions_json())
        self.bus.publish("status", self.status())
        if wallet:
            self._spawn(self.refresh_positions())

    async def refresh_positions(self) -> None:
        if not self.my_wallet:
            return
        try:
            rows = await self.data.positions(self.my_wallet)
        except Exception as exc:
            log.warning("could not load your positions: %s", exc)
            return
        self.positions = my_positions(rows)
        self.holdings = holdings_from_positions(rows)
        self._fetch_due_timings(p.slug for p in self.positions)
        self.bus.publish("positions", self.positions_json())

    def positions_json(self) -> dict[str, Any]:
        return {"positions": [position_json(p) for p in self.positions],
                "holdings": {a: asdict(h) for a, h in self.holdings.items()}}

    # --- the feed --------------------------------------------------------------------------------

    def set_stream_status(self, status: str) -> None:
        self.stream_status = status

    def handle_trade(self, trade: Trade) -> None:
        item = self.aggregator.add(trade)
        for other in self.aggregator.take_changed():  # the first leg of a both-sides trade, now dimmed
            if other.key in self.items:
                self._dirty.add(other.key)
        if item is None or item.wallet not in self.watched:
            return
        self.store.save_feed_item(item)
        timing = self.times.cached(item.slug)
        if timing is not None and timing.closed:
            return  # resolved: nothing left to copy
        new = item.key not in self.items
        self.items[item.key] = item
        self._dirty.add(item.key)
        if item.side == "BUY":
            self._common_dirty = True
            if new:  # placed by the trader alone for now; the next rescore adds who else is in
                self.scores[item.key] = copy_score(item, self.watched[item.wallet], now=self.clock(),
                                                   price=self.prices.get(item.asset))
        self._fetch_due_timings([item.slug])
        self._maybe_alert(item, self.watched[item.wallet])
        if item.asset not in self.prices and not self.prices_stream.connected:
            self._spawn(self._fetch_price(item.asset))

    def _maybe_alert(self, item: FeedItem, trader: WatchedTrader) -> None:
        if item.notified or item.last_ts < self.clock() - ALERT_MAX_AGE_S:
            return
        holding = self.holdings.get(item.asset)
        if item.side == "SELL":
            if holding is None:
                return
            title, body = exit_alert_text(item, trader, holding)
            kind = "exit"
        elif is_alert_worthy(item, trader, self.cfg):
            title, body = alert_text(item, trader, self.cfg.conviction_multiple)
            kind = "buy"
        else:
            return
        item.notified = True
        url = market_url(item.event_slug, item.slug)
        self.notifier.notify(title, body, url)
        self.bus.publish("alert", {"title": title, "body": body, "url": url, "kind": kind, "key": item.key})

    def tick(self) -> None:
        now = self.clock()
        stale = [k for k, i in self.items.items() if i.last_ts < now - WINDOW_S]
        for key in stale:
            self._drop(key)
        if self._dirty:
            self.bus.publish("items", [item_json(self.items[k]) for k in self._dirty if k in self.items])
            self._dirty.clear()
        if self._removed:
            self.bus.publish("remove", sorted(self._removed))
            self._removed.clear()
        if self._common_dirty:
            self._common_dirty = False
            self.bus.publish("common", self.common_json())
        self._update_rates()
        self.bus.publish("status", self.status())
        self.prices_stream.watch(self._price_assets())

    def _update_rates(self) -> None:
        """Trades and API requests per second, smoothed over about ten seconds."""
        mark, trades, requests = self._rate
        now = time.monotonic()
        span = max(now - mark, 1e-6)
        fresh = {"trades": (self.feed.trades_seen - trades) / span, "requests": (self.http.requests - requests) / span}
        self.rates = {k: round(0.9 * self.rates[k] + 0.1 * v, 1) for k, v in fresh.items()}
        self._rate = (now, self.feed.trades_seen, self.http.requests)

    def _drop(self, key: str) -> None:
        if self.items.pop(key, None) is not None:
            self.scores.pop(key, None)
            self._dirty.discard(key)
            self._removed.add(key)
            self._common_dirty = True

    def common_json(self) -> list[dict[str, Any]]:
        bets = common_bets(self.items.values(), self.clock(), window_s=WINDOW_S)
        return [{**asdict(b), "avg_price": b.avg_price, "url": market_url(b.event_slug, b.slug)} for b in bets]

    def rescore(self) -> None:
        buys = [i for i in self.items.values() if i.side == "BUY"]
        self.scores = copy_scores(buys, self.items.values(), self.watched, self.prices, self.clock())
        self.bus.publish("scores", self.scores)

    # --- prices and timing -----------------------------------------------------------------------

    def _price_assets(self) -> list[str]:
        """Outcomes on screen, newest bets first, up to what one socket carries."""
        assets = [p.asset for p in self.positions if not p.redeemable]
        assets += [i.asset for i in sorted(self.items.values(), key=lambda i: -i.last_ts)]
        return list(dict.fromkeys(assets))[:MAX_PRICE_ASSETS]

    def on_prices(self, batch: dict[str, float]) -> None:
        self.prices.update(batch)
        self.bus.publish("prices", batch)

    async def _fetch_price(self, asset: str) -> None:
        price = await self.clob.midpoint(asset)
        if price is not None:
            self.on_prices({asset: price})

    async def fallback_prices(self) -> None:
        """Only while the price socket is down: poll the midpoints of what's on screen."""
        if self.prices_stream.connected:
            return
        batch = {}
        for asset in self._price_assets()[:150]:
            price = await self.clob.midpoint(asset)
            if price is not None:
                batch[asset] = price
        if batch:
            self.on_prices(batch)

    def refresh_minute(self) -> None:
        self._fetch_due_timings([i.slug for i in self.items.values()] + [p.slug for p in self.positions])

    def _fetch_due_timings(self, slugs: Any) -> None:
        for slug in self.times.take_due(slugs):
            self._spawn(self._fetch_timing(slug))

    async def _fetch_timing(self, slug: str) -> None:
        timing = await self.times.fetch(slug)
        if timing is None:
            return
        self.bus.publish("markets", {slug: timing_json(timing)})
        if timing.closed:
            for key in [k for k, i in self.items.items() if i.slug == slug]:
                self._drop(key)
