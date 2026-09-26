"""Core TrainingJob class — orchestrates the full training pipeline."""

from __future__ import annotations

import copy
import gc
import hashlib
import os
import tempfile
from collections.abc import Callable, Iterable, Mapping
from contextlib import AbstractContextManager, closing, nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np
import polars as pl

from haute._execution_context import ExecutionCancelledError, ExecutionContext
from haute._logging import get_logger
from haute._polars_dtypes import contract_dtype_name
from haute._polars_utils import bounded_collect_batches, streaming_collect
from haute.errors import HauteError, HauteValidationError
from haute.modelling._algorithms import (
    ALGORITHM_REGISTRY,
    IterationCallback,
    _malloc_trim,
    resolve_loss_function,
)
from haute.modelling._descriptors import (
    algorithm_descriptor,
    project_refit_params,
    round_ceiling,
    training_threads,
)
from haute.modelling._evaluation import (
    EvaluationConfig,
    EvaluationFitResult,
    EvaluationPlan,
    EvaluationResultsArtifact,
    aggregate_evaluation_results,
    generate_evaluation_plan,
    load_evaluation_plan,
    load_evaluation_report,
    load_evaluation_results,
    save_evaluation_plan,
    save_evaluation_report,
    save_evaluation_results,
)
from haute.modelling._evaluation import file_sha256 as evaluation_file_sha256
from haute.modelling._feature_contract import ModelIdentity
from haute.modelling._metrics import compute_metrics
from haute.modelling._model_export import MODEL_FILE_SUFFIXES
from haute.modelling._split import (
    PARTITION_HOLDOUT,
    PARTITION_TRAIN,
    PARTITION_VALIDATION,
    SplitConfig,
    split_mask,
)
from haute.modelling._train_config import (
    default_metrics,
    glm_effective_link,
    glm_params_issue,
    validate_glm_params,
)
from haute.modelling._tuning import (
    TUNING_SCHEMA_VERSION,
    TuningConfig,
    TuningPlanArtifact,
    TuningTrialResult,
    TuningTrialsArtifact,
    build_tuning_report,
    canonical_json_bytes,
    choose_winner,
    resolve_trial_parameters,
    save_tuning_plan,
    save_tuning_report,
    save_tuning_trials,
    suggest_parameters,
    tuning_final_projection,
    validation_weighted_tree_count,
)

logger = get_logger(component="training_job")

# The failure classes a target/metric/dtype mismatch produces inside pure
# metric computation. The metric-stage wrap deliberately trades
# terminal-reason precision for context: any of these surfaces as a
# ValueError (mapped to `contract_error`) naming the target, task, and
# metrics, with the original chained — even when the underlying cause was an
# internal bug. MemoryError is intentionally NOT included: it must keep its
# `memory_limited` taxonomy.
_METRIC_STAGE_FAILURE_TYPES: tuple[type[Exception], ...] = (
    ValueError,
    TypeError,
    ArithmeticError,
)

# Internal partition names → the labels the evaluation-plan pipeline reports
# publicly. The legacy constructor-only pipeline reports internal names as-is.
_PUBLIC_EVALUATION_SET_LABELS = {"holdout": "final test", "train": "development"}


def model_contract_filename(model_name: str) -> str:
    """Per-model feature-contract filename (remediation 4b.9).

    Contracts are written next to the model file as
    ``{model_name}.feature_contract.json`` so several models sharing one
    ``output_dir`` (the UI trains everything into ``outputs/`` by default)
    keep distinct contracts instead of overwriting one shared
    ``feature_contract.json``.  Every contract consumer takes an explicit
    path (``feature_contract_path`` config, ``load_contract``), so this
    writer-side scheme is the single source of naming truth for training
    outputs.
    """
    from haute.modelling._feature_contract import CONTRACT_FILENAME

    return f"{model_name}.{CONTRACT_FILENAME}"


def evaluation_artifact_filenames(model_name: str) -> dict[str, str]:
    """Return the canonical names of the evaluation publication artifacts."""
    return {
        "plan": f"{model_name}.evaluation-plan.json",
        "results": f"{model_name}.evaluation-results.json",
        "report": f"{model_name}.evaluation-report.json",
    }


def tuning_artifact_filenames(model_name: str) -> dict[str, str]:
    """Return the canonical names of the tuning publication artifacts."""
    return {
        "plan": f"{model_name}.tuning-plan.json",
        "trials": f"{model_name}.tuning-trials.json",
        "report": f"{model_name}.tuning-report.json",
    }


def _remove_temp_parquet(path: str | None, *, context: str) -> bool:
    """Unlink a run-owned temp parquet; loud on failure, silent if absent.

    Returns ``True`` when a file was actually removed.  Removal failures
    are logged as warnings instead of raising so cleanup running inside
    exception handling can never mask the in-flight error.
    """
    if not path:
        return False
    try:
        os.unlink(path)
        return True
    except FileNotFoundError:
        return False
    except OSError as cleanup_exc:
        logger.warning(
            "training_temp_parquet_cleanup_failed",
            path=path,
            context=context,
            error=str(cleanup_exc),
            exc_info=True,
        )
        return False


def _remove_training_artifact(path: Path) -> None:
    """Unlink a run-owned evaluation JSON artifact; loud on failure, silent if absent.

    Removal failures are logged instead of raising so cleanup running inside
    exception handling can never mask the in-flight error.
    """
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as cleanup_exc:
        logger.warning(
            "training_artifact_cleanup_failed",
            path=str(path),
            error=str(cleanup_exc),
            exc_info=True,
        )


# Mapping used by the feature-contract artifact and the MLflow signature.
# Polars dtype repr -> canonical contract dtype name.  Uncovered dtypes
# fall back to the str(dtype) form; the signature builder will raise if
# the resulting string isn't a supported MLflow type so bugs surface
# loudly rather than being coerced into a wrong type silently.
_POLARS_DTYPE_CANONICAL: dict[Any, str] = {}


def _training_checkpoint(
    execution_context: ExecutionContext | None,
    *,
    label: str,
) -> None:
    if execution_context is not None:
        execution_context.checkpoint(label=label)


def _training_stage(
    execution_context: ExecutionContext | None,
    name: str,
) -> AbstractContextManager[None]:
    return execution_context.stage(name) if execution_context is not None else nullcontext()


def _training_streaming_collect(
    lf: pl.LazyFrame,
    *,
    stage_name: str,
    execution_context: ExecutionContext | None = None,
) -> pl.DataFrame:
    _training_checkpoint(
        execution_context,
        label=f"before_{stage_name}",
    )
    with _training_stage(execution_context, stage_name):
        df = streaming_collect(lf, execution_context=execution_context)
    _training_checkpoint(
        execution_context,
        label=f"after_{stage_name}",
    )
    return df


def _record_diag_error(
    errors: list[dict[str, str]],
    diagnostic: str,
    exc: BaseException,
) -> None:
    """Record an optional-diagnostic failure without aborting training.

    Writes a structured entry into ``errors`` so the caller (and eventually
    the UI) sees that the named diagnostic was skipped and why — replaces
    the old warning-only swallow that silently degraded runs.
    """
    entry = {
        "diagnostic": diagnostic,
        "error": str(exc),
        "error_type": type(exc).__name__,
    }
    errors.append(entry)
    logger.warning(
        "diagnostic_skipped",
        diagnostic=diagnostic,
        error=str(exc),
        error_type=type(exc).__name__,
    )


@dataclass
class TrainResult:
    """Result of a training job."""

    metrics: dict[str, float]
    feature_importance: list[dict[str, Any]]
    model_path: str
    train_rows: int
    validation_rows: int
    features: list[str]
    cat_features: list[str]
    holdout_rows: int = 0
    holdout_metrics: dict[str, float] = field(default_factory=dict)
    diagnostics_set: str = "validation"  # "train" | "validation" | "holdout"
    best_iteration: int | None = None
    loss_history: list[dict[str, float]] = field(default_factory=list)
    #: The holdout validation fit's history when the final model is its refit.
    validation_loss_history: list[dict[str, float]] = field(default_factory=list)
    double_lift: list[dict[str, Any]] = field(default_factory=list)
    shap_summary: list[dict[str, Any]] = field(default_factory=list)
    feature_importance_loss: list[dict[str, Any]] = field(default_factory=list)
    ave_per_feature: list[dict[str, Any]] = field(default_factory=list)
    residuals_histogram: list[dict[str, Any]] = field(default_factory=list)
    residuals_stats: dict[str, float] = field(default_factory=dict)
    actual_vs_predicted: list[dict[str, float]] = field(default_factory=list)
    lorenz_curve: list[dict[str, float]] = field(default_factory=list)
    lorenz_curve_perfect: list[dict[str, float]] = field(default_factory=list)
    pdp_data: list[dict[str, Any]] = field(default_factory=list)
    # GLM-specific (empty for CatBoost)
    glm_coefficients: list[dict[str, Any]] = field(default_factory=list)
    glm_relativities: list[dict[str, Any]] = field(default_factory=list)
    glm_fit_statistics: dict[str, float] = field(default_factory=dict)
    glm_inference: dict[str, Any] | None = None
    glm_smooth_terms: list[dict[str, Any]] = field(default_factory=list)
    glm_regularization: dict[str, Any] | None = None
    #: EBM shape functions and pairwise surfaces (empty for other families).
    ebm_terms: list[dict[str, Any]] = field(default_factory=list)
    # Optional-diagnostic failures surfaced to callers so a degraded
    # run (SHAP/PDP/GLM diagnostics missing) is visible in the UI and
    # in test suites, instead of being silently swallowed.
    diagnostics_errors: list[dict[str, str]] = field(default_factory=list)
    development_rows: int = 0
    final_test_rows: int = 0
    final_test_metrics: dict[str, float] = field(default_factory=dict)
    evaluation: dict[str, Any] | None = None
    tuning: dict[str, Any] | None = None
    final_tree_count: int | None = None
    #: The final fit's threads, round ceiling, fitted rounds, and stopping reason.
    fit_evidence: dict[str, Any] | None = None


@dataclass(frozen=True)
class SelectionFit:
    """One selection fit: its persisted result and its loss history, which is not."""

    result: EvaluationFitResult
    loss_history: list[dict[str, float]]


@dataclass
class _PreparedData:
    """Intermediate result from the data-preparation phase."""

    data_path: str
    owns_tmp: bool
    features: list[str]
    cat_features: list[str]
    total_rows: int
    # Feature dtype snapshot captured at data-prep time, used to build
    # the MLflow signature and the feature contract artifact.
    feature_dtypes: dict[str, str] = field(default_factory=dict)
    categorical_levels: dict[str, list[str | None]] = field(default_factory=dict)
    target_dtype: str = ""
    target_null_count: int = 0
    offset_dtype: str = ""


@dataclass
class _SplitResult:
    """Intermediate result from the train/validation/holdout split phase."""

    split_path: str
    owns_tmp: bool
    n_train: int
    n_validation: int
    n_holdout: int


@dataclass
class _TrainModelResult:
    """Intermediate result from the model-fitting phase."""

    model: Any
    algo: Any
    fit_result: Any
    fit_params: dict[str, Any]


@dataclass
class _MetricsResult:
    """Intermediate result from the metrics/evaluation phase."""

    metrics: dict[str, float]
    holdout_metrics: dict[str, float]
    diagnostics_set: str  # "train" | "validation" | "holdout"
    importance: list[dict[str, Any]]
    double_lift: list[dict[str, Any]]
    shap_summary: list[dict[str, float]]
    feature_importance_loss: list[dict[str, Any]]
    ave_per_feature: list[dict[str, Any]]
    residuals_histogram: list[dict[str, Any]]
    residuals_stats: dict[str, float]
    actual_vs_predicted: list[dict[str, float]]
    lorenz_curve: list[dict[str, float]]
    lorenz_curve_perfect: list[dict[str, float]]
    pdp_data: list[dict[str, Any]]
    # GLM-specific
    glm_coefficients: list[dict[str, Any]] = field(default_factory=list)
    glm_relativities: list[dict[str, Any]] = field(default_factory=list)
    glm_fit_statistics: dict[str, float] = field(default_factory=dict)
    glm_inference: dict[str, Any] | None = None
    glm_smooth_terms: list[dict[str, Any]] = field(default_factory=list)
    glm_regularization: dict[str, Any] | None = None
    ebm_terms: list[dict[str, Any]] = field(default_factory=list)
    # Optional-diagnostic failures (SHAP, PDP, GLM diagnostics) —
    # surfaced rather than silently swallowed.
    diagnostics_errors: list[dict[str, str]] = field(default_factory=list)


