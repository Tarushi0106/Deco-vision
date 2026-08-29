"""QR code generation for licenses — encodes a signed (not just bare-text)
payload so a photographed/leaked QR image can't be trivially edited to
claim a different license key; activation still re-validates everything
server-side regardless (status, expiry, camera caps), so this is a tamper-
evidence measure, not the actual access control.
"""

from __future__ import annotations

import io
import time

import jwt
import qrcode

from . import config

QR_TOKEN_TYPE = "license_qr"
QR_TOKEN_TTL_SECONDS = 365 * 86400  # a printed/shared QR should keep working for a long time, not expire like a login session


def build_qr_token(license_key: str) -> str:
    payload = {
        "type": QR_TOKEN_TYPE,
        "license_key": license_key,
        "iat": int(time.time()),
        "exp": int(time.time()) + QR_TOKEN_TTL_SECONDS,
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)


def decode_qr_token(token: str) -> str:
    """Returns the license_key embedded in a QR token, or raises
    jwt.InvalidTokenError / jwt.ExpiredSignatureError if it's not a
    genuine, unexpired token this server signed."""
    payload = jwt.decode(token, config.JWT_SECRET, algorithms=[config.JWT_ALGORITHM])
    if payload.get("type") != QR_TOKEN_TYPE:
        raise jwt.InvalidTokenError("Not a license QR token")
    return payload["license_key"]


def render_qr_png(data: str) -> bytes:
    img = qrcode.make(data, box_size=10, border=2)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()
