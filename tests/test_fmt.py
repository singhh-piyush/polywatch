import re

from polywatch.fmt import (ago, alert_text, cents, delta_cents, edge_pp, feed_text, payout, pct,
                           short_url, short_wallet, trader_cells, usd, usd_compact)
from polywatch.models import FeedItem, RankedTrader, Verdict
from tests.factories import stats, watched


def item(**kw):
    base = dict(key="k", wallet="0xaaa", name="alice", side="BUY", asset="a1", title="Chelsea vs Brentford",
                outcome="Chelsea", slug="epl-che-bre-che", event_slug="epl-che-bre", first_ts=1_790_000_000,
                last_ts=1_790_000_000, shares=7258.62, usd=4210.0, fills=3, conviction=3.4)
    base.update(kw)
    return FeedItem(**base)


def test_cents():
    assert cents(0.58) == "58¢"
    assert cents(0.5799999981) == "58¢"
    assert cents(0.005) == "0.5¢"
    assert cents(0.995) == "99.5¢"


def test_payout_and_delta():
    assert payout(0.58) == "1.72x"
    assert payout(0) == "—"
    assert delta_cents(0.58, 0.59) == "+1¢"
    assert delta_cents(0.58, 0.55) == "-3¢"
    assert delta_cents(0.58, 0.58) == "±0¢"
    assert delta_cents(0.895, 0.92) == "+2¢"  # matches the rounded prices shown: 90¢ → 92¢
    assert delta_cents(0.286, 0.304) == "+1¢"  # 29¢ → 30¢


def test_money_and_percentages():
    assert usd(4210.4) == "$4,210"
    assert usd(-50) == "-$50"
    assert usd_compact(1_817_967) == "$1.8M"
    assert usd_compact(240_000) == "$240k"
    assert usd_compact(-12_600) == "-$13k"
    assert usd_compact(9_999) == "$9,999"
    assert pct(0.714) == "71%"
    assert edge_pp(0.1333) == "+13.3pp"
    assert edge_pp(-0.02) == "-2.0pp"


def test_short_forms():
    assert short_wallet("0x1f2dd6d473f3e824cd2f8a89d9c69fb96f6ad0cf") == "0x1f2d…d0cf"
    assert short_wallet("alice") == "alice"
    assert ago(42) == "42s" and ago(125) == "2m" and ago(7300) == "2h" and ago(3 * 86400) == "3d"
    assert short_url("https://polymarket.com/event/x") == "polymarket.com/event/x"
    assert short_url("https://polymarket.com/" + "a" * 100, width=20) == "polymarket.com/aaaa…"


def test_feed_text_for_a_buy():
    text = feed_text(item(), watched("0xaaa", name="alice", rank=1, win_rate=0.71, pinned=True), 0.59, 3.0).plain
    assert re.match(r"\d\d:\d\d:\d\d  BUY ", text)
    assert "alice  #1 · 71% win · ★" in text
    assert "Chelsea vs Brentford — Chelsea" in text
    assert "@ 58¢ → pays 1.72x" in text and "$4,210" in text and "🔥3.4x" in text
    assert "now 59¢ (+1¢)" in text
    assert "polymarket.com/event/epl-che-bre/epl-che-bre-che" in text


def test_feed_text_for_a_sell_without_price_or_conviction():
    text = feed_text(item(side="SELL", conviction=1.2), watched(rank=None, win_rate=None), None, 3.0).plain
    assert "SELL" in text and "(exit)" in text
    assert "pays" not in text and "🔥" not in text and "now " not in text


def test_trader_cells_for_ranked_trader():
    t = RankedTrader(stats(username="alice", win_rate=0.714, edge=0.1333, roi=0.25, pnl=1_817_967, n=40),
                     Verdict(flags=("CONC", "NEW")), score=87.4, rank=3)
    assert trader_cells(t, "pin") == ("★ 3", "alice", "87", "71%", "+13.3pp", "25%", "$1.8M", "40", "CONC NEW")


def test_trader_cells_for_excluded_and_provisional_traders():
    ex = RankedTrader(stats(username="bob"), Verdict(excluded="inactive: last trade 30d ago"))
    assert trader_cells(ex, "ban")[0] == "✕ —"
    assert trader_cells(ex, None)[-1] == "excluded: inactive: last trade 30d ago"
    provisional = RankedTrader(stats(username="carol"), Verdict())
    assert trader_cells(provisional, None)[:3] == ("—", "carol", "…")
    nameless = RankedTrader(stats(username="", wallet="0x1f2dd6d473f3e824cd2f8a89d9c69fb96f6ad0cf"), Verdict())
    assert trader_cells(nameless, None)[1] == "0x1f2d…d0cf"


def test_alert_text():
    title, body = alert_text(item(), watched(name="alice"), 3.0)
    assert title == "alice: BUY Chelsea @ 58¢"
    assert "Chelsea vs Brentford" in body and "$4,210" in body and "3.4x usual size" in body
    _, quiet = alert_text(item(conviction=1.0), watched(name="alice"), 3.0)
    assert "usual size" not in quiet


def test_fast_bets_are_labelled():
    fast = item()
    fast.fast = "both sides"
    assert "both sides · not worth copying" in feed_text(fast, watched(), None, 3.0).plain
    assert "not worth copying" not in feed_text(item(), watched(), None, 3.0).plain
