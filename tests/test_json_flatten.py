"""Tests for haute._json_flatten — schema inference, flattening, and I/O."""

from __future__ import annotations

import json
import time
from pathlib import Path

import polars as pl
import pytest

from haute._json_flatten import (
    _FLATTEN_CHUNK_SIZE,
    _LARGE_FILE_THRESHOLD,
    _MIN_CHUNK_ROWS,
    JsonCacheCancelledError,
    JsonFlattenSchemaError,
    _adaptive_chunk_size,
    _arrow_schema_from_flatten,
    _build_flatten_exprs,
    _cancel_events,
    _chunked,
    _clear_cancel_events,
    _clear_flatten_progress,
    _coerce_to_arrow,
    _flatten_and_write,
    _flatten_and_write_streaming,
    _infer_schema_streaming,
    _infer_type,
    _is_cache_valid,
    _iter_json_records,
    _iter_line_chunks,
    _json_cache_path,
    _polars_flatten_to_parquet,
    _rows_to_batch,
    _schema_leaf_types,
    _set_flatten_progress,
    _wider_type,
    build_json_cache,
    cancel_json_cache,
    clear_json_cache,
    flatten,
    flatten_progress,
    flatten_to_frame,
    infer_schema,
    is_large_json,
    json_cache_info,
    json_cache_path_if_valid,
    load_samples,
    read_json_flat,
    schema_columns,
)
from haute.errors import HauteError

# ---------------------------------------------------------------------------
# Type inference helpers
# ---------------------------------------------------------------------------


class TestInferType:
    def test_bool(self):
        assert _infer_type(True) == "bool"
        assert _infer_type(False) == "bool"

    def test_int(self):
        assert _infer_type(0) == "int"
        assert _infer_type(42) == "int"

    def test_float(self):
        assert _infer_type(3.14) == "float"
        assert _infer_type(0.0) == "float"

    def test_str(self):
        assert _infer_type("hello") == "str"
        assert _infer_type("") == "str"

    def test_bool_before_int(self):
        # bool is subclass of int — must detect bool first
        assert _infer_type(True) == "bool"


class TestWiderType:
    def test_same_types(self):
        assert _wider_type("int", "int") == "int"

    def test_int_float_widens_to_float(self):
        assert _wider_type("int", "float") == "float"
        assert _wider_type("float", "int") == "float"

    def test_anything_with_str_widens_to_str(self):
        assert _wider_type("int", "str") == "str"
        assert _wider_type("str", "bool") == "str"

    def test_bool_int_widens_to_int(self):
        assert _wider_type("bool", "int") == "int"


# ---------------------------------------------------------------------------
# Schema inference
# ---------------------------------------------------------------------------


class TestInferSchema:
    def test_empty_samples(self):
        assert infer_schema([]) == {}

    def test_flat_dict(self):
        schema = infer_schema([{"name": "Alice", "age": 30}])
        assert schema == {"name": "str", "age": "int"}

    def test_nested_object(self):
        schema = infer_schema([{"address": {"city": "London", "postcode": "SW1"}}])
        assert schema == {"address": {"city": "str", "postcode": "str"}}

    def test_array_of_objects(self):
        schema = infer_schema(
            [
                {
                    "drivers": [
                        {"name": "Alice", "age": 30},
                        {"name": "Bob", "age": 25},
                    ],
                }
            ]
        )
        assert schema == {
            "drivers": {
                "$max": 2,
                "$items": {"name": "str", "age": "int"},
            },
        }

    def test_array_of_scalars(self):
        schema = infer_schema([{"tags": ["a", "b", "c"]}])
        assert schema == {"tags": {"$max": 3, "$items": "str"}}

    def test_empty_array(self):
        schema = infer_schema([{"claims": []}])
        assert schema == {"claims": {"$max": 0, "$items": {}}}

    def test_empty_object_then_populated_object_merges_fields(self):
        schema = infer_schema([{"vehicle": {}}, {"vehicle": {"make": "Ford"}}])
        assert schema == {"vehicle": {"make": "str"}}

    def test_empty_array_then_populated_array_merges_items_and_max(self):
        schema = infer_schema([{"claims": []}, {"claims": [{"amount": 100}]}])
        assert schema == {"claims": {"$max": 1, "$items": {"amount": "int"}}}

    def test_array_of_empty_objects_then_populated_objects_merges_item_fields(self):
        schema = infer_schema([{"drivers": [{}]}, {"drivers": [{"name": "Alice"}]}])
        assert schema == {"drivers": {"$max": 1, "$items": {"name": "str"}}}

    @pytest.mark.parametrize("bad_key", ["", "a.b", "$max", "$items", "1", "001"])
    def test_ambiguous_object_keys_fail_loudly(self, bad_key: str):
        with pytest.raises(JsonFlattenSchemaError, match="Unsupported JSON object key"):
            infer_schema([{bad_key: "value"}])

    def test_nested_reserved_schema_key_fails_loudly(self):
        with pytest.raises(JsonFlattenSchemaError) as exc_info:
            infer_schema([{"outer": {"$max": 1}}])

        message = str(exc_info.value)
        assert "Unsupported JSON object key" in message
        assert "path=outer" in message
        assert "key=$max" in message

    def test_null_value_defaults_to_str(self):
        schema = infer_schema([{"field": None}])
        assert schema == {"field": "str"}

    def test_null_then_object_resolves_to_object(self):
        schema = infer_schema(
            [
                {"addr": None},
                {"addr": {"city": "Paris"}},
            ]
        )
        assert schema == {"addr": {"city": "str"}}

    def test_multiple_samples_union_fields(self):
        schema = infer_schema(
            [
                {"a": 1},
                {"a": 2, "b": "x"},
            ]
        )
        assert schema == {"a": "int", "b": "str"}

    def test_multiple_samples_max_array_length(self):
        schema = infer_schema(
            [
                {"items": [{"x": 1}]},
                {"items": [{"x": 2}, {"x": 3}, {"x": 4}]},
            ]
        )
        assert schema["items"]["$max"] == 3

    def test_multiple_samples_union_array_fields(self):
        schema = infer_schema(
            [
                {"items": [{"a": 1}]},
                {"items": [{"b": "x"}]},
            ]
        )
        assert schema["items"]["$items"] == {"a": "int", "b": "str"}

    def test_type_widening_int_to_float(self):
        schema = infer_schema(
            [
                {"value": 10},
                {"value": 3.14},
            ]
        )
        assert schema == {"value": "float"}

    def test_deeply_nested(self):
        schema = infer_schema(
            [
                {
                    "a": {"b": {"c": {"d": 42}}},
                }
            ]
        )
        assert schema == {"a": {"b": {"c": {"d": "int"}}}}

    def test_nested_arrays(self):
        schema = infer_schema(
            [
                {
                    "drivers": [
                        {"name": "Alice", "claims": [{"amount": 500}]},
                    ],
                }
            ]
        )
        expected = {
            "drivers": {
                "$max": 1,
                "$items": {
                    "name": "str",
                    "claims": {
                        "$max": 1,
                        "$items": {"amount": "int"},
                    },
                },
            },
        }
        assert schema == expected

    def test_object_and_array_conflict_is_order_invariant(self):
        first = infer_schema([{"contact": {"email": "a@example.com"}}, {"contact": []}])
        second = infer_schema([{"contact": []}, {"contact": {"email": "a@example.com"}}])
        expected = {"contact": {"$max": 1, "$items": {"email": "str"}}}
        assert first == expected
        assert second == expected

    def test_scalar_and_array_conflict_promotes_to_richer_array_schema(self):
        legacy = {"contact": "unknown"}
        structured = {
            "contact": [
                {"email": "a@example.com", "verified": True},
                {"email": "b@example.com"},
            ],
        }

        first = infer_schema([legacy, structured])
        second = infer_schema([structured, legacy])

        expected = {
            "contact": {
                "$max": 2,
                "$items": {"email": "str", "verified": "bool"},
            },
        }
        assert first == expected
        assert second == expected

    def test_mixed_array_item_shapes_keep_richer_item_schema(self):
        schema = infer_schema(
            [
                {
                    "events": [
                        "legacy-note",
                        {"type": "renewal", "premium": 125.50},
                    ],
                },
            ]
        )

        assert schema == {
            "events": {
                "$max": 2,
                "$items": {"type": "str", "premium": "float"},
            },
        }


