"""V2 schema codec + per-port shred (MULTI_FRAME_PLAN commit 3).

Layered:

1. ``is_v2_shape`` / ``validate_v2_schema`` / path helpers — pure
   functions, exhaustive edge cases.
2. ``legacy_to_v2`` / ``v2_to_legacy`` round-trips — migration policy
   per §4d (orphan drop, label = path default, emit=true on migrated
   table, column_renames into per-column name).
3. ``shred_to_buffers`` — algorithm correctness on rating-shaped
   nested-array data.
4. ``build_per_port_cache`` + ``load_per_port_cache`` +
   ``is_per_port_cache_valid`` — disk round-trip, fingerprint
   invalidation on schema change.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import orjson
import polars as pl
import pytest

from haute._api_input_schema import (
    is_v2_shape,
    legacy_to_v2,
    parse_column_path,
    parse_table_path,
    v2_to_legacy,
    validate_v2_schema,
)
from haute._json_shred import (
    build_per_port_cache,
    is_per_port_cache_valid,
    load_per_port_cache,
    read_per_port_cache_meta,
    shred_to_buffers,
)


# ─── Path helpers ─────────────────────────────────────────────────


def test_parse_table_path_root() -> None:
    assert parse_table_path("$") == ()
    assert parse_table_path("$[*]") == ()


def test_parse_table_path_one_level() -> None:
    assert parse_table_path("$[*].drivers[*]") == ("drivers",)


def test_parse_table_path_two_levels() -> None:
    assert parse_table_path("$[*].drivers[*].licenses[*]") == ("drivers", "licenses")


def test_parse_table_path_rejects_malformed() -> None:
    with pytest.raises(ValueError):
        parse_table_path("drivers[*]")  # no $[*] prefix
    with pytest.raises(ValueError):
        parse_table_path("$[*].drivers")  # missing [*] at iteration depth
    with pytest.raises(ValueError):
        parse_table_path("$[*]..drivers[*]")  # empty segment


def test_parse_column_path_simple() -> None:
    assert parse_column_path("$[*].drivers[*].driver_id", "$[*].drivers[*]") == "driver_id"


def test_parse_column_path_nested_dotted() -> None:
    assert parse_column_path(
        "$[*].drivers[*].profile.age",
        "$[*].drivers[*]",
    ) == "profile.age"


def test_parse_column_path_rejects_unrelated() -> None:
    with pytest.raises(ValueError):
        parse_column_path("$[*].vehicles[*].id", "$[*].drivers[*]")


# ─── is_v2_shape / validate_v2_schema ─────────────────────────────


def test_is_v2_shape_recognises_tables() -> None:
    assert is_v2_shape({"tables": []}) is True
    assert is_v2_shape({"tables": [{"label": "x", "path": "$[*]"}]}) is True


def test_is_v2_shape_rejects_v1() -> None:
    assert is_v2_shape({"flattenSchema": {"a": "int"}}) is False
    assert is_v2_shape({}) is False


def test_is_v2_shape_rejects_corrupt_mix() -> None:
    """A config carrying BOTH tables AND flattenSchema is corrupt — treat as v1
    so the migration codec is forced to run rather than silently using v2.
    """
    assert is_v2_shape({"tables": [], "flattenSchema": {"a": "int"}}) is False


def _minimal_v2() -> dict[str, Any]:
    return {
        "path": "data.json",
        "contract": "opaque",
        "tables": [
            {
                "path": "$[*]",
                "label": "policies",
                "emit": True,
                "columns": [
                    {
                        "name": "policy_id",
                        "path": "$[*].policy_id",
                        "type": "int",
                        "status": "Confirmed",
                        "selected": True,
                    },
                ],
            },
        ],
    }


def test_validate_v2_schema_accepts_minimal() -> None:
    validate_v2_schema(_minimal_v2())  # raises on failure


def test_validate_rejects_missing_label() -> None:
    cfg = _minimal_v2()
    del cfg["tables"][0]["label"]
    with pytest.raises(ValueError, match="label"):
        validate_v2_schema(cfg)


def test_validate_rejects_duplicate_table_labels() -> None:
    cfg = _minimal_v2()
    cfg["tables"].append(dict(cfg["tables"][0]))
    cfg["tables"][1]["path"] = "$[*].drivers[*]"
    cfg["tables"][1]["columns"] = [
        {"name": "id", "path": "$[*].drivers[*].id", "type": "int", "selected": True},
    ]
    # Both tables still labelled "policies"
    with pytest.raises(ValueError, match="appears more than once"):
        validate_v2_schema(cfg)


def test_validate_rejects_duplicate_column_names_within_table() -> None:
    cfg = _minimal_v2()
    cfg["tables"][0]["columns"].append(
        {"name": "policy_id", "path": "$[*].policy_number", "type": "str", "selected": True},
    )
    with pytest.raises(ValueError, match="duplicate column name"):
        validate_v2_schema(cfg)


def test_validate_accepts_same_column_name_across_tables() -> None:
    """Per §4d, column.name uniqueness is per-table; cross-table reuse OK."""
    cfg = _minimal_v2()
    cfg["tables"].append(
        {
            "path": "$[*].drivers[*]",
            "label": "drivers",
            "emit": True,
            "columns": [
                {
                    "name": "policy_id",
                    "path": "$[*].drivers[*].policy_id",
                    "type": "int",
                    "selected": True,
                },
            ],
        },
    )
    validate_v2_schema(cfg)


def test_validate_rejects_row_id_column_pointing_at_nothing() -> None:
    cfg = _minimal_v2()
    cfg["tables"][0]["row_id_column"] = "nonexistent"
    with pytest.raises(ValueError, match="row_id_column"):
        validate_v2_schema(cfg)


def test_validate_accepts_valid_row_id_column() -> None:
    cfg = _minimal_v2()
    cfg["tables"][0]["row_id_column"] = "policy_id"
    validate_v2_schema(cfg)


def test_validate_rejects_empty_levels_list() -> None:
    cfg = _minimal_v2()
    cfg["tables"][0]["columns"][0]["levels"] = []
    with pytest.raises(ValueError, match="levels is empty"):
        validate_v2_schema(cfg)


def test_validate_accepts_null_levels() -> None:
    cfg = _minimal_v2()
    cfg["tables"][0]["columns"][0]["levels"] = None
    validate_v2_schema(cfg)


def test_validate_accepts_non_empty_levels() -> None:
    cfg = _minimal_v2()
    cfg["tables"][0]["columns"][0]["type"] = "str"
    cfg["tables"][0]["columns"][0]["levels"] = ["A", "B", None]
    validate_v2_schema(cfg)


# ─── legacy_to_v2 migration ───────────────────────────────────────


def test_legacy_to_v2_minimal_flatten_schema() -> None:
    v1 = {
        "path": "data.json",
        "contract": "opaque",
        "flattenSchema": {"policy_id": "int", "premium": "float"},
    }
    v2 = legacy_to_v2(v1)
    assert is_v2_shape(v2)
    assert v2["path"] == "data.json"
    tables = v2["tables"]
    assert len(tables) == 1
    assert tables[0]["path"] == "$[*]"
    assert tables[0]["emit"] is True
    cols = tables[0]["columns"]
    names = {c["name"]: c for c in cols}
    assert "policy_id" in names
    assert names["policy_id"]["type"] == "int"
    assert names["premium"]["type"] == "float"


def test_legacy_to_v2_carries_column_renames() -> None:
    """v1 column_renames lift into per-column ``name`` overrides on v2."""
    v1 = {
        "path": "x.json",
        "flattenSchema": {"policy_details.policy_number": "str", "premium": "float"},
        "column_renames": {"policy_details.policy_number": "policy_number"},
    }
    v2 = legacy_to_v2(v1)
    cols = {c["name"]: c for c in v2["tables"][0]["columns"]}
    assert "policy_number" in cols, "column_renames target should land as the v2 name"
    assert cols["policy_number"]["path"] == "$[*].policy_details.policy_number"


def test_legacy_to_v2_carries_selected_columns() -> None:
    v1 = {
        "flattenSchema": {"a": "int", "b": "str", "c": "float"},
        "selected_columns": ["a", "c"],
    }
    v2 = legacy_to_v2(v1)
    cols = {c["name"]: c for c in v2["tables"][0]["columns"]}
    assert cols["a"]["selected"] is True
    assert cols["b"]["selected"] is False
    assert cols["c"]["selected"] is True


def test_legacy_to_v2_carries_categorical_levels() -> None:
    v1 = {
        "flattenSchema": {"channel": "str", "premium": "float"},
        "categorical_levels": {"channel": ["web", "phone", None]},
    }
    v2 = legacy_to_v2(v1)
    cols = {c["name"]: c for c in v2["tables"][0]["columns"]}
    assert cols["channel"]["levels"] == ["web", "phone", None]
    assert "levels" not in cols["premium"]


def test_legacy_to_v2_carries_row_id_column() -> None:
    v1 = {
        "flattenSchema": {"policy_id": "int", "premium": "float"},
        "row_id_column": "policy_id",
    }
    v2 = legacy_to_v2(v1)
    assert v2["tables"][0]["row_id_column"] == "policy_id"


def test_legacy_to_v2_drops_row_id_pointing_at_missing_column() -> None:
    v1 = {
        "flattenSchema": {"premium": "float"},
        "row_id_column": "policy_id",  # not in flattenSchema → orphan
    }
    v2 = legacy_to_v2(v1)
    assert "row_id_column" not in v2["tables"][0]


def test_legacy_to_v2_idempotent_on_v2() -> None:
    v2 = _minimal_v2()
    again = legacy_to_v2(v2)
    assert again == v2


# ─── v2_to_legacy reverse direction ───────────────────────────────


def test_v2_to_legacy_basic_round_trip() -> None:
    v1 = {
        "path": "x.json",
        "contract": "opaque",
        "flattenSchema": {"policy_id": "int", "premium": "float"},
    }
    v2 = legacy_to_v2(v1)
    back = v2_to_legacy(v2)
    assert back["flattenSchema"] == {"policy_id": "int", "premium": "float"}


def test_v2_to_legacy_preserves_rename() -> None:
    v1 = {
        "flattenSchema": {"raw.path": "str"},
        "column_renames": {"raw.path": "renamed"},
    }
    v2 = legacy_to_v2(v1)
    back = v2_to_legacy(v2)
    assert back["column_renames"] == {"raw.path": "renamed"}


# ─── shred_to_buffers algorithm ───────────────────────────────────


def _rating_v2() -> dict[str, Any]:
    """Three-level nested schema modelled on the rating example."""
    return {
        "path": "x.json",
        "contract": "opaque",
        "tables": [
            {
                "path": "$[*]",
                "label": "policies",
                "emit": True,
                "columns": [
                    {
                        "name": "policy_id",
                        "path": "$[*].policy_id",
                        "type": "int",
                        "selected": True,
                    },
                ],
            },
            {
                "path": "$[*].drivers[*]",
                "label": "drivers",
                "emit": True,
                "columns": [
                    {
                        "name": "driver_id",
                        "path": "$[*].drivers[*].driver_id",
                        "type": "int",
                        "selected": True,
                    },
                    {
                        "name": "age_band",
                        "path": "$[*].drivers[*].age_band",
                        "type": "str",
                        "selected": True,
                    },
                ],
            },
            {
                "path": "$[*].drivers[*].licenses[*]",
                "label": "licenses",
                "emit": True,
                "columns": [
                    {
                        "name": "license_id",
                        "path": "$[*].drivers[*].licenses[*].license_id",
                        "type": "int",
                        "selected": True,
                    },
                ],
            },
        ],
    }


def _rating_records() -> list[dict[str, Any]]:
    """Two policies; one has two drivers (with 1 + 2 licenses respectively),
    the other has one driver with no licenses. Total counts:
    - policies: 2 rows
    - drivers: 3 rows
    - licenses: 3 rows
    """
    return [
        {
            "policy_id": 1001,
            "drivers": [
                {
                    "driver_id": 1,
                    "age_band": "30-59",
                    "licenses": [{"license_id": 100}],
                },
                {
                    "driver_id": 2,
                    "age_band": "60+",
                    "licenses": [{"license_id": 101}, {"license_id": 102}],
                },
            ],
        },
        {
            "policy_id": 1002,
            "drivers": [
                {"driver_id": 3, "age_band": "60+", "licenses": []},
            ],
        },
    ]


def test_shred_to_buffers_row_counts_match_iteration_depths() -> None:
    cfg = _rating_v2()
    buffers = shred_to_buffers(_rating_records(), cfg)
    assert len(buffers["policies"]) == 2
    assert len(buffers["drivers"]) == 3
    assert len(buffers["licenses"]) == 3


def test_shred_to_buffers_extracts_per_depth_columns() -> None:
    cfg = _rating_v2()
    buffers = shred_to_buffers(_rating_records(), cfg)
    assert buffers["policies"] == [{"policy_id": 1001}, {"policy_id": 1002}]
    assert buffers["drivers"][0] == {"driver_id": 1, "age_band": "30-59"}
    assert buffers["licenses"][2] == {"license_id": 102}


def test_shred_to_buffers_skips_non_emitting_tables() -> None:
    cfg = _rating_v2()
    cfg["tables"][1]["emit"] = False  # drivers off
    buffers = shred_to_buffers(_rating_records(), cfg)
    assert "drivers" not in buffers
    # But licenses still gets shredded because it's emit-true and we
    # transparently walk through the drivers depth to reach it.
    assert len(buffers["licenses"]) == 3


def test_shred_to_buffers_handles_missing_leaves_as_null() -> None:
    """A column whose JSONPath doesn't resolve produces ``None`` (per the
    plan §4d edge case — "a path that doesn't exist needs to produce a
    null value")."""
    cfg = _rating_v2()
    records = [
        {"policy_id": 999, "drivers": [{"driver_id": 1}]},  # age_band missing
    ]
    buffers = shred_to_buffers(records, cfg)
    assert buffers["drivers"] == [{"driver_id": 1, "age_band": None}]


