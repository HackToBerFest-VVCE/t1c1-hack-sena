// Redirect to /login if no user is logged in.
// If `role` is provided, also enforces that the user holds that role.

import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { useAuth, type Role } from '../auth'

interface Props {
  role?: Role   // optional: restrict to a specific role
}

export default function RequireAuth({ role }: Props) {
  const { user }   = useAuth()
  const location   = useLocation()

  if (!user) {
    // Preserve the attempted URL so we can redirect back after login
    return <Navigate to="/login" state={{ from: location }} replace />
  }

  if (role && user.role !== role) {
    // Logged in but wrong role → back to dashboard
    return <Navigate to="/" replace />
  }

  return <Outlet />
}
