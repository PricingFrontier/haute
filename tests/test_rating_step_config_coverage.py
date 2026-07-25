"""Value-integrity and sidecar round-trip tests for ``_rating_step_config``."""

from __future__ import annotations

import json
import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from haute._rating_step_config import (
    _compact_entry_rows,
    _compact_table_for_sidecar,
    _entry_value,
    _expand_entries_map,
    _insert_entry_value,
    _validate_factors,
    _validate_rating_value,
    compact_rating_step_config_for_sidecar,
    expand_rating_step_config_from_sidecar,
    normalise_rating_tables,
)
from tests.fixtures.rating_key_cases import RATING_KEY_CASES, RatingKeyCase

_FACTOR_SCALARS = st.one_of(
    st.booleans(),
    st.integers(min_value=-(2**53), max_value=2**53),
    st.floats(allow_nan=False, allow_infinity=False, allow_subnormal=False),
    st.text(max_size=24),
)
_RATING_VALUES = st.one_of(
    st.integers(min_value=-1_000_000, max_value=1_000_000),
    st.floats(
        min_value=-1_000_000,
        max_value=1_000_000,
        allow_nan=False,
        allow_infinity=False,
        allow_subnormal=False,
    ),
    st.integers(min_value=-1_000_000, max_value=1_000_000).map(str),
)
_JSON_VALUES = st.recursive(
    st.one_of(
        st.none(),
        st.booleans(),
        st.integers(min_value=-(2**53), max_value=2**53),
        st.floats(allow_nan=False, allow_infinity=False, allow_subnormal=False),
        st.text(max_size=24),
    ),
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.dictionaries(st.text(max_size=12), children, max_size=4),
    ),
    max_leaves=12,
)
_METADATA_KEYS = st.text(min_size=1, max_size=12).filter(
    lambda key: key not in {"value", "rating", "factor_0", "factor_1", "factor_2"}
)

# ---------------------------------------------------------------------------
# _validate_rating_value
# ---------------------------------------------------------------------------


def test_validate_rating_value_rejects_none_and_empty() -> None:
    with pytest.raises(ValueError, match="requires value"):
        _validate_rating_value(None, "ctx")
    with pytest.raises(ValueError, match="requires value"):
        _validate_rating_value("", "ctx")


def test_validate_rating_value_rejects_bool() -> None:
    with pytest.raises(ValueError, match="must be numeric"):
        _validate_rating_value(True, "ctx")
    with pytest.raises(ValueError, match="must be numeric"):
        _validate_rating_value(False, "ctx")


def test_validate_rating_value_rejects_non_finite_numeric() -> None:
    with pytest.raises(ValueError, match="must be finite"):
        _validate_rating_value(math.nan, "ctx")
    with pytest.raises(ValueError, match="must be finite"):
        _validate_rating_value(math.inf, "ctx")


def test_validate_rating_value_accepts_finite_numeric() -> None:
    assert _validate_rating_value(1.5, "ctx") is None
    assert _validate_rating_value(3, "ctx") is None


def test_validate_rating_value_rejects_whitespace_string() -> None:
    with pytest.raises(ValueError, match="requires value"):
        _validate_rating_value("   ", "ctx")


def test_validate_rating_value_rejects_non_numeric_string() -> None:
    with pytest.raises(ValueError, match="must be numeric"):
        _validate_rating_value("abc", "ctx")


def test_validate_rating_value_rejects_non_finite_numeric_string() -> None:
    with pytest.raises(ValueError, match="must be finite"):
        _validate_rating_value("nan", "ctx")
    with pytest.raises(ValueError, match="must be finite"):
        _validate_rating_value("inf", "ctx")


def test_validate_rating_value_coerces_numeric_string() -> None:
    # Numeric strings are accepted (coerced for the finite check) without raising.
    assert _validate_rating_value("2.25", "ctx") is None


def test_validate_rating_value_rejects_other_types() -> None:
    with pytest.raises(ValueError, match="must be a JSON string or number"):
        _validate_rating_value([1, 2], "ctx")
    with pytest.raises(ValueError, match="must be a JSON string or number"):
        _validate_rating_value({"a": 1}, "ctx")


# ---------------------------------------------------------------------------
# _validate_factors factor-cap guard
# ---------------------------------------------------------------------------


