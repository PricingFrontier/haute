"""Waterfall data generation for sequential multiplicative/additive rating steps.

Given a list of rating steps (base, multiply, add), produces a waterfall
summary with cumulative values and deltas at each step.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from haute._logging import get_logger

if TYPE_CHECKING:
    from haute.trace import TraceStep

logger = get_logger(component="trace_waterfall")


@dataclass
class WaterfallEntry:
    """A single entry in the waterfall."""

    label: str
    operation: str
    value: float
    delta: float
    cumulative: float


@dataclass
class WaterfallResult:
    """Complete waterfall summary."""

    entries: list[WaterfallEntry]
    final_value: float


def build_waterfall(steps: list[dict[str, Any]]) -> WaterfallResult | None:
    """Build a waterfall from a list of rating steps.

    Each step dict has keys: ``label``, ``operation`` (``"base"``,
    ``"multiply"``, ``"add"``), and ``value``.

    Returns ``None`` if fewer than 3 steps are provided (not enough
    for a meaningful waterfall).
    """
    if len(steps) < 3:
        return None

    entries: list[WaterfallEntry] = []
    cumulative = 0.0

    if not all(isinstance(step, dict) for step in steps):
        return None

    for step in steps:
        label = step.get("label", "")
        operation = step.get("operation", "base")
        raw_value = step.get("value", 0)
        try:
            value = float(raw_value)
        except (ValueError, TypeError):
            return None

        if operation == "base":
            cumulative = value
            delta = 0.0
        elif operation == "multiply":
            new_cumulative = cumulative * value
            delta = new_cumulative - cumulative
            cumulative = new_cumulative
        elif operation == "add":
            delta = value
            cumulative = cumulative + value
        else:
            delta = 0.0

        if not math.isfinite(cumulative):
            return None

        entries.append(
            WaterfallEntry(
                label=label,
                operation=operation,
                value=value,
                delta=delta,
                cumulative=cumulative,
            )
        )

    return WaterfallResult(entries=entries, final_value=cumulative)


def build_waterfall_from_steps(
    steps: list[TraceStep],
    column: str,
    *,
    target_node_id: str,
) -> list[dict[str, Any]] | dict[str, Any] | None:
    """Assemble a waterfall from trace steps for *column*.

    Looks for sequential steps where the traced column is added (base)
    or modified (multiplicative/additive).  Returns a list of entry
    dicts on the happy path, a structured ``{"error": "..."}`` payload
    if waterfall construction fails, or ``None`` when the pre-conditions
    (column + ≥3 steps) are not met.
    """
    if not column or len(steps) < 3:
        return None
    try:
        waterfall_steps: list[dict[str, Any]] = []
        for step in steps:
            val = step.output_values.get(column)
            if val is None:
                continue
            if column in step.schema_diff.columns_added and not waterfall_steps:
                waterfall_steps.append({"label": step.node_name, "operation": "base", "value": val})
            elif column in step.schema_diff.columns_modified:
                # Detect multiply vs add from the expression
                op = "multiply"
                if step.expression and isinstance(step.expression, dict):
                    expr_text = step.expression.get("expression_text", "")
                    if "+" in expr_text or "-" in expr_text:
                        op = "add"
                waterfall_steps.append({"label": step.node_name, "operation": op, "value": val})
        wf_result = build_waterfall(waterfall_steps)
        if wf_result is None:
            return None
        return [
            {
                "label": e.label,
                "operation": e.operation,
                "value": e.value,
                "delta": e.delta,
                "cumulative": e.cumulative,
            }
            for e in wf_result.entries
        ]
    except Exception as exc:
        logger.warning(
            "waterfall_build_failed",
            error=str(exc),
            error_type=type(exc).__name__,
            target=target_node_id,
            column=column,
            exc_info=True,
        )
        return {
            "error": f"waterfall build failed: {exc}",
            "error_type": type(exc).__name__,
        }
