"""slowapi-backed rate limiting (OP-59): five per minute on login, a tunable cap on analyses.

Two limits, for two different reasons, and the docstrings on `Settings.login_rate_limit` and
`Settings.analyses_rate_limit` say which is which rather than repeating it here.

**The client-IP question is the load-bearing part.** `slowapi`'s default key function trusts
`request.client.host` — the direct TCP peer — which is exactly right when nothing sits in front
of this service, and exactly wrong once something does: every request would then key off the
proxy's own address, and the limit would apply to the whole userbase as one client. The opposite
mistake is trusting `X-Forwarded-For` unconditionally, which lets any caller pick its own bucket
by sending the header itself. :func:`resolve_client_ip` is the seam that makes the trusted case
configurable (`Settings.trusted_proxy_hops`) without ever taking a client's word for its own
address.

Both limited routes fail the same way: 429, `Retry-After`, and the same `application/problem+json`
envelope as every other error (OP-50) — never `slowapi`'s own default JSON shape.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from starlette import status

from openposture_api.errors import problem_response

if TYPE_CHECKING:
    from fastapi import FastAPI
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    from openposture_api.config import Settings

__all__ = ["build_limiter", "register_rate_limit_handlers", "resolve_client_ip"]


def resolve_client_ip(request: Request) -> str:
    """The address a rate limit should key on, honouring only *configured* proxy hops.

    `Settings.trusted_proxy_hops` (default `0`) says how many proxies between the internet and
    this process are trusted to append correctly to `X-Forwarded-For`. With the default, the
    header is not read at all — the direct TCP peer is used, which an HTTP client cannot forge.

    With `hops = N > 0`, each trusted proxy is assumed to append the address it received the
    request from, so the entry contributed by the *outermost* trusted proxy — the one closest to
    the real client — sits `N` places from the right. Anything to its left, including a client's
    own claimed IP in position zero, is attacker-controlled and never consulted. A header with
    fewer entries than `N` (a misconfigured or lied-to proxy) falls back to the leftmost entry
    rather than raising, since a rate-limit key is not worth failing the request over.
    """
    settings: Settings = request.app.state.settings
    hops = settings.trusted_proxy_hops
    if hops > 0:
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            parts = [part.strip() for part in forwarded_for.split(",") if part.strip()]
            if parts:
                return parts[max(len(parts) - hops, 0)]
    client = request.client
    return client.host if client is not None else "unknown"


def build_limiter() -> Limiter:
    """One `Limiter` per app, for the same reason routers are built per app rather than shared.

    `slowapi`'s default in-memory storage is process-local, which is what every other piece of
    state in this codebase already is (no shared cache exists yet) — fine for a single API
    process, and the first thing to revisit if this service is ever run with more than one.
    """
    return Limiter(key_func=resolve_client_ip)


def _retry_after_seconds(request: Request) -> int:
    """Seconds until the limit that was just hit resets, read from the same state `slowapi`
    itself would use to build the `Retry-After` header — computed directly rather than through
    `Limiter._inject_headers`, which no-ops unless `headers_enabled` is set and would otherwise
    require every limited route to accept a `response` parameter it has no other use for.
    """
    limiter: Limiter = request.app.state.limiter
    current = getattr(request.state, "view_rate_limit", None)
    if current is None:  # pragma: no cover - defensive; slowapi always sets this before raising
        return 1
    item, identifiers = current
    stats = limiter.limiter.get_window_stats(item, *identifiers)
    return max(1, int(stats.reset_time - time.time()))


async def _handle_rate_limit_exceeded(request: Request, exc: Exception) -> JSONResponse:
    """The one place a 429 is built, so it looks like every other error this API returns."""
    assert isinstance(exc, RateLimitExceeded)
    retry_after = _retry_after_seconds(request)
    return problem_response(
        request,
        status.HTTP_429_TOO_MANY_REQUESTS,
        f"Rate limit exceeded: {exc.detail}. Retry after {retry_after} seconds.",
        headers={"Retry-After": str(retry_after)},
    )


def register_rate_limit_handlers(app: FastAPI) -> None:
    """Attach the 429 handler to one app instance — see `errors.register_error_handlers`."""
    app.add_exception_handler(RateLimitExceeded, _handle_rate_limit_exceeded)
