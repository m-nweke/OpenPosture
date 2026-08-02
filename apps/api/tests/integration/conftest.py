"""Shared fixtures for integration tests.

Each integration test module gets one Postgres container (module scope), which keeps container
startup time out of the per-test measurement. Each test function gets its own session backed by a
connection-level transaction that is rolled back at the end, so tests are isolated without
truncation.

The schema is applied with `Base.metadata.create_all` rather than `alembic upgrade head` because:
  - these tests assert repository behaviour, not migration correctness
  - the migration round-trip (`upgrade head`, `check`) is already covered in `integration.yml`
  - `create_all` is faster and has no dependency on the filesystem layout

`asyncio_mode = "auto"` in the root `pyproject.toml` means all async fixtures and test functions
are automatically treated as coroutines. No `@pytest.mark.asyncio` markers are needed.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from testcontainers.community.postgres import PostgresContainer

import openposture_api.db.models  # noqa: F401  side-effect: registers all tables with Base.metadata
from openposture_api.db.base import Base


@pytest.fixture(scope="module")
def pg_dsn() -> Iterator[str]:
    """Start a real Postgres container and apply the schema.

    Module-scoped: one container per test module, not one per test. Container startup
    dominates wall clock here (~5 s on a cold pull), so a per-test container would make the
    integration suite unusable as a fast-feedback loop. The rollback fixture below is what
    keeps tests isolated despite sharing the container.

    Yields an asyncpg-style DSN: `postgresql+asyncpg://...`.
    """
    with PostgresContainer("postgres:16.10-alpine") as container:
        # Ask for the driver by name rather than rewriting the URL. `get_connection_url()` defaults
        # to `postgresql+psycopg2://`, so a `postgresql://` -> `postgresql+asyncpg://` string
        # replace matches nothing and hands SQLAlchemy the psycopg2 dialect — which then fails on
        # an import of a driver this project deliberately does not install (ADR: async end to end).
        async_url = container.get_connection_url(driver="asyncpg")

        async def _apply_schema() -> None:
            engine = create_async_engine(async_url, echo=False)
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            await engine.dispose()

        # asyncio.run() is safe in a sync fixture: it creates its own event loop, runs the
        # coroutine, and tears the loop down. pytest-asyncio's per-test loop is separate and
        # has not been created yet when the module fixture runs.
        asyncio.run(_apply_schema())
        yield async_url


@pytest_asyncio.fixture
async def session(pg_dsn: str) -> AsyncIterator[AsyncSession]:
    """One session per test, backed by a rolled-back transaction.

    The pattern: begin a connection-level transaction, bind the session to that connection,
    yield the session, then roll back the connection transaction. The session sees its own
    writes (via flush), but nothing commits to the database — the next test starts clean.

    The engine is created and disposed per test. Engine creation is cheap (no connections
    until the first query), and tying the engine to the module scope would require a
    module-scoped async fixture, which introduces event-loop scope complexity for negligible gain.
    """
    engine = create_async_engine(pg_dsn, echo=False)
    conn = await engine.connect()
    trans = await conn.begin()
    db_session = AsyncSession(bind=conn, expire_on_commit=False)
    try:
        yield db_session
    finally:
        await db_session.close()
        await trans.rollback()
        await conn.close()
    await engine.dispose()
