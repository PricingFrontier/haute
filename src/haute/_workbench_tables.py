"""The workbench's tables as the pipeline holds them (specs/workbench).

Each schema table's inputs are a table of the Workbench Input's, its input side, and its
outputs a table of the Workbench Output's, its output side, given to each node's ``tables``
config from the form wherever the pipeline runs (``haute._workbench_nodes``): a side is its
table's name, its side and its port (``<name>_input`` or ``<name>_output``), whether a quote
holds one row of it or many, and its typed columns, with the index that keys a many-row
side's rows and puts each output row on its input row. The form's rules and labels stay in
the form. In a quote, and in a priced
quote, a one-row table is an object under its table's name and a many-row table a list of
objects, ``{"policy_details": {...}, "equipment": [{...}, ...]}``: a request holds the
inputs and a response the outputs. A block with no column of its own is not a port: there
is nothing to read into or to fill. Every table but ``quote_details`` leads with the quote's
identity, its ``reference`` and ``option``: a request gives them in ``quote_details`` alone
(:func:`request_columns`), and the frames and the response carry them on every row.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any, Literal

import polars as pl
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from haute._graph_utils import is_frame_label
from haute._json_safe import to_json_safe
from haute._output_assembler import prune_document
from haute._workbench_form import (
    IDENTITY_COLUMNS,
    QUOTE_DETAILS_NAME,
    QUOTE_DETAILS_STORE_ONLY,
    ColumnType,
    IndexKind,
    holds_identity,
    index_text_limit,
)
from haute.errors import HauteError

if TYPE_CHECKING:
    from haute._workbench_form import FormSpec, SchemaColumn, SchemaTable

#: Each column type's dtype in a frame. ``tests/test_workbench_tables.py`` holds it equal to
#: the Quote Input's, so a step reads either input's columns alike.
COLUMN_DTYPES: dict[ColumnType, pl.DataType] = {
    "int": pl.Int64(),
    "float": pl.Float64(),
    "str": pl.String(),
    "bool": pl.Boolean(),
    "date": pl.Date(),
}

_NAME_RULE = "letters, digits and underscores, not starting with a digit and not a Python keyword"

#: The longest table or column name the quote store keeps: PostgreSQL's limit for an
#: identifier, so no database shortens one and two long names never become one.
STORE_NAME_LENGTH = 63
#: What starts a name the quote store keeps for its own tables and columns.
_STORE_PREFIX = "haute_"
#: What starts a table name SQLite keeps for its own tables.
_SQLITE_PREFIX = "sqlite_"
#: Haute's own columns whose names start as the store's do, by table and column name.
_HAUTE_COLUMNS = frozenset({(QUOTE_DETAILS_NAME, "haute_version")})


def store_name_problems(table: str, columns: Sequence[str]) -> list[str]:
    """What keeps *table*'s names from being the quote store's (specs/workbench), each said as
    ``table 'x' ...``: a name longer than ``STORE_NAME_LENGTH``, a name starting ``haute_``
    (Haute's own ``quote_details.haute_version`` aside), a table starting ``sqlite_`` and two
    columns whose names differ only in case, which no database holds both of."""
    problems: list[str] = []
    if len(table) > STORE_NAME_LENGTH:
        problems.append(
            f"table {table!r} has a name of {len(table)} characters: a name is at most "
            f"{STORE_NAME_LENGTH}, the longest every database keeps whole"
        )
    if table.startswith(_STORE_PREFIX):
        problems.append(
            f"table {table!r} starts {_STORE_PREFIX}, which the quote store keeps for its own names"
        )
    if table.startswith(_SQLITE_PREFIX):
        problems.append(
            f"table {table!r} starts {_SQLITE_PREFIX}, which SQLite keeps for its own tables"
        )
    folded: dict[str, str] = {}
    for name in columns:
        earlier = folded.setdefault(name.casefold(), name)
        if earlier != name:
            problems.append(
                f"table {table!r} has columns {earlier!r} and {name!r}, which differ only in "
                "case, so a database cannot hold both"
            )
        if len(name) > STORE_NAME_LENGTH:
            problems.append(
                f"table {table!r} has a column {name!r} of {len(name)} characters: a name is at "
                f"most {STORE_NAME_LENGTH}, the longest every database keeps whole"
            )
        if name.startswith(_STORE_PREFIX) and (table, name) not in _HAUTE_COLUMNS:
            problems.append(
                f"table {table!r} has a column {name!r}, and a name starting {_STORE_PREFIX} is "
                "the quote store's own"
            )
    return problems


class WorkbenchTablesError(HauteError):
    """A workbench node's tables cannot be the pipeline's: the form's, or a bundle's.

    A public contract error: the message says what to fix, and a route answers it as 422.
    """

    error_code = "workbench_tables_invalid"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WorkbenchColumn(_Strict):
    """A column of a table: its name in the frame and its type."""

    name: str
    type: ColumnType

    @property
    def dtype(self) -> pl.DataType:
        return COLUMN_DTYPES[self.type]


#: An index's value as JSON holds it: an Integer's number or a Text's text.
KeyValue = str | int

#: Which block of its table a node's table is: the inputs a Workbench Input reads, or the
#: outputs a Workbench Output fills.
Side = Literal["input", "output"]


class WorkbenchTable(_Strict):
    """A side of a schema table: its inputs or its outputs, a port of the node, one row per
    quote or many, its columns, and, for a many-row table, its index, which keys its rows."""

    #: The schema table's name, under which a quote holds it.
    name: str
    side: Side
    #: The port it is read into or filled through, ``<name>_<side>``.
    port: str
    rows: Literal["one", "many"]
    columns: list[WorkbenchColumn] = Field(default_factory=list)
    #: On every side of a many-row table, its index's name: the column after the quote's
    #: identity, an Integer or Text, that keys its rows. None for one row.
    key: str | None = None
    #: On the input side of a many-row table, the value the null quote's row holds in its
    #: index (:func:`null_index`).
    null_key: KeyValue | None = None
    #: On the output side of a many-row table of outputs alone, how its index gets its
    #: values: ``numbered``, the Workbench Output numbering the rows 1, 2, 3 in the order
    #: pricing gives them whatever the frame holds under its name; ``typed``, the pipeline's,
    #: each row its own; ``values``, the pipeline's, each one of :attr:`index_values`. None on
    #: every other side, whose rows the quote's input rows key.
    index_kind: IndexKind | None = None
    #: With ``index_kind`` ``values``, the index's values in their order, typed as it.
    index_values: list[KeyValue] = Field(default_factory=list)

    @model_validator(mode="after")
    def _holds_its_port_and_key(self) -> WorkbenchTable:
        if self.port != f"{self.name}_{self.side}":
            raise ValueError(f"{self.name}'s {self.side} side is the port {self.name}_{self.side}")
        if self.one_row:
            if self.key is not None or self.null_key is not None or self.index_kind is not None:
                raise ValueError(f"{self.port} has one row, so it has no index")
        elif not any(
            column.name == self.key and column.type in ("int", "str") for column in self.columns
        ):
            raise ValueError(
                f"{self.port} has many rows, so its key names its index, an Integer or Text "
                "column it holds"
            )
        if self.null_key is not None and self.side != "input":
            raise ValueError(f"{self.port} holds a null key, which only an input side does")
        if self.index_kind is not None and self.side != "output":
            raise ValueError(
                f"{self.port} says how its index gets its values, which only the output side "
                "of a table of outputs alone does"
            )
        if bool(self.index_values) != (self.index_kind == "values"):
            raise ValueError(
                f"{self.port} holds its index's values exactly when its index gives a row for "
                "each value"
            )
        return self

    @property
    def one_row(self) -> bool:
        return self.rows == "one"

    @property
    def frame_schema(self) -> pl.Schema:
        """The frame's schema: each column with its declared dtype."""
        return pl.Schema({column.name: column.dtype for column in self.columns})

    @property
    def quote_dtype(self) -> pl.DataType:
        """How a quote holds the table: an object of its columns, or a list of them."""
        struct = pl.Struct(dict(self.frame_schema))
        return struct if self.one_row else pl.List(struct)


_TABLES = TypeAdapter(list[WorkbenchTable])


def parse_workbench_tables(value: object, *, owner: str) -> tuple[WorkbenchTable, ...]:
    """*value*, a workbench node's ``tables`` config, as the tables it holds.

    *owner* names the node in a refusal. Haute gives the tables (``haute._workbench_nodes``),
    so a shape it does not give, a node built without them included, is refused naming the
    spot, and the names are checked as :func:`check_tables` checks them.
    """
    try:
        tables = tuple(_TABLES.validate_python(value, strict=True))
    except ValidationError as exc:
        # The first error is enough to find the problem; the rest usually follow it.
        first = exc.errors()[0]
        where = "tables" + "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}" for part in first["loc"]
        )
        raise WorkbenchTablesError(
            f"The {owner}'s tables are not as the workbench gives them ({where}: {first['msg']})."
        ) from exc
    check_tables(tables, owner=owner)
    return tables


