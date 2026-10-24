import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth, type Role } from '../auth'
import './LoginPage.css'

export default function LoginPage() {
  const { login } = useAuth()
  const navigate  = useNavigate()

  const [name, setName]   = useState('')
  const [role, setRole]   = useState<Role>('Reviewer')
  const [error, setError] = useState('')

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const trimmed = name.trim()
    if (!trimmed) { setError('Please enter your name.'); return }
    login({ name: trimmed, role })
    navigate('/', { replace: true })
  }

  return (
    <div className="login-bg">
      <div className="login-card">
        {/* brand */}
        <div className="login-brand">
          <span className="login-icon">🔍</span>
          <span className="login-brand-name">ClaimLens AI</span>
        </div>
        <p className="login-tagline">Hospital Bill Adjudication Platform</p>

        <form onSubmit={handleSubmit} noValidate>
          {/* name */}
          <label className="login-label" htmlFor="name">Your name</label>
          <input
            id="name"
            className="login-input"
            type="text"
            placeholder="e.g. Ananya Krishnan"
            value={name}
            onChange={e => { setName(e.target.value); setError('') }}
            autoComplete="off"
            autoFocus
          />

          {/* role */}
          <p className="login-label" style={{ marginTop: '1.1rem' }}>Sign in as</p>
          <div className="role-grid">
            {(['Reviewer', 'Supervisor'] as Role[]).map(r => (
              <button
                key={r}
                type="button"
                className={`role-card${role === r ? ' selected' : ''}`}
                onClick={() => setRole(r)}
              >
                <span className="role-icon">{r === 'Reviewer' ? '🧾' : '🛡️'}</span>
                <span className="role-name">{r}</span>
                <span className="role-desc">
                  {r === 'Reviewer'
                    ? 'Review and query claims'
                    : 'Approve, escalate & audit log'}
                </span>
              </button>
            ))}
          </div>

          {error && <p className="login-error" role="alert">{error}</p>}

          <button type="submit" className="login-btn">
            Sign in as {role}
          </button>
        </form>
      </div>
    </div>
  )
}
