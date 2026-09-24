"""SQLite tables for short markets: indexed windows, each wallet's result in them, stars and the crowd model.

It opens its own connection to the polywatch database, so the leaderboard can be aggregated in a worker thread
without holding up the main one.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, TypeVar

from .markets import Window
from .pnl import WalletWindow

T = TypeVar("T")
KEEP_DAYS = 35
MIN_STAKE = 5.0  # wallets that put in and made less than this in a window aren't stored: dust, and most of the rows

SCHEMA = """
CREATE TABLE IF NOT EXISTS short_windows (
    id INTEGER PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    coin TEXT NOT NULL,
    interval TEXT NOT NULL,
    start_ts INTEGER NOT NULL,
    end_ts INTEGER NOT NULL,
    condition_id TEXT NOT NULL DEFAULT '',
    outcomes TEXT NOT NULL DEFAULT '',
    winner TEXT,                 -- NULL: the market doesn't exist (nothing to index)
    price_before REAL,           -- first outcome's price a minute before close
    price_copy REAL,             -- and COPY_DELAY_S later: about what someone copying the signal by hand would pay
    fills INTEGER NOT NULL DEFAULT 0,
    truncated INTEGER NOT NULL DEFAULT 0,
    indexed_ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS short_windows_end ON short_windows (end_ts);
CREATE TABLE IF NOT EXISTS short_wallets (
    id INTEGER PRIMARY KEY,
    wallet TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL DEFAULT ''
);
-- One row per wallet per window. Wallets and windows are stored by id: there are millions of rows.
CREATE TABLE IF NOT EXISTS short_results (
    window_id INTEGER NOT NULL,
    wallet_id INTEGER NOT NULL,
    coin TEXT NOT NULL,
    interval TEXT NOT NULL,
    end_ts INTEGER NOT NULL,
    pnl REAL NOT NULL,
    cost REAL NOT NULL,
    buy_usd REAL NOT NULL,
    side INTEGER,                -- index of the outcome it put most on; NULL if it only sold
    side_price REAL,
    won INTEGER,
    both_sides INTEGER NOT NULL,
    snipe_usd REAL NOT NULL,
    minted REAL NOT NULL,
    fills INTEGER NOT NULL,
    last_buy_s INTEGER,
    early0 REAL NOT NULL,        -- dollars bought on each outcome a minute or more before close
    early1 REAL NOT NULL,
    PRIMARY KEY (window_id, wallet_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS short_results_end ON short_results (end_ts);
CREATE INDEX IF NOT EXISTS short_results_wallet ON short_results (wallet_id, end_ts);
CREATE TABLE IF NOT EXISTS short_prefs (
    wallet TEXT PRIMARY KEY,
    star INTEGER NOT NULL DEFAULT 0,
    bell INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS short_model (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    fitted_ts INTEGER NOT NULL,
    stats_json TEXT NOT NULL
);
"""


class ShortStore:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(short_windows)")}
        if "price_copy" not in columns:  # files from before the column existed
            self.db.execute("ALTER TABLE short_windows ADD COLUMN price_copy REAL")
        self._local = threading.local()
        self._writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="short-store", initializer=self._open_writer)

    def _open_writer(self) -> None:
        if self.path != ":memory:":
            self._local.db = sqlite3.connect(self.path, timeout=30)

    @property
    def conn(self) -> sqlite3.Connection:
        """The writer thread's own connection there, the main one elsewhere."""
        return getattr(self._local, "db", None) or self.db

    def close(self) -> None:
        self._writer.shutdown(wait=True)
        self.db.close()

    async def write(self, fn: Callable[[], T]) -> T:
        """Run fn on the single writer thread: writes stay in order and off the event loop."""
        return await asyncio.get_running_loop().run_in_executor(self._writer, fn)

    def read(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        """Run fn on a connection of its own, so it can run in a worker thread. The in-memory test database only has
        the one connection."""
        if self.path == ":memory:":
            return fn(self.db)
        db = sqlite3.connect(self.path)
        try:
            return fn(db)
        finally:
            db.close()

    # --- windows ---------------------------------------------------------------------------------

    def indexed_slugs(self, since_ts: int) -> set[str]:
        rows = self.db.execute("SELECT slug FROM short_windows WHERE end_ts >= ?", (since_ts,))
        return {r[0] for r in rows}

    def save_missing(self, window: Window, now: int) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO short_windows (slug, coin, interval, start_ts, end_ts, indexed_ts) "
                "VALUES (?, ?, ?, ?, ?, ?)", (window.slug, window.coin, window.interval, window.start_ts, window.end_ts, now))

    def _wallet_ids(self, results: list[WalletWindow]) -> dict[str, int]:
        self.conn.executemany("INSERT OR IGNORE INTO short_wallets (wallet, name) VALUES (?, ?)",
                            [(r.wallet, r.name) for r in results])
        self.conn.executemany("UPDATE short_wallets SET name = ? WHERE wallet = ? AND name != ?",
                            [(r.name, r.wallet, r.name) for r in results if r.name])
        ids: dict[str, int] = {}
        wallets = [r.wallet for r in results]
        for i in range(0, len(wallets), 500):
            chunk = wallets[i:i + 500]
            marks = ",".join("?" * len(chunk))
            ids.update(self.conn.execute(f"SELECT wallet, id FROM short_wallets WHERE wallet IN ({marks})", chunk))
        return ids

    def save_window(self, window: Window, *, condition_id: str, outcomes: tuple[str, str], winner: str,
                    price_before: float | None, fills: int, truncated: bool, results: Iterable[WalletWindow],
                    now: int, price_copy: float | None = None) -> None:
        kept = [r for r in results if r.cost >= MIN_STAKE or abs(r.pnl) >= MIN_STAKE]
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO short_windows (slug, coin, interval, start_ts, end_ts, condition_id, outcomes, winner, "
                "price_before, price_copy, fills, truncated, indexed_ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(slug) DO UPDATE SET condition_id = excluded.condition_id, outcomes = excluded.outcomes, "
                "winner = excluded.winner, price_before = excluded.price_before, price_copy = excluded.price_copy, "
                "fills = excluded.fills, truncated = excluded.truncated, indexed_ts = excluded.indexed_ts RETURNING id",
                (window.slug, window.coin, window.interval, window.start_ts, window.end_ts, condition_id,
                 json.dumps(list(outcomes)), winner, price_before, price_copy, fills, int(truncated), now))
            window_id = int(cur.fetchone()[0])
            ids = self._wallet_ids(kept)
            rows = []
            for r in kept:
                side = outcomes.index(r.side) if r.side else None
                rows.append((window_id, ids[r.wallet], window.coin, window.interval, window.end_ts, r.pnl, r.cost,
                             r.buy_usd, side, r.side_price if r.side else None, int(r.won) if r.side else None,
                             int(r.both_sides), r.snipe_usd, r.minted, r.fills, r.last_buy_s,
                             r.early_buys.get(outcomes[0], 0.0), r.early_buys.get(outcomes[1], 0.0)))
            self.conn.execute("DELETE FROM short_results WHERE window_id = ?", (window_id,))
            self.conn.executemany(
                "INSERT INTO short_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)

    def coverage(self) -> tuple[int, int, int] | None:
        """(windows indexed, oldest close, newest close) over the markets that exist."""
        row = self.db.execute(
            "SELECT COUNT(*), MIN(end_ts), MAX(end_ts) FROM short_windows WHERE winner IS NOT NULL").fetchone()
        return (int(row[0]), int(row[1]), int(row[2])) if row and row[0] else None

    def prune(self, now: int) -> None:
        cutoff = now - KEEP_DAYS * 86_400
        with self.conn:
            self.conn.execute("DELETE FROM short_results WHERE end_ts < ?", (cutoff,))
            self.conn.execute("DELETE FROM short_wallets WHERE id NOT IN (SELECT DISTINCT wallet_id FROM short_results)")
            self.conn.execute("DELETE FROM short_windows WHERE end_ts < ?", (cutoff,))

    def names(self, wallets: Iterable[str]) -> dict[str, str]:
        wallets = list(wallets)
        if not wallets:
            return {}
        marks = ",".join("?" * len(wallets))
        return dict(self.db.execute(f"SELECT wallet, name FROM short_wallets WHERE wallet IN ({marks})", wallets))

    # --- stars and bells -------------------------------------------------------------------------

    def prefs(self) -> dict[str, tuple[bool, bool]]:
        return {w: (bool(s), bool(b)) for w, s, b in self.db.execute("SELECT wallet, star, bell FROM short_prefs")}

    def set_pref(self, wallet: str, *, star: bool | None = None, bell: bool | None = None) -> None:
        current = self.prefs().get(wallet, (False, False))
        new = (current[0] if star is None else star, current[1] if bell is None else bell)
        with self.db:
            if new == (False, False):
                self.db.execute("DELETE FROM short_prefs WHERE wallet = ?", (wallet,))
            else:
                self.db.execute("INSERT OR REPLACE INTO short_prefs VALUES (?, ?, ?)", (wallet, *map(int, new)))

    # --- crowd model -----------------------------------------------------------------------------

    def model(self) -> tuple[int, dict[str, Any]] | None:
        row = self.db.execute("SELECT fitted_ts, stats_json FROM short_model WHERE id = 1").fetchone()
        return (int(row[0]), json.loads(row[1])) if row else None

    def save_model(self, fitted_ts: int, stats: dict[str, Any]) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO short_model VALUES (1, ?, ?)", (fitted_ts, json.dumps(stats)))
