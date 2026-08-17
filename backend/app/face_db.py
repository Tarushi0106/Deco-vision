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


def clear_faces() -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM enrolled_faces")


def add_face(name: str, source_photo: str, embedding: np.ndarray) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO enrolled_faces (name, source_photo, embedding) VALUES (?, ?, ?)",
            (name, source_photo, embedding.astype(np.float32).tobytes()),
        )


def load_all_faces() -> list[tuple[str, np.ndarray]]:
    with get_connection() as conn:
        rows = conn.execute("SELECT name, embedding FROM enrolled_faces").fetchall()
    return [(name, np.frombuffer(blob, dtype=np.float32)) for name, blob in rows]


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
