"""Pydantic models for API request/response validation.

The canonical graph types (``GraphEdge``, ``NodeData``, ``GraphNode``,
``PipelineGraph``) are defined in ``haute._types`` and re-exported here
with API-friendly aliases so that FastAPI endpoint signatures stay clean.
"""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field, RootModel, field_validator

from haute._types import GraphEdge as GraphEdge  # noqa: F401
from haute._types import GraphNode as GraphNode  # noqa: F401
from haute._types import NodeData as GraphNodeData  # noqa: F401
from haute._types import PipelineGraph as Graph  # noqa: F401


def _normalise_frontier_range_pair(value: Any, *, field: str) -> tuple[float, float]:
    """Validate one ``(min, max)`` frontier-range value.

    Single source of truth for both the request-body schema layer and the
    config-side path in ``_optimiser_service``.  Accepts either a dict
    ``{"min": ..., "max": ...}`` or a 2-element list/tuple.
    """
    if isinstance(value, dict):
        raw_min = value.get("min")
        raw_max = value.get("max")
    elif isinstance(value, list | tuple) and len(value) == 2:
        raw_min, raw_max = value
    else:
        raise ValueError(f"{field} must contain min and max values.")

    if raw_min is None or raw_max is None:
        raise ValueError(f"{field} must contain min and max values.")

    min_value = float(raw_min)
    max_value = float(raw_max)
    if not math.isfinite(min_value) or not math.isfinite(max_value):
        raise ValueError(f"{field} must contain finite min and max values.")
    if min_value > max_value:
        raise ValueError(f"{field} min must be less than or equal to max.")
    return min_value, max_value


class ColumnInfo(BaseModel):
    name: str
    dtype: str


# ---------------------------------------------------------------------------
# /api/pipeline/save
# ---------------------------------------------------------------------------


class SavePipelineRequest(BaseModel):
    name: str = "main"
    description: str = ""
    graph: Graph = Field(default_factory=Graph)
    preamble: str | None = None
    preserved_blocks: list[str] = Field(default_factory=list)
    source_file: str = ""
    sources: list[str] = Field(default_factory=lambda: ["live"])
    active_source: str = "live"


class SavePipelineResponse(BaseModel):
    status: str = "saved"
    file: str
    pipeline_name: str
    # Non-fatal warnings surfaced to the UI (e.g. sanitized-name
    # collisions that dropped a node position).  An empty list means
    # "no issues" and callers can rely on truthiness for UX branches.
    warnings: list[str] = Field(default_factory=list)
    # SHA of the ledger commit this save produced, when the clone has a
    # working branch configured; None otherwise. Consumed by the toolbar
    # branch/SHA indicator — the save toast stays git-silent.
    git_sha: str | None = None


# ---------------------------------------------------------------------------
# Shared result models
# ---------------------------------------------------------------------------


class SchemaWarning(BaseModel):
    column: str
    status: str


class NodeResult(BaseModel):
    status: str
    row_count: int = 0
    column_count: int = 0
    columns: list[ColumnInfo] = Field(default_factory=list)
    available_columns: list[ColumnInfo] = Field(default_factory=list)
    preview: list[dict[str, Any]] = Field(default_factory=list)
    preview_columns: list[str] = Field(default_factory=list)
    preview_row_count: int = 0
    preview_row_limit: int | None = None
    preview_truncated: bool = False
    error: str | None = None
    error_line: int | None = None
    timing_ms: float = 0
    memory_bytes: int = 0
    schema_warnings: list[SchemaWarning] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# /api/pipeline/preview
# ---------------------------------------------------------------------------


class PreviewNodeRequest(BaseModel):
    graph: Graph
    node_id: str
    row_limit: int = Field(default=100, ge=1, le=10000)
    source: str = "live"
    requested_preview_columns: list[str] | None = Field(default=None, min_length=1)


class NodeTimingInfo(BaseModel):
    node_id: str
    label: str
    timing_ms: float


