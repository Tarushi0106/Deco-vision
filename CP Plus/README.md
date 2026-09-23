# CP Plus — Detection (isolated project)

Person detection and continuous face-training, built as a **completely
separate application** from Parachute — its own backend, its own
frontend, its own database, its own dependencies. It talks to Parachute
only through Parachute's existing public HTTP/WebSocket API, never by
importing Parachute's Python modules or reading its database directly.

```
CP Plus/
├── backend/    — FastAPI app, own port (8812), own SQLite DB, own models
└── frontend/   — Vite + React app, own port (dev: 5173), own routing
```

See `backend/README.md` for the exact API surface this talks to on
Parachute, and what each of the two directions of communication carries.

## Running both, next to Parachute

```bash
# Terminal 1 — Parachute backend (existing, unchanged)
cd backend && venv/Scripts/activate && uvicorn app.main:app --port 8811

# Terminal 2 — Parachute frontend (existing, unchanged)
cd frontend && npm run dev

# Terminal 3 — CP Plus backend (this project)
cd "CP Plus/backend" && venv/Scripts/activate && uvicorn app.main:app --port 8812

# Terminal 4 — CP Plus frontend (this project)
cd "CP Plus/frontend" && npm run dev
```

## Deployment

Not wired into Parachute's existing `.github/workflows/deploy.yml` —
that workflow only builds/restarts the existing `backend/`/`frontend/`
and is unchanged. Deploying this project (a systemd service for
`CP Plus/backend`, a build+serve step for `CP Plus/frontend`, and
whatever reverse-proxy routing is wanted) is a separate, deliberate step
for whoever operates the production server, not something this repo
change does automatically.
