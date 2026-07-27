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

| Command                 | What it does                                   |
| ----------------------- | ---------------------------------------------- |
| `npm run dev`           | Dev server on <http://localhost:5173>          |
| `npm run lint`          | oxlint                                         |
| `npm run format:check`  | Prettier, check only (`npm run format` writes) |
| `npm run typecheck`     | `tsc`, strict                                  |
| `npm run test`          | Vitest                                         |
| `npm run test:coverage` | Vitest with the 70% floor CI enforces          |
| `npm run test:e2e`      | Playwright, against a real production build    |
| `npm run build`         | Production build                               |

Each maps to a job in `pr.yml` (`web-lint`, `web-typecheck`, `web-test`, `web-build`,
`web-e2e`), so anything that passes here passes there.

## Authentication is a placeholder

`src/auth` defines the contract the UI is written against — `AuthContextValue` in `types.ts` —
and ships one implementation of it, `InMemoryAuthProvider`.

That implementation is real but has no backend: it genuinely creates accounts, genuinely rejects
a wrong password, and genuinely restores your session across a reload, all inside the tab.
Accounts live in a `Map` and passwords are compared in plaintext, so **none of it is secure and
none of it should survive Epic E**. What survives is the interface.

Epic E (OP-57) deletes `InMemoryAuthProvider.tsx`, adds an API-backed provider satisfying the
same interface, and changes one line of `src/auth/index.ts` plus one of `main.tsx`. No component
imports anything auth-specific, so no component should need to change. If one does, the boundary
was drawn in the wrong place.

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
