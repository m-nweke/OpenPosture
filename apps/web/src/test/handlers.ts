import type { RequestHandler } from 'msw'

/**
 * Default request stubs, applied to every test by `setupTests.ts`.
 *
 * Empty: the app makes exactly one kind of request now — `POST /api/v1/analyses` — and every test
 * that exercises it declares its own response with `server.use(...)`, because the interesting
 * part is always *which* response. A default here would be a fifth answer nobody asked for.
 *
 * Unhandled requests are an error rather than a warning (see `setupTests.ts`), so a component
 * that starts calling something new fails a test instead of passing quietly.
 */
export const handlers: RequestHandler[] = []
