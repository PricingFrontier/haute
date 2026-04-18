"""Trace enrichment: node-type enrichers + per-step dispatch walk.

This module combines two layers:

1. Node-type enrichers (``enrich_rating_step``, ``enrich_banding``,
   ``enrich_model_score``, ``enrich_scenario_expansion``,
   ``enrich_live_switch``) — each examines a node's config and the
   input/output row values to produce a structured dict of enrichment
   details that explain *what happened* at that node during a trace.

2. The per-step dispatch walk (``enrich_steps``) that iterates over
   every trace step and delegates to:

     - expression parsing / evaluation (from ``_expression_parser``)
     - intra-node chain analysis
     - upstream input-source derivation
     - rename detection
     - node-type enrichment (the dispatchers above)
     - row-lineage detection

   Enrichment functions imported from ``_expression_parser`` and the
   node-type dispatchers are resolved at call time via
   ``sys.modules["haute.trace"]`` so ``monkeypatch.setattr`` on those
   module attributes (e.g. ``haute.trace.parse_expression``) flows
   through into this dispatch walk unchanged.

Supports both real Haute config structures (from the pipeline editor)
and simplified test configs (from the TDD test suite).
"""

from __future__ import annotations

import dataclasses
import math
import re
import sys
from typing import TYPE_CHECKING, Any

import polars as pl

from haute._logging import get_logger

if TYPE_CHECKING:
    from haute.trace import TraceStep

logger = get_logger(component="trace_enrichment")


# ---------------------------------------------------------------------------
# Null value explanation
# ---------------------------------------------------------------------------


def explain_null_value(value: Any = None, context: dict[str, Any] | None = None) -> str | None:
    """Explain why a value is null.

    Args:
        value: The value to explain. If not None, returns None (no explanation needed).
        context: Dict with keys describing the origin:
            - ``join_type``, ``right_table``, ``join_key``, ``join_value`` for joins
            - ``origin`` = ``"source"`` for source data
            - ``origin`` = ``"computation"`` for computed nulls

    Returns:
        A human-readable explanation string, or None if the value is not null.
    """
    if value is not None:
        return None

    if context is None:
        return "null value (unknown origin)"

    origin = context.get("origin", "")
    join_type = context.get("join_type", "")

    if join_type == "left":
        right_table = context.get("right_table", "table")
        join_key = context.get("join_key", "")
        join_value = context.get("join_value", "")
        return f"no match in {right_table} — {join_key} = {join_value} (left join)"

    if origin == "source":
        return "null in source data"

    if origin == "computation":
        error = context.get("error", "")
        if error:
            return f"computation produced null ({error})"
        return "computation produced null"

    return "null value"


# ---------------------------------------------------------------------------
# Rating step enrichment
# ---------------------------------------------------------------------------


def _enrich_single_table(
    table: dict[str, Any],
    input_row: dict[str, Any],
    output_row: dict[str, Any],
) -> dict[str, Any]:
    """Enrich a single rate table lookup within a rating step."""
    factors: list[str] = table.get("factors", []) or []
    entries: list[dict[str, Any]] = table.get("entries", []) or []
    output_col: str = table.get("outputColumn", "")
    default_raw = table.get("defaultValue")

    # Lookup key values from the input row
    lookup_keys = {f: input_row.get(f) for f in factors}

    # The output value for this table
    rate_value = output_row.get(output_col)

    # Determine if matched: check if value is the default
    has_default = default_raw is not None and str(default_raw).strip()
    try:
        default_val = float(str(default_raw)) if has_default else None
    except (ValueError, TypeError):
        default_val = None
    if default_val is not None and not math.isfinite(default_val):
        default_val = None

    # Try to find the matched entry in the table
    matched_entry: dict[str, Any] | None = None
    if rate_value is not None:
        input_key_strs = {f: str(input_row.get(f, "")) for f in factors}
        for entry in entries:
            entry_key_strs = {f: str(entry.get(f, "")) for f in factors}
            if entry_key_strs == input_key_strs:
                matched_entry = dict(entry)
                break

    matched = rate_value is not None
    default_used = False
    if rate_value is not None and matched_entry is None and default_val is not None:
        # Value exists but no entry matched — default was used
        try:
            default_used = float(rate_value) == default_val
        except (ValueError, TypeError):
            pass

    return {
        "output_column": output_col,
        "lookup_keys": lookup_keys,
        "rate_value": rate_value,
        "matched": matched and not default_used,
        "default_used": default_used,
        "matched_entry": matched_entry,
        "default_value": default_val,
    }


