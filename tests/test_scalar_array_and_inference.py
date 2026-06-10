"""Behavioural tests for v2 shred inference + scalar-array handling.

Regression coverage for the multi-frame review findings:

- A JSON *scalar array* (e.g. ``coverages: ["TPFT", "comprehensive"]``) was
  inferred as a ``str`` leaf column on the parent, then crashed the strict
  parquet build with an opaque ``TypeError`` surfaced as HTTP 500.  Per the
  agreed design (Option 2) a scalar array now becomes its own *child table*
  (one row per element, single ``value`` column) — exactly how arrays of
  objects already behave.
- ``infer_v2_schema_from_data`` was previously reached only by *mocked*
  tests, so its behaviour (types, nested tables, widening) was unverified.
- A declared-vs-actual type mismatch must fail *loud and specific*
  (``ApiInputSchemaError`` → structured 422) rather than as an opaque 500.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from haute._api_input_schema import ApiInputSchemaError
from haute._json_shred import (
    build_per_port_cache,
    infer_v2_schema_from_data,
    load_per_port_cache,
    read_per_port_cache_meta,
)


def _write(tmp_path: Path, records: list[dict[str, Any]]) -> Path:
    p = tmp_path / "data.json"
    p.write_text(json.dumps(records), encoding="utf-8")
    return p


def _table(schema: dict[str, Any], path: str) -> dict[str, Any] | None:
    for t in schema["tables"]:
        if t["path"] == path:
            return t
    return None


def _enable_all(schema: dict[str, Any]) -> dict[str, Any]:
    for t in schema["tables"]:
        t["emit"] = True
    return schema


# ---------------------------------------------------------------------------
# Inference: scalar arrays -> child tables
# ---------------------------------------------------------------------------


def test_infer_scalar_array_becomes_child_table(tmp_path: Path) -> None:
    data = [
        {"policy_id": 1, "coverages": ["TPFT", "comprehensive"]},
        {"policy_id": 2, "coverages": ["home"]},
    ]
    schema = infer_v2_schema_from_data(_write(tmp_path, data))

    root = _table(schema, "$[*]")
    assert root is not None
    assert root["emit"] is True
    root_cols = {c["name"] for c in root["columns"]}
    assert "policy_id" in root_cols
    # The scalar array must NOT appear as a (mis-typed) str column on the root.
    assert "coverages" not in root_cols

    child = _table(schema, "$[*].coverages[*]")
    assert child is not None, "scalar array should produce a child table"
    assert child["emit"] is False, "nested tables are opt-in"
    assert len(child["columns"]) == 1
    col = child["columns"][0]
    assert col["name"] == "value"
    assert col["type"] == "str"
    assert col["path"] == "$[*].coverages[*].$value"
    assert col["selected"] is True
    assert col["status"] == "Inferred"


def test_infer_then_build_scalar_array_no_crash(tmp_path: Path) -> None:
    """The exact repro that produced an opaque 500 must now build cleanly."""
    data = [
        {"policy_id": 1, "tags": ["motor", "fleet"]},
        {"policy_id": 2, "tags": ["home"]},
    ]
    p = _write(tmp_path, data)
    schema = _enable_all(infer_v2_schema_from_data(p))

    summary = build_per_port_cache(p, schema, tmp_path / "cache")
    by_label = {t["label"]: t for t in summary["tables"]}
    assert "$[*].tags[*]" in by_label
    assert by_label["$[*].tags[*]"]["row_count"] == 3  # motor, fleet, home

    frames = load_per_port_cache(tmp_path / "cache", schema)
    tags = frames["$[*].tags[*]"].collect()
    assert tags["value"].to_list() == ["motor", "fleet", "home"]


def test_scalar_array_mixed_types_widen_to_str(tmp_path: Path) -> None:
    data = [{"id": 1, "vals": [1, "x", 2.5]}]
    p = _write(tmp_path, data)
    schema = infer_v2_schema_from_data(p)
    child = _table(schema, "$[*].vals[*]")
    assert child is not None
    assert child["columns"][0]["type"] == "str"

    build_per_port_cache(p, _enable_all(schema), tmp_path / "cache")
    frames = load_per_port_cache(tmp_path / "cache", schema)
    assert frames["$[*].vals[*]"].collect()["value"].to_list() == ["1", "x", "2.5"]


def test_scalar_array_numeric_widens_to_float(tmp_path: Path) -> None:
    data = [{"id": 1, "amts": [1, 2]}, {"id": 2, "amts": [2.5]}]
    p = _write(tmp_path, data)
    schema = infer_v2_schema_from_data(p)
    child = _table(schema, "$[*].amts[*]")
    assert child is not None
    assert child["columns"][0]["type"] == "float"

    build_per_port_cache(p, _enable_all(schema), tmp_path / "cache")
    frames = load_per_port_cache(tmp_path / "cache", schema)
    assert frames["$[*].amts[*]"].collect()["value"].to_list() == [1.0, 2.0, 2.5]


def test_scalar_array_of_bools(tmp_path: Path) -> None:
    data = [{"id": 1, "flags": [True, False, True]}]
    p = _write(tmp_path, data)
    schema = infer_v2_schema_from_data(p)
    child = _table(schema, "$[*].flags[*]")
    assert child is not None
    assert child["columns"][0]["type"] == "bool"

    build_per_port_cache(p, _enable_all(schema), tmp_path / "cache")
    frames = load_per_port_cache(tmp_path / "cache", schema)
    assert frames["$[*].flags[*]"].collect()["value"].to_list() == [True, False, True]


def test_empty_then_struct_array_is_object_table(tmp_path: Path) -> None:
    """A key that is [] early then [ {...} ] later is an OBJECT table, not scalar."""
    data = [{"id": 1, "drivers": []}, {"id": 2, "drivers": [{"age": 30}]}]
    p = _write(tmp_path, data)
    schema = infer_v2_schema_from_data(p)

    child = _table(schema, "$[*].drivers[*]")
    assert child is not None
    names = {c["name"] for c in child["columns"]}
    assert "age" in names
    assert "value" not in names  # not mis-classified as a scalar table

    root = _table(schema, "$[*]")
    assert "drivers" not in {c["name"] for c in root["columns"]}

    # Must build without crashing (drivers has one struct row).
    build_per_port_cache(p, _enable_all(schema), tmp_path / "cache")


def test_inference_widens_type_past_first_records(tmp_path: Path) -> None:
    """Type inference must reflect the WHOLE file, not just an early sample."""
    data = [{"amount": 1} for _ in range(150)] + [{"amount": 2.5}]
    p = _write(tmp_path, data)
    schema = infer_v2_schema_from_data(p)
    root = _table(schema, "$[*]")
    assert root is not None
    amount = next(c for c in root["columns"] if c["name"] == "amount")
    assert amount["type"] == "float"

    build_per_port_cache(p, schema, tmp_path / "cache")  # no crash on row 151


def test_infer_returns_root_and_object_child_tables_with_types(tmp_path: Path) -> None:
    data = [
        {
            "policy_id": 1,
            "premium": 100.5,
            "active": True,
            "drivers": [
                {"driver_id": "d1", "age": 30},
                {"driver_id": "d2", "age": 25},
            ],
        }
    ]
    schema = infer_v2_schema_from_data(_write(tmp_path, data))

    root = _table(schema, "$[*]")
    assert root is not None
    rtypes = {c["name"]: c["type"] for c in root["columns"]}
    assert rtypes["policy_id"] == "int"
    assert rtypes["premium"] == "float"
    assert rtypes["active"] == "bool"
    # `drivers` is a nested array, not a leaf column on the root.
    assert "drivers" not in rtypes

    drivers = _table(schema, "$[*].drivers[*]")
    assert drivers is not None
    assert {c["name"]: c["type"] for c in drivers["columns"]} == {
        "driver_id": "str",
        "age": "int",
    }
    assert drivers["emit"] is False

    for t in schema["tables"]:
        for c in t["columns"]:
            assert c["selected"] is True
            assert c["status"] == "Inferred"


# ---------------------------------------------------------------------------
# Loud failure (never an opaque 500)
# ---------------------------------------------------------------------------


def test_build_type_mismatch_raises_structured_error(tmp_path: Path) -> None:
    """A hand-authored schema whose type doesn't match the data fails loud."""
    data = [{"age": 30}, {"age": "oops"}]
    schema = {
        "tables": [
            {
                "path": "$[*]",
                "label": "root",
                "emit": True,
                "row_id_column": None,
                "columns": [
                    {
                        "name": "age",
                        "path": "$[*].age",
                        "type": "int",
                        "status": "Confirmed",
                        "selected": True,
                        "levels": None,
                    }
                ],
            }
        ]
    }
    with pytest.raises(ApiInputSchemaError) as ei:
        build_per_port_cache(_write(tmp_path, data), schema, tmp_path / "cache")
    # The error must name the offending column so the user can act.
    assert "age" in str(ei.value)


