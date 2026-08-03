"""Make `analyses.user_id` NOT NULL — the debt E5 took on and E8 pays.

Revision ID: b4c1f7e29a05
Revises: f7a2c9d81b3e
Create Date: 2026-08-02

`analyses.user_id` was created nullable in OP-50 because analyses became persistable (E5) before
authentication existed to attribute them to anyone. That window is closed: as of OP-56 every
route that writes an analysis depends on `get_current_user_id`, so no code path can produce an
ownerless row any more.

**The rows deleted here are unreachable, not merely unowned.** Every read in
`AnalysisRepository` is scoped by `user_id`, and no `user_id` matches null — so a null-owner row
cannot be listed, fetched, or deleted through the API. Leaving them would leave uploaded
photographs in the database that no user can see and no account deletion can reach, which is the
worse outcome for exactly the kind of data this application stores.

**This migration is one-way for data.** The downgrade restores the column's nullability, which is
all schema can restore; it cannot bring the deleted rows back. That is the intended trade — they
are anonymous development uploads by definition, since production has never run without auth —
but it is the reason to read the DELETE before running this against anything you care about.

The children (`keypoints`, `metrics`, `findings`) all reference `analyses.id` with
`ON DELETE CASCADE`, so the DELETE takes them with it and no explicit child cleanup is needed.

**The stored images are not deleted, and cannot be from here.** Each deleted row named an object
in the storage backend, and a migration holds a database connection, not a storage client — the
delete route destroys rows and object together precisely because only the route can. Those
objects become unreferenced: invisible to the application, and reclaimable only by sweeping the
bucket against the surviving `object_key` values. On any real deployment that set is empty
(production has never run without auth), so this is a note for whoever runs the migration against
a development bucket, not a step in it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b4c1f7e29a05"
down_revision: str | None = "f7a2c9d81b3e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Ordering matters: the ALTER would fail on any surviving null, so the rows go first. Both
    # statements run inside the one transaction Alembic opens, so a failure in the ALTER rolls the
    # DELETE back rather than leaving the table half-migrated.
    op.execute(sa.text("DELETE FROM analyses WHERE user_id IS NULL"))

    op.alter_column(
        "analyses",
        "user_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )


def downgrade() -> None:
    """Reopen the column. The deleted rows do not come back — see the module docstring."""
    op.alter_column(
        "analyses",
        "user_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )
