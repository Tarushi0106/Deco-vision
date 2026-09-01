"""Classical, model-free fire/smoke detection: HSV color rules combined
with temporal signals (frame-to-frame flicker for fire, sustained area
growth vs. a running background for smoke) — there's no fire/smoke-labeled
model bundled with this project (unlike yolov8n-pose.pt for people), so
this is a heuristic, not a trained detector. It's cheap enough (no NN, just
color-space math on a downscaled frame) to run every detect cycle rather
than being gated to detection_worker.POSE_INTERVAL_SECONDS like pose
detection.

Confirmed live on real office-camera footage (see cam1-3 snapshots pulled
2026-08-31): the original color-plus-single-frame-motion smoke rule flagged
plain grey/white walls, frosted-glass partitions, and glossy floor tiles as
"smoke" — those surfaces are large, low-saturation, mid-brightness areas
just like real smoke, and ordinary camera sensor/compression noise on them
was enough to pass a per-frame motion check. A static wall's noisy mask
area jitters around a fixed baseline; a real smoke plume actually expands
over time. Requiring that growth (see SMOKE_GROWTH_RATIO below) is what
tells the two apart — color+single-frame-motion alone can't, the same way
fire's flicker check (not color alone) is what rejects a static red/orange
decor object.

These thresholds are still starting points, not exhaustively tuned against
a real fire/smoke event — expect to revisit the FIRE_*/SMOKE_* constants
below per camera once more real footage is available, the same way
detection_worker.CAMERA_DET_THRESH was tuned per camera for face
recognition.
"""

import cv2
import numpy as np

DOWNSCALE_MAX_DIM = 480  # cheap enough to run every cycle; color/motion masks don't need full res

# --- fire: HSV color rule + flicker (variance of masked area over time) ---
FIRE_HUE_RANGE = (0, 35)  # red -> orange -> yellow, OpenCV's 0-179 hue scale
FIRE_MIN_SATURATION = 90  # vivid, not a washed-out/pastel color
FIRE_MIN_VALUE = 180  # bright/glowing, not a dim red object
FIRE_MIN_AREA_FRACTION = 0.0015  # ignore tiny specks (LEDs, reflections) relative to frame area
FIRE_FLICKER_HISTORY = 5  # samples of masked-area used to judge flicker
# Real flame area jitters noticeably frame to frame; a static red/orange
# object (a curtain, a chair) holds a near-constant masked area instead.
# Requiring the coefficient of variation (std/mean) of recent masked-area
# samples to clear this bar is what tells the two apart — color alone can't.
FIRE_FLICKER_MIN_CV = 0.15
FIRE_CONSECUTIVE_SAMPLES = 3  # require the signal to hold across several samples before alerting (mirrors person_tracker.FALL_CONSECUTIVE_SAMPLES)

