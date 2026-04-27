"""Encapsulates MODEL_SCORE node logic: model loading, prediction, post-processing.

Extracted from executor.py to reduce the size and nesting of ``_build_node_fn``
while keeping behaviour identical.
"""

from __future__ import annotations

import contextvars
import os
import threading
from typing import Any

import numpy as np
import polars as pl

from haute._hashing import content_hash_bytes
from haute._logging import get_logger
from haute._lru_cache import LRUCache
from haute._types import _Frame
from haute.errors import ConfigError
from haute.errors import FeatureMismatchError as FeatureMismatchError

logger = get_logger(component="model_scorer")

# Supported scoring flavors for explicit dispatch.
# Unknown flavor → ConfigError at the scoring entry point (fail loudly: a
# typo in the flavor string must not silently fall through to pyfunc).
_SUPPORTED_FLAVORS: frozenset[str] = frozenset({"catboost", "pyfunc", "rustystats"})


# ---------------------------------------------------------------------------
# Feature-validation cache
# ---------------------------------------------------------------------------
#
# ``_validate_features`` is invoked on every ``score`` call.  In the batch /
# preview hot path the caller hands us the same ``ScoringModel`` against the
# same input schema thousands of times in a row — the answer never changes
# but the O(n_features) walk + set construction still shows up in profiles.
#
# We memoise the ``(usable, missing)`` tuple keyed by
# ``(id(scoring_model), schema_hash)``.  ``id()`` is stable for the lifetime
# of the object, so a reload (which produces a fresh ``ScoringModel``
# instance) naturally misses; an eviction cascade from ``_mlflow_io``'s
# ``_model_cache`` drops stale entries so we never accumulate dead pins.
#
# Bounded: LRUCache, same max_size as ``_model_cache`` so the two caches
# evict on roughly the same timeline.  Thread-safe via LRUCache's RLock.
#
# Errors are NOT cached — ``FeatureMismatchError`` propagates; a later call
# with the same broken schema must re-raise so a repaired schema is detected
# on the very next call.

# Imported lazily inside ``_feature_validation_cache`` construction to avoid
# a circular import: ``_mlflow_io`` imports from us transitively via
# ``score_frame`` / ``_score_eager``.  The sizing constant itself is cheap
# to pull through once at module load.
from haute._mlflow_io import _MODEL_CACHE_MAX_SIZE as _MLFLOW_MODEL_CACHE_MAX_SIZE  # noqa: E402

_feature_validation_cache: LRUCache[tuple[int, str], tuple[list[str], list[str]]] = LRUCache(
    max_size=_MLFLOW_MODEL_CACHE_MAX_SIZE,
)


def _compute_schema_hash(schema: pl.Schema) -> str:
    """Stable 16-char xxh64 hex digest of the schema's ``(name, dtype)`` pairs.

    Sensitive to:
    * column order — CatBoost categorical indices are positional
    * column rename — the pair tuple changes → new digest
    * dtype change — the dtype repr changes → new digest

    Uses the project's standard ``xxh64`` hasher so the digest is
    consistent with every other content-addressed key in the codebase.

    No per-object memoisation: production callers always hand us a fresh
    ``pl.Schema`` object (``lf.collect_schema()`` returns a new instance
    each call), so a side table keyed on ``id(schema)`` was permanent
    cold cache with zero hit rate while adding weakref bookkeeping and a
    threading lock.  The downstream feature-validation LRU still keys on
    the digest itself, so every pair of equal ordered schemas collapses
    to the same cache slot regardless of object identity.
    """
    # Polars dtypes have stable ``str(...)`` reprs (e.g. "Float64", "Int64",
    # "Utf8") that differ per dtype — good enough as a lightweight equality
    # proxy for cache keys.
    pairs = [(name, str(dtype)) for name, dtype in schema.items()]
    payload = "\n".join(f"{name}\0{dtype}" for name, dtype in pairs).encode("utf-8")
    return content_hash_bytes(payload)


def _clear_feature_validation_cache() -> None:
    """Drop every entry in the feature-validation cache.

    Used as the blanket cascade target for
    :func:`haute._mlflow_io.clear_model_cache`.
    """
    _feature_validation_cache.clear()


