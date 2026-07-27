import { describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import { ANALYSES_ENDPOINT, ApiError, analysePosture } from './client'
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
})
