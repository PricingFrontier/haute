"""MLflow model loading utilities for the MODEL_SCORE node.

Thread-safe LRU cache for models loaded from MLflow.
Supports CatBoost (native ``.cbm``) and any MLflow pyfunc model.

Every load resolves its destination to a :class:`~haute._mlflow_utils.ResolvedBackend`
exactly once, before any cache lookup, and threads that object through
download, load, and lock selection. The backend is part of every cache
identity, so the same run ID and artifact path on two destinations — or on
two endpoints of one category — never alias:

- disk cache: ``.cache/models/<backend digest>/<run_id>/<16 hex of the sha256 of
  artifact_path>/<identity token>/artifact<suffix>``, one copy per identity the
  store gave the file's bytes, beside the ``resolved.json`` record of the copy
  the last resolved load used;
- in-memory cache key: built from the :class:`~haute._mlflow_model.ResolvedModel`,
  the backend's secret-free identity last;
- artifact I/O locks: keyed on ``(backend digest, run_id, artifact_path)``.

Which model a reference means is the model resolution's answer
(:mod:`haute._mlflow_model`); this module loads that answer and searches no run.

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
from contextvars import ContextVar
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any
from weakref import WeakValueDictionary

import numpy as np
import polars as pl

from haute._logging import get_logger
from haute._lru_cache import LRUCache
from haute._mlflow_model import (
    ResolvedModel,
    StoredArtifact,
    resolve_model,
    validate_artifact_path,
)
from haute._mlflow_utils import (
    ResolvedBackend,
    allow_file_store_if_local,
    mlflow_fluent_operation,
    set_tracking_uri_preserving_env,
)
from haute._model_flavors import (
    family_for_algorithm,
    family_for_artifact,
    is_registered_flavor,
    model_families,
    model_family,
    model_file_suffixes,
)
from haute._project_context import current_project
from haute._sandbox import contained_path
from haute._stat_gated_cache import StatGatedCache, artifact_cache_key, resolve_artifact_path
from haute.errors import PathOutsideProjectError

if TYPE_CHECKING:
    from catboost import CatBoostClassifier, CatBoostRegressor

    from haute._model_scorer import ScoreWriteProjection
    from haute._model_source import RegisteredModelSource, RunModelSource
    from haute.modelling._feature_contract import FeatureContract

logger = get_logger(component="mlflow_io")

_MODEL_CACHE_MAX_SIZE = 16
_DISK_CACHE_MAX_DIRS = 50
_DISK_CACHE_EVICTION_PREFIX = ".evicting-"
_RESOLUTION_RECORD = "resolved.json"


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
    return current_project().root / ".cache" / "models"


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
    token: str,
) -> Path:
    """Return the safe disk-cache path of one identity of a run artifact on one backend.

    ``<cache_root>/<backend digest>/<run_id>/<path digest>/<token>/artifact<suffix>``
    — the backend partition means two destinations never share a cached
    file even for identical run IDs and artifact paths, and the token
    (:attr:`StoredArtifact.token`) means two identities of one path are two
    files, so a file logged again never overwrites a copy another load reads.
    """
    from pathlib import PurePosixPath

    _validate_backend_digest(backend_digest)
    _validate_disk_cache_run_id(run_id)
    validate_artifact_path(artifact_path)
    if len(token) != 16 or any(c not in "0123456789abcdef" for c in token):
        raise ValueError(f"Invalid artifact identity token: {token!r}")
    suffix = PurePosixPath(artifact_path).suffix
    file_name = f"artifact{suffix}" if suffix else "artifact"
    try:
        return contained_path(
            cache_root,
            _artifact_cache_dir(backend_digest, run_id, artifact_path) / token / file_name,
        )
    except PathOutsideProjectError:
        raise ValueError(
            f"Invalid artifact cache identity: run_id={run_id!r}, artifact_path={artifact_path!r}"
        ) from None


def _artifact_cache_dir(backend_digest: str, run_id: str, artifact_path: str) -> Path:
    """The cache-root-relative folder every identity of one run artifact sits in.

    Named by 16 hex characters of the path's sha256, unique among one run's
    artifacts and short, because every character counts against Windows'
    260-character path limit beneath a project folder.
    """
    digest = sha256(artifact_path.encode("utf-8")).hexdigest()[:16]
    return Path(backend_digest) / run_id / digest


def _resolution_record_path(
    cache_root: Path, backend: ResolvedBackend, run_id: str, path: str
) -> Path:
    """Where the disk cache records the copy the last resolved load of *path* used."""
    _validate_backend_digest(backend.digest)
    _validate_disk_cache_run_id(run_id)
    validate_artifact_path(path)
    return contained_path(
        cache_root, _artifact_cache_dir(backend.digest, run_id, path) / _RESOLUTION_RECORD
    )


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


def _model_cache_key(resolved: ResolvedModel, task: str) -> tuple[str, ...]:
    """Build the in-process model cache key from the model resolution's answer.

    Every reference that resolves to one file and its contract shares the
    entry, and a changed identity of either (a file logged again, a contract
    logged later) is a different one. ``run_id`` stays in slot 1, which
    targeted ``clear_model_cache(run_id=...)`` eviction matches on, and the
    backend's secret-free identity is the last element, so the same run on two
    destinations never aliases.
    """
    contract = resolved.contract.identity if resolved.contract is not None else ""
    return (
        "mlflow",
        resolved.run_id,
        resolved.artifact.path,
        resolved.artifact.identity,
        contract,
        task,
        resolved.backend.identity,
    )


@dataclass(frozen=True, slots=True)
class DiskCachedRunModel:
    """The copies the last resolved load of one run artifact recorded in the disk cache.

    The model's copy, the copy of the contract its run logged beside it when it
    logged one, and the record naming both, in the disk model cache partition
    of one resolved backend. A load confined to the disk cache answers from it.
    """

    artifact_path: str
    model_path: Path
    contract_artifact: str | None
    contract_path: Path | None
    record_path: Path

    @property
    def files(self) -> tuple[Path, ...]:
        """The record, the model copy and the contract copy when there is one."""
        if self.contract_path is None:
            return (self.record_path, self.model_path)
        return (self.record_path, self.model_path, self.contract_path)

    @property
    def run_contract(self) -> str | None:
        """The cached contract the run logged, or ``None`` when it logged none."""
        return str(self.contract_path) if self.contract_path is not None else None

    def present(self) -> bool:
        """Whether every file the record names exists (a stat, never a read)."""
        return all(path.is_file() for path in self.files)

    def identity(self) -> str:
        """The byte identity of what a load reads: the record, the model and its contract."""
        from haute.deploy._scorer import artifact_identity_fingerprint

        paths = {"record": str(self.record_path), self.artifact_path: str(self.model_path)}
        if self.contract_artifact is not None and self.contract_path is not None:
            paths[self.contract_artifact] = str(self.contract_path)
        return artifact_identity_fingerprint(paths)


def _recorded_resolution(
    backend: ResolvedBackend, run_id: str, artifact_path: str
) -> tuple[ResolvedModel, DiskCachedRunModel] | None:
    """The answer the last resolved load of a run artifact recorded, from the disk cache.

    ``None`` when no load recorded one, which a pyfunc folder, loaded through
    MLflow and never copied, never does.
    """
    import json

    from haute._mlflow_model import StoredArtifact

    root = _disk_cache_root()
    record_path = _resolution_record_path(root, backend, run_id, artifact_path)
    if not record_path.is_file():
        return None
    record = json.loads(record_path.read_text(encoding="utf-8"))
    artifact = StoredArtifact(artifact_path, str(record["artifact"]))
    contract_record = record["contract"]
    contract = (
        None
        if contract_record is None
        else StoredArtifact(str(contract_record["path"]), str(contract_record["identity"]))
    )
    resolved = ResolvedModel(
        backend=backend,
        run_id=run_id,
        artifact=artifact,
        family=family_for_artifact(artifact_path),
        contract=contract,
        version="",
        reference=f"MLflow run {run_id!r}",
    )
    return resolved, DiskCachedRunModel(
        artifact_path,
        _artifact_cache_path(root, backend.digest, run_id, artifact_path, artifact.token),
        None if contract is None else contract.path,
        (
            None
            if contract is None
            else _artifact_cache_path(root, backend.digest, run_id, contract.path, contract.token)
        ),
        record_path,
    )


def disk_cached_run_model(
    *, run_id: str, artifact_path: str, destination: str
) -> DiskCachedRunModel | None:
    """The copies a disk-cache-only load of a run artifact reads, from configuration alone.

    Raises ``MlflowConfigError`` when the destination does not resolve and
    ``ValueError`` for an invalid run id or artifact path; ``None`` when no
    resolved load recorded a copy of it (a pyfunc folder never does).
    """
    from haute._mlflow_utils import resolve_backend

    validate_artifact_path(artifact_path)
    recorded = _recorded_resolution(resolve_backend(destination), run_id, artifact_path)
    return None if recorded is None else recorded[1]


_DISK_CACHE_ONLY: ContextVar[bool] = ContextVar("haute_disk_cache_only_model_loads", default=False)


@contextmanager
def disk_cache_only_model_loads() -> Iterator[None]:
    """Within this scope, :func:`load_mlflow_model` loads only from the disk model cache.

    Where it would otherwise resolve the model through a tracking server or
    registry and download it, it raises :class:`ModelNotInDiskCacheError`.
    """
    token = _DISK_CACHE_ONLY.set(True)
    try:
        yield
    finally:
        _DISK_CACHE_ONLY.reset(token)


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

    ``offset_declared`` is ``False`` only for a CatBoost model whose file does
    not record whether it was trained with an offset, or an XGBoost model
    trained outside Haute without the ``haute_offset`` stamp;
    :func:`bind_feature_contract` takes the declaration from its contract. Scoring reads the
    offset from this carrier, never the raw model, so a contract-bound offset
    applies to a raw model a cache may share.

    ``optional_feature_names`` are inputs the model reads when the input has
    them and scores without otherwise: a pyfunc signature input marked
    optional (an exposure Haute's own logged model declares). They are never
    among ``feature_names``, which are required.

    ``run_contract_path`` is the local copy of the contract an MLflow run logged
    beside a native model :func:`load_mlflow_model` loaded, read from that same
    run; ``None`` for a run that logged none and for any other model.
    """

    __slots__ = (
        "_model",
        "feature_names",
        "optional_feature_names",
        "cat_feature_names",
        "flavor",
        "offset_column",
        "offset_link",
        "offset_declared",
        "run_contract_path",
    )

    def __init__(
        self,
        model: Any,
        feature_names: list[str],
        cat_feature_names: frozenset[str] = frozenset(),
        flavor: str = "pyfunc",
        offset_column: str | None = None,
        offset_link: str | None = None,
        offset_declared: bool = True,
        run_contract_path: str | None = None,
        optional_feature_names: frozenset[str] = frozenset(),
    ) -> None:
        self._model = model
        self.feature_names = feature_names
        self.optional_feature_names = optional_feature_names
        self.cat_feature_names = cat_feature_names
        self.flavor = flavor
        self.offset_column = offset_column
        self.offset_link = offset_link
        self.offset_declared = offset_declared
        self.run_contract_path = run_contract_path

    def with_offset(self, offset_column: str | None, offset_link: str | None) -> ScoringModel:
        """A declared carrier sharing this raw model, with the given offset."""
        return ScoringModel(
            model=self._model,
            feature_names=self.feature_names,
            optional_feature_names=self.optional_feature_names,
            cat_feature_names=self.cat_feature_names,
            flavor=self.flavor,
            offset_column=offset_column,
            offset_link=offset_link,
            run_contract_path=self.run_contract_path,
        )

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

    recorded = model.get_metadata().get(CATBOOST_CLASS_LABELS_METADATA_KEY)
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


