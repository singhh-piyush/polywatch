from polywatch.feed.consensus import common_bets
from polywatch.models import FeedItem
from tests.factories import NOW

DAY = 86_400


def buy(wallet, asset="yes", *, ts=NOW, usd=100.0, shares=200.0, side="BUY", condition="m1", fast=None):
    return FeedItem(key=f"{wallet}:{asset}:{ts}", wallet=wallet, name=wallet[2:], side=side, asset=asset,
                    title="Chelsea vs Brentford", outcome=asset.title(), slug="epl-che-bre", event_slug="epl",
                    first_ts=ts, last_ts=ts, condition_id=condition, shares=shares, usd=usd, fast=fast)


def test_two_traders_on_the_same_outcome_make_a_common_bet():
    [bet] = common_bets([buy("0xalice", ts=NOW - 60), buy("0xbob", usd=300.0, shares=500.0), buy("0xcarol", "no")],
                        NOW)
    assert bet.asset == "yes" and bet.wallets == ("0xalice", "0xbob") and bet.names == ("alice", "bob")
    assert bet.usd == 400.0 and bet.avg_price == 400.0 / 700.0 and bet.last_ts == NOW
    assert bet.against == 1  # carol bought the other side
    assert bet.title == "Chelsea vs Brentford" and bet.slug == "epl-che-bre" and bet.condition_id == "m1"


def test_one_trader_buying_twice_is_not_common():
    assert common_bets([buy("0xalice"), buy("0xalice", ts=NOW - 600)], NOW) == []


def test_old_sells_and_dimmed_buys_do_not_count():
    items = [buy("0xalice"), buy("0xbob", ts=NOW - DAY - 1), buy("0xcarol", side="SELL"),
             buy("0xdave", fast="95¢+")]
    assert common_bets(items, NOW) == []
    assert len(common_bets(items, NOW, window_s=DAY + 1)) == 1


def test_most_traders_first_then_most_recent():
    items = [buy("0xa", "x", condition="m1", ts=NOW - 10), buy("0xb", "x", condition="m1", ts=NOW - 10),
             buy("0xa", "y", condition="m2"), buy("0xb", "y", condition="m2"),
             buy("0xa", "z", condition="m3", ts=NOW - 99), buy("0xb", "z", condition="m3"), buy("0xc", "z", condition="m3")]
    assert [b.asset for b in common_bets(items, NOW)] == ["z", "y", "x"]


def test_min_traders():
    items = [buy("0xa"), buy("0xb"), buy("0xc")]
    assert common_bets(items, NOW, min_traders=4) == []
    assert common_bets(items, NOW, min_traders=3)[0].wallets == ("0xa", "0xb", "0xc")
