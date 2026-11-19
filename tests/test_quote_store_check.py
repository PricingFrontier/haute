"""The quote store's check proven against every line of the SQL Haute writes (specs/workbench).

The store's tables are described from the store schema's own ``Table`` objects, the ones the
SQL Haute writes is rendered from: each column (its name, type, null and numbering), primary
key, unique constraint, foreign key and index. Anything else a table holds fails the
description, and the description is shown to render the SQL Haute writes exactly, so a line
added to that SQL is either described here or fails. A store is then made from the
description with one thing removed or altered, each in a SQL schema (on a server) or a file
(on SQLite) of its own, and the app's check (:func:`open_engine`) must refuse it and the
schema pane's comparison mark it, each naming the thing, read through the catalogue the
browser carries. An alteration that changes nothing the store writes or reads is listed in
``ACCEPTED`` with its reason, and is shown accepted.

Some alterations exist only on some engines. SQLite keeps any integer in 64 bits, any real as
a double and any text or moment as written, whatever its declared type, so no type of a kind
holds less there; its foreign keys name no SQL schema; and only SQL Server's text holds a
collation the store pins, or fails to hold every character.
"""

from __future__ import annotations

import io
import json
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import (
    BIGINT,
    REAL,
    BigInteger,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Table,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import mssql, postgresql
from sqlalchemy.engine import Dialect, make_url
from sqlalchemy.types import TypeDecorator, TypeEngine

from haute._quote_store import QuoteStoreMismatchError, store_url
from haute._quote_store._catalogue import (
    CatalogueMovedError,
    CatalogueTable,
    capacity_of,
    capacity_problems,
    kind_of,
    on_engine,
    read_catalogue,
    table_problems,
)
from haute._quote_store._compare import Difference, compare, read_store_catalogue
from haute._quote_store._environment import StoreLocation
from haute._quote_store._migrate import _SCRIPT_TRANSACTIONS, PROVIDER_DIALECTS, create_table
from haute._quote_store._providers import PROVIDERS
from haute._quote_store._schema import (
    ACTIVITY_TABLE,
    STORE_COLUMN_NAMES,
    UtcDateTime,
    column_type,
    store_schema,
)
from haute._quote_store._store import make_engine, open_engine
from haute._workbench_catalogue import api_tables, catalogue_of
from haute._workbench_form import QUOTE_DETAILS_NAME, FormSpec, with_quote_details
from haute.schemas import WorkbenchCatalogueTable
from tests._store_servers import HOSTED, Target, backends, configured, database

BACKENDS = ("sqlite", "postgresql", "sqlserver")


def _column(name: str, type_: str = "str", **rest: Any) -> dict[str, Any]:
    return {"name": name, "type": type_, **rest}


#: A form of every table and column the store makes: quote_details, a one-row table of every
#: type, a many-row table numbered by an Integer index with inputs and outputs, one indexed
#: by typed Text, and a table of outputs alone.
FORM: FormSpec = with_quote_details(
    FormSpec.model_validate(
        {
            "name": "motor",
            "schema": {
                "tables": [
                    {
                        "name": "policy",
                        "rows": "one",
                        "inputs": [
                            _column("region"),
                            _column("drivers", "int"),
                            _column("exposure", "float"),
                            _column("garaged", "bool"),
                            _column("start", "date"),
                        ],
                    },
                    {
                        "name": "vehicles",
                        "rows": "many",
                        "inputs": [
                            _column("vehicle", "int", index="numbered"),
                            _column("reg"),
                            _column("value", "float"),
                        ],
                        "outputs": [_column("premium", "float")],
                    },
                    {
                        "name": "claims",
                        "rows": "many",
                        "inputs": [_column("claim", index="typed"), _column("amount", "float")],
                    },
                    {"name": "pricing", "rows": "one", "outputs": [_column("total", "float")]},
                ]
            },
            "pages": [
                {
                    "id": "p",
                    "title": "Sheet 1",
                    "width": 1280,
                    "sections": [{"id": "s", "title": "s", "height": 120, "widgets": []}],
                }
            ],
        }
    )
)

#: What an alteration that changes nothing the store writes or reads is, and why: each still
#: accepted by the check and matched by the comparison.
ACCEPTED: dict[str, str] = {
    "holds more": "a type holding more than the store's keeps every value the store writes",
    "named otherwise": (
        "nothing reads a constraint or an index by its name, so a store whose names were made "
        "otherwise opens and saves as it did (_schema._named)"
    ),
    "index gone": (
        "an index that is not unique decides how fast a read finds rows, never what is stored "
        "or read"
    ),
    "whole seconds": (
        "Haute shows a moment to the minute and pages the quote list by the moment as the "
        "database keeps it, so fewer digits of a second change no answer"
    ),
    "with its offset": (
        "SQL Server's DATETIMEOFFSET keeps the UTC moment the store writes at offset zero and "
        "reads it back as the same moment"
    ),
    "unique by an index": (
        "a unique index holds its columns unique in every row, as the unique constraint does"
    ),
}


# ── The description ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class ColumnLine:
    """A column of a CREATE TABLE: its name, type, null, numbering and autoincrement."""

    name: str
    type: TypeEngine[Any]
    nullable: bool
    identity: Identity | None
    autoincrement: bool | str = "auto"


@dataclass(frozen=True)
class KeyLine:
    """A primary key or unique constraint: its name and columns."""

    name: str | None
    columns: tuple[str, ...]


#: Where a table outside the store's SQL schema is: False for none, True for the case's own
#: schema set aside (:data:`_ASIDE`), or a schema by name.
Aside = bool | str


@dataclass(frozen=True)
class ReferenceLine:
    """A foreign key: its name, the columns it constrains, the table it refers to, that
    table's columns, and whether that table is set aside (:data:`Aside`)."""

    name: str | None
    columns: tuple[str, ...]
    table: str
    referred: tuple[str, ...]
    aside: Aside = False


@dataclass(frozen=True)
class IndexLine:
    """A CREATE INDEX: its name, columns, whether it is unique and the predicate filtering
    the rows it holds, None for every row."""

    name: str | None
    columns: tuple[str, ...]
    unique: bool
    where: str | None = None


@dataclass(frozen=True)
class TableLines:
    """A table as its SQL makes it: its columns, keys, references (in the order the table
    declares them, keys and references together) and indexes, whether it is set aside
    (:data:`Aside`) and, on SQLite, whether it has no rowid."""

    name: str
    columns: tuple[ColumnLine, ...]
    primary_key: KeyLine | None
    constraints: tuple[KeyLine | ReferenceLine, ...] = ()
    indexes: tuple[IndexLine, ...] = ()
    aside: Aside = False
    without_rowid: bool = False

    def column(self, name: str) -> ColumnLine:
        return next(column for column in self.columns if column.name == name)

    @property
    def unique(self) -> tuple[KeyLine, ...]:
        return tuple(each for each in self.constraints if isinstance(each, KeyLine))

    @property
    def references(self) -> tuple[ReferenceLine, ...]:
        return tuple(each for each in self.constraints if isinstance(each, ReferenceLine))


#: A column's attributes that render into its SQL which this description does not hold: one
#: set is a line it does not know.
_UNDESCRIBED = ("server_default", "server_onupdate", "computed", "comment")


def _names(constraint: Any) -> tuple[str, ...]:
    return tuple(column.name for column in constraint.columns)


def _described(table: Table) -> TableLines:
    """*table* as lines, failing on anything of it this description does not hold."""
    assert table.comment is None and not table.dialect_kwargs, table.name
    columns: list[ColumnLine] = []
    for column in table.columns:
        # An identity is held as the column's server default too.
        unknown = [
            name
            for name in _UNDESCRIBED
            if getattr(column, name) not in (None, column.identity)
            or (name != "server_default" and getattr(column, name) is not None)
        ]
        assert not unknown and not column.dialect_kwargs, (
            f"{table.name}.{column.name} holds {unknown or column.dialect_kwargs}, which this "
            "test does not describe: describe and alter it here, and check it in _catalogue."
        )
        assert column.autoincrement == "auto", f"{table.name}.{column.name}"
        columns.append(ColumnLine(column.name, column.type, bool(column.nullable), column.identity))
    primary_key: KeyLine | None = None
    constraints: list[KeyLine | ReferenceLine] = []
    for constraint in sorted(table.constraints, key=lambda each: each._creation_order):
        if isinstance(constraint, PrimaryKeyConstraint):
            primary_key = (
                KeyLine(constraint.name, _names(constraint)) if constraint.columns else None
            )
        elif isinstance(constraint, UniqueConstraint):
            constraints.append(KeyLine(constraint.name, _names(constraint)))
        elif isinstance(constraint, ForeignKeyConstraint):
            assert (
                constraint.ondelete is None
                and constraint.onupdate is None
                and constraint.deferrable is None
                and constraint.initially is None
                and constraint.match is None
                and not constraint.use_alter
            ), f"{table.name}'s {constraint.name} holds options this test does not describe."
            referred = {element.column.table for element in constraint.elements}
            assert len(referred) == 1 and next(iter(referred)).schema == table.schema
            constraints.append(
                ReferenceLine(
                    constraint.name,
                    tuple(element.parent.name for element in constraint.elements),
                    next(iter(referred)).name,
                    tuple(element.column.name for element in constraint.elements),
                )
            )
        else:
            raise AssertionError(
                f"{table.name} holds a {type(constraint).__name__}, which this test does not "
                "describe: describe and alter it here, and check it in _catalogue."
            )
    indexes: list[IndexLine] = []
    for index in sorted(table.indexes, key=lambda each: str(each.name)):
        assert all(isinstance(each, Column) for each in index.expressions), index.name
        assert not index.dialect_kwargs, index.name
        indexes.append(IndexLine(index.name, _names(index), bool(index.unique)))
    return TableLines(table.name, tuple(columns), primary_key, tuple(constraints), tuple(indexes))


def describe(form: FormSpec) -> tuple[TableLines, ...]:
    """The store's tables for *form* as lines, in the order the SQL Haute writes makes them:
    the form's, then haute_activity."""
    schema = store_schema(form)
    return (*(_described(stored.table) for stored in schema.tables), _described(schema.activity))


#: The SQL schema each case's referred copy is set aside in, after the case's own.
_ASIDE = "{}_aside"
#: The dialects whose filtered index takes its predicate as ``<name>_where``.
_WHERE_DIALECTS = ("postgresql", "sqlite", "mssql")


def _tables(lines: tuple[TableLines, ...], sql_schema: str | None) -> list[Table]:
    """*lines* as SQLAlchemy tables in *sql_schema*, those set aside where they say."""
    metadata = MetaData(schema=sql_schema)

    def placed(aside: Aside) -> str | None:
        if isinstance(aside, str):
            return aside
        return _ASIDE.format(sql_schema or "dbo") if aside else None

    tables: list[Table] = []
    for each in lines:
        items: list[Any] = [
            Column(
                column.name,
                column.type,
                *([column.identity._copy()] if column.identity is not None else []),
                nullable=column.nullable,
                autoincrement=column.autoincrement,
            )
            for column in each.columns
        ]
        if each.primary_key is not None:
            items.append(
                PrimaryKeyConstraint(*each.primary_key.columns, name=each.primary_key.name)
            )
        for constraint in each.constraints:
            if isinstance(constraint, KeyLine):
                items.append(UniqueConstraint(*constraint.columns, name=constraint.name))
                continue
            where = "" if not constraint.aside else f"{placed(constraint.aside)}."
            items.append(
                ForeignKeyConstraint(
                    list(constraint.columns),
                    [f"{where}{constraint.table}.{name}" for name in constraint.referred],
                    name=constraint.name,
                )
            )
        items.extend(
            Index(
                index.name,
                *index.columns,
                unique=index.unique,
                **(
                    {}
                    if index.where is None
                    else {f"{name}_where": text(index.where) for name in _WHERE_DIALECTS}
                ),
            )
            for index in each.indexes
        )
        rowid = {"sqlite_with_rowid": False} if each.without_rowid else {}
        tables.append(Table(each.name, metadata, *items, schema=placed(each.aside), **rowid))
    return tables


def render(lines: tuple[TableLines, ...], backend: str, sql_schema: str | None = None) -> str:
    """*lines* as SQL for *backend*, rendered as the SQL Haute writes is: each table made by
    Alembic offline as the store's are (:func:`_migrate.create_table`), in turn, in the
    script's one transaction."""
    dialect = PROVIDER_DIALECTS[backend]
    buffer = io.StringIO()
    options: dict[str, Any] = {"as_sql": True, "output_buffer": buffer, "literal_binds": True}
    if dialect == "mssql":
        options["mssql_batch_separator"] = None
    operations = Operations(MigrationContext.configure(dialect_name=dialect, opts=options))
    begin, commit = _SCRIPT_TRANSACTIONS[dialect]
    buffer.write(begin)
    for table in _tables(lines, sql_schema):
        create_table(operations, table)
    buffer.write(commit)
    return buffer.getvalue()


def _engine_dialect(backend: str) -> Dialect:
    dialect: Dialect = make_url(f"{PROVIDER_DIALECTS[backend]}://").get_dialect()()
    return dialect


def _held(column: ColumnLine, backend: str) -> TypeEngine[Any]:
    """The column's type as *backend*'s engine holds it."""
    held = column.type.dialect_impl(_engine_dialect(backend))
    return held.impl_instance if isinstance(held, TypeDecorator) else held


# ── Altering one thing ────────────────────────────────────────────────────


@dataclass(frozen=True)
class Case:
    """A store made from the description with one thing altered: *what* says which, *lines*
    make it, *said* is each fragment one of the check's differences holds and *marked* the
    comparison's mark naming it, both None for an alteration ``ACCEPTED`` lists."""

    what: str
    lines: tuple[TableLines, ...]
    said: tuple[str, ...] | None
    marked: Callable[[Difference], bool] | None
    accepted: str | None = None


def _swap(lines: tuple[TableLines, ...], name: str, made: TableLines) -> tuple[TableLines, ...]:
    return tuple(made if each.name == name and not each.aside else each for each in lines)


def _table_in(lines: tuple[TableLines, ...], name: str) -> TableLines:
    return next(each for each in lines if each.name == name and not each.aside)


def _sound(lines: tuple[TableLines, ...]) -> tuple[TableLines, ...]:
    """*lines* without what can no longer be made: a key, unique constraint, index or
    reference naming a column gone, and a reference to columns its table no longer keys or
    holds unique, on every engine."""
    by_name = {(each.name, each.aside): each for each in lines}

    def keyed(reference: ReferenceLine) -> bool:
        referred = by_name.get((reference.table, reference.aside))
        if referred is None:
            return False
        held = {column.name for column in referred.columns}
        keys = [each.columns for each in referred.unique]
        if referred.primary_key is not None:
            keys.append(referred.primary_key.columns)
        return set(reference.referred) <= held and set(reference.referred) in map(set, keys)

    made: list[TableLines] = []
    for each in lines:
        held = {column.name for column in each.columns}
        primary_key = each.primary_key
        if primary_key is not None and not set(primary_key.columns) <= held:
            primary_key = None
        made.append(
            replace(
                each,
                primary_key=primary_key,
                constraints=tuple(
                    constraint
                    for constraint in each.constraints
                    if set(constraint.columns) <= held
                    and (isinstance(constraint, KeyLine) or keyed(constraint))
                ),
                indexes=tuple(index for index in each.indexes if set(index.columns) <= held),
            )
        )
    return tuple(made)


def _unlinked(lines: tuple[TableLines, ...], table: str, column: str) -> tuple[TableLines, ...]:
    """*lines* without each reference *table*'s *column* takes part in, either side, which an
    engine refuses once their types or collations differ."""
    return tuple(
        replace(
            each,
            constraints=tuple(
                constraint
                for constraint in each.constraints
                if not isinstance(constraint, ReferenceLine)
                or not (
                    (each.name == table and column in constraint.columns)
                    or (constraint.table == table and column in constraint.referred)
                )
            ),
        )
        for each in lines
    )


def _with_column(
    lines: tuple[TableLines, ...], table: TableLines, name: str, **changes: Any
) -> tuple[TableLines, ...]:
    column = table.column(name)
    made = replace(
        table,
        columns=tuple(
            replace(column, **changes) if each is column else each for each in table.columns
        ),
    )
    return _swap(lines, table.name, made)


def _column_mark(table: str, column: str, kind: str) -> Callable[[Difference], bool]:
    """The comparison's mark on *table*'s *column*, of *kind*: the store's own columns and
    haute_activity's marked ``store``, haute_activity's on no table, naming the column."""
    if table == ACTIVITY_TABLE:
        return lambda each: (
            each.table is None
            and each.kind == "store"
            and f"{ACTIVITY_TABLE}.{column}," in each.message
        )
    if table == QUOTE_DETAILS_NAME and column in STORE_COLUMN_NAMES:
        kind = "store"
    return lambda each: each.table == table and each.column == column and each.kind == kind


def _table_mark(table: str, *kinds: str) -> Callable[[Difference], bool]:
    """The comparison's mark on *table* as a whole, of one of *kinds*: haute_activity's on no
    table, naming it."""
    if table == ACTIVITY_TABLE:
        return lambda each: (
            each.table is None
            and each.column is None
            and each.kind in kinds
            and ACTIVITY_TABLE in each.message
        )
    return lambda each: each.table == table and each.column is None and each.kind in kinds


#: A type of another kind than each kind, keyable on every engine.
_OTHER_KIND: dict[str, Callable[[], TypeEngine[Any]]] = {
    "integer": lambda: String(40),
    "float": BigInteger,
    "string": BigInteger,
    "boolean": BigInteger,
    "date": lambda: String(40),
    # A dialect not connected renders a Date as DATETIME on SQL Server, as _schema pins.
    "datetime": lambda: Date().with_variant(mssql.DATE(), "mssql"),
}
#: How each fragment a type holding less is said by the check.
_HOLDS_LESS = {
    "integer": "holds smaller whole numbers than",
    "float": "keeps fewer digits of a number than",
    "length": "holds shorter text than",
    "every_character": "cannot hold every character",
    "unpadded": "pads its text with spaces",
    "zoneless": "reads a moment in the session's time zone",
}


def _narrower(column: ColumnLine, backend: str) -> list[tuple[str, TypeEngine[Any] | None, str]]:
    """Each type of *column*'s kind holding less than the store's on *backend*, by what it
    lacks, with the ``ACCEPTED`` reason where it changes nothing the store keeps: none on
    SQLite, which keeps any integer, double, text or moment whatever its declared type."""
    if backend == "sqlite":
        return []
    held = _held(column, backend)
    found: list[tuple[str, TypeEngine[Any] | None, str]] = []
    collation = getattr(held, "collation", None)
    if isinstance(held, BigInteger):
        found.append(("integer", Integer(), ""))
    elif isinstance(held, Integer):
        found.append(("integer", SmallInteger(), ""))
    elif isinstance(held, Float):
        found.append(("float", REAL(), ""))
    elif isinstance(held, String):
        length = held.length
        if backend == "postgresql":
            found.append(("length", String(100 if length is None else length // 2), ""))
            found.append(("unpadded", postgresql.CHAR(100 if length is None else length), ""))
        else:
            shorter = 100 if length is None else length // 2
            found.append(("length", mssql.NVARCHAR(shorter, collation=collation), ""))
            found.append(("every_character", mssql.VARCHAR(length, collation=collation), ""))
            found.append(
                (
                    "unpadded",
                    mssql.NCHAR(100 if length is None else length, collation=collation),
                    "",
                )
            )
    elif isinstance(held, DateTime):
        if backend == "postgresql":
            found.append(("zoneless", postgresql.TIMESTAMP(timezone=True), ""))
            found.append(("seconds", postgresql.TIMESTAMP(precision=0), "whole seconds"))
        else:
            found.append(("zoneless", mssql.DATETIMEOFFSET(), "with its offset"))
            found.append(("seconds", mssql.DATETIME2(precision=0), "whole seconds"))
    return found


def _wider(column: ColumnLine, backend: str) -> TypeEngine[Any] | None:
    """A type of *column*'s kind holding more than the store's, for bounded text."""
    held = _held(column, backend)
    if not isinstance(held, String) or held.length is None:
        return None
    if backend == "sqlserver":
        return mssql.NVARCHAR(2 * held.length, collation=getattr(held, "collation", None))
    return String(2 * held.length)


def _other_columns(table: TableLines, columns: tuple[str, ...], backend: str) -> tuple[str, ...]:
    """Columns of *table* other than *columns*, each of the type of one of them in turn, a
    foreign key could refer to instead."""
    dialect = _engine_dialect(backend)

    def rendered(name: str) -> str:
        return str(table.column(name).type.compile(dialect=dialect))

    chosen: list[str] = []
    for name in columns:
        chosen.append(
            next(
                each.name
                for each in table.columns
                if each.name not in columns
                and each.name not in chosen
                and rendered(each.name) == rendered(name)
            )
        )
    return tuple(chosen)


def _copy_of(table: TableLines, columns: tuple[str, ...], **changes: Any) -> TableLines:
    """A table holding *table*'s *columns* alone, keyed by them."""
    return TableLines(
        changes.pop("name", table.name),
        tuple(replace(table.column(name), nullable=False) for name in columns),
        KeyLine(None, columns),
        **changes,
    )


def alterations(lines: tuple[TableLines, ...], backend: str) -> list[Case]:
    """Every store made from *lines* with one thing removed or altered, on *backend*."""
    cases: list[Case] = []
    for table in lines:
        name = table.name
        dropped = _sound(tuple(each for each in lines if each.name != name))
        cases.append(
            Case(
                f"{name} gone",
                dropped,
                (f"the table {name} is missing",),
                _table_mark(name, "missing"),
            )
        )
        for column in table.columns:
            where = f"{name}.{column.name}"
            gone = replace(
                table, columns=tuple(each for each in table.columns if each is not column)
            )
            cases.append(
                Case(
                    f"{where} gone",
                    _sound(_swap(lines, name, gone)),
                    (f"{where} is missing",),
                    _column_mark(name, column.name, "missing"),
                )
            )
            kind = kind_of(column.type)
            unlinked = _unlinked(lines, name, column.name)
            unlinked_table = _table_in(unlinked, name)
            retyped = dict(type=_OTHER_KIND[kind](), identity=None, autoincrement=False)
            cases.append(
                Case(
                    f"{where} of another kind",
                    _with_column(unlinked, unlinked_table, column.name, **retyped),
                    (f"{where} holds ", f", not {kind}"),
                    _column_mark(name, column.name, "type"),
                )
            )
            keyed = table.primary_key is not None and column.name in table.primary_key.columns
            # A key's column, or a numbered one, takes no null on PostgreSQL or SQL Server:
            # the key and the numbering go with it.
            unkeyed = (
                _sound(_swap(lines, name, replace(table, primary_key=None))) if keyed else lines
            )
            nulled = _with_column(
                unkeyed,
                _table_in(unkeyed, name),
                column.name,
                nullable=not column.nullable,
                **({"identity": None, "autoincrement": False} if column.identity else {}),
            )
            cases.append(
                Case(
                    f"{where} taking a null otherwise",
                    nulled,
                    (f"{where} takes ",),
                    _column_mark(name, column.name, "null"),
                )
            )
            for measure, narrower, accepted in _narrower(column, backend):
                altered = _with_column(unlinked, unlinked_table, column.name, type=narrower)
                if accepted:
                    cases.append(Case(f"{where} {measure}", altered, None, None, accepted))
                    continue
                cases.append(
                    Case(
                        f"{where} holding less: {measure}",
                        altered,
                        (f"{where} is ", _HOLDS_LESS[measure]),
                        _column_mark(name, column.name, "type"),
                    )
                )
            collation = (
                getattr(_held(column, backend), "collation", None)
                if backend == "sqlserver"
                else None
            )
            if collation:
                held = _held(column, backend)
                cases.append(
                    Case(
                        f"{where} under another collation",
                        _with_column(
                            unlinked,
                            unlinked_table,
                            column.name,
                            type=mssql.NVARCHAR(
                                getattr(held, "length", None), collation="Latin1_General_100_CI_AS"
                            ),
                        ),
                        (f"{where} compares under Latin1_General_100_CI_AS, not {collation}",),
                        _column_mark(name, column.name, "collation"),
                    )
                )
            if column.identity is not None:
                unnumbered: dict[str, Any] = dict(identity=None, autoincrement=False)
                if backend == "sqlite":
                    # SQLite numbers a sole INTEGER primary key whatever else it says.
                    unnumbered["type"] = BIGINT()
                cases.append(
                    Case(
                        f"{where} not numbered",
                        _with_column(lines, table, column.name, **unnumbered),
                        (f"{where} is not numbered by the database",),
                        _column_mark(name, column.name, "numbered"),
                    )
                )
                if backend == "sqlite":
                    # Its INTEGER key kept, a table with no rowid numbers no row.
                    cases.append(
                        Case(
                            f"{name} with no rowid",
                            _swap(lines, name, replace(table, without_rowid=True)),
                            (f"{where} is not numbered by the database",),
                            _column_mark(name, column.name, "numbered"),
                        )
                    )
            wider = _wider(column, backend)
            if wider is not None and unlinked == lines:
                cases.append(
                    Case(
                        f"{where} holding more",
                        _with_column(lines, table, column.name, type=wider),
                        None,
                        None,
                        "holds more",
                    )
                )
        key = table.primary_key
        if key is not None:
            cases.append(
                Case(
                    f"{name} keyed by nothing",
                    _sound(_swap(lines, name, replace(table, primary_key=None))),
                    (f"{name} is keyed by nothing, not {', '.join(key.columns)}",),
                    _table_mark(name, "key", "rows"),
                )
            )
            if len(key.columns) > 1:
                fewer = KeyLine(key.name, key.columns[:-1])
                cases.append(
                    Case(
                        f"{name} keyed by fewer columns",
                        _sound(_swap(lines, name, replace(table, primary_key=fewer))),
                        (f"{name} is keyed by {', '.join(fewer.columns)}, not",),
                        _table_mark(name, "key", "rows"),
                    )
                )
        for constraint in table.constraints:
            others = tuple(each for each in table.constraints if each is not constraint)
            without = _swap(lines, name, replace(table, constraints=others))
            if isinstance(constraint, KeyLine):
                held = ", ".join(constraint.columns)
                not_unique = (f"{name} does not hold {held} unique",)
                cases.append(
                    Case(
                        f"{name} not holding {held} unique",
                        _sound(without),
                        not_unique,
                        _table_mark(name, "key"),
                    )
                )
                by_index = IndexLine(None, constraint.columns, unique=True)
                cases.append(
                    Case(
                        f"{name} holding {held} unique by an index",
                        _swap(
                            lines,
                            name,
                            replace(table, constraints=others, indexes=(*table.indexes, by_index)),
                        ),
                        None,
                        None,
                        "unique by an index",
                    )
                )
                # Unique only among the rows a predicate keeps: two outside it hold the same.
                beside = next(each.name for each in table.columns if each.nullable)
                filtered = replace(by_index, where=f"{beside} IS NULL")
                cases.append(
                    Case(
                        f"{name} holding {held} unique only where {beside} is null",
                        _swap(
                            lines,
                            name,
                            replace(table, constraints=others, indexes=(*table.indexes, filtered)),
                        ),
                        not_unique,
                        _table_mark(name, "key"),
                    )
                )
                continue
            refused = (
                f"{name} does not refer by {', '.join(constraint.columns)} to {constraint.table}",
            )
            marked = _table_mark(name, "refers")
            cases.append(Case(f"{name}'s reference gone", without, refused, marked))
            referred = next(each for each in lines if each.name == constraint.table)
            copy = _copy_of(referred, constraint.referred, name=f"{constraint.table}_copy")
            cases.append(
                Case(
                    f"{name} referring to another table",
                    (
                        copy,
                        *_swap(
                            lines,
                            name,
                            replace(
                                table, constraints=(*others, replace(constraint, table=copy.name))
                            ),
                        ),
                    ),
                    refused,
                    marked,
                )
            )
            elsewhere = _other_columns(referred, constraint.referred, backend)
            held_unique = replace(
                referred, constraints=(*referred.constraints, KeyLine(None, elsewhere))
            )
            moved = _swap(lines, referred.name, held_unique)
            moved_table = next(each for each in moved if each.name == name)
            cases.append(
                Case(
                    f"{name} referring to other columns",
                    _swap(
                        moved,
                        name,
                        replace(
                            moved_table,
                            constraints=(*others, replace(constraint, referred=elsewhere)),
                        ),
                    ),
                    refused,
                    marked,
                )
            )
            if backend != "sqlite":
                aside = _copy_of(referred, constraint.referred, aside=True)
                cases.append(
                    Case(
                        f"{name} referring to another schema",
                        (
                            aside,
                            *_swap(
                                lines,
                                name,
                                replace(
                                    table, constraints=(*others, replace(constraint, aside=True))
                                ),
                            ),
                        ),
                        refused,
                        marked,
                    )
                )
        for index in table.indexes:
            assert not index.unique, f"{name}'s unique index {index.name} needs its cases here"
            others_indexes = tuple(each for each in table.indexes if each is not index)
            cases.append(
                Case(
                    f"{name}'s index {index.name} gone",
                    _swap(lines, name, replace(table, indexes=others_indexes)),
                    None,
                    None,
                    "index gone",
                )
            )
    renamed = tuple(
        replace(
            each,
            primary_key=None
            if each.primary_key is None
            else replace(each.primary_key, name=f"other_pk_{at}"),
            constraints=tuple(
                replace(constraint, name=f"other_{at}_{place}")
                for place, constraint in enumerate(each.constraints)
            ),
            indexes=tuple(
                replace(index, name=f"other_ix_{at}_{place}")
                for place, index in enumerate(each.indexes)
            ),
        )
        for at, each in enumerate(lines)
    )
    cases.append(
        Case("every constraint and index named otherwise", renamed, None, None, "named otherwise")
    )
    return cases


# ── Making and judging a store ────────────────────────────────────────────


def _made(target: Target, lines: tuple[TableLines, ...], sql_schema: str | None) -> None:
    """The store *lines* make, in *sql_schema* and, for what is set aside, its aside schema."""
    if sql_schema is not None:
        target.script(f"CREATE SCHEMA {sql_schema}")
        if any(each.aside for each in lines):
            target.script(f"CREATE SCHEMA {_ASIDE.format(sql_schema)}")
    target.script(render(lines, target.backend, sql_schema))


def _browsed(target: Target, sql_schema: str | None) -> Any:
    """The store's catalogue as the schema pane compares it: read with its counts, sent to
    the browser and back."""
    engine = make_engine(target.url, sign_in=target.sign_in)
    try:
        with engine.connect() as connection:
            read = read_catalogue(connection, sql_schema, count=True)
    finally:
        engine.dispose()
    sent = json.loads(json.dumps([table.model_dump(mode="json") for table in api_tables(read)]))
    return catalogue_of([WorkbenchCatalogueTable.model_validate(table) for table in sent])


def _judged(target: Target, sql_schema: str | None) -> tuple[list[str], list[Difference]]:
    """What the app's check refuses in the store, none when it opens, and the comparison's
    marks."""
    try:
        engine, _ = open_engine(
            target.url, FORM, environment=HOSTED, sql_schema=sql_schema, sign_in=target.sign_in
        )
    except QuoteStoreMismatchError as refused:
        refusal = refused.differences
    else:
        engine.dispose()
        refusal = []
    comparison = compare(FORM, _browsed(target, sql_schema), provider=PROVIDERS[target.backend])
    return refusal, list(comparison.differences)


def _failure(case: Case, refusal: list[str], marks: list[Difference]) -> str | None:
    """What is wrong with how the check and the comparison judged *case*'s store, if
    anything."""
    if case.said is None:
        if refusal or marks:
            return f"{case.what} ({ACCEPTED[str(case.accepted)]}) was refused: {refusal} {marks}"
        return None
    if not any(all(each in difference for each in case.said) for difference in refusal):
        return f"{case.what}: the check did not say {case.said}: {refusal}"
    assert case.marked is not None
    if not any(case.marked(mark) for mark in marks):
        return f"{case.what}: the comparison did not mark it: {marks}"
    return None


@pytest.fixture(params=backends())
def target(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Target]:
    with database(request.param, tmp_path) as made:
        yield made


# ── The tests ─────────────────────────────────────────────────────────────


#: Each engine with the SQL schemas a store is made in there: SQLite's is its file's own.
_PLACES = [
    ("sqlite", None),
    ("postgresql", None),
    ("postgresql", "store"),
    ("sqlserver", None),
    ("sqlserver", "store"),
]


@pytest.mark.parametrize(("backend", "sql_schema"), _PLACES)
def test_the_description_renders_the_sql_haute_writes(backend: str, sql_schema: str | None) -> None:
    written = Target(backend, "").sql(FORM, sql_schema=sql_schema)

    assert render(describe(FORM), backend, sql_schema) == written


def test_a_line_the_description_does_not_know_fails_it() -> None:
    table = Table(
        "policy",
        MetaData(),
        Column("quote_reference", String(40), primary_key=True),
        Column("drivers", BigInteger, server_default="1"),
    )
    with pytest.raises(AssertionError, match="server_default"):
        _described(table)
    checked = Table("policy", MetaData(), Column("x", BigInteger), CheckConstraint("x > 0"))
    with pytest.raises(AssertionError, match="CheckConstraint"):
        _described(checked)


def test_a_constraint_the_check_does_not_compare_is_a_fault() -> None:
    checked = Table("policy", MetaData(), Column("x", BigInteger), CheckConstraint("x > 0"))

    with pytest.raises(TypeError, match="compares no CheckConstraint"):
        table_problems(checked, CatalogueTable(name="policy", columns=(), primary_key=()))


@pytest.mark.parametrize(
    ("backend", "found", "kind", "short"),
    [
        ("postgresql", REAL(), "float", ["bits"]),
        ("postgresql", postgresql.DOUBLE_PRECISION(precision=53), "float", []),
        ("postgresql", Integer(), "int", ["bits"]),
        ("postgresql", String(100), "str", ["length"]),
        ("postgresql", postgresql.CHAR(10), "str", ["length", "unpadded"]),
        ("postgresql", postgresql.TIMESTAMP(timezone=True), "datetime", ["zoneless"]),
        ("postgresql", postgresql.TIMESTAMP(precision=0), "datetime", []),
        ("sqlserver", mssql.REAL(), "float", ["bits"]),
        ("sqlserver", mssql.FLOAT(precision=53), "float", []),
        ("sqlserver", mssql.TINYINT(), "int", ["bits"]),
        ("sqlserver", mssql.VARCHAR(None), "str", ["every_character"]),
        ("sqlserver", mssql.NCHAR(10), "str", ["length", "unpadded"]),
        ("sqlserver", mssql.DATETIMEOFFSET(), "datetime", []),
        ("sqlite", REAL(), "float", []),
        ("sqlite", Integer(), "int", []),
        ("sqlite", String(10), "str", []),
    ],
)
def test_a_type_holding_less_than_the_store_s_is_told_on_each_engine(
    backend: str, found: TypeEngine[Any], kind: str, short: list[str]
) -> None:
    # As the engine's catalogue reflects each, against the store's own type for the kind.
    dialect = _engine_dialect(backend)
    store = UtcDateTime() if kind == "datetime" else column_type(kind)

    held = capacity_of(on_engine(store, dialect), dialect)

    assert capacity_problems(capacity_of(found, dialect), held) == short


def test_a_store_made_from_the_description_is_accepted(target: Target) -> None:
    # In the connection's default SQL schema and, on a server, in a named one beside it.
    for backend, sql_schema in _PLACES:
        if backend == target.backend:
            _made(target, describe(FORM), sql_schema)

            assert _judged(target, sql_schema) == ([], []), sql_schema


@pytest.mark.parametrize(
    "server",
    [
        pytest.param(name, marks=pytest.mark.store_servers)
        for name in configured()
        if name == "postgresql"
    ],
)
def test_a_store_its_search_path_finds_behind_an_empty_schema_is_accepted(
    server: str, tmp_path: Path
) -> None:
    # PostgreSQL reads every table its search path finds when no schema is named: a store in
    # public is the connection's though an empty schema comes first on the path.
    with database(server, tmp_path) as target:
        _made(target, describe(FORM), None)
        target.script("CREATE SCHEMA app")
        target.script(
            f"ALTER DATABASE {make_url(target.url).database} SET search_path = app, public"
        )

        assert _judged(target, None) == ([], [])


@pytest.mark.parametrize(
    "server",
    [
        pytest.param(name, marks=pytest.mark.store_servers)
        for name in configured()
        if name == "sqlserver"
    ],
)
def test_a_schema_named_in_another_case_is_the_same_on_sql_server(
    server: str, tmp_path: Path
) -> None:
    # SQL Server compares identifiers under the database's collation, which ignores case on
    # the CI's server as by default: STORE is the schema its catalogue spells store.
    with database(server, tmp_path) as target:
        target.script("CREATE SCHEMA store")
        target.script(target.sql(FORM, sql_schema="STORE"))

        assert _judged(target, "STORE") == ([], [])


def _dropped(target: Target, lines: tuple[TableLines, ...], sql_schema: str) -> None:
    """The store *lines* made in *sql_schema* dropped with its schemas, each table after
    those referring to it, so the server's catalogue stays the size of one store."""
    aside = _ASIDE.format(sql_schema)
    target.script(
        "".join(
            f"DROP TABLE {aside if each.aside else sql_schema}.{each.name};\n"
            for each in reversed(lines)
        )
    )
    target.script(f"DROP SCHEMA {sql_schema}")
    if any(each.aside for each in lines):
        target.script(f"DROP SCHEMA {aside}")


#: How many stores a server holds at once, made one after another, then judged together,
#: each on its own connections (the reads are I/O bound), then dropped: SQL Server's
#: catalogue reads deadlock with DDL beside them, which snapshot isolation does not cover.
_BATCH = 30
_AT_ONCE = 6
#: The parts the cases are judged in, each a test of its own within the store-servers
#: job's time for one.
_PARTS = 3


@pytest.mark.parametrize("part", range(_PARTS))
def test_every_line_of_the_sql_haute_writes_is_checked(
    target: Target, tmp_path: Path, part: int
) -> None:
    every = alterations(describe(FORM), target.backend)
    assert {case.accepted for case in every} - {None} <= set(ACCEPTED)
    cases = every[part::_PARTS]
    failures: list[str | None] = []
    if target.backend == "sqlite":
        for number, case in enumerate(cases):
            each = Target("sqlite", store_url(tmp_path / f"case{number}.sqlite"))
            _made(each, case.lines, None)
            failures.append(_failure(case, *_judged(each, None)))
    else:
        with ThreadPoolExecutor(max_workers=_AT_ONCE) as pool:
            for start in range(0, len(cases), _BATCH):
                batch = [
                    (f"c{start + at}", case)
                    for at, case in enumerate(cases[start : start + _BATCH])
                ]
                for sql_schema, case in batch:
                    _made(target, case.lines, sql_schema)
                failures.extend(
                    pool.map(lambda each: _failure(each[1], *_judged(target, each[0])), batch)
                )
                for sql_schema, case in batch:
                    _dropped(target, case.lines, sql_schema)

    assert not [each for each in failures if each], "\n".join(each for each in failures if each)


#: Each server's default SQL schema, where a store names none.
_DEFAULT_SCHEMA = {"postgresql": "public", "sqlserver": "dbo"}


@pytest.mark.parametrize(
    ("server", "sql_schema"),
    [
        pytest.param(name, sql_schema, marks=pytest.mark.store_servers)
        for name in configured()
        for sql_schema in (None, "store")
    ],
)
def test_a_reference_to_quote_details_in_another_schema_is_named_from_any_schema(
    server: str, sql_schema: str | None, tmp_path: Path
) -> None:
    # The activity refers to a copy of quote_details in another SQL schema, as a table moved
    # and made again leaves it: from a store in the default schema, to a schema set aside;
    # from one in a named schema, to the default, which PostgreSQL's catalogue leaves unsaid
    # as it does the schema read.
    elsewhere = _DEFAULT_SCHEMA[server] if sql_schema else "elsewhere"
    lines = describe(FORM)
    details = _table_in(lines, QUOTE_DETAILS_NAME)
    activity = _table_in(lines, ACTIVITY_TABLE)
    reference = activity.references[0]
    others = tuple(each for each in activity.constraints if each is not reference)
    moved = (
        _copy_of(details, reference.referred, aside=elsewhere),
        *_swap(
            lines,
            ACTIVITY_TABLE,
            replace(activity, constraints=(*others, replace(reference, aside=elsewhere))),
        ),
    )
    with database(server, tmp_path) as target:
        for schema in {sql_schema or elsewhere, elsewhere} - set(_DEFAULT_SCHEMA.values()):
            target.script(f"CREATE SCHEMA {schema}")
        target.script(render(moved, server, sql_schema))

        refusal, marks = _judged(target, sql_schema)

    assert any(
        f"{ACTIVITY_TABLE} does not refer by quote_reference, quote_option to quote_details" in each
        and f"{elsewhere}.quote_details" in each
        for each in refusal
    ), refusal
    assert any(_table_mark(ACTIVITY_TABLE, "refers")(mark) for mark in marks), marks


#: How many times the check and the schema pane's read each run beside the DDL: SQL Server
#: made about one check in thirty a deadlock's victim in this churn before each was run
#: again (19 November 2026), so a check that is not run again fails here near certainly.
_BESIDE_DDL = 75


@pytest.mark.parametrize(
    "server", [pytest.param(name, marks=pytest.mark.store_servers) for name in configured()]
)
def test_the_check_and_the_pane_read_the_store_beside_ddl_its_owners_run(
    server: str, tmp_path: Path
) -> None:
    # The owners add and drop a column of the store's and make and drop a table elsewhere in
    # the database, again and again, while the app checks the store and the pane reads it.
    # The check reads no column it then names, so it opens every time. The pane counts the
    # quotes in each column it has read, which DDL that never stops can change under every
    # run of it: it reads the tables or says they changed while it read them, and never says
    # its sign-in cannot read them.
    with database(server, tmp_path) as target:
        target.script(target.sql(FORM))
        target.script("CREATE SCHEMA other")
        stop = threading.Event()
        churned: list[BaseException] = []

        def churn() -> None:
            made = 0
            try:
                while not stop.is_set():
                    made += 1
                    target.script(f"CREATE TABLE other.t{made} (a INTEGER PRIMARY KEY)")
                    target.script("ALTER TABLE policy ADD churned INTEGER NULL")
                    target.script("ALTER TABLE policy DROP COLUMN churned")
                    target.script(f"DROP TABLE other.t{made}")
            except BaseException as exc:  # noqa: BLE001 - said by the test below
                churned.append(exc)

        owners = threading.Thread(target=churn)
        owners.start()
        location = StoreLocation(HOSTED, target.url, sign_in=target.sign_in)
        pane_read = 0
        try:
            for _ in range(_BESIDE_DDL):
                engine, _ = open_engine(
                    target.url, FORM, environment=HOSTED, sign_in=target.sign_in
                )
                engine.dispose()
                try:
                    read = read_store_catalogue(location)
                except CatalogueMovedError:
                    continue
                assert read.table("policy") is not None
                pane_read += 1
        finally:
            stop.set()
            owners.join()

    assert not churned, churned
    assert pane_read > 0
