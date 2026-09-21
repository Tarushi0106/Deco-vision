import json
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

CAMERA_HOST = os.getenv("CAMERA_HOST", "")
CAMERA_RTSP_PORT = int(os.getenv("CAMERA_RTSP_PORT", "554"))
CAMERA_USER = os.getenv("CAMERA_USER", "")
CAMERA_PASSWORD = os.getenv("CAMERA_PASSWORD", "")
CAMERA_STREAM_PATH = os.getenv("CAMERA_STREAM_PATH", "/h264/ch1/sub/av_stream")
CAMERA_ADMIN_PORT = int(os.getenv("CAMERA_ADMIN_PORT", "443"))

# Camera 2 ("Main gate camera") and camera 3 ("Technical section") are two
# RTSP channels off the same NVR unit at CAMERA2_HOST — same login as camera
# 1 (CAMERA_USER/CAMERA_PASSWORD above), just different ports per channel.
CAMERA2_HOST = os.getenv("CAMERA2_HOST", "")
CAMERA2_RTSP_PORT = int(os.getenv("CAMERA2_RTSP_PORT", "554"))
CAMERA2_ADMIN_PORT = int(os.getenv("CAMERA2_ADMIN_PORT", "443"))

CAMERA3_HOST = os.getenv("CAMERA3_HOST", "")
CAMERA3_RTSP_PORT = int(os.getenv("CAMERA3_RTSP_PORT", "554"))
CAMERA3_ADMIN_PORT = int(os.getenv("CAMERA3_ADMIN_PORT", "443"))

SERVER_HOST = os.getenv("SERVER_HOST", "127.0.0.1")
SERVER_PORT = int(os.getenv("SERVER_PORT", "8811"))

# Many RTSP cameras/NVRs never reply over UDP from behind NAT/firewalls,
# which makes OpenCV's ffmpeg backend fail to open the stream with no
# useful error - forcing TCP fixes that for the large majority of devices.
RTSP_TRANSPORT = os.getenv("RTSP_TRANSPORT", "tcp")
# fflags;nobuffer + flags;low_delay + max_delay;0: ffmpeg's RTSP demuxer
# otherwise keeps its own internal jitter/probe buffer on top of whatever
# OpenCV does (CAP_PROP_BUFFERSIZE only controls OpenCV's own queue, not
# ffmpeg's) - that's real, observed latency a purpose-built live-viewing
# NVR client doesn't have on the identical camera/network, since it isn't
# using ffmpeg's general-purpose (buffer-for-seekability) defaults.
#
# max_delay;0 means zero tolerance for any out-of-order/late packet — fine
# on a short, low-jitter path, but diagnosed live on a longer network path
# (AWS server, further from the camera than a local machine) causing real
# HEVC decode corruption: "Could not find ref with POC N" / "Error
# constructing the frame RPS" repeating in the raw ffmpeg log, because a
# reordered reference frame gets dropped instead of waited for. Overridable
# per-deployment via RTSP_FFMPEG_OPTIONS so a longer/jitterier path can add a
# small reorder buffer without changing the aggressive default everywhere
# (e.g. this machine's own direct connection, where this hasn't been an
# issue) — set to a plain string like
# "rtsp_transport;tcp|max_delay;500000" to allow ~0.5s of reorder tolerance.
_DEFAULT_FFMPEG_OPTIONS = f"rtsp_transport;{RTSP_TRANSPORT}|fflags;nobuffer|flags;low_delay|max_delay;0"
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = os.getenv("RTSP_FFMPEG_OPTIONS", _DEFAULT_FFMPEG_OPTIONS)

# Cameras confirmed to support on-demand playback from their own onboard
# recording (ONVIF Profile G / Replay - see onvif_client.py), mapped to the
# recording's ONVIF channel number. Only camera 1 is in here: validated
# live this session (real HEVC+audio pulled via ffmpeg for an exact
# requested time window). Cameras 2/3 live on a different physical device
# whose ONVIF replay support was NOT confirmed (its ONVIF port didn't
# respond the same way camera 1's did) - they keep local self-recording
# (pipeline.py) until/unless that's verified too. A camera in this dict
# skips local recording entirely; clips are fetched from the camera only
# when actually played, never stored permanently on this machine.
CAMERA_ONVIF_REPLAY_CHANNEL = {
    1: 1,
}

