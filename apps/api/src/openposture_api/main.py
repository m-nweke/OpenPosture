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

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import structlog
from fastapi import FastAPI
from starlette.staticfiles import StaticFiles

from openposture_api import __version__
from openposture_api.analyses import build_analyses_router, register_analysis_error_handlers
from openposture_api.auth import build_auth_router
from openposture_api.config import Settings, get_settings
from openposture_api.db import close_engine, create_engine, create_session_factory
from openposture_api.errors import register_error_handlers
from openposture_api.health import ReadinessCheck, build_health_router
from openposture_api.logging import configure_logging, request_id_middleware
from openposture_api.pose import (
    PoseBackendState,
    PoseBackendUnavailableError,
    close_pose_backend,
    handle_pose_backend_unavailable,
    load_pose_backend,
)
from openposture_api.rate_limit import build_limiter, register_rate_limit_handlers
from openposture_api.storage import LOCAL_BACKEND, create_storage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

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
    load_backend: bool = True,
) -> FastAPI:
    """Construct an application instance.

    Args:
        settings: Configuration to build against. Defaults to the process settings read from the
            environment. Tests pass an explicit instance rather than mutating `os.environ`.
        readiness_probes: Extra subsystem checks for `/health/ready`. The pose backend registers
            itself; these are appended after it. Injected so a test can build an app whose
            dependencies are pretend.
        load_backend: Whether `lifespan` builds the pose backend. Tests that override
            `get_pose_backend` set this to `False` so no model is touched and startup is instant.
            A deployment never sets it.

    Returns:
        A configured app. Constructing it still performs no I/O — the backend is built when
        `lifespan` runs, which is on startup, not at import.
    """
    resolved = settings or get_settings()

    # Before anything else, so that any log line emitted during construction is already
    # structured and at the configured level.
    configure_logging(resolved)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Load the model once, here, and release it on the way out.

        Startup is synchronous on purpose: the ASGI server accepts no connections until this
        returns, so an instance that is still loading cannot be sent traffic. That *is* the
        not-ready state — an orchestrator probing during it gets a refused connection rather
        than a 503, which reaches the same conclusion without a background task and the
        half-initialised window one would open.
        """
        # Loaded before the `try` deliberately: if this raises there is nothing to release, and
        # wrapping it would mean the teardown below had to cope with a state that was never
        # constructed.
        state = load_pose_backend(resolved) if load_backend else PoseBackendState()
        app.state.pose_backend_state = state

        # Present before anything that can fail, so the teardown always finds an attribute
        # rather than an `AttributeError` that would replace the real startup error with a
        # misleading one. `close_engine` takes `None` for the same reason.
        app.state.db_engine = None
        app.state.session_factory = None

        # Everything from here is inside the `try`, so a failure part-way through startup still
        # releases what was already acquired. Constructing the engine outside it would mean an
        # unusable database URL leaked a loaded pose model — tens of seconds of work and a live
        # native handle — on every failed boot.
        try:
            # Built here rather than at construction because `local` creates its root directory
            # and `s3` builds a boto3 client — both side effects, and the factory promises none.
            app.state.storage = create_storage(resolved)
            _LOGGER.info("storage_ready", backend=app.state.storage.name)

            # The engine opens no connection here: `create_async_engine` fills its pool lazily,
            # on the first query. That is deliberate and load-bearing — as of OP-50 no route
            # touches the database, so a stack running without Postgres starts exactly as it did
            # before, and E5 turns the first query on without changing startup at all.
            #
            # It also means an unreachable database is *not* a startup failure. It does not mean
            # nothing can fail here: a malformed URL or an uninstalled driver raises immediately,
            # which is the case the `try` exists for.
            #
            # When the database becomes load-bearing in E5, `/health/ready` should gain a probe
            # for it, so an instance that cannot reach Postgres is taken out of rotation rather
            # than serving 500s. Adding that probe now would fail readiness for the whole current
            # stack, which does not use it.
            app.state.db_engine = create_engine(resolved)
            app.state.session_factory = create_session_factory(app.state.db_engine)
            _LOGGER.info("database_configured", pool_size=resolved.database_pool_size)

            yield
        finally:
            close_pose_backend(state)
            await close_engine(app.state.db_engine)

    app = FastAPI(
        title="OpenPosture API",
        description=_DESCRIPTION,
        version=__version__,
        docs_url=resolved.docs_url,
        # ReDoc is a second rendering of the same schema; one is enough, and each route is
        # surface area. `/openapi.json` stays available because OP-45 generates the frontend's
        # types from it.
        redoc_url=None,
        lifespan=lifespan,
    )

    app.state.settings = resolved
    # Present before startup so that a route reaching for it during a failed lifespan finds a
    # state object describing "not loaded" rather than an AttributeError.
    app.state.pose_backend_state = PoseBackendState()
    # Built here, one per app, for the same reason `app.state.settings` is: two apps in one test
    # session must not share limiter state. `resolve_client_ip` reads `app.state.settings`, which
    # is why this is assigned after it rather than before.
    app.state.limiter = build_limiter()

    # Registered before the routes it wraps. Starlette runs middleware outermost-first, so this
    # binds the request ID before any handler — including the error handlers — can log.
    app.middleware("http")(request_id_middleware(resolved))

    register_error_handlers(app)
    app.add_exception_handler(PoseBackendUnavailableError, handle_pose_backend_unavailable)
    register_analysis_error_handlers(app)
    register_rate_limit_handlers(app)

    # The probe reads `app.state` when it runs, not when it is registered, so binding it here —
    # before lifespan has produced a state — reports the real thing at request time.
    async def pose_probe() -> ReadinessCheck:
        state: PoseBackendState = app.state.pose_backend_state
        return await state.probe()

    probes: list[ReadinessProbe] = [pose_probe, *(readiness_probes or [])]

    app.include_router(
        build_health_router(version=__version__, probes=probes),
    )
    app.include_router(build_auth_router(app.state.limiter, resolved))
    app.include_router(build_analyses_router(app.state.limiter, resolved))

    if resolved.storage_backend == LOCAL_BACKEND:
        # `LocalDiskStorage.url_for` builds URLs under `media_base_url` (D3) on the promise that
        # something serves them; until E10 nothing dereferenced one, so nothing had to yet. The
        # `s3` backend needs no mount here — its `url_for` returns a presigned URL straight to
        # the object store.
        #
        # `check_dir=False`: at this point in `create_app`, `lifespan` has not run, so
        # `LocalDiskStorage.__init__` has not yet created `storage_root`. It exists by the time a
        # request can arrive — lifespan startup completes before the ASGI server accepts
        # connections — so the check would only ever fail on a directory the app is about to
        # create itself.
        app.mount(
            resolved.media_base_url,
            StaticFiles(directory=resolved.storage_root, check_dir=False),
            name="media",
        )

    _LOGGER.info(
        "app_created",
        environment=resolved.environment,
        version=__version__,
        docs_enabled=resolved.docs_url is not None,
        pose_backend=resolved.pose_backend if load_backend else "disabled",
        readiness_probes=len(probes),
    )

    return app


app = create_app()
"""The ASGI entry point: `uvicorn openposture_api.main:app`.

Import this from application code and you have reintroduced exactly the coupling the factory
exists to remove. Tests build their own with `create_app(...)`.
"""
