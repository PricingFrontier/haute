"""A database's tables as the quote store finds them (specs/workbench): its own catalogue read
into a :class:`Catalogue`, with, when counted, the quotes holding values in each table and
column, and the ways a table or column of it differs from the one the store derives from a
form, which the app's check (:func:`._store.open_engine`) and the schema pane's comparison
(:mod:`._compare`) both name.

A catalogue is read through SQLAlchemy's ``Inspector`` and holds names, kinds and counts
alone, never a value: it is what the editor's comparison is made from, sent to the browser
and back, and holds nothing of where the database is or who signed in to it.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    Connection,
    Date,
    DateTime,
    Dialect,
    Float,
    Integer,
    MetaData,
    Select,
    String,
    Subquery,
    Table,
    case,
    func,
    inspect,
    literal_column,
    select,
    true,
)
from sqlalchemy.exc import CompileError
from sqlalchemy.sql.elements import ColumnClause
from sqlalchemy.types import TypeDecorator, TypeEngine

from haute._workbench_form import QUOTE_DETAILS_ID

#: The columns every table of the store leads with: the quote's identity.
IDENTITY = ("quote_reference", "quote_option")
#: The most values one statement counting quotes reads, a table's quotes and each of its
#: columns', under the engines' limits on a select's columns: PostgreSQL's 1,664 the lowest.
COUNTED_AT_ONCE = 1000
#: The kinds a column's SQL type is told apart by, the first one it is wins: what the store
#: reads and writes it as, whatever each engine names it.
_KINDS: tuple[type[TypeEngine[Any]], ...] = (Boolean, Integer, Float, DateTime, Date, String)


def kind_of(sql_type: TypeEngine[Any]) -> str:
    """The kind of *sql_type*, a type decorator's its implementation's."""
    if isinstance(sql_type, TypeDecorator):
        sql_type = sql_type.impl_instance
    for kind in _KINDS:
        if isinstance(sql_type, kind):
            return kind.__name__.lower()
    return type(sql_type).__name__.lower()


@dataclass(frozen=True, slots=True)
class CatalogueColumn:
    """A column as the database holds it: its name, its kind (:func:`kind_of`), its type as
    the database names it, whether it takes a null, its collation where it has one, and the
    quotes holding a value in it, None when not counted."""

    name: str
    kind: str
    type: str
    nullable: bool
    collation: str | None = None
    quotes: int | None = None


@dataclass(frozen=True, slots=True)
class CatalogueTable:
    """A table as the database holds it: its columns in order, the columns of its primary key,
    whether it refers by the identity to its quote's ``quote_details`` row, and the quotes
    holding a row in it, None when not counted."""

    name: str
    columns: tuple[CatalogueColumn, ...]
    primary_key: tuple[str, ...]
    refers: bool
    quotes: int | None = None

    def column(self, name: str) -> CatalogueColumn | None:
        return next((column for column in self.columns if column.name == name), None)


@dataclass(frozen=True, slots=True)
class Catalogue:
    """The tables of a database's SQL schema the store reads."""

    tables: tuple[CatalogueTable, ...]

    def table(self, name: str) -> CatalogueTable | None:
        return next((table for table in self.tables if table.name == name), None)


def _type_name(sql_type: TypeEngine[Any], dialect: Dialect) -> str:
    """*sql_type* as *dialect* names it; a type it cannot render (one SQLAlchemy reflected
    as unknown) by its kind."""
    try:
        return str(sql_type.compile(dialect=dialect))
    except CompileError:
        return kind_of(sql_type).upper()


