import { useEffect, useState } from 'react'
import { api } from '../api'
import CameraTile from '../components/CameraTile'
import CameraModal from '../components/CameraModal'
import './pages.css'

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
    </div>
  )
}