def check_tables(tables: Sequence[WorkbenchTable], *, owner: str) -> None:
    """Refuse a port that cannot be one, a frame column's name that cannot be one, or either
    used twice.

    Two ports whose names differ only in case are refused too: the editor binds a port by
    its name compared without case, as it binds a Quote Input's frames. So is a name the quote
    store cannot keep as it is (:func:`store_name_problems`), each schema table being its own
    table there.
    """
    by_folded_port: dict[str, str] = {}
    names_seen: set[str] = set()
    for table in tables:
        if not table.name.isidentifier():
            raise WorkbenchTablesError(
                f"The {owner}'s table {table.name!r} cannot be named so: a table's name is "
                "letters, digits and underscores, not starting with a digit."
            )
        if not is_frame_label(table.port):
            raise WorkbenchTablesError(
                f"The {owner}'s table {table.name!r} cannot be the port {table.port!r}: a "
                f"port's name is {_NAME_RULE}."
            )
        earlier = by_folded_port.get(table.port.casefold())
        if earlier == table.port or table.name in names_seen:
            raise WorkbenchTablesError(f"The {owner}'s tables name {table.name!r} twice.")
        if earlier is not None:
            raise WorkbenchTablesError(
                f"The {owner}'s ports {earlier!r} and {table.port!r} differ only in case, so "
                "they cannot both be ports."
            )
        by_folded_port[table.port.casefold()] = table.port
        names_seen.add(table.name)
        names: set[str] = set()
        for column in table.columns:
            if not is_frame_label(column.name):
                raise WorkbenchTablesError(
                    f"The {owner}'s table {table.name!r} has a column {column.name!r}, which "
                    f"cannot name a frame's column: a column's name is {_NAME_RULE}."
                )
            if column.name in names:
                raise WorkbenchTablesError(
                    f"The {owner}'s table {table.name!r} names a column {column.name!r} twice."
                )
            names.add(column.name)
        problems = store_name_problems(table.name, [column.name for column in table.columns])
        if problems:
            raise WorkbenchTablesError(f"The {owner}'s {problems[0]}.")


