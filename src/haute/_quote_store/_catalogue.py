"""A database's tables as the quote store finds them (specs/workbench): its own catalogue read
into a :class:`Catalogue`, with, when counted, the quotes holding values in each table and
column, and the ways a table or column of it differs from the one the store derives from a
form, which the app's check (:func:`._store.open_engine`) and the schema pane's comparison
(:mod:`._compare`) both name.

A catalogue is read through SQLAlchemy's ``Inspector`` and holds names, kinds, what each type
holds, keys and counts alone, never a value: it is what the editor's comparison is made from,
sent to the browser and back, and holds nothing of where the database is or who signed in to
it. It holds facts, not verdicts: what a table should hold is the store's own :class:`Table`,
the one the SQL Haute writes is made from, which :func:`column_problems` and
:func:`table_problems` compare it with, every constraint of the store's table by what it
constrains.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from sqlalchemy import (
    CHAR,
    NCHAR,
    REAL,
    BigInteger,
    Boolean,
    Column,
    Connection,
    Date,
    DateTime,
    Dialect,
    Float,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    Select,
    SmallInteger,
    String,
    Subquery,
    Table,
    Unicode,
    UnicodeText,
    UniqueConstraint,
    case,
    func,
    inspect,
    literal_column,
    select,
    text,
    true,
)
from sqlalchemy.dialects.mssql import TINYINT
from sqlalchemy.engine import Inspector
from sqlalchemy.exc import CompileError, DBAPIError
from sqlalchemy.sql.elements import ColumnClause
from sqlalchemy.types import TypeDecorator, TypeEngine

from haute._workbench_config import WorkbenchError


class CatalogueMovedError(WorkbenchError):
    """The database's tables changed while they were read: a table or column the read took
    from the catalogue was gone, or another, by the time its quotes were counted, as DDL run
    beside the read changes them. Read again (:func:`._store.read_store` does)."""

    error_code = "quote_store_moved"


#: The columns every table of the store leads with: the quote's identity.
IDENTITY = ("quote_reference", "quote_option")
#: The most values one statement counting quotes reads, a table's quotes and each of its
#: columns', under the engines' limits on a select's columns: PostgreSQL's 1,664 the lowest.
COUNTED_AT_ONCE = 1000
#: The most tables one statement counting quotes joins, each table's aggregate one of the
#: join's: SQLite joins at most 64.
TABLES_AT_ONCE = 50
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
class Capacity:
    """What a column's type holds beyond its kind, on its engine (:func:`capacity_of`): an
    integer's or a Decimal's ``bits``; the most a text holds, ``length``, in its engine's
    units, None for any length; and whether a text holds ``every_character`` and keeps its
    value ``unpadded``, and a moment is held ``zoneless``, each False where the type holds
    less. A kind with no such measure takes None and True."""

    bits: int | None = None
    length: int | None = None
    every_character: bool = True
    unpadded: bool = True
    zoneless: bool = True


def on_engine(sql_type: TypeEngine[Any], dialect: Dialect) -> TypeEngine[Any]:
    """*sql_type*, one of the store's, as *dialect*'s engine holds it: a variant's type for
    that engine, a type decorator's implementation there."""
    held = sql_type.dialect_impl(dialect)
    return held.impl_instance if isinstance(held, TypeDecorator) else held


def capacity_of(sql_type: TypeEngine[Any], dialect: Dialect) -> Capacity:
    """What *sql_type*, as *dialect*'s engine holds it (one of the store's through
    :func:`on_engine`, or one reflected from the engine's catalogue), holds beyond its kind.
    SQLite keeps any integer in 64 bits, any real as a double and any text or moment as
    written, whatever its declared type; on SQL Server text that is not national holds its
    collation's code page alone."""
    if dialect.name == "sqlite":
        return Capacity(bits=64) if isinstance(sql_type, Integer | Float) else Capacity()
    if isinstance(sql_type, Integer):
        if isinstance(sql_type, BigInteger):
            return Capacity(bits=64)
        if isinstance(sql_type, SmallInteger):
            return Capacity(bits=16)
        return Capacity(bits=8 if isinstance(sql_type, TINYINT) else 32)
    if isinstance(sql_type, Float):
        precision = sql_type.precision
        single = isinstance(sql_type, REAL) or (precision is not None and precision <= 24)
        return Capacity(bits=32 if single else 64)
    if isinstance(sql_type, String):
        return Capacity(
            length=sql_type.length,
            every_character=dialect.name != "mssql" or isinstance(sql_type, Unicode | UnicodeText),
            unpadded=not isinstance(sql_type, CHAR | NCHAR),
        )
    if isinstance(sql_type, DateTime):
        return Capacity(zoneless=not sql_type.timezone)
    return Capacity()


