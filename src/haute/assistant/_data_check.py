"""The assistant's data check: value-free findings about a plan's candidate graph.

After an eligible schema-tier dry-run, the check executes the lineage of the
nodes the plan changes in the plan's candidate graph, in a killable
interactive worker, and measures each checked node's input frames and output
ports: rows in and out, null counts of new and changed columns, per-rule
banding claims, rating misses and unused entries, Edge Join matches and
duplicated keys, and execution failures with their provenance. It returns
counts and shares only, never a row value or a configuration value, and its
findings are advisory or informational. The specification is "Data checks"
in ``specs/assistant/high-level.md`` and ``specs/assistant/low-level.md``.

This module owns the check whole: eligibility (on the server, reading only
configuration, file metadata and published-generation pointers), the job
that crosses into the worker, the worker half (input verification, the
binding, the measuring walk's queries, the records and findings), the closed
result shapes, the size reduction of the model-facing view, and the
freshness comparison a consumer uses to decide whether stored findings still
describe the graph it shows. Whether a check runs at all (the egress policy)
is the caller's decision.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from fractions import Fraction
from functools import partial
from pathlib import Path
from typing import Annotated, Any, Literal, cast

import polars as pl
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from haute._banding_config import normalise_banding_factors, normalise_banding_rules
from haute._cache import canonical_json, graph_fingerprint
from haute._edge_join import build_edge_join_kwargs
from haute._execution_admission import (
    ExecutionAdmissionError,
    IsolatedExecutionBudget,
    create_admitted_execution_context,
    create_isolated_execution_context,
    isolated_execution_budget,
)
from haute._execution_context import (
    ExecutionCancellationToken,
    ExecutionContext,
    ExecutionMemoryLimitExceededError,
    ExecutionProfile,
)
from haute._graph_walker import (
    AttributedFailure,
    CollectPolicy,
    MeasuredInput,
    NodeMeasurement,
    walk_graph,
)
from haute._hashing import content_hash_bytes
from haute._input_preparation import preparation_base_dir, snapshot_backed_inputs
from haute._input_providers import source_cache_identity, source_signature
from haute._interactive_workers import (
    InteractiveWorkerBusyError,
    InteractiveWorkerError,
    InteractiveWorkerPreemptedError,
    InteractiveWorkerRemoteError,
    InteractiveWorkerStoppedError,
    InteractiveWorkerTimeoutError,
    resolve_interactive_execution_mode,
    run_preemptible_in_interactive_worker,
)
from haute._json_shred._snapshots import (
    api_input_snapshot_source,
    api_input_source_signature,
    api_input_table_statuses,
)
from haute._json_shred._source_proof import freshness_record
from haute._logging import get_logger
from haute._mlflow_io import DiskCachedRunModel, disk_cached_run_model
from haute._path_resolution import runtime_project_root_scope
from haute._rating import (
    RatingTableMissError,
    apply_banding_factors,
    apply_rating_table_lookup,
    banding_factor_is_active,
    banding_rule_claim_expr,
    normalise_combined_outputs,
    rating_table_lookup,
    require_banding_type,
)
from haute._rating_step_config import normalise_rating_tables
from haute._source_cache import SourceCacheStore
from haute._topo import canonical_topological_order
from haute._types import SINK_ONLY_NODE_TYPES, GraphEdge, GraphNode, NodeType, PipelineGraph
from haute._worker_isolation import WorkerTerminalReason, resolve_worker_memory_enforcement
from haute.assistant._application import diff_seed_nodes
from haute.assistant._config import EgressPolicy
from haute.assistant._ops import SemanticDiff
from haute.assistant._tools import _MAX_PROFILE_ROWS, _json_size, execution_error_record
from haute.errors import MlflowConfigError, PreambleError
from haute.execution import (
    dataframe_graph_input_identity,
    mlflow_backend_signature,
    runtime_input_signed_paths,
    source_lineage_graph,
)
from haute.executor import _build_node_fn, _compile_preamble, _pipeline_dir
from haute.graph_utils import flatten_graph
from haute.routes._supersession import SupersededRequestError, SupersessionCoordinator
from haute.routes.input_cache import input_snapshot_read_status

logger = get_logger(component="assistant.data_check")

# ---------------------------------------------------------------------------
# Constants (fixed, never configurable)
# ---------------------------------------------------------------------------

DATA_CHECK_VERSION = 1
DATA_CHECK_DEADLINE_SECONDS = 30.0
DATA_CHECK_MAX_NODES = 8
DATA_CHECK_MAX_FINDINGS = 20
DATA_CHECK_MAX_PORTS = 10
DATA_CHECK_MAX_COLUMNS = 20
DATA_CHECK_MAX_FACTORS = 20
DATA_CHECK_MAX_TABLES = 20
DATA_CHECK_MAX_RULE_COUNTS = 100
DATA_CHECK_MAX_RULE_POSITIONS = 20
DATA_CHECK_DETAIL_BYTES = 32_000
DATA_CHECK_FINDINGS_BYTES = 16_000
# Thresholds are exact ratios: a share is compared, never its rounded form.
RATING_MISS_ADVISORY_SHARE = Fraction(1, 10)
MOSTLY_DEFAULT_SHARE = Fraction(1, 2)
MOSTLY_NULL_SHARE = Fraction(1, 2)
DATA_CHECK_OPERATION = "assistant_data_check"
DATA_CHECK_OMITTED_NOTE = (
    "The data check's result did not fit in this tool result; it is stored with the plan."
)
_ROW_BOUND = _MAX_PROFILE_ROWS
_SHARE_DIGITS = 4
_OPAQUE_LOAD_FILE_TYPES = frozenset({"pickle", "joblib", "catboost"})
# The node types whose builders bind the preamble, as a preview injects its failure.
_PREAMBLE_NODE_TYPES = frozenset({NodeType.POLARS, NodeType.LIVE_SWITCH})

NotRunReason = Literal[
    "worker_mode_unsupported",
    "not_schema_tier",
    "no_checkable_nodes",
    "admission_refused",
    "worker_busy",
    "deadline",
    "memory_limited",
    "superseded",
    "superseded_by_preview",
    "cancelled",
    "source_changed",
    "internal_error",
]
NotCheckedReason = Literal[
    "submodel",
    "sink_only",
    "artifact_in_lineage",
    "artifact_not_local",
    "input_not_prepared",
    "node_cap",
]
FindingsVisibility = Literal["current", "earlier_inputs", "other_scenario", "other_graph"]

# The kinds in the contract's table order, which orders findings of one node.
_FINDING_KINDS = (
    "execution_failed",
    "rows_emptied",
    "banding_all_default",
    "banding_mostly_default",
    "banding_rules_unclaimed",
    "rating_misses",
    "rating_entries_unused",
    "join_unmatched",
    "join_partial",
    "join_validation_failed",
    "join_fan_out",
    "column_all_null",
    "column_mostly_null",
)

# ---------------------------------------------------------------------------
# Closed result shapes
# ---------------------------------------------------------------------------


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class StepRef(_Closed):
    id: str
    number: int


class ErrorRecord(_Closed):
    error_class: Literal["authored_code", "configuration", "validation", "haute", "internal"] = (
        Field(alias="class")
    )
    type: str
    step: StepRef | None
    line: int | None
    columns: list[str]
    text: str | None
    withheld: str | None


class InputRows(_Closed):
    input: str
    rows: int
    truncated: bool


class ColumnNulls(_Closed):
    name: str
    kind: Literal["new", "changed"]
    nulls: int
    share: float | None


class PortRows(_Closed):
    port: str | None
    rows: int
    truncated: bool
    columns: list[ColumnNulls]
    columns_omitted: int


class BandingFactor(_Closed):
    factor: int
    output_column: str
    status: Literal["measured", "skipped"]
    rows: int | None
    rule_rows: list[int] | None
    claimed: int | None
    defaulted: int | None
    unclaimed_rules: int | None
    truncated: bool | None


class RatingTable(_Closed):
    table: int
    output_column: str
    status: Literal["measured", "skipped"]
    rows: int | None
    missed: int | None
    entries: int
    unused_entries: int | None
    truncated: bool | None


class SideKeys(_Closed):
    base: list[str]
    join: list[str]


class SideCounts(_Closed):
    base: int
    join: int


class JoinMeasure(_Closed):
    how: str
    join_validate: str | None = Field(alias="validate")
    keys: SideKeys
    base_rows: int
    join_rows: int
    matched_base_rows: int | None
    duplicate_key_tuples: SideCounts | None
    truncated: bool


class CheckedNode(_Closed):
    node: str
    status: Literal["checked"]
    inputs: list[InputRows]
    outputs: list[PortRows]
    ports_omitted: int
    banding: list[BandingFactor] | None
    factors_omitted: int
    rating: list[RatingTable] | None
    tables_omitted: int
    join: JoinMeasure | None


class FailedNode(_Closed):
    node: str
    status: Literal["failed"]
    inputs: list[InputRows]
    banding: list[BandingFactor] | None
    factors_omitted: int
    rating: list[RatingTable] | None
    tables_omitted: int
    join: JoinMeasure | None
    error: ErrorRecord


class UpstreamFailedNode(_Closed):
    node: str
    status: Literal["upstream_failed"]
    failed_node: str
    at_or_upstream: bool


class NotCheckedNode(_Closed):
    node: str
    status: Literal["not_checked"]
    reason: NotCheckedReason
    blocking_node: str | None
    remedy: str | None


class ReducedNode(_Closed):
    node: str
    status: Literal["checked", "failed"]
    detail_omitted: Literal[True]


NodeRecord = CheckedNode | FailedNode | UpstreamFailedNode | NotCheckedNode | ReducedNode


class _FindingBase(_Closed):
    node: str
    truncated: bool


class ExecutionFailedFinding(_FindingBase):
    kind: Literal["execution_failed"]
    severity: Literal["advisory"]
    error: ErrorRecord
    at_or_upstream: bool


class RowsEmptiedFinding(_FindingBase):
    kind: Literal["rows_emptied"]
    severity: Literal["advisory"]
    port: str | None
    input_rows: int


class BandingAllDefaultFinding(_FindingBase):
    kind: Literal["banding_all_default"]
    severity: Literal["advisory"]
    factor: int
    output_column: str
    rows: int


class BandingMostlyDefaultFinding(_FindingBase):
    kind: Literal["banding_mostly_default"]
    severity: Literal["informational"]
    factor: int
    output_column: str
    defaulted: int
    rows: int
    share: float


class BandingRulesUnclaimedFinding(_FindingBase):
    kind: Literal["banding_rules_unclaimed"]
    severity: Literal["informational"]
    factor: int
    output_column: str
    rules: list[int]
    rules_omitted: int


class RatingMissesFinding(_FindingBase):
    kind: Literal["rating_misses"]
    severity: Literal["advisory", "informational"]
    table: int
    output_column: str
    missed: int
    rows: int
    share: float


class RatingEntriesUnusedFinding(_FindingBase):
    kind: Literal["rating_entries_unused"]
    severity: Literal["informational"]
    table: int
    output_column: str
    unused_entries: int
    entries: int


class JoinUnmatchedFinding(_FindingBase):
    kind: Literal["join_unmatched"]
    severity: Literal["advisory"]
    base_rows: int
    join_rows: int


class JoinPartialFinding(_FindingBase):
    kind: Literal["join_partial"]
    severity: Literal["informational"]
    matched_base_rows: int
    base_rows: int
    share: float


class JoinValidationFailedFinding(_FindingBase):
    kind: Literal["join_validation_failed"]
    severity: Literal["advisory"]
    join_validate: str = Field(alias="validate")
    side: Literal["base", "join", "both"]
    duplicate_key_tuples: SideCounts


class JoinFanOutFinding(_FindingBase):
    kind: Literal["join_fan_out"]
    severity: Literal["advisory"]
    base_rows: int
    output_rows: int
    duplicate_key_tuples: SideCounts


class ColumnAllNullFinding(_FindingBase):
    kind: Literal["column_all_null"]
    severity: Literal["advisory"]
    port: str | None
    column: str
    rows: int


class ColumnMostlyNullFinding(_FindingBase):
    kind: Literal["column_mostly_null"]
    severity: Literal["informational"]
    port: str | None
    column: str
    nulls: int
    rows: int
    share: float


Finding = Annotated[
    ExecutionFailedFinding
    | RowsEmptiedFinding
    | BandingAllDefaultFinding
    | BandingMostlyDefaultFinding
    | BandingRulesUnclaimedFinding
    | RatingMissesFinding
    | RatingEntriesUnusedFinding
    | JoinUnmatchedFinding
    | JoinPartialFinding
    | JoinValidationFailedFinding
    | JoinFanOutFinding
    | ColumnAllNullFinding
    | ColumnMostlyNullFinding,
    Field(discriminator="kind"),
]


class CheckedCheck(_Closed):
    version: int
    outcome: Literal["checked"]
    scenario: str
    row_bound: int
    elapsed_ms: int
    nodes: list[NodeRecord]
    nodes_omitted: int
    findings: list[Finding]
    findings_omitted: int
    detail_truncated: bool


class NotRunCheck(_Closed):
    version: int
    outcome: Literal["not_run"]
    scenario: str
    reason: NotRunReason
    detail: str | None
    elapsed_ms: int
    nodes: list[NotCheckedNode]
    nodes_omitted: int


DataCheckView = Annotated[CheckedCheck | NotRunCheck, Field(discriminator="outcome")]
#: Validates a model-facing check object against the closed shapes.
DATA_CHECK_VIEW: TypeAdapter[CheckedCheck | NotRunCheck] = TypeAdapter(DataCheckView)


# ---------------------------------------------------------------------------
# Request, binding and stored result
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DataCheckRequest:
    """What a check needs from one stored dry-run plan.

    *candidate_graph* is the ``result_graph`` of the ``VerifiedPlan`` the
    dry-run stored, never the saved graph; *submitted* is the operations
    payload the model sent, whose values an error record may echo.
    """

    plan_hash: str
    candidate_graph: PipelineGraph
    diff: SemanticDiff
    verification_tier: str
    policy: EgressPolicy
    submitted: tuple[Mapping[str, object], ...] = ()


@dataclass(frozen=True, slots=True)
class DataCheckBinding:
    """What a check's findings describe: its plan, graph, scenario and inputs.

    ``source_generation`` is ``None`` for a check that never read its inputs.
    """

    plan_hash: str
    graph_digest: str
    scenario: str
    source_generation: str | None = None
    freshness_tokens: Mapping[str, object] = field(default_factory=dict)
    identity_components: Mapping[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "plan_hash": self.plan_hash,
            "graph_digest": self.graph_digest,
            "source_generation": self.source_generation,
            "freshness_tokens": dict(self.freshness_tokens),
            "identity_components": dict(self.identity_components),
            "scenario": self.scenario,
        }


@dataclass(frozen=True, slots=True)
class DataCheckResult:
    """The stored check: the model-facing object whole, never reduced, and its binding."""

    check: Mapping[str, Any]
    binding: DataCheckBinding

    @property
    def outcome(self) -> str:
        return str(self.check["outcome"])

    @property
    def reason(self) -> str | None:
        reason = self.check.get("reason")
        return None if reason is None else str(reason)

    def as_dict(self) -> dict[str, object]:
        """The stored form: the check's fields and its whole binding."""
        return {**deepcopy(dict(self.check)), "binding": self.binding.as_dict()}