def port_tables(tables: Iterable[WorkbenchTable]) -> tuple[WorkbenchTable, ...]:
    """The tables that are ports: those with a column."""
    return tuple(table for table in tables if table.columns)


#: The quote's identity at the head of a table, as a node's tables hold it: each column's
#: name and type.
IDENTITY_SHAPE: tuple[tuple[str, ColumnType], ...] = IDENTITY_COLUMNS
IDENTITY_SCHEMA: tuple[WorkbenchColumn, ...] = tuple(
    WorkbenchColumn(name=name, type=kind) for name, kind in IDENTITY_SHAPE
)


def carries_identity(table: WorkbenchTable) -> bool:
    """Whether *table* leads with the quote's identity, its reference and option: every
    table the form makes but ``quote_details``, which holds the identity itself. A node's
    tables have no ids, so the columns are known by their place, names and types."""
    held = tuple((column.name, column.type) for column in table.columns[: len(IDENTITY_SHAPE)])
    return table.name != QUOTE_DETAILS_NAME and held == IDENTITY_SHAPE


def request_columns(table: WorkbenchTable) -> list[WorkbenchColumn]:
    """The columns a request gives *table*: its own, the identity left to ``quote_details``."""
    return table.columns[len(IDENTITY_SHAPE) :] if carries_identity(table) else table.columns


def request_schema(tables: Iterable[WorkbenchTable]) -> pl.Schema:
    """The schema of one request holding *tables*: each under its name, of the columns a
    request gives it (:func:`request_columns`), as a quote holds it."""
    schema: dict[str, pl.DataType] = {}
    for table in tables:
        struct = pl.Struct({column.name: column.dtype for column in request_columns(table)})
        schema[table.name] = struct if table.one_row else pl.List(struct)
    return pl.Schema(schema)


def quote_schema(tables: Iterable[WorkbenchTable]) -> pl.Schema:
    """The schema of one quote holding *tables* with every column, each under its name, as
    a quote holds it: a response's, whose tables carry the quote's identity themselves. A
    request's is :func:`request_schema`."""
    return pl.Schema({table.name: table.quote_dtype for table in tables})


@dataclass(frozen=True, slots=True)
class WorkbenchTables:
    """The three things ``GET /api/workbench/tables`` serves, as the form defines them now."""

    tables: tuple[WorkbenchTable, ...]
    sample: dict[str, Any]
    response_tables: tuple[WorkbenchTable, ...]