# Unique footfall (people counting, see footfall_counter.py) is opt-in per
# camera, not automatic for every camera in the system: comma-separated list
# of entry/exit camera identifiers to run it on. Each entry may be a numeric
# camera ID, a cam_code, or a (case/spacing-insensitive) substring of the
# camera's name — e.g. "main_gate" matches a camera named "Main gate camera".
# Empty (the default) means footfall counting is disabled everywhere until a
# gate camera is explicitly named here.
FOOTFALL_CAMERAS = os.getenv("FOOTFALL_CAMERAS", "")

# How long a face embedding stays valid for re-identification before a
# re-appearance at the same camera counts as a brand-new visit.
FOOTFALL_REID_WINDOW_MINUTES = float(os.getenv("FOOTFALL_REID_WINDOW_MINUTES", "10"))

# Cosine similarity floor for matching two embeddings as the same person —
# only consulted for a face with no recognized name (footfall_counter.py
# prefers matching by name outright when one's available, since it's far
# more reliable). Measured live on the Entry/Exit camera: this camera's own
# same-person embedding pairs ranged 0.20-0.72 (median 0.36) while
# different-person pairs ranged up to 0.385 (99th percentile ~0.30) — the
# distributions genuinely overlap, so no value here is exact; 0.30 leans
# toward not merging two different anonymous people rather than toward
# catching every re-appearance of the same one.
FOOTFALL_SIMILARITY_THRESHOLD = float(os.getenv("FOOTFALL_SIMILARITY_THRESHOLD", "0.30"))

# When the end-of-day footfall report job (scheduler.py) runs, as "HH:MM" —
# shortly after midnight by default so it finalizes the day that just ended.
FOOTFALL_REPORT_FINALIZE_TIME = os.getenv("FOOTFALL_REPORT_FINALIZE_TIME", "00:05")

# Rolling local storage window for recognition clips (see clips_db.py's
# delete_expired_clips, run daily by scheduler.py): a clip and its video
# file are deleted once older than this, on every camera. Note this is a
# ceiling on what OUR storage keeps, not a guarantee - camera 1's own
# onboard recording (the source replay_prefetch.py / on-demand fetches pull
# from) independently only holds ~3 days before it overwrites itself
# (confirmed live), so its practical availability window is whichever is
# smaller: this setting, or however far back the camera's own memory still
# reaches. Self-recording cameras (anything NOT in CAMERA_ONVIF_REPLAY_
# CHANNEL) save locally as they're recorded, so for them this setting is
# the real ceiling.
CLIP_RETENTION_DAYS = int(os.getenv("CLIP_RETENTION_DAYS", "7"))

# When the daily clip-retention prune job (scheduler.py) runs, as "HH:MM" -
# shortly after the footfall finalize job so both maintenance jobs land
# together just after midnight.
CLIP_RETENTION_PRUNE_TIME = os.getenv("CLIP_RETENTION_PRUNE_TIME", "00:15")

# License & Camera Access Management (auth.py / license_db.py): JWT signing
# secret for that module's real password-based login — separate from the
# existing trivial /api/auth/login (user_db.record_login), which has no
# password and stays exactly as-is for the main dashboard's "who's using
# this" tracking. Persisted to a file rather than regenerated per process,
# so a backend restart doesn't invalidate every signed-in session (this app
# gets restarted often during development) - only used if JWT_SECRET isn't
# set in the environment, which is the recommended path for production.
_JWT_SECRET_FILE = Path(__file__).resolve().parent.parent / "data" / "jwt_secret.key"


def _load_or_create_jwt_secret() -> str:
    env_secret = os.getenv("JWT_SECRET")
    if env_secret:
        return env_secret
    _JWT_SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    if _JWT_SECRET_FILE.exists():
        return _JWT_SECRET_FILE.read_text().strip()
    secret = secrets.token_hex(32)
    _JWT_SECRET_FILE.write_text(secret)
    return secret


JWT_SECRET = _load_or_create_jwt_secret()
JWT_ALGORITHM = "HS256"
JWT_ACCESS_TOKEN_MINUTES = int(os.getenv("JWT_ACCESS_TOKEN_MINUTES", "60"))

