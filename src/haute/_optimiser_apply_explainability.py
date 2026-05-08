"""Trace explainability helpers for optimiserApply nodes."""

from __future__ import annotations

import math
from typing import Any, cast

import polars as pl

from haute._builders import _prepare_online_apply_frame, _select_optimiser_apply_input
from haute._logging import get_logger
from haute._trace_correlation import _jsonify_row, _trace_values_match

logger = get_logger(component="optimiser_apply_explainability")


class OptimiserApplyTraceError(RuntimeError):
    """Raised when optimiserApply trace detail cannot reconcile with runtime output."""


def explain_optimiser_apply_from_config(
    config: dict[str, Any],
    input_row: dict[str, Any],
    output_row: dict[str, Any],
    *,
    input_frames: list[pl.DataFrame | pl.LazyFrame] | tuple[pl.DataFrame | pl.LazyFrame, ...],
    source_names: list[str] | None = None,
    source_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Build structured optimiserApply trace detail for one clicked output row."""
    artifact: dict[str, Any] | None = None
    try:
        artifact = _load_artifact_from_config(config)
        # Treat absent ``mode`` as the canonical online default, but reject
        # an explicit empty value — see _required_artifact_column for the
        # rationale on distinguishing absent from blank.
        if "mode" in artifact:
            raw_mode = artifact["mode"]
            if not isinstance(raw_mode, str) or not raw_mode:
                raise OptimiserApplyTraceError(
                    f"optimiserApply artifact field 'mode' must be a non-empty string; "
                    f"got {raw_mode!r}"
                )
            mode = raw_mode
        else:
            mode = "online"
        parent_frame = _select_frame_from_inputs(
            config,
            artifact,
            input_frames,
            source_names or [],
            source_ids or [],
        )
        if mode == "ratebook":
            return _explain_ratebook(config, artifact, parent_frame, input_row, output_row)
        return _explain_online(config, artifact, parent_frame, output_row)
    except ImportError as exc:
        # The price_contour package is a hard runtime dep for online apply
        # tracing.  When the env is misconfigured we want the user to see
        # which library is missing, not a raw "No module named ..." string,
        # so deploy operators can fix the environment without grepping the
        # traceback.
        #
        # CPython sets ``exc.name`` automatically for ``import x``
        # / ``from x import y`` failures, but a re-raised or chained
        # ImportError can have ``name=None``.  Fall back to a generic phrasing
        # in that case rather than exposing the literal ``None`` to the user.
        if exc.name:
            wrapped = OptimiserApplyTraceError(
                f"optimiserApply trace requires the {exc.name!r} library to be installed; "
                f"install it (e.g. `uv add {exc.name}`) and retry. Original error: {exc}"
            )
        else:
            wrapped = OptimiserApplyTraceError(
                f"optimiserApply trace failed to import a required library; "
                f"check the deploy environment. Original error: {exc}"
            )
        return _error_detail(config, artifact, wrapped)
    except Exception as exc:
        return _error_detail(config, artifact, exc)


def _load_artifact_from_config(config: dict[str, Any]) -> dict[str, Any]:
    source_type = str(config.get("sourceType", "") or "")
    if source_type in {"run", "registered"}:
        from haute._optimiser_io import load_mlflow_optimiser_artifact

        return load_mlflow_optimiser_artifact(
            source_type=source_type,
            run_id=str(config.get("run_id", "") or ""),
            registered_model=str(config.get("registered_model", "") or ""),
            version=str(config.get("version", "latest") or "latest"),
        )
    if source_type == "file" and config.get("artifact_path"):
        from haute._optimiser_io import load_optimiser_artifact

        return load_optimiser_artifact(str(config["artifact_path"]))
    raise OptimiserApplyTraceError(
        "optimiserApply trace requires a configured file or MLflow artifact source"
    )


def _select_frame_from_inputs(
    config: dict[str, Any],
    artifact: dict[str, Any],
    input_frames: list[pl.DataFrame | pl.LazyFrame] | tuple[pl.DataFrame | pl.LazyFrame, ...],
    source_names: list[str],
    source_ids: list[str],
) -> pl.LazyFrame:
    if not input_frames:
        raise OptimiserApplyTraceError(
            "optimiserApply trace could not find a materialised parent DataFrame"
        )
    return _select_optimiser_apply_input(
        tuple(_as_lazy(frame) for frame in input_frames),
        artifact,
        str(config.get("ratebook_input", "") or ""),
        source_names,
        source_ids,
    )


def _as_lazy(frame: pl.DataFrame | pl.LazyFrame) -> pl.LazyFrame:
    if isinstance(frame, pl.LazyFrame):
        return frame
    if isinstance(frame, pl.DataFrame):
        return frame.lazy()
    raise OptimiserApplyTraceError(
        f"optimiserApply trace expected Polars input frame, got {type(frame).__name__}"
    )


def _explain_online(
    config: dict[str, Any],
    artifact: dict[str, Any],
    parent_frame: pl.LazyFrame,
    output_row: dict[str, Any],
) -> dict[str, Any]:
    from price_contour import ApplyOptimiser

    # Use plain ``.get(name, default)`` rather than ``.get(name, default) or default``:
    # the previous pattern silently rewrote a deliberate empty string to the
    # default, which hid configuration bugs.  An explicit empty value is a
    # misconfiguration we want to surface — runtime apply will fail loudly on
    # the resulting column lookup, but we add an early validation here so the
    # error names the offending artifact key rather than producing a bare
    # ``polars.ColumnNotFoundError``.
    qid_col = _required_artifact_column(artifact, "quote_id", default="quote_id")
    step_col = _required_artifact_column(artifact, "scenario_index", default="scenario_index")
    value_col = _required_artifact_column(artifact, "scenario_value", default="scenario_value")
    objective_col = _required_artifact_column(artifact, "objective", default="expected_income")
    constraints = artifact.get("constraints") or {}
    lambdas = artifact.get("lambdas") or {}
    output_col = _required_config_column(
        config,
        "optimised_value_column",
        default="optimal_scenario_value",
    )

    df = _prepare_online_apply_frame(parent_frame, artifact)
    applier = ApplyOptimiser(
        lambdas=lambdas,
        objective=objective_col,
        constraints=constraints,
        quote_id=qid_col,
        scenario_index=step_col,
        scenario_value=value_col,
    )
    explained = applier.with_explainer_columns(df)

    quote_id_value = output_row.get("quote_id", output_row.get(qid_col))
    if quote_id_value is None:
        raise OptimiserApplyTraceError(
            "optimiserApply online trace could not resolve quote id from output row"
        )

    quote_rows = explained.filter(pl.col(qid_col).cast(pl.Utf8) == str(quote_id_value)).sort(
        step_col
    )
    if quote_rows.is_empty():
        raise OptimiserApplyTraceError(
            f"no optimiser candidates found for quote_id={quote_id_value!r}"
        )

    candidates = [
        _online_candidate_payload(row, constraints, objective_col, step_col, value_col)
        for row in quote_rows.iter_rows(named=True)
    ]
    selected = [row for row in candidates if row.get("selected") is True]
    baseline = [row for row in candidates if row.get("is_baseline") is True]
    if len(selected) != 1:
        raise OptimiserApplyTraceError(
            "optimiserApply online trace expected exactly one selected candidate "
            f"for quote_id={quote_id_value!r}, found {len(selected)}"
        )
    if len(baseline) != 1:
        raise OptimiserApplyTraceError(
            "optimiserApply online trace expected exactly one baseline candidate "
            f"for quote_id={quote_id_value!r}, found {len(baseline)}"
        )

    selected_row = selected[0]
    output_value = output_row.get(output_col)
    if output_col not in output_row:
        raise OptimiserApplyTraceError(
            f"clicked optimiserApply output row is missing output column {output_col!r}"
        )
    if not _values_match(selected_row.get("scenario_value"), output_value):
        raise OptimiserApplyTraceError(
            "optimiserApply trace selected scenario does not match output "
            f"{output_col}: selected={selected_row.get('scenario_value')!r}, "
            f"output={output_value!r}"
        )

    return {
        "detail_type": "optimiser_apply",
        "mode": "online",
        "status": "ok",
        "artifact_version": artifact.get("version"),
        "columns": {
            "quote_id": qid_col,
            "scenario_index": step_col,
            "scenario_value": value_col,
            "objective": objective_col,
        },
        "constraints": {
            name: {
                "spec": spec,
                "lambda": float(lambdas.get(name, 0.0)),
                "linearised_column": f"linearised_{name}",
                "lambda_term_column": f"lambda_term_{name}",
            }
            for name, spec in constraints.items()
        },
        "lambdas": dict(lambdas),
        "output": {"column": output_col, "value": _json_safe(output_value)},
        "output_column": output_col,
        "output_value": _json_safe(output_value),
        "quote_id_column": qid_col,
        "quote_id_value": _json_safe(quote_id_value),
        "scenario_index_column": step_col,
        "scenario_value_column": value_col,
        "objective_column": objective_col,
        "candidates": candidates,
        "selected": selected_row,
        "baseline": baseline[0],
    }


def _online_candidate_payload(
    row: dict[str, Any],
    constraints: dict[str, Any],
    objective_col: str,
    step_col: str,
    value_col: str,
) -> dict[str, Any]:
    raw = _jsonify_row(dict(row))
    payload = dict(raw)
    constraint_values: dict[str, Any] = {}
    linearised: dict[str, Any] = {}
    lambda_terms: dict[str, Any] = {}
    for name in constraints:
        constraint_values[name] = raw.get(name)
        linearised[name] = raw.get(f"linearised_{name}")
        lambda_terms[name] = raw.get(f"lambda_term_{name}")
    payload.update(
        {
            "scenario_index": raw.get(step_col),
            "scenario_value": raw.get(value_col),
            "objective": raw.get(objective_col),
            "decision_score": raw.get("decision_score"),
            "selected": raw.get("selected") is True,
            "is_baseline": raw.get("is_baseline") is True,
            "constraints": constraint_values,
            "linearised_constraints": linearised,
            "lambda_terms": lambda_terms,
            "row": raw,
        }
    )
    return payload


def _explain_ratebook(
    config: dict[str, Any],
    artifact: dict[str, Any],
    parent_frame: pl.LazyFrame,
    input_row: dict[str, Any],  # noqa: ARG001 - retained for public signature parity.
    output_row: dict[str, Any],
) -> dict[str, Any]:
    matched_input = _match_ratebook_input_row(parent_frame, output_row)
    factor_tables = artifact.get("factor_tables", {}) or {}
    output_col = _required_config_column(
        config,
        "optimised_value_column",
        default="optimised_factor",
    )
    running_product = 1.0
    factor_ladder: list[dict[str, Any]] = []

    for factor_name, entries in factor_tables.items():
        if not entries:
            continue
        valid_entries = [
            entry for entry in entries if isinstance(entry, dict) and "__factor_group__" in entry
        ]
        if not valid_entries:
            continue
        if factor_name not in matched_input:
            raise OptimiserApplyTraceError(
                f"ratebook input row is missing factor column {factor_name!r}"
            )
        input_value = matched_input.get(factor_name)
        matched_entry = _match_ratebook_entry(valid_entries, input_value)
        factor_value: Any
        if matched_entry is None:
            default_used = True
            factor_value = 1.0
        else:
            default_used = False
            factor_value = matched_entry.get("optimal_scenario_value")
        numeric_factor = _numeric_or_error(factor_value, factor_name)
        factor_col = f"{factor_name}_optimised_factor"
        if factor_col in output_row and not _values_match(output_row.get(factor_col), factor_value):
            raise OptimiserApplyTraceError(
                "optimiserApply ratebook trace factor does not reconcile with output "
                f"{factor_col}: expected={factor_value!r}, output={output_row.get(factor_col)!r}"
            )

        before = running_product
        running_product *= numeric_factor
        factor_ladder.append(
            {
                "factor": factor_name,
                "name": factor_name,
                "input_value": _json_safe(input_value),
                "factor_value": _json_safe(factor_value),
                "factor_column": factor_col,
                "output_column": factor_col,
                "running_product_before": _json_safe(before),
                "running_product_after": _json_safe(running_product),
                "running_total": _json_safe(running_product),
                "status": "default" if default_used else "matched",
                "default_used": default_used,
                "matched": matched_entry is not None,
                "matched_entry": _json_safe(matched_entry),
            }
        )

    if factor_ladder and output_col not in output_row:
        raise OptimiserApplyTraceError(
            f"clicked optimiserApply output row is missing output column {output_col!r}"
        )
    final_value = output_row.get(output_col)
    if factor_ladder and not _values_match(final_value, running_product):
        raise OptimiserApplyTraceError(
            "optimiserApply ratebook trace factors do not reconcile with output "
            f"{output_col}: factors={running_product!r}, output={final_value!r}"
        )

    detail: dict[str, Any] = {
        "detail_type": "optimiser_apply",
        "mode": "ratebook",
        "status": "ok",
        "artifact_version": artifact.get("version"),
        "output": {"column": output_col, "value": _json_safe(final_value)},
        "output_column": output_col,
        "output_value": _json_safe(final_value),
        "base_value": 1.0,
        "factor_ladder": factor_ladder,
        "factors": factor_ladder,
        "final_value": _json_safe(final_value),
        "lambdas": dict(artifact.get("lambdas") or {}),
        "constraints": dict(artifact.get("constraints") or {}),
        "input_row": _json_safe(matched_input),
    }
    if not factor_ladder:
        detail["message"] = "No ratebook factor tables were available in the optimiser artifact."
    return detail


def _required_artifact_column(
    artifact: dict[str, Any],
    key: str,
    *,
    default: str,
) -> str:
    """Return the artifact's column name for *key*, defaulting only when absent.

    Distinguishes "key missing" (legitimate, fall back to default) from "key
    present but blank" (misconfiguration, raise) — the earlier ``str(... or default)``
    pattern collapsed the two cases and hid bugs.
    """
    if key not in artifact:
        return default
    value = artifact[key]
    if not isinstance(value, str) or not value:
        raise OptimiserApplyTraceError(
            f"optimiserApply artifact field {key!r} must be a non-empty string; got {value!r}"
        )
    return value


def _required_config_column(config: dict[str, Any], key: str, *, default: str) -> str:
    """Return a configured column name, defaulting only when the key is absent."""
    if key not in config:
        return default
    value = config[key]
    if not isinstance(value, str) or not value:
        raise OptimiserApplyTraceError(
            f"optimiserApply config field {key!r} must be a non-empty string; got {value!r}"
        )
    return value


def _match_ratebook_input_row(
    parent_frame: pl.LazyFrame,
    output_row: dict[str, Any],
) -> dict[str, Any]:
    """Locate the ratebook input row that produced the clicked output row.

    Pushes the equality predicate into Polars (``.filter(...)``) and only
    materialises the matching row, so a ratebook trace on a large input frame
    no longer collects the whole DataFrame and walks it in Python.
    """
    schema_names = parent_frame.collect_schema().names()
    shared = [column for column in schema_names if column in output_row]
    if not shared:
        raise OptimiserApplyTraceError(
            "selected ratebook input frame shares no columns with clicked output row"
        )

    predicate = _build_polars_equality_predicate(shared, output_row)

    # The Polars predicate is the fast path — it only materialises the
    # matching row.  Two ways the fast path can miss:
    #  1. The predicate evaluates to "no rows" (returned ``matched`` is empty).
    #  2. Polars rejects the predicate at evaluation time (e.g. ``ComputeError``
    #     "cannot compare string with numeric type" when output_row carries a
    #     stringly-typed value for an Int64 column).
    # Both fall through to the Python scan, which tolerates cross-dtype values
    # via ``_trace_values_match``'s string-cast comparator.  Catching
    # ``PolarsError`` (the base of ColumnNotFound/Schema/Compute/InvalidOperation)
    # rather than bare ``Exception`` keeps unrelated bugs surfacing as errors.
    try:
        matched = parent_frame.filter(predicate).head(1).collect(engine="streaming")
    except pl.exceptions.PolarsError:
        return _python_match_ratebook_input_row(parent_frame, output_row, shared)
    if matched.is_empty():
        return _python_match_ratebook_input_row(parent_frame, output_row, shared)
    return _jsonify_row(dict(matched.row(0, named=True)))


def _build_polars_equality_predicate(
    shared: list[str],
    output_row: dict[str, Any],
) -> pl.Expr:
    """And-combine per-column equality predicates for the shared columns.

    ``shared`` is guaranteed non-empty by the caller, so the result is always
    a real predicate (no ``None`` to handle downstream).
    """
    predicate = _polars_equality_predicate(shared[0], output_row.get(shared[0]))
    for column in shared[1:]:
        predicate = predicate & _polars_equality_predicate(column, output_row.get(column))
    return predicate


def _polars_equality_predicate(column: str, value: Any) -> pl.Expr:
    """Build the strictest Polars equality predicate we can for *value*.

    Nulls and NaN need explicit handling because Polars ``==`` propagates them.
    Floats use ``==`` for the fast path; NaN/tolerance comparisons fall through
    to :func:`_python_match_ratebook_input_row`.
    """
    if value is None:
        return pl.col(column).is_null()
    if isinstance(value, float) and math.isnan(value):
        return pl.col(column).is_nan()
    return cast(pl.Expr, pl.col(column) == value)


def _python_match_ratebook_input_row(
    parent_frame: pl.LazyFrame,
    output_row: dict[str, Any],
    shared: list[str],
) -> dict[str, Any]:
    """Fallback: scan the input frame in Python for tolerance-aware matches."""
    input_df = parent_frame.collect(engine="streaming")
    if input_df.is_empty():
        raise OptimiserApplyTraceError("selected ratebook input frame is empty")
    for row in input_df.iter_rows(named=True):
        if all(_values_match(row.get(column), output_row.get(column)) for column in shared):
            return _jsonify_row(dict(row))
    raise OptimiserApplyTraceError(
        "could not reconcile clicked ratebook output row to the selected input frame"
    )


def _match_ratebook_entry(entries: list[dict[str, Any]], input_value: Any) -> dict[str, Any] | None:
    # ``_apply_rating_table`` deduplicates with ``unique(subset=factors, keep="last")``
    # before joining, so a duplicate ``__factor_group__`` resolves to the LAST
    # entry at runtime.  Walking ``entries`` in reverse mirrors that semantics
    # — see ``test_ratebook_match_entry_uses_last_duplicate_to_match_runtime``.
    for entry in reversed(entries):
        if str(entry.get("__factor_group__")) == str(input_value):
            return entry
    return None


def _numeric_or_error(value: Any, factor_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise OptimiserApplyTraceError(
            f"ratebook factor {factor_name!r} has non-numeric value {value!r}"
        ) from exc
    if not math.isfinite(number):
        raise OptimiserApplyTraceError(
            f"ratebook factor {factor_name!r} has non-finite value {value!r}"
        )
    return number


def _values_match(left: Any, right: Any) -> bool:
    # ``_trace_values_match`` does an exact (string-cast) comparison — it
    # catches stringly-typed columns, but rejects values that should compare
    # equal under floating-point tolerance.  Ratebook factor reconciliation
    # routes through here, so we add a tolerant numeric branch for the cases
    # the strict comparator declines.
    if _trace_values_match(left, right):
        return True
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        if _is_nan(left) and _is_nan(right):
            return True
        return math.isclose(float(left), float(right), rel_tol=1e-6, abs_tol=1e-6)
    return False


def _is_nan(value: Any) -> bool:
    return isinstance(value, float) and math.isnan(value)


def _error_detail(
    config: dict[str, Any],
    artifact: dict[str, Any] | None,
    exc: Exception,
) -> dict[str, Any]:
    mode = "unknown"
    if artifact is not None:
        mode = str(artifact.get("mode", "online") or "online")
    elif config.get("mode"):
        mode = str(config.get("mode"))
    logger.warning(
        "optimiser_apply_trace_enrichment_failed",
        mode=mode,
        error=str(exc),
        error_type=type(exc).__name__,
        exc_info=True,
    )
    return {
        "detail_type": "optimiser_apply",
        "mode": mode,
        "status": "error",
        "error": str(exc),
        "error_type": type(exc).__name__,
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
