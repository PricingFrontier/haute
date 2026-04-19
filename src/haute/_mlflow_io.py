"""MLflow model loading utilities for the MODEL_SCORE node.

Thread-safe LRU cache for models loaded from MLflow.
Supports CatBoost (native ``.cbm``) and any MLflow pyfunc model.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import polars as pl

from haute._logging import get_logger
from haute._lru_cache import LRUCache
from haute._mlflow_utils import resolve_mlflow_source

if TYPE_CHECKING:
    from catboost import CatBoostClassifier, CatBoostRegressor
    from mlflow.tracking import MlflowClient

logger = get_logger(component="mlflow_io")

_MODEL_CACHE_MAX_SIZE = 16
_DISK_CACHE_MAX_DIRS = 50
_model_cache: LRUCache[tuple[str, str, str, str], ScoringModel] = LRUCache(
    max_size=_MODEL_CACHE_MAX_SIZE,
)


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
    """

    __slots__ = ("_model", "feature_names", "cat_feature_names", "flavor")

    def __init__(
        self,
        model: Any,
        feature_names: list[str],
        cat_feature_names: frozenset[str] = frozenset(),
        flavor: str = "pyfunc",
    ) -> None:
        self._model = model
        self.feature_names = feature_names
        self.cat_feature_names = cat_feature_names
        self.flavor = flavor

    @property
    def raw_model(self) -> Any:
        """Access the underlying model object."""
        return self._model

    def predict(self, x_data: Any) -> np.ndarray:
        """Return 1-D array of predictions."""
        raw = self._model.predict(x_data)
        return np.asarray(raw).flatten()

    def predict_proba(self, x_data: Any) -> np.ndarray | None:
        """Return class probabilities, or ``None`` if unsupported."""
        fn = getattr(self._model, "predict_proba", None)
        if fn is None:
            return None
        return np.asarray(fn(x_data))


# ---------------------------------------------------------------------------
# CatBoost helpers
# ---------------------------------------------------------------------------


def _load_catboost_model(path: str, task: str) -> CatBoostRegressor | CatBoostClassifier:
    """Load a CatBoost model from a local file path."""
    if task == "classification":
        from catboost import CatBoostClassifier

        model = CatBoostClassifier()
    else:
        from catboost import CatBoostRegressor

        model = CatBoostRegressor()
    model.load_model(path)
    return model


def _wrap_catboost(model: CatBoostRegressor | CatBoostClassifier) -> ScoringModel:
    """Wrap a raw CatBoost model in a ``ScoringModel``."""
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
    )


def _load_rustystats_model(path: str) -> ScoringModel:
    """Load a RustyStats GLM from a ``.rsglm`` binary file."""
    import rustystats as rs

    with open(path, "rb") as f:
        model = rs.GLMModel.from_bytes(f.read())
    # Use raw input column names (terms_dict keys) rather than design matrix
    # names (feature_names) -- the GLM handles spline/basis expansion internally.
    if hasattr(model, "terms_dict") and model.terms_dict:
        feature_names = list(model.terms_dict.keys())
    elif hasattr(model, "feature_names"):
        feature_names = list(model.feature_names)
    else:
        feature_names = []
    return ScoringModel(
        model=model,
        feature_names=feature_names,
        cat_feature_names=frozenset(),
        flavor="rustystats",
    )


def load_local_model(path: str, task: str = "regression") -> ScoringModel:
    """Load a model from a local file path (e.g. bundled deploy artifact).

    Auto-detects flavor from file extension:
    - ``.cbm`` → CatBoost native loader
    - ``.rsglm`` → RustyStats GLM loader
    - Otherwise → not yet supported (pyfunc local loading planned)
    """
    if path.endswith(".cbm"):
        raw = _load_catboost_model(path, task)
        return _wrap_catboost(raw)
    if path.endswith(".rsglm"):
        return _load_rustystats_model(path)
    raise NotImplementedError(
        f"Local model loading not yet supported for: {path!r}. "
        "Supported formats: .cbm (CatBoost), .rsglm (RustyStats GLM)."
    )


