import { useEffect, useState } from 'react'
import { api } from '../api'
import './pages.css'

const API_BASE = 'http://127.0.0.1:8811'
const ADD_PERSON_URL = `${API_BASE}/api/people`

function AddPersonModal({ onClose, onAdded }) {
  const [name, setName] = useState('')
  const [file, setFile] = useState(null)
  const [status, setStatus] = useState(null)

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!name || !file) return
    setStatus({ loading: true })
    const form = new FormData()
    form.append('name', name)
    form.append('photo', file)
    try {
      const res = await fetch(ADD_PERSON_URL, { method: 'POST', body: form })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Request failed')
      setStatus({ loading: false, result: data })
      onAdded()
    } catch (err) {
      setStatus({ loading: false, error: err.message })
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <form className="card modal" onClick={(e) => e.stopPropagation()} onSubmit={handleSubmit}>
        <h3>Add Person</h3>
        <div className="form-row">
          <label>Name</label>
          <input value={name} onChange={(e) => setName(e.target.value)} required />
        </div>
        <div className="form-row">
          <label>Photo</label>
          <input type="file" accept="image/*" onChange={(e) => setFile(e.target.files[0])} required />
        </div>
        {status?.result && (
          <div className="form-message success">
            Enrolled locally.{' '}
            {Object.entries(status.result.devices || {}).map(([host, r]) => (
              <div key={host}>
                {host}: {r.synced ? 'synced to Allow List' : `sync failed (${r.error})`}
              </div>
            ))}
          </div>
        )}
        {status?.error && <div className="form-message error">{status.error}</div>}
        <div className="modal-actions">
          <button type="button" className="btn btn-outline" onClick={onClose}>
            Close
          </button>
          <button type="submit" className="btn btn-primary" disabled={status?.loading}>
            {status?.loading ? 'Adding...' : 'Add Person'}
          </button>
        </div>
      </form>
    </div>
  )
}

function PhotoViewerModal({ person, onClose }) {
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="card modal" onClick={(e) => e.stopPropagation()}>
        <h3>{person.name}</h3>
        <div className="photo-viewer-grid">
          {person.photo_urls.map((url) => (
            <img key={url} src={`${API_BASE}${url}`} alt={person.name} className="photo-viewer-img" />
          ))}
        </div>
        <div className="modal-actions">
          <button type="button" className="btn btn-outline" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  )
}

export default function People() {
  const [faces, setFaces] = useState([])
  const [showModal, setShowModal] = useState(false)
  const [viewingPerson, setViewingPerson] = useState(null)
  const [deleteNote, setDeleteNote] = useState(null)

  const load = () => {
    api.listFaces().then(setFaces).catch(() => {})
  }

  useEffect(() => {
    load()
  }, [])

  const handleDelete = async (name) => {
    if (!confirm(`Remove ${name} from local face recognition?`)) return
    const result = await api.deletePerson(name)
    setDeleteNote(result.note)
    load()
  }

  return (
    <div>
      <div className="page-toolbar">
        <div>
          <h2>People</h2>
          <div className="page-toolbar-sub">{faces.length} enrolled in the Allow List</div>
        </div>
        <button className="btn btn-primary" onClick={() => setShowModal(true)}>
          + Add Person
        </button>
      </div>

      {deleteNote && <div className="form-message" style={{ marginBottom: '0.75rem' }}>{deleteNote}</div>}

      <div className="card">
        <table>
          <thead>
            <tr>
              <th>Photo</th>
              <th>Name</th>
              <th>Face Enrollment</th>
              <th>Samples</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {faces.map((f) => (
              <tr key={f.name}>
                <td>
                  {f.photo_urls[0] && (
                    <img
                      src={`${API_BASE}${f.photo_urls[0]}`}
                      alt={f.name}
                      className="people-thumb"
                      onClick={() => setViewingPerson(f)}
                    />
                  )}
                </td>
                <td>{f.name}</td>
                <td>
                  <span className="pill pill-success">COMPLETED</span>
                </td>
                <td>{f.sample_count}</td>
                <td>
                  <button className="btn btn-outline" onClick={() => handleDelete(f.name)}>
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {showModal && (
        <AddPersonModal
          onClose={() => setShowModal(false)}
          onAdded={() => {
            load()
          }}
        />
      )}

      {viewingPerson && (
        <PhotoViewerModal person={viewingPerson} onClose={() => setViewingPerson(null)} />
      )}
    </div>
  )
}
