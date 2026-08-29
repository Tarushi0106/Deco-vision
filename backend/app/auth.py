"""JWT authentication + role-based access control for the License & Camera
Access Management module (see license_db.py for the domain it protects,
user_db.py for the accounts it authenticates against).

Scope note: this is the first REAL authentication in this app — the
pre-existing /api/auth/login (user_db.record_login) never checked a
password at all, it just tracked who's using the main dashboard. That
endpoint and its behavior are untouched. Only the License module's new
endpoints require a valid bearer token; retrofitting auth onto the rest of
the existing API (cameras, people, attendance, ...) is a separate, much
larger change this doesn't attempt.
"""

from __future__ import annotations

import time
import uuid as uuid_lib

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import config, license_db, user_db
from .user_db import ALL_ROLES, ROLE_COMPANY_ADMIN, ROLE_OPERATOR, ROLE_SUPER_ADMIN, ROLE_VIEWER

__all__ = [
    "ALL_ROLES", "ROLE_COMPANY_ADMIN", "ROLE_OPERATOR", "ROLE_SUPER_ADMIN", "ROLE_VIEWER",
    "hash_password", "verify_password", "create_access_token", "decode_token",
    "get_current_user", "require_role",
]

security = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(user: dict) -> tuple[str, str]:
    """Returns (token, jti) — the jti is stored in license_db's sessions
    table so a token can be individually revoked (real logout / an admin
    force-signing someone out), something a stateless JWT can't do on its
    own."""
    now = int(time.time())
    jti = uuid_lib.uuid4().hex
    payload = {
        "sub": user["uuid"],
        "email": user["email"],
        "role": user["role"],
        "company_id": user.get("company_id"),
        "jti": jti,
        "iat": now,
        "exp": now + config.JWT_ACCESS_TOKEN_MINUTES * 60,
    }
    token = jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)
    return token, jti


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, config.JWT_SECRET, algorithms=[config.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired, please sign in again")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid authentication token")


def get_current_user(credentials: HTTPAuthorizationCredentials | None = Depends(security)) -> dict:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
    payload = decode_token(credentials.credentials)
    if license_db.is_session_revoked(payload["jti"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session has been signed out")
    user = user_db.get_user_by_uuid(payload["sub"])
    if user is None or not user.get("is_active", 1):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account no longer active")
    user["jti"] = payload["jti"]
    return user


def require_role(*allowed_roles: str):
    """Dependency factory: Depends(require_role(auth.ROLE_SUPER_ADMIN)) etc.
    Every License-module write endpoint should be gated by one of these —
    read endpoints generally allow all four roles (scoped by company for
    non-super_admins at the query level, not here)."""
    def _dependency(user: dict = Depends(get_current_user)) -> dict:
        if user["role"] not in allowed_roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You don't have permission to do this")
        return user
    return _dependency
