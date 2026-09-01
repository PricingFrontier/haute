"""The provider-wire vocabulary for graph edits.

This module deliberately imports nothing from the assistant package so that
every other assistant module can depend on the operation models with an
ordinary top-level import.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Annotated, Any, Literal, NoReturn, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
)

from haute._types import NodeType
from haute.errors import HauteError


class OpValidationError(HauteError):
    """Raised when an operation cannot be parsed or applied to a graph."""


class _OpModel(BaseModel):
    """Shared wire-model policy for graph operations."""

    model_config = ConfigDict(extra="forbid")


def _reject_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value


_NODE_REFERENCE_DESCRIPTION = "Node id, or a batch-local $ref declared by an earlier add_node."
_SOURCE_HANDLE_DESCRIPTION = (
    "Output port on the source node, exactly as get_pipeline reports it under "
    "an edge's 'source_handle'. Required only when the source emits several "
    "frames, such as an apiInput table; omit it for an ordinary single-output "
    "node."
)
_TARGET_HANDLE_DESCRIPTION = (
    "Input port on the target node, exactly as get_pipeline reports it under "
    "an edge's 'target_handle'. Only nodes with named input roles use it: an "
    "edgeJoin requires 'base' or 'join'. Omit it for an ordinary node such as "
    "polars, which binds inputs by source name and has no input ports."
)


class AddNodeOp(_OpModel):
    op: Literal["add_node"] = "add_node"
    node_type: NodeType
    name: str
    config: dict[str, Any] = Field(default_factory=dict)
    ref: str | None = Field(
        default=None,
        description=(
            "Declares a batch-local handle for this new node. Write the bare "
            "name here without a leading '$' (for example \"agg\"); later "
            'operations in the same batch address it as "$agg".'
        ),
    )

    _name_not_blank = field_validator("name")(_reject_blank)

    @field_validator("ref")
    @classmethod
    def _ref_not_blank(cls, value: str | None) -> str | None:
        if value is not None:
            _reject_blank(value)
            if value.startswith("$"):
                raise ValueError("must not start with '$'")
        return value


class UpdateNodeOp(_OpModel):
    op: Literal["update_node"] = "update_node"
    node: str = Field(description=_NODE_REFERENCE_DESCRIPTION)
    config: dict[str, Any]

    _node_not_blank = field_validator("node")(_reject_blank)


class RenameNodeOp(_OpModel):
    op: Literal["rename_node"] = "rename_node"
    node: str = Field(description=_NODE_REFERENCE_DESCRIPTION)
    new_name: str

    _node_not_blank = field_validator("node")(_reject_blank)
    _new_name_not_blank = field_validator("new_name")(_reject_blank)


class DeleteNodeOp(_OpModel):
    op: Literal["delete_node"] = "delete_node"
    node: str = Field(description=_NODE_REFERENCE_DESCRIPTION)

    _node_not_blank = field_validator("node")(_reject_blank)


class AddEdgeOp(_OpModel):
    op: Literal["add_edge"] = "add_edge"
    source: str = Field(description=_NODE_REFERENCE_DESCRIPTION)
    target: str = Field(description=_NODE_REFERENCE_DESCRIPTION)
    source_handle: str | None = Field(
        default=None,
        description=_SOURCE_HANDLE_DESCRIPTION,
    )
    target_handle: str | None = Field(
        default=None,
        description=_TARGET_HANDLE_DESCRIPTION,
    )

    _source_not_blank = field_validator("source")(_reject_blank)
    _target_not_blank = field_validator("target")(_reject_blank)

    @field_validator("source_handle", "target_handle")
    @classmethod
    def _handles_not_blank(cls, value: str | None) -> str | None:
        if value is not None:
            _reject_blank(value)
        return value


class DeleteEdgeOp(_OpModel):
    op: Literal["delete_edge"] = "delete_edge"
    source: str = Field(description=_NODE_REFERENCE_DESCRIPTION)
    target: str = Field(description=_NODE_REFERENCE_DESCRIPTION)
    source_handle: str | None = Field(
        default=None,
        description=_SOURCE_HANDLE_DESCRIPTION,
    )
    target_handle: str | None = Field(
        default=None,
        description=_TARGET_HANDLE_DESCRIPTION,
    )

    _source_not_blank = field_validator("source")(_reject_blank)
    _target_not_blank = field_validator("target")(_reject_blank)

    @field_validator("source_handle", "target_handle")
    @classmethod
    def _handles_not_blank(cls, value: str | None) -> str | None:
        if value is not None:
            _reject_blank(value)
        return value


class UpdatePreambleOp(_OpModel):
    op: Literal["update_preamble"] = "update_preamble"
    preamble: str | None


GraphEditOp: TypeAlias = Annotated[
    AddNodeOp
    | UpdateNodeOp
    | RenameNodeOp
    | DeleteNodeOp
    | AddEdgeOp
    | DeleteEdgeOp
    | UpdatePreambleOp,
    Field(discriminator="op"),
]

_OP_ADAPTER: TypeAdapter[GraphEditOp] = TypeAdapter(GraphEditOp)

_OPERATION_MODELS = (
    AddNodeOp,
    UpdateNodeOp,
    RenameNodeOp,
    DeleteNodeOp,
    AddEdgeOp,
    DeleteEdgeOp,
    UpdatePreambleOp,
)


def _inline_local_references(schema: object, definitions: Mapping[str, object]) -> object:
    """Expand the local definitions emitted by Pydantic's JSON-schema generator."""

    if isinstance(schema, list):
        return [_inline_local_references(item, definitions) for item in schema]
    if not isinstance(schema, dict):
        return schema
    if "$ref" in schema:
        if set(schema) != {"$ref"}:
            raise RuntimeError("Pydantic emitted a $ref with unsupported sibling keywords")
        reference = schema["$ref"]
        if not isinstance(reference, str) or not reference.startswith("#/$defs/"):
            raise RuntimeError(f"Pydantic emitted an unsupported schema reference: {reference!r}")
        definition_name = reference.removeprefix("#/$defs/")
        if not definition_name or "/" in definition_name or definition_name not in definitions:
            raise RuntimeError(f"Pydantic emitted an unknown local schema reference: {reference!r}")
        return _inline_local_references(deepcopy(definitions[definition_name]), definitions)
    return {key: _inline_local_references(value, definitions) for key, value in schema.items()}