# --- smoke: achromatic (low-saturation) haze + motion vs. a running background,
# confirmed only once the masked area actually grows over time ---
SMOKE_MAX_SATURATION = 35  # grey/hazy, not a colored object — tightened after the wall/glass/floor false positives above
SMOKE_MIN_VALUE, SMOKE_MAX_VALUE = 90, 210  # excludes near-black shadow and blown-out glare/reflections (glossy floors, glass)
# Low enough to still catch a small, real, early-stage source (e.g.
# cigarette smoke) rather than only a room-filling cloud — the false
# -positive fighting is now carried by the other signals below (fill ratio,
# growth trend, spatial overlap, face exclusion), not by a large area floor.
SMOKE_MIN_AREA_FRACTION = 0.006
# A single contour bigger than this is far more likely a whole-frame change
# (day/night IR-cut switch, AC/lighting flip) than an actual smoke plume —
# discarded outright rather than counted (see _boxes_from_mask's max_area_fraction).
SMOKE_MAX_AREA_FRACTION = 0.5
SMOKE_BG_ALPHA = 0.02  # running-average background learning rate (cv2.accumulateWeighted)
# Confirmed live: raising this to 30 to shrug off wall/floor compression
# noise (see the false positives documented above) also meant a realistic,
# semi-transparent early smoke source (measured: a synthetic wispy plume
# with real cigarette-like partial opacity only shifts grayscale by ~25)
# never registered as motion at all. Lowered back down for sensitivity —
# the fill-ratio/growth/overlap/face-exclusion checks below now do the
# false-positive rejection that this threshold used to carry alone.
SMOKE_MOTION_THRESHOLD = 15
SMOKE_TREND_HISTORY = 10  # samples of masked-area used to judge growth (split into first/second half, see below)
# Second-half average masked area must be at least this many times the
# first-half average (or the plume must have appeared from ~nothing) before
# the signal counts as "actually growing" rather than just "present".
SMOKE_GROWTH_RATIO = 1.5
# Confirmed live: a person walking straight TOWARD the camera (an entry/exit
# doorway's whole reason to exist) is solid, can wear pale/low-saturation
# clothing, and genuinely grows in apparent size in place — passing the
# fill-ratio, growth, and spatial-overlap checks below just like real smoke
# would. What's different: a person crosses the frame in a few seconds and
# then is gone (exits frame, gets face-detected, or gets too large and is
# discarded above SMOKE_MAX_AREA_FRACTION); real smoke keeps growing for as
# long as its source keeps burning. Requiring a LONGER sustained-growth
# window (SMOKE_TREND_HISTORY above, effectively ~10s at 1 sample/sec) lets
# a brief walk-through's confirmation run out before it ever reaches this
# bar, while persisting real smoke still clears it, just a bit slower.
SMOKE_CONSECUTIVE_SAMPLES = 4  # require the (already growth-confirmed) signal to hold across several more samples before alerting
# Confirmed live: a person shifting posture at a desk (real motion) against
# their own low-saturation clothing/desk/monitor surroundings can satisfy the
# growth check above too — over a several-second window, ordinary fidgeting
# reads as a "growing" low-saturation blob just like an expanding haze does.
# Two independent fixes: (1) never let the padded area around an already
# -detected face count (see exclude_boxes in update() — the pipeline already
# knows where people are, every detect cycle); (2) require the candidate
# region to be a reasonably SOLID blob. A real diffuse smoke cloud, once
# dilated, fills most of its own bounding box; a person's fragmented motion
# (head moves, hand moves, chair creaks) dilates into disjoint patches that
# a bounding box spans loosely, with much emptier space in between.
SMOKE_MIN_FILL_RATIO = 0.55  # contour area / bounding-box area
# A person WALKING (not just fidgeting in place) is solid, can be in light/
# low-saturation clothing, and genuinely grows in apparent size as they
# approach the camera — passing both the fill-ratio and growth checks above.
# What real smoke does differently: it grows roughly where it started,
# spreading from its source; a walking person's position several seconds
# later barely overlaps where they were standing before. Requiring the
# region's position, not just its area, to overlap itself over time is what
# tells "expanding in place" apart from "moving across the frame".
SMOKE_MIN_SPATIAL_OVERLAP = 0.2  # IoU between the first-half and second-half combined footprint
# Confirmed live (2026-08-31, camera "Main gate camera"): an active laptop
# screen on a desk passed every check above — real per-frame content change
# counts as motion, it doesn't move, and text/UI elements can be pale enough
# that ~59% of the flagged box's pixels individually cleared the per-pixel
# saturation test. What gave it away: unlike a real haze (which stays
# uniformly greyish through dilation), the box's OVERALL average saturation
# (mean 65, pulled up by the screen's own colored UI + nearby desk clutter
# caught by dilation) was well above what a genuine smoke region measured
# even with those same false positives folded in. Not proven against real
# smoke footage (none was available to test against) — a best-effort
# addition based on the one real case found, not a guarantee.
SMOKE_MAX_REGION_MEAN_SATURATION = 45
# How far around a detected face box to exclude from fire/smoke consideration
# — a face box is head-only, so this pads out to roughly cover the seated
# body + desk area below/around it. Generous and unscientific (no real
# measurement backs these multipliers), but directly targets the observed
# failure mode above; a real fire/smoke event actually AT a person's desk
# would be suppressed too — an accepted trade-off given how often this
# false-fired otherwise on a normal, people-filled office camera.
FACE_EXCLUDE_SIDE_PAD = 1.5  # x bbox width, each side
FACE_EXCLUDE_UP_PAD = 1.0  # x bbox height, above
FACE_EXCLUDE_DOWN_PAD = 7.0  # x bbox height, below


def _resize(frame):
    h, w = frame.shape[:2]
    scale = min(1.0, DOWNSCALE_MAX_DIM / max(h, w))
    if scale >= 1.0:
        return frame, 1.0
    return cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA), scale


