"""MLflow experiment logging and shared tracking helpers.

Shared helpers (used by training, optimiser, and model-loading routes):
- ``resolve_tracking_backend()`` — resolve a destination key ("" = local) to a tracking URI.
- ``configure_mlflow_tracking()`` — set tracking/registry URIs.
- ``resolve_experiment_name()`` — the requested experiment, else the backend default.
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
from typing import Any, Literal

from haute._logging import get_logger
from haute._mlflow_utils import (
    mlflow_fluent_operation,
    registry_uri_for_tracking,
    runtime_environment_inference,
    set_experiment_creating_workspace_folder,
    set_tracking_uri_preserving_env,
)
from haute.errors import HauteValidationError
from haute.modelling._candidate_run import CandidateRun
from haute.modelling._result_types import ModelCardMetadata, ModelDiagnostics

logger = get_logger(component="mlflow_log")


@dataclass
class MLflowLogResult:
    """Result of logging an experiment to MLflow."""

    backend: str  # "databricks", "server", or "local"
    experiment_name: str
    run_id: str
    tracking_uri: str
    run_url: str | None  # Databricks/server URL to the run, or None for local


def resolve_tracking_backend(destination: str = "") -> tuple[str, str]:
    """Resolve the tracking destination to ``(tracking_uri, backend)``.

    Thin wrapper over
    :func:`haute.modelling._mlflow_settings.resolve_destination`: an empty
    *destination* is the local folder, and Databricks or a server is used only
    when named. ``backend`` is ``"databricks"``, ``"server"``, or ``"local"``.

    Raises:
        MlflowConfigError: for an unknown or unconfigured destination, or an
            unsupported tracking-URI form — never a silent fallback.
    """
    from haute.modelling._mlflow_settings import node_destination_key, resolve_destination

    config = resolve_destination(node_destination_key(destination))
    return config.tracking_uri, config.mode


def resolve_experiment_name(
    *,
    explicit: str | None = None,
    node_label: str,
    backend: str | None = None,
    destination: str = "",
) -> str:
    """Return the MLflow experiment a log request targets.

    *explicit* is the experiment the request names — the node's current
    Export setting. When it is blank the backend-aware default applies:
    ``/Shared/haute/{node_label}`` for Databricks, the bare ``{node_label}``
    for server and local modes. A training-time snapshot of the setting is
    never consulted, so clearing the field logs to the default it shows.

    If *backend* is not supplied the current backend is detected via
    :func:`resolve_tracking_backend` with *destination*.
    """
    if explicit:
        return explicit
    if backend is None:
        _, backend = resolve_tracking_backend(destination)
    if backend == "databricks":
        return f"/Shared/haute/{node_label}"
    return node_label


def configure_mlflow_tracking(destination: str = "") -> tuple[str, str]:
    """Resolve the MLflow backend and configure tracking/registry URIs.

    Calls :func:`resolve_tracking_backend` with *destination*, then sets
    the tracking URI and matching registry URI while preserving the
    configured environment. Call inside :func:`mlflow_fluent_operation`
    so another writer cannot change the destination before the run finishes.

    Returns:
        ``(tracking_uri, backend)`` — same pair as
        :func:`resolve_tracking_backend`.
    """
    import mlflow

    tracking_uri, backend = resolve_tracking_backend(destination)
    if backend == "local":
        # mlflow 3.14 puts the local filesystem tracking backend into
        # "maintenance mode" and raises MlflowException at FileStore
        # construction unless MLFLOW_ALLOW_FILE_STORE=true. haute's local
        # workflow logs to ./mlruns, so opt in here. setdefault keeps a user
        # who set the variable explicitly in control.
        os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")
    set_tracking_uri_preserving_env(mlflow, tracking_uri)
    mlflow.set_registry_uri(registry_uri_for_tracking(tracking_uri))
    return tracking_uri, backend


def build_run_url(
    backend: str,
    experiment_name: str,
    run_id: str,
) -> str | None:
    """Build a Databricks/server run URL, or return ``None`` for local mode.

    Uses ``mlflow.get_experiment_by_name`` to resolve the experiment ID
    (run URLs require the numeric ID, not the name). Databricks URLs point
    at the workspace host; server URLs point at the configured tracking
    server's own UI.
    """
    if backend not in ("databricks", "server"):
        return None

    import mlflow

    if backend == "databricks":
        # The host MLflow's requests actually target: the MLflow pair or the
        # selected profile, never the data-access DATABRICKS_HOST.
        from mlflow.utils.databricks_utils import get_databricks_host_creds

        try:
            base = (get_databricks_host_creds(mlflow.get_tracking_uri()).host or "").rstrip("/")
        except Exception:
            logger.debug("run_url_host_unavailable", exc_info=True)
            return None
        path = "#mlflow/experiments"
    else:
        from haute.modelling._mlflow_settings import redact_uri

        # A credential-bearing env tracking URI must not leak into the
        # displayed run link.
        base = redact_uri(mlflow.get_tracking_uri()).rstrip("/")
        path = "#/experiments"
    if not base:
        return None
    try:
        exp = mlflow.get_experiment_by_name(experiment_name)
        if exp is None:
            logger.warning(
                "experiment_lookup_returned_none",
                experiment_name=experiment_name,
            )
            return None
        return f"{base}/{path}/{exp.experiment_id}/runs/{run_id}"
    except Exception:
        logger.debug("run_url_build_failed", exc_info=True)
        return None


_DIAGNOSTIC_ARTIFACTS: tuple[tuple[str, str, str], ...] = (
    ("shap_summary", "shap_summary", "shap"),
    ("feature_importance_loss", "importance_loss", "importance"),
    ("double_lift", "double_lift", "diagnostics"),
    ("loss_history", "loss_history", "diagnostics"),
    ("feature_importance", "importance_prediction", "importance"),
    ("ave_per_feature", "ave_per_feature", "diagnostics"),
    ("residuals_histogram", "residuals_histogram", "diagnostics"),
    ("residuals_stats", "residuals_stats", "diagnostics"),
    ("actual_vs_predicted", "actual_vs_predicted", "diagnostics"),
    ("lorenz_curve", "lorenz_curve", "diagnostics"),
    ("lorenz_curve_perfect", "lorenz_curve_perfect", "diagnostics"),
    ("pdp_data", "pdp_data", "diagnostics"),
    ("glm_coefficients", "glm_coefficients", "glm"),
    ("glm_relativities", "glm_relativities", "glm"),
    ("glm_fit_statistics", "glm_fit_statistics", "glm"),
    ("glm_inference", "glm_inference", "glm"),
    ("glm_smooth_terms", "glm_smooth_terms", "glm"),
    ("glm_regularization", "glm_regularization", "glm"),
)


@mlflow_fluent_operation()
def log_experiment(
    *,
    experiment_name: str,
    candidate: CandidateRun,
    destination: str = "",
    check_cancelled: Callable[[], None] | None = None,
) -> MLflowLogResult:
    """Log a candidate training run to MLflow.

    Every artifact is verified before any tracking call, so a run is only
    created for a complete candidate. Registration never happens here: a
    separate promotion process registers candidates.

    Returns:
        MLflowLogResult with backend, experiment name, run ID, and URLs.
    """
    import mlflow

    candidate.artifacts.require_files()

    def _check_cancelled() -> None:
        if check_cancelled is not None:
            check_cancelled()

    tracking_uri, backend = configure_mlflow_tracking(destination)
    logger.info("mlflow_logging_started", experiment=experiment_name, backend=backend)

    set_experiment_creating_workspace_folder(mlflow, experiment_name)
    _check_cancelled()

    diag = candidate.diagnostics
    with mlflow.start_run(run_name=candidate.run_name, tags=dict(candidate.tags)) as run:
        _check_cancelled()
        # Truncate params to 500 chars (MLflow limit) and batch in groups of 100
        truncated_params = {k: str(v)[:500] for k, v in candidate.params.items()}
        param_items = list(truncated_params.items())
        for i in range(0, len(param_items), 100):
            _check_cancelled()
            mlflow.log_params(dict(param_items[i : i + 100]))
        mlflow.log_metrics(dict(candidate.metrics))
        _check_cancelled()

        # The model carries a ModelSignature from the feature contract, so a
        # scorer can detect train-vs-score drift from the MLflow artifact alone.
        _log_model_with_signature(
            mlflow,
            model_path=candidate.artifacts.model,
            metadata=candidate.metadata,
        )
        mlflow.log_artifact(str(candidate.artifacts.feature_contract))
        _check_cancelled()

        for field_name, prefix, artifact_dir in _DIAGNOSTIC_ARTIFACTS:
            value = getattr(diag, field_name)
            if value:
                _log_json_artifact(mlflow, value, prefix, artifact_dir)

        for artifact_kind, path in candidate.artifacts.evidence.items():
            _check_cancelled()
            artifact_dir = "tuning" if artifact_kind.startswith("tuning_") else "evaluation"
            mlflow.log_artifact(str(path), artifact_dir)

        _check_cancelled()
        try:
            _log_model_card(
                mlflow,
                name=candidate.run_name,
                metrics=dict(candidate.metrics),
                params=dict(candidate.params),
                diagnostics=diag,
                metadata=candidate.metadata,
            )
        except Exception as exc:
            logger.warning("model_card_generation_failed", error_type=type(exc).__name__)
            mlflow.set_tag("haute.model_card", "unavailable")

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


def _log_json_artifact(mlflow: Any, data: Any, prefix: str, artifact_dir: str) -> None:
    """Write *data* to a temp JSON file and log it as an MLflow artifact."""
    with tempfile.NamedTemporaryFile(
        encoding="utf-8",
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


def _log_model_with_signature(
    mlflow: Any,
    *,
    model_path: Path,
    metadata: ModelCardMetadata,
) -> None:
    """Log a trained model to MLflow with a ``ModelSignature`` attached.

    The signature's input schema preserves the exact training feature order and
    dtypes from the feature contract. A ``.cbm`` model is logged through the
    native CatBoost flavor; a ``.rsglm`` model through a pyfunc whose loader
    scores with haute's own GLM path. The native file is also logged at the run
    root, where haute's run-artifact discovery finds it.
    """
    from haute.modelling._signature import build_signature

    task: Literal["classification", "regression"] = (
        "classification" if metadata.task == "classification" else "regression"
    )
    signature = build_signature(
        features=list(metadata.features),
        feature_types=dict(metadata.feature_types),
        categorical_features=list(metadata.categorical_features),
        target_name=metadata.target_name,
        target_type=metadata.target_type,
        task=task,
        offset_name=metadata.offset_name or None,
        offset_type=metadata.offset_type or "Float64",
    )

    if model_path.suffix == ".cbm":
        from catboost import CatBoostClassifier, CatBoostRegressor

        cat_model: CatBoostClassifier | CatBoostRegressor = (
            CatBoostClassifier() if task == "classification" else CatBoostRegressor()
        )
        try:
            cat_model.load_model(str(model_path))
        except Exception as exc:
            raise HauteValidationError(
                "The trained CatBoost model file could not be loaded "
                f"({type(exc).__name__}); retrain the model."
            ) from exc
        # ``name`` is MLflow 3's spelling (``artifact_path`` is deprecated);
        # ``runs:/<run>/model`` still resolves the logged model. The
        # environment scope makes the recorded requirements describe this
        # interpreter, not a uv.lock in the working directory.
        with runtime_environment_inference():
            mlflow.catboost.log_model(
                cb_model=cat_model,
                name="model",
                signature=signature,
            )
    elif model_path.suffix == ".rsglm":
        with runtime_environment_inference():
            mlflow.pyfunc.log_model(
                name="model",
                loader_module="haute.modelling._glm_pyfunc",
                data_path=str(model_path),
                signature=signature,
            )
    else:
        raise HauteValidationError(
            f"Cannot log a {model_path.suffix or 'suffix-less'} model file to MLflow; "
            "expected a CatBoost .cbm or RustyStats .rsglm model."
        )
    # mlflow 3.x stores logged models as LoggedModel entities outside the run's
    # artifact listing, so haute's run-artifact discovery needs the native file
    # at the run root too.
    mlflow.log_artifact(str(model_path))


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
        encoding="utf-8",
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
