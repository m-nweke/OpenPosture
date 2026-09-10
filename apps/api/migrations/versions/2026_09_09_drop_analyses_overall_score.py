"""Drop `analyses.overall_score`.

Revision ID: bca732e744f4
Revises: b4c1f7e29a05
Create Date: 2026-09-09

The score was a fixed per-finding penalty weighted by severity, with no evidence behind the
weights and no accounting for correlated findings or measurement coverage. Findings and quality
(what the engine could and could not assess) remain the signal; nothing replaces this column.

Reversible: the downgrade re-adds the column, nullable, with the same range check it had.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "bca732e744f4"
down_revision: str | None = "b4c1f7e29a05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Use raw SQL with IF EXISTS. The initial migration (f7a2c9d81b3e) delegates to
    # `Base.metadata.create_all` against the *current* model, which already omits
    # `overall_score` on this branch — so on a fresh database neither the column nor its
    # check constraint is ever created, and a bare drop would fail.
    op.execute(
        "ALTER TABLE analyses DROP CONSTRAINT IF EXISTS ck_analyses_score_is_a_percentage"
    )
    op.execute("ALTER TABLE analyses DROP COLUMN IF EXISTS overall_score")


def downgrade() -> None:
    op.add_column("analyses", sa.Column("overall_score", sa.Float(), nullable=True))
    # Unprefixed name: `op.create_check_constraint` runs it through the same naming convention as
    # the model (`db/base.py`), which is what resolves it to `ck_analyses_score_is_a_percentage`.
    op.create_check_constraint(
        "score_is_a_percentage",
        "analyses",
        "overall_score IS NULL OR (overall_score >= 0 AND overall_score <= 100)",
    )
