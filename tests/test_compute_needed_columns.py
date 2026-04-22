"""Tests for ``_compute_needed_columns`` — backward column analysis.

These tests pin the public contract of ``_compute_needed_columns`` so the
production implementation can be rewritten from an O(edges × contract
lookups) backward pass into a single forward-pass over sorted nodes that
reuses a per-node cached ``get_column_contract`` result (review item #87
in ``docs/CODEBASE_REVIEW.md``).

The suite has three layers:

1. **Topology invariants** — linear chain, diamond, fan-out, fan-in,
   opaque propagation, empty graph.  These pass on the current
   implementation today and must keep passing after the rewrite.
2. **Semantic invariants** — contract algebra: upstream needs equal
   ``(downstream_needed - produced_here) | referenced_here``.  These are
   phrased so an equivalent forward-pass implementation yields identical
   results node-by-node.
3. **Algorithmic work benchmark** - on a 200-node realistic graph
   (mix of passthrough + banding + output), the forward-pass shape
   computes column contracts once per node instead of once per incoming
   edge.  This pins the deterministic source of the performance win
   without making CI depend on an exact wall-clock ratio.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

import pytest

from haute._builders import get_column_contract
from haute._execute_lazy import _compute_needed_columns
from haute._types import (
    GraphNode,
    NodeData,
    NodeType,
)

# ---------------------------------------------------------------------------
# Graph-construction helpers
# ---------------------------------------------------------------------------


def _node(nid: str, node_type: NodeType, **config) -> GraphNode:
    return GraphNode(id=nid, data=NodeData(label=nid, nodeType=node_type, config=config))


def _source(nid: str) -> GraphNode:
    return _node(nid, NodeType.DATA_SOURCE)


def _output(nid: str, fields: list[str] | None = None) -> GraphNode:
    return _node(nid, NodeType.OUTPUT, fields=fields or [])


def _banding(nid: str, factors: list[dict] | None = None) -> GraphNode:
    return _node(nid, NodeType.BANDING, factors=factors or [])


def _polars(nid: str) -> GraphNode:
    """Opaque POLARS node (unknown produced + referenced)."""
    return _node(nid, NodeType.POLARS)


def _passthrough(nid: str) -> GraphNode:
    """Explicit passthrough (LIVE_SWITCH) — produced=∅, referenced=∅."""
    return _node(nid, NodeType.LIVE_SWITCH)


def _sink(nid: str) -> GraphNode:
    return _node(nid, NodeType.DATA_SINK)


def _build_children_of(
    order: list[str],
    parents_of: dict[str, list[str]],
) -> dict[str, list[str]]:
    """Mirror the children_of construction done inside ``_execute_lazy``."""
    children_of: dict[str, list[str]] = {nid: [] for nid in order}
    for nid, pids in parents_of.items():
        for pid in pids:
            if pid in children_of:
                children_of[pid].append(nid)
    return children_of


# ---------------------------------------------------------------------------
# Reference implementations used by the benchmark
# ---------------------------------------------------------------------------


def _reference_backward_pass(
    order: list[str],
    children_of: dict[str, list[str]],
    node_map: dict[str, GraphNode],
) -> dict[str, set[str] | None]:
    """Literal transcription of the current O(edges × contract-lookup)
    backward pass.  Each child's contract is re-fetched once per parent
    that visits it, which is the inefficiency review item #87 targets.

    Behaviour is byte-for-byte identical to the current production
    ``_compute_needed_columns`` (see ``_execute_lazy.py`` lines 207+).
    Kept here so the benchmark has a stable baseline to measure the
    forward pass against, independent of whatever the production
    function later becomes.
    """
    needed: dict[str, set[str] | None] = {}
    for nid in reversed(order):
        node = node_map[nid]
        children = children_of.get(nid, [])
        if not children:
            if node.data.nodeType == NodeType.OUTPUT:
                fields = node.data.config.get("fields") or []
                needed[nid] = set(fields) if fields else None
            else:
                needed[nid] = None
            continue
        needed_by_children: set[str] | None = set()
        for cid in children:
            child_node = node_map[cid]
            child_needed = needed.get(cid)
            if child_needed is None:
                needed_by_children = None
                break
            produced, referenced = get_column_contract(
                child_node.data.nodeType,
                child_node.data.config,
            )
            if produced is None or referenced is None:
                needed_by_children = None
                break
            from_parent = (child_needed - produced) | referenced
            needed_by_children |= from_parent  # type: ignore[operator]
        needed[nid] = needed_by_children
    return needed


def _reference_forward_pass(
    order: list[str],
    children_of: dict[str, list[str]],
    node_map: dict[str, GraphNode],
) -> dict[str, set[str] | None]:
    """Reference O(V + E) forward-pass.

    Key optimisations over the backward transcription:

    * ``get_column_contract`` is invoked **once per node**, not once
      per incoming edge.  For a node with ``k`` parents this drops
      ``k-1`` redundant lookups.
    * The node-level contribution ``(needed[n] - produced_n) |
      referenced_n`` is computed **once per node** and cached, not
      recomputed per incoming edge.  This is the bigger win — that
      expression is the dominant work on wide fan-ins where each
      aggregator's set algebra otherwise runs ``in_degree`` times.
    * A single reverse-topo sweep visits each node once; after
      ``needed[nid]`` is known, ``contribution[nid]`` is the set each
      parent should union in.  There is no second loop over children.
    * Opaque propagation is an early-exit: if ``contribution[cid]`` is
      ``None`` (meaning the child or any of its descendants is
      opaque), the parent's accumulator short-circuits to ``None``.

    The algorithm must return exactly the same dict as the backward
    pass for all topologies — this is pinned by the equivalence tests
    below.
    """
    needed: dict[str, set[str] | None] = {}
    # For each node n, ``contribution[n]`` is the set a parent should
    # union in for n as a child: ``(needed[n] - produced_n) |
    # referenced_n``.  ``None`` means "parent must fall to None".
    #
    # The whole point of the rewrite is to compute this once per node,
    # not once per incoming edge.
    contribution: dict[str, set[str] | None] = {}

    for nid in reversed(order):
        node = node_map[nid]
        children = children_of.get(nid, [])

        if not children:
            if node.data.nodeType == NodeType.OUTPUT:
                fields = node.data.config.get("fields") or []
                needed[nid] = set(fields) if fields else None
            else:
                needed[nid] = None
        else:
            acc: set[str] | None = set()
            for cid in children:
                child_contrib = contribution.get(cid)
                if child_contrib is None:
                    acc = None
                    break
                acc |= child_contrib  # type: ignore[operator]
            needed[nid] = acc

        # Cache this node's contribution to its parents (called at most
        # once per node, reused by every parent).
        my_needed = needed[nid]
        if my_needed is None:
            contribution[nid] = None
            continue
        produced, referenced = get_column_contract(
            node.data.nodeType,
            node.data.config,
        )
        if produced is None or referenced is None:
            contribution[nid] = None
        else:
            contribution[nid] = (my_needed - produced) | referenced

    return needed


# ===========================================================================
# Correctness — topology invariants
# ===========================================================================


class TestLinearChain:
    """Chains exercise the backward-propagation rule without fan-out."""

    def test_ten_node_passthrough_chain_propagates_terminal_fields(self):
        """In a 10-node chain of passthrough nodes, every intermediate
        node's ``needed_cols`` equals the terminal OUTPUT's field set.

        Pins the invariant that passthrough propagation is lossless —
        nothing should be dropped by an explicit passthrough.
        """
        nodes = [_source("n0")]
        nodes.extend(_passthrough(f"n{i}") for i in range(1, 9))
        nodes.append(_output("n9", fields=["alpha", "beta", "gamma"]))
        node_map = {n.id: n for n in nodes}
        order = [f"n{i}" for i in range(10)]
        parents_of = {order[i]: ([order[i - 1]] if i > 0 else []) for i in range(10)}
        children_of = _build_children_of(order, parents_of)

        needed = _compute_needed_columns(order, children_of, node_map)

        assert needed["n9"] == {"alpha", "beta", "gamma"}
        for intermediate in order[:-1]:
            assert needed[intermediate] == {"alpha", "beta", "gamma"}, (
                f"intermediate {intermediate!r} did not propagate losslessly"
            )

    def test_linear_chain_with_banding_subtracts_produced_and_adds_referenced(self):
        """A banding step creates ``X_band`` from ``X``; upstream needs
        ``X`` not ``X_band``, plus any columns the output demands that
        banding didn't create."""
        nodes = [
            _source("src"),
            _banding(
                "band",
                factors=[{"column": "age", "outputColumn": "age_band"}],
            ),
            _output("out", fields=["age_band", "extra"]),
        ]
        node_map = {n.id: n for n in nodes}
        order = ["src", "band", "out"]
        parents_of = {"src": [], "band": ["src"], "out": ["band"]}
        children_of = _build_children_of(order, parents_of)

        needed = _compute_needed_columns(order, children_of, node_map)

        assert needed["out"] == {"age_band", "extra"}
        # band carries {age_band, extra} downstream
        assert needed["band"] == {"age_band", "extra"}
        # src: ({age_band, extra} - {age_band}) | {age} = {age, extra}
        assert needed["src"] == {"age", "extra"}


