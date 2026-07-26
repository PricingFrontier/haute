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
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from haute._edge_join import EDGE_JOIN_DEFAULT_SUFFIX
from haute._json_safe import MAX_SAFE_INTEGER
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


class WaterfallUnavailableError(ValueError):
    """The traced values cannot support a truthful waterfall chart."""


@dataclass
class WaterfallEntry:
    """A single entry in the waterfall."""

    label: str
    operation: str
    value: float
    delta: float
    cumulative: float
    default_used: bool = False


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


def _is_unsafe_integer_string(value: Any) -> bool:
    """Return whether *value* is a decimal string outside the JSON-safe int range."""
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    if not stripped:
        return False
    digits = stripped[1:] if stripped[0] in {"+", "-"} else stripped
    if not digits.isdecimal():
        return False
    try:
        parsed = int(stripped)
    except ValueError:
        return False
    return abs(parsed) > MAX_SAFE_INTEGER


def _as_trace_waterfall_float(
    value: Any,
    *,
    column: str,
    json_safe_integer_string: bool = False,
) -> float | None:
    """Coerce trace values only when they are safe to render as JSON numbers."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise WaterfallUnavailableError(
                f"column {column!r} contains a JSON-safe integer outside JavaScript's "
                "exact numeric range; the waterfall cannot render it as a number "
                "without losing precision"
            )
        return float(value)
    if isinstance(value, str):
        if json_safe_integer_string and _is_unsafe_integer_string(value):
            raise WaterfallUnavailableError(
                f"column {column!r} contains a JSON-safe integer outside JavaScript's "
                "exact numeric range; the waterfall cannot render it as a number "
                "without losing precision"
            )
        return None
    return _as_finite_float(value)


def _edge_join_config(node_map: dict[str, Any] | None, node_id: str) -> dict[str, Any] | None:
    if node_map is None:
        return None
    node = node_map.get(node_id)
    data = getattr(node, "data", None)
    if getattr(data, "nodeType", None) != "edgeJoin":
        return None
    config = getattr(data, "config", None)
    return config if isinstance(config, dict) else None


def _has_lineage_path(
    parents_of: dict[str, list[str]],
    ancestor_id: str,
    descendant_id: str,
) -> bool:
    """Return whether *ancestor_id* is upstream of *descendant_id*."""
    if ancestor_id == descendant_id:
        return True
    seen: set[str] = set()
    stack = list(parents_of.get(descendant_id, []))
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        if current == ancestor_id:
            return True
        seen.add(current)
        stack.extend(parents_of.get(current, []))
    return False


def _reject_renamed_join_branch_origins(
    steps: list[TraceStep],
    column: str,
    parents_of: dict[str, list[str]],
    origin_ids: list[str],
    node_map: dict[str, Any] | None,
) -> None:
    """Reject origins from a join branch whose colliding column was suffixed."""
    steps_by_id = {step.node_id: step for step in steps}
    for step in steps:
        config = _edge_join_config(node_map, step.node_id)
        if config is None:
            continue
        base_id = config.get("baseInput")
        join_id = config.get("joinInput")
        if not isinstance(base_id, str) or not isinstance(join_id, str):
            continue
        suffix = config.get("suffix") or EDGE_JOIN_DEFAULT_SUFFIX
        if not isinstance(suffix, str) or not suffix:
            continue

        suffixed_column = f"{column}{suffix}"
        base_step = steps_by_id.get(base_id)
        join_step = steps_by_id.get(join_id)
        if base_step is None or join_step is None:
            continue
        if column not in base_step.output_values or column not in join_step.output_values:
            continue
        if column not in step.output_values or suffixed_column not in step.output_values:
            continue

        renamed_origin_ids = [
            origin_id
            for origin_id in origin_ids
            if _has_lineage_path(parents_of, origin_id, join_id)
            and not _has_lineage_path(parents_of, origin_id, base_id)
        ]
        if not renamed_origin_ids:
            continue
        branch_nodes = ", ".join(sorted(set(renamed_origin_ids)))
        raise WaterfallUnavailableError(
            f"column {column!r} is produced on joined branch(es) {branch_nodes} "
            f"but edgeJoin node {step.node_id!r} emits that branch value as "
            f"{suffixed_column!r}; the waterfall cannot use it as upstream lineage "
            f"for unsuffixed {column!r}"
        )


def _ensure_single_column_lineage(
    steps: list[TraceStep],
    column: str,
    parents_of: dict[str, list[str]] | None,
    node_map: dict[str, Any] | None,
) -> None:
    """Reject waterfalls whose candidate column origins are on separate branches."""
    if parents_of is None:
        return
    origin_ids = [
        step.node_id
        for step in steps
        if column in step.output_values
        and (
            column in step.schema_diff.columns_added or column in step.schema_diff.columns_modified
        )
    ]
    for index, left_id in enumerate(origin_ids):
        for right_id in origin_ids[index + 1 :]:
            if _has_lineage_path(parents_of, left_id, right_id) or _has_lineage_path(
                parents_of,
                right_id,
                left_id,
            ):
                continue
            branch_nodes = ", ".join(sorted({left_id, right_id}))
            raise WaterfallUnavailableError(
                f"column {column!r} is produced on multiple joined branches "
                f"({branch_nodes}); the waterfall cannot compare consecutive values "
                "until the branch lineage is disambiguated"
            )
    _reject_renamed_join_branch_origins(steps, column, parents_of, origin_ids, node_map)


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

    ``build_waterfall_from_steps`` snaps each entry's cumulative to the
    observed value and derives the display number from consecutive
    observations. Final reconciliation against the traced output value
    is asserted separately in ``build_waterfall_from_steps``.
    """
    if operation == "base":
        reapplied = display_value
    elif operation == "multiply":
        if prev_cumulative == 0.0:
            # 0 × anything == 0, so re-application cannot validate the
            # displayed factor: any factor "reconciles" whenever the
            # observed value is also 0.  Only the identity (×1.0) is
            # self-consistent from a zero base — reject any other factor
            # rather than silently accepting an unverifiable one.
            if display_value != 1.0:
                raise WaterfallReconciliationError(
                    f"waterfall step {label!r}: multiply factor {display_value!r} "
                    f"cannot be validated against a zero prior cumulative "
                    f"(0 × {display_value!r} == 0 for any factor)"
                )
            reapplied = 0.0
        else:
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
    """Build a waterfall from internally observed trace-step values."""
    if len(steps) < 3:
        return None

    entries: list[WaterfallEntry] = []
    cumulative = 0.0

    for step in steps:
        label = step["label"]
        operation = step["operation"]
        value = step["value"]
        observed = step["cumulative"]
        _check_display_consistency(operation, cumulative, value, observed, label)
        delta = 0.0 if operation == "base" else observed - cumulative
        cumulative = observed

        entries.append(
            WaterfallEntry(
                label=label,
                operation=operation,
                value=value,
                delta=delta,
                cumulative=cumulative,
                default_used=bool(step.get("default_used", False)),
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


def _step_targets_column(
    step: TraceStep,
    column: str,
    node_map: dict[str, Any] | None,
) -> bool:
    """Whether *step*'s code assigns *column*, even if this row was a no-op.

    A multiplicative/additive step whose factor is the identity for the
    traced row (e.g. a region relativity of ``1.0``) leaves the cell
    numerically unchanged, so the schema diff records it as *passed*, not
    *modified*, and it would be silently dropped from the waterfall.
    Sniffing the node's code for an assignment to *column* (the same
    ``\\bcolumn\\s*=`` pattern the enricher uses) lets such a structurally
    relevant step still appear as an explicit identity contribution rather
    than vanishing.
    """
    if not node_map:
        return False
    node = node_map.get(step.node_id)
    data = getattr(node, "data", None)
    config = getattr(data, "config", None)
    code = config.get("code", "") if isinstance(config, dict) else ""
    if not isinstance(code, str) or not code:
        return False
    return bool(re.search(rf"\b{re.escape(column)}\s*=", code))


def _detail_uses_default(value: Any) -> bool:
    """Return whether specialised trace evidence says a default was used."""
    if isinstance(value, dict):
        if (
            value.get("default_used") is True
            or value.get("is_default") is True
            or value.get("status") == "default"
        ):
            return True
        return any(_detail_uses_default(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_detail_uses_default(item) for item in value)
    return False


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
    # A negative base or a sign flip between the two values yields a
    # negative implied factor, which renders as a nonsensical "×-1.3".
    # Display these additively (delta only) and log the sign change rather
    # than emitting a negative factor — mirroring the value_before == 0
    # fallback above.
    if value_before < 0.0 or (value_after < 0.0) != (value_before < 0.0):
        logger.warning(
            "waterfall_sign_change",
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
    parents_of: dict[str, list[str]] | None = None,
    node_map: dict[str, Any] | None = None,
    integer_output_node_ids: set[str] | None = None,
    final_output_is_integer: bool = False,
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
    try:
        integer_output_node_ids = integer_output_node_ids or set()
        final_value = _as_trace_waterfall_float(
            final_output_value,
            column=column,
            json_safe_integer_string=final_output_is_integer,
        )
        if final_value is None:
            # Nothing numeric to reconcile against — a waterfall would be
            # unverifiable, so the feature does not apply.
            return None
        _ensure_single_column_lineage(steps, column, parents_of, node_map)

        waterfall_steps: list[dict[str, Any]] = []
        value_before: float | None = None
        for step in steps:
            observed = _as_trace_waterfall_float(
                step.output_values.get(column),
                column=column,
                json_safe_integer_string=step.node_id in integer_output_node_ids,
            )
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
                            "default_used": _detail_uses_default(step.node_detail),
                        }
                    )
                    value_before = observed
            elif column in diff.columns_modified or _step_targets_column(step, column, node_map):
                # Include steps that structurally target the column even
                # when the cell was numerically unchanged for this row: a
                # no-op factor (e.g. ×1.0) is displayed as an explicit
                # identity contribution instead of silently vanishing.
                operation, display_value = _classify_contribution(
                    step, column, value_before, observed, target_node_id
                )
                waterfall_steps.append(
                    {
                        "label": step.node_name,
                        "operation": operation,
                        "value": display_value,
                        "cumulative": observed,
                        "default_used": _detail_uses_default(step.node_detail),
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
                "default_used": e.default_used,
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
    except WaterfallUnavailableError as exc:
        logger.warning(
            "waterfall_unavailable",
            reason=str(exc),
            target=target_node_id,
            column=column,
        )
        return {
            "error": f"waterfall unavailable: {exc}",
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
