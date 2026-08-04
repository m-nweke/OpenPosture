import type { AuthUser } from './types'

/**
 * Where the last known profile — id, email, display name — is cached between page loads.
 *
 * Never the token. See noTokenStorage.test.tsx: that test bans any credential-shaped key, and
 * this one is exempt because it holds no secret. It exists because the API has no `/users/me`
 * route yet — a restored session recovers only the `sub` claim (the user id) from the access
 * token, and email/display name have to come from somewhere. This is that somewhere. If the
 * cache is missing or belongs to a different user id than the restored token, callers fall back
 * to an id-only profile rather than trusting it.
 */
export const PROFILE_STORAGE_KEY = 'openposture.profile'

export function readCachedProfile(): AuthUser | null {
  try {
    const raw = window.sessionStorage.getItem(PROFILE_STORAGE_KEY)
    return raw === null ? null : (JSON.parse(raw) as AuthUser)
  } catch {
    // Private browsing, a disabled storage API, or corrupt JSON. Treat all three as "no cached
    // profile" rather than failing the provider's mount over a value the user cannot see.
    return null
  }
}

export function writeCachedProfile(user: AuthUser | null): void {
  try {
    if (user === null) {
      window.sessionStorage.removeItem(PROFILE_STORAGE_KEY)
    } else {
      window.sessionStorage.setItem(PROFILE_STORAGE_KEY, JSON.stringify(user))
    }
  } catch {
    // Best effort. A profile that does not survive reload degrades to an id-only user, not a
    // broken app.
  }
}
