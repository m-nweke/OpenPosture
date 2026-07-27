"""The app factory.

`API/app.py` built its Flask application at import time. That single line is why none of it was
testable: importing the module started a real app bound to real Firebase credentials, so a test
could neither substitute a dependency nor construct two differently-configured instances. There
was no seam.

:func:`create_app` is that seam. The app is a *value* you construct, with settings passed in, so a
test builds one configured for production and one for development in the same session and they
cannot interfere. `app.dependency_overrides` — the reason FastAPI was chosen over Flask
(ADR-0001) — only works on an app you can construct.

The module-level `app` at the bottom exists solely because ASGI servers need an importable target.
It is the deployment entry point and nothing else should import it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from fastapi import FastAPI

from openposture_api import __version__
from openposture_api.config import Settings, get_settings
from openposture_api.errors import register_error_handlers
from openposture_api.health import build_health_router
from openposture_api.logging import configure_logging, request_id_middleware

if TYPE_CHECKING:
    from openposture_api.health import ReadinessProbe

__all__ = ["app", "create_app"]

_LOGGER = structlog.get_logger(__name__)

_DESCRIPTION = """
Posture assessment from a single photograph.

The analysis is deterministic and model-derived: landmarks come from a pose backend, and the
verdicts come from a pure rules engine that reports a *gap* when it cannot measure something
rather than guessing.
"""


def create_app(
    settings: Settings | None = None,
    *,
    readiness_probes: list[ReadinessProbe] | None = None,
) -> FastAPI:
    """Construct an application instance.

    Args:
        settings: Configuration to build against. Defaults to the process settings read from the
            environment. Tests pass an explicit instance rather than mutating `os.environ`.
        readiness_probes: Subsystem checks for `/health/ready`. Empty until OP-40 registers the
            pose backend; injected so a test can build an app whose dependencies are pretend.

    Returns:
        A configured app. Constructing it performs no I/O and opens no connections — that starts
        with the `lifespan` handler in OP-40.
    """
    resolved = settings or get_settings()

    # Before anything else, so that any log line emitted during construction is already
    # structured and at the configured level.
    configure_logging(resolved)

    app = FastAPI(
        title="OpenPosture API",
        description=_DESCRIPTION,
        version=__version__,
        docs_url=resolved.docs_url,
        # ReDoc is a second rendering of the same schema; one is enough, and each route is
        # surface area. `/openapi.json` stays available because OP-45 generates the frontend's
        # types from it.
        redoc_url=None,
    )

    app.state.settings = resolved

    # Registered before the routes it wraps. Starlette runs middleware outermost-first, so this
    # binds the request ID before any handler — including the error handlers — can log.
    app.middleware("http")(request_id_middleware(resolved))

    register_error_handlers(app)

    app.include_router(
        build_health_router(version=__version__, probes=readiness_probes),
    )

    _LOGGER.info(
        "app_created",
        environment=resolved.environment,
        version=__version__,
        docs_enabled=resolved.docs_url is not None,
        readiness_probes=len(readiness_probes or []),
    )

    return app


app = create_app()
"""The ASGI entry point: `uvicorn openposture_api.main:app`.

Import this from application code and you have reintroduced exactly the coupling the factory
exists to remove. Tests build their own with `create_app(...)`.
"""
