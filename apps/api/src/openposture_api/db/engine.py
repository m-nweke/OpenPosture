"""The async engine, the session factory, and the per-request session dependency.

**Async, on asyncpg.** The API is async end to end — the analysis endpoint awaits an upload,
inference and a storage write — and a synchronous driver blocks the event loop for the duration of
every query. Under the concurrency this application actually sees, several people uploading while
inference is running, that turns a fast query into a stall for every other request on the worker.

**One engine per process, built in `lifespan`, held on `app.state`.** The same arrangement as the
pose backend and the storage backend, and for the same three reasons: no module-level global, one
connection pool rather than one per import site, and a seam
(`app.dependency_overrides[get_session]`) that lets an endpoint test run against a different
database — or against no database at all.

**Nothing here connects eagerly.** `create_async_engine` builds a pool lazily; the first
connection is opened when the first query runs. That is what allows this to be wired into the
application now, in OP-50, while the API still has no route that touches the database: a
development stack with no Postgres running behaves exactly as it did before, and E5 turns the
first query on without a startup change.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import structlog
from fastapi import Request
from sqlalchemy.engine import make_url
from sqlalchemy.engine.default import DefaultDialect
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import AsyncAdaptedQueuePool, QueuePool

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from openposture_api.config import Settings

__all__ = [
    "DatabaseNotConfiguredError",
    "close_engine",
    "create_engine",
    "create_session_factory",
    "get_session",
]

_LOGGER = structlog.get_logger(__name__)


class DatabaseNotConfiguredError(RuntimeError):
    """Raised when a route asks for a session on an app whose lifespan never ran.

    In practice this means a test built an app with `create_app(...)` and called it without
    entering the `TestClient` context manager, so `lifespan` never created the session factory.
    The message says that, because the alternative — an `AttributeError` on `app.state` — sends
    people looking for a missing attribute rather than a missing startup.
    """


def create_engine(settings: Settings) -> AsyncEngine:
    """Build the process's engine.

    Args:
        settings: Configuration to read the database URL and pool sizing from.

    Returns:
        An engine with no open connections. The pool fills on first use.
    """
    url = make_url(settings.database_url)

    # Pool sizing is not universal: `pool_size` and `max_overflow` belong to the queue pools, and
    # SQLite picks a `StaticPool` or `NullPool` that rejects both outright. Passing them
    # unconditionally works against Postgres and raises `TypeError: Invalid argument(s)` the
    # moment anything points at SQLite — which the model tests do, deliberately, to stay
    # container-free. Asking the dialect which pool it would choose keeps this a property of the
    # database rather than a hardcoded exception for one driver.
    # `get_pool_class` lives on `DefaultDialect` rather than on the `Dialect` protocol, and every
    # real dialect subclasses it — the cast says so rather than reaching past the type checker
    # with an ignore.
    dialect = cast("type[DefaultDialect]", url.get_dialect())

    sizing: dict[str, int] = {}
    if issubclass(dialect.get_pool_class(url), (QueuePool, AsyncAdaptedQueuePool)):
        sizing = {
            "pool_size": settings.database_pool_size,
            "max_overflow": settings.database_max_overflow,
        }

    return create_async_engine(
        url,
        # Off by default and on when `OPENPOSTURE_LOG_LEVEL=debug`. SQLAlchemy's echo writes
        # every statement and every parameter set to stdout, which on this schema includes email
        # addresses and password hashes — so it is tied to an explicitly-chosen debug level
        # rather than to `environment != production`, which people set without meaning to opt
        # into that.
        echo=settings.log_level == "debug",
        # `pool_pre_ping` costs one trivial round trip per checkout and removes the entire class
        # of "first request after an idle period fails". Connections die for reasons outside the
        # process — a database restart, an idle timeout on a proxy — and without this the
        # application discovers that by handing a dead connection to a real request.
        pool_pre_ping=True,
        # Recycle below any sensible upstream idle timeout, so the pool retires a connection
        # before something else does it unannounced. Accepted by every pool class.
        pool_recycle=1800,
        **sizing,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Build the session factory bound to `engine`.

    `expire_on_commit=False` because the default is actively wrong for an API. On commit,
    SQLAlchemy expires every loaded attribute, so the next attribute access re-fetches from the
    database — and in an async session that emits IO from what looks like plain attribute access,
    which raises `MissingGreenlet` if it happens after the session has closed. Serialising a
    just-committed object is exactly that shape, so this would surface as a confusing error in
    every write endpoint.
    """
    return async_sessionmaker(
        engine,
        expire_on_commit=False,
        # Explicit: the routes own their transactions. An autobegin-and-autocommit session hides
        # where a transaction starts and ends, which is the wrong property for a layer whose
        # ordering guarantees (E5 writes rows then the object; E6 deletes in reverse) are the
        # point.
        autoflush=False,
    )


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, closed deterministically.

    The session is closed in a `finally`, so it is returned to the pool whether the handler
    returned, raised, or the client disconnected mid-response. A leaked session is a leaked
    connection, and a pool of leaked connections is an application that stops serving anything
    after `pool_size + max_overflow` requests — a failure that looks like a hang under load and
    like nothing at all in development.

    **This dependency does not commit.** It hands out a session and guarantees cleanup; deciding
    what constitutes a unit of work is the route's job, and a dependency that committed on the
    way out would commit partial work after a handler raised halfway through.

    Override it in tests with `app.dependency_overrides[get_session]`.
    """
    factory: async_sessionmaker[AsyncSession] | None = getattr(
        request.app.state, "session_factory", None
    )
    if factory is None:
        raise DatabaseNotConfiguredError(
            "no session factory on this app: lifespan has not run. In a test, enter the "
            "TestClient context manager, or override this dependency."
        )

    session = factory()
    try:
        yield session
    finally:
        await session.close()


async def close_engine(engine: AsyncEngine | None) -> None:
    """Dispose of the pool on shutdown.

    Tolerates `None` so the caller in `lifespan` does not need to know whether construction got
    far enough to produce an engine — the same shape as `close_pose_backend`.
    """
    if engine is None:
        return
    await engine.dispose()
    _LOGGER.info("database_engine_disposed")
