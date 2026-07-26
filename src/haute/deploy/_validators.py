"""Pre-deploy validation - catch errors before they reach production."""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from numbers import Real
from pathlib import Path
from typing import Any, NamedTuple

import polars as pl

from haute._io import read_user_text
from haute._logging import get_logger
from haute._types import NodeType
from haute.deploy._config import ResolvedDeploy
from haute.deploy._scorer import score_graph

logger = get_logger(component="deploy.validators")


@dataclass(frozen=True)
class _TestQuoteCase:
    input: dict[str, Any]
    expected: dict[str, Any] | None
    tolerance_pct: float


_GOLDEN_QUOTE_KEYS = frozenset({"input", "expected", "tolerance_pct"})


def _strip_metadata_fields(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if not k.startswith("_")}


def _parse_tolerance_pct(value: Any, *, row_index: int) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"test quote row {row_index}: tolerance_pct must be a non-negative number")
    tolerance = float(value)
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError(
            f"test quote row {row_index}: tolerance_pct must be a non-negative finite number"
        )
    # tolerance_pct is consumed as a raw FRACTION (0.01 == 1%), so a value
    # above 1 means >100% tolerance — almost always an operator who wrote
    # ``tolerance_pct: 5`` meaning 5%.  A 500% tolerance would silently pass
    # a wildly wrong quote, so reject it loudly rather than accept the footgun.
    if tolerance > 1:
        raise ValueError(
            f"test quote row {row_index}: tolerance_pct is a fraction (0.01 == 1%), "
            f"so it must not exceed 1; got {tolerance!r}. Did you mean "
            f"{tolerance / 100!r} (i.e. {tolerance!r}%)?"
        )
    return tolerance


def _parse_test_quote_case(row: Any, *, row_index: int) -> _TestQuoteCase:
    if not isinstance(row, dict):
        raise ValueError(f"test quote row {row_index}: expected a JSON object")

    unknown_keys = sorted(
        key for key in row if not key.startswith("_") and key not in _GOLDEN_QUOTE_KEYS
    )
    if unknown_keys:
        raise ValueError(
            f"test quote row {row_index}: unknown golden test quote key(s) "
            f"{unknown_keys}. Use '_' prefixes for metadata fields."
        )

    raw_input = row.get("input")
    if not isinstance(raw_input, dict):
        raise ValueError(f"test quote row {row_index}: input must be a JSON object")

    has_expected_key = "expected" in row
    raw_expected = row.get("expected")
    expected: dict[str, Any] | None
    if not has_expected_key and "tolerance_pct" in row:
        raise ValueError(f"test quote row {row_index}: expected must be a JSON object")
    if not has_expected_key:
        expected = None
    elif isinstance(raw_expected, dict):
        expected = dict(raw_expected)
    else:
        raise ValueError(f"test quote row {row_index}: expected must be a JSON object")

    tolerance_pct = _parse_tolerance_pct(
        row.get("tolerance_pct", 0.0),
        row_index=row_index,
    )
    return _TestQuoteCase(
        input=_strip_metadata_fields(raw_input),
        expected=expected,
        tolerance_pct=tolerance_pct,
    )


def _load_test_quote_cases(path: Path) -> list[_TestQuoteCase]:
    raw = json.loads(read_user_text(path))
    if not isinstance(raw, list):
        raise ValueError("Expected a JSON array of quote objects")
    return [_parse_test_quote_case(row, row_index=i) for i, row in enumerate(raw)]


def load_test_quote_file(path: Path) -> list[dict]:
    """Load a test quote JSON file, strip metadata fields (``_`` prefixed).

    Rows use ``{"input": {...}, "expected": {...}?}``; live smoke tests send
    only the unwrapped API input payload.

    Returns a list of cleaned quote dicts ready for scoring.

    Raises:
        ValueError: If the file is not a JSON array.
    """
    return [case.input for case in _load_test_quote_cases(path)]


def _is_numeric(value: Any) -> bool:
    return isinstance(value, (Decimal, Real)) and not isinstance(value, bool)


def _to_decimal(value: Any) -> Decimal | None:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return Decimal(str(value))
    if isinstance(value, Real):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    return None


class _NumericComparison(NamedTuple):
    """Result of comparing two numeric values within a tolerance.

    ``diff``/``allowed`` are ``None`` when either side could not be converted
    to a finite :class:`~decimal.Decimal` (non-finite float, etc.), in which
    case ``matched`` is ``False``.
    """

    actual_decimal: Decimal | None
    expected_decimal: Decimal | None
    diff: Decimal | None
    allowed: Decimal | None
    matched: bool


