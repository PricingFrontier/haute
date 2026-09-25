"""MLflow model loading utilities for the MODEL_SCORE node.

Thread-safe LRU cache for models loaded from MLflow.
Supports CatBoost (native ``.cbm``) and any MLflow pyfunc model.

Every load resolves its destination to a :class:`~haute._mlflow_utils.ResolvedBackend`
exactly once, before any cache lookup, and threads that object through
download, load, and lock selection. The backend is part of every cache
identity, so the same run ID and artifact path on two destinations — or on
two endpoints of one category — never alias:

- disk cache: ``.cache/models/<backend digest>/<run_id>/<sha256 of
  artifact_path>/artifact<suffix>``;
- in-memory cache key: the backend's secret-free identity is the last element;
- artifact I/O locks: keyed on ``(backend digest, run_id, artifact_path)``.

The backend's ``tracking_uri`` is a connection value that may carry
environment credentials, so it never reaches a log line, a cache path, or a
lock key — only ``mode`` and ``digest`` are logged.
"""

from __future__ import annotations

import os
import threading
import uuid
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from weakref import WeakValueDictionary

import numpy as np
import polars as pl

from haute._logging import get_logger
from haute._lru_cache import LRUCache
from haute._mlflow_utils import (
    ResolvedBackend,
    mlflow_fluent_operation,
    resolve_mlflow_source,
    set_tracking_uri_preserving_env,
)
from haute._model_flavors import (
    _SUPPORTED_FLAVORS,
    NATIVE_WRAPPER_FLAVORS,
    NATIVE_WRAPPER_SUFFIXES,
    ModelFlavor,
)

if TYPE_CHECKING:
    from catboost import CatBoostClassifier, CatBoostRegressor
    from mlflow.tracking import MlflowClient

    from haute._model_scorer import ScoreWriteProjection

logger = get_logger(component="mlflow_io")

_MODEL_CACHE_MAX_SIZE = 16
_DISK_CACHE_MAX_DIRS = 50
_DISK_CACHE_EVICTION_PREFIX = ".evicting-"


class _ArtifactNotFoundError(FileNotFoundError):
    """Internal sentinel: a probe completed and no artifact matched.

    Subclasses :class:`FileNotFoundError` so callers can catch the broad
    file-missing category while internal probe callers catch the narrower
    sentinel.  A bare ``FileNotFoundError`` bubbling out of
    ``list_artifacts`` (future MLflow behaviour, local-fs shim, etc.)
    is **not** swallowed by ``except _ArtifactNotFoundError`` — it surfaces
    as an infrastructure error the operator must see.
    """


# ---------------------------------------------------------------------------
# Cache observability counters (issue #98)
# ---------------------------------------------------------------------------
# Module-level hit/miss counters scraped by ``get_model_cache_stats()`` for
# ops dashboards.  Incremented under a dedicated stats lock rather than
# piggy-backing on ``_model_cache._lock``: the LRU lock is a private
# implementation detail and we do not want counter updates racing with
# cache reads/writes inside that critical section — counters are
# observability, not cache correctness.
_model_cache_hits: int = 0
_model_cache_misses: int = 0
_model_cache_stats_lock = threading.Lock()


def _flavor_from_artifact(artifact_path: str) -> ModelFlavor:
    """Derive the model flavor from an artifact filename extension.

    Kept in sync with the dispatch in :func:`load_mlflow_model`.  Used by
    the fast-path cache hit site where the artifact string is known up
    front but the full ``resolve_mlflow_source`` round-trip has been
    skipped.
    """
    if artifact_path.endswith(".cbm"):
        return "catboost"
    if artifact_path.endswith(".rsglm"):
        return "rustystats"
    for suffix, wrapper_flavor in NATIVE_WRAPPER_SUFFIXES.items():
        if artifact_path.endswith(suffix):
            return wrapper_flavor
    return "pyfunc"


def get_model_cache_stats() -> dict[str, int]:
    """Return a snapshot of cache hit/miss counters.

    Returns a dict of the form ``{"hits": N, "misses": M}`` for scraping
    via ops / debug endpoints.  The snapshot is consistent: both counters
    are read under the stats lock in one atomic step so a caller never
    sees a mid-increment tear.
    """
    with _model_cache_stats_lock:
        return {"hits": _model_cache_hits, "misses": _model_cache_misses}


def _record_cache_hit(*, run_id: str, artifact_path: str, flavor: str) -> None:
    """Log a structured ``model_cache_hit`` event and bump the hit counter.

    Structured fields (``run_id``, ``artifact_path``, ``flavor``) mirror
    the ``model_cache_miss`` event vocabulary so log aggregators can
    compute hit rate per-run, per-artifact, or per-flavor without
    re-parsing a stringified tuple.
    """
    global _model_cache_hits
    with _model_cache_stats_lock:
        _model_cache_hits += 1
    logger.info(
        "model_cache_hit",
        run_id=run_id,
        artifact_path=artifact_path,
        flavor=flavor,
    )


def _record_cache_miss(*, run_id: str, artifact_path: str, flavor: str) -> None:
    """Log a structured ``model_cache_miss`` event and bump the miss counter.

    Emitted before the (potentially expensive) model download so the miss
    is visible even when the subsequent load fails — production on-call
    should still see the miss that triggered the failure.
    """
    global _model_cache_misses
    with _model_cache_stats_lock:
        _model_cache_misses += 1
    logger.info(
        "model_cache_miss",
        run_id=run_id,
        artifact_path=artifact_path,
        flavor=flavor,
    )


def _reset_model_cache_stats() -> None:
    """Reset the hit/miss counters to zero.

    Called from :func:`clear_model_cache` to pair cache clearing with
    stats reset — the caller's mental model of "clear everything
    cache-related" wins over "clear data but keep stats".
    """
    global _model_cache_hits, _model_cache_misses
    with _model_cache_stats_lock:
        _model_cache_hits = 0
        _model_cache_misses = 0


class _ModelCacheWithCascade(LRUCache[tuple[str, ...], "ScoringModel"]):
    """LRU cache for ``ScoringModel`` instances with a validation-cache cascade.

    Wraps :meth:`LRUCache.put` and :meth:`LRUCache.clear` so every eviction
    triggers the targeted ``_invalidate_feature_validation_cache_for`` hook
    in :mod:`haute._model_scorer`, and a full ``clear()`` cascades into a
    blanket ``_clear_feature_validation_cache()``.

    This is a *shim* on top of the base LRU contract — no new public API
    on :class:`LRUCache` itself.  The cascade is invoked via a lazy import
    inside the override to keep the module-import ordering clean:
    ``_mlflow_io`` and ``_model_scorer`` otherwise have a cyclic dep.
    """

    __slots__ = ()

    def put(self, key: tuple[str, ...], value: ScoringModel) -> bool:
        with self._lock:
            # Snapshot live entries *before* the put so we can diff after
            # super().put() completes.  The diff is exact because puts
            # are serialised on self._lock and eviction happens inline.
            before = dict(self._data)
            stored = super().put(key, value)
            # Any key that was live before but is gone now was evicted.
            after_keys = set(self._data)
            evicted_models = [v for k, v in before.items() if k not in after_keys]
        if evicted_models:
            from haute import _model_scorer as _ms

            for sm in evicted_models:
                _ms._invalidate_feature_validation_cache_for(sm)
        return stored

    def clear(self) -> None:
        with self._lock:
            super().clear()
        from haute import _model_scorer as _ms

        _ms._clear_feature_validation_cache()

    def evict_matching(
        self,
        predicate: Callable[[tuple[str, ...]], bool],
    ) -> int:
        """Evict every entry whose key satisfies *predicate*, cascading.

        Returns the number of entries evicted.  Used by
        :func:`clear_model_cache` to implement targeted ``run_id=...``
        clears without wiping the whole cache.

        Delegates eviction to :meth:`LRUCache.evict_where` so no call
        site reaches into the internal data structures directly.  The
        cascade runs **outside** the base cache's lock — ``evict_where``
        returns evicted values, and we invalidate dependent caches here
        without any of our own locking held, avoiding a potential
        deadlock if the callback reaches back into another cache.
        """
        evicted = self.evict_where(predicate)
        if not evicted:
            return 0
        from haute import _model_scorer as _ms

        for sm in evicted:
            _ms._invalidate_feature_validation_cache_for(sm)
        return len(evicted)


_model_cache: _ModelCacheWithCascade = _ModelCacheWithCascade(
    max_size=_MODEL_CACHE_MAX_SIZE,
)


def _disk_cache_root() -> Path:
    """Return the working-directory-local root for cached model artifacts."""
    return Path.cwd() / ".cache" / "models"


