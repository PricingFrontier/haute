"""Tests for per-row model explainability helpers."""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl
import pytest


def _train_catboost_with_categorical_feature() -> Any:
    pytest.importorskip("catboost", reason="catboost optional dependency not installed")
    from catboost import CatBoostRegressor, Pool

    rng = np.random.RandomState(7)
    n = 72
    regions = np.array(["north", "south", "west"])
    region_values = rng.choice(regions, size=n)
    age = rng.randint(18, 75, size=n).astype(float)
    vehicle_value = rng.uniform(5_000.0, 35_000.0, size=n)
    region_loading = {"north": 20.0, "south": 55.0, "west": -10.0}
    target = (
        100.0
        + age * 1.7
        + vehicle_value * 0.003
        + np.array([region_loading[r] for r in region_values])
    )

    train_df = pl.DataFrame(
        {
            "region": region_values,
            "age": age,
            "vehicle_value": vehicle_value,
            "target": target,
        }
    )
    features = ["region", "age", "vehicle_value"]
    model = CatBoostRegressor(
        iterations=12,
        depth=3,
        learning_rate=0.25,
        random_seed=11,
        verbose=0,
        allow_writing_files=False,
    )
    model.fit(
        Pool(
            train_df.select(features).to_pandas(),
            label=train_df["target"].to_numpy(),
            cat_features=[0],
        )
    )
    return model


@pytest.fixture()
def catboost_scoring_model() -> Any:
    pytest.importorskip("catboost", reason="catboost optional dependency not installed")
    from haute._mlflow_io import _wrap_catboost

    return _wrap_catboost(_train_catboost_with_categorical_feature())


def test_catboost_shap_contributions_sum_to_prediction(catboost_scoring_model: Any) -> None:
    from haute._model_explainability import explain_catboost_prediction

    row = {
        "vehicle_value": 18_250.0,
        "age": 43.0,
        "region": "south",
        "unused_input_column": "ignored",
    }

    explanation = explain_catboost_prediction(catboost_scoring_model, row)
    shap_prediction = explanation["base_value"] + sum(
        item["shap_value"] for item in explanation["contributions"]
    )

    assert list(explanation["feature_values"]) == [
        "region",
        "age",
        "vehicle_value",
    ]
    assert explanation["status"] == "ok"
    assert explanation["output_space"] == "prediction"
    assert explanation["truncated"] is False
    assert explanation["omitted_count"] == 0
    assert shap_prediction == pytest.approx(explanation["prediction_value"], abs=1e-6)
    assert explanation["prediction_from_shap"] == pytest.approx(
        explanation["prediction_value"],
        abs=1e-6,
    )


def test_catboost_shap_preserves_categorical_feature_values(catboost_scoring_model: Any) -> None:
    from haute._model_explainability import explain_catboost_prediction

    row = {"vehicle_value": 12_500.0, "age": 29.0, "region": "north"}

    explanation = explain_catboost_prediction(catboost_scoring_model, row)
    region_contribution = next(
        item for item in explanation["contributions"] if item["feature"] == "region"
    )

    assert region_contribution["feature_value"] == "north"
    assert region_contribution["is_categorical"] is True
    assert explanation["feature_values"]["region"] == "north"


def test_catboost_shap_fails_loudly_for_missing_features(
    catboost_scoring_model: Any,
) -> None:
    from haute._model_explainability import ModelExplanationError, explain_catboost_prediction

    with pytest.raises(ModelExplanationError, match="Missing feature"):
        explain_catboost_prediction(
            catboost_scoring_model,
            {"region": "north", "age": 29.0},
        )


def test_catboost_shap_fails_loudly_when_prediction_mismatches(
    catboost_scoring_model: Any,
) -> None:
    from haute._model_explainability import ModelExplanationError, explain_catboost_prediction

    with pytest.raises(ModelExplanationError, match="does not match"):
        explain_catboost_prediction(
            catboost_scoring_model,
            {"vehicle_value": 12_500.0, "age": 29.0, "region": "north"},
            prediction_value=-999.0,
        )


def test_catboost_classifier_shap_labels_raw_formula_output_space() -> None:
    pytest.importorskip("catboost", reason="catboost optional dependency not installed")
    from catboost import CatBoostClassifier, Pool

    from haute._mlflow_io import _wrap_catboost
    from haute._model_explainability import explain_catboost_prediction

    features = ["age", "region"]
    train_df = pl.DataFrame(
        {
            "age": [20, 22, 35, 42, 55, 65],
            "region": ["north", "north", "south", "south", "west", "west"],
            "target": [0, 0, 1, 1, 0, 1],
        }
    )
    model = CatBoostClassifier(
        iterations=10,
        depth=2,
        learning_rate=0.3,
        random_seed=13,
        verbose=0,
        allow_writing_files=False,
    )
    model.fit(
        Pool(
            train_df.select(features).to_pandas(),
            label=train_df["target"].to_numpy(),
            cat_features=[1],
        )
    )
    scoring_model = _wrap_catboost(model)

    explanation = explain_catboost_prediction(
        scoring_model,
        {"age": 42, "region": "south"},
        task="classification",
        prediction_value="accepted",
    )

    assert explanation["status"] == "ok"
    assert explanation["output_space"] == "raw_formula_val"
    assert explanation["prediction_from_shap"] == pytest.approx(
        explanation["model_output_value"],
        abs=1e-6,
    )
