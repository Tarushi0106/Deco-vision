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

import contextlib
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

# Fixed catalog, not a DB table — a closed, rarely-changing set (like
# ALL_STATUSES above). Every key traces to an existing feature already
# built into this app (see Sidebar.jsx's nav items); adding a new one
# later is a one-line addition here, same as adding a status would be.
FEATURE_FACE_RECOGNITION = "face_recognition"
FEATURE_ATTENDANCE = "attendance"
FEATURE_FOOTFALL_ANALYTICS = "footfall_analytics"
FEATURE_WORKFORCE_ANALYTICS = "workforce_analytics"
FEATURE_INTRUSION_DETECTION = "intrusion_detection"
FEATURE_SMOKE_DETECTION = "smoke_detection"
FEATURE_CROWD_ANALYTICS = "crowd_analytics"
FEATURE_THREAT_DETECTION = "threat_detection"
FEATURE_VEHICLE_DETECTION = "vehicle_detection"
ALL_FEATURES = (
    FEATURE_FACE_RECOGNITION, FEATURE_ATTENDANCE, FEATURE_FOOTFALL_ANALYTICS,
    FEATURE_WORKFORCE_ANALYTICS, FEATURE_INTRUSION_DETECTION, FEATURE_SMOKE_DETECTION,
    FEATURE_CROWD_ANALYTICS, FEATURE_THREAT_DETECTION, FEATURE_VEHICLE_DETECTION,
)
FEATURE_LABELS = {
    FEATURE_FACE_RECOGNITION: "Face Recognition",
    FEATURE_ATTENDANCE: "Attendance",
    FEATURE_FOOTFALL_ANALYTICS: "Footfall Analytics",
    FEATURE_WORKFORCE_ANALYTICS: "Workforce Analytics",
    FEATURE_INTRUSION_DETECTION: "Intrusion Detection",
    FEATURE_SMOKE_DETECTION: "Smoke Detection",
    FEATURE_CROWD_ANALYTICS: "Crowd Analytics",
    FEATURE_THREAT_DETECTION: "Threat Detection",
    FEATURE_VEHICLE_DETECTION: "Vehicle Detection",
}

# Camera-permission toggle names, in display order — used both as the
# camera_permissions column list and the shape of a "permissions" dict
# passed around between this module and main.py.
PERMISSION_KEYS = ("live_view", "playback", "analytics", "events", "camera_settings")

# Cameras are sold outright, not leased/subscribed — a license never
# expires or needs renewing on its own; the only way out of "active" is an
# explicit admin action (disable or revoke). expires_at still exists as a
# DB column (dropping it would need a table rebuild SQLite doesn't do via
# ALTER, and nothing reads it for status anymore) — new licenses just get
# it set far enough out that it can never practically matter.
_NEVER_EXPIRES_SECONDS = 100 * 365 * 86400

# Distinct from _NEVER_EXPIRES_SECONDS above (which is what NEW licenses
# get written with): this is the threshold the UI uses to decide whether
# an expires_at value is "real" or just the vestigial default — anything
# further out than 10 years is treated as non-expiring for display
# purposes, so the Expiry stat card can say "No expiry — owned outright"
# instead of a nonsensical ~36,500-day countdown.
NON_EXPIRING_HORIZON_SECONDS = 10 * 365 * 86400

