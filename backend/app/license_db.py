"""License & Camera Access Management — companies, licenses, per-license
camera assignments, JWT session revocation, audit logs, and device
bindings. Reuses the existing cameras table (camera_db.py) rather than
duplicating a camera registry — a "CameraAssignment" here just links a
license to a row already in that table.

UUID primary keys throughout (stored as TEXT, generated with uuid4) per
this module's security requirements — a deliberate departure from the
INTEGER AUTOINCREMENT ids used elsewhere in this app: a license key/id
being guessable-in-sequence is a real exposure this module specifically
needs to avoid, unlike e.g. a camera row id.
"""

from __future__ import annotations

import secrets
import sqlite3
import time
import uuid as uuid_lib
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "app.db"

STATUS_ACTIVE = "active"
STATUS_INACTIVE = "inactive"
STATUS_SUSPENDED = "suspended"
ALL_STATUSES = (STATUS_ACTIVE, STATUS_INACTIVE, STATUS_SUSPENDED)

# Cameras are sold outright, not leased/subscribed — a license never
# expires or needs renewing on its own; the only way out of "active" is an
# explicit admin action (disable or revoke). expires_at still exists as a
# DB column (dropping it would need a table rebuild SQLite doesn't do via
# ALTER, and nothing reads it for status anymore) — new licenses just get
# it set far enough out that it can never practically matter.
_NEVER_EXPIRES_SECONDS = 100 * 365 * 86400

# Ambiguity-free alphabet (no 0/O, 1/I/L) for license keys people may need
# to type by hand off a printout, not just scan.
_KEY_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_KEY_GROUPS = 4
_KEY_GROUP_LEN = 4


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DB_PATH)


