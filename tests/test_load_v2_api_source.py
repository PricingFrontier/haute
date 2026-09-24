"""Direct tests for the shared v2 apiInput runtime entry point.

``load_v2_api_source`` is the single function both the executor's source
builder and the generated/deploy code call, so its behaviour (emit checks,
demand projection, store-leased tables for canvas execution, the standalone
in-process shred, uniform per-port return shape) is the contract that keeps
the two paths from drifting.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import orjson
import polars as pl
import pytest

from haute._api_input_schema import ApiInputSchemaError
from haute._execution_context import ExecutionContext, ExecutionProfile
from haute._json_shred._cache import load_v2_api_source
from haute._json_shred._snapshots import api_input_snapshot_source
from haute._polars_io_registry import PolarsIoConfigError
from haute._sandbox import set_project_root
from haute._source_cache import SourceCacheStore
from tests.conftest import build_test_api_input_snapshots


@pytest.fixture(autouse=True)
def _isolated_cache_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the project store and standalone spills inside each test's project."""
    monkeypatch.chdir(tmp_path)
    set_project_root(tmp_path)


def _inputs_root(tmp_path: Path) -> Path:
    return tmp_path / ".haute_cache" / "inputs"


def _context() -> ExecutionContext:
    return ExecutionContext(operation="preview", profile=ExecutionProfile.PREVIEW_EAGER)


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


def _build(data_path: Path, config: dict[str, Any]) -> None:
    build_test_api_input_snapshots(data_path, config)


def _read(data_path: Path, config: dict[str, Any], **kwargs: Any) -> dict[str, pl.LazyFrame]:
    """The canvas-execution read: each demanded table's leased generation."""
    return load_v2_api_source(str(data_path), config, read_snapshots=True, **kwargs)


def test_single_port_returns_one_entry_dict(tmp_path: Path) -> None:
    data = _write(tmp_path, [{"id": 1}, {"id": 2}])
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    _build(data, cfg)
    out = _read(data, cfg)
    assert isinstance(out, dict)
    assert list(out) == ["root"]
    assert isinstance(out["root"], pl.LazyFrame)
    assert out["root"].collect()["id"].to_list() == [1, 2]


def test_a_standalone_shred_is_recorded_on_the_active_execution(tmp_path: Path) -> None:
    data = _write(tmp_path, [{"id": 1}, {"id": 2}])
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    context = _context()

    with context.stage("load_json"):
        assert load_v2_api_source(str(data), cfg)["root"].collect().height == 2

    evidence = context.metrics_payload(status="completed")["cache_proof"]
    assert evidence["hits"] == 0
    assert evidence["misses"] == 0
    assert evidence["direct_fallbacks"] == 1


def test_a_snapshot_read_is_not_a_direct_shred(tmp_path: Path) -> None:
    data = _write(tmp_path, [{"id": 1}, {"id": 2}])
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    _build(data, cfg)
    context = _context()

    with context.stage("load_json"):
        assert _read(data, cfg)["root"].collect().height == 2

    assert context.metrics_payload(status="completed")["cache_proof"]["direct_fallbacks"] == 0


def test_multi_port_returns_dict_in_schema_order(tmp_path: Path) -> None:
    data = _write(tmp_path, [{"id": 1, "drivers": [{"age": 30}, {"age": 40}]}])
    cfg = {
        "tables": [
            _table("$[:]", "root", [_col("id", "$[:].id")]),
            _table("$[:].drivers[:]", "drivers", [_col("age", "$[:].drivers[:].age")]),
        ]
    }
    _build(data, cfg)
    out = _read(data, cfg)
    assert isinstance(out, dict)
    assert list(out) == ["root", "drivers"]
    assert all(isinstance(frame, pl.LazyFrame) for frame in out.values())
    assert out["root"].collect()["id"].to_list() == [1]
    assert out["drivers"].collect()["age"].to_list() == [30, 40]


