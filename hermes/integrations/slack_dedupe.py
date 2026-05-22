"""Slack event_id dedupe shared across Hermes transports.

Both the Socket Mode listener (rsg-hermes) and the Events-API webhook
(rsg-hermes-api) can receive the same #crm-entry message — once from each
Slack app installed in the workspace. Backed by a sqlite file on the
bind-mounted repo so all hermes-* containers see the same store.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path

CRM_ENTRY_CHANNEL = "C0B57E18RK5"

_DEFAULT_DIR = "/app/state" if os.path.isdir("/app") else "/tmp/hermes"
_TTL_SECONDS = 7 * 24 * 3600

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _db_path() -> Path:
    base = Path(os.environ.get("HERMES_STATE_DIR", _DEFAULT_DIR))
    base.mkdir(parents=True, exist_ok=True)
    return base / "slack_events.sqlite"


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(_db_path(), check_same_thread=False, isolation_level=None)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute(
            "CREATE TABLE IF NOT EXISTS slack_events_seen ("
            "event_id TEXT PRIMARY KEY,"
            "seen_at INTEGER NOT NULL"
            ")"
        )
    return _conn


def claim_event(event_id: str) -> bool:
    """Atomically record an event_id. Return True if newly claimed, False if duplicate.

    Caller should skip processing when False is returned.
    """
    if not event_id:
        return True
    now = int(time.time())
    with _lock:
        conn = _connect()
        cur = conn.execute(
            "INSERT OR IGNORE INTO slack_events_seen(event_id, seen_at) VALUES (?, ?)",
            (event_id, now),
        )
        claimed = cur.rowcount == 1
        if claimed and now % 256 == 0:
            conn.execute(
                "DELETE FROM slack_events_seen WHERE seen_at < ?",
                (now - _TTL_SECONDS,),
            )
        return claimed


def reset_for_tests() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None