# ---------------------------------------------------------------------------
# Running a check
# ---------------------------------------------------------------------------

_SUPERSESSION = SupersessionCoordinator()


def data_check_session_key(session_id: str) -> tuple[str, str]:
    """The session's supersession key, which is also its worker affinity key."""
    return (DATA_CHECK_OPERATION, session_id)


class _CheckStop:
    """Why a running check must stop: a newer check in its session, or its turn stopped.

    Supersession outranks a stopped turn when both apply.
    """

    def __init__(self, cancellation: ExecutionCancellationToken | None) -> None:
        self._cancellation = cancellation
        self._superseded = False
        self.token = ExecutionCancellationToken()
        if cancellation is not None:
            cancellation.on_cancel(self.token.cancel)

    def supersede(self) -> None:
        self._superseded = True
        self.token.cancel()

    def reason(self) -> WorkerTerminalReason | None:
        if self._superseded:
            return "superseded"
        if self._cancellation is not None and self._cancellation.cancelled:
            return "cancelled"
        return None


async def run_data_check(
    request: DataCheckRequest,
    *,
    session_id: str,
    cancellation: ExecutionCancellationToken | None = None,
) -> DataCheckResult:
    """Check *request*'s candidate graph and return the stored result; never raises.

    One check runs per session: a newer one stops this one (``superseded``),
    and cancelling *cancellation* (the turn stopped) stops it as
    ``cancelled``. The check never waits for a worker slot and runs within
    its own 30-second deadline. Every outcome, a defect in the check
    included, is a result: only a cancellation of the awaiting task itself
    propagates, after the worker has been stopped.
    """
    started = time.monotonic()
    graph = request.candidate_graph
    binding = DataCheckBinding(request.plan_hash, "", graph.active_source)
    try:
        flat = await asyncio.to_thread(flatten_graph, graph)
        digest = await asyncio.to_thread(graph_fingerprint, flat)
        binding = DataCheckBinding(request.plan_hash, digest, graph.active_source)
        return await _run_data_check(request, flat, binding, session_id, cancellation, started)
    except Exception as exc:
        logger.error(
            "assistant_data_check_failed",
            error_class=type(exc).__name__,
            error_message=str(exc),
            exc_info=True,
        )
        return _not_run(binding, "internal_error", started)


