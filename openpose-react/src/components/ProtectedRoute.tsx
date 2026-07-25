import { useEffect, useState, type ReactNode } from 'react'
import { Navigate } from 'react-router-dom'
import { onAuthStateChanged, type User } from 'firebase/auth'
import { auth } from '../firebase'

/**
 * React's answer to the Vue router's global `beforeEach` guard.
 *
 * Vue centralizes this: routes carry `meta: { requiresAuth: true }` and one
 * `router.beforeEach` hook inspects every navigation before it happens.
 *
 * React Router has no global navigation guard. Instead you wrap the protected
 * element in a component that decides what to render — so the guard is
 * *compositional* rather than configured, and it runs during render rather than
 * before navigation. Practical upshot: the component mounts, then redirects,
 * so you need an explicit loading state to avoid flashing the wrong UI.
 */
export default function ProtectedRoute({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [checking, setChecking] = useState(true)

  useEffect(() => {
    const unsubscribe = onAuthStateChanged(auth, (u) => {
      setUser(u)
      setChecking(false)
    })
    return unsubscribe
  }, [])

  if (checking) return <p style={{ textAlign: 'center' }}>Checking sign-in…</p>

  // The Vue guard pops an alert() then redirects. Redirecting with `replace`
  // keeps the blocked page out of history so Back doesn't bounce the user.
  if (!user) return <Navigate to="/login" replace />

  return <>{children}</>
}
