"""Desk-time / productivity analytics with FULLY AUTOMATIC desk assignment.

No zone is pre-assigned to an employee. Every detection cycle resolves who
(if anyone) currently occupies each zone from live signals, and drives a
per-employee state machine: UNKNOWN -> AT_DESK(zone) -> AWAY -> AT_DESK(zone')
-> ... Every transition is written immediately (desk_sessions / away_sessions
/ desk_movement_events in desk_db.py), not derived after the fact.

Two signal sources, called from pipeline.py once per detection cycle:

  - process_frame(): the SAME per-frame face recognition already running for
    attendance/footfall (~1x/sec via detection_fps). This is the ONLY signal
    that can ESTABLISH identity — a face match is the one thing that says
    "this specific employee is here," so only this path opens a new session
    or moves an employee between zones.

  - process_pose_frame(): the existing pose/person-tracking pass
    (person_tracker.py + pose_detector.py, ~1x/20s). This is the "Re-ID"
    substitute — no dedicated appearance-embedding model, no GPU on this
    box, and the alternative (bumping pose to face-recognition frequency)
    would repeat the exact latency blowup this codebase measured and
    reverted once already for detection resolution. Instead, pose only
    EXTENDS an already-open, face-established session: if a body remains in
    a zone that a face already put someone in, that session doesn't expire
    just because their face wasn't clearly visible for a while (looking
    down, side profile, phone in hand). It never creates a new identity on
    its own — an anonymous body with no recent face confirmation for that
    zone stays anonymous.

Nothing here can misattribute two different employees to the same identity:
every identity string used is recognizer.py's own canonical enrolled name
(the same one attendance/footfall already trust), never invented here.
"""

import logging
import time

from . import config, desk_db

logger = logging.getLogger("dashboard.desk")


