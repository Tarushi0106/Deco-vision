"""Storage for desk-time / productivity analytics (see desk_tracker.py for
the live tracking side, which is where identity gets bound to a zone —
nothing here decides WHO is at a desk, only records what the tracker
already decided). Four tables:

  - desk_zones: admin-drawn rectangles (normalized 0..1 fractions of frame
    width/height, so they survive a camera resolution change) — anonymous,
    auto-labeled ("Desk 1", "Desk 2", ...). NOT tied to an employee; who
    occupies a zone is resolved dynamically every detection cycle.
  - desk_sessions: one row per continuous stretch an employee was detected
    occupying a specific zone — start_ts set once, end_ts (last confirmed
    still there) updated on every touch, the same create/touch shape
    footfall_db.py uses for visits.
  - away_sessions: the mirror image — one row per continuous stretch an
    employee was AWAY from every zone (but still "at work" per attendance/
    face recognition elsewhere, or simply unaccounted for).
  - desk_movement_events: an append-only audit log of every state
    transition (session start/end, away start/end, desk switch), each with
    the recognition confidence that triggered it — this is both the
    "Desk Movement History" and the debugging trail.
"""

import sqlite3
import time
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "app.db"


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DB_PATH)


def init_db() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS desk_zones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                camera_id INTEGER NOT NULL,
                zone_label TEXT NOT NULL,
                x1 REAL NOT NULL,
                y1 REAL NOT NULL,
                x2 REAL NOT NULL,
                y2 REAL NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
        _migrate_zones_off_static_employee(conn)

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS desk_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                zone_id INTEGER NOT NULL,
                employee_name TEXT NOT NULL,
                camera_id INTEGER NOT NULL,
                start_ts REAL NOT NULL,
                end_ts REAL NOT NULL,
                last_confidence REAL
            )
            """
        )
        existing_session_cols = {row[1] for row in conn.execute("PRAGMA table_info(desk_sessions)")}
        if "last_confidence" not in existing_session_cols:
            conn.execute("ALTER TABLE desk_sessions ADD COLUMN last_confidence REAL")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS away_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_name TEXT NOT NULL,
                start_ts REAL NOT NULL,
                end_ts REAL NOT NULL
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS desk_movement_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_name TEXT NOT NULL,
                event_type TEXT NOT NULL,
                zone_id INTEGER,
                ts REAL NOT NULL,
                confidence REAL,
                details TEXT
            )
            """
        )

        conn.execute("CREATE INDEX IF NOT EXISTS idx_desk_zones_camera ON desk_zones (camera_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_desk_sessions_start ON desk_sessions (start_ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_desk_sessions_employee ON desk_sessions (employee_name, start_ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_away_sessions_start ON away_sessions (start_ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_away_sessions_employee ON away_sessions (employee_name, start_ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_movement_events_employee ON desk_movement_events (employee_name, ts)")


def _migrate_zones_off_static_employee(conn: sqlite3.Connection) -> None:
    """Zones used to be created pre-assigned to one employee_name (NOT
    NULL). Moving to dynamic assignment: add zone_label, backfill any
    existing rows with an auto-generated "Desk N" label (their old static
    employee_name is not reused as the label — that was WHO sat there, not
    the desk's identity), then drop the now-unused column."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(desk_zones)")}
    if "zone_label" not in cols:
        conn.execute("ALTER TABLE desk_zones ADD COLUMN zone_label TEXT")
    if "employee_name" in cols:
        unlabeled = conn.execute(
            "SELECT id, camera_id FROM desk_zones WHERE zone_label IS NULL ORDER BY camera_id, id"
        ).fetchall()
        per_camera_seq: dict[int, int] = {}
        for zone_id, camera_id in unlabeled:
            per_camera_seq[camera_id] = per_camera_seq.get(camera_id, 0) + 1
            conn.execute(
                "UPDATE desk_zones SET zone_label = ? WHERE id = ?",
                (f"Desk {per_camera_seq[camera_id]}", zone_id),
            )
        conn.execute("ALTER TABLE desk_zones DROP COLUMN employee_name")


# ---- Zones ----

def list_zones(camera_id: int | None = None) -> list[dict]:
    query = "SELECT * FROM desk_zones"
    params = []
    if camera_id is not None:
        query += " WHERE camera_id = ?"
        params.append(camera_id)
    query += " ORDER BY id"
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def get_zone(zone_id: int) -> dict | None:
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM desk_zones WHERE id = ?", (zone_id,)).fetchone()
    return dict(row) if row else None


def create_zone(camera_id: int, x1: float, y1: float, x2: float, y2: float) -> int:
    """Auto-labels "Desk N" — the next unused number for this camera, not
    just count+1, so deleting and re-adding zones doesn't produce
    duplicate labels."""
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT zone_label FROM desk_zones WHERE camera_id = ?", (camera_id,)
        ).fetchall()
        used_numbers = set()
        for (label,) in existing:
            if label and label.startswith("Desk "):
                try:
                    used_numbers.add(int(label.removeprefix("Desk ")))
                except ValueError:
                    pass
        next_number = 1
        while next_number in used_numbers:
            next_number += 1
        zone_label = f"Desk {next_number}"

        cur = conn.execute(
            "INSERT INTO desk_zones (camera_id, zone_label, x1, y1, x2, y2, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (camera_id, zone_label, x1, y1, x2, y2, time.time()),
        )
        return cur.lastrowid


def update_zone(zone_id: int, **fields) -> None:
    if not fields:
        return
    columns = ", ".join(f"{k} = ?" for k in fields)
    with get_connection() as conn:
        conn.execute(f"UPDATE desk_zones SET {columns} WHERE id = ?", (*fields.values(), zone_id))


def delete_zone(zone_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM desk_zones WHERE id = ?", (zone_id,))
        # sessions/events already logged for this zone stay — historical
        # fact ("someone sat at Desk 3 from 9-11am") independent of whether
        # the zone itself still exists.


# ---- Desk sessions ----

def start_session(zone_id: int, employee_name: str, camera_id: int, ts: float, confidence: float | None) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO desk_sessions (zone_id, employee_name, camera_id, start_ts, end_ts, last_confidence) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (zone_id, employee_name, camera_id, ts, ts, confidence),
        )
        return cur.lastrowid


def touch_session(session_id: int, ts: float, confidence: float | None = None) -> None:
    with get_connection() as conn:
        if confidence is not None:
            conn.execute(
                "UPDATE desk_sessions SET end_ts = ?, last_confidence = ? WHERE id = ?",
                (ts, confidence, session_id),
            )
        else:
            conn.execute("UPDATE desk_sessions SET end_ts = ? WHERE id = ?", (ts, session_id))


def load_open_sessions(grace_seconds: float) -> list[dict]:
    """Sessions still inside the tracker's grace period as of now — used to
    rehydrate DeskTracker's in-memory state on startup."""
    cutoff = time.time() - grace_seconds
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM desk_sessions WHERE end_ts >= ?", (cutoff,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_sessions_for_day(date: str, employee_name: str | None = None) -> list[dict]:
    day_start = datetime.strptime(date, "%Y-%m-%d").timestamp()
    day_end = day_start + 86400
    query = "SELECT * FROM desk_sessions WHERE start_ts >= ? AND start_ts < ?"
    params = [day_start, day_end]
    if employee_name is not None:
        query += " AND employee_name = ?"
        params.append(employee_name)
    query += " ORDER BY start_ts"
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


