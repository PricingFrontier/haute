"""The provider-wire vocabulary for graph edits.

This module deliberately imports nothing from the assistant package so that
every other assistant module can depend on the operation models with an
ordinary top-level import.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
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


class AddNodeOp(_OpModel):
    op: Literal["add_node"] = "add_node"
    node_type: NodeType
    name: str
    config: dict[str, Any] = Field(default_factory=dict)
    ref: str | None = None

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
    node: str
    config: dict[str, Any]

    _node_not_blank = field_validator("node")(_reject_blank)


class RenameNodeOp(_OpModel):
    op: Literal["rename_node"] = "rename_node"
    node: str
    new_name: str

    _node_not_blank = field_validator("node")(_reject_blank)
    _new_name_not_blank = field_validator("new_name")(_reject_blank)


class DeleteNodeOp(_OpModel):
    op: Literal["delete_node"] = "delete_node"
    node: str

    _node_not_blank = field_validator("node")(_reject_blank)


class AddEdgeOp(_OpModel):
    op: Literal["add_edge"] = "add_edge"
    source: str
    target: str
    source_handle: str | None = None
    target_handle: str | None = None

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
    source: str
    target: str
    source_handle: str | None = None
    target_handle: str | None = None

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
    "parse_ops",
]
