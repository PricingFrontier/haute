"""Pydantic models for API request/response validation.

The canonical graph types (``GraphEdge``, ``NodeData``, ``GraphNode``,
``PipelineGraph``) are defined in ``haute._types`` and re-exported here
with API-friendly aliases so that FastAPI endpoint signatures stay clean.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    RootModel,
    field_validator,
    model_validator,
)

from haute._types import GraphEdge as GraphEdge  # noqa: F401
from haute._types import GraphNode as GraphNode  # noqa: F401
from haute._types import NodeData as GraphNodeData  # noqa: F401
from haute._types import PipelineGraph as Graph  # noqa: F401


def _reject_bool_chunk_size(value: object) -> object:
    if isinstance(value, bool):
        raise ValueError("streaming_chunk_size must not be a bool")
    return value


StreamingChunkSize = Annotated[
    int | None,
    BeforeValidator(_reject_bool_chunk_size),
    Field(ge=1, le=10_000_000),
]

JobStatus = Literal[
    "running",
    "completed",
    "error",
    "cancelled",
    "superseded",
    "timed_out",
    "memory_limited",
    "contract_error",
]


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


class SessionStatusResponse(BaseModel):
    ok: bool = True


# ---------------------------------------------------------------------------
# Assistant HTTP request/response models
# ---------------------------------------------------------------------------


class AssistantStatusResponse(BaseModel):
    configured: bool
    reason: str | None
    provider: str | None
    model: str | None
    mutations_enabled: bool
    mutations_reason: str | None


class AssistantSessionRequest(BaseModel):
    pipeline: str | None = None
    # A previously issued session id the client wants to resume. Resume is an
    # offer: unknown/pruned ids or a different pipeline yield a fresh session.
    session_id: str | None = None


class AssistantTranscriptEntry(BaseModel):
    """One rehydratable transcript item from a resumed session's history."""

    kind: Literal["user", "assistant", "tool"]
    text: str = ""
    name: str = ""
    summary: str = ""
    is_error: bool = False


class AssistantSessionResponse(BaseModel):
    session_id: str
    # Non-empty only when the requested session was resumed: the stored turns
    # mapped to transcript entries for the panel to rehydrate.
    history: list[AssistantTranscriptEntry] = []


class AssistantMessageRequest(BaseModel):
    session_id: str
    message: str


# ---------------------------------------------------------------------------
# Assistant stream contract
# ---------------------------------------------------------------------------


class AssistantUsage(BaseModel):
    input_tokens: int
    output_tokens: int


class AssistantTextDeltaEvent(BaseModel):
    type: Literal["text_delta"] = "text_delta"
    text: str


class AssistantToolStartedEvent(BaseModel):
    type: Literal["tool_started"] = "tool_started"
    id: str
    name: str
    # Compact rendering of the call's arguments for the chat activity row.
    summary: str = ""


class AssistantToolFinishedEvent(BaseModel):
    type: Literal["tool_finished"] = "tool_finished"
    id: str
    name: str
    is_error: bool
    # Compact rendering of the result (or the error message) for the row.
    summary: str = ""


class AssistantGraphUpdatedEvent(BaseModel):
    type: Literal["graph_updated"] = "graph_updated"
    fingerprint: str


class AssistantCompletedEvent(BaseModel):
    type: Literal["completed"] = "completed"
    usage: AssistantUsage


class AssistantFailedEvent(BaseModel):
    type: Literal["failed"] = "failed"
    message: str


class AssistantCancelledEvent(BaseModel):
    type: Literal["cancelled"] = "cancelled"


AssistantStreamEvent = Annotated[
    AssistantTextDeltaEvent
    | AssistantToolStartedEvent
    | AssistantToolFinishedEvent
    | AssistantGraphUpdatedEvent
    | AssistantCompletedEvent
    | AssistantFailedEvent
    | AssistantCancelledEvent,
    Field(discriminator="type"),
]


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


DiagnosticCollectionState = Literal["available", "unavailable", "truncated"]


class ExecutionStrategyBoundaryPayload(BaseModel):
    topological_rank: int = Field(ge=0)
    node_id: str
    operator: str
    boundary_kind: Literal[
        "unprojected-streaming-boundary",
        "materialisation-boundary",
    ]


class ExecutionStrategyReasonPayload(BaseModel):
    reason_code: str
    topological_rank: int | None = Field(default=None, ge=0)
    node_id: str | None = None
    operator: str | None = None
    message: str | None = Field(default=None, max_length=512)
    parent_node_id: str | None = None


class ExecutionStrategyProvenancePayload(BaseModel):
    column: str
    origin_kind: Literal[
        "seed",
        "contract",
        "expression",
        "join_key",
        "conservative_boundary",
    ]
    source_node_id: str | None = None
    source_column: str | None = None


def _validate_diagnostic_collection(
    *,
    state: DiagnosticCollectionState,
    total_count: int | None,
    items: list[Any],
    cap: int,
) -> None:
    if len(items) > cap:
        raise ValueError(f"diagnostic collection exceeds its {cap}-item cap")
    if state == "unavailable":
        if total_count is not None or items:
            raise ValueError("an unavailable collection has null total_count and no items")
        return
    if total_count is None or total_count < 0:
        raise ValueError("an available/truncated collection requires a non-negative count")
    if state == "available" and total_count != len(items):
        raise ValueError("available total_count must equal len(items)")
    if state == "truncated" and total_count <= len(items):
        raise ValueError("truncated total_count must exceed len(items)")


class ExecutionStrategyBoundaryCollectionPayload(BaseModel):
    state: DiagnosticCollectionState
    total_count: int | None
    items: list[ExecutionStrategyBoundaryPayload]

    @model_validator(mode="after")
    def _validate_collection(self) -> ExecutionStrategyBoundaryCollectionPayload:
        _validate_diagnostic_collection(
            state=self.state,
            total_count=self.total_count,
            items=self.items,
            cap=32,
        )
        keys = [
            (item.topological_rank, item.node_id, item.operator, item.boundary_kind)
            for item in self.items
        ]
        if keys != sorted(keys):
            raise ValueError("boundary items must be in nondecreasing primary-key order")
        return self


