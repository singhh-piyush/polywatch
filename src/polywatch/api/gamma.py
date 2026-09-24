"""Polymarket Gamma API: public profiles, profile search and market timing."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..models import MarketTiming
from .http import GAMMA_API, ApiError, Http


def _iso_ts(value: Any) -> int | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return int((dt if dt.tzinfo else dt.replace(tzinfo=UTC)).timestamp())


class GammaApi:
    def __init__(self, http: Http) -> None:
        self.http = http

    async def account_created_ts(self, wallet: str) -> int | None:
        try:
            data = await self.http.get_json(GAMMA_API, "/public-profile", {"address": wallet})
        except ApiError:
            return None
        created = data.get("createdAt") if isinstance(data, dict) else None
        if not created:
            return None
        try:
            return int(datetime.fromisoformat(created).timestamp())
        except ValueError:
            return None

    async def search_profiles(self, query: str, limit: int = 5) -> list[tuple[str, str]]:
        data = await self.http.get_json(
            GAMMA_API, "/public-search", {"q": query, "search_profiles": "true", "limit_per_type": limit})
        profiles = data.get("profiles") or [] if isinstance(data, dict) else []
        return [
            (str(p.get("name") or p.get("pseudonym") or ""), str(p["proxyWallet"]).lower())
            for p in profiles
            if p.get("proxyWallet")
        ]

    async def market_timing(self, slug: str) -> MarketTiming | None:
        """When a market starts and ends, and whether it has resolved. None if it can't be found or the API fails."""
        try:
            for extra in ({}, {"closed": "true"}):  # the default listing leaves out resolved markets
                rows = await self.http.get_json(GAMMA_API, "/markets", {"slug": slug, **extra})
                if isinstance(rows, list) and rows and isinstance(rows[0], dict):
                    market = rows[0]
                    return MarketTiming(start_ts=_iso_ts(market.get("gameStartTime")),
                                        end_ts=_iso_ts(market.get("endDate")), closed=bool(market.get("closed")))
        except ApiError:
            return None
        return None
