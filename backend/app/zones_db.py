"""Restricted-zone registry — a zone is a polygon on one camera's frame plus
an allow-list of enrolled person names (face_db "name" values). Anyone else
detected inside the polygon raises a "zone_intrusion" alert (see pipeline.py
PipelineManager._check_zone_violations). Mirrors camera_db.py's pattern.
"""

import contextlib
import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "app.db"


@contextlib.contextmanager
def get_connection():
    """Closed on exit — see alerts_db.get_connection for why this matters
    (sqlite3's own `with conn:` never closes the connection, which leaked
    a file descriptor per call and eventually exhausted the process's
    open-file limit). This module's own list_zones() is called on every
    detection cycle for every camera (see pipeline.py's
    _check_zone_violations) — by far the highest-frequency caller of any
    get_connection() in this codebase, so this was the leak that actually
    exhausted the process's fd limit in practice, confirmed live via
    `OSError: [Errno 24] Too many open files` and the resulting
    `sqlite3.OperationalError: unable to open database file` breaking
    every DB-backed endpoint until the next restart."""
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
            CREATE TABLE IF NOT EXISTS zones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                camera_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                polygon TEXT NOT NULL,
                allowed_names TEXT NOT NULL DEFAULT '[]',
                enabled INTEGER DEFAULT 1,
                restricted_start TEXT,
                restricted_end TEXT
            )
            """
        )
        _migrate_restricted_window(conn)


def _migrate_restricted_window(conn: sqlite3.Connection) -> None:
    """zones table predates per-zone restricted hours ("only enforce the
    allow-list from this time to this time") - add the columns for DBs
    created earlier. NULL/blank on both means the allow-list applies at
    any time, matching the original always-on behavior."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(zones)")}
    if "restricted_start" not in columns:
        conn.execute("ALTER TABLE zones ADD COLUMN restricted_start TEXT")
    if "restricted_end" not in columns:
        conn.execute("ALTER TABLE zones ADD COLUMN restricted_end TEXT")


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["polygon"] = json.loads(d["polygon"])
    d["allowed_names"] = json.loads(d["allowed_names"])
    d["enabled"] = bool(d["enabled"])
    return d


def list_zones(camera_id: int | None = None) -> list[dict]:
    query = "SELECT * FROM zones"
    params = []
    if camera_id is not None:
        query += " WHERE camera_id = ?"
        params.append(camera_id)
    query += " ORDER BY id"
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def add_zone(
    camera_id: int,
    name: str,
    polygon: list,
    allowed_names: list[str],
    restricted_start: str | None = None,
    restricted_end: str | None = None,
) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO zones (camera_id, name, polygon, allowed_names, enabled, restricted_start, restricted_end)
               VALUES (?, ?, ?, ?, 1, ?, ?)""",
            (camera_id, name, json.dumps(polygon), json.dumps(allowed_names), restricted_start, restricted_end),
        )
        return cur.lastrowid


def update_zone(zone_id: int, **fields) -> None:
    if not fields:
        return
    if "polygon" in fields:
        fields["polygon"] = json.dumps(fields["polygon"])
    if "allowed_names" in fields:
        fields["allowed_names"] = json.dumps(fields["allowed_names"])
    if "enabled" in fields:
        fields["enabled"] = int(fields["enabled"])
    columns = ", ".join(f"{k} = ?" for k in fields)
    with get_connection() as conn:
        conn.execute(f"UPDATE zones SET {columns} WHERE id = ?", (*fields.values(), zone_id))


def delete_zone(zone_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM zones WHERE id = ?", (zone_id,))


def rename_person_in_zones(old_name: str, new_name: str) -> None:
    """Cascades a People-page rename into every zone's allow-list — otherwise
    a renamed person silently falls off any zone they were allowed into."""
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT id, allowed_names FROM zones").fetchall()
        for row in rows:
            names = json.loads(row["allowed_names"])
            if old_name in names:
                names = [new_name if n == old_name else n for n in names]
                conn.execute("UPDATE zones SET allowed_names = ? WHERE id = ?", (json.dumps(names), row["id"]))


def remove_person_from_zones(name: str) -> None:
    """Cascades a People-page delete into every zone's allow-list."""
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT id, allowed_names FROM zones").fetchall()
        for row in rows:
            names = json.loads(row["allowed_names"])
            if name in names:
                names = [n for n in names if n != name]
                conn.execute("UPDATE zones SET allowed_names = ? WHERE id = ?", (json.dumps(names), row["id"]))