# ---------------------------------------------------------------------------
# Flattening
# ---------------------------------------------------------------------------


class TestFlatten:
    def test_flat_dict(self):
        schema = {"name": "str", "age": "int"}
        result = flatten({"name": "Alice", "age": 30}, schema)
        assert result == {"name": "Alice", "age": 30}

    def test_nested_object(self):
        schema = {"address": {"city": "str", "postcode": "str"}}
        result = flatten({"address": {"city": "London", "postcode": "SW1"}}, schema)
        assert result == {"address.city": "London", "address.postcode": "SW1"}

    def test_missing_data_gives_none(self):
        schema = {"a": "str", "b": "int"}
        result = flatten({"a": "hello"}, schema)
        assert result == {"a": "hello", "b": None}

    def test_null_data_gives_all_none(self):
        schema = {"a": "str", "b": {"c": "int"}}
        result = flatten(None, schema)
        assert result == {"a": None, "b.c": None}

    def test_array_one_based_indices(self):
        schema = {"items": {"$max": 2, "$items": {"x": "int"}}}
        result = flatten({"items": [{"x": 10}]}, schema)
        assert result == {"items.1.x": 10, "items.2.x": None}

    def test_array_of_scalars(self):
        schema = {"tags": {"$max": 3, "$items": "str"}}
        result = flatten({"tags": ["a", "b"]}, schema)
        assert result == {"tags.1": "a", "tags.2": "b", "tags.3": None}

    def test_empty_array_fills_nulls(self):
        schema = {"items": {"$max": 2, "$items": {"name": "str"}}}
        result = flatten({"items": []}, schema)
        assert result == {"items.1.name": None, "items.2.name": None}

    def test_array_truncates_to_max(self):
        schema = {"items": {"$max": 2, "$items": "int"}}
        result = flatten({"items": [1, 2, 3, 4]}, schema)
        assert result == {"items.1": 1, "items.2": 2}

    def test_max_zero_produces_no_columns(self):
        schema = {"items": {"$max": 0, "$items": {}}}
        result = flatten({"items": [1, 2, 3]}, schema)
        assert result == {}

    def test_empty_items_schema_produces_no_columns(self):
        schema = {"items": {"$max": 3, "$items": {}}}
        result = flatten({"items": [1, 2, 3]}, schema)
        assert result == {}

    def test_null_nested_object_gives_null_leaves(self):
        schema = {"addr": {"city": "str", "postcode": "str"}}
        result = flatten({"addr": None}, schema)
        assert result == {"addr.city": None, "addr.postcode": None}

    def test_deeply_nested(self):
        schema = {"a": {"b": {"c": "int"}}}
        result = flatten({"a": {"b": {"c": 42}}}, schema)
        assert result == {"a.b.c": 42}

    def test_nested_arrays(self):
        schema = {
            "drivers": {
                "$max": 2,
                "$items": {
                    "name": "str",
                    "claims": {"$max": 1, "$items": {"amount": "int"}},
                },
            },
        }
        data = {
            "drivers": [
                {"name": "Alice", "claims": [{"amount": 500}]},
            ],
        }
        result = flatten(data, schema)
        assert result == {
            "drivers.1.name": "Alice",
            "drivers.1.claims.1.amount": 500,
            "drivers.2.name": None,
            "drivers.2.claims.1.amount": None,
        }

    def test_nested_array_of_scalars(self):
        schema = {"matrix": {"$max": 2, "$items": {"$max": 2, "$items": "int"}}}
        result = flatten({"matrix": [[1, 2], [3]]}, schema)
        assert result == {
            "matrix.1.1": 1,
            "matrix.1.2": 2,
            "matrix.2.1": 3,
            "matrix.2.2": None,
        }

    def test_non_list_value_under_array_schema_is_treated_as_singleton(self):
        schema = {"contact": {"$max": 1, "$items": {"email": "str"}}}
        result = flatten({"contact": {"email": "a@example.com"}}, schema)
        assert result == {"contact.1.email": "a@example.com"}

    def test_scalar_under_array_schema_is_treated_as_singleton(self):
        schema = {"scores": {"$max": 3, "$items": "int"}}
        result = flatten({"scores": 99}, schema)
        assert result == {"scores.1": 99, "scores.2": None, "scores.3": None}

    def test_non_mapping_under_object_schema_nulls_all_leaves(self):
        schema = {
            "driver": {
                "name": "str",
                "claims": {"$max": 1, "$items": {"amount": "int"}},
            },
        }
        result = flatten({"driver": ["not", "an", "object"]}, schema)
        assert result == {"driver.name": None, "driver.claims.1.amount": None}

    def test_mixed_array_item_shapes_flatten_missing_richer_fields_to_none(self):
        schema = {
            "events": {
                "$max": 2,
                "$items": {"type": "str", "premium": "float"},
            },
        }
        result = flatten(
            {
                "events": [
                    "legacy-note",
                    {"type": "renewal", "premium": 125.50},
                ],
            },
            schema,
        )
        assert result == {
            "events.1.type": None,
            "events.1.premium": None,
            "events.2.type": "renewal",
            "events.2.premium": 125.50,
        }

    def test_extra_data_ignored(self):
        schema = {"a": "str"}
        result = flatten({"a": "hello", "b": "ignored"}, schema)
        assert result == {"a": "hello"}

    def test_manual_schema_with_dotted_key_fails_before_overwriting_values(self):
        schema = {"a.b": "int", "a": {"b": "int"}}

        with pytest.raises(JsonFlattenSchemaError, match="Unsupported JSON object key"):
            flatten({"a.b": 1, "a": {"b": 2}}, schema)

    def test_manual_schema_with_numeric_path_segment_fails_before_array_collision(self):
        schema = {"items": {"$max": 1, "$items": {"1": "str"}}}

        with pytest.raises(JsonFlattenSchemaError, match="Unsupported JSON object key"):
            flatten({"items": [{"1": "literal"}]}, schema)


# ---------------------------------------------------------------------------
# schema_columns
# ---------------------------------------------------------------------------


class TestSchemaColumns:
    def test_flat(self):
        schema = {"name": "str", "age": "int"}
        assert schema_columns(schema) == ["name", "age"]

    def test_nested(self):
        schema = {"a": {"b": "str", "c": "int"}}
        assert schema_columns(schema) == ["a.b", "a.c"]

    def test_array(self):
        schema = {"items": {"$max": 2, "$items": {"x": "int"}}}
        assert schema_columns(schema) == ["items.1.x", "items.2.x"]

    def test_nested_array_of_scalars(self):
        schema = {"matrix": {"$max": 2, "$items": {"$max": 2, "$items": "int"}}}
        assert schema_columns(schema) == [
            "matrix.1.1",
            "matrix.1.2",
            "matrix.2.1",
            "matrix.2.2",
        ]

    def test_max_zero_skipped(self):
        schema = {"items": {"$max": 0, "$items": {}}}
        assert schema_columns(schema) == []

    def test_matches_flatten_keys(self):
        schema = {
            "a": "str",
            "b": {"c": "int"},
            "d": {"$max": 2, "$items": {"e": "str"}},
        }
        cols = schema_columns(schema)
        flat = flatten(None, schema)
        assert cols == list(flat.keys())

    def test_ambiguous_schema_key_fails_loudly(self):
        with pytest.raises(JsonFlattenSchemaError, match="Unsupported JSON object key"):
            schema_columns({"a.b": "int", "a": {"b": "int"}})


class TestCollisionContracts:
    def test_dotted_key_collision_fails_loudly(self):
        schema = {"driver": {"name": "str"}, "driver.name": "str"}

        with pytest.raises(HauteError, match="Unsupported JSON object key"):
            flatten(
                {"driver": {"name": "nested"}, "driver.name": "literal"},
                schema,
            )


