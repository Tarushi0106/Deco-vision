import { useEffect, useState } from 'react'
import { api } from '../api'
import CameraTile from '../components/CameraTile'
import CameraModal from '../components/CameraModal'
import './pages.css'

// Face detection/recognition happens on every camera continuously in the
// backend regardless of which page is open — this control just tunes how
// often it samples each camera. Lives here (not Dashboard) since it's a
// face-recognition setting, and this is the page where recognized names
// actually show up (open a camera below to see the live overlay).
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
    <div className="card panel" style={{ marginTop: '1rem' }}>
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
        Video playback is unaffected either way — recognition runs in its own process.
        {status?.saved && ' Saved.'}
        {status?.error && ` ${status.error}`}
      </div>
    </div>
  )
}

export default function LiveCameras() {
  const [cameras, setCameras] = useState([])
  const [expanded, setExpanded] = useState(null)

  useEffect(() => {
    api.listCameras().then(setCameras).catch(() => {})
  }, [])

  return (
    <div>
      <div className="page-toolbar">
        <div>
          <h2>Live Cameras</h2>
          <div className="page-toolbar-sub">{cameras.length} feeds · realtime inference</div>
        </div>
        <span className="pill pill-success">
          <span className="dot dot-success" /> LIVE
        </span>
      </div>
      <div className="camera-grid" style={{ gridTemplateColumns: 'repeat(3, 1fr)' }}>
        {cameras.map((cam) =>
          expanded?.id === cam.id ? (
            // already streaming full-res + overlay in the modal below —
            // avoid opening a second, redundant pair of sockets for the
            // same camera just to render a thumbnail behind it
            <div key={cam.id} className="camera-tile card camera-tile-clickable" onClick={() => setExpanded(null)}>
              <div className="camera-tile-video">
                <div className="camera-tile-offline">Viewing below — click to close</div>
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

      {expanded && <CameraModal camera={expanded} onClose={() => setExpanded(null)} />}

      <DetectionRateCard />
    </div>
  )
}
