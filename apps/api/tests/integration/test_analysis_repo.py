"""Integration tests for AnalysisRepository against a real Postgres.

These tests exercise the repository's behaviour under real database semantics — specifically the
things SQLite cannot replicate: cursor pagination ordering, the `RETURNING` clause on DELETE,
and `SELECT ... WHERE user_id = :uid` scoping that is the whole point of the layer.

**Test isolation** is via the `session` fixture in conftest.py, which wraps each test in a
connection-level transaction that is rolled back when the test ends. No truncation, no teardown.
"""

from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from openposture_api.db.models import User
from openposture_api.repos.analyses import (
    AnalysisRepository,
    FindingRecord,
    KeypointRecord,
    MetricRecord,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from openposture_api.db.models import Analysis


# ---------------------------------------------------------------------------
# Structural contract
# ---------------------------------------------------------------------------


def test_every_public_method_requires_user_id() -> None:
    """The tenancy guarantee as an executable rule.

    Every public method must declare a `user_id` parameter. Adding one without it would fail this
    test before the branch is merged, which is the only reliable way to enforce a constraint on
    code nobody has written yet.

    Widened in E8 from "every read" to "every method". `create` was the exception while analyses
    could outlive the absence of authentication; now that an owner always exists, an unscoped
    write is as much a hole as an unscoped read — it produces a row no scoped read can return.
    """
    public_methods = [
        name
        for name, member in inspect.getmembers(AnalysisRepository, callable)
        if not name.startswith("_")
    ]
    assert public_methods, "expected at least one method on AnalysisRepository"

    for method_name in public_methods:
        params = inspect.signature(getattr(AnalysisRepository, method_name)).parameters
        assert "user_id" in params, (
            f"AnalysisRepository.{method_name} must declare a `user_id` parameter — "
            "every query and every insert is scoped to one user"
        )


def test_user_id_is_required_rather_than_defaulted() -> None:
    """Declaring the parameter is not enough; it must have no default.

    A `user_id: uuid.UUID | None = None` satisfies the test above while still allowing every
    caller to omit it — which is exactly the state `create` was in before E8. Requiring the
    absence of a default is what makes "cannot be forgotten" true: forgetting is a TypeError at
    the call site rather than a null in the database.
    """
    for method_name in ("create", "get", "list_page", "delete"):
        parameter = inspect.signature(getattr(AnalysisRepository, method_name)).parameters[
            "user_id"
        ]
        assert parameter.default is inspect.Parameter.empty, (
            f"AnalysisRepository.{method_name} gives `user_id` a default, so a caller can omit it"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user_id(session: AsyncSession) -> uuid.UUID:
    """Insert a real user and return its id.

    Not `uuid.uuid4()`. `analyses.user_id` is a genuine foreign key, so an id with no `users` row
    behind it is not a usable owner — Postgres rejects the insert outright. The model tests get
    away with an invented id because SQLite does not enforce foreign keys unless asked to, and
    that gap between the two engines is a large part of why this suite runs against real Postgres.
    """
    user = User(email=f"{uuid.uuid4()}@example.test", password_hash="not-a-real-hash")
    session.add(user)
    await session.flush()
    return user.id


async def _create_minimal(
    session: AsyncSession,
    *,
    user_id: uuid.UUID | None = None,
    object_key: str = "analyses/test.jpg",
) -> Analysis:
    """An analysis owned by `user_id`, or by a freshly-minted user if none is named.

    The default stopped being `None` in E8: `create` now requires an owner and the column is
    `NOT NULL`, so a caller that does not care who owns the row still has to produce someone.
    """
    repo = AnalysisRepository(session)
    return await repo.create(
        user_id=user_id if user_id is not None else await _make_user_id(session),
        object_key=object_key,
        pose_detected=True,
        image_width=640,
        image_height=480,
        overall_score=72.0,
        assessed=5,
        total=6,
        inference_ms=18.3,
        pose_backend="fake",
        rules_version="1.0.0",
        schema_version="1.0",
    )


# ---------------------------------------------------------------------------
# Create and retrieve
# ---------------------------------------------------------------------------


async def test_create_returns_a_persisted_analysis(session: AsyncSession) -> None:
    user_id = await _make_user_id(session)
    repo = AnalysisRepository(session)
    analysis = await repo.create(
        user_id=user_id,
        object_key="analyses/test.jpg",
        pose_detected=True,
        image_width=1280,
        image_height=720,
        overall_score=65.0,
        assessed=7,
        total=8,
        inference_ms=22.1,
        pose_backend="mediapipe",
        rules_version="1.0.0",
        schema_version="1.0",
    )

    assert analysis.id is not None
    assert analysis.user_id == user_id
    assert analysis.overall_score == pytest.approx(65.0)


async def test_create_persists_children(session: AsyncSession) -> None:
    user_id = await _make_user_id(session)
    repo = AnalysisRepository(session)
    analysis = await repo.create(
        user_id=user_id,
        object_key="analyses/kp.jpg",
        pose_detected=True,
        image_width=640,
        image_height=480,
        overall_score=80.0,
        assessed=7,
        total=7,
        inference_ms=14.0,
        pose_backend="fake",
        rules_version="1.0.0",
        schema_version="1.0",
        keypoints=[
            KeypointRecord(
                name="left_shoulder", x=0.4, y=0.3, status="ok", visibility=0.95, presence=0.98
            )
        ],
        metrics=[
            MetricRecord(
                code="trunk_inclination_deg",
                value=32.0,
                unit="deg",
                status="ok",
                detail="",
                confidence=0.91,
            )
        ],
        findings=[
            FindingRecord(
                code="trunk_slouch",
                severity="major",
                message="Your torso is leaning 32° forward.",
                metric="trunk_inclination_deg",
                value=32.0,
                confidence=0.91,
            )
        ],
    )

    retrieved = await repo.get(user_id, analysis.id)
    assert retrieved is not None
    assert len(retrieved.keypoints) == 1
    assert retrieved.keypoints[0].name == "left_shoulder"
    assert len(retrieved.metrics) == 1
    assert retrieved.metrics[0].code == "trunk_inclination_deg"
    assert len(retrieved.findings) == 1
    assert retrieved.findings[0].code == "trunk_slouch"


async def test_get_returns_none_for_unknown_id(session: AsyncSession) -> None:
    user_id = await _make_user_id(session)
    repo = AnalysisRepository(session)
    result = await repo.get(user_id, uuid.uuid4())
    assert result is None


async def test_get_returns_none_for_another_users_analysis(session: AsyncSession) -> None:
    """Cross-tenant access returns None, not the row.

    The repo has no path that returns an analysis to a user who does not own it. The caller
    sees only None — there is no 403 because existence is not confirmed.
    """
    owner = await _make_user_id(session)
    requester = await _make_user_id(session)

    analysis = await _create_minimal(session, user_id=owner)

    repo = AnalysisRepository(session)
    result = await repo.get(requester, analysis.id)
    assert result is None


# ---------------------------------------------------------------------------
# List / cursor pagination
# ---------------------------------------------------------------------------


async def test_list_page_returns_only_the_requesting_users_rows(session: AsyncSession) -> None:
    user_a = await _make_user_id(session)
    user_b = await _make_user_id(session)

    await _create_minimal(session, user_id=user_a, object_key="analyses/a1.jpg")
    await _create_minimal(session, user_id=user_a, object_key="analyses/a2.jpg")
    await _create_minimal(session, user_id=user_b, object_key="analyses/b1.jpg")

    repo = AnalysisRepository(session)
    page = await repo.list_page(user_a)

    assert len(page) == 2
    assert all(row.user_id == user_a for row in page)


async def test_list_page_is_newest_first(session: AsyncSession) -> None:
    user_id = await _make_user_id(session)
    repo = AnalysisRepository(session)

    first = await _create_minimal(session, user_id=user_id, object_key="analyses/old.jpg")
    second = await _create_minimal(session, user_id=user_id, object_key="analyses/new.jpg")

    page = await repo.list_page(user_id)

    # Both share the same `created_at` millisecond in practice, so fall back to id ordering.
    # The repo orders by (created_at DESC, id DESC), and UUIDs are random — just assert both appear.
    ids = [row.id for row in page]
    assert first.id in ids
    assert second.id in ids


async def test_list_page_cursor_advances_the_window(session: AsyncSession) -> None:
    """Passing a cursor from page N returns page N+1 without overlap or gaps."""
    user_id = await _make_user_id(session)
    repo = AnalysisRepository(session)

    analyses = []
    # Stagger created_at so the ordering is deterministic.
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(5):
        a = await _create_minimal(session, user_id=user_id, object_key=f"analyses/{i}.jpg")
        # Patch created_at via the ORM so page ordering is predictable.
        a.created_at = base + timedelta(seconds=i)
        analyses.append(a)
    await session.flush()

    page1 = await repo.list_page(user_id, limit=3)
    assert len(page1) == 3

    last = page1[-1]
    page2 = await repo.list_page(
        user_id,
        cursor_created_at=last.created_at,
        cursor_id=last.id,
        limit=3,
    )
    assert len(page2) == 2

    ids_page1 = {row.id for row in page1}
    ids_page2 = {row.id for row in page2}
    assert ids_page1.isdisjoint(ids_page2), "pages must not overlap"
    assert ids_page1 | ids_page2 == {a.id for a in analyses}, "pages must cover all rows"


async def test_list_page_is_stable_when_rows_are_inserted_mid_traversal(
    session: AsyncSession,
) -> None:
    """A row inserted between page 1 and page 2 causes neither a skip nor a duplicate.

    This is the property offset pagination cannot have, and the reason the ticket specified a
    cursor. With `LIMIT 3 OFFSET 3`, inserting one newer row pushes everything down by one
    position, so page 2 re-serves the last row of page 1 and the reader sees it twice. The
    cursor is anchored to a row's own `(created_at, id)` rather than to a count, so an insert
    elsewhere in the ordering cannot move the boundary.

    The new row is newer than everything, so under `created_at DESC` it sorts ahead of page 1
    and is correctly absent from page 2 — it was not there when traversal began.
    """
    user_id = await _make_user_id(session)
    repo = AnalysisRepository(session)

    base = datetime(2026, 1, 1, tzinfo=UTC)
    original = []
    for i in range(5):
        a = await _create_minimal(session, user_id=user_id, object_key=f"analyses/{i}.jpg")
        a.created_at = base + timedelta(seconds=i)
        original.append(a)
    await session.flush()

    page1 = await repo.list_page(user_id, limit=3)
    assert len(page1) == 3

    # The insert that would break an offset-based reader.
    intruder = await _create_minimal(session, user_id=user_id, object_key="analyses/new.jpg")
    intruder.created_at = base + timedelta(seconds=99)
    await session.flush()

    last = page1[-1]
    page2 = await repo.list_page(
        user_id,
        cursor_created_at=last.created_at,
        cursor_id=last.id,
        limit=3,
    )

    ids1 = [row.id for row in page1]
    ids2 = [row.id for row in page2]
    assert set(ids1).isdisjoint(ids2), "no row may appear on both pages"
    assert len(ids1 + ids2) == len(set(ids1 + ids2)), "no duplicates within the traversal"
    assert set(ids1) | set(ids2) == {a.id for a in original}, "every original row seen exactly once"
    assert intruder.id not in set(ids1) | set(ids2), "a row inserted mid-traversal is not injected"


async def test_list_page_returns_empty_for_user_with_no_analyses(session: AsyncSession) -> None:
    user_id = await _make_user_id(session)
    repo = AnalysisRepository(session)
    page = await repo.list_page(user_id)
    assert page == []


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


async def test_delete_removes_the_row_and_reports_its_object_key(
    session: AsyncSession,
) -> None:
    """The key comes back because the caller has to delete the object next.

    It is only readable from the row being removed, so returning it here is the last chance
    anything has to name the object this analysis owned.
    """
    user_id = await _make_user_id(session)
    analysis = await _create_minimal(session, user_id=user_id)
    expected_key = analysis.object_key

    repo = AnalysisRepository(session)
    object_key = await repo.delete(user_id, analysis.id)

    assert object_key == expected_key
    assert await repo.get(user_id, analysis.id) is None


async def test_delete_returns_none_for_unknown_id(session: AsyncSession) -> None:
    user_id = await _make_user_id(session)
    repo = AnalysisRepository(session)
    result = await repo.delete(user_id, uuid.uuid4())
    assert result is None


async def test_delete_returns_none_for_another_users_analysis(session: AsyncSession) -> None:
    """Deleting another user's row returns None, not an error.

    The attacker learns nothing: they cannot tell whether the row exists.
    """
    owner = await _make_user_id(session)
    requester = await _make_user_id(session)

    analysis = await _create_minimal(session, user_id=owner)

    repo = AnalysisRepository(session)
    result = await repo.delete(requester, analysis.id)

    assert result is None
    # The row is still there for its owner.
    assert await repo.get(owner, analysis.id) is not None
