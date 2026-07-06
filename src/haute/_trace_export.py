"""Export a trace result to a structured dict for markdown/report generation."""

from __future__ import annotations

from typing import Any


def export_trace(trace_result: Any) -> dict[str, Any]:
    """Convert a TraceResult into a structured dict suitable for reports.

    Returns a dict with keys: ``header``, ``formula``, ``sources``,
    ``data_flow``, ``metadata``.
    """
    steps = trace_result.steps

    # --- header ---
    header = {
        "column": trace_result.column,
        "output_value": trace_result.output_value,
        "target_node_id": trace_result.target_node_id,
        "row_index": trace_result.row_index,
    }

    # --- formula (from the step that creates/modifies the column) ---
    formula: dict[str, Any] = {}
    target_step = None
    for s in steps:
        if trace_result.column and (
            trace_result.column in s.schema_diff.columns_added
            or trace_result.column in s.schema_diff.columns_modified
        ):
            target_step = s
            break

    if target_step is not None:
        if target_step.expression:
            formula["expression"] = target_step.expression.get("expression_text", "")
        elif target_step.calculation:
            formula["expression"] = target_step.calculation.get("expression_text", "")
        else:
            formula["expression"] = ""

        if target_step.calculation:
            formula["substituted"] = target_step.calculation.get("substituted_text", "")
        else:
            formula["substituted"] = ""
    else:
        formula["expression"] = ""
        formula["substituted"] = ""

    # --- sources (input columns and their origins) ---
    sources: list[dict[str, Any]] = []
    if target_step is not None:
        # Get referenced columns from expression or calculation
        ref_cols: list[str] = []
        if target_step.expression and "referenced_columns" in target_step.expression:
            ref_cols = target_step.expression["referenced_columns"]
        elif target_step.calculation and "referenced_columns" in target_step.calculation:
            ref_cols = target_step.calculation["referenced_columns"]

        for col in ref_cols:
            # Report the column's true UPSTREAM origin: the first step
            # that creates or assigns it (schema diff added/modified), not
            # the most-downstream node that merely carries it forward.
            origin_node = None
            for s in steps:
                if col in s.schema_diff.columns_added or col in s.schema_diff.columns_modified:
                    origin_node = s.node_name
                    break
            if origin_node is None:
                # No producer recorded the column in its schema diff — fall
                # back to the first step that carries it in its output.
                for s in steps:
                    if col in s.output_values:
                        origin_node = s.node_name
                        break
            sources.append(
                {
                    "column": col,
                    "value": target_step.input_values.get(col),
                    "origin": origin_node,
                }
            )

    # --- data_flow (ordered step summaries) ---
    data_flow: list[dict[str, Any]] = []
    for s in steps:
        data_flow.append(
            {
                "node_id": s.node_id,
                "node_name": s.node_name,
                "node_type": s.node_type,
                "columns_added": s.schema_diff.columns_added,
                "columns_removed": s.schema_diff.columns_removed,
            }
        )

    # --- metadata ---
    metadata = {
        "step_count": len(steps),
        "execution_ms": trace_result.execution_ms,
        "total_nodes_in_pipeline": trace_result.total_nodes_in_pipeline,
    }

    return {
        "header": header,
        "formula": formula,
        "sources": sources,
        "data_flow": data_flow,
        "metadata": metadata,
    }