class TestDiamond:
    """Diamond = fan-out then fan-in.  Pins the union rule at the source."""

    def test_simple_diamond_source_is_union_of_branches(self):
        """Source → (branch_a | branch_b) → sink.

        Source's needed_cols = union of the two branches' contributions.
        Branches are explicit passthroughs so their contribution is
        exactly ``needed[branch]``.
        """
        nodes = [
            _source("src"),
            _passthrough("a"),
            _passthrough("b"),
            _passthrough("sink"),  # passthrough sink so we can check its parents
            _output("out", fields=["x", "y", "z"]),
        ]
        # src → a → sink; src → b → sink; sink → out
        node_map = {n.id: n for n in nodes}
        order = ["src", "a", "b", "sink", "out"]
        parents_of = {
            "src": [],
            "a": ["src"],
            "b": ["src"],
            "sink": ["a", "b"],
            "out": ["sink"],
        }
        children_of = _build_children_of(order, parents_of)

        needed = _compute_needed_columns(order, children_of, node_map)

        # Every passthrough just carries the OUTPUT fields through.
        assert needed["out"] == {"x", "y", "z"}
        assert needed["sink"] == {"x", "y", "z"}
        assert needed["a"] == {"x", "y", "z"}
        assert needed["b"] == {"x", "y", "z"}
        # src sees the union from a + b (which is the same set).
        assert needed["src"] == {"x", "y", "z"}

    def test_diamond_distinct_branch_needs_union_at_source(self):
        """Each branch consumes different columns; source gets the union.

        Source → BandA(col:a→a_band) → Output1(fields=[a_band, shared])
        Source → BandB(col:b→b_band) → Output2(fields=[b_band, shared])

        Source needs {a, b, shared} (union of what each branch requires
        from it).  ``shared`` is needed by both outputs and neither
        branch produces it, so it appears in both contributions — the
        union still equals {a, b, shared}.
        """
        nodes = [
            _source("src"),
            _banding("ba", factors=[{"column": "a", "outputColumn": "a_band"}]),
            _banding("bb", factors=[{"column": "b", "outputColumn": "b_band"}]),
            _output("o1", fields=["a_band", "shared"]),
            _output("o2", fields=["b_band", "shared"]),
        ]
        node_map = {n.id: n for n in nodes}
        order = ["src", "ba", "bb", "o1", "o2"]
        parents_of = {
            "src": [],
            "ba": ["src"],
            "bb": ["src"],
            "o1": ["ba"],
            "o2": ["bb"],
        }
        children_of = _build_children_of(order, parents_of)

        needed = _compute_needed_columns(order, children_of, node_map)

        assert needed["src"] == {"a", "b", "shared"}


