import { useCallback, useEffect, useRef, useState } from 'react'
import { detectionApi } from '../api'

function formatTimestamp(ts) {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleString()
}

export default function FaceTraining() {
  const [sample, setSample] = useState(null)
  const [pendingCount, setPendingCount] = useState(0)
  const [status, setStatus] = useState(null)
  const [nameInput, setNameInput] = useState('')
  const [message, setMessage] = useState(null)
  const [busy, setBusy] = useState(false)
  const [trainMessage, setTrainMessage] = useState(null)
  const inputRef = useRef(null)

  const loadNext = useCallback(() => {
    detectionApi.getNextSample()
      .then((data) => {
        setSample(data.sample)
        setPendingCount(data.pending_count)
      })
      .catch((err) => setMessage({ type: 'error', text: err.message }))
  }, [])

  const loadStatus = useCallback(() => {
    detectionApi.getStatus().then(setStatus).catch(() => {})
  }, [])

  useEffect(() => {
    loadNext()
    loadStatus()
    const interval = setInterval(loadStatus, 15000)
    return () => clearInterval(interval)
  }, [loadNext, loadStatus])

  useEffect(() => {
    inputRef.current?.focus()
  }, [sample])

  const handleLabel = async (e) => {
    e.preventDefault()
    if (!sample || !nameInput.trim() || busy) return
    setBusy(true)
    setMessage(null)
    try {
      await detectionApi.labelSample(sample.id, nameInput.trim())
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
    try {
      await detectionApi.skipSample(sample.id)
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
      const result = await detectionApi.trainNow()
      if (result.status === 'success') {
        setTrainMessage(
          `Promoted ${result.samples_promoted}, rejected ${result.samples_rejected}. ` +
          (result.validation_accuracy != null
            ? `Validation accuracy: ${(result.validation_accuracy * 100).toFixed(1)}% (${result.validation_samples} held-out samples).`
            : 'Not enough gallery samples yet for a validation measurement.')
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
      <p className="muted" style={{ marginBottom: '1rem' }}>
        Unknown faces are collected automatically in the background from every live camera — no separate
        capture step. Label them below; new labels are folded into recognition automatically once enough
        accumulate, or immediately with "Train now".
      </p>

      {status && (
        <div className="stat-grid">
          <div className="card"><div className="stat-tile-label">Pending</div><div className="stat-tile-value">{status.pending_samples}</div></div>
          <div className="card"><div className="stat-tile-label">Captured today</div><div className="stat-tile-value">{status.samples_captured_today}</div></div>
          <div className="card"><div className="stat-tile-label">Labeled today</div><div className="stat-tile-value">{status.samples_labeled_today}</div></div>
          <div className="card"><div className="stat-tile-label">Gallery people</div><div className="stat-tile-value">{status.total_gallery_people}</div></div>
        </div>
      )}

      <div className="card" style={{ marginBottom: '1rem' }}>
        <button className="btn btn-primary" onClick={handleTrainNow}>Train now</button>
        {trainMessage && <span className="muted" style={{ marginLeft: '0.75rem' }}>{trainMessage}</span>}
        {lastRun && (
          <div className="muted" style={{ marginTop: '0.5rem' }}>
            Last run: {formatTimestamp(lastRun.trained_at)} — promoted {lastRun.samples_promoted}, rejected{' '}
            {lastRun.samples_rejected}.{' '}
            {lastRun.validation_accuracy != null
              ? `Validation accuracy ${(lastRun.validation_accuracy * 100).toFixed(1)}% on ${lastRun.validation_samples} held-out samples (not a live-camera measurement).`
              : 'Not enough gallery samples yet for a validation measurement.'}
          </div>
        )}
      </div>

      <div className="card">
        <h3 style={{ marginTop: 0 }}>Label pending sample {pendingCount > 0 && `(${pendingCount} waiting)`}</h3>
        {!sample ? (
          <div className="muted">All caught up — no pending samples right now.</div>
        ) : (
          <div style={{ display: 'flex', gap: '1.5rem', flexWrap: 'wrap' }}>
            <img
              src={detectionApi.sampleImageUrl(sample.id)}
              alt="Pending face sample"
              style={{ width: 220, height: 220, objectFit: 'cover', borderRadius: 8, background: '#000' }}
            />
            <div style={{ flex: 1, minWidth: 240 }}>
              <div className="muted" style={{ marginBottom: '0.5rem' }}>
                Camera {sample.camera_id ?? '—'} · captured {formatTimestamp(sample.captured_at)}
              </div>
              <form onSubmit={handleLabel}>
                <input
                  ref={inputRef}
                  type="text"
                  placeholder="Employee name"
                  value={nameInput}
                  onChange={(e) => setNameInput(e.target.value)}
                  autoComplete="off"
                  disabled={busy}
                />
                <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.75rem' }}>
                  <button type="submit" className="btn btn-primary" disabled={busy || !nameInput.trim()}>
                    Label (Enter)
                  </button>
                  <button type="button" className="btn" onClick={handleSkip} disabled={busy}>Skip</button>
                </div>
              </form>
              {message && <div className={`message ${message.type}`}>{message.text}</div>}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
