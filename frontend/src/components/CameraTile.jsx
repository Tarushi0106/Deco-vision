import { useEffect, useRef, useState } from 'react'
import { WS_HOST, WS_PROTOCOL } from '../api'
import './CameraTile.css'

export default function CameraTile({ camera, showOverlay = true, onClick, large = false }) {
  const canvasRef = useRef(null)
  const overlayRef = useRef(null)
  const [status, setStatus] = useState(camera.live ? 'connecting' : 'offline')

  useEffect(() => {
    if (!camera.live) {
      setStatus('offline')
      return
    }

    const canvas = canvasRef.current
    const overlay = overlayRef.current
    const ctx = canvas.getContext('2d')
    const overlayCtx = overlay.getContext('2d')
    const img = new Image()
    let objectUrl = null

    const videoWs = new WebSocket(`${WS_PROTOCOL}://${WS_HOST}/ws/live/${camera.id}`)
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
    }

    let detectionsWs = null
    if (showOverlay) {
      detectionsWs = new WebSocket(`${WS_PROTOCOL}://${WS_HOST}/ws/detections/${camera.id}`)
      detectionsWs.onmessage = (event) => {
        const { faces } = JSON.parse(event.data)
        overlayCtx.clearRect(0, 0, overlay.width, overlay.height)
        overlayCtx.lineWidth = 2
        overlayCtx.font = 'bold 18px sans-serif'
        overlayCtx.textBaseline = 'bottom'

        const LABEL_HEIGHT = 25
        const placedLabels = []
        const overlaps = (a, b) => a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y

        // sort left-to-right so labels for adjacent faces are placed in a
        // stable order — otherwise which face "wins" the preferred spot
        // above its box flickers frame to frame as detection order changes
        const sortedFaces = [...(faces || [])].sort((a, b) => a.bbox[0] - b.bbox[0])

        for (const face of sortedFaces) {
          const [x1, y1, x2, y2] = face.bbox
          const known = face.name !== 'Unknown'
          const color = known ? '#1a7f4b' : '#c62828'
          // Recognized-name label uses a distinct, lighter blue background
          // (rather than reusing the box's dark green) — white text on the
          // dark green read poorly; blue + white has much better contrast.
          const labelBg = known ? '#2f6fed' : color
          overlayCtx.strokeStyle = color
          overlayCtx.strokeRect(x1, y1, x2 - x1, y2 - y1)

          const label = known ? face.name : 'Unknown'
          const labelWidth = overlayCtx.measureText(label).width + 8

          // default: just above the box. If that collides with a
          // neighboring face's label (faces close together), place it
          // inside the top of this box instead rather than overlapping.
          let candidate = { x: x1, y: y1 - LABEL_HEIGHT, w: labelWidth, h: LABEL_HEIGHT }
          if (placedLabels.some((r) => overlaps(r, candidate))) {
            candidate = { x: x1, y: y1, w: labelWidth, h: LABEL_HEIGHT }
          }
          placedLabels.push(candidate)

          overlayCtx.fillStyle = labelBg
          overlayCtx.fillRect(candidate.x, candidate.y, candidate.w, candidate.h)
          overlayCtx.fillStyle = 'white'
          overlayCtx.fillText(label, candidate.x + 4, candidate.y + LABEL_HEIGHT - 4)
        }
      }
    }

    return () => {
      videoWs.close()
      detectionsWs?.close()
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [camera.id, camera.live, showOverlay])

  return (
    <div
      className={`camera-tile card${onClick ? ' camera-tile-clickable' : ''}${large ? ' camera-tile-large' : ''}`}
      onClick={onClick}
    >
      <div className="camera-tile-video">
        <canvas ref={canvasRef} />
        <canvas ref={overlayRef} className="camera-tile-overlay" />
        {status !== 'live' && (
          <div className="camera-tile-offline">
            {camera.is_configured ? 'Starting live feed...' : 'Not configured'}
          </div>
        )}
        <span className={`pill camera-tile-badge ${status === 'live' ? 'pill-success' : 'pill-neutral'}`}>
          <span className={`dot ${status === 'live' ? 'dot-success' : 'dot-danger'}`} />
          {status === 'live' ? 'LIVE' : status === 'connecting' ? 'CONNECTING' : 'OFFLINE'}
        </span>
      </div>
      <div className="camera-tile-label">
        <span>{camera.name}</span>
        <span className="camera-tile-site">{camera.site}</span>
      </div>
    </div>
  )
}
