"""Read-only tools exposed to the pricing assistant.

The synchronous readers in this module deliberately return JSON-shaped
payloads.  The loop runs them in worker threads through ``build_tool_executor``
and therefore never has to know about route exceptions or Polars objects.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from functools import partial
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

import polars as pl
from fastapi import HTTPException

from haute._credential_security import is_credential_name
from haute._event_bus import GraphUpdatePayload, default_bus
from haute._logging import get_logger
from haute._source_cache import SourceCacheError
from haute._types import GraphNode, NodeType, PipelineGraph
from haute.assistant._application import CommittedVerificationError, PipelineApplicationService
from haute.assistant._assets import authoring_guide, load_example
from haute.assistant._catalog import (
    NODE_CATALOG,
    capability_manifest,
    compact_manifest,
    materialise_json,
)
from haute.assistant._config import mutations_readiness, resolve_egress_policy
from haute.assistant._ops import (
    AssistantOperationError,
    OpValidationError,
    PlanStore,
    ProjectSourceEvidence,
    build_project_snapshot,
    dataset_schema_digest,
)
from haute.assistant._project_knowledge import build_project_knowledge, query_project_knowledge
from haute.assistant._recipes import (
    RecipeError,
    explicit_dataset_directory,
    explicit_primary_recipe_name,
    request_requires_material_clarification,
    route_recipe_request,
)
from haute.assistant._recipes import plan_recipe as _plan_recipe
from haute.assistant._render import render_pipeline_graph
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

logger = get_logger(component="assistant.tools")

_INTERNAL_ERROR_DETAIL = "The assistant tool failed unexpectedly."
_DENIED_DATASET_NAMES = frozenset(
    {
        "application_default_credentials.json",
        "credentials.json",
        "secrets.json",
        "service-account.json",
        "service_account.json",
    }
)
_DENIED_DATASET_DIRECTORIES = frozenset({"credentials", "secrets"})
_BOUNDARY_NODE_TYPES = frozenset({NodeType.SUBMODEL, NodeType.SUBMODEL_PORT})
_PLAN_STORE = PlanStore()
_MAX_TOOL_PAYLOAD_BYTES = 1_000_000
_MAX_TOOL_CONTEXT_BYTES = 256_000
_MAX_LISTED_DATASETS = 200
_MAX_LISTED_DATASET_DIRECTORIES = 200
_MAX_VISITED_DATASET_DIRECTORIES = 500
_INTERNAL_PROJECT_TOOLS = frozenset(
    {
        "get_pipeline",
        "get_node_schema",
        "get_node_config",
        "list_datasets",
        "get_dataset_schema",
        "dry_run_recipe_plan",
        "dry_run_graph_edits",
        "apply_graph_plan",
    }
)


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
        graph = _parse_graph(source_file)
        validation_error = _validate_top_level_target(graph, node)
        if validation_error is not None:
            return validation_error
        project_revision = _project_revision(source_file, graph)

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
                "project_revision": project_revision,
            }
        return {
            "node": node,
            "columns": _schema_for_frame(output),
            "project_revision": project_revision,
        }
    except Exception as exc:  # noqa: BLE001 - tool boundary must not raise
        return _error(
            "schema_unresolvable",
            _error_message(exc, operation="get_node_schema"),
        )


_EXECUTABLE_CONFIG_KEYS = frozenset({"code", "preamble", "query", "script"})
_ROW_VALUE_CONFIG_KEYS = frozenset({"records"})


def _redact_config_value(value: object, *, key: str | None = None) -> object:
    if key is not None:
        if is_credential_name(key):
            return "<redacted: credential>"
        if key.casefold() in _EXECUTABLE_CONFIG_KEYS:
            return "<redacted: executable_source>"
        if key.casefold() in _ROW_VALUE_CONFIG_KEYS:
            return "<redacted: row_values>"
    if isinstance(value, Mapping):
        return {
            str(child_key): _redact_config_value(child_value, key=str(child_key))
            for child_key, child_value in value.items()
        }
    if isinstance(value, list | tuple):
        return [_redact_config_value(child) for child in value]
    return value


def _parse_graph(source_file: str) -> PipelineGraph:
    return parse_pipeline_to_graph(Path(source_file))


def _project_revision(
    source_file: str,
    graph: PipelineGraph,
    project_sources: tuple[Path | ProjectSourceEvidence, ...] = (),
) -> str:
    project_root = Path.cwd().resolve()
    source = Path(source_file)
    if not source.is_absolute():
        source = project_root / source
    return build_project_snapshot(
        project_root,
        source,
        graph,
        project_sources,
    ).revision


def get_pipeline(source_file: str) -> dict[str, object]:
    """Return the saved graph in the assistant's compact graph shape."""

    try:
        graph = _parse_graph(source_file)
        project_revision = _project_revision(source_file, graph)
        return {
            **render_pipeline_graph(graph),
            "project_revision": project_revision,
        }
    except Exception as exc:  # noqa: BLE001 - structured tool boundary
        return _error("pipeline_unavailable", _error_message(exc, operation="get_pipeline"))