def workbench_tables(spec: FormSpec) -> WorkbenchTables:
    return WorkbenchTables(input_tables(spec), sample_quote(spec), output_tables(spec))


def input_tables(spec: FormSpec) -> tuple[WorkbenchTable, ...]:
    """The Workbench Input's tables: each input table's input side, the quote's identity and
    its inputs, in schema order, checked."""
    tables = tuple(_input_side(table) for table in spec.data_schema.tables if table.has_inputs)
    check_tables(tables, owner="Workbench Input")
    return tables


def output_tables(spec: FormSpec) -> tuple[WorkbenchTable, ...]:
    """The Workbench Output's tables: each output table's output side, the quote's identity,
    the index of a many-row input table, then its outputs, in schema order, checked."""
    tables = tuple(_output_side(table) for table in spec.data_schema.tables if table.has_outputs)
    check_tables(tables, owner="Workbench Output")
    return tables


def _column(column: SchemaColumn) -> WorkbenchColumn:
    return WorkbenchColumn(name=column.name, type=column.type)


def _input_side(table: SchemaTable) -> WorkbenchTable:
    # quote_details' columns the store keeps alone (QUOTE_DETAILS_STORE_ONLY) are no port's.
    store_only = QUOTE_DETAILS_STORE_ONLY if table.name == QUOTE_DETAILS_NAME else frozenset()
    index = table.index
    return WorkbenchTable(
        name=table.name,
        side="input",
        port=f"{table.name}_input",
        rows=table.rows,
        columns=[_column(column) for column in table.inputs if column.name not in store_only],
        key=None if index is None else index.name,
        null_key=None if index is None else null_index(index),
    )


def _output_side(table: SchemaTable) -> WorkbenchTable:
    index = table.index
    identity = list(IDENTITY_SCHEMA) if holds_identity(table) else []
    # A table of outputs alone holds its index among its outputs, filled as its kind says;
    # an input table's index stays with its inputs and is carried here, the key each output
    # row is put back on its input row by.
    alone = index is not None and table.outputs_alone
    carried = [] if index is None or alone else [_column(index)]
    return WorkbenchTable(
        name=table.name,
        side="output",
        port=f"{table.name}_output",
        rows=table.rows,
        columns=[*identity, *carried, *(_column(column) for column in table.own_outputs)],
        key=None if index is None else index.name,
        index_kind=index.index if alone and index is not None else None,
        index_values=index_values(index) if alone and index is not None else [],
    )


def index_values(column: SchemaColumn) -> list[KeyValue]:
    """An index of values' allowed values typed as the index, in their order; none for any
    other column."""
    if column.index != "values":
        return []
    return [_typed(column.type, value) for value in column.options]


def null_index(column: SchemaColumn) -> KeyValue:
    """The value the null quote's row holds in a table's index *column*: an index of values'
    first value, else the first row's number, as text for a Text index."""
    values = index_values(column)
    return values[0] if values else numbered_value(column, 1)


def numbered_value(column: SchemaColumn, number: int) -> KeyValue:
    """A numbered index's value for row *number*: the number, or its digits for Text."""
    return number if column.type == "int" else str(number)


def index_order(column: SchemaColumn, values: Sequence[Any]) -> list[int]:
    """The places in *values*, the index values of a quote's stored rows in the order they
    were read, in the order the rows open in: a numbered index's by number (a Text one's
    digits read as a number), a typed one's by value, and an index of values' by each value's
    place among its values; a value that is none of those after the rest, in the order read."""
    places: dict[Any, int] = {}
    for place, value in enumerate(index_values(column)):
        places.setdefault(value, place)

    def rank(place: int) -> tuple[int, Any, int]:
        value = values[place]
        if column.index == "numbered":
            number = index_number(value)
            return (0, number, place) if number is not None else (1, 0, place)
        if column.index == "values":
            return (0, places[value], place) if value in places else (1, 0, place)
        # A typed index's values are all of its type as the store holds them; another kind
        # of value, as a hand edit could leave, goes after them rather than being compared.
        if column.type == "int" and isinstance(value, int) and not isinstance(value, bool):
            return (0, value, place)
        if column.type == "str" and isinstance(value, str):
            return (0, value, place)
        return (1, 0, place)

    return sorted(range(len(values)), key=rank)


