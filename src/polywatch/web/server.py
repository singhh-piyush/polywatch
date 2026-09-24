"""The local web server: the page, a snapshot endpoint, a live event stream and a few actions."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from ..api.http import Http
from ..config import Settings, short_db_path
from ..short.live import ShortService
from ..short.store import ShortStore
from ..store import Store
from .bus import EventBus, sse_frame
from .engine import LiveEngine

log = logging.getLogger(__name__)

STATIC = Path(__file__).parent / "static"
HEARTBEAT_S = 15


class Services:
    """Everything the routes need, started and stopped with the app."""

    def __init__(self, cfg: Settings, *, store: Store | None = None, short_store: ShortStore | None = None,
                 http: Http | None = None, network: bool = True, **engine_kw: Any) -> None:
        self.cfg = cfg
        self.network = network
        self.bus = EventBus()
        self.store = store or Store(cfg.db_path)
        self.short_store = short_store or ShortStore(short_db_path(cfg.db_path))
        self.http = http or Http()
        self.engine = LiveEngine(cfg, store=self.store, http=self.http, bus=self.bus,
                                 on_any_trade=self._on_any_trade, **engine_kw)
        self.short = ShortService(cfg, store=self.short_store, data=self.engine.data, gamma=self.engine.gamma,
                                  bus=self.bus, notify=self.engine.notifier.notify)

    def _on_any_trade(self, trade: Any) -> None:
        self.short.on_trade(trade)

    async def start(self) -> None:
        self.engine.start(network=self.network)
        if self.network:
            self.short.start()  # loads the leaderboard in the background, so the page is up right away

    async def stop(self) -> None:
        await self.short.stop()
        await self.engine.stop()
        await self.http.aclose()
        self.short_store.close()
        self.store.close()


def _json_body(data: Any) -> dict[str, Any]:
    return data if isinstance(data, dict) else {}


def create_app(services: Services) -> Starlette:
    engine, short = services.engine, services.short

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        await services.start()
        try:
            yield
        finally:
            await services.stop()

    async def index(_request: Request) -> Response:
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    async def snapshot(_request: Request) -> Response:
        return JSONResponse({**engine.snapshot(), "short": short.snapshot()})

    async def events(request: Request) -> Response:
        queue = services.bus.subscribe()

        async def stream() -> AsyncIterator[str]:
            try:
                yield sse_frame("hello", {"ok": True})
                while True:
                    try:
                        frame = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_S)
                    except TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    if frame is None or await request.is_disconnected():
                        break
                    yield frame
            finally:
                services.bus.unsubscribe(queue)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    async def override(request: Request) -> Response:
        body = _json_body(await request.json())
        wallet = str(body.get("wallet") or "").lower()
        mode = body.get("mode")
        if not wallet or mode not in ("pin", "ban", None):
            return JSONResponse({"error": "wallet and mode (pin, ban or null) are required"}, status_code=400)
        engine.set_override(wallet, mode, str(body.get("name") or ""))
        return JSONResponse({"ok": True})

    async def resolve(request: Request) -> Response:
        body = _json_body(await request.json())
        try:
            matches = await engine.resolve_trader(str(body.get("text") or ""))
        except Exception as exc:
            return JSONResponse({"error": f"Search failed: {exc}"}, status_code=502)
        return JSONResponse({"matches": [{"name": n, "wallet": w} for n, w in matches]})

    async def account(request: Request) -> Response:
        body = _json_body(await request.json())
        wallet = str(body.get("wallet") or "").lower() or None
        engine.set_account(wallet, str(body.get("name") or ""))
        return JSONResponse({"ok": True})

    async def rescan(_request: Request) -> Response:
        return JSONResponse({"started": engine.rescan()})

    async def alerts(request: Request) -> Response:
        body = _json_body(await request.json())
        engine.notifier.enabled = bool(body.get("enabled"))
        return JSONResponse({"ok": True})

    async def short_board(request: Request) -> Response:
        q = request.query_params
        coins = tuple(c for c in q.get("coins", "").split(",") if c)
        intervals = tuple(i for i in q.get("intervals", "").split(",") if i)
        min_windows = int(q["min_windows"]) if q.get("min_windows", "").isdigit() else None
        rows = await short.board_json(q.get("period", "7d"), coins, intervals, min_windows)
        return JSONResponse({"rows": rows})

    async def short_pref(request: Request) -> Response:
        body = _json_body(await request.json())
        wallet = str(body.get("wallet") or "").lower()
        if not wallet:
            return JSONResponse({"error": "wallet is required"}, status_code=400)
        star = body.get("star")
        bell = body.get("bell")
        short.set_pref(wallet, star=None if star is None else bool(star), bell=None if bell is None else bool(bell))
        return JSONResponse({"ok": True})

    async def trader_positions(request: Request) -> Response:
        wallet = request.path_params["wallet"].lower()
        try:
            rows = await engine.data.positions(wallet, max_pages=2)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        keep = ("title", "outcome", "avgPrice", "curPrice", "currentValue", "cashPnl", "redeemable", "slug",
                "eventSlug", "icon")
        open_rows = [{k: r.get(k) for k in keep} for r in rows
                     if not r.get("redeemable") and float(r.get("currentValue") or 0) > 0]
        open_rows.sort(key=lambda r: -float(r.get("currentValue") or 0))
        return JSONResponse({"positions": open_rows[:25]})

    routes = [
        Route("/", index),
        Route("/api/snapshot", snapshot),
        Route("/api/events", events),
        Route("/api/override", override, methods=["POST"]),
        Route("/api/resolve", resolve, methods=["POST"]),
        Route("/api/account", account, methods=["POST"]),
        Route("/api/rescan", rescan, methods=["POST"]),
        Route("/api/alerts", alerts, methods=["POST"]),
        Route("/api/short/board", short_board),
        Route("/api/short/pref", short_pref, methods=["POST"]),
        Route("/api/trader/{wallet}/positions", trader_positions),
        Mount("/static", StaticFiles(directory=STATIC), name="static"),
    ]
    return Starlette(routes=routes, lifespan=lifespan)


def serve(cfg: Settings, *, open_browser: bool = True) -> None:
    import socket
    import threading
    import time
    import webbrowser

    import uvicorn

    app = create_app(Services(cfg))
    url = f"http://127.0.0.1:{cfg.web_port}/"

    def open_when_up() -> None:
        """Open the browser once the server accepts connections, not before."""
        for _ in range(600):
            try:
                with socket.create_connection(("127.0.0.1", cfg.web_port), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.1)
        webbrowser.open(url)

    if open_browser:
        threading.Thread(target=open_when_up, daemon=True).start()
    print(f"polywatch is starting at {url}  (Ctrl+C to stop)")
    uvicorn.run(app, host="127.0.0.1", port=cfg.web_port, log_level="warning")
