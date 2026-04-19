"""Algorithm abstraction for model training."""

from __future__ import annotations

import gc
import os
import sys
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from haute._polars_utils import _malloc_trim  # noqa: F401 — re-exported for backward compat
from haute._ram_estimate import available_ram_bytes

_MEM_LOG = Path(os.environ.get("HAUTE_MEM_LOG", str(Path.home() / "training_mem.log")))


def _get_rss_mb() -> float:
    """Return current-process RSS in MB.  Returns 0.0 if unavailable.

    - **Linux**: reads ``/proc/self/status`` (current RSS, most accurate).
    - **macOS**: ``resource.getrusage`` (reports max RSS in bytes).
    - **Windows**: ``GetProcessMemoryInfo`` via ctypes (WorkingSetSize).
    """
    # Linux — /proc/self/status gives current (not peak) RSS
    if sys.platform == "linux":
        try:
            with open("/proc/self/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1]) / 1024  # kB → MB
        except OSError:
            pass

    # macOS — resource module reports max RSS in bytes
    elif sys.platform == "darwin":
        try:
            import resource

            return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)
        except (ImportError, AttributeError, ValueError):
            pass

    # Windows — kernel32 / psapi
    elif sys.platform == "win32":
        try:
            import ctypes
            import ctypes.wintypes

            class ProcessMemoryCounters(ctypes.Structure):
                _fields_ = [
                    ("cb", ctypes.wintypes.DWORD),
                    ("PageFaultCount", ctypes.wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            pmc = ProcessMemoryCounters()
            pmc.cb = ctypes.sizeof(ProcessMemoryCounters)
            handle = ctypes.windll.kernel32.GetCurrentProcess()  # type: ignore[attr-defined]
            if ctypes.windll.psapi.GetProcessMemoryInfo(  # type: ignore[attr-defined]
                handle, ctypes.byref(pmc), pmc.cb
            ):
                return float(pmc.WorkingSetSize) / (1024 * 1024)  # bytes → MB
        except (OSError, AttributeError, ImportError):
            pass

    return 0.0


def _get_available_mb() -> float:
    """Return available system RAM in MB.  Returns 0.0 if unavailable.

    Delegates to :func:`haute._ram_estimate.available_ram_bytes` for
    cross-platform detection (Linux ``/proc``, macOS ``sysconf``,
    Windows ``GlobalMemoryStatusEx``, 4 GiB fallback).
    """
    return available_ram_bytes() / (1024 * 1024)


def _mem_checkpoint(label: str) -> None:
    """Write a memory checkpoint line to the persistent log.

    Cross-platform: uses ``/proc`` on Linux, ``resource`` on macOS,
    and Win32 APIs on Windows.  Flushes immediately so the line
    survives a crash.
    """
    rss_mb = _get_rss_mb()
    avail_mb = _get_available_mb()

    ts = time.strftime("%H:%M:%S")
    entry = f"[{ts}] {label:<45} RSS={rss_mb:>9.1f} MB   Avail={avail_mb:>9.1f} MB\n"
    with open(_MEM_LOG, "a") as f:
        f.write(entry)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass  # exotic Windows builds may not support fsync on all handles


# Callback type: (iteration, total_iterations, metrics_dict) -> None
IterationCallback = Callable[[int, int, dict[str, float]], None]


@dataclass
class FitResult:
    """Result of algorithm.fit() — model plus training artifacts."""

    model: Any
    best_iteration: int | None = None
    loss_history: list[dict[str, float]] = field(default_factory=list)


class BaseAlgorithm(ABC):
    """Abstract base class for training algorithms."""

    @abstractmethod
    def fit(
        self,
        train_df: pl.DataFrame | None,
        features: list[str],
        cat_features: list[str],
        target: str,
        weight: str | None,
        params: dict[str, Any],
        task: str,
        on_iteration: IterationCallback | None = None,
        eval_df: pl.DataFrame | None = None,
        offset: str | None = None,
        monotone_constraints: dict[str, int] | None = None,
        feature_weights: dict[str, float] | None = None,
        **kwargs: Any,
    ) -> FitResult:
        """Train a model and return a FitResult.

        *train_df* may be ``None`` when a pre-built pool is passed
        via the ``pool`` keyword argument (CatBoost path).
        """

    @abstractmethod
    def predict(
        self,
        model: Any,
        df: pl.DataFrame,
        features: list[str],
    ) -> np.ndarray:
        """Generate predictions from a fitted model."""

    @abstractmethod
    def feature_importance(self, model: Any) -> list[dict[str, Any]]:
        """Return feature importances as [{feature, importance}, ...]."""

    @abstractmethod
    def save(self, model: Any, path: Path) -> None:
        """Save the model to disk."""


class _CatBoostProgressCallback:
    """CatBoost after_iteration callback that reports to an IterationCallback.

    Also collects loss history.
    """

    def __init__(
        self,
        on_iteration: IterationCallback | None,
        total_iterations: int,
        loss_history: list[dict[str, float]],
    ) -> None:
        self._on_iteration = on_iteration
        self._total = total_iterations
        self._loss_history = loss_history

    def after_iteration(self, info: Any) -> bool:
        # Log memory every 50 iterations to track growth during training
        it = info.iteration + 1
        if it <= 5 or it % 50 == 0:
            _mem_checkpoint(f"  iteration {it}/{self._total}")
        metrics: dict[str, float] = {}
        history_entry: dict[str, float] = {"iteration": float(it)}
        if info.metrics:
            for dataset_name, metric_dict in info.metrics.items():
                for metric_name, values in metric_dict.items():
                    label = (
                        metric_name if dataset_name == "learn" else f"{dataset_name}_{metric_name}"
                    )
                    if values:
                        metrics[label] = values[-1]
                    # Store in loss history with explicit train_/eval_ prefixes
                    prefix = "train" if dataset_name == "learn" else "eval"
                    if values:
                        history_entry[f"{prefix}_{metric_name}"] = values[-1]
        self._loss_history.append(history_entry)
        if self._on_iteration:
            self._on_iteration(info.iteration + 1, self._total, metrics)
        return True  # True = continue training


# User-friendly loss name → CatBoost loss_function string.
# For Tweedie, the caller appends `:variance_power=X` via resolve_loss_function().
REGRESSION_LOSSES = {"RMSE", "MAE", "Poisson", "Tweedie"}
CLASSIFICATION_LOSSES = {"Logloss", "CrossEntropy"}


def resolve_loss_function(
    loss_name: str | None,
    task: str,
    variance_power: float | None = None,
) -> str | None:
    """Map a user-facing loss name to a CatBoost ``loss_function`` param value.

    Returns ``None`` if no explicit loss was requested (CatBoost uses its own default).
    """
    if not loss_name:
        return None

    valid = REGRESSION_LOSSES if task == "regression" else CLASSIFICATION_LOSSES
    if loss_name not in valid:
        raise ValueError(
            f"Loss '{loss_name}' is not valid for task '{task}'. Choose from: {sorted(valid)}"
        )

    if loss_name == "Tweedie":
        vp = variance_power if variance_power is not None else 1.5
        return f"Tweedie:variance_power={vp}"

    return loss_name


def _build_pool(
    df: pl.DataFrame,
    features: list[str],
    cat_features: list[str] | None = None,
    *,
    target: str | None = None,
    weight: str | None = None,
    offset: str | None = None,
    y: np.ndarray | None = None,
    w: np.ndarray | None = None,
    baseline: np.ndarray | None = None,
) -> Any:
    """Memory-efficient Polars DataFrame → CatBoost Pool conversion.

    Optimisations over the naïve ``df.to_pandas()`` path:

    - Casts numeric features to float32 (halves memory vs float64).
    - Skips the Pandas intermediate when there are no categorical features.
    - Frees intermediate arrays immediately after Pool creation.

    When the caller pre-extracts ``y``/``w``/``baseline`` arrays and passes
    a features-only DataFrame, it can ``del`` the original full DataFrame
    before this function runs — avoiding triple copies (Polars + Pandas + Pool).
    """
    from catboost import Pool

    n_rows = len(df)
    tag = f"_build_pool({n_rows:,} rows)"
    _mem_checkpoint(f"{tag} start")

    from haute._mlflow_io import _prepare_predict_frame

    cat_set = set(cat_features or [])

    # Select only features present in the DataFrame (may differ when the
    # caller pre-selects columns).
    cols_to_select = [f for f in features if f in df.columns]

    # Compute cat_indices from cols_to_select (not features) so indices
    # match the actual columns in x_data after filtering.
    cat_indices = [i for i, f in enumerate(cols_to_select) if f in cat_set]
    selected = df.select(cols_to_select) if cols_to_select != df.columns else df

    x_data = _prepare_predict_frame(
        selected,
        cols_to_select,
        cat_feature_names=frozenset(cat_set),
        flavor="catboost",
    )
    del selected
    gc.collect()
    _malloc_trim()
    _mem_checkpoint(f"{tag} after data preparation")

    # Extract labels from df if not pre-supplied by the caller.
    # Cast to Float64 before to_numpy to avoid Python None values
    # (Polars integer columns with nulls produce object arrays with None,
    # which CatBoost rejects — Float64 converts nulls to NaN instead).
    if y is None and target and target in df.columns:
        y = df[target].cast(pl.Float64).to_numpy()
    if w is None and weight and weight in df.columns:
        w = df[weight].cast(pl.Float64).to_numpy()
    if baseline is None and offset and offset in df.columns:
        baseline = df[offset].cast(pl.Float64).to_numpy()

    pool = Pool(
        data=x_data,
        label=y,
        weight=w,
        cat_features=cat_indices if cat_indices else None,
        baseline=baseline,
    )
    _mem_checkpoint(f"{tag} after Pool()")
    del x_data, y, w, baseline
    gc.collect()
    _malloc_trim()
    _mem_checkpoint(f"{tag} after cleanup")

    return pool


class CatBoostAlgorithm(BaseAlgorithm):
    """CatBoost gradient boosting implementation."""

    def fit(
        self,
        train_df: pl.DataFrame | None,
        features: list[str],
        cat_features: list[str],
        target: str,
        weight: str | None,
        params: dict[str, Any],
        task: str,
        on_iteration: IterationCallback | None = None,
        eval_df: pl.DataFrame | None = None,
        offset: str | None = None,
        monotone_constraints: dict[str, int] | None = None,
        feature_weights: dict[str, float] | None = None,
        **kwargs: Any,
    ) -> FitResult:
        from catboost import CatBoostClassifier, CatBoostRegressor

        pool = kwargs.get("pool")
        eval_pool = kwargs.get("eval_pool")

        if pool is None:
            assert train_df is not None, "Either train_df or pool must be provided"
            pool = _build_pool(
                train_df,
                features,
                cat_features,
                target=target,
                weight=weight,
                offset=offset,
            )

        if eval_pool is None and eval_df is not None:
            eval_pool = _build_pool(
                eval_df,
                features,
                cat_features,
                target=target,
                weight=weight,
                offset=offset,
            )

        model_params = {**params}
        is_gpu = str(model_params.get("task_type", "")).upper() == "GPU"
        # Suppress verbose output and training log files by default
        # GPU needs verbose > 0 to record eval metrics (no callback support)
        if "verbose" not in model_params:
            model_params["verbose"] = 50 if is_gpu else 0
        if not is_gpu and "allow_writing_files" not in model_params:
            model_params["allow_writing_files"] = False

        # GPU progress: use CatBoost's metric file logging to track
        # iterations (tree_count_ is not updated during GPU training).
        _gpu_train_dir: str | None = None
        if is_gpu and on_iteration:
            import tempfile as _tf

            _gpu_train_dir = _tf.mkdtemp(prefix="catboost_gpu_")
            model_params["allow_writing_files"] = True
            model_params["train_dir"] = _gpu_train_dir

        # Enable early stopping when eval set is present and user hasn't explicitly set it
        if eval_pool is not None and "early_stopping_rounds" not in model_params:
            early_stop = model_params.pop("early_stopping_rounds", 50)
            if early_stop and early_stop > 0:
                model_params["early_stopping_rounds"] = early_stop

        # Map feature-name-based monotone_constraints to index-based list
        if monotone_constraints:
            mc_list = [monotone_constraints.get(f, 0) for f in features]
            model_params["monotone_constraints"] = mc_list

        # Map feature-name-based feature_weights to index-based list
        if feature_weights:
            fw_list = [feature_weights.get(f, 1.0) for f in features]
            model_params["feature_weights"] = fw_list

        total_iterations = model_params.get("iterations", 1000)

        if task == "classification":
            model = CatBoostClassifier(**model_params)
        else:
            model = CatBoostRegressor(**model_params)

        # Collect loss history via callback (GPU doesn't support custom callbacks)
        loss_history: list[dict[str, float]] = []

        fit_kwargs: dict[str, Any] = {}
        if not is_gpu:
            fit_kwargs["callbacks"] = [
                _CatBoostProgressCallback(on_iteration, total_iterations, loss_history),
            ]
        if eval_pool is not None:
            fit_kwargs["eval_set"] = eval_pool

        _mem_checkpoint("catboost model.fit() START")
        if is_gpu and on_iteration and _gpu_train_dir is not None:
            # GPU doesn't support callbacks — poll CatBoost's metric
            # files (learn_error.tsv / test_error.tsv) written to train_dir.
            import shutil
            import threading

            fit_error: BaseException | None = None

            def _fit_thread() -> None:
                nonlocal fit_error
                try:
                    model.fit(pool, **fit_kwargs)
                except BaseException as exc:
                    fit_error = exc

            t = threading.Thread(target=_fit_thread, daemon=True)
            t.start()

            metric_path = os.path.join(_gpu_train_dir, "learn_error.tsv")
            last_seen = 0  # number of data lines already processed

            while t.is_alive():
                t.join(timeout=2.0)
                try:
                    if os.path.exists(metric_path):
                        with open(metric_path) as mf:
                            lines = mf.readlines()
                        # First line is header (iter\tmetric_name\t...)
                        data_lines = lines[1:]
                        if len(data_lines) > last_seen:
                            for line in data_lines[last_seen:]:
                                parts = line.strip().split("\t")
                                if not parts:
                                    continue
                                try:
                                    iteration = int(parts[0]) + 1
                                    on_iteration(iteration, total_iterations, {})
                                except ValueError:
                                    pass
                            last_seen = len(data_lines)
                except OSError:
                    pass

            # Clean up metric files
            shutil.rmtree(_gpu_train_dir, ignore_errors=True)

            if fit_error is not None:
                raise fit_error
        else:
            model.fit(pool, **fit_kwargs)
        _mem_checkpoint("catboost model.fit() END")

        # Capture best iteration if early stopping was active
        best_iteration: int | None = None
        if eval_pool is not None and hasattr(model, "best_iteration_"):
            best_iteration = model.best_iteration_

        # On GPU, reconstruct loss history from the model's eval results
        if is_gpu and eval_pool is not None and hasattr(model, "evals_result_"):
            evals = model.evals_result_
            if "validation" in evals:
                for metric_name, values in evals["validation"].items():
                    for i, v in enumerate(values):
                        if i >= len(loss_history):
                            loss_history.append({"iteration": i})
                        loss_history[i][f"eval_{metric_name}"] = v
            if "learn" in evals:
                for metric_name, values in evals["learn"].items():
                    for i, v in enumerate(values):
                        if i >= len(loss_history):
                            loss_history.append({"iteration": i})
                        loss_history[i][f"train_{metric_name}"] = v

        return FitResult(
            model=model,
            best_iteration=best_iteration,
            loss_history=loss_history,
        )

    def predict(
        self,
        model: Any,
        df: pl.DataFrame,
        features: list[str],
    ) -> np.ndarray:
        from haute._mlflow_io import _prepare_predict_frame

        selected = df.select(features)
        cat_cols = frozenset(
            c for c in features if selected[c].dtype in (pl.Utf8, pl.Categorical, pl.String)
        )
        x_data = _prepare_predict_frame(
            selected,
            features,
            cat_feature_names=cat_cols,
            flavor="catboost",
        )
        del selected
        from catboost import CatBoostClassifier

        if isinstance(model, CatBoostClassifier):
            preds: np.ndarray = model.predict_proba(x_data)[:, 1]
        else:
            preds = model.predict(x_data).flatten()
        del x_data
        return preds

    def feature_importance(self, model: Any) -> list[dict[str, Any]]:
        names = model.feature_names_
        importances = model.get_feature_importance()
        pairs = sorted(
            zip(names, importances),
            key=lambda x: x[1],
            reverse=True,
        )
        return [{"feature": name, "importance": float(imp)} for name, imp in pairs]

    def feature_importance_typed(
        self,
        model: Any,
        pool: Any,
        type_name: str,
    ) -> list[dict[str, Any]]:
        """Get feature importance using a specific CatBoost importance type.

        Supported types: PredictionValuesChange, LossFunctionChange, ShapValues.
        """
        names = model.feature_names_
        importances = model.get_feature_importance(data=pool, type=type_name)
        pairs = sorted(
            zip(names, importances),
            key=lambda x: x[1],
            reverse=True,
        )
        return [{"feature": name, "importance": float(imp)} for name, imp in pairs]

    def shap_summary(
        self,
        model: Any,
        df: pl.DataFrame,
        features: list[str],
        cat_features: list[str] | None = None,
        max_rows: int = 1000,
    ) -> list[dict[str, Any]]:
        """Compute mean |SHAP| per feature using CatBoost's native SHAP.

        Subsamples to max_rows for performance. Returns
        [{feature, mean_abs_shap}, ...] sorted by importance desc.
        """
        sample = df.sample(min(len(df), max_rows), seed=42) if len(df) > max_rows else df
        pool = _build_pool(sample, features, cat_features)

        # CatBoost ShapValues returns shape (n_samples, n_features + 1), last col is base value
        shap_values = model.get_feature_importance(data=pool, type="ShapValues")
        del pool
        # Ensure 2D and drop the base value column
        if shap_values.ndim == 1:
            shap_values = shap_values.reshape(1, -1)
        shap_values = shap_values[:, :-1]

        mean_abs = np.abs(shap_values).mean(axis=0)
        pairs = sorted(zip(features, mean_abs), key=lambda x: x[1], reverse=True)
        return [{"feature": name, "mean_abs_shap": float(val)} for name, val in pairs]

    def save(self, model: Any, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        model.save_model(str(path))


ALGORITHM_REGISTRY: dict[str, type[BaseAlgorithm]] = {
    "catboost": CatBoostAlgorithm,
}

# Register GLM if RustyStats is installed (lazy import keeps it optional)
try:
    from haute.modelling._rustystats import GLMAlgorithm

    ALGORITHM_REGISTRY["glm"] = GLMAlgorithm
except ImportError:
    pass
