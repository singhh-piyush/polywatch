"""Composite score: percentile ranks of edge, ROI, win rate and PnL within the eligible cohort."""
from __future__ import annotations

from ..config import Settings
from ..models import RankedTrader, TraderStats, Verdict


def percentile_ranks(values: list[float]) -> list[float]:
    """Rank of each value scaled to [0, 1]; ties share their average rank."""
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [1.0]
    order = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        shared = (i + j) / 2 / (n - 1)
        for k in range(i, j + 1):
            ranks[order[k]] = shared
        i = j + 1
    return ranks


def rank_traders(entries: list[tuple[TraderStats, Verdict]], cfg: Settings) -> list[RankedTrader]:
    eligible = [(s, v) for s, v in entries if v.eligible]
    cohort = [s for s, _ in eligible]
    # Percentiles ignore magnitude, so a whale's PnL counts no more than "highest in the cohort"
    # (the same as ranking log10(PnL)).
    edge = percentile_ranks([s.edge for s in cohort])
    roi = percentile_ranks([s.roi for s in cohort])
    win = percentile_ranks([s.win_rate for s in cohort])
    pnl = percentile_ranks([s.pnl for s in cohort])
    ranked = [
        RankedTrader(s, v, score=100 * (cfg.w_edge * e + cfg.w_roi * r + cfg.w_win_rate * w + cfg.w_pnl * p))
        for (s, v), e, r, w, p in zip(eligible, edge, roi, win, pnl)
    ]
    ranked.sort(key=lambda t: t.score, reverse=True)
    for position, trader in enumerate(ranked, start=1):
        trader.rank = position
    return ranked