def test_validate_factors_rejects_more_than_three() -> None:
    table = {"factors": ["a", "b", "c", "d"]}
    with pytest.raises(ValueError, match="supports at most 3 columns"):
        _validate_factors(table, 0)


def test_validate_factors_accepts_three() -> None:
    table = {"factors": ["a", "b", "c"]}
    assert _validate_factors(table, 0) == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# _insert_entry_value dict-conflict / duplicate-leaf
# ---------------------------------------------------------------------------


def test_insert_entry_value_dict_conflict() -> None:
    target: dict = {"x": 1.0}
    with pytest.raises(ValueError, match="conflicts with an existing rating value"):
        _insert_entry_value(target, ["x", "y"], 2.0, "ctx")


def test_insert_entry_value_duplicate_leaf() -> None:
    target: dict = {}
    _insert_entry_value(target, ["x"], 1.0, "ctx")
    with pytest.raises(ValueError, match="duplicate ctx key"):
        _insert_entry_value(target, ["x"], 2.0, "ctx")


def test_insert_entry_value_nested_path() -> None:
    target: dict = {}
    _insert_entry_value(target, ["a", "b"], 1.0, "ctx")
    _insert_entry_value(target, ["a", "c"], 2.0, "ctx")
    assert target == {"a": {"b": 1.0, "c": 2.0}}


# ---------------------------------------------------------------------------
# _entry_value value-vs-outputColumn conflict
# ---------------------------------------------------------------------------


def test_entry_value_conflict_between_value_and_output_column() -> None:
    row = {"value": 1.0, "premium": 2.0}
    with pytest.raises(ValueError, match="contains both 'value' and 'premium'"):
        _entry_value(row, "premium", "ctx")


def test_entry_value_prefers_value() -> None:
    row = {"value": 1.0}
    assert _entry_value(row, "premium", "ctx") == 1.0


def test_entry_value_falls_back_to_output_column() -> None:
    row = {"premium": 2.0}
    assert _entry_value(row, "premium", "ctx") == 2.0


def test_entry_value_requires_value() -> None:
    with pytest.raises(ValueError, match="requires value"):
        _entry_value({"factor": "x"}, "premium", "ctx")


def test_entry_value_matching_value_and_output_column_no_conflict() -> None:
    row = {"value": 3.0, "premium": 3.0}
    assert _entry_value(row, "premium", "ctx") == 3.0


# ---------------------------------------------------------------------------
# _expand_entries_map duplicate-key / depth-mismatch guards
# ---------------------------------------------------------------------------


def test_expand_entries_map_empty_factors_with_entries_raises() -> None:
    with pytest.raises(ValueError, match="factors must be a non-empty list"):
        _expand_entries_map({"a": 1.0}, [], 0)


def test_expand_entries_map_empty_factors_empty_entries_ok() -> None:
    assert _expand_entries_map({}, [], 0) == []


def test_expand_entries_map_leaf_dict_depth_mismatch() -> None:
    # Single factor expects a scalar at depth 1; a dict at the leaf is too deep.
    with pytest.raises(ValueError, match="must have rating values at depth 1"):
        _expand_entries_map({"a": {"b": 1.0}}, ["f0"], 0)


def test_expand_entries_map_leaf_list_rejected() -> None:
    with pytest.raises(ValueError, match="rating values must be scalar"):
        _expand_entries_map({"a": [1.0, 2.0]}, ["f0"], 0)


def test_expand_entries_map_non_dict_branch_when_nesting_expected() -> None:
    # Two factors expect nesting; a scalar where a dict is required is too shallow.
    with pytest.raises(ValueError, match="must be nested to match 2 factors"):
        _expand_entries_map({"a": 1.0}, ["f0", "f1"], 0)


def test_expand_entries_map_single_factor_roundtrip() -> None:
    rows = _expand_entries_map({"low": 1.0, "high": 2.0}, ["band"], 0)
    assert rows == [
        {"band": "high", "value": 2.0},
        {"band": "low", "value": 1.0},
    ]


# ---------------------------------------------------------------------------
# _compact_entry_rows + sidecar dispatch
# ---------------------------------------------------------------------------


def test_compact_entry_rows_empty_factors_with_rows_preserves_draft_rows() -> None:
    assert _compact_entry_rows([{"value": 1.0}], [], 0, "") == [{"value": 1.0}]


