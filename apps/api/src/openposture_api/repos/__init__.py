"""Repository layer: thin typed wrappers over the ORM.

    from openposture_api.repos import AnalysisRepository

    async def create_analysis(session: AsyncSession) -> Analysis:
        repo = AnalysisRepository(session)
        return await repo.create(object_key="...", ...)

**Tenancy is scoped where a resource has an owner distinct from the requester.** Every read on
:class:`AnalysisRepository` takes a `user_id` — that is the structural guarantee that makes the
"404 not 403" rule in E8 enforceable at the layer, not just at the route.

:class:`UserRepository` and :class:`RefreshTokenRepository` deliberately do not. Their reads
(`get_by_email`, `get_by_hash`) run during the auth flow, where the caller is establishing *who*
the user is and so has no user_id to scope by yet. Requiring one there would be circular rather
than safe.

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
