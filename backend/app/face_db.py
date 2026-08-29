import contextlib
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import numpy as np

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
        if "employee_id" not in existing_face_cols:
            # optional HR-facing ID code (e.g. "EMP01"), shown on the
            # Attendance roster — nobody has one until set explicitly
            conn.execute("ALTER TABLE enrolled_faces ADD COLUMN employee_id TEXT")
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
        if "score" not in existing_cols:
            # recognition similarity score (recognizer._match's return value)
            # at the moment this sighting was logged — powers the Attendance
            # roster's "Best Match" column
            conn.execute("ALTER TABLE detection_events ADD COLUMN score REAL")
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


def rename_face(old_name: str, new_name: str) -> int:
    """Renames every enrolled sample for this person, and cascades to past
    detection_events too — those rows store the name as a plain text snapshot,
    not a reference, so without this a rename leaves history fragmented under
    the old name, showing up as a second, orphaned person in Attendance/
    Analytics. Returns enrolled-sample rows affected (0 means old_name wasn't
    enrolled)."""
    with get_connection() as conn:
        cur = conn.execute("UPDATE enrolled_faces SET name = ? WHERE name = ?", (new_name, old_name))
        conn.execute("UPDATE detection_events SET name = ? WHERE name = ?", (new_name, old_name))
        return cur.rowcount


def load_faces_with_photos() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("SELECT name, source_photo, employee_id FROM enrolled_faces ORDER BY name").fetchall()
    grouped: dict[str, dict] = {}
    for name, source_photo, employee_id in rows:
        g = grouped.setdefault(name, {"photos": [], "employee_id": None})
        g["photos"].append(source_photo)
        if employee_id:
            g["employee_id"] = employee_id
    return [
        {"name": name, "photos": g["photos"], "sample_count": len(g["photos"]), "employee_id": g["employee_id"]}
        for name, g in grouped.items()
    ]


def log_detection_event(camera_id: int, name: str, bbox: list[int], score: float | None = None) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO detection_events (ts, camera_id, name, bbox, score) VALUES (?, ?, ?, ?, ?)",
            (time.time(), camera_id, name, str(bbox), score),
        )


def count_detections_today() -> int:
    now = datetime.now()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM detection_events WHERE ts >= ?", (midnight,)
        ).fetchone()
    return row[0]


def list_enrolled_roster() -> list[dict]:
    """Distinct enrolled people (one row per person, not per sample), with
    their employee_id if one's been set — the base roster the Attendance
    page cross-references detection_events against to also show who's
    ABSENT, not just who showed up."""
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT name, MAX(employee_id) AS employee_id FROM enrolled_faces GROUP BY name ORDER BY name"
        ).fetchall()
    return [dict(r) for r in rows]


def set_employee_id(name: str, employee_id: str | None) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE enrolled_faces SET employee_id = ? WHERE name = ?", (employee_id, name))


