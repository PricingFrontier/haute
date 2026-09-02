"""Prepare bounded training inputs and evaluate launch feasibility.

Preparation runs in a hard-capped spawn worker: :func:`prepare_training_data`
is the in-process core, :func:`prepare_training_data_worker` is the spawn
entrypoint, and the parent supervisor lives in
``haute.routes._training_lifecycle``. Everything crossing the boundary is a
plain picklable dataclass — the child never touches a ``JobStore``.
"""

from __future__ import annotations

import gc
import os
import shutil
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import polars as pl
from fastapi import HTTPException

from haute._execution_admission import (
    ExecutionAdmissionError,
    IsolatedExecutionBudget,
    create_isolated_execution_context,
)
from haute._execution_context import (
    ExecutionContext,
    ExecutionMemoryLimitExceededError,
)
from haute._graph_utils import upstream_node_ids
from haute._logging import get_logger
from haute._types import GraphNode, PipelineGraph
from haute.errors import BoundedMemoryUnsupportedError, HauteValidationError
from haute.execution import (
    AllExceptColumns,
    build_dataframe_execution_cache_request,
    dataframe_graph_input_fingerprint,
    execute_lazy_graph,
)
from haute.graph_utils import NodeType
from haute.modelling._train_config import (
    build_train_params,
)
from haute.routes._contract_errors import (
    PUBLIC_CONTRACT_ERROR_TYPES,
    contract_error_http_exception,
    contract_error_job_fields,
)
from haute.routes._helpers import find_typed_node
from haute.routes._memory_messages import memory_limit_user_message
from haute.schemas import (
    TrainingFeatureSelectionDiagnosticPayload,
)

logger = get_logger(component="server.modelling.train")

_DEFAULT_BORDER_COUNT = 128
_DEFAULT_DEPTH = 6
_TRAINING_DOWNSAMPLE_SEED = 42


def _seeded_training_sample(lf: pl.LazyFrame, row_limit: int) -> pl.LazyFrame:
    """Uniform random sample of ``row_limit`` rows — deterministic, order-preserving.

    Replaces the previous ``head(row_limit)`` downsample, which was order-biased:
    temporally or target-ordered data trained on the oldest slice only. The
    shuffle-filter idiom samples without replacement, keeps the surviving rows
    in their original relative order, and is a no-op when ``row_limit`` is at
    or above the frame height. Only the row-index column is materialised for
    the mask, so the data columns still stream through the bounded sink.
    """
    if row_limit <= 0:
        raise HauteValidationError(f"row_limit must be positive, got {row_limit}")
    return lf.filter(
        pl.int_range(pl.len()).shuffle(seed=_TRAINING_DOWNSAMPLE_SEED) < row_limit,
    )


def _memory_limit_http_exception(
    exc: ExecutionAdmissionError | ExecutionMemoryLimitExceededError,
) -> HTTPException:
    detail = exc.to_payload()
    # str(exc) names the internal operation and raw byte counts; author the
    # public message from the structured attributes instead. Assigned
    # unconditionally: a payload-carried "message" must not win over the
    # curated wording.
    detail["message"] = memory_limit_user_message(exc, operation_noun="Training")
    return HTTPException(status_code=507, detail=detail)


