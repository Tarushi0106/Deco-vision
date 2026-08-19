import { useEffect, useState } from 'react'
import { api } from '../api'
import './pages.css'

function formatTime(ts) {
  return new Date(ts * 1000).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })
}

export default function Attendance() {
  const [rows, setRows] = useState([])

  useEffect(() => {
    api.getAttendance().then(setRows).catch(() => {})
  }, [])

  return (
    <div>
      <div className="page-toolbar">
        <div>
          <h2>Attendance</h2>
          <div className="page-toolbar-sub">First/last recognized sighting per person, today</div>
        </div>
      </div>

      <div className="card">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>First Seen</th>
              <th>Last Seen</th>
              <th>Cameras</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={4} className="empty-state">
                  No recognized sightings yet today.
                </td>
              </tr>
            ) : (
              rows.map((row) => (
                <tr key={row.name}>
                  <td>{row.name}</td>
                  <td>{formatTime(row.first_seen)}</td>
                  <td>{formatTime(row.last_seen)}</td>
                  <td>{row.cameras}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
