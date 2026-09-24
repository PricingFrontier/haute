"""Edge-key lookup helpers for projection-plan test assertions.

Production projection mappings are keyed by the complete
:class:`~haute.projection.ProjectionEdgeKey`. Tests that assert on a single
``source -> target`` edge resolve the unique key here instead of the mappings
accepting lossy pairs.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from haute._types import GraphEdge
from haute.projection import ProjectionEdgeKey


def adjacency_edges(
    order: Iterable[str],
    children_of: Mapping[str, Iterable[str]],
) -> list[GraphEdge]:
    """Return one edge per ``children_of`` adjacency, as a prepared graph carries.

    The planners take the prepared graph's edges. A test that describes its
    topology by adjacency passes these; a repeated pair gets an ordinal suffix,
    as distinct edges between the same two nodes do.
    """
    occurrences: dict[tuple[str, str], int] = {}
    edges: list[GraphEdge] = []
    for source in order:
        for target in children_of.get(source, ()):
            ordinal = occurrences.get((source, target), 0)
            occurrences[(source, target)] = ordinal + 1
            edge_id = f"e_{source}_{target}" if ordinal == 0 else f"e_{source}_{target}_{ordinal}"
            edges.append(GraphEdge(id=edge_id, source=source, target=target))
    return edges


def edge_keys_for_pair(
    mapping: Mapping[ProjectionEdgeKey, Any],
    source: str,
    target: str,
) -> list[ProjectionEdgeKey]:
    """Return every complete key connecting ``source`` to ``target``."""
    return [key for key in mapping if key.source == source and key.target == target]


def has_pair(mapping: Mapping[ProjectionEdgeKey, Any], source: str, target: str) -> bool:
    """Whether any complete key connects ``source`` to ``target``."""
    return bool(edge_keys_for_pair(mapping, source, target))


def pair_value(mapping: Mapping[ProjectionEdgeKey, Any], source: str, target: str) -> Any:
    """Return the value for the unique ``source -> target`` key."""
    matches = edge_keys_for_pair(mapping, source, target)
    if len(matches) != 1:
        raise AssertionError(
            f"expected exactly one edge {source!r} -> {target!r}, found {len(matches)}"
        )
    return mapping[matches[0]]


def pair_value_or_none(
    mapping: Mapping[ProjectionEdgeKey, Any],
    source: str,
    target: str,
) -> Any:
    """Return the unique ``source -> target`` value, or ``None`` when absent."""
    matches = edge_keys_for_pair(mapping, source, target)
    if not matches:
        return None
    if len(matches) != 1:
        raise AssertionError(
            f"expected at most one edge {source!r} -> {target!r}, found {len(matches)}"
        )
    return mapping[matches[0]]