class ExecutionStrategyReasonCollectionPayload(BaseModel):
    state: DiagnosticCollectionState
    total_count: int | None
    items: list[ExecutionStrategyReasonPayload]

    @model_validator(mode="after")
    def _validate_collection(self) -> ExecutionStrategyReasonCollectionPayload:
        _validate_diagnostic_collection(
            state=self.state,
            total_count=self.total_count,
            items=self.items,
            cap=32,
        )
        max_rank = 2**63 - 1
        keys = [
            (
                max_rank if item.topological_rank is None else item.topological_rank,
                item.node_id or "",
                item.reason_code,
                item.operator or "",
            )
            for item in self.items
        ]
        if keys != sorted(keys):
            raise ValueError("reason items must be in nondecreasing primary-key order")
        return self


class ExecutionStrategyProvenanceCollectionPayload(BaseModel):
    state: DiagnosticCollectionState
    total_count: int | None
    items: list[ExecutionStrategyProvenancePayload]

    @model_validator(mode="after")
    def _validate_collection(self) -> ExecutionStrategyProvenanceCollectionPayload:
        _validate_diagnostic_collection(
            state=self.state,
            total_count=self.total_count,
            items=self.items,
            cap=128,
        )
        keys = [
            (
                item.column,
                item.origin_kind,
                item.source_node_id or "",
                item.source_column or "",
            )
            for item in self.items
        ]
        if keys != sorted(keys):
            raise ValueError("provenance items must be in nondecreasing primary-key order")
        return self


class ExecutionStrategyDiagnosticPayload(BaseModel):
    """Strict V1 API DTO for one shared execution-planning decision."""

    schema_version: Literal[1]
    status: Literal["projected", "admitted_eager", "boundary", "rejected", "not_planned"]
    strategy: Literal[
        "projected",
        "schema-all-except",
        "full-width-admitted-eager",
        "unprojected-streaming-boundary",
        "materialisation-boundary",
        "unsupported",
        "not-planned",
    ]
    profile: Literal[
        "preview_eager",
        "lazy_sink",
        "training_prep",
        "optimiser_setup",
        "explore_analysis",
        "auto_range",
        "deploy_live",
        "deploy_batch",
        "chunked_map_reduce",
    ]
    boundedness: Literal["bounded", "unbounded", "unknown"]
    reason_code: str
    detail_state: DiagnosticCollectionState
    boundaries: ExecutionStrategyBoundaryCollectionPayload
    reasons: ExecutionStrategyReasonCollectionPayload
    provenance: ExecutionStrategyProvenanceCollectionPayload
    blocking_node_id: str | None = None
    blocking_operator: str | None = None
    remediation: str | None = Field(default=None, max_length=512)
    estimated_peak_bytes: int | None = Field(default=None, ge=0)
    headroom_bytes: int | None = Field(default=None, ge=0)
    assumptions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_strategy_contract(self) -> ExecutionStrategyDiagnosticPayload:
        expected_status = {
            "projected": "projected",
            "schema-all-except": "projected",
            "full-width-admitted-eager": "admitted_eager",
            "unprojected-streaming-boundary": "boundary",
            "materialisation-boundary": "boundary",
            "unsupported": "rejected",
            "not-planned": "not_planned",
        }[self.strategy]
        if self.status != expected_status:
            raise ValueError("status does not match the V1 strategy mapping")
        precedence = {"available": 0, "unavailable": 1, "truncated": 2}
        expected_detail_state = max(
            (self.boundaries.state, self.reasons.state, self.provenance.state),
            key=precedence.__getitem__,
        )
        if self.detail_state != expected_detail_state:
            raise ValueError("detail_state must equal the worst child collection state")
        return self


class ExecutionStageMetricsPayload(BaseModel):
    schema_version: int = 1
    name: str = ""
    operation: str = ""
    profile: str = ""
    elapsed_ms: float = 0.0
    node_id: str | None = None
    job_id: str | None = None
    rss_start_bytes: int | None = None
    rss_end_bytes: int | None = None
    rss_delta_bytes: int | None = None
    rss_peak_bytes: int | None = None
    rows_in: int | None = None
    rows_out: int | None = None
    bytes_read: int | None = None
    bytes_written: int | None = None
    columns_scanned: int | None = None
    n_collects: int = 0
    n_checkpoints: int = 0


class ExecutionStreamabilityEvidencePayload(BaseModel):
    state: DiagnosticCollectionState = "unavailable"
    total_count: int | None = None
    items: list[str] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def _validate_collection(self) -> ExecutionStreamabilityEvidencePayload:
        _validate_diagnostic_collection(
            state=self.state,
            total_count=self.total_count,
            items=self.items,
            cap=32,
        )
        if self.items != sorted(set(self.items)):
            raise ValueError("streamability evidence must be sorted and unique")
        return self


class ExecutionColumnWidthsPayload(BaseModel):
    node_id: str
    input_width: int | None = Field(default=None, ge=0)
    output_width: int | None = Field(default=None, ge=0)
    requested_width: int | None = Field(default=None, ge=0)
    physically_scanned_width: int | None = Field(default=None, ge=0)


class ExecutionColumnWidthsCollectionPayload(BaseModel):
    state: Literal["available", "truncated"] = "available"
    total_count: int = Field(default=0, ge=0)
    items: list[ExecutionColumnWidthsPayload] = Field(default_factory=list, max_length=128)

    @model_validator(mode="after")
    def _validate_collection(self) -> ExecutionColumnWidthsCollectionPayload:
        _validate_diagnostic_collection(
            state=self.state,
            total_count=self.total_count,
            items=self.items,
            cap=128,
        )
        node_ids = [item.node_id for item in self.items]
        if node_ids != sorted(node_ids) or len(node_ids) != len(set(node_ids)):
            raise ValueError("column-width items must have unique sorted node ids")
        return self


class ExecutionAdmissionPayload(BaseModel):
    admitted: bool = True
    operation: str = ""
    profile: str = ""
    memory_limit_bytes: int = 0
    rss_at_admission_bytes: int | None = None
    rss_limit_bytes: int | None = None
    process_rss_limit_bytes: int | None = None
    headroom_bytes: int | None = None
    config_key: str = ""
    budget_policy: str = "fixed_default"
    available_ram_bytes: int | None = None
    os_reserve_bytes: int | None = None
    reason: str = ""


