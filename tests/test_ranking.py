import pytest

from polywatch.feed.ranking import copy_score, copy_scores, upside_kept
from polywatch.models import FeedItem
from tests.factories import NOW, watched

HOUR = 3600


def buy(wallet="0xalice", asset="yes", *, ts=NOW, usd=100.0, shares=200.0, side="BUY", condition="m1", fast=None,
        conviction=None):
    return FeedItem(key=f"{wallet}:{asset}:{side}:{ts}", wallet=wallet, name=wallet[2:], side=side, asset=asset,
                    title="Chelsea vs Brentford", outcome=asset.title(), slug="epl-che-bre", event_slug="epl",
                    first_ts=ts, last_ts=ts, condition_id=condition, shares=shares, usd=usd, fast=fast,
                    conviction=conviction)


def test_upside_kept():
    assert upside_kept(0.5, None) == 1.0          # no live price: nothing known
    assert upside_kept(0.5, 0.5) == 1.0
    assert upside_kept(0.5, 0.6) == pytest.approx(2 / 3)
    assert upside_kept(0.9, 0.95) == pytest.approx(0.4737, abs=1e-4)  # most of a favourite's upside is gone
    assert upside_kept(0.5, 0.45) == 1.15         # cheaper helps, capped
    assert upside_kept(0.5, 0.2) == 0.5           # more than halved: the market turned against it
    assert upside_kept(0.5, 1.0) == 0.0
    assert upside_kept(0.0, 0.5) == 1.0


def score(item=None, *, trader_score=80.0, price=None, others=0, against=0, now=NOW):
    return copy_score(item or buy(), watched("0xalice", score=trader_score), now=now, price=price, others=others,
                      against=against)


def test_trader_quality_sets_the_base():
    assert score() == 40                       # 100 x 0.5 x 0.80
    assert score(trader_score=None) == 25      # pinned, not scanned yet: middling
    assert score(trader_score=0.0) == 0


def test_bigger_than_usual_bets_score_higher():
    assert score(buy(conviction=0.5)) == 40    # smaller than usual adds nothing
    assert score(buy(conviction=3.0)) == 52    # + 25 x log10(3)
    assert score(buy(conviction=10.0)) == 65   # 10x and above: the full 25
    assert score(buy(conviction=50.0)) == 65


def test_other_traders_agreeing_or_disagreeing():
    assert score(others=2) == 57               # + 25 x 2/3
    assert score(others=7) == 65               # capped at 3
    assert score(others=1, against=1) == 40    # they cancel out
    assert score(against=3) == 15              # - 25
    assert score(trader_score=0.0, against=3) == 0  # never below zero


def test_bets_fade_with_age():
    assert score(buy(ts=NOW - 6 * HOUR)) == 20
    assert score(buy(ts=NOW - 12 * HOUR)) == 10


def test_price_moving_after_the_bet():
    assert score(price=0.6) == 27              # bought at 50c, now 60c: two thirds of the upside left
    assert score(price=0.45) == 46             # a little cheaper: small bonus
    assert score(trader_score=100.0, others=3, price=0.45, item=buy(conviction=10.0)) == 100  # capped


def test_dimmed_buys_score_zero():
    assert score(buy(fast="95¢+", conviction=10.0), others=3) == 0


def test_copy_scores_counts_recent_copyable_buys_by_other_traders():
    context = [
        buy("0xalice", ts=NOW - 60), buy("0xbob"), buy("0xcarol", "no"),
        buy("0xdave", fast="95¢+"),              # dimmed: doesn't count
        buy("0xerin", ts=NOW - 86_400 - 1),       # older than a day: doesn't count
        buy("0xalice", side="SELL"),
    ]
    people = {w: watched(w, score=80.0) for w in ("0xalice", "0xbob", "0xcarol", "0xdave", "0xerin")}
    items = [context[0], context[2], context[5], buy("0xnobody")]
    scores = copy_scores(items, context, people, {}, NOW)
    assert scores == {context[0].key: 40,         # bob agrees, carol disagrees
                      context[2].key: 23}         # alice and bob disagree; sells and unwatched wallets not scored


def test_copy_scores_uses_live_prices():
    item = buy("0xalice")
    scores = copy_scores([item], [item], {"0xalice": watched("0xalice", score=80.0)}, {"yes": 0.6}, NOW)
    assert scores == {item.key: 27}