# Bootstrap Super Admin (user_db.py's init_db creates this account if no
# super_admin exists yet) - otherwise there'd be no way to sign into the
# License module at all on a fresh database. Change the password after
# first login; this is a development-friendly default, not a production
# secret.
SUPER_ADMIN_EMAIL = os.getenv("SUPER_ADMIN_EMAIL", "admin@deco-vision.local")
SUPER_ADMIN_PASSWORD = os.getenv("SUPER_ADMIN_PASSWORD", "ChangeMe123!")

# Rate limiting (slowapi, in-memory — no Redis dependency at this scale)
# for the License module's public-ish endpoints (login, activation), which
# are the ones worth throttling against brute-force/abuse.
AUTH_RATE_LIMIT = os.getenv("AUTH_RATE_LIMIT", "10/minute")

# Desk-time analytics (see desk_tracker.py): how long an employee can go
# unconfirmed at their desk zone — by face OR by the pose-tracking bridge —
# before that stretch of presence ends and they're marked Away. Long enough
# that one missed detection cycle (face recognition runs ~1x/sec via
# detection_fps) doesn't fragment one sitting into several; short enough
# that a real "got up and left" registers as Away within a reasonable time.
DESK_SESSION_GRACE_SECONDS = int(os.getenv("DESK_SESSION_GRACE_SECONDS", "20"))

# --- Recognition pipeline tuning (all environment-overridable) ----------
# These were previously hardcoded constants scattered across detection_worker.py
# / pipeline.py / recognizer.py, each tuned against a specific real camera this
# session (see the git history / inline comments in those files for the exact
# measurements behind each default below). Centralizing them here as env vars
# means retuning for a different camera, a different physical install, or
# different deployment hardware never requires editing the recognition code
# itself. The defaults below reproduce exactly what was already running —
# setting no env vars changes no behavior.

# Cosine-similarity floor for a face embedding to count as a recognized match
# (recognizer.py). Below this, a face is reported as "Unknown" regardless of
# whose embedding it's closest to.
#
# Confirmed live there is NO clean separation between "correct match" and
# "wrong match" scores with the current single-reference-photo-per-person
# enrollment (38 people): 0.55 was tried and left almost nothing recognized
# at all (observed max score was ~0.541 across a full session of live
# traffic); 0.30 let clearly wrong matches through. 0.40 is a pragmatic
# middle ground, not a value backed by a clean threshold in the data — real
# improvement here needs multiple/better-quality reference photos per
# person, not a different single number.
RECOGNITION_SIMILARITY_THRESHOLD = float(os.getenv("RECOGNITION_SIMILARITY_THRESHOLD", "0.40"))

# Face-detector confidence floor used when no per-camera override applies
# (detection_worker.py's CAMERA_DET_THRESH still takes priority for cameras
# 1/2 — see CAMERA_DET_THRESH_JSON below to override those too).
RECOGNITION_DET_THRESH_DEFAULT = float(os.getenv("RECOGNITION_DET_THRESH_DEFAULT", "0.65"))

# Optional JSON object mapping camera_id -> detection threshold, e.g.
# '{"1": 0.5, "2": 0.45}', to override detection_worker.py's measured
# per-camera defaults without touching code. Unset (the default) keeps those
# measured values exactly as they are.
_camera_det_thresh_json = os.getenv("CAMERA_DET_THRESH_JSON")
CAMERA_DET_THRESH_OVERRIDES = (
    {int(k): float(v) for k, v in json.loads(_camera_det_thresh_json).items()} if _camera_det_thresh_json else None
)

# Optional JSON object mapping camera_id -> detection resolution (longer side,
# px), e.g. '{"2": 1280}', overriding detection_worker.py's
# CAMERA_DETECTION_MAX_DIM. Unset keeps the measured per-camera defaults.
_camera_max_dim_json = os.getenv("CAMERA_DETECTION_MAX_DIM_JSON")
CAMERA_DETECTION_MAX_DIM_OVERRIDES = (
    {int(k): int(v) for k, v in json.loads(_camera_max_dim_json).items()} if _camera_max_dim_json else None
)

