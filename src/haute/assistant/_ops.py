"""Pure graph-edit operations used by the assistant mutation tool.

The wire models re-exported here deliberately know nothing about files or the
save service.  ``parse_ops`` validates the provider-shaped payload and
``apply_ops`` evaluates a parsed batch against a deep copy of a
``PipelineGraph``.  This keeps a failed batch from ever changing the graph
that the caller owns.
"""

from __future__ import annotations

import ast
import json
from collections import OrderedDict
from collections.abc import Iterator, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from threading import RLock
from time import monotonic
from types import MappingProxyType
from typing import Any, Literal, NoReturn, TypeVar, cast

from pydantic import BaseModel, ValidationError

from haute._config_io import NODE_TYPE_TO_FOLDER
from haute._config_validation import VALID_KEYS
from haute._graph_utils import _edge_id, _sanitize_func_name
from haute._types import (
    GraphEdge,
    GraphNode,
    NodeData,
    NodeType,
    PipelineGraph,
    SubmodelDefinition,
)
from haute.assistant._catalog import capability_manifest
from haute.assistant._wire_ops import (
    AddEdgeOp,
    AddNodeOp,
    DeleteEdgeOp,
    DeleteNodeOp,
    GraphEditOp,
    OpValidationError,
    RenameNodeOp,
    UpdateNodeOp,
    UpdatePreambleOp,
    parse_ops,
)
from haute.errors import HauteError

_SUBMODEL_TYPES = frozenset({NodeType.SUBMODEL, NodeType.SUBMODEL_PORT})
_X_STEP = 280.0
_Y_STEP = 120.0
_ANY_HANDLE = object()
_GRAPH_EDIT_OP_MODELS = (
    AddNodeOp,
    UpdateNodeOp,
    RenameNodeOp,
    DeleteNodeOp,
    AddEdgeOp,
    DeleteEdgeOp,
    UpdatePreambleOp,
)


def _invalid(message: str) -> NoReturn:
    raise OpValidationError(message)


def _mapping(value: object) -> Mapping[str, Any] | None:
    if isinstance(value, BaseModel):
        dumped = value.model_dump(mode="python")
        return dumped if isinstance(dumped, Mapping) else None
    return value if isinstance(value, Mapping) else None


