"""Covers: event-ID dedup (real ID and composite fallback), restart-recovery
idempotency, attendance write-throttle vs event-identity dedup being
distinct concepts, unknown-person-ID handling, low-confidence tagging."""

import sqlite3

from app import config, face_db
from app import honeywell_recognition_poller as poller
from conftest import make_camera, make_event


def _rows(temp_db):
    conn = sqlite3.connect(temp_db)
    rows = conn.execute(
        "SELECT name, honeywell_person_id, event_key, recognition_source, score FROM detection_events"
    ).fetchall()
    conn.close()
    return rows


def test_known_person_id_resolves_via_people_cache(temp_db, fake_provider):
    cam = make_camera()
    face_db.add_face("Kanishka", "photo.jpg", __import__("numpy").zeros(4, dtype="float32"),
                      camera_face_id=f"{cam['host']}:64")

    fake_provider.queue_events([make_event(person_id=64, name="kanishka_raw_camera_name")])
    poller._poll_camera_row(cam)

    rows = _rows(temp_db)
    assert len(rows) == 1
    assert rows[0][0] == "Kanishka"  # resolved via cache, not the raw camera-reported name
    assert rows[0][1] == "64"
    assert rows[0][3] == "honeywell"


def test_unknown_person_id_stored_not_discarded(temp_db, fake_provider):
    cam = make_camera()
    fake_provider.queue_events([make_event(person_id=999, name="Nobody Enrolled")])
    poller._poll_camera_row(cam)

    rows = _rows(temp_db)
    assert len(rows) == 1
    assert rows[0][0] is None  # name left null — never silently dropped, never crashes
    assert rows[0][1] == "999"
    assert rows[0][3] == "unresolved"
    assert poller._unknown_id_seen[f"{cam['host']}:999"] == 1


def test_event_key_dedup_survives_simulated_restart(temp_db, fake_provider):
    """The same Honeywell occurrence (same person, same event_ts) polled
    twice — e.g. because a restart re-fetched an overlapping window — must
    only ever produce one row, even though the in-memory de-dup state was
    wiped by the "restart" (poller._last_polled_until/_last_logged_at
    cleared, simulating a fresh process)."""
    cam = make_camera()
    face_db.add_face("Kanishka", "p.jpg", __import__("numpy").zeros(4, dtype="float32"),
                      camera_face_id=f"{cam['host']}:64")
    event = make_event(person_id=64, name="kanishka", event_ts=1_800_000_000.0)

    fake_provider.queue_events([event])
    poller._poll_camera_row(cam)

    # simulate a restart: wipe every in-memory dedup/cooldown structure
    poller._last_polled_until.clear()
    poller._last_logged_at.clear()

    fake_provider.queue_events([event])  # the exact same occurrence again
    poller._poll_camera_row(cam)

    rows = _rows(temp_db)
    assert len(rows) == 1, "the same Honeywell occurrence was written twice across a simulated restart"


def test_real_event_id_used_as_dedup_key_when_present(temp_db, fake_provider):
    cam = make_camera()
    event = make_event(person_id=64, name="A", raw_event_id="evt-123")
    fake_provider.queue_events([event])
    poller._poll_camera_row(cam)
    rows = _rows(temp_db)
    assert rows[0][2] == "real:evt-123"


def test_composite_key_used_when_no_real_event_id(temp_db, fake_provider):
    cam = make_camera()
    event = make_event(person_id=64, name="A", event_ts=1700000000.0)
    fake_provider.queue_events([event])
    poller._poll_camera_row(cam)
    rows = _rows(temp_db)
    assert rows[0][2].startswith("composite:64:1700000000.0:")


def test_distinct_close_together_sightings_throttled_by_cooldown(temp_db, fake_provider, monkeypatch):
    """DETECTION_LOG_COOLDOWN_SECONDS is a SEPARATE concept from event_key
    dedup: two genuinely distinct sightings (different event_ts, so
    different event_key) of the same person moments apart should still be
    throttled to avoid flooding raw sightings — this is deliberate existing
    behavior, not a bug, and must not be removed by the event_key redesign."""
    monkeypatch.setattr(config, "DETECTION_LOG_COOLDOWN_SECONDS", 30)
    cam = make_camera()
    fake_provider.queue_events([make_event(person_id=64, name="A", event_ts=1000.0)])
    poller._poll_camera_row(cam)
    fake_provider.queue_events([make_event(person_id=64, name="A", event_ts=1005.0)])  # 5s later, distinct event_key
    poller._poll_camera_row(cam)

    rows = _rows(temp_db)
    assert len(rows) == 1, "a second distinct sighting within the cooldown window should have been throttled"


def test_low_confidence_tagged_not_dropped(temp_db, fake_provider, monkeypatch):
    monkeypatch.setattr(config, "HONEYWELL_LOW_CONFIDENCE_THRESHOLD", 0.5)
    cam = make_camera()
    face_db.add_face("Kanishka", "p.jpg", __import__("numpy").zeros(4, dtype="float32"),
                      camera_face_id=f"{cam['host']}:64")
    fake_provider.queue_events([make_event(person_id=64, name="kanishka", score=0.2)])
    poller._poll_camera_row(cam)

    rows = _rows(temp_db)
    assert len(rows) == 1  # Honeywell's own recognition still authoritative — never discarded
    assert rows[0][3] == "low_confidence"


def test_stranger_with_no_id_is_ignored(temp_db, fake_provider):
    cam = make_camera()
    fake_provider.queue_events([make_event(person_id=None, name="Unknown")])
    poller._poll_camera_row(cam)
    assert _rows(temp_db) == []