class DeskTracker:
    def __init__(self):
        # camera_id -> [zone dict, ...]
        self._zones_by_camera: dict[int, list[dict]] = {}
        # employee_name -> state dict, one of:
        #   {"status": "at_desk", "zone_id": int, "session_id": int,
        #    "last_seen": float, "last_confidence": float | None}
        #   {"status": "away", "away_id": int, "last_seen": float}
        self._person_state: dict[str, dict] = {}
        # zone_id -> count of person bodies pose detected there last cycle —
        # feeds the "don't let a 2nd employee claim an occupied desk unless
        # a 2nd body is actually there" check. Defaults to "unknown" (not
        # present in the dict) until the first pose sample for that camera.
        self._zone_body_count: dict[int, int] = {}

        self.refresh_zones()
        self._rehydrate()

    # ---- setup / persistence ----

    def refresh_zones(self) -> None:
        zones = desk_db.list_zones()
        by_camera: dict[int, list[dict]] = {}
        for zone in zones:
            by_camera.setdefault(zone["camera_id"], []).append(zone)
        self._zones_by_camera = by_camera
        live_zone_ids = {z["id"] for z in zones}
        for name, state in list(self._person_state.items()):
            if state["status"] == "at_desk" and state["zone_id"] not in live_zone_ids:
                # the zone they were in got deleted/edited out from under
                # them — drop the in-memory claim; the DB row is untouched
                # history, this just stops crediting them more desk time
                # against a zone that no longer exists
                del self._person_state[name]

    def _rehydrate(self) -> None:
        grace = config.DESK_SESSION_GRACE_SECONDS
        for row in desk_db.load_open_sessions(grace):
            self._person_state[row["employee_name"]] = {
                "status": "at_desk", "zone_id": row["zone_id"], "session_id": row["id"],
                "last_seen": row["end_ts"], "last_confidence": row["last_confidence"],
            }
        for row in desk_db.load_open_away(grace):
            if row["employee_name"] in self._person_state:
                continue  # shouldn't happen, but a desk session wins if both are somehow open
            self._person_state[row["employee_name"]] = {
                "status": "away", "away_id": row["id"], "last_seen": row["end_ts"],
            }
        if self._person_state:
            logger.info("Desk analytics: rehydrated %d in-progress state(s) from DB", len(self._person_state))

    # ---- geometry ----

    @staticmethod
    def _in_zone(cx: float, cy: float, zone: dict) -> bool:
        return zone["x1"] <= cx <= zone["x2"] and zone["y1"] <= cy <= zone["y2"]

    def _occupant_of(self, zone_id: int) -> str | None:
        for name, state in self._person_state.items():
            if state["status"] == "at_desk" and state["zone_id"] == zone_id:
                return name
        return None

    # ---- face-driven: establishes and moves identity ----

    def process_frame(
        self, camera_id: int, faces: list[dict], frame_width: int, frame_height: int, now: float | None = None,
    ) -> None:
        zones = self._zones_by_camera.get(camera_id)
        if not zones or not frame_width or not frame_height:
            self._evict_stale(now if now is not None else time.time())
            return
        now = now if now is not None else time.time()

        for face in faces:
            name = face.get("name")
            if not name or name == "Unknown":
                continue
            x1, y1, x2, y2 = face["bbox"]
            cx, cy = (x1 + x2) / 2 / frame_width, (y1 + y2) / 2 / frame_height
            for zone in zones:
                if self._in_zone(cx, cy, zone):
                    self._confirm_at_desk(name, zone, camera_id, face.get("score"), now)
                    break  # a face can only be in one zone at a time

        self._evict_stale(now)

    def _confirm_at_desk(self, name: str, zone: dict, camera_id: int, confidence, now: float) -> None:
        zone_id = zone["id"]
        state = self._person_state.get(name)

        if state is not None and state["status"] == "at_desk" and state["zone_id"] == zone_id:
            desk_db.touch_session(state["session_id"], now, confidence)
            state["last_seen"] = now
            if confidence is not None:
                state["last_confidence"] = confidence
            return

        # Occupancy guard: don't let a second employee claim a zone that's
        # already confidently occupied unless pose evidence shows >=2 bodies
        # there this cycle. A body count we simply haven't sampled yet
        # (zone_id absent) doesn't block the claim — better to assign than
        # to silently drop a confirmed face match.
        current_occupant = self._occupant_of(zone_id)
        if current_occupant is not None and current_occupant != name:
            if self._zone_body_count.get(zone_id, 2) < 2:
                logger.info(
                    "Desk analytics: %s confirmed at %s but %s already holds it and only 1 body seen — displacing",
                    name, zone["zone_label"], current_occupant,
                )
                self._end_desk_session(current_occupant, now, reason="displaced")
            # else: pose says 2+ people there — leave the existing occupant
            # alone and still seat `name`; desk_sessions allows multiple
            # concurrent rows for one zone_id by design (no unique
            # constraint), which is exactly what "explicitly detects
            # multiple people" is supposed to allow.

        if state is not None and state["status"] == "at_desk":
            # direct desk-to-desk switch, no away gap in between
            old_zone_id = state["zone_id"]
            desk_db.touch_session(state["session_id"], now, confidence)
            new_session_id = desk_db.start_session(zone_id, name, camera_id, now, confidence)
            desk_db.log_event(
                name, "desk_switch", now, zone_id=zone_id, confidence=confidence,
                details=f"zone {old_zone_id} -> zone {zone_id}",
            )
            self._person_state[name] = {
                "status": "at_desk", "zone_id": zone_id, "session_id": new_session_id,
                "last_seen": now, "last_confidence": confidence,
            }
            return

        if state is not None and state["status"] == "away":
            desk_db.touch_away(state["away_id"], now)
            desk_db.log_event(name, "away_end", now, confidence=confidence)

        session_id = desk_db.start_session(zone_id, name, camera_id, now, confidence)
        desk_db.log_event(name, "session_start", now, zone_id=zone_id, confidence=confidence,
                           details=zone["zone_label"])
        self._person_state[name] = {
            "status": "at_desk", "zone_id": zone_id, "session_id": session_id,
            "last_seen": now, "last_confidence": confidence,
        }

    def _end_desk_session(self, name: str, now: float, reason: str) -> None:
        state = self._person_state.get(name)
        if state is None or state["status"] != "at_desk":
            return
        desk_db.touch_session(state["session_id"], now)
        desk_db.log_event(name, "session_end", now, zone_id=state["zone_id"], details=reason)
        away_id = desk_db.start_away(name, now)
        desk_db.log_event(name, "away_start", now, details=reason)
        self._person_state[name] = {"status": "away", "away_id": away_id, "last_seen": now}

    def _evict_stale(self, now: float) -> None:
        grace = config.DESK_SESSION_GRACE_SECONDS
        for name, state in list(self._person_state.items()):
            if state["status"] != "at_desk":
                continue
            if now - state["last_seen"] > grace:
                self._end_desk_session(name, state["last_seen"], reason="grace period lapsed")

    # ---- pose-driven: extends (never creates) a session ----

    def process_pose_frame(
        self, camera_id: int, people: list[dict], frame_width: int, frame_height: int, now: float | None = None,
    ) -> None:
        zones = self._zones_by_camera.get(camera_id)
        if not zones or not frame_width or not frame_height:
            return
        now = now if now is not None else time.time()

        counts: dict[int, int] = {}
        for person in people:
            x1, y1, x2, y2 = person["bbox"]
            cx, cy = (x1 + x2) / 2 / frame_width, (y1 + y2) / 2 / frame_height
            for zone in zones:
                if self._in_zone(cx, cy, zone):
                    counts[zone["id"]] = counts.get(zone["id"], 0) + 1

        for zone in zones:
            self._zone_body_count[zone["id"]] = counts.get(zone["id"], 0)

        for zone in zones:
            if counts.get(zone["id"], 0) == 0:
                continue
            occupant = self._occupant_of(zone["id"])
            if occupant is not None:
                state = self._person_state[occupant]
                desk_db.touch_session(state["session_id"], now)
                state["last_seen"] = now

        self._evict_stale(now)

    # ---- read side, for the dashboard's "current status" column ----

    def get_live_status(self) -> dict[str, dict]:
        """employee_name -> {"status": "at_desk"|"away", "zone_id": int|None,
        "zone_label": str|None}. Employees never observed today aren't
        present here at all — the caller (desk_db report) treats that as
        "Unknown"."""
        zone_labels = {z["id"]: z["zone_label"] for cams in self._zones_by_camera.values() for z in cams}
        result = {}
        for name, state in self._person_state.items():
            if state["status"] == "at_desk":
                result[name] = {
                    "status": "at_desk", "zone_id": state["zone_id"],
                    "zone_label": zone_labels.get(state["zone_id"]),
                }
            else:
                result[name] = {"status": "away", "zone_id": None, "zone_label": None}
        return result
