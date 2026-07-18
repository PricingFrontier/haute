"""ISOLATED reproduction for BUG-1.

Claim: ``haute deploy`` scores every test-quote file TWICE. ``handle_deploy``
calls ``validate_deploy(resolved)`` (which itself runs the full scoring pass
``score_test_quotes`` -> ``score_graph`` at _validators.py:312/375 to surface
runtime errors) and THEN calls ``score_test_quotes(resolved)`` again at
_deploy.py:173 to print per-file status. So for every ``.json`` in
test_quotes/, the entire pruned pipeline (``score_graph`` -> model/optimiser
artifact loads + full Polars collect) is executed twice.

This repro proves the DOUBLE EXECUTION without touching any real project file,
src/, tests/, or rating/. Strategy:

  * Build a minimal synthetic ``resolved`` object exposing only the attributes
    ``validate_deploy`` and ``score_test_quotes`` actually read.
  * Point its ``test_quotes_dir`` at a tempdir holding ONE valid quote file.
  * Monkeypatch the module-level ``score_graph`` symbol that BOTH
    ``score_test_quotes`` invocations resolve through, with a counter that
    records how many times the full pipeline entry point is driven. The
    counter is the load-bearing assertion: the same single file produces TWO
    ``score_graph`` calls across the handle_deploy sequence.

A correct (de-duplicated) implementation would either score once inside
validate_deploy and reuse the results, or skip the internal scoring pass and
score once in handle_deploy -- either way the single file would drive exactly
ONE ``score_graph`` call.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import polars as pl

import haute.deploy._validators as validators
from haute.deploy._validators import score_test_quotes, validate_deploy


def _fake_node(node_id: str, node_type) -> SimpleNamespace:
    """A stand-in GraphNode exposing .id and .data.{nodeType,config}."""
    data = SimpleNamespace(nodeType=node_type, config={})
    return SimpleNamespace(id=node_id, data=data)


def _build_resolved(tq_dir: Path) -> SimpleNamespace:
    """Synthetic ResolvedDeploy that passes every structural check.

    validate_deploy reads: pruned_graph.nodes (.id/.data.nodeType/.data.config),
    pruned_graph.edges (.target), output_node_id, input_node_ids, artifacts,
    input_schema, output_schema, config.test_quotes_dir.
    """
    from haute._types import NodeType

    # apiInput source -> output node, single edge. No databricks dataSource,
    # so check #5 passes. input node is a source (no incoming edge).
    in_node = _fake_node("in", NodeType.API_INPUT)
    out_node = _fake_node("out", NodeType.POLARS)
    edge = SimpleNamespace(source="in", target="out")
    pruned_graph = SimpleNamespace(nodes=[in_node, out_node], edges=[edge])

    config = SimpleNamespace(test_quotes_dir=tq_dir)

    return SimpleNamespace(
        config=config,
        pruned_graph=pruned_graph,
        input_node_ids=["in"],
        output_node_id="out",
        artifacts={},  # no artifacts -> check #4 passes
        input_schema={"premium": "Float64"},  # non-empty -> check #6 passes
        output_schema={"price": "Float64"},  # non-empty -> check #7 passes
    )


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        tq_dir = Path(td)
        # ONE test-quote file with ONE row that supplies the required input col.
        quote_file = tq_dir / "smoke.json"
        quote_file.write_text(json.dumps([{"premium": 100.0}]), encoding="utf-8")

        resolved = _build_resolved(tq_dir)

        # Count how many times the full pipeline entry point runs. Both the
        # internal validate_deploy pass and the handle_deploy pass resolve the
        # SAME module-global ``score_graph`` symbol, so one counter captures
        # both. Return a 1-row frame so _validate_expected_outputs (no expected
        # blocks here) and the ok-status path are satisfied.
        calls: list[dict] = []

        def counting_score_graph(*, graph, input_df, input_node_ids, output_node_id, **_):
            calls.append(
                {
                    "rows_in": input_df.height,
                    "output_node_id": output_node_id,
                }
            )
            return pl.DataFrame({"price": [42.0]})

        validators.score_graph = counting_score_graph
        try:
            # --- Replicate handle_deploy's EXACT sequence (_deploy.py) ---
            # Step 3 (line 157): validate gate. This INTERNALLY scores
            # (validators.py:312) -> +1 score_graph call.
            validate_deploy(resolved)
            calls_after_validate = len(calls)

            # Step 4 (line 173): print per-file status. Scores AGAIN
            # (validators.py:375) -> +1 score_graph call for the SAME file.
            tq_results = score_test_quotes(resolved)
        finally:
            # restore the real symbol (defensive; process is short-lived)
            from haute.deploy._scorer import score_graph as real_score_graph

            validators.score_graph = real_score_graph

        total_calls = len(calls)

        print(f"test-quote files on disk           : 1")
        print(f"score_graph calls during validate  : {calls_after_validate}")
        print(f"score_graph calls TOTAL (validate  : {total_calls}")
        print(f"                       + handle)")
        print(f"per-file status results returned   : {len(tq_results)}")
        print(f"all per-file calls: {calls}")

        # validate_deploy alone already ran the full pipeline once for the file.
        assert calls_after_validate == 1, (
            "expected validate_deploy to run the full scoring pass once for the "
            f"single quote file, got {calls_after_validate}"
        )

        # LOAD-BEARING ASSERTION: the single file drove the full pipeline TWICE
        # across the handle_deploy sequence (gate + status print).
        assert total_calls == 2, (
            "BUG-1 NOT reproduced: expected the single quote file to drive "
            f"score_graph exactly twice (validate gate + status print), got "
            f"{total_calls}"
        )

        # The 2nd run is byte-for-byte the same work as the 1st: same output
        # node, same input rows -> redundant full pipeline + (uncached) model
        # loads + Polars collect, for zero functional gain.
        assert calls[0] == calls[1], (
            "the two score_graph invocations differ; expected identical "
            f"redundant work, got {calls!r}"
        )

        print(
            "\nREPRODUCED: one test-quote file -> score_graph executed TWICE "
            "(validate_deploy internal pass + handle_deploy status pass), "
            "running the full pruned pipeline redundantly."
        )


if __name__ == "__main__":
    main()
