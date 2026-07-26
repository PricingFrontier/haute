"""Read-only tools exposed to the pricing assistant.

The synchronous readers in this module deliberately return JSON-shaped
payloads.  The loop runs them in worker threads through ``build_tool_executor``
and therefore never has to know about route exceptions or Polars objects.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from functools import partial
from pathlib import Path
from typing import Any, cast

import polars as pl
from fastapi import HTTPException

from haute._event_bus import GraphUpdatePayload, default_bus
from haute._logging import get_logger
from haute._source_cache import SourceCacheError
from haute._types import GraphNode, NodeType, PipelineGraph
from haute.assistant._assets import load_example
from haute.assistant._catalog import NODE_CATALOG
from haute.assistant._config import mutations_readiness
from haute.assistant._ops import OpValidationError, apply_ops, parse_ops
from haute.errors import HauteError
from haute.execution import execute_lazy_graph
from haute.executor import (
    _build_node_fn,
    _compile_preamble,
    _pipeline_dir,
)
from haute.graph_utils import flatten_graph
from haute.routes._helpers import (
    parse_pipeline_to_graph,
    pipeline_dir,
    save_lock,
    validate_safe_path,
)
from haute.routes._save_pipeline import SavePipelineService

logger = get_logger(component="assistant.tools")

_INTERNAL_ERROR_DETAIL = "The assistant tool failed unexpectedly."
_DATASET_EXTENSIONS = frozenset({".parquet", ".csv", ".json", ".xml"})
_BOUNDARY_NODE_TYPES = frozenset({NodeType.SUBMODEL, NodeType.SUBMODEL_PORT})


def _schema_for_frame(frame: pl.LazyFrame) -> list[dict[str, str]]:
    """Render a lazy frame's schema without collecting rows."""

    return [{"name": name, "dtype": str(dtype)} for name, dtype in frame.collect_schema().items()]


def _error(code: str, message: str, **fields: object) -> dict[str, object]:
    return {"error": {"code": code, "message": message, **fields}}


def _error_message(exc: Exception, *, operation: str) -> str:
    """Keep analyst-facing Haute errors, but do not leak internal details."""

    if isinstance(exc, (HauteError, SourceCacheError)):
        return str(exc)
    if isinstance(exc, HTTPException):
        return str(exc.detail)
    logger.error(
        "assistant_tool_failed",
        operation=operation,
        error_class=type(exc).__name__,
        error_message=str(exc),
        exc_info=True,
    )
    return _INTERNAL_ERROR_DETAIL


def _node_id(raw: object) -> str | None:
    if isinstance(raw, GraphNode):
        return raw.id
    if isinstance(raw, Mapping):
        value = raw.get("id")
        return value if isinstance(value, str) else None
    return None


def _nested_graph_nodes(metadata: object) -> set[str]:
    if isinstance(metadata, Mapping):
        nested = metadata.get("graph")
    else:
        nested = getattr(metadata, "graph", None)
    if isinstance(nested, PipelineGraph):
        return {node.id for node in nested.nodes}
    if isinstance(nested, Mapping):
        raw_nodes = nested.get("nodes", [])
        if isinstance(raw_nodes, list):
            return {node_id for raw in raw_nodes if (node_id := _node_id(raw)) is not None}
    return set()


def _validate_top_level_target(graph: PipelineGraph, node: str) -> dict[str, object] | None:
    top_level = {candidate.id: candidate for candidate in graph.nodes}
    candidate = top_level.get(node)
    if candidate is not None:
        if candidate.data.nodeType in _BOUNDARY_NODE_TYPES:
            return _error(
                "submodel_boundary",
                f"Node {node!r} is a submodel boundary and cannot be inspected directly.",
            )
        return None

    nested_ids = {
        nested_id
        for metadata in (graph.submodels or {}).values()
        for nested_id in _nested_graph_nodes(metadata)
    }
    if node in nested_ids:
        return _error(
            "submodel_boundary",
            f"Node {node!r} is inside a submodel and cannot be inspected directly.",
        )
    return _error("unknown_node", f"Unknown node {node!r}.")