class NodeMemoryInfo(BaseModel):
    node_id: str
    label: str
    memory_bytes: int


class PreviewNodeResponse(NodeResult):
    """Full preview response — extends ``NodeResult`` with graph-wide metadata.

    Inherits all per-node fields (status, row_count, columns, preview, etc.)
    and adds ``node_id``, ``timings``, ``memory``, and ``node_statuses`` for
    the full graph context.
    """

    node_id: str
    timings: list[NodeTimingInfo] = Field(default_factory=list)
    memory: list[NodeMemoryInfo] = Field(default_factory=list)
    node_statuses: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# /api/pipeline/trace
# ---------------------------------------------------------------------------


class TraceRequest(BaseModel):
    graph: Graph
    row_index: int = Field(default=0, ge=0)
    target_node_id: str | None = None
    column: str | None = None
    row_limit: int = Field(default=100, ge=1, le=10000)
    source: str = "live"
    row_values: dict[str, Any] | None = None


class SchemaDiffResponse(BaseModel):
    columns_added: list[str] = Field(default_factory=list)
    columns_removed: list[str] = Field(default_factory=list)
    columns_modified: list[str] = Field(default_factory=list)
    columns_passed: list[str] = Field(default_factory=list)


class TraceStepResponse(BaseModel):
    node_id: str
    node_name: str
    node_type: str
    schema_diff: SchemaDiffResponse
    input_values: dict[str, Any] = Field(default_factory=dict)
    output_values: dict[str, Any] = Field(default_factory=dict)
    column_relevant: bool = True
    execution_ms: float = 0.0
    expression: dict[str, Any] | None = None
    calculation: dict[str, Any] | None = None
    node_detail: dict[str, Any] | None = None
    row_lineage_type: str | None = None


class TraceResultResponse(BaseModel):
    target_node_id: str
    row_index: int
    column: str | None = None
    output_value: Any = None
    steps: list[TraceStepResponse] = Field(default_factory=list)
    row_id_column: str | None = None
    row_id_value: Any = None
    total_nodes_in_pipeline: int = 0
    nodes_in_trace: int = 0
    execution_ms: float = 0.0
    waterfall: list[dict[str, Any]] | dict[str, Any] | None = None


class TraceResponse(BaseModel):
    status: str
    trace: TraceResultResponse


# ---------------------------------------------------------------------------
# /api/pipeline/sink
# ---------------------------------------------------------------------------


class SinkRequest(BaseModel):
    graph: Graph
    node_id: str
    source: str = "live"


class SinkResponse(BaseModel):
    status: str
    message: str = ""
    row_count: int = 0
    path: str = ""
    format: str = "parquet"


# ---------------------------------------------------------------------------
# /api/files
# ---------------------------------------------------------------------------


class FileItem(BaseModel):
    name: str
    path: str
    type: str
    size: int | None = None


class BrowseFilesResponse(BaseModel):
    dir: str
    items: list[FileItem]


# ---------------------------------------------------------------------------
# /api/schema
# ---------------------------------------------------------------------------


class SchemaResponse(BaseModel):
    path: str
    columns: list[ColumnInfo]
    row_count: int | None = None
    row_count_estimated: bool = False
    column_count: int
    preview: list[dict[str, Any]] = Field(default_factory=list)


class ReadJsonRequest(BaseModel):
    path: str


class ReadJsonResponse(RootModel[dict[str, Any]]):
    """Raw JSON object payload read from disk."""


# ---------------------------------------------------------------------------
# /api/pipelines (list)
# ---------------------------------------------------------------------------


class PipelineSummary(BaseModel):
    name: str
    description: str = ""
    file: str
    node_count: int = 0
    error: str | None = None


# ---------------------------------------------------------------------------
# /api/databricks/*
# ---------------------------------------------------------------------------


class WarehouseItem(BaseModel):
    id: str
    name: str
    http_path: str
    state: str
    size: str = ""


