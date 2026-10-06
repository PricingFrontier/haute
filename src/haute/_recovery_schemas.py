"""Engine field-outcome and issue types shared by recovery reconciliation."""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator


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
