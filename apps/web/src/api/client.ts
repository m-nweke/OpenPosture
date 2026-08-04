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

import type { AnalysisResponse, CredentialsRequest, Problem, TokenResponse } from './types'
import { getAccessToken, setAccessToken } from '../auth/tokenStore'

export const ANALYSES_ENDPOINT = '/api/v1/analyses'
export const AUTH_ENDPOINT = '/api/v1/auth'

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
  return attemptAnalysis(file, options, /* allowRetry */ true)
}

/**
 * One attempt at the request above, with the 401-refresh-retry split out so it can call itself
 * exactly once more and never again.
 *
 * `allowRetry` is what makes "a failed refresh logs out cleanly rather than looping" true by
 * construction: the retried attempt is always called with `false`, so a second 401 — refreshed
 * token and all — falls straight through to the ordinary error branch instead of refreshing
 * again. Without that flag a server that kept answering 401 would recurse forever.
 */
function attemptAnalysis(
  file: File,
  options: AnalyseOptions,
  allowRetry: boolean,
): Promise<AnalysisResponse> {
  const { onProgress, signal, timeoutMs = DEFAULT_TIMEOUT_MS } = options

  return new Promise<AnalysisResponse>((resolve, reject) => {
    const request = new XMLHttpRequest()
    const body = new FormData()
    body.append('image', file)

    request.open('POST', ANALYSES_ENDPOINT)
    request.responseType = 'text'

    const token = getAccessToken()
    if (token !== null) {
      request.setRequestHeader('Authorization', `Bearer ${token}`)
    }

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

      // A single-flight guard, not one guard per request: concurrent 401s from three components
      // loading at once all call `refreshAccessToken`, but only the first actually reaches the
      // network — see the docstring on that function for why a second refresh mid-rotation would
      // get the user logged out rather than merely wasting a request.
      if (request.status === 401 && allowRetry) {
        void refreshAccessToken().then((refreshed) => {
          if (refreshed === null) {
            reject(new ApiError('Your session has expired. Please sign in again.', { status: 401 }))
            return
          }
          attemptAnalysis(file, options, /* allowRetry */ false).then(resolve, reject)
        })
        return
      }

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

/**
 * `fetch`, not `XMLHttpRequest`, for everything below.
 *
 * The XHR machinery above earns its keep on one thing only — upload progress — and none of these
 * calls upload anything. `fetch` is the plainer tool for a JSON request/response.
 */
async function postJson<T>(path: string, body?: unknown): Promise<T> {
  const init: RequestInit = {
    method: 'POST',
    // The refresh cookie is `HttpOnly` and scoped to `AUTH_PREFIX` (see the API's auth.py) —
    // `credentials: 'include'` is what makes the browser attach it on the way out and store the
    // one register, login and refresh send back. Requests are same-origin (the Vite proxy sees
    // to that), so this never turns into a cross-site credentialed request.
    credentials: 'include',
  }
  // `exactOptionalPropertyTypes` forbids assigning `undefined` to an optional property, so a
  // bodyless call (logout, refresh) omits `headers`/`body` entirely rather than setting them to
  // undefined.
  if (body !== undefined) {
    init.headers = { 'Content-Type': 'application/json' }
    init.body = JSON.stringify(body)
  }

  const response = await fetch(`${AUTH_ENDPOINT}${path}`, init)

  if (!response.ok) {
    const problem = (await response.json().catch(() => null)) as Problem | null
    throw new ApiError(problem?.detail ?? `The server responded with ${response.status}.`, {
      status: response.status,
      type: problem?.type,
      requestId: problem?.request_id,
    })
  }

  // `logout` answers 204 with no body; parsing that as JSON would throw.
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T)
}

/** Register an account and open a session for it in the same call — see the route's own docs. */
export async function register(email: string, password: string): Promise<TokenResponse> {
  const credentials: CredentialsRequest = { email, password }
  const tokens = await postJson<TokenResponse>('/register', credentials)
  setAccessToken(tokens.access_token)
  return tokens
}

export async function login(email: string, password: string): Promise<TokenResponse> {
  const credentials: CredentialsRequest = { email, password }
  const tokens = await postJson<TokenResponse>('/login', credentials)
  setAccessToken(tokens.access_token)
  return tokens
}

/**
 * Best-effort: the client forgets the session whether or not the network call lands, and this
 * function itself never rejects — a `finally` alone still lets the original error propagate past
 * it. Callers fire this without a `.catch` (`App.tsx`'s `signOut`), so a rejection here would be
 * an unhandled promise rejection despite the token already being cleared.
 */
export async function logout(): Promise<void> {
  try {
    await postJson<void>('/logout')
  } catch {
    // Nothing to do: the caller only cares that the session ends locally, which `finally` below
    // guarantees regardless of how this request went.
  } finally {
    setAccessToken(null)
  }
}

let refreshInFlight: Promise<string | null> | null = null

/**
 * Exchange the refresh cookie for a new access token, coalescing concurrent callers onto one
 * request.
 *
 * **Why single-flight, specifically:** the refresh token rotates on every use, and reusing an
 * already-rotated token is treated as theft — the API revokes the *entire* token family
 * (auth.py's `refresh` route). A page that renders three components which each hold an expired
 * access token fires three 401s at once. Without this guard, each would call `refresh`
 * independently: the first rotation succeeds, and the second and third then present the token the
 * first just retired. The server cannot tell that apart from an attacker replaying a stolen
 * token, so it revokes the whole family — and the user is signed out for the crime of loading a
 * page. Coalescing every concurrent caller onto the *same* promise means exactly one refresh
 * request reaches the server no matter how many callers ask at once; each caller still gets the
 * resulting token (or `null`) when it resolves.
 *
 * Resolves to `null` — never rejects — on any failure, whether that is a 401 (no valid cookie) or
 * a network error. Callers branch on `null` to end the session; a promise that could also reject
 * would need two failure paths for what is, from here, one outcome.
 */
export function refreshAccessToken(): Promise<string | null> {
  if (refreshInFlight === null) {
    refreshInFlight = performRefresh().finally(() => {
      refreshInFlight = null
    })
  }
  return refreshInFlight
}

async function performRefresh(): Promise<string | null> {
  try {
    const tokens = await postJson<TokenResponse>('/refresh')
    setAccessToken(tokens.access_token)
    return tokens.access_token
  } catch {
    setAccessToken(null)
    return null
  }
}