class WarehouseListResponse(BaseModel):
    warehouses: list[WarehouseItem]


class CatalogItem(BaseModel):
    name: str
    comment: str = ""


class CatalogListResponse(BaseModel):
    catalogs: list[CatalogItem]


class SchemaItem(BaseModel):
    name: str
    comment: str = ""


class SchemaListResponse(BaseModel):
    schemas: list[SchemaItem]


class TableItem(BaseModel):
    name: str
    full_name: str
    table_type: str = ""
    comment: str = ""


class TableListResponse(BaseModel):
    tables: list[TableItem]


class FetchTableRequest(BaseModel):
    table: str
    http_path: str | None = None
    query: str | None = None


class FetchTableResponse(BaseModel):
    path: str
    table: str
    row_count: int
    column_count: int
    columns: dict[str, str]
    size_bytes: int
    fetched_at: float
    fetch_seconds: float


class FetchProgressResponse(BaseModel):
    active: bool
    rows: int = 0
    batches: int = 0
    elapsed: float = 0.0


class CacheStatusResponse(BaseModel):
    cached: bool
    path: str | None = None
    table: str = ""
    row_count: int = 0
    column_count: int = 0
    columns: dict[str, str] = Field(default_factory=dict)
    size_bytes: int = 0
    fetched_at: float = 0


# ---------------------------------------------------------------------------
# /api/json-cache/*
# ---------------------------------------------------------------------------


class JsonCacheBuildRequest(BaseModel):
    path: str
    config_path: str | None = None
    flatten_schema: dict[str, Any] | None = None


class JsonCacheBuildResponse(BaseModel):
    path: str
    data_path: str
    row_count: int
    column_count: int
    columns: dict[str, str]
    size_bytes: int
    cached_at: float
    cache_seconds: float


class JsonCacheCancelResponse(BaseModel):
    cancelled: bool
    data_path: str


class JsonCacheProgressResponse(BaseModel):
    active: bool
    rows: int = 0
    elapsed: float = 0.0
    phase: str = ""


class JsonCacheStatusResponse(BaseModel):
    cached: bool
    path: str | None = None
    data_path: str = ""
    row_count: int = 0
    column_count: int = 0
    columns: dict[str, str] = Field(default_factory=dict)
    size_bytes: int = 0
    cached_at: float = 0


# ---------------------------------------------------------------------------
# /api/utility
# ---------------------------------------------------------------------------


class UtilityFileItem(BaseModel):
    name: str
    module: str  # e.g. "features" (stem, no .py)


class UtilityListResponse(BaseModel):
    files: list[UtilityFileItem]


class UtilityReadResponse(BaseModel):
    name: str
    module: str
    content: str


class UtilityWriteRequest(BaseModel):
    content: str


class UtilityCreateRequest(BaseModel):
    # Pattern validation lives in ``_validate_module_name`` so bad names
    # surface as a 400 with a flat string ``detail`` rather than the
    # structured-list body that Pydantic ``Field(pattern=)`` would produce
    # via FastAPI's 422 handler.
    name: str  # filename without .py extension
    content: str = ""


class UtilityWriteResponse(BaseModel):
    status: str = "ok"
    name: str = ""
    module: str = ""
    import_line: str = ""  # e.g. "from utility.features import *"
    error: str | None = None
    error_line: int | None = None


class UtilityDeleteResponse(BaseModel):
    status: str = "ok"
    module: str


# ---------------------------------------------------------------------------
# /api/submodel/*
# ---------------------------------------------------------------------------


class CreateSubmodelRequest(BaseModel):
    name: str
    node_ids: list[str]
    graph: Graph
    preamble: str = ""
    source_file: str = ""
    pipeline_name: str = "main"
    pipeline_description: str | None = None


class CreateSubmodelResponse(BaseModel):
    status: str = "ok"
    submodel_file: str = ""
    parent_file: str = ""
    graph: Graph = Field(default_factory=Graph)


