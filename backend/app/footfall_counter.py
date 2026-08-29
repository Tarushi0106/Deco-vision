"""Unique footfall (people-counting) via face-embedding re-identification.

Distinct from two other, unrelated things already in this codebase that
also touch "footfall"/faces:
  - person_tracker.py's PersonTracker counts anonymous IN/OUT midline
    crossings from pose detection — no identity, no dedup, just direction
    (see face_db.log_footfall / the footfall_counts table).
  - recognizer.py + face_db.py match a face against ENROLLED (named) people
    for attendance.
This module answers a third question: "how many DISTINCT people, named or
not, showed up today" — every detected face (whatever _detect_and_recognize
in detection_worker.py returns, Unknown included) is a candidate visitor.
An embedding that doesn't match anyone seen on the same camera within
config.FOOTFALL_REID_WINDOW_MINUTES is a new visit and bumps the count; a
match just refreshes that visit's last-seen time so someone standing in
frame (or walking past repeatedly) is never counted twice. Once a person's
embedding falls out of the window (evicted from the cache below), a later
re-appearance has nothing to match against and correctly starts a new visit
row — this is what makes "leave and return after the window" count as new.

Re-identification prefers the recognized NAME over raw embedding similarity
whenever one is available. Measured live on the Entry/Exit camera after
this overcounted badly (68 "unique" visits in ~1hr from a handful of staff):
the SAME real person's own embeddings, compared to each other across two
live sightings just 1-3 SECONDS apart, scored as low as 0.36 cosine
similarity — under the 0.38 threshold this module used at the time — while
different people's embeddings occasionally scored up to 0.385. There is no
single embedding-only threshold that cleanly separates those two
distributions for this camera's footage (compression, angle, lighting all
add noise a single reference-photo comparison doesn't have to survive).
recognizer.py's own name match is far more reliable, because it's already
tuned and validated against each enrolled person's own reference embedding
(see recognizer.SIMILARITY_THRESHOLD) — so two sightings that both resolve
to the same non-"Unknown" name on the same camera within the window are
treated as the same visit outright, without even consulting embedding
similarity. Embedding similarity is now only the decision-maker for
Unknown/unenrolled faces, where no name exists to lean on — that case still
carries the same inherent ambiguity described above; FOOTFALL_SIMILARITY_
THRESHOLD is tuned to reduce (not eliminate) it.

Matching is scoped per camera, not globally across all cameras: the same
person seen on two different cameras is a separate footfall count on each,
matching how physical people-counting sensors are normally deployed (one
count per entrance/location) and keeping the report's per-camera breakdown
meaningful.

Which camera(s) this even runs on is gated by config.FOOTFALL_CAMERAS
(resolved to camera IDs by resolve_footfall_camera_ids below) — the caller
(pipeline.py) is responsible for only calling process() for a face detected
on a resolved gate camera, not every camera in the system.
"""

import logging
import re
import time
import uuid

import numpy as np

from . import config, footfall_db

logger = logging.getLogger("dashboard.footfall")


def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def resolve_footfall_camera_ids(cameras: list[dict]) -> set[int]:
    """Resolves config.FOOTFALL_CAMERAS (comma-separated identifiers) against
    the current camera list. Each identifier matches a camera by exact
    numeric ID, exact cam_code (normalized), or as a substring of the
    camera's name (also normalized — case-insensitive, punctuation/
    underscores treated as spaces) — so "main_gate" matches a camera named
    "Main gate camera". Returns an empty set (footfall disabled everywhere)
    when FOOTFALL_CAMERAS is unset."""
    identifiers = [s.strip() for s in config.FOOTFALL_CAMERAS.split(",") if s.strip()]
    if not identifiers:
        return set()

    resolved: set[int] = set()
    matched_identifiers: set[str] = set()
    for cam in cameras:
        norm_name = _normalize(cam.get("name", ""))
        norm_code = _normalize(cam.get("cam_code") or "")
        for ident in identifiers:
            norm_ident = _normalize(ident)
            if ident == str(cam["id"]) or norm_ident == norm_code or (norm_ident and norm_ident in norm_name):
                resolved.add(cam["id"])
                matched_identifiers.add(ident)
                break

    unmatched = [i for i in identifiers if i not in matched_identifiers]
    if unmatched:
        logger.warning(
            "Footfall: FOOTFALL_CAMERAS entry/entries %s matched no camera — "
            "check camera name/cam_code/ID against what's configured in Camera Management",
            unmatched,
        )
    return resolved


class FootfallCounter:
    def __init__(self):
        # camera_id -> {person_key: {"embedding": np.ndarray, "last_seen": float, "name": str | None}}
        self._cache: dict[int, dict[str, dict]] = {}
        self._rehydrate()

    def _rehydrate(self) -> None:
        """Reloads visits still inside the dedup window from the DB, so a
        backend restart mid-window doesn't recount someone still in frame
        as a brand-new visitor."""
        for row in footfall_db.load_open_visits(config.FOOTFALL_REID_WINDOW_MINUTES * 60):
            cam_cache = self._cache.setdefault(row["camera_id"], {})
            cam_cache[row["person_key"]] = {
                "embedding": row["embedding"], "last_seen": row["last_seen"], "name": row["name"],
            }
        open_count = sum(len(c) for c in self._cache.values())
        if open_count:
            logger.info("Footfall: rehydrated %d open visit(s) from DB", open_count)

    @staticmethod
    def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8))

    def process(
        self, camera_id: int, embedding: np.ndarray, name: str | None = None, now: float | None = None,
    ) -> tuple[str, bool]:
        """Call once per detected face embedding (enrolled or Unknown —
        footfall counts every distinct person, not just named ones). `name`
        is recognizer.py's match for this same face, if any ("Unknown"/None
        otherwise). Returns (person_key, is_new_visit); is_new_visit is True
        exactly when the daily unique footfall count should be incremented.

        Matching order: same non-"Unknown" name on an open visit for this
        camera wins outright (see the module docstring for why — embedding-
        vs-embedding comparison is measurably too noisy to trust here);
        otherwise falls back to embedding similarity, which is the only
        signal available for a face with no reliable name."""
        now = now if now is not None else time.time()
        window = config.FOOTFALL_REID_WINDOW_MINUTES * 60
        cam_cache = self._cache.setdefault(camera_id, {})

        for key in [k for k, v in cam_cache.items() if now - v["last_seen"] > window]:
            del cam_cache[key]

        clean_name = name if name and name != "Unknown" else None

        if clean_name is not None:
            for key, visit in cam_cache.items():
                if visit["name"] == clean_name:
                    cam_cache[key]["last_seen"] = now
                    footfall_db.touch_visit(key, now, name=clean_name)
                    return key, False

        best_key, best_score = None, 0.0
        for key, visit in cam_cache.items():
            score = self._cosine_sim(embedding, visit["embedding"])
            if score > best_score:
                best_key, best_score = key, score

        if best_key is not None and best_score >= config.FOOTFALL_SIMILARITY_THRESHOLD:
            cam_cache[best_key]["last_seen"] = now
            if clean_name is not None:
                cam_cache[best_key]["name"] = clean_name
            footfall_db.touch_visit(best_key, now, name=clean_name)
            return best_key, False

        person_key = uuid.uuid4().hex
        cam_cache[person_key] = {"embedding": embedding, "last_seen": now, "name": clean_name}
        footfall_db.create_visit(person_key, camera_id, embedding, now, name=clean_name)
        return person_key, True
