"""The form compared with the tables a database holds (specs/workbench): the schema pane's
Compare.

:func:`compare` takes a :class:`._catalogue.Catalogue` read from an environment's database
and a form, and answers each way the database differs from what the store derives from the
form, by the form's table and column names (:class:`Difference`); each table of the store's
and each column of the form's tables the database holds and the form does not
(:class:`Extra`); and the plan (:mod:`._migrate`) that brings the tables to the form from what
is there. A table or column of the form is the database's of the same name; a column the
database lacks is a rename of one the database holds and the form does not where the analyst
paired them (:class:`Rename`). Each other way
a matched table or column differs (taking a null, its key, its reference, its collation, the
store's own columns) is a note the plan's SQL heads with, which no statement of it changes.

Nothing here connects to a database: the catalogue holds names, kinds and counts alone.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal, cast

from sqlalchemy import Column, Dialect, Engine, Table, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from haute._quote_store._catalogue import (
    IDENTITY,
    Catalogue,
    CatalogueColumn,
    CatalogueTable,
    column_problems,
    read_catalogue,
    table_problems,
)
from haute._quote_store._environment import StoreLocation
from haute._quote_store._migrate import UNCOUNTED, Key, Plan, held, plan
from haute._quote_store._providers import Provider
from haute._quote_store._schema import ACTIVITY_TABLE, StoreSchema, store_schema, structure
from haute._quote_store._store import (
    QuoteStoreEnvironmentError,
    QuoteStoreUnreachableError,
    make_engine,
    reaching,
)
from haute._workbench_form import QUOTE_DETAILS_NAME, FormSpec

#: Each catalogue kind a form's column type is held as.
_FORM_TYPES: Mapping[str, str] = {
    "integer": "int",
    "float": "float",
    "string": "str",
    "boolean": "bool",
    "date": "date",
}
#: What a database not reached may be, which an analyst reading one should know: a
#: database that pauses when idle, as Azure SQL Database's serverless tier does.
_WAKING = (
    "A database that pauses when idle refuses the first connection while it wakes: try again "
    "in a minute."
)
#: The id a table or column the form does not have is planned under.
_THE_DATABASE_S = "database:"


#: What a difference is: missing, renamed from another name, of another kind, a table of
#: other rows, taking a null otherwise, keyed or referring otherwise, under another collation,
#: or one of the store's own columns missing or otherwise.
DifferenceKind = Literal[
    "missing", "renamed", "type", "rows", "null", "key", "refers", "collation", "store"
]


@dataclass(frozen=True, slots=True)
class Difference:
    """A way the database differs from the form: the form's table and column it concerns by
    name (no table for Haute's own ``haute_activity``), its kind (``missing``, ``renamed``,
    ``type``, ``rows``, ``null``, ``key``, ``refers``, ``collation`` or ``store``) and what it
    is, in words."""

    table: str | None
    column: str | None
    kind: DifferenceKind
    message: str


@dataclass(frozen=True, slots=True)
class Extra:
    """A table of the store's, or a column of one of the form's tables, that the database holds
    and the form does not: its table's and its name in the database, the form's table's name
    when the table is the form's, and the quotes holding values in it, None when not counted."""

    table: str
    column: str | None
    form_table: str | None
    quotes: int | None


@dataclass(frozen=True, slots=True)
class Rename:
    """A column of the form's table *table*, *column*, both by name, that the database holds
    as *was*, the analyst's pairing of the two."""

    table: str
    column: str
    was: str


@dataclass(frozen=True, slots=True)
class Comparison:
    """What :func:`compare` found: the differences, the extras, the renames the analyst paired
    that it took, and the plan that brings the tables to the form."""

    differences: tuple[Difference, ...]
    extras: tuple[Extra, ...]
    renames: tuple[Rename, ...]
    plan: Plan

    @property
    def matches(self) -> bool:
        """Whether the database holds the form's tables as the store needs them."""
        return not self.differences


def _live_column(column: CatalogueColumn, column_key: str) -> dict[str, Any]:
    """*column* as a plan's structure holds it, under *column_key*: its type the form's that
    its kind is held as, else its kind, and the type the database names it by."""
    return {
        "key": column_key,
        "name": column.name,
        "type": _FORM_TYPES.get(column.kind, column.kind),
        "index": False,
        "sql_type": column.type,
    }