def test_shred_to_buffers_two_columns_from_same_source_get_same_values() -> None:
    """Per §4d: two columns with the same `path` but different `name`
    emit identical row values to both."""
    cfg = {
        "path": "x.json",
        "tables": [
            {
                "path": "$[*]",
                "label": "policies",
                "emit": True,
                "columns": [
                    {
                        "name": "policy_id",
                        "path": "$[*].id",
                        "type": "int",
                        "selected": True,
                    },
                    {
                        "name": "id_copy",
                        "path": "$[*].id",
                        "type": "int",
                        "selected": True,
                    },
                ],
            },
        ],
    }
    buffers = shred_to_buffers([{"id": 7}], cfg)
    assert buffers["policies"] == [{"policy_id": 7, "id_copy": 7}]


# ─── Disk round-trip: build + load + validity ─────────────────────


def _write_rating_json(path: Path) -> None:
    path.write_text(json.dumps(_rating_records()))


def test_build_per_port_cache_writes_one_parquet_per_emit_table(tmp_path: Path) -> None:
    data_path = tmp_path / "data.json"
    _write_rating_json(data_path)
    cache_dir = tmp_path / "cache"

    summary = build_per_port_cache(data_path, _rating_v2(), cache_dir)
    assert summary["schema_mode"] == "v2"
    assert len(summary["tables"]) == 3

    # All three parquets exist + a meta.json
    files = sorted(p.name for p in cache_dir.iterdir())
    assert "meta.json" in files
    assert any(f.startswith("policies") and f.endswith(".parquet") for f in files)
    assert any(f.startswith("drivers") and f.endswith(".parquet") for f in files)
    assert any(f.startswith("licenses") and f.endswith(".parquet") for f in files)


