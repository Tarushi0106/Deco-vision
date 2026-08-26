import os

from dotenv import load_dotenv

load_dotenv()

CAMERA_HOST = os.getenv("CAMERA_HOST", "")
CAMERA_RTSP_PORT = int(os.getenv("CAMERA_RTSP_PORT", "554"))
CAMERA_USER = os.getenv("CAMERA_USER", "")
CAMERA_PASSWORD = os.getenv("CAMERA_PASSWORD", "")
CAMERA_STREAM_PATH = os.getenv("CAMERA_STREAM_PATH", "/h264/ch1/sub/av_stream")
CAMERA_ADMIN_PORT = int(os.getenv("CAMERA_ADMIN_PORT", "443"))

SERVER_HOST = os.getenv("SERVER_HOST", "127.0.0.1")
SERVER_PORT = int(os.getenv("SERVER_PORT", "8811"))

# Many RTSP cameras/NVRs never reply over UDP from behind NAT/firewalls,
# which makes OpenCV's ffmpeg backend fail to open the stream with no
# useful error - forcing TCP fixes that for the large majority of devices.
RTSP_TRANSPORT = os.getenv("RTSP_TRANSPORT", "tcp")
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = f"rtsp_transport;{RTSP_TRANSPORT}"

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

# Desk-time analytics (see desk_tracker.py): how long an employee can go
# unconfirmed at their desk zone — by face OR by the pose-tracking bridge —
# before that stretch of presence ends and they're marked Away. Long enough
# that one missed detection cycle (face recognition runs ~1x/sec via
# detection_fps) doesn't fragment one sitting into several; short enough
# that a real "got up and left" registers as Away within a reasonable time.
DESK_SESSION_GRACE_SECONDS = int(os.getenv("DESK_SESSION_GRACE_SECONDS", "20"))