@dataclass(frozen=True, slots=True)
class CatalogueColumn:
    """A column as the database holds it: its name, its kind (:func:`kind_of`), its type as
    the database names it, whether it takes a null, what its type holds beyond its kind,
    whether the database numbers it as each row is inserted, its collation where it has one,
    and the quotes holding a value in it, None when not counted."""

    name: str
    kind: str
    type: str
    nullable: bool
    capacity: Capacity = field(default_factory=Capacity)
    numbered: bool = False
    collation: str | None = None
    quotes: int | None = None


@dataclass(frozen=True, slots=True)
class CatalogueReference:
    """A foreign key as the database holds it: the ``columns`` it constrains, the ``table``
    it refers to and that table's columns, ``referred``, each paired in order with one of
    *columns*, and the referred table's ``sql_schema``, None for the one the catalogue was
    read from."""

    columns: tuple[str, ...]
    table: str
    referred: tuple[str, ...]
    sql_schema: str | None = None

    def pairs(self) -> frozenset[tuple[str, str]]:
        """Each constrained column with the column it refers to, whatever their order."""
        return frozenset(zip(self.columns, self.referred, strict=True))

    def describe(self) -> str:
        """Where it refers, as a message names it."""
        where = self.table if self.sql_schema is None else f"{self.sql_schema}.{self.table}"
        return f"{where} ({', '.join(self.referred)})"


@dataclass(frozen=True, slots=True)
class CatalogueTable:
    """A table as the database holds it: its columns in order, the columns of its primary key,
    each set of its columns a unique constraint or a unique index holds, its foreign keys, and
    the quotes holding a row in it, None when not counted."""

    name: str
    columns: tuple[CatalogueColumn, ...]
    primary_key: tuple[str, ...]
    unique: tuple[tuple[str, ...], ...] = ()
    references: tuple[CatalogueReference, ...] = ()
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
    dialect = connection.dialect
    # Every table's columns, keys, indexes and references read together, a statement or a few
    # for the schema on a server rather than several for each table, each a round trip across
    # the network; keyed by the schema as given, None for the connection's default.
    columns = inspector.get_multi_columns(schema=sql_schema, filter_names=reading)
    keys = inspector.get_multi_pk_constraint(schema=sql_schema, filter_names=reading)
    references = inspector.get_multi_foreign_keys(schema=sql_schema, filter_names=reading)
    indexes = inspector.get_multi_indexes(schema=sql_schema, filter_names=reading)
    # SQL Server's dialect reports a unique constraint as the unique index backing it alone.
    uniques = (
        {}
        if dialect.name == "mssql"
        else inspector.get_multi_unique_constraints(schema=sql_schema, filter_names=reading)
    )
    referred_schema = _ReferredSchema(connection, inspector, sql_schema)
    found: list[tuple[CatalogueTable, list[Any], set[str]]] = []
    countable: dict[str, list[str]] = {}
    for name in reading:
        key = (sql_schema, name)
        reflected = columns[key]
        primary_key = tuple(keys[key].get("constrained_columns") or ())
        numbered = (
            _sqlite_numbered(connection, sql_schema, name, primary_key)
            if dialect.name == "sqlite"
            else {column["name"] for column in reflected if column.get("identity")}
        )
        held_unique = [
            *(tuple(constraint["column_names"]) for constraint in uniques.get(key, [])),
            *(
                tuple(str(column) for column in index["column_names"])
                for index in indexes[key]
                if _unconditionally_unique(index)
            ),
        ]
        if count and set(IDENTITY) <= {column["name"] for column in reflected}:
            countable[name] = [column["name"] for column in reflected]
        table = CatalogueTable(
            name=name,
            columns=(),
            primary_key=primary_key,
            unique=_distinct(held_unique),
            references=tuple(
                CatalogueReference(
                    columns=tuple(foreign["constrained_columns"]),
                    table=foreign["referred_table"],
                    referred=tuple(foreign["referred_columns"]),
                    sql_schema=referred_schema.of(name, foreign),
                )
                for foreign in references[key]
            ),
        )
        found.append((table, reflected, numbered))
    try:
        counts = _counted(connection, list(countable.items()), sql_schema)
    except DBAPIError as exc:
        # Every name counted was the catalogue's a moment before, so a count that finds one
        # missing as it runs read a catalogue DDL beside it changed; any other failure is
        # the database's refusal, raised as it is.
        if not names_missing(exc):
            raise
        raise CatalogueMovedError(
            "The database's tables changed while they were read, as SQL run beside the read "
            "changes them: read them again once it has run."
        ) from None
    tables: list[CatalogueTable] = []
    for table, reflected, numbered in found:
        quotes, counted = counts[table.name] if table.name in countable else (None, {})
        tables.append(
            replace(
                table,
                columns=tuple(
                    CatalogueColumn(
                        name=column["name"],
                        kind=kind_of(column["type"]),
                        type=_type_name(column["type"], dialect),
                        nullable=bool(column["nullable"]),
                        capacity=capacity_of(column["type"], dialect),
                        numbered=column["name"] in numbered,
                        collation=getattr(column["type"], "collation", None),
                        quotes=counted.get(column["name"]),
                    )
                    for column in reflected
                ),
                quotes=quotes,
            )
        )
    return Catalogue(tables=tuple(tables))


