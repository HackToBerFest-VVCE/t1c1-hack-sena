import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Route, Routes, Navigate } from 'react-router-dom'

import { AuthProvider } from './auth'
import RequireAuth    from './components/RequireAuth'
import Layout         from './components/Layout'

import LoginPage       from './pages/LoginPage'
import DashboardPage   from './pages/DashboardPage'
import ClaimDetailPage from './pages/ClaimDetailPage'
import UploadPage      from './pages/UploadPage'
import AuditPage       from './pages/AuditPage'

import './index.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          {/* public */}
          <Route path="/login" element={<LoginPage />} />

          {/* protected – any logged-in role */}
          <Route element={<RequireAuth />}>
            <Route element={<Layout />}>
              <Route index          element={<DashboardPage />} />
              <Route path="claims/:id" element={<ClaimDetailPage />} />
              <Route path="upload"     element={<UploadPage />} />

              {/* Supervisor only */}
              <Route element={<RequireAuth role="Supervisor" />}>
                <Route path="audit" element={<AuditPage />} />
              </Route>
            </Route>
          </Route>

          {/* fallback */}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  </StrictMode>,
)
