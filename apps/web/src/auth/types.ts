/**
 * The auth contract the UI is written against.
 *
 * Nothing here mentions how authentication actually happens. That is the point: OP-13 ships an
 * in-memory implementation so the app builds and can be tested, and Epic E (OP-30) replaces it
 * with one that talks to the self-hosted API. If this file has to change when that happens, the
 * boundary was drawn in the wrong place.
 *
 * The previous version had no boundary at all — `firebase/auth` was imported directly by App,
 * ProtectedRoute, Login, Registration and Dashboard, so the vendor reached into five components
 * and the app could not render without a live Firebase project.
 */

/** The subset of a user the UI needs. Deliberately smaller than any provider's user object. */
export interface AuthUser {
  id: string
  email: string
  displayName: string | null
}

/**
 * Failure codes the UI is allowed to branch on.
 *
 * Our own vocabulary, not a passthrough of the provider's. Firebase's `auth/wrong-password` was
 * being switch-ed on in Login, which quietly made the login form depend on Firebase's error
 * taxonomy — a second coupling that the import list did not reveal.
 */
export type AuthErrorCode =
  'invalid-email' | 'invalid-credentials' | 'email-already-registered' | 'weak-password' | 'unknown'

export class AuthError extends Error {
  // Declared and assigned rather than a `constructor(readonly code)` parameter property:
  // parameter properties emit real code, which `erasableSyntaxOnly` forbids because the build
  // strips types rather than compiling them.
  readonly code: AuthErrorCode

  constructor(code: AuthErrorCode) {
    super(code)
    this.name = 'AuthError'
    this.code = code
  }
}

export interface AuthContextValue {
  /** The signed-in user, or null. Meaningless while `checking` is true. */
  user: AuthUser | null
  /**
   * True until the initial session restore settles.
   *
   * Kept from the Firebase version, where it existed because `onAuthStateChanged` fires
   * asynchronously. It is still needed for the same reason: any real implementation has to ask
   * a server or storage whether there is a session, and rendering "signed out" during that
   * window flashes the login page at users who are in fact signed in.
   */
  checking: boolean
  signIn(email: string, password: string): Promise<void>
  signUp(email: string, password: string, displayName: string): Promise<void>
  signOut(): Promise<void>
}
