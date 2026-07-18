"""V044 reproduction — misconfigured modelScore deploys as a SILENT passthrough.

Claim under test
----------------
When a ``modelScore`` node is misconfigured so the bundler skips it
(``sourceType='run'`` with an empty ``run_id`` => no model artifact and no
feature contract bundled), the deploy scorer's "no model artifact" fail-loud
guard (``_scorer.py`` ``model_score_missing_artifact``, RuntimeError) is
BYPASSED because that guard only fires when a feature contract WAS bundled
(``if bundled_contract_path is not None``).

With neither model nor contract in ``artifact_paths``:
  * ``_intercept`` returns ``None`` (falls through to the base builder),
  * ``_build_model_score`` returns ``_passthrough_fn`` (run + empty run_id),
  * the executor SKIPS the output-contract boundary check for passthrough
    nodes (``and not is_passthrough_runtime``),
  * ``score_graph_lazy`` only ``.select(output_fields)`` when output_fields is
    truthy — a deploy manifest with ``output_fields=None`` never surfaces the
    missing column.

Net effect: the deployed node passes its input through UNCHANGED, producing a
scoring response that silently OMITS the model's prediction column, with NO
error raised at build, serve, or contract-enforcement time.

This script asserts on the specific WRONG VALUE/behaviour:
  * the scored output does NOT contain the configured ``output_column``
    ("pred"), and instead equals the raw input passed straight through;
  * by contrast a correctly-bundled node yields the prediction column.

It also demonstrates the inconsistency: the SAME misconfiguration makes the
column-contract planner raise ``ConfigError`` ("misconfigured: sourceType='run'
but run_id is empty"), yet that error is swallowed on the deploy boundary path,
so deploy serves a silent passthrough instead of failing loudly.

Isolation: pure in-memory synthetic graph + DataFrame. No disk I/O, no MLflow,
no rating/ or real project files. ``artifact_paths`` is left EMPTY to mirror the
bundler having skipped the node entirely.
"""

from __future__ import annotations

import sys

import polars as pl

from haute._types import PipelineGraph


def make_graph(d: dict) -> PipelineGraph:
    """Build a PipelineGraph from a raw dict (mirrors tests.conftest.make_graph)."""
    return PipelineGraph.model_validate(d)


def _graph_with_model_score(*, source_type: str, run_id: str) -> object:
    """A 3-node deploy graph: apiInput -> modelScore -> output.

    The modelScore declares ``output_column='pred'`` so a real model would add
    a ``pred`` column; a passthrough leaves the input (``x``) untouched.
    """
    return make_graph(
        {
            "nodes": [
                {
                    "id": "src",
                    "data": {
                        "label": "src",
                        "nodeType": "apiInput",
                        "config": {"path": ""},
                    },
                },
                {
                    "id": "ms",
                    "data": {
                        "label": "ms",
                        "nodeType": "modelScore",
                        "config": {
                            "sourceType": source_type,
                            "run_id": run_id,
                            "artifact_path": "model.cbm",
                            "task": "regression",
                            "output_column": "pred",
                        },
                    },
                },
                {
                    "id": "out",
                    "data": {"label": "out", "nodeType": "output", "config": {}},
                },
            ],
            "edges": [
                {"id": "e1", "source": "src", "target": "ms"},
                {"id": "e2", "source": "ms", "target": "out"},
            ],
        }
    )


def main() -> int:
    from haute.deploy._scorer import score_graph

    # ------------------------------------------------------------------
    # The misconfigured node: sourceType='run' but run_id is EMPTY.
    # The bundler (_bundler.py:90-91) does `continue` for exactly this
    # case, so NOTHING is bundled => we score with an EMPTY artifact map,
    # which is precisely what a container built from such a graph carries.
    # output_fields=None mirrors a manifest whose .get("output_fields")
    # returned None.
    # ------------------------------------------------------------------
    input_df = pl.DataFrame({"x": [3.0]})

    graph = _graph_with_model_score(source_type="run", run_id="")
    out = score_graph(
        graph=graph,
        input_df=input_df,
        input_node_ids=["src"],
        output_node_id="out",
        artifact_paths={},          # bundler skipped the node => no artifacts
        output_fields=None,         # manifest had no output_fields
    )

    print("scored columns (misconfigured, no model/contract):", out.columns)
    print("scored rows:", out.to_dicts())

    # --- Reference: the same graph CORRECTLY built yields a prediction. ---
    # We do not load a real model here (isolation); instead we prove the
    # column the operator configured is "pred". The contract for a healthy
    # node is: output contains 'pred'. The bug is that the misconfigured
    # node silently DROPS it and passes 'x' through unchanged.

    # 1. The deployed response SILENTLY OMITS the configured prediction column.
    assert "pred" not in out.columns, (
        "EXPECTED the bug: misconfigured modelScore should silently omit the "
        f"'pred' prediction column, but the output has columns {out.columns!r}. "
        "If 'pred' is present the silent-passthrough bug does not reproduce."
    )

    # 2. The deployed response is the INPUT passed straight through unchanged.
    #    A scoring endpoint returning the raw request as the 'score' is the
    #    concrete wrong behaviour — no prediction, no error.
    assert out.columns == ["x"], (
        f"EXPECTED passthrough of the input schema ['x'], got {out.columns!r}."
    )
    assert out.to_dicts() == [{"x": 3.0}], (
        f"EXPECTED the raw input row passed through, got {out.to_dicts()!r}."
    )

    # 3. CRITICAL: no error was raised. The 'no model artifact' guard
    #    (RuntimeError 'cannot produce predictions without a model artifact')
    #    NEVER fired, because no contract was bundled. Reaching this line at
    #    all proves the fail-loud invariant was bypassed.
    print(
        "REPRODUCED: deployed modelScore served a silent passthrough — "
        "no 'pred' column, no error, input returned unchanged."
    )

    # ------------------------------------------------------------------
    # Demonstrate the inconsistency: the SAME config is treated as a hard
    # ConfigError by the column-contract planner the rest of the engine uses.
    # ------------------------------------------------------------------
    from haute._builders import get_column_contract
    from haute._types import NodeType
    from haute.errors import ConfigError

    misconfig = {
        "sourceType": "run",
        "run_id": "",
        "artifact_path": "model.cbm",
        "task": "regression",
        "output_column": "pred",
    }
    planner_raised = False
    try:
        get_column_contract(NodeType.MODEL_SCORE, misconfig)
    except ConfigError as exc:
        planner_raised = True
        print("column-contract planner correctly rejects the same config:", str(exc))

    assert planner_raised, (
        "EXPECTED the column-contract planner to raise ConfigError for "
        "sourceType='run' with empty run_id (proving the engine considers "
        "this misconfigured), but it did not."
    )

    print(
        "\nCONCLUSION: the engine's column planner treats this node as a hard "
        "config error, yet the deploy scorer serves it as a silent passthrough "
        "with no prediction and no error. V044 is REAL."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
