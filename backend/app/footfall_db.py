"""Storage for unique footfall visits (see footfall_counter.py).

Deliberately a separate table from the existing footfall_counts table in
face_db.py: that one logs anonymous directional midline crossings ("in"/
"out") from pose tracking, with no identity and no dedup. This table stores
one row per DISTINCT visit — an embedding-based person_key, which camera saw
them, and the first/last time they were seen within that visit's dedup
window — powering the daily unique-footfall report.
"""

import contextlib
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import numpy as np
from openpyxl import Workbook

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
            CREATE TABLE IF NOT EXISTS footfall_visits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                person_key TEXT NOT NULL,
                camera_id INTEGER NOT NULL,
                embedding BLOB NOT NULL,
                first_seen REAL NOT NULL,
                last_seen REAL NOT NULL
            )
            """
        )
        existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(footfall_visits)")}
        if "name" not in existing_cols:
            # the enrolled person's name, when recognizer.py matched one for
            # this visit — NULL for a visit that stayed Unknown throughout
            # (footfall counts every distinct face, named or not; see
            # footfall_counter.py). Reports display this name in place of
            # the anonymous person_key when it's set.
            conn.execute("ALTER TABLE footfall_visits ADD COLUMN name TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_footfall_visits_camera_last_seen "
            "ON footfall_visits (camera_id, last_seen)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_footfall_visits_first_seen "
            "ON footfall_visits (first_seen)"
        )


def create_visit(person_key: str, camera_id: int, embedding: np.ndarray, ts: float, name: str | None = None) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO footfall_visits (person_key, camera_id, embedding, first_seen, last_seen, name) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (person_key, camera_id, embedding.astype(np.float32).tobytes(), ts, ts, _clean_name(name)),
        )


def touch_visit(person_key: str, ts: float, name: str | None = None) -> None:
    """Refreshes last_seen for an already-counted visit. Also upgrades the
    stored name if this later sighting recognized someone the earlier one(s)
    didn't (e.g. a bad angle on entry, a clean match a few seconds later) —
    never the other way around: a None/"Unknown" name here never clears an
    already-stored real name."""
    clean = _clean_name(name)
    with get_connection() as conn:
        if clean is not None:
            conn.execute(
                "UPDATE footfall_visits SET last_seen = ?, name = ? WHERE person_key = ?",
                (ts, clean, person_key),
            )
        else:
            conn.execute("UPDATE footfall_visits SET last_seen = ? WHERE person_key = ?", (ts, person_key))


def _clean_name(name: str | None) -> str | None:
    return name if name and name != "Unknown" else None


def load_open_visits(window_seconds: float) -> list[dict]:
    """Visits still inside the re-identification window as of now — used to
    rehydrate FootfallCounter's in-memory cache on startup so a process
    restart mid-window doesn't recount someone still in frame."""
    cutoff = time.time() - window_seconds
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT person_key, camera_id, embedding, first_seen, last_seen, name FROM footfall_visits "
            "WHERE last_seen >= ?",
            (cutoff,),
        ).fetchall()
    return [
        {
            "person_key": r["person_key"],
            "camera_id": r["camera_id"],
            "embedding": np.frombuffer(r["embedding"], dtype=np.float32),
            "first_seen": r["first_seen"],
            "last_seen": r["last_seen"],
            "name": r["name"],
        }
        for r in rows
    ]


def get_daily_report(date: str | None = None) -> dict:
    """Full daily unique-footfall report: total count, an hourly breakdown
    (bucketed by each visit's first-seen hour), a camera breakdown, and the
    raw per-visit rows for the combined Person / First Seen / Camera / Last
    Seen table. Camera IDs are returned as-is; the caller (main.py / the
    report script) attaches camera names, matching how the attendance
    report already does it."""
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    day_start = datetime.strptime(date, "%Y-%m-%d").timestamp()
    day_end = day_start + 86400

    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT person_key, camera_id, first_seen, last_seen, name FROM footfall_visits "
            "WHERE first_seen >= ? AND first_seen < ? ORDER BY first_seen",
            (day_start, day_end),
        ).fetchall()

    hourly = [0] * 24
    by_camera: dict[int, int] = {}
    visits = []
    for row in rows:
        visit = dict(row)
        hourly[datetime.fromtimestamp(visit["first_seen"]).hour] += 1
        by_camera[visit["camera_id"]] = by_camera.get(visit["camera_id"], 0) + 1
        visits.append(visit)

    return {
        "date": date,
        "total": len(visits),
        "hourly": hourly,
        "by_camera": [{"camera_id": cid, "count": count} for cid, count in sorted(by_camera.items())],
        "visits": visits,
    }


def _display_name(visit: dict) -> str:
    """The report shows the recognized person's name when footfall_counter.py
    got one; an anonymous visit (never matched an enrolled face) falls back
    to "Unknown" — same convention as everywhere else in this app (Attendance,
    Analytics) rather than exposing the raw embedding-derived person_key."""
    return visit.get("name") or "Unknown"


def write_csv_rows(report: dict, writer) -> None:
    """Writes the combined Person / First Seen / Camera / Last Seen table to
    a csv.writer — shared by the /api/footfall/report/csv endpoint and
    scripts/generate_footfall_report.py. Same camera_name expectation as
    build_workbook() above."""
    writer.writerow(["Person", "First Seen", "Camera", "Last Seen"])
    for v in report["visits"]:
        writer.writerow([
            _display_name(v),
            datetime.fromtimestamp(v["first_seen"]).strftime("%Y-%m-%d %H:%M:%S"),
            v["camera_name"],
            datetime.fromtimestamp(v["last_seen"]).strftime("%Y-%m-%d %H:%M:%S"),
        ])


def build_workbook(report: dict) -> Workbook:
    """Builds the Excel export for a get_daily_report() result — a
    "Footfall" sheet with the combined Person / First Seen / Camera / Last
    Seen table, plus a "Summary" sheet with the hourly/camera breakdowns.
    Shared by the /api/footfall/report/xlsx endpoint in main.py and
    scripts/generate_footfall_report.py so both produce identical files.

    Expects each entry in report["visits"]/report["by_camera"] to already
    carry a "camera_name" key — get_daily_report() only knows camera_id, so
    the caller attaches names (see footfall_report.enrich_with_camera_names)."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Footfall"
    ws.append(["Person", "First Seen", "Camera", "Last Seen"])
    for v in report["visits"]:
        ws.append([
            _display_name(v),
            datetime.fromtimestamp(v["first_seen"]).strftime("%Y-%m-%d %H:%M:%S"),
            v["camera_name"],
            datetime.fromtimestamp(v["last_seen"]).strftime("%Y-%m-%d %H:%M:%S"),
        ])

    summary = wb.create_sheet("Summary")
    summary.append(["Date", report["date"]])
    summary.append(["Total Unique Footfall", report["total"]])
    summary.append([])
    summary.append(["Hour", "Unique Visitors"])
    for hour, count in enumerate(report["hourly"]):
        summary.append([f"{hour:02d}:00", count])
    summary.append([])
    summary.append(["Camera", "Unique Visitors"])
    for cam in report["by_camera"]:
        summary.append([cam["camera_name"], cam["count"]])

    return wb
