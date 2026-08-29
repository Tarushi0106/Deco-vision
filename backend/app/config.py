import os

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
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = f"rtsp_transport;{RTSP_TRANSPORT}"
