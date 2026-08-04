import { http, HttpResponse } from 'msw'
import { AUTH_ENDPOINT } from '../api/client'
import type { CredentialsRequest } from '../api/types'
import { server } from './mswServer'
import { fakeAccessToken } from './authFixtures'

/**
 * A stateful stand-in for the register/login routes, for tests that exercise `signUp`/`signIn`
 * end to end rather than mocking one response at a time.
 *
 * `InMemoryAuthProvider` used to be this: a `Map` of accounts inside the provider itself, so the
 * app could be demoed and tested before OP-55's API existed. `ApiAuthProvider` has no such Map —
 * accounts genuinely live in Postgres now — so the tests that relied on that behaviour (register,
 * sign out, sign back in; reject a duplicate email) need it here instead, at the network boundary
 * mocked in every other test in this suite.
 *
 * This intentionally does not model rotation or family revocation — `/refresh` is not part of it,
 * and stays whatever `handlers.ts` or a test's own `server.use` says. Those properties are
 * `refreshAccessToken`'s to test (see client.test.ts), not `signUp`/`signIn`'s.
 */

interface FakeAccount {
  id: string
  password: string
}

let accounts = new Map<string, FakeAccount>()
let nextId = 1

export function installFakeAuthApi(): void {
  accounts = new Map()
  nextId = 1

  server.use(
    http.post(`${AUTH_ENDPOINT}/register`, async ({ request }) => {
      const body = (await request.json()) as CredentialsRequest
      const email = body.email.trim().toLowerCase()

      if (accounts.has(email)) {
        return HttpResponse.json(
          {
            type: 't',
            title: 'Conflict',
            status: 409,
            detail: 'That email is already registered.',
          },
          { status: 409 },
        )
      }

      const id = String(nextId++)
      accounts.set(email, { id, password: body.password })
      return HttpResponse.json(
        { access_token: fakeAccessToken(id), token_type: 'bearer', expires_in: 900 },
        { status: 201 },
      )
    }),

    http.post(`${AUTH_ENDPOINT}/login`, async ({ request }) => {
      const body = (await request.json()) as CredentialsRequest
      const email = body.email.trim().toLowerCase()
      const account = accounts.get(email)

      // One response for "no such account" and "wrong password" alike, matching the server's own
      // refusal to distinguish them (auth.py's `_INVALID_CREDENTIALS`).
      if (account === undefined || account.password !== body.password) {
        return HttpResponse.json(
          {
            type: 't',
            title: 'Unauthorized',
            status: 401,
            detail: 'Email or password is incorrect.',
          },
          { status: 401 },
        )
      }

      return HttpResponse.json({
        access_token: fakeAccessToken(account.id),
        token_type: 'bearer',
        expires_in: 900,
      })
    }),

    http.post(`${AUTH_ENDPOINT}/logout`, () => new HttpResponse(null, { status: 204 })),
  )
}
