"""The quote store's tables, derived from the form (specs/workbench).

Each table of the form's schema is a SQL table of the same name: ``quote_details``, every
input table and every output table, its columns the schema's under their names and in their
types. Every column of the project's own is nullable, so a draft saves with any of them
empty. Each row holds the quote's identity, ``quote_reference`` and ``quote_option``, and a
row of a many-row table holds ``table_index`` too, the store's number for it. The identity,
with ``table_index`` in a many-row table, is each table's primary key, and each table's
identity refers to its quote's ``quote_details`` row. Beside the form's tables the store
keeps ``haute_activity``, a row for each thing done to a quote; a database records nothing of
its tables' shape. ``quote_details`` holds the store's own columns besides the form's
(:data:`STORE_COLUMNS`), which no request holds.
"""

from __future__ import annotations

import dataclasses
import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
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
    IDENTITY_NAMES,
    QUOTE_DETAILS_NAME,
    ColumnType,
    FormSpec,
    SchemaTable,
)
from haute._workbench_tables import TABLE_INDEX

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


def _named(kind: str, table_name: str) -> str:
    """A constraint's or index's name made from its table's name when Haute creates the
    table. Nothing looks one up by its name afterwards, so a store whose names were made
    otherwise, from the ids an earlier Haute gave its tables, opens and saves as it did."""
    return f"haute_{kind}_{hashlib.sha1(table_name.encode('utf-8')).hexdigest()[:12]}"


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
    """The store's tables for one form: the form's tables in the schema's order, each known
    by its name, and Haute's activity."""

    metadata: MetaData
    tables: tuple[StoredTable, ...]
    activity: Table
    #: Each input table's columns the store holds that the form does not, by table name, each
    #: by its name and kind: what a quote's rows carry back to the save (READ-05).
    kept: Mapping[str, tuple[tuple[str, str], ...]] = field(default_factory=dict)

    @property
    def details(self) -> StoredTable:
        return self.by_name(QUOTE_DETAILS_NAME)

    def by_name(self, name: str) -> StoredTable:
        for stored in self.tables:
            if stored.form.name == name:
                return stored
        raise KeyError(name)


def structure(form: FormSpec) -> dict[str, Any]:
    """What of *form* decides the store's tables: each table's name and rows, and each
    column's name, type and whether it is the index, whatever its block, as the SQL Haute
    writes plans from it,
    each table and column under the ``key`` it is planned by, its name in the form (READ-23).
    Labels, rules and the sheets decide nothing there."""
    return {
        "tables": [
            {
                "key": table.name,
                "name": table.name,
                "rows": table.rows,
                "columns": [
                    {
                        "key": column.name,
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
    table name, each by its name and what it holds: ``quote_details``' own
    (:data:`STORE_COLUMNS`), and a many-row table's ``table_index``."""
    kept: dict[str, list[tuple[str, StoreColumnKind]]] = {}
    for table in form.data_schema.tables:
        if table.name == QUOTE_DETAILS_NAME:
            kept[table.name] = [(name, _kind(made())) for name, made, _ in STORE_COLUMNS]
        elif table.rows == "many":
            kept[table.name] = [(TABLE_INDEX, "int")]
    return kept


def store_schema(form: FormSpec, *, sql_schema: str | None = None) -> StoreSchema:
    """The store's tables for *form*: its tables, then ``haute_activity``, each in the SQL
    schema *sql_schema*, or the connection's default when it names none."""
    metadata = MetaData(schema=sql_schema)
    tables = tuple(_stored(metadata, table) for table in form.data_schema.tables)
    return StoreSchema(metadata=metadata, tables=tables, activity=_activity_table(metadata))


#: The SQL type each kind a kept column can be of is written as (``_catalogue.kind_of``): the
#: kinds of the types Haute gives a form's columns.
_KEPT_TYPES: dict[str, Callable[[], TypeEngine[Any]]] = {
    "boolean": Boolean,
    "integer": BigInteger,
    "float": _double,
    "date": _date,
    "datetime": UtcDateTime,
    "string": _text,
}


def with_kept_columns(
    schema: StoreSchema, extras: Mapping[str, list[tuple[str, str]]]
) -> StoreSchema:
    """*schema* with each input table's store-only columns, *extras* by table name as the
    database's catalogue holds them (name and kind), read and written with its rows (READ-05).

    A column leaves the form but the SQL Haute writes keeps it, so a saved quote's values stay
    in it. A save deletes a quote's rows and writes them again, so its rows carry those values
    back, a former output's of an input table among them. ``quote_details`` is updated in
    place, and a table of outputs alone is written afresh by each pricing, so neither carries
    any; nor does a column of a kind Haute never writes.
    """
    kept: dict[str, tuple[tuple[str, str], ...]] = {}
    for stored in schema.tables:
        if not stored.form.has_inputs or stored.form.name == QUOTE_DETAILS_NAME:
            continue
        carried = tuple(
            (name, kind)
            for name, kind in extras.get(stored.table.name, [])
            if kind in _KEPT_TYPES and name not in stored.table.columns
        )
        for name, kind in carried:
            stored.table.append_column(Column(name, _KEPT_TYPES[kind](), nullable=True))
        if carried:
            kept[stored.form.name] = carried
    return dataclasses.replace(schema, kept=kept)


def _stored(metadata: MetaData, table: SchemaTable) -> StoredTable:
    details = table.name == QUOTE_DETAILS_NAME
    columns: list[Column[Any]] = []
    for column in table.columns:
        identity = column.name in IDENTITY_NAMES or (details and column.name in _IDENTITY)
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
        columns.append(Column(TABLE_INDEX, BigInteger, nullable=False))
        keys.append(TABLE_INDEX)
    constraints: list[Any] = [PrimaryKeyConstraint(*keys, name=_named("pk", table.name))]
    if details:
        constraints.extend(
            [
                Index(_named("ix_list", table.name), "archived", "assignee", "saved_at"),
                Index(_named("ix_source", table.name), "source_reference", "source_option"),
            ]
        )
    else:
        constraints.append(
            ForeignKeyConstraint(
                list(_IDENTITY),
                [f"{QUOTE_DETAILS_NAME}.{name}" for name in _IDENTITY],
                name=_named("fk", table.name),
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
            [f"{QUOTE_DETAILS_NAME}.{name}" for name in _IDENTITY],
            name="haute_fk_activity_quote",
        ),
        Index("haute_ix_activity_quote", "quote_reference", "quote_option", "activity_id"),
    )