# How many of a frame's largest not-yet-matched faces get a second, full-
# resolution recognition pass (detection_worker.py) — the expensive step that
# fixes small/distant faces scoring far lower than their true similarity.
# NOT lowered to save CPU: confirmed live on a real 6-person frame that
# rechecking only the closest 3 faces missed 2 of 3 real, strong matches —
# see this constant's other use site in detection_worker.py for that
# measurement. recognition_track_cache.py is what actually cuts recheck
# volume now (an already-confirmed or already-recently-tried face doesn't
# consume a slot at all), not a lower ceiling here.
RECOGNITION_MAX_FULL_RES_RECHECKS = int(os.getenv("RECOGNITION_MAX_FULL_RES_RECHECKS", "8"))
RECOGNITION_RECHECK_DET_THRESH = float(os.getenv("RECOGNITION_RECHECK_DET_THRESH", "0.3"))
RECOGNITION_RECHECK_CROP_PADDING = float(os.getenv("RECOGNITION_RECHECK_CROP_PADDING", "0.8"))

# recognition_track_cache.py: how long a confidently-recognized face is
# trusted before spending one recheck to reconfirm it, rather than paying
# for a fresh recheck every single cycle it stays in frame. Note: on a
# camera whose real per-cycle time already exceeds this (measured 24-100+s
# under load on the wide-angle "Main gate" camera — see
# RECOGNITION_TRACK_TIMEOUT_SECONDS below), this is effectively a no-op —
# expected, not a bug. The actual latency win on that camera comes from the
# recheck BUDGET no longer being spent on already-known faces at all
# (see RECOGNITION_MAX_FULL_RES_RECHECKS above), not from this interval,
# which mainly helps lighter-load cameras with faster cycles.
RECOGNITION_CACHE_REVERIFY_SECONDS = float(os.getenv("RECOGNITION_CACHE_REVERIFY_SECONDS", "10"))

# Same module, the opposite case: a tracked face that has never been
# confidently identified only gets another expensive recheck attempt this
# often, not on every single cycle — avoids repeatedly paying for a
# recheck on a real Unknown/unregistered visitor who was never going to
# match. No stale-identity risk here (unlike the reverify window above) —
# "still Unknown" is always safe to keep reporting while waiting.
RECOGNITION_RETRY_INTERVAL_SECONDS = float(os.getenv("RECOGNITION_RETRY_INTERVAL_SECONDS", "3"))

# How long a recognition_track_cache.py track survives with no matching
# detection before it's dropped (person left frame / long occlusion).
# Deliberately its OWN, much shorter constant than
# RECOGNITION_TRACK_TIMEOUT_SECONDS (180s, below) even though both are
# IoU-based face tracking: that 180s value was tuned for
# recognition_stabilizer.py, a purely cosmetic main-process layer where a
# stale cached identity only mislabels the overlay for a while. This
# cache's decisions also reach footfall/desk/zone-violation logic in
# pipeline.py (whatever name a track reports IS what those consume), so a
# wrong identity surviving 180s there risks a real desk-session or
# zone-alert misattribution, not just a mislabeled frame. Start
# conservative; widen only if live use shows tracks being needlessly
# recreated more than this protects against.
RECOGNITION_CACHE_TRACK_TIMEOUT_SECONDS = float(os.getenv("RECOGNITION_CACHE_TRACK_TIMEOUT_SECONDS", "20"))

# How many consecutive detection cycles the SAME name must appear on a camera
# before it's logged as a detection_event (attendance/analytics) — filters out
# a one-off spurious match (embedding noise on a single frame) without
# touching what the live overlay shows immediately. 1 (the default) reproduces
# the previous behavior exactly: any single hit logs, same as before this
# setting existed. Raise it to require the match to repeat before it's
# recorded; keep DETECTION_LOG_COOLDOWN_SECONDS below as the separate
# duplicate-suppression window for an already-confirmed, continuously-present
# person.
RECOGNITION_MIN_CONSECUTIVE_HITS = int(os.getenv("RECOGNITION_MIN_CONSECUTIVE_HITS", "1"))

# Once a name has been logged, don't log it again for the same camera more
# often than this — avoids flooding detection_events while someone stands
# continuously in frame.
DETECTION_LOG_COOLDOWN_SECONDS = int(os.getenv("DETECTION_LOG_COOLDOWN_SECONDS", "30"))