class TrainingJob:
    """Orchestrate evaluation, optional tuning, final fitting, and MLflow logging.

    Canonical modelling-node jobs use one versioned ``evaluation`` contract
    and may add a bounded ``tuning`` contract. ``split`` is a constructor-only
    internal seam for direct test callers exercising the shared partition and
    fit machinery; node config parsing rejects it.

    Parameters
    ----------
    name : str
        Name for the model / training run.
    data : str | pl.DataFrame
        Path to parquet file, or a DataFrame directly.
    target : str
        Target column name.
    weight : str | None
        Optional weight/exposure column name.
    exclude : list[str] | None
        Columns to exclude from features. Everything not in
        {target, weight, *exclude} is automatically a feature.
    algorithm : str
        Algorithm key from ALGORITHM_REGISTRY (default: "catboost").
    task : str
        "regression" or "classification".
    params : dict | None
        Algorithm-specific hyperparameters.
    split : dict | SplitConfig | None
        Internal test-seam partition configuration for direct callers.
    metrics : list[str] | None
        Metrics to compute. When omitted, the default list is derived from
        the training objective (loss function / GLM family) so a Poisson or
        Tweedie model is not silently reported with squared-error metrics.
    evaluation : dict | EvaluationConfig | None
        Canonical development/validation/final-test plan configuration.
    tuning : dict | TuningConfig | None
        Optional bounded, seeded search on the evaluation validation fits.
    mlflow_experiment : str | None
        MLflow experiment path. If set and mlflow is importable, logs the run.
    mlflow_destination : str
        MLflow tracking destination key ("databricks" | "server"; "" = the local folder).
    output_dir : str
        Directory to save the model file.
    """

    def __init__(
        self,
        *,
        name: str,
        data: str | pl.DataFrame | pl.LazyFrame,
        target: str,
        weight: str | None = None,
        exclude: list[str] | None = None,
        feature_columns: list[str] | None = None,
        fold_column: str | None = None,
        id_columns: list[str] | None = None,
        algorithm: str = "catboost",
        task: str = "regression",
        params: dict[str, Any] | None = None,
        split: dict[str, Any] | SplitConfig | None = None,
        metrics: list[str] | None = None,
        mlflow_experiment: str | None = None,
        mlflow_destination: str = "",
        output_dir: str = "outputs",
        loss_function: str | None = None,
        variance_power: float | None = None,
        offset: str | None = None,
        monotone_constraints: dict[str, int] | None = None,
        feature_weights: dict[str, float] | None = None,
        categorical_levels: Mapping[str, Iterable[str | None]] | None = None,
        evaluation: Mapping[str, Any] | EvaluationConfig | None = None,
        tuning: Mapping[str, Any] | TuningConfig | None = None,
        refit_on_development: bool = True,
        evaluation_plan: EvaluationPlan | None = None,
        fit_index: int | None = None,
        plan_source_sha256: str | None = None,
        positive_class: bool | int | str | None = None,
        device: str = "cpu",
    ) -> None:
        self.name = name
        self._data: str | pl.DataFrame | pl.LazyFrame | None = data
        self.target = target
        self.weight = weight
        self.exclude = exclude or []
        self.feature_columns = list(feature_columns or [])
        self.fold_column = fold_column
        self.id_columns = list(id_columns or [])
        self.algorithm = algorithm
        self.task = task
        self.params = params or {}
        self.metrics = metrics or default_metrics(
            task,
            loss_function=loss_function,
            family=self.params.get("family") if algorithm == "glm" else None,
        )
        self.mlflow_experiment = mlflow_experiment
        self.mlflow_destination = mlflow_destination
        self.output_dir = output_dir
        self.loss_function = loss_function
        self.variance_power = variance_power
        if positive_class is not None and (
            isinstance(positive_class, float) or not isinstance(positive_class, (bool, int, str))
        ):
            raise HauteValidationError(
                "positive_class must be a Boolean, an integer or a string label."
            )
        self.positive_class = positive_class
        if device not in ("cpu", "gpu"):
            raise HauteValidationError(f'device must be "cpu" or "gpu", got {device!r}')
        if device == "gpu" and not algorithm_descriptor(algorithm).gpu_device:
            raise HauteValidationError(
                f"{algorithm_descriptor(algorithm).label} trains on CPU only in Haute."
            )
        #: ``gpu`` asks a GPU-capable family to train on its device (XGBoost CUDA).
        self.device = device
        #: ``(negative, positive)`` once a classification split resolves them.
        self._class_labels: tuple[bool | int | str, bool | int | str] | None = None
        self.offset = offset
        self.monotone_constraints = monotone_constraints
        self.feature_weights = feature_weights
        if self.algorithm == "glm":
            catboost_levers = [
                name
                for name, value in (
                    ("exclude", self.exclude),
                    ("feature_columns", self.feature_columns),
                    ("monotone_constraints", monotone_constraints),
                    ("feature_weights", feature_weights),
                )
                if value
            ]
            if catboost_levers:
                raise HauteValidationError(
                    f"{', '.join(catboost_levers)} only apply to CatBoost. A GLM's features are "
                    "its terms and interaction factors, and GLM monotonicity lives on each "
                    "term's 'monotonicity' key."
                )
            glm_issue = glm_params_issue(self.params)
            if glm_issue is not None:
                raise HauteValidationError(glm_issue)
            validate_glm_params(self.params)
        else:
            descriptor = algorithm_descriptor(self.algorithm)
            descriptor.validate_params(self.params)
            config_issue = descriptor.config_issue(
                self.params, self.loss_function, monotone_constraints
            )
            if config_issue is not None:
                raise HauteValidationError(config_issue)
        #: One thread allotment per job, passed to every engine that takes one.
        self.threads = training_threads()
        if split is not None and evaluation is not None:
            raise HauteValidationError("split and evaluation are competing contracts")
        self.evaluation: EvaluationConfig | None
        if evaluation is None:
            self.evaluation = None
        elif isinstance(evaluation, EvaluationConfig):
            self.evaluation = evaluation
        else:
            self.evaluation = EvaluationConfig.from_plain_data(evaluation)
        if not isinstance(refit_on_development, bool):
            raise HauteValidationError("refit_on_development must be a boolean")
        if not refit_on_development and (
            self.evaluation is None or self.evaluation.validation["method"] != "single"
        ):
            raise HauteValidationError("Skipping the final refit requires holdout validation")
        self.refit_on_development = refit_on_development
        if mlflow_experiment and self.evaluation is None:
            raise HauteValidationError(
                "mlflow_experiment requires an explicit evaluation contract: MLflow "
                "candidate runs publish the evaluation plan and results"
            )
        if tuning is None:
            self.tuning = None
        elif self.evaluation is None:
            raise HauteValidationError("tuning requires an explicit evaluation contract")
        elif isinstance(tuning, TuningConfig):
            self.tuning = tuning
        else:
            self.tuning = TuningConfig.from_plain_data(
                tuning,
                algorithm=self.algorithm,
                base_params=self.params,
                evaluation=self.evaluation,
                configured_metrics=self.metrics,
            )
        if self.tuning is not None and not self.refit_on_development:
            raise HauteValidationError("Parameter tuning requires a final refit")
        if evaluation_plan is not None and self.evaluation is None:
            raise HauteValidationError("evaluation_plan requires an explicit evaluation contract")
        if evaluation_plan is None and fit_index is not None:
            raise HauteValidationError("fit_index requires an evaluation_plan")
        if (
            evaluation_plan is not None
            and fit_index is not None
            and not (0 <= fit_index < len(evaluation_plan.validation_fits))
        ):
            raise HauteValidationError("fit_index is outside evaluation plan")
        self.evaluation_plan = evaluation_plan
        self.evaluation_fit_index = fit_index
        if plan_source_sha256 is not None and evaluation_plan is None:
            raise HauteValidationError("plan_source_sha256 requires an evaluation_plan")
        self._plan_source_sha256 = plan_source_sha256
        evaluation_key = None
        if self.evaluation is not None:
            evaluation_key = (
                self.evaluation.group_column
                if self.evaluation.strategy == "group"
                else (
                    self.evaluation.date_column if self.evaluation.strategy == "temporal" else None
                )
            )
        if evaluation_key:
            if evaluation_key in self.feature_columns:
                raise HauteValidationError("evaluation key cannot be an explicit feature column")
            if self.algorithm == "glm":
                from haute.modelling._glm_terms import glm_model_columns

                glm_columns = glm_model_columns(
                    self.params.get("terms") or {},
                    self.params.get("interactions") or [],
                )
                if evaluation_key in glm_columns:
                    raise HauteValidationError("evaluation key cannot be a GLM term")
            if evaluation_key not in self.id_columns:
                self.id_columns.append(evaluation_key)
        from haute.modelling._feature_contract import normalise_categorical_levels

        self._declared_categorical_levels = normalise_categorical_levels(
            categorical_levels,
        )

        # Parse split config
        if isinstance(split, SplitConfig):
            self.split_config = split
        elif isinstance(split, dict):
            self.split_config = SplitConfig(**split)
        else:
            self.split_config = SplitConfig()

        # Contract snapshot — populated during ``_prepare_data`` so that
        # ``_log_to_mlflow`` and the feature-contract artifact writer can
        # reach the exact dtypes the trainer saw.
        self._contract_feature_dtypes: dict[str, str] = {}
        self._contract_categorical_levels: dict[str, list[str | None]] = {}
        self._contract_target_dtype: str = ""
        self._contract_offset_dtype: str = ""

    def run(
        self,
        progress: Callable[[str, float], None] | None = None,
        on_iteration: IterationCallback | None = None,
        check_cancelled: Callable[[], None] | None = None,
        execution_context: ExecutionContext | None = None,
        on_tuning_progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> TrainResult:
        """Execute the full training pipeline.

        Parameters
        ----------
        progress : callable | None
            Optional callback ``(message, fraction)`` for progress reporting.
        on_iteration : callable | None
            Optional callback ``(iteration, total, metrics_dict, history_row)``
            called after each training iteration.

        Returns
        -------
        TrainResult
            Metrics, feature importances, model path, and split sizes.
        """

        if self.evaluation_plan is None and self.evaluation is not None:
            return self._run_evaluation(
                progress=progress,
                on_iteration=on_iteration,
                check_cancelled=check_cancelled,
                execution_context=execution_context,
                on_tuning_progress=on_tuning_progress,
            )

        def _report(msg: str, frac: float) -> None:
            if check_cancelled is not None:
                check_cancelled()
            if progress:
                progress(msg, frac)
            if check_cancelled is not None:
                check_cancelled()

        def _checkpoint() -> None:
            if check_cancelled is not None:
                check_cancelled()
            _training_checkpoint(
                execution_context,
                label="training_job_checkpoint",
            )

        from haute.modelling._algorithms import _mem_checkpoint

        _mem_checkpoint("training run START")

        prepared: _PreparedData | None = None
        split_result: _SplitResult | None = None
        try:
            _checkpoint()
            _report("Loading data", 0.0)
            prepared = self._prepare_data(_report, execution_context=execution_context)
            _checkpoint()
            # Cache the dtype snapshot so downstream artifact writers
            # (MLflow signature, feature contract) see the same types the
            # trainer saw — not whatever the post-fit DataFrame reports.
            self._contract_feature_dtypes = dict(prepared.feature_dtypes)
            self._contract_categorical_levels = dict(prepared.categorical_levels)
            self._contract_target_dtype = prepared.target_dtype
            self._contract_offset_dtype = prepared.offset_dtype

            prepared = self._prepare_fit_features(prepared, _report)

            _report("Splitting data", 0.15)
            split_result = self._split_data(
                prepared,
                _report,
                execution_context=execution_context,
            )
            _checkpoint()

            _report("Training model", 0.2)
            train_result = self._train_model(
                split_result,
                prepared.features,
                prepared.cat_features,
                on_iteration,
                _report,
                execution_context=execution_context,
            )
            _checkpoint()

            _report("Evaluating model", 0.7)
            metrics_result = self._compute_metrics(
                split_result,
                prepared.features,
                prepared.cat_features,
                train_result,
                _report,
                execution_context=execution_context,
            )
            _checkpoint()

            _report("Saving model", 0.9)
            with _training_stage(execution_context, "training_artifact_save"):
                model_path = self._save_artifacts(
                    train_result,
                    features=prepared.features,
                    cat_features=prepared.cat_features,
                    categorical_levels=(
                        train_result.fit_result.categorical_levels
                        if getattr(train_result.fit_result, "categorical_levels", None) is not None
                        else prepared.categorical_levels
                    ),
                )

            result = TrainResult(
                metrics=metrics_result.metrics,
                feature_importance=metrics_result.importance,
                model_path=str(model_path),
                train_rows=split_result.n_train,
                validation_rows=split_result.n_validation,
                holdout_rows=split_result.n_holdout,
                holdout_metrics=metrics_result.holdout_metrics,
                diagnostics_set=metrics_result.diagnostics_set,
                features=prepared.features,
                cat_features=prepared.cat_features,
                best_iteration=train_result.fit_result.best_iteration,
                loss_history=train_result.fit_result.loss_history,
                double_lift=metrics_result.double_lift,
                shap_summary=metrics_result.shap_summary,
                feature_importance_loss=metrics_result.feature_importance_loss,
                ave_per_feature=metrics_result.ave_per_feature,
                residuals_histogram=metrics_result.residuals_histogram,
                residuals_stats=metrics_result.residuals_stats,
                actual_vs_predicted=metrics_result.actual_vs_predicted,
                lorenz_curve=metrics_result.lorenz_curve,
                lorenz_curve_perfect=metrics_result.lorenz_curve_perfect,
                pdp_data=metrics_result.pdp_data,
                glm_coefficients=metrics_result.glm_coefficients,
                glm_relativities=metrics_result.glm_relativities,
                glm_fit_statistics=metrics_result.glm_fit_statistics,
                glm_inference=metrics_result.glm_inference,
                glm_smooth_terms=metrics_result.glm_smooth_terms,
                glm_regularization=metrics_result.glm_regularization,
                ebm_terms=metrics_result.ebm_terms,
                diagnostics_errors=metrics_result.diagnostics_errors,
            )
            fit_result = train_result.fit_result
            result.fit_evidence = {
                "threads": getattr(fit_result, "threads", None) or self.threads,
                "rounds_configured": fit_result.rounds_configured,
                "rounds_fitted": fit_result.rounds_fitted,
                "stopping_reason": fit_result.stopping_reason,
            }
            trained_device = getattr(fit_result, "device", None)
            if trained_device is not None:
                result.fit_evidence["device"] = trained_device
            term_update_steps = getattr(fit_result, "term_update_steps", None)
            if term_update_steps is not None:
                result.fit_evidence["term_update_steps"] = list(term_update_steps)
            if (
                algorithm_descriptor(self.algorithm).refit_policy == "validation_weighted_rounds"
                and isinstance(fit_result.rounds_fitted, int)
                and fit_result.rounds_fitted > 0
            ):
                result.final_tree_count = fit_result.rounds_fitted

            # An internal final evaluation fit must attach the persisted
            # evaluation/tuning report before the one MLflow handoff.
            if self.mlflow_experiment and self.evaluation_plan is None:
                _checkpoint()
                with _training_stage(execution_context, "training_mlflow_log"):
                    self._log_to_mlflow(result, check_cancelled=_checkpoint)

            _report("Done", 1.0)
            _checkpoint()
            return result
        finally:
            # Remediation 4b.6 — abort safety net.  The success path
            # deletes both run-owned temp parquets at their natural
            # hand-off points (_split_data removes the prepared input once
            # the split file exists; _compute_metrics removes the split
            # file after the final partition read), so this is a no-op on
            # success.  On failure or cancellation anywhere in between it
            # stops multi-GB parquets leaking into the OS temp dir.
            self._cleanup_owned_temp_parquets(prepared, split_result)

    def _cleanup_owned_temp_parquets(
        self,
        prepared: _PreparedData | None,
        split_result: _SplitResult | None,
    ) -> None:
        """Remove run-owned temp parquets that survived an aborted run.

        Only files the run itself created (``owns_tmp=True``) are
        candidates — caller-supplied parquet inputs are never touched.
        Files already consumed by the normal pipeline flow are silently
        skipped, so on a successful run this is a no-op.
        """
        candidates: list[str] = []
        if prepared is not None and prepared.owns_tmp:
            candidates.append(prepared.data_path)
        if split_result is not None and split_result.owns_tmp:
            candidates.append(split_result.split_path)
        for path in candidates:
            if _remove_temp_parquet(path, context="training_run_abort"):
                logger.warning("training_temp_parquet_removed_after_abort", path=path)

    def _build_evaluation_plan(
        self,
        prepared: _PreparedData,
        *,
        execution_context: ExecutionContext | None = None,
    ) -> EvaluationPlan:
        """Build the canonical plan for the already-clean eligible source."""
        assert self.evaluation is not None
        values: dict[str, list[object]] = {}
        columns: list[str] = []
        if self.evaluation.strategy == "random" and self.task == "classification":
            columns.append(self.target)
        elif self.evaluation.strategy == "group":
            assert self.evaluation.group_column is not None
            columns.append(self.evaluation.group_column)
        elif self.evaluation.strategy == "temporal":
            assert self.evaluation.date_column is not None
            columns.append(self.evaluation.date_column)
        if columns:
            frame = _training_streaming_collect(
                pl.scan_parquet(prepared.data_path).select(columns),
                stage_name="evaluation_plan_key_collect",
                execution_context=execution_context,
            )
            values = {column: frame[column].to_list() for column in columns}
        return generate_evaluation_plan(
            self.evaluation,
            source_sha256=evaluation_file_sha256(prepared.data_path),
            row_count=prepared.total_rows,
            task=self.task,
            target_values=values.get(self.target),
            group_values=values.get(self.evaluation.group_column or ""),
            date_values=values.get(self.evaluation.date_column or ""),
        )

    def _new_evaluation_job(
        self,
        *,
        name: str,
        data: str,
        output_dir: str,
        plan: EvaluationPlan,
        fit_index: int | None,
        mlflow_experiment: str | None,
        params: Mapping[str, Any] | None = None,
        source_sha256: str | None = None,
    ) -> TrainingJob:
        """Clone this job for one evaluation selection or deployable final fit."""
        return TrainingJob(
            name=name,
            data=data,
            target=self.target,
            weight=self.weight,
            exclude=list(self.exclude),
            feature_columns=list(self.feature_columns),
            fold_column=self.fold_column,
            id_columns=list(self.id_columns),
            algorithm=self.algorithm,
            task=self.task,
            params=copy.deepcopy(dict(self.params if params is None else params)),
            metrics=list(self.metrics),
            mlflow_experiment=mlflow_experiment,
            mlflow_destination=self.mlflow_destination,
            output_dir=output_dir,
            loss_function=self.loss_function,
            variance_power=self.variance_power,
            offset=self.offset,
            monotone_constraints=self.monotone_constraints,
            feature_weights=self.feature_weights,
            categorical_levels=self._declared_categorical_levels,
            evaluation=self.evaluation,
            tuning=self.tuning,
            refit_on_development=self.refit_on_development,
            evaluation_plan=plan,
            fit_index=fit_index,
            plan_source_sha256=source_sha256,
            positive_class=self.positive_class,
            device=self.device,
        )

    def _prepare_fit_features(
        self,
        prepared: _PreparedData,
        report: Callable[[str, float], None],
    ) -> _PreparedData:
        """Apply the same final feature contract to selection and final fits."""
        if self.algorithm == "glm":
            from haute.modelling._glm_terms import validate_glm_model_columns

            model_columns = validate_glm_model_columns(
                self.params["terms"],
                self.params.get("interactions") or [],
                prepared.feature_dtypes,
                role_columns=self._role_columns(),
            )
            keep = set(model_columns)
            prepared = _PreparedData(
                data_path=prepared.data_path,
                owns_tmp=prepared.owns_tmp,
                features=[feature for feature in prepared.features if feature in keep],
                cat_features=[feature for feature in prepared.cat_features if feature in keep],
                total_rows=prepared.total_rows,
                feature_dtypes={
                    feature: dtype
                    for feature, dtype in prepared.feature_dtypes.items()
                    if feature in keep
                },
                categorical_levels={
                    feature: levels
                    for feature, levels in prepared.categorical_levels.items()
                    if feature in keep
                },
                target_dtype=prepared.target_dtype,
                target_null_count=prepared.target_null_count,
                offset_dtype=prepared.offset_dtype,
            )
            report(
                f"GLM: using {len(prepared.features)} model columns "
                f"({len(prepared.cat_features)} categorical)",
                0.12,
            )
        self._validate_monotone_constraints(prepared)
        return prepared

    def _role_columns(self) -> dict[str, str]:
        """Columns with a modelling role, which can never be GLM model columns."""
        roles: dict[str, str] = {}

        def add(column: str | None, role: str) -> None:
            if column:
                roles.setdefault(column, role)

        add(self.target, "target")
        add(self.weight, "weight")
        add(self.offset, "offset")
        add(self.fold_column, "fold")
        for column in self.id_columns:
            add(column, "identifier")
        if self.evaluation is not None:
            if self.evaluation.strategy == "group":
                add(self.evaluation.group_column, "evaluation")
            elif self.evaluation.strategy == "temporal":
                add(self.evaluation.date_column, "evaluation")
        return roles

    def _catboost_loss_function(self) -> str | None:
        """The CatBoost ``loss_function`` the fit uses.

        The job's loss setting wins over a ``loss_function`` inside ``params``,
        exactly as the fit params are built, so the offset baseline and the
        link stamped on the model always describe the same loss.
        """
        resolved = resolve_loss_function(self.loss_function, self.task, self.variance_power)
        if resolved:
            return resolved
        in_params = self.params.get("loss_function")
        return str(in_params) if in_params else None

    def _resolve_class_labels(
        self,
        split_lf: pl.LazyFrame,
        *,
        execution_context: ExecutionContext | None,
    ) -> tuple[bool | int | str, bool | int | str]:
        """``(negative, positive)`` for a binary target, by the shared class rule.

        Boolean and 0/1 targets make ``True``/``1`` positive; any other pair of
        labels needs an explicit ``positive_class``.
        """
        dtype = split_lf.collect_schema()[self.target]
        values = (
            _training_streaming_collect(
                split_lf.select(pl.col(self.target).unique()).head(3),
                stage_name="training_class_labels_collect",
                execution_context=execution_context,
            )
            .get_column(self.target)
            .to_list()
        )
        if len(values) != 2:
            shown = ", ".join(repr(v) for v in sorted(values, key=repr))
            raise HauteValidationError(
                f"Binary classification needs exactly two classes in '{self.target}', "
                f"found {'more than two' if len(values) > 2 else len(values)}"
                f"{f' ({shown})' if values and len(values) <= 2 else ''}. Multiclass targets "
                "are not supported."
            )
        labels: list[bool | int | str] = []
        for value in values:
            if isinstance(value, float):
                if not value.is_integer():
                    raise HauteValidationError(
                        f"Classification target '{self.target}' has non-integer labels; "
                        "cast it to integer or string labels upstream."
                    )
                value = int(value)
            if not isinstance(value, (bool, int, str)):
                raise HauteValidationError(
                    f"Classification target '{self.target}' must hold Boolean, integer or "
                    "string labels."
                )
            labels.append(value)
        if dtype == pl.Boolean or sorted(labels) in ([0, 1], [False, True]):
            positive: bool | int | str = True if dtype == pl.Boolean else 1
            if self.positive_class is not None and self.positive_class != positive:
                raise HauteValidationError(
                    f"'{self.target}' is a Boolean/0-1 target, whose positive class is "
                    f"{positive!r}; remove positive_class or recode the target upstream."
                )
        else:
            if self.positive_class is None:
                raise HauteValidationError(
                    f"'{self.target}' has the labels {labels[0]!r} and {labels[1]!r}; choose "
                    "which one is the positive class."
                )
            if self.positive_class not in labels:
                raise HauteValidationError(
                    f"positive_class {self.positive_class!r} is not one of the labels in "
                    f"'{self.target}': {labels[0]!r}, {labels[1]!r}."
                )
            positive = next(label for label in labels if label == self.positive_class)
        negative = next(label for label in labels if label != positive)
        return negative, positive

    def _model_identity(self) -> ModelIdentity:
        """The feature contract's record of which model this job trains."""
        from importlib import import_module

        from haute import __version__ as haute_version

        descriptor = algorithm_descriptor(self.algorithm)
        task = "classification" if self.task == "classification" else "regression"
        loss: str | None = None
        glm_family: str | None = None
        variance_power: float | None = None
        if descriptor.key == "glm":
            glm_family = str(self.params["family"])
            link = glm_effective_link(self.params)
            if glm_family == "tweedie" and self.params.get("var_power") is not None:
                variance_power = float(self.params["var_power"])
        else:
            loss = self._haute_loss()
            if loss is not None:
                link = descriptor.native_loss(task, loss).link
            else:
                link = "logit" if task == "classification" else "identity"
            if loss == "Tweedie" and self.variance_power is not None:
                variance_power = float(self.variance_power)
        return ModelIdentity(
            algorithm=descriptor.key,
            link=link,
            engine_name=descriptor.engine_module,
            engine_version=str(import_module(descriptor.engine_module).__version__),
            haute_version=haute_version,
            loss=loss,
            glm_family=glm_family,
            variance_power=variance_power,
            class_labels=self._class_labels if task == "classification" else None,
        )

    def _haute_loss(self) -> str | None:
        """The Haute loss name this job trains (``None`` for the GLM)."""
        if self.algorithm == "glm":
            return None
        if self.algorithm == "catboost":
            resolved = self._catboost_loss_function()
            return resolved.partition(":")[0] if resolved else None
        return self.loss_function

    def _offset_link(self) -> str:
        """How the offset enters the model: ``log`` multiplies, ``identity`` adds."""
        if self.algorithm == "glm":
            return glm_effective_link(self.params)
        if self.algorithm == "catboost":
            from haute.modelling._algorithms import catboost_offset_link

            return catboost_offset_link(self._catboost_loss_function())
        loss = self._haute_loss()
        task = "classification" if self.task == "classification" else "regression"
        link = algorithm_descriptor(self.algorithm).native_loss(task, str(loss)).link
        return "log" if link == "log" else "identity"

    def _require_positive_log_link_offset(
        self,
        data_path: str,
        *,
        execution_context: ExecutionContext | None,
    ) -> None:
        """Refuse null, zero, or negative offsets before fitting under a log link."""
        if not self.offset or self._offset_link() != "log":
            return
        offset = pl.col(self.offset).cast(pl.Float64)
        invalid = _training_streaming_collect(
            pl.scan_parquet(data_path)
            .filter(pl.col(self.target).is_not_null())
            .select((offset.is_null() | offset.is_nan() | (offset <= 0)).sum()),
            stage_name="training_offset_check",
            execution_context=execution_context,
        ).item()
        if invalid:
            raise HauteValidationError(
                f"Offset column {self.offset!r} must be positive under a log link, but "
                f"{int(invalid):,} training rows are null, zero, or negative. The offset "
                "multiplies the prediction (an exposure), so fix or remove those rows upstream."
            )

    def run_evaluation_fit(
        self,
        progress: Callable[[str, float], None] | None = None,
        check_cancelled: Callable[[], None] | None = None,
        execution_context: ExecutionContext | None = None,
    ) -> SelectionFit:
        """Fit one selection partition without publishing model or diagnostics."""
        if self.evaluation_plan is None or self.evaluation_fit_index is None:
            raise HauteValidationError(
                "run_evaluation_fit requires an internal evaluation selection job"
            )
        prepared: _PreparedData | None = None
        split_result: _SplitResult | None = None

        def report(message: str, fraction: float) -> None:
            if check_cancelled is not None:
                check_cancelled()
            if progress is not None:
                progress(message, fraction)

        try:
            prepared = self._prepare_data(report, execution_context=execution_context)
            prepared = self._prepare_fit_features(prepared, report)
            split_result = self._split_data(prepared, report, execution_context=execution_context)

            def selection_iteration(
                iteration: int,
                total: int,
                _metrics: dict[str, float],
                _history_row: dict[str, float] | None,
            ) -> None:
                if check_cancelled is not None:
                    check_cancelled()
                _training_checkpoint(
                    execution_context,
                    label="evaluation_selection_iteration",
                )
                if total > 0:
                    report(
                        f"Iteration {iteration} of {total}",
                        0.3 + 0.5 * min(iteration / total, 1.0),
                    )

            trained = self._train_model(
                split_result,
                prepared.features,
                prepared.cat_features,
                (
                    selection_iteration
                    if progress is not None
                    or check_cancelled is not None
                    or execution_context is not None
                    else None
                ),
                report,
                execution_context=execution_context,
            )
            report("Evaluating validation predictions", 0.85)
            validation = self._read_partition(
                split_result.split_path,
                PARTITION_VALIDATION,
                columns=self._glm_select_columns(prepared.features),
                execution_context=execution_context,
                stage_name="evaluation_selection_metrics_materialise",
            )
            predictions = trained.algo.predict(
                trained.model,
                validation,
                prepared.features,
                offset=self.offset,
            )
            try:
                metrics = compute_metrics(
                    validation[self.target].to_numpy(),
                    predictions,
                    validation[self.weight].to_numpy() if self.weight else None,
                    self.metrics,
                    variance_power=self.variance_power,
                )
            except _METRIC_STAGE_FAILURE_TYPES as exc:
                raise self._metric_stage_error(
                    exc,
                    evaluation_set=f"validation fit {self.evaluation_fit_index}",
                ) from exc
            result = EvaluationFitResult(
                1,
                self.evaluation_fit_index,
                split_result.n_train,
                split_result.n_validation,
                metrics,
                trained.fit_result.best_iteration,
            )
            report("Validation complete", 1.0)
            return SelectionFit(result, trained.fit_result.loss_history)
        finally:
            self._cleanup_owned_temp_parquets(prepared, split_result)

    def _run_tuning_trials(
        self,
        *,
        plan: EvaluationPlan,
        plan_digest: str,
        source_sha256: str,
        prepared: _PreparedData,
        output: Path,
        fit_output_dir: str,
        created: list[Path],
        report: Callable[[str, float], None],
        checkpoint: Callable[[str], None],
        check_cancelled: Callable[[], None] | None,
        execution_context: ExecutionContext | None,
        on_tuning_progress: Callable[[dict[str, Any]], None] | None,
    ) -> tuple[
        tuple[EvaluationFitResult, ...],
        dict[str, Any],
        dict[str, Any],
    ]:
        """Run seeded sequential ask/tell trials on the persisted plan."""
        assert self.tuning is not None
        import optuna

        config = self.tuning
        names = tuning_artifact_filenames(self.name)
        plan_path = output / names["plan"]
        trials_path = output / names["trials"]
        tuning_report_path = output / names["report"]
        if any(path.exists() for path in (plan_path, trials_path, tuning_report_path)):
            raise HauteValidationError("tuning artifact paths already exist")

        tuning_plan = TuningPlanArtifact.create(
            config=config,
            base_params=self.params,
            evaluation_plan_sha256=plan_digest,
            sampler="TPESampler",
            sampler_version=optuna.__version__,
        )
        save_tuning_plan(tuning_plan, plan_path)
        created.append(plan_path)
        tuning_plan_digest = evaluation_file_sha256(plan_path)

        sampler = optuna.samplers.TPESampler(seed=config.seed)
        study = optuna.create_study(direction=config.direction, sampler=sampler)
        trials: list[TuningTrialResult] = []
        completed_fits = 0

        def emit_progress(
            *,
            phase: str,
            trial_index: int | None,
            fold_index: int | None,
            best_objective: float | None,
        ) -> None:
            if on_tuning_progress is None:
                return
            payload: dict[str, Any] = {
                "phase": phase,
                "trial_index": (None if trial_index is None else trial_index + 1),
                "trial_count": config.trial_count,
                "fold_index": None if fold_index is None else fold_index + 1,
                "fold_count": config.validation_fit_count,
                "completed_fits": completed_fits,
                "total_fits": config.total_fit_count,
                "best_objective": best_objective,
            }
            on_tuning_progress(payload)

        emit_progress(
            phase="planning",
            trial_index=None,
            fold_index=None,
            best_objective=None,
        )
        for trial_index in range(config.trial_count):
            checkpoint(f"before_tuning_trial_{trial_index}")
            optuna_trial = None
            label: Literal["baseline", "sampled"]
            if trial_index == 0:
                sampled_params: dict[str, Any] = {}
                label = "baseline"
            else:
                optuna_trial = study.ask()
                sampled_params = suggest_parameters(
                    optuna_trial,
                    config,
                    self.params,
                )
                label = "sampled"
            resolved_params = resolve_trial_parameters(
                self.params,
                sampled_params,
            )
            fit_results: list[EvaluationFitResult] = []
            try:
                for fit_index in range(config.validation_fit_count):
                    report(
                        (
                            f"Tuning: trial {trial_index + 1}/"
                            f"{config.trial_count}, fold {fit_index + 1}/"
                            f"{config.validation_fit_count}"
                        ),
                        completed_fits / config.total_fit_count,
                    )
                    best_so_far = (
                        None
                        if not trials
                        else choose_winner(
                            trials,
                            direction=config.direction,
                        ).objective
                    )
                    emit_progress(
                        phase="trial_fit",
                        trial_index=trial_index,
                        fold_index=fit_index,
                        best_objective=best_so_far,
                    )
                    child = self._new_evaluation_job(
                        name=(f"{self.name}.tuning-{trial_index}-evaluation-{fit_index}"),
                        data=prepared.data_path,
                        output_dir=fit_output_dir,
                        plan=plan,
                        fit_index=fit_index,
                        mlflow_experiment=None,
                        params=resolved_params,
                        source_sha256=source_sha256,
                    )
                    fit_results.append(
                        child.run_evaluation_fit(
                            check_cancelled=check_cancelled,
                            execution_context=execution_context,
                        ).result
                    )
                    completed_fits += 1
                    checkpoint(f"after_tuning_trial_{trial_index}_fit_{fit_index}")
            except (ExecutionCancelledError, MemoryError, HauteError):
                raise
            except Exception as exc:
                raise RuntimeError(
                    f"Tuning trial {trial_index} failed for sampled parameters "
                    f"{sampled_params!r}: {exc}"
                ) from exc

            transient_results = EvaluationResultsArtifact(
                TUNING_SCHEMA_VERSION,
                plan_digest,
                tuple(fit_results),
            )
            transient_digest = hashlib.sha256(
                canonical_json_bytes(transient_results.to_plain_data())
            ).hexdigest()
            aggregate = aggregate_evaluation_results(
                plan,
                transient_results,
                self.metrics,
                results_sha256=transient_digest,
            )
            aggregate_metrics = {
                metric: float(summary["mean"]) for metric, summary in aggregate.metrics.items()
            }
            objective = aggregate_metrics[config.metric]
            completed_trial = TuningTrialResult(
                schema_version=TUNING_SCHEMA_VERSION,
                trial_index=trial_index,
                label=label,
                sampled_params=sampled_params,
                resolved_params=resolved_params,
                fits=tuple(fit_results),
                aggregate_metrics=aggregate_metrics,
                objective=objective,
                # Runtime duration is deliberately not part of canonical
                # reproducibility. Live status owns elapsed time.
                elapsed_seconds=0.0,
            )
            trials.append(completed_trial)
            if optuna_trial is not None:
                study.tell(optuna_trial, objective)
            emit_progress(
                phase="trial_complete",
                trial_index=trial_index,
                fold_index=None,
                best_objective=choose_winner(
                    trials,
                    direction=config.direction,
                ).objective,
            )

        trials_artifact = TuningTrialsArtifact(
            schema_version=TUNING_SCHEMA_VERSION,
            plan_sha256=tuning_plan_digest,
            evaluation_plan_sha256=plan_digest,
            trials=tuple(trials),
        )
        save_tuning_trials(trials_artifact, trials_path)
        created.append(trials_path)
        trials_digest = evaluation_file_sha256(trials_path)
        winner = choose_winner(trials, direction=config.direction)
        descriptor = algorithm_descriptor(self.algorithm)
        iteration_ceiling = round_ceiling(descriptor, self.params, 1000)
        if (
            isinstance(iteration_ceiling, bool)
            or not isinstance(iteration_ceiling, int)
            or iteration_ceiling <= 0
        ):
            raise HauteValidationError(
                f"Fixed {descriptor.label} {descriptor.round_key} must be a positive exact "
                "integer when tuning is enabled"
            )
        final_params, final_tree_count = tuning_final_projection(
            descriptor,
            winner.resolved_params,
            [(fit.best_iteration, fit.validation_rows) for fit in winner.fits],
        )
        tuning_report = build_tuning_report(
            tuning_plan,
            trials_artifact,
            trials_sha256=trials_digest,
            final_params=final_params,
            final_tree_count=final_tree_count,
        )
        save_tuning_report(tuning_report, tuning_report_path)
        created.append(tuning_report_path)
        response = {
            **tuning_report.to_plain_data(),
            "trials": [trial.to_plain_data() for trial in trials_artifact.trials],
            "plan_path": str(plan_path),
            "trials_path": str(trials_path),
            "report_path": str(tuning_report_path),
        }
        return winner.fits, final_params, response

    def _run_evaluation(
        self,
        *,
        progress: Callable[[str, float], None] | None,
        on_iteration: IterationCallback | None,
        check_cancelled: Callable[[], None] | None,
        execution_context: ExecutionContext | None,
        on_tuning_progress: Callable[[dict[str, Any]], None] | None,
    ) -> TrainResult:
        """Orchestrate persisted selection evaluation followed by one final fit."""
        assert self.evaluation is not None
        prepared: _PreparedData | None = None
        created: list[Path] = []
        floor = 0.0

        def checkpoint(label: str) -> None:
            if check_cancelled is not None:
                check_cancelled()
            _training_checkpoint(execution_context, label=label)

        def report(message: str, fraction: float) -> None:
            nonlocal floor
            checkpoint("before_evaluation_progress")
            floor = max(floor, fraction)
            if progress is not None:
                progress(message, floor)
            checkpoint("after_evaluation_progress")

        try:
            report("Evaluation: preparing source", 0.0)
            prepared = self._prepare_data(report, execution_context=execution_context)
            # Caller-owned parquets retain the fused null-target filter until
            # planning needs stable eligible positions. Run-owned prepared
            # parquets are already physically filtered and must not be copied
            # again (or orphaned when ``prepared`` is replaced).
            if prepared.target_null_count and not prepared.owns_tmp:
                clean = tempfile.NamedTemporaryFile(
                    suffix=".parquet", prefix="haute_evaluation_clean_", delete=False
                )
                clean.close()
                try:
                    from haute._polars_utils import bounded_sink

                    bounded_sink(
                        pl.scan_parquet(prepared.data_path).filter(
                            pl.col(self.target).is_not_null()
                        ),
                        clean.name,
                        fast_checkpoint=True,
                    )
                except BaseException:
                    _remove_temp_parquet(clean.name, context="evaluation_clean_source")
                    raise
                prepared = _PreparedData(
                    clean.name,
                    True,
                    prepared.features,
                    prepared.cat_features,
                    prepared.total_rows,
                    prepared.feature_dtypes,
                    prepared.categorical_levels,
                    prepared.target_dtype,
                    0,
                    prepared.offset_dtype,
                )
            generated_plan = self._build_evaluation_plan(
                prepared,
                execution_context=execution_context,
            )
            output = Path(self.output_dir).resolve()
            output.mkdir(parents=True, exist_ok=True)
            names = evaluation_artifact_filenames(self.name)
            plan_path, results_path, report_path = (
                output / names[key] for key in ("plan", "results", "report")
            )
            if any(path.exists() for path in (plan_path, results_path, report_path)):
                raise HauteValidationError("evaluation artifact paths already exist")
            save_evaluation_plan(generated_plan, plan_path)
            created.append(plan_path)
            source_digest = evaluation_file_sha256(prepared.data_path)
            plan = load_evaluation_plan(plan_path, source_sha256=source_digest)
            if plan.to_plain_data() != generated_plan.to_plain_data():
                raise HauteValidationError("reloaded evaluation plan differs from generated plan")
            plan_digest = evaluation_file_sha256(plan_path)
            selection_fit_count = len(plan.validation_fits)
            tuning_response: dict[str, Any] | None = None
            # Refit selection fits' histories; tuning trials keep none.
            selection_histories: list[list[dict[str, float]]] = []
            if self.tuning is not None:
                with tempfile.TemporaryDirectory(prefix="haute_tuning_fits_") as tuning_fit_root:
                    fits, final_params, tuning_response = self._run_tuning_trials(
                        plan=plan,
                        plan_digest=plan_digest,
                        source_sha256=source_digest,
                        prepared=prepared,
                        output=output,
                        fit_output_dir=tuning_fit_root,
                        created=created,
                        report=report,
                        checkpoint=checkpoint,
                        check_cancelled=check_cancelled,
                        execution_context=execution_context,
                        on_tuning_progress=on_tuning_progress,
                    )
                total = self.tuning.total_fit_count
                completed_before_final = self.tuning.trial_fit_count
            else:
                descriptor = algorithm_descriptor(self.algorithm)
                ordinary_fits: list[EvaluationFitResult] = []
                selected_result: TrainResult | None = None
                total = selection_fit_count + int(self.refit_on_development)
                completed_before_final = selection_fit_count
                with tempfile.TemporaryDirectory(prefix="haute_evaluation_fits_") as root:
                    for fit_index in range(selection_fit_count):
                        report(
                            f"Fit {fit_index + 1} of {total} (validation)",
                            fit_index / total,
                        )
                        child = self._new_evaluation_job(
                            name=(
                                f"{self.name}.evaluation-{fit_index}"
                                if self.refit_on_development
                                else self.name
                            ),
                            data=prepared.data_path,
                            output_dir=root if self.refit_on_development else self.output_dir,
                            plan=plan,
                            fit_index=fit_index,
                            mlflow_experiment=None,
                            params=self.params,
                            source_sha256=source_digest,
                        )

                        def fit_progress(
                            message: str, fraction: float, *, fit_index: int = fit_index
                        ) -> None:
                            report(
                                f"Fit {fit_index + 1} of {total} (validation): {message}",
                                (fit_index + fraction) / total,
                            )

                        if self.refit_on_development:
                            selection = child.run_evaluation_fit(
                                progress=fit_progress,
                                check_cancelled=check_cancelled,
                                execution_context=execution_context,
                            )
                            ordinary_fits.append(selection.result)
                            selection_histories.append(selection.loss_history)
                        else:
                            selected_result = child.run(
                                progress=fit_progress,
                                on_iteration=on_iteration,
                                check_cancelled=check_cancelled,
                                execution_context=execution_context,
                            )
                            if (
                                descriptor.refit_policy == "validation_weighted_rounds"
                                and selected_result.final_tree_count is None
                            ):
                                raise HauteValidationError(
                                    f"{descriptor.label} validation fit did not report its "
                                    "trained round count"
                                )
                            ordinary_fits.append(
                                EvaluationFitResult(
                                    1,
                                    fit_index,
                                    selected_result.train_rows,
                                    selected_result.validation_rows,
                                    selected_result.metrics,
                                    selected_result.best_iteration,
                                )
                            )
                fits = tuple(ordinary_fits)
                final_params = copy.deepcopy(self.params)
                if (
                    descriptor.refit_policy == "validation_weighted_rounds"
                    and fits
                    and self.refit_on_development
                ):
                    if any(fit.best_iteration is None for fit in fits):
                        raise HauteValidationError(
                            f"{descriptor.label} validation fits did not report best_iteration"
                        )
                    final_tree_count = validation_weighted_tree_count(
                        best_iterations=[
                            fit.best_iteration for fit in fits if fit.best_iteration is not None
                        ],
                        validation_rows=[fit.validation_rows for fit in fits],
                        iteration_ceiling=round_ceiling(descriptor, self.params, 1000),
                    )
                    final_params = project_refit_params(descriptor, final_params, final_tree_count)
            artifact = EvaluationResultsArtifact(1, plan_digest, tuple(fits))
            save_evaluation_results(artifact, results_path)
            created.append(results_path)
            results = load_evaluation_results(results_path, plan_sha256=plan_digest)
            results_digest = evaluation_file_sha256(results_path)
            aggregate = aggregate_evaluation_results(
                plan,
                results,
                self.metrics,
                results_sha256=results_digest,
                refit_on_development=self.refit_on_development,
            )
            save_evaluation_report(aggregate, report_path)
            created.append(report_path)
            aggregate = load_evaluation_report(report_path)
            if self.refit_on_development:
                final_source_digest = evaluation_file_sha256(prepared.data_path)
                if final_source_digest != plan.source_sha256:
                    raise HauteValidationError("evaluation source changed before final fit")
                report("Evaluation: final fit", completed_before_final / total)
                if self.tuning is not None and on_tuning_progress is not None:
                    on_tuning_progress(
                        {
                            "phase": "final_fit",
                            "trial_index": None,
                            "trial_count": self.tuning.trial_count,
                            "fold_index": None,
                            "fold_count": self.tuning.validation_fit_count,
                            "completed_fits": completed_before_final,
                            "total_fits": total,
                            "best_objective": (
                                tuning_response["winner_objective"]
                                if tuning_response is not None
                                else None
                            ),
                        }
                    )
                final = self._new_evaluation_job(
                    name=self.name,
                    data=prepared.data_path,
                    output_dir=self.output_dir,
                    plan=plan,
                    fit_index=None,
                    # The outer orchestration logs exactly once after attaching
                    # evaluation/tuning reports and canonical final-test labels.
                    mlflow_experiment=None,
                    params=final_params,
                    source_sha256=final_source_digest,
                )
                result = final.run(
                    progress=lambda message, fraction: report(
                        f"Evaluation: final fit: {message}",
                        (completed_before_final + fraction) / total,
                    ),
                    on_iteration=on_iteration,
                    check_cancelled=check_cancelled,
                    execution_context=execution_context,
                )
                if len(selection_histories) == 1:
                    # One holdout fit chose the refit's tree count: keep its curves.
                    result.validation_loss_history = selection_histories[0]
            else:
                assert selected_result is not None
                result = selected_result
            result.development_rows = len(plan.development_positions)
            result.final_test_rows = len(plan.test_positions)
            result.final_test_metrics = dict(result.holdout_metrics) if plan.test_positions else {}
            result.diagnostics_set = (
                "final_test"
                if plan.test_positions
                else "development"
                if self.refit_on_development
                else "validation"
            )
            result.evaluation = {
                "schema_version": 1,
                "strategy": self.evaluation.strategy,
                "validation_method": self.evaluation.validation["method"],
                "validation_fit_count": selection_fit_count,
                "fit_count": total,
                "refit_on_development": self.refit_on_development,
                "development_rows": len(plan.development_positions),
                "final_test_rows": len(plan.test_positions),
                "selection_fits": [fit.to_plain_data() for fit in results.fits],
                "selection_metrics": {
                    metric: dict(values) for metric, values in aggregate.metrics.items()
                },
                "plan_sha256": plan_digest,
                "results_sha256": results_digest,
                "plan_path": str(plan_path),
                "results_path": str(results_path),
                "report_path": str(report_path),
                "summary": dict(plan.summary),
            }
            result.tuning = tuning_response
            refit = algorithm_descriptor(self.algorithm)
            if (
                refit.refit_policy == "validation_weighted_rounds"
                and selection_fit_count
                and self.refit_on_development
            ):
                result.final_tree_count = final_params[refit.refit_round_key]
            if self.tuning is not None and on_tuning_progress is not None:
                on_tuning_progress(
                    {
                        "phase": "publication",
                        "trial_index": None,
                        "trial_count": self.tuning.trial_count,
                        "fold_index": None,
                        "fold_count": self.tuning.validation_fit_count,
                        "completed_fits": total,
                        "total_fits": total,
                        "best_objective": (
                            tuning_response["winner_objective"]
                            if tuning_response is not None
                            else None
                        ),
                    }
                )
            if self.mlflow_experiment:
                checkpoint("before_evaluation_mlflow_log")
                with _training_stage(
                    execution_context,
                    "training_mlflow_log",
                ):
                    self._log_to_mlflow(
                        result,
                        final_params=final_params,
                        check_cancelled=lambda: checkpoint("evaluation_mlflow_checkpoint"),
                    )
            report("Done", 1.0)
            return result
        except BaseException:
            for path in created:
                _remove_training_artifact(path)
            raise
        finally:
            self._cleanup_owned_temp_parquets(prepared, None)

    # ------------------------------------------------------------------
    # Pipeline sub-methods
    # ------------------------------------------------------------------

    def _prepare_data(
        self,
        _report: Callable[[str, float], None],
        *,
        execution_context: ExecutionContext | None = None,
    ) -> _PreparedData:
        """Load data, validate columns, clean null targets, and derive features."""
        from haute.modelling._algorithms import _mem_checkpoint

        owns_tmp = False
        data_path: str | None = None

        if isinstance(self._data, str) and self._data.endswith(".parquet"):
            # Route already sunk the LazyFrame to disk -- no collect needed
            data_path = self._data
            self._data = None
            _mem_checkpoint(f"using on-disk parquet: {data_path}")
        else:
            # DataFrame or LazyFrame: collect, write to temp parquet, free
            df = self._load_data(execution_context=execution_context)
            self._data = None
            _mem_checkpoint(f"data loaded ({len(df):,} rows)")

            if self.target in df.columns:
                null_count = df[self.target].null_count()
                if null_count is not None and null_count > 0:
                    _mem_checkpoint(f"target has {null_count:,} null rows (will be cleaned)")

            with tempfile.NamedTemporaryFile(
                suffix=".parquet",
                prefix="haute_split_",
                delete=False,
            ) as f:
                data_path = f.name
            owns_tmp = True
            try:
                _training_checkpoint(
                    execution_context,
                    label="before_training_input_parquet_write",
                )
                with _training_stage(execution_context, "training_input_parquet_write"):
                    df.write_parquet(data_path)
                _training_checkpoint(
                    execution_context,
                    label="after_training_input_parquet_write",
                )
            except BaseException:
                _remove_temp_parquet(data_path, context="training_input_parquet_write")
                raise
            del df
            gc.collect()
            _malloc_trim()
            _mem_checkpoint("wrote temp parquet, freed df")

        try:
            # Validate schema from parquet metadata (cheap, no data loaded)
            _report("Validating columns", 0.05)
            from haute._polars_utils import read_parquet_metadata

            pq_meta = read_parquet_metadata(Path(data_path))
            if pq_meta["row_count"] == 0:
                raise HauteValidationError("DataFrame is empty — cannot train on zero rows")
            schema_lf = pl.scan_parquet(data_path)
            schema_df = _training_streaming_collect(
                schema_lf.head(0),
                stage_name="training_schema_collect",
                execution_context=execution_context,
            )
            self._validate_columns(schema_df)

            # The target's values must be able to serve the configured task
            # and the effective metric set (objective-implied defaults
            # included). The train route runs the same gate before dispatching
            # the fit worker; repeating it here covers the CLI and
            # exported-script paths, and the shared function keeps the two
            # from drifting.
            # Internal evaluation clones (selection, tuning, and the final
            # fit — all constructed with evaluation_plan set) re-read a
            # prepared source the outer job already gated, so they skip the
            # redundant per-fit target re-scan.
            if self.evaluation_plan is None:
                from haute.modelling._target_check import training_target_task_issue

                target_task_issue = training_target_task_issue(
                    pl.scan_parquet(data_path),
                    target=self.target,
                    task=self.task,
                    metrics=self.metrics,
                    collect=lambda lf: _training_streaming_collect(
                        lf,
                        stage_name="training_target_task_check",
                        execution_context=execution_context,
                    ),
                )
                if target_task_issue is not None:
                    raise HauteValidationError(target_task_issue)

            # Null targets cannot be passed to trainers.  External parquet inputs
            # keep the filter fused into the split sink to avoid an extra wide
            # clean file; owned temp inputs are already materialized, so publish a
            # clean prepared source for callers that inspect _prepare_data directly.
            null_count = _training_streaming_collect(
                pl.scan_parquet(data_path).select(pl.col(self.target).is_null().sum()),
                stage_name="training_target_null_count",
                execution_context=execution_context,
            ).item()
            target_null_count = int(null_count or 0)
            filtered_row_count = pq_meta["row_count"] - target_null_count
            if target_null_count > 0:
                _mem_checkpoint(
                    f"target has {target_null_count:,} null rows (will be filtered during split)"
                )
                if filtered_row_count == 0:
                    raise HauteValidationError(
                        f"Target column '{self.target}' contains only null values; "
                        "cannot train on zero non-null target rows"
                    )
                if owns_tmp:
                    clean_path: str | None = None
                    with tempfile.NamedTemporaryFile(
                        suffix=".parquet",
                        prefix="haute_clean_",
                        delete=False,
                    ) as f:
                        clean_path = f.name
                    try:
                        from haute._polars_utils import bounded_sink

                        _training_checkpoint(
                            execution_context,
                            label="before_training_clean_parquet_write",
                        )
                        with _training_stage(execution_context, "training_clean_parquet_write"):
                            bounded_sink(
                                pl.scan_parquet(data_path).filter(
                                    pl.col(self.target).is_not_null()
                                ),
                                clean_path,
                                fast_checkpoint=True,
                            )
                        _training_checkpoint(
                            execution_context,
                            label="after_training_clean_parquet_write",
                        )
                    except BaseException:
                        # The original temp input is removed by the outer
                        # data-prep guard below.
                        _remove_temp_parquet(clean_path, context="training_clean_parquet_write")
                        raise
                    os.unlink(data_path)
                    data_path = clean_path
                    _mem_checkpoint(
                        f"wrote clean temp parquet without {target_null_count:,} null target rows"
                    )

            self._require_positive_log_link_offset(
                data_path,
                execution_context=execution_context,
            )

            # Derive features from schema
            features, cat_features = self._derive_features(schema_df)
            # Snapshot dtypes before we drop the schema frame — downstream
            # consumers (MLflow signature, feature contract) need them.
            feature_dtypes = {f: contract_dtype_name(schema_df[f].dtype) for f in features}
            categorical_levels = self._categorical_levels_for_contract(
                features,
                cat_features,
            )
            target_dtype = (
                contract_dtype_name(schema_df[self.target].dtype)
                if self.target in schema_df.columns
                else ""
            )
            offset_dtype = (
                contract_dtype_name(schema_df[self.offset].dtype)
                if self.offset and self.offset in schema_df.columns
                else ""
            )
            del schema_df, schema_lf
            _report(f"Using {len(features)} features ({len(cat_features)} categorical)", 0.1)

            return _PreparedData(
                data_path=data_path,
                owns_tmp=owns_tmp,
                features=features,
                cat_features=cat_features,
                total_rows=filtered_row_count,
                feature_dtypes=feature_dtypes,
                categorical_levels=categorical_levels,
                target_dtype=target_dtype,
                target_null_count=target_null_count,
                offset_dtype=offset_dtype,
            )
        except BaseException:
            # Remediation 4b.6 — validation/cleaning/feature-derivation
            # failures (and cancellations raised through _report) must not
            # orphan the run-owned temp input written above.  Caller-owned
            # parquet inputs (owns_tmp=False) are never touched.
            if owns_tmp:
                _remove_temp_parquet(data_path, context="training_prepare_data_abort")
            raise

    def _split_data(
        self,
        prepared: _PreparedData,
        _report: Callable[[str, float], None],
        *,
        execution_context: ExecutionContext | None = None,
    ) -> _SplitResult:
        """Compute train/validation/holdout split mask and write split parquet."""
        from haute.modelling._algorithms import _mem_checkpoint

        data_path = prepared.data_path
        owns_tmp = prepared.owns_tmp
        total_rows = prepared.total_rows
        target_null_count = prepared.target_null_count
        split_lf = pl.scan_parquet(data_path)
        if target_null_count > 0:
            split_lf = split_lf.filter(pl.col(self.target).is_not_null())
        if self.task == "classification":
            self._class_labels = self._resolve_class_labels(
                split_lf, execution_context=execution_context
            )
            # Every consumer of the split file (fit, metrics, diagnostics)
            # sees one encoding: the positive class is 1.0, the other 0.0.
            split_lf = split_lf.with_columns(
                (pl.col(self.target) == self._class_labels[1]).cast(pl.Float64).alias(self.target)
            )

        if self.evaluation_plan is not None:
            # The orchestrator hashes the prepared source once per run (and
            # freshly again before the deployable final fit); children reuse
            # that digest instead of re-hashing a multi-GB parquet per fit.
            source_digest = self._plan_source_sha256 or evaluation_file_sha256(data_path)
            if self.evaluation_plan.source_sha256 != source_digest:
                raise HauteValidationError(
                    "evaluation plan source digest does not match prepared source"
                )
            if total_rows != self.evaluation_plan.row_count:
                raise HauteValidationError(
                    "evaluation plan row count does not match prepared source"
                )
            if self.evaluation_fit_index is None:
                mask = pl.Series("_partition", self.evaluation_plan.final_mask())
            else:
                mask = pl.Series(
                    "_partition",
                    (
                        self.evaluation_plan.selection_mask(self.evaluation_fit_index)
                        if self.refit_on_development
                        else self.evaluation_plan.saved_selection_mask(self.evaluation_fit_index)
                    ),
                )
        else:
            # Compute mask -- for temporal/group we need a small scan
            mask_df = None
            if self.split_config.strategy in ("temporal", "group"):
                col = self.split_config.date_column or self.split_config.group_column
                mask_df = _training_streaming_collect(
                    split_lf.select(col),
                    stage_name="training_split_key_collect",
                    execution_context=execution_context,
                )
            mask = split_mask(total_rows, self.split_config, df=mask_df)
            del mask_df
        n_train = int((mask == PARTITION_TRAIN).sum())
        n_validation = int((mask == PARTITION_VALIDATION).sum())
        n_holdout = int((mask == PARTITION_HOLDOUT).sum())
        _mem_checkpoint(
            f"split mask (train={n_train:,} val={n_validation:,} holdout={n_holdout:,})"
        )

        # Write split parquet: original data + _partition column.
        # Prefer Polars' streaming sink so wide split files do not have to
        # materialize as one full eager DataFrame before writing.
        with tempfile.NamedTemporaryFile(
            suffix=".parquet",
            prefix="haute_split_",
            delete=False,
        ) as f:
            split_path = f.name
        from haute._polars_utils import bounded_sink

        try:
            _training_checkpoint(
                execution_context,
                label="before_training_split_parquet_write",
            )
            with _training_stage(execution_context, "training_split_parquet_write"):
                bounded_sink(
                    split_lf.with_columns(mask),
                    split_path,
                    fast_checkpoint=True,
                )
            _training_checkpoint(
                execution_context,
                label="after_training_split_parquet_write",
            )
        except BaseException:
            _remove_temp_parquet(split_path, context="training_split_parquet_write")
            raise
        del mask
        gc.collect()
        _malloc_trim()

        # Free the original temp parquet if we own it
        if owns_tmp and data_path != split_path:
            os.unlink(data_path)
        _mem_checkpoint("wrote split parquet")

        return _SplitResult(
            split_path=split_path,
            owns_tmp=True,
            n_train=n_train,
            n_validation=n_validation,
            n_holdout=n_holdout,
        )

    def _train_model(
        self,
        split_result: _SplitResult,
        features: list[str],
        cat_features: list[str],
        on_iteration: IterationCallback | None,
        _report: Callable[[str, float], None],
        *,
        execution_context: ExecutionContext | None = None,
    ) -> _TrainModelResult:
        """Build train/eval pools (or DataFrames for GLM), fit the model."""
        from haute.modelling._algorithms import _mem_checkpoint

        data_path = split_result.split_path
        has_validation = split_result.n_validation > 0

        # Look up algorithm
        algo_cls = ALGORITHM_REGISTRY.get(self.algorithm)
        if algo_cls is None:
            raise HauteValidationError(
                f"Unknown algorithm: {self.algorithm}. Available: {list(ALGORITHM_REGISTRY.keys())}"
            )
        algo = algo_cls()

        # Resolve loss function and inject into params
        fit_params = {**self.params}

        # GLM: pack all GLM-specific config into fit_params for the algorithm
        is_glm = self.algorithm == "glm"
        frame_based = self.algorithm != "catboost"
        if self.algorithm == "catboost":
            loss_function = self._catboost_loss_function()
            if loss_function:
                fit_params["loss_function"] = loss_function

        # Read train partition
        _report("Loading training data", 0.2)
        train_df = _training_streaming_collect(
            self._scan_with_columns(data_path, features)
            .filter(pl.col("_partition") == PARTITION_TRAIN)
            .drop("_partition"),
            stage_name="training_train_partition_materialise",
            execution_context=execution_context,
        )
        _mem_checkpoint(f"read train partition ({len(train_df):,} rows)")

        def load_validation() -> pl.DataFrame:
            _report("Loading validation data", 0.25)
            validation = _training_streaming_collect(
                self._scan_with_columns(data_path, features)
                .filter(pl.col("_partition") == PARTITION_VALIDATION)
                .drop("_partition"),
                stage_name="training_validation_partition_materialise",
                execution_context=execution_context,
            )
            _mem_checkpoint(f"read validation partition ({len(validation):,} rows)")
            return validation

        if frame_based:
            # GLM and the new-family adapters take DataFrames and build their
            # own native data (no CatBoost Pool).
            eval_df = load_validation() if has_validation else None
            _report(f"Fitting {algorithm_descriptor(self.algorithm).label}", 0.3)
            adapter_kwargs: dict[str, Any] = {}
            if not is_glm:
                adapter_kwargs = {
                    "loss": self._haute_loss(),
                    "variance_power": self.variance_power,
                    "threads": self.threads,
                    "categorical_levels": self._declared_categorical_levels or None,
                    "class_labels": self._class_labels,
                    "seed": self.evaluation.seed if self.evaluation is not None else 0,
                    "device": self.device,
                }
            with _training_stage(execution_context, "training_algorithm_fit"):
                fit_result = algo.fit(
                    train_df,
                    features,
                    cat_features,
                    self.target,
                    self.weight,
                    fit_params,
                    self.task,
                    on_iteration=on_iteration,
                    eval_df=eval_df,
                    offset=self.offset,
                    monotone_constraints=self.monotone_constraints,
                    feature_weights=self.feature_weights,
                    **adapter_kwargs,
                )
            _mem_checkpoint("frame-based algo.fit() returned")
            del train_df, eval_df
            gc.collect()
            _malloc_trim()
        else:
            # CatBoost: build memory-efficient pools, then fit
            from haute.modelling._algorithms import _build_pool

            train_y = train_df[self.target].cast(pl.Float64).to_numpy()
            train_w = train_df[self.weight].cast(pl.Float64).to_numpy() if self.weight else None
            from haute.modelling._algorithms import offset_baseline

            offset_link = self._offset_link()
            train_baseline = (
                offset_baseline(
                    train_df[self.offset].cast(pl.Float64).to_numpy(),
                    column=self.offset,
                    link=offset_link,
                    context="CatBoost training",
                )
                if self.offset
                else None
            )
            train_features_df = train_df.select(features)
            del train_df
            gc.collect()
            _malloc_trim()
            _mem_checkpoint("extracted labels, freed train_df")

            with _training_stage(execution_context, "training_build_train_pool"):
                train_pool = _build_pool(
                    train_features_df,
                    features,
                    cat_features,
                    y=train_y,
                    w=train_w,
                    baseline=train_baseline,
                )
            del train_features_df, train_y, train_w, train_baseline
            gc.collect()
            _malloc_trim()
            _mem_checkpoint("train pool built")

            eval_pool = None
            if has_validation:
                eval_df = load_validation()
                _report("Building eval pool", 0.25)
                val_y = eval_df[self.target].cast(pl.Float64).to_numpy()
                val_w = eval_df[self.weight].cast(pl.Float64).to_numpy() if self.weight else None
                val_baseline = (
                    offset_baseline(
                        eval_df[self.offset].cast(pl.Float64).to_numpy(),
                        column=self.offset,
                        link=offset_link,
                        context="CatBoost validation",
                    )
                    if self.offset
                    else None
                )
                val_features_df = eval_df.select(features)
                del eval_df
                gc.collect()
                _malloc_trim()

                with _training_stage(execution_context, "training_build_eval_pool"):
                    eval_pool = _build_pool(
                        val_features_df,
                        features,
                        cat_features,
                        y=val_y,
                        w=val_w,
                        baseline=val_baseline,
                    )
                del val_features_df, val_y, val_w, val_baseline
                gc.collect()
                _malloc_trim()
                _mem_checkpoint("eval pool built")

            _report("Training model", 0.3)
            with _training_stage(execution_context, "training_algorithm_fit"):
                fit_result = algo.fit(
                    None,
                    features,
                    cat_features,
                    self.target,
                    self.weight,
                    fit_params,
                    self.task,
                    on_iteration=on_iteration,
                    offset=self.offset,
                    monotone_constraints=self.monotone_constraints,
                    feature_weights=self.feature_weights,
                    pool=train_pool,
                    eval_pool=eval_pool,
                    threads=self.threads,
                    class_labels=self._class_labels,
                )
            _mem_checkpoint("algo.fit() returned")
            del train_pool, eval_pool
            gc.collect()
            _malloc_trim()
            _mem_checkpoint("del pools")

        return _TrainModelResult(
            model=fit_result.model,
            algo=algo,
            fit_result=fit_result,
            fit_params=fit_params,
        )

    def _glm_select_columns(self, features: list[str]) -> list[str] | None:
        """Column subset needed for GLM parquet reads, or ``None`` for CatBoost.

        GLM only needs the terms + target + weight + offset columns.
        Returning ``None`` means "read all columns" (CatBoost path).
        """
        if self.algorithm != "glm":
            return None
        needed = set(features)
        needed.add(self.target)
        if self.weight:
            needed.add(self.weight)
        if self.offset:
            needed.add(self.offset)
        return sorted(needed)

    def _catboost_select_columns(self, features: list[str]) -> list[str] | None:
        """Column subset needed for CatBoost train/eval partition reads.

        Preserve feature order for pool construction, then append target,
        weight, and offset columns required to extract labels/aux arrays.
        Returning ``None`` means "read all columns" for non-CatBoost paths.
        """
        if self.algorithm == "glm":
            return None
        needed: list[str] = []
        for column in [*features, self.target, self.weight, self.offset]:
            if column and column not in needed:
                needed.append(column)
        return needed

    def _scan_with_columns(self, data_path: str, features: list[str]) -> pl.LazyFrame:
        """Scan parquet with training-column projection when the algorithm supports it."""
        scan = pl.scan_parquet(data_path)
        projected_columns = self._glm_select_columns(features)
        if projected_columns is None:
            projected_columns = self._catboost_select_columns(features)
        if projected_columns is not None:
            scan = scan.select([*projected_columns, "_partition"])
        return scan

    def _read_partition(
        self,
        data_path: str,
        partition: int,
        columns: list[str] | None = None,
        *,
        execution_context: ExecutionContext | None = None,
        stage_name: str = "training_partition_materialise",
    ) -> pl.DataFrame:
        """Read a single partition from the split parquet.

        If *columns* is given, only those columns (plus ``_partition`` for
        filtering) are loaded — Polars pushes the projection into the
        parquet reader so unused columns never touch RAM.
        """
        scan = pl.scan_parquet(data_path)
        if columns is not None:
            # Always need _partition for the filter; drop it after
            select_cols = columns if "_partition" in columns else [*columns, "_partition"]
            scan = scan.select(select_cols)
        return _training_streaming_collect(
            scan.filter(pl.col("_partition") == partition).drop("_partition"),
            stage_name=stage_name,
            execution_context=execution_context,
        )

    def _metric_stage_error(self, exc: Exception, *, evaluation_set: str) -> HauteValidationError:
        """Wrap a mandatory metric failure with the user-model objects involved.

        The library error alone ("continuous format is not supported") names
        neither the target column nor the task nor a fix; this is a
        user-facing boundary, so the wrapped message must carry all three.
        The library detail goes last: worker failure messages are truncated
        to a bounded length, and the call to action must survive that.
        On the evaluation-plan pipeline the internal partition names map to
        the labels its reports use publicly (`development`/`final test`);
        the legacy constructor-only pipeline reports internal names as-is.
        """
        label = evaluation_set
        if self.evaluation_plan is not None:
            label = _PUBLIC_EVALUATION_SET_LABELS.get(evaluation_set, evaluation_set)
        metric_list = ", ".join(self.metrics)
        return HauteValidationError(
            f"Could not evaluate the trained model on the {label} data. The "
            f"metrics ({metric_list}) were computed against target column "
            f"'{self.target}' with task '{self.task}'. Check that the target's values "
            "match the task and metrics (AUC and log loss need a discrete 0/1 target), "
            f"then adjust the target column, the task, or the reported metrics. "
            f"Underlying error: {exc}"
        )

    def _predict_diagnostic_batches(
        self,
        algo: Any,
        model: Any,
        frame: pl.DataFrame,
        features: list[str],
        *,
        execution_context: ExecutionContext | None,
    ) -> np.ndarray:
        """Predict diagnostics in bounded row batches without changing empty semantics."""
        if frame.height == 0:
            return np.asarray(algo.predict(model, frame, features, offset=self.offset))
        result: np.ndarray | None = None
        offset = 0
        with closing(
            bounded_collect_batches(
                frame.lazy(),
                chunk_size=65_536,
                execution_context=execution_context,
                stage_name="training_diagnostic_predictions",
            )
        ) as batches:
            for batch in batches:
                prediction = np.asarray(algo.predict(model, batch, features, offset=self.offset))
                if prediction.ndim == 0 or prediction.shape[0] != batch.height:
                    raise ValueError("Diagnostic predictions must have one row per input row")
                if result is None:
                    result = np.empty((frame.height, *prediction.shape[1:]), dtype=prediction.dtype)
                elif prediction.dtype != result.dtype or prediction.shape[1:] != result.shape[1:]:
                    raise ValueError("Diagnostic prediction shape or dtype changed between batches")
                next_offset = offset + len(batch)
                result[offset:next_offset] = prediction
                offset = next_offset
                del batch, prediction
        if result is None or offset != frame.height:
            raise RuntimeError("Diagnostic prediction batches did not cover the input rows")
        return result

    def _compute_metrics(
        self,
        split_result: _SplitResult,
        features: list[str],
        cat_features: list[str],
        train_result: _TrainModelResult,
        _report: Callable[[str, float], None],
        *,
        execution_context: ExecutionContext | None = None,
    ) -> _MetricsResult:
        """Evaluate model: metrics on validation + holdout, diagnostics on best available set.

        Memory-optimised: each partition is read at most once.  The diagnostics
        partition (holdout > validation > train) is read once and used for both
        its per-partition metrics *and* all diagnostic plots.  A separate read
        is only done for validation when holdout also exists.
        """
        from haute.modelling._algorithms import _build_pool, _mem_checkpoint
        from haute.modelling._metrics import (
            compute_actual_vs_predicted,
            compute_ave_per_feature,
            compute_double_lift,
            compute_lorenz_curve,
            compute_pdp,
            compute_residuals_histogram,
        )

        data_path = split_result.split_path
        algo = train_result.algo
        model = train_result.model

        # Optional-diagnostic error surface — see fail-loud policy:
        # mandatory failures (feature_importance, compute_metrics) must
        # propagate, optional ones (SHAP, PDP, GLM info) are skipped
        # but recorded so callers see the degraded state.
        diagnostics_errors: list[dict[str, str]] = []

        has_validation = split_result.n_validation > 0
        has_holdout = split_result.n_holdout > 0
        glm_columns = self._glm_select_columns(features)

        # Feature importance (MANDATORY — doesn't need eval data)
        importance = algo.feature_importance(model)
        sorted_features = [fi["feature"] for fi in importance if fi["feature"] in features]
        sorted_features += [f for f in features if f not in sorted_features]

        # ── Determine which set to use for diagnostics (holdout > validation > train) ──
        if has_holdout:
            diag_partition = PARTITION_HOLDOUT
            diagnostics_set = "holdout"
        elif has_validation:
            diag_partition = PARTITION_VALIDATION
            diagnostics_set = "validation"
        else:
            diag_partition = PARTITION_TRAIN
            diagnostics_set = "train"

        # Holdout diagnostics are the largest live allocation in this branch.
        # Calculate and release validation quality first so the two partitions
        # are never resident together.
        validation_metrics: dict[str, float] | None = None
        vp = self.variance_power
        if has_holdout and has_validation:
            val_df = self._read_partition(
                data_path,
                PARTITION_VALIDATION,
                columns=glm_columns,
                execution_context=execution_context,
                stage_name="training_validation_metrics_materialise",
            )
            val_y_true = val_df[self.target].to_numpy()
            val_y_pred = self._predict_diagnostic_batches(
                algo, model, val_df, features, execution_context=execution_context
            )
            val_w = val_df[self.weight].to_numpy() if self.weight else None
            try:
                validation_metrics = compute_metrics(
                    val_y_true, val_y_pred, val_w, self.metrics, variance_power=vp
                )
            except _METRIC_STAGE_FAILURE_TYPES as exc:
                raise self._metric_stage_error(exc, evaluation_set="validation") from exc
            del val_df, val_y_true, val_y_pred, val_w
            gc.collect()

        # ── Read the diagnostics partition ONCE — metrics + all diagnostics ──
        _report("Computing diagnostics", 0.8)
        diag_df = self._read_partition(
            data_path,
            diag_partition,
            columns=glm_columns,
            execution_context=execution_context,
            stage_name=f"training_{diagnostics_set}_diagnostics_materialise",
        )
        _mem_checkpoint(f"read {diagnostics_set} partition for diagnostics ({len(diag_df):,} rows)")
        y_true = diag_df[self.target].to_numpy()
        # Reported fit quality describes the predictions the model serves.
        y_pred = self._predict_diagnostic_batches(
            algo, model, diag_df, features, execution_context=execution_context
        )
        w = diag_df[self.weight].to_numpy() if self.weight else None

        # Primary metrics from the diagnostics set
        try:
            metrics = compute_metrics(
                y_true,
                y_pred,
                w,
                self.metrics,
                variance_power=vp,
            )
        except _METRIC_STAGE_FAILURE_TYPES as exc:
            raise self._metric_stage_error(exc, evaluation_set=diagnostics_set) from exc

        # When holdout is present, diagnostics were computed on holdout.
        # Validation quality was already computed and released before this read.
        holdout_metrics: dict[str, float] = {}
        if diagnostics_set == "holdout":
            holdout_metrics = metrics
            if validation_metrics is not None:
                metrics = validation_metrics

        # Double-lift
        double_lift = compute_double_lift(y_true, y_pred, w)

        # AvE per feature
        ave_per_feature = compute_ave_per_feature(
            diag_df,
            sorted_features,
            cat_features,
            y_true,
            y_pred,
            w,
        )

        # SHAP + LossFunctionChange importance (OPTIONAL: failures
        # surface in diagnostics_errors so the UI can flag a degraded run.)
        shap_summary: list[dict[str, float]] = []
        feature_importance_loss: list[dict[str, Any]] = []
        if hasattr(algo, "shap_summary"):
            _report("Computing SHAP values", 0.85)
            try:
                shap_summary = algo.shap_summary(model, diag_df, features, cat_features)
            except Exception as exc:
                _record_diag_error(diagnostics_errors, "shap", exc)
        if hasattr(algo, "feature_importance_typed"):
            _report("Computing loss-based feature importance", 0.855)
            try:
                _diag_pool = _build_pool(
                    diag_df,
                    features,
                    cat_features,
                    target=self.target,
                    offset=self.offset,
                    offset_link=self._offset_link() if self.offset else None,
                )
                feature_importance_loss = algo.feature_importance_typed(
                    model,
                    _diag_pool,
                    "LossFunctionChange",
                )
                del _diag_pool
            except Exception as exc:
                _record_diag_error(diagnostics_errors, "feature_importance_loss", exc)

        # Residuals, scatter, Lorenz
        _report("Computing diagnostics", 0.86)
        residuals_histogram, residuals_stats = compute_residuals_histogram(y_true, y_pred, w)
        actual_vs_predicted = compute_actual_vs_predicted(y_true, y_pred, w)
        lorenz_model, lorenz_perfect = compute_lorenz_curve(y_true, y_pred, w)

        # PDP (OPTIONAL — numerically fragile on small subsamples; its
        # output is diagnostic-only, so record failures in diagnostics_errors
        # rather than aborting an otherwise-successful run.)
        _report("Computing partial dependence", 0.87)
        pdp_data: list[dict[str, Any]] = []
        try:
            pdp_data = compute_pdp(
                model,
                algo,
                diag_df,
                features,
                cat_features,
                offset=self.offset,
            )
            # Importance ranks charts, never the positional inputs to prediction.
            pdp_order = {feature: index for index, feature in enumerate(sorted_features)}
            pdp_data.sort(key=lambda entry: pdp_order[entry["feature"]])
        except Exception as exc:
            _record_diag_error(diagnostics_errors, "pdp", exc)

        # ── GLM-specific diagnostics (all OPTIONAL) ──
        glm_report = None
        if hasattr(algo, "glm_result"):
            glm_report = algo.glm_result(model, train_result.fit_params)
            for diagnostic, glm_error in glm_report.errors:
                _record_diag_error(diagnostics_errors, diagnostic, glm_error)

        # ── EBM term report (OPTIONAL) ──
        ebm_terms: list[dict[str, Any]] = []
        if hasattr(algo, "ebm_terms"):
            try:
                ebm_terms = algo.ebm_terms(model)
            except Exception as exc:
                _record_diag_error(diagnostics_errors, "ebm_terms", exc)

        del diag_df
        gc.collect()

        # Clean up split parquet
        if split_result.owns_tmp and Path(data_path).exists():
            os.unlink(data_path)

        return _MetricsResult(
            metrics=metrics,
            holdout_metrics=holdout_metrics,
            diagnostics_set=diagnostics_set,
            importance=importance,
            double_lift=double_lift,
            shap_summary=shap_summary,
            feature_importance_loss=feature_importance_loss,
            ave_per_feature=ave_per_feature,
            residuals_histogram=residuals_histogram,
            residuals_stats=residuals_stats,
            actual_vs_predicted=actual_vs_predicted,
            lorenz_curve=lorenz_model,
            lorenz_curve_perfect=lorenz_perfect,
            pdp_data=pdp_data,
            glm_coefficients=glm_report.coefficients if glm_report else [],
            glm_relativities=glm_report.relativities if glm_report else [],
            glm_fit_statistics=glm_report.fit_statistics if glm_report else {},
            glm_inference=glm_report.inference if glm_report else None,
            glm_smooth_terms=glm_report.smooth_terms if glm_report else [],
            glm_regularization=glm_report.regularization if glm_report else None,
            ebm_terms=ebm_terms,
            diagnostics_errors=diagnostics_errors,
        )

    def _save_artifacts(
        self,
        train_result: _TrainModelResult,
        *,
        features: list[str] | None = None,
        cat_features: list[str] | None = None,
        categorical_levels: Mapping[str, Iterable[str | None]] | None = None,
    ) -> Path:
        """Save the trained model and its feature contract to disk.

        Writes into ``output_dir``:

        * the native model file (``.cbm`` / ``.rsglm`` / ``.model``),
        * ``{name}.feature_contract.json`` — the train-vs-score contract
          (see :func:`model_contract_filename`), written when the caller
          supplies ``features`` (the real ``run()`` path does).  The name
          is per-model so several models trained into one ``output_dir``
          cannot overwrite each other's contracts (remediation 4b.9).

        ``features`` and ``cat_features`` default to ``None`` so the
        helper remains callable from unit tests that mock the earlier
        training steps; the contract is only written when the caller
        has the real feature list in hand.
        """
        from haute.modelling._feature_contract import build_contract, save_contract

        ext = MODEL_FILE_SUFFIXES.get(self.algorithm)
        if ext is None:
            raise HauteValidationError(
                f"Algorithm {self.algorithm!r} has no model file suffix; "
                "register its descriptor before saving."
            )
        output_dir = Path(self.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        model_path = output_dir / f"{self.name}{ext}"
        train_result.algo.save(train_result.model, model_path)

        if features:
            contract = build_contract(
                features=list(features),
                feature_types=self._feature_dtypes_for_contract(features),
                categorical_features=list(cat_features or []),
                categorical_levels=(
                    categorical_levels
                    if categorical_levels is not None
                    else self._categorical_levels_for_contract(
                        features,
                        list(cat_features or []),
                    )
                ),
                target_name=self.target,
                target_type=self._target_dtype_for_contract(),
                task="classification" if self.task == "classification" else "regression",
                offset_column=self.offset,
                model=self._model_identity(),
            )
            contract_path = output_dir / model_contract_filename(self.name)
            save_contract(contract, contract_path)

        return model_path

    # ------------------------------------------------------------------
    # Utility methods (unchanged)
    # ------------------------------------------------------------------

    def _load_data(
        self,
        *,
        execution_context: ExecutionContext | None = None,
    ) -> pl.DataFrame:
        """Load data from path or use directly if already a DataFrame."""
        if isinstance(self._data, pl.DataFrame):
            return self._data
        if isinstance(self._data, pl.LazyFrame):
            return _training_streaming_collect(
                self._data,
                stage_name="training_input_materialise",
                execution_context=execution_context,
            )
        if self._data is None:
            raise RuntimeError("Training data has already been consumed")
        from haute._io import read_source

        path = Path(self._data)
        if not path.exists():
            raise FileNotFoundError(f"Data file not found: {path}")
        return _training_streaming_collect(
            read_source(str(path)),
            stage_name="training_source_materialise",
            execution_context=execution_context,
        )

    def _validate_columns(self, df: pl.DataFrame) -> None:
        """Validate that required columns exist in the DataFrame."""
        if self.target not in df.columns:
            raise HauteValidationError(
                f"Target column '{self.target}' not found. Available: {df.columns}"
            )
        if self.weight and self.weight not in df.columns:
            raise HauteValidationError(
                f"Weight column '{self.weight}' not found. Available: {df.columns}"
            )
        if self.offset and self.offset not in df.columns:
            raise HauteValidationError(
                f"Offset column '{self.offset}' not found. Available: {df.columns}"
            )
        # Excluded columns may already have been projected out during
        # pipeline execution — only flag genuinely unknown columns.
        available = set(df.columns)
        non_feature_cols = {self.target}
        if self.weight:
            non_feature_cols.add(self.weight)
        if self.offset:
            non_feature_cols.add(self.offset)
        dropped = [
            col for col in self.exclude if col not in available and col not in non_feature_cols
        ]
        if dropped:
            logger.debug("exclude_columns_already_dropped", count=len(dropped))

    def _derive_features(self, df: pl.DataFrame) -> tuple[list[str], list[str]]:
        """Derive feature list: all columns minus {target, weight, *exclude}.

        Also detects categorical features from Polars dtype.
        """
        if self.feature_columns:
            missing = [column for column in self.feature_columns if column not in df.columns]
            if missing:
                raise HauteValidationError(
                    "Configured feature column(s) not found in training data: "
                    f"{missing}. Available columns: {df.columns}"
                )
            features = list(self.feature_columns)
            cat_features = [
                column
                for column in features
                if df[column].dtype in (pl.Utf8, pl.Categorical, pl.String)
            ]
            return features, cat_features

        non_features = {self.target}
        if self.weight:
            non_features.add(self.weight)
        if self.offset:
            non_features.add(self.offset)
        if self.fold_column:
            non_features.add(self.fold_column)
        non_features.update(self.id_columns)
        if self.split_config.strategy == "temporal" and self.split_config.date_column:
            non_features.add(self.split_config.date_column)
        if self.split_config.strategy == "group" and self.split_config.group_column:
            non_features.add(self.split_config.group_column)
        non_features.update(self.exclude)

        features = [c for c in df.columns if c not in non_features]
        if not features:
            raise HauteValidationError(
                "No feature columns remaining after excluding "
                f"{non_features}. Check your target/weight/exclude settings."
            )

        # Detect categorical features from Polars dtype
        cat_features = []
        for col in features:
            dtype = df[col].dtype
            if dtype in (pl.Utf8, pl.Categorical, pl.String):
                cat_features.append(col)

        return features, cat_features

    def _validate_monotone_constraints(self, prepared: _PreparedData) -> None:
        """Validate monotone constraints against the final training features."""
        constraints = self.monotone_constraints
        if constraints is None or constraints == {}:
            return
        if type(constraints) is not dict:
            raise HauteValidationError(
                "monotone_constraints must be a dict mapping feature names to -1 or 1"
            )

        invalid_names = sorted(
            (key for key in constraints if not isinstance(key, str) or not key.strip()),
            key=lambda key: (type(key).__name__, repr(key)),
        )
        if invalid_names:
            raise HauteValidationError(
                "monotone_constraints keys must be non-empty strings; invalid keys: "
                f"{invalid_names}"
            )

        invalid_directions = sorted(
            key
            for key, direction in constraints.items()
            if type(direction) is not int or direction not in (-1, 1)
        )
        if invalid_directions:
            raise HauteValidationError(
                "monotone_constraints values must be exact Python ints -1 or 1; "
                f"invalid features: {invalid_directions}"
            )

        feature_set = set(prepared.features)
        unknown_features = sorted(key for key in constraints if key not in feature_set)
        if unknown_features:
            raise HauteValidationError(
                "monotone_constraints may only reference final selected features; "
                f"unknown features: {unknown_features}"
            )

        nonnumeric_features = sorted(
            key
            for key in constraints
            if prepared.feature_dtypes.get(key) not in {"Int64", "Float64"}
        )
        if nonnumeric_features:
            dtypes = {key: prepared.feature_dtypes.get(key) for key in nonnumeric_features}
            raise HauteValidationError(
                "monotone_constraints require numeric Int64 or Float64 features; "
                f"non-numeric features: {dtypes}"
            )

    def _feature_dtypes_for_contract(self, features: list[str]) -> dict[str, str]:
        """Return a ``{feature: dtype_name}`` map for the contract/signature.

        Reads the dtype snapshot captured during ``_prepare_data``.  GLM
        narrows the feature set after prep, so we may be asked about fewer
        features than we snapshotted — iterate ``features`` to preserve
        the training order.
        """
        return {f: self._contract_feature_dtypes.get(f, "Float64") for f in features}

    def _target_dtype_for_contract(self) -> str:
        """Return the target dtype seen at data-prep time."""
        return self._contract_target_dtype or "Float64"

    def _categorical_levels_for_contract(
        self,
        features: list[str],
        cat_features: list[str],
    ) -> dict[str, list[str | None]]:
        """Return declared categorical domains for the model feature boundary."""
        from haute.modelling._feature_contract import normalise_categorical_levels

        if not self._declared_categorical_levels:
            return {}
        feature_set = set(features)
        selected_levels = {
            column: levels
            for column, levels in self._declared_categorical_levels.items()
            if column in feature_set
        }
        return normalise_categorical_levels(
            selected_levels,
            features=features,
            categorical_features=cat_features,
        )

    @property
    def training_identity_sha256(self) -> str:
        """Digest of the parsed inputs that determine what this job trains."""
        from haute.modelling._candidate_run import training_identity_sha256

        return training_identity_sha256(
            {
                "target": self.target,
                "weight": self.weight,
                "exclude": list(self.exclude),
                # Empty optional lists hash as ``None``, exactly as the
                # config builder passes them, so a canvas run and a scripted
                # run of one configuration share one identity.
                "feature_columns": list(self.feature_columns) or None,
                "fold_column": self.fold_column,
                "id_columns": list(self.id_columns) or None,
                "algorithm": self.algorithm,
                "task": self.task,
                "params": self.params,
                "evaluation": self.evaluation,
                "tuning": self.tuning,
                "metrics": list(self.metrics),
                "loss_function": self.loss_function,
                "variance_power": self.variance_power,
                "offset": self.offset,
                "monotone_constraints": self.monotone_constraints,
                "feature_weights": self.feature_weights,
                "categorical_levels": self._declared_categorical_levels or None,
                "positive_class": self.positive_class,
                "device": self.device,
            }
        )

    def _log_to_mlflow(
        self,
        result: TrainResult,
        *,
        final_params: Mapping[str, Any] | None = None,
        check_cancelled: Callable[[], None] | None = None,
    ) -> None:
        """Log this scripted run to MLflow as a contracted candidate run."""
        import uuid

        from haute._sandbox import _get_project_root
        from haute.modelling._candidate_run import (
            CandidateArtifacts,
            build_candidate_run,
            capture_provenance,
        )
        from haute.modelling._mlflow_log import log_experiment
        from haute.modelling._result_types import ModelDiagnostics

        if not self.mlflow_experiment:
            return
        if result.evaluation is None:
            raise HauteValidationError("MLflow logging requires an evaluated training run")

        diagnostics = ModelDiagnostics(
            feature_importance=result.feature_importance,
            shap_summary=result.shap_summary,
            feature_importance_loss=result.feature_importance_loss,
            double_lift=result.double_lift,
            loss_history=result.loss_history,
            ave_per_feature=result.ave_per_feature,
            residuals_histogram=result.residuals_histogram,
            residuals_stats=result.residuals_stats,
            actual_vs_predicted=result.actual_vs_predicted,
            lorenz_curve=result.lorenz_curve,
            glm_coefficients=result.glm_coefficients,
            glm_relativities=result.glm_relativities,
            glm_fit_statistics=result.glm_fit_statistics,
            glm_inference=result.glm_inference,
            glm_smooth_terms=result.glm_smooth_terms,
            glm_regularization=result.glm_regularization,
            ebm_terms=result.ebm_terms,
            lorenz_curve_perfect=result.lorenz_curve_perfect,
            pdp_data=result.pdp_data,
            final_test_metrics=result.final_test_metrics,
            selection_metrics=dict(result.evaluation.get("selection_metrics", {})),
            evaluation=result.evaluation,
            tuning=result.tuning,
            diagnostics_set=result.diagnostics_set,
        )
        model_path = Path(result.model_path)
        evidence = {
            "evaluation_plan": Path(result.evaluation["plan_path"]),
            "evaluation_results": Path(result.evaluation["results_path"]),
            "evaluation_report": Path(result.evaluation["report_path"]),
        }
        if result.tuning is not None:
            evidence.update(
                {
                    "tuning_plan": Path(result.tuning["plan_path"]),
                    "tuning_trials": Path(result.tuning["trials_path"]),
                    "tuning_report": Path(result.tuning["report_path"]),
                }
            )
        if final_params is not None:
            logged_final_params = dict(final_params)
        elif result.tuning is not None:
            logged_final_params = dict(result.tuning["final_params"])
        else:
            logged_final_params = self.params
        assert self.evaluation is not None  # result.evaluation implies the contract
        candidate = build_candidate_run(
            provenance=capture_provenance(
                job_id=uuid.uuid4().hex,
                node_label=self.name,
                training_identity_sha256=self.training_identity_sha256,
                project_root=_get_project_root(),
            ),
            algorithm=self.algorithm,
            weight=self.weight or "",
            evaluation_strategy=self.evaluation.strategy,
            validation_method=self.evaluation.validation["method"],
            evaluation_config=self.evaluation.to_plain_data(),
            evaluation_plan_sha256=str(result.evaluation["plan_sha256"]),
            final_params=logged_final_params,
            final_test_metrics=result.final_test_metrics,
            development_metrics=(result.metrics if result.diagnostics_set == "development" else {}),
            diagnostics=diagnostics,
            development_rows=result.development_rows,
            final_test_rows=result.final_test_rows,
            best_iteration=result.best_iteration,
            fit_evidence=result.fit_evidence,
            artifacts=CandidateArtifacts(
                model=model_path,
                feature_contract=model_path.parent / model_contract_filename(model_path.stem),
                evidence=evidence,
            ),
        )
        log_experiment(
            experiment_name=self.mlflow_experiment,
            candidate=candidate,
            destination=self.mlflow_destination,
            check_cancelled=check_cancelled,
        )
