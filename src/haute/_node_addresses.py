"""Resolve the address a request names its target by, and label the keys an answer names
(specs/server-api/low-level.md, "Node addresses").

A request names a node or frame by address; once a route has flattened the document
(:func:`haute._flatten.flatten_executable_graph`), :func:`resolve_target` resolves the
address against the expansion's record (:class:`haute._types.SubmodelExpansion`), never by
building a runtime key. An answer
names nodes by runtime key, and :func:`labelled` gives it the address of every key it
names, found from the same record, never by reading a key.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from functools import cache
from typing import Any, Literal, TypeVar

from pydantic import BaseModel
from pydantic_core import to_jsonable_python

from haute._node_address_schemas import (
    RUNTIME_NODE_KEY_MARKER,
    FrameTargetPayload,
    NodeAddressEntry,
    NodeAddressPayload,
    NodeFrameTargetPayload,
    NodeTargetRequest,
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

_NO_EXPANSION = SubmodelExpansion.of({})

NodeRequestT = TypeVar("NodeRequestT", bound=NodeTargetRequest)
AnswerT = TypeVar("AnswerT", bound=BaseModel)


@dataclass(frozen=True)
class ResolvedTarget:
    """A target as the flat graph runs it: the runtime key, and the port or ports read."""

    key: str
    port_label: str | None = None
    port_labels: tuple[str, ...] | None = None


def expansion_of(flat: PipelineGraph) -> SubmodelExpansion:
    """The record of the expansion that produced *flat*; empty for a graph without
    occurrences."""
    return flat._submodel_expansion or _NO_EXPANSION


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


def resolved_request(body: NodeRequestT, flat: PipelineGraph) -> NodeRequestT:
    """*body* over the flattened document *flat*, its target resolved to the runtime key the
    services read as ``node_id``, or refused by name (404)."""
    key = resolve_node(flat, body.target.occurrence, body.target.node_id)
    return body.with_node_key(key, graph=flat)


def resolve_node(flat: PipelineGraph, occurrence_id: str | None, node_id: str) -> str:
    """The runtime key of the node at ``{occurrence_id, node_id}`` in *flat*."""
    if occurrence_id is None:
        expansion = expansion_of(flat)
        # A child's runtime key is in the flat graph too, but names no node of the
        # pipeline: a child is addressed by its occurrence and local id.
        if node_id in flat.node_map and node_id not in expansion.origins:
            return node_id
        collapsed = expansion.occurrences.get(node_id)
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


@dataclass(frozen=True)
class SpelledTarget:
    """What a name typed on the command line runs to: the runtime key, the port read (a
    submodel occurrence's output's handle), and the name its output is shown by."""

    key: str
    port_label: str | None
    shown: str


def spelled_name(flat: PipelineGraph, key: str) -> str:
    """How the command line names the node *flat* runs under *key*: a pipeline node by its
    id, a submodel occurrence's node as ``<occurrence>/<node>``, its address, never by its
    runtime key. :func:`resolve_spelled` takes every such name back."""
    origin = expansion_of(flat).origins.get(key)
    return key if origin is None else f"{origin[0]}/{origin[1]}"


def resolve_spelled(flat: PipelineGraph, spelled: str) -> SpelledTarget:
    """What *spelled*, a node's name as the command line takes it (``haute run --node``),
    runs to in *flat*, through the expansion's record as a request's address resolves.

    ``<occurrence>/<node>`` is that occurrence's node. A bare name is, in turn: a pipeline
    node by its id; a submodel occurrence by its name, as its first declared output, the
    frame the editor previews; or the node of that id in the one occurrence holding one.
    Raises :class:`NotFoundError` for a name several occurrences' nodes have, naming each
    spelling, an occurrence without outputs, naming its nodes, and a name that fits nothing,
    naming the names there are.
    """
    expansion = expansion_of(flat)
    occurrence_id, slash, local_id = spelled.rpartition("/")
    if slash:
        occurrence = expansion.occurrences.get(occurrence_id)
        if occurrence is None:
            raise NotFoundError(
                f"The pipeline has no submodel {occurrence_id!r}.{_names_there_are(flat)}"
            )
        key = occurrence.children.get(local_id)
        if key is None:
            raise NotFoundError(
                f"Submodel {occurrence_id!r} has no node {local_id!r}. "
                f"Its nodes: {_occurrence_nodes(occurrence)}."
            )
        return SpelledTarget(key, None, spelled)
    if spelled in flat.node_map and spelled not in expansion.origins:
        return SpelledTarget(spelled, None, spelled)
    occurrence = expansion.occurrences.get(spelled)
    if occurrence is not None:
        port, endpoint = next(iter(occurrence.output_ports.items()), (None, None))
        if endpoint is None:
            said = "has no outputs" if port is None else f"names no node for its output {port!r}"
            raise NotFoundError(
                f"Submodel {spelled!r} {said} to run to. "
                f"Name one of its nodes: {_occurrence_nodes(occurrence)}."
            )
        return SpelledTarget(endpoint.node_id, endpoint.handle_id, f"{spelled}[{port}]")
    holders = [held for held in expansion.occurrences.values() if spelled in held.children]
    if len(holders) == 1:
        return SpelledTarget(
            holders[0].children[spelled], None, f"{holders[0].instance_id}/{spelled}"
        )
    if holders:
        each = ", ".join(f"{held.instance_id}/{spelled}" for held in holders)
        raise NotFoundError(
            f"{spelled!r} is a node of {len(holders)} submodels: {each}. "
            "Name one as <submodel>/<node>."
        )
    raise NotFoundError(f"The pipeline has no node {spelled!r}.{_names_there_are(flat)}")


def _occurrence_nodes(occurrence: ExpandedOccurrence) -> str:
    return ", ".join(f"{occurrence.instance_id}/{local}" for local in occurrence.children)


def _names_there_are(flat: PipelineGraph) -> str:
    """The names a command line can run to in *flat*, as a sentence or two to append."""
    expansion = expansion_of(flat)
    nodes = [spelled_name(flat, node.id) for node in flat.nodes]
    said = f" Its nodes: {', '.join(nodes)}." if nodes else ""
    if expansion.occurrences:
        said += f" Its submodels: {', '.join(expansion.occurrences)}."
    return said


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


def display_name(label: str, occurrence_label: str | None) -> str:
    """A node's name in a message: its authored *label*, and a submodel child's with its
    occurrence's ("Banding 1 in pricing")."""
    return label if occurrence_label is None else f"{label} in {occurrence_label}"


