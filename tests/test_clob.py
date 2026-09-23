import httpx

from polywatch.api.clob import ClobApi
from polywatch.api.http import CLOB_API


async def test_midpoint_parses_and_caches(respx_mock, http):
    route = respx_mock.get(f"{CLOB_API}/midpoint").mock(return_value=httpx.Response(200, json={"mid": "0.26"}))
    now = [100.0]
    clob = ClobApi(http, ttl=15, clock=lambda: now[0])
    assert await clob.midpoint("t1") == 0.26
    assert await clob.midpoint("t1") == 0.26
    assert route.call_count == 1
    now[0] += 16
    await clob.midpoint("t1")
    assert route.call_count == 2


async def test_midpoint_is_none_without_orderbook(respx_mock, http):
    respx_mock.get(f"{CLOB_API}/midpoint").mock(
        return_value=httpx.Response(404, json={"error": "No orderbook exists for the requested token id"}))
    assert await ClobApi(http).midpoint("t1") is None
