"""Encapsulates MODEL_SCORE node logic: model loading, prediction, post-processing.

Extracted from executor.py to reduce the size and nesting of ``_build_node_fn``
while keeping behaviour identical.
"""

from __future__ import annotations

import contextvars
import os
import threading
from collections.abc import Hashable, Iterable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, TypeAlias, cast

import numpy as np
import polars as pl

from haute._cache import CacheConsumer, checked_cache_inputs
from haute._hashing import content_hash_bytes
from haute._logging import get_logger
from haute._lru_cache import LRUCache
from haute._model_flavors import _SUPPORTED_FLAVORS as _SUPPORTED_FLAVORS
from haute._model_flavors import ModelFlavor as ModelFlavor
from haute._types import _Frame
from haute.errors import ConfigError
from haute.errors import FeatureMismatchError as FeatureMismatchError

if TYPE_CHECKING:
    # ``Task`` is the shared classification/regression literal already defined
    # for the feature contract; reuse it so the scorer surface does not invent
    # a second, drift-prone spelling of the task domain.
    from haute.modelling._feature_contract import Task

logger = get_logger(component="model_scorer")

# ``ModelFlavor`` / ``_SUPPORTED_FLAVORS`` are the single source of truth for
# the scoring flavor domain and are imported (above) from
# :mod:`haute._model_flavors` so this module and :mod:`haute._mlflow_io`
# dispatch on the *same* object and can never drift.  Re-exported here (via the
# ``import X as X`` form) so existing call sites keep importing them from
# ``haute._model_scorer``.
#
# Unknown flavor → ConfigError at the scoring entry point (fail loudly: a
# typo in the flavor string must not silently fall through to pyfunc).

# How an MLflow model is located.  ``"run"`` resolves an artifact within a
# run; ``"registered"`` resolves a version of a registered model.  Typed so a
# typo cannot silently reach the loader's dispatch as an unhandled string.
ModelSource: TypeAlias = Literal["run", "registered"]


# ---------------------------------------------------------------------------
# Feature-validation cache
# ---------------------------------------------------------------------------
#
# ``_validate_features`` is invoked on every ``score`` call.  In the batch /
# preview hot path the caller hands us the same ``ScoringModel`` against the
# same input schema thousands of times in a row — the answer never changes
# but the O(n_features) walk + set construction still shows up in profiles.
#
# We memoise the ``(usable, missing)`` tuple keyed on the model-side feature
# contract (ordered feature names + categorical set) and a schema-content key.
# The result is a pure function of exactly those two inputs — the validator
# reads nothing else off the model — so the key is fully content-addressed.
#
# We deliberately do NOT key on ``id(scoring_model)``: it added no correctness
# (a hit already required an identical contract + schema, whose result is the
# same regardless of object identity) while creating a latent footgun — every
# distinct ``ScoringModel`` instance minted its own dead entry, and transient
# carrier models that never flow through ``_model_cache`` never received an
# eviction cascade, so their id-keyed rows could only ever age out via the LRU.
# Content addressing lets two reloads of the same contract share one entry and
# makes the eviction cascade a pure cleanup (drop entries for an evicted
# contract) rather than the sole reclamation path.
#
# The in-memory cache key intentionally uses Polars dtype objects directly
# instead of serialising them to a digest on every score call. It is still
# content-based across fresh ``pl.Schema`` instances, but the hot path avoids
# the stringification/hash work that made cache hits barely faster than a
# cold validation.
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

_SchemaValidationKey: TypeAlias = tuple[tuple[str, Hashable], ...]
_ModelFeatureContractKey: TypeAlias = tuple[tuple[str, ...], frozenset[str], str | None]
_FeatureValidationCacheKey: TypeAlias = tuple[_ModelFeatureContractKey, _SchemaValidationKey]
_FeatureValidationResult: TypeAlias = tuple[list[str], list[str]]
_FeatureValidationLastEntry: TypeAlias = tuple[_FeatureValidationCacheKey, _FeatureValidationResult]
_CategoricalLevels: TypeAlias = Mapping[str, Iterable[str | None]] | None

_feature_validation_cache: LRUCache[_FeatureValidationCacheKey, _FeatureValidationResult] = (
    LRUCache(max_size=_MLFLOW_MODEL_CACHE_MAX_SIZE)
)
_feature_validation_last_entry: _FeatureValidationLastEntry | None = None


def _compute_schema_hash(schema: pl.Schema) -> str:
    """Stable 16-char xxh64 hex digest of the schema's ``(name, dtype)`` pairs.

    Sensitive to:
    * column order — CatBoost categorical indices are positional
    * column rename — the pair tuple changes → new digest
    * dtype change — the dtype repr changes → new digest

    Uses the project's standard ``xxh64`` hasher so the digest is
    consistent with every other content-addressed key in the codebase.

    This digest is retained for stable diagnostics and tests. The hot
    feature-validation path uses ``_schema_validation_cache_key`` instead
    so repeated score calls do not pay stringification/hash work before
    every LRU lookup.
    """
    # Polars dtypes have stable ``str(...)`` reprs (e.g. "Float64", "Int64",
    # "Utf8") that differ per dtype — good enough as a lightweight equality
    # proxy for cache keys.
    pairs = [(name, str(dtype)) for name, dtype in schema.items()]
    payload = "\n".join(f"{name}\0{dtype}" for name, dtype in pairs).encode("utf-8")
    return content_hash_bytes(payload)


def _schema_validation_cache_key(schema: pl.Schema) -> _SchemaValidationKey:
    """Return the hot-path schema key used by ``_validate_features``.

    This is process-local cache state, not a persisted digest. ``pl.Schema``
    preserves ordered ``(name, dtype)`` pairs, and Polars dtype objects are
    hashable/equality-comparable, so this key remains sensitive to column
    order, renames, and dtype changes while avoiding per-call string
    serialisation.
    """
    return cast(_SchemaValidationKey, tuple(schema.items()))