def _invalidate_feature_validation_cache_for(scoring_model: Any) -> None:
    """Drop every entry whose first key component is ``id(scoring_model)``.

    Targeted cascade from the ``_model_cache`` eviction path: when a
    ``ScoringModel`` is dropped from the MLflow cache, any validation
    results we cached for it become unreachable garbage — purge them
    so the table does not fill with dead ``id()`` values.

    Silent on an unknown model: the predicate simply matches no keys
    and ``evict_where`` returns an empty list.
    """
    target_id = id(scoring_model)
    _feature_validation_cache.evict_where(lambda k: k[0] == target_id)


def _format_feature_mismatch(
    expected: list[str],
    available: list[str],
    missing: list[str],
    type_mismatches: list[tuple[str, str, str]] | None = None,
) -> str:
    """Build the multi-line diagnostic that replaces cryptic CatBoost errors."""
    type_mismatches = type_mismatches or []
    n_expected = len(expected)
    n_available = len(available)
    n_missing = len(missing)

    lines: list[str] = [
        f"Feature mismatch: model expects {n_expected} feature(s) "
        f"but the input data has {n_available} column(s).",
        "",
    ]

    if missing:
        lines.append(f"Missing feature(s) ({n_missing}):")
        for name in missing[:20]:
            lines.append(f"  - {name}")
        if n_missing > 20:
            lines.append(f"  ... and {n_missing - 20} more")
        lines.append("")

    if type_mismatches:
        lines.append("Type mismatch(es):")
        for col, expected_type, actual_type in type_mismatches[:10]:
            lines.append(f"  - '{col}': model expects {expected_type}, got {actual_type}")
        lines.append("")

    lines.append("These features were expected by the model but are not in the current input data.")
    return "\n".join(lines)


def _raise_feature_mismatch(
    expected: list[str],
    available: list[str],
    missing: list[str],
    type_mismatches: list[tuple[str, str, str]] | None = None,
) -> None:
    raise FeatureMismatchError(
        _format_feature_mismatch(expected, available, missing, type_mismatches),
        expected=expected,
        available=available,
        missing=missing,
        type_mismatches=type_mismatches or [],
    )


def _validate_features_uncached(
    scoring_model: Any,
    schema: pl.Schema,
) -> tuple[list[str], list[str]]:
    """Compare model features against available schema columns.

    Returns ``(usable_features, missing_features)``.
    Raises :class:`FeatureMismatchError` when any features are missing,
    their relative order disagrees with training, or a categorical column
    was supplied with a numeric dtype.

    Cost: O(n) set operations on column names — no data materialisation.

    This is the raw worker.  Call sites should go through
    :func:`_validate_features`, which memoises the result.
    """
    schema_order = schema.names()
    available = set(schema_order)
    expected = scoring_model.feature_names

    missing = [f for f in expected if f not in available]
    usable = [f for f in expected if f in available]

    # Cheap dtype checks for categorical expectations
    type_mismatches: list[tuple[str, str, str]] = []
    if scoring_model.cat_feature_names:
        for col in usable:
            if col in scoring_model.cat_feature_names:
                actual_dtype = schema[col]
                if actual_dtype.is_numeric():
                    type_mismatches.append((col, "categorical (String)", str(actual_dtype)))

    if not usable:
        _raise_feature_mismatch(
            expected=expected,
            available=sorted(available),
            missing=missing,
            type_mismatches=type_mismatches,
        )

    if missing:
        _raise_feature_mismatch(
            expected=expected,
            available=sorted(available),
            missing=missing,
            type_mismatches=type_mismatches,
        )

    if type_mismatches:
        # A categorical column passed to the scorer with a numeric dtype
        # will be encoded differently from how the model was trained.
        # Silently casting or warning-only is a recipe for invisible
        # prediction drift — raise so the operator can fix the upstream
        # schema or re-train.
        _raise_feature_mismatch(
            expected=expected,
            available=sorted(available),
            missing=missing,
            type_mismatches=type_mismatches,
        )

    # Feature order check: CatBoost treats the categorical set as positional
    # indices into the feature vector, so a reorder of training features
    # silently misaligns every categorical column.  Enforce that the input
    # schema presents the model's features in the same relative order as
    # training — extra columns elsewhere in the schema are fine.
    schema_position_by_name: dict[str, int] = {}
    for index, name in enumerate(schema_order):
        schema_position_by_name.setdefault(name, index)

    feature_positions = [
        (name, schema_position_by_name[name]) for name in expected if name in available
    ]
    actual_order_by_position = [name for name, _ in sorted(feature_positions, key=lambda p: p[1])]
    if actual_order_by_position != list(expected):
        raise FeatureMismatchError(
            "Feature order mismatch between training and scoring: the "
            "input data presents the model's features in a different order. "
            f"Expected order: {list(expected)}; actual relative order in "
            f"the input schema: {actual_order_by_position}. "
            "CatBoost categorical indices are positional — reordering features "
            "at score time silently misaligns categorical columns.",
            expected=list(expected),
            actual=actual_order_by_position,
            schema_order=list(schema_order),
        )

    return usable, missing


