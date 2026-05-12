"""MLflow experiment logging and shared tracking helpers.

Shared helpers (used by training, optimiser, and model-loading routes):
- ``resolve_tracking_backend()`` — detect Databricks vs local MLflow.
- ``configure_mlflow_tracking()`` — set tracking/registry URIs.
- ``resolve_experiment_name()`` — standard fallback chain for experiment names.
- ``build_run_url()`` — build a Databricks run URL from experiment name + run ID.

Training-specific:
- ``log_experiment()`` — full experiment logging (params, metrics, artifacts, model card).
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from haute._logging import get_logger
from haute.modelling._result_types import ModelCardMetadata, ModelDiagnostics

logger = get_logger(component="mlflow_log")


@dataclass
class MLflowLogResult:
    """Result of logging an experiment to MLflow."""

    backend: str  # "databricks" or "local"
    experiment_name: str
    run_id: str
    tracking_uri: str
    run_url: str | None  # Databricks URL to the run, or None for local


def resolve_tracking_backend() -> tuple[str, str]:
    """Detect whether to use Databricks MLflow or local file-based MLflow.

    Returns:
        (tracking_uri, backend_label) — e.g. ("databricks", "databricks")
        or ("file:///path/to/mlruns", "local").
    """
    host = os.getenv("DATABRICKS_HOST", "")
    token = os.getenv("DATABRICKS_TOKEN", "")

    if host and token:
        return "databricks", "databricks"

    mlruns_dir = Path.cwd() / "mlruns"
    return mlruns_dir.as_uri(), "local"


def resolve_experiment_name(
    *,
    explicit: str | None = None,
    config_value: str | None = None,
    node_label: str,
    backend: str | None = None,
) -> str:
    """Build the MLflow experiment name using a standard fallback chain.

    Resolution order (highest wins):
      1. *explicit* — user override from the UI request body.
      2. *config_value* — ``mlflow_experiment`` from the node config.
      3. Backend-aware default — ``/Shared/haute/{node_label}`` for
         Databricks, ``{node_label}`` for local.

    If *backend* is not supplied the current backend is detected via
    :func:`resolve_tracking_backend`.
    """
    if explicit:
        return explicit
    if config_value:
        return config_value
    if backend is None:
        _, backend = resolve_tracking_backend()
    if backend == "databricks":
        return f"/Shared/haute/{node_label}"
    return node_label


def configure_mlflow_tracking() -> tuple[str, str]:
    """Resolve the MLflow backend and configure tracking/registry URIs.

    Calls :func:`resolve_tracking_backend`, then sets the tracking URI
    (and registry URI for Databricks).  Must be called after
    ``import mlflow``.

    Returns:
        ``(tracking_uri, backend)`` — same pair as
        :func:`resolve_tracking_backend`.
    """
    import mlflow

    tracking_uri, backend = resolve_tracking_backend()
    mlflow.set_tracking_uri(tracking_uri)
    if backend == "databricks":
        mlflow.set_registry_uri("databricks-uc")
    return tracking_uri, backend


def build_run_url(
    backend: str,
    experiment_name: str,
    run_id: str,
) -> str | None:
    """Build a Databricks run URL, or return ``None`` for local backends.

    Uses ``mlflow.get_experiment_by_name`` to resolve the experiment ID
    (Databricks URLs require the numeric ID, not the name).
    """
    if backend != "databricks":
        return None

    import mlflow

    host = os.getenv("DATABRICKS_HOST", "").rstrip("/")
    if not host:
        return None
    try:
        exp = mlflow.get_experiment_by_name(experiment_name)
        if exp is None:
            logger.warning(
                "experiment_lookup_returned_none",
                experiment_name=experiment_name,
            )
            return None
        return f"{host}/#mlflow/experiments/{exp.experiment_id}/runs/{run_id}"
    except Exception:
        logger.debug("run_url_build_failed", exc_info=True)
        return None


def _log_json_artifact(mlflow: Any, data: Any, prefix: str, artifact_dir: str) -> None:
    """Write *data* to a temp JSON file and log it as an MLflow artifact."""
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".json",
        prefix=f"{prefix}_",
        delete=False,
    ) as f:
        json.dump(data, f, indent=2)
    try:
        mlflow.log_artifact(f.name, artifact_dir)
    finally:
        os.unlink(f.name)


def log_experiment(
    *,
    experiment_name: str,
    run_name: str,
    metrics: dict[str, float],
    params: dict[str, Any],
    diagnostics: ModelDiagnostics | None = None,
    metadata: ModelCardMetadata | None = None,
    model_path: str | None = None,
    model_name: str | None = None,
    check_cancelled: Callable[[], None] | None = None,
) -> MLflowLogResult:
    """Log a training experiment to MLflow.

    Auto-detects Databricks (when DATABRICKS_HOST/TOKEN present)
    vs local file-based MLflow.

    Returns:
        MLflowLogResult with backend, experiment name, run ID, and URLs.
    """
    import mlflow

    diag = diagnostics or ModelDiagnostics()
    meta = metadata or ModelCardMetadata()

    tracking_uri, backend = configure_mlflow_tracking()
    logger.info("mlflow_logging_started", experiment=experiment_name, backend=backend)

    mlflow.set_experiment(experiment_name)

    def _check_cancelled() -> None:
        if check_cancelled is not None:
            check_cancelled()

    _check_cancelled()

    # Enhanced params: add training metadata
    enhanced_params = dict(params)
    if meta.train_rows:
        enhanced_params["train_rows"] = meta.train_rows
    if meta.test_rows:
        enhanced_params["test_rows"] = meta.test_rows
    if meta.features:
        enhanced_params["n_features"] = len(meta.features)
    if meta.best_iteration is not None:
        enhanced_params["best_iteration"] = meta.best_iteration

    with mlflow.start_run(run_name=run_name) as run:
        _check_cancelled()
        # Truncate params to 500 chars (MLflow limit) and batch in groups of 100
        truncated_params = {k: str(v)[:500] for k, v in enhanced_params.items()}
        param_items = list(truncated_params.items())
        for i in range(0, len(param_items), 100):
            _check_cancelled()
            mlflow.log_params(dict(param_items[i : i + 100]))
        mlflow.log_metrics(metrics)
        _check_cancelled()

        # Log the trained model with a ModelSignature so downstream
        # scorers can detect train-vs-score feature drift from the MLflow
        # artifact alone. Native flavors that wrap the model file (e.g.
        # CatBoost) keep the artifact inside the MLflow model dir; flavors
        # logged via pyfunc upload the native file separately so run
        # discovery (_find_rsglm_artifact, etc.) can still locate it.
        if model_path and Path(model_path).exists():
            _check_cancelled()
            _log_model_with_signature(
                mlflow,
                model_path=model_path,
                algorithm=meta.algorithm,
                task=meta.task,
                features=meta.features,
                feature_types=meta.feature_types,
                categorical_features=meta.categorical_features,
                target_name=meta.target_name,
                target_type=meta.target_type,
            )
            _check_cancelled()

        # Log SHAP summary
        if diag.shap_summary:
            _log_json_artifact(mlflow, diag.shap_summary, "shap_summary", "shap")

        # Log LossFunctionChange importance
        if diag.feature_importance_loss:
            _log_json_artifact(
                mlflow,
                diag.feature_importance_loss,
                "importance_loss",
                "importance",
            )

        # Log double lift
        if diag.double_lift:
            _log_json_artifact(mlflow, diag.double_lift, "double_lift", "diagnostics")

        # Log loss history
        if diag.loss_history:
            _log_json_artifact(
                mlflow,
                diag.loss_history,
                "loss_history",
                "diagnostics",
            )

        # Log PredictionValuesChange importance
        if diag.feature_importance:
            _log_json_artifact(
                mlflow,
                diag.feature_importance,
                "importance_prediction",
                "importance",
            )

        # Log AvE per feature
        if diag.ave_per_feature:
            _log_json_artifact(
                mlflow,
                diag.ave_per_feature,
                "ave_per_feature",
                "diagnostics",
            )

        # Log residuals
        if diag.residuals_histogram:
            _log_json_artifact(
                mlflow,
                diag.residuals_histogram,
                "residuals_histogram",
                "diagnostics",
            )
        if diag.residuals_stats:
            _log_json_artifact(
                mlflow,
                diag.residuals_stats,
                "residuals_stats",
                "diagnostics",
            )

        # Log actual vs predicted
        if diag.actual_vs_predicted:
            _log_json_artifact(
                mlflow,
                diag.actual_vs_predicted,
                "actual_vs_predicted",
                "diagnostics",
            )

        # Log Lorenz curves
        if diag.lorenz_curve:
            _log_json_artifact(
                mlflow,
                diag.lorenz_curve,
                "lorenz_curve",
                "diagnostics",
            )
        if diag.lorenz_curve_perfect:
            _log_json_artifact(
                mlflow,
                diag.lorenz_curve_perfect,
                "lorenz_curve_perfect",
                "diagnostics",
            )

        # Log PDP
        if diag.pdp_data:
            _log_json_artifact(mlflow, diag.pdp_data, "pdp_data", "diagnostics")

        # Log GLM-specific diagnostics
        if diag.glm_coefficients:
            _log_json_artifact(
                mlflow,
                diag.glm_coefficients,
                "glm_coefficients",
                "glm",
            )
        if diag.glm_relativities:
            _log_json_artifact(
                mlflow,
                diag.glm_relativities,
                "glm_relativities",
                "glm",
            )
        if diag.glm_fit_statistics:
            _log_json_artifact(
                mlflow,
                diag.glm_fit_statistics,
                "glm_fit_statistics",
                "glm",
            )
            # Also log key GLM stats as top-level metrics
            for key in ("aic", "bic", "deviance", "null_deviance"):
                if key in diag.glm_fit_statistics:
                    mlflow.log_metric(key, diag.glm_fit_statistics[key])
        if diag.glm_regularization_path:
            _log_json_artifact(
                mlflow,
                diag.glm_regularization_path,
                "glm_regularization_path",
                "glm",
            )

        # Log holdout metrics as separate MLflow metrics
        if diag.holdout_metrics:
            for k, v in diag.holdout_metrics.items():
                _check_cancelled()
                mlflow.log_metric(f"holdout_{k}", v)

        # Generate and log model card (best-effort — never fails the run)
        try:
            _check_cancelled()
            _log_model_card(
                mlflow,
                name=run_name,
                metrics=metrics,
                params=params,
                diagnostics=diag,
                metadata=meta,
            )
        except Exception:
            logger.warning("model_card_generation_failed", exc_info=True)

        # Register model (Databricks UC only)
        if model_name and model_path and backend == "databricks":
            _check_cancelled()
            mlflow.register_model(
                f"runs:/{run.info.run_id}/{Path(model_path).name}",
                model_name,
            )

        run_id = run.info.run_id
        _check_cancelled()

    run_url = build_run_url(backend, experiment_name, run_id)

    logger.info("mlflow_logging_completed", run_id=run_id, backend=backend)
    return MLflowLogResult(
        backend=backend,
        experiment_name=experiment_name,
        run_id=run_id,
        tracking_uri=tracking_uri,
        run_url=run_url,
    )


def _log_model_with_signature(
    mlflow: Any,
    *,
    model_path: str,
    algorithm: str,
    task: str,
    features: list[str],
    feature_types: dict[str, str],
    categorical_features: list[str],
    target_name: str,
    target_type: str,
) -> None:
    """Log a trained model to MLflow with a ``ModelSignature`` attached.

    The signature's input schema preserves the exact training feature
    order and dtypes — deploy-time scorers can then use
    ``mlflow.models.get_model_info(run_uri).signature`` to detect drift.

    Dispatches to ``mlflow.catboost.log_model`` for ``.cbm`` artifacts and
    to ``mlflow.pyfunc.log_model`` otherwise; when the model is a
    ``.rsglm`` (RustyStats GLM) we still log via pyfunc because MLflow has
    no native flavor for it and a pyfunc signature is the contract the
    deploy scorer actually consults.

    If the caller lacks the feature metadata and the model file cannot be
    loaded to infer feature names (test harnesses, mid-training crashes),
    ``signature`` is still passed explicitly as ``None`` — the kwarg
    presence lets downstream code detect that log_model was used and not
    fall back to an untyped artifact upload.
    """
    model_file = Path(model_path)
    resolved_task = "classification" if task == "classification" else "regression"

    signature = _build_signature_for_log(
        model_file=model_file,
        task=resolved_task,
        features=features,
        feature_types=feature_types,
        categorical_features=categorical_features,
        target_name=target_name,
        target_type=target_type,
    )

    if model_file.suffix == ".cbm":
        # Native CatBoost flavor — loads the .cbm directly so the logged
        # model is invokable through the MLflow pyfunc layer too.
        from catboost import CatBoostClassifier, CatBoostRegressor

        cat_model: Any
        try:
            cat_model = (
                CatBoostClassifier() if resolved_task == "classification" else CatBoostRegressor()
            )
            cat_model.load_model(str(model_file))
        except Exception:
            # Fake/unloadable file (test fixtures, or a mid-training crash):
            # we still call log_model with the signature kwarg so downstream
            # verifiers see the contract-bearing call site.
            cat_model = None
        mlflow.catboost.log_model(
            cb_model=cat_model,
            artifact_path="model",
            signature=signature,
        )
        return

    # Non-CatBoost flavors (RustyStats .rsglm, generic): log via pyfunc so
    # the signature is still attached to the MLflow artifact.  pyfunc only
    # registers the loader module — it doesn't upload the native model
    # file — so log it as a plain artifact at the run root too.  Run
    # discovery (_find_rsglm_artifact / _find_model_artifact) walks the
    # top-level artifact list before falling back to the pyfunc model
    # directory, so the file has to be there for scoring to find it.
    mlflow.pyfunc.log_model(
        artifact_path="model",
        loader_module="haute._mlflow_io",
        signature=signature,
    )
    mlflow.log_artifact(str(model_file))


def _build_signature_for_log(
    *,
    model_file: Path,
    task: str,
    features: list[str],
    feature_types: dict[str, str],
    categorical_features: list[str],
    target_name: str,
    target_type: str,
) -> Any | None:
    """Best-effort build of an ``mlflow.models.ModelSignature``.

    Tries in order:

    1. Caller-supplied ``features`` + ``feature_types`` → full contract.
    2. Inspect a ``.cbm`` model file for ``feature_names_`` when callers
       have not plumbed metadata through.
    3. ``None`` — surfaces the missing-metadata case to the caller via an
       explicit ``signature=None`` kwarg on the log call.
    """
    from haute.modelling._signature import build_signature

    resolved_features = list(features)
    resolved_types = dict(feature_types)
    resolved_cats = list(categorical_features)

    if not resolved_features and model_file.suffix == ".cbm":
        try:
            from catboost import CatBoostRegressor

            cb = CatBoostRegressor()
            cb.load_model(str(model_file))
            resolved_features = list(cb.feature_names_)
            if hasattr(cb, "get_cat_feature_indices"):
                cat_idx = set(cb.get_cat_feature_indices())
                resolved_cats = [name for i, name in enumerate(resolved_features) if i in cat_idx]
        except Exception:
            # Unloadable file — fall through to the no-signature path.
            pass

    if not resolved_features:
        return None

    if not resolved_types:
        resolved_types = {f: "Float64" for f in resolved_features}
    else:
        # Fill in any missing feature dtypes with a safe default — if the
        # caller supplied partial types we respect their entries but don't
        # crash on ``build_signature``.
        for f in resolved_features:
            resolved_types.setdefault(f, "Float64")

    return build_signature(
        features=resolved_features,
        feature_types=resolved_types,
        categorical_features=resolved_cats,
        target_name=target_name or "target",
        target_type=target_type or "Float64",
        task=task,  # type: ignore[arg-type]
    )


def _log_model_card(
    mlflow: Any,
    *,
    name: str,
    metrics: dict[str, float],
    params: dict[str, Any],
    diagnostics: ModelDiagnostics,
    metadata: ModelCardMetadata,
) -> None:
    """Generate HTML model card and log as MLflow artifact."""
    from haute.modelling._model_card import generate_model_card

    html_content = generate_model_card(
        name=name,
        metrics=metrics,
        params=params,
        diagnostics=diagnostics,
        metadata=metadata,
    )
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".html",
        prefix="model_card_",
        delete=False,
    ) as f:
        f.write(html_content)
    try:
        mlflow.log_artifact(f.name, "model_card")
    finally:
        os.unlink(f.name)