class _Matcher:
    """The comparison's working state: the differences, extras and notes as they are found,
    the database's tables as a plan's structure and the quotes counted in each."""

    def __init__(self, catalogue: Catalogue, schema: StoreSchema, dialect: Dialect) -> None:
        self.catalogue = catalogue
        self.schema = schema
        self.dialect = dialect
        self.differences: list[Difference] = []
        self.extras: list[Extra] = []
        self.notes: list[str] = []
        self.tables: list[dict[str, Any]] = []
        self.counts: dict[Key, int] | None = {}

    def count(self, key: Key, quotes: int | None) -> None:
        if self.counts is None:
            return
        if quotes is None:
            # A catalogue read without its counts plans with none.
            self.counts = None
            return
        self.counts[key] = quotes

    def differ(
        self, table: str | None, column: str | None, kind: DifferenceKind, message: str
    ) -> None:
        self.differences.append(Difference(table, column, kind, message))

    def note(self, table: str | None, column: str | None, kind: str, message: str) -> None:
        """A difference no statement of the plan changes, of a kind
        :func:`._catalogue.column_problems` or :func:`._catalogue.table_problems` names:
        marked and noted in its SQL."""
        self.differ(table, column, cast(DifferenceKind, kind), message)
        self.notes.append(message)

    def column_checks(
        self,
        table_key: str | None,
        column_key: str | None,
        expected: Column[Any],
        found: CatalogueColumn,
        where: str,
        *,
        quotes: int | None = None,
    ) -> None:
        """The ways *found* differs from *expected* beyond its type, which a plan's retype
        changes: each a note."""
        counted = found.quotes if quotes is None else quotes
        for kind, said in column_problems(expected, found, self.dialect):
            if kind != "type":
                self.note(table_key, column_key, kind, f"{where}{held(counted)} {said}")

    def own_columns(
        self, table_key: str | None, expected: Table, found: CatalogueTable, skip: set[str]
    ) -> None:
        """The columns of *expected* the store keeps that the form does not (its own, the
        identity of ``haute_activity``), each missing, of another kind or nullable otherwise
        a note on its field (on the table for activity, which has no schema field)."""
        for column in expected.columns:
            if column.name in skip:
                continue
            held = found.column(column.name)
            where = f"{expected.name}.{column.name}"
            column_key = column.name if table_key is not None else None
            if held is None:
                self.note(
                    table_key, column_key, "store", f"{where}, the store's own column, is missing"
                )
                continue
            for _, said in column_problems(column, held, self.dialect):
                self.note(
                    table_key, column_key, "store", f"{where}, the store's own column, {said}"
                )

    def not_null_extras(
        self, table_key: str | None, found: CatalogueTable, known: set[str]
    ) -> None:
        for column in found.columns:
            if column.name not in known and not column.nullable:
                self.note(
                    table_key,
                    None,
                    "null",
                    f"{found.name}.{column.name} is no column of the form's and takes no null, "
                    "so no row of it could be saved",
                )


def _reading_engine(location: StoreLocation) -> Engine:
    """An engine that reads the database at *location* and writes nothing: a SQLite file
    opened read only, never made where it is missing nor its journal changed, as the store's
    own engine would; a server's signed in as the location says."""
    url = make_url(location.url)
    if url.get_backend_name() != "sqlite":
        return make_engine(location.url, sign_in=location.sign_in)
    reading = Path(str(url.database)).resolve().as_uri() + "?mode=ro"
    return create_engine("sqlite://", creator=lambda: sqlite3.connect(reading, uri=True))