def test_compact_entry_rows_empty_factors_no_rows_ok() -> None:
    assert _compact_entry_rows([], [], 0, "") == []


def test_compact_table_for_sidecar_no_entries_passthrough() -> None:
    table = {"factors": ["a"]}
    assert _compact_table_for_sidecar(table, 0) == {"factors": ["a"]}


def test_compact_table_for_sidecar_none_entries_raises() -> None:
    table = {"factors": ["a"], "entries": None}
    with pytest.raises(ValueError, match="must be a list or object"):
        _compact_table_for_sidecar(table, 0)


def test_compact_table_for_sidecar_empty_entries_with_factors_stays_list() -> None:
    table = {"factors": ["a"], "entries": []}
    result = _compact_table_for_sidecar(table, 0)
    assert result["entries"] == []


def test_compact_table_for_sidecar_empty_entries_without_factors_stays_list() -> None:
    table = {"factors": [], "entries": []}
    result = _compact_table_for_sidecar(table, 0)
    assert result["entries"] == []


def test_compact_table_for_sidecar_dict_entries_dispatch() -> None:
    # Legacy maps are migrated to canonical rows on their next write.
    table = {"factors": ["band"], "entries": {"low": 1.0, "high": 2.0}}
    result = _compact_table_for_sidecar(table, 0)
    assert result["entries"] == [
        {"band": "high", "value": 2.0},
        {"band": "low", "value": 1.0},
    ]


def test_compact_table_for_sidecar_list_entries_dispatch() -> None:
    table = {
        "factors": ["band"],
        "entries": [{"band": "low", "value": 1.0}, {"band": "high", "value": 2.0}],
    }
    result = _compact_table_for_sidecar(table, 0)
    assert result["entries"] == table["entries"]


def test_compact_table_for_sidecar_invalid_entries_type_raises() -> None:
    table = {"factors": ["band"], "entries": 42}
    with pytest.raises(ValueError, match="must be a list or object"):
        _compact_table_for_sidecar(table, 0)


# ---------------------------------------------------------------------------
# Clean table round-trips value <-> factor-tuple
# ---------------------------------------------------------------------------


def test_compact_expand_roundtrip_single_factor() -> None:
    canonical = {
        "tables": [
            {
                "factors": ["band"],
                "entries": [
                    {"band": "low", "value": 1.0},
                    {"band": "high", "value": 2.0},
                ],
            }
        ]
    }
    compact = compact_rating_step_config_for_sidecar(canonical)
    assert compact["tables"][0]["entries"] == canonical["tables"][0]["entries"]

    expanded = expand_rating_step_config_from_sidecar(compact)
    assert expanded["tables"][0]["entries"] == [
        {"band": "low", "value": 1.0},
        {"band": "high", "value": 2.0},
    ]


def test_compact_expand_roundtrip_three_factors() -> None:
    canonical = {
        "tables": [
            {
                "factors": ["a", "b", "c"],
                "entries": [
                    {"a": "1", "b": "2", "c": "3", "value": 1.5},
                ],
            }
        ]
    }
    compact = compact_rating_step_config_for_sidecar(canonical)
    assert compact["tables"][0]["entries"] == canonical["tables"][0]["entries"]

    expanded = expand_rating_step_config_from_sidecar(compact)
    assert expanded["tables"][0]["entries"] == [
        {"a": "1", "b": "2", "c": "3", "value": 1.5},
    ]


# ---------------------------------------------------------------------------
# Public-API table-shape guards
# ---------------------------------------------------------------------------


def test_expand_no_tables_passthrough() -> None:
    assert expand_rating_step_config_from_sidecar({"foo": 1}) == {"foo": 1}


def test_expand_tables_not_a_list_raises() -> None:
    with pytest.raises(ValueError, match="tables must be a list"):
        expand_rating_step_config_from_sidecar({"tables": {}})


def test_expand_table_not_object_raises() -> None:
    with pytest.raises(ValueError, match=r"tables\[0\] must be an object"):
        expand_rating_step_config_from_sidecar({"tables": [42]})


def test_compact_no_tables_passthrough() -> None:
    assert compact_rating_step_config_for_sidecar({"foo": 1}) == {"foo": 1}