def init_db() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS companies (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS licenses (
                id TEXT PRIMARY KEY,
                license_key TEXT NOT NULL UNIQUE,
                company_id TEXT NOT NULL,
                label TEXT DEFAULT '',
                max_cameras INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'active',
                device_bind_enabled INTEGER NOT NULL DEFAULT 0,
                expires_at REAL NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY (company_id) REFERENCES companies (id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_licenses_company ON licenses (company_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_licenses_status ON licenses (status)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS camera_assignments (
                id TEXT PRIMARY KEY,
                license_id TEXT NOT NULL,
                camera_id INTEGER NOT NULL,
                assigned_at REAL NOT NULL,
                UNIQUE (license_id, camera_id),
                FOREIGN KEY (license_id) REFERENCES licenses (id),
                FOREIGN KEY (camera_id) REFERENCES cameras (id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_camassign_license ON camera_assignments (license_id)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                jti TEXT NOT NULL UNIQUE,
                device_info TEXT DEFAULT '',
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                revoked INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_jti ON sessions (jti)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_logs (
                id TEXT PRIMARY KEY,
                actor_user_id TEXT,
                actor_email TEXT DEFAULT '',
                action TEXT NOT NULL,
                target_type TEXT DEFAULT '',
                target_id TEXT DEFAULT '',
                details TEXT DEFAULT '',
                ts REAL NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_logs (ts)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS device_bindings (
                id TEXT PRIMARY KEY,
                license_id TEXT NOT NULL UNIQUE,
                device_fingerprint TEXT NOT NULL,
                bound_at REAL NOT NULL,
                FOREIGN KEY (license_id) REFERENCES licenses (id)
            )
            """
        )


# --- Companies ------------------------------------------------------------

def create_company(name: str) -> dict:
    company_id = uuid_lib.uuid4().hex
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO companies (id, name, created_at) VALUES (?, ?, ?)",
            (company_id, name, time.time()),
        )
    return get_company(company_id)


def get_company(company_id: str) -> dict | None:
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()
    return dict(row) if row else None


def list_companies() -> list[dict]:
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM companies ORDER BY name").fetchall()
    return [dict(r) for r in rows]


# --- Licenses ---------------------------------------------------------------

def generate_license_key() -> str:
    groups = [
        "".join(secrets.choice(_KEY_ALPHABET) for _ in range(_KEY_GROUP_LEN))
        for _ in range(_KEY_GROUPS)
    ]
    return "-".join(groups)


def create_license(
    company_id: str, max_cameras: int, device_bind_enabled: bool, label: str = "",
) -> dict:
    license_id = uuid_lib.uuid4().hex
    now = time.time()
    key = generate_license_key()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO licenses "
            "(id, license_key, company_id, label, max_cameras, status, device_bind_enabled, "
            " expires_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (license_id, key, company_id, label, max_cameras, STATUS_ACTIVE,
             1 if device_bind_enabled else 0, now + _NEVER_EXPIRES_SECONDS, now, now),
        )
    return get_license(license_id)


def get_license(license_id: str) -> dict | None:
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM licenses WHERE id = ?", (license_id,)).fetchone()
    return dict(row) if row else None


def get_license_by_key(license_key: str) -> dict | None:
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM licenses WHERE license_key = ?", (license_key,)).fetchone()
    return dict(row) if row else None


def list_licenses(
    company_id: str | None = None, status: str | None = None, search: str = "",
    limit: int = 50, offset: int = 0,
) -> tuple[list[dict], int]:
    """Returns (rows, total_count) for pagination."""
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        clauses, params = [], []
        if company_id:
            clauses.append("company_id = ?")
            params.append(company_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if search:
            clauses.append("(license_key LIKE ? OR label LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%"])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        total = conn.execute(f"SELECT COUNT(*) FROM licenses {where}", params).fetchone()[0]
        rows = [
            dict(r) for r in conn.execute(
                f"SELECT * FROM licenses {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
        ]
    return rows, total


def update_license(license_id: str, **fields) -> dict | None:
    allowed = {"label", "max_cameras", "device_bind_enabled"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return get_license(license_id)
    updates["updated_at"] = time.time()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    with get_connection() as conn:
        conn.execute(f"UPDATE licenses SET {set_clause} WHERE id = ?", (*updates.values(), license_id))
    return get_license(license_id)


def set_license_status(license_id: str, status: str) -> dict | None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE licenses SET status = ?, updated_at = ? WHERE id = ?",
            (status, time.time(), license_id),
        )
    return get_license(license_id)


# --- Camera assignments ------------------------------------------------

def assign_cameras(license_id: str, camera_ids: list[int]) -> int:
    """Bulk-assigns cameras to a license, skipping ones already assigned.
    Returns how many were newly added (caller enforces the max_cameras cap
    before calling this)."""
    now = time.time()
    added = 0
    with get_connection() as conn:
        for camera_id in camera_ids:
            try:
                conn.execute(
                    "INSERT INTO camera_assignments (id, license_id, camera_id, assigned_at) VALUES (?, ?, ?, ?)",
                    (uuid_lib.uuid4().hex, license_id, camera_id, now),
                )
                added += 1
            except sqlite3.IntegrityError:
                continue  # already assigned — not an error, just a no-op
    return added


def remove_cameras(license_id: str, camera_ids: list[int]) -> None:
    with get_connection() as conn:
        placeholders = ",".join("?" * len(camera_ids))
        conn.execute(
            f"DELETE FROM camera_assignments WHERE license_id = ? AND camera_id IN ({placeholders})",
            (license_id, *camera_ids),
        )


def list_cameras_for_license(license_id: str) -> list[dict]:
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT c.id, c.name, c.site, c.status AS camera_status, ca.assigned_at
            FROM camera_assignments ca
            JOIN cameras c ON c.id = ca.camera_id
            WHERE ca.license_id = ?
            ORDER BY ca.assigned_at
            """,
            (license_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def count_cameras_for_license(license_id: str) -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM camera_assignments WHERE license_id = ?", (license_id,)
        ).fetchone()
    return row[0]


def is_camera_assigned(license_id: str, camera_id: int) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM camera_assignments WHERE license_id = ? AND camera_id = ?",
            (license_id, camera_id),
        ).fetchone()
    return row is not None


def count_assigned_cameras() -> int:
    """How many distinct cameras have been handed out via ANY license —
    powers the main Dashboard's "Cameras Assigned" tile (see main.py's
    /api/stats), which is deliberately public/unauthenticated like the
    rest of that endpoint: just a count, no license keys or company
    names."""
    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(DISTINCT camera_id) FROM camera_assignments").fetchone()
    return row[0]


def count_live_assigned_cameras(online_camera_ids: set[int]) -> int:
    """Of the cameras that have been assigned to a license, how many are
    actually live right now — "accessed", not just "given out"."""
    with get_connection() as conn:
        assigned_ids = {r[0] for r in conn.execute("SELECT DISTINCT camera_id FROM camera_assignments").fetchall()}
    return len(assigned_ids & online_camera_ids)


def get_license_for_camera(camera_id: int) -> dict | None:
    """Which license (if any) a camera is assigned to — the enforcement
    point for "prevent users from accessing unassigned cameras"."""
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT license_id FROM camera_assignments WHERE camera_id = ? LIMIT 1", (camera_id,)
        ).fetchone()
    return get_license(row["license_id"]) if row else None


# --- Device bindings ------------------------------------------------------

def get_binding(license_id: str) -> dict | None:
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM device_bindings WHERE license_id = ?", (license_id,)).fetchone()
    return dict(row) if row else None


def bind_device(license_id: str, device_fingerprint: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO device_bindings (id, license_id, device_fingerprint, bound_at) VALUES (?, ?, ?, ?)",
            (uuid_lib.uuid4().hex, license_id, device_fingerprint, time.time()),
        )


def clear_binding(license_id: str) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM device_bindings WHERE license_id = ?", (license_id,))


# --- Sessions (JWT revocation) --------------------------------------------

def create_session(user_id: str, jti: str, expires_at: float, device_info: str = "") -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO sessions (id, user_id, jti, device_info, created_at, expires_at, revoked) "
            "VALUES (?, ?, ?, ?, ?, ?, 0)",
            (uuid_lib.uuid4().hex, user_id, jti, device_info, time.time(), expires_at),
        )


def revoke_session(jti: str) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE sessions SET revoked = 1 WHERE jti = ?", (jti,))


def is_session_revoked(jti: str) -> bool:
    with get_connection() as conn:
        row = conn.execute("SELECT revoked FROM sessions WHERE jti = ?", (jti,)).fetchone()
    return bool(row and row[0])


# --- Audit logs -------------------------------------------------------------

def log_audit(actor_user_id: str | None, actor_email: str, action: str,
              target_type: str = "", target_id: str = "", details: str = "") -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO audit_logs (id, actor_user_id, actor_email, action, target_type, target_id, details, ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (uuid_lib.uuid4().hex, actor_user_id, actor_email, action, target_type, target_id, details, time.time()),
        )


def list_audit_logs(limit: int = 100, offset: int = 0) -> tuple[list[dict], int]:
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        total = conn.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM audit_logs ORDER BY ts DESC LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()
    return [dict(r) for r in rows], total


# --- Analytics --------------------------------------------------------------

def get_analytics(online_camera_count: int | None = None) -> dict:
    """online_camera_count comes from pipeline_manager.is_live() (actual
    live RTSP connectivity) via main.py — this module deliberately doesn't
    import pipeline.py (no *_db.py module does; pipeline.py imports them,
    never the other way, to avoid a circular import), so it falls back to
    counting configured cameras (status='active' in the cameras table,
    which really just means "has connection details", not "currently
    connected") when the caller doesn't supply the real figure."""
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        all_licenses = [dict(r) for r in conn.execute("SELECT * FROM licenses").fetchall()]
        total_cameras = conn.execute("SELECT COUNT(*) FROM cameras").fetchone()[0]
        cameras_assigned = conn.execute("SELECT COUNT(DISTINCT camera_id) FROM camera_assignments").fetchone()[0]
        if online_camera_count is None:
            cameras_online = conn.execute("SELECT COUNT(*) FROM cameras WHERE status = 'active'").fetchone()[0]
        else:
            cameras_online = online_camera_count

    by_status = {s: 0 for s in ALL_STATUSES}
    for lic in all_licenses:
        by_status[lic["status"]] = by_status.get(lic["status"], 0) + 1

    total_license_camera_capacity = sum(lic["max_cameras"] for lic in all_licenses if lic["status"] == STATUS_ACTIVE)

    return {
        "total_licenses": len(all_licenses),
        "active_licenses": by_status[STATUS_ACTIVE],
        "inactive_licenses": by_status[STATUS_INACTIVE],
        "suspended_licenses": by_status[STATUS_SUSPENDED],
        "total_cameras": total_cameras,
        "cameras_assigned": cameras_assigned,
        "cameras_online": cameras_online,
        "cameras_offline": max(0, total_cameras - cameras_online),
        "license_usage_percent": (
            round(100 * cameras_assigned / total_license_camera_capacity, 1)
            if total_license_camera_capacity else 0.0
        ),
        "camera_usage_percent": (
            round(100 * cameras_assigned / total_cameras, 1) if total_cameras else 0.0
        ),
    }
