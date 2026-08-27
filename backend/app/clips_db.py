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

# Sentinel file_path for a replay-camera clip whose fetch was attempted and
# came back with no footage (the camera's own onboard recording has already
# overwritten that window - confirmed live, see onvif_client.py). Distinct
# from "" (never attempted yet): a "" clip is still a live candidate for
# on-demand or background prefetch; this one is a settled miss, never
# retried again, so a doomed 2-3 minute fetch isn't repeated on every play
# click or every prefetch pass.
UNAVAILABLE_SENTINEL = "unavailable"


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


def set_clip_file_path(clip_id: int, file_path: str) -> None:
    """Fills in a replay-camera clip's file_path after its first on-demand
    fetch from the camera (see onvif_client.py) - caches the result so
    every later play of the same clip is instant instead of re-fetching.
    Also used to write UNAVAILABLE_SENTINEL after a failed fetch."""
    with get_connection() as conn:
        conn.execute("UPDATE clips SET file_path = ? WHERE id = ?", (file_path, clip_id))


def list_pending_replay_clips(camera_ids: list[int], retention_days: float, limit: int) -> list[dict]:
    """Clips on a replay camera (config.CAMERA_ONVIF_REPLAY_CHANNEL) that
    haven't been fetched yet (file_path == "", never attempted - see
    UNAVAILABLE_SENTINEL for a settled miss) and are still within the
    retention window - the backlog replay_prefetch.py works through in the
    background so clips are cached ahead of anyone clicking Play. Newest
    first: a recent clip is both more likely to still be on the camera's
    own storage and more likely to matter to someone browsing right now."""
    if not camera_ids:
        return []
    cutoff = time.time() - retention_days * 86400
    placeholders = ",".join("?" * len(camera_ids))
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT id, camera_id, ts, duration FROM clips "
            f"WHERE camera_id IN ({placeholders}) AND file_path = '' AND ts >= ? "
            f"ORDER BY ts DESC LIMIT ?",
            (*camera_ids, cutoff, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def delete_expired_clips(retention_days: float) -> int:
    """Deletes clips (row + local video file, if any) older than
    retention_days - the rolling recording-history window (see
    config.CLIP_RETENTION_DAYS), run daily by scheduler.py. Files are
    removed before their row so a crash mid-prune can only leave a stray
    orphaned file (wastes disk, harmless) rather than a DB row pointing at
    an already-deleted file (would 404 confusingly on play). Returns how
    many clips were deleted."""
    cutoff = time.time() - retention_days * 86400
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT id, file_path FROM clips WHERE ts < ?", (cutoff,)).fetchall()
        for row in rows:
            if row["file_path"] and row["file_path"] != UNAVAILABLE_SENTINEL:
                Path(row["file_path"]).unlink(missing_ok=True)
        conn.execute("DELETE FROM clips WHERE ts < ?", (cutoff,))
    return len(rows)
