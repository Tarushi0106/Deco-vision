import { useEffect, useRef, useState } from 'react'
import { api, WS_HOST, WS_PROTOCOL } from '../api'
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

// Live camera feed with existing desk zones drawn over it, plus click-drag
// to define a new one. Deliberately reuses CameraTile's ws/live pattern
// rather than a still snapshot — a live feed makes it obvious where each
// desk boundary should go while drawing, and needs no separate "take a
// snapshot" endpoint.
function ZoneEditor({ camera, zones, onZoneDrawn }) {
  const canvasRef = useRef(null)
  const overlayRef = useRef(null)
  const containerRef = useRef(null)
  const [status, setStatus] = useState('connecting')
  const [drag, setDrag] = useState(null) // {x1,y1,x2,y2} in fractions, while actively dragging

  useEffect(() => {
    if (!camera?.live) {
      setStatus('offline')
      return
    }
    const canvas = canvasRef.current
    const overlay = overlayRef.current
    const ctx = canvas.getContext('2d')
    const img = new Image()
    let objectUrl = null

    const ws = new WebSocket(`${WS_PROTOCOL}://${WS_HOST}/ws/live/${camera.id}`)
    ws.binaryType = 'blob'
    ws.onopen = () => setStatus('live')
    ws.onclose = () => setStatus('offline')
    ws.onerror = () => setStatus('offline')
    ws.onmessage = (event) => {
      if (objectUrl) URL.revokeObjectURL(objectUrl)
      objectUrl = URL.createObjectURL(event.data)
      img.src = objectUrl
    }
    img.onload = () => {
      if (canvas.width !== img.width || canvas.height !== img.height) {
        canvas.width = img.width
        canvas.height = img.height
        overlay.width = img.width
        overlay.height = img.height
      }
      ctx.drawImage(img, 0, 0)
    }

    return () => {
      ws.close()
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [camera?.id, camera?.live])

  // Redraw the zone overlay whenever zones, the in-progress drag rect, or
  // the canvas size changes.
  useEffect(() => {
    const overlay = overlayRef.current
    if (!overlay) return
    const octx = overlay.getContext('2d')
    octx.clearRect(0, 0, overlay.width, overlay.height)
    octx.lineWidth = 3
    octx.font = '16px sans-serif'
    octx.textBaseline = 'bottom'

    zones.forEach((zone, i) => {
      const color = ZONE_COLORS[i % ZONE_COLORS.length]
      const x = zone.x1 * overlay.width
      const y = zone.y1 * overlay.height
      const w = (zone.x2 - zone.x1) * overlay.width
      const h = (zone.y2 - zone.y1) * overlay.height
      octx.strokeStyle = color
      octx.strokeRect(x, y, w, h)
      const labelWidth = octx.measureText(zone.zone_label).width + 8
      octx.fillStyle = color
      octx.fillRect(x, y - 20, labelWidth, 20)
      octx.fillStyle = 'white'
      octx.fillText(zone.zone_label, x + 4, y - 4)
    })

    if (drag) {
      const x = Math.min(drag.x1, drag.x2) * overlay.width
      const y = Math.min(drag.y1, drag.y2) * overlay.height
      const w = Math.abs(drag.x2 - drag.x1) * overlay.width
      const h = Math.abs(drag.y2 - drag.y1) * overlay.height
      octx.strokeStyle = '#c62828'
      octx.setLineDash([6, 4])
      octx.strokeRect(x, y, w, h)
      octx.setLineDash([])
    }
  }, [zones, drag])

  const fracFromEvent = (e) => {
    const rect = containerRef.current.getBoundingClientRect()
    return {
      x: Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width)),
      y: Math.min(1, Math.max(0, (e.clientY - rect.top) / rect.height)),
    }
  }

  const handleMouseDown = (e) => {
    const { x, y } = fracFromEvent(e)
    setDrag({ x1: x, y1: y, x2: x, y2: y })
  }
  const handleMouseMove = (e) => {
    if (!drag) return
    const { x, y } = fracFromEvent(e)
    setDrag((d) => ({ ...d, x2: x, y2: y }))
  }
  const handleMouseUp = () => {
    if (!drag) return
    const x1 = Math.min(drag.x1, drag.x2)
    const y1 = Math.min(drag.y1, drag.y2)
    const x2 = Math.max(drag.x1, drag.x2)
    const y2 = Math.max(drag.y1, drag.y2)
    if (x2 - x1 > 0.02 && y2 - y1 > 0.02) {
      onZoneDrawn({ x1, y1, x2, y2 })
    }
    setDrag(null)
  }

  return (
    <div
      className="desk-zone-editor"
      ref={containerRef}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={() => setDrag(null)}
    >
      <canvas ref={canvasRef} className="desk-zone-video" />
      <canvas ref={overlayRef} className="desk-zone-overlay" />
      {status !== 'live' && <div className="camera-tile-offline">{status === 'offline' ? 'Camera offline' : 'Connecting…'}</div>}
    </div>
  )
}

function ZoneManager() {
  const [cameras, setCameras] = useState([])
  const [cameraId, setCameraId] = useState(null)
  const [zones, setZones] = useState([])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

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

  // A zone is a plain "this rectangle is a desk" declaration — no employee
  // to pick. It saves the moment it's drawn; who ends up occupying it is
  // resolved automatically, every detection cycle, from face recognition
  // (see desk_tracker.py) — never assigned here.
  const handleZoneDrawn = async (rect) => {
    setError(null)
    setSaving(true)
    try {
      await api.createDeskZone({ camera_id: cameraId, ...rect })
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

  return (
    <div className="card panel">
      <div className="panel-header">
        <h3>Desk Zones</h3>
        <select value={cameraId ?? ''} onChange={(e) => setCameraId(Number(e.target.value))}>
          {cameras.map((c) => (
            <option key={c.id} value={c.id}>{c.name}</option>
          ))}
        </select>
      </div>
      <div className="stat-tile-sub" style={{ marginBottom: '0.6rem' }}>
        Click and drag on the feed below to mark a desk — it's auto-labeled ("Desk 1", "Desk 2", …) and saved
        immediately. Who's sitting there is detected automatically, not assigned here.
      </div>

      {camera ? (
        <ZoneEditor camera={camera} zones={zones} onZoneDrawn={handleZoneDrawn} />
      ) : (
        <div className="empty-state">No cameras configured.</div>
      )}

      {saving && <div className="stat-tile-sub" style={{ marginTop: '0.5rem' }}>Saving zone…</div>}
      {error && <div className="form-message error">{error}</div>}

      {zones.length > 0 && (
        <div className="desk-zone-list">
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
    <div className="card panel">
      <div className="panel-header">
        <h3>Desk Time Report</h3>
        <input type="date" value={date} max={todayStr()} onChange={(e) => setDate(e.target.value)} />
      </div>
      {error && <div className="form-message error">{error}</div>}
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
