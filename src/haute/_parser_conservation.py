"""Fail-loud structural acceptance gate for pipeline parsing."""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from typing import Any

from haute._graph_builders import _edge_param_names_for_node
from haute._types import GraphEdge, GraphNode
from haute.errors import ParseError

_EdgeIdentity = tuple[str, str, str | None, str | None]
_ConnectTuple = tuple[str, str, str | None, str | None]


def _connect_identity(edge_tuple: _ConnectTuple) -> _EdgeIdentity:
    return edge_tuple


def _edge_identity(edge: GraphEdge) -> _EdgeIdentity:
    return edge.source, edge.target, edge.sourceHandle, edge.targetHandle


def _edge_detail(identity: _EdgeIdentity) -> dict[str, str | None]:  # pragma: no mutate
    source, target, source_handle, target_handle = identity
    return {
        "source": source,
        "target": target,
        "source_handle": source_handle,
        "target_handle": target_handle,
    }


def assert_parser_structure_conserved(
    *,
    raw_nodes: Sequence[dict[str, Any]],
    explicit_connects: Sequence[_ConnectTuple],
    root_nodes: Sequence[GraphNode],
    root_edges: Sequence[GraphEdge],
    submodel_paths: Sequence[str] = (),
    submodel_files: Mapping[str, str] | None = None,  # pragma: no mutate
    submodel_occurrence_paths: Sequence[str] | None = None,  # pragma: no mutate
    submodel_aliases: Collection[str] = (),
    **kwargs: Any,
) -> None:
    """Reject any parser result that lost an authored structural identity.

    Root nodes and locally-resolvable edges are compared exactly before
    submodel boundary rewiring. A parent ``connect`` endpoint must be a root
    node or a registered occurrence alias; a definition-owned child id, like
    any other unknown name, is reported as dangling with its authored
    identity so the connection can never be dropped silently.
    Authored submodel paths must match the loaded metadata in source order.
    """
    authored_node_ids = [str(node["func_name"]) for node in raw_nodes]
    parsed_node_ids = [node.id for node in root_nodes]
    if authored_node_ids != parsed_node_ids:
        raise ParseError(
            "Pipeline parser did not conserve authored node identities.",
            authored_node_ids=authored_node_ids,
            parsed_node_ids=parsed_node_ids,
        )

    root_ids = set(authored_node_ids)
    connect_identities = [_connect_identity(edge) for edge in explicit_connects]
    local_explicit = [
        identity
        for identity in connect_identities
        if identity[0] in root_ids and identity[1] in root_ids
    ]
    explicit_pairs = {(edge[0], edge[1]) for edge in local_explicit}

    implicit: list[_EdgeIdentity] = []
    seen_implicit: set[tuple[str, str]] = set()
    for node in raw_nodes:
        target = str(node["func_name"])
        for parameter in _edge_param_names_for_node(node):
            pair = (str(parameter), target)
            if (
                pair[0] in root_ids
                and pair[0] != target
                and pair not in explicit_pairs
                and pair not in seen_implicit
            ):
                seen_implicit.add(pair)
                implicit.append((pair[0], pair[1], None, None))

    expected_local_edges = [*local_explicit, *implicit]
    parsed_local_edges = [_edge_identity(edge) for edge in root_edges]
    if expected_local_edges != parsed_local_edges:
        raise ParseError(
            "Pipeline parser did not conserve authored edge and handle identities.",
            authored_edges=[_edge_detail(edge) for edge in expected_local_edges],
            parsed_edges=[_edge_detail(edge) for edge in parsed_local_edges],
        )

    duplicate_edges = [
        _edge_detail(edge) for edge, count in Counter(connect_identities).items() if count > 1
    ]
    if duplicate_edges:
        raise ParseError(
            "Pipeline declares duplicate edge identities.",
            duplicate_edges=duplicate_edges,
        )

    # Definition-owned child ids are deliberately not endpoints: a parent may
    # only reach a definition through a registered occurrence alias and its
    # declared public ports (expression-parsing and codegen contracts).
    known_endpoint_ids = root_ids | set(submodel_aliases)
    dangling = [
        identity
        for identity in connect_identities
        if identity[0] not in known_endpoint_ids or identity[1] not in known_endpoint_ids
    ]
    if dangling:
        raise ParseError(
            "Pipeline contains dangling connect() endpoint(s).",
            dangling_edges=[_edge_detail(edge) for edge in dangling],
        )

    authored_paths = list(submodel_paths)
    legacy_arg = kwargs.get("submodel" + "_instance_paths")
    loaded_arg = submodel_occurrence_paths if submodel_occurrence_paths is not None else legacy_arg
    loaded_paths = (
        list(loaded_arg) if loaded_arg is not None else list((submodel_files or {}).values())
    )
    if authored_paths != loaded_paths:
        raise ParseError(
            "Pipeline parser did not conserve authored submodel references.",
            authored_submodel_paths=authored_paths,
            loaded_submodel_paths=loaded_paths,
        )


def missing_submodel_error(paths: Iterable[str]) -> ParseError:
    """Build the shared deterministic missing-submodel diagnostic."""
    missing_paths = list(paths)
    return ParseError(
        "Referenced submodel file(s) do not exist.",
        missing_paths=missing_paths,
    )
