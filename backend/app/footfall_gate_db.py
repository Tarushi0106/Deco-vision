"""Storage for the footfall gate line (see gate_tracker.py for the live
crossing-detection side). One line per camera — a real gate/doorway has one
threshold, not several — stored as two endpoint points (normalized 0..1
fractions of frame width/height, so it survives a camera resolution change)
plus which side counts as "outside" (entry_sign): crossing FROM the
outside side TO the inside side is what counts as an entry.
"""

import contextlib
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "app.db"


@contextlib.contextmanager
def get_connection():
    """Closed on exit — see alerts_db.get_connection for why this matters
    (sqlite3's own `with conn:` never closes the connection, which leaked
    a file descriptor per call and eventually exhausted the process's
    open-file limit)."""
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
            CREATE TABLE IF NOT EXISTS footfall_gates (
                camera_id INTEGER PRIMARY KEY,
                x1 REAL NOT NULL,
                y1 REAL NOT NULL,
                x2 REAL NOT NULL,
                y2 REAL NOT NULL,
                entry_sign INTEGER NOT NULL DEFAULT 1
            )
            """
        )


def get_gate(camera_id: int) -> dict | None:
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM footfall_gates WHERE camera_id = ?", (camera_id,)).fetchone()
    return dict(row) if row else None


def list_gates() -> list[dict]:
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM footfall_gates").fetchall()
    return [dict(r) for r in rows]


def set_gate(camera_id: int, x1: float, y1: float, x2: float, y2: float, entry_sign: int = 1) -> None:
    """One gate per camera — drawing a new line replaces the old one."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO footfall_gates (camera_id, x1, y1, x2, y2, entry_sign) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(camera_id) DO UPDATE SET x1=excluded.x1, y1=excluded.y1, x2=excluded.x2, "
            "y2=excluded.y2, entry_sign=excluded.entry_sign",
            (camera_id, x1, y1, x2, y2, entry_sign),
        )


def flip_gate_direction(camera_id: int) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE footfall_gates SET entry_sign = -entry_sign WHERE camera_id = ?", (camera_id,))


def delete_gate(camera_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM footfall_gates WHERE camera_id = ?", (camera_id,))
