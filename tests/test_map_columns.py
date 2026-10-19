"""A map column reaches a pipeline as a list of key-value structs, with every entry.

Polars 2's Parquet reader keeps only the last value of a key a map repeats;
pyarrow keeps them all (``haute._map_columns``).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import polars as pl
import pyarrow as pa
import pyarrow.feather as feather
import pyarrow.parquet as pq
import pytest

from haute._execution_context import ExecutionProfile
from haute._input_providers import (
    build_input_snapshot,
    recording_schema_tiers,
    resolve_data_input,
)
from haute._io import read_data_source
from haute._map_columns import (
    arrow_holds_map,
    holds_map,
    iter_parquet_map_entries,
    map_entries_dtype,
    map_entries_frame,
    map_entries_table,
    parquet_map_column_names,
)
from haute._polars_io_registry import PolarsIoConfigError
from haute._source_cache import SourceCacheBuildContext, SourceCacheIdentity, SourceCacheStore
from haute.errors import SchemaMismatchError

_MAP = pa.map_(pa.string(), pa.int64())
_ENTRIES = pl.List(pl.Struct({"key": pl.String, "value": pl.Int64}))
# The same list of key-value structs, as a Data Input's configured schema spells it.
_ENTRIES_SPEC = {
    "type": "List",
    "inner": {"type": "Struct", "fields": {"key": "String", "value": "Int64"}},
}
# A map that repeats a key: Polars 1 kept all three entries, as pyarrow does.
_REPEATED = [("a", 1), ("a", 2), ("b", 3)]
_REPEATED_ENTRIES = [
    {"key": "a", "value": 1},
    {"key": "a", "value": 2},
    {"key": "b", "value": 3},
]


def _map_table(rows: int = 2) -> pa.Table:
    maps = [_REPEATED, [("c", 4)]] * (rows // 2) + [_REPEATED] * (rows % 2)
    return pa.table({"id": pa.array(range(rows), pa.int64()), "m": pa.array(maps, type=_MAP)})


def _write_map_parquet(path: Path, *, rows: int = 2, row_group_size: int | None = None) -> Path:
    pq.write_table(_map_table(rows), path, row_group_size=row_group_size)
    return path


def _entries(frame: pl.DataFrame, column: str = "m") -> list[object]:
    return frame.get_column(column).to_list()


# ---------------------------------------------------------------------------
# The conversion
# ---------------------------------------------------------------------------


def test_polars_reads_a_repeated_map_key_only_once_from_parquet(tmp_path: Path) -> None:
    """The reason for the conversion: Polars 2's Parquet reader drops entries."""
    path = _write_map_parquet(tmp_path / "maps.parquet")

    read = pl.read_parquet(path).select(pl.col("m").cast(_ENTRIES))

    assert _entries(read)[0] == [{"key": "a", "value": 2}, {"key": "b", "value": 3}]


def test_a_schema_naming_a_map_a_list_of_entries_reads_it_the_same_way(tmp_path: Path) -> None:
    """Why a map is found in the file's own schema: a declared schema hides it."""
    path = _write_map_parquet(tmp_path / "maps.parquet")

    read = pl.scan_parquet(path, schema={"id": pl.Int64, "m": _ENTRIES}).collect()

    assert _entries(read)[0] == [{"key": "a", "value": 2}, {"key": "b", "value": 3}]


def test_the_arrow_cast_keeps_every_entry_and_copies_nothing() -> None:
    table = _map_table()

    cast = map_entries_table(table)

    assert cast.column("m").to_pylist()[0] == _REPEATED_ENTRIES
    assert pl.from_arrow(cast).schema == pl.Schema({"id": pl.Int64, "m": _ENTRIES})
    before = table.column("m").chunk(0)
    after = cast.column("m").chunk(0)
    assert before.offsets.buffers()[1].address == after.offsets.buffers()[1].address
    assert before.keys.buffers()[2].address == after.flatten().field(0).buffers()[2].address


