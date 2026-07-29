"""The seven tables.

    users · refresh_tokens · sessions · analyses · keypoints · metrics · findings

Three decisions here are the ones worth defending, and each is written next to the thing it
governs rather than only here.

**`keypoints` and `metrics` are tables, not JSONB blobs.** A blob makes "show me this user's trunk
angle over six months" an application-side loop that deserialises every document to read one
field. A table makes it a plain indexed `SELECT`. The storage cost is real and small; the query
difference is the entire history feature (E10) and every trend view after it. The counter-example
is in this same file — :attr:`PostureSession.verdict_counts` *is* JSON, because a histogram is
read whole and never queried by key. The distinction is queryability, not a blanket preference.

**Every analysis stamps `pose_backend`, `rules_version` and `schema_version`, non-nullably.**
Without them, retuning a threshold silently makes old and new results incomparable, and nobody
notices until a user's history shows a cliff on the date of a deployment. With them the cliff is
explainable, old rows stay interpretable, and E10 can mark the boundary on the chart instead of
drawing a step that looks like the user's posture changed.

**The database stores object keys, never URLs.** Established in D3 and enforced here by column
naming and by a test over the schema. A URL embeds a hostname and often an expiry; persisting one
means moving the object store, or switching from a path-style MinIO to a real S3, invalidates
every row. The storage layer owns URL construction, from a key, at read time.
"""

from __future__ import annotations

import uuid

# Imported at runtime, not under TYPE_CHECKING, and it has to stay that way. SQLAlchemy resolves
# `Mapped[...]` annotations at class-definition time to choose a column type, so a name that only
# exists for the type checker fails with `Could not resolve all types within mapped annotation`.
# `from __future__ import annotations` makes this easy to get wrong, because every other module
# here can safely defer its imports.
from datetime import datetime
from typing import Final

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from openposture_api.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

__all__ = [
    "Analysis",
    "Finding",
    "Keypoint",
    "Metric",
    "PostureSession",
    "RefreshToken",
    "User",
]


# Rendered as VARCHAR + CHECK rather than a native Postgres ENUM (`native_enum=False`). A native
# enum needs `ALTER TYPE` to gain a value, which Alembic does not autogenerate and which cannot
# run inside a transaction on older Postgres; a CHECK constraint is an ordinary migration. It also
# means these types work unchanged on SQLite, which is what lets the model tests run in-process.
#
# The members mirror the `Literal` aliases in `schemas.py`. They are duplicated rather than
# derived because the API contract and the storage schema are allowed to diverge — but a
# divergence should be a deliberate migration, so a test asserts they currently agree.
_METRIC_STATUSES: Final = ("ok", "insufficient_keypoints", "low_confidence")
_KEYPOINT_STATUSES: Final = ("ok", "low_confidence", "not_detected", "out_of_frame")
_SEVERITIES: Final = ("major", "minor", "info")

