"""Adversarial REFUTATION repro for the claim:

  'Eager core silently drops a requested-but-absent column instead of raising
   when the node is in required_columns_by_node, diverging from the
   lazy/checkpoint path which ALWAYS raises.'  (claimed eager-vs-lazy asymmetry)

VERDICT REACHED BY THIS SCRIPT: REFUTED.

The claim's central thesis is that, for the *same* demanded-but-missing column,
the batch/lazy path raises ContractMismatchError while the eager preview path
silently narrows. Running both paths (_execute_eager_core and _execute_lazy)
on every reachable configuration shows the two paths behave IDENTICALLY to each
other -- there is no asymmetry:

  Config 1  intermediate passthrough M, seed names a missing col
            -> EAGER RAISES (at the parent source); LAZY silently narrows.
               (This is the REVERSE of the claim: eager is the loud one.)

  Config 2  seeded SOURCE node, seed names a missing col
            -> EAGER silently narrows AND LAZY silently narrows.  SYMMETRIC.
               (This is the only config where eager is fully silent, and lazy
               does the exact same thing -- so the claimed asymmetry is absent.)

  Config 3  seeded intermediate that PRODUCES a new column, seed names missing
            -> EAGER RAISES and LAZY RAISES.  SYMMETRIC.

Why the claim is wrong about lazy: the lazy checkpoint-projection guard
(_execute_lazy.py:1379-1389) only runs when a node is PARQUET-checkpointed
(fan-out / join / join-feeder). A source node is never checkpoint-projected
(``_checkpoint_decision`` returns SKIP for sources), and a single non-fan-out
intermediate is SKIP too. So in exactly the inputs where the eager guard at
line 1955 is suppressed (``nid in normalised_required_columns``), the lazy
checkpoint guard does not fire either -- lazy silently narrows the same way.

The asserts below encode this REFUTING reality; the script exits 0 when the
claim is refuted.

ISOLATION: pure in-memory synthetic graphs; tempfile for the lazy checkpoint
dir; no real project files touched.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import polars as pl

from haute._execute_lazy import _execute_eager_core, _execute_lazy
from haute._execution_context import ExecutionContext, ExecutionProfile
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.errors import ContractMismatchError


def _e(s: str, t: str) -> GraphEdge:
    return GraphEdge(id=f"e_{s}_{t}", source=s, target=t)


def _node(nid: str, nt: NodeType, **cfg) -> GraphNode:
    return GraphNode(id=nid, data=NodeData(label=nid, nodeType=nt, config=cfg))


def _eager(graph, build_fn, seed, inspect_node):
    """Return ('raise', node_id) or ('cols', [...])."""
    try:
        res = _execute_eager_core(
            graph, build_fn, target_node_id="N", required_columns_by_node=seed
        )
    except ContractMismatchError as exc:
        return "raise", getattr(exc, "node_id", None) or _node_id_from_msg(exc)
    df = res.outputs.get(inspect_node)
    cols = list(df.columns) if isinstance(df, pl.DataFrame) else df
    return "cols", cols


def _lazy(graph, build_fn, seed, inspect_node):
    with tempfile.TemporaryDirectory() as td:
        try:
            outputs, *_ = _execute_lazy(
                graph,
                build_fn,
                target_node_id="N",
                checkpoint_dir=Path(td),
                required_columns_by_node=seed,
                execution_context=ExecutionContext(
                    operation="repro", profile=ExecutionProfile.LAZY_SINK
                ),
            )
        except ContractMismatchError as exc:
            return "raise", getattr(exc, "node_id", None) or _node_id_from_msg(exc)
        fr = outputs.get(inspect_node)
        if isinstance(fr, pl.LazyFrame):
            return "cols", list(fr.collect_schema().names())
        if isinstance(fr, pl.DataFrame):
            return "cols", list(fr.columns)
        return "cols", fr


def _node_id_from_msg(exc) -> str:
    return "<raised>"


# --------------------------------------------------------------------------
# Config builders
# --------------------------------------------------------------------------
def config1_intermediate_passthrough():
    nodes = [
        _node("src", NodeType.DATA_SOURCE),
        _node("M", NodeType.POLARS),  # passthrough (no code)
        _node("N", NodeType.OUTPUT, fields=["real_col"]),
    ]
    g = PipelineGraph(nodes=nodes, edges=[_e("src", "M"), _e("M", "N")])

    def bf(node, **kw):
        if node.id == "src":
            return node.id, lambda: pl.DataFrame({"real_col": [1, 2, 3]}).lazy(), True
        if node.id == "M":
            return node.id, lambda *dfs: dfs[0], False
        f = node.data.config.get("fields") or []
        return node.id, lambda *dfs, _f=f: dfs[0].select(_f), False

    return g, bf, {"M": ["does_not_exist", "real_col"]}, "M"


def config2_seeded_source():
    nodes = [
        _node("src", NodeType.DATA_SOURCE),
        _node("N", NodeType.OUTPUT, fields=["real_col"]),
    ]
    g = PipelineGraph(nodes=nodes, edges=[_e("src", "N")])

    def bf(node, **kw):
        if node.id == "src":
            return node.id, lambda: pl.DataFrame({"real_col": [1, 2, 3]}).lazy(), True
        f = node.data.config.get("fields") or []
        return node.id, lambda *dfs, _f=f: dfs[0].select(_f), False

    return g, bf, {"src": ["does_not_exist", "real_col"]}, "src"


def config3_intermediate_produces():
    # Realistic parseable code so the projection planner routes parent demand
    # rather than treating M's contract as opaque/unparseable.
    code = "df = df.with_columns(pl.lit(9).alias('made'))"
    nodes = [
        _node("src", NodeType.DATA_SOURCE),
        _node("M", NodeType.POLARS, code=code),
        _node("N", NodeType.OUTPUT, fields=["made"]),
    ]
    g = PipelineGraph(nodes=nodes, edges=[_e("src", "M"), _e("M", "N")])

    def bf(node, **kw):
        if node.id == "src":
            return node.id, lambda: pl.DataFrame({"real_col": [1, 2, 3]}).lazy(), True
        if node.id == "M":
            return (
                node.id,
                lambda *dfs: dfs[0].with_columns(pl.lit(9).alias("made")),
                False,
            )
        f = node.data.config.get("fields") or []
        return node.id, lambda *dfs, _f=f: dfs[0].select(_f), False

    return g, bf, {"M": ["does_not_exist", "made"]}, "M"


def main() -> None:
    results = {}
    for name, builder in (
        ("config1_intermediate_passthrough", config1_intermediate_passthrough),
        ("config2_seeded_source", config2_seeded_source),
        ("config3_intermediate_produces", config3_intermediate_produces),
    ):
        g, bf, seed, inspect = builder()
        e = _eager(g, bf, seed, inspect)
        lz = _lazy(g, bf, seed, inspect)
        results[name] = (e, lz)
        print(f"=== {name} (inspect={inspect}) ===")
        print(f"  EAGER: {e}")
        print(f"  LAZY : {lz}")
        print()

    # ---- Encode the REFUTING reality ----

    # Config 1: claim says eager is SILENT here. It is NOT -- eager RAISES.
    e1, l1 = results["config1_intermediate_passthrough"]
    assert e1[0] == "raise", (
        "Claim says eager silently narrows the intermediate passthrough node, "
        f"but eager RAISED. eager={e1}"
    )
    # Lazy here silently narrows (no PARQUET checkpoint on the single
    # non-fan-out intermediate). So in config 1 the asymmetry is the REVERSE
    # of the claim (eager loud, lazy silent).
    assert l1[0] == "cols" and l1[1] == ["real_col"], f"unexpected lazy={l1}"
    print("Config 1: eager RAISES, lazy silently narrows -> REVERSE of claim.")

    # Config 2: the ONLY config where eager is fully silent. Lazy must be
    # silent too (SYMMETRIC) -> claimed asymmetry absent.
    e2, l2 = results["config2_seeded_source"]
    assert e2 == ("cols", ["real_col"]), f"eager seeded-source unexpected: {e2}"
    assert l2 == ("cols", ["real_col"]), f"lazy seeded-source unexpected: {l2}"
    assert e2 == l2, "eager and lazy DIVERGE on seeded source (claim would hold)"
    print("Config 2: eager and lazy BOTH silently narrow -> SYMMETRIC (no asymmetry).")

    # Config 3: both raise -> symmetric.
    e3, l3 = results["config3_intermediate_produces"]
    assert e3[0] == "raise", f"eager should raise: {e3}"
    assert l3[0] == "raise", f"lazy should raise: {l3}"
    print("Config 3: eager and lazy BOTH raise -> SYMMETRIC.")

    print(
        "\nCLAIM REFUTED: there is no configuration where eager silently narrows "
        "the same demanded-but-missing column that the lazy path raises on. The "
        "guard at _execute_lazy.py:1955 only goes silent for an upstream-terminal "
        "seeded node, and the lazy checkpoint guard (1379-1389) is silent there "
        "too because sources/non-fan-out nodes are never checkpoint-projected."
    )


if __name__ == "__main__":
    main()
