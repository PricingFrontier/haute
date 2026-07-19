"""Pure graph-edit operations used by the assistant mutation tool.

The wire models in this module deliberately know nothing about files or the
save service.  ``parse_ops`` validates the provider-shaped payload and
``apply_ops`` evaluates a parsed batch against a deep copy of a
``PipelineGraph``.  This keeps a failed batch from ever changing the graph
that the caller owns.
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

from haute._config_validation import VALID_KEYS
from haute._graph_utils import _edge_id, _sanitize_func_name
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
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
_SUBMODEL_TYPES = frozenset({NodeType.SUBMODEL, NodeType.SUBMODEL_PORT})
_X_STEP = 280.0
_Y_STEP = 120.0
_ANY_HANDLE = object()


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


def _mapping(value: object) -> Mapping[str, Any] | None:
    if isinstance(value, BaseModel):
        dumped = value.model_dump(mode="python")
        return dumped if isinstance(dumped, Mapping) else None
    return value if isinstance(value, Mapping) else None


def _nested_submodel_node_ids(submodels: object) -> set[str]:
    """Collect ids from graphs nested below ``PipelineGraph.submodels``."""

    result: set[str] = set()

    def walk(value: object) -> None:
        mapped = _mapping(value)
        if mapped is None:
            if isinstance(value, (list, tuple)):
                for item in value:
                    walk(item)
            return

        raw_nodes = mapped.get("nodes")
        if isinstance(raw_nodes, (list, tuple)):
            for raw_node in raw_nodes:
                node = _mapping(raw_node)
                if node is not None and isinstance(node.get("id"), str):
                    result.add(node["id"])

        raw_child_ids = mapped.get("childNodeIds")
        if isinstance(raw_child_ids, (list, tuple)):
            result.update(child_id for child_id in raw_child_ids if isinstance(child_id, str))

        if "graph" in mapped:
            walk(mapped["graph"])
        if "submodels" in mapped:
            walk(mapped["submodels"])

        # A submodels mapping is normally keyed by submodel name and contains
        # a ``graph`` field.  Walk other containers as well so the check also
        # handles a directly embedded graph and recursively nested models.
        if "nodes" not in mapped and "graph" not in mapped and "submodels" not in mapped:
            for item in mapped.values():
                walk(item)

    walk(submodels)
    return result


def _node_index(graph: PipelineGraph, node_id: str) -> int | None:
    for index, node in enumerate(graph.nodes):
        if node.id == node_id:
            return index
    return None


def _resolve_node_id(
    raw_id: str,
    graph: PipelineGraph,
    refs: Mapping[str, str],
    nested_ids: set[str],
    *,
    role: str,
) -> str:
    if raw_id.startswith("$"):
        ref = raw_id[1:]
        if not ref:
            _invalid(f"{role} reference '$' is empty")
        try:
            node_id = refs[ref]
        except KeyError:
            _invalid(f"Unknown batch reference {raw_id!r}")
    else:
        node_id = raw_id

    if node_id in nested_ids:
        _invalid(
            f"{role} {raw_id!r} crosses the submodel boundary; "
            "submodel-internal nodes are not editable by assistant operations"
        )

    index = _node_index(graph, node_id)
    if index is None:
        _invalid(f"Unknown {role} node {raw_id!r}")

    node_type = graph.nodes[index].data.nodeType
    if node_type in _SUBMODEL_TYPES:
        _invalid(
            f"{role} {raw_id!r} is a submodel boundary; "
            "submodel placeholders and ports are not editable by assistant operations"
        )
    return node_id


def _validate_config(
    node_type: NodeType,
    config: Mapping[str, Any],
    *,
    operation: str,
    removable_keys: set[str] | None = None,
) -> None:
    allowed = VALID_KEYS.get(node_type)
    if allowed is None:
        _invalid(f"{operation} does not support config for node type {node_type.value!r}")
    removable_keys = removable_keys or set()
    unknown = sorted(
        key
        for key, value in config.items()
        if key not in allowed and not (value is None and key in removable_keys)
    )
    if unknown:
        _invalid(
            f"{operation} contains unknown config key(s) for node type "
            f"{node_type.value!r}: {', '.join(unknown)}"
        )


def _replace_node(graph: PipelineGraph, index: int, node: GraphNode) -> None:
    graph.nodes[index] = node


def _apply_add_node(
    graph: PipelineGraph,
    op: AddNodeOp,
    refs: dict[str, str],
    new_node_ids: list[str],
) -> None:
    if op.node_type in _SUBMODEL_TYPES:
        _invalid(
            f"Cannot add node type {op.node_type.value!r}: "
            "assistant operations cannot create submodel boundaries"
        )
    _validate_config(op.node_type, op.config, operation="add_node")

    node_id = _sanitize_func_name(op.name)
    node = GraphNode(
        id=node_id,
        type=op.node_type.value,
        data=NodeData(
            label=op.name,
            nodeType=op.node_type,
            config=deepcopy(op.config),
        ),
        position={"x": 0.0, "y": 0.0},
    )
    graph.nodes.append(node)
    new_node_ids.append(node_id)

    if op.ref is not None:
        if op.ref in refs:
            _invalid(f"Duplicate batch reference {op.ref!r}")
        refs[op.ref] = node_id


def _apply_update_node(
    graph: PipelineGraph,
    op: UpdateNodeOp,
    refs: Mapping[str, str],
    nested_ids: set[str],
) -> None:
    node_id = _resolve_node_id(op.node, graph, refs, nested_ids, role="update target")
    index = _node_index(graph, node_id)
    assert index is not None  # _resolve_node_id already checked this
    node = graph.nodes[index]
    _validate_config(
        node.data.nodeType,
        op.config,
        operation="update_node",
        removable_keys=set(node.data.config),
    )

    config = dict(node.data.config)
    for key, value in op.config.items():
        if value is None:
            config.pop(key, None)
        else:
            config[key] = value
    data = node.data.model_copy(update={"config": config})
    _replace_node(graph, index, node.model_copy(update={"data": data}))


def _apply_rename_node(
    graph: PipelineGraph,
    op: RenameNodeOp,
    refs: dict[str, str],
    nested_ids: set[str],
    new_node_ids: list[str],
) -> None:
    old_id = _resolve_node_id(op.node, graph, refs, nested_ids, role="rename target")
    index = _node_index(graph, old_id)
    assert index is not None
    new_id = _sanitize_func_name(op.new_name)

    node = graph.nodes[index]
    data = node.data.model_copy(update={"label": op.new_name})
    _replace_node(graph, index, node.model_copy(update={"id": new_id, "data": data}))
    graph.edges = [
        edge.model_copy(
            update={
                "source": new_id if edge.source == old_id else edge.source,
                "target": new_id if edge.target == old_id else edge.target,
            }
        )
        for edge in graph.edges
    ]

    for ref, ref_id in list(refs.items()):
        if ref_id == old_id:
            refs[ref] = new_id
    for position, ref_id in enumerate(new_node_ids):
        if ref_id == old_id:
            new_node_ids[position] = new_id


def _apply_delete_node(
    graph: PipelineGraph,
    op: DeleteNodeOp,
    refs: Mapping[str, str],
    nested_ids: set[str],
    new_node_ids: list[str],
) -> None:
    node_id = _resolve_node_id(op.node, graph, refs, nested_ids, role="delete target")
    index = _node_index(graph, node_id)
    assert index is not None
    del graph.nodes[index]
    graph.edges = [
        edge for edge in graph.edges if edge.source != node_id and edge.target != node_id
    ]
    new_node_ids[:] = [candidate for candidate in new_node_ids if candidate != node_id]


def _apply_add_edge(
    graph: PipelineGraph,
    op: AddEdgeOp,
    refs: Mapping[str, str],
    nested_ids: set[str],
) -> None:
    source = _resolve_node_id(op.source, graph, refs, nested_ids, role="edge source")
    target = _resolve_node_id(op.target, graph, refs, nested_ids, role="edge target")
    # An exact duplicate would share its React Flow id with the existing
    # edge and become impossible to remove through delete_edge (the match
    # is ambiguous by construction).  Reject loudly instead.
    if any(
        edge.source == source
        and edge.target == target
        and edge.sourceHandle == op.source_handle
        and edge.targetHandle == op.target_handle
        for edge in graph.edges
    ):
        _invalid(
            f"Edge {source!r} -> {target!r} with these handles already exists; "
            "add_edge does not create duplicates"
        )
    try:
        graph.edges.append(
            GraphEdge(
                id=_edge_id(source, target, op.source_handle, op.target_handle),
                source=source,
                target=target,
                sourceHandle=op.source_handle,
                targetHandle=op.target_handle,
            )
        )
    except ValidationError as exc:
        raise OpValidationError(f"Invalid edge: {exc}") from exc


def _apply_delete_edge(
    graph: PipelineGraph,
    op: DeleteEdgeOp,
    refs: Mapping[str, str],
    nested_ids: set[str],
) -> None:
    source = _resolve_node_id(op.source, graph, refs, nested_ids, role="edge source")
    target = _resolve_node_id(op.target, graph, refs, nested_ids, role="edge target")

    # An omitted handle is a wildcard.  An explicitly supplied JSON null is
    # meaningful and matches an edge with no handle, which is why the model's
    # fields-set metadata is used instead of treating both cases identically.
    source_handle: object = (
        op.source_handle if "source_handle" in op.model_fields_set else _ANY_HANDLE
    )
    target_handle: object = (
        op.target_handle if "target_handle" in op.model_fields_set else _ANY_HANDLE
    )

    matches = [
        index
        for index, edge in enumerate(graph.edges)
        if edge.source == source
        and edge.target == target
        and (source_handle is _ANY_HANDLE or edge.sourceHandle == source_handle)
        and (target_handle is _ANY_HANDLE or edge.targetHandle == target_handle)
    ]
    if not matches:
        _invalid(f"No edge matches {source!r} -> {target!r} with the requested handles")
    if len(matches) > 1:
        _invalid(
            f"Edge match {source!r} -> {target!r} is ambiguous; "
            "specify source_handle and target_handle"
        )
    del graph.edges[matches[0]]


def _parents_by_node(graph: PipelineGraph) -> dict[str, tuple[str, ...]]:
    parents: dict[str, list[str]] = {node.id: [] for node in graph.nodes}
    for edge in graph.edges:
        if edge.target in parents and edge.source not in parents[edge.target]:
            parents[edge.target].append(edge.source)
    return {node_id: tuple(sorted(parent_ids)) for node_id, parent_ids in parents.items()}


def _assign_new_positions(graph: PipelineGraph, new_node_ids: Sequence[str]) -> None:
    """Assign deterministic positions after all operations have wired the graph."""

    new_order: list[str] = []
    seen: set[str] = set()
    final_ids = {node.id for node in graph.nodes}
    for node_id in new_node_ids:
        if node_id in final_ids and node_id not in seen:
            new_order.append(node_id)
            seen.add(node_id)
    if not new_order:
        return

    new_set = set(new_order)
    parents = _parents_by_node(graph)
    nodes_by_id = {node.id: node for node in graph.nodes}
    existing_nodes = [node for node in graph.nodes if node.id not in new_set]
    fallback_x = max((float(node.position.get("x", 0.0)) for node in existing_nodes), default=None)
    fallback_x = 0.0 if fallback_x is None else fallback_x + _X_STEP

    group_for = {node_id: parents.get(node_id, ()) for node_id in new_order}
    existing_group_counts: dict[tuple[str, ...], int] = {}
    for node in existing_nodes:
        group = parents.get(node.id, ())
        existing_group_counts[group] = existing_group_counts.get(group, 0) + 1
    sibling_index = {
        node_id: existing_group_counts.get(group_for[node_id], 0)
        + sum(1 for prior in new_order[:index] if group_for[prior] == group_for[node_id])
        for index, node_id in enumerate(new_order)
    }

    positions: dict[str, dict[str, float]] = {
        node.id: {
            "x": float(node.position.get("x", 0.0)),
            "y": float(node.position.get("y", 0.0)),
        }
        for node in existing_nodes
    }
    visiting: set[str] = set()

    def position_for(node_id: str) -> dict[str, float]:
        if node_id in positions:
            return positions[node_id]
        if node_id in visiting:
            # Cyclic graphs are not made invalid by this layout pass.  Use a
            # stable fallback for the cycle, then let every other operation
            # retain its normal validation semantics.
            return {"x": fallback_x, "y": sibling_index.get(node_id, 0) * _Y_STEP}

        visiting.add(node_id)
        parent_ids = group_for.get(node_id, ())
        parent_positions = [position_for(parent) for parent in parent_ids if parent in nodes_by_id]
        if parent_positions:
            x = max(position["x"] for position in parent_positions) + _X_STEP
            y = min(position["y"] for position in parent_positions)
        else:
            x = fallback_x
            y = 0.0
        result = {"x": x, "y": y + sibling_index[node_id] * _Y_STEP}
        visiting.remove(node_id)
        positions[node_id] = result
        return result

    for node_id in new_order:
        position_for(node_id)

    graph.nodes = [
        node.model_copy(update={"position": positions[node.id]}) if node.id in new_set else node
        for node in graph.nodes
    ]


def apply_ops(graph: PipelineGraph, ops: Sequence[GraphEditOp]) -> PipelineGraph:
    """Apply a batch to a deep copy of *graph* and return the resulting graph.

    Operations are evaluated in order.  A validation error can therefore
    refer to an earlier add, rename, or delete, while the caller's original
    graph remains untouched because no operation runs against it directly.
    """

    raw_ops = list(ops)
    if any(isinstance(op, Mapping) for op in raw_ops):
        parsed_ops = parse_ops(raw_ops)  # type: ignore[arg-type]
    else:
        parsed_ops = raw_ops

    working = graph.model_copy(deep=True)
    nested_ids = _nested_submodel_node_ids(working.submodels)
    refs: dict[str, str] = {}
    new_node_ids: list[str] = []

    for index, op in enumerate(parsed_ops):
        try:
            if isinstance(op, AddNodeOp):
                _apply_add_node(working, op, refs, new_node_ids)
            elif isinstance(op, UpdateNodeOp):
                _apply_update_node(working, op, refs, nested_ids)
            elif isinstance(op, RenameNodeOp):
                _apply_rename_node(working, op, refs, nested_ids, new_node_ids)
            elif isinstance(op, DeleteNodeOp):
                _apply_delete_node(working, op, refs, nested_ids, new_node_ids)
            elif isinstance(op, AddEdgeOp):
                _apply_add_edge(working, op, refs, nested_ids)
            elif isinstance(op, DeleteEdgeOp):
                _apply_delete_edge(working, op, refs, nested_ids)
            elif isinstance(op, UpdatePreambleOp):
                working.preamble = op.preamble
            else:
                _invalid(f"Unsupported graph edit operation at index {index}")
        except OpValidationError:
            raise
        except ValidationError as exc:
            raise OpValidationError(
                f"Invalid graph edit operation at index {index}: {exc}"
            ) from exc

    _assign_new_positions(working, new_node_ids)
    return working


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
    "apply_ops",
    "parse_ops",
]
