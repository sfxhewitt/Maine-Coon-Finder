"""SQLite store: every advert we've seen, what we decided, and the alert state.
This is what stops duplicate alerts and makes 'never email without Yes' durable."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .models import Assessment, Listing

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    key TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT,
    status TEXT NOT NULL,          -- rejected | error | notified | approved | declined | contacted
    reason TEXT,
    data TEXT,                     -- JSON of the Listing
    assessment TEXT,               -- JSON of the Assessment
    telegram_message_id INTEGER,
    first_seen TEXT NOT NULL,
    updated TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE INDEX IF NOT EXISTS idx_status ON listings(status);
"""

# Statuses that mean "we're done deciding about this advert".
FINAL = {"rejected", "notified", "approved", "declined", "contacted"}
MAX_ATTEMPTS = 3


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _listing_json(l: Listing) -> str:
    d = asdict(l)
    d["published_at"] = l.published_at.isoformat() if l.published_at else None
    return json.dumps(d)


def listing_from_json(s: str) -> Listing:
    d = json.loads(s)
    if d.get("published_at"):
        d["published_at"] = datetime.fromisoformat(d["published_at"])
    return Listing(**d)


class Store:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.commit()

    # --- dedupe ---
    def is_done(self, key: str) -> bool:
        row = self._db.execute("SELECT status, attempts FROM listings WHERE key=?", (key,)).fetchone()
        if row is None:
            return False
        return row["status"] in FINAL or row["attempts"] >= MAX_ATTEMPTS

    def record(
        self,
        listing: Listing,
        status: str,
        reason: str = "",
        assessment: Assessment | None = None,
        telegram_message_id: int | None = None,
    ) -> None:
        now = _now()
        with self._lock:
            self._db.execute(
                """INSERT INTO listings (key, source, url, title, status, reason, data, assessment,
                                         telegram_message_id, first_seen, updated, attempts)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?, ?)
                   ON CONFLICT(key) DO UPDATE SET
                     status=excluded.status, reason=excluded.reason, data=excluded.data,
                     assessment=COALESCE(excluded.assessment, listings.assessment),
                     telegram_message_id=COALESCE(excluded.telegram_message_id, listings.telegram_message_id),
                     updated=excluded.updated,
                     attempts=listings.attempts + (CASE WHEN excluded.status='error' THEN 1 ELSE 0 END)""",
                (
                    listing.key,
                    listing.source,
                    listing.url,
                    listing.title,
                    status,
                    reason,
                    _listing_json(listing),
                    json.dumps(asdict(assessment)) if assessment else None,
                    telegram_message_id,
                    now,
                    now,
                    1 if status == "error" else 0,
                ),
            )
            self._db.commit()

    # --- alert state ---
    def get(self, key: str) -> sqlite3.Row | None:
        return self._db.execute("SELECT * FROM listings WHERE key=?", (key,)).fetchone()

    def set_status(self, key: str, status: str, reason: str = "", expect: str | None = None) -> bool:
        """Change status. With `expect`, only changes if the current status matches
        (so a double-tap on Yes can't send two emails). Returns True if changed."""
        with self._lock:
            if expect is None:
                cur = self._db.execute(
                    "UPDATE listings SET status=?, reason=?, updated=? WHERE key=?",
                    (status, reason, _now(), key),
                )
            else:
                cur = self._db.execute(
                    "UPDATE listings SET status=?, reason=?, updated=? WHERE key=? AND status=?",
                    (status, reason, _now(), key, expect),
                )
            self._db.commit()
            return cur.rowcount == 1

    def counts(self) -> dict[str, int]:
        rows = self._db.execute("SELECT status, COUNT(*) n FROM listings GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rows}

    def recent(self, status: str, limit: int = 10) -> list[sqlite3.Row]:
        return self._db.execute(
            "SELECT * FROM listings WHERE status=? ORDER BY updated DESC LIMIT ?", (status, limit)
        ).fetchall()

    # --- diagnostics ---
    def stats_since(self, since_iso: str) -> dict[str, int]:
        rows = self._db.execute(
            "SELECT status, COUNT(*) n FROM listings WHERE first_seen >= ? GROUP BY status", (since_iso,)
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    def top_reasons(self, since_iso: str, limit: int = 6) -> list[tuple[str, int]]:
        """Most common reasons adverts were turned down, with prices grouped together."""
        rows = self._db.execute(
            "SELECT reason FROM listings WHERE status='rejected' AND updated >= ?", (since_iso,)
        ).fetchall()
        out: dict[str, int] = {}
        for r in rows:
            reason = r["reason"] or "?"
            if reason.startswith("price £"):
                reason = "over the price cap"
            elif reason.endswith("days old"):
                reason = "too old"
            elif reason.endswith("miles away"):
                reason = "too far away"
            elif reason.startswith("colour"):
                reason = "not dark grey"
            out[reason] = out.get(reason, 0) + 1
        return sorted(out.items(), key=lambda kv: -kv[1])[:limit]

    def colours_seen(self, since_iso: str, limit: int = 6) -> list[tuple[str, int]]:
        """What colours the photo check saw on adverts it turned down."""
        rows = self._db.execute(
            "SELECT reason FROM listings WHERE status='rejected' AND reason LIKE 'colour: %' AND updated >= ?",
            (since_iso,),
        ).fetchall()
        out: dict[str, int] = {}
        for r in rows:
            c = r["reason"][len("colour: "):].strip().lower()[:40]
            out[c] = out.get(c, 0) + 1
        return sorted(out.items(), key=lambda kv: -kv[1])[:limit]

    def last_error(self) -> sqlite3.Row | None:
        return self._db.execute(
            "SELECT * FROM listings WHERE status='error' ORDER BY updated DESC LIMIT 1"
        ).fetchone()

    def last_alert_time(self) -> str | None:
        row = self._db.execute(
            "SELECT MAX(first_seen) t FROM listings WHERE status IN ('notified','approved','declined','contacted')"
            " AND source != 'test'"
        ).fetchone()
        return row["t"] if row else None

    def requeue(self, where: str, params: tuple = ()) -> int:
        """Give matching adverts another go: they'll be checked again on the next scan."""
        with self._lock:
            cur = self._db.execute(
                f"UPDATE listings SET status='error', attempts=0, updated=? WHERE {where}", (_now(), *params)
            )
            self._db.commit()
            return cur.rowcount

    # --- misc ---
    def meta_get(self, k: str) -> str | None:
        row = self._db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return row["v"] if row else None

    def meta_set(self, k: str, v: str) -> None:
        with self._lock:
            self._db.execute("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))
            self._db.commit()