def test_built_parquet_carries_per_frame_schema_in_footer(tmp_path: Path) -> None:
    """DUAL_CACHE.md §3 — per-frame schema embedded in parquet footer
    so each file is self-describing (no separate schema-side-file race)."""
    import pyarrow.parquet as pq

    data_path = tmp_path / "data.json"
    _write_rating_json(data_path)
    cache_dir = tmp_path / "cache"
    build_per_port_cache(data_path, _rating_v2(), cache_dir)

    drivers_parquet = next(p for p in cache_dir.iterdir() if p.name.startswith("drivers"))
    pq_meta = pq.read_metadata(drivers_parquet)
    schema_md = pq_meta.schema.to_arrow_schema().metadata or {}
    assert b"haute_per_frame_schema" in schema_md, schema_md
    payload = orjson.loads(schema_md[b"haute_per_frame_schema"])
    assert payload["port_label"] == "drivers"
    col_names = {c["name"] for c in payload["columns"]}
    assert col_names == {"driver_id", "age_band"}


def test_load_per_port_cache_returns_one_lazyframe_per_emit_table(tmp_path: Path) -> None:
    data_path = tmp_path / "data.json"
    _write_rating_json(data_path)
    cache_dir = tmp_path / "cache"
    build_per_port_cache(data_path, _rating_v2(), cache_dir)

    bundle = load_per_port_cache(cache_dir, _rating_v2())
    assert set(bundle.keys()) == {"policies", "drivers", "licenses"}

    drivers_df = bundle["drivers"].collect()
    assert drivers_df.height == 3
    assert sorted(drivers_df.columns) == ["age_band", "driver_id"]
    assert drivers_df["driver_id"].to_list() == [1, 2, 3]


