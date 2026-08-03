"""The schema, asserted as a schema.

Most of these read `Base.metadata` rather than talking to a database, because most of what OP-50
promises is a property of the *declaration*: a column is non-nullable, an index exists, no column
holds a URL. Those are worth checking without a container, and they are the checks that would
otherwise be silently lost the first time someone adds a table.

The rest use SQLite through `aiosqlite`. The models are deliberately dialect-neutral — `Uuid`,
`JSON` and `native_enum=False` all map onto SQLite — so these exercise the real mappings and the
real `CHECK` constraints in-process. What they cannot exercise is Postgres-specific behaviour;
that is E4's `testcontainers` suite, deliberately a separate ticket.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, get_args

import pytest
from sqlalchemy import UniqueConstraint, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from openposture_api import schemas
from openposture_api.db import Analysis, Base, Finding, Keypoint, Metric, PostureSession, User
from openposture_api.db.models import (
    KeypointStatusType,
    MetricStatusType,
    SeverityType,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

EXPECTED_TABLES = {
    "users",
    "refresh_tokens",
    "sessions",
    "analyses",
    "keypoints",
    "metrics",
    "findings",
}


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """A session against a fresh in-memory SQLite database with the real schema applied."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as open_session:
        # SQLite ignores foreign keys unless asked, and the cascade tests below are meaningless
        # without them. Postgres needs no equivalent — this is a SQLite default, not a schema
        # property, which is exactly the kind of difference E4's real-Postgres suite exists for.
        await open_session.execute(text("PRAGMA foreign_keys=ON"))
        yield open_session

    await engine.dispose()


async def _make_owner(session: AsyncSession) -> uuid.UUID:
    """A user row to hang an analysis off.

    `analyses.user_id` is a real foreign key and these tests run with `PRAGMA foreign_keys=ON`, so
    a random UUID would not do — the row has to exist. The address is derived from a fresh UUID
    because `users.email` is unique and several tests make more than one owner. Lowercase by
    construction, which the column's CHECK constraint requires.
    """
    user = User(email=f"{uuid.uuid4().hex}@example.com", password_hash="$argon2id$fake")
    session.add(user)
    await session.flush()
    return user.id


async def _make_analysis(session: AsyncSession, **overrides: object) -> Analysis:
    """An analysis with every non-nullable column filled, so a test can vary one thing."""
    # Only minted when the caller did not name an owner — otherwise every call that passes its own
    # `user_id` would still insert a second, unreferenced user row.
    if "user_id" not in overrides:
        overrides["user_id"] = await _make_owner(session)

    defaults: dict[str, object] = {
        "object_key": "analyses/abc123.jpg",
        "pose_detected": True,
        "image_width": 640,
        "image_height": 480,
        "assessed": 7,
        "total": 7,
        "inference_ms": 12.5,
        "overall_score": 70.0,
        "pose_backend": "fake",
        "rules_version": "1.0.0",
        "schema_version": "1.0",
    }
    analysis = Analysis(**{**defaults, **overrides})
    session.add(analysis)
    await session.flush()
    return analysis


# --- the declaration ---------------------------------------------------------------------------


def test_the_schema_is_exactly_the_seven_planned_tables() -> None:
    """A new table should be a deliberate act, visible in this diff.

    The plan names seven. An eighth appearing without this test failing means a model was added
    without anyone deciding it belonged.
    """
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_no_column_anywhere_stores_a_url() -> None:
    """D3's rule, enforced against the schema rather than trusted.

    A URL embeds a hostname and often an expiry. Persisting one means that moving the object
    store — or switching a path-style MinIO for a real S3 — invalidates every row that has one.
    The storage layer builds URLs from keys at read time, and the way that stays true is for
    there to be nowhere to put a URL.
    """
    offenders = [
        f"{table}.{column.name}"
        for table, definition in Base.metadata.tables.items()
        for column in definition.columns
        if "url" in column.name.lower() or "uri" in column.name.lower()
    ]
    assert offenders == [], f"columns that look like they store a URL: {offenders}"


def test_the_image_reference_is_a_key() -> None:
    """The positive half of the test above: the column exists and is named for what it holds."""
    assert "object_key" in Base.metadata.tables["analyses"].columns


