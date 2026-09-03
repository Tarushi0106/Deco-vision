import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import AlertBanner from '../components/AlertBanner'
import CameraTile from '../components/CameraTile'
import './pages.css'

// Clips for a smoke event are logged under this fixed pseudo "person" name
// (see backend pipeline.py's SMOKE_CLIP_SUBJECT) — reuses the exact same
// /api/clips lookup the People/Analytics pages use for real people.
const SMOKE_CLIP_SUBJECT = 'Smoke Alert'

function formatTimestamp(ts) {
  return new Date(ts * 1000).toLocaleString('en-IN', {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
  })
}

function formatDuration(seconds) {
  if (seconds == null) return null
  return seconds >= 60 ? `${Math.round(seconds / 60)}m ${Math.round(seconds % 60)}s` : `${Math.round(seconds)}s`
}

function timeAgo(ts) {
  const seconds = Math.floor(Date.now() / 1000 - ts)
  if (seconds < 60) return `${seconds}s ago`
  const mins = Math.floor(seconds / 60)
  if (mins < 60) return `${mins}m ago`
  return `${Math.floor(mins / 60)}h ago`
}

export default function SmokeDetection() {
  const [cameras, setCameras] = useState([])
  const [alerts, setAlerts] = useState([])
  const [clips, setClips] = useState(null)
  const [clipsError, setClipsError] = useState(null)
  const [playingClip, setPlayingClip] = useState(null)
  const [playStatus, setPlayStatus] = useState(null) // 'loading' | 'error' | null

  const loadAlerts = () => {
    api.listAlerts({ resolved: false })
      .then((all) => setAlerts(all.filter((a) => a.type === 'smoke' || a.type === 'fire')))
      .catch(() => {})
  }

  const loadClips = () => {
    api.getClips(SMOKE_CLIP_SUBJECT).then(setClips).catch((err) => setClipsError(err.message))
  }

  useEffect(() => {
    api.listCameras().then(setCameras).catch(() => {})
    loadAlerts()
    loadClips()
    const interval = setInterval(() => {
      loadAlerts()
      loadClips()
    }, 15000)
    return () => clearInterval(interval)
  }, [])

  const sortedClips = useMemo(() => (clips ? [...clips].sort((a, b) => b.ts - a.ts) : []), [clips])

  const handleResolve = async (id) => {
    await api.resolveAlert(id)
    loadAlerts()
  }

  const handleSelectClip = (clip) => {
    setPlayStatus('loading')
    setPlayingClip(clip)
  }

  return (
    <div>
      <AlertBanner />
      <div className="page-toolbar">
        <div>
          <h2>Smoke &amp; Fire Detection</h2>
          <div className="page-toolbar-sub">
            Cameras are watched for a genuinely growing haze (color + motion, not just a live model) and for
            flickering flame color. No box is drawn on the video for a suspected smoke region — a flashing dashboard
            alert and a saved clip are the signal instead, since a box on hazy/uncertain footage reads as an
            accusation more than a fire box does. Fire regions are boxed live, same as faces.
          </div>
        </div>
      </div>

      <div className="dashboard-main-grid">
        <div className="card panel">
          <div className="panel-header">
            <h3>Cameras</h3>
          </div>
          <div className="camera-grid">
            {cameras.map((cam) => (
              <CameraTile key={cam.id} camera={cam} showOverlay />
            ))}
          </div>
        </div>

        <div className="card panel">
          <div className="panel-header">
            <h3>Smoke &amp; Fire Alerts</h3>
          </div>
          {alerts.length === 0 ? (
            <div className="empty-state">No active smoke or fire alerts.</div>
          ) : (
            <div className="alerts-list">
              {alerts.map((alert) => (
                <div key={alert.id} className="alerts-list-row">
                  <div className="alerts-list-top">
                    <span className="pill pill-danger">{alert.type.toUpperCase()}</span>
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

        <div className="card panel" style={{ gridColumn: '1 / -1' }}>
          <div className="panel-header">
            <h3>Smoke Clips</h3>
          </div>
          {clipsError && <div className="form-message error">{clipsError}</div>}

          {playingClip && playStatus === 'error' && (
            <div className="form-message error" style={{ margin: '0.75rem 0' }}>
              This clip's footage is no longer available.
            </div>
          )}
          {playingClip && playStatus !== 'error' && (
            <>
              {playStatus === 'loading' && (
                <div className="stat-tile-sub" style={{ margin: '0.75rem 0 0.35rem' }}>
                  Loading clip…
                </div>
              )}
              <video
                key={playingClip.id}
                src={api.clipVideoUrl(playingClip.id)}
                controls
                autoPlay
                onPlaying={() => setPlayStatus(null)}
                onError={() => setPlayStatus('error')}
                style={{ width: '100%', maxWidth: 640, borderRadius: 8, margin: '0.75rem 0', background: '#000' }}
              />
            </>
          )}

          {clips === null ? (
            <div className="empty-state">Loading…</div>
          ) : sortedClips.length === 0 ? (
            <div className="empty-state">No smoke clips recorded yet.</div>
          ) : (
            <div className="clips-list">
              {sortedClips.map((clip) => (
                <div
                  key={clip.id}
                  className={`clips-list-row${playingClip?.id === clip.id ? ' clips-list-row-active' : ''}`}
                  onClick={() => handleSelectClip(clip)}
                >
                  <span>{formatTimestamp(clip.ts)}</span>
                  <span className="camera-tile-site">{clip.camera_name}</span>
                  {formatDuration(clip.duration) && (
                    <span className="stat-tile-sub">{formatDuration(clip.duration)}</span>
                  )}
                  <span className="clips-list-play">▶ Clip</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
