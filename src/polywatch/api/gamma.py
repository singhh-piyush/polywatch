"""Polymarket Gamma API: public profiles, profile search and market timing."""
from __future__ import annotations

import json
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
                rows = await self.http.get_json(GAMMA_API, "/markets", {"slug": slug, "include_tag": "true", **extra})
                if isinstance(rows, list) and rows and isinstance(rows[0], dict):
                    market = rows[0]
                    return MarketTiming(start_ts=_iso_ts(market.get("gameStartTime")),
                                        end_ts=_iso_ts(market.get("endDate")), closed=bool(market.get("closed")),
                                        category=category_of(market), icon=str(market.get("icon") or ""))
        except ApiError:
            return None
        return None

    async def markets_by_slug(self, slugs: list[str]) -> dict[str, dict[str, Any]]:
        """Market rows for a batch of slugs: one request for resolved markets, one more for any still open."""
        found: dict[str, dict[str, Any]] = {}
        if not slugs:
            return found
        for extra in ({"closed": "true"}, {}):
            wanted = [s for s in slugs if s not in found]
            if not wanted:
                break
            rows = await self.http.get_json(GAMMA_API, "/markets", [("slug", s) for s in wanted] + [("limit", len(wanted)), *extra.items()])
            for row in rows if isinstance(rows, list) else []:
                if isinstance(row, dict) and row.get("slug"):
                    found[str(row["slug"])] = row
        return found


# Polymarket's top-level sections; a market's tags list one of these alongside finer ones ("mlb", "pennant").
CATEGORIES = ("Sports", "Politics", "Crypto", "Finance", "Economy", "Tech", "AI", "Culture", "Pop Culture",
              "Geopolitics", "World", "Elections", "Weather", "Science", "Business", "Esports", "Mentions")


def category_of(market: dict[str, Any]) -> str:
    labels = [str(t.get("label") or "") for t in market.get("tags") or [] if isinstance(t, dict)]
    for label in labels:
        if label in CATEGORIES:
            return "Culture" if label == "Pop Culture" else label
    return ""  # shown as "Other"


def json_list(value: Any) -> list[Any]:
    """Gamma returns some lists as JSON-encoded strings."""
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def winner_of(market: dict[str, Any], *, sure: float = 0.99) -> str | None:
    """The winning outcome once a market is settled (or its price is pinned at 99c+ past its end date)."""
    outcomes = [str(o) for o in json_list(market.get("outcomes"))]
    try:
        prices = [float(p) for p in json_list(market.get("outcomePrices"))]
    except (TypeError, ValueError):
        return None
    if len(outcomes) != 2 or len(prices) != 2:
        return None
    best = max(range(2), key=lambda i: prices[i])
    return outcomes[best] if prices[best] >= sure else None
