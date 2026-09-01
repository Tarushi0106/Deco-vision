// Small presentational pieces shared across the License & Camera Access
// module's split files (see LicenseManagementPage.jsx for the top-level
// gating switch that ties them together).

// Cameras are sold outright, not leased — licenses never expire and are
// never "renewed"; the only way off "active" is an explicit admin action
// (disable or revoke), so there's no "expired" status to show here.
export const STATUS_LABELS = { active: 'Active', inactive: 'Inactive', suspended: 'Suspended' }
export const STATUS_PILL_CLASS = {
  active: 'pill-success', inactive: 'pill-neutral', suspended: 'pill-danger',
}

export function StatusPill({ status }) {
  return <span className={`pill ${STATUS_PILL_CLASS[status] || 'pill-neutral'}`}>{STATUS_LABELS[status] || status}</span>
}

export function StatTile({ label, value, sub }) {
  return (
    <div className="stat-tile card">
      <div className="stat-tile-label">{label}</div>
      <div className="stat-tile-value">{value}</div>
      {sub && <div className="stat-tile-sub">{sub}</div>}
    </div>
  )
}

export function UsageBar({ label, percent }) {
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

// "Admin" for gating purposes = literally this codebase's own two admin
// roles — nothing invented (see AdminGate.jsx / LicenseManagementPage.jsx).
export function isAdminRole(role) {
  return role === 'super_admin' || role === 'company_admin'
}