async def _run_data_check(
    request: DataCheckRequest,
    flat: PipelineGraph,
    binding: DataCheckBinding,
    session_id: str,
    cancellation: ExecutionCancellationToken | None,
    started: float,
) -> DataCheckResult:
    deadline = started + DATA_CHECK_DEADLINE_SECONDS
    if resolve_interactive_execution_mode() != "process":
        return _not_run(binding, "worker_mode_unsupported", started)
    if request.verification_tier != "schema":
        return _not_run(binding, "not_schema_tier", started)
    graph = request.candidate_graph
    changed = changed_nodes(graph, request.diff)
    # Configuration, file metadata and pointers only, but file reads all the same.
    exclusions = await asyncio.to_thread(server_exclusions, graph, flat, changed)
    candidates = tuple(node for node in changed if exclusions[node] is None)
    excluded = {
        node: _not_checked_record(node, exclusion)
        for node, exclusion in exclusions.items()
        if exclusion is not None
    }
    if not candidates:
        return _not_run(
            binding, "no_checkable_nodes", started, nodes=[excluded[node] for node in changed]
        )
    job = DataCheckJob(
        candidate_graph=graph,
        scenario=graph.active_source,
        candidates=candidates,
        submitted=request.submitted,
        policy=request.policy,
    )
    stop = _CheckStop(cancellation)
    key = data_check_session_key(session_id)
    try:
        outcome = await _SUPERSESSION.run_latest(
            key,
            partial(_admitted_check, job, stop, key, deadline),
            cancel_active=stop.supersede,
            superseded_message="A newer data check in this assistant session replaced this one.",
        )
    except SupersededRequestError:
        return _not_run(binding, "superseded", started)
    except ExecutionAdmissionError as exc:
        return _not_run(binding, "admission_refused", started, detail=exc.reason)
    except InteractiveWorkerError as exc:
        return _not_run(binding, _worker_failure_reason(exc), started)
    return _checked_result(binding, outcome, changed, excluded, started)


async def _admitted_check(
    job: DataCheckJob,
    stop: _CheckStop,
    key: tuple[str, str],
    deadline: float,
) -> WorkerOutcome:
    """Admit one preview execution, then run the job pre-emptibly in the worker."""
    reason = stop.reason()
    if reason is not None:
        raise InteractiveWorkerStoppedError(reason)
    context = create_admitted_execution_context(
        operation=DATA_CHECK_OPERATION,
        profile=ExecutionProfile.PREVIEW_EAGER,
        cancellation_token=stop.token,
    )
    try:
        budget = isolated_execution_budget(context)
        return await run_preemptible_in_interactive_worker(
            _run_data_check_job,
            job,
            budget,
            affinity_key=key,
            deadline=deadline,
            stop_reason=stop.reason,
            absolute_rss_limit_bytes=budget.process_rss_limit_bytes,
            memory_growth_limit_bytes=budget.memory_limit_bytes,
            require_memory_limit=resolve_worker_memory_enforcement() == "required",
        )
    finally:
        context.release_admission(preserve_primary_error=True)


_MEMORY_REMOTE_IDENTITIES = frozenset(
    {
        ("builtins", "MemoryError"),
        (ExecutionMemoryLimitExceededError.__module__, ExecutionMemoryLimitExceededError.__name__),
        (ExecutionAdmissionError.__module__, ExecutionAdmissionError.__name__),
    }
)


def _worker_failure_reason(exc: InteractiveWorkerError) -> NotRunReason:
    """The not-run reason of a worker outcome; anything unexpected is logged as internal."""
    if isinstance(exc, InteractiveWorkerBusyError):
        return "worker_busy"
    if isinstance(exc, InteractiveWorkerPreemptedError):
        return "superseded_by_preview"
    if isinstance(exc, InteractiveWorkerTimeoutError):
        return "deadline"
    if isinstance(exc, InteractiveWorkerStoppedError) and exc.terminal_reason in (
        "superseded",
        "cancelled",
    ):
        return "superseded" if exc.terminal_reason == "superseded" else "cancelled"
    if exc.terminal_reason == "memory_limited" or (
        isinstance(exc, InteractiveWorkerRemoteError)
        and (exc.remote_module, exc.remote_type) in _MEMORY_REMOTE_IDENTITIES
    ):
        return "memory_limited"
    logger.error(
        "assistant_data_check_failed",
        error_class=type(exc).__name__,
        error_message=str(exc),
        remote_traceback=getattr(exc, "remote_traceback", None),
    )
    return "internal_error"


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _not_run(
    binding: DataCheckBinding,
    reason: NotRunReason,
    started: float,
    *,
    detail: str | None = None,
    nodes: Sequence[Mapping[str, object]] = (),
) -> DataCheckResult:
    check = {
        "version": DATA_CHECK_VERSION,
        "outcome": "not_run",
        "scenario": binding.scenario,
        "reason": reason,
        "detail": detail,
        "elapsed_ms": _elapsed_ms(started),
        "nodes": [dict(node) for node in nodes],
        "nodes_omitted": 0,
    }
    DATA_CHECK_VIEW.validate_python(check)
    logger.info(
        "assistant_data_check",
        outcome="not_run",
        reason=reason,
        elapsed_ms=check["elapsed_ms"],
    )
    return DataCheckResult(check, binding)


def _checked_result(
    binding: DataCheckBinding,
    outcome: WorkerOutcome,
    changed: Sequence[str],
    excluded: Mapping[str, Mapping[str, object]],
    started: float,
) -> DataCheckResult:
    records = {**excluded, **outcome.records}
    nodes = [dict(records[node]) for node in changed]
    if outcome.binding is not None:
        binding = DataCheckBinding(
            binding.plan_hash,
            binding.graph_digest,
            binding.scenario,
            outcome.binding.source_generation,
            outcome.binding.freshness_tokens,
            outcome.binding.identity_components,
        )
    if outcome.kind == "no_checkable_nodes":
        return _not_run(binding, "no_checkable_nodes", started, nodes=nodes)
    if outcome.kind == "source_changed":
        return _not_run(binding, "source_changed", started)
    findings = [dict(finding) for finding in outcome.findings]
    check = {
        "version": DATA_CHECK_VERSION,
        "outcome": "checked",
        "scenario": binding.scenario,
        "row_bound": _ROW_BOUND,
        "elapsed_ms": _elapsed_ms(started),
        "nodes": nodes,
        "nodes_omitted": 0,
        "findings": findings,
        "findings_omitted": 0,
        "detail_truncated": False,
    }
    DATA_CHECK_VIEW.validate_python(check)
    logger.info(
        "assistant_data_check",
        outcome="checked",
        elapsed_ms=check["elapsed_ms"],
        nodes=len(nodes),
        findings=len(findings),
    )
    return DataCheckResult(check, binding)


# ---------------------------------------------------------------------------
# Eligibility (server side: configuration, file metadata, generation pointers)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Exclusion:
    reason: NotCheckedReason
    blocking_node: str | None = None
    remedy: str | None = None


def changed_nodes(graph: PipelineGraph, diff: SemanticDiff) -> list[str]:
    """The nodes a plan adds or writes, the new ids of renames and the targets of
    edge changes, in the graph's topological order (ties broken by node id).

    A preamble change widens nothing: a preamble-only plan changes no node.
    """
    seeds = diff_seed_nodes(graph, diff, preamble_widens=False)
    return [node for node in _canonical_order(graph) if node in seeds]


def _canonical_order(graph: PipelineGraph) -> list[str]:
    children: dict[str, list[str]] = {}
    for edge in graph.edges:
        children.setdefault(edge.source, []).append(edge.target)
    return canonical_topological_order((node.id for node in graph.nodes), children)


def _lineage(flat: PipelineGraph, node_id: str, scenario: str) -> PipelineGraph:
    """*node_id* and its ancestors as a preview of *scenario* runs them."""
    return source_lineage_graph(flat, node_id, source=scenario)


def server_exclusions(
    graph: PipelineGraph, flat: PipelineGraph, changed: Sequence[str]
) -> dict[str, _Exclusion | None]:
    """Each changed node's first exclusion the server can decide, or ``None``.

    The first four reasons and the pointer stage of ``input_not_prepared``;
    the worker decides freshness and verification, then the node cap.
    """
    scenario = graph.active_source
    exclusions: dict[str, _Exclusion | None] = {}
    with runtime_project_root_scope(graph.source_file):
        base_dir = preparation_base_dir(flat)
        for node_id in changed:
            node_type = graph.node_map[node_id].data.nodeType
            if node_type in (NodeType.SUBMODEL, NodeType.SUBMODEL_PORT):
                exclusions[node_id] = _Exclusion("submodel")
            elif node_type in SINK_ONLY_NODE_TYPES:
                exclusions[node_id] = _Exclusion("sink_only")
            else:
                lineage = _lineage(flat, node_id, scenario)
                exclusions[node_id] = _lineage_exclusion(lineage, base_dir)
    return exclusions


