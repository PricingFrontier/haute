"""Strict parameter binding for parsed Polars nodes (F13).

A Polars node's positional parameters are its executable inputs. The parser
never infers a binding: each parameter must name a connected input exactly
(after the node's declared ``inputMapping``), every connected input must be
consumed by one parameter, and any other shape is a ``ParseError`` that the
editor surfaces as a degraded document, so nothing is guessed at execution
time and no save can rewrite the authored signature.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from haute._graph_utils import incoming_edge_bindings
from haute._types import NodeType, PipelineGraph, SubmodelDefinition
from haute.errors import ParseError

_REMEDIATION_MESSAGE = (
    "Name each parameter after the node or frame connected to it, "
    "or declare inputMapping={logical: connected} on the decorator."
)


def _validate_node_parameters(
    *,
    node_id: str,
    authored_params: Sequence[str],
    connected_inputs: Sequence[str],
    input_mapping: Mapping[str, str] | None,
) -> None:
    """Raise unless the authored parameters and the connected inputs coincide.

    ``input_mapping`` translates an authored (logical) parameter name to the
    connected input it stands for; a parameter without an entry must equal a
    connected input itself. A parameter that resolves to an input another
    parameter already consumed counts as unbound.
    """
    available = set(connected_inputs)
    consumed: list[str] = []
    unbound_parameters: list[str] = []
    for param in authored_params:
        resolved = input_mapping.get(param, param) if input_mapping is not None else param
        if resolved in available and resolved not in consumed:
            consumed.append(resolved)
        else:
            unbound_parameters.append(param)

    remaining = list(consumed)
    unconsumed_inputs: list[str] = []
    for name in connected_inputs:
        if name in remaining:
            remaining.remove(name)
        else:
            unconsumed_inputs.append(name)

    if unbound_parameters or unconsumed_inputs:
        raise ParseError(
            "Pipeline function parameters do not match the node's connected inputs.",
            node_id=node_id,
            unbound_parameters=unbound_parameters,
            unconsumed_inputs=unconsumed_inputs,
            connected_inputs=list(connected_inputs),
            remediation=_REMEDIATION_MESSAGE,
        )


def _declared_input_mapping(config: Mapping[str, Any]) -> Mapping[str, str] | None:
    """The node's logical-to-connected mapping, or None for instances and unmapped nodes."""
    if config.get("instanceOf"):
        # An instance signature is already physical; ``inputMapping`` there
        # describes the referenced original, not this node's own binding.
        return None
    mapping = config.get("inputMapping")
    return mapping if isinstance(mapping, Mapping) else None


def _port_bound_inputs(definition: SubmodelDefinition, node_id: str) -> list[str]:
    """Inputs a definition node receives through public input ports.

    Flattening binds a port-targeted node to the public input port name,
    so that port name is the name the authored parameter must carry.
    """
    return [
        port.name
        for port in definition.input_ports
        for target in port.targets
        if target.node_id == node_id
    ]


def assert_polars_parameters_bound(
    graph: PipelineGraph,
    raw_nodes: Sequence[Mapping[str, Any]],
) -> None:
    """Reject any Polars node whose positional parameters do not equal its inputs.

    Root nodes are checked against the hierarchical graph (occurrence outputs,
    API frames and ordinary sources all contribute their executable input
    names through :func:`incoming_edge_bindings`). Definition nodes are checked
    against their definition graph plus the public input ports that target
    them. Non-Polars node types have no authored signature and are skipped;
    keyword-only parameters are configuration, not edges, and are ignored.
    """
    node_map = graph.node_map
    for raw_node in raw_nodes:
        if raw_node.get("node_type") != NodeType.POLARS:
            continue
        node_id = str(raw_node["func_name"])
        graph_node = node_map.get(node_id)
        config: Mapping[str, Any] = (
            graph_node.data.config if graph_node is not None else raw_node.get("config", {})
        )
        authored = [
            str(name) for name in raw_node.get("edge_param_names", raw_node.get("param_names", ()))
        ]
        connected = [name for _edge, name in incoming_edge_bindings(graph, node_id)]
        _validate_node_parameters(
            node_id=node_id,
            authored_params=authored,
            connected_inputs=connected,
            input_mapping=_declared_input_mapping(config),
        )

    for definition in (graph.submodels or {}).values():
        child_graph = definition.graph
        edge_params = child_graph._parser_edge_parameter_names
        for child_node in child_graph.nodes:
            if child_node.data.nodeType != NodeType.POLARS:
                continue
            connected = [
                *_port_bound_inputs(definition, child_node.id),
                *(name for _edge, name in incoming_edge_bindings(child_graph, child_node.id)),
            ]
            _validate_node_parameters(
                node_id=child_node.id,
                authored_params=edge_params.get(child_node.id, []),
                connected_inputs=connected,
                input_mapping=_declared_input_mapping(child_node.data.config),
            )