def read_catalogue(
    connection: Connection,
    sql_schema: str | None = None,
    names: Iterable[str] | None = None,
    *,
    count: bool = False,
) -> Catalogue:
    """The tables of the SQL schema *sql_schema* (the connection's default when None) on
    *connection*: those named in *names* that it holds, or, without *names*, all it holds, so
    an incomplete table of the form's is compared rather than taken for missing; with *count*,
    the quotes holding a row in each table holding both of the quote's identity columns and a
    value in each of its columns, None where a table lacks either identity column and cannot
    be counted by quote."""
    inspector = inspect(connection)
    present = inspector.get_table_names(schema=sql_schema)
    wanted = None if names is None else set(names)
    reading = [name for name in present if wanted is None or name in wanted]
    if not reading:
        # The multi-table reads take no names to mean every table.
        return Catalogue(tables=())
    # Every table's columns, key and references read together, a statement or a few for the
    # schema on a server rather than several for each table, each a round trip across the
    # network; keyed by the schema as given, None for the connection's default.
    columns = inspector.get_multi_columns(schema=sql_schema, filter_names=reading)
    keys = inspector.get_multi_pk_constraint(schema=sql_schema, filter_names=reading)
    references = inspector.get_multi_foreign_keys(schema=sql_schema, filter_names=reading)
    found: list[tuple[str, list[Any], Any, bool]] = []
    countable: dict[str, list[str]] = {}
    for name in reading:
        reflected = columns[(sql_schema, name)]
        held = {column["name"] for column in reflected}
        keyed = keys[(sql_schema, name)]
        refers = any(
            foreign.get("referred_table") == QUOTE_DETAILS_ID
            and list(foreign.get("constrained_columns") or []) == list(IDENTITY)
            for foreign in references[(sql_schema, name)]
        )
        if count and set(IDENTITY) <= held:
            countable[name] = [column["name"] for column in reflected]
        found.append((name, reflected, keyed, refers))
    counts = _counted(connection, list(countable.items()), sql_schema)
    tables: list[CatalogueTable] = []
    for name, reflected, keyed, refers in found:
        quotes, counted = counts[name] if name in countable else (None, {})
        tables.append(
            CatalogueTable(
                name=name,
                columns=tuple(
                    CatalogueColumn(
                        name=column["name"],
                        kind=kind_of(column["type"]),
                        type=_type_name(column["type"], connection.dialect),
                        nullable=bool(column["nullable"]),
                        collation=getattr(column["type"], "collation", None),
                        quotes=counted.get(column["name"]),
                    )
                    for column in reflected
                ),
                primary_key=tuple(keyed.get("constrained_columns") or ()),
                refers=refers,
                quotes=quotes,
            )
        )
    return Catalogue(tables=tuple(tables))


def _quote_counts(
    index: int, name: str, columns: Sequence[str], sql_schema: str | None
) -> Subquery:
    """The table *name*'s quotes, and those holding a value in each of its *columns* past the
    identity, as the one row an aggregate makes, labelled ``t<index>_q`` and
    ``t<index>_c<column's place>``: quotes, not rows, in a many-row table, every quote's in
    the identity's. Its numbers are written into its SQL, never bound, as one statement counts
    many tables' columns and SQL Server binds at most 2,100 values to one."""
    held = Table(name, MetaData(schema=sql_schema), *(Column(column) for column in columns))
    counted = [column for column in columns if column not in IDENTITY]
    identity = [held.c[column] for column in IDENTITY]
    one: ColumnClause[int] = literal_column("1")
    none: ColumnClause[int] = literal_column("0")
    per_quote = (
        select(
            *identity,
            *(
                func.max(case((held.c[column].is_not(None), one), else_=none)).label(f"c{at}")
                for at, column in enumerate(counted)
            ),
        )
        .group_by(*identity)
        .subquery()
    )
    return (
        select(
            func.count().label(f"t{index}_q"),
            *(
                func.coalesce(func.sum(per_quote.c[f"c{at}"]), none).label(f"t{index}_c{at}")
                for at in range(len(counted))
            ),
        )
        .select_from(per_quote)
        .subquery()
    )


