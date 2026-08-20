"""Per-camera capture loops sharing one recognition model instance.

Each active camera (one with real connection details) gets its own
CameraPipeline thread that does ONLY frame capture + JPEG encoding — as
fast as the camera delivers frames, never blocked by inference. Face
detection/recognition runs on a SEPARATE shared worker thread that
round-robins across cameras, always operating on whichever frame is
currently newest. This matters because InsightFace inference takes
100-300ms; if it ran inline in the capture loop (as it used to), RTSP
frames would back up in the network/FFmpeg buffer while inference was
running, and the live feed would drift further and further behind
real time the longer the process ran. Decoupling means capture never
waits on inference, so video stays live regardless of detection load.
"""

import logging
import threading
import time
from datetime import datetime

import cv2

from . import alerts_db, camera_db, face_db
from .person_tracker import PersonTracker
from .pose_detector import PoseDetector
from .recognizer import FaceRecognizer
from .video_source import RtspSource, WebcamSource

logger = logging.getLogger("dashboard.pipeline")

JPEG_QUALITY = 80
DETECTION_INTERVAL_SECONDS = 0.3  # per camera, in the shared round-robin
# pose detection (YOLO) is meaningfully heavier per-frame than InsightFace
# on CPU, so it runs on its own slower per-camera cadence within the same loop
PERSON_ANALYSIS_INTERVAL_SECONDS = 1.5
# how long an after-hours intrusion alert stays "fresh" before it's allowed
# to fire again for the same camera — otherwise it would refire every sample
INTRUSION_ALERT_COOLDOWN_SECONDS = 300
# don't log a new detection_event for the same person on the same camera
# more often than this - avoids flooding the table while someone stands in frame
DETECTION_LOG_COOLDOWN_SECONDS = 30


class CameraPipeline:
    def __init__(self, camera_id: int):
        self.camera_id = camera_id
        self._lock = threading.Lock()
        self._latest_jpeg: bytes | None = None
        self._latest_frame = None  # raw BGR, for the detection worker
        self._latest_detections: list[dict] = []
        self._running = False
        self._thread: threading.Thread | None = None
        self._last_logged: dict[str, float] = {}

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def get_latest_jpeg(self) -> bytes | None:
        with self._lock:
            return self._latest_jpeg

    def get_latest_frame(self):
        with self._lock:
            return self._latest_frame

    def get_latest_detections(self) -> list[dict]:
        with self._lock:
            return list(self._latest_detections)

    def set_detections(self, detections: list[dict]) -> None:
        with self._lock:
            self._latest_detections = detections
        self._log_new_detections(detections)

    def _log_new_detections(self, detections: list[dict]) -> None:
        now = time.time()
        for det in detections:
            name = det["name"]
            
            # --- Restricted Room Security Check (Only CEO Mahesh Choudhary allowed) ---
            if name == "Unknown":
                last_alert = self._last_logged.get("alert_unknown", 0)
                if now - last_alert >= DETECTION_LOG_COOLDOWN_SECONDS:
                    self._last_logged["alert_unknown"] = now
                    alerts_db.log_alert(self.camera_id, "unauthorized", "Unknown person entered the restricted room!")
                    logger.warning("Camera %s: Unknown person tried entering the restricted room!", self.camera_id)
                continue
            
            if name != "Mahesh Choudhary":
                last_alert = self._last_logged.get(f"alert_{name}", 0)
                if now - last_alert >= DETECTION_LOG_COOLDOWN_SECONDS:
                    self._last_logged[f"alert_{name}"] = now
                    alerts_db.log_alert(self.camera_id, "unauthorized", f"Unauthorized entry attempt by {name}!")
                    logger.warning("Camera %s: Unauthorized person %s tried entering the restricted room!", self.camera_id, name)
                continue  # Baaki log jo allowed nahi hain unka attendance record nahi banega

            last = self._last_logged.get(name, 0)
            if now - last < DETECTION_LOG_COOLDOWN_SECONDS:
                continue
            self._last_logged[name] = now
            face_db.log_detection_event(self.camera_id, name, det["bbox"])
            
            # --- Attendance Saving Logic (Only for CEO Mahesh Choudhary) ---
            try:
                from .database import SessionLocal
                from .attendance import AttendanceRecord
                from datetime import datetime
                
                db = SessionLocal()
                new_attendance = AttendanceRecord(visitor_name=name, status="Present", entry_time=datetime.utcnow())
                db.add(new_attendance)
                db.commit()
                db.close()
            except Exception as e:
                print("Error saving attendance:", e)

    def _create_source(self):
        cam = camera_db.get_camera_connection(self.camera_id)
        if cam is None or not cam["host"]:
            raise RuntimeError(f"Camera {self.camera_id} has no connection details")
        if cam["host"] == "webcam":
            return WebcamSource(index=0)
        return RtspSource(
            host=cam["host"],
            port=cam["port"],
            user=cam["user"],
            password=cam["password"],
            stream_path=cam["stream_path"],
        )

    def _run(self) -> None:
        """Capture-only loop — reads frames as fast as the camera delivers
        them and never blocks on inference."""
        source = None
        while self._running:
            try:
                if source is None:
                    source = self._create_source()
                    logger.info("Camera %s: video source opened", self.camera_id)

                frame = source.get_frame()
                if frame is None:
                    time.sleep(0.05)
                    continue

                ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
                with self._lock:
                    self._latest_frame = frame
                    if ok:
                        self._latest_jpeg = buf.tobytes()

            except RuntimeError as e:
                logger.error("Camera %s: video source error: %s — retrying in 3s", self.camera_id, e)
                source = None
                time.sleep(3)

        if source is not None:
            source.release()


