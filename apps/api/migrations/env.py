"""Alembic migration environment — async, with a Postgres advisory lock.

**Async engine, synchronous DDL.** SQLAlchemy's async engine wraps a synchronous one; Alembic's
migration operations are synchronous. The bridge is `connection.run_sync(do_run_migrations)`,
which runs the migration on the underlying synchronous connection while the async event loop owns
the surrounding connection lifecycle. This is the standard pattern for alembic + asyncpg.

**Why the advisory lock.** With more than one API replica starting simultaneously, each runs
`upgrade head` against the same database. Concurrent DDL on the same tables produces deadlocks,
partially applied migrations, or duplicate-object errors — and the symptom is a container that
crash-loops on deploy for reasons that look random. `SELECT pg_advisory_lock(N)` blocks until the
lock is free: the first instance migrates while the others wait, then find nothing to do.

`NullPool` is deliberate. Alembic runs once at startup and exits; a connection pool that keeps
connections open would have nothing to pool — `NullPool` makes the resource allocation explicit.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig
from typing import TYPE_CHECKING

from alembic import context
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy.pool import NullPool

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

# Alembic config object — access to values in alembic.ini.
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Import Base and register all model tables with its metadata. Both imports are load-bearing:
# Base brings the naming convention; the models import registers the seven table definitions.
# Without the second import, Base.metadata is empty and migrations operate on nothing.
import openposture_api.db.models  # noqa: E402, F401
from openposture_api.db.base import Base  # noqa: E402

target_metadata = Base.metadata

# Fixed key for the session-level advisory lock. The only requirement is uniqueness within
# the database instance — nothing else in this stack uses advisory locks. The value is
# arbitrary; changing it would leave an old migrator and a new one using different locks
# and therefore not serialising.
_ADVISORY_LOCK_KEY = 7374891


def run_migrations_offline() -> None:
    """Run migrations against a URL rather than a live connection.

    Used when generating SQL scripts offline. The URL comes from alembic.ini's placeholder
    unless overridden; OPENPOSTURE_DATABASE_URL is not consulted here.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run the actual migration operations on a synchronous connection.

    Called via `connection.run_sync` from the async path. The connection is the raw synchronous
    connection underlying the async one — Alembic's op-level functions expect this.
    """
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Acquire the advisory lock, migrate, release.

    Settings is constructed here (not at module level) so that the environment is fully
    initialised — OPENPOSTURE_DATABASE_URL set, dotenv loaded — before any validation runs.
    NullPool means the engine opens one connection and closes it when we exit the `async with`
    block, which is also when the session-level advisory lock is released.
    """
    from openposture_api.config import Settings

    settings = Settings()

    ini_section = config.get_section(config.config_ini_section) or {}
    ini_section["sqlalchemy.url"] = settings.database_url

    connectable = async_engine_from_config(
        ini_section,
        prefix="sqlalchemy.",
        poolclass=NullPool,
    )

    async with connectable.connect() as connection:
        # Acquire the session-level advisory lock. This call blocks until the lock is free,
        # so a second migrator waits here while the first runs its migrations. When the first
        # migrator's connection closes (end of this `async with`), the lock is released and
        # the second migrator acquires it, runs `upgrade head`, finds nothing to do, and exits.
        await connection.execute(text(f"SELECT pg_advisory_lock({_ADVISORY_LOCK_KEY})"))
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