# How stale a camera's last-computed recognition result is allowed to look
# before the LIVE OVERLAY hides it, independent of how often the frontend
# happens to re-request it. Diagnosed live this session: /ws/detections
# polls get_latest_detections() every ~167ms (DETECTIONS_FPS=6 in main.py)
# regardless of whether the underlying result actually changed, so a
# frontend "clear if no message arrives for 1s" timer can never fire — a
# message always arrives. The camera's own compute cycle is what actually
# refreshes the data (measured: ~1-2s on a light scene, 8-11s on the busy
# Main gate camera's 6-8 person frame), so THAT is the real staleness clock.
# Each detections payload now carries computed_at (the timestamp
# set_detections() last ran) and the frontend clears locally once
# now - computed_at exceeds this, rather than relying on message silence.
# This does not — and physically cannot — make a busy multi-person scene's
# true removal latency faster than its own recognition cycle time; it only
# fixes the frontend showing data that's already known to be older than
# this, whatever the real cycle time turns out to be this cycle.
IDENTITY_LOST_TIMEOUT_SECONDS = float(os.getenv("IDENTITY_LOST_TIMEOUT_SECONDS", "1.0"))

# A flat IDENTITY_LOST_TIMEOUT_SECONDS alone caused a real, reported bug: on
# a camera whose actual recognition cycle takes longer than this (measured
# 8-12s on a busy multi-person scene), the still-present person's name
# flashed on for under a second and then disappeared every single cycle,
# even though they never left — the fixed timeout was simply shorter than
# the time between genuinely fresh results. CameraPipeline tracks each
# camera's own last real cycle gap and widens the effective timeout to
# max(IDENTITY_LOST_TIMEOUT_SECONDS, last_cycle_gap * this factor) — see
# get_effective_identity_lost_timeout(). >1 on purpose: cycle time varies
# cycle to cycle, so sizing the timeout to exactly the last gap would still
# false-clear whenever the next cycle happens to run a bit slower than the
# one before it.
IDENTITY_LOST_TIMEOUT_SAFETY_FACTOR = float(os.getenv("IDENTITY_LOST_TIMEOUT_SAFETY_FACTOR", "1.5"))

# How long a synchronous embedding request (enrollment / camera Allow List
# sync) waits for the detection worker to respond before giving up. Must
# comfortably exceed the worker's worst-case single-frame processing time on
# whatever hardware this is running on, or a slow (but eventually successful)
# detection gets wrongly reported as "no face detected".
DETECTION_EMBED_TIMEOUT_SECONDS = float(os.getenv("DETECTION_EMBED_TIMEOUT_SECONDS", "25"))

# How often the Honeywell recognition poller (honeywell_recognition_poller.py)
# queries each camera's own onboard SnapedFaces recognition log for new
# matches. This — not anything in the local detection pipeline — is what
# now decides how fast a recognized person shows up in Attendance/People
# Analytics, since identity comes from the camera's own engine rather than
# local ArcFace matching. A short interval (originally 2s) was tried and
# confirmed too aggressive: this device starts refusing/timing out new
# connections after just a handful of requests in quick succession, live-
# verified this session (a People List fetch — 3 requests — succeeded, but
# a SnapedFaces fetch moments later was refused, then timed out on retry).
# 20s matches the value already in production use as a stability mitigation.
HONEYWELL_POLL_INTERVAL_SECONDS = float(os.getenv("HONEYWELL_POLL_INTERVAL_SECONDS", "20"))

# How fresh a Honeywell recognition (face_db.detection_events,
# recognition_source='honeywell') must be for pipeline.py's
# CameraPipeline.set_detections to prefer it over the local worker's own
# match for the live overlay — only when exactly one face is in frame,
# since Honeywell's own events carry no bounding box and there's no way to
# tell which face a Honeywell identity belongs to once more than one
# person is present. Set comfortably above HONEYWELL_POLL_INTERVAL_SECONDS
# (20s) and the poller's own write-cooldown (DETECTION_LOG_COOLDOWN_SECONDS,
# 30s) — a shorter window would frequently find "nothing fresh enough" even
# for someone Honeywell recognized moments ago, purely from the poller's
# own cadence, silently defeating this most of the time. 0 disables the
# feature entirely (always falls back to local recognition).
HONEYWELL_LIVE_PREFERENCE_WINDOW_SECONDS = float(os.getenv("HONEYWELL_LIVE_PREFERENCE_WINDOW_SECONDS", "45"))

