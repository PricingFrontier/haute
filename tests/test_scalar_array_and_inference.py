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
import pickle
from pathlib import Path
from typing import Any

import orjson
import pytest

from haute._api_input_schema import ApiInputSchemaError
from haute._json_shred import _inference, _inference_filter
from haute._json_shred._cache import (
    build_per_port_cache,
    load_per_port_cache,
    read_per_port_cache_meta,
)
from haute._json_shred._inference import infer_v2_schema_from_data
from haute._json_shred._inference_filter import InferenceFilter


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


def _full_walk_schema(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Reference the exact inference contract without the native fast path."""
    state = _inference._InferenceState()
    for record in records:
        state.walk(record)
    return _inference._assemble_inference_schema(state)


def _assert_inferred_schema_matches_full_walk(records: list[dict[str, Any]]) -> None:
    """Compare successful schemas or the public structured failure evidence."""
    try:
        expected = _full_walk_schema(records)
    except ApiInputSchemaError as expected_error:
        with pytest.raises(ApiInputSchemaError) as actual_error:
            _inference._assemble_inference_schema(_inference._infer_records(records))
        assert actual_error.value.message == expected_error.message
        assert actual_error.value.context == expected_error.context
    else:
        assert _inference._assemble_inference_schema(_inference._infer_records(records)) == expected


# ---------------------------------------------------------------------------
# Inference: scalar arrays -> child tables
# ---------------------------------------------------------------------------


def test_infer_scalar_array_becomes_child_table(tmp_path: Path) -> None:
    data = [
        {"policy_id": 1, "coverages": ["TPFT", "comprehensive"]},
        {"policy_id": 2, "coverages": ["home"]},
    ]
    schema = infer_v2_schema_from_data(_write(tmp_path, data))

    root = _table(schema, "$[:]")
    assert root is not None
    assert root["emit"] is True
    root_cols = {c["name"] for c in root["columns"]}
    assert "policy_id" in root_cols
    # The scalar array must NOT appear as a (mis-typed) str column on the root.
    assert "coverages" not in root_cols

    child = _table(schema, "$[:].coverages[:]")
    assert child is not None, "scalar array should produce a child table"
    assert child["emit"] is False, "nested tables are opt-in"
    assert len(child["columns"]) == 1
    col = child["columns"][0]
    assert col["name"] == "value"
    assert col["type"] == "str"
    assert col["path"] == "$[:].coverages[:].$value"
    assert col["selected"] is True
    assert col["status"] == "Inferred"


def test_infer_sample_size_bounds_jsonl_reads(tmp_path: Path) -> None:
    """A sample should stop reading JSONL before later malformed records."""
    p = tmp_path / "data.jsonl"
    p.write_text('{"policy_id": 1, "premium": 12.5}\n{not-json}\n', encoding="utf-8")

    schema = infer_v2_schema_from_data(p, sample_size=1)

    root = _table(schema, "$[:]")
    assert root is not None
    cols = {col["name"]: col["type"] for col in root["columns"]}
    assert cols == {"policy_id": "int", "premium": "float"}


def test_infer_sample_size_bounds_root_json_array_reads(tmp_path: Path) -> None:
    """A sample should stop reading a root JSON array before later malformed records."""
    p = tmp_path / "data.json"
    p.write_text('[{"policy_id": 1}, {"policy_id": 2}, {"policy_id": ', encoding="utf-8")

    schema = infer_v2_schema_from_data(p, sample_size=2)

    root = _table(schema, "$[:]")
    assert root is not None
    cols = {col["name"]: col["type"] for col in root["columns"]}
    assert cols == {"policy_id": "int"}

    with pytest.raises(orjson.JSONDecodeError):
        infer_v2_schema_from_data(p)


def test_infer_sampled_root_json_array_handles_nested_delimiters(tmp_path: Path) -> None:
    """The bounded JSON-array sampler must ignore delimiters inside nested values."""
    p = tmp_path / "data.json"
    p.write_text(
        (
            '[{"note": "comma, bracket ] and escaped quote \\"", '
            '"tags": ["a,b", "c]d"]}, {"broken": '
        ),
        encoding="utf-8",
    )

    schema = infer_v2_schema_from_data(p, sample_size=1)

    root = _table(schema, "$[:]")
    assert root is not None
    root_types = {col["name"]: col["type"] for col in root["columns"]}
    assert root_types == {"note": "str"}
    tags = _table(schema, "$[:].tags[:]")
    assert tags is not None
    assert tags["columns"][0]["type"] == "str"


@pytest.mark.parametrize(
    "payload",
    [
        '[{"policy_id": 1},]',
        '[{"policy_id": 1}] trailing',
        '[{"policy_id": 1}]\x0b',
    ],
)
def test_infer_sampled_root_json_array_rejects_invalid_tail(
    tmp_path: Path,
    payload: str,
) -> None:
    p = tmp_path / "data.json"
    p.write_text(payload, encoding="utf-8")

    with pytest.raises(orjson.JSONDecodeError):
        infer_v2_schema_from_data(p, sample_size=99)


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
    assert "tags" in by_label
    assert by_label["tags"]["row_count"] == 3  # motor, fleet, home

    frames = load_per_port_cache(tmp_path / "cache", schema)
    tags = frames["tags"].collect()
    assert tags["value"].to_list() == ["motor", "fleet", "home"]


def test_scalar_array_mixed_types_widen_to_str(tmp_path: Path) -> None:
    data = [{"id": 1, "vals": [1, "x", 2.5]}]
    p = _write(tmp_path, data)
    schema = infer_v2_schema_from_data(p)
    child = _table(schema, "$[:].vals[:]")
    assert child is not None
    assert child["columns"][0]["type"] == "str"

    build_per_port_cache(p, _enable_all(schema), tmp_path / "cache")
    frames = load_per_port_cache(tmp_path / "cache", schema)
    assert frames["vals"].collect()["value"].to_list() == ["1", "x", "2.5"]


def test_scalar_array_numeric_widens_to_float(tmp_path: Path) -> None:
    data = [{"id": 1, "amts": [1, 2]}, {"id": 2, "amts": [2.5]}]
    p = _write(tmp_path, data)
    schema = infer_v2_schema_from_data(p)
    child = _table(schema, "$[:].amts[:]")
    assert child is not None
    assert child["columns"][0]["type"] == "float"

    build_per_port_cache(p, _enable_all(schema), tmp_path / "cache")
    frames = load_per_port_cache(tmp_path / "cache", schema)
    assert frames["amts"].collect()["value"].to_list() == [1.0, 2.0, 2.5]


def test_scalar_array_of_bools(tmp_path: Path) -> None:
    data = [{"id": 1, "flags": [True, False, True]}]
    p = _write(tmp_path, data)
    schema = infer_v2_schema_from_data(p)
    child = _table(schema, "$[:].flags[:]")
    assert child is not None
    assert child["columns"][0]["type"] == "bool"

    build_per_port_cache(p, _enable_all(schema), tmp_path / "cache")
    frames = load_per_port_cache(tmp_path / "cache", schema)
    assert frames["flags"].collect()["value"].to_list() == [True, False, True]


def test_empty_then_struct_array_is_object_table(tmp_path: Path) -> None:
    """A key that is [] early then [ {...} ] later is an OBJECT table, not scalar."""
    data = [{"id": 1, "drivers": []}, {"id": 2, "drivers": [{"age": 30}]}]
    p = _write(tmp_path, data)
    schema = infer_v2_schema_from_data(p)

    child = _table(schema, "$[:].drivers[:]")
    assert child is not None
    names = {c["name"] for c in child["columns"]}
    assert "age" in names
    assert "value" not in names  # not mis-classified as a scalar table

    root = _table(schema, "$[:]")
    assert "drivers" not in {c["name"] for c in root["columns"]}

    # Must build without crashing (drivers has one struct row).
    build_per_port_cache(p, _enable_all(schema), tmp_path / "cache")


def test_inference_widens_type_past_first_records(tmp_path: Path) -> None:
    """Type inference must reflect the WHOLE file, not just an early sample."""
    data = [{"amount": 1} for _ in range(150)] + [{"amount": 2.5}]
    p = _write(tmp_path, data)
    schema = infer_v2_schema_from_data(p)
    root = _table(schema, "$[:]")
    assert root is not None
    amount = next(c for c in root["columns"] if c["name"] == "amount")
    assert amount["type"] == "float"

    build_per_port_cache(p, schema, tmp_path / "cache")  # no crash on row 151


# ---------------------------------------------------------------------------
# Native shape-filter inference: exactness and relearning
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("records", "expected_top_level_walks"),
    [
        ([{"id": 1, "name": "same"}] * 500, 100),
        (
            ([{"id": 1}] * 100)
            + ([{"id": 1, "late": "new"}] * 100)
            + ([{"id": 1, "late": "new"}] * 300),
            200,
        ),
    ],
    ids=["unchanged_shape", "late_field_relearned"],
)
def test_infer_records_filters_known_shapes_and_relearns_on_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    records: list[dict[str, Any]],
    expected_top_level_walks: int,
) -> None:
    monkeypatch.setattr(_inference_filter, "_INITIAL_SAMPLE_RECORDS", 100)
    original_walk = _inference._InferenceState.walk
    top_level_walks = 0

    def counting_walk(
        self: _inference._InferenceState,
        value: Any,
        level: tuple[object, ...] = (),
        obj_prefix: tuple[str, ...] = (),
    ) -> None:
        nonlocal top_level_walks
        if level == () and obj_prefix == () and isinstance(value, dict):
            top_level_walks += 1
        original_walk(self, value, level, obj_prefix)

    monkeypatch.setattr(_inference._InferenceState, "walk", counting_walk)

    schema = _inference._assemble_inference_schema(_inference._infer_records(records))

    assert top_level_walks == expected_top_level_walks
    if expected_top_level_walks == 200:
        assert "$[:].late" in _leaf_types(schema)


def test_native_filter_learns_the_default_10000_record_prefix_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default prefix includes a field first seen on record 10,000."""
    records = ([{"id": 1}] * 9_999) + ([{"id": 1, "late_seed": "present"}] * 102)
    original_walk = _inference._InferenceState.walk
    top_level_walks = 0

    def counting_walk(
        self: _inference._InferenceState,
        value: Any,
        level: tuple[object, ...] = (),
        obj_prefix: tuple[str, ...] = (),
    ) -> None:
        nonlocal top_level_walks
        if level == () and obj_prefix == () and isinstance(value, dict):
            top_level_walks += 1
        original_walk(self, value, level, obj_prefix)

    monkeypatch.setattr(_inference._InferenceState, "walk", counting_walk)

    schema = _inference._assemble_inference_schema(_inference._infer_records(records))

    assert top_level_walks == 10_000
    assert _leaf_types(schema) == {"$[:].id": "int", "$[:].late_seed": "str"}


@pytest.mark.parametrize(
    ("seed", "tail"),
    [
        ([{"tags": [1, None]}], [{"tags": [None]}]),
        ([{"tags": [{}, [1]]}], [{"tags": [[1]]}]),
        ([{"tags": []}], [{"tags": [None]}, {"tags": [1.5]}]),
        ([{"tags": [1]}], [{"tags": [{"code": "x"}]}, {"tags": [2]}]),
        ([{"value": True}], [{"value": 1}, {"value": 2.5}, {"value": "x"}]),
        ([{"profile": None}], [{"profile": {"city": "Hull"}}, {"profile": "flat"}]),
        (
            [{"profile": {"city": "Hull"}, "left": {"value": 1}}],
            [
                {
                    "profile": {"city": "Leeds", "postcode": "LS1"},
                    "claims": [{"amount": 3}],
                    "right": {"value": 2},
                }
            ],
        ),
    ],
    ids=[
        "scalar_array_nulls",
        "mixed_object_and_nested_array_then_nested_array",
        "empty_then_null_then_numeric_array",
        "scalar_object_scalar_array",
        "bool_int_float_string",
        "null_object_scalar",
        "late_nested_table_field_and_duplicate_leaf",
    ],
)
def test_native_shape_filter_matches_full_walk_for_shape_edges(
    monkeypatch: pytest.MonkeyPatch,
    seed: list[dict[str, Any]],
    tail: list[dict[str, Any]],
) -> None:
    monkeypatch.setattr(_inference_filter, "_INITIAL_SAMPLE_RECORDS", 100)
    _assert_inferred_schema_matches_full_walk(seed * 100 + tail)


@pytest.mark.parametrize("bad_key", ["bad.key", "$value", "not-valid"])
def test_native_shape_filter_relearns_late_invalid_nested_keys(
    monkeypatch: pytest.MonkeyPatch, bad_key: str
) -> None:
    monkeypatch.setattr(_inference_filter, "_INITIAL_SAMPLE_RECORDS", 100)
    records = ([{"profile": {"city": "Hull"}}] * 100) + [
        {"profile": {"city": "Hull", bad_key: "late"}}
    ]

    _assert_inferred_schema_matches_full_walk(records)


@pytest.mark.parametrize("key", ["class", "__dict__", "__weakref__", "field_0"])
def test_native_shape_filter_accepts_reserved_python_identifier_keys(
    monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    monkeypatch.setattr(_inference_filter, "_INITIAL_SAMPLE_RECORDS", 100)
    records = [{key: "value"}] * 101

    _assert_inferred_schema_matches_full_walk(records)


def test_inference_filter_snapshot_round_trips_and_is_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_inference_filter, "_INITIAL_SAMPLE_RECORDS", 100)
    original = {"id": 1}
    new_field = {"id": 1, "late": "new"}
    learned = InferenceFilter()
    for _ in range(100):
        learned.observe(original)

    snapshot = pickle.loads(pickle.dumps(learned.snapshot()))
    first = InferenceFilter(snapshot)
    second = InferenceFilter(snapshot)
    for _ in range(100):
        assert first.matches(new_field) is False
        first.observe(new_field)

    assert first.matches(new_field) is True
    assert second.matches(new_field) is False
    assert second.matches(original) is True


@pytest.mark.parametrize(
    ("tail_lines", "expected_type"),
    [
        ([b'{"amount":-9223372036854775809}'], "float"),
        ([b'{"amount":-9223372036854775808}'], "int"),
        ([b'{"amount":9223372036854775807}'], "int"),
        ([b'{"amount":9223372036854775808}'], "int"),
        ([b'{"amount":18446744073709551615}'], "int"),
        ([b'{"amount":18446744073709551616}'], "float"),
        ([b'{"amount":1.25}'], "float"),
        ([b'{"amount":1e20}'], "float"),
    ],
    ids=[
        "below-int64",
        "min-int64",
        "max-int64",
        "above-int64",
        "max-uint64",
        "overflow-widens",
        "decimal",
        "exponent",
    ],
)
def test_fused_json_range_matches_orjson_for_integer_bounds_and_numbers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tail_lines: list[bytes],
    expected_type: str,
) -> None:
    """Integer overflow must fall back to the original parser and evidence."""
    monkeypatch.setattr(_inference_filter, "_INITIAL_SAMPLE_RECORDS", 100)
    prefix_records = [{"amount": 1} for _ in range(100)]
    prefix = _inference._InferenceState()
    filter_ = InferenceFilter(json_mode=True)
    for record in prefix_records:
        prefix.walk(record)
        filter_.observe(record)
    source = tmp_path / "numeric-tail.jsonl"
    source.write_bytes(b"\n".join(tail_lines) + b"\n")

    delta = _inference._infer_jsonl_range(source, 0, source.stat().st_size, seed=filter_.snapshot())
    prefix.merge(delta)
    expected_records = [*prefix_records, *(orjson.loads(raw) for raw in tail_lines)]
    expected = _full_walk_schema(expected_records)

    assert _inference._assemble_inference_schema(prefix) == expected
    root = _table(expected, "$[:]")
    assert root is not None
    amount = next(column for column in root["columns"] if column["name"] == "amount")
    assert amount["type"] == expected_type


