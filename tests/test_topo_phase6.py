"""Pinning tests for Phase 6 #133 — ``graphlib.TopologicalSorter`` refactor.

These invariants MUST remain green whether ``haute._topo.topo_sort_ids`` is
implemented with the hand-rolled Kahn's algorithm (current) or rewritten on
top of :class:`graphlib.TopologicalSorter` (the planned refactor).

What this file pins
-------------------
1. Topological correctness for every supported graph shape (linear chain,
   diamond, wide fan-out, deep chain, disconnected components, parallel
   edges, unknown-endpoint edges, empty graph, single node).
2. ``CycleError`` behaviour that users see in the GUI — the error must
   name every node participating in the cycle and stay a ``HauteError``
   subclass so callers catching ``HauteError`` do not silently miss cycles
   when the implementation switches.
3. Determinism — re-running the sort on the same inputs produces the
   same output (regardless of the specific tie-break rule).
4. An integration smoke through :func:`haute._execute_lazy._prepare_graph`
   to catch signature drift between the topo function and its real caller.

What this file deliberately does NOT pin
----------------------------------------
- The specific tie-break order when multiple nodes are simultaneously
  ready. The current heap-based impl pops alphabetically; ``graphlib``
  returns insertion order. Both are deterministic; the refactor changes
  which one Haute uses. Existing alphabetical assertions in
  ``tests/test_topo.py`` are the dev's responsibility to update.
"""

from __future__ import annotations

import graphlib

import pytest

from haute._execute_lazy import _prepare_graph
from haute._topo import CycleError, topo_sort_ids
from haute._types import GraphEdge, GraphNode, HauteError, NodeData, PipelineGraph

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _e(src: str, tgt: str, eid: str | None = None) -> GraphEdge:
    """Build a ``GraphEdge`` from source/target ids with a unique edge id."""
    return GraphEdge(id=eid or f"e_{src}_{tgt}", source=src, target=tgt)


def _is_topologically_valid(order: list[str], edges: list[GraphEdge]) -> bool:
    """True iff every edge's source precedes its target in ``order``.

    Edges pointing at unknown nodes (outside ``order``) are ignored, matching
    the current implementation's silent-drop semantics.
    """
    position = {nid: i for i, nid in enumerate(order)}
    for edge in edges:
        if edge.source not in position or edge.target not in position:
            continue
        if position[edge.source] >= position[edge.target]:
            return False
    return True


# ---------------------------------------------------------------------------
# Shape invariants — topological correctness
# ---------------------------------------------------------------------------


