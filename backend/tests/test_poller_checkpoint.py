"""Covers: persistent checkpoint (not memory-only), incremental retrieval
(narrow window, not full history), restart recovery."""

from datetime import datetime, timedelta

from app import face_db
from app import honeywell_recognition_poller as poller
from conftest import make_camera


def test_first_poll_ever_uses_short_lookback_not_full_history(temp_db, fake_provider):
    cam = make_camera()
    fake_provider.queue_events([])
    poller._poll_camera_row(cam)

    call = fake_provider.calls[0]
    lookback = call["until"] - call["since"]
    assert lookback <= timedelta(seconds=poller._INITIAL_LOOKBACK_SECONDS + 1)


def test_checkpoint_persisted_after_successful_poll(temp_db, fake_provider):
    cam = make_camera()
    fake_provider.queue_events([])
    poller._poll_camera_row(cam)

    saved = face_db.get_poll_checkpoint(cam["id"])
    assert saved is not None
    assert saved["host"] == cam["host"]
    assert saved["last_processed_ts"] is not None


def test_restart_resumes_from_persisted_checkpoint_not_full_history(temp_db, fake_provider):
    cam = make_camera()
    fake_provider.queue_events([])
    poller._poll_camera_row(cam)
    first_checkpoint = face_db.get_poll_checkpoint(cam["id"])["last_processed_ts"]

    # simulate a full process restart: wipe every in-memory structure the
    # module holds, but keep the DB (the whole point of persisting it)
    poller._last_polled_until.clear()

    fake_provider.queue_events([])
    poller._poll_camera_row(cam)

    second_call_since = fake_provider.calls[1]["since"]
    assert abs(second_call_since.timestamp() - first_checkpoint) < 1.0, (
        "restart re-polled from a fresh lookback window instead of the persisted checkpoint"
    )


def test_checkpoint_not_advanced_on_failed_poll(temp_db, fake_provider):
    """A failed fetch must not move the checkpoint forward — otherwise
    events that occurred during the outage would be permanently skipped
    once the connection recovers."""
    cam = make_camera()
    fake_provider.queue_failure(ConnectionError("simulated camera outage"))
    try:
        poller._poll_camera_row(cam)
    except ConnectionError:
        pass

    assert face_db.get_poll_checkpoint(cam["id"]) is None
    assert cam["id"] not in poller._last_polled_until
