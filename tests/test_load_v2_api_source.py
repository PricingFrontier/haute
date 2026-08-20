"""Direct tests for the shared v2 apiInput runtime entry point.

``load_v2_api_source`` is the single function both the executor's source
builder and the generated/deploy code now call, so its behaviour (emit
checks, working→committed→direct resolution, uniform per-port return shape) is
the contract that keeps the two paths from drifting.
"""

from __future__ import annotations

import gc
import hashlib
import json
from pathlib import Path
from typing import Any

import orjson
import polars as pl
import pytest

from haute._api_input_schema import ApiInputSchemaError
from haute._json_flatten import _json_cache_dir, clear_json_cache
from haute._json_shred import (
    build_per_port_cache,
    is_per_port_cache_valid,
    load_per_port_cache,
    load_v2_api_source,
    read_per_port_cache_meta,
)


@pytest.fixture(autouse=True)
def _isolated_cache_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep production cache helpers inside each test's temporary project."""
    monkeypatch.chdir(tmp_path)


def _col(
    name: str,
    path: str,
    *,
    selected: bool = True,
    type_token: str = "int",
) -> dict[str, Any]:
    return {
        "name": name,
        "path": path,
        "type": type_token,
        "status": "Confirmed",
        "selected": selected,
        "levels": None,
    }


def _table(
    path: str, label: str, cols: list[dict[str, Any]], *, emit: bool = True
) -> dict[str, Any]:
    return {"path": path, "label": label, "emit": emit, "row_id_column": None, "columns": cols}


def _write(tmp_path: Path, records: list[dict[str, Any]]) -> Path:
    p = tmp_path / "data.json"
    p.write_text(json.dumps(records), encoding="utf-8")
    return p


def _build(data_path: Path, config: dict[str, Any], layer: str = "working") -> None:
    build_per_port_cache(str(data_path), config, _json_cache_dir(str(data_path), layer))


def _corrupt_parquet_data_page(path: Path) -> None:
    """Damage parquet payload bytes while leaving its footer schema readable."""
    import pyarrow.parquet as pq

    column = pq.ParquetFile(path).metadata.row_group(0).column(0)
    offset = column.data_page_offset + 1
    payload = bytearray(path.read_bytes())
    assert 0 <= offset < len(payload)
    payload[offset] ^= 0x01
    path.write_bytes(payload)


def _refresh_content_signature(cache_dir: Path, label: str) -> None:
    parquet = cache_dir / f"{label}.parquet"
    payload = parquet.read_bytes()
    meta_path = cache_dir / "meta.json"
    meta = orjson.loads(meta_path.read_bytes())
    entry = next(table for table in meta["tables"] if table["label"] == label)
    entry["content_signature"] = {
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    meta_path.write_bytes(orjson.dumps(meta))


def _deny_path_operation(
    monkeypatch: pytest.MonkeyPatch,
    denied_path: Path,
    operation: str,
) -> None:
    """Make one metadata-path operation fail without affecting other files."""
    original = getattr(Path, operation)

    def _permission_denied(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path == denied_path:
            raise PermissionError(f"permission denied: {path}")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, operation, _permission_denied)


def test_single_port_returns_one_entry_dict(tmp_path: Path) -> None:
    data = _write(tmp_path, [{"id": 1}, {"id": 2}])
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    _build(data, cfg)
    out = load_v2_api_source(str(data), cfg)
    assert isinstance(out, dict)
    assert list(out) == ["root"]
    assert isinstance(out["root"], pl.LazyFrame)
    assert out["root"].collect()["id"].to_list() == [1, 2]


def test_multi_port_returns_dict_in_schema_order(tmp_path: Path) -> None:
    data = _write(tmp_path, [{"id": 1, "drivers": [{"age": 30}, {"age": 40}]}])
    cfg = {
        "tables": [
            _table("$[:]", "root", [_col("id", "$[:].id")]),
            _table("$[:].drivers[:]", "drivers", [_col("age", "$[:].drivers[:].age")]),
        ]
    }
    _build(data, cfg)
    out = load_v2_api_source(str(data), cfg)
    assert isinstance(out, dict)
    assert list(out) == ["root", "drivers"]
    assert all(isinstance(frame, pl.LazyFrame) for frame in out.values())
    assert out["root"].collect()["id"].to_list() == [1]
    assert out["drivers"].collect()["age"].to_list() == [30, 40]


def test_demand_scoped_cache_load_opens_only_requested_port_and_columns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _write(
        tmp_path,
        [{"id": 1, "premium": 12.5, "drivers": [{"age": 30, "name": "A"}]}],
    )
    cfg = {
        "tables": [
            _table(
                "$[:]",
                "root",
                [
                    _col("id", "$[:].id"),
                    _col("premium", "$[:].premium", type_token="float"),
                ],
            ),
            _table(
                "$[:].drivers[:]",
                "drivers",
                [
                    _col("age", "$[:].drivers[:].age"),
                    _col("name", "$[:].drivers[:].name", type_token="str"),
                ],
            ),
        ]
    }
    _build(data, cfg)
    cache_dir = _json_cache_dir(str(data), "working")
    parquet_reads = {"root": 0, "drivers": 0}
    real_read_bytes = Path.read_bytes

    def _count_payload_reads(path: Path) -> bytes:
        if path.suffix == ".parquet" and path.parent == cache_dir:
            parquet_reads[path.stem] += 1
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", _count_payload_reads)

    out = load_v2_api_source(
        str(data),
        cfg,
        port_columns={"drivers": frozenset({"age"})},
    )

    assert list(out) == ["drivers"]
    assert out["drivers"].collect().to_dict(as_series=False) == {"age": [30]}
    assert "PROJECT 1/2 COLUMNS" in out["drivers"].explain(optimized=True)
    # Signature verification is chunked and Polars receives a stable file path;
    # no requested Parquet is materialised through Path.read_bytes().
    assert parquet_reads == {"root": 0, "drivers": 0}


def test_demand_scoped_direct_shred_builds_only_requested_port_and_columns(
    tmp_path: Path,
) -> None:
    data = _write(
        tmp_path,
        [{"id": 1, "premium": 12.5, "drivers": [{"age": 30, "name": "A"}]}],
    )
    cfg = {
        "tables": [
            _table(
                "$[:]",
                "root",
                [
                    _col("id", "$[:].id"),
                    _col("premium", "$[:].premium", type_token="float"),
                ],
            ),
            _table(
                "$[:].drivers[:]",
                "drivers",
                [
                    _col("age", "$[:].drivers[:].age"),
                    _col("name", "$[:].drivers[:].name", type_token="str"),
                ],
            ),
        ]
    }

    out = load_v2_api_source(
        str(data),
        cfg,
        port_columns={"drivers": frozenset({"age"})},
    )

    assert list(out) == ["drivers"]
    assert out["drivers"].collect().to_dict(as_series=False) == {"age": [30]}
    assert not _json_cache_dir(str(data), "working").exists()
    assert not _json_cache_dir(str(data), "committed").exists()


def test_cardinality_only_demand_retains_one_declared_carrier_column(
    tmp_path: Path,
) -> None:
    data = _write(
        tmp_path,
        [
            {"id": 1, "drivers": [{"age": 30}, {"age": 40}]},
            {"id": 2, "drivers": [{"age": 50}]},
        ],
    )
    cfg = {
        "tables": [
            _table("$[:]", "root", [_col("id", "$[:].id")]),
            _table(
                "$[:].drivers[:]",
                "drivers",
                [
                    _col("age", "$[:].drivers[:].age"),
                    _col("name", "$[:].drivers[:].name", type_token="str"),
                ],
            ),
        ]
    }
    _build(data, cfg)

    frame = load_v2_api_source(
        str(data),
        cfg,
        port_columns={"drivers": frozenset()},
    )["drivers"]

    assert frame.collect_schema().names() == ["age"]
    assert "PROJECT 1/2 COLUMNS" in frame.explain(optimized=True)
    assert frame.select(pl.len().alias("row_count")).collect().item() == 3


def test_demand_scoped_load_none_selects_the_complete_port(tmp_path: Path) -> None:
    data = _write(tmp_path, [{"id": 1, "drivers": [{"age": 30, "name": "A"}]}])
    cfg = {
        "tables": [
            _table("$[:]", "root", [_col("id", "$[:].id")]),
            _table(
                "$[:].drivers[:]",
                "drivers",
                [
                    _col("age", "$[:].drivers[:].age"),
                    _col("name", "$[:].drivers[:].name", type_token="str"),
                ],
            ),
        ]
    }

    frame = load_v2_api_source(str(data), cfg, port_columns={"drivers": None})["drivers"]

    assert frame.collect().to_dict(as_series=False) == {"age": [30], "name": ["A"]}


def test_cache_snapshot_vanishing_during_probe_falls_back_to_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _write(tmp_path, [{"id": 1}, {"id": 2}])
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    _build(data, cfg)

    monkeypatch.setattr(
        "haute._json_shred._snapshot_cache_artifact",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError("vanished")),
    )

    frame = load_v2_api_source(str(data), cfg)["root"]
    assert frame.collect().to_dict(as_series=False) == {"id": [1, 2]}


