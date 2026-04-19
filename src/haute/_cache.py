"""Graph fingerprinting for cache invalidation."""

from __future__ import annotations

import hashlib
import json as _json
from typing import Any

from haute._logging import get_logger
from haute._types import PipelineGraph

logger = get_logger(component="cache")


def _canonicalise(value: Any) -> Any:
    """Recursively convert *value* to a JSON-safe, order-independent form.

    The resulting structure is fed to ``json.dumps(..., sort_keys=True)``
    to produce a digest that is:

      * deterministic across runs (no ``repr()``-based fallbacks that
        depend on hash-seed or insertion order);
      * equal for sets / frozensets whose elements are the same regardless
        of the order they were inserted (unordered containers are sorted).

    Unsupported types raise ``TypeError`` loudly rather than silently
    reducing to ``repr()``.  This ensures a drift in config shape is
    caught at fingerprint time instead of producing quietly-wrong digests.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        # ``bool`` is a subclass of ``int`` but that's fine for our use —
        # both survive ``json.dumps`` losslessly.  We intentionally reject
        # ``bytes`` and ``complex`` below because neither has a canonical
        # JSON text form.
        return value
    if isinstance(value, (list, tuple)):
        return [_canonicalise(v) for v in value]
    if isinstance(value, (set, frozenset)):
        # Canonicalise members first so mixed-type sets raise loudly on
        # unsupported members rather than hitting the ``sorted`` TypeError
        # with a confusing message.
        members = [_canonicalise(v) for v in value]
        try:
            return sorted(members, key=_sort_key)
        except TypeError as exc:  # heterogeneous unsortable set
            raise TypeError(
                f"Cannot fingerprint set with unsortable members: {exc}",
            ) from exc
    if isinstance(value, dict):
        canon: dict[str, Any] = {}
        for k, v in value.items():
            if not isinstance(k, str):
                raise TypeError(
                    f"Cannot fingerprint dict with non-string key of type {type(k).__name__!r}",
                )
            canon[k] = _canonicalise(v)
        return canon
    raise TypeError(
        f"Cannot fingerprint value of type {type(value).__name__!r} — "
        f"no deterministic canonical form is defined",
    )


def _sort_key(value: Any) -> tuple[str, Any]:
    """Key function for sorting canonicalised set members.

    Produces a tuple of (type-tag, value) so mixed-type canonical values
    (all of which are JSON-safe by construction) can be ordered stably
    without relying on cross-type ``<`` support.
    """
    if value is None:
        return ("0_none", 0)
    if isinstance(value, bool):
        return ("1_bool", value)
    if isinstance(value, (int, float)):
        return ("2_num", value)
    if isinstance(value, str):
        return ("3_str", value)
    if isinstance(value, list):
        # Nested structures: sort by their JSON encoding.  ``sort_keys``
        # makes the encoding itself deterministic.
        return ("4_list", _json.dumps(value, sort_keys=True))
    if isinstance(value, dict):
        return ("5_dict", _json.dumps(value, sort_keys=True))
    raise TypeError(
        f"Cannot produce sort key for canonicalised value of type {type(value).__name__!r}",
    )


def _graph_base_fingerprint(graph: PipelineGraph) -> str:
    """Compute the base fingerprint of a graph's structure.

    Always recomputed to avoid serving stale cached results when the
    graph instance is mutated (e.g. node config changes).
    """
    parts: list[str] = []
    for n in sorted(graph.nodes, key=lambda n: n.id):
        canonical_config = _canonicalise(n.data.config)
        parts.append(
            f"{n.id}|{n.data.nodeType}|{_json.dumps(canonical_config, sort_keys=True)}",
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
