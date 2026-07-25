"""AUD-C06: factor dtypes are part of the rating-key contract."""

from __future__ import annotations

from typing import Any

import polars as pl
import pytest

from haute._builders import _apply_ratebook
from haute._rating import (
    _apply_rating_table,
    _rating_key_expr,
    normalise_rating_key,
    rating_dtype_descriptor,
)
from haute._trace_enrichment import _enrich_single_table
from haute.errors import RatingFactorDtypeContractError
from haute.routes._optimiser_service import (
    _ratebook_factor_dtypes,
    _ratebook_factor_level_counts,
)
from tests.fixtures.rating_key_cases import RATING_KEY_CASES, RatingKeyCase


@pytest.mark.parametrize("case", RATING_KEY_CASES, ids=lambda case: case.name)
def test_python_key_uses_the_exact_engine_expression(case: RatingKeyCase) -> None:
    series = pl.Series("factor", [case.value], dtype=case.dtype)
    engine_key = series.to_frame().select(_rating_key_expr("factor", case.dtype)).item()

    assert normalise_rating_key(series.item(), case.dtype) == engine_key
    assert rating_dtype_descriptor(case.dtype) == case.descriptor


@pytest.mark.parametrize("case", RATING_KEY_CASES, ids=lambda case: case.name)
def test_lookup_coerces_entries_through_input_dtype_and_preserves_source(
    case: RatingKeyCase,
) -> None:
    source = pl.DataFrame(
        {
            "factor": pl.Series("factor", [case.value], dtype=case.dtype),
            "__haute_rating_key_0__": ["user-data"],
            "__haute_lookup_val__": [99.0],
        }
    )
    table: dict[str, Any] = {
        "name": case.name,
        "factors": ["factor"],
        "outputColumn": "rate",
        "entries": [{"factor": case.entry_value, "value": 2.0}],
        "onMissing": "neutral",
    }

    result = _apply_rating_table(source.lazy(), table).collect()

    assert result.schema["factor"] == case.dtype
    assert result["factor"].to_list() == source["factor"].to_list()
    assert result["__haute_rating_key_0__"].to_list() == ["user-data"]
    assert result["__haute_lookup_val__"].to_list() == [99.0]
    assert result["rate"].to_list() == ([2.0] if case.matches else [None])


@pytest.mark.parametrize("case", RATING_KEY_CASES, ids=lambda case: case.name)
def test_trace_uses_the_same_originating_dtype(case: RatingKeyCase) -> None:
    typed_value = pl.Series("factor", [case.value], dtype=case.dtype).item()
    table = {
        "name": case.name,
        "factors": ["factor"],
        "outputColumn": "rate",
        "entries": [{"factor": case.entry_value, "value": 2.0}],
    }

    detail = _enrich_single_table(
        table,
        {"factor": typed_value},
        {"rate": 2.0 if case.matches else None},
        factor_input_dtypes={"factor": case.dtype},
    )

    assert detail["status"] == ("matched" if case.matches else "no_match")


@pytest.mark.parametrize("case", RATING_KEY_CASES, ids=lambda case: case.name)
def test_ratebook_apply_reuses_the_same_dtype_contract(case: RatingKeyCase) -> None:
    source = pl.DataFrame({"factor": pl.Series("factor", [case.value], dtype=case.dtype)})
    artifact = {
        "mode": "ratebook",
        "factor_tables": {
            "factor": [
                {
                    "__factor_group__": normalise_rating_key(case.value, case.dtype),
                    "optimal_scenario_value": 2.0,
                }
            ]
        },
        "factor_dtypes": {"factor": [{"column": "factor", "dtype": case.descriptor}]},
    }

    result = _apply_ratebook(
        source.lazy(),
        artifact,
        "v1",
        "__version__",
    ).collect()

    assert result["optimised_factor"].to_list() == ([2.0] if case.matches else [1.0])


def test_ratebook_rejects_missing_dtype_metadata_before_lookup() -> None:
    artifact = {
        "mode": "ratebook",
        "factor_tables": {"factor": [{"__factor_group__": "0.1", "optimal_scenario_value": 2.0}]},
    }

    with pytest.raises(RatingFactorDtypeContractError, match="factor_dtypes"):
        _apply_ratebook(
            pl.DataFrame({"factor": pl.Series("factor", [0.1], dtype=pl.Float32)}).lazy(),
            artifact,
            "v1",
            "__version__",
        )


