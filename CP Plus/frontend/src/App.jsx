import { BrowserRouter, Navigate, NavLink, Route, Routes } from 'react-router-dom'
import LiveDetection from './pages/LiveDetection'
import FaceTraining from './pages/FaceTraining'

export default function App() {
  return (
    <BrowserRouter>
      <div className="app-shell">
        <header className="app-header">
          <h1>Deco Vision — Detection</h1>
          <nav className="app-nav">
            <NavLink to="/live" className={({ isActive }) => (isActive ? 'active' : '')}>Live Detection</NavLink>
            <NavLink to="/face-training" className={({ isActive }) => (isActive ? 'active' : '')}>Face Training</NavLink>
          </nav>
        </header>
        <main className="app-main">
          <Routes>
            <Route path="/" element={<Navigate to="/live" replace />} />
            <Route path="/live" element={<LiveDetection />} />
            <Route path="/face-training" element={<FaceTraining />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  )
}