def test_the_arrow_cast_reaches_maps_inside_structs_lists_and_maps() -> None:
    nested_map = pa.map_(pa.string(), _MAP)
    table = pa.table(
        {
            "s": pa.array(
                [{"inner": [("x", 1), ("x", 2)], "n": 1}],
                type=pa.struct([pa.field("inner", _MAP), pa.field("n", pa.int64())]),
            ),
            "l": pa.array([[[("y", 3), ("y", 4)]]], type=pa.list_(_MAP)),
            "mm": pa.array([[("k", [("z", 5), ("z", 6)])]], type=nested_map),
        }
    )

    cast = map_entries_table(table)

    assert not any(arrow_holds_map(field.type) for field in cast.schema)
    assert cast.to_pylist() == [
        {
            "s": {"inner": [{"key": "x", "value": 1}, {"key": "x", "value": 2}], "n": 1},
            "l": [[{"key": "y", "value": 3}, {"key": "y", "value": 4}]],
            "mm": [{"key": "k", "value": [{"key": "z", "value": 5}, {"key": "z", "value": 6}]}],
        }
    ]
    from_ipc = pl.from_arrow(table).schema
    assert pl.from_arrow(cast).schema == pl.Schema(
        {name: map_entries_dtype(dtype) for name, dtype in from_ipc.items()}
    )


def test_the_arrow_cast_keeps_schema_and_field_metadata() -> None:
    table = _map_table()
    field = table.schema.field("m").with_metadata({b"origin": b"provider"})
    table = table.cast(
        pa.schema([table.schema.field("id"), field], metadata={b"source": b"databricks"})
    )

    cast = map_entries_table(table)

    assert cast.schema.metadata == {b"source": b"databricks"}
    assert cast.schema.field("m").metadata == {b"origin": b"provider"}


def test_a_table_or_frame_without_a_map_is_left_as_it_is() -> None:
    table = pa.table({"id": [1], "tags": pa.array([["a"]], pa.list_(pa.string()))})
    frame = pl.LazyFrame({"id": [1], "s": [{"a": 1}]})

    assert map_entries_table(table) is table
    assert map_entries_frame(frame) is frame


def test_the_polars_cast_keeps_every_entry_polars_read(tmp_path: Path) -> None:
    feather.write_feather(_map_table(), tmp_path / "maps.arrow")
    frame = pl.scan_ipc(tmp_path / "maps.arrow")
    assert holds_map(frame.collect_schema()["m"])

    cast = map_entries_frame(frame).collect()

    assert cast.schema["m"] == _ENTRIES
    assert _entries(cast)[0] == _REPEATED_ENTRIES


def test_the_entries_dtype_replaces_every_nested_map() -> None:
    dtype = pl.Struct(
        {"inner": pl.Map(pl.String, pl.Int64), "l": pl.Array(pl.Map(pl.String, pl.Int8), 2)}
    )

    assert holds_map(dtype)
    assert not holds_map(map_entries_dtype(dtype))
    assert map_entries_dtype(dtype) == pl.Struct(
        {
            "inner": _ENTRIES,
            "l": pl.Array(pl.List(pl.Struct({"key": pl.String, "value": pl.Int8})), 2),
        }
    )


# ---------------------------------------------------------------------------
# The Parquet reader
# ---------------------------------------------------------------------------


def test_the_parquet_reader_holds_one_row_group_at_a_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write_map_parquet(tmp_path / "maps.parquet", rows=6, row_group_size=2)
    reads: list[int] = []
    real_read = pq.ParquetFile.read_row_group

    def counting_read(
        self: pq.ParquetFile, index: int, *args: object, **kwargs: object
    ) -> pa.Table:
        reads.append(index)
        return real_read(self, index, *args, **kwargs)

    monkeypatch.setattr(pq.ParquetFile, "read_row_group", counting_read)

    reads_at_each_yield = [len(reads) for _table in iter_parquet_map_entries(path)]

    assert reads_at_each_yield == [1, 2, 3]
    assert reads == [0, 1, 2]


def test_the_parquet_reader_selects_orders_and_limits_rows(tmp_path: Path) -> None:
    path = _write_map_parquet(tmp_path / "maps.parquet", rows=6, row_group_size=2)

    tables = list(iter_parquet_map_entries(path, columns=["m", "id"], n_rows=3))

    assert [table.num_rows for table in tables] == [2, 1]
    combined = pa.concat_tables(tables)
    assert combined.column_names == ["m", "id"]
    assert combined.column("m").to_pylist()[0] == _REPEATED_ENTRIES