def test_compact_tables_not_a_list_raises() -> None:
    with pytest.raises(ValueError, match="tables must be a list"):
        compact_rating_step_config_for_sidecar({"tables": {}})


def test_compact_table_not_object_raises() -> None:
    with pytest.raises(ValueError, match=r"tables\[0\] must be an object"):
        compact_rating_step_config_for_sidecar({"tables": [42]})


def test_normalise_rating_tables_none_returns_empty() -> None:
    assert normalise_rating_tables({}) == []


def test_normalise_rating_tables_not_a_list_raises() -> None:
    with pytest.raises(ValueError, match="tables must be a list"):
        normalise_rating_tables({"tables": {}})


def test_normalise_rating_tables_expands_compact_map() -> None:
    config = {"tables": [{"factors": ["band"], "entries": {"low": 1.0}}]}
    tables = normalise_rating_tables(config)
    assert tables == [{"factors": ["band"], "entries": [{"band": "low", "value": 1.0}]}]


# ---------------------------------------------------------------------------
# Symmetric compact<->expand key normalisation (write/read agreement)
# ---------------------------------------------------------------------------


def test_compact_distinct_numeric_looking_string_labels_losslessly() -> None:
    """Canonical rows preserve labels a JSON object-key map would conflate."""
    config = {
        "tables": [
            {
                "factors": ["age"],
                "outputColumn": "f",
                "entries": [
                    {"age": "25", "value": 1.0},
                    {"age": "25.0", "value": 2.0},
                ],
            }
        ]
    }
    assert compact_rating_step_config_for_sidecar(config) == config


def test_string_label_spelling_int_like_float_roundtrips_unchanged() -> None:
    """A numeric-looking String label keeps its spelling and scalar type."""
    config = {
        "tables": [
            {
                "factors": ["age"],
                "outputColumn": "f",
                "entries": [{"age": "25.0", "value": 2.0}],
            }
        ]
    }
    compact = compact_rating_step_config_for_sidecar(config)
    assert compact["tables"][0]["entries"] == [{"age": "25.0", "value": 2.0}]

    expanded = expand_rating_step_config_from_sidecar(compact)
    assert expanded["tables"][0]["entries"] == [{"age": "25.0", "value": 2.0}]

    # Round trip is idempotent and does not canonicalise labels.
    recompacted = compact_rating_step_config_for_sidecar({"tables": expanded["tables"]})
    assert recompacted["tables"][0]["entries"] == [{"age": "25.0", "value": 2.0}]


def test_canonical_rows_preserve_scalar_identity_order_and_metadata() -> None:
    entries = [
        {"age": 25.0, "value": 1.0, "note": {"source": "first"}},
        {"age": "25.0", "value": 2.0, "note": {"source": "last"}},
    ]
    config = {
        "tables": [
            {
                "factors": ["age"],
                "factorDtypes": {"age": {"kind": "Float64"}},
                "outputColumn": "factor",
                "defaultValue": 1.0,
                "onMissing": "neutral",
                "entries": entries,
            }
        ]
    }
    compact = compact_rating_step_config_for_sidecar(config)
    assert compact == config
    assert compact is not config
    assert compact["tables"][0]["entries"] is not entries


@settings(max_examples=100, deadline=None)
@given(factor_count=st.integers(min_value=1, max_value=3), data=st.data())
def test_canonical_row_sidecars_are_lossless_json_roundtrips(
    factor_count: int,
    data: st.DataObject,
) -> None:
    factors = [f"factor_{index}" for index in range(factor_count)]
    entries: list[dict[str, object]] = []
    for row_index in range(data.draw(st.integers(min_value=0, max_value=8), label="row_count")):
        metadata = data.draw(
            st.dictionaries(_METADATA_KEYS, _JSON_VALUES, max_size=4),
            label=f"metadata_{row_index}",
        )
        row = {
            **metadata,
            **{
                factor: data.draw(_FACTOR_SCALARS, label=f"{factor}_{row_index}")
                for factor in factors
            },
            "value": data.draw(_RATING_VALUES, label=f"value_{row_index}"),
        }
        entries.append(row)

    config = {
        "tables": [
            {
                "name": "property_table",
                "factors": factors,
                "factorDtypes": {factor: {"kind": "String"} for factor in factors},
                "outputColumn": "rating",
                "defaultValue": "1.0",
                "onMissing": "neutral",
                "entries": entries,
            }
        ]
    }

    compacted = compact_rating_step_config_for_sidecar(config)
    encoded = json.dumps(compacted, allow_nan=False)
    roundtripped = expand_rating_step_config_from_sidecar(json.loads(encoded))

    assert compacted == config
    assert compacted is not config
    assert roundtripped == config
    for original, restored in zip(entries, roundtripped["tables"][0]["entries"], strict=True):
        for factor in factors:
            assert type(restored[factor]) is type(original[factor])