# ---------------------------------------------------------------------------
# Per-artifact I/O locks — serialize download / load / retry-delete (4a.7)
# ---------------------------------------------------------------------------
# Without serialization, concurrent loads of the same model each miss the
# in-memory cache, each download the artifact (thundering herd), and the
# loser's ``shutil.move`` lands on the cache file the winner is actively
# reading (on Windows the rename falls back to an in-place copy over the
# open file); the corrupt-retry path can likewise ``unlink`` a file
# mid-read.  One lock per on-disk artifact identity — which includes the
# resolved backend's digest — fixes all three: the first caller
# downloads/loads, same-artifact callers wait and then reuse the cached
# result, and distinct artifacts (including the same run on a different
# backend) proceed concurrently.
#
# ``WeakValueDictionary`` + guard: entries evaporate once no caller
# holds the lock, so the table never grows unboundedly.
_artifact_io_locks: WeakValueDictionary[tuple[str, str, str], threading.RLock] = (
    WeakValueDictionary()
)
_artifact_io_locks_guard = threading.Lock()
_disk_cache_active_runs: Counter[str] = Counter()
_disk_cache_active_runs_guard = threading.Lock()


def _validate_disk_cache_run_id(run_id: str) -> None:
    if (
        not run_id
        or run_id in {".", ".."}
        or "\x00" in run_id
        or os.sep in run_id
        or (os.altsep is not None and os.altsep in run_id)
        or "/" in run_id
        or "\\" in run_id
        or ".." in run_id
    ):
        raise ValueError(f"Invalid run_id: {run_id!r}")


def _validate_artifact_path(artifact_path: str) -> None:
    from pathlib import PurePosixPath

    if not artifact_path or "\\" in artifact_path or "\x00" in artifact_path:
        raise ValueError(f"Invalid artifact_path: {artifact_path!r}")
    raw_parts = artifact_path.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise ValueError(f"Invalid artifact_path: {artifact_path!r}")
    parsed = PurePosixPath(artifact_path)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
        raise ValueError(f"Invalid artifact_path: {artifact_path!r}")


def _validate_backend_digest(backend_digest: str) -> None:
    """Reject anything that is not a ``ResolvedBackend`` filesystem digest.

    The digest is the only part of a backend identity that reaches the
    filesystem, so it is pinned to the shape :func:`resolve_backend` mints
    (16 lowercase hex characters) before it is used as a directory name.
    """
    if len(backend_digest) != 16 or any(c not in "0123456789abcdef" for c in backend_digest):
        raise ValueError(f"Invalid backend digest: {backend_digest!r}")


def _artifact_cache_path(
    cache_root: Path,
    backend_digest: str,
    run_id: str,
    artifact_path: str,
) -> Path:
    """Return the safe disk-cache path for a run artifact on one backend.

    ``<cache_root>/<backend digest>/<run_id>/<sha256 of artifact_path>/artifact<suffix>``
    — the backend partition means two destinations never share a cached
    file even for identical run IDs and artifact paths.
    """
    from pathlib import PurePosixPath

    _validate_backend_digest(backend_digest)
    _validate_disk_cache_run_id(run_id)
    _validate_artifact_path(artifact_path)
    digest = sha256(artifact_path.encode("utf-8")).hexdigest()
    suffix = PurePosixPath(artifact_path).suffix
    file_name = f"artifact{suffix}" if suffix else "artifact"
    cache_root_abs = cache_root.resolve()
    candidate = (cache_root / backend_digest / run_id / digest / file_name).resolve()
    if not candidate.is_relative_to(cache_root_abs):
        raise ValueError(
            f"Invalid artifact cache identity: run_id={run_id!r}, artifact_path={artifact_path!r}"
        )
    return candidate


@contextmanager
def _disk_cache_run_in_use(run_id: str) -> Iterator[None]:
    """Mark a run directory as unsafe for eviction for this critical section."""
    with _disk_cache_active_runs_guard:
        _disk_cache_active_runs[run_id] += 1
    try:
        yield
    finally:
        with _disk_cache_active_runs_guard:
            _disk_cache_active_runs[run_id] -= 1
            if _disk_cache_active_runs[run_id] <= 0:
                del _disk_cache_active_runs[run_id]


def _active_disk_cache_runs() -> frozenset[str]:
    with _disk_cache_active_runs_guard:
        return frozenset(_disk_cache_active_runs)


def _artifact_io_lock(backend_digest: str, run_id: str, artifact_path: str) -> threading.RLock:
    """Return the per-(backend, run, artifact-path) lock for one cached file.

    Keyed on the full disk-cache identity used by
    :func:`_artifact_cache_path` — the resolved backend's digest plus the
    artifact's full MLflow path — so every code path that could touch the
    same cached file (downloader, loader, corrupt-retry deleter, deploy
    bundler) is mutually exclusive without serializing artifacts that
    merely share a basename, or the same run on two backends.  Reentrant so
    the load path can re-enter through ``_resolve_artifact_local`` while
    already holding the lock.
    """
    key = (backend_digest, run_id, artifact_path)
    with _artifact_io_locks_guard:
        return _artifact_io_locks.setdefault(key, threading.RLock())


def _model_cache_key(
    *,
    source_type: str,
    run_id: str,
    version: str,
    artifact_path: str,
    task: str,
    artifact_fingerprint: str,
    backend_identity: str,
) -> tuple[str, ...]:
    """Build the in-process model cache key.

    ``artifact_fingerprint`` folds the identity of the model artifact
    *bytes* into the key (see :func:`_local_artifact_fingerprint`), so a
    re-logged run or a ``version="latest"`` retrain-in-place — same run
    reference, different bytes — misses instead of serving the previously
    loaded model on a long-lived server.  It is a required keyword so no
    call site can silently drop the byte-identity component.  Pass ``""``
    only where no local artifact file exists to fingerprint (pyfunc
    models loaded through MLflow by URI — a documented residual).

    ``backend_identity`` is the resolved backend's secret-free identity and
    is always the LAST element, so the same run ID and artifact path on two
    destinations — or on two endpoints of the same category — never alias.
    It is a required keyword for the same reason as the fingerprint: no
    call site can silently drop the backend from the key.

    ``run_id`` stays in slot 1: targeted ``clear_model_cache(run_id=...)``
    eviction matches on ``key[1]``.
    """
    if version:
        return (
            source_type,
            run_id,
            version,
            artifact_path,
            task,
            artifact_fingerprint,
            backend_identity,
        )
    return (source_type, run_id, artifact_path, task, artifact_fingerprint, backend_identity)


def _local_artifact_fingerprint(artifact_path: str, local_path: str) -> str:
    """Byte-identity fingerprint of the local model artifact file.

    Reuses the deploy path's
    :func:`~haute.deploy._scorer.artifact_identity_fingerprint` derivation
    (stat-gated content hash) rather than minting a parallel one, so the
    serve-path in-process cache and the deploy output-schema cache agree
    on what "the same artifact" means.  Imported lazily to avoid a
    module-import cycle (``haute.deploy._scorer`` imports from this
    module's dependents).
    """
    from haute.deploy._scorer import artifact_identity_fingerprint

    return artifact_identity_fingerprint({artifact_path: local_path})


# ---------------------------------------------------------------------------
# ScoringModel — uniform interface for all model flavors
# ---------------------------------------------------------------------------


class ScoringModel:
    """Carrier for a loaded model plus the metadata scoring needs.

    Holds the raw flavor-specific model object (CatBoost / pyfunc /
    RustyStats GLM) together with the declared ``feature_names``,
    ``cat_feature_names``, and ``flavor`` string.  All scoring internals
    dispatch explicitly on ``flavor``; there is no ``__getattr__``
    proxying — callers must go through the declared ``predict`` /
    ``predict_proba`` / ``raw_model`` surface.

    ``offset_column`` names the offset/exposure column the model was
    trained with (``None`` when the model has none), and ``offset_link``
    records how it enters the prediction: ``log`` for a positive exposure
    multiplier, ``identity`` for an additive term.  Scoring frames must carry
    the column: the CatBoost path re-supplies it as a ``Pool`` baseline, the
    RustyStats path hands it to the model inside the predict frame (it is
    already part of ``required_columns``).  A missing column fails loud —
    scoring never silently proceeds on an offset-0/absent basis.
    """

    __slots__ = (
        "_model",
        "feature_names",
        "cat_feature_names",
        "flavor",
        "offset_column",
        "offset_link",
    )

    def __init__(
        self,
        model: Any,
        feature_names: list[str],
        cat_feature_names: frozenset[str] = frozenset(),
        flavor: ModelFlavor = "pyfunc",
        offset_column: str | None = None,
        offset_link: str | None = None,
    ) -> None:
        self._model = model
        self.feature_names = feature_names
        self.cat_feature_names = cat_feature_names
        self.flavor = flavor
        self.offset_column = offset_column
        self.offset_link = offset_link

    @property
    def raw_model(self) -> Any:
        """Access the underlying model object."""
        return self._model

    def predict(self, x_data: Any) -> np.ndarray:
        """Return 1-D array of predictions (binary labels from the positive probability)."""
        return native_predictions(self._model, x_data, self.flavor)

    def predict_proba(self, x_data: Any) -> np.ndarray | None:
        """Return class probabilities, or ``None`` if unsupported."""
        fn = getattr(self._model, "predict_proba", None)
        if fn is None:
            return None
        return np.asarray(fn(x_data))


# ---------------------------------------------------------------------------
# CatBoost helpers
# ---------------------------------------------------------------------------