@pytest.mark.parametrize(
    ("raw", "seed_record"),
    [
        (b'{"id":"\xff"}', {"id": "seed"}),
        (b'{"\xff":1}', {"id": 1}),
        (b'{"id":"\\ud800"}', {"id": "seed"}),
        (b'{"id":1e400}', {"id": 1}),
        (b'{"id":NaN}', {"id": 1}),
        (b'{"id":1', {"id": 1}),
        (b'{"id":1,}', {"id": 1}),
        (b'{"id":1} trailing', {"id": 1}),
        (b'\xef\xbb\xbf{"id":1}', {"id": 1}),
    ],
    ids=[
        "invalid-utf8-value",
        "invalid-utf8-key",
        "unpaired-surrogate",
        "overflow-float",
        "nan",
        "missing-close",
        "trailing-comma",
        "trailing-junk",
        "utf8-bom",
    ],
)
def test_fused_json_range_preserves_orjson_error_evidence_for_known_shapes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw: bytes, seed_record: dict[str, Any]
) -> None:
    monkeypatch.setattr(_inference_filter, "_INITIAL_SAMPLE_RECORDS", 100)
    learned = InferenceFilter(json_mode=True)
    for _ in range(100):
        learned.observe(seed_record)
    source = tmp_path / "bad-known.jsonl"
    source.write_bytes(raw + b"\n")

    with pytest.raises(orjson.JSONDecodeError) as expected:
        orjson.loads(raw.strip())
    with pytest.raises(orjson.JSONDecodeError) as actual:
        _inference._infer_jsonl_range(source, 0, source.stat().st_size, seed=learned.snapshot())

    assert actual.value.msg == expected.value.msg
    assert actual.value.doc == expected.value.doc
    assert actual.value.pos == expected.value.pos
    assert str(actual.value) == str(expected.value)


