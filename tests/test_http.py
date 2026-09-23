import time

import httpx
import pytest

from polywatch.api.http import ApiError, Http, RateLimiter

BASE = "https://example.test"


async def test_get_json_returns_parsed_body(respx_mock):
    respx_mock.get(f"{BASE}/x", params={"a": "1"}).mock(return_value=httpx.Response(200, json={"ok": True}))
    http = Http(max_tries=1)
    assert await http.get_json(BASE, "/x", {"a": 1}) == {"ok": True}
    await http.aclose()


async def test_retries_rate_limit_then_succeeds(respx_mock):
    route = respx_mock.get(f"{BASE}/x").mock(side_effect=[httpx.Response(429), httpx.Response(200, json=[1])])
    http = Http(max_tries=3, backoff=0)
    assert await http.get_json(BASE, "/x") == [1]
    assert route.call_count == 2
    await http.aclose()


async def test_retries_transport_errors(respx_mock):
    route = respx_mock.get(f"{BASE}/x").mock(side_effect=[httpx.ConnectError("down"), httpx.Response(200, json=[])])
    http = Http(max_tries=2, backoff=0)
    assert await http.get_json(BASE, "/x") == []
    assert route.call_count == 2
    await http.aclose()


async def test_client_errors_are_not_retried(respx_mock):
    route = respx_mock.get(f"{BASE}/x").mock(return_value=httpx.Response(404, json={"error": "nope"}))
    http = Http(max_tries=5, backoff=0)
    with pytest.raises(ApiError, match="404"):
        await http.get_json(BASE, "/x")
    assert route.call_count == 1
    await http.aclose()


async def test_gives_up_after_max_tries(respx_mock):
    route = respx_mock.get(f"{BASE}/x").mock(return_value=httpx.Response(503))
    http = Http(max_tries=3, backoff=0)
    with pytest.raises(ApiError, match="after 3 tries"):
        await http.get_json(BASE, "/x")
    assert route.call_count == 3
    await http.aclose()


async def test_rate_limiter_spaces_out_bursts():
    limiter = RateLimiter(5, 0.5)
    start = time.monotonic()
    for _ in range(10):
        await limiter.acquire()
    assert time.monotonic() - start >= 0.4


async def test_retries_a_body_that_is_not_json(respx_mock):
    route = respx_mock.get(f"{BASE}/x").mock(side_effect=[httpx.Response(200, text="<html>busy</html>"),
                                                         httpx.Response(200, json=[1])])
    http = Http(max_tries=2, backoff=0)
    assert await http.get_json(BASE, "/x") == [1]
    assert route.call_count == 2
    await http.aclose()
