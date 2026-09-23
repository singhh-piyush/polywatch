import httpx

from polywatch.api.data_api import DataApi
from polywatch.api.http import DATA_API
from tests.factories import load_fixture


def rows(prefix, count, **extra):
    return [{"proxyWallet": f"0xW{prefix}{i}", "userName": f"u{i}", "pnl": 1.0, "vol": 2.0, **extra} for i in range(count)]


async def test_leaderboard_paginates_until_a_short_page(respx_mock, http):
    def page(request):
        offset = int(request.url.params["offset"])
        return httpx.Response(200, json=rows(offset, 50 if offset == 0 else 10))
    route = respx_mock.get(f"{DATA_API}/v1/leaderboard").mock(side_effect=page)
    entries = await DataApi(http).leaderboard("MONTH", 1000)
    assert len(entries) == 60 and route.call_count == 2
    assert entries[0].wallet == "0xw00"
    assert route.calls[0].request.url.params["timePeriod"] == "MONTH"
    assert route.calls[0].request.url.params["orderBy"] == "PNL"


async def test_leaderboard_respects_depth(respx_mock, http):
    respx_mock.get(f"{DATA_API}/v1/leaderboard").mock(return_value=httpx.Response(200, json=rows("x", 50)))
    assert len(await DataApi(http).leaderboard("WEEK", 60)) == 60


async def test_leaderboard_parses_recorded_page(respx_mock, http):
    recorded = load_fixture("leaderboard.json")
    respx_mock.get(f"{DATA_API}/v1/leaderboard").mock(return_value=httpx.Response(200, json=recorded))
    [first, *_] = await DataApi(http).leaderboard("MONTH", 3)
    assert first.wallet == recorded[0]["proxyWallet"].lower()
    assert first.pnl == float(recorded[0]["pnl"]) and first.volume == float(recorded[0]["vol"])


async def test_closed_positions_stop_at_window_edge(respx_mock, http):
    page = [{"asset": f"a{i}", "timestamp": 100 - i} for i in range(50)]  # timestamps 100..51
    route = respx_mock.get(f"{DATA_API}/closed-positions").mock(return_value=httpx.Response(200, json=page))
    got, truncated = await DataApi(http).closed_positions("0xa", since_ts=75, max_pages=10)
    assert [r["timestamp"] for r in got] == list(range(100, 74, -1))
    assert not truncated and route.call_count == 1
    assert route.calls[0].request.url.params["sortBy"] == "TIMESTAMP"


async def test_closed_positions_report_truncation(respx_mock, http):
    page = [{"asset": f"a{i}", "timestamp": 10_000} for i in range(50)]
    route = respx_mock.get(f"{DATA_API}/closed-positions").mock(return_value=httpx.Response(200, json=page))
    got, truncated = await DataApi(http).closed_positions("0xa", since_ts=0, max_pages=2)
    assert len(got) == 100 and truncated and route.call_count == 2


async def test_positions_paginate(respx_mock, http):
    def page(request):
        offset = int(request.url.params["offset"])
        return httpx.Response(200, json=[{"asset": str(i)} for i in range(500 if offset == 0 else 3)])
    route = respx_mock.get(f"{DATA_API}/positions").mock(side_effect=page)
    assert len(await DataApi(http).positions("0xa")) == 503
    assert route.calls[0].request.url.params["sizeThreshold"] == "0"


async def test_activity_passes_start_only_when_given(respx_mock, http):
    route = respx_mock.get(f"{DATA_API}/activity").mock(return_value=httpx.Response(200, json=[]))
    api = DataApi(http)
    await api.activity("0xa")
    assert "start" not in route.calls[-1].request.url.params
    await api.activity("0xa", type_="MAKER_REBATE", start=123)
    params = route.calls[-1].request.url.params
    assert params["start"] == "123" and params["type"] == "MAKER_REBATE" and params["user"] == "0xa"


async def test_traded(respx_mock, http):
    respx_mock.get(f"{DATA_API}/traded").mock(return_value=httpx.Response(200, json={"user": "0xa", "traded": 45}))
    assert await DataApi(http).traded("0xa") == 45