@pytest.mark.parametrize(
    ("raw", "expected_record"),
    [
        (b'{"amount":"wrong","amount":2}', {"amount": 2}),
        (b'{"profile":{"unknown":"bad"},"profile":{"city":"Hull"}}', {"profile": {"city": "Hull"}}),
        (b'{"na\\u006de":"wrong","name":"valid"}', {"name": "valid"}),
    ],
    ids=["wrong-type-overwritten", "nested-unknown-overwritten", "escaped-duplicate-key"],
)
def test_fused_json_range_accepts_valid_duplicate_keys_like_orjson(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raw: bytes,
    expected_record: dict[str, Any],
) -> None:
    monkeypatch.setattr(_inference_filter, "_INITIAL_SAMPLE_RECORDS", 100)
    prefix_record = {"amount": 1, "profile": {"city": "Hull"}, "name": "seed"}
    prefix = _inference._InferenceState()
    learned = InferenceFilter(json_mode=True)
    for _ in range(100):
        prefix.walk(prefix_record)
        learned.observe(prefix_record)
    source = tmp_path / "duplicates.jsonl"
    source.write_bytes(raw + b"\n")

    delta = _inference._infer_jsonl_range(source, 0, source.stat().st_size, seed=learned.snapshot())
    prefix.merge(delta)

    assert _inference._assemble_inference_schema(prefix) == _full_walk_schema(
        [*[prefix_record] * 100, expected_record]
    )


