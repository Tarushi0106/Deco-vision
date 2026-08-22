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
import re
import subprocess
import threading
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path

import cv2
import imageio_ffmpeg
import numpy as np

try:
    import psutil
except ImportError:
    psutil = None

from . import alerts_db, camera_db, clips_db, face_db
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
# recognition clips (Analytics "Clips" column): one continuous clip per
# presence — recording starts the moment a person is first recognized and
# keeps going, at full capture rate, for as long as they keep showing up in
# recognition results, not a fixed-length snippet per event.
CLIP_PRE_SECONDS = 4  # how much buffered video to prepend from just before the person was first seen
CLIP_BUFFER_SECONDS = CLIP_PRE_SECONDS + 2  # rolling buffer used only to seed that pre-roll; margin vs. read races
# Measured live on the "Technical section" camera (multi-person, wide-angle — the one with known
# recognition flakiness): a person sitting continuously in frame still only gets a confident NAME match
# sporadically, with real observed gaps of 60-114s between hits (nothing tracks face identity across
# frames when the name match drops, so there's no cheaper way to bridge the gap than a generous grace
# window). Too short a grace period fragments one visit into many disconnected mini-clips instead of the
# single continuous "they were here" clip that's the point of this feature.
PRESENCE_GRACE_SECONDS = 60  # how long to keep a clip open after the person's last recognition hit
MAX_CLIP_DURATION_SECONDS = 300  # cap a single clip (e.g. someone sitting for an hour) — chapters into new clips past this
MIN_CLIP_DURATION_SECONDS = 1.5  # discard clips shorter than this — a single flickered detection, not a real visit
MAX_CLIPS_PER_PERSON = 30  # retention cap — older clips (file + row) are deleted past this
# Measured this session: 3 clips finalizing around the same time each spawned their own
# ffmpeg transcode with no thread cap, pegging all 12 logical cores to 100% on top of the
# already-pinned recognition worker — the exact "everything is slow" oversubscription
# pattern seen earlier with onnxruntime. Clips are for human playback only (recognition
# already happened on the full-res frame before this ever runs), so downscaling here has
# zero accuracy cost — unlike the recognition pipeline, where downscaling wrecked matching.
CLIP_MAX_DIM = 960  # cap the longer side of saved clip video — free CPU/disk win, no accuracy impact
TRANSCODE_THREADS = 2  # ffmpeg's own thread cap per transcode job
MAX_CONCURRENT_TRANSCODES = 2  # system-wide — bounds worst case to MAX_CONCURRENT_TRANSCODES * TRANSCODE_THREADS cores
_transcode_semaphore = threading.Semaphore(MAX_CONCURRENT_TRANSCODES)


