"""Standalone detection service — a separate FastAPI application from
Parachute's own backend/app/main.py, run as its own process on its own
port (config.SERVER_PORT, default 8812). Talks to Parachute only over its
existing public HTTP/WebSocket API (see parachute_client.py) — no shared
Python code, no shared database, no direct import of anything under
Parachute's backend/app/.
"""

import asyncio
import logging

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import config, face_training_db, training
from .manager import detection_manager

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s:%(name)s:%(message)s",
)
logger = logging.getLogger("detection")

app = FastAPI(title="Deco Vision Detection Service")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DETECTIONS_INTERVAL = 1 / 6  # matches Parachute's own /ws/detections poll rate


class LabelIn(BaseModel):
    sample_id: int
    name: str


class SkipIn(BaseModel):
    sample_id: int


@app.on_event("startup")
def startup():
    face_training_db.init_db()
    detection_manager.start()
    from . import scheduler
    scheduler.start()


@app.on_event("shutdown")
def shutdown():
    from . import scheduler
    scheduler.stop()
    detection_manager.stop()


@app.get("/health")
def health():
    return {"status": "ok", "live_camera_ids": detection_manager.live_camera_ids()}


@app.websocket("/ws/detections/{camera_id}")
async def ws_detections(websocket: WebSocket, camera_id: int):
    """This service's own detection channel — separate from Parachute's
    /ws/detections (which still carries Parachute's own face/fire-smoke/
    zone data unchanged). The detection frontend connects to Parachute's
    /ws/live for video and to THIS endpoint for person/face boxes."""
    await websocket.accept()
    try:
        while True:
            data = detection_manager.get_live_detections(camera_id)
            await websocket.send_json(data or {"people": [], "faces": [], "computed_at": 0})
            await asyncio.sleep(DETECTIONS_INTERVAL)
    except WebSocketDisconnect:
        logger.info("ws_detections client disconnected (camera %s)", camera_id)


@app.get("/api/face-training/status")
def face_training_status():
    from .recognizer import get_recognizer
    by_status = face_training_db.count_by_status()
    last_run = face_training_db.get_latest_training_run()
    gallery = get_recognizer().gallery_snapshot()
    return {
        "collection_active": len(detection_manager.live_camera_ids()) > 0,
        "live_camera_ids": detection_manager.live_camera_ids(),
        "pending_samples": by_status.get("pending", 0),
        "labeled_awaiting_promotion": by_status.get("labeled", 0),
        "promoted_samples": by_status.get("promoted", 0),
        "skipped_samples": by_status.get("skipped", 0),
        "samples_captured_today": face_training_db.count_captured_today(),
        "samples_labeled_today": face_training_db.count_labeled_today(),
        "total_gallery_people": len({n for n, _ in gallery}),
        "total_gallery_samples": len(gallery),
        "min_new_samples_threshold": config.FACE_TRAINING_MIN_NEW_SAMPLES,
        "max_interval_seconds": config.FACE_TRAINING_MAX_INTERVAL_SECONDS,
        "last_training_run": last_run,
    }


@app.get("/api/face-training/next")
def face_training_next():
    sample = face_training_db.get_next_pending()
    return {"sample": sample, "pending_count": face_training_db.count_by_status().get("pending", 0)}


@app.get("/api/face-training/image/{sample_id}")
def face_training_image(sample_id: int):
    from pathlib import Path
    sample = face_training_db.get_sample(sample_id)
    if sample is None:
        raise HTTPException(404, "Sample not found")
    if not Path(sample["image_path"]).exists():
        raise HTTPException(404, "Sample image file missing on disk")
    return FileResponse(sample["image_path"], media_type="image/jpeg")


@app.post("/api/face-training/label")
def face_training_label(body: LabelIn):
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "name is required")
    if not face_training_db.label_sample(body.sample_id, name):
        raise HTTPException(409, "Sample not found, or already labeled/skipped/promoted")
    return {"ok": True}


@app.post("/api/face-training/skip")
def face_training_skip(body: SkipIn):
    if not face_training_db.skip_sample(body.sample_id):
        raise HTTPException(409, "Sample not found, or already labeled/skipped/promoted")
    return {"ok": True}


@app.post("/api/face-training/train")
def face_training_train():
    return training.run_promotion_cycle(min_samples=0)


@app.get("/api/face-training/history")
def face_training_history(limit: int = 20):
    return face_training_db.list_training_runs(limit)
