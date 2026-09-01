import { useEffect, useMemo, useRef, useState } from 'react'
import { api, WS_HOST, WS_PROTOCOL } from '../api'
import './pages.css'
import './footfall.css'

const MAX_CAMERA_SLOTS = 4 // categorical palette ceiling before folding into "Other" — see footfall.css

function todayStr() {
  return new Date().toISOString().slice(0, 10)
}

function formatDateTime(ts) {
  return new Date(ts * 1000).toLocaleString('en-IN', {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function StatTile({ label, value, sub }) {
  return (
    <div className="stat-tile card">
      <div className="stat-tile-label">{label}</div>
      <div className="stat-tile-value">{value}</div>
      {sub && <div className="stat-tile-sub">{sub}</div>}
    </div>
  )
}

// Top-rounded, square-baseline bar (see dataviz skill mark spec) as an SVG path.
function roundedTopBarPath(x, yTop, w, h, r) {
  if (h <= 0) return ''
  const rad = Math.max(0, Math.min(r, w / 2, h))
  const yBase = yTop + h
  if (rad === 0) return `M${x},${yBase} H${x + w} V${yTop} H${x} Z`
  return `M${x},${yBase} V${yTop + rad} Q${x},${yTop} ${x + rad},${yTop} H${x + w - rad} Q${x + w},${yTop} ${x + w},${yTop + rad} V${yBase} Z`
}

// Right-rounded, square-baseline (left edge) bar for horizontal charts.
function roundedRightBarPath(x, y, w, h, r) {
  if (w <= 0) return ''
  const rad = Math.max(0, Math.min(r, h / 2, w))
  if (rad === 0) return `M${x},${y} H${x + w} V${y + h} H${x} Z`
  return `M${x},${y} H${x + w - rad} Q${x + w},${y} ${x + w},${y + rad} V${y + h - rad} Q${x + w},${y + h} ${x + w - rad},${y + h} H${x} Z`
}

function ChartTooltip({ tooltip }) {
  if (!tooltip) return null
  return (
    <div className="viz-tooltip" style={{ left: tooltip.x, top: tooltip.y }}>
      <div className="viz-tooltip-value">{tooltip.value}</div>
      <div className="viz-tooltip-label">{tooltip.label}</div>
    </div>
  )
}

function HourlyBarChart({ hourly }) {
  const containerRef = useRef(null)
  const [tooltip, setTooltip] = useState(null)

  const width = 760
  const height = 200
  const padding = { top: 24, right: 6, bottom: 22, left: 6 }
  const plotW = width - padding.left - padding.right
  const plotH = height - padding.top - padding.bottom
  const maxVal = Math.max(1, ...hourly)
  const slot = plotW / 24
  const barW = Math.min(20, slot - 3)
  const peakValue = Math.max(...hourly)
  const peakHour = hourly.indexOf(peakValue)
  const hasData = peakValue > 0

  const showTooltip = (e, hour, value) => {
    const rect = containerRef.current.getBoundingClientRect()
    setTooltip({
      hour,
      x: e.clientX - rect.left,
      y: e.clientY - rect.top - 14,
      value: `${value} unique visitor${value === 1 ? '' : 's'}`,
      label: `${String(hour).padStart(2, '0')}:00 – ${String((hour + 1) % 24).padStart(2, '0')}:00`,
    })
  }

  return (
    <div className="viz-root viz-chart-wrap" ref={containerRef}>
      <svg viewBox={`0 0 ${width} ${height}`} className="viz-svg" role="img" aria-label="Unique footfall by hour of day">
        <line
          x1={padding.left}
          y1={height - padding.bottom}
          x2={width - padding.right}
          y2={height - padding.bottom}
          className="viz-baseline"
        />
        {hourly.map((value, hour) => {
          const x = padding.left + hour * slot + (slot - barW) / 2
          const h = hasData ? (value / maxVal) * plotH : 0
          const yTop = height - padding.bottom - h
          return (
            <g key={hour}>
              <path
                d={roundedTopBarPath(x, yTop, barW, Math.max(h, value > 0 ? 2 : 0), 4)}
                className={`viz-bar-sequential${tooltip?.hour === hour ? ' viz-bar-hover' : ''}`}
                tabIndex={0}
                onMouseMove={(e) => showTooltip(e, hour, value)}
                onMouseLeave={() => setTooltip(null)}
                onFocus={(e) => showTooltip(e, hour, value)}
                onBlur={() => setTooltip(null)}
              />
              {hour === peakHour && value > 0 && (
                <text x={x + barW / 2} y={yTop - 7} textAnchor="middle" className="viz-bar-label">
                  {value}
                </text>
              )}
              {hour % 3 === 0 && (
                <text x={x + barW / 2} y={height - padding.bottom + 14} textAnchor="middle" className="viz-axis-label">
                  {String(hour).padStart(2, '0')}
                </text>
              )}
            </g>
          )
        })}
      </svg>
      <ChartTooltip tooltip={tooltip} />
      {!hasData && <div className="empty-state">No footfall recorded yet for this day.</div>}
    </div>
  )
}

function CameraBarChart({ byCamera }) {
  const containerRef = useRef(null)
  const [tooltip, setTooltip] = useState(null)

  const rows = useMemo(() => {
    const sorted = [...byCamera].sort((a, b) => b.count - a.count)
    if (sorted.length <= MAX_CAMERA_SLOTS) return sorted
    // Never generate a 5th hue (see dataviz skill) — fold the tail into "Other" instead.
    const shown = sorted.slice(0, MAX_CAMERA_SLOTS - 1)
    const rest = sorted.slice(MAX_CAMERA_SLOTS - 1)
    const otherCount = rest.reduce((sum, r) => sum + r.count, 0)
    return [
      ...shown,
      { camera_id: 'other', camera_name: `Other (${rest.length} camera${rest.length === 1 ? '' : 's'})`, count: otherCount },
    ]
  }, [byCamera])

  const width = 480
  const rowHeight = 34
  const height = Math.max(1, rows.length) * rowHeight + 6
  const labelWidth = 140
  const plotW = width - labelWidth - 50
  const maxVal = Math.max(1, ...rows.map((r) => r.count))

  const showTooltip = (e, row) => {
    const rect = containerRef.current.getBoundingClientRect()
    setTooltip({
      id: row.camera_id,
      x: e.clientX - rect.left,
      y: e.clientY - rect.top - 14,
      value: `${row.count} unique visitor${row.count === 1 ? '' : 's'}`,
      label: row.camera_name,
    })
  }

  if (rows.length === 0) {
    return <div className="empty-state">No footfall recorded yet for this day.</div>
  }

  return (
    <div className="viz-root viz-chart-wrap" ref={containerRef}>
      <svg viewBox={`0 0 ${width} ${height}`} className="viz-svg" role="img" aria-label="Unique footfall by camera">
        {rows.map((row, i) => {
          const y = i * rowHeight + 3
          const barH = 20
          const w = row.count > 0 ? Math.max(3, (row.count / maxVal) * plotW) : 0
          return (
            <g key={row.camera_id}>
              <text x={0} y={y + barH / 2} className="viz-row-label">
                {row.camera_name}
              </text>
              <path
                d={roundedRightBarPath(labelWidth, y, w, barH, 4)}
                style={{ fill: `var(--viz-cat-${(i % MAX_CAMERA_SLOTS) + 1})` }}
                className={`viz-bar${tooltip?.id === row.camera_id ? ' viz-bar-hover' : ''}`}
                tabIndex={0}
                onMouseMove={(e) => showTooltip(e, row)}
                onMouseLeave={() => setTooltip(null)}
                onFocus={(e) => showTooltip(e, row)}
                onBlur={() => setTooltip(null)}
              />
              <text x={labelWidth + w + 8} y={y + barH / 2} className="viz-bar-end-label">
                {row.count}
              </text>
            </g>
          )
        })}
      </svg>
      <ChartTooltip tooltip={tooltip} />
    </div>
  )
}

// Draw a 2-click line across the gate on the live camera feed. Someone
// tracked crossing it in the arrow's direction counts as a footfall entry —
// see gate_tracker.py — even when their face isn't recognized, since the
// crossing itself (not a face match) is what triggers the count.
function GateEditor() {
  const [cameras, setCameras] = useState([])
  const [cameraId, setCameraId] = useState(null)
  const [gate, setGate] = useState(null)
  const [pendingPoint, setPendingPoint] = useState(null)
  const [status, setStatus] = useState('connecting')
  const [error, setError] = useState(null)
  const canvasRef = useRef(null)
  const overlayRef = useRef(null)
  const containerRef = useRef(null)

  useEffect(() => {
    api.listCameras().then((cams) => {
      setCameras(cams)
      if (cams.length > 0) setCameraId(cams[0].id)
    }).catch(() => {})
  }, [])

  const loadGate = (camId) => {
    if (!camId) return
    api.listFootfallGates(camId).then((gates) => setGate(gates[0] || null)).catch(() => {})
  }

  useEffect(() => {
    loadGate(cameraId)
    setPendingPoint(null)
  }, [cameraId])

  const camera = cameras.find((c) => c.id === cameraId)

  useEffect(() => {
    if (!camera?.live) {
      setStatus('offline')
      return
    }
    const canvas = canvasRef.current
    const overlay = overlayRef.current
    const ctx = canvas.getContext('2d')
    const img = new Image()
    let objectUrl = null

    const ws = new WebSocket(`${WS_PROTOCOL}://${WS_HOST}/ws/live/${camera.id}`)
    ws.binaryType = 'blob'
    ws.onopen = () => setStatus('live')
    ws.onclose = () => setStatus('offline')
    ws.onerror = () => setStatus('offline')
    ws.onmessage = (event) => {
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

    return () => {
      ws.close()
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [camera?.id, camera?.live])

  useEffect(() => {
    const overlay = overlayRef.current
    if (!overlay) return
    const octx = overlay.getContext('2d')
    octx.clearRect(0, 0, overlay.width, overlay.height)
    octx.lineWidth = 3

    if (gate) {
      const x1 = gate.x1 * overlay.width, y1 = gate.y1 * overlay.height
      const x2 = gate.x2 * overlay.width, y2 = gate.y2 * overlay.height
      octx.strokeStyle = '#1baf7a'
      octx.beginPath()
      octx.moveTo(x1, y1)
      octx.lineTo(x2, y2)
      octx.stroke()

      // Direction arrow, perpendicular to the line, matching the backend's
      // cross-product sign convention (see gate_tracker.py._side_sign).
      const mx = (x1 + x2) / 2, my = (y1 + y2) / 2
      const dx = x2 - x1, dy = y2 - y1
      const len = Math.hypot(dx, dy) || 1
      const sign = gate.entry_sign < 0 ? -1 : 1
      const px = (-dy / len) * sign, py = (dx / len) * sign
      const ax = mx + px * 45, ay = my + py * 45
      octx.beginPath()
      octx.moveTo(mx, my)
      octx.lineTo(ax, ay)
      octx.stroke()
      const angle = Math.atan2(ay - my, ax - mx)
      octx.beginPath()
      octx.moveTo(ax, ay)
      octx.lineTo(ax - 12 * Math.cos(angle - Math.PI / 6), ay - 12 * Math.sin(angle - Math.PI / 6))
      octx.lineTo(ax - 12 * Math.cos(angle + Math.PI / 6), ay - 12 * Math.sin(angle + Math.PI / 6))
      octx.closePath()
      octx.fillStyle = '#1baf7a'
      octx.fill()
    }

    if (pendingPoint) {
      octx.fillStyle = '#c62828'
      octx.beginPath()
      octx.arc(pendingPoint.x * overlay.width, pendingPoint.y * overlay.height, 6, 0, Math.PI * 2)
      octx.fill()
    }
  }, [gate, pendingPoint])

  const handleClick = async (e) => {
    const rect = containerRef.current.getBoundingClientRect()
    const x = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width))
    const y = Math.min(1, Math.max(0, (e.clientY - rect.top) / rect.height))

    if (!pendingPoint) {
      setPendingPoint({ x, y })
      return
    }
    setError(null)
    try {
      await api.setFootfallGate({
        camera_id: cameraId, x1: pendingPoint.x, y1: pendingPoint.y, x2: x, y2: y, entry_sign: 1,
      })
      loadGate(cameraId)
    } catch (err) {
      setError(err.message)
    } finally {
      setPendingPoint(null)
    }
  }

  const handleFlip = async () => {
    await api.flipFootfallGate(cameraId)
    loadGate(cameraId)
  }

  const handleRemove = async () => {
    await api.deleteFootfallGate(cameraId)
    loadGate(cameraId)
  }

  return (
    <div className="dashboard-main-grid" style={{ marginBottom: '1rem' }}>
      <div className="card panel">
        <div className="panel-header">
          <h3>Entry Gate Line</h3>
          <select value={cameraId ?? ''} onChange={(e) => setCameraId(Number(e.target.value))}>
            {cameras.map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
        </div>

        {camera ? (
          <div className="footfall-gate-editor" ref={containerRef} onClick={handleClick}>
            <canvas ref={canvasRef} className="footfall-gate-video" />
            <canvas ref={overlayRef} className="footfall-gate-overlay" />
            {status !== 'live' && (
              <div className="camera-tile-offline">{status === 'offline' ? 'Camera offline' : 'Connecting…'}</div>
            )}
          </div>
        ) : (
          <div className="empty-state">No cameras configured.</div>
        )}

        {pendingPoint && (
          <div className="stat-tile-sub" style={{ marginTop: '0.5rem' }}>Click the second point to finish the line…</div>
        )}
        {error && <div className="form-message error">{error}</div>}
      </div>

      <div className="card panel">
        <div className="panel-header">
          <h3>Gate on this camera</h3>
        </div>
        <div className="stat-tile-sub" style={{ marginBottom: '0.6rem' }}>
          Click two points on the feed to draw a line across the gate. Someone tracked crossing it in the arrow's
          direction counts as a unique footfall entry — even if their face isn't recognized, since the crossing
          itself is what triggers the count, not a face match.
        </div>
        {gate ? (
          <div className="footfall-gate-actions">
            <button className="btn btn-outline" onClick={handleFlip}>Flip Direction</button>
            <button className="btn btn-outline" onClick={handleRemove}>Remove Gate</button>
          </div>
        ) : (
          <div className="empty-state">No gate line yet — draw one on the left.</div>
        )}
      </div>
    </div>
  )
}

export default function Footfall() {
  const [date, setDate] = useState(todayStr())
  const [report, setReport] = useState(null)
  const [peopleCount, setPeopleCount] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    setError(null)
    api.getFootfallReport(date).then(setReport).catch((err) => setError(err.message))
    api.getPeopleCountReport(date).then(setPeopleCount).catch(() => {})
  }, [date])

  const busiestHour = useMemo(() => {
    if (!report) return null
    const max = Math.max(...report.hourly)
    if (max === 0) return null
    const hour = report.hourly.indexOf(max)
    return `${String(hour).padStart(2, '0')}:00`
  }, [report])

  const topCamera = useMemo(() => {
    if (!report || report.by_camera.length === 0) return null
    return [...report.by_camera].sort((a, b) => b.count - a.count)[0]
  }, [report])

  return (
    <div>
      <div className="page-toolbar">
        <div>
          <h2>Footfall</h2>
          <div className="page-toolbar-sub">
            Unique people counted per camera, deduped by face embedding within the re-identification window
          </div>
        </div>
        <div className="footfall-toolbar">
          <input type="date" value={date} max={todayStr()} onChange={(e) => setDate(e.target.value)} />
          {report && report.total > 0 && (
            <>
              <a className="btn btn-outline" href={api.footfallReportCsvUrl(date)}>
                Export CSV
              </a>
              <a className="btn btn-outline" href={api.footfallReportXlsxUrl(date)}>
                Export Excel
              </a>
            </>
          )}
        </div>
      </div>

      {error && <div className="form-message error">{error}</div>}

      <GateEditor />

      <div className="stat-grid">
        <StatTile label="Unique Footfall" value={report ? report.total : '—'} sub={`on ${date}`} />
        <StatTile
          label="People Counted"
          value={peopleCount ? peopleCount.total_in + peopleCount.total_out : '—'}
          sub={peopleCount ? `${peopleCount.total_in} in · ${peopleCount.total_out} out` : `on ${date}`}
        />
        <StatTile label="Busiest Hour" value={busiestHour ?? '—'} sub="by first-seen visits" />
        <StatTile
          label="Top Camera"
          value={topCamera ? topCamera.count : '—'}
          sub={topCamera ? topCamera.camera_name : 'no visits yet'}
        />
        <StatTile label="Cameras Reporting" value={report ? report.by_camera.length : '—'} sub="with at least one visit" />
      </div>

      <div className="footfall-charts-grid">
        <div className="card panel">
          <div className="panel-header">
            <h3>Footfall by Hour</h3>
          </div>
          {report ? <HourlyBarChart hourly={report.hourly} /> : <div className="empty-state">Loading…</div>}
        </div>

        <div className="card panel">
          <div className="panel-header">
            <h3>Footfall by Camera</h3>
          </div>
          {report ? <CameraBarChart byCamera={report.by_camera} /> : <div className="empty-state">Loading…</div>}
        </div>
      </div>

      <div className="card" style={{ marginTop: '1rem' }}>
        <table>
          <thead>
            <tr>
              <th>Person</th>
              <th>First Seen</th>
              <th>Camera</th>
              <th>Last Seen</th>
            </tr>
          </thead>
          <tbody>
            {!report || report.visits.length === 0 ? (
              <tr>
                <td colSpan={4} className="empty-state">
                  No unique visits recorded for {date}.
                </td>
              </tr>
            ) : (
              report.visits.map((v) => (
                <tr key={v.person_key}>
                  <td>{v.name || 'Unknown'}</td>
                  <td>{formatDateTime(v.first_seen)}</td>
                  <td>{v.camera_name}</td>
                  <td>{formatDateTime(v.last_seen)}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
