"""TrainService — orchestrates model training, extracted from the route handler.

The route handler becomes a thin adapter that validates the HTTP request and
delegates to ``TrainService.start()``.
"""

from __future__ import annotations

import gc
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from haute._logging import get_logger
from haute._types import GraphNode, PipelineGraph
from haute.graph_utils import NodeType
from haute.modelling._algorithms import ALGORITHM_REGISTRY
from haute.modelling._split import DEFAULT_SPLIT_DICT
from haute.routes._helpers import find_typed_node
from haute.routes._job_store import JobStore
from haute.schemas import TrainRequest, TrainResponse

logger = get_logger(component="server.modelling.train")

# ── Default constants ─────────────────────────────────────────────
_DEFAULT_BORDER_COUNT = 128  # CatBoost border count for VRAM estimation
_DEFAULT_DEPTH = 6  # CatBoost tree depth for VRAM estimation

# GLM config keys live at the top level of ModellingConfig (not inside
# config.params).  Must be merged into train_params for GLMAlgorithm.
_GLM_CONFIG_KEYS: tuple[str, ...] = (
    "terms",
    "family",
    "link",
    "interactions",
    "regularization",
    "alpha",
    "l1_ratio",
    "intercept",
    "var_power",
    "offset",
    "cv_folds",
)


# Valid GLM family → link combinations.  The canonical link (used when
# the user leaves "link" empty) is listed first.
_VALID_GLM_LINKS: dict[str, tuple[str, ...]] = {
    "gaussian": ("identity", "log", "inverse"),
    "binomial": ("logit", "probit", "cloglog"),
    "poisson": ("log", "identity", "sqrt"),
    "gamma": ("inverse", "log", "identity"),
    "tweedie": ("log", "identity"),
    "inverse_gaussian": ("inverse_squared", "inverse", "log", "identity"),
}


def _validate_glm_family_link(family: str, link: str) -> None:
    """Raise HTTPException(400) if the family/link combination is invalid."""
    if not family:
        return  # will use default (gaussian)
    if family not in _VALID_GLM_LINKS:
        raise HTTPException(
            status_code=400,
            detail=(f"Unknown GLM family '{family}'. Available: {', '.join(_VALID_GLM_LINKS)}."),
        )
    if not link:
        return  # canonical link will be used
    valid = _VALID_GLM_LINKS[family]
    if link not in valid:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Link '{link}' is not valid for the {family} family. "
                f"Valid links: {', '.join(valid)}. "
                f"For a binary target like sale_flag, use family='binomial' with link='logit'."
            ),
        )


class _VramCheck:
    """Result of a GPU VRAM feasibility check."""

    __slots__ = ("estimated_mb", "available_mb", "warning")

    def __init__(
        self,
        estimated_mb: float | None = None,
        available_mb: float | None = None,
        warning: str | None = None,
    ) -> None:
        self.estimated_mb = estimated_mb
        self.available_mb = available_mb
        self.warning = warning


def _clamp_row_limit(
    current_limit: int | None,
    user_limit: object,
) -> int | None:
    """Apply a user-specified row_limit, taking the minimum with *current_limit*."""
    if user_limit and isinstance(user_limit, (int, float)):
        clamped = int(user_limit)
        if clamped > 0:
            return min(current_limit, clamped) if current_limit else clamped
    return current_limit


def _find_modelling_node(graph: PipelineGraph, node_id: str) -> GraphNode:
    """Find and validate a modelling node in the graph."""
    return find_typed_node(graph, node_id, NodeType.MODELLING, "modelling")


def _friendly_error(exc: Exception) -> str:
    """Translate common training exceptions into actionable messages."""
    msg = str(exc)

    if isinstance(exc, ValueError):
        return msg

    if isinstance(exc, FileNotFoundError):
        return f"File not found: {msg}"

    exc_type = type(exc).__name__
    if "CatBoost" in exc_type or "catboost" in msg.lower():
        if "nan" in msg.lower() or "inf" in msg.lower():
            return (
                "Training failed: the data contains NaN or infinite values. "
                "Add a polars node upstream to handle missing values "
                "(e.g. .fill_null() or .drop_nulls()) before training."
            )
        if "feature" in msg.lower() and "number" in msg.lower():
            return f"Training failed: feature mismatch. {msg}"
        return f"CatBoost error: {msg}"

    if isinstance(exc, OSError):
        return f"Could not save model file: {msg}"

    return f"Training failed ({exc_type}): {msg}"