def test_nested_scalar_array_fails_loud(tmp_path: Path) -> None:
    """Array-of-arrays isn't expressible as a flat table — fail loud, not 500."""
    data = [{"id": 1, "matrix": [[1, 2], [3, 4]]}]
    with pytest.raises(ApiInputSchemaError) as ei:
        infer_v2_schema_from_data(_write(tmp_path, data))
    assert "matrix" in str(ei.value)


# ---------------------------------------------------------------------------
# Additional shred-core behaviours
# ---------------------------------------------------------------------------


def test_dotted_leaf_column_resolves_nested_field(tmp_path: Path) -> None:
    """A column path with a dotted tail reaches into a nested object."""
    data = [{"id": 1, "profile": {"age": 30}}]
    schema = {
        "tables": [
            {
                "path": "$[*]",
                "label": "root",
                "emit": True,
                "row_id_column": None,
                "columns": [
                    {
                        "name": "age",
                        "path": "$[*].profile.age",
                        "type": "int",
                        "status": "Confirmed",
                        "selected": True,
                        "levels": None,
                    }
                ],
            }
        ]
    }
    p = _write(tmp_path, data)
    build_per_port_cache(p, schema, tmp_path / "cache")
    frames = load_per_port_cache(tmp_path / "cache", schema)
    assert frames["root"].collect()["age"].to_list() == [30]


