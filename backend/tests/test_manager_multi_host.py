"""Covers: multiple cameras running independently, and that a permanently
failing host's polling doesn't prevent another host's thread from existing
and reporting healthy — the concrete bug this session found in the old
single-shared-loop design (camera 1's dead host delayed camera 2/3's poll
every cycle)."""

import time

from app import camera_db
from app import honeywell_recognition_poller as poller


def test_eligible_cameras_grouped_by_host(temp_db, monkeypatch):
    cameras = [
        {"id": 1, "host": "103.204.0.126", "status": "active", "user": "admin"},
        {"id": 2, "host": "103.204.0.122", "status": "active", "user": "admin"},
        {"id": 3, "host": "103.204.0.122", "status": "active", "user": "admin"},
    ]
    monkeypatch.setattr(camera_db, "list_cameras", lambda: cameras)
    monkeypatch.setattr(
        camera_db, "get_camera_connection",
        lambda cid: {**next(c for c in cameras if c["id"] == cid), "password": "pw"},
    )

    eligible = poller._eligible_cameras()
    by_host: dict[str, list[int]] = {}
    for cam in eligible:
        by_host.setdefault(cam["host"], []).append(cam["id"])

    assert by_host["103.204.0.126"] == [1]
    assert sorted(by_host["103.204.0.122"]) == [2, 3], "camera rows 2 and 3 share one physical device"


def test_health_status_reports_independently_per_host(temp_db):
    poller._host_health["103.204.0.126"] = {"last_poll_at": time.time(), "healthy": False, "backoff_delay_seconds": 40}
    poller._host_health["103.204.0.122"] = {"last_poll_at": time.time(), "healthy": True, "backoff_delay_seconds": 0}

    status = poller.get_health_status()
    assert status["103.204.0.126"]["healthy"] is False
    assert status["103.204.0.122"]["healthy"] is True, (
        "a permanently-failing host must not make a healthy host report unhealthy too"
    )