def get_node_schema(source_file: str, node: str) -> dict[str, object]:
    """Resolve one top-level executable node's output schema."""

    try:
        graph = parse_pipeline_to_graph(Path(source_file))
        validation_error = _validate_top_level_target(graph, node)
        if validation_error is not None:
            return validation_error

        flat = flatten_graph(graph)
        # Exactly the production preparation — no assistant-only recovery.  A
        # helper the parser does not classify as preamble is equally invisible
        # to explore/preview execution; diverging here would report schemas
        # the real engine cannot produce.
        preamble_ns = _compile_preamble(
            graph.preamble or "",
            pipeline_dir=_pipeline_dir(graph),
        )
        lazy_outputs, *_ = execute_lazy_graph(
            flat,
            _build_node_fn,
            target_node_id=node,
            preserve_node_ids={node},
            preamble_ns=preamble_ns or None,
            source=graph.active_source,
            enforce_contracts=True,
        )
        output = lazy_outputs[node]
        if isinstance(output, dict):
            return {
                "node": node,
                "ports": {port: _schema_for_frame(frame) for port, frame in output.items()},
            }
        return {"node": node, "columns": _schema_for_frame(output)}
    except Exception as exc:  # noqa: BLE001 - tool boundary must not raise
        return _error(
            "schema_unresolvable",
            _error_message(exc, operation="get_node_schema"),
        )


def _node_type(node: GraphNode) -> str:
    value = node.data.nodeType
    return value.value if isinstance(value, NodeType) else str(value)


def _render_config_summary(config: Mapping[str, Any]) -> dict[str, object]:
    return {"keys": sorted(config), "count": len(config)}


def render_pipeline_graph(graph: PipelineGraph) -> dict[str, object]:
    """Render the compact graph shape shared by live pipelines and examples."""

    nodes = [
        {
            "id": node.id,
            "type": _node_type(node),
            "label": node.data.label,
            "config": _render_config_summary(node.data.config),
        }
        for node in graph.nodes
    ]
    edges = [
        {
            "id": edge.id,
            "source": edge.source,
            "target": edge.target,
            "sourceHandle": edge.sourceHandle,
            "targetHandle": edge.targetHandle,
        }
        for edge in graph.edges
    ]
    singletons = {
        entry.node_type.value: any(
            _node_type(node) == entry.node_type.value for node in graph.nodes
        )
        for entry in NODE_CATALOG.values()
        if entry.singleton
    }
    return {
        "name": graph.pipeline_name,
        "description": graph.pipeline_description,
        "nodes": nodes,
        "edges": edges,
        "preamble": graph.preamble or "",
        "singletons": singletons,
    }


def _parse_graph(source_file: str) -> PipelineGraph:
    return parse_pipeline_to_graph(Path(source_file))


def get_pipeline(source_file: str) -> dict[str, object]:
    """Return the saved graph in the assistant's compact graph shape."""

    try:
        return render_pipeline_graph(_parse_graph(source_file))
    except Exception as exc:  # noqa: BLE001 - structured tool boundary
        return _error("pipeline_unavailable", _error_message(exc, operation="get_pipeline"))


def get_node_config(source_file: str, node: str) -> dict[str, object]:
    """Return one top-level node's complete saved configuration."""

    try:
        graph = _parse_graph(source_file)
        validation_error = _validate_top_level_target(graph, node)
        if validation_error is not None:
            return validation_error
        candidate = next(candidate for candidate in graph.nodes if candidate.id == node)
        return {"node": node, "config": dict(candidate.data.config)}
    except Exception as exc:  # noqa: BLE001 - structured tool boundary
        return _error("node_config_unavailable", _error_message(exc, operation="get_node_config"))


def list_node_types() -> dict[str, object]:
    """Return the complete derived node catalog."""

    return {"node_types": [entry.as_dict() for entry in NODE_CATALOG.values()]}


def _dataset_item(path: Path, base: Path) -> dict[str, object]:
    return {
        "name": path.name,
        # POSIX separators keep model-facing paths identical across platforms.
        "path": path.relative_to(base).as_posix(),
        "type": "file",
        "size": path.stat().st_size,
    }


def list_datasets(project_root: str | None = None) -> dict[str, object]:
    """List visible project data files using the files-route allowlist."""

    try:
        base = Path.cwd().resolve()
        target = validate_safe_path(base, project_root or ".")
        if not target.is_dir():
            return _error("directory_not_found", f"Directory not found: {project_root or '.'}.")
        entries = [entry for entry in sorted(target.iterdir()) if not entry.name.startswith(".")]
        datasets = [
            _dataset_item(entry, base)
            for entry in entries
            if entry.is_file() and entry.suffix.lower() in _DATASET_EXTENSIONS
        ]
        directories = [entry.relative_to(base).as_posix() for entry in entries if entry.is_dir()]
        return {"datasets": datasets, "directories": directories}
    except Exception as exc:  # noqa: BLE001 - structured tool boundary
        return _error("dataset_list_unavailable", _error_message(exc, operation="list_datasets"))


def get_dataset_schema(path: str) -> dict[str, object]:
    """Return a dataset's schema and preview through the files route reader."""

    try:
        base = Path.cwd().resolve()
        target = validate_safe_path(base, path)
        if not target.is_file():
            return _error("dataset_not_found", f"File not found: {path}.")
        from haute.routes.files import _read_schema_blocking

        response = _read_schema_blocking(path, target)
        return response.model_dump(mode="json")
    except Exception as exc:  # noqa: BLE001 - structured tool boundary
        return _error(
            "dataset_schema_unavailable",
            _error_message(exc, operation="get_dataset_schema"),
        )