# Ambiguity-free alphabet (no 0/O, 1/I/L) for license keys people may need
# to type by hand off a printout, not just scan.
_KEY_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_KEY_GROUPS = 4
_KEY_GROUP_LEN = 4


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

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS license_features (
                id TEXT PRIMARY KEY,
                license_id TEXT NOT NULL,
                feature_key TEXT NOT NULL,
                enabled_at REAL NOT NULL,
                UNIQUE (license_id, feature_key),
                FOREIGN KEY (license_id) REFERENCES licenses (id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_license_features_license ON license_features (license_id)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS camera_features (
                id TEXT PRIMARY KEY,
                license_id TEXT NOT NULL,
                camera_id INTEGER NOT NULL,
                feature_key TEXT NOT NULL,
                enabled_at REAL NOT NULL,
                UNIQUE (camera_id, feature_key),
                FOREIGN KEY (license_id) REFERENCES licenses (id),
                FOREIGN KEY (camera_id) REFERENCES cameras (id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_camera_features_camera ON camera_features (camera_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_camera_features_license ON camera_features (license_id)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS camera_permissions (
                id TEXT PRIMARY KEY,
                user_uuid TEXT NOT NULL,
                camera_id INTEGER NOT NULL,
                live_view INTEGER NOT NULL DEFAULT 0,
                playback INTEGER NOT NULL DEFAULT 0,
                analytics INTEGER NOT NULL DEFAULT 0,
                events INTEGER NOT NULL DEFAULT 0,
                camera_settings INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL,
                UNIQUE (user_uuid, camera_id),
                FOREIGN KEY (camera_id) REFERENCES cameras (id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_camera_permissions_user ON camera_permissions (user_uuid)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_camera_permissions_camera ON camera_permissions (camera_id)")


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
    """Also cascades to camera_features/camera_permissions — otherwise a
    camera unassigned from a license would leave stale feature/permission
    grants behind, silently outliving the Camera Access row they belonged
    to (see the "orthogonality rule" note on camera_features/
    camera_permissions below: a camera must be assigned before either can
    exist, so removal must clean both up)."""
    with get_connection() as conn:
        placeholders = ",".join("?" * len(camera_ids))
        conn.execute(
            f"DELETE FROM camera_assignments WHERE license_id = ? AND camera_id IN ({placeholders})",
            (license_id, *camera_ids),
        )
        conn.execute(
            f"DELETE FROM camera_features WHERE license_id = ? AND camera_id IN ({placeholders})",
            (license_id, *camera_ids),
        )
        conn.execute(
            f"DELETE FROM camera_permissions WHERE camera_id IN ({placeholders})",
            camera_ids,
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


# --- License-level features ------------------------------------------------

def set_license_features(license_id: str, feature_keys: list[str]) -> list[str]:
    """Full-replace, not assign/remove — the UI saves from a single
    checklist with no capacity cap to diff against, so "this is now the
    enabled set" is simpler than a two-call add/remove pair. Silently
    drops any key not in ALL_FEATURES (defensive; the endpoint validates
    first)."""
    keys = [k for k in feature_keys if k in ALL_FEATURES]
    now = time.time()
    with get_connection() as conn:
        conn.execute("DELETE FROM license_features WHERE license_id = ?", (license_id,))
        for key in keys:
            conn.execute(
                "INSERT INTO license_features (id, license_id, feature_key, enabled_at) VALUES (?, ?, ?, ?)",
                (uuid_lib.uuid4().hex, license_id, key, now),
            )
    return keys


def list_license_features(license_id: str) -> list[str]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT feature_key FROM license_features WHERE license_id = ?", (license_id,)
        ).fetchall()
    return [r[0] for r in rows]


# --- Per-camera feature overrides -------------------------------------------
#
# A camera's enabled features must be a subset of its license's enabled
# features (set_license_features above) — enforced here, not just at the
# API layer, so any future caller of this module gets the same guarantee.
# Orthogonality rule: a camera must already be in camera_assignments for
# this license before it can have a features/permissions row — see
# remove_cameras' cascade-delete, which is the other half of that rule.

def set_camera_features(license_id: str, camera_id: int, feature_keys: list[str]) -> list[str]:
    """Returns the subset of feature_keys NOT enabled on the license (i.e.
    invalid). Empty list = all valid and the update was applied; a
    non-empty list means nothing was written and the caller should reject
    with 400 listing these keys."""
    licensed = set(list_license_features(license_id))
    invalid = [k for k in feature_keys if k not in licensed]
    if invalid:
        return invalid
    now = time.time()
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM camera_features WHERE license_id = ? AND camera_id = ?", (license_id, camera_id)
        )
        for key in feature_keys:
            conn.execute(
                "INSERT INTO camera_features (id, license_id, camera_id, feature_key, enabled_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (uuid_lib.uuid4().hex, license_id, camera_id, key, now),
            )
    return []


def get_camera_features(camera_id: int) -> list[str]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT feature_key FROM camera_features WHERE camera_id = ?", (camera_id,)
        ).fetchall()
    return [r[0] for r in rows]


def list_camera_features_bulk(camera_ids: list[int]) -> dict[int, list[str]]:
    """Avoids N+1 queries when rendering the Camera Access table."""
    if not camera_ids:
        return {}
    out: dict[int, list[str]] = {cid: [] for cid in camera_ids}
    placeholders = ",".join("?" * len(camera_ids))
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT camera_id, feature_key FROM camera_features WHERE camera_id IN ({placeholders})",
            camera_ids,
        ).fetchall()
    for camera_id, feature_key in rows:
        out[camera_id].append(feature_key)
    return out


# --- Per-user camera permissions --------------------------------------------

def default_permissions_for_role(role: str) -> dict:
    """The effective permissions for a user with no explicit override row
    on a given camera — pre-fills the grid UI and is what a viewer/
    operator's own self-service view (ClientLicenseView) shows until an
    admin saves a real override."""
    from . import user_db  # deferred: avoids a hard import-order dependency for a single constant lookup

    if role in (user_db.ROLE_SUPER_ADMIN, user_db.ROLE_COMPANY_ADMIN):
        return {k: True for k in PERMISSION_KEYS}
    if role == user_db.ROLE_OPERATOR:
        return {k: (k != "camera_settings") for k in PERMISSION_KEYS}
    return {k: (k == "live_view") for k in PERMISSION_KEYS}  # viewer, and any unrecognized role


def _row_to_permissions(row) -> dict:
    return {k: bool(row[k]) for k in PERMISSION_KEYS}


def get_camera_permission_row(user_uuid: str, camera_id: int) -> dict | None:
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM camera_permissions WHERE user_uuid = ? AND camera_id = ?", (user_uuid, camera_id)
        ).fetchone()
    return dict(row) if row else None


def effective_permissions(user_uuid: str, camera_id: int, role: str) -> dict:
    row = get_camera_permission_row(user_uuid, camera_id)
    return _row_to_permissions(row) if row else default_permissions_for_role(role)


def list_camera_permissions(camera_id: int, users: list[dict]) -> list[dict]:
    """users = the company's license-users (user_db.list_license_users).
    One row per user: {user_uuid, name, email, role, permissions, is_override}
    — is_override False means these are the role default, not a saved row."""
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = {
            r["user_uuid"]: r for r in conn.execute(
                "SELECT * FROM camera_permissions WHERE camera_id = ?", (camera_id,)
            ).fetchall()
        }
    out = []
    for u in users:
        row = rows.get(u["uuid"])
        perms = _row_to_permissions(row) if row else default_permissions_for_role(u["role"])
        out.append({
            "user_uuid": u["uuid"], "name": u["name"], "email": u["email"], "role": u["role"],
            "permissions": perms, "is_override": row is not None,
        })
    return out


def set_camera_permission(user_uuid: str, camera_id: int, perms: dict) -> dict:
    now = time.time()
    values = [1 if perms.get(k) else 0 for k in PERMISSION_KEYS]
    with get_connection() as conn:
        conn.execute(
            f"""
            INSERT INTO camera_permissions
                (id, user_uuid, camera_id, {", ".join(PERMISSION_KEYS)}, updated_at)
            VALUES (?, ?, ?, {", ".join("?" * len(PERMISSION_KEYS))}, ?)
            ON CONFLICT (user_uuid, camera_id) DO UPDATE SET
                {", ".join(f"{k} = excluded.{k}" for k in PERMISSION_KEYS)},
                updated_at = excluded.updated_at
            """,
            (uuid_lib.uuid4().hex, user_uuid, camera_id, *values, now),
        )
    return get_camera_permission_row(user_uuid, camera_id)


def list_permission_counts_bulk(camera_ids: list[int]) -> dict[int, int]:
    """Count of users with an explicit override row where ANY flag is
    true, per camera — "assigned users" means an admin explicitly granted
    something on THIS camera, not "everyone in the company by default"."""
    if not camera_ids:
        return {}
    out = {cid: 0 for cid in camera_ids}
    placeholders = ",".join("?" * len(camera_ids))
    any_true = " OR ".join(f"{k} = 1" for k in PERMISSION_KEYS)
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT camera_id, COUNT(*) FROM camera_permissions "
            f"WHERE camera_id IN ({placeholders}) AND ({any_true}) GROUP BY camera_id",
            camera_ids,
        ).fetchall()
    for camera_id, count in rows:
        out[camera_id] = count
    return out


def list_cameras_for_license_detailed(license_id: str) -> list[dict]:
    """Like list_cameras_for_license, but with enabled_feature_count and
    assigned_user_count added — kept as a SEPARATE function (not a change
    to the original) because CameraAssignModal/ClientLicenseView already
    depend on that function's existing shape."""
    cameras = list_cameras_for_license(license_id)
    camera_ids = [c["id"] for c in cameras]
    features_by_camera = list_camera_features_bulk(camera_ids)
    counts_by_camera = list_permission_counts_bulk(camera_ids)
    for c in cameras:
        c["enabled_features"] = features_by_camera.get(c["id"], [])
        c["assigned_user_count"] = counts_by_camera.get(c["id"], 0)
    return cameras


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