def get_daily_attendance_roster(date: str | None = None) -> dict:
    """The full day's attendance roster — every ENROLLED person, present or
    absent, built from list_enrolled_roster() + detection_events. A present
    person gets check_in/check_out (first/last sighting), which camera the
    LAST sighting was on, total detections, and the best (highest)
    recognition score seen that day; an absent person gets nulls and
    detections=0. This is the whole-roster counterpart to get_attendance()
    above, which only lists who showed up."""
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    day_start = datetime.strptime(date, "%Y-%m-%d").timestamp()
    day_end = day_start + 86400

    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        events = conn.execute(
            "SELECT name, ts, camera_id, score FROM detection_events "
            "WHERE ts >= ? AND ts < ? AND name != 'Unknown' ORDER BY ts",
            (day_start, day_end),
        ).fetchall()

    by_name: dict[str, dict] = {}
    for e in events:
        d = by_name.setdefault(
            e["name"],
            {"first_seen": e["ts"], "last_seen": e["ts"], "checkout_camera_id": e["camera_id"],
             "detections": 0, "best_match": None},
        )
        d["first_seen"] = min(d["first_seen"], e["ts"])
        if e["ts"] >= d["last_seen"]:
            d["last_seen"] = e["ts"]
            d["checkout_camera_id"] = e["camera_id"]
        d["detections"] += 1
        if e["score"] is not None:
            d["best_match"] = e["score"] if d["best_match"] is None else max(d["best_match"], e["score"])

    roster = []
    for person in list_enrolled_roster():
        name = person["name"]
        d = by_name.get(name)
        if d:
            roster.append({
                "name": name,
                "employee_id": person["employee_id"],
                "present": True,
                "check_in": d["first_seen"],
                "check_out": d["last_seen"],
                "checkout_camera_id": d["checkout_camera_id"],
                "time_stay_seconds": round(d["last_seen"] - d["first_seen"]),
                "detections": d["detections"],
                "best_match": d["best_match"],
            })
        else:
            roster.append({
                "name": name,
                "employee_id": person["employee_id"],
                "present": False,
                "check_in": None,
                "check_out": None,
                "checkout_camera_id": None,
                "time_stay_seconds": None,
                "detections": 0,
                "best_match": None,
            })

    present_count = sum(1 for r in roster if r["present"])
    return {
        "date": date,
        "present": present_count,
        "absent": len(roster) - present_count,
        "total_detections": len(events),
        "roster": roster,
    }


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


def get_attendance_report(name: str, start_date: str, end_date: str) -> list[dict]:
    """Day-by-day attendance breakdown for one person over [start_date, end_date]
    (inclusive, both "YYYY-MM-DD"), built from the same detection_events log as
    get_attendance/get_person_analytics — the detailed per-person view behind
    the Attendance page's downloadable report."""
    range_start = datetime.strptime(start_date, "%Y-%m-%d").timestamp()
    range_end = datetime.strptime(end_date, "%Y-%m-%d").timestamp() + 86400
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT ts, camera_id FROM detection_events WHERE name = ? AND ts >= ? AND ts < ? ORDER BY ts",
            (name, range_start, range_end),
        ).fetchall()

    days: dict[str, dict] = {}
    for row in rows:
        day_key = datetime.fromtimestamp(row["ts"]).strftime("%Y-%m-%d")
        d = days.setdefault(day_key, {
            "date": day_key, "first_seen": row["ts"], "last_seen": row["ts"],
            "cameras": set(), "total_detections": 0,
        })
        d["first_seen"] = min(d["first_seen"], row["ts"])
        d["last_seen"] = max(d["last_seen"], row["ts"])
        d["cameras"].add(row["camera_id"])
        d["total_detections"] += 1

    result = []
    for day_key in sorted(days):
        d = days[day_key]
        result.append({
            "date": d["date"],
            "first_seen": d["first_seen"],
            "last_seen": d["last_seen"],
            "camera_ids": sorted(d["cameras"]),
            "total_detections": d["total_detections"],
        })
    return result


def get_person_day_sessions(
    name: str, camera_id: int, date: str,
    grace_seconds: float, max_duration_seconds: float, min_duration_seconds: float,
) -> list[dict]:
    """Reconstructs presence sessions (ts, duration) for one person on one
    camera/day purely from detection_events — the same gap/chapter grouping
    pipeline.py's _update_clip_sessions uses live, just applied retroactively.
    detection_events is never pruned (unlike the old clips table, which used
    to cap at 30/person before that cap was removed), so this can rebuild
    "all the clips of that day" even for sightings whose clips row was
    deleted by that old cap or never created at all. See main.py's
    /api/people/{name}/clips-for-day, which uses this to backfill missing
    clips rows for replay-capable cameras."""
    day_start = datetime.strptime(date, "%Y-%m-%d").timestamp()
    day_end = day_start + 86400
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT ts FROM detection_events WHERE name = ? AND camera_id = ? AND ts >= ? AND ts < ? ORDER BY ts",
            (name, camera_id, day_start, day_end),
        ).fetchall()

    sessions = []
    session_start = session_last = None
    for (ts,) in rows:
        if session_start is None:
            session_start = session_last = ts
        elif ts - session_last > grace_seconds or ts - session_start >= max_duration_seconds:
            duration = max(session_last - session_start, min_duration_seconds * 2)
            sessions.append({"ts": session_start, "duration": duration})
            session_start = session_last = ts
        else:
            session_last = ts
    if session_start is not None:
        duration = max(session_last - session_start, min_duration_seconds * 2)
        sessions.append({"ts": session_start, "duration": duration})
    return sessions


