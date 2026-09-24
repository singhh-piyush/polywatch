"""The user's own positions, from their public /positions data."""
from __future__ import annotations

from typing import Any

from ..models import Holding, MyPosition

MIN_SHARES = 0.01  # ignore dust left over after selling
MIN_VALUE = 0.01   # a resolved position worth less than this lost, and there is nothing to redeem


def my_positions(rows: list[dict[str, Any]]) -> list[MyPosition]:
    """Open positions, plus resolved ones that still pay out when redeemed. Lost positions are left out."""
    positions = []
    for row in rows:
        try:
            shares = float(row.get("size") or 0)
            avg_price = float(row.get("avgPrice") or 0)
            cur_price = float(row.get("curPrice") or 0)
        except (TypeError, ValueError):
            continue
        redeemable = bool(row.get("redeemable"))
        if shares < MIN_SHARES or not row.get("asset") or (redeemable and shares * cur_price < MIN_VALUE):
            continue
        positions.append(MyPosition(
            asset=str(row["asset"]), title=str(row.get("title") or ""), outcome=str(row.get("outcome") or ""),
            slug=str(row.get("slug") or ""), event_slug=str(row.get("eventSlug") or ""), shares=shares,
            avg_price=avg_price, cur_price=cur_price, redeemable=redeemable,
        ))
    return positions


def holdings_from_positions(rows: list[dict[str, Any]]) -> dict[str, Holding]:
    return {p.asset: Holding(shares=p.shares, avg_price=p.avg_price) for p in my_positions(rows) if not p.redeemable}


def live_price(position: MyPosition, prices: dict[str, float]) -> float:
    """The midpoint while the market trades; once it has resolved, the payout per share."""
    return position.cur_price if position.redeemable else prices.get(position.asset, position.cur_price)


def ordered(positions: list[MyPosition], prices: dict[str, float]) -> list[MyPosition]:
    """Positions to redeem first, then the biggest by current value."""
    return sorted(positions, key=lambda p: (not p.redeemable, -p.shares * live_price(p, prices)))


def totals(positions: list[MyPosition], prices: dict[str, float]) -> tuple[float, float]:
    """(current value, cost) of all the positions."""
    return (sum(p.shares * live_price(p, prices) for p in positions), sum(p.cost for p in positions))
