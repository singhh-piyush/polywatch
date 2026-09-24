"""Short markets, live: the open windows, what followed traders bet in them, and the crowd probability.

Taker fills come from the site-wide trade stream the moment they happen. Maker (limit-order) fills of the followed
traders and the crowd come from a fast /activity poll. The leaderboard and the crowd model are rebuilt from the
indexed history in a worker thread.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..api.data_api import DataApi
from ..api.gamma import GammaApi, json_list
from ..config import Settings
from ..feed.sources import ActivityPoller
from ..markets import market_url
from ..models import Trade
from ..web.bus import EventBus
from .board import BOT_BADGES, PERIODS, BoardRow, board
from .crowd import Member, crowd_members, fit_model, probability, signal
from .indexer import IndexProgress, ShortIndexer
from .markets import INTERVALS, Window, open_windows, parse_window
from .store import ShortStore

log = logging.getLogger(__name__)

KEEP_CLOSED_S = 45       # a card stays up this long after its window closes, showing the result
MAX_BETS = 60            # bets kept per window card
BOARD_REFRESH_S = 60
MODEL_REFRESH_S = 900
BOARD_CACHE_S = 30
PRICE_TRADES = 9        # recent trades a window's price is the median of
PRICE_MIN_USD = 2.0     # smaller trades don't count toward the price
CROWD_EVERY = 4          # the crowd's maker fills are polled every 4th poll (20 s)


@dataclass(slots=True)
class LiveWindow:
    window: Window
    outcomes: tuple[str, str] = ("Up", "Down")
    tokens: tuple[str, str] = ("", "")
    condition_id: str = ""
    stances: dict[str, list[float]] = field(default_factory=lambda: defaultdict(lambda: [0.0, 0.0]))  # $ bought
    bets: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=MAX_BETS))
    seen: set[tuple[Any, ...]] = field(default_factory=set)
    known: bool = False  # the market's tokens are known
    recent: deque[float] = field(default_factory=lambda: deque(maxlen=PRICE_TRADES))  # first outcome's prices

    @property
    def price(self) -> float | None:
        """The median of the last few real-sized trades: stray 1c and 99c fills don't move it."""
        if not self.recent:
            return None
        ordered = sorted(self.recent)
        mid = len(ordered) // 2
        return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2

    def side_of(self, trade: Trade) -> int | None:
        if trade.asset and trade.asset in self.tokens:
            return self.tokens.index(trade.asset)
        if trade.outcome in self.outcomes:
            return self.outcomes.index(trade.outcome)
        return None


