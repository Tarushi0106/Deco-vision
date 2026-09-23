"""The ONLY point of contact with the existing Parachute application —
plain HTTP calls against its already-public API, the exact same endpoints
a browser tab already uses. No Parachute Python module is ever imported
here. See config.py's module docstring for the full list of endpoints
this talks to.
"""

import logging

import requests

from . import config

logger = logging.getLogger("detection.parachute_client")


def list_cameras() -> list[dict]:
    """Every camera Parachute knows about, each with at least id/name/live
    (live = currently streaming — see Parachute's camera_db.py). Best-effort:
    an unreachable Parachute API is logged and treated as "no cameras" for
    this cycle rather than crashing the detection service."""
    try:
        resp = requests.get(f"{config.PARACHUTE_API_BASE}/api/cameras", timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        logger.exception("Failed to fetch camera list from Parachute")
        return []


def list_enrolled_people() -> list[dict]:
    """[{"name", "photos": [...], "sample_count", "employee_id"}, ...] —
    Parachute's own roster, read-only. This service builds its OWN face
    embeddings from these photos (see recognizer.py) rather than needing
    Parachute to expose embeddings directly — the photos are already public
    via /photos/, so no new Parachute endpoint is required for this."""
    try:
        resp = requests.get(f"{config.PARACHUTE_API_BASE}/api/faces", timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        logger.exception("Failed to fetch enrolled-people roster from Parachute")
        return []


def fetch_photo_bytes(photo_filename: str) -> bytes | None:
    try:
        resp = requests.get(f"{config.PARACHUTE_API_BASE}/photos/{photo_filename}", timeout=10)
        resp.raise_for_status()
        return resp.content
    except Exception:
        logger.warning("Failed to fetch enrollment photo %s from Parachute", photo_filename)
        return None


def live_frame_ws_url(camera_id: int) -> str:
    return f"{config.PARACHUTE_WS_BASE}/ws/live/{camera_id}"