class DissolveSubmodelRequest(BaseModel):
    submodel_name: str
    graph: Graph
    preamble: str = ""
    source_file: str = ""
    pipeline_name: str = "main"
    pipeline_description: str | None = None


class DissolveSubmodelResponse(BaseModel):
    status: str = "ok"
    graph: Graph = Field(default_factory=Graph)


class SubmodelGraphResponse(BaseModel):
    status: str = "ok"
    submodel_name: str = ""
    graph: Graph = Field(default_factory=Graph)


# ---------------------------------------------------------------------------
# /api/modelling/*
# ---------------------------------------------------------------------------


class TrainRequest(BaseModel):
    graph: Graph
    node_id: str
    source: str = "live"


class TrainResponse(BaseModel):
    status: Literal["started", "completed", "error"]
    job_id: str | None = None
    metrics: dict[str, float] = Field(default_factory=dict)
    feature_importance: list[dict[str, Any]] = Field(default_factory=list)
    model_path: str = ""
    train_rows: int = 0
    test_rows: int = 0  # validation rows
    holdout_rows: int = 0
    holdout_metrics: dict[str, float] = Field(default_factory=dict)
    diagnostics_set: str = "validation"  # "train" | "validation" | "holdout"
    features: list[str] = Field(default_factory=list)
    cat_features: list[str] = Field(default_factory=list)
    error: str | None = None
    best_iteration: int | None = None
    loss_history: list[dict[str, float]] = Field(default_factory=list)
    double_lift: list[dict[str, Any]] = Field(default_factory=list)
    shap_summary: list[dict[str, Any]] = Field(default_factory=list)
    feature_importance_loss: list[dict[str, Any]] = Field(default_factory=list)
    ave_per_feature: list[dict[str, Any]] = Field(default_factory=list)
    residuals_histogram: list[dict[str, Any]] = Field(default_factory=list)
    residuals_stats: dict[str, float] = Field(default_factory=dict)
    actual_vs_predicted: list[dict[str, float]] = Field(default_factory=list)
    lorenz_curve: list[dict[str, float]] = Field(default_factory=list)
    lorenz_curve_perfect: list[dict[str, float]] = Field(default_factory=list)
    pdp_data: list[dict[str, Any]] = Field(default_factory=list)
    glm_coefficients: list[dict[str, Any]] = Field(default_factory=list)
    glm_relativities: list[dict[str, Any]] = Field(default_factory=list)
    glm_fit_statistics: dict[str, float] = Field(default_factory=dict)
    glm_regularization_path: dict[str, Any] | None = None
    diagnostics_errors: list[dict[str, str]] = Field(default_factory=list)
    warning: str | None = None
    total_source_rows: int | None = None


class TrainStatusResponse(BaseModel):
    status: Literal["running", "completed", "error"]
    progress: float = 0.0
    message: str = ""
    iteration: int = 0
    total_iterations: int = 0
    train_loss: dict[str, float] = Field(default_factory=dict)
    elapsed_seconds: float = 0.0
    result: TrainResponse | None = None
    warning: str | None = None


class TrainEstimateRequest(BaseModel):
    graph: Graph
    node_id: str
    source: str = "live"


class TrainEstimateResponse(BaseModel):
    total_rows: int | None = None
    safe_row_limit: int | None = None
    estimated_mb: float = 0.0
    training_mb: float = 0.0
    available_mb: float = 0.0
    bytes_per_row: float = 0.0
    was_downsampled: bool = False
    warning: str | None = None
    # GPU VRAM estimation
    gpu_vram_estimated_mb: float | None = None
    gpu_vram_available_mb: float | None = None
    gpu_warning: str | None = None


class ExportScriptRequest(BaseModel):
    node_id: str
    graph: Graph
    data_path: str = ""


class ExportScriptResponse(BaseModel):
    script: str
    filename: str


