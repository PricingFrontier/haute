"""Read-only tools exposed to the pricing assistant.

The synchronous readers in this module deliberately return JSON-shaped
payloads.  The loop runs them in worker threads through ``build_tool_executor``
and therefore never has to know about route exceptions or Polars objects.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

import polars as pl
from fastapi import HTTPException

from haute._code_extraction import INCOMPLETE_TRANSFORM_MESSAGE
from haute._column_summary import (
    CATEGORICAL_COUNT_FIELD,
    is_unhashable_dtype,
    json_safe_scalar,
)
from haute._credential_security import is_credential_name
from haute._event_bus import default_bus
from haute._execution_admission import create_admitted_execution_context
from haute._execution_context import ExecutionContext, ExecutionProfile
from haute._graph_utils import edge_input_name
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
from haute.assistant._recipes import RecipeError
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
    load_pipeline_editor_document,
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
        "get_column_profiles",
        "list_datasets",
        "get_dataset_schema",
        "dry_run_recipe_plan",
        "dry_run_graph_edits",
        "apply_graph_plan",
    }
)
_SAVE_LOCK_READ_TOOLS = frozenset(
    {
        "get_pipeline",
        "get_node_schema",
        "get_node_config",
        "get_column_profiles",
        "get_dataset_schema",
        "get_project_knowledge",
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


def _execution_error_message(exc: Exception, *, operation: str) -> str:
    """Surface engine query failures, which are analyst-facing by construction.

    A Polars failure names the offending column, node code, or plan step and is
    exactly what the model needs to correct its own authoring. It is the same
    text the dry-run schema-validation path already returns, so classifying it
    here keeps one behaviour across both schema boundaries rather than leaving
    `get_node_schema` alone reporting an unattributable internal-error string.
    """

    if isinstance(exc, pl.exceptions.PolarsError):
        return str(exc)
    return _error_message(exc, operation=operation)


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


@dataclass(frozen=True, slots=True)
class _NodeInput:
    """One incoming edge described the way the node's own code sees it."""

    name: str
    source: str
    source_port: str | None


def _node_inputs(flat: PipelineGraph, node: str) -> tuple[_NodeInput, ...]:
    """Return each incoming edge as (code-visible input name, source, port)."""

    nodes_by_id = {candidate.id: candidate for candidate in flat.nodes}
    inputs: list[_NodeInput] = []
    for edge in flat.edges:
        if edge.target != node:
            continue
        source_node = nodes_by_id.get(edge.source)
        if source_node is None:
            continue
        inputs.append(
            _NodeInput(
                name=edge_input_name(edge, source_node),
                source=edge.source,
                source_port=edge.sourceHandle,
            )
        )
    return tuple(inputs)


def _resolve_frame_outputs(
    flat: PipelineGraph,
    graph: PipelineGraph,
    *,
    target: str,
    preserve: set[str] | frozenset[str],
    execution_context: ExecutionContext,
) -> Mapping[str, object]:
    """Prepare frames a caller intends to collect.

    Deliberately not `schema_only`: collecting is materialisation, so the
    engine's ordinary admission policy applies exactly as it would to any
    other read of these rows.
    """

    return _resolve_schema_outputs(
        flat,
        graph,
        target=target,
        preserve=frozenset(preserve),
        schema_only=False,
        execution_context=execution_context,
    )


def _resolve_schema_outputs(
    flat: PipelineGraph,
    graph: PipelineGraph,
    *,
    target: str,
    preserve: frozenset[str],
    schema_only: bool = True,
    execution_context: ExecutionContext | None = None,
) -> Mapping[str, object]:
    """Run the production preparation for schema resolution only.

    Exactly the `_explore_service._materialise_and_summarise_worker` sequence — no
    assistant-only recovery. `schema_only=True` states the invariant this
    module already guarantees and tests by poisoning `collect`: nothing is
    collected and no sink runs, so the engine's group-by materialisation gate,
    which bounds peak memory during materialisation, does not apply.
    """

    preamble_ns = _compile_preamble(
        graph.preamble or "",
        pipeline_dir=_pipeline_dir(graph),
    )
    lazy_outputs, *_ = execute_lazy_graph(
        flat,
        _build_node_fn,
        target_node_id=target,
        preserve_node_ids=set(preserve),
        preamble_ns=preamble_ns or None,
        source=graph.active_source,
        enforce_contracts=True,
        schema_only=schema_only,
        execution_context=execution_context,
    )
    return lazy_outputs