class ShortService:
    def __init__(self, cfg: Settings, *, store: ShortStore, data: DataApi, gamma: GammaApi, bus: EventBus,
                 notify: Callable[[str, str, str], None] | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.cfg = cfg
        self.store = store
        self.data = data
        self.gamma = gamma
        self.bus = bus
        self.notify = notify or (lambda _t, _b, _u: None)
        self.clock = clock
        self.indexer = ShortIndexer(data, gamma, store, cfg, clock=clock, on_indexed=self._on_indexed)
        self.poller = ActivityPoller(data, backfill_s=3600, overlap_s=120, concurrency=6)
        self.windows: dict[str, LiveWindow] = {}
        self.board7: list[BoardRow] = []
        self.followed: dict[str, BoardRow] = {}
        self.members: dict[str, Member] = {}
        self.model: dict[str, Any] | None = None
        self.prefs = store.prefs()
        self._board_cache: dict[tuple[Any, ...], tuple[float, list[dict[str, Any]]]] = {}
        self._dirty = True
        self._polls = 0
        self._tasks: list[asyncio.Task[Any]] = []

    # --- lifecycle -------------------------------------------------------------------------------

    def start(self) -> None:
        saved = self.store.model()
        self.model = saved[1] if saved else None
        for coro in (self.indexer.run(), self._loop(1.0, self.tick), self._loop(BOARD_REFRESH_S, self.refresh_board),
                     self._loop(MODEL_REFRESH_S, self.refresh_model), self._loop(self.cfg.short_poll_s, self.poll)):
            self._tasks.append(asyncio.create_task(coro))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _loop(self, seconds: float, fn: Callable[[], Any]) -> None:
        while True:
            try:
                result = fn()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                log.exception("short-market task %s failed", getattr(fn, "__name__", fn))
            await asyncio.sleep(seconds)

    def _on_indexed(self, progress: IndexProgress) -> None:
        self.bus.publish("short_index", self.index_json(progress))

    def index_json(self, progress: IndexProgress | None = None) -> dict[str, Any]:
        p = progress or self.indexer.progress
        coverage = self.store.coverage()
        return {"done": p.done, "total": p.total, "pending": p.pending,
                "windows": coverage[0] if coverage else 0, "oldest_ts": coverage[1] if coverage else None}

    # --- leaderboard and model -------------------------------------------------------------------

    async def refresh_board(self) -> None:
        now = int(self.clock())
        rows = await asyncio.to_thread(self._board_rows, now - PERIODS["7d"])
        self.board7 = rows
        self.members = crowd_members(rows, self.cfg.short_crowd_n)
        self._refollow()
        self._board_cache.clear()
        self.bus.publish("short_board_changed", {"ts": now})

    def _board_rows(self, since: int, **kw: Any) -> list[BoardRow]:
        return self.store.read(lambda db: board(db, self.cfg, since_ts=since, **kw))

    def _refollow(self) -> None:
        """Top traders by 7-day profit (with and without bots, so bots can't crowd out the rest) plus stars."""
        n = self.cfg.short_follow_n
        top = self.board7[:n] + [r for r in self.board7 if not r.is_bot][:n]
        by_wallet = {r.wallet: r for r in self.board7}
        starred = [w for w, (star, _bell) in self.prefs.items() if star]
        self.followed = {r.wallet: r for r in top}
        for wallet in starred:
            self.followed.setdefault(wallet, by_wallet.get(wallet) or _unranked(wallet, self.store.names([wallet])))
        self._dirty = True

    async def refresh_model(self) -> None:
        coverage = self.store.coverage()
        if coverage is None or coverage[2] - coverage[1] < 3 * 86_400:
            return  # too little history to fit and judge on
        now = int(self.clock())
        stats = await asyncio.to_thread(self.store.read, lambda db: fit_model(db, self.cfg, now))
        self.model = stats
        self.store.save_model(now, stats)
        self._dirty = True

    async def board_json(self, period: str, coins: tuple[str, ...], intervals: tuple[str, ...],
                         min_windows: int | None) -> list[dict[str, Any]]:
        key = (period, coins, intervals, min_windows)
        cached = self._board_cache.get(key)
        if cached and time.monotonic() - cached[0] < BOARD_CACHE_S:
            return cached[1]
        since = int(self.clock()) - PERIODS.get(period, PERIODS["7d"])
        rows = await asyncio.to_thread(self._board_rows, since, coins=coins, intervals=intervals,
                                       min_windows=min_windows)
        out = []
        for r in rows[:500]:
            star, bell = self.prefs.get(r.wallet, (False, False))
            out.append({**r.to_json(), "star": star, "bell": bell, "followed": r.wallet in self.followed,
                        "url": f"https://polymarket.com/profile/{r.wallet}"})
        self._board_cache[key] = (time.monotonic(), out)
        return out

    def set_pref(self, wallet: str, *, star: bool | None = None, bell: bool | None = None) -> None:
        self.store.set_pref(wallet, star=star, bell=bell)
        self.prefs = self.store.prefs()
        self._board_cache.clear()
        self._refollow()

    # --- live windows ----------------------------------------------------------------------------

    async def tick(self) -> None:
        now = int(self.clock())
        current = {w.slug: w for w in open_windows(self.cfg.short_coins, tuple(INTERVALS), now)}
        for slug in [s for s, lw in self.windows.items() if s not in current and now > lw.window.end_ts + KEEP_CLOSED_S]:
            del self.windows[slug]
            self._dirty = True
        new = [w for s, w in current.items() if s not in self.windows]
        for window in new:
            self.windows[window.slug] = LiveWindow(window)
            self._dirty = True
        unknown = [lw.window.slug for lw in self.windows.values() if not lw.known]
        if unknown and (new or now % 15 == 0):
            await self._load_markets(unknown)
        if self._dirty:
            self._dirty = False
            self.bus.publish("short_windows", self.windows_json())

    async def _load_markets(self, slugs: list[str]) -> None:
        try:
            markets = await self.gamma.markets_by_slug(slugs)
        except Exception as exc:
            log.warning("could not look up short windows: %s", exc)
            return
        for slug, market in markets.items():
            lw = self.windows.get(slug)
            outcomes = [str(o) for o in json_list(market.get("outcomes"))]
            tokens = [str(t) for t in json_list(market.get("clobTokenIds"))]
            if lw is None or len(outcomes) != 2 or len(tokens) != 2:
                continue
            lw.outcomes, lw.tokens = (outcomes[0], outcomes[1]), (tokens[0], tokens[1])
            prices = json_list(market.get("outcomePrices"))
            if not lw.recent and prices:  # a starting price until the window trades
                try:
                    lw.recent.append(float(prices[0]))
                except (TypeError, ValueError):
                    pass
            lw.condition_id = str(market.get("conditionId") or "")
            lw.known = True
            self._dirty = True

    def on_trade(self, trade: Trade) -> None:
        """Every trade on the site stream. Trades in open windows set the price; followed and crowd traders' count."""
        lw = self.windows.get(trade.slug)
        if lw is not None and trade.ts <= lw.window.end_ts and (side := lw.side_of(trade)) is not None:
            # These books change too fast for the price socket, but they trade every second: trades give the price.
            if trade.usd >= PRICE_MIN_USD:
                lw.recent.append(trade.price if side == 0 else 1 - trade.price)
                self._dirty = True
        if trade.wallet not in self.followed and trade.wallet not in self.members:
            return
        if lw is None:
            window = parse_window(trade.slug)
            if window is None or not window.start_ts <= trade.ts <= window.end_ts:
                return
            lw = self.windows.setdefault(trade.slug, LiveWindow(window))
        self._record(lw, trade)

    def _record(self, lw: LiveWindow, trade: Trade) -> None:
        if trade.dedupe_key in lw.seen or trade.ts > lw.window.end_ts:
            return
        side = lw.side_of(trade)
        if side is None:
            return
        lw.seen.add(trade.dedupe_key)
        if trade.side == "BUY":
            # Only buys say which side a trader backs, as in the backtest. Near the close, winners sell what they
            # bought to lock in profit: counting that as a bet on the other side flipped the card.
            lw.stances[trade.wallet][side] += trade.usd
        self._dirty = True
        row = self.followed.get(trade.wallet)
        if row is None:
            return
        bet = {"wallet": trade.wallet, "name": row.name or trade.name, "side": trade.side, "outcome": lw.outcomes[side],
               "usd": trade.usd, "price": trade.price, "ts": trade.ts, "rank": row.rank, "badges": list(row.badges),
               "is_bot": row.is_bot, "pnl": row.pnl, "win_rate": row.win_rate}
        lw.bets.appendleft(bet)
        self.bus.publish("short_bet", {"slug": lw.window.slug, **bet})
        if trade.side == "BUY" and self.prefs.get(trade.wallet, (False, False))[1] \
                and trade.ts >= self.clock() - 60:
            title = f"{bet['name']} bought {lw.outcomes[side]} · {lw.window.label}"
            body = f"${trade.usd:,.0f} at {trade.price * 100:.0f}¢"
            url = market_url(lw.window.slug, lw.window.slug)
            self.notify(title, body, url)
            self.bus.publish("alert", {"title": title, "body": body, "url": url, "kind": "short"})

    async def poll(self) -> None:
        """Maker fills, which the stream doesn't carry: followed traders every poll, the rest of the crowd every
        CROWD_EVERY polls, which keeps the request rate low."""
        self._polls += 1
        wallets = set(self.followed)
        if self._polls % CROWD_EVERY == 0:
            wallets |= set(self.members)
        if not wallets or not self.windows:
            return
        for trade in await self.poller.poll(sorted(wallets), int(self.clock())):
            lw = self.windows.get(trade.slug)
            if lw is not None:
                self._record(lw, trade)

    def windows_json(self) -> list[dict[str, Any]]:
        now = int(self.clock())
        k = float(self.model["k"]) if self.model and self.model.get("edge") else None
        out = []
        for lw in sorted(self.windows.values(), key=lambda w: (INTERVALS[w.window.interval], w.window.coin)):
            w = lw.window
            price = lw.price
            crowd_stances = {wallet: (s[0], s[1]) for wallet, s in lw.stances.items() if wallet in self.members}
            sig, first_n, second_n = signal(crowd_stances, self.members)
            followed_usd = [0.0, 0.0]
            followed_n = [0, 0]
            for wallet, (a, b) in lw.stances.items():
                if wallet in self.followed and a != b:
                    i = 0 if a > b else 1
                    followed_usd[i] += abs(a - b)
                    followed_n[i] += 1
            crowd_p = probability(price, sig, k) if price is not None and k is not None and 0 < price < 1 else None
            out.append({
                "slug": w.slug, "coin": w.coin, "interval": w.interval, "start_ts": w.start_ts, "end_ts": w.end_ts,
                "label": w.label, "outcomes": list(lw.outcomes), "tokens": list(lw.tokens),
                "url": market_url(w.slug, w.slug), "closed": now >= w.end_ts, "price": price,
                "crowd": {"p": crowd_p, "signal": sig, "first_n": first_n, "second_n": second_n},
                "followed": {"usd": followed_usd, "n": followed_n},
                "bets": list(lw.bets),
            })
        return out

    def snapshot(self) -> dict[str, Any]:
        return {"windows": self.windows_json(), "index": self.index_json(), "model": self.model,
                "coins": list(self.cfg.short_coins), "intervals": list(INTERVALS),
                "bot_badges": sorted(BOT_BADGES)}


def _unranked(wallet: str, names: dict[str, str]) -> BoardRow:
    return BoardRow(wallet=wallet, name=names.get(wallet, ""), windows=0, pnl=0.0, cost=0.0, volume=0.0, wins=0,
                    directional=0, win_rate=0.0, avg_price=0.0, edge=0.0, roi=0.0, median_bet=0.0, badges=())