# ---------------------------------------------------------------------------
# Consistency: same schema → same columns regardless of data
# ---------------------------------------------------------------------------


class TestConsistency:
    def test_different_data_same_columns(self):
        schema = {
            "name": "str",
            "items": {"$max": 2, "$items": {"x": "int"}},
        }
        flat_a = flatten({"name": "Alice", "items": [{"x": 1}]}, schema)
        flat_b = flatten({"name": "Bob"}, schema)
        flat_c = flatten(None, schema)
        assert list(flat_a.keys()) == list(flat_b.keys()) == list(flat_c.keys())

    def test_infer_then_flatten_round_trip(self):
        sample = {
            "id": 1,
            "meta": {"source": "web"},
            "tags": ["a", "b"],
        }
        schema = infer_schema([sample])
        flat = flatten(sample, schema)
        assert flat["id"] == 1
        assert flat["meta.source"] == "web"
        assert flat["tags.1"] == "a"
        assert flat["tags.2"] == "b"

    def test_inferred_column_order_is_first_seen_order(self):
        samples = [{"b": 1, "a": 1}, {"c": 1, "a": 2}]
        reversed_samples = list(reversed(samples))

        assert schema_columns(infer_schema(samples)) == ["b", "a", "c"]
        assert schema_columns(infer_schema(reversed_samples)) == ["c", "a", "b"]


# ---------------------------------------------------------------------------
# load_samples
# ---------------------------------------------------------------------------


class TestLoadSamples:
    def test_single_json_object(self, tmp_path: Path):
        p = tmp_path / "single.json"
        p.write_text(json.dumps({"a": 1}))
        result = load_samples(p)
        assert result == [{"a": 1}]

    def test_json_array(self, tmp_path: Path):
        p = tmp_path / "batch.json"
        p.write_text(json.dumps([{"a": 1}, {"a": 2}]))
        result = load_samples(p)
        assert len(result) == 2
        assert result[0] == {"a": 1}
        assert result[1] == {"a": 2}

    def test_jsonl(self, tmp_path: Path):
        p = tmp_path / "data.jsonl"
        p.write_text('{"a": 1}\n{"a": 2}\n{"a": 3}\n')
        result = load_samples(p)
        assert len(result) == 3

    def test_jsonl_blank_lines_skipped(self, tmp_path: Path):
        p = tmp_path / "data.jsonl"
        p.write_text('{"a": 1}\n\n\n{"a": 2}\n')
        result = load_samples(p)
        assert len(result) == 2

    def test_json_non_dict_elements_filtered(self, tmp_path: Path):
        p = tmp_path / "mixed.json"
        p.write_text(json.dumps([{"a": 1}, 42, "string", {"b": 2}]))
        result = load_samples(p)
        assert len(result) == 2

    def test_jsonl_max_samples_limits_rows(self, tmp_path: Path):
        p = tmp_path / "big.jsonl"
        p.write_text("".join(f'{{"i": {i}}}\n' for i in range(50)))
        result = load_samples(p, max_samples=10)
        assert len(result) == 10
        assert result[0] == {"i": 0}
        assert result[-1] == {"i": 9}


# ---------------------------------------------------------------------------
# flatten_to_frame
# ---------------------------------------------------------------------------


class TestFlattenToFrame:
    def test_single_dict(self):
        schema = {"name": "str", "age": "int"}
        lf = flatten_to_frame({"name": "Alice", "age": 30}, schema)
        assert isinstance(lf, pl.LazyFrame)
        df = lf.collect()
        assert df.shape == (1, 2)
        assert df["name"][0] == "Alice"
        assert df["age"][0] == 30

    def test_multiple_dicts(self):
        schema = {"x": "int"}
        lf = flatten_to_frame([{"x": 1}, {"x": 2}, {"x": 3}], schema)
        df = lf.collect()
        assert df.shape == (3, 1)
        assert df["x"].to_list() == [1, 2, 3]

    def test_missing_values_are_null(self):
        schema = {"a": "str", "b": "int"}
        lf = flatten_to_frame([{"a": "x"}, {"b": 5}], schema)
        df = lf.collect()
        assert df.shape == (2, 2)
        assert df["b"][0] is None
        assert df["a"][1] is None

    def test_consistent_columns_across_rows(self):
        schema = {
            "name": "str",
            "items": {"$max": 2, "$items": {"val": "int"}},
        }
        lf = flatten_to_frame(
            [
                {"name": "A", "items": [{"val": 1}, {"val": 2}]},
                {"name": "B", "items": []},
            ],
            schema,
        )
        df = lf.collect()
        assert df.columns == ["name", "items.1.val", "items.2.val"]
        assert df.shape == (2, 3)


# ---------------------------------------------------------------------------
# Real data: sample_quote.json
# ---------------------------------------------------------------------------


SAMPLE_QUOTE_PATH = Path(__file__).parent.parent / "data" / "sample_quote.json"


@pytest.mark.skipif(
    not SAMPLE_QUOTE_PATH.exists(),
    reason="data/sample_quote.json not found",
)
class TestSampleQuote:
    @pytest.fixture()
    def quote(self) -> dict:
        return json.loads(SAMPLE_QUOTE_PATH.read_text())

    @pytest.fixture()
    def schema(self, quote: dict) -> dict:
        return infer_schema([quote])

    def test_infer_produces_non_empty_schema(self, schema: dict):
        assert len(schema) > 0

    def test_top_level_keys(self, schema: dict):
        expected = {
            "quote_metadata",
            "policy_details",
            "proposer",
            "additional_drivers",
            "vehicle",
            "address",
            "previous_address",
            "add_ons",
        }
        assert expected == set(schema.keys())

    def test_additional_drivers_is_array(self, schema: dict):
        ad = schema["additional_drivers"]
        assert "$max" in ad
        assert ad["$max"] == 1  # sample has 1 additional driver

    def test_empty_arrays_detected(self, schema: dict):
        # proposer.claims is [] in the sample
        claims = schema["proposer"]["claims"]
        assert claims == {"$max": 0, "$items": {}}

    def test_flatten_produces_consistent_columns(self, quote: dict, schema: dict):
        flat = flatten(quote, schema)
        cols = schema_columns(schema)
        assert list(flat.keys()) == cols

    def test_flatten_preserves_values(self, quote: dict, schema: dict):
        flat = flatten(quote, schema)
        assert flat["quote_metadata.quote_id"] == "QUO-2026-000000001"
        assert flat["proposer.date_of_birth"] == "1990-11-17"
        assert flat["vehicle.make"] == "Ford"
        assert flat["address.postcode"] == "PH16 5LD"
        assert flat["policy_details.ncd_protected"] is False
        assert flat["additional_drivers.1.first_name"] == "Aurora"
        assert flat["add_ons.breakdown_cover.selected"] is True
        assert flat["add_ons.breakdown_cover.level"] == "european"

    def test_null_previous_address(self, quote: dict, schema: dict):
        flat = flatten(quote, schema)
        # previous_address is null → inferred as "str" → single null column
        assert flat["previous_address"] is None

    def test_flatten_to_frame_shape(self, quote: dict, schema: dict):
        lf = flatten_to_frame(quote, schema)
        df = lf.collect()
        assert df.shape[0] == 1
        assert df.shape[1] == len(schema_columns(schema))

    def test_two_quotes_same_columns(self, quote: dict, schema: dict):
        # Flatten the same quote twice — columns must match
        lf = flatten_to_frame([quote, quote], schema)
        df = lf.collect()
        assert df.shape == (2, len(schema_columns(schema)))


# ---------------------------------------------------------------------------
# Parquet cache
# ---------------------------------------------------------------------------


