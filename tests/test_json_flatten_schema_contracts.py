"""Fast mutation contracts for JSON flatten schema semantics."""

from __future__ import annotations

import pytest

from haute._json_flatten_schema import (
    JsonFlattenSchemaError,
    _assert_unique_column_names,
    _infer_type,
    _schema_leaf_types,
    _wider_type,
    flatten,
    infer_schema,
    schema_columns,
)


def test_type_inference_distinguishes_bool_from_int() -> None:
    assert _infer_type(True) == "bool"
    assert _infer_type(False) == "bool"
    assert _infer_type(1) == "int"
    assert _infer_type(1.5) == "float"
    assert _infer_type("1") == "str"
    assert _infer_type(None) == "str"


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("bool", "int", "int"),
        ("int", "float", "float"),
        ("float", "str", "str"),
        ("str", "bool", "str"),
    ],
)
def test_wider_type_preserves_scalar_widening_order(
    left: str,
    right: str,
    expected: str,
) -> None:
    assert _wider_type(left, right) == expected
    assert _wider_type(right, left) == expected


def test_infer_schema_merges_nested_arrays_and_optional_fields() -> None:
    samples = [
        {
            "quote": {"id": "Q1"},
            "drivers": [{"name": "Alice", "claims": []}],
            "flags": [True],
        },
        {
            "quote": {"premium": 123.45},
            "drivers": [
                {"name": "Bob", "claims": [{"amount": 50}]},
                {"age": 31},
            ],
            "flags": [False, True],
        },
    ]

    assert infer_schema(samples) == {
        "quote": {"id": "str", "premium": "float"},
        "drivers": {
            "$max": 2,
            "$items": {
                "name": "str",
                "claims": {"$max": 1, "$items": {"amount": "int"}},
                "age": "int",
            },
        },
        "flags": {"$max": 2, "$items": "bool"},
    }


def test_infer_schema_promotes_scalar_array_and_object_conflicts_to_richer_shape() -> None:
    schema = infer_schema(
        [
            {"contact": "unknown", "events": ["legacy-note"]},
            {
                "contact": {"email": "a@example.com"},
                "events": [{"type": "renewal", "premium": 125.5}],
            },
        ]
    )

    assert schema == {
        "contact": {"email": "str"},
        "events": {"$max": 1, "$items": {"type": "str", "premium": "float"}},
    }


def test_object_array_conflict_preserves_array_length_and_object_fields_in_both_orders() -> None:
    array_shape = {"contact": [{"email": "a@example.com"}, {"phone": "123"}]}
    object_shape = {"contact": {"verified": True}}

    expected = {
        "contact": {
            "$max": 2,
            "$items": {"email": "str", "phone": "str", "verified": "bool"},
        }
    }
    assert infer_schema([array_shape, object_shape]) == expected
    assert infer_schema([object_shape, array_shape]) == expected


def test_array_scalar_conflict_preserves_array_length_in_both_orders() -> None:
    array_shape = {"events": [{"type": "renewal"}, {"premium": 125.5}]}
    scalar_shape = {"events": "legacy-note"}

    expected = {"events": {"$max": 2, "$items": {"type": "str", "premium": "float"}}}
    assert infer_schema([array_shape, scalar_shape]) == expected
    assert infer_schema([scalar_shape, array_shape]) == expected


def test_empty_structures_merge_with_later_populated_shapes() -> None:
    assert infer_schema([{"vehicle": {}}, {"vehicle": {"make": "Ford"}}]) == {
        "vehicle": {"make": "str"}
    }
    assert infer_schema([{"claims": []}, {"claims": [{"amount": 100}]}]) == {
        "claims": {"$max": 1, "$items": {"amount": "int"}}
    }
    assert infer_schema([{"drivers": [{}]}, {"drivers": [{"name": "Alice"}]}]) == {
        "drivers": {"$max": 1, "$items": {"name": "str"}}
    }


@pytest.mark.parametrize("bad_key", ["", "a.b", "$max", "$items", "1", "001"])
def test_ambiguous_json_object_keys_fail_loudly(bad_key: str) -> None:
    with pytest.raises(JsonFlattenSchemaError, match="Unsupported JSON object key"):
        infer_schema([{bad_key: "value"}])