def index_number(value: Any) -> int | None:
    """A numbered index's stored *value* as its number: an Integer's, or a Text one's written
    as Haute writes one, digits with no sign or leading zero, so ``"01"`` beside ``"1"`` is
    no number and never takes its place."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.isascii() and value.isdigit() and value == str(int(value)):
        return int(value)
    return None


def key_collisions(table: SchemaTable) -> list[str]:
    """*table*'s index of values' allowed values that read as one value in its type, each
    group said with what to correct: a value giving a row cannot key it while another reads
    the same (``keyCollisions`` in the view)."""
    column = table.index
    if column is None or column.index != "values" or column.type != "int":
        return []
    groups: dict[Any, list[str]] = {}
    for value in column.options:
        groups.setdefault(_typed(column.type, value), []).append(value)
    return [
        f"{table.name}.{column.name}'s allowed values {_listed(values)} are one Integer, so "
        "they cannot key its rows: correct the params file"
        for values in groups.values()
        if len(values) > 1
    ]


def _missing_index(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def request_key_problem(table: WorkbenchTable, keys: Sequence[Any]) -> str | None:
    """Why the request's rows of a many-row input side cannot be priced, given each row's
    index value in order: a row holding none, two holding one, or a Text index's value
    longer than the store keys a row by; None when each holds its own."""
    if table.key is None:
        return None
    missing = [place for place, value in enumerate(keys, 1) if _missing_index(value)]
    if missing:
        rows = "row" if len(missing) == 1 else "rows"
        verb = "holds" if len(missing) == 1 else "hold"
        return f"{table.name}'s {rows} {_listed(missing)} {verb} no {table.key}"
    places: dict[Any, list[int]] = {}
    for place, value in enumerate(keys, 1):
        places.setdefault(value, []).append(place)
    for value, held in places.items():
        if len(held) > 1:
            return (
                f"{table.name}'s rows {_listed(held)} hold {table.key} {key_text(value)}; each "
                f"row's {table.key} is its own"
            )
    for place, value in enumerate(keys, 1):
        if isinstance(value, str) and (limit := index_text_limit(value)):
            return f"{table.name}'s row {place} holds a {table.key} longer than {limit}"
    return None


#: The most key values a misfit names in one list before saying how many more there are.
_MISFIT_LISTED = 10


def output_misfit(
    table: WorkbenchTable, answer: Sequence[Any], request: Sequence[Any]
) -> str | None:
    """Why a many-row output side's answer does not fit its rows, given the index value of
    each answer row and, for a table with inputs, of each of the request's rows, in order;
    None when every answer row holds one, none comes twice and each is a row's of the
    request, or for a table of outputs alone one of its index's values, or a typed Text
    index's no longer than a key is held. A numbered index of outputs alone always fits, as
    the Workbench Output numbers it (``outputMisfit`` in the view, which answers the same
    cases with the same text)."""
    if table.key is None or table.index_kind == "numbered":
        return None
    parts: list[str] = []
    given = [value for value in answer if not _missing_index(value)]
    missing = len(answer) - len(given)
    if missing:
        parts.append(
            f"{missing} row has no {table.key}"
            if missing == 1
            else f"{missing} rows have no {table.key}"
        )
    seen: set[Any] = set()
    twice: list[Any] = []
    for value in given:
        if value in seen and value not in twice:
            twice.append(value)
        seen.add(value)
    if twice:
        parts.append(
            f"{table.key} {_values(twice)} {'is' if len(twice) == 1 else 'are'} given twice"
        )
    if table.index_kind is None:
        held = {value for value in request if not _missing_index(value)}
        stray = list(dict.fromkeys(value for value in given if value not in held))
        if stray:
            parts.append(
                f"{table.key} {_values(stray)} {'is' if len(stray) == 1 else 'are'} on no row "
                "priced"
            )
    elif table.index_kind == "values":
        allowed = set(table.index_values)
        stray = list(dict.fromkeys(value for value in given if value not in allowed))
        if stray:
            parts.append(
                f"{table.key} {_values(stray)} {'is' if len(stray) == 1 else 'are'} none of its "
                "values"
            )
    else:
        limits: dict[str, list[str]] = {}
        for value in dict.fromkeys(given):
            if isinstance(value, str) and (limit := index_text_limit(value)):
                limits.setdefault(limit, []).append(value)
        for limit, long in limits.items():
            parts.append(
                f"{table.key} {_values([value[:40] for value in long])} "
                f"{'is' if len(long) == 1 else 'are'} longer than {limit}"
            )
    if not parts:
        return None
    return f"{table.port} does not fit {table.name}'s rows: {'; '.join(parts)}."


def holds_output(value: Any) -> bool:
    """Whether an answer's *value* is one the store keeps: neither null nor a number that is
    not finite (``holdsOutput`` in the view)."""
    return value is not None and not (isinstance(value, float) and not math.isfinite(value))


def key_text(value: Any) -> str:
    """A key's value as a message says it: a number as its shortest text, a whole one
    without a point, text in double quotes and a date as its ISO text."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _values(values: Sequence[Any]) -> str:
    shown = [key_text(value) for value in values[:_MISFIT_LISTED]]
    more = len(values) - len(shown)
    return ", ".join(shown) + (f" and {more} more" if more else "")


