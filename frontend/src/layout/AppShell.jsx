import { Outlet } from 'react-router-dom'
import Sidebar from './Sidebar'
import Topbar from './Topbar'
import './layout.css'

export default function AppShell({ title, breadcrumb }) {
  return (
    <div className="app-shell">
      <Sidebar />
      <div className="main-area">
        <Topbar title={title} breadcrumb={breadcrumb} />
        <div className="page-content">
          <Outlet />
        </div>
      </div>
    </div>
  )
}
