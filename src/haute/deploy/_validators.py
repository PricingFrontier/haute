"""Pre-deploy validation - catch errors before they reach production."""

from __future__ import annotations

import json
import time
from pathlib import Path

import polars as pl

from haute._io import read_user_text
from haute._logging import get_logger
from haute._types import NodeType
from haute.deploy._config import ResolvedDeploy
from haute.deploy._scorer import score_graph

logger = get_logger(component="deploy.validators")


def load_test_quote_file(path: Path) -> list[dict]:
    """Load a test quote JSON file, strip metadata fields (``_`` prefixed).

    Returns a list of cleaned quote dicts ready for scoring.

    Raises:
        ValueError: If the file is not a JSON array.
    """
    raw = json.loads(read_user_text(path))
    if not isinstance(raw, list):
        raise ValueError("Expected a JSON array of quote objects")
    return [{k: v for k, v in row.items() if not k.startswith("_")} for row in raw]


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

    # 5. No unresolved nodes (e.g. Databricks source stubs)
    for node in resolved.pruned_graph.nodes:
        if (
            node.data.nodeType == NodeType.DATA_SOURCE
            and node.data.config.get("sourceType") == "databricks"
        ):
            errors.append(
                f"Node '{node.id}' is a Databricks dataSource (not yet implemented "
                "for deploy). Use an apiInput node for live API data."
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

    if errors:
        logger.warning("validation_failed", error_count=len(errors))
    else:
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
            cleaned = load_test_quote_file(jf)
            input_df = pl.DataFrame(cleaned)

            output = score_graph(
                graph=resolved.pruned_graph,
                input_df=input_df,
                input_node_ids=resolved.input_node_ids,
                output_node_id=resolved.output_node_id,
            )

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
