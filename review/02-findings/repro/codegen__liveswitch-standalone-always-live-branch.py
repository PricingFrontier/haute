"""Adversarial repro: liveSwitch standalone-generated body hard-wires the
'live' input, diverging from the in-canvas executor's scenario-aware branch
selection under a batch run (source='batch').

Claim under test (liveswitch-standalone-always-live-branch):
  - Codegen (_gen_live_switch / _LIVE_SWITCH template) emits a body whose
    single statement is `return <live-mapped input>` chosen at SAVE time.
  - The executor (_build_live_switch) selects the input whose scenario ==
    ctx.source at RUNTIME.
  - Pipeline.run() forces _scenario_ctx='batch' and invokes the *literal*
    generated body (Node.__call__ calls self.fn), with NO NODE_REGISTRY
    dispatch. So a standalone batch run of the saved file routes the LIVE
    frame while the in-canvas batch executor routes the BATCH frame.

This repro is fully in-memory (no disk I/O, no real project files). It asserts
on the specific WRONG VALUE: which input frame flows downstream for source=
'batch'.
"""

import polars as pl

from haute._builders import _build_node_fn
from haute._codegen_builders import _gen_live_switch
from haute._types import GraphNode, NodeData, NodeType


def _make_live_switch_node() -> GraphNode:
    return GraphNode(
        id="ls1",
        data=NodeData(
            label="Live Switch",
            nodeType=NodeType.LIVE_SWITCH,
            config={
                "live_switch": True,
                "input_scenario_map": {
                    "live_src": "live",
                    "batch_src": "batch",
                },
                "inputs": ["live_src", "batch_src"],
            },
        ),
    )


def main() -> None:
    node = _make_live_switch_node()
    source_names = ["live_src", "batch_src"]

    # Distinguishable frames: a row-marker column tells us which branch flowed.
    LIVE = pl.LazyFrame({"branch": ["LIVE"], "x": [1]})
    BATCH = pl.LazyFrame({"branch": ["BATCH"], "x": [2]})

    # ------------------------------------------------------------------
    # 1) CODEGEN body (what gets written to the standalone saved file).
    # ------------------------------------------------------------------
    gen_src = _gen_live_switch(node, source_names)
    print("---- generated standalone body ----")
    print(gen_src)

    # The body's return statement, normalised.
    return_lines = [
        ln.strip() for ln in gen_src.splitlines() if ln.strip().startswith("return ")
    ]
    assert len(return_lines) == 1, f"expected exactly one return, got {return_lines!r}"
    gen_return = return_lines[0]
    # The generated body is statically wired to the 'live'-mapped input.
    assert gen_return == "return live_src", (
        f"expected generated body 'return live_src' (live-mapped, save-time), "
        f"got {gen_return!r}"
    )
    # And it is NOT batch_src, regardless of any runtime scenario.
    assert gen_return != "return batch_src"
    generated_branch = "LIVE"  # `return live_src` -> the LIVE frame flows.

    # ------------------------------------------------------------------
    # 2) EXECUTOR fn under a BATCH run (source='batch'): the in-canvas path.
    # ------------------------------------------------------------------
    _name, exec_fn, _is_src = _build_node_fn(
        node,
        source_names=source_names,
        source="batch",
    )
    # Executor wrapper accepts kwargs (executor form) and positional (test form).
    exec_out = exec_fn(live_src=LIVE, batch_src=BATCH).collect()
    executor_branch = exec_out["branch"][0]
    print(f"executor (source='batch') routed branch = {executor_branch!r}")
    assert executor_branch == "BATCH", (
        f"executor should route the BATCH frame under source='batch', "
        f"got {executor_branch!r}"
    )

    # ------------------------------------------------------------------
    # 3) Prove the generated body, when actually executed under a batch
    #    scenario context (as Pipeline.run does), STILL returns the LIVE
    #    frame -- i.e. the literal body ignores the scenario entirely.
    # ------------------------------------------------------------------
    from haute._model_scorer import _scenario_ctx
    from haute.pipeline import Pipeline

    pipe = Pipeline("repro")
    ns: dict = {"pipeline": pipe, "pl": pl}
    exec(compile(gen_src, "<generated_live_switch>", "exec"), ns)
    # The decorated function was registered on the pipeline as a node under
    # its (sanitized) function name. Grab the single registered node.
    assert len(pipe._node_map) == 1, f"unexpected nodes: {list(pipe._node_map)!r}"
    ls_node = next(iter(pipe._node_map.values()))

    _token = _scenario_ctx.set("batch")  # exactly what Pipeline.run() does
    try:
        # Pipeline.run() invokes the literal body via Node.__call__(*input_dfs)
        # in declared-source (topo) order: [live_src, batch_src].
        standalone_out = ls_node(LIVE.collect(), BATCH.collect())
    finally:
        _scenario_ctx.reset(_token)
    standalone_branch = standalone_out["branch"][0]
    print(f"standalone Pipeline.run body (_scenario_ctx='batch') routed branch = "
          f"{standalone_branch!r}")
    assert standalone_branch == "LIVE", (
        f"standalone generated body should (buggily) route LIVE even under "
        f"batch context, got {standalone_branch!r}"
    )

    # ------------------------------------------------------------------
    # 4) The divergence: same node, same batch run, DIFFERENT frame.
    # ------------------------------------------------------------------
    assert generated_branch == standalone_branch == "LIVE"
    assert executor_branch == "BATCH"
    assert standalone_branch != executor_branch, (
        "EXPECTED DIVERGENCE NOT OBSERVED -- claim would be refuted"
    )

    print()
    print("REPRO CONFIRMED: under a batch run (source/_scenario_ctx='batch'):")
    print(f"  in-canvas executor      -> {executor_branch}  (correct: batch frame)")
    print(f"  standalone saved file   -> {standalone_branch}  (WRONG: live frame)")
    print("Different rows/columns flow downstream with NO error raised.")


if __name__ == "__main__":
    main()