class TestTopologicalCorrectness:
    """Pin that every supported graph shape produces a valid topo order."""

    def test_empty_graph_returns_empty_list(self) -> None:
        """No nodes and no edges is trivially sorted to an empty list."""
        assert topo_sort_ids([], []) == []

    def test_single_node_returns_that_node(self) -> None:
        """A graph of one node with no edges returns just that node."""
        assert topo_sort_ids(["only"], []) == ["only"]

    def test_linear_chain_has_unique_order(self) -> None:
        """``a -> b -> c -> d`` has exactly one valid topo order; pin it."""
        edges = [_e("a", "b"), _e("b", "c"), _e("c", "d")]
        assert topo_sort_ids(["a", "b", "c", "d"], edges) == ["a", "b", "c", "d"]

    def test_linear_chain_ignores_input_list_order(self) -> None:
        """Shuffling the node-id input must not change the forced order."""
        edges = [_e("a", "b"), _e("b", "c"), _e("c", "d")]
        assert topo_sort_ids(["d", "a", "c", "b"], edges) == ["a", "b", "c", "d"]

    def test_diamond_anchors_are_pinned(self) -> None:
        """Diamond ``a -> {b, c} -> d``: ``a`` is first, ``d`` is last."""
        edges = [_e("a", "b"), _e("a", "c"), _e("b", "d"), _e("c", "d")]
        order = topo_sort_ids(["a", "b", "c", "d"], edges)

        assert order[0] == "a"
        assert order[-1] == "d"
        assert set(order) == {"a", "b", "c", "d"}
        assert _is_topologically_valid(order, edges)

    def test_disconnected_components_all_returned(self) -> None:
        """Two independent chains: every node appears, each chain ordered."""
        edges = [_e("a", "b"), _e("c", "d")]
        order = topo_sort_ids(["a", "b", "c", "d"], edges)

        assert set(order) == {"a", "b", "c", "d"}
        # Within each component, the edge direction must be respected.
        assert order.index("a") < order.index("b")
        assert order.index("c") < order.index("d")

    def test_parallel_edges_between_same_pair_do_not_break_sort(self) -> None:
        """Two edges ``a -> b`` between the same pair still produce ``[a, b]``."""
        edges = [_e("a", "b", "edge1"), _e("a", "b", "edge2")]
        order = topo_sort_ids(["a", "b"], edges)

        assert order == ["a", "b"]
        assert _is_topologically_valid(order, edges)

    def test_edges_with_unknown_endpoints_are_ignored(self) -> None:
        """Edges whose source or target is not in ``node_ids`` are dropped."""
        edges = [_e("a", "b"), _e("ghost", "a"), _e("b", "phantom")]
        order = topo_sort_ids(["a", "b"], edges)

        assert set(order) == {"a", "b"}
        assert order == ["a", "b"]

    def test_deep_chain_of_60_nodes(self) -> None:
        """A 60-node linear chain must come out in exactly the chain order."""
        ids = [f"n{i:03d}" for i in range(60)]
        edges = [_e(ids[i], ids[i + 1]) for i in range(59)]
        assert topo_sort_ids(ids, edges) == ids

    def test_wide_fan_out_root_is_first(self) -> None:
        """One root fanning out to 25 children: root first, children follow."""
        children = [f"c{i:02d}" for i in range(25)]
        ids = ["root", *children]
        edges = [_e("root", c) for c in children]

        order = topo_sort_ids(ids, edges)

        assert order[0] == "root"
        assert set(order[1:]) == set(children)
        assert _is_topologically_valid(order, edges)

    def test_wide_fan_in_leaf_is_last(self) -> None:
        """25 parents all feeding into one leaf: leaf is last in the order."""
        parents = [f"p{i:02d}" for i in range(25)]
        ids = [*parents, "leaf"]
        edges = [_e(p, "leaf") for p in parents]

        order = topo_sort_ids(ids, edges)

        assert order[-1] == "leaf"
        assert set(order[:-1]) == set(parents)
        assert _is_topologically_valid(order, edges)

    def test_complex_dag_preserves_every_edge_constraint(self) -> None:
        """Non-trivial DAG: every edge ``u -> v`` has ``u`` before ``v``."""
        # a -> b -> d
        #  \-> c -> d -> e
        #       \-> e
        ids = ["a", "b", "c", "d", "e"]
        edges = [
            _e("a", "b"),
            _e("a", "c"),
            _e("b", "d"),
            _e("c", "d"),
            _e("c", "e"),
            _e("d", "e"),
        ]

        order = topo_sort_ids(ids, edges)

        assert set(order) == set(ids)
        assert _is_topologically_valid(order, edges)
        # a has no parents → must be first; e has no children → must be last.
        assert order[0] == "a"
        assert order[-1] == "e"


# ---------------------------------------------------------------------------
# Cycle detection — actionable error messages in the GUI
# ---------------------------------------------------------------------------


