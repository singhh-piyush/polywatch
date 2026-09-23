import pytest

from polywatch.discovery.watchlist import build_watchlist, select_watchlist
from polywatch.models import RankedTrader, Verdict
from tests.factories import stats


def ranked(*specs, edges=None):
    edges = edges or {}
    return [RankedTrader(stats(wallet=w, username=w[2:], edge=edges.get(w, 0.16)), Verdict(flags=f), score=100 - i,
                         rank=i + 1) for i, (w, f) in enumerate(specs)]


def test_select_skips_flagged_and_banned_then_adds_pins():
    r = ranked(("0xa", ()), ("0xb", ("NEW",)), ("0xc", ()), ("0xd", ()))
    assert select_watchlist(r, 2, pins={"0xz", "0xc"}, bans={"0xa"}) == ["0xc", "0xd", "0xz"]


def test_banned_pins_are_dropped():
    assert select_watchlist(ranked(("0xa", ())), 5, pins={"0xa"}, bans={"0xa"}) == []


def test_build_watchlist_uses_override_names_and_stats():
    traders = ranked(("0xa", ()), ("0xb", ())) + [
        RankedTrader(stats(wallet="0xpinned", username="pinny", median_bet=250.0), Verdict(excluded="inactive: x")),
    ]
    w = build_watchlist(traders, 1, overrides={"0xpinned": "pin", "0xnew": "pin"}, names={"0xnew": "Newbie"})
    assert list(w) == ["0xa", "0xnew", "0xpinned"]
    assert w["0xa"].rank == 1 and not w["0xa"].pinned and w["0xa"].win_rate == pytest.approx(0.65)
    assert w["0xpinned"].rank is None and w["0xpinned"].pinned and w["0xpinned"].median_bet == 250.0
    assert w["0xpinned"].name == "pinny"
    assert w["0xnew"].name == "Newbie" and w["0xnew"].median_bet == 0.0 and w["0xnew"].win_rate is None


def test_only_traders_who_beat_the_odds_are_auto_watched():
    r = ranked(("0xa", ()), ("0xb", ()), ("0xc", ()), edges={"0xa": 0.0, "0xb": -0.02})
    assert select_watchlist(r, 5, pins={"0xb"}, bans=set(), min_edge=0.0) == ["0xc", "0xb"]  # pins still count


def test_24_7_is_a_badge_not_a_hold_back():
    r = ranked(("0xa", ("24/7",)), ("0xb", ("24/7", "NEW")), ("0xc", ("FAST",)))
    assert select_watchlist(r, 5, pins=set(), bans=set()) == ["0xa"]