class LogExperimentRequest(BaseModel):
    job_id: str
    experiment_name: str | None = None
    model_name: str | None = None


class MlflowLogResponse(BaseModel):
    """Shared base for MLflow experiment-logging responses.

    Used by both training (``LogExperimentResponse``) and optimisation
    (``OptimiserMlflowLogResponse``) to avoid duplicating the identical
    seven fields.
    """

    status: Literal["ok", "error"]
    backend: str = ""
    experiment_name: str = ""
    run_id: str | None = None
    run_url: str | None = None
    tracking_uri: str = ""
    error: str | None = None


class LogExperimentResponse(MlflowLogResponse):
    pass


class MlflowCheckResponse(BaseModel):
    mlflow_installed: bool
    mlflow_importable: bool = False
    tracking_configured: bool = False
    backend: str = ""
    databricks_host: str = ""
    detail: str = ""


class ModelCacheClearResponse(BaseModel):
    removed: int
    run_id: str | None = None


# ---------------------------------------------------------------------------
# /api/mlflow/* (discovery for Model Score node)
# ---------------------------------------------------------------------------


class MlflowExperimentSummary(BaseModel):
    experiment_id: str
    name: str


class MlflowRunSummary(BaseModel):
    run_id: str
    run_name: str
    status: str
    start_time: int | None = None
    metrics: dict[str, float] = Field(default_factory=dict)
    params: dict[str, str] = Field(default_factory=dict)
    artifacts: list[str] = Field(default_factory=list)


class MlflowVersionBrief(BaseModel):
    version: str
    status: str
    run_id: str


class MlflowModelSummary(BaseModel):
    name: str
    latest_versions: list[MlflowVersionBrief] = Field(default_factory=list)


class MlflowModelVersionSummary(BaseModel):
    version: str
    run_id: str
    status: str
    creation_timestamp: int | None = None
    description: str = ""
    params: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# /api/optimiser/*
# ---------------------------------------------------------------------------


class OptimiserSolveRequest(BaseModel):
    graph: Graph
    node_id: str


class OptimiserSolveResponse(BaseModel):
    status: Literal["started", "error"]
    job_id: str | None = None
    error: str | None = None


class OptimiserEstimateRequest(BaseModel):
    """Body for the lightweight optimiser-cost estimate.

    Used by the frontend to preview source size / RAM availability before
    kicking off a solve.  Symmetric with :class:`TrainEstimateRequest`
    except that the pre-flight for the optimiser only needs row and column
    counts from ancestor data sources — there's no fitting phase to size.
    """

    graph: Graph
    node_id: str
    source: str = "live"


class OptimiserEstimateResponse(BaseModel):
    """Result shape for ``POST /api/optimiser/estimate``."""

    total_rows: int | None = None
    """Max row count across ancestor data sources, if readable."""
    quote_count: int | None = None
    """Distinct quotes in the optimiser input after scenario expansion."""
    scenarios_per_quote_min: int | None = None
    """Minimum scenario rows per quote in the optimiser input."""
    scenarios_per_quote_max: int | None = None
    """Maximum scenario rows per quote in the optimiser input."""
    scenarios_per_quote_mean: float | None = None
    """Mean scenario rows per quote in the optimiser input."""
    expanded_row_count: int | None = None
    """Total rows in the optimiser input after scenario expansion."""


class OptimiserFrontierAutoRangeRequest(BaseModel):
    graph: Graph
    node_id: str


class OptimiserFrontierRange(BaseModel):
    min: float
    max: float


class OptimiserFrontierAutoRangeResponse(BaseModel):
    status: str = "ok"
    ranges: dict[str, OptimiserFrontierRange] = Field(default_factory=dict)
    method: str = "scenario_envelope"
    warning: str | None = None