def _http_failure_job_parts(
    exc: HTTPException,
    *,
    job_id: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Preserve an HTTP failure's public shape on a terminal background job."""
    detail = exc.detail
    if isinstance(detail, dict):
        detail = dict(detail)
        if job_id is not None:
            detail.setdefault("job_id", job_id)
    message = detail.get("message") if isinstance(detail, dict) else None
    message = message if isinstance(message, str) and message else str(detail)
    return message, {
        "error": message,
        "error_detail": detail,
        "error_code": detail.get("error_code") if isinstance(detail, dict) else None,
        "http_status_code": exc.status_code,
    }


def _gpu_vram_http_exception(
    *,
    warning: str,
    estimated_mb: float | None,
    available_mb: float | None,
    job_id: str,
) -> HTTPException:
    payload = {
        "error_code": "gpu_vram_limit",
        "operation": "training_job",
        "job_id": job_id,
        "message": warning,
        "gpu_vram_estimated_mb": estimated_mb,
        "gpu_vram_available_mb": available_mb,
        "reason": "gpu_vram_limit_exceeded",
    }
    return HTTPException(status_code=507, detail=payload)


# Valid GLM family → link combinations.  The canonical link (used when
# the user leaves "link" empty) is listed first.
_VALID_GLM_LINKS: dict[str, tuple[str, ...]] = {
    "gaussian": ("identity", "log", "inverse"),
    "binomial": ("logit", "probit", "cloglog"),
    "poisson": ("log", "identity", "sqrt"),
    # Quasi-Poisson estimates its dispersion from Pearson residuals (a fitted
    # scale, no user parameter), so it is safe to offer. RustyStats accepts
    # only log/identity for it — no sqrt.
    "quasipoisson": ("log", "identity"),
    # Negative Binomial's dispersion `theta` is not estimated by RustyStats —
    # an unset theta silently fits at theta=1.0 — so the training objective
    # gate (training_objective_issue) requires an explicit theta; the config
    # panel offers profile-likelihood estimation on demand. RustyStats accepts
    # only log/identity for it.
    "negbinomial": ("log", "identity"),
    "gamma": ("inverse", "log", "identity"),
    "tweedie": ("log", "identity"),
    "inverse_gaussian": ("inverse_squared", "inverse", "log", "identity"),
}


class _VramCheck:
    """Result of a GPU VRAM feasibility check.

    ``insufficient`` is True only when VRAM was actually observed and the
    estimate exceeds it — the one state that refuses a GPU launch.  An
    unknown VRAM (no GPU detected, or detection failed) sets ``warning``
    without ``insufficient``: the pre-check is advisory ahead of CatBoost's
    own device errors, so unknown warns rather than blocks.
    """

    __slots__ = ("estimated_mb", "available_mb", "warning", "insufficient")

    def __init__(
        self,
        estimated_mb: float | None = None,
        available_mb: float | None = None,
        warning: str | None = None,
        insufficient: bool = False,
    ) -> None:
        self.estimated_mb = estimated_mb
        self.available_mb = available_mb
        self.warning = warning
        self.insufficient = insufficient


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


def _glm_training_term_columns(config: dict[str, Any]) -> frozenset[str] | None:
    if str(config.get("algorithm", "catboost")).lower() != "glm":
        return None
    raw_terms = build_train_params(config).get("terms")
    if not isinstance(raw_terms, dict) or not raw_terms:
        return None
    terms = frozenset(name for name in raw_terms if isinstance(name, str) and name)
    return terms or None


def _string_list_config(config: Mapping[str, Any], key: str) -> list[str]:
    raw = config.get(key)
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise HauteValidationError(f"{key} must be a list of column names")
    columns: list[str] = []
    seen: set[str] = set()
    for value in raw:
        if not isinstance(value, str) or not value:
            raise HauteValidationError(f"{key} must contain non-empty string column names")
        if value in seen:
            continue
        columns.append(value)
        seen.add(value)
    return columns


def _training_required_metadata_columns(config: Mapping[str, Any]) -> set[str]:
    target = config.get("target")
    columns = {target} if isinstance(target, str) and target else set()
    for aux_key in ("weight", "offset", "fold_column"):
        aux_col = config.get(aux_key)
        if isinstance(aux_col, str) and aux_col:
            columns.add(aux_col)

    evaluation = config.get("evaluation")
    if isinstance(evaluation, dict):
        strategy = evaluation.get("strategy")
        evaluation_col = None
        if strategy == "temporal":
            evaluation_col = evaluation.get("date_column")
        elif strategy == "group":
            evaluation_col = evaluation.get("group_column")
        if isinstance(evaluation_col, str) and evaluation_col:
            columns.add(evaluation_col)

    columns.update(_string_list_config(config, "id_columns"))
    return columns


def _training_projection_keep_columns(config: Mapping[str, Any]) -> list[str]:
    """Return every configured column that exclusion projection must retain."""
    return sorted(
        _training_required_metadata_columns(config)
        | set(_string_list_config(config, "feature_columns"))
    )


def _training_metadata_reasons(config: Mapping[str, Any]) -> dict[str, str]:
    """Return configured non-feature columns in deterministic role precedence."""
    reasons: dict[str, str] = {}

    def add(raw_column: object, reason: str) -> None:
        if isinstance(raw_column, str) and raw_column:
            reasons.setdefault(raw_column, reason)

    add(config.get("target"), "target")
    add(config.get("weight"), "weight")
    add(config.get("offset"), "offset")
    add(config.get("fold_column"), "fold")
    for column in _string_list_config(config, "id_columns"):
        add(column, "identifier")
    evaluation = config.get("evaluation")
    if isinstance(evaluation, dict):
        strategy = evaluation.get("strategy")
        if strategy == "temporal":
            add(evaluation.get("date_column"), "evaluation")
        elif strategy == "group":
            add(evaluation.get("group_column"), "evaluation")
    return reasons


def _bounded_training_detail(items: list[Any], *, cap: int = 128) -> dict[str, Any]:
    retained = items[:cap]
    return {
        "state": "truncated" if len(items) > len(retained) else "available",
        "total_count": len(items),
        "items": retained,
    }


def _build_training_feature_selection(
    config: Mapping[str, Any],
    schema_columns: Iterable[str],
) -> TrainingFeatureSelectionDiagnosticPayload:
    """Validate and explain the final ordered training feature selection.

    This operates on schema metadata only. It is intentionally called before
    the training sink, so missing features or an empty feature set cannot
    trigger a data collection first.
    """
    schema = list(schema_columns)
    if any(not isinstance(column, str) or not column for column in schema):
        raise HauteValidationError("training schema must contain non-empty column names")
    if len(schema) != len(set(schema)):
        raise HauteValidationError("training schema contains duplicate column names")
    schema_set = set(schema)
    metadata_reasons = _training_metadata_reasons(config)
    missing_metadata = [column for column in metadata_reasons if column not in schema_set]
    if missing_metadata:
        raise HauteValidationError(
            "Training input is missing required column(s): "
            f"{missing_metadata}. Available columns: {schema}"
        )

    explicit_features = _string_list_config(config, "feature_columns")
    term_columns = _glm_training_term_columns(dict(config))
    configured_exclusions = set(_string_list_config(config, "exclude"))
    if explicit_features:
        mode = "explicit"
        missing_features = [column for column in explicit_features if column not in schema_set]
        if missing_features:
            raise HauteValidationError(
                "Configured feature column(s) not found in training data: "
                f"{missing_features}. Available columns: {schema}"
            )
        features = explicit_features
    elif term_columns is not None:
        mode = "glm_terms"
        missing_terms = sorted(term_columns - schema_set)
        if missing_terms:
            raise HauteValidationError(
                "GLM terms reference columns not found in training data: "
                f"{missing_terms}. Available columns: {schema}"
            )
        features = [column for column in schema if column in term_columns]
    else:
        mode = "all_except"
        non_features = set(metadata_reasons) | configured_exclusions
        features = [column for column in schema if column not in non_features]

    if not features:
        raise HauteValidationError(
            "No feature columns remaining after applying target, metadata, and exclusion settings."
        )

    retained_metadata = [
        {"column": column, "reason": metadata_reasons[column]}
        for column in schema
        if column in metadata_reasons
    ]
    feature_set = set(features)
    excluded: list[dict[str, str]] = []
    for column in schema:
        if column in feature_set:
            continue
        if column in metadata_reasons:
            reason = metadata_reasons[column]
        elif column in configured_exclusions:
            reason = "configured_exclusion"
        elif mode == "glm_terms":
            reason = "not_in_formula"
        else:
            reason = "not_selected"
        excluded.append({"column": column, "reason": reason})

    features_payload = _bounded_training_detail(features)
    metadata_payload = _bounded_training_detail(retained_metadata)
    excluded_payload = _bounded_training_detail(excluded)
    detail_state = (
        "truncated"
        if "truncated"
        in {
            features_payload["state"],
            metadata_payload["state"],
            excluded_payload["state"],
        }
        else "available"
    )
    return TrainingFeatureSelectionDiagnosticPayload.model_validate(
        {
            "schema_version": 1,
            "mode": mode,
            "feature_count": len(features),
            "detail_state": detail_state,
            "features": features_payload,
            "retained_metadata": metadata_payload,
            "excluded_columns": excluded_payload,
        }
    )


def _training_required_columns_by_node(
    node_id: str,
    config: dict[str, Any],
) -> dict[str, frozenset[str] | AllExceptColumns] | None:
    """Return modelling-node output demand needed by training.

    GLM with explicit terms has an exact feature contract before the target
    schema is materialised. CatBoost derives features from the target schema as
    all columns except configured non-feature columns, so it advertises an
    all-except demand rather than pretending the feature set is unknown.
    """
    term_columns = _glm_training_term_columns(config)
    target = config.get("target")
    if not isinstance(target, str) or not target:
        return None

    if term_columns is None:
        algorithm = str(config.get("algorithm", "catboost")).lower()
        if algorithm != "catboost":
            return None
        keep_columns = _training_required_metadata_columns(config)
        feature_columns = _string_list_config(config, "feature_columns")
        if feature_columns:
            return {node_id: frozenset([*feature_columns, *sorted(keep_columns)])}
        raw_exclude = _string_list_config(config, "exclude")
        exclude = {
            column
            for column in raw_exclude
            if isinstance(column, str) and column and column not in keep_columns
        }
        return {
            node_id: AllExceptColumns(
                required_columns=frozenset(keep_columns),
                excluded_columns=frozenset(keep_columns | exclude),
            )
        }

    columns = set(term_columns)
    columns.update(_training_required_metadata_columns(config))

    return {node_id: frozenset(columns)}


def _declared_categorical_levels_for_training(
    graph: PipelineGraph,
    node_id: str,
    config: dict[str, Any],
) -> dict[str, list[str | None]]:
    from haute.modelling._feature_contract import merge_categorical_level_declarations

    declarations: list[tuple[str, Any]] = [(node_id, config.get("categorical_levels"))]
    declarations.extend(
        (upstream_id, graph.node_map[upstream_id].data.config.get("categorical_levels"))
        for upstream_id in upstream_node_ids(node_id, graph.parents_of)
        if upstream_id in graph.node_map
    )
    return merge_categorical_level_declarations(declarations)


def _find_modelling_node(graph: PipelineGraph, node_id: str) -> GraphNode:
    """Find and validate a modelling node in the graph."""
    return find_typed_node(graph, node_id, NodeType.MODELLING, "modelling")


def _check_gpu_vram(
    effective_rows: int,
    probe_columns: int,
    params: dict[str, Any],
) -> _VramCheck:
    """Estimate GPU VRAM requirements and return a check result."""
    if effective_rows <= 0 or probe_columns <= 0:
        return _VramCheck()

    from haute._host_memory import available_vram_bytes
    from haute._ram_estimate import estimate_gpu_vram_bytes

    vram_needed = estimate_gpu_vram_bytes(
        effective_rows,
        probe_columns,
        border_count=params.get("border_count", _DEFAULT_BORDER_COUNT),
        depth=params.get("depth", _DEFAULT_DEPTH),
    )
    estimated_mb = round(vram_needed / 1024**2, 1)

    vram = available_vram_bytes()
    available_mb = round(vram / 1024**2, 1) if vram is not None else None

    warning: str | None = None
    insufficient = False
    if vram is None:
        warning = (
            f"GPU VRAM could not be detected (no NVIDIA GPU found, or nvidia-smi is "
            f"unavailable). GPU training needs ~{vram_needed / 1024**3:.1f} GB VRAM and "
            f"may fail on a smaller GPU."
        )
    elif vram_needed > vram:
        warning = (
            f"GPU training needs ~{vram_needed / 1024**3:.1f} GB VRAM "
            f"but GPU has {vram / 1024**3:.1f} GB."
        )
        insufficient = True

    return _VramCheck(
        estimated_mb=estimated_mb,
        available_mb=available_mb,
        warning=warning,
        insufficient=insufficient,
    )


# ---------------------------------------------------------------------------
# Hard-capped preparation worker (EXEC-P06)
# ---------------------------------------------------------------------------

TrainingPreparationTerminalReason = Literal["contract_error", "memory_limited", "error"]


@dataclass(frozen=True)
class TrainingPreparationRequest:
    """Everything the preparation child needs, as picklable plain data."""

    graph: PipelineGraph
    node_id: str
    job_id: str
    source: str
    parquet_path: str
    config: dict[str, Any]
    project_root: str
    streaming_chunk_size: int | None = None
    row_limit: int | None = None
    exclude: list[str] | None = None
    keep_columns: list[str] | None = None
    required_columns_by_node: dict[str, frozenset[str] | AllExceptColumns] | None = None
    preamble_supplied: bool = False


@dataclass(frozen=True)
class TrainingPreparationFailure:
    """An expected preparation failure, already shaped for job and HTTP."""

    terminal_reason: TrainingPreparationTerminalReason
    message: str
    http_status_code: int
    http_detail: Any
    fields: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TrainingPreparationOutcome:
    """The child's only return value — success evidence or a typed failure."""

    parquet_path: str | None = None
    feature_selection: dict[str, Any] | None = None
    execution_metrics: dict[str, Any] | None = None
    failure: TrainingPreparationFailure | None = None


def _remove_prepared_parquet(parquet_path: str) -> None:
    """Remove the prepared parquet, raising when a partial artifact survives.

    Deliberately fail-loud: a swallowed ``OSError`` would leave real training
    data on disk while the job records a failure that claims no artifact
    exists. Callers convert the removal failure into a terminal state that
    says so, keeping the original failure alongside it.
    """
    Path(parquet_path).unlink(missing_ok=True)


def _finalise_preparation_failure(
    failure: TrainingPreparationFailure,
    *,
    parquet_path: str,
    execution_metrics: dict[str, Any] | None,
) -> TrainingPreparationOutcome:
    """Remove the parquet for a failing preparation and report both outcomes.

    A successful removal returns *failure* unchanged. A failed removal is
    itself terminal: the outcome degrades to a 500 ``error`` naming the
    surviving file, while ``fields`` keeps the original ``error_detail`` and
    adds ``cleanup_error`` so the first cause is never hidden.
    """
    try:
        _remove_prepared_parquet(parquet_path)
    except OSError as cleanup_exc:
        logger.error(
            "training_preparation_temp_cleanup_failed",
            path=parquet_path,
            error=str(cleanup_exc),
        )
        message = (
            f"{failure.message}; the partial training data at {parquet_path} "
            f"could not be removed: {cleanup_exc}"
        )
        fields = dict(failure.fields)
        fields.setdefault("error_detail", failure.http_detail)
        fields["error"] = message
        fields["cleanup_error"] = str(cleanup_exc)
        fields["http_status_code"] = 500
        failure = TrainingPreparationFailure(
            terminal_reason="error",
            message=message,
            http_status_code=500,
            http_detail=message,
            fields=fields,
        )
    return TrainingPreparationOutcome(
        execution_metrics=execution_metrics,
        failure=failure,
    )


def _preparation_failure_from_http(
    exc: HTTPException,
    *,
    job_id: str,
    terminal_reason: TrainingPreparationTerminalReason | None = None,
) -> TrainingPreparationFailure:
    message, fields = _http_failure_job_parts(exc, job_id=job_id)
    if terminal_reason is None:
        if exc.status_code == 507:
            terminal_reason = "memory_limited"
        elif 400 <= exc.status_code < 500:
            terminal_reason = "contract_error"
        else:
            terminal_reason = "error"
    return TrainingPreparationFailure(
        terminal_reason=terminal_reason,
        message=message,
        http_status_code=exc.status_code,
        http_detail=fields["error_detail"],
        fields=fields,
    )


def _validate_target_task_pairing(
    tmp_parquet: str,
    config: dict[str, Any],
    *,
    execution_context: ExecutionContext,
) -> None:
    """Gate a target whose materialised values cannot serve the task/metrics.

    Runs on the sunk training parquet, after materialisation but before
    the fit worker is dispatched, so a config/data mismatch (a continuous
    target under a classification task, or under objective-implied
    AUC/log-loss defaults — e.g. a binomial family with
    ``task="regression"``) fails with the target column, task, and metrics
    named instead of surfacing a context-free library error from inside
    the child. The gate keys on the effective metric set
    (``effective_metrics`` — explicit config metrics or the
    objective-implied defaults), the same derivation
    ``build_training_job_kwargs`` uses. Removes the temp parquet before
    raising — no later owner exists for it on this path.
    """
    from haute._polars_utils import streaming_collect
    from haute.modelling._target_check import training_target_task_issue
    from haute.modelling._train_config import TrainingConfigError, effective_metrics

    # Derive the effective metrics before the data scan: it is a pure
    # config computation, and a malformed metrics config (normally caught
    # by the route's upfront validation) must map to the same
    # 422/contract_error taxonomy as the gate itself, not fall through
    # the scan-failure path below.
    try:
        metrics = effective_metrics(config)
    except TrainingConfigError as exc:
        _remove_prepared_parquet(tmp_parquet)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        issue = training_target_task_issue(
            pl.scan_parquet(tmp_parquet),
            target=str(config.get("target", "")),
            task=str(config.get("task", "regression")),
            metrics=metrics,
            collect=lambda lf: streaming_collect(lf, execution_context=execution_context),
        )
    except BaseException:
        # A failure inside the scan itself (corrupt parquet, cancellation,
        # memory pressure) must not orphan the multi-GB temp input either.
        _remove_prepared_parquet(tmp_parquet)
        raise
    if issue is not None:
        _remove_prepared_parquet(tmp_parquet)
        raise HTTPException(status_code=422, detail=issue)


def _execute_and_sink_training_frame(
    request: TrainingPreparationRequest,
    *,
    execution_context: ExecutionContext,
) -> TrainingFeatureSelectionDiagnosticPayload:
    """Materialise the projected training frame into ``request.parquet_path``."""
    from haute._polars_utils import (
        DEFAULT_STREAMING_CHUNK_SIZE,
        _malloc_trim,
        bounded_sink,
    )
    from haute.executor import _build_node_fn, _compile_preamble, _pipeline_dir, _preview_cache
    from haute.modelling._algorithms import _mem_checkpoint, _mem_log_path
    from haute.trace import _cache as _trace_cache

    graph = request.graph
    node_id = request.node_id
    tmp_parquet = request.parquet_path

    mem_log = _mem_log_path()
    mem_log.parent.mkdir(parents=True, exist_ok=True)
    mem_log.write_text("")
    _mem_checkpoint("train_model endpoint START")

    # Free the preview cache to reclaim memory
    _preview_cache.clear()
    _trace_cache.clear()
    gc.collect()
    _mem_checkpoint("cleared preview cache")

    preamble_ns = (
        _compile_preamble(graph.preamble or "", pipeline_dir=_pipeline_dir(graph)) or None
        if request.preamble_supplied
        else None
    )

    checkpoint_dir: Path | None = None
    try:
        _mem_checkpoint("before _execute_lazy")
        checkpoint_dir = Path(tempfile.mkdtemp(prefix="haute_train_ckpt_"))
        chunk_size = request.streaming_chunk_size or DEFAULT_STREAMING_CHUNK_SIZE
        dataframe_cache_request = build_dataframe_execution_cache_request(
            graph,
            node_ids=[node_id],
            namespace="training_prep",
            source=request.source,
            profile=execution_context.profile,
            input_fingerprint=dataframe_graph_input_fingerprint(
                graph,
                target_node_id=node_id,
                source=request.source,
            ),
            target_node_id=node_id,
            required_columns_by_node=request.required_columns_by_node,
            enforce_contracts=True,
            preamble_ns_supplied=preamble_ns is not None,
            streaming_chunk_size=chunk_size,
        )

        lazy_outputs, _order, _parents, _id_to_name = execute_lazy_graph(
            graph,
            _build_node_fn,
            target_node_id=node_id,
            preamble_ns=preamble_ns,
            source=request.source,
            checkpoint_dir=checkpoint_dir,
            enforce_contracts=True,
            required_columns_by_node=request.required_columns_by_node,
            execution_context=execution_context,
            dataframe_cache_request=dataframe_cache_request,
        )

        target_lf = lazy_outputs.get(node_id)
        if target_lf is None:
            raise HauteValidationError(
                "No training data arrived at the modelling node. "
                "Make sure an upstream data source is connected and producing data."
            )

        if request.row_limit:
            target_lf = _seeded_training_sample(target_lf, request.row_limit)

        schema_cols = (
            target_lf.collect_schema().names()
            if hasattr(target_lf, "collect_schema")
            else target_lf.columns
        )
        schema_set = set(schema_cols)
        try:
            feature_selection = _build_training_feature_selection(
                graph.node_map[node_id].data.config,
                schema_cols,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

        required_training_columns = set(request.keep_columns or [])
        node_demand = (
            request.required_columns_by_node.get(node_id)
            if request.required_columns_by_node is not None
            else None
        )
        if isinstance(node_demand, AllExceptColumns):
            required_training_columns.update(node_demand.required_columns)
        elif node_demand is not None:
            required_training_columns.update(str(column) for column in node_demand)
        missing_training_columns = sorted(required_training_columns - schema_set)
        if missing_training_columns:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Training input is missing required column(s): "
                    f"{missing_training_columns}. Available columns: {schema_cols}"
                ),
            )

        # Project down to only the columns needed for training.
        # This reduces peak memory during sink and all subsequent
        # phases (evaluation partitions, pool construction, diagnostics).
        if request.exclude and request.keep_columns:
            drop_cols = [
                column
                for column in schema_cols
                if column in request.exclude and column not in request.keep_columns
            ]
            if drop_cols:
                target_lf = target_lf.drop(drop_cols)
                _mem_checkpoint(f"projected: dropped {len(drop_cols)} excluded columns")

        _mem_checkpoint("before sink_parquet")
        execution_context.checkpoint(label="before_training_sink_write", node_id=node_id)
        with execution_context.stage("training_sink_write", node_id=node_id):
            bounded_sink(target_lf, tmp_parquet, streaming_chunk_size=chunk_size)
        execution_context.checkpoint(label="after_training_sink_write", node_id=node_id)

        del lazy_outputs, target_lf
        gc.collect()
        _malloc_trim()
        _mem_checkpoint("sunk to temp parquet")
        return feature_selection
    finally:
        if checkpoint_dir is not None and checkpoint_dir.exists():
            shutil.rmtree(checkpoint_dir, ignore_errors=True)


