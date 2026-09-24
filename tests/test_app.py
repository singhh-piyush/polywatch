import time

import httpx
from textual.widgets import Static

from polywatch.api.http import Http
from polywatch.config import Settings
from polywatch.discovery.scoring import rank_traders
from polywatch.feed.timing import MarketTimes
from polywatch.models import Holding, MarketTiming, Verdict
from polywatch.store import Store
from polywatch.tui.add import AddTrader
from polywatch.tui.app import PolywatchApp
from polywatch.tui.common import CommonList
from polywatch.tui.detail import TraderDetail
from polywatch.tui.feed import FeedList
from polywatch.tui.mine import MyTrades
from polywatch.tui.traders import TradersTable
from tests.factories import NOW, position_row, stats, trade

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


class FakeGamma:
    def __init__(self, timings):
        self.timings = timings

    async def market_timing(self, slug):
        return self.timings.get(slug)


def panes(app):
    return app.query_one("#buys", FeedList), app.query_one("#sells", FeedList)


async def test_traders_take_a_quarter_and_trades_the_rest():
    app, _ = make_app()
    async with app.run_test(size=(200, 50)) as pilot:
        await pilot.pause()
        assert app.query_one(TradersTable).size.width == 50
        buys, sells = panes(app)
        assert buys.region.width > sells.region.width and app.query_one(CommonList).region.y < buys.region.y


async def test_buys_and_sells_go_to_separate_panes():
    app, _ = make_app()
    async with app.run_test() as pilot:
        now = int(time.time())
        app.handle_trade(trade(now, wallet="0xaaa", size=400, asset="a1"))
        app.handle_trade(trade(now, wallet="0xaaa", size=400, asset="a2", side="SELL"))
        await pilot.pause()
        buys, sells = panes(app)
        assert [r.feed_item.side for r in buys.rows.values()] == ["BUY"]
        assert [r.feed_item.side for r in sells.rows.values()] == ["SELL"]  # no account yet: every sell shows
        assert sells.border_title == "Sells · all (press m: your account) · following"


async def test_with_an_account_only_sells_of_what_you_hold_show_and_alert():
    app, _ = make_app()
    async with app.run_test() as pilot:
        app.my_wallet = "0xme"
        now = int(time.time())
        app.handle_trade(trade(now, wallet="0xbbb", size=400, asset="later", side="SELL"))
        app.set_holdings({"held": Holding(120, 0.58)})
        app.handle_trade(trade(now, wallet="0xaaa", size=400, asset="other", side="SELL"))
        app.handle_trade(trade(now, wallet="0xaaa", size=400, price=0.71, asset="held", side="SELL"))
        await pilot.pause()
        _, sells = panes(app)
        assert [r.feed_item.asset for r in sells.rows.values()] == ["held"]
        assert "you hold 120 sh @ 58¢" in sells.rows[next(iter(sells.rows))].rendered.plain
        [(title, body, _)] = app.notifier.sent
        assert title == "EXIT: alice sold Yes @ 71¢" and body.endswith("You hold 120 sh @ 58¢")
        app.set_holdings({"later": Holding(10, 0.4)})  # you bought it: its earlier sell shows, "held" goes
        await pilot.pause()
        assert [r.feed_item.asset for r in sells.rows.values()] == ["later"]


async def test_buys_of_what_you_hold_are_tagged():
    app, _ = make_app()
    async with app.run_test() as pilot:
        app.my_wallet = "0xme"
        app.handle_trade(trade(int(time.time()), wallet="0xaaa", size=400, asset="a1"))
        await pilot.pause()
        buys, _ = panes(app)
        assert "✓ you hold" not in next(iter(buys.rows.values())).rendered.plain
        app.set_holdings({"a1": Holding(5, 0.5)})
        assert "✓ you hold" in next(iter(buys.rows.values())).rendered.plain


async def test_two_watched_traders_on_one_outcome_make_a_common_trade():
    app, _ = make_app()
    async with app.run_test() as pilot:
        now = int(time.time())
        app.handle_trade(trade(now - 60, wallet="0xaaa", size=400, asset="a1"))
        app.handle_trade(trade(now, wallet="0xbbb", size=400, asset="a1"))
        app.refresh_common()
        await pilot.pause()
        common = app.query_one(CommonList)
        [bet] = common.bets()
        assert bet.names == ("alice", "bob") and "2 traders" in common.children[0].rendered.plain


