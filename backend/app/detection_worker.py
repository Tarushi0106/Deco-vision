"""Runs face recognition and pose-based footfall/fall analysis in a
SEPARATE PROCESS from the FastAPI app.

Measured this session: onnxruntime's inference call does not release the
GIL for its full wall-clock duration, so running it in a thread of the
main process — even a "separate" one — still froze the asyncio loop
serving /ws/live for as long as each call ran (100-300ms), no matter how
that call's own thread pool was tuned. A real OS process has its own GIL;
nothing it does can block the main process's event loop. The only contact
between the two is the queues passed into run_worker() below, carrying
small payloads (JPEG bytes in, small result dicts out) — never raw frames
or model objects.

DB access (writing detection/footfall/alert rows, reading intrusion
settings) stays in the main process, which already owns those modules;
this worker is pure compute and knows nothing about SQLite.
"""

import queue
import time

import cv2
import numpy as np

from .person_tracker import PersonTracker
from .pose_detector import PoseDetector
from .recognizer import FaceRecognizer

POSE_INTERVAL_SECONDS = 5.0  # pose is heavier than face rec; runs on its own slower cadence


def run_worker(input_queue: "queue.Queue", result_queue: "queue.Queue", embed_response_queue: "queue.Queue") -> None:
    recognizer = FaceRecognizer()
    pose_detector = PoseDetector()
    trackers: dict[int, PersonTracker] = {}
    last_pose_at: dict[int, float] = {}

    while True:
        try:
            item = input_queue.get(timeout=0.5)
        except queue.Empty:
            continue

        kind = item.get("type")

        if kind == "reload_faces":
            recognizer._reload_enrolled()
            continue

        if kind == "embed":
            frame = cv2.imdecode(np.frombuffer(item["jpeg"], np.uint8), cv2.IMREAD_COLOR)
            embedding = None
            if frame is not None:
                faces = recognizer._app.get(frame)
                if faces:
                    face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
                    embedding = face.embedding
            embed_response_queue.put({"request_id": item["request_id"], "embedding": embedding})
            continue

        if kind == "detect":
            camera_id = item["camera_id"]
            frame = cv2.imdecode(np.frombuffer(item["jpeg"], np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                continue

            result = {"camera_id": camera_id, "faces": recognizer.detect_and_recognize(frame)}

            now = time.time()
            if now - last_pose_at.get(camera_id, 0) >= POSE_INTERVAL_SECONDS:
                last_pose_at[camera_id] = now
                people = pose_detector.detect(frame)
                tracker = trackers.setdefault(camera_id, PersonTracker())
                events = tracker.update(people, frame.shape[0])
                result["footfall_events"] = events["footfall_events"]
                result["fall_events"] = events["fall_events"]
                result["person_count"] = len(people)

            try:
                result_queue.put_nowait(result)
            except queue.Full:
                pass
