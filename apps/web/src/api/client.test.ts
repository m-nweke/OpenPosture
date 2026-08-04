import { describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import { ANALYSES_ENDPOINT, AUTH_ENDPOINT, ApiError, analysePosture, logout } from './client'
import { getAccessToken, setAccessToken } from '../auth/tokenStore'
import { server } from '../test/mswServer'
import { analysisOf, hunchbackReport } from '../test/reports'

function pngFile() {
  return new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], 'posture.png', { type: 'image/png' })
}

describe('analysePosture', () => {
  it('posts to a relative URL on the page origin', async () => {
    // Relative, so the Vite proxy handles it and there is no base URL per environment. The
    // original hardcoded `http://127.0.0.1:5000/` into a component, which works on one machine.
    let seen: string | undefined
    server.use(
      http.post(ANALYSES_ENDPOINT, ({ request }) => {
        seen = new URL(request.url).pathname
        return HttpResponse.json(analysisOf(hunchbackReport()), { status: 201 })
      }),
    )

    await analysePosture(pngFile())

    expect(seen).toBe('/api/v1/analyses')
  })

  it('sends the file as multipart under the field the API expects', async () => {
    let field: FormDataEntryValue | null = null as FormDataEntryValue | null
    server.use(
      http.post(ANALYSES_ENDPOINT, async ({ request }) => {
        field = (await request.formData()).get('image')
        return HttpResponse.json(analysisOf(hunchbackReport()), { status: 201 })
      }),
    )

    await analysePosture(pngFile())

    // Not `toBeInstanceOf(File)`: MSW parses the body in its own realm, so the constructor
    // differs even though the value is a file. The byte length is not asserted either —
    // jsdom's XHR does not round-trip a Uint8Array body at its original length. What matters
    // here is that the file arrived under the field name the API reads, carrying its type.
    expect(field).not.toBeNull()
    const sent = field as unknown as File
    expect(sent.type).toBe('image/png')
    expect(sent.size).toBeGreaterThan(0)
  })

  it('rejects with the problem type so callers can branch on it', async () => {
    // `type` is stable and machine-readable; `title` and `detail` are prose a copy edit changes.
    server.use(
      http.post(ANALYSES_ENDPOINT, () =>
        HttpResponse.json(
          {
            type: 'https://openposture.dev/problems/image-too-large',
            title: 'Payload too large',
            status: 413,
            detail: 'image is 12000000 bytes, over the 10485760 byte limit',
            request_id: 'req-1',
          },
          { status: 413 },
        ),
      ),
    )

    await expect(analysePosture(pngFile())).rejects.toMatchObject({
      status: 413,
      type: 'https://openposture.dev/problems/image-too-large',
      requestId: 'req-1',
    })
  })

  it('rejects when the signal is already aborted, without sending anything', async () => {
    let called = false
    server.use(
      http.post(ANALYSES_ENDPOINT, () => {
        called = true
        return HttpResponse.json(analysisOf(hunchbackReport()), { status: 201 })
      }),
    )
    const controller = new AbortController()
    controller.abort()

    await expect(analysePosture(pngFile(), { signal: controller.signal })).rejects.toBeInstanceOf(
      ApiError,
    )
    expect(called).toBe(false)
  })

  it('rejects when aborted mid-flight', async () => {
    // The Clear button and unmount both do this. Without it an in-flight upload resolves into a
    // component that is gone.
    let release: (() => void) | undefined
    const held = new Promise<void>((resolve) => {
      release = resolve
    })
    server.use(
      http.post(ANALYSES_ENDPOINT, async () => {
        await held
        return HttpResponse.json(analysisOf(hunchbackReport()), { status: 201 })
      }),
    )
    const controller = new AbortController()

    const pending = analysePosture(pngFile(), { signal: controller.signal })
    controller.abort()

    await expect(pending).rejects.toThrow('The upload was cancelled.')
    release?.()
  })

  it('gives up rather than hanging forever', async () => {
    // `XMLHttpRequest.timeout` defaults to 0, meaning no timeout at all. Wiring the listener
    // without setting it leaves a branch that can never run and a spinner that never stops.
    server.use(
      http.post(ANALYSES_ENDPOINT, async () => {
        await new Promise((resolve) => setTimeout(resolve, 200))
        return HttpResponse.json(analysisOf(hunchbackReport()), { status: 201 })
      }),
    )

    await expect(analysePosture(pngFile(), { timeoutMs: 10 })).rejects.toThrow(
      'The request timed out before the server answered.',
    )
  })

  it('reports progress as bytes are sent', async () => {
    server.use(
      http.post(ANALYSES_ENDPOINT, () =>
        HttpResponse.json(analysisOf(hunchbackReport()), { status: 201 }),
      ),
    )
    const seen: number[] = []

    await analysePosture(pngFile(), { onProgress: (percent) => seen.push(percent) })

    // The exact sequence depends on chunking, so this asserts the contract rather than the
    // schedule: every value is a real percentage, and the last one is completion.
    expect(seen.every((percent) => percent >= 0 && percent <= 100)).toBe(true)
    expect(seen.at(-1)).toBe(100)
  })

  describe('the access token', () => {
    it('is attached as a bearer header when one is present', async () => {
      setAccessToken('a-token')
      let seenAuth: string | null = null
      server.use(
        http.post(ANALYSES_ENDPOINT, ({ request }) => {
          seenAuth = request.headers.get('authorization')
          return HttpResponse.json(analysisOf(hunchbackReport()), { status: 201 })
        }),
      )

      await analysePosture(pngFile())

      expect(seenAuth).toBe('Bearer a-token')
    })

    it('is omitted when there is no access token', async () => {
      let seenAuth: string | null | undefined
      server.use(
        http.post(ANALYSES_ENDPOINT, ({ request }) => {
          seenAuth = request.headers.get('authorization')
          return HttpResponse.json(analysisOf(hunchbackReport()), { status: 201 })
        }),
      )

      await analysePosture(pngFile())

      expect(seenAuth).toBeNull()
    })
  })
})

