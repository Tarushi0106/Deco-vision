"""Covers: face_db.get_recent_camera_recognition (read-only freshness
query) and CameraPipeline.set_detections's new Honeywell-preference branch
— only applies with exactly one face in frame, never mutates the caller's
dict, and correctly falls back to local recognition otherwise."""

import time

from app import config, face_db
from app.pipeline import CameraPipeline


def _insert_event(camera_id, name, score, ts, recognition_source="honeywell"):
    face_db.insert_detection_event_if_new(
        camera_id, name, bbox=[], event_key=f"{name}:{ts}", score=score,
        recognition_source=recognition_source,
    )
    # insert_detection_event_if_new always stamps ts=time.time() internally;
    # override it directly so freshness-window tests are deterministic.
    import sqlite3
    conn = sqlite3.connect(face_db.DB_PATH)
    conn.execute("UPDATE detection_events SET ts = ? WHERE event_key = ?", (ts, f"{name}:{ts}"))
    conn.commit()
    conn.close()


def test_get_recent_camera_recognition_returns_fresh_honeywell_row(temp_db):
    now = time.time()
    _insert_event(2, "Kanishka", 0.9, now - 5)
    result = face_db.get_recent_camera_recognition(2, within_seconds=45)
    assert result is not None
    assert result["name"] == "Kanishka"


def test_get_recent_camera_recognition_ignores_stale_rows(temp_db):
    now = time.time()
    _insert_event(2, "Kanishka", 0.9, now - 100)
    assert face_db.get_recent_camera_recognition(2, within_seconds=45) is None


def test_get_recent_camera_recognition_ignores_non_honeywell_source(temp_db):
    now = time.time()
    _insert_event(2, "Kanishka", 0.9, now - 5, recognition_source="unresolved")
    assert face_db.get_recent_camera_recognition(2, within_seconds=45) is None


def test_get_recent_camera_recognition_scoped_to_the_right_camera(temp_db):
    now = time.time()
    _insert_event(3, "Kanishka", 0.9, now - 5)
    assert face_db.get_recent_camera_recognition(2, within_seconds=45) is None


def test_set_detections_prefers_honeywell_when_single_face(temp_db, monkeypatch):
    monkeypatch.setattr(config, "HONEYWELL_LIVE_PREFERENCE_WINDOW_SECONDS", 45)
    monkeypatch.setattr(
        face_db, "get_recent_camera_recognition",
        lambda camera_id, within_seconds: {"name": "Kanishka", "score": 0.9, "ts": time.time()},
    )
    pipeline = CameraPipeline(camera_id=2)
    original = {"bbox": [1, 2, 3, 4], "name": "Unknown", "score": 0.1}
    pipeline.set_detections([original])

    result = pipeline.get_latest_detections()
    assert len(result) == 1
    assert result[0]["name"] == "Kanishka"
    assert result[0]["source"] == "honeywell"
    assert result[0]["local_name"] == "Unknown"  # original local match preserved for comparison
    assert original["name"] == "Unknown"  # caller's dict never mutated in place


def test_set_detections_falls_back_to_local_with_multiple_faces(temp_db, monkeypatch):
    monkeypatch.setattr(config, "HONEYWELL_LIVE_PREFERENCE_WINDOW_SECONDS", 45)
    called = []
    monkeypatch.setattr(
        face_db, "get_recent_camera_recognition",
        lambda camera_id, within_seconds: called.append(1) or {"name": "Kanishka", "score": 0.9, "ts": time.time()},
    )
    pipeline = CameraPipeline(camera_id=2)
    faces = [
        {"bbox": [1, 2, 3, 4], "name": "Unknown", "score": 0.1},
        {"bbox": [5, 6, 7, 8], "name": "Unknown", "score": 0.1},
    ]
    pipeline.set_detections(faces)

    assert called == []  # never even queried — ambiguous with >1 face, must not guess
    result = pipeline.get_latest_detections()
    assert len(result) == 2
    assert all(f["name"] == "Unknown" for f in result)


def test_set_detections_disabled_via_zero_window(temp_db, monkeypatch):
    monkeypatch.setattr(config, "HONEYWELL_LIVE_PREFERENCE_WINDOW_SECONDS", 0)
    monkeypatch.setattr(
        face_db, "get_recent_camera_recognition",
        lambda camera_id, within_seconds: {"name": "Kanishka", "score": 0.9, "ts": time.time()},
    )
    pipeline = CameraPipeline(camera_id=2)
    pipeline.set_detections([{"bbox": [1, 2, 3, 4], "name": "Unknown", "score": 0.1}])

    result = pipeline.get_latest_detections()
    assert result[0]["name"] == "Unknown"  # feature off — never overridden


