// ── Auth context ──────────────────────────────────────────────────────────────
// Stores the logged-in user in React state (no backend auth, no tokens).
// Role is chosen on the login page and lives until logout or page refresh.

import {
  createContext,
  useContext,
  useState,
  type ReactNode,
} from 'react'

export type Role = 'Reviewer' | 'Supervisor'

export interface AuthUser {
  name: string
  role: Role
}

interface AuthCtx {
  user:    AuthUser | null
  login:   (user: AuthUser) => void
  logout:  () => void
}

const AuthContext = createContext<AuthCtx | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null)

  function login(u: AuthUser)  { setUser(u) }
  function logout()            { setUser(null) }

  return (
    <AuthContext.Provider value={{ user, login, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthCtx {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>')
  return ctx
}
