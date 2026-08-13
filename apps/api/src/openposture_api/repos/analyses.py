"""Repository for the analysis aggregate.

The analysis aggregate is the core multi-tenant resource. Every read method requires a
`user_id` — there is no method that returns an analysis without one. That is not a convention;
it is why a route that calls this repo cannot accidentally serve one user's analysis to another.
The 404-not-403 rule in E8 is a consequence of this: the repo returns None for "not found" and
for "found but wrong owner", so the route sees exactly one signal and renders it once.

**No unscoped reads.** A test in the integration suite enumerates every public method and
asserts that `user_id` appears in its signature *without a default*. Adding a method without
one will fail that test before the branch is merged.

**No unscoped writes either, as of E8.** `create` took `user_id` as an optional keyword until
authentication existed to supply one; it is now required, and `analyses.user_id` is `NOT NULL`.
An ownerless row is unreachable by construction — every read is scoped by `user_id`, and no
`user_id` matches null — so allowing one only meant writing data nothing could ever return.
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import and_, or_, select
from sqlalchemy import delete as sql_delete
from sqlalchemy.orm import selectinload

from openposture_api.db.models import Analysis, Finding, Keypoint, Metric

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = [
    "AnalysisRepository",
    "FindingRecord",
    "KeypointRecord",
    "MetricRecord",
    "MetricTrendPoint",
]


@dataclasses.dataclass(frozen=True)
class MetricTrendPoint:
    """One point of a metric's trend across a user's analyses, newest first."""

    created_at: datetime
    rules_version: str
    value: float | None
    status: str


@dataclasses.dataclass(frozen=True)
class KeypointRecord:
    """One landmark's data, ready to be inserted as a `Keypoint` row."""

    name: str
    x: float
    y: float
    status: str
    visibility: float
    presence: float


@dataclasses.dataclass(frozen=True)
class MetricRecord:
    """One metric's data, ready to be inserted as a `Metric` row."""

    code: str
    value: float | None
    unit: str
    status: str
    detail: str
    confidence: float | None


@dataclasses.dataclass(frozen=True)
class FindingRecord:
    """One finding's data, ready to be inserted as a `Finding` row."""

    code: str
    severity: str
    message: str
    metric: str
    value: float
    confidence: float


