import { useState } from 'react'
import { licenseApi } from '../../api'
import { setLicenseSession } from '../../licenseAuth'
import { useActivateLicense } from './useActivateLicense'

export function AdminLoginForm({ onLoggedIn }) {
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
// created email/password needed. Kept intentionally compact — this is the
// de-emphasized disclosure inside AdminGate, not the richer post-login
// ActivateLicenseModal (see modals/ActivateLicenseModal.jsx for that).
export function ActivateForm({ onLoggedIn }) {
  const [code, setCode] = useState('')
  const { status, errorInfo, activate } = useActivateLicense(onLoggedIn)

  const handleSubmit = async (e) => {
    e.preventDefault()
    await activate(code)
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
      {status === 'error' && <div className="form-message error">{errorInfo.message}</div>}
      <button type="submit" className="btn btn-primary" disabled={status === 'loading'}>
        {status === 'loading' ? 'Activating…' : 'Activate'}
      </button>
    </form>
  )
}
