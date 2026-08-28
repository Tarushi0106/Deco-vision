import { useEffect, useState } from 'react'
import { api } from '../api'
import CameraTile from '../components/CameraTile'
import CameraModal from '../components/CameraModal'
import './pages.css'

function StatTile({ label, value, sub }) {
  return (
    <div className="stat-tile card">
      <div className="stat-tile-label">{label}</div>
      <div className="stat-tile-value">{value}</div>
      {sub && <div className="stat-tile-sub">{sub}</div>}
    </div>
  )
}

function timeAgo(ts) {
  const seconds = Math.floor(Date.now() / 1000 - ts)
  if (seconds < 60) return `${seconds}s ago`
  const mins = Math.floor(seconds / 60)
  if (mins < 60) return `${mins}m ago`
  return `${Math.floor(mins / 60)}h ago`
}

function IntrusionWindowCard() {
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [status, setStatus] = useState(null)

  useEffect(() => {
    api.getSettings().then((s) => {
      setStart(s.restricted_start || '')
      setEnd(s.restricted_end || '')
    }).catch(() => {})
  }, [])

  const handleSave = async (e) => {
    e.preventDefault()
    setStatus({ loading: true })
    try {
      await api.updateSettings({ restricted_start: start, restricted_end: end })
      setStatus({ loading: false, saved: true })
    } catch (err) {
      setStatus({ loading: false, error: err.message })
    }
  }

  return (
    <div className="card panel">
      <div className="panel-header">
        <h3>Intrusion Window</h3>
      </div>
      <form onSubmit={handleSave} className="form-row" style={{ alignItems: 'center', gap: '0.5rem' }}>
        <label style={{ marginRight: '0.5rem' }}>From</label>
        <input type="time" value={start} onChange={(e) => setStart(e.target.value)} />
        <label style={{ margin: '0 0.5rem' }}>To</label>
        <input type="time" value={end} onChange={(e) => setEnd(e.target.value)} />
        <button type="submit" className="btn btn-primary" style={{ marginLeft: '0.75rem' }} disabled={status?.loading}>
          Save
        </button>
      </form>
      <div className="stat-tile-sub" style={{ marginTop: '0.5rem' }}>
        Any person seen on any camera in this window raises an intrusion alert. Leave blank to disable.
        {status?.saved && ' Saved.'}
        {status?.error && ` ${status.error}`}
      </div>
    </div>
  )
}

function DetectionRateCard() {
  const [fps, setFps] = useState(1)
  const [status, setStatus] = useState(null)

  useEffect(() => {
    api.getSettings().then((s) => setFps(s.detection_fps ?? 1)).catch(() => {})
  }, [])

  const handleSave = async (e) => {
    e.preventDefault()
    setStatus({ loading: true })
    try {
      await api.updateSettings({ detection_fps: fps })
      setStatus({ loading: false, saved: true })
    } catch (err) {
      setStatus({ loading: false, error: err.message })
    }
  }

  return (
    <div className="card panel">
      <div className="panel-header">
        <h3>Detection Rate</h3>
      </div>
      <form onSubmit={handleSave} className="form-row" style={{ alignItems: 'center', gap: '0.5rem' }}>
        <label style={{ marginRight: '0.5rem' }}>Frames/sec</label>
        <input
          type="number"
          min="0.2"
          max="15"
          step="0.2"
          value={fps}
          onChange={(e) => setFps(Number(e.target.value))}
          style={{ width: '5rem' }}
        />
        <button type="submit" className="btn btn-primary" style={{ marginLeft: '0.75rem' }} disabled={status?.loading}>
          Save
        </button>
      </form>
      <div className="stat-tile-sub" style={{ marginTop: '0.5rem' }}>
        How often face recognition runs per camera. Lower is easier on the machine, higher is more responsive.
        Video playback is unaffected either way â€” recognition runs in its own process.
        {status?.saved && ' Saved.'}
        {status?.error && ` ${status.error}`}
      </div>
    </div>
  )
}

export default function Dashboard() {
  const [cameras, setCameras] = useState([])
  const [stats, setStats] = useState(null)
  const [expanded, setExpanded] = useState(null)
  const [alerts, setAlerts] = useState([])

  const loadAlerts = () => {
    api.listAlerts({ resolved: false }).then(setAlerts).catch(() => {})
  }

  useEffect(() => {
    api.listCameras().then(setCameras).catch(() => {})
    api.getStats().then(setStats).catch(() => {})
    loadAlerts()
    const interval = setInterval(loadAlerts, 15000)
    return () => clearInterval(interval)
  }, [])

  const handleResolve = async (id) => {
    await api.resolveAlert(id)
    loadAlerts()
  }

  return (
    <div>
      <div className="stat-grid">
        <StatTile
          label="Active Cameras"
          value={stats ? stats.active_cameras : 'â€”'}
        />
        <StatTile
          label="Faces Enrolled"
          value={stats ? stats.faces_enrolled : 'â€”'}
          sub="in Allow List"
        />
        <StatTile
          label="Active Alerts"
          value={stats ? stats.active_alerts : 'â€”'}
          sub="fall + intrusion, unresolved"
        />
        <StatTile
          label="Detections Today"
          value={stats ? stats.detections_today : 'â€”'}
          sub="recognized sightings, deduped 30s"
        />
        <StatTile
          label="Footfall Today"
          value={stats ? `${stats.footfall_in_today} in / ${stats.footfall_out_today} out` : 'â€”'}
          sub="midline crossings, all cameras"
        />
      </div>

      <div className="dashboard-main-grid">
        <div className="card panel">
          <div className="panel-header">
            <h3>Live Camera Overview</h3>
            <span className="pill pill-success">
              <span className="dot dot-success" /> LIVE
            </span>
          </div>
          <div className="camera-grid">
            {cameras.map((cam) =>
              expanded?.id === cam.id ? (
                // already streaming full-res + overlay in the modal below â€”
                // avoid opening a second, redundant pair of sockets for the
                // same camera just to render a thumbnail behind it
                <div key={cam.id} className="camera-tile card camera-tile-clickable" onClick={() => setExpanded(null)}>
                  <div className="camera-tile-video">
                    <div className="camera-tile-offline">Viewing below â€” click to close</div>
                  </div>
                  <div className="camera-tile-label">
                    <span>{cam.name}</span>
                    <span className="camera-tile-site">{cam.site}</span>
                  </div>
                </div>
              ) : (
                <CameraTile
                  key={cam.id}
                  camera={cam}
                  showOverlay={false}
                  onClick={() => setExpanded(cam)}
                />
              )
            )}
          </div>
        </div>

        <div className="card panel">
          <div className="panel-header">
            <h3>Live Alerts</h3>
          </div>
          {alerts.length === 0 ? (
            <div className="empty-state">No active alerts.</div>
          ) : (
            <table>
              <tbody>
                {alerts.map((alert) => (
                  <tr key={alert.id}>
                    <td>
                      <span className="pill pill-danger">{alert.type === 'fall' ? 'FALL' : 'INTRUSION'}</span>
                    </td>
                    <td>{alert.camera_name}</td>
                    <td>{alert.message}</td>
                    <td>{timeAgo(alert.ts)}</td>
                    <td>
                      <button className="btn btn-outline" onClick={() => handleResolve(alert.id)}>
                        Resolve
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <IntrusionWindowCard />
        <DetectionRateCard />
      </div>

      {expanded && <CameraModal camera={expanded} onClose={() => setExpanded(null)} />}
    </div>
  )
}
