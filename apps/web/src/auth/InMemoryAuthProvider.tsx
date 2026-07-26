import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { AuthContext } from './context'
import { AuthError, type AuthUser, type AuthContextValue } from './types'

/**
 * A real, working auth implementation that happens to have no backend.
 *
 * This is a placeholder for Epic E (OP-30), not a no-op. Registration genuinely creates an
 * account, sign-in genuinely rejects a wrong password, and the session genuinely survives a page
 * reload. That matters for two reasons: the app is demoable end to end before the API exists,
 * and the component tests exercise the same paths the real provider will take instead of
 * asserting against stubs that always resolve.
 *
 * What it is NOT: secure. Accounts live in a Map in this tab's memory and passwords are compared
 * in plaintext. Nothing here should survive Epic E — the file gets deleted, `ApiAuthProvider`
 * takes its place, and no component changes because both satisfy `AuthContextValue`.
 */

/** Where the *session* is kept — never the credentials. See `restoreSession` below. */
const SESSION_KEY = 'openposture.session'

interface Account {
  user: AuthUser
  password: string
}

/** Mirrors what a server would reject before ever checking a password. */
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
const MINIMUM_PASSWORD_LENGTH = 8

function readStoredSession(): AuthUser | null {
  try {
    const raw = window.sessionStorage.getItem(SESSION_KEY)
    return raw === null ? null : (JSON.parse(raw) as AuthUser)
  } catch {
    // Private browsing, a disabled storage API or corrupt JSON. Treat all three as "no session"
    // rather than letting the provider fail to mount and take the whole app down with it.
    return null
  }
}

function writeStoredSession(user: AuthUser | null): void {
  try {
    if (user === null) {
      window.sessionStorage.removeItem(SESSION_KEY)
    } else {
      window.sessionStorage.setItem(SESSION_KEY, JSON.stringify(user))
    }
  } catch {
    // Best effort. A session that does not survive reload is a worse experience, not a broken one.
  }
}

export function InMemoryAuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [checking, setChecking] = useState(true)

  // Accounts are not state: changing them must never re-render, and they are read inside
  // callbacks rather than during render. useRef is React's box for exactly that.
  const accounts = useRef(new Map<string, Account>())

  // Session restore, deliberately asynchronous.
  //
  // sessionStorage could be read synchronously, and then `checking` would flip to false in the
  // same commit as the first render — never actually observable. That would make
  // ProtectedRoute's loading state look like dead code, someone would delete it, and it would
  // be needed again the moment Epic E replaces this with a fetch. Resolving a promise first
  // reproduces the one-render window a real implementation cannot avoid.
  useEffect(() => {
    let cancelled = false
    void Promise.resolve().then(() => {
      // Guards against a state update after unmount, which React warns about and which a real
      // provider would hit whenever a user navigates away mid-request.
      if (cancelled) return
      setUser(readStoredSession())
      setChecking(false)
    })
    return () => {
      cancelled = true
    }
  }, [])

  const signIn = useCallback(async (email: string, password: string) => {
    const normalized = email.trim().toLowerCase()
    if (!EMAIL_PATTERN.test(normalized)) throw new AuthError('invalid-email')

    const account = accounts.current.get(normalized)
    // One error for "no such account" and for "wrong password", on purpose: distinguishing them
    // tells an attacker which addresses are registered. Epic E must preserve this.
    if (account === undefined || account.password !== password) {
      throw new AuthError('invalid-credentials')
    }

    writeStoredSession(account.user)
    setUser(account.user)
  }, [])

  const signUp = useCallback(async (email: string, password: string, displayName: string) => {
    const normalized = email.trim().toLowerCase()
    if (!EMAIL_PATTERN.test(normalized)) throw new AuthError('invalid-email')
    if (password.length < MINIMUM_PASSWORD_LENGTH) throw new AuthError('weak-password')
    if (accounts.current.has(normalized)) throw new AuthError('email-already-registered')

    const trimmedName = displayName.trim()
    const newUser: AuthUser = {
      id: crypto.randomUUID(),
      email: normalized,
      displayName: trimmedName === '' ? null : trimmedName,
    }
    accounts.current.set(normalized, { user: newUser, password })

    // The Firebase version navigated to /dashboard in one branch before `updateProfile` had
    // resolved, so the dashboard could render "Hello, undefined". Here the display name is part
    // of the user object from the moment it exists, so that race cannot be reintroduced.
    writeStoredSession(newUser)
    setUser(newUser)
  }, [])

  const signOut = useCallback(async () => {
    writeStoredSession(null)
    setUser(null)
  }, [])

  const value = useMemo<AuthContextValue>(
    () => ({ user, checking, signIn, signUp, signOut }),
    [user, checking, signIn, signUp, signOut],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
