"""Direct tests for the shared v2 apiInput runtime entry point.

``load_v2_api_source`` is the single function both the executor's source
builder and the generated/deploy code now call, so its behaviour (emit
checks, working→committed cache resolution, uniform per-port return shape) is
the contract that keeps the two paths from drifting.
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


def test_missing_cache_raises_with_actionable_message(tmp_path: Path) -> None:
    data = _write(tmp_path, [{"id": 1}])
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    # No cache built in either layer.
    with pytest.raises(RuntimeError, match="Cache as Parquet"):
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
        orjson.dumps({"schema_mode": "v1", "schema_fingerprint": "x", "tables": []}),
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


def test_load_per_port_cache_skips_non_string_label(tmp_path: Path) -> None:
    data = _write(tmp_path, [{"id": 1}])
    good = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    cache_dir = tmp_path / "cache"
    build_per_port_cache(str(data), good, cache_dir)
    weird = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    weird["tables"][0]["label"] = 123
    frames = load_per_port_cache(cache_dir, weird)
    assert frames == {}


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


def test_read_meta_missing_file_returns_none(tmp_path: Path) -> None:
    assert read_per_port_cache_meta(tmp_path / "no-such-dir") is None


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
