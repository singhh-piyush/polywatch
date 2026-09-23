import pytest

from polywatch.markets import is_excluded_market, market_url, profile_url


@pytest.mark.parametrize("slug,excluded", [
    ("btc-updown-5m-1790183100", True),
    ("eth-updown-15m-1790183100", True),
    ("bitcoin-up-or-down-september-23-2026-1pm-et", False),
    ("btc-updown-1h-1790183100", False),
    ("mlb-tor-bal-2026-09-22-total-7pt5", False),
    ("", False),
])
def test_is_excluded_market(slug, excluded):
    assert is_excluded_market(slug) is excluded


def test_market_url():
    assert market_url("mlb-tor-bal-2026-09-22", "mlb-tor-bal-2026-09-22-total-7pt5") == \
        "https://polymarket.com/event/mlb-tor-bal-2026-09-22/mlb-tor-bal-2026-09-22-total-7pt5"
    assert market_url("fed-oct", "fed-oct") == "https://polymarket.com/event/fed-oct"
    assert market_url("", "solo") == "https://polymarket.com/event/solo"


def test_profile_url():
    assert profile_url("0xabc") == "https://polymarket.com/profile/0xabc"
