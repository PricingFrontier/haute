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

from haute._types import NodeType, PipelineGraph
from haute.errors import HauteError


class LocatedPlanError(HauteError):
    """A plan failure that says where it happened and how to correct it.

    ``where`` holds any of ``op_index``, ``node``, ``field`` and ``step``;
    ``fix`` is one concrete correction; ``graph`` is the graph the failure was
    judged against, from which the tool boundary resolves the located node's
    input columns. The operation layer stamps ``op_index`` and ``graph`` when
    the raise site cannot know them. ``did_you_mean`` holds close names the
    failure itself found, such as node ids near an unknown node reference.
    """

    def __init__(
        self,
        message: str,
        *,
        where: Mapping[str, object] | None = None,
        fix: str | None = None,
        did_you_mean: Sequence[str] = (),
    ) -> None:
        super().__init__(message)
        self.where: dict[str, object] = dict(where or {})
        self.fix = fix
        self.did_you_mean: tuple[str, ...] = tuple(did_you_mean)
        self.graph: PipelineGraph | None = None


class OpValidationError(LocatedPlanError):
    """Raised when an operation cannot be parsed or applied to a graph.

    Every such failure is one the model can correct, so ``fix`` is required.
    """

    def __init__(
        self,
        message: str,
        *,
        fix: str,
        where: Mapping[str, object] | None = None,
        did_you_mean: Sequence[str] = (),
    ) -> None:
        super().__init__(message, where=where, fix=fix, did_you_mean=did_you_mean)


class _OpModel(BaseModel):
    """Shared wire-model policy for graph operations."""

    model_config = ConfigDict(extra="forbid")


def _reject_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value


#: Operations one plan may hold. Each adds or updates at most one node, so a
#: plan also seals at most this many ``node_config`` postconditions.
MAX_PLAN_OPERATIONS = 100
#: Postconditions a caller (the model or a recipe) may declare for one plan.
MAX_DECLARED_POSTCONDITIONS = 100

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
    "polars, which names each input after its incoming edge and has no input ports."
)
_NODE_TYPE_DESCRIPTION = "Node type id, exactly as the prompt's node index lists it."
# The compatible provider projection flattens the operation union into one
# object and joins distinct descriptions, so each text names its operation.
_ADD_CONFIG_DESCRIPTION = (
    "Config keys, as the node type's descriptor config schema lists them. "
    "add_node starts the node from the palette's config for its type and writes "
    "these keys over it."
)
_UPDATE_CONFIG_DESCRIPTION = (
    "update_node merges shallowly: each key written replaces that key's whole "
    "value, so send a nested object or list complete; keys not written keep their "
    "saved values; a JSON null removes the key."
)


class AddNodeOp(_OpModel):
    op: Literal["add_node"] = "add_node"
    node_type: NodeType = Field(description=_NODE_TYPE_DESCRIPTION)
    name: str = Field(
        description=(
            "add_node: the new node's name, which becomes its id. When the analyst names "
            'the node ("add X", "as X", "called X", "named X"), use exactly that name, '
            "with no suffix such as _node or _input."
        )
    )
    config: dict[str, Any] = Field(default_factory=dict, description=_ADD_CONFIG_DESCRIPTION)
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
    config: dict[str, Any] = Field(description=_UPDATE_CONFIG_DESCRIPTION)

    _node_not_blank = field_validator("node")(_reject_blank)


_EDIT_STEP_DESCRIPTION = (
    'edit_steps: the whole step, in the shape the step_grammar of the "guide" '
    "reference gives its kind. Omit id to have one assigned: a replacement keeps the id "
    "it replaces, an insertion gets <kind>_<n>."
)


class StepInsert(_OpModel):
    insert_after: str | None = Field(
        description=(
            "edit_steps: the id of the step the new step follows; null inserts it at the start."
        )
    )
    step: dict[str, Any] = Field(description=_EDIT_STEP_DESCRIPTION)


class StepReplace(_OpModel):
    replace: str = Field(description="edit_steps: the id of the step to replace whole.")
    step: dict[str, Any] = Field(description=_EDIT_STEP_DESCRIPTION)


class StepRemove(_OpModel):
    remove: str = Field(description="edit_steps: the id of the step to remove.")


StepEdit: TypeAlias = StepInsert | StepReplace | StepRemove