class TestOpaquePropagation:
    """``None`` (opaque) must propagate to every ancestor."""

    def test_opaque_consumer_propagates_none_through_whole_chain(self):
        """Single opaque POLARS node in the middle of a chain taints
        every upstream node's ``needed_cols`` with ``None``.
        """
        # src → p1 → opaque → p2 → out
        nodes = [
            _source("src"),
            _passthrough("p1"),
            _polars("opaque"),
            _passthrough("p2"),
            _output("out", fields=["z"]),
        ]
        node_map = {n.id: n for n in nodes}
        order = ["src", "p1", "opaque", "p2", "out"]
        parents_of = {
            "src": [],
            "p1": ["src"],
            "opaque": ["p1"],
            "p2": ["opaque"],
            "out": ["p2"],
        }
        children_of = _build_children_of(order, parents_of)

        needed = _compute_needed_columns(order, children_of, node_map)

        # Downstream of opaque is concrete (its child is passthrough OUTPUT):
        assert needed["out"] == {"z"}
        assert needed["p2"] == {"z"}
        # opaque's own needed is also {z} — needed[n] is about n's output,
        # so until we *consult* opaque's contract from its parent we
        # don't yet know it's opaque.
        assert needed["opaque"] == {"z"}
        # But p1 asks "what does my child (opaque) need from me?" and
        # falls to None because opaque's contract is (None, None).
        assert needed["p1"] is None
        assert needed["src"] is None

    def test_opaque_sibling_in_fanout_taints_shared_parent(self):
        """Parent fans out to a concrete child AND an opaque child.
        The opaque child alone forces the parent to ``None``.
        """
        nodes = [
            _source("src"),
            _passthrough("fanout"),
            _polars("opaque_child"),
            _output("concrete_child", fields=["x"]),
        ]
        node_map = {n.id: n for n in nodes}
        order = ["src", "fanout", "opaque_child", "concrete_child"]
        parents_of = {
            "src": [],
            "fanout": ["src"],
            "opaque_child": ["fanout"],
            "concrete_child": ["fanout"],
        }
        children_of = _build_children_of(order, parents_of)

        needed = _compute_needed_columns(order, children_of, node_map)

        # fanout has one opaque + one concrete child → opaque wins.
        assert needed["fanout"] is None
        assert needed["src"] is None


