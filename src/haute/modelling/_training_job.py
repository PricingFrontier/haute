"""Core TrainingJob class — orchestrates the full training pipeline."""

from __future__ import annotations

import gc
import os
import tempfile
from collections.abc import Callable, Iterable, Mapping
from contextlib import AbstractContextManager, nullcontext
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import polars as pl

from haute._execution_context import ExecutionContext, ExecutionProfile
from haute._logging import get_logger
from haute._polars_utils import streaming_collect
from haute.modelling._algorithms import (
    ALGORITHM_REGISTRY,
    IterationCallback,
    _malloc_trim,
    resolve_loss_function,
)
from haute.modelling._metrics import compute_metrics
from haute.modelling._split import (
    PARTITION_HOLDOUT,
    PARTITION_TRAIN,
    PARTITION_VALIDATION,
    SplitConfig,
    split_mask,
)

logger = get_logger(component="training_job")

_MODEL_EXT_MAP: dict[str, str] = {"catboost": ".cbm", "glm": ".rsglm"}


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
        df = streaming_collect(lf, profile=ExecutionProfile.TRAINING_PREP)
    _training_checkpoint(
        execution_context,
        label=f"after_{stage_name}",
    )
    return df


def _polars_dtype_name(dtype: Any) -> str:
    """Canonical dtype name used by the MLflow signature and feature contract.

    Collapses Polars' many integer/float variants to the four dtypes the
    ``build_signature`` helper understands (``Int64``, ``Float64``,
    ``String``, ``Boolean``).  Unknown dtypes return their str() form so
    bugs are loud at contract-build time.
    """
    if dtype == pl.Boolean:
        return "Boolean"
    if dtype in (pl.Utf8, pl.String, pl.Categorical):
        return "String"
    if dtype.is_integer() if hasattr(dtype, "is_integer") else False:
        return "Int64"
    if dtype.is_float() if hasattr(dtype, "is_float") else False:
        return "Float64"
    return str(dtype)


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
    # ``test_rows`` is a legacy name that carries the VALIDATION-set count
    # (``split_result.n_validation``), kept for API/frontend back-compat. See
    # schemas.TrainResponse and the semantics pin in tests/test_modelling.py.
    test_rows: int
    features: list[str]
    cat_features: list[str]
    holdout_rows: int = 0
    holdout_metrics: dict[str, float] = field(default_factory=dict)
    diagnostics_set: str = "validation"  # "train" | "validation" | "holdout"
    best_iteration: int | None = None
    loss_history: list[dict[str, float]] = field(default_factory=list)
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
    glm_regularization_path: dict[str, Any] | None = None
    # Optional-diagnostic failures surfaced to callers so a degraded
    # run (SHAP/PDP/GLM diagnostics missing) is visible in the UI and
    # in test suites, instead of being silently swallowed.
    diagnostics_errors: list[dict[str, str]] = field(default_factory=list)


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
    glm_regularization_path: dict[str, Any] | None = None
    # Optional-diagnostic failures (SHAP, PDP, GLM diagnostics) —
    # surfaced rather than silently swallowed.
    diagnostics_errors: list[dict[str, str]] = field(default_factory=list)