def get_node_config(source_file: str, node: str) -> dict[str, object]:
    """Return policy-eligible config with executable and secret values redacted."""

    try:
        policy = resolve_egress_policy(Path.cwd().resolve())
        if policy.max_sensitivity != "restricted":
            return _error(
                "egress_policy_denied",
                "Saved node configuration is restricted and exceeds the provider policy.",
                required_sensitivity="restricted",
                max_sensitivity=policy.max_sensitivity,
            )
        graph = _parse_graph(source_file)
        validation_error = _validate_top_level_target(graph, node)
        if validation_error is not None:
            return validation_error
        project_revision = _project_revision(source_file, graph)
        candidate = next(candidate for candidate in graph.nodes if candidate.id == node)
        return {
            "node": node,
            "sensitivity": "restricted",
            "config": _redact_config_value(candidate.data.config),
            "project_revision": project_revision,
        }
    except Exception as exc:  # noqa: BLE001 - structured tool boundary
        return _error("node_config_unavailable", _error_message(exc, operation="get_node_config"))


def list_node_types() -> dict[str, object]:
    """Return the manifest-backed legacy node-catalogue compatibility view."""

    manifest_nodes = {descriptor.id: descriptor for descriptor in capability_manifest().nodes}
    return {
        "node_types": [
            {
                **entry.as_dict(),
                "config_schema": manifest_nodes[entry.node_type.value].as_dict()["config_schema"],
            }
            for entry in NODE_CATALOG.values()
        ]
    }


def get_capability_manifest() -> dict[str, object]:
    """Return manifest identity and the bounded descriptor index."""

    return compact_manifest(capability_manifest())


def get_capability_descriptors(
    kind: str,
    descriptor_ids: Sequence[str],
) -> dict[str, object]:
    """Return one ordered, all-or-nothing batch of capability descriptors."""

    valid_kinds = ("node", "operation", "recipe")
    if kind not in valid_kinds:
        return _error(
            "unsupported_capability",
            "The requested capability kind is not installed.",
            kind=kind,
            valid_kinds=list(valid_kinds),
        )
    if (
        isinstance(descriptor_ids, str)
        or not isinstance(descriptor_ids, Sequence)
        or not 1 <= len(descriptor_ids) <= 12
        or any(
            not isinstance(descriptor_id, str) or not descriptor_id
            for descriptor_id in descriptor_ids
        )
        or len(set(descriptor_ids)) != len(descriptor_ids)
    ):
        return _error(
            "invalid_capability_query",
            "Capability ids must contain one to twelve unique non-empty strings.",
            kind=kind,
        )

    manifest = capability_manifest()
    if kind == "node":
        descriptors = {descriptor.id: descriptor.as_dict() for descriptor in manifest.nodes}
    elif kind == "operation":
        descriptors = {descriptor.id: descriptor.as_dict() for descriptor in manifest.operations}
    else:
        descriptors = {
            str(descriptor["id"]): cast(dict[str, object], materialise_json(descriptor))
            for descriptor in manifest.recipes
        }

    unknown_ids = [
        descriptor_id for descriptor_id in descriptor_ids if descriptor_id not in descriptors
    ]
    if unknown_ids:
        return _error(
            "unsupported_capability",
            f"One or more requested {kind} capabilities are not installed.",
            kind=kind,
            id=unknown_ids[0],
            valid_ids=sorted(descriptors),
        )
    ordered = [descriptors[descriptor_id] for descriptor_id in descriptor_ids]
    return {"kind": kind, "count": len(ordered), "descriptors": ordered}


def plan_recipe(recipe_id: str, arguments: object) -> dict[str, object]:
    """Expand one deterministic recipe without reading or writing project state."""

    try:
        return _plan_recipe(recipe_id, arguments)
    except RecipeError as exc:
        return _error(exc.code, str(exc), **dict(exc.context))


def _dataset_item(path: Path, base: Path) -> dict[str, object]:
    return {
        "name": path.name,
        # POSIX separators keep model-facing paths identical across platforms.
        "path": path.relative_to(base).as_posix(),
        "type": "file",
        "size": path.stat().st_size,
    }


def _dataset_path_forbidden(path: Path, base: Path) -> bool:
    relative = path.relative_to(base)
    parts = tuple(part.casefold() for part in relative.parts)
    if any(part.startswith(".") for part in parts):
        return True
    if any(part in _DENIED_DATASET_DIRECTORIES for part in parts):
        return True
    return bool(parts and parts[-1] in _DENIED_DATASET_NAMES)


def _dataset_extensions() -> tuple[str, ...]:
    from haute.routes.files import _installed_input_extensions

    return _installed_input_extensions()


def _has_dataset_extension(path: Path, extensions: tuple[str, ...]) -> bool:
    name = path.name.casefold()
    return any(name.endswith(extension) for extension in extensions)


