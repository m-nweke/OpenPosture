import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { AuthError, useAuth, type AuthErrorCode } from '../../auth'
import styles from './AuthForm.module.css'

const MESSAGES: Record<AuthErrorCode, string> = {
  'invalid-email': 'Please enter a valid email address',
  'invalid-credentials': 'Email or password incorrect',
  'email-already-registered': 'An account with that email already exists',
  'weak-password': 'Password must be at least 8 characters',
  unknown: 'Something went wrong. Please try again.',
}

export default function Registration() {
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [errMsg, setErrMsg] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const navigate = useNavigate()
  const { signUp } = useAuth()

  const register = async () => {
    setSubmitting(true)
    try {
      // One await, one navigation. The Firebase version called `router.push('/dashboard')` both
      // inside the `.then()` and again immediately after, so it could navigate before the
      // display name was saved and render "Hello, undefined". The name is part of the user
      // object the moment it exists now, so there is no window in which it can be missing.
      await signUp(email, password, name)
      setErrMsg('')
      navigate('/dashboard')
    } catch (error) {
      // Was `alert(error.message)`, which leaked raw provider text into a modal the user had to
      // dismiss. Inline and in our own words instead.
      setErrMsg(error instanceof AuthError ? MESSAGES[error.code] : MESSAGES.unknown)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className={styles.container}>
      <h1>Create a Posture Profile</h1>
      <form
        className={styles.form}
        onSubmit={(e) => {
          e.preventDefault()
          void register()
        }}
      >
        <div className={styles.formGroup}>
          <label htmlFor="name">Name:</label>
          <input
            type="text"
            id="name"
            autoComplete="name"
            placeholder="Enter your name"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        <div className={styles.formGroup}>
          <label htmlFor="email">Email:</label>
          <input
            type="email"
            id="email"
            autoComplete="email"
            placeholder="Enter your email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <div className={styles.formGroup}>
          <label htmlFor="password">Password:</label>
          <div className={styles.passwordField}>
            <input
              type={showPassword ? 'text' : 'password'}
              id="password"
              autoComplete="new-password"
              placeholder="At least 8 characters"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            {/* A button, not a checkbox with an eye glyph: it performs an action rather than
                recording a preference. `aria-pressed` is what tells a screen reader which state
                it is currently in, and the label says what clicking will do. */}
            <button
              type="button"
              className={styles.passwordToggle}
              onClick={() => setShowPassword((shown) => !shown)}
              aria-pressed={showPassword}
              aria-controls="password"
            >
              {showPassword ? 'Hide' : 'Show'}
            </button>
          </div>
        </div>
        {errMsg !== '' && (
          <p className={styles.error} role="alert">
            {errMsg}
          </p>
        )}
        <button type="submit" className="button buttonPrimary" disabled={submitting}>
          {submitting ? 'Creating account…' : 'Create account'}
        </button>
      </form>
      <p className={styles.signInHere}>
        Already have an account? <Link to="/login">Sign in here</Link>.
      </p>
    </div>
  )
}
