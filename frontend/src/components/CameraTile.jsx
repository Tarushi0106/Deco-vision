import { useEffect, useRef, useState } from 'react'
import { WS_HOST, WS_PROTOCOL } from '../api'
import './CameraTile.css'

// zones are edited rarely (unlike per-frame face detections) but still need
// to render inside the same detections-driven redraw cycle to stay in the
// right native-pixel coordinate space — refs let the persistent WS handler
// (set up once per camera/showOverlay, see the effect below) always read the
// latest value without tearing down and reopening the sockets on every click
function drawZonesOverlay(ctx, zones) {
  for (const zone of zones) {
    if (!zone.polygon || zone.polygon.length < 2) continue
    ctx.beginPath()
    zone.polygon.forEach(([x, y], i) => (i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)))
    ctx.closePath()
    ctx.fillStyle = 'rgba(255, 193, 7, 0.15)'
    ctx.fill()
    ctx.strokeStyle = zone.enabled === false ? '#6b7280' : '#ffc107'
    ctx.lineWidth = 2
    ctx.stroke()
    const [lx, ly] = zone.polygon[0]
    ctx.fillStyle = zone.enabled === false ? '#6b7280' : '#ffc107'
    ctx.font = '14px sans-serif'
    ctx.fillText(zone.name, lx + 4, Math.max(14, ly - 6))
  }
}

// Ray casting, same algorithm as desk_tracker.py's DeskTracker._in_zone
// (kept in sync deliberately — both decide "is this point inside this
// shape" from the same kind of polygon, just one in the browser for a
// click and one in the backend for a detected face).
function pointInPolygon(px, py, polygon) {
  let inside = false
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const [xi, yi] = polygon[i]
    const [xj, yj] = polygon[j]
    const intersects = yi > py !== yj > py && px < ((xj - xi) * (py - yi)) / (yj - yi) + xi
    if (intersects) inside = !inside
  }
  return inside
}

function drawDraftPolygon(ctx, points) {
  if (!points || points.length === 0) return
  if (points.length > 1) {
    ctx.beginPath()
    points.forEach(([x, y], i) => (i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)))
    if (points.length >= 3) ctx.closePath() // ≥3 points = a valid finished shape, close the loop for clarity
    ctx.strokeStyle = '#facc15'
    ctx.lineWidth = 2
    ctx.stroke()
  }
  ctx.fillStyle = '#facc15'
  points.forEach(([x, y]) => {
    ctx.beginPath()
    ctx.arc(x, y, 4, 0, Math.PI * 2)
    ctx.fill()
  })
}

