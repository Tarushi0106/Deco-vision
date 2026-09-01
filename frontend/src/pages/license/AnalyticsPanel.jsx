import { useEffect, useState } from 'react'
import { licenseApi } from '../../api'
import { StatTile, UsageBar } from './shared'

// super_admin's cross-company overview — aggregate totals across every
// license/company, not any single license's detail (that's
// CompanyAdminPanel's/ClientLicenseView's 4-card layout, scoped to one
// company's one license). Unchanged from the pre-redesign version aside
// from the move into this file.
export default function AnalyticsPanel() {
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
        <StatTile label="Active Cameras" value={stats.cameras_online} sub="online right now" />
        <StatTile label="Cameras Offline" value={stats.cameras_offline} />
      </div>
      <div className="card panel license-usage-panel">
        <UsageBar label="License capacity in use" percent={stats.license_usage_percent} />
        <UsageBar label="Cameras assigned vs. total" percent={stats.camera_usage_percent} />
      </div>
    </div>
  )
}
