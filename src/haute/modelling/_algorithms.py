"""Algorithm abstraction for model training."""

from __future__ import annotations

import gc
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from haute._host_memory import available_ram_bytes
from haute._logging import get_logger
from haute._polars_utils import _malloc_trim
from haute.errors import HauteValidationError
from haute.modelling._algorithm_base import BaseAlgorithm, FitResult, IterationCallback

logger = get_logger(component="algorithms")

# Explicit override for the memory-log destination; tests patch this.  When
# None, the destination is resolved per write by _mem_log_path() so that
# HAUTE_MEM_LOG (or a cwd change) takes effect without a re-import.
_MEM_LOG: Path | None = None

_DEFAULT_MEM_LOG = Path(".haute_cache") / "training_mem.log"


def _mem_log_path() -> Path:
    """Resolve the memory-log destination at write time.

    Precedence: the ``_MEM_LOG`` override if set, else the ``HAUTE_MEM_LOG``
    environment variable, else ``.haute_cache/training_mem.log`` under the
    project root (haute's runtime cwd).
    """
    if _MEM_LOG is not None:
        return _MEM_LOG
    env = os.environ.get("HAUTE_MEM_LOG")
    return Path(env) if env else _DEFAULT_MEM_LOG


def _get_rss_mb() -> float:
    """Return current-process RSS in MB.  Returns 0.0 if unavailable.

    - **Linux**: reads ``/proc/self/status`` (current RSS, most accurate).
    - **macOS**: ``resource.getrusage`` (reports max RSS in bytes).
    - **Windows**: ``GetProcessMemoryInfo`` via ctypes (WorkingSetSize).
    """
    # Linux — /proc/self/status gives current (not peak) RSS
    if sys.platform == "linux":
        try:
            with open("/proc/self/status", encoding="utf-8") as f:
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
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb):
                return float(pmc.WorkingSetSize) / (1024 * 1024)  # bytes → MB
        except (OSError, AttributeError, ImportError):
            pass

    return 0.0


def _get_available_mb() -> float:
    """Return available system RAM in MB.  Returns 0.0 if unavailable.

    Delegates to :func:`haute._host_memory.available_ram_bytes` for
    cross-platform detection (Linux ``/proc``, macOS Mach VM counters,
    Windows ``GlobalMemoryStatusEx``; no fabricated fallback).
    """
    available_bytes = available_ram_bytes()
    return 0.0 if available_bytes is None else available_bytes / (1024 * 1024)


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
    log_path = _mem_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(entry)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass  # exotic Windows builds may not support fsync on all handles


# CatBoost model-metadata keys recording the offset/baseline column a model
# was trained with and how it enters the raw score.  The .cbm format has no
# native baseline memory, so both are stamped into the model's metadata at fit
# time and read back by every loader — making the artifact self-describing the
# same way a RustyStats model carries its exposure or offset spec.
CATBOOST_OFFSET_METADATA_KEY = "haute_offset_column"
CATBOOST_OFFSET_LINK_METADATA_KEY = "haute_offset_link"
#: JSON ``[negative, positive]`` for a binary classifier Haute trained.
CATBOOST_CLASS_LABELS_METADATA_KEY = "haute_class_labels"
OFFSET_LINKS: frozenset[str] = frozenset({"log", "identity"})
# CatBoost losses whose raw score is on the log scale, so an offset column is an
# exposure multiplier that enters the baseline as log(offset).
_LOG_LINK_CATBOOST_LOSSES: frozenset[str] = frozenset({"Poisson", "Tweedie"})


def catboost_offset_link(loss_function: str | None) -> str:
    """How a CatBoost loss applies an offset: ``log`` multiplies, ``identity`` adds."""
    name = str(loss_function or "").partition(":")[0]
    return "log" if name in _LOG_LINK_CATBOOST_LOSSES else "identity"


def offset_baseline(values: Any, *, column: str, link: str, context: str) -> np.ndarray:
    """Transform offset column values into a raw-score baseline.

    Under a log link the offset is a positive exposure multiplier and enters
    as ``log(offset)``; null, zero, or negative values are refused rather than
    producing an infinite or undefined baseline. Other links add it verbatim.
    """
    array = np.asarray(values, dtype=np.float64)
    if link == "identity":
        return array
    if link != "log":
        raise HauteValidationError(f"{context}: unknown offset link {link!r}")
    with np.errstate(invalid="ignore"):
        invalid = int(np.count_nonzero(~np.isfinite(array) | (array <= 0)))
    if invalid:
        raise HauteValidationError(
            f"{context}: offset column {column!r} must be positive under a log link, but "
            f"{invalid:,} rows are null, zero, or negative."
        )
    return np.log(array)