# CatBoost losses that train a classifier; every other loss trains a regressor.
_CATBOOST_CLASSIFICATION_LOSSES = frozenset(
    {"Logloss", "CrossEntropy", "MultiClass", "MultiClassOneVsAll"}
)


def _load_catboost_model(path: str, task: str) -> CatBoostRegressor | CatBoostClassifier:
    """Load a CatBoost model from a local file path as the *task* it will score.

    The model file records the loss it was trained with, which fixes its task.
    Loading a classifier as a regressor (or the reverse) predicts plausible but
    wrong numbers, so a *task* that contradicts the recorded loss is rejected.

    Raises:
        ConfigError: the model was trained for the other task.
    """
    from haute.errors import ConfigError

    if task == "classification":
        from catboost import CatBoostClassifier

        model = CatBoostClassifier()
    else:
        from catboost import CatBoostRegressor

        model = CatBoostRegressor()
    model.load_model(path)
    params = model.get_all_params()
    recorded_loss = params.get("loss_function") if isinstance(params, dict) else None
    # A model file without a recorded loss keeps the node's explicit task.
    loss = recorded_loss.partition(":")[0] if isinstance(recorded_loss, str) else ""
    if loss:
        trained_task = "classification" if loss in _CATBOOST_CLASSIFICATION_LOSSES else "regression"
        if trained_task != task:
            raise ConfigError(
                f"This CatBoost model was trained for {trained_task} (loss {loss}) but the "
                f"node scores it as {task}. Set the node's task to {trained_task}.",
                trained_task=trained_task,
                task=task,
            )
    return model


def catboost_class_labels(model: Any) -> tuple[Any, Any] | None:
    """``(negative, positive)`` for a binary CatBoost classifier, else ``None``.

    A model Haute trained records the labels it encoded as 0/1; any other
    binary CatBoost classifier uses its own ``classes_`` order.
    """
    import json

    from haute.modelling._algorithms import CATBOOST_CLASS_LABELS_METADATA_KEY

    try:
        recorded = model.get_metadata().get(CATBOOST_CLASS_LABELS_METADATA_KEY)
    except Exception:
        recorded = None
    if isinstance(recorded, str) and recorded:
        labels = json.loads(recorded)
        if not isinstance(labels, list) or len(labels) != 2:
            from haute.errors import ConfigError

            raise ConfigError(
                "This CatBoost model's recorded class labels are malformed; retrain it.",
            )
        return labels[0], labels[1]
    classes = getattr(model, "classes_", None)
    if classes is None or len(classes) != 2:
        return None
    return _python_scalar(classes[0]), _python_scalar(classes[1])


def _python_scalar(value: Any) -> Any:
    return value.item() if isinstance(value, np.generic) else value


def binary_labels(positive_proba: np.ndarray, labels: tuple[Any, Any]) -> np.ndarray:
    """The label rule every binary classifier shares: positive iff proba > 0.5."""
    negative, positive = labels
    return np.where(np.asarray(positive_proba) > 0.5, positive, negative)


def native_predictions(model: Any, x_data: Any, flavor: str) -> np.ndarray:
    """1-D predictions; a binary CatBoost classifier's labels follow its probability."""
    if flavor == "catboost":
        labels = catboost_class_labels(model)
        if labels is not None and callable(getattr(model, "predict_proba", None)):
            positive = _positive_class_proba_vector(model.predict_proba(x_data), "prediction")
            return binary_labels(positive, labels)
    return np.asarray(model.predict(x_data)).flatten()


#: The ScoringModel flavor each Haute-trained algorithm loads as.
_ALGORITHM_FLAVORS = {
    "catboost": "catboost",
    "glm": "rustystats",
    "xgboost": "xgboost",
    "lightgbm": "lightgbm",
    "ebm": "ebm",
}


def verify_contract_identity(identity: Any, scoring_model: ScoringModel) -> None:
    """Fail when a loaded model is not the model its contract's identity describes."""
    from haute.errors import ConfigError

    expected_flavor = _ALGORITHM_FLAVORS.get(identity.algorithm)
    if expected_flavor != scoring_model.flavor:
        raise ConfigError(
            f"The feature contract describes a {identity.algorithm} model, but the model file "
            f"loads as {scoring_model.flavor}. Use the contract saved with this model.",
            algorithm=identity.algorithm,
            flavor=scoring_model.flavor,
        )
    if scoring_model.flavor in NATIVE_WRAPPER_FLAVORS and identity.loss:
        from haute.modelling._descriptors import algorithm_descriptor

        descriptor = algorithm_descriptor(identity.algorithm)
        objective = scoring_model.raw_model.objective()
        task = "classification" if identity.link == "logit" else "regression"
        expected = descriptor.native_loss(task, identity.loss).objective
        if objective != expected:
            raise ConfigError(
                f"The feature contract describes a {identity.loss} model, but this "
                f"{descriptor.label} model was trained with {objective}. Use the contract "
                "saved with this model.",
                contract_loss=identity.loss,
                model_objective=objective,
            )
    if scoring_model.flavor == "catboost" and identity.loss:
        params = scoring_model.raw_model.get_all_params()
        recorded = params.get("loss_function") if isinstance(params, dict) else None
        loss = recorded.partition(":")[0] if isinstance(recorded, str) else ""
        if loss and loss != identity.loss:
            raise ConfigError(
                f"The feature contract describes a {identity.loss} model, but this CatBoost "
                f"model was trained with {loss}. Use the contract saved with this model.",
                contract_loss=identity.loss,
                model_loss=loss,
            )


def _catboost_offset_column(model: Any) -> str | None:
    """Read the trained-with offset column stamped into the .cbm metadata.

    ``CatBoostAlgorithm.fit`` records the offset column name under
    ``CATBOOST_OFFSET_METADATA_KEY`` because the .cbm format has no native
    baseline memory — without this, a served model would silently score
    from baseline 0. Only an absent key means "no offset", so a failed
    metadata read propagates.
    """
    from haute.modelling._algorithms import CATBOOST_OFFSET_METADATA_KEY

    value = model.get_metadata().get(CATBOOST_OFFSET_METADATA_KEY)
    # Strict str gate: metadata proxies (and mocked models in tests) can
    # return non-string truthy objects for absent keys.
    return value if isinstance(value, str) and value else None


def _catboost_offset_link(model: Any) -> str | None:
    """Read how a CatBoost model's offset enters its raw score.

    ``None`` when the model records no offset column. A model that records
    an offset column without its link is refused: its baseline cannot be
    rebuilt the way it was trained.
    """
    from haute.errors import ConfigError
    from haute.modelling._algorithms import CATBOOST_OFFSET_LINK_METADATA_KEY, OFFSET_LINKS

    column = _catboost_offset_column(model)
    if column is None:
        return None
    value = model.get_metadata().get(CATBOOST_OFFSET_LINK_METADATA_KEY)
    if not isinstance(value, str) or value not in OFFSET_LINKS:
        raise ConfigError(
            f"This CatBoost model records offset column {column!r} but not how the offset "
            "enters its predictions. Retrain it with this version of Haute.",
            offset_column=column,
        )
    return value


def rustystats_offset_column(model: Any) -> str | None:
    """The exposure (log link) or offset (other links) column a GLM was fitted with."""
    for attribute in ("_exposure_spec", "_offset_spec"):
        value = getattr(model, attribute, None)
        if isinstance(value, str) and value:
            return value
    return None


def rustystats_offset_link(model: Any) -> str | None:
    """``log`` for a GLM fitted with an exposure column, ``identity`` for an offset."""
    exposure = getattr(model, "_exposure_spec", None)
    if isinstance(exposure, str) and exposure:
        return "log"
    offset = getattr(model, "_offset_spec", None)
    if isinstance(offset, str) and offset:
        return "identity"
    return None


def _wrap_catboost(model: CatBoostRegressor | CatBoostClassifier, *, source: str) -> ScoringModel:
    """Wrap a raw CatBoost model in a ``ScoringModel``.

    *source* names the model (its file, or its run artifact) in the refusal
    raised when its metadata cannot be read.

    Raises:
        ConfigError: the model's metadata cannot be read, so whether it was
            trained with an offset is unknown.
    """
    from haute.errors import ConfigError

    try:
        model.get_metadata()
    except Exception as exc:
        raise ConfigError(
            f"The metadata of CatBoost model {source} cannot be read "
            f"({type(exc).__name__}: {exc}), so Haute cannot tell whether it was "
            "trained with an offset and will not score it."
        ) from exc
    feature_names = list(model.feature_names_)
    cat_idx = (
        set(model.get_cat_feature_indices()) if hasattr(model, "get_cat_feature_indices") else set()
    )
    cat_names = frozenset(feature_names[i] for i in cat_idx if i < len(feature_names))
    return ScoringModel(
        model=model,
        feature_names=feature_names,
        cat_feature_names=cat_names,
        flavor="catboost",
        offset_column=_catboost_offset_column(model),
        offset_link=_catboost_offset_link(model),
    )


