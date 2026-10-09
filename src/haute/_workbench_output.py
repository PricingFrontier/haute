"""The Workbench Output: the response's tables, filled from the frames connected to them.

A Workbench Output's ``tables`` are a copy of the response's tables that an installed
extension supplies (specs/extensions), in the Quote Input's v2 shape. Each table is a port:
the frame connected to it fills it, each column from the frame's column the ``mapping`` picks
or, without a pick, the frame's column of the same name, given its declared type. A one-row
table (path ``$[:]``) holds the quote's one row and a many-row table (one ``[:]`` below the
root) any number. The node's result is its tables; where the pipeline answers a request they
become the response, built by the Quote Response's assembler, so the two response nodes
build, prune and type a response alike.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

import polars as pl

from haute._api_input_schema import (
    ColumnType,
    parse_column_path_full,
    parse_table_path,
    validate_v2_schema,
)
from haute._json_shred._shred import _POLARS_TYPE_MAP
from haute._output_assembler import assemble_output_from_mapping, output_document_schema
from haute._polars_utils import execution_collect, limited_python_scan
from haute.errors import ExecutionError

# The source frame the one-row tables' columns make together in the response. It is no
# identifier, so no table's label can be it.
_ONE_ROW_TABLES = "$[:]"


class WorkbenchOutputError(ExecutionError):
    """A Workbench Output that cannot fill its tables from the frames connected to it."""


@dataclass(frozen=True, slots=True)
class OutputColumn:
    """A declared column: its name, its path in the response, its type."""

    name: str
    path: str
    type: ColumnType


@dataclass(frozen=True, slots=True)
class OutputTable:
    """One of a Workbench Output's tables: its port's label and its declared columns."""

    label: str
    one_row: bool
    columns: tuple[OutputColumn, ...]


class WorkbenchOutputTables(dict[str, pl.LazyFrame]):
    """A Workbench Output's result: its tables' frames by label, and the tables they fill.

    A ``dict`` of frames, as a multi-frame source's result is, so a preview shows each table
    and a walk keeps them as a bundle; ``tables`` lets :func:`workbench_response` build the
    response from them wherever the pipeline answers a request.
    """

    def __init__(self, frames: Mapping[str, pl.LazyFrame], tables: tuple[OutputTable, ...]):
        super().__init__(frames)
        self.tables = tables


def workbench_output_tables(config: Mapping[str, Any]) -> tuple[OutputTable, ...]:
    """The tables a Workbench Output fills, read from its *config*."""
    tables = config.get("tables")
    if not tables:
        raise WorkbenchOutputError(
            "This Workbench Output has no tables: add output tables to the workbench's "
            "schema and save it."
        )
    validate_v2_schema({"tables": tables})
    return tuple(_table(table) for table in cast(list[dict[str, Any]], tables))


def _table(table: dict[str, Any]) -> OutputTable:
    label = str(table["label"])
    path = str(table["path"])
    segments = parse_table_path(path)
    if sum(1 for _key, is_array in segments if is_array) > 1:
        raise WorkbenchOutputError(
            f"The Workbench Output's {label!r} table is at {path!r}: a table has one row "
            "per quote ('$[:]') or many ('$[:].<name>[:]')."
        )
    columns: list[OutputColumn] = []
    for column in table.get("columns") or []:
        locating, _leaf = parse_column_path_full(column["path"])
        if locating != segments:
            raise WorkbenchOutputError(
                f"The Workbench Output's {label!r} table has its {column['name']!r} column "
                f"at {column['path']!r}, outside the table at {path!r}."
            )
        columns.append(OutputColumn(column["name"], column["path"], column["type"]))
    return OutputTable(label, not segments, tuple(columns))


def workbench_output_mapping(
    config: Mapping[str, Any], tables: tuple[OutputTable, ...]
) -> dict[str, dict[str, str | None]]:
    """The frame column each table column's ``mapping`` entry picks, by table and column.

    An entry is a frame column's name, or ``None`` for none; a column without one is filled
    by name. An entry for a table or column the tables do not have fails.
    """
    mapping = config.get("mapping") or {}
    if not isinstance(mapping, dict):
        raise WorkbenchOutputError(
            "The Workbench Output's mapping must be an object of tables, each an object of columns."
        )
    columns_by_table = {table.label: {c.name for c in table.columns} for table in tables}
    picks: dict[str, dict[str, str | None]] = {}
    for label, entries in mapping.items():
        if label not in columns_by_table:
            raise WorkbenchOutputError(
                f"The Workbench Output's mapping has a {label!r} table, which is none of its "
                f"tables ({', '.join(columns_by_table)})."
            )
        if not isinstance(entries, dict):
            raise WorkbenchOutputError(
                f"The Workbench Output's mapping for its {label!r} table must be an object of "
                "columns."
            )
        for column, source in entries.items():
            if column not in columns_by_table[label]:
                raise WorkbenchOutputError(
                    f"The Workbench Output's mapping has a {column!r} column in its {label!r} "
                    "table, which has no such column."
                )
            if source is not None and (not isinstance(source, str) or not source):
                raise WorkbenchOutputError(
                    f"The Workbench Output's mapping for {label!r}.{column!r} must name a "
                    "column, or be null for none."
                )
        picks[label] = dict(entries)
    return picks


def _fits(declared: ColumnType, dtype: pl.DataType) -> bool:
    """Whether a frame's column of *dtype* can fill a column declared *declared*."""
    if dtype == pl.Null:
        return True
    if declared == "int":
        return dtype.is_integer()
    if declared == "float":
        return dtype.is_numeric()
    if declared == "str":
        return dtype == pl.String or isinstance(dtype, (pl.Categorical, pl.Enum))
    if declared == "bool":
        return dtype == pl.Boolean
    return dtype == pl.Date


