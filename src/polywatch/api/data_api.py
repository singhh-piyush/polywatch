"""Polymarket Data API: leaderboards, positions and activity."""
from __future__ import annotations

from typing import Any

from ..models import LeaderboardEntry
from .http import DATA_API, Http

PAGE = 50              # server maximum for leaderboard and closed-positions
POSITIONS_PAGE = 500   # server maximum for positions
TRADES_PAGE = 4000         # /trades honours large pages, so most markets come back in one request
TRADES_MAX_OFFSET = 10_000  # the server refuses offsets past this


class DataApi:
    def __init__(self, http: Http) -> None:
        self.http = http

    async def leaderboard(self, period: str, depth: int) -> list[LeaderboardEntry]:
        entries: list[LeaderboardEntry] = []
        for offset in range(0, depth, PAGE):
            rows = await self.http.get_json(
                DATA_API, "/v1/leaderboard",
                {"timePeriod": period, "orderBy": "PNL", "limit": PAGE, "offset": offset},
            )
            entries.extend(
                LeaderboardEntry(
                    wallet=str(r["proxyWallet"]).lower(),
                    username=str(r.get("userName") or ""),
                    pnl=float(r.get("pnl") or 0),
                    volume=float(r.get("vol") or 0),
                )
                for r in rows
                if r.get("proxyWallet")
            )
            if len(rows) < PAGE:
                break
        return entries[:depth]

    async def traded(self, wallet: str) -> int:
        data = await self.http.get_json(DATA_API, "/traded", {"user": wallet})
        return int(data.get("traded") or 0)

    async def closed_positions(self, wallet: str, since_ts: int, max_pages: int) -> tuple[list[dict[str, Any]], bool]:
        """Closed positions newest-first back to `since_ts`. Returns (rows, hit_page_cap)."""
        rows: list[dict[str, Any]] = []
        for page in range(max_pages):
            batch = await self.http.get_json(
                DATA_API, "/closed-positions",
                {"user": wallet, "sortBy": "TIMESTAMP", "sortDirection": "DESC", "limit": PAGE, "offset": page * PAGE},
            )
            rows.extend(r for r in batch if int(r.get("timestamp") or 0) >= since_ts)
            if len(batch) < PAGE or int(batch[-1].get("timestamp") or 0) < since_ts:
                return rows, False
        return rows, True

    async def positions(self, wallet: str, max_pages: int = 20) -> list[dict[str, Any]]:
        """All current positions, including resolved-but-unredeemed ones (sizeThreshold=0)."""
        rows: list[dict[str, Any]] = []
        for page in range(max_pages):
            batch = await self.http.get_json(
                DATA_API, "/positions",
                {"user": wallet, "sizeThreshold": 0, "limit": POSITIONS_PAGE, "offset": page * POSITIONS_PAGE},
            )
            rows.extend(batch)
            if len(batch) < POSITIONS_PAGE:
                break
        return rows

    async def activity(self, wallet: str, *, type_: str = "TRADE", start: int | None = None,
                       limit: int = 500) -> list[dict[str, Any]]:
        """Newest-first activity rows. Unlike /trades, this includes maker (limit-order) fills."""
        params: dict[str, Any] = {"user": wallet, "type": type_, "limit": limit}
        if start is not None:
            params["start"] = start
        return await self.http.get_json(DATA_API, "/activity", params)

    async def market_trades(self, condition_id: str, *, side: str | None = None) -> tuple[list[dict[str, Any]], bool]:
        """Every fill in a market, both sides of each match. Returns (rows, hit_offset_cap)."""
        rows: list[dict[str, Any]] = []
        params: dict[str, Any] = {"market": condition_id, "takerOnly": "false", "limit": TRADES_PAGE}
        if side:
            params["side"] = side
        for offset in range(0, TRADES_MAX_OFFSET, TRADES_PAGE):
            limit = min(TRADES_PAGE, TRADES_MAX_OFFSET - offset)
            batch = await self.http.get_json(DATA_API, "/trades", {**params, "offset": offset, "limit": limit})
            rows.extend(batch)
            if len(batch) < limit:
                return rows, False
        return rows, True
