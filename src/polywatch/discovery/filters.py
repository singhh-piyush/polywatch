"""Pure rules deciding which traders are bots, one-shots or otherwise not worth following."""
from __future__ import annotations

from ..config import Settings
from ..fmt import cents, usd_compact
from ..models import TraderStats, Verdict

DAY = 86_400
# Shown as a badge only: when backtested, traders active around the clock were as copyable as anyone.
BADGE_ONLY_FLAGS = frozenset({"24/7"})


def _margin(s: TraderStats) -> float:
    return s.lb_pnl / s.lb_volume if s.lb_volume else 0.0


def _rebate_rate(s: TraderStats) -> float:
    # Anyone with resting limit orders earns maker rebates, so only the rate relative to the amount staked matters.
    return s.maker_rebates / s.staked if s.staked else 0.0


def _mm_signals(s: TraderStats, cfg: Settings) -> list[str]:
    signals = []
    if _rebate_rate(s) >= cfg.flag_rebate_rate:
        signals.append(f"maker rebates are {_rebate_rate(s):.2%} of the amount staked")
    if s.lb_volume > cfg.mm_min_volume and _margin(s) < cfg.flag_mm_margin:
        signals.append(f"{_margin(s):.1%} margin on {usd_compact(s.lb_volume)} volume")
    if s.n >= cfg.flag_min_roi_bets and s.roi < cfg.flag_min_roi:
        signals.append(f"{s.roi:.1%} ROI across {s.n} bets")
    return signals


def _fast_detail(s: TraderStats, cfg: Settings) -> str:
    return (f"{s.fast_share or 0:.0%} of recent buys are at {cents(cfg.snipe_price)}+, "
            f"sold within {cfg.flip_window_s // 60} min or bought on both sides")


def early_exclusion(s: TraderStats, now: int, cfg: Settings) -> str | None:
    """Rules that only need leaderboard, /traded and recent-activity data (checked before the expensive fetches)."""
    if s.lb_volume > cfg.mm_min_volume and _margin(s) < cfg.mm_max_margin:
        return f"market maker: {_margin(s):.1%} margin on {usd_compact(s.lb_volume)} volume"
    if s.markets_traded > cfg.max_lifetime_markets:
        return f"bot: {s.markets_traded:,} markets traded"
    if s.last_trade_ts == 0:
        return "inactive: no recent trades"
    idle_days = (now - s.last_trade_ts) / DAY
    if idle_days > cfg.active_days:
        return f"inactive: last trade {idle_days:.0f}d ago"
    if s.short_share > cfg.max_short_share:
        return f"bot: {s.short_share:.0%} of trades in 5/15-min markets"
    if s.fast_share is not None and s.fast_share >= cfg.max_fast_share:
        return f"too fast to copy: {_fast_detail(s, cfg)}"
    return None


def full_exclusion(s: TraderStats, now: int, cfg: Settings) -> str | None:
    if reason := early_exclusion(s, now, cfg):
        return reason
    if s.truncated or s.n > cfg.max_resolved:
        return f"bot: over {cfg.max_resolved:,} resolved bets in {cfg.window_days}d"
    if s.void_share >= cfg.max_void_share:
        return f"void arb: {s.void_share:.0%} of resolved bets were on voided (50/50) markets"
    if s.n < cfg.min_resolved:
        return f"too few bets: {s.n} resolved in {cfg.window_days}d"
    if s.pnl <= 0:
        return f"unprofitable: {usd_compact(s.pnl)} in {cfg.window_days}d"
    if s.top_share > cfg.max_top_share:
        return f"one-hit: top bet is {s.top_share:.0%} of profit"
    if s.account_age_d is not None and s.account_age_d < cfg.min_account_age_d:
        return f"new account: {s.account_age_d:.0f}d old"
    if _rebate_rate(s) >= cfg.max_rebate_rate:
        return f"market maker: maker rebates are {_rebate_rate(s):.2%} of the amount staked"
    return None


def flags_for(s: TraderStats, cfg: Settings) -> tuple[str, ...]:
    flags = []
    if s.top_share > cfg.flag_top_share:
        flags.append("CONC")
    if s.account_age_d is not None and s.account_age_d < cfg.flag_account_age_d:
        flags.append("NEW")
    if s.quiet_gap_h is not None and s.quiet_gap_h < cfg.flag_quiet_gap_h:
        flags.append("24/7")
    if _mm_signals(s, cfg):
        flags.append("MM?")
    if s.fast_share is not None and s.fast_share >= cfg.flag_fast_share:
        flags.append("FAST")
    return tuple(flags)


def holds_back(flags: tuple[str, ...]) -> bool:
    """Whether these flags keep a trader off the automatic watchlist."""
    return any(flag not in BADGE_ONLY_FLAGS for flag in flags)


def evaluate(s: TraderStats, now: int, cfg: Settings) -> Verdict:
    if reason := full_exclusion(s, now, cfg):
        return Verdict(excluded=reason)
    return Verdict(flags=flags_for(s, cfg))


def describe_flag(flag: str, s: TraderStats, cfg: Settings) -> str:
    if flag == "CONC":
        return f"one bet is {s.top_share:.0%} of {cfg.window_days}-day profit (possible one-hit)"
    if flag == "NEW":
        return f"account is only {s.account_age_d or 0:.0f} days old"
    if flag == "24/7":
        return f"trades around the clock (longest quiet stretch {s.quiet_gap_h}h), possible bot"
    if flag == "MM?":
        return "market-maker signals: " + "; ".join(_mm_signals(s, cfg))
    if flag == "FAST":
        return f"often too fast to copy by hand: {_fast_detail(s, cfg)}"
    return flag
