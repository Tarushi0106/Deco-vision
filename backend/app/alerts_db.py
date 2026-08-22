"""Alerts (fall detection, after-hours intrusion) and the small global
settings table backing them (currently just the restricted-hours window)."""

from __future__ import annotations
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "app.db"


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DB_PATH)


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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )


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


def log_alert(camera_id: int, alert_type: str, message: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO alerts (ts, camera_id, type, message) VALUES (?, ?, ?, ?)",
            (time.time(), camera_id, alert_type, message),
        )


def recent_open_alert(camera_id: int, alert_type: str, within_seconds: float) -> bool:
    """De-duplication/cooldown check — e.g. don't fire a new intrusion alert
    every pose-detection cycle (detection_worker.POSE_INTERVAL_SECONDS) while
    the same after-hours condition persists."""
    cutoff = time.time() - within_seconds
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM alerts WHERE camera_id = ? AND type = ? AND ts >= ? LIMIT 1",
            (camera_id, alert_type, cutoff),
        ).fetchone()
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
