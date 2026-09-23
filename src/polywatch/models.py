"""Plain data types shared across polywatch."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class LeaderboardEntry:
    wallet: str
    username: str
    pnl: float
    volume: float


@dataclass(frozen=True, slots=True)
class ResolvedBet:
    """One market position that resolved (or was fully sold) inside the scoring window."""

    asset: str
    slug: str
    avg_price: float
    cost: float
    pnl: float
    resolved_ts: int
    voided: bool = False  # the market resolved 50/50, so nothing was predicted


@dataclass(frozen=True, slots=True)
class TraderStats:
    wallet: str
    username: str = ""
    lb_pnl: float = 0.0
    lb_volume: float = 0.0
    markets_traded: int = 0
    last_trade_ts: int = 0
    short_share: float = 0.0
    quiet_gap_h: int | None = None
    fast_share: float | None = None
    n: int = 0
    wins: int = 0
    win_rate: float = 0.0
    mean_price: float = 0.0
    edge: float = 0.0
    roi: float = 0.0
    pnl: float = 0.0
    staked: float = 0.0
    top_share: float = 0.0
    median_bet: float = 0.0
    void_share: float = 0.0
    truncated: bool = False
    maker_rebates: float = 0.0
    account_age_d: float | None = None


@dataclass(frozen=True, slots=True)
class Verdict:
    excluded: str | None = None
    flags: tuple[str, ...] = ()

    @property
    def eligible(self) -> bool:
        return self.excluded is None


@dataclass(slots=True)
class RankedTrader:
    stats: TraderStats
    verdict: Verdict
    score: float = 0.0
    rank: int = 0


@dataclass(frozen=True, slots=True)
class Trade:
    """A single fill. The websocket and /activity use the same field names."""

    wallet: str
    side: str
    asset: str
    condition_id: str
    price: float
    size: float
    ts: int
    title: str
    outcome: str
    slug: str
    event_slug: str
    tx_hash: str
    name: str = ""

    @property
    def usd(self) -> float:
        return self.price * self.size

    @property
    def dedupe_key(self) -> tuple[str, str, str, str, float, float]:
        # One transaction can hold several fills of the same size at different prices.
        return (self.wallet, self.tx_hash, self.asset, self.side, round(self.size, 4), round(self.price, 4))

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> Trade | None:
        try:
            price = float(d.get("price") or 0)
            size = float(d.get("size") or 0)
            ts = int(float(d.get("timestamp") or 0))
        except (TypeError, ValueError):
            return None
        side = str(d.get("side") or "").upper()
        wallet = str(d.get("proxyWallet") or "").lower()
        if side not in ("BUY", "SELL") or price <= 0 or size <= 0 or not wallet:
            return None
        if ts > 10**11:  # milliseconds
            ts //= 1000
        return cls(
            wallet=wallet,
            side=side,
            asset=str(d.get("asset") or ""),
            condition_id=str(d.get("conditionId") or ""),
            price=price,
            size=size,
            ts=ts,
            title=str(d.get("title") or ""),
            outcome=str(d.get("outcome") or ""),
            slug=str(d.get("slug") or ""),
            event_slug=str(d.get("eventSlug") or ""),
            tx_hash=str(d.get("transactionHash") or ""),
            name=str(d.get("name") or d.get("pseudonym") or ""),
        )


@dataclass(frozen=True, slots=True)
class WatchedTrader:
    wallet: str
    name: str
    rank: int | None
    win_rate: float | None
    median_bet: float
    pinned: bool


@dataclass(slots=True)
class FeedItem:
    """One order as shown in the feed: the fills of (wallet, asset, side) that landed close together."""

    key: str
    wallet: str
    name: str
    side: str
    asset: str
    title: str
    outcome: str
    slug: str
    event_slug: str
    first_ts: int
    last_ts: int
    shares: float = 0.0
    usd: float = 0.0
    fills: int = 0
    conviction: float | None = None
    fast: str | None = None  # why copying this buy gains nothing, e.g. "95¢+" or "both sides"
    tx_hashes: list[str] = field(default_factory=list)
    notified: bool = False

    @property
    def avg_price(self) -> float:
        return self.usd / self.shares if self.shares else 0.0
