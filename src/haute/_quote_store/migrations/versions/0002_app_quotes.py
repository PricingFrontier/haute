"""The quote store's second schema: the details of each revision of a quote the underwriter
app keeps, the events of who did what, the reference counter, the operation keys, and the
oldest schema whose Haute can still use the database.

Tables are added and nothing is changed or dropped, so a Haute at the first schema could
read and write what it wrote; it does not know to look for the oldest reader, so this
migration names itself as that.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "haute_quote_details",
        sa.Column("quote_id", sa.String(36), primary_key=True),
        sa.Column("revision", sa.Integer, primary_key=True),
        sa.Column("reference", sa.String(40), nullable=False),
        sa.Column("quote_name", sa.Text, nullable=True),
        sa.Column("insured", sa.Text, nullable=True),
        sa.Column("broker", sa.Text, nullable=True),
        sa.Column("inception_date", sa.Date, nullable=True),
        sa.Column("expiry_date", sa.Date, nullable=True),
        sa.Column("valid_until", sa.Date, nullable=True),
        sa.Column("currency", sa.Text, nullable=True),
        sa.Column("stage", sa.String(20), nullable=False),
        sa.Column("assignee", sa.String(320), nullable=False),
        sa.Column("archived", sa.Boolean, nullable=False),
        sa.ForeignKeyConstraint(
            ["quote_id", "revision"],
            ["haute_quote_revisions.quote_id", "haute_quote_revisions.number"],
            name="fk_haute_quote_details_revision",
        ),
    )
    op.create_index("ix_haute_quote_details_reference", "haute_quote_details", ["reference"])
    op.create_index("ix_haute_quote_details_assignee", "haute_quote_details", ["assignee"])
    op.create_table(
        "haute_quote_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("quote_id", sa.String(36), sa.ForeignKey("haute_quotes.id"), nullable=False),
        sa.Column("at", sa.DateTime, nullable=False),
        sa.Column("actor", sa.String(320), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("revision", sa.Integer, nullable=True),
        sa.Column("detail", sa.Text, nullable=False),
    )
    op.create_index("ix_haute_quote_events_quote", "haute_quote_events", ["quote_id", "id"])
    counter = op.create_table(
        "haute_quote_counter",
        sa.Column("scope", sa.String(40), primary_key=True),
        sa.Column("last_number", sa.Integer, nullable=False),
    )
    op.bulk_insert(counter, [{"scope": "quotes", "last_number": 0}])
    op.create_table(
        "haute_quote_operations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("quote_id", sa.String(36), sa.ForeignKey("haute_quotes.id"), nullable=False),
        sa.Column("number", sa.Integer, nullable=False),
        sa.Column("actor", sa.String(320), nullable=False),
    )
    compat = op.create_table(
        "haute_quote_store_compat",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=False),
        sa.Column("oldest_reader", sa.String(32), nullable=False),
    )
    op.bulk_insert(compat, [{"id": 1, "oldest_reader": "0002"}])


def downgrade() -> None:
    raise NotImplementedError(
        "The quote store migrates forward only: undoing a change is a new migration."
    )