@pytest.mark.parametrize("stamp", ["pose_backend", "rules_version", "schema_version"])
def test_the_version_stamps_are_not_nullable(stamp: str) -> None:
    """A nullable stamp is a row nobody can interpret.

    These three are what keep a history readable after a retune: without them, a threshold change
    silently makes old and new analyses incomparable and the only symptom is a cliff in a chart on
    the date of a deployment.
    """
    assert Base.metadata.tables["analyses"].columns[stamp].nullable is False


def test_analyses_are_indexed_for_the_history_cursor() -> None:
    """E6 pages `(user_id, created_at DESC, id DESC)`; the index has to cover that prefix.

    Declared ascending — Postgres scans a b-tree backwards at the same cost — so this asserts the
    columns and their order, which is the part that matters.
    """
    indexes = {
        str(index.name): [column.name for column in index.columns]
        for index in Base.metadata.tables["analyses"].indexes
    }
    assert indexes["ix_analyses_user_id_created_at_id"] == ["user_id", "created_at", "id"]


def test_metrics_are_indexed_for_the_trend_query() -> None:
    """E10's sparkline looks each metric up by `(analysis_id, code)`.

    Carried by the uniqueness constraint rather than a second index, which is why this test reads
    the constraint: adding a separate index for the same prefix would be the speculative kind the
    ticket rules out.
    """
    constraints = {
        tuple(column.name for column in constraint.columns)
        for constraint in Base.metadata.tables["metrics"].constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("analysis_id", "code") in constraints


def test_the_stored_statuses_match_the_api_contract() -> None:
    """The schema and `schemas.py` duplicate these; a test is what keeps the duplication honest.

    They are allowed to diverge — the wire contract and the storage schema are different
    concerns — but a divergence should be a deliberate migration rather than something that
    happens because two files were edited weeks apart.
    """
    assert set(MetricStatusType.enums) == set(get_args(schemas.MetricStatus))
    assert set(KeypointStatusType.enums) == set(get_args(schemas.KeypointStatus))
    assert set(SeverityType.enums) == set(get_args(schemas.Severity))


# --- the behaviour -----------------------------------------------------------------------------


async def test_a_metric_cannot_hold_a_value_it_did_not_measure(session: AsyncSession) -> None:
    """The original engine's central defect, forbidden at the storage layer.

    A row saying "I could not assess this" *and* carrying a number is precisely the shape that
    turned a failed check into "Straight back position". The constraint means no future writer has
    to remember.
    """
    analysis = await _make_analysis(session)
    session.add(
        Metric(
            analysis_id=analysis.id,
            code="trunk_inclination_deg",
            value=32.0,
            unit="deg",
            status="insufficient_keypoints",
            detail="left hip was not detected",
            confidence=None,
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_a_measured_metric_requires_its_value(session: AsyncSession) -> None:
    """The other half of the same constraint: `ok` with a null value is equally incoherent."""
    analysis = await _make_analysis(session)
    session.add(
        Metric(
            analysis_id=analysis.id,
            code="trunk_inclination_deg",
            value=None,
            unit="deg",
            status="ok",
            detail="",
            confidence=0.95,
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_a_metric_is_stored_once_per_analysis(session: AsyncSession) -> None:
    analysis = await _make_analysis(session)
    for _ in range(2):
        session.add(
            Metric(
                analysis_id=analysis.id,
                code="trunk_inclination_deg",
                value=32.0,
                unit="deg",
                status="ok",
                detail="",
                confidence=0.95,
            )
        )
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_a_score_outside_the_scale_is_rejected(session: AsyncSession) -> None:
    with pytest.raises(IntegrityError):
        await _make_analysis(session, overall_score=101.0)


async def test_an_unassessable_photograph_scores_null_rather_than_zero(
    session: AsyncSession,
) -> None:
    """Null, not 0. Zero would be a confident claim about a photograph nothing could be read
    from — the original engine's central defect, in column form."""
    analysis = await _make_analysis(session, overall_score=None, assessed=0, pose_detected=False)
    assert analysis.overall_score is None


async def test_deleting_an_analysis_takes_its_children(session: AsyncSession) -> None:
    """Cascade, so a delete cannot leave keypoints pointing at nothing.

    E6 hard-deletes analyses — when someone deletes a photograph of their own body it should be
    gone — and that is only safe if the children go with it.
    """
    analysis = await _make_analysis(session)
    session.add_all(
        [
            Keypoint(
                analysis_id=analysis.id,
                name="left_shoulder",
                x=0.4,
                y=0.3,
                status="ok",
                visibility=0.9,
                presence=0.95,
            ),
            Metric(
                analysis_id=analysis.id,
                code="trunk_inclination_deg",
                value=32.0,
                unit="deg",
                status="ok",
                detail="",
                confidence=0.95,
            ),
            Finding(
                analysis_id=analysis.id,
                code="trunk_slouch",
                severity="major",
                message="Your torso is leaning 32° forward.",
                metric="trunk_inclination_deg",
                value=32.0,
                confidence=0.95,
            ),
        ]
    )
    await session.flush()

    await session.delete(analysis)
    await session.flush()

    for model in (Keypoint, Metric, Finding):
        remaining = (await session.execute(select(model))).scalars().all()
        assert remaining == [], model.__name__


async def test_deleting_a_user_takes_their_analyses(session: AsyncSession) -> None:
    user = User(email="sam@example.com", password_hash="$argon2id$fake")
    session.add(user)
    await session.flush()
    await _make_analysis(session, user_id=user.id)

    await session.delete(user)
    await session.flush()

    assert (await session.execute(select(Analysis))).scalars().all() == []


async def test_an_email_is_stored_lowercased(session: AsyncSession) -> None:
    """Normalisation happens at the boundary, and the constraint is what proves it happened.

    A functional `lower(email)` index would make the *database* case-insensitive while leaving a
    mixed-case value in the column, so any equality filter written without `lower()` would still
    be wrong. Storing it normalised means the column means what it says.
    """
    session.add(User(email="Sam@Example.com", password_hash="$argon2id$fake"))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_a_session_cannot_claim_more_good_posture_than_it_lasted(
    session: AsyncSession,
) -> None:
    user = User(email="sam@example.com", password_hash="$argon2id$fake")
    session.add(user)
    await session.flush()

    started = datetime(2026, 7, 28, 9, 0, tzinfo=UTC)
    session.add(
        PostureSession(
            user_id=user.id,
            started_at=started,
            ended_at=datetime(2026, 7, 28, 9, 10, tzinfo=UTC),
            duration_seconds=600,
            good_posture_seconds=900,
            verdict_counts={"good": 400, "slouched": 200},
            rules_version="1.0.0",
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_a_session_stores_only_aggregates(session: AsyncSession) -> None:
    """The privacy claim, as a schema property.

    Epic G runs inference in the browser and posts a duration, a time-in-good-posture and a
    histogram. There is no column that could hold a frame, which is what makes "frames never leave
    the browser" structural rather than a promise about code that could change.
    """
    columns = set(Base.metadata.tables["sessions"].columns.keys())
    assert columns == {
        "id",
        "user_id",
        "started_at",
        "ended_at",
        "duration_seconds",
        "good_posture_seconds",
        "verdict_counts",
        "rules_version",
        "created_at",
        "updated_at",
    }


async def test_ids_are_random_rather_than_sequential(session: AsyncSession) -> None:
    """Analysis ids appear in URLs, so a sequential key would leak how much data exists.

    `/analyses/41` implies `/analyses/40` belongs to somebody. Epic E returns 404 rather than 403
    for another user's analysis precisely so existence is not leaked; a guessable key would leak
    it through a different door.
    """
    first = await _make_analysis(session)
    second = await _make_analysis(session)
    assert isinstance(first.id, uuid.UUID)
    assert first.id != second.id


async def test_a_repr_does_not_print_the_row(session: AsyncSession) -> None:
    """These tables hold an email address and a password hash, and reprs end up in tracebacks."""
    user = User(email="sam@example.com", password_hash="$argon2id$super-secret")
    session.add(user)
    await session.flush()

    rendered = repr(user)
    assert "super-secret" not in rendered
    assert "sam@example.com" not in rendered
    assert str(user.id) in rendered
