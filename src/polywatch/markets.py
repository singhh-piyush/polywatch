"""Market-level rules and Polymarket URLs."""
from __future__ import annotations

import re

# Recurring 5-minute / 15-minute markets ("btc-updown-5m-1790183100"). Hourly markets use a
# different slug ("bitcoin-up-or-down-september-23-2026-1pm-et") and are deliberately kept.
_SHORT_MARKET = re.compile(r"-updown-\d+m-")


def is_excluded_market(slug: str) -> bool:
    return bool(_SHORT_MARKET.search(slug or ""))


def market_url(event_slug: str, slug: str) -> str:
    if event_slug and slug and event_slug != slug:
        return f"https://polymarket.com/event/{event_slug}/{slug}"
    return f"https://polymarket.com/event/{event_slug or slug}"


def profile_url(wallet: str) -> str:
    return f"https://polymarket.com/profile/{wallet}"