def verify_contract_identity(identity: Any, scoring_model: ScoringModel) -> None:
    """Fail when a loaded model is not the model its contract's identity describes."""
    from haute.errors import ConfigError

    expected = family_for_algorithm(identity.algorithm)
    if expected is None or expected.flavor != scoring_model.flavor:
        raise ConfigError(
            f"The feature contract describes a {identity.algorithm} model, but the model file "
            f"loads as {scoring_model.flavor}. Use the contract saved with this model.",
            algorithm=identity.algorithm,
            flavor=scoring_model.flavor,
        )
    if expected.self_describing and identity.loss:
        from haute.modelling._descriptors import algorithm_descriptor

        descriptor = algorithm_descriptor(identity.algorithm)
        objective = scoring_model.raw_model.objective()
        task = "classification" if identity.link == "logit" else "regression"
        expected_objective = descriptor.native_loss(task, identity.loss).objective
        if objective != expected_objective:
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


def _catboost_offset_declared(model: Any) -> bool:
    """Whether a CatBoost model's metadata declares its offset (or its absence).

    Haute stamps ``CATBOOST_OFFSET_METADATA_KEY`` at every fit, empty for a fit
    without an offset. A model without the key (trained outside Haute, or by
    an earlier Haute) declares nothing: its baseline must come from a contract.
    """
    from haute.modelling._algorithms import CATBOOST_OFFSET_METADATA_KEY

    return isinstance(model.get_metadata().get(CATBOOST_OFFSET_METADATA_KEY), str)