def _lineage_exclusion(lineage: PipelineGraph, base_dir: Path | None) -> _Exclusion | None:
    order = [lineage.node_map[node_id] for node_id in _canonical_order(lineage)]
    for node in order:
        if _opaque_load_file(node):
            return _Exclusion("artifact_in_lineage", node.id)
    for node in order:
        exclusion = _artifact_exclusion(node)
        if exclusion is not None:
            return exclusion
    for node_id, kind, config in snapshot_backed_inputs(
        [node.id for node in order], lineage.node_map
    ):
        if not _published(kind, config, base_dir):
            return _input_not_prepared(node_id)
    return None


def _opaque_load_file(node: GraphNode) -> bool:
    return (
        node.data.nodeType == NodeType.EXTERNAL_FILE
        and str(node.data.config.get("fileType") or "") in _OPAQUE_LOAD_FILE_TYPES
    )


def _artifact_exclusion(node: GraphNode) -> _Exclusion | None:
    """``artifact_not_local`` for a model or optimiser artifact a preview would fetch."""
    config = node.data.config
    if node.data.nodeType == NodeType.OPTIMISER_APPLY:
        if config.get("sourceType") == "file":
            return None
        return _Exclusion("artifact_not_local", node.id)
    if node.data.nodeType != NodeType.MODEL_SCORE or not config.get("sourceType"):
        # An unconfigured Model Score passes its input through and loads nothing.
        return None
    cached = _cached_run_model(config)
    if cached is None:
        return _Exclusion("artifact_not_local", node.id)
    if cached.present():
        return None
    return _Exclusion(
        "artifact_not_local",
        node.id,
        f"Preview the Model Scoring node {node.id!r} in the editor, which fills the local "
        "model cache, then dry-run again.",
    )


def _cached_run_model(config: Mapping[str, Any]) -> DiskCachedRunModel | None:
    """The disk-cache files a run-sourced, single-file model loads from, or ``None``.

    ``None`` for a registered model (resolved by the registry on every load),
    a run without an artifact path (discovered through the tracking server),
    a ``pyfunc`` directory, and a destination that does not resolve.
    """
    run_id = str(config.get("run_id") or "")
    artifact_path = str(config.get("artifact_path") or "")
    if config.get("sourceType") != "run" or not run_id or not artifact_path:
        return None
    try:
        return disk_cached_run_model(
            run_id=run_id,
            artifact_path=artifact_path,
            destination=str(config.get("mlflow_destination") or ""),
        )
    except (MlflowConfigError, ValueError):
        return None


def _published(kind: str, config: Mapping[str, Any], base_dir: Path | None) -> bool:
    """Whether every snapshot an input reads has a published generation (a pointer probe)."""
    if kind == "data_input":
        identity = source_cache_identity(config, base_dir=base_dir)
        return input_snapshot_read_status(identity).state == "ready"
    source = api_input_snapshot_source(config, str(config["path"]))
    return all(
        input_snapshot_read_status(table.identity, build_digest=source.group_digest).state
        == "ready"
        for table in source.tables
    )


def _input_not_prepared(node_id: str) -> _Exclusion:
    return _Exclusion(
        "input_not_prepared",
        node_id,
        f"Preview the input {node_id!r} in the editor first, which prepares it, then "
        "dry-run again.",
    )


def _not_checked_record(node_id: str, exclusion: _Exclusion) -> dict[str, object]:
    return {
        "node": node_id,
        "status": "not_checked",
        "reason": exclusion.reason,
        "blocking_node": exclusion.blocking_node,
        "remedy": exclusion.remedy,
    }


# ---------------------------------------------------------------------------
# The worker half
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DataCheckJob:
    """What crosses into the worker: the candidate graph and the nodes to check.

    *candidates* are the changed nodes no server-side reason excludes, in
    the changed nodes' order.
    """

    candidate_graph: PipelineGraph
    scenario: str
    candidates: tuple[str, ...]
    submitted: tuple[Mapping[str, object], ...]
    policy: EgressPolicy


@dataclass(frozen=True, slots=True)
class BindingRead:
    """One read of a lineage's inputs: their digest, tokens and identity components."""

    source_generation: str
    freshness_tokens: Mapping[str, object]
    identity_components: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class WorkerOutcome:
    """The value-free result the worker returns; no frame leaves it."""

    kind: Literal["checked", "no_checkable_nodes", "source_changed"]
    records: Mapping[str, Mapping[str, object]] = field(default_factory=dict)
    findings: tuple[Mapping[str, object], ...] = ()
    binding: BindingRead | None = None


def _run_data_check_job(job: DataCheckJob, budget: IsolatedExecutionBudget) -> WorkerOutcome:
    """The interactive worker's entry: measure under the budget the server admitted."""
    context = create_isolated_execution_context(budget)
    try:
        return measure_candidate(job, context)
    finally:
        context.release_admission(preserve_primary_error=True)


def measure_candidate(job: DataCheckJob, execution_context: ExecutionContext) -> WorkerOutcome:
    """The worker half of a check: verify, bind, measure, bind again, judge.

    Runs where every read that can hash an input runs: in the killable
    interactive worker, inside the check's deadline.
    """
    graph = job.candidate_graph
    with runtime_project_root_scope(graph.source_file):
        flat = flatten_graph(graph)
        lineages = {node: _lineage(flat, node, job.scenario) for node in job.candidates}
        unreadable = _unreadable_inputs(flat, lineages.values())
        records: dict[str, Mapping[str, object]] = {}
        checked: list[str] = []
        for node_id in job.candidates:
            blocking = next(
                (
                    input_id
                    for input_id in _canonical_order(lineages[node_id])
                    if input_id in unreadable
                ),
                None,
            )
            if blocking is not None:
                records[node_id] = _not_checked_record(node_id, _input_not_prepared(blocking))
            elif len(checked) >= DATA_CHECK_MAX_NODES:
                records[node_id] = _not_checked_record(node_id, _Exclusion("node_cap"))
            else:
                checked.append(node_id)
        if not checked:
            return WorkerOutcome("no_checkable_nodes", records)
        lineage = _union_lineage(flat, [lineages[node_id] for node_id in checked])
        start = read_binding(lineage, job.scenario)
        queries = _DataCheckQueries(_ROW_BOUND)
        walked = _measure(lineage, checked, job.scenario, queries, execution_context)
        end = read_binding(lineage, job.scenario)
        if end.source_generation != start.source_generation:
            return WorkerOutcome("source_changed", binding=end)
        judged = _Judgement(job, flat, queries, walked, checked)
        records.update(judged.records)
        return WorkerOutcome("checked", records, judged.findings(), end)


def _cache_store() -> SourceCacheStore:
    from haute._sandbox import _get_project_root

    return SourceCacheStore(_get_project_root().resolve())


def _unreadable_inputs(flat: PipelineGraph, lineages: Iterable[PipelineGraph]) -> set[str]:
    """The snapshot-backed inputs a check cannot read, verified and judged fresh here.

    Opening a generation verifies it (part sizes, footers, and a content hash
    of each part this process has not verified), and freshness hashes the
    source: both run in the worker, never on the server.
    """
    read = {node_id for lineage in lineages for node_id in lineage.node_map}
    order = [node_id for node_id in _canonical_order(flat) if node_id in read]
    base_dir = preparation_base_dir(flat)
    store = _cache_store()
    return {
        node_id
        for node_id, kind, config in snapshot_backed_inputs(order, flat.node_map)
        if not _readable(kind, config, store, base_dir)
    }


def _readable(
    kind: str, config: Mapping[str, Any], store: SourceCacheStore, base_dir: Path | None
) -> bool:
    if kind == "data_input":
        signature = source_signature(config, base_dir=base_dir)
        # A source that is gone leaves the published generation authoritative.
        status = store.status(
            source_cache_identity(config, base_dir=base_dir),
            source_signature=None if signature == "missing" else signature,
        )
        return status.state == "ready" and status.freshness != "stale"
    source = api_input_snapshot_source(config, str(config["path"]))
    statuses = api_input_table_statuses(
        source, store, source_signature=api_input_source_signature(source.data_path)
    )
    return all(
        status.state == "ready" and status.freshness != "stale" for _table, status in statuses
    )


def _union_lineage(flat: PipelineGraph, lineages: Sequence[PipelineGraph]) -> PipelineGraph:
    """One graph of every checked node's lineage: nothing downstream of them runs."""
    node_ids = {node_id for lineage in lineages for node_id in lineage.node_map}
    edges: dict[tuple[object, ...], GraphEdge] = {}
    for lineage in lineages:
        for edge in lineage.edges:
            edges.setdefault(
                (edge.id, edge.source, edge.target, edge.sourceHandle, edge.targetHandle), edge
            )
    return flat.model_copy(
        update={
            "nodes": [node for node in flat.nodes if node.id in node_ids],
            "edges": list(edges.values()),
        }
    )


