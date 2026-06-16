"""Property-based invariants for the v2 shred + inference.

Replaces the inference/shape property coverage that lived in the deleted
``test_json_flatten_properties.py`` (the v1 codec's property suite). These
pin the structural guarantees that are easy to regress silently:

- a shred emits exactly one root row per top-level record;
- a scalar-array child table emits exactly one row per array element;
- inference is independent of record order (type widening is set-based);
- conservation (W2 item 2.7): every array element at an emitting table's
  depth is either emitted as a row or counted as skipped — no element can
  vanish without a trace.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from haute._json_shred import (
    ShredSkipStats,
    infer_v2_schema_from_data,
    shred_to_buffers,
)

# JSON scalars hypothesis can round-trip safely (no NaN/inf).
_scalars = st.one_of(
    st.integers(min_value=-1_000, max_value=1_000),
    st.floats(allow_nan=False, allow_infinity=False, width=32),
    st.text(max_size=6),
    st.booleans(),
)


def _root_table(columns: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "path": "$[*]",
        "label": "root",
        "emit": True,
        "row_id_column": None,
        "columns": columns,
    }


@given(st.lists(st.fixed_dictionaries({"id": st.integers()}), max_size=40))
@settings(max_examples=60, deadline=None)
def test_shred_root_row_count_equals_record_count(records: list[dict[str, Any]]) -> None:
    cfg = {
        "tables": [
            _root_table(
                [
                    {
                        "name": "id",
                        "path": "$[*].id",
                        "type": "int",
                        "status": "Confirmed",
                        "selected": True,
                        "levels": None,
                    }
                ]
            )
        ]
    }
    buffers = shred_to_buffers(records, cfg)
    assert len(buffers["root"]) == len(records)


@given(
    st.lists(
        st.fixed_dictionaries(
            # Include None elements: a null in a scalar array is a real (None)
            # row, so the one-row-per-element invariant must still hold.
            {"tags": st.lists(st.one_of(st.text(max_size=5), st.none()), max_size=6)}
        ),
        max_size=25,
    )
)
@settings(max_examples=60, deadline=None)
def test_shred_scalar_child_row_count_equals_total_elements(
    records: list[dict[str, Any]],
) -> None:
    cfg = {
        "tables": [
            {
                "path": "$[*].tags[*]",
                "label": "tags",
                "emit": True,
                "row_id_column": None,
                "columns": [
                    {
                        "name": "value",
                        "path": "$[*].tags[*].$value",
                        "type": "str",
                        "status": "Inferred",
                        "selected": True,
                        "levels": None,
                    }
                ],
            }
        ]
    }
    buffers = shred_to_buffers(records, cfg)
    assert len(buffers["tags"]) == sum(len(r["tags"]) for r in records)


# Arbitrary JSON-ish array elements: objects (emit), scalars/nulls/lists
# (skip, for an object table). Exercises the W2 item 2.7 conservation law.
_mixed_elements = st.one_of(
    st.fixed_dictionaries({"age": st.integers(min_value=0, max_value=120)}),
    st.integers(min_value=-100, max_value=100),
    st.text(max_size=4),
    st.booleans(),
    st.none(),
    st.lists(st.integers(min_value=0, max_value=3), max_size=2),
)


@given(
    st.lists(
        st.fixed_dictionaries({"drivers": st.lists(_mixed_elements, max_size=6)}),
        max_size=20,
    )
)
@settings(max_examples=60, deadline=None)
def test_shred_conserves_object_array_elements_as_rows_plus_skips(
    records: list[dict[str, Any]],
) -> None:
    """Every element of an object-table array is either an emitted row or a
    counted skip — emitted + skipped == total, for any mix of shapes."""
    cfg = {
        "tables": [
            {
                "path": "$[*].drivers[*]",
                "label": "drivers",
                "emit": True,
                "row_id_column": None,
                "columns": [
                    {
                        "name": "age",
                        "path": "$[*].drivers[*].age",
                        "type": "int",
                        "status": "Confirmed",
                        "selected": True,
                        "levels": None,
                    }
                ],
            }
        ]
    }
    stats = ShredSkipStats()
    buffers = shred_to_buffers(records, cfg, stats=stats)

    # Nested lists recurse (canonical list-flattening), so their leaf
    # elements are what hits the drivers depth — count what the walk
    # actually visits there.
    visited = 0
    for r in records:
        for el in r["drivers"]:
            visited += len(el) if isinstance(el, list) else 1
    emitted = len(buffers["drivers"])
    skipped = stats.skipped_rows_by_table.get("drivers", 0)
    assert emitted + skipped == visited, (records, emitted, skipped, visited)
    # Only object elements emit; everything else must be in the skip count.
    object_elements = sum(1 for r in records for el in r["drivers"] if isinstance(el, dict))
    assert emitted == object_elements


def _normalise(schema: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Reduce a schema to {table_path: {column_name: type}} (order-independent)."""
    return {t["path"]: {c["name"]: c["type"] for c in t["columns"]} for t in schema["tables"]}


@given(
    records=st.lists(
        st.dictionaries(
            keys=st.sampled_from(["a", "b", "c"]),
            values=_scalars,
            max_size=3,
        ),
        min_size=1,
        max_size=15,
    )
)
@settings(
    max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
def test_inference_is_record_order_invariant(records: list[dict[str, Any]], tmp_path: Path) -> None:
    forward = tmp_path / "f.json"
    forward.write_text(json.dumps(records), encoding="utf-8")
    reverse = tmp_path / "r.json"
    reverse.write_text(json.dumps(list(reversed(records))), encoding="utf-8")

    assert _normalise(infer_v2_schema_from_data(forward)) == _normalise(
        infer_v2_schema_from_data(reverse)
    )
