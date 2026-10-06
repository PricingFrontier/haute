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
from itertools import repeat
from pathlib import Path
from typing import Any

import orjson
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from haute._api_input_schema import ApiInputSchemaError
from haute._json_shred import _inference, _inference_filter
from haute._json_shred._inference import (
    _assemble_inference_schema,
    _infer_records,
    _InferenceState,
    infer_v2_schema_from_data,
)
from haute._json_shred._inference_filter import InferenceFilter
from haute._json_shred._records import ShredSkipStats
from haute._json_shred._shred import shred_to_buffers

# JSON scalars hypothesis can round-trip safely (no NaN/inf).
_scalars = st.one_of(
    st.integers(min_value=-1_000, max_value=1_000),
    st.floats(allow_nan=False, allow_infinity=False, width=32),
    st.text(max_size=6),
    st.booleans(),
)

_inference_values = st.recursive(
    st.one_of(_scalars, st.none()),
    lambda children: st.one_of(
        st.lists(children, max_size=3),
        st.dictionaries(st.sampled_from(["a", "b", "class", "field_0"]), children, max_size=3),
    ),
    max_leaves=12,
)


@given(
    st.lists(
        st.dictionaries(st.sampled_from(["a", "b", "c"]), _inference_values, max_size=3),
        min_size=2,
        max_size=5,
    )
)
@settings(max_examples=150, deadline=None)
def test_native_filter_matches_full_walk_across_shape_changes(phases: list[dict[str, Any]]) -> None:
    # Each phase crosses the learning boundary, so later phases exercise both
    # strict mismatch handling and the rebuilt native type.
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(_inference_filter, "_INITIAL_SAMPLE_RECORDS", 100)
        records = [record for phase in phases for record in repeat(phase, 125)]
        exact = _InferenceState()
        try:
            for record in records:
                exact.walk(record)
        except ApiInputSchemaError as expected:
            with pytest.raises(ApiInputSchemaError) as native_actual:
                _infer_records(records)
            assert native_actual.value.message == expected.message
            assert native_actual.value.context == expected.context
            with pytest.raises(ApiInputSchemaError) as fused_actual:
                _inference._infer_jsonl_lines(orjson.dumps(record) for record in records)
            assert fused_actual.value.message == expected.message
            assert fused_actual.value.context == expected.context
        else:
            native_schema = _assemble_inference_schema(_infer_records(records))
            assert native_schema == _assemble_inference_schema(exact)
            prefix_records = records[:125]
            prefix = _InferenceState()
            filter_ = InferenceFilter(json_mode=True)
            for record in prefix_records:
                prefix.walk(record)
                filter_.observe(record)
            prefix.merge(
                _inference._infer_jsonl_lines(
                    (orjson.dumps(record) for record in records[125:]),
                    seed=filter_.snapshot(),
                )
            )
            assert _assemble_inference_schema(prefix) == _assemble_inference_schema(exact)


def test_native_filter_leaves_deep_valid_objects_on_full_walk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_inference_filter, "_INITIAL_SAMPLE_RECORDS", 100)
    value: Any = 1
    for _ in range(150):
        value = {"nested": value}
    records = [value] * 101
    exact = _InferenceState()
    for record in records:
        exact.walk(record)
    assert _assemble_inference_schema(_infer_records(records)) == _assemble_inference_schema(exact)


def test_native_filter_bounds_compilation_while_preserving_late_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import msgspec

    monkeypatch.setattr(_inference_filter, "_INITIAL_SAMPLE_RECORDS", 100)
    compile_type = msgspec.defstruct
    compilations = 0

    def count_compile(*args: Any, **kwargs: Any) -> type:
        nonlocal compilations
        compilations += 1
        return compile_type(*args, **kwargs)

    monkeypatch.setattr(msgspec, "defstruct", count_compile)
    records = [{f"field_{i}": i} for i in range(1000)]
    actual = _assemble_inference_schema(_infer_records(records))
    assert compilations == 8
    assert len(actual["tables"][0]["columns"]) == 1000
    assert actual["tables"][0]["columns"][-1]["path"] == "$[:].field_999"


def _root_table(columns: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "path": "$[:]",
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
                        "path": "$[:].id",
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
                "path": "$[:].tags[:]",
                "label": "tags",
                "emit": True,
                "row_id_column": None,
                "columns": [
                    {
                        "name": "value",
                        "path": "$[:].tags[:].$value",
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
                "path": "$[:].drivers[:]",
                "label": "drivers",
                "emit": True,
                "row_id_column": None,
                "columns": [
                    {
                        "name": "age",
                        "path": "$[:].drivers[:].age",
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

    # Every DIRECT element of the drivers array is one element at the drivers
    # depth: a dict emits a row, anything else (scalar/null/list — a nested
    # array is schema-inexpressible and treated as a shape mismatch) is one
    # counted skip. No recursive flattening.
    visited = sum(len(r["drivers"]) for r in records)
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


_nested_inference_object = st.dictionaries(
    keys=st.sampled_from(["x", "y", "z"]),
    values=st.one_of(_scalars, st.none()),
    max_size=3,
)
_inference_value = st.one_of(
    _scalars,
    st.none(),
    _nested_inference_object,
    st.lists(st.one_of(_scalars, st.none()), max_size=5),
    st.lists(_nested_inference_object, max_size=5),
)


@given(
    records=st.lists(
        st.dictionaries(
            keys=st.sampled_from(["a", "b", "profile", "items"]),
            values=_inference_value,
            max_size=4,
        ),
        max_size=30,
    ),
    first_cut=st.integers(min_value=0, max_value=30),
    second_cut=st.integers(min_value=0, max_value=30),
)
@settings(max_examples=100, deadline=None)
def test_chunk_state_merge_is_exactly_equivalent_to_serial_inference(
    records: list[dict[str, Any]], first_cut: int, second_cut: int
) -> None:
    """Any file partition must preserve the complete ordered schema payload."""
    cuts = sorted((min(first_cut, len(records)), min(second_cut, len(records))))
    chunks = (records[: cuts[0]], records[cuts[0] : cuts[1]], records[cuts[1] :])

    expected = _assemble_inference_schema(_infer_records(records))
    merged = _InferenceState()
    for chunk in chunks:
        merged.merge(_infer_records(chunk))

    assert _assemble_inference_schema(merged) == expected