def read_store_catalogue(location: StoreLocation) -> Catalogue:
    """The store's tables the database at *location* holds, with the quotes counted in each,
    read under its sign-in and nothing written. A server not reached is said naming what places
    it (:func:`._store.reaching`), and any other refusal of the database's, a file that is no
    database or tables its sign-in may not read, naming the same, never what the driver
    said, a database not reached saying it may be waking from a pause (_WAKING)."""
    engine = _reading_engine(location)
    try:
        with reaching(location.url, location.environment, location.named_by):
            with engine.connect() as connection:
                return read_catalogue(connection, location.sql_schema, count=True)
    except QuoteStoreUnreachableError as exc:
        raise QuoteStoreUnreachableError(f"{exc} {_WAKING}") from None
    except DBAPIError:
        raise QuoteStoreEnvironmentError(
            f"The {location.environment} database's tables could not be read: check "
            f"{location.named_by}, and that the database lets its sign-in read them."
        ) from None
    finally:
        engine.dispose()


def _dialect(name: str) -> Dialect:
    """The SQLAlchemy dialect *name*, with no driver loaded: what a type's collation is read
    from."""
    dialect: Dialect = make_url(f"{name}://").get_dialect()()
    return dialect


def compare(
    form: FormSpec,
    catalogue: Catalogue,
    *,
    provider: Provider,
    renames: Sequence[Rename] = (),
) -> Comparison:
    """*form*, holding ``quote_details``, compared with the database *catalogue* was read from,
    on *provider*'s engine: its differences, its extras and the plan from what it holds to the
    form, a column the form has that the database lacks taken for a rename of one the database
    holds and the form does not where *renames* pairs them. A table is the database's of its
    name: one renamed in the form is one the database lacks beside one it keeps (READ-23)."""
    wanted = structure(form)
    schema = store_schema(form)
    matcher = _Matcher(catalogue, schema, _dialect(provider.dialect))
    claimed: set[str] = set()
    taken: list[Rename] = []
    for table in wanted["tables"]:
        found = catalogue.table(table["name"])
        if found is None:
            matcher.differ(table["key"], None, "missing", f"the database has no {table['name']}")
            continue
        claimed.add(found.name)
        taken.extend(_match_table(matcher, table, found, renames))
    for found in catalogue.tables:
        if (
            found.name in claimed
            or found.name == ACTIVITY_TABLE
            or not set(IDENTITY) <= {column.name for column in found.columns}
        ):
            continue
        key = f"{_THE_DATABASE_S}{found.name}"
        matcher.extras.append(Extra(found.name, None, None, found.quotes))
        matcher.tables.append({"key": key, "name": found.name, "rows": "one", "columns": []})
        matcher.count((key, None), found.quotes)
    activity = catalogue.table(ACTIVITY_TABLE)
    if activity is None:
        matcher.differ(None, None, "missing", f"the database has no {ACTIVITY_TABLE}")
    else:
        matcher.own_columns(None, schema.activity, activity, set())
        known = {column.name for column in schema.activity.columns}
        matcher.not_null_extras(None, activity, known)
        for _, said in table_problems(schema.activity, activity, refers=False):
            matcher.note(None, None, "key", f"{ACTIVITY_TABLE} {said}")
    made = plan(wanted, {"tables": matcher.tables}, matcher.counts, provider=provider)
    made = replace(made, activity=activity is None, notes=tuple(matcher.notes))
    for step in made.steps:
        # A retyped index is marked with its column's type, as any column's is.
        if step.kind == "reshape table" and step.column_key is None:
            rows = next(
                table["rows"] for table in wanted["tables"] if table["key"] == step.table_key
            )
            matcher.differ(
                step.table_key,
                None,
                "rows",
                f"the database holds {'one row' if rows == 'many' else 'many rows'} per quote"
                + held(step.quotes, "rows"),
            )
    return Comparison(
        differences=tuple(matcher.differences),
        extras=tuple(matcher.extras),
        renames=tuple(taken),
        plan=made,
    )


