import time

import httpx

from polywatch.api.http import Http
from polywatch.config import Settings
from polywatch.discovery.scoring import rank_traders
from polywatch.models import Verdict
from polywatch.store import Store
from polywatch.tui.add import AddTrader
from polywatch.tui.app import PolywatchApp
from polywatch.tui.detail import TraderDetail
from polywatch.tui.feed import FeedList
from polywatch.tui.traders import TradersTable
from tests.factories import NOW, stats, trade

WALLET = "0xabc" + "0" * 36 + "1"


class FakeNotifier:
    def __init__(self):
        self.enabled = True
        self.sent = []

    def notify(self, title, body, url):
        self.sent.append((title, body, url))


def offline_http() -> Http:
    transport = httpx.MockTransport(lambda request: httpx.Response(404, json={"error": "offline"}))
    return Http(httpx.AsyncClient(transport=transport), max_tries=1)


def seeded_store() -> Store:
    store = Store(":memory:")
    scan = store.start_scan(NOW)
    entries = [
        (stats(wallet="0xaaa", username="alice", edge=0.2), Verdict()),
        (stats(wallet="0xbbb", username="bob", edge=0.1), Verdict()),
        (stats(wallet="0xccc", username="carol", edge=0.15, top_share=0.4), Verdict(flags=("CONC",))),
    ]
    for s, v in entries:
        store.save_trader(scan, s, v)
    store.finish_scan(scan, rank_traders(entries, Settings()), candidates=3, now=NOW)
    return store


def make_app():
    opened = []
    app = PolywatchApp(Settings(), store=seeded_store(), http=offline_http(), opener=opened.append,
                       notifier=FakeNotifier(), autostart=False)
    return app, opened


async def test_loads_ranked_traders_and_watchlist():
    app, _ = make_app()
    async with app.run_test():
        table = app.query_one(TradersTable)
        assert table.row_wallets() == ["0xaaa", "0xccc", "0xbbb"]
        assert table.selected_wallet() == "0xaaa"
        assert set(app.watched) == {"0xaaa", "0xbbb"}  # carol is flagged, so not auto-watched


async def test_pin_unpin_and_ban_the_selected_trader():
    app, _ = make_app()
    async with app.run_test() as pilot:
        await pilot.press("p")
        assert app.store.overrides() == {"0xaaa": "pin"} and app.watched["0xaaa"].pinned
        await pilot.press("p")
        assert app.store.overrides() == {}
        await pilot.press("b")
        assert app.store.overrides() == {"0xaaa": "ban"} and "0xaaa" not in app.watched
        assert str(app.query_one(TradersTable).get_cell("0xaaa", "rank")).startswith("✕")


async def test_toggles_for_flagged_rows_and_alerts():
    app, _ = make_app()
    async with app.run_test() as pilot:
        await pilot.press("f")
        assert app.query_one(TradersTable).row_wallets() == ["0xaaa", "0xbbb"]
        await pilot.press("n")
        assert app.notifier.enabled is False


async def test_watched_trade_appears_in_feed_and_opens_market():
    app, opened = make_app()
    async with app.run_test() as pilot:
        app.handle_trade(trade(int(time.time()), wallet="0xaaa", price=0.58, size=500))  # $290
        await pilot.pause()
        feed = app.query_one(FeedList)
        [row] = feed.rows.values()
        assert "alice" in row.rendered.plain and "@ 58¢ → pays 1.72x" in row.rendered.plain
        assert app.notifier.sent == []  # 0.58x the usual bet, not pinned
        assert app.store.db.execute("SELECT COUNT(*) FROM feed_events").fetchone()[0] == 1
        feed.focus()
        await pilot.pause()
        await pilot.press("enter")
        assert opened == ["https://polymarket.com/event/some-event/some-market"]


async def test_high_conviction_bet_alerts_once():
    app, _ = make_app()
    async with app.run_test() as pilot:
        now = int(time.time())
        app.handle_trade(trade(now, wallet="0xaaa", price=0.58, size=5000))  # $2,900 = 5.8x the $500 median
        app.handle_trade(trade(now + 1, wallet="0xaaa", price=0.58, size=100))  # same order, more fills
        await pilot.pause()
        [(title, body, url)] = app.notifier.sent
        assert title.startswith("alice: BUY") and "5.8x" in body and url.endswith("/some-event/some-market")


async def test_backfilled_bets_do_not_alert_and_noise_is_ignored():
    app, _ = make_app()
    async with app.run_test() as pilot:
        now = int(time.time())
        app.handle_trade(trade(now - 3600, wallet="0xaaa", price=0.58, size=5000))  # old: shown, no alert
        app.handle_trade(trade(now, wallet="0xccc", price=0.5, size=5000))           # not watched
        app.handle_trade(trade(now, wallet="0xbbb", price=0.5, size=10))             # $5, under the minimum
        await pilot.pause()
        assert len(app.query_one(FeedList).rows) == 1 and app.notifier.sent == []


async def test_open_profile_and_detail_screen():
    app, opened = make_app()
    async with app.run_test() as pilot:
        await pilot.press("o")
        assert opened == ["https://polymarket.com/profile/0xaaa"]
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, TraderDetail)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, TraderDetail)


async def test_add_trader_by_wallet_pins_it():
    app, _ = make_app()
    async with app.run_test() as pilot:
        await pilot.press("a")
        await pilot.pause()
        assert isinstance(app.screen, AddTrader)
        await pilot.press(*WALLET, "enter")
        await pilot.pause()
        assert app.store.overrides()[WALLET] == "pin" and WALLET in app.watched
        assert WALLET in app.query_one(TradersTable).row_wallets()


async def test_alerts_only_for_bets_that_are_still_recent():
    app, _ = make_app()
    async with app.run_test() as pilot:
        now = int(time.time())
        # e.g. a rescan hours into a session adds a trader, and the poller backfills their last 24 h
        app.handle_trade(trade(now - 3600, wallet="0xaaa", price=0.58, size=5000, asset="old"))
        app.handle_trade(trade(now - 30, wallet="0xaaa", price=0.58, size=5000, asset="new"))
        await pilot.pause()
        assert [url for _, _, url in app.notifier.sent] == ["https://polymarket.com/event/some-event/some-market"]
        assert len(app.query_one(FeedList).rows) == 2


async def test_both_sides_trade_dims_the_first_leg_too():
    app, _ = make_app()
    async with app.run_test() as pilot:
        now = int(time.time())
        app.handle_trade(trade(now - 10, wallet="0xaaa", price=0.5, size=400, asset="yes"))
        app.handle_trade(trade(now, wallet="0xaaa", price=0.5, size=400, asset="no"))
        await pilot.pause()
        rows = app.query_one(FeedList).rows.values()
        assert len(rows) == 2 and all("both sides" in row.rendered.plain for row in rows)