@pytest.mark.parametrize(
    ("port_columns", "message"),
    [
        ({}, "non-empty"),
        ({"missing": None}, "unknown"),
        ({"drivers": frozenset({1})}, "non-empty string"),
        ({"drivers": frozenset({"missing"})}, "missing"),
    ],
)
def test_demand_scoped_load_rejects_invalid_port_or_column_demands(
    tmp_path: Path,
    port_columns: dict[str, Any],
    message: str,
) -> None:
    data = _write(tmp_path, [{"id": 1, "drivers": [{"age": 30}]}])
    cfg = {
        "tables": [
            _table("$[:]", "root", [_col("id", "$[:].id")]),
            _table("$[:].drivers[:]", "drivers", [_col("age", "$[:].drivers[:].age")]),
        ]
    }

    with pytest.raises(ValueError, match=message):
        load_v2_api_source(str(data), cfg, port_columns=port_columns)


def test_demand_scoped_load_rejects_non_set_port_columns_value(tmp_path: Path) -> None:
    data = _write(tmp_path, [{"id": 1, "drivers": [{"age": 30}]}])
    cfg = {
        "tables": [
            _table("$[:]", "root", [_col("id", "$[:].id")]),
            _table("$[:].drivers[:]", "drivers", [_col("age", "$[:].drivers[:].age")]),
        ]
    }

    with pytest.raises(
        ValueError,
        match=r"port_columns\['drivers'\] must be None or a frozenset/set",
    ):
        load_v2_api_source(str(data), cfg, port_columns={"drivers": ["age"]})


def test_no_emit_tables_raises(tmp_path: Path) -> None:
    data = _write(tmp_path, [{"id": 1}])
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")], emit=False)]}
    with pytest.raises(RuntimeError, match="no emitting tables"):
        load_v2_api_source(str(data), cfg)


def test_emit_without_selected_columns_raises(tmp_path: Path) -> None:
    data = _write(tmp_path, [{"id": 1}])
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id", selected=False)])]}
    with pytest.raises(RuntimeError, match="selected columns"):
        load_v2_api_source(str(data), cfg)


def test_never_cached_input_shreds_in_memory_without_creating_cache(tmp_path: Path) -> None:
    data = _write(tmp_path, [{"id": 1}])
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}

    working = _json_cache_dir(str(data), "working")
    committed = _json_cache_dir(str(data), "committed")
    assert not working.exists()
    assert not committed.exists()

    out = load_v2_api_source(str(data), cfg)

    assert out["root"].collect().to_dict(as_series=False) == {"id": [1]}
    assert not working.exists()
    assert not committed.exists()


def test_never_cached_jsonl_shreds_in_memory(tmp_path: Path) -> None:
    data = tmp_path / "data.jsonl"
    data.write_text('{"id": 1}\n\n{"id": 2}\n', encoding="utf-8")
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}

    frame = load_v2_api_source(str(data), cfg)["root"].collect()

    assert frame["id"].to_list() == [1, 2]
    assert not _json_cache_dir(str(data), "working").exists()
    assert not _json_cache_dir(str(data), "committed").exists()


