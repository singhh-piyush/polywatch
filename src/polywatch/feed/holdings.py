"""The user's own open positions, from their public /positions data."""
from __future__ import annotations

from typing import Any

from ..models import Holding

MIN_SHARES = 0.01  # ignore dust left over after selling


def holdings_from_positions(rows: list[dict[str, Any]]) -> dict[str, Holding]:
    held: dict[str, Holding] = {}
    for row in rows:
        try:
            shares = float(row.get("size") or 0)
            avg_price = float(row.get("avgPrice") or 0)
        except (TypeError, ValueError):
            continue
        if shares >= MIN_SHARES and not row.get("redeemable") and row.get("asset"):
            held[str(row["asset"])] = Holding(shares=shares, avg_price=avg_price)
    return held