def test_cache_validity_passes_when_schema_unchanged(tmp_path: Path) -> None:
    data_path = tmp_path / "data.json"
    _write_rating_json(data_path)
    cache_dir = tmp_path / "cache"
    cfg = _rating_v2()
    build_per_port_cache(data_path, cfg, cache_dir)
    assert is_per_port_cache_valid(cache_dir, cfg) is True


def test_cache_validity_fails_when_fingerprint_changes(tmp_path: Path) -> None:
    """Adding a column to a table changes the schema fingerprint;
    is_per_port_cache_valid must return False so the cache layer rebuilds."""
    data_path = tmp_path / "data.json"
    _write_rating_json(data_path)
    cache_dir = tmp_path / "cache"
    cfg = _rating_v2()
    build_per_port_cache(data_path, cfg, cache_dir)
    # Mutate schema by adding a column to the drivers table.
    cfg["tables"][1]["columns"].append(
        {
            "name": "main",
            "path": "$[*].drivers[*].main",
            "type": "bool",
            "selected": True,
        },
    )
    assert is_per_port_cache_valid(cache_dir, cfg) is False


def test_cache_validity_fails_when_a_parquet_missing(tmp_path: Path) -> None:
    data_path = tmp_path / "data.json"
    _write_rating_json(data_path)
    cache_dir = tmp_path / "cache"
    cfg = _rating_v2()
    build_per_port_cache(data_path, cfg, cache_dir)
    # Delete one of the per-port parquets out-of-band.
    drivers_parquet = next(p for p in cache_dir.iterdir() if p.name.startswith("drivers"))
    drivers_parquet.unlink()
    assert is_per_port_cache_valid(cache_dir, cfg) is False


