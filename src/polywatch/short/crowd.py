"""Crowd probability for an open window: the market's price, nudged toward the side proven winners are on.

    logit(P) = logit(market price) + k * sum(sign * skill * size)

- skill: the trader's shrunk edge (win rate minus the price paid) on short markets, capped at MAX_SKILL
- size: how big the bet is for them, log-scaled: their usual bet counts about 0.3, ten times it counts 1
- k: fitted on indexed history, and judged on windows it wasn't fitted on
"""
from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from ..config import Settings
from .board import BoardRow, board
from .pnl import COPY_DELAY_S

MAX_SKILL = 0.25
K_GRID = [x / 2 for x in range(0, 201)]  # 0 .. 100
LEAN_PTS = 0.10  # a "lean" is the model moving this far from the market price
EPS = 1e-4
TEST_DAYS = 2
MIN_SAMPLES = 200  # windows judged on before the model may claim to beat the market


@dataclass(frozen=True, slots=True)
class Member:
    wallet: str
    skill: float
    median_bet: float


def crowd_members(rows: Iterable[BoardRow], n: int) -> dict[str, Member]:
    """The traders whose bets count: not bots, beating the odds, the n with the most edge."""
    good = sorted((r for r in rows if not r.is_bot and r.edge > 0 and r.directional > 0), key=lambda r: -r.edge)
    return {r.wallet: Member(r.wallet, min(r.edge, MAX_SKILL), r.median_bet) for r in good[:n]}


def size_weight(usd: float, median_bet: float) -> float:
    if usd <= 0:
        return 0.0
    base = median_bet if median_bet > 0 else usd
    return min(max(math.log10(1 + usd / base), 0.0), 1.0)


def signal(stances: dict[str, tuple[float, float]], members: dict[str, Member]) -> tuple[float, int, int]:
    """(signal toward the first outcome, members on it, members on the other). Stances are dollars on each side."""
    total, first, second = 0.0, 0, 0
    for wallet, (a, b) in stances.items():
        member = members.get(wallet)
        if member is None or a == b:
            continue
        sign = 1 if a > b else -1
        total += sign * member.skill * size_weight(abs(a - b), member.median_bet)
        first += sign > 0
        second += sign < 0
    return total, first, second


def _logit(p: float) -> float:
    p = min(max(p, EPS), 1 - EPS)
    return math.log(p / (1 - p))


def probability(price: float, sig: float, k: float) -> float:
    return 1 / (1 + math.exp(-(_logit(price) + k * sig)))


def _log_loss(p: float, won: bool) -> float:
    p = min(max(p, EPS), 1 - EPS)
    return -math.log(p if won else 1 - p)


@dataclass(frozen=True, slots=True)
class Sample:
    price: float     # the first outcome's price a minute before close
    signal: float
    first_won: bool
    interval: str
    copy_price: float | None = None  # its price a little later, when a person could have copied the signal


def samples(db: sqlite3.Connection, members: dict[str, Member], since_ts: int, until_ts: int) -> list[Sample]:
    """Resolved windows in [since, until) where at least one crowd member had a side a minute before close."""
    windows = {slug: (price, winner == json.loads(outcomes)[0], interval, copy)
               for slug, price, winner, outcomes, interval, copy in db.execute(
                   "SELECT slug, price_before, winner, outcomes, interval, price_copy FROM short_windows "
                   "WHERE winner IS NOT NULL AND price_before IS NOT NULL AND end_ts >= ? AND end_ts < ?",
                   (since_ts, until_ts))}
    stances: dict[str, dict[str, tuple[float, float]]] = defaultdict(dict)
    if members:
        marks = ",".join("?" * len(members))
        for slug, wallet, a, b in db.execute(
                f"SELECT sw.slug, w.wallet, r.early0, r.early1 FROM short_results r "
                f"JOIN short_wallets w ON w.id = r.wallet_id JOIN short_windows sw ON sw.id = r.window_id "
                f"WHERE r.end_ts >= ? AND r.end_ts < ? AND w.wallet IN ({marks}) AND (r.early0 > 0 OR r.early1 > 0)",
                (since_ts, until_ts, *members)):
            stances[slug][wallet] = (a, b)
    out = []
    for slug, per in stances.items():
        if slug not in windows:
            continue
        price, first_won, interval, copy = windows[slug]
        sig, _, _ = signal(per, members)
        if sig != 0 and 0 < price < 1:
            out.append(Sample(price, sig, first_won, interval, copy))
    return out


def fit_k(train: list[Sample]) -> float:
    best_k, best = 0.0, math.inf
    for k in K_GRID:
        loss = sum(_log_loss(probability(s.price, s.signal, k), s.first_won) for s in train)
        if loss < best:
            best_k, best = k, loss
    return best_k


def evaluate(test: list[Sample], k: float) -> dict[str, float | int]:
    """How the model did on windows it wasn't fitted on, next to the market price alone."""
    n = len(test)
    if n == 0:
        return {"n": 0}
    market_loss = sum(_log_loss(s.price, s.first_won) for s in test) / n
    model_loss = sum(_log_loss(probability(s.price, s.signal, k), s.first_won) for s in test) / n
    market_hits = sum((s.price > 0.5) == s.first_won for s in test if s.price != 0.5)
    model_hits = sum((probability(s.price, s.signal, k) > 0.5) == s.first_won for s in test)
    # Leans: the model moved LEAN_PTS or more away from the market. Buying the side it leaned toward pays off only if
    # that side won more often than its price, and the price that counts is the one a little later, when a person
    # copying the signal could buy: fast traders move it within seconds.
    lean_n = lean_won = 0
    lean_cost = 0.0
    for s in test:
        p = probability(s.price, s.signal, k)
        if abs(p - s.price) < LEAN_PTS:
            continue
        toward_first = p > s.price
        paid = s.copy_price if s.copy_price is not None else s.price
        lean_n += 1
        lean_won += s.first_won == toward_first
        lean_cost += paid if toward_first else 1 - paid
    return {
        "n": n, "market_loss": market_loss, "model_loss": model_loss,
        "market_accuracy": market_hits / n, "model_accuracy": model_hits / n,
        "lean_n": lean_n, "lean_win_rate": lean_won / lean_n if lean_n else 0.0,
        "lean_avg_price": lean_cost / lean_n if lean_n else 0.0,
        "copy_delay_s": COPY_DELAY_S,
    }


def fit_model(db: sqlite3.Connection, cfg: Settings, now: int) -> dict[str, object]:
    """The crowd is picked from the days before the last TEST_DAYS; k is fitted on half of those days' windows and
    judged on the other half, so neither the crowd nor k has seen the windows it is judged on."""
    test_start = now - TEST_DAYS * 86_400
    train_since = test_start - max(cfg.short_backfill_days - TEST_DAYS, 1) * 86_400
    members = crowd_members(board(db, cfg, since_ts=train_since, until_ts=test_start), cfg.short_crowd_n)
    recent = samples(db, members, test_start, now)
    fit_on = recent[0::2]
    judge_on = recent[1::2]
    k = fit_k(fit_on)
    stats: dict[str, object] = {"k": k, "members": len(members), **evaluate(judge_on, k)}
    enough = int(stats.get("n", 0)) >= MIN_SAMPLES
    stats["edge"] = enough and float(stats["model_loss"]) < float(stats["market_loss"])
    return stats