def test_demand_scoped_snapshot_read_leases_only_requested_port_and_columns(
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
    _build(data, cfg)
    store = SourceCacheStore(tmp_path)
    source = api_input_snapshot_source(cfg, data)
    context = _context()

    with context.stage("load_json"):
        out = _read(data, cfg, port_columns={"drivers": frozenset({"age"})})
        leased = {
            table.label: store.leased_generation_ids(table.identity) for table in source.tables
        }
        assert list(out) == ["drivers"]
        assert out["drivers"].collect().to_dict(as_series=False) == {"age": [30]}
        assert "PROJECT 1/2 COLUMNS" in out["drivers"].explain(optimized=True)

    # Only the demanded table is leased, and only until the execution's cleanup.
    assert leased["root"] == frozenset() and len(leased["drivers"]) == 1
    context.release_admission()
    assert store.leased_generation_ids(source.table("drivers").identity) == frozenset()


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
    assert not _inputs_root(tmp_path).exists()


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

    with _context().stage("load_json"):
        frame = _read(data, cfg, port_columns={"drivers": frozenset()})["drivers"]
        assert frame.collect_schema().names() == ["age"]
        assert "PROJECT 1/2 COLUMNS" in frame.explain(optimized=True)
        assert frame.select(pl.len().alias("row_count")).collect().item() == 3
    standalone = load_v2_api_source(str(data), cfg, port_columns={"drivers": frozenset()})
    assert standalone["drivers"].collect_schema().names() == ["age"]
    assert standalone["drivers"].collect().height == 3


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


@pytest.mark.parametrize(
    ("port_columns", "message"),
    [
        ({}, "non-empty"),
        (["drivers"], "non-empty mapping"),
        ({"missing": None}, "unknown"),
        ({"drivers": frozenset({""})}, "non-empty string"),
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


def test_standalone_load_shreds_without_touching_the_store(tmp_path: Path) -> None:
    data = _write(tmp_path, [{"id": 1}])
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}

    out = load_v2_api_source(str(data), cfg)

    assert out["root"].collect().to_dict(as_series=False) == {"id": [1]}
    assert not _inputs_root(tmp_path).exists()


def test_a_snapshot_read_of_an_unbuilt_table_is_refused_without_shredding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _write(tmp_path, [{"id": 1}])
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}

    def _unexpected_shred(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("a snapshot read must never shred the raw source")

    monkeypatch.setattr("haute._json_shred._records._iter_records", _unexpected_shred)

    with pytest.raises(PolarsIoConfigError, match="^input_snapshot_missing: .*'root'"):
        _read(data, cfg)


def test_never_cached_jsonl_shreds_in_memory(tmp_path: Path) -> None:
    data = tmp_path / "data.jsonl"
    data.write_text('{"id": 1}\n\n{"id": 2}\n', encoding="utf-8")
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}

    frame = load_v2_api_source(str(data), cfg)["root"].collect()

    assert frame["id"].to_list() == [1, 2]
    assert not _inputs_root(tmp_path).exists()


def test_a_schema_edit_never_serves_the_old_tables_snapshot(tmp_path: Path) -> None:
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

    # The edited table is a new identity: its snapshot is not built yet, and the
    # old one is never served in its place.
    with pytest.raises(PolarsIoConfigError, match="input_snapshot_missing"):
        _read(data, cfg)
    _build(data, cfg)
    assert _read(data, cfg)["root"].collect().to_dict(as_series=False) == {
        "id": [1],
        "premium": [12.5],
    }


def test_a_snapshot_read_does_not_reshred_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _write(tmp_path, [{"id": 9}])
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    _build(data, cfg)

    def _unexpected_reshred(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("a published table must not re-shred the JSON source")

    monkeypatch.setattr("haute._json_shred._records._iter_records", _unexpected_reshred)

    frame = _read(data, cfg)["root"]

    assert isinstance(frame, pl.LazyFrame)
    assert frame.collect()["id"].to_list() == [9]


def test_a_read_stays_on_its_leased_generation_across_a_rebuild(
    tmp_path: Path,
) -> None:
    data = _write(tmp_path, [{"id": 1}])
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    _build(data, cfg)
    generation_a = _read(data, cfg)["root"]

    data.write_text(json.dumps([{"id": 2}]), encoding="utf-8")
    _build(data, cfg)
    generation_b = _read(data, cfg)["root"]

    assert generation_a.collect().to_dict(as_series=False) == {"id": [1]}
    assert generation_b.collect().to_dict(as_series=False) == {"id": [2]}


def test_a_leased_table_survives_a_clear_and_repeated_collect(
    tmp_path: Path,
) -> None:
    data = _write(tmp_path, [{"id": 1}, {"id": 2}])
    cfg = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id")])]}
    _build(data, cfg)
    generation = _read(data, cfg)["root"]

    SourceCacheStore(tmp_path).clear(api_input_snapshot_source(cfg, data).table("root").identity)

    expected = {"id": [1, 2]}
    assert generation.collect().to_dict(as_series=False) == expected
    assert generation.collect().to_dict(as_series=False) == expected


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

    monkeypatch.setattr("haute._json_shred._cache.logger.warning", _capture_warning)

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
        "haute._json_shred._cache.logger.info",
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
def test_malformed_container_shapes_are_loud_at_every_boundary(
    tmp_path: Path,
    bad_config: dict[str, Any],
    error_match: str,
) -> None:
    data = _write(tmp_path, [{"id": 43}])

    with pytest.raises(ApiInputSchemaError, match=error_match):
        load_v2_api_source(str(data), bad_config)
    with pytest.raises(ApiInputSchemaError, match=error_match):
        _read(data, bad_config)
    with pytest.raises(ApiInputSchemaError, match=error_match):
        api_input_snapshot_source(bad_config, data)
