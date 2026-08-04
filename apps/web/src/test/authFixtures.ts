import { http, HttpResponse } from 'msw'
import { AUTH_ENDPOINT } from '../api/client'
import { PROFILE_STORAGE_KEY } from '../auth/profileCache'
import type { AuthUser } from '../auth'
import { server } from './mswServer'

/**
 * Builds a token shaped enough for `decodeAccessTokenSubject` to read back: three base64url
 * segments, `sub` in the middle one. Not a real JWT — nothing here is signed, and nothing in the
 * client verifies a signature (that is the server's job) — so an unsigned stand-in is exactly as
 * useful to these tests as a real one.
 */
export function fakeAccessToken(userId: string): string {
  const now = Math.floor(Date.now() / 1000)
  const header = toBase64Url({ alg: 'none', typ: 'JWT' })
  const payload = toBase64Url({ sub: userId, iat: now, exp: now + 900 })
  return `${header}.${payload}.test-signature`
}

function toBase64Url(claims: Record<string, unknown>): string {
  return btoa(JSON.stringify(claims)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

/**
 * Simulates a page load where the refresh cookie is still valid: `POST /auth/refresh` succeeds
 * with a token for `user.id`, and the profile cache (see profileCache.ts — a non-credential, not
 * covered by noTokenStorage.test.tsx's ban) is seeded so the restored session knows the email and
 * display name a bare access token cannot carry.
 */
export function mockSignedIn(user: AuthUser): void {
  window.sessionStorage.setItem(PROFILE_STORAGE_KEY, JSON.stringify(user))
  server.use(
    http.post(`${AUTH_ENDPOINT}/refresh`, () =>
      HttpResponse.json({
        access_token: fakeAccessToken(user.id),
        token_type: 'bearer',
        expires_in: 900,
      }),
    ),
  )
}