@pytest.mark.parametrize("n_rows", [0, None])
def test_a_parquet_file_without_rows_still_yields_its_schema(
    tmp_path: Path, n_rows: int | None
) -> None:
    path = tmp_path / "maps.parquet"
    source = _map_table(2) if n_rows == 0 else _map_table(2).slice(0, 0)
    pq.write_table(source, path)

    tables = list(iter_parquet_map_entries(path, n_rows=n_rows))

    assert len(tables) == 1
    assert tables[0].num_rows == 0
    assert pl.from_arrow(tables[0]).schema == pl.Schema({"id": pl.Int64, "m": _ENTRIES})


def test_map_columns_are_found_from_a_footer_pyarrow_cannot_open(tmp_path: Path) -> None:
    """Polars writes Int128 to Parquet, which pyarrow refuses to open."""
    wide = tmp_path / "wide.parquet"
    pl.DataFrame({"key": pl.Series([1], dtype=pl.Int128)}).write_parquet(wide)

    assert parquet_map_column_names(wide) == []
    assert parquet_map_column_names(_write_map_parquet(tmp_path / "maps.parquet")) == ["m"]


# ---------------------------------------------------------------------------
# Every path a source takes into a pipeline
# ---------------------------------------------------------------------------


@dataclass
class _ArrowBuilder:
    tables: list[pa.Table]

    def build(self, context: SourceCacheBuildContext) -> Iterator[pa.Table]:
        context.checkpoint()
        return iter(self.tables)


@dataclass
class _FrameBuilder:
    frame: pl.LazyFrame

    def build(self, context: SourceCacheBuildContext) -> pl.LazyFrame:
        context.checkpoint()
        return self.frame


def _built(store: SourceCacheStore, builder: object) -> pl.DataFrame:
    generation = store.build(
        SourceCacheIdentity(provider="databricks", descriptor={"table": "c.s.policies"}),
        builder,  # type: ignore[arg-type]
        context=SourceCacheBuildContext(profile=ExecutionProfile.LAZY_SINK, build_class="bounded"),
    )
    return generation.lazy_frame.collect()


def test_a_provider_s_arrow_batches_keep_every_map_entry(tmp_path: Path) -> None:
    """A Databricks fetch hands Haute Arrow batches, which its parts store as entries."""
    snapshot = _built(SourceCacheStore(tmp_path), _ArrowBuilder([_map_table(), _map_table()]))

    assert snapshot.schema["m"] == _ENTRIES
    assert _entries(snapshot)[0] == _REPEATED_ENTRIES
    assert snapshot.height == 4


def test_a_polars_frame_s_maps_are_stored_as_entries(tmp_path: Path) -> None:
    feather.write_feather(_map_table(), tmp_path / "maps.arrow")

    snapshot = _built(
        SourceCacheStore(tmp_path), _FrameBuilder(pl.scan_ipc(tmp_path / "maps.arrow"))
    )

    assert snapshot.schema["m"] == _ENTRIES
    assert _entries(snapshot)[0] == _REPEATED_ENTRIES


def _parquet_input(**extra: object) -> dict[str, object]:
    return {"inputType": "file", "format": "parquet", "path": "maps.parquet", **extra}


def test_a_parquet_snapshot_keeps_every_map_entry(tmp_path: Path) -> None:
    _write_map_parquet(tmp_path / "maps.parquet", rows=5, row_group_size=2)
    config = _parquet_input(mode="read")
    store = SourceCacheStore(tmp_path)

    build_input_snapshot(config, store=store, base_dir=tmp_path)
    snapshot = resolve_data_input(config, store=store, base_dir=tmp_path).collect()

    assert snapshot.schema == pl.Schema({"id": pl.Int64, "m": _ENTRIES})
    assert snapshot.height == 5
    assert _entries(snapshot)[0] == _REPEATED_ENTRIES