def _exclusion_mask(shape, exclude_boxes, scale):
    mask = np.zeros(shape, dtype=np.uint8)
    if not exclude_boxes:
        return mask
    h, w = shape
    for x1, y1, x2, y2 in exclude_boxes:
        bw, bh = x2 - x1, y2 - y1
        ex1 = int((x1 - bw * FACE_EXCLUDE_SIDE_PAD) * scale)
        ex2 = int((x2 + bw * FACE_EXCLUDE_SIDE_PAD) * scale)
        ey1 = int((y1 - bh * FACE_EXCLUDE_UP_PAD) * scale)
        ey2 = int((y2 + bh * FACE_EXCLUDE_DOWN_PAD) * scale)
        ex1, ey1 = max(0, ex1), max(0, ey1)
        ex2, ey2 = min(w, ex2), min(h, ey2)
        if ex2 > ex1 and ey2 > ey1:
            mask[ey1:ey2, ex1:ex2] = 255
    return mask


def _boxes_from_mask(
    mask, min_area_fraction, frame_area, max_area_fraction=None, min_fill_ratio=None,
    sat_channel=None, max_mean_saturation=None,
):
    min_area = frame_area * min_area_fraction
    max_area = frame_area * max_area_fraction if max_area_fraction is not None else None
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < min_area:
            continue
        if max_area is not None and area > max_area:
            continue
        x, y, w, h = cv2.boundingRect(c)
        if min_fill_ratio is not None and area / (w * h) < min_fill_ratio:
            continue  # a real diffuse cloud is solid; fragmented motion (e.g. a person fidgeting) isn't
        if sat_channel is not None and max_mean_saturation is not None:
            if sat_channel[y:y + h, x:x + w].mean() > max_mean_saturation:
                continue  # e.g. a screen/UI's own color content mixed in via dilation — real haze stays uniformly grey
        boxes.append(([x, y, x + w, y + h], area))
    return boxes


def _union_bbox(bboxes: list[list[int]]) -> list[int] | None:
    if not bboxes:
        return None
    xs1 = [b[0] for b in bboxes]
    ys1 = [b[1] for b in bboxes]
    xs2 = [b[2] for b in bboxes]
    ys2 = [b[3] for b in bboxes]
    return [min(xs1), min(ys1), max(xs2), max(ys2)]


def _iou(a: list[int], b: list[int]) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    if inter == 0:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


