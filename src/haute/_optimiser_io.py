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
import json
from pathlib import Path
from typing import Any

from haute._hashing import content_hash
from haute._logging import get_logger
from haute._lru_cache import LRUCache
from haute._mlflow_utils import resolve_mlflow_source

logger = get_logger(component="optimiser_io")

_ARTIFACT_CACHE_MAX_SIZE = 8
_artifact_cache: LRUCache[tuple[str, str], dict[str, Any]] = LRUCache(
    max_size=_ARTIFACT_CACHE_MAX_SIZE,
)


def load_optimiser_artifact(path: str) -> dict[str, Any]:
    """Load an optimiser artifact JSON file with content-hash caching.

    The cache key is ``(path, content_hash)`` so edits to the file on
    disk are picked up automatically — including same-second overwrites
    where ``mtime`` would not change (a TOCTOU hazard).  Bounded to
    ``_ARTIFACT_CACHE_MAX_SIZE`` entries with oldest-eviction.

    Returns the full parsed JSON dict (mode, lambdas, constraints,
    factor_tables, version, etc.).
    """
    digest = content_hash(Path(path))
    key = (path, digest)

    cached = _artifact_cache.get(key)
    if cached is not None:
        logger.debug("optimiser_artifact_cache_hit", path=path)
        return copy.deepcopy(cached)

    with open(path, encoding="utf-8") as f:
        artifact: dict[str, Any] = json.load(f)

    _artifact_cache.put(key, artifact)
    logger.info("optimiser_artifact_loaded", path=path, mode=artifact.get("mode"))
    return copy.deepcopy(artifact)


# ---------------------------------------------------------------------------
# MLflow resolution
# ---------------------------------------------------------------------------

_MLFLOW_ARTIFACT_NAME = "optimiser_result.json"

# Separate LRU cache for MLflow-sourced artifacts (keyed by run_id or
# model+version, not file path).
_mlflow_cache: LRUCache[tuple[str, str, str], dict[str, Any]] = LRUCache(
    max_size=_ARTIFACT_CACHE_MAX_SIZE,
)


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
    resolved_run_id, resolved_version, mlflow, _client = resolve_mlflow_source(
        source_type=source_type,
        run_id=run_id,
        registered_model=registered_model,
        version=version,
        tracking_uri=tracking_uri,
    )

    cache_key = (source_type, resolved_run_id, resolved_version)
    cached = _mlflow_cache.get(cache_key)
    if cached is not None:
        logger.debug("mlflow_optimiser_cache_hit", key=str(cache_key))
        return copy.deepcopy(cached)

    local_path = mlflow.artifacts.download_artifacts(
        f"runs:/{resolved_run_id}/{_MLFLOW_ARTIFACT_NAME}"
    )

    with open(local_path, encoding="utf-8") as f:
        artifact: dict[str, Any] = json.load(f)

    _mlflow_cache.put(cache_key, artifact)
    logger.info(
        "mlflow_optimiser_artifact_loaded",
        source_type=source_type,
        run_id=resolved_run_id,
        mode=artifact.get("mode"),
    )
    return copy.deepcopy(artifact)