def _check_gpu_vram(
    effective_rows: int,
    probe_columns: int,
    params: dict[str, Any],
    *,
    validation_size: float = 0.2,
    holdout_size: float = 0.0,
) -> _VramCheck:
    """Estimate GPU VRAM requirements and return a check result."""
    if effective_rows <= 0 or probe_columns <= 0:
        return _VramCheck()

    from haute._ram_estimate import available_vram_bytes, estimate_gpu_vram_bytes

    vram_needed = estimate_gpu_vram_bytes(
        effective_rows,
        probe_columns,
        border_count=params.get("border_count", _DEFAULT_BORDER_COUNT),
        depth=params.get("depth", _DEFAULT_DEPTH),
        validation_size=validation_size,
        holdout_size=holdout_size,
    )
    estimated_mb = round(vram_needed / 1024**2, 1)

    vram = available_vram_bytes()
    available_mb = round(vram / 1024**2, 1) if vram is not None else None

    warning: str | None = None
    if vram is not None and vram_needed > vram:
        warning = (
            f"GPU training needs ~{vram_needed / 1024**3:.1f} GB VRAM "
            f"but GPU has {vram / 1024**3:.1f} GB."
        )

    return _VramCheck(
        estimated_mb=estimated_mb,
        available_mb=available_mb,
        warning=warning,
    )