class OptimiserFrontierRequest(BaseModel):
    job_id: str
    threshold_ranges: dict[str, list[float]] = Field(default_factory=dict)
    n_points_per_dim: int = Field(default=5, ge=1, le=100)

    @field_validator("threshold_ranges", mode="after")
    @classmethod
    def _validate_threshold_ranges(
        cls,
        value: dict[str, list[float]],
    ) -> dict[str, list[float]]:
        for name, range_value in value.items():
            # Re-use the canonical validator so request-body and config-side
            # error messages match.  We discard the normalised tuple — the
            # field type stays as ``list[float]`` for JSON-payload simplicity.
            _normalise_frontier_range_pair(
                range_value,
                field=f"threshold_ranges.{name}",
            )
        return value


class OptimiserFrontierResponse(BaseModel):
    status: str
    points: list[dict[str, Any]] = Field(default_factory=list)
    n_points: int = 0
    points_returned: int = 0
    constraint_names: list[str] = Field(default_factory=list)
    points_limit: int | None = None
    points_truncated: bool = False


class OptimiserHistoryEntry(BaseModel):
    iteration: int
    total_objective: float
    max_lambda_change: float
    all_constraints_satisfied: bool | None = None
    lambdas: dict[str, float] = Field(default_factory=dict)
    total_constraints: dict[str, float] = Field(default_factory=dict)


class OptimiserScenarioValueStats(BaseModel):
    mean: float
    std: float
    min: float
    max: float
    p5: float
    p25: float
    p50: float
    p75: float
    p95: float
    pct_increase: float
    pct_decrease: float


class OptimiserScenarioValueHistogram(BaseModel):
    counts: list[int] = Field(default_factory=list)
    edges: list[float] = Field(default_factory=list)


class OptimiserSolveResult(BaseModel):
    mode: str | None = None
    total_objective: float
    baseline_objective: float
    constraints: dict[str, float] = Field(default_factory=dict)
    baseline_constraints: dict[str, float] = Field(default_factory=dict)
    lambdas: dict[str, float] = Field(default_factory=dict)
    converged: bool
    iterations: int | None = None
    n_quotes: int | None = None
    n_steps: int | None = None
    cd_iterations: int | None = None
    factor_tables: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    history: list[OptimiserHistoryEntry] | None = None
    warning: str | None = None
    scenario_value_stats: OptimiserScenarioValueStats | None = None
    scenario_value_histogram: OptimiserScenarioValueHistogram | None = None
    clamp_rate: float | None = None
    frontier: OptimiserFrontierResponse | None = None
    frontier_error: str | None = None
    selected_frontier_point: int | None = None


class OptimiserStatusResponse(BaseModel):
    status: Literal["running", "completed", "error"]
    progress: float = 0.0
    message: str = ""
    elapsed_seconds: float = 0.0
    result: OptimiserSolveResult | None = None
    frontier: OptimiserFrontierResponse | None = None


class OptimiserApplyRequest(BaseModel):
    job_id: str
    point_index: int | None = Field(default=None, ge=0)


class OptimiserApplyResponse(BaseModel):
    status: str
    total_objective: float = 0.0
    constraints: dict[str, float] = Field(default_factory=dict)
    from_artifact: bool = False
    preview: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    preview_row_count: int = 0
    preview_row_limit: int | None = None
    preview_truncated: bool = False
    error: str | None = None


class OptimiserFrontierSelectRequest(BaseModel):
    job_id: str
    point_index: int | None = Field(..., ge=0)
    include_ratebook_tables: bool = False


class OptimiserFrontierSelectResponse(BaseModel):
    status: str
    point_index: int | None = None
    total_objective: float = 0.0
    constraints: dict[str, float] = Field(default_factory=dict)
    baseline_objective: float = 0.0
    baseline_constraints: dict[str, float] = Field(default_factory=dict)
    lambdas: dict[str, float] = Field(default_factory=dict)
    converged: bool = True
    iterations: int | None = None
    cd_iterations: int | None = None
    factor_tables: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    history: list[OptimiserHistoryEntry] | None = None
    warning: str | None = None
    scenario_value_stats: OptimiserScenarioValueStats | None = None
    scenario_value_histogram: OptimiserScenarioValueHistogram | None = None
    clamp_rate: float | None = None
    error: str | None = None


