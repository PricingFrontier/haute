"""Resolve the address a request names its target by, and label the keys an answer names
(specs/server-api/low-level.md, "Node addresses").

A request names a node or frame by address; :func:`prepare_execution` flattens the
document and resolves the address against the expansion's record
(:class:`haute._types.SubmodelExpansion`), never by building a runtime key. An answer
names nodes by runtime key, and :func:`labelled` gives it the address of every key it
names, found from the same record, never by reading a key.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING, Any, Literal, TypeVar

from pydantic import BaseModel
from pydantic_core import to_jsonable_python

from haute._flatten import flatten_executable_graph
from haute._node_address_schemas import (
    RUNTIME_NODE_KEY_MARKER,
    FrameTargetPayload,
    NodeAddressEntry,
    NodeAddressPayload,
    NodeFrameTargetPayload,
    OccurrenceInputTargetPayload,
    OccurrencePortProvenance,
    RuntimeEndpointPayload,
)
from haute._types import (
    ExpandedOccurrence,
    PipelineGraph,
    SubmodelEndpoint,
    SubmodelExpansion,
)
from haute.errors import NotFoundError

if TYPE_CHECKING:
    from haute._workbench_form import Cell, FormSpec

_NO_EXPANSION = SubmodelExpansion.of({})

AnswerT = TypeVar("AnswerT", bound=BaseModel)


@dataclass(frozen=True)
class ResolvedTarget:
    """A target as the flat graph runs it: the runtime key, and the port or ports read."""

    key: str
    port_label: str | None = None
    port_labels: tuple[str, ...] | None = None


@dataclass(frozen=True)
class PreparedExecution:
    """A request's document flattened, with its target resolved in it."""

    graph: PipelineGraph
    target: ResolvedTarget


def expansion_of(flat: PipelineGraph) -> SubmodelExpansion:
    """The record of the expansion that produced *flat*; empty for a graph without
    occurrences."""
    return flat._submodel_expansion or _NO_EXPANSION


def prepare_execution(
    graph: PipelineGraph,
    target: NodeAddressPayload | FrameTargetPayload,
    *,
    form: FormSpec | None = None,
    sample: Mapping[str, list[dict[str, Cell]]] | None = None,
) -> PreparedExecution:
    """Flatten *graph* as every route that runs a browser graph does
    (:func:`haute._flatten.flatten_executable_graph`) and resolve *target* in it."""
    flat = flatten_executable_graph(graph, form=form, sample=sample)
    return PreparedExecution(graph=flat, target=resolve_target(flat, target))


def resolve_target(
    flat: PipelineGraph,
    target: NodeAddressPayload | FrameTargetPayload,
) -> ResolvedTarget:
    """The runtime key and port(s) *target* names in *flat*, or a 404 naming what is
    missing. A card's port resolves through the expansion's endpoint, its handle with it."""
    if isinstance(target, NodeAddressPayload):
        return ResolvedTarget(key=resolve_node(flat, target.occurrence, target.node_id))
    if isinstance(target, NodeFrameTargetPayload):
        return ResolvedTarget(
            key=resolve_node(flat, target.occurrence, target.node_id),
            port_label=target.port_label,
            port_labels=None if target.port_labels is None else tuple(target.port_labels),
        )
    occurrence = _occurrence(flat, target.occurrence)
    if isinstance(target, OccurrenceInputTargetPayload):
        if target.port not in occurrence.input_ports:
            raise NotFoundError(f"Submodel '{occurrence.label}' has no input '{target.port}'")
        endpoint = occurrence.input_ports[target.port]
        if endpoint is None:
            raise NotFoundError(
                f"Input '{target.port}' of submodel '{occurrence.label}' is not connected"
            )
    else:
        if target.port not in occurrence.output_ports:
            raise NotFoundError(f"Submodel '{occurrence.label}' has no output '{target.port}'")
        endpoint = occurrence.output_ports[target.port]
        if endpoint is None:
            raise NotFoundError(
                f"Output '{target.port}' of submodel '{occurrence.label}' names no node"
            )
    return ResolvedTarget(key=endpoint.node_id, port_label=endpoint.handle_id)


def resolve_node(flat: PipelineGraph, occurrence_id: str | None, node_id: str) -> str:
    """The runtime key of the node at ``{occurrence_id, node_id}`` in *flat*."""
    if occurrence_id is None:
        if node_id in flat.node_map:
            return node_id
        collapsed = expansion_of(flat).occurrences.get(node_id)
        if collapsed is not None:
            raise NotFoundError(
                f"'{collapsed.label}' is a submodel: name one of its nodes or ports"
            )
        raise NotFoundError(f"Node '{node_id}' not found")
    occurrence = _occurrence(flat, occurrence_id)
    key = occurrence.children.get(node_id)
    if key is None:
        raise NotFoundError(f"Submodel '{occurrence.label}' has no node '{node_id}'")
    return key


