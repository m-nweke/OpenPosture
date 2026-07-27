"""Structured logging, and the request ID that makes it correlatable.

FINDINGS §4 records the legacy observability story in full: every result was a bare ``print()``
to stdout, with no timestamp, no level, and no way to tell which request produced which line.
Under any concurrency at all that output is unreadable, which is the same as having none.

The replacement is one event per log call with a stable set of keys, and a request ID bound into
a context variable so that *every* line emitted while handling a request carries it without any
call site having to pass it down. That is what turns "an error happened" into "these fourteen
lines are the request that failed, in order."
"""

from __future__ import annotations

import logging
import sys
import uuid
from typing import TYPE_CHECKING, Final, TextIO

import structlog

from openposture_api.errors import handle_unexpected_error

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from starlette.requests import Request
    from starlette.responses import Response

    from openposture_api.config import Settings

__all__ = [
    "REQUEST_ID_CONTEXT_KEY",
    "configure_logging",
    "current_request_id",
    "request_id_middleware",
]

REQUEST_ID_CONTEXT_KEY: Final = "request_id"

_MAX_INBOUND_REQUEST_ID = 128
"""Cap on a caller-supplied ID before it is replaced with a generated one.

The header is attacker-controlled and ends up in every log line for the request. Without a bound,
one request can write an arbitrarily large string into the log for each line it produces, which
is a cheap way to fill a disk. Length is checked rather than sanitised because the value is only
ever emitted as a JSON string value or an HTTP header, both of which are escaped by their writers.
"""


def configure_logging(settings: Settings, *, stream: TextIO | None = None) -> None:
    """Point both structlog and the standard library at one renderer.

    Configuring structlog alone is the common half-measure: uvicorn, starlette and every library
    log through :mod:`logging`, so half the output stays unstructured and the two formats
    interleave. Routing stdlib records through structlog's formatter is what makes the stream
    uniform.

    Idempotent, because the app factory may be called many times in one test session.

    Args:
        settings: Level and renderer come from here.
        stream: Where rendered lines go. Defaults to stderr — logs are diagnostics, and
            stdout belongs to whatever the process actually produces. A test passes a
            buffer so it can assert on the real rendered line rather than on a mock.
    """
    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]

    target = stream if stream is not None else sys.stderr

    # Colour is decided by the stream actually being written to, not by the process's stderr.
    # Asking stderr while rendering into a buffer gets the answer for the wrong file: run the
    # suite from a terminal and every captured line arrives wrapped in ANSI escapes, so an
    # assertion on the rendered text fails for reasons that have nothing to do with the code.
    # `isatty` is part of IOBase, but a caller can pass any file-like object, so this does not
    # assume it exists.
    colorize = callable(getattr(target, "isatty", None)) and target.isatty()

    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer()
        if settings.emit_json_logs
        else structlog.dev.ConsoleRenderer(colors=colorize)
    )

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(target)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    # Replace rather than append. Called twice without this, every line is emitted twice — and
    # the app factory is called once per test.
    root.handlers = [handler]
    root.setLevel(settings.log_level.upper())


def current_request_id() -> str | None:
    """The ID bound to the request being handled, if there is one.

    Returns ``None`` outside a request — during startup, in a background task, in a test that
    never went through the middleware — rather than inventing an ID that correlates with nothing.
    """
    bound = structlog.contextvars.get_contextvars().get(REQUEST_ID_CONTEXT_KEY)
    return bound if isinstance(bound, str) else None


def request_id_middleware(
    settings: Settings,
) -> Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]:
    """Build the middleware that binds a request ID for the lifetime of one request.

    Takes settings rather than reading them, so which header is honoured is a property of the app
    instance under test and not of the process environment.

    A caller-supplied ID is preferred when present: the frontend, or a proxy in front of it,
    generates one so that a browser-side error report and the server logs can be joined. It is
    accepted only if it is short enough to be safe to log; otherwise it is replaced rather than
    rejected, because a bad correlation header is not a reason to fail a request.
    """
    header_name = settings.request_id_header

    async def middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        # Stripped before it is judged: `"   "` is truthy and within the length bound, so
        # without this it would be accepted, echoed as a header nobody can search for, and
        # bound onto every log line for the request. Whitespace is not an identifier.
        inbound = (request.headers.get(header_name) or "").strip()
        request_id = inbound if 0 < len(inbound) <= _MAX_INBOUND_REQUEST_ID else uuid.uuid4().hex

        # `clear_contextvars` first: the context is per-task, and a task that handled an earlier
        # request could otherwise leak its bindings into this one.
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(**{REQUEST_ID_CONTEXT_KEY: request_id})
        # Stashed on the request too, so the error handlers can reach it without depending on
        # contextvar state surviving whatever raised.
        request.state.request_id = request_id

        try:
            response = await call_next(request)
        except Exception as exc:  # noqa: BLE001
            # BLE001 is enabled because blind excepts were the legacy engine's defining
            # failure mode — ~10 of them swallowing errors into a `print`. This one is the
            # opposite: it is a boundary handler that catches everything precisely so that
            # nothing is swallowed. The exception is logged with its traceback and turned
            # into a 500; a narrower clause here would let some failures escape to a
            # handler that cannot correlate them.
            # Caught *here* rather than left to Starlette's ServerErrorMiddleware, which sits
            # outside this middleware. Out there the request ID does not exist: the response
            # would carry no ID header, the problem document would carry no `request_id`, and
            # the "unhandled_exception" log line would be the one line in the request with
            # nothing to correlate it to — precisely when correlation is worth having.
            response = await handle_unexpected_error(request, exc)
        finally:
            structlog.contextvars.clear_contextvars()

        # Echoed on every response, including error responses — a client reporting a failure can
        # quote the ID and the logs for exactly that request are one grep away.
        response.headers[header_name] = request_id
        return response

    return middleware
