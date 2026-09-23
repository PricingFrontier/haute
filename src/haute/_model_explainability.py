"""Per-prediction model explanation helpers for trace enrichment."""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl

from haute._logging import get_logger
from haute.errors import HauteValidationError

logger = get_logger(component="model_explainability")


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
            "Missing feature(s) required for CatBoost SHAP explanation: " + ", ".join(missing)
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

    offset_column = getattr(scoring_model, "offset_column", None)
    if offset_column:
        if offset_column not in input_row:
            raise ModelExplanationError(
                "Missing offset column required for CatBoost SHAP explanation: " + offset_column
            )
        baseline_value = _as_float(
            input_row[offset_column],
            field_name=f"offset column {offset_column!r}",
            strict=True,
        )
        if baseline_value is None:
            raise ModelExplanationError(
                f"offset column {offset_column!r} must be finite; got None."
            )
        from haute.modelling._algorithms import offset_baseline

        offset_link = getattr(scoring_model, "offset_link", None)
        if offset_link is None:
            raise ModelExplanationError(
                f"offset column {offset_column!r} has no recorded link; retrain the model."
            )
        try:
            baseline = offset_baseline(
                [baseline_value],
                column=offset_column,
                link=offset_link,
                context="CatBoost SHAP explanation",
            )
        except HauteValidationError as exc:
            raise ModelExplanationError(str(exc)) from exc
    else:
        baseline = None
    cat_indices = [features.index(column) for column in cat_cols]
    return Pool(
        x_data,
        cat_features=cat_indices if cat_indices else None,
        feature_names=features,
        baseline=baseline,
    )


def _normalise_shap_values(shap_values: Any, feature_count: int) -> np.ndarray:
    values = np.asarray(shap_values, dtype=float)
    if values.ndim == 1:
        values = values.reshape(1, -1)
    if values.ndim != 2:
        raise ModelExplanationError(
            f"CatBoost SHAP explanation returned an unsupported shape: {tuple(values.shape)}"
        )
    expected_shape = (1, feature_count + 1)
    if tuple(values.shape) != expected_shape:
        raise ModelExplanationError(
            "CatBoost SHAP explanation returned an unexpected shape: "
            f"{tuple(values.shape)}; expected {expected_shape}"
        )
    shap_row: np.ndarray = values[0]
    return shap_row


def _assert_finite_shap_row(shap_row: np.ndarray) -> None:
    if not np.all(np.isfinite(shap_row)):
        raise ModelExplanationError("CatBoost SHAP explanation returned non-finite values.")


#: Relative bound for raw margins rebuilt from XGBoost contributions, which the
#: engine accumulates in float32.
FLOAT32_CONTRIBUTION_TOLERANCE = 1e-5


def prediction_tolerance(value: float, *, relative: float = 1e-6) -> float:
    """The absolute tolerance every prediction-parity check allows around *value*."""
    return max(relative, abs(value) * relative)


def _catboost_single_prediction_value(prediction: Any) -> float:
    values = np.asarray(prediction, dtype=float).reshape(-1)
    if values.size != 1:
        raise ModelExplanationError(
            "CatBoost SHAP explanation for multi-output predictions is not supported yet."
        )
    return float(values[0])


def _catboost_raw_prediction(raw_model: Any, pool: Any) -> float:
    """Model output in raw-formula space — the space CatBoost ShapValues sum in.

    ``get_feature_importance(type="ShapValues")`` always returns raw-formula
    contributions, regardless of loss function, so the additivity check must
    compare against ``prediction_type="RawFormulaVal"`` explicitly.  Relying
    on the default ``predict()`` is wrong for link-function losses: CatBoost
    resolves the default prediction type to ``Exponent`` for Poisson/Tweedie,
    which would compare exponentiated predictions against raw SHAP sums.
    """
    return _catboost_single_prediction_value(
        raw_model.predict(pool, prediction_type="RawFormulaVal")
    )


def _catboost_response_prediction(raw_model: Any, pool: Any) -> float:
    """Model output in default ``predict()`` space (what scoring traces).

    For link-function losses this applies the final transform (``Exponent``
    for Poisson/Tweedie); for identity losses it equals the raw-formula value.
    """
    return _catboost_single_prediction_value(raw_model.predict(pool))


