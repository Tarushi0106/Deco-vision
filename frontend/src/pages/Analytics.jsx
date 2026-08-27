import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import DeskAnalyticsPanel from './DeskAnalytics'
import './pages.css'

function todayStr() {
  return new Date().toISOString().slice(0, 10)
}

// Must match backend config.CLIP_RETENTION_DAYS — recordings older than
// this are deleted by the nightly prune job (scheduler.py), so a date
// picker shouldn't invite picking one that can't have anything left.
const CLIP_RETENTION_DAYS = 7

function retentionMinDateStr() {
  const d = new Date()
  d.setDate(d.getDate() - (CLIP_RETENTION_DAYS - 1))
  return d.toISOString().slice(0, 10)
}

function formatTimestamp(ts) {
  return new Date(ts * 1000).toLocaleString('en-IN', {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function formatTime(ts) {
  return new Date(ts * 1000).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })
}

function formatDuration(seconds) {
  if (seconds == null) return null
  return seconds >= 60 ? `${Math.round(seconds / 60)}m ${Math.round(seconds % 60)}s` : `${Math.round(seconds)}s`
}

function formatDateLabel(dateStr) {
  return new Date(`${dateStr}T00:00:00`).toLocaleDateString('en-IN', {
    day: '2-digit', month: 'short', year: 'numeric',
  })
}

// Person + date search over recognition clips — folded into the existing
// per-person Clips view rather than a separate page: opened either from a
// row's "View" button (no date filter, shows whatever's left within the
// retention window) or from the Daily Activity search bar above the table
// (a specific date pre-filled). Clips are fetched once per person and
// filtered/sorted client-side, since the rolling retention window (see
// config.CLIP_RETENTION_DAYS, enforced by scheduler.py's nightly prune)
// already keeps the working set small.
function DailyActivityModal({ personName, initialDate, onClose }) {
  const [clips, setClips] = useState(null)
  const [error, setError] = useState(null)
  const [date, setDate] = useState(initialDate || '')
  const [playingClip, setPlayingClip] = useState(null)
  const [playAll, setPlayAll] = useState(false)
  // A camera-1 clip not yet cached locally is fetched from the camera's own
  // recording on first play (see main.py's get_clip_video) — that can take
  // 2-3 minutes, and fails outright if the footage is older than the
  // camera's own retention window (its onboard storage overwrites itself
  // after a few days — confirmed live: a clip from 5 days back came back
  // with literally no stream data, while one from a day back played fine).
  // Both states need to be visible instead of a silently blank/broken player.
  const [playStatus, setPlayStatus] = useState(null) // 'loading' | 'error' | null

  // A specific date goes through clips-for-day, which reconstructs any
  // sightings missing a clips row (e.g. deleted by the old prune-to-30 cap)
  // straight from the camera's own recording — so "all clips of that day"
  // is actually complete, not just whatever happened to still be logged.
  // "All dates" has no day to reconstruct against, so it stays on the plain
  // list.
  useEffect(() => {
    setClips(null)
    const request = date ? api.getClipsForDay(personName, date) : api.getClips(personName)
    request.then(setClips).catch((err) => setError(err.message))
  }, [personName, date])

  // Chronological (ascending) within a day so "Play All" reads as the
  // person's actual timeline, not most-recent-first like the row-level view.
  const shown = useMemo(() => {
    if (!clips) return []
    return [...clips].sort((a, b) => (date ? a.ts - b.ts : b.ts - a.ts))
  }, [clips, date])

  const playIndex = playingClip ? shown.findIndex((c) => c.id === playingClip.id) : -1

  const handleClipEnded = () => {
    if (!playAll) return
    const next = shown[playIndex + 1]
    if (next) {
      setPlayStatus('loading')
      setPlayingClip(next)
    } else {
      setPlayAll(false)
    }
  }

  const handlePlayAll = () => {
    if (shown.length === 0) return
    setPlayAll(true)
    setPlayStatus('loading')
    setPlayingClip(shown[0])
  }

  const handleSelectClip = (clip) => {
    setPlayAll(false)
    setPlayStatus('loading')
    setPlayingClip(clip)
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="card modal daily-activity-modal" onClick={(e) => e.stopPropagation()}>
        <h3>{personName} — {date ? `Daily Activity — ${formatDateLabel(date)}` : 'Clips'}</h3>

        <div className="daily-activity-controls">
          <label style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>Date</label>
          <input
            type="date"
            value={date}
            min={retentionMinDateStr()}
            max={todayStr()}
            onChange={(e) => setDate(e.target.value)}
          />
          {date && (
            <button type="button" className="btn btn-outline" onClick={() => setDate('')}>
              All dates
            </button>
          )}
          <button
            type="button"
            className="btn btn-outline"
            disabled={shown.length === 0}
            onClick={handlePlayAll}
          >
            ▶ Play All
          </button>
        </div>

        {error && <div className="form-message error">{error}</div>}

        {playingClip && playStatus === 'error' && (
          <div className="form-message error" style={{ margin: '0.75rem 0' }}>
            This footage is no longer available — the camera's own onboard recording only keeps a few days of
            history, and this clip is older than that.
          </div>
        )}

        {playingClip && playStatus !== 'error' && (
          <>
            {playStatus === 'loading' && (
              <div className="stat-tile-sub" style={{ margin: '0.75rem 0 0.35rem' }}>
                Fetching from the camera's own recording — older footage can take a couple of minutes…
              </div>
            )}
            <video
              key={playingClip.id}
              src={api.clipVideoUrl(playingClip.id)}
              controls
              autoPlay
              onEnded={handleClipEnded}
              onPlaying={() => setPlayStatus(null)}
              onError={() => setPlayStatus('error')}
              style={{ width: '100%', borderRadius: 8, margin: '0.75rem 0', background: '#000' }}
            />
          </>
        )}

        {clips === null ? (
          <div className="empty-state">Loading…</div>
        ) : shown.length === 0 ? (
          <div className="empty-state">
            {date
              ? `No activity found for ${personName} on this date.`
              : 'No clips yet.'}
          </div>
        ) : (
          <div className="clips-list">
            {shown.map((clip) => (
              <div
                key={clip.id}
                className={`clips-list-row${playingClip?.id === clip.id ? ' clips-list-row-active' : ''}`}
                onClick={() => handleSelectClip(clip)}
              >
                <span>{date ? formatTime(clip.ts) : formatTimestamp(clip.ts)}</span>
                <span className="camera-tile-site">{clip.camera_name}</span>
                {formatDuration(clip.duration) && (
                  <span className="stat-tile-sub">{formatDuration(clip.duration)}</span>
                )}
                <span className="clips-list-play">▶ Clip</span>
              </div>
            ))}
          </div>
        )}

        <div className="modal-actions">
          <button type="button" className="btn btn-outline" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  )
}

// No clip count shown here by design — just whether recording is live right
// now, plus a View button straight into Daily Activity (which handles the
// "nothing recorded" case itself, per date, with its own empty state).
function ClipsCell({ personName, recordingCameraName }) {
  const [showModal, setShowModal] = useState(false)

  return (
    <>
      {recordingCameraName && (
        <span className="pill pill-danger" style={{ marginRight: '0.5rem' }}>
          <span className="dot dot-danger" /> LIVE — {recordingCameraName}
        </span>
      )}
      <button type="button" className="btn btn-outline" onClick={() => setShowModal(true)}>
        View
      </button>
      {showModal && (
        <DailyActivityModal personName={personName} initialDate={null} onClose={() => setShowModal(false)} />
      )}
    </>
  )
}

function PeopleAnalyticsPanel() {
  const [days, setDays] = useState(7)
  const [rows, setRows] = useState([])
  const [activeByName, setActiveByName] = useState({})
  const [searchPerson, setSearchPerson] = useState('')
  const [searchDate, setSearchDate] = useState(todayStr())
  const [activity, setActivity] = useState(null) // { person, date } while the modal is open

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
      <div className="page-toolbar" style={{ marginBottom: '0.75rem' }}>
        <div className="page-toolbar-sub">Per-person visit patterns from recognized sightings</div>
        <select value={days} onChange={(e) => setDays(Number(e.target.value))} className="btn btn-outline">
          <option value={1}>Last 24 hours</option>
          <option value={7}>Last 7 days</option>
        </select>
      </div>

      <div className="card daily-activity-search">
        <div className="daily-activity-search-title">Daily Activity — search one person's sightings for a day</div>
        <div className="daily-activity-controls">
          <label>Person</label>
          <select value={searchPerson} onChange={(e) => setSearchPerson(e.target.value)}>
            <option value="">Select person…</option>
            {rows.map((r) => (
              <option key={r.name} value={r.name}>{r.name}</option>
            ))}
          </select>
          <label>Date</label>
          <input
            type="date"
            value={searchDate}
            min={retentionMinDateStr()}
            max={todayStr()}
            onChange={(e) => setSearchDate(e.target.value)}
          />
          <button
            type="button"
            className="btn btn-primary"
            disabled={!searchPerson}
            onClick={() => setActivity({ person: searchPerson, date: searchDate })}
          >
            Search
          </button>
        </div>
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

      {activity && (
        <DailyActivityModal
          personName={activity.person}
          initialDate={activity.date}
          onClose={() => setActivity(null)}
        />
      )}
    </div>
  )
}

const TABS = [
  { key: 'people', label: 'People Analytics' },
  { key: 'desk', label: 'Desk Analytics' },
]

export default function Analytics() {
  const [tab, setTab] = useState('people')

  return (
    <div>
      <div className="page-toolbar">
        <div>
          <h2>Workforce Analytics</h2>
          <div className="page-toolbar-sub">
            {tab === 'people'
              ? 'Face recognition visit patterns per person'
              : 'Automatic desk occupancy and time tracking'}
          </div>
        </div>
        <div className="tab-switcher">
          {TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              className={`btn ${tab === t.key ? 'btn-primary' : 'btn-outline'}`}
              onClick={() => setTab(t.key)}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {tab === 'people' ? <PeopleAnalyticsPanel /> : <DeskAnalyticsPanel />}
    </div>
  )
}
