import { useState } from 'react'
import { AdminLoginForm, ActivateForm } from './AuthForms'

// Shown to anyone who clicks "License & Access" with no licenseAuth
// session at all — i.e. someone who just happened onto the nav item, not
// necessarily someone with legitimate business here. "Admin access
// required" is the dominant message; the public code-activation flow
// (still fully functional and unauthenticated server-side — see
// activate_license in main.py, which must keep working for a company
// unlocking access for the first time) is demoted to a small, collapsed
// disclosure rather than a co-equal tab, per the redesign's admin-gating
// resolution.
export default function AdminGate({ onLoggedIn }) {
  const [showActivate, setShowActivate] = useState(false)

  return (
    <div className="license-login-gate">
      <div className="card license-login-card license-admin-gate">
        <div className="license-admin-gate-icon">🔒</div>
        <h2>Admin access required</h2>
        <p className="page-toolbar-sub license-admin-gate-message">
          License &amp; Camera Access is managed by your organization's admins. Sign in with an admin account to
          continue.
        </p>
        <AdminLoginForm onLoggedIn={onLoggedIn} />

        <button
          type="button"
          className="license-activate-disclosure-toggle"
          onClick={() => setShowActivate((v) => !v)}
        >
          {showActivate ? '−' : '+'} Have a license code? Activate it here
        </button>
        {showActivate && (
          <div className="license-activate-disclosure">
            <ActivateForm onLoggedIn={onLoggedIn} />
          </div>
        )}
      </div>
    </div>
  )
}
