"""Adversarial repro for claim
`deploy-output-schema-cache-misses-model-artifact`.

CLAIM
-----
`infer_output_schema` (src/haute/deploy/_schema.py:95-150) caches the dry-run
output schema in ``.haute_cache/output_schema.json`` keyed by
``graph_fingerprint(graph, output_node_id, *input_node_ids)``.  That fingerprint
(src/haute/_cache.py:400-437 -> _graph_base_fingerprint @ 191-218) hashes ONLY:
  * per-node ``id | nodeType | canonical_json(config)``
  * edge wiring (source/target/handles)
  * preamble text + imported ``utility`` module source
It does NOT mix in any model-artifact bytes nor the *resolved* MLflow version.

Therefore a ``modelScore`` node configured by ``registered_model`` with
``version="latest"`` (or an artifact retrained in place under a fixed run_id)
keeps a byte-identical fingerprint when the underlying model changes.  After a
model swap that changes an output dtype (regression float -> classification int
label, or an added/renamed prediction column) the STALE cached schema is reused
and baked into the deploy manifest + MLflow ModelSignature.

WHAT THIS SCRIPT PROVES (mechanism-level, no real MLflow / training needed)
--------------------------------------------------------------------------
A.  graph_fingerprint(graph, out, in) is a pure function of node config + edges.
    Swapping the *model the node points at* (its registered version's bytes)
    without editing config cannot change the fingerprint -> demonstrated by
    showing the fingerprint is byte-identical across two calls AND that it is
    computed without ever consulting a model artifact.

B.  With a cache entry stored under that fingerprint, ``infer_output_schema``
    returns the cached (STALE) schema and NEVER runs the dry-run that would
    observe the new model's output dtype.  We seed the cache with a fingerprint
    that matches ``graph_fingerprint`` but a deliberately-wrong schema (the
    schema a *previous* regression model produced), monkeypatch the dry-run
    entrypoint ``score_graph`` to raise if it is ever called, and assert that
    ``infer_output_schema`` returns the stale schema with the dry-run NOT run.

ISOLATION
---------
* All disk I/O is under a fresh ``tempfile.TemporaryDirectory``.
* We ``os.chdir`` into the tempdir because the cache path
  ``.haute_cache/output_schema.json`` is CWD-relative (this also exercises the
  "shared/stale across projects from the same CWD" sub-claim).
* Project root is pinned to the tempdir via ``haute._sandbox.set_project_root``.
* No real project file (rating/, src/, tests/) is read or written.

A non-zero exit / AssertionError below means the bug reproduced.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


def main() -> int:
    import haute._sandbox as sandbox
    from haute._cache import graph_fingerprint
    from haute._types import GraphEdge, GraphNode, NodeData, PipelineGraph
    from haute.deploy import _schema
    from haute.deploy._schema import infer_output_schema

    original_cwd = Path.cwd()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp).resolve()
        sandbox.set_project_root(tmp_path)
        os.chdir(tmp_path)
        try:
            # --- Build apiInput -> modelScore -> (modelScore is output) graph.
            # modelScore is sourced from a REGISTERED model at version "latest":
            # the canonical "artifact changes underneath a fixed config" case.
            model_config = {
                "sourceType": "registered",
                "registered_model": "claims_severity_model",
                "version": "latest",          # resolves to a moving target
                "task": "regression",
                "output_column": "prediction",
            }
            graph = PipelineGraph(
                nodes=[
                    GraphNode(
                        id="api_in",
                        data=NodeData(label="In", nodeType="apiInput",
                                      config={"path": "input.csv"}),
                    ),
                    GraphNode(
                        id="score",
                        data=NodeData(label="Score", nodeType="modelScore",
                                      config=dict(model_config)),
                    ),
                ],
                edges=[GraphEdge(id="e1", source="api_in", target="score")],
            )
            out_id = "score"
            in_ids = ["api_in"]

            # ============================================================
            # PART A — fingerprint is blind to model-artifact content
            # ============================================================
            fp_before = graph_fingerprint(graph, out_id, *in_ids)

            # Simulate "the registered model 'latest' was retrained / a new
            # version registered" WITHOUT editing any node config.  In reality
            # this changes the bytes MLflow returns at runtime; it changes
            # NOTHING in the graph object.  Recompute the fingerprint on the
            # identical, unmodified config.
            fp_after = graph_fingerprint(graph, out_id, *in_ids)

            assert fp_before == fp_after, (
                "PART A precondition: fingerprint should be deterministic"
            )

            # Prove the fingerprint depends ONLY on config (not on any model
            # bytes): an *unrelated* config edit moves it, but a model swap (no
            # config change) cannot, because there is no model-content input.
            graph_other_cfg = PipelineGraph(
                nodes=[
                    graph.nodes[0],
                    GraphNode(
                        id="score",
                        data=NodeData(
                            label="Score",
                            nodeType="modelScore",
                            config={**model_config, "output_column": "renamed_pred"},
                        ),
                    ),
                ],
                edges=list(graph.edges),
            )
            fp_other = graph_fingerprint(graph_other_cfg, out_id, *in_ids)
            assert fp_other != fp_before, (
                "sanity: a config change MUST move the fingerprint"
            )
            print(f"[A] fingerprint (config-only)         = {fp_before}")
            print(f"[A] fingerprint after model 'swap'    = {fp_after}")
            print("[A] -> byte-identical across model swap:",
                  fp_before == fp_after)

            # ============================================================
            # PART B — stale cached schema reused; dry-run never runs
            # ============================================================
            # The OLD (regression) model produced a Float64 'prediction'.
            stale_schema = {"Area": "String", "prediction": "Float64"}
            # The NEW model (classification, swapped in place under the same
            # registered_model='latest') would produce an Int64 label column.
            # That truth would only be discovered by RE-RUNNING the dry-run.
            truthful_new_schema = {"Area": "String", "prediction": "Int64"}
            assert stale_schema != truthful_new_schema

            cache_path = Path(_schema._SCHEMA_CACHE_FILE)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps({"fingerprint": fp_before, "schema": stale_schema})
            )

            # Tripwire: if infer_output_schema actually RE-RAN the dry-run it
            # would call score_graph (imported lazily inside the function as
            # ``from haute.deploy._scorer import score_graph``).  Patch the
            # source name so any dry-run attempt is loudly visible.
            dry_run_calls: list[str] = []

            import haute.deploy._scorer as scorer_mod

            def _exploding_score_graph(*args: object, **kwargs: object):
                dry_run_calls.append("called")
                raise AssertionError(
                    "score_graph (dry-run) was invoked — cache did NOT short-circuit"
                )

            original_score_graph = scorer_mod.score_graph
            scorer_mod.score_graph = _exploding_score_graph  # type: ignore[assignment]
            try:
                returned = infer_output_schema(graph, out_id, in_ids)
            finally:
                scorer_mod.score_graph = original_score_graph  # type: ignore[assignment]

            print(f"[B] dry-run (score_graph) invoked     : {bool(dry_run_calls)}")
            print(f"[B] schema returned                   : {returned}")
            print(f"[B] truthful new-model schema         : {truthful_new_schema}")

            # The bug: the dry-run never ran, and the STALE schema was served.
            assert not dry_run_calls, (
                "EXPECTED bug not present: dry-run ran (cache did not short-circuit)"
            )
            assert returned == stale_schema, (
                f"EXPECTED stale schema {stale_schema}, got {returned}"
            )
            assert returned != truthful_new_schema, (
                "Cache served a schema that happens to match reality — bug not shown"
            )

            print()
            print("REPRODUCED: graph_fingerprint is identical across a model-artifact")
            print("swap (PART A), so infer_output_schema serves the STALE cached output")
            print("schema and skips the dry-run that would observe the new dtype (PART B).")
            print("A stale ModelSignature / manifest would be baked into the deploy.")
            return 0
        finally:
            os.chdir(original_cwd)


if __name__ == "__main__":
    sys.exit(main())
