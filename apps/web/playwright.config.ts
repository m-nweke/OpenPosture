import { defineConfig, devices } from '@playwright/test'

/**
 * End-to-end config.
 *
 * `webServer` builds and serves the production bundle rather than pointing at `vite dev`, so
 * these specs exercise what actually ships — minified, with the real asset pipeline. A dev-server
 * e2e suite passes happily while the production build is broken.
 */
export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  // A test that only passes when retried is a flaky test, and CI should say so rather than
  // paper over it. Retries stay at zero until there is a reason to change that.
  retries: 0,
  forbidOnly: Boolean(process.env.CI),
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL: 'http://127.0.0.1:4173',
    trace: 'on-first-retry',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    // `--host 127.0.0.1` is load-bearing. Left to itself `vite preview` binds ::1 only, so
    // every request to 127.0.0.1 is refused and Playwright waits out its full start timeout
    // with no useful error.
    command: 'npm run build && npm run preview -- --port 4173 --strictPort --host 127.0.0.1',
    url: 'http://127.0.0.1:4173',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
})
