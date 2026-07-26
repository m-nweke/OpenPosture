/**
 * The auth module's public surface. Components import from `../auth`, never from the files
 * inside it — so swapping `InMemoryAuthProvider` for Epic E's `ApiAuthProvider` is a one-line
 * change here plus one in `main.tsx`, and no component is touched at all.
 */
export { AuthContext, useAuth } from './context'
export { InMemoryAuthProvider as AuthProvider } from './InMemoryAuthProvider'
export { AuthError } from './types'
export type { AuthUser, AuthErrorCode, AuthContextValue } from './types'
