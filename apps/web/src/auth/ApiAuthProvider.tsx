import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { AuthContext } from './context'
import { AuthError, type AuthUser, type AuthContextValue } from './types'
import { decodeAccessTokenSubject } from './jwt'
import { readCachedProfile, writeCachedProfile } from './profileCache'
import { subscribeToAccessToken } from './tokenStore'
import { ApiError, login, logout, refreshAccessToken, register } from '../api/client'

/**
 * The real implementation: `InMemoryAuthProvider`'s replacement, talking to the self-hosted API
 * from OP-55 instead of a `Map` in this tab's memory.
 *
 * Two things carry over unchanged from that placeholder, and both are load-bearing rather than
 * incidental:
 *
 * `checking` still exists, for the same reason it always did — session restore is asynchronous
 * (here, an actual network round trip rather than a resolved `Promise.resolve()`), and rendering
 * "signed out" during that window would flash the login page at a user who is in fact signed in.
 *
 * Client-side email/password validation still runs before any request, so a malformed email or a
 * too-short password never reaches the network — matching what OP-13's tests already expect and
 * sparing a round trip for input the server would reject anyway.
 *
 * What's different, and worth naming: the API has no endpoint that returns a user's profile.
 * `TokenResponse` carries only `access_token` — no email, no display name — and the JWT's only
 * claim is `sub`, the user id (security/tokens.py). So a session restored purely from the refresh
 * cookie knows *who* signed in but not what to call them. `profileCache.ts` papers over that with
 * a same-id cache of the last email/display name this tab saw at sign-in — a real value the
 * moment someone signs in or up, an id-only fallback the moment a *different* tab's cookie is
 * restored here. A `/users/me` route would remove the need for this cache entirely — worth a
 * follow-up ticket rather than solving here.
 */

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
const MINIMUM_PASSWORD_LENGTH = 8

function userFromToken(token: string, email: string, displayName: string | null): AuthUser | null {
  const id = decodeAccessTokenSubject(token)
  return id === null ? null : { id, email, displayName }
}

export function ApiAuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [checking, setChecking] = useState(true)

  // Session restore. Runs once, on mount: exchange whatever refresh cookie the browser is
  // holding for an access token. A page load with no valid cookie resolves to `null` here, which
  // is indistinguishable from — and handled by — the subscription below.
  useEffect(() => {
    let cancelled = false
    void refreshAccessToken().then((token) => {
      // Checked first and alone: if the component has already unmounted, nothing below —
      // including `setChecking(false)` — may run. Folding this into the `token === null` branch
      // below would still call `setChecking` on an unmounted component whenever unmount and "no
      // session" land in the same tick.
      if (cancelled) return
      if (token === null) {
        setChecking(false)
        return
      }
      const cached = readCachedProfile()
      const id = decodeAccessTokenSubject(token)
      const restored: AuthUser | null =
        id === null
          ? null
          : cached !== null && cached.id === id
            ? cached
            : { id, email: '', displayName: null }
      if (restored !== null) {
        writeCachedProfile(restored)
        setUser(restored)
      }
      setChecking(false)
    })
    return () => {
      cancelled = true
    }
  }, [])

  // The other half of "a failed refresh logs out cleanly rather than looping": whenever the token
  // store reports the token is gone — mount-time restore failing, a mid-session refresh failing
  // after api/client.ts's interceptor tries it, or this provider's own `signOut` — the signed-in
  // user disappears too. One subscription covers all three, so there is exactly one place that
  // decides what "signed out" means.
  useEffect(() => {
    return subscribeToAccessToken((token) => {
      if (token === null) {
        writeCachedProfile(null)
        setUser(null)
      }
    })
  }, [])

  const signIn = useCallback(async (email: string, password: string) => {
    const normalized = email.trim().toLowerCase()
    if (!EMAIL_PATTERN.test(normalized)) throw new AuthError('invalid-email')

    let tokens
    try {
      tokens = await login(normalized, password)
    } catch (err) {
      // One error for every *credentials* rejection, on purpose — see `_INVALID_CREDENTIALS` in
      // the API's auth.py. Distinguishing "no such account" from "wrong password" here would
      // reintroduce the account-existence oracle that constant-time verification closes
      // server-side, so both collapse to the same 401 and the same `invalid-credentials`.
      //
      // Checked by status, not just `instanceof ApiError`: `login` and `register` share one
      // schema, so a password Pydantic rejects (422) or a rate limit this account just tripped
      // (429, OP-59) are also `ApiError`s — and "invalid credentials" would be a wrong, unhelpful
      // thing to tell someone who typed a fine password too many times.
      throw err instanceof ApiError && err.status === 401
        ? new AuthError('invalid-credentials')
        : new AuthError('unknown')
    }

    const cached = readCachedProfile()
    const id = decodeAccessTokenSubject(tokens.access_token)
    const nextUser: AuthUser | null =
      id === null
        ? null
        : {
            id,
            email: normalized,
            displayName: cached !== null && cached.id === id ? cached.displayName : null,
          }
    if (nextUser === null) throw new AuthError('unknown')

    writeCachedProfile(nextUser)
    setUser(nextUser)
  }, [])

  const signUp = useCallback(async (email: string, password: string, displayName: string) => {
    const normalized = email.trim().toLowerCase()
    if (!EMAIL_PATTERN.test(normalized)) throw new AuthError('invalid-email')
    if (password.length < MINIMUM_PASSWORD_LENGTH) throw new AuthError('weak-password')

    let tokens
    try {
      tokens = await register(normalized, password)
    } catch (err) {
      if (err instanceof ApiError && err.status === 409)
        throw new AuthError('email-already-registered')
      // The server's real minimum is 12 characters (`schemas.py`'s `MIN_PASSWORD_LENGTH`); this
      // form's own check above only enforces 8. A password of 8–11 characters clears the client
      // check, reaches the server, and comes back 422 — which belongs in the same bucket as a
      // password this form rejected itself, not lumped in with "unknown".
      if (err instanceof ApiError && err.status === 422) throw new AuthError('weak-password')
      throw new AuthError('unknown')
    }

    const trimmedName = displayName.trim()
    const nextUser = userFromToken(
      tokens.access_token,
      normalized,
      trimmedName === '' ? null : trimmedName,
    )
    if (nextUser === null) throw new AuthError('unknown')

    writeCachedProfile(nextUser)
    setUser(nextUser)
  }, [])

  const signOut = useCallback(async () => {
    // logout() clears the token store on its way out (even if the network call itself fails),
    // which the subscription above turns into `setUser(null)` — so there is nothing left to do
    // here beyond making the request.
    await logout()
  }, [])

  const value = useMemo<AuthContextValue>(
    () => ({ user, checking, signIn, signUp, signOut }),
    [user, checking, signIn, signUp, signOut],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
