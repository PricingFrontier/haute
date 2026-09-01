"""Topological sorting and graph traversal algorithms.

Ordering uses :class:`graphlib.TopologicalSorter`; :func:`_find_cycle_nodes`
additionally reports every node participating in a cycle. Every caller must
pass ``node_ids`` as an insertion-ordered sequence because the stdlib sorter
uses insertion order to break ties.
"""

from __future__ import annotations

import graphlib
from collections import deque
from dataclasses import dataclass

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


@dataclass(frozen=True, slots=True)
class FilteredTopology:
    """The result of explicitly sorting only edges with known endpoints."""

    order: list[str]
    dropped_edges: tuple[GraphEdge, ...]
    unknown_node_ids: tuple[str, ...]


class UnknownEdgeEndpointError(HauteError):
    """Raised when an edge refers to a node outside the supplied graph."""

    def __init__(
        self,
        unknown_node_ids: tuple[str, ...],
        dropped_edges: tuple[GraphEdge, ...],
    ) -> None:
        self.unknown_node_ids = unknown_node_ids
        self.dropped_edges = dropped_edges
        names = ", ".join(unknown_node_ids)
        super().__init__(f"Edges reference unknown node IDs: {names}.")


def _partition_edges(
    node_ids: list[str], edges: list[GraphEdge]
) -> tuple[list[GraphEdge], tuple[GraphEdge, ...], tuple[str, ...]]:
    """Split edges by endpoint membership and collect unknown node IDs."""
    known_node_ids = set(node_ids)
    known_edges: list[GraphEdge] = []
    dropped_edges: list[GraphEdge] = []
    unknown_node_ids: set[str] = set()

    for edge in edges:
        unknown_endpoints = {edge.source, edge.target} - known_node_ids
        if unknown_endpoints:
            dropped_edges.append(edge)
            unknown_node_ids.update(unknown_endpoints)
        else:
            known_edges.append(edge)

    return known_edges, tuple(dropped_edges), tuple(sorted(unknown_node_ids))


def _topo_sort_known_ids(node_ids: list[str], edges: list[GraphEdge]) -> list[str]:
    """Topological sort of node IDs based on edges.

    Delegates ordering to :class:`graphlib.TopologicalSorter` (Python stdlib).

    Raises :class:`CycleError` if the graph contains a cycle, listing every
    node that participates in (or is downstream of) a cycle so the user
    can identify the offending edges from the GUI's error message.
    """
    sorter: graphlib.TopologicalSorter[str] = graphlib.TopologicalSorter()

    # Register every node so isolated nodes still appear in the output.
    # Insertion order here defines the tie-break order for simultaneously
    # ready nodes in graphlib.static_order().
    for nid in node_ids:
        sorter.add(nid)

    for e in edges:
        sorter.add(e.target, e.source)

    try:
        return list(sorter.static_order())
    except graphlib.CycleError:
        raise CycleError(_find_cycle_nodes(node_ids, edges)) from None


def topo_sort_ids(node_ids: list[str], edges: list[GraphEdge]) -> list[str]:
    """Topologically sort node IDs, rejecting edges with unknown endpoints.

    Raises :class:`UnknownEdgeEndpointError` before sorting when an edge
    references a node not included in ``node_ids``.
    """
    known_edges, dropped_edges, unknown_node_ids = _partition_edges(node_ids, edges)
    if dropped_edges:
        raise UnknownEdgeEndpointError(unknown_node_ids, dropped_edges)
    return _topo_sort_known_ids(node_ids, known_edges)


def topo_sort_ids_filtered(node_ids: list[str], edges: list[GraphEdge]) -> FilteredTopology:
    """Sort known edges while explicitly reporting edges that were excluded."""
    known_edges, dropped_edges, unknown_node_ids = _partition_edges(node_ids, edges)
    return FilteredTopology(
        order=_topo_sort_known_ids(node_ids, known_edges),
        dropped_edges=dropped_edges,
        unknown_node_ids=unknown_node_ids,
    )


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
