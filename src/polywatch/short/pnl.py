"""Each wallet's exact result in one resolved window, worked out from every fill in the market."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

SNIPE_PRICE = 0.95
ARB_BALANCE = 0.4  # the smaller side's dollars as a share of the bigger side's
LEAD_S = 60  # the crowd signal is backtested as it stood this long before a window closed
COPY_DELAY_S = 20  # and bought this much later, as someone copying it by hand would


@dataclass(slots=True)
class WalletWindow:
    """One wallet's trading in one window, and what it made once the window resolved."""

    wallet: str
    name: str = ""
    pnl: float = 0.0
    cost: float = 0.0          # dollars put in: buys plus pairs minted to sell
    side: str = ""             # the outcome it put the most money on; "" if it only sold
    side_price: float = 0.0    # its average buy price on that outcome
    won: bool = False          # that outcome won
    buy_usd: float = 0.0
    snipe_usd: float = 0.0     # buys at 95c or more
    both_sides: bool = False   # bought both outcomes in similar amounts, locking in a result (an arb)
    minted: float = 0.0        # pairs it must have minted to sell more than it bought
    fills: int = 0
    first_buy_s: int | None = None  # seconds before close of its first and last buys
    last_buy_s: int | None = None
    buys: dict[str, float] = field(default_factory=dict)  # outcome -> dollars bought
    early_buys: dict[str, float] = field(default_factory=dict)  # the same, counting only buys LEAD_S before close

    @property
    def edge(self) -> float:
        """How much better it did than the price it paid implied: 1 - price on a win, -price on a loss."""
        return (1.0 if self.won else 0.0) - self.side_price if self.side else 0.0


def _row_key(r: dict[str, Any]) -> tuple[Any, ...]:
    return (r.get("transactionHash"), r.get("proxyWallet"), r.get("asset"), r.get("side"), r.get("size"),
            r.get("price"))


def dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    out = []
    for r in rows:
        key = _row_key(r)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def window_results(rows: list[dict[str, Any]], outcomes: tuple[str, str], winner: str,
                   end_ts: int) -> dict[str, WalletWindow]:
    """Per-wallet results. `rows` are the market's fills from both sides of each match (takerOnly=false).

    Splits don't show up as trades, so a wallet that sold more of an outcome than it bought must have minted
    pairs at $1 each. Merges and redemptions don't matter: at resolution a pair is worth $1 either way.
    """
    cash: dict[str, float] = defaultdict(float)
    shares: dict[str, dict[str, float]] = defaultdict(lambda: dict.fromkeys(outcomes, 0.0))
    buy_shares: dict[str, dict[str, float]] = defaultdict(lambda: dict.fromkeys(outcomes, 0.0))
    results: dict[str, WalletWindow] = {}
    for r in dedupe(rows):
        wallet = str(r.get("proxyWallet") or "").lower()
        outcome = str(r.get("outcome") or "")
        try:
            price, size, ts = float(r["price"]), float(r["size"]), int(float(r.get("timestamp") or 0))
        except (KeyError, TypeError, ValueError):
            continue
        if not wallet or outcome not in outcomes or size <= 0:
            continue
        res = results.setdefault(wallet, WalletWindow(wallet, buys=dict.fromkeys(outcomes, 0.0),
                                                      early_buys=dict.fromkeys(outcomes, 0.0)))
        res.name = res.name or str(r.get("name") or r.get("pseudonym") or "")
        res.fills += 1
        usd = price * size
        if str(r.get("side")).upper() == "BUY":
            cash[wallet] -= usd
            shares[wallet][outcome] += size
            buy_shares[wallet][outcome] += size
            res.buys[outcome] += usd
            res.buy_usd += usd
            if ts <= end_ts - LEAD_S:
                res.early_buys[outcome] += usd
            if price >= SNIPE_PRICE:
                res.snipe_usd += usd
            before = end_ts - ts
            res.first_buy_s = before if res.first_buy_s is None else max(res.first_buy_s, before)
            res.last_buy_s = before if res.last_buy_s is None else min(res.last_buy_s, before)
        else:
            cash[wallet] += usd
            shares[wallet][outcome] -= size
    for wallet, res in results.items():
        held = shares[wallet]
        res.minted = max(0.0, *(-held[o] for o in outcomes))
        # A minted pair costs $1 and pays $1, so minting only shows in the cost.
        res.pnl = cash[wallet] + held[winner]
        res.cost = res.buy_usd + res.minted
        # Buying a little of the other side is an ordinary hedge; similar amounts on both is an arb.
        spent = sorted(res.buys[o] for o in outcomes)
        res.both_sides = spent[0] > 0 and spent[0] >= ARB_BALANCE * spent[1]
        if res.buy_usd > 0:
            res.side = max(outcomes, key=lambda o: res.buys[o])
            res.side_price = res.buys[res.side] / buy_shares[wallet][res.side]
            res.won = res.side == winner
    return results


def price_before(rows: list[dict[str, Any]], outcomes: tuple[str, str], end_ts: int, *, lead_s: int = LEAD_S,
                 last_n: int = 5) -> float | None:
    """The first outcome's price lead_s before close: the average of the last few fills up to then.

    It has to be the price at that moment, not an average over the minute before: traders reacting to a late move
    would otherwise look like they knew in advance. A fill of the second outcome at p counts as the first at 1 - p.
    """
    cutoff = end_ts - lead_s
    recent: list[tuple[int, float]] = []
    for r in rows:
        try:
            price, size, ts = float(r["price"]), float(r["size"]), int(float(r.get("timestamp") or 0))
        except (KeyError, TypeError, ValueError):
            continue
        outcome = str(r.get("outcome") or "")
        if outcome not in outcomes or ts > cutoff or size <= 0:
            continue
        recent.append((ts, price if outcome == outcomes[0] else 1 - price))
    if not recent:
        return None
    recent.sort()
    last = [p for _ts, p in recent[-last_n:]]
    return sum(last) / len(last)