def test_rebuild_clears_stale_per_port_parquets(tmp_path: Path) -> None:
    """If a previous build wrote a parquet for a now-disabled or renamed
    table, the next build removes it so the directory stays clean."""
    data_path = tmp_path / "data.json"
    _write_rating_json(data_path)
    cache_dir = tmp_path / "cache"
    cfg_with_drivers = _rating_v2()
    build_per_port_cache(data_path, cfg_with_drivers, cache_dir)
    assert any(p.name.startswith("drivers") for p in cache_dir.iterdir())

    # Disable drivers and rebuild.
    cfg_no_drivers = _rating_v2()
    cfg_no_drivers["tables"][1]["emit"] = False
    build_per_port_cache(data_path, cfg_no_drivers, cache_dir)
    assert not any(
        p.name.startswith("drivers") and p.suffix == ".parquet" for p in cache_dir.iterdir()
    )


# ─── meta.json shape ──────────────────────────────────────────────


def test_meta_json_carries_schema_mode_and_fingerprint(tmp_path: Path) -> None:
    data_path = tmp_path / "data.json"
    _write_rating_json(data_path)
    cache_dir = tmp_path / "cache"
    build_per_port_cache(data_path, _rating_v2(), cache_dir)
    meta = read_per_port_cache_meta(cache_dir)
    assert meta is not None
    assert meta["schema_mode"] == "v2"
    assert isinstance(meta["schema_fingerprint"], str)
    assert len(meta["schema_fingerprint"]) == 64  # sha256 hex


# ─── Route dispatch via FastAPI test client ───────────────────────


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from fastapi.testclient import TestClient

    monkeypatch.chdir(tmp_path)
    from haute.server import app

    return TestClient(app)


def _write_v2_config(tmp_path: Path) -> Path:
    cfg_path = tmp_path / "quotes.json"
    cfg_path.write_text(json.dumps(_rating_v2()))
    return cfg_path


def _write_v2_data(tmp_path: Path) -> Path:
    data_path = tmp_path / "data.json"
    _write_rating_json(data_path)
    return data_path


