"""Route-side evaluation preview and GLM-dispersion contracts."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from fastapi import HTTPException

from haute._logging import get_logger
from haute.errors import HauteValidationError
from haute.modelling._evaluation import (
    EvaluationPlan,
)
from haute.modelling._train_config import build_train_params, validate_glm_params

logger = get_logger(component="server.modelling.train")

# Dispersion estimation profiles the likelihood on a bounded sample: each
# candidate is a full GLM fit, so the row cap bounds the per-candidate cost.
# 200k rows pins a single dispersion scalar far tighter than the search's own
# tolerance.
_DISPERSION_ESTIMATE_ROW_CAP = 200_000

# Which GLM family owns each estimable dispersion parameter.
_DISPERSION_PARAM_FAMILIES = {"theta": "negbinomial", "var_power": "tweedie"}
# Stub value injected so config machinery built for complete objectives
# (training_objective_issue, build_training_job_kwargs) can run while the
# parameter is still the one being estimated. Never reaches a fit: the
# profile search overrides the parameter at every candidate.
_DISPERSION_PARAM_STUBS = {"theta": 1.0, "var_power": 1.5}


def _validate_glm_config_values(config: Mapping[str, Any]) -> None:
    """Raise HTTPException(400) when a GLM config holds invalid values."""
    try:
        validate_glm_params(build_train_params(config))
    except HauteValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _evaluation_preview_payload(
    plan: EvaluationPlan,
    *,
    date_values: list[str] | None = None,
) -> dict[str, Any]:
    """Project an exact plan into the bounded public preflight summary."""
    payload: dict[str, Any] = {
        "schema_version": 1,
        "strategy": plan.config.strategy,
        "validation_method": plan.config.validation["method"],
        "development_rows": len(plan.development_positions),
        "final_test_rows": len(plan.test_positions),
        "validation_fit_count": len(plan.validation_fits),
    }
    if plan.validation_fits:
        train_rows = [fit.train_rows for fit in plan.validation_fits]
        validation_rows = [fit.validation_rows for fit in plan.validation_fits]
        payload.update(
            {
                "min_selection_train_rows": min(train_rows),
                "max_selection_train_rows": max(train_rows),
                "min_selection_validation_rows": min(validation_rows),
                "max_selection_validation_rows": max(validation_rows),
            }
        )
    if plan.config.strategy == "group":
        payload.update(
            {
                "development_group_count": plan.summary["development_group_count"],
                "final_test_group_count": plan.summary["test_group_count"],
            }
        )
    if plan.config.strategy == "temporal":
        if date_values is None or len(date_values) != plan.row_count:
            raise HauteValidationError("temporal evaluation preview requires exact date values")

        def date_range(positions: tuple[int, ...]) -> dict[str, str] | None:
            if not positions:
                return None
            values = [date_values[position] for position in positions]
            ordered = sorted(
                values,
                key=lambda value: datetime.fromisoformat(value.replace("Z", "+00:00")),
            )
            return {"start": ordered[0], "end": ordered[-1]}

        payload["development_date_range"] = date_range(plan.development_positions)
        final_test_range = date_range(plan.test_positions)
        if final_test_range is not None:
            payload["final_test_date_range"] = final_test_range
    return payload
