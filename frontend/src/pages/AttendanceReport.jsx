import { useEffect, useState } from 'react'
import { api } from '../api'
import './pages.css'

function formatTime(ts) {
  return new Date(ts * 1000).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })
}

function todayStr() {
  return new Date().toISOString().slice(0, 10)
}

function daysAgoStr(n) {
  const d = new Date()
  d.setDate(d.getDate() - n)
  return d.toISOString().slice(0, 10)
}

export default function AttendanceReport() {
  const [people, setPeople] = useState([])
  const [name, setName] = useState('')
  const [start, setStart] = useState(daysAgoStr(30))
  const [end, setEnd] = useState(todayStr())
  const [rows, setRows] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    api.listFaces().then((faces) => {
      setPeople(faces)
      if (faces.length > 0) setName(faces[0].name)
    }).catch(() => {})
  }, [])

  const handleGenerate = async (e) => {
    e.preventDefault()
    if (!name) return
    setError(null)
    try {
      const data = await api.getAttendanceReport(name, start, end)
      setRows(data)
    } catch (err) {
      setError(err.message)
      setRows(null)
    }
  }

  return (
    <div>
      <div className="page-toolbar">
        <div>
          <h2>Attendance Report</h2>
          <div className="page-toolbar-sub">Detailed day-by-day attendance for one person, downloadable as CSV</div>
        </div>
      </div>

      <div className="card">
        <form
          onSubmit={handleGenerate}
          className="form-row"
          style={{ flexDirection: 'row', alignItems: 'center', gap: '0.6rem', flexWrap: 'wrap' }}
        >
          <select
            value={name}
            onChange={(e) => setName(e.target.value)}
            style={{ padding: '0.5rem 0.6rem', borderRadius: 6, border: '1px solid var(--border)' }}
          >
            {people.map((p) => (
              <option key={p.name} value={p.name}>{p.name}</option>
            ))}
          </select>
          <label style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>From</label>
          <input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
          <label style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>To</label>
          <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
          <button type="submit" className="btn btn-primary">Generate</button>
          {rows && rows.length > 0 && (
            <>
              <a className="btn btn-outline" href={api.attendanceReportPdfUrl(name, start, end)}>
                Download PDF
              </a>
              <a className="btn btn-outline" href={api.attendanceReportCsvUrl(name, start, end)}>
                Download CSV
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
      </div>
    </div>
  )
}
