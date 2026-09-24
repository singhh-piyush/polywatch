import re

from polywatch.fmt import (ago, alert_text, cents, common_text, delta_cents, duration, edge_pp, exit_alert_text,
                           feed_text, payout, pct, resolves_text, short_url, short_wallet, trader_cells, usd,
                           usd_compact)
from polywatch.models import CommonBet, FeedItem, Holding, MarketTiming, RankedTrader, Verdict
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


def test_duration():
    assert duration(30) == "<1m" and duration(-5) == "<1m"
    assert duration(45 * 60) == "45m"
    assert duration(2 * 3600) == "2h" and duration(2 * 3600 + 10 * 60 + 59) == "2h 10m"
    assert duration(3 * 86400) == "3d" and duration(3 * 86400 + 4 * 3600 + 59 * 60) == "3d 4h"


T0 = 1_790_000_000


def test_resolves_text_for_sports_markets():
    game = MarketTiming(start_ts=T0, end_ts=T0 + 7 * 86400)  # end dates on sports markets are loose deadlines
    assert resolves_text(game, T0 - 2 * 3600 - 600) == "⏱ in 2h 10m"
    assert resolves_text(game, T0 + 40 * 60) == "⏱ live 40m"
    assert resolves_text(game, T0 + 12 * 3600) == "⏱ awaiting result"


def test_resolves_text_for_other_markets():
    election = MarketTiming(start_ts=None, end_ts=T0)
    assert resolves_text(election, T0 - 3 * 86400 - 4 * 3600) == "⏱ ends in 3d 4h"
    assert resolves_text(election, T0 + 60) == "⏱ awaiting result"
    assert resolves_text(MarketTiming(start_ts=None, end_ts=None), T0) == ""
    assert resolves_text(MarketTiming(start_ts=T0, end_ts=T0, closed=True), T0 - 3600) == "⏱ resolved"
    assert resolves_text(None, T0) == ""


def test_feed_text_shows_time_to_resolve_and_your_holding():
    text = feed_text(item(), watched(), None, 3.0, resolves="⏱ in 2h 10m", holding=Holding(120, 0.58)).plain
    lines = text.splitlines()
    assert lines[1].endswith("Chelsea vs Brentford — Chelsea   ⏱ in 2h 10m")
    assert lines[3].endswith("✓ you hold")
    sell = feed_text(item(side="SELL", conviction=None), watched(), None, 3.0, holding=Holding(120, 0.58)).plain
    assert "(exit)   you hold 120 sh @ 58¢" in sell and "✓ you hold" not in sell
    plain = feed_text(item(), watched(), None, 3.0).plain
    assert "⏱" not in plain and "you hold" not in plain


def bet(**kw):
    base = dict(asset="a", condition_id="c", title="Chelsea vs Brentford", outcome="Chelsea", slug="s",
                event_slug="e", wallets=("0x1", "0x2", "0x3"), names=("alice", "bob", "carol"), usd=12_400.0,
                shares=12_400.0 / 0.57, last_ts=T0, against=1)
    base.update(kw)
    return CommonBet(**base)


def test_common_text():
    text = common_text(bet(), 0.59, "⏱ in 2h").plain
    assert text == ("3 traders · Chelsea vs Brentford — Chelsea   ⏱ in 2h\n"
                    "  alice, bob, carol · $12k · avg 57¢ · now 59¢ (+2¢) · 1 against")
    many = bet(wallets=tuple(f"0x{i}" for i in range(6)), names=tuple("abcdef"), against=0)
    assert common_text(many, None, "").plain.splitlines()[1] == "  a, b, c, d +2 · $12k · avg 57¢"


def test_exit_alert_text():
    title, body = exit_alert_text(item(side="SELL", conviction=None, usd=710.0, shares=1000.0), watched(),
                                  Holding(120, 0.58))
    assert title == "EXIT: sharp sold Chelsea @ 71¢"
    assert body == "Chelsea vs Brentford\nYou hold 120 sh @ 58¢"
