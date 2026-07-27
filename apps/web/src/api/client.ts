/**
 * The one place the frontend talks to the API.
 *
 * **Relative URLs only.** Every request goes to `/api/...` on the page's own origin, which the
 * Vite proxy forwards to the API service. There is no base URL to configure per environment and
 * no CORS to get wrong. `HelloWorld.tsx` did the opposite — `axios.get('http://127.0.0.1:5000/')`
 * hardcoded into a component — which works on exactly one machine.
 *
 * `XMLHttpRequest` rather than `fetch`, for one reason: **upload progress**. `fetch` reports
 * nothing about how much of a request body has been sent, so a progress bar built on it is an
 * animation pretending to be information. That is the same lie as the five-second `setTimeout`
 * this ticket deletes, just better dressed. XHR's `upload.onprogress` reports real bytes.
 */

import type { AnalysisResponse, Problem } from './types'

export const ANALYSES_ENDPOINT = '/api/v1/analyses'

/** A failure the UI can render, whatever its origin. */
export class ApiError extends Error {
  readonly status: number
  /** The RFC 9457 `type` URI when the server sent a problem document. */
  readonly type: string | undefined
  readonly requestId: string | undefined

  // `type?: string | undefined` rather than `type?: string`: this project runs
  // `exactOptionalPropertyTypes`, under which an optional property may be *absent* but not
  // explicitly `undefined`. Passing through a value that might be undefined needs the union.
  constructor(
    message: string,
    options: { status: number; type?: string | undefined; requestId?: string | undefined },
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = options.status
    this.type = options.type
    this.requestId = options.requestId
  }
}

/**
 * How long to wait before giving up on one analysis.
 *
 * Generous, because the work is genuinely slow: a 10 MB upload over a poor connection plus model
 * inference on the server. Short enough that a request lost to a dropped connection surfaces as a
 * message rather than a spinner that never stops — the browser's own default is *no* timeout, so
 * without this the failure mode is waiting forever.
 */
export const DEFAULT_TIMEOUT_MS = 60_000

export interface AnalyseOptions {
  /** Called with 0–100 as the request body is sent. Real bytes, not a timer. */
  onProgress?: (percent: number) => void
  signal?: AbortSignal
  /** Milliseconds before the request is abandoned. `0` disables the timeout entirely. */
  timeoutMs?: number
}

/**
 * Upload one image and return its analysis.
 *
 * Rejects with {@link ApiError} for anything that is not a 2xx. A report full of gaps is *not* a
 * failure — it arrives as a 201 with `quality.gaps` populated, and the UI is expected to render
 * that as an answer rather than an error.
 */
export function analysePosture(
  file: File,
  options: AnalyseOptions = {},
): Promise<AnalysisResponse> {
  const { onProgress, signal, timeoutMs = DEFAULT_TIMEOUT_MS } = options

  return new Promise<AnalysisResponse>((resolve, reject) => {
    const request = new XMLHttpRequest()
    const body = new FormData()
    body.append('image', file)

    request.open('POST', ANALYSES_ENDPOINT)
    request.responseType = 'text'

    // The timeout is our own timer rather than `XMLHttpRequest.timeout`.
    //
    // The native attribute is the idiomatic mechanism and browsers enforce it — but it defaults
    // to 0, meaning *no* timeout, and the earlier version of this file wired a `timeout` listener
    // without ever setting it. That is a branch which can never run: the request hangs and the
    // spinner never stops.
    //
    // Setting the attribute would fix production and stay unverifiable here, because the XHR
    // implementation the tests run against does not enforce it. A guard nobody can test is the
    // same shape of problem as the one being fixed, so this uses a timer that behaves identically
    // in both places.
    const timer =
      timeoutMs > 0
        ? setTimeout(() => {
            request.abort()
            reject(new ApiError('The request timed out before the server answered.', { status: 0 }))
          }, timeoutMs)
        : undefined

    const clearTimer = () => {
      if (timer !== undefined) clearTimeout(timer)
    }

    request.upload.addEventListener('progress', (event) => {
      // `lengthComputable` is false when the browser cannot know the total — rare for a file
      // upload, but reporting a fabricated percentage in that case is the failure mode this
      // whole approach exists to avoid, so nothing is reported instead.
      if (event.lengthComputable && onProgress) {
        onProgress(Math.round((event.loaded / event.total) * 100))
      }
    })

    request.addEventListener('load', () => {
      clearTimer()
      const parsed = parseBody(request.responseText)

      if (request.status >= 200 && request.status < 300) {
        resolve(parsed as AnalysisResponse)
        return
      }

      const problem = parsed as Problem | null
      reject(
        new ApiError(problem?.detail ?? `The server responded with ${request.status}.`, {
          status: request.status,
          type: problem?.type,
          requestId: problem?.request_id,
        }),
      )
    })

    // Status 0 covers both a dropped connection and a blocked request. Neither has a body, so
    // there is nothing to parse and nothing to quote back to the user beyond "it did not arrive".
    request.addEventListener('error', () => {
      clearTimer()
      reject(
        new ApiError('Could not reach the server. Check your connection and try again.', {
          status: 0,
        }),
      )
    })

    // Rejection is driven from here rather than from the XHR `abort` event, because that event
    // is not guaranteed to fire. `abort()` on a request that has been opened but not sent moves
    // it straight to UNSENT without dispatching anything — so a caller passing an
    // already-aborted signal would wait on a promise that never settles. Found by a test.
    const cancel = () => {
      clearTimer()
      request.abort()
      reject(new ApiError('The upload was cancelled.', { status: 0 }))
    }

    if (signal) {
      if (signal.aborted) {
        cancel()
        return
      }
      signal.addEventListener('abort', cancel, { once: true })
    }

    request.send(body)
  })
}

/** Parse a response body, tolerating one that is not JSON at all. */
function parseBody(text: string): unknown {
  if (!text) return null
  try {
    return JSON.parse(text)
  } catch {
    // A proxy error page or an HTML 502 from something in front of the API. Swallowing the parse
    // failure is right here: the caller wants a usable message, and "Unexpected token < in JSON"
    // is not one.
    return null
  }
}
