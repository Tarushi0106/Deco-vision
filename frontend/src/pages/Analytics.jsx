import { useEffect, useState } from 'react'
import { api } from '../api'
import './pages.css'

const SPARKLINE_WIDTH = 168
const SPARKLINE_HEIGHT = 28
const BAR_GAP = 2
const BAR_COUNT = 24

function HourlySparkline({ hourly }) {
  const max = Math.max(...hourly, 1)
  const barWidth = (SPARKLINE_WIDTH - BAR_GAP * (BAR_COUNT - 1)) / BAR_COUNT

  return (
    <svg width={SPARKLINE_WIDTH} height={SPARKLINE_HEIGHT} role="img" aria-label="Detections by hour of day">
      {hourly.map((count, hour) => {
        const barHeight = Math.max((count / max) * (SPARKLINE_HEIGHT - 2), count > 0 ? 2 : 0.5)
        const x = hour * (barWidth + BAR_GAP)
        const y = SPARKLINE_HEIGHT - barHeight
        return (
          <rect
            key={hour}
            x={x}
            y={y}
            width={barWidth}
            height={barHeight}
            rx={1.5}
            fill="var(--brand)"
            opacity={count > 0 ? 0.85 : 0.15}
          >
            <title>{`${hour}:00 - ${count} detection${count === 1 ? '' : 's'}`}</title>
          </rect>
        )
      })}
    </svg>
  )
}

function formatTimestamp(ts) {
  return new Date(ts * 1000).toLocaleString('en-IN', {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export default function Analytics() {
  const [days, setDays] = useState(7)
  const [rows, setRows] = useState([])

  useEffect(() => {
    api.getPeopleAnalytics(days).then(setRows).catch(() => {})
  }, [days])

  return (
    <div>
      <div className="page-toolbar">
        <div>
          <h2>Face Recognition Analytics</h2>
          <div className="page-toolbar-sub">Per-person visit patterns from recognized sightings</div>
        </div>
        <select value={days} onChange={(e) => setDays(Number(e.target.value))} className="btn btn-outline">
          <option value={1}>Last 24 hours</option>
          <option value={7}>Last 7 days</option>
          <option value={30}>Last 30 days</option>
        </select>
      </div>

      <div className="card">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Total Detections</th>
              <th>Days Seen</th>
              <th>Most Seen At</th>
              <th>Last Seen</th>
              <th>Hourly Pattern</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={6} className="empty-state">
                  No recognized sightings in this period yet.
                </td>
              </tr>
            ) : (
              rows.map((row) => (
                <tr key={row.name}>
                  <td>{row.name}</td>
                  <td>{row.total_detections}</td>
                  <td>{row.days_seen}</td>
                  <td>{row.top_camera_name}</td>
                  <td>{formatTimestamp(row.last_seen)}</td>
                  <td>
                    <HourlySparkline hourly={row.hourly} />
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