def _load_rustystats_model(path: str, *, source: str | None = None) -> ScoringModel:
    """Load a RustyStats GLM from a ``.rsglm`` binary file.

    *source* names the model in errors (an MLflow run); the file name otherwise.

    ``required_columns`` includes expression sources, offsets, and complement
    columns. RustyStats 0.9 reports named encoding keys there even though its
    predictor reads their ``variable`` source. Resolve those aliases using
    the saved terms, preserving the other required inputs and their order.
    """
    import rustystats as rs

    from haute.errors import ConfigError
    from haute.modelling._glm_terms import ENCODING_TERM_TYPES

    with open(path, "rb") as f:
        data = f.read()
    try:
        model = rs.GLMModel.from_bytes(data)
    except rs.exceptions.ValidationError as exc:
        # RustyStats refuses files written under another serialization schema,
        # e.g. a GLM trained before the RustyStats 0.9 upgrade.
        raise ConfigError(
            f"The RustyStats GLM from {source or repr(Path(path).name)} was saved by an "
            f"older RustyStats and cannot be loaded by RustyStats {rs.__version__}. "
            "Retrain it with this version of Haute."
        ) from exc
    terms = model.terms_dict
    aliases = (
        {
            name: spec["variable"]
            for name, spec in terms.items()
            if spec.get("type") in ENCODING_TERM_TYPES and "variable" in spec
        }
        if terms is not None
        else {}
    )
    feature_names = list(dict.fromkeys(aliases.get(name, name) for name in model.required_columns))
    return ScoringModel(
        model=model,
        feature_names=feature_names,
        cat_feature_names=frozenset(),
        flavor="rustystats",
        offset_column=rustystats_offset_column(model),
        offset_link=rustystats_offset_link(model),
    )


def load_local_model(
    path: str,
    task: str = "regression",
    *,
    contract_path: str | None = None,
    source: str | None = None,
) -> ScoringModel:
    """Load a model from a local file path (e.g. bundled deploy artifact).

    Auto-detects flavor from file extension:
    - ``.cbm`` → CatBoost native loader
    - ``.rsglm`` → RustyStats GLM loader
    - ``.ubj`` / ``.lgbm`` / ``.ebm`` → Haute's native wrappers; an EBM loads
      under *contract_path* when given, else the contract saved beside it
    - Otherwise → not yet supported (pyfunc local loading planned)

    *source* names the model in errors (an MLflow run); the path otherwise.
    """
    if path.endswith(".cbm"):
        raw = _load_catboost_model(path, task)
        return _wrap_catboost(raw, source=source or repr(path))
    if path.endswith(".rsglm"):
        return _load_rustystats_model(path, source=source)
    for suffix, wrapper_flavor in NATIVE_WRAPPER_SUFFIXES.items():
        if path.endswith(suffix):
            return _load_wrapper_model(path, task, wrapper_flavor, contract_path=contract_path)
    raise NotImplementedError(
        f"Local model loading not yet supported for: {path!r}. Supported formats: .cbm "
        "(CatBoost), .rsglm (RustyStats GLM), .ubj (XGBoost), .lgbm (LightGBM), .ebm (EBM)."
    )


def model_contract_candidates(path: str | Path) -> list[Path]:
    """Where a model file's feature contract sits: beside it, by name or in a package."""
    from haute.modelling._feature_contract import CONTRACT_FILENAME
    from haute.modelling._training_job import model_contract_filename

    model_file = Path(path)
    return [
        model_file.with_name(model_contract_filename(model_file.stem)),
        model_file.parent / CONTRACT_FILENAME,
    ]


def _sibling_contract(path: str) -> Any:
    """The feature contract saved with *path*; an EBM cannot load without one."""
    from haute.errors import ConfigError
    from haute.modelling._feature_contract import load_contract

    for candidate in model_contract_candidates(path):
        if candidate.is_file():
            return load_contract(candidate)
    raise ConfigError(
        f"No feature contract was found beside {Path(path).name}. An EBM model loads only "
        "with the contract it was trained with (saved next to it as "
        f"{model_contract_candidates(path)[0].name}).",
        model_path=str(path),
    )


def _run_contract_artifact(artifact: str) -> str:
    """The run-relative path of the contract logged beside *artifact*."""
    from pathlib import PurePosixPath

    return str(PurePosixPath(artifact).with_name(model_contract_candidates(artifact)[0].name))


def _ebm_identity_fingerprint(
    artifact: str, local_path: str, contract_artifact: str, contract_path: str
) -> str:
    """An EBM's cache identity: its model bytes and the contract it loads under."""
    from haute.deploy._scorer import artifact_identity_fingerprint

    return artifact_identity_fingerprint({artifact: local_path, contract_artifact: contract_path})


def _loaded_artifact_fingerprint(
    flavor: str,
    mlflow_mod: Any,
    backend: ResolvedBackend,
    run_id: str,
    artifact: str,
    local_path: str,
) -> str:
    """The cache identity of a run artifact: its bytes, plus an EBM's contract.

    Lookup and insertion both use this, so an entry is always found under the
    key it was stored with.
    """
    if flavor != "ebm":
        return _local_artifact_fingerprint(artifact, local_path)
    return _ebm_identity_fingerprint(
        artifact,
        local_path,
        _run_contract_artifact(artifact),
        _resolve_run_contract(mlflow_mod, backend, run_id, artifact),
    )


def _resolve_run_contract(
    mlflow_mod: Any, backend: ResolvedBackend, run_id: str, artifact: str
) -> str:
    """Fetch the feature contract a run logged beside *artifact* (an EBM needs it to load)."""

    from haute.errors import ConfigError

    contract_artifact = _run_contract_artifact(artifact)
    try:
        return _resolve_artifact_local(mlflow_mod, backend, run_id, contract_artifact)
    except Exception as exc:
        raise ConfigError(
            f"Run {run_id} has no feature contract {contract_artifact} beside {artifact}; an "
            f"EBM model loads only with the contract it was trained with ({exc}).",
            run_id=run_id,
            artifact_path=artifact,
        ) from exc


def _load_wrapper_model(
    path: str, task: str, flavor: ModelFlavor, *, contract_path: str | None = None
) -> ScoringModel:
    """Load a Haute-trained native model as the *task* it will score.

    XGBoost and LightGBM files describe themselves; an EBM file is the bare
    estimator and loads under its feature contract: *contract_path* when the
    caller fetched it (the MLflow cache keeps artifacts apart), else the one
    saved beside the model.
    """
    from haute.errors import ConfigError

    if flavor == "ebm":
        from haute.modelling._ebm import EBMModel
        from haute.modelling._feature_contract import load_contract

        contract = load_contract(contract_path) if contract_path else _sibling_contract(path)
        model: Any = EBMModel.load(path, contract)
        label = "EBM"
    elif flavor == "lightgbm":
        from haute.modelling._lightgbm import LightGBMModel

        model = LightGBMModel.load(path)
        label = "LightGBM"
    else:
        from haute.modelling._xgboost import XGBoostModel

        model = XGBoostModel.load(path)
        label = "XGBoost"
    if model.task != task:
        raise ConfigError(
            f"This {label} model was trained for {model.task} but the node scores it as "
            f"{task}. Set the node's task to {model.task}.",
            trained_task=model.task,
            task=task,
        )
    return ScoringModel(
        model=model,
        feature_names=list(model.features),
        cat_feature_names=model.cat_feature_names,
        flavor=flavor,
        offset_column=model.offset_column,
        offset_link=model.offset_link,
    )


# ---------------------------------------------------------------------------
# Pyfunc helpers
# ---------------------------------------------------------------------------


def _load_pyfunc_model(
    mlflow_module: Any,
    run_id: str,
    artifact_path: str,
    *,
    backend: ResolvedBackend,
) -> Any:
    """Load a model via MLflow pyfunc flavor from the resolved backend."""
    model_uri = f"runs:/{run_id}/{artifact_path}"
    # MLflow 3's nested logged-model repository still constructs a client from
    # global state, even when download_artifacts receives tracking_uri. Share
    # the fluent lock with logging so this lookup cannot redirect an active run.
    with mlflow_fluent_operation():
        set_tracking_uri_preserving_env(mlflow_module, backend.tracking_uri)
        mlflow_module.set_registry_uri(backend.registry_uri)
        local_path = mlflow_module.artifacts.download_artifacts(
            model_uri,
            tracking_uri=backend.tracking_uri,
        )
    return mlflow_module.pyfunc.load_model(local_path)


def _wrap_pyfunc(model: Any) -> ScoringModel:
    """Wrap an MLflow pyfunc model in a ``ScoringModel``."""
    feature_names = _extract_pyfunc_features(model)
    return ScoringModel(
        model=model,
        feature_names=feature_names,
        cat_feature_names=frozenset(),
        flavor="pyfunc",
    )


def _extract_pyfunc_features(model: Any) -> list[str]:
    """Extract feature names from a pyfunc model's signature."""
    sig = getattr(model.metadata, "signature", None)
    if sig is None:
        return []
    inputs = sig.inputs
    if inputs is None:
        return []
    return list(inputs.input_names())


# ---------------------------------------------------------------------------
# Artifact discovery
# ---------------------------------------------------------------------------