def test_set_detections_no_recent_honeywell_event_keeps_local(temp_db, monkeypatch):
    monkeypatch.setattr(config, "HONEYWELL_LIVE_PREFERENCE_WINDOW_SECONDS", 45)
    monkeypatch.setattr(face_db, "get_recent_camera_recognition", lambda camera_id, within_seconds: None)
    pipeline = CameraPipeline(camera_id=2)
    pipeline.set_detections([{"bbox": [1, 2, 3, 4], "name": "Unknown", "score": 0.1}])

    result = pipeline.get_latest_detections()
    assert result[0]["name"] == "Unknown"


def test_set_detections_stamps_computed_at(temp_db, monkeypatch):
    """The /ws/detections poll fires on its own fixed schedule regardless of
    whether recognition produced anything new (see main.py's
    DETECTIONS_FPS) — the frontend can only tell stale data from fresh data
    via this timestamp, not via message arrival. Must reflect when
    set_detections() actually ran, not some other clock."""
    monkeypatch.setattr(config, "HONEYWELL_LIVE_PREFERENCE_WINDOW_SECONDS", 0)  # isolate from that branch
    pipeline = CameraPipeline(camera_id=2)
    assert pipeline.get_latest_detections_computed_at() == 0.0  # nothing computed yet

    before = time.time()
    pipeline.set_detections([{"bbox": [1, 2, 3, 4], "name": "Unknown", "score": 0.1}])
    after = time.time()

    computed_at = pipeline.get_latest_detections_computed_at()
    assert before <= computed_at <= after


def test_set_detections_computed_at_advances_on_each_call(temp_db, monkeypatch):
    monkeypatch.setattr(config, "HONEYWELL_LIVE_PREFERENCE_WINDOW_SECONDS", 0)
    pipeline = CameraPipeline(camera_id=2)
    pipeline.set_detections([])
    first = pipeline.get_latest_detections_computed_at()

    time.sleep(0.01)
    pipeline.set_detections([])
    second = pipeline.get_latest_detections_computed_at()

    assert second > first


def test_pipeline_manager_get_latest_detections_computed_at_unknown_camera_returns_zero():
    from app.pipeline import pipeline_manager
    assert pipeline_manager.get_latest_detections_computed_at(999999) == 0.0


def test_effective_timeout_defaults_to_configured_target_before_any_cycle(temp_db, monkeypatch):
    """No cycle has completed yet (_recent_cycle_gap is 0) — must fall back
    to the configured target, not 0, or the very first frame would have a
    zero-second clear timeout."""
    monkeypatch.setattr(config, "HONEYWELL_LIVE_PREFERENCE_WINDOW_SECONDS", 0)
    monkeypatch.setattr(config, "IDENTITY_LOST_TIMEOUT_SECONDS", 1.0)
    pipeline = CameraPipeline(camera_id=2)
    assert pipeline.get_effective_identity_lost_timeout() == 1.0


def test_effective_timeout_widens_for_a_slow_camera(temp_db, monkeypatch):
    """The real bug this fixes: a camera whose actual recognition cycle
    takes longer than the flat configured timeout must get a WIDER
    effective timeout, or a still-present person's name flickers off
    between cycles even though they never left."""
    monkeypatch.setattr(config, "HONEYWELL_LIVE_PREFERENCE_WINDOW_SECONDS", 0)
    monkeypatch.setattr(config, "IDENTITY_LOST_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(config, "IDENTITY_LOST_TIMEOUT_SAFETY_FACTOR", 1.5)
    pipeline = CameraPipeline(camera_id=2)

    real_time = time.time
    fake_now = [real_time()]
    monkeypatch.setattr(time, "time", lambda: fake_now[0])

    pipeline.set_detections([])  # first cycle — no gap measurable yet
    fake_now[0] += 10.0  # this camera's real cycle took 10s
    pipeline.set_detections([])

    # effective timeout must widen to cover the real 10s cycle (with margin),
    # not stay pinned at the flat 1.0s target that caused the flicker
    assert pipeline.get_effective_identity_lost_timeout() == 15.0  # 10 * 1.5


def test_effective_timeout_never_drops_below_configured_target_for_a_fast_camera(temp_db, monkeypatch):
    """A camera whose cycles are already faster than the configured target
    must not get an ARTIFICIALLY LONGER timeout than the target — the
    target is the goal to approach, not a floor to always inflate past."""
    monkeypatch.setattr(config, "HONEYWELL_LIVE_PREFERENCE_WINDOW_SECONDS", 0)
    monkeypatch.setattr(config, "IDENTITY_LOST_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(config, "IDENTITY_LOST_TIMEOUT_SAFETY_FACTOR", 1.5)
    pipeline = CameraPipeline(camera_id=2)

    real_time = time.time
    fake_now = [real_time()]
    monkeypatch.setattr(time, "time", lambda: fake_now[0])

    pipeline.set_detections([])
    fake_now[0] += 0.2  # a fast camera, well under the 1.0s target
    pipeline.set_detections([])

    assert pipeline.get_effective_identity_lost_timeout() == 1.0  # floor at the configured target, not 0.3