MetricStatusType: Final = Enum(
    *_METRIC_STATUSES, name="metric_status", native_enum=False, create_constraint=True
)
KeypointStatusType: Final = Enum(
    *_KEYPOINT_STATUSES, name="keypoint_status", native_enum=False, create_constraint=True
)
SeverityType: Final = Enum(
    *_SEVERITIES, name="finding_severity", native_enum=False, create_constraint=True
)


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A person with an account.

    Only the two fields authentication needs. Profile data is not speculatively modelled here —
    the application has never asked a user for a name, and a nullable column nothing writes is a
    migration to remove later rather than a feature now.
    """

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), nullable=False)

    password_hash: Mapped[str] = mapped_column(
        # argon2id encoded hashes are around 100 characters; `Text` avoids a migration if the
        # parameters are ever retuned upwards. There is no length worth enforcing here — the
        # value is produced by the hasher, never by a user.
        Text(),
        nullable=False,
        # Excluded from `repr` at the base class, which never prints columns. Noted here because
        # this is the field that makes that rule load-bearing rather than tidy.
    )

    __table_args__ = (
        # Case-insensitive uniqueness, done by storing the address already lowercased and
        # indexing it plainly, rather than with a functional `lower(email)` index or `citext`.
        #
        # The reason is that normalisation has to happen in the application regardless: a user
        # who registers as `Sam@Example.com` and logs in as `sam@example.com` must match, and a
        # functional index makes the *database* case-insensitive while leaving the value stored
        # in mixed case — so anything reading the column back, including an equality filter
        # written without `lower()`, is still case-sensitive and still wrong. Normalise once, at
        # the boundary, and the column means exactly what it says.
        UniqueConstraint("email", name="uq_users_email"),
        CheckConstraint("email = lower(email)", name="email_is_lowercased"),
    )

    analyses: Mapped[list[Analysis]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class RefreshToken(UUIDPrimaryKeyMixin, Base):
    """One issued refresh token, stored hashed, in a rotation family.

    **The token itself is never stored.** `token_hash` holds a digest, for the same reason
    `users.password_hash` does: a database disclosure should not be a session-hijacking kit. E7
    owns the hashing; this table only guarantees there is nowhere to put the plaintext.

    **`family_id` is what makes replay detectable.** Every refresh rotates the token and links the
    new row to the same family. If a token that has already been rotated is presented again, the
    only explanations are a stolen copy or a client bug, and both warrant revoking every token in
    the family rather than the one row — otherwise the attacker keeps the branch they created.

    No `TimestampMixin`: `issued_at` is the creation time under a name that says what it means,
    and there is no update path that would give `updated_at` a meaning. Rotation writes a new row.
    """

    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    token_hash: Mapped[str] = mapped_column(Text(), nullable=False)

    family_id: Mapped[uuid.UUID] = mapped_column(nullable=False, default=uuid.uuid4)

    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Set when rotated, logged out, or revoked as part of a compromised family.",
    )

    __table_args__ = (
        # Every refresh presents a token and looks it up by digest, so this is the hot path and
        # it must be unique: two rows with the same digest would make revocation ambiguous.
        UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
        # Revoking a family is `UPDATE ... WHERE family_id = ?`, which is the whole point of the
        # column. Without this index that statement scans the table on every replay detection.
        Index("ix_refresh_tokens_family_id", "family_id"),
        # "Log out everywhere" and cleanup of a deleted user's tokens both filter by user.
        Index("ix_refresh_tokens_user_id", "user_id"),
    )


class PostureSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A live-mode session, stored as aggregates only.

    Mapped to the `sessions` table but named `PostureSession` in Python, because `Session` in a
    file that also imports SQLAlchemy's `Session` is a name collision waiting to be resolved
    incorrectly by whoever edits it next.

    **Frames never reach this table, and there is no column that could hold one.** Epic G runs
    inference in the browser and posts only what is aggregated at session end — duration, time
    spent in good posture, and a histogram of verdicts. That is the privacy claim the README
    makes, and a schema with nowhere to put a frame is what makes it structural rather than a
    promise about code that could change.

    `verdict_counts` is JSON, in a file whose header argues against JSON blobs. The distinction:
    keypoints and metrics are *queried by key* across rows, so they are tables; a verdict
    histogram is read whole, with the row, and never filtered or aggregated on in SQL. Modelling
    it as a table would buy a query nobody issues and cost a join everybody pays.
    """

    __tablename__ = "sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    duration_seconds: Mapped[int] = mapped_column(Integer(), nullable=False)

    good_posture_seconds: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
        comment="Seconds within the session whose smoothed verdict was good posture.",
    )

    verdict_counts: Mapped[dict[str, int]] = mapped_column(
        JSON(),
        nullable=False,
        default=dict,
        comment="Histogram of smoothed per-frame verdicts. Read whole; never queried by key.",
    )

    rules_version: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="Which rules the browser engine applied. Same reasoning as on `analyses`.",
    )

    __table_args__ = (
        CheckConstraint("ended_at >= started_at", name="ends_after_it_starts"),
        CheckConstraint("duration_seconds >= 0", name="duration_is_not_negative"),
        CheckConstraint(
            "good_posture_seconds >= 0 AND good_posture_seconds <= duration_seconds",
            name="good_posture_fits_in_the_session",
        ),
        # Listing a user's sessions newest-first, the same access pattern as `analyses`.
        Index("ix_sessions_user_id_started_at", "user_id", "started_at"),
    )


