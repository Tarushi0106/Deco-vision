import { useEffect, useState } from 'react'
import { licenseApi } from '../../../api'

// Per-camera feature override — must be a subset of the license's own
// enabled features (enforced both here, greying out anything not
// licensed, and server-side in set_camera_features). super_admin and
// company_admin only.
export default function CameraFeaturesModal({ camera, licenseFeatureKeys, onClose, onSaved }) {
  const [catalog, setCatalog] = useState([])
  const [selected, setSelected] = useState(null)
  const [error, setError] = useState(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    Promise.all([licenseApi.featureCatalog(), licenseApi.getCameraFeatures(camera.id)])
      .then(([features, current]) => {
        setCatalog(features)
        setSelected(new Set(current.feature_keys))
      })
      .catch((err) => setError(err.message))
  }, [camera.id])

  const licensedSet = new Set(licenseFeatureKeys)

  const toggle = (key) => {
    if (!licensedSet.has(key)) return
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
      await licenseApi.setCameraFeatures(camera.id, [...selected])
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
        <h3>Features — {camera.name}</h3>
        {licensedSet.size === 0 && (
          <div className="form-message error">This license has no features enabled yet — nothing to turn on here.</div>
        )}
        {error && <div className="form-message error">{error}</div>}
        {selected === null ? (
          <div className="empty-state">Loading…</div>
        ) : (
          <div className="license-feature-checklist">
            {catalog.map((f) => {
              const licensed = licensedSet.has(f.key)
              return (
                <label key={f.key} className={`license-feature-row${licensed ? '' : ' license-feature-row-locked'}`}>
                  <input
                    type="checkbox"
                    checked={selected.has(f.key)}
                    disabled={!licensed}
                    onChange={() => toggle(f.key)}
                  />
                  <span>{f.label}</span>
                  {!licensed && <span className="stat-tile-sub">not licensed</span>}
                </label>
              )
            })}
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
