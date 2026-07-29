"""Persistence: the declarative models, the async engine, and the session dependency.

    from openposture_api.db import get_session

    @router.get("/analyses")
    async def list_analyses(session: Annotated[AsyncSession, Depends(get_session)]) -> ...:
        ...

The models are in :mod:`openposture_api.db.models` and the three design decisions behind them are
documented there. The engine and the per-request session are in
:mod:`openposture_api.db.engine`.

Nothing in this package is imported by a route yet — OP-50 wires the machinery, and E5 (OP-53) is
the ticket that first writes a row. It is imported by the app factory, which builds the engine in
`lifespan`, and by Alembic (OP-51), which reads `Base.metadata`.
"""

from __future__ import annotations

from openposture_api.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from openposture_api.db.engine import (
    DatabaseNotConfiguredError,
    close_engine,
    create_engine,
    create_session_factory,
    get_session,
)
from openposture_api.db.models import (
    Analysis,
    Finding,
    Keypoint,
    Metric,
    PostureSession,
    RefreshToken,
    User,
)

__all__ = [
    "Analysis",
    "Base",
    "DatabaseNotConfiguredError",
    "Finding",
    "Keypoint",
    "Metric",
    "PostureSession",
    "RefreshToken",
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
    "User",
    "close_engine",
    "create_engine",
    "create_session_factory",
    "get_session",
]
