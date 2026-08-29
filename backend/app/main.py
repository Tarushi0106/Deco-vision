import asyncio
import csv
import io
import logging
import mimetypes
import re
import time
import uuid
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import alerts_db, camera_db, clips_db, config, face_db, user_db, zones_db
from .camera_client import camera_client, get_camera_client, sync_face_to_all_devices
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
    admin_port: int | None = config.CAMERA_ADMIN_PORT


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


class PersonRename(BaseModel):
    new_name: str


class SettingsIn(BaseModel):
    restricted_start: str | None = None  # "HH:MM"; empty/omitted disables intrusion detection
    restricted_end: str | None = None
    detection_fps: float | None = None  # how often frames are sent for face recognition


class ZoneIn(BaseModel):
    camera_id: int
    name: str
    polygon: list[list[float]]
    allowed_names: list[str] = []
    restricted_start: str | None = None  # "HH:MM"; blank (with restricted_end) means allow-list applies any time
    restricted_end: str | None = None


class ZoneUpdate(BaseModel):
    name: str | None = None
    polygon: list[list[float]] | None = None
    allowed_names: list[str] | None = None
    enabled: bool | None = None
    restricted_start: str | None = None
    restricted_end: str | None = None


@app.on_event("startup")
def startup():
    face_db.init_db()
    camera_db.init_db()
    user_db.init_db()
    alerts_db.init_db()
    clips_db.init_db()
    zones_db.init_db()
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


@app.get("/api/zones")
def list_zones(camera_id: int | None = None):
    return zones_db.list_zones(camera_id=camera_id)


@app.post("/api/zones")
def create_zone(zone: ZoneIn):
    zone_id = zones_db.add_zone(
        zone.camera_id, zone.name, zone.polygon, zone.allowed_names, zone.restricted_start, zone.restricted_end
    )
    return {"id": zone_id}


@app.put("/api/zones/{zone_id}")
def edit_zone(zone_id: int, zone: ZoneUpdate):
    fields = {k: v for k, v in zone.model_dump().items() if v is not None}
    zones_db.update_zone(zone_id, **fields)
    return {"ok": True}


@app.delete("/api/zones/{zone_id}")
def remove_zone(zone_id: int):
    zones_db.delete_zone(zone_id)
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
        "detection_fps": float(alerts_db.get_setting("detection_fps", "1")),
    }


@app.put("/api/settings")
def update_settings(settings: SettingsIn):
    alerts_db.set_setting("restricted_start", settings.restricted_start or "")
    alerts_db.set_setting("restricted_end", settings.restricted_end or "")
    if settings.detection_fps is not None:
        alerts_db.set_setting("detection_fps", str(settings.detection_fps))
    return {"ok": True}


@app.get("/api/attendance")
def get_attendance(date: str | None = None):
    return face_db.get_attendance(date)


@app.get("/api/attendance/report")
def get_attendance_report(name: str, start: str, end: str):
    cameras_by_id = {c["id"]: c["name"] for c in camera_db.list_cameras()}
    rows = face_db.get_attendance_report(name, start, end)
    for r in rows:
        r["camera_names"] = [cameras_by_id.get(cid, "—") for cid in r["camera_ids"]]
    return rows


