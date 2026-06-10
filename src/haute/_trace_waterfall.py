"""Waterfall data generation for sequential multiplicative/additive rating steps.

Given a traced column's path through the pipeline, produces a waterfall
summary with per-step contributions, cumulative values and deltas.

Arithmetic contract (CODE_REVIEW.md C8): every contribution is derived
from CONSECUTIVE OBSERVED OUTPUT VALUES along the traced path —
``delta = value_after - value_before`` and, for multiplicative display,
the implied factor ``value_after / value_before``.  Expression text is
consulted only to choose the display label (x vs +) and can never
corrupt the numbers.  The final cumulative must reconcile (within float
tolerance) with the traced output value displayed beside the waterfall;
a violation raises :class:`WaterfallReconciliationError`, which
``build_waterfall_from_steps`` converts into a structured, user-visible
``{"error": ...}`` payload — never a silently wrong chart.
"""

from __future__ import annotations

import ast
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from haute._logging import get_logger

if TYPE_CHECKING:
    from haute.trace import TraceStep

logger = get_logger(component="trace_waterfall")

#: Relative float tolerance for reconciliation checks.  Observed values
#: are carried through verbatim, so genuine chains agree to the ulp;
#: this tolerance only absorbs float noise, not arithmetic mistakes.
_RECONCILE_REL_TOL = 1e-9


class WaterfallReconciliationError(ValueError):
    """The waterfall arithmetic does not reconcile with observed values.

    Raised when a step's display value contradicts the observed
    cumulative chain, or when the final cumulative does not match the
    traced output value.  This is an invariant violation — the chart
    would lie — so construction fails loudly instead of rendering it.
    """


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


def _as_finite_float(value: Any) -> float | None:
    """Return *value* as a finite float, or ``None`` if it is not a real
    finite number (bools, strings, None, NaN/Inf and overflowing ints
    are all rejected)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        as_float = float(value)
    except OverflowError:
        return None
    return as_float if math.isfinite(as_float) else None


def _check_display_consistency(
    operation: str,
    prev_cumulative: float,
    display_value: float,
    observed: float,
    label: str,
) -> None:
    """Assert that a step's display value agrees with the observed chain.

    This is the in-builder C8 guard: feeding a post-step cumulative value
    in as a multiply/add amount (``100 x 120 = 12,000``) is off by orders
    of magnitude and must fail loudly, while value-derived display
    numbers re-apply to within float noise.  The tolerance scales with
    the magnitudes involved so it never fires on ulp drift.
    """
    if operation == "base":
        reapplied = display_value
    elif operation == "multiply":
        reapplied = prev_cumulative * display_value
    elif operation == "add":
        reapplied = prev_cumulative + display_value
    else:
        return

    tolerance = _RECONCILE_REL_TOL * max(1.0, abs(prev_cumulative), abs(observed), abs(reapplied))
    if not math.isfinite(reapplied) or abs(reapplied - observed) > tolerance:
        raise WaterfallReconciliationError(
            f"waterfall step {label!r}: {operation} display value {display_value!r} "
            f"applied to {prev_cumulative!r} gives {reapplied!r}, which does not "
            f"reconcile with the observed value {observed!r}"
        )


def build_waterfall(steps: list[dict[str, Any]]) -> WaterfallResult | None:
    """Build a waterfall from a list of rating steps.

    Each step dict has keys: ``label``, ``operation`` (``"base"``,
    ``"multiply"``, ``"add"``), and ``value``.

    A step may additionally carry ``"cumulative"`` — the OBSERVED
    post-step value of the traced column.  When present, the entry's
    cumulative snaps to that observation (no re-application drift), the
    delta is the difference of consecutive observations, and ``value``
    is validated against the observed chain — a display number that
    contradicts the observations raises
    :class:`WaterfallReconciliationError` (the C8 invariant).  Without
    ``"cumulative"`` the step is applied arithmetically (hand-authored
    factor lists).

    Returns ``None`` if fewer than 3 steps are provided (not enough
    for a meaningful waterfall) or if any value is non-numeric or
    non-finite.
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

        observed_raw = step.get("cumulative")
        if observed_raw is not None:
            try:
                observed = float(observed_raw)
            except (ValueError, TypeError):
                return None
            if not math.isfinite(observed):
                return None
            _check_display_consistency(operation, cumulative, value, observed, label)
            delta = 0.0 if operation == "base" else observed - cumulative
            cumulative = observed
        elif operation == "base":
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