def get_person_analytics(days: int = 7) -> list[dict]:
    """Per-person visit patterns over the last N days, built from
    detection_events (already deduped 30s per person/camera) — no new
    capture logic, just aggregation over data already being logged."""
    cutoff = time.time() - days * 86400
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT name, camera_id, ts FROM detection_events WHERE ts >= ? AND name != 'Unknown'",
            (cutoff,),
        ).fetchall()

    people: dict[str, dict] = {}
    for row in rows:
        p = people.setdefault(
            row["name"],
            {"cameras": {}, "hourly": [0] * 24, "days_seen": set(), "first_seen": row["ts"], "last_seen": row["ts"]},
        )
        p["cameras"][row["camera_id"]] = p["cameras"].get(row["camera_id"], 0) + 1
        dt = datetime.fromtimestamp(row["ts"])
        p["hourly"][dt.hour] += 1
        p["days_seen"].add(dt.date().isoformat())
        p["first_seen"] = min(p["first_seen"], row["ts"])
        p["last_seen"] = max(p["last_seen"], row["ts"])

    results = []
    for name, p in people.items():
        top_camera_id = max(p["cameras"], key=p["cameras"].get) if p["cameras"] else None
        results.append(
            {
                "name": name,
                "total_detections": sum(p["cameras"].values()),
                "days_seen": len(p["days_seen"]),
                "top_camera_id": top_camera_id,
                "first_seen": p["first_seen"],
                "last_seen": p["last_seen"],
                "hourly": p["hourly"],
            }
        )
    results.sort(key=lambda r: r["total_detections"], reverse=True)
    return results


def log_footfall(camera_id: int, direction: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO footfall_counts (ts, camera_id, direction) VALUES (?, ?, ?)",
            (time.time(), camera_id, direction),
        )


def get_people_counting_report(date: str | None = None) -> dict:
    """Raw IN/OUT midline-crossing counts for one day (see person_tracker.py)
    — every crossing counted, no identity, no dedup, unlike footfall_db's
    unique-footfall report (embedding-based, one row per distinct visit).
    This is the "people counting" view: total traffic through each camera's
    frame, not distinct visitors. Caller (main.py) attaches camera_name,
    matching how every other report in this app does it."""
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    day_start = datetime.strptime(date, "%Y-%m-%d").timestamp()
    day_end = day_start + 86400

    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT ts, camera_id, direction FROM footfall_counts WHERE ts >= ? AND ts < ? ORDER BY ts",
            (day_start, day_end),
        ).fetchall()

    hourly_in, hourly_out = [0] * 24, [0] * 24
    by_camera: dict[int, dict] = {}
    events = []
    total_in = total_out = 0
    for row in rows:
        event = dict(row)
        events.append(event)
        hour = datetime.fromtimestamp(event["ts"]).hour
        cam = by_camera.setdefault(event["camera_id"], {"camera_id": event["camera_id"], "in": 0, "out": 0})
        if event["direction"] == "in":
            hourly_in[hour] += 1
            cam["in"] += 1
            total_in += 1
        else:
            hourly_out[hour] += 1
            cam["out"] += 1
            total_out += 1

    return {
        "date": date,
        "total_in": total_in,
        "total_out": total_out,
        "total": total_in + total_out,
        "hourly_in": hourly_in,
        "hourly_out": hourly_out,
        "by_camera": sorted(by_camera.values(), key=lambda c: c["camera_id"]),
        "events": events,
    }


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
