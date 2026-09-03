"""Alerts (fall detection, after-hours intrusion) and the small global
settings table backing them (currently just the restricted-hours window)."""

from __future__ import annotations
import contextlib
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "app.db"


@contextlib.contextmanager
def get_connection():
    """A fresh sqlite3.Connection per call, closed on exit — sqlite3's own
    `with conn:` only commits/rolls back, it never closes the connection,
    so every prior get_connection() call leaked a file descriptor. Under
    this app's constant background DB traffic (capture loop, sender/
    receiver threads, replay prefetch) that exhausted the process's fd
    limit (ulimit -n) within minutes, breaking every DB-backed endpoint
    with "unable to open database file" until the next restart."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                camera_id INTEGER,
                type TEXT NOT NULL,
                message TEXT NOT NULL,
                resolved INTEGER DEFAULT 0
            )
            """
        )
        _migrate_zone_columns(conn)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )


def _migrate_zone_columns(conn: sqlite3.Connection) -> None:
    """alerts table predates zone_intrusion alerts — zone_id/person_name are
    only used for that alert type's dedup (see recent_open_alert), so NULL
    for every other alert type."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(alerts)")}
    if "zone_id" not in columns:
        conn.execute("ALTER TABLE alerts ADD COLUMN zone_id INTEGER")
    if "person_name" not in columns:
        conn.execute("ALTER TABLE alerts ADD COLUMN person_name TEXT")
    if "snapshot_path" not in columns:
        conn.execute("ALTER TABLE alerts ADD COLUMN snapshot_path TEXT")


def get_setting(key: str, default: str | None = None) -> str | None:
    with get_connection() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_setting(key: str, value: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def log_alert(
    camera_id: int,
    alert_type: str,
    message: str,
    zone_id: int | None = None,
    person_name: str | None = None,
    snapshot_path: str | None = None,
) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO alerts (ts, camera_id, type, message, zone_id, person_name, snapshot_path) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (time.time(), camera_id, alert_type, message, zone_id, person_name, snapshot_path),
        )


def get_alert(alert_id: int) -> dict | None:
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
    return dict(row) if row else None


def recent_open_alert(
    camera_id: int,
    alert_type: str,
    within_seconds: float,
    zone_id: int | None = None,
    person_name: str | None = None,
) -> bool:
    """De-duplication/cooldown check — e.g. don't fire a new intrusion alert
    every pose-detection cycle (detection_worker.POSE_INTERVAL_SECONDS) while
    the same after-hours condition persists. When zone_id/person_name are
    given (zone_intrusion alerts), the cooldown is scoped per zone AND per
    detected identity, so a different unauthorized person in the same zone
    still alerts immediately instead of being suppressed by someone else's
    still-fresh alert."""
    cutoff = time.time() - within_seconds
    query = "SELECT 1 FROM alerts WHERE camera_id = ? AND type = ? AND ts >= ?"
    params = [camera_id, alert_type, cutoff]
    if zone_id is not None:
        query += " AND zone_id = ? AND person_name = ?"
        params += [zone_id, person_name]
    query += " LIMIT 1"
    with get_connection() as conn:
        row = conn.execute(query, params).fetchone()
    return row is not None


def list_alerts(resolved: bool | None = None, limit: int = 50) -> list[dict]:
    query = "SELECT * FROM alerts"
    params = []
    if resolved is not None:
        query += " WHERE resolved = ?"
        params.append(int(resolved))
    query += " ORDER BY ts DESC LIMIT ?"
    params.append(limit)
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def count_open_alerts() -> int:
    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) FROM alerts WHERE resolved = 0").fetchone()
    return row[0]


def resolve_alert(alert_id: int) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE alerts SET resolved = 1 WHERE id = ?", (alert_id,))
