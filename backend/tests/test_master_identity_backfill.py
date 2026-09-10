"""Covers the master-identity backfill added for the "Main Section Camera
is master, Technical/Exit Camera are secondaries" architecture:
face_db.get_names_unmapped_for_host / get_embedding_for_name (pure DB
queries) and honeywell_recognition_poller._backfill_identity_mapping (the
push-to-secondary-device + read-back-and-record-its-ID flow), using a fake
camera client so no real network call is ever made."""

import numpy as np
import pytest

from app import config, face_db
from app import honeywell_recognition_poller as poller

PRIMARY_HOST = "103.204.0.122"
SECONDARY_HOST = "103.204.0.126"


def _enroll(name, camera_face_id):
    face_db.add_face(name, f"{name}.jpg", np.ones(512, dtype=np.float32), camera_face_id=camera_face_id)


def test_get_names_unmapped_for_host_finds_master_only_person(temp_db):
    _enroll("Rahul", f"{PRIMARY_HOST}:12345")
    missing = face_db.get_names_unmapped_for_host(PRIMARY_HOST, SECONDARY_HOST)
    assert [m["name"] for m in missing] == ["Rahul"]


def test_get_names_unmapped_for_host_excludes_already_mapped(temp_db):
    _enroll("Rahul", f"{PRIMARY_HOST}:12345")
    _enroll("Rahul", f"{SECONDARY_HOST}:999")
    missing = face_db.get_names_unmapped_for_host(PRIMARY_HOST, SECONDARY_HOST)
    assert missing == []


def test_get_names_unmapped_for_host_ignores_people_never_on_master(temp_db):
    # e.g. someone only ever added directly via the local /api/people upload,
    # never sourced from the master host at all — not this backfill's job.
    _enroll("LocalOnly", None)
    missing = face_db.get_names_unmapped_for_host(PRIMARY_HOST, SECONDARY_HOST)
    assert missing == []


def test_get_embedding_for_name_returns_existing_embedding(temp_db):
    _enroll("Rahul", f"{PRIMARY_HOST}:12345")
    emb = face_db.get_embedding_for_name("Rahul")
    assert emb is not None
    assert emb.shape == (512,)


def test_get_embedding_for_name_missing_person_returns_none(temp_db):
    assert face_db.get_embedding_for_name("Nobody") is None


class FakeSecondaryClient:
    def __init__(self, allow_list_after_push):
        self.added = []
        self._allow_list_after_push = allow_list_after_push

    def add_face(self, name, jpeg_bytes):
        self.added.append(name)
        return {"ok": True}

    def list_added_faces(self):
        return self._allow_list_after_push


def _photo(tmp_path, monkeypatch, name):
    monkeypatch.setattr(face_db, "ENROLLMENT_PHOTOS_DIR", tmp_path)
    (tmp_path / f"{name}.jpg").write_bytes(b"fake-jpeg-bytes")


def test_backfill_identity_mapping_pushes_and_records_new_device_id(temp_db, monkeypatch, tmp_path):
    _enroll("Rahul", f"{PRIMARY_HOST}:12345")
    _photo(tmp_path, monkeypatch, "Rahul")
    monkeypatch.setattr(config, "PRIMARY_PEOPLE_SOURCE_HOST", PRIMARY_HOST)

    fake_client = FakeSecondaryClient(allow_list_after_push=[{"Id": 777, "Name": "Rahul"}])
    monkeypatch.setattr(poller.camera_client, "get_camera_client", lambda *a, **k: fake_client)

    cam = {"user": "admin", "password": "secret", "admin_port": 443}
    poller._backfill_identity_mapping(SECONDARY_HOST, cam)

    assert fake_client.added == ["Rahul"]
    assert face_db.get_name_by_camera_face_id(f"{SECONDARY_HOST}:777") == "Rahul"
    # already-mapped now, so a second run must be a no-op even after
    # clearing the throttle (proves it converges, not just "ran once")
    poller._last_identity_backfill_at.clear()
    fake_client.added.clear()
    poller._backfill_identity_mapping(SECONDARY_HOST, cam)
    assert fake_client.added == []


def test_backfill_identity_mapping_skips_the_master_host_itself(temp_db, monkeypatch):
    monkeypatch.setattr(config, "PRIMARY_PEOPLE_SOURCE_HOST", PRIMARY_HOST)
    calls = []
    monkeypatch.setattr(poller.camera_client, "get_camera_client", lambda *a, **k: calls.append(1))
    poller._backfill_identity_mapping(PRIMARY_HOST, {"user": "admin", "password": "x", "admin_port": 100})
    assert calls == []


def test_backfill_identity_mapping_respects_interval_throttle(temp_db, monkeypatch, tmp_path):
    _enroll("Rahul", f"{PRIMARY_HOST}:12345")
    _photo(tmp_path, monkeypatch, "Rahul")
    monkeypatch.setattr(config, "PRIMARY_PEOPLE_SOURCE_HOST", PRIMARY_HOST)
    fake_client = FakeSecondaryClient(allow_list_after_push=[{"Id": 777, "Name": "Rahul"}])
    monkeypatch.setattr(poller.camera_client, "get_camera_client", lambda *a, **k: fake_client)

    cam = {"user": "admin", "password": "secret", "admin_port": 443}
    poller._backfill_identity_mapping(SECONDARY_HOST, cam)
    assert fake_client.added == ["Rahul"]

    # Simulate a second poll cycle happening immediately after (normal
    # HONEYWELL_POLL_INTERVAL_SECONDS cadence, e.g. 20s) — must NOT re-check
    # the DB/re-hit the device again until _IDENTITY_BACKFILL_INTERVAL_SECONDS
    # has actually elapsed, even though Rahul is (correctly) still mapped.
    fake_client.added.clear()
    poller._backfill_identity_mapping(SECONDARY_HOST, cam)
    assert fake_client.added == []


def test_backfill_identity_mapping_skips_person_with_missing_photo_file(temp_db, monkeypatch, tmp_path):
    _enroll("Rahul", f"{PRIMARY_HOST}:12345")
    monkeypatch.setattr(face_db, "ENROLLMENT_PHOTOS_DIR", tmp_path)  # no Rahul.jpg written
    monkeypatch.setattr(config, "PRIMARY_PEOPLE_SOURCE_HOST", PRIMARY_HOST)
    fake_client = FakeSecondaryClient(allow_list_after_push=[])
    monkeypatch.setattr(poller.camera_client, "get_camera_client", lambda *a, **k: fake_client)

    poller._backfill_identity_mapping(SECONDARY_HOST, {"user": "admin", "password": "x", "admin_port": 443})

    assert fake_client.added == []
    assert face_db.get_names_unmapped_for_host(PRIMARY_HOST, SECONDARY_HOST) != []
