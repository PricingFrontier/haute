"""Reproduction for V043.

Claim: ``rewire_edges`` (src/haute/_submodel_graph.py:116-135) drops the
*external* endpoint's port handle on cross-boundary edges.

- External -> internal branch (lines 117-125) sets targetHandle="in__<child>"
  but never forwards e.sourceHandle. So an external MULTI-OUTPUT source node
  feeding a submodel child loses its output-port selector.
- Internal -> external branch (lines 127-135) sets sourceHandle="out__<child>"
  but never forwards e.targetHandle, dropping the external target's input-port
  role.

Downstream consequence: after flatten-for-execution (_flatten.py) the edge
from the multi-port source carries sourceHandle=None, which
_pick_source_frame (_execute_lazy.py:103-108) rejects with
ValueError "Edge from multi-port node X has no sourceHandle".

This script ISOLATES the bug: it builds tiny synthetic GraphEdge objects and
a minimal hierarchical PipelineGraph in memory. No project files are read or
written. It ASSERTS on the specific wrong VALUE (the dropped handle), not
merely that "something raised".
"""

from __future__ import annotations

from haute._flatten import flatten_graph
from haute._submodel_graph import rewire_edges
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph

failures: list[str] = []


# ---------------------------------------------------------------------------
# Part 1 — root cause: rewire_edges drops the EXTERNAL side handle (both branches)
# ---------------------------------------------------------------------------

# (A) External -> internal: external multi-output source "ext" picks port
#     "frequency" via sourceHandle. The rewired edge must keep that selector.
inbound = GraphEdge(
    id="e_ext_child",
    source="ext",
    target="child",
    sourceHandle="frequency",  # external source's chosen output port
)
[rewired_in] = rewire_edges([inbound], "submodel__grp", {"child"})

print("=== Part 1A: External -> internal ===")
print(f"  input  sourceHandle = {inbound.sourceHandle!r}")
print(f"  result sourceHandle = {rewired_in.sourceHandle!r}  (expected 'frequency')")
print(f"  result targetHandle = {rewired_in.targetHandle!r}  (expected 'in__child')")
if rewired_in.targetHandle != "in__child":
    failures.append(f"1A targetHandle wrong: {rewired_in.targetHandle!r}")
if rewired_in.sourceHandle != "frequency":
    failures.append(
        "1A DROPPED external sourceHandle: expected 'frequency', "
        f"got {rewired_in.sourceHandle!r}"
    )

# (B) Internal -> external: external target "ext" expects input via a role
#     handle (targetHandle). The rewired edge must keep that role.
outbound = GraphEdge(
    id="e_child_ext",
    source="child",
    target="ext",
    targetHandle="base",  # external target's chosen input role
)
[rewired_out] = rewire_edges([outbound], "submodel__grp", {"child"})

print("=== Part 1B: Internal -> external ===")
print(f"  input  targetHandle = {outbound.targetHandle!r}")
print(f"  result sourceHandle = {rewired_out.sourceHandle!r}  (expected 'out__child')")
print(f"  result targetHandle = {rewired_out.targetHandle!r}  (expected 'base')")
if rewired_out.sourceHandle != "out__child":
    failures.append(f"1B sourceHandle wrong: {rewired_out.sourceHandle!r}")
if rewired_out.targetHandle != "base":
    failures.append(
        "1B DROPPED external targetHandle: expected 'base', "
        f"got {rewired_out.targetHandle!r}"
    )


# ---------------------------------------------------------------------------
# Part 2 — end-to-end consequence: flatten yields sourceHandle=None on the
#          multi-port source edge (the value _execute_lazy rejects).
# ---------------------------------------------------------------------------

def _node(node_id: str, node_type: NodeType) -> GraphNode:
    return GraphNode(
        id=node_id,
        type=node_type,
        position={"x": 0, "y": 0},
        data=NodeData(label=node_id, nodeType=node_type),
    )


# Parent graph after a "create submodel" op grouping child node "child":
#   - "ext" is an external multi-output source (e.g. apiInput emitting
#     frequency/severity) feeding "child" inside the submodel via port
#     "frequency".
# rewire_edges has already replaced the cross edge with one to the submodel
# placeholder, KEEPING (per the real code) only targetHandle="in__child" and
# DROPPING sourceHandle="frequency".
boundary_edge = GraphEdge(
    id="e_ext_submodel__grp__child",
    source="ext",
    target="submodel__grp",
    targetHandle="in__child",
    # sourceHandle is None here — exactly what rewire_edges produced.
)

hierarchical = PipelineGraph(
    nodes=[
        _node("ext", NodeType.API_INPUT),
        GraphNode(
            id="submodel__grp",
            type=NodeType.SUBMODEL,
            position={"x": 0, "y": 0},
            data=NodeData(
                label="grp",
                nodeType=NodeType.SUBMODEL,
                config={
                    "file": "modules/grp.py",
                    "childNodeIds": ["child"],
                    "inputPorts": ["child"],
                    "outputPorts": [],
                },
            ),
        ),
    ],
    edges=[boundary_edge],
    pipeline_name="parent",
    submodels={
        "grp": {
            "file": "modules/grp.py",
            "childNodeIds": ["child"],
            "inputPorts": ["child"],
            "outputPorts": [],
            "graph": {
                "nodes": [_node("child", NodeType.POLARS).model_dump()],
                "edges": [],
                "submodel_name": "grp",
                "submodel_description": "",
                "source_file": "modules/grp.py",
            },
        }
    },
)

flat = flatten_graph(hierarchical)
# Find the rewired ext -> child edge in the flattened graph.
ext_child = [e for e in flat.edges if e.source == "ext" and e.target == "child"]
print("=== Part 2: flatten-for-execution ===")
print(f"  ext->child edges: {[(e.source, e.target, e.sourceHandle) for e in ext_child]}")

assert len(ext_child) == 1, f"expected exactly one ext->child edge, got {ext_child}"
flat_edge = ext_child[0]
print(
    f"  flattened sourceHandle = {flat_edge.sourceHandle!r}  "
    "(should be 'frequency'; is None because rewire dropped it)"
)
if flat_edge.sourceHandle is not None:
    failures.append(
        "2 flattened edge unexpectedly retained a sourceHandle: "
        f"{flat_edge.sourceHandle!r}"
    )


# ---------------------------------------------------------------------------
# Part 3 — that None is exactly the value _pick_source_frame rejects.
# ---------------------------------------------------------------------------

from haute._execute_lazy import _pick_source_frame  # noqa: E402

multi_port_output = {"frequency": object(), "severity": object()}
print("=== Part 3: _pick_source_frame on the flattened edge ===")
raised: Exception | None = None
try:
    _pick_source_frame(multi_port_output, flat_edge)
except ValueError as exc:
    raised = exc
    print(f"  raised ValueError: {exc}")

if raised is None:
    failures.append("3 expected ValueError from _pick_source_frame, none raised")
elif "has no sourceHandle" not in str(raised):
    failures.append(f"3 wrong error message: {raised}")


# ---------------------------------------------------------------------------
print()
if failures:
    print("REPRODUCED — bug confirmed. Wrong values observed:")
    for f in failures:
        print(f"  - {f}")
    raise SystemExit(1)
print("NOT reproduced — all handles preserved (claim would be refuted).")
