import { http, HttpResponse } from 'msw'
import type { RequestHandler } from 'msw'
import { AUTH_ENDPOINT } from '../api/client'

/**
 * Default request stubs, applied to every test by `setupTests.ts`.
 *
 * One handler, not zero: `ApiAuthProvider` calls `POST /auth/refresh` on mount, unconditionally,
 * to find out whether the browser is already holding a valid refresh cookie. Every test that
 * renders `AuthProvider` — which is most of them, via `renderWithProviders` — triggers that call
 * whether or not the test has anything to do with auth. Defaulting it to 401 models the ordinary
 * case, a fresh session with no cookie, so only the tests that care about a *restored* session
 * need to override it with `server.use(...)`. Everything else follows the existing rule: the
 * interesting part is always which response, and a default here would be a second answer nobody
 * asked for if it covered more than the one call every test makes regardless.
 *
 * Unhandled requests are an error rather than a warning (see `setupTests.ts`), so a component
 * that starts calling something new fails a test instead of passing quietly.
 */
export const handlers: RequestHandler[] = [
  http.post(`${AUTH_ENDPOINT}/refresh`, () =>
    HttpResponse.json(
      {
        type: 'https://openposture.dev/problems/unauthorized',
        title: 'Unauthorized',
        status: 401,
        detail: 'Your session has expired. Please sign in again.',
      },
      { status: 401 },
    ),
  ),
]
