"""One error shape for the whole API: RFC 9457 `application/problem+json`.

The alternative is what the legacy service did — each route inventing its own failure payload, or
returning a 200 with an error string in it — which forces every client to special-case every
endpoint. RFC 9457 fixes the envelope: ``type``, ``title``, ``status``, ``detail``, ``instance``,
plus whatever extension members a specific problem needs.

``type`` is the field that earns the standard its keep. It is a stable, machine-readable
identifier, so the frontend branches on ``problem.type == ".../validation-error"`` rather than
pattern-matching a human sentence that a copy edit will silently break.

Three handlers, covering the three ways a request fails:

* :class:`~fastapi.HTTPException` — a route said no, deliberately.
* :class:`~fastapi.exceptions.RequestValidationError` — the request never reached the route.
* Anything else — a bug. The client is told nothing about it; the logs are told everything.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

import structlog
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette import status
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

if TYPE_CHECKING:
    from starlette.requests import Request

__all__ = [
    "PROBLEM_CONTENT_TYPE",
    "PROBLEM_TYPE_BASE",
    "handle_unexpected_error",
    "problem_response",
    "register_error_handlers",
]

PROBLEM_CONTENT_TYPE: Final = "application/problem+json"

PROBLEM_TYPE_BASE: Final = "https://openposture.dev/problems"
"""Namespace for the ``type`` URIs.

RFC 9457 asks for a URI, and says it need not resolve. This one does not: the repository is not
publicly hosted (V2-PLAN, "CI and GitHub Actions"), so a URI that dereferenced today would 404
tomorrow. What matters is that it is stable and unambiguous, which a namespaced relative path
under one owned domain is.
"""

_TITLES: Final[dict[int, str]] = {
    status.HTTP_400_BAD_REQUEST: "Bad request",
    status.HTTP_401_UNAUTHORIZED: "Unauthorized",
    status.HTTP_403_FORBIDDEN: "Forbidden",
    status.HTTP_404_NOT_FOUND: "Not found",
    status.HTTP_405_METHOD_NOT_ALLOWED: "Method not allowed",
    status.HTTP_409_CONFLICT: "Conflict",
    status.HTTP_413_CONTENT_TOO_LARGE: "Payload too large",
    status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: "Unsupported media type",
    status.HTTP_422_UNPROCESSABLE_CONTENT: "Validation error",
    status.HTTP_429_TOO_MANY_REQUESTS: "Too many requests",
    status.HTTP_500_INTERNAL_SERVER_ERROR: "Internal server error",
    status.HTTP_503_SERVICE_UNAVAILABLE: "Service unavailable",
}

_SLUGS: Final[dict[int, str]] = {
    status.HTTP_400_BAD_REQUEST: "bad-request",
    status.HTTP_401_UNAUTHORIZED: "unauthorized",
    status.HTTP_403_FORBIDDEN: "forbidden",
    status.HTTP_404_NOT_FOUND: "not-found",
    status.HTTP_405_METHOD_NOT_ALLOWED: "method-not-allowed",
    status.HTTP_409_CONFLICT: "conflict",
    status.HTTP_413_CONTENT_TOO_LARGE: "payload-too-large",
    status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: "unsupported-media-type",
    status.HTTP_422_UNPROCESSABLE_CONTENT: "validation-error",
    status.HTTP_429_TOO_MANY_REQUESTS: "too-many-requests",
    status.HTTP_500_INTERNAL_SERVER_ERROR: "internal-server-error",
    status.HTTP_503_SERVICE_UNAVAILABLE: "service-unavailable",
}

_LOGGER = structlog.get_logger(__name__)


def problem_response(
    request: Request,
    status_code: int,
    detail: str,
    *,
    title: str | None = None,
    problem_type: str | None = None,
    headers: dict[str, str] | None = None,
    **extensions: Any,
) -> JSONResponse:
    """Build one `application/problem+json` response.

    ``instance`` is the request path rather than a generated URI: RFC 9457 allows a relative
    reference, and the path is the thing a reader actually wants when triaging.

    The request ID travels as an extension member. A user pasting an error payload into an issue
    is then handing over the exact key needed to find the server-side story, which a support
    conversation otherwise spends three round trips establishing.
    """
    slug = _SLUGS.get(status_code, "error")
    body: dict[str, Any] = {
        "type": problem_type or f"{PROBLEM_TYPE_BASE}/{slug}",
        "title": title or _TITLES.get(status_code, "Error"),
        "status": status_code,
        "detail": detail,
        "instance": request.url.path,
    }

    request_id = getattr(request.state, "request_id", None)
    if isinstance(request_id, str):
        body["request_id"] = request_id

    body.update(extensions)

    return JSONResponse(
        status_code=status_code,
        content=body,
        media_type=PROBLEM_CONTENT_TYPE,
        headers=headers,
    )


async def _handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
    """A route (or Starlette itself) refused the request on purpose."""
    # Registered for this exception type only; the narrowing is for mypy, not a runtime check.
    assert isinstance(exc, HTTPException)
    # `detail` is typed `Any` because HTTPException accepts any JSON-serialisable value. Coercing
    # keeps the field a string, which is what RFC 9457 specifies and what clients can render.
    detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    return problem_response(
        request,
        exc.status_code,
        detail,
        headers=dict(exc.headers) if exc.headers else None,
    )


async def _handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    """The request body, query or path did not typecheck; the route never ran.

    FastAPI's default is a bare ``{"detail": [...]}`` with a different shape from every other
    error the API can produce. Rewriting it into the same envelope means a client needs one error
    parser, and the per-field information survives as the ``errors`` extension member rather than
    being flattened into prose.
    """
    # Registered for this exception type only; the narrowing is for mypy, not a runtime check.
    assert isinstance(exc, RequestValidationError)
    errors = [
        {
            # `loc` is a tuple like ("body", "image", 0). Joined, because a JSON pointer-ish
            # string is what a form library can map back onto a field.
            "location": ".".join(str(part) for part in error.get("loc", ())),
            "message": error.get("msg", ""),
            "type": error.get("type", ""),
        }
        for error in exc.errors()
    ]
    return problem_response(
        request,
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "The request did not match the expected schema.",
        errors=errors,
    )


async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """A bug. Log everything, tell the client nothing but the request ID.

    The asymmetry is deliberate. Exception text leaks internals — file paths, driver messages,
    occasionally credentials — and the client can do nothing with it. The request ID is the one
    piece that lets a user report the failure precisely, and it costs nothing to disclose.
    """
    _LOGGER.exception(
        "unhandled_exception",
        path=request.url.path,
        method=request.method,
        exc_type=type(exc).__name__,
    )
    return problem_response(
        request,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "The server encountered an unexpected condition. The request ID identifies this failure "
        "in the server logs.",
    )


def register_error_handlers(app: FastAPI) -> None:
    """Attach all three handlers to an app instance.

    Registered against the app rather than installed globally so that two apps built by the
    factory in one test session cannot affect one another.
    """
    app.add_exception_handler(HTTPException, _handle_http_exception)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    # The catch-all runs in Starlette's ServerErrorMiddleware, which sits *outside* every
    # middleware this app adds — including the one that binds the request ID. So it is the net
    # for a failure in the middleware stack itself, and produces a problem document without a
    # request ID because at that point there is genuinely no request ID to report. Exceptions
    # raised by routes are caught closer in, by `request_id_middleware`, which calls the same
    # renderer with the ID available.
    app.add_exception_handler(Exception, handle_unexpected_error)
