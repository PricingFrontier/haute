"""The quote store's first schema: forms, quotes and their revisions.

A migration is a snapshot: it creates the tables as they were when it was written, never by
importing the schema module, which moves on.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "haute_quote_forms",
        sa.Column("revision", sa.String(64), primary_key=True),
        sa.Column("document", sa.Text, nullable=False),
    )
    op.create_table(
        "haute_quotes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("owner", sa.String(320), nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("latest", sa.Integer, nullable=False),
    )
    op.create_index("ix_haute_quotes_owner", "haute_quotes", ["owner"])
    op.create_table(
        "haute_quote_revisions",
        sa.Column("quote_id", sa.String(36), sa.ForeignKey("haute_quotes.id"), primary_key=True),
        sa.Column("number", sa.Integer, primary_key=True),
        sa.Column(
            "form_revision",
            sa.String(64),
            sa.ForeignKey("haute_quote_forms.revision"),
            nullable=False,
        ),
        sa.Column("inputs", sa.Text, nullable=False),
        sa.Column("request", sa.Text, nullable=False),
        sa.Column("response", sa.Text, nullable=True),
        sa.Column("release", sa.String(200), nullable=True),
        sa.Column("saved_by", sa.String(320), nullable=False),
        sa.Column("saved_at", sa.DateTime, nullable=False),
    )


def downgrade() -> None:
    raise NotImplementedError(
        "The quote store migrates forward only: undoing a change is a new migration."
    )
