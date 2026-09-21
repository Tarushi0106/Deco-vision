"""Continuous face-collection / manual-labeling / retraining storage.

Same DB file, same raw-sqlite3-with-CREATE-TABLE-IF-NOT-EXISTS style as
face_db.py — deliberately not a separate database: this is additional
state about the SAME recognition system (enrolled_faces), not a competing
one. "Training" in this app has no separate model file to fit — recognition
is nearest-neighbor cosine similarity straight against enrolled_faces (see
recognizer.py) — so "promoting" a labeled sample means inserting its
embedding into that same table and asking every live worker to reload it
(pipeline_manager.reload_faces(), which already existed for this exact
purpose before this feature).

Tables:
  pending_face_samples: one row per collected Unknown-face crop (image on
    disk under backend/data/face_training_samples/, gitignored same as the
    rest of backend/data/). status moves pending -> labeled -> promoted (or
    -> skipped). A sample is never deleted by this module — an operator's
    labeling work and the crops themselves are retained even after
    promotion, so the dataset only ever grows.
  face_training_runs: one row per completed promotion batch, for the
    training-status API and history.
"""

import contextlib
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import numpy as np

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "app.db"
SAMPLES_DIR = Path(__file__).resolve().parent.parent / "data" / "face_training_samples"


@contextlib.contextmanager
def _connect():
    """Same closed-on-exit pattern as face_db.get_connection — a bare
    sqlite3 connection left open per call leaks a file descriptor until the
    process eventually exhausts its open-file limit (see that module's
    comment for the incident this fixed there)."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db() -> None:
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
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
            "CREATE INDEX IF NOT EXISTS idx_pending_face_samples_camera "
            "ON pending_face_samples(camera_id)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS face_training_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trained_at REAL NOT NULL,
                samples_considered INTEGER NOT NULL,
                samples_promoted INTEGER NOT NULL,
                samples_rejected INTEGER NOT NULL,
                total_enrolled_people INTEGER NOT NULL,
                total_enrolled_samples INTEGER NOT NULL,
                validation_accuracy REAL,
                validation_samples INTEGER,
                per_person_json TEXT,
                status TEXT NOT NULL
            )
            """
        )


def add_pending_sample(
    camera_id: int | None, image_path: str, embedding: np.ndarray, det_score: float | None,
) -> int:
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
            "SELECT COUNT(*) FROM pending_face_samples WHERE camera_id = ? AND status = 'pending'",
            (camera_id,),
        ).fetchone()
    return row[0]


def get_next_pending() -> dict | None:
    """Oldest pending sample first — works through a backlog in the order
    it was collected rather than newest-first, which would let old samples
    sit forever if collection keeps outpacing labeling."""
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
    """Marks a sample labeled — does NOT insert it into enrolled_faces yet
    (see module docstring: promotion is a separate, batched step run by
    face_training_scheduler.py). Returns False if the sample doesn't exist
    or was already labeled/skipped/promoted (caller's job to report that,
    not silently relabel)."""
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
            "UPDATE pending_face_samples SET status = 'skipped' WHERE id = ? AND status = 'pending'",
            (sample_id,),
        )
        return cur.rowcount > 0


def get_labeled_awaiting_promotion() -> list[dict]:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, camera_id, image_path, embedding, name FROM pending_face_samples "
            "WHERE status = 'labeled' ORDER BY labeled_at ASC"
        ).fetchall()
    return [
        {**dict(r), "embedding": np.frombuffer(r["embedding"], dtype=np.float32)}
        for r in rows
    ]


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
    """A labeled sample that failed validation (see
    face_training_scheduler.py's conflict check) goes back to 'pending' —
    NOT deleted, NOT silently promoted — with reject_reason recorded so an
    operator can see why and relabel or skip it explicitly. Distinct from
    skip_sample: this is the system flagging a problem, not an operator
    choice."""
    with _connect() as conn:
        conn.execute(
            "UPDATE pending_face_samples SET status = 'pending', name = NULL, labeled_at = NULL, "
            "reject_reason = ? WHERE id = ?",
            (reason, sample_id),
        )


def count_by_status() -> dict[str, int]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) FROM pending_face_samples GROUP BY status"
        ).fetchall()
    return {status: count for status, count in rows}


def count_captured_since(ts: float) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM pending_face_samples WHERE captured_at >= ?", (ts,)
        ).fetchone()
    return row[0]


def count_labeled_since(ts: float) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM pending_face_samples WHERE labeled_at IS NOT NULL AND labeled_at >= ?", (ts,)
        ).fetchone()
    return row[0]


def count_captured_today() -> int:
    midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    return count_captured_since(midnight)


def count_labeled_today() -> int:
    midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    return count_labeled_since(midnight)


def add_training_run(
    samples_considered: int, samples_promoted: int, samples_rejected: int,
    total_enrolled_people: int, total_enrolled_samples: int, status: str,
    validation_accuracy: float | None = None, validation_samples: int | None = None,
    per_person_json: str | None = None,
) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO face_training_runs "
            "(trained_at, samples_considered, samples_promoted, samples_rejected, total_enrolled_people, "
            " total_enrolled_samples, validation_accuracy, validation_samples, per_person_json, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), samples_considered, samples_promoted, samples_rejected, total_enrolled_people,
             total_enrolled_samples, validation_accuracy, validation_samples, per_person_json, status),
        )
        return cur.lastrowid


def get_latest_training_run() -> dict | None:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM face_training_runs ORDER BY trained_at DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def list_training_runs(limit: int = 20) -> list[dict]:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM face_training_runs ORDER BY trained_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]
