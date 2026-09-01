"""Prepare bounded training inputs and evaluate launch feasibility."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import polars as pl
from fastapi import HTTPException

from haute._execution_admission import (
    ExecutionAdmissionError,
)
from haute._execution_context import (
    ExecutionMemoryLimitExceededError,
)
from haute._graph_utils import upstream_node_ids
from haute._logging import get_logger
from haute._types import GraphNode, PipelineGraph
from haute.errors import HauteValidationError
from haute.execution import (
    AllExceptColumns,
)
from haute.graph_utils import NodeType
from haute.modelling._train_config import (
    build_train_params,
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