class TestContractAlgebra:
    """Pins the precise rule:
    needed_at_parent_from_child = (needed[child] - produced_child) | referenced_child.
    """

    def test_upstream_sees_inputs_plus_unmet_downstream_demand(self):
        """When a node declares ``referenced = {a, b}`` but the
        downstream node only uses ``c`` (which the middle node
        produces), upstream must still see ``{a, b}`` plus any
        downstream column the middle node doesn't create.
        """
        # Banding(col:a→a_band, col:b→b_band) reads {a, b} and produces
        # {a_band, b_band}.  Output asks for {a_band, extra}.  Upstream
        # source must receive {a, b, extra}.
        nodes = [
            _source("src"),
            _banding(
                "band",
                factors=[
                    {"column": "a", "outputColumn": "a_band"},
                    {"column": "b", "outputColumn": "b_band"},
                ],
            ),
            _output("out", fields=["a_band", "extra"]),
        ]
        node_map = {n.id: n for n in nodes}
        order = ["src", "band", "out"]
        parents_of = {"src": [], "band": ["src"], "out": ["band"]}
        children_of = _build_children_of(order, parents_of)

        needed = _compute_needed_columns(order, children_of, node_map)

        # Pin the exact algebra.
        # band_needed = {a_band, extra}
        # band produces {a_band, b_band}, references {a, b}
        # ({a_band, extra} - {a_band, b_band}) | {a, b}
        #   = {extra} | {a, b} = {a, b, extra}
        assert needed["src"] == {"a", "b", "extra"}

    def test_produced_columns_not_double_requested(self):
        """A column produced by the node itself must be subtracted from
        the downstream need before it's passed upward.  Otherwise the
        source would be asked for something that only exists after the
        banding step runs.
        """
        nodes = [
            _source("src"),
            _banding(
                "band",
                factors=[{"column": "age", "outputColumn": "age_band"}],
            ),
            _output("out", fields=["age_band"]),
        ]
        node_map = {n.id: n for n in nodes}
        order = ["src", "band", "out"]
        parents_of = {"src": [], "band": ["src"], "out": ["band"]}
        children_of = _build_children_of(order, parents_of)

        needed = _compute_needed_columns(order, children_of, node_map)

        # age_band is produced by band, so upstream must NOT see it.
        assert needed["src"] == {"age"}
        assert "age_band" not in needed["src"]