#: What a missing table or column is called by the drivers that keep no SQLSTATE: SQL
#: Server's 42S02 and 42S22 as mssql-python names them, and SQLite's own words.
_MISSING_NAMED = frozenset({"Base table or view not found", "Column not found"})
_SQLITE_MISSING = ("no such table:", "no such column:")


def names_missing(exc: DBAPIError) -> bool:
    """Whether *exc* is a statement refused for naming a table or column the database does
    not hold: SQLSTATE 42P01 or 42703 on PostgreSQL (psycopg keeps it), SQL Server's 42S02 or
    42S22 (mssql-python keeps its name for it alone) and SQLite's ``no such table`` or ``no
    such column``. A refusal of any other kind, a permission among them, is not one, whatever
    the catalogue holds by the time it is looked at."""
    original = exc.orig
    if getattr(original, "sqlstate", None) in ("42P01", "42703"):
        return True
    if getattr(original, "driver_error", None) in _MISSING_NAMED:
        return True
    return isinstance(original, sqlite3.OperationalError) and str(original).startswith(
        _SQLITE_MISSING
    )


def _distinct(sets: Iterable[tuple[str, ...]]) -> tuple[tuple[str, ...], ...]:
    """*sets* of columns, each set once whatever its order: a unique constraint is reported
    again as the index backing it on PostgreSQL."""
    kept: list[tuple[str, ...]] = []
    for each in sets:
        if set(each) not in [set(held) for held in kept]:
            kept.append(each)
    return tuple(kept)


def _unconditionally_unique(index: Any) -> bool:
    """Whether the reflected *index* holds its columns unique in every row: unique, on columns
    rather than expressions, and filtered by no predicate (each dialect's ``<name>_where``),
    under which two rows outside it could hold the same."""
    return bool(index.get("unique")) and (
        None not in index["column_names"]
        and not any(
            option.endswith("_where") and value is not None
            for option, value in (index.get("dialect_options") or {}).items()
        )
    )


class _ReferredSchema:
    """The SQL schema each foreign key read from *sql_schema* refers to, None for the store's
    own: *sql_schema*, or, when it is None, wherever the connection finds a table by its bare
    name, as the store's every statement names its tables.

    A dialect leaves the referred schema unsaid where a bare name reaches the table: reading
    no schema, that is the store's own. Reading a named schema, SQL Server's and PostgreSQL's
    dialects name the schema read, and PostgreSQL's leaves unsaid another schema on its search
    path, which ``to_regclass`` finds the bare name in; any other unsaid schema is a fault.
    """

    def __init__(self, connection: Connection, inspector: Inspector, sql_schema: str | None):
        self._connection = connection
        self._sql_schema = sql_schema
        self._read = (
            inspector.default_schema_name
            if sql_schema is None
            else _spelled(connection, sql_schema)
        )
        self._on_path: dict[str, str | None] = {}

    def of(self, table: str, foreign: Any) -> str | None:
        """The schema *table*'s foreign key *foreign*, as the Inspector reflects it, refers to."""
        referred: str | None = foreign.get("referred_schema")
        if referred is None and self._sql_schema is None:
            return None
        if referred is None and self._connection.dialect.name == "postgresql":
            referred = self._found_by_path(foreign["referred_table"])
        if referred is None:
            raise ValueError(
                f"The catalogue does not say which SQL schema {table}'s foreign key refers to "
                f"{foreign['referred_table']} in."
            )
        return None if referred == self._read else referred

    def _found_by_path(self, name: str) -> str | None:
        """The schema PostgreSQL's search path finds the table *name* in by its bare name."""
        if name not in self._on_path:
            self._on_path[name] = self._connection.execute(
                text(
                    "SELECT n.nspname FROM pg_catalog.pg_class AS k "
                    "JOIN pg_catalog.pg_namespace AS n ON n.oid = k.relnamespace "
                    "WHERE k.oid = to_regclass(quote_ident(:name))"
                ),
                {"name": name},
            ).scalar()
        return self._on_path[name]