def _nested_submodel_node_ids(
    submodels: dict[str, SubmodelDefinition] | None,
) -> set[str]:
    """Collect child ids owned by canonical submodel definitions."""
    return {node.id for definition in (submodels or {}).values() for node in definition.graph.nodes}


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
    if _node_index(graph, node_id) is not None:
        _invalid(f"Cannot add node {op.name!r}: sanitized id {node_id!r} already exists")
    node = GraphNode(
        id=node_id,
        type=op.node_type.value,
        data=NodeData(
            label=node_id,
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
    if new_id != old_id and _node_index(graph, new_id) is not None:
        _invalid(f"Cannot rename node: sanitized id {new_id!r} already exists")

    node = graph.nodes[index]
    data = node.data.model_copy(update={"label": new_id})
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


def _apply_ops_with_refs(
    graph: PipelineGraph, ops: Sequence[GraphEditOp]
) -> tuple[PipelineGraph, dict[str, str], tuple[str, ...]]:
    """Apply a batch and return its graph, resolved refs, and final new-node ids.

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
    return working, refs, tuple(new_node_ids)


def apply_ops(graph: PipelineGraph, ops: Sequence[GraphEditOp]) -> PipelineGraph:
    """Apply a batch to a deep copy of *graph* and return the resulting graph."""

    return _apply_ops_with_refs(graph, ops)[0]


# The plan domain below is deliberately file-service agnostic.  It records the
# facts which an application service must later re-check under its save lock;
# it does not itself write a source file or a sidecar.
_DIFF_LIMIT = 50


class AssistantOperationError(HauteError):
    """A stable, machine-readable failure from the assistant plan domain."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


def _canonical_json(value: object) -> str:
    """Render JSON with the one representation used for revisions and plans."""

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _frozen_json(value: object) -> object:
    """Make a JSON-shaped value recursively immutable and equality-friendly."""

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _frozen_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_frozen_json(item) for item in value)
    return value


def _wire_json(value: object) -> object:
    """Turn the immutable representation back into ordinary JSON values."""

    if isinstance(value, Mapping):
        return {key: _wire_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_wire_json(item) for item in value]
    return value


def _project_relative_path(project_root: Path, path: Path) -> tuple[Path, str]:
    root = project_root.resolve()
    resolved = path.resolve()
    try:
        return resolved, resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise AssistantOperationError(
            "project_source_forbidden", "Project source is outside the project root"
        ) from exc


def _source_manifest_entry(project_root: Path, path: Path) -> tuple[str, str]:
    resolved, relative = _project_relative_path(project_root, path)
    if not resolved.is_file():
        raise AssistantOperationError(
            "project_source_missing", f"Project source is missing: {relative}"
        )
    return f"content:{relative}", sha256(resolved.read_bytes()).hexdigest()


@dataclass(frozen=True, slots=True)
class ProjectSourceEvidence:
    """One exact project fact previously returned to the assistant."""

    path: Path
    digest: str
    kind: Literal["content", "schema"] = "content"

    def __post_init__(self) -> None:
        if len(self.digest) != 64 or any(
            character not in "0123456789abcdef" for character in self.digest
        ):
            raise ValueError("project evidence digest must be lowercase SHA-256 hex")


def dataset_schema_digest(schema: Mapping[str, object]) -> str:
    """Hash exactly the schema-only payload exposed to the model."""

    return _digest(schema)


def _evidence_manifest_entry(
    project_root: Path,
    evidence: ProjectSourceEvidence,
) -> tuple[str, str]:
    resolved, relative = _project_relative_path(project_root, evidence.path)
    if not resolved.is_file():
        raise AssistantOperationError(
            "project_source_missing", f"Project source is missing: {relative}"
        )
    if evidence.kind == "content":
        actual = sha256(resolved.read_bytes()).hexdigest()
    else:
        from haute.routes.files import _read_schema_only_blocking

        actual = dataset_schema_digest(_read_schema_only_blocking(relative, resolved))
    if actual != evidence.digest:
        raise AssistantOperationError(
            "stale_project_evidence",
            "A retrieved project source changed; inspect it again before planning.",
        )
    return f"{evidence.kind}:{relative}", actual


@dataclass(frozen=True, slots=True)
class ProjectSnapshot:
    """An immutable description of the saved state a plan is authorized against."""

    revision: str
    capability_hash: str
    graph: PipelineGraph
    source_manifest: tuple[tuple[str, str], ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "revision": self.revision,
            "capability_hash": self.capability_hash,
            "graph": self.graph.model_dump(mode="json"),
            "source_manifest": dict(self.source_manifest),
        }


def build_project_snapshot(
    project_root: Path,
    source_file: Path,
    graph: PipelineGraph,
    project_sources: Sequence[Path | ProjectSourceEvidence] = (),
) -> ProjectSnapshot:
    """Build a content-addressed snapshot without reading anything outside *root*."""

    root = project_root.resolve()
    source_entries = [_source_manifest_entry(root, source_file)]
    config_path = root / "haute.toml"
    if config_path.exists():
        source_entries.append(_source_manifest_entry(root, config_path))
    for source in project_sources:
        source_entries.append(
            _evidence_manifest_entry(root, source)
            if isinstance(source, ProjectSourceEvidence)
            else _source_manifest_entry(root, source)
        )
    # Duplicate references are one source identity, not an accidental revision change.
    manifest = tuple(sorted(dict(source_entries).items()))
    capability_hash = capability_manifest().capability_hash
    canonical_graph = graph.model_dump(mode="json")
    revision = _digest(
        {
            "capability_hash": capability_hash,
            "graph": canonical_graph,
            "sources": dict(manifest),
        }
    )
    return ProjectSnapshot(
        revision=revision,
        capability_hash=capability_hash,
        graph=graph.model_copy(deep=True),
        source_manifest=manifest,
    )


@dataclass(frozen=True, slots=True)
class SemanticDiff:
    nodes_added: tuple[str, ...] = ()
    nodes_removed: tuple[str, ...] = ()
    nodes_renamed: tuple[tuple[str, str], ...] = ()
    nodes_updated: tuple[str, ...] = ()
    edges_added: tuple[tuple[str, str, str | None, str | None], ...] = ()
    edges_removed: tuple[tuple[str, str, str | None, str | None], ...] = ()
    config_changes: tuple[str, ...] = ()
    preamble_changed: bool = False
    sidecar_changes: tuple[str, ...] = ()
    complete_counts: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))
    complete_hash: str = ""
    truncated: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "nodes_added": self.nodes_added,
            "nodes_removed": self.nodes_removed,
            "nodes_renamed": self.nodes_renamed,
            "nodes_updated": self.nodes_updated,
            "edges_added": self.edges_added,
            "edges_removed": self.edges_removed,
            "config_changes": self.config_changes,
            "preamble_changed": self.preamble_changed,
            "sidecar_changes": self.sidecar_changes,
            "complete_counts": dict(self.complete_counts),
            "complete_hash": self.complete_hash,
            "truncated": self.truncated,
        }


def _edge_identity(edge: GraphEdge) -> tuple[str, str, str | None, str | None]:
    return (edge.source, edge.target, edge.sourceHandle, edge.targetHandle)


def _complete_edge_identity(
    edge: GraphEdge,
) -> tuple[str, str, str | None, str | None, str | None, str | None]:
    return (
        edge.source,
        edge.target,
        edge.sourceHandle,
        edge.targetHandle,
        edge.sourcePort,
        edge.targetPort,
    )


_Identity = TypeVar("_Identity", bound=tuple[Any, ...])


def _sorted_identities(values: set[_Identity]) -> tuple[_Identity, ...]:
    """Sort nullable tuple identities without comparing ``None`` to strings."""

    return tuple(sorted(values, key=_canonical_json))


def _semantic_node_id(raw_id: str, refs: Mapping[str, str]) -> str:
    if raw_id.startswith("$"):
        return refs.get(raw_id[1:], raw_id)
    return raw_id