def _listed(items: Sequence[Any]) -> str:
    texts = [str(item) for item in items]
    return texts[0] if len(texts) == 1 else f"{', '.join(texts[:-1])} and {texts[-1]}"


def sample_quote(
    spec: FormSpec, *, untyped: Collection[tuple[str, str]] = frozenset()
) -> dict[str, Any]:
    """The sample quote typed while building, as a request holds it, for the Workbench Input.

    An input table with values is an object under its name when it has one row per quote, and
    a list of objects when it has many; rows with nothing typed are left out, and so is a table
    with none. A table whose index gives a row for each value has those rows
    (:func:`preset_rows`), each filled by its value; but those values are not typing, nor is a
    numbered index's, so a quote holding nothing else
    is empty, as a blank one is, and so are the defaults a new quote starts with, which are
    not typing either. A value in a column the sheets hide is left out, as if it had
    never been typed, save in ``quote_details``, which every request holds whole whatever the
    sheets hide, but for the columns the quote store keeps alone
    (``QUOTE_DETAILS_STORE_ONLY``), which no request holds. Values
    take their column's type: a number typed with a currency sign or separators becomes a
    number, and an unticked box in a filled row is false. A value that isn't of its column's
    type stays as typed, for the Workbench Input to report. A cell of *untyped*, by table and
    column name, is no typing either: the underwriter app's own details, which decide what the
    sheets show as any value does, but are not the underwriter's.
    """
    # Imported here: the visibility rules read cells as this module types them.
    from haute._workbench_visibility import visibility

    hidden = visibility(spec, spec.sample).hidden
    sample, _, typed = _quoted(spec, hidden, untyped)
    return sample if typed else {}


def _quoted(
    spec: FormSpec,
    hidden: Collection[tuple[str, str]],
    untyped: Collection[tuple[str, str]],
    carried: Callable[[Mapping[str, Any]], bool] | None = None,
) -> tuple[dict[str, Any], dict[str, list[int]], bool]:
    """:func:`sample_quote`'s quote whatever is typed, each many-row table's rows' numbers on
    the sheet, by its name, and whether anything is typed. A row nothing is typed in is left
    out, unless *carried* says it holds something the sheets do not show."""
    sample: dict[str, Any] = {}
    numbers: dict[str, list[int]] = {}
    typed = False
    for table in spec.data_schema.tables:
        if not table.has_inputs:
            continue
        rows = preset_rows(table, spec.sample.get(table.name, []))
        if table.rows == "one":
            rows = rows[:1]
        if table.name == QUOTE_DETAILS_NAME:
            columns = [
                column for column in table.inputs if column.name not in QUOTE_DETAILS_STORE_ONLY
            ]
        else:
            # The index says which row is which, so it is sent whatever the sheets hide.
            columns = [
                column
                for column in table.inputs
                if (table.name, column.name) not in hidden or column.index is not None
            ]
        typed = typed or any(
            _filled(row.get(column.name)) and not _starting(table, column, row.get(column.name))
            for row in rows
            for column in columns
            if _is_typing(column) and (table.name, column.name) not in untyped
        )
        # Numbered as the rows stand, from 1, so deleting a row renumbers those after it.
        numbered = [
            (number, record)
            for number, row in enumerate(rows, 1)
            if (record := _record(columns, row, number)) or (carried is not None and carried(row))
        ]
        if numbered:
            records = [record for _, record in numbered]
            sample[table.name] = records if table.rows == "many" else records[0]
            if table.rows == "many":
                numbers[table.name] = [number for number, _ in numbered]
    return sample, numbers, typed


