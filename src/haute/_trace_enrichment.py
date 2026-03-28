"""Node-type-specific trace enrichment functions.

Each function examines a node's config and the input/output row values
to produce a structured dict of enrichment details that explain *what
happened* at that node during a trace.

Supports both real Haute config structures (from the pipeline editor)
and simplified test configs (from the TDD test suite).
"""

from __future__ import annotations

import math
from typing import Any

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
        return "passthrough"
