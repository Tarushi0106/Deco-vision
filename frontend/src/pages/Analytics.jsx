import { useEffect, useState } from 'react'
import { api } from '../api'
import './pages.css'

function formatTimestamp(ts) {
  return new Date(ts * 1000).toLocaleString('en-IN', {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function ClipsModal({ personName, clips, onClose }) {
  const [playingClip, setPlayingClip] = useState(null)

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="card modal" style={{ width: 520 }} onClick={(e) => e.stopPropagation()}>
        <h3>{personName} — Clips</h3>

        {playingClip && (
          <video
            key={playingClip.id}
            src={api.clipVideoUrl(playingClip.id)}
            controls
            autoPlay
            style={{ width: '100%', borderRadius: 8, marginBottom: '0.75rem', background: '#000' }}
          />
        )}

        <div className="clips-list">
          {clips.map((clip) => (
            <div
              key={clip.id}
              className={`clips-list-row${playingClip?.id === clip.id ? ' clips-list-row-active' : ''}`}
              onClick={() => setPlayingClip(clip)}
            >
              <span>{formatTimestamp(clip.ts)}</span>
              <span className="camera-tile-site">{clip.camera_name}</span>
            </div>
          ))}
        </div>

        <div className="modal-actions">
          <button type="button" className="btn btn-outline" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  )
}

function ClipsCell({ personName, recordingCameraName }) {
  const [clips, setClips] = useState(null)
  const [showModal, setShowModal] = useState(false)

  useEffect(() => {
    api.getClips(personName).then(setClips).catch(() => setClips([]))
  }, [personName])

  if (clips === null) return <span className="stat-tile-sub">Loading…</span>

  const liveBadge = recordingCameraName && (
    <span className="pill pill-danger" style={{ marginRight: '0.5rem' }}>
      <span className="dot dot-danger" /> LIVE — {recordingCameraName}
    </span>
  )

  if (clips.length === 0) {
    return (
      <>
        {liveBadge}
        <span className="stat-tile-sub">
          {recordingCameraName ? 'Recording — check back shortly' : 'No clips yet'}
        </span>
      </>
    )
  }

  return (
    <>
      {liveBadge}
      <span style={{ marginRight: '0.5rem' }}>
        {clips.length} clip{clips.length === 1 ? '' : 's'}
      </span>
      <button type="button" className="btn btn-outline" onClick={() => setShowModal(true)}>
        View
      </button>
      {showModal && (
        <ClipsModal personName={personName} clips={clips} onClose={() => setShowModal(false)} />
      )}
    </>
  )
}

export default function Analytics() {
  const [days, setDays] = useState(7)
  const [rows, setRows] = useState([])
  const [activeByName, setActiveByName] = useState({})

  useEffect(() => {
    api.getPeopleAnalytics(days).then(setRows).catch(() => {})
  }, [days])

  useEffect(() => {
    const load = () => {
      api.getActiveClips()
        .then((active) => {
          const map = {}
          for (const a of active) map[a.person_name] = a.camera_name
          setActiveByName(map)
        })
        .catch(() => {})
    }
    load()
    const interval = setInterval(load, 5000)
    return () => clearInterval(interval)
  }, [])

  return (
    <div>
      <div className="page-toolbar">
        <div>
          <h2>Face Recognition Analytics</h2>
          <div className="page-toolbar-sub">Per-person visit patterns from recognized sightings</div>
        </div>
        <select value={days} onChange={(e) => setDays(Number(e.target.value))} className="btn btn-outline">
          <option value={1}>Last 24 hours</option>
          <option value={7}>Last 7 days</option>
          <option value={30}>Last 30 days</option>
        </select>
      </div>

      <div className="card">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Total Detections</th>
              <th>Days Seen</th>
              <th>Most Seen At</th>
              <th>Last Seen</th>
              <th>Clips</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={6} className="empty-state">
                  No recognized sightings in this period yet.
                </td>
              </tr>
            ) : (
              rows.map((row) => (
                <tr key={row.name}>
                  <td>{row.name}</td>
                  <td>{row.total_detections}</td>
                  <td>{row.days_seen}</td>
                  <td>{row.top_camera_name}</td>
                  <td>{formatTimestamp(row.last_seen)}</td>
                  <td>
                    <ClipsCell personName={row.name} recordingCameraName={activeByName[row.name]} />
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