def enrich_rating_step(
    config: dict[str, Any],
    input_row: dict[str, Any],
    output_row: dict[str, Any],
) -> dict[str, Any]:
    """Enrich a rating-step (rate table lookup) trace.

    Handles both real Haute config (with ``tables`` list) and simplified
    test config (with ``join_key`` and ``rate_column``).
    """
    try:
        tables: list[dict[str, Any]] = config.get("tables", []) or []

        if tables:
            # Real Haute config — process each table
            table_details = [_enrich_single_table(t, input_row, output_row) for t in tables]

            # Combined column info
            operation = config.get("operation", "multiply") or "multiply"
            combined_col = config.get("combinedColumn", "") or ""
            combined_value = output_row.get(combined_col) if combined_col else None

            result: dict[str, Any] = {
                "detail_type": "rating_step",
                "tables": table_details,
            }

            if combined_col and len(table_details) >= 2:
                result["combined"] = {
                    "column": combined_col,
                    "operation": operation,
                    "value": combined_value,
                    "input_values": [t["rate_value"] for t in table_details],
                }

            # Top-level convenience fields (from first table for simple cases)
            if len(table_details) == 1:
                t = table_details[0]
                result["matched_key"] = t["lookup_keys"]
                result["rate_value"] = t["rate_value"]
                result["matched"] = t["matched"]
            else:
                # Multiple tables — matched if any table matched
                result["matched_key"] = {
                    k: v for t in table_details for k, v in t["lookup_keys"].items()
                }
                result["rate_value"] = combined_value
                result["matched"] = any(t["matched"] for t in table_details)

            return result

        # Fallback: simplified test config
        join_key = config.get("join_key", "")
        rate_column = config.get("rate_column", "")

        if isinstance(join_key, list):
            matched_key = {k: input_row.get(k) for k in join_key}
        else:
            matched_key = {join_key: input_row.get(join_key)} if join_key else {}

        rate_value = output_row.get(rate_column)
        matched = rate_value is not None

        return {
            "detail_type": "rating_step",
            "matched_key": matched_key,
            "rate_value": rate_value,
            "matched": matched,
        }
    except Exception:
        logger.debug("enrichment_failed", node_type="rating_step", exc_info=True)
        return {
            "detail_type": "rating_step",
            "matched_key": {},
            "rate_value": None,
            "matched": False,
        }


# ---------------------------------------------------------------------------
# Banding enrichment
# ---------------------------------------------------------------------------


def _match_continuous_rule(
    input_value: Any,
    rule: dict[str, Any],
) -> bool:
    """Check if input_value satisfies a continuous banding rule."""
    if input_value is None:
        return False
    try:
        val = float(input_value)
    except (ValueError, TypeError):
        return False

    op_fn = {
        "<": lambda v, t: v < t,
        "<=": lambda v, t: v <= t,
        ">": lambda v, t: v > t,
        ">=": lambda v, t: v >= t,
        "=": lambda v, t: v == t,
        "==": lambda v, t: v == t,
        "!=": lambda v, t: v != t,
        "<>": lambda v, t: v != t,
    }

    for suffix in ("1", "2"):
        op = str(rule.get(f"op{suffix}", "") or "").strip()
        threshold = rule.get(f"val{suffix}")
        if not op or threshold is None or threshold == "":
            continue
        try:
            threshold_num = float(threshold)
        except (ValueError, TypeError):
            continue
        fn = op_fn.get(op)
        if fn is None:
            continue
        if not fn(val, threshold_num):
            return False
    return True


def enrich_banding(
    config: dict[str, Any],
    input_row: dict[str, Any],
    output_row: dict[str, Any],
) -> dict[str, Any]:
    """Enrich a banding node trace.

    Handles both real Haute config (with ``factors`` list containing
    ``column``, ``outputColumn``, ``rules``, ``banding``, ``default``)
    and simplified test config (with ``input_column``, ``output_column``,
    ``rules``).
    """
    try:
        factors = config.get("factors", [])

        if isinstance(factors, list) and factors and isinstance(factors[0], dict):
            # Real Haute config — multiple banding factors
            factor_details = []
            for factor_cfg in factors:
                col = factor_cfg.get("column", "")
                out_col = factor_cfg.get("outputColumn", "")
                rules = factor_cfg.get("rules", []) or []
                banding_type = factor_cfg.get("banding", "continuous")
                default = factor_cfg.get("default")

                input_value = input_row.get(col)
                selected_band = output_row.get(out_col)

                # Find which rule matched
                rule_index = -1
                is_default = False

                if banding_type == "categorical":
                    for i, rule in enumerate(rules):
                        rule_val = rule.get("value", "")
                        rule_assignment = rule.get("assignment", "")
                        if str(input_value) == str(rule_val) and rule_assignment == selected_band:
                            rule_index = i
                            break
                else:
                    # Continuous — evaluate each rule against input value
                    for i, rule in enumerate(rules):
                        if _match_continuous_rule(input_value, rule):
                            assignment = rule.get("assignment", "")
                            if assignment == selected_band:
                                rule_index = i
                                break

                if rule_index == -1 and default is not None and selected_band == str(default):
                    is_default = True

                factor_details.append(
                    {
                        "column": col,
                        "output_column": out_col,
                        "banding_type": banding_type,
                        "input_value": input_value,
                        "selected_band": selected_band,
                        "rule_index": rule_index,
                        "is_default": is_default,
                    }
                )

            result: dict[str, Any] = {
                "detail_type": "banding",
                "factors": factor_details,
            }

            # Top-level convenience (first factor for simple cases)
            if factor_details:
                f = factor_details[0]
                result["selected_band"] = f["selected_band"]
                result["rule_index"] = f["rule_index"]
                result["is_default"] = f["is_default"]
                result["input_value"] = f["input_value"]

            return result

        # Fallback: simplified test config
        output_column = config.get("output_column", "")
        input_column = config.get("input_column", "")
        rules = config.get("rules", [])

        selected_band = output_row.get(output_column)
        input_value = input_row.get(input_column)

        rule_index = -1
        is_default = False

        for i, rule in enumerate(rules):
            if "default" in rule:
                if rule.get("default") == selected_band or rule.get("value") == selected_band:
                    rule_index = i
                    is_default = True
                    break
            elif rule.get("value") == selected_band:
                rule_index = i
                break

        if rule_index == -1:
            for i, rule in enumerate(rules):
                if "default" in rule and rule["default"] == selected_band:
                    rule_index = i
                    is_default = True
                    break

        return {
            "detail_type": "banding",
            "selected_band": selected_band,
            "rule_index": rule_index,
            "is_default": is_default,
            "input_value": input_value,
        }
    except Exception:
        logger.debug("enrichment_failed", node_type="banding", exc_info=True)
        return {
            "detail_type": "banding",
            "selected_band": None,
            "rule_index": -1,
            "is_default": False,
            "input_value": None,
        }