def _numeric_comparison(actual: Any, expected: Any, tolerance_pct: float) -> _NumericComparison:
    """Compare two numeric values within ``tolerance_pct`` (a raw fraction).

    Single source of truth for the ``diff``/``allowed``/``matched`` arithmetic
    shared by :func:`_expected_value_matches` and
    :func:`_format_expected_mismatch`.
    """
    actual_decimal = _to_decimal(actual)
    expected_decimal = _to_decimal(expected)
    if actual_decimal is None or expected_decimal is None:
        return _NumericComparison(actual_decimal, expected_decimal, None, None, False)
    diff = abs(actual_decimal - expected_decimal)
    allowed = abs(expected_decimal) * Decimal(str(tolerance_pct))
    return _NumericComparison(actual_decimal, expected_decimal, diff, allowed, diff <= allowed)


def _expected_value_matches(actual: Any, expected: Any, tolerance_pct: float) -> bool:
    if _is_numeric(actual) and _is_numeric(expected):
        return _numeric_comparison(actual, expected, tolerance_pct).matched
    if isinstance(actual, bool) or isinstance(expected, bool):
        return isinstance(actual, bool) and isinstance(expected, bool) and actual is expected
    return bool(actual == expected)


def _format_expected_mismatch(
    *,
    row_index: int,
    column: str,
    actual: Any,
    expected: Any,
    tolerance_pct: float,
) -> str:
    if _is_numeric(actual) and _is_numeric(expected):
        comparison = _numeric_comparison(actual, expected, tolerance_pct)
        if comparison.diff is None or comparison.allowed is None:
            diff: Any = "non-finite"
            allowed: Any = "unavailable"
        else:
            diff = comparison.diff
            allowed = comparison.allowed
        return (
            f"row {row_index} column {column!r} outside tolerance: "
            f"expected={expected!r} actual={actual!r} diff={diff!r} "
            f"allowed={allowed!r} tolerance_pct={tolerance_pct!r}"
        )
    return f"row {row_index} column {column!r} mismatch: expected={expected!r} actual={actual!r}"


def _validate_expected_outputs(
    *,
    cases: list[_TestQuoteCase],
    output: pl.DataFrame,
) -> None:
    expected_cases = [(i, case) for i, case in enumerate(cases) if case.expected is not None]
    if not expected_cases:
        return

    if output.height != len(cases):
        raise ValueError(
            "expected-output validation row count mismatch: "
            f"{len(cases)} expected row(s), {output.height} output row(s)."
        )

    output_columns = set(output.columns)
    output_rows = output.to_dicts()
    errors: list[str] = []
    missing_columns: set[str] = set()

    for row_index, case in expected_cases:
        assert case.expected is not None
        for column, expected_value in case.expected.items():
            if column not in output_columns:
                if column not in missing_columns:
                    errors.append(
                        f"missing expected output column {column!r}; "
                        f"available columns {output.columns!r}"
                    )
                    missing_columns.add(column)
                continue

            actual_value = output_rows[row_index][column]
            if _expected_value_matches(
                actual_value,
                expected_value,
                case.tolerance_pct,
            ):
                continue
            errors.append(
                _format_expected_mismatch(
                    row_index=row_index,
                    column=column,
                    actual=actual_value,
                    expected=expected_value,
                    tolerance_pct=case.tolerance_pct,
                )
            )

    if errors:
        raise ValueError("expected-output validation failed: " + "; ".join(errors))