def _find_artifact_by_extension(
    client: MlflowClient,
    run_id: str,
    ext: str,
    label: str,
) -> str:
    """Find the first artifact with the given extension in a run.

    Searches the top-level artifact list first, then one level of
    subdirectories.

    Args:
        client: MLflow tracking client.
        run_id: MLflow run ID to search.
        ext: File extension including the dot (e.g. ``".cbm"``).
        label: Human-readable label for error messages (e.g. ``"CatBoost"``).

    Raises:
        _ArtifactNotFoundError: If no artifact with *ext* is found.  Subclass
            of :class:`FileNotFoundError` so the public contract
            (callers expect ``FileNotFoundError``) is preserved while
            the internal triage at :func:`_find_model_artifact` can
            distinguish "probe missed" from a credential / network error
            that would be a bare ``FileNotFoundError`` from MLflow.
    """
    artifacts = client.list_artifacts(run_id)
    for art in artifacts:
        if art.path.endswith(ext):
            return str(art.path)
    # Check one level deep (artifacts may be in subdirectories)
    for art in artifacts:
        if art.is_dir:
            sub_artifacts = client.list_artifacts(run_id, art.path)
            for sub in sub_artifacts:
                if sub.path.endswith(ext):
                    return str(sub.path)
    raise _ArtifactNotFoundError(
        f"No {ext} artifact found in run '{run_id}'. "
        f"Ensure the {label} model was logged with mlflow.log_artifact()."
    )


def _find_cbm_artifact(client: MlflowClient, run_id: str) -> str:
    """Find the first ``.cbm`` artifact in a run's artifact list."""
    return _find_artifact_by_extension(client, run_id, ".cbm", "CatBoost")


def _find_rsglm_artifact(client: MlflowClient, run_id: str) -> str:
    """Find the first ``.rsglm`` artifact in a run's artifact list."""
    return _find_artifact_by_extension(client, run_id, ".rsglm", "RustyStats")


def _find_model_artifact(client: MlflowClient, run_id: str) -> tuple[str, str]:
    """Find the model artifact in a run, returning ``(path, flavor)``.

    Checks for CatBoost (``.cbm``) first, then RustyStats (``.rsglm``),
    then falls back to a pyfunc model directory.

    Only catches :class:`_ArtifactNotFoundError` — a dedicated subclass of
    :class:`FileNotFoundError` raised by our own helpers when a probe
    genuinely sees no matching artifact.  A bare ``FileNotFoundError``
    or an :class:`mlflow.exceptions.MlflowException` (credential /
    network failure from ``list_artifacts``) propagates so the operator
    sees the real infrastructure problem instead of a misleading "no
    model artifact" message.
    """
    try:
        return _find_cbm_artifact(client, run_id), "catboost"
    except _ArtifactNotFoundError:
        pass

    try:
        return _find_rsglm_artifact(client, run_id), "rustystats"
    except _ArtifactNotFoundError:
        pass

    for suffix, wrapper_flavor in NATIVE_WRAPPER_SUFFIXES.items():
        try:
            return (
                _find_artifact_by_extension(client, run_id, suffix, wrapper_flavor),
                wrapper_flavor,
            )
        except _ArtifactNotFoundError:
            pass

    # Look for a pyfunc model directory (contains MLmodel file).
    # ``list_artifacts`` failures (MlflowException etc.) propagate so
    # that an auth / network error is never masqueraded as "no model
    # artifact found".
    artifacts = client.list_artifacts(run_id)
    for art in artifacts:
        if art.path == "model" and art.is_dir:
            return "model", "pyfunc"
    # Check one level deep
    for art in artifacts:
        if art.is_dir:
            sub = client.list_artifacts(run_id, art.path)
            for s in sub:
                if s.path.endswith("/MLmodel") or s.path == "MLmodel":
                    return art.path, "pyfunc"

    raise _ArtifactNotFoundError(
        f"No model artifact found in run '{run_id}'. "
        "Expected .cbm (CatBoost), .rsglm (RustyStats), or model directory (pyfunc)."
    )


def _delete_tombstone(remove_tree: Callable[[Path], bool], tombstone: Path) -> None:
    """Delete one tombstone, reporting a tree that survives.

    Eviction is housekeeping and must never raise into a caller that only asked
    for an artifact, but a tombstone that silently stays behind holds disk for
    good: Windows fails a delete while an indexer or scanner briefly holds a
    handle, so the shared retrying removal is used and a survivor is logged.
    """
    if not remove_tree(tombstone):
        logger.warning("mlflow_disk_cache_tombstone_delete_failed", tombstone=str(tombstone))


def _evict_disk_cache(cache_root: Path) -> None:
    """Remove oldest run directories when disk cache exceeds the limit.

    Keeps at most ``_DISK_CACHE_MAX_DIRS`` run directories across *every*
    backend-digest partition under *cache_root* (a run cached on two
    backends is two directories), deleting the ones with the oldest
    modification time.
    """
    from haute._file_ops import remove_tree

    if not cache_root.is_dir():
        return

    cache_dirs = [
        run_dir
        for backend_dir in cache_root.iterdir()
        if backend_dir.is_dir()
        for run_dir in backend_dir.iterdir()
        if run_dir.is_dir()
    ]
    tombstones = [d for d in cache_dirs if d.name.startswith(_DISK_CACHE_EVICTION_PREFIX)]
    for tombstone in tombstones:
        _delete_tombstone(remove_tree, tombstone)

    active_runs = _active_disk_cache_runs()
    run_dirs = [
        d
        for d in cache_dirs
        if not d.name.startswith(_DISK_CACHE_EVICTION_PREFIX) and d.name not in active_runs
    ]
    if len(run_dirs) <= _DISK_CACHE_MAX_DIRS:
        return

    # Sort by modification time, oldest first
    run_dirs.sort(key=lambda d: d.stat().st_mtime)
    to_remove = len(run_dirs) - _DISK_CACHE_MAX_DIRS
    for d in run_dirs[:to_remove]:
        with _disk_cache_active_runs_guard:
            if d.name in _disk_cache_active_runs:
                continue
            # Short on purpose: a tombstone name that grew on the run id it
            # replaced pushed the cached artifact underneath it past Windows'
            # 260-character path limit, and a path that long cannot be opened —
            # so the delete below failed and the tombstone held disk for good.
            # 8 hex digits are shorter than any real run id and unique enough;
            # the log line below records which run the tombstone was.
            tombstone = d.with_name(f"{_DISK_CACHE_EVICTION_PREFIX}{uuid.uuid4().hex[:8]}")
            try:
                d.replace(tombstone)
            except FileExistsError:
                # Another pass owns that name; this run is evicted on the next.
                continue
            except FileNotFoundError:
                continue
        # The active check and same-filesystem rename are atomic with respect
        # to run users. Slow recursive deletion happens after releasing the
        # global guard, while new users cleanly miss the original run path.
        logger.info(
            "mlflow_disk_cache_evict",
            path=str(d),
            tombstone=str(tombstone),
        )
        _delete_tombstone(remove_tree, tombstone)


def _resolve_artifact_local(
    mlflow: Any,
    backend: ResolvedBackend,
    run_id: str,
    artifact_path: str,
) -> str:
    """Return a local path to the model artifact, downloading only if needed.

    Saves downloaded artifacts under
    ``.cache/models/<backend digest>/<run_id>/`` so they survive server
    restarts without re-downloading from remote tracking servers (saves
    ~30 s+ for Databricks-hosted artifacts) while never serving one
    backend's bytes for another's run.

    Downloads to a temp file first then renames atomically, so a partial
    download (network interruption, timeout) never leaves a corrupt file
    in the cache.

    Same-artifact callers are serialized on the per-artifact I/O lock:
    exactly one thread downloads while the others wait, then find the
    file via the in-lock re-check — no thundering herd, and the move can
    never land on a file another thread is concurrently writing.
    """
    with _disk_cache_run_in_use(run_id):
        return _resolve_artifact_local_in_use(mlflow, backend, run_id, artifact_path)


