"""SHAP sampling, and the summary, beeswarm and curves built from one SHAP matrix."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest
from pydantic import ValidationError

from haute.modelling._shap import (
    BEESWARM_FEATURES,
    BEESWARM_ROWS,
    CURVE_BANDS,
    CURVE_LEVELS,
    SHAP_SAMPLE_ROWS,
    shap_diagnostics,
    shap_sample,
)
from haute.modelling._xgboost import XGBoostAlgorithm
from haute.schemas import (
    TrainResponse,
    TrainShapBeeswarmFeature,
    TrainShapCurveFeature,
)


def test_sample_caps_the_rows_and_is_deterministic() -> None:
    df = pl.DataFrame({"row": range(SHAP_SAMPLE_ROWS * 2)})

    sample = shap_sample(df)

    assert sample.height == SHAP_SAMPLE_ROWS
    assert sample["row"].n_unique() == SHAP_SAMPLE_ROWS
    assert sample.equals(shap_sample(df))


def test_sample_shuffles_a_partition_under_the_cap() -> None:
    df = pl.DataFrame({"row": range(BEESWARM_ROWS + 1_000)})

    sample = shap_sample(df)

    assert sorted(sample["row"].to_list()) == list(range(df.height))
    # The beeswarm's leading rows spread across the partition, not its first rows.
    assert sample["row"].head(BEESWARM_ROWS).max() > BEESWARM_ROWS


def test_summary_is_largest_first_with_ties_in_feature_order() -> None:
    sample = pl.DataFrame({"a": [1.0, 2.0], "b": [1.0, 2.0], "c": [1.0, 2.0]})
    values = np.array([[0.1, -0.4, 0.4], [-0.1, 0.4, -0.4]])

    views = shap_diagnostics(values, sample, ["a", "b", "c"], [])

    assert views.summary == [
        {"feature": "b", "mean_abs_shap": pytest.approx(0.4)},
        {"feature": "c", "mean_abs_shap": pytest.approx(0.4)},
        {"feature": "a", "mean_abs_shap": pytest.approx(0.1)},
    ]
    assert [curve["feature"] for curve in views.curves] == ["b", "c", "a"]


def test_beeswarm_keeps_the_summary_top_features_over_the_leading_rows() -> None:
    feature_count = BEESWARM_FEATURES + 5
    features = [f"f{index:02d}" for index in range(feature_count)]
    rows = BEESWARM_ROWS + 100
    sample = pl.DataFrame({name: np.arange(rows, dtype=float) for name in features})
    # Feature i's SHAP magnitude is i + 1, alternating sign by row, so the last
    # feature ranks first.
    signs = np.where(np.arange(rows) % 2 == 0, 1.0, -1.0)
    values = np.outer(signs, np.arange(1, feature_count + 1, dtype=float))

    views = shap_diagnostics(values, sample, features, [])

    assert [entry["feature"] for entry in views.beeswarm] == [
        row["feature"] for row in views.summary[:BEESWARM_FEATURES]
    ]
    assert views.beeswarm[0]["feature"] == features[-1]
    assert {len(entry["shap_values"]) for entry in views.beeswarm} == {BEESWARM_ROWS}
    assert views.beeswarm[0]["shap_values"][:2] == [feature_count, -feature_count]
    assert views.beeswarm[0]["values"] == np.arange(BEESWARM_ROWS, dtype=float).tolist()
    # Curves cover every feature over every sampled row.
    assert len(views.curves) == feature_count
    assert sum(point["rows"] for point in views.curves[0]["points"]) == rows


def test_beeswarm_rounds_shap_and_numeric_values_for_transport() -> None:
    sample = pl.DataFrame({"sum_insured": [123456.789, None]})

    views = shap_diagnostics(np.array([[0.123456789], [-0.000987654]]), sample, ["sum_insured"], [])

    [entry] = views.beeswarm
    assert entry["shap_values"] == [0.1235, -0.0009877]
    assert entry["values"] == [123457.0, None]


def test_numeric_ranks_average_ties_and_leave_missing_values_unranked() -> None:
    sample = pl.DataFrame({"age": [30.0, 20.0, None, 20.0, float("nan"), 40.0]})

    [entry] = shap_diagnostics(np.ones((6, 1)), sample, ["age"], []).beeswarm

    assert entry["kind"] == "numeric"
    assert entry["values"] == [30.0, 20.0, None, 20.0, None, 40.0]
    assert entry["value_ranks"] == [0.6, 0.0, None, 0.0, None, 1.0]


def test_boolean_feature_ranks_false_low_and_true_high() -> None:
    sample = pl.DataFrame({"flag": [True, False, True]})

    [entry] = shap_diagnostics(np.ones((3, 1)), sample, ["flag"], []).beeswarm

    assert entry["values"] == [1.0, 0.0, 1.0]
    assert entry["value_ranks"] == [1.0, 0.0, 1.0]


def test_single_valued_feature_has_no_ranks() -> None:
    sample = pl.DataFrame({"band": [5.0, None, 5.0]})

    [entry] = shap_diagnostics(np.ones((3, 1)), sample, ["band"], []).beeswarm

    assert entry["values"] == [5.0, None, 5.0]
    assert entry["value_ranks"] == [None, None, None]


def test_categorical_levels_are_named_and_never_ranked() -> None:
    sample = pl.DataFrame({"region": ["north", None, "east"], "group": [3, 1, 3]})

    region, group = shap_diagnostics(
        np.array([[0.2, 0.1], [0.3, 0.1], [0.1, 0.1]]),
        sample,
        ["region", "group"],
        ["region", "group"],
    ).beeswarm

    assert (region["kind"], region["values"]) == ("categorical", ["north", None, "east"])
    assert (group["kind"], group["values"]) == ("categorical", ["3", "1", "3"])
    assert region["value_ranks"] == group["value_ranks"] == [None, None, None]


def test_few_valued_numeric_curve_has_a_point_per_value_and_missing_rows_last() -> None:
    sample = pl.DataFrame({"ncd": [2.0, 0.0, None, 2.0, 0.0, 5.0]})
    shap = np.array([[0.4], [-0.2], [0.05], [0.2], [-0.4], [1.0]])

    [curve] = shap_diagnostics(shap, sample, ["ncd"], []).curves

    assert curve["levels_omitted"] == 0
    assert [(p["value"], p["low"], p["high"], p["rows"]) for p in curve["points"]] == [
        (0.0, 0.0, 0.0, 2),
        (2.0, 2.0, 2.0, 2),
        (5.0, 5.0, 5.0, 1),
        (None, None, None, 1),
    ]
    zero = curve["points"][0]
    assert zero["mean_shap"] == pytest.approx(-0.3)
    assert zero["p10_shap"] == pytest.approx(-0.38)
    assert zero["p90_shap"] == pytest.approx(-0.22)


def test_many_valued_numeric_curve_bands_ordered_quantiles_that_cover_their_rows() -> None:
    rng = np.random.default_rng(0)
    ages = rng.uniform(18, 80, 1_000)
    sample = pl.DataFrame({"age": ages})
    shap = (ages - 49) / 100

    [curve] = shap_diagnostics(shap[:, None], sample, ["age"], []).curves

    points = curve["points"]
    assert len(points) == CURVE_BANDS
    assert sum(point["rows"] for point in points) == 1_000
    assert all(point["low"] <= point["value"] <= point["high"] for point in points)
    assert all(left["high"] < right["low"] for left, right in zip(points, points[1:], strict=False))
    assert points[0]["low"] == ages.min() and points[-1]["high"] == ages.max()
    # SHAP rises with age, so the band means rise too.
    means = [point["mean_shap"] for point in points]
    assert means == sorted(means)
    TrainShapCurveFeature.model_validate(curve)


def test_categorical_curve_keeps_the_most_frequent_levels_and_counts_the_rest() -> None:
    levels = [f"L{index:02d}" for index in range(CURVE_LEVELS + 5)]
    # Level i appears i + 1 times, so the last levels are the most frequent.
    column = [level for index, level in enumerate(levels) for _ in range(index + 1)]
    column += [None] * 3
    sample = pl.DataFrame({"occupation": column})
    shap = np.arange(len(column), dtype=float)[:, None]

    [curve] = shap_diagnostics(shap, sample, ["occupation"], ["occupation"]).curves

    assert curve["kind"] == "categorical"
    assert len(curve["points"]) == CURVE_LEVELS
    assert curve["levels_omitted"] == len(levels) + 1 - CURVE_LEVELS
    assert [point["value"] for point in curve["points"][:2]] == [levels[-1], levels[-2]]
    assert [point["rows"] for point in curve["points"][:2]] == [len(levels), len(levels) - 1]
    assert all(point["low"] is None and point["high"] is None for point in curve["points"])
    TrainShapCurveFeature.model_validate(curve)


def test_views_validate_as_the_response_payload() -> None:
    sample = pl.DataFrame({"region": ["north", "east"], "age": [30.0, None]})

    views = shap_diagnostics(
        np.array([[0.2, -0.3], [0.3, 0.4]]), sample, ["region", "age"], ["region"]
    )

    response = TrainResponse(
        status="started",
        shap_beeswarm=views.beeswarm,
        shap_curves=views.curves,
        shap_link="log",
    )
    assert [entry.feature for entry in response.shap_beeswarm] == ["age", "region"]
    assert [curve.feature for curve in response.shap_curves] == ["age", "region"]


def test_response_names_a_link_exactly_when_it_has_curves() -> None:
    curve = {
        "feature": "age",
        "kind": "numeric",
        "points": [
            {"value": 1.0, "low": 1.0, "high": 1.0, "rows": 1},
        ],
        "levels_omitted": 0,
    }
    curve["points"][0] |= {"mean_shap": 0.1, "p10_shap": 0.1, "p90_shap": 0.1}

    with pytest.raises(ValidationError, match="shap_link"):
        TrainResponse(status="started", shap_curves=[curve])
    with pytest.raises(ValidationError, match="shap_link"):
        TrainResponse(status="started", shap_link="log")


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


def _point(value: object, low: float | None = None, high: float | None = None, **extra: object):
    return {
        "value": value,
        "low": low,
        "high": high,
        "rows": 1,
        "mean_shap": 0.0,
        "p10_shap": -0.1,
        "p90_shap": 0.1,
    } | extra


@pytest.mark.parametrize(
    ("kind", "points", "omitted", "message"),
    [
        ("numeric", [_point(5.0, 1.0, 4.0)], 0, "between its low and high"),
        ("numeric", [_point(None), _point(1.0, 1.0, 1.0)], 0, "missing-value point comes last"),
        ("numeric", [_point("north")], 0, "need a numeric value, low and high"),
        ("numeric", [_point(1.0, 1.0, 1.0)], 2, "omits no levels"),
        ("categorical", [_point(None), _point(None)], 0, "at most one missing-value point"),
        ("categorical", [_point(3.0)], 0, "must be levels"),
        ("categorical", [_point("north", 1.0, 2.0)], 0, "have no low or high"),
        ("categorical", [_point("north", p10_shap=0.5)], 0, "must not exceed p90_shap"),
        ("categorical", [_point("north", rows=0)], 0, "greater than or equal to 1"),
    ],
)
def test_curve_payload_refuses_inconsistent_points(
    kind: str, points: list[dict[str, object]], omitted: int, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        TrainShapCurveFeature(feature="x", kind=kind, points=points, levels_omitted=omitted)
