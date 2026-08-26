"""Footfall gate-line crossing detection — makes "someone actually walked
through the gate" the trigger for counting footfall, instead of "a face was
recognized anywhere in the frame" (the previous behavior). This matters
because face recognition alone is unreliable for a meaningful slice of
people (thin/low-quality enrollment — see the investigation in this
session's attendance/desk-analytics work): someone can walk right through
the gate and simply never get a confident name match, and the old
whole-frame approach would silently undercount them along with everyone
else who happened not to be recognized on a given frame.

Deliberately NOT built on person_tracker.py's existing pose-based midline
crossing, even though the concept (track a moving point, detect which side
of a line it's on, count on-flip) is the same: pose sampling runs on its
own, much slower cadence (detection_worker.POSE_INTERVAL_SECONDS, 20s) to
keep the shared worker's CPU budget in check — measured live this session,
that cadence caught zero real gate crossings all day (a walk-through takes
1-3 seconds, well under the sampling gap). This tracker runs on the face
detection stream instead, which already samples every ~1s via
detection_fps — same crossing-detection idea, no new model call, and fast
enough to actually catch a crossing.

A crossing event feeds straight into footfall_counter.process() with
whatever name (or None/"Unknown") recognition managed for that specific
face — the crossing is what counts someone, the name is just attached
best-effort, exactly the same "prefer a name, fall back to anonymous"
convention footfall_counter.py already uses.
"""

import logging
import math
import time

logger = logging.getLogger("dashboard.gate")

# How far (as a fraction of frame width/height) a face can move between two
# ~1s samples (detection_fps) and still be considered the same person
# walking through. Calibrated generously on purpose: a doorway/gate is
# usually framed as only a portion of the full camera view, so a person
# walking briskly (~1.2-1.5 m/s) can easily cover 15-25% of the FRAME width
# in one second even though they've only crossed a couple of doorway-widths.
# Too tight here reproduces exactly the failure this tracker exists to fix
# — person_tracker.py's own MAX_MATCH_DISTANCE (80px) was already flagged
# as "tune if footfall misses fast walkers" at a 20s sampling gap, and this
# tracker samples far more often, so it can afford to be generous per step
# without needing anywhere near that much slack.
MATCH_MAX_DISTANCE_FRACTION = 0.25
# How long a track survives with no matching detection before it's dropped
# — a walk-through resolves in a few seconds; no need to hold state longer
# and risk falsely bridging a later, unrelated face to a stale track.
TRACK_TIMEOUT_SECONDS = 4.0


class GateTracker:
    def __init__(self, gate_db_module):
        self._gate_db = gate_db_module
        self._gates_by_camera: dict[int, dict] = {}
        # camera_id -> track_id -> {"cx","cy","side","last_seen"}
        self._tracks_by_camera: dict[int, dict[int, dict]] = {}
        self._next_track_id = 0
        self.refresh_gates()

    def refresh_gates(self) -> None:
        gates = self._gate_db.list_gates()
        self._gates_by_camera = {g["camera_id"]: g for g in gates}
        live_cameras = set(self._gates_by_camera)
        for camera_id in list(self._tracks_by_camera):
            if camera_id not in live_cameras:
                del self._tracks_by_camera[camera_id]

    @staticmethod
    def _side_sign(gate: dict, cx: float, cy: float) -> int:
        """Which side of the gate line (cx, cy) falls on, as the sign of
        the 2D cross product of the line's direction vector against the
        vector from the line's start point to (cx, cy). 0 means exactly on
        the line (treated as "no reading yet" — geometrically near-
        impossible for a real detection, but keeps the crossing check from
        firing on a degenerate zero)."""
        dx, dy = gate["x2"] - gate["x1"], gate["y2"] - gate["y1"]
        cross = dx * (cy - gate["y1"]) - dy * (cx - gate["x1"])
        return 1 if cross > 0 else (-1 if cross < 0 else 0)

    def process_frame(self, camera_id: int, faces: list[dict], frame_width: int, frame_height: int,
                       footfall_counter, now: float | None = None) -> bool:
        """Returns True if a gate line is configured for this camera (i.e.
        gate-crossing mode is active) — the caller uses this to decide
        whether to fall back to the old whole-frame behavior for a
        footfall-enabled camera that doesn't have a line drawn yet."""
        gate = self._gates_by_camera.get(camera_id)
        if gate is None:
            return False
        if not frame_width or not frame_height:
            return True
        now = now if now is not None else time.time()

        tracks = self._tracks_by_camera.setdefault(camera_id, {})
        for tid in [t for t, tr in tracks.items() if now - tr["last_seen"] > TRACK_TIMEOUT_SECONDS]:
            del tracks[tid]

        for face in faces:
            embedding = face.get("embedding")
            if embedding is None:
                continue
            x1, y1, x2, y2 = face["bbox"]
            cx, cy = (x1 + x2) / 2 / frame_width, (y1 + y2) / 2 / frame_height
            side = self._side_sign(gate, cx, cy)
            name = face.get("name")
            if side == 0:
                continue

            # Identity mismatch disqualifies a track: two confidently-named
            # detections that DISAGREE can never be the same physical person,
            # regardless of how close they are in the frame. Without this, a
            # multi-person crossing (very much the normal case at an office
            # entrance) can "steal" a nearby track from a different, already-
            # identified person mid-walk and misattribute their crossing —
            # observed directly in this camera's own logs: a track walking
            # in as "Shams" got its final sample matched to a detection
            # recognized as "Ankit (Office Boy)", so the crossing was logged
            # under the wrong name. Anonymous ("Unknown"/None) detections on
            # either side still match purely on position, same as before.
            best_tid, best_dist = None, MATCH_MAX_DISTANCE_FRACTION
            for tid, tr in tracks.items():
                if name and tr["name"] and name != tr["name"]:
                    continue
                dist = math.hypot(cx - tr["cx"], cy - tr["cy"])
                if dist < best_dist:
                    best_tid, best_dist = tid, dist

            if best_tid is not None:
                track = tracks[best_tid]
                if track["side"] and track["side"] != side and side == gate["entry_sign"]:
                    footfall_counter.process(camera_id, embedding, name=name, now=now)
                    logger.info(
                        "Gate: crossing detected on camera %s (%s)",
                        camera_id, name or "unrecognized",
                    )
                track["cx"], track["cy"], track["side"], track["last_seen"] = cx, cy, side, now
                # A confident name strengthens an until-now-anonymous track;
                # never downgrade a named track back to anonymous.
                if name:
                    track["name"] = name
            else:
                self._next_track_id += 1
                tracks[self._next_track_id] = {"cx": cx, "cy": cy, "side": side, "last_seen": now, "name": name}

        return True