# ---------------------------------------------------------------------------
# Model score enrichment
# ---------------------------------------------------------------------------


def enrich_model_score(
    config: dict[str, Any],
    input_row: dict[str, Any],
    output_row: dict[str, Any],
) -> dict[str, Any]:
    """Enrich a model-score node trace.

    Handles real Haute config (with ``output_column``, ``sourceType``,
    ``run_id``, ``task``) and simplified test config.
    """
    try:
        # Try real config keys first, then test config keys
        prediction_column = config.get("output_column") or config.get("prediction_column") or ""
        prediction_value = output_row.get(prediction_column)

        # Model identity
        model_identity = {
            "source_type": config.get("sourceType", ""),
            "run_id": config.get("run_id", ""),
            "registered_model": config.get("registered_model", ""),
            "version": config.get("version", ""),
            "task": config.get("task", "regression"),
        }

        # Feature columns
        feature_columns = config.get("feature_columns", None)
        if feature_columns is None:
            # Infer: input columns minus the prediction column
            feature_columns = [k for k in input_row if k != prediction_column]

        return {
            "detail_type": "model_score",
            "prediction_value": prediction_value,
            "prediction_column": prediction_column,
            "feature_columns": list(feature_columns),
            "feature_values": {f: input_row.get(f) for f in feature_columns},
            "model_identity": model_identity,
        }
    except Exception:
        logger.debug("enrichment_failed", node_type="model_score", exc_info=True)
        return {
            "detail_type": "model_score",
            "prediction_value": None,
            "prediction_column": "",
            "feature_columns": [],
        }


# ---------------------------------------------------------------------------
# Scenario expansion enrichment
# ---------------------------------------------------------------------------


def enrich_scenario_expansion(
    config: dict[str, Any],
    input_row: dict[str, Any],
    output_row: dict[str, Any],
) -> dict[str, Any]:
    """Enrich a scenario-expansion node trace."""
    try:
        scenario_column = config.get("scenario_column") or config.get("column_name") or ""
        step_column = config.get("step_column", "scenario_index")
        scenario_value = output_row.get(scenario_column)
        scenario_index = output_row.get(step_column)

        return {
            "detail_type": "scenario_expander",
            "scenario_value": scenario_value,
            "scenario_column": scenario_column,
            "scenario_index": scenario_index,
            "parameters": {
                "min_value": config.get("min_value"),
                "max_value": config.get("max_value"),
                "steps": config.get("steps"),
            },
        }
    except Exception:
        logger.debug("enrichment_failed", node_type="scenario_expander", exc_info=True)
        return {"detail_type": "scenario_expander", "scenario_value": None, "scenario_column": ""}


# ---------------------------------------------------------------------------
# Live switch enrichment
# ---------------------------------------------------------------------------


def enrich_live_switch(
    config: dict[str, Any],
    source: str,
) -> dict[str, Any]:
    """Enrich a live-switch node trace."""
    try:
        input_scenario_map: dict[str, str] = config.get("input_scenario_map", {})

        active_branch = ""
        active_scenario = ""
        pruned_branches: list[str] = []

        for input_name, scenario in input_scenario_map.items():
            if scenario == source:
                active_branch = input_name
                active_scenario = scenario
            else:
                pruned_branches.append(input_name)

        return {
            "detail_type": "live_switch",
            "active_branch": active_branch,
            "active_scenario": active_scenario,
            "pruned_branches": pruned_branches,
        }
    except Exception:
        logger.debug("enrichment_failed", node_type="live_switch", exc_info=True)
        return {
            "detail_type": "live_switch",
            "active_branch": "",
            "active_scenario": "",
            "pruned_branches": [],
        }


# ---------------------------------------------------------------------------
# Row lineage type detection
# ---------------------------------------------------------------------------


def detect_row_lineage_type(
    *,
    input_row_count: int | None = None,
    output_row_count: int = 0,
    node_type: str = "",
    operation_type: str = "",
) -> str:
    """Detect the row lineage type based on node metadata and row counts.

    Returns one of:
      - "created"     : rows originate from a data source / API input
      - "selected"    : rows chosen by a live switch
      - "filtered"    : rows removed by a filter
      - "aggregated"  : rows collapsed by a group_by
      - "joined"      : rows produced by a join
      - "expanded"    : rows multiplied (cross join, explode, scenario expansion)
      - "sorted"      : rows reordered
      - "passthrough" : rows unchanged (with_columns, rename, etc.)
    """
    try:
        # Source nodes always create rows
        if node_type in ("dataSource", "apiInput"):
            return "created"

        if node_type == "liveSwitch":
            return "selected"

        # Operation-type based detection
        op = operation_type.lower() if operation_type else ""

        if op in ("group_by", "groupby", "agg"):
            return "aggregated"

        if op in ("join",):
            return "joined"

        if op in ("sort", "sort_by"):
            return "sorted"

        if op in ("filter",):
            return "filtered"

        if op in ("cross_join", "explode", "scenario_expand"):
            return "expanded"

        # Fallback: infer from row count changes
        parent = input_row_count if input_row_count is not None else 0

        if parent == 0 and output_row_count > 0:
            return "created"

        if output_row_count < parent:
            return "filtered"

        if output_row_count > parent:
            return "expanded"

        return "passthrough"
    except Exception:
        logger.debug("enrichment_failed", node_type="row_lineage", exc_info=True)
        return "passthrough"


