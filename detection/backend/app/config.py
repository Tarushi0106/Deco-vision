"""Configuration for the standalone detection service — a separate
application from Parachute (backend/), not a module inside it. Every
setting is an env var with a sensible default, same convention as
Parachute's own config.py.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# --- Integration boundary with the existing Parachute application -------
# This service NEVER imports Parachute's Python modules directly (no
# shared process, no shared code) — it only talks to Parachute over the
# same public HTTP/WebSocket API a browser already uses:
#   GET  {PARACHUTE_API_BASE}/api/cameras   — which cameras exist / are live
#   GET  {PARACHUTE_API_BASE}/api/faces     — existing enrolled roster (name + photo)
#   GET  {PARACHUTE_API_BASE}/photos/{file} — the actual enrollment photos
#   WS   {PARACHUTE_WS_BASE}/ws/live/{id}   — the live JPEG frame stream
# This service is a CLIENT of Parachute, exactly like a browser tab would
# be — it opens no second RTSP connection to any camera and requires zero
# changes to Parachute's own backend.
PARACHUTE_API_BASE = os.getenv("PARACHUTE_API_BASE", "http://127.0.0.1:8811")
PARACHUTE_WS_BASE = os.getenv("PARACHUTE_WS_BASE", "ws://127.0.0.1:8811")

# How often the camera list and enrolled-faces roster are refreshed from
# Parachute — cheap HTTP GETs, no reason to poll faster than this.
CAMERA_LIST_REFRESH_SECONDS = float(os.getenv("CAMERA_LIST_REFRESH_SECONDS", "30"))
ROSTER_REFRESH_SECONDS = float(os.getenv("ROSTER_REFRESH_SECONDS", "60"))

# This service's own HTTP/WebSocket API.
SERVER_HOST = os.getenv("DETECTION_SERVER_HOST", "127.0.0.1")
SERVER_PORT = int(os.getenv("DETECTION_SERVER_PORT", "8812"))

# --- Person detection -----------------------------------------------------
# Plain YOLOv8n (COCO, class 0 = person) — a standard, general-purpose
# person detector. Deliberately NOT Parachute's own pose-based detector
# (this is a new, independent service, free to make its own choice) — a
# plain box detector is simpler and sufficient for "draw a box around every
# person", with no pose/keypoint output this service has no use for.
PERSON_MODEL_WEIGHTS = os.getenv("PERSON_MODEL_WEIGHTS", "yolov8n.pt")
PERSON_DETECT_CONF = float(os.getenv("PERSON_DETECT_CONF", "0.35"))
# How often (seconds) each camera's frame gets a fresh person-detection
# pass — independent of face recognition's own cadence below.
PERSON_DETECT_INTERVAL_SECONDS = float(os.getenv("PERSON_DETECT_INTERVAL_SECONDS", "1.0"))

# --- Face recognition -----------------------------------------------------
RECOGNITION_SIMILARITY_THRESHOLD = float(os.getenv("RECOGNITION_SIMILARITY_THRESHOLD", "0.40"))
FACE_DETECT_INTERVAL_SECONDS = float(os.getenv("FACE_DETECT_INTERVAL_SECONDS", "1.0"))

# --- Continuous face collection -------------------------------------------
FACE_TRAINING_DEDUP_SIMILARITY = float(os.getenv("FACE_TRAINING_DEDUP_SIMILARITY", "0.90"))
FACE_TRAINING_DEDUP_WINDOW_SECONDS = float(os.getenv("FACE_TRAINING_DEDUP_WINDOW_SECONDS", "300"))
FACE_TRAINING_MAX_PENDING_PER_CAMERA = int(os.getenv("FACE_TRAINING_MAX_PENDING_PER_CAMERA", "500"))

# --- Background retraining -------------------------------------------------
FACE_TRAINING_MIN_NEW_SAMPLES = int(os.getenv("FACE_TRAINING_MIN_NEW_SAMPLES", "10"))
FACE_TRAINING_CHECK_INTERVAL_SECONDS = float(os.getenv("FACE_TRAINING_CHECK_INTERVAL_SECONDS", "300"))
FACE_TRAINING_MAX_INTERVAL_SECONDS = float(os.getenv("FACE_TRAINING_MAX_INTERVAL_SECONDS", "21600"))
FACE_TRAINING_VALIDATION_CONFLICT_THRESHOLD = float(
    os.getenv("FACE_TRAINING_VALIDATION_CONFLICT_THRESHOLD", "0.40")
)

# --- Storage: entirely separate from Parachute's own database/files ------
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "detection.db"
SAMPLES_DIR = DATA_DIR / "face_training_samples"
GALLERY_CACHE_DIR = DATA_DIR / "roster_photos_cache"

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
