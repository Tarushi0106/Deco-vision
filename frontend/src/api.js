export const API_BASE = import.meta.env.VITE_API_BASE || 'http://127.0.0.1:8811'
export const WS_HOST = API_BASE.replace(/^https?:\/\//, '')
export const WS_PROTOCOL = API_BASE.startsWith('https') ? 'wss' : 'ws'

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
  getActiveClips: () => req('/api/clips/active'),
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
