"""Graph fingerprinting for cache invalidation."""

from __future__ import annotations

import hashlib
import json as _json

from haute._logging import get_logger
from haute._types import PipelineGraph

logger = get_logger(component="cache")


def _graph_base_fingerprint(graph: PipelineGraph) -> str:
    """Compute the base fingerprint of a graph's structure.

    Always recomputed to avoid serving stale cached results when the
    graph instance is mutated (e.g. node config changes).
    """
    parts: list[str] = []
    for n in sorted(graph.nodes, key=lambda n: n.id):
        parts.append(
            f"{n.id}|{n.data.nodeType}|{_json.dumps(n.data.config, sort_keys=True, default=repr)}",
        )
    for e in sorted(graph.edges, key=lambda e: (e.source, e.target)):
        parts.append(f"{e.source}->{e.target}")
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def graph_fingerprint(graph: PipelineGraph, *extra_keys: str) -> str:
    """Deterministic hash of graph structure for cache invalidation.

    *extra_keys* are prepended (e.g. target_node_id, row_limit) so the
    same graph with different execution parameters gets a different hash.
    Used by both the trace cache (trace.py) and preview cache (executor.py).

    The graph's base fingerprint (node configs + edge topology) is computed
    once per ``PipelineGraph`` instance and cached; only the extra-key
    combination adds overhead on subsequent calls.
    """
    base = _graph_base_fingerprint(graph)
    if not extra_keys:
        logger.debug("graph_fingerprint_computed", fingerprint=base[:8], extra_keys=())
        return base
    combined = "\n".join(extra_keys) + "\n" + base
    fp = hashlib.sha256(combined.encode()).hexdigest()
    logger.debug("graph_fingerprint_computed", fingerprint=fp[:8], extra_keys=extra_keys)
    return fp
