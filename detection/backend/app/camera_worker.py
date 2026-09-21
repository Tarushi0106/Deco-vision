"""Per-camera worker: connects to Parachute's EXISTING /ws/live/{camera_id}
WebSocket as a plain client — exactly like a browser tab already does —
decodes each JPEG frame, and runs this service's own person detection +
face recognition on it. Opens no RTSP connection of its own; Parachute's
own camera pipeline is the only thing that ever talks to the physical
camera. This is the entire integration boundary for video: one outbound
WebSocket connection to an already-public endpoint, nothing added to
Parachute itself.

One background thread per camera, each running its own small asyncio loop,
kept isolated per thread so one camera's reconnect/backoff can never affect
another's.
"""

import asyncio
import logging
import threading
import time
import uuid

import cv2
import numpy as np
import websockets

from . import config, face_training_db, parachute_client
from .person_detector import PersonDetector
from .recognizer import get_recognizer

logger = logging.getLogger("detection.camera_worker")


class _TrainingCropDedup:
    """Per-camera recent-embedding cache — decides whether an Unknown
    face's crop is novel enough to bother saving as a training sample."""

    def __init__(self):
        self._recent: list[tuple[float, np.ndarray]] = []

    def is_novel(self, embedding: np.ndarray, now: float) -> bool:
        self._recent = [(t, e) for t, e in self._recent if now - t <= config.FACE_TRAINING_DEDUP_WINDOW_SECONDS]
        for _t, e in self._recent:
            sim = float(np.dot(embedding, e) / (np.linalg.norm(embedding) * np.linalg.norm(e) + 1e-8))
            if sim >= config.FACE_TRAINING_DEDUP_SIMILARITY:
                return False
        return True

    def record(self, embedding: np.ndarray, now: float) -> None:
        self._recent.append((now, embedding))


def _encode_training_crop(frame, bbox: list[int]) -> bytes | None:
    x1, y1, x2, y2 = bbox
    bw, bh = x2 - x1, y2 - y1
    if bw <= 0 or bh <= 0:
        return None
    pad_x, pad_y = int(bw * 0.3), int(bh * 0.3)
    h, w = frame.shape[:2]
    cx1, cy1 = max(0, x1 - pad_x), max(0, y1 - pad_y)
    cx2, cy2 = min(w, x2 + pad_x), min(h, y2 + pad_y)
    crop = frame[cy1:cy2, cx1:cx2]
    if crop.size == 0:
        return None
    ch, cw = crop.shape[:2]
    scale = min(1.0, 320 / max(ch, cw))
    if scale < 1.0:
        crop = cv2.resize(crop, (int(cw * scale), int(ch * scale)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return buf.tobytes() if ok else None


class CameraWorker:
    """One instance per camera_id. Owns the WS client connection to
    Parachute, this camera's latest person/face results (read by
    main.py's /ws/detections), and its own training-crop dedup cache."""

    def __init__(self, camera_id: int, camera_name: str):
        self.camera_id = camera_id
        self.camera_name = camera_name
        self._lock = threading.Lock()
        self._latest_people: list[dict] = []
        self._latest_faces: list[dict] = []
        self._computed_at = 0.0
        self._running = False
        self._thread: threading.Thread | None = None
        self._person_detector: PersonDetector | None = None
        self._dedup = _TrainingCropDedup()

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    def get_live_detections(self) -> dict:
        with self._lock:
            return {"people": list(self._latest_people), "faces": list(self._latest_faces),
                    "computed_at": self._computed_at}

    def _run(self) -> None:
        asyncio.run(self._consume_loop())

    async def _consume_loop(self) -> None:
        # Lazy model load, inside this worker thread — never blocks FastAPI
        # startup in main.py.
        self._person_detector = PersonDetector()
        recognizer = get_recognizer()

        url = parachute_client.live_frame_ws_url(self.camera_id)
        backoff = 2
        last_person_at = 0.0
        last_face_at = 0.0

        while self._running:
            try:
                async with websockets.connect(url, max_size=None) as ws:
                    logger.info("Camera %s (%s): connected to Parachute live feed", self.camera_id, self.camera_name)
                    backoff = 2
                    async for message in ws:
                        if not self._running:
                            break
                        frame = cv2.imdecode(np.frombuffer(message, np.uint8), cv2.IMREAD_COLOR)
                        if frame is None:
                            continue
                        now = time.time()

                        people = self._latest_people
                        if now - last_person_at >= config.PERSON_DETECT_INTERVAL_SECONDS:
                            last_person_at = now
                            people = self._person_detector.detect(frame)

                        faces = self._latest_faces
                        if now - last_face_at >= config.FACE_DETECT_INTERVAL_SECONDS:
                            last_face_at = now
                            faces = self._process_faces(recognizer, frame, now)

                        with self._lock:
                            self._latest_people = people
                            self._latest_faces = faces
                            self._computed_at = now
            except (websockets.exceptions.ConnectionClosed, OSError, asyncio.TimeoutError) as e:
                logger.warning("Camera %s (%s): live feed connection lost (%s) — retrying in %ds",
                                self.camera_id, self.camera_name, e, backoff)
            except Exception:
                logger.exception("Camera %s (%s): unexpected error in consume loop",
                                  self.camera_id, self.camera_name)
            if not self._running:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)

    def _process_faces(self, recognizer, frame, now: float) -> list[dict]:
        detections = recognizer.detect_and_embed(frame)
        results = []
        for det in detections:
            name, score = recognizer.match(det["embedding"])
            if name == "Unknown" and self._dedup.is_novel(det["embedding"], now):
                crop_jpeg = _encode_training_crop(frame, det["bbox"])
                if crop_jpeg is not None:
                    self._dedup.record(det["embedding"], now)
                    self._store_training_sample(det["embedding"], crop_jpeg)
            results.append({"bbox": det["bbox"], "name": name, "score": round(score, 3)})
        return results

    def _store_training_sample(self, embedding: np.ndarray, crop_jpeg: bytes) -> None:
        try:
            if face_training_db.count_pending_for_camera(self.camera_id) >= config.FACE_TRAINING_MAX_PENDING_PER_CAMERA:
                return
            camera_dir = config.SAMPLES_DIR / str(self.camera_id)
            camera_dir.mkdir(parents=True, exist_ok=True)
            image_path = camera_dir / f"{int(time.time())}_{uuid.uuid4().hex[:8]}.jpg"
            image_path.write_bytes(crop_jpeg)
            face_training_db.add_pending_sample(self.camera_id, str(image_path), embedding, None)
        except Exception:
            logger.exception("Camera %s: failed to store face-training sample", self.camera_id)
