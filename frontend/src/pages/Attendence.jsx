import React, { useEffect, useState } from 'react';

export default function Attendance() {
  const [attendance, setAttendance] = useState([]);

  useEffect(() => {
    fetch('http://localhost:8000/api/attendance')
      .then((res) => res.json())
      .then((data) => setAttendance(data))
      .catch((err) => console.error('Error fetching attendance:', err));
  }, []);

  return (
    <div style={{ padding: '24px' }}>
      <h2>Visitor Attendance Records</h2>
      <div style={{ marginTop: '20px', background: 'white', padding: '15px', borderRadius: '8px', boxShadow: '0 2px 4px rgba(0,0,0,0.1)' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ borderBottom: '2px solid #eee', textAlign: 'left' }}>
              <th style={{ padding: '10px' }}>ID</th>
              <th style={{ padding: '10px' }}>Visitor Name</th>
              <th style={{ padding: '10px' }}>Entry Time</th>
              <th style={{ padding: '10px' }}>Status</th>
            </tr>
          </thead>
          <tbody>
            {attendance.length > 0 ? (
              attendance.map((record) => (
                <tr key={record.id} style={{ borderBottom: '1px solid #eee' }}>
                  <td style={{ padding: '10px' }}>{record.id}</td>
                  <td style={{ padding: '10px' }}>{record.visitor_name}</td>
                  <td style={{ padding: '10px' }}>{record.entry_time}</td>
                  <td style={{ padding: '10px' }}>
                    <span style={{ padding: '4px 8px', background: '#e6f4ea', color: '#137333', borderRadius: '4px', fontSize: '12px' }}>
                      {record.status}
                    </span>
                  </td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan="4" style={{ padding: '20px', textAlign: 'center', color: '#666' }}>
                  No attendance records found.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}