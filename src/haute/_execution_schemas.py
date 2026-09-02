from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StrictInt, model_validator

from haute._estimate_calibration import (
    CALIBRATION_BASE_BASIS_POINTS,
    CALIBRATION_MAX_BASIS_POINTS,
)

DiagnosticCollectionState = Literal["available", "unavailable", "truncated"]
MAX_JSON_SAFE_INTEGER = 2**53 - 1
JsonSafeNonNegativeInt = Annotated[
    StrictInt,
    Field(ge=0, le=MAX_JSON_SAFE_INTEGER),
]
CalibrationFactorBasisPoints = Annotated[
    StrictInt,
    Field(ge=0, le=CALIBRATION_MAX_BASIS_POINTS),
]


class ExecutionStrategyBoundaryPayload(BaseModel):
    topological_rank: JsonSafeNonNegativeInt
    node_id: str
    operator: str
    boundary_kind: Literal[
        "unprojected-streaming-boundary",
        "materialisation-boundary",
    ]


class ExecutionStrategyReasonPayload(BaseModel):
    reason_code: str
    topological_rank: JsonSafeNonNegativeInt | None = None
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
    total_count: JsonSafeNonNegativeInt | None
    items: list[ExecutionStrategyBoundaryPayload] = Field(max_length=32)

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
    total_count: JsonSafeNonNegativeInt | None
    items: list[ExecutionStrategyReasonPayload] = Field(max_length=32)

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
    total_count: JsonSafeNonNegativeInt | None
    items: list[ExecutionStrategyProvenancePayload] = Field(max_length=128)

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


def _validate_calibrated_estimate_evidence(
    *,
    estimated_bytes: int | None,
    raw_estimated_bytes: int | None,
    factor_basis_points: int | None,
    admission_basis: str | None,
) -> None:
    evidence = (raw_estimated_bytes, factor_basis_points, admission_basis)
    if not any(value is not None for value in evidence):
        return
    if estimated_bytes is None or any(value is None for value in evidence):
        raise ValueError(
            "calibrated estimate evidence requires estimated/raw bytes, factor, "
            "and admission basis together"
        )
    assert raw_estimated_bytes is not None and factor_basis_points is not None
    if factor_basis_points < CALIBRATION_BASE_BASIS_POINTS:
        raise ValueError("estimate calibration factor cannot reduce an estimate")
    if factor_basis_points > CALIBRATION_MAX_BASIS_POINTS:
        raise ValueError("estimate calibration factor exceeds the supported cap")
    expected = (
        raw_estimated_bytes * factor_basis_points + CALIBRATION_BASE_BASIS_POINTS - 1
    ) // CALIBRATION_BASE_BASIS_POINTS
    if estimated_bytes != expected:
        raise ValueError("calibrated estimate bytes do not match raw bytes and factor")


class ExecutionStrategyDiagnosticPayload(BaseModel):
    """Strict V1 API DTO for one shared execution-planning decision."""

    schema_version: Literal[1]
    status: Literal[
        "projected",
        "admitted_eager",
        "boundary",
        "warned",
        "rejected",
        "not_planned",
    ]
    strategy: Literal[
        "projected",
        "schema-all-except",
        "full-width-admitted-eager",
        "unprojected-streaming-boundary",
        "materialisation-boundary",
        "full-width-conservative",
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
    estimated_peak_bytes: JsonSafeNonNegativeInt | None = None
    raw_estimated_peak_bytes: JsonSafeNonNegativeInt | None = None
    estimate_calibration_factor_basis_points: CalibrationFactorBasisPoints | None = None
    estimate_admission_basis: (
        Literal[
            "provided",
            "projected_columns",
            "complete_width_fallback",
        ]
        | None
    ) = None
    headroom_bytes: JsonSafeNonNegativeInt | None = None
    assumptions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_strategy_contract(self) -> ExecutionStrategyDiagnosticPayload:
        expected_status = {
            "projected": "projected",
            "schema-all-except": "projected",
            "full-width-admitted-eager": "admitted_eager",
            "unprojected-streaming-boundary": "boundary",
            "materialisation-boundary": "boundary",
            "full-width-conservative": "warned",
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
        _validate_calibrated_estimate_evidence(
            estimated_bytes=self.estimated_peak_bytes,
            raw_estimated_bytes=self.raw_estimated_peak_bytes,
            factor_basis_points=self.estimate_calibration_factor_basis_points,
            admission_basis=self.estimate_admission_basis,
        )
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


class ExecutionCacheProofMissReasonCountsPayload(BaseModel):
    metadata_source_mismatch: int = Field(ge=0)
    artifact_integrity_schema_failure: int = Field(ge=0)
    unreadable_artifact: int = Field(ge=0)
    proof_unavailable: int = Field(ge=0)


class ExecutionCacheProofPayload(BaseModel):
    hits: int = Field(ge=0)
    misses: int = Field(ge=0)
    direct_fallbacks: int = Field(ge=0)
    miss_reason_counts: ExecutionCacheProofMissReasonCountsPayload

    @model_validator(mode="after")
    def _validate_miss_total(self) -> ExecutionCacheProofPayload:
        reason_total = sum(
            (
                self.miss_reason_counts.metadata_source_mismatch,
                self.miss_reason_counts.artifact_integrity_schema_failure,
                self.miss_reason_counts.unreadable_artifact,
                self.miss_reason_counts.proof_unavailable,
            )
        )
        if self.misses != reason_total:
            raise ValueError("cache proof misses must equal the closed reason-count total")
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
    streamability: Literal["streaming", "materialising"] | None = None
    streamability_evidence: ExecutionStreamabilityEvidencePayload = Field(
        default_factory=ExecutionStreamabilityEvidencePayload
    )
    column_widths: ExecutionColumnWidthsCollectionPayload = Field(
        default_factory=ExecutionColumnWidthsCollectionPayload
    )
    requested_column_width_total: int | None = Field(default=None, ge=0)
    physically_scanned_column_width_total: int | None = Field(default=None, ge=0)
    cache_proof: ExecutionCacheProofPayload
    bytes_read: int | None = Field(default=None, ge=0)
    bytes_written: int | None = Field(default=None, ge=0)
    estimated_bytes: StrictInt | None = Field(default=None, ge=0)
    raw_estimated_bytes: StrictInt | None = Field(default=None, ge=0)
    estimate_calibration_factor_basis_points: StrictInt | None = Field(default=None, ge=0)
    estimate_admission_basis: (
        Literal[
            "provided",
            "projected_columns",
            "complete_width_fallback",
        ]
        | None
    ) = None
    checkpoint_count: int = Field(default=0, ge=0)
    chunk_count: int = Field(default=0, ge=0)
    observed_peak_rss_bytes: int | None = Field(default=None, ge=0)
    observed_peak_rss_growth_bytes: int | None = Field(default=None, ge=0)
    cancellation_latency_ms: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _validate_calibration_evidence(self) -> ExecutionMetricsPayload:
        _validate_calibrated_estimate_evidence(
            estimated_bytes=self.estimated_bytes,
            raw_estimated_bytes=self.raw_estimated_bytes,
            factor_basis_points=self.estimate_calibration_factor_basis_points,
            admission_basis=self.estimate_admission_basis,
        )
        return self


NodeExecutionStatus = Literal["ok", "error"]
