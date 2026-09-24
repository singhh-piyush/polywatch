from datetime import datetime

import httpx

from polywatch.api.gamma import GammaApi
from polywatch.api.http import GAMMA_API
from polywatch.models import MarketTiming
from tests.factories import load_fixture


async def test_account_created_ts_parses_recorded_profile(respx_mock, http):
    profile = load_fixture("public_profile.json")
    respx_mock.get(f"{GAMMA_API}/public-profile").mock(return_value=httpx.Response(200, json=profile))
    expected = int(datetime.fromisoformat(profile["createdAt"]).timestamp())
    assert await GammaApi(http).account_created_ts("0xa") == expected


async def test_account_created_ts_is_none_for_unknown_wallet(respx_mock, http):
    respx_mock.get(f"{GAMMA_API}/public-profile").mock(return_value=httpx.Response(404, json={}))
    assert await GammaApi(http).account_created_ts("0xa") is None


async def test_search_profiles(respx_mock, http):
    body = {"profiles": [{"name": "Fredi", "proxyWallet": "0xABC"}, {"pseudonym": "Anon", "proxyWallet": "0xDEF"},
                         {"name": "no-wallet"}]}
    route = respx_mock.get(f"{GAMMA_API}/public-search").mock(return_value=httpx.Response(200, json=body))
    assert await GammaApi(http).search_profiles("fredi") == [("Fredi", "0xabc"), ("Anon", "0xdef")]
    assert route.calls[0].request.url.params["search_profiles"] == "true"


async def test_search_profiles_without_matches(respx_mock, http):
    respx_mock.get(f"{GAMMA_API}/public-search").mock(
        return_value=httpx.Response(200, json={"pagination": {"hasMore": False, "totalResults": 0}}))
    assert await GammaApi(http).search_profiles("nobody") == []


GAME = {"slug": "wta-ma-birrell-2026-09-23", "gameStartTime": "2026-09-24 03:00:00+00",
        "endDate": "2026-10-01T01:00:00Z", "closed": False}


def ts(iso):
    return int(datetime.fromisoformat(iso).timestamp())


async def test_market_timing_for_a_sports_market(respx_mock, http):
    route = respx_mock.get(f"{GAMMA_API}/markets").mock(return_value=httpx.Response(200, json=[GAME]))
    timing = await GammaApi(http).market_timing(GAME["slug"])
    assert timing == MarketTiming(start_ts=ts("2026-09-24T03:00:00+00:00"), end_ts=ts("2026-10-01T01:00:00+00:00"),
                                  closed=False)
    assert route.calls[0].request.url.params["slug"] == GAME["slug"]


async def test_market_timing_without_a_game_start(respx_mock, http):
    market = {"endDate": "2026-11-05T04:59:00Z", "gameStartTime": None, "closed": False}
    respx_mock.get(f"{GAMMA_API}/markets").mock(return_value=httpx.Response(200, json=[market]))
    timing = await GammaApi(http).market_timing("mo-05")
    assert timing.start_ts is None and timing.end_ts == ts("2026-11-05T04:59:00+00:00")


async def test_resolved_markets_are_found_in_the_closed_listing(respx_mock, http):
    def listing(request):
        closed = request.url.params.get("closed") == "true"
        return httpx.Response(200, json=[dict(GAME, closed=True)] if closed else [])

    respx_mock.get(f"{GAMMA_API}/markets").mock(side_effect=listing)
    timing = await GammaApi(http).market_timing(GAME["slug"])
    assert timing is not None and timing.closed


async def test_market_timing_is_none_when_unknown_or_failing(respx_mock, http):
    respx_mock.get(f"{GAMMA_API}/markets").mock(return_value=httpx.Response(200, json=[]))
    assert await GammaApi(http).market_timing("nope") is None
    respx_mock.get(f"{GAMMA_API}/markets").mock(return_value=httpx.Response(500))
    assert await GammaApi(http).market_timing("nope") is None
