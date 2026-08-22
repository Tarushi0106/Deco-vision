"""Per-camera capture loops, plus the glue between them and the detection
worker process (see detection_worker.py).

Each active camera (one with real connection details) gets its own
CameraPipeline thread that does ONLY frame capture + JPEG encoding — as
fast as the camera delivers frames, never blocked by inference. Face
recognition and pose-based footfall/fall/intrusion analysis run in a
SEPARATE OS PROCESS (not just a thread — see detection_worker.py for why
that distinction matters), so no amount of inference load can ever slow
capture or the /ws/live asyncio loop down. PipelineManager's job is to
feed that worker JPEG bytes and route its results back to the right
camera + into the DB.
"""

import logging
import multiprocessing
import queue
import threading
import time
import uuid
from datetime import datetime

import cv2

try:
    import psutil
except ImportError:
    psutil = None

from . import alerts_db, camera_db, face_db
from .detection_worker import run_worker
from .video_source import RtspSource, WebcamSource

logger = logging.getLogger("dashboard.pipeline")

JPEG_QUALITY = 80
# Measured this session: pre-downscaling to 640px here before sending to the
# worker (an earlier "efficiency" change) looked free — the detector resizes
# to det_size=640x640 internally anyway — but it silently wrecked RECOGNITION
# quality. ArcFace crops its 112x112 alignment patch from whatever image is
# passed to FaceAnalysis.get(), not from the detector's internal resized copy;
# feeding it an already-shrunk frame meant every face's recognition crop was
# sourced from far fewer real pixels, especially on wide/multi-person cameras
# where each face is already small. A verified real person's true similarity
# went from 0.64 (full-res crop) to ~0.06-0.2 (640px-downscaled whole frame) —
# entirely explaining "everyone shows as Visitor" on that camera. Reverted:
# the worker now gets the full-res frame and does its own bounded, per-face
# high-res re-check (see detection_worker.py) instead of a blanket downscale.
VIDEO_ENCODE_INTERVAL_SECONDS = 1 / 15  # matches main.py's VIDEO_FPS — no point encoding faster than that
STALE_SOURCE_TIMEOUT_SECONDS = 5  # force-reconnect a capture source that's stopped delivering frames
DEFAULT_DETECTION_FPS = 1  # how often frames are sent to the worker for face recognition; user-configurable
SETTINGS_POLL_INTERVAL_SECONDS = 2  # how often the sender thread re-reads detection_fps from settings
# onnxruntime (face rec) and YOLO both default to spawning threads across every
# logical CPU per inference call. Measured this session: that let the worker
# process burst to ~8 of 12 threads continuously, starving capture/encode, the
# API process, and the rest of the machine — the "everything is slow" symptom,
# distinct from recognition-result lag. Pinning the worker to a minority of
# cores caps its footprint regardless of how many threads it spawns internally.
WORKER_MAX_CPU_CORES = 4
EMBED_REQUEST_TIMEOUT_SECONDS = 10
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
            if name == "Unknown":
                continue
            last = self._last_logged.get(name, 0)
            if now - last < DETECTION_LOG_COOLDOWN_SECONDS:
                continue
            self._last_logged[name] = now
            face_db.log_detection_event(self.camera_id, name, det["bbox"])

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
        them (draining the RTSP buffer so it can never back up) and never
        blocks on inference. JPEG-encoding, though, is throttled to
        VIDEO_ENCODE_INTERVAL_SECONDS — the camera's native rate is often
        well above the 15fps actually delivered to the browser, and encoding
        every single frame just to immediately overwrite it before it's ever
        sent was wasted CPU cost."""
        source = None
        last_frame_at = time.time()
        last_encode_at = 0.0
        while self._running:
            try:
                if source is None:
                    source = self._create_source()
                    logger.info("Camera %s: video source opened", self.camera_id)
                    last_frame_at = time.time()

                frame = source.get_frame()
                if frame is None:
                    # RTSP over a dropped/stale TCP connection can return
                    # None forever without OpenCV ever raising — nothing
                    # here would otherwise trigger a reconnect, so the feed
                    # just freezes silently. Force one after a timeout.
                    if time.time() - last_frame_at > STALE_SOURCE_TIMEOUT_SECONDS:
                        logger.error("Camera %s: no frame for %ds, forcing reconnect",
                                     self.camera_id, STALE_SOURCE_TIMEOUT_SECONDS)
                        source.release()
                        source = None
                    time.sleep(0.05)
                    continue

                last_frame_at = time.time()

                now = time.time()
                if now - last_encode_at >= VIDEO_ENCODE_INTERVAL_SECONDS:
                    last_encode_at = now
                    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
                    if ok:
                        with self._lock:
                            self._latest_jpeg = buf.tobytes()

            except RuntimeError as e:
                logger.error("Camera %s: video source error: %s — retrying in 3s", self.camera_id, e)
                source = None
                time.sleep(3)

        if source is not None:
            source.release()


class PipelineManager:
    def __init__(self):
        self._pipelines: dict[int, CameraPipeline] = {}
        self._conn_keys: dict[int, tuple] = {}

        self._input_queue: multiprocessing.Queue = multiprocessing.Queue(maxsize=10)
        self._result_queue: multiprocessing.Queue = multiprocessing.Queue(maxsize=50)
        # Separate from _input_queue on purpose: embed requests (enrollment) are rare
        # and time-sensitive (the caller is waiting synchronously, see
        # compute_embedding's EMBED_REQUEST_TIMEOUT_SECONDS deadline below), while
        # _input_queue carries a constant stream of "detect" frames. Sharing one FIFO
        # meant an embed request could sit behind a backlog of detect frames long
        # enough to blow past its own timeout — the worker would eventually process
        # it and find the face just fine, but the caller had already given up and
        # reported "no face detected", even though detection itself never failed.
        # This is why every camera Allow List sync and manual enrollment was failing.
        self._embed_request_queue: multiprocessing.Queue = multiprocessing.Queue(maxsize=5)
        self._embed_response_queue: multiprocessing.Queue = multiprocessing.Queue()
        self._worker_process: multiprocessing.Process | None = None

        self._running = False
        self._sender_thread: threading.Thread | None = None
        self._receiver_thread: threading.Thread | None = None

    @staticmethod
    def _should_be_live(cam: dict) -> bool:
        return bool(cam["is_configured"] and cam["status"] == "active" and cam["live_feed_enabled"])

    def _pin_worker_cpu_affinity(self) -> None:
        if psutil is None:
            logger.warning("psutil not installed — detection worker can use all CPU cores")
            return
        try:
            proc = psutil.Process(self._worker_process.pid)
            available = proc.cpu_affinity()
            proc.cpu_affinity(available[:WORKER_MAX_CPU_CORES])
            logger.info(
                "Detection worker pinned to %d of %d CPU cores",
                min(WORKER_MAX_CPU_CORES, len(available)), len(available),
            )
        except Exception as e:
            logger.warning("Could not set detection worker CPU affinity: %s", e)

    def start(self) -> None:
        self._worker_process = multiprocessing.Process(
            target=run_worker,
            args=(
                self._input_queue,
                self._result_queue,
                self._embed_response_queue,
                self._embed_request_queue,
                WORKER_MAX_CPU_CORES,
            ),
            daemon=True,
        )
        self._worker_process.start()
        logger.info("Detection worker process started (pid %s)", self._worker_process.pid)
        self._pin_worker_cpu_affinity()

        for cam in camera_db.list_cameras():
            if self._should_be_live(cam):
                self._start_camera(cam["id"])

        self._running = True
        self._sender_thread = threading.Thread(target=self._sender_loop, daemon=True)
        self._sender_thread.start()
        self._receiver_thread = threading.Thread(target=self._receiver_loop, daemon=True)
        self._receiver_thread.start()

    def stop(self) -> None:
        self._running = False
        if self._sender_thread:
            self._sender_thread.join(timeout=5)
        if self._receiver_thread:
            self._receiver_thread.join(timeout=5)
        if self._worker_process is not None:
            self._worker_process.terminate()
            self._worker_process.join(timeout=5)
        for pipeline in self._pipelines.values():
            pipeline.stop()
        self._pipelines.clear()
        self._conn_keys.clear()

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
            elif self._connection_key(camera_id) != self._conn_keys.get(camera_id):
                logger.info("Camera %s: connection details changed, reconnecting", camera_id)
                self._pipelines.pop(camera_id).stop()
                self._conn_keys.pop(camera_id, None)

        for cam in cameras.values():
            if self._should_be_live(cam):
                self._start_camera(cam["id"])

    def _sender_loop(self) -> None:
        """Feeds the detection worker JPEG bytes at the user-configurable
        detection_fps rate (see /api/settings) — cheap enqueueing only,
        never inference, so this can't block anything downstream."""
        interval = 1 / DEFAULT_DETECTION_FPS
        last_settings_check = 0.0

        while self._running:
            now = time.time()
            if now - last_settings_check >= SETTINGS_POLL_INTERVAL_SECONDS:
                last_settings_check = now
                try:
                    fps = float(alerts_db.get_setting("detection_fps", str(DEFAULT_DETECTION_FPS)))
                    interval = 1 / fps if fps > 0 else 1 / DEFAULT_DETECTION_FPS
                except (TypeError, ValueError):
                    interval = 1 / DEFAULT_DETECTION_FPS

            for camera_id, pipeline in list(self._pipelines.items()):
                jpeg = pipeline.get_latest_jpeg()
                if jpeg is None:
                    continue
                try:
                    self._input_queue.put_nowait({"type": "detect", "camera_id": camera_id, "jpeg": jpeg})
                except queue.Full:
                    pass

            time.sleep(interval)

    def _receiver_loop(self) -> None:
        """Drains the worker's result queue and routes each result to its
        camera's overlay + the DB (footfall/fall/intrusion logging stays
        here, in the process that already owns those DB modules)."""
        while self._running:
            try:
                result = self._result_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            self._dispatch_result(result)

    def _dispatch_result(self, result: dict) -> None:
        camera_id = result["camera_id"]
        pipeline = self._pipelines.get(camera_id)
        if pipeline is None:
            return

        if "faces" in result:
            pipeline.set_detections(result["faces"])

        for direction in result.get("footfall_events", []):
            face_db.log_footfall(camera_id, direction)

        for _track_id in result.get("fall_events", []):
            alerts_db.log_alert(camera_id, "fall", "Person down detected")
            logger.warning("Camera %s: fall detected", camera_id)

        if result.get("person_count", 0) > 0 and self._is_within_restricted_window():
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
        """Used only by /api/people (enrollment) — rare enough that a
        blocking round-trip to the worker process is fine here."""
        ok, buf = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if not ok:
            return None
        request_id = uuid.uuid4().hex
        self._embed_request_queue.put({"type": "embed", "request_id": request_id, "jpeg": buf.tobytes()})

        deadline = time.time() + EMBED_REQUEST_TIMEOUT_SECONDS
        while time.time() < deadline:
            try:
                resp = self._embed_response_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if resp["request_id"] == request_id:
                return resp["embedding"]
        return None

    def reload_faces(self) -> None:
        self._input_queue.put({"type": "reload_faces"})
        logger.info("Signaled detection worker to reload enrolled faces")

    def get_latest_jpeg(self, camera_id: int) -> bytes | None:
        pipeline = self._pipelines.get(camera_id)
        return pipeline.get_latest_jpeg() if pipeline else None

    def get_latest_detections(self, camera_id: int) -> list[dict]:
        pipeline = self._pipelines.get(camera_id)
        return pipeline.get_latest_detections() if pipeline else []

    def is_live(self, camera_id: int) -> bool:
        return camera_id in self._pipelines


pipeline_manager = PipelineManager()