class TestCycleErrorInvariants:
    """Pin the user-facing error shape when the graph contains a cycle."""

    def test_self_loop_raises_cycle_error(self) -> None:
        """``a -> a`` raises ``CycleError`` — a self-loop is a one-node cycle."""
        with pytest.raises(CycleError):
            topo_sort_ids(["a"], [_e("a", "a")])

    def test_self_loop_names_the_node(self) -> None:
        """Self-loop error must identify the offending node by id."""
        with pytest.raises(CycleError) as exc_info:
            topo_sort_ids(["a"], [_e("a", "a")])

        assert "a" in exc_info.value.cycle_nodes
        assert "a" in str(exc_info.value)

    def test_two_node_cycle_raises_cycle_error(self) -> None:
        """``a -> b -> a`` raises ``CycleError``."""
        with pytest.raises(CycleError):
            topo_sort_ids(["a", "b"], [_e("a", "b"), _e("b", "a")])

    def test_two_node_cycle_names_both_nodes(self) -> None:
        """Two-node cycle error must name BOTH participating nodes."""
        with pytest.raises(CycleError) as exc_info:
            topo_sort_ids(["a", "b"], [_e("a", "b"), _e("b", "a")])

        # The user must see both node ids so they can find either edge to cut.
        assert "a" in exc_info.value.cycle_nodes
        assert "b" in exc_info.value.cycle_nodes
        message = str(exc_info.value)
        assert "a" in message
        assert "b" in message

    def test_three_node_cycle_names_all_three(self) -> None:
        """``a -> b -> c -> a`` error must name all three nodes."""
        with pytest.raises(CycleError) as exc_info:
            topo_sort_ids(
                ["a", "b", "c"],
                [_e("a", "b"), _e("b", "c"), _e("c", "a")],
            )

        assert set(exc_info.value.cycle_nodes) >= {"a", "b", "c"}
        message = str(exc_info.value)
        for nid in ("a", "b", "c"):
            assert nid in message, f"node {nid!r} missing from error: {message!r}"

    def test_cycle_error_message_is_more_than_a_generic_string(self) -> None:
        """Users need actionable info, not just ``"cycle detected"``.

        The message must contain at least one node id so the GUI can
        surface the offending nodes.  A message with only the literal
        phrase "cycle detected" and no node ids would be a regression.
        """
        with pytest.raises(CycleError) as exc_info:
            topo_sort_ids(["x", "y"], [_e("x", "y"), _e("y", "x")])

        message = str(exc_info.value)
        # At least one of the two node ids must appear in the message.
        assert ("x" in message) or ("y" in message)

    def test_cycle_error_remains_haute_error_subclass(self) -> None:
        """``CycleError`` must inherit from ``HauteError``.

        Callers across the codebase catch ``HauteError`` to convert
        pipeline failures into structured GUI messages. If the refactor
        lost this inheritance (e.g. raised ``graphlib.CycleError`` bare),
        those callers would re-raise as unhandled exceptions.
        """
        with pytest.raises(HauteError):
            topo_sort_ids(["a", "b"], [_e("a", "b"), _e("b", "a")])

    def test_cycle_error_exposes_cycle_nodes_attribute(self) -> None:
        """``CycleError`` instance must expose ``cycle_nodes`` as an iterable.

        The GUI pipeline-error renderer reads ``.cycle_nodes`` directly —
        it is part of the public API of this error type.
        """
        with pytest.raises(CycleError) as exc_info:
            topo_sort_ids(["a", "b"], [_e("a", "b"), _e("b", "a")])

        # ``cycle_nodes`` exists and is iterable.
        cycle_nodes = list(exc_info.value.cycle_nodes)
        assert set(cycle_nodes) == {"a", "b"}

    def test_cycle_in_larger_graph_still_raises(self) -> None:
        """A DAG-with-one-cycle graph still raises — cycle isn't hidden by DAG parts.

        Acyclic prefix ``d -> a`` then cycle ``a -> b -> a``: the cycle
        must still be detected even though ``d`` itself is sortable.
        """
        with pytest.raises(CycleError) as exc_info:
            topo_sort_ids(
                ["a", "b", "d"],
                [_e("d", "a"), _e("a", "b"), _e("b", "a")],
            )

        # The cycle involves a and b; d is on an acyclic tail.
        reported = set(exc_info.value.cycle_nodes)
        assert {"a", "b"} <= reported

    def test_multiple_disjoint_cycles_are_both_reported(self) -> None:
        """Two disjoint cycles ``{a,b}`` and ``{c,d}`` both surface in the error."""
        ids = ["a", "b", "c", "d"]
        edges = [_e("a", "b"), _e("b", "a"), _e("c", "d"), _e("d", "c")]

        with pytest.raises(CycleError) as exc_info:
            topo_sort_ids(ids, edges)

        # All four cycle nodes must be reported so the user knows both
        # cycles exist (not just one).
        assert set(exc_info.value.cycle_nodes) >= {"a", "b", "c", "d"}

    def test_valid_dag_never_raises_cycle_error(self) -> None:
        """A valid DAG must not raise — cycle detection is not overzealous."""
        # No exception expected.
        order = topo_sort_ids(
            ["a", "b", "c", "d"],
            [_e("a", "b"), _e("b", "c"), _e("c", "d")],
        )
        assert order == ["a", "b", "c", "d"]


# ---------------------------------------------------------------------------
# Determinism / stability
# ---------------------------------------------------------------------------


class TestDeterminism:
    """Pin that identical inputs produce identical outputs, run after run."""

    def test_repeated_sort_is_stable_for_forced_chain(self) -> None:
        """Linear chain has one valid order; 10 runs must all agree."""
        edges = [_e("a", "b"), _e("b", "c"), _e("c", "d")]
        first = topo_sort_ids(["a", "b", "c", "d"], edges)

        for _ in range(10):
            assert topo_sort_ids(["a", "b", "c", "d"], edges) == first

    def test_repeated_sort_is_stable_with_ties(self) -> None:
        """When ties exist the *choice* of tie-break may change between
        implementations, but a given implementation must make the SAME
        choice every time — running the same input 10 times always yields
        the same output.
        """
        ids = ["z", "m", "a", "f"]
        edges: list[GraphEdge] = []
        first = topo_sort_ids(ids, edges)

        for _ in range(10):
            assert topo_sort_ids(ids, edges) == first

    def test_repeated_sort_is_stable_for_diamond(self) -> None:
        """Diamond has two valid topo orders; the impl must pick one and stick."""
        edges = [_e("a", "b"), _e("a", "c"), _e("b", "d"), _e("c", "d")]
        first = topo_sort_ids(["a", "b", "c", "d"], edges)

        for _ in range(10):
            assert topo_sort_ids(["a", "b", "c", "d"], edges) == first

    def test_forced_linear_order_is_exact(self) -> None:
        """When the DAG has a unique topo order, the sort MUST produce it.

        Any tie-break rule (alphabetical heap, insertion-order graphlib,
        anything else) must still produce the single valid order for a
        fully-constrained chain.
        """
        ids = [f"step_{i}" for i in range(20)]
        edges = [_e(ids[i], ids[i + 1]) for i in range(19)]
        assert topo_sort_ids(ids, edges) == ids


