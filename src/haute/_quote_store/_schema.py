"""The quote store's tables, derived from the form (specs/workbench).

Each table of the form's schema is a SQL table of the same name: ``quote_details``, every
input table and every output table, its columns the schema's under their names and in their
types. Every column of the project's own is nullable, so a draft saves with any of them
empty. Each row holds the quote's identity, ``quote_reference`` and ``quote_option``, and a
row of a many-row table holds ``quote_row`` too, the store's number for it. The identity,
with ``quote_row`` in a many-row table, is each table's primary key, and each table's
identity refers to its quote's ``quote_details`` row. Beside the form's tables the store
keeps ``haute_activity``, a row for each thing done to a quote; a database records nothing of
its tables' shape. ``quote_details`` holds the store's own columns besides the form's
(:data:`STORE_COLUMNS`), which no request holds.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Dialect,
    Double,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects import mssql
from sqlalchemy.types import TypeDecorator, TypeEngine

from haute._workbench_form import (
    IDENTITY_IDS,
    QUOTE_DETAILS_ID,
    ColumnType,
    FormSpec,
    SchemaTable,
)
from haute._workbench_tables import QUOTE_ROW

#: Haute's own table: what was done to each quote.
ACTIVITY_TABLE = "haute_activity"

#: The longest reference a quote keys, its option's place, a principal, a release, a stage,
#: a relation and an operation key.
REFERENCE_LENGTH = 40
PRINCIPAL_LENGTH = 320
RELEASE_LENGTH = 200
STAGE_LENGTH = 20
RELATION_LENGTH = 20
ACTION_LENGTH = 20
OPERATION_LENGTH = 64
#: A quote's name is at most this long, wherever it is saved.
NAME_LENGTH = 200
#: The collation SQL Server compares the identity and Haute's other exact texts under: by
#: code point, so ``ABC-1`` and ``abc-1`` are two quotes whatever the database's default.
EXACT_COLLATION = "Latin1_General_100_BIN2"


class UtcDateTime(TypeDecorator[datetime]):
    """A moment in UTC, held without its zone and read back with it, on every engine: to the
    microsecond on SQL Server, as ``DATETIME2(6)``."""

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if dialect.name == "mssql":
            return dialect.type_descriptor(mssql.DATETIME2(precision=6))
        return dialect.type_descriptor(DateTime())

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("The quote store holds moments with their time zone.")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        return None if value is None else value.replace(tzinfo=UTC)


def _text() -> TypeEngine[Any]:
    """Text of any length: ``NVARCHAR(max)`` on SQL Server, whose ``TEXT`` is gone."""
    return Text().with_variant(mssql.NVARCHAR(None), "mssql")


def _exact(length: int) -> TypeEngine[Any]:
    """Text of at most *length* characters that the store compares exactly: on SQL Server
    ``NVARCHAR`` of twice as many UTF-16 units, so any *length* characters fit, under
    :data:`EXACT_COLLATION`."""
    return String(length).with_variant(
        mssql.NVARCHAR(2 * length, collation=EXACT_COLLATION), "mssql"
    )


def _double() -> TypeEngine[Any]:
    return Double().with_variant(mssql.FLOAT(53), "mssql")


def _date() -> TypeEngine[Any]:
    """A day: ``DATE`` on SQL Server too, which a dialect not yet connected renders as
    ``DATETIME``."""
    return Date().with_variant(mssql.DATE(), "mssql")


#: Each column type's SQL type in the store, pinned on each engine so the SQL a plan prints
#: is the SQL the connected engine runs.
_TYPES: dict[ColumnType, Callable[[], TypeEngine[Any]]] = {
    "int": BigInteger,
    "float": _double,
    "str": _text,
    "bool": Boolean,
    "date": _date,
}


def column_type(kind: ColumnType | str) -> TypeEngine[Any]:
    """The store's SQL type for a column of the form's type *kind*."""
    return _TYPES[kind]()  # type: ignore[index]


#: quote_details' columns the app sets that the store keys or indexes, bounded so every
#: engine can: the stage, who it is with and who made and changed it.
_BOUNDED_DETAILS: dict[str, Callable[[], TypeEngine[Any]]] = {
    "stage": lambda: _exact(STAGE_LENGTH),
    "assignee": lambda: _exact(PRINCIPAL_LENGTH),
    "created_by": lambda: _exact(PRINCIPAL_LENGTH),
    "modified_by": lambda: _exact(PRINCIPAL_LENGTH),
}


#: quote_details' columns the app sets on every write, never null.
_SET_DETAILS = frozenset(
    {"stage", "assignee", "archived", "created_date", "created_by", "modified_date", "modified_by"}
)


def _reference() -> TypeEngine[Any]:
    return _exact(REFERENCE_LENGTH)


#: The store's own columns of quote_details, in order: each one's name, type and whether it
#: may be null. None of them is the form's, and no request holds them.
STORE_COLUMNS: tuple[tuple[str, Callable[[], TypeEngine[Any]], bool], ...] = (
    ("quote_version", BigInteger, False),
    ("saved_at", UtcDateTime, False),
    ("saved_by", lambda: _exact(PRINCIPAL_LENGTH), False),
    ("priced_release", lambda: _exact(RELEASE_LENGTH), True),
    ("source_reference", _reference, True),
    ("source_option", BigInteger, True),
    ("source_relation", lambda: _exact(RELATION_LENGTH), True),
    ("handover_note", _text, True),
    ("note_by", lambda: _exact(PRINCIPAL_LENGTH), True),
    ("note_at", UtcDateTime, True),
)
STORE_COLUMN_NAMES: frozenset[str] = frozenset(name for name, _, _ in STORE_COLUMNS)

#: What a column the store keeps holds, as the schema editor shows it: a whole number, text
#: or a moment.
StoreColumnKind = Literal["int", "str", "datetime"]

_IDENTITY = ("quote_reference", "quote_option")


def _named(kind: str, table_id: str) -> str:
    """A constraint's or index's name made from its table's stable id, so a rename of the
    table leaves it findable."""
    return f"haute_{kind}_{hashlib.sha1(table_id.encode('utf-8')).hexdigest()[:12]}"


@dataclass(frozen=True, slots=True)
class StoredTable:
    """A table of the form's schema as the store holds it."""

    form: SchemaTable
    table: Table

    @property
    def many(self) -> bool:
        return self.form.rows == "many"


