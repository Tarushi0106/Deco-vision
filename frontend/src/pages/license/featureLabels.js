// Static mirror of backend/app/license_db.py's FEATURE_LABELS — same
// pattern as shared.jsx's STATUS_LABELS already duplicating the backend's
// status strings. The catalog itself is still fetched live from
// GET /api/feature-catalog wherever a checklist of it is rendered
// (LicenseFeaturesModal, CameraFeaturesModal); this map is only for
// quickly labeling an already-known feature key (e.g. in a table cell)
// without an extra round trip.
export const FEATURE_LABELS = {
  face_recognition: 'Face Recognition',
  attendance: 'Attendance',
  footfall_analytics: 'Footfall Analytics',
  workforce_analytics: 'Workforce Analytics',
  intrusion_detection: 'Intrusion Detection',
  smoke_detection: 'Smoke Detection',
  crowd_analytics: 'Crowd Analytics',
  threat_detection: 'Threat Detection',
  vehicle_detection: 'Vehicle Detection',
}
