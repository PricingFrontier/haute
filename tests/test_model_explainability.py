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


class _FakeRustyStatsGLM:
    def __init__(
        self,
        contribution_record: dict[str, Any],
        *,
        prediction: float | None = None,
    ) -> None:
        self.contribution_record = contribution_record
        self.prediction = (
            float(contribution_record["prediction_value"]) if prediction is None else prediction
        )
        self.received_frame: pl.DataFrame | None = None

    def predict_contributions(self, new_data: pl.DataFrame, **kwargs: Any) -> list[dict[str, Any]]:
        self.received_frame = new_data
        assert kwargs == {
            "group_terms": True,
            "include_design_columns": False,
            "return_format": "records",
            "validate": True,
        }
        return [self.contribution_record]

    def predict(self, new_data: pl.DataFrame) -> np.ndarray:
        self.received_frame = new_data
        return np.asarray([self.prediction], dtype=float)


def _rustystats_scoring_model(raw_model: Any, features: list[str] | None = None) -> Any:
    from haute._mlflow_io import ScoringModel

    return ScoringModel(
        model=raw_model,
        feature_names=features or ["difference_to_market"],
        cat_feature_names=frozenset(),
        flavor="rustystats",
    )


def test_rustystats_glm_contributions_map_to_shared_trace_contract() -> None:
    from haute._model_explainability import explain_rustystats_prediction

    raw_model = _FakeRustyStatsGLM(
        {
            "family": "binomial",
            "link": "logit",
            "output_space": "linear_predictor",
            "prediction_space": "response",
            "base_value": 0.1,
            "sum_contributions": 0.5,
            "prediction_from_contributions": 0.6,
            "prediction_value": 0.645656306,
            "contributions": [
                {
                    "term": "difference_to_market",
                    "term_type": "ns",
                    "feature": "difference_to_market",
                    "feature_value": -20.0,
                    "contribution": 0.5,
                    "rank": 1,
                }
            ],
        }
    )
    scoring_model = _rustystats_scoring_model(raw_model)

    explanation = explain_rustystats_prediction(
        scoring_model,
        {"difference_to_market": -20.0, "ignored": "not passed"},
        prediction_value=0.645656306,
    )

    assert explanation["type"] == "rustystats_glm_contributions"
    assert explanation["method"] == "rustystats_glm_contributions"
    assert explanation["status"] == "ok"
    assert explanation["family"] == "binomial"
    assert explanation["link_function"] == "logit"
    assert explanation["output_space"] == "linear_predictor"
    assert explanation["prediction_space"] == "response"
    assert explanation["base_value"] == pytest.approx(0.1)
    assert explanation["sum_contributions"] == pytest.approx(0.5)
    assert explanation["contribution_sum"] == pytest.approx(0.5)
    assert explanation["prediction_from_contributions"] == pytest.approx(0.6)
    assert explanation["model_output_value"] == pytest.approx(0.6)
    assert explanation["prediction_value"] == pytest.approx(0.645656306)
    assert explanation["feature_values"] == {"difference_to_market": -20.0}
    assert explanation["feature_count"] == 1
    assert explanation["truncated"] is False
    assert explanation["omitted_count"] == 0
    assert raw_model.received_frame is not None
    assert raw_model.received_frame.columns == ["difference_to_market"]

    contribution = explanation["contributions"][0]
    assert contribution["feature"] == "difference_to_market"
    assert contribution["term"] == "difference_to_market"
    assert contribution["term_type"] == "ns"
    assert contribution["feature_value"] == -20.0
    assert contribution["contribution"] == pytest.approx(0.5)
    assert contribution["abs_contribution"] == pytest.approx(0.5)
    assert contribution["shap_value"] == pytest.approx(0.5)
    assert contribution["abs_shap_value"] == pytest.approx(0.5)
    assert contribution["rank"] == 1