def _resolve_artifact_local_in_use(
    mlflow: Any,
    backend: ResolvedBackend,
    run_id: str,
    artifact_path: str,
) -> str:
    """Implementation for ``_resolve_artifact_local`` while eviction is guarded."""
    import shutil
    import tempfile

    cache_root = _disk_cache_root()
    local_path = _artifact_cache_path(cache_root, backend.digest, run_id, artifact_path)
    cache_dir = local_path.parent

    if local_path.is_file():
        logger.info(
            "mlflow_artifact_disk_cache_hit",
            path=str(local_path),
        )
        return str(local_path)

    with _artifact_io_lock(backend.digest, run_id, artifact_path):
        # Re-check under the lock: a concurrent caller may have completed
        # the download while this thread was waiting to acquire.
        if local_path.is_file():
            logger.info(
                "mlflow_artifact_disk_cache_hit",
                path=str(local_path),
            )
            return str(local_path)

        # Cache miss — download to a temp directory first, then move into
        # place atomically.  If the download is interrupted the temp dir
        # is cleaned up and no corrupt file is left in the cache.
        logger.info(
            "mlflow_artifact_downloading",
            run_id=run_id,
            artifact=artifact_path,
            backend_mode=backend.mode,
            backend_digest=backend.digest,
        )
        tmp_dir = None
        try:
            tmp_dir = Path(tempfile.mkdtemp(prefix="haute_dl_"))
            downloaded = mlflow.artifacts.download_artifacts(
                f"runs:/{run_id}/{artifact_path}",
                dst_path=str(tmp_dir),
                tracking_uri=backend.tracking_uri,
            )
            downloaded_path = Path(downloaded)
            if not downloaded_path.is_file():
                # download_artifacts may nest; look for the expected filename
                downloaded_path = tmp_dir / Path(artifact_path).name
            if not downloaded_path.is_file():
                raise FileNotFoundError(
                    f"Download completed but artifact not found at {downloaded_path}"
                )

            # Move into cache atomically
            cache_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(downloaded_path), str(local_path))
            logger.info(
                "mlflow_artifact_cached",
                path=str(local_path),
                size_mb=round(local_path.stat().st_size / 1024**2, 1),
            )
        except Exception:
            # Clean up partial cache entry if it was created
            if local_path.is_file():
                local_path.unlink()
            raise
        finally:
            if tmp_dir and tmp_dir.exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)

        # Evict oldest run directories if disk cache exceeds the limit
        _evict_disk_cache(cache_root)

    return str(local_path)


def clear_model_cache(run_id: str | None = None) -> int:
    """Delete cached model artifacts, returning the number of files removed.

    If *run_id* is given, only that run's cache is cleared — in-memory
    entries whose cache key's ``run_id`` slot matches are evicted (on every
    backend), that run's on-disk directory is removed under every
    backend-digest partition, and observability counters are left untouched
    (a targeted clear is not a measurement-window boundary).

    If *run_id* is ``None``, everything goes: all in-memory entries, the
    entire ``.cache/models`` tree, AND the observability counters
    (``hits`` / ``misses``).  The counter reset on blanket clear is
    intentional so the next measurement window is not contaminated with
    pre-clear counts.
    """
    import shutil

    # Validate run_id up front — do this before any other work so a bad
    # input fails loudly regardless of whether the disk cache exists.
    if run_id:
        _validate_disk_cache_run_id(run_id)

    cache_root = _disk_cache_root()
    removed = 0
    if cache_root.exists():
        if run_id:
            # Run directories live under a backend-digest partition, so a
            # targeted clear walks ``cache_root/*/<run_id>``.
            for backend_dir in cache_root.iterdir():
                if not backend_dir.is_dir():
                    continue
                target = backend_dir / run_id
                if target.exists():
                    removed += sum(1 for _ in target.rglob("*") if _.is_file())
                    shutil.rmtree(target, ignore_errors=True)
        else:
            for d in cache_root.iterdir():
                if d.is_dir():
                    removed += sum(1 for _ in d.rglob("*") if _.is_file())
            shutil.rmtree(cache_root, ignore_errors=True)

    # In-memory + counter cleanup is scoped to the caller's intent:
    # blanket ``clear_model_cache()`` zeroes everything; targeted
    # ``clear_model_cache(run_id="x")`` only evicts entries whose cache
    # key matches the run_id and leaves counters alone.
    if run_id is None:
        _model_cache.clear()
        _reset_model_cache_stats()
    else:
        # Cache keys are ``(source_type, resolved_run_id, [version,]
        # artifact, task, artifact_fingerprint, backend_identity)`` —
        # run_id is always slot 1, so the match is backend-agnostic.
        # Evict every entry whose run_id slot matches (may be multiple,
        # different task / version per run) and let the cascade handle
        # feature-validation-cache invalidation.  Counters are left
        # alone — targeted clear is not a measurement-window boundary.
        _model_cache.evict_matching(lambda k: len(k) >= 2 and k[1] == run_id)
    return removed


# ---------------------------------------------------------------------------
# Main loader
# ---------------------------------------------------------------------------


# Maximum total attempts for loading an MLflow-backed model.  One fresh
# attempt plus one retry after deleting a suspected-corrupt cache.  Higher
# ceilings turn persistent corruption into an invisible loop; lower is
# friendly to truly transient read errors without masking real problems.
_LOAD_MAX_ATTEMPTS = 2
# Initial backoff before the retry (seconds).  Exponential growth applied
# if _LOAD_MAX_ATTEMPTS is ever raised.
_LOAD_BACKOFF_BASE_S = 0.1
_LOAD_BACKOFF_JITTER_S = 0.1


def _load_with_bounded_retry(
    *,
    mlflow_mod: Any,
    backend: ResolvedBackend,
    run_id: str,
    artifact: str,
    flavor: str,
    task: str,
) -> ScoringModel:
    """Resolve artifact and load the model with a bounded retry window.

    On failure deletes the suspect cached file, sleeps for a brief
    exponential-backoff interval with jitter, re-downloads, and retries.
    After :data:`_LOAD_MAX_ATTEMPTS` total attempts, propagates the final
    failure with a diagnostic message noting that the retry was exhausted —
    an operator can then act on truly corrupt artifacts instead of watching
    a silent loop re-download the same bad bytes.
    """
    import random
    import time

    from haute._sandbox import ArtifactVersionMismatchError
    from haute.errors import ConfigError

    last_err: BaseException | None = None
    for attempt in range(1, _LOAD_MAX_ATTEMPTS + 1):
        local_path: str | None = None
        try:
            local_path = _resolve_artifact_local(mlflow_mod, backend, run_id, artifact)
            contract_path: str | None = None
            if flavor == "ebm":
                contract_path = _resolve_run_contract(mlflow_mod, backend, run_id, artifact)
            if flavor == "catboost":
                raw = _load_catboost_model(local_path, task)
                return _wrap_catboost(raw, source=f"run {run_id!r}, artifact {artifact!r}")
            if flavor in NATIVE_WRAPPER_FLAVORS:
                return _load_wrapper_model(
                    local_path, task, cast(ModelFlavor, flavor), contract_path=contract_path
                )
            return _load_rustystats_model(local_path, source=f"MLflow run {run_id!r}")
        except (AttributeError, TypeError, KeyError, ConfigError, ArtifactVersionMismatchError):
            # Programmer error — a missing attribute, wrong type, or
            # unknown dict key is a bug in our dispatch code (or a
            # breaking change in catboost / rustystats), not a corrupt
            # artifact; a ConfigError (the node scores the model as the
            # wrong task, or an EBM under a contract that does not describe
            # it) and an ArtifactVersionMismatchError (a model written by
            # another engine version) are readable models that
            # re-downloading cannot fix.  Wrapping these as "persistently corrupt" would send
            # on-call down the wrong path.  Re-raise so the real error
            # surfaces.
            raise
        except Exception as err:
            last_err = err
            logger.warning(
                "model_load_failed",
                attempt=attempt,
                max_attempts=_LOAD_MAX_ATTEMPTS,
                path=local_path,
                error=str(err),
            )
            # Delete suspected-corrupt cache so the next attempt
            # re-downloads from scratch.
            cached_file = Path(local_path) if local_path is not None else None
            if cached_file is not None and cached_file.is_file():
                cached_file.unlink()
            if attempt >= _LOAD_MAX_ATTEMPTS:
                break
            # Exponential backoff with jitter between attempts.
            delay = _LOAD_BACKOFF_BASE_S * (2 ** (attempt - 1)) + random.uniform(
                0, _LOAD_BACKOFF_JITTER_S
            )
            time.sleep(delay)

    # Exhausted the retry budget — surface a diagnostic that names the
    # artifact so on-call engineers don't need to spelunk debug logs.
    assert last_err is not None  # loop always sets last_err on failure
    raise RuntimeError(
        f"Persistently corrupt or unloadable model artifact "
        f"(run_id={run_id!r}, artifact={artifact!r}, flavor={flavor}): "
        f"retry exhausted after {_LOAD_MAX_ATTEMPTS} attempt(s). "
        f"Last error: {last_err}"
    ) from last_err