def _catboost_offset_column(model: Any) -> str | None:
    """Read the trained-with offset column stamped into the .cbm metadata.

    ``CatBoostAlgorithm.fit`` records the offset column name under
    ``CATBOOST_OFFSET_METADATA_KEY`` because the .cbm format has no native
    baseline memory — without this, a served model would ignore the offset
    it was trained with. ``None`` covers both an empty value (no offset) and an
    absent key (undeclared, see :func:`_catboost_offset_declared`); a failed
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
        offset_declared=_catboost_offset_declared(model),
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


def _load_catboost_file(
    path: str, task: str, *, contract_path: str | None, source: str | None
) -> ScoringModel:
    """The CatBoost family's file loader."""
    raw = _load_catboost_model(path, task)
    return _wrap_catboost(raw, source=source or repr(path))


def _load_rustystats_file(
    path: str, task: str, *, contract_path: str | None, source: str | None
) -> ScoringModel:
    """The RustyStats GLM family's file loader (the GLM records no task)."""
    return _load_rustystats_model(path, source=source)


def load_local_model(
    path: str,
    task: str = "regression",
    *,
    contract_path: str | None = None,
    source: str | None = None,
) -> ScoringModel:
    """Load a model from a local file path (e.g. bundled deploy artifact).

    The file's suffix selects its family in the registry, whose loader reads
    it from the file alone; *contract_path* is the contract the caller binds.

    *source* names the model in errors (an MLflow run); the path otherwise.

    Raises:
        ConfigError: no family loads files with this suffix, or the path names
            a family that loads only from MLflow (a pyfunc directory).
    """
    family = family_for_artifact(path)
    if family.load_file is None:
        from haute.errors import ConfigError

        raise ConfigError(
            f"{path!r} is not a model file Haute loads locally. Expected "
            f"{', '.join(model_file_suffixes())}; an {family.label} model loads only from MLflow.",
            model_path=path,
            flavor=family.flavor,
        )
    return family.load_file(path, task, contract_path=contract_path, source=source)


def _offset_transform(link: str | None) -> str | None:
    """How an offset enters the raw score: ``log``, else ``identity`` (``None`` for none)."""
    if link is None:
        return None
    return "log" if link == "log" else "identity"


def _declare_offset(
    scoring_model: ScoringModel, offset_column: str | None, offset_link: str | None
) -> ScoringModel:
    """The carrier with a contract-declared offset; a wrapper adding its own margin gets it too."""
    if not model_family(scoring_model.flavor).self_describing:
        return scoring_model.with_offset(offset_column, offset_link)
    import dataclasses

    wrapper = dataclasses.replace(
        scoring_model.raw_model,
        offset_column=offset_column,
        offset_link=offset_link,
        offset_declared=True,
    )
    return ScoringModel(
        model=wrapper,
        feature_names=scoring_model.feature_names,
        cat_feature_names=scoring_model.cat_feature_names,
        flavor=scoring_model.flavor,
        offset_column=offset_column,
        offset_link=offset_link,
        run_contract_path=scoring_model.run_contract_path,
    )


def bind_feature_contract(
    scoring_model: ScoringModel, contract: FeatureContract | None, *, model_name: str
) -> ScoringModel:
    """Bind a loaded model to the feature contract it scores under.

    A contract that records a model identity must describe the loaded model.
    A model that declares its own offset must agree with the contract's; a
    pyfunc model, whose contract is its only offset description, takes the
    contract's. A model whose file does not declare its offset (a CatBoost
    model, or an XGBoost model trained outside Haute) takes the contract's
    declaration: a null ``offset_column`` declares no offset, a named one with
    an ``offset_link`` declares that offset. With no contract an undeclared
    CatBoost model scores with no offset, the full-policy-term basis a quote is
    priced on (it predicts the same with no baseline as with a zero one), and
    an undeclared XGBoost model is refused: XGBoost scores a model trained with
    a margin from its ``base_score`` when none is supplied. Returns the carrier
    to score (a new one when the offset changes); *scoring_model* itself, which
    a cache may hold, is never mutated.

    Raises:
        ConfigError: the identity or the offset disagree, the contract of a
            model that declares no offset names an offset column without its
            link, or an undeclared XGBoost model has no contract.
    """
    from haute.errors import ConfigError

    if contract is not None and contract.model is not None:
        verify_contract_identity(contract.model, scoring_model)
    if not scoring_model.offset_declared:
        label = model_family(scoring_model.flavor).label
        remedy = (
            "Save a feature contract beside the model that declares its offset "
            "(offset_column with offset_link 'log' or 'identity', or offset_column null "
            "for a model trained without one), or retrain it with this version of Haute."
        )
        if contract is None:
            if scoring_model.flavor == "xgboost":
                raise ConfigError(
                    f"XGBoost model {model_name} was trained outside Haute and does not say "
                    "whether it was trained with a margin (base_margin); XGBoost scores such a "
                    "model from its base_score when no margin is given, which is not a full "
                    "policy term. Declare it before saving: "
                    'booster.set_attr(haute_offset="none") for no margin, or '
                    'booster.set_attr(haute_offset="exposure:log") for log-exposure margins '
                    f'("<column>:identity" for an additive one). {remedy}',
                    model=model_name,
                )
            return scoring_model.with_offset(None, None)
        if contract.offset_column is not None and contract.offset_link is None:
            raise ConfigError(
                f"The feature contract of {label} model {model_name} names offset column "
                f"{contract.offset_column!r} but not how it enters the model "
                f"(offset_link). {remedy}",
                model=model_name,
                offset_column=contract.offset_column,
            )
        return _declare_offset(scoring_model, contract.offset_column, contract.offset_link)
    if contract is None:
        return scoring_model
    if scoring_model.flavor == "pyfunc":
        return scoring_model.with_offset(contract.offset_column, contract.offset_link)
    model_link = _offset_transform(scoring_model.offset_link)
    if contract.offset_column != scoring_model.offset_column or (
        contract.offset_link is not None and contract.offset_link != model_link
    ):
        raise ConfigError(
            f"Model {model_name} records offset {scoring_model.offset_column!r} "
            f"(link {model_link!r}), but its feature contract declares "
            f"{contract.offset_column!r} (link {contract.offset_link!r}). Use the contract "
            "saved with this model.",
            model=model_name,
            model_offset_column=scoring_model.offset_column,
            contract_offset_column=contract.offset_column,
        )
    return scoring_model


