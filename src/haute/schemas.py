"""Pydantic models for API request/response validation.

The canonical graph types (``GraphEdge``, ``NodeData``, ``GraphNode``,
``PipelineGraph``) are defined in ``haute._types`` and re-exported here
with API-friendly aliases so that FastAPI endpoint signatures stay clean.
"""

from __future__ import annotations

import keyword
import math
from datetime import datetime
from typing import Annotated, Any, Literal, cast

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    RootModel,
    StrictBool,
    field_validator,
    model_validator,
)

from haute._execution_schemas import (
    DiagnosticCollectionState,  # noqa: F401
    ExecutionAdmissionPayload,  # noqa: F401
    ExecutionCacheProofMissReasonCountsPayload,  # noqa: F401
    ExecutionCacheProofPayload,  # noqa: F401
    ExecutionColumnWidthsCollectionPayload,  # noqa: F401
    ExecutionColumnWidthsPayload,  # noqa: F401
    ExecutionMemoryLimitErrorPayload,  # noqa: F401
    ExecutionMemoryPressureEventPayload,  # noqa: F401
    ExecutionMetricsPayload,  # noqa: F401
    ExecutionStageMetricsPayload,  # noqa: F401
    ExecutionStrategyBoundaryCollectionPayload,  # noqa: F401
    ExecutionStrategyBoundaryPayload,  # noqa: F401
    ExecutionStrategyDiagnosticPayload,  # noqa: F401
    ExecutionStrategyProvenanceCollectionPayload,  # noqa: F401
    ExecutionStrategyProvenancePayload,  # noqa: F401
    ExecutionStrategyReasonCollectionPayload,  # noqa: F401
    ExecutionStrategyReasonPayload,  # noqa: F401
    ExecutionStreamabilityEvidencePayload,  # noqa: F401
    NodeExecutionStatus,  # noqa: F401
    _validate_diagnostic_collection,
)
from haute._types import GraphEdge as GraphEdge  # noqa: F401
from haute._types import GraphNode as GraphNode  # noqa: F401
from haute._types import NodeData as GraphNodeData  # noqa: F401
from haute._types import NodeType
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

RevisionToken = Annotated[str, Field(min_length=1, pattern=r"^\S+$")]

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

PipelineLoadStatus = Literal["ready", "degraded", "source_only"]
PipelineElementAvailability = Literal["ready", "unavailable", "blocked"]
PipelineDiagnosticSeverity = Literal["warning", "error"]
PipelineDiagnosticScope = Literal["pipeline", "node", "edge", "submodel"]


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
    endpoint_host: str | None
    trust: Literal["local", "organization", "external"] | None
    max_sensitivity: Literal["public", "internal", "restricted"] | None
    mutations_enabled: bool
    mutations_reason: str | None

    @model_validator(mode="after")
    def _configured_status_has_egress_identity(self) -> AssistantStatusResponse:
        if self.configured and (
            self.reason is not None
            or self.provider is None
            or self.model is None
            or self.endpoint_host is None
            or self.trust is None
            or self.max_sensitivity is None
        ):
            raise ValueError(
                "configured assistant status requires provider, model, endpoint host, "
                "trust, maximum sensitivity, and no readiness reason"
            )
        return self


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


class AssistantSessionSummary(BaseModel):
    """One conversation as the chat list renders it, without its transcript."""

    session_id: str
    # The opening user message, whitespace-collapsed and length-bounded. Empty
    # only for a session whose first turn carried no user text.
    title: str = ""
    created_at: float
    last_used: float
    message_count: int


class AssistantSessionListResponse(BaseModel):
    sessions: list[AssistantSessionSummary] = []


class AssistantMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
# Pipeline editor recovery document
# ---------------------------------------------------------------------------


class RecoverySourceSpan(BaseModel):
    """One bounded, one-based source range attributable during recovery."""

    model_config = ConfigDict(extra="forbid")

    start_line: int = Field(ge=1)
    start_column: int = Field(ge=0)
    end_line: int = Field(ge=1)
    end_column: int = Field(ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> RecoverySourceSpan:
        if (self.end_line, self.end_column) < (self.start_line, self.start_column):
            raise ValueError("Recovery source span must end at or after its start.")
        return self


class PipelineRecoveryDiagnostic(BaseModel):
    """Safe, stable diagnostic carried by an editor recovery document."""

    model_config = ConfigDict(extra="forbid")

    diagnostic_id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9._:-]*$")
    code: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    severity: PipelineDiagnosticSeverity = "error"
    scope: PipelineDiagnosticScope
    message: str = Field(min_length=1, max_length=1024)
    element_id: str | None = None
    source_file: str | None = None
    source_span: RecoverySourceSpan | None = None
    remediation: str | None = Field(default=None, max_length=1024)
    incident_id: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9][a-z0-9-]*$",
    )


class PipelineDocumentCapabilities(BaseModel):
    """Server-derived admission fence for one loaded editor document."""

    model_config = ConfigDict(extra="forbid")

    can_mutate: bool
    can_save: bool
    can_execute: bool
    can_preview: bool
    can_manage_submodels: bool
    can_repair: bool = False
    reserved_api_input_frame_labels: list[str]

    @field_validator("reserved_api_input_frame_labels")
    @classmethod
    def _sorted_unique_reserved_labels(cls, value: list[str]) -> list[str]:
        if value != sorted(set(value)):
            raise ValueError("reserved_api_input_frame_labels must be sorted and unique.")
        return value


class RecoveryPipelineNode(BaseModel):
    """Editor node shape intentionally incompatible with canonical GraphNode."""

    model_config = ConfigDict(extra="forbid")

    recovery_id: str = Field(min_length=1)
    authored_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    decorator_name: str = Field(min_length=1)
    node_type: str | None
    description: str = ""
    availability: PipelineElementAvailability
    display_position: dict[str, float]
    config: dict[str, Any] | None = None
    config_reference: str | None = None
    function_name: Annotated[str, Field(min_length=1)]
    default_input_name: Annotated[str, Field(min_length=1)] | None
    source_handle_input_names: dict[
        Annotated[str, Field(min_length=1)],
        Annotated[str, Field(min_length=1)],
    ]
    source_file: str | None = None
    source_span: RecoverySourceSpan | None = None
    diagnostic_ids: list[str] = Field(default_factory=list)
    blocking_path: list[str] = Field(default_factory=list)

    @field_validator("display_position")
    @classmethod
    def _finite_position(cls, value: dict[str, float]) -> dict[str, float]:
        if set(value) != {"x", "y"}:
            raise ValueError("Recovery display_position must contain exactly x and y.")
        position = {"x": float(value["x"]), "y": float(value["y"])}
        if not all(math.isfinite(coordinate) for coordinate in position.values()):
            raise ValueError("Recovery display_position coordinates must be finite.")
        return position


