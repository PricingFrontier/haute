"""Wire shapes of node addresses: request targets, the opaque runtime key an answer names a
node by, and the address an answer gives each key (specs/server-api/low-level.md, "Node
addresses").

Kept apart from :mod:`haute.schemas` so the execution-metrics payloads, which
:mod:`haute.schemas` imports, can name nodes by key too.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

#: The runtime key a flat graph runs a node under: a pipeline node's id, or a child's
#: ``submodel_runtime/<occurrence>/<child>``. Opaque to the browser, which reads it only
#: through the answer's ``node_addresses``; the generated TypeScript brands it.
RUNTIME_NODE_KEY_MARKER = "x-haute-node-key"
RuntimeNodeKey = Annotated[str, Field(json_schema_extra={RUNTIME_NODE_KEY_MARKER: True})]

_Name = Annotated[str, Field(min_length=1, max_length=512)]


class NodeAddressPayload(BaseModel):
    """A node by address: a pipeline node (``occurrence`` null; an occurrence is itself the
    pipeline's node of its instance id), or a child of ``occurrence`` by its local id."""

    model_config = ConfigDict(extra="forbid")

    occurrence: _Name | None
    node_id: _Name


class NodeTargetRequest(BaseModel):
    """A request that runs one node, named by address in ``target``; a field it does not
    define is refused. The route resolves the address in the flattened document
    (:func:`haute._node_addresses.resolved_request`), and the services read the runtime key
    the node runs under as :attr:`node_id`, which no request sends."""

    model_config = ConfigDict(extra="forbid")

    target: NodeAddressPayload
    _node_key: str | None = PrivateAttr(default=None)

    @property
    def node_id(self) -> str:
        """The runtime key of the node ``target`` names, once the route resolved it."""
        if self._node_key is None:
            raise RuntimeError("The request's target has not been resolved")
        return self._node_key

    def with_node_key(self, key: str, **update: Any) -> Self:
        """A copy whose target resolved to *key*, with *update* applied."""
        resolved = self.model_copy(update=update)
        resolved._node_key = key
        return resolved


class NodeFrameTargetPayload(BaseModel):
    """A node's output frame: the whole of it, one port, or several ports read in one
    execution (the Workbench's tables), never one port and several."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["node"]
    occurrence: _Name | None
    node_id: _Name
    port_label: _Name | None
    port_labels: Annotated[list[_Name], Field(min_length=1, max_length=1000)] | None

    @model_validator(mode="after")
    def _one_port_or_several(self) -> NodeFrameTargetPayload:
        if self.port_label is not None and self.port_labels is not None:
            raise ValueError("port_label and port_labels cannot both be set")
        return self


class OccurrenceInputTargetPayload(BaseModel):
    """The frame feeding a public input port of an occurrence (its Input card's)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["occurrence_input"]
    occurrence: _Name
    port: _Name


class OccurrenceOutputTargetPayload(BaseModel):
    """The frame a public output port of an occurrence produces (its Output card's, or an
    edge out of the collapsed occurrence)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["occurrence_output"]
    occurrence: _Name
    port: _Name


FrameTargetPayload = Annotated[
    NodeFrameTargetPayload | OccurrenceInputTargetPayload | OccurrenceOutputTargetPayload,
    Field(discriminator="kind"),
]


class NodeAddressEntry(BaseModel):
    """The address of a key an answer names, with the node's authored label."""

    model_config = ConfigDict(extra="forbid")

    occurrence: str | None
    node_id: str
    label: str


class RuntimeEndpointPayload(BaseModel):
    """A node and output handle of the executed graph."""

    model_config = ConfigDict(extra="forbid")

    key: RuntimeNodeKey
    handle: str | None


class OccurrencePortProvenance(BaseModel):
    """What one declared port of an occurrence resolves to in the executed graph: the
    endpoint feeding an input, or producing an output; ``None`` for an input nothing
    feeds."""

    model_config = ConfigDict(extra="forbid")

    occurrence: str
    direction: Literal["input", "output"]
    port: str
    endpoint: RuntimeEndpointPayload | None
