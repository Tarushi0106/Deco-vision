import sqlite3
import time
from datetime import datetime
from pathlib import Path

import numpy as np

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "app.db"


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DB_PATH)


def init_db() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS enrolled_faces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                source_photo TEXT NOT NULL,
                embedding BLOB NOT NULL
            )
            """
        )
        existing_face_cols = {row[1] for row in conn.execute("PRAGMA table_info(enrolled_faces)")}
        if "camera_face_id" not in existing_face_cols:
            # tracks which camera Allow List entry (device host + its Id) this
            # row was pulled from, so re-running the camera sync doesn't
            # re-import the same person every time
            conn.execute("ALTER TABLE enrolled_faces ADD COLUMN camera_face_id TEXT")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS detection_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                camera_id INTEGER,
                track_id INTEGER,
                name TEXT,
                shirt_color TEXT,
                bbox TEXT
            )
            """
        )
        existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(detection_events)")}
        if "camera_id" not in existing_cols:
            conn.execute("ALTER TABLE detection_events ADD COLUMN camera_id INTEGER")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS footfall_counts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                direction TEXT NOT NULL
            )
            """
        )
        existing_footfall_cols = {row[1] for row in conn.execute("PRAGMA table_info(footfall_counts)")}
        if "camera_id" not in existing_footfall_cols:
            conn.execute("ALTER TABLE footfall_counts ADD COLUMN camera_id INTEGER")


def clear_faces() -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM enrolled_faces")


def add_face(name: str, source_photo: str, embedding: np.ndarray, camera_face_id: str | None = None) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO enrolled_faces (name, source_photo, embedding, camera_face_id) VALUES (?, ?, ?, ?)",
            (name, source_photo, embedding.astype(np.float32).tobytes(), camera_face_id),
        )


def get_synced_camera_face_ids() -> set[str]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT camera_face_id FROM enrolled_faces WHERE camera_face_id IS NOT NULL"
        ).fetchall()
    return {r[0] for r in rows}


def load_all_faces() -> list[tuple[str, np.ndarray]]:
    with get_connection() as conn:
        rows = conn.execute("SELECT name, embedding FROM enrolled_faces").fetchall()
    return [(name, np.frombuffer(blob, dtype=np.float32)) for name, blob in rows]


def delete_face(name: str) -> list[str]:
    """Removes every enrolled sample for this person locally. Returns the
    source_photo filenames so the caller can also unlink the photo files."""
    with get_connection() as conn:
        rows = conn.execute("SELECT source_photo FROM enrolled_faces WHERE name = ?", (name,)).fetchall()
        conn.execute("DELETE FROM enrolled_faces WHERE name = ?", (name,))
    return [r[0] for r in rows]


def load_faces_with_photos() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("SELECT name, source_photo FROM enrolled_faces ORDER BY name").fetchall()
    grouped: dict[str, list[str]] = {}
    for name, source_photo in rows:
        grouped.setdefault(name, []).append(source_photo)
    return [{"name": name, "photos": photos, "sample_count": len(photos)} for name, photos in grouped.items()]


def log_detection_event(camera_id: int, name: str, bbox: list[int]) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO detection_events (ts, camera_id, name, bbox) VALUES (?, ?, ?, ?)",
            (time.time(), camera_id, name, str(bbox)),
        )


def count_detections_today() -> int:
    now = datetime.now()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM detection_events WHERE ts >= ?", (midnight,)
        ).fetchone()
    return row[0]


def get_attendance(date: str | None = None) -> list[dict]:
    """First/last-seen per recognized person for the given day (default
    today), built purely from the existing detection_events log — no
    separate capture logic needed."""
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    day_start = datetime.strptime(date, "%Y-%m-%d").timestamp()
    day_end = day_start + 86400
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT name, MIN(ts) AS first_seen, MAX(ts) AS last_seen,
                      GROUP_CONCAT(DISTINCT camera_id) AS cameras
               FROM detection_events
               WHERE ts >= ? AND ts < ? AND name != 'Unknown'
               GROUP BY name ORDER BY name""",
            (day_start, day_end),
        ).fetchall()
    return [dict(r) for r in rows]


def log_footfall(camera_id: int, direction: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO footfall_counts (ts, camera_id, direction) VALUES (?, ?, ?)",
            (time.time(), camera_id, direction),
        )


def count_footfall_today() -> dict:
    now = datetime.now()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT direction, COUNT(*) FROM footfall_counts WHERE ts >= ? GROUP BY direction", (midnight,)
        ).fetchall()
    counts = {"in": 0, "out": 0}
    for direction, count in rows:
        counts[direction] = count
    return counts