def test_nested_ambiguous_json_object_key_reports_parent_path() -> None:
    with pytest.raises(JsonFlattenSchemaError) as exc_info:
        infer_schema([{"outer": {"$max": 1}}])

    message = str(exc_info.value)
    assert "Unsupported JSON object key" in message
    assert "path=outer" in message
    assert "key=$max" in message


def test_flatten_uses_schema_order_null_filling_truncation_and_singleton_coercion() -> None:
    schema = {
        "quote_id": "str",
        "driver": {"name": "str", "age": "int"},
        "coverages": {
            "$max": 2,
            "$items": {"code": "str", "limits": {"$max": 2, "$items": "int"}},
        },
        "scores": {"$max": 3, "$items": "int"},
    }

    result = flatten(
        {
            "quote_id": "Q1",
            "driver": None,
            "coverages": [
                {"code": "bi", "limits": [100, 200, 300]},
                {"code": "pd"},
                {"code": "ignored"},
            ],
            "scores": 99,
            "extra": "ignored",
        },
        schema,
    )

    assert list(result) == schema_columns(schema)
    assert result == {
        "quote_id": "Q1",
        "driver.name": None,
        "driver.age": None,
        "coverages.1.code": "bi",
        "coverages.1.limits.1": 100,
        "coverages.1.limits.2": 200,
        "coverages.2.code": "pd",
        "coverages.2.limits.1": None,
        "coverages.2.limits.2": None,
        "scores.1": 99,
        "scores.2": None,
        "scores.3": None,
    }


def test_flatten_null_root_emits_all_schema_columns_as_none() -> None:
    schema = {"a": "str", "b": {"c": "int"}, "d": {"$max": 2, "$items": "bool"}}

    assert flatten(None, schema) == {
        "a": None,
        "b.c": None,
        "d.1": None,
        "d.2": None,
    }


def test_schema_columns_and_leaf_types_cover_nested_arrays_without_duplicates() -> None:
    schema = {"matrix": {"$max": 2, "$items": {"$max": 2, "$items": "int"}}}

    expected = ["matrix.1.1", "matrix.1.2", "matrix.2.1", "matrix.2.2"]
    assert schema_columns(schema) == expected
    assert _schema_leaf_types(schema) == [(column, "int") for column in expected]


def test_zero_length_and_empty_item_arrays_emit_no_columns() -> None:
    zero_length = {"items": {"$max": 0, "$items": "str"}}
    empty_items = {"items": {"$max": 2, "$items": {}}}

    for schema in (zero_length, empty_items):
        assert schema_columns(schema) == []
        assert _schema_leaf_types(schema) == []
        assert flatten({"items": ["ignored"]}, schema) == {}


def test_duplicate_column_names_fail_loudly() -> None:
    with pytest.raises(JsonFlattenSchemaError, match="duplicate column names"):
        _assert_unique_column_names(["a", "b", "a"])


def test_manual_ambiguous_schema_fails_before_silent_value_overwrite() -> None:
    schema = {"a.b": "int", "a": {"b": "int"}}

    with pytest.raises(JsonFlattenSchemaError, match="Unsupported JSON object key"):
        flatten({"a.b": 1, "a": {"b": 2}}, schema)


def test_invalid_array_schema_fails_loudly() -> None:
    with pytest.raises(JsonFlattenSchemaError, match="only \\$max and \\$items"):
        schema_columns({"items": {"$max": 1, "$items": "str", "extra": "bad"}})

    with pytest.raises(JsonFlattenSchemaError, match="only \\$max and \\$items"):
        schema_columns({"items": {"$max": 1}})

    with pytest.raises(JsonFlattenSchemaError, match="only \\$max and \\$items"):
        schema_columns({"items": {"$items": "str"}})

    with pytest.raises(JsonFlattenSchemaError, match="non-negative integer"):
        schema_columns({"items": {"$max": True, "$items": "str"}})

    with pytest.raises(JsonFlattenSchemaError, match="non-negative integer"):
        schema_columns({"items": {"$max": -1, "$items": "str"}})