_local_model_cache: StatGatedCache[tuple[str, str, str | None], ScoringModel] = StatGatedCache(
    artifact_kind="local model file"
)


def load_local_model_cached(
    path: str,
    task: str,
    contract_path: str | None = None,
    *,
    model_name: str | None = None,
) -> ScoringModel:
    """Load a local model file bound to *contract_path*, through a stat-gated cache.

    The one in-memory local-model cache, shared by file-sourced Model Scoring
    and the deploy scorer. A slot is keyed on the case-folded path, the task
    and the contract's byte identity, and gated on the model file's freshness
    token: replacing the model or only its contract reloads, and a missing
    model or contract raises however warm the cache. A failed load or binding
    is never cached. *model_name* names the model in errors (its file name
    otherwise).
    """
    from haute.deploy._scorer import artifact_identity_fingerprint

    io_path = resolve_artifact_path(path)
    name = model_name or repr(Path(path).name)
    # The contract decides an undeclared CatBoost model's offset and is checked
    # against every other model: replacing only it must reload.
    contract_identity = (
        artifact_identity_fingerprint({"contract": contract_path}) if contract_path else None
    )

    def _load() -> ScoringModel:
        from haute.modelling._feature_contract import load_contract_cached

        contract = load_contract_cached(contract_path) if contract_path else None
        scoring_model = load_local_model(
            io_path, task, contract_path=contract_path, source=model_name
        )
        return bind_feature_contract(scoring_model, contract, model_name=name)

    return _local_model_cache.get_or_load(
        (artifact_cache_key(io_path), task, contract_identity), io_path, _load
    )


def clear_local_model_cache() -> None:
    """Drop every cached local model (test isolation / targeted resets)."""
    _local_model_cache.clear()


def model_contract_candidates(path: str | Path) -> list[Path]:
    """Where a model file's feature contract sits: beside it, by name or in a package."""
    from haute.modelling._feature_contract import CONTRACT_FILENAME
    from haute.modelling._training_job import model_contract_filename

    model_file = Path(path)
    return [
        model_file.with_name(model_contract_filename(model_file.stem)),
        model_file.parent / CONTRACT_FILENAME,
    ]


def _load_xgboost_file(
    path: str, task: str, *, contract_path: str | None, source: str | None
) -> ScoringModel:
    """The XGBoost family's file loader; the file describes itself."""
    from haute.modelling._xgboost import XGBoostModel

    return _wrapper_scoring_model(XGBoostModel.load(path), task, "xgboost")


def _load_lightgbm_file(
    path: str, task: str, *, contract_path: str | None, source: str | None
) -> ScoringModel:
    """The LightGBM family's file loader; the file describes itself."""
    from haute.modelling._lightgbm import LightGBMModel

    return _wrapper_scoring_model(LightGBMModel.load(path), task, "lightgbm")


def _load_ebm_file(
    path: str, task: str, *, contract_path: str | None, source: str | None
) -> ScoringModel:
    """The EBM family's file loader.

    An EBM file records what its estimator alone does not say, so it loads
    from the file alone.
    """
    from haute.modelling._ebm import EBMModel

    return _wrapper_scoring_model(EBMModel.load(path), task, "ebm")


def _load_tboost_file(
    path: str, task: str, *, contract_path: str | None, source: str | None
) -> ScoringModel:
    """The t-boost family's file loader; the file describes itself."""
    from haute.modelling._tboost import TBoostModel

    return _wrapper_scoring_model(TBoostModel.load(path), task, "tboost")


def _wrapper_scoring_model(model: Any, task: str, flavor: str) -> ScoringModel:
    """Wrap a loaded Haute native model, refusing the task it was not trained for."""
    from haute.errors import ConfigError

    if model.task != task:
        label = model_family(flavor).label
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
        offset_declared=getattr(model, "offset_declared", True),
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


#: The outputs of Haute's own classifier pyfunc (``modelling/_native_pyfunc.py``):
#: the original label and the positive-class probability, in this order.
PYFUNC_CLASSIFIER_OUTPUTS = ("pred_label", "pred_proba")


