"""sqlite persistence: schema, migration, and run/history/config helpers.

WAL mode is used because the GUI and the systemd timer may both write.
No `gi` import here — shared by GUI and headless paths.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from . import config

SCHEMA_VERSION = 1

_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS device (
    id          INTEGER PRIMARY KEY,
    uuid        TEXT UNIQUE,
    label       TEXT,
    fstype      TEXT,
    last_target TEXT,
    first_seen  TEXT DEFAULT (datetime('now')),
    last_seen   TEXT
);

CREATE TABLE IF NOT EXISTS backup_run (
    id                 INTEGER PRIMARY KEY,
    device_id          INTEGER REFERENCES device(id),
    started_at         TEXT NOT NULL,
    finished_at        TEXT,
    duration_secs      REAL,
    mode               TEXT NOT NULL DEFAULT 'mirror',
    trigger            TEXT NOT NULL DEFAULT 'gui',
    source_total_bytes INTEGER,
    bytes_transferred  INTEGER,
    files_transferred  INTEGER,
    files_deleted      INTEGER,
    exit_status        INTEGER,
    status             TEXT NOT NULL,
    rsync_log_path     TEXT,
    error_summary      TEXT
);
CREATE INDEX IF NOT EXISTS idx_run_started ON backup_run(started_at DESC);

CREATE TABLE IF NOT EXISTS config (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect(path: Optional[Path] = None) -> sqlite3.Connection:
    config.ensure_dirs()
    conn = sqlite3.connect(str(path or config.db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,))
        conn.commit()
    # Future migrations: compare row["version"] and ALTER as needed.


# --- config key/value ------------------------------------------------------

def get_config(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_config(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO config(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


# --- devices ---------------------------------------------------------------

def upsert_device(conn: sqlite3.Connection, uuid: str | None, label: str | None,
                  fstype: str | None, target: str | None) -> int:
    """Insert or refresh a device row keyed by UUID; return its id."""
    if uuid:
        conn.execute(
            "INSERT INTO device(uuid, label, fstype, last_target, last_seen) "
            "VALUES (?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT(uuid) DO UPDATE SET "
            "label=excluded.label, fstype=excluded.fstype, "
            "last_target=excluded.last_target, last_seen=datetime('now')",
            (uuid, label, fstype, target),
        )
        conn.commit()
        row = conn.execute("SELECT id FROM device WHERE uuid=?", (uuid,)).fetchone()
        return row["id"]
    # No UUID (rare): store an anonymous row keyed by target.
    cur = conn.execute(
        "INSERT INTO device(label, fstype, last_target, last_seen) "
        "VALUES (?, ?, ?, datetime('now'))",
        (label, fstype, target),
    )
    conn.commit()
    return cur.lastrowid


# --- backup runs -----------------------------------------------------------

def start_run(conn: sqlite3.Connection, device_id: int | None, mode: str,
              trigger: str, started_at: str, log_path: str | None) -> int:
    cur = conn.execute(
        "INSERT INTO backup_run(device_id, started_at, mode, trigger, status, rsync_log_path) "
        "VALUES (?, ?, ?, ?, 'running', ?)",
        (device_id, started_at, mode, trigger, log_path),
    )
    conn.commit()
    return cur.lastrowid


def finish_run(conn: sqlite3.Connection, run_id: int, *, finished_at: str,
               duration_secs: float, status: str, exit_status: int | None,
               stats: dict | None = None, error_summary: str | None = None) -> None:
    stats = stats or {}
    conn.execute(
        "UPDATE backup_run SET finished_at=?, duration_secs=?, status=?, "
        "exit_status=?, source_total_bytes=?, bytes_transferred=?, "
        "files_transferred=?, files_deleted=?, error_summary=? WHERE id=?",
        (finished_at, duration_secs, status, exit_status,
         stats.get("source_total_bytes"), stats.get("bytes_transferred"),
         stats.get("files_transferred"), stats.get("files_deleted"),
         error_summary, run_id),
    )
    conn.commit()


def last_successful(conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM backup_run WHERE status IN ('success','partial') "
        "ORDER BY finished_at DESC LIMIT 1"
    ).fetchone()


def history(conn: sqlite3.Connection, limit: int = 100) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM backup_run ORDER BY started_at DESC LIMIT ?", (limit,)
    ).fetchall()


def reconcile_stale(conn: sqlite3.Connection) -> None:
    """Mark orphaned 'running' rows (from a crash) as failed on startup."""
    conn.execute(
        "UPDATE backup_run SET status='failed', "
        "error_summary=COALESCE(error_summary,'interrupted') "
        "WHERE status='running'"
    )
    conn.commit()
