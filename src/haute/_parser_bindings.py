"""Strict parameter binding for parsed nodes (F13, READ-01).

Every node's positional parameters name its inputs by the one rule of
:mod:`haute._node_bindings`, which a standalone run applies too. The parser
never guesses a binding: a parameter that names no input, an input nothing
reads, a parameter at odds with the node's ``connect()`` calls, or calls in
another order than the parameters read them is a ``ParseError`` that the
editor surfaces as a degraded document, so no save can rewrite the authored
signature. A node that binds has its incoming edges put in the order its
parameters read them, the order every run gives it its frames.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from haute._graph_utils import REQUEST_INPUT_KINDS, incoming_edge_bindings
from haute._node_bindings import NodeInput, bind_node_inputs
from haute._types import GraphEdge, PipelineGraph, SubmodelInputPort
from haute.errors import ParseError

_ConnectCall = tuple[str, str, str | None, str | None]


def _declared_input_mapping(config: Mapping[str, Any]) -> Mapping[str, str] | None:
    """The node's logical-to-connected mapping, or None for unmapped nodes."""
    mapping = config.get("input_mapping")
    return mapping if isinstance(mapping, Mapping) else None


def _port_bound_inputs(input_ports: Sequence[SubmodelInputPort], node_id: str) -> list[str]:
    """Inputs a definition node receives through public input ports.

    Flattening binds a port-targeted node to the public input port name,
    so that port name is the name the authored parameter must carry.
    """
    return [
        port.name for port in input_ports for target in port.targets if target.node_id == node_id
    ]


def _connect_rank(explicit_connects: Sequence[_ConnectCall]) -> dict[tuple[str, str], list[int]]:
    """Each connected pair's ``connect()`` calls, by their place in the file."""
    ranks: dict[tuple[str, str], list[int]] = {}
    for position, (source, target, _source_port, _target_port) in enumerate(explicit_connects):
        ranks.setdefault((source, target), []).append(position)
    return ranks


def _bind_graph_edges(
    graph: PipelineGraph,
    node_parameters: Mapping[str, Sequence[str]],
    *,
    explicit_connects: Sequence[_ConnectCall],
    input_ports: Sequence[SubmodelInputPort] = (),
    source_file: str | None = None,
) -> list[GraphEdge]:
    """Bind each listed node's parameters to its inputs; the edges in the bound order.

    A node's stated inputs are taken in the order of their ``connect()`` calls,
    which a submodel merge does not keep in the graph's edge list; its port
    inputs and the inputs its parameters imply follow. Each node's edges keep
    the places its edges held, filled in the order its parameters read them.
    """
    ranks = _connect_rank(explicit_connects)
    edges = list(graph.edges)
    places: dict[str, list[int]] = {}
    for place, edge in enumerate(edges):
        places.setdefault(edge.target, []).append(place)
    node_map = graph.node_map
    for node_id, positional in node_parameters.items():
        node = node_map.get(node_id)
        if node is None:
            continue
        bindings = incoming_edge_bindings(graph, node_id)
        seen: dict[tuple[str, str], int] = {}
        stated: list[tuple[int, GraphEdge, str]] = []
        implied: list[tuple[GraphEdge, str]] = []
        for edge, name in bindings:
            pair = (edge.source, edge.target)
            calls = ranks.get(pair, [])
            occurrence = seen.get(pair, 0)
            seen[pair] = occurrence + 1
            if occurrence < len(calls):
                stated.append((calls[occurrence], edge, name))
            else:
                implied.append((edge, name))
        stated.sort(key=lambda item: item[0])
        ports = _port_bound_inputs(input_ports, node_id)
        ordered_edges = [edge for _rank, edge, _name in stated] + [edge for edge, _ in implied]
        inputs = [
            *(NodeInput(name, None, stated=False) for name in ports),
            *(NodeInput(name, edge.targetHandle) for _rank, edge, name in stated),
            *(NodeInput(name, edge.targetHandle, stated=False) for edge, name in implied),
        ]
        order = bind_node_inputs(
            node_id=node_id,
            node_type=node.data.nodeType,
            positional=positional,
            inputs=inputs,
            input_mapping=_declared_input_mapping(node.data.config),
            source_file=source_file,
        )
        bound = [ordered_edges[index - len(ports)] for index in order if index >= len(ports)]
        for place, edge in zip(places.get(node_id, []), bound, strict=True):
            edges[place] = edge
    return edges


def bind_parameters(
    graph: PipelineGraph,
    raw_nodes: Sequence[Mapping[str, Any]],
    explicit_connects: Sequence[_ConnectCall],
) -> PipelineGraph:
    """Refuse a parent node whose parameters do not bind; *graph* with its edges bound.

    Root nodes are checked against the hierarchical graph (occurrence outputs,
    API frames and ordinary sources all contribute their executable input
    names through :func:`incoming_edge_bindings`). Definition nodes were
    checked when their file was parsed (:func:`bind_definition_parameters`).
    Keyword-only parameters are configuration, not edges, and are ignored.
    """
    node_parameters = {
        str(raw_node["func_name"]): [str(name) for name in raw_node["edge_param_names"]]
        for raw_node in raw_nodes
    }
    edges = _bind_graph_edges(graph, node_parameters, explicit_connects=explicit_connects)
    if edges == graph.edges:
        return graph
    return graph.model_copy(update={"edges": edges})


def assert_request_frames_named(graph: PipelineGraph, *, source_file: str | None = None) -> None:
    """Refuse an edge out of a Quote or Workbench Input that names none of its tables.

    Such an input emits one frame per table, so each edge out of it takes the
    table its ``connect()`` names in ``source_port``, or the one its parameter
    is named after. Refused here, the failure is the connection's, which the
    editor offers to remove, rather than an error the whole document fails on.
    """
    node_types = {node.id: str(node.data.nodeType) for node in graph.nodes}
    missing = [
        f"{edge.source} -> {edge.target}"
        for edge in graph.edges
        if node_types.get(edge.source) in REQUEST_INPUT_KINDS and edge.sourceHandle is None
    ]
    if missing:
        located = {} if source_file is None else {"source_file": source_file}
        raise ParseError(
            "An input from a Quote or Workbench Input does not name the table it takes.",
            frame_missing_edges=missing,
            remediation=(
                "Name the table in the connect()'s source_port, or name the parameter after "
                "the table."
            ),
            **located,
        )


def bind_definition_parameters(
    graph: PipelineGraph,
    input_ports: Sequence[SubmodelInputPort],
    explicit_connects: Sequence[_ConnectCall],
    *,
    source_file: str,
) -> PipelineGraph:
    """Refuse a submodel file's node whose parameters do not bind; *graph* with its edges bound.

    Checked against the definition's own graph plus the public input ports
    that target each node, when the file is parsed, so the failure is the
    submodel's own and names its file.
    """
    edges = _bind_graph_edges(
        graph,
        graph._parser_edge_parameter_names,
        explicit_connects=explicit_connects,
        input_ports=input_ports,
        source_file=source_file,
    )
    if edges == graph.edges:
        return graph
    return graph.model_copy(update={"edges": edges})