def _wrap_pyfunc(model: Any, *, task: str) -> ScoringModel:
    """Wrap an MLflow pyfunc model in a ``ScoringModel`` scoring it as *task*.

    The signature's outputs fix the predict surface: Haute's classifier pair
    (:data:`PYFUNC_CLASSIFIER_OUTPUTS`) scores labels and probabilities from
    their own columns; anything else must return one prediction per row.

    Raises:
        ConfigError: the signature declares the classifier pair but the node
            scores regression, or declares several outputs of another kind.
    """
    from haute.errors import ConfigError

    outputs = _pyfunc_output_names(model)
    predictor: _PyfuncPredictor
    if outputs == list(PYFUNC_CLASSIFIER_OUTPUTS):
        if task != "classification":
            raise ConfigError(
                "This MLflow model returns a classifier's label and positive-class "
                f"probability ({', '.join(PYFUNC_CLASSIFIER_OUTPUTS)}) but the node scores it "
                f"as {task}. Set the node's task to classification.",
                trained_task="classification",
                task=task,
            )
        predictor = _PyfuncClassifier(model)
    elif outputs is not None and len(outputs) > 1:
        raise ConfigError(
            f"This MLflow model declares {len(outputs)} outputs "
            f"({', '.join(str(name) for name in outputs)}); Model Scoring reads one "
            f"prediction per row, or a classifier's {' and '.join(PYFUNC_CLASSIFIER_OUTPUTS)}.",
            outputs=[str(name) for name in outputs],
        )
    else:
        predictor = _PyfuncPredictor(model)
    required, optional = _extract_pyfunc_features(model)
    return ScoringModel(
        model=predictor,
        feature_names=required,
        optional_feature_names=optional,
        cat_feature_names=frozenset(),
        flavor="pyfunc",
    )


def _extract_pyfunc_features(model: Any) -> tuple[list[str], frozenset[str]]:
    """The signature's required input names, and those marked optional."""
    sig = getattr(model.metadata, "signature", None)
    inputs = None if sig is None else sig.inputs
    if inputs is None:
        return [], frozenset()
    return list(inputs.required_input_names()), frozenset(inputs.optional_input_names())


def _pyfunc_output_names(model: Any) -> list[Any] | None:
    """The output names a pyfunc signature declares, or ``None`` without them."""
    sig = getattr(model.metadata, "signature", None)
    outputs = None if sig is None else sig.outputs
    return None if outputs is None else list(outputs.input_names())


class _PyfuncPredictor:
    """A pyfunc model behind the ``predict`` surface the scorer reads.

    MLflow returns whatever the model's code returns. Model Scoring reads one
    prediction per row: a vector, or a one-column table. Any other shape is
    refused by its shape, never flattened into more values than rows.
    """

    def __init__(self, model: Any) -> None:
        self._model = model

    def predict(self, x_data: Any) -> np.ndarray:
        import pandas as pd

        output = self._model.predict(x_data)
        if isinstance(output, pd.DataFrame):
            if output.shape[1] != 1:
                raise ValueError(
                    f"The MLflow model returned {output.shape[1]} columns "
                    f"({', '.join(map(str, output.columns))}) for each row; Model Scoring reads "
                    "one prediction per row, or a classifier's "
                    f"{' and '.join(PYFUNC_CLASSIFIER_OUTPUTS)} declared by its signature."
                )
            values: np.ndarray = output.iloc[:, 0].to_numpy()
        else:
            values = np.asarray(output)
            if values.ndim == 2 and values.shape[1] == 1:
                values = values[:, 0]
            elif values.ndim != 1:
                raise ValueError(
                    f"The MLflow model returned predictions of shape {values.shape}; Model "
                    "Scoring reads one prediction per row."
                )
        _require_one_per_row(len(values), x_data)
        return values


class _PyfuncClassifier(_PyfuncPredictor):
    """Haute's classifier pyfunc: labels and probabilities from their own columns.

    Labels take the type the signature declares for ``pred_label``, so an
    empty input keeps the typed column a scored one has. One model call
    serves both: ``predict`` keeps the probabilities for the
    ``predict_proba`` that every classification path makes next on the same
    input, per thread because a cached model is shared across scoring threads.
    """

    def __init__(self, model: Any) -> None:
        super().__init__(model)
        self._label_dtype = model.metadata.signature.outputs.inputs[0].type.to_numpy()
        self._pending = threading.local()

    @property
    def label_dtype(self) -> pl.DataType:
        """The Polars type of the labels, as the signature declares them."""
        return pl.Series("label", np.array([], dtype=self._label_dtype)).dtype

    def _pair(self, x_data: Any) -> tuple[np.ndarray, np.ndarray]:
        import pandas as pd

        output = self._model.predict(x_data)
        columns = list(output.columns) if isinstance(output, pd.DataFrame) else None
        if columns != list(PYFUNC_CLASSIFIER_OUTPUTS):
            shape = f"columns {columns}" if columns is not None else type(output).__name__
            raise ValueError(
                f"The MLflow model declares the outputs {list(PYFUNC_CLASSIFIER_OUTPUTS)} but "
                f"returned {shape}."
            )
        _require_one_per_row(len(output), x_data)
        labels = output["pred_label"].to_numpy().astype(self._label_dtype)
        return labels, output["pred_proba"].to_numpy(dtype=np.float64)

    def predict(self, x_data: Any) -> np.ndarray:
        labels, probabilities = self._pair(x_data)
        self._pending.scored = (x_data, probabilities)
        return labels

    def predict_proba(self, x_data: Any) -> np.ndarray:
        pending = getattr(self._pending, "scored", None)
        self._pending.scored = None
        if pending is not None and pending[0] is x_data:
            probabilities: np.ndarray = pending[1]
            return probabilities
        return self._pair(x_data)[1]