def test_stale_cache_after_cascade_shreds_ancestor_into_child_without_rebuild(
    tmp_path: Path,
) -> None:
    data = _write(
        tmp_path,
        [
            {"policy_id": 1001, "drivers": [{"age": 30}, {"age": 40}]},
            {"policy_id": 1002, "drivers": [{"age": 50}]},
        ],
    )
    cfg = {
        "tables": [
            _table("$[:]", "root", [_col("policy_id", "$[:].policy_id")]),
            _table(
                "$[:].drivers[:]",
                "drivers",
                [_col("age", "$[:].drivers[:].age")],
            ),
        ]
    }
    _build(data, cfg)
    working = _json_cache_dir(str(data), "working")
    old_meta = (working / "meta.json").read_bytes()

    # Cascading the root key down changes the post-schema cache fingerprint.
    cfg["tables"][1]["columns"].append(_col("policy_id", "$[:].policy_id"))
    assert not is_per_port_cache_valid(working, cfg, data_path=data)

    out = load_v2_api_source(str(data), cfg)

    assert out["drivers"].collect().to_dict(as_series=False) == {
        "age": [30, 40, 50],
        "policy_id": [1001, 1001, 1002],
    }
    assert (working / "meta.json").read_bytes() == old_meta
    assert not is_per_port_cache_valid(working, cfg, data_path=data)


def test_stale_cache_after_selecting_column_uses_current_schema(tmp_path: Path) -> None:
    data = _write(tmp_path, [{"id": 1, "premium": 12.5}])
    cfg = {
        "tables": [
            _table(
                "$[:]",
                "root",
                [
                    _col("id", "$[:].id"),
                    _col(
                        "premium",
                        "$[:].premium",
                        selected=False,
                        type_token="float",
                    ),
                ],
            )
        ]
    }
    _build(data, cfg)
    cfg["tables"][0]["columns"][1]["selected"] = True

    out = load_v2_api_source(str(data), cfg)

    assert out["root"].collect().to_dict(as_series=False) == {
        "id": [1],
        "premium": [12.5],
    }


def test_stale_cache_after_type_change_uses_current_schema(tmp_path: Path) -> None:
    data = _write(tmp_path, [{"premium": 1}, {"premium": 2}])
    cfg = {"tables": [_table("$[:]", "root", [_col("premium", "$[:].premium")])]}
    _build(data, cfg)
    cfg["tables"][0]["columns"][0]["type"] = "float"

    frame = load_v2_api_source(str(data), cfg)["root"].collect()

    assert frame.schema == pl.Schema({"premium": pl.Float64})
    assert frame["premium"].to_list() == [1.0, 2.0]


def test_stale_cache_after_column_rename_uses_current_schema(tmp_path: Path) -> None:
    data = _write(tmp_path, [{"id": 7}])
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    _build(data, cfg)
    cfg["tables"][0]["columns"][0]["name"] = "quote_id"

    frame = load_v2_api_source(str(data), cfg)["root"].collect()

    assert frame.to_dict(as_series=False) == {"quote_id": [7]}