# ---------------------------------------------------------------------------
# Dispatch helpers — used by ``enrich_steps`` below
#
# These call into the expression parser and the node-type enrichers via
# attribute lookup on ``haute.trace`` so that tests can
# ``monkeypatch.setattr("haute.trace.parse_expression", ...)`` and have
# the patched function reach this dispatcher.
# ---------------------------------------------------------------------------


def _trace_module() -> Any:
    """Return the ``haute.trace`` module object (for dynamic lookup)."""
    return sys.modules["haute.trace"]


def _wrap_node_code(raw_code: str) -> str:
    """Wrap dot-chain or bare-expression code so the parser sees valid Python."""
    if not raw_code:
        return raw_code
    if raw_code.startswith("."):
        return f"df = (df\n{raw_code})"
    stripped = raw_code.lstrip()
    if not stripped.startswith("df") and "=" not in raw_code.split("\n")[0].split("(")[0]:
        return f"df = (\n{raw_code}\n)"
    return raw_code


def _fix_upstream_values(
    input_sources: dict[str, Any],
    steps: list[TraceStep],
    eager_outputs: dict[str, pl.DataFrame],
) -> None:
    """Fix upstream step output_values using known-good values from input_sources.

    When the row correlator matched the wrong row in a source node (due to
    non-deterministic join ordering or value changes through scenario
    expansion), the step's output_values shows null for columns that
    actually have values.  This function uses the known-good values from
    expression evaluation to find the correct row in the source DataFrame
    and update the step's output_values.
    """
    from haute._trace_correlation import _jsonify_row

    for col_name, src_info in input_sources.items():
        if not isinstance(src_info, dict):
            continue
        src_node_name = src_info.get("node_name")
        known_value = src_info.get("result_value")
        if src_node_name is None or known_value is None:
            continue

        # Find the step for this source node
        for s in steps:
            if s.node_name != src_node_name:
                continue
            current_val = s.output_values.get(col_name)
            if current_val is not None:
                break  # value is already correct

            # Step has null but we know the correct value — try to find
            # the right row in the source DataFrame using the known value.
            df = eager_outputs.get(s.node_id)
            if df is None or col_name not in df.columns:
                break
            try:
                # Filter to rows where this column matches the known value
                if isinstance(known_value, float):
                    matched = df.filter((pl.col(col_name) - known_value).abs() < 1e-6)
                else:
                    matched = df.filter(pl.col(col_name) == known_value)
                if len(matched) > 0:
                    new_row = _jsonify_row(matched.row(0, named=True))
                    s.output_values[col_name] = new_row.get(col_name)
            except Exception as exc:
                # Row-fixup is opportunistic — it patches upstream rows
                # that the post-hoc correlator got wrong.  If the filter
                # itself errors (type mismatch, non-comparable value),
                # log visibly so the user can see the fixup was skipped
                # rather than silently leaving the wrong row in place.
                logger.warning(
                    "fix_upstream_row_failed",
                    node_id=s.node_id,
                    column=col_name,
                    error=str(exc),
                    error_type=type(exc).__name__,
                    exc_info=True,
                )
            break

        # Recurse into nested input_sources
        nested = src_info.get("input_sources")
        if isinstance(nested, dict):
            _fix_upstream_values(nested, steps, eager_outputs)


def _build_input_sources(
    ref_cols: list[str],
    current_step: TraceStep,
    all_steps: list[TraceStep],
    node_map: dict[str, Any],
    preamble_ns: dict[str, Any] | None,
    *,
    depth: int = 0,
    max_depth: int = 3,
    visited: set[str] | None = None,
) -> dict[str, Any]:
    """Recursively build input source derivations for referenced columns.

    For each column in *ref_cols*, finds the upstream step that created it
    and extracts its formula + values.  If that source itself has an
    expression with referenced columns, recurses to build nested sources.
    """
    trace_mod = _trace_module()
    parse_expression = trace_mod.parse_expression
    evaluate_expression = trace_mod.evaluate_expression

    if visited is None:
        visited = set()
    result: dict[str, Any] = {}
    for ref_col in ref_cols:
        if ref_col in visited:
            continue
        visited.add(ref_col)
        for other_step in all_steps:
            if other_step is current_step:
                continue
            if ref_col not in other_step.schema_diff.columns_added:
                continue
            other_combined = {**other_step.input_values, **other_step.output_values}
            source_info: dict[str, Any] = {
                "node_name": other_step.node_name,
            }

            # Parse the expression for this specific column from the
            # upstream node's code — don't rely on other_step.expression
            # since that's only populated for the traced column.
            parsed_refs: list[str] = []
            try:
                other_code = ""
                nd = node_map.get(other_step.node_id)
                if nd is not None:
                    cfg = nd.data.config if isinstance(nd.data.config, dict) else {}
                    raw = cfg.get("code", "") or ""

                    # Instance resolution: if this node is an instance
                    # and its code doesn't contain with_columns, use the
                    # original node's code instead.
                    instance_of = cfg.get("instanceOf", "")
                    if instance_of and ".with_columns(" not in raw and instance_of in node_map:
                        orig_cfg = node_map[instance_of].data.config
                        if isinstance(orig_cfg, dict):
                            raw = orig_cfg.get("code", "") or ""

                    other_code = _wrap_node_code(raw)
                if other_code:
                    parsed = parse_expression(other_code, ref_col)
                    if parsed and parsed.expression_text:
                        source_info["expression_text"] = parsed.expression_text
                        parsed_refs = list(parsed.referenced_columns)
                    ev = evaluate_expression(
                        other_code,
                        ref_col,
                        other_combined,
                        preamble_ns=preamble_ns,
                    )
                    if ev is not None:
                        source_info["substituted_text"] = ev.substituted_text
                        source_info["result_value"] = ev.result_value
            except Exception as exc:
                # Surface the derivation failure on the source entry so
                # the caller can see why an input column's value/
                # expression is missing, rather than silently falling
                # back to the raw cell value.
                logger.warning(
                    "input_source_derivation_failed",
                    node_id=other_step.node_id,
                    column=ref_col,
                    error=str(exc),
                    error_type=type(exc).__name__,
                    exc_info=True,
                )
                source_info["error"] = f"input-source derivation failed: {exc}"
                source_info["error_type"] = type(exc).__name__
                source_info.setdefault("result_value", other_combined.get(ref_col))

            # If no expression was found (source node with no code),
            # use the value from the step's output_values directly.
            if "result_value" not in source_info:
                source_info["result_value"] = other_combined.get(ref_col)

            # Recurse into this source's dependencies
            if depth < max_depth and parsed_refs:
                sub_sources = _build_input_sources(
                    parsed_refs,
                    other_step,
                    all_steps,
                    node_map,
                    preamble_ns,
                    depth=depth + 1,
                    max_depth=max_depth,
                    visited=set(visited),
                )
                if sub_sources:
                    source_info["input_sources"] = sub_sources

            result[ref_col] = source_info
            break
    return result