def _require_one_per_row(predictions: int, x_data: Any) -> None:
    rows = len(x_data)
    if predictions != rows:
        raise ValueError(
            f"The MLflow model returned {predictions} predictions for {rows} rows; Model "
            "Scoring reads one prediction per row."
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
    stored: StoredArtifact,
) -> str:
    """Return a local copy of one identity of a run artifact, downloading only if needed.

    Saves downloaded artifacts under
    ``.cache/models/<backend digest>/<run_id>/<path digest>/<token>/`` so they
    survive server restarts without re-downloading from remote tracking servers
    (saves ~30 s+ for Databricks-hosted artifacts) while never serving one
    backend's bytes for another's run, nor one identity's bytes for another's.

    Downloads to a temp file first then renames atomically, so a partial
    download (network interruption, timeout) never leaves a corrupt file
    in the cache, and the download must be the bytes the store said it holds
    (its size, or on a local store its content hash): a file logged again
    between the listing and the download raises ``ModelResolutionError``.

    Same-artifact callers are serialized on the per-artifact I/O lock:
    exactly one thread downloads while the others wait, then find the
    file via the in-lock re-check — no thundering herd, and the move can
    never land on a file another thread is concurrently writing.

    A local backend's file store is opted into first: a process that has
    opened no client for it would otherwise have MLflow refuse the download.
    """
    allow_file_store_if_local(backend.tracking_uri, backend.mode)
    with _disk_cache_run_in_use(run_id):
        return _resolve_artifact_local_in_use(mlflow, backend, run_id, stored)


def _resolve_artifact_local_in_use(
    mlflow: Any,
    backend: ResolvedBackend,
    run_id: str,
    stored: StoredArtifact,
) -> str:
    """Implementation for ``_resolve_artifact_local`` while eviction is guarded."""
    import shutil
    import tempfile

    cache_root = _disk_cache_root()
    local_path = _artifact_cache_path(cache_root, backend.digest, run_id, stored.path, stored.token)
    cache_dir = local_path.parent

    if local_path.is_file():
        logger.info("mlflow_artifact_disk_cache_hit", path=str(local_path))
        return str(local_path)

    with _artifact_io_lock(backend.digest, run_id, stored.path):
        # Re-check under the lock: a concurrent caller may have completed
        # the download while this thread was waiting to acquire.
        if local_path.is_file():
            logger.info("mlflow_artifact_disk_cache_hit", path=str(local_path))
            return str(local_path)
        if _DISK_CACHE_ONLY.get():
            # A copy that went after the record was read is the load's failure,
            # never a download.
            from haute.errors import ModelNotInDiskCacheError

            raise ModelNotInDiskCacheError(
                f"The local model cache no longer holds {stored.path!r} of run {run_id!r}, "
                "and this execution loads models only from that cache. Preview the Model "
                "Scoring node in the editor, which fills it.",
                source_type="run",
            )

        # Cache miss — download to a temp directory first, then move into
        # place atomically.  If the download is interrupted the temp dir
        # is cleaned up and no corrupt file is left in the cache.
        logger.info(
            "mlflow_artifact_downloading",
            run_id=run_id,
            artifact=stored.path,
            backend_mode=backend.mode,
            backend_digest=backend.digest,
        )
        tmp_dir = None
        try:
            tmp_dir = Path(tempfile.mkdtemp(prefix="haute_dl_"))
            downloaded = mlflow.artifacts.download_artifacts(
                f"runs:/{run_id}/{stored.path}",
                dst_path=str(tmp_dir),
                tracking_uri=backend.tracking_uri,
            )
            downloaded_path = Path(downloaded)
            if not downloaded_path.is_file():
                # download_artifacts may nest; look for the expected filename
                downloaded_path = tmp_dir / Path(stored.path).name
            if not downloaded_path.is_file():
                raise FileNotFoundError(
                    f"Download completed but artifact not found at {downloaded_path}"
                )
            _require_stored_bytes(downloaded_path, stored, run_id)

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


def _require_stored_bytes(downloaded: Path, stored: StoredArtifact, run_id: str) -> None:
    """Refuse a download that is not the bytes the store described."""
    from haute._mlflow_model import copy_identity
    from haute.errors import ModelResolutionError

    if copy_identity(stored, downloaded) != stored.identity:
        raise ModelResolutionError(
            f"The model {stored.path!r} in MLflow run {run_id!r} changed while it was "
            "loaded. Load it again.",
            reference=f"MLflow run {run_id!r}",
            candidates=[],
        )


@dataclass(frozen=True, slots=True)
class FetchedFiles:
    """The local copies of a resolved model and the contract beside it."""

    model: Path
    contract: Path | None


def fetch_resolved_files(mlflow: Any, resolved: ResolvedModel) -> FetchedFiles:
    """Copy a resolved model file, and its contract, into the disk cache under their identities.

    Then record them as the copies the last resolved load of the artifact used
    (the record a disk-cache-only load reads) and remove the older copies of
    both paths. Only a file-loaded family is copied; a pyfunc folder loads
    through MLflow.
    """
    if not resolved.loads_from_file:
        raise ValueError(f"{resolved.artifact.path!r} is a pyfunc folder; it is not copied.")
    backend, run_id = resolved.backend, resolved.run_id
    with _disk_cache_run_in_use(run_id):
        model = Path(_resolve_artifact_local(mlflow, backend, run_id, resolved.artifact))
        contract = (
            None
            if resolved.contract is None
            else Path(_resolve_artifact_local(mlflow, backend, run_id, resolved.contract))
        )
        _record_resolution(resolved)
    return FetchedFiles(model, contract)


def _record_resolution(resolved: ResolvedModel) -> None:
    import json

    from haute._file_ops import atomic_write_bytes

    root = _disk_cache_root()
    contract = resolved.contract
    record = {
        "artifact": resolved.artifact.identity,
        "contract": None
        if contract is None
        else {"path": contract.path, "identity": contract.identity},
    }
    record_path = _resolution_record_path(
        root, resolved.backend, resolved.run_id, resolved.artifact.path
    )
    payload = json.dumps(record, sort_keys=True).encode("utf-8")
    if not record_path.is_file() or record_path.read_bytes() != payload:
        atomic_write_bytes(record_path, payload)
    for stored in (resolved.artifact, contract):
        if stored is not None:
            _remove_older_copies(root, resolved, stored)


