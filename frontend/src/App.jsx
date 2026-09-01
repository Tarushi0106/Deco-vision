import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import AppShell from './layout/AppShell'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import LiveCameras from './pages/LiveCameras'
import People from './pages/People'
import CameraManagement from './pages/CameraManagement'
import Intrusion from './pages/Intrusion'
import SmokeDetection from './pages/SmokeDetection'
import Sites from './pages/Sites'
import Attendance from './pages/Attendance'
import Analytics from './pages/Analytics'
import Footfall from './pages/Footfall'
import LicenseManagement from './pages/LicenseManagement'
import './theme.css'

const PAGES = [
  { path: '/dashboard', title: 'Dashboard', breadcrumb: 'Deco Vision / Dashboard', element: <Dashboard /> },
  { path: '/live-cameras', title: 'Live Cameras', breadcrumb: 'Deco Vision / Live Cameras', element: <LiveCameras /> },
  { path: '/people', title: 'People', breadcrumb: 'Deco Vision / People', element: <People /> },
  { path: '/cameras', title: 'Cameras', breadcrumb: 'Deco Vision / Cameras', element: <CameraManagement /> },
  { path: '/intrusion', title: 'Intrusion', breadcrumb: 'Deco Vision / Intrusion', element: <Intrusion /> },
  {
    path: '/smoke-detection', title: 'Smoke Detection',
    breadcrumb: 'Deco Vision / Smoke Detection', element: <SmokeDetection />,
  },
  { path: '/sites', title: 'Sites', breadcrumb: 'Deco Vision / Sites', element: <Sites /> },
  { path: '/attendance', title: 'Attendance', breadcrumb: 'Deco Vision / Attendance', element: <Attendance /> },
  { path: '/analytics', title: 'Workforce Analytics', breadcrumb: 'Deco Vision / Workforce Analytics', element: <Analytics /> },
  { path: '/footfall', title: 'Footfall', breadcrumb: 'Deco Vision / Footfall', element: <Footfall /> },
  {
    path: '/license-management', title: 'License & Camera Access',
    breadcrumb: 'Deco Vision / License & Camera Access', element: <LicenseManagement />,
  },
]

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Navigate to="/login" replace />} />
        <Route path="/login" element={<Login />} />
        {PAGES.map(({ path, title, breadcrumb, element }) => (
          <Route key={path} element={<AppShell title={title} breadcrumb={breadcrumb} />}>
            <Route path={path} element={element} />
          </Route>
        ))}
      </Routes>
    </BrowserRouter>
  )
}

export default App
