from polywatch.api.http import ApiError
from polywatch.config import Settings
from polywatch.feed.sources import ActivityPoller, FeedService
from tests.factories import NOW, trade, trade_row


class FakeActivity:
    def __init__(self, rows_by_wallet):
        self.rows = rows_by_wallet
        self.calls = []

    async def activity(self, wallet, *, type_="TRADE", start=None, limit=500):
        self.calls.append((wallet, start))
        rows = self.rows.get(wallet, [])
        if isinstance(rows, Exception):
            raise rows
        return [r for r in rows if start is None or r["timestamp"] >= start]


async def test_first_poll_backfills_then_uses_the_cursor():
    data = FakeActivity({"0xa": [trade_row(NOW - 50, wallet="0xa", tx="t2"),
                                 trade_row(NOW - 100, wallet="0xa", tx="t1")]})
    poller = ActivityPoller(data, backfill_s=3600, overlap_s=120)
    trades = await poller.poll(["0xa"], NOW)
    assert [t.tx_hash for t in trades] == ["t1", "t2"]  # oldest first
    assert data.calls == [("0xa", NOW - 3600)]
    await poller.poll(["0xa"], NOW + 20)
    assert data.calls[-1] == ("0xa", NOW - 50 - 120)  # newest trade seen, minus the overlap


async def test_cursor_ignores_the_local_clock():
    data = FakeActivity({"0xa": [], "0xb": [trade_row(NOW - 50, wallet="0xb")]})
    poller = ActivityPoller(data, backfill_s=3600, overlap_s=600)
    await poller.poll(["0xa", "0xb"], NOW)
    await poller.poll(["0xa", "0xb"], NOW + 3 * 3600)  # a fast local clock can't skip trades
    assert data.calls[-2:] == [("0xa", NOW - 3600), ("0xb", NOW - 50 - 600)]


async def test_failed_wallet_keeps_no_cursor_and_backfills_again():
    data = FakeActivity({"0xa": ApiError("boom"), "0xb": [trade_row(NOW - 5, wallet="0xb")]})
    poller = ActivityPoller(data, backfill_s=3600)
    trades = await poller.poll(["0xa", "0xb"], NOW)
    assert [t.wallet for t in trades] == ["0xb"]
    assert "0xa" not in poller.cursor
    await poller.poll(["0xa"], NOW + 20)
    assert data.calls[-1] == ("0xa", NOW + 20 - 3600)


async def test_stream_forwards_only_watched_wallets():
    seen, statuses = [], []

    async def fake_stream(on_status):
        on_status("live")
        yield trade(NOW, wallet="0xa")
        yield trade(NOW, wallet="0xother")

    service = FeedService(FakeActivity({}), Settings(), on_trade=seen.append, on_status=statuses.append,
                          stream=fake_stream)
    service.set_watched({"0xa"})
    await service.run_stream()
    assert [t.wallet for t in seen] == ["0xa"]
    assert service.trades_seen == 2 and statuses == ["live"]


async def test_handler_errors_do_not_stop_the_stream():
    def broken(_trade):
        raise RuntimeError("bug")

    async def fake_stream(on_status):
        yield trade(NOW, wallet="0xa")
        yield trade(NOW + 1, wallet="0xa")

    service = FeedService(FakeActivity({}), Settings(), on_trade=broken, on_status=lambda s: None, stream=fake_stream)
    service.set_watched({"0xa"})
    await service.run_stream()
    assert service.trades_seen == 2


async def test_poll_once_forwards_polled_trades():
    seen = []
    data = FakeActivity({"0xa": [trade_row(NOW - 5, wallet="0xa")]})
    service = FeedService(data, Settings(), on_trade=seen.append, on_status=lambda s: None, clock=lambda: NOW)
    service.set_watched({"0xa"})
    await service.poll_once()
    assert len(seen) == 1 and seen[0].wallet == "0xa"


async def test_stream_skips_fills_without_market_details():
    seen = []

    async def fake_stream(on_status):
        yield trade(NOW, wallet="0xa", slug="")  # the websocket sometimes omits title, slug and outcome
        yield trade(NOW + 1, wallet="0xa")

    service = FeedService(FakeActivity({}), Settings(), on_trade=seen.append, on_status=lambda s: None,
                          stream=fake_stream)
    service.set_watched({"0xa"})
    await service.run_stream()
    assert [t.ts for t in seen] == [NOW + 1]