def node_display_name(flat: PipelineGraph, key: str) -> str:
    """How a message names the node *flat* runs under *key* (:func:`display_name`), never by
    its key. A key *flat* does not hold is named as it is, so a message about a broken answer
    still reads."""
    try:
        entry = address_entry(flat, key)
    except ValueError:
        return key
    if entry.occurrence is None:
        return display_name(entry.label, None)
    occurrence = expansion_of(flat).occurrences.get(entry.occurrence)
    return display_name(
        entry.label, occurrence.label if occurrence is not None else entry.occurrence
    )


def node_reference(flat: PipelineGraph, key: str) -> dict[str, str | None]:
    """A node an error names, as its payload carries it: its key, address and label."""
    entry = address_entry(flat, key)
    return {
        "key": key,
        "occurrence": entry.occurrence,
        "node_id": entry.node_id,
        "label": entry.label,
    }


def node_addresses(flat: PipelineGraph, keys: Iterable[str]) -> dict[str, NodeAddressEntry]:
    """The address entry of each of *keys*, sorted by key so an answer is stable."""
    return {key: address_entry(flat, key) for key in sorted(set(keys))}


def occurrence_ports(flat: PipelineGraph) -> list[OccurrencePortProvenance]:
    """Frame provenance: every declared port of every occurrence *flat* expanded, by instance
    id and then in declared order, with the endpoint it resolves to (``None`` for an input
    nothing feeds). Never refuses."""
    provenance: list[OccurrencePortProvenance] = []
    # By instance id, so an answer lists them alike however the expansion ran.
    occurrences = sorted(expansion_of(flat).occurrences.values(), key=lambda o: o.instance_id)
    for occurrence in occurrences:
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
    """Every runtime key *data*, a JSON form of an *answer_type*, names, but for those a
    nested part addressing its own keys names (the execution metrics, a data point)."""
    schema = _schema(answer_type)
    return set(_keys_at(data, schema, schema.get("$defs", {}), top=True))


@cache
def _schema(answer_type: type[BaseModel]) -> dict[str, Any]:
    return answer_type.model_json_schema(by_alias=True)


def _keys_at(
    value: Any, schema: Mapping[str, Any], defs: Mapping[str, Any], *, top: bool = False
) -> Iterator[str]:
    while "$ref" in schema:
        schema = defs[str(schema["$ref"]).rsplit("/", 1)[-1]]
    if not top and "node_addresses" in schema.get("properties", {}):
        # A part that addresses the keys it names answers for them itself.
        return
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