class FireSmokeTracker:
    """One instance per camera (see detection_worker.run_worker) — holds the
    running background model (smoke's growth signal) and recent masked-area
    history (fire's flicker signal, smoke's growth trend) plus
    consecutive-sample streaks, so a single confirmed frame can't raise an
    alert on its own. Overlay boxes are only emitted once each type's own
    temporal signal (flicker for fire, growth for smoke) is confirmed for
    the current frame — not for every raw color-mask match — so a
    not-yet-confirmed candidate never shows a misleading label on screen.

    `events` keeps firing every cycle for as long as the condition stays
    confirmed (no one-shot latch) — a real, ongoing fire/smoke event should
    keep demanding attention, not go silent after a single alert while
    nobody's looked at the dashboard. pipeline.py's own per-camera cooldown
    (FIRE_SMOKE_ALERT_COOLDOWN_SECONDS) is what turns this into a periodic
    re-alert rather than a flood of duplicate DB rows every second."""

    def __init__(self):
        self._background: np.ndarray | None = None  # float32 grayscale running average
        self._fire_area_history: list[float] = []
        self._fire_streak = 0
        self._smoke_area_history: list[float] = []
        self._smoke_bbox_history: list[list[int] | None] = []
        self._smoke_streak = 0

    def update(self, frame_bgr, exclude_boxes: list | None = None) -> dict:
        """Returns {"boxes": [{"bbox": [x1,y1,x2,y2], "type": "fire"/"smoke",
        "score": float}, ...], "events": ["fire", "smoke", ...]}. `boxes`
        only includes a type once its own frame-level signal is confirmed
        (see class docstring); `events` only fires once that confirmed
        signal has held for several consecutive samples (for alerting).

        exclude_boxes: [x1,y1,x2,y2] boxes (original frame coordinates,
        e.g. this camera's face detections from the same detect cycle) that
        never count as fire/smoke — see FACE_EXCLUDE_*_PAD above."""
        small, scale = _resize(frame_bgr)
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        hue, sat, val = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
        frame_area = small.shape[0] * small.shape[1]
        exclude_mask = _exclusion_mask((small.shape[0], small.shape[1]), exclude_boxes, scale)

        boxes: list[dict] = []
        events: list[str] = []

        # --- fire ---
        fire_mask = (
            (hue >= FIRE_HUE_RANGE[0]) & (hue <= FIRE_HUE_RANGE[1])
            & (sat >= FIRE_MIN_SATURATION) & (val >= FIRE_MIN_VALUE)
        ).astype(np.uint8) * 255
        fire_mask[exclude_mask > 0] = 0
        fire_mask = cv2.dilate(fire_mask, np.ones((5, 5), np.uint8))
        fire_boxes = _boxes_from_mask(fire_mask, FIRE_MIN_AREA_FRACTION, frame_area)
        fire_area_now = sum(area for _, area in fire_boxes)

        self._fire_area_history.append(fire_area_now)
        if len(self._fire_area_history) > FIRE_FLICKER_HISTORY:
            self._fire_area_history.pop(0)

        fire_present = False
        if fire_boxes and len(self._fire_area_history) >= FIRE_FLICKER_HISTORY:
            mean = sum(self._fire_area_history) / len(self._fire_area_history)
            if mean > 0:
                variance = sum((a - mean) ** 2 for a in self._fire_area_history) / len(self._fire_area_history)
                fire_present = (variance ** 0.5) / mean >= FIRE_FLICKER_MIN_CV

        if fire_present:
            for bbox, area in fire_boxes:
                boxes.append({
                    "bbox": [round(v / scale) for v in bbox],
                    "type": "fire",
                    "score": round(min(1.0, area / frame_area * 20), 3),
                })
            self._fire_streak += 1
            if self._fire_streak >= FIRE_CONSECUTIVE_SAMPLES:
                events.append("fire")
        else:
            self._fire_streak = 0

        # --- smoke ---
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)
        if self._background is None or self._background.shape != gray.shape:
            self._background = gray.copy()  # first frame for this camera, or its resolution changed
        motion = cv2.absdiff(gray, self._background) > SMOKE_MOTION_THRESHOLD
        cv2.accumulateWeighted(gray, self._background, SMOKE_BG_ALPHA)

        smoke_color = (sat <= SMOKE_MAX_SATURATION) & (val >= SMOKE_MIN_VALUE) & (val <= SMOKE_MAX_VALUE)
        smoke_mask = (smoke_color & motion).astype(np.uint8) * 255
        smoke_mask[exclude_mask > 0] = 0
        smoke_mask = cv2.dilate(smoke_mask, np.ones((7, 7), np.uint8))
        smoke_boxes = _boxes_from_mask(
            smoke_mask, SMOKE_MIN_AREA_FRACTION, frame_area,
            max_area_fraction=SMOKE_MAX_AREA_FRACTION, min_fill_ratio=SMOKE_MIN_FILL_RATIO,
            sat_channel=sat, max_mean_saturation=SMOKE_MAX_REGION_MEAN_SATURATION,
        )
        smoke_area_now = sum(area for _, area in smoke_boxes)

        self._smoke_area_history.append(smoke_area_now)
        if len(self._smoke_area_history) > SMOKE_TREND_HISTORY:
            self._smoke_area_history.pop(0)
        self._smoke_bbox_history.append(_union_bbox([b for b, _ in smoke_boxes]))
        if len(self._smoke_bbox_history) > SMOKE_TREND_HISTORY:
            self._smoke_bbox_history.pop(0)

        smoke_present = False
        if smoke_boxes and len(self._smoke_area_history) >= SMOKE_TREND_HISTORY:
            half = SMOKE_TREND_HISTORY // 2
            first_mean = sum(self._smoke_area_history[:half]) / half
            second_mean = sum(self._smoke_area_history[half:]) / (SMOKE_TREND_HISTORY - half)
            # first_mean ~0 means it appeared from nothing over this window —
            # the classic real-smoke signature — which counts as growth too.
            growing = second_mean > 0 and (
                first_mean <= 1e-6 or second_mean >= first_mean * SMOKE_GROWTH_RATIO
            )

            first_union = _union_bbox([b for b in self._smoke_bbox_history[:half] if b is not None])
            second_union = _union_bbox([b for b in self._smoke_bbox_history[half:] if b is not None])
            # Can't judge "moved vs. grew in place" without a footprint to
            # compare against (e.g. the appeared-from-nothing case) — don't
            # let a missing first-half footprint block a real detection.
            stayed_in_place = first_union is None or (
                second_union is not None and _iou(first_union, second_union) >= SMOKE_MIN_SPATIAL_OVERLAP
            )

            smoke_present = growing and stayed_in_place

        if smoke_present:
            for bbox, area in smoke_boxes:
                boxes.append({
                    "bbox": [round(v / scale) for v in bbox],
                    "type": "smoke",
                    "score": round(min(1.0, area / frame_area * 5), 3),
                })
            self._smoke_streak += 1
            if self._smoke_streak >= SMOKE_CONSECUTIVE_SAMPLES:
                events.append("smoke")
        else:
            self._smoke_streak = 0

        return {"boxes": boxes, "events": events}
