import httpx
import pytest
from starlette.testclient import TestClient

from polywatch.api.data_api import DataApi
from polywatch.api.gamma import GammaApi, category_of, winner_of
from polywatch.api.http import DATA_API, GAMMA_API
from polywatch.api.market_stream import PriceBook, shown_price
from polywatch.markets import parse_trader_ref
from polywatch.models import Trade
from polywatch.short.store import ShortStore
from polywatch.store import Store
from polywatch.web.bus import EventBus, sse_frame
from polywatch.web.engine import LiveEngine
from polywatch.web.server import Services, create_app
from tests.factories import PINNED, trade_row


# --- api additions -----------------------------------------------------------------------------------

async def test_market_trades_pages_until_a_short_page(respx_mock, http):
    pages = {0: [{"i": n} for n in range(4000)], 4000: [{"i": 1}]}
    route = respx_mock.get(f"{DATA_API}/trades").mock(
        side_effect=lambda req: httpx.Response(200, json=pages.get(int(req.url.params["offset"]), [])))
    rows, capped = await DataApi(http).market_trades("0xc")
    assert len(rows) == 4001 and not capped
    assert route.calls[0].request.url.params["takerOnly"] == "false"


async def test_markets_by_slug_asks_for_every_slug_at_once(respx_mock, http):
    route = respx_mock.get(f"{GAMMA_API}/markets").mock(
        side_effect=lambda req: httpx.Response(200, json=[{"slug": s} for s in req.url.params.get_list("slug")]
                                               if req.url.params.get("closed") == "true" else []))
    found = await GammaApi(http).markets_by_slug(["a", "b"])
    assert set(found) == {"a", "b"} and len(route.calls) == 1
    assert route.calls[0].request.url.params["limit"] == "2"


def test_winner_and_category():
    assert winner_of({"outcomes": '["Up", "Down"]', "outcomePrices": '["0", "1"]'}) == "Down"
    assert winner_of({"outcomes": '["Up", "Down"]', "outcomePrices": '["0.6", "0.4"]'}) is None
    assert category_of({"tags": [{"label": "mlb"}, {"label": "Sports"}]}) == "Sports"
    assert category_of({"tags": [{"label": "Pop Culture"}]}) == "Culture"
    assert category_of({}) == ""


def test_parse_trader_ref_moved_to_markets():
    assert parse_trader_ref("@swisstony") == ("name", "swisstony")


# --- live prices -------------------------------------------------------------------------------------

def test_price_book_uses_midpoint_or_last_trade_when_spread_is_wide():
    book = PriceBook()
    book.apply({"event_type": "book", "asset_id": "a", "bids": [{"price": "0.40"}, {"price": "0.44"}],
                "asks": [{"price": "0.46"}], "last_trade_price": "0.45"})
    assert book.price("a") == pytest.approx(0.45)
    changed = book.apply({"event_type": "price_change", "price_changes": [
        {"asset_id": "a", "best_bid": "0.2", "best_ask": "0.6"}]})
    assert changed == {"a"} and book.price("a") == pytest.approx(0.45)  # spread 40c: shows the last trade
    assert shown_price(0.5, 0.52, None) == pytest.approx(0.51)


# --- event bus ---------------------------------------------------------------------------------------

async def test_bus_fans_out_and_drops_tabs_that_fall_behind():
    bus = EventBus()
    q = bus.subscribe()
    bus.publish("prices", {"a": 0.5})
    assert await q.get() == sse_frame("prices", {"a": 0.5})
    assert sse_frame("x", [1]) == 'event: x\ndata: [1]\n\n'
    for n in range(600):
        bus.publish("n", n)
    assert bus.listeners == 0


# --- engine ------------------------------------------------------------------------------------------

@pytest.fixture
def engine(http):
    store = Store(":memory:")
    bus = EventBus()
    eng = LiveEngine(PINNED, store=store, http=http, bus=bus, notifier=type("N", (), {"enabled": False,
                     "notify": lambda *a: None})(), clock=lambda: 1_790_000_000)
    store.set_override("0xsharp", "pin", name="sharp", now=1)
    eng.start(network=False)
    yield eng, bus
    store.close()


async def test_engine_turns_trades_into_item_events(engine):
    eng, bus = engine
    q = bus.subscribe()
    eng.times.take_due = lambda slugs: []  # no timing lookups in this test
    trade = Trade.from_api(trade_row(1_790_000_000 - 30, size=400, price=0.5))
    eng.handle_trade(trade)
    eng.tick()
    frames = []
    while not q.empty():
        frames.append(q.get_nowait())
    items = [f for f in frames if f.startswith("event: items")]
    assert items and '"wallet":"0xsharp"' in items[0]
    assert eng.snapshot()["items"][0]["usd"] == pytest.approx(200)
    eng.rescore()
    assert list(eng.scores.values())[0] >= 0


# --- server ------------------------------------------------------------------------------------------

@pytest.fixture
def client(http):
    services = Services(PINNED, store=Store(":memory:"), short_store=ShortStore(":memory:"), http=http,
                        network=False)
    with TestClient(create_app(services)) as c:
        yield c, services


def test_page_and_snapshot(client):
    c, _ = client
    assert "polywatch" in c.get("/").text
    snap = c.get("/api/snapshot").json()
    assert {"items", "traders", "positions", "status", "short"} <= set(snap)
    assert snap["short"]["coins"]


def test_pin_and_account_actions(client):
    c, services = client
    assert c.post("/api/override", json={"wallet": "0xABC", "mode": "pin", "name": "abc"}).json() == {"ok": True}
    assert services.store.overrides() == {"0xabc": "pin"}
    assert c.post("/api/override", json={"wallet": "0xabc", "mode": "boo"}).status_code == 400
    c.post("/api/account", json={"wallet": None})
    assert services.store.get_pref("my_wallet") is None


def test_short_board_and_stars(client):
    c, services = client
    assert c.get("/api/short/board?period=24h").json() == {"rows": []}
    c.post("/api/short/pref", json={"wallet": "0xa", "star": True})
    assert services.short_store.prefs() == {"0xa": (True, False)}
    assert "0xa" in services.short.followed