def test_rustystats_glm_contributions_fail_loudly_for_missing_features() -> None:
    from haute._model_explainability import (
        ModelExplanationError,
        explain_rustystats_prediction,
    )

    raw_model = _FakeRustyStatsGLM(
        {
            "base_value": 0.0,
            "sum_contributions": 0.0,
            "prediction_from_contributions": 0.0,
            "prediction_value": 0.5,
            "contributions": [],
        }
    )
    scoring_model = _rustystats_scoring_model(
        raw_model,
        ["difference_to_market", "offset_col"],
    )

    with pytest.raises(ModelExplanationError, match="Missing feature"):
        explain_rustystats_prediction(
            scoring_model,
            {"difference_to_market": -20.0},
        )


def test_rustystats_glm_contributions_fail_loudly_when_prediction_mismatches() -> None:
    from haute._model_explainability import (
        ModelExplanationError,
        explain_rustystats_prediction,
    )

    raw_model = _FakeRustyStatsGLM(
        {
            "base_value": 0.1,
            "sum_contributions": 0.5,
            "prediction_from_contributions": 0.6,
            "prediction_value": 0.645656306,
            "contributions": [
                {
                    "feature": "difference_to_market",
                    "contribution": 0.5,
                }
            ],
        }
    )

    with pytest.raises(ModelExplanationError, match="does not match"):
        explain_rustystats_prediction(
            _rustystats_scoring_model(raw_model),
            {"difference_to_market": -20.0},
            prediction_value=0.2,
        )


def test_rustystats_glm_contributions_fail_loudly_when_additivity_breaks() -> None:
    from haute._model_explainability import (
        ModelExplanationError,
        explain_rustystats_prediction,
    )

    raw_model = _FakeRustyStatsGLM(
        {
            "base_value": 0.1,
            "sum_contributions": 0.5,
            "prediction_from_contributions": 99.0,
            "prediction_value": 0.645656306,
            "contributions": [
                {
                    "feature": "difference_to_market",
                    "contribution": 0.5,
                }
            ],
        }
    )

    with pytest.raises(ModelExplanationError, match="does not reconstruct"):
        explain_rustystats_prediction(
            _rustystats_scoring_model(raw_model),
            {"difference_to_market": -20.0},
        )


def test_real_rustystats_conversion_model_predict_contributions_contract() -> None:
    pytest.importorskip("rustystats", reason="rustystats optional dependency not installed")
    from pathlib import Path

    from haute._mlflow_io import _load_rustystats_model
    from haute._model_explainability import explain_rustystats_prediction

    path = Path("outputs/conversion.rsglm")
    if not path.is_file():
        pytest.skip("new-format conversion.rsglm fixture not present")

    scoring_model = _load_rustystats_model(str(path))
    row = {"difference_to_market": 0.0}
    explanation = explain_rustystats_prediction(scoring_model, row)

    assert explanation["method"] == "rustystats_glm_contributions"
    assert explanation["family"] == "binomial"
    assert explanation["link_function"] == "logit"
    assert explanation["output_space"] == "linear_predictor"
    assert explanation["prediction_space"] == "response"
    assert explanation["prediction_value"] == pytest.approx(
        float(scoring_model.raw_model.predict(pl.DataFrame(row))[0]),
        abs=1e-9,
    )
    assert explanation["base_value"] + sum(
        item["contribution"] for item in explanation["contributions"]
    ) == pytest.approx(explanation["prediction_from_contributions"], abs=1e-9)


