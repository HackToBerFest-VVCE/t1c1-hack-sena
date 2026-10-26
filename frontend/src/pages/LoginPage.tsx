import { useState, useId } from 'react'
import { useNavigate } from 'react-router-dom'
import axios from 'axios'
import { useAuth, normalizeRole } from '../auth'
import { loginApi, signupApi } from '../api'
import './LoginPage.css'

export default function LoginPage() {
  const { login } = useAuth()
  const navigate = useNavigate()

  const [activeTab, setActiveTab] = useState<'signin' | 'signup'>('signin')

  // ── Sign in state ──────────────────────────────────────────────────────────
  const [signInEmail, setSignInEmail] = useState('')
  const [signInPassword, setSignInPassword] = useState('')
  const [showSignInPassword, setShowSignInPassword] = useState(false)
  const [signInLoading, setSignInLoading] = useState(false)
  const [signInError, setSignInError] = useState('')

  // ── Sign up state ──────────────────────────────────────────────────────────
  const [signUpName, setSignUpName] = useState('')
  const [signUpEmail, setSignUpEmail] = useState('')
  const [signUpPassword, setSignUpPassword] = useState('')
  const [signUpConfirmPassword, setSignUpConfirmPassword] = useState('')
  const [showSignUpPassword, setShowSignUpPassword] = useState(false)
  const [showSignUpConfirmPassword, setShowSignUpConfirmPassword] = useState(false)
  const [showInviteInput, setShowInviteInput] = useState(false)
  const [inviteCode, setInviteCode] = useState('')
  const [signUpLoading, setSignUpLoading] = useState(false)
  const [signUpGeneralError, setSignUpGeneralError] = useState('')

  // ── Inline errors for sign up ──────────────────────────────────────────────
  const [nameError, setNameError] = useState('')
  const [emailError, setEmailError] = useState('')
  const [passwordError, setPasswordError] = useState('')
  const [confirmPasswordError, setConfirmPasswordError] = useState('')

  // ── Password strength calculations ────────────────────────────────────────
  const hasMinLength = signUpPassword.length >= 8
  const hasLetter = /[a-zA-Z]/.test(signUpPassword)
  const hasNumber = /[0-9]/.test(signUpPassword)
  const strengthCount = (hasMinLength ? 1 : 0) + (hasLetter ? 1 : 0) + (hasNumber ? 1 : 0)

  const strengthClass =
    strengthCount === 3 ? 'strong' : strengthCount === 2 ? 'medium' : 'weak'

  // Generate unique IDs for accessibility
  const signInEmailId = useId()
  const signInPasswordId = useId()
  const signUpNameId = useId()
  const signUpEmailId = useId()
  const signUpPasswordId = useId()
  const signUpConfirmPasswordId = useId()
  const inviteCodeId = useId()

  // ── Handlers ───────────────────────────────────────────────────────────────

  async function handleSignInSubmit(e: React.FormEvent) {
    e.preventDefault()
    setSignInError('')

    const cleanEmail = signInEmail.trim()
    if (!cleanEmail || !signInPassword) {
      setSignInError('Invalid email or password')
      return
    }

    setSignInLoading(true)
    try {
      const res = await loginApi(cleanEmail, signInPassword)
      login(res.access_token, {
        id: res.user.id,
        name: res.user.name,
        email: res.user.email,
        role: normalizeRole(res.user.role),
      })
      navigate('/', { replace: true })
    } catch (err: unknown) {
      if (axios.isAxiosError(err)) {
        if (err.response?.status === 429) {
          setSignInError(
            err.response.data?.detail ||
              'Too many failed login attempts. Please try again in 10 minutes.'
          )
        } else {
          // Standard generic error per requirement 3
          setSignInError('Invalid email or password')
        }
      } else {
        setSignInError('Invalid email or password')
      }
    } finally {
      setSignInLoading(false)
    }
  }

  function validateSignUpForm(): boolean {
    let isValid = true
    setNameError('')
    setEmailError('')
    setPasswordError('')
    setConfirmPasswordError('')
    setSignUpGeneralError('')

    if (!signUpName.trim()) {
      setNameError('Full name is required.')
      isValid = false
    }

    const emailPattern = /^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$/
    if (!signUpEmail.trim()) {
      setEmailError('Email address is required.')
      isValid = false
    } else if (!emailPattern.test(signUpEmail.trim())) {
      setEmailError('Please enter a valid email address.')
      isValid = false
    }

    if (!signUpPassword) {
      setPasswordError('Password is required.')
      isValid = false
    } else if (!hasMinLength || !hasLetter || !hasNumber) {
      setPasswordError(
        'Password must be at least 8 characters and contain at least one letter and one number.'
      )
      isValid = false
    }

    if (!signUpConfirmPassword) {
      setConfirmPasswordError('Please confirm your password.')
      isValid = false
    } else if (signUpPassword !== signUpConfirmPassword) {
      setConfirmPasswordError('Passwords do not match.')
      isValid = false
    }

    return isValid
  }

  async function handleSignUpSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!validateSignUpForm()) return

    setSignUpLoading(true)
    try {
      const res = await signupApi({
        full_name: signUpName.trim(),
        email: signUpEmail.trim(),
        password: signUpPassword,
        confirm_password: signUpConfirmPassword,
        invite_code: showInviteInput ? inviteCode.trim() : undefined,
      })

      login(res.access_token, {
        id: res.user.id,
        name: res.user.name,
        email: res.user.email,
        role: normalizeRole(res.user.role),
      })
      navigate('/', { replace: true })
    } catch (err: unknown) {
      if (axios.isAxiosError(err)) {
        const detail = err.response?.data?.detail
        if (err.response?.status === 400 && String(detail).toLowerCase().includes('already registered')) {
          setEmailError('Email is already registered.')
        } else {
          setSignUpGeneralError(
            typeof detail === 'string'
              ? detail
              : 'Failed to create account. Please check your inputs.'
          )
        }
      } else {
        setSignUpGeneralError('An unexpected error occurred. Please try again.')
      }
    } finally {
      setSignUpLoading(false)
    }
  }

  return (
    <div className="login-bg">
      <div className="login-card">
        {/* Brand */}
        <div className="login-brand">
          <span className="login-icon">🔍</span>
          <span className="login-brand-name">ClaimLens AI</span>
        </div>
        <p className="login-tagline">Hospital Bill Adjudication Platform</p>

        {/* Tabs */}
        <div className="auth-tabs" role="tablist" aria-label="Authentication Options">
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === 'signin'}
            className={`auth-tab-btn${activeTab === 'signin' ? ' active' : ''}`}
            onClick={() => {
              setActiveTab('signin')
              setSignInError('')
            }}
          >
            Sign in
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === 'signup'}
            className={`auth-tab-btn${activeTab === 'signup' ? ' active' : ''}`}
            onClick={() => {
              setActiveTab('signup')
              setSignUpGeneralError('')
            }}
          >
            Sign up
          </button>
        </div>

        {/* Sign In Form */}
        {activeTab === 'signin' && (
          <form onSubmit={handleSignInSubmit} noValidate>
            {signInError && (
              <div className="general-error" role="alert">
                {signInError}
              </div>
            )}

            <div className="form-group">
              <label className="login-label" htmlFor={signInEmailId}>
                Email address
              </label>
              <input
                id={signInEmailId}
                className="login-input"
                type="email"
                placeholder="reviewer@claimlens.demo"
                value={signInEmail}
                onChange={e => {
                  setSignInEmail(e.target.value)
                  setSignInError('')
                }}
                autoComplete="email"
                autoFocus
              />
            </div>

            <div className="form-group">
              <label className="login-label" htmlFor={signInPasswordId}>
                Password
              </label>
              <div className="input-with-toggle">
                <input
                  id={signInPasswordId}
                  className="login-input"
                  type={showSignInPassword ? 'text' : 'password'}
                  placeholder="Enter your password"
                  value={signInPassword}
                  onChange={e => {
                    setSignInPassword(e.target.value)
                    setSignInError('')
                  }}
                  autoComplete="current-password"
                />
                <button
                  type="button"
                  className="toggle-pwd-btn"
                  onClick={() => setShowSignInPassword(v => !v)}
                  aria-label={showSignInPassword ? 'Hide password' : 'Show password'}
                >
                  {showSignInPassword ? 'Hide' : 'Show'}
                </button>
              </div>
            </div>

            <button type="submit" className="login-btn" disabled={signInLoading}>
              {signInLoading ? 'Signing in…' : 'Sign in'}
            </button>
          </form>
        )}

        {/* Sign Up Form */}
        {activeTab === 'signup' && (
          <form onSubmit={handleSignUpSubmit} noValidate>
            {signUpGeneralError && (
              <div className="general-error" role="alert">
                {signUpGeneralError}
              </div>
            )}

            {/* Full Name */}
            <div className="form-group">
              <label className="login-label" htmlFor={signUpNameId}>
                Full name
              </label>
              <input
                id={signUpNameId}
                className={`login-input${nameError ? ' has-error' : ''}`}
                type="text"
                placeholder="e.g. Ananya Krishnan"
                value={signUpName}
                onChange={e => {
                  setSignUpName(e.target.value)
                  if (nameError) setNameError('')
                }}
                autoComplete="name"
                autoFocus
              />
              {nameError && <span className="field-error">{nameError}</span>}
            </div>

            {/* Email */}
            <div className="form-group">
              <label className="login-label" htmlFor={signUpEmailId}>
                Email address
              </label>
              <input
                id={signUpEmailId}
                className={`login-input${emailError ? ' has-error' : ''}`}
                type="email"
                placeholder="ananya@example.com"
                value={signUpEmail}
                onChange={e => {
                  setSignUpEmail(e.target.value)
                  if (emailError) setEmailError('')
                }}
                autoComplete="email"
              />
              {emailError && <span className="field-error">{emailError}</span>}
            </div>

            {/* Password */}
            <div className="form-group">
              <label className="login-label" htmlFor={signUpPasswordId}>
                Password
              </label>
              <div className="input-with-toggle">
                <input
                  id={signUpPasswordId}
                  className={`login-input${passwordError ? ' has-error' : ''}`}
                  type={showSignUpPassword ? 'text' : 'password'}
                  placeholder="At least 8 characters"
                  value={signUpPassword}
                  onChange={e => {
                    setSignUpPassword(e.target.value)
                    if (passwordError) setPasswordError('')
                  }}
                  autoComplete="new-password"
                />
                <button
                  type="button"
                  className="toggle-pwd-btn"
                  onClick={() => setShowSignUpPassword(v => !v)}
                  aria-label={showSignUpPassword ? 'Hide password' : 'Show password'}
                >
                  {showSignUpPassword ? 'Hide' : 'Show'}
                </button>
              </div>

              {/* Live strength hint */}
              {signUpPassword.length > 0 && (
                <div className="pwd-strength-container" aria-live="polite">
                  <div className="pwd-strength-meter">
                    <div className={`pwd-strength-bar ${strengthClass}`} />
                  </div>
                  <div className="pwd-hints">
                    <span className={`pwd-hint-item${hasMinLength ? ' valid' : ''}`}>
                      {hasMinLength ? '✓' : '○'} 8+ characters
                    </span>
                    <span className={`pwd-hint-item${hasLetter ? ' valid' : ''}`}>
                      {hasLetter ? '✓' : '○'} 1 letter
                    </span>
                    <span className={`pwd-hint-item${hasNumber ? ' valid' : ''}`}>
                      {hasNumber ? '✓' : '○'} 1 number
                    </span>
                  </div>
                </div>
              )}
              {passwordError && <span className="field-error">{passwordError}</span>}
            </div>

            {/* Confirm Password */}
            <div className="form-group">
              <label className="login-label" htmlFor={signUpConfirmPasswordId}>
                Confirm password
              </label>
              <div className="input-with-toggle">
                <input
                  id={signUpConfirmPasswordId}
                  className={`login-input${confirmPasswordError ? ' has-error' : ''}`}
                  type={showSignUpConfirmPassword ? 'text' : 'password'}
                  placeholder="Re-enter your password"
                  value={signUpConfirmPassword}
                  onChange={e => {
                    setSignUpConfirmPassword(e.target.value)
                    if (confirmPasswordError) setConfirmPasswordError('')
                  }}
                  autoComplete="new-password"
                />
                <button
                  type="button"
                  className="toggle-pwd-btn"
                  onClick={() => setShowSignUpConfirmPassword(v => !v)}
                  aria-label={showSignUpConfirmPassword ? 'Hide confirm password' : 'Show confirm password'}
                >
                  {showSignUpConfirmPassword ? 'Hide' : 'Show'}
                </button>
              </div>
              {confirmPasswordError && (
                <span className="field-error">{confirmPasswordError}</span>
              )}
            </div>

            {/* Collapsed supervisor invite code link */}
            <div>
              <button
                type="button"
                className="invite-toggle-btn"
                onClick={() => setShowInviteInput(v => !v)}
              >
                {showInviteInput ? '− Hide invite code' : '+ Have a supervisor invite code?'}
              </button>

              {showInviteInput && (
                <div className="form-group">
                  <label className="login-label" htmlFor={inviteCodeId}>
                    Supervisor invite code
                  </label>
                  <input
                    id={inviteCodeId}
                    className="login-input"
                    type="password"
                    placeholder="Enter invite code"
                    value={inviteCode}
                    onChange={e => setInviteCode(e.target.value)}
                    autoComplete="off"
                  />
                </div>
              )}
            </div>

            <button type="submit" className="login-btn" disabled={signUpLoading}>
              {signUpLoading ? 'Creating account…' : 'Sign up'}
            </button>
          </form>
        )}

        {/* Note on page per requirement 13 */}
        <div className="demo-system-note">
          Demo system, synthetic data only.
        </div>
      </div>
    </div>
  )
}