class TrainService:
    """Orchestrates the full training lifecycle.

    Parameters
    ----------
    store:
        The in-memory job store used to track training jobs.
    """

    def __init__(self, store: JobStore) -> None:
        self._store = store
        self._start_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def start(self, body: TrainRequest) -> TrainResponse:
        """Validate config, execute pipeline, and launch training in a background thread.

        Returns a ``TrainResponse`` with status ``"started"`` and the job ID.
        Raises ``HTTPException`` on validation or pipeline execution failures.
        """
        node = _find_modelling_node(body.graph, body.node_id)
        config = node.data.config

        self._validate_config(config)

        with self._start_lock:
            self._check_no_concurrent_jobs()
            job_id = self._store.create_job(
                {
                    "status": "running",
                    "progress": 0.0,
                    "message": "Starting",
                    "config": dict(config),
                    "node_label": node.data.label,
                }
            )

        try:
            preamble_ns = self._compile_preamble(body.graph)
            ram_warning, row_limit, total_source_rows, probe_columns = self._estimate_ram(
                body.graph,
                body.node_id,
                preamble_ns,
                job_id,
                source=body.source,
            )
            user_limit = config.get("row_limit")
            row_limit = _clamp_row_limit(row_limit, user_limit)

            # If the user's row_limit is the binding constraint, the RAM
            # downsample warning is irrelevant — suppress it.
            if (
                ram_warning
                and user_limit
                and isinstance(user_limit, (int, float))
                and int(user_limit) > 0
                and (row_limit is not None and row_limit == int(user_limit))
            ):
                ram_warning = None
                self._store.update_job(job_id, warning=None)

            train_params = {**config.get("params", {})}
            for k in _GLM_CONFIG_KEYS:
                if k in config and k not in train_params:
                    train_params[k] = config[k]

            split_cfg = config.get("split", {})
            ram_warning = self._check_gpu_fallback(
                train_params,
                row_limit,
                total_source_rows,
                probe_columns,
                ram_warning,
                job_id,
                validation_size=float(
                    split_cfg.get("validation_size", split_cfg.get("test_size", 0.2))
                ),
                holdout_size=float(split_cfg.get("holdout_size", 0.0)),
            )

            # Build the list of columns that must survive projection
            # (target, weight, offset — even if they're in the exclude list).
            excluded = config.get("exclude", [])
            keep_cols: list[str] = [config["target"]]
            if config.get("weight"):
                keep_cols.append(config["weight"])
            if config.get("offset"):
                keep_cols.append(config["offset"])

            tmp_parquet = self._execute_and_sink(
                body,
                preamble_ns,
                row_limit,
                job_id,
                exclude=excluded or None,
                keep_columns=keep_cols,
            )
        except Exception as exc:
            self._store.update_job(job_id, status="error", error=str(exc))
            raise

        # Default output_dir to <pipeline_dir>/outputs when not explicitly set.
        if "output_dir" not in config:
            from haute.executor import _pipeline_dir

            p_dir = _pipeline_dir(body.graph)
            config = {**config, "output_dir": str(p_dir / "outputs") if p_dir else "outputs"}

        self._launch_background(
            job_id,
            body.node_id,
            config,
            train_params,
            tmp_parquet,
            ram_warning,
            total_source_rows,
        )
        return TrainResponse(status="started", job_id=job_id)

    # ------------------------------------------------------------------
    # Private orchestration steps
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_config(config: dict[str, Any]) -> None:
        """Fast upfront validation — no pipeline execution yet."""
        target = config.get("target")
        if not target:
            raise HTTPException(
                status_code=400,
                detail="No target column selected."
                " Open the config panel and choose a target column.",
            )

        algorithm = config.get("algorithm", "catboost")
        if algorithm not in ALGORITHM_REGISTRY:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unknown algorithm '{algorithm}'. "
                    f"Available algorithms: {', '.join(ALGORITHM_REGISTRY.keys())}."
                ),
            )

        # GLM: validate family/link combination
        if algorithm == "glm":
            family = config.get("family", "")
            link = config.get("link", "")
            _validate_glm_family_link(family, link)

    def _check_no_concurrent_jobs(self) -> None:
        """Reject if a training job is already running."""
        self._store._evict_stale()
        running = [jid for jid, j in self._store.jobs.items() if j.get("status") == "running"]
        if running:
            raise HTTPException(
                status_code=409,
                detail="A training job is already running. Please wait for it to finish.",
            )

    @staticmethod
    def _compile_preamble(graph: PipelineGraph) -> dict[str, Any] | None:
        from haute.executor import _compile_preamble, _pipeline_dir

        return (
            _compile_preamble(
                graph.preamble or "",
                pipeline_dir=_pipeline_dir(graph),
            )
            or None
        )

    def _estimate_ram(
        self,
        graph: PipelineGraph,
        node_id: str,
        preamble_ns: dict[str, Any] | None,
        job_id: str,
        source: str = "live",
    ) -> tuple[str | None, int | None, int | None, int]:
        """Estimate safe row limit from available RAM.

        Returns (ram_warning, row_limit, total_source_rows, probe_columns).
        """
        from haute.executor import _build_node_fn

        ram_warning: str | None = None
        total_source_rows: int | None = None
        probe_columns: int = 0

        try:
            from haute._ram_estimate import estimate_safe_training_rows

            self._store.update_job(job_id, message="Estimating memory requirements")
            ram_est = estimate_safe_training_rows(
                graph,
                node_id,
                _build_node_fn,
                preamble_ns=preamble_ns,
                source=source,
            )
            row_limit = ram_est.safe_row_limit
            ram_warning = ram_est.warning
            total_source_rows = ram_est.total_rows
            probe_columns = ram_est.probe_columns
            if ram_warning:
                self._store.update_job(job_id, warning=ram_warning)
        except Exception as exc:
            logger.warning("ram_estimate_failed", error=str(exc))
            row_limit = None

        return ram_warning, row_limit, total_source_rows, probe_columns

    def _check_gpu_fallback(
        self,
        train_params: dict[str, Any],
        row_limit: int | None,
        total_source_rows: int | None,
        probe_columns: int,
        ram_warning: str | None,
        job_id: str,
        *,
        validation_size: float = 0.2,
        holdout_size: float = 0.0,
    ) -> str | None:
        """Check GPU VRAM; fall back to CPU if insufficient.

        Mutates *train_params* in-place.  Returns updated ram_warning.
        """
        if str(train_params.get("task_type", "")).upper() != "GPU":
            return ram_warning

        try:
            effective_rows = row_limit or (total_source_rows or 0)
            vram_check = _check_gpu_vram(
                effective_rows,
                probe_columns,
                train_params,
                validation_size=validation_size,
                holdout_size=holdout_size,
            )
            if vram_check.warning:
                train_params["task_type"] = "CPU"
                gpu_warning = f"{vram_check.warning} Falling back to CPU."
                logger.warning(
                    "gpu_vram_fallback",
                    estimated_mb=vram_check.estimated_mb,
                    available_mb=vram_check.available_mb,
                )
                self._store.update_job(job_id, gpu_warning=gpu_warning)
                ram_warning = f"{ram_warning}\n{gpu_warning}" if ram_warning else gpu_warning
                self._store.update_job(job_id, warning=ram_warning)
        except Exception as exc:
            logger.warning("vram_estimate_failed", error=str(exc))

        return ram_warning

    def _execute_and_sink(
        self,
        body: TrainRequest,
        preamble_ns: dict[str, Any] | None,
        row_limit: int | None,
        job_id: str,
        *,
        exclude: list[str] | None = None,
        keep_columns: list[str] | None = None,
    ) -> str:
        """Execute the pipeline lazily and sink to a temp parquet file.

        If *exclude* and *keep_columns* are provided, projects down to
        only the needed columns before sinking.  This reduces peak memory
        by dropping columns that won't be used for training.

        Returns the path to the temp parquet file.
        Raises ``HTTPException`` on failure (cleans up temp file first).
        """
        from haute._execute_lazy import _execute_lazy
        from haute.executor import _build_node_fn
        from haute.modelling._algorithms import _MEM_LOG, _mem_checkpoint

        _MEM_LOG.write_text("")
        _mem_checkpoint("train_model endpoint START")

        # Free the preview cache to reclaim memory
        from haute.executor import _preview_cache

        _preview_cache.invalidate()
        from haute.trace import _cache as _trace_cache

        _trace_cache.invalidate()
        gc.collect()
        _mem_checkpoint("cleared preview cache")

        tmp_fd, tmp_parquet = tempfile.mkstemp(suffix=".parquet", prefix="haute_train_")
        os.close(tmp_fd)

        checkpoint_dir: Path | None = None
        try:
            self._store.update_job(job_id, message="Executing pipeline")
            _mem_checkpoint("before _execute_lazy")

            checkpoint_dir = Path(tempfile.mkdtemp(prefix="haute_train_ckpt_"))
            lazy_outputs, _order, _parents, _id_to_name = _execute_lazy(
                body.graph,
                _build_node_fn,
                target_node_id=body.node_id,
                preamble_ns=preamble_ns,
                source=body.source,
                checkpoint_dir=checkpoint_dir,
            )

            target_lf = lazy_outputs.get(body.node_id)
            if target_lf is None:
                raise ValueError(
                    "No training data arrived at the modelling node. "
                    "Make sure an upstream data source is connected and producing data."
                )

            if row_limit:
                target_lf = target_lf.head(row_limit)

            # Project down to only the columns needed for training.
            # This reduces peak memory during sink and all subsequent
            # phases (split, pool construction, diagnostics).
            if exclude and keep_columns:
                import polars as pl

                all_cols = (
                    target_lf.collect_schema().names()
                    if isinstance(target_lf, pl.LazyFrame)
                    else target_lf.columns
                )
                drop_cols = [c for c in all_cols if c in exclude and c not in keep_columns]
                if drop_cols:
                    target_lf = target_lf.drop(drop_cols)
                    _mem_checkpoint(f"projected: dropped {len(drop_cols)} excluded columns")

            from haute._polars_utils import _malloc_trim, safe_sink

            _mem_checkpoint("before sink_parquet")
            safe_sink(target_lf, tmp_parquet)

            del lazy_outputs, target_lf
            gc.collect()
            _malloc_trim()
            _mem_checkpoint("sunk to temp parquet")
        except Exception as exc:
            if os.path.exists(tmp_parquet):
                os.unlink(tmp_parquet)
            error_msg = f"Pipeline execution failed: {exc}"
            logger.error("pipeline_exec_failed", error=str(exc), node_id=body.node_id)
            self._store.update_job(job_id, status="error", message=error_msg)
            raise HTTPException(
                status_code=500,
                detail="Pipeline execution failed. Check the server logs for details.",
            )
        finally:
            if checkpoint_dir and checkpoint_dir.exists():
                shutil.rmtree(checkpoint_dir, ignore_errors=True)

        return tmp_parquet

    def _launch_background(
        self,
        job_id: str,
        node_id: str,
        config: dict[str, Any],
        train_params: dict[str, Any],
        tmp_parquet: str,
        ram_warning: str | None,
        total_source_rows: int | None,
    ) -> None:
        """Build a TrainingJob and run it in a background thread."""
        from haute.modelling import TrainingJob

        target = config["target"]
        algorithm = config.get("algorithm", "catboost")
        name = config.get("name", node_id)
        split_raw = config.get("split", DEFAULT_SPLIT_DICT)

        start_time = time.monotonic()
        self._store.atomic_update(job_id, {"start_time": start_time})

        def _progress(msg: str, frac: float) -> None:
            self._store.atomic_update(
                job_id,
                {
                    "progress": frac,
                    "message": msg,
                    "elapsed_seconds": time.monotonic() - start_time,
                },
            )

        def _on_iteration(iteration: int, total: int, metrics: dict[str, float]) -> None:
            self._store.atomic_update(
                job_id,
                {
                    "iteration": iteration,
                    "total_iterations": total,
                    "train_loss": metrics,
                    "elapsed_seconds": time.monotonic() - start_time,
                },
            )

        job = TrainingJob(
            name=name,
            data=tmp_parquet,
            target=target,
            weight=config.get("weight") or None,
            exclude=config.get("exclude", []),
            algorithm=algorithm,
            task=config.get("task", "regression"),
            params=train_params,
            split=split_raw,
            metrics=config.get("metrics", ["gini", "rmse"]),
            mlflow_experiment=config.get("mlflow_experiment") or None,
            model_name=config.get("model_name") or None,
            output_dir=config.get("output_dir", "outputs"),
            loss_function=config.get("loss_function") or None,
            variance_power=(
                config.get("variance_power")
                if config.get("variance_power") is not None
                else config.get("var_power")
            ),
            offset=config.get("offset") or None,
            monotone_constraints=config.get("monotone_constraints") or None,
            feature_weights=config.get("feature_weights") or None,
            cv_folds=config.get("cv_folds"),
        )

        def _train_background() -> None:
            try:
                train_result = job.run(_progress, _on_iteration)
                response = TrainResponse(
                    status="completed",
                    job_id=job_id,
                    metrics=train_result.metrics,
                    feature_importance=train_result.feature_importance,
                    model_path=train_result.model_path,
                    train_rows=train_result.train_rows,
                    test_rows=train_result.test_rows,
                    features=train_result.features,
                    cat_features=train_result.cat_features,
                    best_iteration=train_result.best_iteration,
                    loss_history=train_result.loss_history,
                    double_lift=train_result.double_lift,
                    shap_summary=train_result.shap_summary,
                    feature_importance_loss=train_result.feature_importance_loss,
                    cv_results=train_result.cv_results,
                    ave_per_feature=train_result.ave_per_feature,
                    residuals_histogram=train_result.residuals_histogram,
                    residuals_stats=train_result.residuals_stats,
                    actual_vs_predicted=train_result.actual_vs_predicted,
                    lorenz_curve=train_result.lorenz_curve,
                    lorenz_curve_perfect=train_result.lorenz_curve_perfect,
                    pdp_data=train_result.pdp_data,
                    warning=ram_warning,
                    total_source_rows=total_source_rows,
                )
                self._store.atomic_update(
                    job_id,
                    {
                        "status": "completed",
                        "result": response,
                        "elapsed_seconds": time.monotonic() - start_time,
                    },
                )
            except ValueError as exc:
                error_msg = str(exc)
                logger.warning("training_validation_error", error=error_msg, node_id=node_id)
                self._store.atomic_update(job_id, {"status": "error", "message": error_msg})
            except Exception as exc:
                error_msg = _friendly_error(exc)
                logger.error("training_failed", error=str(exc), node_id=node_id)
                self._store.atomic_update(job_id, {"status": "error", "message": error_msg})
            finally:
                if os.path.exists(tmp_parquet):
                    os.unlink(tmp_parquet)

        try:
            thread = threading.Thread(target=_train_background, daemon=True)
            thread.start()
        except Exception:
            if os.path.exists(tmp_parquet):
                os.unlink(tmp_parquet)
            raise
