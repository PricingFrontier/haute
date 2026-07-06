"""Reproduction for V035.

Claim: the graph fingerprint serializations in src/haute/_cache.py are NOT
injective. Two distinct, in-memory inputs collide onto one digest because:

  (A) _graph_base_fingerprint builds each node line as the unframed
      f"{n.id}|{n.data.nodeType}|{canonical_json(config)}" and joins all node
      lines with "\n". A single node whose *id* embeds a "|...|...\n..."
      sequence reconstructs the exact two-line text that two *different* nodes
      would produce -> two structurally different graphs hash identically.

  (B) graph_fingerprint joins [*extra_keys, *context_parts, base] with "\n",
      so one extra_key containing a newline reproduces the join of two
      separate extra_keys -> different execution-parameter tuples collide.

ISOLATION: everything is built in memory (small synthetic PipelineGraph
objects). No project root, no disk I/O, no reading/writing rating/, src/,
tests/, or any real project file.

The script ASSERTS on the specific wrong behaviour: the two distinct inputs
produce EQUAL fingerprints (a collision), and -- as a control -- it confirms
that the encoder *is* injective for the pipe-only (no-newline) case, matching
the finding's nuance.
"""

from __future__ import annotations

from haute import _cache
from haute._cache import _graph_base_fingerprint, graph_fingerprint
from haute._types import GraphNode, NodeData, NodeType, PipelineGraph


def _node(node_id: str) -> GraphNode:
    # POLARS nodeType, empty config -> node line is f"{id}|polars|{}" where
    # canonical_json({}) == "{}".
    return GraphNode(id=node_id, data=NodeData(nodeType=NodeType.POLARS, config={}))


def main() -> None:
    # The value of nodeType as it appears in the f-string. NodeType is a str
    # enum; the f-string interpolates str(n.data.nodeType). Capture it exactly
    # so the crafted id matches byte-for-byte regardless of the enum's repr.
    node_type_text = f"{NodeType.POLARS}"
    empty_config_json = _cache.canonical_json({})  # "{}"
    line_suffix = f"|{node_type_text}|{empty_config_json}"  # "|polars|{}"

    # ----- (A) base-fingerprint node-line collision -----------------------
    # Two-node graph: ids "a" and "b" -> after sort + "\n".join the digest
    # material is:  "a|polars|{}\nb|polars|{}"
    two_nodes = PipelineGraph(nodes=[_node("a"), _node("b")], edges=[])

    # One-node graph whose single id is crafted to BE that whole two-line text
    # minus its own trailing suffix:  id = "a|polars|{}\nb"
    crafted_id = f"a{line_suffix}\nb"  # "a|polars|{}\nb"
    one_node = PipelineGraph(nodes=[_node(crafted_id)], edges=[])

    fp_two = _graph_base_fingerprint(two_nodes)
    fp_one = _graph_base_fingerprint(one_node)

    print(f"[A] two-node base fp = {fp_two}")
    print(f"[A] one-node base fp = {fp_one}")
    print(f"[A] crafted node id  = {crafted_id!r}")

    assert fp_two == fp_one, (
        "EXPECTED COLLISION NOT OBSERVED: a structurally distinct 1-node graph "
        "and 2-node graph produced different base fingerprints; the node-line "
        "encoding may have been framed/escaped."
    )

    # Sanity: these graphs really are structurally different (1 node vs 2).
    assert len(one_node.nodes) == 1
    assert len(two_nodes.nodes) == 2
    assert {n.id for n in one_node.nodes} != {n.id for n in two_nodes.nodes}

    # ----- (A control) pipe-only (no newline) must NOT collide ------------
    # The finding explicitly verified that a pipe *within one line* does not
    # collide because the config-JSON boundary differs. Confirm the encoder is
    # injective here, isolating the defect to the newline cross-line case.
    pipe_only_id = f"a{line_suffix}b"  # "a|polars|{}b"  -- no newline
    pipe_only_graph = PipelineGraph(nodes=[_node(pipe_only_id)], edges=[])
    fp_pipe_only = _graph_base_fingerprint(pipe_only_graph)
    print(f"[A-control] pipe-only id fp = {fp_pipe_only}")
    assert fp_pipe_only != fp_two, (
        "Control failed: a pipe-only (no-newline) id collided with the "
        "two-node graph, which would mean the defect is broader than claimed."
    )

    # ----- (B) graph_fingerprint extra-keys join collision ----------------
    # Use a graph with no preamble so context_fingerprint is empty and the
    # combined material is purely "\n".join([*extra_keys, base]).
    g = PipelineGraph(nodes=[_node("n0")], edges=[])

    # Two distinct extra-key tuples that share the same "\n".join:
    #   ("k1\nk2",)      -> "k1\nk2" + "\n" + base
    #   ("k1", "k2")     -> "k1" + "\n" + "k2" + "\n" + base
    fp_one_key = graph_fingerprint(g, "k1\nk2")
    fp_two_keys = graph_fingerprint(g, "k1", "k2")

    print(f"[B] graph_fingerprint(g, 'k1\\nk2') = {fp_one_key}")
    print(f"[B] graph_fingerprint(g, 'k1','k2') = {fp_two_keys}")

    assert fp_one_key == fp_two_keys, (
        "EXPECTED COLLISION NOT OBSERVED: a single newline-bearing extra key "
        "and a two-key tuple produced different fingerprints; the extra-key "
        "join may have been framed/escaped."
    )

    # Control: a distinct single key must NOT collide with the two-key form.
    fp_other_key = graph_fingerprint(g, "k1XXk2")
    print(f"[B-control] graph_fingerprint(g, 'k1XXk2') = {fp_other_key}")
    assert fp_other_key != fp_two_keys, (
        "Control failed: an unrelated single key matched the two-key digest."
    )

    print()
    print("V035 REPRODUCED: non-injective node-line and extra-key joins "
          "collide distinct inputs onto one fingerprint.")


if __name__ == "__main__":
    main()