def _operation_schema(model: type[_OpModel]) -> dict[str, object]:
    """Derive one closed provider-schema branch from its canonical wire model."""

    generated = model.model_json_schema()
    definitions = generated.pop("$defs", {})
    if not isinstance(definitions, Mapping):
        raise RuntimeError(f"{model.__name__} emitted non-object $defs")
    branch = _inline_local_references(generated, definitions)
    if not isinstance(branch, dict):
        raise RuntimeError(f"{model.__name__} did not emit an object schema")

    properties = branch.get("properties")
    if not isinstance(properties, dict) or set(properties) != set(model.model_fields):
        raise RuntimeError(f"{model.__name__} properties do not match its wire fields")
    if branch.get("type") != "object" or branch.get("additionalProperties") is not False:
        raise RuntimeError(f"{model.__name__} did not emit a closed object schema")

    op_field = properties.get("op")
    discriminator = model.model_fields["op"].default
    if not isinstance(op_field, dict) or op_field.get("const") != discriminator:
        raise RuntimeError(f"{model.__name__} did not emit its operation discriminator")

    branch["required"] = [
        "op",
        *(name for name, field in model.model_fields.items() if field.is_required()),
    ]
    return branch


def graph_edit_operations_schema() -> dict[str, object]:
    """Return the provider's bounded graph-operation batch schema."""

    branches = [_operation_schema(model) for model in _OPERATION_MODELS]
    add_node_properties = branches[0]["properties"]
    if not isinstance(add_node_properties, Mapping):
        raise RuntimeError("AddNodeOp schema is missing properties")
    node_type_schema = add_node_properties.get("node_type")
    expected_node_types = [node_type.value for node_type in NodeType]
    if (
        not isinstance(node_type_schema, Mapping)
        or node_type_schema.get("enum") != expected_node_types
    ):
        raise RuntimeError("AddNodeOp schema does not preserve the canonical NodeType enum")
    return {
        "type": "array",
        "items": {"oneOf": branches},
        "maxItems": 100,
    }


def _invalid(message: str) -> NoReturn:
    raise OpValidationError(message)


def parse_ops(raw_ops: Sequence[Mapping[str, Any]]) -> list[GraphEditOp]:
    """Validate wire-shaped operation dictionaries.

    Parsing is intentionally separate from graph-dependent validation.  For
    example, whether a node id exists can only be checked while applying the
    ordered batch to its evolving graph.
    """

    if isinstance(raw_ops, (str, bytes)) or not isinstance(raw_ops, Sequence):
        _invalid("Graph edit operations must be a list of operation objects")
    if len(raw_ops) > 100:
        _invalid("A graph edit plan may contain at most 100 operations")

    parsed: list[GraphEditOp] = []
    for index, raw_op in enumerate(raw_ops):
        if not isinstance(raw_op, Mapping):
            _invalid(f"Operation {index} must be an object")
        try:
            parsed.append(_OP_ADAPTER.validate_python(raw_op))
        except ValidationError as exc:
            raise OpValidationError(
                f"Invalid graph edit operation at index {index}: {exc}"
            ) from exc
    return parsed


__all__ = [
    "AddEdgeOp",
    "AddNodeOp",
    "DeleteEdgeOp",
    "DeleteNodeOp",
    "GraphEditOp",
    "OpValidationError",
    "RenameNodeOp",
    "UpdateNodeOp",
    "UpdatePreambleOp",
    "graph_edit_operations_schema",
    "parse_ops",
]
