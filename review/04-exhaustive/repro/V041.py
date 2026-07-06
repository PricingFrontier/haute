"""Isolated reproduction for V041.

Claim: a direct main-file edge between an OUTPUT child of one submodel and an
INPUT child of another submodel (``pipeline.connect("a_out", "b_in")`` where
``a_out`` is a child of submodel A and ``b_in`` is a child of submodel B) loses
its boundary handle during ``merge_submodels``.

``rewire_edges`` is applied once per submodel. The source-side pass (for the
submodel owning ``a_out``) and the target-side pass (for the submodel owning
``b_in``) each construct a FRESH ``GraphEdge`` and never copy the handle set by
the other pass. So whichever pass runs second drops the handle the first pass
installed, yielding ``submodel__A -> submodel__B`` with exactly ONE of
{sourceHandle, targetHandle} set and the other ``None``.

Two downstream failures, neither caught at parse time:
  (a) flatten=True: ``flatten_graph`` only rewrites the side whose handle is
      truthy; the un-rewritten side still references a submodel node, so the
      edge is dropped (line 77 ``continue``) and the a_out->b_in data
      dependency vanishes — the flattened graph has ZERO edges.
  (b) flatten=False: ``graph_to_code_multi`` requires every edge whose source
      is a submodel placeholder to carry an ``out__<child>`` sourceHandle and
      raises ParseError otherwise — the round-trip save throws.

ISOLATION: everything is built in memory. ``merge_submodels`` /
``flatten_graph`` / ``graph_to_code_multi`` are pure functions over
``PipelineGraph`` — no disk I/O, no project root, no rating/src/tests files.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

# Make the in-repo source importable without touching project data files.
_REPO_SRC = Path(__file__).resolve().parents[3] / "src"
sys.path.insert(0, str(_REPO_SRC))

from haute._parser_submodels import merge_submodels  # noqa: E402
from haute.codegen import graph_to_code_multi  # noqa: E402
from haute.errors import ParseError  # noqa: E402
from haute._types import GraphNode, NodeData, NodeType, PipelineGraph  # noqa: E402


def _child(node_id: str) -> GraphNode:
    """A trivial polars child node living inside a submodel."""
    return GraphNode(
        id=node_id,
        data=NodeData(label=node_id, nodeType=NodeType.POLARS),
    )


def _build_inputs():
    """Two single-child submodels + a parent edge crossing both boundaries.

    Parent graph has NO main-file nodes (the only nodes are the submodel
    children), and a single explicit connect a_out -> b_in. This mirrors
    ``pipeline.connect("a_out", "b_in")`` in the main file, which the parser
    passes verbatim as ``parent_edges``. ``_build_edges`` would have dropped it
    (neither endpoint is a main-file node), so it is reconstructed inside
    ``merge_submodels`` as a cross-boundary edge.
    """
    parent_graph = PipelineGraph(
        nodes=[],
        edges=[],
        pipeline_name="main",
    )
    submodel_graphs = {
        "A": PipelineGraph(nodes=[_child("a_out")], edges=[], pipeline_name="A"),
        "B": PipelineGraph(nodes=[_child("b_in")], edges=[], pipeline_name="B"),
    }
    submodel_files = {"A": "modules/a.py", "B": "modules/b.py"}
    parent_edges = [("a_out", "b_in")]
    return parent_graph, submodel_graphs, submodel_files, parent_edges


def main() -> int:
    failures: list[str] = []

    # ---- flatten=False (hierarchical / GUI) --------------------------------
    pg, smg, smf, pe = _build_inputs()
    hierarchical = merge_submodels(pg, smg, smf, pe, flatten=False)

    boundary_edges = [
        e
        for e in hierarchical.edges
        if e.source == "submodel__A" or e.target == "submodel__B"
    ]
    print("--- flatten=False hierarchical edges ---")
    for e in hierarchical.edges:
        print(f"  {e.source} -> {e.target}  sh={e.sourceHandle!r} th={e.targetHandle!r}")

    # Port classification correctness (the finder asserted these are right).
    a_out_ports = hierarchical.submodels["A"]["outputPorts"]
    b_in_ports = hierarchical.submodels["B"]["inputPorts"]
    print(f"  submodels['A'].outputPorts = {a_out_ports}")
    print(f"  submodels['B'].inputPorts  = {b_in_ports}")

    if len(boundary_edges) != 1:
        failures.append(
            f"flatten=False: expected exactly 1 boundary edge, got {len(boundary_edges)}: "
            f"{[(e.source, e.target, e.sourceHandle, e.targetHandle) for e in boundary_edges]}"
        )
    else:
        be = boundary_edges[0]
        # The defining symptom: the reconnected edge spans BOTH placeholders
        # (submodel__A -> submodel__B) and exactly one handle was dropped.
        spans_both = be.source == "submodel__A" and be.target == "submodel__B"
        one_handle_missing = (be.sourceHandle is None) != (be.targetHandle is None)
        if not spans_both:
            failures.append(
                f"flatten=False: boundary edge does not span both placeholders: "
                f"{be.source} -> {be.target} (sh={be.sourceHandle!r} th={be.targetHandle!r}); "
                f"expected submodel__A -> submodel__B — bug mechanism absent."
            )
        elif not one_handle_missing:
            failures.append(
                f"flatten=False: both handles present (sh={be.sourceHandle!r} "
                f"th={be.targetHandle!r}); the handle was NOT dropped — claim refuted."
            )
        else:
            dropped = "sourceHandle" if be.sourceHandle is None else "targetHandle"
            print(
                f"  REPRODUCED (a): corrupt boundary edge {be.source} -> {be.target} "
                f"with {dropped}=None (sh={be.sourceHandle!r} th={be.targetHandle!r})"
            )

    # ---- flatten=True (execution) ------------------------------------------
    pg2, smg2, smf2, pe2 = _build_inputs()
    flat = merge_submodels(pg2, smg2, smf2, pe2, flatten=True)
    flat_ids = sorted(n.id for n in flat.nodes)
    print("--- flatten=True executable graph ---")
    print(f"  nodes = {flat_ids}")
    print(f"  edge_count = {len(flat.edges)}")
    for e in flat.edges:
        print(f"    {e.source} -> {e.target}  sh={e.sourceHandle!r} th={e.targetHandle!r}")

    has_dependency = any(
        e.source == "a_out" and e.target == "b_in" for e in flat.edges
    )
    children_present = "a_out" in flat_ids and "b_in" in flat_ids
    if not children_present:
        failures.append(
            f"flatten=True: expected both child nodes present, got {flat_ids}"
        )
    elif has_dependency:
        failures.append(
            "flatten=True: the a_out -> b_in dependency SURVIVED flattening; "
            "the edge was NOT silently dropped — claim refuted."
        )
    else:
        print(
            "  REPRODUCED (a/exec): a_out and b_in present but the a_out -> b_in "
            "data dependency was SILENTLY DROPPED (edge_count for that pair = 0)."
        )

    # ---- flatten=False round-trip codegen (part b) -------------------------
    print("--- flatten=False round-trip: graph_to_code_multi(hierarchical) ---")
    codegen_error: BaseException | None = None
    try:
        graph_to_code_multi(hierarchical, pipeline_name="main")
    except BaseException as exc:  # noqa: BLE001 - characterising the failure
        codegen_error = exc
    if isinstance(codegen_error, ParseError):
        msg = str(codegen_error)
        print(f"  REPRODUCED (b): graph_to_code_multi raised ParseError: {msg}")
        if "sourceHandle" not in msg and "targetHandle" not in msg:
            failures.append(
                f"part(b): ParseError raised but not about a missing boundary handle: {msg!r}"
            )
    elif codegen_error is None:
        # Not necessarily a refutation of the whole finding (part a stands),
        # but record that the round-trip did NOT throw as predicted.
        print("  NOTE: graph_to_code_multi did NOT raise — part (b) prediction not met.")
    else:
        print(
            f"  NOTE: graph_to_code_multi raised {type(codegen_error).__name__}: "
            f"{codegen_error}"
        )

    print()
    if failures:
        print("REPRO RESULT: claim NOT fully reproduced as predicted")
        for f in failures:
            print(f"  FAIL: {f}")
        return 1

    print("REPRO RESULT: BUG REPRODUCED — a cross-submodel-child edge loses one")
    print("boundary handle in merge_submodels; flatten=True drops the dependency")
    print("entirely (0 edges) and flatten=False round-trip codegen raises ParseError.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:  # pragma: no cover - surface unexpected harness errors
        traceback.print_exc()
        raise SystemExit(2)