def get_example(name: str) -> dict[str, object]:
    """Return one packaged exemplar, rendered like a live pipeline."""

    try:
        return load_example(name)
    except Exception as exc:  # noqa: BLE001 - structured tool boundary
        return _error("example_unavailable", _error_message(exc, operation="get_example"))


def _graph_edit_schema() -> dict[str, object]:
    """Return the provider-facing schema for the ordered graph-edit batch."""

    ref = {
        "type": "string",
        "description": "Node id, or a batch-local $ref declared by an earlier add_node.",
    }
    return {
        "type": "array",
        "description": (
            "Ordered graph edits. An add_node may declare ref; later node and edge fields "
            "may use $ref to address that node."
        ),
        "items": {
            "oneOf": [
                {
                    "type": "object",
                    "properties": {
                        "op": {"const": "add_node"},
                        "node_type": {"type": "string"},
                        "name": {"type": "string"},
                        "config": {"type": "object"},
                        "ref": {"type": "string"},
                    },
                    "required": ["op", "node_type", "name"],
                    "additionalProperties": False,
                },
                {
                    "type": "object",
                    "properties": {
                        "op": {"const": "update_node"},
                        "node": ref,
                        "config": {"type": "object"},
                    },
                    "required": ["op", "node", "config"],
                    "additionalProperties": False,
                },
                {
                    "type": "object",
                    "properties": {
                        "op": {"const": "rename_node"},
                        "node": ref,
                        "new_name": {"type": "string"},
                    },
                    "required": ["op", "node", "new_name"],
                    "additionalProperties": False,
                },
                {
                    "type": "object",
                    "properties": {"op": {"const": "delete_node"}, "node": ref},
                    "required": ["op", "node"],
                    "additionalProperties": False,
                },
                {
                    "type": "object",
                    "properties": {
                        "op": {"const": "add_edge"},
                        "source": ref,
                        "target": ref,
                        "source_handle": {"type": ["string", "null"]},
                        "target_handle": {"type": ["string", "null"]},
                    },
                    "required": ["op", "source", "target"],
                    "additionalProperties": False,
                },
                {
                    "type": "object",
                    "properties": {
                        "op": {"const": "delete_edge"},
                        "source": ref,
                        "target": ref,
                        "source_handle": {"type": ["string", "null"]},
                        "target_handle": {"type": ["string", "null"]},
                    },
                    "required": ["op", "source", "target"],
                    "additionalProperties": False,
                },
                {
                    "type": "object",
                    "properties": {
                        "op": {"const": "update_preamble"},
                        "preamble": {"type": ["string", "null"]},
                    },
                    "required": ["op", "preamble"],
                    "additionalProperties": False,
                },
            ]
        },
    }


def _publish_graph_update(source_file: str, graph: PipelineGraph) -> str:
    """Publish the exact graph-update payload used by the file watcher."""

    from haute.server import _graph_payload_fingerprint, _wire_source_file

    graph_payload = graph.model_dump()
    fingerprint = _graph_payload_fingerprint(graph_payload)
    payload: dict[str, Any] = {
        "graph": graph_payload,
        "graph_fingerprint": fingerprint,
        "source_file": _wire_source_file(Path(source_file)),
    }
    default_bus.publish(
        "graph.update",
        cast(GraphUpdatePayload, payload),
    )
    return fingerprint


def _apply_graph_edits_blocking(
    source_file: str,
    ops_payload: object,
) -> tuple[PipelineGraph, int, list[str]]:
    """Run parse -> apply -> save -> re-parse synchronously in a worker thread.

    Publishing is deliberately NOT done here: the WebSocket broadcast
    subscriber schedules onto the running event loop and silently skips
    when publish happens on a loop-less worker thread — the caller must
    publish on the event-loop thread while still holding ``save_lock``.
    """

    graph = parse_pipeline_to_graph(Path(source_file))
    parsed_ops = parse_ops(ops_payload)  # type: ignore[arg-type]
    applied_graph = apply_ops(graph, parsed_ops)

    service = SavePipelineService(
        project_root=Path.cwd(),
        pipeline_root=pipeline_dir(),
    )
    response = service.save_graph_transactionally(
        graph=applied_graph,
        name=applied_graph.pipeline_name or "",
        description=applied_graph.pipeline_description or "",
        preamble=applied_graph.preamble,
        source_file=source_file,
    )
    reparsed = parse_pipeline_to_graph(Path(source_file))
    return reparsed, len(parsed_ops), list(response.warnings or [])


