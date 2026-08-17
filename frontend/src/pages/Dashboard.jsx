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

export default function Dashboard() {
  const [cameras, setCameras] = useState([])
  const [stats, setStats] = useState(null)
  const [expanded, setExpanded] = useState(null)

  useEffect(() => {
    api.listCameras().then(setCameras).catch(() => {})
    api.getStats().then(setStats).catch(() => {})
  }, [])

  return (
    <div>
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
          sub="no alerting system built yet"
        />
        <StatTile
          label="Detections Today"
          value={stats ? stats.detections_today : '—'}
          sub="recognized sightings, deduped 30s"
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
            {cameras.map((cam) => (
              <CameraTile
                key={cam.id}
                camera={cam}
                showOverlay={cam.live}
                onClick={() => setExpanded(cam)}
              />
            ))}
          </div>
        </div>

        <div className="card panel">
          <div className="panel-header">
            <h3>Live Alerts</h3>
          </div>
          <div className="empty-state">No active alerts.</div>
        </div>
      </div>

      {expanded && <CameraModal camera={expanded} onClose={() => setExpanded(null)} />}
    </div>
  )
}