class ExecutionMemoryPressureEventPayload(BaseModel):
    schema_version: int = 1
    event: Literal["memory_pressure"] = "memory_pressure"
    operation: str = ""
    profile: str = ""
    job_id: str | None = None
    node_id: str | None = None
    stage: str | None = None
    label: str | None = None
    threshold_ratio: float = 0.0
    threshold_percent: int = 0
    rss_bytes: int = 0
    rss_limit_bytes: int = 0
    headroom_bytes: int = 0
    headroom_used_bytes: int = 0
    rss_peak_bytes: int = 0
    memory_limit_bytes: int | None = None
    memory_baseline_bytes: int | None = None
    baseline_rss_bytes: int | None = None
    budget_policy: str | None = None
    config_key: str | None = None
    available_ram_bytes: int | None = None
    os_reserve_bytes: int | None = None
    pressure_ratio: float = 0.0


class ExecutionMemoryLimitErrorPayload(BaseModel):
    error_code: Literal["memory_limit"]
    operation: str = ""
    profile: str | None = None
    job_id: str | None = None
    memory_limit_bytes: int | None = None
    rss_bytes: int | None = None
    rss_at_admission_bytes: int | None = None
    baseline_rss_bytes: int | None = None
    rss_limit_bytes: int | None = None
    process_rss_limit_bytes: int | None = None
    headroom_bytes: int | None = None
    reason: str = ""


class ExecutionMetricsPayload(BaseModel):
    schema_version: int = 1
    operation: str = ""
    profile: str = ""
    job_id: str | None = None
    status: str | None = None
    terminal_reason: str | None = None
    stage_count: int = 0
    retained_stage_count: int = 0
    truncated_stage_count: int = 0
    stages_truncated: bool = False
    total_elapsed_ms: float = 0.0
    node_elapsed_ms: dict[str, float] = Field(default_factory=dict)
    stage_elapsed_ms: dict[str, float] = Field(default_factory=dict)
    rss_start_bytes: int | None = None
    rss_end_bytes: int | None = None
    rss_delta_bytes: int | None = None
    rss_peak_bytes: int | None = None
    max_rss_bytes: int | None = None
    n_collects: int = 0
    n_checkpoints: int = 0
    memory_pressure_event_count: int = 0
    retained_memory_pressure_event_count: int = 0
    truncated_memory_pressure_event_count: int = 0
    memory_pressure_events_truncated: bool = False
    memory_limit_bytes: int | None = None
    memory_baseline_bytes: int | None = None
    rss_limit_bytes: int | None = None
    admission: ExecutionAdmissionPayload | None = None
    stages: list[ExecutionStageMetricsPayload] = Field(default_factory=list)
    memory_pressure_events: list[ExecutionMemoryPressureEventPayload] = Field(default_factory=list)
    execution_strategy: ExecutionStrategyDiagnosticPayload | None = None
    projection_plan_diagnostics: dict[str, Any] | None = None
    streamability: Literal["streaming", "materialising"] | None = None
    streamability_evidence: ExecutionStreamabilityEvidencePayload = Field(
        default_factory=ExecutionStreamabilityEvidencePayload
    )
    column_widths: ExecutionColumnWidthsCollectionPayload = Field(
        default_factory=ExecutionColumnWidthsCollectionPayload
    )
    bytes_read: int | None = Field(default=None, ge=0)
    bytes_written: int | None = Field(default=None, ge=0)
    estimated_bytes: int | None = Field(default=None, ge=0)
    checkpoint_count: int = Field(default=0, ge=0)
    chunk_count: int = Field(default=0, ge=0)
    observed_peak_rss_bytes: int | None = Field(default=None, ge=0)
    cancellation_latency_ms: float | None = Field(default=None, ge=0)


NodeExecutionStatus = Literal["ok", "error"]


class NodeResult(BaseModel):
    status: NodeExecutionStatus
    row_count: int = 0
    column_count: int = 0
    columns: list[ColumnInfo] = Field(default_factory=list)
    available_columns: list[ColumnInfo] = Field(default_factory=list)
    # Per-frame column schema for multi-frame producers (currently a
    # multi-table apiInput, future submodels / external callouts). Keyed
    # by the emit-table label (the ``sourceHandle`` / frame name a
    # downstream edge binds to). Empty for single-frame nodes, where
    # ``columns`` already carries the full schema. Additive to
    # ``columns`` — never replaces it.
    frame_columns: dict[str, list[ColumnInfo]] = Field(default_factory=dict)
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
    streaming_chunk_size: StreamingChunkSize = None
    # The frame/emit-table label to preview for a multi-frame producer (a
    # multi-table apiInput today; submodels / external callouts later). The
    # node holds every frame's DataFrame in ``eager_outputs`` as
    # ``dict[label, df]``; this picks which frame the flat ``columns`` /
    # ``preview`` reflect. ``None`` (the default) previews the FIRST frame —
    # the legacy behaviour. A label absent from the dict also falls back to
    # the first frame. Single-frame nodes ignore it. Part of the preview
    # cache key, so frame B is a DISTINCT cache entry from frame A.
    port_label: str | None = None


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
    node_statuses: dict[str, NodeExecutionStatus] = Field(default_factory=dict)
    node_columns: dict[str, list[ColumnInfo]] = Field(default_factory=dict)
    node_available_columns: dict[str, list[ColumnInfo]] = Field(default_factory=dict)
    # Per-frame column schemas for multi-frame producers, keyed
    # node_id → port_label → columns. Only nodes that emit 2+ frames
    # (a multi-table apiInput today; submodels / external callouts
    # later) appear here; single-frame nodes are absent and the
    # consumer falls back to ``node_columns``. Sibling to
    # ``node_columns`` — additive, never replaces it.
    node_frame_columns: dict[str, dict[str, list[ColumnInfo]]] = Field(default_factory=dict)
    node_schema_warnings: dict[str, list[SchemaWarning]] = Field(default_factory=dict)
    execution_metrics: ExecutionMetricsPayload | None = None


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
    streaming_chunk_size: StreamingChunkSize = None


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
    topological_rank: int = Field(ge=0)
    column_relevant: bool = True
    expression: dict[str, Any] | None = None
    calculation: dict[str, Any] | None = None
    node_detail: dict[str, Any] | None = None
    row_lineage_type: str | None = None


class TraceOmissionResponse(BaseModel):
    node_id: str
    node_name: str
    node_type: str
    topological_rank: int = Field(ge=0)
    reason: str = Field(min_length=1)
    diagnostic_index: int = Field(ge=0)


class TraceCorrelationDiagnosticResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    code: str
    severity: str
    reason: str
    message: str
    node_id: str | None = None
    child_node_id: str | None = None
    match_strategy: str | None = None
    match_columns: list[str] = Field(default_factory=list)
    ignored_columns: list[str] = Field(default_factory=list)
    matched_row_count: int | None = None
    matched_row_indices: list[int] = Field(default_factory=list)


class TraceWaterfallEntryResponse(BaseModel):
    label: str
    operation: str
    value: float
    delta: float
    cumulative: float
    default_used: bool


class TraceWaterfallErrorResponse(BaseModel):
    error: str
    error_type: str


class TraceResultResponse(BaseModel):
    target_node_id: str
    row_index: int
    column: str | None = None
    output_value: Any = None
    steps: list[TraceStepResponse] = Field(default_factory=list)
    omissions: list[TraceOmissionResponse]
    row_id_column: str | None = None
    row_id_value: Any = None
    total_nodes_in_pipeline: int = 0
    nodes_in_trace: int = 0
    execution_ms: float = 0.0
    waterfall: list[TraceWaterfallEntryResponse] | TraceWaterfallErrorResponse | None = None
    correlation_diagnostics: list[TraceCorrelationDiagnosticResponse]
    generated_at: str
    pipeline_source: str | None = None
    execution_origin: Literal["fresh_execution", "preview_cache", "trace_cache"]

    @field_validator("generated_at")
    @classmethod
    def _generated_at_must_be_utc(cls, value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("generated_at must be an ISO-8601 timestamp") from exc
        utc_offset = parsed.utcoffset()
        if utc_offset is None or utc_offset.total_seconds() != 0:
            raise ValueError("generated_at must include a UTC offset")
        return value

    @model_validator(mode="after")
    def _omissions_must_link_to_their_diagnostic(self) -> TraceResultResponse:
        for omission in self.omissions:
            if omission.diagnostic_index >= len(self.correlation_diagnostics):
                raise ValueError(
                    f"omission for {omission.node_id!r} references missing "
                    f"diagnostic {omission.diagnostic_index}"
                )
            diagnostic = self.correlation_diagnostics[omission.diagnostic_index]
            if diagnostic.node_id != omission.node_id:
                raise ValueError(
                    f"omission for {omission.node_id!r} references a diagnostic "
                    f"for {diagnostic.node_id!r}"
                )
        return self


class TraceResponse(BaseModel):
    status: str
    trace: TraceResultResponse


# ---------------------------------------------------------------------------
# /api/pipeline/write-output
# ---------------------------------------------------------------------------


class WriteOutputRequest(BaseModel):
    graph: Graph
    node_id: str
    source: str = "live"
    streaming_chunk_size: StreamingChunkSize = None


class WriteOutputResponse(BaseModel):
    status: str
    message: str = ""
    row_count: int = 0
    path: str = ""
    format: str = "parquet"
    execution_metrics: ExecutionMetricsPayload | None = None


# ---------------------------------------------------------------------------
# /api/input-cache
# ---------------------------------------------------------------------------


class _StrictInputCacheModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InputCacheSourceRequest(_StrictInputCacheModel):
    schema_version: Literal[1] = 1
    config: dict[str, Any]


class InputCacheBuildRequest(InputCacheSourceRequest):
    refresh: bool = False
    profile: Literal["preview_eager", "lazy_sink"] = "lazy_sink"


class InputCacheBuildResponse(_StrictInputCacheModel):
    schema_version: Literal[1] = 1
    job_id: str
    identity_digest: str
    status: Literal["running"]
    joined: bool


class InputCacheProgress(_StrictInputCacheModel):
    phase: Literal["queued", "building", "publishing", "completed", "failed", "cancelled"]
    rows: int = Field(default=0, ge=0)
    batches: int = Field(default=0, ge=0)
    bytes: int = Field(default=0, ge=0)
    elapsed_seconds: float = Field(default=0.0, ge=0)


class InputCacheGenerationPayload(_StrictInputCacheModel):
    generation_id: str
    row_count: int = Field(ge=0)
    column_count: int = Field(ge=0)
    columns: dict[str, str]
    size_bytes: int = Field(ge=0)
    created_at: float
    build_class: Literal["bounded", "admitted_eager", "unsupported"]


class InputCacheSnapshotStatusResponse(_StrictInputCacheModel):
    schema_version: Literal[1] = 1
    identity_digest: str
    state: Literal["missing", "building", "ready", "corrupt", "failed"]
    freshness: Literal["fresh", "stale", "unknown"]
    generation: InputCacheGenerationPayload | None = None


class InputCacheJobStatusResponse(_StrictInputCacheModel):
    schema_version: Literal[1] = 1
    job_id: str
    identity_digest: str
    status: JobStatus
    terminal_reason: str | None = None
    message: str = ""
    refresh: bool
    build_class: Literal["bounded", "admitted_eager", "unsupported"]
    progress: InputCacheProgress
    snapshot: InputCacheSnapshotStatusResponse | None = None
    error_code: str | None = None


class InputCacheCancelResponse(_StrictInputCacheModel):
    schema_version: Literal[1] = 1
    job_id: str
    cancellation_requested: bool
    status: JobStatus


# ---------------------------------------------------------------------------
# /api/explore
# ---------------------------------------------------------------------------


ExploreColumnKind = Literal["Numeric", "Text", "Temporal", "Boolean", "Nested", "Other"]


class ExploreColumnStat(BaseModel):
    """Per-column stats captured at Explore cache-materialisation time.

    Missingness is reported as a three-way split rather than a valid/invalid
    dichotomy: ``null_count`` (absent values), ``nan_count`` (float NaN — an
    invalid-numeric value that a stream unable to distinguish string from int
    materialises for non-numeric input), and everything else is valid. Polars
    ``null_count`` ignores NaN, so an all-NaN float column would otherwise look
    fully populated. ``nan_count`` is None for non-float dtypes (not
    applicable), mirroring ``zero_count``/``negative_count`` on non-numeric
    columns.

    ``distinct_count`` counts distinct non-null values (the null bucket is
    excluded) and may be None when the dtype is not hashable (Object columns),
    in which case the UI renders an em-dash.
    """

    name: str
    dtype: str
    kind: ExploreColumnKind
    null_count: int
    nan_count: int | None = None
    distinct_count: int | None
    min_value: str | None = None
    p25_value: str | None = None
    median_value: str | None = None
    mean_value: str | None = None
    p75_value: str | None = None
    max_value: str | None = None
    std_value: str | None = None
    zero_count: int | None = None
    negative_count: int | None = None


class ExploreDistinctValueCount(BaseModel):
    value: str | None
    count: int


class ExploreCategoricalColumnProfile(BaseModel):
    field: str
    distinct_count: int | None
    expandable: bool = False
    values_truncated: bool = False
    values: list[ExploreDistinctValueCount] = Field(default_factory=list)


class ExploreDataQualityIssue(BaseModel):
    severity: Literal["warning", "danger"]
    label: str
    detail: str


class ExploreDataQualitySummary(BaseModel):
    issue_count: int = 0
    issues: list[ExploreDataQualityIssue] = Field(default_factory=list)


class ExploreOverviewSummary(BaseModel):
    data_quality: ExploreDataQualitySummary = Field(default_factory=ExploreDataQualitySummary)
    categorical_summary: list[ExploreCategoricalColumnProfile] = Field(default_factory=list)


class ExploreCacheReport(BaseModel):
    """Result of materialising an Explore node's upstream dataset.

    Lightweight by design: the full frame lives in DataFrameExecutionCache
    (parquet on disk). This payload tells the UI what was cached and how to
    identify the cache entry.
    """

    status: Literal["ok"] = "ok"
    node_id: str
    upstream_node_id: str
    source: str = "live"
    dataframe_cache_key: str
    row_count: int = 0
    column_count: int = 0
    columns: list[ExploreColumnStat] = Field(default_factory=list)
    overview_summary: ExploreOverviewSummary = Field(default_factory=ExploreOverviewSummary)
    generated_at: float = 0.0
    execution_metrics: ExecutionMetricsPayload | None = None


class ExploreRunRequest(BaseModel):
    graph: Graph
    node_id: str
    source: str = "live"
    streaming_chunk_size: StreamingChunkSize = None


class ExploreRunResponse(BaseModel):
    status: Literal["started", "running", "completed"]
    job_id: str | None = None
    cached: bool = False
    message: str = ""
    result: ExploreCacheReport | None = None


class ExploreStatusResponse(BaseModel):
    status: JobStatus
    progress: float = 0.0
    message: str = ""
    result: ExploreCacheReport | None = None
    terminal_reason: str | None = None
    execution_metrics: ExecutionMetricsPayload | None = None


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


# ---------------------------------------------------------------------------
# /api/json-cache/*
# ---------------------------------------------------------------------------


class JsonCacheBuildRequest(BaseModel):
    """Request body for ``POST /api/json-cache/{build,status,cancel}``.

    Dispatch precedence in the route:
      1. ``volatile_schema is not None`` — use the in-memory v2 schema
         (the ApiInputEditor's React state, sent verbatim). This is the
         "user has unsaved edits open" path; mirrors the dual-cache
         model at the schema plane (handover working principle 4).
      2. Otherwise — read ``config_path`` from disk and use that.
      3. If both are absent, the route returns 422 (no schema source).

    ``volatile_schema`` carries the same shape as the on-disk config
    (``{tables: [...], path: ..., ...}``). Note ``is not None`` — an
    empty ``{}`` is distinct from ``None``: ``{}`` means "user provided
    a malformed payload", which surfaces as a 422 from
    ``validate_v2_schema``; ``None`` means "use disk".
    """

    path: str
    config_path: str | None = None
    # `Any` (not `dict`) so malformed shapes from the frontend reach
    # `validate_v2_schema` and surface as our structured 422 rather
    # than as Pydantic's default 422 — T8 contract.
    volatile_schema: Any = None


class JsonCacheInferRequest(BaseModel):
    """Request body for ``POST /api/json-cache/infer`` — sniff a v2 schema
    mapping from a JSON/JSONL file. Used by the ApiInputEditor's *Infer
    Tables* button so the user gets a sensible starting structure without
    hand-typing column paths.

    ``sample_size`` is ``None`` by default — types are inferred across the
    whole file so a value that appears late (e.g. a float in an otherwise
    integer column) widens the inferred type instead of being missed and
    then crashing the strict build. Pass an int to cap the scan on very
    large files (the build still reads every record, so a past-sample
    mismatch fails loud with a clear error rather than silently).
    """

    path: str
    sample_size: int | None = None


class JsonCacheInferResponse(BaseModel):
    """v2-shaped inference output: a list of table specs to merge into
    the apiInput's config. Caller stitches in the apiInput's existing
    ``path`` and ``contract`` metadata.
    """

    tables: list[dict[str, Any]]


class JsonCacheBuildResponse(BaseModel):
    path: str
    data_path: str
    row_count: int
    column_count: int
    columns: dict[str, str]
    size_bytes: int
    cached_at: float
    cache_seconds: float
    # W2 item 2.7 — zero silent record loss. ``skipped_records`` counts
    # top-level inputs that weren't JSON objects (e.g. a JSONL line holding
    # a bare number); ``skipped_rows`` counts, per frame label, array
    # elements whose shape mismatched that table (mixed arrays). Both are
    # zero/empty for clean data.
    skipped_records: int = 0
    skipped_rows: dict[str, int] = Field(default_factory=dict)


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
    # Mirrors JsonCacheBuildResponse (W2 item 2.7): the skip counts the
    # build recorded into meta.json, echoed on status polls.
    skipped_records: int = 0
    skipped_rows: dict[str, int] = Field(default_factory=dict)


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
    streaming_chunk_size: StreamingChunkSize = None


TrainingFeatureSelectionMode = Literal["explicit", "all_except", "glm_terms"]
TrainingFeatureExclusionReason = Literal[
    "target",
    "weight",
    "offset",
    "fold",
    "identifier",
    "split",
    "configured_exclusion",
    "not_selected",
    "not_in_formula",
]


class TrainingFeatureNameCollectionPayload(BaseModel):
    state: Literal["available", "truncated"]
    total_count: int = Field(ge=0)
    items: list[str] = Field(max_length=128)

    @model_validator(mode="after")
    def _validate_collection(self) -> TrainingFeatureNameCollectionPayload:
        _validate_diagnostic_collection(
            state=self.state,
            total_count=self.total_count,
            items=self.items,
            cap=128,
        )
        if len(self.items) != len(set(self.items)):
            raise ValueError("training feature names must be unique")
        return self


class TrainingFeatureColumnReasonPayload(BaseModel):
    column: str
    reason: TrainingFeatureExclusionReason


class TrainingFeatureColumnReasonCollectionPayload(BaseModel):
    state: Literal["available", "truncated"]
    total_count: int = Field(ge=0)
    items: list[TrainingFeatureColumnReasonPayload] = Field(max_length=128)

    @model_validator(mode="after")
    def _validate_collection(self) -> TrainingFeatureColumnReasonCollectionPayload:
        _validate_diagnostic_collection(
            state=self.state,
            total_count=self.total_count,
            items=self.items,
            cap=128,
        )
        columns = [item.column for item in self.items]
        if len(columns) != len(set(columns)):
            raise ValueError("training feature column reasons must be unique")
        return self


class TrainingFeatureSelectionDiagnosticPayload(BaseModel):
    schema_version: Literal[1] = 1
    mode: TrainingFeatureSelectionMode
    feature_count: int = Field(ge=0)
    detail_state: Literal["available", "truncated"]
    features: TrainingFeatureNameCollectionPayload
    retained_metadata: TrainingFeatureColumnReasonCollectionPayload
    excluded_columns: TrainingFeatureColumnReasonCollectionPayload

    @model_validator(mode="after")
    def _validate_selection(self) -> TrainingFeatureSelectionDiagnosticPayload:
        if self.feature_count != self.features.total_count:
            raise ValueError("feature_count must equal features.total_count")
        expected = (
            "truncated"
            if "truncated"
            in {
                self.features.state,
                self.retained_metadata.state,
                self.excluded_columns.state,
            }
            else "available"
        )
        if self.detail_state != expected:
            raise ValueError("detail_state must equal the worst child collection state")
        return self


class TrainResponse(BaseModel):
    status: Literal["started", "completed", "error"]
    job_id: str | None = None
    metrics: dict[str, float] = Field(default_factory=dict)
    feature_importance: list[dict[str, Any]] = Field(default_factory=list)
    model_path: str = ""
    train_rows: int = 0
    # NB: despite the name, ``test_rows`` carries the VALIDATION-set row count
    # (``split_result.n_validation``), not a separate test set. The name is
    # frozen by the external API/frontend contract (frontend/src/api/types.ts,
    # guards.ts, ui_contracts fixtures) so it is intentionally NOT renamed; its
    # meaning is pinned by
    # tests/test_modelling.py::TestTrainingJob::test_test_rows_field_carries_validation_set_count.
    test_rows: int = 0
    holdout_rows: int = 0
    holdout_metrics: dict[str, float] = Field(default_factory=dict)
    diagnostics_set: str = "validation"  # "train" | "validation" | "holdout"
    features: list[str] = Field(default_factory=list)
    cat_features: list[str] = Field(default_factory=list)
    error: str | None = None
    best_iteration: int | None = None
    loss_history: list[dict[str, float]] = Field(default_factory=list)
    loss_history_truncated: bool = False
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
    feature_selection: TrainingFeatureSelectionDiagnosticPayload | None = None


class TrainStatusResponse(BaseModel):
    status: JobStatus
    progress: float = 0.0
    message: str = ""
    iteration: int = 0
    total_iterations: int = 0
    train_loss: dict[str, float] = Field(default_factory=dict)
    train_loss_history: list[dict[str, float]] = Field(default_factory=list)
    train_loss_history_truncated: bool = False
    elapsed_seconds: float = 0.0
    result: TrainResponse | None = None
    warning: str | None = None
    terminal_reason: str | None = None
    execution_metrics: ExecutionMetricsPayload | None = None
    feature_selection: TrainingFeatureSelectionDiagnosticPayload | None = None


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


class DispersionEstimateRequest(BaseModel):
    """Estimate a GLM dispersion parameter (NB theta / Tweedie var_power).

    The estimate is an explicit user action in the config panel: the resolved
    value lands in the node config where the training-objective gate requires
    it, never as a hidden default.
    """

    graph: Graph
    node_id: str
    source: str = "live"
    param: Literal["theta", "var_power"]


class DispersionEstimateResponse(BaseModel):
    status: Literal["started"]
    job_id: str


class DispersionEstimateStatusResponse(BaseModel):
    status: JobStatus
    progress: float = 0.0
    message: str = ""
    elapsed_seconds: float = 0.0
    param: str | None = None
    value: float | None = None
    llf: float | None = None
    n_fits: int | None = None
    error: str | None = None
    terminal_reason: str | None = None


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
    streaming_chunk_size: StreamingChunkSize = None


class OptimiserSolveResponse(BaseModel):
    status: Literal["started", "error"]
    job_id: str | None = None
    error: str | None = None


class OptimiserEstimateRequest(BaseModel):
    """Body for the optimiser-cost estimate.

    Used by the frontend to preview the solver input volume before kicking
    off a solve.  ``total_rows`` comes from cheap ancestor parquet metadata,
    but the exact quote/scenario counts execute the pipeline up to the
    optimiser's data input (dataframe-execution cache assisted) plus one
    streaming aggregation scan — see ``POST /api/optimiser/estimate``.
    The solver itself is never invoked.
    """

    graph: Graph
    node_id: str
    source: str = "live"
    streaming_chunk_size: StreamingChunkSize = None


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
    streaming_chunk_size: StreamingChunkSize = None


class OptimiserFrontierRange(BaseModel):
    min: float
    max: float


class OptimiserFrontierAutoRangeResponse(BaseModel):
    status: str = "ok"
    ranges: dict[str, OptimiserFrontierRange] = Field(default_factory=dict)
    method: str = "scenario_envelope"
    warning: str | None = None


class OptimiserFrontierAutoRangeStartResponse(BaseModel):
    status: Literal["started", "error"]
    job_id: str | None = None
    error: str | None = None


class OptimiserFrontierAutoRangeStatusResponse(BaseModel):
    status: JobStatus
    progress: float = 0.0
    message: str = ""
    elapsed_seconds: float = 0.0
    result: OptimiserFrontierAutoRangeResponse | None = None
    terminal_reason: str | None = None
    error_code: str | None = None
    http_status_code: int | None = None
    error_detail: ExecutionMemoryLimitErrorPayload | dict[str, Any] | str | None = None
    execution_metrics: ExecutionMetricsPayload | None = None


class OptimiserFrontierRequest(BaseModel):
    job_id: str
    threshold_ranges: dict[str, list[float]] = Field(default_factory=dict)
    n_points_per_dim: int = Field(default=5, ge=1, le=100)
    streaming_chunk_size: StreamingChunkSize = None

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
    job_id: str | None = None
    """Pollable frontier job handle when ``status == "started"``."""


class OptimiserFrontierStatusResponse(BaseModel):
    status: JobStatus
    progress: float = 0.0
    message: str = ""
    elapsed_seconds: float = 0.0
    result: OptimiserFrontierResponse | None = None
    terminal_reason: str | None = None
    error_code: str | None = None
    http_status_code: int | None = None
    error_detail: ExecutionMemoryLimitErrorPayload | dict[str, Any] | str | None = None
    execution_metrics: ExecutionMetricsPayload | None = None


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
    status: JobStatus
    progress: float = 0.0
    message: str = ""
    elapsed_seconds: float = 0.0
    result: OptimiserSolveResult | None = None
    frontier: OptimiserFrontierResponse | None = None
    terminal_reason: str | None = None
    execution_metrics: ExecutionMetricsPayload | None = None


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
# Move through history (P6): materialise a historical commit as the working
# directory (detached checkout). Creates nothing — the next save spawns a fresh
# working branch there (S13).
# ---------------------------------------------------------------------------


class GitMoveRequest(BaseModel):
    # The commit to move to — its tree becomes the working directory.
    sha: str


class GitMoveResponse(BaseModel):
    # The commit now checked out (detached HEAD).
    sha: str
    short_sha: str
    # The branch HEAD was on before the move. The move detaches rather than
    # moving any ref, so this branch stays put and fully reachable.
    prior_branch: str
    # Always True: a move leaves HEAD detached with no working branch recorded.
    is_detached: bool = True


# ---------------------------------------------------------------------------
# Save & commit (P3): milestone merge of the ledger onto the working branch,
# and the working branch's milestone history.
# ---------------------------------------------------------------------------


class GitCommitRequest(BaseModel):
    # User-supplied milestone message (rides the merge commit, S18).
    message: str
    # Optional version label → annotated git tag on the milestone (S18).
    version_label: str | None = None
    # Escape hatch for the P7 fork-gate (U4/D4): when the working branch is behind
    # the remote, a milestone would fork it. False (default) makes the engine
    # refuse with GitMilestoneFork data so the UI can warn; True is the user's
    # deliberate "commit anyway (creates a fork)" override.
    allow_fork: bool = False


class GitCommitResponse(BaseModel):
    sha: str
    short_sha: str
    working_branch: str
    version_label: str | None = None


class GitMilestoneFork(BaseModel):
    # The pre-milestone fork warning (P7 U4/D4): the working branch is behind its
    # remote, so saving a milestone now would branch off the shared copy instead
    # of building on it. Delivered as the body of a 409 from POST /api/git/commit
    # so the UI can warn + offer "commit anyway (creates a fork)". Read from LOCAL
    # refs only (no fetch — the milestone stays instant and offline-safe).
    status: Literal["would_fork"] = "would_fork"
    remote: str
    working: GitRemoteLeg
    message: str


class GitMilestoneEntry(BaseModel):
    sha: str
    short_sha: str
    message: str
    timestamp: str
    version_label: str | None = None
    # The repo's initial commit (no parents) — the UI tags it "init".
    is_root: bool = False


class GitMilestonesResponse(BaseModel):
    working_branch: str | None = None
    entries: list[GitMilestoneEntry] = Field(default_factory=list)


class GitGraphEntry(GitMilestoneEntry):
    # One commit on a branch's first-parent spine, for the graph rail: the
    # milestone fields plus the topology the rail draws edges from.
    # All parent SHAs — first is the previous spine commit, second (on a merge
    # milestone) the folded ledger tip. The rail's magnifier gate derives from
    # this: >= 2 parents ⇔ folded saves exist (the engine never commits an
    # empty fold).
    parents: list[str] = Field(default_factory=list)


class GitGraphBranch(BaseModel):
    # One working pair in the graph forest (its ledger implicit, as in the
    # branch manager). Archived pairs are included; the client filters.
    name: str
    is_archived: bool
    is_current: bool
    tip_sha: str
    # Fork attachment, derived from git ancestry (claim-based over FULL
    # first-parent spines — never forks.json): the newest spine commit already
    # owned by an earlier-processed branch, and that branch's name. Both null
    # for the root branch of each tree in the forest. Reported even when the
    # commit falls outside the windowed entries.
    fork_point_sha: str | None = None
    fork_of: str | None = None
    # The SAVE commit this branch was actually spawned from, when that differs
    # from the fork-point milestone: forking at a save crystallizes an
    # anchoring merge as the fork's oldest own commit, and its second parent
    # is the save — reported only when that save belongs to the PARENT pair's
    # history (folded into a later parent milestone, or still pending on the
    # parent's ledger). Null for ordinary milestone-level forks (whose
    # anchoring second parent is the fork's OWN ledger save) and for branches
    # with no fork point. UI: the spawn chip anchors to this save's row
    # whenever it is visible (its containing fold expanded).
    fork_source_sha: str | None = None
    # The parent-spine milestone whose fold CONTAINS fork_source — the
    # milestone that visually "takes credit" for the spawn while its saves are
    # collapsed. Null when fork_source is unset, or when the source save is
    # still pending on the parent's ledger (not yet folded into any parent
    # milestone). UI: the spawn chip anchors here when the source save's row
    # is not visible, falling back to fork_point_sha when this is null too.
    fork_credit_sha: str | None = None
    # Clone-local back-link (forks.json), kept as passthrough for API
    # completeness — the fork chips are served by /api/git/working-branches,
    # and the graph client does not read it yet.
    forked_from: str | None = None
    # True when the full spine is longer than the requested limit (entries are
    # windowed to the newest ``limit``; fork points are not).
    truncated: bool = False
    # Newest-first first-parent spine, windowed to the limit.
    entries: list[GitGraphEntry] = Field(default_factory=list)


class GitGraphResponse(BaseModel):
    working_branch: str | None = None
    # Deterministic branch processing order (the current working branch first,
    # then spine depth desc, then name) — doubles as the stable lane order so
    # clients never re-derive it.
    order: list[str] = Field(default_factory=list)
    branches: list[GitGraphBranch] = Field(default_factory=list)


class GitCommitRef(BaseModel):
    sha: str
    short_sha: str
    message: str
    version_label: str | None = None
    is_root: bool = False


class GitCommitContext(BaseModel):
    sha: str
    short_sha: str
    message: str
    timestamp: str
    is_root: bool = False
    is_milestone: bool = False
    version_label: str | None = None
    # The LATEST milestone at this commit (its working-chain anchor), and the
    # number of commits between that milestone's ledger fold-point and this commit.
    nearest_milestone: GitCommitRef
    distance: int = 0
    # Optional: commits between a caller-supplied base commit and this one
    # (``rev-list --count base..self``). Populated only when ``commit-context`` is
    # queried with ``?base=`` — the historic↔current delta for the compare UI.
    delta_from_base: int | None = None


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


class GitUndeleteRequest(BaseModel):
    # Working-branch name to restore (a ledger name resolves to its pair).
    branch: str


class GitUndeleteResponse(BaseModel):
    status: str = "ok"
    branch: str


class GitRemoteLeg(BaseModel):
    # Divergence of one local branch (the working branch or its ledger) vs its
    # remote-tracking ref. `status` carries the tri-state honesty (F2):
    # "untracked" = never pushed to this remote / not spawned locally yet (NOT
    # the same as in-sync); "unknown" = the count couldn't be read; otherwise the
    # measured state. ahead/behind are null unless measured.
    status: Literal["untracked", "unknown", "synced", "ahead", "behind", "diverged"]
    ahead: int | None = None
    behind: int | None = None


class GitRemote(BaseModel):
    # One existing remote, for the deliberate-push dropdown (S16) and the passive
    # behind-remote surface (P7). `ahead`/`behind` remain the WORKING leg's counts
    # for back-compat; `working`/`ledger` add the per-leg structured state (F6) so
    # ledger divergence — the two-machine save accident — is visible, not just the
    # working leg. Read from locally-known remote refs (a throttled pair fetch
    # freshens them first); null when no working branch is set.
    name: str
    url: str | None = None
    ahead: int | None = None
    behind: int | None = None
    working: GitRemoteLeg | None = None
    ledger: GitRemoteLeg | None = None


class GitRemotesResponse(BaseModel):
    remotes: list[GitRemote] = Field(default_factory=list)
    # The branch ahead/behind is computed for (the clone's working branch), or
    # null when none is set.
    working_branch: str | None = None


class GitPushRequest(BaseModel):
    remote: str


class GitPushResponse(BaseModel):
    remote: str
    working_branch: str
    ledger_branch: str
    default_branch: str
    bootstrapped_default: bool = False
    # Explicit branch refs submitted (never --follow-tags additions).
    pushed_refs: list[str] = Field(default_factory=list)


class GitBranchAwayRequest(BaseModel):
    remote: str


class GitBranchAwayResponse(BaseModel):
    # M3: the local (forked) pair was set aside under a dated name and the
    # canonical branch name repointed to the remote's tips — both lineages kept,
    # nothing rewritten (S35: the new name is surfaced, never silent).
    # `working_branch` is the unchanged canonical name now tracking the remote;
    # `set_aside_as` is the dated name preserving the local divergent work.
    working_branch: str
    set_aside_as: str


class GitFastForwardRequest(BaseModel):
    remote: str


class GitFastForwardResponse(BaseModel):
    # A conflict-free catch-up (P7 D1/D2): the working pair advanced to the
    # remote's tips by fast-forward only (never a merge). `fast_forwarded` lists
    # the refs that actually moved (working and/or the ledger).
    remote: str
    working_branch: str
    fast_forwarded: list[str] = Field(default_factory=list)


class GitPushRejection(BaseModel):
    # A non-fast-forward push rejection, carrying the per-leg divergence so the UI
    # can show the honest fork instead of a dead-end string (P7 M7/M6). Delivered
    # as the body of a 409 response. `status` is a fixed discriminator the client
    # keys on; `working`/`ledger` are the legs recomputed from a fetch taken at the
    # moment of rejection (`ledger` null when the ledger isn't spawned); `message`
    # is a hand-written, leg-naming explanation safe to surface verbatim.
    status: Literal["rejected_diverged"] = "rejected_diverged"
    remote: str
    working: GitRemoteLeg
    ledger: GitRemoteLeg | None = None
    message: str
    # X3: True when the remote dropped a commit this clone had published (a
    # rebase/force-push upstream), not an ordinary divergence — the UI says so
    # distinctly and points at a person-reconciles off-ramp.
    is_rewrite: bool = False


# ---------------------------------------------------------------------------
# Data In/Out format capabilities (dataInput / dataOutput node editors)
# ---------------------------------------------------------------------------


class _StrictIoCapabilitiesModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IoInputCapability(_StrictIoCapabilitiesModel):
    modes: list[Literal["scan", "read"]]
    arguments: dict[str, list[str]]
    engines_missing: list[str]
    direct_bounded: bool
    needs_schema_when_bounded: bool
    snapshot_build: Literal["bounded", "admitted_eager", "unsupported"]
    cached_read: bool


class IoOutputCapability(_StrictIoCapabilitiesModel):
    modes: list[Literal["sink", "write"]]
    arguments: dict[str, list[str]]
    engines_missing: list[str]
    native_sink: bool
    eager_writer: bool
    publication: Literal["atomic_file", "transactional"]


class IoFormatCapability(_StrictIoCapabilitiesModel):
    name: str
    label: str
    group: Literal["file", "database", "lakehouse", "inline"]
    extensions: list[str]
    unstable: bool
    input: IoInputCapability | None = None
    output: IoOutputCapability | None = None


class IoFieldCapability(_StrictIoCapabilitiesModel):
    name: str
    label: str
    kind: Literal["path", "connection", "text", "query", "table", "records"]
    required: bool


class IoCapabilityGroup(_StrictIoCapabilitiesModel):
    name: Literal["file", "database", "lakehouse", "databricks", "inline"]
    label: str
    input_available: bool
    output_available: bool
    cache_modes: list[Literal["direct", "snapshot"]]
    input_fields: list[IoFieldCapability]
    output_fields: list[IoFieldCapability]
    formats: list[IoFormatCapability]


class IoCapabilitiesResponse(_StrictIoCapabilitiesModel):
    schema_version: Literal[1]
    groups: list[IoCapabilityGroup]
