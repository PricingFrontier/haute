"""Read-only tools exposed to the pricing assistant.

The synchronous readers in this module deliberately return JSON-shaped
payloads.  The loop runs them in worker threads through ``build_tool_executor``
and therefore never has to know about route exceptions or Polars objects.
"""

from __future__ import annotations

import ast
import asyncio
import difflib
import json
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from functools import partial
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

import polars as pl
from fastapi import HTTPException

from haute._code_extraction import INCOMPLETE_STEPS_MESSAGE, INCOMPLETE_TRANSFORM_MESSAGE
from haute._column_summary import (
    CATEGORICAL_COUNT_FIELD,
    is_unhashable_dtype,
    json_safe_scalar,
)
from haute._credential_security import is_credential_name
from haute._event_bus import PipelineDocumentUpdatePayload, default_bus
from haute._execution_admission import (
    IsolatedExecutionBudget,
    create_admitted_execution_context,
    create_isolated_execution_context,
    isolated_execution_budget,
)
from haute._execution_context import (
    ExecutionCancellationToken,
    ExecutionCancelledError,
    ExecutionContext,
    ExecutionProfile,
)
from haute._graph_utils import edge_input_name
from haute._interactive_workers import (
    InteractiveWorkerError,
    InteractiveWorkerStoppedError,
    InteractiveWorkerTimeoutError,
    resolve_interactive_execution_mode,
    run_in_interactive_worker,
)
from haute._logging import get_logger
from haute._polars_io_registry import PolarsIoConfigError
from haute._sandbox import contained_path
from haute._source_cache import SourceCacheError
from haute._submodel_instances import (
    ResolvedSubmodelInstance,
    bound_input_port,
    bound_output_port,
    qualified_runtime_node_id,
    resolve_submodel_instances,
)
from haute._types import GraphNode, NodeType, PipelineGraph
from haute._user_exec import user_code_line
from haute._worker_isolation import resolve_worker_memory_enforcement
from haute.assistant._application import (
    CommittedVerificationError,
    FailedStep,
    InvalidConfigError,
    PipelineApplicationService,
    PreambleFailedError,
    SchemaUnresolvableError,
    _failed_step,
)
from haute.assistant._assets import authoring_guide, example_index, load_example
from haute.assistant._build_plan import BuildPlan, BuildPlanError, build_plan_view
from haute.assistant._catalog import (
    INSPECT_NODE_PARTS,
    capability_manifest,
    materialise_json,
    step_grammar,
)
from haute.assistant._change_record import touched_node_ids
from haute.assistant._config import EgressPolicy, mutations_readiness, resolve_egress_policy
from haute.assistant._ops import (
    SUBMODEL_BOUNDARY_CODE,
    SUBMODEL_BOUNDARY_FIX,
    SUBMODEL_BOUNDARY_TEXT,
    AssistantOperationError,
    ConfigVisibility,
    LocatedPlanError,
    OpValidationError,
    PlanStore,
    ProjectSourceEvidence,
    RenameConsumersError,
    SourceEvidenceLedger,
    build_project_snapshot,
    dataset_schema_digest,
)
from haute.assistant._project_knowledge import build_project_knowledge, query_project_knowledge
from haute.assistant._recipes import RecipeOperationError, expand_recipe_operations
from haute.assistant._render import (
    BriefFrame,
    BriefInput,
    BriefNode,
    ChangedGraph,
    ContextUpdate,
    GraphBrief,
    PreviewError,
    TurnContext,
    node_authoring,
    render_context_update,
    render_pipeline_graph,
)
from haute.errors import (
    ConfigSettingError,
    HauteError,
    InvalidPathError,
    PathOutsideProjectError,
    PreambleError,
)
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
)
from haute.routes._supersession import SupersededRequestError, SupersessionCoordinator
from haute.routes.pipeline import preview_timeout
from haute.schemas import AssistantBuildPlan, AssistantChangeRecord

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
# Tools refused outright under a `public` policy: everything they answer is
# internal project metadata. `inspect_node` checks each part it answers instead.
_INTERNAL_PROJECT_TOOLS = frozenset(
    {
        "get_pipeline",
        "find_data",
        "dry_run_graph_edits",
        "apply_graph_plan",
    }
)
_SAVE_LOCK_READ_TOOLS = frozenset(
    {
        "get_pipeline",
        "inspect_node",
        "find_data",
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

    if isinstance(exc, (PathOutsideProjectError, InvalidPathError)):
        # The bare refusal, as the API gives it; the refused path stays in the log context.
        return exc.message
    if isinstance(exc, PreambleError):
        # A Haute error, but its text is the authored preamble's own exception,
        # which can quote data the preamble read at import.
        return _execution_error_message(exc, operation=operation, site=None)
    if isinstance(exc, (HauteError, SourceCacheError, PolarsIoConfigError)):
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


# A Polars error names a column only in these phrases of its first paragraph:
# `unable to find column "x"`, `at column 'x'`, `in column 'x'`, `"x" not found`.
_POLARS_COLUMN_PHRASES = (
    re.compile(r"\bcolumn [`'\"]([^`'\"\n]+)[`'\"]"),
    re.compile(r'^"([^"\n]+)" not found'),
)


@dataclass(frozen=True, slots=True)
class _FailureSite:
    """Where an authored-code failure was met, for naming the columns it names.

    `node` is the node whose input frames' schemas are permitted metadata: the
    failing step's node, or else the node the tool resolved. `own_output`
    adds that node's own output schema, which only a caller whose schema
    resolved before the failure (a column profile's collection) sets.
    `input_name` narrows the metadata to that one named input's frame, the
    only frame a profile of that input resolved, so rendering its failure
    never resolves the consumer. `submitted` is the operations payload the
    model sent for the current plan.
    """

    graph: PipelineGraph
    node: str
    own_output: bool = False
    input_name: str | None = None
    submitted: object = ()


def _names_in_source(source: str) -> set[str]:
    """Every identifier, keyword name and string literal *source* writes.

    Text that does not parse contributes nothing, which withholds a name
    rather than trusting it.
    """

    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return set()
    names: set[str] = set()
    for item in ast.walk(tree):
        if isinstance(item, ast.Name):
            names.add(item.id)
        elif isinstance(item, ast.keyword) and item.arg is not None:
            names.add(item.arg)
        elif isinstance(item, ast.Constant) and isinstance(item.value, str):
            names.add(item.value)
    return names


def _authored_names(graph: PipelineGraph) -> set[str]:
    """The names the graph's preamble and node code write.

    A stepped node's code is its rendered steps. This is executable source, so
    it is disclosable only when `allow_executable_source` permits it.
    """

    names = _names_in_source(graph.preamble or "")
    for node in flatten_graph(graph).nodes:
        code = node.data.config.get("code")
        if isinstance(code, str):
            names |= _names_in_source(code)
    return names


def _submitted_names(submitted: object) -> set[str]:
    """Every string in the operations the model sent, and the names each writes.

    The provider already holds this text, so echoing any of it discloses
    nothing new.
    """

    names: set[str] = set()
    pending = [submitted]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            names.add(item)
            names |= _names_in_source(item)
        elif isinstance(item, Mapping):
            for key, value in item.items():
                pending.extend((key, value))
        elif isinstance(item, (list, tuple)):
            pending.extend(item)
    return names


def _submitted_scalars(submitted: object) -> set[str]:
    """The text of every scalar the operations the model sent hold."""

    scalars: set[str] = set()
    pending = [submitted]
    while pending:
        item = pending.pop()
        if isinstance(item, Mapping):
            pending.extend(item.values())
        elif isinstance(item, (list, tuple)):
            pending.extend(item)
        elif isinstance(item, str | int | float | bool):
            scalars.add(str(item))
    return scalars


def _config_setting_message(exc: ConfigSettingError, *, submitted: object) -> str:
    """A config parser's refusal, unless it quotes configuration the model cannot read.

    Its column and output names are pipeline metadata; the configured values
    it quotes (a boundary, a category) are the node's saved configuration. The
    text is shown when the policy lets the model read saved configuration, or
    when every quoted value is one the model's own operations sent.
    """

    if exc.values:
        try:
            policy = resolve_egress_policy(Path.cwd().resolve())
            readable = _part_requirement(policy, "config") is None
        except Exception:  # noqa: BLE001 - an unreadable policy withholds
            readable = False
        sent = _submitted_scalars(submitted)
        if not readable and not all(str(value) in sent for value in exc.values):
            return (
                f"Its {exc.setting} setting is invalid; the parser's message quotes saved "
                "configuration this project's egress policy withholds."
            )
    return str(exc)


def _frame_column_names(output: object, port: str | None) -> list[str]:
    """Column names of one resolved output, selecting `port` when it emits several.

    An edge that names no port on a multi-frame output reads every frame.
    """

    if not isinstance(output, dict):
        frames = [output]
    elif port is None:
        frames = list(output.values())
    else:
        frames = [output[port]] if port in output else []
    names: list[str] = []
    for frame in frames:
        if isinstance(frame, pl.LazyFrame):
            names.extend(name for name in frame.collect_schema().names() if name not in names)
    return names


def _input_columns(
    graph: PipelineGraph, node: str, *, input_name: str | None = None
) -> dict[str, list[str]]:
    """Each incoming input's code-visible name and column names at *node*.

    Resolved schema-only, once per source, on the same engine path as
    `inspect_node`'s schema part, so each name is one that part would disclose.
    `input_name` narrows the result to that one input. An input whose source
    does not resolve is left out and is never an error of its own.
    """

    try:
        flat = flatten_graph(graph)
        inputs = _node_inputs(graph, flat, node)
    except Exception as exc:  # noqa: BLE001 - an unresolvable site discloses nothing
        logger.info("assistant_failure_schema_unresolved", node=node, error=type(exc).__name__)
        return {}
    outputs: dict[str, object] = {}
    columns: dict[str, list[str]] = {}
    for item in inputs:
        if input_name is not None and item.name != input_name:
            continue
        source = item.runtime_source
        try:
            if source not in outputs:
                outputs[source] = _resolve_schema_outputs(
                    flat, graph, target=source, preserve=frozenset({source})
                )[source]
            columns[item.name] = _frame_column_names(outputs[source], item.runtime_port)
        except Exception as exc:  # noqa: BLE001 - an unresolvable frame discloses nothing
            logger.info(
                "assistant_failure_schema_unresolved", node=source, error=type(exc).__name__
            )
    return columns


def _schema_metadata_names(site: _FailureSite) -> set[str]:
    """Column names of the failing node's input frames, resolved schema-only.

    With `own_output`, the node's own output columns too. A frame that does
    not resolve contributes nothing.
    """

    names = {
        name
        for columns in _input_columns(site.graph, site.node, input_name=site.input_name).values()
        for name in columns
    }
    if site.own_output and site.input_name is None:
        try:
            output = _resolve_schema_outputs(
                flatten_graph(site.graph),
                site.graph,
                target=site.node,
                preserve=frozenset({site.node}),
            )[site.node]
            names.update(_frame_column_names(output, None))
        except Exception as exc:  # noqa: BLE001 - an unresolvable frame discloses nothing
            logger.info(
                "assistant_failure_schema_unresolved", node=site.node, error=type(exc).__name__
            )
    return names


def _polars_error_columns(
    exc: Exception, site: _FailureSite | None, *, allow_executable_source: bool
) -> tuple[tuple[str, ...], bool]:
    """The column names a Polars error names that the egress policy already discloses.

    A `column '<x>'` phrase can sit inside a quoted cell value or inside an
    authored exception's collected values, and saved code the model may not
    read can hold a literal equal to such a cell, so a candidate is reported
    only when it is independently disclosable: the failing node's schema
    metadata, the model's own plan text, or saved code when executable source
    is permitted. Without a site none is. The allowlist is built only when a
    candidate exists. Returns the reported names and whether any was dropped.
    """

    if not isinstance(exc, pl.exceptions.PolarsError):
        return (), False
    first_paragraph = str(exc).split("\n\n", 1)[0]
    candidates: list[str] = []
    for pattern in _POLARS_COLUMN_PHRASES:
        for match in pattern.finditer(first_paragraph):
            if match.group(1) not in candidates:
                candidates.append(match.group(1))
    if not candidates:
        return (), False
    known: set[str] = set()
    if site is not None:
        known |= _submitted_names(site.submitted)
        if allow_executable_source:
            known |= _authored_names(site.graph)
        if not all(name in known for name in candidates):
            known |= _schema_metadata_names(site)
    names = tuple(name for name in candidates if name in known)
    return names, len(names) < len(candidates)


def _execution_error_message(
    exc: Exception,
    *,
    operation: str,
    site: _FailureSite | None,
    step: FailedStep | None = None,
) -> str:
    """Render a failure raised while the engine ran authored code over project inputs."""

    return _execution_failure(exc, operation=operation, site=site, step=step)[0]


def _execution_failure(
    exc: Exception,
    *,
    operation: str,
    site: _FailureSite | None,
    step: FailedStep | None = None,
) -> tuple[str, tuple[str, ...]]:
    """Render an authored-code failure and return the column names it reports.

    An authored-code failure — a Polars error, any exception raised from node
    code, or a preamble failure — is what the model needs to correct its own
    authoring, but its text can quote row values: a Polars cast error names the
    cell it could not parse, and node or preamble code can put values it read
    in its exception. It is reported as its type, the step or line that raised
    it, and those column names it names that the egress policy already
    discloses at `site`; its text follows only when the project permits row
    samples. Haute's own errors keep their text, the incomplete-transform
    messages included, and anything else is an internal failure.
    """

    if isinstance(exc, ConfigSettingError):
        return _config_setting_message(exc, submitted=() if site is None else site.submitted), ()
    preamble = isinstance(exc, PreambleError)
    line = user_code_line(exc)
    if not preamble and not isinstance(exc, pl.exceptions.PolarsError) and line is None:
        if isinstance(exc, NotImplementedError) and str(exc).startswith(
            (INCOMPLETE_TRANSFORM_MESSAGE, INCOMPLETE_STEPS_MESSAGE)
        ):
            return str(exc), ()
        return _error_message(exc, operation=operation), ()

    summary = type(exc).__name__
    if isinstance(exc, PreambleError):
        summary += (
            f" at line {exc.source_line} of the preamble"
            if exc.source_line is not None
            else " in the preamble"
        )
    elif step is not None:
        summary += f" in step {step.number} ({step.step_id!r}) of node {step.node!r}"
    elif line is not None:
        summary += f" at line {line} of the node code"
    try:
        policy = resolve_egress_policy(Path.cwd().resolve())
        allowed = policy.allow_row_samples
        allow_executable_source = policy.allow_executable_source
        withheld_because = (
            "[assistant.egress].allow_row_samples is false and the text can quote row values"
        )
    except Exception as policy_exc:  # noqa: BLE001 - an unreadable policy withholds
        allowed = False
        allow_executable_source = False
        withheld_because = "the egress policy could not be read: " + _error_message(
            policy_exc, operation=operation
        )
    columns, dropped = _polars_error_columns(
        exc, site, allow_executable_source=allow_executable_source
    )
    if columns:
        summary += "; it names column(s) " + ", ".join(repr(name) for name in columns)
    elif dropped:
        summary += "; it names a column"
    if allowed:
        return f"{summary}: {exc}", columns
    logger.info(
        "assistant_execution_error_withheld",
        operation=operation,
        error_class=type(exc).__name__,
        error_message=str(exc),
    )
    return f"{summary}. Its text is withheld because {withheld_because}.", columns


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


#: The fix of a refused read of a submodel occurrence, whose columns are readable.
_OCCURRENCE_BOUNDARY_FIX = (
    'inspect_node with parts ["schema"] reads the columns its ports take and give; for '
    "anything else, reply with a line starting BLOCKED: that asks the analyst to look in "
    "the editor."
)


def _validate_top_level_target(
    graph: PipelineGraph, node: str, *, occurrence_schema: bool = False
) -> dict[str, object] | None:
    """None when *node* is a top-level node the read may answer, else its error.

    A submodel occurrence answers only its schema (*occurrence_schema*): the
    columns of its ports are metadata, while its configuration, its code and
    the nodes inside it stay behind the submodel boundary.
    """

    top_level = {candidate.id: candidate for candidate in graph.nodes}
    candidate = top_level.get(node)
    if candidate is not None:
        if candidate.data.nodeType == NodeType.SUBMODEL:
            if occurrence_schema:
                return None
            return _error(
                SUBMODEL_BOUNDARY_CODE,
                f"Node {node!r} is a submodel occurrence: its configuration, its code and the "
                "nodes inside it cannot be read or edited by the assistant.",
                fix=_OCCURRENCE_BOUNDARY_FIX,
            )
        if candidate.data.nodeType in _BOUNDARY_NODE_TYPES:
            return _error(
                SUBMODEL_BOUNDARY_CODE,
                f"Node {node!r} is a submodel boundary: {SUBMODEL_BOUNDARY_TEXT}",
                fix=SUBMODEL_BOUNDARY_FIX,
            )
        return None

    nested_ids = {
        nested_id
        for metadata in (graph.submodels or {}).values()
        for nested_id in _nested_graph_nodes(metadata)
    }
    if node in nested_ids:
        return _error(
            SUBMODEL_BOUNDARY_CODE,
            f"Node {node!r} is inside a submodel: {SUBMODEL_BOUNDARY_TEXT}",
            fix=SUBMODEL_BOUNDARY_FIX,
        )
    return _error("unknown_node", f"Unknown node {node!r}.")


@dataclass(frozen=True, slots=True)
class _NodeInput:
    """One incoming edge described the way the node's own saved code sees it.

    `name` is the input name that code binds and `source` the node the edge
    comes from, both as the saved graph has them. `runtime_source` and
    `runtime_port` are the node and port of the flattened graph the engine runs
    that supply the frame: for an edge out of a submodel occurrence, the inner
    node its output port names.
    """

    name: str
    source: str
    runtime_source: str
    runtime_port: str | None


def _node_inputs(graph: PipelineGraph, flat: PipelineGraph, node: str) -> tuple[_NodeInput, ...]:
    """Return each incoming edge of *node*, named as its saved code names it.

    A top-level node's inputs are the saved graph's edges, each located in
    *flat*, the flattened graph; an edge into a submodel occurrence is named by
    the input port it binds. A node only *flat* holds, one inside a submodel
    that a failure was located at, has no saved edges, so its inputs are
    *flat*'s, named as the flattened code binds them.
    """

    if node not in graph.node_map:
        flat_nodes = flat.node_map
        return tuple(
            _NodeInput(
                name=edge_input_name(edge, flat_nodes[edge.source]),
                source=edge.source,
                runtime_source=edge.source,
                runtime_port=edge.sourceHandle,
            )
            for edge in flat.edges
            if edge.target == node and edge.source in flat_nodes
        )
    instances = resolve_submodel_instances(graph)
    occurrence = instances.get(node)
    inputs: list[_NodeInput] = []
    for edge in graph.edges:
        if edge.target != node:
            continue
        source_node = graph.node_map.get(edge.source)
        if source_node is None:
            continue
        origin = instances.get(edge.source)
        if origin is None:
            runtime_source, runtime_port = edge.source, edge.sourceHandle
        else:
            port = bound_output_port(origin, edge)
            runtime_source = qualified_runtime_node_id(edge.source, port.source.node_id)
            runtime_port = port.source.handle_id
        inputs.append(
            _NodeInput(
                name=(
                    edge_input_name(edge, source_node)
                    if occurrence is None
                    else bound_input_port(occurrence, edge).name
                ),
                source=edge.source,
                runtime_source=runtime_source,
                runtime_port=runtime_port,
            )
        )
    return tuple(inputs)


def _occurrence_ports(
    instance: ResolvedSubmodelInstance,
) -> tuple[tuple[str, str, str | None], ...]:
    """Each output port of a submodel occurrence: its name and the flattened node and port."""

    return tuple(
        (
            port.name,
            qualified_runtime_node_id(instance.node.id, port.source.node_id),
            port.source.handle_id,
        )
        for port in instance.definition.output_ports
    )


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
    target: str | None,
    preserve: frozenset[str],
    schema_only: bool = True,
    execution_context: ExecutionContext | None = None,
) -> Mapping[str, object]:
    """Run the production preparation for schema resolution only.

    Exactly the node-data snapshot worker's execution sequence — no
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
    return {
        item.name: _port_schema(lazy_outputs[item.runtime_source], item.runtime_port)
        for item in inputs
    }


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
                target=item.runtime_source,
                preserve=frozenset({item.runtime_source}),
            )
            resolved[item.name] = _port_schema(lazy_outputs[item.runtime_source], item.runtime_port)
        except Exception as exc:  # noqa: BLE001 - one unresolvable input is reportable
            resolved[item.name] = {
                "unresolved_reason": _execution_error_message(
                    exc, operation="inspect_node", site=_FailureSite(graph, item.runtime_source)
                ),
                "source": item.source,
            }
    return resolved


def _failing_step(graph: PipelineGraph, node: str, exc: Exception) -> FailedStep | None:
    """The step of *node* a schema failure came from, when that is known without rerunning.

    A failure raised on a line of the node's code names the step that line
    renders. A lazy plan failure surfaces after the code ran, with no line;
    when every step but one only binds an input (`source`), the plan can fail
    only in that one, the taught `[source, free_code]` form. Any other step
    list is not replayed, because that would run authored code again.
    """

    located = _failed_step(graph, frozenset({node}), exc)
    if located is not None or not isinstance(exc, pl.exceptions.PolarsError):
        return located
    config = next(candidate.data.config for candidate in graph.nodes if candidate.id == node)
    steps = config.get("steps")
    if not isinstance(steps, list):
        return None
    working = [
        (number, step)
        for number, step in enumerate(steps, start=1)
        if isinstance(step, Mapping) and step.get("kind") != "source"
    ]
    if len(working) != 1 or not isinstance(step_id := working[0][1].get("id"), str):
        return None
    return FailedStep(node=node, number=working[0][0], step_id=step_id)


def node_schema(source_file: str, node: str) -> dict[str, object]:
    """`inspect_node`'s schema part: one top-level node's output and input schemas.

    The caller has checked the egress policy permits the part.
    """

    try:
        graph = _parse_graph(source_file)
        validation_error = _validate_top_level_target(graph, node, occurrence_schema=True)
        if validation_error is not None:
            return validation_error
        project_revision = _project_revision(source_file, graph)
        flat = flatten_graph(graph)
        inputs = _node_inputs(graph, flat, node)
        occurrence = resolve_submodel_instances(graph).get(node)
    except Exception as exc:  # noqa: BLE001 - tool boundary must not raise
        return _error(
            "schema_unresolvable",
            _error_message(exc, operation="inspect_node"),
        )
    if occurrence is not None:
        return _occurrence_schema(graph, flat, occurrence, inputs, project_revision)

    try:
        lazy_outputs = _resolve_schema_outputs(
            flat,
            graph,
            target=node,
            preserve=frozenset({node, *(item.runtime_source for item in inputs)}),
        )
        output = lazy_outputs[node]
        result: dict[str, object] = {"node": node}
        if isinstance(output, dict):
            result["ports"] = {port: _schema_for_frame(frame) for port, frame in output.items()}
        else:
            result["columns"] = _schema_for_frame(cast(pl.LazyFrame, output))
        if inputs:
            result["inputs"] = _input_schemas_from_outputs(inputs, lazy_outputs)
        target = next(candidate for candidate in graph.nodes if candidate.id == node)
        if scenarios := _switch_scenarios(target):
            result["scenarios"] = list(scenarios)
        result["project_revision"] = project_revision
        return result
    except NotImplementedError as exc:
        if not str(exc).startswith((INCOMPLETE_TRANSFORM_MESSAGE, INCOMPLETE_STEPS_MESSAGE)):
            return _error(
                "schema_unresolvable",
                _execution_error_message(
                    exc, operation="inspect_node", site=_FailureSite(graph, node)
                ),
            )
        # An authored-but-empty transform (no code, or a step list still
        # incomplete, which the engine reports as either placeholder message
        # followed by the step problem) is an ordinary editing state, not a
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
        # Like an empty node, a failing one still answers which columns arrive on
        # each input, and names the step that raised when that is known.
        step = _failing_step(graph, node, exc)
        return _error(
            "schema_unresolvable",
            _execution_error_message(
                exc, operation="inspect_node", site=_FailureSite(graph, node), step=step
            ),
            **({} if step is None else {"step": step.step_id}),
            inputs=_input_schemas_independently(flat, graph, inputs),
        )


def _occurrence_schema(
    graph: PipelineGraph,
    flat: PipelineGraph,
    occurrence: ResolvedSubmodelInstance,
    inputs: Sequence[_NodeInput],
    project_revision: str,
) -> dict[str, object]:
    """A submodel occurrence's schema part: each output port's and input port's columns.

    Each port resolves on the flattened graph the engine runs, at the inner node
    the definition names, which the answer names only by its port.
    """

    try:
        ports: dict[str, object] = {}
        outputs: dict[str, object] = {}
        for name, runtime, handle in _occurrence_ports(occurrence):
            resolved = _resolve_schema_outputs(
                flat,
                graph,
                target=runtime,
                preserve=frozenset({runtime, *(item.runtime_source for item in inputs)}),
            )
            ports[name] = _port_schema(resolved[runtime], handle)
            outputs.update(resolved)
        result: dict[str, object] = {"node": occurrence.node.id, "ports": ports}
        if inputs:
            result["inputs"] = _input_schemas_from_outputs(inputs, outputs)
        result["project_revision"] = project_revision
        return result
    except Exception as exc:  # noqa: BLE001 - tool boundary must not raise
        # No site: the failing node is inside the submodel, whose code is not
        # the model's to read, so the message names no column.
        return _error(
            "schema_unresolvable",
            _execution_error_message(exc, operation="inspect_node", site=None),
            inputs=_input_schemas_independently(flat, graph, inputs),
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
        policy = resolve_egress_policy(Path.cwd().resolve())
        graph = _parse_graph(source_file)
        project_revision = _project_revision(source_file, graph)
        return {
            **render_pipeline_graph(graph, egress=policy),
            "project_revision": project_revision,
        }
    except Exception as exc:  # noqa: BLE001 - structured tool boundary
        return _error("pipeline_unavailable", _error_message(exc, operation="get_pipeline"))


class TurnContextError(HauteError):
    """The request's context names a node the saved pipeline's top level lacks."""


# The brief's resolved facts for the latest project revisions. A revision covers
# the pipeline file, `haute.toml` and the capability hash, so an edit always
# misses; a data file's header is read again only when the revision changes.
_BRIEF_CACHE: OrderedDict[str, tuple[BriefNode, ...]] = OrderedDict()
_BRIEF_CACHE_SIZE = 8
_BRIEF_CACHE_LOCK = threading.Lock()


def _incomplete(exc: Exception | None) -> bool:
    return isinstance(exc, NotImplementedError) and str(exc).startswith(
        (INCOMPLETE_TRANSFORM_MESSAGE, INCOMPLETE_STEPS_MESSAGE)
    )


def _brief_frames(output: object) -> tuple[BriefFrame, ...]:
    if isinstance(output, dict):
        return tuple(
            BriefFrame(port, tuple(_frame_column_names(frame, None)))
            for port, frame in output.items()
        )
    return (BriefFrame(None, tuple(_frame_column_names(output, None))),)


def _brief_nodes(graph: PipelineGraph) -> tuple[BriefNode, ...]:
    """Every top-level node's brief entry, in graph order, resolved schema-only.

    One engine call resolves every node. When it raises, each node resolves on
    its own, so one broken node marks only itself unresolved and never puts its
    error text in the brief.
    """

    flat = flatten_graph(graph)
    flat_ids = frozenset(node.id for node in flat.nodes)
    outputs: Mapping[str, object] = {}
    try:
        outputs = _resolve_schema_outputs(flat, graph, target=None, preserve=flat_ids)
    except Exception as exc:  # noqa: BLE001 - each node then resolves on its own
        logger.info("assistant_turn_context_graph_unresolved", error=type(exc).__name__)
    resolved: dict[str, tuple[object | None, Exception | None]] = {}

    def output_of(node_id: str) -> tuple[object | None, Exception | None]:
        if node_id not in resolved:
            try:
                output = (
                    outputs[node_id]
                    if node_id in outputs
                    else _resolve_schema_outputs(
                        flat, graph, target=node_id, preserve=frozenset({node_id})
                    )[node_id]
                )
                # A lazy frame raises a column its plan reads but its input lacks
                # only when its schema is read, after resolution has returned.
                _brief_frames(output)
                resolved[node_id] = (output, None)
            except Exception as exc:  # noqa: BLE001 - an unresolved node is reported as such
                logger.info(
                    "assistant_turn_context_node_unresolved",
                    node=node_id,
                    error=type(exc).__name__,
                )
                resolved[node_id] = (None, exc)
        return resolved[node_id]

    def brief_inputs(node_id: str) -> tuple[BriefInput, ...]:
        inputs = []
        for item in _node_inputs(graph, flat, node_id):
            source_output, _failure = output_of(item.runtime_source)
            inputs.append(
                BriefInput(
                    item.name,
                    item.source,
                    None
                    if source_output is None
                    else tuple(_frame_column_names(source_output, item.runtime_port)),
                )
            )
        return tuple(inputs)

    def occurrence_frames(occurrence: ResolvedSubmodelInstance) -> tuple[BriefFrame, ...] | None:
        frames = []
        for name, runtime, handle in _occurrence_ports(occurrence):
            output, _failure = output_of(runtime)
            if output is None:
                return None
            frames.append(BriefFrame(name, tuple(_frame_column_names(output, handle))))
        return tuple(frames)

    occurrences = resolve_submodel_instances(graph)
    nodes: list[BriefNode] = []
    for node in graph.nodes:
        node_type = node.data.nodeType
        if node.id in occurrences:
            # Its ports' columns are metadata; its configuration and inner nodes are not.
            nodes.append(
                BriefNode(
                    node.id,
                    node_type.value,
                    node.data.label,
                    None,
                    brief_inputs(node.id),
                    occurrence_frames(occurrences[node.id]),
                )
            )
            continue
        if node_type in _BOUNDARY_NODE_TYPES or node.id not in flat_ids:
            nodes.append(BriefNode(node.id, node_type.value, node.data.label, None, (), None))
            continue
        inputs = brief_inputs(node.id)
        output, failure = output_of(node.id)
        authoring = node_authoring(node_type, node.data.config)
        if authoring is not None and _incomplete(failure):
            # Resolution also checks what the config alone cannot: a Transform's
            # step inputs against its connected edges.
            authoring = replace(authoring, state="incomplete")
        nodes.append(
            BriefNode(
                node.id,
                node_type.value,
                node.data.label,
                authoring,
                inputs,
                None if output is None else _brief_frames(output),
                _switch_scenarios(node),
            )
        )
    return tuple(nodes)


def _switch_scenarios(node: GraphNode) -> tuple[str, ...]:
    """The source scenarios a Source Switch routes, sorted: names, never its routing."""

    if node.data.nodeType != NodeType.LIVE_SWITCH:
        return ()
    routing = node.data.config.get("input_scenario_map")
    if not isinstance(routing, Mapping):
        return ()
    return tuple(sorted({value for value in routing.values() if isinstance(value, str)}))


def _cached_brief_nodes(graph: PipelineGraph, revision: str) -> tuple[BriefNode, ...]:
    with _BRIEF_CACHE_LOCK:
        cached = _BRIEF_CACHE.get(revision)
        if cached is not None:
            _BRIEF_CACHE.move_to_end(revision)
            return cached
    nodes = _brief_nodes(graph)
    with _BRIEF_CACHE_LOCK:
        _BRIEF_CACHE[revision] = nodes
        _BRIEF_CACHE.move_to_end(revision)
        while len(_BRIEF_CACHE) > _BRIEF_CACHE_SIZE:
            _BRIEF_CACHE.popitem(last=False)
    return nodes


def _preview_error(graph: PipelineGraph, node: str) -> PreviewError:
    """The node's schema-only resolution failure, rendered by the authored-code renderer.

    Its text is present only when the project permits row samples; otherwise it
    is the failure's type, step or line and the column names the policy already
    discloses.
    """

    try:
        _resolve_schema_outputs(
            flatten_graph(graph), graph, target=node, preserve=frozenset({node})
        )
    except Exception as exc:  # noqa: BLE001 - the failure is what was asked for
        return PreviewError(
            node,
            _execution_error_message(exc, operation="turn_context", site=_FailureSite(graph, node)),
        )
    return PreviewError(node, None)


def build_turn_context(
    source_file: str,
    egress: EgressPolicy,
    *,
    selected_node_ids: Sequence[str] = (),
    preview_error_node_id: str | None = None,
    undone: Sequence[AssistantChangeRecord] = (),
    build_plan: AssistantBuildPlan | None = None,
) -> TurnContext:
    """Gather the turn context's facts for the saved pipeline.

    The graph brief, revision, selection and preview error are `internal`
    project metadata, withheld under a `public` policy. A selected or
    preview-error id the saved top level lacks raises `TurnContextError`: the
    canvas it came from is stale. *undone* are the changes the analyst undid
    since the last turn and *build_plan* the session's build plan, each given
    whatever the policy: the model wrote them.
    """

    if egress.max_sensitivity == "public":
        return TurnContext(egress, None, tuple(undone), build_plan)
    graph = _parse_graph(source_file)
    top_level = {node.id: node for node in graph.nodes}
    for node_id in selected_node_ids:
        if node_id not in top_level:
            raise TurnContextError(
                f"The canvas selection names node '{node_id}', which the saved pipeline "
                "does not have. Reload the pipeline and send again."
            )
    if preview_error_node_id is not None and (
        preview_error_node_id not in top_level
        or top_level[preview_error_node_id].data.nodeType in _BOUNDARY_NODE_TYPES
    ):
        raise TurnContextError(
            f"Node '{preview_error_node_id}' has no preview error the assistant can "
            "reproduce: it is not a top-level executable node of the saved pipeline."
        )
    revision = _project_revision(source_file, graph)
    nodes = _cached_brief_nodes(graph, revision)
    by_id = {node.id: node for node in nodes}
    ordered = tuple(
        [by_id[node_id] for node_id in selected_node_ids]
        + [node for node in nodes if node.id not in set(selected_node_ids)]
    )
    return TurnContext(
        egress,
        GraphBrief(
            pipeline_name=graph.pipeline_name or Path(source_file).stem,
            revision=revision,
            nodes=ordered,
            selected_node_ids=tuple(selected_node_ids),
            preview_error=(
                None
                if preview_error_node_id is None
                else _preview_error(graph, preview_error_node_id)
            ),
        ),
        tuple(undone),
        build_plan,
    )


def build_context_update(
    source_file: str,
    egress: EgressPolicy,
    changes: Sequence[AssistantChangeRecord],
) -> ContextUpdate:
    """Gather the turn context update's facts after *changes* were saved.

    The new revision, and the brief entries of the nodes the change records
    name that the saved top level still has, from the same per-revision cache
    as the turn context; the named ids it no longer has are removed. Withheld,
    without reading the pipeline, under a `public` policy.
    """

    if egress.max_sensitivity == "public":
        return ContextUpdate(egress, None)
    graph = _parse_graph(source_file)
    revision = _project_revision(source_file, graph)
    named = touched_node_ids(changes)
    present = {node.id for node in graph.nodes}
    return ContextUpdate(
        egress,
        ChangedGraph(
            revision=revision,
            nodes=tuple(node for node in _cached_brief_nodes(graph, revision) if node.id in named),
            removed_node_ids=tuple(node_id for node_id in named if node_id not in present),
            truncated=any(change.changes.truncated for change in changes),
        ),
    )


async def context_update(
    source_file: str,
    egress: EgressPolicy,
    changes: Sequence[AssistantChangeRecord],
) -> str:
    """Render the turn context update after *changes*, read under the save lock."""

    async with save_lock:
        update = await asyncio.to_thread(build_context_update, source_file, egress, changes)
    return render_context_update(update)


def node_config(source_file: str, node: str) -> dict[str, object]:
    """`inspect_node`'s config part, with executable and secret values redacted by policy."""

    try:
        policy = resolve_egress_policy(Path.cwd().resolve())
        required = _part_requirement(policy, "config")
        if required is not None:
            return _error(
                "egress_policy_denied",
                "Saved node configuration is restricted and exceeds the provider policy.",
                required_policy=required,
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
        return _error("node_config_unavailable", _error_message(exc, operation="inspect_node"))


def _profile_value(value: object) -> object:
    """Render one profile value: JSON-encodable, and bounded in length.

    Every summary is encoded twice before the model reads it — once to bound
    the result, once by the provider adapter — and both encoders take only
    JSON values. `json_safe_scalar` is where a `date` or `Decimal` becomes text
    and an infinity becomes the shared tagged non-finite sentinel; the length
    bound then applies to whatever text results, so a rendered value can no
    more dominate the payload than a stored string can.
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


@dataclass(frozen=True)
class _ColumnProfileRequest:
    """One profile's target, as plain data that crosses into the preview worker."""

    graph: PipelineGraph
    node: str
    input_name: str | None


# One running profile per assistant session: a newer call stops an older one.
_PROFILE_SUPERSESSION = SupersessionCoordinator()


def _prepare_column_profile(
    source_file: str, node: str, input_name: str | None
) -> tuple[_ColumnProfileRequest, str] | dict[str, object]:
    """Gate, parse and validate on the server; return the request or an error."""

    try:
        policy = resolve_egress_policy(Path.cwd().resolve())
        required = _part_requirement(policy, "profile")
        if required is not None:
            return _error(
                "egress_policy_denied",
                f"Column value profiles read project data. Set [assistant.egress] {required} "
                "to enable them.",
                required_policy=required,
            )
        graph = _parse_graph(source_file)
        validation_error = _validate_top_level_target(graph, node)
        if validation_error is not None:
            return validation_error
        project_revision = _project_revision(source_file, graph)
        inputs = _node_inputs(graph, flatten_graph(graph), node)
    except Exception as exc:  # noqa: BLE001 - tool boundary must not raise
        return _error("profile_unavailable", _error_message(exc, operation="inspect_node"))
    if input_name is not None and all(item.name != input_name for item in inputs):
        return _error(
            "unknown_input",
            f"Node {node!r} has no input named {input_name!r}.",
            inputs=[item.name for item in inputs],
        )
    return _ColumnProfileRequest(graph, node, input_name), project_revision


def _profile_failure_site(request: _ColumnProfileRequest) -> _FailureSite:
    """A profile fails collecting a frame whose schema resolved first.

    Profiling a node's own output makes that output's schema permitted
    metadata alongside its inputs'. Profiling one named input collected only
    that input's frame, so only its schema is used: rendering the failure
    never resolves (and so never runs) the consumer the profile did not ask for.
    """

    if request.input_name is None:
        return _FailureSite(request.graph, request.node, own_output=True)
    return _FailureSite(request.graph, request.node, input_name=request.input_name)


def _collect_column_profile(
    request: _ColumnProfileRequest, execution_context: ExecutionContext
) -> dict[str, object]:
    """Prepare the target frame and profile it; an execution failure is a result.

    It is rendered here, where it was raised: in the preview worker the
    exception's traceback, which names the failing node code line, does not
    cross the process boundary.
    """

    try:
        flat = flatten_graph(request.graph)
        if request.input_name is None:
            lazy_outputs = _resolve_frame_outputs(
                flat,
                request.graph,
                target=request.node,
                preserve={request.node},
                execution_context=execution_context,
            )
            output = lazy_outputs[request.node]
            if isinstance(output, dict):
                return _error(
                    "profile_target_ambiguous",
                    f"Node {request.node!r} emits several frames; name one of its ports: "
                    + ", ".join(sorted(output)),
                    ports=sorted(output),
                )
            frame = cast(pl.LazyFrame, output)
        else:
            match = next(
                item
                for item in _node_inputs(request.graph, flat, request.node)
                if item.name == request.input_name
            )
            lazy_outputs = _resolve_frame_outputs(
                flat,
                request.graph,
                target=match.runtime_source,
                preserve={match.runtime_source},
                execution_context=execution_context,
            )
            source_output = lazy_outputs[match.runtime_source]
            if isinstance(source_output, dict):
                if match.runtime_port is None or match.runtime_port not in source_output:
                    return _error(
                        "profile_unavailable",
                        f"Input {request.input_name!r} does not resolve to one emitted frame.",
                    )
                frame = source_output[match.runtime_port]
            else:
                frame = cast(pl.LazyFrame, source_output)
        return _profile_frame(frame, execution_context=execution_context)
    except ExecutionCancelledError:
        return _error("profile_unavailable", "The column profile was stopped before it finished.")
    except Exception as exc:  # noqa: BLE001 - tool boundary must not raise
        return _error(
            "profile_unavailable",
            _execution_error_message(
                exc, operation="inspect_node", site=_profile_failure_site(request)
            ),
        )


def _run_column_profile_worker(
    request: _ColumnProfileRequest, budget: IsolatedExecutionBudget
) -> dict[str, object]:
    """The preview worker's half: profile under the budget the server admitted."""

    context = create_isolated_execution_context(budget)
    try:
        return _collect_column_profile(request, context)
    finally:
        context.release_admission(preserve_primary_error=True)


async def _run_column_profile(
    request: _ColumnProfileRequest,
    token: ExecutionCancellationToken,
    affinity_key: tuple[str, str],
) -> dict[str, object]:
    """Admit one preview execution, then collect in the preview worker.

    A cancelled call (the turn stopped) stops the worker before it returns.
    In thread mode the collection cannot be interrupted mid-query: the token
    stops the engine at its next checkpoint, and the admission is released
    when the thread finishes.
    """

    context = create_admitted_execution_context(
        operation="assistant_column_profiles",
        profile=ExecutionProfile.PREVIEW_EAGER,
        cancellation_token=token,
    )
    release_on_exit = True
    try:
        if resolve_interactive_execution_mode() == "process":
            budget = isolated_execution_budget(context)
            return await run_in_interactive_worker(
                _run_column_profile_worker,
                request,
                budget,
                affinity_key=affinity_key,
                timeout_seconds=preview_timeout(),
                stop_reason=(lambda: "superseded" if token.cancelled else None),
                absolute_rss_limit_bytes=budget.process_rss_limit_bytes,
                memory_growth_limit_bytes=budget.memory_limit_bytes,
                require_memory_limit=resolve_worker_memory_enforcement() == "required",
            )
        collection = asyncio.ensure_future(
            asyncio.to_thread(_collect_column_profile, request, context)
        )
        try:
            return await asyncio.shield(collection)
        except asyncio.CancelledError:
            token.cancel()
            release_on_exit = False
            collection.add_done_callback(lambda _done: context.release_admission())
            raise
    finally:
        if release_on_exit:
            context.release_admission(preserve_primary_error=True)


def _profile_worker_failure(
    exc: InteractiveWorkerError | SupersededRequestError,
) -> dict[str, object]:
    """Report a preview-worker outcome with a parent-authored, data-free message."""

    if isinstance(exc, InteractiveWorkerError) and exc.terminal_reason == "memory_limited":
        return _error(
            "profile_unavailable",
            "Profiling this frame exceeded the preview memory budget. Profile a frame "
            "upstream of the step that multiplies rows, or narrow its input.",
        )
    if isinstance(
        exc,
        InteractiveWorkerTimeoutError | InteractiveWorkerStoppedError | SupersededRequestError,
    ):
        return _error("profile_unavailable", str(exc))
    logger.error(
        "assistant_tool_failed",
        operation="inspect_node",
        error_class=type(exc).__name__,
        error_message=str(exc),
    )
    return _error("profile_unavailable", _INTERNAL_ERROR_DETAIL)


async def column_profiles(
    source_file: str,
    node: str,
    input_name: str | None = None,
    *,
    session_id: str,
) -> dict[str, object]:
    """`inspect_node`'s profile part: the values in one node frame, without returning rows.

    This is the only part that reads data, and it never emits a row: a value
    only appears as a distinct level of a small-cardinality column, alongside
    its count. Authoring correct code needs the encoding of a categorical
    column, and inferring one from its name is guesswork the model has no way
    to check. The frame is collected in the interactive preview worker, under
    its memory cap, so joins and aggregations upstream run as they do for an
    editor preview.
    """

    prepared = await asyncio.to_thread(_prepare_column_profile, source_file, node, input_name)
    if isinstance(prepared, dict):
        return prepared
    request, project_revision = prepared
    token = ExecutionCancellationToken()
    session_key = ("assistant_column_profiles", session_id)
    try:
        profile = await _PROFILE_SUPERSESSION.run_latest(
            session_key,
            partial(_run_column_profile, request, token, session_key),
            cancel_active=token.cancel,
            superseded_message=(
                "A newer column profile in this assistant session replaced this one."
            ),
        )
    except (InteractiveWorkerError, SupersededRequestError) as exc:
        return _profile_worker_failure(exc)
    except Exception as exc:  # noqa: BLE001 - tool boundary must not raise
        # Naming a failure's columns can resolve schemas, which runs the engine.
        failure = await asyncio.to_thread(
            _execution_error_message,
            exc,
            operation="inspect_node",
            site=_profile_failure_site(request),
        )
        return _error("profile_unavailable", failure)
    if "error" in profile:
        return profile
    return {
        "node": node,
        "input": input_name,
        **profile,
        "max_levels": _MAX_PROFILE_LEVELS,
        "project_revision": project_revision,
    }


#: What each `inspect_node` part answers, said when a denied call can use it instead.
_AVAILABLE_PARTS = {
    "schema": (
        "The schema part is permitted: it lists the node's output columns with their "
        "dtypes, where a struct dtype names its fields, and each input's columns."
    ),
    "config": "The config part is permitted: it returns the node's saved configuration.",
    "profile": ("The profile part is permitted: it summarises the values in the node's columns."),
}


def _part_requirement(policy: EgressPolicy, part: str) -> str | None:
    """The `[assistant.egress]` setting an `inspect_node` part needs, or None when permitted."""

    if policy.max_sensitivity == "public":
        return 'max_sensitivity = "internal"'
    if part == "config" and policy.max_sensitivity != "restricted":
        return 'max_sensitivity = "restricted"'
    if part == "profile" and not policy.allow_row_samples:
        return "allow_row_samples = true"
    return None


async def inspect_node(
    source_file: str,
    node: str,
    parts: Sequence[str] = ("schema",),
    input_name: str | None = None,
    *,
    session_id: str,
) -> dict[str, object]:
    """Answer the requested parts of one node, each under its own egress check.

    A denied part is listed under ``withheld`` with the setting it needs while
    the permitted parts answer; when every part is denied the call is refused.
    A part that fails fails the call, naming the part.
    """

    if input_name is not None and "profile" not in parts:
        return _error(
            "invalid_request",
            'input names the frame the "profile" part reads; add "profile" to parts or omit input.',
            validation_path="inspect_node.input",
            validation_reason="input_without_profile",
        )
    try:
        policy = resolve_egress_policy(Path.cwd().resolve())
    except Exception as exc:  # noqa: BLE001 - tool boundary must not raise
        return _error("egress_policy_unavailable", _error_message(exc, operation="inspect_node"))
    requested = [part for part in INSPECT_NODE_PARTS if part in parts]
    withheld = [
        {"part": part, "required_policy": required}
        for part in requested
        if (required := _part_requirement(policy, part)) is not None
    ]
    permitted = [part for part in requested if _part_requirement(policy, part) is None]
    if not permitted:
        available = [part for part in INSPECT_NODE_PARTS if _part_requirement(policy, part) is None]
        return _error(
            "egress_policy_denied",
            "The egress policy permits none of the requested parts; withheld names the "
            "setting each one needs." + "".join(f" {_AVAILABLE_PARTS[part]}" for part in available),
            withheld=withheld,
            **({"available": available} if available else {}),
        )
    result: dict[str, object] = {"node": node}
    revisions: set[object] = set()
    for part in permitted:
        if part == "schema":
            answer = await asyncio.to_thread(node_schema, source_file, node)
        elif part == "config":
            answer = await asyncio.to_thread(node_config, source_file, node)
        else:
            answer = await column_profiles(source_file, node, input_name, session_id=session_id)
        error = answer.get("error")
        if isinstance(error, Mapping):
            return {"error": {**error, "part": part}}
        revisions.add(answer.pop("project_revision"))
        answer.pop("node")
        result[part] = answer
    if len(revisions) != 1:
        raise RuntimeError("inspect_node parts described different project revisions")
    if withheld:
        result["withheld"] = withheld
    result["project_revision"] = revisions.pop()
    return result


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


def dataset_listing(
    directory: str | None = None,
    *,
    recursive: bool = False,
) -> dict[str, object]:
    """List safe project data files using the installed input registry."""

    try:
        if not isinstance(recursive, bool):
            return _error("invalid_request", "recursive must be a boolean.")
        base = Path.cwd().resolve()
        target = contained_path(base, directory or ".")
        if _dataset_path_forbidden(target, base):
            return _error(
                "dataset_path_forbidden",
                "Hidden, state, and credential paths are unavailable to assistant dataset tools.",
            )
        if not target.is_dir():
            return _error("directory_not_found", f"Directory not found: {directory or '.'}.")
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
        return _error("dataset_list_unavailable", _error_message(exc, operation="find_data"))


def dataset_schema(
    path: str,
    *,
    source_file: str | None = None,
) -> dict[str, object]:
    """Return a dataset's schema without materialising or returning row values."""

    try:
        base = Path.cwd().resolve()
        target = contained_path(base, path)
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
            _error_message(exc, operation="find_data"),
        )


def find_data(
    source_file: str,
    directory: str | None = None,
    *,
    recursive: bool = False,
    path: str | None = None,
) -> dict[str, object]:
    """List one project directory's data files, with one file's schema when *path* names it."""

    listing = dataset_listing(directory, recursive=recursive)
    if path is None or "error" in listing:
        return listing
    schema = dataset_schema(path, source_file=source_file)
    if "error" in schema:
        return schema
    project_revision = schema.pop("project_revision")
    return {**listing, "schema": schema, "project_revision": project_revision}


#: The `read_reference` id of the authoring guide; every other id is `<kind>:<name>`.
_GUIDE_REFERENCE = "guide"


def _authoring_guide_reference() -> dict[str, object]:
    content = authoring_guide()
    return {
        "id": "haute-authoring-guide",
        "version": "1",
        "sha256": sha256(content.encode("utf-8")).hexdigest(),
        "source": "package:haute.assistant/assets/authoring_guide.md",
        "sensitivity": "public",
        "evidence_class": "canonical_library_guidance",
        "approval_status": "reviewed",
        "content": content,
        "step_grammar": step_grammar(),
    }


def _reference_ids() -> tuple[str, ...]:
    """Every id `read_reference` serves: the guide, then nodes, recipes and examples."""

    manifest = capability_manifest()
    return (
        _GUIDE_REFERENCE,
        *(f"node:{node.id}" for node in manifest.nodes),
        *(f"recipe:{recipe['id']}" for recipe in manifest.recipes),
        *(f"example:{name}" for name, _summary in example_index()),
    )


def _close_reference_ids(reference: str, valid: Sequence[str]) -> list[str]:
    """Valid ids naming *reference* under a namespace, then close spellings; at most three."""

    namespaced = [item for item in valid if item.partition(":")[2] == reference]
    close = difflib.get_close_matches(reference, valid, n=3, cutoff=0.6)
    return list(dict.fromkeys((*namespaced, *close)))[:3]


def _reference_content(reference: str) -> dict[str, object]:
    if reference == _GUIDE_REFERENCE:
        return _authoring_guide_reference()
    kind, _separator, name = reference.partition(":")
    manifest = capability_manifest()
    if kind == "node":
        return next(node for node in manifest.nodes if node.id == name).as_dict()
    if kind == "recipe":
        recipe = next(recipe for recipe in manifest.recipes if recipe["id"] == name)
        return cast(dict[str, object], materialise_json(recipe))
    example = load_example(name)
    if "error" in example:
        raise RuntimeError(f"Indexed example {name!r} could not be loaded")
    return example


def read_reference(references: Sequence[str]) -> dict[str, object]:
    """Return one ordered, all-or-nothing batch of library references."""

    try:
        valid = _reference_ids()
        unknown = [reference for reference in references if reference not in valid]
        if unknown:
            close = _close_reference_ids(unknown[0], valid)
            return _error(
                "unknown_reference",
                f'No reference has the id {unknown[0]!r}. Ids are "guide", '
                '"node:<node type id>", "recipe:<recipe id>" and "example:<example name>".',
                id=unknown[0],
                fix=(f"Use {close[0]!r}." if close else "Use an id from the prompt's indexes."),
                **({"did_you_mean": close} if close else {}),
            )
        items = [
            {"id": reference, "content": _reference_content(reference)} for reference in references
        ]
        return {"count": len(items), "references": items}
    except Exception as exc:  # noqa: BLE001 - structured tool boundary
        return _error(
            "reference_unavailable",
            _error_message(exc, operation="read_reference"),
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


def _publish_document_update(
    source_file: str, change: AssistantChangeRecord | None, *, session_id: str
) -> str:
    """Publish the file watcher's pipeline-document payload, tagged with *change*.

    The origin names the chat and the change, and every node id the change
    names, because the canvas receives this update before the change card.
    """

    from haute._pipeline_recovery import pipeline_document_fingerprint
    from haute.server import _wire_source_file

    document_payload = load_pipeline_editor_document(
        Path(source_file), project_root=Path.cwd()
    ).model_dump(mode="json", by_alias=True)
    fingerprint = pipeline_document_fingerprint(document_payload)
    payload: PipelineDocumentUpdatePayload = {
        "document": document_payload,
        "document_fingerprint": fingerprint,
        "source_file": _wire_source_file(Path(source_file)),
    }
    if change is not None:
        payload["origin"] = {
            "kind": "assistant",
            "session_id": session_id,
            "change_id": change.id,
            "node_ids": list(touched_node_ids([change])),
        }
    default_bus.publish("pipeline.document.update", payload)
    return fingerprint


def _no_publication(source_file: str, change: AssistantChangeRecord | None) -> str:
    raise RuntimeError("A dry-run never publishes a document update.")


def application_service(
    project_sources: tuple[Path | ProjectSourceEvidence, ...] = (),
    *,
    session_id: str | None,
) -> PipelineApplicationService:
    """The application service for the server's project.

    Its saves publish as chat *session_id*; a service built for a dry-run, which
    never saves, has none and refuses to publish.
    """

    project_root = Path.cwd().resolve()

    return PipelineApplicationService(
        project_root=project_root,
        pipeline_root=pipeline_dir(),
        mutations_readiness=mutations_readiness,
        publish_document_update=(
            _no_publication
            if session_id is None
            else partial(_publish_document_update, session_id=session_id)
        ),
        plan_store=_PLAN_STORE,
        parse_graph=parse_pipeline_to_graph,
        project_sources=lambda _source_file: project_sources,
    )


def _close_matches(
    names: Sequence[str], inputs: Mapping[str, Sequence[str]]
) -> dict[str, list[str]]:
    """Each name no input provides, with its close matches among the inputs' names."""

    pool = sorted({*inputs, *(column for columns in inputs.values() for column in columns)})
    matches: dict[str, list[str]] = {}
    for name in names:
        if name in pool:
            continue
        close = difflib.get_close_matches(name, pool, n=3, cutoff=0.6)
        if close:
            matches[name] = close
    return matches


def _located_error(
    code: str,
    message: str,
    exc: LocatedPlanError,
    *,
    named_columns: Sequence[str] = (),
    fix: Callable[[Mapping[str, list[str]]], str] | None = None,
    **fields: object,
) -> dict[str, object]:
    """Render a plan failure with where it happened and how to correct it.

    `context.inputs` and `did_you_mean` are schema metadata. The dry-run and
    apply tools that reach here are internal-project tools, so the executor
    has already required the policy that permits that metadata, the one
    `inspect_node`'s schema part needs. `fix`, when given, composes the correction from
    the close matches of `named_columns`; otherwise the failure's own `fix`
    is used. Without close column matches, `did_you_mean` is the failure's
    own close names, such as the node ids near an unknown node reference.
    """

    envelope: dict[str, object] = dict(fields)
    if exc.where:
        envelope["where"] = dict(exc.where)
    node = exc.where.get("node")
    inputs: dict[str, list[str]] = {}
    if isinstance(node, str) and exc.graph is not None:
        inputs = _input_columns(exc.graph, node)
    if inputs:
        envelope["context"] = {"inputs": inputs}
    matches = _close_matches(named_columns, inputs)
    suggestions = list(dict.fromkeys(match for close in matches.values() for match in close))
    suggestions = suggestions or list(exc.did_you_mean)
    if suggestions:
        envelope["did_you_mean"] = suggestions[:3]
    correction = fix(matches) if fix is not None else exc.fix
    if correction is not None:
        envelope["fix"] = correction
    return _error(code, message, **envelope)


def _schema_fix(exc: SchemaUnresolvableError) -> Callable[[Mapping[str, list[str]]], str]:
    """The correction for a schema failure: the replacement name when one is close."""

    target = (
        f"step {exc.step.step_id!r} of node {exc.step.node!r}"
        if exc.step is not None
        else f"node {exc.node!r}"
    )

    def fix(matches: Mapping[str, list[str]]) -> str:
        if matches:
            name, close = next(iter(matches.items()))
            return f"Replace {name!r} with {close[0]!r} in {target}."
        return f"Correct {target} so it reads only columns its inputs provide, then dry-run again."

    return fix


def _operation_error(exc: LocatedPlanError, *, operation: str) -> dict[str, object]:
    if isinstance(exc, SchemaUnresolvableError):
        if exc.graph is None:
            raise RuntimeError("a schema failure must carry the graph that ran")
        site = _FailureSite(
            exc.graph,
            exc.step.node if exc.step is not None else exc.node,
            submitted=exc.submitted,
        )
        failure, named = _execution_failure(
            exc.failure, operation=operation, site=site, step=exc.step
        )
        if isinstance(exc.failure, ConfigSettingError):
            correction = exc.failure.fix or (
                f"Correct {exc.failure.setting} on node {exc.node!r} as the message says, "
                "then dry-run again."
            )
            return _located_error(exc.code, f"{exc} {failure}", exc, fix=lambda _: correction)
        return _located_error(
            exc.code, f"{exc} {failure}", exc, named_columns=named, fix=_schema_fix(exc)
        )
    if isinstance(exc, InvalidConfigError):
        message = _config_setting_message(exc.failure, submitted=exc.submitted)
        return _located_error(exc.code, f"{exc} {message}", exc)
    if isinstance(exc, PreambleFailedError):
        # A preamble failure names no column, so it needs no failure site.
        failure = _execution_error_message(exc.failure, operation=operation, site=None)
        return _error(
            exc.code,
            f"{exc} {failure}",
            fix="Correct the pipeline preamble so it runs without error.",
        )
    if isinstance(exc, RenameConsumersError):
        consumers = [{"node": node, "field": field} for node, field in exc.consumers]
        return _located_error(exc.code, str(exc), exc, consumers=consumers)
    if isinstance(exc, OpValidationError):
        return _located_error("invalid_ops", str(exc), exc)
    if isinstance(exc, AssistantOperationError):
        return _located_error(exc.code, str(exc), exc)
    raise TypeError(f"unexpected plan failure {type(exc).__name__}")


def _recipe_operation_error(exc: RecipeOperationError) -> dict[str, object]:
    """A recipe failure, located at the `recipe` operation the model sent."""

    where: dict[str, object] = {"op_index": exc.op_index, "recipe": exc.recipe_id}
    argument = exc.context.get("argument")
    if isinstance(argument, str):
        where["field"] = f"arguments.{argument}"
    reference = f"recipe:{exc.recipe_id}" if exc.recipe_id else "recipe:<id>"
    return _error(
        exc.code,
        str(exc),
        where=where,
        fix=(
            f"Correct operation {exc.op_index}'s arguments as the message says; "
            f"read_reference {reference!r} gives the recipe's argument schema."
        ),
        **{key: value for key, value in exc.context.items() if key != "argument"},
    )


async def dry_run_graph_edits(
    source_file: str,
    ops_payload: object,
    *,
    summary: str,
    config_visibility: ConfigVisibility | None,
    assumptions: Sequence[str] = (),
    postconditions: object = (),
    project_sources: tuple[Path | ProjectSourceEvidence, ...] = (),
) -> dict[str, object]:
    """Validate and retain an exact graph-edit plan and its receipt without writing.

    Each `recipe` operation is expanded in place first; every failure names an
    operation by its index in *ops_payload*, and a recipe operation's by its
    `recipe` too. *config_visibility* says what the model has seen of saved
    node configuration, so a blind rewrite of a saved list or map is refused;
    the executor always passes it, and None checks no rewrite.
    """

    try:
        batch = expand_recipe_operations(ops_payload)
    except RecipeOperationError as exc:
        return _recipe_operation_error(exc)
    try:
        async with save_lock:
            result = await asyncio.to_thread(
                partial(
                    application_service(project_sources, session_id=None).dry_run,
                    source_file,
                    batch.operations,  # type: ignore[arg-type]
                    postconditions=postconditions,  # type: ignore[arg-type]
                    summary=summary,
                    assumptions=assumptions,
                    positions=batch.positions or None,
                    config_visibility=config_visibility,
                )
            )
        return result.as_dict()
    except LocatedPlanError as exc:
        op_index = exc.where.get("op_index")
        if isinstance(op_index, int) and op_index in batch.recipes:
            exc.where = {**exc.where, "recipe": batch.recipes[op_index]}
        # Naming a failure's columns can resolve schemas, which runs the engine.
        return await asyncio.to_thread(_operation_error, exc, operation="dry_run_graph_edits")
    except Exception as exc:  # noqa: BLE001 - tool boundary must not raise
        # `invalid_plan` is a specific authorization verdict raised by the
        # domain layer. Reusing it for an unexpected exception told the model
        # its plan was rejected when nothing had judged the plan at all.
        return _error("operation_failed", _error_message(exc, operation="dry_run_graph_edits"))


async def apply_graph_plan(
    source_file: str,
    plan_hash: str,
    *,
    session_id: str,
) -> dict[str, object]:
    """Apply and verify one exact, single-use plan."""

    try:
        result = await application_service(session_id=session_id).apply(
            source_file,
            plan_hash,
        )
        return result.as_dict()
    except CommittedVerificationError as exc:
        # The committed save's change record sits beside the error, where a
        # successful apply's sits, so the chat shows its card and can undo it.
        fields = dict(exc.result)
        change = fields.pop("change", None)
        failure = _error(exc.code, str(exc), **fields)
        if change is not None:
            failure["change"] = change
        return failure
    except LocatedPlanError as exc:
        return await asyncio.to_thread(_operation_error, exc, operation="apply_graph_plan")
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


# Tools that were removed, each refused with what replaces it rather than as an
# unknown name: a resumed session's history can still name one.
_REMOVED_TOOLS: dict[str, str] = {
    "list_node_types": (
        "list_node_types was removed; the prompt's node index lists every node type, and "
        'read_reference with "node:<node type id>" gives its full descriptor.'
    ),
    "get_capability_manifest": (
        "get_capability_manifest was removed; the prompt carries the manifest's indexes."
    ),
    "get_capability_descriptors": (
        "get_capability_descriptors was removed; use read_reference with "
        '"node:<node type id>" or "recipe:<recipe id>".'
    ),
    "get_example": 'get_example was removed; use read_reference with "example:<example name>".',
    "get_authoring_guide": 'get_authoring_guide was removed; use read_reference with "guide".',
    "get_node_schema": 'get_node_schema was removed; use inspect_node with parts ["schema"].',
    "get_node_config": 'get_node_config was removed; use inspect_node with parts ["config"].',
    "get_column_profiles": (
        'get_column_profiles was removed; use inspect_node with parts ["profile"].'
    ),
    "list_datasets": "list_datasets was removed; use find_data.",
    "get_dataset_schema": "get_dataset_schema was removed; use find_data with the file's path.",
    "plan_recipe": (
        'plan_recipe was removed; add {"op": "recipe", "recipe": <recipe id>, "arguments": '
        "{...}} to the ops of dry_run_graph_edits."
    ),
    "dry_run_recipe_plan": (
        'dry_run_recipe_plan was removed; add {"op": "recipe", "recipe": <recipe id>, '
        '"arguments": {...}} to the ops of dry_run_graph_edits.'
    ),
}


#: Tool names a model reaches for to ask the analyst, which is a reply, not a tool.
_ASKING_TOOL_NAMES = frozenset({"ask", "ask_user", "clarify", "question"})


def _dispatch_error(name: str, message: str) -> dict[str, object]:
    fields: dict[str, object] = {"name": name, "valid_names": list(_TOOL_NAMES)}
    close = difflib.get_close_matches(name, _TOOL_NAMES, n=3, cutoff=0.6)
    if close:
        fields["did_you_mean"] = close
    if name in _ASKING_TOOL_NAMES:
        fix = "To ask the analyst, end your reply with a line starting NEEDS_INPUT:."
    else:
        fix = f"Call {close[0]} instead." if close else "Call one of valid_names."
    return _error("unknown_tool", message, fix=fix, **fields)


#: Error codes no corrected call can clear within the turn: an internal
#: failure, a policy refusal, an interrupted call, a spent dry-run budget, a
#: save that committed unverified, a result too large for the model's
#: context, or a read or edit of a submodel's nodes. Every other code is a
#: rejection the model can correct.
_NON_RETRYABLE_CODES = frozenset(
    {
        "tool_failed",
        "operation_failed",
        "mutation_failed",
        "egress_policy_denied",
        "egress_policy_unavailable",
        "tool_interrupted",
        "dry_run_retry_limit",
        "verification_failed",
        "tool_result_too_large",
        SUBMODEL_BOUNDARY_CODE,
    }
)


def _with_retryable(result: Mapping[str, object]) -> Mapping[str, object]:
    """Mark an error result retryable unless its code is one no correction clears."""

    error = result.get("error")
    if not isinstance(error, Mapping):
        return result
    code = error.get("code")
    if not isinstance(code, str):
        raise RuntimeError("a tool error must carry a string code")
    return {**result, "error": {**error, "retryable": code not in _NON_RETRYABLE_CODES}}


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


#: Fields one graph operation does not take that another does, with the
#: correction an `unknown_field` rejection carries.
_MISPLACED_FIELD_FIXES: dict[tuple[object, str], str] = {
    ("update_node", "edits"): (
        'Change steps by id with {"op": "edit_steps", "node": <node id>, "edits": [...]}; '
        "update_node writes config keys."
    ),
}


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


#: Characters of the model's own JSON text shown on each side of a decode error.
_JSON_ERROR_EXCERPT = 30
#: Each JSON bracket that opens a container, with the one that closes it.
_JSON_OPENERS = {"{": "}", "[": "]"}
#: The most still-open brackets a decode error names, innermost first.
_JSON_OPENERS_SHOWN = 3


@dataclass(frozen=True)
class _JsonOpener:
    """A bracket of JSON text still open: ``{`` or ``[``, its character position,
    and the key it is the value of (``None`` for an item of a list or the top level)."""

    char: str
    position: int
    key: str | None


@dataclass(frozen=True)
class _JsonScan:
    """JSON text scanned up to a position: the brackets still open there, outermost
    first, and whether a complete value ends there, rather than a key, a ``,`` or
    ``:``, an opening bracket, or a string still being read."""

    opened: tuple[_JsonOpener, ...]
    after_value: bool


def _scan_json_brackets(text: str, end: int) -> _JsonScan:
    """Scan *text* up to character *end*, skipping the contents of strings and their escapes.

    *end* is a decode error's position, so the text before it is a valid JSON
    prefix: a closer there that does not close the innermost bracket is a bug.
    """

    opened: list[_JsonOpener] = []
    string_start: int | None = None
    key: str | None = None
    # The last token read: "open", ",", ":", "key" or "value" ("" before any).
    last = ""
    index = 0
    while index < end:
        char = text[index]
        if string_start is not None:
            if char == "\\":
                index += 1
            elif char == '"':
                if opened and opened[-1].char == "{" and last in ("open", ","):
                    key, last = text[string_start + 1 : index], "key"
                else:
                    last = "value"
                string_start = None
        elif char == '"':
            string_start = index
        elif char in _JSON_OPENERS:
            opened.append(_JsonOpener(char, index, key))
            key, last = None, "open"
        elif char in "]}":
            if not opened or _JSON_OPENERS[opened.pop().char] != char:
                raise RuntimeError(f"JSON text before a decode error closes {char!r} at {index}")
            key, last = None, "value"
        elif char in ",:":
            last = char
            if char == ",":
                key = None
        elif not char.isspace():
            last = "value"  # a number, true, false or null
        index += 1
    return _JsonScan(tuple(opened), string_start is None and last == "value")


def _json_opener_role(opened: Sequence[_JsonOpener], index: int) -> str:
    """Where the bracket *opened[index]* sits: the key it is the value of, or the list it is in."""

    opener = opened[index]
    if opener.key is not None:
        return f'the value of "{opener.key}"'
    if index == 0:
        return "the top level"
    parent = opened[index - 1]
    if parent.key is not None:
        return f'an item of "{parent.key}"'
    if index == 1:
        return "an item of the top-level list"
    return f"an item of the list from character {parent.position}"


def _json_bracket_diagnosis(text: str, position: int) -> tuple[str, str] | None:
    """The brackets a decode error at *position* leaves open, and the likely fix.

    Only a fault the brackets explain is diagnosed: after a complete value, a
    closer at *position* that does not close the innermost open bracket, or the
    text ending with brackets open. Anything else (every bracket closed, an error
    inside a string, after a key, a ``,`` or a ``:``, or at another character)
    is another fault, and None is returned.
    """

    scan = _scan_json_brackets(text, position)
    if not scan.after_value or not scan.opened:
        return None
    innermost = scan.opened[-1]
    if text[position:].strip():
        found = text[position]
        expected = _JSON_OPENERS[innermost.char]
        if found not in "]}" or found == expected:
            return None
        place = f"character {position}"
        noun = "object" if innermost.char == "{" else "list"
        fix = (
            f'Close the {noun} opened at character {innermost.position} with "{expected}" '
            f'before the "{found}" at character {position}'
        )
    else:
        place = "the end of the text"
        closers = "".join(_JSON_OPENERS[opener.char] for opener in reversed(scan.opened))
        fix = f'Close them at the end of the text with "{closers}"'
    depth = len(scan.opened)
    shown = ", ".join(
        f'"{scan.opened[index].char}" from character {scan.opened[index].position} '
        f"({_json_opener_role(scan.opened, index)})"
        for index in reversed(range(max(0, depth - _JSON_OPENERS_SHOWN), depth))
    )
    return (
        f"brackets still open at {place}, innermost first: {shown}.",
        f"{fix}, then resend the call.",
    )


def _refuse_undecodable_json_text(text: str, path: str) -> None:
    """Locate the decode error of JSON text sent where an array or object belongs.

    A provider that sends containers as JSON text (Databricks' Qwen dialect)
    cannot send the value itself, so a text that opens like JSON but does not
    decode is refused at the decoder's character with the text around it: the
    model's own argument, already in its history. When brackets left open
    explain the error, the message names them and the fix says how to close
    them; the text is never repaired. Text that decodes returns, for the plain
    wrong-type refusal.
    """

    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        start = max(0, exc.pos - _JSON_ERROR_EXCERPT)
        end = exc.pos + _JSON_ERROR_EXCERPT
        excerpt = (
            ("..." if start else "")
            + text[start : exc.pos]
            + "<<here>>"
            + text[exc.pos : end]
            + ("..." if end < len(text) else "")
        )
        message = f"{path} is JSON text with an error at character {exc.pos} ({exc.msg}): {excerpt}"
        fix = (
            "Correct the JSON text at that character and resend the call. Inside a "
            "string such as a step's code, escape each double quote as \\\" and each "
            "newline as \\n, and close the string before the next key."
        )
        diagnosis = _json_bracket_diagnosis(text, exc.pos)
        if diagnosis is not None:
            still_open, fix = diagnosis
            message = f"{message}; {still_open}"
        raise _ToolArgumentValidationError(
            path, "invalid_json_text", message, fields={"fix": fix}
        ) from None


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
            if not selected:
                raise _ToolArgumentValidationError(
                    discriminator_path,
                    "unsupported_discriminator",
                    f"{discriminator_path} does not identify a supported operation variant",
                )
            # Several variants share this value (every recipe operation has op
            # "recipe"): the next discriminator that separates them selects one.
            _validate_tool_value(
                value,
                selected[0] if len(selected) == 1 else {union_keyword: selected},
                path=path,
            )
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
        if (
            isinstance(value, str)
            and {"array", "object"} & set(expected_types)
            and value.lstrip()[:1] in ("[", "{")
        ):
            _refuse_undecodable_json_text(value, path)
        article = "an" if received[:1] in "aeiou" else "a"
        # A boolean spells its literals: a model writing Python's `True`
        # cannot tell from the type name alone that JSON wants `true`.
        expected = " or ".join(
            "boolean (true or false)" if name == "boolean" else name for name in expected_types
        )
        # The field's own description says what a correct value looks like,
        # e.g. the text form a categorical rule value must take.
        description = schema.get("description")
        raise _ToolArgumentValidationError(
            path,
            "wrong_type",
            f"{path} must be JSON {expected}, but "
            f"{article} {received} was sent"
            + (
                ". Send the value itself, not a JSON-encoded string of it"
                if received == "string" and {"array", "object"} & set(expected_types)
                else ""
            )
            + (f". {description}" if isinstance(description, str) and description else ""),
            fields={"expected_types": list(expected_types), "received_type": received},
        )

    if isinstance(value, Mapping) and "object" in expected_types:
        raw_properties = schema.get("properties", {})
        raw_required = schema.get("required", ())
        if not isinstance(raw_properties, Mapping) or not isinstance(raw_required, (list, tuple)):
            raise RuntimeError(f"Invalid operation object schema at {path}")
        required = {str(item) for item in raw_required}
        missing = sorted(required.difference(value))
        unknown = sorted(str(key) for key in value if key not in raw_properties)
        additional = schema.get("additionalProperties", True)
        # A field another operation takes says which operation was meant, so it
        # is reported ahead of the fields this one misses.
        misplaced = [_MISPLACED_FIELD_FIXES.get((value.get("op"), key)) for key in unknown]
        hinted = additional is False and any(misplaced)
        if missing and not hinted:
            missing_path = f"{path}.{missing[0]}"
            raise _ToolArgumentValidationError(
                missing_path,
                "missing_required",
                f"{missing_path} is required",
            )
        if unknown and additional is False:
            # Naming the rejected key and the closed allowlist is what makes
            # this correctable in one retry. Both are already known to the
            # model — it sent the key, and the allowlist is its own schema.
            allowed = sorted(str(key) for key in raw_properties)
            fix = next((item for item in misplaced if item is not None), None)
            raise _ToolArgumentValidationError(
                path,
                "unknown_field",
                f"{path} does not allow the field(s) {', '.join(unknown)}; "
                f"this variant accepts only {', '.join(allowed)}",
                fields={
                    "unknown_fields": unknown,
                    "allowed_fields": allowed,
                    **({"fix": fix} if fix is not None else {}),
                },
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
                f"{path} has too few items: at least {minimum}",
            )
        if isinstance(maximum, int) and len(value) > maximum:
            raise _ToolArgumentValidationError(
                path,
                "too_many_items",
                f"{path} has too many items: at most {maximum}",
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
                f"{path} is too short: at least {minimum_length} characters",
            )
        if isinstance(maximum_length, int) and len(value) > maximum_length:
            raise _ToolArgumentValidationError(
                path,
                "too_long",
                f"{path} is too long: at most {maximum_length} characters",
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
                f"{path} is below its minimum of {minimum}",
            )
        if isinstance(maximum, int | float) and value > maximum:
            raise _ToolArgumentValidationError(
                path,
                "above_maximum",
                f"{path} is above its maximum of {maximum}",
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
        allowed = [_schema_allowed_values(properties[candidate]) for properties in property_maps]
        # A candidate whose values are the same on every variant separates none of them.
        if all(allowed) and len({tuple(map(repr, values or ())) for values in allowed}) > 1:
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
    ledger: SourceEvidenceLedger,
    *,
    name: str,
    result: Mapping[str, object],
    project_root: Path,
) -> None:
    """Retain exact source facts returned to the provider for later plans.

    Inspecting datasets again drops dataset evidence whose file no longer
    exists: the model has looked at the project since, so a renamed dataset
    stops binding its plans.
    """

    if name == "find_data":
        ledger.drop_vanished_schemas()
        schema = result.get("schema")
        raw_path = schema.get("path") if isinstance(schema, Mapping) else None
        raw_digest = schema.get("source_digest") if isinstance(schema, Mapping) else None
        if isinstance(raw_path, str) and isinstance(raw_digest, str):
            ledger.observe(
                ("schema", raw_path),
                ProjectSourceEvidence(
                    path=contained_path(project_root, raw_path),
                    digest=raw_digest,
                    kind="schema",
                ),
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
            ledger.observe(
                ("content", raw_source),
                ProjectSourceEvidence(
                    path=contained_path(project_root, raw_source),
                    digest=item["source_digest"],
                    kind="content",
                ),
            )


def _build_plan_error(exc: BuildPlanError) -> dict[str, object]:
    """A refused build-plan update or apply item, located and with its fix."""

    located: dict[str, object] = {} if exc.where is None else {"where": exc.where}
    return _error(exc.code, exc.message, **located, fix=exc.fix, **exc.fields)


def _added_node_ids(result: Mapping[str, object]) -> tuple[str, ...]:
    """The nodes a successful apply's change record lists as added."""

    if "error" in result:
        return ()
    change = result.get("change")
    changes = change.get("changes") if isinstance(change, Mapping) else None
    nodes = changes.get("nodes") if isinstance(changes, Mapping) else None
    if not isinstance(nodes, list):
        raise RuntimeError("a saved apply must carry its change record's nodes")
    return tuple(
        entry["id"]
        for entry in nodes
        if isinstance(entry, Mapping) and entry.get("change") == "added"
    )


def build_tool_executor(
    source_file: str,
    *,
    session_id: str = "legacy",
    evidence: SourceEvidenceLedger | None = None,
    plan: BuildPlan | None = None,
) -> Callable[[str, dict[str, Any]], Awaitable[Mapping[str, object]]]:
    """Build the loop's non-raising, source-bound async tool dispatcher for one turn.

    *evidence* is the session's evidence ledger; building the executor starts a
    turn on it, so what earlier turns observed is carried. *plan* is the
    session's build plan, which `update_build_plan` updates and an apply naming
    an `item` records its committed change against. An executor built without
    either keeps one of its own.
    """

    project_root = Path.cwd().resolve()
    ledger = SourceEvidenceLedger() if evidence is None else evidence
    build_plan = BuildPlan() if plan is None else plan
    ledger.begin_turn()
    # The nodes whose saved configuration this turn has seen: an inspect_node
    # config part returned to the model, or a node an apply of the turn added.
    # The executor lives for one turn, so earlier turns' reads never count.
    seen_config: set[str] = set()

    async def execute_tool(name: str, arguments: dict[str, Any]) -> Mapping[str, object]:
        started = time.monotonic()
        result = _with_retryable(await _dispatch_tool(name, arguments))
        _log_tool_outcome(name, result, elapsed_ms=(time.monotonic() - started) * 1000)
        return result

    async def _dispatch_tool(name: str, arguments: dict[str, Any]) -> Mapping[str, object]:
        if name in _REMOVED_TOOLS:
            return _error("tool_removed", _REMOVED_TOOLS[name], name=name)
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
            return _attributed_tool_result(
                name,
                _error("invalid_request", "The tool request must be a finite JSON object."),
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
            return _attributed_tool_result(
                name,
                _error(
                    "invalid_request",
                    str(exc),
                    validation_path=exc.path,
                    validation_reason=exc.reason,
                    **exc.fields,
                ),
            )
        if name == "dry_run_graph_edits":
            # Evidence an earlier turn observed binds this plan only while it
            # still holds; the model no longer sees the result it came from.
            try:
                released = await asyncio.to_thread(ledger.release_stale_carried, project_root)
            except Exception as exc:  # noqa: BLE001 - executor must not raise
                return _bounded_tool_result(
                    name, _error("operation_failed", _error_message(exc, operation=name))
                )
            for kind, path in released:
                logger.info("assistant_evidence_released", kind=kind, path=path)
            return _bounded_tool_result(
                name,
                await dry_run_graph_edits(
                    source_file,
                    arguments.get("ops"),
                    summary=arguments["summary"],
                    config_visibility=ConfigVisibility(
                        withheld=_part_requirement(policy, "config") is not None,
                        read=frozenset(seen_config),
                    ),
                    assumptions=arguments.get("assumptions", ()),
                    postconditions=arguments.get("postconditions", ()),
                    project_sources=ledger.sources(),
                ),
            )
        if name == "update_build_plan":
            try:
                updated = build_plan.update(
                    items=arguments.get("items"), complete=arguments.get("complete")
                )
            except BuildPlanError as exc:
                return _attributed_tool_result(name, _build_plan_error(exc))
            return _bounded_tool_result(name, {"items": build_plan_view(updated)})
        if name == "apply_graph_plan":
            item = arguments.get("item")
            if item is not None:
                try:
                    build_plan.require_item(item)
                except BuildPlanError as exc:
                    return _attributed_tool_result(name, _build_plan_error(exc))
            result = await apply_graph_plan(
                source_file,
                arguments.get("plan_hash", ""),
                session_id=session_id,
            )
            change = result.get("change")
            if item is not None and isinstance(change, Mapping):
                # The save committed, verified or not: its change counts toward the item.
                build_plan.record_change(item, str(change["id"]))
                result = {**result, "item": item}
            applied = _bounded_tool_result(name, result)
            seen_config.update(_added_node_ids(applied))
            return applied

        try:
            operation: Callable[[], dict[str, object]] | None = None
            worker_call: Callable[[], Awaitable[dict[str, object]]] | None = None
            if name == "get_pipeline":
                operation = partial(get_pipeline, source_file)
            elif name == "inspect_node":
                # Each part prepares on a thread; a profile collects in the
                # interactive preview worker.
                worker_call = partial(
                    inspect_node,
                    source_file,
                    arguments["node"],
                    arguments.get("parts", ("schema",)),
                    arguments.get("input"),
                    session_id=session_id,
                )
            elif name == "find_data":
                operation = partial(
                    find_data,
                    source_file,
                    arguments.get("directory"),
                    recursive=arguments.get("recursive", False),
                    path=arguments.get("path"),
                )
            elif name == "read_reference":
                operation = partial(read_reference, arguments["ids"])
            elif name == "get_project_knowledge":
                operation = partial(
                    get_project_knowledge,
                    source_file,
                    arguments["query"],
                    limit=arguments.get("limit", 5),
                )
            else:  # pragma: no cover - guarded by _TOOL_NAMES
                return _dispatch_error(name, f"Unknown assistant tool {name!r}.")
            call = (
                worker_call
                if worker_call is not None
                else partial(asyncio.to_thread, cast(Callable[[], dict[str, object]], operation))
            )
            if name in _SAVE_LOCK_READ_TOOLS:
                async with save_lock:
                    result = await call()
            else:
                result = await call()
            bounded = _bounded_tool_result(name, result)
            if "error" in bounded and bounded.get("error") != result.get("error"):
                return bounded
            result = dict(bounded)
            if "error" not in result:
                if name == "inspect_node" and "config" in result:
                    seen_config.add(arguments["node"])
                _observe_project_source_evidence(
                    ledger,
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
    "TurnContextError",
    "apply_graph_plan",
    "build_context_update",
    "build_tool_executor",
    "build_turn_context",
    "column_profiles",
    "context_update",
    "dataset_listing",
    "dataset_schema",
    "dry_run_graph_edits",
    "find_data",
    "get_pipeline",
    "get_project_knowledge",
    "inspect_node",
    "node_config",
    "node_schema",
    "read_reference",
    "render_pipeline_graph",
]
