"""Per-camera cache of recently-recognized face identities, keyed by
bounding-box continuity (IoU) across consecutive detect cycles — lets
detection_worker.py skip the expensive full-resolution recheck pass
(_lean_recognize_crop) for a face that's already been confidently
identified, or that was just tried and failed recently, instead of paying
for it fresh every single cycle regardless of whether anything changed.
This is the actual latency fix: the cheap whole-frame pass still runs
every cycle (fire/smoke exclusion, footfall re-identification, desk/gate
tracking all need every face's bbox+embedding regardless of identity) —
only the recheck crop is ever skipped.

Lives in the worker process (one instance per camera, held in run_worker's
per-camera dict, same pattern as fire_smoke_trackers/trackers) because the
expensive work it's gating happens here, not in the main process where the
purely-cosmetic recognition_stabilizer.py runs (that one only smooths
already-computed results for display; it cannot skip computing them).

Deliberately its own timeout (RECOGNITION_CACHE_TRACK_TIMEOUT_SECONDS),
shorter than recognition_stabilizer's RECOGNITION_TRACK_TIMEOUT_SECONDS
(180s) even though both are IoU-based face tracking: this cache's cached
identity feeds straight into pipeline.py's footfall/desk/zone-violation
logic too (see PipelineManager._dispatch_result, which consumes the same
result["faces"] this produces), not just a cosmetic overlay label — a
stale wrong identity surviving 180s there risks a real desk-session or
zone-alert misattribution, not just a mislabeled video frame.
"""

from __future__ import annotations

from . import config


def _iou(a: list[float], b: list[float]) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    if inter == 0:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


class _Track:
    __slots__ = ("bbox", "name", "score", "confirmed_at", "last_attempt_at", "last_seen_at")

    def __init__(self, bbox: list[float], now: float):
        self.bbox = bbox
        self.name = "Unknown"
        self.score = 0.0
        self.confirmed_at: float | None = None  # last time this track's identity was confidently (re)confirmed
        self.last_attempt_at: float | None = None  # last time ANY recognition attempt (cheap-confident or recheck) updated this track
        self.last_seen_at = now


class RecognitionTrackCache:
    """One instance per camera (see detection_worker.run_worker)."""

    def __init__(self):
        self._tracks: list[_Track] = []

    def _prune(self, now: float) -> None:
        timeout = config.RECOGNITION_CACHE_TRACK_TIMEOUT_SECONDS
        self._tracks = [t for t in self._tracks if now - t.last_seen_at <= timeout]

    def match(self, bbox: list[float], now: float) -> _Track:
        """Best-IoU-overlapping existing track for this bbox, or a fresh
        one if nothing matches closely enough — a brand-new track always
        reports name="Unknown", so should_recheck() naturally returns True
        for it on the caller's very next check."""
        self._prune(now)
        best, best_iou = None, config.RECOGNITION_TRACK_IOU_THRESHOLD
        for t in self._tracks:
            iou = _iou(t.bbox, bbox)
            if iou >= best_iou:
                best, best_iou = t, iou
        if best is None:
            best = _Track(bbox, now)
            self._tracks.append(best)
        else:
            best.bbox = bbox
            best.last_seen_at = now
        return best

    def should_recheck(self, track: _Track, now: float) -> bool:
        """Only meaningful when the cheap pass already reported "Unknown"
        for this face (caller's existing guard is unchanged) — decides
        whether this cycle should spend one of the limited, expensive
        recheck slots on it, or trust/wait instead."""
        if (
            track.name != "Unknown" and track.name
            and track.confirmed_at is not None
            and (now - track.confirmed_at) < config.RECOGNITION_CACHE_REVERIFY_SECONDS
        ):
            return False  # confidently identified recently enough to trust without spending a recheck
        if track.last_attempt_at is not None and (now - track.last_attempt_at) < config.RECOGNITION_RETRY_INTERVAL_SECONDS:
            return False  # already tried very recently (confirmed or not) — don't hammer every cycle
        return True

    def record(self, track: _Track, name: str, score: float, now: float) -> None:
        """Call whenever a recognition attempt actually ran this cycle
        (cheap-confident match OR a recheck), whatever its outcome."""
        track.last_attempt_at = now
        if name != "Unknown":
            track.name, track.score, track.confirmed_at = name, score, now
        # else: leave any existing cached identity alone — a single
        # transient miss on an already-confirmed track shouldn't erase it.

    def invalidate_all(self) -> None:
        """Called on reload_faces — the enrolled roster changed (rename/
        add/remove), so any cached identity could now be wrong."""
        self._tracks.clear()
