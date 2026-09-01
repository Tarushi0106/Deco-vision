import { useState } from 'react'
import { licenseApi } from '../../api'
import { getDeviceFingerprint, setLicenseSession } from '../../licenseAuth'

// Maps activate_license's actual HTTP responses (backend/app/main.py) onto
// a small set of named states the UI can branch on, rather than every
// caller re-deriving this from a raw error message. See the plan's
// mapping table for why each of these is what it is — several of the
// spec's original states ("expired", "camera limit exceeded", "feature
// not included") can't occur here at all (expiry is vestigial, and
// activation never touches cameras/features), so they're intentionally
// not represented as activation-time states.
function classifyError(err) {
  const detail = err.message || ''
  if (err.status === 404) return { kind: 'invalid', message: 'This license code doesn’t match any license.' }
  if (err.status === 403 && detail.includes('revoked')) {
    return { kind: 'revoked', message: 'This license has been revoked.' }
  }
  if (err.status === 403 && detail.includes('disabled')) {
    return { kind: 'disabled', message: 'This license is currently disabled.' }
  }
  if (err.status === 403 && detail.includes('different device')) {
    return { kind: 'device_mismatch', message: 'This license is already activated on another device.' }
  }
  if (err.status === 400 && detail.includes('QR code')) {
    return { kind: 'invalid_qr', message: 'This QR code is invalid or has expired.' }
  }
  return { kind: 'generic', message: detail || 'Activation failed — please try again.' }
}

// Shared by both AdminGate's compact activation disclosure and the fuller
// ActivateLicenseModal — both call the same public /api/licenses/activate
// endpoint and need the same state mapping, so it lives once here rather
// than being duplicated between the two surfaces.
//
// setSession matters because activate_license (main.py) always mints a
// session for the license's auto-provisioned per-company VIEWER account,
// regardless of who called it. That's correct for AdminGate (nobody is
// logged in yet — this IS how you sign in). It would be wrong for an
// already-authenticated admin using ActivateLicenseModal to redeem an
// extra code: overwriting their real admin session with a viewer session
// would silently sign them out of their own account. Pass
// setSession:false there — the caller stays whoever they were.
export function useActivateLicense(onLoggedIn, { setSession = true } = {}) {
  const [status, setStatus] = useState('idle') // idle | loading | success | error
  const [errorInfo, setErrorInfo] = useState(null)
  const [result, setResult] = useState(null)

  const activate = async (code) => {
    setStatus('loading')
    setErrorInfo(null)
    try {
      const data = await licenseApi.activate({
        code: code.trim().toUpperCase(),
        device_fingerprint: getDeviceFingerprint(),
      })
      if (setSession) {
        setLicenseSession(data.access_token, data.user)
        onLoggedIn?.(data.user)
      }
      setResult(data)
      setStatus('success')
      return data
    } catch (err) {
      setErrorInfo(classifyError(err))
      setStatus('error')
      return null
    }
  }

  const reset = () => { setStatus('idle'); setErrorInfo(null); setResult(null) }

  // Non-blocking, post-success only: a freshly activated company already
  // at its camera cap. Not an activation failure (activation never touches
  // camera assignment) — just informational.
  const atCapacity = Boolean(
    result && result.license.max_cameras > 0 && result.cameras.length >= result.license.max_cameras,
  )

  return { status, errorInfo, result, atCapacity, activate, reset }
}
