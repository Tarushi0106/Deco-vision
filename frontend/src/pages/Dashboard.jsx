import { useEffect, useState } from 'react'
import { api } from '../api'
import AlertBanner from '../components/AlertBanner'
import CameraTile from '../components/CameraTile'
import useLiveAlerts from '../hooks/useLiveAlerts'
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

const ALERT_TYPE_LABELS = {
  fall: 'FALL',
  intrusion: 'INTRUSION',
  zone_intrusion: 'ZONE INTRUSION',
  fire: 'FIRE',
  smoke: 'SMOKE',
}

function timeAgo(ts) {
  // Clamped at 0: a server clock running ahead of the viewer's browser
  // otherwise makes Date.now()/1000 - ts negative, showing a nonsensical
  // "-3713s ago" instead of just reading as "just now".
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - ts))
  if (seconds < 60) return `${seconds}s ago`
  const mins = Math.floor(seconds / 60)
  if (mins < 60) return `${mins}m ago`
  return `${Math.floor(mins / 60)}h ago`
}

export default function Dashboard() {
  const [cameras, setCameras] = useState([])
  const [stats, setStats] = useState(null)
  const alerts = useLiveAlerts()

  useEffect(() => {
    api.listCameras().then(setCameras).catch(() => {})
    api.getStats().then(setStats).catch(() => {})
  }, [])

  const handleResolve = async (id) => {
    await api.resolveAlert(id)
  }

  return (
    <div>
      <AlertBanner />
      <div className="stat-grid">
        <StatTile
          label="Active Cameras"
          value={stats ? stats.active_cameras : '—'}
          sub={stats ? `of ${stats.total_cameras} total` : ''}
        />
        <StatTile
          label="Faces Enrolled"
          value={stats ? stats.faces_enrolled : '—'}
          sub="in Allow List"
        />
        <StatTile
          label="Active Alerts"
          value={stats ? stats.active_alerts : '—'}
          sub="intrusion + zone, unresolved"
        />
        <StatTile
          label="Detections Today"
          value={stats ? stats.detections_today : '—'}
          sub="recognized sightings, deduped 30s"
        />
        <StatTile
          label="Footfall Today"
          value={stats ? `${stats.footfall_in_today} in / ${stats.footfall_out_today} out` : '—'}
          sub="midline crossings, all cameras"
        />
        <StatTile
          label="Cameras Given"
          value={stats ? stats.cameras_assigned : '—'}
          sub={stats ? `of ${stats.total_cameras} total, via a license` : ''}
        />
        <StatTile
          label="Cameras Accessed"
          value={stats ? stats.cameras_accessed : '—'}
          sub="assigned cameras currently live"
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
            {/* Status thumbnails only — no click-to-expand here. Expanding
                into a live feed shows the face-recognition overlay (see
                CameraModal), which belongs on the Live Cameras page, not
                the Dashboard. */}
            {cameras.map((cam) => (
              <CameraTile key={cam.id} camera={cam} showOverlay={false} />
            ))}
          </div>
        </div>

        <div className="card panel">
          <div className="panel-header">
            <h3>Live Alerts</h3>
          </div>
          {alerts.length === 0 ? (
            <div className="empty-state">No active alerts.</div>
          ) : (
            <div className="alerts-list">
              {alerts.map((alert) => (
                <div key={alert.id} className="alerts-list-row">
                  <div className="alerts-list-top">
                    <span className="pill pill-danger">{ALERT_TYPE_LABELS[alert.type] || alert.type.toUpperCase()}</span>
                    <span className="alerts-list-camera">{alert.camera_name}</span>
                    <span className="alerts-list-time">{timeAgo(alert.ts)}</span>
                  </div>
                  <div className="alerts-list-message">{alert.message}</div>
                  {alert.snapshot_path && (
                    <img
                      className="alerts-list-snapshot"
                      src={api.alertSnapshotUrl(alert.id)}
                      alt={`Snapshot: ${alert.message}`}
                    />
                  )}
                  <button className="btn btn-outline" onClick={() => handleResolve(alert.id)}>
                    Resolve
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
