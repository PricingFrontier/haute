"""The quote store's schema as SQLAlchemy Core tables (specs/workbench).

The schema is Haute's, the same on every database the store runs on. Each table's name
starts ``haute_`` and Alembic's version table is the store's own, so the store can share a
database the project's IT team provides. A migration under ``migrations/versions`` creates
what is here: when one changes, the other does.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Column,
    DateTime,
    Dialect,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)
from sqlalchemy.types import TypeDecorator

#: Alembic's version table for the store, apart from any other Alembic user's.
VERSION_TABLE = "haute_quote_store_version"

#: Longest id, name, principal and release the store holds, so every engine can index them.
ID_LENGTH = 36
NAME_LENGTH = 200
PRINCIPAL_LENGTH = 320
RELEASE_LENGTH = 200
#: A form's revision: the content hash ``haute._workbench_form.form_revision`` gives.
FORM_REVISION_LENGTH = 64


class UtcDateTime(TypeDecorator[datetime]):
    """A moment in UTC, held without its zone and read back with it, on every engine."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("The quote store holds moments with their time zone.")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        return None if value is None else value.replace(tzinfo=UTC)


METADATA = MetaData()

#: Each form a quote was keyed into, once, under its revision.
QUOTE_FORMS = Table(
    "haute_quote_forms",
    METADATA,
    Column("revision", String(FORM_REVISION_LENGTH), primary_key=True),
    Column("document", Text, nullable=False),
)

#: A quote: who owns it, and the number of its newest revision.
QUOTES = Table(
    "haute_quotes",
    METADATA,
    Column("id", String(ID_LENGTH), primary_key=True),
    Column("name", String(NAME_LENGTH), nullable=False),
    Column("owner", String(PRINCIPAL_LENGTH), nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    Column("latest", Integer, nullable=False),
    Index("ix_haute_quotes_owner", "owner"),
)

#: A quote's revisions, each written whole: the values, the request, the response and the
#: form it was keyed into.
QUOTE_REVISIONS = Table(
    "haute_quote_revisions",
    METADATA,
    Column("quote_id", String(ID_LENGTH), ForeignKey("haute_quotes.id"), primary_key=True),
    Column("number", Integer, primary_key=True),
    Column(
        "form_revision",
        String(FORM_REVISION_LENGTH),
        ForeignKey("haute_quote_forms.revision"),
        nullable=False,
    ),
    Column("inputs", Text, nullable=False),
    Column("request", Text, nullable=False),
    Column("response", Text, nullable=True),
    Column("release", String(RELEASE_LENGTH), nullable=True),
    Column("saved_by", String(PRINCIPAL_LENGTH), nullable=False),
    Column("saved_at", UtcDateTime, nullable=False),
)
