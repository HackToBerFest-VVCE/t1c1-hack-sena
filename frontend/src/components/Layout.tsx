import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth'
import './Layout.css'

export default function Layout() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()

  function handleLogout() {
    logout()
    navigate('/login', { replace: true })
  }

  return (
    <div className="layout">
      {/* ── top header ─────────────────────────────────────── */}
      <header className="layout-header">
        <div className="layout-brand">
          <span className="layout-icon">🔍</span>
          <span className="layout-name">ClaimLens AI</span>
        </div>

        <nav className="layout-nav">
          <NavLink to="/"       end className={navCls}>Claims Queue</NavLink>
          <NavLink to="/upload"     className={navCls}>Upload</NavLink>
          {user?.role === 'Supervisor' && (
            <NavLink to="/audit" className={navCls}>Audit Log</NavLink>
          )}
        </nav>

        <div className="layout-user">
          <span className="layout-user-info">
            <span className="layout-user-name">{user?.name}</span>
            <span className="layout-role-badge" data-role={user?.role}>
              {user?.role}
            </span>
          </span>
          <button className="layout-logout" onClick={handleLogout}>
            Sign out
          </button>
        </div>
      </header>

      {/* ── page content ───────────────────────────────────── */}
      <main className="layout-content">
        <Outlet />
      </main>
    </div>
  )
}

// active / inactive NavLink class helper
function navCls({ isActive }: { isActive: boolean }) {
  return `layout-nav-link${isActive ? ' active' : ''}`
}
