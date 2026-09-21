# Deco Vision Detection Service (backend)

A standalone FastAPI application — **not** a module inside Parachute's own
`backend/`. It runs as its own process, on its own port (default `8812`),
with its own dependencies, its own database, and its own models.

## How it talks to Parachute

This service never imports a Parachute Python module and never touches
Parachute's database. It is a **client** of Parachute's existing, already-
public API — exactly what a browser tab already does:

| Call | Purpose |
|---|---|
| `GET  {PARACHUTE_API_BASE}/api/cameras` | which cameras exist / are live |
| `GET  {PARACHUTE_API_BASE}/api/faces` | existing enrolled roster (name + photo filenames) |
| `GET  {PARACHUTE_API_BASE}/photos/{file}` | the actual enrollment photos |
| `WS   {PARACHUTE_WS_BASE}/ws/live/{camera_id}` | the live JPEG frame stream |

No RTSP connection of its own, no second camera capture path — video comes
entirely from Parachute's existing live-view WebSocket. Face identities
come from a **separate embedding gallery this service builds itself** by
downloading Parachute's enrollment photos and running its own InsightFace
instance over them — not by reading Parachute's `enrolled_faces` table.
Two independent recognition engines that happen to agree because they're
looking at the same photos, not one engine shared between two processes.

Zero changes were required in Parachute's own backend for this to work —
every endpoint above already existed and was already public.

## Running

```bash
cd detection/backend
python -m venv venv
venv/Scripts/activate  # or source venv/bin/activate on Linux/Mac
pip install -r requirements.txt
cp .env.example .env   # edit PARACHUTE_API_BASE/PARACHUTE_WS_BASE if not localhost
uvicorn app.main:app --host 0.0.0.0 --port 8812
```

## What it does

- **Person detection**: plain YOLOv8n (COCO, class 0), independent per-camera
  worker thread, throttled (`PERSON_DETECT_INTERVAL_SECONDS`).
- **Face recognition**: InsightFace against this service's own embedding
  gallery, synced from Parachute's roster on a timer (`ROSTER_REFRESH_SECONDS`).
- **Continuous face collection**: an Unknown face's crop is saved only if
  novel versus a short-window per-camera dedup cache — reuses the embedding
  already computed for recognition, no extra detection pass.
- **Manual labeling + background retraining**: `/api/face-training/*` —
  see `app/training.py` for the promote/validate/reject logic. Validation
  is a real leave-one-out measurement against the actual matching
  algorithm, not an invented number, and is explicitly reported as
  unavailable when there isn't enough enrolled data yet.

## Data

Everything this service writes lives under `detection/backend/data/`
(gitignored): its own SQLite DB (`detection.db`), and collected face-crop
images (`face_training_samples/`). Nothing here is ever committed, and
nothing here is Parachute's data.