def _occurrence(flat: PipelineGraph, occurrence_id: str) -> ExpandedOccurrence:
    occurrence = expansion_of(flat).occurrences.get(occurrence_id)
    if occurrence is None:
        raise NotFoundError(f"Submodel '{occurrence_id}' not found")
    return occurrence


def address_entry(flat: PipelineGraph, key: str) -> NodeAddressEntry:
    """The address and authored label of the node *flat* runs under *key*: a child of an
    occurrence, a collapsed occurrence's own key, or a pipeline node. A key *flat* does
    not hold is a defect of the answer naming it, and raises."""
    expansion = expansion_of(flat)
    origin = expansion.origins.get(key)
    if origin is not None:
        occurrence_id, local_id = origin
        occurrence = expansion.occurrences[occurrence_id]
        return NodeAddressEntry(
            occurrence=occurrence_id, node_id=local_id, label=occurrence.child_labels[local_id]
        )
    collapsed = expansion.occurrences.get(key)
    if collapsed is not None:
        return NodeAddressEntry(occurrence=None, node_id=key, label=collapsed.label)
    node = flat.node_map.get(key)
    if node is None:
        raise ValueError(f"An answer names {key!r}, which the executed graph does not hold")
    return NodeAddressEntry(occurrence=None, node_id=key, label=node.data.label)


def node_addresses(flat: PipelineGraph, keys: Iterable[str]) -> dict[str, NodeAddressEntry]:
    """The address entry of each of *keys*, sorted by key so an answer is stable."""
    return {key: address_entry(flat, key) for key in sorted(set(keys))}


def occurrence_ports(flat: PipelineGraph) -> list[OccurrencePortProvenance]:
    """Frame provenance: every declared port of every occurrence *flat* expanded, with the
    endpoint it resolves to (``None`` for an input nothing feeds). Never refuses."""
    provenance: list[OccurrencePortProvenance] = []
    for occurrence in expansion_of(flat).occurrences.values():
        sides: tuple[tuple[Literal["input", "output"], Mapping[str, SubmodelEndpoint | None]], ...]
        sides = (("input", occurrence.input_ports), ("output", occurrence.output_ports))
        for direction, ports in sides:
            provenance.extend(
                OccurrencePortProvenance(
                    occurrence=occurrence.instance_id,
                    direction=direction,
                    port=port,
                    endpoint=None
                    if endpoint is None
                    else RuntimeEndpointPayload(key=endpoint.node_id, handle=endpoint.handle_id),
                )
                for port, endpoint in ports.items()
            )
    return provenance


def labelled(answer_type: type[AnswerT], flat: PipelineGraph, /, **fields: Any) -> AnswerT:
    """*answer_type* built from *fields*, with ``node_addresses`` addressing every key the
    answer names: every value at a :data:`RuntimeNodeKey` position of its schema."""
    named = named_keys(answer_type, to_jsonable_python(fields, by_alias=True))
    return answer_type(**fields, node_addresses=node_addresses(flat, named))


def named_keys(answer_type: type[BaseModel], data: Any) -> set[str]:
    """Every runtime key *data*, a JSON form of an *answer_type*, names."""
    schema = _schema(answer_type)
    return set(_keys_at(data, schema, schema.get("$defs", {})))


@cache
def _schema(answer_type: type[BaseModel]) -> dict[str, Any]:
    return answer_type.model_json_schema(by_alias=True)


def _keys_at(value: Any, schema: Mapping[str, Any], defs: Mapping[str, Any]) -> Iterator[str]:
    while "$ref" in schema:
        schema = defs[str(schema["$ref"]).rsplit("/", 1)[-1]]
    if schema.get(RUNTIME_NODE_KEY_MARKER) and isinstance(value, str):
        yield value
        return
    for branch in (*schema.get("anyOf", ()), *schema.get("oneOf", ()), *schema.get("allOf", ())):
        yield from _keys_at(value, branch, defs)
    if isinstance(value, dict):
        if schema.get("propertyNames", {}).get(RUNTIME_NODE_KEY_MARKER):
            yield from (key for key in value if isinstance(key, str))
        properties = schema.get("properties", {})
        for name, item in value.items():
            if name in properties:
                yield from _keys_at(item, properties[name], defs)
            elif isinstance(schema.get("additionalProperties"), Mapping):
                yield from _keys_at(item, schema["additionalProperties"], defs)
    elif isinstance(value, list) and isinstance(schema.get("items"), Mapping):
        for item in value:
            yield from _keys_at(item, schema["items"], defs)