export default function CameraTile({
  camera,
  showOverlay = true,
  onClick,
  large = false,
  zones = [],
  drawMode = false,
  draftPoints = [],
  onAddPoint,
  onFrameSize,
  onZoneClick,
  zonePointSpace = 'pixel', // 'pixel' (restricted zones) or 'fraction' (desk zones — see desk_db.py)
}) {
  const canvasRef = useRef(null)
  const overlayRef = useRef(null)
  const [status, setStatus] = useState(camera.live ? 'connecting' : 'offline')

  const zonesRef = useRef(zones)
  const draftPointsRef = useRef(draftPoints)
  useEffect(() => {
    zonesRef.current = zones
  }, [zones])
  useEffect(() => {
    draftPointsRef.current = draftPoints
  }, [draftPoints])

  const handleCanvasClick = (e) => {
    const canvas = overlayRef.current
    const rect = canvas.getBoundingClientRect()
    const x = ((e.clientX - rect.left) / rect.width) * canvas.width
    const y = ((e.clientY - rect.top) / rect.height) * canvas.height

    if (drawMode) {
      onAddPoint?.(x, y)
      return
    }
    if (!onZoneClick) return
    // Zone polygons are stored either in native frame pixels (restricted
    // zones) or as 0..1 fractions of frame size (desk zones — survives a
    // camera resolution change, see desk_db.py) — convert the click point
    // to whichever space this caller's zones use rather than converting
    // every zone's polygon on every click.
    const px = zonePointSpace === 'fraction' ? x / canvas.width : x
    const py = zonePointSpace === 'fraction' ? y / canvas.height : y
    const hit = zonesRef.current.find((zone) => zone.polygon?.length >= 3 && pointInPolygon(px, py, zone.polygon))
    if (hit) onZoneClick(hit)
  }

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
        onFrameSize?.({ width: img.width, height: img.height })
      }
      ctx.drawImage(img, 0, 0)
    }

    let detectionsWs = null
    // Fallback only for a payload from a backend that hasn't been restarted
    // since this field was added — the real value always arrives once it has.
    const DEFAULT_IDENTITY_LOST_TIMEOUT_SECONDS = 1.0
    if (showOverlay) {
      detectionsWs = new WebSocket(`${WS_PROTOCOL}://${WS_HOST}/ws/detections/${camera.id}`)
      detectionsWs.onmessage = (event) => {
        const { faces, fire_smoke, computed_at, identity_lost_timeout } = JSON.parse(event.data)
        // /ws/detections polls the backend's last computed result on its own
        // fixed schedule (every ~167ms) regardless of whether recognition
        // has actually produced anything new since the last tick — a
        // message arriving here is NOT evidence the data is fresh. A busy
        // multi-person scene can take several seconds per real recognition
        // cycle, so without this check the same stale name/box would keep
        // redrawing every message long after the person actually left.
        // computed_at is when the backend last ran set_detections() for
        // this camera; if that's older than the configured timeout, treat
        // it the same as "nothing known right now" rather than redrawing it.
        const ageSeconds = computed_at ? Date.now() / 1000 - computed_at : 0
        const timeoutSeconds = identity_lost_timeout ?? DEFAULT_IDENTITY_LOST_TIMEOUT_SECONDS
        const dataIsStale = ageSeconds > timeoutSeconds
        const freshFaces = dataIsStale ? [] : faces || []
        const freshFireSmoke = dataIsStale ? [] : fire_smoke || []
        overlayCtx.clearRect(0, 0, overlay.width, overlay.height)
        drawZonesOverlay(overlayCtx, zonesRef.current)
        drawDraftPolygon(overlayCtx, draftPointsRef.current)
        overlayCtx.lineWidth = 2
        overlayCtx.font = 'bold 9px sans-serif'
        overlayCtx.textBaseline = 'bottom'

        const LABEL_HEIGHT = 12
        const placedLabels = []
        const overlaps = (a, b) => a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y

        // sort left-to-right so labels for adjacent faces are placed in a
        // stable order — otherwise which face "wins" the preferred spot
        // above its box flickers frame to frame as detection order changes
        const sortedFaces = [...freshFaces].sort((a, b) => a.bbox[0] - b.bbox[0])

        for (const face of sortedFaces) {
          const [x1, y1, x2, y2] = face.bbox
          const known = face.name !== 'Unknown'
          const color = face.zone_violation ? '#f97316' : known ? '#1a7f4b' : '#c62828'
          // Recognized-name label uses a distinct, lighter blue background
          // (rather than reusing the box's dark green) — white text on the
          // dark green read poorly; blue + white has much better contrast.
          const labelBg = known ? '#2f6fed' : color

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
          overlayCtx.fillText(label, candidate.x + 3, candidate.y + LABEL_HEIGHT - 2)
        }

        for (const item of freshFireSmoke) {
          // Smoke is alert-only (see AlertBanner) — no bounding box drawn, per
          // request: a hazy/uncertain region flagged with a box on live video
          // reads as a false-positive accusation more than a fire box does.
          if (item.type === 'smoke') continue
          const [x1, y1, x2, y2] = item.bbox
          const color = item.type === 'fire' ? '#ef4444' : '#8b5cf6'
          overlayCtx.strokeStyle = color
          overlayCtx.lineWidth = 3
          overlayCtx.strokeRect(x1, y1, x2 - x1, y2 - y1)

          const label = item.type.toUpperCase()
          const labelWidth = overlayCtx.measureText(label).width + 8
          overlayCtx.fillStyle = color
          overlayCtx.fillRect(x1, y1 - LABEL_HEIGHT, labelWidth, LABEL_HEIGHT)
          overlayCtx.fillStyle = 'white'
          overlayCtx.fillText(label, x1 + 4, y1 - 4)
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
        <canvas
          ref={overlayRef}
          className="camera-tile-overlay"
          onClick={handleCanvasClick}
          style={
            drawMode
              ? { cursor: 'crosshair', pointerEvents: 'auto' }
              : onZoneClick
                ? { cursor: 'pointer', pointerEvents: 'auto' }
                : undefined
          }
        />
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
