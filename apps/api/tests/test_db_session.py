"""The engine and the per-request session dependency.

Three properties, and the third is the one that matters most for a long-running service: a session
must be returned to the pool even when the handler raises. A leaked session is a leaked
connection, and a pool of leaked connections is an application that stops serving after
`pool_size + max_overflow` requests — a failure that looks like a hang under load and like nothing
at all in development.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import AsyncAdaptedQueuePool

from openposture_api.config import Settings
from openposture_api.db import (
    DatabaseNotConfiguredError,
    close_engine,
    create_engine,
    create_session_factory,
    get_session,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest.fixture
def sqlite_settings() -> Settings:
    """Settings pointed at an in-process database, so nothing here needs Postgres."""
    return Settings(
        environment="test",
        log_level="warning",
        json_logs=True,
        pose_backend="fake",
        database_url="sqlite+aiosqlite:///:memory:",
    )


def _app_with_session_route(factory: async_sessionmaker[AsyncSession]) -> FastAPI:
    """A minimal app whose one route depends on `get_session`.

    Built here rather than reusing the real application because the real one has no
    database-backed route until E5 — the dependency has to be exercised through the machinery it
    will actually run under, and that machinery is FastAPI's, not the analysis endpoint's.
    """
    app = FastAPI()
    app.state.session_factory = factory
    sessions_seen: list[AsyncSession] = []
    app.state.sessions_seen = sessions_seen

    @app.get("/probe")
    async def probe(session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, int]:
        sessions_seen.append(session)
        result = await session.execute(text("select 1"))
        return {"value": int(result.scalar_one())}

    @app.get("/explode")
    async def explode(session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, int]:
        sessions_seen.append(session)
        # Queries *before* raising, deliberately. A session checks a connection out lazily, on
        # first use, so a handler that fails before touching the database leaks nothing and would
        # make this test pass with the `finally` deleted. The leak being asserted here is the
        # realistic one: real work, then a failure partway through.
        await session.execute(text("select 1"))
        raise RuntimeError("the handler failed after taking a session")

    return app


@pytest.fixture
async def sqlite_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    yield create_session_factory(engine)
    await engine.dispose()


class ConnectionLedger:
    """Counts pool checkouts and check-ins on an engine.

    The direct way to assert "no connection was leaked", and the only one that means anything
    across dialects. `AsyncSession.is_active` looks like the check to write and is not: it reports
    whether the session is in a failed-flush state, and stays `True` on a perfectly closed
    session — so an assertion on it passes whether or not the `finally` exists.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self.checkouts = 0
        self.checkins = 0
        event.listen(engine.sync_engine, "checkout", self._on_checkout)
        event.listen(engine.sync_engine, "checkin", self._on_checkin)

    def _on_checkout(self, *_: object) -> None:
        self.checkouts += 1

    def _on_checkin(self, *_: object) -> None:
        self.checkins += 1

    @property
    def outstanding(self) -> int:
        return self.checkouts - self.checkins


