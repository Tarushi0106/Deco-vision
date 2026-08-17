import { useEffect, useState } from 'react'
import { NavLink } from 'react-router-dom'
import { api } from '../api'
import BrandLogo from '../components/BrandLogo'
import './layout.css'

const DEWIN_LOGO_PATH = '/src/assets/branding/dewin-logo.png'
const MASCOT_PATH = '/src/assets/branding/deco-vision-mascot.png'

const NAV_GROUPS = [
  {
    label: 'Overview',
    items: [
      { to: '/dashboard', label: 'Dashboard' },
      { to: '/live-cameras', label: 'Live Cameras' },
    ],
  },
  {
    label: 'Core',
    items: [
      { to: '/people', label: 'People' },
      { to: '/cameras', label: 'Camera Management' },
      { to: '/sites', label: 'Site Management' },
    ],
  },
  {
    label: 'Coming soon',
    items: [
      { label: 'Attendance', tag: 'P2' },
      { label: 'Crowd Analytics', tag: 'P2' },
      { label: 'Threat Detection', tag: 'P3' },
      { label: 'Vehicle Detection', tag: 'P3' },
    ],
  },
]

function formatUptime(seconds) {
  if (seconds < 60) return `${seconds}s`
  const mins = Math.floor(seconds / 60)
  if (mins < 60) return `${mins}m`
  const hours = Math.floor(mins / 60)
  return `${hours}h ${mins % 60}m`
}

export default function Sidebar() {
  const [stats, setStats] = useState(null)

  useEffect(() => {
    const load = () => {
      api.getStats().then(setStats).catch(() => {})
    }
    load()
    const interval = setInterval(load, 15000)
    return () => clearInterval(interval)
  }, [])

  const nominal = stats?.system_status === 'nominal'

  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <BrandLogo src={DEWIN_LOGO_PATH} fallbackText="DV" className="sidebar-logo" />
        <div>
          <div className="sidebar-brand-name">Deco Vision</div>
          <div className="sidebar-brand-sub">AI SURVEILLANCE PLATFORM</div>
        </div>
      </div>

      {stats && (
        <div className={`sidebar-status pill ${nominal ? 'pill-success' : 'pill-danger'}`}>
          <span className={`dot ${nominal ? 'dot-success' : 'dot-danger'}`} />
          {nominal
            ? `All cameras online · up ${formatUptime(stats.uptime_seconds)}`
            : `${stats.active_cameras}/${stats.total_cameras} configured cameras online`}
        </div>
      )}

      <nav className="sidebar-nav">
        {NAV_GROUPS.map((group) => (
          <div className="sidebar-group" key={group.label}>
            <div className="sidebar-group-label">{group.label}</div>
            {group.items.map((item) =>
              item.to ? (
                <NavLink
                  key={item.label}
                  to={item.to}
                  className={({ isActive }) =>
                    'sidebar-item' + (isActive ? ' sidebar-item-active' : '')
                  }
                >
                  {item.label}
                </NavLink>
              ) : (
                <div className="sidebar-item sidebar-item-disabled" key={item.label}>
                  {item.label}
                  {item.tag && <span className="sidebar-tag">{item.tag}</span>}
                </div>
              )
            )}
          </div>
        ))}
      </nav>

      <div className="sidebar-footer">
        <img src={MASCOT_PATH} alt="" className="sidebar-mascot" />
        <div className="sidebar-footer-row">
          <BrandLogo src={DEWIN_LOGO_PATH} fallbackText="DW" className="sidebar-avatar" />
          <div>
            <div className="sidebar-footer-name">DeWin Solutions</div>
            <div className="sidebar-footer-sub">Powered by DeWin</div>
          </div>
        </div>
      </div>
    </aside>
  )
}