def test_a_parquet_snapshot_applies_columns_and_n_rows_itself(tmp_path: Path) -> None:
    _write_map_parquet(tmp_path / "maps.parquet", rows=5, row_group_size=2)
    config = _parquet_input(mode="read", arguments={"columns": [1, 0], "n_rows": 3})
    store = SourceCacheStore(tmp_path)

    # ``columns`` is the eager reader's, so this build runs in automatic preparation.
    build_input_snapshot(config, store=store, base_dir=tmp_path, allow_admitted_eager=True)
    snapshot = resolve_data_input(config, store=store, base_dir=tmp_path).collect()

    assert snapshot.columns == ["m", "id"]
    assert snapshot.height == 3
    assert _entries(snapshot)[0] == _REPEATED_ENTRIES


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"row_index_name": "row"}, r"remove \['row_index_name'\]"),
        ({"columns": ["id", "missing"]}, "columns names 'missing'"),
        ({"columns": ["id", 0]}, "more than once"),
        ({"columns": [7]}, "outside the file's 2 columns"),
    ],
)
def test_a_parquet_snapshot_refuses_what_its_pyarrow_read_cannot_apply(
    tmp_path: Path, arguments: dict[str, object], message: str
) -> None:
    _write_map_parquet(tmp_path / "maps.parquet")
    config = _parquet_input(mode="read", arguments=arguments)

    with pytest.raises(PolarsIoConfigError, match=message):
        build_input_snapshot(
            config, store=SourceCacheStore(tmp_path), base_dir=tmp_path, allow_admitted_eager=True
        )


def test_a_direct_scan_of_a_parquet_file_with_a_map_is_refused(tmp_path: Path) -> None:
    _write_map_parquet(tmp_path / "maps.parquet")

    with pytest.raises(PolarsIoConfigError) as exc_info:
        resolve_data_input(_parquet_input(), store=SourceCacheStore(tmp_path), base_dir=tmp_path)

    message = str(exc_info.value)
    assert "'maps.parquet' has map column(s) ['m']" in message
    assert 'mode "read"' in message


def test_a_direct_scan_is_refused_whatever_schema_it_declares_for_the_map(
    tmp_path: Path,
) -> None:
    _write_map_parquet(tmp_path / "maps.parquet")
    config = _parquet_input(arguments={"schema": {"id": "Int64", "m": _ENTRIES_SPEC}})

    with pytest.raises(PolarsIoConfigError, match=r"'maps.parquet' has map column\(s\) \['m'\]"):
        resolve_data_input(config, store=SourceCacheStore(tmp_path), base_dir=tmp_path)


def test_a_flat_quote_input_refuses_a_parquet_file_with_a_map(tmp_path: Path) -> None:
    """A flat Quote Input reads its file directly, as a direct scan does."""
    path = _write_map_parquet(tmp_path / "maps.parquet")

    with pytest.raises(SchemaMismatchError, match=r"has map column\(s\) \['m'\]"):
        read_data_source({"sourceType": "flat_file", "path": str(path)})


@pytest.mark.parametrize("source", ["parts", "parts/*.parquet"])
def test_a_snapshot_of_several_parquet_files_holding_a_map_is_refused(
    tmp_path: Path, source: str
) -> None:
    """pyarrow keeps every entry one file at a time, not across Polars' file expansion."""
    (tmp_path / "parts").mkdir()
    for name in ("a.parquet", "b.parquet"):
        _write_map_parquet(tmp_path / "parts" / name)
    config = {"inputType": "file", "format": "parquet", "path": source, "mode": "read"}

    with pytest.raises(PolarsIoConfigError, match=r"names several files and has map column"):
        build_input_snapshot(config, store=SourceCacheStore(tmp_path), base_dir=tmp_path)


def test_a_snapshot_of_several_parquet_files_without_a_map_is_unaffected(tmp_path: Path) -> None:
    (tmp_path / "parts").mkdir()
    for index, name in enumerate(("a.parquet", "b.parquet")):
        pl.DataFrame({"id": [index]}).write_parquet(tmp_path / "parts" / name)
    config = {"inputType": "file", "format": "parquet", "path": "parts", "mode": "read"}
    store = SourceCacheStore(tmp_path)

    build_input_snapshot(config, store=store, base_dir=tmp_path)
    snapshot = resolve_data_input(config, store=store, base_dir=tmp_path).collect()

    assert sorted(snapshot["id"].to_list()) == [0, 1]


