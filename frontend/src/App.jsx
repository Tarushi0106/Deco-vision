import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import AppShell from './layout/AppShell'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import LiveCameras from './pages/LiveCameras'
import People from './pages/People'
import CameraManagement from './pages/CameraManagement'
import Sites from './pages/Sites'
import Attendance from './pages/Attendance'
import Analytics from './pages/Analytics'
import Footfall from './pages/Footfall'
import './theme.css'

const PAGES = [
  { path: '/dashboard', title: 'Dashboard', breadcrumb: 'Deco Vision / Dashboard', element: <Dashboard /> },
  { path: '/live-cameras', title: 'Live Cameras', breadcrumb: 'Deco Vision / Live Cameras', element: <LiveCameras /> },
  { path: '/people', title: 'People', breadcrumb: 'Deco Vision / People', element: <People /> },
  { path: '/cameras', title: 'Cameras', breadcrumb: 'Deco Vision / Cameras', element: <CameraManagement /> },
  { path: '/sites', title: 'Sites', breadcrumb: 'Deco Vision / Sites', element: <Sites /> },
  { path: '/attendance', title: 'Attendance', breadcrumb: 'Deco Vision / Attendance', element: <Attendance /> },
  { path: '/analytics', title: 'Analytics', breadcrumb: 'Deco Vision / Analytics', element: <Analytics /> },
  { path: '/footfall', title: 'Footfall', breadcrumb: 'Deco Vision / Footfall', element: <Footfall /> },
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