def _validate_features(
    scoring_model: Any,
    schema: pl.Schema,
) -> tuple[list[str], list[str]]:
    """Memoised façade over :func:`_validate_features_uncached`.

    Keys on ``(id(scoring_model), _compute_schema_hash(schema))``.
    Cache hits short-circuit the O(n_features) walk that the uncached
    worker performs — a measurable win on the batch / preview hot path
    where the same model is scored thousands of times against the same
    input schema.

    Errors are NOT cached: when
    :class:`~haute.errors.FeatureMismatchError` propagates, we leave the
    cache untouched so the next call with the same broken schema runs
    the validator again and re-raises.  A cached exception would
    silently swallow a later fix.
    """
    key = (id(scoring_model), _compute_schema_hash(schema))
    cached = _feature_validation_cache.get(key)
    if cached is not None:
        return cached
    result = _validate_features_uncached(scoring_model, schema)
    _feature_validation_cache.put(key, result)
    return result


# Runtime scenario context — set by Pipeline.run() / Pipeline.score()
# so that score_from_config (codegen path) can pick the right strategy.
# "live" = eager in-memory scoring, anything else = disk-batched.
_scenario_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "haute_scenario",
    default="batch",
)

_SCORE_BATCH_SIZE = 500_000

# Module-level temp file cleanup — avoids accumulating atexit handlers
_temp_files_to_clean: set[str] = set()
_atexit_registered = False
_temp_cleanup_lock = threading.Lock()


def _register_temp_cleanup(path: str) -> None:
    global _atexit_registered
    with _temp_cleanup_lock:
        _temp_files_to_clean.add(path)
        if not _atexit_registered:
            import atexit

            def _cleanup_all() -> None:
                for p in _temp_files_to_clean:
                    try:
                        os.unlink(p)
                    except OSError:
                        pass

            atexit.register(_cleanup_all)
            _atexit_registered = True


# ---------------------------------------------------------------------------
# Unified scoring entry point — explicit flavor dispatch, single batch/eager
# path.  The small wrapper helpers delegate onto this so scoring logic
# remains centralized.
# ---------------------------------------------------------------------------


def _predict_positive_proba(raw_model: Any, x_data: Any) -> np.ndarray | None:
    """Return the positive-class probability vector, or ``None`` if unsupported.

    Explicit branch on the raw model's ``predict_proba`` attribute — no
    proxying, no silent fallback.  If the model does not expose
    ``predict_proba`` we return ``None`` and the caller skips the proba
    column (the predict-only path still runs).
    """
    fn = getattr(raw_model, "predict_proba", None)
    if fn is None:
        return None
    probas = np.asarray(fn(x_data))
    if probas.ndim == 2:
        col_idx = 1 if probas.shape[1] > 1 else 0
        probas = probas[:, col_idx]
    return np.asarray(probas).flatten()