@pytest.mark.parametrize(
    "factor_dtypes",
    [
        [],
        {"stale": {"kind": "String"}},
        {"age": {"kind": "Float32", "unexpected": True}},
        {"age": {"kind": "Unknown"}},
    ],
)
def test_factor_dtype_metadata_is_validated(factor_dtypes: object) -> None:
    with pytest.raises(ValueError, match="factorDtypes"):
        compact_rating_step_config_for_sidecar(
            {
                "tables": [
                    {
                        "factors": ["age"],
                        "factorDtypes": factor_dtypes,
                        "entries": [{"age": 25.0, "value": 1.0}],
                    }
                ]
            }
        )


@pytest.mark.parametrize("case", RATING_KEY_CASES, ids=lambda case: case.name)
def test_factor_dtype_metadata_preserves_every_supported_descriptor(
    case: RatingKeyCase,
) -> None:
    config = {
        "tables": [
            {
                "factors": ["factor"],
                "factorDtypes": {"factor": case.descriptor},
                "entries": [],
            }
        ]
    }

    assert compact_rating_step_config_for_sidecar(config) == config


def test_duplicate_factor_columns_are_rejected_as_ambiguous() -> None:
    with pytest.raises(ValueError, match=r"duplicate column 'age'"):
        compact_rating_step_config_for_sidecar(
            {
                "tables": [
                    {
                        "factors": ["age", "age"],
                        "entries": [{"age": 25, "value": 1.0}],
                    }
                ]
            }
        )


def test_legacy_maps_are_traversed_in_deterministic_key_order() -> None:
    first = expand_rating_step_config_from_sidecar(
        {"tables": [{"factors": ["a", "b"], "entries": {"z": {"b": 1.0}, "a": {"c": 2.0}}}]}
    )
    second = expand_rating_step_config_from_sidecar(
        {"tables": [{"factors": ["a", "b"], "entries": {"a": {"c": 2.0}, "z": {"b": 1.0}}}]}
    )
    assert first == second
    assert first["tables"][0]["entries"] == [
        {"a": "a", "b": "c", "value": 2.0},
        {"a": "z", "b": "b", "value": 1.0},
    ]


@pytest.mark.parametrize("value", [None, [], {}, math.nan])
def test_canonical_rows_reject_invalid_factor_scalars(value: object) -> None:
    with pytest.raises(ValueError, match=r"entries\[0\] factor 'age'"):
        compact_rating_step_config_for_sidecar(
            {"tables": [{"factors": ["age"], "entries": [{"age": value, "value": 1.0}]}]}
        )


# ---------------------------------------------------------------------------
# Empty-entries tables still validate factor structure (fail loud)
# ---------------------------------------------------------------------------


def test_expand_empty_entries_with_invalid_factors_raises() -> None:
    """F305: an empty-entries table must not skip factor validation."""
    config = {"tables": [{"factors": [123], "outputColumn": "f", "entries": []}]}
    with pytest.raises(ValueError, match="must be a column name"):
        expand_rating_step_config_from_sidecar(config)


def test_compact_empty_entries_with_invalid_factors_raises() -> None:
    table = {"factors": [123], "outputColumn": "f", "entries": []}
    with pytest.raises(ValueError, match="must be a column name"):
        _compact_table_for_sidecar(table, 0)


def test_expand_empty_entries_missing_factors_key_raises() -> None:
    config = {"tables": [{"outputColumn": "f", "entries": []}]}
    with pytest.raises(ValueError, match="factors must be a list"):
        expand_rating_step_config_from_sidecar(config)


def test_table_without_entries_still_rejects_malformed_factors() -> None:
    config = {"tables": [{"factors": "age", "outputColumn": "f"}]}
    with pytest.raises(ValueError, match="factors must be a list"):
        compact_rating_step_config_for_sidecar(config)
