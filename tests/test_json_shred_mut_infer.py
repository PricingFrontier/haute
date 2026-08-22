"""Mutation-killing witness tests for ``infer_v2_schema_from_data`` / ``_walk``.

Targets specific Cosmic Ray survivors in the schema-inference walk of
``src/haute/_json_shred/`` (the v2 relational decomposition of an
array-outer JSON document). Each test below is constructed so that the
documented mutation flips an OBSERVABLE field of the returned
``{"tables": [...]}`` structure.

These are witnesses only — they must NOT change behaviour of the source.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from haute._api_input_schema import _RESERVED_LEAF as _SCALAR_VALUE_LEAF
from haute._api_input_schema import ApiInputSchemaError
from haute._json_shred._inference import infer_v2_schema_from_data


def _infer(tmp_path: Path, data: list[dict[str, Any]], name: str = "data.json") -> dict[str, Any]:
    """Write *data* as a root JSON array and run inference over it."""
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return infer_v2_schema_from_data(p)


def _table(out: dict[str, Any], path: str) -> dict[str, Any]:
    for t in out["tables"]:
        if t["path"] == path:
            return t
    raise AssertionError(f"table {path!r} not found in {[t['path'] for t in out['tables']]}")


def _col_type(table: dict[str, Any], name: str) -> str:
    for c in table["columns"]:
        if c["name"] == name:
            return c["type"]
    raise AssertionError(f"column {name!r} not found in {[c['name'] for c in table['columns']]}")


# ---------------------------------------------------------------------------
# Survivor 1287: `continue` -> `break` inside the scalar-array element loop.
#
# The loop widens the element type of a scalar array, skipping None items via
# `continue`. With `continue`, a non-None element AFTER a None still
# contributes its type. With `break`, the loop terminates at the first None
# and the later element is never typed: elem_type stays None and falls back to
# the "str" sentinel.
# ---------------------------------------------------------------------------


def test_scalar_array_none_then_float_widens_past_the_none(tmp_path: Path) -> None:
    """A `[null, 1.5]` scalar array must be typed `float`, not the `str` fallback.

    `continue` skips the leading None and the trailing 1.5 widens the element
    type to `float`. `break` would stop at the None, leaving elem_type None ->
    "str". So the exact token `float` discriminates `continue` from `break`.
    """
    out = _infer(tmp_path, [{"tags": [None, 1.5]}])
    child = _table(out, "$[:].tags[:]")
    # Single reserved "value" column carrying the widened element type.
    assert [c["name"] for c in child["columns"]] == ["value"]
    assert _col_type(child, "value") == "float"
    # Sanity: the column path uses the reserved scalar leaf, not the "str" path.
    assert child["columns"][0]["path"] == f"$[:].tags[:].{_SCALAR_VALUE_LEAF}"


def test_scalar_array_none_then_int_still_typed_int(tmp_path: Path) -> None:
    """`[null, 7]` -> `int`, confirming the post-None element is processed.

    A second, independent witness for 1287 with a different non-str type, so a
    `break` mutation (which yields the `str` fallback) is caught regardless of
    which numeric token the post-None element carries.
    """
    out = _infer(tmp_path, [{"codes": [None, 7]}])
    child = _table(out, "$[:].codes[:]")
    assert _col_type(child, "value") == "int"
    # A bare `[null]` array (no post-None element) is the "str" fallback; this
    # pins that the int above came specifically from processing past the None.
    out_only_none = _infer(tmp_path, [{"codes": [None]}], name="only_none.json")
    assert _col_type(_table(out_only_none, "$[:].codes[:]"), "value") == "str"


# ---------------------------------------------------------------------------
# Survivor 1332: `"emit": array_depth(level) == 0`.
#
# Only the ROOT array level (depth 0) emits by default; nested array tables
# (depth >= 1) are emit=False. We assert both, which pins the boolean.
# (The Eq->LtE mutation is equivalent because array_depth is always >= 0 — see
#  the structured-output notes — but these assertions still guard the True/False
#  split against AddNot / boolean-flip mutations on the same line.)
# ---------------------------------------------------------------------------


def test_root_emits_nested_does_not(tmp_path: Path) -> None:
    out = _infer(tmp_path, [{"id": 1, "kids": [{"x": 10}]}])
    root = _table(out, "$[:]")
    nested = _table(out, "$[:].kids[:]")
    # Exact booleans (not just truthiness) to kill True/False flips on the line.
    assert root["emit"] is True
    assert nested["emit"] is False


def test_scalar_child_table_also_does_not_emit(tmp_path: Path) -> None:
    """A scalar-array child table (depth 1) is emit=False, like an object child."""
    out = _infer(tmp_path, [{"tags": ["a", "b"]}])
    assert _table(out, "$[:]")["emit"] is True
    assert _table(out, "$[:].tags[:]")["emit"] is False


# ---------------------------------------------------------------------------
# Inner-_walk coverage strengtheners: int/float widening, scalar-array shape,
# and the nested-array-of-arrays error.
# ---------------------------------------------------------------------------


def test_int_then_float_row_widens_column_to_float(tmp_path: Path) -> None:
    """An int column whose later record is a float is typed `float` (file-wide)."""
    out = _infer(tmp_path, [{"score": 1}, {"score": 2.5}])
    root = _table(out, "$[:]")
    assert _col_type(root, "score") == "float"


def test_scalar_array_becomes_single_value_column_child_table(tmp_path: Path) -> None:
    """`["a", "b"]` becomes a child table with one reserved `value` column."""
    out = _infer(tmp_path, [{"tags": ["a", "b"]}])
    child = _table(out, "$[:].tags[:]")
    assert [c["name"] for c in child["columns"]] == ["value"]
    col = child["columns"][0]
    assert col["type"] == "str"
    assert col["path"] == f"$[:].tags[:].{_SCALAR_VALUE_LEAF}"


def test_nested_array_of_arrays_raises(tmp_path: Path) -> None:
    """An array of arrays cannot be a flat table column — must raise."""
    with pytest.raises(ApiInputSchemaError) as ei:
        _infer(tmp_path, [{"matrix": [[1, 2]]}])
    # The offending column is carried in the error context and rendered.
    assert ei.value.context["column"] == "matrix"
    assert "nested arrays" in str(ei.value)
