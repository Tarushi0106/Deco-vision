"""Alerts (fall detection, after-hours intrusion) and the small global
settings table backing them (currently just the restricted-hours window)."""

from __future__ import annotations
import contextlib
import sqlite3
import time
from pathlib import Path

from . import camera_db

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


def list_alerts_with_camera_names(resolved: bool | None = None, limit: int = 50) -> list[dict]:
    """list_alerts() plus each row's camera_name — the one shared shape both
    GET /api/alerts and the /ws/alerts push socket send, so a REST fetch and
    a live push are never subtly different from each other (see main.py)."""
    cameras_by_id = {c["id"]: c["name"] for c in camera_db.list_cameras()}
    alerts = list_alerts(resolved=resolved, limit=limit)
    for alert in alerts:
        alert["camera_name"] = cameras_by_id.get(alert["camera_id"], "Unknown camera")
    return alerts


def upgrade_unknown_zone_alert(
    camera_id: int, zone_id: int, new_name: str, new_message: str, within_seconds: float = 15
) -> bool:
    """Recognition can resolve a face from "Unknown" to a real name a frame
    or two after a zone violation first fires (detection_worker's full-res
    recheck pass runs in the SAME cycle but can still land after the first
    "Unknown" match) — this updates that alert in place instead of logging a
    second, duplicate row for what is physically the same entry. Only
    matches a genuinely recent (within_seconds) unresolved "Unknown" row for
    this exact camera+zone; an older one is a separate, real prior visit and
    is deliberately left alone. Returns whether an upgrade happened, so the
    caller knows to skip creating a new alert."""
    cutoff = time.time() - within_seconds
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM alerts WHERE camera_id = ? AND zone_id = ? AND type = 'zone_intrusion' "
            "AND person_name = 'Unknown' AND resolved = 0 AND ts >= ? ORDER BY ts DESC LIMIT 1",
            (camera_id, zone_id, cutoff),
        ).fetchone()
        if row is None:
            return False
        conn.execute(
            "UPDATE alerts SET person_name = ?, message = ? WHERE id = ?",
            (new_name, new_message, row[0]),
        )
        return True


def count_open_alerts() -> int:
    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) FROM alerts WHERE resolved = 0").fetchone()
    return row[0]


def resolve_alert(alert_id: int) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE alerts SET resolved = 1 WHERE id = ?", (alert_id,))