class TestEdgeCases:
    """Degenerate graphs that shouldn't crash or return garbage."""

    def test_empty_graph_returns_empty_dict(self):
        needed = _compute_needed_columns([], {}, {})
        assert needed == {}

    def test_single_source_no_consumers_returns_none(self):
        """A single node with no children is terminal.  It's not an
        OUTPUT so ``needed`` is ``None`` (we don't know what — if
        anything — a hypothetical consumer would want).
        """
        node_map = {"src": _source("src")}
        needed = _compute_needed_columns(["src"], {"src": []}, node_map)
        assert needed == {"src": None}

    def test_single_output_no_consumers_with_fields(self):
        """A terminal OUTPUT with fields: needed = those fields."""
        node_map = {"out": _output("out", fields=["a", "b"])}
        needed = _compute_needed_columns(["out"], {"out": []}, node_map)
        assert needed == {"out": {"a", "b"}}

    def test_single_output_no_fields_returns_none(self):
        """A terminal OUTPUT with no fields signals "all columns" ≡ None."""
        node_map = {"out": _output("out", fields=[])}
        needed = _compute_needed_columns(["out"], {"out": []}, node_map)
        assert needed == {"out": None}

    def test_single_source_with_one_output_child(self):
        """A source with exactly one OUTPUT child inherits that OUTPUT's
        fields (since OUTPUT is passthrough: produced=∅, referenced=∅).
        """
        nodes = [_source("src"), _output("out", fields=["x", "y"])]
        node_map = {n.id: n for n in nodes}
        order = ["src", "out"]
        parents_of = {"src": [], "out": ["src"]}
        children_of = _build_children_of(order, parents_of)

        needed = _compute_needed_columns(order, children_of, node_map)

        assert needed["out"] == {"x", "y"}
        assert needed["src"] == {"x", "y"}

    def test_terminal_non_output_does_not_crash(self):
        """A terminal DATA_SINK is not an OUTPUT — its needed is
        ``None`` and that propagates up without error.
        """
        nodes = [_source("src"), _sink("sink")]
        node_map = {n.id: n for n in nodes}
        order = ["src", "sink"]
        parents_of = {"src": [], "sink": ["src"]}
        children_of = _build_children_of(order, parents_of)

        needed = _compute_needed_columns(order, children_of, node_map)

        assert needed["sink"] is None
        assert needed["src"] is None


# ===========================================================================
# Equivalence: reference backward pass == reference forward pass == production
# ===========================================================================