def _extract_offset_baseline(
    df: pl.DataFrame,
    offset: str,
    *,
    link: str,
    context: str,
) -> np.ndarray:
    """Return the offset column as a raw-score baseline, loud when absent."""
    if offset not in df.columns:
        raise HauteValidationError(
            f"{context}: offset column {offset!r} is missing from the input "
            f"data. The model was trained with this offset and predictions "
            f"without it would be silently mis-scaled."
        )
    return offset_baseline(
        df[offset].cast(pl.Float64).to_numpy(),
        column=offset,
        link=link,
        context=context,
    )


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
        # CatBoost's callback reports completed iterations, starting at one.
        it = info.iteration
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
            self._on_iteration(it, self._total, metrics)
        return True  # True = continue training


# User-friendly loss name → CatBoost loss_function string.
# For Tweedie, the caller appends `:variance_power=X` via resolve_loss_function().
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

    from haute.modelling._descriptors import CATBOOST

    CATBOOST.native_loss(task, loss_name)

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
    offset_link: str | None = None,
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
    A pre-extracted ``baseline`` is already on the raw-score scale; an
    ``offset`` column is transformed by ``offset_link``.
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
    if baseline is None and offset:
        if offset_link is None:
            raise HauteValidationError(
                "CatBoost pool: an offset column needs its link to build the baseline"
            )
        baseline = _extract_offset_baseline(
            df,
            offset,
            link=offset_link,
            context="CatBoost pool",
        )

    # Always pass feature names explicitly: the numeric-only fast path hands
    # CatBoost a bare numpy array, and without names the saved model reports
    # feature_names_ == ['0', '1', ...] — which the name-based validation in
    # haute._model_scorer._validate_features then rejects for every named
    # scoring frame.
    pool = Pool(
        data=x_data,
        label=y,
        weight=w,
        cat_features=cat_indices if cat_indices else None,
        baseline=baseline,
        # Pin the real column names onto the Pool.  The numeric-only fast
        # path hands CatBoost a bare numpy matrix, which otherwise bakes
        # positional names ('0', '1', …) into the saved .cbm — breaking
        # name-based scoring when the model is reloaded from disk (the
        # scorer's feature validation matches model.feature_names_ against
        # the input schema).  Passing the names is a no-op for the pandas
        # (categorical) path, which already carries them.
        feature_names=list(cols_to_select),
    )
    _mem_checkpoint(f"{tag} after Pool()")
    del x_data, y, w, baseline
    gc.collect()
    _malloc_trim()
    _mem_checkpoint(f"{tag} after cleanup")

    return pool


# GPU fit-thread lifecycle bounds (remediation 4b.7).  The polling
# interval doubles as the cancellation latency; the abort join timeout is
# how long a cancelled run waits for the fit worker before declaring it a
# zombie.  CatBoost cannot be interrupted mid-fit, but once cancellation
# stops consuming progress the worker normally finishes its in-flight
# work well inside this window.
_GPU_FIT_POLL_INTERVAL_SECONDS = 2.0
_GPU_FIT_ABORT_JOIN_TIMEOUT_SECONDS = 30.0