def test_rustystats_glm_contributions_sum_to_prediction() -> None:
    rs = pytest.importorskip("rustystats", reason="rustystats optional dependency not installed")
    from haute._mlflow_io import ScoringModel
    from haute._model_explainability import explain_rustystats_glm_prediction

    train_df = pl.DataFrame(
        {
            "driver_age": [21.0, 30.0, 42.0, 55.0, 67.0, 72.0],
            "area": ["A", "B", "A", "C", "B", "C"],
            "claim_count": [0, 1, 1, 2, 2, 3],
        }
    )
    model = rs.glm_dict(
        response="claim_count",
        terms={
            "driver_age": {"type": "linear"},
            "area": {"type": "categorical"},
        },
        data=train_df,
        family="poisson",
    ).fit()
    scoring_model = ScoringModel(
        model=model,
        feature_names=list(model.required_columns),
        flavor="rustystats",
    )

    row = {"driver_age": 42.0, "area": "B", "ignored": "not a feature"}
    explanation = explain_rustystats_glm_prediction(scoring_model, row)

    assert explanation["type"] == "rustystats_glm_contributions"
    assert explanation["method"] == "rustystats_glm_contributions"
    assert explanation["status"] == "ok"
    assert explanation["output_space"] == "linear_predictor"
    assert explanation["prediction_space"] == "response"
    assert explanation["prediction_value"] == pytest.approx(model.predict(pl.DataFrame([row]))[0])
    assert explanation["prediction_from_contributions"] == pytest.approx(
        explanation["base_value"] + sum(c["contribution"] for c in explanation["contributions"]),
        abs=1e-9,
    )
    assert list(explanation["feature_values"]) == list(model.required_columns)
    assert {item["feature"] for item in explanation["contributions"]} == {
        "driver_age",
        "area",
    }


def test_rustystats_glm_fails_loudly_for_missing_features() -> None:
    rs = pytest.importorskip("rustystats", reason="rustystats optional dependency not installed")
    from haute._mlflow_io import ScoringModel
    from haute._model_explainability import (
        ModelExplanationError,
        explain_rustystats_glm_prediction,
    )

    train_df = pl.DataFrame({"x": [1.0, 2.0, 3.0, 4.0], "y": [2.0, 3.0, 4.0, 5.0]})
    model = rs.glm_dict(
        response="y",
        terms={"x": {"type": "linear"}},
        data=train_df,
        family="gaussian",
    ).fit()
    scoring_model = ScoringModel(
        model=model,
        feature_names=list(model.required_columns),
        flavor="rustystats",
    )

    with pytest.raises(ModelExplanationError, match="Missing feature"):
        explain_rustystats_glm_prediction(scoring_model, {})


def test_rustystats_glm_explanation_selected_from_rsglm_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute import _model_explainability
    from haute._mlflow_io import ScoringModel

    class FakeGLM:
        required_columns = ["x"]

        def predict(self, data: pl.DataFrame) -> np.ndarray:
            return np.asarray(data["x"], dtype=float) + 2.0

        def predict_contributions(self, data: pl.DataFrame, **_: Any) -> list[dict[str, Any]]:
            x_value = float(data["x"][0])
            return [
                {
                    "family": "gaussian",
                    "link": "identity",
                    "output_space": "response",
                    "prediction_space": "response",
                    "base_value": 2.0,
                    "sum_contributions": x_value,
                    "prediction_from_contributions": x_value + 2.0,
                    "prediction_value": x_value + 2.0,
                    "contributions": [
                        {
                            "term": "x",
                            "term_type": "linear",
                            "feature": "x",
                            "feature_value": x_value,
                            "contribution": x_value,
                            "rank": 1,
                        }
                    ],
                }
            ]

    def fake_load_mlflow_model(**_: Any) -> ScoringModel:
        return ScoringModel(model=FakeGLM(), feature_names=["x"], flavor="rustystats")

    monkeypatch.setattr("haute._mlflow_io.load_mlflow_model", fake_load_mlflow_model)

    explanation = _model_explainability.explain_model_score_from_config(
        {
            "sourceType": "run",
            "run_id": "run-1",
            "artifact_path": "conversion.rsglm",
            "task": "regression",
        },
        {"x": 3.5},
        {"prediction": 5.5},
        prediction_column="prediction",
        prediction_value=5.5,
    )

    assert explanation is not None
    assert explanation["type"] == "rustystats_glm_contributions"
    assert explanation["prediction_value"] == pytest.approx(5.5)
    assert explanation["prediction_from_contributions"] == pytest.approx(5.5)
