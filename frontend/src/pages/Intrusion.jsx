import { useEffect, useState } from 'react'
import { api } from '../api'
import AlertBanner from '../components/AlertBanner'
import CameraTile from '../components/CameraTile'
import './pages.css'

const ZONE_BLANK = { name: '', allowed_names: [], restricted_start: '', restricted_end: '' }

const INTRUSION_ALERT_TYPES = new Set(['zone_intrusion', 'intrusion'])
const INTRUSION_TYPE_LABELS = { zone_intrusion: 'ZONE INTRUSION', intrusion: 'INTRUSION' }

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
        Anybody seen on any camera in this window raises an intrusion alert. Leave blank to disable.
        {status?.saved && ' Saved.'}
        {status?.error && ` ${status.error}`}
      </div>
    </div>
  )
}

function ZoneForm({ points, people, initial, cameraId, zoneId, onCancel, onSaved }) {
  const [form, setForm] = useState(
    initial
      ? {
          name: initial.name,
          allowed_names: initial.allowed_names,
          restricted_start: initial.restricted_start || '',
          restricted_end: initial.restricted_end || '',
        }
      : ZONE_BLANK
  )
  const [status, setStatus] = useState(null)

  const toggleName = (name) => {
    setForm((f) => ({
      ...f,
      allowed_names: f.allowed_names.includes(name)
        ? f.allowed_names.filter((n) => n !== name)
        : [...f.allowed_names, name],
    }))
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    setStatus({ loading: true })
    const timeFields = { restricted_start: form.restricted_start, restricted_end: form.restricted_end }
    try {
      if (zoneId) {
        await api.updateZone(zoneId, { name: form.name, allowed_names: form.allowed_names, ...timeFields })
      } else {
        await api.createZone({
          camera_id: cameraId,
          name: form.name,
          polygon: points,
          allowed_names: form.allowed_names,
          ...timeFields,
        })
      }
      onSaved()
    } catch (err) {
      setStatus({ loading: false, error: err.message })
    }
  }

  return (
    <form className="card panel zone-form" onSubmit={handleSubmit}>
      <h3>{zoneId ? 'Edit Zone' : 'New Zone'}</h3>
      <div className="form-row">
        <label>Zone name</label>
        <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required autoFocus />
      </div>
      <div className="form-row">
        <label>Restricted hours (optional)</label>
        <div className="zone-time-row">
          <input
            type="time"
            value={form.restricted_start}
            onChange={(e) => setForm({ ...form, restricted_start: e.target.value })}
          />
          <span>to</span>
          <input
            type="time"
            value={form.restricted_end}
            onChange={(e) => setForm({ ...form, restricted_end: e.target.value })}
          />
        </div>
        <div className="stat-tile-sub">
          Leave both blank to enforce the allow-list at any time. Set a window (e.g. 22:00 to 06:00) to only alert on
          unauthorized people during those hours.
        </div>
      </div>
      <div className="form-row">
        <label>Allowed people — always allowed in this zone, whenever it's enforced</label>
        <div className="zone-people-list">
          {people.length === 0 && <div className="empty-state">No enrolled people yet — add some on the People page.</div>}
          {people.map((p) => (
            <label key={p.name} className="zone-people-row">
              <input type="checkbox" checked={form.allowed_names.includes(p.name)} onChange={() => toggleName(p.name)} />
              {p.name}
            </label>
          ))}
        </div>
      </div>
      {status?.error && <div className="form-message error">{status.error}</div>}
      <div className="modal-actions">
        <button type="button" className="btn btn-outline" onClick={onCancel}>
          Cancel
        </button>
        <button type="submit" className="btn btn-primary" disabled={status?.loading}>
          Save Zone
        </button>
      </div>
    </form>
  )
}

