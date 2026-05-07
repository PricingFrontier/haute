"""Per-prediction model explanation helpers for trace enrichment."""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl


class ModelExplanationError(RuntimeError):
    """Raised when a model explanation cannot be computed correctly."""


def _as_float(
    value: Any,
    *,
    field_name: str = "value",
    strict: bool = False,
) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        if not strict:
            return None
        raise ModelExplanationError(f"{field_name} must be numeric; got {value!r}.") from exc
    if not np.isfinite(result):
        if not strict:
            return None
        raise ModelExplanationError(f"{field_name} must be finite; got {value!r}.")
    return result


def _catboost_pool_for_row(scoring_model: Any, input_row: dict[str, Any]) -> Any:
    """Build a one-row CatBoost Pool using Haute's scoring feature contract."""
    from catboost import Pool

    from haute._mlflow_io import _prepare_predict_frame

    features = list(scoring_model.feature_names)
    missing = [feature for feature in features if feature not in input_row]
    if missing:
        raise ModelExplanationError(
            "Missing feature(s) required for CatBoost SHAP explanation: "
            + ", ".join(missing)
        )

    cat_feature_names = frozenset(scoring_model.cat_feature_names or ())
    cat_cols = [feature for feature in features if feature in cat_feature_names]

    feature_frame = pl.DataFrame({feature: [input_row[feature]] for feature in features})
    x_data = _prepare_predict_frame(
        feature_frame,
        features,
        cat_feature_names=cat_feature_names,
        flavor="catboost",
    )

    if cat_cols:
        cat_indices = [features.index(column) for column in cat_cols]
        return Pool(
            x_data,
            cat_features=cat_indices,
            feature_names=features,
        )
    return Pool(x_data, feature_names=features)


def _normalise_shap_values(shap_values: Any, feature_count: int) -> np.ndarray:
    values = np.asarray(shap_values, dtype=float)
    if values.ndim == 1:
        values = values.reshape(1, -1)
    if values.ndim != 2:
        raise ModelExplanationError(
            "CatBoost SHAP explanation returned an unsupported shape: "
            f"{tuple(values.shape)}"
        )
    expected_shape = (1, feature_count + 1)
    if tuple(values.shape) != expected_shape:
        raise ModelExplanationError(
            "CatBoost SHAP explanation returned an unexpected shape: "
            f"{tuple(values.shape)}; expected {expected_shape}"
        )
    return values[0]


def _assert_finite_shap_row(shap_row: np.ndarray) -> None:
    if not np.all(np.isfinite(shap_row)):
        raise ModelExplanationError("CatBoost SHAP explanation returned non-finite values.")


def _prediction_tolerance(value: float) -> float:
    return max(1e-6, abs(value) * 1e-6)


def _catboost_prediction_in_shap_space(raw_model: Any, pool: Any, *, task: str) -> float:
    if task == "regression":
        prediction = raw_model.predict(pool)
    else:
        prediction = raw_model.predict(pool, prediction_type="RawFormulaVal")
    values = np.asarray(prediction, dtype=float).reshape(-1)
    if values.size != 1:
        raise ModelExplanationError(
            "CatBoost SHAP explanation for multi-output predictions is not supported yet."
        )
    return float(values[0])


