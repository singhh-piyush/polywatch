import json
import sqlite3

import pytest

from polywatch.config import Settings
from polywatch.short.board import board
from polywatch.short.crowd import Member, crowd_members, evaluate, fit_k, probability, Sample, signal, size_weight
from polywatch.short.indexer import ShortIndexer
from polywatch.short.markets import hourly_slug, open_windows, parse_window, windows_between
from polywatch.short.pnl import price_before, window_results
from polywatch.short.store import ShortStore

UP, DOWN = "Up", "Down"
END = 1_790_227_800 + 900


def fill(wallet, side, outcome, price, size, ts=END - 300, tx=None, name=""):
    return {"proxyWallet": wallet, "side": side, "outcome": outcome, "price": price, "size": size, "timestamp": ts,
            "transactionHash": tx or f"{wallet}{side}{outcome}{price}{size}{ts}", "asset": outcome, "name": name}


# --- markets -----------------------------------------------------------------------------------------

def test_parse_5m_and_15m_windows():
    w = parse_window("btc-updown-15m-1790227800")
    assert (w.coin, w.interval, w.start_ts, w.end_ts) == ("btc", "15m", 1790227800, 1790228700)
    assert w.label == "BTC 15m · 1:30am–1:45am ET"
    assert parse_window("eth-updown-5m-1790227800").end_ts == 1790227800 + 300


def test_parse_hourly_window_in_eastern_time():
    w = parse_window("bitcoin-up-or-down-september-24-2026-1am-et")
    assert (w.coin, w.interval, w.start_ts, w.end_ts) == ("btc", "1h", 1790226000, 1790229600)  # 05:00Z, EDT
    assert hourly_slug("btc", w.start_ts) == w.slug
    winter = parse_window("ethereum-up-or-down-december-1-2026-12pm-et")
    assert winter.start_ts == 1_796_144_400  # 17:00Z, EST


def test_daily_and_other_markets_are_not_short():
    assert parse_window("bitcoin-up-or-down-on-september-24-2026") is None
    assert parse_window("will-it-rain") is None
    assert parse_window("unknowncoin-up-or-down-september-24-2026-1am-et") is None


def test_windows_between_are_newest_first_and_complete():
    since = 1790227800 - 3600
    windows = windows_between(("btc",), ("5m", "15m", "1h"), since, 1790227800)
    assert [w.end_ts for w in windows] == sorted((w.end_ts for w in windows), reverse=True)
    assert sum(w.interval == "5m" for w in windows) == 12
    assert sum(w.interval == "15m" for w in windows) == 4
    assert all(since <= w.start_ts and w.end_ts <= 1790227800 for w in windows)


def test_open_windows_cover_every_interval():
    now = 1790229000
    got = {w.interval: w for w in open_windows(("btc",), ("5m", "15m", "1h"), now)}
    assert all(w.start_ts <= now < w.end_ts for w in got.values())


# --- pnl ---------------------------------------------------------------------------------------------

def test_directional_win_and_loss():
    rows = [fill("a", "BUY", UP, 0.4, 100), fill("b", "SELL", UP, 0.4, 100)]
    res = window_results(rows, (UP, DOWN), UP, END)
    assert res["a"].pnl == pytest.approx(60)       # paid 40, got 100
    assert res["a"].side == UP and res["a"].won and res["a"].side_price == pytest.approx(0.4)
    assert res["a"].edge == pytest.approx(0.6)
    assert res["b"].pnl == pytest.approx(-60)      # sold what it minted: kept 40, owes the winning side
    assert res["b"].minted == pytest.approx(100) and res["b"].cost == pytest.approx(100)


def test_results_sum_to_zero_across_a_market():
    rows = [
        fill("a", "BUY", UP, 0.55, 50), fill("b", "BUY", DOWN, 0.45, 50),           # a mint
        fill("c", "SELL", UP, 0.6, 20), fill("a", "BUY", UP, 0.6, 20),               # c sells minted Up
        fill("a", "SELL", UP, 0.7, 30), fill("b", "SELL", DOWN, 0.3, 30),            # a merge
    ]
    res = window_results(rows, (UP, DOWN), DOWN, END)
    assert sum(r.pnl for r in res.values()) == pytest.approx(0)


