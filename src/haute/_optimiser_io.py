"""Optimiser artifact loading utilities for the OPTIMISER_APPLY node.

Thread-safe mtime-aware cache for optimiser artifacts (saved JSON files
containing lambdas, factor tables, and version metadata).

Supports two resolution paths:
  - **File**: Load directly from a local JSON path.
  - **MLflow**: Download ``optimiser_result.json`` from an MLflow run
    (by run ID or registered model + version), then load as JSON.

Analogous to ``_mlflow_io.py`` for MLflow models and ``_io.py`` for
external objects.
"""

from __future__ import annotations

import copy
import functools
import json
from pathlib import Path
from typing import Any

from haute._hashing import content_hash
from haute._logging import get_logger
from haute._mlflow_utils import resolve_mlflow_source

logger = get_logger(component="optimiser_io")

_ARTIFACT_CACHE_MAX_SIZE = 8


@functools.lru_cache(maxsize=_ARTIFACT_CACHE_MAX_SIZE)
def _load_artifact_cached(
    path: str,
    digest: str,  # noqa: ARG001 — part of cache key, not used in body.
) -> dict[str, Any]:
    """Memoised artifact loader keyed on ``(path, digest)``.

    The ``digest`` argument (the xxh64 content hash) is NEVER used
    inside the body — it exists solely as part of the
    ``functools.lru_cache`` key so a same-second overwrite (mtime
    unchanged, bytes changed) produces a fresh miss rather than a
    stale hit.  The caller hashes the file on each invocation and
    passes the digest in.
    """
    with open(path, encoding="utf-8") as f:
        artifact: dict[str, Any] = json.load(f)
    return artifact


def load_optimiser_artifact(path: str) -> dict[str, Any]:
    """Load an optimiser artifact JSON file with content-hash caching.

    The cache key is ``(path, content_hash)`` so edits to the file on
    disk are picked up automatically — including same-second overwrites
    where ``mtime`` would not change (a TOCTOU hazard).  Bounded to
    ``_ARTIFACT_CACHE_MAX_SIZE`` entries with oldest-eviction.

    Returns the full parsed JSON dict (mode, lambdas, constraints,
    factor_tables, version, etc.).  A deep copy is returned so callers
    can safely mutate the result without corrupting the cached value.
    """
    digest = content_hash(Path(path))

    info_before = _load_artifact_cached.cache_info()
    artifact = _load_artifact_cached(path, digest)
    info_after = _load_artifact_cached.cache_info()

    if info_after.misses > info_before.misses:
        logger.info("optimiser_artifact_loaded", path=path, mode=artifact.get("mode"))
    else:
        logger.debug("optimiser_artifact_cache_hit", path=path)

    return copy.deepcopy(artifact)


# ---------------------------------------------------------------------------
# MLflow resolution
# ---------------------------------------------------------------------------

_MLFLOW_ARTIFACT_NAME = "optimiser_result.json"


@functools.lru_cache(maxsize=_ARTIFACT_CACHE_MAX_SIZE)
def _load_mlflow_cached(
    source_type: str,
    resolved_run_id: str,
    resolved_version: str,  # noqa: ARG001 — part of cache key, not used in body.
) -> dict[str, Any]:
    """Memoised MLflow artifact loader keyed on the resolved triple.

    Does the MLflow download inside the cached body so repeat calls
    with an identical ``(source_type, run_id, version)`` resolution
    reuse the cached dict without re-invoking ``download_artifacts``.
    ``resolved_version`` participates in the cache key but is not used
    in the body (``run_id`` uniquely identifies the download URI).

    Imports :mod:`mlflow` lazily inside the function so a hit served
    from cache never incurs the MLflow import cost, and so modules
    that don't touch MLflow don't pay for the import on collection.
    """
    import mlflow as _mlflow

    local_path = _mlflow.artifacts.download_artifacts(
        f"runs:/{resolved_run_id}/{_MLFLOW_ARTIFACT_NAME}"
    )
    with open(local_path, encoding="utf-8") as f:
        artifact: dict[str, Any] = json.load(f)
    logger.info(
        "mlflow_optimiser_artifact_loaded",
        source_type=source_type,
        run_id=resolved_run_id,
        mode=artifact.get("mode"),
    )
    return artifact


def load_mlflow_optimiser_artifact(
    *,
    source_type: str,
    run_id: str = "",
    registered_model: str = "",
    version: str = "",
    tracking_uri: str = "",
) -> dict[str, Any]:
    """Download and cache an optimiser artifact from MLflow.

    Args:
        source_type: ``"run"`` or ``"registered"``.
        run_id: MLflow run ID (required when *source_type* is ``"run"``).
        registered_model: Registered model name (required when
            *source_type* is ``"registered"``).
        version: Model version (``"1"``, ``"latest"``, etc.).
        tracking_uri: Override tracking URI; auto-detected if empty.

    Returns:
        Parsed artifact dict (same shape as ``load_optimiser_artifact``).
    """
    resolved_run_id, resolved_version, _mlflow, _client = resolve_mlflow_source(
        source_type=source_type,
        run_id=run_id,
        registered_model=registered_model,
        version=version,
        tracking_uri=tracking_uri,
    )

    info_before = _load_mlflow_cached.cache_info()
    artifact = _load_mlflow_cached(source_type, resolved_run_id, resolved_version)
    info_after = _load_mlflow_cached.cache_info()

    if info_after.hits > info_before.hits:
        logger.debug(
            "mlflow_optimiser_cache_hit",
            key=str((source_type, resolved_run_id, resolved_version)),
        )

    return copy.deepcopy(artifact)
