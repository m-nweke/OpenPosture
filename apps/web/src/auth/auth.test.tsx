import { beforeEach, describe, expect, it } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { http, HttpResponse } from 'msw'
import { AuthProvider, AuthError, useAuth } from '.'
import { AUTH_ENDPOINT } from '../api/client'
import { setAccessToken } from './tokenStore'
import { installFakeAuthApi } from '../test/fakeAuthApi'
import { mockSignedIn } from '../test/authFixtures'
import { server } from '../test/mswServer'

function wrapper({ children }: { children: ReactNode }) {
  return <AuthProvider>{children}</AuthProvider>
}

/** Renders `useAuth` and waits out the initial session restore. */
async function renderAuth() {
  const view = renderHook(() => useAuth(), { wrapper })
  await waitFor(() => {
    expect(view.result.current.checking).toBe(false)
  })
  return view
}

const CREDENTIALS = ['ada@example.com', 'correct-horse'] as const

// `installFakeAuthApi` gives `signUp`/`signIn` a real register/login round trip to exercise —
// see the file for why ApiAuthProvider has no in-memory account map of its own to test against.
beforeEach(() => {
  installFakeAuthApi()
})

describe('useAuth', () => {
  it('refuses to work outside a provider', () => {
    // Without the null default this would silently report "signed out" and the symptom would be
    // an unexplained redirect to /login. See the note in auth/context.ts.
    expect(() => renderHook(() => useAuth())).toThrow(/must be used inside an <AuthProvider>/)
  })
})

describe('session restore', () => {
  it('reports checking until the initial refresh call settles', async () => {
    const { result } = renderHook(() => useAuth(), { wrapper })

    // The window ProtectedRoute's loading state exists for. If this ever became synchronous the
    // guard would look unnecessary, and would then break the moment the network round trip it is
    // hiding actually takes any real time.
    expect(result.current.checking).toBe(true)
    await waitFor(() => {
      expect(result.current.checking).toBe(false)
    })
    expect(result.current.user).toBeNull()
  })

  it('restores a session from a still-valid refresh cookie', async () => {
    mockSignedIn({ id: 'u1', email: 'ada@example.com', displayName: 'Ada' })

    const { result } = await renderAuth()

    expect(result.current.user).toEqual({
      id: 'u1',
      email: 'ada@example.com',
      displayName: 'Ada',
    })
  })

  it('treats a rejected refresh as no session', async () => {
    // The default handler in test/handlers.ts already does this — asserted explicitly here so
    // the behaviour has a test that names it, rather than relying on every other test's silence.
    const { result } = await renderAuth()

    expect(result.current.user).toBeNull()
  })

  it('treats a network failure during restore as no session, not a hang', async () => {
    server.use(http.post(`${AUTH_ENDPOINT}/refresh`, () => HttpResponse.error()))

    const { result } = await renderAuth()

    expect(result.current.user).toBeNull()
  })
})

describe('signUp', () => {
  it('creates an account and signs the user in', async () => {
    const { result } = await renderAuth()

    await act(async () => {
      await result.current.signUp(...CREDENTIALS, 'Ada Lovelace')
    })

    expect(result.current.user).toMatchObject({
      email: 'ada@example.com',
      displayName: 'Ada Lovelace',
    })
  })

  it('has the display name available immediately, not one render later', async () => {
    const { result } = await renderAuth()

    await act(async () => {
      await result.current.signUp(...CREDENTIALS, 'Ada Lovelace')
    })

    expect(result.current.user?.displayName).toBe('Ada Lovelace')
  })

  it('stores a blank display name as null rather than an empty string', async () => {
    const { result } = await renderAuth()

    await act(async () => {
      await result.current.signUp(...CREDENTIALS, '   ')
    })

    expect(result.current.user?.displayName).toBeNull()
  })

  it('normalises the email so case and padding do not create separate accounts', async () => {
    const { result } = await renderAuth()

    await act(async () => {
      await result.current.signUp('  Ada@Example.COM ', 'correct-horse', 'Ada')
    })

    expect(result.current.user?.email).toBe('ada@example.com')
  })

  it.each([
    ['not-an-email', 'invalid-email'],
    ['ada@example.com', 'weak-password'],
  ])('rejects %s with %s, before any request is made', async (email, code) => {
    const { result } = await renderAuth()
    const password = code === 'weak-password' ? 'short' : 'correct-horse'

    await expect(result.current.signUp(email, password, 'Ada')).rejects.toThrow(AuthError)
    expect(result.current.user).toBeNull()
  })

  it('rejects a second registration for the same email', async () => {
    const { result } = await renderAuth()
    await act(async () => {
      await result.current.signUp(...CREDENTIALS, 'Ada')
    })

    await expect(
      result.current.signUp('ADA@example.com', 'other-password', 'Imposter'),
    ).rejects.toMatchObject({ code: 'email-already-registered' })
  })
})

describe('signIn', () => {
  it('signs in with the credentials used at registration', async () => {
    const { result } = await renderAuth()
    await act(async () => {
      await result.current.signUp(...CREDENTIALS, 'Ada')
      await result.current.signOut()
    })

    await act(async () => {
      await result.current.signIn(...CREDENTIALS)
    })

    expect(result.current.user?.email).toBe('ada@example.com')
  })

  it('rejects a malformed email before making a request', async () => {
    const { result } = await renderAuth()

    await expect(result.current.signIn('nope', 'correct-horse')).rejects.toMatchObject({
      code: 'invalid-email',
    })
  })

  it('gives the same error for an unknown account and a wrong password', async () => {
    const { result } = await renderAuth()
    await act(async () => {
      await result.current.signUp(...CREDENTIALS, 'Ada')
    })

    const wrongPassword = result.current.signIn('ada@example.com', 'guess')
    const noSuchAccount = result.current.signIn('grace@example.com', 'correct-horse')

    // Not cosmetic: distinguishing these lets an attacker enumerate registered addresses, the
    // same property the server's own constant-time check protects (auth.py's `_verify_credentials`).
    await expect(wrongPassword).rejects.toMatchObject({ code: 'invalid-credentials' })
    await expect(noSuchAccount).rejects.toMatchObject({ code: 'invalid-credentials' })
  })
})

describe('signOut', () => {
  it('clears the signed-in user', async () => {
    const { result } = await renderAuth()
    await act(async () => {
      await result.current.signUp(...CREDENTIALS, 'Ada')
    })

    await act(async () => {
      await result.current.signOut()
    })

    expect(result.current.user).toBeNull()
  })
})

describe('a session ended elsewhere', () => {
  it('signs the user out when the token store reports the token is gone', async () => {
    // The path api/client.ts's 401 interceptor takes when a refresh fails mid-session — see the
    // single-flight docstring on refreshAccessToken. Driving it through the token store directly
    // isolates "does the provider react correctly to session loss" from "does the interceptor
    // call the right function at the right time", which client.test.ts covers separately. This is
    // the acceptance criterion that a failed refresh logs the user out rather than leaving them
    // in limbo.
    const { result } = await renderAuth()
    await act(async () => {
      await result.current.signUp(...CREDENTIALS, 'Ada')
    })
    expect(result.current.user).not.toBeNull()

    act(() => {
      setAccessToken(null)
    })

    expect(result.current.user).toBeNull()
  })
})