def _dataset_listing(
    target: Path,
    base: Path,
    *,
    recursive: bool,
) -> tuple[list[dict[str, object]], list[str], bool]:
    extensions = _dataset_extensions()
    datasets: list[dict[str, object]] = []
    directories: list[str] = []
    pending = [target]
    visited = 0
    truncated = False

    while pending:
        current = pending.pop()
        visited += 1
        if visited > _MAX_VISITED_DATASET_DIRECTORIES:
            truncated = True
            break
        entries = sorted(
            current.iterdir(),
            key=lambda candidate: candidate.relative_to(base).as_posix().casefold(),
        )
        child_directories: list[Path] = []
        for entry in entries:
            if entry.is_symlink() or _dataset_path_forbidden(entry, base):
                continue
            if entry.is_dir():
                if len(directories) < _MAX_LISTED_DATASET_DIRECTORIES:
                    directories.append(entry.relative_to(base).as_posix())
                else:
                    truncated = True
                if recursive:
                    child_directories.append(entry)
                continue
            if entry.is_file() and _has_dataset_extension(entry, extensions):
                if len(datasets) < _MAX_LISTED_DATASETS:
                    datasets.append(_dataset_item(entry, base))
                else:
                    truncated = True
        if recursive:
            pending.extend(reversed(child_directories))

    datasets.sort(key=lambda item: str(item["path"]).casefold())
    directories.sort(key=str.casefold)
    return datasets, directories, truncated


def list_datasets(
    project_root: str | None = None,
    *,
    recursive: bool = False,
) -> dict[str, object]:
    """List safe project data files using the installed input registry."""

    try:
        if not isinstance(recursive, bool):
            return _error("invalid_request", "recursive must be a boolean.")
        base = Path.cwd().resolve()
        target = validate_safe_path(base, project_root or ".")
        if _dataset_path_forbidden(target, base):
            return _error(
                "dataset_path_forbidden",
                "Hidden, state, and credential paths are unavailable to assistant dataset tools.",
            )
        if not target.is_dir():
            return _error("directory_not_found", f"Directory not found: {project_root or '.'}.")
        datasets, directories, truncated = _dataset_listing(
            target,
            base,
            recursive=recursive,
        )
        return {
            "datasets": datasets,
            "directories": directories,
            "recursive": recursive,
            "truncated": truncated,
        }
    except Exception as exc:  # noqa: BLE001 - structured tool boundary
        return _error("dataset_list_unavailable", _error_message(exc, operation="list_datasets"))


def get_dataset_schema(
    path: str,
    *,
    source_file: str | None = None,
) -> dict[str, object]:
    """Return a dataset's schema without materialising or returning row values."""

    try:
        base = Path.cwd().resolve()
        target = validate_safe_path(base, path)
        if _dataset_path_forbidden(target, base):
            return _error(
                "dataset_path_forbidden",
                "Hidden, state, and credential paths are unavailable to assistant dataset tools.",
            )
        if not target.is_file():
            return _error("dataset_not_found", f"File not found: {path}.")
        if not _has_dataset_extension(target, _dataset_extensions()):
            return _error(
                "dataset_format_unsupported",
                "The dataset format is not available in this Haute installation.",
            )
        from haute.routes.files import _read_schema_only_blocking

        result = _read_schema_only_blocking(path, target)
        result["source_digest"] = dataset_schema_digest(result)
        if source_file is not None:
            evidence = ProjectSourceEvidence(
                path=target,
                digest=result["source_digest"],  # type: ignore[arg-type]
                kind="schema",
            )
            graph = _parse_graph(source_file)
            project_revision = _project_revision(
                source_file,
                graph,
                (evidence,),
            )
            result["project_revision"] = project_revision
        return result
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


def get_authoring_guide() -> dict[str, object]:
    """Return attributable canonical guidance only when the model requests it."""

    try:
        content = authoring_guide()
        digest = sha256(content.encode("utf-8")).hexdigest()
        return {
            "id": "haute-authoring-guide",
            "version": "1",
            "sha256": digest,
            "source": "package:haute.assistant/assets/authoring_guide.md",
            "sensitivity": "public",
            "evidence_class": "canonical_library_guidance",
            "approval_status": "reviewed",
            "content": content,
        }
    except Exception as exc:  # noqa: BLE001 - structured tool boundary
        return _error(
            "authoring_guide_unavailable",
            _error_message(exc, operation="get_authoring_guide"),
        )


