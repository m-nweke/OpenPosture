"""Repository layer: thin typed wrappers over the ORM, each scoped by user_id.

    from openposture_api.repos import AnalysisRepository

    async def create_analysis(session: AsyncSession) -> Analysis:
        repo = AnalysisRepository(session)
        return await repo.create(object_key="...", ...)

Every read method on every repo here takes a `user_id` — that is the structural guarantee that
makes the "404 not 403" rule in E8 enforceable at the layer, not just at the route.

Transaction ownership lives with the caller. The repos flush to make new rows visible within the
current transaction, but they never commit or roll back. A route that writes two aggregates in one
request owns the decision about whether that is one unit of work or two.
"""

from __future__ import annotations

from openposture_api.repos.analyses import (
    AnalysisRepository,
    FindingRecord,
    KeypointRecord,
    MetricRecord,
)
from openposture_api.repos.refresh_tokens import RefreshTokenRepository
from openposture_api.repos.users import UserRepository

__all__ = [
    "AnalysisRepository",
    "FindingRecord",
    "KeypointRecord",
    "MetricRecord",
    "RefreshTokenRepository",
    "UserRepository",
]