@pytest.fixture
async def ledgered() -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], ConnectionLedger]]:
    """A session factory alongside a ledger of its engine's pool traffic."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    yield create_session_factory(engine), ConnectionLedger(engine)
    await engine.dispose()


def test_the_engine_is_built_without_connecting() -> None:
    """Construction opens no connection, which is what lets OP-50 ship before any route uses it.

    Built against a host that does not resolve, on purpose: if `create_async_engine` connected
    eagerly this would raise, and the fact that it does not is the property. A development stack
    with no Postgres running therefore starts exactly as it did before this ticket, and E5 turns
    the first query on without a startup change.
    """
    settings = Settings(
        database_url="postgresql+asyncpg://someone:secret@no-such-host.invalid:5432/nothing"
    )
    pool = create_engine(settings).pool

    # Narrowed rather than cast: a Postgres URL must produce a queue pool, and if it ever stops
    # doing so the sizing settings are silently doing nothing, which is worth failing on.
    assert isinstance(pool, AsyncAdaptedQueuePool)
    assert pool.checkedout() == 0
    assert pool.size() == settings.database_pool_size


def test_pool_sizing_is_only_applied_where_the_pool_accepts_it() -> None:
    """SQLite picks a pool that rejects `pool_size` outright.

    Passing it unconditionally raises `TypeError: Invalid argument(s)` the moment anything points
    at SQLite — which the model tests do, deliberately, to stay container-free.
    """
    engine = create_engine(Settings(database_url="sqlite+aiosqlite:///:memory:"))
    assert engine.pool.__class__.__name__ == "StaticPool"


def test_statement_logging_is_tied_to_the_debug_level() -> None:
    """SQLAlchemy's echo prints every statement *and every parameter set*.

    On this schema that includes email addresses and password hashes, so it is bound to an
    explicitly-chosen debug level rather than to `environment != production`, which people set
    without meaning to opt into that.
    """
    quiet = create_engine(Settings(log_level="info", database_url="sqlite+aiosqlite://"))
    loud = create_engine(Settings(log_level="debug", database_url="sqlite+aiosqlite://"))
    assert quiet.echo is False
    assert loud.echo is True


async def test_the_session_survives_a_commit(
    sqlite_factory: async_sessionmaker[AsyncSession],
) -> None:
    """`expire_on_commit=False`, and the default is actively wrong for an API.

    On commit SQLAlchemy expires every loaded attribute, so the next attribute access re-fetches —
    which, in an async session, emits IO from what looks like plain attribute access and raises
    `MissingGreenlet` once the session has closed. Serialising a just-committed object is exactly
    that shape.
    """
    async with sqlite_factory() as session:
        assert session.sync_session.expire_on_commit is False


def test_each_request_gets_its_own_session(
    sqlite_factory: async_sessionmaker[AsyncSession],
) -> None:
    app = _app_with_session_route(sqlite_factory)
    with TestClient(app) as client:
        assert client.get("/probe").json() == {"value": 1}
        assert client.get("/probe").json() == {"value": 1}

    first, second = app.state.sessions_seen
    assert first is not second


def test_a_session_returns_its_connection_when_the_handler_returns(
    ledgered: tuple[async_sessionmaker[AsyncSession], ConnectionLedger],
) -> None:
    factory, ledger = ledgered
    app = _app_with_session_route(factory)
    with TestClient(app) as client:
        client.get("/probe")

    assert ledger.checkouts == 1
    assert ledger.outstanding == 0


def test_a_session_returns_its_connection_when_the_handler_raises(
    ledgered: tuple[async_sessionmaker[AsyncSession], ConnectionLedger],
) -> None:
    """The property the `finally` exists for.

    Without it, every failing request leaks a connection, and the pool runs out silently at
    whatever concurrency happens to reach it first — a failure that looks like a hang under load
    and like nothing at all in development.
    """
    factory, ledger = ledgered
    app = _app_with_session_route(factory)
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/explode").status_code == 500

    assert ledger.checkouts == 1
    assert ledger.outstanding == 0


def test_the_dependency_is_overridable(
    sqlite_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The seam that lets an endpoint test run against a different database, or none at all."""
    app = _app_with_session_route(sqlite_factory)
    substitute = sqlite_factory()

    async def override() -> AsyncIterator[AsyncSession]:
        yield substitute

    app.dependency_overrides[get_session] = override
    with TestClient(app) as client:
        assert client.get("/probe").json() == {"value": 1}

    assert app.state.sessions_seen == [substitute]


def test_asking_for_a_session_before_startup_says_so(
    sqlite_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The error names the cause: lifespan did not run.

    The alternative is an `AttributeError` on `app.state`, which sends people looking for a
    missing attribute rather than a missing startup — usually a `TestClient` used without its
    context manager.
    """
    app = _app_with_session_route(sqlite_factory)
    del app.state.session_factory

    with (
        TestClient(app, raise_server_exceptions=True) as client,
        pytest.raises(DatabaseNotConfiguredError, match="lifespan has not run"),
    ):
        client.get("/probe")


async def test_closing_a_missing_engine_is_not_an_error() -> None:
    """Same shape as `close_pose_backend`: the caller in `lifespan` should not have to know
    whether construction got far enough to produce an engine."""
    await close_engine(None)


def test_the_real_app_builds_an_engine_on_startup(sqlite_settings: Settings) -> None:
    from openposture_api.main import create_app

    app = create_app(sqlite_settings)
    with TestClient(app):
        assert app.state.db_engine is not None
        assert app.state.session_factory is not None