def prepare_training_data(
    request: TrainingPreparationRequest,
    *,
    execution_context: ExecutionContext,
) -> TrainingPreparationOutcome:
    """Materialise, gate, and sink one training frame — the in-process core.

    Owns the whole preparation contract inside its own process: pipeline
    execution, the feature-selection diagnostic, the required-column check,
    the bounded sink, and the target/task gate. Expected failures become a
    :class:`TrainingPreparationFailure` carrying exactly the job fields and
    HTTP shape the former in-thread path produced. Every failure removes the
    parquet, so no partial artifact ever survives.
    """
    job_id = request.job_id
    tmp_parquet = request.parquet_path
    try:
        feature_selection = _execute_and_sink_training_frame(
            request,
            execution_context=execution_context,
        )
        _validate_target_task_pairing(
            tmp_parquet,
            request.config,
            execution_context=execution_context,
        )
    except (ExecutionAdmissionError, ExecutionMemoryLimitExceededError) as exc:
        logger.warning(
            "pipeline_exec_memory_limited",
            error=str(exc),
            node_id=request.node_id,
        )
        return _finalise_preparation_failure(
            _preparation_failure_from_http(
                _memory_limit_http_exception(exc),
                job_id=job_id,
                terminal_reason="memory_limited",
            ),
            parquet_path=tmp_parquet,
            execution_metrics=execution_context.metrics_payload(),
        )
    except PUBLIC_CONTRACT_ERROR_TYPES as exc:
        http_exc = contract_error_http_exception(exc)
        return _finalise_preparation_failure(
            TrainingPreparationFailure(
                terminal_reason="contract_error",
                message=str(exc),
                http_status_code=http_exc.status_code,
                http_detail=http_exc.detail,
                fields=contract_error_job_fields(exc),
            ),
            parquet_path=tmp_parquet,
            execution_metrics=execution_context.metrics_payload(),
        )
    except BoundedMemoryUnsupportedError as exc:
        logger.warning(
            "pipeline_bounded_streaming_unsupported",
            error=str(exc),
            node_id=request.node_id,
        )
        return _finalise_preparation_failure(
            _preparation_failure_from_http(
                HTTPException(
                    status_code=422,
                    detail=f"Pipeline cannot run in bounded streaming mode: {exc}",
                ),
                job_id=job_id,
                terminal_reason="contract_error",
            ),
            parquet_path=tmp_parquet,
            execution_metrics=execution_context.metrics_payload(),
        )
    except HTTPException as exc:
        return _finalise_preparation_failure(
            _preparation_failure_from_http(exc, job_id=job_id),
            parquet_path=tmp_parquet,
            execution_metrics=execution_context.metrics_payload(),
        )
    except Exception as exc:
        logger.error("pipeline_exec_failed", error=str(exc), node_id=request.node_id)
        return _finalise_preparation_failure(
            _preparation_failure_from_http(
                HTTPException(
                    status_code=500,
                    detail="Pipeline execution failed. Check the server logs for details.",
                ),
                job_id=job_id,
                terminal_reason="error",
            ),
            parquet_path=tmp_parquet,
            execution_metrics=execution_context.metrics_payload(),
        )
    execution_context.checkpoint(label="training_preparation_complete")
    return TrainingPreparationOutcome(
        parquet_path=tmp_parquet,
        feature_selection=feature_selection.model_dump(mode="json"),
        execution_metrics=execution_context.metrics_payload(),
    )


