"""Camera registry — SQLite-backed so the Camera Management page can do
real CRUD, mirroring the pattern in face_db.py. Seeded once from the
connection details in config.py: the three real cameras this deployment
has credentials for ("exit", "Main gate camera", "Technical section" —
the latter two are separate RTSP channels off the same NVR unit).
"""

import contextlib
import sqlite3
from pathlib import Path

from . import config

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
            CREATE TABLE IF NOT EXISTS cameras (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                site TEXT NOT NULL,
                cam_code TEXT,
                purpose TEXT DEFAULT 'GENERAL',
                host TEXT DEFAULT '',
                port INTEGER DEFAULT 554,
                user TEXT DEFAULT '',
                password TEXT DEFAULT '',
                stream_path TEXT DEFAULT '',
                status TEXT DEFAULT 'inactive',
                live_feed_enabled INTEGER DEFAULT 1,
                admin_port INTEGER DEFAULT 443,
                ai_enabled INTEGER DEFAULT 1
            )
            """
        )
        _migrate_admin_port(conn)
        _migrate_ai_enabled(conn)
        _seed_if_empty(conn)

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT DEFAULT ''
            )
            """
        )
        _seed_sites_if_empty(conn)


def _migrate_admin_port(conn: sqlite3.Connection) -> None:
    """cameras table predates the admin_port column (the RTSP port and the
    device's HTTPS admin port can differ, as they do on 103.204.0.122:
    RTSP on 101/103, admin UI on 8443) — add it for DBs created earlier."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(cameras)")}
    if "admin_port" not in columns:
        conn.execute("ALTER TABLE cameras ADD COLUMN admin_port INTEGER DEFAULT 443")


def _migrate_ai_enabled(conn: sqlite3.Connection) -> None:
    """cameras table predates the display-only mode (see pipeline.py's
    PipelineManager._start_camera) — default 1 (on) for every camera that
    existed before this column, matching their unchanged prior behavior
    (every camera always ran face recognition/zone/fire/smoke). Only a
    camera explicitly added or edited with ai_enabled=0 skips the
    detection worker entirely."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(cameras)")}
    if "ai_enabled" not in columns:
        conn.execute("ALTER TABLE cameras ADD COLUMN ai_enabled INTEGER DEFAULT 1")


def _seed_if_empty(conn: sqlite3.Connection) -> None:
    count = conn.execute("SELECT COUNT(*) FROM cameras").fetchone()[0]
    if count > 0:
        return

    if config.CAMERA_HOST:
        conn.execute(
            """INSERT INTO cameras
               (name, site, cam_code, purpose, host, port, user, password, stream_path, status, live_feed_enabled, admin_port)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 1, ?)""",
            (
                "exit",
                "Noida Site",
                "I-HIPB5PI-MV",
                "ATTENDANCE",
                config.CAMERA_HOST,
                config.CAMERA_RTSP_PORT,
                config.CAMERA_USER,
                config.CAMERA_PASSWORD,
                config.CAMERA_STREAM_PATH,
                config.CAMERA_ADMIN_PORT,
            ),
        )

    if config.CAMERA2_HOST:
        conn.execute(
            """INSERT INTO cameras
               (name, site, cam_code, purpose, host, port, user, password, stream_path, status, live_feed_enabled, admin_port)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 1, ?)""",
            (
                "Main gate camera",
                "Noida Site",
                "CP-UNC-DA21L32-NOIDA(2MP)",
                "GENERAL",
                config.CAMERA2_HOST,
                config.CAMERA2_RTSP_PORT,
                config.CAMERA_USER,
                config.CAMERA_PASSWORD,
                config.CAMERA_STREAM_PATH,
                config.CAMERA2_ADMIN_PORT,
            ),
        )

    if config.CAMERA3_HOST:
        conn.execute(
            """INSERT INTO cameras
               (name, site, cam_code, purpose, host, port, user, password, stream_path, status, live_feed_enabled, admin_port)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 1, ?)""",
            (
                "Technical section",
                "Noida Site",
                "CP-UNC-DA21L32-NOIDA(2MP)",
                "GENERAL",
                config.CAMERA3_HOST,
                config.CAMERA3_RTSP_PORT,
                config.CAMERA_USER,
                config.CAMERA_PASSWORD,
                config.CAMERA_STREAM_PATH,
                config.CAMERA3_ADMIN_PORT,
            ),
        )


