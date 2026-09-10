"""Targeted tests for this session's observability additions (task section
14): last_successful_api_ts must move on every successful API call (even one
that returns zero events), last_successful_recognition_ts must move ONLY
when a real Honeywell-sourced event is actually written — so a stuck clock
on either one is immediately diagnostic of a different failure mode."""

import numpy as np
import requests

from app import face_db
from app import honeywell_recognition_poller as poller
from conftest import make_camera, make_event


def test_api_ts_and_recognition_ts_both_move_on_a_real_event(temp_db, fake_provider):
    cam = make_camera()
    face_db.add_face("Kanishka", "photo.jpg", np.zeros(4, dtype="float32"), camera_face_id=f"{cam['host']}:64")
    fake_provider.queue_events([make_event(person_id=64, name="raw")])

    assert cam["host"] not in poller._last_successful_api_ts
    assert cam["host"] not in poller._last_successful_recognition_ts
    poller._poll_camera_row(cam)

    assert cam["host"] in poller._last_successful_api_ts
    assert cam["host"] in poller._last_successful_recognition_ts


def test_api_ts_moves_even_with_zero_events_but_recognition_ts_does_not(temp_db, fake_provider):
    cam = make_camera()
    fake_provider.queue_events([])  # API call succeeds, genuinely nothing to report

    poller._poll_camera_row(cam)

    assert cam["host"] in poller._last_successful_api_ts
    assert cam["host"] not in poller._last_successful_recognition_ts


def test_recognition_ts_not_moved_by_unresolved_or_low_confidence_events(temp_db, fake_provider, monkeypatch):
    from app import config
    cam = make_camera()
    # person_id never enrolled locally -> stays unresolved, recognition_source="unresolved"
    fake_provider.queue_events([make_event(person_id=999, name=None)])
    poller._poll_camera_row(cam)

    assert cam["host"] in poller._last_successful_api_ts
    assert cam["host"] not in poller._last_successful_recognition_ts


def test_api_failure_moves_neither_timestamp(temp_db, fake_provider):
    cam = make_camera()
    fake_provider.queue_failure(ConnectionError("simulated Honeywell reset"))

    try:
        poller._poll_camera_row(cam)
        assert False, "expected the simulated failure to propagate"
    except ConnectionError:
        pass

    assert cam["host"] not in poller._last_successful_api_ts
    assert cam["host"] not in poller._last_successful_recognition_ts


class _StopAfterOneIteration:
    """Mimics just enough of threading.Event's interface for _host_loop:
    lets its while-condition run exactly once, then stops — without a real
    multi-second sleep through stop_evt.wait(backoff_delay)."""

    def __init__(self):
        self._done = False

    def is_set(self):
        return self._done

    def wait(self, timeout=None):
        self._done = True
        return True


def test_host_health_surfaces_next_retry_and_failure_category(temp_db, fake_provider, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "HONEYWELL_RECONNECT_BASE_DELAY_SECONDS", 5.0)
    cam = make_camera()
    poller._host_cameras[cam["host"]] = [cam]
    fake_provider.queue_failure(requests.exceptions.ConnectionError("('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))"))
    stop_evt = _StopAfterOneIteration()

    poller._host_loop(cam["host"], stop_evt)

    health = poller.get_health_status()[cam["host"]]
    assert health["healthy"] is False
    assert health["last_failure_category"] == "CONNECTION_RESET"
    assert health["next_retry_at"] > 0
    assert health["last_successful_api_ts"] is None
    assert health["last_successful_recognition_ts"] is None