def test_dotted_leaf_through_list_takes_first_element(tmp_path: Path) -> None:
    """A dotted leaf that crosses a list resolves the first element (v1 parity)."""
    data = [{"id": 1, "items": [{"name": "x"}, {"name": "y"}]}]
    schema = {
        "tables": [
            {
                "path": "$[*]",
                "label": "root",
                "emit": True,
                "row_id_column": None,
                "columns": [
                    {
                        "name": "first_item",
                        "path": "$[*].items.name",
                        "type": "str",
                        "status": "Confirmed",
                        "selected": True,
                        "levels": None,
                    }
                ],
            }
        ]
    }
    p = _write(tmp_path, data)
    build_per_port_cache(p, schema, tmp_path / "cache")
    frames = load_per_port_cache(tmp_path / "cache", schema)
    assert frames["root"].collect()["first_item"].to_list() == ["x"]


def test_jsonl_input_is_shredded(tmp_path: Path) -> None:
    p = tmp_path / "data.jsonl"
    p.write_text('{"id": 1}\n\n{"id": 2}\n', encoding="utf-8")  # blank line tolerated
    schema = _enable_all(infer_v2_schema_from_data(p))
    summary = build_per_port_cache(p, schema, tmp_path / "cache")
    assert summary["tables"][0]["row_count"] == 2


def test_empty_array_only_produces_empty_child_table(tmp_path: Path) -> None:
    data = [{"id": 1, "tags": []}, {"id": 2, "tags": []}]
    p = _write(tmp_path, data)
    schema = infer_v2_schema_from_data(p)
    child = _table(schema, "$[*].tags[*]")
    assert child is not None  # a scalar child table even though always empty
    assert child["columns"][0]["type"] == "str"
    summary = build_per_port_cache(p, _enable_all(schema), tmp_path / "cache")
    by_label = {t["label"]: t for t in summary["tables"]}
    assert by_label["$[*].tags[*]"]["row_count"] == 0


def test_read_meta_returns_none_on_corrupt_meta(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "meta.json").write_bytes(b"{ not valid json")
    assert read_per_port_cache_meta(cache) is None


def test_scalar_array_with_nulls_preserves_row_count(tmp_path: Path) -> None:
    """A null element of a scalar array is a real (None) value, not dropped."""
    data = [{"id": 1, "tags": ["a", None, "b"]}]
    p = _write(tmp_path, data)
    schema = _enable_all(infer_v2_schema_from_data(p))
    summary = build_per_port_cache(p, schema, tmp_path / "cache")
    by_label = {t["label"]: t for t in summary["tables"]}
    assert by_label["$[*].tags[*]"]["row_count"] == 3  # a, null, b — count preserved
    frames = load_per_port_cache(tmp_path / "cache", schema)
    assert frames["$[*].tags[*]"].collect()["value"].to_list() == ["a", None, "b"]


def test_build_bool_in_numeric_column_fails_loud(tmp_path: Path) -> None:
    """Polars would silently coerce a bool into an int/float column (True→1);
    we reject it loudly so a real type mismatch isn't hidden."""
    data = [{"flag": 1}, {"flag": True}]
    schema = {
        "tables": [
            {
                "path": "$[*]",
                "label": "root",
                "emit": True,
                "row_id_column": None,
                "columns": [
                    {
                        "name": "flag",
                        "path": "$[*].flag",
                        "type": "int",
                        "status": "Confirmed",
                        "selected": True,
                        "levels": None,
                    }
                ],
            }
        ]
    }
    with pytest.raises(ApiInputSchemaError) as ei:
        build_per_port_cache(_write(tmp_path, data), schema, tmp_path / "cache")
    assert "flag" in str(ei.value)
    assert "boolean" in str(ei.value).lower()
