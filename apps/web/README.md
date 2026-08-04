# apps/web

The OpenPosture frontend: React 19, TypeScript, Vite.

This was `openpose-react/` at the repository root until OP-13. It is a standalone npm package —
deliberately **not** part of the `uv` workspace, which covers Python only.

## Requirements

Node 22 (the version CI pins via `NODE_VERSION` in `.github/workflows/pr.yml`).

```bash
npm ci        # exact lockfile install; use this rather than `npm install`
```

## Commands

| Command                  | What it does                                           |
| ------------------------ | ------------------------------------------------------ |
| `npm run dev`            | Dev server on <http://localhost:5173>                  |
| `npm run lint`           | oxlint                                                 |
| `npm run format:check`   | Prettier, check only (`npm run format` writes)         |
| `npm run typecheck`      | `tsc`, strict                                          |
| `npm run test`           | Vitest                                                 |
| `npm run test:coverage`  | Vitest with the 70% floor CI enforces                  |
| `npm run test:e2e`       | Playwright against a production build, no API needed   |
| `npm run test:e2e:stack` | Playwright against a running Compose stack (see below) |
| `npm run codegen`        | Regenerate the API schema and its TypeScript types     |
| `npm run build`          | Production build                                       |

Each maps to a job in `pr.yml` (`web-lint`, `web-typecheck`, `web-test`, `web-build`,
`web-e2e`, `contract`), so anything that passes here passes there. `test:e2e:stack` is the
exception — it needs a running stack, so it lives in `e2e.yml` instead.

## Talking to the API

`src/api/` is the only place that does. Three files, and the split matters:

- **`schema.d.ts` is generated** from the backend's OpenAPI document and must never be edited.
  `npm run codegen` dumps the schema straight from the FastAPI app object — no server to
  start — and runs `openapi-typescript` over it.
- **`types.ts` only aliases** into that schema. Adding a shape here by hand would recreate the
  second source of truth the generation exists to remove: rename a field in Python and `tsc`
  should fail, not a browser months later.
- **`client.ts`** issues the requests. Relative URLs only, so the Vite proxy handles routing
  and there is no per-environment base URL — the original hardcoded `http://127.0.0.1:5000/`
  into a component, which works on exactly one machine.

It uses `XMLHttpRequest` rather than `fetch`, for one reason: `fetch` reports nothing about
how much of a request body has been sent, so a progress bar built on it is an animation
pretending to be information.

**After changing anything the API returns, run `npm run codegen` and commit the result.** The
`contract` job regenerates and fails on any diff.

## Two end-to-end suites

They are separate configs, not two projects in one, because they need different things:

- **`e2e/`** runs against `vite preview` serving a production build. No API. This is the auth
  and routing journey.
- **`e2e-stack/`** runs against Compose, through the proxy, against a real API. This is the
  upload journey, and it asserts an exact measured value on screen.

`vite preview` applies `preview.proxy`, not `server.proxy`, so the production-bundle server
does not forward `/api` at all — a stack spec run there would exercise a frontend talking to
nothing. Both directories are excluded from Vitest in `vite.config.ts`.

## Authentication talks to the API

`src/auth` defines the contract the UI is written against — `AuthContextValue` in `types.ts` —
and ships `ApiAuthProvider`, which talks to the self-hosted API's register/login/refresh/logout
routes (OP-55).

OP-13 shipped a placeholder first: `InMemoryAuthProvider`, real but backend-less — accounts in a
`Map`, passwords compared in plaintext — so the app could be built and tested before the API
existed. OP-57 deleted it and added `ApiAuthProvider` in its place, changing one line of
`src/auth/index.ts` plus one of `main.tsx`. No component imports anything auth-specific, so no
component needed to change. If one had, the boundary was drawn in the wrong place.

The access token lives in memory only (`src/auth/tokenStore.ts`), never in `localStorage` or
`sessionStorage` — see `noTokenStorage.test.tsx`. The refresh token never reaches the client at
all: it is an `HttpOnly` cookie, rotated on every use, with the whole family revoked if an
already-rotated token is presented again. That rotation is why `api/client.ts`'s 401 handling
coalesces concurrent refreshes into one request rather than firing one per failed request —
several components hitting 401 at once would otherwise race each other into a stolen-token replay
and get the user logged out for loading a page.

Before OP-13 there was no boundary at all: `firebase/auth` was imported directly by five
components, the web config for a live Firebase project was committed in `src/firebase.ts`, and
`Login.tsx` branched on Firebase's own error codes.

## Testing

- **Vitest + Testing Library** for components, in jsdom. Queries go through accessible roles and
  labels, so the tests break when the UI becomes unusable rather than when markup is refactored.
- **MSW** intercepts at the network layer, so components run their real HTTP calls and only the
  response is faked. Handlers live in `src/test/handlers.ts`; unhandled requests are an error,
  not a warning.
- **Playwright** for one end-to-end journey, run against `vite preview` serving a production
  build — a suite that only ever sees the dev server will not notice a broken build.

The 70% coverage floor is configured in `vite.config.ts`, not in CI, so the local command and the
CI job enforce identical numbers. It applies to branches and functions as well as lines.