def _score_eager_unified(
    model: Any,
    lf: pl.LazyFrame,
    features: list[str],
    cat_feature_names: frozenset[str],
    flavor: str,
    task: str,
    output_col: str,
) -> pl.LazyFrame:
    """Eager in-memory scoring for a pre-validated flavor.

    Collects the LazyFrame via streaming (bounded memory), prepares the
    flavor-specific input via :func:`_prepare_predict_frame`, and calls
    the raw model's ``predict``.  For classification tasks the
    positive-class probability is appended when ``predict_proba`` is
    available; otherwise only the point prediction is written.
    """
    from haute._mlflow_io import _prepare_predict_frame

    feature_df = lf.select(features).collect(engine="streaming")
    x_data = _prepare_predict_frame(
        feature_df,
        features,
        cat_feature_names=cat_feature_names,
        flavor=flavor,
    )
    preds = np.asarray(model.predict(x_data)).flatten()
    prediction_columns = [pl.Series(output_col, preds)]
    if task == "classification":
        probas = _predict_positive_proba(model, x_data)
        if probas is not None:
            prediction_columns.append(pl.Series(f"{output_col}_proba", probas))
    return lf.with_columns(prediction_columns)


def _score_batched_unified(
    model: Any,
    lf: pl.LazyFrame,
    features: list[str],
    cat_feature_names: frozenset[str],
    flavor: str,
    task: str,
    output_col: str,
) -> pl.LazyFrame:
    """Sink → batch score → lazy scan (low-memory path) for the unified API.

    Wraps the raw model in a short-lived :class:`ScoringModel` so it can
    flow through :func:`_batch_score_to_parquet` — that helper is still
    directly tested by ``test_model_scorer.py`` and its signature is
    load-bearing.  Using ``ScoringModel`` here is a scoped carrier object,
    not a return of the ``__getattr__`` proxy pattern.
    """
    from haute._mlflow_io import ScoringModel

    carrier = ScoringModel(
        model=model,
        feature_names=features,
        cat_feature_names=cat_feature_names,
        flavor=flavor,
    )
    input_path = _sink_to_temp(lf)
    scored_path = _batch_score_to_parquet(
        carrier,
        input_path,
        features,
        output_col,
        task,
    )
    _register_temp_cleanup(scored_path)
    os.unlink(input_path)
    return pl.scan_parquet(scored_path)


def score_frame(
    *,
    model: Any,
    lf: pl.LazyFrame,
    features: list[str],
    cat_feature_names: frozenset[str],
    flavor: str,
    task: str = "regression",
    output_col: str = "prediction",
    batch: bool = False,
) -> pl.LazyFrame:
    """Unified scoring entry point with explicit flavor dispatch.

    Parameters
    ----------
    model
        A flavor-specific model object (``CatBoostRegressor``, MLflow
        ``PyFuncModel``, RustyStats ``GLMModel``).  Called as
        ``model.predict(x_data)`` (and ``model.predict_proba`` for
        classification when available).
    lf
        Input LazyFrame.
    features
        Ordered feature names the model expects.  Passed through to the
        flavor-specific preprocessor.
    cat_feature_names
        Categorical feature set.  Used by the CatBoost path to keep
        columns as ``pl.Categorical`` (and route through pandas), and
        ignored by pyfunc / rustystats paths.
    flavor
        One of ``"catboost"``, ``"pyfunc"``, ``"rustystats"``.  Unknown
        flavors raise :class:`~haute.errors.ConfigError` — silent
        fallback to pyfunc would produce subtly wrong predictions on a
        typo.
    task
        ``"regression"`` or ``"classification"``.  The latter appends a
        ``<output_col>_proba`` column when ``predict_proba`` is
        available.
    output_col
        Name of the prediction column written to the result frame.
    batch
        * ``True``  → sink + batched parquet scoring (low peak memory).
        * ``False`` → eager in-memory scoring (one ``predict`` call).

        Callers choose per-path: the preview / live-API caller passes
        ``False``; the batch-scoring caller passes ``True``.  No
        auto-detect — that would force a row-count probe on a potentially
        expensive ``scan_ndjson`` / filtered chain.

    Returns
    -------
    pl.LazyFrame
        The input columns plus the prediction column (and
        ``<output_col>_proba`` for classification when supported).

    Raises
    ------
    ConfigError
        If *flavor* is not one of the supported dispatch targets.
    """
    if flavor not in _SUPPORTED_FLAVORS:
        raise ConfigError(
            f"Unsupported scoring flavor: {flavor!r}. "
            f"Expected one of: {sorted(_SUPPORTED_FLAVORS)}.",
            flavor=flavor,
            supported=sorted(_SUPPORTED_FLAVORS),
        )

    if batch:
        return _score_batched_unified(
            model, lf, features, cat_feature_names, flavor, task, output_col
        )
    return _score_eager_unified(model, lf, features, cat_feature_names, flavor, task, output_col)


