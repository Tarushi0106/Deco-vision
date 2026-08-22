import CameraTile from './CameraTile'
import './CameraModal.css'

export default function CameraModal({ camera, onClose }) {
  return (
    <div className="camera-modal-backdrop" onClick={onClose}>
      <div className="camera-modal" onClick={(e) => e.stopPropagation()}>
        <button className="camera-modal-close" onClick={onClose} aria-label="Close">
          ✕
        </button>
        <CameraTile camera={camera} showOverlay={camera.live} large />
      </div>
    </div>
  )
}