def _operation_hint(expression: Any, column: str) -> str | None:
    """Classify the expression's top-level operator for display labeling.

    Returns ``"multiply"``, ``"add"``, or ``None`` when no confident
    hint exists.  Parses the expression text's AST — substring matching
    misread ``premium * (1 - discount)`` as additive (C8).  The hint
    only selects the x/+ label; all numbers are value-derived.
    """
    if not isinstance(expression, dict):
        return None
    target = expression.get("target_column")
    if target not in (None, column):
        return None
    text = expression.get("expression_text")
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        tree = ast.parse(text, mode="eval")
    except (SyntaxError, ValueError):
        return None
    node = tree.body
    if isinstance(node, ast.BinOp):
        if isinstance(node.op, (ast.Add, ast.Sub)):
            return "add"
        if isinstance(node.op, (ast.Mult, ast.Div)):
            return "multiply"
    return None


def _classify_contribution(
    step: TraceStep,
    column: str,
    value_before: float,
    value_after: float,
    target_node_id: str,
) -> tuple[str, float]:
    """Choose the display label and number for one modified step.

    Both candidates are value-derived — implied factor
    ``value_after / value_before`` for multiplicative display, delta
    ``value_after - value_before`` for additive display — so the label
    can never corrupt the arithmetic.  A zero (or vanishing)
    ``value_before`` has no defined implied factor: the step falls back
    to delta-only display, loudly logged, and an Inf factor is never
    rendered.
    """
    delta = value_after - value_before
    if value_before == 0.0 or not math.isfinite(value_after / value_before):
        logger.warning(
            "waterfall_implied_factor_undefined",
            node=step.node_id,
            column=column,
            target=target_node_id,
            value_before=value_before,
            value_after=value_after,
        )
        return "add", delta
    if _operation_hint(step.expression, column) == "add":
        return "add", delta
    return "multiply", value_after / value_before


def build_waterfall_from_steps(
    steps: list[TraceStep],
    column: str,
    *,
    target_node_id: str,
    final_output_value: Any,
) -> list[dict[str, Any]] | dict[str, Any] | None:
    """Assemble a waterfall from trace steps for *column*.

    Walks the traced path and derives every contribution from
    consecutive observed output values: the first step that creates (or
    first carries a changed value of) the column opens the chain as the
    base; each subsequent step that modifies the column contributes
    ``delta = value_after - value_before``, displayed as the implied
    factor ``value_after / value_before`` for multiplicative steps.
    Passthrough steps contribute nothing.

    The final cumulative must reconcile with *final_output_value* — the
    traced output value displayed beside the waterfall.  Returns a list
    of entry dicts on the happy path, a structured ``{"error": ...}``
    payload if construction fails or the reconciliation invariant is
    violated, or ``None`` when the pre-conditions (column, ≥3 steps, a
    numeric traced output value) are not met.
    """
    if not column or len(steps) < 3:
        return None
    final_value = _as_finite_float(final_output_value)
    if final_value is None:
        # Nothing numeric to reconcile against — a waterfall would be
        # unverifiable, so the feature does not apply.
        return None
    try:
        waterfall_steps: list[dict[str, Any]] = []
        value_before: float | None = None
        for step in steps:
            observed = _as_finite_float(step.output_values.get(column))
            if observed is None:
                continue
            diff = step.schema_diff
            if value_before is None:
                if column in diff.columns_added or column in diff.columns_modified:
                    waterfall_steps.append(
                        {
                            "label": step.node_name,
                            "operation": "base",
                            "value": observed,
                            "cumulative": observed,
                        }
                    )
                    value_before = observed
            elif column in diff.columns_modified:
                operation, display_value = _classify_contribution(
                    step, column, value_before, observed, target_node_id
                )
                waterfall_steps.append(
                    {
                        "label": step.node_name,
                        "operation": operation,
                        "value": display_value,
                        "cumulative": observed,
                    }
                )
                value_before = observed

        wf_result = build_waterfall(waterfall_steps)
        if wf_result is None:
            return None

        if not math.isclose(
            wf_result.final_value,
            final_value,
            rel_tol=_RECONCILE_REL_TOL,
            abs_tol=_RECONCILE_REL_TOL,
        ):
            raise WaterfallReconciliationError(
                f"waterfall for column {column!r} does not reconcile: final "
                f"cumulative {wf_result.final_value!r} != traced output value "
                f"{final_value!r}"
            )

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
    except WaterfallReconciliationError as exc:
        logger.error(
            "waterfall_reconciliation_failed",
            error=str(exc),
            target=target_node_id,
            column=column,
            final_output_value=final_value,
        )
        return {
            "error": f"waterfall reconciliation failed: {exc}",
            "error_type": type(exc).__name__,
        }
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