def _run_score_pipeline(
    scoring_model: Any,
    lf: pl.LazyFrame,
    *,
    task: str,
    output_col: str,
    code: str = "",
    source_names: list[str] | None = None,
    extra_dfs: tuple[_Frame, ...] = (),
    source: str = "live",
    row_limit: int | None = None,
) -> _Frame:
    """Core scoring logic shared by ``ModelScorer.score()`` and deploy scorer.

    1. Intersect model features with available columns.
    2. Run eager or batched prediction.
    3. Optionally execute user post-processing code.

    Parameters
    ----------
    scoring_model
        A pre-loaded ``ScoringModel`` (from MLflow or local disk).
    lf
        The input LazyFrame to score.
    task, output_col, code, source_names
        Scoring configuration (same semantics as ``ModelScorer`` attributes).
    extra_dfs
        Additional upstream LazyFrames passed through to user code.
    source
        ``"live"`` → eager path; anything else → batched path.
    row_limit
        When set, forces the eager path regardless of source.
    """
    from haute._mlflow_io import _score_eager as score_eager_

    schema = lf.collect_schema()
    features, _missing = _validate_features(scoring_model, schema)

    # No catch-all here by design.  ``_validate_features`` above already
    # raises ``FeatureMismatchError`` with full schema context for the
    # real "model / schema disagree" case, so the rewrap-as-
    # ``FeatureMismatchError`` the previous code did for *every* other
    # exception was pure laundering — it hid ``RuntimeError`` from a
    # corrupt artifact, ``AttributeError`` from a broken predict
    # surface, and ``ValueError`` from a malformed frame behind a
    # misleading mismatch message.  The ``_execute_eager_core`` /
    # ``_execute_lazy`` boundary already handles per-node failures
    # correctly (preview swallows, trace / batch propagate), so letting
    # the real error type reach the caller is both safe and fail-loud.
    if source == "live" or row_limit:
        result_lf = score_eager_(scoring_model, lf, features, output_col, task)
    else:
        result_lf = _score_batched_standalone(scoring_model, lf, features, output_col, task)

    if code:
        from haute._user_exec import _exec_user_code

        all_dfs = (result_lf,) + extra_dfs
        result_lf = _exec_user_code(
            code,
            source_names or [],
            all_dfs,
            extra_ns={"model": scoring_model},
        )
    return result_lf


def _score_batched_standalone(
    scoring_model: Any,
    lf: pl.LazyFrame,
    features: list[str],
    output_col: str,
    task: str,
) -> pl.LazyFrame:
    """Sink → batch score → lazy scan (low-memory path).

    Thin delegate onto :func:`score_frame` with ``batch=True`` so the
    actual scoring logic lives in the unified entry point.
    """
    return score_frame(
        model=scoring_model.raw_model,
        lf=lf,
        features=features,
        cat_feature_names=scoring_model.cat_feature_names,
        flavor=scoring_model.flavor,
        task=task,
        output_col=output_col,
        batch=True,
    )


