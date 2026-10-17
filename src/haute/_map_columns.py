"""How Haute reads a map column: as a list of key-value structs, keeping every entry.

Polars 2 reads an Arrow or Parquet map as its own ``Map`` dtype, which Haute's
dtype vocabularies do not have, and its Parquet reader keeps only the last value
of a key a map repeats, even when asked for a list of key-value structs. Polars
1, which had no ``Map``, read every map as a list of ``{key, value}`` structs
with all of its entries, and pipelines are written against that shape.

pyarrow keeps every entry, and its cast from a map to a list of key-value
structs shares the map's buffers, so Haute converts a map column where it holds
the data before Polars reads it: the Arrow batches a provider returns, and a
Parquet file it snapshots, which it reads through pyarrow one row group at a
time. A frame Polars itself read from a source that keeps every entry (Arrow
IPC) is cast in Polars, which keeps them too. A lakehouse table is read by
Polars' Parquet reader, so a key its writer repeated keeps only its last value
there; Spark and Delta writers keep a map's keys unique. A Parquet file a Data
Input would scan directly is refused instead (see
``haute._polars_io_registry.refuse_direct_map_columns``).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

import polars as pl

if TYPE_CHECKING:
    import pyarrow as pa

# The names Polars 1 and pyarrow's map layout give a map entry's two fields.
_KEY = "key"
_VALUE = "value"


def holds_map(dtype: Any) -> bool:
    """Whether *dtype* is a ``Map`` or nests one in a list, array or struct."""
    if isinstance(dtype, pl.Map):
        return True
    if isinstance(dtype, pl.List | pl.Array):
        return holds_map(dtype.inner)
    if isinstance(dtype, pl.Struct):
        return any(holds_map(field.dtype) for field in dtype.fields)
    return False


def map_entries_dtype(dtype: Any) -> Any:
    """*dtype* with every ``Map`` in it a list of ``{key, value}`` structs."""
    if isinstance(dtype, pl.Map):
        return pl.List(
            pl.Struct({_KEY: map_entries_dtype(dtype.key), _VALUE: map_entries_dtype(dtype.value)})
        )
    if isinstance(dtype, pl.List):
        return pl.List(map_entries_dtype(dtype.inner))
    if isinstance(dtype, pl.Array):
        return pl.Array(map_entries_dtype(dtype.inner), dtype.size)
    if isinstance(dtype, pl.Struct):
        return pl.Struct({field.name: map_entries_dtype(field.dtype) for field in dtype.fields})
    return dtype


def map_column_names(schema: Mapping[str, Any]) -> list[str]:
    """The columns of *schema* that hold a map, in schema order."""
    return [name for name, dtype in schema.items() if holds_map(dtype)]


def map_entries_frame(frame: pl.LazyFrame) -> pl.LazyFrame:
    """*frame* with every column holding a map cast to lists of key-value structs.

    The cast keeps every entry Polars read; a frame without a map is returned
    as it is.
    """
    schema = frame.collect_schema()
    casts = [
        pl.col(name).cast(map_entries_dtype(dtype))
        for name, dtype in schema.items()
        if holds_map(dtype)
    ]
    return frame.with_columns(casts) if casts else frame


def arrow_holds_map(arrow_type: pa.DataType) -> bool:
    """Whether *arrow_type* is a map or nests one in a list or struct."""
    import pyarrow as pa

    if pa.types.is_map(arrow_type):
        return True
    if pa.types.is_struct(arrow_type):
        return any(arrow_holds_map(field.type) for field in arrow_type)
    if (
        pa.types.is_list(arrow_type)
        or pa.types.is_large_list(arrow_type)
        or pa.types.is_fixed_size_list(arrow_type)
    ):
        return arrow_holds_map(arrow_type.value_type)
    return False


def arrow_map_entries_type(arrow_type: pa.DataType) -> pa.DataType:
    """*arrow_type* with every map in it a list of ``{key, value}`` structs.

    The list holds the map's own entries field, so the cast from the map
    reuses its offsets and children.
    """
    import pyarrow as pa

    if pa.types.is_map(arrow_type):
        key = arrow_type.key_field
        item = arrow_type.item_field
        entries = pa.struct(
            [
                key.with_name(_KEY).with_type(arrow_map_entries_type(key.type)),
                item.with_name(_VALUE).with_type(arrow_map_entries_type(item.type)),
            ]
        )
        return pa.list_(pa.field("item", entries, nullable=False))
    if pa.types.is_struct(arrow_type):
        return pa.struct(
            [field.with_type(arrow_map_entries_type(field.type)) for field in arrow_type]
        )
    if pa.types.is_large_list(arrow_type):
        field = arrow_type.value_field
        return pa.large_list(field.with_type(arrow_map_entries_type(field.type)))
    if pa.types.is_fixed_size_list(arrow_type):
        field = arrow_type.value_field
        return pa.list_(field.with_type(arrow_map_entries_type(field.type)), arrow_type.list_size)
    if pa.types.is_list(arrow_type):
        field = arrow_type.value_field
        return pa.list_(field.with_type(arrow_map_entries_type(field.type)))
    return arrow_type


def map_entries_table(table: pa.Table) -> pa.Table:
    """*table* with every column holding a map cast to lists of key-value structs.

    Field and schema metadata are kept (a provider's Arrow schema, an extension
    column's name), and a table without a map is returned as it is.
    """
    import pyarrow as pa

    if not any(arrow_holds_map(field.type) for field in table.schema):
        return table
    target = pa.schema(
        [field.with_type(arrow_map_entries_type(field.type)) for field in table.schema],
        metadata=table.schema.metadata,
    )
    return table.cast(target)


def parquet_map_column_names(path: Path) -> list[str]:
    """The columns of the Parquet file at *path* that hold a map, from its footer.

    Read by Polars, not pyarrow, which cannot open a file that holds a 128-bit
    integer.
    """
    return map_column_names(pl.read_parquet_schema(path))


def iter_parquet_map_entries(
    path: Path,
    *,
    columns: Sequence[str] | None = None,
    n_rows: int | None = None,
) -> Iterator[pa.Table]:
    """The rows of the Parquet file at *path*, one row group at a time, maps as entries.

    pyarrow reads each row group, keeping every entry of every map, and the
    cast to lists of key-value structs copies nothing, so memory holds one row
    group. *columns* selects and orders the columns and *n_rows* stops after
    that many rows. A file that yields no rows yields one empty table, so its
    schema is still written.
    """
    import pyarrow.parquet as pq

    parquet = pq.ParquetFile(path)
    selected = list(columns) if columns is not None else None
    remaining = n_rows
    yielded = False
    for index in range(parquet.num_row_groups):
        if remaining is not None and remaining <= 0:
            break
        table = parquet.read_row_group(index, columns=selected)
        if remaining is not None:
            table = table.slice(0, remaining)
            remaining -= table.num_rows
        yielded = True
        yield map_entries_table(table)
    if not yielded:
        empty = parquet.schema_arrow.empty_table()
        yield map_entries_table(empty.select(selected) if selected is not None else empty)