class Analysis(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One assessment of one uploaded photograph.

    The parent of `keypoints`, `metrics` and `findings`, and the row E6 paginates and E10 plots.
    """

    __tablename__ = "analyses"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        # Nullable, and only until E7. Analyses become persistable in E5, which lands before
        # authentication exists in E7, so there is a window where an upload has no owner. Making
        # the column non-nullable now would mean E5 could not write a row at all.
        #
        # E7 tightens this: a migration deletes any ownerless rows — they are anonymous
        # development uploads, not user data — and sets `NOT NULL`. Left permanently nullable it
        # would be a hole in the authorization story, because "scope every query by user_id"
        # cannot scope a row whose user_id is null.
        nullable=True,
    )

    object_key: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        comment=(
            "Storage key for the uploaded image. A key, never a URL — the storage layer builds "
            "URLs at read time so moving the object store does not invalidate every row."
        ),
    )

    pose_detected: Mapped[bool] = mapped_column(nullable=False)

    image_width: Mapped[int] = mapped_column(Integer(), nullable=False)
    image_height: Mapped[int] = mapped_column(Integer(), nullable=False)

    overall_score: Mapped[float | None] = mapped_column(
        Float(),
        # Null when nothing could be measured, and specifically not 0. Both 0 and 100 would be
        # confident claims about a photograph the engine could not assess — the original engine's
        # central defect, in column form.
        nullable=True,
    )

    assessed: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
        comment="How many metrics produced a value. Coverage, stored beside the score.",
    )
    total: Mapped[int] = mapped_column(Integer(), nullable=False)

    inference_ms: Mapped[float] = mapped_column(Float(), nullable=False)

    # --- the version stamps, all three non-nullable -------------------------------------------
    #
    # Non-nullable is the requirement, not a preference. A nullable stamp is a row that cannot be
    # interpreted, and the one thing this trio exists to prevent is an uninterpretable row.
    pose_backend: Mapped[str] = mapped_column(String(32), nullable=False)
    rules_version: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)

    user: Mapped[User | None] = relationship(back_populates="analyses")

    keypoints: Mapped[list[Keypoint]] = relationship(
        back_populates="analysis",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    metrics: Mapped[list[Metric]] = relationship(
        back_populates="analysis",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    findings: Mapped[list[Finding]] = relationship(
        back_populates="analysis",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        CheckConstraint("assessed >= 0 AND assessed <= total", name="assessed_fits_in_total"),
        CheckConstraint("image_width > 0 AND image_height > 0", name="image_has_area"),
        CheckConstraint(
            "overall_score IS NULL OR (overall_score >= 0 AND overall_score <= 100)",
            name="score_is_a_percentage",
        ),
        # **The index E6 pages on.** Cursor pagination orders by `(created_at DESC, id DESC)`
        # within one user — `id` as the tie-break, because two analyses uploaded in the same
        # millisecond would otherwise have an unstable order and a cursor over an unstable order
        # skips and duplicates rows, which is the failure cursor pagination was chosen to avoid.
        #
        # Declared ascending even though the query orders descending. Postgres scans a b-tree
        # backwards at the same cost, so the index serves both directions, and an ascending
        # declaration keeps this expressible as plain column names — `created_at` and `id` come
        # from mixins and are not in this class's namespace, so a `.desc()` here would need
        # `text()` fragments that Alembic's autogenerate then reports as perpetual drift.
        Index("ix_analyses_user_id_created_at_id", "user_id", "created_at", "id"),
    )


class Keypoint(UUIDPrimaryKeyMixin, Base):
    """One landmark, as it was measured for one analysis.

    Coordinates are normalised fractions of the image, not pixels — the same choice
    `DetectedLandmark` makes in the API contract, for the same reason: a pixel coordinate is
    wrong the moment the image is displayed at a different size.

    World coordinates are deliberately absent. They are metres from the hip, they are what the
    metrics were computed *from*, and storing them would invite recomputing a metric from stored
    landmarks at a rules version that no longer matches the stamp on the analysis.
    """

    __tablename__ = "keypoints"

    analysis_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("analyses.id", ondelete="CASCADE"),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(String(64), nullable=False)

    x: Mapped[float] = mapped_column(Float(), nullable=False)
    y: Mapped[float] = mapped_column(Float(), nullable=False)

    status: Mapped[str] = mapped_column(KeypointStatusType, nullable=False)

    visibility: Mapped[float] = mapped_column(Float(), nullable=False)
    presence: Mapped[float] = mapped_column(Float(), nullable=False)

    analysis: Mapped[Analysis] = relationship(back_populates="keypoints")

    __table_args__ = (
        # One row per landmark per analysis. This is both a correctness constraint and the index
        # every read uses: fetching an analysis loads its keypoints by `analysis_id`, and the
        # unique index serves that prefix. No separate index on `analysis_id` is needed, and
        # adding one would be the speculative kind this ticket rules out.
        UniqueConstraint("analysis_id", "name", name="uq_keypoints_analysis_id_name"),
        CheckConstraint(
            "visibility >= 0 AND visibility <= 1 AND presence >= 0 AND presence <= 1",
            name="confidences_are_fractions",
        ),
    )


class Metric(UUIDPrimaryKeyMixin, Base):
    """One measurement, or an honest absence of one.

    `value` is nullable and `status` says why. A row with `status != 'ok'` and a non-null value
    would be the original engine's defect reintroduced at the storage layer, so a check constraint
    forbids it rather than trusting every future writer to remember.
    """

    __tablename__ = "metrics"

    analysis_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("analyses.id", ondelete="CASCADE"),
        nullable=False,
    )

    code: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="Stable metric identifier, e.g. `trunk_inclination_deg`. Filter on this.",
    )

    value: Mapped[float | None] = mapped_column(Float(), nullable=True)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(MetricStatusType, nullable=False)
    detail: Mapped[str] = mapped_column(Text(), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float(), nullable=True)

    analysis: Mapped[Analysis] = relationship(back_populates="metrics")

    __table_args__ = (
        # **The index E10's trend query uses**, and the reason this is a table rather than a blob.
        # The sparkline is
        #
        #   SELECT a.created_at, a.rules_version, m.value, m.status
        #   FROM analyses a JOIN metrics m ON m.analysis_id = a.id
        #   WHERE a.user_id = :user AND m.code = 'trunk_inclination_deg'
        #   ORDER BY a.created_at DESC
        #
        # which drives from `ix_analyses_user_id_created_at_id` and looks each metric up on
        # exactly this `(analysis_id, code)` prefix. Two indexes, one query, no deserialisation.
        UniqueConstraint("analysis_id", "code", name="uq_metrics_analysis_id_code"),
        CheckConstraint(
            "(status = 'ok' AND value IS NOT NULL) OR (status <> 'ok' AND value IS NULL)",
            name="a_value_exists_exactly_when_the_status_is_ok",
        ),
    )


class Finding(UUIDPrimaryKeyMixin, Base):
    """Something the engine noticed, already worded for a person.

    Denormalised on purpose: `metric` and `value` repeat what the corresponding `metrics` row
    holds. A finding is a statement made at a point in time about a specific measured value, and
    it should stay readable exactly as it was issued. Joining to reconstruct it would make the
    displayed sentence depend on a row that a later correction could change underneath it.
    """

    __tablename__ = "findings"

    analysis_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("analyses.id", ondelete="CASCADE"),
        nullable=False,
    )

    code: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="Stable identifier, e.g. `trunk_slouch`. Clients branch on this, not on message.",
    )

    severity: Mapped[str] = mapped_column(SeverityType, nullable=False)
    message: Mapped[str] = mapped_column(Text(), nullable=False)

    metric: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[float] = mapped_column(Float(), nullable=False)
    confidence: Mapped[float] = mapped_column(Float(), nullable=False)

    analysis: Mapped[Analysis] = relationship(back_populates="findings")

    __table_args__ = (
        # Findings are always read for one analysis, never filtered by code across analyses. Not
        # unique: an analysis can legitimately raise the same code twice — left and right
        # shoulder, say — so uniqueness here would be a constraint on the rules engine's output
        # rather than on the data.
        Index("ix_findings_analysis_id", "analysis_id"),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="confidence_is_a_fraction",
        ),
    )