class ModelScorer:
    """Load an MLflow model and score a LazyFrame.

    Encapsulates the full MODEL_SCORE lifecycle:
    1. Model loading (from MLflow run or registered model).
    2. Feature intersection (skip features absent from input).
    3. Prediction (eager in-memory or batched via parquet).
    4. Optional post-processing user code.

    Parameters
    ----------
    source_type : str
        ``"run"`` or ``"registered"`` — how to locate the model in MLflow.
    run_id : str
        MLflow run ID (used when *source_type* is ``"run"``).
    artifact_path : str
        Artifact path within the run (e.g. ``"model.cbm"``).
    registered_model : str
        Registered model name (used when *source_type* is ``"registered"``).
    version : str
        Model version string (``"1"``, ``"2"``, or ``"latest"``).
    task : str
        ``"regression"`` or ``"classification"``.
    output_col : str
        Name of the column that receives predictions.
    code : str
        Optional user post-processing code applied after scoring.
    source_names : list[str]
        Sanitised upstream node names (variable names for user code).
    source : str
        Active execution source — ``"live"`` uses eager scoring, anything
        else uses the batched parquet path.
    row_limit : int | None
        When set (preview/trace), forces the eager path regardless of source.
    """

    def __init__(
        self,
        *,
        source_type: str,
        run_id: str = "",
        artifact_path: str = "",
        registered_model: str = "",
        version: str = "latest",
        task: str = "regression",
        output_col: str = "prediction",
        code: str = "",
        source_names: list[str] | None = None,
        source: str = "live",
        row_limit: int | None = None,
    ) -> None:
        self.source_type = source_type
        self.run_id = run_id
        self.artifact_path = artifact_path
        self.registered_model = registered_model
        self.version = version
        self.task = task
        self.output_col = output_col
        self.code = code
        self.source_names = list(source_names) if source_names else []
        self.source = source
        self.row_limit = row_limit

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def score(self, *dfs: _Frame) -> _Frame:
        """Load the model, predict, and optionally post-process.

        Accepts one or more upstream LazyFrames (first is the scoring input).
        Returns a LazyFrame with prediction column(s) appended.
        """
        from haute._mlflow_io import load_mlflow_model

        scoring_model = load_mlflow_model(
            source_type=self.source_type,
            run_id=self.run_id,
            artifact_path=self.artifact_path,
            registered_model=self.registered_model,
            version=self.version,
            task=self.task,
        )

        lf = dfs[0] if dfs else pl.LazyFrame()
        return _run_score_pipeline(
            scoring_model,
            lf,
            task=self.task,
            output_col=self.output_col,
            code=self.code,
            source_names=self.source_names,
            extra_dfs=dfs[1:],
            source=self.source,
            row_limit=self.row_limit,
        )

    # ------------------------------------------------------------------
    # Scoring strategies
    # ------------------------------------------------------------------

    def _score_eager(
        self,
        scoring_model: Any,
        lf: pl.LazyFrame,
        features: list[str],
    ) -> pl.LazyFrame:
        """Collect and score in-memory -- delegates to shared helper."""
        from haute._mlflow_io import _score_eager as score_eager_

        return score_eager_(scoring_model, lf, features, self.output_col, self.task)

    def _score_batched(
        self,
        scoring_model: Any,
        lf: pl.LazyFrame,
        features: list[str],
    ) -> pl.LazyFrame:
        """Sink -> batch score -> lazy scan -- low-memory path.

        Thin delegate onto :func:`_score_batched_standalone`, which in
        turn delegates onto :func:`score_frame` with ``batch=True``.
        """
        return _score_batched_standalone(
            scoring_model,
            lf,
            features,
            self.output_col,
            self.task,
        )


# ----------------------------------------------------------------------
# score_from_config — thin delegation target for codegen
# ----------------------------------------------------------------------