def _semantic_diff(
    before: PipelineGraph,
    after: PipelineGraph,
    ops: Sequence[GraphEditOp],
    refs: Mapping[str, str],
) -> SemanticDiff:
    old_nodes = {node.id: node for node in before.nodes}
    new_nodes = {node.id: node for node in after.nodes}
    renamed = tuple(
        (_semantic_node_id(op.node, refs), _sanitize_func_name(op.new_name))
        for op in ops
        if isinstance(op, RenameNodeOp) and not op.node.startswith("$")
    )
    # Save/codegen/parser may canonicalise untouched source bodies and
    # inferred contracts. The semantic mutation diff is therefore grounded
    # in the explicit operation vocabulary, while added/removed nodes and
    # edges are still derived from the actual before/after graphs.
    updates = tuple(
        sorted({_semantic_node_id(op.node, refs) for op in ops if isinstance(op, UpdateNodeOp)})
    )
    config_changes = tuple(
        sorted(
            f"{_semantic_node_id(op.node, refs)}:{key}"
            for op in ops
            if isinstance(op, UpdateNodeOp)
            for key in op.config
        )
    )
    old_edges = {_edge_identity(edge) for edge in before.edges}
    new_edges = {_edge_identity(edge) for edge in after.edges}
    added_ids = new_nodes.keys() - old_nodes.keys()
    removed_ids = old_nodes.keys() - new_nodes.keys()
    sidecar_ids = added_ids | removed_ids | set(updates)
    sidecar_changes = tuple(
        sorted(
            f"config/{folder}/{node_id}"
            for node_id in sidecar_ids
            if (node := new_nodes.get(node_id) or old_nodes.get(node_id)) is not None
            if (folder := NODE_TYPE_TO_FOLDER.get(node.data.nodeType)) is not None
        )
    )
    nodes_added = tuple(sorted(added_ids))
    nodes_removed = tuple(sorted(removed_ids))
    edges_added = _sorted_identities(new_edges - old_edges)
    edges_removed = _sorted_identities(old_edges - new_edges)
    complete_values: dict[str, tuple[object, ...]] = {
        "nodes_added": nodes_added,
        "nodes_removed": nodes_removed,
        "nodes_renamed": renamed,
        "nodes_updated": updates,
        "edges_added": edges_added,
        "edges_removed": edges_removed,
        "config_changes": config_changes,
        "sidecar_changes": sidecar_changes,
    }
    complete_counts = {category: len(values) for category, values in complete_values.items()}
    old_complete_edges = {_complete_edge_identity(edge) for edge in before.edges}
    new_complete_edges = {_complete_edge_identity(edge) for edge in after.edges}
    complete_payload = {
        **complete_values,
        "complete_edges_added": _sorted_identities(new_complete_edges - old_complete_edges),
        "complete_edges_removed": _sorted_identities(old_complete_edges - new_complete_edges),
        "preamble_digests": (
            sha256((before.preamble or "").encode("utf-8")).hexdigest(),
            sha256((after.preamble or "").encode("utf-8")).hexdigest(),
        ),
    }
    return SemanticDiff(
        nodes_added=nodes_added[:_DIFF_LIMIT],
        nodes_removed=nodes_removed[:_DIFF_LIMIT],
        nodes_renamed=renamed[:_DIFF_LIMIT],
        nodes_updated=updates[:_DIFF_LIMIT],
        edges_added=edges_added[:_DIFF_LIMIT],
        edges_removed=edges_removed[:_DIFF_LIMIT],
        config_changes=config_changes[:_DIFF_LIMIT],
        preamble_changed=before.preamble != after.preamble,
        sidecar_changes=sidecar_changes[:_DIFF_LIMIT],
        complete_counts=MappingProxyType(complete_counts),
        complete_hash=_digest(complete_payload),
        truncated=any(count > _DIFF_LIMIT for count in complete_counts.values()),
    )


def semantic_diff(
    before: PipelineGraph,
    after: PipelineGraph,
    operations: Sequence[GraphEditOp] | Sequence[Mapping[str, Any]],
) -> SemanticDiff:
    """Return the bounded canonical semantic diff for an exact operation batch."""

    typed_operations = [
        operation if isinstance(operation, _GRAPH_EDIT_OP_MODELS) else parse_ops([operation])[0]
        for operation in operations
    ]
    _expected, refs, _new_node_ids = _apply_ops_with_refs(before, typed_operations)
    return _semantic_diff(before, after, typed_operations, refs)


def _automatic_postconditions(graph: PipelineGraph, diff: SemanticDiff) -> tuple[object, ...]:
    conditions: list[object] = [
        _frozen_json({"kind": "node_exists", "node": node_id}) for node_id in diff.nodes_added
    ]
    conditions.extend(
        _frozen_json(
            {
                "kind": "edge_exists",
                "source": source,
                "target": target,
                "source_handle": source_handle,
                "target_handle": target_handle,
            }
        )
        for source, target, source_handle, target_handle in diff.edges_added
    )
    conditions.extend(
        _frozen_json({"kind": "node_absent", "node": node_id}) for node_id in diff.nodes_removed
    )
    conditions.extend(
        _frozen_json(
            {
                "kind": "edge_absent",
                "source": source,
                "target": target,
                "source_handle": source_handle,
                "target_handle": target_handle,
            }
        )
        for source, target, source_handle, target_handle in diff.edges_removed
    )
    if diff.preamble_changed:
        conditions.append(
            _frozen_json(
                {
                    "kind": "preamble_digest",
                    "sha256": sha256((graph.preamble or "").encode("utf-8")).hexdigest(),
                }
            )
        )
    conditions.append(
        _frozen_json({"kind": "graph_shape", "nodes": len(graph.nodes), "edges": len(graph.edges)})
    )
    return tuple(conditions[:_DIFF_LIMIT])