def _port_schema(output: object, port: str | None) -> list[dict[str, str]]:
    """Render one frame, selecting `port` when the source emits several."""

    if isinstance(output, dict):
        if port is None:
            raise KeyError(
                "A multi-frame source needs the edge's source port to identify one frame"
            )
        if port not in output:
            raise KeyError(f"Source port {port!r} is not emitted; available: {sorted(output)}")
        return _schema_for_frame(output[port])
    return _schema_for_frame(cast(pl.LazyFrame, output))


def _input_schemas_from_outputs(
    inputs: Sequence[_NodeInput],
    lazy_outputs: Mapping[str, object],
) -> dict[str, object]:
    return {item.name: _port_schema(lazy_outputs[item.source], item.source_port) for item in inputs}


def _input_schemas_independently(
    flat: PipelineGraph,
    graph: PipelineGraph,
    inputs: Sequence[_NodeInput],
) -> dict[str, object]:
    """Resolve each input against its own source when the target cannot run.

    One engine call per distinct source, on the failure path only. An input
    whose own source is unresolvable reports that reason in place of columns
    rather than disappearing from the result.
    """

    resolved: dict[str, object] = {}
    for item in inputs:
        try:
            lazy_outputs = _resolve_schema_outputs(
                flat,
                graph,
                target=item.source,
                preserve=frozenset({item.source}),
            )
            resolved[item.name] = _port_schema(lazy_outputs[item.source], item.source_port)
        except Exception as exc:  # noqa: BLE001 - one unresolvable input is reportable
            resolved[item.name] = {
                "unresolved_reason": _execution_error_message(exc, operation="get_node_schema"),
                "source": item.source,
            }
    return resolved


def get_node_schema(source_file: str, node: str) -> dict[str, object]:
    """Resolve one top-level executable node's output and input schemas."""

    try:
        graph = _parse_graph(source_file)
        validation_error = _validate_top_level_target(graph, node)
        if validation_error is not None:
            return validation_error
        project_revision = _project_revision(source_file, graph)
        flat = flatten_graph(graph)
        inputs = _node_inputs(flat, node)
    except Exception as exc:  # noqa: BLE001 - tool boundary must not raise
        return _error(
            "schema_unresolvable",
            _error_message(exc, operation="get_node_schema"),
        )

    try:
        lazy_outputs = _resolve_schema_outputs(
            flat,
            graph,
            target=node,
            preserve=frozenset({node, *(item.source for item in inputs)}),
        )
        output = lazy_outputs[node]
        result: dict[str, object] = {"node": node}
        if isinstance(output, dict):
            result["ports"] = {port: _schema_for_frame(frame) for port, frame in output.items()}
        else:
            result["columns"] = _schema_for_frame(cast(pl.LazyFrame, output))
        if inputs:
            result["inputs"] = _input_schemas_from_outputs(inputs, lazy_outputs)
        result["project_revision"] = project_revision
        return result
    except NotImplementedError as exc:
        if str(exc) != INCOMPLETE_TRANSFORM_MESSAGE:
            return _error(
                "schema_unresolvable",
                _execution_error_message(exc, operation="get_node_schema"),
            )
        # An authored-but-empty transform is an ordinary editing state, not a
        # defect: the analyst is asking the assistant to write that code. The
        # node's own output is genuinely unresolvable, so say so by a stable
        # reason and still answer the question the model actually needs —
        # which columns arrive on each input.
        return {
            "node": node,
            "unresolved_reason": "node_has_no_code",
            "inputs": _input_schemas_independently(flat, graph, inputs),
            "project_revision": project_revision,
        }
    except Exception as exc:  # noqa: BLE001 - tool boundary must not raise
        return _error(
            "schema_unresolvable",
            _execution_error_message(exc, operation="get_node_schema"),
        )


_MAX_PROFILE_LEVELS = 50
_MAX_PROFILE_ROWS = 1_000_000
_MAX_PROFILE_VALUE_CHARS = 120
_EXECUTABLE_CONFIG_KEYS = frozenset({"code", "preamble", "query", "script"})
_ROW_VALUE_CONFIG_KEYS = frozenset({"records"})
# Only these dtypes can carry a value list. A categorical encoding is exactly
# what authoring needs ("is `fault` Y/N or true/false?"). The cardinality cap
# reduces disclosure but is not an authorization boundary: repeated personal
# values can fit, so the explicit row-sample policy remains authoritative.
# Polars instantiates every schema dtype, so `isinstance` matches each of these
# including their parameterised forms (`Enum(categories=[...])`).
_PROFILABLE_LEVEL_DTYPES = (pl.String, pl.Categorical, pl.Enum, pl.Boolean)


