import { useEffect, useState } from 'react'
import { WS_HOST, WS_PROTOCOL, api } from '../api'

const RECONNECT_DELAY_MS = 3000

function timestampMs() {
  const d = new Date()
  return `${d.toTimeString().slice(0, 8)}.${String(d.getMilliseconds()).padStart(3, '0')}`
}

// Live-pushed unresolved alerts (see backend main.py's /ws/alerts +
// alert_events.py) — event-driven, NOT a poll: the backend pushes a new
// message the instant pipeline.py logs/upgrades an alert or an alert gets
// resolved, via asyncio.run_coroutine_threadsafe from wherever the DB
// write happened. There is no interval timer on either side. The DB row
// is still the single source of truth; this only delivers it to every
// open tab (Dashboard/Intrusion/Smoke/AlertBanner all share this same
// hook/connection pattern) as soon as it exists. Falls back to a one-off
// REST fetch immediately on mount so the list isn't empty while the
// socket connects, and reconnects on drop so a blip doesn't silently
// freeze the alert list.
export default function useLiveAlerts() {
  const [alerts, setAlerts] = useState([])

  useEffect(() => {
    let socket = null
    let reconnectTimer = null
    let stopped = false

    api.listAlerts({ resolved: false }).then(setAlerts).catch(() => {})

    const connect = () => {
      if (stopped) return
      socket = new WebSocket(`${WS_PROTOCOL}://${WS_HOST}/ws/alerts`)
      socket.onmessage = (event) => {
        try {
          const next = JSON.parse(event.data)
          // eslint-disable-next-line no-console
          console.log(`[${timestampMs()}] Received live alert push (${next.length} unresolved)`)
          setAlerts(next)
        } catch {
          // malformed frame — next push corrects it, nothing to recover here
        }
      }
      socket.onclose = () => {
        if (!stopped) reconnectTimer = setTimeout(connect, RECONNECT_DELAY_MS)
      }
      socket.onerror = () => socket.close()
    }
    connect()

    return () => {
      stopped = true
      clearTimeout(reconnectTimer)
      socket?.close()
    }
  }, [])

  return alerts
}