@pytest.mark.parametrize(
    ("raw", "expected_orjson_parses"),
    [(b' \t{"id":1}\r ', 0), (b'\v{"id":1}\f', 1)],
    ids=["json-whitespace", "strip-only-whitespace"],
)
def test_fused_json_range_keeps_strip_framing_for_known_shapes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raw: bytes,
    expected_orjson_parses: int,
) -> None:
    """Raw decoder misses must retain the historical ``raw.strip()`` parse."""
    monkeypatch.setattr(_inference_filter, "_INITIAL_SAMPLE_RECORDS", 100)
    learned = InferenceFilter(json_mode=True)
    prefix = _inference._InferenceState()
    for _ in range(100):
        learned.observe({"id": 1})
        prefix.walk({"id": 1})
    source = tmp_path / "framing.jsonl"
    source.write_bytes(raw + b"\n")
    original_loads = _inference.orjson.loads
    parsed = 0

    def counting_loads(value: bytes, *args: Any, **kwargs: Any) -> Any:
        nonlocal parsed
        parsed += 1
        return original_loads(value, *args, **kwargs)

    monkeypatch.setattr(_inference.orjson, "loads", counting_loads)
    state = _inference._infer_jsonl_range(source, 0, source.stat().st_size, seed=learned.snapshot())
    prefix.merge(state)

    assert parsed == expected_orjson_parses
    assert _inference._assemble_inference_schema(prefix) == _full_walk_schema([{"id": 1}])


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

    root = _table(schema, "$[:]")
    assert root is not None
    rtypes = {c["name"]: c["type"] for c in root["columns"]}
    assert rtypes["policy_id"] == "int"
    assert rtypes["premium"] == "float"
    assert rtypes["active"] == "bool"
    # `drivers` is a nested array, not a leaf column on the root.
    assert "drivers" not in rtypes

    drivers = _table(schema, "$[:].drivers[:]")
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
                "path": "$[:]",
                "label": "root",
                "emit": True,
                "row_id_column": None,
                "columns": [
                    {
                        "name": "age",
                        "path": "$[:].age",
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
                "path": "$[:]",
                "label": "root",
                "emit": True,
                "row_id_column": None,
                "columns": [
                    {
                        "name": "age",
                        "path": "$[:].profile.age",
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


def test_dotted_leaf_through_list_fails_loud(tmp_path: Path) -> None:
    """A dotted leaf crossing a list fails LOUD instead of silently collapsing.

    The historical v1-parity behaviour silently resolved the first element
    (``"x"``), discarding ``"y"`` with no accounting. That is a conservation
    violation (W1): a dotted leaf addresses 1-1 object nesting only, so an
    array at that position must be modelled as its own child table. The build
    now raises ``ApiInputSchemaError`` rather than dropping rows.
    """
    data = [{"id": 1, "items": [{"name": "x"}, {"name": "y"}]}]
    schema = {
        "tables": [
            {
                "path": "$[:]",
                "label": "root",
                "emit": True,
                "row_id_column": None,
                "columns": [
                    {
                        "name": "first_item",
                        "path": "$[:].items.name",
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
    with pytest.raises(ApiInputSchemaError, match=r"items\.name"):
        build_per_port_cache(p, schema, tmp_path / "cache")


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
    child = _table(schema, "$[:].tags[:]")
    assert child is not None  # a scalar child table even though always empty
    assert child["columns"][0]["type"] == "str"
    summary = build_per_port_cache(p, _enable_all(schema), tmp_path / "cache")
    by_label = {t["label"]: t for t in summary["tables"]}
    assert by_label["tags"]["row_count"] == 0


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
    assert by_label["tags"]["row_count"] == 3  # a, null, b — count preserved
    frames = load_per_port_cache(tmp_path / "cache", schema)
    assert frames["tags"].collect()["value"].to_list() == ["a", None, "b"]


def test_build_bool_in_numeric_column_fails_loud(tmp_path: Path) -> None:
    """Polars would silently coerce a bool into an int/float column (True→1);
    we reject it loudly so a real type mismatch isn't hidden."""
    data = [{"flag": 1}, {"flag": True}]
    schema = {
        "tables": [
            {
                "path": "$[:]",
                "label": "root",
                "emit": True,
                "row_id_column": None,
                "columns": [
                    {
                        "name": "flag",
                        "path": "$[:].flag",
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


# ---------------------------------------------------------------------------
# Inference: a JSON ``null`` carries no type evidence
#
# A null is absence, not a value. It must neither widen a sibling's inferred
# type nor mint a scalar column at a path that other records hold a container
# at. The latter produced a schema declaring BOTH ``$[:].addr`` (str) and the
# folded ``$[:].addr.city``; the strict build then failed on the first record
# actually carrying the object ("column 'addr' has values that don't match its
# declared type 'str'"). The array branch already skipped nulls — these pin the
# scalar-leaf branch to the same convention.
# ---------------------------------------------------------------------------


def _leaf_types(schema: dict[str, Any]) -> dict[str, str]:
    return {c["path"]: c["type"] for t in schema["tables"] for c in t.get("columns", [])}


def test_infer_null_does_not_widen_a_numeric_column(tmp_path: Path) -> None:
    """One null in an int column must leave it ``int``, not retype it ``str``."""
    p = _write(tmp_path, [{"age": 30}, {"age": None}, {"age": 41}])
    assert _leaf_types(infer_v2_schema_from_data(p)) == {"$[:].age": "int"}


def test_bounded_inference_explicitly_owns_late_field_omission(tmp_path: Path) -> None:
    """Sampling is an explicit completeness trade-off, never a hidden default.

    This pins why the frontend may not silently add a head-sample: the build
    succeeds over every row while a never-inferred field remains absent.
    """
    p = _write(tmp_path, [{"id": 1}, {"id": 2}, {"id": 3, "late": "value"}])
    schema = _enable_all(infer_v2_schema_from_data(p, sample_size=2))
    assert _leaf_types(schema) == {"$[:].id": "int"}

    build_per_port_cache(p, schema, tmp_path / "cache")
    frame = next(iter(load_per_port_cache(tmp_path / "cache", schema).values()))
    assert frame.collect().columns == ["id"]


def test_infer_all_null_leaf_still_becomes_a_str_column(tmp_path: Path) -> None:
    """A leaf with no non-null value anywhere keeps a column, defaulting to
    ``str`` — the same default an only-ever-empty scalar array takes. Skipping
    nulls must not silently drop the field."""
    p = _write(tmp_path, [{"note": None}, {"note": None}])
    assert _leaf_types(infer_v2_schema_from_data(p)) == {"$[:].note": "str"}


def test_infer_nullable_object_mints_no_column_at_the_object_path(tmp_path: Path) -> None:
    """The reported failure: a 1-1 object that is null in some records. Only
    the folded dotted leaf is a column — the object's own path is not."""
    p = _write(tmp_path, [{"addr": {"city": "Hull"}}, {"addr": None}])
    assert _leaf_types(infer_v2_schema_from_data(p)) == {"$[:].addr.city": "str"}


def test_infer_nullable_array_mints_no_column_at_the_array_path(tmp_path: Path) -> None:
    """Same class as the nullable object: the child table carries the data, so
    a null occurrence must not also mint a scalar column at the array path."""
    p = _write(tmp_path, [{"claims": [{"amt": 5}]}, {"claims": None}])
    assert _leaf_types(infer_v2_schema_from_data(p)) == {"$[:].claims[:].amt": "int"}


def test_build_accepts_a_nullable_object_using_the_inferred_schema(tmp_path: Path) -> None:
    """End-to-end: inference -> build must now succeed on nullable-object data,
    with the null record's folded leaves read as null."""
    data = [{"addr": {"city": "Hull"}}, {"addr": None}, {"addr": {"city": "Leeds"}}]
    p = _write(tmp_path, data)
    schema = _enable_all(infer_v2_schema_from_data(p))
    build_per_port_cache(p, schema, tmp_path / "cache")
    frames = load_per_port_cache(tmp_path / "cache", schema)
    (frame,) = frames.values()
    (column,) = [c for t in schema["tables"] for c in t["columns"]]
    assert column["path"] == "$[:].addr.city"
    assert frame.collect()[column["name"]].to_list() == ["Hull", None, "Leeds"]
