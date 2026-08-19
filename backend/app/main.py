import asyncio
import logging
import re
import time
import uuid
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import alerts_db, camera_db, face_db, user_db
from .camera_client import camera_client, sync_face_to_all_devices
from .pipeline import pipeline_manager

ENROLLMENT_PHOTOS_DIR = Path(__file__).resolve().parent.parent / "data" / "enrollment_photos"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("dashboard")

app = FastAPI()
START_TIME = time.time()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ENROLLMENT_PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/photos", StaticFiles(directory=ENROLLMENT_PHOTOS_DIR), name="photos")

VIDEO_FPS = 15
VIDEO_INTERVAL = 1 / VIDEO_FPS
DETECTIONS_FPS = 6
DETECTIONS_INTERVAL = 1 / DETECTIONS_FPS


class CameraIn(BaseModel):
    name: str
    site: str
    cam_code: str | None = ""
    purpose: str | None = "GENERAL"
    host: str | None = ""
    port: int | None = 554
    user: str | None = ""
    password: str | None = ""
    stream_path: str | None = "/h264/ch1/sub/av_stream"
    live_feed_enabled: bool | None = True
    admin_port: int | None = 443


class CameraUpdate(BaseModel):
    name: str | None = None
    site: str | None = None
    cam_code: str | None = None
    purpose: str | None = None
    host: str | None = None
    port: int | None = None
    user: str | None = None
    password: str | None = None
    stream_path: str | None = None
    live_feed_enabled: bool | None = None
    admin_port: int | None = None


class SiteIn(BaseModel):
    name: str
    description: str | None = ""


class SiteUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class LoginIn(BaseModel):
    email: str


class SettingsIn(BaseModel):
    restricted_start: str | None = None  # "HH:MM"; empty/omitted disables intrusion detection
    restricted_end: str | None = None


@app.on_event("startup")
def startup():
    face_db.init_db()
    camera_db.init_db()
    user_db.init_db()
    alerts_db.init_db()
    pipeline_manager.start()


@app.on_event("shutdown")
def shutdown():
    pipeline_manager.stop()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/cameras")
def list_cameras():
    cameras = camera_db.list_cameras()
    for cam in cameras:
        cam["live"] = pipeline_manager.is_live(cam["id"])
    return cameras


@app.post("/api/cameras")
def create_camera(camera: CameraIn):
    camera_id = camera_db.add_camera(camera.name, camera.site, **camera.model_dump(exclude={"name", "site"}))
    pipeline_manager.refresh_cameras()
    return {"id": camera_id}


@app.put("/api/cameras/{camera_id}")
def edit_camera(camera_id: int, camera: CameraUpdate):
    fields = {k: v for k, v in camera.model_dump().items() if v is not None}
    camera_db.update_camera(camera_id, **fields)
    pipeline_manager.refresh_cameras()
    return {"ok": True}


@app.delete("/api/cameras/{camera_id}")
def remove_camera(camera_id: int):
    camera_db.delete_camera(camera_id)
    pipeline_manager.refresh_cameras()
    return {"ok": True}


@app.get("/api/sites")
def list_sites():
    sites = camera_db.list_sites()
    cameras = camera_db.list_cameras()
    for cam in cameras:
        cam["live"] = pipeline_manager.is_live(cam["id"])
    for site in sites:
        site_cameras = [c for c in cameras if c["site"] == site["name"]]
        site["cameras"] = site_cameras
        site["active_count"] = sum(1 for c in site_cameras if c["live"])
    return sites


@app.post("/api/sites")
def create_site(site: SiteIn):
    site_id = camera_db.add_site(site.name, site.description or "")
    return {"id": site_id}


@app.put("/api/sites/{site_id}")
def edit_site(site_id: int, site: SiteUpdate):
    camera_db.update_site(site_id, name=site.name, description=site.description)
    return {"ok": True}


@app.delete("/api/sites/{site_id}")
def remove_site(site_id: int):
    camera_db.delete_site(site_id)
    return {"ok": True}


@app.post("/api/auth/login")
def login(payload: LoginIn):
    return user_db.record_login(payload.email)


@app.get("/api/users")
def list_users():
    return user_db.list_users()


@app.get("/api/stats")
def get_stats():
    cameras = camera_db.list_cameras()
    active_configured = [c for c in cameras if c["is_configured"] and c["status"] == "active"]
    live_count = sum(1 for c in active_configured if pipeline_manager.is_live(c["id"]))
    degraded = live_count < len(active_configured)

    uptime_seconds = int(time.time() - START_TIME)
    footfall = face_db.count_footfall_today()

    return {
        "active_cameras": live_count,
        "total_cameras": len(cameras),
        "faces_enrolled": len({name for name, _ in face_db.load_all_faces()}),
        "detections_today": face_db.count_detections_today(),
        "active_alerts": alerts_db.count_open_alerts(),
        "system_status": "degraded" if degraded else "nominal",
        "uptime_seconds": uptime_seconds,
        "footfall_in_today": footfall["in"],
        "footfall_out_today": footfall["out"],
    }