class OptimiserSaveRequest(BaseModel):
    job_id: str
    output_path: str
    version: str = ""  # optional user-specified version label; auto-generated if empty
    point_index: int | None = Field(default=None, ge=0)


class OptimiserSaveResponse(BaseModel):
    status: str
    path: str | None = None
    message: str = ""


class OptimiserMlflowLogRequest(BaseModel):
    job_id: str
    point_index: int | None = Field(default=None, ge=0)
    experiment_name: str | None = None
    model_name: str | None = None


class OptimiserMlflowLogResponse(MlflowLogResponse):
    pass


# ---------------------------------------------------------------------------
# /api/git/*
# ---------------------------------------------------------------------------


class GitStatusResponse(BaseModel):
    branch: str
    is_main: bool
    is_read_only: bool
    changed_files: list[str] = Field(default_factory=list)
    main_ahead: bool = False
    main_ahead_by: int = 0
    main_last_updated: str | None = None


# ---------------------------------------------------------------------------
# Working-branch selection (P2): the per-clone working-branch association and
# the readiness signal the startup flow + toolbar indicator consume.
# ---------------------------------------------------------------------------


class GitWorkingBranchResponse(BaseModel):
    # The branch recorded against this clone in .haute/state.json, or None.
    working_branch: str | None = None
    # Drives whether the startup modal fires (S27) and which variant (S14).
    state: Literal["ready", "unset", "invalid", "divergent"] = "unset"
    # Human-readable reasons when state is "invalid" (check_invariants output
    # or eligibility failure).
    errors: list[str] = Field(default_factory=list)
    # HEAD's current branch ("HEAD" when detached). For the divergence message.
    current_branch: str
    # Short SHA of the ledger tip (or working tip pre-spawn) — feeds the
    # toolbar indicator. None when neither ref exists yet.
    last_save_sha: str | None = None
    # Branches the user may choose as a working branch (not protected, not a
    # ledger, not archived).
    eligible_branches: list[str] = Field(default_factory=list)
    # Git commit identity — when unset, the modal prompts for it (question 3).
    identity_set: bool = True
    user_name: str | None = None
    user_email: str | None = None


class GitSetWorkingBranchRequest(BaseModel):
    branch: str
    # Create the branch off current HEAD before adopting it.
    create: bool = False


class GitSetWorkingBranchResponse(BaseModel):
    working_branch: str
    state: Literal["ready", "unset", "invalid", "divergent"]
    last_save_sha: str | None = None


class GitSetIdentityRequest(BaseModel):
    user_name: str
    user_email: str
    # Write to the global git config rather than this repo's local config.
    set_global: bool = False


class GitSetIdentityResponse(BaseModel):
    user_name: str
    user_email: str
    scope: Literal["local", "global"]


# ---------------------------------------------------------------------------
# Save & commit (P3): milestone merge of the ledger onto the working branch,
# and the working branch's milestone history.
# ---------------------------------------------------------------------------


class GitCommitRequest(BaseModel):
    # User-supplied milestone message (rides the merge commit, S18).
    message: str
    # Optional version label → annotated git tag on the milestone (S18).
    version_label: str | None = None


class GitCommitResponse(BaseModel):
    sha: str
    short_sha: str
    working_branch: str
    version_label: str | None = None


class GitMilestoneEntry(BaseModel):
    sha: str
    short_sha: str
    message: str
    timestamp: str
    version_label: str | None = None


class GitMilestonesResponse(BaseModel):
    working_branch: str | None = None
    entries: list[GitMilestoneEntry] = Field(default_factory=list)


class GitFileChange(BaseModel):
    # Rename-aware (`-M`) per-file change in a ledger save.
    # status: single git status letter — M/A/D/R/C/T. old_path is set for R/C.
    status: str
    path: str
    old_path: str | None = None


