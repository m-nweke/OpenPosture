# ADR-0001 — FastAPI, not a modernized Flask

**Status:** Accepted
**Date:** 2026-07-26
**Ticket:** OP-15 (decision made in `docs/V2-PLAN.md`, implemented from OP-50)

## Context

The inherited backend is `API/app.py`: 56 lines of Flask exposing one route, `POST /upload`, which
writes an uploaded file to disk and returns a filename. It never imports the model. There is no
inference endpoint — that missing wire is the entire gap between the original demo and a working
product (FINDINGS §8).

"Modernize the existing Flask app" sounds conservative, and would be, if there were anything to
conserve. There is not: the route is unauthenticated, unvalidated as to type or size, writes to a
cwd-relative path, and runs with `debug=True` (FINDINGS §5.1, §5.5). Nothing in those 56 lines
survives contact with the v2 requirements. So this is a rewrite either way, and the only real
question is which framework to rewrite into.

The requirements that actually discriminate between frameworks:

1. Accept a photo up to ~15 MB without holding it in memory.
2. Run blocking, CPU-bound inference without stalling every other request.
3. Stream LLM coaching tokens to the browser while a database session is open (Epic F).
4. Test an endpoint whose real behaviour is "run a 9 MB model" without running the model.

## Decision

**FastAPI**, with Pydantic v2 and SQLAlchemy 2.0.

Each requirement above maps to something FastAPI provides directly:

| Need | FastAPI | Flask |
| --- | --- | --- |
| Large upload | `UploadFile` spools to disk past a threshold | `request.files` buffers in memory |
| Blocking inference | `run_in_threadpool` keeps it off the event loop | WSGI worker blocks; needs Celery or gunicorn tuning |
| Token streaming | `StreamingResponse` over an async iterator, ~10 lines | Generator streaming while holding a DB session is genuinely awkward |
| Testing without the model | `app.dependency_overrides` | Monkeypatching, or a seam built by hand |

`app.dependency_overrides` is the decisive one. The pose backend is injected as a dependency, so a
test replaces it with `FakePoseBackend` (OP-19) and the endpoint runs its real code path against
canned landmarks. That is what makes the API testable in CI with no model weights, no download and
no GPU — the same property `posture-core`'s purity buys at the layer below.

Bonus that improves the frontend rather than the backend: FastAPI emits OpenAPI 3.1, so
`openapi-typescript` generates `apps/web/src/api/schema.d.ts` from `/openapi.json`. A breaking
backend change then fails `tsc` in CI instead of failing in a browser.

## Alternatives considered

**Modernized Flask (2.3+ with blueprints and type hints).** Rejected. It would preserve the
*shape* of a file whose every line is being replaced, while requiring the four capabilities above
to be built by hand. The one honest argument for it — "the team already knows Flask" — does not
apply to a rewrite whose purpose is partly to demonstrate current practice.

**Django REST Framework.** Rejected as too much framework. This service has two or three
resources; Django's admin, ORM and migrations would be carried for nothing, and its async support
is still the awkward path rather than the default.

**Litestar.** Genuinely close on the technical merits, and arguably a cleaner dependency-injection
design. Rejected on ecosystem: FastAPI's documentation, StackOverflow surface and third-party
integrations are much larger, which matters more for a project meant to be read by others than a
marginally nicer API does.

## Consequences

- Async is now a correctness concern. A blocking call in a coroutine stalls the whole event loop,
  and the failure mode is latency under concurrency rather than an exception — invisible in
  single-request testing. Inference must go through `run_in_threadpool`.
- Pydantic v2 is in the dependency graph, so `apps/api` is decisively "heavy". This is fine and
  intended: it is exactly what `posture-core` is kept clear of.
- The generated-types pipeline adds a CI step and a real dependency of the frontend on the backend
  schema. That coupling is deliberate — it is what makes a breaking change loud.
- Nothing here constrains the rules engine. `posture-core` does not know FastAPI exists, so this
  decision could be reversed without touching a single rule.