class RecoveryPipelineEdge(BaseModel):
    """Editor edge shape intentionally incompatible with canonical GraphEdge."""

    model_config = ConfigDict(extra="forbid")

    recovery_id: str = Field(min_length=1)
    source_recovery_id: str = Field(min_length=1)
    target_recovery_id: str = Field(min_length=1)
    source_authored_id: str = Field(min_length=1)
    target_authored_id: str = Field(min_length=1)
    source_handle: str | None = None
    target_handle: str | None = None
    source_port: str | None = None
    target_port: str | None = None
    input_name: str | None
    availability: PipelineElementAvailability
    source_span: RecoverySourceSpan | None = None
    diagnostic_ids: list[str] = Field(default_factory=list)
    blocking_path: list[str] = Field(default_factory=list)


class RecoveryUnresolvedConnection(BaseModel):
    """Authored connection retained as a diagnostic structure."""

    model_config = ConfigDict(extra="forbid")

    recovery_id: str = Field(min_length=1)
    source_recovery_id: str | None = None
    target_recovery_id: str | None = None
    source_authored_id: str = Field(min_length=1)
    target_authored_id: str = Field(min_length=1)
    source_handle: str | None = None
    target_handle: str | None = None
    source_port: str | None = None
    target_port: str | None = None
    source_span: RecoverySourceSpan | None = None
    diagnostic_ids: list[str] = Field(min_length=1)


class RecoveryGraphSnapshot(BaseModel):
    """Renderable recovery graph used at the root and in submodel definitions."""

    model_config = ConfigDict(extra="forbid")

    nodes: list[RecoveryPipelineNode] = Field(default_factory=list)
    edges: list[RecoveryPipelineEdge] = Field(default_factory=list)
    unresolved_connections: list[RecoveryUnresolvedConnection] = Field(default_factory=list)
    submodels: dict[str, RecoverySubmodelDefinition] | None = None


class RecoverySubmodelDefinition(BaseModel):
    """One editor-only recovered submodel definition."""

    model_config = ConfigDict(extra="forbid")

    definition_id: str = Field(min_length=1)
    file: str = Field(min_length=1)
    availability: PipelineElementAvailability
    diagnostic_ids: list[str] = Field(default_factory=list)
    graph: RecoveryGraphSnapshot
    input_ports: list[dict[str, Any]] = Field(default_factory=list)
    input_port_input_names: dict[str, str]
    output_ports: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _input_identity_coverage(self) -> RecoverySubmodelDefinition:
        port_ids = [port.get("portId") for port in self.input_ports]
        if any(not isinstance(port_id, str) or not port_id for port_id in port_ids):
            raise ValueError("Recovery submodel input ports require non-empty portId values.")
        if set(self.input_port_input_names) != set(port_ids):
            raise ValueError("input_port_input_names must exactly cover input_ports.")
        if any(not value for value in self.input_port_input_names.values()):
            raise ValueError("input_port_input_names values must be non-empty.")
        return self


class PipelineEditorDocument(BaseModel):
    """Versioned editor load result; never a canonical executable graph."""

    model_config = ConfigDict(extra="forbid")

    document_kind: Literal["haute.pipeline_editor_document"] = "haute.pipeline_editor_document"
    schema_version: Literal[1] = 1
    load_status: PipelineLoadStatus
    pipeline_name: str | None = None
    pipeline_description: str | None = None
    preamble: str | None = None
    preserved_blocks: list[str] = Field(default_factory=list)
    source_file: str = ""
    source_revision: RevisionToken | None = None
    source_text: str = ""
    sources: list[str] = Field(default_factory=lambda: ["live"])
    active_source: str | None = "live"
    source_selection_trusted: bool = True
    has_authored_content: bool
    nodes: list[RecoveryPipelineNode] = Field(default_factory=list)
    edges: list[RecoveryPipelineEdge] = Field(default_factory=list)
    unresolved_connections: list[RecoveryUnresolvedConnection] = Field(default_factory=list)
    submodels: dict[str, RecoverySubmodelDefinition] | None = None
    diagnostics: list[PipelineRecoveryDiagnostic] = Field(default_factory=list)
    diagnostics_omitted: int = Field(default=0, ge=0)
    capabilities: PipelineDocumentCapabilities