class PipelineManager:
    def __init__(self):
        self._recognizer: FaceRecognizer | None = None
        self._recognizer_lock = threading.Lock()
        self._pose_detector: PoseDetector | None = None
        self._person_trackers: dict[int, PersonTracker] = {}
        self._last_person_analysis: dict[int, float] = {}
        self._pipelines: dict[int, CameraPipeline] = {}
        self._conn_keys: dict[int, tuple] = {}
        self._detection_thread: threading.Thread | None = None
        self._detection_running = False

    @staticmethod
    def _should_be_live(cam: dict) -> bool:
        return bool(cam["is_configured"] and cam["status"] == "active" and cam["live_feed_enabled"])

    def start(self) -> None:
        self._recognizer = FaceRecognizer()
        logger.info("Shared face recognizer loaded")

        for cam in camera_db.list_cameras():
            if self._should_be_live(cam):
                self._start_camera(cam["id"])

        self._detection_running = True
        self._detection_thread = threading.Thread(target=self._detection_loop, daemon=True)
        self._detection_thread.start()

    def stop(self) -> None:
        self._detection_running = False
        if self._detection_thread:
            self._detection_thread.join(timeout=5)
        for pipeline in self._pipelines.values():
            pipeline.stop()
        self._pipelines.clear()
        self._conn_keys.clear()
        self._person_trackers.clear()
        self._last_person_analysis.clear()

    def _connection_key(self, camera_id: int) -> tuple | None:
        cam = camera_db.get_camera_connection(camera_id)
        if cam is None:
            return None
        return (cam["host"], cam["port"], cam["user"], cam["password"], cam["stream_path"])

    def _start_camera(self, camera_id: int) -> None:
        if camera_id in self._pipelines:
            return
        pipeline = CameraPipeline(camera_id)
        pipeline.start()
        self._pipelines[camera_id] = pipeline
        self._conn_keys[camera_id] = self._connection_key(camera_id)

    def refresh_cameras(self) -> None:
        """Call after camera CRUD changes to start/stop pipelines accordingly.

        Also reconnects any already-active camera whose connection details
        (host/port/user/password/stream_path) changed — otherwise editing
        just the password would leave the pipeline running on its already
        -open, old-credentials connection until that connection happened to
        drop on its own."""
        cameras = {c["id"]: c for c in camera_db.list_cameras()}

        for camera_id in list(self._pipelines):
            cam = cameras.get(camera_id)
            if cam is None or not self._should_be_live(cam):
                self._pipelines.pop(camera_id).stop()
                self._conn_keys.pop(camera_id, None)
                self._person_trackers.pop(camera_id, None)
                self._last_person_analysis.pop(camera_id, None)
            elif self._connection_key(camera_id) != self._conn_keys.get(camera_id):
                logger.info("Camera %s: connection details changed, reconnecting", camera_id)
                self._pipelines.pop(camera_id).stop()
                self._conn_keys.pop(camera_id, None)
                self._person_trackers.pop(camera_id, None)
                self._last_person_analysis.pop(camera_id, None)

        for cam in cameras.values():
            if self._should_be_live(cam):
                self._start_camera(cam["id"])

    def _detection_loop(self) -> None:
        """Round-robins across all live cameras, running inference on
        whichever frame is currently newest. Runs independently of every
        camera's capture loop, so it can never slow video down."""
        while self._detection_running:
            pipelines = list(self._pipelines.values())
            if not pipelines:
                time.sleep(0.2)
                continue

            for pipeline in pipelines:
                if not self._detection_running:
                    break
                frame = pipeline.get_latest_frame()
                if frame is None:
                    continue
                with self._recognizer_lock:
                    detections = self._recognizer.detect_and_recognize(frame)
                pipeline.set_detections(detections)

                self._maybe_run_person_analysis(pipeline, frame)

                time.sleep(DETECTION_INTERVAL_SECONDS)

    def _maybe_run_person_analysis(self, pipeline: "CameraPipeline", frame) -> None:
        """Runs on PERSON_ANALYSIS_INTERVAL_SECONDS, not every face-detection
        cycle — pose estimation is heavier than InsightFace on CPU. Drives
        footfall, fall detection, and after-hours intrusion off one shared
        PoseDetector pass, so this costs no extra inference per feature."""
        camera_id = pipeline.camera_id
        now = time.time()
        if now - self._last_person_analysis.get(camera_id, 0) < PERSON_ANALYSIS_INTERVAL_SECONDS:
            return
        self._last_person_analysis[camera_id] = now

        if self._pose_detector is None:
            self._pose_detector = PoseDetector()
            logger.info("Shared pose detector loaded")

        people = self._pose_detector.detect(frame)

        tracker = self._person_trackers.setdefault(camera_id, PersonTracker())
        events = tracker.update(people, frame.shape[0])

        for direction in events["footfall_events"]:
            face_db.log_footfall(camera_id, direction)

        for _track_id in events["fall_events"]:
            alerts_db.log_alert(camera_id, "fall", "Person down detected")
            logger.warning("Camera %s: fall detected", camera_id)

        if people and self._is_within_restricted_window():
            if not alerts_db.recent_open_alert(camera_id, "intrusion", INTRUSION_ALERT_COOLDOWN_SECONDS):
                alerts_db.log_alert(camera_id, "intrusion", "Person present during restricted hours")
                logger.warning("Camera %s: intrusion during restricted hours", camera_id)

    @staticmethod
    def _is_within_restricted_window() -> bool:
        start_str = alerts_db.get_setting("restricted_start")
        end_str = alerts_db.get_setting("restricted_end")
        if not start_str or not end_str:
            return False
        now = datetime.now().time()
        start = datetime.strptime(start_str, "%H:%M").time()
        end = datetime.strptime(end_str, "%H:%M").time()
        if start <= end:
            return start <= now <= end
        return now >= start or now <= end

    def compute_embedding(self, frame_bgr):
        with self._recognizer_lock:
            faces = self._recognizer._app.get(frame_bgr)
        if not faces:
            return None
        face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
        return face.embedding

    def reload_faces(self) -> None:
        if self._recognizer is not None:
            self._recognizer._reload_enrolled()
            logger.info("Reloaded enrolled faces into shared recognizer")

    def get_latest_jpeg(self, camera_id: int) -> bytes | None:
        pipeline = self._pipelines.get(camera_id)
        return pipeline.get_latest_jpeg() if pipeline else None

    def get_latest_detections(self, camera_id: int) -> list[dict]:
        pipeline = self._pipelines.get(camera_id)
        return pipeline.get_latest_detections() if pipeline else []

    def is_live(self, camera_id: int) -> bool:
        return camera_id in self._pipelines


pipeline_manager = PipelineManager()