def _model_feature_contract_key(scoring_model: Any) -> _ModelFeatureContractKey:
    """Return the model-side contract that controls feature validation."""
    inputs = checked_cache_inputs(
        CacheConsumer.MODEL_CONTRACT,
        {
            "feature_names": tuple(scoring_model.feature_names),
            "categorical_features": frozenset(scoring_model.cat_feature_names or ()),
            "offset_column": _declared_offset_column(scoring_model),
        },
    )
    return cast(_ModelFeatureContractKey, inputs.ordered_values)


def _clear_feature_validation_cache() -> None:
    """Drop every entry in the feature-validation cache.

    Used as the blanket cascade target for
    :func:`haute._mlflow_io.clear_model_cache`.
    """
    global _feature_validation_last_entry
    _feature_validation_cache.clear()
    _feature_validation_last_entry = None


def _invalidate_feature_validation_cache_for(scoring_model: Any) -> None:
    """Drop every entry whose feature contract matches ``scoring_model``.

    Targeted cascade from the ``_model_cache`` eviction path: when a
    ``ScoringModel`` is dropped from the MLflow cache, the validation
    results we cached for its feature contract are no longer worth
    pinning — purge them so the table does not hold results for a
    contract no longer resident in the model cache.

    Keyed on the content-addressed contract (the same first key
    component :func:`_validate_features` writes), not object identity, so
    a reloaded instance of the same contract invalidates correctly.
    Silent on an unknown model: the predicate simply matches no keys and
    ``evict_where`` returns an empty list.
    """
    global _feature_validation_last_entry
    target_contract = _model_feature_contract_key(scoring_model)
    _feature_validation_cache.evict_where(lambda k: k[0] == target_contract)
    last_entry = _feature_validation_last_entry
    if last_entry is not None and last_entry[0][0] == target_contract:
        _feature_validation_last_entry = None


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
        n_type = len(type_mismatches)
        lines.append(f"Type mismatch(es) ({n_type}):")
        for col, expected_type, actual_type in type_mismatches[:10]:
            lines.append(f"  - '{col}': model expects {expected_type}, got {actual_type}")
        if n_type > 10:
            lines.append(f"  ... and {n_type - 10} more")
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

    # The trained-with offset column is a required scoring input even though
    # it is not a design-matrix feature (RustyStats already lists it in
    # ``required_columns``; CatBoost does not, so it is enforced here).
    # Scoring without it would silently proceed on an offset-0 basis.
    offset_column = _declared_offset_column(scoring_model)
    if offset_column and offset_column not in available and offset_column not in missing:
        missing.append(offset_column)

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

    Keys on ``(feature-contract, ordered schema items)`` — a fully
    content-addressed key, since the validation result depends only on the
    model's feature contract and the input schema.
    The immediate last-entry probe covers the dominant batch/preview hot
    path without taking the LRU lock or promoting an ``OrderedDict`` entry
    on every score call. The bounded LRU remains the general cache for
    callers that alternate among several schemas or models.

    Errors are NOT cached: when
    :class:`~haute.errors.FeatureMismatchError` propagates, we leave the
    cache untouched so the next call with the same broken schema runs
    the validator again and re-raises.  A cached exception would
    silently swallow a later fix.
    """
    global _feature_validation_last_entry
    key = (
        _model_feature_contract_key(scoring_model),
        _schema_validation_cache_key(schema),
    )
    last_entry = _feature_validation_last_entry
    if last_entry is not None and last_entry[0] == key:
        return last_entry[1]

    cached = _feature_validation_cache.get(key)
    if cached is not None:
        _feature_validation_last_entry = (key, cached)
        return cached
    result = _validate_features_uncached(scoring_model, schema)
    _feature_validation_cache.put(key, result)
    _feature_validation_last_entry = (key, result)
    return result


# Runtime scenario context — set by Pipeline.run() / Pipeline.score()
# so that score_from_config (codegen path) can pick the right strategy.
# "live" = eager in-memory scoring, anything else = disk-batched.
_scenario_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "haute_scenario",
    default="batch",
)

_SCORE_BATCH_SIZE = 500_000


@dataclass(frozen=True, slots=True)
class ScoreWriteProjection:
    """Explicit projection for batch model-score parquet writes.

    ``None`` passthrough columns means preserve the full scored input.  A
    concrete set means write exactly those input columns, in input schema
    order, plus the prediction/probability outputs produced by scoring.
    """

    passthrough_columns: frozenset[str] | None = None
    optional_passthrough_columns: frozenset[str] = frozenset()
    required_output_columns: frozenset[str] = frozenset()


# Module-level temp file cleanup — avoids accumulating atexit handlers
_temp_files_to_clean: set[str] = set()
_atexit_registered = False
_temp_cleanup_lock = threading.Lock()
_temp_file_scope: contextvars.ContextVar[list[str] | None] = contextvars.ContextVar(
    "haute_model_score_temp_file_scope",
    default=None,
)


def _register_temp_cleanup(path: str) -> None:
    global _atexit_registered
    with _temp_cleanup_lock:
        _temp_files_to_clean.add(path)
        if not _atexit_registered:
            import atexit

            def _cleanup_all() -> None:
                with _temp_cleanup_lock:
                    paths = tuple(_temp_files_to_clean)
                    _temp_files_to_clean.clear()
                for p in paths:
                    with suppress(FileNotFoundError):
                        os.unlink(p)

            atexit.register(_cleanup_all)
            _atexit_registered = True


def _cleanup_registered_temp_files(paths: Iterable[str]) -> None:
    """Unlink scorer temp files and remove them from the process-exit set."""
    for path in dict.fromkeys(paths):
        with suppress(FileNotFoundError):
            os.unlink(path)
        with _temp_cleanup_lock:
            _temp_files_to_clean.discard(path)


@contextmanager
def model_score_temp_file_scope(paths: list[str] | None = None) -> Iterator[list[str]]:
    """Collect batch scorer output temps created within this context."""
    scoped_paths: list[str] = paths if paths is not None else []
    token = _temp_file_scope.set(scoped_paths)
    try:
        yield scoped_paths
    finally:
        _temp_file_scope.reset(token)


# ---------------------------------------------------------------------------
# Unified scoring entry point — explicit flavor dispatch, single batch/eager
# path.  The small wrapper helpers delegate onto this so scoring logic
# remains centralized.
# ---------------------------------------------------------------------------


def _predict_positive_proba(raw_model: Any, x_data: Any, output_col: str) -> np.ndarray | None:
    """Return the positive-class probability vector, or ``None`` if unsupported.

    Explicit branch on the raw model's ``predict_proba`` attribute — no
    proxying, no silent fallback.  If the model does not expose
    ``predict_proba`` we return ``None`` and the caller skips the proba
    column (the predict-only path still runs).

    Shape semantics are owned by ``_mlflow_io._positive_class_proba_vector``
    — the SAME dispatch the batch path uses — so eager and batch cannot
    drift: 1-D output is used as-is, ``(n, 1)`` takes column 0, ``(n, 2)``
    takes column 1, and multiclass / degenerate shapes raise the identical
    named ``ValueError`` on both surfaces.
    """
    fn = getattr(raw_model, "predict_proba", None)
    if fn is None:
        return None
    from haute._mlflow_io import _positive_class_proba_vector

    return _positive_class_proba_vector(fn(x_data), output_col)


def _raw_model_supports_predict_proba(model: Any) -> bool:
    raw_model = getattr(model, "raw_model", model)
    return getattr(raw_model, "predict_proba", None) is not None


def _declared_offset_column(scoring_model: Any) -> str | None:
    """Read ``offset_column`` off a carrier, hardened to real strings.

    Mocked scoring models (and duck-typed carriers predating the field)
    can expose truthy non-string attributes; only a non-empty ``str`` is
    an offset declaration.
    """
    value = getattr(scoring_model, "offset_column", None)
    return value if isinstance(value, str) and value else None


def _model_offset_column(model: Any, flavor: ModelFlavor) -> str | None:
    """Return the offset column a raw model was trained with, if any.

    Both native flavors are self-describing: CatBoost via the
    ``haute_offset_column`` model-metadata key stamped at fit time,
    RustyStats via the serialised offset spec.  Pyfunc models expose no
    offset surface (their signature declares the column as an input, and
    the wrapped model owns applying it).
    """
    if flavor == "catboost":
        from haute._mlflow_io import _catboost_offset_column

        return _catboost_offset_column(model)
    if flavor == "rustystats":
        spec = getattr(model, "_offset_spec", None)
        return spec if isinstance(spec, str) and spec else None
    return None


def _require_offset_column(available: Iterable[str], offset_column: str | None) -> None:
    """Fail loud when a scoring input lacks the model's offset column."""
    if offset_column and offset_column not in set(available):
        raise FeatureMismatchError(
            f"Scoring input is missing the model's offset column "
            f"{offset_column!r}. The model was trained with this offset and "
            f"scoring without it would silently mis-scale every prediction. "
            f"Provide the column (use a constant 1 for a unit basis).",
            missing=[offset_column],
            offset_column=offset_column,
        )