def verify_postconditions(
    graph: PipelineGraph,
    postconditions: Sequence[object],
) -> tuple[Mapping[str, object], ...]:
    """Evaluate closed structural postconditions and return bounded evidence."""

    nodes = {node.id: node for node in graph.nodes}
    edges = {_edge_identity(edge) for edge in graph.edges}
    evidence: list[Mapping[str, object]] = []
    for raw in postconditions:
        if not isinstance(raw, Mapping):
            raise AssistantOperationError("invalid_plan", "Postcondition must be an object")
        condition = _wire_json(raw)
        assert isinstance(condition, dict)
        kind = condition.get("kind")
        passed = False
        if kind == "node_exists":
            passed = condition.get("node") in nodes
        elif kind == "node_absent":
            passed = condition.get("node") not in nodes
        elif kind in {"edge_exists", "edge_absent"}:
            source = condition.get("source")
            target = condition.get("target")
            source_handle = condition.get("source_handle", _ANY_HANDLE)
            target_handle = condition.get("target_handle", _ANY_HANDLE)
            matched = any(
                edge[0] == source
                and edge[1] == target
                and (source_handle is _ANY_HANDLE or edge[2] == source_handle)
                and (target_handle is _ANY_HANDLE or edge[3] == target_handle)
                for edge in edges
            )
            passed = matched if kind == "edge_exists" else not matched
        elif kind == "graph_shape":
            passed = condition.get("nodes") == len(graph.nodes) and condition.get("edges") == len(
                graph.edges
            )
        elif kind == "preamble_digest":
            passed = (
                condition.get("sha256")
                == sha256((graph.preamble or "").encode("utf-8")).hexdigest()
            )
        else:
            raise AssistantOperationError(
                "invalid_plan",
                f"Unsupported postcondition kind: {kind!r}",
            )
        item = MappingProxyType({"kind": str(kind), "passed": passed})
        evidence.append(item)
        if not passed:
            raise AssistantOperationError(
                "postcondition_failed",
                f"Postcondition {kind!r} was not satisfied",
            )
    return tuple(evidence)


def _affected_capabilities(
    before: PipelineGraph,
    after: PipelineGraph,
    diff: SemanticDiff,
    ops: Sequence[GraphEditOp],
    refs: Mapping[str, str],
) -> tuple[str, ...]:
    old_nodes = {node.id: node for node in before.nodes}
    new_nodes = {node.id: node for node in after.nodes}
    ids = set(old_nodes).symmetric_difference(new_nodes)
    old_edges = {_edge_identity(edge) for edge in before.edges}
    new_edges = {_edge_identity(edge) for edge in after.edges}
    for source, target, _source_handle, _target_handle in old_edges.symmetric_difference(new_edges):
        ids.update((source, target))
    ids.update(
        _semantic_node_id(op.node, refs)
        for op in ops
        if isinstance(op, (UpdateNodeOp, RenameNodeOp))
    )
    capabilities = {
        node.data.nodeType.value
        for node_id in ids
        if (node := new_nodes.get(node_id) or old_nodes.get(node_id)) is not None
    }
    if diff.preamble_changed:
        capabilities.add("pipeline_preamble")
    return tuple(sorted(capabilities))


@dataclass(frozen=True, slots=True)
class GraphEditPlan:
    base_revision: str
    capability_hash: str
    source_manifest: tuple[tuple[str, str], ...]
    normalized_operations: tuple[object, ...]
    diff: SemanticDiff
    affected_capabilities: tuple[str, ...]
    postconditions: tuple[object, ...]
    validation_warnings: tuple[str, ...]
    resulting_graph_shape: Mapping[str, int]
    egress: Literal["none"]
    verification_tier: Literal["structural", "schema"]
    verification_evidence: tuple[Mapping[str, object], ...]
    plan_hash: str

    def as_dict(self) -> dict[str, object]:
        return {
            "base_revision": self.base_revision,
            "capability_hash": self.capability_hash,
            "revision_sources": dict(self.source_manifest),
            "normalized_operations": _wire_json(self.normalized_operations),
            "diff": self.diff.as_dict(),
            "affected_capabilities": self.affected_capabilities,
            "postconditions": _wire_json(self.postconditions),
            "validation_warnings": self.validation_warnings,
            "resulting_graph_shape": dict(self.resulting_graph_shape),
            "egress": self.egress,
            "verification_tier": self.verification_tier,
            "verification_evidence": _wire_json(self.verification_evidence),
            "plan_hash": self.plan_hash,
        }


def _resolve_postcondition_refs(
    condition: Mapping[str, Any],
    refs: Mapping[str, str],
) -> dict[str, Any]:
    """Resolve recipe/user postcondition refs against this exact applied batch."""

    resolved = dict(condition)
    for field_name in ("node", "source", "target"):
        value = resolved.get(field_name)
        if not isinstance(value, str) or not value.startswith("$"):
            continue
        ref = value[1:]
        if ref not in refs:
            raise AssistantOperationError(
                "invalid_plan",
                f"Postcondition references unknown batch-local ref {value!r}",
            )
        resolved[field_name] = refs[ref]
    return resolved


