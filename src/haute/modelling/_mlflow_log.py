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
    return f"file://{mlruns_dir}", "local"


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
        mlflow.log_params(enhanced_params)
        mlflow.log_metrics(metrics)

        # Log model file as artifact
        if model_path and Path(model_path).exists():
            mlflow.log_artifact(model_path)

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

        # Log CV results
        if diag.cv_results:
            _log_json_artifact(mlflow, diag.cv_results, "cv_results", "cv")
            for k, v in diag.cv_results.get("mean_metrics", {}).items():
                mlflow.log_metric(f"cv_mean_{k}", v)

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
                mlflow.log_metric(f"holdout_{k}", v)

        # Generate and log model card (best-effort — never fails the run)
        try:
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
            mlflow.register_model(
                f"runs:/{run.info.run_id}/{Path(model_path).name}",
                model_name,
            )

        run_id = run.info.run_id

    run_url = build_run_url(backend, experiment_name, run_id)

    logger.info("mlflow_logging_completed", run_id=run_id, backend=backend)
    return MLflowLogResult(
        backend=backend,
        experiment_name=experiment_name,
        run_id=run_id,
        tracking_uri=tracking_uri,
        run_url=run_url,
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