def _seed_sites_if_empty(conn: sqlite3.Connection) -> None:
    count = conn.execute("SELECT COUNT(*) FROM sites").fetchone()[0]
    if count > 0:
        return
    for name in ("Noida Site", "Mumbai Site"):
        conn.execute("INSERT OR IGNORE INTO sites (name, description) VALUES (?, '')", (name,))


def list_sites() -> list[dict]:
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM sites ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def add_site(name: str, description: str = "") -> int:
    with get_connection() as conn:
        cur = conn.execute("INSERT INTO sites (name, description) VALUES (?, ?)", (name, description))
        return cur.lastrowid


def update_site(site_id: int, name: str | None = None, description: str | None = None) -> None:
    with get_connection() as conn:
        if name is not None:
            old_name = conn.execute("SELECT name FROM sites WHERE id = ?", (site_id,)).fetchone()
            if old_name and old_name[0] != name:
                # site is a free-text label on cameras, not a foreign key —
                # cascade the rename so cameras don't silently orphan from it
                conn.execute("UPDATE cameras SET site = ? WHERE site = ?", (name, old_name[0]))
            conn.execute("UPDATE sites SET name = ? WHERE id = ?", (name, site_id))
        if description is not None:
            conn.execute("UPDATE sites SET description = ? WHERE id = ?", (description, site_id))


def delete_site(site_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM sites WHERE id = ?", (site_id,))


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["is_configured"] = bool(d["host"])
    d.pop("password", None)  # never sent to the frontend
    return d


def list_cameras() -> list[dict]:
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM cameras ORDER BY id").fetchall()
    return [_row_to_dict(r) for r in rows]


def list_active_devices() -> list[dict]:
    """Distinct physical devices (by host) behind the active, configured
    cameras. Several camera rows can share one physical device (e.g. two
    RTSP channels off the same NVR/unit) — admin-API operations like
    Allow List sync apply once per device, not once per channel."""
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT DISTINCT host, user, password, admin_port FROM cameras WHERE status = 'active' AND host != ''"
        ).fetchall()
    return [dict(r) for r in rows]


def get_camera_connection(camera_id: int) -> dict | None:
    """Includes the password — internal use only (pipeline connecting)."""
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM cameras WHERE id = ?", (camera_id,)).fetchone()
    return dict(row) if row else None


def add_camera(name: str, site: str, **fields) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO cameras
               (name, site, cam_code, purpose, host, port, user, password, stream_path, status, live_feed_enabled, admin_port, ai_enabled)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                name,
                site,
                fields.get("cam_code", ""),
                fields.get("purpose", "GENERAL"),
                fields.get("host", ""),
                fields.get("port", 554),
                fields.get("user", ""),
                fields.get("password", ""),
                fields.get("stream_path", "/h264/ch1/sub/av_stream"),
                "active" if fields.get("host") else "inactive",
                int(fields.get("live_feed_enabled", True)),
                fields.get("admin_port", config.CAMERA_ADMIN_PORT),
                int(fields.get("ai_enabled", True)),
            ),
        )
        return cur.lastrowid


def update_camera(camera_id: int, **fields) -> None:
    if not fields:
        return
    if "host" in fields:
        fields["status"] = "active" if fields["host"] else "inactive"
    columns = ", ".join(f"{k} = ?" for k in fields)
    with get_connection() as conn:
        conn.execute(f"UPDATE cameras SET {columns} WHERE id = ?", (*fields.values(), camera_id))


def delete_camera(camera_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM cameras WHERE id = ?", (camera_id,))