def test_ratebook_rejects_missing_dtype_metadata_for_empty_table() -> None:
    artifact = {
        "mode": "ratebook",
        "factor_tables": {"factor": []},
    }

    with pytest.raises(RatingFactorDtypeContractError, match="factor_dtypes"):
        _apply_ratebook(
            pl.DataFrame({"factor": ["known"]}).lazy(),
            artifact,
            "v1",
            "__version__",
        )


def test_ratebook_rejects_dtype_record_with_extra_fields() -> None:
    artifact = {
        "mode": "ratebook",
        "factor_tables": {"factor": []},
        "factor_dtypes": {
            "factor": [
                {
                    "column": "factor",
                    "dtype": {"kind": "String"},
                    "unexpected": True,
                }
            ]
        },
    }

    with pytest.raises(RatingFactorDtypeContractError, match="malformed"):
        _apply_ratebook(
            pl.DataFrame({"factor": ["known"]}).lazy(),
            artifact,
            "v1",
            "__version__",
        )


def test_ratebook_rejects_exact_dtype_drift_before_neutral_miss() -> None:
    artifact = {
        "mode": "ratebook",
        "factor_tables": {"factor": [{"__factor_group__": "0.1", "optimal_scenario_value": 2.0}]},
        "factor_dtypes": {"factor": [{"column": "factor", "dtype": {"kind": "Float64"}}]},
    }

    with pytest.raises(RatingFactorDtypeContractError) as exc_info:
        _apply_ratebook(
            pl.DataFrame({"factor": pl.Series("factor", [0.1], dtype=pl.Float32)}).lazy(),
            artifact,
            "v1",
            "__version__",
        )

    assert exc_info.value.saved_dtype == {"kind": "Float64"}
    assert exc_info.value.input_dtype == {"kind": "Float32"}


def test_ratebook_rejects_unsupported_apply_dtype_as_typed_contract_error() -> None:
    artifact = {
        "mode": "ratebook",
        "factor_tables": {"factor": []},
        "factor_dtypes": {"factor": [{"column": "factor", "dtype": {"kind": "String"}}]},
    }

    with pytest.raises(RatingFactorDtypeContractError) as exc_info:
        _apply_ratebook(
            pl.DataFrame({"factor": pl.Series("factor", [b"x"], dtype=pl.Binary)}).lazy(),
            artifact,
            "v1",
            "__version__",
        )

    assert exc_info.value.saved_dtype == {"kind": "String"}
    assert exc_info.value.input_dtype is None


def test_ratebook_save_metadata_and_level_keys_reuse_shared_matrix() -> None:
    supported_cases = [case for case in RATING_KEY_CASES if case.value is not None]
    factors = pl.DataFrame(
        {
            case.name: pl.Series(case.name, [case.value], dtype=case.dtype)
            for case in supported_cases
        }
    )
    factor_columns = [[case.name] for case in supported_cases]

    descriptors = _ratebook_factor_dtypes(factors, factor_columns)
    counts = _ratebook_factor_level_counts(factors, factor_columns)

    assert descriptors == {
        case.name: [{"column": case.name, "dtype": case.descriptor}] for case in supported_cases
    }
    assert counts == {
        case.name: {normalise_rating_key(case.value, case.dtype): 1} for case in supported_cases
    }


@pytest.mark.parametrize(
    ("dtype", "value"),
    [
        (pl.Float32, float("nan")),
        (pl.Float32, float("inf")),
        (pl.Float32, float("-inf")),
        (pl.Float64, float("nan")),
        (pl.Float64, float("inf")),
        (pl.Float64, float("-inf")),
    ],
)
def test_non_finite_python_and_engine_keys_agree(dtype: pl.DataType, value: float) -> None:
    series = pl.Series("factor", [value], dtype=dtype)
    engine_key = series.to_frame().select(_rating_key_expr("factor", dtype)).item()
    assert normalise_rating_key(series.item(), dtype) == engine_key


@pytest.mark.parametrize("dtype", [pl.Binary, pl.List(pl.Int64), pl.Struct({"x": pl.Int64})])
def test_unsupported_factor_dtype_fails_before_lookup(dtype: pl.DataType) -> None:
    value: Any
    if dtype == pl.Binary:
        value = b"x"
    elif isinstance(dtype, pl.List):
        value = [1]
    else:
        value = {"x": 1}
    frame = pl.DataFrame({"factor": pl.Series("factor", [value], dtype=dtype)})
    table = {
        "name": "unsupported",
        "factors": ["factor"],
        "outputColumn": "rate",
        "entries": [{"factor": value, "value": 2.0}],
    }

    with pytest.raises(ValueError, match=r"unsupported.*factor.*dtype"):
        _apply_rating_table(frame.lazy(), table)