def _equivalence_cases() -> list[tuple[str, Callable[[], tuple[list[str], dict, dict]]]]:
    """Return labelled builders for graphs we compare across algorithms.

    Each builder returns (order, children_of, node_map).
    """

    def linear_chain_10():
        nodes = [_source("n0")]
        nodes.extend(_passthrough(f"n{i}") for i in range(1, 9))
        nodes.append(_output("n9", fields=["a", "b", "c"]))
        node_map = {n.id: n for n in nodes}
        order = [f"n{i}" for i in range(10)]
        parents_of = {order[i]: ([order[i - 1]] if i > 0 else []) for i in range(10)}
        return order, _build_children_of(order, parents_of), node_map

    def diamond_distinct():
        nodes = [
            _source("src"),
            _banding("ba", factors=[{"column": "a", "outputColumn": "a_band"}]),
            _banding("bb", factors=[{"column": "b", "outputColumn": "b_band"}]),
            _output("o1", fields=["a_band", "shared"]),
            _output("o2", fields=["b_band", "shared"]),
        ]
        node_map = {n.id: n for n in nodes}
        order = ["src", "ba", "bb", "o1", "o2"]
        parents_of = {
            "src": [],
            "ba": ["src"],
            "bb": ["src"],
            "o1": ["ba"],
            "o2": ["bb"],
        }
        return order, _build_children_of(order, parents_of), node_map

    def opaque_in_chain():
        nodes = [
            _source("src"),
            _passthrough("p1"),
            _polars("opaque"),
            _passthrough("p2"),
            _output("out", fields=["z"]),
        ]
        node_map = {n.id: n for n in nodes}
        order = ["src", "p1", "opaque", "p2", "out"]
        parents_of = {
            "src": [],
            "p1": ["src"],
            "opaque": ["p1"],
            "p2": ["opaque"],
            "out": ["p2"],
        }
        return order, _build_children_of(order, parents_of), node_map

    def fan_out_mixed():
        nodes = [
            _source("src"),
            _passthrough("fanout"),
            _polars("opaque_child"),
            _output("concrete_child", fields=["x"]),
        ]
        node_map = {n.id: n for n in nodes}
        order = ["src", "fanout", "opaque_child", "concrete_child"]
        parents_of = {
            "src": [],
            "fanout": ["src"],
            "opaque_child": ["fanout"],
            "concrete_child": ["fanout"],
        }
        return order, _build_children_of(order, parents_of), node_map

    def empty_graph():
        return [], {}, {}

    def single_source():
        return ["src"], {"src": []}, {"src": _source("src")}

    return [
        ("linear_chain_10", linear_chain_10),
        ("diamond_distinct", diamond_distinct),
        ("opaque_in_chain", opaque_in_chain),
        ("fan_out_mixed", fan_out_mixed),
        ("empty_graph", empty_graph),
        ("single_source", single_source),
    ]


_EQUIVALENCE_CASES = _equivalence_cases()


@pytest.mark.parametrize(
    "label, build",
    _EQUIVALENCE_CASES,
    ids=[lbl for lbl, _ in _EQUIVALENCE_CASES],
)
class TestAlgorithmEquivalence:
    """All three implementations produce byte-identical dicts.

    This guards against a developer landing a forward-pass rewrite that
    subtly changes semantics for some topology, and conversely guarantees
    that the in-test reference forward pass used by the benchmark really
    is a drop-in replacement for the production backward pass.
    """

    def test_reference_backward_matches_production(self, label, build):
        order, children_of, node_map = build()
        assert _reference_backward_pass(order, children_of, node_map) == _compute_needed_columns(
            order,
            children_of,
            node_map,
        )

    def test_reference_forward_matches_production(self, label, build):
        order, children_of, node_map = build()
        assert _reference_forward_pass(order, children_of, node_map) == _compute_needed_columns(
            order,
            children_of,
            node_map,
        )


# ===========================================================================
# Benchmark — 200-node realistic graph
# ===========================================================================


