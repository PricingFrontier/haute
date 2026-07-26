"""Value-integrity and sidecar round-trip tests for ``_rating_step_config``."""

from __future__ import annotations

import json
import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from haute._rating_step_config import (
    _validate_factors,
    _validate_rating_value,
    normalise_rating_step_config,
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
# Canonical row-array round trips
# ---------------------------------------------------------------------------


def test_normalise_roundtrip_single_factor() -> None:
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
    normalised = normalise_rating_step_config(canonical)
    assert normalised["tables"][0]["entries"] == canonical["tables"][0]["entries"]

    repeated = normalise_rating_step_config(normalised)
    assert repeated["tables"][0]["entries"] == [
        {"band": "low", "value": 1.0},
        {"band": "high", "value": 2.0},
    ]


def test_normalise_roundtrip_three_factors() -> None:
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
    normalised = normalise_rating_step_config(canonical)
    assert normalised["tables"][0]["entries"] == canonical["tables"][0]["entries"]

    repeated = normalise_rating_step_config(normalised)
    assert repeated["tables"][0]["entries"] == [
        {"a": "1", "b": "2", "c": "3", "value": 1.5},
    ]


# ---------------------------------------------------------------------------
# Public-API table-shape guards
# ---------------------------------------------------------------------------


def test_normalise_no_tables_passthrough() -> None:
    assert normalise_rating_step_config({"foo": 1}) == {"foo": 1}


def test_normalise_tables_not_a_list_raises() -> None:
    with pytest.raises(ValueError, match="tables must be a list"):
        normalise_rating_step_config({"tables": {}})


def test_normalise_table_not_object_raises() -> None:
    with pytest.raises(ValueError, match=r"tables\[0\] must be an object"):
        normalise_rating_step_config({"tables": [42]})


def test_normalise_rating_tables_none_returns_empty() -> None:
    assert normalise_rating_tables({}) == []


def test_normalise_rating_tables_not_a_list_raises() -> None:
    with pytest.raises(ValueError, match="tables must be a list"):
        normalise_rating_tables({"tables": {}})


# ---------------------------------------------------------------------------
# Canonical row preservation
# ---------------------------------------------------------------------------


def test_normalise_preserves_numeric_looking_string_labels_losslessly() -> None:
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
    assert normalise_rating_step_config(config) == config


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
    normalised = normalise_rating_step_config(config)
    assert normalised["tables"][0]["entries"] == [{"age": "25.0", "value": 2.0}]

    repeated = normalise_rating_step_config(normalised)
    assert repeated["tables"][0]["entries"] == [{"age": "25.0", "value": 2.0}]

    # Round trip is idempotent and does not canonicalise labels.
    roundtripped = normalise_rating_step_config({"tables": repeated["tables"]})
    assert roundtripped["tables"][0]["entries"] == [{"age": "25.0", "value": 2.0}]


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
    normalised = normalise_rating_step_config(config)
    assert normalised == config
    assert normalised is not config
    assert normalised["tables"][0]["entries"] is not entries


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

    normalised = normalise_rating_step_config(config)
    encoded = json.dumps(normalised, allow_nan=False)
    roundtripped = normalise_rating_step_config(json.loads(encoded))

    assert normalised == config
    assert normalised is not config
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
        normalise_rating_step_config(
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

    assert normalise_rating_step_config(config) == config


def test_duplicate_factor_columns_are_rejected_as_ambiguous() -> None:
    with pytest.raises(ValueError, match=r"duplicate column 'age'"):
        normalise_rating_step_config(
            {
                "tables": [
                    {
                        "factors": ["age", "age"],
                        "entries": [{"age": 25, "value": 1.0}],
                    }
                ]
            }
        )


@pytest.mark.parametrize("value", [None, [], {}, math.nan])
def test_canonical_rows_reject_invalid_factor_scalars(value: object) -> None:
    with pytest.raises(ValueError, match=r"entries\[0\] factor 'age'"):
        normalise_rating_step_config(
            {"tables": [{"factors": ["age"], "entries": [{"age": value, "value": 1.0}]}]}
        )


# ---------------------------------------------------------------------------
# Empty-entries tables still validate factor structure (fail loud)
# ---------------------------------------------------------------------------


def test_normalise_empty_entries_with_invalid_factors_raises() -> None:
    """F305: an empty-entries table must not skip factor validation."""
    config = {"tables": [{"factors": [123], "outputColumn": "f", "entries": []}]}
    with pytest.raises(ValueError, match="must be a column name"):
        normalise_rating_step_config(config)


def test_normalise_empty_entries_missing_factors_key_raises() -> None:
    config = {"tables": [{"outputColumn": "f", "entries": []}]}
    with pytest.raises(ValueError, match="factors must be a list"):
        normalise_rating_step_config(config)


def test_table_without_entries_still_rejects_malformed_factors() -> None:
    config = {"tables": [{"factors": "age", "outputColumn": "f"}]}
    with pytest.raises(ValueError, match="factors must be a list"):
        normalise_rating_step_config(config)
