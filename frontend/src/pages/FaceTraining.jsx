import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import './pages.css'

function formatTimestamp(ts) {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleString('en-IN', {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
  })
}

export default function FaceTraining() {
  const [sample, setSample] = useState(null)
  const [pendingCount, setPendingCount] = useState(0)
  const [status, setStatus] = useState(null)
  const [nameInput, setNameInput] = useState('')
  const [knownNames, setKnownNames] = useState([])
  const [message, setMessage] = useState(null) // {type: 'error'|'success', text}
  const [busy, setBusy] = useState(false)
  const [trainMessage, setTrainMessage] = useState(null)
  const inputRef = useRef(null)

  const loadNext = useCallback(() => {
    api.getNextFaceTrainingSample()
      .then((data) => {
        setSample(data.sample)
        setPendingCount(data.pending_count)
      })
      .catch((err) => setMessage({ type: 'error', text: err.message }))
  }, [])

  const loadStatus = useCallback(() => {
    api.getFaceTrainingStatus().then(setStatus).catch(() => {})
  }, [])

  useEffect(() => {
    loadNext()
    loadStatus()
    api.listFaces().then((people) => setKnownNames(people.map((p) => p.name))).catch(() => {})
    const interval = setInterval(loadStatus, 15000)
    return () => clearInterval(interval)
  }, [loadNext, loadStatus])

  useEffect(() => {
    // Keep focus on the name field so an operator can keep typing+Enter
    // through a whole backlog without reaching for the mouse — the "process
    // a large number of samples efficiently" requirement.
    inputRef.current?.focus()
  }, [sample])

  const handleLabel = async (e) => {
    e.preventDefault()
    if (!sample || !nameInput.trim() || busy) return
    setBusy(true)
    setMessage(null)
    try {
      await api.labelFaceTrainingSample(sample.id, nameInput.trim())
      setNameInput('')
      setMessage({ type: 'success', text: `Labeled as "${nameInput.trim()}"` })
      loadNext()
      loadStatus()
    } catch (err) {
      setMessage({ type: 'error', text: err.message })
    } finally {
      setBusy(false)
    }
  }

  const handleSkip = async () => {
    if (!sample || busy) return
    setBusy(true)
    setMessage(null)
    try {
      await api.skipFaceTrainingSample(sample.id)
      setNameInput('')
      loadNext()
      loadStatus()
    } catch (err) {
      setMessage({ type: 'error', text: err.message })
    } finally {
      setBusy(false)
    }
  }

  const handleTrainNow = async () => {
    setTrainMessage('Running…')
    try {
      const result = await api.triggerFaceTraining()
      if (result.status === 'success') {
        setTrainMessage(
          `Promoted ${result.samples_promoted}, rejected ${result.samples_rejected}. ` +
          (result.validation_accuracy != null
            ? `Validation accuracy: ${(result.validation_accuracy * 100).toFixed(1)}% (${result.validation_samples} held-out samples).`
            : 'Not enough enrolled samples yet for a validation measurement.')
        )
      } else {
        setTrainMessage('Nothing to promote — no new labeled samples waiting.')
      }
      loadStatus()
    } catch (err) {
      setTrainMessage(`Failed: ${err.message}`)
    }
  }

  const lastRun = status?.last_training_run

  return (
    <div>
      <div className="page-toolbar">
        <div>
          <h2>Face Training</h2>
          <div className="page-toolbar-sub">
            Faces the live cameras couldn't recognize are collected automatically in the background — no
            separate capture step needed. Label them below to teach the recognizer; newly labeled samples are
            folded into recognition automatically in the background once enough accumulate, or immediately with
            "Train now".
          </div>
        </div>
      </div>

      <div className="dashboard-main-grid">
        <div className="card panel" style={{ gridColumn: '1 / -1' }}>
          <div className="panel-header">
            <h3>Stat Overview</h3>
          </div>
          {status && (
            <div className="stat-grid">
              <div className="stat-tile card">
                <div className="stat-tile-label">Pending samples</div>
                <div className="stat-tile-value">{status.pending_samples}</div>
              </div>
              <div className="stat-tile card">
                <div className="stat-tile-label">Captured today</div>
                <div className="stat-tile-value">{status.samples_captured_today}</div>
              </div>
              <div className="stat-tile card">
                <div className="stat-tile-label">Labeled today</div>
                <div className="stat-tile-value">{status.samples_labeled_today}</div>
              </div>
              <div className="stat-tile card">
                <div className="stat-tile-label">Awaiting next training run</div>
                <div className="stat-tile-value">{status.labeled_awaiting_promotion}</div>
              </div>
              <div className="stat-tile card">
                <div className="stat-tile-label">Enrolled people</div>
                <div className="stat-tile-value">{status.total_enrolled_people}</div>
              </div>
              <div className="stat-tile card">
                <div className="stat-tile-label">Enrolled samples</div>
                <div className="stat-tile-value">{status.total_enrolled_samples}</div>
              </div>
            </div>
          )}
          <div style={{ marginTop: '0.75rem', display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <button className="btn btn-outline" onClick={handleTrainNow}>Train now</button>
            {trainMessage && <span className="stat-tile-sub">{trainMessage}</span>}
          </div>
          {lastRun && (
            <div className="stat-tile-sub" style={{ marginTop: '0.5rem' }}>
              Last run: {formatTimestamp(lastRun.trained_at)} — promoted {lastRun.samples_promoted}, rejected{' '}
              {lastRun.samples_rejected}.{' '}
              {lastRun.validation_accuracy != null
                ? `Validation accuracy ${(lastRun.validation_accuracy * 100).toFixed(1)}% on ${lastRun.validation_samples} held-out samples (NOT a live-camera accuracy measurement — see note below).`
                : 'Not enough enrolled samples per person yet for a validation measurement.'}
            </div>
          )}
        </div>

        <div className="card panel" style={{ gridColumn: '1 / -1' }}>
          <div className="panel-header">
            <h3>Label pending sample {pendingCount > 0 && `(${pendingCount} waiting)`}</h3>
          </div>

          {!sample ? (
            <div className="empty-state">
              All caught up — no pending samples right now. New ones appear automatically as the live cameras
              see faces they don't recognize.
            </div>
          ) : (
            <div style={{ display: 'flex', gap: '1.5rem', flexWrap: 'wrap', alignItems: 'flex-start' }}>
              <img
                src={api.faceTrainingImageUrl(sample.id)}
                alt="Pending face sample"
                style={{ width: 240, height: 240, objectFit: 'cover', borderRadius: 8, background: '#111' }}
              />
              <div style={{ flex: 1, minWidth: 260 }}>
                <div className="stat-tile-sub" style={{ marginBottom: '0.5rem' }}>
                  Camera {sample.camera_id ?? '—'} · captured {formatTimestamp(sample.captured_at)}
                </div>
                <form onSubmit={handleLabel}>
                  <input
                    ref={inputRef}
                    type="text"
                    list="face-training-known-names"
                    placeholder="Employee name (existing or new)"
                    value={nameInput}
                    onChange={(e) => setNameInput(e.target.value)}
                    autoComplete="off"
                    disabled={busy}
                  />
                  <datalist id="face-training-known-names">
                    {knownNames.map((n) => <option key={n} value={n} />)}
                  </datalist>
                  <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.75rem' }}>
                    <button type="submit" className="btn btn-primary" disabled={busy || !nameInput.trim()}>
                      Label (Enter)
                    </button>
                    <button type="button" className="btn btn-outline" onClick={handleSkip} disabled={busy}>
                      Skip
                    </button>
                  </div>
                </form>
                {message && (
                  <div className={`form-message ${message.type}`} style={{ marginTop: '0.5rem' }}>
                    {message.text}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>

        <div className="card panel" style={{ gridColumn: '1 / -1' }}>
          <div className="panel-header">
            <h3>About these numbers</h3>
          </div>
          <div className="stat-tile-sub">
            Validation accuracy is measured by holding out each enrolled sample and checking whether the
            REMAINING enrolled samples still correctly match it — a real, measured number grounded in the same
            matching this app uses live, not an invented percentage. It is NOT the same as real-world accuracy
            on live camera footage (lighting, angle, motion, and crop quality all differ from a held-out still
            sample), and it is only computed once at least one enrolled person has 2+ samples. Collection and
            labeling continue regardless of what this number reaches — there is no automatic stopping point.
          </div>
        </div>
      </div>
    </div>
  )
}
