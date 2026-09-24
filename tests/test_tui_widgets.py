from rich.text import Text
from textual.app import App

from polywatch.models import CommonBet, FeedItem, Holding, RankedTrader, Verdict
from polywatch.tui.common import CommonList
from polywatch.tui.feed import FeedList
from polywatch.tui.traders import TradersTable
from tests.factories import stats, watched


class Host(App):
    def __init__(self, widget):
        super().__init__()
        self._widget = widget

    def compose(self):
        yield self._widget


def traders():
    return [
        RankedTrader(stats(wallet="0xa", username="alice", win_rate=0.6), Verdict(), score=90, rank=1),
        RankedTrader(stats(wallet="0xb", username="bob", win_rate=0.8), Verdict(flags=("NEW",)), score=80, rank=2),
        RankedTrader(stats(wallet="0xc", username="carol", win_rate=0.7), Verdict(flags=("24/7",)), score=70, rank=3),
    ]


def fitem(key, first_ts, *, usd=116.0, asset="a1"):
    return FeedItem(key=key, wallet="0xa", name="alice", side="BUY", asset=asset, title="Some market",
                    outcome="Yes", slug="some-market", event_slug="some-event", first_ts=first_ts,
                    last_ts=first_ts, shares=200, usd=usd)


async def test_traders_table_shows_rank_order_and_markers():
    table = TradersTable()
    async with Host(table).run_test() as pilot:
        table.show(traders(), {"0xc": "pin"})
        await pilot.pause()
        assert table.row_wallets() == ["0xa", "0xb", "0xc"]
        assert str(table.get_cell("0xc", "rank")) == "★ 3"
        assert table.selected_wallet() == "0xa"


async def test_hiding_flagged_keeps_pinned_rows():
    table = TradersTable()
    async with Host(table).run_test():
        table.show(traders(), {}, show_flagged=False)
        assert table.row_wallets() == ["0xa", "0xc"]
        table.show(traders(), {"0xb": "pin"}, show_flagged=False)
        assert table.row_wallets() == ["0xa", "0xb", "0xc"]


async def test_sorting_keeps_the_selected_trader():
    table = TradersTable()
    async with Host(table).run_test():
        table.show(traders(), {})
        table.move_cursor(row=2)
        table.sort_by("win")
        assert table.row_wallets() == ["0xb", "0xc", "0xa"]
        assert table.selected_wallet() == "0xc"


async def test_provisional_rows_during_a_scan():
    table = TradersTable()
    async with Host(table).run_test():
        table.show([], {})
        table.add_provisional(stats(wallet="0xd", username="dave"), Verdict())
        table.add_provisional(stats(wallet="0xd", username="dave"), Verdict())
        assert table.row_wallets() == ["0xd"]
        assert str(table.get_cell("0xd", "score")) == "…"


async def test_feed_orders_newest_first_and_updates_in_place():
    feed = FeedList()
    async with Host(feed).run_test() as pilot:
        w = watched("0xa", name="alice")
        for key, ts in (("k1", 100), ("k2", 300), ("k3", 200)):
            feed.upsert(fitem(key, ts), w, None, 3.0)
        await pilot.pause()
        assert [row.feed_item.key for row in feed.children] == ["k2", "k3", "k1"]
        feed.upsert(fitem("k1", 100, usd=999.0), w, None, 3.0)
        await pilot.pause()
        assert len(feed.children) == 3 and "$999" in feed.rows["k1"].rendered.plain


async def test_feed_is_trimmed_to_max_rows(monkeypatch):
    monkeypatch.setattr(FeedList, "MAX_ROWS", 2)
    feed = FeedList()
    async with Host(feed).run_test() as pilot:
        for key, ts in (("k1", 100), ("k2", 200), ("k3", 300)):
            feed.upsert(fitem(key, ts), watched("0xa"), None, 3.0)
        await pilot.pause()
        assert sorted(feed.rows) == ["k2", "k3"] and len(feed.children) == 2


async def test_feed_selection_prices_and_assets():
    feed = FeedList()
    async with Host(feed).run_test() as pilot:
        feed.upsert(fitem("k1", 100), watched("0xa"), None, 3.0)
        await pilot.pause()
        assert feed.selected_item().key == "k1"
        feed.refresh_prices({"a1": 0.6})
        assert feed.rows["k1"].price == 0.6 and "now 60¢" in feed.rows["k1"].rendered.plain
        assert feed.visible_assets() == ["a1"]


