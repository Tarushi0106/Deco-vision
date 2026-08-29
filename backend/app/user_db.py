"""Tracks who has logged into the dashboard itself (distinct from
face_db's enrolled people, which are recognition targets for the
cameras, not dashboard users)."""

import re
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "app.db"


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
