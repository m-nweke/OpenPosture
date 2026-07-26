# ADR-0003 — Self-hosted JWT, not Firebase Auth

**Status:** Accepted
**Date:** 2026-07-26
**Ticket:** OP-15 (implemented in Epic E, from OP-30)

## Context

Both original frontends authenticated directly against Firebase Auth. The Flask backend had no
authentication at all: `POST /upload` accepted anything from anyone (FINDINGS §5.1, §5.2). So the
system had a login screen and no access control — the frontend knew who you were and the only
component holding data did not.

That is the actual defect. It is worth being precise about what was *not* a defect: the committed
Firebase web `apiKey` (FINDINGS §5.3) is public by design and is not a leaked secret. The problem is
that with no backend verification, Firebase Security Rules were the only access control in the
system, and the unauthenticated upload route bypassed them entirely.

v2 needs authentication the API itself can enforce, because Epic E introduces per-user assessment
history in Postgres and every read must be scoped to its owner.

## Decision

**Self-hosted authentication**: email and password with `argon2` hashing, short-lived access JWTs,
rotating refresh tokens, verified in FastAPI dependencies.

The deciding reason is that **the trust boundary belongs where the data is.** With self-hosted JWTs
the API verifies every request itself, so authorization is a property of the endpoint rather than of
whichever client happens to be calling. There is no configuration in which a request reaches a
database row without having passed the check.

Secondary, and specific to this project: the frontend already had to be decoupled from Firebase
regardless. OP-13 removed `firebase/auth` from five components and replaced it with an
`AuthContextValue` interface plus an in-memory implementation. Epic E swaps that implementation for
an API-backed one; no component changes. Having built that boundary, adopting a different hosted
provider would mean reintroducing a vendor behind it for no gain.

It also happens to be the more demonstrable choice — password hashing, token rotation and
dependency-injected auth are things worth being able to show and reason about, rather than
delegating and hoping.

## Alternatives considered

**Keep Firebase Auth, verify its ID tokens in FastAPI.** The strongest alternative, and genuinely
reasonable: it fixes the actual defect (the API would verify tokens) while keeping password storage
and reset flows out of scope. Rejected for three reasons — it keeps a Google dependency in a project
whose stated aim is self-hostable via Docker Compose; it needs network access to Google's JWKS
endpoint in tests and in CI, which the "no network service, no secret" rule for `pr.yml` forbids;
and it splits the user record across Firebase and Postgres, so every foreign key points at an
identifier owned by someone else.

**OAuth-only via an identity provider (Auth0, Clerk, Keycloak).** Rejected as disproportionate.
Auth0 and Clerk reintroduce the hosted dependency this decision exists to remove. Keycloak is
self-hostable but is a substantial Java service to run beside a small Python API — more operational
weight than the whole rest of the stack.

**Session cookies instead of JWTs.** A defensible choice, and simpler in one real respect: server-side
sessions can be revoked immediately, whereas a JWT is valid until it expires. Rejected because Epic G
(browser live mode) and a possible future native client both want a bearer token rather than a cookie
tied to one origin. The revocation gap is mitigated by short access-token lifetimes plus refresh-token
rotation, not ignored.

## Consequences

- **Password storage is now this project's responsibility.** Argon2id with sensible parameters, no
  password in a log or an error message, and constant-time comparison. This is the cost of the
  decision and it should not be understated — it is the part a hosted provider genuinely does well.
- Sign-in must not reveal whether an email is registered. The in-memory provider already returns one
  `invalid-credentials` error for both "no such account" and "wrong password", with a test pinning
  that behaviour, so the real implementation inherits the requirement rather than discovering it.
- No third-party sign-in at launch. OP-13 deleted the "Sign in with Google" buttons rather than
  disabling them, because a control that looks live and does nothing is worse than its absence.
  Adding OAuth later is additive.
- Access tokens are unrevokable until expiry. Keep them short (minutes), rotate refresh tokens, and
  store refresh tokens hashed so a database leak does not hand over live sessions.
- Auth is testable without a network. Tokens are signed with a test key and verified in-process, so
  `pr.yml` keeps its no-secret, no-service guarantee.
- Email verification and password reset need an outbound mail path, which the project does not have.
  Both are deliberately out of scope for Epic E and should be recorded as gaps rather than quietly
  omitted.