def _build_realistic_200_node_graph() -> tuple[
    list[str],
    dict[str, list[str]],
    dict[str, GraphNode],
]:
    """Build a 200-node graph resembling a production pricing pipeline.

    The baseline algorithm's quadratic behaviour comes from calling
    ``get_column_contract(child)`` once **per incoming edge** of every
    child node.  A node with in-degree ``k`` has its contract recomputed
    ``k`` times — ``k-1`` of those are wasted.  To expose that cost the
    benchmark uses a dense bipartite "banding bank → aggregator bank"
    pattern where every aggregator has dozens of parents:

        source ──► bandings_per_bank multi-factor bandings (layer A)
                        │
                        └─► aggregators_per_bank aggregators (each has all
                        │       bandings_per_bank parents — dense bipartite)
                        │         │
                        │         └─► bandings_per_bank bandings (layer B)
                        │                   │
                        │                   └─► aggregators_per_bank aggregators
                        │                             │
                        │                             └─► chain of passthroughs
                        │                                      │
                        │                                      └─► final OUTPUT

    With the defaults below (60 bandings × 10 aggregators per bank,
    two stages) the edge-to-node ratio is ≈ 7, so the baseline issues
    ≈ 7× more contract lookups than the forward pass.  BANDING
    contracts are built via set comprehension over
    ``factors_per_banding`` factors so each call is expensive —
    contract cost dominates the raw set-arithmetic cost and the
    redundant lookups are where the time goes.

    Realism: stacked banding + aggregation stages are the most common
    shape in production pricing pipelines (factors → sum → apply →
    sum again → output).  The ``POLARS`` node is intentionally omitted
    from the *benchmark* graph because it is opaque and would
    short-circuit the backward pass to ``None``, trivially matching
    both algorithms and defeating the timing.
    """
    bandings_per_bank = 60  # fan-out per banding bank
    aggregators_per_bank = 10  # fan-in bank — each sees 60 parents
    factors_per_banding = 3  # kept small so accumulated-set sizes stay
    # manageable — the benchmark's signal is contract-lookup cost, not
    # set-arithmetic throughput.
    nodes: list[GraphNode] = []
    parents_of: dict[str, list[str]] = {}
    order: list[str] = []

    def add(node: GraphNode, parents: list[str]) -> str:
        nodes.append(node)
        parents_of[node.id] = list(parents)
        order.append(node.id)
        return node.id

    def add_banding_bank(
        bank_id: str,
        parent_id: str,
        count: int,
    ) -> tuple[list[str], list[str]]:
        """Return (banding_ids, first_output_per_banding)."""
        band_ids = []
        outputs = []
        for b in range(count):
            factors = [
                {
                    "column": f"{bank_id}_b{b}_c{fi}",
                    "outputColumn": f"{bank_id}_b{b}_o{fi}",
                }
                for fi in range(factors_per_banding)
            ]
            bid = add(_banding(f"{bank_id}_b{b}", factors=factors), [parent_id])
            band_ids.append(bid)
            outputs.append(f"{bank_id}_b{b}_o0")
        return band_ids, outputs

    def add_aggregator_bank(
        bank_id: str,
        parent_ids: list[str],
        count: int,
    ) -> list[str]:
        """Every aggregator in the bank takes ALL parents — dense bipartite."""
        return [add(_passthrough(f"{bank_id}_a{a}"), parent_ids) for a in range(count)]

    # Source
    src = add(_source("src"), [])

    # Stage 1: bandings_per_bank bandings → aggregators_per_bank
    # aggregators (full bipartite — every aggregator sees every banding).
    band_a_ids, band_a_outs = add_banding_bank("s1_a", src, bandings_per_bank)
    agg_a_ids = add_aggregator_bank("s1_agg", band_a_ids, aggregators_per_bank)

    # Single merge collapsing the first aggregator bank so it has a
    # unique non-terminal downstream.  Without this, aggregators beyond
    # the one feeding stage 2 would be terminal non-OUTPUTs and their
    # ``needed`` would be ``None``, which would then poison every
    # upstream node via opaque propagation and turn the benchmark into
    # a trivial ``None`` sweep.
    merge_a = add(_passthrough("s1_merge"), agg_a_ids)

    # Stage 2: same shape again.
    band_b_ids, band_b_outs = add_banding_bank("s2_b", merge_a, bandings_per_bank)
    agg_b_ids = add_aggregator_bank("s2_agg", band_b_ids, aggregators_per_bank)

    # Single merge collapsing the second aggregator bank (same reason
    # as merge_a).
    merge_b = add(_passthrough("s2_merge"), agg_b_ids)

    # Tail passthrough chain to pad up toward 199.
    prev = merge_b
    chain_idx = 0
    while len(order) < 199:
        prev = add(_passthrough(f"chain_{chain_idx}"), [prev])
        chain_idx += 1

    # Terminal OUTPUT citing one banding output per bank — both banks
    # must be traversed with real set math, no ``None`` short circuits.
    fields = [band_a_outs[0], band_a_outs[1], band_b_outs[0], band_b_outs[1]]
    add(_output("final_out", fields=fields), [prev])

    node_map = {n.id: n for n in nodes}
    children_of = _build_children_of(order, parents_of)
    return order, children_of, node_map


def _count_contract_lookups(
    monkeypatch: pytest.MonkeyPatch,
    module: object,
    fn: Callable[[], dict[str, set[str] | None]],
) -> tuple[int, dict[str, set[str] | None]]:
    """Run *fn* while counting calls to that module's contract resolver."""
    original = getattr(module, "get_column_contract")
    calls = 0

    def counted(node_type: NodeType, config: dict) -> tuple[set[str] | None, set[str] | None]:
        nonlocal calls
        calls += 1
        return original(node_type, config)

    monkeypatch.setattr(module, "get_column_contract", counted)
    result = fn()
    return calls, result


