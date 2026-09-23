"""Settings with in-code defaults and optional TOML overrides."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields, replace
from pathlib import Path


def _xdg(var: str, default: str) -> Path:
    value = os.environ.get(var)
    return Path(value) if value else Path.home() / default


def _db_path() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share") / "polywatch" / "polywatch.db"


def _log_path() -> Path:
    return _xdg("XDG_STATE_HOME", ".local/state") / "polywatch" / "polywatch.log"


@dataclass(frozen=True)
class Settings:
    # scoring window and activity requirement
    window_days: int = 90
    active_days: int = 14
    # candidate pool: leaderboard (period, depth) pairs, merged in order
    candidate_depths: tuple[tuple[str, int], ...] = (("MONTH", 1000), ("WEEK", 250), ("ALL", 500))
    scan_concurrency: int = 8
    closed_positions_max_pages: int = 40
    # exclusion thresholds
    min_resolved: int = 15
    max_resolved: int = 2000
    max_lifetime_markets: int = 20_000
    max_top_share: float = 0.50
    min_account_age_d: float = 30
    mm_min_volume: float = 5_000_000
    mm_max_margin: float = 0.02
    max_rebate_rate: float = 0.005  # maker rebates as a share of the amount staked
    max_short_share: float = 0.50
    max_fast_share: float = 0.50  # recent buys a person could not copy by hand (see snipe_price, flip_window_s)
    max_void_share: float = 0.50  # resolved bets on voided (50/50) markets
    # flag thresholds (kept, badged, not auto-watched)
    flag_top_share: float = 0.40
    flag_account_age_d: float = 60
    flag_quiet_gap_h: int = 3
    flag_rebate_rate: float = 0.0025
    flag_mm_margin: float = 0.04
    flag_min_roi: float = 0.02
    flag_min_roi_bets: int = 200
    flag_fast_share: float = 0.25
    quiet_gap_min_trades: int = 200
    # copyability: a recent buy is too fast to copy when it is at snipe_price or more, is sold again within
    # flip_window_s, or has the other outcome bought within flip_window_s
    snipe_price: float = 0.95
    flip_window_s: int = 600
    fast_min_buys: int = 20
    # composite score
    shrink_k: int = 10
    w_edge: float = 0.35
    w_roi: float = 0.25
    w_win_rate: float = 0.20
    w_pnl: float = 0.20
    # watchlist and feed
    watchlist_size: int = 50
    feed_min_usd: float = 100.0
    conviction_multiple: float = 3.0
    merge_gap_s: int = 30
    merge_max_s: int = 300
    poll_interval_s: int = 20
    price_refresh_s: int = 30
    backfill_hours: int = 24
    alerts: bool = True
    stale_scan_hours: int = 24
    # files
    db_path: Path = field(default_factory=_db_path)
    log_path: Path = field(default_factory=_log_path)


def config_path() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / "polywatch" / "config.toml"


def load_settings(path: Path | None = None) -> Settings:
    path = path or config_path()
    base = Settings()
    if not path.exists():
        return base
    known = {f.name for f in fields(Settings)}
    overrides = {}
    for key, value in tomllib.loads(path.read_text()).items():
        if key not in known:
            raise ValueError(f"unknown setting {key!r} in {path}")
        if key in ("db_path", "log_path"):
            value = Path(value).expanduser()
        elif key == "candidate_depths":
            value = tuple((str(period), int(depth)) for period, depth in value)
        overrides[key] = value
    return replace(base, **overrides)