def _catboost_baseline_pool(
    x_data: Any,
    frame: pl.DataFrame,
    features: list[str],
    cat_feature_names: frozenset[str],
    offset_column: str,
) -> Any:
    """Wrap prepared CatBoost predict input in a Pool carrying the baseline.

    CatBoost only applies a baseline supplied inside a ``Pool``; a bare
    matrix predict silently scores from baseline 0.
    """
    from catboost import Pool

    _require_offset_column(frame.columns, offset_column)
    baseline = frame[offset_column].cast(pl.Float64).to_numpy()
    cat_indices = [i for i, f in enumerate(features) if f in cat_feature_names]
    return Pool(
        data=x_data,
        cat_features=cat_indices if cat_indices else None,
        baseline=baseline,
    )


# Flavors whose model consumes a named frame and owns applying the offset
# itself, so the offset column must ride along in the predict frame.  RustyStats
# extracts its offset column by name; a pyfunc model receives it as a declared
# signature input and the wrapped model applies it (there is no baseline haute
# can re-inject into an opaque pyfunc).  CatBoost is the exception — its offset
# is a numeric ``Pool`` baseline, never a design-matrix column.
_OFFSET_PASSTHROUGH_FLAVORS: frozenset[ModelFlavor] = frozenset({"rustystats", "pyfunc"})


def _offset_predict_features(
    features: list[str],
    flavor: ModelFlavor,
    offset_column: str | None,
) -> list[str]:
    """Feature selection handed to ``_prepare_predict_frame``.

    For passthrough flavors (rustystats, pyfunc) the offset column rides
    along with the features so the model can apply it; CatBoost keeps the
    pure feature list and receives the offset separately as a ``Pool``
    baseline.
    """
    if offset_column and flavor in _OFFSET_PASSTHROUGH_FLAVORS and offset_column not in features:
        return [*features, offset_column]
    return list(features)