def _run_gpu_fit_with_metric_polling(
    fit: Callable[[], object],
    *,
    train_dir: str,
    on_iteration: IterationCallback,
    total_iterations: int,
    poll_interval_seconds: float = _GPU_FIT_POLL_INTERVAL_SECONDS,
    abort_join_timeout_seconds: float = _GPU_FIT_ABORT_JOIN_TIMEOUT_SECONDS,
) -> None:
    """Run *fit* on a worker thread, polling CatBoost metric files for progress.

    GPU training doesn't support custom callbacks, so progress comes from
    tailing ``learn_error.tsv`` in *train_dir* and reporting each new data
    line through *on_iteration*.

    Lifecycle contract (remediation 4b.7 — no zombie fit thread, no leaked
    ``train_dir``):

    * normal completion or a fit-side error: the metric file is drained
      once more after the worker exits, *train_dir* is removed, and any
      fit error is re-raised;
    * *on_iteration* raising (training cancellation in the live server):
      the worker is joined with ``abort_join_timeout_seconds`` before the
      exception propagates.  If the worker exits in time, *train_dir* is
      removed; if it does not, the exception is annotated with the zombie
      thread and the retained *train_dir* (removing files under a live
      CatBoost writer would half-destroy its working dir) and an
      error-level log is emitted — the abandon is loud, never silent.
    """
    import shutil
    import threading

    fit_error: BaseException | None = None

    def _fit_thread() -> None:
        nonlocal fit_error
        try:
            fit()
        except BaseException as exc:
            fit_error = exc

    worker = threading.Thread(
        target=_fit_thread,
        name="catboost-gpu-fit",
        daemon=True,
    )
    worker.start()

    metric_path = Path(train_dir) / "learn_error.tsv"
    last_seen = 0  # number of data lines already processed

    def _poll_metric_file() -> None:
        nonlocal last_seen
        try:
            if not metric_path.exists():
                return
            with open(metric_path, encoding="utf-8") as mf:
                lines = mf.readlines()
        except OSError:
            return  # metric file mid-write — pick it up on the next poll
        # First line is header (iter\tmetric_name\t...)
        data_lines = lines[1:]
        if len(data_lines) <= last_seen:
            return
        for line in data_lines[last_seen:]:
            parts = line.strip().split("\t")
            if not parts:
                continue
            try:
                iteration = int(parts[0]) + 1
            except ValueError:
                continue
            # Outside the parse guard: a cancellation raised by the
            # callback must propagate, never be swallowed as a bad line.
            on_iteration(iteration, total_iterations, {})
        last_seen = len(data_lines)

    try:
        while worker.is_alive():
            worker.join(timeout=poll_interval_seconds)
            _poll_metric_file()
        # Fast fits can complete before the first liveness check, so
        # drain the metric file once more after the worker exits.
        _poll_metric_file()
    except BaseException as poll_exc:
        # on_iteration raised — in the live server this is the training
        # cancellation (BackgroundJobStoppedError).  CatBoost cannot be
        # interrupted mid-fit; give the worker a bounded window to finish
        # instead of silently abandoning it.
        worker.join(timeout=abort_join_timeout_seconds)
        if worker.is_alive():
            logger.error(
                "gpu_fit_thread_zombie_after_cancel",
                train_dir=train_dir,
                join_timeout_seconds=abort_join_timeout_seconds,
                thread_name=worker.name,
            )
            poll_exc.add_note(
                f"CatBoost GPU fit thread {worker.name!r} was still running "
                f"{abort_join_timeout_seconds:g}s after cancellation; train_dir "
                f"{train_dir} was left in place for the live writer."
            )
            raise
        shutil.rmtree(train_dir, ignore_errors=True)
        raise

    # Clean up metric files
    shutil.rmtree(train_dir, ignore_errors=True)

    if fit_error is not None:
        raise fit_error


