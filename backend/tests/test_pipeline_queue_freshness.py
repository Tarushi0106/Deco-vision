"""Targeted test for this session's frame-queue freshness fix (task section
5): under sustained load (arrival rate > drain rate, simulating a worker
that's behind on CPU-only inference), the queue must end up holding the
NEWEST frames, never grow past its bound, and never silently keep serving an
ever-staler backlog by dropping incoming fresh frames instead of stale ones.

Uses a real queue.Queue (not a mock) against the actual enqueue_latest_frame
function pipeline.py's _sender_loop calls — this is the same function, not a
reimplementation of its logic, so a bug here is a real bug in production.
"""

import queue

from app.pipeline import enqueue_latest_frame


def test_queue_never_exceeds_maxsize_under_sustained_overload():
    q = queue.Queue(maxsize=2)
    for i in range(50):  # arrival rate far exceeds any plausible drain rate
        enqueue_latest_frame(q, {"type": "detect", "camera_id": 2, "seq": i})
    assert q.qsize() <= 2


def test_queue_holds_the_newest_frames_not_the_oldest():
    q = queue.Queue(maxsize=2)
    for i in range(50):
        enqueue_latest_frame(q, {"type": "detect", "camera_id": 2, "seq": i})
    remaining_seqs = sorted(item["seq"] for item in list(q.queue))
    # the two survivors must be the two most recently enqueued, not two
    # arbitrary/old ones from earlier in the burst
    assert remaining_seqs == [48, 49]


def test_full_queue_drops_oldest_and_admits_newest():
    q = queue.Queue(maxsize=1)
    enqueue_latest_frame(q, {"type": "detect", "seq": 1})
    sent, dropped_stale = enqueue_latest_frame(q, {"type": "detect", "seq": 2})
    assert sent is True
    assert dropped_stale is True
    assert q.get_nowait()["seq"] == 2  # the older seq=1 frame is gone, not seq=2


def test_control_message_is_preserved_not_dropped_as_stale():
    """A pending "reload_faces" message (roster changed) must survive a
    frame-drop cycle — losing it would leave the worker's recognizer using a
    stale enrolled-faces roster until the next lucky timing window."""
    q = queue.Queue(maxsize=1)
    q.put_nowait({"type": "reload_faces"})
    sent, dropped_stale = enqueue_latest_frame(q, {"type": "detect", "seq": 1})
    assert dropped_stale is False  # nothing "detect" was evicted
    # both must now be somewhere reachable: the control message was put back,
    # and the new frame could not fit this cycle (queue is maxsize=1) — that's
    # fine, it'll be retried next tick by the real sender loop.
    assert sent is False
    remaining = q.get_nowait()
    assert remaining["type"] == "reload_faces"


def test_underloaded_queue_never_drops_anything():
    q = queue.Queue(maxsize=10)
    for i in range(5):
        sent, dropped_stale = enqueue_latest_frame(q, {"type": "detect", "seq": i})
        assert sent is True
        assert dropped_stale is False
    assert q.qsize() == 5
