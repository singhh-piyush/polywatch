import pytest

from polywatch.discovery.filters import describe_flag, early_exclusion, evaluate, full_exclusion
from tests.factories import DAY, NOW, PINNED, stats

CFG = PINNED


def test_clean_trader_is_eligible_without_flags():
    v = evaluate(stats(), NOW, CFG)
    assert v.eligible and v.flags == ()


@pytest.mark.parametrize("overrides,prefix", [
    ({"lb_volume": 10_000_000.0, "lb_pnl": 100_000.0}, "market maker"),
    ({"markets_traded": 20_001}, "bot"),
    ({"last_trade_ts": 0}, "inactive"),
    ({"last_trade_ts": NOW - 15 * DAY}, "inactive"),
    ({"short_share": 0.51}, "bot"),
    ({"truncated": True}, "bot"),
    ({"n": 2001}, "bot"),
    ({"n": 14}, "too few bets"),
    ({"pnl": 0.0}, "unprofitable"),
    ({"top_share": 0.51}, "one-hit"),
    ({"account_age_d": 29.0}, "new account"),
    ({"maker_rebates": 400.0}, "market maker"),  # 0.5% of the $80k staked
])
def test_exclusion_rules(overrides, prefix):
    assert full_exclusion(stats(**overrides), NOW, CFG).startswith(prefix)


@pytest.mark.parametrize("overrides", [
    {"lb_volume": 5_000_000.0, "lb_pnl": 1.0},
    {"markets_traded": 20_000},
    {"last_trade_ts": NOW - 14 * DAY},
    {"short_share": 0.5},
    {"n": 15},
    {"n": 2000},
    {"top_share": 0.5},
    {"account_age_d": 30.0},
    {"account_age_d": None},
    {"maker_rebates": 399.0},
])
def test_boundaries_stay_eligible(overrides):
    assert full_exclusion(stats(**overrides), NOW, CFG) is None


def test_early_exclusion_ignores_bet_based_rules():
    assert early_exclusion(stats(n=0, pnl=0.0, top_share=0.9), NOW, CFG) is None


@pytest.mark.parametrize("overrides,flag", [
    ({"top_share": 0.41}, "CONC"),
    ({"account_age_d": 45.0}, "NEW"),
    ({"quiet_gap_h": 2}, "24/7"),
    ({"maker_rebates": 250.0}, "MM?"),  # 0.31% of the amount staked
    ({"n": 250, "roi": 0.01}, "MM?"),  # thin ROI across many bets
    ({"lb_volume": 6_000_000.0, "lb_pnl": 180_000.0}, "MM?"),  # 3% margin: suspicious, not excluded
])
def test_flags(overrides, flag):
    v = evaluate(stats(**overrides), NOW, CFG)
    assert v.eligible and flag in v.flags


def test_describe_flag_includes_the_numbers():
    assert "42%" in describe_flag("CONC", stats(top_share=0.42), CFG)
    assert "45 days" in describe_flag("NEW", stats(account_age_d=45.0), CFG)
    assert "2h" in describe_flag("24/7", stats(quiet_gap_h=2), CFG)
    assert "0.31%" in describe_flag("MM?", stats(maker_rebates=250.0), CFG)
    assert "1.0% ROI across 250 bets" in describe_flag("MM?", stats(n=250, roi=0.01), CFG)