def _spelled(connection: Connection, sql_schema: str) -> str:
    """*sql_schema* as the database's catalogue spells it. SQL Server compares identifiers
    under the database's collation, so a schema named in another case may be the same one,
    which its catalogue, and so each reflected reference, spells its own way; PostgreSQL and
    SQLite keep a name as Haute quotes it."""
    if connection.dialect.name != "mssql":
        return sql_schema
    spelled = connection.execute(
        text("SELECT SCHEMA_NAME(SCHEMA_ID(:name))"), {"name": sql_schema}
    ).scalar()
    return sql_schema if spelled is None else str(spelled)


def _sqlite_numbered(
    connection: Connection, sql_schema: str | None, name: str, primary_key: tuple[str, ...]
) -> set[str]:
    """The column of the SQLite table *name* that SQLite numbers as a row is inserted: the
    primary key's sole column when it is declared ``INTEGER`` in a table with a rowid, which
    makes it the rowid. The catalogue reflects ``INT`` alike, which does not, so the
    declaration is read; and a key that is not the rowid (``WITHOUT ROWID``, or not declared
    ``INTEGER``) is held by an index of its own, which a rowid never is."""
    if len(primary_key) != 1:
        return set()
    quote = connection.dialect.identifier_preparer.quote
    where = "" if sql_schema is None else f"{quote(sql_schema)}."
    declared = {
        row[1]: str(row[2])
        for row in connection.exec_driver_sql(f"PRAGMA {where}table_xinfo({quote(name)})")
    }
    keyed_by_index = any(
        row[3] == "pk"
        for row in connection.exec_driver_sql(f"PRAGMA {where}index_list({quote(name)})")
    )
    rowid = declared.get(primary_key[0], "").upper() == "INTEGER" and not keyed_by_index
    return set(primary_key) if rowid else set()


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
    """*tables* in turn, as many to a batch as one statement may count: ``COUNTED_AT_ONCE``
    values and ``TABLES_AT_ONCE`` tables; a table wider than those values alone."""
    batches: list[list[tuple[str, Sequence[str]]]] = []
    values = 0
    for table in tables:
        width = 1 + sum(column not in IDENTITY for column in table[1])
        full = len(batches[-1]) >= TABLES_AT_ONCE if batches else True
        if full or values + width > COUNTED_AT_ONCE:
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


#: What a type holding less than the store's loses, by the measure of :class:`Capacity` it
#: falls short in, the bits by its kind: how a message says it.
_SHORT: dict[str, str] = {
    "integer": "holds smaller whole numbers than",
    "float": "keeps fewer digits of a number than",
    "length": "holds shorter text than",
    "every_character": "cannot hold every character",
    "unpadded": "pads its text with spaces, unlike",
    "zoneless": "reads a moment in the session's time zone, unlike",
}


def capacity_problems(found: Capacity, expected: Capacity) -> list[str]:
    """Each measure of :class:`Capacity` in which *found* holds less than *expected*, the
    store's, by name: fewer ``bits``, a shorter ``length`` (None holding any), and each of
    the others False where the store's is True. Holding more is no problem: every value the
    store writes is kept."""
    short: list[str] = []
    if expected.bits is not None and (found.bits is None or found.bits < expected.bits):
        short.append("bits")
    if found.length is not None and (expected.length is None or found.length < expected.length):
        short.append("length")
    short.extend(
        measure
        for measure in ("every_character", "unpadded", "zoneless")
        if getattr(expected, measure) and not getattr(found, measure)
    )
    return short


