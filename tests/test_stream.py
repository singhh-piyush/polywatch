import asyncio
import json

from websockets.asyncio.server import serve

from polywatch.api.stream import SUBSCRIBE, parse_message, stream_trades
from tests.factories import load_fixture

RECORDED = load_fixture("rtds_trade.json")


def message(wallet="0xABC", tx="0xtx1"):
    payload = dict(RECORDED["payload"], proxyWallet=wallet, transactionHash=tx)
    return json.dumps({"topic": "activity", "type": "trades", "payload": payload, "timestamp": 1790183658173})


def test_parse_message_reads_recorded_trade():
    t = parse_message(json.dumps(RECORDED))
    assert t is not None and t.wallet == RECORDED["payload"]["proxyWallet"].lower()


def test_parse_message_ignores_noise():
    assert parse_message("PONG") is None
    assert parse_message("") is None
    assert parse_message("{not json") is None
    assert parse_message(json.dumps({"topic": "comments", "type": "x", "payload": {}})) is None
    assert parse_message(message().encode()) is not None


async def test_stream_subscribes_parses_and_reconnects():
    connections, subscriptions = 0, []

    async def handler(ws):
        nonlocal connections
        connections += 1
        subscriptions.append(json.loads(await ws.recv()))
        await ws.send("PONG")
        await ws.send(message(tx=f"0xtx{connections}"))
        # returning closes the connection and forces the client to reconnect

    statuses, got = [], []
    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        stream = stream_trades(f"ws://127.0.0.1:{port}", on_status=statuses.append, min_backoff=0.01)
        async for trade in stream:
            got.append(trade)
            if len(got) == 2:
                break
        await stream.aclose()

    assert connections == 2
    assert subscriptions[0] == json.loads(SUBSCRIBE)
    assert [t.tx_hash for t in got] == ["0xtx1", "0xtx2"]
    assert got[0].wallet == "0xabc"
    assert statuses[:2] == ["connecting", "live"] and "reconnecting" in statuses


async def test_stream_sends_text_keepalive():
    pinged = asyncio.Event()

    async def handler(ws):
        await ws.recv()
        async for msg in ws:
            if msg == "PING":
                pinged.set()
                await ws.send(message())

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        stream = stream_trades(f"ws://127.0.0.1:{port}", ping_interval=0.05, min_backoff=0.01)
        trade = await asyncio.wait_for(anext(stream), timeout=5)
        await stream.aclose()

    assert pinged.is_set() and trade is not None