def fill_workbench_tables(
    frames: Mapping[str, pl.LazyFrame],
    tables: tuple[OutputTable, ...],
    mapping: Mapping[str, Mapping[str, str | None]],
) -> WorkbenchOutputTables:
    """Each table filled from the frame its port has in *frames*, through *mapping*.

    Nothing is collected: a one-row table checks it has one row when it is read.
    """
    filled: dict[str, pl.LazyFrame] = {}
    for table in tables:
        frame = frames.get(table.label)
        if frame is None:
            raise WorkbenchOutputError(
                f"Connect a frame to the Workbench Output's {table.label!r} table."
            )
        selection = _filled(table, frame, mapping.get(table.label, {}))
        # A one-row table with no columns yet has no row to check.
        checked = table.one_row and table.columns
        filled[table.label] = _exactly_one_row(table, selection) if checked else selection
    return WorkbenchOutputTables(filled, tables)


def _filled(
    table: OutputTable, frame: pl.LazyFrame, picks: Mapping[str, str | None]
) -> pl.LazyFrame:
    """*table*'s columns from *frame*: each from its source, cast to its declared type."""
    schema = frame.collect_schema()
    columns: list[pl.Expr] = []
    for column in table.columns:
        dtype = _POLARS_TYPE_MAP[column.type]
        if column.name in picks:
            source = picks[column.name]
            if source is not None and source not in schema:
                raise WorkbenchOutputError(
                    f"The Workbench Output's {table.label!r} table fills {column.name!r} from "
                    f"{source!r}, which the frame connected to it doesn't have; it has "
                    f"{', '.join(schema.names()) or 'none'}. Pick another in its mapping."
                )
        else:
            source = column.name if column.name in schema else None
        if source is None:
            # Nothing fills it: a column of nulls, as long as the frame.
            columns.append(pl.lit(None, dtype=dtype).alias(column.name))
            continue
        if not _fits(column.type, schema[source]):
            raise WorkbenchOutputError(
                f"The Workbench Output's {table.label!r} table declares {column.name!r} as "
                f"{column.type}, but {source!r}, which fills it, is {schema[source]}. Convert "
                "it upstream, pick another column, or change its type in the workbench."
            )
        columns.append(pl.col(source).cast(dtype).alias(column.name))
    # Added beside the frame's own columns, so a literal takes the frame's length and two
    # picks may swap names; then cut to the table's columns, in order.
    return frame.with_columns(columns).select([column.name for column in table.columns])


def _exactly_one_row(table: OutputTable, selection: pl.LazyFrame) -> pl.LazyFrame:
    """*selection*, failing when it is read with other than exactly one row."""

    def produce(_row_limit: int | None) -> pl.DataFrame:
        rows = execution_collect(selection)
        if rows.height != 1:
            held = "no rows" if rows.height == 0 else f"{rows.height} rows"
            raise WorkbenchOutputError(
                f"The Workbench Output's {table.label!r} table has one row per quote, but the "
                f"frame connected to it has {held}. A Workbench Output answers one quote per "
                "request."
            )
        return rows

    return limited_python_scan(produce, schema=selection.collect_schema())


def workbench_response(filled: WorkbenchOutputTables) -> pl.LazyFrame:
    """The response for one quote: the tables in *filled*, each column placed at its path.

    The one-row tables' columns form one source frame for the Quote Response's assembler and
    each many-row table's another, every column mapped to the path of the same name. The
    schema is derived without collecting.
    """
    by_path: dict[str, pl.LazyFrame] = {
        table.label: filled[table.label].select(
            [pl.col(column.name).alias(column.path) for column in table.columns]
        )
        for table in filled.tables
    }
    # A one-row table with no columns yet has nothing to place.
    one_row = [table for table in filled.tables if table.one_row and table.columns]
    sources: dict[str, pl.Schema] = {}
    if one_row:
        one_row_schema: dict[str, pl.DataType] = {}
        for table in one_row:
            for path, dtype in by_path[table.label].collect_schema().items():
                if path in one_row_schema:
                    raise WorkbenchOutputError(
                        f"Two of the Workbench Output's tables put a column at {path!r}."
                    )
                one_row_schema[path] = dtype
        sources[_ONE_ROW_TABLES] = pl.Schema(one_row_schema)
    for table in filled.tables:
        if not table.one_row:
            sources[table.label] = by_path[table.label].collect_schema()
    mapping = [
        {"source_port": port, "source_column": path, "output_path": path, "enabled": True}
        for port, schema in sources.items()
        for path in schema
    ]
    schema = output_document_schema(sources, mapping)

    def produce(row_limit: int | None) -> pl.DataFrame:
        frames_by_source = {
            table.label: by_path[table.label] for table in filled.tables if not table.one_row
        }
        if one_row:
            # Each has been read with exactly one row, so they are one quote side by side.
            rows = [execution_collect(by_path[table.label]) for table in one_row]
            frames_by_source[_ONE_ROW_TABLES] = pl.concat(rows, how="horizontal").lazy()
        document = assemble_output_from_mapping(frames_by_source, mapping, row_limit=row_limit)
        return pl.DataFrame(document, schema=schema)

    return limited_python_scan(produce, schema=schema)


def as_response(result: Any) -> Any:
    """A response node's result as the frame a request is answered with.

    A Workbench Output's tables become their response; a Quote Response's result is its
    response already.
    """
    return workbench_response(result) if isinstance(result, WorkbenchOutputTables) else result
