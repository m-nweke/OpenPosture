import { createContext, useContext } from 'react'
import type { AuthContextValue } from './types'

/**
 * Separate from the provider component on purpose: oxlint's `react/only-export-components`
 * flags modules that export both components and non-components, because mixing them breaks
 * React Fast Refresh. The context object and the hook live here, the component lives next door.
 */
export const AuthContext = createContext<AuthContextValue | null>(null)

/**
 * The only supported way to read auth state.
 *
 * The null default is load-bearing. A context defaulting to a signed-out user would let a
 * component render outside the provider and silently behave as if the user were logged out —
 * the failure would surface as a redirect to /login, which looks like an auth bug rather than
 * the wiring bug it is. Throwing turns that into an immediate, named error.
 */
export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext)
  if (value === null) {
    throw new Error('useAuth must be used inside an <AuthProvider>')
  }
  return value
}