def _validate_postconditions(
    postconditions: Sequence[Mapping[str, Any]],
) -> None:
    """Validate the closed structural proof vocabulary before any save."""

    if isinstance(postconditions, (str, bytes)) or not isinstance(postconditions, Sequence):
        raise AssistantOperationError("invalid_plan", "Postconditions must be a list of objects")
    if len(postconditions) > 100:
        raise AssistantOperationError(
            "invalid_plan", "A plan may declare at most 100 postconditions"
        )
    allowed_keys = {
        "node_exists": {"kind", "node"},
        "node_absent": {"kind", "node"},
        "edge_exists": {
            "kind",
            "source",
            "target",
            "source_handle",
            "target_handle",
        },
        "edge_absent": {
            "kind",
            "source",
            "target",
            "source_handle",
            "target_handle",
        },
        "graph_shape": {"kind", "nodes", "edges"},
        "preamble_digest": {"kind", "sha256"},
    }
    required_keys = {
        "node_exists": {"kind", "node"},
        "node_absent": {"kind", "node"},
        "edge_exists": {"kind", "source", "target"},
        "edge_absent": {"kind", "source", "target"},
        "graph_shape": {"kind", "nodes", "edges"},
        "preamble_digest": {"kind", "sha256"},
    }
    for condition in postconditions:
        if not isinstance(condition, Mapping):
            raise AssistantOperationError("invalid_plan", "Postcondition must be an object")
        kind = condition.get("kind")
        if not isinstance(kind, str) or kind not in allowed_keys:
            raise AssistantOperationError(
                "invalid_plan", f"Unsupported postcondition kind: {kind!r}"
            )
        if set(condition) - allowed_keys[kind] or required_keys[kind] - set(condition):
            raise AssistantOperationError(
                "invalid_plan",
                f"Postcondition {kind!r} is not the closed supported shape",
            )
        if kind in {"node_exists", "node_absent"}:
            if not isinstance(condition["node"], str) or not condition["node"]:
                raise AssistantOperationError(
                    "invalid_plan", f"Postcondition {kind!r} needs a node id"
                )
        elif kind in {"edge_exists", "edge_absent"}:
            if any(
                not isinstance(condition[field], str) or not condition[field]
                for field in ("source", "target")
            ) or any(
                condition.get(field) is not None and not isinstance(condition.get(field), str)
                for field in ("source_handle", "target_handle")
            ):
                raise AssistantOperationError(
                    "invalid_plan", f"Postcondition {kind!r} has invalid edge identity"
                )
        elif kind == "graph_shape":
            if any(
                type(condition[field]) is not int or condition[field] < 0
                for field in ("nodes", "edges")
            ):
                raise AssistantOperationError(
                    "invalid_plan", "graph_shape values must be non-negative integers"
                )
        else:
            digest = condition["sha256"]
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                raise AssistantOperationError(
                    "invalid_plan", "preamble_digest must be lowercase SHA-256 hex"
                )


def _target_binds_df(target: ast.expr) -> bool:
    if isinstance(target, ast.Name):
        return target.id == "df"
    if isinstance(target, (ast.List, ast.Tuple)):
        return any(_target_binds_df(element) for element in target.elts)
    return False


def _is_df_name(value: ast.expr) -> bool:
    return isinstance(value, ast.Name) and value.id == "df"


def _is_frame_candidate(value: ast.expr) -> bool:
    return not _is_df_name(value) and not isinstance(value, ast.Constant)


class _PolarsResultVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.retained = False

    def visit_Assign(self, node: ast.Assign) -> None:
        if any(_target_binds_df(target) for target in node.targets) and _is_frame_candidate(
            node.value
        ):
            self.retained = True

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if (
            _target_binds_df(node.target)
            and node.value is not None
            and _is_frame_candidate(node.value)
        ):
            self.retained = True

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if _target_binds_df(node.target):
            self.retained = True

    def visit_Return(self, node: ast.Return) -> None:
        if node.value is not None and _is_frame_candidate(node.value):
            self.retained = True

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        pass

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        pass

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        pass

    def visit_Lambda(self, node: ast.Lambda) -> None:
        pass


_BARE_INPUT_NAME = "df"
_NESTED_SCOPES = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.Lambda,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
)


def _binds_name(scope: ast.AST, name: str) -> bool:
    """Report whether a nested scope binds *name* as its own local.

    A parameter or a comprehension target shadows the module binding outright.
    So does any assignment in a function body, which Python makes local for the
    whole function. Bindings inside a further nested scope belong to that scope
    and must not suppress a real read in the enclosing function.
    """

    if isinstance(scope, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        return any(
            _is_name(node, name, ast.Store)
            for generator in scope.generators
            for node in ast.walk(generator.target)
        )
    if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        return False
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
        isinstance(node, ast.Global) and name in node.names
        for statement in scope.body
        for node in _nodes_in_own_scope(statement)
    ):
        return True
    arguments = scope.args
    declared = (
        *arguments.posonlyargs,
        *arguments.args,
        *arguments.kwonlyargs,
        *(arg for arg in (arguments.vararg, arguments.kwarg) if arg is not None),
    )
    if any(argument.arg == name for argument in declared):
        return True
    # A lambda's body is one expression, not a statement list, and can still
    # bind through a walrus (`lambda x: (df := x)`).
    body: list[ast.AST] = list(scope.body) if isinstance(scope.body, list) else [scope.body]
    return any(_binds_name_in_own_scope(item, name) for item in body)


