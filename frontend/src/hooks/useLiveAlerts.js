import { useEffect, useRef, useState } from 'react'
import { WS_HOST, WS_PROTOCOL, api } from '../api'

const RECONNECT_DELAY_MS = 3000

// Live-pushed unresolved alerts (see backend main.py's /ws/alerts) — the
// single source of truth is still the alerts DB row pipeline.py writes;
// this just delivers it to the UI within ~2s instead of the old 15s HTTP
// poll. Falls back to a one-off REST fetch immediately on mount so the
// list isn't empty while the socket connects, and reconnects on drop so a
// blip in the connection doesn't silently freeze the alert list.
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
          setAlerts(JSON.parse(event.data))
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
