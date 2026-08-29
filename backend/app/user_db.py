"""Tracks who has logged into the dashboard itself (distinct from
face_db's enrolled people, which are recognition targets for the
cameras, not dashboard users).

Also holds the real accounts (password + role + company) for the License &
Camera Access Management module's JWT auth (see auth.py) — added as extra
columns on this same table rather than a parallel one, since a License-
module user IS a dashboard user, just one with a role assigned. The
pre-existing record_login()/login_count tracking (used by the main
dashboard's Login.jsx — no password, any email works) is untouched: a row
can have both a login_count from that flow and a password_hash/role from
this one, independently."""

import re
import secrets
import sqlite3
import time
import uuid as uuid_lib
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "app.db"

ROLE_SUPER_ADMIN = "super_admin"
ROLE_COMPANY_ADMIN = "company_admin"
ROLE_OPERATOR = "operator"
ROLE_VIEWER = "viewer"
ALL_ROLES = (ROLE_SUPER_ADMIN, ROLE_COMPANY_ADMIN, ROLE_OPERATOR, ROLE_VIEWER)


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DB_PATH)


def init_db() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                login_count INTEGER DEFAULT 0,
                first_login REAL,
                last_login REAL
            )
            """
        )
        _migrate_license_module_columns(conn)
        _bootstrap_super_admin(conn)


def _migrate_license_module_columns(conn: sqlite3.Connection) -> None:
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    if "uuid" not in existing_cols:
        conn.execute("ALTER TABLE users ADD COLUMN uuid TEXT")
        # Backfill so any pre-existing (login-tracker-only) rows still get a
        # stable identity if they're later promoted to a real account.
        for row in conn.execute("SELECT id FROM users WHERE uuid IS NULL").fetchall():
            conn.execute("UPDATE users SET uuid = ? WHERE id = ?", (uuid_lib.uuid4().hex, row[0]))
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_uuid ON users (uuid)")
    if "password_hash" not in existing_cols:
        conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT DEFAULT ''")
    if "role" not in existing_cols:
        conn.execute(f"ALTER TABLE users ADD COLUMN role TEXT DEFAULT '{ROLE_VIEWER}'")
    if "company_id" not in existing_cols:
        conn.execute("ALTER TABLE users ADD COLUMN company_id TEXT")
    if "is_active" not in existing_cols:
        conn.execute("ALTER TABLE users ADD COLUMN is_active INTEGER DEFAULT 1")


def _bootstrap_super_admin(conn: sqlite3.Connection) -> None:
    """Ensures a super_admin account always exists — otherwise a fresh
    database has no way to sign into the License module at all (every
    other role has to be created BY a super_admin). Idempotent: does
    nothing once any super_admin exists, even if its email/password have
    since changed."""
    from . import config  # deferred: config imports nothing from here, but avoids any import-order surprises

    exists = conn.execute("SELECT 1 FROM users WHERE role = ?", (ROLE_SUPER_ADMIN,)).fetchone()
    if exists:
        return
    email = _normalize_email(config.SUPER_ADMIN_EMAIL)
    row = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    password_hash = _hash_password(config.SUPER_ADMIN_PASSWORD)
    if row:
        conn.execute(
            "UPDATE users SET role = ?, password_hash = ?, is_active = 1 WHERE id = ?",
            (ROLE_SUPER_ADMIN, password_hash, row[0]),
        )
    else:
        conn.execute(
            "INSERT INTO users (email, name, uuid, password_hash, role, is_active) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            (email, "Super Admin", uuid_lib.uuid4().hex, password_hash, ROLE_SUPER_ADMIN),
        )


def _hash_password(password: str) -> str:
    import bcrypt
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _normalize_email(email: str) -> str:
    """License-module accounts are looked up by exact string match — without
    this, "Admin@..." (e.g. a mobile keyboard auto-capitalizing the first
    letter of an email field) silently fails to find a row that's actually
    there, and comes back as the same generic "invalid email or password"
    as a genuinely wrong password. Only applied to the License-module path
    (this function, create_license_user, get_user_by_email) — record_login
    below is the separate, pre-existing no-password dashboard tracker and
    is intentionally left untouched."""
    return email.strip().lower()


def _name_from_email(email: str) -> str:
    local_part = email.split("@")[0]
    words = re.split(r"[._-]+", local_part)
    return " ".join(w.capitalize() for w in words if w)


def record_login(email: str) -> dict:
    now = time.time()
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if row:
            conn.execute(
                "UPDATE users SET login_count = login_count + 1, last_login = ? WHERE email = ?",
                (now, email),
            )
        else:
            conn.execute(
                "INSERT INTO users (email, name, login_count, first_login, last_login) VALUES (?, ?, 1, ?, ?)",
                (email, _name_from_email(email), now, now),
            )
        updated = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    return dict(updated)


def list_users() -> list[dict]:
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM users ORDER BY last_login DESC").fetchall()
    return [dict(r) for r in rows]


# --- License module accounts (password + role + company) ---------------

def create_license_user(
    email: str, name: str, password_hash: str, role: str, company_id: str | None = None,
) -> dict:
    """Creates (or promotes) a real License-module account. If this email
    already exists as a login-tracker-only row (no password), it's
    upgraded in place rather than erroring — the two identities are the
    same person."""
    email = _normalize_email(email)
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE users SET name = ?, password_hash = ?, role = ?, company_id = ?, is_active = 1 "
                "WHERE id = ?",
                (name, password_hash, role, company_id, existing["id"]),
            )
            user_id = existing["id"]
        else:
            cur = conn.execute(
                "INSERT INTO users (email, name, uuid, password_hash, role, company_id, is_active) "
                "VALUES (?, ?, ?, ?, ?, ?, 1)",
                (email, name, uuid_lib.uuid4().hex, password_hash, role, company_id),
            )
            user_id = cur.lastrowid
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row)


def get_user_by_email(email: str) -> dict | None:
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM users WHERE email = ?", (_normalize_email(email),)).fetchone()
    return dict(row) if row else None


def get_user_by_uuid(user_uuid: str) -> dict | None:
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM users WHERE uuid = ?", (user_uuid,)).fetchone()
    return dict(row) if row else None


def list_license_users(company_id: str | None = None) -> list[dict]:
    """Accounts that have an actual role (not just a login-tracker row) —
    what the License module's user-management screens show."""
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        if company_id:
            rows = conn.execute(
                "SELECT * FROM users WHERE password_hash != '' AND company_id = ? ORDER BY name",
                (company_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM users WHERE password_hash != '' ORDER BY name"
            ).fetchall()
    return [dict(r) for r in rows]


def set_user_active(user_uuid: str, is_active: bool) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE users SET is_active = ? WHERE uuid = ?", (1 if is_active else 0, user_uuid))


def set_user_role(user_uuid: str, role: str) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE users SET role = ? WHERE uuid = ?", (role, user_uuid))


def set_user_password(user_uuid: str, password_hash: str) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE users SET password_hash = ? WHERE uuid = ?", (password_hash, user_uuid))


def generate_temp_password() -> str:
    """A random, URL-safe temporary password for admin-created accounts —
    shown once at creation time, the user changes it after first login."""
    return secrets.token_urlsafe(9)