def validate_deploy(resolved: ResolvedDeploy) -> None:
    """Run all pre-deploy validations.

    Structural errors and test-quote failures are collected first, then
    aggregated into a single :class:`DeployError` so the operator sees the
    full picture in one report rather than a trickle of "fix this, rerun,
    fix that, rerun" cycles.
    """
    from haute.errors import DeployError

    errors: list[str] = []

    # 1. Output node exists in pruned graph
    output_ids = {n.id for n in resolved.pruned_graph.nodes}
    if resolved.output_node_id not in output_ids:
        errors.append(f"Output node '{resolved.output_node_id}' not in pruned graph.")

    # 2. Input nodes exist in pruned graph
    for nid in resolved.input_node_ids:
        if nid not in output_ids:
            errors.append(f"Input node '{nid}' not in pruned graph.")

    # 3. Input nodes are sources (no incoming edges)
    targets_with_incoming = {e.target for e in resolved.pruned_graph.edges}
    for nid in resolved.input_node_ids:
        if nid in targets_with_incoming:
            errors.append(f"Input node '{nid}' has incoming edges - it should be a source node.")

    # 4. All artifacts exist on disk
    for name, path in resolved.artifacts.items():
        if not path.is_file():
            errors.append(f"Artifact '{name}' not found: {path}")

    # 5. Canonical Data Inputs must resolve to a valid direct provider or a
    # ready immutable snapshot. Provider exceptions are deliberately redacted.
    for node in resolved.pruned_graph.nodes:
        if node.data.nodeType != NodeType.DATA_INPUT:
            continue
        try:
            from haute._input_providers import source_cache_identity
            from haute._polars_io_registry import validate_data_input_config
            from haute._sandbox import _get_project_root
            from haute._source_cache import SourceCacheStore

            config = validate_data_input_config(node.data.config)
            if config["cacheMode"] == "snapshot":
                identity = source_cache_identity(
                    config, base_dir=resolved.config.pipeline_file.parent
                )
                SourceCacheStore(_get_project_root()).open_generation(identity)
            elif config["inputType"] not in {"file", "lakehouse", "inline"}:
                errors.append(
                    f"Data Input node '{node.id}' cannot execute directly for deploy; "
                    "a ready snapshot is required."
                )
        except Exception:
            errors.append(
                f"Data Input node '{node.id}' requires a ready, valid matching snapshot "
                "or a supported direct engine before packaging."
            )

    # 6. Input schema is non-empty
    if not resolved.input_schema:
        errors.append("Input schema is empty - could not infer columns from input data.")

    # 7. Output schema is non-empty
    if not resolved.output_schema:
        errors.append("Output schema is empty - dry-run produced no output columns.")

    # 8. Test-quote scoring — any failure here is a fatal deploy error.
    #    We collect them alongside structural errors so the aggregated
    #    report surfaces everything at once.  Two failure modes:
    #      * The quote is missing required input columns → shape-level
    #        fail before any scoring happens.
    #      * The scorer raised an exception → surfaced via the result dict.
    test_quote_errors: list[str] = []
    tq_dir = resolved.config.test_quotes_dir
    if tq_dir is not None and tq_dir.is_dir():
        # Pre-check each quote against the declared input schema; a
        # silently missing column won't be caught by a passthrough graph
        # and would deploy an API that accepts garbage quotes.
        required_cols = set(resolved.input_schema or {})
        for jf in sorted(tq_dir.glob("*.json")):
            try:
                cleaned = load_test_quote_file(jf)
            except Exception as exc:
                test_quote_errors.append(f"test quote {jf.name!r} failed: could not parse ({exc})")
                continue
            for row in cleaned:
                missing = sorted(required_cols - set(row))
                if missing:
                    test_quote_errors.append(
                        f"test quote {jf.name!r} failed: missing required input "
                        f"column(s) {missing}; provided columns {sorted(row)}."
                    )
                    break

        # Actual scoring pass — surfaces runtime errors.
        quote_results = score_test_quotes(resolved, tq_dir)
        for q in quote_results:
            if q.get("status") == "error":
                test_quote_errors.append(
                    f"test quote {q.get('file', '<unknown>')!r} failed: {q.get('error', '')}"
                )

    if errors or test_quote_errors:
        logger.warning(
            "validation_failed",
            structural_errors=len(errors),
            test_quote_errors=len(test_quote_errors),
        )
        combined = errors + test_quote_errors
        raise DeployError(
            "Deploy validation failed:\n  - " + "\n  - ".join(combined),
            structural_errors=errors,
            test_quote_errors=test_quote_errors,
        )

    # Control only reaches here when both ``errors`` and ``test_quote_errors``
    # are empty (the branch above raises otherwise), so validation passed.
    logger.info("validation_passed")
    return None


def score_test_quotes(
    resolved: ResolvedDeploy,
    test_quotes_dir: Path | None = None,
) -> list[dict[str, str | int | float]]:
    """Score every JSON file in the test_quotes directory.

    Each JSON file should contain a list of dicts (quote objects).

    Args:
        resolved: Fully resolved deployment config.
        test_quotes_dir: Directory containing ``.json`` files.
            Falls back to ``resolved.config.test_quotes_dir``.

    Returns:
        List of result dicts with keys: file, rows, status, time_ms, error.

    Raises:
        Nothing - errors are captured in the result dicts.
    """
    tq_dir = test_quotes_dir or resolved.config.test_quotes_dir
    if tq_dir is None or not tq_dir.is_dir():
        return []

    json_files = sorted(tq_dir.glob("*.json"))
    if not json_files:
        return []

    results: list[dict[str, str | int | float]] = []

    for jf in json_files:
        t0 = time.perf_counter()
        try:
            cases = _load_test_quote_cases(jf)
            cleaned = [case.input for case in cases]
            input_df = pl.DataFrame(cleaned)

            output = score_graph(
                graph=resolved.pruned_graph,
                input_df=input_df,
                input_node_ids=resolved.input_node_ids,
                output_node_id=resolved.output_node_id,
                artifact_paths={name: str(path) for name, path in resolved.artifacts.items()},
            )
            _validate_expected_outputs(cases=cases, output=output)

            elapsed = (time.perf_counter() - t0) * 1000
            results.append(
                {
                    "file": jf.name,
                    "rows": len(output),
                    "status": "ok",
                    "time_ms": round(elapsed, 1),
                    "error": "",
                }
            )
        except Exception as exc:
            elapsed = (time.perf_counter() - t0) * 1000
            results.append(
                {
                    "file": jf.name,
                    "rows": 0,
                    "status": "error",
                    "time_ms": round(elapsed, 1),
                    "error": str(exc),
                }
            )

    return results