def explain_catboost_prediction(
    scoring_model: Any,
    input_row: dict[str, Any],
    *,
    task: str = "regression",
    prediction_value: Any = None,
    max_contributions: int | None = None,
) -> dict[str, Any]:
    """Return CatBoost SHAP detail for one traced prediction.

    The returned values are sorted by absolute contribution so the trace UI
    shows the largest drivers first while still preserving every feature.
    """
    if getattr(scoring_model, "flavor", "") != "catboost":
        raise ModelExplanationError("CatBoost SHAP explanation requires a CatBoost model.")

    raw_model = scoring_model.raw_model
    if not hasattr(raw_model, "get_feature_importance"):
        raise ModelExplanationError(
            "Loaded CatBoost model does not expose get_feature_importance()."
        )

    features = list(scoring_model.feature_names)
    pool = _catboost_pool_for_row(scoring_model, input_row)
    shap_row = _normalise_shap_values(
        raw_model.get_feature_importance(data=pool, type="ShapValues"),
        len(features),
    )
    _assert_finite_shap_row(shap_row)

    base_value = float(shap_row[-1])
    contribution_values = shap_row[:-1]
    prediction_from_shap = float(shap_row.sum())
    model_prediction = _catboost_prediction_in_shap_space(raw_model, pool, task=task)
    output_value = _as_float(
        prediction_value,
        field_name="prediction_value",
        strict=task == "regression" and prediction_value is not None,
    )
    effective_prediction = model_prediction if output_value is None else output_value
    output_difference = None if output_value is None else float(output_value - prediction_from_shap)
    output_space = "prediction" if task == "regression" else "raw_formula_val"

    model_difference = float(model_prediction - prediction_from_shap)
    tolerance = _prediction_tolerance(prediction_from_shap)
    if abs(model_difference) > tolerance:
        raise ModelExplanationError(
            "CatBoost SHAP explanation does not match the model prediction: "
            f"SHAP reconstructs {prediction_from_shap}, model predicts {model_prediction}."
        )
    if task == "regression":
        if output_difference is not None and abs(output_difference) > tolerance:
            raise ModelExplanationError(
                "CatBoost SHAP explanation does not match the traced prediction: "
                f"SHAP reconstructs {prediction_from_shap}, traced output is {output_value}."
            )

    ranked_contributions = [
        {
            "feature": feature,
            "feature_index": index,
            "feature_value": input_row.get(feature),
            "shap_value": float(value),
            "abs_shap_value": float(abs(value)),
            "is_categorical": feature in frozenset(scoring_model.cat_feature_names or ()),
            "_feature_index": index,
        }
        for index, (feature, value) in enumerate(zip(features, contribution_values))
    ]
    ranked_contributions.sort(
        key=lambda item: (-float(item["abs_shap_value"]), int(item["_feature_index"]))
    )

    truncated = False
    omitted_count = 0
    if max_contributions is not None and len(ranked_contributions) > max_contributions:
        truncated = True
        omitted_count = len(ranked_contributions) - max_contributions
        ranked_contributions = ranked_contributions[:max_contributions]

    contributions = []
    for rank, item in enumerate(ranked_contributions, start=1):
        item = dict(item)
        item.pop("_feature_index", None)
        item["rank"] = rank
        contributions.append(item)

    return {
        "type": "catboost_shap",
        "method": "catboost_shap",
        "status": "ok",
        "output_space": output_space,
        "prediction_space": output_space,
        "base_value": base_value,
        "sum_contributions": float(contribution_values.sum()),
        "contribution_sum": float(contribution_values.sum()),
        "prediction_from_shap": prediction_from_shap,
        "model_output_value": model_prediction,
        "prediction_value": effective_prediction,
        "output_difference": output_difference,
        "feature_count": len(features),
        "feature_values": {feature: input_row.get(feature) for feature in features},
        "contributions": contributions,
        "truncated": truncated,
        "omitted_count": omitted_count,
    }


def _config_requests_catboost_explanation(config: dict[str, Any]) -> bool:
    source_type = config.get("sourceType")
    if source_type not in {"run", "registered"}:
        return False
    artifact_path = str(config.get("artifact_path", ""))
    return artifact_path.endswith(".cbm")


def explain_model_score_from_config(
    config: dict[str, Any],
    input_row: dict[str, Any],
    output_row: dict[str, Any],
    *,
    prediction_column: str,
    prediction_value: Any,
) -> dict[str, Any] | None:
    """Load the configured model and return trace explanation detail."""
    if not _config_requests_catboost_explanation(config):
        return None

    from haute._mlflow_io import load_mlflow_model

    scoring_model = load_mlflow_model(
        source_type=config.get("sourceType", "run"),
        run_id=config.get("run_id", ""),
        artifact_path=config.get("artifact_path", ""),
        registered_model=config.get("registered_model", ""),
        version=config.get("version", "latest"),
        task=config.get("task", "regression"),
    )
    return explain_catboost_prediction(
        scoring_model,
        input_row,
        task=config.get("task", "regression"),
        prediction_value=(
            prediction_value if prediction_value is not None else output_row.get(prediction_column)
        ),
    )
