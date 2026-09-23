import pytest

from polywatch.discovery.metrics import (activity_fields, bet_fields, fast_share, longest_quiet_gap, parse_ts,
                                         rebate_total, resolved_bets)
from polywatch.models import ResolvedBet
from tests.factories import DAY, NOW, closed_row, load_fixture, position_row, trade_row

SINCE = NOW - 90 * DAY


def test_parse_ts_accepts_dates_and_datetimes():
    assert parse_ts("1970-01-02") == 86400
    assert parse_ts("1970-01-01T00:01:00Z") == 60
    assert parse_ts("") is None and parse_ts("garbage") is None and parse_ts(None) is None


def test_unredeemed_loser_counts_as_a_loss():
    bets = resolved_bets([closed_row("a1", 50.0)], [position_row("a2", avg_price=0.4, size=100, cur_price=0.0)], SINCE)
    by_asset = {b.asset: b for b in bets}
    assert by_asset["a2"].pnl == pytest.approx(-40.0) and by_asset["a2"].cost == pytest.approx(40.0)
    assert by_asset["a1"].pnl == pytest.approx(50.0)


def test_only_redeemable_positions_inside_the_window_count():
    positions = [
        position_row("open", redeemable=False),
        position_row("old", end_ts=SINCE - DAY),
        position_row("recent", end_ts=NOW - DAY),
    ]
    assert [b.asset for b in resolved_bets([], positions, SINCE)] == ["recent"]


def test_closed_rows_before_the_window_are_ignored():
    closed = [closed_row("new", 10.0, ts=NOW - DAY), closed_row("old", 10.0, ts=SINCE - 1)]
    assert [b.asset for b in resolved_bets(closed, [], SINCE)] == ["new"]


def test_position_row_replaces_closed_row_for_the_same_asset():
    closed = [closed_row("a1", 30.0)]
    positions = [position_row("a1", avg_price=0.5, size=100, cur_price=1.0, realized=30.0)]
    [bet] = resolved_bets(closed, positions, SINCE)
    assert bet.pnl == pytest.approx(80.0)  # 30 realized from partial sells + 50 unrealized on the rest


def test_short_markets_and_zero_cost_rows_are_dropped():
    closed = [closed_row("fast", 5.0, slug="btc-updown-5m-1790000000"), closed_row("free", 5.0, avg_price=0.0),
              closed_row("keep", 5.0)]
    assert [b.asset for b in resolved_bets(closed, [], SINCE)] == ["keep"]


def test_resolved_bets_accept_recorded_rows():
    bets = resolved_bets(load_fixture("closed_positions.json"), load_fixture("positions.json"), 0)
    assert all(isinstance(b.pnl, float) and b.cost > 0 for b in bets)


def test_bet_fields():
    bets = [ResolvedBet(f"a{i}", "m", 0.4, 100.0, 150.0 if i < 12 else -100.0, NOW) for i in range(20)]
    f = bet_fields(bets, shrink_k=10)
    assert f["n"] == 20 and f["wins"] == 12
    assert f["win_rate"] == pytest.approx(0.6)
    assert f["mean_price"] == pytest.approx(0.4)
    assert f["edge"] == pytest.approx((0.6 - 0.4) * 20 / 30)
    assert f["pnl"] == pytest.approx(1000.0)
    assert f["roi"] == pytest.approx(0.5)
    assert f["staked"] == pytest.approx(2000.0)
    assert f["top_share"] == pytest.approx(0.15)
    assert f["median_bet"] == pytest.approx(100.0)


def test_shrinkage_pulls_small_samples_toward_zero():
    lucky = [ResolvedBet(f"l{i}", "m", 0.3, 10.0, 20.0, NOW) for i in range(3)]  # 3 of 3 at 30¢
    steady = [ResolvedBet(f"s{i}", "m", 0.3, 10.0, 20.0 if i < 30 else -10.0, NOW) for i in range(50)]  # 60% at 30¢
    assert bet_fields(lucky, 10)["edge"] < bet_fields(steady, 10)["edge"]


def test_bet_fields_for_no_bets_or_losses():
    assert bet_fields([], 10) == {"n": 0, "wins": 0, "win_rate": 0.0, "mean_price": 0.0, "edge": 0.0, "roi": 0.0,
                                  "pnl": 0.0, "staked": 0.0, "top_share": 0.0, "median_bet": 0.0, "void_share": 0.0}
    assert bet_fields([ResolvedBet("a", "m", 0.5, 10.0, -10.0, NOW)], 10)["top_share"] == 0.0


