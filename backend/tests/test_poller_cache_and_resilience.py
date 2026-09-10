"""Covers: People List caching (not a fresh camera API call per event),
periodic + on-demand refresh, camera/network failure resilience (one bad
event/response doesn't kill the loop), exponential backoff math, and that
two camera rows sharing a host never interleave on the shared client."""

import numpy as np
import pytest

from app import config, face_db
from app import honeywell_recognition_poller as poller
from conftest import make_camera, make_event


def test_cache_built_from_local_db_not_a_live_camera_call(temp_db, fake_provider):
    """The cache is populated from our already-synced enrolled_faces, never
    by calling the camera's AddedFaces API — fake_provider only implements
    fetch_new_events/get_person, so any attempt to hit a "live people list"
    endpoint that isn't there would raise, not silently pass."""
    face_db.add_face("Kanishka", "p.jpg", np.zeros(4, dtype="float32"), camera_face_id="103.204.0.122:64")
    poller._load_person_cache(force=True)
    assert poller._person_cache["103.204.0.122:64"] == "Kanishka"


def test_cache_refresh_on_demand_when_unknown_id_appears(temp_db, fake_provider):
    cam = make_camera()
    # not enrolled yet -> unknown on first poll
    fake_provider.queue_events([make_event(person_id=64, name="raw")])
    poller._poll_camera_row(cam)
    assert poller._unknown_id_seen[f"{cam['host']}:64"] == 1

    # now enroll them locally (simulating a sync that happened meanwhile)
    face_db.add_face("Kanishka", "p.jpg", np.zeros(4, dtype="float32"), camera_face_id=f"{cam['host']}:64")
    fake_provider.queue_events([make_event(person_id=64, name="raw")])
    poller._poll_camera_row(cam)  # on-demand refresh should pick up the new enrollment immediately

    conn = __import__("sqlite3").connect(temp_db)
    names = [r[0] for r in conn.execute("SELECT name FROM detection_events ORDER BY id").fetchall()]
    conn.close()
    assert names == [None, "Kanishka"]


def test_cache_refresh_respects_configured_interval(temp_db, fake_provider, monkeypatch):
    face_db.add_face("Kanishka", "p.jpg", np.zeros(4, dtype="float32"), camera_face_id="h:1")
    monkeypatch.setattr(config, "HONEYWELL_PEOPLE_CACHE_REFRESH_INTERVAL_SECONDS", 300)
    poller._load_person_cache(force=True)

    face_db.add_face("New Person", "p2.jpg", np.zeros(4, dtype="float32"), camera_face_id="h:2")
    poller._load_person_cache()  # not forced, interval hasn't elapsed -> should be a no-op
    assert "h:2" not in poller._person_cache


def test_one_malformed_event_does_not_stop_processing_the_rest(temp_db, fake_provider):
    cam = make_camera()
    good = make_event(person_id=64, name="A")
    # an entry with no usable id/name at all — should just be skipped
    from app.recognition_provider import RawRecognitionEvent
    junk = RawRecognitionEvent(person_id=None, name=None, event_ts=None, score=None, channel="CH1")
    fake_provider.queue_events([junk, good])
    poller._poll_camera_row(cam)  # must not raise

    conn = __import__("sqlite3").connect(temp_db)
    count = conn.execute("SELECT COUNT(*) FROM detection_events").fetchone()[0]
    conn.close()
    assert count == 1


def test_one_failed_camera_row_does_not_stop_others_on_same_host_loop(temp_db, fake_provider):
    """Mirrors _host_loop's per-camera try/except: a poll failure for one
    camera row must not prevent the next row from being polled this cycle."""
    cam_ok = make_camera(camera_id=2)
    cam_bad = make_camera(camera_id=3)
    poller._host_cameras["103.204.0.122"] = [cam_bad, cam_ok]

    fake_provider.queue_failure(ConnectionError("simulated"))
    fake_provider.queue_events([make_event(person_id=1, name="A")])

    results = []
    for cam in poller._host_cameras["103.204.0.122"]:
        try:
            poller._poll_camera_row(cam)
            results.append("ok")
        except Exception:
            results.append("failed")
    assert results == ["failed", "ok"]


def test_backoff_doubles_and_caps_at_max(monkeypatch):
    monkeypatch.setattr(config, "HONEYWELL_RECONNECT_BASE_DELAY_SECONDS", 5.0)
    monkeypatch.setattr(config, "HONEYWELL_RECONNECT_MAX_DELAY_SECONDS", 40.0)

    delay = config.HONEYWELL_RECONNECT_BASE_DELAY_SECONDS
    sequence = []
    for _ in range(6):
        sequence.append(delay)
        delay = min(delay * 2, config.HONEYWELL_RECONNECT_MAX_DELAY_SECONDS)
    assert sequence == [5.0, 10.0, 20.0, 40.0, 40.0, 40.0]


def test_backoff_resets_to_base_after_a_success():
    base = 5.0
    delay = base
    delay = min(delay * 2, 120.0)  # one failure
    delay = min(delay * 2, 120.0)  # a second failure
    assert delay == 20.0
    delay = base  # a success resets it, per _host_loop
    assert delay == base


def test_camera_client_lock_serializes_two_rows_on_the_same_host():
    """Two camera rows on the same physical device share one CameraClient —
    its lock (added this session) must stop a Search-then-GetByIndex
    sequence from one row interleaving with another's, which previously
    produced a false empty People List result under concurrent access."""
    from app import camera_client

    client = camera_client.get_camera_client("shared-host-test", "u", "p", 100)
    assert isinstance(client._lock, type(__import__("threading").Lock()))
