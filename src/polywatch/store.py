"""SQLite persistence: scans, pins/bans and feed history."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

from .models import FeedItem, RankedTrader, TraderStats, Verdict

KEEP_SCANS = 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS scan_runs (
    id INTEGER PRIMARY KEY,
    started_ts INTEGER NOT NULL,
    finished_ts INTEGER,
    candidates INTEGER NOT NULL DEFAULT 0,
    ranked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS trader_stats (
    scan_id INTEGER NOT NULL,
    wallet TEXT NOT NULL,
    stats_json TEXT NOT NULL,
    excluded TEXT,
    flags TEXT NOT NULL DEFAULT '',
    score REAL,
    rank INTEGER,
    PRIMARY KEY (scan_id, wallet)
);
CREATE TABLE IF NOT EXISTS overrides (
    wallet TEXT PRIMARY KEY,
    mode TEXT NOT NULL CHECK (mode IN ('pin', 'ban')),
    name TEXT NOT NULL DEFAULT '',
    added_ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS feed_events (
    key TEXT PRIMARY KEY,
    wallet TEXT NOT NULL,
    name TEXT NOT NULL,
    side TEXT NOT NULL,
    asset TEXT NOT NULL,
    title TEXT NOT NULL,
    outcome TEXT NOT NULL,
    slug TEXT NOT NULL,
    event_slug TEXT NOT NULL,
    first_ts INTEGER NOT NULL,
    last_ts INTEGER NOT NULL,
    shares REAL NOT NULL,
    usd REAL NOT NULL,
    fills INTEGER NOT NULL,
    conviction REAL
);
"""


class Store:
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # --- scans -------------------------------------------------------------------------------

    def start_scan(self, now: int) -> int:
        cur = self.db.execute("INSERT INTO scan_runs (started_ts) VALUES (?)", (now,))
        self.db.commit()
        return int(cur.lastrowid)

    def save_trader(self, scan_id: int, stats: TraderStats, verdict: Verdict) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO trader_stats (scan_id, wallet, stats_json, excluded, flags) VALUES (?, ?, ?, ?, ?)",
            (scan_id, stats.wallet, json.dumps(asdict(stats)), verdict.excluded, ",".join(verdict.flags)),
        )
        self.db.commit()

    def finish_scan(self, scan_id: int, ranked: list[RankedTrader], *, candidates: int, now: int) -> None:
        self.db.executemany(
            "UPDATE trader_stats SET score = ?, rank = ? WHERE scan_id = ? AND wallet = ?",
            [(t.score, t.rank, scan_id, t.stats.wallet) for t in ranked],
        )
        self.db.execute("UPDATE scan_runs SET finished_ts = ?, candidates = ?, ranked = ? WHERE id = ?",
                        (now, candidates, len(ranked), scan_id))
        keep = [row[0] for row in self.db.execute("SELECT id FROM scan_runs ORDER BY id DESC LIMIT ?", (KEEP_SCANS,))]
        marks = ",".join("?" * len(keep))
        self.db.execute(f"DELETE FROM trader_stats WHERE scan_id NOT IN ({marks})", keep)
        self.db.execute(f"DELETE FROM scan_runs WHERE id NOT IN ({marks})", keep)
        self.db.commit()

    def latest_scan(self) -> tuple[int, int] | None:
        row = self.db.execute(
            "SELECT id, finished_ts FROM scan_runs WHERE finished_ts IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
        return (int(row[0]), int(row[1])) if row else None

    def load_scan(self, scan_id: int) -> list[RankedTrader]:
        rows = self.db.execute(
            "SELECT stats_json, excluded, flags, score, rank FROM trader_stats WHERE scan_id = ? "
            "ORDER BY rank IS NULL, rank, wallet",
            (scan_id,),
        ).fetchall()
        return [
            RankedTrader(
                stats=TraderStats(**json.loads(stats_json)),
                verdict=Verdict(excluded=excluded, flags=tuple(f for f in flags.split(",") if f)),
                score=score or 0.0,
                rank=rank or 0,
            )
            for stats_json, excluded, flags, score, rank in rows
        ]

    # --- pins and bans -----------------------------------------------------------------------

    def set_override(self, wallet: str, mode: str | None, *, name: str = "", now: int = 0) -> None:
        if mode is None:
            self.db.execute("DELETE FROM overrides WHERE wallet = ?", (wallet,))
        else:
            self.db.execute(
                "INSERT INTO overrides (wallet, mode, name, added_ts) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(wallet) DO UPDATE SET mode = excluded.mode, added_ts = excluded.added_ts, "
                "name = CASE WHEN excluded.name != '' THEN excluded.name ELSE overrides.name END",
                (wallet, mode, name, now),
            )
        self.db.commit()

    def overrides(self) -> dict[str, str]:
        return dict(self.db.execute("SELECT wallet, mode FROM overrides").fetchall())

    def override_names(self) -> dict[str, str]:
        return dict(self.db.execute("SELECT wallet, name FROM overrides WHERE name != ''").fetchall())

    # --- feed ----------------------------------------------------------------------------------

    def save_feed_item(self, item: FeedItem) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO feed_events (key, wallet, name, side, asset, title, outcome, slug, event_slug, "
            "first_ts, last_ts, shares, usd, fills, conviction) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (item.key, item.wallet, item.name, item.side, item.asset, item.title, item.outcome, item.slug,
             item.event_slug, item.first_ts, item.last_ts, item.shares, item.usd, item.fills, item.conviction),
        )
        self.db.commit()
