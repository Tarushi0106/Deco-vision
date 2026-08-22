import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import { setCurrentUser } from '../auth'
import { DEWIN_LOGO_PATH, MASCOT_PATH } from '../branding'
import BrandLogo from '../components/BrandLogo'
import './login.css'

export default function Login() {
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [keepSignedIn, setKeepSignedIn] = useState(true)
  const [stats, setStats] = useState(null)

  useEffect(() => {
    api.getStats().then(setStats).catch(() => {})
  }, [])

  const handleSubmit = async (e) => {
    e.preventDefault()
    // Password isn't verified against anything yet (no real auth backend) —
    // but the login IS now recorded for real via /api/auth/login, so the
    // topbar and /api/users reflect who actually signed in.
    try {
      const user = await api.login(email)
      setCurrentUser(user)
    } catch {
      // backend hiccup shouldn't lock the user out of the UI — just proceed
      // without a stored identity, topbar falls back to a placeholder
    }
    navigate('/dashboard')
  }

  return (
    <div className="login-page">
      <section className="login-left">
        <div className="login-brand">
          <BrandLogo src={DEWIN_LOGO_PATH} fallbackText="DV" className="login-brand-logo" />
          <span className="login-brand-name">Deco Vision</span>
        </div>

        <form className="login-form" onSubmit={handleSubmit}>
          <div className="login-kicker">SIGN IN</div>
          <h1 className="login-headline">
            Welcome to <em>Deco Vision</em>.
          </h1>
          <p className="login-subtext">Use the email tied to your organization's security workspace.</p>

          <label className="login-field">
            Work email
            <input
              type="email"
              placeholder="you@company.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </label>

          <label className="login-field">
            <span className="login-field-label-row">
              Password
              <a href="#" className="login-forgot" onClick={(e) => e.preventDefault()}>
                Forgot?
              </a>
            </span>
            <input
              type="password"
              placeholder="••••••••"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </label>

          <label className="login-checkbox">
            <input
              type="checkbox"
              checked={keepSignedIn}
              onChange={(e) => setKeepSignedIn(e.target.checked)}
            />
            Keep me signed in on this device
          </label>

          <button type="submit" className="login-submit">
            Sign in securely
          </button>
          <button type="button" className="login-demo">
            Request a demo
          </button>

          <div className="login-note">
            <span className="login-note-icon">🛡</span>
            Deco Vision never stores camera footage without consent.{' '}
            <a href="#" onClick={(e) => e.preventDefault()}>
              Read policy
            </a>
          </div>
        </form>
      </section>

      <section className="login-right">
        <div className="login-right-kicker">DECO VISION by DeWin</div>
        <p className="login-tagline">AI Built for the Real World.</p>
        <p className="login-bold-line">Total Site Awareness.</p>

        <img src={MASCOT_PATH} alt="Deco Vision" className="login-mascot" />

        <hr className="login-divider" />

        <div className="login-stats">
          <div className="login-stat">
            <div className="login-stat-value">{stats ? stats.active_cameras : '—'}</div>
            <div className="login-stat-label">Cameras live</div>
          </div>
          <div className="login-stat">
            <div className="login-stat-value">{stats ? stats.faces_enrolled : '—'}</div>
            <div className="login-stat-label">People enrolled</div>
          </div>
          <div className="login-stat">
            <div className="login-stat-value">{stats ? stats.detections_today : '—'}</div>
            <div className="login-stat-label">Detections today</div>
          </div>
        </div>
      </section>
    </div>
  )
}