@dataclass(frozen=True, slots=True)
class StoreSchema:
    """The store's tables for one form: the form's tables in the schema's order, each keyed
    by its stable id, and Haute's activity."""

    metadata: MetaData
    tables: tuple[StoredTable, ...]
    activity: Table

    @property
    def details(self) -> StoredTable:
        return self.by_id(QUOTE_DETAILS_ID)

    def by_id(self, table_id: str) -> StoredTable:
        for stored in self.tables:
            if stored.form.id == table_id:
                return stored
        raise KeyError(table_id)

    def of_role(self, role: str) -> tuple[StoredTable, ...]:
        return tuple(stored for stored in self.tables if stored.form.role == role)


def structure(form: FormSpec) -> dict[str, Any]:
    """What of *form* decides the store's tables, by stable id: each table's name, role and
    rows, and each column's name, type and whether it is the index, as the SQL Haute writes
    plans from it. Labels, rules and the sheets decide nothing there."""
    return {
        "tables": [
            {
                "id": table.id,
                "name": table.name,
                "role": table.role,
                "rows": table.rows,
                "columns": [
                    {
                        "id": column.id,
                        "name": column.name,
                        "type": "int" if column.index else column.type,
                        "index": column.index,
                    }
                    for column in table.columns
                ],
            }
            for table in form.data_schema.tables
        ],
    }


def _kind(sql_type: TypeEngine[Any]) -> StoreColumnKind:
    if isinstance(sql_type, UtcDateTime):
        return "datetime"
    return "int" if isinstance(sql_type, Integer) else "str"


