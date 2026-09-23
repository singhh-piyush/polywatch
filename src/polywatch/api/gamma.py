"""Polymarket Gamma API: public profiles and profile search."""
from __future__ import annotations

from datetime import datetime

from .http import GAMMA_API, ApiError, Http


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