def _defined_importances(importances: Any) -> np.ndarray:
    """Score features CatBoost cannot normalise as contributing nothing.

    PredictionValuesChange normalises by the model's total prediction change.
    A model whose few trees leave that total at zero (e.g. a one-tree refit at
    the validation-selected iteration) yields 0/0 = NaN for every split
    feature, although no feature moves the prediction. Infinities are left in
    place so a genuinely broken result still fails the finite-result check.
    """
    values = np.asarray(importances, dtype=float)
    return np.where(np.isnan(values), 0.0, values)


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
        offset_link = catboost_offset_link(params.get("loss_function"))

        if pool is None:
            assert train_df is not None, "Either train_df or pool must be provided"
            pool = _build_pool(
                train_df,
                features,
                cat_features,
                target=target,
                weight=weight,
                offset=offset,
                offset_link=offset_link,
            )

        if eval_pool is None and eval_df is not None:
            eval_pool = _build_pool(
                eval_df,
                features,
                cat_features,
                target=target,
                weight=weight,
                offset=offset,
                offset_link=offset_link,
            )

        model_params = {**params}
        threads = kwargs.get("threads")
        if threads is not None:
            model_params["thread_count"] = threads
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

        from haute.modelling._descriptors import CATBOOST, round_ceiling

        total_iterations = round_ceiling(CATBOOST, model_params, 1000)

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
            # GPU doesn't support callbacks — run the fit on a worker
            # thread and poll CatBoost's metric files (learn_error.tsv)
            # written to train_dir.  The helper owns the cancel-safe
            # thread + train_dir lifecycle (remediation 4b.7).
            _run_gpu_fit_with_metric_polling(
                lambda: model.fit(pool, **fit_kwargs),
                train_dir=_gpu_train_dir,
                on_iteration=on_iteration,
                total_iterations=total_iterations,
            )
        else:
            model.fit(pool, **fit_kwargs)
        _mem_checkpoint("catboost model.fit() END")

        # Record the offset column and its link on the model so saved .cbm
        # artifacts are self-describing: predict/serve must re-supply this
        # baseline exactly as it was built for the fit.
        if offset:
            metadata = model.get_metadata()
            metadata[CATBOOST_OFFSET_METADATA_KEY] = offset
            metadata[CATBOOST_OFFSET_LINK_METADATA_KEY] = offset_link
        # The job trains on the target encoded as positive = 1; record which
        # original labels those codes stand for so served labels are original.
        class_labels = kwargs.get("class_labels")
        if task == "classification" and class_labels is not None:
            import json

            fitted = [float(value) for value in getattr(model, "classes_", [])]
            if fitted != [0.0, 1.0]:
                raise HauteValidationError(
                    "CatBoost did not fit the encoded classes in order (negative 0, positive 1); "
                    f"it reported {fitted}. Remove any class-order parameters and retrain."
                )

            model.get_metadata()[CATBOOST_CLASS_LABELS_METADATA_KEY] = json.dumps(
                list(class_labels)
            )

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

        rounds_fitted = getattr(model, "tree_count_", None)
        rounds_fitted = rounds_fitted if isinstance(rounds_fitted, int) else None
        rounds_configured = total_iterations if isinstance(total_iterations, int) else None
        stopping_reason = (
            "validation"
            if eval_pool is not None
            and rounds_fitted is not None
            and rounds_configured is not None
            and rounds_fitted < rounds_configured
            else "none"
        )
        return FitResult(
            model=model,
            best_iteration=best_iteration,
            loss_history=loss_history,
            rounds_configured=rounds_configured,
            rounds_fitted=rounds_fitted,
            stopping_reason=stopping_reason,
        )

    def predict(
        self,
        model: Any,
        df: pl.DataFrame,
        features: list[str],
        offset: str | None = None,
    ) -> np.ndarray:
        from haute._mlflow_io import _prepare_predict_frame

        selected = df.select(features)
        cat_cols = frozenset(
            c for c in features if selected[c].dtype in (pl.Utf8, pl.Categorical, pl.String)
        )
        x_data: Any = _prepare_predict_frame(
            selected,
            features,
            cat_feature_names=cat_cols,
            flavor="catboost",
        )
        del selected
        if offset:
            # CatBoost only applies a baseline when it arrives inside a Pool;
            # a bare matrix predict silently scores from baseline 0.
            from catboost import Pool

            from haute._mlflow_io import _catboost_offset_link

            link = _catboost_offset_link(model)
            if link is None:
                raise HauteValidationError(
                    f"CatBoost predict: the model records no offset, but offset column "
                    f"{offset!r} was supplied"
                )
            baseline = _extract_offset_baseline(
                df,
                offset,
                link=link,
                context="CatBoost predict",
            )
            cat_indices = [i for i, f in enumerate(features) if f in cat_cols]
            x_data = Pool(
                data=x_data,
                cat_features=cat_indices if cat_indices else None,
                baseline=baseline,
            )
        from catboost import CatBoostClassifier

        if isinstance(model, CatBoostClassifier):
            preds: np.ndarray = model.predict_proba(x_data)[:, 1]
        else:
            preds = model.predict(x_data).flatten()
        del x_data
        return preds

    def feature_importance(self, model: Any) -> list[dict[str, Any]]:
        names = model.feature_names_
        importances = _defined_importances(model.get_feature_importance())
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
        importances = _defined_importances(model.get_feature_importance(data=pool, type=type_name))
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

# The XGBoost adapter imports the engine only inside fit/predict/load.
from haute.modelling._xgboost import XGBoostAlgorithm  # noqa: E402

ALGORITHM_REGISTRY["xgboost"] = XGBoostAlgorithm

from haute.modelling._lightgbm import LightGBMAlgorithm  # noqa: E402

ALGORITHM_REGISTRY["lightgbm"] = LightGBMAlgorithm

from haute.modelling._ebm import EBMAlgorithm  # noqa: E402

ALGORITHM_REGISTRY["ebm"] = EBMAlgorithm

# Register GLM if RustyStats is installed (lazy import keeps it optional)
try:
    from haute.modelling._rustystats import GLMAlgorithm

    ALGORITHM_REGISTRY["glm"] = GLMAlgorithm
except ImportError:
    pass