def _score_output_projection_columns(
    schema_names: list[str],
    write_projection: ScoreWriteProjection,
    *,
    generated_columns: Iterable[str],
) -> list[str]:
    if write_projection.passthrough_columns is None:
        passthrough = list(schema_names)
        optional_passthrough: list[str] = []
    else:
        passthrough = _ordered_required_columns(
            schema_names,
            write_projection.passthrough_columns,
            context="model-score output projection",
        )
        optional_passthrough = [
            c
            for c in schema_names
            if c in write_projection.optional_passthrough_columns and c not in passthrough
        ]

    projected_columns = list(passthrough)
    for cname in generated_columns:
        if cname in schema_names and cname not in projected_columns:
            projected_columns.append(cname)
    for cname in optional_passthrough:
        if cname not in projected_columns:
            projected_columns.append(cname)

    missing_required = write_projection.required_output_columns - set(projected_columns)
    if missing_required:
        raise ValueError(
            "model-score output projection requested columns that were "
            f"not produced or preserved: {sorted(missing_required)}"
        )
    return projected_columns


def _project_scored_output(
    result_lf: pl.LazyFrame,
    write_projection: ScoreWriteProjection | None,
    *,
    output_col: str,
    generated_columns: Iterable[str] | None = None,
) -> pl.LazyFrame:
    if write_projection is None:
        return result_lf

    schema_names = result_lf.collect_schema().names()
    generated = list(generated_columns) if generated_columns is not None else [output_col]
    if generated_columns is None:
        proba_col = f"{output_col}_proba"
        if proba_col in schema_names:
            generated.append(proba_col)
    projected_columns = _score_output_projection_columns(
        schema_names,
        write_projection,
        generated_columns=generated,
    )
    return result_lf.select(projected_columns)


def _apply_score_write_projection(
    frame: pl.DataFrame,
    *,
    write_projection: ScoreWriteProjection | None,
    output_col: str,
    can_predict_proba: bool,
) -> pl.DataFrame:
    """Apply a batch write projection to an already-scored eager frame.

    Eager (``pl.DataFrame``) counterpart of :func:`_project_scored_output`.
    Assembles the generated-column list (prediction, plus the proba column
    when the model produced one) and selects the projected columns. A
    ``None`` projection preserves the full scored frame. Shared by the
    per-chunk and zero-row branches of :func:`_batch_score_to_parquet` so
    the two cannot drift.
    """
    if write_projection is None:
        return frame
    generated = [output_col]
    proba_col = f"{output_col}_proba"
    if can_predict_proba and proba_col in frame.columns:
        generated.append(proba_col)
    return frame.select(
        _score_output_projection_columns(
            frame.columns,
            write_projection,
            generated_columns=generated,
        )
    )


def _score_input_projection_columns(
    lf: pl.LazyFrame,
    features: list[str],
    write_projection: ScoreWriteProjection | None,
    offset_column: str | None = None,
) -> frozenset[str] | None:
    """Return the input columns scoring needs for a concrete write projection.

    ``None`` means the scored output preserves the full input, so every
    input column must be materialised.  A concrete passthrough set means
    scoring only needs the model features plus the projected passthrough
    columns (and any optional passthrough columns actually present) —
    everything else can be pruned from the single upstream execution.
    The model's offset column, when set, is always part of the scoring
    input and survives the pruning.  Shared by the eager and batched
    paths so both prune identically.
    """
    if write_projection is None or write_projection.passthrough_columns is None:
        return None
    optional_present: frozenset[str] = frozenset()
    if write_projection.optional_passthrough_columns:
        schema_names = frozenset(lf.collect_schema().names())
        optional_present = write_projection.optional_passthrough_columns & schema_names
    required = frozenset(features) | write_projection.passthrough_columns | optional_present
    if offset_column:
        required |= {offset_column}
    return required


def _score_eager_unified(
    model: Any,
    lf: pl.LazyFrame,
    features: list[str],
    cat_feature_names: frozenset[str],
    flavor: ModelFlavor,
    task: str,
    output_col: str,
    write_projection: ScoreWriteProjection | None = None,
    categorical_levels: _CategoricalLevels = None,
    offset_column: str | None = None,
) -> pl.LazyFrame:
    """Eager in-memory scoring for a pre-validated flavor.

    Collects the LazyFrame via streaming exactly once.  Predictions are
    computed from that single materialised frame and attached to the
    same frame, so row alignment is structural: the upstream plan is
    never re-executed, and an order-unstable upstream op (``group_by``
    without ``maintain_order``, ``unique``, a streaming join) cannot
    land predictions on the wrong rows.  Declared categorical value
    domains are validated against the same materialisation — the exact
    rows the model scores — before ``predict`` runs.

    A concrete write projection prunes the collection to the columns the
    output needs (mirroring the batched path's sink projection); without
    one, the full input is part of the scored output and materialises
    here, so upstream failures surface at score time instead of being
    deferred to a later collect.

    For classification tasks the positive-class probability is appended
    when ``predict_proba`` is available; otherwise only the point
    prediction is written.
    """
    from haute._execution_context import ExecutionProfile
    from haute._mlflow_io import _prepare_predict_frame
    from haute._polars_utils import streaming_collect

    # An explicit offset (from the feature contract) is authoritative; only
    # fall back to the model's self-description when the caller has none — so
    # pyfunc models, which cannot self-describe an offset, still apply it.
    offset_column = (
        offset_column if offset_column is not None else _model_offset_column(model, flavor)
    )
    if offset_column:
        _require_offset_column(lf.collect_schema().names(), offset_column)
    collect_lf = lf
    input_columns = _score_input_projection_columns(
        lf,
        features,
        write_projection,
        offset_column=offset_column,
    )
    if input_columns is not None:
        ordered = _ordered_required_columns(
            lf.collect_schema().names(),
            input_columns,
            context="model-score input projection",
        )
        collect_lf = lf.select(ordered)
    frame = streaming_collect(
        collect_lf,
        profile=ExecutionProfile.PREVIEW_EAGER,
    )
    _validate_runtime_categorical_values(frame, categorical_levels or {})
    predict_features = _offset_predict_features(features, flavor, offset_column)
    x_data = _prepare_predict_frame(
        frame.select(predict_features),
        predict_features,
        cat_feature_names=cat_feature_names,
        flavor=flavor,
    )
    if offset_column and flavor == "catboost":
        x_data = _catboost_baseline_pool(
            x_data,
            frame,
            features,
            cat_feature_names,
            offset_column,
        )
    preds = np.asarray(model.predict(x_data)).flatten()
    prediction_columns = [pl.Series(output_col, preds)]
    generated_columns = [output_col]
    if task == "classification":
        probas = _predict_positive_proba(model, x_data, output_col)
        if probas is not None:
            proba_col = f"{output_col}_proba"
            prediction_columns.append(pl.Series(proba_col, probas))
            generated_columns.append(proba_col)
    result_lf = frame.with_columns(prediction_columns).lazy()
    return _project_scored_output(
        result_lf,
        write_projection,
        output_col=output_col,
        generated_columns=generated_columns,
    )