def test_duplicate_fills_count_once_and_arb_needs_balance():
    rows = [fill("a", "BUY", UP, 0.5, 10, tx="t1"), fill("a", "BUY", UP, 0.5, 10, tx="t1"),
            fill("a", "BUY", DOWN, 0.5, 1)]
    res = window_results(rows, (UP, DOWN), UP, END)["a"]
    assert res.fills == 2 and not res.both_sides  # a small hedge isn't an arb
    res2 = window_results([fill("a", "BUY", UP, 0.5, 10), fill("a", "BUY", DOWN, 0.45, 10)], (UP, DOWN), UP, END)["a"]
    assert res2.both_sides


def test_early_buys_only_count_before_the_lead():
    rows = [fill("a", "BUY", UP, 0.5, 10, ts=END - 120), fill("a", "BUY", DOWN, 0.5, 30, ts=END - 10)]
    res = window_results(rows, (UP, DOWN), UP, END)["a"]
    assert res.early_buys == {UP: pytest.approx(5), DOWN: 0}
    assert res.last_buy_s == 10 and res.first_buy_s == 120


def test_price_before_is_the_latest_price_not_a_minute_average():
    rows = [fill("a", "BUY", UP, 0.3, 10, ts=END - 110)] + \
           [fill("a", "BUY", DOWN, 0.2, 10, ts=END - 65 + i) for i in range(5)] + \
           [fill("a", "BUY", UP, 0.1, 10, ts=END - 10)]  # after the cutoff: ignored
    assert price_before(rows, (UP, DOWN), END) == pytest.approx(0.8)
    assert price_before([], (UP, DOWN), END) is None


# --- store, board and crowd --------------------------------------------------------------------------

@pytest.fixture
def store():
    s = ShortStore(":memory:")
    yield s
    s.close()


def add_window(store, n, winner, bets, *, price=0.5, interval="5m"):
    """bets: wallet -> (outcome, price, usd)."""
    w = parse_window(f"btc-updown-{interval}-{1790000000 + n * 300}")
    rows = [fill(wallet, "BUY", outcome, p, usd / p, ts=w.end_ts - 120, name=wallet)
            for wallet, (outcome, p, usd) in bets.items()]
    rows += [fill("mm", "SELL", o, p, usd / p, ts=w.end_ts - 120) for o, p, usd in bets.values()]
    results = window_results(rows, (UP, DOWN), winner, w.end_ts)
    store.save_window(w, condition_id=f"c{n}", outcomes=(UP, DOWN), winner=winner, price_before=price, fills=len(rows),
                      truncated=False, results=results.values(), now=w.end_ts)
    return w


CFG = Settings(short_min_windows=5, shrink_k=10)


def test_board_ranks_by_profit_and_badges_snipers(store):
    for n in range(10):
        add_window(store, n, UP, {"sharp": (UP, 0.5, 50), "sniper": (UP, 0.97, 500), "dust": (UP, 0.5, 1)})
    rows = {r.wallet: r for r in board(store.db, CFG, since_ts=0)}
    assert "dust" not in rows  # under the minimum stake: not stored
    assert rows["sharp"].rank == 1 and rows["sharp"].win_rate == 1.0 and rows["sharp"].windows == 10
    assert rows["sharp"].edge == pytest.approx(0.5 * 10 / 20)
    assert "SNIPE" in rows["sniper"].badges and rows["sniper"].is_bot
    assert rows["mm"].pnl < 0 and "MAKER" in rows["mm"].badges