def counting_statement(
    tables: Sequence[tuple[str, Sequence[str]]], sql_schema: str | None
) -> Select[Any]:
    """One statement counting the quotes in each of *tables*, each its name and its columns,
    as :func:`_quote_counts` labels them: the aggregates joined on nothing, each one row, so
    the statement answers one row holding every table's counts."""
    aggregates = [
        _quote_counts(index, name, columns, sql_schema)
        for index, (name, columns) in enumerate(tables)
    ]
    statement = select(*(column for aggregate in aggregates for column in aggregate.c))
    statement = statement.select_from(aggregates[0])
    for aggregate in aggregates[1:]:
        statement = statement.join(aggregate, true())
    return statement


def _batches(
    tables: Sequence[tuple[str, Sequence[str]]],
) -> list[list[tuple[str, Sequence[str]]]]:
    """*tables* in turn, as many to a batch as one statement may count, ``COUNTED_AT_ONCE``
    values; a table wider than that alone."""
    batches: list[list[tuple[str, Sequence[str]]]] = []
    values = 0
    for table in tables:
        width = 1 + sum(column not in IDENTITY for column in table[1])
        if not batches or values + width > COUNTED_AT_ONCE:
            batches.append([])
            values = 0
        batches[-1].append(table)
        values += width
    return batches


def _counted(
    connection: Connection,
    tables: Sequence[tuple[str, Sequence[str]]],
    sql_schema: str | None,
) -> dict[str, tuple[int, dict[str, int]]]:
    """For each of *tables*, its name and its columns, the quotes holding a row in it and a
    value in each of its columns, the identity's every quote's: the tables counted together,
    a statement for as many as one may count, each statement a round trip to the server."""
    counts: dict[str, tuple[int, dict[str, int]]] = {}
    for batch in _batches(tables):
        row = connection.execute(counting_statement(batch, sql_schema)).one()._mapping
        for index, (name, columns) in enumerate(batch):
            counted = [column for column in columns if column not in IDENTITY]
            quotes = int(row[f"t{index}_q"])
            found = {column: int(row[f"t{index}_c{at}"]) for at, column in enumerate(counted)}
            counts[name] = (quotes, {**dict.fromkeys(IDENTITY, quotes), **found})
    return counts


def column_problems(
    expected: Column[Any], found: CatalogueColumn, dialect: Dialect
) -> list[tuple[str, str]]:
    """Each way *found* differs from the store's *expected* column, as its kind (``type``,
    ``collation`` or ``null``) and what follows the column's name in a message: of another
    kind; on an engine pinning one, compared under another collation; taking a null where the
    store needs a value, or none where it writes one."""
    problems: list[tuple[str, str]] = []
    if found.kind != kind_of(expected.type):
        problems.append(("type", f"holds {found.kind}, not {kind_of(expected.type)}"))
    collation = getattr(expected.type.dialect_impl(dialect), "collation", None)
    if collation and found.collation != collation:
        problems.append(
            (
                "collation",
                f"compares under {found.collation or 'another collation'}, not {collation}, "
                "so two quotes could be taken for one",
            )
        )
    if found.nullable != bool(expected.nullable):
        takes = "takes a null" if found.nullable else "takes no null"
        problems.append(("null", f"{takes}, unlike the store's"))
    return problems


def table_problems(expected: Table, found: CatalogueTable, refers: bool) -> list[tuple[str, str]]:
    """Each way *found* differs from the store's *expected* table as a whole, as its kind
    (``key`` or ``refers``) and what follows the table's name in a message: keyed otherwise,
    or, where it should (*refers*), not referring to its quote's row."""
    problems: list[tuple[str, str]] = []
    wanted = [column.name for column in expected.primary_key.columns]
    if list(found.primary_key) != wanted:
        problems.append(
            (
                "key",
                f"is keyed by {', '.join(found.primary_key) or 'nothing'}, not {', '.join(wanted)}",
            )
        )
    if refers and not found.refers:
        problems.append(("refers", f"does not refer to its quote's {QUOTE_DETAILS_ID} row"))
    return problems