# The one Honeywell device treated as the authoritative source for the People
# List (see main.py's people/sync-from-camera). Enrolling people directly on
# this camera and re-syncing is meant to fully reconcile enrolled_faces
# (update existing, add new, remove anything no longer on this device's Allow
# List) — never a blind merge across every configured camera/device, which
# previously pulled in an unrelated device's stale Allow List alongside this
# one's real data.
PRIMARY_PEOPLE_SOURCE_HOST = os.getenv("PRIMARY_PEOPLE_SOURCE_HOST", "103.204.0.122")

# Honeywell recognition-poller reconnect backoff, mirroring
# CAMERA_RECONNECT_*_DELAY_SECONDS below but tracked per physical device host
# rather than per RTSP stream — a host that's failing backs off up to the max
# instead of being retried every HONEYWELL_POLL_INTERVAL_SECONDS regardless,
# and resets to base the moment a poll against it succeeds again.
HONEYWELL_RECONNECT_BASE_DELAY_SECONDS = float(os.getenv("HONEYWELL_RECONNECT_BASE_DELAY_SECONDS", "5"))
HONEYWELL_RECONNECT_MAX_DELAY_SECONDS = float(os.getenv("HONEYWELL_RECONNECT_MAX_DELAY_SECONDS", "120"))

# Diagnosed live (scripts/diagnose_honeywell.py, both from this machine and
# from AWS): this device reliably serves roughly one request per connection
# before resetting it, so a second failure right after a fresh relogin is
# expected device behavior, not something more retries would fix. Bounded at
# 2 (1 initial attempt + 1 reconnect-and-retry) rather than higher — a higher
# value would turn a device that's already struggling under one request into
# a request storm without meaningfully improving success odds; a
# persistently-down device should fail fast here and let the poller's own
# HONEYWELL_RECONNECT_*_DELAY_SECONDS backoff decide when to try again.
HONEYWELL_MAX_REQUEST_ATTEMPTS = int(os.getenv("HONEYWELL_MAX_REQUEST_ATTEMPTS", "2"))

# How often the recognition poller's in-memory Honeywell-person-ID -> name
# cache (built from our already-synced enrolled_faces, not a fresh camera API
# call) is refreshed on a timer, independent of the immediate on-demand
# refresh that already happens the moment an unresolved person ID is seen.
HONEYWELL_PEOPLE_CACHE_REFRESH_INTERVAL_SECONDS = float(
    os.getenv("HONEYWELL_PEOPLE_CACHE_REFRESH_INTERVAL_SECONDS", "300")
)

# Optional, off by default (None = disabled): Honeywell's recognition-score
# semantics have never been confirmed against real documentation, so this
# never discards a low-scoring event — Honeywell remains the recognition
# authority. If set, an event scoring below this is still written normally,
# just tagged recognition_source='low_confidence' instead of 'honeywell' for
# a reviewer to notice, rather than silently trusted or silently dropped.
_low_conf_raw = os.getenv("HONEYWELL_LOW_CONFIDENCE_THRESHOLD")
HONEYWELL_LOW_CONFIDENCE_THRESHOLD = float(_low_conf_raw) if _low_conf_raw else None

# RTSP reconnect backoff (pipeline.py): starts at the base delay, doubles on
# each consecutive failure up to the max, resets to base on a successful
# reconnect. Prevents a real outage from hammering the camera's own login
# endpoint (some devices self-lockout after repeated rapid auth failures).
CAMERA_RECONNECT_BASE_DELAY_SECONDS = float(os.getenv("CAMERA_RECONNECT_BASE_DELAY_SECONDS", "3"))
CAMERA_RECONNECT_MAX_DELAY_SECONDS = float(os.getenv("CAMERA_RECONNECT_MAX_DELAY_SECONDS", "60"))

# Total CPU core budget for the whole detection subsystem (all per-camera
# worker processes combined), split proportionally by relative cost — see
# PipelineManager._worker_core_allocation. Explicitly set
# DETECTION_WORKER_MAX_CPU_CORES to tune this for the actual deployment
# machine — see the deployment note on this in recognition_config's module
# docstring below. Left unset, the fallback below reserves 2 cores for
# everything that ISN'T detection (the API/WebSocket event loop, nginx,
# ffmpeg clip transcodes, the OS) rather than a fixed number that can equal
# — or exceed — a small box's entire core count. That exact bug shipped to
# a 4-vCPU EC2 instance with this defaulted to 4: the detection workers
# were allowed the whole machine, starving the API process and making
# every dashboard fetch slow regardless of how fast the DB or network was.
DETECTION_WORKER_MAX_CPU_CORES = int(
    os.getenv("DETECTION_WORKER_MAX_CPU_CORES", str(max(1, (os.cpu_count() or 4) - 2)))
)