async def test_traders_table_is_compact():
    table = TradersTable()
    async with Host(table).run_test():
        table.show(traders(), {})
        assert [str(key.value) for key in table.columns] == ["rank", "name", "score", "win", "edge", "flags"]
        assert str(table.get_cell("0xb", "flags")) == "NEW"


async def test_feed_follows_new_rows():
    feed = FeedList(title="Buys")
    async with Host(feed).run_test() as pilot:
        feed.upsert(fitem("k1", 100), watched("0xa"), None, 3.0)
        await pilot.pause()
        feed.upsert(fitem("k2", 200), watched("0xa"), None, 3.0)
        await pilot.pause()
        assert feed.following and feed.selected_item().key == "k2"
        assert feed.border_title == "Buys · following"


async def test_browsing_pauses_following_until_idle_or_home():
    feed = FeedList(title="Buys")
    async with Host(feed).run_test() as pilot:
        for key, ts in (("k1", 100), ("k2", 200)):
            feed.upsert(fitem(key, ts), watched("0xa"), None, 3.0)
        await pilot.pause()
        feed.focus()
        await pilot.press("down")
        assert not feed.following and feed.border_title == "Buys · paused"
        assert feed.selected_item().key == "k1"
        feed.upsert(fitem("k3", 300), watched("0xa"), None, 3.0)  # arrives while browsing
        await pilot.pause()
        assert feed.selected_item().key == "k1"  # the row being looked at stays selected
        paused_at = feed.paused_at
        feed.tick(paused_at + FeedList.RESUME_AFTER_S - 1)
        assert not feed.following
        feed.tick(paused_at + FeedList.RESUME_AFTER_S)
        await pilot.pause()
        assert feed.following and feed.selected_item().key == "k3"
        await pilot.press("down")
        await pilot.press("home")
        await pilot.pause()
        assert feed.following and feed.selected_item().key == "k3"


async def test_feed_rows_show_time_to_resolve_and_holdings():
    feed = FeedList()
    async with Host(feed).run_test() as pilot:
        feed.upsert(fitem("k1", 100), watched("0xa"), None, 3.0, resolves="⏱ in 2h", holding=Holding(5, 0.5))
        await pilot.pause()
        text = feed.rows["k1"].rendered.plain
        assert "⏱ in 2h" in text and "✓ you hold" in text
        row = feed.rows["k1"]
        row.resolves, row.holding = "⏱ live 5m", None
        row.redraw()
        assert "⏱ live 5m" in row.rendered.plain and "you hold" not in row.rendered.plain


async def test_remove_where():
    feed = FeedList()
    async with Host(feed).run_test() as pilot:
        feed.upsert(fitem("k1", 100, asset="gone"), watched("0xa"), None, 3.0)
        feed.upsert(fitem("k2", 200, asset="stays"), watched("0xa"), None, 3.0)
        await pilot.pause()
        assert feed.remove_where(lambda item: item.asset == "gone") == 1
        await pilot.pause()
        assert list(feed.rows) == ["k2"] and len(feed.children) == 1 and feed.selected_item().key == "k2"


def cbet(asset, n=2):
    return CommonBet(asset=asset, condition_id="c", title=f"Market {asset}", outcome="Yes", slug=asset,
                     event_slug=asset, wallets=tuple(f"0x{i}" for i in range(n)), names=tuple("ab"[:n]), usd=100.0,
                     shares=200.0, last_ts=1)


async def test_common_list_rebuilds_and_keeps_the_selection():
    common = CommonList()
    async with Host(common).run_test() as pilot:
        assert common.border_title == "Common trades" and common.border_subtitle == "none in the last 24 h"
        common.show([(cbet("x"), Text("x")), (cbet("y"), Text("y"))])
        await pilot.pause()
        assert common.selected_bet().asset == "x"
        common.index = 1
        common.show([(cbet("z", 3), Text("z")), (cbet("x"), Text("x")), (cbet("y"), Text("y"))])
        await pilot.pause()
        assert [row.bet.asset for row in common.children] == ["z", "x", "y"]
        assert common.selected_bet().asset == "y" and common.border_subtitle == "3"
        common.show([])
        await pilot.pause()
        assert len(common.children) == 0 and common.selected_bet() is None
