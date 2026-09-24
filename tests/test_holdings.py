from polywatch.feed.holdings import holdings_from_positions
from polywatch.models import Holding
from tests.factories import position_row


def test_open_positions_are_holdings():
    rows = [
        position_row("held", avg_price=0.31, size=3.2, cur_price=0.28, redeemable=False),
        position_row("settled", size=10, redeemable=True),
        position_row("dust", size=0.001, redeemable=False),
        {"asset": "broken", "size": "n/a", "avgPrice": 0.5},
    ]
    assert holdings_from_positions(rows) == {"held": Holding(shares=3.2, avg_price=0.31)}