def _remove_older_copies(root: Path, resolved: ResolvedModel, stored: StoredArtifact) -> None:
    """Remove the copies of *stored*'s path under identities other than its own.

    Housekeeping, like eviction: it never raises into a load that only asked for
    a model. A copy that survives (held open by another process) or a folder that
    cannot be listed is logged and left for a later pass or for eviction.
    """
    from haute._file_ops import remove_tree

    folder = root / _artifact_cache_dir(resolved.backend.digest, resolved.run_id, stored.path)
    try:
        copies = [copy for copy in folder.iterdir() if copy.is_dir()]
    except OSError as exc:
        logger.warning(
            "mlflow_disk_cache_stale_copy_kept", path=str(folder), error_type=type(exc).__name__
        )
        return
    for copy in copies:
        if copy.name != stored.token and not remove_tree(copy):
            logger.warning("mlflow_disk_cache_stale_copy_kept", path=str(copy))


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
        # Cache keys are ``("mlflow", run_id, artifact, artifact identity,
        # contract identity, task, backend_identity)`` — run_id is slot 1,
        # so the match is backend-agnostic.
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
    resolved: ResolvedModel,
    task: str,
) -> ScoringModel:
    """Fetch the resolved files and load the model with a bounded retry window.

    On failure deletes the suspect cached model copy, sleeps for a brief
    exponential-backoff interval with jitter, re-downloads, and retries.
    After :data:`_LOAD_MAX_ATTEMPTS` total attempts, propagates the final
    failure with a diagnostic message noting that the retry was exhausted —
    an operator can then act on truly corrupt artifacts instead of watching
    a silent loop re-download the same bad bytes.
    """
    import random
    import time

    from haute.errors import HauteError, HauteValidationError

    run_id, artifact, flavor = resolved.run_id, resolved.artifact.path, resolved.family.flavor
    load_file = resolved.family.load_file
    assert load_file is not None  # only file-loaded families retry here
    last_err: BaseException | None = None
    for attempt in range(1, _LOAD_MAX_ATTEMPTS + 1):
        local_path: Path | None = None
        try:
            fetched = fetch_resolved_files(mlflow_mod, resolved)
            local_path = fetched.model
            contract_path = None if fetched.contract is None else str(fetched.contract)
            scoring_model = load_file(
                str(local_path),
                task,
                contract_path=contract_path,
                source=f"MLflow run {run_id!r}, artifact {artifact!r}",
            )
            scoring_model.run_contract_path = contract_path
            return scoring_model
        except (AttributeError, TypeError, KeyError, HauteError, HauteValidationError):
            # A missing attribute, wrong type or unknown key is a bug in the
            # dispatch code (or a breaking library change), and every Haute
            # error is a refusal of bytes Haute could read (the wrong task, a
            # model without Haute's record, a contract that does not describe
            # it, another engine version, bytes that are not what the store
            # described). Re-downloading fixes none, and reporting them as a
            # corrupt artifact would hide the real reason.
            raise
        except Exception as err:
            if _DISK_CACHE_ONLY.get():
                # This execution may neither fetch the model again nor change the
                # cache: an unreadable copy is its failure, and the copy stays.
                from haute.errors import ModelNotInDiskCacheError

                raise ModelNotInDiskCacheError(
                    f"The local model cache's copy of {artifact!r} of MLflow run {run_id!r} "
                    f"does not load ({type(err).__name__}), and this execution loads models "
                    "only from that cache and leaves it as it is. Preview the Model Scoring "
                    "node in the editor, which fetches the model again.",
                    source_type="run",
                ) from err
            last_err = err
            logger.warning(
                "model_load_failed",
                attempt=attempt,
                max_attempts=_LOAD_MAX_ATTEMPTS,
                path=None if local_path is None else str(local_path),
                error=str(err),
            )
            # Delete suspected-corrupt cache so the next attempt
            # re-downloads from scratch.
            if local_path is not None and local_path.is_file():
                local_path.unlink()
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


_VALID_TASKS = ("regression", "classification")


def model_resolution(
    source: RunModelSource | RegisteredModelSource, *, backend: ResolvedBackend
) -> ResolvedModel:
    """The answer a load of *source* uses: the store's, or the disk cache's record.

    Outside :func:`disk_cache_only_model_loads` this is
    :func:`~haute._mlflow_model.resolve_model`. Inside it nothing asks the store:
    a run source with an artifact path is answered by the record the last
    resolved load wrote, when its copies are all present, and anything else
    raises :class:`~haute.errors.ModelNotInDiskCacheError`.
    """
    if not _DISK_CACHE_ONLY.get():
        return resolve_model(source, backend=backend)
    from haute._model_source import RunModelSource
    from haute.errors import ModelNotInDiskCacheError

    if isinstance(source, RunModelSource) and source.artifact_path:
        validate_artifact_path(source.artifact_path)
        with _disk_cache_run_in_use(source.run_id):
            recorded = _recorded_resolution(backend, source.run_id, source.artifact_path)
            if recorded is not None and recorded[1].present():
                return recorded[0]
        reference = f"run {source.run_id!r}, artifact {source.artifact_path!r}"
    elif isinstance(source, RunModelSource):
        reference = f"run {source.run_id!r}"
    else:
        reference = f"registered model {source.registered_model!r}"
    raise ModelNotInDiskCacheError(
        f"The local model cache does not hold {reference} ready to load, and this "
        "execution loads models only from that cache. Preview the Model Scoring node "
        "in the editor, which fills it.",
        source_type=source.source_type,
    )


def load_mlflow_model(
    source: RunModelSource | RegisteredModelSource,
    *,
    task: str = "regression",
    backend: ResolvedBackend | None = None,
) -> ScoringModel:
    """Load the model an MLflow *source* resolves to, through its registered model family.

    The model resolution (:func:`model_resolution`) answers which model the
    source means; :func:`load_resolved_model` loads that answer.

    Args:
        source: The parsed run or registered source.
        task: ``"regression"`` or ``"classification"`` — checked against a
            CatBoost model's recorded loss; a pyfunc reads its signature.
        backend: The destination already resolved by a caller that makes
            further reads of the same source, so every read uses one backend;
            the source's destination is resolved here otherwise.

    Raises:
        MlflowConfigError: If the destination cannot be resolved — raised
            before any cache lookup, so a warm entry is never served for a
            destination whose prerequisites are gone.
        ModelResolutionError: the source does not resolve to one model.
    """
    from haute._mlflow_utils import resolve_backend

    if task not in _VALID_TASKS:
        raise ValueError(f"Invalid task {task!r}. Expected one of: {', '.join(_VALID_TASKS)}")
    backend = backend if backend is not None else resolve_backend(source.mlflow_destination)
    return load_resolved_model(model_resolution(source, backend=backend), task)