def store_columns(form: FormSpec) -> dict[str, list[tuple[str, StoreColumnKind]]]:
    """The columns the store keeps in each of *form*'s tables that the form does not, by
    table id, each by its name and what it holds: ``quote_details``' own
    (:data:`STORE_COLUMNS`), and a many-row table's ``quote_row``."""
    kept: dict[str, list[tuple[str, StoreColumnKind]]] = {}
    for table in form.data_schema.tables:
        if table.id == QUOTE_DETAILS_ID:
            kept[table.id] = [(name, _kind(made())) for name, made, _ in STORE_COLUMNS]
        elif table.rows == "many":
            kept[table.id] = [(QUOTE_ROW, "int")]
    return kept


def store_schema(form: FormSpec, *, sql_schema: str | None = None) -> StoreSchema:
    """The store's tables for *form*: its tables, then ``haute_activity``, each in the SQL
    schema *sql_schema*, or the connection's default when it names none."""
    metadata = MetaData(schema=sql_schema)
    tables = tuple(_stored(metadata, table) for table in form.data_schema.tables)
    return StoreSchema(metadata=metadata, tables=tables, activity=_activity_table(metadata))


def _stored(metadata: MetaData, table: SchemaTable) -> StoredTable:
    details = table.id == QUOTE_DETAILS_ID
    columns: list[Column[Any]] = []
    for column in table.columns:
        identity = column.id in IDENTITY_IDS or (details and column.name in _IDENTITY)
        if column.name == "quote_reference" and identity:
            sql_type: TypeEngine[Any] = _reference()
        elif details and column.name in _BOUNDED_DETAILS:
            sql_type = _BOUNDED_DETAILS[column.name]()
        else:
            sql_type = _TYPES["int" if column.index else column.type]()
        required = identity or (details and column.name in _SET_DETAILS)
        columns.append(Column(column.name, sql_type, nullable=not required))
    keys = list(_IDENTITY)
    if details:
        columns.extend(
            Column(name, kind(), nullable=nullable) for name, kind, nullable in STORE_COLUMNS
        )
    elif table.rows == "many":
        columns.append(Column(QUOTE_ROW, BigInteger, nullable=False))
        keys.append(QUOTE_ROW)
    constraints: list[Any] = [PrimaryKeyConstraint(*keys, name=_named("pk", table.id))]
    if details:
        constraints.extend(
            [
                Index(_named("ix_list", table.id), "archived", "assignee", "saved_at"),
                Index(_named("ix_source", table.id), "source_reference", "source_option"),
            ]
        )
    else:
        constraints.append(
            ForeignKeyConstraint(
                list(_IDENTITY),
                [f"{QUOTE_DETAILS_ID}.{name}" for name in _IDENTITY],
                name=_named("fk", table.id),
            )
        )
    return StoredTable(form=table, table=Table(table.name, metadata, *columns, *constraints))


def _activity_table(metadata: MetaData) -> Table:
    """Who did what to a quote and when, a row each, never changed or removed."""
    return Table(
        ACTIVITY_TABLE,
        metadata,
        # An identity column, which takes a value with no grant on a sequence of its own.
        Column("activity_id", Integer, Identity(), primary_key=True),
        Column("operation_key", _exact(OPERATION_LENGTH), nullable=False),
        Column("quote_reference", _reference(), nullable=False),
        Column("quote_option", BigInteger, nullable=False),
        # The quote's version the write left; a note moves none.
        Column("quote_version", BigInteger, nullable=True),
        Column("occurred_at", UtcDateTime, nullable=False),
        Column("actor", _exact(PRINCIPAL_LENGTH), nullable=False),
        Column("action", _exact(ACTION_LENGTH), nullable=False),
        Column("assignee", _exact(PRINCIPAL_LENGTH), nullable=True),
        Column("note", _text(), nullable=True),
        Column("reason", _text(), nullable=True),
        Column("priced_release", _exact(RELEASE_LENGTH), nullable=True),
        UniqueConstraint("operation_key", name="haute_uq_activity_operation"),
        ForeignKeyConstraint(
            list(_IDENTITY),
            [f"{QUOTE_DETAILS_ID}.{name}" for name in _IDENTITY],
            name="haute_fk_activity_quote",
        ),
        Index("haute_ix_activity_quote", "quote_reference", "quote_option", "activity_id"),
    )