class GitLedgerSave(BaseModel):
    sha: str
    short_sha: str
    message: str
    timestamp: str
    files: list[GitFileChange] = Field(default_factory=list)


class GitLedgerSavesResponse(BaseModel):
    # The ledger saves folded into one milestone (its second-parent run), or the
    # pending saves on the ledger ahead of the working tip (next-milestone preview).
    saves: list[GitLedgerSave] = Field(default_factory=list)


class GitBranchItem(BaseModel):
    name: str
    is_yours: bool
    is_current: bool
    is_archived: bool
    last_commit_time: str = ""
    commit_count: int = 0


class GitBranchListResponse(BaseModel):
    current: str
    branches: list[GitBranchItem] = Field(default_factory=list)


class GitManagedBranch(BaseModel):
    # A working branch as the branch manager sees it (its ledger is implicit).
    name: str
    is_current: bool
    is_archived: bool
    has_unmerged_saves: bool
    # True only for the current branch when the working tree has tracked,
    # uncommitted changes — archive/delete would have to switch away and can't.
    has_uncommitted_changes: bool = False
    # The commit this branch was spawned from (if recorded + still reachable), so
    # the history view can back-link that commit to this branch (S38).
    forked_from: str | None = None


class GitWorkingBranchesResponse(BaseModel):
    current: str | None = None
    branches: list[GitManagedBranch] = Field(default_factory=list)


class GitRestoreRequest(BaseModel):
    branch: str


class GitRestoreResponse(BaseModel):
    restored_as: str


class GitCreateWorkingBranchRequest(BaseModel):
    # New working-branch name.
    name: str
    # Fork point: a milestone sha, or a pending-save sha (crystallized into an
    # anchoring milestone). None → the current branch's latest milestone (S38).
    at: str | None = None
    # Relocate the work after the fork point onto the new branch and switch to
    # it, rewinding the current branch (vs. spinning off a parallel line).
    move: bool = False


class GitCreateWorkingBranchResponse(BaseModel):
    working_branch: str
    # Whether in-progress work was relocated onto the new branch.
    moved: bool
    # Whether HEAD now sits on the new branch (the client reloads when so).
    switched: bool
    last_save_sha: str | None = None


class GitPrefs(BaseModel):
    # Per-clone UI preferences (the "whole local environment" scope). Used for
    # both the GET response and the POST body.
    skip_switch_confirm: bool = False


class GitCreateBranchRequest(BaseModel):
    description: str


class GitCreateBranchResponse(BaseModel):
    branch: str


class GitSwitchBranchRequest(BaseModel):
    branch: str


class GitSwitchBranchResponse(BaseModel):
    status: str = "ok"
    branch: str


class GitSaveResponse(BaseModel):
    commit_sha: str
    message: str
    timestamp: str


class GitSubmitResponse(BaseModel):
    compare_url: str | None = None
    branch: str


class GitHistoryEntry(BaseModel):
    sha: str
    short_sha: str
    message: str
    timestamp: str
    files_changed: list[str] = Field(default_factory=list)


class GitHistoryResponse(BaseModel):
    entries: list[GitHistoryEntry] = Field(default_factory=list)


class GitRevertRequest(BaseModel):
    sha: str


class GitRevertResponse(BaseModel):
    backup_tag: str
    reverted_to: str


class GitPullResponse(BaseModel):
    success: bool
    conflict: bool = False
    conflict_message: str | None = None
    commits_pulled: int = 0


class GitArchiveRequest(BaseModel):
    branch: str


class GitArchiveResponse(BaseModel):
    archived_as: str


class GitDeleteBranchRequest(BaseModel):
    branch: str
    # Override the unmerged-ledger-saves refusal (S32: loss is real on delete).
    confirm: bool = False


class GitDeleteBranchResponse(BaseModel):
    status: str = "ok"
    branch: str
