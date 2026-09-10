import { useEffect, useState } from 'react'
import { api } from '../api'
import CameraTile from '../components/CameraTile'
import './pages.css'
import './deskAnalytics.css'

function todayStr() {
  return new Date().toISOString().slice(0, 10)
}

function formatDuration(seconds) {
  if (seconds == null) return '—'
  const h = Math.floor(seconds / 3600)
  const m = Math.round((seconds % 3600) / 60)
  if (h === 0) return `${m}m`
  return `${h}h ${m}m`
}

function formatTime(ts) {
  if (ts == null) return '—'
  return new Date(ts * 1000).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })
}

const ZONE_COLORS = ['#2f6fed', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#4a3aa7']

const STATUS_LABEL = { at_desk: 'At Desk', away: 'Away', unknown: 'Unknown' }
const STATUS_PILL_CLASS = { at_desk: 'pill-success', away: 'pill-danger', unknown: 'pill-neutral' }

// zone_label -> name is the only reshaping CameraTile's overlay needs;
// polygon already comes back from the API as a real point list (desk_db.py
// fills one in for every zone, rectangle-only ones included), same shape
// restricted zones already use — this is what gives desk zones the same
// click-N-dots free-shape drawing as the Intrusion page instead of a fixed
// drag-rectangle.
function toOverlayZones(zones) {
  return zones.map((z) => ({ ...z, name: z.zone_label }))
}

function ZoneManager() {
  const [cameras, setCameras] = useState([])
  const [cameraId, setCameraId] = useState(null)
  const [zones, setZones] = useState([])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const [drawMode, setDrawMode] = useState(false)
  const [draftPoints, setDraftPoints] = useState([]) // pixel coords, native frame resolution — see frameSize
  const [frameSize, setFrameSize] = useState(null) // {width, height}, reported by CameraTile as frames arrive

  useEffect(() => {
    api.listCameras().then((cams) => {
      setCameras(cams)
      if (cams.length > 0) setCameraId(cams[0].id)
    }).catch(() => {})
  }, [])

  const loadZones = (camId) => {
    if (!camId) return
    api.listDeskZones(camId).then(setZones).catch(() => {})
  }

  useEffect(() => {
    loadZones(cameraId)
  }, [cameraId])

  const camera = cameras.find((c) => c.id === cameraId)

  const startDraw = () => {
    setDrawMode(true)
    setDraftPoints([])
    setError(null)
  }
  const cancelDraw = () => {
    setDrawMode(false)
    setDraftPoints([])
  }
  const handleAddPoint = (x, y) => {
    setDraftPoints((pts) => [...pts, [x, y]])
  }

  // A zone is a plain "this shape is a desk" declaration — no employee to
  // pick. It saves the moment it's drawn; who ends up occupying it is
  // resolved automatically, every detection cycle, from face recognition
  // (see desk_tracker.py) — never assigned here. draftPoints are in native
  // frame PIXEL coords (what CameraTile's click handler reports); desk
  // zones are stored as 0..1 FRACTIONS of frame width/height instead (see
  // desk_db.py) so a saved shape still lines up correctly if the camera's
  // resolution ever changes — frameSize (reported by CameraTile as the
  // live feed's actual decoded size) is what makes that conversion exact
  // rather than guessed.
  const handleFinishShape = async () => {
    if (draftPoints.length < 3 || !frameSize) return
    setError(null)
    setSaving(true)
    try {
      const polygon = draftPoints.map(([x, y]) => [x / frameSize.width, y / frameSize.height])
      await api.createDeskZone({ camera_id: cameraId, polygon })
      cancelDraw()
      loadZones(cameraId)
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  const handleDeleteZone = async (zoneId) => {
    await api.deleteDeskZone(zoneId)
    loadZones(cameraId)
  }

  // Clicking directly on a drawn desk in the video is the same delete as
  // the Remove button in the list below — just a faster path once you can
  // see the shape on screen. Confirms first: a single misclick on the
  // feed is much easier to trigger by accident than the deliberate act of
  // finding and clicking a specific row's Remove button.
  const handleZoneClick = (zone) => {
    if (window.confirm(`Remove ${zone.zone_label}?`)) handleDeleteZone(zone.id)
  }

  return (
    <div className="card panel desk-analytics-col">
      <div className="panel-header">
        <h3>Desk Zones</h3>
        <select
          value={cameraId ?? ''}
          onChange={(e) => {
            setCameraId(Number(e.target.value))
            cancelDraw()
          }}
        >
          {cameras.map((c) => (
            <option key={c.id} value={c.id}>{c.name}</option>
          ))}
        </select>
      </div>

      {!drawMode ? (
        <>
          <div className="stat-tile-sub" style={{ marginBottom: '0.6rem' }}>
            Click "Draw New Desk", then click points on the feed below to trace any shape (3 points for a
            triangle, 4+ for any polygon) — it's auto-labeled ("Desk 1", "Desk 2", …). Who's sitting there is
            detected automatically, not assigned here. Click an existing desk's outline to remove it.
          </div>
          <button className="btn btn-primary" onClick={startDraw} disabled={!camera} style={{ marginBottom: '0.6rem' }}>
            + Draw New Desk
          </button>
        </>
      ) : (
        <div className="zone-draw-controls" style={{ marginBottom: '0.6rem' }}>
          <span className="stat-tile-sub">{draftPoints.length} point(s) placed</span>
          <button className="btn btn-outline" onClick={cancelDraw}>Cancel</button>
          <button className="btn btn-primary" onClick={handleFinishShape} disabled={draftPoints.length < 3 || saving}>
            Finish Shape
          </button>
        </div>
      )}

      {camera ? (
        <CameraTile
          camera={camera}
          large
          showOverlay
          zones={toOverlayZones(zones)}
          drawMode={drawMode}
          draftPoints={draftPoints}
          onAddPoint={handleAddPoint}
          onFrameSize={setFrameSize}
          onZoneClick={handleZoneClick}
          zonePointSpace="fraction"
        />
      ) : (
        <div className="empty-state">No cameras configured.</div>
      )}

      {saving && <div className="stat-tile-sub" style={{ marginTop: '0.5rem' }}>Saving desk…</div>}
      {error && <div className="form-message error">{error}</div>}

      {zones.length > 0 && (
        <div className="desk-zone-list desk-analytics-scroll">
          {zones.map((z, i) => (
            <div key={z.id} className="desk-zone-list-row">
              <span className="dot" style={{ background: ZONE_COLORS[i % ZONE_COLORS.length] }} />
              <span>{z.zone_label}</span>
              <button className="btn btn-outline" onClick={() => handleDeleteZone(z.id)}>Remove</button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function ReportView() {
  const [date, setDate] = useState(todayStr())
  const [report, setReport] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    setError(null)
    const load = () => api.getDeskAnalyticsReport(date).then(setReport).catch((err) => setError(err.message))
    load()
    // Poll while viewing today, so Current Desk/Status stay live without a
    // manual refresh — harmless for a past date too (nothing changes there).
    const interval = setInterval(load, 15000)
    return () => clearInterval(interval)
  }, [date])

  return (
    <div className="card panel desk-analytics-col">
      <div className="panel-header">
        <h3>Desk Time Report</h3>
        <input type="date" value={date} max={todayStr()} onChange={(e) => setDate(e.target.value)} />
      </div>
      {error && <div className="form-message error">{error}</div>}
      <div className="desk-analytics-scroll">
        <table>
          <thead>
            <tr>
              <th>Employee</th>
              <th>Current Desk</th>
              <th>Current Status</th>
              <th>Total Desk Time</th>
              <th>Total Time Away</th>
              <th>First Session</th>
              <th>Last Session</th>
            </tr>
          </thead>
          <tbody>
            {!report || report.employees.length === 0 ? (
              <tr>
                <td colSpan={7} className="empty-state">No employees enrolled yet.</td>
              </tr>
            ) : (
              report.employees.map((e) => (
                <tr key={e.employee_name}>
                  <td>{e.employee_name}</td>
                  <td>{e.current_desk || '—'}</td>
                  <td>
                    <span className={`pill ${STATUS_PILL_CLASS[e.current_status]}`}>
                      {STATUS_LABEL[e.current_status]}
                    </span>
                  </td>
                  <td>{formatDuration(e.desk_seconds)}</td>
                  <td>{formatDuration(e.away_seconds)}</td>
                  <td>{formatTime(e.first_session)}</td>
                  <td>{formatTime(e.last_session)}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// Embedded as the "Desk Analytics" tab on the merged Analytics page — no
// page-level header of its own, the tab switcher above already carries that.
export default function DeskAnalyticsPanel() {
  return (
    <div>
      <div className="page-toolbar-sub" style={{ marginBottom: '0.75rem' }}>
        Fully automatic — desk assignment, desk-switching, and away tracking are all detected live from the same
        camera feed and face recognition as attendance, no manual assignment needed.
      </div>

      <div className="desk-analytics-grid">
        <ZoneManager />
        <ReportView />
      </div>
    </div>
  )
}