def _redact_config_value(
    value: object,
    *,
    key: str | None = None,
    allow_executable_source: bool = False,
) -> object:
    """Redact by egress policy. Credentials and row values are never eligible."""

    if key is not None:
        if is_credential_name(key):
            return "<redacted: credential>"
        if key.casefold() in _EXECUTABLE_CONFIG_KEYS and not allow_executable_source:
            return "<redacted: executable_source>"
        if key.casefold() in _ROW_VALUE_CONFIG_KEYS:
            return "<redacted: row_values>"
    if isinstance(value, Mapping):
        return {
            str(child_key): _redact_config_value(
                child_value,
                key=str(child_key),
                allow_executable_source=allow_executable_source,
            )
            for child_key, child_value in value.items()
        }
    if isinstance(value, list | tuple):
        return [
            _redact_config_value(child, allow_executable_source=allow_executable_source)
            for child in value
        ]
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
            # `allow_executable_source` is the policy's own decision. Redacting
            # regardless made the setting inert and left the assistant editing
            # code it was permitted to read but could not see.
            "config": _redact_config_value(
                candidate.data.config,
                allow_executable_source=policy.allow_executable_source,
            ),
            "project_revision": project_revision,
        }
    except Exception as exc:  # noqa: BLE001 - structured tool boundary
        return _error("node_config_unavailable", _error_message(exc, operation="get_node_config"))


def _profile_value(value: object) -> object:
    """Render one profile value: JSON-encodable, and bounded in length.

    Every summary is encoded twice before the model reads it — once to bound
    the result, once by the provider adapter — and both encoders take only
    JSON scalars. `json_safe_scalar` is where a `date`, `Decimal`, or infinity
    becomes one; the length bound then applies to whatever text results, so a
    rendered value can no more dominate the payload than a stored string can.
    Never a partial row.
    """

    rendered = json_safe_scalar(value)
    if isinstance(rendered, str) and len(rendered) > _MAX_PROFILE_VALUE_CHARS:
        return rendered[:_MAX_PROFILE_VALUE_CHARS] + "…"
    return rendered


def _column_profile(frame: pl.DataFrame, name: str, dtype: pl.DataType) -> dict[str, object]:
    """Summarise one column: levels when categorical and small, else bounds.

    Every branch is chosen by dtype before the aggregation runs. A column whose
    values Polars cannot hash raises rather than returning nothing, and one
    raise would otherwise abort the whole frame's profile — losing every
    column the analyst did ask about to one they did not.
    """

    column = frame.get_column(name)
    profile: dict[str, object] = {
        "name": name,
        "dtype": str(dtype),
        "null_count": int(column.null_count()),
    }
    if is_unhashable_dtype(dtype):
        profile["values_withheld"] = "unsupported_dtype"
        return profile

    distinct = int(column.n_unique())
    profile["distinct_count"] = distinct

    if isinstance(dtype, _PROFILABLE_LEVEL_DTYPES):
        if distinct <= _MAX_PROFILE_LEVELS:
            # Name the count field explicitly: Polars refuses `value_counts` on
            # a column already called `count`, which is an ordinary name in an
            # aggregated frame and used to abort the entire profile.
            counts = column.value_counts(sort=True, name=CATEGORICAL_COUNT_FIELD)
            profile["values"] = [
                {
                    "value": _profile_value(row[name]),
                    "count": int(row[CATEGORICAL_COUNT_FIELD]),
                }
                for row in counts.head(_MAX_PROFILE_LEVELS).to_dicts()
            ]
        else:
            # Deliberate: a column with this many distinct values is an
            # identifier or free text, not an encoding. Withholding it is the
            # boundary that keeps names, addresses, and registrations out.
            profile["values_withheld"] = "high_cardinality"
        return profile

    if dtype.is_numeric() or dtype.is_temporal():
        profile["min"] = _profile_value(column.min())
        profile["max"] = _profile_value(column.max())
    else:
        profile["values_withheld"] = "unsupported_dtype"
    return profile


def _profile_frame(
    frame: pl.LazyFrame,
    *,
    execution_context: ExecutionContext | None = None,
) -> dict[str, object]:
    """Collect one bounded prefix and summarise every column of it."""

    from haute._polars_utils import streaming_collect

    collected = streaming_collect(
        frame.head(_MAX_PROFILE_ROWS),
        execution_context=execution_context,
    )
    schema = collected.collect_schema()
    return {
        "rows_scanned": collected.height,
        "scan_bounded": collected.height >= _MAX_PROFILE_ROWS,
        "columns": [_column_profile(collected, name, dtype) for name, dtype in schema.items()],
    }


