/**
 * The auth module's public surface. Components import from `../auth`, never from the files
 * inside it — so OP-57's swap from `InMemoryAuthProvider` to `ApiAuthProvider` was a one-line
 * change here plus one in `main.tsx`, and no component was touched at all.
 */
export { AuthContext, useAuth } from './context'
export { ApiAuthProvider as AuthProvider } from './ApiAuthProvider'
export { AuthError } from './types'
export type { AuthUser, AuthErrorCode, AuthContextValue } from './types'