async def test_markets_show_time_to_resolve_and_resolved_ones_drop_out():
    app, _ = make_app()
    async with app.run_test() as pilot:
        now = int(time.time())
        app.times = MarketTimes(FakeGamma({
            "soon": MarketTiming(start_ts=now + 2 * 3600 + 630, end_ts=now + 7 * 86400),
            "done": MarketTiming(start_ts=now - 86400, end_ts=now, closed=True),
        }))
        app.handle_trade(trade(now, wallet="0xaaa", size=400, asset="a1", slug="soon", condition="m1"))
        app.handle_trade(trade(now, wallet="0xaaa", size=400, asset="a2", slug="done", condition="m2"))
        app.handle_trade(trade(now, wallet="0xbbb", size=400, asset="a2", slug="done", condition="m2"))
        await pilot.pause()
        await pilot.pause()
        buys, _ = panes(app)
        assert [r.feed_item.slug for r in buys.rows.values()] == ["soon"]
        assert "⏱ in 2h 10m" in next(iter(buys.rows.values())).rendered.plain
        app.refresh_common()
        assert app.query_one(CommonList).bets() == [] and all(i.slug != "done" for i in app.items.values())


async def test_m_sets_your_account():
    app, _ = make_app()
    async with app.run_test() as pilot:
        await pilot.press("m")
        await pilot.pause()
        assert isinstance(app.screen, AddTrader)
        await pilot.press(*WALLET, "enter")
        await pilot.pause()
        assert app.store.get_pref("my_wallet") == WALLET and app.my_wallet == WALLET
        app.set_holdings({"a1": Holding(5, 0.5)})
        app.update_status()
        assert str(app.query_one("#status", Static).content).endswith(" · 1 position")
        _, sells = panes(app)
        assert sells.border_title.startswith("Sells · your holdings")


async def test_my_trades_sit_above_sells_and_common_trades_is_shorter():
    app, _ = make_app()
    async with app.run_test(size=(200, 50)) as pilot:
        await pilot.pause()
        buys, sells = panes(app)
        mine, common = app.query_one(MyTrades), app.query_one(CommonList)
        assert mine.region.x == sells.region.x and mine.region.y < sells.region.y
        assert mine.region.x > buys.region.x and common.region.height < buys.region.height / 2
        assert mine.border_subtitle == "press m: your account"


def keys(feed):
    return [row.feed_item.asset for row in feed.children]


async def test_buys_are_ranked_by_copy_score_and_s_switches_to_newest():
    app, _ = make_app()
    async with app.run_test() as pilot:
        now = int(time.time())
        app.handle_trade(trade(now - 300, wallet="0xbbb", size=400, asset="shared", condition="m1"))
        app.handle_trade(trade(now - 200, wallet="0xaaa", size=400, asset="shared", condition="m1"))
        app.handle_trade(trade(now, wallet="0xaaa", size=400, asset="solo", condition="m2"))
        await pilot.pause()
        buys, _ = panes(app)
        app.rescore()
        await pilot.pause()
        assert keys(buys) == ["shared", "solo", "shared"]  # alice's bet with bob in beats her newer solo bet
        assert buys.border_title == "Buys · best first · following"
        assert all("copy " in row.rendered.plain for row in buys.rows.values())
        await pilot.press("s")
        await pilot.pause()
        assert keys(buys) == ["solo", "shared", "shared"] and app.store.get_pref("buy_order") == "newest"
        assert buys.border_title == "Buys · newest first · following"
    again = PolywatchApp(Settings(), store=app.store, http=offline_http(), notifier=FakeNotifier(), autostart=False)
    assert not again.best_first  # the choice is remembered


class FakeData:
    def __init__(self, rows):
        self.rows = rows

    async def positions(self, wallet):
        return self.rows


async def test_my_trades_show_positions_their_pnl_and_what_tracked_traders_did():
    app, opened = make_app()
    async with app.run_test(size=(200, 50)) as pilot:
        app.my_wallet = "0xme"
        app.data = FakeData([
            position_row("held", avg_price=0.31, size=3.2258, cur_price=0.245, redeemable=False, slug="atl"),
            position_row("won", avg_price=0.4, size=5, cur_price=1.0, redeemable=True, slug="done"),
            position_row("lost", avg_price=0.5, size=4, cur_price=0.0, redeemable=True),
        ])
        app.refresh_holdings()
        await pilot.pause()
        assert [p.asset for p in app.positions] == ["held", "won"] and set(app.holdings) == {"held"}
        app.prices["held"] = 0.5
        app.handle_trade(trade(int(time.time()) - 120, wallet="0xbbb", size=400, price=0.38, asset="held",
                               side="SELL", slug="atl"))
        app.tick()
        await pilot.pause()
        mine = app.query_one(MyTrades)
        assert [p.asset for p in mine.values()] == ["won", "held"]  # redeem first
        held = mine.children[1].rendered.plain
        assert "50¢   $1.61  +$0.61 (+61%)" in held and "⚠ bob sold · last @ 38¢ 2m ago" in held
        assert "✓ redeem $5.00 on Polymarket" in mine.children[0].rendered.plain
        assert mine.border_subtitle == "1 open · 1 to redeem · $6.61 · +$3.61 (+120%)"
        mine.focus()
        mine.index = 1
        await pilot.press("enter")
        assert opened == ["https://polymarket.com/event/atl"]