class CameraPipeline:
    def __init__(self, camera_id: int):
        self.camera_id = camera_id
        self._lock = threading.Lock()
        self._latest_jpeg: bytes | None = None
        self._latest_detections: list[dict] = []
        self._running = False
        self._thread: threading.Thread | None = None
        self._last_logged: dict[str, float] = {}
        self._clip_buffer: deque[tuple[float, bytes]] = deque()
        self._clip_buffer_lock = threading.Lock()
        # Resizing+encoding clip video is real per-frame CPU work — measured this session:
        # running it inline in the capture loop (below) directly stole time from reading/
        # encoding the next camera frame, which showed up as live-feed lag even though
        # recognition itself (a separate process) was unaffected. Moved to its own thread,
        # fed by this small drop-if-full queue, so clip encoding can NEVER block capture —
        # worst case a clip frame is skipped, never the live view stuttering.
        self._clip_frame_queue: "queue.Queue" = queue.Queue(maxsize=2)
        self._clip_thread: threading.Thread | None = None
        # last time each name was seen in a recognition result — written by
        # set_detections() (called from PipelineManager's receiver thread),
        # read by _update_clip_sessions() (called from this camera's OWN dedicated
        # _clip_writer_loop thread, never the capture thread). _active_clip_sessions
        # itself is touched ONLY from that one thread, so opening/writing/closing
        # cv2.VideoWriters never has to be synchronized against another thread.
        self._presence_lock = threading.Lock()
        self._last_detection: dict[str, float] = {}
        self._active_clip_sessions: dict[str, dict] = {}
        # snapshot of _active_clip_sessions.keys(), refreshed by the capture thread
        # every time a session opens/closes — lets other threads (the /api/clips/active
        # endpoint) read "who's being recorded right now" without a lock or racing an
        # in-progress dict mutation (a plain frozenset reassignment is an atomic pointer
        # swap under the GIL)
        self._active_names: frozenset = frozenset()

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._clip_thread = threading.Thread(target=self._clip_writer_loop, daemon=True)
        self._clip_thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        if self._clip_thread:
            self._clip_thread.join(timeout=5)

    def get_latest_jpeg(self) -> bytes | None:
        with self._lock:
            return self._latest_jpeg

    def get_latest_detections(self) -> list[dict]:
        with self._lock:
            return list(self._latest_detections)

    def set_detections(self, detections: list[dict]) -> None:
        with self._lock:
            self._latest_detections = detections
        now = time.time()
        with self._presence_lock:
            for det in detections:
                name = det["name"]
                if name == "Unknown":
                    continue
                self._last_detection[name] = now
        self._log_new_detections(detections, now)

    def _log_new_detections(self, detections: list[dict], now: float) -> None:
        for det in detections:
            name = det["name"]
            if name == "Unknown":
                continue
            last = self._last_logged.get(name, 0)
            if now - last < DETECTION_LOG_COOLDOWN_SECONDS:
                continue
            self._last_logged[name] = now
            face_db.log_detection_event(self.camera_id, name, det["bbox"])

    def _update_clip_sessions(self, frame, now: float) -> None:
        """Called once per encoded frame from the capture loop (never from
        another thread — see the comment on _active_clip_sessions in
        __init__). Opens a clip the moment a person is first recognized,
        keeps writing frames to it every tick they're still within
        PRESENCE_GRACE_SECONDS of their last recognition hit, and finalizes
        it once they've been gone that long (or the clip hits the max
        duration cap, in which case it chapters into a fresh one)."""
        with self._presence_lock:
            last_detection_snapshot = dict(self._last_detection)

        for name, last_seen in last_detection_snapshot.items():
            if now - last_seen > PRESENCE_GRACE_SECONDS:
                continue
            session = self._active_clip_sessions.get(name)
            if session is None:
                session = self._open_clip_session(name, now, frame)
                if session is None:
                    continue
                self._active_clip_sessions[name] = session
                self._active_names = frozenset(self._active_clip_sessions)
            try:
                out_w, out_h = session["out_size"]
                session["writer"].write(cv2.resize(frame, (out_w, out_h)) if frame.shape[1::-1] != (out_w, out_h) else frame)
            except Exception:
                logger.exception("Camera %s: failed writing clip frame for %s", self.camera_id, name)
            session["last_written_ts"] = now
            if now - session["start_ts"] >= MAX_CLIP_DURATION_SECONDS:
                self._finalize_clip_session(name, session)
                del self._active_clip_sessions[name]
                self._active_names = frozenset(self._active_clip_sessions)

        for name in list(self._active_clip_sessions):
            last_seen = last_detection_snapshot.get(name, 0)
            if now - last_seen > PRESENCE_GRACE_SECONDS:
                self._finalize_clip_session(name, self._active_clip_sessions.pop(name))
                self._active_names = frozenset(self._active_clip_sessions)

    def _open_clip_session(self, name: str, now: float, frame) -> dict | None:
        try:
            camera_dir = clips_db.CLIPS_DIR / str(self.camera_id)
            camera_dir.mkdir(parents=True, exist_ok=True)
            safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
            base_name = f"{safe_name}_{int(now)}_{uuid.uuid4().hex[:8]}"
            # Written first as a RAW intermediate, then transcoded to real H.264 on
            # finalize (see _transcode_and_log_clip) — cv2.VideoWriter's own "avc1"/
            # H.264 path depends on either the Cisco openh264 DLL (unreliable to load
            # from a background thread on this machine — confirmed via direct testing:
            # "Unable to create encoder" mid-session) or Windows Media Foundation (which
            # needs COM initialized on the calling thread, which this capture thread
            # doesn't do). "mp4v" has no such dependency and reliably opens/writes here.
            raw_path = camera_dir / f"{base_name}.raw.mp4"
            file_path = camera_dir / f"{base_name}.mp4"

            height, width = frame.shape[:2]
            scale = min(1.0, CLIP_MAX_DIM / max(height, width))
            out_width, out_height = max(2, round(width * scale) // 2 * 2), max(2, round(height * scale) // 2 * 2)
            fps = round(1 / VIDEO_ENCODE_INTERVAL_SECONDS)
            writer = cv2.VideoWriter(str(raw_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (out_width, out_height))

            with self._clip_buffer_lock:
                pre_roll = [(ts, jpeg) for ts, jpeg in self._clip_buffer if ts < now]
            start_ts = pre_roll[0][0] if pre_roll else now
            for _ts, jpeg in pre_roll:
                pre_frame = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
                if pre_frame is None:
                    continue
                if pre_frame.shape[1::-1] != (out_width, out_height):
                    pre_frame = cv2.resize(pre_frame, (out_width, out_height))
                writer.write(pre_frame)

            return {
                "writer": writer, "raw_path": raw_path, "file_path": file_path,
                "start_ts": start_ts, "last_written_ts": now, "out_size": (out_width, out_height),
            }
        except Exception:
            logger.exception("Camera %s: failed to open clip for %s", self.camera_id, name)
            return None

    def _finalize_clip_session(self, name: str, session: dict) -> None:
        """Releases the raw writer (cheap) synchronously, then hands the slow
        part — transcoding to real H.264 + the DB write — to a background
        thread so a multi-minute clip can't stall this camera's live capture
        loop. Safe to run off-thread: by the time this is called, `session`
        has already been removed from _active_clip_sessions by the caller."""
        try:
            session["writer"].release()
        except Exception:
            logger.exception("Camera %s: failed to release writer for %s", self.camera_id, name)
            return
        duration = session["last_written_ts"] - session["start_ts"]
        if duration < MIN_CLIP_DURATION_SECONDS:
            Path(session["raw_path"]).unlink(missing_ok=True)
            return
        threading.Thread(
            target=self._transcode_and_log_clip, args=(name, session, duration), daemon=True,
        ).start()

    def _transcode_and_log_clip(self, name: str, session: dict, duration: float) -> None:
        raw_path = session["raw_path"]
        file_path = session["file_path"]
        try:
            ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
            # -threads caps ffmpeg's OWN encoder thread pool per job; the semaphore caps
            # how many of these run at once — together they bound the worst case to
            # MAX_CONCURRENT_TRANSCODES * TRANSCODE_THREADS cores instead of every
            # camera's finalize spawning an uncapped encoder and pegging all 12 at once.
            with _transcode_semaphore:
                result = subprocess.run(
                    [ffmpeg_exe, "-y", "-i", str(raw_path),
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-threads", str(TRANSCODE_THREADS),
                     "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(file_path)],
                    capture_output=True, timeout=max(60.0, duration * 2),
                )
            if result.returncode != 0 or not file_path.exists():
                logger.error(
                    "Camera %s: ffmpeg transcode failed for %s: %s",
                    self.camera_id, name, result.stderr.decode(errors="replace")[-2000:],
                )
                return
            clips_db.log_clip(name, self.camera_id, session["start_ts"], duration, str(file_path))
            clips_db.prune_old_clips(name, MAX_CLIPS_PER_PERSON)
        except Exception:
            logger.exception("Camera %s: failed to transcode/log clip for %s", self.camera_id, name)
        finally:
            Path(raw_path).unlink(missing_ok=True)

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
                        jpeg = buf.tobytes()
                        with self._lock:
                            self._latest_jpeg = jpeg
                        with self._clip_buffer_lock:
                            self._clip_buffer.append((now, jpeg))
                            cutoff = now - CLIP_BUFFER_SECONDS
                            while self._clip_buffer and self._clip_buffer[0][0] < cutoff:
                                self._clip_buffer.popleft()
                    try:
                        self._clip_frame_queue.put_nowait((frame, now))
                    except queue.Full:
                        pass  # clip writer thread is behind — drop this tick's frame rather than block capture

            except RuntimeError as e:
                logger.error("Camera %s: video source error: %s — retrying in 3s", self.camera_id, e)
                source = None
                time.sleep(3)

        if source is not None:
            source.release()

    def _clip_writer_loop(self) -> None:
        """Owns all per-person clip VideoWriters end to end (open/write/
        finalize) in its own thread, fed by _run()'s drop-if-full queue —
        see the comment on _clip_frame_queue in __init__ for why this is
        split out from the capture loop."""
        while self._running:
            try:
                frame, now = self._clip_frame_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            self._update_clip_sessions(frame, now)
        for name in list(self._active_clip_sessions):
            self._finalize_clip_session(name, self._active_clip_sessions.pop(name))
        self._active_names = frozenset()

    def get_active_names(self) -> frozenset:
        return self._active_names


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

    def get_active_clip_people(self) -> list[dict]:
        """Who's currently being recorded (session open, not yet finalized/
        playable) — lets the Analytics UI show "recording now" for someone
        still in frame instead of a misleading "No clips yet"."""
        cameras_by_id = {c["id"]: c["name"] for c in camera_db.list_cameras()}
        result = []
        for camera_id, pipeline in self._pipelines.items():
            for name in pipeline.get_active_names():
                result.append({
                    "person_name": name,
                    "camera_id": camera_id,
                    "camera_name": cameras_by_id.get(camera_id, "—"),
                })
        return result


pipeline_manager = PipelineManager()