def _normalise_score_write_projection(
    required_output_columns: frozenset[str] | set[str] | None,
    *,
    output_col: str,
    task: str,
) -> ScoreWriteProjection | None:
    if required_output_columns is None:
        return None
    generated = {output_col}
    optional_passthrough: set[str] = set()
    if task == "classification":
        proba_col = f"{output_col}_proba"
        generated.add(proba_col)
        if proba_col in required_output_columns:
            optional_passthrough.add(proba_col)
    passthrough_columns = frozenset(str(c) for c in required_output_columns if c not in generated)
    return ScoreWriteProjection(
        passthrough_columns=passthrough_columns,
        optional_passthrough_columns=frozenset(optional_passthrough),
        required_output_columns=frozenset(str(c) for c in required_output_columns),
    )


def _normalise_runtime_categorical_levels(
    categorical_levels: _CategoricalLevels,
    *,
    features: list[str],
) -> dict[str, list[str | None]]:
    from haute.modelling._feature_contract import normalise_categorical_levels

    feature_set = set(features)
    return {
        column: levels
        for column, levels in normalise_categorical_levels(categorical_levels).items()
        if column in feature_set
    }


def _validate_runtime_categorical_values(
    frame: pl.LazyFrame | pl.DataFrame,
    categorical_levels: Mapping[str, Iterable[str | None]],
) -> None:
    if not categorical_levels:
        return
    from haute.modelling._feature_contract import validate_categorical_value_domains

    validate_categorical_value_domains(frame, categorical_levels)


def _ordered_required_columns(
    schema_names: list[str],
    required_columns: frozenset[str],
    *,
    context: str,
) -> list[str]:
    available = set(schema_names)
    missing = sorted(required_columns - available)
    if missing:
        raise ValueError(f"{context} requested missing passthrough columns: {missing}")
    return [c for c in schema_names if c in required_columns]


