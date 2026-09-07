import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import face_db  # noqa: E402
from app import honeywell_recognition_poller as poller  # noqa: E402
from app.recognition_provider import RawRecognitionEvent  # noqa: E402


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """A real, throwaway SQLite DB (not a mock) so migrations/constraints/
    unique-index behavior are exercised for real, matching every other
    module's convention in this codebase (no mocking of the DB layer
    elsewhere either)."""
    db_path = tmp_path / "test_app.db"
    monkeypatch.setattr(face_db, "DB_PATH", db_path)
    face_db.init_db()
    return db_path


@pytest.fixture(autouse=True)
def reset_poller_state(temp_db):
    """The poller module holds process-wide mutable state (in-memory
    checkpoint mirror, cooldown timestamps, people cache, health snapshot) —
    reset it before every test so tests can't leak into each other."""
    poller._last_polled_until.clear()
    poller._last_logged_at.clear()
    with poller._person_cache_lock:
        poller._person_cache.clear()
    poller._person_cache_loaded_at = 0.0
    poller._unknown_id_seen.clear()
    poller._host_health.clear()
    poller._events_processed_today.clear()
    poller._events_processed_day = None
    poller._host_threads.clear()
    poller._host_stop_events.clear()
    poller._host_cameras.clear()
    poller._stop_event.clear()
    yield


class FakeProvider:
    """Stands in for HoneywellRecognitionProvider — returns canned events
    (or raises, to simulate a connection failure) without any real network
    call, so these tests never depend on the live camera's availability."""

    def __init__(self):
        self.calls: list[dict] = []
        self.responses: list[list[RawRecognitionEvent] | Exception] = []

    def queue_events(self, events: list[RawRecognitionEvent]) -> None:
        self.responses.append(events)

    def queue_failure(self, exc: Exception) -> None:
        self.responses.append(exc)

    def fetch_new_events(self, host, user, password, admin_port, channel, since, until):
        self.calls.append({"host": host, "channel": channel, "since": since, "until": until})
        if not self.responses:
            return []
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def get_person(self, host, user, password, admin_port, person_id):
        return None


@pytest.fixture
def fake_provider(monkeypatch):
    fp = FakeProvider()
    monkeypatch.setattr(poller, "_provider", fp)
    return fp


def make_camera(camera_id=2, host="103.204.0.122", stream_path="/h264/ch1/sub/av_stream"):
    return {
        "id": camera_id, "host": host, "user": "admin", "password": "secret",
        "admin_port": 100, "stream_path": stream_path,
    }


def make_event(person_id=None, name=None, event_ts=None, score=None, channel="CH1", raw_event_id=None):
    return RawRecognitionEvent(
        person_id=str(person_id) if person_id is not None else None,
        name=name, event_ts=event_ts if event_ts is not None else time.time(),
        score=score, channel=channel, raw_event_id=raw_event_id,
    )