def _match_table(
    matcher: _Matcher,
    table: dict[str, Any],
    found: CatalogueTable,
    renames: Sequence[Rename],
) -> list[Rename]:
    """*table*, of the form's structure, matched with *found*: each of its columns with the
    database's of its name, or one the analyst paired; the database's table added to the
    plan's structure, each column the form does not have an extra; each difference found.
    Answers the pairings taken."""
    stored = matcher.schema.by_name(table["key"])
    expected = stored.table
    form_columns = {column["name"] for column in table["columns"]}
    claimed: set[str] = set()
    matched: dict[str, tuple[CatalogueColumn, str | None]] = {}
    # A many-row table's index is the database's key column after the identity, whatever
    # either is named: paired first, without the analyst, so renaming the index renames that
    # column and never adds one beside it, and no other column of either takes it by name.
    index = next((column for column in table["columns"] if column["index"]), None)
    keyed = [name for name in found.primary_key if name not in IDENTITY]
    key_column = found.column(keyed[0]) if index is not None and len(keyed) == 1 else None
    if index is not None and key_column is not None:
        renamed_from = None if key_column.name == index["name"] else key_column.name
        matched[index["key"]] = (key_column, renamed_from)
        claimed.add(key_column.name)
    for column in table["columns"]:
        found_column = found.column(column["name"])
        if (
            column["key"] not in matched
            and found_column is not None
            and found_column.name not in claimed
        ):
            matched[column["key"]] = (found_column, None)
            claimed.add(found_column.name)
    taken: list[Rename] = []
    paired = {each.column: each for each in renames if each.table == table["key"]}
    for column in table["columns"]:
        pairing = paired.get(column["key"])
        if column["key"] in matched or pairing is None:
            continue
        held_as = pairing.was
        found_column = found.column(held_as)
        if (
            found_column is None
            or held_as in form_columns
            or held_as in claimed
            or held_as in IDENTITY
        ):
            continue
        matched[column["key"]] = (found_column, held_as)
        claimed.add(held_as)
        taken.append(pairing)
    # The store's own columns are no extras.
    own = {column.name for column in expected.columns} - form_columns
    uncounted = not set(IDENTITY) <= {column.name for column in found.columns}
    live: list[dict[str, Any]] = []
    for column in table["columns"]:
        if column["key"] not in matched:
            matcher.differ(
                table["key"], column["key"], "missing", f"the database has no {column['name']}"
            )
            continue
        found_column, was = matched[column["key"]]
        quotes = UNCOUNTED if uncounted else found_column.quotes
        live.append(_live_column(found_column, column["key"]))
        matcher.count((table["key"], column["key"]), quotes)
        if was is not None:
            matcher.differ(table["key"], column["key"], "renamed", f"the database names it {was}")
        wanted_column = expected.c[column["name"]]
        if _FORM_TYPES.get(found_column.kind, found_column.kind) != column["type"]:
            new = wanted_column.type.compile(dialect=matcher.dialect)
            matcher.differ(
                table["key"],
                column["key"],
                "type",
                f"the database holds it as {found_column.type}, not {new}" + held(quotes),
            )
        matcher.column_checks(
            table["key"],
            column["key"],
            wanted_column,
            found_column,
            f"{found.name}.{found_column.name}",
            quotes=quotes,
        )
    # A table keyed by the identity alone is one row per quote, by one more column many.
    many = len(keyed) > 0
    rows_differ = many != (table["rows"] == "many")
    for found_column in found.columns:
        if found_column.name in claimed or found_column.name in own:
            continue
        key = f"{_THE_DATABASE_S}{found_column.name}"
        matcher.extras.append(
            Extra(found.name, found_column.name, table["key"], found_column.quotes)
        )
        live.append(_live_column(found_column, key))
        matcher.count((table["key"], key), UNCOUNTED if uncounted else found_column.quotes)
    matcher.own_columns(table["key"], expected, found, form_columns)
    matcher.not_null_extras(table["key"], found, claimed | own)
    # The key compared under the form's names: a column paired as renamed keys as its new name.
    renamed = {
        known_as: column["name"]
        for column in table["columns"]
        if column["key"] in matched and (known_as := matched[column["key"]][1]) is not None
    }
    as_named = replace(
        found, primary_key=tuple(renamed.get(name, name) for name in found.primary_key)
    )
    for kind, said in table_problems(expected, as_named, refers=table["key"] != QUOTE_DETAILS_NAME):
        if not rows_differ:
            matcher.note(table["key"], None, kind, f"{found.name} {said}")
    matcher.tables.append(
        {
            "key": table["key"],
            "name": found.name,
            "rows": "many" if many else "one",
            "columns": live,
        }
    )
    matcher.count((table["key"], None), UNCOUNTED if uncounted else found.quotes)
    return taken