def _score_batched_unified(
    model: Any,
    lf: pl.LazyFrame,
    features: list[str],
    cat_feature_names: frozenset[str],
    flavor: ModelFlavor,
    task: str,
    output_col: str,
    write_projection: ScoreWriteProjection | None = None,
    temporary_paths: list[str] | None = None,
    categorical_levels: _CategoricalLevels = None,
    offset_column: str | None = None,
) -> pl.LazyFrame:
    """Sink → batch score → lazy scan (low-memory path) for the unified API.

    Wraps the raw model in a short-lived :class:`ScoringModel` so it can
    flow through :func:`_batch_score_to_parquet` — that helper is still
    directly tested by ``test_model_scorer.py`` and its signature is
    load-bearing.  Using ``ScoringModel`` here is a scoped carrier object,
    not a return of the ``__getattr__`` proxy pattern.
    """
    from haute._mlflow_io import ScoringModel

    # Contract-supplied offset wins; fall back to model self-description.
    offset_column = (
        offset_column if offset_column is not None else _model_offset_column(model, flavor)
    )
    if offset_column:
        _require_offset_column(lf.collect_schema().names(), offset_column)
    carrier = ScoringModel(
        model=model,
        feature_names=features,
        cat_feature_names=cat_feature_names,
        flavor=flavor,
        offset_column=offset_column,
    )
    sink_columns = _score_input_projection_columns(
        lf,
        features,
        write_projection,
        offset_column=offset_column,
    )
    input_path = _sink_to_temp(lf, columns=sink_columns)
    try:
        scored_path = _batch_score_to_parquet(
            carrier,
            input_path,
            features,
            output_col,
            task,
            write_projection=write_projection,
            categorical_levels=categorical_levels,
        )
    finally:
        with suppress(FileNotFoundError):
            os.unlink(input_path)
    _register_temp_cleanup(scored_path)
    scoped_temp_paths = temporary_paths if temporary_paths is not None else _temp_file_scope.get()
    if scoped_temp_paths is not None:
        scoped_temp_paths.append(scored_path)
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
    required_output_columns: frozenset[str] | set[str] | None = None,
    write_projection: ScoreWriteProjection | None = None,
    temporary_paths: list[str] | None = None,
    categorical_levels: _CategoricalLevels = None,
    offset_column: str | None = None,
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
    # Validated above: narrow the untrusted ``str`` boundary to the concrete
    # ``ModelFlavor`` domain so the internal dispatch helpers are statically
    # guaranteed a supported flavor (no unsound guess — the guard just raised).
    flavor = cast(ModelFlavor, flavor)

    if required_output_columns is not None:
        if write_projection is not None:
            raise ValueError(
                "Pass either required_output_columns or write_projection to score_frame, not both."
            )
        write_projection = _normalise_score_write_projection(
            required_output_columns,
            output_col=output_col,
            task=task,
        )

    normalised_levels = _normalise_runtime_categorical_levels(
        categorical_levels,
        features=features,
    )

    if batch:
        return _score_batched_unified(
            model,
            lf,
            features,
            cat_feature_names,
            flavor,
            task,
            output_col,
            write_projection=write_projection,
            temporary_paths=temporary_paths,
            categorical_levels=normalised_levels,
            offset_column=offset_column,
        )
    return _score_eager_unified(
        model,
        lf,
        features,
        cat_feature_names,
        flavor,
        task,
        output_col,
        write_projection=write_projection,
        categorical_levels=normalised_levels,
        offset_column=offset_column,
    )


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
    required_output_columns: frozenset[str] | set[str] | None = None,
    temporary_paths: list[str] | None = None,
    categorical_levels: _CategoricalLevels = None,
    offset_column: str | None = None,
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
    offset_column
        Offset column from the feature contract, when the caller has one.
        Authoritative over the model's self-description — the only offset
        source a pyfunc model has, and a redundant confirmation for native
        flavors that self-describe.
    """
    from haute._mlflow_io import _score_eager as score_eager_

    schema = lf.collect_schema()
    features, _missing = _validate_features(scoring_model, schema)
    resolved_offset = (
        offset_column if offset_column is not None else _declared_offset_column(scoring_model)
    )
    normalised_levels = _normalise_runtime_categorical_levels(
        categorical_levels,
        features=features,
    )

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
    write_projection = None
    if not code:
        write_projection = _normalise_score_write_projection(
            required_output_columns,
            output_col=output_col,
            task=task,
        )

    if source == "live" or row_limit:
        eager_lf = lf
        if normalised_levels:
            # Materialise ONCE so domain validation inspects the exact rows
            # that get scored.  Validating the lazy plan would execute the
            # upstream a second time (the eager scorer collects again), and
            # an order- or row-unstable upstream could diverge between the
            # two executions.  The downstream eager collect of this
            # DataFrame-backed plan re-runs no upstream compute.
            from haute._execution_context import ExecutionProfile
            from haute._polars_utils import streaming_collect

            validation_lf = lf
            validation_columns = _score_input_projection_columns(
                lf,
                features,
                write_projection,
                offset_column=resolved_offset,
            )
            if validation_columns is not None:
                validation_lf = lf.select(
                    _ordered_required_columns(
                        schema.names(),
                        validation_columns,
                        context="live model-score categorical validation projection",
                    )
                )
            collected = streaming_collect(
                validation_lf,
                profile=ExecutionProfile.PREVIEW_EAGER,
            )
            _validate_runtime_categorical_values(collected, normalised_levels)
            eager_lf = collected.lazy()
        # Preserve the offset-less call arity: pass the kwarg only when an
        # offset is actually in play, so offset-less scoring (the common case)
        # calls the delegate exactly as before — keeping in-place test doubles
        # that patch ``_score_eager`` with the pre-offset signature working.
        if resolved_offset is None:
            result_lf = score_eager_(scoring_model, eager_lf, features, output_col, task)
        else:
            result_lf = score_eager_(
                scoring_model,
                eager_lf,
                features,
                output_col,
                task,
                offset_column=resolved_offset,
            )
        result_lf = _project_scored_output(
            result_lf,
            write_projection,
            output_col=output_col,
        )
    elif resolved_offset is None:
        result_lf = _score_batched_standalone(
            scoring_model,
            lf,
            features,
            output_col,
            task,
            write_projection=write_projection,
            temporary_paths=temporary_paths,
            categorical_levels=normalised_levels,
        )
    else:
        result_lf = _score_batched_standalone(
            scoring_model,
            lf,
            features,
            output_col,
            task,
            write_projection=write_projection,
            temporary_paths=temporary_paths,
            categorical_levels=normalised_levels,
            offset_column=resolved_offset,
        )

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
    write_projection: ScoreWriteProjection | None = None,
    temporary_paths: list[str] | None = None,
    categorical_levels: _CategoricalLevels = None,
    offset_column: str | None = None,
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
        write_projection=write_projection,
        temporary_paths=temporary_paths,
        categorical_levels=categorical_levels,
        offset_column=offset_column
        if offset_column is not None
        else _declared_offset_column(scoring_model),
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
    source_type : ModelSource
        ``"run"`` or ``"registered"`` — how to locate the model in MLflow.
    run_id : str
        MLflow run ID (used when *source_type* is ``"run"``).
    artifact_path : str
        Artifact path within the run (e.g. ``"model.cbm"``).
    registered_model : str
        Registered model name (used when *source_type* is ``"registered"``).
    version : str
        Model version string (``"1"``, ``"2"``, or ``"latest"``).
    task : Task
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
    feature_contract_path : str | None
        Optional train-time feature contract. When it declares categorical
        value domains, runtime declarations must match and observed values
        are checked before prediction.
    reuse_loaded_model : bool
        When true, pin the loaded model on this scorer instance. Intended for
        short-lived streaming jobs that reuse one scorer across many chunks.
    """

    def __init__(
        self,
        *,
        source_type: ModelSource,
        run_id: str = "",
        artifact_path: str = "",
        registered_model: str = "",
        version: str = "latest",
        task: Task = "regression",
        output_col: str = "prediction",
        code: str = "",
        source_names: list[str] | None = None,
        source: str = "live",
        row_limit: int | None = None,
        required_output_columns: frozenset[str] | set[str] | None = None,
        feature_contract_path: str | None = None,
        categorical_levels: _CategoricalLevels = None,
        reuse_loaded_model: bool = False,
    ) -> None:
        from haute.modelling._feature_contract import normalise_categorical_levels

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
        self.required_output_columns = (
            frozenset(str(c) for c in required_output_columns)
            if required_output_columns is not None
            else None
        )
        self.feature_contract_path = feature_contract_path or None
        self._declared_categorical_levels = (
            normalise_categorical_levels(categorical_levels)
            if categorical_levels is not None
            else None
        )
        self.reuse_loaded_model = reuse_loaded_model
        self._scoring_model: Any | None = None
        self._scoring_model_lock = threading.Lock()

    def _load_scoring_model_uncached(self) -> Any:
        """Load the configured model via the shared MLflow loader."""
        from haute._mlflow_io import load_mlflow_model

        return load_mlflow_model(
            source_type=self.source_type,
            run_id=self.run_id,
            artifact_path=self.artifact_path,
            registered_model=self.registered_model,
            version=self.version,
            task=self.task,
        )

    def _load_scoring_model(self) -> Any:
        """Load the configured model, optionally pinning it for this scorer."""
        if not self.reuse_loaded_model:
            return self._load_scoring_model_uncached()

        if self._scoring_model is not None:
            return self._scoring_model

        with self._scoring_model_lock:
            if self._scoring_model is None:
                self._scoring_model = self._load_scoring_model_uncached()
        return self._scoring_model

    def _categorical_levels_for_score(self) -> dict[str, list[str | None]]:
        """Return the categorical value domains to enforce for this score call."""
        declared = self._declared_categorical_levels
        if self.feature_contract_path is None:
            return {column: list(levels) for column, levels in (declared or {}).items()}

        from haute.modelling._feature_contract import (
            load_contract,
            normalise_categorical_levels,
        )

        expected = load_contract(self.feature_contract_path)
        if not expected.categorical_levels:
            return normalise_categorical_levels(declared, features=expected.features)

        declared_for_contract = {
            column: levels
            for column, levels in (declared or {}).items()
            if column in expected.categorical_levels
        }
        mismatched_levels = {
            column: levels
            for column, levels in declared_for_contract.items()
            if levels != expected.categorical_levels[column]
        }
        if mismatched_levels:
            raise FeatureMismatchError(
                "contract mismatch: categorical_levels",
                field="categorical_levels",
                expected=expected.categorical_levels,
                actual=mismatched_levels,
                feature_contract_path=self.feature_contract_path,
            )
        return {column: list(levels) for column, levels in expected.categorical_levels.items()}

    def _offset_column_for_score(self) -> str | None:
        """Return the offset column the bundled contract declares, if any.

        The contract is the authoritative offset source at score time — the
        only one a pyfunc model has (its signature lists the offset as an
        input but cannot mark which input it is). ``None`` when there is no
        contract or it declares no offset, in which case the scorer falls
        back to a native model's self-description.
        """
        if self.feature_contract_path is None:
            return None
        from haute.modelling._feature_contract import load_contract

        return load_contract(self.feature_contract_path).offset_column

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def score(self, *dfs_positional: _Frame, **dfs_by_name: _Frame) -> _Frame:
        """Load the model, predict, and optionally post-process.

        Accepts one or more upstream LazyFrames (first is the scoring input).
        Returns a LazyFrame with prediction column(s) appended.

        Per MULTI_FRAME_PLAN §4b the executor binds incoming edges as
        keyword arguments; this method accepts both forms so direct
        callers in tests / deploy paths keep working.
        """
        categorical_levels = self._categorical_levels_for_score()
        offset_column = self._offset_column_for_score()
        scoring_model = self._load_scoring_model()

        if dfs_by_name:
            # Reconstruct positional tuple in declared-source order.
            dfs: tuple[_Frame, ...] = tuple(
                dfs_by_name[name] for name in self.source_names if name in dfs_by_name
            )
        else:
            dfs = dfs_positional
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
            required_output_columns=self.required_output_columns,
            categorical_levels=categorical_levels,
            offset_column=offset_column,
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
            write_projection=_normalise_score_write_projection(
                None if self.code else self.required_output_columns,
                output_col=self.output_col,
                task=self.task,
            ),
            categorical_levels=self._categorical_levels_for_score(),
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
        feature_contract_path=cfg.get("feature_contract_path") or None,
        categorical_levels=cfg.get("categorical_levels") or None,
    )
    return scorer.score(*dfs)


