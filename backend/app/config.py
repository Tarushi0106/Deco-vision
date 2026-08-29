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
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
    f"rtsp_transport;{RTSP_TRANSPORT}|fflags;nobuffer|flags;low_delay|max_delay;0"
)

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
