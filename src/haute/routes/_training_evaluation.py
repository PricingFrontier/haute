"""Route-side evaluation preview and GLM-dispersion contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import HTTPException

from haute._logging import get_logger
from haute.errors import HauteValidationError
from haute.modelling._evaluation import (
    EvaluationPlan,
)

logger = get_logger(component="server.modelling.train")

_DISPERSION_ESTIMATE_ROW_CAP = 200_000
_DISPERSION_PARAM_FAMILIES = {"theta": "negbinomial", "var_power": "tweedie"}
_DISPERSION_PARAM_STUBS = {"theta": 1.0, "var_power": 1.5}
_VALID_GLM_LINKS: dict[str, tuple[str, ...]] = {
    "gaussian": ("identity", "log", "inverse"),
    "binomial": ("logit", "probit", "cloglog"),
    "poisson": ("log", "identity", "sqrt"),
    "quasipoisson": ("log", "identity"),
    "negbinomial": ("log", "identity"),
    "gamma": ("inverse", "log", "identity"),
    "tweedie": ("log", "identity"),
    "inverse_gaussian": ("inverse_squared", "inverse", "log", "identity"),
}


# cost per candidate. 200k rows pins a single dispersion scalar far tighter
# than the search's own tolerance.
_DISPERSION_ESTIMATE_ROW_CAP = 200_000

# Which GLM family owns each estimable dispersion parameter.
_DISPERSION_PARAM_FAMILIES = {"theta": "negbinomial", "var_power": "tweedie"}
# Stub value injected so config machinery built for complete objectives
# (training_objective_issue, build_training_job_kwargs) can run while the
# parameter is still the one being estimated. Never reaches a fit: the
# profile search overrides the parameter at every candidate.
_DISPERSION_PARAM_STUBS = {"theta": 1.0, "var_power": 1.5}


def _validate_glm_family_link(family: str, link: str) -> None:
    """Raise HTTPException(400) if the family is unset or the combination invalid."""
    if not family:
        raise HTTPException(
            status_code=400,
            detail=(
                "No GLM family selected. Open the config panel and choose a "
                "distribution family explicitly (e.g. poisson for claim counts, "
                "gamma for severity) — an unset family would silently train a "
                "gaussian model."
            ),
        )
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