def get_project_knowledge(
    source_file: str,
    query: str,
    *,
    limit: int = 5,
) -> dict[str, object]:
    """Return bounded source-linked facts allowed by the effective egress policy."""

    try:
        project_root = Path.cwd().resolve()
        policy = resolve_egress_policy(project_root)
        view = build_project_knowledge(project_root, source_file, policy=policy)
        items = query_project_knowledge(view, query, limit=limit)
        for item in items:
            if not isinstance(item.get("source"), str) or not isinstance(
                item.get("source_digest"), str
            ):
                raise ValueError("Project knowledge item attribution is invalid")
        returned_evidence = tuple(
            ProjectSourceEvidence(
                path=project_root / cast(str, item["source"]),
                digest=cast(str, item["source_digest"]),
                kind="content",
            )
            for item in items
        )
        graph = _parse_graph(source_file)
        project_revision = _project_revision(
            source_file,
            graph,
            returned_evidence,
        )
        return {
            "items": list(items),
            "excluded_by_policy_count": len(view.excluded_by_policy),
            "cache_hit": view.cache_hit,
            "policy_hash": policy.policy_hash,
            "trust": policy.trust,
            "max_sensitivity": policy.max_sensitivity,
            "project_revision": project_revision,
        }
    except ValueError as exc:
        return _error("invalid_project_knowledge_query", str(exc))
    except Exception as exc:  # noqa: BLE001 - structured tool boundary
        return _error(
            "project_knowledge_unavailable",
            _error_message(exc, operation="get_project_knowledge"),
        )


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


def _application_service(
    project_sources: tuple[Path | ProjectSourceEvidence, ...] = (),
) -> PipelineApplicationService:
    project_root = Path.cwd().resolve()

    return PipelineApplicationService(
        project_root=project_root,
        pipeline_root=pipeline_dir(),
        mutations_readiness=mutations_readiness,
        publish_graph_update=_publish_graph_update,
        plan_store=_PLAN_STORE,
        parse_graph=parse_pipeline_to_graph,
        project_sources=lambda _source_file: project_sources,
    )


def _operation_error(exc: AssistantOperationError) -> dict[str, object]:
    return _error(exc.code, str(exc))


async def dry_run_graph_edits(
    source_file: str,
    ops_payload: object,
    *,
    postconditions: object = (),
    project_sources: tuple[Path | ProjectSourceEvidence, ...] = (),
) -> dict[str, object]:
    """Validate and retain an exact graph-edit plan without writing."""

    try:
        async with save_lock:
            plan = await asyncio.to_thread(
                _application_service(project_sources).dry_run,
                source_file,
                ops_payload,  # type: ignore[arg-type]
                postconditions=postconditions,  # type: ignore[arg-type]
            )
        return plan.as_dict()
    except OpValidationError as exc:
        return _error("invalid_ops", str(exc))
    except AssistantOperationError as exc:
        return _operation_error(exc)
    except Exception as exc:  # noqa: BLE001 - tool boundary must not raise
        return _error("invalid_plan", _error_message(exc, operation="dry_run_graph_edits"))


async def apply_graph_plan(
    source_file: str,
    plan_hash: str,
) -> dict[str, object]:
    """Apply and verify one exact, single-use plan."""

    try:
        result = await _application_service().apply(
            source_file,
            plan_hash,
        )
        return result.as_dict()
    except CommittedVerificationError as exc:
        return _error(exc.code, str(exc), **exc.result)
    except AssistantOperationError as exc:
        return _operation_error(exc)
    except Exception as exc:  # noqa: BLE001 - tool boundary must not raise
        return _error("mutation_failed", _error_message(exc, operation="apply_graph_plan"))


TOOL_DEFINITIONS: list[dict[str, object]] = [
    {
        "name": descriptor.id,
        "description": descriptor.description,
        "input_schema": descriptor.as_dict()["input_schema"],
    }
    for descriptor in capability_manifest().operations
]

_TOOL_NAMES = tuple(str(definition["name"]) for definition in TOOL_DEFINITIONS)
_OPERATION_VERSIONS = {
    descriptor.id: descriptor.version for descriptor in capability_manifest().operations
}
_OPERATION_INPUT_SCHEMAS = {
    descriptor.id: descriptor.input_schema for descriptor in capability_manifest().operations
}


def _dispatch_error(name: str, message: str) -> dict[str, object]:
    return _error("unknown_tool", message, name=name, valid_names=list(_TOOL_NAMES))


def _json_size(value: object) -> int:
    return len(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    )


