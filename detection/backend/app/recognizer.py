"""Face detection + recognition for this standalone service. Its own
InsightFace instance, its own embedding gallery — built by downloading
Parachute's existing enrollment photos (already public via /photos/) and
computing embeddings locally, NOT by importing Parachute's recognizer or
reading its database. Two independent recognition engines that happen to
agree because they're looking at the same underlying photos, not one
engine shared between two processes.
"""

import logging
import threading
import time

import cv2
import numpy as np
from insightface.app import FaceAnalysis

from . import config, parachute_client

logger = logging.getLogger("detection.recognizer")


class FaceRecognizer:
    def __init__(self):
        self._app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"],
                                  allowed_modules=["detection", "recognition"])
        self._app.prepare(ctx_id=-1, det_size=(640, 640))
        self._lock = threading.Lock()
        self._gallery: list[tuple[str, np.ndarray]] = []
        self._last_synced = 0.0

    def detect_and_embed(self, frame_bgr) -> list[dict]:
        """Returns [{"bbox": [x1,y1,x2,y2], "embedding": np.ndarray}, ...]
        for every face found — detection only, matching is a separate step
        (match_all below) so a caller can batch-match against a gallery
        snapshot without re-detecting."""
        faces = self._app.get(frame_bgr)
        return [
            {"bbox": [int(v) for v in f.bbox], "embedding": f.normed_embedding}
            for f in faces
        ]

    def match(self, embedding: np.ndarray) -> tuple[str, float]:
        with self._lock:
            gallery = list(self._gallery)
        best_name, best_score = "Unknown", 0.0
        for name, ref in gallery:
            score = float(np.dot(embedding, ref) / (np.linalg.norm(embedding) * np.linalg.norm(ref) + 1e-8))
            if score > best_score:
                best_name, best_score = name, score
        if best_score < config.RECOGNITION_SIMILARITY_THRESHOLD:
            return "Unknown", best_score
        return best_name, best_score

    def add_to_gallery(self, name: str, embedding: np.ndarray) -> None:
        """Called when a labeled training sample is promoted (see
        training.py) — takes effect immediately for this process, no
        restart, same idea as Parachute's own reload_faces() but entirely
        local to this service's own gallery."""
        with self._lock:
            self._gallery.append((name, embedding))

    def gallery_snapshot(self) -> list[tuple[str, np.ndarray]]:
        with self._lock:
            return list(self._gallery)

    def sync_roster_from_parachute(self, force: bool = False) -> int:
        """Rebuilds this service's gallery from Parachute's current
        enrolled-people roster — downloads each photo (already public) and
        computes a fresh embedding locally. Runs periodically (see
        manager.py) so a rename/add/remove made in Parachute's own People
        page eventually reaches this service too, without any direct DB
        access. Returns how many people were loaded."""
        now = time.time()
        if not force and now - self._last_synced < config.ROSTER_REFRESH_SECONDS:
            return len(self._gallery)
        self._last_synced = now

        people = parachute_client.list_enrolled_people()
        new_gallery: list[tuple[str, np.ndarray]] = []
        for person in people:
            name = person["name"]
            for photo in person.get("photos", []):
                content = parachute_client.fetch_photo_bytes(photo)
                if content is None:
                    continue
                arr = np.frombuffer(content, dtype=np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if img is None:
                    continue
                faces = self._app.get(img)
                if not faces:
                    continue
                face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
                new_gallery.append((name, face.normed_embedding))

        with self._lock:
            self._gallery = new_gallery
        logger.info(
            "Synced roster from Parachute: %d people, %d embeddings",
            len({n for n, _ in new_gallery}), len(new_gallery),
        )
        return len(new_gallery)


_recognizer: FaceRecognizer | None = None
_recognizer_lock = threading.Lock()


def get_recognizer() -> FaceRecognizer:
    global _recognizer
    with _recognizer_lock:
        if _recognizer is None:
            _recognizer = FaceRecognizer()
        return _recognizer