def test_voided_markets_are_marked():
    closed = [closed_row("void", 1.0, avg_price=0.49, cur_price=0.5), closed_row("won", 50.0)]
    positions = [position_row("void-unredeemed", avg_price=0.6, cur_price=0.5)]
    voided = {b.asset: b.voided for b in resolved_bets(closed, positions, SINCE)}
    assert voided == {"void": True, "won": False, "void-unredeemed": True}


def test_voided_bets_are_left_out_of_scoring():
    real = [ResolvedBet(f"a{i}", "m", 0.4, 100.0, 150.0 if i < 12 else -100.0, NOW) for i in range(20)]
    voids = [ResolvedBet(f"v{i}", "m", 0.49, 100.0, 2.0, NOW, voided=True) for i in range(5)]
    f = bet_fields(real + voids, shrink_k=10)
    assert f["n"] == 20 and f["wins"] == 12 and f["mean_price"] == pytest.approx(0.4)
    assert f["pnl"] == pytest.approx(12 * 150.0 - 8 * 100.0)
    assert f["void_share"] == pytest.approx(0.2)
    only_voids = bet_fields(voids, 10)
    assert only_voids["n"] == 0 and only_voids["void_share"] == 1.0


def fast(rows, min_buys=4):
    return fast_share(rows, snipe_price=0.95, flip_s=600, min_buys=min_buys)


def test_fast_share_counts_snipes_flips_and_both_side_buys():
    rows = [
        trade_row(NOW, price=0.97, asset="snipe", condition="m1"),
        trade_row(NOW, price=0.02, asset="flip", condition="m2"),
        trade_row(NOW + 120, side="SELL", price=0.96, asset="flip", condition="m2"),
        trade_row(NOW, price=0.49, asset="yes", condition="m3"),
        trade_row(NOW + 5, price=0.49, asset="no", condition="m3"),
        trade_row(NOW, price=0.40, asset="held", condition="m4"),
        trade_row(NOW, price=0.03, asset="longshot", condition="m5"),
        trade_row(NOW + 3600, side="SELL", price=0.60, asset="held", condition="m4"),
    ]
    assert fast(rows) == pytest.approx(4 / 6)  # snipe, flip, yes, no; not held (sold an hour later) or longshot


def test_fast_share_boundaries():
    rows = [trade_row(NOW, price=0.94, asset="a", condition="m1"),
            trade_row(NOW + 601, side="SELL", asset="a", condition="m1"),
            trade_row(NOW, asset="b", condition="m2"),
            trade_row(NOW - 60, side="SELL", asset="b", condition="m2"),  # sold before this buy
            trade_row(NOW, asset="c", condition="m3"),
            trade_row(NOW + 601, asset="d", condition="m3"),  # other side, but 10 min later
            trade_row(NOW, price=0.95, asset="e", condition="m4")]
    assert fast(rows) == pytest.approx(1 / 5)  # only the 95¢ buy


def test_fast_share_needs_enough_buys():
    rows = [trade_row(NOW + i, price=0.99, asset=f"a{i}") for i in range(3)]
    assert fast(rows) is None
    assert fast(rows, min_buys=3) == 1.0


def test_activity_fields():
    rows = [trade_row(NOW - 3600), trade_row(NOW - 7200, slug="btc-updown-5m-1"), trade_row(NOW - DAY)]
    f = activity_fields(rows, min_trades_for_gap=200)
    assert f["last_trade_ts"] == NOW - 3600
    assert f["short_share"] == pytest.approx(1 / 3)
    assert f["quiet_gap_h"] is None
    assert activity_fields([], 200) == {"last_trade_ts": 0, "short_share": 0.0, "quiet_gap_h": None}


def test_quiet_gap_needs_enough_trades():
    rows = [trade_row(9 * 3600 + i * 60) for i in range(240)]  # 09:00-12:59 UTC
    assert activity_fields(rows, 200)["quiet_gap_h"] == 20


def test_longest_quiet_gap():
    office = [h * 3600 for h in range(9, 18) for _ in range(20)]
    assert longest_quiet_gap(office) == 15
    wraps = [h * 3600 for h in range(6, 22) for _ in range(20)]  # quiet 22:00-05:59, across midnight
    assert longest_quiet_gap(wraps) == 8
    assert longest_quiet_gap([h * 3600 for h in range(24) for _ in range(10)]) == 0


def test_rebate_total():
    assert rebate_total([{"usdcSize": 10.5}, {"usdcSize": "4.5"}, {}]) == pytest.approx(15.0)