# ----------------------------------------------------------------------
# Module-level helpers (shared by the class, kept out of the class body
# because they are pure functions with no dependency on instance state).
# ----------------------------------------------------------------------


def _sink_to_temp(
    lf: pl.LazyFrame,
    *,
    columns: frozenset[str] | set[str] | None = None,
) -> str:
    """Sink a LazyFrame to a temp parquet file via streaming.

    Uses ``fast_checkpoint=True`` for lz4 compression — these temp
    files are read back immediately for batch scoring and then deleted,
    so speed matters more than compression ratio.  Streaming planner errors
    propagate instead of broadening to an eager collect.
    """
    import os
    import tempfile

    from haute._polars_utils import bounded_sink

    sink_lf = lf
    if columns is not None:
        ordered = _ordered_required_columns(
            sink_lf.collect_schema().names(),
            frozenset(columns),
            context="model-score input projection",
        )
        sink_lf = sink_lf.select(ordered)

    fd, path = tempfile.mkstemp(
        suffix=".parquet",
        prefix="haute_score_in_",
    )
    os.close(fd)
    try:
        bounded_sink(sink_lf, path, fast_checkpoint=True)
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(path)
        raise
    return path


def _declared_empty_score_dtypes(
    *,
    flavor: ModelFlavor,
    task: str,
    include_proba: bool,
) -> (
    tuple[
        pl.DataType | type[pl.DataType],
        pl.DataType | type[pl.DataType] | None,
    ]
    | None
):
    """Return task/flavor output dtypes when the scoring contract fixes them."""
    if flavor != "catboost":
        return None
    prediction_dtype = pl.Int64 if task == "classification" else pl.Float64
    proba_dtype = pl.Float64 if include_proba else None
    return prediction_dtype, proba_dtype


