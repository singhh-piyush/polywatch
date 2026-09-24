"""Recurring up-or-down crypto markets: 5-minute, 15-minute and hourly windows."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
INTERVALS = {"5m": 300, "15m": 900, "1h": 3600}
# Hourly markets name the coin in full ("bitcoin-up-or-down-september-24-2026-2am-et").
HOURLY_NAMES = {"btc": "bitcoin", "eth": "ethereum", "sol": "solana", "xrp": "xrp", "bnb": "bnb",
                "doge": "dogecoin", "hype": "hyperliquid"}
_TICKER_FOR_NAME = {name: ticker for ticker, name in HOURLY_NAMES.items()}
MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december")

_UPDOWN = re.compile(r"^([a-z0-9]+)-updown-(5m|15m)-(\d{9,11})$")
_HOURLY = re.compile(r"^([a-z0-9]+)-up-or-down-([a-z]+)-(\d{1,2})-(\d{4})-(\d{1,2})(am|pm)-et$")


@dataclass(frozen=True, slots=True)
class Window:
    """One market window: its coin, interval and when it opens and closes (UTC seconds)."""

    slug: str
    coin: str
    interval: str
    start_ts: int
    end_ts: int

    @property
    def label(self) -> str:
        start = datetime.fromtimestamp(self.start_ts, ET)
        end = datetime.fromtimestamp(self.end_ts, ET)
        return f"{self.coin.upper()} {self.interval} · {_clock(start)}–{_clock(end)} ET"


def _clock(dt: datetime) -> str:
    return f"{dt.hour % 12 or 12}:{dt.minute:02d}{'am' if dt.hour < 12 else 'pm'}"


def parse_window(slug: str) -> Window | None:
    """The window a short-market slug stands for, or None for any other market (daily up-or-down included)."""
    slug = slug or ""
    if m := _UPDOWN.match(slug):
        coin, interval, start = m.group(1), m.group(2), int(m.group(3))
        return Window(slug, coin, interval, start, start + INTERVALS[interval])
    if m := _HOURLY.match(slug):
        name, month, day, year, hour, half = m.groups()
        coin = _TICKER_FOR_NAME.get(name)
        if coin is None or month not in MONTHS:
            return None
        h = int(hour) % 12 + (12 if half == "pm" else 0)
        try:
            start_dt = datetime(int(year), MONTHS.index(month) + 1, int(day), h, tzinfo=ET)
        except ValueError:
            return None
        start = int(start_dt.timestamp())
        return Window(slug, coin, "1h", start, start + 3600)
    return None


def is_short_market(slug: str) -> bool:
    return parse_window(slug) is not None


def hourly_slug(coin: str, start_ts: int) -> str | None:
    name = HOURLY_NAMES.get(coin)
    if name is None:
        return None
    dt = datetime.fromtimestamp(start_ts, ET)
    hour = f"{dt.hour % 12 or 12}{'am' if dt.hour < 12 else 'pm'}"
    return f"{name}-up-or-down-{MONTHS[dt.month - 1]}-{dt.day}-{dt.year}-{hour}-et"


def window_for(coin: str, interval: str, start_ts: int) -> Window | None:
    if interval == "1h":
        slug = hourly_slug(coin, start_ts)
        return parse_window(slug) if slug else None
    return Window(f"{coin}-updown-{interval}-{start_ts}", coin, interval, start_ts, start_ts + INTERVALS[interval])


def _aligned_starts(interval: str, since: int, until: int) -> list[int]:
    """Window starts whose windows lie in [since, until]. Hourly windows start on the ET hour."""
    size = INTERVALS[interval]
    if interval != "1h":
        first = -(-since // size) * size
        return list(range(first, until - size + 1, size))
    starts = []
    dt = datetime.fromtimestamp(since, ET).replace(minute=0, second=0, microsecond=0)
    if dt.timestamp() < since:
        dt += timedelta(hours=1)
    while dt.timestamp() + size <= until:
        starts.append(int(dt.timestamp()))
        dt = datetime.fromtimestamp(dt.timestamp() + size, ET)
    return starts


def windows_between(coins: list[str] | tuple[str, ...], intervals: list[str] | tuple[str, ...], since: int,
                    until: int) -> list[Window]:
    """Every window that opened at or after `since` and closed by `until`, newest first."""
    windows = []
    for interval in intervals:
        for start in _aligned_starts(interval, since, until):
            for coin in coins:
                window = window_for(coin, interval, start)
                if window is not None:
                    windows.append(window)
    windows.sort(key=lambda w: (-w.end_ts, w.interval, w.coin))
    return windows


def open_windows(coins: list[str] | tuple[str, ...], intervals: list[str] | tuple[str, ...],
                 now: int) -> list[Window]:
    """The window currently trading for every coin and interval."""
    windows = []
    for interval in intervals:
        size = INTERVALS[interval]
        if interval == "1h":
            start = int(datetime.fromtimestamp(now, ET).replace(minute=0, second=0, microsecond=0).timestamp())
        else:
            start = now // size * size
        for coin in coins:
            window = window_for(coin, interval, start)
            if window is not None:
                windows.append(window)
    return windows
