import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import './layout.css'

function MoonIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path
        d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79Z"
        fill="var(--brand)"
      />
    </svg>
  )
}

function SunIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
      <circle cx="12" cy="12" r="4.5" fill="var(--brand)" />
      <g stroke="var(--brand)" strokeWidth="1.8" strokeLinecap="round">
        <line x1="12" y1="1.5" x2="12" y2="4" />
        <line x1="12" y1="20" x2="12" y2="22.5" />
        <line x1="1.5" y1="12" x2="4" y2="12" />
        <line x1="20" y1="12" x2="22.5" y2="12" />
        <line x1="4.2" y1="4.2" x2="6" y2="6" />
        <line x1="18" y1="18" x2="19.8" y2="19.8" />
        <line x1="4.2" y1="19.8" x2="6" y2="18" />
        <line x1="18" y1="6" x2="19.8" y2="4.2" />
      </g>
    </svg>
  )
}

export default function Topbar({ title, breadcrumb }) {
  const navigate = useNavigate()
  const [theme, setTheme] = useState(() => localStorage.getItem('theme') || 'light')
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef(null)

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('theme', theme)
  }, [theme])

  useEffect(() => {
    if (!menuOpen) return
    const handleClickOutside = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) {
        setMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [menuOpen])

  const handleLogout = () => {
    setMenuOpen(false)
    navigate('/login')
  }

  const now = new Date()
  const date = now.toLocaleDateString('en-IN', { day: '2-digit', month: '2-digit', year: 'numeric' })
  const time = now.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })

  return (
    <header className="topbar">
      <div>
        <h1 className="topbar-title">{title}</h1>
        <div className="topbar-breadcrumb">{breadcrumb}</div>
      </div>
      <div className="topbar-right">
        <span className="pill pill-neutral">Asia/Kolkata</span>
        <span className="pill pill-neutral">{date}</span>
        <span className="pill pill-neutral">{time}</span>
        <button
          className="theme-toggle"
          onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')}
          aria-label="Toggle day/night theme"
          title={theme === 'light' ? 'Switch to night theme' : 'Switch to day theme'}
        >
          {theme === 'light' ? <MoonIcon /> : <SunIcon />}
        </button>
        <div className="topbar-avatar-menu" ref={menuRef}>
          <button className="topbar-avatar" onClick={() => setMenuOpen((v) => !v)}>
            KG
          </button>
          {menuOpen && (
            <div className="topbar-dropdown">
              <div className="topbar-dropdown-name">Kanishka Gangwar</div>
              <div className="topbar-dropdown-email">kanishka.gangwar@dewin.in</div>
              <button className="topbar-dropdown-logout" onClick={handleLogout}>
                Log out
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  )
}