def load_mlflow_model(
    *,
    source_type: str,
    run_id: str = "",
    artifact_path: str = "",
    registered_model: str = "",
    version: str = "",
    task: str = "regression",
    destination: str = "",
    alias: str = "",
) -> ScoringModel:
    """Load a model from MLflow, auto-detecting CatBoost vs pyfunc.

    CatBoost models (``.cbm`` artifacts) get the optimized native loader
    with categorical feature support.  All other models are loaded via
    MLflow's pyfunc flavor.

    Cached by ``(source_type, identifier, version/artifact, task,
    artifact_fingerprint, backend_identity)`` — the fingerprint is the byte
    identity of the local model artifact, so re-logging a run or retraining
    a ``version="latest"`` model in place invalidates the in-process entry,
    and the backend identity means the same run on another destination is a
    different entry rather than a stale hit.

    Args:
        source_type: ``"run"`` to load from a specific run, or ``"registered"``
            to load from a registered model version.
        run_id: MLflow run ID (required when *source_type* is ``"run"``).
        artifact_path: Artifact path within the run (e.g. ``"model.cbm"``).
            If empty, auto-discovers: tries ``.cbm`` first, then pyfunc ``model/``.
        registered_model: Registered model name (required when *source_type* is
            ``"registered"``).
        version: Model version string (``"1"``, ``"2"``, or ``"latest"``).
        alias: Registered model alias; resolved to its current version on every
            load, so the cache follows the alias when it moves.
        task: ``"regression"`` or ``"classification"`` — determines which
            CatBoost class to use for loading (ignored for pyfunc).
        destination: Destination key (``"databricks"``, ``"server"``,
            ``"local"``) or ``""`` for the local folder.

    Returns:
        A ``ScoringModel`` wrapping the loaded model with a uniform interface.

    Raises:
        MlflowConfigError: If *destination* cannot be resolved — raised
            before any cache lookup, so a warm entry is never served for a
            destination whose prerequisites are gone.
    """
    from haute._mlflow_utils import resolve_backend

    valid_tasks = ("regression", "classification")
    if task not in valid_tasks:
        raise ValueError(f"Invalid task {task!r}. Expected one of: {', '.join(valid_tasks)}")
    if artifact_path:
        # This is a POSIX-style identifier within an MLflow run, not a local
        # project path. Validate it before any cache lookup or network access.
        _validate_artifact_path(artifact_path)

    # Resolve the destination exactly once, before any cache lookup: every
    # cache path, key, and lock below is keyed on this backend, and the full
    # path reuses the same object so a settings save mid-load cannot split
    # one load across two backends.
    backend = resolve_backend(destination)

    # Fast-path cache check using the raw inputs — avoids calling
    # resolve_mlflow_source() (which hits the MLflow tracking server)
    # on every invocation when the model is already cached.
    # For source_type="run" with a known artifact_path, the cache key
    # components are fully determined without any network call.  The
    # artifact-fingerprint component is derived from the disk-cached file,
    # so the in-process entry can never outlive the bytes it was loaded
    # from; a fingerprint change (re-log / retrain-in-place) is a miss.
    if source_type == "run" and run_id and artifact_path:
        flavor = _flavor_from_artifact(artifact_path)
        if flavor == "pyfunc":
            # No local artifact file exists to fingerprint — pyfunc loads
            # by MLflow URI.  Keyed without byte identity (documented
            # residual in _model_cache_key).
            fast_key = _model_cache_key(
                source_type=source_type,
                run_id=run_id,
                version=version,
                artifact_path=artifact_path,
                task=task,
                artifact_fingerprint="",
                backend_identity=backend.identity,
            )
            cached = _model_cache.get(fast_key)
            if cached is not None:
                _record_cache_hit(
                    run_id=run_id,
                    artifact_path=artifact_path,
                    flavor=flavor,
                )
                return cached
        else:
            with _disk_cache_run_in_use(run_id):
                local_path = _artifact_cache_path(
                    _disk_cache_root(),
                    backend.digest,
                    run_id,
                    artifact_path,
                )
                # An EBM is only as current as the contract it loads under, so
                # its fast path needs the cached contract too.
                contract_local: Path | None = None
                if flavor == "ebm":
                    contract_artifact = _run_contract_artifact(artifact_path)
                    contract_local = _artifact_cache_path(
                        _disk_cache_root(), backend.digest, run_id, contract_artifact
                    )
                if local_path.is_file() and (contract_local is None or contract_local.is_file()):
                    fast_key = _model_cache_key(
                        source_type=source_type,
                        run_id=run_id,
                        version=version,
                        artifact_path=artifact_path,
                        task=task,
                        artifact_fingerprint=(
                            _local_artifact_fingerprint(artifact_path, str(local_path))
                            if contract_local is None
                            else _ebm_identity_fingerprint(
                                artifact_path,
                                str(local_path),
                                contract_artifact,
                                str(contract_local),
                            )
                        ),
                        backend_identity=backend.identity,
                    )
                    cached = _model_cache.get(fast_key)
                    if cached is not None:
                        _record_cache_hit(
                            run_id=run_id,
                            artifact_path=artifact_path,
                            flavor=flavor,
                        )
                        return cached
                    with _artifact_io_lock(backend.digest, run_id, artifact_path):
                        # Single-flight: a concurrent caller may have loaded
                        # this exact model while we waited for the lock.
                        cached = _model_cache.get(fast_key)
                        if cached is not None:
                            _record_cache_hit(
                                run_id=run_id,
                                artifact_path=artifact_path,
                                flavor=flavor,
                            )
                            return cached
                        if local_path.is_file():
                            _record_cache_miss(
                                run_id=run_id,
                                artifact_path=artifact_path,
                                flavor=flavor,
                            )
                            source = f"MLflow run {run_id!r}"
                            scoring_model = (
                                load_local_model(str(local_path), task=task, source=source)
                                if contract_local is None
                                else load_local_model(
                                    str(local_path),
                                    task=task,
                                    contract_path=str(contract_local),
                                    source=source,
                                )
                            )
                            _model_cache.put(fast_key, scoring_model)
                            logger.info(
                                "mlflow_model_loaded_from_disk_cache",
                                source_type=source_type,
                                run_id=run_id,
                                artifact=artifact_path,
                                task=task,
                                flavor=flavor,
                                path=str(local_path),
                                backend_mode=backend.mode,
                                backend_digest=backend.digest,
                            )
                            return scoring_model
                    # The file vanished while this thread waited for the lock
                    # (e.g. a concurrent corrupt-retry deleted it).  Fall
                    # through to the full resolve + re-download path below.

    # The already-resolved backend is threaded in, so this load resolves the
    # destination exactly once no matter which path it takes.
    resolved_run_id, resolved_version, mlflow_mod, client, _resolved = resolve_mlflow_source(
        source_type=source_type,
        run_id=run_id,
        registered_model=registered_model,
        version=version,
        backend=backend,
        alias=alias,
    )
    resolved_artifact = artifact_path

    # Auto-discover artifact if not specified
    if not resolved_artifact:
        resolved_artifact, _flavor = _find_model_artifact(client, resolved_run_id)
    # else: detect from the artifact path extension

    # Detect flavor from artifact path
    flavor = _flavor_from_artifact(resolved_artifact)

    # Resolve the local artifact up front so its byte identity can be part
    # of the cache key: a "latest" retrain or re-logged run must miss, not
    # serve the previously loaded model.  For a cached artifact this is a
    # stat-gated memo lookup; only a genuinely new artifact downloads here.
    # Pyfunc models load by MLflow URI with no local file to fingerprint —
    # keyed without byte identity (documented residual in _model_cache_key).
    local_artifact_path: str | None = None
    artifact_fp = ""
    if flavor in ("catboost", "rustystats") or flavor in NATIVE_WRAPPER_FLAVORS:
        with _disk_cache_run_in_use(resolved_run_id):
            local_artifact_path = _resolve_artifact_local(
                mlflow_mod,
                backend,
                resolved_run_id,
                resolved_artifact,
            )
            artifact_fp = _loaded_artifact_fingerprint(
                flavor, mlflow_mod, backend, resolved_run_id, resolved_artifact, local_artifact_path
            )

    cache_key = _model_cache_key(
        source_type=source_type,
        run_id=resolved_run_id,
        version=resolved_version,
        artifact_path=resolved_artifact,
        task=task,
        artifact_fingerprint=artifact_fp,
        backend_identity=backend.identity,
    )

    cached = _model_cache.get(cache_key)
    if cached is not None:
        _record_cache_hit(
            run_id=resolved_run_id,
            artifact_path=resolved_artifact,
            flavor=flavor,
        )
        return cached

    # Single-flight the download + load per on-disk artifact: one thread
    # does the work while same-artifact callers wait, then reuse the
    # in-memory entry via the re-check below.  This also makes the
    # corrupt-retry's delete + re-download mutually exclusive with any
    # concurrent load of the same cached file.
    with _artifact_io_lock(backend.digest, resolved_run_id, resolved_artifact):
        cached = _model_cache.get(cache_key)
        if cached is not None:
            _record_cache_hit(
                run_id=resolved_run_id,
                artifact_path=resolved_artifact,
                flavor=flavor,
            )
            return cached

        # Real-path miss — record the miss (counter + structured event)
        # before the potentially-expensive download so the miss is
        # observable even if the subsequent load raises.
        _record_cache_miss(
            run_id=resolved_run_id,
            artifact_path=resolved_artifact,
            flavor=flavor,
        )

        # Load model based on detected flavor.
        # If loading fails (corrupt/truncated cache), delete the cached file
        # and re-download exactly once before giving up.  The retry uses a
        # small exponential backoff with jitter so transient upstream hiccups
        # (tracking-server flaps) get a moment to recover — but the total
        # retry budget is bounded so persistent corruption surfaces loudly.
        if flavor in ("catboost", "rustystats") or flavor in NATIVE_WRAPPER_FLAVORS:
            with _disk_cache_run_in_use(resolved_run_id):
                scoring_model = _load_with_bounded_retry(
                    mlflow_mod=mlflow_mod,
                    backend=backend,
                    run_id=resolved_run_id,
                    artifact=resolved_artifact,
                    flavor=flavor,
                    task=task,
                )
                # The bounded retry may have deleted and re-downloaded the
                # artifact; re-derive the fingerprint so the entry is keyed
                # by the bytes that were actually loaded (stat-gated memo —
                # a no-op stat when nothing changed).
                assert local_artifact_path is not None
                cache_key = _model_cache_key(
                    source_type=source_type,
                    run_id=resolved_run_id,
                    version=resolved_version,
                    artifact_path=resolved_artifact,
                    task=task,
                    artifact_fingerprint=_loaded_artifact_fingerprint(
                        flavor,
                        mlflow_mod,
                        backend,
                        resolved_run_id,
                        resolved_artifact,
                        local_artifact_path,
                    ),
                    backend_identity=backend.identity,
                )
        else:
            raw_model = _load_pyfunc_model(
                mlflow_mod,
                resolved_run_id,
                resolved_artifact,
                backend=backend,
            )
            scoring_model = _wrap_pyfunc(raw_model)

        _model_cache.put(cache_key, scoring_model)

    logger.info(
        "mlflow_model_loaded",
        source_type=source_type,
        run_id=resolved_run_id,
        artifact=resolved_artifact,
        task=task,
        flavor=flavor,
        backend_mode=backend.mode,
        backend_digest=backend.digest,
    )
    return scoring_model


