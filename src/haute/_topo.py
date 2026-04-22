"""Topological sorting and graph traversal algorithms.

Haute originally hand-rolled Kahn's algorithm here.  We migrated to
:class:`graphlib.TopologicalSorter` (Python 3.9+ stdlib) because that
implementation is battle-tested, maintained upstream, and our previous
loop was almost line-for-line stdlib-equivalent behaviour.  The only
piece kept custom is :func:`_find_cycle_nodes` — :mod:`graphlib`
reports one representative from a cycle but not the disjoint union
when multiple cycles exist, and surfacing every participating node in
the user-facing :class:`CycleError` matters more than the code-size
saving.

Tie-break note: :mod:`graphlib` is insertion-order deterministic (unlike
the previous heap which sorted alphabetically).  Every caller must pass
``node_ids`` as an insertion-ordered sequence — passing a ``set`` would
couple the sort to CPython hash randomisation across process invocations.
"""

from __future__ import annotations

import graphlib
from collections import deque

from haute._graph_utils import build_parents_of
from haute._types import GraphEdge
from haute.errors import HauteError


class CycleError(HauteError):
    """Raised when a cycle is detected in the pipeline graph."""

    def __init__(self, cycle_nodes: list[str]) -> None:
        self.cycle_nodes = cycle_nodes
        names = ", ".join(sorted(cycle_nodes))
        super().__init__(
            f"Cycle detected in pipeline graph involving nodes: {names}. "
            f"Remove one of the edges to break the cycle."
        )


def topo_sort_ids(node_ids: list[str], edges: list[GraphEdge]) -> list[str]:
    """Topological sort of node IDs based on edges.

    Delegates ordering to :class:`graphlib.TopologicalSorter` (Python stdlib).
    Edges whose source or target is not in ``node_ids`` are silently skipped.

    Raises :class:`CycleError` if the graph contains a cycle, listing every
    node that participates in (or is downstream of) a cycle so the user
    can identify the offending edges from the GUI's error message.
    """
    sorter: graphlib.TopologicalSorter[str] = graphlib.TopologicalSorter()
    known = set(node_ids)

    # Register every node so isolated nodes still appear in the output.
    # Insertion order here defines the tie-break order for simultaneously
    # ready nodes in graphlib.static_order().
    for nid in node_ids:
        sorter.add(nid)

    # Only add edges whose endpoints are both known nodes. Unknown
    # endpoints are silently dropped (matches existing public behaviour).
    for e in edges:
        if e.source in known and e.target in known:
            sorter.add(e.target, e.source)

    try:
        return list(sorter.static_order())
    except graphlib.CycleError:
        raise CycleError(_find_cycle_nodes(node_ids, edges)) from None


def _find_cycle_nodes(node_ids: list[str], edges: list[GraphEdge]) -> list[str]:
    """Identify every node that participates in (or is downstream of) a cycle.

    This is the cold-path complement to ``graphlib.TopologicalSorter``:
    ``graphlib.CycleError.args[1]`` only names ONE cycle, so we peel
    acyclic-ancestor nodes ourselves and report everything that never
    reaches in-degree zero.  Acyclic tails (e.g. ``d`` in
    ``d -> a, a -> b, b -> a``) are excluded from the report, while
    disjoint cycles are all surfaced so the user can see every offending
    edge group in the GUI error message.
    """
    in_degree: dict[str, int] = {nid: 0 for nid in node_ids}
    children: dict[str, list[str]] = {nid: [] for nid in node_ids}

    for e in edges:
        if e.source not in in_degree or e.target not in in_degree:
            continue
        in_degree[e.target] += 1
        children[e.source].append(e.target)

    ready: deque[str] = deque(nid for nid, d in in_degree.items() if d == 0)
    peeled: set[str] = set()
    while ready:
        nid = ready.popleft()
        peeled.add(nid)
        for child in children[nid]:
            in_degree[child] -= 1
            if in_degree[child] == 0:
                ready.append(child)

    return [nid for nid in node_ids if nid not in peeled]


def ancestors(target_id: str, edges: list[GraphEdge], all_ids: set[str]) -> set[str]:
    """Get all ancestor node IDs of target (inclusive)."""
    parents = build_parents_of(edges, all_ids)

    visited: set[str] = set()
    queue = deque([target_id])
    while queue:
        nid = queue.popleft()
        if nid in visited:
            continue
        visited.add(nid)
        queue.extend(parents.get(nid, []))
    return visited