def get_column_profiles(
    source_file: str, node: str, input_name: str | None = None
) -> dict[str, object]:
    """Summarise the values in one node frame, without returning rows.

    This is the only tool that reads data, and it never emits a row: a value
    only appears as a distinct level of a small-cardinality column, alongside
    its count. Authoring correct code needs the encoding of a categorical
    column, and inferring one from its name is guesswork the model has no way
    to check.
    """

    try:
        policy = resolve_egress_policy(Path.cwd().resolve())
        if not policy.allow_row_samples:
            return _error(
                "egress_policy_denied",
                "Column value profiles read project data. Set "
                "[assistant.egress].allow_row_samples to enable them.",
                required_policy="allow_row_samples",
            )
        graph = _parse_graph(source_file)
        validation_error = _validate_top_level_target(graph, node)
        if validation_error is not None:
            return validation_error
        project_revision = _project_revision(source_file, graph)
        flat = flatten_graph(graph)
        inputs = _node_inputs(flat, node)
    except Exception as exc:  # noqa: BLE001 - tool boundary must not raise
        return _error("profile_unavailable", _error_message(exc, operation="get_column_profiles"))

    execution_context: ExecutionContext | None = None
    try:
        execution_context = create_admitted_execution_context(
            operation="assistant_column_profiles",
            profile=ExecutionProfile.PREVIEW_EAGER,
        )
        if input_name is None:
            lazy_outputs = _resolve_frame_outputs(
                flat,
                graph,
                target=node,
                preserve={node},
                execution_context=execution_context,
            )
            output = lazy_outputs[node]
            if isinstance(output, dict):
                return _error(
                    "profile_target_ambiguous",
                    f"Node {node!r} emits several frames; name one of its ports: "
                    + ", ".join(sorted(output)),
                    ports=sorted(output),
                )
            frame = cast(pl.LazyFrame, output)
        else:
            match = next((item for item in inputs if item.name == input_name), None)
            if match is None:
                return _error(
                    "unknown_input",
                    f"Node {node!r} has no input named {input_name!r}.",
                    inputs=[item.name for item in inputs],
                )
            lazy_outputs = _resolve_frame_outputs(
                flat,
                graph,
                target=match.source,
                preserve={match.source},
                execution_context=execution_context,
            )
            source_output = lazy_outputs[match.source]
            if isinstance(source_output, dict):
                if match.source_port is None or match.source_port not in source_output:
                    return _error(
                        "profile_unavailable",
                        f"Input {input_name!r} does not resolve to one emitted frame.",
                    )
                frame = source_output[match.source_port]
            else:
                frame = cast(pl.LazyFrame, source_output)
        return {
            "node": node,
            "input": input_name,
            **_profile_frame(frame, execution_context=execution_context),
            "max_levels": _MAX_PROFILE_LEVELS,
            "project_revision": project_revision,
        }
    except Exception as exc:  # noqa: BLE001 - tool boundary must not raise
        return _error(
            "profile_unavailable",
            _execution_error_message(exc, operation="get_column_profiles"),
        )
    finally:
        if execution_context is not None:
            execution_context.release_admission(preserve_primary_error=True)


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


