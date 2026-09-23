# Deco Vision 

Multi-site camera intelligence platform: live RTSP/ONVIF camera feeds,
face recognition and attendance, footfall counting, desk/zone analytics,
fire & smoke detection, intrusion alerts, and per-client license
management — with a FastAPI backend and a React frontend.

## Repository layout

```
backend/     FastAPI application (Python) — cameras, detection, recognition,
             footfall, attendance, licensing, WebSocket live feeds
frontend/    React + Vite application — dashboards, live camera views,
             analytics, license/admin management
CP Plus/     Isolated person-detection + face-training service (its own
             backend, frontend, database) — see "CP Plus" below
```

Plus deployment tooling at the repo root: `deploy.sh` / `deploy.ps1` /
`deploy_backend.sh` / `deploy_frontend.sh` / `deploy_complete.sh` /
`health_check.sh`, and detailed deployment docs in `START_HERE.md`,
`DEPLOYMENT_README.md`, `EC2_DEPLOYMENT_GUIDE.md`, and
`QUICK_REFERENCE.md`.

## Backend

FastAPI (`backend/app/main.py`), covering:

- Live camera ingestion (RTSP/ONVIF) and per-camera detection pipelines
  (`pipeline.py`, `detection_worker.py`, `video_source.py`, `onvif_client.py`)
- Face recognition and enrollment (`recognizer.py`, `face_db.py`,
  `recognition_provider.py`, `recognition_stabilizer.py`), including
  polling a Honeywell device's own onboard recognition
  (`honeywell_recognition_poller.py`) as a preferred identity source
- Footfall counting and reporting (`footfall_counter.py`, `footfall_db.py`,
  `footfall_gate_db.py`, `footfall_report.py`, `gate_tracker.py`)
- Desk/zone occupancy (`desk_db.py`, `desk_tracker.py`, `zones_db.py`)
- Fire/smoke detection (`fire_smoke_detector.py`) and pose-based analytics
  (`pose_detector.py`, YOLOv8n-pose)
- Multi-tenant licensing (`license_db.py`, `license_qr.py`) and auth
  (`auth.py`, `user_db.py`)
- Live video/detections pushed to the frontend over WebSocket

Run locally:

```bash
cd backend
python -m venv venv
venv/Scripts/activate        # source venv/bin/activate on Linux/Mac
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8811
```

Tests: `cd backend && pytest`.

## Frontend

React 19 + Vite, with pages for the dashboard, live camera grid,
attendance, footfall, desk analytics, intrusion, smoke detection, people,
sites, camera management, and license administration
(`frontend/src/pages/`).

Run locally:

```bash
cd frontend
npm install
npm run dev
```

## CP Plus

A completely separate, isolated application — its own backend (port
8812), own frontend, own database, own dependencies — that adds person
detection and continuous face-training on top of the existing Parachute
system without modifying it. It talks to this backend only through its
already-public HTTP/WebSocket API (cameras, enrolled faces, live feed),
never by importing its Python modules or touching its database directly.
See `CP Plus/README.md` and `CP Plus/backend/README.md` for the full
design and how to run it alongside Parachute.

## Deployment

`.github/workflows/deploy.yml` deploys automatically on every push to
`main`: it SSHes into the production EC2 instance, resets the checkout to
`origin/main`, reinstalls backend/frontend dependencies, rebuilds the
frontend, and restarts the `deco-vision-backend` systemd service and
nginx. It only ever touches `backend/` and `frontend/` — `CP Plus/` is
not wired into this pipeline and must be deployed separately if/when
that's wanted.

For manual/first-time server setup, see `START_HERE.md` (quick start),
`DEPLOYMENT_README.md` (full guide), `EC2_DEPLOYMENT_GUIDE.md`
(step-by-step), and `QUICK_REFERENCE.md` (command cheat sheet).