def _measure(
    lineage: PipelineGraph,
    checked: Sequence[str],
    scenario: str,
    queries: _DataCheckQueries,
    execution_context: ExecutionContext,
) -> Any:
    """Walk the lineage under the measuring purpose: records, never frames."""
    build: Callable[..., Any] = _build_node_fn
    preamble_ns: dict[str, Any] | None = None
    try:
        preamble_ns = _compile_preamble(lineage.preamble or "", pipeline_dir=_pipeline_dir(lineage))
    except PreambleError as exc:
        # As a preview does: the nodes that bind the preamble fail with it.
        build = partial(_preamble_failing_build, exc)
    return walk_graph(
        lineage,
        build,
        policy=CollectPolicy.measuring(checked=checked, row_bound=_ROW_BOUND, queries=queries),
        preamble_ns=preamble_ns or None,
        source=scenario,
        execution_context=execution_context,
        prepare_inputs=False,
    )


def _preamble_failing_build(failure: PreambleError, node: GraphNode, **kwargs: Any) -> Any:
    if node.data.nodeType in _PREAMBLE_NODE_TYPES:
        # One failure per node, so each is attributed to its own node.
        raise PreambleError(str(failure), source_line=failure.source_line) from failure
    return _build_node_fn(node, **kwargs)


# ---------------------------------------------------------------------------
# The binding
# ---------------------------------------------------------------------------


def _cached_models(lineage: PipelineGraph) -> dict[str, DiskCachedRunModel]:
    models: dict[str, DiskCachedRunModel] = {}
    for node in lineage.nodes:
        if node.data.nodeType != NodeType.MODEL_SCORE:
            continue
        cached = _cached_run_model(node.data.config)
        if cached is not None:
            models[node.id] = cached
    return models


def identity_components(lineage: PipelineGraph) -> dict[str, Any]:
    """The parts of a lineage's input identity that are not file contents.

    The resolved path of every file the source generation signs, and each
    run-sourced Model Scoring node's resolved MLflow backend identity (whose
    digest also selects its disk model cache): configuration reads only.
    """
    paths = {str(path) for path in runtime_input_signed_paths(lineage)}
    for cached in _cached_models(lineage).values():
        paths.update(str(path.resolve()) for path in cached.files)
    backends = {
        node.id: mlflow_backend_signature(node.data.config)
        for node in sorted(lineage.nodes, key=lambda item: item.id)
        if node.data.nodeType == NodeType.MODEL_SCORE
        and node.data.config.get("sourceType") == "run"
    }
    return {"paths": sorted(paths), "backends": backends}


def _freshness_token(path: str) -> object:
    try:
        return freshness_record(Path(path))
    except OSError:
        return None


def read_binding(lineage: PipelineGraph, scenario: str) -> BindingRead:
    """Read a lineage's source generation, freshness tokens and identity components.

    The source generation digests the runtime input identity execution caches
    sign together with each local Model Scoring node's cached model (and an
    EBM's cached contract), which that identity does not sign. It hashes
    inputs, so it is read only in the worker.
    """
    components = identity_components(lineage)
    paths: list[str] = components["paths"]
    tokens = {path: _freshness_token(path) for path in paths}
    inputs = dataframe_graph_input_identity(lineage, target_node_id=None, source=scenario)
    models = {node_id: cached.fingerprint() for node_id, cached in _cached_models(lineage).items()}
    generation = content_hash_bytes(
        canonical_json({"inputs": inputs.digest, "models": models}).encode("utf-8")
    )
    return BindingRead(generation, tokens, components)


def data_check_visibility(result: DataCheckResult, graph: PipelineGraph) -> FindingsVisibility:
    """Whether stored findings describe *graph*, the graph a consumer shows.

    ``other_graph`` when the graph digest differs and ``other_scenario`` when
    only the checked scenario does: findings are hidden for either.
    ``earlier_inputs`` labels them when an identity component, the signed path
    set or a freshness token differs, or a signed file is missing. Never
    hashes a file: it re-derives the components from configuration and
    re-observes the tokens.
    """
    binding = result.binding
    flat = flatten_graph(graph)
    if graph_fingerprint(flat) != binding.graph_digest:
        return "other_graph"
    if graph.active_source != binding.scenario:
        return "other_scenario"
    measured = [
        str(record["node"])
        for record in result.check.get("nodes", ())
        if record.get("status") in ("checked", "failed", "upstream_failed")
    ]
    if not measured or binding.source_generation is None:
        return "current"
    with runtime_project_root_scope(graph.source_file):
        lineage = _union_lineage(
            flat, [_lineage(flat, node_id, binding.scenario) for node_id in measured]
        )
        components = identity_components(lineage)
        if components != dict(binding.identity_components):
            return "earlier_inputs"
        for path, token in binding.freshness_tokens.items():
            current = _freshness_token(path)
            if token is None or current is None or current != token:
                return "earlier_inputs"
    return "current"


# ---------------------------------------------------------------------------
# Measurement queries
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _FactorPlan:
    index: int
    output_column: str
    measured: bool
    rule_count: int = 0


@dataclass(slots=True)
class _TablePlan:
    index: int
    output_column: str
    measured: bool
    entries: int
    guarded: bool = False


@dataclass(slots=True)
class _PortPlan:
    port: str | None
    columns: list[tuple[str, Literal["new", "changed"]]]
    omitted: int


@dataclass(slots=True)
class _NodePlan:
    factors: list[_FactorPlan] = field(default_factory=list)
    factors_total: int = 0
    tables: list[_TablePlan] = field(default_factory=list)
    tables_total: int = 0
    join: dict[str, Any] | None = None
    ports: list[_PortPlan] = field(default_factory=list)
    ports_seen: int = 0
    refused: Exception | None = None
    """The failure the node's configuration gave its structured measurement."""


def _key_list(value: str | list[str]) -> list[str]:
    return [value] if isinstance(value, str) else list(value)