def score_from_config(
    *dfs: pl.LazyFrame,
    config: str,
    base_dir: str | None = None,
) -> pl.LazyFrame:
    """Score using model parameters from a JSON config file.

    Reads the config, loads the model from MLflow (auto-detecting
    CatBoost vs pyfunc flavor), and returns predictions appended to
    the input DataFrame.

    This is the delegation target generated by codegen for MODEL_SCORE
    nodes — it keeps the ``.py`` file clean while the library handles
    the heavy lifting.

    Args:
        *dfs: Upstream LazyFrame(s) — the first is used as scoring input.
        config: Path to the JSON config file (e.g.
            ``"config/model_scoring/competitor_scoring.json"``).
        base_dir: Directory to resolve *config* against.  When ``None``
            the path is resolved relative to ``Path.cwd()``.  Codegen
            templates pass ``Path(__file__).parent`` so the config is
            always found regardless of the working directory at runtime.
    """
    import json
    from pathlib import Path

    from haute._io import read_user_text

    config_path = Path(config)
    if base_dir is not None and not config_path.is_absolute():
        config_path = Path(base_dir) / config_path
    # Validate path stays within project directory
    resolved = config_path.resolve()
    root = (Path(base_dir) if base_dir else Path.cwd()).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"Config path {config!r} resolves outside project root")
    cfg = json.loads(read_user_text(resolved))
    scorer = ModelScorer(
        source_type=cfg.get("sourceType", "run"),
        run_id=cfg.get("run_id", ""),
        artifact_path=cfg.get("artifact_path", ""),
        registered_model=cfg.get("registered_model", ""),
        version=cfg.get("version", "latest"),
        task=cfg.get("task", "regression"),
        output_col=cfg.get("output_column", "prediction"),
        source=_scenario_ctx.get(),
    )
    return scorer.score(*dfs)


# ----------------------------------------------------------------------
# Module-level helpers (shared by the class, kept out of the class body
# because they are pure functions with no dependency on instance state).
# ----------------------------------------------------------------------


def _sink_to_temp(lf: pl.LazyFrame) -> str:
    """Sink a LazyFrame to a temp parquet file via streaming.

    Uses ``fast_checkpoint=True`` for lz4 compression — these temp
    files are read back immediately for batch scoring and then deleted,
    so speed matters more than compression ratio.
    """
    import os
    import tempfile

    from haute._polars_utils import safe_sink

    fd, path = tempfile.mkstemp(
        suffix=".parquet",
        prefix="haute_score_in_",
    )
    os.close(fd)
    safe_sink(lf, path, fast_checkpoint=True)
    return path


def _batch_score_to_parquet(
    scoring_model: Any,
    input_path: str,
    features: list[str],
    output_col: str,
    task: str,
) -> str:
    """Score a parquet file in batches, return path to scored output."""
    import os
    import tempfile

    import pyarrow.parquet as pq

    from haute._mlflow_io import _append_classification_proba, _prepare_predict_frame

    fd, out_path = tempfile.mkstemp(
        suffix=".parquet",
        prefix="haute_score_out_",
    )
    os.close(fd)

    pf = pq.ParquetFile(input_path)
    writer = None
    want_proba = task == "classification"

    try:
        for batch in pf.iter_batches(
            batch_size=_SCORE_BATCH_SIZE,
        ):
            chunk_raw = pl.from_arrow(batch)
            if isinstance(chunk_raw, pl.Series):
                chunk = chunk_raw.to_frame()
            else:
                chunk = chunk_raw
            feature_chunk = chunk.select(features)
            x_data = _prepare_predict_frame(
                feature_chunk,
                features,
                cat_feature_names=scoring_model.cat_feature_names,
                flavor=scoring_model.flavor,
            )
            preds = scoring_model.predict(x_data)
            chunk = chunk.with_columns(
                pl.Series(output_col, preds),
            )
            if want_proba:
                chunk = _append_classification_proba(
                    chunk,
                    scoring_model,
                    x_data,
                    output_col,
                )
            table = chunk.to_arrow()
            if writer is None:
                writer = pq.ParquetWriter(
                    out_path,
                    table.schema,
                )
            writer.write_table(table)
            del chunk, x_data, table
    finally:
        if writer is not None:
            writer.close()
        else:
            # Zero-row input: write an empty parquet preserving correct dtypes
            input_schema = pl.read_parquet_schema(input_path)
            empty = pl.DataFrame(
                {c: pl.Series([], dtype=input_schema.get(c, pl.Float64)) for c in features}
            ).with_columns(pl.Series(output_col, [], dtype=pl.Float64))
            if want_proba:
                empty = empty.with_columns(pl.Series(f"{output_col}_proba", [], dtype=pl.Float64))
            pq.write_table(empty.to_arrow(), out_path)
    return out_path