async def apply_graph_edits(
    source_file: str,
    ops_payload: object,
) -> dict[str, object]:
    """Apply and publish one transactional batch of graph edits."""

    try:
        mutations_enabled, mutations_reason = mutations_readiness(Path.cwd())
        if not mutations_enabled:
            return _error(
                "mutations_disabled",
                mutations_reason or "Assistant mutations are not enabled for this project.",
            )

        async with save_lock:
            reparsed, applied, warnings = await asyncio.to_thread(
                _apply_graph_edits_blocking,
                source_file,
                ops_payload,
            )
            # On the event-loop thread, still inside the critical section:
            # the /ws/sync subscriber needs a running loop to schedule its
            # broadcast, and the lock must span the publish per the spec.
            fingerprint = _publish_graph_update(source_file, reparsed)

        result: dict[str, object] = {
            "applied": applied,
            "graph_fingerprint": fingerprint,
            "nodes": len(reparsed.nodes),
        }
        if warnings:
            result["warnings"] = warnings
        return result
    except OpValidationError as exc:
        return _error("invalid_ops", str(exc))
    except Exception as exc:  # noqa: BLE001 - tool boundary must not raise
        return _error("mutation_failed", _error_message(exc, operation="apply_graph_edits"))


TOOL_DEFINITIONS: list[dict[str, object]] = [
    {
        "name": "get_pipeline",
        "description": "Inspect the saved pipeline graph.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_node_schema",
        "description": "Resolve the output columns and dtypes for a pipeline node.",
        "input_schema": {
            "type": "object",
            "properties": {"node": {"type": "string"}},
            "required": ["node"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_node_config",
        "description": "Inspect one node's complete saved configuration.",
        "input_schema": {
            "type": "object",
            "properties": {"node": {"type": "string"}},
            "required": ["node"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_node_types",
        "description": "List every supported node type and its configuration vocabulary.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "list_datasets",
        "description": (
            "List data files in one project directory (non-recursive; defaults to the "
            "project root). The result also names visible subdirectories — pass one as "
            "project_root to look inside it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"project_root": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "get_dataset_schema",
        "description": "Read a dataset's column names and dtypes.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_example",
        "description": "Load a packaged exemplar pipeline and its authoring narrative.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "apply_graph_edits",
        "description": "Apply an ordered batch of graph edits transactionally.",
        "input_schema": {
            "type": "object",
            "properties": {"ops": _graph_edit_schema()},
            "required": ["ops"],
            "additionalProperties": False,
        },
    },
]

_TOOL_NAMES = tuple(str(definition["name"]) for definition in TOOL_DEFINITIONS)


def _dispatch_error(name: str, message: str) -> dict[str, object]:
    return _error("unknown_tool", message, name=name, valid_names=list(_TOOL_NAMES))


def build_tool_executor(
    source_file: str,
) -> Callable[[str, dict[str, Any]], Awaitable[Mapping[str, object]]]:
    """Build the loop's non-raising, source-bound async tool dispatcher."""

    async def execute_tool(name: str, arguments: dict[str, Any]) -> Mapping[str, object]:
        if name not in _TOOL_NAMES:
            return _dispatch_error(
                name,
                f"Unknown assistant tool {name!r}. Choose one of: {', '.join(_TOOL_NAMES)}.",
            )
        if name == "apply_graph_edits":
            return await apply_graph_edits(source_file, arguments.get("ops"))

        try:
            operation: Callable[[], dict[str, object]]
            if name == "get_pipeline":
                operation = partial(get_pipeline, source_file)
            elif name == "get_node_schema":
                operation = partial(get_node_schema, source_file, arguments["node"])
            elif name == "get_node_config":
                operation = partial(get_node_config, source_file, arguments["node"])
            elif name == "list_node_types":
                operation = list_node_types
            elif name == "list_datasets":
                operation = partial(list_datasets, arguments.get("project_root"))
            elif name == "get_dataset_schema":
                operation = partial(get_dataset_schema, arguments["path"])
            elif name == "get_example":
                operation = partial(get_example, arguments["name"])
            else:  # pragma: no cover - guarded by _TOOL_NAMES
                return _dispatch_error(name, f"Unknown assistant tool {name!r}.")
            result = await asyncio.to_thread(operation)
            return result
        except Exception as exc:  # noqa: BLE001 - executor must never raise
            return _error("tool_failed", _error_message(exc, operation=name))

    return execute_tool


__all__ = [
    "TOOL_DEFINITIONS",
    "apply_graph_edits",
    "build_tool_executor",
    "get_dataset_schema",
    "get_example",
    "get_node_config",
    "get_node_schema",
    "get_pipeline",
    "list_datasets",
    "list_node_types",
    "render_pipeline_graph",
]
