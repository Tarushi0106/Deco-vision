// Two distinct API bases — never conflated:
//   detectionApi   -> this project's OWN backend (detection/backend), person/face boxes + training
//   parachuteApi   -> the EXISTING Parachute backend, used ONLY for the camera list and the live
//                      video feed (the same public endpoints a browser tab already uses)
const DETECTION_BASE = import.meta.env.VITE_DETECTION_API_BASE || 'http://127.0.0.1:8812'
const PARACHUTE_BASE = import.meta.env.VITE_PARACHUTE_API_BASE || 'http://127.0.0.1:8811'

function wsBase(httpBase) {
  return httpBase.replace(/^http/, 'ws')
}

export const DETECTION_WS_BASE = wsBase(DETECTION_BASE)
export const PARACHUTE_WS_BASE = wsBase(PARACHUTE_BASE)

async function req(base, path, options) {
  const res = await fetch(`${base}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `Request failed: ${res.status}`)
  }
  return res.json()
}

export const parachuteApi = {
  listCameras: () => req(PARACHUTE_BASE, '/api/cameras'),
}

export const detectionApi = {
  health: () => req(DETECTION_BASE, '/health'),
  getStatus: () => req(DETECTION_BASE, '/api/face-training/status'),
  getNextSample: () => req(DETECTION_BASE, '/api/face-training/next'),
  sampleImageUrl: (id) => `${DETECTION_BASE}/api/face-training/image/${id}`,
  labelSample: (id, name) =>
    req(DETECTION_BASE, '/api/face-training/label', { method: 'POST', body: JSON.stringify({ sample_id: id, name }) }),
  skipSample: (id) =>
    req(DETECTION_BASE, '/api/face-training/skip', { method: 'POST', body: JSON.stringify({ sample_id: id }) }),
  trainNow: () => req(DETECTION_BASE, '/api/face-training/train', { method: 'POST' }),
  getHistory: (limit = 20) => req(DETECTION_BASE, `/api/face-training/history?limit=${limit}`),
}
