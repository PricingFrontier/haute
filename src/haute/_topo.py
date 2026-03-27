"""Topological sorting and graph traversal algorithms."""

from __future__ import annotations

import heapq
from collections import deque

from haute._types import GraphEdge, HauteError, build_parents_of


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
    """Topological sort of node IDs based on edges (Kahn's algorithm).

    Raises ``CycleError`` if the graph contains a cycle, listing the
    node IDs involved so the user can identify the offending edges.
    """
    in_degree: dict[str, int] = {nid: 0 for nid in node_ids}
    children: dict[str, list[str]] = {nid: [] for nid in node_ids}

    for e in edges:
        src, tgt = e.source, e.target
        # Skip edges where either endpoint is not a known node
        if src not in children or tgt not in in_degree:
            continue
        in_degree[tgt] += 1
        children[src].append(tgt)

    heap = [nid for nid, deg in in_degree.items() if deg == 0]
    heapq.heapify(heap)
    result: list[str] = []

    while heap:
        nid = heapq.heappop(heap)
        result.append(nid)
        for child in children.get(nid, []):
            in_degree[child] -= 1
            if in_degree[child] == 0:
                heapq.heappush(heap, child)

    if len(result) < len(node_ids):
        cycle_nodes = [nid for nid in node_ids if nid not in set(result)]
        raise CycleError(cycle_nodes)

    return result


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
