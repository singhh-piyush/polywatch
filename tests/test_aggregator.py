from polywatch.config import Settings
from polywatch.feed.aggregator import Aggregator, is_alert_worthy
from tests.factories import NOW, trade, watched

CFG = Settings(feed_min_usd=100.0, merge_gap_s=30, merge_max_s=300, conviction_multiple=3.0)


def agg(**watch_kw):
    return Aggregator(CFG, {"0xsharp": watched("0xsharp", **watch_kw)})


def test_ignores_unwatched_wallets_and_short_markets():
    a = agg()
    assert a.add(trade(NOW, wallet="0xother", size=1000)) is None
    assert a.add(trade(NOW, size=1000, slug="btc-updown-15m-1790000000")) is None


def test_merges_fills_and_shows_once_over_the_minimum():
    a = agg()
    assert a.add(trade(NOW, size=100, price=0.5)) is None  # $50 so far
    item = a.add(trade(NOW + 5, size=120, price=0.5))       # $110 total
    assert item is not None and item.fills == 2 and item.usd == 110 and item.shares == 220
    assert a.add(trade(NOW + 10, size=10, price=0.5)) is item and item.fills == 3


def test_a_gap_starts_a_new_item():
    a = agg()
    first = a.add(trade(NOW, size=400))
    second = a.add(trade(NOW + 31, size=400))
    assert first is not None and second is not None and first.key != second.key


def test_long_orders_are_split_at_max_span():
    a = agg()
    keys = {a.add(trade(NOW + t, size=400)).key for t in range(0, 330, 20)}
    assert len(keys) == 2


def test_duplicate_fills_count_once():
    a = agg()
    fill = trade(NOW, size=400, tx="0xsame")
    item = a.add(fill)
    assert a.add(fill) is None and item.fills == 1


def test_late_older_fill_joins_the_open_order():
    a = agg()
    item = a.add(trade(NOW, size=400))
    assert a.add(trade(NOW - 10, size=400)) is item
    assert item.first_ts == NOW - 10 and item.fills == 2


def test_old_backfilled_fill_does_not_merge_into_a_new_order():
    a = agg()
    new = a.add(trade(NOW, size=400))
    assert a.add(trade(NOW - 3600, size=400)) is not new


def test_buys_and_sells_are_separate_items():
    a = agg()
    buy = a.add(trade(NOW, size=400))
    sell = a.add(trade(NOW + 1, size=400, side="SELL"))
    assert buy is not sell and sell.side == "SELL"


def test_conviction_is_relative_to_the_median_bet():
    assert agg(median_bet=100.0).add(trade(NOW, size=700, price=0.5)).conviction == 3.5
    assert agg(median_bet=0.0).add(trade(NOW, size=700, price=0.5)).conviction is None


def test_set_watched_replaces_the_watch_map():
    a = agg()
    a.set_watched({})
    assert a.add(trade(NOW, size=1000)) is None


def test_is_alert_worthy():
    item = agg(median_bet=100.0).add(trade(NOW, size=600, price=0.5))  # $300 = 3.0x
    assert is_alert_worthy(item, watched(), CFG)
    item.conviction = 2.9
    assert not is_alert_worthy(item, watched(), CFG)
    assert is_alert_worthy(item, watched(pinned=True), CFG)