def quote_request_rows(
    spec: FormSpec,
    values: Mapping[str, Any],
    *,
    carried: Callable[[Mapping[str, Any]], bool] | None = None,
) -> tuple[dict[str, Any], dict[str, list[int]]]:
    """The request :func:`quote_request` builds from *values*, whatever is typed, and each
    many-row table's rows' numbers on the sheet, by its name, in the request's order: the
    quote store keeps a row under its sheet number, so a grid opens as it was saved. A row
    *carried* says holds values the sheets do not show is a row too, its cells empty."""
    # Imported here: the visibility rules read cells as this module types them.
    from haute._workbench_visibility import visibility

    priced = spec.model_copy(update={"sample": dict(values)})
    request, numbers, _ = _quoted(
        priced, visibility(priced, priced.sample).hidden, frozenset(), carried
    )
    return request, numbers


def quote_request(
    spec: FormSpec,
    values: Mapping[str, Any],
    *,
    untyped: Collection[tuple[str, str]] = frozenset(),
) -> dict[str, Any]:
    """The request a pricing builds from *values* typed into *spec*'s sheets: each input
    table's filled rows by name, read as :func:`sample_quote` reads the sample, the cells the
    sheets hide left out, and empty when nothing but the cells of *untyped* is typed."""
    return sample_quote(spec.model_copy(update={"sample": dict(values)}), untyped=untyped)


def finite_document(document: Any) -> Any:
    """A priced quote's response *document* with every float that is not finite as null,
    then pruned as a response is: what the quote store keeps, and what a pricing answers."""

    def finite(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: finite(each) for key, each in value.items()}
        if isinstance(value, list):
            return [finite(each) for each in value]
        return None if isinstance(value, float) and not math.isfinite(value) else value

    return prune_document(finite(document))


def priced_tables(document: Mapping[str, Any]) -> dict[str, list[Any]]:
    """A pruned response *document* as a pricing answers its tables: each output table's rows
    by name, a one-row table's one row in a list, JSON-safe."""
    answered = to_json_safe(document)
    return {name: rows if isinstance(rows, list) else [rows] for name, rows in answered.items()}


def _is_typing(column: SchemaColumn) -> bool:
    """Whether a value in *column* is the underwriter's typing: in any column but a numbered
    index and an index of values, whose values the sheets give."""
    return column.index in (None, "typed")


def _values_index(table: SchemaTable) -> SchemaColumn | None:
    """*table*'s index of values, giving an input table a row for each value; None for none."""
    index = table.index
    if index is None or index.index != "values" or not table.has_inputs:
        return None
    return index


def preset_rows(
    table: SchemaTable, rows: Sequence[Mapping[str, str | bool]]
) -> list[Mapping[str, str | bool]]:
    """*rows* as *table* holds them: with an index giving it a row for each value, those
    rows in the values' order, each value's row the first holding it in the index's type,
    the rows holding
    nothing there filling the rows left in order, each holding its value, and any other row
    left out; else *rows* as they are.

    The view reads a table's rows by the same rule (``presetRows`` in
    ``frontend/src/utils/sheetValues.ts``), so a grid's rows are the rows priced; the cases
    in ``frontend/src/utils/__tests__/fixtures/presetRows.json`` hold both to it.
    """
    column = _values_index(table)
    if column is None:
        return list(rows)
    places: dict[Any, int] = {}
    for place, value in enumerate(column.options):
        places.setdefault(_typed(column.type, value), place)
    placed: list[Mapping[str, str | bool] | None] = [None] * len(column.options)
    loose: list[Mapping[str, str | bool]] = []
    for row in rows:
        raw = row.get(column.name)
        if not _filled(raw):
            loose.append(row)
            continue
        at = places.get(_typed(column.type, raw.strip())) if isinstance(raw, str) else None
        if at is not None and placed[at] is None:
            placed[at] = row
    free = iter(loose)
    return [
        {**(row if row is not None else next(free, {})), column.name: value}
        for row, value in zip(placed, column.options, strict=True)
    ]


@dataclass(frozen=True, slots=True)
class IndexProblem:
    """A typed index's problem on a quote's rows: its table, the column, the rows as the
    sheet numbers them, and what is wrong, said whole."""

    table: str
    column: str
    rows: tuple[int, ...]
    message: str


