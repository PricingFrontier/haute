"""Edge-key lookup helpers for projection-plan test assertions.

Production projection mappings are keyed by the complete
:class:`~haute.projection.ProjectionEdgeKey`. Tests that assert on a single
``source -> target`` edge resolve the unique key here instead of the mappings
accepting lossy pairs.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from haute.projection import ProjectionEdgeKey


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