# ---- Away sessions ----

def start_away(employee_name: str, ts: float) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO away_sessions (employee_name, start_ts, end_ts) VALUES (?, ?, ?)",
            (employee_name, ts, ts),
        )
        return cur.lastrowid


def touch_away(away_id: int, ts: float) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE away_sessions SET end_ts = ? WHERE id = ?", (ts, away_id))


def load_open_away(grace_seconds: float) -> list[dict]:
    cutoff = time.time() - grace_seconds
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM away_sessions WHERE end_ts >= ?", (cutoff,)).fetchall()
    return [dict(r) for r in rows]


def get_away_for_day(date: str, employee_name: str | None = None) -> list[dict]:
    day_start = datetime.strptime(date, "%Y-%m-%d").timestamp()
    day_end = day_start + 86400
    query = "SELECT * FROM away_sessions WHERE start_ts >= ? AND start_ts < ?"
    params = [day_start, day_end]
    if employee_name is not None:
        query += " AND employee_name = ?"
        params.append(employee_name)
    query += " ORDER BY start_ts"
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


# ---- Movement / audit log ----

def log_event(
    employee_name: str, event_type: str, ts: float,
    zone_id: int | None = None, confidence: float | None = None, details: str | None = None,
) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO desk_movement_events (employee_name, event_type, zone_id, ts, confidence, details) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (employee_name, event_type, zone_id, ts, confidence, details),
        )


def get_events_for_day(date: str, employee_name: str | None = None) -> list[dict]:
    day_start = datetime.strptime(date, "%Y-%m-%d").timestamp()
    day_end = day_start + 86400
    query = "SELECT * FROM desk_movement_events WHERE ts >= ? AND ts < ?"
    params = [day_start, day_end]
    if employee_name is not None:
        query += " AND employee_name = ?"
        params.append(employee_name)
    query += " ORDER BY ts"
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


# ---- Report ----

def get_daily_report(date: str | None = None) -> dict:
    """Per-employee daily desk-time report, entirely from real tracked
    sessions (no derived/estimated figures): desk_seconds (sum of desk
    session durations), away_seconds (sum of away session durations),
    movements (desk-switch count — see desk_tracker.py), current desk/
    status (whichever session, if any, is still open), first/last session.
    Only includes employees the tracker has seen at least once today (at a
    desk or away) — someone never observed simply doesn't appear."""
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    desk_sessions = get_sessions_for_day(date)
    away_sessions = get_away_for_day(date)
    events = get_events_for_day(date)

    by_employee: dict[str, dict] = {}

    def bucket(name):
        return by_employee.setdefault(name, {"desk": [], "away": [], "switches": 0})

    for s in desk_sessions:
        bucket(s["employee_name"])["desk"].append(s)
    for a in away_sessions:
        bucket(a["employee_name"])["away"].append(a)
    for e in events:
        if e["event_type"] == "desk_switch":
            bucket(e["employee_name"])["switches"] += 1

    employees = []
    for name, b in by_employee.items():
        desk_seconds = sum(s["end_ts"] - s["start_ts"] for s in b["desk"])
        away_seconds = sum(a["end_ts"] - a["start_ts"] for a in b["away"])

        all_sessions = [(s["start_ts"], s["end_ts"], s["zone_id"]) for s in b["desk"]] + \
                       [(a["start_ts"], a["end_ts"], None) for a in b["away"]]
        first_session = min(s[0] for s in all_sessions)
        last_session = max(s[1] for s in all_sessions)

        employees.append({
            "employee_name": name,
            "desk_seconds": round(desk_seconds),
            "away_seconds": round(away_seconds),
            "movements": b["switches"],
            "first_session": first_session,
            "last_session": last_session,
        })
    employees.sort(key=lambda e: -e["desk_seconds"])

    return {"date": date, "employees": employees}
