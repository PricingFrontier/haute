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

import copy
import dataclasses
import json
import math
import re
import sys
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

import polars as pl

from haute._banding_config import normalise_banding_factors
from haute._graph_utils import _sanitize_func_name
from haute._json_safe import to_json_safe
from haute._logging import get_logger
from haute._rating import (
    _breakpoints_to_rules,
    _normalise_combined_outputs,
    normalise_rating_key,
)
from haute._rating_step_config import normalise_rating_tables

if TYPE_CHECKING:
    from haute.trace import TraceStep

logger = get_logger(component="trace_enrichment")

_EnrichmentMemoKey = tuple[object, ...]


def _enrichment_value_identity(value: Any) -> str:
    """Return a deterministic request-local identity for concern inputs."""
    return json.dumps(
        to_json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _enrichment_frame_identity(
    eager_outputs: Mapping[str, Any],
) -> tuple[tuple[str, str, int], ...]:
    identities: list[tuple[str, str, int]] = []
    for node_id, output in eager_outputs.items():
        if isinstance(output, dict):
            identities.extend(
                (str(node_id), str(handle), id(frame)) for handle, frame in output.items()
            )
        else:
            identities.append((str(node_id), "", id(output)))
    return tuple(sorted(identities))


def _enrichment_contains_error(value: Any) -> bool:
    if isinstance(value, Mapping):
        if "error" in value or "error_type" in value:
            return True
        return any(_enrichment_contains_error(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_enrichment_contains_error(item) for item in value)
    return False


# ---------------------------------------------------------------------------
# Rating step enrichment
# ---------------------------------------------------------------------------


def _enrich_single_table(
    table: dict[str, Any],
    input_row: dict[str, Any],
    output_row: dict[str, Any],
) -> dict[str, Any]:
    """Enrich a single rate table lookup within a rating step."""
    table_name = str(table.get("name", "") or "")
    factors: list[str] = table.get("factors", []) or []
    entries: list[dict[str, Any]] = table.get("entries", []) or []
    output_col: str = table.get("outputColumn", "")
    default_raw = table.get("defaultValue")

    # Lookup key values from the input row
    lookup_keys = {f: input_row.get(f) for f in factors}
    factor_details = [{"column": f, "value": input_row.get(f)} for f in factors]

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

    # Try to find the matched entry in the table.  Keys are compared in
    # the engine's canonical form (shared ``normalise_rating_key``), so
    # the matched/default flags here cannot diverge from what the
    # rating lookup join actually did (e.g. int-like float 25.0 matching
    # the string key "25").  Null keys never match the join, so a null
    # input key skips entry matching entirely.
    matched_entry: dict[str, Any] | None = None
    if rate_value is not None:
        input_keys = {f: normalise_rating_key(input_row.get(f)) for f in factors}
        if all(key is not None for key in input_keys.values()):
            # Runtime rating lookup deduplicates with keep="last" before joining.
            # Walk in reverse so the trace shows the same row that supplied the value.
            for entry in reversed(entries):
                entry_keys = {f: normalise_rating_key(entry.get(f)) for f in factors}
                if entry_keys == input_keys:
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
    if default_used:
        status = "default"
    elif matched_entry is not None and rate_value is not None:
        status = "matched"
    elif rate_value is None:
        status = "no_match"
    else:
        status = "unmatched_value"

    return {
        "name": table_name,
        "output_column": output_col,
        "factors": factor_details,
        "lookup_keys": lookup_keys,
        "selected_value": rate_value,
        "rate_value": rate_value,
        "matched": matched and not default_used,
        "default_used": default_used,
        "status": status,
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
    tables = normalise_rating_tables(config)
    combined_col = str(config.get("combinedColumn", "") or "").strip()
    has_combined_outputs = "combinedOutputs" in config

    if tables or combined_col or has_combined_outputs:
        table_details = [_enrich_single_table(t, input_row, output_row) for t in tables]
        table_output_columns = [t["output_column"] for t in table_details if t.get("output_column")]

        normalised_combined_outputs = _normalise_combined_outputs(config)
        legacy_combined = next(
            (combined for combined in normalised_combined_outputs if combined.get("_legacy")),
            None,
        )
        combined_value = output_row.get(combined_col) if combined_col else None

        result: dict[str, Any] = {
            "detail_type": "rating_step",
            "tables": table_details,
        }

        if legacy_combined and len(table_details) >= 2:
            result["combined"] = {
                "column": combined_col,
                "operation": legacy_combined["operation"],
                "value": combined_value,
                "input_values": [t["rate_value"] for t in table_details],
            }

        combined_outputs = []
        for combined in normalised_combined_outputs:
            if combined.get("_legacy") and len(table_output_columns) < 2:
                continue
            column = combined["outputColumn"]
            combined_outputs.append(
                {
                    "column": column,
                    "operation": combined["operation"],
                    "base_value": combined["baseValue"],
                    "input_values": {
                        output_col: output_row.get(output_col)
                        for output_col in table_output_columns
                    },
                    "value": output_row.get(column),
                }
            )
        if combined_outputs:
            result["combined_outputs"] = combined_outputs

        # Top-level convenience fields (from first table for simple cases)
        if len(table_details) == 1:
            t = table_details[0]
            result["matched_key"] = t["lookup_keys"]
            result["rate_value"] = t["rate_value"]
            result["matched"] = t["matched"]
        else:
            # Multiple tables: matched if any table matched.
            result["matched_key"] = {
                k: v for t in table_details for k, v in t["lookup_keys"].items()
            }
            first_combined_value = combined_outputs[0]["value"] if combined_outputs else None
            result["rate_value"] = combined_value if combined_col else first_combined_value
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


# ---------------------------------------------------------------------------
# Banding enrichment
# ---------------------------------------------------------------------------


def _coerce_pair_through_dtype(
    left: float,
    right: float,
    dtype: pl.DataType | None,
) -> tuple[float, float]:
    """Round both operands into *dtype*'s numeric domain for comparison.

    The engine bands the factor column in its own dtype (often
    ``Float32``), but the trace boundary widens every cell to Python
    ``float`` (``float64``).  A ``Float32`` value the engine matched with
    an ``=`` rule then reads as ``no_match`` under exact ``float64`` ``==``
    (e.g. ``0.1`` widened to ``0.10000000149…`` != literal ``0.1``).
    Canonicalising BOTH the observed cell and the rule threshold through
    the source dtype reproduces the engine's own comparison, so the trace
    cannot contradict the band it actually applied.  ``None`` (dtype
    unknown) leaves the operands untouched.
    """
    if dtype is None:
        return left, right
    try:
        coerced = pl.Series([left, right], dtype=dtype)
        return float(coerced[0]), float(coerced[1])
    except (pl.exceptions.PolarsError, ValueError, TypeError, OverflowError):
        return left, right


def _match_continuous_rule(
    input_value: Any,
    rule: dict[str, Any],
    input_dtype: pl.DataType | None = None,
) -> bool:
    """Check if input_value satisfies a continuous banding rule.

    *input_dtype* is the source factor column's original Polars dtype;
    when supplied, the observed value and each rule threshold are
    canonicalised through it (see :func:`_coerce_pair_through_dtype`) so a
    ``Float32``-banded value the engine matched does not read as
    ``no_match`` under widened ``float64`` comparison.
    """
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
        cmp_val, cmp_threshold = _coerce_pair_through_dtype(val, threshold_num, input_dtype)
        if not fn(cmp_val, cmp_threshold):
            return False
    return True


def _values_equivalent(left: Any, right: Any) -> bool:
    """Compare labels the same way banding runtime casts assignments."""
    return str(left) == str(right)


def _categorical_rule_matches(
    input_value: Any,
    selected_band: Any,
    rule: dict[str, Any],
) -> bool:
    """Mirror categorical banding's Utf8 remap semantics for one rule."""
    rule_val = rule.get("value", "")
    rule_assignment = rule.get("assignment", "")
    if rule_val is None or rule_val == "":
        return False
    if rule_assignment is None or rule_assignment == "":
        return False
    return str(input_value) == str(rule_val) and str(selected_band) == str(rule_assignment)


def _continuous_rule_bounds(rule: dict[str, Any]) -> dict[str, Any]:
    """Extract range metadata from a continuous banding rule for trace display."""
    result: dict[str, Any] = {
        "lower_bound": None,
        "upper_bound": None,
        "lower_inclusive": None,
        "upper_inclusive": None,
        "conditions": [],
    }

    for suffix in ("1", "2"):
        op = str(rule.get(f"op{suffix}", "") or "").strip()
        threshold = rule.get(f"val{suffix}")
        if not op or threshold is None or threshold == "":
            continue
        try:
            threshold_value: Any = float(threshold)
        except (ValueError, TypeError):
            threshold_value = threshold

        result["conditions"].append({"operator": op, "value": threshold_value})

        if op == ">":
            result["lower_bound"] = threshold_value
            result["lower_inclusive"] = False
        elif op == ">=":
            result["lower_bound"] = threshold_value
            result["lower_inclusive"] = True
        elif op == "<":
            result["upper_bound"] = threshold_value
            result["upper_inclusive"] = False
        elif op == "<=":
            result["upper_bound"] = threshold_value
            result["upper_inclusive"] = True
        elif op in {"=", "=="}:
            result["lower_bound"] = threshold_value
            result["upper_bound"] = threshold_value
            result["lower_inclusive"] = True
            result["upper_inclusive"] = True

    return result


def _focus_banding_factor(
    factor_details: list[dict[str, Any]],
    traced_column: str | None,
) -> dict[str, Any] | None:
    """Return the traced factor, or the first factor when no trace column is set."""
    if traced_column:
        for detail in factor_details:
            if detail.get("output_column") == traced_column:
                return detail
        return None
    return factor_details[0] if factor_details else None


def _copy_banding_factor_summary(result: dict[str, Any], factor: dict[str, Any]) -> None:
    """Populate top-level convenience fields from one factor detail."""
    for key in (
        "column",
        "input_column",
        "output_column",
        "banding_type",
        "input_value",
        "selected_band",
        "matched_band",
        "rule_index",
        "is_default",
        "status",
        "matched_rule",
        "matched_value",
        "lower_bound",
        "upper_bound",
        "lower_inclusive",
        "upper_inclusive",
        "conditions",
    ):
        if key in factor:
            result[key] = factor[key]


def _quote_trace_value(value: Any) -> str:
    """Format a trace value for compact human-readable calculation text."""
    if value is None:
        return "null"
    if isinstance(value, str):
        return f'"{value}"'
    return str(value)


def _format_banding_expression(factor: dict[str, Any]) -> str:
    input_column = str(factor.get("column", "") or "")
    output_column = str(factor.get("output_column", "") or "")
    is_default = bool(factor.get("is_default"))

    expression = f"{input_column} -> {output_column}"
    return f"{expression} (default)" if is_default else expression


def _banding_expression_payload(factor: dict[str, Any]) -> dict[str, Any]:
    input_column = str(factor.get("column", "") or "")
    output_column = str(factor.get("output_column", "") or "")
    return {
        "target_column": output_column,
        "expression_text": _format_banding_expression(factor),
        "expression_type": "banding",
        "referenced_columns": [input_column] if input_column else [],
        "constants": [],
        "sub_expressions": [],
        "source_line": None,
    }


def _banding_calculation_payload(factor: dict[str, Any]) -> dict[str, Any]:
    input_column = str(factor.get("column", "") or "")
    input_value = factor.get("input_value")
    selected_band = factor.get("selected_band")
    expression = _banding_expression_payload(factor)
    rule_index = factor.get("rule_index")
    taken_branch_index = rule_index if isinstance(rule_index, int) and rule_index >= 0 else None
    return {
        **expression,
        "substituted_text": (
            f"{_quote_trace_value(input_value)} -> {_quote_trace_value(selected_band)}"
        ),
        "result_value": selected_band,
        "input_values": {input_column: input_value} if input_column else {},
        "taken_branch": _format_banding_expression(factor),
        "taken_branch_index": taken_branch_index,
        "dimmed_branches": [],
        "nested_branches": [],
    }


def _banding_factor_for_column(
    detail: dict[str, Any],
    column: str,
) -> dict[str, Any] | None:
    factors = detail.get("factors")
    if not isinstance(factors, list):
        return None
    for factor in factors:
        if isinstance(factor, dict) and factor.get("output_column") == column:
            return factor
    return None


def enrich_banding(
    config: dict[str, Any],
    input_row: dict[str, Any],
    output_row: dict[str, Any],
    traced_column: str | None = None,
    *,
    factor_input_dtypes: dict[str, pl.DataType] | None = None,
) -> dict[str, Any]:
    """Enrich a banding node trace.

    Handles both real Haute config (with ``factors`` list containing
    ``column``, ``outputColumn``, ``rules``, ``banding``, ``default``)
    and simplified test config (with ``input_column``, ``output_column``,
    ``rules``).

    *factor_input_dtypes* maps a factor's input column name to its
    original Polars dtype.  It makes continuous-rule re-matching
    dtype-faithful (see :func:`_match_continuous_rule`) so a
    ``Float32``-banded value the engine matched is not reported as
    ``no_match``.  When absent, comparisons fall back to widened
    ``float64`` (historical behaviour).
    """
    dtype_by_column = factor_input_dtypes or {}
    try:
        factors = normalise_banding_factors(config)

        if isinstance(factors, list) and factors and isinstance(factors[0], dict):
            # Real Haute config — multiple banding factors
            factor_details = []
            for factor_cfg in factors:
                col = factor_cfg.get("column", "")
                out_col = factor_cfg.get("outputColumn", "")
                raw_rules = factor_cfg.get("rules", []) or []
                rules = raw_rules
                banding_type = factor_cfg.get("banding", "continuous")
                default = factor_cfg.get("default")
                if banding_type == "breakpoints":
                    rules = _breakpoints_to_rules(
                        raw_rules,
                        right_closed=bool(factor_cfg.get("rightClosed", True)),
                    )

                input_value = input_row.get(col)
                selected_band = output_row.get(out_col)

                # Find which rule matched
                rule_index = -1
                is_default = False
                matched_rule: dict[str, Any] | None = None

                if banding_type == "categorical":
                    for i, rule in enumerate(raw_rules):
                        if _categorical_rule_matches(input_value, selected_band, rule):
                            rule_index = i
                            matched_rule = dict(rule)
                            break
                else:
                    # Continuous — evaluate each rule against input value,
                    # comparing in the source column's own dtype so a
                    # Float32-banded value is not reported as no_match.
                    input_dtype = dtype_by_column.get(col)
                    for i, rule in enumerate(rules):
                        if _match_continuous_rule(input_value, rule, input_dtype):
                            assignment = rule.get("assignment", "")
                            if _values_equivalent(assignment, selected_band):
                                rule_index = i
                                matched_rule = dict(rule)
                                break

                if (
                    rule_index == -1
                    and default is not None
                    and _values_equivalent(
                        selected_band,
                        default,
                    )
                ):
                    is_default = True

                status = "default" if is_default else "matched" if rule_index >= 0 else "no_match"
                factor_detail: dict[str, Any] = {
                    "column": col,
                    "input_column": col,
                    "output_column": out_col,
                    "banding_type": banding_type,
                    "input_value": input_value,
                    "selected_band": selected_band,
                    "matched_band": selected_band,
                    "rule_index": rule_index,
                    "is_default": is_default,
                    "status": status,
                    "matched_rule": matched_rule,
                }
                if matched_rule is not None:
                    if banding_type == "categorical":
                        factor_detail["matched_value"] = matched_rule.get("value")
                    else:
                        factor_detail.update(_continuous_rule_bounds(matched_rule))
                factor_details.append(factor_detail)

            result: dict[str, Any] = {
                "detail_type": "banding",
                "factors": factor_details,
            }

            focused_factor = _focus_banding_factor(factor_details, traced_column)
            if focused_factor:
                _copy_banding_factor_summary(result, focused_factor)

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
            "input_column": input_column,
            "output_column": output_column,
            "selected_band": selected_band,
            "matched_band": selected_band,
            "rule_index": rule_index,
            "is_default": is_default,
            "input_value": input_value,
            "column": input_column,
        }
    except Exception as exc:
        logger.warning(
            "enrichment_failed",
            node_type="banding",
            error=str(exc),
            error_type=type(exc).__name__,
            exc_info=True,
        )
        return {
            "detail_type": "banding",
            "error": f"banding enrichment failed: {exc}",
            "error_type": type(exc).__name__,
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

        # Feature columns. Prefer explicit config, then the node contract,
        # then inference from the row. The contract keeps technical columns
        # such as quote IDs out of the model explanation.
        feature_columns = config.get("feature_columns", None)
        if feature_columns is None:
            contract = config.get("contract")
            contract_inputs = (
                contract.get("inputs")
                if isinstance(contract, dict) and isinstance(contract.get("inputs"), list)
                else None
            )
            feature_columns = contract_inputs
        if feature_columns is None:
            # Infer: input columns minus the prediction column
            feature_columns = [k for k in input_row if k != prediction_column]

        detail = {
            "detail_type": "model_score",
            "prediction_value": prediction_value,
            "prediction_column": prediction_column,
            "feature_columns": list(feature_columns),
            "feature_values": {f: input_row.get(f) for f in feature_columns},
            "model_identity": model_identity,
        }

        from haute import _model_explainability

        try:
            explanation = _model_explainability.explain_model_score_from_config(
                config,
                input_row,
                output_row,
                prediction_column=prediction_column,
                prediction_value=prediction_value,
            )
        except _model_explainability.ModelExplanationError as exc:
            error_metadata = _model_explainability.explanation_error_metadata_for_config(config)
            explanation = {
                "type": error_metadata["type"],
                "method": error_metadata["method"],
                "status": "error",
                "error": str(exc),
                "error_type": type(exc).__name__,
            }
        if explanation is not None:
            detail["explanation"] = explanation

        return detail
    except Exception as exc:
        logger.warning(
            "enrichment_failed",
            node_type="model_score",
            error=str(exc),
            error_type=type(exc).__name__,
            exc_info=True,
        )
        return {
            "detail_type": "model_score",
            "error": f"model score enrichment failed: {exc}",
            "error_type": type(exc).__name__,
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
    except Exception as exc:
        logger.warning(
            "enrichment_failed",
            node_type="scenario_expander",
            error=str(exc),
            error_type=type(exc).__name__,
            exc_info=True,
        )
        return {
            "detail_type": "scenario_expander",
            "error": f"scenario expansion enrichment failed: {exc}",
            "error_type": type(exc).__name__,
            "scenario_value": None,
            "scenario_column": "",
        }


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
    except Exception as exc:
        logger.warning(
            "enrichment_failed",
            node_type="live_switch",
            error=str(exc),
            error_type=type(exc).__name__,
            exc_info=True,
        )
        return {
            "detail_type": "live_switch",
            "error": f"live switch enrichment failed: {exc}",
            "error_type": type(exc).__name__,
            "active_branch": "",
            "active_scenario": "",
            "pruned_branches": [],
        }


# ---------------------------------------------------------------------------
# Optimiser apply enrichment
# ---------------------------------------------------------------------------


def enrich_optimiser_apply(
    config: dict[str, Any],
    input_row: dict[str, Any],
    output_row: dict[str, Any],
    *,
    input_frames: list[pl.DataFrame | pl.LazyFrame]
    | tuple[pl.DataFrame | pl.LazyFrame, ...]
    | None = None,
    source_names: list[str] | None = None,
    source_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Enrich an optimiserApply node trace."""
    from haute._optimiser_apply_explainability import explain_optimiser_apply_from_config

    return explain_optimiser_apply_from_config(
        config,
        input_row,
        output_row,
        input_frames=input_frames or [],
        source_names=source_names,
        source_ids=source_ids,
    )


# ---------------------------------------------------------------------------
# Row lineage type detection
# ---------------------------------------------------------------------------


def _node_output_row_count(df: pl.DataFrame | dict[str, pl.DataFrame] | None) -> int:
    """Row count of a node's materialised output for lineage detection.

    Multi-frame sources store ``dict[label, DataFrame]`` in
    ``eager_outputs`` — count the widest frame's rows (mirroring the
    parent-side handling in ``enrich_steps``), never ``len(dict)``,
    which would count FRAMES, not rows.
    """
    if df is None:
        return 0
    if isinstance(df, dict):
        return max((len(frame) for frame in df.values()), default=0)
    return len(df)


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

        # Join nodes are config-driven — their code carries no literal
        # ".join(" token, so row-count deltas would otherwise mislabel a
        # join fan-out as "expanded" or a fan-in as "filtered".  Classify
        # them by node type before falling through to code/row-count.
        if node_type == "edgeJoin":
            return "joined"

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
    except Exception as exc:
        logger.warning(
            "enrichment_failed",
            node_type="row_lineage",
            error=str(exc),
            error_type=type(exc).__name__,
            exc_info=True,
        )
        return f"error: row lineage enrichment failed: {exc}"


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
        src_node_id = src_info.get("node_id")
        src_node_name = src_info.get("node_name")
        known_value = src_info.get("result_value")
        if (src_node_id is None and src_node_name is None) or known_value is None:
            continue

        # Find the step for this source node.  Match on node_id — the
        # stable identity — so two nodes that happen to share a display
        # name don't cross-write each other's output_values.  Only fall
        # back to name matching for legacy sources that carry no id.
        for s in steps:
            if src_node_id is not None:
                if s.node_id != src_node_id:
                    continue
            elif s.node_name != src_node_name:
                continue
            current_val = s.output_values.get(col_name)
            if current_val is not None:
                break  # value is already correct

            # Step has null but we know the correct value — try to find
            # the right row in the source DataFrame using the known value.
            df = eager_outputs.get(s.node_id)
            if not isinstance(df, pl.DataFrame) or col_name not in df.columns:
                break
            try:
                # Filter to rows where this column matches the known value.
                # Floats use a SCALE-RELATIVE tolerance (a fixed 1e-6
                # absolute window collides distinct small-magnitude
                # factors — e.g. 1.0000001 vs 1.0000004 — and .row(0)
                # would then overwrite the displayed value with the wrong
                # row).  The match must also be UNIQUE: if several rows
                # satisfy it we cannot tell which one produced the value,
                # so we log and leave the existing row untouched rather
                # than guessing (fail loud, never a wrong attribution).
                if isinstance(known_value, float):
                    tol = abs(known_value) * 1e-9 + 1e-12
                    matched = df.filter((pl.col(col_name) - known_value).abs() <= tol)
                else:
                    matched = df.filter(pl.col(col_name) == known_value)
                if len(matched) == 1:
                    new_row = _jsonify_row(matched.row(0, named=True))
                    s.output_values[col_name] = new_row.get(col_name)
                elif len(matched) > 1:
                    logger.warning(
                        "fix_upstream_row_ambiguous",
                        node_id=s.node_id,
                        column=col_name,
                        match_count=len(matched),
                    )
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
    completed_memo: dict[_EnrichmentMemoKey, dict[str, Any]] | None = None,
    active_path: tuple[tuple[str, str], ...] = (),
    frame_identity: object | None = None,
) -> dict[str, Any]:
    """Recursively build input source derivations for referenced columns.

    For each column in *ref_cols*, finds the upstream step that created it
    and extracts its formula + values.  If that source itself has an
    expression with referenced columns, recurses to build nested sources.
    """
    trace_mod = _trace_module()
    parse_expression = trace_mod.parse_expression
    evaluate_expression = trace_mod.evaluate_expression

    if completed_memo is None:
        completed_memo = {}
    result: dict[str, Any] = {}
    try:
        current_step_index = all_steps.index(current_step)
    except ValueError as exc:
        raise ValueError(
            f"current_step {current_step.node_id!r} is not present in all_steps"
        ) from exc

    for ref_col in ref_cols:
        upstream_steps = all_steps[:current_step_index]
        for other_step in reversed(upstream_steps):
            if other_step is current_step:
                continue
            if (
                ref_col not in other_step.schema_diff.columns_added
                and ref_col not in other_step.schema_diff.columns_modified
            ):
                continue
            memo_node = node_map.get(other_step.node_id)
            memo_config = (
                memo_node.data.config
                if memo_node is not None and isinstance(memo_node.data.config, dict)
                else {}
            )
            active_key = (other_step.node_id, ref_col)
            memo_key: _EnrichmentMemoKey = (
                "input_source",
                other_step.node_id,
                ref_col,
                _enrichment_value_identity(other_step.input_values),
                _enrichment_value_identity(other_step.output_values),
                _enrichment_value_identity(memo_config),
                frame_identity,
                id(preamble_ns),
            )
            memo_value = completed_memo.get(memo_key)
            if memo_value is not None:
                result[ref_col] = copy.deepcopy(memo_value)
                break
            if active_key in active_path:
                result[ref_col] = {
                    "error": "input-source enrichment cycle detected",
                    "error_type": "TraceEnrichmentCycle",
                    "node_id": other_step.node_id,
                    "column": ref_col,
                }
                break
            other_combined = {**other_step.input_values, **other_step.output_values}
            source_info: dict[str, Any] = {
                "node_id": other_step.node_id,
                "node_name": other_step.node_name,
            }

            # Parse the expression for this specific column from the
            # upstream node's code — don't rely on other_step.expression
            # since that's only populated for the traced column.
            parsed_refs: list[str] = []
            banding_lineage_applied = False
            try:
                other_code = ""
                cfg: dict[str, Any] = {}
                nd = node_map.get(other_step.node_id)
                if nd is not None:
                    cfg = nd.data.config if isinstance(nd.data.config, dict) else {}
                    if other_step.node_type == "banding":
                        banding_detail = trace_mod.enrich_banding(
                            cfg,
                            other_step.input_values,
                            other_step.output_values,
                            traced_column=ref_col,
                        )
                        banding_factor = _banding_factor_for_column(banding_detail, ref_col)
                        if banding_factor is not None:
                            banding_expression = _banding_expression_payload(banding_factor)
                            banding_calculation = _banding_calculation_payload(banding_factor)
                            source_info["expression_text"] = banding_expression["expression_text"]
                            source_info["substituted_text"] = banding_calculation[
                                "substituted_text"
                            ]
                            source_info["result_value"] = banding_calculation["result_value"]
                            parsed_refs = list(banding_expression["referenced_columns"])
                            # The banding factor is the authoritative
                            # lineage for this column.  A banding node that
                            # also carries a `code` config key must not have
                            # its expression/substituted/result values
                            # clobbered by the generic parse/eval below.
                            banding_lineage_applied = True
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
                if other_code and not banding_lineage_applied:
                    parsed = parse_expression(other_code, ref_col)
                    if parsed and parsed.expression_text:
                        source_info["expression_text"] = parsed.expression_text
                        parsed_refs = list(parsed.referenced_columns)
                    eval_values = {**other_step.input_values, **other_step.output_values}
                    self_referential_modification = (
                        ref_col in other_step.schema_diff.columns_modified
                        and parsed
                        and ref_col in parsed.referenced_columns
                    )
                    skip_evaluation = False
                    if self_referential_modification:
                        if ref_col in other_step.input_values:
                            eval_values[ref_col] = other_step.input_values[ref_col]
                        else:
                            source_info["result_value"] = other_step.output_values.get(ref_col)
                            source_info["substituted_text"] = (
                                f"{ref_col} = {_quote_trace_value(source_info['result_value'])}"
                            )
                            skip_evaluation = True
                    if not skip_evaluation:
                        ev = evaluate_expression(
                            other_code,
                            ref_col,
                            eval_values,
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
                    completed_memo=completed_memo,
                    active_path=(*active_path, active_key),
                    frame_identity=frame_identity,
                )
                if sub_sources:
                    source_info["input_sources"] = sub_sources

            result[ref_col] = source_info
            if not _enrichment_contains_error(source_info):
                completed_memo[memo_key] = copy.deepcopy(source_info)
            break
    return result


def _attach_banding_lineage(
    step: TraceStep,
    detail: dict[str, Any],
    column: str | None,
    steps: list[TraceStep],
    node_map: dict[str, Any],
    preamble_ns: dict[str, Any] | None,
    eager_outputs: dict[str, pl.DataFrame],
    *,
    completed_memo: dict[_EnrichmentMemoKey, dict[str, Any]],
    frame_identity: object,
) -> None:
    if not column:
        return
    factor = _banding_factor_for_column(detail, column)
    if factor is None:
        return

    expression = _banding_expression_payload(factor)
    calculation = _banding_calculation_payload(factor)
    ref_cols = expression.get("referenced_columns", [])

    if ref_cols:
        input_sources = _build_input_sources(
            list(ref_cols),
            step,
            steps,
            node_map,
            preamble_ns,
            depth=0,
            max_depth=3,
            completed_memo=completed_memo,
            frame_identity=frame_identity,
        )
        if input_sources:
            calculation["input_sources"] = input_sources
            _fix_upstream_values(input_sources, steps, eager_outputs)

    step.expression = expression
    step.calculation = calculation


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
    parse_expression = trace_mod.parse_expression

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
        if node_map and parse_expression is not None:
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


#: Ordered (substrings -> label) table for sniffing a node's row-lineage
#: operation from its code.  ``cross_join`` precedes ``join`` so a cross
#: join is never mislabelled as a plain join; the first matching row wins.
_OPERATION_TYPE_TABLE: tuple[tuple[tuple[str, ...], str], ...] = (
    ((".group_by(", ".groupby("), "group_by"),
    ((".cross_join(",), "cross_join"),
    ((".join(",), "join"),
    ((".filter(",), "filter"),
    ((".sort(", ".sort_by("), "sort"),
    ((".explode(",), "explode"),
)


def _sniff_operation_type(code: str) -> str:
    """Classify a node's row-lineage operation from its code string."""
    low = code.lower()
    return next(
        (label for subs, label in _OPERATION_TYPE_TABLE if any(s in low for s in subs)),
        "",
    )


def enrich_steps(
    steps: list[TraceStep],
    node_map: dict[str, Any],
    eager_outputs: dict[str, pl.DataFrame],
    parents_of: dict[str, list[str]],
    column: str | None,
    source: str,
    preamble_ns: dict[str, Any] | None = None,
    source_frames_of: Mapping[tuple[str, str], Sequence[str | None]] | None = None,
) -> None:
    """Enrich trace steps in-place with expression/calculation/detail data.

    This is a best-effort pass — if the enrichment modules are unavailable
    or a per-step enrichment fails, the fields stay ``None``.  The
    expression-parser and node-type enricher references are resolved at
    call time via ``haute.trace`` attribute lookup so pytest
    monkeypatching at that location flows through unchanged.

    *source_frames_of* maps a (source, target) node pair to the
    ``sourceHandle`` of every edge between them — the per-edge frame
    selection for multi-frame sources, used to scope parent-frame
    lookups to the frame(s) a node actually consumes.
    """
    trace_mod = _trace_module()
    completed_memo: dict[_EnrichmentMemoKey, dict[str, Any]] = {}
    frame_identity = _enrichment_frame_identity(eager_outputs)

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
                column
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
            if column and (_col_in_schema or _col_in_code):
                parsed = None
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
                # Self-referential assignment (premium = premium * ...):
                # the post-assignment output value must not clobber the
                # RHS input, or the substitution shows the OUTPUT on the
                # right-hand side and a result contradicting the
                # displayed value.  Same guard as the input-sources path
                # (``self_referential_modification`` above).
                eval_values = {**step.input_values, **step.output_values}
                self_referential = (
                    column in step.schema_diff.columns_modified
                    and parsed is not None
                    and column in parsed.referenced_columns
                )
                skip_evaluation = False
                if self_referential:
                    if column in step.input_values:
                        eval_values[column] = step.input_values[column]
                    else:
                        # No pre-assignment value available: showing a
                        # substitution would require the input we don't
                        # have, so present the output value directly
                        # rather than an arithmetically false eval.
                        result = step.output_values.get(column)
                        step.calculation = {
                            "target_column": column,
                            "substituted_text": f"{column} = {_quote_trace_value(result)}",
                            "result_value": result,
                        }
                        skip_evaluation = True
                if not skip_evaluation:
                    try:
                        evaluated = trace_mod.evaluate_expression(
                            code,
                            column,
                            eval_values,
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
                            if (
                                evaluated.expression_type == "window"
                                and column in step.output_values
                            ):
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
                        # Evaluate chain entries in order, feeding each
                        # result forward.  Seeding every entry from the
                        # final output values instead is wrong for
                        # self-referential assignments: the entry that
                        # rewrites ``premium`` would see the
                        # post-assignment premium on its own RHS.  Chain
                        # target columns therefore start from their
                        # PRE-node input values (absent if newly created)
                        # and are filled in as each entry evaluates.
                        combined_values = {**step.input_values, **step.output_values}
                        chain_targets = {p.target_column for p in chain}
                        for target in chain_targets:
                            if target in step.input_values:
                                combined_values[target] = step.input_values[target]
                            else:
                                combined_values.pop(target, None)
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
                                    combined_values[p.target_column] = ev.result_value
                            except Exception as inner_exc:
                                logger.warning(
                                    "chain_entry_eval_failed",
                                    node_id=step.node_id,
                                    column=p.target_column,
                                    error=str(inner_exc),
                                    error_type=type(inner_exc).__name__,
                                    exc_info=True,
                                )
                                entry["error"] = f"chain entry evaluation failed: {inner_exc}"
                                entry["error_type"] = type(inner_exc).__name__
                                entry.setdefault("substituted_text", p.expression_text)
                                fallback = combined_values.get(
                                    p.target_column,
                                    step.output_values.get(p.target_column),
                                )
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
                            completed_memo=completed_memo,
                            frame_identity=frame_identity,
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
                    step.calculation.setdefault("error", f"input_sources build failed: {exc}")
                    step.calculation.setdefault("error_type", type(exc).__name__)

            # --- Rename detection ---
            if column:
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
            if True:
                try:
                    detail: dict[str, Any] | None = None
                    if node_type == "ratingStep":
                        detail = trace_mod.enrich_rating_step(
                            cfg, step.input_values, step.output_values
                        )
                    elif node_type == "banding":
                        # Resolve each factor's source column dtype from
                        # the parent frames so continuous-rule re-matching
                        # compares in the engine's own numeric domain
                        # (Float32-faithful), not widened float64.
                        factor_input_dtypes: dict[str, Any] = {}
                        for pid in parents_of.get(step.node_id, []):
                            pdf = eager_outputs.get(pid)
                            # Multi-frame parents store dict[label, DataFrame].
                            # Scope to the frame(s) this node's incoming
                            # edge(s) actually consume (per-edge sourceHandle)
                            # so a column name that recurs across frames with
                            # a different dtype resolves in the consumed
                            # frame's numeric domain, not by dict-iteration
                            # order across every emitted frame.
                            frames: list[pl.DataFrame]
                            if isinstance(pdf, dict):
                                handles = (source_frames_of or {}).get((pid, step.node_id))
                                frames = [
                                    pdf[h]
                                    for h in dict.fromkeys(handles or ())
                                    if h is not None and h in pdf
                                ] or list(pdf.values())
                            elif pdf is not None:
                                frames = [pdf]
                            else:
                                frames = []
                            for frame in frames:
                                for cname, cdtype in frame.schema.items():
                                    factor_input_dtypes.setdefault(cname, cdtype)
                        detail = trace_mod.enrich_banding(
                            cfg,
                            step.input_values,
                            step.output_values,
                            traced_column=column,
                            factor_input_dtypes=factor_input_dtypes,
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
                    elif node_type == "optimiserApply":
                        parent_ids = parents_of.get(step.node_id, [])
                        input_frames = [
                            eager_outputs[pid]
                            for pid in parent_ids
                            if isinstance(eager_outputs.get(pid), pl.DataFrame)
                        ]
                        source_names = [
                            _sanitize_func_name(node_map[pid].data.label)
                            for pid in parent_ids
                            if pid in node_map
                        ]
                        detail = trace_mod.enrich_optimiser_apply(
                            cfg,
                            step.input_values,
                            step.output_values,
                            input_frames=input_frames,
                            source_names=source_names,
                            source_ids=[pid for pid in parent_ids if pid in node_map],
                        )
                    if detail is not None:
                        step.node_detail = detail
                        if node_type == "banding":
                            _attach_banding_lineage(
                                step,
                                detail,
                                column,
                                steps,
                                node_map,
                                preamble_ns,
                                eager_outputs,
                                completed_memo=completed_memo,
                                frame_identity=frame_identity,
                            )
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
                        parent_row_count = max(
                            parent_row_count,
                            _node_output_row_count(eager_outputs.get(pid)),
                        )
                    # The node's own output may itself be a multi-frame
                    # bundle (the source appearing as an intermediate
                    # step) — same dict guard as the parent side.
                    child_row_count = _node_output_row_count(eager_outputs.get(step.node_id))

                    # Sniff operation type from code string
                    operation_type = _sniff_operation_type(code) if code else ""

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
                    step.row_lineage_type = f"error: row lineage detection failed: {exc}"
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
                step.node_detail.setdefault("error", f"trace enrichment step failed: {exc}")
                step.node_detail.setdefault("error_type", type(exc).__name__)
            continue
