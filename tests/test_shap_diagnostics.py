"""SHAP sampling, and the summary and beeswarm views built from one SHAP matrix."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest
from pydantic import ValidationError

from haute.modelling._shap import (
    BEESWARM_FEATURES,
    BEESWARM_ROWS,
    SHAP_SAMPLE_ROWS,
    shap_diagnostics,
    shap_sample,
)
from haute.modelling._xgboost import XGBoostAlgorithm
from haute.schemas import TrainShapBeeswarmFeature


def test_sample_caps_the_rows_and_is_deterministic() -> None:
    df = pl.DataFrame({"row": range(5_000)})

    sample = shap_sample(df)

    assert sample.height == SHAP_SAMPLE_ROWS
    assert sample["row"].n_unique() == SHAP_SAMPLE_ROWS
    assert sample.equals(shap_sample(df))


def test_sample_shuffles_a_partition_under_the_cap() -> None:
    df = pl.DataFrame({"row": range(800)})

    sample = shap_sample(df)

    assert sorted(sample["row"].to_list()) == list(range(800))
    # The beeswarm's leading rows spread across the partition, not its first rows.
    assert sample["row"].head(BEESWARM_ROWS).max() > BEESWARM_ROWS


def test_summary_is_largest_first_with_ties_in_feature_order() -> None:
    sample = pl.DataFrame({"a": [1.0, 2.0], "b": [1.0, 2.0], "c": [1.0, 2.0]})
    values = np.array([[0.1, -0.4, 0.4], [-0.1, 0.4, -0.4]])

    summary, _ = shap_diagnostics(values, sample, ["a", "b", "c"], [])

    assert summary == [
        {"feature": "b", "mean_abs_shap": pytest.approx(0.4)},
        {"feature": "c", "mean_abs_shap": pytest.approx(0.4)},
        {"feature": "a", "mean_abs_shap": pytest.approx(0.1)},
    ]


def test_beeswarm_keeps_the_summary_top_features_over_the_leading_rows() -> None:
    feature_count = BEESWARM_FEATURES + 5
    features = [f"f{index:02d}" for index in range(feature_count)]
    rows = BEESWARM_ROWS + 100
    sample = pl.DataFrame({name: np.arange(rows, dtype=float) for name in features})
    # Feature i's SHAP magnitude is i + 1, alternating sign by row, so the last
    # feature ranks first.
    signs = np.where(np.arange(rows) % 2 == 0, 1.0, -1.0)
    values = np.outer(signs, np.arange(1, feature_count + 1, dtype=float))

    summary, beeswarm = shap_diagnostics(values, sample, features, [])

    assert [entry["feature"] for entry in beeswarm] == [
        row["feature"] for row in summary[:BEESWARM_FEATURES]
    ]
    assert beeswarm[0]["feature"] == features[-1]
    assert {len(entry["shap_values"]) for entry in beeswarm} == {BEESWARM_ROWS}
    assert beeswarm[0]["shap_values"][:2] == [feature_count, -feature_count]
    assert beeswarm[0]["values"] == np.arange(BEESWARM_ROWS, dtype=float).tolist()


def test_numeric_ranks_average_ties_and_leave_missing_values_unranked() -> None:
    sample = pl.DataFrame({"age": [30.0, 20.0, None, 20.0, float("nan"), 40.0]})

    _, [entry] = shap_diagnostics(np.ones((6, 1)), sample, ["age"], [])

    assert entry["kind"] == "numeric"
    assert entry["values"] == [30.0, 20.0, None, 20.0, None, 40.0]
    assert entry["value_ranks"] == [0.6, 0.0, None, 0.0, None, 1.0]


def test_boolean_feature_ranks_false_low_and_true_high() -> None:
    sample = pl.DataFrame({"flag": [True, False, True]})

    _, [entry] = shap_diagnostics(np.ones((3, 1)), sample, ["flag"], [])

    assert entry["values"] == [1.0, 0.0, 1.0]
    assert entry["value_ranks"] == [1.0, 0.0, 1.0]


def test_single_valued_feature_has_no_ranks() -> None:
    sample = pl.DataFrame({"band": [5.0, None, 5.0]})

    _, [entry] = shap_diagnostics(np.ones((3, 1)), sample, ["band"], [])

    assert entry["values"] == [5.0, None, 5.0]
    assert entry["value_ranks"] == [None, None, None]


def test_categorical_levels_are_named_and_never_ranked() -> None:
    sample = pl.DataFrame({"region": ["north", None, "east"], "group": [3, 1, 3]})

    _, beeswarm = shap_diagnostics(
        np.array([[0.2, 0.1], [0.3, 0.1], [0.1, 0.1]]),
        sample,
        ["region", "group"],
        ["region", "group"],
    )

    region, group = beeswarm
    assert (region["kind"], region["values"]) == ("categorical", ["north", None, "east"])
    assert (group["kind"], group["values"]) == ("categorical", ["3", "1", "3"])
    assert region["value_ranks"] == group["value_ranks"] == [None, None, None]


def test_views_validate_as_the_response_payload() -> None:
    sample = pl.DataFrame({"region": ["north", "east"], "age": [30.0, None]})

    _, beeswarm = shap_diagnostics(
        np.array([[0.2, -0.3], [0.3, 0.4]]), sample, ["region", "age"], ["region"]
    )

    assert [TrainShapBeeswarmFeature.model_validate(entry).feature for entry in beeswarm] == [
        "age",
        "region",
    ]


def test_matrix_must_match_the_sample_and_features() -> None:
    sample = pl.DataFrame({"age": [1.0, 2.0, 3.0]})

    with pytest.raises(ValueError, match=r"expected \(3, 1\)"):
        shap_diagnostics(np.ones((2, 1)), sample, ["age"], [])


def test_adapter_refuses_a_model_with_another_feature_order() -> None:
    model = SimpleNamespace(features=["age", "region"])

    with pytest.raises(ValueError, match="model's feature order"):
        XGBoostAlgorithm().shap_values(model, pl.DataFrame(), ["region", "age"], ["region"])


def test_payload_refuses_rows_of_unequal_length() -> None:
    with pytest.raises(ValidationError, match="one item per plotted row"):
        TrainShapBeeswarmFeature(
            feature="age",
            kind="numeric",
            shap_values=[0.1],
            values=[1.0, 2.0],
            value_ranks=[0.0, 1.0],
        )


@pytest.mark.parametrize(
    ("kind", "values", "ranks", "message"),
    [
        ("numeric", ["north"], [None], "numeric feature's values must be numbers"),
        ("categorical", [3.0], [None], "categorical feature's values must be levels"),
        ("categorical", ["north"], [0.5], "categorical feature's value ranks must be null"),
        ("numeric", [3.0], [1.5], "less than or equal to 1"),
    ],
)
def test_payload_refuses_values_inconsistent_with_the_kind(
    kind: str, values: list[object], ranks: list[float | None], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        TrainShapBeeswarmFeature(
            feature="x", kind=kind, shap_values=[0.1], values=values, value_ranks=ranks
        )