def test_a_direct_scan_without_a_map_is_unaffected(tmp_path: Path) -> None:
    pq.write_table(pa.table({"id": [1], "s": pa.array([{"a": 1}])}), tmp_path / "maps.parquet")

    frame = resolve_data_input(
        _parquet_input(), store=SourceCacheStore(tmp_path), base_dir=tmp_path
    )

    assert frame.collect().to_dicts() == [{"id": 1, "s": {"a": 1}}]


def test_schema_only_resolution_reports_the_snapshot_s_shape(tmp_path: Path) -> None:
    _write_map_parquet(tmp_path / "maps.parquet")
    config = _parquet_input(mode="read")
    store = SourceCacheStore(tmp_path)

    with recording_schema_tiers():
        inferred = resolve_data_input(
            config, store=store, base_dir=tmp_path, schema_tier_node="policies"
        ).collect_schema()
    build_input_snapshot(config, store=store, base_dir=tmp_path)
    built = resolve_data_input(config, store=store, base_dir=tmp_path).collect_schema()

    assert inferred == built == pl.Schema({"id": pl.Int64, "m": _ENTRIES})


# ---------------------------------------------------------------------------
# Extension columns
# ---------------------------------------------------------------------------


class _UnknownExtension(pa.ExtensionType):
    def __init__(self) -> None:
        super().__init__(pa.int64(), "haute.test.unknown")

    def __arrow_ext_serialize__(self) -> bytes:
        return b""

    @classmethod
    def __arrow_ext_deserialize__(
        cls, storage_type: pa.DataType, serialized: bytes
    ) -> pa.ExtensionType:
        return cls()


# Run under Haute's Polars settings, as every process Haute starts is, with the
# extension unknown to both Polars and pyarrow there.
_EXTENSION_PATHS = """
import json, sys
from pathlib import Path
import haute.cli  # Haute's Polars settings, applied before Polars loads
import polars as pl
import pyarrow.parquet as pq
from haute._execution_context import ExecutionProfile
from haute._input_providers import build_input_snapshot, resolve_data_input
from haute._source_cache import SourceCacheBuildContext, SourceCacheIdentity, SourceCacheStore

root = Path(sys.argv[1])
store = SourceCacheStore(root)


class Arrow:
    def build(self, context):
        return iter([pq.read_table(root / "ext.parquet")])


direct = resolve_data_input(
    {"inputType": "file", "format": "parquet", "path": "ext.parquet"}, store=store, base_dir=root
)
ipc = {"inputType": "file", "format": "ipc", "path": "ext.arrow"}
build_input_snapshot(ipc, store=store, base_dir=root)
arrow = store.build(
    SourceCacheIdentity(provider="databricks", descriptor={"table": "c.s.ext"}),
    Arrow(),
    context=SourceCacheBuildContext(profile=ExecutionProfile.LAZY_SINK, build_class="bounded"),
).lazy_frame
snapshot = resolve_data_input(ipc, store=store, base_dir=root)
print(json.dumps({
    "direct": str(direct.collect_schema()["ext"]),
    "ipc_snapshot": str(snapshot.collect_schema()["ext"]),
    "arrow_snapshot": str(arrow.collect_schema()["ext"]),
    "values": arrow.collect()["ext"].to_list(),
}))
"""


def test_an_unknown_extension_column_loads_as_its_storage_type_on_every_path(
    tmp_path: Path,
) -> None:
    extension = _UnknownExtension()
    pa.register_extension_type(extension)
    try:
        storage = pa.array([1, 2], pa.int64())
        table = pa.table({"ext": pa.ExtensionArray.from_storage(extension, storage)})
        pq.write_table(table, tmp_path / "ext.parquet")
        feather.write_feather(table, tmp_path / "ext.arrow")
    finally:
        pa.unregister_extension_type("haute.test.unknown")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("POLARS_")}

    completed = subprocess.run(
        [sys.executable, "-c", _EXTENSION_PATHS, str(tmp_path)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )

    report = json.loads(completed.stdout.strip().splitlines()[-1])
    assert report == {
        "direct": "Int64",
        "ipc_snapshot": "Int64",
        "arrow_snapshot": "Int64",
        "values": [1, 2],
    }