class TestForwardPassReferenceAlgorithmBenchmark:
    """Reference-vs-reference guard for the algorithm shape.

    This class compares two *reference* implementations defined in this
    test file (``_reference_forward_pass`` vs ``_reference_backward_pass``).
    Neither is production code.  The purpose is to characterise the
    deterministic work reduction of the forward-pass shape independent of
    whatever ad-hoc optimisations production may pick up over time.
    """

    def test_graph_is_at_least_200_nodes(self):
        """Guardrail: if the builder changes, we still want 200+ nodes."""
        order, _children_of, _node_map = _build_realistic_200_node_graph()
        assert len(order) >= 200, f"benchmark graph has only {len(order)} nodes"

    def test_equivalence_on_200_node_graph(self):
        """The forward pass must match the backward pass bit-for-bit on
        the benchmark graph — otherwise the speed comparison is between
        two algorithms that aren't solving the same problem.
        """
        order, children_of, node_map = _build_realistic_200_node_graph()
        backward = _reference_backward_pass(order, children_of, node_map)
        forward = _reference_forward_pass(order, children_of, node_map)
        production = _compute_needed_columns(order, children_of, node_map)
        assert backward == forward
        assert backward == production

    def test_forward_pass_reference_algorithm_reduces_contract_work(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """The forward-pass reference must avoid per-edge contract lookup.

        Wall-clock ratios are too scheduler-sensitive for CI.  The
        deterministic optimisation delivered by the forward-pass shape is
        the contract lookup count: once per node instead of once per
        parent-child visit.
        """
        order, children_of, node_map = _build_realistic_200_node_graph()

        with monkeypatch.context() as ctx:
            backward_calls, backward = _count_contract_lookups(
                ctx,
                sys.modules[__name__],
                lambda: _reference_backward_pass(order, children_of, node_map),
            )
        with monkeypatch.context() as ctx:
            forward_calls, forward = _count_contract_lookups(
                ctx,
                sys.modules[__name__],
                lambda: _reference_forward_pass(order, children_of, node_map),
            )

        assert backward == forward
        assert forward_calls <= len(order)
        assert backward_calls >= forward_calls * 2, (
            "forward-reference did not cut contract lookups by at least half "
            "vs the backward-reference. "
            f"backward_calls={backward_calls} forward_calls={forward_calls}"
        )


class TestProductionComputeNeededColumnsBenchmark:
    """Production-path work-reduction guard for review item #87.

    Counts contract resolver calls in the real ``_compute_needed_columns``
    imported from ``haute._execute_lazy`` against the backward-reference
    baseline.  This keeps the performance claim deterministic in CI.
    """

    def test_production_compute_needed_columns_reuses_contracts_per_node(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Production must keep the deterministic work reduction from #87.

        The old backward pass fetched each child contract once per parent
        visit.  The production algorithm should fetch each node's contract
        at most once and reuse its contribution for every parent.  This
        checks the load-bearing performance claim without relying on a
        precise wall-clock ratio on shared CI runners.
        """
        order, children_of, node_map = _build_realistic_200_node_graph()

        with monkeypatch.context() as ctx:
            backward_calls, backward = _count_contract_lookups(
                ctx,
                sys.modules[__name__],
                lambda: _reference_backward_pass(order, children_of, node_map),
            )
        with monkeypatch.context() as ctx:
            production_calls, production = _count_contract_lookups(
                ctx,
                sys.modules[_compute_needed_columns.__module__],
                lambda: _compute_needed_columns(order, children_of, node_map),
            )

        assert backward == production
        assert production_calls <= len(order)
        assert backward_calls >= production_calls * 2, (
            "production _compute_needed_columns did not cut contract lookups "
            "by at least half vs the backward-reference baseline. "
            f"backward_calls={backward_calls} production_calls={production_calls}"
        )
