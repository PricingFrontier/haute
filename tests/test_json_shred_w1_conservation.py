"""W1 conservation / fail-loud regressions for :mod:`haute._json_shred`.

These pin the remediation of the wave-1 findings whose theme is
*fail-loud-or-account, never silent*:

- inference must reject a source key that collides with the reserved
  ``$value`` sentinel or contains the ``.`` object-nesting separator
  (both would silently drop the field at shred time);
- the shred must reject a table that mixes a ``$value`` leaf with real
  columns (every row would otherwise vanish as a shape mismatch);
- empty-array type inference must not poison a later concrete element type;
- the build must consume its record iterator without materialising it and
  assert root-level row conservation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from haute._api_input_schema import ApiInputSchemaError
from haute._json_shred import (
    _SCALAR_VALUE_LEAF,
    build_per_port_cache,
    infer_v2_schema_from_data,
    read_per_port_cache_meta,
    shred_to_buffers,
)


def _write(tmp_path: Path, records: list[Any], name: str = "data.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(records), encoding="utf-8")
    return p


def _col(name: str, path: str, type_: str = "str") -> dict[str, Any]:
    return {
        "name": name,
        "path": path,
        "type": type_,
        "status": "Confirmed",
        "selected": True,
        "levels": None,
    }


def _table(path: str, label: str, cols: list[dict[str, Any]]) -> dict[str, Any]:
    return {"path": path, "label": label, "emit": True, "row_id_column": None, "columns": cols}


# ─── F132 — a literal "$value" source key collides with the sentinel ──


def test_infer_rejects_literal_dollar_value_key(tmp_path: Path) -> None:
    p = _write(tmp_path, [{"items": [{"$value": "REAL", "other": 1}]}])
    with pytest.raises(ApiInputSchemaError, match=r"\$value"):
        infer_v2_schema_from_data(p)


def test_infer_rejects_dollar_value_key_at_root(tmp_path: Path) -> None:
    p = _write(tmp_path, [{"$value": "REAL"}])
    with pytest.raises(ApiInputSchemaError, match=r"\$value"):
        infer_v2_schema_from_data(p)


# ─── F153 — a source key containing "." can't be addressed cleanly ────


def test_infer_rejects_dotted_key(tmp_path: Path) -> None:
    p = _write(tmp_path, [{"a.b": "VAL"}])
    with pytest.raises(ApiInputSchemaError, match=r"a\.b"):
        infer_v2_schema_from_data(p)


def test_infer_rejects_dotted_key_nested_in_object(tmp_path: Path) -> None:
    p = _write(tmp_path, [{"outer": {"a.b": "VAL"}}])
    with pytest.raises(ApiInputSchemaError, match=r"a\.b"):
        infer_v2_schema_from_data(p)


# ─── $value sole-column guard at shred time (hand-edited config) ──────


def test_shred_rejects_value_leaf_mixed_with_real_columns() -> None:
    config = {
        "tables": [
            _table(
                "$[:].items[:]",
                "items",
                [
                    _col("value", f"$[:].items[:].{_SCALAR_VALUE_LEAF}"),
                    _col("other", "$[:].items[:].other", "int"),
                ],
            ),
        ],
    }
    with pytest.raises(ApiInputSchemaError, match="only own-depth column"):
        shred_to_buffers([{"items": [{"other": 1}]}], config)


def test_shred_accepts_lone_value_leaf_scalar_table() -> None:
    # The legitimate scalar-array child table: a single $value column is fine.
    config = {
        "tables": [
            _table("$[:].tags[:]", "tags", [_col("value", f"$[:].tags[:].{_SCALAR_VALUE_LEAF}")]),
        ],
    }
    buffers = shred_to_buffers([{"tags": ["a", "b"]}], config)
    assert buffers["tags"] == [{"value": "a"}, {"value": "b"}]


# ─── F103 — empty array must not poison a later concrete element type ─


def test_empty_array_then_int_array_types_int_not_str(tmp_path: Path) -> None:
    # First record has an empty scalar array; a later record fills it with ints.
    # The inferred $value column must be typed 'int', not widened to 'str' by
    # the empty-array seed.
    p = _write(tmp_path, [{"nums": []}, {"nums": [1, 2, 3]}])
    schema = infer_v2_schema_from_data(p)
    nums_table = next(t for t in schema["tables"] if t["path"] == "$[:].nums[:]")
    (value_col,) = nums_table["columns"]
    assert value_col["type"] == "int"


def test_only_ever_empty_array_defaults_to_str(tmp_path: Path) -> None:
    p = _write(tmp_path, [{"nums": []}, {"nums": []}])
    schema = infer_v2_schema_from_data(p)
    nums_table = next(t for t in schema["tables"] if t["path"] == "$[:].nums[:]")
    (value_col,) = nums_table["columns"]
    assert value_col["type"] == "str"


# ─── F717 + conservation — build consumes the iterator and reconciles ─


def test_build_conserves_root_rows_and_accounts_skips(tmp_path: Path) -> None:
    # A root array with two real objects and one non-object element: the two
    # objects each emit a row; the non-object is counted as a skipped record.
    p = _write(tmp_path, [{"id": 1}, "junk", {"id": 2}])
    cache_dir = tmp_path / "cache"
    config = {"tables": [_table("$[:]", "root", [_col("id", "$[:].id", "int")])]}

    summary = build_per_port_cache(p, config, cache_dir)

    root = next(t for t in summary["tables"] if t["label"] == "root")
    assert root["row_count"] == 2  # both objects emitted, the string skipped
    assert summary["skipped"]["records"] == 1
    meta = read_per_port_cache_meta(cache_dir)
    assert meta is not None
    assert meta["skipped"]["records"] == 1
