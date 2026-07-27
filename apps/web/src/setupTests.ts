import '@testing-library/jest-dom/vitest'
import { afterAll, afterEach, beforeAll } from 'vitest'
import { cleanup } from '@testing-library/react'
import { server } from './test/mswServer'

// MSW intercepts at the network layer, so components run their real axios/fetch calls and only
// the response is faked. That is the point: mocking the HTTP client instead would leave the
// call sites untested and would not notice if a URL or method were wrong.
//
// `error` rather than `warn` for unhandled requests: a component that starts calling an
// endpoint nobody stubbed should fail loudly here, not silently receive undefined and pass.
beforeAll(() => {
  server.listen({ onUnhandledRequest: 'error' })
})

afterEach(() => {
  cleanup()
  // Drops per-test overrides so a handler added by one test cannot leak into the next.
  server.resetHandlers()
  window.sessionStorage.clear()
})

afterAll(() => {
  server.close()
})

/**
 * jsdom implements neither `ResizeObserver` nor a canvas 2D context.
 *
 * Both are stubbed rather than pulled in as dependencies, because what the tests need is the
 * *ability to observe calls*, not a real rendering engine. A component that measures its own
 * layout has nothing to measure in jsdom anyway — the overlay's own tests drive the box size
 * explicitly, which is more deterministic than hoping jsdom reports one.
 */
class ResizeObserverStub implements ResizeObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

if (!('ResizeObserver' in globalThis)) {
  globalThis.ResizeObserver = ResizeObserverStub
}