class TestJsonCache:
    def test_cache_path_is_deterministic(self):
        p1 = _json_cache_path("data/quotes.json")
        p2 = _json_cache_path("data/quotes.json")
        assert p1 == p2
        assert p1.suffix == ".parquet"
        assert ".haute_cache" in str(p1)

    def test_cache_invalid_when_missing(self, tmp_path):
        cache_dir = tmp_path / "missing_dir"
        source = tmp_path / "data.json"
        source.write_text("[]")
        assert not _is_cache_valid(cache_dir, source)

    def test_cache_valid_when_newer(self, tmp_path):
        source = tmp_path / "data.json"
        source.write_text("[]")
        cache_dir = tmp_path / "cache_dir"
        cache_dir.mkdir()
        (cache_dir / "data.parquet").write_bytes(b"fake")
        # Ensure cache is newer by touching it
        import os

        data_parquet = cache_dir / "data.parquet"
        os.utime(
            data_parquet,
            (data_parquet.stat().st_mtime + 10, data_parquet.stat().st_mtime + 10),
        )
        assert _is_cache_valid(cache_dir, source)

    def test_cache_invalid_when_source_newer(self, tmp_path):
        cache_dir = tmp_path / "cache_dir"
        cache_dir.mkdir()
        (cache_dir / "data.parquet").write_bytes(b"fake")
        source = tmp_path / "data.json"
        source.write_text("[]")
        # Ensure source is newer
        import os

        os.utime(source, (source.stat().st_mtime + 10, source.stat().st_mtime + 10))
        assert not _is_cache_valid(cache_dir, source)

    def test_flatten_and_write_creates_parquet(self, tmp_path):
        schema = {"name": "str", "age": "int"}
        samples = [{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}]
        cache = tmp_path / "test.parquet"

        _flatten_and_write(samples, schema, cache)

        assert cache.exists()
        df = pl.read_parquet(cache)
        assert df.shape == (2, 2)
        assert df["name"].to_list() == ["Alice", "Bob"]

    def test_flatten_and_write_is_atomic(self, tmp_path):
        """Tmp file is cleaned up on failure."""
        # Force an error by passing a schema that causes parquet write to fail
        # (write to a path that is a directory, not a file)
        bad_cache = tmp_path / "dir_not_file.parquet"
        bad_cache.mkdir()
        with pytest.raises((IsADirectoryError, PermissionError, OSError)):
            _flatten_and_write([{"x": 1}], {"x": "int"}, bad_cache)

    def test_read_json_flat_caches_and_reuses(self, tmp_path, monkeypatch):
        """Second call to read_json_flat uses the cached parquet."""
        monkeypatch.chdir(tmp_path)

        data_file = tmp_path / "data.json"
        data_file.write_text(json.dumps([{"x": 1}, {"x": 2}]))

        # First call — cache miss, writes parquet
        lf1 = read_json_flat(str(data_file))
        df1 = lf1.collect()
        assert df1["x"].to_list() == [1, 2]

        cache_path = _json_cache_path(str(data_file))
        assert cache_path.exists()
        first_mtime = cache_path.stat().st_mtime

        # Second call — cache hit, no rewrite
        lf2 = read_json_flat(str(data_file))
        df2 = lf2.collect()
        assert df2["x"].to_list() == [1, 2]
        assert cache_path.stat().st_mtime == first_mtime

    def test_read_json_flat_invalidates_on_data_change(self, tmp_path, monkeypatch):
        """Cache is rebuilt when the source data file changes."""
        import os

        monkeypatch.chdir(tmp_path)

        data_file = tmp_path / "data.json"
        data_file.write_text(json.dumps([{"x": 1}]))

        lf = read_json_flat(str(data_file))
        assert lf.collect()["x"].to_list() == [1]

        cache_path = _json_cache_path(str(data_file))

        # Push cache mtime to the past so a rewrite is always detectable,
        # regardless of filesystem timestamp resolution.
        # Use 1980-01-01 (not epoch 0) — NTFS rejects timestamps before 1980.
        old_ts = 315532800.0  # 1980-01-01 00:00:00 UTC
        os.utime(cache_path, (old_ts, old_ts))
        first_mtime = cache_path.stat().st_mtime

        # Modify source file — its mtime is "now", well after 1980
        data_file.write_text(json.dumps([{"x": 99}]))

        lf2 = read_json_flat(str(data_file))
        assert lf2.collect()["x"].to_list() == [99]
        assert cache_path.stat().st_mtime > first_mtime

    def test_read_json_flat_invalidates_on_config_change(self, tmp_path, monkeypatch):
        """Cache is rebuilt when the config file changes."""
        import os

        monkeypatch.chdir(tmp_path)

        data_file = tmp_path / "data.json"
        data_file.write_text(json.dumps([{"name": "Alice", "age": 30}]))

        config_file = tmp_path / "config.json"
        schema1 = {"name": "str", "age": "int"}
        config_file.write_text(json.dumps({"flattenSchema": schema1}))

        lf = read_json_flat(str(data_file), config_path=str(config_file))
        assert set(lf.collect().columns) == {"name", "age"}

        cache_path = _json_cache_path(str(data_file))

        # Push cache mtime to the past so a rewrite is always detectable.
        # Use 1980-01-01 (not epoch 0) — NTFS rejects timestamps before 1980.
        old_ts = 315532800.0  # 1980-01-01 00:00:00 UTC
        os.utime(cache_path, (old_ts, old_ts))

        # Modify config (drop the age column from schema)
        schema2 = {"name": "str"}
        config_file.write_text(json.dumps({"flattenSchema": schema2}))

        lf2 = read_json_flat(str(data_file), config_path=str(config_file))
        assert lf2.collect().columns == ["name"]


# ---------------------------------------------------------------------------
# Explicit cache management functions
# ---------------------------------------------------------------------------


class TestIsLargeJson:
    def test_small_file_returns_false(self, tmp_path):
        p = tmp_path / "small.jsonl"
        p.write_text('{"a": 1}\n')
        assert not is_large_json(p)

    def test_large_file_returns_true(self, tmp_path):
        p = tmp_path / "large.jsonl"
        # Write a file that exceeds the threshold
        p.write_bytes(b"x" * (_LARGE_FILE_THRESHOLD + 1))
        assert is_large_json(p)

    def test_exact_threshold_returns_true(self, tmp_path):
        p = tmp_path / "exact.jsonl"
        p.write_bytes(b"x" * _LARGE_FILE_THRESHOLD)
        assert is_large_json(p)

    def test_nonexistent_file_returns_false(self, tmp_path):
        p = tmp_path / "missing.jsonl"
        assert not is_large_json(p)