class TrainingJob:
    """Orchestrates model training: split, fit, evaluate, optionally log to MLflow.

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
        Split configuration (strategy, validation_size, seed, etc.).
    metrics : list[str] | None
        Metrics to compute (default: ["gini", "rmse"]).
    mlflow_experiment : str | None
        MLflow experiment path. If set and mlflow is importable, logs the run.
    model_name : str | None
        Optional MLflow registered model name.
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
        model_name: str | None = None,
        output_dir: str = "outputs",
        loss_function: str | None = None,
        variance_power: float | None = None,
        offset: str | None = None,
        monotone_constraints: dict[str, int] | None = None,
        feature_weights: dict[str, float] | None = None,
        categorical_levels: Mapping[str, Iterable[str | None]] | None = None,
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
        self.metrics = metrics or (["gini", "rmse"] if task == "regression" else ["auc", "logloss"])
        self.mlflow_experiment = mlflow_experiment
        self.model_name = model_name
        self.output_dir = output_dir
        self.loss_function = loss_function
        self.variance_power = variance_power
        self.offset = offset
        self.monotone_constraints = monotone_constraints
        self.feature_weights = feature_weights
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

    def run(
        self,
        progress: Callable[[str, float], None] | None = None,
        on_iteration: IterationCallback | None = None,
        check_cancelled: Callable[[], None] | None = None,
        execution_context: ExecutionContext | None = None,
    ) -> TrainResult:
        """Execute the full training pipeline.

        Parameters
        ----------
        progress : callable | None
            Optional callback ``(message, fraction)`` for progress reporting.
        on_iteration : callable | None
            Optional callback ``(iteration, total, metrics_dict)`` called
            after each training iteration.

        Returns
        -------
        TrainResult
            Metrics, feature importances, model path, and split sizes.
        """

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

            # GLM: narrow features to only the terms the user selected.
            # CatBoost uses all features; GLM should only carry the columns
            # referenced by its terms dict so we don't build a massive
            # design matrix or load unnecessary columns from parquet.
            if self.algorithm == "glm":
                glm_terms = self.params.get("terms", {})
                if glm_terms:
                    term_names = set(glm_terms.keys())
                    missing = term_names - set(prepared.features)
                    if missing:
                        raise ValueError(
                            f"GLM terms reference columns not found in training data: "
                            f"{sorted(missing)}. Available columns: {prepared.features[:20]}"
                            + ("..." if len(prepared.features) > 20 else "")
                        )
                    prepared = _PreparedData(
                        data_path=prepared.data_path,
                        owns_tmp=prepared.owns_tmp,
                        features=[f for f in prepared.features if f in term_names],
                        cat_features=[f for f in prepared.cat_features if f in term_names],
                        total_rows=prepared.total_rows,
                        feature_dtypes={
                            f: dt for f, dt in prepared.feature_dtypes.items() if f in term_names
                        },
                        categorical_levels={
                            f: levels
                            for f, levels in prepared.categorical_levels.items()
                            if f in term_names
                        },
                        target_dtype=prepared.target_dtype,
                        target_null_count=prepared.target_null_count,
                    )
                    _report(
                        f"GLM: using {len(prepared.features)} term features "
                        f"({len(prepared.cat_features)} categorical)",
                        0.12,
                    )
                    if not prepared.features:
                        raise ValueError(
                            "GLM: no valid features remaining after matching terms to data "
                            "columns. Check that your factor names match column names in the "
                            "training data."
                        )

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
                    categorical_levels=prepared.categorical_levels,
                )

            result = TrainResult(
                metrics=metrics_result.metrics,
                feature_importance=metrics_result.importance,
                model_path=str(model_path),
                train_rows=split_result.n_train,
                test_rows=split_result.n_validation,
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
                glm_regularization_path=metrics_result.glm_regularization_path,
                diagnostics_errors=metrics_result.diagnostics_errors,
            )

            if self.mlflow_experiment:
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
                raise ValueError("DataFrame is empty — cannot train on zero rows")
            schema_lf = pl.scan_parquet(data_path)
            schema_df = _training_streaming_collect(
                schema_lf.head(0),
                stage_name="training_schema_collect",
                execution_context=execution_context,
            )
            self._validate_columns(schema_df)

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
                    raise ValueError(
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

            # Derive features from schema
            features, cat_features = self._derive_features(schema_df)
            # Snapshot dtypes before we drop the schema frame — downstream
            # consumers (MLflow signature, feature contract) need them.
            feature_dtypes = {f: _polars_dtype_name(schema_df[f].dtype) for f in features}
            categorical_levels = self._categorical_levels_for_contract(
                features,
                cat_features,
            )
            target_dtype = (
                _polars_dtype_name(schema_df[self.target].dtype)
                if self.target in schema_df.columns
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
            raise ValueError(
                f"Unknown algorithm: {self.algorithm}. Available: {list(ALGORITHM_REGISTRY.keys())}"
            )
        algo = algo_cls()

        # Resolve loss function and inject into params
        fit_params = {**self.params}

        # GLM: pack all GLM-specific config into fit_params for the algorithm
        is_glm = self.algorithm == "glm"
        if not is_glm:
            resolved_loss = resolve_loss_function(
                self.loss_function,
                self.task,
                self.variance_power,
            )
            if resolved_loss:
                fit_params["loss_function"] = resolved_loss

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

        eval_df = None
        if has_validation:
            _report("Loading validation data", 0.25)
            eval_df = _training_streaming_collect(
                self._scan_with_columns(data_path, features)
                .filter(pl.col("_partition") == PARTITION_VALIDATION)
                .drop("_partition"),
                stage_name="training_validation_partition_materialise",
                execution_context=execution_context,
            )
            _mem_checkpoint(f"read validation partition ({len(eval_df):,} rows)")

        if is_glm:
            # GLM: pass DataFrames directly (no Pool conversion needed)
            _report("Fitting GLM", 0.3)
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
                )
            _mem_checkpoint("glm algo.fit() returned")
            del train_df, eval_df
            gc.collect()
            _malloc_trim()
        else:
            # CatBoost: build memory-efficient pools, then fit
            from haute.modelling._algorithms import _build_pool

            train_y = train_df[self.target].cast(pl.Float64).to_numpy()
            train_w = train_df[self.weight].cast(pl.Float64).to_numpy() if self.weight else None
            train_baseline = (
                train_df[self.offset].cast(pl.Float64).to_numpy() if self.offset else None
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
            if eval_df is not None:
                _report("Building eval pool", 0.25)
                val_y = eval_df[self.target].cast(pl.Float64).to_numpy()
                val_w = eval_df[self.weight].cast(pl.Float64).to_numpy() if self.weight else None
                val_baseline = (
                    eval_df[self.offset].cast(pl.Float64).to_numpy() if self.offset else None
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
        if self.algorithm != "catboost":
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
        y_pred = algo.predict(model, diag_df, features)
        w = diag_df[self.weight].to_numpy() if self.weight else None

        # Primary metrics from the diagnostics set
        vp = self.variance_power
        metrics = compute_metrics(
            y_true,
            y_pred,
            w,
            self.metrics,
            variance_power=vp,
        )

        # When holdout is present, diagnostics were computed on holdout.
        # Also compute validation metrics separately so both are available.
        holdout_metrics: dict[str, float] = {}
        if diagnostics_set == "holdout":
            holdout_metrics = metrics
            # Compute validation metrics separately if a validation set exists
            if has_validation:
                val_df = self._read_partition(
                    data_path,
                    PARTITION_VALIDATION,
                    columns=glm_columns,
                    execution_context=execution_context,
                    stage_name="training_validation_metrics_materialise",
                )
                val_y_true = val_df[self.target].to_numpy()
                val_y_pred = algo.predict(model, val_df, features)
                val_w = val_df[self.weight].to_numpy() if self.weight else None
                metrics = compute_metrics(
                    val_y_true,
                    val_y_pred,
                    val_w,
                    self.metrics,
                    variance_power=vp,
                )
                del val_df

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
        _report("Computing SHAP values", 0.85)
        shap_summary: list[dict[str, float]] = []
        feature_importance_loss: list[dict[str, Any]] = []
        if hasattr(algo, "shap_summary"):
            try:
                shap_summary = algo.shap_summary(model, diag_df, features, cat_features)
            except Exception as exc:
                _record_diag_error(diagnostics_errors, "shap", exc)
        if hasattr(algo, "feature_importance_typed"):
            try:
                _diag_pool = _build_pool(
                    diag_df,
                    features,
                    cat_features,
                    target=self.target,
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
            pdp_data = compute_pdp(model, algo, diag_df, sorted_features, cat_features)
        except Exception as exc:
            _record_diag_error(diagnostics_errors, "pdp", exc)

        # ── GLM-specific diagnostics (all OPTIONAL) ──
        glm_coefficients: list[dict[str, Any]] = []
        glm_relativities: list[dict[str, Any]] = []
        glm_fit_statistics: dict[str, float] = {}
        glm_regularization_path: dict[str, Any] | None = None

        if hasattr(algo, "coefficients_table"):
            try:
                glm_coefficients = algo.coefficients_table(model)
            except Exception as exc:
                _record_diag_error(diagnostics_errors, "glm_coefficients", exc)
        if hasattr(algo, "relativities"):
            try:
                glm_relativities = algo.relativities(model)
            except Exception as exc:
                _record_diag_error(diagnostics_errors, "glm_relativities", exc)
        if hasattr(algo, "fit_statistics"):
            try:
                glm_fit_statistics = algo.fit_statistics(model)
            except Exception as exc:
                _record_diag_error(diagnostics_errors, "glm_fit_statistics", exc)
        if hasattr(model, "regularization_path") and model.regularization_path:
            try:
                rp = model.regularization_path
                glm_regularization_path = {
                    "selected_alpha": float(getattr(rp, "selected_alpha", 0)),
                    "n_nonzero": int(model.n_nonzero()) if hasattr(model, "n_nonzero") else 0,
                }
            except Exception as exc:
                _record_diag_error(diagnostics_errors, "glm_regularization_path", exc)

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
            glm_coefficients=glm_coefficients,
            glm_relativities=glm_relativities,
            glm_fit_statistics=glm_fit_statistics,
            glm_regularization_path=glm_regularization_path,
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
        from haute.modelling._feature_contract import (
            CONTRACT_FILENAME,
            build_contract,
            save_contract,
        )

        ext = _MODEL_EXT_MAP.get(self.algorithm, ".model")
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
            )
            contract_path = output_dir / model_contract_filename(self.name)
            save_contract(contract, contract_path)

            legacy_contract_path = output_dir / CONTRACT_FILENAME
            if legacy_contract_path.exists():
                # Pre-4b.9 versions wrote one SHARED contract per output
                # dir; with more than one model it silently described
                # whichever model trained last.  Never trust, rewrite, or
                # delete the leftover — warn loudly so operators repoint
                # any feature_contract_path config at the per-model file.
                logger.warning(
                    "legacy_shared_feature_contract_present",
                    legacy_path=str(legacy_contract_path),
                    per_model_path=str(contract_path),
                    model_name=self.name,
                )
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
            raise ValueError(f"Target column '{self.target}' not found. Available: {df.columns}")
        if self.weight and self.weight not in df.columns:
            raise ValueError(f"Weight column '{self.weight}' not found. Available: {df.columns}")
        if self.offset and self.offset not in df.columns:
            raise ValueError(f"Offset column '{self.offset}' not found. Available: {df.columns}")
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
                raise ValueError(
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
            raise ValueError(
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

    def _log_to_mlflow(
        self,
        result: TrainResult,
        *,
        check_cancelled: Callable[[], None] | None = None,
    ) -> None:
        """Log training run to MLflow (conditional import).

        Delegates to the standalone ``log_experiment()`` function so the
        same logic is reused by the "Log to MLflow" button in the UI.
        """
        from haute.modelling._mlflow_log import log_experiment
        from haute.modelling._result_types import (
            ModelCardMetadata,
            ModelDiagnostics,
        )

        if not self.mlflow_experiment:
            return

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
            glm_regularization_path=result.glm_regularization_path,
            lorenz_curve_perfect=result.lorenz_curve_perfect,
            pdp_data=result.pdp_data,
            holdout_metrics=result.holdout_metrics,
            diagnostics_set=result.diagnostics_set,
        )
        # Populate the feature-contract metadata so ``log_experiment``
        # can attach an MLflow ``ModelSignature`` to the logged model.
        feature_types = self._feature_dtypes_for_contract(result.features)
        metadata = ModelCardMetadata(
            algorithm=self.algorithm,
            task=self.task,
            train_rows=result.train_rows,
            test_rows=result.test_rows,
            holdout_rows=result.holdout_rows,
            features=result.features,
            split_config=asdict(self.split_config) if self.split_config else {},
            best_iteration=result.best_iteration,
            feature_types=feature_types,
            categorical_features=list(result.cat_features),
            target_name=self.target,
            target_type=self._target_dtype_for_contract(),
        )

        log_experiment(
            experiment_name=self.mlflow_experiment,
            run_name=self.name,
            metrics=result.metrics,
            params={
                "algorithm": self.algorithm,
                "task": self.task,
                "target": self.target,
                "weight": self.weight or "",
                "split_strategy": self.split_config.strategy,
                "validation_size": self.split_config.validation_size,
                "holdout_size": self.split_config.holdout_size,
                **{f"param_{k}": v for k, v in self.params.items()},
            },
            diagnostics=diagnostics,
            metadata=metadata,
            model_path=result.model_path or None,
            model_name=self.model_name,
            check_cancelled=check_cancelled,
        )
