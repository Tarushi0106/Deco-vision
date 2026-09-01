import { useState } from 'react'
import { licenseApi } from '../../api'
import { clearLicenseSession, getLicenseUser } from '../../licenseAuth'
import AdminGate from './AdminGate'
import AnalyticsPanel from './AnalyticsPanel'
import LicensesPanel from './LicensesPanel'
import CompanyAdminPanel from './CompanyAdminPanel'
import ClientLicenseView from './ClientLicenseView'
import '../pages.css'
import '../licenseManagement.css'

// Three gating tiers, driven entirely by the existing licenseAuth
// session's role — no new auth system (see AdminGate.jsx for the
// resolution of "admin-only page" vs. "activation must stay public"):
//   1. no session at all          -> AdminGate ("Admin access required")
//   2. super_admin / company_admin -> full module
//   3. operator / viewer           -> read-only self-service (unblocked —
//      this is the account's own scoped data, not an admin control, and
//      includes the auto-provisioned viewer from a fresh code activation)
export default function LicenseManagementPage() {
  const [user, setUser] = useState(getLicenseUser())

  if (!user) return <AdminGate onLoggedIn={setUser} />

  const handleLogout = async () => {
    try { await licenseApi.logout() } catch { /* sign out locally regardless */ }
    clearLicenseSession()
    setUser(null)
  }

  const isSuperAdmin = user.role === 'super_admin'
  const isCompanyAdmin = user.role === 'company_admin'

  return (
    <div>
      <div className="page-toolbar">
        <div>
          <h2>License &amp; Camera Access</h2>
          <div className="page-toolbar-sub">
            Manage your platform license, cameras, users, and feature access.
            {' '}Signed in as {user.name} ({user.role.replace('_', ' ')})
          </div>
        </div>
        <button type="button" className="btn btn-outline" onClick={handleLogout}>Sign out</button>
      </div>

      {isSuperAdmin ? (
        <>
          <AnalyticsPanel />
          <LicensesPanel />
        </>
      ) : isCompanyAdmin ? (
        <CompanyAdminPanel user={user} />
      ) : (
        <ClientLicenseView />
      )}
    </div>
  )
}
