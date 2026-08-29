import { useEffect, useMemo, useState } from 'react'
import { api, licenseApi } from '../api'
import { clearLicenseSession, getDeviceFingerprint, getLicenseUser, setLicenseSession } from '../licenseAuth'
import './pages.css'
import './licenseManagement.css'

// Cameras are sold outright, not leased — licenses never expire and are
// never "renewed"; the only way off "active" is an explicit admin action
// (disable or revoke), so there's no "expired" status to show here.
const STATUS_LABELS = { active: 'Active', inactive: 'Inactive', suspended: 'Suspended' }
const STATUS_PILL_CLASS = {
  active: 'pill-success', inactive: 'pill-neutral', suspended: 'pill-danger',
}

function StatusPill({ status }) {
  return <span className={`pill ${STATUS_PILL_CLASS[status] || 'pill-neutral'}`}>{STATUS_LABELS[status] || status}</span>
}

function StatTile({ label, value, sub }) {
  return (
    <div className="stat-tile card">
      <div className="stat-tile-label">{label}</div>
      <div className="stat-tile-value">{value}</div>
      {sub && <div className="stat-tile-sub">{sub}</div>}
    </div>
  )
}

function UsageBar({ label, percent }) {
  return (
    <div className="usage-bar">
      <div className="usage-bar-header">
        <span>{label}</span>
        <span>{percent}%</span>
      </div>
      <div className="usage-bar-track">
        <div className="usage-bar-fill" style={{ width: `${Math.min(100, percent)}%` }} />
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------

function AdminLoginForm({ onLoggedIn }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      // Backend now normalizes email case/whitespace too, but trimming
      // here as well avoids a confusing round-trip for the common case
      // (leading/trailing space from a copy-paste).
      const result = await licenseApi.login(email.trim(), password)
      setLicenseSession(result.access_token, result.user)
      onLoggedIn(result.user)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <form onSubmit={handleSubmit}>
      <label>
        Email
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          autoComplete="username"
          required
          autoFocus
        />
      </label>
      <label>
        Password
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          autoComplete="current-password"
          required
        />
      </label>
      {error && <div className="form-message error">{error}</div>}
      <button type="submit" className="btn btn-primary" disabled={loading}>
        {loading ? 'Signing in…' : 'Sign in'}
      </button>
    </form>
  )
}

// For an end user handed a license code or QR — the code IS their
// credential (see main.py's activate_license, which now provisions/reuses
// a viewer account and returns a real session), no separately admin-
// created email/password needed.
function ActivateForm({ onLoggedIn }) {
  const [code, setCode] = useState('')
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      const result = await licenseApi.activate({
        code: code.trim().toUpperCase(),
        device_fingerprint: getDeviceFingerprint(),
      })
      setLicenseSession(result.access_token, result.user)
      onLoggedIn(result.user)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <form onSubmit={handleSubmit}>
      <label>
        License code
        <input
          value={code}
          onChange={(e) => setCode(e.target.value)}
          placeholder="XXXX-XXXX-XXXX-XXXX"
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          required
          autoFocus
        />
      </label>
      {error && <div className="form-message error">{error}</div>}
      <button type="submit" className="btn btn-primary" disabled={loading}>
        {loading ? 'Activating…' : 'Activate'}
      </button>
    </form>
  )
}

