"""Unit tests for recognition_track_cache.py — deterministic, synthetic bbox
sequences, no camera/model dependency. Covers: same-track persistence
(recheck skipped), a new track (recheck runs), a confident track surviving
past its reverify window (one recheck spent, then trusted again), an
unresolved track's retry throttle, track expiry, and invalidate_all()."""

from app import config
from app.recognition_track_cache import RecognitionTrackCache

BBOX_A = [100, 100, 200, 200]
BBOX_A_SHIFTED = [102, 101, 202, 201]  # same physical face, tiny frame-to-frame jitter
BBOX_B = [500, 500, 600, 600]  # a totally different location — a different face


def test_new_face_always_needs_a_recheck(monkeypatch):
    cache = RecognitionTrackCache()
    track = cache.match(BBOX_A, now=0.0)
    assert cache.should_recheck(track, now=0.0) is True


def test_confident_track_skips_recheck_within_reverify_window(monkeypatch):
    monkeypatch.setattr(config, "RECOGNITION_CACHE_REVERIFY_SECONDS", 10.0)
    cache = RecognitionTrackCache()
    track = cache.match(BBOX_A, now=0.0)
    cache.record(track, "Kanishka", 0.55, now=0.0)

    track_again = cache.match(BBOX_A_SHIFTED, now=2.0)
    assert track_again is track  # IoU match found the same track
    assert cache.should_recheck(track_again, now=2.0) is False


def test_confident_track_reverifies_after_window_elapses(monkeypatch):
    monkeypatch.setattr(config, "RECOGNITION_CACHE_REVERIFY_SECONDS", 10.0)
    monkeypatch.setattr(config, "RECOGNITION_RETRY_INTERVAL_SECONDS", 3.0)
    cache = RecognitionTrackCache()
    track = cache.match(BBOX_A, now=0.0)
    cache.record(track, "Kanishka", 0.55, now=0.0)

    track_later = cache.match(BBOX_A, now=15.0)  # past the 10s reverify window
    assert cache.should_recheck(track_later, now=15.0) is True


def test_unresolved_track_is_throttled_not_retried_every_cycle(monkeypatch):
    monkeypatch.setattr(config, "RECOGNITION_RETRY_INTERVAL_SECONDS", 3.0)
    cache = RecognitionTrackCache()
    track = cache.match(BBOX_A, now=0.0)
    cache.record(track, "Unknown", 0.1, now=0.0)  # a recheck ran and still failed

    track_soon = cache.match(BBOX_A, now=1.0)  # well within the 3s retry throttle
    assert cache.should_recheck(track_soon, now=1.0) is False

    track_later = cache.match(BBOX_A, now=4.0)  # past the throttle
    assert cache.should_recheck(track_later, now=4.0) is True


def test_a_single_miss_does_not_erase_an_already_confirmed_identity(monkeypatch):
    monkeypatch.setattr(config, "RECOGNITION_CACHE_REVERIFY_SECONDS", 0.0)  # force immediate reverify eligibility
    monkeypatch.setattr(config, "RECOGNITION_RETRY_INTERVAL_SECONDS", 0.0)
    cache = RecognitionTrackCache()
    track = cache.match(BBOX_A, now=0.0)
    cache.record(track, "Kanishka", 0.55, now=0.0)

    cache.record(track, "Unknown", 0.1, now=1.0)  # a transient miss on the reverify recheck
    assert track.name == "Kanishka"  # untouched — graceful degradation, not erased


def test_different_bbox_location_is_a_different_track():
    cache = RecognitionTrackCache()
    track_a = cache.match(BBOX_A, now=0.0)
    cache.record(track_a, "Kanishka", 0.55, now=0.0)

    track_b = cache.match(BBOX_B, now=0.0)
    assert track_b is not track_a
    assert cache.should_recheck(track_b, now=0.0) is True


def test_track_expires_after_timeout(monkeypatch):
    monkeypatch.setattr(config, "RECOGNITION_CACHE_TRACK_TIMEOUT_SECONDS", 5.0)
    cache = RecognitionTrackCache()
    track = cache.match(BBOX_A, now=0.0)
    cache.record(track, "Kanishka", 0.55, now=0.0)

    # Same bbox reappears, but well past the expiry — must be treated as a
    # brand-new track (person left and someone/something else is here now),
    # not silently inherit the old identity.
    new_track = cache.match(BBOX_A, now=100.0)
    assert new_track is not track
    assert new_track.name == "Unknown"


def test_invalidate_all_clears_every_cached_identity():
    cache = RecognitionTrackCache()
    track = cache.match(BBOX_A, now=0.0)
    cache.record(track, "Kanishka", 0.55, now=0.0)

    cache.invalidate_all()

    fresh = cache.match(BBOX_A, now=0.1)
    assert fresh.name == "Unknown"
    assert cache.should_recheck(fresh, now=0.1) is True
