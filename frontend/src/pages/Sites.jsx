import { Fragment, useEffect, useState } from 'react'
import { api } from '../api'
import './pages.css'

function AddSiteModal({ onClose, onSaved }) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [status, setStatus] = useState(null)

  const handleSubmit = async (e) => {
    e.preventDefault()
    setStatus({ loading: true })
    try {
      await api.createSite({ name, description })
      onSaved()
      onClose()
    } catch (err) {
      setStatus({ loading: false, error: err.message })
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <form className="card modal" onClick={(e) => e.stopPropagation()} onSubmit={handleSubmit}>
        <h3>Add Site</h3>
        <div className="form-row">
          <label>Name</label>
          <input value={name} onChange={(e) => setName(e.target.value)} required />
        </div>
        <div className="form-row">
          <label>Description</label>
          <input value={description} onChange={(e) => setDescription(e.target.value)} />
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

export default function Sites() {
  const [sites, setSites] = useState([])
  const [expanded, setExpanded] = useState(null)
  const [showModal, setShowModal] = useState(false)

  const load = () => {
    api.listSites().then(setSites).catch(() => {})
  }
  useEffect(load, [])

  const toggle = (id) => setExpanded(expanded === id ? null : id)

  return (
    <div>
      <div className="page-toolbar">
        <div>
          <h2>Sites</h2>
          <div className="page-toolbar-sub">Manage sites for realtime monitoring</div>
        </div>
        <button className="btn btn-primary" onClick={() => setShowModal(true)}>
          + Add Site
        </button>
      </div>

      <div className="card">
        <table>
          <thead>
            <tr>
              <th></th>
              <th>Name</th>
              <th>Cameras</th>
              <th>Active</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {sites.map((site) => (
              <Fragment key={site.id}>
                <tr
                  className="site-row"
                  onClick={() => toggle(site.id)}
                >
                  <td className="site-expand-arrow">{expanded === site.id ? '▾' : '▸'}</td>
                  <td>{site.name}</td>
                  <td>{site.cameras.length}</td>
                  <td>{site.active_count}</td>
                  <td>
                    <span className="pill pill-success">Active</span>
                  </td>
                </tr>
                {expanded === site.id && (
                  <tr>
                    <td colSpan={5} className="site-cameras-cell">
                      {site.cameras.length === 0 ? (
                        <div className="empty-state">No cameras assigned to this site yet.</div>
                      ) : (
                        <table className="site-cameras-table">
                          <thead>
                            <tr>
                              <th>Camera</th>
                              <th>Cam Code</th>
                              <th>Status</th>
                              <th>Live</th>
                            </tr>
                          </thead>
                          <tbody>
                            {site.cameras.map((cam) => (
                              <tr key={cam.id}>
                                <td>{cam.name}</td>
                                <td>{cam.cam_code}</td>
                                <td>
                                  <span className={`pill ${cam.status === 'active' ? 'pill-success' : 'pill-danger'}`}>
                                    {cam.status === 'active' ? 'Active' : 'Inactive'}
                                  </span>
                                </td>
                                <td>{cam.live ? 'On' : 'Off'}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      )}
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>

      {showModal && <AddSiteModal onClose={() => setShowModal(false)} onSaved={load} />}
    </div>
  )
}
