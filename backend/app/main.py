import asyncio
import csv
import io
import logging
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
from openpyxl import Workbook
from pydantic import BaseModel
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import (
    alerts_db, camera_db, clips_db, config, desk_db, face_db, footfall_db, footfall_gate_db, footfall_report,
    scheduler, user_db,
)
from . import onvif_client, pipeline, replay_prefetch
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
    employee_id: str | None = None


class SettingsIn(BaseModel):
    restricted_start: str | None = None  # "HH:MM"; empty/omitted disables intrusion detection
    restricted_end: str | None = None
    detection_fps: float | None = None  # how often frames are sent for face recognition


class DeskZoneIn(BaseModel):
    camera_id: int
    x1: float
    y1: float
    x2: float
    y2: float


class DeskZoneUpdate(BaseModel):
    zone_label: str | None = None
    x1: float | None = None
    y1: float | None = None
    x2: float | None = None
    y2: float | None = None


class FootfallGateIn(BaseModel):
    camera_id: int
    x1: float
    y1: float
    x2: float
    y2: float
    entry_sign: int = 1


@app.on_event("startup")
def startup():
    face_db.init_db()
    camera_db.init_db()
    user_db.init_db()
    alerts_db.init_db()
    clips_db.init_db()
    footfall_db.init_db()
    footfall_gate_db.init_db()
    desk_db.init_db()
    pipeline_manager.start()
    scheduler.start_scheduler()
    replay_prefetch.start()


@app.on_event("shutdown")
def shutdown():
    replay_prefetch.stop()
    scheduler.shutdown_scheduler()
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


@app.get("/api/desk-zones")
def list_desk_zones(camera_id: int | None = None):
    return desk_db.list_zones(camera_id)


@app.post("/api/desk-zones")
def create_desk_zone(zone: DeskZoneIn):
    """Zones are anonymous — no employee is assigned at creation time. Who
    occupies a zone is resolved automatically, every detection cycle, by
    desk_tracker.py from face recognition (+ pose-based continuity)."""
    zone_id = desk_db.create_zone(zone.camera_id, zone.x1, zone.y1, zone.x2, zone.y2)
    pipeline_manager.refresh_desk_zones()
    return {"id": zone_id}


@app.put("/api/desk-zones/{zone_id}")
def edit_desk_zone(zone_id: int, zone: DeskZoneUpdate):
    fields = {k: v for k, v in zone.model_dump().items() if v is not None}
    desk_db.update_zone(zone_id, **fields)
    pipeline_manager.refresh_desk_zones()
    return {"ok": True}


@app.delete("/api/desk-zones/{zone_id}")
def remove_desk_zone(zone_id: int):
    desk_db.delete_zone(zone_id)
    pipeline_manager.refresh_desk_zones()
    return {"ok": True}


@app.get("/api/desk-analytics/report")
def get_desk_analytics_report(date: str | None = None):
    """Per-employee daily desk-time report: every enrolled employee (like
    the Attendance roster), with desk/away totals + movement count from
    desk_db (real tracked sessions, not estimates), plus their live
    current-desk/current-status from the running DeskTracker — which is
    only meaningful for TODAY; a past date always reports "unknown" there,
    since there's no "current" moment to speak of in history."""
    report = desk_db.get_daily_report(date)
    by_employee = {e["employee_name"]: e for e in report["employees"]}

    is_today = date is None or date == datetime.now().strftime("%Y-%m-%d")
    live_status = pipeline_manager.get_desk_status() if is_today else {}

    employees = []
    for person in face_db.list_enrolled_roster():
        name = person["name"]
        e = by_employee.get(name)
        status = live_status.get(name)
        employees.append({
            "employee_name": name,
            "desk_seconds": e["desk_seconds"] if e else 0,
            "away_seconds": e["away_seconds"] if e else 0,
            "movements": e["movements"] if e else 0,
            "first_session": e["first_session"] if e else None,
            "last_session": e["last_session"] if e else None,
            "current_status": status["status"] if status else "unknown",
            "current_desk": status["zone_label"] if status else None,
        })
    employees.sort(key=lambda e: -e["desk_seconds"])

    return {"date": report["date"], "employees": employees}


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
    unique_footfall_today = footfall_db.get_daily_report()["total"]

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
        "unique_footfall_today": unique_footfall_today,
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
    """Full-roster daily attendance — every enrolled person, present or
    absent, with check-in/out, checkout camera, detections, and best
    recognition match. See face_db.get_daily_attendance_roster."""
    cameras = camera_db.list_cameras()
    cameras_by_id = {c["id"]: c["name"] for c in cameras}
    report = face_db.get_daily_attendance_roster(date)
    for row in report["roster"]:
        row["checkout_camera_name"] = cameras_by_id.get(row["checkout_camera_id"])
    report["camera_scope"] = len(cameras)
    return report


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return ""
    h, m = divmod(round(seconds / 60), 60)
    return f"{h}h {m}m" if h else f"{m}m"