def _detect_rename(
    step: TraceStep,
    code: str,
    raw_code: str,
    column: str,
    all_steps: list[TraceStep],
    node_map: dict[str, Any] | None = None,
) -> None:
    """Detect if the column is a rename and populate calculation/node_detail."""

    # Case 1: .rename({'old': 'new'}) syntax
    rename_match = re.search(r"\.rename\s*\(\s*\{", raw_code)
    if rename_match:
        # Parse rename mapping from the raw code
        pairs = re.findall(r"['\"](\w+)['\"]\s*:\s*['\"](\w+)['\"]", raw_code)
        for old_name, new_name in pairs:
            if new_name == column:
                if step.calculation is None:
                    step.calculation = {}
                step.calculation["original_name"] = old_name

                # Build rename chain by looking at previous steps
                chain = _build_rename_chain(all_steps, step, old_name, column, node_map)
                if chain and len(chain) > 2:
                    step.calculation["rename_chain"] = chain

                if step.node_detail is None:
                    step.node_detail = {}
                step.node_detail["detail_type"] = "rename"
                step.node_detail["original_name"] = old_name
                step.node_detail["new_name"] = new_name
                return

    # Case 2: .with_columns(new_name=pl.col('old_name')) — pure rename (col reference only)
    if ".with_columns(" in raw_code:
        col_match = re.search(
            rf"{re.escape(column)}\s*=\s*pl\.col\(\s*['\"](\w+)['\"]\s*\)",
            raw_code,
        )
        if col_match:
            old_name = col_match.group(1)
            # Check if this is a pure rename (no additional operations)
            # The expression text should be just the column name
            expr = step.expression
            if expr and expr.get("expression_type") == "arithmetic":
                expr_text = expr.get("expression_text", "")
                if expr_text.strip() == old_name:
                    if step.calculation is None:
                        step.calculation = {}
                    step.calculation["original_name"] = old_name

                    # Build rename chain
                    chain = _build_rename_chain(all_steps, step, old_name, column, node_map)
                    if chain and len(chain) > 2:
                        step.calculation["rename_chain"] = chain


def _build_rename_chain(
    all_steps: list[TraceStep],
    current_step: TraceStep,
    old_name: str,
    new_name: str,
    node_map: dict[str, Any] | None = None,
) -> list[str]:
    """Build a chain of renames by looking backward through steps."""
    trace_mod = _trace_module()
    has_parser = trace_mod._HAS_EXPRESSION_PARSER
    parse_expression = trace_mod.parse_expression if has_parser else None

    # Start with the current rename: old_name -> new_name
    chain = [old_name, new_name]

    step_idx = None
    for i, s in enumerate(all_steps):
        if s.node_id == current_step.node_id:
            step_idx = i
            break

    if step_idx is None:
        return chain

    current_name = old_name
    for i in range(step_idx - 1, -1, -1):
        prev_step = all_steps[i]
        # Check if current_name was added by this step (indicating a possible rename)
        if current_name not in prev_step.schema_diff.columns_added:
            continue

        # Try to detect what column was the source by parsing the step's code
        if has_parser and node_map and parse_expression is not None:
            try:
                nd = node_map.get(prev_step.node_id)
                if nd:
                    cfg = nd.data.config if isinstance(nd.data.config, dict) else {}
                    raw_code = cfg.get("code", "") or ""
                    if raw_code:
                        wrapped = _wrap_node_code(raw_code)
                        parsed = parse_expression(wrapped, current_name)
                        if parsed and parsed.expression_type == "arithmetic":
                            refs = parsed.referenced_columns
                            if len(refs) == 1 and refs[0] != current_name:
                                if parsed.expression_text.strip() == refs[0]:
                                    chain.insert(0, refs[0])
                                    current_name = refs[0]
                                    continue
            except Exception as exc:
                # Walking the rename chain is best-effort — if parsing a
                # prior step's code blows up we stop walking and return
                # what we have so far.  Log loudly rather than silently
                # truncate the chain.
                logger.warning(
                    "rename_chain_walk_failed",
                    node_id=prev_step.node_id,
                    column=current_name,
                    error=str(exc),
                    error_type=type(exc).__name__,
                    exc_info=True,
                )
                break

    # Remove duplicates while preserving order
    seen = set()
    unique_chain = []
    for c in chain:
        if c not in seen:
            seen.add(c)
            unique_chain.append(c)

    return unique_chain


