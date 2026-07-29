"""The declarative base and the two mixins every table shares.

Kept apart from :mod:`openposture_api.db.models` so that Alembic (OP-51) can import `Base` for its
metadata without importing the engine, and so a model file cannot accidentally become the place
where a primary-key or timestamp convention is decided a second time.

**Why `Uuid` primary keys rather than `BIGSERIAL`.** Analysis identifiers appear in URLs
(`GET /analyses/{id}`), and a sequential integer there is an enumerable handle on other people's
data: `/analyses/41` tells you `/analyses/40` exists and belongs to somebody. Epic E's
authorization rule is that another user's analysis returns 404 rather than 403 precisely so
existence is not leaked, and a guessable key would leak it a different way — through how many rows
the sequence has reached. UUIDv4 costs 16 bytes against 8 and removes the question.

`Uuid` is SQLAlchemy's dialect-agnostic type: a native `uuid` column on Postgres, `CHAR(32)` on
SQLite. That is what lets the model tests run in-process without a container, while the deployed
schema still gets the real type.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

if TYPE_CHECKING:
    from typing import Any

__all__ = ["Base", "TimestampMixin", "UUIDPrimaryKeyMixin"]


# Explicit constraint naming, and it matters more than it looks. Alembic's autogenerate compares
# the models against the database and emits `ALTER`s; without a naming convention, an unnamed
# constraint gets a different system-assigned name in each, and OP-51's drift check — "does
# autogenerate produce an empty diff?" — reports spurious differences forever. Setting this once,
# before the first migration exists, is far cheaper than renaming constraints afterwards.
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for every table in the service.

    Alembic reads `Base.metadata`; nothing else should.
    """

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    def __repr__(self) -> str:
        """Identify the row without dumping its contents.

        The default `<object at 0x...>` is useless in a log line, and a full column dump is worse
        than useless here: these tables hold an email address and a password hash, and a repr is
        exactly the thing that ends up in an exception traceback shipped to a log aggregator.
        """
        identifier: Any = getattr(self, "id", None)
        return f"<{type(self).__name__} id={identifier}>"


# Server-side `now()`, deliberately not a Python `default=datetime.utcnow`. Timestamps come from
# the database rather than from the application process: two API containers with drifting clocks
# would otherwise produce a history whose ordering disagrees with its `created_at` values, and E6
# paginates on exactly that ordering.
_NOW = func.now()


class UUIDPrimaryKeyMixin:
    """A random primary key, generated in Python.

    Client-side generation rather than a `gen_random_uuid()` server default, because the
    application needs the id *before* the flush in order to build child rows — an analysis knows
    its own id while it is still assembling its keypoints, metrics and findings, which turns four
    inserts into one flush instead of a round trip per parent.
    """

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    """`created_at` and `updated_at`, both database-generated and both timezone-aware.

    `DateTime(timezone=True)` maps to `TIMESTAMPTZ`, not `TIMESTAMP`. A naive timestamp column is
    the classic way to lose an hour twice a year, and this application shows users a history
    ordered by time.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=_NOW,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=_NOW,
        # `onupdate` is emitted by SQLAlchemy on UPDATE, so it holds for ORM writes and not for
        # raw SQL run against the database by hand. That is an accepted limit: a trigger would
        # cover both, at the cost of a schema object Alembic cannot autogenerate.
        onupdate=_NOW,
        nullable=False,
    )
