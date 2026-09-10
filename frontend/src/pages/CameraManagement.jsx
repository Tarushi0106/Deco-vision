import { useEffect, useState } from 'react'
import { api } from '../api'
import './pages.css'

const BLANK = {
  name: '',
  site: '',
  cam_code: '',
  purpose: 'GENERAL',
  host: '',
  port: 554,
  user: '',
  password: '',
  stream_path: '/h264/ch1/sub/av_stream',
  live_feed_enabled: true,
}

function CameraFormModal({ initial, onClose, onSaved }) {
  const [form, setForm] = useState(initial ? { ...BLANK, ...initial, password: '' } : BLANK)
  const [status, setStatus] = useState(null)
  const isEdit = Boolean(initial?.id)

  const set = (key) => (e) => setForm({ ...form, [key]: e.target.value })

  const handleSubmit = async (e) => {
    e.preventDefault()
    setStatus({ loading: true })
    try {
      const payload = { ...form }
      // never sent back by the API (security) — an empty field here means
      // "unchanged", not "clear the password", so drop it unless retyped
      if (isEdit && !payload.password) {
        delete payload.password
      }
      if (isEdit) {
        await api.updateCamera(initial.id, payload)
      } else {
        await api.createCamera(payload)
      }
      onSaved()
      onClose()
    } catch (err) {
      setStatus({ loading: false, error: err.message })
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <form className="card modal" onClick={(e) => e.stopPropagation()} onSubmit={handleSubmit}>
        <h3>{isEdit ? 'Edit Camera' : 'Add Camera'}</h3>
        <div className="form-row">
          <label>Name</label>
          <input value={form.name} onChange={set('name')} required />
        </div>
        <div className="form-row">
          <label>Cam Code</label>
          <input value={form.cam_code} onChange={set('cam_code')} placeholder="e.g. CAM-008" />
        </div>
        <div className="form-row">
          <label>Site</label>
          <input value={form.site} onChange={set('site')} required />
        </div>
        <div className="form-row">
          <label>Host / IP</label>
          <input value={form.host} onChange={set('host')} placeholder="e.g. 192.168.1.50" />
        </div>
        <div className="form-row">
          <label>RTSP Port</label>
          <input type="number" value={form.port} onChange={set('port')} />
        </div>
        <div className="form-row">
          <label>Username</label>
          <input value={form.user} onChange={set('user')} />
        </div>
        <div className="form-row">
          <label>Password</label>
          <input
            type="password"
            value={form.password}
            onChange={set('password')}
            placeholder={isEdit ? 'Leave blank to keep existing password' : ''}
          />
        </div>
        <div className="form-row">
          <label>Stream Path</label>
          <input value={form.stream_path} onChange={set('stream_path')} />
        </div>
        <div className="form-row">
          <label>Live Feed Enabled</label>
          <input
            type="checkbox"
            checked={Boolean(form.live_feed_enabled)}
            onChange={(e) => setForm({ ...form, live_feed_enabled: e.target.checked })}
          />
        </div>
        {status?.error && <div className="form-message error">{status.error}</div>}
        <div className="modal-actions">
          <button type="button" className="btn btn-outline" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={status?.loading}>
            Save
          </button>
        </div>
      </form>
    </div>
  )
}

export default function CameraManagement() {
  const [cameras, setCameras] = useState([])
  const [editing, setEditing] = useState(null)
  const [showModal, setShowModal] = useState(false)

  const load = () => {
    api.listCameras().then(setCameras).catch(() => {})
  }
  useEffect(() => {
    load()
  }, [])

  const handleDelete = async (id) => {
    await api.deleteCamera(id)
    load()
  }

  return (
    <div>
      <div className="page-toolbar">
        <div>
          <h2>Cameras Management</h2>
          <div className="page-toolbar-sub">Manage cameras for realtime monitoring</div>
        </div>
        <button
          className="btn btn-primary"
          onClick={() => {
            setEditing(null)
            setShowModal(true)
          }}
        >
          + Add Camera
        </button>
      </div>

      <div className="card">
        <table>
          <thead>
            <tr>
              <th>Cam Code</th>
              <th>Label</th>
              <th>Site</th>
              <th>Purpose</th>
              <th>Status</th>
              <th>Live Feed</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {cameras.map((cam) => (
              <tr key={cam.id}>
                <td>{cam.cam_code}</td>
                <td>{cam.name}</td>
                <td>{cam.site}</td>
                <td>{cam.purpose}</td>
                <td>
                  <span className={`pill ${cam.status === 'active' ? 'pill-success' : 'pill-danger'}`}>
                    {cam.status === 'active' ? 'Active' : 'Inactive'}
                  </span>
                </td>
                <td>{cam.live ? 'On' : 'Off'}</td>
                <td>
                  <button
                    className="btn btn-outline"
                    style={{ marginRight: '0.4rem' }}
                    onClick={() => {
                      setEditing(cam)
                      setShowModal(true)
                    }}
                  >
                    Edit
                  </button>
                  <button className="btn btn-outline" onClick={() => handleDelete(cam.id)}>
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {showModal && (
        <CameraFormModal initial={editing} onClose={() => setShowModal(false)} onSaved={load} />
      )}
    </div>
  )
}