def test_board_filters_by_time_and_interval(store):
    for n in range(6):
        add_window(store, n, UP, {"a": (UP, 0.5, 50)})
    assert board(store.db, CFG, since_ts=0, intervals=("15m",)) == []
    assert len(board(store.db, CFG, since_ts=0, until_ts=1790000000 + 3 * 300 + 300)) == 0  # only 3 windows


def test_crowd_members_skip_bots_and_losers(store):
    for n in range(10):
        add_window(store, n, UP, {"good": (UP, 0.5, 50), "bad": (DOWN, 0.5, 50), "sniper": (UP, 0.97, 500)})
    members = crowd_members(board(store.db, CFG, since_ts=0), 10)
    assert set(members) == {"good"}


def test_signal_and_probability():
    members = {"a": Member("a", 0.2, 100), "b": Member("b", 0.1, 100)}
    sig, first, second = signal({"a": (900, 0), "b": (0, 100), "x": (500, 0)}, members)
    assert (first, second) == (1, 1)
    assert sig == pytest.approx(0.2 * 1.0 - 0.1 * size_weight(100, 100))
    assert probability(0.5, 0, 10) == pytest.approx(0.5)
    assert probability(0.5, sig, 10) > 0.5


def test_fit_k_finds_signal_that_predicts():
    good = [Sample(0.5, 0.1, True, "5m")] * 40 + [Sample(0.5, -0.1, False, "5m")] * 40 + \
           [Sample(0.5, 0.1, False, "5m")] * 10
    k = fit_k(good)
    assert k > 5
    stats = evaluate(good, k)
    assert stats["model_loss"] < stats["market_loss"] and stats["lean_n"] == 90
    assert fit_k([Sample(0.5, 0.1, True, "5m"), Sample(0.5, 0.1, False, "5m")]) == 0


def test_prefs_and_model_round_trip(store):
    store.set_pref("0xa", star=True)
    store.set_pref("0xa", bell=True)
    assert store.prefs() == {"0xa": (True, True)}
    store.set_pref("0xa", star=False, bell=False)
    assert store.prefs() == {}
    store.save_model(5, {"k": 3.5})
    assert store.model() == (5, {"k": 3.5})


# --- indexer -----------------------------------------------------------------------------------------

class FakeGamma:
    def __init__(self, markets):
        self.markets = markets

    async def markets_by_slug(self, slugs):
        return {s: self.markets[s] for s in slugs if s in self.markets}


class FakeData:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def market_trades(self, condition_id, side=None):
        self.calls.append((condition_id, side))
        return self.rows.get(condition_id, []), False


async def test_indexer_indexes_settled_windows_and_retries_unsettled(store):
    now = 1790227800 + 900 + 120
    cfg = Settings(short_coins=("btc",), short_backfill_days=1)
    settled = parse_window("btc-updown-15m-1790227800")
    pending = parse_window("btc-updown-5m-1790228400")
    gamma = FakeGamma({
        settled.slug: {"conditionId": "c1", "outcomes": json.dumps([UP, DOWN]), "outcomePrices": json.dumps(["1", "0"])},
        pending.slug: {"conditionId": "c2", "outcomes": json.dumps([UP, DOWN]), "outcomePrices": json.dumps(["0.6", "0.4"])},
    })
    data = FakeData({"c1": [fill("a", "BUY", UP, 0.5, 100, ts=settled.end_ts - 100),
                            fill("b", "SELL", UP, 0.5, 100, ts=settled.end_ts - 100)]})
    ix = ShortIndexer(data, gamma, store, cfg, clock=lambda: now)
    assert await ix.step()
    assert settled.slug in store.indexed_slugs(0)
    assert pending.slug not in store.indexed_slugs(0)  # no result yet: tried again later
    rows = {r.wallet: r for r in board(store.db, Settings(short_min_windows=1), since_ts=0)}
    assert rows["a"].pnl == pytest.approx(50)
    # windows Gamma doesn't know that closed over an hour ago are recorded as missing, not refetched forever
    old = [w for w in ix.pending(now)[0] if now - w.end_ts > 3600]
    assert old
