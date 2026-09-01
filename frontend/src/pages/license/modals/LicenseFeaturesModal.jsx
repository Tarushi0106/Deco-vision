import { useEffect, useState } from 'react'
import { licenseApi } from '../../../api'

// License-level feature checklist — super_admin only (PUT /api/licenses/
// {id}/features enforces this server-side too). A camera's own feature
// set (CameraFeaturesModal) must be a subset of whatever's enabled here.
export default function LicenseFeaturesModal({ license, onClose, onSaved }) {
  const [catalog, setCatalog] = useState([])
  const [selected, setSelected] = useState(null) // Set, once loaded
  const [error, setError] = useState(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    Promise.all([licenseApi.featureCatalog(), licenseApi.getLicenseFeatures(license.id)])
      .then(([features, current]) => {
        setCatalog(features)
        setSelected(new Set(current.feature_keys))
      })
      .catch((err) => setError(err.message))
  }, [license.id])

  const toggle = (key) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const handleSave = async () => {
    setError(null)
    setSaving(true)
    try {
      await licenseApi.setLicenseFeatures(license.id, [...selected])
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
      <div className="card modal" onClick={(e) => e.stopPropagation()}>
        <h3>AI features — {license.label || license.license_key}</h3>
        <p className="page-toolbar-sub" style={{ marginBottom: '0.75rem' }}>
          Cameras on this license can only enable features selected here.
        </p>
        {error && <div className="form-message error">{error}</div>}
        {selected === null ? (
          <div className="empty-state">Loading…</div>
        ) : (
          <div className="license-feature-checklist">
            {catalog.map((f) => (
              <label key={f.key} className="license-feature-row">
                <input type="checkbox" checked={selected.has(f.key)} onChange={() => toggle(f.key)} />
                <span>{f.label}</span>
              </label>
            ))}
          </div>
        )}
        <div className="modal-actions">
          <button type="button" className="btn btn-outline" onClick={onClose}>Cancel</button>
          <button type="button" className="btn btn-primary" onClick={handleSave} disabled={saving || selected === null}>
            {saving ? 'Saving…' : 'Save features'}
          </button>
        </div>
      </div>
    </div>
  )
}
