"""The quote store's fourth schema: a quote's option, its reference and option held on the
quote itself, and the details rows' reference named as the sheets name it.

An app quote takes the reference of its newest details row and option 1, and its details
rows option 1, their columns ``quote_reference`` and ``quote_option`` as ``quote_details``
names them now; a quote the store saved alone holds neither. The pair is unique among app
quotes, and the last option taken under each reference is counted, so an alternative takes
the next. A Haute at the third schema could not read the forms this one saves with each
quote: their ``quote_details`` holds ``option`` and their other tables lead with the quote's
reference and option, which it refuses. So this migration names itself as the oldest
reader, and a release rolled back past it refuses the database, naming the upgrade.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("haute_quotes", sa.Column("reference", sa.String(40), nullable=True))
    op.add_column("haute_quotes", sa.Column("option", sa.Integer, nullable=True))
    # The details rows' columns as quote_details names them; every row written before is
    # option 1. SQLite renames a column by copying the table, so its index is made again.
    op.drop_index("ix_haute_quote_details_reference", table_name="haute_quote_details")
    with op.batch_alter_table("haute_quote_details") as batch:
        batch.alter_column(
            "reference",
            new_column_name="quote_reference",
            existing_type=sa.String(40),
            existing_nullable=False,
        )
        batch.add_column(sa.Column("quote_option", sa.Integer, nullable=False, server_default="1"))
    op.create_index(
        "ix_haute_quote_details_quote_reference", "haute_quote_details", ["quote_reference"]
    )
    quotes = sa.table(
        "haute_quotes",
        sa.column("id", sa.String),
        sa.column("latest", sa.Integer),
        sa.column("reference", sa.String),
        sa.column("option", sa.Integer),
    )
    details = sa.table(
        "haute_quote_details",
        sa.column("quote_id", sa.String),
        sa.column("revision", sa.Integer),
        sa.column("quote_reference", sa.String),
    )
    newest = (
        sa.select(details.c.quote_reference)
        .where(details.c.quote_id == quotes.c.id, details.c.revision == quotes.c.latest)
        .scalar_subquery()
    )
    op.execute(quotes.update().values(reference=newest))
    op.execute(quotes.update().where(quotes.c.reference.is_not(None)).values(option=1))
    op.create_index(
        "ux_haute_quotes_identity", "haute_quotes", ["reference", "option"], unique=True
    )
    options = op.create_table(
        "haute_quote_options",
        sa.Column("reference", sa.String(40), primary_key=True),
        sa.Column("last_option", sa.Integer, nullable=False),
    )
    op.execute(
        options.insert().from_select(
            ["reference", "last_option"],
            sa.select(quotes.c.reference, sa.literal(1)).where(quotes.c.reference.is_not(None)),
        )
    )
    compat = sa.table(
        "haute_quote_store_compat",
        sa.column("id", sa.Integer),
        sa.column("oldest_reader", sa.String),
    )
    op.execute(compat.update().where(compat.c.id == 1).values(oldest_reader="0004"))


def downgrade() -> None:
    raise NotImplementedError(
        "The quote store migrates forward only: undoing a change is a new migration."
    )