class _DataCheckQueries:
    """The one-row aggregates of each checked node, built from structured configuration.

    Banding, rating and join measurements read only the node's inputs; output
    ports are counted by rows and the nulls of new and changed columns. Each
    query's layout is remembered per node for reading its row back.
    """

    def __init__(self, row_bound: int) -> None:
        self.row_bound = row_bound
        self.plans: dict[str, _NodePlan] = {}

    def plan(self, node_id: str) -> _NodePlan:
        return self.plans.setdefault(node_id, _NodePlan())

    # -- inputs ---------------------------------------------------------

    def input_query(self, node: GraphNode, measured: MeasuredInput) -> pl.LazyFrame:
        counted = measured.frame.select(
            pl.len().alias("rows"), *_every_column(measured.frame.collect_schema())
        )
        try:
            parts = self._structured_parts(node, measured)
        except Exception as exc:
            # The node's configuration refuses its measurement, as execution
            # will refuse the node: the input is still counted, and the
            # failure is the node's own, raised by ``joint_query``.
            plan = self.plan(node.id)
            plan.refused = exc
            plan.factors, plan.tables, plan.join = [], [], None
            return counted
        return pl.concat([counted, *parts], how="horizontal", strict=True)

    def _structured_parts(self, node: GraphNode, measured: MeasuredInput) -> list[pl.LazyFrame]:
        node_type = node.data.nodeType
        if node_type == NodeType.BANDING and measured.position == 0:
            return self._banding_parts(node, measured.frame)
        if node_type == NodeType.RATING_STEP and measured.position == 0:
            return self._rating_parts(node, measured.frame)
        if node_type == NodeType.EDGE_JOIN:
            return self._join_side_parts(node, measured)
        return []

    def _banding_parts(self, node: GraphNode, frame: pl.LazyFrame) -> list[pl.LazyFrame]:
        plan = self.plan(node.id)
        factors = normalise_banding_factors(node.data.config)
        plan.factors_total = len(factors)
        parts: list[pl.LazyFrame] = []
        for index, factor in enumerate(factors[:DATA_CHECK_MAX_FACTORS]):
            mode = str(factor.get("banding", "") or "")
            require_banding_type(mode, subject=f"Banding factor {index}")
            output_column = str(factor.get("outputColumn") or "")
            if not banding_factor_is_active(factor):
                plan.factors.append(_FactorPlan(index, output_column, measured=False))
                continue
            claim = banding_rule_claim_expr(
                pl.col(factor["column"]),
                frame.collect_schema().get(factor["column"]),
                mode,
                factor["rules"],
                bool(factor.get("rightClosed", True)),
                output_column=output_column,
            )
            plan.factors.append(
                _FactorPlan(
                    index,
                    output_column,
                    measured=True,
                    rule_count=len(normalise_banding_rules(mode, factor["rules"])),
                )
            )
            parts.append(
                frame.select(claim.value_counts(name="count").implode().alias(f"banding_{index}"))
            )
            # The next factor reads this one's output, as execution applies them.
            frame = apply_banding_factors(frame, [factor])
        return parts

    def _rating_parts(self, node: GraphNode, frame: pl.LazyFrame) -> list[pl.LazyFrame]:
        plan = self.plan(node.id)
        tables = normalise_rating_tables(node.data.config)
        plan.tables_total = len(tables)
        schema: dict[str, Any] = dict(frame.collect_schema())
        parts: list[pl.LazyFrame] = []
        for index, table in enumerate(tables[:DATA_CHECK_MAX_TABLES]):
            output_column = str(table.get("outputColumn") or "")
            built = rating_table_lookup(table, schema)
            if built is None:
                entries = table.get("entries")
                plan.tables.append(
                    _TablePlan(
                        index,
                        output_column,
                        measured=False,
                        entries=len(entries) if isinstance(entries, list) else 0,
                    )
                )
                continue
            key_columns = list(built.key_columns)
            keys = frame.select(built.frame_key_exprs())
            missed = (
                keys.join(built.lookup.lazy(), on=key_columns, how="left")
                .select(pl.col(built.lookup_value_column).is_null().sum())
                .select(pl.all().alias(f"rating_{index}_missed"))
            )
            unused = (
                built.keyed_entries.lazy()
                .join(keys.unique(), on=key_columns, how="anti")
                .select(pl.len().alias(f"rating_{index}_unused"))
            )
            parts.extend((missed, unused))
            plan.tables.append(
                _TablePlan(
                    index,
                    output_column,
                    measured=True,
                    entries=built.keyed_entries.height,
                    guarded=built.default_value is None and built.on_missing == "error",
                )
            )
            # The next table reads this one's output; misses stay null here.
            frame = apply_rating_table_lookup(frame, built, frame_schema=schema, guard_misses=False)
            schema[built.output_column] = pl.Float64
        return parts

    def _join_keys(self, node: GraphNode) -> dict[str, Any]:
        plan = self.plan(node.id)
        if plan.join is None:
            kwargs = build_edge_join_kwargs(node.data.config)
            how = str(kwargs["how"])
            if how == "cross":
                base_keys: list[str] = []
                join_keys: list[str] = []
            elif "on" in kwargs:
                base_keys = join_keys = _key_list(kwargs["on"])
            else:
                base_keys = _key_list(kwargs["left_on"])
                join_keys = _key_list(kwargs["right_on"])
            plan.join = {
                "how": how,
                "validate": kwargs.get("validate"),
                "base": base_keys,
                "join": join_keys,
                "inputs": {},
            }
        return plan.join

    def _join_side_parts(self, node: GraphNode, measured: MeasuredInput) -> list[pl.LazyFrame]:
        join = self._join_keys(node)
        if measured.role not in ("base", "join"):
            return []
        join["inputs"][measured.role] = measured.name
        if join["how"] == "cross":
            return []
        keys = join[measured.role]
        # A key tuple with a null part never matches, so it is never a duplicate.
        complete = pl.all_horizontal([pl.col(key).is_not_null() for key in keys])
        tuples = pl.struct(keys).filter(complete)
        duplicated = tuples.filter(tuples.is_duplicated()).n_unique()
        return [measured.frame.select(duplicated.alias("duplicate_key_tuples"))]

    def joint_query(self, node: GraphNode, inputs: Sequence[MeasuredInput]) -> pl.LazyFrame | None:
        refused = self.plan(node.id).refused
        if refused is not None:
            raise refused
        if node.data.nodeType != NodeType.EDGE_JOIN:
            return None
        join = self._join_keys(node)
        roles = {item.role: item for item in inputs}
        if join["how"] == "cross" or set(roles) != {"base", "join"}:
            return None
        return (
            roles["base"]
            .frame.join(
                roles["join"].frame.select(join["join"]),
                left_on=join["base"],
                right_on=join["join"],
                how="semi",
            )
            .select(pl.len().alias("matched_base_rows"))
        )

    # -- outputs --------------------------------------------------------

    def output_query(
        self,
        node: GraphNode,
        port: str | None,
        frame: pl.LazyFrame,
        inputs: Sequence[MeasuredInput],
    ) -> pl.LazyFrame | None:
        plan = self.plan(node.id)
        plan.ports_seen += 1
        if plan.ports_seen > DATA_CHECK_MAX_PORTS:
            return None
        schema = frame.collect_schema()
        input_schemas = [item.frame.collect_schema() for item in inputs]
        written = _written_columns(node)
        columns: list[tuple[str, Literal["new", "changed"]]] = []
        for name, dtype in schema.items():
            present = [found[name] for found in input_schemas if name in found]
            if not present:
                columns.append((name, "new"))
            elif any(other != dtype for other in present) or name in written:
                columns.append((name, "changed"))
        measured = columns[:DATA_CHECK_MAX_COLUMNS]
        plan.ports.append(_PortPlan(port, measured, len(columns) - len(measured)))
        return frame.select(
            pl.len().alias("rows"),
            *_every_column(schema),
            *[
                pl.col(name).null_count().alias(f"nulls_{position}")
                for position, (name, _kind) in enumerate(measured)
            ],
        )


def _every_column(schema: pl.Schema) -> list[pl.Expr]:
    """An aggregate that reads every column, so the frame is computed as collecting it is.

    A row count alone lets Polars prune every column, and with them a cast or
    a callback that would raise when the frame is read.
    """
    if not schema.names():
        return []
    return [pl.sum_horizontal(pl.all().null_count()).alias("__every_column_nulls__")]


def _written_columns(node: GraphNode) -> frozenset[str]:
    """The columns a node's structured configuration writes (which are changed even when kept)."""
    config = node.data.config
    node_type = node.data.nodeType
    if node_type == NodeType.BANDING:
        return frozenset(
            str(factor["outputColumn"])
            for factor in normalise_banding_factors(config)
            if banding_factor_is_active(factor)
        )
    if node_type == NodeType.RATING_STEP:
        outputs = {
            str(table.get("outputColumn"))
            for table in normalise_rating_tables(config)
            if table.get("outputColumn")
        }
        outputs.update(str(item["outputColumn"]) for item in normalise_combined_outputs(config))
        return frozenset(outputs)
    steps = config.get("steps")
    if isinstance(steps, list):
        return frozenset(
            str(step["name"])
            for step in steps
            if isinstance(step, Mapping) and step.get("kind") == "with_column" and step.get("name")
        )
    return frozenset()


# ---------------------------------------------------------------------------
# Records and findings
# ---------------------------------------------------------------------------


def _count(row: Mapping[str, object], key: str) -> int:
    """One count from a measurement's aggregate row; anything else is a defect."""
    value = row[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"Measurement {key!r} is not a count: {type(value).__name__}")
    return value


def _share(count: int, denominator: int) -> float | None:
    return None if denominator == 0 else round(count / denominator, _SHARE_DIGITS)