class AnalysisRepository:
    """Read and write operations on the analysis aggregate.

    One instance per request, constructed with the request's session. The session is
    not owned here — the caller created it and the caller will commit or roll back.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        object_key: str,
        pose_detected: bool,
        image_width: int,
        image_height: int,
        overall_score: float | None,
        assessed: int,
        total: int,
        inference_ms: float,
        pose_backend: str,
        rules_version: str,
        schema_version: str,
        keypoints: list[KeypointRecord] | None = None,
        metrics: list[MetricRecord] | None = None,
        findings: list[FindingRecord] | None = None,
    ) -> Analysis:
        """Persist a complete analysis with all its child rows.

        Flushes the analysis first so child rows have a valid `analysis_id`, then
        flushes the children. Does not commit — the caller controls the transaction.
        """
        analysis = Analysis(
            user_id=user_id,
            object_key=object_key,
            pose_detected=pose_detected,
            image_width=image_width,
            image_height=image_height,
            overall_score=overall_score,
            assessed=assessed,
            total=total,
            inference_ms=inference_ms,
            pose_backend=pose_backend,
            rules_version=rules_version,
            schema_version=schema_version,
        )
        self._session.add(analysis)
        await self._session.flush()

        if keypoints:
            self._session.add_all(
                Keypoint(
                    analysis_id=analysis.id,
                    name=kp.name,
                    x=kp.x,
                    y=kp.y,
                    status=kp.status,
                    visibility=kp.visibility,
                    presence=kp.presence,
                )
                for kp in keypoints
            )

        if metrics:
            self._session.add_all(
                Metric(
                    analysis_id=analysis.id,
                    code=m.code,
                    value=m.value,
                    unit=m.unit,
                    status=m.status,
                    detail=m.detail,
                    confidence=m.confidence,
                )
                for m in metrics
            )

        if findings:
            self._session.add_all(
                Finding(
                    analysis_id=analysis.id,
                    code=f.code,
                    severity=f.severity,
                    message=f.message,
                    metric=f.metric,
                    value=f.value,
                    confidence=f.confidence,
                )
                for f in findings
            )

        if keypoints or metrics or findings:
            await self._session.flush()

        return analysis

    async def get(
        self,
        user_id: uuid.UUID,
        analysis_id: uuid.UUID,
    ) -> Analysis | None:
        """Fetch one analysis with its children, scoped to user_id.

        Returns None both when the analysis does not exist and when it belongs to a
        different user. The caller sees only 404 — existence is not leaked to the requester.
        """
        stmt = (
            select(Analysis)
            .where(Analysis.id == analysis_id, Analysis.user_id == user_id)
            .options(
                selectinload(Analysis.keypoints),
                selectinload(Analysis.metrics),
                selectinload(Analysis.findings),
            )
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_page(
        self,
        user_id: uuid.UUID,
        *,
        cursor_created_at: datetime | None = None,
        cursor_id: uuid.UUID | None = None,
        limit: int = 20,
    ) -> list[Analysis]:
        """Return one page of analyses for a user, newest first.

        Cursor pagination on `(created_at DESC, id DESC)`. To advance past the first
        page, pass the last row's `created_at` and `id` from the previous response.

        Both cursor fields must be provided together or not at all — a cursor with only
        one half produces the same result as no cursor, silently.
        """
        stmt = (
            select(Analysis)
            .where(Analysis.user_id == user_id)
            .order_by(Analysis.created_at.desc(), Analysis.id.desc())
            .limit(limit)
        )

        if cursor_created_at is not None and cursor_id is not None:
            stmt = stmt.where(
                or_(
                    Analysis.created_at < cursor_created_at,
                    and_(
                        Analysis.created_at == cursor_created_at,
                        Analysis.id < cursor_id,
                    ),
                )
            )

        return list((await self._session.execute(stmt)).scalars().all())

    async def list_metric_trend(
        self,
        user_id: uuid.UUID,
        *,
        code: str,
    ) -> list[MetricTrendPoint]:
        """Every value of one metric across a user's analyses, newest first, in one query.

        This is the query named in `Metric.__table_args__`: it drives off
        `ix_analyses_user_id_created_at_id` and looks each metric up on the
        `(analysis_id, code)` prefix of `uq_metrics_analysis_id_code`. A plain indexed `SELECT`
        rather than an application-side loop deserialising documents is the entire payoff E2
        argued for and E10 spends.

        `value` and `status` come through unfiltered, gaps included — a row with
        `status != 'ok'` has `value IS NULL` by the database's own check constraint, so a gap
        here is already a gap, never a 0 this method would have to know to avoid inventing.
        """
        stmt = (
            select(Analysis.created_at, Analysis.rules_version, Metric.value, Metric.status)
            .join(Metric, Metric.analysis_id == Analysis.id)
            .where(Analysis.user_id == user_id, Metric.code == code)
            .order_by(Analysis.created_at.desc())
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            MetricTrendPoint(
                created_at=row.created_at,
                rules_version=row.rules_version,
                value=row.value,
                status=row.status,
            )
            for row in rows
        ]

    async def delete(
        self,
        user_id: uuid.UUID,
        analysis_id: uuid.UUID,
    ) -> str | None:
        """Delete an analysis, scoped to user_id, and report the object key it referenced.

        Returns None if no row matched. Returning None for another user's row is intentional:
        the caller sees no difference between "not found" and "found but not yours".

        **Returns the key rather than a bool** because the stored object has to be deleted too,
        and this is the last moment anything knows which one it was — the key lives only in the
        row being removed. A bool would leave the caller holding a successful delete and no way
        to name the object it just orphaned.

        CASCADE on the FK handles the children; no explicit child deletion needed.
        """
        result = await self._session.execute(
            sql_delete(Analysis)
            .where(Analysis.id == analysis_id, Analysis.user_id == user_id)
            .returning(Analysis.object_key)
        )
        await self._session.flush()
        return result.scalar_one_or_none()
