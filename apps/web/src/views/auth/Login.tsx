import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { AuthError, useAuth, type AuthErrorCode } from '../../auth'
import styles from './AuthForm.module.css'

/**
 * Maps failure codes to what the user reads.
 *
 * A lookup rather than the switch this used to be, because the switch was over *Firebase's*
 * error codes — `auth/wrong-password` and friends — which quietly made this form depend on the
 * vendor's error taxonomy. The codes are ours now, so Epic E can change transport without
 * touching copy.
 */
const MESSAGES: Record<AuthErrorCode, string> = {
  'invalid-email': 'Invalid email',
  // Deliberately does not distinguish "no such account" from "wrong password". Telling them
  // apart hands an attacker a way to enumerate which addresses are registered.
  'invalid-credentials': 'Email or password incorrect',
  'email-already-registered': 'That email is already registered',
  'weak-password': 'Password is too short',
  unknown: 'Something went wrong. Please try again.',
}

export default function Login() {
  // Vue's v-model is two-way binding. React has no two-way binding — you wire
  // `value` down and `onChange` back up by hand. That's the "controlled
  // component" pattern, and it's the single biggest day-to-day difference
  // between the two frameworks for form code.
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [errMsg, setErrMsg] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const navigate = useNavigate()
  const { signIn } = useAuth()

  const login = async () => {
    setSubmitting(true)
    try {
      await signIn(email, password)
      setErrMsg('')
      navigate('/dashboard')
    } catch (error) {
      // An unrecognised throw is a bug, not a credential problem — say so generically rather
      // than blaming the user's password for it.
      setErrMsg(error instanceof AuthError ? MESSAGES[error.code] : MESSAGES.unknown)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className={styles.container}>
      <h1>Log into your PostureProfile</h1>
      <form
        className={styles.form}
        onSubmit={(e) => {
          // A <form> rather than a bare button: this is what makes Enter submit, what lets the
          // browser offer password autofill, and what associates the fields as one group for
          // screen readers. The original had three inputs and a click handler, so none of that
          // worked.
          e.preventDefault()
          void login()
        }}
      >
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
              autoComplete="current-password"
              placeholder="Enter your password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
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
        {/* role="alert" so the error is announced when it appears, not just drawn. */}
        {errMsg !== '' && (
          <p className={styles.error} role="alert">
            {errMsg}
          </p>
        )}
        <button type="submit" className="button buttonPrimary" disabled={submitting}>
          {submitting ? 'Logging in…' : 'Log in'}
        </button>
      </form>
      <p className={styles.signInHere}>
        New here? <Link to="/register">Create an account</Link>.
      </p>
    </div>
  )
}