def prepare_training_data_worker(
    request: TrainingPreparationRequest,
    budget: IsolatedExecutionBudget,
) -> TrainingPreparationOutcome:
    """Spawn entrypoint: run preparation under the child's own hard cap.

    The parent's admitted headroom is re-expressed as a worker-local context
    (no double reservation) and the spawn machinery installs the matching
    native cap, so an unavailable materialisation estimate plans
    conservatively here instead of being rejected outright.
    """
    from haute._sandbox import set_project_root

    # The spawned child starts with the interpreter's default sandbox root.
    # Carry the parent's resolved root across so path validation inside the
    # child accepts exactly the sources the request was admitted against.
    set_project_root(Path(request.project_root))
    context: ExecutionContext | None = None
    try:
        context = create_isolated_execution_context(budget)
        context.checkpoint(label="training_preparation")
        return prepare_training_data(request, execution_context=context)
    except (ExecutionAdmissionError, ExecutionMemoryLimitExceededError) as exc:
        return _finalise_preparation_failure(
            _preparation_failure_from_http(
                _memory_limit_http_exception(exc),
                job_id=request.job_id,
                terminal_reason="memory_limited",
            ),
            parquet_path=request.parquet_path,
            execution_metrics=None,
        )
    except Exception as exc:
        logger.error(
            "training_preparation_worker_error",
            error=str(exc),
            error_type=type(exc).__name__,
            node_id=request.node_id,
        )
        return _finalise_preparation_failure(
            _preparation_failure_from_http(
                HTTPException(
                    status_code=500,
                    detail="Pipeline execution failed. Check the server logs for details.",
                ),
                job_id=request.job_id,
                terminal_reason="error",
            ),
            parquet_path=request.parquet_path,
            execution_metrics=None,
        )
    finally:
        if context is not None:
            context.release_admission(preserve_primary_error=True)


def create_training_parquet_path() -> str:
    """Create the parent-owned empty parquet path the child sinks into."""
    tmp_fd, tmp_parquet = tempfile.mkstemp(suffix=".parquet", prefix="haute_train_")
    os.close(tmp_fd)
    return tmp_parquet
