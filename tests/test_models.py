from polywatch.models import FeedItem, Trade, Verdict
from tests.factories import load_fixture


def test_trade_from_recorded_activity_row():
    row = load_fixture("activity_trades.json")[0]
    t = Trade.from_api(row)
    assert t is not None
    assert t.wallet == row["proxyWallet"].lower()
    assert t.side in ("BUY", "SELL") and 0 < t.price < 1 and t.size > 0
    assert t.slug == row["slug"]


def test_trade_from_recorded_stream_payload():
    payload = load_fixture("rtds_trade.json")["payload"]
    t = Trade.from_api(payload)
    assert t is not None and t.wallet == payload["proxyWallet"].lower()


def test_trade_rejects_incomplete_rows():
    good = {"proxyWallet": "0xAB", "side": "buy", "price": 0.5, "size": 10, "timestamp": 1}
    assert Trade.from_api(good).side == "BUY"
    for broken in ({**good, "side": ""}, {**good, "price": 0}, {**good, "proxyWallet": ""}, {**good, "size": "x"}):
        assert Trade.from_api(broken) is None


def test_trade_converts_millisecond_timestamps():
    t = Trade.from_api({"proxyWallet": "0xab", "side": "SELL", "price": 0.2, "size": 5, "timestamp": 1790183658173})
    assert t.ts == 1790183658


def test_trade_usd_and_dedupe_key():
    t = Trade.from_api({"proxyWallet": "0xab", "side": "BUY", "price": 0.25, "size": 8, "timestamp": 1,
                        "transactionHash": "0xtx", "asset": "a"})
    assert t.usd == 2.0
    assert t.dedupe_key == ("0xab", "0xtx", "a", "BUY", 8.0)


def test_verdict_eligibility():
    assert Verdict().eligible and Verdict(flags=("CONC",)).eligible
    assert not Verdict(excluded="inactive").eligible


def test_feed_item_avg_price():
    item = FeedItem(key="k", wallet="w", name="n", side="BUY", asset="a", title="t", outcome="o",
                    slug="s", event_slug="e", first_ts=1, last_ts=1, shares=200, usd=116)
    assert item.avg_price == 0.58
