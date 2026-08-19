import os

from dotenv import load_dotenv

load_dotenv()

CAMERA_HOST = os.getenv("CAMERA_HOST", "")
CAMERA_RTSP_PORT = int(os.getenv("CAMERA_RTSP_PORT", "554"))
CAMERA_USER = os.getenv("CAMERA_USER", "")
CAMERA_PASSWORD = os.getenv("CAMERA_PASSWORD", "")
CAMERA_STREAM_PATH = os.getenv("CAMERA_STREAM_PATH", "/h264/ch1/sub/av_stream")
