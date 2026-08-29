import { useEffect, useMemo, useState } from 'react'
import { api, API_BASE } from '../api'
import './pages.css'
import './attendance.css'

const PAGE_SIZE_OPTIONS = [10, 25, 50]
const MAX_PAGE_BUTTONS = 6

function todayStr() {
  return new Date().toISOString().slice(0, 10)
}

function formatTime(ts) {
  return new Date(ts * 1000).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })
}

function formatDuration(seconds) {
  const h = Math.floor(seconds / 3600)
  const m = Math.round((seconds % 3600) / 60)
  return `${h}h ${String(m).padStart(2, '0')}m`
}

function initials(name) {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0].toUpperCase())
    .join('')
}

function StatTile({ label, value }) {
  return (
    <div className="stat-tile card">
      <div className="stat-tile-label">{label}</div>
      <div className="stat-tile-value">{value}</div>
    </div>
  )
}

function daysAgoStr(n) {
  const d = new Date()
  d.setDate(d.getDate() - n)
  return d.toISOString().slice(0, 10)
}

// Detailed day-by-day report for one person over a date range — folded in
// from the old standalone Attendance Report page so it's reachable directly
// from the row it's about, instead of a separate page with its own person
// picker.
function PersonReportModal({ name, onClose }) {
  const [start, setStart] = useState(daysAgoStr(30))
  const [end, setEnd] = useState(todayStr())
  const [rows, setRows] = useState(null)
  const [error, setError] = useState(null)

  const handleGenerate = async (e) => {
    e.preventDefault()
    setError(null)
    try {
      const data = await api.getAttendanceReport(name, start, end)
      setRows(data)
    } catch (err) {
      setError(err.message)
      setRows(null)
    }
  }

  useEffect(() => {
    handleGenerate({ preventDefault: () => {} })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="card modal attendance-report-modal" onClick={(e) => e.stopPropagation()}>
        <h3>{name} — Attendance Report</h3>
        <form
          onSubmit={handleGenerate}
          className="form-row"
          style={{ flexDirection: 'row', alignItems: 'center', gap: '0.6rem', flexWrap: 'wrap' }}
        >
          <label style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>From</label>
          <input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
          <label style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>To</label>
          <input type="date" value={end} max={todayStr()} onChange={(e) => setEnd(e.target.value)} />
          <button type="submit" className="btn btn-primary">Generate</button>
          {rows && rows.length > 0 && (
            <>
              <a className="btn btn-outline" href={api.attendanceReportPdfUrl(name, start, end)}>
                Download PDF
              </a>
              <a className="btn btn-outline" href={api.attendanceReportXlsxUrl(name, start, end)}>
                Download Excel
              </a>
            </>
          )}
        </form>

        {error && <div className="form-message error" style={{ marginTop: '0.75rem' }}>{error}</div>}

        {rows !== null && (
          <table style={{ marginTop: '1rem' }}>
            <thead>
              <tr>
                <th>Date</th>
                <th>First Seen</th>
                <th>Last Seen</th>
                <th>Total Detections</th>
                <th>Cameras</th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 ? (
                <tr>
                  <td colSpan={5} className="empty-state">
                    No sightings for {name} in this date range.
                  </td>
                </tr>
              ) : (
                rows.map((row) => (
                  <tr key={row.date}>
                    <td>{row.date}</td>
                    <td>{formatTime(row.first_seen)}</td>
                    <td>{formatTime(row.last_seen)}</td>
                    <td>{row.total_detections}</td>
                    <td>{row.camera_names.join(', ')}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        )}

        <div className="modal-actions">
          <button type="button" className="btn btn-outline" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  )
}

export default function Attendance() {
  const [date, setDate] = useState(todayStr())
  const [report, setReport] = useState(null)
  const [photoByName, setPhotoByName] = useState({})
  const [statusFilter, setStatusFilter] = useState('all')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)
  const [error, setError] = useState(null)
  const [lastUpdated, setLastUpdated] = useState(null)
  const [reportPerson, setReportPerson] = useState(null)

  const load = () => {
    setError(null)
    api.getAttendance(date)
      .then((data) => {
        setReport(data)
        setLastUpdated(new Date())
      })
      .catch((err) => setError(err.message))
  }

  useEffect(load, [date])

  useEffect(() => {
    api.listFaces().then((faces) => {
      const map = {}
      faces.forEach((f) => {
        if (f.photo_urls?.[0]) map[f.name] = f.photo_urls[0]
      })
      setPhotoByName(map)
    }).catch(() => {})
  }, [])

  useEffect(() => {
    setPage(1)
  }, [statusFilter, date, pageSize])

  const filteredRoster = useMemo(() => {
    if (!report) return []
    if (statusFilter === 'present') return report.roster.filter((r) => r.present)
    if (statusFilter === 'absent') return report.roster.filter((r) => !r.present)
    return report.roster
  }, [report, statusFilter])

  const totalPages = Math.max(1, Math.ceil(filteredRoster.length / pageSize))
  const pageRows = filteredRoster.slice((page - 1) * pageSize, page * pageSize)
  const pageButtons = Array.from({ length: Math.min(totalPages, MAX_PAGE_BUTTONS) }, (_, i) => i + 1)

  return (
    <div>
      <div className="card attendance-header">
        <div className="attendance-header-left">
          <div className="attendance-header-icon">📅</div>
          <div>
            <div className="attendance-eyebrow">Customer-wide Attendance</div>
            <h2>Attendance</h2>
            <div className="page-toolbar-sub">
              One attendance report generated from face recognition across every mapped camera.
            </div>
            {lastUpdated && (
              <div className="attendance-updated">
                <span className="dot dot-success" />
                Last updated {lastUpdated.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })}
              </div>
            )}
          </div>
        </div>

        <div className="attendance-header-actions">
          <div className="attendance-date-field">
            <label>Attendance Date</label>
            <input type="date" value={date} max={todayStr()} onChange={(e) => setDate(e.target.value)} />
          </div>
          {report && report.roster.length > 0 && (
            <>
              <a className="btn btn-outline" href={`${API_BASE}/api/attendance/daily/xlsx?date=${date}`}>
                Excel
              </a>
              <a className="btn btn-outline" href={`${API_BASE}/api/attendance/daily/pdf?date=${date}`}>
                PDF
              </a>
            </>
          )}
          <button className="btn btn-primary" onClick={load}>
            Refresh
          </button>
        </div>
      </div>

      {error && <div className="form-message error">{error}</div>}

      {report && (
        <div className="attendance-banner">
          <span>Face recognition across all mapped cameras</span>
          <span>
            {report.present} present · {report.absent} absent
          </span>
        </div>
      )}

      <div className="stat-grid attendance-stat-grid">
        <StatTile label="Present" value={report ? report.present : '—'} />
        <StatTile label="Absent" value={report ? report.absent : '—'} />
        <StatTile label="Total Detections" value={report ? report.total_detections : '—'} />
        <StatTile label="Camera Scope" value={report ? `All mapped (${report.camera_scope})` : '—'} />
        <StatTile label="Date" value={date} />
      </div>

      <div className="attendance-filters">
        <label>Status</label>
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="all">All employees</option>
          <option value="present">Present</option>
          <option value="absent">Absent</option>
        </select>
      </div>

      <div className="card">
        <div className="attendance-toolbar-row">
          <span>
            {filteredRoster.length === 0
              ? 'No records'
              : `Showing ${(page - 1) * pageSize + 1}-${Math.min(page * pageSize, filteredRoster.length)} of ${filteredRoster.length} total records`}
          </span>
          <div className="attendance-pagination">
            <span>Records per page</span>
            <select value={pageSize} onChange={(e) => setPageSize(Number(e.target.value))}>
              {PAGE_SIZE_OPTIONS.map((n) => (
                <option key={n} value={n}>{n}</option>
              ))}
            </select>
            <div className="attendance-page-buttons">
              <button
                type="button"
                className="attendance-page-btn"
                disabled={page === 1}
                onClick={() => setPage((p) => p - 1)}
              >
                ‹
              </button>
              {pageButtons.map((p) => (
                <button
                  type="button"
                  key={p}
                  className={`attendance-page-btn${p === page ? ' attendance-page-btn-active' : ''}`}
                  onClick={() => setPage(p)}
                >
                  {p}
                </button>
              ))}
              <button
                type="button"
                className="attendance-page-btn"
                disabled={page === totalPages}
                onClick={() => setPage((p) => p + 1)}
              >
                ›
              </button>
            </div>
          </div>
        </div>

        <div className="attendance-table-wrap">
          <table>
            <thead>
              <tr>
                <th>Employee</th>
                <th>ID Code</th>
                <th>Check-In</th>
                <th>Check-Out</th>
                <th>Check-Out Camera</th>
                <th>Time Stay</th>
                <th>Detections</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {pageRows.length === 0 ? (
                <tr>
                  <td colSpan={9} className="empty-state">
                    {report ? 'No employees enrolled yet.' : 'Loading…'}
                  </td>
                </tr>
              ) : (
                pageRows.map((r) => (
                  <tr key={r.name} className={r.present ? '' : 'attendance-row-absent'}>
                    <td>
                      <div className="attendance-employee-cell">
                        {photoByName[r.name] ? (
                          <img src={`${API_BASE}${photoByName[r.name]}`} alt={r.name} className="attendance-avatar" />
                        ) : (
                          <span className="attendance-avatar-fallback">{initials(r.name)}</span>
                        )}
                        <span>{r.name}</span>
                      </div>
                    </td>
                    <td>{r.employee_id || '—'}</td>
                    <td>{r.check_in ? formatTime(r.check_in) : '—'}</td>
                    <td>{r.check_out ? formatTime(r.check_out) : '—'}</td>
                    <td>{r.checkout_camera_name || '—'}</td>
                    <td>{r.time_stay_seconds != null ? formatDuration(r.time_stay_seconds) : '—'}</td>
                    <td>{r.detections}</td>
                    <td>
                      <span className={`pill ${r.present ? 'pill-success' : 'pill-danger'}`}>
                        {r.present ? 'Present' : 'Absent'}
                      </span>
                    </td>
                    <td>
                      <div className="attendance-row-actions">
                        <button type="button" className="btn btn-outline" onClick={() => setReportPerson(r.name)}>
                          Report
                        </button>
                        <a
                          className="btn btn-outline"
                          title={`Download ${r.name}'s attendance (last 30 days) as PDF`}
                          href={api.attendanceReportPdfUrl(r.name, daysAgoStr(30), todayStr())}
                        >
                          PDF
                        </a>
                        <a
                          className="btn btn-outline"
                          title={`Download ${r.name}'s attendance (last 30 days) as Excel`}
                          href={api.attendanceReportXlsxUrl(r.name, daysAgoStr(30), todayStr())}
                        >
                          Excel
                        </a>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {reportPerson && <PersonReportModal name={reportPerson} onClose={() => setReportPerson(null)} />}
    </div>
  )
}
