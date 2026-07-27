import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// One config for build and test rather than a separate vitest.config.ts, so the alias, the
// plugin list and anything added later cannot drift between what ships and what is tested.
// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Was pinned to 5174 so this app could run beside the Vue original for side-by-side diffing.
    // The Vue app is archived (OP-10), so this takes the default port back.
    port: 5173,
    strictPort: true,
    proxy: {
      // Everything under /api goes to the API service, so the browser only ever talks to one
      // origin. That is what removes CORS from this project entirely: no preflight, no CORS
      // middleware to misconfigure, no credentialed-request cookie subtleties, and — the part
      // that bit the original — no per-environment API base URL. `HelloWorld.tsx` hardcoded
      // `http://127.0.0.1:5000/`, which works on exactly one machine.
      //
      // The target is read from the environment so the same config serves both cases: `api:8000`
      // over the compose network, and `localhost:8000` when the API is run directly on the host.
      '/api': {
        target: process.env.VITE_API_PROXY_TARGET ?? 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    // describe/it/expect without an import in every file. Declared to TypeScript via the
    // "vitest/globals" entry in tsconfig.app.json's `types`.
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/setupTests.ts'],
    css: true,
    // Playwright owns both e2e directories. Without this, `vitest run` collects those specs,
    // fails to resolve @playwright/test's runner and reports errors that look like broken
    // tests — which is exactly what happened when `e2e-stack/` was added and this list was
    // not: the spec parsed under Vitest, called `fileURLToPath` on a non-file URL and failed
    // a job that had nothing to do with it.
    exclude: ['node_modules/**', 'dist/**', 'e2e/**', 'e2e-stack/**'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'lcov'],
      include: ['src/**/*.{ts,tsx}'],
      exclude: [
        // The composition root: it mounts React to a real DOM node and renders nothing of its
        // own. Exercising it in jsdom would test ReactDOM, not us.
        'src/main.tsx',
        // Type-only modules erase to nothing at runtime, so they can never be "covered" and
        // would drag the ratio down for no signal.
        'src/auth/types.ts',
        'src/vite-env.d.ts',
        // The test harness itself. Counting it inflates the ratio with code that is 100%
        // covered by definition — it only runs when tests run — and hides how much of the
        // *application* is actually exercised.
        'src/setupTests.ts',
        'src/test/**',
        '**/*.test.{ts,tsx}',
        // Presentational leftovers from the Vite starter template: static SVG paths and the
        // welcome blurbs. Slated for deletion when the real UI lands in Epic D, and writing
        // snapshot tests for markup that is about to be removed is waste.
        'src/components/icons/**',
        'src/components/TheWelcome.tsx',
        'src/components/WelcomeItem.tsx',
        'src/views/AboutView.tsx',
      ],
      // The floor from OP-13. Applies to every metric, not just lines — a lines-only gate is
      // easy to clear while leaving whole branches unexercised.
      thresholds: {
        statements: 70,
        branches: 70,
        functions: 70,
        lines: 70,
      },
    },
  },
})