class EditorIdentityRequestNode(BaseModel):
    """One bounded, side-effect-free editor identity resolution request."""

    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1, max_length=512)
    label: str = Field(min_length=1, max_length=2048)
    node_type: NodeType
    submodel_alias: str | None = Field(default=None, min_length=1, max_length=512)
    source_handles: list[Annotated[str, Field(min_length=1, max_length=512)]] = Field(
        default_factory=list,
        max_length=1024,
    )

    @field_validator("source_handles")
    @classmethod
    def _unique_source_handles(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("source_handles must be unique.")
        return value

    @model_validator(mode="after")
    def _identity_inputs_match_node_type(self) -> EditorIdentityRequestNode:
        if self.node_type == NodeType.SUBMODEL:
            if self.submodel_alias is None:
                raise ValueError("submodel_alias is required for submodel nodes.")
            if any(
                not handle.startswith("out__") or len(handle) == len("out__")
                for handle in self.source_handles
            ):
                raise ValueError("submodel source handles must use out__<port_id>.")
            return self
        if self.submodel_alias is not None:
            raise ValueError("submodel_alias is only valid for submodel nodes.")
        if self.node_type == NodeType.API_INPUT and any(
            not handle.isascii() or not handle.isidentifier() or keyword.iskeyword(handle)
            for handle in self.source_handles
        ):
            raise ValueError("apiInput source handles must be non-keyword ASCII identifiers.")
        if (
            self.node_type not in {NodeType.API_INPUT, NodeType.SUBMODEL_PORT}
            and self.source_handles
        ):
            raise ValueError("source_handles are not valid for this node type.")
        return self


class EditorIdentitiesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: list[EditorIdentityRequestNode] = Field(min_length=0, max_length=10_000)

    @field_validator("nodes")
    @classmethod
    def _unique_node_ids(
        cls, value: list[EditorIdentityRequestNode]
    ) -> list[EditorIdentityRequestNode]:
        if len({node.node_id for node in value}) != len(value):
            raise ValueError("node_id values must be unique.")
        return value


class EditorIdentityResponseNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: Annotated[str, Field(min_length=1)]
    function_name: Annotated[str, Field(min_length=1)]
    config_reference: Annotated[str, Field(min_length=1)] | None
    default_input_name: Annotated[str, Field(min_length=1)] | None
    source_handle_input_names: dict[
        Annotated[str, Field(min_length=1)],
        Annotated[str, Field(min_length=1)],
    ]


class EditorIdentitiesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identities: list[EditorIdentityResponseNode]

    @field_validator("identities")
    @classmethod
    def _unique_identity_node_ids(
        cls, value: list[EditorIdentityResponseNode]
    ) -> list[EditorIdentityResponseNode]:
        if len({identity.node_id for identity in value}) != len(value):
            raise ValueError("identity node_id values must be unique.")
        return value


RecoveryGraphSnapshot.model_rebuild()


# ---------------------------------------------------------------------------
# /api/pipeline/repair/remove/*
# ---------------------------------------------------------------------------


class PipelineRepairRemoveRequest(BaseModel):
    """Server-identified remove-only repair request shared by dry-run/apply."""

    model_config = ConfigDict(extra="forbid")

    source_file: str = Field(min_length=1)
    source_revision: RevisionToken
    target_source_file: str = Field(min_length=1)
    target_recovery_id: str = Field(min_length=1)
    delete_config: StrictBool = False


class PipelineRepairDryRunRequest(PipelineRepairRemoveRequest):
    """Read-only remove-node planning request."""


class PipelineRepairApplyRequest(PipelineRepairRemoveRequest):
    """Confirmed remove-only plan; replacement bytes never cross the API."""

    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class PipelineRepairChange(BaseModel):
    """Bounded display patch for one server-owned artifact edit."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    operation: Literal["update", "delete"]
    description: str = Field(min_length=1, max_length=1024)
    diff: str = Field(max_length=131_072)
    diff_truncated: bool


class PipelineRepairPlanResponse(BaseModel):
    """Read-only remove-node plan presented for explicit confirmation."""

    model_config = ConfigDict(extra="forbid")

    repair_kind: Literal["remove_unavailable_node"] = "remove_unavailable_node"
    source_file: str = Field(min_length=1)
    source_revision: RevisionToken
    target_source_file: str = Field(min_length=1)
    target_recovery_id: str = Field(min_length=1)
    target_authored_id: str = Field(min_length=1)
    delete_config: bool
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    changes: list[PipelineRepairChange] = Field(min_length=1)
    retained_artifacts: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    predicted_load_status: Literal["ready", "degraded"]


class PipelineRepairApplyResponse(BaseModel):
    """Committed repair plus the newly authoritative editor document."""

    model_config = ConfigDict(extra="forbid")

    repair_kind: Literal["remove_unavailable_node"] = "remove_unavailable_node"
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    applied_artifacts: list[str] = Field(min_length=1)
    document: PipelineEditorDocument


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
    source_revision: RevisionToken
    # Non-fatal warnings surfaced to the UI (e.g. sanitized-name
    # collisions that dropped a node position).  An empty list means
    # "no issues" and callers can rely on truthiness for UX branches.
    warnings: list[str] = Field(default_factory=list)
    # SHA of the ledger commit this save produced, when the clone has a
    # working branch configured; None otherwise. Consumed by the toolbar
    # branch/SHA indicator — the save toast stays git-silent.
    git_sha: str | None = None
    # True when version capture was skipped purely because git has no commit
    # identity (common on a restored hosted container). The UI prompts for a
    # name/email and retries the save; every other capture failure stays a
    # plain warning with this flag False.
    identity_required: bool = False


# ---------------------------------------------------------------------------
# Shared result models
# ---------------------------------------------------------------------------


class SchemaWarning(BaseModel):
    column: str
    status: str


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
    # Frame label selected for a multi-frame target. Single-frame targets
    # ignore it. It is part of the preview cache identity.
    port_label: str | None = None


class RecoveryPreviewRequest(BaseModel):
    """Server-planned preview request for an editor recovery document."""

    model_config = ConfigDict(extra="forbid")

    source_file: str = Field(min_length=1)
    source_revision: RevisionToken
    target_recovery_id: str = Field(min_length=1)
    row_limit: int = Field(default=100, ge=1, le=10000)
    source: str = "live"
    requested_preview_columns: list[str] | None = Field(default=None, min_length=1)
    streaming_chunk_size: StreamingChunkSize = None
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


class OutputDestinationRequest(BaseModel):
    graph: Graph
    node_id: str


class OutputDestinationResponse(BaseModel):
    path: str
    format: str
    suffix_mismatch: bool


class WriteOutputRequest(BaseModel):
    graph: Graph
    node_id: str
    source: str = "live"
    streaming_chunk_size: StreamingChunkSize = None
    overwrite: StrictBool = False


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
    unique_ratio: float | None = None
    is_high_cardinality: bool = False
    is_identifier_candidate: bool = False
    text_min_length: int | None = None
    text_mean_length: float | None = None
    text_max_length: int | None = None
    temporal_span: str | None = None


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
    duplicate_row_count: int | None = None
    duplicate_ratio: float | None = None


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
    refresh: bool = False


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


class ExploreCacheSnapshotResponse(BaseModel):
    state: Literal["missing", "current", "stale"]
    message: str
    result: ExploreCacheReport | None = None


ExplorePivotMemberKind = Literal[
    "null",
    "string",
    "boolean",
    "integer",
    "float",
    "nan",
    "date",
    "datetime",
    "time",
    "decimal",
]
ExplorePivotAggregation = Literal[
    "sum", "count", "average", "min", "max", "median", "distinct_count", "formula"
]


class ExplorePivotFailure(BaseModel):
    reason_code: str
    message: str
    remediation: str
    dimensions: dict[str, str | int] = Field(default_factory=dict)


class ExplorePivotMemberKey(BaseModel):
    kind: ExplorePivotMemberKind
    value: str | float | int | bool | None


class ExplorePivotMemberOption(BaseModel):
    key: ExplorePivotMemberKey
    label: str
    count: int = Field(ge=0)


class ExplorePivotValueIdentity(BaseModel):
    id: str
    field: str
    aggregation: ExplorePivotAggregation


class ExplorePivotPath(BaseModel):
    members: list[ExplorePivotMemberKey] = Field(default_factory=list)
    is_grand_total: bool = False


class ExplorePivotCell(BaseModel):
    row_index: int = Field(ge=0)
    column_index: int = Field(ge=0)
    value_id: str
    value: str | float | int | bool | None = None


class ExplorePivotResult(BaseModel):
    version: Literal[1] = 1
    node_id: str
    pivot_id: str
    source: str = "live"
    dataframe_cache_key: str
    calculation_key: str
    row_fields: list[str] = Field(default_factory=list)
    column_fields: list[str] = Field(default_factory=list)
    values: list[ExplorePivotValueIdentity] = Field(default_factory=list)
    row_paths: list[ExplorePivotPath] = Field(default_factory=list)
    column_paths: list[ExplorePivotPath] = Field(default_factory=list)
    cells: list[ExplorePivotCell] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    generated_at: float = 0.0
    execution_metrics: ExecutionMetricsPayload | None = None


class ExplorePivotRunRequest(BaseModel):
    graph: Graph
    node_id: str
    pivot: dict[str, Any]
    source: str = "live"
    streaming_chunk_size: StreamingChunkSize = None


class ExplorePivotRunResponse(BaseModel):
    status: Literal["started", "completed", "cache_required"]
    job_id: str | None = None
    cached: bool = False
    message: str = ""
    result: ExplorePivotResult | None = None
    failure: ExplorePivotFailure | None = None


class ExplorePivotStatusResponse(BaseModel):
    status: JobStatus
    progress: float = 0.0
    message: str = ""
    result: ExplorePivotResult | None = None
    failure: ExplorePivotFailure | None = None
    terminal_reason: str | None = None
    execution_metrics: ExecutionMetricsPayload | None = None


class ExplorePivotMembersRequest(BaseModel):
    graph: Graph
    node_id: str
    field: str
    source: str = "live"
    search: str | None = None
    streaming_chunk_size: StreamingChunkSize = None


class ExplorePivotMembersResponse(BaseModel):
    status: Literal["ok", "cache_required", "error"]
    field: str | None = None
    members: list[ExplorePivotMemberOption] = Field(default_factory=list)
    failure: ExplorePivotFailure | None = None


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
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    file: str
    node_count: int = 0
    load_status: PipelineLoadStatus
    diagnostic_count: int = Field(default=0, ge=0)


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
    """Request body for ``POST /api/json-cache/{build,status}``.

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
    # than as Pydantic's default 422.
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
    preserved_blocks: list[str] = Field(default_factory=list)
    source_file: str = ""
    base_revision: RevisionToken
    pipeline_name: str = "main"
    pipeline_description: str | None = None


class CreateSubmodelResponse(BaseModel):
    status: str = "ok"
    submodel_file: str = ""
    parent_file: str = ""
    source_revision: RevisionToken
    graph: Graph = Field(default_factory=Graph)


class DissolveSubmodelRequest(BaseModel):
    instance_id: str

    @field_validator("instance_id")
    @classmethod
    def _validate_target_identity(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError("Dissolve target identity must be non-empty and unpadded.")
        return value

    graph: Graph
    preamble: str = ""
    preserved_blocks: list[str] = Field(default_factory=list)
    source_file: str = ""
    base_revision: RevisionToken
    pipeline_name: str = "main"
    pipeline_description: str | None = None


class DissolveSubmodelResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = "ok"
    graph: Graph = Field(default_factory=Graph)
    source_revision: RevisionToken
    instance_id: str
    definition_id: str


class SubmodelGraphResponse(BaseModel):
    status: str = "ok"
    submodel_name: str
    definition_id: str
    submodel_file: str
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
    "evaluation",
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


def _strict_finite_metric_mapping(value: Any, *, field: str) -> dict[str, float]:
    if not isinstance(value, dict) or not value:
        raise ValueError(f"{field} must be a non-empty object")
    metrics: dict[str, float] = {}
    for name, raw_value in value.items():
        if not isinstance(name, str) or not name:
            raise ValueError(f"{field} names must be non-empty strings")
        if (
            isinstance(raw_value, bool)
            or not isinstance(raw_value, int | float)
            or not math.isfinite(float(raw_value))
        ):
            raise ValueError(f"{field}.{name} must be a finite number")
        metrics[name] = float(raw_value)
    return metrics


class _StrictPublicTrainingPayload(BaseModel):
    """Base class for persisted public training/evaluation artifacts."""

    model_config = ConfigDict(extra="forbid")


def _finite_number(value: Any, *, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(float(value))
    ):
        raise ValueError(f"{field} must be a finite number")
    return float(value)


def _finite_json_object(value: Any, *, field: str) -> dict[str, Any]:
    def validate(item: Any, path: str) -> None:
        if item is None or isinstance(item, bool | str | int):
            return
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError(f"{path} must be finite JSON")
            return
        if isinstance(item, list):
            for index, child in enumerate(item):
                validate(child, f"{path}[{index}]")
            return
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ValueError(f"{path} keys must be strings")
                validate(child, f"{path}.{key}")
            return
        raise ValueError(f"{path} must contain only JSON values")

    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    validate(value, field)
    return value


class EvaluationFitPayload(_StrictPublicTrainingPayload):
    schema_version: Literal[1]
    fit_index: int = Field(strict=True, ge=0, le=9)
    train_rows: int = Field(strict=True, ge=1)
    validation_rows: int = Field(strict=True, ge=1)
    metrics: dict[str, float]
    best_iteration: int | None = Field(default=None, strict=True, ge=0)

    @field_validator("metrics", mode="before")
    @classmethod
    def _validate_metrics(cls, value: Any) -> dict[str, float]:
        return _strict_finite_metric_mapping(value, field="evaluation fit metrics")


class EvaluationMetricSummaryPayload(_StrictPublicTrainingPayload):
    mean: float
    stddev: float = Field(ge=0)
    min: float
    max: float
    fit_count: int = Field(strict=True, ge=1)
    validation_rows: int = Field(strict=True, ge=1)

    @field_validator("mean", "stddev", "min", "max", mode="before")
    @classmethod
    def _validate_finite(cls, value: Any) -> float:
        return _finite_number(value, field="evaluation summary value")

    @model_validator(mode="after")
    def _validate_range(self) -> EvaluationMetricSummaryPayload:
        if self.min > self.max:
            raise ValueError("evaluation metric min must not exceed max")
        return self


class EvaluationSummaryPayload(_StrictPublicTrainingPayload):
    development_rows: int = Field(strict=True, ge=1)
    test_rows: int = Field(strict=True, ge=0)
    validation_fit_count: int = Field(strict=True, ge=0, le=10)
    development_group_count: int | None = Field(default=None, strict=True, ge=1)
    test_group_count: int | None = Field(default=None, strict=True, ge=0)
    development_date_count: int | None = Field(default=None, strict=True, ge=1)
    test_date_count: int | None = Field(default=None, strict=True, ge=0)


class EvaluationReportPayload(_StrictPublicTrainingPayload):
    schema_version: Literal[1]
    strategy: Literal["random", "group", "temporal"]
    validation_method: Literal["none", "single", "cross_validation"]
    validation_fit_count: int = Field(strict=True, ge=0, le=10)
    fit_count: int = Field(strict=True, ge=1, le=201)
    development_rows: int = Field(strict=True, ge=1)
    final_test_rows: int = Field(strict=True, ge=0)
    selection_fits: list[EvaluationFitPayload] = Field(max_length=10)
    selection_metrics: dict[str, EvaluationMetricSummaryPayload]
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    results_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_path: str = Field(min_length=1)
    results_path: str = Field(min_length=1)
    report_path: str = Field(min_length=1)
    summary: EvaluationSummaryPayload

    @model_validator(mode="after")
    def _validate_report(self) -> EvaluationReportPayload:
        expected_validation_count = (
            0
            if self.validation_method == "none"
            else 1
            if self.validation_method == "single"
            else self.validation_fit_count
        )
        if self.validation_fit_count != expected_validation_count:
            raise ValueError("validation_fit_count is inconsistent with validation_method")
        if self.validation_method == "cross_validation" and not (
            2 <= self.validation_fit_count <= 10
        ):
            raise ValueError("cross-validation requires 2 to 10 selection fits")
        if len(self.selection_fits) != self.validation_fit_count:
            raise ValueError("validation_fit_count must equal the number of selection_fits")
        if [fit.fit_index for fit in self.selection_fits] != list(range(self.validation_fit_count)):
            raise ValueError("selection fit indices must be contiguous and ascending")
        if self.summary.development_rows != self.development_rows:
            raise ValueError("summary development_rows must equal report development_rows")
        if self.summary.test_rows != self.final_test_rows:
            raise ValueError("summary test_rows must equal report final_test_rows")
        if self.summary.validation_fit_count != self.validation_fit_count:
            raise ValueError("summary validation_fit_count must equal report validation_fit_count")
        strategy_counts = {
            "group": (
                self.summary.development_group_count,
                self.summary.test_group_count,
            ),
            "temporal": (
                self.summary.development_date_count,
                self.summary.test_date_count,
            ),
        }
        active_counts = strategy_counts.get(self.strategy)
        all_counts = (
            self.summary.development_group_count,
            self.summary.test_group_count,
            self.summary.development_date_count,
            self.summary.test_date_count,
        )
        if active_counts is None:
            if any(value is not None for value in all_counts):
                raise ValueError("random evaluation summary must not contain group/date counts")
        else:
            if any(value is None for value in active_counts):
                raise ValueError(f"{self.strategy} evaluation summary requires its strategy counts")
            inactive_counts = all_counts[2:] if self.strategy == "group" else all_counts[:2]
            if any(value is not None for value in inactive_counts):
                raise ValueError(
                    f"{self.strategy} evaluation summary has incompatible strategy counts"
                )
            if bool(self.final_test_rows) != bool(active_counts[1]):
                raise ValueError("evaluation summary test count disagrees with final_test_rows")
        metric_names = set(self.selection_metrics)
        if self.validation_fit_count == 0:
            if metric_names:
                raise ValueError("selection_metrics must be empty without validation")
            return self
        if not metric_names:
            raise ValueError("selection_metrics are required when validation is enabled")
        if any(set(fit.metrics) != metric_names for fit in self.selection_fits):
            raise ValueError("selection fit metric names must exactly match selection_metrics")
        total_rows = sum(fit.validation_rows for fit in self.selection_fits)
        for name, summary in self.selection_metrics.items():
            if summary.fit_count != self.validation_fit_count:
                raise ValueError(f"{name} fit_count must equal validation_fit_count")
            if summary.validation_rows != total_rows:
                raise ValueError(f"{name} validation_rows must equal selection fit row total")
            values = [fit.metrics[name] for fit in self.selection_fits]
            weights = [fit.validation_rows for fit in self.selection_fits]
            mean = (
                sum(value * weight for value, weight in zip(values, weights, strict=True))
                / total_rows
            )
            variance = (
                sum(
                    weight * (value - mean) ** 2
                    for value, weight in zip(values, weights, strict=True)
                )
                / total_rows
            )
            for field, expected in {
                "mean": mean,
                "stddev": math.sqrt(variance),
                "min": min(values),
                "max": max(values),
            }.items():
                if not math.isclose(
                    getattr(summary, field), expected, rel_tol=1e-12, abs_tol=1e-12
                ):
                    raise ValueError(f"{name} {field} does not match the persisted selection fits")
        return self


class TuningTrialPayload(_StrictPublicTrainingPayload):
    schema_version: Literal[1]
    trial_index: int = Field(strict=True, ge=0, le=199)
    label: Literal["baseline", "sampled"]
    sampled_params: dict[str, Any]
    resolved_params: dict[str, Any]
    fits: list[EvaluationFitPayload] = Field(min_length=1, max_length=10)
    aggregate_metrics: dict[str, float]
    objective: float
    elapsed_seconds: float = Field(ge=0)

    @field_validator("aggregate_metrics", mode="before")
    @classmethod
    def _validate_aggregate_metrics(cls, value: Any) -> dict[str, float]:
        return _strict_finite_metric_mapping(value, field="trial aggregate metrics")

    @field_validator("sampled_params", "resolved_params", mode="before")
    @classmethod
    def _validate_params(cls, value: Any, info: Any) -> dict[str, Any]:
        return _finite_json_object(value, field=info.field_name)

    @field_validator("objective", "elapsed_seconds", mode="before")
    @classmethod
    def _validate_finite(cls, value: Any, info: Any) -> float:
        return _finite_number(value, field=info.field_name)


class TuningReportPayload(_StrictPublicTrainingPayload):
    schema_version: Literal[1]
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    trials_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evaluation_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    metric: str = Field(min_length=1)
    direction: Literal["maximize", "minimize"]
    baseline_objective: float
    winner_trial_index: int = Field(strict=True, ge=0, le=199)
    winner_objective: float
    improvement: float = Field(ge=0)
    best_sampled_params: dict[str, Any]
    final_params: dict[str, Any]
    final_tree_count: int = Field(strict=True, ge=1)
    trial_count: int = Field(strict=True, ge=5, le=50)
    trial_fit_count: int = Field(strict=True, ge=5, le=200)
    total_fit_count: int = Field(strict=True, ge=6, le=201)
    trials: list[TuningTrialPayload] = Field(min_length=5, max_length=50)
    plan_path: str = Field(min_length=1)
    trials_path: str = Field(min_length=1)
    report_path: str = Field(min_length=1)

    @field_validator("baseline_objective", "winner_objective", "improvement", mode="before")
    @classmethod
    def _validate_finite(cls, value: Any, info: Any) -> float:
        return _finite_number(value, field=info.field_name)

    @field_validator("best_sampled_params", "final_params", mode="before")
    @classmethod
    def _validate_params(cls, value: Any, info: Any) -> dict[str, Any]:
        return _finite_json_object(value, field=info.field_name)

    @model_validator(mode="after")
    def _validate_report(self) -> TuningReportPayload:
        from haute.modelling._tuning import metric_direction

        try:
            expected_direction = metric_direction(self.metric)
        except ValueError as exc:
            raise ValueError(f"tuning metric direction is unsupported: {exc}") from exc
        if self.direction != expected_direction:
            raise ValueError(
                f"tuning metric direction must be {expected_direction} for {self.metric!r}"
            )
        if len(self.trials) != self.trial_count:
            raise ValueError("trial_count must equal the number of trials")
        if [trial.trial_index for trial in self.trials] != list(range(self.trial_count)):
            raise ValueError("trial indices must be contiguous and ascending")
        baseline = self.trials[0]
        if baseline.label != "baseline" or baseline.sampled_params:
            raise ValueError("trial 0 must be the baseline with empty sampled_params")
        if any(trial.label != "sampled" or not trial.sampled_params for trial in self.trials[1:]):
            raise ValueError("trials after baseline must contain sampled parameters")
        for trial in self.trials:
            expected_resolved = dict(baseline.resolved_params)
            expected_resolved.update(trial.sampled_params)
            if trial.resolved_params != expected_resolved:
                raise ValueError(
                    "trial resolved parameters must equal baseline plus sampled parameters"
                )
        if any(
            set(trial.aggregate_metrics) != set(baseline.aggregate_metrics) for trial in self.trials
        ):
            raise ValueError("trial aggregate metric names must exactly match")
        fit_count = len(baseline.fits)
        if any(
            len(trial.fits) != fit_count
            or [fit.fit_index for fit in trial.fits] != list(range(fit_count))
            for trial in self.trials
        ):
            raise ValueError("each tuning trial must use the same contiguous evaluation fits")
        aggregate_metric_names = set(baseline.aggregate_metrics)
        for trial in self.trials:
            if any(set(fit.metrics) != aggregate_metric_names for fit in trial.fits):
                raise ValueError("tuning trial fit metric names must match aggregate metrics")
            total_validation_rows = sum(fit.validation_rows for fit in trial.fits)
            for name, aggregate in trial.aggregate_metrics.items():
                weighted_mean = (
                    sum(fit.metrics[name] * fit.validation_rows for fit in trial.fits)
                    / total_validation_rows
                )
                if not math.isclose(
                    aggregate,
                    weighted_mean,
                    rel_tol=1e-12,
                    abs_tol=1e-12,
                ):
                    raise ValueError(
                        f"trial aggregate metric {name!r} does not match its validation fits"
                    )
        if self.metric not in baseline.aggregate_metrics:
            raise ValueError("tuning metric must be present in aggregate_metrics")
        if any(
            not math.isclose(
                trial.aggregate_metrics[self.metric], trial.objective, rel_tol=1e-12, abs_tol=1e-12
            )
            for trial in self.trials
        ):
            raise ValueError("trial objective must equal its aggregate metric")
        if not math.isclose(
            self.baseline_objective, baseline.objective, rel_tol=1e-12, abs_tol=1e-12
        ):
            raise ValueError("baseline_objective must equal the baseline objective")
        winner = (
            max(self.trials, key=lambda trial: (trial.objective, -trial.trial_index))
            if self.direction == "maximize"
            else min(self.trials, key=lambda trial: (trial.objective, trial.trial_index))
        )
        if self.winner_trial_index != winner.trial_index or not math.isclose(
            self.winner_objective, winner.objective, rel_tol=1e-12, abs_tol=1e-12
        ):
            raise ValueError("winner must be selected deterministically from trial objectives")
        if self.best_sampled_params != winner.sampled_params:
            raise ValueError(
                "best sampled parameters must equal the winning trial sampled parameters"
            )
        iteration_ceiling = winner.resolved_params.get("iterations", 1000)
        if (
            isinstance(iteration_ceiling, bool)
            or not isinstance(iteration_ceiling, int)
            or iteration_ceiling <= 0
            or any(fit.best_iteration is None for fit in winner.fits)
        ):
            raise ValueError(
                "winning trial must retain a positive iteration ceiling and "
                "best_iteration for every fit"
            )
        weighted_tree_counts = sorted(
            (
                fit.best_iteration + 1,
                fit.validation_rows,
            )
            for fit in winner.fits
            if fit.best_iteration is not None
        )
        threshold = sum(rows for _, rows in weighted_tree_counts) / 2
        selected_tree_count = next(
            tree_count
            for index, (tree_count, _) in enumerate(weighted_tree_counts)
            if sum(rows for _, rows in weighted_tree_counts[: index + 1]) >= threshold
        )
        expected_tree_count = min(selected_tree_count, iteration_ceiling)
        expected_final_params = dict(winner.resolved_params)
        for key in (
            "early_stopping_rounds",
            "od_pval",
            "od_type",
            "od_wait",
            "use_best_model",
        ):
            expected_final_params.pop(key, None)
        expected_final_params["iterations"] = expected_tree_count
        if (
            self.final_tree_count != expected_tree_count
            or self.final_params != expected_final_params
        ):
            raise ValueError(
                "final parameter projection must be derived from the winning validation fits"
            )
        expected_improvement = (
            winner.objective - baseline.objective
            if self.direction == "maximize"
            else baseline.objective - winner.objective
        )
        if not math.isclose(self.improvement, expected_improvement, rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError("improvement must equal winner versus baseline")
        if self.trial_fit_count != sum(len(trial.fits) for trial in self.trials):
            raise ValueError("trial_fit_count must equal all trial fits")
        if self.total_fit_count != self.trial_fit_count + 1:
            raise ValueError("total_fit_count must equal trial_fit_count + final fit")
        return self


class TrainResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["started", "completed", "error"]
    job_id: str | None = None
    diagnostic_metrics: dict[str, float] = Field(default_factory=dict)
    final_test_metrics: dict[str, float] = Field(default_factory=dict)
    feature_importance: list[dict[str, Any]] = Field(default_factory=list)
    model_path: str = ""
    development_rows: int = Field(default=0, strict=True, ge=0)
    final_test_rows: int = Field(default=0, strict=True, ge=0)
    diagnostics_set: Literal["development", "final_test"] = "development"
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
    evaluation: EvaluationReportPayload | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    tuning: TuningReportPayload | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )

    @field_validator("diagnostic_metrics", "final_test_metrics", mode="before")
    @classmethod
    def _validate_public_metrics(cls, value: Any, info: Any) -> dict[str, float]:
        # Non-completed responses serialise their empty defaults, so a dumped
        # response must re-validate; completed-status non-emptiness lives in
        # the model validator below.
        if value == {}:
            return {}
        return _strict_finite_metric_mapping(value, field=info.field_name)

    @model_validator(mode="after")
    def _validate_evaluation_status(self) -> TrainResponse:
        if self.status != "completed":
            if self.evaluation is not None or self.tuning is not None:
                raise ValueError("evaluation and tuning are present only for completed training")
            return self
        if self.evaluation is None:
            raise ValueError("completed training requires evaluation")
        if not self.diagnostic_metrics:
            raise ValueError("completed training requires diagnostic_metrics")
        if self.development_rows != self.evaluation.development_rows:
            raise ValueError("development_rows must equal evaluation development_rows")
        if self.final_test_rows != self.evaluation.final_test_rows:
            raise ValueError("final_test_rows must equal evaluation final_test_rows")
        if self.final_test_rows:
            if self.diagnostic_metrics != self.final_test_metrics:
                raise ValueError(
                    "diagnostic_metrics must equal final_test_metrics when a final test exists"
                )
            if self.diagnostics_set != "final_test":
                raise ValueError(
                    "completed training diagnostics_set must be final_test when a test exists"
                )
        else:
            if self.final_test_metrics:
                raise ValueError("final_test_metrics must be empty without a final test")
            if self.diagnostics_set != "development":
                raise ValueError(
                    "completed training diagnostics_set must be development without a test"
                )
        if self.tuning is None:
            if self.evaluation.fit_count != self.evaluation.validation_fit_count + 1:
                raise ValueError("evaluation fit_count must equal validation_fit_count + final fit")
        else:
            if self.evaluation.fit_count != self.tuning.total_fit_count:
                raise ValueError("evaluation fit_count must equal tuning total_fit_count")
            if self.tuning.evaluation_plan_sha256 != self.evaluation.plan_sha256:
                raise ValueError("tuning evaluation plan digest must match evaluation")
        return self


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
    error_code: str | None = None
    http_status_code: int | None = None
    error_detail: Any | None = None
    phase: (
        Literal[
            "planning",
            "trial_fit",
            "trial_complete",
            "final_fit",
            "publication",
            "completed",
        ]
        | None
    ) = None
    trial_index: int | None = Field(default=None, strict=True, ge=1)
    trial_count: int | None = Field(default=None, strict=True, ge=5, le=50)
    fold_index: int | None = Field(default=None, strict=True, ge=1)
    fold_count: int | None = Field(default=None, strict=True, ge=1, le=10)
    completed_fits: int | None = Field(default=None, strict=True, ge=0)
    total_fits: int | None = Field(default=None, strict=True, ge=1, le=201)
    best_objective: float | None = None

    @field_validator("best_objective", mode="before")
    @classmethod
    def _validate_best_objective(cls, value: Any) -> float | None:
        return None if value is None else _finite_number(value, field="best_objective")

    @model_validator(mode="after")
    def _validate_tuning_progress(self) -> TrainStatusResponse:
        values = (
            self.phase,
            self.trial_index,
            self.trial_count,
            self.fold_index,
            self.fold_count,
            self.completed_fits,
            self.total_fits,
            self.best_objective,
        )
        if self.phase is None:
            if any(value is not None for value in values[1:]):
                raise ValueError("tuning progress fields require phase")
            return self
        if any(
            value is None
            for value in (
                self.trial_count,
                self.fold_count,
                self.completed_fits,
                self.total_fits,
            )
        ):
            raise ValueError("tuning progress count fields are required with phase")
        assert self.trial_count is not None
        assert self.fold_count is not None
        assert self.completed_fits is not None and self.total_fits is not None
        if self.phase == "trial_fit":
            if self.trial_index is None or self.fold_index is None:
                raise ValueError("trial_fit tuning progress requires trial and fold indices")
        elif self.phase == "trial_complete":
            if self.trial_index is None or self.fold_index is not None:
                raise ValueError("trial_complete progress requires only a trial index")
        elif self.trial_index is not None or self.fold_index is not None:
            raise ValueError(f"{self.phase} progress must not contain trial/fold indices")
        if (self.trial_index is not None and self.trial_index > self.trial_count) or (
            self.fold_index is not None and self.fold_index > self.fold_count
        ):
            raise ValueError("tuning progress index must be within its count")
        if self.completed_fits > self.total_fits:
            raise ValueError("tuning progress completed_fits must not exceed total_fits")
        if self.total_fits != self.trial_count * self.fold_count + 1:
            raise ValueError(
                "tuning progress total_fits must equal trial_count * fold_count + final fit"
            )
        return self


class TrainEstimateRequest(BaseModel):
    graph: Graph
    node_id: str
    source: str = "live"


class EvaluationDateRangePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: str = Field(min_length=1)
    end: str = Field(min_length=1)


class EvaluationPreviewPayload(BaseModel):
    """Bounded, result-free summary of the exact preflight evaluation plan."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    strategy: Literal["random", "group", "temporal"]
    validation_method: Literal["none", "single", "cross_validation"]
    development_rows: int = Field(strict=True, ge=1)
    final_test_rows: int = Field(strict=True, ge=0)
    validation_fit_count: int = Field(strict=True, ge=0, le=10)
    min_selection_train_rows: int | None = Field(
        default=None,
        strict=True,
        ge=1,
        exclude_if=lambda value: value is None,
    )
    max_selection_train_rows: int | None = Field(
        default=None,
        strict=True,
        ge=1,
        exclude_if=lambda value: value is None,
    )
    min_selection_validation_rows: int | None = Field(
        default=None,
        strict=True,
        ge=1,
        exclude_if=lambda value: value is None,
    )
    max_selection_validation_rows: int | None = Field(
        default=None,
        strict=True,
        ge=1,
        exclude_if=lambda value: value is None,
    )
    development_group_count: int | None = Field(
        default=None,
        strict=True,
        ge=1,
        exclude_if=lambda value: value is None,
    )
    final_test_group_count: int | None = Field(
        default=None,
        strict=True,
        ge=0,
        exclude_if=lambda value: value is None,
    )
    development_date_range: EvaluationDateRangePayload | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    final_test_date_range: EvaluationDateRangePayload | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def _validate_preview_shape(self) -> EvaluationPreviewPayload:
        min_train_rows = self.min_selection_train_rows
        max_train_rows = self.max_selection_train_rows
        min_validation_rows = self.min_selection_validation_rows
        max_validation_rows = self.max_selection_validation_rows
        selection_bounds = (
            min_train_rows,
            max_train_rows,
            min_validation_rows,
            max_validation_rows,
        )
        if self.validation_method == "none":
            if self.validation_fit_count != 0 or any(
                value is not None for value in selection_bounds
            ):
                raise ValueError("no-validation preview must not contain selection bounds")
        else:
            if self.validation_method == "single" and self.validation_fit_count != 1:
                raise ValueError("single-validation preview requires exactly one fit")
            if self.validation_method == "cross_validation" and not (
                2 <= self.validation_fit_count <= 10
            ):
                raise ValueError("cross-validation preview requires 2 to 10 fits")
            if any(value is None for value in selection_bounds):
                raise ValueError("validated preview requires all selection row bounds")
            if cast(int, min_train_rows) > cast(int, max_train_rows) or cast(
                int, min_validation_rows
            ) > cast(int, max_validation_rows):
                raise ValueError("evaluation preview minimums must not exceed maximums")

        if self.strategy == "group":
            if self.development_group_count is None or self.final_test_group_count is None:
                raise ValueError("group preview requires group counts")
            if bool(self.final_test_rows) != bool(self.final_test_group_count):
                raise ValueError("group final-test rows and group count must agree")
        elif self.development_group_count is not None or self.final_test_group_count is not None:
            raise ValueError("only group preview may contain group counts")

        if self.strategy == "temporal":
            if self.development_date_range is None:
                raise ValueError("temporal preview requires a development date range")
            if bool(self.final_test_rows) != bool(self.final_test_date_range):
                raise ValueError("temporal final-test rows and date range must agree")
        elif self.development_date_range is not None or self.final_test_date_range is not None:
            raise ValueError("only temporal preview may contain date ranges")
        return self


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
    evaluation_preview: EvaluationPreviewPayload | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )


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
    mlflow_importable: bool
    tracking_configured: bool
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
# Working-branch selection (P2): the per-clone working-branch association and
# the readiness signal the startup flow + toolbar indicator consume.
# ---------------------------------------------------------------------------

GitWorkingBranchState = Literal[
    "git-unavailable",
    "no-repository",
    "unset",
    "detached",
    "invalid",
    "divergent",
    "ready",
]


GitStorageState = Literal["unsupported", "unbound", "bound"]
GitSyncState = Literal["synced", "pending", "failed"]
GitSyncFailure = Literal["transport", "rejected", "config"]
GitBindState = Literal["idle", "running", "succeeded", "failed"]


class GitStorageClaim(BaseModel):
    """Who holds a uc:// location's lease.

    Steering, not stonewalling: the UI names the holder and offers the
    two ways forward (bind elsewhere, or fork the location).
    """

    app_name: str
    user: str | None = None
    refreshed_at: str | None = None
    message: str


class GitStorageBind(BaseModel):
    """Progress of a bind running in the background.

    A bind publishes the whole project, so the dialog closes as soon as
    the instant checks pass and the outcome arrives here instead.
    """

    state: GitBindState = "idle"
    outcome: Literal["adopted", "restart-required"] | None = None
    # Hand-authored failure prose; never raw library text.
    message: str | None = None
    # Set when the failure was a held uc:// location, so the dialog can
    # name the holder and offer to fork.
    claim: GitStorageClaim | None = None
    remote_url: str | None = None


class GitStorageSync(BaseModel):
    """Publication state of a hosted project's durable storage.

    Carries counts and a failure CLASS plus a hand-authored message — never
    raw git stderr, which embeds remote URLs and credential material.
    """

    state: GitSyncState = "synced"
    # Commits made locally but not yet published.
    pending: int = 0
    failure: GitSyncFailure | None = None
    message: str | None = None


class GitWorkingBranchResponse(BaseModel):
    # The branch recorded against this clone in .haute/state.json, or None.
    working_branch: str | None = None
    # Durable-storage binding for hosted sessions; "unsupported" everywhere a
    # binding cannot be remembered (every local session), which hides the
    # storage surface rather than offering an action that cannot work.
    storage: GitStorageState = "unsupported"
    # The bound remote's URL, for display beside the sync state.
    storage_remote: str | None = None
    # Parent uc:// URL when the bound location is a fork — provenance
    # signposting, read once at bind/restore and cached off the Files API.
    storage_forked_from: str | None = None
    sync: GitStorageSync | None = None
    # Progress of a bind running in the background, so the dialog can close
    # immediately and still report what happened.
    storage_bind: GitStorageBind | None = None
    # Drives whether the startup modal fires (S27) and which variant (S14).
    state: GitWorkingBranchState = "unset"
    # Human-readable reasons when state is "invalid" (check_invariants output
    # or eligibility failure).
    errors: list[str] = Field(default_factory=list)
    # HEAD's attached branch; empty when the repository is absent or detached.
    current_branch: str = ""
    # Full HEAD commit when it can be resolved (especially detached state).
    head_sha: str | None = None
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


class GitBindStorageRequest(BaseModel):
    # HTTPS repository URL; credentials come from the app's secret, never here.
    remote_url: str


class GitBindStorageResponse(BaseModel):
    # Always "pending": the instant checks passed and the network work —
    # claim, inspect, publish, record — now runs in the background. The
    # real outcome ("adopted" or "restart-required") arrives on the
    # readiness response's `storage_bind`, so the dialog never has to hold
    # the session open across a whole-project publish.
    outcome: Literal["pending"] = "pending"
    remote_url: str
    message: str


class GitForkStorageRequest(BaseModel):
    # Both uc:// locations; the target must be empty.
    source_url: str
    target_url: str


class GitForkStorageResponse(BaseModel):
    outcome: Literal["forked"] = "forked"
    target_url: str
    parent_url: str
    # Which published generation of the parent the fork copied.
    parent_generation: int
    message: str


class GitSetWorkingBranchRequest(BaseModel):
    branch: str
    # Create the branch off current HEAD before adopting it.
    create: bool = False


class GitSetWorkingBranchResponse(BaseModel):
    working_branch: str
    state: GitWorkingBranchState
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
    # first-parent spines): the newest spine commit already
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
    # behind-remote surface (P7). `working`/`ledger` carry the per-leg structured
    # state (F6):
    # ledger divergence — the two-machine save accident — is visible, not just the
    # working leg. Read from locally-known remote refs (a throttled pair fetch
    # freshens them first); null when no working branch is set.
    name: str
    url: str | None = None
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


class GitUpstreamStatusResponse(BaseModel):
    # A fork's measured relationship to the parent it was forked from.
    # `working`/`ledger` carry the same per-leg honesty as every other
    # divergence surface; `can_fast_forward` is the single predicate the
    # catch-up affordance keys on, and `message` is hand-authored prose safe to
    # surface verbatim.
    parent_url: str
    # Which published generation of the parent the comparison was made against.
    parent_generation: int
    working: GitRemoteLeg
    ledger: GitRemoteLeg
    can_fast_forward: bool
    checked_at: str
    message: str


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
    cache_mode: Literal["direct", "snapshot"]
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
