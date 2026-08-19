"""Per-camera person tracker: greedy centroid matching across pose-detection
samples, powering both footfall (midline crossing) and fall detection
(sustained horizontal torso orientation) from the same PoseDetector output.
One instance per camera, since track IDs and positions are meaningless
across different camera views.
"""

import math
import time

MAX_MATCH_DISTANCE = 80  # pixels between samples — tune if footfall misses fast walkers
TRACK_TIMEOUT_SECONDS = 2.0
FALL_CONSECUTIVE_SAMPLES = 3  # ~4.5s at the 1.5s pose-detection cadence
FALL_ANGLE_THRESHOLD_DEGREES = 60  # torso more horizontal than this = "down"
KEYPOINT_CONF_THRESHOLD = 0.5

# COCO-pose keypoint indices
LEFT_SHOULDER, RIGHT_SHOULDER = 5, 6
LEFT_HIP, RIGHT_HIP = 11, 12


class PersonTracker:
    def __init__(self):
        self._next_track_id = 0
        self._tracks: dict[int, dict] = {}

    @staticmethod
    def _centroid(bbox):
        x1, y1, x2, y2 = bbox
        return ((x1 + x2) / 2, (y1 + y2) / 2)

    @staticmethod
    def _side(cy, frame_height):
        return "top" if cy < frame_height / 2 else "bottom"

    @staticmethod
    def _is_down(bbox, keypoints) -> bool:
        if keypoints is not None:
            ls, rs = keypoints[LEFT_SHOULDER], keypoints[RIGHT_SHOULDER]
            lh, rh = keypoints[LEFT_HIP], keypoints[RIGHT_HIP]
            if ls[2] > KEYPOINT_CONF_THRESHOLD and rs[2] > KEYPOINT_CONF_THRESHOLD \
                    and lh[2] > KEYPOINT_CONF_THRESHOLD and rh[2] > KEYPOINT_CONF_THRESHOLD:
                shoulder_mid = ((ls[0] + rs[0]) / 2, (ls[1] + rs[1]) / 2)
                hip_mid = ((lh[0] + rh[0]) / 2, (lh[1] + rh[1]) / 2)
                dx = hip_mid[0] - shoulder_mid[0]
                dy = hip_mid[1] - shoulder_mid[1]
                angle = math.degrees(math.atan2(abs(dx), abs(dy) + 1e-6))
                return angle > FALL_ANGLE_THRESHOLD_DEGREES
        # keypoints not confident enough (e.g. partial occlusion) — fall back
        # to a plain bbox aspect-ratio heuristic
        x1, y1, x2, y2 = bbox
        width, height = x2 - x1, y2 - y1
        return width > height * 1.3

    def update(self, people: list[dict], frame_height: int) -> dict:
        """people: PoseDetector.detect() output for one frame.
        Returns {"footfall_events": ["in"/"out", ...], "fall_events": [track_id, ...]}."""
        now = time.time()

        for tid in list(self._tracks):
            if now - self._tracks[tid]["last_seen"] > TRACK_TIMEOUT_SECONDS:
                del self._tracks[tid]

        unmatched_track_ids = set(self._tracks)
        footfall_events, fall_events = [], []

        for person in people:
            bbox = person["bbox"]
            cx, cy = self._centroid(bbox)
            is_down = self._is_down(bbox, person.get("keypoints"))

            best_id, best_dist = None, MAX_MATCH_DISTANCE
            for tid in unmatched_track_ids:
                tx, ty = self._tracks[tid]["centroid"]
                dist = math.hypot(cx - tx, cy - ty)
                if dist < best_dist:
                    best_id, best_dist = tid, dist

            new_side = self._side(cy, frame_height)

            if best_id is not None:
                track = self._tracks[best_id]
                if track["side"] != new_side:
                    footfall_events.append("in" if new_side == "bottom" else "out")
                track["centroid"] = (cx, cy)
                track["side"] = new_side
                track["last_seen"] = now

                if is_down:
                    track["down_streak"] += 1
                    if track["down_streak"] >= FALL_CONSECUTIVE_SAMPLES and not track["fall_alerted"]:
                        fall_events.append(best_id)
                        track["fall_alerted"] = True
                else:
                    track["down_streak"] = 0
                    track["fall_alerted"] = False

                unmatched_track_ids.discard(best_id)
            else:
                self._tracks[self._next_track_id] = {
                    "centroid": (cx, cy),
                    "side": new_side,
                    "last_seen": now,
                    "down_streak": 1 if is_down else 0,
                    "fall_alerted": False,
                }
                self._next_track_id += 1

        return {"footfall_events": footfall_events, "fall_events": fall_events}
