import { useEffect, useState } from 'react'
import { parachuteApi } from '../api'
import DetectionCameraTile from '../components/DetectionCameraTile'

export default function LiveDetection() {
  const [cameras, setCameras] = useState([])

  useEffect(() => {
    const load = () => parachuteApi.listCameras().then(setCameras).catch(() => {})
    load()
    const interval = setInterval(load, 30000)
    return () => clearInterval(interval)
  }, [])

  const liveCameras = cameras.filter((c) => c.live)

  return (
    <div>
      <p className="muted" style={{ marginBottom: '1rem' }}>
        Video streams from the existing Parachute application; person and face boxes are computed by this
        detection service independently. Every visible person gets a blue box — their recognized name if a
        usable face matches, otherwise "Person". Never "Unknown".
      </p>
      {liveCameras.length === 0 ? (
        <div className="card muted">No live cameras reported by Parachute right now.</div>
      ) : (
        <div className="camera-grid">
          {liveCameras.map((cam) => (
            <DetectionCameraTile key={cam.id} camera={cam} />
          ))}
        </div>
      )}
    </div>
  )
}
