import type { ReactNode } from 'react'
import { Navigate } from 'react-router-dom'
import { useAuth } from '../auth'

/**
 * React's answer to the Vue router's global `beforeEach` guard.
 *
 * Vue centralizes this: routes carry `meta: { requiresAuth: true }` and one `router.beforeEach`
 * hook inspects every navigation before it happens.
 *
 * React Router has no global navigation guard. Instead you wrap the protected element in a
 * component that decides what to render — so the guard is *compositional* rather than
 * configured, and it runs during render rather than before navigation. Practical upshot: the
 * component mounts, then redirects, so you need an explicit loading state to avoid flashing the
 * wrong UI.
 *
 * The subscription this used to own is gone. It duplicated the one in App, so two components
 * held two independent copies of the same auth state and could disagree mid-transition. There
 * is now one source: the provider.
 */
export default function ProtectedRoute({ children }: { children: ReactNode }) {
  const { user, checking } = useAuth()

  if (checking) return <p style={{ textAlign: 'center' }}>Checking sign-in…</p>

  // The Vue guard pops an alert() then redirects. Redirecting with `replace`
  // keeps the blocked page out of history so Back doesn't bounce the user.
  if (user === null) return <Navigate to="/login" replace />

  return <>{children}</>
}