def _binds_name_in_own_scope(node: ast.AST, name: str) -> bool:
    """Find a binding without descending into a child lexical scope."""

    if _is_name(node, name, ast.Store) or _is_name(node, name, ast.Del):
        return True
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name == name or any(
            _binds_name_in_own_scope(expression, name)
            for expression in _scope_entry_expressions(node)
        )
    if isinstance(node, ast.Lambda):
        return any(
            _binds_name_in_own_scope(expression, name)
            for expression in _scope_entry_expressions(node)
        )
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        return any(
            isinstance(candidate, ast.NamedExpr) and _is_name(candidate.target, name, ast.Store)
            for candidate in _nodes_in_comprehension(node)
        )
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return any(
            (alias.asname or alias.name.split(".", maxsplit=1)[0]) == name
            for alias in node.names
            if alias.name != "*"
        )
    if isinstance(node, ast.ExceptHandler) and node.name == name:
        return True
    if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name == name:
        return True
    if isinstance(node, ast.MatchMapping) and node.rest == name:
        return True
    return any(_binds_name_in_own_scope(child, name) for child in ast.iter_child_nodes(node))


def _scope_entry_expressions(node: ast.AST) -> tuple[ast.expr, ...]:
    """Expressions evaluated outside a function, lambda, or class body."""

    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        annotations = tuple(
            annotation
            for annotation in (
                *(argument.annotation for argument in node.args.posonlyargs),
                *(argument.annotation for argument in node.args.args),
                *(argument.annotation for argument in node.args.kwonlyargs),
                node.args.vararg.annotation if node.args.vararg is not None else None,
                node.args.kwarg.annotation if node.args.kwarg is not None else None,
                node.returns,
            )
            if annotation is not None
        )
        return (
            *node.decorator_list,
            *node.args.defaults,
            *(default for default in node.args.kw_defaults if default is not None),
            *annotations,
        )
    if isinstance(node, ast.Lambda):
        return (
            *node.args.defaults,
            *(default for default in node.args.kw_defaults if default is not None),
        )
    if isinstance(node, ast.ClassDef):
        return (*node.decorator_list, *node.bases, *(keyword.value for keyword in node.keywords))
    return ()


def _nodes_in_own_scope(node: ast.AST) -> Iterator[ast.AST]:
    """Yield nodes without entering a nested lexical scope's body."""

    yield node
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
        for expression in _scope_entry_expressions(node):
            yield from _nodes_in_own_scope(expression)
        return
    for child in ast.iter_child_nodes(node):
        yield from _nodes_in_own_scope(child)


def _nodes_in_comprehension(node: ast.AST) -> Iterator[ast.AST]:
    """Yield comprehension nodes while excluding child function/lambda scopes."""

    yield node
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
        return
    for child in ast.iter_child_nodes(node):
        yield from _nodes_in_comprehension(child)


def _is_name(node: ast.AST, name: str, context: type[ast.expr_context]) -> bool:
    return isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, context)