# ---------------------------------------------------------------------------
# Integration smoke — real call site
# ---------------------------------------------------------------------------


class TestIntegrationSmoke:
    """Verify the topo sort still wires up correctly behind
    :func:`haute._execute_lazy._prepare_graph` — the main executor caller.
    """

    def test_prepare_graph_returns_topologically_valid_order(self) -> None:
        """Run a small synthetic PipelineGraph through ``_prepare_graph``
        and check the returned order respects every edge in the graph.

        This is the real integration point that the executor, trace, and
        codegen all depend on. If the topo refactor breaks its signature
        or return shape, this test fails loudly rather than waiting for
        end-to-end pipeline execution to fall over.
        """
        nodes = [
            GraphNode(id="src", type="dataSource", data=NodeData(label="Source")),
            GraphNode(id="mid", type="polars", data=NodeData(label="Transform")),
            GraphNode(id="snk", type="output", data=NodeData(label="Output")),
        ]
        edges = [_e("src", "mid"), _e("mid", "snk")]
        graph = PipelineGraph(nodes=nodes, edges=edges)

        _node_map, order, _parents_of, _id_to_name = _prepare_graph(graph)

        assert set(order) == {"src", "mid", "snk"}
        assert _is_topologically_valid(order, edges)
        assert order == ["src", "mid", "snk"]

    def test_prepare_graph_target_filters_to_ancestors(self) -> None:
        """``target_node_id`` restricts the order to that node's ancestors.

        Pins that the slice through ``ancestors`` + ``topo_sort_ids``
        continues to cooperate post-refactor.
        """
        nodes = [
            GraphNode(id="a", type="dataSource", data=NodeData(label="A")),
            GraphNode(id="b", type="polars", data=NodeData(label="B")),
            GraphNode(id="c", type="polars", data=NodeData(label="C")),
            GraphNode(id="d", type="output", data=NodeData(label="D")),
        ]
        # a -> b -> d  and  c -> d  (c is an ancestor of d, not of b)
        edges = [_e("a", "b"), _e("b", "d"), _e("c", "d")]
        graph = PipelineGraph(nodes=nodes, edges=edges)

        _node_map, order, _parents_of, _id_to_name = _prepare_graph(
            graph, target_node_id="b"
        )

        # Targeting b should include only b's ancestors (a, b), not c or d.
        assert set(order) == {"a", "b"}
        assert order == ["a", "b"]


# ---------------------------------------------------------------------------
# Documentation / sanity: graphlib behaviour reference
# ---------------------------------------------------------------------------


class TestGraphlibReferenceBehaviour:
    """Document the stdlib ``graphlib`` contract the refactor will lean on.

    If a future Python release changes ``graphlib.TopologicalSorter``'s
    contract in a way that Haute depends on (e.g. drops ``CycleError``'s
    node-list args tuple), this test fails LOUDLY rather than letting the
    pipeline silently report opaque errors to the user.

    These tests DO NOT touch ``haute._topo`` — they lock in the stdlib
    shape the refactor will target.
    """

    def test_graphlib_reports_cycles_with_node_list(self) -> None:
        """``graphlib.CycleError.args[1]`` is the list of nodes in the cycle.

        The Haute ``CycleError`` wrapper relies on this contract to
        extract node ids from the stdlib exception.
        """
        sorter = graphlib.TopologicalSorter()
        sorter.add("b", "a")
        sorter.add("a", "b")

        with pytest.raises(graphlib.CycleError) as exc_info:
            list(sorter.static_order())

        # args[1] is the list of nodes participating in the detected cycle.
        # It may include a node repeated at the end to show the closure.
        cycle_nodes = exc_info.value.args[1]
        assert {"a", "b"} <= set(cycle_nodes)

    def test_graphlib_static_order_is_valid_topo(self) -> None:
        """``graphlib`` produces a valid topological order for a simple DAG."""
        sorter = graphlib.TopologicalSorter()
        sorter.add("b", "a")
        sorter.add("c", "b")

        order = list(sorter.static_order())

        # a must precede b, which must precede c.
        assert order.index("a") < order.index("b") < order.index("c")
