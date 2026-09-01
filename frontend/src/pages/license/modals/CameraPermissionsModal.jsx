import { useEffect, useState } from 'react'
import { licenseApi } from '../../../api'

const PERMISSION_COLUMNS = [
  { key: 'live_view', label: 'Live View' },
  { key: 'playback', label: 'Playback' },
  { key: 'analytics', label: 'Analytics' },
  { key: 'events', label: 'Events' },
  { key: 'camera_settings', label: 'Camera Settings' },
]

// Per-user × per-camera permission grid. Rows with no saved override show
// their role-derived default (is_override: false, from
// license_db.default_permissions_for_role) — editing one just calls PUT
// for that single user/camera pair; there's no bulk "save all" diff like
// CameraAssignModal's toAdd/toRemove, since each row is independently
// upserted the moment it changes (simpler here: no capacity cap to
// reconcile against, unlike camera assignment).
export default function CameraPermissionsModal({ camera, onClose }) {
  const [rows, setRows] = useState(null)
  const [error, setError] = useState(null)
  const [savingUuid, setSavingUuid] = useState(null)

  const load = () => {
    licenseApi.getCameraPermissions(camera.id).then(setRows).catch((err) => setError(err.message))
  }

  useEffect(load, [camera.id])

  const handleToggle = async (row, key) => {
    setError(null)
    setSavingUuid(row.user_uuid)
    const nextPerms = { ...row.permissions, [key]: !row.permissions[key] }
    try {
      await licenseApi.setCameraPermission(camera.id, row.user_uuid, nextPerms)
      setRows((prev) => prev.map((r) => (
        r.user_uuid === row.user_uuid ? { ...r, permissions: nextPerms, is_override: true } : r
      )))
    } catch (err) {
      setError(err.message)
    } finally {
      setSavingUuid(null)
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="card modal license-permissions-modal" onClick={(e) => e.stopPropagation()}>
        <h3>Camera access — {camera.name}</h3>
        <p className="page-toolbar-sub" style={{ marginBottom: '0.75rem' }}>
          Toggling a permission saves it immediately for that person.
        </p>
        {error && <div className="form-message error">{error}</div>}
        {rows === null ? (
          <div className="empty-state">Loading…</div>
        ) : rows.length === 0 ? (
          <div className="empty-state">No users in your company yet — create one under License Users first.</div>
        ) : (
          <div className="license-permissions-grid">
            {rows.map((row) => (
              <div key={row.user_uuid} className="license-permissions-row">
                <div className="license-permissions-user">
                  <div>{row.name}</div>
                  <div className="stat-tile-sub">
                    {row.email} · {row.role.replace('_', ' ')}
                    {!row.is_override && ' · role default'}
                  </div>
                </div>
                <div className="license-permissions-toggles">
                  {PERMISSION_COLUMNS.map((col) => (
                    <label key={col.key} className="license-permissions-toggle">
                      <input
                        type="checkbox"
                        checked={row.permissions[col.key]}
                        disabled={savingUuid === row.user_uuid}
                        onChange={() => handleToggle(row, col.key)}
                      />
                      {col.label}
                    </label>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
        <div className="modal-actions">
          <button type="button" className="btn btn-outline" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  )
}
