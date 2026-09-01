import { useState } from 'react'
import { Link } from 'react-router-dom'
import { FEATURE_LABELS } from './featureLabels'
import CameraFeaturesModal from './modals/CameraFeaturesModal'
import CameraPermissionsModal from './modals/CameraPermissionsModal'

// Camera Name / ID / Location / Status / License Status / Assigned Users
// / Enabled AI Features / Actions — backed by GET /api/licenses/{id}/
// cameras/detailed (license_db.list_cameras_for_license_detailed), which
// adds enabled_features + assigned_user_count on top of the base
// list_cameras_for_license shape. "License Status" here is always
// "Licensed" — this table only ever lists cameras already in
// camera_assignments for this license; an unlicensed camera simply isn't
// a row (assigning happens via the existing CameraAssignModal in
// LicensesPanel, super_admin only).
export default function CameraAccessTable({ cameras, licenseFeatureKeys, onChanged }) {
  const [featuresCamera, setFeaturesCamera] = useState(null)
  const [permissionsCamera, setPermissionsCamera] = useState(null)

  return (
    <div className="card">
      <div className="panel-header"><h3>Camera Access</h3></div>
      <table>
        <thead>
          <tr>
            <th>Camera</th>
            <th>Location</th>
            <th>Status</th>
            <th>Assigned Users</th>
            <th>Enabled AI Features</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {cameras.length === 0 ? (
            <tr><td colSpan={6} className="empty-state">No cameras assigned to this license yet.</td></tr>
          ) : (
            cameras.map((cam) => (
              <tr key={cam.id}>
                <td>
                  <div>{cam.name}</div>
                  <div className="stat-tile-sub">#{cam.id}</div>
                </td>
                <td>{cam.site || '—'}</td>
                <td>
                  <span className={`pill ${cam.camera_status === 'active' ? 'pill-success' : 'pill-neutral'}`}>
                    <span className={`dot ${cam.camera_status === 'active' ? 'dot-success' : 'dot-neutral'}`} />
                    {cam.camera_status === 'active' ? 'Online' : 'Offline'}
                  </span>
                </td>
                <td>{cam.assigned_user_count}</td>
                <td>
                  {cam.enabled_features.length === 0 ? (
                    <span className="stat-tile-sub">None</span>
                  ) : (
                    cam.enabled_features.map((k) => FEATURE_LABELS[k] || k).join(', ')
                  )}
                </td>
                <td className="license-actions-cell">
                  <button type="button" className="btn btn-outline" onClick={() => setFeaturesCamera(cam)}>
                    Manage Features
                  </button>
                  <button type="button" className="btn btn-outline" onClick={() => setPermissionsCamera(cam)}>
                    Manage Access
                  </button>
                  <Link className="btn btn-outline" to="/live-cameras">View Camera</Link>
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>

      {featuresCamera && (
        <CameraFeaturesModal
          camera={featuresCamera}
          licenseFeatureKeys={licenseFeatureKeys}
          onClose={() => setFeaturesCamera(null)}
          onSaved={onChanged}
        />
      )}
      {permissionsCamera && (
        <CameraPermissionsModal camera={permissionsCamera} onClose={() => setPermissionsCamera(null)} />
      )}
    </div>
  )
}