function LoginGate({ onLoggedIn }) {
  const [tab, setTab] = useState('activate')

  return (
    <div className="license-login-gate">
      <div className="card license-login-card">
        <h2>License &amp; Camera Access</h2>
        <div className="tab-switcher" style={{ marginBottom: '1rem' }}>
          <button
            type="button"
            className={`btn ${tab === 'activate' ? 'btn-primary' : 'btn-outline'}`}
            onClick={() => setTab('activate')}
          >
            Activate License
          </button>
          <button
            type="button"
            className={`btn ${tab === 'admin' ? 'btn-primary' : 'btn-outline'}`}
            onClick={() => setTab('admin')}
          >
            Admin Sign In
          </button>
        </div>
        {tab === 'activate' ? (
          <>
            <p className="page-toolbar-sub">Enter the license code you were given, or scanned from its QR code.</p>
            <ActivateForm onLoggedIn={onLoggedIn} />
          </>
        ) : (
          <>
            <p className="page-toolbar-sub">For Super Admins and Company Admins managing licenses.</p>
            <AdminLoginForm onLoggedIn={onLoggedIn} />
          </>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------

function AnalyticsPanel() {
  const [stats, setStats] = useState(null)

  useEffect(() => {
    licenseApi.analytics().then(setStats).catch(() => {})
  }, [])

  if (!stats) return <div className="empty-state">Loading analytics…</div>

  return (
    <div className="license-analytics">
      <div className="stat-grid">
        <StatTile label="Total Licenses" value={stats.total_licenses} />
        <StatTile label="Active" value={stats.active_licenses} />
        <StatTile label="Total Cameras" value={stats.total_cameras} />
        <StatTile label="Cameras Assigned" value={stats.cameras_assigned} />
        <StatTile label="Cameras Online" value={stats.cameras_online} />
        <StatTile label="Cameras Offline" value={stats.cameras_offline} />
      </div>
      <div className="card panel license-usage-panel">
        <UsageBar label="License capacity in use" percent={stats.license_usage_percent} />
        <UsageBar label="Cameras assigned vs. total" percent={stats.camera_usage_percent} />
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------

function QrModal({ license, onClose }) {
  const [imgUrl, setImgUrl] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let objectUrl = null
    licenseApi.getLicenseQrBlob(license.id)
      .then((blob) => {
        objectUrl = URL.createObjectURL(blob)
        setImgUrl(objectUrl)
      })
      .catch((err) => setError(err.message))
    return () => { if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [license.id])

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="card modal license-qr-modal" onClick={(e) => e.stopPropagation()}>
        <h3>{license.label || license.license_key}</h3>
        <div className="license-key-display">{license.license_key}</div>
        {error && <div className="form-message error">{error}</div>}
        {imgUrl ? (
          <>
            <img src={imgUrl} alt="License QR code" className="license-qr-image" />
            <a className="btn btn-outline" href={imgUrl} download={`license-${license.license_key}.png`}>
              Download QR
            </a>
          </>
        ) : (
          <div className="empty-state">Generating QR…</div>
        )}
        <div className="modal-actions">
          <button type="button" className="btn btn-outline" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------

function CameraAssignModal({ license, allCameras, onClose, onChanged }) {
  const [assigned, setAssigned] = useState(null) // Set of camera ids currently assigned (server truth)
  const [selected, setSelected] = useState(new Set())
  const [error, setError] = useState(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    licenseApi.listLicenseCameras(license.id).then((cams) => {
      const ids = new Set(cams.map((c) => c.id))
      setAssigned(ids)
      setSelected(new Set(ids))
    }).catch((err) => setError(err.message))
  }, [license.id])

  const toggle = (id) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const handleSave = async () => {
    setError(null)
    setSaving(true)
    try {
      const toAdd = [...selected].filter((id) => !assigned.has(id))
      const toRemove = [...assigned].filter((id) => !selected.has(id))
      if (toAdd.length > 0) await licenseApi.assignLicenseCameras(license.id, toAdd)
      if (toRemove.length > 0) await licenseApi.removeLicenseCameras(license.id, toRemove)
      onChanged()
      onClose()
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="card modal license-camera-modal" onClick={(e) => e.stopPropagation()}>
        <h3>Assign cameras — {license.label || license.license_key}</h3>
        <div className="stat-tile-sub" style={{ marginBottom: '0.5rem' }}>
          {selected.size} / {license.max_cameras} selected
        </div>
        {error && <div className="form-message error">{error}</div>}
        {assigned === null ? (
          <div className="empty-state">Loading…</div>
        ) : (
          <div className="license-camera-list">
            {allCameras.map((cam) => (
              <label key={cam.id} className="license-camera-row">
                <input
                  type="checkbox"
                  checked={selected.has(cam.id)}
                  disabled={!selected.has(cam.id) && selected.size >= license.max_cameras}
                  onChange={() => toggle(cam.id)}
                />
                <span>{cam.name}</span>
                <span className="camera-tile-site">{cam.site}</span>
              </label>
            ))}
          </div>
        )}
        <div className="modal-actions">
          <button type="button" className="btn btn-outline" onClick={onClose}>Cancel</button>
          <button type="button" className="btn btn-primary" onClick={handleSave} disabled={saving || assigned === null}>
            {saving ? 'Saving…' : 'Save assignment'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------

function LicenseFormModal({ companies, license, onClose, onSaved }) {
  const isEdit = Boolean(license)
  const [companyId, setCompanyId] = useState(license?.company_id || companies[0]?.id || '')
  const [label, setLabel] = useState(license?.label || '')
  const [maxCameras, setMaxCameras] = useState(license?.max_cameras ?? 1)
  const [deviceBind, setDeviceBind] = useState(license?.device_bind_enabled ? true : false)
  const [error, setError] = useState(null)
  const [saving, setSaving] = useState(false)

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError(null)
    setSaving(true)
    try {
      if (isEdit) {
        await licenseApi.updateLicense(license.id, {
          label, max_cameras: Number(maxCameras), device_bind_enabled: deviceBind,
        })
      } else {
        await licenseApi.createLicense({
          company_id: companyId, max_cameras: Number(maxCameras),
          device_bind_enabled: deviceBind, label,
        })
      }
      onSaved()
      onClose()
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <form className="card modal" onClick={(e) => e.stopPropagation()} onSubmit={handleSubmit}>
        <h3>{isEdit ? 'Edit license' : 'New license'}</h3>
        {!isEdit && (
          <label className="license-form-field">
            Company
            <select value={companyId} onChange={(e) => setCompanyId(e.target.value)} required>
              {companies.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </label>
        )}
        <label className="license-form-field">
          Label (optional)
          <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="e.g. Noida Site A" />
        </label>
        <label className="license-form-field">
          Max cameras
          <input type="number" min={0} value={maxCameras} onChange={(e) => setMaxCameras(e.target.value)} required />
        </label>
        <label className="license-checkbox-field">
          <input type="checkbox" checked={deviceBind} onChange={(e) => setDeviceBind(e.target.checked)} />
          Bind to first activating device
        </label>
        {error && <div className="form-message error">{error}</div>}
        <div className="modal-actions">
          <button type="button" className="btn btn-outline" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={saving}>
            {saving ? 'Saving…' : isEdit ? 'Save changes' : 'Create license'}
          </button>
        </div>
      </form>
    </div>
  )
}

// ---------------------------------------------------------------------

function LicensesPanel() {
  const [licenses, setLicenses] = useState([])
  const [total, setTotal] = useState(0)
  const [companies, setCompanies] = useState([])
  const [allCameras, setAllCameras] = useState([])
  const [status, setStatus] = useState('')
  const [search, setSearch] = useState('')
  const [offset, setOffset] = useState(0)
  const [error, setError] = useState(null)
  const limit = 20

  const [showCreate, setShowCreate] = useState(false)
  const [editLicense, setEditLicense] = useState(null)
  const [qrLicense, setQrLicense] = useState(null)
  const [assignLicense, setAssignLicense] = useState(null)
  const [newCompanyName, setNewCompanyName] = useState('')

  const companiesById = useMemo(() => Object.fromEntries(companies.map((c) => [c.id, c.name])), [companies])

  const load = () => {
    licenseApi.listLicenses({ status, search, limit, offset })
      .then((res) => { setLicenses(res.items); setTotal(res.total) })
      .catch((err) => setError(err.message))
  }

  useEffect(load, [status, search, offset])
  useEffect(() => {
    licenseApi.listCompanies().then(setCompanies).catch(() => {})
    api.listCameras().then(setAllCameras).catch(() => {})
  }, [])

  const handleCreateCompany = async (e) => {
    e.preventDefault()
    if (!newCompanyName.trim()) return
    const company = await licenseApi.createCompany(newCompanyName.trim())
    setCompanies((prev) => [...prev, company])
    setNewCompanyName('')
  }

  const handleAction = async (action, license) => {
    try {
      if (action === 'revoke') await licenseApi.revokeLicense(license.id)
      else if (action === 'enable') await licenseApi.enableLicense(license.id)
      else if (action === 'disable') await licenseApi.disableLicense(license.id)
      load()
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <div>
      <div className="card license-companies-card">
        <div className="panel-header">
          <h3>Companies</h3>
        </div>
        <div className="license-companies-list">
          {companies.length === 0 && <span className="stat-tile-sub">No companies yet.</span>}
          {companies.map((c) => <span key={c.id} className="pill pill-neutral">{c.name}</span>)}
        </div>
        <form className="license-companies-form" onSubmit={handleCreateCompany}>
          <input
            placeholder="New company name"
            value={newCompanyName}
            onChange={(e) => setNewCompanyName(e.target.value)}
          />
          <button type="submit" className="btn btn-outline">Add company</button>
        </form>
      </div>

      <div className="page-toolbar" style={{ marginTop: '1rem' }}>
        <div className="page-toolbar-sub">Licenses</div>
        <div className="footfall-toolbar">
          <input placeholder="Search key or label…" value={search} onChange={(e) => { setOffset(0); setSearch(e.target.value) }} />
          <select value={status} onChange={(e) => { setOffset(0); setStatus(e.target.value) }}>
            <option value="">All statuses</option>
            <option value="active">Active</option>
            <option value="inactive">Inactive</option>
          </select>
          <button type="button" className="btn btn-primary" disabled={companies.length === 0} onClick={() => setShowCreate(true)}>
            New License
          </button>
        </div>
      </div>

      {error && <div className="form-message error">{error}</div>}

      <div className="card">
        <table>
          <thead>
            <tr>
              <th>License Key</th>
              <th>Company</th>
              <th>Status</th>
              <th>Cameras</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {licenses.length === 0 ? (
              <tr><td colSpan={5} className="empty-state">No licenses found.</td></tr>
            ) : (
              licenses.map((lic) => (
                <tr key={lic.id}>
                  <td>
                    <div>{lic.license_key}</div>
                    {lic.label && <div className="stat-tile-sub">{lic.label}</div>}
                  </td>
                  <td>{companiesById[lic.company_id] || '—'}</td>
                  <td><StatusPill status={lic.status} /></td>
                  <td>{lic.cameras_assigned} / {lic.max_cameras}</td>
                  <td className="license-actions-cell">
                    <button type="button" className="btn btn-outline" onClick={() => setEditLicense(lic)}>Edit</button>
                    <button type="button" className="btn btn-outline" onClick={() => setAssignLicense(lic)}>Cameras</button>
                    <button type="button" className="btn btn-outline" onClick={() => setQrLicense(lic)}>QR</button>
                    {lic.status === 'inactive' ? (
                      <button type="button" className="btn btn-outline" onClick={() => handleAction('enable', lic)}>Enable</button>
                    ) : (
                      <button type="button" className="btn btn-outline" onClick={() => handleAction('disable', lic)}>Disable</button>
                    )}
                    {lic.status !== 'suspended' && (
                      <button type="button" className="btn btn-outline" onClick={() => handleAction('revoke', lic)}>Revoke</button>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
        <div className="license-pagination">
          <button type="button" className="btn btn-outline" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>
            ← Previous
          </button>
          <span className="stat-tile-sub">{total === 0 ? '0' : `${offset + 1}–${Math.min(offset + limit, total)}`} of {total}</span>
          <button type="button" className="btn btn-outline" disabled={offset + limit >= total} onClick={() => setOffset(offset + limit)}>
            Next →
          </button>
        </div>
      </div>

      {showCreate && (
        <LicenseFormModal companies={companies} license={null} onClose={() => setShowCreate(false)} onSaved={load} />
      )}
      {editLicense && (
        <LicenseFormModal companies={companies} license={editLicense} onClose={() => setEditLicense(null)} onSaved={load} />
      )}
      {qrLicense && <QrModal license={qrLicense} onClose={() => setQrLicense(null)} />}
      {assignLicense && (
        <CameraAssignModal license={assignLicense} allCameras={allCameras} onClose={() => setAssignLicense(null)} onChanged={load} />
      )}
    </div>
  )
}

// ---------------------------------------------------------------------

function ClientLicenseView() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    licenseApi.myLicense().then(setData).catch((err) => setError(err.message))
  }, [])

  if (error) return <div className="form-message error">{error}</div>
  if (!data) return <div className="empty-state">Loading your license…</div>

  const { license, cameras } = data

  return (
    <div>
      <div className="stat-grid">
        <StatTile label="License Status" value={<StatusPill status={license.status} />} />
        <StatTile label="Total Allowed" value={data.total_allowed} />
        <StatTile label="Assigned" value={data.assigned} />
        <StatTile label="Currently In Use" value={data.in_use} sub="cameras live right now" />
        <StatTile label="Remaining Slots" value={data.remaining} />
      </div>

      <div className="card" style={{ marginTop: '1rem' }}>
        <div className="panel-header"><h3>Assigned Cameras</h3></div>
        <table>
          <thead>
            <tr><th>Camera</th><th>Site</th><th>Live Status</th></tr>
          </thead>
          <tbody>
            {cameras.length === 0 ? (
              <tr><td colSpan={3} className="empty-state">No cameras assigned to your license yet.</td></tr>
            ) : (
              cameras.map((cam) => (
                <tr key={cam.id}>
                  <td>{cam.name}</td>
                  <td>{cam.site}</td>
                  <td>
                    <span className={`pill ${cam.live ? 'pill-success' : 'pill-neutral'}`}>
                      <span className={`dot ${cam.live ? 'dot-success' : 'dot-neutral'}`} /> {cam.live ? 'Live' : 'Offline'}
                    </span>
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

// ---------------------------------------------------------------------

export default function LicenseManagement() {
  const [user, setUser] = useState(getLicenseUser())

  if (!user) return <LoginGate onLoggedIn={setUser} />

  const handleLogout = async () => {
    try { await licenseApi.logout() } catch { /* sign out locally regardless */ }
    clearLicenseSession()
    setUser(null)
  }

  return (
    <div>
      <div className="page-toolbar">
        <div>
          <h2>License &amp; Camera Access</h2>
          <div className="page-toolbar-sub">
            Signed in as {user.name} ({user.role.replace('_', ' ')})
          </div>
        </div>
        <button type="button" className="btn btn-outline" onClick={handleLogout}>Sign out</button>
      </div>

      {user.role === 'super_admin' ? (
        <>
          <AnalyticsPanel />
          <LicensesPanel />
        </>
      ) : (
        <ClientLicenseView />
      )}
    </div>
  )
}