class EditStepsOp(_OpModel):
    op: Literal["edit_steps"] = "edit_steps"
    node: str = Field(description=_NODE_REFERENCE_DESCRIPTION)
    edits: list[StepEdit] = Field(
        min_length=1,
        description=(
            "edit_steps changes an existing steps list by step id: each edit inserts "
            "a step after one ({insert_after, step}), replaces one whole ({replace, "
            "step}) or removes one ({remove}), applied in order. Steps not named stay "
            "as saved, so a free-code step you cannot read is kept, replaced or "
            "removed, never edited in place."
        ),
    )

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
    | EditStepsOp
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
    EditStepsOp,
    RenameNodeOp,
    DeleteNodeOp,
    AddEdgeOp,
    DeleteEdgeOp,
    UpdatePreambleOp,
)


def _inline_local_references(schema: object, definitions: Mapping[str, object]) -> object:
    """Expand the local definitions emitted by Pydantic's JSON-schema generator.

    A field's own description, which Pydantic emits beside the ``$ref``, replaces
    the definition's: a definition's description is its Python class docstring,
    written for maintainers rather than the model.
    """

    if isinstance(schema, list):
        return [_inline_local_references(item, definitions) for item in schema]
    if not isinstance(schema, dict):
        return schema
    if "$ref" in schema:
        if not set(schema) <= {"$ref", "description"}:
            raise RuntimeError("Pydantic emitted a $ref with unsupported sibling keywords")
        reference = schema["$ref"]
        if not isinstance(reference, str) or not reference.startswith("#/$defs/"):
            raise RuntimeError(f"Pydantic emitted an unsupported schema reference: {reference!r}")
        definition_name = reference.removeprefix("#/$defs/")
        if not definition_name or "/" in definition_name or definition_name not in definitions:
            raise RuntimeError(f"Pydantic emitted an unknown local schema reference: {reference!r}")
        inlined = _inline_local_references(deepcopy(definitions[definition_name]), definitions)
        if not isinstance(inlined, dict):
            raise RuntimeError(f"Pydantic emitted a non-object definition: {reference!r}")
        if "description" in schema:
            inlined["description"] = schema["description"]
        else:
            inlined.pop("description", None)
        return inlined
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
        "maxItems": MAX_PLAN_OPERATIONS,
    }


def _invalid(message: str, *, fix: str) -> NoReturn:
    raise OpValidationError(message, fix=fix)


def parse_ops(
    raw_ops: Sequence[Mapping[str, Any]], positions: Sequence[int] | None = None
) -> list[GraphEditOp]:
    """Validate wire-shaped operation dictionaries.

    Parsing is intentionally separate from graph-dependent validation.  For
    example, whether a node id exists can only be checked while applying the
    ordered batch to its evolving graph. *positions*, when given, holds each
    operation's index in the batch the model sent, which a failure names.
    """

    if isinstance(raw_ops, (str, bytes)) or not isinstance(raw_ops, Sequence):
        _invalid(
            "Graph edit operations must be a list of operation objects",
            fix="Send ops as a JSON array of operation objects.",
        )
    if len(raw_ops) > MAX_PLAN_OPERATIONS:
        _invalid(
            f"A graph edit plan may contain at most {MAX_PLAN_OPERATIONS} operations",
            fix=f"Split the work into plans of at most {MAX_PLAN_OPERATIONS} operations.",
        )

    parsed: list[GraphEditOp] = []
    for offset, raw_op in enumerate(raw_ops):
        index = offset if positions is None else positions[offset]
        if not isinstance(raw_op, Mapping):
            raise OpValidationError(
                f"Operation {index} must be an object",
                where={"op_index": index},
                fix=f"Send operation {index} as a JSON object with an op field.",
            )
        try:
            parsed.append(_OP_ADAPTER.validate_python(raw_op))
        except ValidationError as exc:
            raise OpValidationError(
                f"Invalid graph edit operation at index {index}: {exc}",
                where={"op_index": index},
                fix=f"Correct operation {index} against the dry_run_graph_edits schema.",
            ) from exc
    return parsed


__all__ = [
    "AddEdgeOp",
    "AddNodeOp",
    "DeleteEdgeOp",
    "DeleteNodeOp",
    "EditStepsOp",
    "GraphEditOp",
    "LocatedPlanError",
    "MAX_DECLARED_POSTCONDITIONS",
    "MAX_PLAN_OPERATIONS",
    "OpValidationError",
    "RenameNodeOp",
    "StepEdit",
    "StepInsert",
    "StepRemove",
    "StepReplace",
    "UpdateNodeOp",
    "UpdatePreambleOp",
    "graph_edit_operations_schema",
    "parse_ops",
]
