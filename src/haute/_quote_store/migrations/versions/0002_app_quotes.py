"""The quote store's second schema: the underwriter app's record of each quote it keeps, the
events of who did what, the reference counter, the operation keys, and the oldest schema
whose Haute can still use the database.

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
        "haute_app_quotes",
        sa.Column("quote_id", sa.String(36), sa.ForeignKey("haute_quotes.id"), primary_key=True),
        sa.Column("reference", sa.String(40), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column("search_text", sa.Text, nullable=False),
        sa.Column("projection", sa.String(64), nullable=False),
        sa.Column("assignee", sa.String(320), nullable=False),
        sa.Column("priced", sa.Boolean, nullable=False),
        sa.Column("final_revision", sa.Integer, nullable=True),
        sa.Column("final_by", sa.String(320), nullable=True),
        sa.Column("final_at", sa.DateTime, nullable=True),
        sa.Column("archived_by", sa.String(320), nullable=True),
        sa.Column("archived_at", sa.DateTime, nullable=True),
        sa.Column("note", sa.Text, nullable=False),
        sa.Column("note_by", sa.String(320), nullable=True),
        sa.Column("note_at", sa.DateTime, nullable=True),
        sa.Column(
            "source_quote_id", sa.String(36), sa.ForeignKey("haute_quotes.id"), nullable=True
        ),
        sa.Column("source_revision", sa.Integer, nullable=True),
        sa.Column("relation", sa.String(20), nullable=True),
        sa.Column("last_saved_by", sa.String(320), nullable=False),
        sa.Column("last_saved_at", sa.DateTime, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
    )
    op.create_index("ux_haute_app_quotes_reference", "haute_app_quotes", ["reference"], unique=True)
    op.create_index("ix_haute_app_quotes_saved", "haute_app_quotes", ["last_saved_at", "quote_id"])
    op.create_index(
        "ix_haute_app_quotes_assignee", "haute_app_quotes", ["assignee", "last_saved_at"]
    )
    op.create_index("ix_haute_app_quotes_source", "haute_app_quotes", ["source_quote_id"])
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