@app.get("/api/alerts")
def list_alerts(resolved: bool | None = None, limit: int = 50):
    cameras_by_id = {c["id"]: c["name"] for c in camera_db.list_cameras()}
    alerts = alerts_db.list_alerts(resolved=resolved, limit=limit)
    for alert in alerts:
        alert["camera_name"] = cameras_by_id.get(alert["camera_id"], "Unknown camera")
    return alerts


@app.post("/api/alerts/{alert_id}/resolve")
def resolve_alert(alert_id: int):
    alerts_db.resolve_alert(alert_id)
    return {"ok": True}


@app.get("/api/settings")
def get_settings():
    return {
        "restricted_start": alerts_db.get_setting("restricted_start", ""),
        "restricted_end": alerts_db.get_setting("restricted_end", ""),
    }


@app.put("/api/settings")
def update_settings(settings: SettingsIn):
    alerts_db.set_setting("restricted_start", settings.restricted_start or "")
    alerts_db.set_setting("restricted_end", settings.restricted_end or "")
    return {"ok": True}


@app.get("/api/attendance")
def get_attendance(date: str | None = None):
    return face_db.get_attendance(date)


@app.get("/api/debug/snaped-faces")
def debug_snaped_faces():
    from datetime import datetime

    today = datetime.now().strftime("%Y-%m-%d")
    result = camera_client.search_snaped_faces(f"{today} 00:00:00", f"{today} 23:59:59")
    return result


@app.get("/api/faces")
def list_faces():
    people = face_db.load_faces_with_photos()
    for person in people:
        person["photo_urls"] = [f"/photos/{p}" for p in person["photos"]]
    return people


@app.post("/api/people")
async def add_person(name: str = Form(...), photo: UploadFile = File(...)):
    photo_bytes = await photo.read()
    frame = cv2.imdecode(np.frombuffer(photo_bytes, np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise HTTPException(400, "Could not read uploaded image")

    embedding = await asyncio.to_thread(pipeline_manager.compute_embedding, frame)
    if embedding is None:
        raise HTTPException(400, "No face detected in photo")

    safe_name = re.sub(r"[^a-zA-Z0-9 ()_-]", "", name).strip().replace(" ", "_")
    saved_filename = f"{safe_name}_{uuid.uuid4().hex[:8]}.jpg"
    (ENROLLMENT_PHOTOS_DIR / saved_filename).write_bytes(photo_bytes)

    face_db.add_face(name, saved_filename, embedding)
    pipeline_manager.reload_faces()
    logger.info("Enrolled %s locally", name)

    device_results = await asyncio.to_thread(sync_face_to_all_devices, name, photo_bytes)

    return {"name": name, "local_enrolled": True, "devices": device_results}


@app.delete("/api/people/{name}")
def remove_person(name: str):
    photos = face_db.delete_face(name)
    for photo in photos:
        (ENROLLMENT_PHOTOS_DIR / photo).unlink(missing_ok=True)
    pipeline_manager.reload_faces()
    logger.info("Removed %s from local recognition (%d photo(s))", name, len(photos))
    return {
        "name": name,
        "removed_locally": True,
        "note": "Removed from this dashboard's recognition only — not from any camera's onboard Allow List "
        "(no verified API for that in this codebase; remove manually via the camera's admin UI if needed).",
    }


@app.websocket("/ws/live/{camera_id}")
async def live_feed(websocket: WebSocket, camera_id: int):
    await websocket.accept()
    if not pipeline_manager.is_live(camera_id):
        await websocket.close(code=1000, reason="camera not live")
        return
    try:
        while True:
            frame = pipeline_manager.get_latest_jpeg(camera_id)
            if frame is not None:
                await websocket.send_bytes(frame)
            await asyncio.sleep(VIDEO_INTERVAL)
    except (WebSocketDisconnect, ConnectionResetError, RuntimeError):
        logger.info("live_feed client disconnected (camera %s)", camera_id)


@app.websocket("/ws/detections/{camera_id}")
async def detections_feed(websocket: WebSocket, camera_id: int):
    await websocket.accept()
    if not pipeline_manager.is_live(camera_id):
        await websocket.close(code=1000, reason="camera not live")
        return
    try:
        while True:
            await websocket.send_json({"faces": pipeline_manager.get_latest_detections(camera_id)})
            await asyncio.sleep(DETECTIONS_INTERVAL)
    except (WebSocketDisconnect, ConnectionResetError, RuntimeError):
        logger.info("detections_feed client disconnected (camera %s)", camera_id)