# Default face-recognition sampling rate (frames/sec sent to the detection
# worker) before any /api/settings override — that DB-backed "detection_fps"
# setting (see pipeline.py's _sender_loop) already lets this be changed live
# from the UI without a restart; this env var only changes the fallback used
# before that setting has ever been saved.
DEFAULT_DETECTION_FPS = float(os.getenv("DEFAULT_DETECTION_FPS", "1"))

# --- Temporal identity stabilization (recognition_stabilizer.py) --------
# Every detection cycle matches faces independently -- nothing carries
# identity between cycles by default, so a single noisy frame can flip a
# confidently-recognized person to "Unknown" or a different name for one
# cycle, then flip back (the classic "Rahul, Unknown, Amit, Rahul" flicker).
# This tracks faces frame-to-frame by bounding-box overlap and only reports
# a name once it has a clear majority across the recent window, holding
# onto a stable identity through brief single-frame dips.
#
# Disabled by default (window=1, min_votes=1 is a no-op: every single vote
# already "wins" its window of size 1) so this ships without changing
# existing behavior; set RECOGNITION_STABILIZATION_ENABLED=true to turn it on.
RECOGNITION_STABILIZATION_ENABLED = os.getenv("RECOGNITION_STABILIZATION_ENABLED", "false").lower() == "true"
RECOGNITION_STABILIZATION_WINDOW = int(os.getenv("RECOGNITION_STABILIZATION_WINDOW", "5"))
RECOGNITION_STABILIZATION_MIN_VOTES = int(os.getenv("RECOGNITION_STABILIZATION_MIN_VOTES", "3"))
# Two detections across consecutive cycles are considered the same physical
# face if their boxes overlap at least this much (Intersection-over-Union).
RECOGNITION_TRACK_IOU_THRESHOLD = float(os.getenv("RECOGNITION_TRACK_IOU_THRESHOLD", "0.3"))
# How long a track survives with no matching detection before it's dropped
# (person left frame, or was fully occluded/undetected for a while).
# Confirmed live and the actual reason this feature failed its first
# production trial: this was left at 5s, assuming a detect cycle roughly
# matches detection_fps's ~1s interval -- but a real per-camera cycle
# (queue wait + recognize time) measured 24-100+ seconds on the wide-angle
# "Main gate" camera under normal load. Every track expired before it could
# accumulate votes, permanently forcing that camera to Unknown. Raised well
# past that camera's worst measured cycle time.
RECOGNITION_TRACK_TIMEOUT_SECONDS = float(os.getenv("RECOGNITION_TRACK_TIMEOUT_SECONDS", "180"))

# fire_smoke_detector.py is a classical HSV-color/flicker heuristic, not a
# trained model — confirmed live to false-positive on skin tone/warm-toned
# clothing under bright lighting when a face wasn't detected that exact
# frame (the face-exclusion zone it relies on needs a face box to exist).
# Turned off by default per that false positive; set back to true once a
# real trained fire/smoke model replaces this heuristic, or the thresholds
# are retuned against real fire footage.
FIRE_SMOKE_DETECTION_ENABLED = os.getenv("FIRE_SMOKE_DETECTION_ENABLED", "false").lower() == "true"

# How often detection_worker.py's pose pass (PoseDetector, YOLOv8n-pose)
# runs — drives the live person-box overlay's refresh rate (see pipeline.py/
# main.py's /ws/detections "persons" field) in addition to its original
# footfall/fall-detection use. Previously a hardcoded 20.0, raised from an
# original 5.0 after live CPU measurement showed 5s backed up the per-camera
# worker's input queue on THIS box (CPU-only, no CUDA — see detection_worker.py's
# comment history for the exact regression). Halved back to 10.0 here as a
# conservative, reversible step — still double the previously-measured-unsafe
# 5s — rather than reverting the full distance blind: this dev environment has
# no GPU to re-measure the real (onnxruntime-gpu-equipped) production cost
# against, so this is deliberately cautious, not a re-verified "safe" number.
# Override via this env var once real production hardware's own cost is
# measured, in either direction, without a code change.
PERSON_DETECTION_INTERVAL_SECONDS = float(os.getenv("PERSON_DETECTION_INTERVAL_SECONDS", "10"))

