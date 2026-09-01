import { useEffect, useState } from 'react'
import { licenseApi } from '../../api'
import { StatTile, StatusPill } from './shared'
import CameraAccessTable from './CameraAccessTable'
import ActivateLicenseModal from './modals/ActivateLicenseModal'

function LicenseUsersCard({ companyId }) {
  const [users, setUsers] = useState(null)
  const [error, setError] = useState(null)
  const [showCreate, setShowCreate] = useState(false)
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [role, setRole] = useState('operator')
  const [createdInfo, setCreatedInfo] = useState(null)
  const [saving, setSaving] = useState(false)

  const load = () => licenseApi.listUsers().then(setUsers).catch((err) => setError(err.message))
  useEffect(load, [])

  const handleCreate = async (e) => {
    e.preventDefault()
    setError(null)
    setSaving(true)
    try {
      const created = await licenseApi.createUser({ email: email.trim(), name: name.trim(), role, company_id: companyId })
      setCreatedInfo(created)
      setEmail('')
      setName('')
      load()
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="card" style={{ marginTop: '1rem' }}>
      <div className="panel-header">
        <h3>License Users</h3>
        <button type="button" className="btn btn-outline" onClick={() => setShowCreate((v) => !v)}>
          {showCreate ? 'Cancel' : 'Add user'}
        </button>
      </div>
      {error && <div className="form-message error">{error}</div>}
      {showCreate && (
        <form className="license-companies-form" style={{ marginBottom: '0.75rem', flexWrap: 'wrap' }} onSubmit={handleCreate}>
          <input placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} required />
          <input type="email" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} required />
          <select value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="operator">Operator</option>
            <option value="viewer">Viewer</option>
          </select>
          <button type="submit" className="btn btn-primary" disabled={saving}>{saving ? 'Creating…' : 'Create'}</button>
        </form>
      )}
      {createdInfo?.temp_password && (
        <div className="form-message success">
          Created {createdInfo.email} — temporary password: <strong>{createdInfo.temp_password}</strong> (shown once)
        </div>
      )}
      <table>
        <thead><tr><th>Name</th><th>Email</th><th>Role</th></tr></thead>
        <tbody>
          {!users || users.length === 0 ? (
            <tr><td colSpan={3} className="empty-state">No users yet.</td></tr>
          ) : (
            users.map((u) => (
              <tr key={u.uuid}>
                <td>{u.name}</td>
                <td>{u.email}</td>
                <td>{u.role.replace('_', ' ')}</td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  )
}

// company_admin's full management view — closes the pre-redesign gap
// where company_admin saw the same bare ClientLicenseView as a viewer,
// even though the backend already supported it managing its own
// license-users (POST/GET /api/license-users). Everything here is scoped
// to the admin's own company: their license (via the same /api/my-license
// every role already uses), license-users, and — per the confirmed
// per-user permission model — the Camera Access table with feature/
// permission management actions.
export default function CompanyAdminPanel({ user }) {
  const [data, setData] = useState(null)
  const [detailedCameras, setDetailedCameras] = useState(null)
  const [error, setError] = useState(null)
  const [showActivate, setShowActivate] = useState(false)

  const load = () => {
    licenseApi.myLicense()
      .then((d) => {
        setData(d)
        return licenseApi.listLicenseCamerasDetailed(d.license.id)
      })
      .then(setDetailedCameras)
      .catch((err) => setError(err.message))
  }

  useEffect(load, [])

  if (error) return <div className="form-message error">{error}</div>
  if (!data) return <div className="empty-state">Loading your license…</div>

  const { license } = data

  return (
    <div>
      <div className="stat-grid">
        <StatTile label="License Status" value={<StatusPill status={license.status} />} />
        <StatTile
          label="License Expiry"
          value={license.non_expiring ? 'No expiry' : new Date(license.expires_at * 1000).toLocaleDateString()}
          sub={license.non_expiring ? 'owned outright' : undefined}
        />
        <StatTile
          label="Camera Usage"
          value={`${data.assigned} / ${data.total_allowed}`}
          sub={`${data.remaining} remaining · ${data.active} active now`}
        />
        <StatTile label="AI Features" value={`${license.enabled_features.length} / ${license.feature_count}`} sub="enabled" />
      </div>

      <div className="card panel" style={{ marginTop: '1rem' }}>
        <div className="panel-header">
          <h3>License Management</h3>
          <button type="button" className="btn btn-primary" onClick={() => setShowActivate(true)}>
            Activate / Update License
          </button>
        </div>
        <table>
          <tbody>
            <tr><td>License Type</td><td>{license.label || '—'}</td></tr>
            <tr><td>License ID</td><td className="license-key-display" style={{ margin: 0 }}>{license.license_key}</td></tr>
            <tr><td>Activated</td><td>{new Date(license.created_at * 1000).toLocaleDateString()}</td></tr>
            <tr><td>Max Cameras</td><td>{license.max_cameras}</td></tr>
            <tr><td>Currently Used</td><td>{data.assigned}</td></tr>
            <tr>
              <td>Enabled Features</td>
              <td>{license.enabled_features.length === 0 ? '—' : license.enabled_features.join(', ')}</td>
            </tr>
          </tbody>
        </table>
      </div>

      {detailedCameras && (
        <div style={{ marginTop: '1rem' }}>
          <CameraAccessTable cameras={detailedCameras} licenseFeatureKeys={license.enabled_features} onChanged={load} />
        </div>
      )}

      <LicenseUsersCard companyId={user.company_id} />

      {showActivate && (
        <ActivateLicenseModal
          myCompanyId={user.company_id}
          onClose={() => setShowActivate(false)}
          onActivated={load}
        />
      )}
    </div>
  )
}