def _keep_copies_current(resolved: ResolvedModel) -> None:
    """Leave a memory hit's copies and record as a load of the same answer would.

    The cached model names the copy of its contract (``run_contract_path``) and a
    disk-cache-only load reads the record, so a hit for bytes another load has since
    replaced (A, then B, then A again) restores A's copies and record. Unchanged
    copies cost a stat and a record compare; a pyfunc folder has none.
    """
    if not resolved.loads_from_file:
        return
    import mlflow

    fetch_resolved_files(mlflow, resolved)


def load_resolved_model(resolved: ResolvedModel, task: str) -> ScoringModel:
    """Load the model a resolution answered, cached in memory under its identities."""
    if task not in _VALID_TASKS:
        raise ValueError(f"Invalid task {task!r}. Expected one of: {', '.join(_VALID_TASKS)}")
    backend, run_id, artifact = resolved.backend, resolved.run_id, resolved.artifact.path
    flavor = resolved.family.flavor
    cache_key = _model_cache_key(resolved, task)
    cached = _model_cache.get(cache_key)
    if cached is not None:
        _keep_copies_current(resolved)
        _record_cache_hit(run_id=run_id, artifact_path=artifact, flavor=flavor)
        return cached

    # Single-flight the download + load per on-disk artifact: one thread
    # does the work while same-artifact callers wait, then reuse the
    # in-memory entry via the re-check below.  This also makes the
    # corrupt-retry's delete + re-download mutually exclusive with any
    # concurrent load of the same cached file.
    with _artifact_io_lock(backend.digest, run_id, artifact):
        cached = _model_cache.get(cache_key)
        if cached is not None:
            _keep_copies_current(resolved)
            _record_cache_hit(run_id=run_id, artifact_path=artifact, flavor=flavor)
            return cached

        # Real-path miss — record the miss (counter + structured event)
        # before the potentially-expensive download so the miss is
        # observable even if the subsequent load raises.
        _record_cache_miss(run_id=run_id, artifact_path=artifact, flavor=flavor)
        import mlflow as mlflow_mod

        if resolved.loads_from_file:
            with _disk_cache_run_in_use(run_id):
                scoring_model = _load_with_bounded_retry(
                    mlflow_mod=mlflow_mod, resolved=resolved, task=task
                )
        else:
            raw_model = _load_pyfunc_model(mlflow_mod, run_id, artifact, backend=backend)
            scoring_model = _wrap_pyfunc(raw_model, task=task)

        _model_cache.put(cache_key, scoring_model)

    logger.info(
        "mlflow_model_loaded",
        run_id=run_id,
        artifact=artifact,
        version=resolved.version,
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
    flavor: str = "pyfunc",
) -> Any:
    """Prepare a Polars DataFrame for model prediction.

    Dispatch on the family's registered ``predict_frame``:

    - ``polars``: Polars DataFrame, untouched — RustyStats and the Haute
      wrappers own their own preprocessing (nulls, categoricals, casts).
    - ``pandas`` (pyfunc): **named pandas DataFrame with native dtypes**.  MLflow
      pyfunc models carry signatures; named-column signatures (the
      standard ``infer_signature`` case) hard-reject unnamed numpy input
      (``MlflowException: Model is missing inputs [...]``), and mlflow's
      own schema enforcement converts dtypes to the declared types —
      handing it a preemptive Float32 downcast would be silently upcast
      back to ``double`` with the mantissa bits already gone.  Float
      nulls surface as NaN; integer columns containing nulls widen to
      float64 NaN via the Arrow conversion.
    - ``tabular`` (catboost): numerics cast to Float32 (CatBoost's internal compute
      dtype; null→NaN); declared categoricals filled with the
      ``_MISSING_`` sentinel and carried as ``pd.Categorical`` through
      pandas.  Without categoricals, the numpy fast path applies — the
      ONLY numpy branch (it avoids the Arrow-to-pandas round-trip that
      keeps the buffer alive twice).

    Unregistered flavors raise ``ValueError`` naming the registered ones —
    routing them through any branch would score with a guessed input
    contract.
    """
    if not is_registered_flavor(flavor):
        raise ValueError(
            f"Unknown model flavor {flavor!r} for predict-frame preparation. "
            f"Expected one of: {sorted(f.flavor for f in model_families())}."
        )
    from haute._polars_dtypes import wide_integer_column, wide_integer_message
    from haute.errors import SchemaMismatchError

    # ``to_numpy`` panics on a 128-bit integer, a BaseException no handler
    # catches, and Arrow has no type for one: name the column instead.
    schema = df_eager.schema
    wide = wide_integer_column(
        {column: schema[column] for column in features or schema.names() if column in schema}
    )
    if wide is not None:
        raise SchemaMismatchError(wide_integer_message(*wide), column=wide[0])

    predict_frame = model_family(flavor).predict_frame
    if predict_frame == "polars":
        return df_eager.select(features) if features else df_eager

    cat_cols = [c for c in features if c in cat_feature_names]
    selected = df_eager.select(features)
    if cat_cols:
        selected = selected.with_columns(
            [pl.col(c).fill_null("_MISSING_").cast(pl.Categorical) for c in cat_cols]
        )

    if predict_frame == "pandas":
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
    # tabular path skips the pandas wrapper entirely.
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
) -> pl.LazyFrame:
    """Collect a LazyFrame and score in-memory. Returns a LazyFrame.

    Thin delegate onto :func:`haute._model_scorer.score_frame` with
    ``batch=False`` — the unified scoring entry point owns the flavor
    dispatch and the batch/eager fork. The carrier's offset (self-described
    or contract-bound) is re-applied at score time.
    """
    from haute._model_scorer import _declared_offset_column, _declared_offset_link, score_frame

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
        offset_column=_declared_offset_column(scoring_model),
        offset_link=_declared_offset_link(scoring_model),
    )