@app.get("/api/attendance/report/csv")
def get_attendance_report_csv(name: str, start: str, end: str):
    cameras_by_id = {c["id"]: c["name"] for c in camera_db.list_cameras()}
    rows = face_db.get_attendance_report(name, start, end)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date", "First Seen", "Last Seen", "Total Detections", "Cameras"])
    for r in rows:
        camera_names = ", ".join(cameras_by_id.get(cid, "—") for cid in r["camera_ids"])
        writer.writerow([
            r["date"],
            datetime.fromtimestamp(r["first_seen"]).strftime("%H:%M:%S"),
            datetime.fromtimestamp(r["last_seen"]).strftime("%H:%M:%S"),
            r["total_detections"],
            camera_names,
        ])

    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
    filename = f"attendance_{safe_name}_{start}_to_{end}.csv"
    return Response(
        content=output.getvalue().encode("utf-8"),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/attendance/report/pdf")
def get_attendance_report_pdf(name: str, start: str, end: str):
    cameras_by_id = {c["id"]: c["name"] for c in camera_db.list_cameras()}
    rows = face_db.get_attendance_report(name, start, end)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        topMargin=1.6 * cm, bottomMargin=1.6 * cm, leftMargin=1.6 * cm, rightMargin=1.6 * cm,
    )
    styles = getSampleStyleSheet()
    elements = [
        Paragraph("Deco Vision — Attendance Report", styles["Title"]),
        Paragraph(f"{name}", styles["Heading2"]),
        Paragraph(f"{start} to {end} &nbsp;&nbsp;·&nbsp;&nbsp; generated {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                   styles["Normal"]),
        Spacer(1, 0.6 * cm),
    ]

    total_days = len(rows)
    total_detections = sum(r["total_detections"] for r in rows)
    elements.append(Paragraph(f"Days present: {total_days} &nbsp;&nbsp;·&nbsp;&nbsp; Total detections: {total_detections}",
                               styles["Normal"]))
    elements.append(Spacer(1, 0.5 * cm))

    table_data = [["Date", "First Seen", "Last Seen", "Detections", "Cameras"]]
    for r in rows:
        camera_names = ", ".join(cameras_by_id.get(cid, "—") for cid in r["camera_ids"])
        table_data.append([
            r["date"],
            datetime.fromtimestamp(r["first_seen"]).strftime("%H:%M:%S"),
            datetime.fromtimestamp(r["last_seen"]).strftime("%H:%M:%S"),
            str(r["total_detections"]),
            camera_names,
        ])
    if len(table_data) == 1:
        table_data.append(["—", "—", "—", "—", f"No sightings for {name} in this date range"])

    table = Table(table_data, colWidths=[2.4 * cm, 2.4 * cm, 2.4 * cm, 2.2 * cm, 7.6 * cm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a2b4c")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dddddd")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f7fb")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    elements.append(table)

    doc.build(elements)
    pdf_bytes = buffer.getvalue()

    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
    filename = f"attendance_{safe_name}_{start}_to_{end}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/analytics/people")
def get_people_analytics(days: int = 7):
    cameras_by_id = {c["id"]: c["name"] for c in camera_db.list_cameras()}
    analytics = face_db.get_person_analytics(days)
    for row in analytics:
        row["top_camera_name"] = cameras_by_id.get(row["top_camera_id"], "—")
    return analytics


@app.get("/api/clips/active")
def list_active_clips():
    return pipeline_manager.get_active_clip_people()


@app.get("/api/clips")
def list_clips(person: str, limit: int = 50):
    cameras_by_id = {c["id"]: c["name"] for c in camera_db.list_cameras()}
    clips = clips_db.list_clips_for_person(person, limit)
    for c in clips:
        c["camera_name"] = cameras_by_id.get(c["camera_id"], "—")
    return clips


@app.get("/api/clips/recent")
def list_recent_clips(limit: int = 50):
    cameras_by_id = {c["id"]: c["name"] for c in camera_db.list_cameras()}
    clips = clips_db.list_recent_clips(limit)
    for c in clips:
        c["camera_name"] = cameras_by_id.get(c["camera_id"], "—")
    return clips


@app.get("/api/clips/{clip_id}/video")
def get_clip_video(clip_id: int):
    clip = clips_db.get_clip(clip_id)
    if clip is None or not Path(clip["file_path"]).exists():
        raise HTTPException(404, "Clip not found")
    return FileResponse(clip["file_path"], media_type="video/mp4")


@app.get("/api/debug/snaped-faces")
def debug_snaped_faces():
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
    # preserve the real format (PNG upload stays a .png file, etc.) instead of
    # always writing a .jpg name onto whatever bytes were actually uploaded
    ext = mimetypes.guess_extension(photo.content_type or "") or ".jpg"
    if ext == ".jpe":
        ext = ".jpg"
    saved_filename = f"{safe_name}_{uuid.uuid4().hex[:8]}{ext}"
    (ENROLLMENT_PHOTOS_DIR / saved_filename).write_bytes(photo_bytes)

    face_db.add_face(name, saved_filename, embedding)
    pipeline_manager.reload_faces()
    logger.info("Enrolled %s locally", name)

    device_results = await asyncio.to_thread(sync_face_to_all_devices, name, photo_bytes)

    return {"name": name, "local_enrolled": True, "devices": device_results}


@app.put("/api/people/{name}")
def rename_person(name: str, payload: PersonRename):
    new_name = payload.new_name.strip()
    if not new_name:
        raise HTTPException(400, "new_name cannot be empty")
    rows_changed = face_db.rename_face(name, new_name)
    if rows_changed == 0:
        raise HTTPException(404, f"No enrolled person named {name!r}")
    clips_db.rename_person_clips(name, new_name)
    zones_db.rename_person_in_zones(name, new_name)
    pipeline_manager.reload_faces()
    logger.info("Renamed %s to %s (%d sample(s))", name, new_name, rows_changed)
    return {"old_name": name, "new_name": new_name, "samples_renamed": rows_changed}


@app.delete("/api/people/{name}")
def remove_person(name: str):
    photos = face_db.delete_face(name)
    for photo in photos:
        (ENROLLMENT_PHOTOS_DIR / photo).unlink(missing_ok=True)
    zones_db.remove_person_from_zones(name)
    pipeline_manager.reload_faces()
    logger.info("Removed %s from local recognition (%d photo(s))", name, len(photos))
    return {
        "name": name,
        "removed_locally": True,
        "note": "Removed from this dashboard's recognition only — not from any camera's onboard Allow List "
        "(no verified API for that in this codebase; remove manually via the camera's admin UI if needed).",
    }


def _sync_people_from_camera() -> dict:
    synced, skipped = 0, 0
    failed = []
    already_synced = face_db.get_synced_camera_face_ids()

    for device in camera_db.list_active_devices():
        host = device["host"]
        if not device.get("user") or not device.get("password"):
            continue
        client = get_camera_client(host, device["user"], device["password"], device.get("admin_port", 443))

        try:
            faces = client.list_added_faces()
        except Exception as e:
            failed.append({"host": host, "name": None, "error": f"could not list Allow List: {e}"})
            continue

        for face in faces:
            camera_face_id = f"{host}:{face['Id']}"
            if camera_face_id in already_synced:
                skipped += 1
                continue

            try:
                photo_bytes = client.get_added_face_photo(face["Id"])
                if not photo_bytes:
                    failed.append({"host": host, "name": face["Name"], "error": "camera has no photo for this entry"})
                    continue

                frame = cv2.imdecode(np.frombuffer(photo_bytes, np.uint8), cv2.IMREAD_COLOR)
                if frame is None:
                    failed.append({"host": host, "name": face["Name"], "error": "camera's photo is unreadable"})
                    continue

                embedding = pipeline_manager.compute_embedding(frame)
                if embedding is None:
                    failed.append({"host": host, "name": face["Name"], "error": "no face detected in camera's photo"})
                    continue

                safe_name = re.sub(r"[^a-zA-Z0-9 ()_-]", "", face["Name"]).strip().replace(" ", "_")
                saved_filename = f"{safe_name}_camera{face['Id']}_{uuid.uuid4().hex[:6]}.jpg"
                (ENROLLMENT_PHOTOS_DIR / saved_filename).write_bytes(photo_bytes)

                face_db.add_face(face["Name"], saved_filename, embedding, camera_face_id=camera_face_id)
                already_synced.add(camera_face_id)
                synced += 1
            except Exception as e:
                failed.append({"host": host, "name": face.get("Name"), "error": str(e)})

            # the camera's admin API is fragile under rapid repeated calls —
            # pace requests rather than hammering it back-to-back
            time.sleep(0.3)

    if synced:
        pipeline_manager.reload_faces()
        logger.info("Synced %d people from camera Allow List(s)", synced)

    return {"synced": synced, "skipped": skipped, "failed": failed}


@app.post("/api/people/sync-from-camera")
async def sync_people_from_camera():
    return await asyncio.to_thread(_sync_people_from_camera)


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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=config.SERVER_HOST, port=config.SERVER_PORT)
