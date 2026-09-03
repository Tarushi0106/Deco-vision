import { useEffect, useRef } from 'react'
import useLiveAlerts from '../hooks/useLiveAlerts'
import '../pages/pages.css'

// short two-tone alert beep, synthesized on the fly — no audio asset to ship
function playAlertBeep() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)()
    const now = ctx.currentTime
    ;[880, 660].forEach((freq, i) => {
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.type = 'square'
      osc.frequency.value = freq
      const start = now + i * 0.18
      gain.gain.setValueAtTime(0.0001, start)
      gain.gain.exponentialRampToValueAtTime(0.2, start + 0.02)
      gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.16)
      osc.connect(gain)
      gain.connect(ctx.destination)
      osc.start(start)
      osc.stop(start + 0.17)
    })
    setTimeout(() => ctx.close(), 500)
  } catch {
    // Web Audio unavailable (e.g. autoplay policy before any user
    // interaction) — the visual red banner still carries the alert
  }
}

// Scope of this flashing banner, per explicit request: fire/smoke/intrusion/
// zone_intrusion flash+beep here (a zone violation is exactly as time-
// sensitive as smoke — someone unauthorized is in a restricted area right
// now). fall stays visible in Dashboard's Live Alerts table only.
const FLASH_ALERT_TYPES = new Set(['fire', 'smoke', 'intrusion', 'zone_intrusion'])

// Self-contained: subscribes to the live alert feed and beeps + shows a
// pulsing red banner on any NEW unresolved alert. Drop it into any page —
// it doesn't need the host page's own alerts state (Dashboard's alerts
// table, e.g., stays independent so it can list/resolve without this
// banner's involvement).
export default function AlertBanner() {
  const allAlerts = useLiveAlerts()
  const alerts = allAlerts.filter((a) => FLASH_ALERT_TYPES.has(a.type))
  const seenAlertIds = useRef(null) // null until first push, so existing alerts don't beep on page open

  useEffect(() => {
    if (seenAlertIds.current === null) {
      seenAlertIds.current = new Set(alerts.map((a) => a.id))
      return
    }
    if (alerts.some((a) => !seenAlertIds.current.has(a.id))) {
      playAlertBeep()
    }
    seenAlertIds.current = new Set(alerts.map((a) => a.id))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [alerts.map((a) => a.id).join(',')])

  if (alerts.length === 0) return null

  return (
    <div className="alert-banner">
      <span className="alert-banner-icon">⚠</span>
      {alerts.length} active alert{alerts.length === 1 ? '' : 's'} — most recent: {alerts[0].message} (
      {alerts[0].camera_name})
    </div>
  )
}