def _catboost_regression_has_link_transform(raw_model: Any) -> bool:
    """True when the trained loss applies a final exp transform in ``predict()``.

    Mirrors CatBoost's own default-prediction-type rule
    (``catboost.core.CatBoost._get_default_prediction_type``): ``Poisson*``
    and ``Tweedie*`` losses predict in ``Exponent`` space while ShapValues
    stay in raw-formula space.  ``get_all_params()`` carries the canonized
    loss name (aliases such as ``objective`` included) on both freshly
    trained and ``.cbm``-loaded models.
    """
    loss_function = str(raw_model.get_all_params().get("loss_function", ""))
    return loss_function.startswith(("Poisson", "Tweedie"))


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

    Spaces: CatBoost ShapValues are always raw-formula-space, so
    ``base_value``, ``contributions``, ``prediction_from_shap`` and
    ``model_output_value`` are raw-formula values and additivity is checked
    in that space.  For identity regression losses (e.g. RMSE) raw-formula
    space *is* the prediction space, reported as
    ``output_space == "prediction"``.  For link-function losses
    (Poisson/Tweedie) ``predict()`` exponentiates, so the displayed
    contributions stay in raw (log) space — the standard presentation for
    link-function models — with ``output_space == "raw_formula_val"``, while
    ``prediction_value`` and the traced-output check stay in response space
    (``prediction_space == "prediction"``).
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
    # Additivity is always checked raw-vs-raw: ShapValues sum to the
    # raw-formula prediction for every CatBoost loss.
    model_prediction = _catboost_raw_prediction(raw_model, pool)

    if task == "regression":
        has_link = _catboost_regression_has_link_transform(raw_model)
        response_prediction = (
            _catboost_response_prediction(raw_model, pool) if has_link else model_prediction
        )
        output_space = "raw_formula_val" if has_link else "prediction"
    else:
        response_prediction = model_prediction
        output_space = "raw_formula_val"
    prediction_space = "prediction" if task == "regression" else "raw_formula_val"

    output_value = _as_float(
        prediction_value,
        field_name="prediction_value",
        strict=task == "regression" and prediction_value is not None,
    )
    effective_prediction = response_prediction if output_value is None else output_value
    output_difference = None if output_value is None else float(output_value - response_prediction)

    model_difference = float(model_prediction - prediction_from_shap)
    tolerance = prediction_tolerance(prediction_from_shap)
    if abs(model_difference) > tolerance:
        raise ModelExplanationError(
            "CatBoost SHAP explanation does not match the model prediction: "
            f"SHAP reconstructs {prediction_from_shap}, model predicts {model_prediction}."
        )
    if task == "regression" and output_difference is not None:
        response_tolerance = prediction_tolerance(response_prediction)
        if abs(output_difference) > response_tolerance:
            raise ModelExplanationError(
                "CatBoost SHAP explanation does not match the traced prediction: "
                f"model predicts {response_prediction}, traced output is {output_value}."
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
        "prediction_space": prediction_space,
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


def _rustystats_frame_for_row(scoring_model: Any, input_row: dict[str, Any]) -> pl.DataFrame:
    """Build a one-row Polars frame using RustyStats' required-column contract."""
    features = list(scoring_model.feature_names)
    missing = [feature for feature in features if feature not in input_row]
    if missing:
        raise ModelExplanationError(
            "Missing feature(s) required for RustyStats GLM explanation: " + ", ".join(missing)
        )
    return pl.DataFrame({feature: [input_row[feature]] for feature in features})


def _single_glm_contribution_record(records: Any) -> dict[str, Any]:
    if isinstance(records, pl.DataFrame):
        rows = records.to_dicts()
    elif isinstance(records, list):
        rows = records
    else:
        raise ModelExplanationError(
            "RustyStats GLM explanation returned an unsupported result type: "
            f"{type(records).__name__}."
        )
    if len(rows) != 1 or not isinstance(rows[0], dict):
        raise ModelExplanationError(
            f"RustyStats GLM explanation expected exactly one contribution record; got {len(rows)}."
        )
    return rows[0]


def _assert_finite_glm_record(record: dict[str, Any]) -> None:
    required_numeric_fields = (
        "base_value",
        "sum_contributions",
        "prediction_from_contributions",
        "prediction_value",
    )
    for field in required_numeric_fields:
        _as_float(record.get(field), field_name=field, strict=True)

    contributions = record.get("contributions")
    if not isinstance(contributions, list):
        raise ModelExplanationError(
            "RustyStats GLM explanation did not return a contributions list."
        )
    for index, contribution in enumerate(contributions):
        if not isinstance(contribution, dict):
            raise ModelExplanationError(
                f"RustyStats GLM explanation returned a non-object contribution at index {index}."
            )
        _as_float(
            contribution.get("contribution"),
            field_name=f"contributions[{index}].contribution",
            strict=True,
        )


def _rustystats_model_prediction(raw_model: Any, row_frame: pl.DataFrame) -> float:
    values = np.asarray(raw_model.predict(row_frame), dtype=float).reshape(-1)
    if values.size != 1:
        raise ModelExplanationError(
            "RustyStats GLM explanation for multi-output predictions is not supported yet."
        )
    if not np.isfinite(values[0]):
        raise ModelExplanationError("RustyStats GLM prediction returned a non-finite value.")
    return float(values[0])


def explain_rustystats_glm_prediction(
    scoring_model: Any,
    input_row: dict[str, Any],
    *,
    prediction_value: Any = None,
    max_contributions: int | None = None,
) -> dict[str, Any]:
    """Return RustyStats-native GLM contribution detail for one traced prediction."""
    if getattr(scoring_model, "flavor", "") != "rustystats":
        raise ModelExplanationError(
            "RustyStats GLM contribution explanation requires a RustyStats model."
        )

    raw_model = scoring_model.raw_model
    if not hasattr(raw_model, "predict_contributions"):
        raise ModelExplanationError(
            "Loaded RustyStats GLM model does not expose predict_contributions()."
        )

    features = list(scoring_model.feature_names)
    row_frame = _rustystats_frame_for_row(scoring_model, input_row)
    record = _single_glm_contribution_record(
        raw_model.predict_contributions(
            row_frame,
            group_terms=True,
            include_design_columns=False,
            return_format="records",
            validate=True,
        )
    )
    _assert_finite_glm_record(record)

    model_prediction = _rustystats_model_prediction(raw_model, row_frame)
    contribution_prediction = _as_float(
        record.get("prediction_value"),
        field_name="prediction_value",
        strict=True,
    )
    assert contribution_prediction is not None
    traced_prediction = _as_float(
        prediction_value,
        field_name="prediction_value",
        strict=prediction_value is not None,
    )
    effective_prediction = model_prediction if traced_prediction is None else traced_prediction

    tolerance = prediction_tolerance(contribution_prediction)
    model_difference = float(model_prediction - contribution_prediction)
    if abs(model_difference) > tolerance:
        raise ModelExplanationError(
            "RustyStats GLM explanation does not match the model prediction: "
            f"contributions reconstruct {contribution_prediction}, "
            f"model predicts {model_prediction}."
        )
    output_difference = (
        None if traced_prediction is None else float(traced_prediction - contribution_prediction)
    )
    if output_difference is not None and abs(output_difference) > tolerance:
        raise ModelExplanationError(
            "RustyStats GLM explanation does not match the traced prediction: "
            f"contributions reconstruct {contribution_prediction}, "
            f"traced output is {traced_prediction}."
        )

    ranked_contributions = []
    for index, contribution in enumerate(record["contributions"]):
        item = dict(contribution)
        value = _as_float(
            item.get("contribution"),
            field_name=f"contributions[{index}].contribution",
            strict=True,
        )
        assert value is not None
        item["contribution"] = value
        item["abs_contribution"] = float(abs(value))
        item["shap_value"] = value
        item["abs_shap_value"] = float(abs(value))
        item["_feature_index"] = index
        ranked_contributions.append(item)
    ranked_contributions.sort(
        key=lambda item: (-float(item["abs_contribution"]), int(item["_feature_index"]))
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

    output_space = str(record.get("output_space", "linear_predictor") or "linear_predictor")
    prediction_space = str(record.get("prediction_space", "response") or "response")
    base_value = _as_float(record.get("base_value"), field_name="base_value", strict=True)
    sum_contributions = _as_float(
        record.get("sum_contributions"),
        field_name="sum_contributions",
        strict=True,
    )
    prediction_from_contributions = _as_float(
        record.get("prediction_from_contributions"),
        field_name="prediction_from_contributions",
        strict=True,
    )
    assert base_value is not None
    assert sum_contributions is not None
    assert prediction_from_contributions is not None
    reconstructed_output = float(base_value + sum_contributions)
    output_tolerance = prediction_tolerance(prediction_from_contributions)
    if abs(reconstructed_output - prediction_from_contributions) > output_tolerance:
        raise ModelExplanationError(
            "RustyStats GLM explanation does not reconstruct the model output: "
            f"base + contributions is {reconstructed_output}, "
            f"reported output is {prediction_from_contributions}."
        )

    return {
        "type": "rustystats_glm_contributions",
        "method": "rustystats_glm_contributions",
        "status": "ok",
        "family": record.get("family"),
        "link": record.get("link"),
        "link_function": record.get("link"),
        "output_space": output_space,
        "prediction_space": prediction_space,
        "base_value": base_value,
        "sum_contributions": sum_contributions,
        "contribution_sum": sum_contributions,
        "prediction_from_contributions": prediction_from_contributions,
        "model_output_value": prediction_from_contributions,
        "model_prediction_value": model_prediction,
        "prediction_value": effective_prediction,
        "output_difference": output_difference,
        "feature_count": len(features),
        "feature_values": {feature: input_row.get(feature) for feature in features},
        "contributions": contributions,
        "truncated": truncated,
        "omitted_count": omitted_count,
    }


def _inverse_link(link: str, margin: float) -> float:
    if link == "log":
        return float(np.exp(margin))
    if link == "logit":
        return float(1.0 / (1.0 + np.exp(-margin)))
    return margin


def explain_xgboost_prediction(
    scoring_model: Any,
    input_row: dict[str, Any],
    *,
    task: str = "regression",
    prediction_value: Any = None,
    max_contributions: int | None = None,
) -> dict[str, Any]:
    """Native XGBoost contributions for one traced prediction.

    The bias carries the row's offset (``base_margin``), so bias plus
    contributions is the raw margin; its inverse link must reproduce the
    served response, or the positive-class probability for a classifier.
    """
    import polars as pl

    if getattr(scoring_model, "flavor", "") != "xgboost":
        raise ModelExplanationError("XGBoost explanation requires an XGBoost model.")
    model = scoring_model.raw_model
    features = list(model.features)
    columns = [*features, *([model.offset_column] if model.offset_column else [])]
    missing = [column for column in columns if column not in input_row]
    if missing:
        raise ModelExplanationError(
            f"XGBoost explanation input is missing columns: {', '.join(missing)}"
        )
    row = pl.DataFrame(
        {column: [input_row[column]] for column in columns},
        strict=False,
    )
    contributions = model.contributions(row)
    values = np.asarray(contributions.values[0], dtype=np.float64)
    bias = float(contributions.bias[0])
    if not np.isfinite(values).all() or not np.isfinite(bias):
        raise ModelExplanationError("XGBoost contributions are not finite.")
    margin = float(model.predict_margin(row)[0])
    reconstructed = float(bias + values.sum())
    tolerance = prediction_tolerance(margin, relative=FLOAT32_CONTRIBUTION_TOLERANCE)
    if abs(reconstructed - margin) > tolerance:
        raise ModelExplanationError(
            "XGBoost explanation does not match the model margin: "
            f"contributions reconstruct {reconstructed}, model margin is {margin}."
        )
    response = float(model.predict_response(row)[0])
    linked = _inverse_link(model.link, margin)
    if abs(linked - response) > prediction_tolerance(response):
        raise ModelExplanationError(
            "XGBoost explanation's inverse link does not reproduce the model response: "
            f"{linked} versus {response}."
        )
    traced = _as_float(
        prediction_value if task == "regression" else None,
        field_name="prediction_value",
        strict=task == "regression" and prediction_value is not None,
    )
    output_difference = None if traced is None else float(traced - response)
    if output_difference is not None and abs(output_difference) > prediction_tolerance(response):
        raise ModelExplanationError(
            "XGBoost explanation does not match the traced prediction: "
            f"model predicts {response}, traced output is {traced}."
        )

    categorical = frozenset(model.categorical_levels)
    ranked = [
        {
            "feature": feature,
            "feature_index": index,
            "feature_value": input_row.get(feature),
            "shap_value": float(value),
            "abs_shap_value": float(abs(value)),
            "is_categorical": feature in categorical,
            "_feature_index": index,
        }
        for index, (feature, value) in enumerate(zip(features, values, strict=True))
    ]
    ranked.sort(key=lambda item: (-float(item["abs_shap_value"]), int(item["_feature_index"])))
    truncated = max_contributions is not None and len(ranked) > max_contributions
    omitted_count = len(ranked) - max_contributions if truncated and max_contributions else 0
    if truncated and max_contributions is not None:
        ranked = ranked[:max_contributions]
    shown = []
    for rank, item in enumerate(ranked, start=1):
        item = dict(item)
        item.pop("_feature_index", None)
        item["rank"] = rank
        shown.append(item)

    output_space = {"identity": "response", "log": "log", "logit": "log_odds"}[model.link]
    return {
        "type": "xgboost_contributions",
        "method": "xgboost_contributions",
        "status": "ok",
        "link": model.link,
        "output_space": output_space,
        "prediction_space": "probability" if task == "classification" else "response",
        "base_value": bias,
        "sum_contributions": float(values.sum()),
        "contribution_sum": float(values.sum()),
        "prediction_from_contributions": reconstructed,
        "model_output_value": margin,
        "model_prediction_value": response,
        "prediction_value": prediction_value if prediction_value is not None else response,
        "output_difference": output_difference,
        "feature_count": len(features),
        "feature_values": {feature: input_row.get(feature) for feature in features},
        "contributions": shown,
        "truncated": truncated,
        "omitted_count": omitted_count,
    }


def _config_requests_supported_explanation(config: dict[str, Any]) -> bool:
    source_type = config.get("sourceType")
    if source_type not in {"run", "registered"}:
        return False
    artifact_path = str(config.get("artifact_path", ""))
    return artifact_path.endswith((".cbm", ".rsglm", ".ubj"))


def explanation_error_metadata_for_config(config: dict[str, Any]) -> dict[str, str]:
    """Return stable error metadata for the configured explanation method.

    Caller (``enrich_model_score``) only invokes this after
    :func:`_config_requests_supported_explanation` has returned True, so the
    artifact path is guaranteed to end in ``.rsglm`` or ``.cbm``.  We still
    enumerate both branches explicitly so adding a third supported flavour in
    future means extending this function alongside the loader.

    The function is on the *error-handling* path: it must always return a
    well-formed dict even when ``_config_requests_supported_explanation`` and
    this lookup disagree — otherwise an internal mismatch crashes the entire
    trace step through the outer ``except Exception`` in ``enrich_model_score``.
    Hit the unreachable branch with a ``logger.warning`` so a regression
    (e.g. a new flavour added to one half of the contract but not the other)
    is visible without poisoning the user's trace.
    """
    artifact_path = str(config.get("artifact_path", ""))
    if artifact_path.endswith(".rsglm"):
        method = "rustystats_glm_contributions"
    elif artifact_path.endswith(".cbm"):
        method = "catboost_shap"
    elif artifact_path.endswith(".ubj"):
        method = "xgboost_contributions"
    else:
        logger.warning(
            "explanation_error_metadata_unsupported_artifact",
            artifact_path=artifact_path,
        )
        method = "model_explanation"
    return {"type": method, "method": method}


def explain_model_score_from_config(
    config: dict[str, Any],
    input_row: dict[str, Any],
    output_row: dict[str, Any],
    *,
    prediction_column: str,
    prediction_value: Any,
) -> dict[str, Any] | None:
    """Load the configured model and return trace explanation detail."""
    if not _config_requests_supported_explanation(config):
        return None

    from haute._mlflow_io import load_mlflow_model

    scoring_model = load_mlflow_model(
        source_type=config.get("sourceType", "run"),
        run_id=config.get("run_id", ""),
        artifact_path=config.get("artifact_path", ""),
        registered_model=config.get("registered_model", ""),
        version=config.get("version", "latest"),
        alias=str(config.get("alias", "") or ""),
        task=config.get("task", "regression"),
        destination=str(config.get("mlflow_destination", "") or ""),
    )
    effective_prediction = (
        prediction_value if prediction_value is not None else output_row.get(prediction_column)
    )
    if getattr(scoring_model, "flavor", "") == "catboost":
        return explain_catboost_prediction(
            scoring_model,
            input_row,
            task=config.get("task", "regression"),
            prediction_value=effective_prediction,
        )
    if getattr(scoring_model, "flavor", "") == "xgboost":
        return explain_xgboost_prediction(
            scoring_model,
            input_row,
            task=config.get("task", "regression"),
            prediction_value=effective_prediction,
        )
    if getattr(scoring_model, "flavor", "") == "rustystats":
        return explain_rustystats_glm_prediction(
            scoring_model,
            input_row,
            prediction_value=effective_prediction,
        )
    return None