def _attendance_roster_rows(report: dict, cameras_by_id: dict) -> list[list[str]]:
    rows = []
    for r in report["roster"]:
        rows.append([
            r["name"],
            r["employee_id"] or "—",
            datetime.fromtimestamp(r["check_in"]).strftime("%H:%M") if r["check_in"] else "—",
            datetime.fromtimestamp(r["check_out"]).strftime("%H:%M") if r["check_out"] else "—",
            cameras_by_id.get(r["checkout_camera_id"], "—"),
            _format_duration(r["time_stay_seconds"]) or "—",
            str(r["detections"]),
            f"{round(r['best_match'] * 100)}%" if r["best_match"] is not None else "—",
            "Present" if r["present"] else "Absent",
        ])
    return rows


@app.get("/api/attendance/daily/xlsx")
def get_attendance_daily_xlsx(date: str | None = None):
    cameras_by_id = {c["id"]: c["name"] for c in camera_db.list_cameras()}
    report = face_db.get_daily_attendance_roster(date)

    wb = Workbook()
    ws = wb.active
    ws.title = "Attendance"
    ws.append(["Employee", "ID Code", "Check-In", "Check-Out", "Check-Out Camera",
               "Time Stay", "Detections", "Best Match", "Status"])
    for row in _attendance_roster_rows(report, cameras_by_id):
        ws.append(row)

    buffer = io.BytesIO()
    wb.save(buffer)
    filename = f"attendance_{report['date']}.xlsx"
    return Response(
        content=buffer.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/attendance/daily/pdf")
def get_attendance_daily_pdf(date: str | None = None):
    cameras_by_id = {c["id"]: c["name"] for c in camera_db.list_cameras()}
    report = face_db.get_daily_attendance_roster(date)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        topMargin=1.6 * cm, bottomMargin=1.6 * cm, leftMargin=1.2 * cm, rightMargin=1.2 * cm,
    )
    styles = getSampleStyleSheet()
    elements = [
        Paragraph("Deco Vision — Daily Attendance", styles["Title"]),
        Paragraph(
            f"{report['date']} &nbsp;&nbsp;·&nbsp;&nbsp; generated {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            styles["Normal"],
        ),
        Spacer(1, 0.4 * cm),
        Paragraph(
            f"Present: {report['present']} &nbsp;&nbsp;·&nbsp;&nbsp; Absent: {report['absent']} "
            f"&nbsp;&nbsp;·&nbsp;&nbsp; Total detections: {report['total_detections']}",
            styles["Normal"],
        ),
        Spacer(1, 0.5 * cm),
    ]

    table_data = [["Employee", "ID", "In", "Out", "Camera", "Stay", "Det.", "Match", "Status"]]
    table_data.extend(_attendance_roster_rows(report, cameras_by_id))
    table = Table(table_data, colWidths=[3.6 * cm, 1.6 * cm, 1.6 * cm, 1.6 * cm, 3 * cm, 1.6 * cm, 1.4 * cm, 1.6 * cm, 1.8 * cm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a2b4c")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dddddd")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f7fb")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(table)

    doc.build(elements)
    filename = f"attendance_{report['date']}.pdf"
    return Response(
        content=buffer.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/attendance/report")
def get_attendance_report(name: str, start: str, end: str):
    cameras_by_id = {c["id"]: c["name"] for c in camera_db.list_cameras()}
    rows = face_db.get_attendance_report(name, start, end)
    for r in rows:
        r["camera_names"] = [cameras_by_id.get(cid, "—") for cid in r["camera_ids"]]
    return rows


@app.get("/api/attendance/report/xlsx")
def get_attendance_report_xlsx(name: str, start: str, end: str):
    cameras_by_id = {c["id"]: c["name"] for c in camera_db.list_cameras()}
    rows = face_db.get_attendance_report(name, start, end)

    wb = Workbook()
    ws = wb.active
    ws.title = "Attendance"
    ws.append(["Date", "First Seen", "Last Seen", "Total Detections", "Cameras"])
    for r in rows:
        camera_names = ", ".join(cameras_by_id.get(cid, "—") for cid in r["camera_ids"])
        ws.append([
            r["date"],
            datetime.fromtimestamp(r["first_seen"]).strftime("%H:%M:%S"),
            datetime.fromtimestamp(r["last_seen"]).strftime("%H:%M:%S"),
            r["total_detections"],
            camera_names,
        ])

    buffer = io.BytesIO()
    wb.save(buffer)
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
    filename = f"attendance_{safe_name}_{start}_to_{end}.xlsx"
    return Response(
        content=buffer.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
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


@app.get("/api/footfall-gate")
def list_footfall_gates(camera_id: int | None = None):
    if camera_id is not None:
        gate = footfall_gate_db.get_gate(camera_id)
        return [gate] if gate else []
    return footfall_gate_db.list_gates()


@app.post("/api/footfall-gate")
def set_footfall_gate(gate: FootfallGateIn):
    """One line per camera — drawing a new one replaces any existing line
    for that camera. Once a line exists, footfall counting for that camera
    switches from "a face was recognized anywhere in the frame" to "someone
    was tracked crossing this line" (see gate_tracker.py)."""
    footfall_gate_db.set_gate(gate.camera_id, gate.x1, gate.y1, gate.x2, gate.y2, gate.entry_sign)
    pipeline_manager.refresh_footfall_gate()
    return {"ok": True}


@app.post("/api/footfall-gate/{camera_id}/flip")
def flip_footfall_gate(camera_id: int):
    """Swaps which side of the line counts as "entering" — for when a
    freshly-drawn line counts people leaving as entries, or vice versa."""
    footfall_gate_db.flip_gate_direction(camera_id)
    pipeline_manager.refresh_footfall_gate()
    return {"ok": True}


@app.delete("/api/footfall-gate/{camera_id}")
def remove_footfall_gate(camera_id: int):
    footfall_gate_db.delete_gate(camera_id)
    pipeline_manager.refresh_footfall_gate()
    return {"ok": True}


@app.get("/api/footfall/report")
def get_footfall_report(date: str | None = None):
    """Full daily unique-footfall report (see footfall_counter.py): total
    unique count, hourly breakdown, camera breakdown, and the combined
    Person ID / First Seen / Camera / Last Seen table — the on-demand,
    JSON/dashboard form. See also the CSV/XLSX variants below and
    scheduler.py / scripts/generate_footfall_report.py for the end-of-day
    job that finalizes this same report to disk."""
    return footfall_report.enrich_with_camera_names(footfall_db.get_daily_report(date))


@app.get("/api/footfall/report/csv")
def get_footfall_report_csv(date: str | None = None):
    report = footfall_report.enrich_with_camera_names(footfall_db.get_daily_report(date))

    output = io.StringIO()
    footfall_db.write_csv_rows(report, csv.writer(output))

    filename = f"footfall_{report['date']}.csv"
    return Response(
        content=output.getvalue().encode("utf-8"),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/footfall/report/xlsx")
def get_footfall_report_xlsx(date: str | None = None):
    report = footfall_report.enrich_with_camera_names(footfall_db.get_daily_report(date))
    wb = footfall_db.build_workbook(report)

    buffer = io.BytesIO()
    wb.save(buffer)

    filename = f"footfall_{report['date']}.xlsx"
    return Response(
        content=buffer.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/footfall/people-count")
def get_people_count_report(date: str | None = None):
    """Raw IN/OUT people-counting totals (see person_tracker.py /
    face_db.get_people_counting_report) — every midline crossing counted, no
    identity, no dedup. Distinct from /api/footfall/report, which is the
    embedding-deduped unique-visitor count. Powers the Footfall page's
    "People Counted" stat tile."""
    return face_db.get_people_counting_report(date)


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
def list_clips(person: str, limit: int = 2000):
    # No storage-side cap anymore (see pipeline.py) — this default just
    # bounds a single response/render, not how much history is kept.
    cameras_by_id = {c["id"]: c["name"] for c in camera_db.list_cameras()}
    clips = clips_db.list_clips_for_person(person, limit)
    for c in clips:
        c["camera_name"] = cameras_by_id.get(c["camera_id"], "—")
    return clips


@app.get("/api/people/{name}/clips-for-day")
def get_clips_for_day(name: str, date: str):
    """The Daily Activity date search's real source of truth for "all the
    clips of that day" — the plain /api/clips list only shows sightings that
    already have a clips row, which for replay cameras (config.
    CAMERA_ONVIF_REPLAY_CHANNEL) can be incomplete (rows deleted by the old
    prune-to-30 cap, back before it was removed). Since the camera's own
    onboard recording still has that footage regardless, this reconstructs
    any missing sessions from detection_events (never pruned) and backfills
    them as real clips rows (empty file_path — fetched on first play, same
    as any other replay clip) so the list becomes complete and self-healing.
    Non-replay cameras can't be recovered this way — once a self-recorded
    clip's local file is gone, so is the video — so they're left as-is.
    Bounded to config.CLIP_RETENTION_DAYS: backfilling further back would
    just recreate rows the nightly prune job (scheduler.py) is going to
    delete again on its next run, and the camera's own onboard recording is
    unlikely to still have footage that old anyway (confirmed live: camera
    1 only holds ~3 days before overwriting itself)."""
    day_start = datetime.strptime(date, "%Y-%m-%d").timestamp()
    day_end = day_start + 86400
    cameras_by_id = {c["id"]: c["name"] for c in camera_db.list_cameras()}

    existing = clips_db.list_clips_for_person(name, limit=2000)
    existing_by_cam: dict[int, list[dict]] = {}
    for c in existing:
        existing_by_cam.setdefault(c["camera_id"], []).append(c)

    retention_cutoff = time.time() - config.CLIP_RETENTION_DAYS * 86400
    backfilled = []
    for camera_id in config.CAMERA_ONVIF_REPLAY_CHANNEL if day_start >= retention_cutoff else []:
        sessions = face_db.get_person_day_sessions(
            name, camera_id, date,
            pipeline.PRESENCE_GRACE_SECONDS, pipeline.MAX_CLIP_DURATION_SECONDS, pipeline.MIN_CLIP_DURATION_SECONDS,
        )
        known = existing_by_cam.get(camera_id, [])
        for s in sessions:
            if any(abs(k["ts"] - s["ts"]) < 5 for k in known):
                continue
            new_id = clips_db.log_clip(name, camera_id, s["ts"], s["duration"], "")
            backfilled.append({
                "id": new_id, "person_name": name, "camera_id": camera_id,
                "ts": s["ts"], "duration": s["duration"], "file_path": "",
            })

    result = [c for c in existing + backfilled if day_start <= c["ts"] < day_end]
    for c in result:
        c["camera_name"] = cameras_by_id.get(c["camera_id"], "—")
    result.sort(key=lambda c: c["ts"])
    return result


@app.get("/api/clips/{clip_id}/video")
def get_clip_video(clip_id: int):
    clip = clips_db.get_clip(clip_id)
    if clip is None:
        raise HTTPException(404, "Clip not found")

    # A settled miss (replay_prefetch.py or an earlier play attempt already
    # tried and the camera had nothing) — never retried, so this fails fast
    # instead of repeating a doomed 2-3 minute fetch.
    if clip["file_path"] == clips_db.UNAVAILABLE_SENTINEL:
        raise HTTPException(404, "This footage is no longer available on the camera")

    # Already cached locally — either a prior fetch (on-demand or background
    # prefetch) for a replay camera, or an old-style clip a non-replay
    # camera self-recorded.
    if clip["file_path"] and Path(clip["file_path"]).exists():
        return FileResponse(clip["file_path"], media_type="video/mp4")

    replay_channel = config.CAMERA_ONVIF_REPLAY_CHANNEL.get(clip["camera_id"])
    if replay_channel is None:
        raise HTTPException(404, "Clip not found")

    cam = camera_db.get_camera_connection(clip["camera_id"])
    if cam is None:
        raise HTTPException(404, "Camera not found")
    replay_url = onvif_client.build_replay_rtsp_url(
        cam["host"], cam["port"], cam["user"], cam["password"], replay_channel,
        clip["ts"] - onvif_client.REPLAY_PADDING_SECONDS,
        clip["ts"] + clip["duration"] + onvif_client.REPLAY_PADDING_SECONDS,
    )
    # Measured live against the real camera: fetch latency is dominated by a
    # large FIXED per-request cost (a 5.8s window took ~121s, a 98s window
    # took ~185s), not by clip length — modeled here as a generous flat
    # floor plus a modest per-second margin, not a multiplier off duration
    # (which would drastically under-time short clips, the common case).
    timeout = clip["duration"] * 1.5 + 180.0
    camera_dir = clips_db.CLIPS_DIR / str(clip["camera_id"])
    camera_dir.mkdir(parents=True, exist_ok=True)
    out_path = camera_dir / f"replay_{clip_id}_{uuid.uuid4().hex[:8]}.mp4"
    fetched = onvif_client.fetch_replay_clip(replay_url, out_path, timeout=timeout)
    if fetched is None:
        clips_db.set_clip_file_path(clip_id, clips_db.UNAVAILABLE_SENTINEL)
        raise HTTPException(502, "Could not fetch this clip from the camera's own recording")
    clips_db.set_clip_file_path(clip_id, str(fetched))
    return FileResponse(fetched, media_type="video/mp4")


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
    saved_filename = f"{safe_name}_{uuid.uuid4().hex[:8]}.jpg"
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
    if payload.employee_id is not None:
        face_db.set_employee_id(new_name, payload.employee_id.strip() or None)
    clips_db.rename_person_clips(name, new_name)
    pipeline_manager.reload_faces()
    logger.info("Renamed %s to %s (%d sample(s))", name, new_name, rows_changed)
    return {"old_name": name, "new_name": new_name, "samples_renamed": rows_changed}


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


@app.get("/api/cameras/{camera_id}/snapshot")
def camera_snapshot(camera_id: int):
    frame = pipeline_manager.get_latest_jpeg(camera_id)
    if frame is None:
        raise HTTPException(404, "No live frame available for this camera")
    return Response(content=frame, media_type="image/jpeg")


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