describe('the single-flight refresh guard', () => {
  it('coalesces N concurrent 401s into exactly one refresh request', async () => {
    // The race this ticket exists for: three components loading at once each hold the same
    // expired token, each get a 401, and — without the guard — each would call `/refresh`
    // independently. With rotation-on-use, the second and third would then present a token the
    // first refresh already retired, and the server would read that as theft and revoke the
    // whole family. See refreshAccessToken's docstring in client.ts.
    setAccessToken('expired-token')
    let refreshCalls = 0
    let analysesCalls = 0

    server.use(
      http.post(`${AUTH_ENDPOINT}/refresh`, () => {
        refreshCalls += 1
        return HttpResponse.json({
          access_token: 'fresh-token',
          token_type: 'bearer',
          expires_in: 900,
        })
      }),
      http.post(ANALYSES_ENDPOINT, ({ request }) => {
        analysesCalls += 1
        if (request.headers.get('authorization') === 'Bearer expired-token') {
          return HttpResponse.json(
            { type: 't', title: 'Unauthorized', status: 401, detail: 'expired' },
            { status: 401 },
          )
        }
        return HttpResponse.json(analysisOf(hunchbackReport()), { status: 201 })
      }),
    )

    const results = await Promise.all([
      analysePosture(pngFile()),
      analysePosture(pngFile()),
      analysePosture(pngFile()),
    ])

    expect(refreshCalls).toBe(1)
    // Three first attempts (401) plus three retries (201) — one refresh, six analyses calls.
    expect(analysesCalls).toBe(6)
    expect(results).toHaveLength(3)
    expect(getAccessToken()).toBe('fresh-token')
  })

  it('rejects once, without retrying again, when the refresh itself fails', async () => {
    setAccessToken('expired-token')
    let refreshCalls = 0
    let analysesCalls = 0

    server.use(
      http.post(`${AUTH_ENDPOINT}/refresh`, () => {
        refreshCalls += 1
        return HttpResponse.json(
          { type: 't', title: 'Unauthorized', status: 401, detail: 'no session' },
          { status: 401 },
        )
      }),
      http.post(ANALYSES_ENDPOINT, () => {
        analysesCalls += 1
        return HttpResponse.json(
          { type: 't', title: 'Unauthorized', status: 401, detail: 'expired' },
          { status: 401 },
        )
      }),
    )

    // A failed refresh logs out cleanly rather than looping: `allowRetry` guarantees at most one
    // retry attempt, so a server that keeps answering 401 cannot recurse forever.
    await expect(analysePosture(pngFile())).rejects.toMatchObject({ status: 401 })

    expect(refreshCalls).toBe(1)
    expect(analysesCalls).toBe(1)
    // The clean-logout half of the guarantee: the token store no longer holds a token a caller
    // could keep retrying with, which is what lets ApiAuthProvider notice and sign the user out.
    expect(getAccessToken()).toBeNull()
  })
})

describe('logout', () => {
  it('never rejects, even when the network call fails — it is best-effort by contract', async () => {
    setAccessToken('some-token')
    server.use(http.post(`${AUTH_ENDPOINT}/logout`, () => HttpResponse.error()))

    // App.tsx's signOut fires this without a .catch. A rejection here — even one a `finally`
    // clears the token before — would be an unhandled promise rejection in the browser.
    await expect(logout()).resolves.toBeUndefined()
    expect(getAccessToken()).toBeNull()
  })

  it('never rejects on a non-2xx response either', async () => {
    setAccessToken('some-token')
    server.use(
      http.post(`${AUTH_ENDPOINT}/logout`, () =>
        HttpResponse.json(
          { type: 't', title: 'Internal Server Error', status: 500, detail: 'db down' },
          { status: 500 },
        ),
      ),
    )

    await expect(logout()).resolves.toBeUndefined()
    expect(getAccessToken()).toBeNull()
  })
})
