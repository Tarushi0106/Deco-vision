import { useState } from 'react'
import { useActivateLicense } from '../useActivateLicense'

// Post-login "Activate / Update License" modal — for an admin redeeming
// an additional license code for their company. Does NOT sign the caller
// in as the code's auto-provisioned viewer account (setSession: false —
// see useActivateLicense's doc comment for why that would otherwise
// silently swap the acting admin's own session). QR *scanning* (camera-
// based) is intentionally not built here — no QR library exists in this
// app yet and it's a materially separate addition (new dependency,
// getUserMedia permissions, mobile testing); manual code entry covers the
// same wire format (ActivateIn.code) end-to-end today.
export default function ActivateLicenseModal({ myCompanyId, onClose, onActivated }) {
  const [code, setCode] = useState('')
  const { status, errorInfo, result, atCapacity, activate } = useActivateLicense(null, { setSession: false })

  const handleSubmit = async (e) => {
    e.preventDefault()
    await activate(code)
  }

  const wrongCompany = status === 'success' && myCompanyId && result.license.company_id !== myCompanyId

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="card modal" onClick={(e) => e.stopPropagation()}>
        <h3>Activate / update license</h3>
        <p className="page-toolbar-sub" style={{ marginBottom: '0.75rem' }}>
          Enter a license code to add it to your account, or re-confirm an existing one.
        </p>

        {status === 'success' ? (
          wrongCompany ? (
            <div className="form-message error">
              This code belongs to a different company than yours — check the code and contact your super admin
              if you believe this is wrong.
            </div>
          ) : (
            <>
              <div className="form-message success">
                License <strong>{result.license.label || result.license.license_key}</strong> is active —
                {' '}{result.cameras.length} / {result.license.max_cameras} camera(s) assigned.
              </div>
              {atCapacity && (
                <div className="form-message error" style={{ marginTop: '0.5rem' }}>
                  This license is at its camera capacity — contact your super admin to raise the limit before
                  more cameras can be assigned.
                </div>
              )}
            </>
          )
        ) : (
          <form onSubmit={handleSubmit}>
            <label className="license-form-field">
              License code
              <input
                value={code}
                onChange={(e) => setCode(e.target.value)}
                placeholder="XXXX-XXXX-XXXX-XXXX"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
                required
                autoFocus
              />
            </label>
            {status === 'error' && <div className="form-message error">{errorInfo.message}</div>}
            <div className="modal-actions">
              <button type="button" className="btn btn-outline" onClick={onClose}>Cancel</button>
              <button type="submit" className="btn btn-primary" disabled={status === 'loading'}>
                {status === 'loading' ? 'Activating…' : 'Activate'}
              </button>
            </div>
          </form>
        )}

        {status === 'success' && (
          <div className="modal-actions">
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => { onActivated?.(); onClose() }}
            >
              Done
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