def _publish_document_update(source_file: str) -> str:
    """Publish the exact pipeline-document payload used by the file watcher."""

    from haute.server import _document_payload_fingerprint, _wire_source_file

    document_payload = load_pipeline_editor_document(
        Path(source_file), project_root=Path.cwd()
    ).model_dump(mode="json", by_alias=True)
    fingerprint = _document_payload_fingerprint(document_payload)
    default_bus.publish(
        "pipeline.document.update",
        {
            "document": document_payload,
            "document_fingerprint": fingerprint,
            "source_file": _wire_source_file(Path(source_file)),
        },
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
        publish_document_update=_publish_document_update,
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
        # `invalid_plan` is a specific authorization verdict raised by the
        # domain layer. Reusing it for an unexpected exception told the model
        # its plan was rejected when nothing had judged the plan at all.
        return _error("operation_failed", _error_message(exc, operation="dry_run_graph_edits"))


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


def _log_tool_outcome(name: str, result: Mapping[str, object], *, elapsed_ms: float) -> None:
    """Record one tool outcome so a failed turn is diagnosable after the fact.

    Durable session files redact arguments and messages by design, which left
    no server-side record of *why* a turn failed. This is the diagnostic
    channel: stable identities and the analyst-facing error message at info
    level, and the argument keys — names only, never values — at debug.
    """

    error = result.get("error")
    if not isinstance(error, Mapping):
        logger.debug(
            "assistant_tool_succeeded",
            operation=name,
            elapsed_ms=round(elapsed_ms, 1),
            result_keys=sorted(str(key) for key in result),
        )
        return
    logger.info(
        "assistant_tool_error",
        operation=name,
        elapsed_ms=round(elapsed_ms, 1),
        error_code=error.get("code"),
        error_message=error.get("message"),
        validation_path=error.get("validation_path"),
        validation_reason=error.get("validation_reason"),
    )


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

    __slots__ = ("fields", "path", "reason")

    def __init__(
        self,
        path: str,
        reason: str,
        message: str,
        fields: Mapping[str, object] | None = None,
    ) -> None:
        self.path = path
        self.reason = reason
        # Model-facing correction detail only. `_session._persisted_message`
        # copies exactly `code`, `validation_path`, and `validation_reason`,
        # so these never reach durable history.
        self.fields = dict(fields or {})
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


def _json_type_name(value: object) -> str:
    """Name a value's JSON type for a validation message. Never its content."""

    if value is None:
        return "null"
    if type(value) is bool:
        return "boolean"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if type(value) is int:
        return "integer"
    if isinstance(value, float):
        return "number"
    return type(value).__name__


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
        # Naming both sides is what makes this correctable. A provider that
        # encodes a container as a JSON string produces exactly this rejection,
        # and "has the wrong JSON type" gave the model nothing to act on — it
        # cannot see that it sent a string where an array was required.
        received = _json_type_name(value)
        article = "an" if received[:1] in "aeiou" else "a"
        raise _ToolArgumentValidationError(
            path,
            "wrong_type",
            f"{path} must be JSON {' or '.join(expected_types)}, but "
            f"{article} {received} was sent"
            + (
                ". Send the value itself, not a JSON-encoded string of it"
                if received == "string" and {"array", "object"} & set(expected_types)
                else ""
            ),
            fields={"expected_types": list(expected_types), "received_type": received},
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
            # Naming the rejected key and the closed allowlist is what makes
            # this correctable in one retry. Both are already known to the
            # model — it sent the key, and the allowlist is its own schema.
            allowed = sorted(str(key) for key in raw_properties)
            raise _ToolArgumentValidationError(
                path,
                "unknown_field",
                f"{path} does not allow the field(s) {', '.join(unknown)}; "
                f"this variant accepts only {', '.join(allowed)}",
                fields={"unknown_fields": unknown, "allowed_fields": allowed},
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
) -> Callable[[str, dict[str, Any]], Awaitable[Mapping[str, object]]]:
    """Build the loop's non-raising, source-bound async tool dispatcher."""

    project_root = Path.cwd().resolve()
    observed_project_sources = _project_source_evidence_from_history(
        prior_messages,
        project_root=project_root,
    )
    pending_recipe_plans: dict[str, dict[str, object]] = {}
    pending_recipe_hash_by_id: dict[str, str] = {}

    def observed_sources() -> tuple[ProjectSourceEvidence, ...]:
        return tuple(observed_project_sources[key] for key in sorted(observed_project_sources))

    async def execute_tool(name: str, arguments: dict[str, Any]) -> Mapping[str, object]:
        started = time.monotonic()
        result = await _dispatch_tool(name, arguments)
        _log_tool_outcome(name, result, elapsed_ms=(time.monotonic() - started) * 1000)
        return result

    async def _dispatch_tool(name: str, arguments: dict[str, Any]) -> Mapping[str, object]:
        if name not in _TOOL_NAMES:
            return _dispatch_error(
                name,
                f"Unknown assistant tool {name!r}. Choose one of: {', '.join(_TOOL_NAMES)}.",
            )
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
                    **exc.fields,
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
            elif name == "get_column_profiles":
                operation = partial(
                    get_column_profiles,
                    source_file,
                    arguments["node"],
                    arguments.get("input"),
                )
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
            if name in _SAVE_LOCK_READ_TOOLS:
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
    "get_column_profiles",
    "get_pipeline",
    "get_project_knowledge",
    "plan_recipe",
    "list_datasets",
    "list_node_types",
    "render_pipeline_graph",
]