def typed_index_problems(
    spec: FormSpec, values: Mapping[str, Any], *, kept: str | None = None
) -> list[IndexProblem]:
    """Each typed index of an input table whose rows, as the sheets number them, include one
    holding anything the request reads but the index blank, two holding one value (compared
    as typed), or, a Text one, a value beyond ``index_text_limit``: the store keys a
    quote's rows by the index, so a quote holding one is neither priced nor saved
    (``typedIndexProblems`` in the view). A row holding nothing but its *kept* field, values
    in columns the form no longer holds, holds something."""
    # Imported here: the visibility rules read cells as this module types them.
    from haute._workbench_visibility import visibility

    priced = spec.model_copy(update={"sample": dict(values)})
    hidden = visibility(priced, priced.sample).hidden
    problems: list[IndexProblem] = []
    for table in spec.data_schema.tables:
        index = table.index
        if index is None or index.index != "typed" or not table.has_inputs:
            continue
        read = [
            column
            for column in table.inputs
            if column is not index and (table.name, column.name) not in hidden
        ]
        rows = values.get(table.name) or []
        blank: list[int] = []
        long: list[tuple[int, str]] = []
        places: dict[Any, list[int]] = {}
        for number, row in enumerate(rows, 1):
            raw = row.get(index.name)
            if not _filled(raw):
                if any(_filled(row.get(column.name)) for column in read) or (
                    kept is not None and row.get(kept) is not None
                ):
                    blank.append(number)
                continue
            value = _typed(index.type, raw.strip()) if isinstance(raw, str) else raw
            if isinstance(value, str) and (limit := index_text_limit(value)):
                long.append((number, limit))
            places.setdefault(value, []).append(number)
        label = index.name
        for number in blank:
            problems.append(
                IndexProblem(
                    table.name,
                    index.name,
                    (number,),
                    f"{table.name}'s row {number} holds no {label}",
                )
            )
        for value, held in places.items():
            if len(held) > 1:
                problems.append(
                    IndexProblem(
                        table.name,
                        index.name,
                        tuple(held),
                        f"{table.name}'s rows {_listed(held)} hold {label} {key_text(value)}; "
                        f"each row's {label} is its own",
                    )
                )
        for number, limit in long:
            problems.append(
                IndexProblem(
                    table.name,
                    index.name,
                    (number,),
                    f"{table.name}'s row {number} holds a {label} longer than {limit}",
                )
            )
    return problems


def _record(
    columns: Sequence[SchemaColumn], row: Mapping[str, str | bool], number: int
) -> dict[str, Any]:
    """Row *number*'s values in *columns* by column name, or nothing when nothing was typed
    in them: a numbered index holds the row's number, and a typed one left blank nothing."""
    if not any(_filled(row.get(column.name)) for column in columns if column.index != "numbered"):
        return {}
    record: dict[str, Any] = {}
    for column in columns:
        raw = row.get(column.name)
        if column.index == "numbered":
            record[column.name] = numbered_value(column, number)
        elif isinstance(raw, str) and raw.strip():
            # Typed text, even in a box: a column whose type changed keeps what was typed.
            record[column.name] = _typed(column.type, raw.strip())
        elif raw is True:
            # A ticked box, even in a column that is no longer True/false.
            record[column.name] = True
        elif column.type == "bool":
            record[column.name] = False
    return record


def starting_value(table: SchemaTable, column: SchemaColumn) -> str | bool | None:
    """What a new quote starts *column* with: its default, in a one-row input table, as if
    typed; None for none, and for any other table's column, which takes none
    (``startingValue`` in the view)."""
    if (
        not table.is_output(column.name)
        and table.rows == "one"
        and not column.index
        and _filled(column.default_value)
    ):
        return column.default_value
    return None


def _starting(table: SchemaTable, column: SchemaColumn, raw: str | bool | None) -> bool:
    """Whether *raw* is the default a new quote starts *column* with, which is not typing."""
    start = starting_value(table, column)
    return start is not None and raw == start


def _filled(raw: str | bool | None) -> bool:
    return raw is True or (isinstance(raw, str) and raw.strip() != "")


#: A decimal number, once a currency sign, separators and spaces are gone: the one rule the
#: view's ``parseTypedNumber`` (``frontend/src/utils/sheetValues.ts``) applies too, so a cell
#: the view marks is one the server would keep as typed.
_NUMBER = re.compile(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
_WHOLE = re.compile(r"[+-]?[0-9]+")


def _typed(column_type: str, text: str) -> Any:
    """Typed text as its column's type holds it; text that isn't of that type, as typed."""
    if column_type not in ("int", "float"):
        return text
    cleaned = re.sub(r"[$£€,\s]", "", text)
    if _NUMBER.fullmatch(cleaned) is None:
        return text
    if column_type == "int" and _WHOLE.fullmatch(cleaned):
        # Digits alone are a whole number, read exactly however large: not through a float
        # (``parseTypedWhole`` in the view, for a Show when's tests).
        return int(cleaned)
    number = float(cleaned)
    if not math.isfinite(number):
        return text
    return int(number) if column_type == "int" and number.is_integer() else number
