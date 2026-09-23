import pytest

from polywatch.config import Settings
from polywatch.discovery.scoring import percentile_ranks, rank_traders
from polywatch.models import Verdict
from tests.factories import stats


def test_percentile_ranks():
    assert percentile_ranks([]) == []
    assert percentile_ranks([5.0]) == [1.0]
    assert percentile_ranks([10, 30, 20]) == [0.0, 1.0, 0.5]
    assert percentile_ranks([1, 1, 2]) == [0.25, 0.25, 1.0]


def test_rank_orders_by_composite_and_skips_excluded():
    best = stats(wallet="0xbest", edge=0.2, roi=0.5, win_rate=0.7, pnl=50_000.0)
    mid = stats(wallet="0xmid", edge=0.1, roi=0.3, win_rate=0.6, pnl=20_000.0)
    low = stats(wallet="0xlow", edge=0.0, roi=0.1, win_rate=0.5, pnl=5_000.0)
    gone = stats(wallet="0xgone", edge=0.9, roi=0.9, win_rate=0.9, pnl=90_000.0)
    ranked = rank_traders([(low, Verdict()), (best, Verdict()), (gone, Verdict(excluded="inactive: x")),
                           (mid, Verdict(flags=("NEW",)))], Settings())
    assert [r.stats.wallet for r in ranked] == ["0xbest", "0xmid", "0xlow"]
    assert [r.rank for r in ranked] == [1, 2, 3]
    assert ranked[0].score == pytest.approx(100.0) and ranked[-1].score == pytest.approx(0.0)
    assert ranked[1].verdict.flags == ("NEW",)


def test_weights_come_from_settings():
    a = stats(wallet="0xa", edge=0.3, roi=0.1)
    b = stats(wallet="0xb", edge=0.1, roi=0.3)
    edge_only = Settings(w_edge=1.0, w_roi=0.0, w_win_rate=0.0, w_pnl=0.0)
    roi_only = Settings(w_edge=0.0, w_roi=1.0, w_win_rate=0.0, w_pnl=0.0)
    assert rank_traders([(a, Verdict()), (b, Verdict())], edge_only)[0].stats.wallet == "0xa"
    assert rank_traders([(a, Verdict()), (b, Verdict())], roi_only)[0].stats.wallet == "0xb"