def enrich_steps(
    steps: list[TraceStep],
    node_map: dict[str, Any],
    eager_outputs: dict[str, pl.DataFrame],
    parents_of: dict[str, list[str]],
    column: str | None,
    source: str,
    preamble_ns: dict[str, Any] | None = None,
) -> None:
    """Enrich trace steps in-place with expression/calculation/detail data.

    This is a best-effort pass — if the enrichment modules are unavailable
    or a per-step enrichment fails, the fields stay ``None``.  The
    expression-parser and node-type enricher references are resolved at
    call time via ``haute.trace`` attribute lookup so pytest
    monkeypatching at that location flows through unchanged.
    """
    trace_mod = _trace_module()
    has_parser = trace_mod._HAS_EXPRESSION_PARSER
    has_enrichment = trace_mod._HAS_TRACE_ENRICHMENT

    for step in steps:
        try:
            node_data = node_map[step.node_id].data
            cfg = node_data.config if isinstance(node_data.config, dict) else {}
            raw_code = cfg.get("code", "") or ""

            # Instance resolution: if this node is an instance and its
            # code doesn't contain with_columns, use the original node's
            # code.  This ensures the step that CREATED the column gets
            # the correct expression, not just the target step.
            instance_of = cfg.get("instanceOf", "")
            if instance_of and ".with_columns(" not in raw_code and instance_of in node_map:
                orig_cfg = node_map[instance_of].data.config
                if isinstance(orig_cfg, dict):
                    orig_code = orig_cfg.get("code", "") or ""
                    if orig_code:
                        raw_code = orig_code

            # The executor wraps dot-chain syntax (e.g. ".filter(...)") as
            # "df = (df\n.filter(...))".  Apply the same wrapping so the
            # expression parser sees valid Python.
            code = _wrap_node_code(raw_code)
            node_type = step.node_type

            # --- Expression parsing ---
            # Trigger if column is added/modified at THIS step, OR if the
            # column is a pass-through at the target step (created upstream).
            # For pass-throughs, find the upstream step that created it and
            # use its expression.
            _col_in_schema = (
                (
                    column in step.schema_diff.columns_added
                    or column in step.schema_diff.columns_modified
                )
                if column
                else False
            )

            # If this is the target step and the column is just passing
            # through, look upstream for the creating step's expression
            if (
                has_parser
                and column
                and not _col_in_schema
                and step.node_id == steps[-1].node_id  # target step
                and column in step.schema_diff.columns_passed
            ):
                for upstream in steps:
                    if upstream is step:
                        continue
                    if column in upstream.schema_diff.columns_added:
                        # Found the upstream creator — parse its code
                        u_cfg = (
                            node_map[upstream.node_id].data.config
                            if isinstance(node_map[upstream.node_id].data.config, dict)
                            else {}
                        )
                        u_raw = u_cfg.get("code", "") or ""
                        # Instance resolution
                        u_inst = u_cfg.get("instanceOf", "")
                        if u_inst and ".with_columns(" not in u_raw and u_inst in node_map:
                            u_orig = node_map[u_inst].data.config
                            if isinstance(u_orig, dict):
                                u_raw = u_orig.get("code", "") or ""
                        u_code = _wrap_node_code(u_raw)
                        if u_code:
                            try:
                                u_combined = {
                                    **upstream.input_values,
                                    **upstream.output_values,
                                }
                                parsed = trace_mod.parse_expression(u_code, column)
                                if parsed and parsed.expression_text:
                                    step.expression = dataclasses.asdict(parsed)
                                ev = trace_mod.evaluate_expression(
                                    u_code,
                                    column,
                                    u_combined,
                                    preamble_ns=preamble_ns,
                                )
                                if ev is not None:
                                    step.calculation = dataclasses.asdict(ev)
                            except Exception as exc:
                                logger.warning(
                                    "upstream_expression_failed",
                                    node_id=upstream.node_id,
                                    column=column,
                                    error=str(exc),
                                    error_type=type(exc).__name__,
                                    exc_info=True,
                                )
                                err_payload: dict[str, Any] = {
                                    "error": f"upstream expression lookup failed: {exc}",
                                    "error_type": type(exc).__name__,
                                    "upstream_node_id": upstream.node_id,
                                }
                                # Surface the error on both enrichment
                                # fields so downstream consumers see it
                                # regardless of which one they inspect.
                                if step.expression is None:
                                    step.expression = dict(err_payload)
                                else:
                                    step.expression.setdefault("error", err_payload["error"])
                                if step.calculation is None:
                                    step.calculation = dict(err_payload)
                                else:
                                    step.calculation.setdefault("error", err_payload["error"])
                        break
            _col_in_code = False
            if column and raw_code and ".with_columns(" in raw_code:
                # Check if the column is a keyword arg or appears as an alias target
                _col_in_code = bool(re.search(rf"\b{re.escape(column)}\s*=", raw_code))
            if has_parser and column and (_col_in_schema or _col_in_code):
                try:
                    parsed = trace_mod.parse_expression(code, column)
                    if parsed is not None:
                        step.expression = dataclasses.asdict(parsed)
                except Exception as exc:
                    logger.warning(
                        "expression_parse_failed",
                        node_id=step.node_id,
                        column=column,
                        error=str(exc),
                        error_type=type(exc).__name__,
                        exc_info=True,
                    )
                    # Surface the parse failure on the enrichment field
                    # so downstream consumers see it instead of an
                    # unexplained missing expression.
                    step.expression = {
                        "error": f"parse_expression failed: {exc}",
                        "error_type": type(exc).__name__,
                        "target_column": column,
                    }
                try:
                    evaluated = trace_mod.evaluate_expression(
                        code,
                        column,
                        {**step.input_values, **step.output_values},
                        preamble_ns=preamble_ns,
                    )
                    if evaluated is not None:
                        calc_dict = dataclasses.asdict(evaluated)
                        # Add taken_branch info to calculation dict
                        if evaluated.taken_branch is not None:
                            calc_dict["taken_branch"] = evaluated.taken_branch
                        if evaluated.taken_branch_index is not None:
                            calc_dict["taken_branch_index"] = evaluated.taken_branch_index
                        # For window functions, use the actual output value
                        if evaluated.expression_type == "window" and column in step.output_values:
                            calc_dict["result_value"] = step.output_values[column]
                        step.calculation = calc_dict
                except Exception as exc:
                    logger.warning(
                        "expression_eval_failed",
                        node_id=step.node_id,
                        column=column,
                        error=str(exc),
                        error_type=type(exc).__name__,
                        exc_info=True,
                    )
                    # Seed calculation with a visible error marker that
                    # persists even if later enrichment stages (chain,
                    # input_sources) add more fields to the dict.
                    step.calculation = {
                        "error": f"evaluate_expression failed: {exc}",
                        "error_type": type(exc).__name__,
                        "target_column": column,
                    }

                # --- Expression chain (intra-node dependencies) ---
                try:
                    chain = trace_mod.parse_expression_chain(raw_code, column)
                    if chain and len(chain) > 1:
                        if step.calculation is None:
                            step.calculation = {}
                        combined_values = {**step.input_values, **step.output_values}
                        enriched_chain: list[dict[str, Any]] = []
                        for p in chain:
                            entry = dataclasses.asdict(p)
                            # Enrich with substituted values and result
                            try:
                                ev = trace_mod.evaluate_expression(
                                    raw_code,
                                    p.target_column,
                                    combined_values,
                                    preamble_ns=preamble_ns,
                                )
                                if ev is not None:
                                    entry["substituted_text"] = ev.substituted_text
                                    entry["result_value"] = ev.result_value
                            except Exception as inner_exc:
                                logger.warning(
                                    "chain_entry_eval_failed",
                                    node_id=step.node_id,
                                    column=p.target_column,
                                    error=str(inner_exc),
                                    error_type=type(inner_exc).__name__,
                                    exc_info=True,
                                )
                                entry["error"] = (
                                    f"chain entry evaluation failed: {inner_exc}"
                                )
                                entry["error_type"] = type(inner_exc).__name__
                                entry.setdefault("substituted_text", p.expression_text)
                                fallback = combined_values.get(p.target_column)
                                entry.setdefault("result_value", fallback)
                            enriched_chain.append(entry)
                        step.calculation["expression_chain"] = enriched_chain
                except Exception as exc:
                    logger.warning(
                        "expression_chain_failed",
                        node_id=step.node_id,
                        column=column,
                        error=str(exc),
                        error_type=type(exc).__name__,
                        exc_info=True,
                    )
                    # Surface a visible failure on the chain sub-field so
                    # the user can see why intra-node dependencies were
                    # not analysed, rather than it looking like "no chain
                    # detected".  Also seed an outer ``error`` key on the
                    # calculation dict (without overwriting a more
                    # specific evaluate-expression error) so generic
                    # consumers that inspect only the outer dict still
                    # see the failure.
                    if step.calculation is None:
                        step.calculation = {}
                    chain_error_msg = f"parse_expression_chain failed: {exc}"
                    chain_error_type = type(exc).__name__
                    step.calculation["expression_chain"] = {
                        "error": chain_error_msg,
                        "error_type": chain_error_type,
                    }
                    step.calculation.setdefault("error", chain_error_msg)
                    step.calculation.setdefault("error_type", chain_error_type)

                # --- Input sources (recursive upstream derivations) ---
                try:
                    # Collect ALL referenced columns: from the target
                    # expression AND from every chain entry. This ensures
                    # upstream derivations are found for intra-node deps
                    # too (e.g., margin = premium - burn_cost in the same
                    # node — we still need to trace premium and burn_cost
                    # to their upstream origins).
                    all_ref_cols: list[str] = []
                    if step.expression and step.expression.get("referenced_columns"):
                        all_ref_cols.extend(step.expression["referenced_columns"])
                    if step.calculation and step.calculation.get("expression_chain"):
                        chain_val = step.calculation["expression_chain"]
                        if isinstance(chain_val, list):
                            for chain_entry in chain_val:
                                if not isinstance(chain_entry, dict):
                                    continue
                                for rc in chain_entry.get("referenced_columns", []):
                                    if rc not in all_ref_cols:
                                        all_ref_cols.append(rc)
                    if all_ref_cols:
                        input_sources = _build_input_sources(
                            all_ref_cols,
                            step,
                            steps,
                            node_map,
                            preamble_ns,
                            depth=0,
                            max_depth=3,
                        )
                        if input_sources:
                            if step.calculation is None:
                                step.calculation = {}
                            step.calculation["input_sources"] = input_sources

                            # Fix upstream steps that have wrong row data.
                            # When input_sources found the correct value
                            # for a column via expression evaluation, but
                            # the upstream step's output_values shows null
                            # (from a row correlation failure), re-correlate
                            # using the known-good value.
                            _fix_upstream_values(
                                input_sources,
                                steps,
                                eager_outputs,
                            )
                except Exception as exc:
                    logger.warning(
                        "input_sources_failed",
                        node_id=step.node_id,
                        column=column,
                        error=str(exc),
                        error_type=type(exc).__name__,
                        exc_info=True,
                    )
                    if step.calculation is None:
                        step.calculation = {}
                    step.calculation.setdefault(
                        "error", f"input_sources build failed: {exc}"
                    )
                    step.calculation.setdefault(
                        "error_type", type(exc).__name__
                    )

            # --- Rename detection ---
            if has_parser and column:
                try:
                    _detect_rename(step, code, raw_code, column, steps, node_map)
                except Exception as exc:
                    logger.warning(
                        "rename_detection_failed",
                        node_id=step.node_id,
                        column=column,
                        error=str(exc),
                        error_type=type(exc).__name__,
                        exc_info=True,
                    )
                    if step.calculation is None:
                        step.calculation = {}
                    step.calculation.setdefault(
                        "rename_detection_error",
                        f"rename detection failed: {exc}",
                    )

            # --- Node-type enrichment ---
            if has_enrichment:
                try:
                    detail: dict[str, Any] | None = None
                    if node_type == "ratingStep":
                        detail = trace_mod.enrich_rating_step(
                            cfg, step.input_values, step.output_values
                        )
                    elif node_type == "banding":
                        detail = trace_mod.enrich_banding(
                            cfg, step.input_values, step.output_values
                        )
                    elif node_type == "modelScore":
                        detail = trace_mod.enrich_model_score(
                            cfg, step.input_values, step.output_values
                        )
                    elif node_type == "scenarioExpander":
                        detail = trace_mod.enrich_scenario_expansion(
                            cfg,
                            step.input_values,
                            step.output_values,
                        )
                    elif node_type == "liveSwitch":
                        detail = trace_mod.enrich_live_switch(cfg, source)
                    if detail is not None:
                        step.node_detail = detail
                except Exception as exc:
                    logger.warning(
                        "node_enrichment_failed",
                        node_id=step.node_id,
                        node_type=str(node_type),
                        error=str(exc),
                        error_type=type(exc).__name__,
                        exc_info=True,
                    )
                    step.node_detail = {
                        "error": f"node enrichment failed: {exc}",
                        "error_type": type(exc).__name__,
                        "node_type": str(node_type),
                    }

                # --- Row lineage type ---
                try:
                    parent_ids = parents_of.get(step.node_id, [])
                    parent_row_count = 0
                    for pid in parent_ids:
                        df = eager_outputs.get(pid)
                        if df is not None:
                            parent_row_count = max(parent_row_count, len(df))
                    child_df = eager_outputs.get(step.node_id)
                    child_row_count = len(child_df) if child_df is not None else 0

                    # Sniff operation type from code string
                    operation_type = ""
                    if code:
                        code_lower = code.lower()
                        if ".group_by(" in code_lower or ".groupby(" in code_lower:
                            operation_type = "group_by"
                        elif ".cross_join(" in code_lower:
                            operation_type = "cross_join"
                        elif ".join(" in code_lower:
                            operation_type = "join"
                        elif ".filter(" in code_lower:
                            operation_type = "filter"
                        elif ".sort(" in code_lower or ".sort_by(" in code_lower:
                            operation_type = "sort"
                        elif ".explode(" in code_lower:
                            operation_type = "explode"

                    step.row_lineage_type = trace_mod.detect_row_lineage_type(
                        input_row_count=parent_row_count,
                        output_row_count=child_row_count,
                        node_type=node_type,
                        operation_type=operation_type,
                    )
                except Exception as exc:
                    logger.warning(
                        "row_lineage_detection_failed",
                        node_id=step.node_id,
                        error=str(exc),
                        error_type=type(exc).__name__,
                        exc_info=True,
                    )
                    # row_lineage_type is a plain string; encode the
                    # error visibly so UI consumers see "error: ..."
                    # rather than a silent None.
                    step.row_lineage_type = (
                        f"error: row lineage detection failed: {exc}"
                    )
        except Exception as exc:
            # Outer catch-all for any enrichment step.  Surface the
            # failure on the step so downstream consumers can see it,
            # then continue with the next step rather than aborting the
            # whole trace.  Raising here would poison every trace if a
            # single step hits an unforeseen bug — instead we emit a
            # WARNING log and annotate the step with an error marker.
            logger.warning(
                "trace_enrichment_step_failed",
                node_id=step.node_id,
                error=str(exc),
                error_type=type(exc).__name__,
                exc_info=True,
            )
            if step.node_detail is None:
                step.node_detail = {
                    "error": f"trace enrichment step failed: {exc}",
                    "error_type": type(exc).__name__,
                }
            else:
                step.node_detail.setdefault(
                    "error", f"trace enrichment step failed: {exc}"
                )
                step.node_detail.setdefault("error_type", type(exc).__name__)
            continue
