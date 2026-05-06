"""Hooks for extending the node-function builder.

Provides ``NodeBuildHooks`` so callers like ``deploy/_scorer.py`` can
intercept specific node types without duplicating boilerplate setup code.

The base builder (``_build_node_fn``) lives in ``executor.py``.  This
module provides a lightweight wrapping mechanism so alternate execution
modes (scoring, testing) can override individual node types while
delegating everything else to the standard builder.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from haute._graph_utils import _sanitize_func_name
from haute._types import GraphNode, _Frame

# (func_name, callable, is_source)
NodeFnResult = tuple[str, Callable[..., _Frame], bool]


@dataclass(slots=True)
class NodeBuildHooks:
    """Interceptors applied before the default build logic.

    ``before_build`` receives ``(node, source_names, source_ids)`` and may
    return a ``NodeFnResult`` to override the default, or ``None`` to fall
    through to the base builder.
    """

    before_build: Callable[[GraphNode, list[str], list[str]], NodeFnResult | None] | None = None


def wrap_builder(
    base: Callable[..., NodeFnResult],
    hooks: NodeBuildHooks,
) -> Callable[..., NodeFnResult]:
    """Wrap a base node-function builder with hook interception.

    If ``hooks.before_build`` returns a non-None result, it takes
    precedence.  Otherwise the call falls through to *base* with all
    original keyword arguments forwarded.
    """

    def wrapped(
        node: GraphNode,
        source_names: list[str] | None = None,
        source_ids: list[str] | None = None,
        **kwargs: Any,
    ) -> NodeFnResult:
        names = source_names if source_names is not None else []
        ids = source_ids if source_ids is not None else []
        if hooks.before_build is not None:
            result = hooks.before_build(node, names, ids)
            if result is not None:
                return result
        return base(node, source_names=source_names, source_ids=source_ids, **kwargs)

    return wrapped


def node_fn_name(node: GraphNode) -> str:
    """Return the sanitized function name for a node (convenience helper)."""
    return _sanitize_func_name(node.data.label)