def test_route_build_dispatches_to_v2_when_config_is_v2(
    client,
    tmp_path: Path,
) -> None:
    """POST /api/json-cache/build with a v2 config file on disk runs the
    per-port shred and returns 200 with the aggregated counts."""
    data_path = _write_v2_data(tmp_path)
    cfg_path = _write_v2_config(tmp_path)

    resp = client.post(
        "/api/json-cache/build",
        json={
            "path": str(data_path.relative_to(tmp_path)),
            "config_path": str(cfg_path.relative_to(tmp_path)),
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Aggregate counts across the three rating-shaped tables.
    # policies:2 + drivers:3 + licenses:3 = 8 rows total.
    assert body["row_count"] == 8
    # 1 + 2 + 1 = 4 columns total.
    assert body["column_count"] == 4
    # Cache path points at the directory (not a single parquet file).
    assert body["path"].endswith(".haute_cache/working/json_" + body["path"].rsplit("_", 1)[-1])


def test_route_post_status_returns_v2_aggregate_after_build(
    client,
    tmp_path: Path,
) -> None:
    data_path = _write_v2_data(tmp_path)
    cfg_path = _write_v2_config(tmp_path)
    rel_data = str(data_path.relative_to(tmp_path))
    rel_cfg = str(cfg_path.relative_to(tmp_path))

    build_resp = client.post(
        "/api/json-cache/build",
        json={"path": rel_data, "config_path": rel_cfg},
    )
    assert build_resp.status_code == 200

    status_resp = client.post(
        "/api/json-cache/status",
        json={"path": rel_data, "config_path": rel_cfg},
    )
    assert status_resp.status_code == 200
    status = status_resp.json()
    assert status["cached"] is True
    assert status["row_count"] == 8
    assert status["column_count"] == 4


def test_route_get_status_with_config_matches_post_for_v2(
    client,
    tmp_path: Path,
) -> None:
    """GET /api/json-cache/status with the same inputs returns the same
    cached-true payload as the POST variant. Closes the GET/POST
    divergence flagged in MULTI_FRAME_PLAN §commit 3."""
    data_path = _write_v2_data(tmp_path)
    cfg_path = _write_v2_config(tmp_path)
    rel_data = str(data_path.relative_to(tmp_path))
    rel_cfg = str(cfg_path.relative_to(tmp_path))

    client.post("/api/json-cache/build", json={"path": rel_data, "config_path": rel_cfg})
    post_body = client.post(
        "/api/json-cache/status",
        json={"path": rel_data, "config_path": rel_cfg},
    ).json()
    get_body = client.get(
        "/api/json-cache/status",
        params={"path": rel_data, "config_path": rel_cfg},
    ).json()

    # cached + counts must match between the two surfaces. The mtime
    # field can differ on the float epsilon if measured at different
    # moments, so we compare structurally.
    assert get_body["cached"] == post_body["cached"]
    assert get_body["row_count"] == post_body["row_count"]
    assert get_body["column_count"] == post_body["column_count"]


def test_route_status_after_clear_returns_cached_false(
    client,
    tmp_path: Path,
) -> None:
    data_path = _write_v2_data(tmp_path)
    cfg_path = _write_v2_config(tmp_path)
    rel_data = str(data_path.relative_to(tmp_path))
    rel_cfg = str(cfg_path.relative_to(tmp_path))

    client.post("/api/json-cache/build", json={"path": rel_data, "config_path": rel_cfg})
    client.delete("/api/json-cache", params={"path": rel_data})
    status_resp = client.post(
        "/api/json-cache/status",
        json={"path": rel_data, "config_path": rel_cfg},
    )
    assert status_resp.status_code == 200
    assert status_resp.json()["cached"] is False


def test_route_build_preserves_v1_behaviour_when_no_v2_config(
    client,
    tmp_path: Path,
) -> None:
    """A request without a config_path (or with a v1 config) routes
    through the existing flat-shred path, unchanged."""
    data_path = tmp_path / "v1_data.jsonl"
    data_path.write_text('{"a": 1, "b": "x"}\n{"a": 2, "b": "y"}\n')

    resp = client.post(
        "/api/json-cache/build",
        json={
            "path": str(data_path.relative_to(tmp_path)),
            "flatten_schema": {"a": "int", "b": "str"},
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["row_count"] == 2
    assert body["column_count"] == 2
