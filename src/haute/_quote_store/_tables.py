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
    Boolean,
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
#: Longest reference, relation, event kind, operation key and migration revision.
REFERENCE_LENGTH = 40
RELATION_LENGTH = 20
EVENT_KIND_LENGTH = 40
OPERATION_LENGTH = 64
SCHEMA_REVISION_LENGTH = 32


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


#: The underwriter app's record of each quote it keeps, beside the quote: the test quotes
#: never have one. The reference is taken from ``QUOTE_COUNTER``; the title, summary and
#: search text are projected from the newest revision; ``version`` moves on every change.
APP_QUOTES = Table(
    "haute_app_quotes",
    METADATA,
    Column("quote_id", String(ID_LENGTH), ForeignKey("haute_quotes.id"), primary_key=True),
    Column("reference", String(REFERENCE_LENGTH), nullable=False),
    Column("title", String(NAME_LENGTH), nullable=False),
    Column("summary", Text, nullable=False),
    Column("search_text", Text, nullable=False),
    Column("projection", String(FORM_REVISION_LENGTH), nullable=False),
    Column("assignee", String(PRINCIPAL_LENGTH), nullable=False),
    Column("priced", Boolean, nullable=False),
    Column("final_revision", Integer, nullable=True),
    Column("final_by", String(PRINCIPAL_LENGTH), nullable=True),
    Column("final_at", UtcDateTime, nullable=True),
    Column("archived_by", String(PRINCIPAL_LENGTH), nullable=True),
    Column("archived_at", UtcDateTime, nullable=True),
    Column("note", Text, nullable=False),
    Column("note_by", String(PRINCIPAL_LENGTH), nullable=True),
    Column("note_at", UtcDateTime, nullable=True),
    Column("source_quote_id", String(ID_LENGTH), ForeignKey("haute_quotes.id"), nullable=True),
    Column("source_revision", Integer, nullable=True),
    Column("relation", String(RELATION_LENGTH), nullable=True),
    Column("last_saved_by", String(PRINCIPAL_LENGTH), nullable=False),
    Column("last_saved_at", UtcDateTime, nullable=False),
    Column("version", Integer, nullable=False),
    Index("ux_haute_app_quotes_reference", "reference", unique=True),
    Index("ix_haute_app_quotes_saved", "last_saved_at", "quote_id"),
    Index("ix_haute_app_quotes_assignee", "assignee", "last_saved_at"),
    Index("ix_haute_app_quotes_source", "source_quote_id"),
)

#: Who did what to an app quote, and when, in the order it was done.
QUOTE_EVENTS = Table(
    "haute_quote_events",
    METADATA,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("quote_id", String(ID_LENGTH), ForeignKey("haute_quotes.id"), nullable=False),
    Column("at", UtcDateTime, nullable=False),
    Column("actor", String(PRINCIPAL_LENGTH), nullable=False),
    Column("kind", String(EVENT_KIND_LENGTH), nullable=False),
    Column("revision", Integer, nullable=True),
    Column("detail", Text, nullable=False),
    Index("ix_haute_quote_events_quote", "quote_id", "id"),
)

#: The last reference number taken, by scope: one row, ``quotes``.
QUOTE_COUNTER = Table(
    "haute_quote_counter",
    METADATA,
    Column("scope", String(EVENT_KIND_LENGTH), primary_key=True),
    Column("last_number", Integer, nullable=False),
)

#: Each save or creation's operation key, with the quote and revision it wrote, so one
#: repeated after its answer was lost writes nothing more.
QUOTE_OPERATIONS = Table(
    "haute_quote_operations",
    METADATA,
    Column("id", String(OPERATION_LENGTH), primary_key=True),
    Column("quote_id", String(ID_LENGTH), ForeignKey("haute_quotes.id"), nullable=False),
    Column("number", Integer, nullable=False),
    Column("actor", String(PRINCIPAL_LENGTH), nullable=False),
)

#: The oldest schema whose Haute can still use the database, one row: a Haute behind the
#: database's schema opens it when its own newest is at least this.
STORE_COMPAT = Table(
    "haute_quote_store_compat",
    METADATA,
    Column("id", Integer, primary_key=True, autoincrement=False),
    Column("oldest_reader", String(SCHEMA_REVISION_LENGTH), nullable=False),
)
