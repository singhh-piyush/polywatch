from datetime import datetime

import httpx

from polywatch.api.gamma import GammaApi
from polywatch.api.http import GAMMA_API
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
