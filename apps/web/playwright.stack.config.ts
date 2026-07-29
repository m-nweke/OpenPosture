import { defineConfig, devices } from '@playwright/test'

/**
 * End-to-end config for the journey that needs a real API behind it.
 *
 * Separate from `playwright.config.ts`, which serves a static production bundle at :4173 and can
 * therefore test everything except the one thing this suite is for. The difference is not a
 * preference:
 *
 * - `vite preview` applies `preview.proxy`, not `server.proxy`, so the production-bundle server
 *   does not forward `/api` at all. A spec run there would exercise a frontend talking to
 *   nothing.
 * - This suite is meaningless without the API, so it must not run in the `web-e2e` job that has
 *   no stack. Two configs keep "can this run here" a property of the config rather than of a
 *   skip inside a spec.
 *
 * No `webServer`. The stack is started by `docker compose` — by `e2e.yml` in CI, or by hand
 * locally — because starting it from Playwright would mean Playwright owning image builds and
 * healthchecks, which Compose already does properly.
 */
export default defineConfig({
  testDir: './e2e-stack',
  // The screenshot spec is a tool for producing the README images, not a check. Excluded here
  // so `npm run test:e2e:stack` and the e2e workflow never depend on it — and never rewrite a
  // committed image as a side effect of running the suite. `npm run screenshot` sets
  // `E2E_INCLUDE_SCREENSHOT` to opt back in; naming the file on the command line does not
  // override `testIgnore`, so without the variable that script matched nothing and exited on
  // "No tests found".
  testIgnore: process.env.E2E_INCLUDE_SCREENSHOT ? [] : ['**/screenshot.spec.ts'],
  fullyParallel: true,
  // A test that only passes on retry is flaky, and the ticket asks for ten consecutive green
  // runs before merge. Retries would hide exactly what that check is looking for.
  retries: 0,
  forbidOnly: Boolean(process.env.CI),
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    // The web service, which proxies /api to the api service. Deliberately *not* :8000 — going
    // straight to the API would skip the proxy, and the proxy is half of what this proves.
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:5173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})