export default function Intrusion() {
  const [cameras, setCameras] = useState([])
  const [people, setPeople] = useState([])
  const [cameraId, setCameraId] = useState(null)
  const [zones, setZones] = useState([])
  const [drawMode, setDrawMode] = useState(false)
  const [draftPoints, setDraftPoints] = useState([])
  const [editingShape, setEditingShape] = useState(false)
  const [editingZone, setEditingZone] = useState(null)
  const [alerts, setAlerts] = useState([])

  const loadAlerts = () => {
    api.listAlerts({ resolved: false })
      .then((all) => setAlerts(all.filter((a) => INTRUSION_ALERT_TYPES.has(a.type))))
      .catch(() => {})
  }

  const handleResolveAlert = async (id) => {
    await api.resolveAlert(id)
    loadAlerts()
  }

  useEffect(() => {
    api
      .listCameras()
      .then((cams) => {
        setCameras(cams)
        if (cams.length > 0) setCameraId(cams[0].id)
      })
      .catch(() => {})
    api.listFaces().then(setPeople).catch(() => {})
    loadAlerts()
    const interval = setInterval(loadAlerts, 15000)
    return () => clearInterval(interval)
  }, [])

  const loadZones = (camId) => {
    api.listZones(camId).then(setZones).catch(() => {})
  }

  useEffect(() => {
    if (cameraId != null) loadZones(cameraId)
  }, [cameraId])

  const camera = cameras.find((c) => c.id === cameraId)

  const cancelDraw = () => {
    setDrawMode(false)
    setDraftPoints([])
    setEditingShape(false)
  }

  const startDraw = () => {
    setEditingZone(null)
    setDrawMode(true)
    setDraftPoints([])
    setEditingShape(false)
  }

  const handleAddPoint = (x, y) => {
    setDraftPoints((pts) => [...pts, [Math.round(x), Math.round(y)]])
  }

  const handleZoneSaved = () => {
    cancelDraw()
    setEditingZone(null)
    loadZones(cameraId)
  }

  const handleDelete = async (zoneId) => {
    await api.deleteZone(zoneId)
    loadZones(cameraId)
  }

  const handleToggleEnabled = async (zone) => {
    await api.updateZone(zone.id, { enabled: !zone.enabled })
    loadZones(cameraId)
  }

  return (
    <div>
      <AlertBanner />
      <div className="page-toolbar">
        <div>
          <h2>Intrusion</h2>
          <div className="page-toolbar-sub">
            Mark an area on a camera and choose who's always allowed there — anyone else detected inside raises an alert.
          </div>
        </div>
        <select
          value={cameraId ?? ''}
          onChange={(e) => {
            setCameraId(Number(e.target.value))
            setEditingZone(null)
            cancelDraw()
          }}
        >
          {cameras.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
      </div>

      <div className="dashboard-main-grid">
        <div className="card panel">
          <div className="panel-header">
            <h3>{camera ? camera.name : 'Select a camera'}</h3>
            {!drawMode ? (
              <button className="btn btn-primary" onClick={startDraw} disabled={!camera}>
                + Draw New Zone
              </button>
            ) : (
              <div className="zone-draw-controls">
                <span className="stat-tile-sub">{draftPoints.length} point(s) placed</span>
                <button className="btn btn-outline" onClick={cancelDraw}>
                  Cancel
                </button>
                <button
                  className="btn btn-primary"
                  onClick={() => setEditingShape(true)}
                  disabled={draftPoints.length < 3}
                >
                  Finish Shape
                </button>
              </div>
            )}
          </div>
          {camera && (
            <CameraTile
              camera={camera}
              large
              showOverlay
              zones={zones}
              drawMode={drawMode && !editingShape}
              draftPoints={draftPoints}
              onAddPoint={handleAddPoint}
            />
          )}
          {drawMode && !editingShape && (
            <div className="stat-tile-sub" style={{ marginTop: '0.5rem' }}>
              Click at least 3 points on the video above to trace the restricted area, then click "Finish Shape".
            </div>
          )}
          {editingShape && (
            <ZoneForm points={draftPoints} people={people} cameraId={cameraId} onCancel={cancelDraw} onSaved={handleZoneSaved} />
          )}
          {editingZone && (
            <ZoneForm
              points={editingZone.polygon}
              people={people}
              initial={editingZone}
              zoneId={editingZone.id}
              onCancel={() => setEditingZone(null)}
              onSaved={handleZoneSaved}
            />
          )}
        </div>

        <div className="card panel">
          <div className="panel-header">
            <h3>Zones on this camera</h3>
          </div>
          {zones.length === 0 ? (
            <div className="empty-state">No zones yet — draw one on the left.</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Allowed</th>
                  <th>Active Window</th>
                  <th>Enabled</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {zones.map((z) => (
                  <tr key={z.id}>
                    <td>{z.name}</td>
                    <td>{z.allowed_names.length ? z.allowed_names.join(', ') : '—'}</td>
                    <td>{z.restricted_start && z.restricted_end ? `${z.restricted_start}–${z.restricted_end}` : 'Always'}</td>
                    <td>
                      <input type="checkbox" checked={z.enabled} onChange={() => handleToggleEnabled(z)} />
                    </td>
                    <td>
                      <button className="btn btn-outline" style={{ marginRight: '0.4rem' }} onClick={() => setEditingZone(z)}>
                        Edit
                      </button>
                      <button className="btn btn-outline" onClick={() => handleDelete(z.id)}>
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="card panel">
          <div className="panel-header">
            <h3>Intrusion Alerts</h3>
          </div>
          {alerts.length === 0 ? (
            <div className="empty-state">No active intrusion alerts.</div>
          ) : (
            <div className="alerts-list">
              {alerts.map((alert) => (
                <div key={alert.id} className="alerts-list-row">
                  <div className="alerts-list-top">
                    <span className="pill pill-danger">{INTRUSION_TYPE_LABELS[alert.type] || alert.type.toUpperCase()}</span>
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
                  <button className="btn btn-outline" onClick={() => handleResolveAlert(alert.id)}>
                    Resolve
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        <IntrusionWindowCard />
      </div>
    </div>
  )
}
