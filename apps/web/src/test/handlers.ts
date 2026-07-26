import { http, HttpResponse } from 'msw'

/**
 * Default request stubs, applied to every test by `setupTests.ts`.
 *
 * A test that needs different behaviour for one endpoint calls `server.use(...)` with an
 * override rather than editing this list — `resetHandlers()` in the afterEach undoes it.
 */
export const handlers = [
  // The legacy Flask root that HelloWorld greets on mount. It is the only outbound call the app
  // currently makes; the real API arrives with the walking skeleton in Epic D (OP-50).
  http.get('http://127.0.0.1:5000/', () => HttpResponse.text('Hello from the OpenPosture API')),
]
