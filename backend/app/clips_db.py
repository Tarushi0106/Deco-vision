"""Recognition clips — a short local video saved around each new-person
detection event (see pipeline.py's CameraPipeline), so Analytics can show
"what did we actually see" instead of just a count.
"""

from __future__ import annotations
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "app.db"
CLIPS_DIR = Path(__file__).resolve().parent.parent / "data" / "clips"


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DB_PATH)


def init_db() -> None:
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS clips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                person_name TEXT NOT NULL,
                camera_id INTEGER,
                ts REAL NOT NULL,
                duration REAL NOT NULL,
                file_path TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_clips_person ON clips (person_name)")


def log_clip(person_name: str, camera_id: int, ts: float, duration: float, file_path: str) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO clips (person_name, camera_id, ts, duration, file_path) VALUES (?, ?, ?, ?, ?)",
            (person_name, camera_id, ts, duration, file_path),
        )
        return cur.lastrowid


def rename_person_clips(old_name: str, new_name: str) -> None:
    """Cascades a People rename to past clips — same reasoning as
    face_db.rename_face's detection_events cascade: person_name here is a
    text snapshot, not a reference, so without this a rename leaves old
    clips orphaned under the old name."""
    with get_connection() as conn:
        conn.execute("UPDATE clips SET person_name = ? WHERE person_name = ?", (new_name, old_name))


def list_clips_for_person(person_name: str, limit: int = 50) -> list[dict]:
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM clips WHERE person_name = ? ORDER BY ts DESC LIMIT ?",
            (person_name, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def count_clips_for_person(person_name: str) -> int:
    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) FROM clips WHERE person_name = ?", (person_name,)).fetchone()
    return row[0]


def get_clip(clip_id: int) -> dict | None:
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
    return dict(row) if row else None


def prune_old_clips(person_name: str, keep: int) -> None:
    """Keep only the most recent `keep` clips for this person — clip files
    are small but unbounded retention would fill the disk over time."""
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, file_path FROM clips WHERE person_name = ? ORDER BY ts DESC",
            (person_name,),
        ).fetchall()
        for row in rows[keep:]:
            Path(row["file_path"]).unlink(missing_ok=True)
            conn.execute("DELETE FROM clips WHERE id = ?", (row["id"],))
