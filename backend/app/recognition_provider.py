"""Vendor abstraction for the recognition-event pipeline: the consumer loop
in honeywell_recognition_poller.py depends on this small interface, not on
camera_client.CameraClient directly — so a future camera vendor can plug in
its own provider without Attendance/Analytics/Dashboard (which only ever see
face_db.detection_events, never a vendor's own API shape) needing to change.

Kept deliberately thin: this is not a plugin system, just enough indirection
that "how do we talk to this camera's recognition API" is isolated from
"what do we do with a recognition event once we have one".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from . import camera_client

logger = logging.getLogger("dashboard.honeywell_recognition")

# Honeywell's own event-timestamp format has never been confirmed live (see
# module docstring in honeywell_recognition_poller.py) — this is the format
# WE send in Search's StartTime/EndTime, and the best guess for what a match
# entry's own Time-like field would echo back, but it's a guess, not a
# confirmed fact. Numeric values are handled separately below.
_ASSUMED_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

_warned_unparseable_ts = False


@dataclass
class RawRecognitionEvent:
    """One entry from a vendor's recognition-event log, before any local
    dedup/identity-resolution — see honeywell_recognition_poller.py for what
    happens to it next."""

    person_id: str | None  # vendor's own unique Allow-List/person ID, if the entry carries one
    name: str | None  # vendor-reported name — used only as a fallback when person_id doesn't resolve locally
    event_ts: float | None  # vendor's own event timestamp (epoch seconds), if parseable
    score: float | None  # vendor's own confidence/similarity, 0-1, if present
    channel: str
    raw_event_id: str | None = None  # a genuine per-occurrence event ID, distinct from person_id, if the vendor's API ever exposes one


@dataclass
class PersonInfo:
    person_id: str
    name: str


class RecognitionProvider:
    def fetch_new_events(
        self, host: str, user: str, password: str, admin_port: int, channel: str,
        since: datetime, until: datetime,
    ) -> list[RawRecognitionEvent]:
        raise NotImplementedError

    def get_person(self, host: str, user: str, password: str, admin_port: int, person_id: str) -> PersonInfo | None:
        raise NotImplementedError


def _fmt(dt: datetime) -> str:
    return dt.strftime(_ASSUMED_TIME_FORMAT)


def _first(entry: dict, *keys, default=None):
    for key in keys:
        if key in entry and entry[key] not in (None, ""):
            return entry[key]
    return default


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_event_ts(value) -> float | None:
    """Best-effort parse of whatever Honeywell's SnapedFaces match-entry
    timestamp field turns out to be (never confirmed live — see module
    docstring). Handles the two plausible shapes: a numeric epoch (seconds
    or milliseconds, auto-detected by magnitude) or a string in the same
    format we send our own StartTime/EndTime as. Returns None rather than
    guessing further, and logs once so a wrong assumption is visible instead
    of silently producing bad latency numbers."""
    global _warned_unparseable_ts
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if value > 1e12:  # looks like milliseconds
            return value / 1000.0
        if value > 1e9:  # looks like seconds
            return float(value)
        return None
    if isinstance(value, str):
        try:
            return datetime.strptime(value, _ASSUMED_TIME_FORMAT).timestamp()
        except ValueError:
            pass
    if not _warned_unparseable_ts:
        logger.warning(
            "Could not parse a SnapedFaces event timestamp value (%r) with any known format — "
            "honeywell_event_ts will be recorded as unknown for this and similar entries until "
            "the real format is confirmed from a raw_response debug log line.",
            value,
        )
        _warned_unparseable_ts = True
    return None


def _extract_entries(response: dict) -> list[dict]:
    """SnapedFaces/GetByIndex's response shape has never been confirmed
    against a live device from this environment — by analogy with the
    confirmed-live AddedFaces/GetByIndex shape (camera_client.py::
    list_added_faces, live-verified this session as {"data": {"FaceInfo":
    [...]}}) it's expected to match. Raw response is logged at DEBUG by the
    caller so a real deployment can confirm/correct this quickly."""
    data = response.get("data", {}) if isinstance(response, dict) else {}
    entries = data.get("FaceInfo") or data.get("MatchedFaces") or []
    return entries if isinstance(entries, list) else []


class HoneywellRecognitionProvider(RecognitionProvider):
    def fetch_new_events(
        self, host: str, user: str, password: str, admin_port: int, channel: str,
        since: datetime, until: datetime,
    ) -> list[RawRecognitionEvent]:
        client = camera_client.get_camera_client(host, user, password, admin_port)
        response = client.search_snaped_faces(_fmt(since), _fmt(until), channel=channel)
        logger.debug("host=%s channel=%s stage=raw_response body=%s", host, channel, response)
        entries = _extract_entries(response)

        events: list[RawRecognitionEvent] = []
        for entry in entries:
            name = _first(entry, "Name", default=None)
            person_id = _as_int(_first(entry, "MatchedId", "RelateId", "AddedId", "PersonId", "FaceId"))
            raw_event_id = _first(entry, "EventId", "RecordId", "Sn")  # tried defensively, never confirmed live
            similarity = _as_float(_first(entry, "Similarity", "Score"))
            score = similarity / 100.0 if similarity is not None and similarity > 1 else similarity
            event_ts = _parse_event_ts(_first(entry, "Time", "SnapTime", "CaptureTime"))
            events.append(
                RawRecognitionEvent(
                    person_id=str(person_id) if person_id is not None else None,
                    name=name if isinstance(name, str) else None,
                    event_ts=event_ts,
                    score=score,
                    channel=channel,
                    raw_event_id=str(raw_event_id) if raw_event_id is not None else None,
                )
            )
        return events

    def get_person(self, host: str, user: str, password: str, admin_port: int, person_id: str) -> PersonInfo | None:
        """Live single-ID lookup, available for callers that explicitly want
        one (e.g. a future manual "resolve this unknown ID now" admin
        action) — NOT called from the poller's per-event hot path, which
        deliberately resolves against the local people cache (populated by
        the primary-camera sync) rather than issuing a live camera API call
        per unresolved event, to avoid compounding this device's documented
        connection instability under repeated rapid requests."""
        client = camera_client.get_camera_client(host, user, password, admin_port)
        face = client.get_added_face_by_id(int(person_id))
        if not face or not face.get("Name"):
            return None
        return PersonInfo(person_id=str(face["Id"]), name=face["Name"])
