// Token storage for the License & Camera Access Management module's own
// JWT auth (backend/app/auth.py) — separate from auth.js's currentUser,
// which is just the main dashboard's no-password login tracker and grants
// no real permissions. Kept in its own module/localStorage key so the two
// systems can't be confused with each other.

const TOKEN_KEY = 'licenseAuthToken'
const USER_KEY = 'licenseAuthUser'

export function getLicenseToken() {
  return localStorage.getItem(TOKEN_KEY)
}

export function getLicenseUser() {
  try {
    const raw = localStorage.getItem(USER_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

export function setLicenseSession(token, user) {
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(USER_KEY, JSON.stringify(user))
}

export function clearLicenseSession() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}

const DEVICE_ID_KEY = 'licenseDeviceFingerprint'

// A stable per-browser identifier for device-bound license activation —
// not a real hardware fingerprint, just enough to tell "this browser
// activated before" from "this is a different device" across visits.
export function getDeviceFingerprint() {
  let id = localStorage.getItem(DEVICE_ID_KEY)
  if (!id) {
    id = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`
    localStorage.setItem(DEVICE_ID_KEY, id)
  }
  return id
}
