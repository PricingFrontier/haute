"""Recovery drafts are editable proposals, never executable graph payloads."""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from haute.schemas import PipelineEditorDocument, PipelineRepairChange


class RecoveryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    @model_validator(mode="before")
    @classmethod
    def _bounded_finite_json(cls, value: Any) -> Any:
        pending = [(value, 0)]
        count = 0
        while pending:
            item, depth = pending.pop()
            count += 1
            if depth > 64 or count > 100_000:
                raise ValueError("Recovery JSON exceeds its depth or item limit.")
            if isinstance(item, float) and not math.isfinite(item):
                raise ValueError("Recovery settings require finite JSON numbers.")
            if isinstance(item, dict):
                pending.extend((child, depth + 1) for child in item.values())
            elif isinstance(item, (list, tuple)):
                pending.extend((child, depth + 1) for child in item)
        return value


class RecoveryFieldChange(RecoveryModel):
    path: str
    outcome: Literal["retained", "defaulted", "needs_input", "removed", "needs_review", "blocked"]
    reason: str


class RecoveryIssue(RecoveryModel):
    path: str = ""
    code: str
    message: str
    severity: Literal["error", "warning"] = "error"


class RecoveryTarget(RecoveryModel):
    source_file: str = Field(min_length=1)
    recovery_id: str = Field(min_length=1)


class RecoveryDraftCreate(RecoveryModel):
    source_file: str = Field(min_length=1)
    source_revision: str = Field(min_length=1)
    targets: list[RecoveryTarget] = Field(min_length=1, max_length=100)
    mode: Literal["recover", "reset"] = "recover"


class RecoveryDraftNode(RecoveryModel):
    key: str
    source_file: str
    recovery_id: str
    authored_id: str
    label: str
    node_type: str | None
    config: dict[str, JsonValue]
    changes: list[RecoveryFieldChange]
    issues: list[RecoveryIssue]
    input_names: list[str] = Field(default_factory=list)
    affected_owners: list[str] = Field(default_factory=list)
    editable: bool = True


class RecoveryDraft(RecoveryModel):
    document_kind: Literal["haute.recovery_draft"] = "haute.recovery_draft"
    schema_version: Literal[1] = 1
    draft_id: str
    draft_revision: str
    source_file: str
    source_revision: str
    contract_fingerprint: str
    mode: Literal["recover", "reset"]
    state: Literal[
        "needs_configuration",
        "review_required",
        "ready_to_apply",
        "stale",
        "applying",
        "applied",
        "discarded",
        "manual_action",
        "restored",
    ]
    nodes: list[RecoveryDraftNode]
    issues: list[RecoveryIssue] = Field(default_factory=list)
    reviewed: bool = False
    updated_at: str


class RecoveryDraftPatch(RecoveryModel):
    draft_revision: str
    configs: dict[str, dict[str, JsonValue]] = Field(default_factory=dict)
    reviewed: bool = False


class RecoveryDraftRevision(RecoveryModel):
    draft_revision: str


class RecoveryDraftPreview(RecoveryModel):
    draft: RecoveryDraft
    plan_hash: str | None = None
    changes: list[PipelineRepairChange] = Field(default_factory=list)
    issues: list[RecoveryIssue] = Field(default_factory=list)
    predicted_load_status: Literal["ready", "degraded"] | None = None


class RecoveryDraftApply(RecoveryDraftRevision):
    source_revision: str
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    operation_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{8,100}$")


class RecoveryDraftApplyResponse(RecoveryModel):
    draft: RecoveryDraft
    document: PipelineEditorDocument
    applied_artifacts: list[str]


class RecoveryDraftList(RecoveryModel):
    drafts: list[RecoveryDraft]


class RecoveryConfigContracts(RecoveryModel):
    fingerprint: str
    schemas: dict[str, dict[str, Any]]
