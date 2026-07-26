import { describe, expect, it } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { AuthProvider, AuthError, useAuth } from '.'

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

describe('useAuth', () => {
  it('refuses to work outside a provider', () => {
    // Without the null default this would silently report "signed out" and the symptom would be
    // an unexplained redirect to /login. See the note in auth/context.ts.
    expect(() => renderHook(() => useAuth())).toThrow(/must be used inside an <AuthProvider>/)
  })
})

describe('session restore', () => {
  it('reports checking until the initial restore settles', async () => {
    const { result } = renderHook(() => useAuth(), { wrapper })

    // The window ProtectedRoute's loading state exists for. If this ever became synchronous the
    // guard would look unnecessary, and would then break when Epic E makes it a network call.
    expect(result.current.checking).toBe(true)
    await waitFor(() => {
      expect(result.current.checking).toBe(false)
    })
    expect(result.current.user).toBeNull()
  })

  it('restores a session written by a previous page load', async () => {
    window.sessionStorage.setItem(
      'openposture.session',
      JSON.stringify({ id: 'u1', email: 'ada@example.com', displayName: 'Ada' }),
    )

    const { result } = await renderAuth()

    expect(result.current.user).toEqual({
      id: 'u1',
      email: 'ada@example.com',
      displayName: 'Ada',
    })
  })

  it('treats unreadable stored session data as no session', async () => {
    window.sessionStorage.setItem('openposture.session', 'not json')

    // Corrupt storage must not stop the provider mounting — that would take down the whole app
    // over a value the user cannot see or clear.
    const { result } = await renderAuth()

    expect(result.current.user).toBeNull()
  })
})

describe('signUp', () => {
  it('creates an account, signs the user in and persists the session', async () => {
    const { result } = await renderAuth()

    await act(async () => {
      await result.current.signUp(...CREDENTIALS, 'Ada Lovelace')
    })

    expect(result.current.user).toMatchObject({
      email: 'ada@example.com',
      displayName: 'Ada Lovelace',
    })
    expect(window.sessionStorage.getItem('openposture.session')).toContain('ada@example.com')
  })

  it('has the display name available immediately, not one render later', async () => {
    const { result } = await renderAuth()

    await act(async () => {
      await result.current.signUp(...CREDENTIALS, 'Ada Lovelace')
    })

    // The Firebase version could navigate before updateProfile resolved, so Dashboard rendered
    // "Hello, undefined". The name is part of the user object from creation now.
    expect(result.current.user?.displayName).toBe('Ada Lovelace')
  })

  it('stores a blank display name as null rather than an empty string', async () => {
    const { result } = await renderAuth()

    await act(async () => {
      await result.current.signUp(...CREDENTIALS, '   ')
    })

    // So Dashboard's `?? 'there'` fallback actually fires. An empty string is truthy enough to
    // slip past it and render "Hello, ".
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
  ])('rejects %s with %s', async (email, code) => {
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

  it('rejects a malformed email before looking anything up', async () => {
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

    // Not cosmetic: distinguishing these lets an attacker enumerate registered addresses. Epic E
    // must keep this property, and this test is what will tell it if it does not.
    await expect(wrongPassword).rejects.toMatchObject({ code: 'invalid-credentials' })
    await expect(noSuchAccount).rejects.toMatchObject({ code: 'invalid-credentials' })
  })
})

describe('signOut', () => {
  it('clears both the user and the stored session', async () => {
    const { result } = await renderAuth()
    await act(async () => {
      await result.current.signUp(...CREDENTIALS, 'Ada')
    })

    await act(async () => {
      await result.current.signOut()
    })

    expect(result.current.user).toBeNull()
    expect(window.sessionStorage.getItem('openposture.session')).toBeNull()
  })
})