# --- Continuous face collection / manual labeling / retraining ----------
# See face_training_db.py and face_training_scheduler.py. Reuses the SAME
# embeddings detection_worker.py's face recognition pass already computes
# every cycle for Unknown faces (no second embedding-extraction pass, no
# second detector) — this just decides which of those already-computed
# results are worth keeping as future training data, and when to fold
# newly labeled ones into enrolled_faces (the same gallery recognizer.py
# already matches against — there is no separate model file).

# A face crop is only ever saved for storage consideration when its cosine
# distance from every other crop already saved for this camera within this
# window is large enough (see face_training_db's dedup check) — stops one
# person standing still from flooding storage with near-identical frames.
# Mirrors FOOTFALL_SIMILARITY_THRESHOLD's reasoning/measurement above,
# applied to the same kind of embedding comparison for a different purpose.
FACE_TRAINING_DEDUP_SIMILARITY = float(os.getenv("FACE_TRAINING_DEDUP_SIMILARITY", "0.90"))
FACE_TRAINING_DEDUP_WINDOW_SECONDS = float(os.getenv("FACE_TRAINING_DEDUP_WINDOW_SECONDS", "300"))

# Hard ceiling on pending (not-yet-labeled) samples kept per camera —
# protects disk space during a long unattended collection run; oldest
# pending samples are simply not replaced once full (existing ones are
# never deleted to make room, collection just pauses for that camera until
# the operator labels some down).
FACE_TRAINING_MAX_PENDING_PER_CAMERA = int(os.getenv("FACE_TRAINING_MAX_PENDING_PER_CAMERA", "500"))

# Background retraining trigger (face_training_scheduler.py): promote newly
# labeled samples into enrolled_faces once at least this many are waiting,
# OR this much time has passed since the last promotion with at least one
# labeled sample waiting — whichever comes first. Mirrors the same
# min-batch-or-max-interval pattern already used for other periodic jobs in
# this app (see scheduler.py), just on a much shorter interval since this
# one isn't a once-a-day job.
FACE_TRAINING_MIN_NEW_SAMPLES = int(os.getenv("FACE_TRAINING_MIN_NEW_SAMPLES", "10"))
FACE_TRAINING_CHECK_INTERVAL_SECONDS = float(os.getenv("FACE_TRAINING_CHECK_INTERVAL_SECONDS", "300"))
FACE_TRAINING_MAX_INTERVAL_SECONDS = float(os.getenv("FACE_TRAINING_MAX_INTERVAL_SECONDS", "21600"))  # 6h

# A candidate labeled sample is rejected (kept labeled, NOT promoted into
# enrolled_faces, flagged for operator review) if it matches a DIFFERENT
# already-enrolled person above this similarity — most likely a mislabel,
# and promoting it would actively degrade that other person's matching.
# Deliberately close to RECOGNITION_SIMILARITY_THRESHOLD's own reasoning
# above: if it's confusable with someone else at live-matching thresholds,
# it isn't safe to add.
FACE_TRAINING_VALIDATION_CONFLICT_THRESHOLD = float(
    os.getenv("FACE_TRAINING_VALIDATION_CONFLICT_THRESHOLD", "0.40")
)

# How often the sender loop checks whether each camera's detection worker
# process is still alive, and respawns it if not. Detection worker crashes
# (confirmed live: a native-level crash with no Python exception, no OOM,
# and no log line at all — multiprocessing.Process has no built-in health
# check or auto-restart) previously left a camera silently unrecognized
# indefinitely, invisible from the API (the last cached result just never
# updated again) until someone noticed and manually restarted the whole
# backend. This closes that gap without needing a full service restart.
WORKER_HEALTH_CHECK_INTERVAL_SECONDS = float(os.getenv("WORKER_HEALTH_CHECK_INTERVAL_SECONDS", "15"))

# Root log level for both the main API process (main.py) and each per-camera
# detection worker process (detection_worker.py runs in a separate OS
# process — see pipeline.py's module docstring — so it configures its own
# logging independently; this one env var controls both). The recognition
# pipeline's per-frame stage tracing (frame received -> sent to worker ->
# recognized -> stored) logs at DEBUG specifically so it stays silent by
# default and can be switched on for a session without a code change when
# actively diagnosing an accuracy/latency issue.
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