@pytest.mark.parametrize("layer", ["working", "committed"])
def test_valid_cache_fast_path_does_not_reshred_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    layer: str,
) -> None:
    data = _write(tmp_path, [{"id": 9}])
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    _build(data, cfg, layer=layer)

    def _unexpected_reshred(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("a valid parquet cache must not re-shred the JSON source")

    monkeypatch.setattr("haute._json_shred._iter_records", _unexpected_reshred)

    frame = load_v2_api_source(str(data), cfg)["root"]

    assert isinstance(frame, pl.LazyFrame)
    assert frame.collect()["id"].to_list() == [9]


def test_lazy_cache_frame_stays_pinned_to_generation_across_data_rebuild(
    tmp_path: Path,
) -> None:
    data = _write(tmp_path, [{"id": 1}])
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    cache_dir = _json_cache_dir(str(data), "working")
    _build(data, cfg)
    generation_a = load_v2_api_source(str(data), cfg)["root"]

    data.write_text(json.dumps([{"id": 2}]), encoding="utf-8")
    build_per_port_cache(str(data), cfg, cache_dir)
    generation_b = load_v2_api_source(str(data), cfg)["root"]

    assert generation_a.collect().to_dict(as_series=False) == {"id": [1]}
    assert generation_b.collect().to_dict(as_series=False) == {"id": [2]}


def test_lazy_cache_frame_schema_cannot_leak_from_later_generation(tmp_path: Path) -> None:
    data = _write(tmp_path, [{"id": 1}])
    cfg_a = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    cache_dir = _json_cache_dir(str(data), "working")
    _build(data, cfg_a)
    generation_a = load_v2_api_source(str(data), cfg_a)["root"]

    data.write_text(json.dumps([{"id": 3}]), encoding="utf-8")
    cfg_b = {"tables": [_table("$[:]", "root", [_col("new_id", "$[:].id")])]}
    build_per_port_cache(str(data), cfg_b, cache_dir)
    generation_b = load_v2_api_source(str(data), cfg_b)["root"]

    assert generation_a.collect().to_dict(as_series=False) == {"id": [1]}
    assert generation_b.collect().to_dict(as_series=False) == {"new_id": [3]}


def test_lazy_cache_frame_snapshot_survives_clear_and_repeated_collect(
    tmp_path: Path,
) -> None:
    data = _write(tmp_path, [{"id": 1}, {"id": 2}])
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    _build(data, cfg)
    generation = load_v2_api_source(str(data), cfg)["root"]

    assert clear_json_cache(str(data), layer="working") is True

    expected = {"id": [1, 2]}
    assert generation.collect().to_dict(as_series=False) == expected
    assert generation.collect().to_dict(as_series=False) == expected


def test_derived_lazy_plan_keeps_snapshot_after_original_is_released(
    tmp_path: Path,
) -> None:
    data = _write(tmp_path, [{"id": 1}, {"id": 2}])
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    _build(data, cfg)
    generation = load_v2_api_source(str(data), cfg)["root"]
    derived = generation.select(pl.col("id").alias("generation_a_id")).with_columns(
        (pl.col("generation_a_id") * 2).alias("doubled")
    )

    del generation
    gc.collect()
    assert clear_json_cache(str(data), layer="working") is True

    expected = {"generation_a_id": [1, 2], "doubled": [2, 4]}
    for _ in range(2):
        collected = derived.collect()
        assert collected.schema == pl.Schema({"generation_a_id": pl.Int64, "doubled": pl.Int64})
        assert collected.to_dict(as_series=False) == expected


def test_cache_probe_keeps_parquets_file_backed_and_collects_from_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _write(tmp_path, [{"id": 1, "drivers": [{"age": 30}, {"age": 40}]}])
    cfg = {
        "tables": [
            _table("$[:]", "root", [_col("id", "$[:].id")]),
            _table("$[:].drivers[:]", "drivers", [_col("age", "$[:].drivers[:].age")]),
        ]
    }
    cache_dir = _json_cache_dir(str(data), "working")
    _build(data, cfg)
    cache_paths = {
        cache_dir / "root.parquet",
        cache_dir / "drivers.parquet",
    }
    scan_sources: list[Any] = []
    real_read_bytes = Path.read_bytes
    real_scan_parquet = pl.scan_parquet

    def _reject_parquet_read_bytes(path: Path) -> bytes:
        if path.suffix == ".parquet":
            pytest.fail(f"Parquet payload was materialised with read_bytes(): {path}")
        return real_read_bytes(path)

    def _capture_scan_source(source: Any, *args: Any, **kwargs: Any) -> pl.LazyFrame:
        scan_sources.append(source)
        return real_scan_parquet(source, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", _reject_parquet_read_bytes)
    monkeypatch.setattr("haute._json_shred.pl.scan_parquet", _capture_scan_source)

    frames = load_v2_api_source(str(data), cfg)
    assert clear_json_cache(str(data), layer="working") is True
    for _ in range(2):
        assert frames["root"].collect().to_dict(as_series=False) == {"id": [1]}
        assert frames["drivers"].collect().to_dict(as_series=False) == {"age": [30, 40]}

    assert len(scan_sources) == len(cache_paths)
    assert all(isinstance(source, Path) for source in scan_sources)
    assert all(source not in cache_paths for source in scan_sources)
    assert all(".runtime-snapshots" in source.parts for source in scan_sources)


def test_cache_probe_stream_copy_fallback_stays_file_backed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _write(tmp_path, [{"id": 1}, {"id": 2}])
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    _build(data, cfg)
    scan_sources: list[Any] = []
    real_read_bytes = Path.read_bytes
    real_scan_parquet = pl.scan_parquet

    def _hard_link_unavailable(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("hard links unavailable")

    def _reject_parquet_read_bytes(path: Path) -> bytes:
        if path.suffix == ".parquet":
            pytest.fail(f"Parquet payload was materialised with read_bytes(): {path}")
        return real_read_bytes(path)

    def _capture_scan_source(source: Any, *args: Any, **kwargs: Any) -> pl.LazyFrame:
        scan_sources.append(source)
        return real_scan_parquet(source, *args, **kwargs)

    monkeypatch.setattr("haute._json_shred.os.link", _hard_link_unavailable)
    monkeypatch.setattr(Path, "read_bytes", _reject_parquet_read_bytes)
    monkeypatch.setattr("haute._json_shred.pl.scan_parquet", _capture_scan_source)

    frame = load_v2_api_source(str(data), cfg)["root"]
    assert clear_json_cache(str(data), layer="working") is True

    assert frame.collect().to_dict(as_series=False) == {"id": [1, 2]}
    assert len(scan_sources) == 1
    assert isinstance(scan_sources[0], Path)
    assert scan_sources[0].exists()
    assert list(scan_sources[0].parent.glob("*.tmp")) == []


def test_managed_executions_share_then_release_file_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _write(tmp_path, [{"id": 1}, {"id": 2}])
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    _build(data, cfg)
    scan_sources: list[Path] = []
    real_scan_parquet = pl.scan_parquet

    class _Context:
        def __init__(self) -> None:
            self.cleanups: list[Any] = []

        def add_cleanup(self, callback: Any) -> None:
            self.cleanups.append(callback)

        def release(self) -> None:
            for callback in reversed(self.cleanups):
                callback()
            self.cleanups.clear()

    first_context = _Context()
    second_context = _Context()
    active_context = [first_context]

    def _capture_scan_source(source: Any, *args: Any, **kwargs: Any) -> pl.LazyFrame:
        assert isinstance(source, Path)
        scan_sources.append(source)
        return real_scan_parquet(source, *args, **kwargs)

    monkeypatch.setattr(
        "haute._json_shred.current_execution_context",
        lambda: active_context[0],
    )
    monkeypatch.setattr("haute._json_shred.pl.scan_parquet", _capture_scan_source)

    first_frame = load_v2_api_source(str(data), cfg)["root"]
    active_context[0] = second_context
    second_frame = load_v2_api_source(str(data), cfg)["root"]

    assert scan_sources[0] == scan_sources[1]
    assert first_frame.collect().to_dict(as_series=False) == {"id": [1, 2]}
    first_context.release()
    assert scan_sources[0].exists()
    assert second_frame.collect().to_dict(as_series=False) == {"id": [1, 2]}
    second_context.release()
    assert not scan_sources[0].exists()


def test_validity_probe_releases_unowned_file_snapshot(tmp_path: Path) -> None:
    data = _write(tmp_path, [{"id": 1}])
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    _build(data, cfg)
    cache_dir = _json_cache_dir(str(data), "working")

    assert is_per_port_cache_valid(cache_dir, cfg, data_path=data) is True
    snapshot_parent = cache_dir.parent / ".runtime-snapshots"
    assert list(snapshot_parent.rglob("*.parquet")) == []


def test_data_page_corrupt_working_cache_falls_through_to_committed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _write(tmp_path, [{"id": 9}])
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    _build(data, cfg, layer="working")
    _build(data, cfg, layer="committed")
    working = _json_cache_dir(str(data), "working") / "root.parquet"
    _corrupt_parquet_data_page(working)
    assert pl.scan_parquet(working).collect_schema() == pl.Schema({"id": pl.Int64})
    with pytest.raises(pl.exceptions.ComputeError):
        pl.read_parquet(working)

    def _unexpected_reshred(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("the valid committed cache should serve after rejecting working")

    monkeypatch.setattr("haute._json_shred._iter_records", _unexpected_reshred)

    frame = load_v2_api_source(str(data), cfg)["root"].collect()

    assert frame["id"].to_list() == [9]


def test_data_page_corrupt_both_caches_fall_back_direct_without_writes(
    tmp_path: Path,
) -> None:
    data = _write(tmp_path, [{"id": 9}])
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    damaged_bytes: dict[str, bytes] = {}
    for layer in ("working", "committed"):
        _build(data, cfg, layer=layer)
        parquet = _json_cache_dir(str(data), layer) / "root.parquet"
        _corrupt_parquet_data_page(parquet)
        assert pl.scan_parquet(parquet).collect_schema() == pl.Schema({"id": pl.Int64})
        damaged_bytes[layer] = parquet.read_bytes()

    frame = load_v2_api_source(str(data), cfg)["root"].collect()

    assert frame["id"].to_list() == [9]
    for layer, expected_bytes in damaged_bytes.items():
        parquet = _json_cache_dir(str(data), layer) / "root.parquet"
        assert parquet.read_bytes() == expected_bytes


def test_data_page_corrupt_cache_is_invalid_and_build_repairs_it(tmp_path: Path) -> None:
    data = _write(tmp_path, [{"id": 9}])
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    cache_dir = _json_cache_dir(str(data), "working")
    _build(data, cfg)
    parquet = cache_dir / "root.parquet"
    _corrupt_parquet_data_page(parquet)
    damaged_bytes = parquet.read_bytes()
    assert pl.scan_parquet(parquet).collect_schema() == pl.Schema({"id": pl.Int64})

    assert is_per_port_cache_valid(cache_dir, cfg, data_path=data) is False

    build_per_port_cache(str(data), cfg, cache_dir)

    assert parquet.read_bytes() != damaged_bytes
    assert is_per_port_cache_valid(cache_dir, cfg, data_path=data) is True
    assert pl.read_parquet(parquet)["id"].to_list() == [9]


def test_manifest_without_parquet_content_signature_is_invalid(tmp_path: Path) -> None:
    data = _write(tmp_path, [{"id": 9}])
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    cache_dir = _json_cache_dir(str(data), "working")
    _build(data, cfg)
    meta_path = cache_dir / "meta.json"
    meta = orjson.loads(meta_path.read_bytes())
    meta["tables"][0].pop("content_signature", None)
    meta_path.write_bytes(orjson.dumps(meta))
    unsigned_meta_bytes = meta_path.read_bytes()

    assert is_per_port_cache_valid(cache_dir, cfg, data_path=data) is False

    frame = load_v2_api_source(str(data), cfg)["root"].collect()

    assert frame["id"].to_list() == [9]
    assert meta_path.read_bytes() == unsigned_meta_bytes

    build_per_port_cache(str(data), cfg, cache_dir)

    repaired_meta = orjson.loads(meta_path.read_bytes())
    assert "content_signature" in repaired_meta["tables"][0]
    assert is_per_port_cache_valid(cache_dir, cfg, data_path=data) is True


@pytest.mark.parametrize("damage", ["missing_table", "duplicate_table"])
def test_manifest_table_entries_must_match_emitting_tables_exactly(
    tmp_path: Path,
    damage: str,
) -> None:
    data = _write(tmp_path, [{"id": 9}])
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    cache_dir = _json_cache_dir(str(data), "working")
    _build(data, cfg)
    meta_path = cache_dir / "meta.json"
    meta = orjson.loads(meta_path.read_bytes())
    if damage == "missing_table":
        meta["tables"] = []
    else:
        meta["tables"].append(dict(meta["tables"][0]))
    meta_path.write_bytes(orjson.dumps(meta))

    assert is_per_port_cache_valid(cache_dir, cfg, data_path=data) is False


def test_stale_working_cache_falls_through_to_valid_committed_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _write(tmp_path, [{"id": 13}])
    old_cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    current_cfg = {"tables": [_table("$[:]", "root", [_col("quote_id", "$[:].id")])]}
    _build(data, old_cfg, layer="working")
    _build(data, current_cfg, layer="committed")

    def _unexpected_reshred(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("the valid committed cache should serve after stale working")

    monkeypatch.setattr("haute._json_shred._iter_records", _unexpected_reshred)

    frame = load_v2_api_source(str(data), current_cfg)["root"].collect()
    assert frame.to_dict(as_series=False) == {"quote_id": [13]}


def test_valid_working_cache_wins_when_both_layers_are_valid(tmp_path: Path) -> None:
    data = _write(tmp_path, [{"id": 1}])
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    _build(data, cfg, layer="working")
    _build(data, cfg, layer="committed")
    working_dir = _json_cache_dir(str(data), "working")
    committed_dir = _json_cache_dir(str(data), "committed")
    working = working_dir / "root.parquet"
    committed = committed_dir / "root.parquet"
    pl.DataFrame({"id": [101]}, schema={"id": pl.Int64}).write_parquet(working)
    pl.DataFrame({"id": [202]}, schema={"id": pl.Int64}).write_parquet(committed)
    _refresh_content_signature(working_dir, "root")
    _refresh_content_signature(committed_dir, "root")

    frame = load_v2_api_source(str(data), cfg)["root"].collect()

    assert frame["id"].to_list() == [101]


@pytest.mark.parametrize("damage", ["corrupt", "wrong_name", "wrong_dtype"])
def test_unusable_working_cache_falls_through_to_valid_committed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    damage: str,
) -> None:
    data = _write(tmp_path, [{"id": 17, "amount": 3}])
    cfg = {
        "tables": [
            _table(
                "$[:]",
                "root",
                [_col("id", "$[:].id"), _col("amount", "$[:].amount")],
            )
        ]
    }
    _build(data, cfg, layer="working")
    _build(data, cfg, layer="committed")
    working_dir = _json_cache_dir(str(data), "working")
    working_parquet = working_dir / "root.parquet"
    if damage == "corrupt":
        working_parquet.write_bytes(b"not parquet")
    elif damage == "wrong_name":
        pl.DataFrame({"wrong_name": [999], "amount": [3]}).write_parquet(working_parquet)
    else:
        pl.DataFrame({"id": ["999"], "amount": [3]}).write_parquet(working_parquet)
    _refresh_content_signature(working_dir, "root")

    def _unexpected_reshred(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("the valid committed cache should serve after rejecting working")

    monkeypatch.setattr("haute._json_shred._iter_records", _unexpected_reshred)

    frame = load_v2_api_source(str(data), cfg)["root"].collect()

    assert frame.to_dict(as_series=False) == {"id": [17], "amount": [3]}


@pytest.mark.parametrize("damage", ["corrupt", "wrong_name", "wrong_dtype"])
def test_cache_build_repairs_unreadable_or_schema_incompatible_parquet(
    tmp_path: Path,
    damage: str,
) -> None:
    data = _write(tmp_path, [{"id": 17, "amount": 3}])
    cfg = {
        "tables": [
            _table(
                "$[:]",
                "root",
                [_col("id", "$[:].id"), _col("amount", "$[:].amount")],
            )
        ]
    }
    cache_dir = _json_cache_dir(str(data), "working")
    _build(data, cfg)
    parquet_path = cache_dir / "root.parquet"
    if damage == "corrupt":
        parquet_path.write_bytes(b"not parquet")
    elif damage == "wrong_name":
        pl.DataFrame({"renamed": [17], "amount": [3]}).write_parquet(parquet_path)
    else:
        pl.DataFrame({"id": ["17"], "amount": [3]}).write_parquet(parquet_path)
    _refresh_content_signature(cache_dir, "root")

    assert is_per_port_cache_valid(cache_dir, cfg, data_path=data) is False

    build_per_port_cache(str(data), cfg, cache_dir)

    assert is_per_port_cache_valid(cache_dir, cfg, data_path=data) is True
    assert pl.read_parquet(parquet_path).to_dict(as_series=False) == {
        "id": [17],
        "amount": [3],
    }


def test_column_order_only_change_reuses_cache_and_projects_current_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _write(tmp_path, [{"id": 17, "amount": 3}])
    cfg = {
        "tables": [
            _table(
                "$[:]",
                "root",
                [_col("id", "$[:].id"), _col("amount", "$[:].amount")],
            )
        ]
    }
    cache_dir = _json_cache_dir(str(data), "working")
    _build(data, cfg)
    meta_path = cache_dir / "meta.json"
    parquet_path = cache_dir / "root.parquet"
    original_meta = meta_path.read_bytes()
    original_parquet = parquet_path.read_bytes()

    cfg["tables"][0]["columns"].reverse()
    assert is_per_port_cache_valid(cache_dir, cfg, data_path=data) is True

    def _unexpected_reshred(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("an order-only schema edit should reuse the existing cache")

    monkeypatch.setattr("haute._json_shred._iter_records", _unexpected_reshred)

    build_per_port_cache(str(data), cfg, cache_dir)
    frame = load_v2_api_source(str(data), cfg)["root"].collect()

    assert meta_path.read_bytes() == original_meta
    assert parquet_path.read_bytes() == original_parquet
    assert frame.columns == ["amount", "id"]
    assert frame.to_dict(as_series=False) == {"amount": [3], "id": [17]}


def test_corrupt_working_and_committed_caches_fall_back_to_direct_shred(
    tmp_path: Path,
) -> None:
    data = _write(tmp_path, [{"id": 23}])
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    for layer in ("working", "committed"):
        _build(data, cfg, layer=layer)
        cache_dir = _json_cache_dir(str(data), layer)
        (cache_dir / "root.parquet").write_bytes(b"not parquet")
        _refresh_content_signature(cache_dir, "root")

    frame = load_v2_api_source(str(data), cfg)["root"].collect()

    assert frame["id"].to_list() == [23]
    for layer in ("working", "committed"):
        cache_dir = _json_cache_dir(str(data), layer)
        assert (cache_dir / "root.parquet").read_bytes() == b"not parquet"


def test_unreadable_cache_candidate_is_logged_before_direct_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _write(tmp_path, [{"id": 23}])
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    _build(data, cfg)
    cache_dir = _json_cache_dir(str(data), "working")
    (cache_dir / "root.parquet").write_bytes(b"not parquet")
    _refresh_content_signature(cache_dir, "root")
    warnings: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        "haute._json_shred.logger.warning",
        lambda event, **fields: warnings.append((event, fields)),
    )

    frame = load_v2_api_source(str(data), cfg)["root"].collect()

    assert frame["id"].to_list() == [23]
    assert [event for event, _fields in warnings] == ["json_shred_cache_candidate_rejected"]
    assert warnings[0][1]["reason"] == "unreadable_parquet"


def test_uncached_direct_shred_excludes_non_emitting_sibling(tmp_path: Path) -> None:
    data = _write(tmp_path, [{"id": 1, "ignored": 2}])
    cfg = {
        "tables": [
            _table("$[:]", "root", [_col("id", "$[:].id")]),
            _table(
                "$[:]",
                "ignored",
                [_col("ignored", "$[:].ignored")],
                emit=False,
            ),
        ]
    }

    out = load_v2_api_source(str(data), cfg)

    assert list(out) == ["root"]
    assert out["root"].collect()["id"].to_list() == [1]


def test_uncached_direct_shred_logs_every_skipped_record_and_child_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = tmp_path / "data.json"
    data.write_text(
        json.dumps([{"items": [{"value": 1}, 2]}, 7]),
        encoding="utf-8",
    )
    cfg = {
        "tables": [
            _table(
                "$[:].items[:]",
                "items",
                [_col("value", "$[:].items[:].value")],
            )
        ]
    }
    warnings: list[tuple[str, dict[str, Any]]] = []

    def _capture_warning(event: str, **fields: Any) -> None:
        warnings.append((event, fields))

    monkeypatch.setattr("haute._json_shred.logger.warning", _capture_warning)

    frame = load_v2_api_source(str(data), cfg)["items"].collect()

    assert frame["value"].to_list() == [1]
    assert warnings == [
        (
            "json_shred_direct_records_skipped",
            {
                "data_path": str(data),
                "skipped_records": 1,
                "skipped_rows_by_table": {"items": 1},
            },
        )
    ]


def test_uncached_scalar_array_ignores_empty_arrays_and_broadcasts_ancestor(
    tmp_path: Path,
) -> None:
    data = _write(
        tmp_path,
        [
            {"quote_id": 1, "tags": ["new", "renewal"]},
            {"quote_id": 2, "tags": []},
        ],
    )
    cfg = {
        "tables": [
            _table(
                "$[:].tags[:]",
                "tags",
                [
                    _col("value", "$[:].tags[:].$value", type_token="str"),
                    _col("quote_id", "$[:].quote_id"),
                ],
            )
        ]
    }

    frame = load_v2_api_source(str(data), cfg)["tags"].collect()

    assert frame.to_dict(as_series=False) == {
        "value": ["new", "renewal"],
        "quote_id": [1, 1],
    }


def test_uncached_all_empty_array_preserves_declared_frame_schema(tmp_path: Path) -> None:
    data = _write(tmp_path, [{"tags": []}])
    cfg = {
        "tables": [
            _table(
                "$[:].tags[:]",
                "tags",
                [_col("value", "$[:].tags[:].$value", type_token="str")],
            )
        ]
    }

    frame = load_v2_api_source(str(data), cfg)["tags"].collect()

    assert frame.schema == pl.Schema({"value": pl.String})
    assert frame.height == 0


def test_uncached_malformed_schema_raises_typed_error(tmp_path: Path) -> None:
    data = _write(tmp_path, [{"id": 1}])

    with pytest.raises(ApiInputSchemaError, match=r"tables\[0\].*dict"):
        load_v2_api_source(str(data), {"tables": ["not-a-table"]})


def test_uncached_declared_type_mismatch_names_column_and_type(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _write(tmp_path, [{"id": "not-an-int"}])
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    info_events: list[str] = []
    monkeypatch.setattr(
        "haute._json_shred.logger.info",
        lambda event, **_fields: info_events.append(event),
    )

    with pytest.raises(ApiInputSchemaError, match=r"column 'id'.*declared type 'int'"):
        load_v2_api_source(str(data), cfg)
    assert "json_shred_loaded_direct" not in info_events


def test_uncached_malformed_json_surfaces_decode_error(tmp_path: Path) -> None:
    data = tmp_path / "data.json"
    data.write_text('[{"id": 1},', encoding="utf-8")
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}

    with pytest.raises(orjson.JSONDecodeError):
        load_v2_api_source(str(data), cfg)


def test_missing_raw_source_stays_a_file_not_found_error(tmp_path: Path) -> None:
    data = tmp_path / "missing.json"
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}

    with pytest.raises(FileNotFoundError, match="missing.json"):
        load_v2_api_source(str(data), cfg)


def test_load_per_port_cache_skips_non_emit_tables(tmp_path: Path) -> None:
    data = _write(tmp_path, [{"id": 1, "x": 2}])
    cfg = {
        "tables": [
            _table("$[:]", "root", [_col("id", "$[:].id")]),
            _table("$[:]", "extra", [_col("x", "$[:].x")], emit=False),
        ]
    }
    _build(data, cfg)
    frames = load_per_port_cache(_json_cache_dir(str(data), "working"), cfg)
    assert set(frames) == {"root"}  # the emit:false table is not loaded


def test_load_per_port_cache_rejects_wrong_schema_mode(tmp_path: Path) -> None:
    data = _write(tmp_path, [{"id": 1}])
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    _build(data, cfg)
    cache_dir = _json_cache_dir(str(data), "working")
    meta_path = cache_dir / "meta.json"
    meta = orjson.loads(meta_path.read_bytes())
    meta["schema_mode"] = "unexpected"
    meta_path.write_bytes(orjson.dumps(meta))

    assert load_per_port_cache(cache_dir, cfg) == {}


def test_load_per_port_cache_rejects_schema_fingerprint_mismatch(
    tmp_path: Path,
) -> None:
    data = _write(tmp_path, [{"id": 1, "alternate": 2}])
    cached_cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    changed_path_cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].alternate")])]}
    _build(data, cached_cfg)
    cache_dir = _json_cache_dir(str(data), "working")

    # Label, output name, and declared dtype are deliberately unchanged, so
    # accepting this cache would silently serve values from the old JSON path.
    assert load_per_port_cache(cache_dir, changed_path_cfg) == {}


def test_load_per_port_cache_returns_empty_for_signed_unreadable_member(
    tmp_path: Path,
) -> None:
    data = _write(tmp_path, [{"id": 1, "drivers": [{"age": 30}]}])
    cfg = {
        "tables": [
            _table("$[:]", "root", [_col("id", "$[:].id")]),
            _table("$[:].drivers[:]", "drivers", [_col("age", "$[:].drivers[:].age")]),
        ]
    }
    _build(data, cfg)
    cache_dir = _json_cache_dir(str(data), "working")
    (cache_dir / "drivers.parquet").write_bytes(b"not parquet")
    _refresh_content_signature(cache_dir, "drivers")

    # Loading a bundle is all-or-empty: a later invalid member must neither
    # expose the valid root frame nor leak the Parquet reader's exception.
    assert load_per_port_cache(cache_dir, cfg) == {}


def test_permission_denied_working_meta_falls_through_to_committed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _write(tmp_path, [{"id": 31}])
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    _build(data, cfg, layer="working")
    _build(data, cfg, layer="committed")
    working_meta = _json_cache_dir(str(data), "working") / "meta.json"
    _deny_path_operation(monkeypatch, working_meta, "read_bytes")

    def _unexpected_reshred(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("a valid committed cache must serve when working meta is unreadable")

    monkeypatch.setattr("haute._json_shred._iter_records", _unexpected_reshred)

    frame = load_v2_api_source(str(data), cfg)["root"].collect()

    assert frame.to_dict(as_series=False) == {"id": [31]}


def test_permission_denied_working_meta_falls_back_to_direct_shred(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _write(tmp_path, [{"id": 37}])
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    _build(data, cfg, layer="working")
    working_meta = _json_cache_dir(str(data), "working") / "meta.json"
    _deny_path_operation(monkeypatch, working_meta, "read_bytes")

    frame = load_v2_api_source(str(data), cfg)["root"].collect()

    assert frame.to_dict(as_series=False) == {"id": [37]}


def test_permission_denied_meta_is_invalid_and_unloadable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _write(tmp_path, [{"id": 41}])
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    cache_dir = _json_cache_dir(str(data), "working")
    _build(data, cfg)
    _deny_path_operation(monkeypatch, cache_dir / "meta.json", "read_bytes")

    assert is_per_port_cache_valid(cache_dir, cfg, data_path=data) is False
    assert read_per_port_cache_meta(cache_dir) is None
    assert load_per_port_cache(cache_dir, cfg) == {}


def test_is_per_port_cache_valid_false_states(tmp_path: Path) -> None:
    data = _write(tmp_path, [{"id": 1}])
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    # No meta at all.
    empty = tmp_path / "empty"
    empty.mkdir()
    assert is_per_port_cache_valid(empty, cfg, data_path=data) is False
    # Wrong schema_mode.
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "meta.json").write_bytes(
        orjson.dumps({"schema_mode": "unexpected", "schema_fingerprint": "x", "tables": []}),
    )
    assert is_per_port_cache_valid(bad, cfg, data_path=data) is False
    # Byte-corrupt meta (interrupted external write).
    corrupt = tmp_path / "corrupt"
    corrupt.mkdir()
    (corrupt / "meta.json").write_bytes(b"{ not json")
    assert is_per_port_cache_valid(corrupt, cfg, data_path=data) is False
    # Valid JSON but not an object.
    nondict = tmp_path / "nondict"
    nondict.mkdir()
    (nondict / "meta.json").write_bytes(orjson.dumps([1, 2, 3]))
    assert is_per_port_cache_valid(nondict, cfg, data_path=data) is False


def test_is_per_port_cache_valid_rejects_non_string_label_on_emitting_table(
    tmp_path: Path,
) -> None:
    """An emitting table whose label isn't a string can't map to a parquet
    filename — validity is False rather than a crash or a silent pass."""
    data = _write(tmp_path, [{"id": 1}])
    good = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    cache_dir = tmp_path / "cache"
    build_per_port_cache(str(data), good, cache_dir)

    bad = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    bad["tables"][0]["label"] = 123
    # Force the fingerprint to match the built cache so the label arm is the
    # deciding check, not the fingerprint.
    from haute._json_shred import _v2_fingerprint

    if _v2_fingerprint(bad) != _v2_fingerprint(good):
        meta_path = cache_dir / "meta.json"
        meta = orjson.loads(meta_path.read_bytes())
        meta["schema_fingerprint"] = _v2_fingerprint(bad)
        meta_path.write_bytes(orjson.dumps(meta))
    assert is_per_port_cache_valid(cache_dir, bad, data_path=data) is False


def test_load_per_port_cache_rejects_non_string_label(tmp_path: Path) -> None:
    data = _write(tmp_path, [{"id": 1}])
    good = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    cache_dir = tmp_path / "cache"
    build_per_port_cache(str(data), good, cache_dir)
    weird = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    weird["tables"][0]["label"] = 123
    with pytest.raises(ApiInputSchemaError, match="label.*non-empty string"):
        load_per_port_cache(cache_dir, weird)


def test_is_per_port_cache_valid_tolerates_non_dict_tables_and_columns(tmp_path: Path) -> None:
    """A malformed on-disk config yields 'invalid' gracefully, never a raise.

    ``_v2_fingerprint`` now fails LOUD on a non-dict table/column (so two
    distinct malformed configs can't silently collapse to one fingerprint),
    but ``is_per_port_cache_valid`` catches that and reports the cache invalid
    — preserving the bool contract that GET /status and other direct validity
    probes depend on.
    """
    data = _write(tmp_path, [{"id": 1}])
    real = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    _build(data, real)
    cache_dir = _json_cache_dir(str(data), "working")
    weird = {
        "tables": [
            "not-a-dict",
            {
                "path": "$[:]",
                "label": "root",
                "emit": True,
                "columns": ["not-a-col", {"name": "id", "path": "$[:].id", "type": "int"}],
            },
        ]
    }
    # Fingerprint of the weird config won't match the real cache → invalid,
    # but the non-dict guards must not raise.
    assert is_per_port_cache_valid(cache_dir, weird, data_path=data) is False


@pytest.mark.parametrize(
    ("bad_config", "error_match"),
    [
        ({"tables": None}, "tables.*list"),
        (
            {
                "tables": [
                    {
                        "path": "$[:]",
                        "label": "root",
                        "emit": True,
                        "row_id_column": None,
                        "columns": 1,
                    }
                ]
            },
            "columns.*list",
        ),
    ],
    ids=["null-tables", "non-list-columns"],
)
def test_malformed_container_shapes_are_invalid_for_probe_but_loud_at_boundaries(
    tmp_path: Path,
    bad_config: dict[str, Any],
    error_match: str,
) -> None:
    data = _write(tmp_path, [{"id": 43}])
    good = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    cache_dir = _json_cache_dir(str(data), "working")
    _build(data, good)

    assert is_per_port_cache_valid(cache_dir, bad_config, data_path=data) is False
    with pytest.raises(ApiInputSchemaError, match=error_match):
        load_v2_api_source(str(data), bad_config)
    with pytest.raises(ApiInputSchemaError, match=error_match):
        build_per_port_cache(str(data), bad_config, tmp_path / "invalid-cache")


def test_read_meta_missing_file_returns_none(tmp_path: Path) -> None:
    cache_dir = tmp_path / "no-such-dir"
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}

    assert read_per_port_cache_meta(cache_dir) is None
    assert load_per_port_cache(cache_dir, cfg) == {}


def test_falls_back_to_committed_layer(tmp_path: Path) -> None:
    data = _write(tmp_path, [{"id": 7}])
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    # Only the committed layer is populated (the deploy / fresh-server case).
    _build(data, cfg, layer="committed")
    out = load_v2_api_source(str(data), cfg)
    assert isinstance(out, dict)
    assert list(out) == ["root"]
    assert isinstance(out["root"], pl.LazyFrame)
    assert out["root"].collect()["id"].to_list() == [7]
