"""Adversarial repro for claim:
  passthrough-body-node-types-not-runtime-equivalent

CLAIM: For node types whose generated standalone body is `return {first}`
(optimiser, optimiserApply, modelling, scenarioExpander when the user wrote no
post-code), a standalone ``pipeline.run()`` of the SAVED .py file is silently a
no-op, whereas the in-canvas ``execute_graph`` path applies the real operation
via the exec-side registry builder (``_build_*``). banding / ratingStep /
modelScore do NOT have this gap because their generated bodies embed
``apply_*_from_config`` / ``score_from_config`` calls.

This script proves BOTH halves at the authoritative layers, with NO MLflow /
artifact machinery required (the decisive difference is at builder DISPATCH and
at the literal generated SOURCE, both fully deterministic):

  (1) STANDALONE half — generate the real saved-file source via
      ``graph_to_code`` for an optimiserApply node configured with a *valid*
      MLflow source (sourceType='run', run_id='abc'). Assert the generated
      function body is a pure passthrough: it returns its input and contains
      NONE of the artifact-loading / version-column machinery. => standalone
      pipeline.run() of this file is a no-op.

  (2) EXECUTOR half — call the real ``_build_node_fn`` on the EQUIVALENT
      executor-side node (identical config). Assert it returns the REAL
      ``optimiser_apply_fn`` (i.e. NOT ``_passthrough_fn``) — proving the canvas
      executor performs the artifact load + ``__optimiser_version__`` column add
      for the same config. The asymmetry is therefore real.

  (3) CONTRAST — generate the source for a modelScore node and assert its body
      DOES call ``score_from_config`` (gap closed), AND a *passthrough-built*
      optimiserApply (no source configured) yields ``_passthrough_fn`` on BOTH
      sides (so the gap is specifically the configured-source case).

We assert on SPECIFIC strings / object identity (expected vs actual), not merely
that "something raised". The script PASSES iff the claim is REPRODUCED.

Isolation: pure in-memory graphs; the only disk touch is a tempfile project
root set via haute._sandbox.set_project_root. No reads/writes of rating/, src/,
tests/, or any real project file.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import polars as pl

from haute._builders import _build_node_fn, _passthrough_fn
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.codegen import graph_to_code


def _constant_source(nid: str) -> GraphNode:
    return GraphNode(
        id=nid,
        data=NodeData(
            label=nid,
            nodeType=NodeType.CONSTANT,
            config={"values": [{"name": "premium", "value": "100"}]},
        ),
    )


def _optimiser_apply(nid: str, config: dict) -> GraphNode:
    return GraphNode(
        id=nid,
        data=NodeData(label=nid, nodeType=NodeType.OPTIMISER_APPLY, config=config),
    )


def _model_score(nid: str, config: dict) -> GraphNode:
    return GraphNode(
        id=nid,
        data=NodeData(label=nid, nodeType=NodeType.MODEL_SCORE, config=config),
    )


def _edge(src: str, tgt: str) -> GraphEdge:
    return GraphEdge(id=f"e_{src}_{tgt}", source=src, target=tgt)


# Configured optimiserApply: a *valid* MLflow run source. Per _build_optimiser_apply
# this trips _has_mlflow=True -> real optimiser_apply_fn on the executor side.
_CONFIGURED_OA = {
    "sourceType": "run",
    "run_id": "abc",
    "ratebook_input": "rb",
    "version_column": "__optimiser_version__",
}


def _extract_function_body(src: str, func_name: str) -> str:
    """Return the source lines of ``def func_name(...)`` up to the next
    top-level ``def``/``@`` (cheap, good enough to inspect a single body)."""
    lines = src.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.startswith(f"def {func_name}("):
            start = i
            break
    assert start is not None, f"function {func_name!r} not found in generated source:\n{src}"
    body: list[str] = []
    for ln in lines[start + 1 :]:
        if ln and not ln[0].isspace():  # next top-level statement / decorator
            break
        body.append(ln)
    return "\n".join(body)


def half_1_standalone_is_noop() -> dict:
    """Generate the real saved-file source; show optimiserApply body is a no-op."""
    nodes = [_constant_source("src"), _optimiser_apply("oa", dict(_CONFIGURED_OA))]
    edges = [_edge("src", "oa")]
    graph = PipelineGraph(nodes=nodes, edges=edges)

    code = graph_to_code(graph, pipeline_name="main")
    body = _extract_function_body(code, "oa")

    # The generated body returns its input parameter and contains none of the
    # exec-side machinery. We look for the function-name marker '@pipeline.optimiser_apply'
    # in the full source and the passthrough 'return src' in the body.
    has_decorator = "@pipeline.optimiser_apply(" in code
    body_returns_input = "return src" in body
    has_artifact_load = "load_mlflow_optimiser_artifact" in code or "load_optimiser_artifact" in code
    has_dispatch_apply = "_dispatch_apply" in code or "ApplyOptimiser" in code
    mentions_version_col = "__optimiser_version__" in body  # only in decorator, not body logic

    return {
        "generated_source": code,
        "optimiser_apply_body": body,
        "has_decorator": has_decorator,
        "body_returns_input": body_returns_input,
        "has_artifact_load_in_file": has_artifact_load,
        "has_dispatch_apply_in_file": has_dispatch_apply,
        "version_col_logic_in_body": mentions_version_col,
    }


def half_2_executor_does_real_work() -> dict:
    """Same config on the executor side -> real optimiser_apply_fn, not passthrough."""
    node = _optimiser_apply("oa", dict(_CONFIGURED_OA))
    func_name, fn, is_source = _build_node_fn(
        node,
        source_names=["src"],
        source_ids=["src"],
    )
    return {
        "func_name": func_name,
        "is_passthrough": fn is _passthrough_fn,
        "fn_qualname": getattr(fn, "__qualname__", getattr(fn, "__name__", repr(fn))),
        "is_source": is_source,
    }


def contrast_modelscore_closes_gap() -> dict:
    """modelScore generated body DOES call score_from_config (no asymmetry)."""
    ms_config = {
        "sourceType": "run",
        "run_id": "xyz",
        "output_column": "prediction",
    }
    nodes = [_constant_source("src"), _model_score("ms", ms_config)]
    edges = [_edge("src", "ms")]
    graph = PipelineGraph(nodes=nodes, edges=edges)
    code = graph_to_code(graph, pipeline_name="main")
    body = _extract_function_body(code, "ms")
    return {
        "model_score_body": body,
        "body_calls_score_from_config": "score_from_config" in body,
    }


def contrast_unconfigured_passthrough_both_sides() -> dict:
    """With NO source configured, optimiserApply is passthrough on BOTH sides,
    so the gap is specifically the *configured-source* case."""
    node = _optimiser_apply("oa", {})  # no sourceType / run_id / artifact_path
    _fn_name, fn, _is_src = _build_node_fn(node, source_names=["src"], source_ids=["src"])
    return {"unconfigured_executor_is_passthrough": fn is _passthrough_fn}


def main() -> None:
    # Project root for any path-portability logic in codegen; never touches real files.
    with tempfile.TemporaryDirectory() as tmp:
        import haute._sandbox as _sandbox

        _sandbox.set_project_root(Path(tmp))

        print("=" * 78)
        print("HALF 1 — standalone saved-file optimiserApply body (graph_to_code)")
        h1 = half_1_standalone_is_noop()
        print("  generated body:")
        for ln in h1["optimiser_apply_body"].splitlines():
            print(f"      {ln}")
        for k in (
            "has_decorator",
            "body_returns_input",
            "has_artifact_load_in_file",
            "has_dispatch_apply_in_file",
            "version_col_logic_in_body",
        ):
            print(f"  {k}: {h1[k]}")

        print("=" * 78)
        print("HALF 2 — executor _build_node_fn for the SAME config")
        h2 = half_2_executor_does_real_work()
        for k, v in h2.items():
            print(f"  {k}: {v}")

        print("=" * 78)
        print("CONTRAST — modelScore body closes the gap")
        c1 = contrast_modelscore_closes_gap()
        print(f"  model_score body calls score_from_config: {c1['body_calls_score_from_config']}")
        print(f"  model_score body:")
        for ln in c1["model_score_body"].splitlines():
            print(f"      {ln}")

        print("=" * 78)
        print("CONTRAST — unconfigured optimiserApply is passthrough on executor side too")
        c2 = contrast_unconfigured_passthrough_both_sides()
        print(f"  unconfigured_executor_is_passthrough: {c2['unconfigured_executor_is_passthrough']}")

        print("=" * 78)
        # ---------------- ASSERTIONS encoding the CLAIM ----------------------
        # The claim is REPRODUCED iff:
        #   * the standalone optimiserApply body is a pure passthrough (returns
        #     input; no artifact load / no _dispatch_apply / no version-col logic
        #     in the body), AND
        #   * the executor builds the REAL optimiser_apply_fn (not passthrough)
        #     for the identical config.
        # These two facts together prove: same saved graph, applied in canvas,
        # adds __optimiser_version__; run standalone, does nothing.

        assert h1["has_decorator"], (
            "Generated source did not contain the @pipeline.optimiser_apply "
            f"decorator — codegen layout changed. Source:\n{h1['generated_source']}"
        )
        assert h1["body_returns_input"], (
            "REFUTED-basis: expected the standalone optimiserApply body to be "
            f"`return src`, but it was:\n{h1['optimiser_apply_body']}"
        )
        assert not h1["has_artifact_load_in_file"], (
            "CLAIM WEAKENED: the standalone file DOES load an optimiser artifact "
            "— the body is not a no-op after all. Body:\n"
            f"{h1['optimiser_apply_body']}"
        )
        assert not h1["has_dispatch_apply_in_file"], (
            "CLAIM WEAKENED: the standalone file DOES dispatch the optimiser apply "
            "— not a no-op. Source:\n"
            f"{h1['generated_source']}"
        )
        assert not h1["version_col_logic_in_body"], (
            "CLAIM WEAKENED: the standalone body references __optimiser_version__ "
            "in executable logic — it may add the column after all. Body:\n"
            f"{h1['optimiser_apply_body']}"
        )

        assert h2["is_passthrough"] is False, (
            "CLAIM REFUTED: executor _build_node_fn returned _passthrough_fn for a "
            "CONFIGURED optimiserApply (sourceType='run', run_id='abc'); the "
            "executor would be a no-op too, so there is no asymmetry. Got: "
            f"{h2}"
        )
        assert h2["fn_qualname"].endswith("optimiser_apply_fn") or "optimiser_apply" in h2[
            "fn_qualname"
        ], (
            "Executor returned a non-passthrough fn but not the expected "
            f"optimiser_apply_fn closure: {h2['fn_qualname']}"
        )

        # Contrasts: prove the gap is SPECIFIC.
        assert c1["body_calls_score_from_config"], (
            "Contrast broken: modelScore standalone body should call "
            f"score_from_config but did not:\n{c1['model_score_body']}"
        )
        assert c2["unconfigured_executor_is_passthrough"] is True, (
            "Contrast broken: an UNCONFIGURED optimiserApply should build "
            "_passthrough_fn on the executor side (no asymmetry there)."
        )

        print("RESULT: CLAIM REPRODUCED — finding is REAL.")
        print("  Standalone saved-file optimiserApply body = `return src` (no-op):")
        print("    no artifact load, no _dispatch_apply, no version-column logic.")
        print("  Executor _build_node_fn for the SAME config = optimiser_apply_fn")
        print("    (loads artifact, adds __optimiser_version__ via _dispatch_apply).")
        print("  => Identical saved graph prices differently standalone vs canvas.")
        print("  Contrast: modelScore body calls score_from_config (gap closed); an")
        print("  unconfigured optimiserApply is passthrough on BOTH sides.")


if __name__ == "__main__":
    main()