# ---------------------------------------------------------------------------
# Shared scoring helpers (used by executor + deploy scorer)
# ---------------------------------------------------------------------------


def _prepare_predict_frame(
    df_eager: pl.DataFrame,
    features: list[str],
    cat_feature_names: frozenset[str] = frozenset(),
    flavor: ModelFlavor = "pyfunc",
) -> Any:
    """Prepare a Polars DataFrame for model prediction.

    Dispatch per flavor:

    - ``rustystats``: Polars DataFrame, untouched — the GLM owns its own
      preprocessing (nulls, categoricals, casts).
    - ``pyfunc``: **named pandas DataFrame with native dtypes**.  MLflow
      pyfunc models carry signatures; named-column signatures (the
      standard ``infer_signature`` case) hard-reject unnamed numpy input
      (``MlflowException: Model is missing inputs [...]``), and mlflow's
      own schema enforcement converts dtypes to the declared types —
      handing it a preemptive Float32 downcast would be silently upcast
      back to ``double`` with the mantissa bits already gone.  Float
      nulls surface as NaN; integer columns containing nulls widen to
      float64 NaN via the Arrow conversion.
    - ``catboost``: numerics cast to Float32 (CatBoost's internal compute
      dtype; null→NaN); declared categoricals filled with the
      ``_MISSING_`` sentinel and carried as ``pd.Categorical`` through
      pandas.  Without categoricals, the numpy fast path applies — the
      ONLY numpy branch (it avoids the Arrow-to-pandas round-trip that
      keeps the buffer alive twice).

    Unknown flavors raise ``ValueError`` — silently routing them through
    the catboost-shaped branch would score with the wrong input contract.
    """
    # RustyStats and the XGBoost wrapper encode their own inputs (the
    # wrapper from its stored category levels) — pass Polars directly.
    if flavor == "rustystats" or flavor in NATIVE_WRAPPER_FLAVORS:
        return df_eager.select(features) if features else df_eager

    # ``catboost`` and ``pyfunc`` share the tabular (pandas/numpy) prep below;
    # ``rustystats`` is handled above.  These are the SSOT flavors *minus*
    # rustystats — anything else (including a flavor newly added to
    # ``ModelFlavor`` but not yet taught a prep path here) fails loudly rather
    # than being scored through the wrong input contract.  The error message
    # enumerates the domain straight from ``_SUPPORTED_FLAVORS`` so it can
    # never drift from the SSOT, and
    # ``tests/test_mlflow_io.py::TestFlavorSsot`` pins that this function
    # recognises exactly the SSOT flavors.
    if flavor not in ("catboost", "pyfunc"):
        raise ValueError(
            f"Unknown model flavor {flavor!r} for predict-frame preparation. "
            f"Expected one of: {sorted(_SUPPORTED_FLAVORS)}."
        )

    cat_cols = [c for c in features if c in cat_feature_names]
    selected = df_eager.select(features)
    if cat_cols:
        selected = selected.with_columns(
            [pl.col(c).fill_null("_MISSING_").cast(pl.Categorical) for c in cat_cols]
        )

    if flavor == "pyfunc":
        # Named DataFrame per the model signature; mlflow's enforcement
        # sees exactly the dtypes the pipeline produced. MLflow's scalar
        # ``datetime`` type is timezone-agnostic and rejects pandas'
        # timezone-aware dtype, so preserve the instant canonically as UTC
        # before removing the zone at this boundary.
        pandas_frame = selected.to_pandas()
        for column in pandas_frame.select_dtypes(include=["datetimetz"]).columns:
            pandas_frame[column] = pandas_frame[column].dt.tz_convert("UTC").dt.tz_localize(None)
        return pandas_frame

    numeric_cols = [c for c in features if c not in cat_feature_names]
    if numeric_cols:
        selected = selected.with_columns([pl.col(c).cast(pl.Float32) for c in numeric_cols])
    # Categorical dtype only round-trips through pandas; the numeric-only
    # CatBoost path skips the pandas wrapper entirely.
    if cat_cols:
        return selected.to_pandas()
    return selected.to_numpy()


def _positive_class_proba_vector(probas: Any, output_col: str) -> np.ndarray:
    """Reduce raw ``predict_proba`` output to the binary positive-class vector.

    The single shape dispatch shared by the batch path
    (:func:`_append_classification_proba`) and the eager path
    (``_model_scorer._predict_positive_proba``) so the two surfaces cannot
    drift — the ``<output_col>_proba`` column carries the **binary
    positive-class probability** on both:

    - 1-D output: used as-is (already the positive-class vector);
    - ``(n, 1)``: column 0 (wrappers that emit only the positive column);
    - ``(n, 2)``: column 1 (the sklearn/CatBoost binary convention).

    Multiclass output (``(n, k>=3)``) raises: a single probability column
    cannot represent k classes, and the pre-fix behaviour — emitting
    ``probas[:, 1]``, the probability of whichever class sits at index 1,
    silently labeled as the binary positive-class probability — is a
    wrong-but-plausible number feeding prices downstream.  Fail loud.
    ``(n, 0)`` and ``ndim != 1/2`` output likewise raise.
    """
    arr = np.asarray(probas)
    if arr.ndim == 2:
        n_classes = arr.shape[1]
        if n_classes == 1:
            arr = arr[:, 0]
        elif n_classes == 2:
            arr = arr[:, 1]
        else:
            raise ValueError(
                f"predict_proba returned probabilities for {n_classes} classes, "
                f"but the single '{output_col}_proba' column is defined only for "
                f"binary classifiers (it carries the positive-class probability). "
                f"Emitting one class's column here would silently mislabel a "
                f"multiclass probability as binary. Score a binary model, or "
                f"expose per-class probabilities through a dedicated node."
            )
    elif arr.ndim != 1:
        raise ValueError(
            f"Unsupported predict_proba output shape {arr.shape}: expected a "
            f"1-D probability vector or a 2-D (n_rows, n_classes) matrix."
        )
    return arr.flatten()


def _append_classification_proba(
    df: pl.DataFrame,
    scoring_model: ScoringModel,
    x_data: Any,
    output_col: str,
) -> pl.DataFrame:
    """Append a ``<output_col>_proba`` column for classification tasks.

    Shape semantics — including the loud multiclass rejection — are owned
    by :func:`_positive_class_proba_vector`, the dispatch shared with the
    eager path.  If the model does not support ``predict_proba`` the
    DataFrame is returned unchanged.
    """
    probas = scoring_model.predict_proba(x_data)
    if probas is None:
        return df
    return df.with_columns(
        pl.Series(f"{output_col}_proba", _positive_class_proba_vector(probas, output_col)),
    )


def _score_eager(
    scoring_model: ScoringModel,
    lf: pl.LazyFrame,
    features: list[str],
    output_col: str = "prediction",
    task: str = "regression",
    write_projection: ScoreWriteProjection | None = None,
    offset_column: str | None = None,
) -> pl.LazyFrame:
    """Collect a LazyFrame and score in-memory. Returns a LazyFrame.

    Thin delegate onto :func:`haute._model_scorer.score_frame` with
    ``batch=False`` — the unified scoring entry point owns the flavor
    dispatch and the batch/eager fork.

    ``offset_column`` is threaded so the model's fit-time offset (contract
    or self-described) is re-applied at score time; ``None`` lets the
    scorer derive it from the model itself.
    """
    from haute._model_scorer import _declared_offset_column, score_frame

    return score_frame(
        model=scoring_model.raw_model,
        lf=lf,
        features=features,
        cat_feature_names=scoring_model.cat_feature_names,
        flavor=scoring_model.flavor,
        task=task,
        output_col=output_col,
        batch=False,
        write_projection=write_projection,
        offset_column=offset_column
        if offset_column is not None
        else _declared_offset_column(scoring_model),
    )
