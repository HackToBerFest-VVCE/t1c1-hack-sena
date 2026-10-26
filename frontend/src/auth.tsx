/* eslint-disable react-refresh/only-export-components */
import {
  createContext,
  useContext,
  useState,
  useEffect,
  type ReactNode,
} from 'react'

export type Role = 'Reviewer' | 'Supervisor'

export interface AuthUser {
  id?:    number
  name:   string
  email?: string
  role:   Role
}

export const TOKEN_STORAGE_KEY = 'claimlens_token'
export const USER_STORAGE_KEY  = 'claimlens_user'

// In-memory token storage + sessionStorage fallback
let inMemoryToken: string | null = null

export function getToken(): string | null {
  if (!inMemoryToken) {
    try {
      inMemoryToken = sessionStorage.getItem(TOKEN_STORAGE_KEY)
    } catch {
      inMemoryToken = null
    }
  }
  return inMemoryToken
}

export function setToken(token: string | null): void {
  inMemoryToken = token
  try {
    if (token) {
      sessionStorage.setItem(TOKEN_STORAGE_KEY, token)
    } else {
      sessionStorage.removeItem(TOKEN_STORAGE_KEY)
    }
  } catch {
    // sessionStorage may be blocked or unavailable
  }
}

export function clearAuthSession(): void {
  setToken(null)
  try {
    sessionStorage.removeItem(USER_STORAGE_KEY)
  } catch {
    // ignore
  }
}

export function normalizeRole(role: string): Role {
  return role.toLowerCase() === 'supervisor' ? 'Supervisor' : 'Reviewer'
}

interface AuthCtx {
  user:    AuthUser | null
  token:   string | null
  login:   (token: string, user: AuthUser) => void
  logout:  () => void
}

const AuthContext = createContext<AuthCtx | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [tokenState, setTokenState] = useState<string | null>(() => getToken())
  const [user, setUser] = useState<AuthUser | null>(() => {
    try {
      const saved = sessionStorage.getItem(USER_STORAGE_KEY)
      if (saved) {
        const parsed = JSON.parse(saved) as AuthUser
        return {
          ...parsed,
          role: normalizeRole(parsed.role),
        }
      }
    } catch {
      // ignore JSON parse errors
    }
    return null
  })

  // Synchronize in-memory token on mount
  useEffect(() => {
    const existing = getToken()
    if (existing) {
      setTokenState(existing)
    }
  }, [])

  function login(newToken: string, newUser: AuthUser) {
    const normalized: AuthUser = {
      ...newUser,
      role: normalizeRole(newUser.role),
    }
    setToken(newToken)
    setTokenState(newToken)
    try {
      sessionStorage.setItem(USER_STORAGE_KEY, JSON.stringify(normalized))
    } catch {
      // ignore
    }
    setUser(normalized)
  }

  function logout() {
    clearAuthSession()
    setTokenState(null)
    setUser(null)
  }

  return (
    <AuthContext.Provider value={{ user, token: tokenState, login, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthCtx {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>')
  return ctx
}