class _Judgement:
    """Turns a measuring walk's aggregates into node records and findings."""

    def __init__(
        self,
        job: DataCheckJob,
        flat: PipelineGraph,
        queries: _DataCheckQueries,
        walked: Any,
        checked: Sequence[str],
    ) -> None:
        self.job = job
        self.flat = flat
        self.bound = queries.row_bound
        self.plans = queries.plans
        self.measurements: Mapping[str, NodeMeasurement] = walked.measurements
        self.attributed: Mapping[str, AttributedFailure] = walked.attributed_failures
        self.checked = list(checked)
        self.errors: dict[str, dict[str, Any]] = {}
        self.records: dict[str, Mapping[str, Any]] = {}
        self.details: dict[str, _NodeDetail] = {}
        for node_id in self.checked:
            measurement = self.measurements.get(node_id)
            if measurement is None:
                raise RuntimeError(f"The measuring walk did not visit checked node {node_id!r}.")
            self.records[node_id] = self._record(measurement)

    def _error(self, failure: AttributedFailure) -> dict[str, Any]:
        record = self.errors.get(failure.node_id)
        if record is None:
            record = execution_error_record(
                failure.error,
                graph=self.job.candidate_graph,
                node=failure.node_id,
                submitted=self.job.submitted,
                policy=self.job.policy,
            )
            self.errors[failure.node_id] = record
        return record

    def _record(self, measurement: NodeMeasurement) -> Mapping[str, Any]:
        node_id = measurement.node_id
        failure = measurement.failure
        if measurement.status == "upstream_failed":
            assert failure is not None
            return {
                "node": node_id,
                "status": "upstream_failed",
                "failed_node": failure.node_id,
                "at_or_upstream": failure.at_or_upstream,
            }
        detail = _NodeDetail.read(
            self.flat.node_map[node_id],
            measurement,
            self.plans.get(node_id, _NodePlan()),
            self.bound,
        )
        self.details[node_id] = detail
        record: dict[str, Any] = {"node": node_id, "status": "checked"}
        record["inputs"] = detail.inputs
        if measurement.status == "measured":
            record["outputs"] = detail.outputs
            record["ports_omitted"] = detail.ports_omitted
        else:
            record["status"] = "failed"
        record.update(
            {
                "banding": detail.banding,
                "factors_omitted": detail.factors_omitted,
                "rating": detail.rating,
                "tables_omitted": detail.tables_omitted,
                "join": detail.join,
            }
        )
        if measurement.status == "failed":
            assert failure is not None
            record["error"] = self._error(failure)
        return record

    # -- findings -------------------------------------------------------

    def findings(self) -> tuple[Mapping[str, Any], ...]:
        ranked: list[tuple[int, int, int, int, Mapping[str, Any]]] = []
        position = {node_id: index for index, node_id in enumerate(self.checked)}
        for node_id, detail in self.details.items():
            for sequence, finding in enumerate(self._node_findings(node_id, detail)):
                ranked.append(_ranked(finding, position[node_id], sequence))
        for root, failure in self.attributed.items():
            if self._replaced(root, failure):
                continue
            finding = {
                "kind": "execution_failed",
                "severity": "advisory",
                "node": root,
                "truncated": False,
                "error": self._error(failure),
                "at_or_upstream": failure.at_or_upstream,
            }
            ranked.append(_ranked(finding, self._failure_position(root, position), 0))
        ranked.sort(key=lambda item: item[:4])
        return tuple(finding for *_rank, finding in ranked)

    def _failure_position(self, root: str, position: Mapping[str, int]) -> int:
        """A failure ranks with the first checked node that reports it."""
        reporters = [
            position[node_id]
            for node_id, record in self.records.items()
            if node_id == root or record.get("failed_node") == root
        ]
        return min(reporters, default=len(position))

    def _replaced(self, root: str, failure: AttributedFailure) -> bool:
        """Whether a join's validation or a rating miss guard explains the failure instead."""
        detail = self.details.get(root)
        if detail is None or failure.node_id != root or failure.at_or_upstream:
            return False
        if detail.join is not None and detail.validation_side() is not None:
            return True
        return isinstance(failure.error, RatingTableMissError) and any(
            table["missed"] for table in detail.guarded_tables()
        )

    def _node_findings(self, node_id: str, detail: _NodeDetail) -> list[dict[str, Any]]:
        failed = self.records[node_id]["status"] == "failed"
        guard_raised = failed and self.records[node_id]["error"]["type"] == "RatingTableMissError"
        found: list[dict[str, Any]] = []
        found.extend(detail.rows_emptied())
        found.extend(detail.banding_findings())
        found.extend(detail.rating_findings(guard_raised=guard_raised))
        found.extend(detail.join_findings())
        found.extend(detail.column_findings())
        for finding in found:
            finding["node"] = node_id
        return found


def _ranked(
    finding: Mapping[str, Any], node_position: int, sequence: int
) -> tuple[int, int, int, int, Mapping[str, Any]]:
    return (
        0 if finding["severity"] == "advisory" else 1,
        node_position,
        _FINDING_KINDS.index(str(finding["kind"])),
        sequence,
        finding,
    )


@dataclass(slots=True)
class _NodeDetail:
    """One checked or failed node's measurements, as record parts and finding inputs."""

    node: GraphNode
    bound: int
    inputs: list[dict[str, Any]]
    outputs: list[dict[str, Any]]
    ports_omitted: int
    banding: list[dict[str, Any]] | None
    factors_omitted: int
    rating: list[dict[str, Any]] | None
    tables_omitted: int
    join: dict[str, Any] | None
    guarded: frozenset[int]
    unclaimed_positions: dict[int, list[int]]

    @classmethod
    def read(
        cls, node: GraphNode, measurement: NodeMeasurement, plan: _NodePlan, bound: int
    ) -> _NodeDetail:
        rows = [row for _name, row in measurement.inputs]
        inputs = [
            {"input": name, "rows": _count(row, "rows"), "truncated": _count(row, "rows") >= bound}
            for name, row in measurement.inputs
        ]
        outputs = [
            _port_record(port_plan, row, bound)
            for port_plan, (_port, row) in zip(plan.ports, measurement.outputs, strict=False)
        ]
        first = rows[0] if rows else None
        node_type = node.data.nodeType
        banding, unclaimed = (
            _banding_records(plan, first, bound) if node_type == NodeType.BANDING else (None, {})
        )
        rating = _rating_records(plan, first, bound) if node_type == NodeType.RATING_STEP else None
        return cls(
            node=node,
            bound=bound,
            inputs=inputs,
            outputs=outputs,
            ports_omitted=max(0, plan.ports_seen - DATA_CHECK_MAX_PORTS),
            banding=banding,
            factors_omitted=max(0, plan.factors_total - DATA_CHECK_MAX_FACTORS),
            rating=rating,
            tables_omitted=max(0, plan.tables_total - DATA_CHECK_MAX_TABLES),
            join=(
                _join_record(plan, measurement, bound) if node_type == NodeType.EDGE_JOIN else None
            ),
            guarded=frozenset(table.index for table in plan.tables if table.guarded),
            unclaimed_positions=unclaimed,
        )

    def _input_truncated(self) -> bool:
        return any(bool(item["truncated"]) for item in self.inputs)

    def rows_emptied(self) -> list[dict[str, Any]]:
        input_rows = max((int(item["rows"]) for item in self.inputs), default=0)
        if input_rows < 1:
            return []
        return [
            {
                "kind": "rows_emptied",
                "severity": "advisory",
                "truncated": self._input_truncated(),
                "port": port["port"],
                "input_rows": input_rows,
            }
            for port in self.outputs
            if port["rows"] == 0
        ]

    def banding_findings(self) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for factor in self.banding or ():
            rows, claimed = factor["rows"], factor["claimed"]
            if factor["status"] != "measured" or not rows:
                continue
            assert isinstance(rows, int) and isinstance(claimed, int)
            common = {
                "truncated": bool(factor["truncated"]),
                "factor": factor["factor"],
                "output_column": factor["output_column"],
            }
            if claimed == 0:
                found.append(
                    {"kind": "banding_all_default", "severity": "advisory", **common, "rows": rows}
                )
                continue
            defaulted = int(factor["defaulted"])
            if Fraction(defaulted, rows) >= MOSTLY_DEFAULT_SHARE:
                found.append(
                    {
                        "kind": "banding_mostly_default",
                        "severity": "informational",
                        **common,
                        "defaulted": defaulted,
                        "rows": rows,
                        "share": _share(defaulted, rows),
                    }
                )
            positions = self.unclaimed_positions.get(int(factor["factor"]), [])
            if positions:
                found.append(
                    {
                        "kind": "banding_rules_unclaimed",
                        "severity": "informational",
                        **common,
                        "rules": positions[:DATA_CHECK_MAX_RULE_POSITIONS],
                        "rules_omitted": max(0, len(positions) - DATA_CHECK_MAX_RULE_POSITIONS),
                    }
                )
        return found

    def guarded_tables(self) -> list[dict[str, Any]]:
        return [
            table
            for table in self.rating or ()
            if table["status"] == "measured" and table["table"] in self.guarded
        ]

    def rating_findings(self, *, guard_raised: bool) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for table in self.rating or ():
            if table["status"] != "measured":
                continue
            rows, missed = int(table["rows"]), int(table["missed"])
            common = {
                "truncated": bool(table["truncated"]),
                "table": table["table"],
                "output_column": table["output_column"],
            }
            if rows and missed:
                # A miss guard that raised makes its table's misses advisory whatever the share.
                forced = guard_raised and table["table"] in self.guarded
                advisory = forced or Fraction(missed, rows) >= RATING_MISS_ADVISORY_SHARE
                found.append(
                    {
                        "kind": "rating_misses",
                        "severity": "advisory" if advisory else "informational",
                        **common,
                        "missed": missed,
                        "rows": rows,
                        "share": _share(missed, rows),
                    }
                )
            unused = int(table["unused_entries"])
            if unused:
                found.append(
                    {
                        "kind": "rating_entries_unused",
                        "severity": "informational",
                        **common,
                        "unused_entries": unused,
                        "entries": table["entries"],
                    }
                )
        return found

    def validation_side(self) -> Literal["base", "join", "both"] | None:
        """The side whose duplicated keys break the join's declared ``validate``."""
        join = self.join
        if join is None or join["duplicate_key_tuples"] is None:
            return None
        duplicates: Mapping[str, int] = join["duplicate_key_tuples"]
        required = {"m:1": ("join",), "1:m": ("base",), "1:1": ("base", "join")}.get(
            str(join["validate"]), ()
        )
        broken = [side for side in required if duplicates[side] > 0]
        if not broken:
            return None
        return "both" if len(broken) == 2 else cast(Literal["base", "join"], broken[0])

    def join_findings(self) -> list[dict[str, Any]]:
        join = self.join
        if join is None:
            return []
        found: list[dict[str, Any]] = []
        truncated = bool(join["truncated"])
        base_rows, matched = int(join["base_rows"]), join["matched_base_rows"]
        if join["how"] in ("left", "inner", "semi") and isinstance(matched, int) and base_rows:
            if matched == 0:
                found.append(
                    {
                        "kind": "join_unmatched",
                        "severity": "advisory",
                        "truncated": truncated,
                        "base_rows": base_rows,
                        "join_rows": join["join_rows"],
                    }
                )
            elif matched < base_rows:
                found.append(
                    {
                        "kind": "join_partial",
                        "severity": "informational",
                        "truncated": truncated,
                        "matched_base_rows": matched,
                        "base_rows": base_rows,
                        "share": _share(matched, base_rows),
                    }
                )
        side = self.validation_side()
        if side is not None:
            found.append(
                {
                    "kind": "join_validation_failed",
                    "severity": "advisory",
                    "truncated": truncated,
                    "validate": join["validate"],
                    "side": side,
                    "duplicate_key_tuples": join["duplicate_key_tuples"],
                }
            )
        found.extend(self._fan_out(join, base_rows))
        return found

    def _fan_out(self, join: Mapping[str, Any], base_rows: int) -> list[dict[str, Any]]:
        duplicates = join["duplicate_key_tuples"]
        if (
            join["how"] not in ("left", "inner")
            or join["validate"] in ("1:m", "m:m")
            or duplicates is None
            or duplicates["join"] == 0
            or len(self.outputs) != 1
            or join["truncated"]
            or self.outputs[0]["truncated"]
        ):
            return []
        output_rows = int(self.outputs[0]["rows"])
        if output_rows <= base_rows:
            return []
        return [
            {
                "kind": "join_fan_out",
                "severity": "advisory",
                "truncated": False,
                "base_rows": base_rows,
                "output_rows": output_rows,
                "duplicate_key_tuples": duplicates,
            }
        ]

    def column_findings(self) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for port in self.outputs:
            rows = int(port["rows"])
            if rows < 1:
                continue
            for column in port["columns"]:
                nulls = int(column["nulls"])
                common = {
                    "truncated": bool(port["truncated"]),
                    "port": port["port"],
                    "column": column["name"],
                }
                if nulls == rows:
                    found.append(
                        {"kind": "column_all_null", "severity": "advisory", **common, "rows": rows}
                    )
                elif Fraction(nulls, rows) >= MOSTLY_NULL_SHARE:
                    found.append(
                        {
                            "kind": "column_mostly_null",
                            "severity": "informational",
                            **common,
                            "nulls": nulls,
                            "rows": rows,
                            "share": _share(nulls, rows),
                        }
                    )
        return found