def column_problems(
    expected: Column[Any], found: CatalogueColumn, dialect: Dialect
) -> list[tuple[str, str]]:
    """Each way *found* differs from the store's *expected* column, as its kind (``type``,
    ``collation``, ``null`` or ``numbered``) and what follows the column's name in a
    message: of another kind, or of its kind holding less than the store's type on the
    engine (:func:`capacity_problems`); on an engine pinning one, compared under another
    collation; taking a null where the store needs a value, or none where it writes one;
    not numbered by the database where the store's column is."""
    problems: list[tuple[str, str]] = []
    held = on_engine(expected.type, dialect)
    kind = kind_of(expected.type)
    if found.kind != kind:
        problems.append(("type", f"holds {found.kind}, not {kind}"))
    else:
        wanted = _type_name(expected.type, dialect)
        problems.extend(
            ("type", f"is {found.type}, which {_SHORT[kind if each == 'bits' else each]} {wanted}")
            for each in capacity_problems(found.capacity, capacity_of(held, dialect))
        )
    collation = getattr(held, "collation", None)
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
    if expected.identity is not None and not found.numbered:
        problems.append(
            (
                "numbered",
                "is not numbered by the database as each row is inserted, as the store's is, "
                "so nothing could be recorded in it",
            )
        )
    return problems


def _named_columns(constraint: Any) -> tuple[str, ...]:
    return tuple(column.name for column in constraint.columns)


def _expected_reference(constraint: ForeignKeyConstraint) -> CatalogueReference:
    """The store's foreign key *constraint* as a catalogue holds it: its referred schema None
    where it refers within its own table's schema, as the store's every foreign key does."""
    referred = constraint.elements[0].column.table
    if not all(element.column.table is referred for element in constraint.elements):
        raise ValueError(f"The store's foreign key {constraint.name} refers to two tables.")
    return CatalogueReference(
        columns=tuple(element.parent.name for element in constraint.elements),
        table=referred.name,
        referred=tuple(element.column.name for element in constraint.elements),
        sql_schema=None if referred.schema == constraint.table.schema else referred.schema,
    )


def table_problems(expected: Table, found: CatalogueTable) -> list[tuple[str, str]]:
    """Each constraint of the store's *expected* table that *found* does not hold, by what it
    constrains whatever its name, as its kind (``key`` or ``refers``) and what follows the
    table's name in a message: its primary key, each set of its columns held unique by a
    unique constraint or a unique index, and each foreign key with the table, columns and
    SQL schema it refers to. A constraint of any other class is a fault, never passed; an
    index that is not unique decides how fast a read finds rows, never what is stored, and
    is no difference."""
    problems: list[tuple[str, str]] = []
    held_unique = [set(each) for each in found.unique]
    unique: list[tuple[str, ...]] = []
    references: list[CatalogueReference] = []
    for constraint in expected.constraints:
        if isinstance(constraint, PrimaryKeyConstraint):
            continue
        if isinstance(constraint, UniqueConstraint):
            unique.append(_named_columns(constraint))
        elif isinstance(constraint, ForeignKeyConstraint):
            references.append(_expected_reference(constraint))
        else:
            raise TypeError(
                f"The quote store's check compares no {type(constraint).__name__}, which "
                f"{expected.name} holds: compare it in _catalogue.table_problems."
            )
    unique.extend(_named_columns(index) for index in expected.indexes if index.unique)
    wanted = [column.name for column in expected.primary_key.columns]
    if list(found.primary_key) != wanted:
        problems.append(
            (
                "key",
                f"is keyed by {', '.join(found.primary_key) or 'nothing'}, not {', '.join(wanted)}",
            )
        )
    for columns in sorted(unique):
        if set(columns) not in held_unique:
            problems.append(
                (
                    "key",
                    f"does not hold {', '.join(columns)} unique, so two of its rows could hold "
                    "the same",
                )
            )
    for reference in sorted(references, key=lambda each: each.columns):
        if any(
            held.table == reference.table
            and held.sql_schema == reference.sql_schema
            and held.pairs() == reference.pairs()
            for held in found.references
        ):
            continue
        constrained = ", ".join(reference.columns)
        elsewhere = [
            held.describe()
            for held in found.references
            if set(held.columns) == set(reference.columns)
        ]
        problems.append(
            (
                "refers",
                f"does not refer by {constrained} to {reference.describe()}"
                + (f"; it refers by them to {', '.join(elsewhere)}" if elsewhere else ""),
            )
        )
    return problems
