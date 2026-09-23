"""Polymarket real-time trade stream (every trade on the site, taker side)."""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable

from websockets.asyncio.client import ClientConnection, connect

from ..models import Trade

log = logging.getLogger(__name__)

RTDS_URL = "wss://ws-live-data.polymarket.com"
SUBSCRIBE = json.dumps({"action": "subscribe", "subscriptions": [{"topic": "activity", "type": "trades"}]})


def parse_message(raw: str | bytes) -> Trade | None:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    raw = raw.strip()
    if not raw.startswith("{"):
        return None  # "PONG" and other keepalive noise
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        log.debug("unparseable stream message: %.200s", raw)
        return None
    if not isinstance(msg, dict) or msg.get("topic") != "activity" or msg.get("type") != "trades":
        return None
    payload = msg.get("payload")
    return Trade.from_api(payload) if isinstance(payload, dict) else None


async def _keepalive(ws: ClientConnection, interval: float) -> None:
    try:
        while True:
            await asyncio.sleep(interval)
            await ws.send("PING")
    except Exception:  # connection gone; the reader notices and reconnects
        return


async def stream_trades(url: str = RTDS_URL, *, on_status: Callable[[str], None] | None = None,
                        ping_interval: float = 5.0, idle_timeout: float = 30.0, min_backoff: float = 1.0,
                        max_backoff: float = 30.0) -> AsyncIterator[Trade]:
    status = on_status or (lambda _s: None)
    backoff = min_backoff
    while True:
        status("connecting")
        try:
            async with connect(url, open_timeout=15) as ws:
                await ws.send(SUBSCRIBE)
                status("live")
                backoff = min_backoff
                keepalive = asyncio.create_task(_keepalive(ws, ping_interval))
                loop = asyncio.get_running_loop()
                # The site trades dozens of times a second, so a connection with no trades for idle_timeout is dead,
                # even if it still answers keepalives.
                deadline = loop.time() + idle_timeout
                try:
                    while True:
                        raw = await asyncio.wait_for(ws.recv(), timeout=max(deadline - loop.time(), 0.0))
                        trade = parse_message(raw)
                        if trade is not None:
                            deadline = loop.time() + idle_timeout
                            yield trade
                finally:
                    keepalive.cancel()
        except TimeoutError:
            log.warning("no trades on the stream for %.0fs; reconnecting", idle_timeout)
        except Exception as exc:
            log.warning("trade stream disconnected: %s", exc)
        status("reconnecting")
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, max_backoff)
