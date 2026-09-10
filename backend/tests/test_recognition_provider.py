"""Parsing/timestamp tests for recognition_provider.py — the Honeywell-
specific translation from a raw API response into RawRecognitionEvent.
Covers plan items: People List / recognition-event parsing, timestamp
parsing, malformed-response resilience."""

from datetime import datetime

from app import camera_client
from app.recognition_provider import HoneywellRecognitionProvider, _extract_entries, _parse_event_ts


def test_extract_entries_confirmed_shape():
    # {"data": {"FaceInfo": [...]}} is the confirmed-live shape (AddedFaces,
    # verified this session: 26 real people, {Id, GrpId, Name}); SnapedFaces
    # is assumed to match by analogy.
    response = {"data": {"FaceInfo": [{"Name": "A"}, {"Name": "B"}]}}
    assert _extract_entries(response) == [{"Name": "A"}, {"Name": "B"}]


def test_extract_entries_missing_data_is_empty():
    assert _extract_entries({}) == []
    assert _extract_entries({"data": {}}) == []
    assert _extract_entries({"data": {"FaceInfo": "not-a-list"}}) == []


def test_parse_event_ts_string_matches_our_own_request_format():
    ts = _parse_event_ts("2026-01-15 10:20:14")
    assert ts == datetime.strptime("2026-01-15 10:20:14", "%Y-%m-%d %H:%M:%S").timestamp()


def test_parse_event_ts_epoch_seconds():
    now_seconds = 1_800_000_000.0  # plausible "seconds since epoch" magnitude
    assert _parse_event_ts(now_seconds) == now_seconds


def test_parse_event_ts_epoch_milliseconds_auto_detected():
    now_ms = 1_800_000_000_000.0
    assert _parse_event_ts(now_ms) == now_ms / 1000.0


def test_parse_event_ts_unparseable_returns_none_not_a_crash():
    assert _parse_event_ts("not-a-timestamp") is None
    assert _parse_event_ts(None) is None
    assert _parse_event_ts({}) is None


class _FakeClient:
    def __init__(self, response):
        self._response = response

    def search_snaped_faces(self, start, end, channel="CH1", count=20):
        return self._response

    def get_added_face_by_id(self, face_id):
        return {"Id": face_id, "GrpId": 2, "Name": "Someone"}


def test_fetch_new_events_confirmed_person_id_field(monkeypatch):
    response = {"data": {"FaceInfo": [
        {"Name": "kanishka", "MatchedId": 64, "Time": "2026-01-15 10:20:14", "Similarity": 92},
    ]}}
    monkeypatch.setattr(camera_client, "get_camera_client", lambda *a, **k: _FakeClient(response))
    provider = HoneywellRecognitionProvider()
    events = provider.fetch_new_events("103.204.0.122", "admin", "pw", 100, "CH1", datetime.now(), datetime.now())

    assert len(events) == 1
    e = events[0]
    assert e.person_id == "64"
    assert e.name == "kanishka"
    assert e.score == 0.92  # 92 similarity -> normalized to 0-1
    assert e.event_ts is not None


def test_fetch_new_events_falls_back_through_candidate_id_fields(monkeypatch):
    # Exact field name for a match entry's person ID has never been
    # confirmed live — verify every candidate name the code defensively
    # tries actually works, not just the first one.
    for field in ("MatchedId", "RelateId", "AddedId", "PersonId", "FaceId"):
        response = {"data": {"FaceInfo": [{"Name": "X", field: 77}]}}
        monkeypatch.setattr(camera_client, "get_camera_client", lambda *a, **k: _FakeClient(response))
        provider = HoneywellRecognitionProvider()
        events = provider.fetch_new_events("h", "u", "p", 100, "CH1", datetime.now(), datetime.now())
        assert events[0].person_id == "77", f"field {field} did not resolve"


def test_fetch_new_events_malformed_entry_does_not_crash(monkeypatch):
    # An entry missing every expected field should degrade to "no id, no
    # name" rather than raising — the poller drops those, it never crashes.
    response = {"data": {"FaceInfo": [{"UnexpectedField": 123}]}}
    monkeypatch.setattr(camera_client, "get_camera_client", lambda *a, **k: _FakeClient(response))
    provider = HoneywellRecognitionProvider()
    events = provider.fetch_new_events("h", "u", "p", 100, "CH1", datetime.now(), datetime.now())
    assert len(events) == 1
    assert events[0].person_id is None
    assert events[0].name is None
