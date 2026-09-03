const configuredApiBase = import.meta.env.VITE_API_BASE

export const API_BASE = configuredApiBase || ''
export const WS_HOST = configuredApiBase
  ? configuredApiBase.replace(/^https?:\/\//, '')
  : window.location.host
export const WS_PROTOCOL = configuredApiBase
  ? configuredApiBase.startsWith('https') ? 'wss' : 'ws'
  : window.location.protocol === 'https:' ? 'wss' : 'ws'

async function req(path, options) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `Request failed: ${res.status}`)
  }
  return res.json()
}

export const api = {
  listCameras: () => req('/api/cameras'),
  createCamera: (data) => req('/api/cameras', { method: 'POST', body: JSON.stringify(data) }),
  updateCamera: (id, data) => req(`/api/cameras/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteCamera: (id) => req(`/api/cameras/${id}`, { method: 'DELETE' }),
  listFaces: () => req('/api/faces'),
  getStats: () => req('/api/stats'),
  listSites: () => req('/api/sites'),
  createSite: (data) => req('/api/sites', { method: 'POST', body: JSON.stringify(data) }),
  updateSite: (id, data) => req(`/api/sites/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteSite: (id) => req(`/api/sites/${id}`, { method: 'DELETE' }),
  deletePerson: (name) => req(`/api/people/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  renamePerson: (name, newName, employeeId) =>
    req(`/api/people/${encodeURIComponent(name)}`, {
      method: 'PUT',
      body: JSON.stringify({ new_name: newName, employee_id: employeeId ?? null }),
    }),
  syncPeopleFromCamera: () => req('/api/people/sync-from-camera', { method: 'POST' }),
  login: (email) => req('/api/auth/login', { method: 'POST', body: JSON.stringify({ email }) }),
  listAlerts: ({ resolved } = {}) =>
    req(`/api/alerts${resolved === undefined ? '' : `?resolved=${resolved}`}`),
  resolveAlert: (id) => req(`/api/alerts/${id}/resolve`, { method: 'POST' }),
  getSettings: () => req('/api/settings'),
  updateSettings: (data) => req('/api/settings', { method: 'PUT', body: JSON.stringify(data) }),
  getAttendance: (date) => req(`/api/attendance${date ? `?date=${date}` : ''}`),
  getAttendanceReport: (name, start, end) =>
    req(`/api/attendance/report?name=${encodeURIComponent(name)}&start=${start}&end=${end}`),
  attendanceReportXlsxUrl: (name, start, end) =>
    `${API_BASE}/api/attendance/report/xlsx?name=${encodeURIComponent(name)}&start=${start}&end=${end}`,
  attendanceReportPdfUrl: (name, start, end) =>
    `${API_BASE}/api/attendance/report/pdf?name=${encodeURIComponent(name)}&start=${start}&end=${end}`,
  getPeopleAnalytics: (days = 7) => req(`/api/analytics/people?days=${days}`),
  getClips: (personName) => req(`/api/clips?person=${encodeURIComponent(personName)}`),
  getClipsForDay: (personName, date) =>
    req(`/api/people/${encodeURIComponent(personName)}/clips-for-day?date=${date}`),
  clipVideoUrl: (clipId) => `${API_BASE}/api/clips/${clipId}/video`,
  alertSnapshotUrl: (alertId) => `${API_BASE}/api/alerts/${alertId}/snapshot`,
  getActiveClips: () => req('/api/clips/active'),
  listZones: (cameraId) => req(`/api/zones${cameraId ? `?camera_id=${cameraId}` : ''}`),
  createZone: (data) => req('/api/zones', { method: 'POST', body: JSON.stringify(data) }),
  updateZone: (id, data) => req(`/api/zones/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteZone: (id) => req(`/api/zones/${id}`, { method: 'DELETE' }),
  getFootfallReport: (date) => req(`/api/footfall/report${date ? `?date=${date}` : ''}`),
  footfallReportCsvUrl: (date) => `${API_BASE}/api/footfall/report/csv${date ? `?date=${date}` : ''}`,
  footfallReportXlsxUrl: (date) => `${API_BASE}/api/footfall/report/xlsx${date ? `?date=${date}` : ''}`,
  getPeopleCountReport: (date) => req(`/api/footfall/people-count${date ? `?date=${date}` : ''}`),
  listDeskZones: (cameraId) => req(`/api/desk-zones${cameraId ? `?camera_id=${cameraId}` : ''}`),
  createDeskZone: (data) => req('/api/desk-zones', { method: 'POST', body: JSON.stringify(data) }),
  updateDeskZone: (id, data) => req(`/api/desk-zones/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteDeskZone: (id) => req(`/api/desk-zones/${id}`, { method: 'DELETE' }),
  getDeskAnalyticsReport: (date) => req(`/api/desk-analytics/report${date ? `?date=${date}` : ''}`),
  listFootfallGates: (cameraId) => req(`/api/footfall-gate${cameraId ? `?camera_id=${cameraId}` : ''}`),
  setFootfallGate: (data) => req('/api/footfall-gate', { method: 'POST', body: JSON.stringify(data) }),
  flipFootfallGate: (cameraId) => req(`/api/footfall-gate/${cameraId}/flip`, { method: 'POST' }),
  deleteFootfallGate: (cameraId) => req(`/api/footfall-gate/${cameraId}`, { method: 'DELETE' }),
}

// --- License & Camera Access Management ------------------------------
// Separate request helper: these endpoints require a bearer token (see
// licenseAuth.js), unlike everything in `api` above, which the rest of
// this app calls with no auth at all.
import { clearLicenseSession, getLicenseToken } from './licenseAuth'

async function licenseReq(path, options = {}) {
  const token = getLicenseToken()
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  })
  if (res.status === 401) {
    clearLicenseSession()
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    const err = new Error(body.detail || `Request failed: ${res.status}`)
    err.status = res.status
    throw err
  }
  const contentType = res.headers.get('content-type') || ''
  return contentType.includes('application/json') ? res.json() : res.blob()
}

export const licenseApi = {
  login: (email, password) =>
    licenseReq('/api/auth/license/login', { method: 'POST', body: JSON.stringify({ email, password }) }),
  logout: () => licenseReq('/api/auth/license/logout', { method: 'POST' }),
  me: () => licenseReq('/api/auth/license/me'),

  listUsers: () => licenseReq('/api/license-users'),
  createUser: (data) => licenseReq('/api/license-users', { method: 'POST', body: JSON.stringify(data) }),

  listCompanies: () => licenseReq('/api/companies'),
  createCompany: (name) => licenseReq('/api/companies', { method: 'POST', body: JSON.stringify({ name }) }),

  listLicenses: (params = {}) => {
    const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== '' && v != null))
    return licenseReq(`/api/licenses${qs.toString() ? `?${qs}` : ''}`)
  },
  getLicense: (id) => licenseReq(`/api/licenses/${id}`),
  createLicense: (data) => licenseReq('/api/licenses', { method: 'POST', body: JSON.stringify(data) }),
  updateLicense: (id, data) => licenseReq(`/api/licenses/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  revokeLicense: (id) => licenseReq(`/api/licenses/${id}/revoke`, { method: 'POST' }),
  enableLicense: (id) => licenseReq(`/api/licenses/${id}/enable`, { method: 'POST' }),
  disableLicense: (id) => licenseReq(`/api/licenses/${id}/disable`, { method: 'POST' }),
  // QR needs a Bearer header, so it can't be a plain <img src> URL — the
  // component fetches this blob and turns it into an object URL instead.
  getLicenseQrBlob: (id) => licenseReq(`/api/licenses/${id}/qr`),
  getLicenseQrToken: (id) => licenseReq(`/api/licenses/${id}/qr-token`),

  listLicenseCameras: (id) => licenseReq(`/api/licenses/${id}/cameras`),
  assignLicenseCameras: (id, cameraIds) =>
    licenseReq(`/api/licenses/${id}/cameras`, { method: 'POST', body: JSON.stringify({ camera_ids: cameraIds }) }),
  removeLicenseCameras: (id, cameraIds) =>
    licenseReq(`/api/licenses/${id}/cameras/remove`, { method: 'POST', body: JSON.stringify({ camera_ids: cameraIds }) }),

  analytics: () => licenseReq('/api/licenses/analytics'),
  auditLogs: (limit = 50, offset = 0) => licenseReq(`/api/audit-logs?limit=${limit}&offset=${offset}`),

  activate: (data) => licenseReq('/api/licenses/activate', { method: 'POST', body: JSON.stringify(data) }),
  myLicense: () => licenseReq('/api/my-license'),

  featureCatalog: () => licenseReq('/api/feature-catalog'),
  getLicenseFeatures: (id) => licenseReq(`/api/licenses/${id}/features`),
  setLicenseFeatures: (id, keys) =>
    licenseReq(`/api/licenses/${id}/features`, { method: 'PUT', body: JSON.stringify({ feature_keys: keys }) }),

  listLicenseCamerasDetailed: (id) => licenseReq(`/api/licenses/${id}/cameras/detailed`),
  getCameraFeatures: (id) => licenseReq(`/api/cameras/${id}/features`),
  setCameraFeatures: (id, keys) =>
    licenseReq(`/api/cameras/${id}/features`, { method: 'PUT', body: JSON.stringify({ feature_keys: keys }) }),

  getCameraPermissions: (id) => licenseReq(`/api/cameras/${id}/permissions`),
  setCameraPermission: (id, userUuid, perms) =>
    licenseReq(`/api/cameras/${id}/permissions/${userUuid}`, { method: 'PUT', body: JSON.stringify(perms) }),
}
