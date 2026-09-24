import pytest

from polywatch.feed.holdings import holdings_from_positions, live_price, my_positions, ordered, totals
from polywatch.models import Holding, MyPosition
from tests.factories import position_row


def test_open_positions_are_holdings():
    rows = [
        position_row("held", avg_price=0.31, size=3.2, cur_price=0.28, redeemable=False),
        position_row("settled", size=10, redeemable=True),
        position_row("dust", size=0.001, redeemable=False),
        {"asset": "broken", "size": "n/a", "avgPrice": 0.5},
    ]
    assert holdings_from_positions(rows) == {"held": Holding(shares=3.2, avg_price=0.31)}


def test_my_positions_keeps_open_ones_and_winnings_to_redeem():
    rows = [
        position_row("open", avg_price=0.31, size=3.2, cur_price=0.28, redeemable=False, slug="atl-temp"),
        position_row("won", avg_price=0.4, size=5, cur_price=1.0, redeemable=True),
        position_row("lost", avg_price=0.5, size=4, cur_price=0.0, redeemable=True),
        position_row("dust", size=0.001, redeemable=False),
        {"asset": "broken", "size": "n/a", "avgPrice": 0.5},
    ]
    positions = my_positions(rows)
    assert [p.asset for p in positions] == ["open", "won"]
    assert positions[0] == MyPosition(asset="open", title="atl-temp", outcome="Yes", slug="atl-temp",
                                      event_slug="atl-temp", shares=3.2, avg_price=0.31, cur_price=0.28)
    assert positions[1].redeemable and positions[1].cost == pytest.approx(2.0)


def pos(asset, shares, avg, cur, redeemable=False):
    return MyPosition(asset=asset, title=asset, outcome="Yes", slug=asset, event_slug=asset, shares=shares,
                      avg_price=avg, cur_price=cur, redeemable=redeemable)


def test_live_prices_value_open_positions_but_not_resolved_ones():
    prices = {"open": 0.5, "won": 0.2}
    assert live_price(pos("open", 10, 0.3, 0.25), prices) == 0.5
    assert live_price(pos("quiet", 10, 0.3, 0.25), prices) == 0.25  # no midpoint yet: the positions data
    assert live_price(pos("won", 10, 0.3, 1.0, redeemable=True), prices) == 1.0


def test_redeemable_first_then_biggest_value_and_totals():
    positions = [pos("small", 10, 0.3, 0.2), pos("big", 10, 0.3, 0.2), pos("won", 2, 0.4, 1.0, redeemable=True)]
    prices = {"big": 0.9}
    assert [p.asset for p in ordered(positions, prices)] == ["won", "big", "small"]
    value, cost = totals(positions, prices)
    assert value == pytest.approx(2 + 9 + 2) and cost == pytest.approx(0.8 + 3 + 3)