def _batch_score_to_parquet(
    scoring_model: Any,
    input_path: str,
    features: list[str],
    output_col: str,
    task: str,
    *,
    write_projection: ScoreWriteProjection | None = None,
    categorical_levels: _CategoricalLevels = None,
) -> str:
    """Score a parquet file in batches, return path to scored output."""
    import os
    import tempfile

    import pyarrow.parquet as pq

    from haute._mlflow_io import (
        _append_classification_proba,
        _positive_class_proba_vector,
        _prepare_predict_frame,
    )

    fd, out_path = tempfile.mkstemp(
        suffix=".parquet",
        prefix="haute_score_out_",
    )
    os.close(fd)

    writer = None
    wrote_any = False
    success = False
    want_proba = task == "classification"
    can_predict_proba = want_proba and _raw_model_supports_predict_proba(scoring_model)
    normalised_levels = _normalise_runtime_categorical_levels(
        categorical_levels,
        features=features,
    )
    offset_column = _declared_offset_column(scoring_model)
    predict_features = _offset_predict_features(
        features,
        scoring_model.flavor,
        offset_column,
    )

    try:
        pf = pq.ParquetFile(input_path)
        input_schema_names = list(pf.schema_arrow.names)
        _require_offset_column(input_schema_names, offset_column)
        for batch in pf.iter_batches(
            batch_size=_SCORE_BATCH_SIZE,
        ):
            chunk_raw = pl.from_arrow(batch)
            if isinstance(chunk_raw, pl.Series):
                chunk = chunk_raw.to_frame()
            else:
                chunk = chunk_raw
            feature_chunk = chunk.select(features)
            _validate_runtime_categorical_values(feature_chunk, normalised_levels)
            x_data = _prepare_predict_frame(
                chunk.select(predict_features),
                predict_features,
                cat_feature_names=scoring_model.cat_feature_names,
                flavor=scoring_model.flavor,
            )
            if offset_column and scoring_model.flavor == "catboost":
                x_data = _catboost_baseline_pool(
                    x_data,
                    chunk,
                    features,
                    scoring_model.cat_feature_names,
                    offset_column,
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
            chunk = _apply_score_write_projection(
                chunk,
                write_projection=write_projection,
                output_col=output_col,
                can_predict_proba=can_predict_proba,
            )
            table = chunk.to_arrow()
            if writer is None:
                writer = pq.ParquetWriter(
                    out_path,
                    table.schema,
                )
            writer.write_table(table)
            wrote_any = True
            del chunk, x_data, table
        if writer is not None:
            active_writer = writer
            writer = None
            active_writer.close()
        if not wrote_any:
            # Zero-row input: write an empty parquet that preserves the input
            # dtypes AND the prediction/proba dtypes the *non-empty* path would
            # have produced.  Hardcoding Float64 here silently diverged from the
            # non-empty path — e.g. a CatBoost classifier emits Int64 hard
            # labels, so an empty score and a non-empty score of the SAME model
            # produced parquet files with incompatible prediction schemas.
            #
            # Prefer declared flavor/task output contracts.  This avoids
            # asking CatBoost to score an artificial all-null row, which is
            # not a valid input for categorical models.  Metadata-free model
            # flavors still use a schema-shaped probe so their output dtype is
            # learned rather than guessed.
            input_schema = pl.read_parquet_schema(input_path)
            declared_dtypes = _declared_empty_score_dtypes(
                flavor=scoring_model.flavor,
                task=task,
                include_proba=can_predict_proba,
            )
            probe_x: Any | None = None
            prediction_dtype: pl.DataType | type[pl.DataType]
            declared_proba_dtype: pl.DataType | type[pl.DataType] | None
            if declared_dtypes is None:
                probe = pl.DataFrame(
                    {
                        c: pl.Series([None], dtype=input_schema.get(c, pl.Float64))
                        for c in input_schema_names
                    }
                )
                probe_x = _prepare_predict_frame(
                    probe.select(predict_features),
                    predict_features,
                    cat_feature_names=scoring_model.cat_feature_names,
                    flavor=scoring_model.flavor,
                )
            if declared_dtypes is None and offset_column and scoring_model.flavor == "catboost":
                # Dtype probe only: a null baseline would make CatBoost
                # reject the Pool, so probe at the unit raw-score offset 0.
                from catboost import Pool

                cat_indices = [
                    i for i, f in enumerate(features) if f in scoring_model.cat_feature_names
                ]
                probe_x = Pool(
                    data=probe_x,
                    cat_features=cat_indices if cat_indices else None,
                    baseline=np.zeros(1),
                )
            if declared_dtypes is None:
                prediction_dtype = pl.Series(
                    output_col,
                    scoring_model.predict(probe_x),
                ).dtype
                declared_proba_dtype = None
            else:
                prediction_dtype, declared_proba_dtype = declared_dtypes
            empty = pl.DataFrame(
                {
                    c: pl.Series([], dtype=input_schema.get(c, pl.Float64))
                    for c in input_schema_names
                }
            ).with_columns(pl.Series(output_col, [], dtype=prediction_dtype))
            if can_predict_proba:
                proba_dtype: pl.DataType | type[pl.DataType]
                if declared_proba_dtype is None:
                    proba_vector = _positive_class_proba_vector(
                        scoring_model.predict_proba(probe_x), output_col
                    )
                    proba_dtype = pl.Series(f"{output_col}_proba", proba_vector).dtype
                else:
                    proba_dtype = declared_proba_dtype
                empty = empty.with_columns(pl.Series(f"{output_col}_proba", [], dtype=proba_dtype))
            empty = _apply_score_write_projection(
                empty,
                write_projection=write_projection,
                output_col=output_col,
                can_predict_proba=can_predict_proba,
            )
            pq.write_table(empty.to_arrow(), out_path)
        success = True
    finally:
        if writer is not None:
            writer.close()
        if not success:
            with suppress(FileNotFoundError):
                os.unlink(out_path)
    return out_path
