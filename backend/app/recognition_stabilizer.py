"""Frame-to-frame identity stabilization for a single camera's recognition
results.

Every detection cycle in this pipeline matches faces independently — nothing
carries identity between cycles (see pipeline.py's CameraPipeline, which
calls set_detections() with a fresh, unrelated list every time). That means
a single noisy frame (motion blur, a bad angle, a momentary lighting change)
can flip a confidently-recognized person to "Unknown" or, worse, to a
DIFFERENT registered name for one cycle, then flip back — the classic
"Rahul, Unknown, Amit, Rahul" flicker.

This module tracks faces across consecutive cycles purely by bounding-box
overlap (IoU) — cheap, and sufficient at this pipeline's sampling rate
(detection_fps default 1/sec; a stationary or slow-walking person's bbox
moves only modestly between samples). Each track keeps a short rolling
history of (name, score) votes and only reports a name once it has a clear
majority in that window — otherwise it reports "Unknown", never a low-
confidence guess. Once stable, a track only flips to a different name once
that new name earns its own majority, which is what prevents flicker.

This does NOT replace per-frame matching (recognizer.py's cosine-similarity
threshold) — a face still has to clear SIMILARITY_THRESHOLD to ever be a
candidate vote here. This is a second, independent layer on top: temporal
consistency, not confidence.
"""

import time

from . import config


def _iou(a: list[float], b: list[float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


class _Track:
    def __init__(self, bbox: list[float], now: float):
        self.bbox = bbox
        self.last_seen = now
        self.votes: list[str] = []  # names only ("Unknown" included); capped to window
        self.stable_name: str | None = None  # None until a majority forms

    def record(self, name: str, window: int) -> None:
        self.votes.append(name)
        if len(self.votes) > window:
            self.votes.pop(0)

    def resolve(self, min_votes: int) -> str:
        """Majority vote among non-Unknown names; requires min_votes of the
        SAME name to report it. Falls back to the previously-stable name if
        the window is currently ambiguous (no name has a majority yet) but
        that name is still present in the window at all — this is what
        keeps a person's name from dropping to Unknown on a single bad
        frame once they were already confidently identified. Only clears to
        Unknown once the stable name no longer appears in the window at
        all, and only switches to a NEW name once that name itself reaches
        min_votes."""
        counts: dict[str, int] = {}
        for n in self.votes:
            if n == "Unknown":
                continue
            counts[n] = counts.get(n, 0) + 1

        if counts:
            best_name, best_count = max(counts.items(), key=lambda kv: kv[1])
            if best_count >= min_votes:
                # a different name reaching min_votes overtakes the old stable
                # name; the same name reaching it again just reconfirms it
                self.stable_name = best_name

        if self.stable_name is not None:
            if self.stable_name in self.votes:
                return self.stable_name
            # the stable identity hasn't appeared anywhere in the recent
            # window any more (person likely left, or was genuinely
            # misidentified earlier) -- stop reporting it rather than
            # holding on to it forever
            self.stable_name = None
        return "Unknown"


class RecognitionStabilizer:
    """One instance per camera (see pipeline.py's CameraPipeline)."""

    def __init__(
        self,
        window: int = config.RECOGNITION_STABILIZATION_WINDOW,
        min_votes: int = config.RECOGNITION_STABILIZATION_MIN_VOTES,
        iou_threshold: float = config.RECOGNITION_TRACK_IOU_THRESHOLD,
        track_timeout: float = config.RECOGNITION_TRACK_TIMEOUT_SECONDS,
    ):
        self._window = window
        self._min_votes = min_votes
        self._iou_threshold = iou_threshold
        self._track_timeout = track_timeout
        self._tracks: list[_Track] = []

    def stabilize(self, faces: list[dict]) -> list[dict]:
        """Mutates and returns `faces` with each face's "name" replaced by
        its track's temporally-stabilized identity. The raw per-frame name/
        score this cycle actually matched is kept under "raw_name"/"score"
        (score already present) so nothing downstream that wants the
        instantaneous match loses it -- only the displayed/logged "name"
        changes."""
        now = time.time()
        self._tracks = [t for t in self._tracks if now - t.last_seen <= self._track_timeout]

        claimed: set[int] = set()
        for face in faces:
            raw_name = face["name"]
            best_track, best_iou = None, self._iou_threshold
            for i, track in enumerate(self._tracks):
                if i in claimed:
                    continue
                iou = _iou(face["bbox"], track.bbox)
                if iou > best_iou:
                    best_track, best_iou = track, iou
            if best_track is None:
                best_track = _Track(face["bbox"], now)
                self._tracks.append(best_track)
            else:
                claimed.add(self._tracks.index(best_track))
                best_track.bbox = face["bbox"]
                best_track.last_seen = now

            best_track.record(raw_name, self._window)
            face["raw_name"] = raw_name
            face["name"] = best_track.resolve(self._min_votes)

        return faces