# ---------------------------------------------------------------------------
# Pyfunc helpers
# ---------------------------------------------------------------------------


def _load_pyfunc_model(mlflow_module: Any, run_id: str, artifact_path: str) -> Any:
    """Load a model via MLflow pyfunc flavor."""
    model_uri = f"runs:/{run_id}/{artifact_path}"
    return mlflow_module.pyfunc.load_model(model_uri)


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
    sig = getattr(getattr(model, "metadata", None), "signature", None)
    if sig is None:
        return []
    inputs = sig.inputs
    if inputs is None:
        return []
    if hasattr(inputs, "input_names"):
        return list(inputs.input_names())
    # Older MLflow versions expose inputs as a list of ColSpec
    return [col.name for col in inputs]


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
        FileNotFoundError: If no artifact with *ext* is found.
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
    raise FileNotFoundError(
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
    """
    try:
        return _find_cbm_artifact(client, run_id), "catboost"
    except FileNotFoundError:
        pass

    try:
        return _find_rsglm_artifact(client, run_id), "rustystats"
    except FileNotFoundError:
        pass

    # Look for a pyfunc model directory (contains MLmodel file)
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

    raise FileNotFoundError(
        f"No model artifact found in run '{run_id}'. "
        "Expected .cbm (CatBoost), .rsglm (RustyStats), or model directory (pyfunc)."
    )


def _evict_disk_cache(cache_root: Path) -> None:
    """Remove oldest run directories when disk cache exceeds the limit.

    Keeps at most ``_DISK_CACHE_MAX_DIRS`` run directories under
    *cache_root*, deleting the ones with the oldest modification time.
    """
    import shutil

    if not cache_root.is_dir():
        return

    run_dirs = [d for d in cache_root.iterdir() if d.is_dir()]
    if len(run_dirs) <= _DISK_CACHE_MAX_DIRS:
        return

    # Sort by modification time, oldest first
    run_dirs.sort(key=lambda d: d.stat().st_mtime)
    to_remove = len(run_dirs) - _DISK_CACHE_MAX_DIRS
    for d in run_dirs[:to_remove]:
        logger.info("mlflow_disk_cache_evict", path=str(d))
        shutil.rmtree(d, ignore_errors=True)


def _resolve_artifact_local(
    mlflow: Any,
    run_id: str,
    artifact_path: str,
) -> str:
    """Return a local path to the model artifact, downloading only if needed.

    Saves downloaded artifacts under ``.cache/models/<run_id>/`` so they
    survive server restarts without re-downloading from remote tracking
    servers (saves ~30 s+ for Databricks-hosted artifacts).

    Downloads to a temp file first then renames atomically, so a partial
    download (network interruption, timeout) never leaves a corrupt file
    in the cache.
    """
    import shutil
    import tempfile
    from pathlib import Path

    cache_dir = Path.cwd() / ".cache" / "models" / run_id
    local_path = cache_dir / Path(artifact_path).name

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
    )
    tmp_dir = None
    try:
        tmp_dir = Path(tempfile.mkdtemp(prefix="haute_dl_"))
        downloaded = mlflow.artifacts.download_artifacts(
            f"runs:/{run_id}/{artifact_path}",
            dst_path=str(tmp_dir),
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
    _evict_disk_cache(cache_dir.parent)

    return str(local_path)


def clear_model_cache(run_id: str | None = None) -> int:
    """Delete cached model artifacts, returning the number of files removed.

    If *run_id* is given, only that run's cache is cleared.
    Otherwise all cached models are removed.
    """
    import shutil
    from pathlib import Path

    cache_root = Path.cwd() / ".cache" / "models"
    if not cache_root.exists():
        return 0

    removed = 0
    if run_id:
        if os.sep in run_id or "/" in run_id or ".." in run_id:
            raise ValueError(f"Invalid run_id: {run_id!r}")
        target = cache_root / run_id
        if target.exists():
            removed = sum(1 for _ in target.glob("*") if _.is_file())
            shutil.rmtree(target, ignore_errors=True)
    else:
        for d in cache_root.iterdir():
            if d.is_dir():
                removed += sum(1 for _ in d.glob("*") if _.is_file())
        shutil.rmtree(cache_root, ignore_errors=True)

    # Also evict from the in-memory LRU cache
    _model_cache.clear()
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

    last_err: BaseException | None = None
    for attempt in range(1, _LOAD_MAX_ATTEMPTS + 1):
        local_path = _resolve_artifact_local(
            mlflow_mod,
            run_id,
            artifact,
        )
        try:
            if flavor == "catboost":
                raw = _load_catboost_model(local_path, task)
                return _wrap_catboost(raw)
            return _load_rustystats_model(local_path)
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
            cached_file = Path(local_path)
            if cached_file.is_file():
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
    tracking_uri: str = "",
) -> ScoringModel:
    """Load a model from MLflow, auto-detecting CatBoost vs pyfunc.

    CatBoost models (``.cbm`` artifacts) get the optimized native loader
    with categorical feature support.  All other models are loaded via
    MLflow's pyfunc flavor.

    Cached by ``(source_type, identifier, version/artifact, task)``.

    Args:
        source_type: ``"run"`` to load from a specific run, or ``"registered"``
            to load from a registered model version.
        run_id: MLflow run ID (required when *source_type* is ``"run"``).
        artifact_path: Artifact path within the run (e.g. ``"model.cbm"``).
            If empty, auto-discovers: tries ``.cbm`` first, then pyfunc ``model/``.
        registered_model: Registered model name (required when *source_type* is
            ``"registered"``).
        version: Model version string (``"1"``, ``"2"``, or ``"latest"``).
        task: ``"regression"`` or ``"classification"`` — determines which
            CatBoost class to use for loading (ignored for pyfunc).
        tracking_uri: Override tracking URI; auto-detected if empty.

    Returns:
        A ``ScoringModel`` wrapping the loaded model with a uniform interface.
    """
    valid_tasks = ("regression", "classification")
    if task not in valid_tasks:
        raise ValueError(f"Invalid task {task!r}. Expected one of: {', '.join(valid_tasks)}")

    # Fast-path cache check using the raw inputs — avoids calling
    # resolve_mlflow_source() (which hits the MLflow tracking server)
    # on every invocation when the model is already cached.
    # For source_type="run" with a known artifact_path, the cache key
    # components are fully determined without any network call.
    if source_type == "run" and run_id and artifact_path:
        fast_key = (source_type, run_id, artifact_path, task)
        cached = _model_cache.get(fast_key)
        if cached is not None:
            logger.info("mlflow_model_cache_hit", key=str(fast_key))
            return cached

    resolved_run_id, resolved_version, mlflow_mod, client = resolve_mlflow_source(
        source_type=source_type,
        run_id=run_id,
        registered_model=registered_model,
        version=version,
        tracking_uri=tracking_uri,
    )
    resolved_artifact = artifact_path

    # Auto-discover artifact if not specified
    if not resolved_artifact:
        resolved_artifact, _flavor = _find_model_artifact(client, resolved_run_id)
    # else: detect from the artifact path extension

    # Detect flavor from artifact path
    if resolved_artifact.endswith(".cbm"):
        flavor = "catboost"
    elif resolved_artifact.endswith(".rsglm"):
        flavor = "rustystats"
    else:
        flavor = "pyfunc"

    cache_key = (source_type, resolved_run_id, resolved_version or resolved_artifact, task)

    cached = _model_cache.get(cache_key)
    if cached is not None:
        logger.info("mlflow_model_cache_hit", key=str(cache_key))
        return cached

    # Load model based on detected flavor.
    # If loading fails (corrupt/truncated cache), delete the cached file
    # and re-download exactly once before giving up.  The retry uses a
    # small exponential backoff with jitter so transient upstream hiccups
    # (tracking-server flaps) get a moment to recover — but the total
    # retry budget is bounded so persistent corruption surfaces loudly.
    if flavor in ("catboost", "rustystats"):
        scoring_model = _load_with_bounded_retry(
            mlflow_mod=mlflow_mod,
            run_id=resolved_run_id,
            artifact=resolved_artifact,
            flavor=flavor,
            task=task,
        )
    else:
        raw_model = _load_pyfunc_model(mlflow_mod, resolved_run_id, resolved_artifact)
        scoring_model = _wrap_pyfunc(raw_model)

    _model_cache.put(cache_key, scoring_model)

    logger.info(
        "mlflow_model_loaded",
        source_type=source_type,
        run_id=resolved_run_id,
        artifact=resolved_artifact,
        task=task,
        flavor=flavor,
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

    Handles null values: float32 cast for numerics (null→NaN),
    sentinel fill + Categorical cast for categorical features.

    Returns numpy array, pandas DataFrame, or Polars DataFrame depending
    on model needs:
    - RustyStats: Polars DataFrame (native Polars input)
    - CatBoost with no categoricals: numpy array (fastest)
    - CatBoost with categoricals: pandas DataFrame (CatBoost requirement)
    - Pyfunc: always pandas DataFrame
    """
    # RustyStats handles its own preprocessing — pass Polars directly
    if flavor == "rustystats":
        return df_eager.select(features) if features else df_eager

    numeric_cols = [c for c in features if c not in cat_feature_names]
    cat_cols = [c for c in features if c in cat_feature_names]
    selected = df_eager.select(features)
    if numeric_cols:
        selected = selected.with_columns([pl.col(c).cast(pl.Float32) for c in numeric_cols])
    if cat_cols:
        selected = selected.with_columns(
            [pl.col(c).fill_null("_MISSING_").cast(pl.Categorical) for c in cat_cols]
        )
    # Pyfunc always needs pandas; CatBoost can use numpy when no cats
    if flavor == "pyfunc" or cat_cols:
        return selected.to_pandas()
    return selected.to_numpy()


def _append_classification_proba(
    df: pl.DataFrame,
    scoring_model: ScoringModel,
    x_data: Any,
    output_col: str,
) -> pl.DataFrame:
    """Append a ``<output_col>_proba`` column for classification tasks.

    Handles models that return 2-D probability arrays (one column per class)
    by extracting the positive-class column (index 1).  If the model does
    not support ``predict_proba`` the DataFrame is returned unchanged.
    """
    probas = scoring_model.predict_proba(x_data)
    if probas is None:
        return df
    if probas.ndim == 2:
        probas = probas[:, 1]
    return df.with_columns(
        pl.Series(f"{output_col}_proba", np.asarray(probas).flatten()),
    )


def _score_eager(
    scoring_model: ScoringModel,
    lf: pl.LazyFrame,
    features: list[str],
    output_col: str = "prediction",
    task: str = "regression",
) -> pl.LazyFrame:
    """Collect a LazyFrame and score in-memory. Returns a LazyFrame.

    Thin delegate onto :func:`haute._model_scorer.score_frame` with
    ``batch=False`` — the unified scoring entry point owns the flavor
    dispatch and the batch/eager fork.  This symbol stays exported so
    existing call sites (dev executor, deploy scorer) and direct-patch
    tests keep working.
    """
    from haute._model_scorer import score_frame

    return score_frame(
        model=scoring_model.raw_model,
        lf=lf,
        features=features,
        cat_feature_names=scoring_model.cat_feature_names,
        flavor=scoring_model.flavor,
        task=task,
        output_col=output_col,
        batch=False,
    )
