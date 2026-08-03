import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { AuthProvider, useAuth } from '.'

/**
 * OP-55: no token may ever reach `localStorage` or `sessionStorage`.
 *
 * The reason is XSS. Anything in web storage is readable by any script that runs on the page, so
 * one injected script turns into a stolen credential — and if that credential is a 30-day refresh
 * token, into permanent account control. The API side of this is settled by construction: the
 * refresh token is delivered `HttpOnly`, so JavaScript cannot read it even deliberately, and it
 * appears in no response body. What remains is the access token, which the client *does* hold,
 * and which must live in a variable that dies with the tab.
 *
 * Two layers here, because they fail in different ways:
 *
 *   1. A scan of the source tree, which catches a `localStorage.setItem` written anywhere —
 *      including in a component nobody thought to write an auth test for.
 *   2. A runtime spy over the real flows, which catches a token reaching storage through a
 *      helper, a library, or a key the scan does not recognise.
 *
 * Note this does not forbid web storage outright. The provider legitimately keeps the *user
 * profile* — id, email, display name — in `sessionStorage` so a reload does not flash the login
 * page. None of that is a credential. The rule is about tokens, and a blanket ban would be both
 * wrong and easy to work around.
 */

/**
 * Every application source file, as text.
 *
 * `import.meta.glob` with `?raw` rather than `node:fs`, because this file is compiled by
 * `tsconfig.app.json`, whose `types` deliberately exclude Node — browser code has no business
 * reaching for the filesystem, and adding `"node"` there to satisfy one test would remove that
 * guarantee for everything else. Vite resolves this at build time and `vite/client` types it, so
 * the scan works with the config exactly as it is.
 */
const SOURCES: Record<string, string> = import.meta.glob('../**/*.{ts,tsx}', {
  query: '?raw',
  import: 'default',
  eager: true,
})

/** Values that must never be handed to a storage API, however they are spelled. */
const TOKEN_KEY_PATTERN = /token|jwt|bearer|credential|password|secret/i

/** `header.payload.signature` — the shape of a JWT, regardless of the key it is stored under. */
const JWT_PATTERN = /^[\w-]+\.[\w-]+\.[\w-]+$/

/**
 * The application's own source, excluding tests.
 *
 * Test files are excluded deliberately: `auth.test.tsx` seeds `sessionStorage` to prove a session
 * survives a reload, and this file names both APIs in order to forbid them.
 */
function applicationSources(): Array<[string, string]> {
  return Object.entries(SOURCES).filter(([path]) => !/\.test\.tsx?$/.test(path))
}

describe('no credential reaches web storage', () => {
  it('never mentions localStorage anywhere in the application source', () => {
    // The stronger half of the rule, and the cheaper one to keep true. `sessionStorage` at least
    // dies with the tab; `localStorage` persists indefinitely, so a token written there outlives
    // every session that could have justified it. Nothing in this app has a reason to use it.
    const offenders = applicationSources()
      .filter(([, source]) => source.includes('localStorage'))
      .map(([path]) => path)

    expect(offenders, `localStorage is used in: ${offenders.join(', ')}`).toEqual([])
  })

  it('never writes a credential-shaped key to sessionStorage', () => {
    // The *key argument* of each `setItem` call, not the whole file. A file-wide search matches
    // `InMemoryAuthProvider`, which mentions `sessionStorage` and separately declares a
    // `password` field — two unrelated facts that a coarse scan reads as one problem. A check
    // that cries wolf gets deleted, so it has to look at what is actually being stored.
    const offenders = applicationSources().flatMap(([path, source]) =>
      [...source.matchAll(/(?:local|session)Storage\.setItem\(\s*([^,]+)/g)]
        // `match[1]` is optional to TypeScript because a group *can* be unmatched; here the
        // group is not optional in the pattern, so `?? ''` states that rather than asserting it.
        .map((match) => (match[1] ?? '').trim())
        .filter((key) => TOKEN_KEY_PATTERN.test(key))
        .map((key) => `${path}: ${key}`),
    )

    expect(offenders, `a credential may be stored in: ${offenders.join(', ')}`).toEqual([])
  })
})

describe('the auth flows store no token at runtime', () => {
  afterEach(() => {
    // The spy patches a global prototype, so leaving it in place would follow the suite into
    // every later file — the kind of leak that surfaces as an unrelated test failing only when
    // the whole suite runs.
    vi.restoreAllMocks()
  })

  /**
   * Watches both storage APIs and records everything written.
   *
   * Spying rather than reading the store afterwards, because a value written and then removed
   * would leave nothing to find — and a token that exists in storage for only a moment is still
   * a token any script on the page could have read.
   */
  function watchStorage() {
    const writes: Array<[string, string]> = []
    // Captured before the spy replaces it, so the real write still happens. A spy that swallowed
    // the call would leave the provider unable to read its own session back, and the test would
    // then be exercising a different application from the one that ships.
    const write = Storage.prototype.setItem

    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(function (
      this: Storage,
      key: string,
      value: string,
    ) {
      writes.push([key, value])
      write.call(this, key, value)
    })

    return writes
  }

  async function renderAuth() {
    const view = renderHook(() => useAuth(), {
      wrapper: ({ children }: { children: ReactNode }) => <AuthProvider>{children}</AuthProvider>,
    })
    await waitFor(() => {
      expect(view.result.current.checking).toBe(false)
    })
    return view
  }

  it('stores nothing token-shaped while signing up and signing in', async () => {
    const writes = watchStorage()
    const { result } = await renderAuth()

    await act(async () => {
      await result.current.signUp('ada@example.com', 'correct-horse', 'Ada')
    })
    await act(async () => {
      await result.current.signOut()
    })
    await act(async () => {
      await result.current.signIn('ada@example.com', 'correct-horse')
    })

    for (const [key, value] of writes) {
      expect(key, `"${key}" names a credential`).not.toMatch(TOKEN_KEY_PATTERN)
      expect(value, `a JWT was written under "${key}"`).not.toMatch(JWT_PATTERN)
      expect(value, `a JWT was embedded in the value under "${key}"`).not.toContain('eyJ')
    }
  })

  it('stores no password, even though the in-memory provider holds one in memory', async () => {
    // The provider keeps a password in a Map to compare against — acceptable for a placeholder
    // with no backend. Writing one to disk would not be, and this is what separates the two.
    const writes = watchStorage()
    const { result } = await renderAuth()

    await act(async () => {
      await result.current.signUp('ada@example.com', 'correct-horse', 'Ada')
    })

    for (const [key, value] of writes) {
      expect(value, `a password was written under "${key}"`).not.toContain('correct-horse')
    }
  })
})
