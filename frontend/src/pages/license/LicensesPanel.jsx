import { useEffect, useMemo, useState } from 'react'
import { api, licenseApi } from '../../api'
import { StatusPill } from './shared'
import LicenseFeaturesModal from './modals/LicenseFeaturesModal'

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

// super_admin's full cross-company license CRUD — companies list, license
// table, and every per-license action. Unchanged in substance from the
// pre-redesign version; only new addition is the "Features" action +
// LicenseFeaturesModal (license-level AI feature checklist, super_admin-
// only per PUT /api/licenses/{id}/features).
export default function LicensesPanel() {
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
  const [featuresLicense, setFeaturesLicense] = useState(null)
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
                    <button type="button" className="btn btn-outline" onClick={() => setFeaturesLicense(lic)}>Features</button>
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
      {featuresLicense && (
        <LicenseFeaturesModal license={featuresLicense} onClose={() => setFeaturesLicense(null)} onSaved={load} />
      )}
    </div>
  )
}