def _port_record(plan: _PortPlan, row: Mapping[str, Any], bound: int) -> dict[str, Any]:
    rows = _count(row, "rows")
    return {
        "port": plan.port,
        "rows": rows,
        "truncated": rows >= bound,
        "columns": [
            {
                "name": name,
                "kind": kind,
                "nulls": _count(row, f"nulls_{position}"),
                "share": _share(_count(row, f"nulls_{position}"), rows),
            }
            for position, (name, kind) in enumerate(plan.columns)
        ],
        "columns_omitted": plan.omitted,
    }


def _banding_records(
    plan: _NodePlan, row: Mapping[str, Any] | None, bound: int
) -> tuple[list[dict[str, Any]], dict[int, list[int]]]:
    """Each planned factor's claims, and every rule position that claims no row."""
    if row is None:
        return [], {}
    rows = _count(row, "rows")
    records: list[dict[str, Any]] = []
    unclaimed_positions: dict[int, list[int]] = {}
    for factor in plan.factors:
        record: dict[str, Any] = {
            "factor": factor.index,
            "output_column": factor.output_column,
            "status": "measured" if factor.measured else "skipped",
            "rows": None,
            "rule_rows": None,
            "claimed": None,
            "defaulted": None,
            "unclaimed_rules": None,
            "truncated": None,
        }
        if factor.measured:
            counts = [0] * factor.rule_count
            defaulted = 0
            for entry in row[f"banding_{factor.index}"]:
                claim = entry["claim"]
                if claim is None:
                    defaulted = int(entry["count"])
                else:
                    counts[int(claim)] = int(entry["count"])
            unclaimed_positions[factor.index] = [
                position for position, count in enumerate(counts) if count == 0
            ]
            record.update(
                {
                    "rows": rows,
                    "rule_rows": counts
                    if factor.rule_count <= DATA_CHECK_MAX_RULE_COUNTS
                    else None,
                    "claimed": rows - defaulted,
                    "defaulted": defaulted,
                    "unclaimed_rules": len(unclaimed_positions[factor.index]),
                    "truncated": rows >= bound,
                }
            )
        records.append(record)
    return records, unclaimed_positions


def _rating_records(
    plan: _NodePlan, row: Mapping[str, Any] | None, bound: int
) -> list[dict[str, Any]]:
    if row is None:
        return []
    rows = _count(row, "rows")
    records: list[dict[str, Any]] = []
    for table in plan.tables:
        measured = table.measured
        records.append(
            {
                "table": table.index,
                "output_column": table.output_column,
                "status": "measured" if measured else "skipped",
                "rows": rows if measured else None,
                "missed": _count(row, f"rating_{table.index}_missed") if measured else None,
                "entries": table.entries,
                "unused_entries": _count(row, f"rating_{table.index}_unused") if measured else None,
                "truncated": rows >= bound if measured else None,
            }
        )
    return records


def _join_record(
    plan: _NodePlan, measurement: NodeMeasurement, bound: int
) -> dict[str, Any] | None:
    join = plan.join
    if join is None:
        return None
    measured = dict(measurement.inputs)
    base, other = (measured.get(join["inputs"].get(role, "")) for role in ("base", "join"))
    if base is None or other is None:
        return None
    base_rows, join_rows = _count(base, "rows"), _count(other, "rows")
    cross = join["how"] == "cross"
    joint = measurement.joint
    return {
        "how": join["how"],
        "validate": join["validate"],
        "keys": {"base": list(join["base"]), "join": list(join["join"])},
        "base_rows": base_rows,
        "join_rows": join_rows,
        "matched_base_rows": (
            None if cross or joint is None else _count(joint, "matched_base_rows")
        ),
        "duplicate_key_tuples": (
            None
            if cross
            else {
                "base": _count(base, "duplicate_key_tuples"),
                "join": _count(other, "duplicate_key_tuples"),
            }
        ),
        "truncated": base_rows >= bound or join_rows >= bound,
    }


# ---------------------------------------------------------------------------
# The model-facing view and its size
# ---------------------------------------------------------------------------


def _key_bytes(key: str) -> int:
    """What adding ``"key":`` and its separator to a non-empty object costs."""
    return _json_size(key) + 2


def fit_data_check(result: DataCheckResult, room_bytes: int) -> dict[str, Any]:
    """The check as it may reach the model in a tool result with *room_bytes* to spare.

    *room_bytes* is the tool-result limit less the size of the fully
    attributed dry-run result without the check. Returns ``{"data_check":
    <view>}`` reduced to its allocation, ``{"data_check_omitted": <note>}``
    when even the empty view does not fit, or ``{}`` when not even the note
    does. The stored check is never reduced: this works on a copy.
    """
    allocation = min(DATA_CHECK_DETAIL_BYTES, room_bytes - _key_bytes("data_check"))
    view: dict[str, Any] = deepcopy(dict(result.check))
    checked = view["outcome"] == "checked"
    if checked:
        _cap_findings(view)
    while _json_size(view) > allocation and _reduce_last_detail(view):
        view["detail_truncated"] = True
    while _json_size(view) > allocation and view["nodes"]:
        view["nodes"].pop()
        view["nodes_omitted"] += 1
        if checked:
            view["detail_truncated"] = True
    while checked and _json_size(view) > allocation and view["findings"]:
        view["findings"].pop()
        view["findings_omitted"] += 1
    if _json_size(view) <= allocation:
        DATA_CHECK_VIEW.validate_python(view)
        return {"data_check": view}
    if _json_size(DATA_CHECK_OMITTED_NOTE) + _key_bytes("data_check_omitted") <= room_bytes:
        return {"data_check_omitted": DATA_CHECK_OMITTED_NOTE}
    return {}


def _cap_findings(view: dict[str, Any]) -> None:
    findings: list[Any] = view["findings"]
    while len(findings) > DATA_CHECK_MAX_FINDINGS or (
        findings and _json_size(findings) > DATA_CHECK_FINDINGS_BYTES
    ):
        findings.pop()
        view["findings_omitted"] += 1


def _reduce_last_detail(view: dict[str, Any]) -> bool:
    """Reduce the last node record still holding detail; ``False`` when none does."""
    nodes: list[dict[str, Any]] = view["nodes"]
    for index in range(len(nodes) - 1, -1, -1):
        record = nodes[index]
        if record["status"] in ("checked", "failed") and "detail_omitted" not in record:
            nodes[index] = {
                "node": record["node"],
                "status": record["status"],
                "detail_omitted": True,
            }
            return True
    return False
