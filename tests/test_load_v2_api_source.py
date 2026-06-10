"""Direct tests for the shared v2 apiInput runtime entry point.

``load_v2_api_source`` is the single function both the executor's source
builder and the generated/deploy code now call, so its behaviour (emit
checks, working→committed cache resolution, single/multi-port return) is the
contract that keeps the two paths from drifting.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import orjson
import polars as pl
import pytest

from haute._json_flatten import _json_cache_dir
from haute._json_shred import (
    build_per_port_cache,
    is_per_port_cache_valid,
    load_per_port_cache,
    load_v2_api_source,
    read_per_port_cache_meta,
)


def _col(name: str, path: str, *, selected: bool = True) -> dict[str, Any]:
    return {
        "name": name,
        "path": path,
        "type": "int",
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


def test_single_port_returns_bare_lazyframe(tmp_path: Path) -> None:
    data = _write(tmp_path, [{"id": 1}, {"id": 2}])
    cfg = {"tables": [_table("$[*]", "root", [_col("id", "$[*].id")])]}
    _build(data, cfg)
    out = load_v2_api_source(str(data), cfg)
    assert isinstance(out, pl.LazyFrame)
    assert out.collect()["id"].to_list() == [1, 2]


def test_multi_port_returns_dict_in_schema_order(tmp_path: Path) -> None:
    data = _write(tmp_path, [{"id": 1, "drivers": [{"age": 30}, {"age": 40}]}])
    cfg = {
        "tables": [
            _table("$[*]", "root", [_col("id", "$[*].id")]),
            _table("$[*].drivers[*]", "drivers", [_col("age", "$[*].drivers[*].age")]),
        ]
    }
    _build(data, cfg)
    out = load_v2_api_source(str(data), cfg)
    assert isinstance(out, dict)
    assert list(out) == ["root", "drivers"]
    assert out["drivers"].collect()["age"].to_list() == [30, 40]


def test_no_emit_tables_raises(tmp_path: Path) -> None:
    data = _write(tmp_path, [{"id": 1}])
    cfg = {"tables": [_table("$[*]", "root", [_col("id", "$[*].id")], emit=False)]}
    with pytest.raises(RuntimeError, match="no emitting tables"):
        load_v2_api_source(str(data), cfg)


def test_emit_without_selected_columns_raises(tmp_path: Path) -> None:
    data = _write(tmp_path, [{"id": 1}])
    cfg = {"tables": [_table("$[*]", "root", [_col("id", "$[*].id", selected=False)])]}
    with pytest.raises(RuntimeError, match="selected columns"):
        load_v2_api_source(str(data), cfg)


def test_missing_cache_raises_with_actionable_message(tmp_path: Path) -> None:
    data = _write(tmp_path, [{"id": 1}])
    cfg = {"tables": [_table("$[*]", "root", [_col("id", "$[*].id")])]}
    # No cache built in either layer.
    with pytest.raises(RuntimeError, match="Cache as Parquet"):
        load_v2_api_source(str(data), cfg)


def test_load_per_port_cache_skips_non_emit_tables(tmp_path: Path) -> None:
    data = _write(tmp_path, [{"id": 1, "x": 2}])
    cfg = {
        "tables": [
            _table("$[*]", "root", [_col("id", "$[*].id")]),
            _table("$[*]", "extra", [_col("x", "$[*].x")], emit=False),
        ]
    }
    _build(data, cfg)
    frames = load_per_port_cache(_json_cache_dir(str(data), "working"), cfg)
    assert set(frames) == {"root"}  # the emit:false table is not loaded


def test_is_per_port_cache_valid_false_states(tmp_path: Path) -> None:
    cfg = {"tables": [_table("$[*]", "root", [_col("id", "$[*].id")])]}
    # No meta at all.
    empty = tmp_path / "empty"
    empty.mkdir()
    assert is_per_port_cache_valid(empty, cfg) is False
    # Wrong schema_mode.
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "meta.json").write_bytes(
        orjson.dumps({"schema_mode": "v1", "schema_fingerprint": "x", "tables": []}),
    )
    assert is_per_port_cache_valid(bad, cfg) is False


def test_is_per_port_cache_valid_tolerates_non_dict_tables_and_columns(tmp_path: Path) -> None:
    """The fingerprint computation defensively skips non-dict tables/columns."""
    data = _write(tmp_path, [{"id": 1}])
    real = {"tables": [_table("$[*]", "root", [_col("id", "$[*].id")])]}
    _build(data, real)
    cache_dir = _json_cache_dir(str(data), "working")
    weird = {
        "tables": [
            "not-a-dict",
            {
                "path": "$[*]",
                "label": "root",
                "emit": True,
                "columns": ["not-a-col", {"name": "id", "path": "$[*].id", "type": "int"}],
            },
        ]
    }
    # Fingerprint of the weird config won't match the real cache → invalid,
    # but the non-dict guards must not raise.
    assert is_per_port_cache_valid(cache_dir, weird) is False


def test_read_meta_missing_file_returns_none(tmp_path: Path) -> None:
    assert read_per_port_cache_meta(tmp_path / "no-such-dir") is None


def test_falls_back_to_committed_layer(tmp_path: Path) -> None:
    data = _write(tmp_path, [{"id": 7}])
    cfg = {"tables": [_table("$[*]", "root", [_col("id", "$[*].id")])]}
    # Only the committed layer is populated (the deploy / fresh-server case).
    _build(data, cfg, layer="committed")
    out = load_v2_api_source(str(data), cfg)
    assert isinstance(out, pl.LazyFrame)
    assert out.collect()["id"].to_list() == [7]