def _has_duplicate_items(value: list[object]) -> bool:
    """Report repeats by canonical encoding, because members may be unhashable."""

    seen: set[str] = set()
    for item in value:
        encoded = json.dumps(
            item,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        if encoded in seen:
            return True
        seen.add(encoded)
    return False


class _ToolArgumentValidationError(ValueError):
    """A value did not satisfy one closed operation input schema."""

    __slots__ = ("path", "reason")

    def __init__(self, path: str, reason: str, message: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(message)


def _json_type_matches(value: object, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, Mapping)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return type(value) is int
    if expected == "number":
        return not isinstance(value, bool) and isinstance(value, int | float)
    if expected == "boolean":
        return type(value) is bool
    if expected == "null":
        return value is None
    raise RuntimeError(f"Unsupported operation-schema JSON type: {expected!r}")


def _validate_tool_value(
    value: object,
    schema: Mapping[str, object],
    *,
    path: str,
) -> None:
    """Validate the JSON-Schema subset emitted by the capability registry."""

    for union_keyword, exact in (("oneOf", True), ("anyOf", False)):
        raw_variants = schema.get(union_keyword)
        if raw_variants is None:
            continue
        if (
            not isinstance(raw_variants, (list, tuple))
            or not raw_variants
            or not all(isinstance(variant, Mapping) for variant in raw_variants)
        ):
            raise RuntimeError(f"Invalid operation schema at {path}.{union_keyword}")
        variants = tuple(cast(Mapping[str, object], variant) for variant in raw_variants)
        discriminator = _variant_discriminator(variants)
        if discriminator is not None and isinstance(value, Mapping):
            discriminator_path = f"{path}.{discriminator}"
            if discriminator not in value:
                raise _ToolArgumentValidationError(
                    discriminator_path,
                    "missing_discriminator",
                    f"{discriminator_path} is required to select an operation variant",
                )
            selected = [
                variant
                for variant in variants
                if value[discriminator]
                in cast(
                    tuple[object, ...],
                    _schema_allowed_values(
                        cast(Mapping[str, object], variant["properties"])[discriminator]
                    ),
                )
            ]
            if len(selected) != 1:
                raise _ToolArgumentValidationError(
                    discriminator_path,
                    "unsupported_discriminator",
                    f"{discriminator_path} does not identify a supported operation variant",
                )
            _validate_tool_value(value, selected[0], path=path)
            return

        matches = 0
        for variant in variants:
            try:
                _validate_tool_value(value, variant, path=path)
            except _ToolArgumentValidationError:
                continue
            matches += 1
        if matches == 0 or (exact and matches != 1):
            raise _ToolArgumentValidationError(
                path,
                "union_mismatch",
                f"{path} does not match the closed {union_keyword} schema",
            )
        return

    if "const" in schema and value != schema["const"]:
        raise _ToolArgumentValidationError(
            path,
            "unsupported_constant",
            f"{path} has an unsupported constant value",
        )
    raw_enum = schema.get("enum")
    if raw_enum is not None:
        if not isinstance(raw_enum, (list, tuple)) or value not in raw_enum:
            raise _ToolArgumentValidationError(
                path,
                "unsupported_value",
                f"{path} has an unsupported value",
            )

    raw_type = schema.get("type")
    expected_types: tuple[str, ...] = ()
    if isinstance(raw_type, str):
        expected_types = (raw_type,)
    elif isinstance(raw_type, (list, tuple)) and all(isinstance(item, str) for item in raw_type):
        expected_types = tuple(raw_type)
    elif raw_type is not None:
        raise RuntimeError(f"Invalid operation schema type at {path}")
    if expected_types and not any(
        _json_type_matches(value, expected) for expected in expected_types
    ):
        raise _ToolArgumentValidationError(
            path,
            "wrong_type",
            f"{path} has the wrong JSON type",
        )

    if isinstance(value, Mapping) and "object" in expected_types:
        raw_properties = schema.get("properties", {})
        raw_required = schema.get("required", ())
        if not isinstance(raw_properties, Mapping) or not isinstance(raw_required, (list, tuple)):
            raise RuntimeError(f"Invalid operation object schema at {path}")
        required = {str(item) for item in raw_required}
        missing = sorted(required.difference(value))
        if missing:
            missing_path = f"{path}.{missing[0]}"
            raise _ToolArgumentValidationError(
                missing_path,
                "missing_required",
                f"{missing_path} is required",
            )
        unknown = sorted(str(key) for key in value if key not in raw_properties)
        additional = schema.get("additionalProperties", True)
        if unknown and additional is False:
            raise _ToolArgumentValidationError(
                path,
                "unknown_field",
                f"{path} contains a field that is not allowed",
            )
        for key, item in value.items():
            property_schema = raw_properties.get(key)
            if isinstance(property_schema, Mapping):
                _validate_tool_value(item, property_schema, path=f"{path}.{key}")
            elif key not in raw_properties and isinstance(additional, Mapping):
                _validate_tool_value(item, additional, path=f"{path}.{key}")

    if isinstance(value, list) and "array" in expected_types:
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if isinstance(minimum, int) and len(value) < minimum:
            raise _ToolArgumentValidationError(
                path,
                "too_few_items",
                f"{path} has too few items",
            )
        if isinstance(maximum, int) and len(value) > maximum:
            raise _ToolArgumentValidationError(
                path,
                "too_many_items",
                f"{path} has too many items",
            )
        if schema.get("uniqueItems") is True and _has_duplicate_items(value):
            raise _ToolArgumentValidationError(
                path,
                "duplicate_items",
                f"{path} contains duplicate items",
            )
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(value):
                _validate_tool_value(item, item_schema, path=f"{path}[{index}]")

    if isinstance(value, str) and "string" in expected_types:
        minimum_length = schema.get("minLength")
        maximum_length = schema.get("maxLength")
        if isinstance(minimum_length, int) and len(value) < minimum_length:
            raise _ToolArgumentValidationError(
                path,
                "too_short",
                f"{path} is too short",
            )
        if isinstance(maximum_length, int) and len(value) > maximum_length:
            raise _ToolArgumentValidationError(
                path,
                "too_long",
                f"{path} is too long",
            )
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            raise _ToolArgumentValidationError(
                path,
                "pattern_mismatch",
                f"{path} does not match its required pattern",
            )

    if not isinstance(value, bool) and isinstance(value, int | float):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, int | float) and value < minimum:
            raise _ToolArgumentValidationError(
                path,
                "below_minimum",
                f"{path} is below its minimum",
            )
        if isinstance(maximum, int | float) and value > maximum:
            raise _ToolArgumentValidationError(
                path,
                "above_maximum",
                f"{path} is above its maximum",
            )


def _schema_allowed_values(schema: object) -> tuple[object, ...] | None:
    if not isinstance(schema, Mapping):
        return None
    if "const" in schema:
        return (schema["const"],)
    raw_enum = schema.get("enum")
    if isinstance(raw_enum, (list, tuple)) and raw_enum:
        return tuple(raw_enum)
    return None


def _variant_discriminator(
    variants: Sequence[Mapping[str, object]],
) -> str | None:
    property_maps: list[Mapping[str, object]] = []
    for variant in variants:
        properties = variant.get("properties")
        if not isinstance(properties, Mapping):
            return None
        property_maps.append(properties)
    common = set(property_maps[0])
    for properties in property_maps[1:]:
        common.intersection_update(properties)
    candidates = [
        candidate
        for candidate in ("op", "kind", *sorted(common.difference({"op", "kind"})))
        if candidate in common
    ]
    for candidate in candidates:
        if all(_schema_allowed_values(properties[candidate]) for properties in property_maps):
            return candidate
    return None


def _attributed_tool_result(
    name: str,
    result: Mapping[str, object],
) -> dict[str, object]:
    attributed = dict(result)
    attributed.setdefault(
        "capability_hash",
        capability_manifest().capability_hash,
    )
    attributed.setdefault("operation_version", _OPERATION_VERSIONS[name])
    return attributed


def _bounded_tool_result(
    name: str,
    result: Mapping[str, object],
) -> Mapping[str, object]:
    attributed = _attributed_tool_result(name, result)
    if _json_size(attributed) > _MAX_TOOL_CONTEXT_BYTES:
        return _attributed_tool_result(
            name,
            _error(
                "tool_result_too_large",
                "The tool result exceeds the bounded model-context limit.",
            ),
        )
    return attributed


def _observe_project_source_evidence(
    observed: dict[tuple[str, str], ProjectSourceEvidence],
    *,
    name: str,
    result: Mapping[str, object],
    project_root: Path,
) -> None:
    """Retain exact source facts returned to the provider for later plans."""

    if name == "get_dataset_schema":
        raw_path = result.get("path")
        raw_digest = result.get("source_digest")
        if isinstance(raw_path, str) and isinstance(raw_digest, str):
            observed[("schema", raw_path)] = ProjectSourceEvidence(
                path=validate_safe_path(project_root, raw_path),
                digest=raw_digest,
                kind="schema",
            )
        return

    if name != "get_project_knowledge":
        return
    raw_items = result.get("items")
    if not isinstance(raw_items, list):
        return
    for item in raw_items:
        if (
            isinstance(item, Mapping)
            and isinstance(item.get("source"), str)
            and isinstance(item.get("source_digest"), str)
        ):
            raw_source = item["source"]
            observed[("content", raw_source)] = ProjectSourceEvidence(
                path=validate_safe_path(project_root, raw_source),
                digest=item["source_digest"],
                kind="content",
            )


def _project_source_evidence_from_history(
    messages: Sequence[Mapping[str, Any]],
    *,
    project_root: Path,
) -> dict[tuple[str, str], ProjectSourceEvidence]:
    """Recover evidence from the exact provider-visible history window."""

    observed: dict[tuple[str, str], ProjectSourceEvidence] = {}
    for message in messages:
        if message.get("role") != "tool" or message.get("is_error") is True:
            continue
        name = message.get("name")
        result = message.get("content")
        if not isinstance(name, str) or not isinstance(result, Mapping) or "error" in result:
            continue
        _observe_project_source_evidence(
            observed,
            name=name,
            result=result,
            project_root=project_root,
        )
    return observed


def build_tool_executor(
    source_file: str,
    *,
    session_id: str = "legacy",
    prior_messages: Sequence[Mapping[str, Any]] = (),
    authoring_request: str = "",
) -> Callable[[str, dict[str, Any]], Awaitable[Mapping[str, object]]]:
    """Build the loop's non-raising, source-bound async tool dispatcher."""

    project_root = Path.cwd().resolve()
    observed_project_sources = _project_source_evidence_from_history(
        prior_messages,
        project_root=project_root,
    )
    pending_recipe_plans: dict[str, dict[str, object]] = {}
    pending_recipe_hash_by_id: dict[str, str] = {}
    required_recipe_id = route_recipe_request(authoring_request)
    material_input_required = request_requires_material_clarification(authoring_request)
    showcase_dataset_root = (
        explicit_dataset_directory(authoring_request)
        if required_recipe_id == "parquet_showcase"
        else None
    )

    def observed_sources() -> tuple[ProjectSourceEvidence, ...]:
        return tuple(observed_project_sources[key] for key in sorted(observed_project_sources))

    async def execute_tool(name: str, arguments: dict[str, Any]) -> Mapping[str, object]:
        if name not in _TOOL_NAMES:
            return _dispatch_error(
                name,
                f"Unknown assistant tool {name!r}. Choose one of: {', '.join(_TOOL_NAMES)}.",
            )
        if name == "list_datasets" and showcase_dataset_root is not None:
            arguments = {
                **arguments,
                "project_root": showcase_dataset_root,
                "recursive": True,
            }
        if name in _INTERNAL_PROJECT_TOOLS:
            try:
                policy = resolve_egress_policy(Path.cwd().resolve())
            except Exception as exc:  # noqa: BLE001 - executor must not raise
                return _attributed_tool_result(
                    name,
                    _error(
                        "egress_policy_unavailable",
                        _error_message(exc, operation=name),
                    ),
                )
            if policy.max_sensitivity == "public":
                return _attributed_tool_result(
                    name,
                    _error(
                        "egress_policy_denied",
                        "Saved project metadata is internal and exceeds the provider policy.",
                        required_sensitivity="internal",
                        max_sensitivity=policy.max_sensitivity,
                    ),
                )
        try:
            request_size = _json_size(arguments)
        except (TypeError, ValueError):
            code = (
                "invalid_capability_query"
                if name == "get_capability_descriptors"
                else "invalid_request"
            )
            return _attributed_tool_result(
                name,
                _error(code, "The tool request must be a finite JSON object."),
            )
        if request_size > _MAX_TOOL_PAYLOAD_BYTES:
            return _attributed_tool_result(
                name,
                _error(
                    "tool_payload_too_large",
                    "The tool request exceeds the operation payload limit.",
                ),
            )
        try:
            _validate_tool_value(
                arguments,
                _OPERATION_INPUT_SCHEMAS[name],
                path=name,
            )
        except _ToolArgumentValidationError as exc:
            code = (
                "invalid_capability_query"
                if name == "get_capability_descriptors"
                else "invalid_request"
            )
            return _attributed_tool_result(
                name,
                _error(
                    code,
                    str(exc),
                    validation_path=exc.path,
                    validation_reason=exc.reason,
                ),
            )
        if material_input_required and name in {
            "plan_recipe",
            "dry_run_recipe_plan",
            "dry_run_graph_edits",
            "apply_graph_plan",
        }:
            return _bounded_tool_result(
                name,
                _error(
                    "material_input_required",
                    "The request explicitly withholds required rating choices. Ask for "
                    "factor values and missing-factor policy before planning a mutation.",
                    required_inputs=("factor_values", "missing_factor_policy"),
                ),
            )
        if (
            name == "plan_recipe"
            and required_recipe_id is not None
            and arguments["recipe_id"] != required_recipe_id
        ):
            return _bounded_tool_result(
                name,
                _error(
                    "recipe_route_mismatch",
                    "The current request has an explicit deterministic recipe route. "
                    "Use the required recipe id.",
                    recipe_id=required_recipe_id,
                ),
            )
        if name == "plan_recipe":
            recipe_id = arguments["recipe_id"]
            expected_name = explicit_primary_recipe_name(authoring_request, recipe_id)
            name_argument = "output_name" if recipe_id == "response_output" else "name"
            if expected_name is not None and arguments.get(name_argument) != expected_name:
                return _bounded_tool_result(
                    name,
                    _error(
                        "recipe_name_mismatch",
                        "Preserve the primary recipe node name exactly as requested.",
                        expected_name=expected_name,
                        argument=name_argument,
                    ),
                )
        if name == "dry_run_graph_edits":
            if pending_recipe_plans:
                return _bounded_tool_result(
                    name,
                    _error(
                        "recipe_plan_requires_handle",
                        "A canonical recipe plan is pending. Pass its recipe_plan_hash to "
                        "dry_run_recipe_plan instead of copying its operations.",
                    ),
                )
            if required_recipe_id is not None:
                return _bounded_tool_result(
                    name,
                    _error(
                        "recipe_route_required",
                        "The current request has an explicit deterministic recipe route. "
                        "Call plan_recipe with the required recipe id before dry-run.",
                        recipe_id=required_recipe_id,
                    ),
                )
            return _bounded_tool_result(
                name,
                await dry_run_graph_edits(
                    source_file,
                    arguments.get("ops"),
                    postconditions=arguments.get("postconditions", ()),
                    project_sources=observed_sources(),
                ),
            )
        if name == "dry_run_recipe_plan":
            recipe_plan_hash = arguments["recipe_plan_hash"]
            pending = pending_recipe_plans.get(recipe_plan_hash)
            if pending is None:
                return _bounded_tool_result(
                    name,
                    _error(
                        "recipe_plan_not_found",
                        "The recipe plan handle is unknown or has been replaced. Call "
                        "plan_recipe again and use its latest returned hash.",
                    ),
                )
            recipe_operations = pending.get("operations")
            recipe_postconditions = pending.get("postconditions")
            if not isinstance(recipe_operations, list) or not isinstance(
                recipe_postconditions, list
            ):
                return _bounded_tool_result(
                    name,
                    _error("tool_failed", _INTERNAL_ERROR_DETAIL),
                )
            result = _bounded_tool_result(
                name,
                await dry_run_graph_edits(
                    source_file,
                    recipe_operations,
                    postconditions=recipe_postconditions,
                    project_sources=observed_sources(),
                ),
            )
            if "error" not in result:
                pending_recipe_plans.pop(recipe_plan_hash, None)
                recipe_id = pending.get("recipe_id")
                if (
                    isinstance(recipe_id, str)
                    and pending_recipe_hash_by_id.get(recipe_id) == recipe_plan_hash
                ):
                    pending_recipe_hash_by_id.pop(recipe_id, None)
            return result
        if name == "apply_graph_plan":
            return _bounded_tool_result(
                name,
                await apply_graph_plan(
                    source_file,
                    arguments.get("plan_hash", ""),
                ),
            )

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
            elif name == "get_capability_manifest":
                operation = get_capability_manifest
            elif name == "get_capability_descriptors":
                operation = partial(
                    get_capability_descriptors,
                    arguments["kind"],
                    arguments["ids"],
                )
            elif name == "list_datasets":
                operation = partial(
                    list_datasets,
                    arguments.get("project_root"),
                    recursive=arguments.get("recursive", False),
                )
            elif name == "get_dataset_schema":
                operation = partial(
                    get_dataset_schema,
                    arguments["path"],
                    source_file=source_file,
                )
            elif name == "get_project_knowledge":
                operation = partial(
                    get_project_knowledge,
                    source_file,
                    arguments["query"],
                    limit=arguments.get("limit", 5),
                )
            elif name == "get_example":
                operation = partial(get_example, arguments["name"])
            elif name == "get_authoring_guide":
                operation = get_authoring_guide
            elif name == "plan_recipe":
                operation = partial(
                    plan_recipe,
                    arguments["recipe_id"],
                    {key: value for key, value in arguments.items() if key != "recipe_id"},
                )
            else:  # pragma: no cover - guarded by _TOOL_NAMES
                return _dispatch_error(name, f"Unknown assistant tool {name!r}.")
            if name in {
                "get_pipeline",
                "get_node_schema",
                "get_node_config",
                "get_dataset_schema",
                "get_project_knowledge",
            }:
                async with save_lock:
                    result = await asyncio.to_thread(operation)
            else:
                result = await asyncio.to_thread(operation)
            if name == "plan_recipe" and "error" not in result:
                recipe_id = result.get("recipe_id")
                recipe_plan_hash = result.get("recipe_plan_hash")
                recipe_operations = result.get("operations")
                recipe_postconditions = result.get("postconditions")
                if (
                    not isinstance(recipe_id, str)
                    or not isinstance(recipe_plan_hash, str)
                    or not isinstance(recipe_operations, list)
                    or not isinstance(recipe_postconditions, list)
                ):
                    raise TypeError("plan_recipe returned a malformed canonical plan")
                previous_hash = pending_recipe_hash_by_id.get(recipe_id)
                if previous_hash is not None:
                    pending_recipe_plans.pop(previous_hash, None)
                pending_recipe_hash_by_id[recipe_id] = recipe_plan_hash
                pending_recipe_plans[recipe_plan_hash] = cast(
                    dict[str, object], materialise_json(result)
                )
                result = {
                    "recipe_id": recipe_id,
                    "version": result["version"],
                    "recipe_plan_hash": recipe_plan_hash,
                }
            bounded = _bounded_tool_result(name, result)
            if "error" in bounded and bounded.get("error") != result.get("error"):
                return bounded
            result = dict(bounded)
            if "error" not in result:
                _observe_project_source_evidence(
                    observed_project_sources,
                    name=name,
                    result=result,
                    project_root=project_root,
                )
            return result
        except Exception as exc:  # noqa: BLE001 - executor must never raise
            return _bounded_tool_result(
                name,
                _error("tool_failed", _error_message(exc, operation=name)),
            )

    return execute_tool


__all__ = [
    "TOOL_DEFINITIONS",
    "apply_graph_plan",
    "build_tool_executor",
    "dry_run_graph_edits",
    "get_capability_descriptors",
    "get_capability_manifest",
    "get_dataset_schema",
    "get_authoring_guide",
    "get_example",
    "get_node_config",
    "get_node_schema",
    "get_pipeline",
    "get_project_knowledge",
    "plan_recipe",
    "list_datasets",
    "list_node_types",
    "render_pipeline_graph",
]