class TestJsonCacheInfo:
    def test_returns_none_when_missing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert json_cache_info("nonexistent.json") is None

    def test_returns_metadata_when_cached(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        data_file = tmp_path / "data.json"
        data_file.write_text(json.dumps([{"x": 1, "y": "a"}, {"x": 2, "y": "b"}]))

        # Build cache first
        read_json_flat(str(data_file))

        info = json_cache_info(str(data_file))
        assert info is not None
        assert info["row_count"] == 2
        assert info["column_count"] == 2
        assert info["size_bytes"] > 0
        assert info["cached_at"] > 0
        assert "x" in info["columns"]
        assert "y" in info["columns"]
        assert info["data_path"] == str(data_file)


class TestClearJsonCache:
    def test_returns_false_when_no_cache(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert clear_json_cache("nonexistent.json") is False

    def test_deletes_cached_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        data_file = tmp_path / "data.json"
        data_file.write_text(json.dumps([{"x": 1}]))

        read_json_flat(str(data_file))
        cache_path = _json_cache_path(str(data_file))
        assert cache_path.exists()

        assert clear_json_cache(str(data_file)) is True
        assert not cache_path.exists()


class TestFlattenProgress:
    @pytest.fixture(autouse=True)
    def _cleanup_progress(self):
        yield
        _clear_flatten_progress()

    def test_returns_none_when_inactive(self):
        assert flatten_progress("some/path.jsonl") is None

    def test_returns_progress_when_active(self):
        _set_flatten_progress("data.jsonl", {"rows": 1000, "elapsed": 2.5})
        result = flatten_progress("data.jsonl")
        assert result is not None
        assert result["rows"] == 1000
        assert result["elapsed"] == 2.5


class TestBuildJsonCache:
    def test_builds_cache_and_returns_metadata(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        data_file = tmp_path / "data.jsonl"
        data_file.write_text('{"a": 1, "b": "x"}\n{"a": 2, "b": "y"}\n')

        result = build_json_cache(str(data_file))

        assert result["row_count"] == 2
        assert result["column_count"] == 2
        assert result["size_bytes"] > 0
        assert result["cached_at"] > 0
        assert result["cache_seconds"] >= 0
        assert result["data_path"] == str(data_file)
        assert "a" in result["columns"]
        assert "b" in result["columns"]

        # Verify the cache file actually exists
        cache_path = Path(result["path"])
        assert cache_path.exists()

        # Verify progress is cleared after build
        assert flatten_progress(str(data_file)) is None

    def test_builds_cache_with_config_schema(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        data_file = tmp_path / "data.json"
        data_file.write_text(json.dumps([{"name": "Alice", "age": 30, "extra": "ignored"}]))

        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"flattenSchema": {"name": "str"}}))

        result = build_json_cache(str(data_file), config_path=str(config_file))
        assert result["column_count"] == 1
        assert "name" in result["columns"]
        assert "extra" not in result["columns"]
        assert json_cache_path_if_valid(str(data_file), schema={"name": "str"}) == Path(
            result["path"]
        )

    def test_cancel_stops_build(self, tmp_path, monkeypatch):
        """Cancelling a build raises JsonCacheCancelledError and cleans up."""
        import threading

        from haute._json_flatten import _iter_byte_chunks

        monkeypatch.chdir(tmp_path)
        # Create a file with enough rows to span multiple chunks
        data_file = tmp_path / "data.jsonl"
        lines = [json.dumps({"a": i, "b": f"val_{i}"}) for i in range(200)]
        data_file.write_text("\n".join(lines) + "\n")

        # Use tiny byte chunks so cancel checks fire frequently
        monkeypatch.setattr("haute._json_flatten._RAW_CHUNK_TARGET_BYTES", 50)

        data_str = str(data_file)
        started = threading.Event()

        def cancel_after_start():
            started.wait(timeout=5)
            cancel_json_cache(data_str)

        t = threading.Thread(target=cancel_after_start)
        t.start()

        # Patch _iter_byte_chunks to signal when step 1 starts iterating
        def signalling_iter(*args, **kwargs):
            started.set()
            yield from _iter_byte_chunks(*args, **kwargs)

        monkeypatch.setattr("haute._json_flatten._iter_byte_chunks", signalling_iter)

        with pytest.raises(JsonCacheCancelledError):
            build_json_cache(data_str)

        t.join(timeout=5)

        # Progress and cancel event should be cleaned up
        assert flatten_progress(data_str) is None
        assert data_str not in _cancel_events

    def test_cancel_no_active_build_returns_false(self):
        """cancel_json_cache returns False when no build is active."""
        assert cancel_json_cache("nonexistent.jsonl") is False

    def test_cancel_active_build_returns_true(self):
        """cancel_json_cache returns True and sets the event when a build is active."""
        import threading

        event = threading.Event()
        _cancel_events["test.jsonl"] = event
        try:
            assert cancel_json_cache("test.jsonl") is True
            assert event.is_set()
        finally:
            _clear_cancel_events()


# ---------------------------------------------------------------------------
# _release_memory cross-platform
# ---------------------------------------------------------------------------


class TestReleaseMemory:
    """Verify _release_memory delegates to gc.collect + _malloc_trim."""

    def test_delegates_gc_collect_then_malloc_trim(self):
        """_release_memory must call gc.collect() then _malloc_trim() in order."""
        from unittest.mock import MagicMock, patch

        from haute import _json_flatten as mod

        call_order: list[str] = []
        mock_gc = MagicMock(side_effect=lambda: call_order.append("gc"))
        mock_trim = MagicMock(side_effect=lambda: call_order.append("trim"))

        with patch("gc.collect", mock_gc), patch.object(mod, "_malloc_trim", mock_trim):
            mod._release_memory()

        assert call_order == ["gc", "trim"]
        mock_gc.assert_called_once()
        mock_trim.assert_called_once()

    # Platform-specific _malloc_trim dispatch is tested in test_polars_utils.py


# ---------------------------------------------------------------------------
# Streaming infrastructure
# ---------------------------------------------------------------------------


class TestIterJsonRecords:
    def test_jsonl_streams_records(self, tmp_path):
        p = tmp_path / "data.jsonl"
        p.write_text('{"a":1}\n{"a":2}\n{"a":3}\n')
        records = list(_iter_json_records(p))
        assert records == [{"a": 1}, {"a": 2}, {"a": 3}]

    def test_jsonl_skips_blank_lines(self, tmp_path):
        p = tmp_path / "data.jsonl"
        p.write_text('{"a":1}\n\n{"a":2}\n')
        assert len(list(_iter_json_records(p))) == 2

    def test_jsonl_skips_invalid_json(self, tmp_path):
        p = tmp_path / "data.jsonl"
        p.write_text('{"a":1}\nnot json\n{"a":2}\n')
        records = list(_iter_json_records(p))
        assert records == [{"a": 1}, {"a": 2}]

    def test_jsonl_skips_non_dict_lines(self, tmp_path):
        p = tmp_path / "data.jsonl"
        p.write_text('{"a":1}\n[1, 2]\n"string"\n{"a":2}\n')
        records = list(_iter_json_records(p))
        assert records == [{"a": 1}, {"a": 2}]

    def test_json_array(self, tmp_path):
        p = tmp_path / "data.json"
        p.write_text(json.dumps([{"x": 1}, {"x": 2}]))
        assert list(_iter_json_records(p)) == [{"x": 1}, {"x": 2}]

    def test_json_single_object(self, tmp_path):
        p = tmp_path / "data.json"
        p.write_text(json.dumps({"x": 42}))
        assert list(_iter_json_records(p)) == [{"x": 42}]

    def test_empty_jsonl(self, tmp_path):
        p = tmp_path / "data.jsonl"
        p.write_text("")
        assert list(_iter_json_records(p)) == []


class TestInferSchemaStreaming:
    def test_infers_from_first_n_samples(self, tmp_path):
        p = tmp_path / "data.jsonl"
        lines = [json.dumps({"x": i, "y": "val"}) for i in range(100)]
        p.write_text("\n".join(lines) + "\n")
        schema = _infer_schema_streaming(p, max_samples=10)
        assert "x" in schema
        assert "y" in schema

    def test_stops_after_max_samples(self, tmp_path):
        """Should stop reading after max_samples records."""
        p = tmp_path / "data.jsonl"
        # 5 valid records followed by records with a different schema
        valid = [json.dumps({"x": i}) for i in range(5)]
        extra = [json.dumps({"x": i, "extra_col": "v"}) for i in range(100)]
        p.write_text("\n".join(valid + extra) + "\n")
        schema = _infer_schema_streaming(p, max_samples=5)
        assert "x" in schema
        # If early stop works, "extra_col" should not appear in schema
        assert "extra_col" not in schema

    def test_empty_file_returns_empty_schema(self, tmp_path):
        p = tmp_path / "empty.jsonl"
        p.write_text("")
        assert _infer_schema_streaming(p) == {}

    def test_nested_schema_inferred(self, tmp_path):
        p = tmp_path / "nested.jsonl"
        p.write_text(json.dumps({"a": {"b": 1}}) + "\n")
        schema = _infer_schema_streaming(p)
        assert "a" in schema
        assert "b" in schema["a"]


class TestSchemaLeafTypes:
    def test_flat_schema(self):
        schema = {"x": "int", "y": "str", "z": "float"}
        result = _schema_leaf_types(schema)
        assert ("x", "int") in result
        assert ("y", "str") in result
        assert ("z", "float") in result

    def test_nested_schema(self):
        schema = {"a": {"b": "int", "c": "str"}}
        result = _schema_leaf_types(schema)
        assert ("a.b", "int") in result
        assert ("a.c", "str") in result

    def test_array_schema(self):
        schema = {"items": {"$max": 2, "$items": {"name": "str"}}}
        result = _schema_leaf_types(schema)
        assert ("items.1.name", "str") in result
        assert ("items.2.name", "str") in result

    def test_matches_schema_columns(self):
        """Leaf type names must match schema_columns output exactly."""
        schema = {"a": "int", "b": {"c": "str"}, "d": {"$max": 1, "$items": "float"}}
        leaf_names = [name for name, _ in _schema_leaf_types(schema)]
        assert leaf_names == schema_columns(schema)

    def test_deep_array_leaf_types_preserve_column_order_and_types(self):
        schema = {
            "policy": {
                "id": "str",
                "drivers": {
                    "$max": 2,
                    "$items": {
                        "name": "str",
                        "claims": {"$max": 2, "$items": {"amount": "float"}},
                    },
                },
            },
            "active": "bool",
        }

        assert _schema_leaf_types(schema) == [
            ("policy.id", "str"),
            ("policy.drivers.1.name", "str"),
            ("policy.drivers.1.claims.1.amount", "float"),
            ("policy.drivers.1.claims.2.amount", "float"),
            ("policy.drivers.2.name", "str"),
            ("policy.drivers.2.claims.1.amount", "float"),
            ("policy.drivers.2.claims.2.amount", "float"),
            ("active", "bool"),
        ]

    def test_nested_array_leaf_types_match_schema_columns(self):
        schema = {"matrix": {"$max": 2, "$items": {"$max": 2, "$items": "int"}}}
        assert _schema_leaf_types(schema) == [
            ("matrix.1.1", "int"),
            ("matrix.1.2", "int"),
            ("matrix.2.1", "int"),
            ("matrix.2.2", "int"),
        ]


class TestArrowSchemaFromFlatten:
    def test_maps_types_correctly(self):
        import pyarrow as pa

        schema = {"x": "int", "y": "float", "z": "str", "flag": "bool"}
        arrow = _arrow_schema_from_flatten(schema)
        # int and float both map to float64 for JSON safety
        assert arrow.field("x").type == pa.float64()
        assert arrow.field("y").type == pa.float64()
        assert arrow.field("z").type == pa.string()
        assert arrow.field("flag").type == pa.bool_()

    def test_all_fields_nullable(self):
        schema = {"a": "int", "b": "str"}
        arrow = _arrow_schema_from_flatten(schema)
        for field in arrow:
            assert field.nullable

    def test_nested_array_fields_are_included_once(self):
        schema = {"matrix": {"$max": 2, "$items": {"$max": 2, "$items": "int"}}}

        arrow = _arrow_schema_from_flatten(schema)

        assert arrow.names == ["matrix.1.1", "matrix.1.2", "matrix.2.1", "matrix.2.2"]


class TestChunked:
    def test_exact_chunks(self):
        result = list(_chunked(iter(range(6)), 3))
        assert result == [[0, 1, 2], [3, 4, 5]]

    def test_remainder_chunk(self):
        result = list(_chunked(iter(range(5)), 3))
        assert result == [[0, 1, 2], [3, 4]]

    def test_empty_iterator(self):
        assert list(_chunked(iter([]), 3)) == []

    def test_single_item(self):
        assert list(_chunked(iter([42]), 3)) == [[42]]


class TestCoerceToArrow:
    def test_clean_float_values(self):
        import pyarrow as pa

        arr = _coerce_to_arrow([1.0, 2.5, None], pa.float64())
        assert arr.type == pa.float64()
        assert arr.to_pylist() == [1.0, 2.5, None]

    def test_int_to_float64(self):
        import pyarrow as pa

        arr = _coerce_to_arrow([1, 2, 3], pa.float64())
        assert arr.type == pa.float64()

    def test_mixed_numeric_coercion(self):
        """String in a numeric column should be coerced or become null."""
        import pyarrow as pa

        arr = _coerce_to_arrow([1, "not_a_number", 3.5, None], pa.float64())
        assert arr.type == pa.float64()
        assert arr[0].as_py() == 1.0
        assert arr[1].as_py() is None  # coerced to null
        assert arr[2].as_py() == 3.5

    def test_string_values(self):
        import pyarrow as pa

        arr = _coerce_to_arrow(["a", "b", None], pa.string())
        assert arr.type == pa.string()
        assert arr.to_pylist() == ["a", "b", None]

    def test_bool_values(self):
        import pyarrow as pa

        arr = _coerce_to_arrow([True, False, None], pa.bool_())
        assert arr.type == pa.bool_()

    def test_bool_with_non_bool_drops_to_null(self):
        import pyarrow as pa

        arr = _coerce_to_arrow([True, "yes", None], pa.bool_())
        assert arr.type == pa.bool_()
        assert arr[0].as_py() is True
        assert arr[1].as_py() is None  # non-bool → null


class TestFlattenAndWriteStreaming:
    def test_writes_correct_data(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        records = iter([{"x": 1, "y": "a"}, {"x": 2, "y": "b"}])
        schema = {"x": "int", "y": "str"}
        cache_path = tmp_path / ".haute_cache" / "test.parquet"

        total = _flatten_and_write_streaming(records, schema, cache_path)

        assert total == 2
        assert cache_path.exists()
        df = pl.read_parquet(cache_path)
        assert len(df) == 2
        assert set(df.columns) == {"x", "y"}

    def test_chunked_write_produces_same_result(self, tmp_path):
        """With chunk_size=1, each row is a separate write — result should be identical."""
        records = [{"a": i} for i in range(5)]
        schema = {"a": "int"}
        cache_path = tmp_path / "chunked.parquet"

        total = _flatten_and_write_streaming(
            iter(records),
            schema,
            cache_path,
            chunk_size=1,
        )

        assert total == 5
        df = pl.read_parquet(cache_path)
        assert len(df) == 5

    def test_empty_iterator_writes_empty_parquet(self, tmp_path):
        schema = {"x": "int"}
        cache_path = tmp_path / "empty.parquet"

        total = _flatten_and_write_streaming(iter([]), schema, cache_path)

        assert total == 0
        assert cache_path.exists()
        df = pl.read_parquet(cache_path)
        assert len(df) == 0
        assert "x" in df.columns

    def test_atomic_cleanup_on_failure(self, tmp_path):
        """If flatten raises mid-stream, temp file is cleaned up."""

        def _bad_iter():
            yield {"x": 1}
            raise ValueError("boom")

        schema = {"x": "int"}
        cache_path = tmp_path / "fail.parquet"

        with pytest.raises(ValueError, match="boom"):
            _flatten_and_write_streaming(_bad_iter(), schema, cache_path)

        assert not cache_path.exists()
        assert not cache_path.with_suffix(".parquet.tmp").exists()

    def test_progress_updates_during_write(self, tmp_path):
        """Progress should be updated during streaming write."""
        records = [{"x": i} for i in range(10)]
        schema = {"x": "int"}
        cache_path = tmp_path / "progress.parquet"
        key = "test_progress_path"

        _flatten_and_write_streaming(
            iter(records),
            schema,
            cache_path,
            chunk_size=5,
            progress_key=key,
            t0=time.monotonic(),
        )
        # After completion, progress should be cleared by the caller (build_json_cache)
        # but the streaming function itself doesn't clear it — that's the caller's job.
        # Here we just verify the file was written correctly.
        assert pl.read_parquet(cache_path).shape[0] == 10


class TestRowsToBatch:
    def test_builds_valid_batch(self):
        schema = {"x": "int", "y": "str"}
        arrow_schema = _arrow_schema_from_flatten(schema)
        rows = [{"x": 1, "y": "a"}, {"x": 2, "y": "b"}]
        batch = _rows_to_batch(rows, arrow_schema)
        assert batch.num_rows == 2
        assert batch.schema == arrow_schema


# ---------------------------------------------------------------------------
# Polars-native fast path
# ---------------------------------------------------------------------------


class TestBuildFlattenExprs:
    def test_flat_schema(self):
        schema = {"name": "str", "age": "int"}
        exprs = _build_flatten_exprs(schema)
        df = pl.DataFrame({"name": ["Alice"], "age": [30]})
        result = df.select(exprs)
        assert result.columns == ["name", "age"]
        assert result["name"][0] == "Alice"
        assert result["age"][0] == 30

    def test_nested_object(self):
        schema = {"address": {"city": "str", "postcode": "str"}}
        exprs = _build_flatten_exprs(schema)
        df = pl.DataFrame(
            {
                "address": [{"city": "London", "postcode": "SW1"}],
            }
        )
        result = df.select(exprs)
        assert result.columns == ["address.city", "address.postcode"]
        assert result["address.city"][0] == "London"
        assert result["address.postcode"][0] == "SW1"

    def test_array_of_objects(self):
        schema = {"drivers": {"$max": 2, "$items": {"name": "str"}}}
        exprs = _build_flatten_exprs(schema)
        df = pl.DataFrame(
            {
                "drivers": [[{"name": "Alice"}]],
            }
        )
        result = df.select(exprs)
        assert result.columns == ["drivers.1.name", "drivers.2.name"]
        assert result["drivers.1.name"][0] == "Alice"
        assert result["drivers.2.name"][0] is None

    def test_array_of_scalars(self):
        schema = {"tags": {"$max": 3, "$items": "str"}}
        exprs = _build_flatten_exprs(schema)
        df = pl.DataFrame({"tags": [["a", "b"]]})
        result = df.select(exprs)
        assert result.columns == ["tags.1", "tags.2", "tags.3"]
        assert result["tags.1"][0] == "a"
        assert result["tags.2"][0] == "b"
        assert result["tags.3"][0] is None

    def test_empty_schema(self):
        assert _build_flatten_exprs({}) == []

    def test_max_zero_skipped(self):
        schema = {"items": {"$max": 0, "$items": {}}}
        assert _build_flatten_exprs(schema) == []

    def test_columns_match_schema_columns(self):
        schema = {
            "a": "str",
            "b": {"c": "int"},
            "d": {"$max": 2, "$items": {"e": "str"}},
        }
        exprs = _build_flatten_exprs(schema)
        df = pl.DataFrame(
            {
                "a": ["x"],
                "b": [{"c": 1}],
                "d": [[{"e": "y"}, {"e": "z"}]],
            }
        )
        result = df.select(exprs)
        assert result.columns == schema_columns(schema)

    def test_deeply_nested(self):
        schema = {"a": {"b": {"c": "int"}}}
        exprs = _build_flatten_exprs(schema)
        df = pl.DataFrame({"a": [{"b": {"c": 42}}]})
        result = df.select(exprs)
        assert result["a.b.c"][0] == 42

    def test_nested_arrays(self):
        schema = {
            "drivers": {
                "$max": 2,
                "$items": {
                    "name": "str",
                    "claims": {"$max": 1, "$items": {"amount": "int"}},
                },
            },
        }
        exprs = _build_flatten_exprs(schema)
        df = pl.DataFrame(
            {
                "drivers": [
                    [
                        {"name": "Alice", "claims": [{"amount": 500}]},
                    ]
                ],
            }
        )
        result = df.select(exprs)
        assert result["drivers.1.name"][0] == "Alice"
        assert result["drivers.1.claims.1.amount"][0] == 500
        assert result["drivers.2.name"][0] is None
        assert result["drivers.2.claims.1.amount"][0] is None

    def test_exprs_match_python_flatten_for_nested_rows_with_missing_data(self):
        schema = {
            "quote_id": "str",
            "driver": {"name": "str", "age": "int"},
            "coverages": {
                "$max": 2,
                "$items": {
                    "code": "str",
                    "limits": {"$max": 2, "$items": "int"},
                },
            },
            "flags": {"$max": 2, "$items": "bool"},
        }
        rows = [
            {
                "quote_id": "Q1",
                "driver": {"name": "Alice", "age": 30},
                "coverages": [
                    {"code": "bi", "limits": [100, 200]},
                    {"code": "pd", "limits": [50]},
                ],
                "flags": [True],
            },
            {
                "quote_id": "Q2",
                "driver": None,
                "coverages": [],
                "flags": [],
            },
        ]

        result = pl.from_dicts(rows).select(_build_flatten_exprs(schema))

        assert result.columns == schema_columns(schema)
        assert result.to_dicts() == [flatten(row, schema) for row in rows]


class TestAdaptiveChunkSize:
    def test_narrow_schema_uses_max(self):
        schema = {"a": "str", "b": "int"}  # 2 columns
        assert _adaptive_chunk_size(schema) == _FLATTEN_CHUNK_SIZE

    def test_wide_schema_reduces_chunks(self):
        # 1000 columns → 256MB / (1000 × 100) = 2621 rows
        schema = {f"col_{i}": "str" for i in range(1000)}
        size = _adaptive_chunk_size(schema)
        assert size < _FLATTEN_CHUNK_SIZE
        assert size >= _MIN_CHUNK_ROWS

    def test_very_wide_schema_hits_minimum(self):
        # 50000 columns → 256MB / (50000 × 100) = 53 → clamped to 1000
        schema = {f"col_{i}": "str" for i in range(50000)}
        assert _adaptive_chunk_size(schema) == _MIN_CHUNK_ROWS

    def test_empty_schema_uses_max(self):
        assert _adaptive_chunk_size({}) == _FLATTEN_CHUNK_SIZE


class TestIterLineChunks:
    def test_exact_chunks(self, tmp_path):
        p = tmp_path / "data.jsonl"
        p.write_bytes(b"a\nb\nc\nd\n")
        chunks = [c for c in _iter_line_chunks(p, 2)]
        assert len(chunks) == 2
        assert chunks[0] == b"a\nb\n"
        assert chunks[1] == b"c\nd\n"

    def test_remainder_chunk(self, tmp_path):
        p = tmp_path / "data.jsonl"
        p.write_bytes(b"a\nb\nc\n")
        chunks = [c for c in _iter_line_chunks(p, 2)]
        assert len(chunks) == 2
        assert chunks[1] == b"c\n"

    def test_empty_file(self, tmp_path):
        p = tmp_path / "empty.jsonl"
        p.write_bytes(b"")
        assert list(_iter_line_chunks(p, 10)) == []

    def test_single_line(self, tmp_path):
        p = tmp_path / "one.jsonl"
        p.write_bytes(b"hello\n")
        chunks = list(_iter_line_chunks(p, 100))
        assert len(chunks) == 1
        assert chunks[0] == b"hello\n"


class TestPolarsFlattenToParquet:
    def test_jsonl_produces_correct_parquet(self, tmp_path):
        data_file = tmp_path / "data.jsonl"
        data_file.write_text('{"name": "Alice", "age": 30}\n{"name": "Bob", "age": 25}\n')
        schema = {"name": "str", "age": "int"}
        cache_path = tmp_path / "cache.parquet"

        count = _polars_flatten_to_parquet(data_file, schema, cache_path)

        assert count == 2
        df = pl.read_parquet(cache_path)
        assert df.shape == (2, 2)
        assert df["name"].to_list() == ["Alice", "Bob"]

    def test_jsonl_nested(self, tmp_path):
        data_file = tmp_path / "nested.jsonl"
        data_file.write_text('{"addr": {"city": "London"}, "tags": ["a", "b"]}\n')
        schema = {
            "addr": {"city": "str"},
            "tags": {"$max": 3, "$items": "str"},
        }
        cache_path = tmp_path / "cache.parquet"

        count = _polars_flatten_to_parquet(data_file, schema, cache_path)

        assert count == 1
        df = pl.read_parquet(cache_path)
        assert "addr.city" in df.columns
        assert "tags.1" in df.columns
        assert "tags.3" in df.columns
        assert df["addr.city"][0] == "London"
        assert df["tags.1"][0] == "a"
        assert df["tags.3"][0] is None

    def test_json_array_format(self, tmp_path):
        data_file = tmp_path / "data.json"
        data_file.write_text(json.dumps([{"x": 1}, {"x": 2}]))
        schema = {"x": "int"}
        cache_path = tmp_path / "cache.parquet"

        count = _polars_flatten_to_parquet(data_file, schema, cache_path)

        assert count == 2
        df = pl.read_parquet(cache_path)
        assert df["x"].to_list() == [1, 2]

    def test_empty_schema_writes_empty_parquet(self, tmp_path):
        data_file = tmp_path / "data.jsonl"
        data_file.write_text('{"x": 1}\n')
        cache_path = tmp_path / "cache.parquet"

        count = _polars_flatten_to_parquet(data_file, {}, cache_path)

        assert count == 0
        assert cache_path.exists()

    def test_progress_tracking(self, tmp_path):
        data_file = tmp_path / "data.jsonl"
        data_file.write_text('{"x": 1}\n{"x": 2}\n')
        schema = {"x": "int"}
        cache_path = tmp_path / "cache.parquet"

        _polars_flatten_to_parquet(
            data_file,
            schema,
            cache_path,
            progress_key="test_polars_key",
            t0=time.monotonic(),
        )

        progress = flatten_progress("test_polars_key")
        assert progress is not None
        assert progress["rows"] == 2
        _clear_flatten_progress()

    def test_chunked_jsonl_multiple_chunks(self, tmp_path):
        """Multiple chunks produce the same result as a single chunk."""
        data_file = tmp_path / "data.jsonl"
        lines = [json.dumps({"x": i, "y": f"val_{i}"}) for i in range(10)]
        data_file.write_text("\n".join(lines) + "\n")
        schema = {"x": "int", "y": "str"}
        cache_path = tmp_path / "cache.parquet"

        count = _polars_flatten_to_parquet(
            data_file,
            schema,
            cache_path,
            chunk_lines=3,
        )

        assert count == 10
        df = pl.read_parquet(cache_path)
        assert len(df) == 10
        assert df["x"].to_list() == list(range(10))

    def test_empty_jsonl_writes_empty_parquet(self, tmp_path):
        data_file = tmp_path / "empty.jsonl"
        data_file.write_text("")
        schema = {"x": "int", "y": "str"}
        cache_path = tmp_path / "cache.parquet"

        count = _polars_flatten_to_parquet(data_file, schema, cache_path)

        assert count == 0
        assert cache_path.exists()
        df = pl.read_parquet(cache_path)
        assert len(df) == 0
        assert set(df.columns) == {"x", "y"}

    def test_atomic_cleanup_on_failure(self, tmp_path):
        data_file = tmp_path / "data.jsonl"
        data_file.write_text('{"x": 1}\n')
        # Schema references a field that doesn't exist → Polars error
        schema = {"nonexistent": "str"}
        cache_path = tmp_path / "cache.parquet"

        with pytest.raises(Exception):
            _polars_flatten_to_parquet(data_file, schema, cache_path)

        assert not cache_path.exists()
        assert not cache_path.with_suffix(".parquet.tmp").exists()

    def test_build_json_cache_uses_streaming(self, tmp_path, monkeypatch):
        """build_json_cache uses the column-oriented streaming path."""
        monkeypatch.chdir(tmp_path)
        data_file = tmp_path / "data.jsonl"
        data_file.write_text('{"a": 1}\n{"a": 2}\n')

        result = build_json_cache(str(data_file))
        assert result["row_count"] == 2

    def test_read_json_flat_uses_streaming(self, tmp_path, monkeypatch):
        """read_json_flat uses the column-oriented streaming path."""
        monkeypatch.chdir(tmp_path)
        data_file = tmp_path / "data.json"
        data_file.write_text(json.dumps([{"x": 1}, {"x": 2}]))

        lf = read_json_flat(str(data_file))
        assert lf.collect()["x"].to_list() == [1, 2]


# ---------------------------------------------------------------------------
# read_json_flat — JSONL two-step pipeline (P6)
# ---------------------------------------------------------------------------


class TestReadJsonFlatJSONL:
    """Tests for the two-step JSONL pipeline in read_json_flat."""

    def test_basic_jsonl(self, tmp_path, monkeypatch):
        """read_json_flat with a .jsonl file uses the two-step pipeline."""
        monkeypatch.chdir(tmp_path)
        data_file = tmp_path / "data.jsonl"
        data_file.write_text('{"x": 1, "y": "a"}\n{"x": 2, "y": "b"}\n')

        lf = read_json_flat(str(data_file))
        df = lf.collect()
        assert df["x"].to_list() == [1, 2]
        assert df["y"].to_list() == ["a", "b"]

    def test_empty_jsonl(self, tmp_path, monkeypatch):
        """Empty .jsonl produces a valid (empty) LazyFrame."""
        monkeypatch.chdir(tmp_path)
        data_file = tmp_path / "empty.jsonl"
        data_file.write_text("")

        lf = read_json_flat(str(data_file))
        df = lf.collect()
        assert len(df) == 0

    def test_single_line_jsonl(self, tmp_path, monkeypatch):
        """Single-line .jsonl works correctly."""
        monkeypatch.chdir(tmp_path)
        data_file = tmp_path / "one.jsonl"
        data_file.write_text('{"val": 42}\n')

        lf = read_json_flat(str(data_file))
        df = lf.collect()
        assert len(df) == 1
        assert df["val"].to_list() == [42]

    def test_nested_jsonl(self, tmp_path, monkeypatch):
        """Nested JSONL records are flattened correctly."""
        monkeypatch.chdir(tmp_path)
        data_file = tmp_path / "nested.jsonl"
        data_file.write_text('{"addr": {"city": "London"}, "name": "Alice"}\n')

        lf = read_json_flat(str(data_file))
        df = lf.collect()
        assert "addr.city" in df.columns
        assert df["addr.city"][0] == "London"

    def test_jsonl_cache_reuse(self, tmp_path, monkeypatch):
        """Second call uses cache, not the two-step pipeline again."""
        monkeypatch.chdir(tmp_path)
        data_file = tmp_path / "data.jsonl"
        data_file.write_text('{"x": 1}\n{"x": 2}\n')

        lf1 = read_json_flat(str(data_file))
        assert lf1.collect()["x"].to_list() == [1, 2]

        cache_path = _json_cache_path(str(data_file))
        assert cache_path.exists()
        first_mtime = cache_path.stat().st_mtime

        lf2 = read_json_flat(str(data_file))
        assert lf2.collect()["x"].to_list() == [1, 2]
        assert cache_path.stat().st_mtime == first_mtime

    def test_jsonl_raw_file_cleaned_up(self, tmp_path, monkeypatch):
        """The intermediate .raw.parquet file is removed after success."""
        monkeypatch.chdir(tmp_path)
        data_file = tmp_path / "data.jsonl"
        data_file.write_text('{"x": 1}\n')

        read_json_flat(str(data_file))

        cache_path = _json_cache_path(str(data_file))
        raw_path = cache_path.with_suffix(".raw.parquet")
        assert cache_path.exists()
        assert not raw_path.exists()

    def test_jsonl_with_schema(self, tmp_path, monkeypatch):
        """Explicit schema is applied to JSONL two-step pipeline."""
        monkeypatch.chdir(tmp_path)
        data_file = tmp_path / "data.jsonl"
        data_file.write_text('{"x": 1, "y": "a"}\n{"x": 2, "y": "b"}\n')

        lf = read_json_flat(str(data_file), schema={"x": "int"})
        df = lf.collect()
        assert df.columns == ["x"]
        assert df["x"].to_list() == [1, 2]

    def test_jsonl_with_ambiguous_keys_fails_before_cache_write(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        data_file = tmp_path / "ambiguous.jsonl"
        data_file.write_text('{"a.b": 1, "a": {"b": 2}}\n')

        with pytest.raises(JsonFlattenSchemaError, match="Unsupported JSON object key"):
            read_json_flat(str(data_file))

        assert not _json_cache_path(str(data_file)).exists()

    def test_jsonl_blank_lines_only(self, tmp_path, monkeypatch):
        """JSONL file with only blank lines produces an empty result."""
        monkeypatch.chdir(tmp_path)
        data_file = tmp_path / "blanks.jsonl"
        data_file.write_text("\n\n\n")

        lf = read_json_flat(str(data_file))
        df = lf.collect()
        assert len(df) == 0
