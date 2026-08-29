import { useEffect, useState } from 'react'
import { api, API_BASE } from '../api'
import './pages.css'

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

function EditPersonModal({ person, onClose, onSaved }) {
  const [name, setName] = useState(person.name)
  const [file, setFile] = useState(null)
  const [status, setStatus] = useState(null)

  const handleSubmit = async (e) => {
    e.preventDefault()
    const newName = name.trim()
    if (!newName) return
    setStatus({ loading: true })
    try {
      // rename first (if changed) so the added photo below lands under the new name
      if (newName !== person.name) {
        await api.renamePerson(person.name, newName)
      }
      if (file) {
        const form = new FormData()
        form.append('name', newName)
        form.append('photo', file)
        const res = await fetch(ADD_PERSON_URL, { method: 'POST', body: form })
        const data = await res.json()
        if (!res.ok) throw new Error(data.detail || 'Request failed')
      }
      setStatus({ loading: false, done: true })
      onSaved()
    } catch (err) {
      setStatus({ loading: false, error: err.message })
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <form className="card modal" onClick={(e) => e.stopPropagation()} onSubmit={handleSubmit}>
        <h3>Edit {person.name}</h3>
        <div className="form-row">
          <label>Name</label>
          <input value={name} onChange={(e) => setName(e.target.value)} required />
        </div>
        <div className="form-row">
          <label>Add a photo</label>
          <input type="file" accept="image/*" onChange={(e) => setFile(e.target.files[0])} />
        </div>
        <div className="stat-tile-sub">
          {person.sample_count} sample{person.sample_count === 1 ? '' : 's'} enrolled. Adding a photo keeps the
          existing ones and improves match accuracy — it doesn't replace them.
        </div>
        {status?.done && <div className="form-message success">Saved.</div>}
        {status?.error && <div className="form-message error">{status.error}</div>}
        <div className="modal-actions">
          <button type="button" className="btn btn-outline" onClick={onClose}>
            Close
          </button>
          <button type="submit" className="btn btn-primary" disabled={status?.loading}>
            {status?.loading ? 'Saving...' : 'Save'}
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
  const [editingPerson, setEditingPerson] = useState(null)
  const [deleteNote, setDeleteNote] = useState(null)
  const [syncing, setSyncing] = useState(false)
  const [syncResult, setSyncResult] = useState(null)

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

  const handleSyncFromCamera = async () => {
    setSyncing(true)
    setSyncResult(null)
    try {
      const result = await api.syncPeopleFromCamera()
      setSyncResult(result)
      load()
    } catch (err) {
      setSyncResult({ error: err.message })
    } finally {
      setSyncing(false)
    }
  }

  return (
    <div>
      <div className="page-toolbar">
        <div>
          <h2>People</h2>
          <div className="page-toolbar-sub">{faces.length} enrolled in the Allow List</div>
        </div>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <button className="btn btn-outline" onClick={handleSyncFromCamera} disabled={syncing}>
            {syncing ? 'Syncing...' : 'Sync from Camera'}
          </button>
          <button className="btn btn-primary" onClick={() => setShowModal(true)}>
            + Add Person
          </button>
        </div>
      </div>

      {syncResult && !syncResult.error && (
        <div className="form-message" style={{ marginBottom: '0.75rem' }}>
          Synced {syncResult.synced}, already up to date {syncResult.skipped}
          {syncResult.failed.length > 0 && `, ${syncResult.failed.length} failed`}.
          {syncResult.failed.length > 0 && (
            <ul>
              {syncResult.failed.map((f, i) => (
                <li key={i}>{f.name || '(unknown)'}: {f.error}</li>
              ))}
            </ul>
          )}
        </div>
      )}
      {syncResult?.error && (
        <div className="form-message error" style={{ marginBottom: '0.75rem' }}>{syncResult.error}</div>
      )}

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
                <td style={{ display: 'flex', gap: '0.5rem' }}>
                  <button className="btn btn-outline" onClick={() => setEditingPerson(f)}>
                    Edit
                  </button>
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

      {editingPerson && (
        <EditPersonModal
          person={editingPerson}
          onClose={() => setEditingPerson(null)}
          onSaved={() => {
            setEditingPerson(null)
            load()
          }}
        />
      )}
    </div>
  )
}
