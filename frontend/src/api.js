const BASE = 'http://127.0.0.1:8811'

async function req(path, options) {
  const res = await fetch(`${BASE}${path}`, {
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
  deleteSite: (id) => req(`/api/sites/${id}`, { method: 'DELETE' }),
  login: (email) => req('/api/auth/login', { method: 'POST', body: JSON.stringify({ email }) }),
  listUsers: () => req('/api/users'),
}
