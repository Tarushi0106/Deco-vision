import { useEffect, useRef, useState } from 'react'
import { DETECTION_WS_BASE, PARACHUTE_WS_BASE } from '../api'

// One consistent color for every person box — recognized or not; only the
// label text changes ("Person" vs the matched name). Never "Unknown".
const PERSON_BOX_COLOR = '#3b82f6'
const LABEL_HEIGHT = 16

// Stateless per-frame match, not a tracker: is a known face's center inside
// this person's box, in whichever of this render's two independent
// detection streams (people from person_detector.py, faces from
// recognizer.py) happen to be current right now.
function matchName(personBbox, faces) {
  const [px1, py1, px2, py2] = personBbox
  for (const face of faces) {
    if (face.name === 'Unknown') continue
    const [fx1, fy1, fx2, fy2] = face.bbox
    const cx = (fx1 + fx2) / 2
    const cy = (fy1 + fy2) / 2
    if (cx >= px1 && cx <= px2 && cy >= py1 && cy <= py2) return face.name
  }
  return null
}

export default function DetectionCameraTile({ camera }) {
  const canvasRef = useRef(null)
  const overlayRef = useRef(null)
  const [status, setStatus] = useState('connecting')

  useEffect(() => {
    const canvas = canvasRef.current
    const overlay = overlayRef.current
    const ctx = canvas.getContext('2d')
    const overlayCtx = overlay.getContext('2d')
    const img = new Image()
    let objectUrl = null
    let latestPeople = []
    let latestFaces = []

    // Video comes straight from Parachute's existing, already-public live
    // feed — the same WebSocket a Parachute browser tab already opens.
    // This project never opens a second RTSP connection.
    const videoWs = new WebSocket(`${PARACHUTE_WS_BASE}/ws/live/${camera.id}`)
    videoWs.binaryType = 'blob'
    videoWs.onopen = () => setStatus('live')
    videoWs.onclose = () => setStatus('offline')
    videoWs.onerror = () => setStatus('offline')
    videoWs.onmessage = (event) => {
      if (objectUrl) URL.revokeObjectURL(objectUrl)
      objectUrl = URL.createObjectURL(event.data)
      img.src = objectUrl
    }
    img.onload = () => {
      if (canvas.width !== img.width || canvas.height !== img.height) {
        canvas.width = img.width
        canvas.height = img.height
        overlay.width = img.width
        overlay.height = img.height
      }
      ctx.drawImage(img, 0, 0)
      redraw()
    }

    function redraw() {
      overlayCtx.clearRect(0, 0, overlay.width, overlay.height)
      overlayCtx.font = 'bold 12px sans-serif'
      overlayCtx.textBaseline = 'bottom'
      for (const person of latestPeople) {
        const [x1, y1, x2, y2] = person.bbox
        overlayCtx.strokeStyle = PERSON_BOX_COLOR
        overlayCtx.lineWidth = 2
        overlayCtx.strokeRect(x1, y1, x2 - x1, y2 - y1)

        const label = matchName(person.bbox, latestFaces) || 'Person'
        const labelWidth = overlayCtx.measureText(label).width + 8
        const labelY = y1 - LABEL_HEIGHT >= 0 ? y1 - LABEL_HEIGHT : y1
        overlayCtx.fillStyle = PERSON_BOX_COLOR
        overlayCtx.fillRect(x1, labelY, labelWidth, LABEL_HEIGHT)
        overlayCtx.fillStyle = 'white'
        overlayCtx.fillText(label, x1 + 3, labelY + LABEL_HEIGHT - 3)
      }
    }

    // This project's OWN detection channel — separate from Parachute's own
    // /ws/detections, which is untouched.
    const detWs = new WebSocket(`${DETECTION_WS_BASE}/ws/detections/${camera.id}`)
    detWs.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        latestPeople = data.people || []
        latestFaces = data.faces || []
      } catch {
        // keep showing the last good overlay on a malformed payload
      }
      redraw()
    }
    detWs.onerror = () => {}

    return () => {
      videoWs.close()
      detWs.close()
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [camera.id])

  return (
    <div className="card">
      <div className="camera-tile-video">
        <canvas ref={canvasRef} />
        <canvas ref={overlayRef} className="camera-tile-overlay" />
      </div>
      <div className="camera-tile-label">
        {camera.name} — <span className="muted">{status}</span>
      </div>
    </div>
  )
}
