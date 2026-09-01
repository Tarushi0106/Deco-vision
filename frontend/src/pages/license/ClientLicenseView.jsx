import { useEffect, useState } from 'react'
import { licenseApi } from '../../api'
import CameraTile from '../../components/CameraTile'
import { StatTile, StatusPill } from './shared'
import { FEATURE_LABELS } from './featureLabels'

// Read-only self-service view for operator/viewer (including the
// auto-provisioned per-company viewer minted by a fresh code activation)
// — tier 3 of the admin-gating design, deliberately NOT blocked by
// AdminGate: this is the account's own scoped data, gated server-side the
// same way /api/my-license always has been. Extended from the pre-
// redesign version with actual live camera feeds (CameraTile, the same
// component Live Cameras/Dashboard use) instead of just a status pill —
// "now they get tha access" to a camera means seeing it, not just a dot.
export default function ClientLicenseView() {
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
        <StatTile label="Camera Usage" value={`${data.assigned} / ${data.total_allowed}`} sub={`${data.remaining} remaining`} />
        <StatTile label="Active Cameras" value={data.active} sub="live right now" />
        <StatTile label="AI Features" value={`${license.enabled_features.length} / ${license.feature_count}`} sub="enabled" />
      </div>

      <div className="card" style={{ marginTop: '1rem' }}>
        <div className="panel-header"><h3>Your Cameras</h3></div>
        {cameras.length === 0 ? (
          <div className="empty-state">No cameras assigned to your license yet.</div>
        ) : (
          <div className="camera-grid">
            {cameras.map((cam) => (
              <div key={cam.id} className="license-client-camera">
                <CameraTile camera={cam} showOverlay={false} />
                <div className="license-client-camera-meta">
                  {cam.enabled_features.length > 0 && (
                    <span className="stat-tile-sub">
                      {cam.enabled_features.map((k) => FEATURE_LABELS[k] || k).join(', ')}
                    </span>
                  )}
                  {!cam.your_permissions.camera_settings && (
                    <span className="pill pill-neutral">View only</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