def _names_in_owning_scope(node: ast.AST, name: str) -> Iterator[ast.AST]:
    """Yield nodes reachable without entering a scope that shadows *name*.

    A nested `def`, `lambda`, or comprehension that binds the name introduces
    its own variable: `def widen(df)` names its parameter, not the node's
    injected input, so reading it there says nothing about wiring order. A
    nested scope that does *not* bind the name still reads the module's, so
    the walk descends into that one.
    """

    if isinstance(node, _NESTED_SCOPES) and _binds_name(node, name):
        # A comprehension target is not bound until after its first iterable
        # has been evaluated in the enclosing scope.
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            yield node
            yield from _names_in_owning_scope(node.generators[0].iter, name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            yield node
            for expression in _scope_entry_expressions(node):
                yield from _names_in_owning_scope(expression, name)
        return
    yield node
    for child in ast.iter_child_nodes(node):
        yield from _names_in_owning_scope(child, name)


def _reads_df_before_binding_it(tree: ast.Module) -> bool:
    """Report whether the code reads `df` before assigning it.

    `df` is the node's output variable and is never bound to an input — a
    polars node's inputs are its named parameters. A read before the code's
    own assignment is therefore a guaranteed `NameError` at execution time,
    in the generated module and canvas execution alike.

    Only reads that resolve to the module-level `df` count, so the walk skips
    any nested scope holding a `df` of its own. A statement's loads are
    considered before its stores because an assignment evaluates its value
    first: `df = df.head()` reads the unbound name, `df = left` does not.
    """

    for statement in tree.body:
        names = list(_names_in_owning_scope(statement, _BARE_INPUT_NAME))
        if any(_is_name(node, _BARE_INPUT_NAME, ast.Load) for node in names):
            return True
        if any(_is_name(node, _BARE_INPUT_NAME, ast.Store) for node in names):
            return False
    return False


def _validate_polars_named_inputs(code: object, node_id: str, input_names: Sequence[str]) -> None:
    """Require assistant-authored code to bind `df` before reading it."""

    if not isinstance(code, str) or not code.strip():
        return
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return  # _validate_polars_result_retained owns the syntax verdict
    if not _reads_df_before_binding_it(tree):
        return
    if input_names:
        _invalid(
            f"Node {node_id!r} reads 'df' before assigning it, but 'df' is not bound to "
            "any input — it is the node's output variable. Start from the input you "
            "mean by name (" + ", ".join(sorted(input_names)) + ") and assign the "
            "result to 'df'."
        )
    _invalid(
        f"Node {node_id!r} reads 'df' before assigning it, but this node has no inputs "
        f"and 'df' is unbound until the code assigns it. Construct a frame and assign "
        f"it to 'df'."
    )


def _validate_polars_result_retained(code: object) -> None:
    if not isinstance(code, str):
        _invalid("Explicit Polars code must be a string")
    if not code.strip():
        return
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise OpValidationError("Explicit Polars code contains invalid Python") from exc

    visitor = _PolarsResultVisitor()
    visitor.visit(tree)
    if not visitor.retained:
        _invalid(
            "Explicit Polars code must assign a transformed frame to 'df' or return "
            "a transformed frame; bare Polars expressions are immutable and their "
            "result would be discarded"
        )


def _incoming_input_names(
    result: PipelineGraph,
    node_id: str,
    nodes_by_id: Mapping[str, GraphNode],
) -> tuple[str, ...]:
    """Return the input names this node's own code binds, in edge order."""

    from haute._graph_utils import edge_input_name

    names: list[str] = []
    for edge in result.edges:
        if edge.target != node_id:
            continue
        source_node = nodes_by_id.get(edge.source)
        if source_node is None:
            continue
        try:
            names.append(edge_input_name(edge, source_node))
        except ValueError:
            # A malformed edge is the save validator's verdict, not this one's.
            continue
    return tuple(names)


def _validate_assistant_authored_graph(
    result: PipelineGraph,
    diff: SemanticDiff,
    authored_added: Sequence[str],
) -> None:
    """Enforce assistant-only authoring invariants on the final planned graph."""

    nodes_by_id = {node.id: node for node in result.nodes}
    code_changed = {
        change.removesuffix(":code") for change in diff.config_changes if change.endswith(":code")
    }
    for node_id in set(authored_added) | code_changed:
        node = nodes_by_id.get(node_id)
        if (
            node is not None
            and node.data.nodeType == NodeType.POLARS
            and "code" in node.data.config
        ):
            _validate_polars_result_retained(node.data.config["code"])
            _validate_polars_named_inputs(
                node.data.config["code"],
                node_id,
                _incoming_input_names(result, node_id, nodes_by_id),
            )

    incident_nodes = {node_id for edge in result.edges for node_id in (edge.source, edge.target)}
    disconnected = sorted(set(authored_added) - incident_nodes)
    if disconnected:
        raise AssistantOperationError(
            "invalid_plan",
            "New assistant-authored node(s) are disconnected: "
            + ", ".join(disconnected)
            + ". Connect every new node in the same edit plan.",
        )


def build_graph_edit_plan(
    snapshot: ProjectSnapshot,
    raw_ops: Sequence[Mapping[str, Any]],
    postconditions: Sequence[Mapping[str, Any]] = (),
    validation_warnings: Sequence[str] = (),
    verification_tier: Literal["structural", "schema"] = "structural",
    verification_evidence: Sequence[Mapping[str, object]] = (),
) -> GraphEditPlan:
    """Normalize and authorize a pure graph edit against one exact snapshot."""

    ops = parse_ops(raw_ops)
    result, refs, authored_added = _apply_ops_with_refs(snapshot.graph, ops)
    diff = _semantic_diff(snapshot.graph, result, ops, refs)
    _validate_assistant_authored_graph(result, diff, authored_added)
    normalized = tuple(_frozen_json(op.model_dump(mode="json")) for op in ops)
    _validate_postconditions(postconditions)
    resolved_conditions = tuple(
        _resolve_postcondition_refs(condition, refs) for condition in postconditions
    )
    _validate_postconditions(resolved_conditions)
    supplied_conditions = tuple(_frozen_json(condition) for condition in resolved_conditions)
    all_conditions = supplied_conditions or _automatic_postconditions(result, diff)
    verify_postconditions(result, all_conditions)
    capability_ids = _affected_capabilities(snapshot.graph, result, diff, ops, refs)
    frozen_evidence_values = tuple(_frozen_json(item) for item in verification_evidence)
    if not all(isinstance(item, Mapping) for item in frozen_evidence_values):
        raise AssistantOperationError(
            "invalid_plan",
            "Verification evidence entries must be objects",
        )
    frozen_evidence = cast(
        tuple[Mapping[str, object], ...],
        frozen_evidence_values,
    )
    if verification_tier == "schema" and not frozen_evidence:
        raise AssistantOperationError("invalid_plan", "Schema verification requires evidence")
    if verification_tier == "structural" and frozen_evidence:
        raise AssistantOperationError(
            "invalid_plan", "Structural plans cannot contain schema evidence"
        )
    authority = {
        "base_revision": snapshot.revision,
        "capability_hash": snapshot.capability_hash,
        "revision_sources": dict(snapshot.source_manifest),
        "normalized_operations": _wire_json(normalized),
        "semantic_diff_hash": diff.complete_hash,
        "postconditions": _wire_json(all_conditions),
        "validation_warnings": list(validation_warnings),
        "resulting_graph_shape": {"nodes": len(result.nodes), "edges": len(result.edges)},
        "egress": "none",
        "verification_tier": verification_tier,
        "verification_evidence": _wire_json(frozen_evidence),
        "affected_capabilities": capability_ids,
    }
    return GraphEditPlan(
        base_revision=snapshot.revision,
        capability_hash=snapshot.capability_hash,
        source_manifest=snapshot.source_manifest,
        normalized_operations=normalized,
        diff=diff,
        affected_capabilities=capability_ids,
        postconditions=all_conditions,
        validation_warnings=tuple(validation_warnings),
        resulting_graph_shape=MappingProxyType(
            {"nodes": len(result.nodes), "edges": len(result.edges)}
        ),
        egress="none",
        verification_tier=verification_tier,
        verification_evidence=frozen_evidence,
        plan_hash=_digest(authority),
    )


@dataclass(slots=True)
class _StoredPlan:
    plan: GraphEditPlan
    expires_at: float
    state: Literal["validated", "applying", "applied", "aborted"] = "validated"
    result: object | None = None


class PlanStore:
    """Thread-safe, bounded single-use plan authority ledger."""

    def __init__(self, *, max_size: int = 100, ttl_seconds: float = 600.0) -> None:
        if max_size < 1 or ttl_seconds <= 0:
            raise ValueError("max_size and ttl_seconds must be positive")
        self._max_size = max_size
        self._ttl_seconds = ttl_seconds
        self._records: OrderedDict[str, _StoredPlan] = OrderedDict()
        self._lock = RLock()

    def _record(self, plan_hash: str) -> _StoredPlan:
        record = self._records.get(plan_hash)
        if record is None:
            raise AssistantOperationError("plan_not_found")
        if record.state != "applying" and monotonic() >= record.expires_at:
            del self._records[plan_hash]
            raise AssistantOperationError("plan_expired")
        self._records.move_to_end(plan_hash)
        return record

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)

    def put(self, plan: GraphEditPlan) -> None:
        with self._lock:
            existing = self._records.get(plan.plan_hash)
            if existing is not None:
                if existing.state == "aborted":
                    del self._records[plan.plan_hash]
                elif existing.state == "applying" or monotonic() < existing.expires_at:
                    self._records.move_to_end(plan.plan_hash)
                    return
                else:
                    del self._records[plan.plan_hash]

            while len(self._records) >= self._max_size:
                evictable = next(
                    (
                        stored_hash
                        for stored_hash, record in self._records.items()
                        if record.state != "applying"
                    ),
                    None,
                )
                if evictable is None:
                    raise AssistantOperationError(
                        "plan_store_busy",
                        "Every plan-store slot is reserved by an in-flight apply; "
                        "retry the dry-run after those saves settle.",
                    )
                del self._records[evictable]

            self._records[plan.plan_hash] = _StoredPlan(plan, monotonic() + self._ttl_seconds)
            self._records.move_to_end(plan.plan_hash)

    def get(self, plan_hash: str) -> GraphEditPlan:
        with self._lock:
            return self._record(plan_hash).plan

    def begin_apply(self, plan_hash: str) -> GraphEditPlan:
        with self._lock:
            record = self._record(plan_hash)
            if record.state == "aborted":
                raise AssistantOperationError(
                    "plan_aborted",
                    "This plan's previous apply attempt was aborted; dry-run the "
                    "operations again before retrying.",
                )
            if record.state in {"applying", "applied"}:
                raise AssistantOperationError("plan_already_applied")
            record.state = "applying"
            return record.plan

    def complete_apply(self, plan_hash: str, result: object) -> None:
        with self._lock:
            record = self._record(plan_hash)
            if record.state != "applying":
                raise AssistantOperationError("plan_already_applied")
            record.state = "applied"
            record.result = _frozen_json(result)

    def abort_apply(self, plan_hash: str) -> None:
        """Invalidate a reserved plan after a pre-save failure.

        A correction is always a fresh dry-run. Keeping the record prevents a
        racing or retried apply from reusing authority whose checks did not
        complete.
        """

        with self._lock:
            record = self._record(plan_hash)
            if record.state == "applying":
                record.state = "aborted"
                record.result = _frozen_json({"error": "plan_aborted"})


__all__ = [
    "AddEdgeOp",
    "AddNodeOp",
    "AssistantOperationError",
    "DeleteEdgeOp",
    "DeleteNodeOp",
    "GraphEditOp",
    "GraphEditPlan",
    "OpValidationError",
    "PlanStore",
    "ProjectSourceEvidence",
    "ProjectSnapshot",
    "RenameNodeOp",
    "UpdateNodeOp",
    "UpdatePreambleOp",
    "apply_ops",
    "build_graph_edit_plan",
    "build_project_snapshot",
    "dataset_schema_digest",
    "parse_ops",
    "SemanticDiff",
    "semantic_diff",
    "verify_postconditions",
]
