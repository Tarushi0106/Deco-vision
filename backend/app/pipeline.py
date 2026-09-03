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

from . import alert_events, alerts_db, camera_db, clips_db, config, face_db, footfall_gate_db, zones_db
from .desk_tracker import DeskTracker
from .detection_worker import CAMERA_DETECTION_MAX_DIM, run_worker
from .footfall_counter import FootfallCounter, resolve_footfall_camera_ids
from .gate_tracker import GateTracker
from .recognition_stabilizer import RecognitionStabilizer
from .video_source import RtspSource, WebcamSource

logger = logging.getLogger("dashboard.pipeline")
recognition_logger = logging.getLogger("dashboard.recognition_pipeline")


def _ts_ms() -> str:
    """Millisecond-precision wall-clock timestamp for the detection-to-
    dashboard latency trace in _check_zone_violations — set LOG_LEVEL=DEBUG
    to see the full per-cycle path this produces."""
    now = time.time()
    return time.strftime("%H:%M:%S", time.localtime(now)) + f".{int(now * 1000) % 1000:03d}"


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
# Diagnosed live: camera 1 rejected login with "number of user logins has
# exceeded the limit" — the device's own brute-force lockout, most likely
# self-inflicted by this loop's old fixed 3s retry hammering the login
# endpoint through an auth failure (transient network blip, device reboot,
# whatever the original cause) for as long as this process had been running.
# Backing off exponentially means a real outage gets retried patiently instead
# of continuously re-triggering/extending a device-side lockout; a successful
# reconnect resets it back to the base delay immediately.
RECONNECT_BASE_DELAY_SECONDS = config.CAMERA_RECONNECT_BASE_DELAY_SECONDS
RECONNECT_MAX_DELAY_SECONDS = config.CAMERA_RECONNECT_MAX_DELAY_SECONDS
DEFAULT_DETECTION_FPS = config.DEFAULT_DETECTION_FPS  # how often frames are sent to the worker for face recognition; user-configurable
SETTINGS_POLL_INTERVAL_SECONDS = 2  # how often the sender thread re-reads detection_fps from settings
# onnxruntime (face rec) and YOLO both default to spawning threads across every
# logical CPU per inference call. Measured this session: that let the worker
# process burst to ~8 of 12 threads continuously, starving capture/encode, the
# API process, and the rest of the machine — the "everything is slow" symptom,
# distinct from recognition-result lag. Pinning the worker to a minority of
# cores caps its footprint regardless of how many threads it spawns internally.
WORKER_MAX_CPU_CORES = config.DETECTION_WORKER_MAX_CPU_CORES
# Must comfortably exceed the worker's worst-case single-frame processing
# time, or every embed request (enrollment, camera Allow List sync) times
# out and gets silently misreported as "no face detected" — the worker only
# checks embed_request_queue BETWEEN frames, never mid-frame, and a busy
# multi-person camera's detect cycle (see detection_worker.py's
# MAX_FULL_RES_RECHECKS comment) can run longer than the old 10s budget
# while multiple live cameras keep it fed.
EMBED_REQUEST_TIMEOUT_SECONDS = config.DETECTION_EMBED_TIMEOUT_SECONDS
# how long an after-hours intrusion alert stays "fresh" before it's allowed
# to fire again for the same camera — otherwise it would refire every sample
INTRUSION_ALERT_COOLDOWN_SECONDS = 300
# shorter than INTRUSION/ZONE's 300s — a real fire/smoke event should keep
# re-alerting more often than that, not go quiet for 5 minutes at a time
FIRE_SMOKE_ALERT_COOLDOWN_SECONDS = 120
# Zone intrusions use real entry/exit tracking instead (see
# PipelineManager._zone_occupancy / _check_zone_violations), not a flat
# cooldown — someone continuously present never re-fires no matter how
# long they stay, and leaving-then-returning fires immediately rather than
# waiting out a multi-minute timer. This is how long an absence has to
# last before it counts as "left", not "recognition missed one frame".
# Originally set to 8s on the (wrong) assumption that detect cycles hit
# every ~1s reliably — confirmed live on "Main gate camera" 2026-09-03:
# a continuously-present, unmoving person re-triggered every ~20-30s at
# 8s, MORE often than the old flat 300s cooldown, the opposite of the
# point of this change. Matches PRESENCE_GRACE_SECONDS below (60s) —
# same real measured recognition gaps (60-114s, same recognition
# pipeline, see that constant's own comment) — but can't just reference
# it directly, it's defined later in this file.
ZONE_EXIT_GRACE_SECONDS = 60
# Recognition can resolve "Unknown" -> a real name a cycle or two after a
# zone violation first fires (detection_worker's full-res recheck can land
# after the initial match) — this is how recent an "Unknown" alert has to
# be to count as the SAME physical entry (upgraded in place, see
# alerts_db.upgrade_unknown_zone_alert) rather than a second, duplicate one.
UNKNOWN_UPGRADE_WINDOW_SECONDS = 15
# One evidence frame per alert (zone_intrusion, fire, smoke) — unlike
# detection_worker.py's separate fire/smoke debug dir, this is linked to the
# actual alert row (alerts.snapshot_path) and served via
# GET /api/alerts/{id}/snapshot. Reuses the same JPEG the live view is
# already showing (CameraPipeline.get_latest_jpeg()), so this costs one file
# write, never a re-encode.
ALERT_SNAPSHOT_DIR = Path(__file__).resolve().parent.parent / "data" / "alert_snapshots"
# don't log a new detection_event for the same person on the same camera
# more often than this - avoids flooding the table while someone stands in frame
DETECTION_LOG_COOLDOWN_SECONDS = config.DETECTION_LOG_COOLDOWN_SECONDS
RECOGNITION_MIN_CONSECUTIVE_HITS = config.RECOGNITION_MIN_CONSECUTIVE_HITS
WORKER_HEALTH_CHECK_INTERVAL_SECONDS = config.WORKER_HEALTH_CHECK_INTERVAL_SECONDS
# Pseudo "person_name" used to piggyback smoke events onto the exact same
# presence/clip-session machinery built for face recognition (_last_detection,
# _active_clip_sessions, _update_clip_sessions et al. below) — a smoke event
# is just another named "presence" as far as that machinery cares, so no new
# recording path is needed. Kept out of get_active_clip_people's result (see
# PipelineManager) so it never shows up disguised as a real person on the
# People/Analytics "recording now" UI.
SMOKE_CLIP_SUBJECT = "Smoke Alert"
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
# No retention cap — clips are kept indefinitely per explicit request (Daily
# Activity search needs full history, not just the most recent slice). This
# is a deliberate disk-usage tradeoff: ~3.4MB/clip measured, unbounded
# growth over time. Revisit with a time-based cap (e.g. keep N days) instead
# of a count-based one if disk space becomes a real constraint.
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
        self._latest_fire_smoke: list[dict] = []
        self._running = False
        self._thread: threading.Thread | None = None
        self._last_logged: dict[str, float] = {}
        # Consecutive-hit streak per name, gating detection_event logging (not
        # the live overlay) — see RECOGNITION_MIN_CONSECUTIVE_HITS. Reset to 0
        # for any name absent from the current detection cycle.
        self._consecutive_hits: dict[str, int] = {}
        # None (the default) means stabilization is off, in which case
        # set_detections() below skips it entirely -- zero behavior change
        # from before this feature existed.
        self._stabilizer = RecognitionStabilizer() if config.RECOGNITION_STABILIZATION_ENABLED else None
        self._clip_buffer: deque[tuple[float, bytes]] = deque()
        self._clip_buffer_lock = threading.Lock()
        # Resizing+encoding clip video is real per-frame CPU work — measured this session:
        # running it inline in the capture loop (below) directly stole time from reading/
        # encoding the next camera frame, which showed up as live-feed lag even though
        # recognition itself (a separate process) was unaffected. Moved to its own thread,
        # fed by this drop-if-full queue, so clip encoding can NEVER block capture — worst
        # case a clip frame is skipped, never the live view stuttering. Sized for ~30s of
        # buffering at the 15fps encode rate (was 2 — ~130ms — until diagnosed live: any
        # transient load on the writer thread silently dropped frames, so a clip's reported
        # duration (wall-clock presence span) could end up far longer than its actual
        # playable video (fewer frames than that span implies at 15fps) — see
        # _open_clip_session's frames_written tracking for the other half of that fix.
        self._clip_frame_queue: "queue.Queue" = queue.Queue(maxsize=450)
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

    def get_latest_fire_smoke(self) -> list[dict]:
        with self._lock:
            return list(self._latest_fire_smoke)

    def set_fire_smoke(self, boxes: list[dict]) -> None:
        with self._lock:
            self._latest_fire_smoke = boxes

    def note_smoke_event(self) -> None:
        """Called once per detect cycle a "smoke" event fires (see
        PipelineManager._dispatch_result). Records SMOKE_CLIP_SUBJECT as
        "seen" right now, which is all _update_clip_sessions needs to open/
        keep-alive/close a real recorded clip around the event — the same
        presence-window logic a recognized person's clip already uses."""
        now = time.time()
        with self._presence_lock:
            self._last_detection[SMOKE_CLIP_SUBJECT] = now

    def set_detections(self, detections: list[dict]) -> None:
        # Scoped here deliberately: zone-violation and footfall/desk logic in
        # PipelineManager._dispatch_result already ran against the RAW
        # per-frame match before this is called, so temporal smoothing never
        # delays a security-relevant decision -- it only affects what's
        # displayed live and what gets logged as a detection_event.
        if self._stabilizer is not None:
            detections = self._stabilizer.stabilize(detections)
        with self._lock:
            self._latest_detections = detections
        now = time.time()
        with self._presence_lock:
            for det in detections:
                name = det["name"]
                if name == "Unknown":
                    continue
                self._last_detection[name] = now
        self._update_consecutive_hits(detections)
        self._log_new_detections(detections, now)
        recognition_logger.debug(
            "camera=%s stage=result_stored names=%s",
            self.camera_id, [d["name"] for d in detections if d["name"] != "Unknown"],
        )

    def _update_consecutive_hits(self, detections: list[dict]) -> None:
        """Tracks how many detection cycles in a row each name has appeared
        for, gating detection_event logging below — a name absent from THIS
        cycle resets to 0 rather than decaying gradually, since each cycle
        already represents a full detection_fps sampling interval (default
        1s), not a single video frame."""
        seen_this_cycle = {det["name"] for det in detections if det["name"] != "Unknown"}
        for name in seen_this_cycle:
            self._consecutive_hits[name] = self._consecutive_hits.get(name, 0) + 1
        for name in list(self._consecutive_hits):
            if name not in seen_this_cycle:
                del self._consecutive_hits[name]

    def _log_new_detections(self, detections: list[dict], now: float) -> None:
        for det in detections:
            name = det["name"]
            if name == "Unknown":
                continue
            if self._consecutive_hits.get(name, 0) < RECOGNITION_MIN_CONSECUTIVE_HITS:
                continue
            last = self._last_logged.get(name, 0)
            if now - last < DETECTION_LOG_COOLDOWN_SECONDS:
                continue
            self._last_logged[name] = now
            face_db.log_detection_event(self.camera_id, name, det["bbox"], score=det.get("score"))

    def _update_clip_sessions(self, frame, now: float) -> None:
        """Called once per encoded frame from the capture loop (never from
        another thread — see the comment on _active_clip_sessions in
        __init__). Opens a clip the moment a person is first recognized,
        keeps writing frames to it every tick they're still within
        PRESENCE_GRACE_SECONDS of their last recognition hit, and finalizes
        it once they've been gone that long (or the clip hits the max
        duration cap, in which case it chapters into a fresh one).

        For a camera in config.CAMERA_ONVIF_REPLAY_CHANNEL, "the clip" is
        just this start/end timestamp window — no local frames are ever
        written; playback fetches the same window straight from the
        camera's own recording on demand (see onvif_client.py / main.py's
        /api/clips/{id}/video)."""
        is_replay_camera = self.camera_id in config.CAMERA_ONVIF_REPLAY_CHANNEL
        with self._presence_lock:
            last_detection_snapshot = dict(self._last_detection)

        for name, last_seen in last_detection_snapshot.items():
            if now - last_seen > PRESENCE_GRACE_SECONDS:
                continue
            session = self._active_clip_sessions.get(name)
            if session is None:
                session = (
                    {"start_ts": now, "last_written_ts": now, "replay": True}
                    if is_replay_camera
                    else self._open_clip_session(name, now, frame)
                )
                if session is None:
                    continue
                self._active_clip_sessions[name] = session
                self._active_names = frozenset(self._active_clip_sessions)
            if not session.get("replay"):
                try:
                    out_w, out_h = session["out_size"]
                    session["writer"].write(cv2.resize(frame, (out_w, out_h)) if frame.shape[1::-1] != (out_w, out_h) else frame)
                    session["frames_written"] += 1
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
                "fps": fps, "frames_written": len(pre_roll),
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
        if session.get("replay"):
            # No local file was ever written — the DB row's start_ts/duration
            # IS the clip; playback fetches those exact seconds from the
            # camera's own recording when actually requested. Cheap enough
            # to log inline, no background thread needed. Wall-clock is the
            # right measure here (unlike the local-recording path below) —
            # there's no frame queue/writer to drop frames from.
            duration = session["last_written_ts"] - session["start_ts"]
            if duration >= MIN_CLIP_DURATION_SECONDS:
                clips_db.log_clip(name, self.camera_id, session["start_ts"], duration, "")
            return

        # The reported/logged duration is the actual video's length (frame
        # count / fps), NOT the wall-clock presence span used above — they
        # can diverge if the writer thread ever fell behind and frames got
        # dropped (see _clip_frame_queue's sizing comment). Using the real
        # frame count means the number shown always matches what's actually
        # playable, even in a worst case where drops still happen.
        duration = session["frames_written"] / session["fps"]

        try:
            session["writer"].release()
        except Exception:
            logger.exception("Camera %s: failed to release writer for %s", self.camera_id, name)
            return
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
        retry_delay = RECONNECT_BASE_DELAY_SECONDS
        while self._running:
            try:
                if source is None:
                    source = self._create_source()
                    logger.info("Camera %s: video source opened", self.camera_id)
                    last_frame_at = time.time()
                    retry_delay = RECONNECT_BASE_DELAY_SECONDS  # a successful open resets the backoff

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
                logger.error("Camera %s: video source error: %s — retrying in %ds",
                              self.camera_id, e, retry_delay)
                source = None
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, RECONNECT_MAX_DELAY_SECONDS)

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

        # One detection-worker PROCESS PER CAMERA (keyed by camera_id), not one
        # shared worker for every camera — see _start_worker. A single shared
        # worker measured this session: it processes one "detect" item at a
        # time, so one camera's expensive frame (e.g. a wide multi-person room
        # at higher detection resolution, needing several costly per-face
        # full-res rechecks — see detection_worker.py's MAX_FULL_RES_RECHECKS)
        # can take 8-15+ seconds, during which every OTHER camera's frames
        # pile up behind it and get silently dropped once its input queue
        # fills (maxsize=10) — that camera then barely gets recognized at
        # all, not because matching is wrong but because its frames rarely
        # reach the worker. Giving each camera its own process+queue means
        # one camera's cost can never block another's — and this scales UP
        # on real deployment hardware (more cores/a GPU), unlike the old
        # design which stayed serialized no matter how much hardware was
        # available.
        self._input_queues: dict[int, multiprocessing.Queue] = {}
        self._worker_processes: dict[int, multiprocessing.Process] = {}
        # cores each camera's worker was (re)started with — recorded so the
        # health-check watchdog (see _check_worker_health) can respawn a dead
        # worker with the same allocation it originally had, without needing
        # to recompute _worker_core_allocation for every other live camera.
        self._worker_cores: dict[int, int] = {}
        self._result_queue: multiprocessing.Queue = multiprocessing.Queue(maxsize=50)
        # Separate from the per-camera input queues on purpose: embed requests
        # (enrollment) are rare and time-sensitive (the caller is waiting
        # synchronously, see compute_embedding's EMBED_REQUEST_TIMEOUT_SECONDS
        # deadline below), while each input queue carries a constant stream of
        # "detect" frames. Sharing one FIFO meant an embed request could sit
        # behind a backlog of detect frames long enough to blow past its own
        # timeout — the worker would eventually process it and find the face
        # just fine, but the caller had already given up and reported "no face
        # detected", even though detection itself never failed. This is why
        # every camera Allow List sync and manual enrollment was failing.
        # Shared across every per-camera worker process below — whichever one
        # polls first (each checks this queue before its own input queue, same
        # priority as before) handles it; harmless with multiple consumers.
        self._embed_request_queue: multiprocessing.Queue = multiprocessing.Queue(maxsize=5)
        self._embed_response_queue: multiprocessing.Queue = multiprocessing.Queue()
        # Round-robins which logical CPU(s) each new worker is pinned to (see
        # _pin_worker_cpu_affinity) so multiple per-camera workers don't all
        # collide on the same core(s) — each slicing from the same start would
        # defeat the point of separate processes.
        self._next_core_offset = 0

        # Unique footfall (people counting) — see footfall_counter.py. Lives
        # here, in the main process, because its in-memory dedup cache needs
        # to persist across the worker's whole lifetime and it writes to the
        # DB, which (like every other DB module) this process owns, not the
        # worker. Constructed in start() rather than here: pipeline_manager
        # is a module-level singleton built at import time, before main.py's
        # startup handler has called footfall_db.init_db() — building it
        # this early would query a table that doesn't exist yet.
        self._footfall_counter: FootfallCounter | None = None
        # camera IDs config.FOOTFALL_CAMERAS resolves to — recomputed in
        # start() and refresh_cameras() so a camera rename/add/remove is
        # picked up without a restart. Only faces from cameras in this set
        # ever reach the footfall counter (see _dispatch_result).
        self._footfall_camera_ids: set[int] = set()

        # Desk-time analytics — see desk_tracker.py. Same startup-ordering
        # constraint and reason as _footfall_counter above (desk_db.init_db()
        # hasn't run yet at import time).
        self._desk_tracker: DeskTracker | None = None

        # Footfall gate-line crossing — see gate_tracker.py. Same
        # startup-ordering constraint as the two above.
        self._gate_tracker: GateTracker | None = None

        self._running = False
        self._sender_thread: threading.Thread | None = None
        self._receiver_thread: threading.Thread | None = None

        # (camera_id, zone_id, person_name) -> last time this identity was
        # seen inside this zone. Only ever read/written from _receiver_loop's
        # thread (via _check_zone_violations), so no lock needed — same
        # single-writer assumption CameraPipeline's own _last_logged/
        # _consecutive_hits dicts already rely on. Entry/exit state for zone
        # alerts (see _check_zone_violations) — replaced a flat time cooldown
        # that couldn't tell "still standing there" from "left and came
        # back", so a real re-entry could sit unalerted for minutes.
        self._zone_occupancy: dict[tuple[int, int, str], float] = {}

    @staticmethod
    def _should_be_live(cam: dict) -> bool:
        return bool(cam["is_configured"] and cam["status"] == "active" and cam["live_feed_enabled"])

    def _pin_worker_cpu_affinity(self, pid: int, cores: int) -> None:
        if psutil is None:
            logger.warning("psutil not installed — detection worker can use all CPU cores")
            return
        try:
            available = psutil.Process().cpu_affinity()
            assigned = [available[(self._next_core_offset + i) % len(available)] for i in range(cores)]
            self._next_core_offset = (self._next_core_offset + cores) % len(available)
            psutil.Process(pid).cpu_affinity(assigned)
            logger.info("Detection worker (pid %s) pinned to CPU core(s) %s", pid, assigned)
        except Exception as e:
            logger.warning("Could not set detection worker CPU affinity: %s", e)

    def _worker_core_allocation(self, camera_ids: list[int]) -> dict[int, int]:
        """WORKER_MAX_CPU_CORES is the TOTAL budget for the whole detection
        subsystem, split across however many per-camera workers are active —
        weighted by relative cost, not evenly. Measured live: an even
        1-core-each split still left the camera tuned for
        CAMERA_DETECTION_MAX_DIM (higher-resolution detection for a wide
        multi-person room — several times more expensive per frame than a
        default camera) taking 100-200+ seconds per full detect+recognize
        cycle (gaps measured directly from its own detection_events), even
        though it was no longer literally starved by other cameras like
        before — an even split just moved the bottleneck from "shared queue"
        to "too few cores for this specific camera's cost". Weighting that
        camera 2x is what actually restores a reasonable cycle time; a
        default-tuned camera never needed more than its even share to begin
        with."""
        if not camera_ids:
            return {}
        weights = {cid: (2 if cid in CAMERA_DETECTION_MAX_DIM else 1) for cid in camera_ids}
        total_weight = sum(weights.values())
        raw = {cid: WORKER_MAX_CPU_CORES * w / total_weight for cid, w in weights.items()}
        allocation = {cid: max(1, int(v)) for cid, v in raw.items()}
        # floor()'ing each share can under-allocate the total budget by a few
        # cores — hand those out one at a time to whichever camera(s) lost the
        # most to flooring, so the full budget is actually used.
        leftover = WORKER_MAX_CPU_CORES - sum(allocation.values())
        by_remainder = sorted(camera_ids, key=lambda cid: raw[cid] - int(raw[cid]), reverse=True)
        i = 0
        while leftover > 0 and by_remainder:
            allocation[by_remainder[i % len(by_remainder)]] += 1
            leftover -= 1
            i += 1
        return allocation

    def _refresh_footfall_cameras(self) -> None:
        resolved = resolve_footfall_camera_ids(camera_db.list_cameras())
        if resolved != self._footfall_camera_ids:
            self._footfall_camera_ids = resolved
            logger.info("Footfall counting enabled on camera ID(s): %s", sorted(resolved) or "none")

    def refresh_desk_zones(self) -> None:
        """Call after any /api/desk-zones CRUD so a new/edited/deleted zone
        takes effect immediately."""
        if self._desk_tracker is not None:
            self._desk_tracker.refresh_zones()

    def get_desk_status(self) -> dict[str, dict]:
        """Live (right-now) per-employee desk status — see
        DeskTracker.get_live_status(). Only meaningful for "today"; the
        Desk Analytics report merges this in for today's date only."""
        if self._desk_tracker is None:
            return {}
        return self._desk_tracker.get_live_status()

    def refresh_footfall_gate(self) -> None:
        """Call after any /api/footfall-gate CRUD so a new/edited/deleted
        gate line takes effect immediately."""
        if self._gate_tracker is not None:
            self._gate_tracker.refresh_gates()

    def start(self) -> None:
        self._footfall_counter = FootfallCounter()
        self._refresh_footfall_cameras()
        self._desk_tracker = DeskTracker()
        self._gate_tracker = GateTracker(footfall_gate_db)

        live_cameras = [cam for cam in camera_db.list_cameras() if self._should_be_live(cam)]
        allocation = self._worker_core_allocation([cam["id"] for cam in live_cameras])
        for cam in live_cameras:
            self._start_camera(cam["id"], allocation[cam["id"]])

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
        for camera_id in list(self._worker_processes):
            self._stop_worker(camera_id)
        for pipeline in self._pipelines.values():
            pipeline.stop()
        self._pipelines.clear()
        self._conn_keys.clear()

    def _connection_key(self, camera_id: int) -> tuple | None:
        cam = camera_db.get_camera_connection(camera_id)
        if cam is None:
            return None
        return (cam["host"], cam["port"], cam["user"], cam["password"], cam["stream_path"])

    def _start_worker(self, camera_id: int, cores: int) -> None:
        input_queue: multiprocessing.Queue = multiprocessing.Queue(maxsize=10)
        worker = multiprocessing.Process(
            target=run_worker,
            args=(input_queue, self._result_queue, self._embed_response_queue, self._embed_request_queue, cores),
            daemon=True,
        )
        worker.start()
        self._input_queues[camera_id] = input_queue
        self._worker_processes[camera_id] = worker
        self._worker_cores[camera_id] = cores
        logger.info("Camera %s: detection worker process started (pid %s)", camera_id, worker.pid)
        self._pin_worker_cpu_affinity(worker.pid, cores)

    def _stop_worker(self, camera_id: int) -> None:
        worker = self._worker_processes.pop(camera_id, None)
        if worker is not None:
            worker.terminate()
            worker.join(timeout=5)
        self._input_queues.pop(camera_id, None)
        self._worker_cores.pop(camera_id, None)

    def _check_worker_health(self) -> None:
        """Detection worker crashes happen with no Python exception, no OOM,
        and no log line at all (confirmed live: a native-level crash in a
        library like onnxruntime/opencv bypasses Python's exception handling
        entirely) — multiprocessing.Process has no built-in health check, so
        a dead worker previously left its camera silently unrecognized
        forever: the last cached detection result just never updated again,
        indistinguishable from "nobody's in frame" from the API alone, until
        someone noticed and manually restarted the whole backend. Runs from
        _sender_loop on WORKER_HEALTH_CHECK_INTERVAL_SECONDS; is_alive() is
        cheap enough to not matter added to that loop's per-tick cost."""
        for camera_id, worker in list(self._worker_processes.items()):
            if worker.is_alive():
                continue
            cores = self._worker_cores.get(camera_id, WORKER_MAX_CPU_CORES)
            logger.error(
                "Camera %s: detection worker (pid %s) is not running (exit code %s) — restarting it",
                camera_id, worker.pid, worker.exitcode,
            )
            self._stop_worker(camera_id)
            self._start_worker(camera_id, cores)

    def _start_camera(self, camera_id: int, worker_cores: int = WORKER_MAX_CPU_CORES) -> None:
        if camera_id in self._pipelines:
            return
        pipeline = CameraPipeline(camera_id)
        pipeline.start()
        self._pipelines[camera_id] = pipeline
        self._conn_keys[camera_id] = self._connection_key(camera_id)
        # Not restarted on a plain reconnect (see refresh_cameras) — the worker
        # process holds no RTSP connection state, only the capture pipeline
        # above does, so an already-running worker for this camera stays put.
        if camera_id not in self._worker_processes:
            self._start_worker(camera_id, worker_cores)

    def refresh_cameras(self) -> None:
        """Call after camera CRUD changes to start/stop pipelines accordingly.

        Also reconnects any already-active camera whose connection details
        (host/port/user/password/stream_path) changed — otherwise editing
        just the password would leave the pipeline running on its already
        -open, old-credentials connection until that connection happened to
        drop on its own."""
        self._refresh_footfall_cameras()
        cameras = {c["id"]: c for c in camera_db.list_cameras()}

        for camera_id in list(self._pipelines):
            cam = cameras.get(camera_id)
            if cam is None or not self._should_be_live(cam):
                self._pipelines.pop(camera_id).stop()
                self._conn_keys.pop(camera_id, None)
                self._stop_worker(camera_id)
            elif self._connection_key(camera_id) != self._conn_keys.get(camera_id):
                logger.info("Camera %s: connection details changed, reconnecting", camera_id)
                self._pipelines.pop(camera_id).stop()
                self._conn_keys.pop(camera_id, None)

        live_ids = [cam["id"] for cam in cameras.values() if self._should_be_live(cam)]
        allocation = self._worker_core_allocation(live_ids)
        for cam in cameras.values():
            if self._should_be_live(cam):
                self._start_camera(cam["id"], allocation[cam["id"]])

    def _sender_loop(self) -> None:
        """Feeds the detection worker JPEG bytes at the user-configurable
        detection_fps rate (see /api/settings) — cheap enqueueing only,
        never inference, so this can't block anything downstream."""
        interval = 1 / DEFAULT_DETECTION_FPS
        last_settings_check = 0.0
        last_health_check = 0.0

        while self._running:
            now = time.time()
            if now - last_settings_check >= SETTINGS_POLL_INTERVAL_SECONDS:
                last_settings_check = now
                try:
                    fps = float(alerts_db.get_setting("detection_fps", str(DEFAULT_DETECTION_FPS)))
                    interval = 1 / fps if fps > 0 else 1 / DEFAULT_DETECTION_FPS
                except (TypeError, ValueError):
                    interval = 1 / DEFAULT_DETECTION_FPS

            if now - last_health_check >= WORKER_HEALTH_CHECK_INTERVAL_SECONDS:
                last_health_check = now
                self._check_worker_health()

            for camera_id, pipeline in list(self._pipelines.items()):
                jpeg = pipeline.get_latest_jpeg()
                if jpeg is None:
                    continue
                input_queue = self._input_queues.get(camera_id)
                if input_queue is None:
                    continue
                try:
                    input_queue.put_nowait(
                        {"type": "detect", "camera_id": camera_id, "jpeg": jpeg, "enqueued_at": now}
                    )
                    recognition_logger.debug("camera=%s stage=frame_selected sent_to_worker=1", camera_id)
                except queue.Full:
                    recognition_logger.debug("camera=%s stage=frame_selected sent_to_worker=0 reason=queue_full", camera_id)

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
            if "faces" in result:
                recognition_logger.debug(
                    "camera=%s stage=response_received face_count=%s", result["camera_id"], len(result["faces"]),
                )
            self._dispatch_result(result)

    def _dispatch_result(self, result: dict) -> None:
        camera_id = result["camera_id"]
        pipeline = self._pipelines.get(camera_id)
        if pipeline is None:
            return

        # Hoisted to method scope (not just the "faces" block below) so the
        # pose-based footfall_events fallback further down can also see it —
        # a camera with a gate line configured must never ALSO log crossings
        # from the old, much-less-reliable pose-based midline tracker, or
        # the same real crossing could be counted twice.
        gate_active = False

        if "faces" in result:
            frame_w, frame_h = result.get("frame_size", (0, 0))

            # Footfall gate-line crossing (see gate_tracker.py) needs the
            # embedding, so it runs BEFORE the pop below strips it. gate_active
            # is True iff this camera has a gate line configured at all — in
            # that case it already called footfall_counter.process() itself
            # for "in" crossings, so the whole-frame fallback below must not
            # ALSO count every frame a face happens to be visible. gate_events
            # is "in"/"out" per actual crossing (both directions), logged the
            # same way person_tracker.py's pose-based crossings already are.
            if (
                self._gate_tracker is not None
                and self._footfall_counter is not None
                and camera_id in self._footfall_camera_ids
            ):
                gate_active, gate_events = self._gate_tracker.process_frame(
                    camera_id, result["faces"], frame_w, frame_h, self._footfall_counter,
                )
                for direction in gate_events:
                    face_db.log_footfall(camera_id, direction)

            for face in result["faces"]:
                # Pop rather than leave in place: this same dict is stored as
                # this camera's "latest detections" below and handed straight
                # to the /ws/detections websocket as JSON — a raw numpy
                # embedding array is neither JSON-serializable nor something
                # that should leave the backend as a live-view payload.
                embedding = face.pop("embedding", None)
                # Gated to configured entry/exit gate camera(s) only (see
                # config.FOOTFALL_CAMERAS) — footfall counting does not run
                # on every camera in the system by default. Whole-frame
                # fallback only when that camera has no gate line drawn yet;
                # once one exists, gate_active already handled counting above.
                if (
                    embedding is not None
                    and self._footfall_counter is not None
                    and camera_id in self._footfall_camera_ids
                    and not gate_active
                ):
                    self._footfall_counter.process(camera_id, embedding, name=face.get("name"))

            if self._desk_tracker is not None:
                self._desk_tracker.process_frame(camera_id, result["faces"], frame_w, frame_h)

            self._check_zone_violations(camera_id, result["faces"], result.get("jpeg"))
            pipeline.set_detections(result["faces"])

        if "people" in result and self._desk_tracker is not None:
            frame_w, frame_h = result.get("frame_size", (0, 0))
            self._desk_tracker.process_pose_frame(camera_id, result["people"], frame_w, frame_h)

        # Skipped when a gate line is active for this camera -- gate_tracker
        # already logged both directions above from the same frame's faces,
        # far more reliably (1s cadence vs pose's 20s); logging this too
        # would double-count the same real crossing on cameras that have
        # both a gate line and pose-based tracking running.
        if not gate_active:
            for direction in result.get("footfall_events", []):
                face_db.log_footfall(camera_id, direction)

        if "fire_smoke" in result:
            pipeline.set_fire_smoke(result["fire_smoke"])

        # Smoke alerting was briefly disabled outright after false positives
        # on "exit" and "Technical section" (2026-09-01) — re-enabled here
        # since the actual fix is tightening fire_smoke_detector.py's smoke
        # thresholds (SMOKE_MIN_FILL_RATIO, SMOKE_GROWTH_RATIO,
        # SMOKE_CONSECUTIVE_SAMPLES) against the real desk-clutter blob that
        # caused them, not suppressing the alert. See that module for the
        # evidence behind each change.
        for event_type in result.get("fire_smoke_events", []):
            if not alerts_db.recent_open_alert(camera_id, event_type, FIRE_SMOKE_ALERT_COOLDOWN_SECONDS):
                # fire_smoke_detector's own per-box "score" (see its update())
                # is the only confidence signal this heuristic detector has —
                # take the strongest matching box from the SAME frame that
                # just confirmed this event, not an average across history.
                matching_scores = [
                    b["score"] for b in result.get("fire_smoke", []) if b.get("type") == event_type
                ]
                confidence_pct = round(max(matching_scores) * 100) if matching_scores else None
                confidence_note = f" (confidence: {confidence_pct}%)" if confidence_pct is not None else ""
                snapshot_path = self._save_alert_snapshot(
                    camera_id, event_type, result.get("jpeg") or pipeline.get_latest_jpeg()
                )
                alerts_db.log_alert(
                    camera_id,
                    event_type,
                    f"Possible {event_type} detected on camera{confidence_note}",
                    snapshot_path=snapshot_path,
                )
                alert_events.broadcast()
                logger.warning("Camera %s: possible %s detected%s", camera_id, event_type, confidence_note)
            if event_type == "smoke":
                pipeline.note_smoke_event()

        # Fall-detection alerting disabled per explicit request — it was
        # firing repeatedly (false positives) on the busy "exit" camera,
        # which is also used for footfall counting. Pose detection still
        # runs (person_tracker.py) for footfall's midline-crossing count;
        # only the "fall" alert itself is suppressed.

        if result.get("person_count", 0) > 0 and self._is_within_restricted_window():
            if not alerts_db.recent_open_alert(camera_id, "intrusion", INTRUSION_ALERT_COOLDOWN_SECONDS):
                alerts_db.log_alert(camera_id, "intrusion", "Person present during restricted hours")
                alert_events.broadcast()
                logger.warning("Camera %s: intrusion during restricted hours", camera_id)

    @staticmethod
    def _point_in_zone(point: tuple[float, float], polygon_points: list) -> bool:
        contour = np.array(polygon_points, dtype=np.int32).reshape((-1, 1, 2))
        return cv2.pointPolygonTest(contour, point, False) >= 0

    def _check_zone_violations(self, camera_id: int, faces: list[dict], jpeg: bytes | None) -> None:
        """Restricted-zone allow-list check: anyone (a different enrolled
        person, or an unrecognized face) detected inside a zone's polygon
        who isn't on that zone's allowed_names list raises a zone_intrusion
        alert, pushed to every connected client immediately (alert_events.
        broadcast() below) — never a timer or a page refresh. Test point is
        each face's bbox center — the only real-time, per-identity position
        signal available (pose/body bbox only runs every
        detection_worker.POSE_INTERVAL_SECONDS and carries no name). A zone
        with both restricted_start/restricted_end set only enforces during
        that window; leaving them blank means the allow-list applies at any
        time.

        Never waits on anything beyond this same cycle's already-completed
        recognition: `name` here is whatever detection_worker's recognizer
        already decided for THIS frame (confidently matched, or "Unknown")
        — there's no separate slower identity step this blocks on. Two
        follow-on behaviors close the gap that leaves, though: (1) entry/
        exit tracking via self._zone_occupancy means someone continuously
        present never re-fires, but leaving and returning fires again
        immediately, not after a multi-minute cooldown; (2) if recognition
        resolves "Unknown" -> a real name a cycle or two later (the same
        physical entry, now identified), upgrade_unknown_zone_alert updates
        that SAME alert row instead of logging a second, duplicate one."""
        zones = zones_db.list_zones(camera_id=camera_id)
        if not zones:
            return
        now = time.time()
        for zone in zones:
            if not zone["enabled"]:
                continue
            if zone.get("restricted_start") and zone.get("restricted_end"):
                if not self._time_in_window(zone["restricted_start"], zone["restricted_end"]):
                    continue
            for face in faces:
                x1, y1, x2, y2 = face["bbox"]
                point = ((x1 + x2) / 2, (y1 + y2) / 2)
                if not self._point_in_zone(point, zone["polygon"]):
                    continue
                name = face["name"]
                if name in zone["allowed_names"]:
                    continue
                face["zone_violation"] = True
                # Per-cycle trace (fires every ~1s a violator stays put) —
                # DEBUG only, or a busy zone would flood production logs;
                # set LOG_LEVEL=DEBUG to see the full detection-to-dashboard
                # path from your example.
                logger.debug(
                    "[%s] Camera %s: zone intrusion confirmed in '%s' - identity=%s, Allow List check: NOT ALLOWED",
                    _ts_ms(), camera_id, zone["name"], name,
                )

                occ_key = (camera_id, zone["id"], name)
                last_inside = self._zone_occupancy.get(occ_key)
                self._zone_occupancy[occ_key] = now
                if last_inside is not None and now - last_inside <= ZONE_EXIT_GRACE_SECONDS:
                    continue  # same, already-alerted presence — not a fresh entry, no new event

                who = name if name != "Unknown" else "Unknown Person"
                message = f"{who} detected in restricted zone '{zone['name']}'"

                if name != "Unknown" and alerts_db.upgrade_unknown_zone_alert(
                    camera_id, zone["id"], name, message, within_seconds=UNKNOWN_UPGRADE_WINDOW_SECONDS
                ):
                    logger.info(
                        "[%s] Camera %s: recognition resolved Unknown -> %s in '%s' - upgraded existing alert "
                        "(no duplicate)", _ts_ms(), camera_id, name, zone["name"],
                    )
                    alert_events.broadcast()
                    continue

                pipeline = self._pipelines.get(camera_id)
                snapshot_path = self._save_alert_snapshot(
                    camera_id, f"zone{zone['id']}", jpeg or (pipeline.get_latest_jpeg() if pipeline else None)
                )
                alerts_db.log_alert(
                    camera_id,
                    "zone_intrusion",
                    message,
                    zone_id=zone["id"],
                    person_name=name,
                    snapshot_path=snapshot_path,
                )
                logger.info("[%s] Camera %s: intrusion event created - %s", _ts_ms(), camera_id, message)
                alert_events.broadcast()
                logger.warning("Camera %s: zone violation in '%s' by %s", camera_id, zone["name"], name)

    @staticmethod
    def _save_alert_snapshot(camera_id: int, label: str, jpeg: bytes | None) -> str | None:
        """Best-effort only, same as detection_worker._save_fire_smoke_debug_frame
        — a failure here should never take down alerting over an evidence frame."""
        if not jpeg:
            return None
        try:
            ALERT_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
            path = ALERT_SNAPSHOT_DIR / f"cam{camera_id}_{label}_{int(time.time() * 1000)}.jpg"
            path.write_bytes(jpeg)
            return str(path)
        except Exception:
            return None

    @staticmethod
    def _time_in_window(start_str: str | None, end_str: str | None) -> bool:
        if not start_str or not end_str:
            return False
        now = datetime.now().time()
        start = datetime.strptime(start_str, "%H:%M").time()
        end = datetime.strptime(end_str, "%H:%M").time()
        if start <= end:
            return start <= now <= end
        return now >= start or now <= end

    @classmethod
    def _is_within_restricted_window(cls) -> bool:
        return cls._time_in_window(alerts_db.get_setting("restricted_start"), alerts_db.get_setting("restricted_end"))

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
            # not ours — a concurrent compute_embedding() call (e.g. a manual
            # enrollment landing mid camera Allow List sync) is waiting on
            # this one. Put it back instead of dropping it, or that caller
            # would spin until its own deadline and wrongly report "no face
            # detected" even though the worker answered it just fine.
            self._embed_response_queue.put(resp)
        return None

    def reload_faces(self) -> None:
        for input_queue in self._input_queues.values():
            input_queue.put({"type": "reload_faces"})
        logger.info("Signaled %d detection worker(s) to reload enrolled faces", len(self._input_queues))

    def get_latest_jpeg(self, camera_id: int) -> bytes | None:
        pipeline = self._pipelines.get(camera_id)
        return pipeline.get_latest_jpeg() if pipeline else None

    def get_latest_detections(self, camera_id: int) -> list[dict]:
        pipeline = self._pipelines.get(camera_id)
        return pipeline.get_latest_detections() if pipeline else []

    def get_latest_fire_smoke(self, camera_id: int) -> list[dict]:
        pipeline = self._pipelines.get(camera_id)
        return pipeline.get_latest_fire_smoke() if pipeline else []

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
                if name == SMOKE_CLIP_SUBJECT:
                    continue
                result.append({
                    "person_name": name,
                    "camera_id": camera_id,
                    "camera_name": cameras_by_id.get(camera_id, "—"),
                })
        return result


pipeline_manager = PipelineManager()
