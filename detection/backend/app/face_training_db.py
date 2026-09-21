"""This service's own SQLite database — entirely separate from Parachute's
app.db. Same raw-sqlite3-with-CREATE-TABLE-IF-NOT-EXISTS style used
throughout Parachute's own *_db.py modules (for consistency/readability),
but a completely independent file and schema — this service never opens,
reads, or writes Parachute's database.
"""

import contextlib
import sqlite3
import time
from datetime import datetime

import numpy as np

from . import config


@contextlib.contextmanager
def _connect():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db() -> None:
    config.SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_face_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                camera_id INTEGER,
                captured_at REAL NOT NULL,
                image_path TEXT NOT NULL,
                embedding BLOB NOT NULL,
                det_score REAL,
                status TEXT NOT NULL DEFAULT 'pending',
                name TEXT,
                labeled_at REAL,
                promoted_at REAL,
                reject_reason TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pending_face_samples_status "
            "ON pending_face_samples(status, captured_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pending_face_samples_camera ON pending_face_samples(camera_id)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS face_training_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trained_at REAL NOT NULL,
                samples_considered INTEGER NOT NULL,
                samples_promoted INTEGER NOT NULL,
                samples_rejected INTEGER NOT NULL,
                total_gallery_people INTEGER NOT NULL,
                total_gallery_samples INTEGER NOT NULL,
                validation_accuracy REAL,
                validation_samples INTEGER,
                per_person_json TEXT,
                status TEXT NOT NULL
            )
            """
        )


def add_pending_sample(camera_id: int | None, image_path: str, embedding: np.ndarray, det_score: float | None) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO pending_face_samples (camera_id, captured_at, image_path, embedding, det_score) "
            "VALUES (?, ?, ?, ?, ?)",
            (camera_id, time.time(), image_path, embedding.astype(np.float32).tobytes(), det_score),
        )
        return cur.lastrowid


def count_pending_for_camera(camera_id: int) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM pending_face_samples WHERE camera_id = ? AND status = 'pending'", (camera_id,)
        ).fetchone()
    return row[0]


def get_next_pending() -> dict | None:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, camera_id, captured_at, image_path, det_score FROM pending_face_samples "
            "WHERE status = 'pending' ORDER BY captured_at ASC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def get_sample(sample_id: int) -> dict | None:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM pending_face_samples WHERE id = ?", (sample_id,)).fetchone()
    return dict(row) if row else None


def label_sample(sample_id: int, name: str) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE pending_face_samples SET status = 'labeled', name = ?, labeled_at = ? "
            "WHERE id = ? AND status = 'pending'",
            (name, time.time(), sample_id),
        )
        return cur.rowcount > 0


def skip_sample(sample_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE pending_face_samples SET status = 'skipped' WHERE id = ? AND status = 'pending'", (sample_id,)
        )
        return cur.rowcount > 0


def get_labeled_awaiting_promotion() -> list[dict]:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, camera_id, image_path, embedding, name FROM pending_face_samples "
            "WHERE status = 'labeled' ORDER BY labeled_at ASC"
        ).fetchall()
    return [{**dict(r), "embedding": np.frombuffer(r["embedding"], dtype=np.float32)} for r in rows]


def mark_promoted(sample_ids: list[int]) -> None:
    if not sample_ids:
        return
    with _connect() as conn:
        placeholders = ",".join("?" * len(sample_ids))
        conn.execute(
            f"UPDATE pending_face_samples SET status = 'promoted', promoted_at = ? WHERE id IN ({placeholders})",
            (time.time(), *sample_ids),
        )


def mark_rejected(sample_id: int, reason: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE pending_face_samples SET status = 'pending', name = NULL, labeled_at = NULL, "
            "reject_reason = ? WHERE id = ?",
            (reason, sample_id),
        )


def count_by_status() -> dict[str, int]:
    with _connect() as conn:
        rows = conn.execute("SELECT status, COUNT(*) FROM pending_face_samples GROUP BY status").fetchall()
    return {status: count for status, count in rows}


def count_captured_since(ts: float) -> int:
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(*) FROM pending_face_samples WHERE captured_at >= ?", (ts,)).fetchone()
    return row[0]


def count_labeled_since(ts: float) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM pending_face_samples WHERE labeled_at IS NOT NULL AND labeled_at >= ?", (ts,)
        ).fetchone()
    return row[0]


def count_captured_today() -> int:
    return count_captured_since(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def count_labeled_today() -> int:
    return count_labeled_since(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def add_training_run(
    samples_considered: int, samples_promoted: int, samples_rejected: int,
    total_gallery_people: int, total_gallery_samples: int, status: str,
    validation_accuracy: float | None = None, validation_samples: int | None = None,
    per_person_json: str | None = None,
) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO face_training_runs "
            "(trained_at, samples_considered, samples_promoted, samples_rejected, total_gallery_people, "
            " total_gallery_samples, validation_accuracy, validation_samples, per_person_json, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), samples_considered, samples_promoted, samples_rejected, total_gallery_people,
             total_gallery_samples, validation_accuracy, validation_samples, per_person_json, status),
        )
        return cur.lastrowid


def get_latest_training_run() -> dict | None:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM face_training_runs ORDER BY trained_at DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def list_training_runs(limit: int = 20) -> list[dict]:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM face_training_runs ORDER BY trained_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]
