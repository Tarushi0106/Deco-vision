import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
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

// Self-contained: polls its own alert feed and beeps + shows a pulsing red
// banner on any NEW unresolved alert. Drop it into any page — it doesn't
// need the host page's own alerts state (Dashboard's alerts table, e.g.,
// stays independent so it can list/resolve without this banner's involvement).
export default function AlertBanner() {
  const [alerts, setAlerts] = useState([])
  const seenAlertIds = useRef(null) // null until first load, so existing alerts don't beep on page open

  useEffect(() => {
    const load = () => {
      api
        .listAlerts({ resolved: false })
        .then((fresh) => {
          if (seenAlertIds.current === null) {
            seenAlertIds.current = new Set(fresh.map((a) => a.id))
          } else if (fresh.some((a) => !seenAlertIds.current.has(a.id))) {
            playAlertBeep()
            seenAlertIds.current = new Set(fresh.map((a) => a.id))
          }
          setAlerts(fresh)
        })
        .catch(() => {})
    }
    load()
    const interval = setInterval(load, 15000)
    return () => clearInterval(interval)
  }, [])

  if (alerts.length === 0) return null

  return (
    <div className="alert-banner">
      <span className="alert-banner-icon">⚠</span>
      {alerts.length} active alert{alerts.length === 1 ? '' : 's'} — most recent: {alerts[0].message} (
      {alerts[0].camera_name})
    </div>
  )
}
