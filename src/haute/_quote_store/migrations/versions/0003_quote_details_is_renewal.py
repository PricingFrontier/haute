"""The quote store's third schema: whether a quote is a renewal, a column of its
``quote_details`` rows.

The column is nullable, so a row written before it reads as unticked. A Haute at the
second schema could still read and write the rows, but not the forms this one saves with
each quote: their ``quote_details`` holds ``is_renewal``, which it refuses. So this
migration names itself as the oldest reader, and a release rolled back past it refuses the
database, naming the upgrade, rather than failing on the quotes saved since.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("haute_quote_details", sa.Column("is_renewal", sa.Boolean, nullable=True))
    compat = sa.table(
        "haute_quote_store_compat",
        sa.column("id", sa.Integer),
        sa.column("oldest_reader", sa.String),
    )
    op.execute(compat.update().where(compat.c.id == 1).values(oldest_reader="0003"))


def downgrade() -> None:
    raise NotImplementedError(
        "The quote store migrates forward only: undoing a change is a new migration."
    )
