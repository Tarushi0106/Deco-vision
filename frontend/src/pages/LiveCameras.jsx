import { useEffect, useState } from 'react'
import { api } from '../api'
import CameraTile from '../components/CameraTile'
import CameraModal from '../components/CameraModal'
import './pages.css'

function formatTime(ts) {
  return new Date(ts * 1000).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })
}

function formatClipTime(ts) {
  return new Date(ts * 1000).toLocaleString('en-IN', {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function AttendancePanel() {
  const [rows, setRows] = useState([])

  useEffect(() => {
    const load = () => api.getAttendance().then(setRows).catch(() => {})
    load()
    const interval = setInterval(load, 15000)
    return () => clearInterval(interval)
  }, [])

  return (
    <div className="card panel">
      <div className="panel-header">
        <h3>Today's Attendance</h3>
      </div>
      {rows.length === 0 ? (
        <div className="empty-state">No recognized sightings yet today.</div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>First Seen</th>
              <th>Last Seen</th>
              <th>Cameras</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.name}>
                <td>{row.name}</td>
                <td>{formatTime(row.first_seen)}</td>
                <td>{formatTime(row.last_seen)}</td>
                <td>{row.cameras}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

function RecentClipsPanel() {
  const [clips, setClips] = useState([])
  const [playingClip, setPlayingClip] = useState(null)

  useEffect(() => {
    const load = () => api.getRecentClips(50).then(setClips).catch(() => {})
    load()
    const interval = setInterval(load, 15000)
    return () => clearInterval(interval)
  }, [])

  return (
    <div className="card panel">
      <div className="panel-header">
        <h3>Recent Clips</h3>
        <span className="stat-tile-sub">{clips.length} clip{clips.length === 1 ? '' : 's'}</span>
      </div>

      {playingClip && (
        <video
          key={playingClip.id}
          src={api.clipVideoUrl(playingClip.id)}
          controls
          autoPlay
          style={{ width: '100%', borderRadius: 8, marginBottom: '0.75rem', background: '#000' }}
        />
      )}

      {clips.length === 0 ? (
        <div className="empty-state">No clips recorded yet.</div>
      ) : (
        <div className="clips-list">
          {clips.map((clip) => (
            <div
              key={clip.id}
              className={`clips-list-row${playingClip?.id === clip.id ? ' clips-list-row-active' : ''}`}
              onClick={() => setPlayingClip(clip)}
            >
              <span>{clip.person_name}</span>
              <span className="camera-tile-site">{clip.camera_name}</span>
              <span>{formatClipTime(clip.ts)}</span>
            </div>
          ))}
        </div>
      )}
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

      <div className="dashboard-main-grid" style={{ marginTop: '1rem', gridTemplateColumns: '1fr 1fr' }}>
        <AttendancePanel />
        <RecentClipsPanel />
      </div>
    </div>
  )
}
