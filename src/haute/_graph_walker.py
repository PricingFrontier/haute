"""The graph walker: every execution builds its frames in one walk.

A walk prepares the graph once (``_prepare_execution``), plans the column
demand it will read, builds each node's function, then visits the nodes in
topological order. At each node it routes the parents' frames along the
node's incoming edges, invokes the node through the shared
``NodeBoundaryRunner``, applies the node's own column shaping and contract
checks and, under a seed plan, captures the node into the shared snapshot
store. What differs between executions is the ``CollectPolicy`` the caller
passes: its purpose, which nodes it collects, at which row limits, and
whether a node's failure is recorded against the node or raised.

No function in this module may exceed a cyclomatic complexity of 15; ruff's
C901 rule holds that, scoped to this module (``pyproject.toml``).
"""

from __future__ import annotations

import contextlib
import gc
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import polars as pl

import haute.execution as execution_facade
import haute.projection as projection_planner
from haute._builders import _passthrough_fn
from haute._chunked_writes import (
    JoinRecipe,
    RecipeEquivalenceError,
    WriteRecipe,
    check_recipe_equivalence,
)
from haute._execute_lazy import (
    _GC_BATCH_INTERVAL,
    NodeBoundary,
    NodeBoundaryRunner,
    PreparedExecution,
    PreparedExecutionRequest,
    _apply_column_renames,
    _apply_selected_columns,
    _build_funcs,
    _check_snapshot_plan,
    _conservative_strategy_passthrough,
    _declared_api_input_frame_schema_items,
    _edge_join_recipe,
    _extract_error_line,
    _is_plain_model_score,
    _located_step_line,
    _pick_source_frame,
    _PlannedCaptures,
    _prepare_execution,
    _replanned_target_preview_strategy,
    _runtime_lineage_demands,
    _runtime_projectable_source_ids,
    _schema_pairs,
    _shapes_output,
    _write_recipe,
)
from haute._execution_context import (
    ExecutionCancelledError,
    ExecutionContext,
    ExecutionMemoryLimitExceededError,
    ExecutionProfile,
)
from haute._graph_utils import edge_input_name, resolve_orig_source_names
from haute._input_preparation import preparation_base_dir, prepare_input_snapshots
from haute._logging import get_logger
from haute._path_resolution import runtime_project_root_scoped
from haute._polars_selectors import preamble_selector_aliases
from haute._polars_utils import _malloc_trim, projected_or_carrier_columns, streaming_collect
from haute._source_cache import SourceCacheError
from haute._step_progress import StepProgress
from haute._types import GraphEdge, GraphNode, NodeType, PipelineGraph, _Frame
from haute.errors import ContractMismatchError, SchemaMismatchError, is_public_contract_error

if TYPE_CHECKING:
    from haute._node_snapshots import NodeSnapshotArtifact
    from haute._seed_plans import SeedPlan, SeedPlanDecision

logger = get_logger(component="execute")

_RequiredColumns = Mapping[str, Iterable[str] | projection_planner.AllExceptColumns]
_SchemaItems = list[tuple[str, str]]
_Collected = pl.DataFrame | dict[str, pl.DataFrame] | None


class WalkPurpose(StrEnum):
    """Why a walk runs, which fixes how it plans and shapes its frames."""

    SINK = "sink"
    """Hand each node's lazy frame to a sink: project every edge to its planned
    demand, plan the execution strategy, prepare inputs, build write recipes."""
    DISPLAY = "display"
    """Show frames to a person (preview and trace): plan demand only from the
    caller, report every node's full schema, collect nodes under the policy's
    limits, and keep every node's plan for the caller."""
    CHUNK = "chunk"
    """Walk a proven chunk suffix one chunk at a time (the chunked runner): the
    chunk plan fixes each node's output demand, the chunk is its start node's
    frame, and nothing is planned, contract-checked or captured."""


@dataclass(frozen=True, slots=True)
class CollectPolicy:
    """What a walk collects, at which row limits, and whether node failures are recorded."""

    purpose: WalkPurpose = WalkPurpose.SINK
    collect: frozenset[str] | None = frozenset()
    """The nodes collected into DataFrames; ``None`` collects every node the walk builds."""
    row_limit: int | None = None
    """Each collection's row limit (SQL ``LIMIT``), and the builders' interactive signal."""
    row_limits_by_node: Mapping[str, int] = field(default_factory=dict)
    column_limits_by_node: Mapping[str, int] = field(default_factory=dict)
    """A cap on the columns a collection keeps; the node's full schema is still reported."""
    record_failures: bool = False
    """Record a node's failure against the node and carry on, rather than raise."""

    def __post_init__(self) -> None:
        for limits in (self.row_limits_by_node, self.column_limits_by_node):
            for node_id, limit in limits.items():
                if not isinstance(node_id, str) or not node_id:
                    raise ValueError("collection limit keys must be node ids")
                if type(limit) is not int or limit < 1:
                    raise ValueError("collection limits must be positive integers")

    @classmethod
    def sink(cls) -> CollectPolicy:
        """Collect nothing: the caller sinks or collects the lazy frames itself."""
        return cls(purpose=WalkPurpose.SINK)

    @classmethod
    def chunk(cls) -> CollectPolicy:
        """Collect nothing: the chunked runner collects each chunk's target itself."""
        return cls(purpose=WalkPurpose.CHUNK)

    @classmethod
    def display(
        cls,
        *,
        collect: Iterable[str] | None = None,
        row_limit: int | None = None,
        row_limits_by_node: Mapping[str, int] | None = None,
        column_limits_by_node: Mapping[str, int] | None = None,
        record_failures: bool = False,
    ) -> CollectPolicy:
        """Collect *collect* (every node the walk builds when ``None``) under these limits."""
        return cls(
            purpose=WalkPurpose.DISPLAY,
            collect=None if collect is None else frozenset(collect),
            row_limit=row_limit,
            row_limits_by_node=dict(row_limits_by_node or {}),
            column_limits_by_node=dict(column_limits_by_node or {}),
            record_failures=record_failures,
        )

    def collects(self, node_id: str) -> bool:
        return self.collect is None or node_id in self.collect

    def row_limit_for(self, node_id: str) -> int | None:
        return self.row_limits_by_node.get(node_id, self.row_limit or None)


@dataclass(frozen=True, slots=True)
class WalkRequest:
    """The graph and execution-independent inputs of one walk."""

    graph: PipelineGraph
    build_node_fn: Callable[..., Any]
    target_node_id: str | None = None
    preamble_ns: dict[str, Any] | None = None
    source: str = "live"
    enforce_contracts: bool = True
    required_columns_by_node: _RequiredColumns | None = None
    execution_context: ExecutionContext | None = None
    snapshot_plan: SeedPlan | None = None
    source_by_node: Mapping[str, str] = field(default_factory=dict)
    """Per-node builder source; graph pruning still uses ``source``."""
    preserve_node_ids: frozenset[str] = frozenset()
    """Non-source outputs kept after their last consumer ran."""
    schema_only: bool = False
    """The caller reads ``collect_schema()`` and never collects or sinks."""
    runtime_source_frames_by_node: Mapping[str, pl.DataFrame] | None = None
    prepare_inputs: bool = True
    """A sink walk prepares snapshot-backed inputs; a display walk's caller does."""
    walk_node_ids: frozenset[str] | None = None
    """Restrict the walk to these nodes (a chunk walk's suffix below its start node)."""
    output_demand: Mapping[str, frozenset[str] | None] = field(default_factory=dict)
    """A chunk walk's per-node output demand, fixed by its chunk plan."""
    reuse_loaded_model_by_node: Mapping[str, bool] | None = None
    """Model Score nodes whose scorer keeps its loaded model across builds."""


@dataclass(frozen=True, slots=True)
class WalkResult:
    """Everything one walk produced."""

    frames: dict[str, _Frame]
    """Each built node's frame as its consumers read it (a frame bundle for a
    multi-frame source). A sink walk drops a frame once its consumers ran; a
    display walk keeps every node's uncapped plan."""
    order: list[str] = field(default_factory=list)
    """The prepared lineage order."""
    run_order: list[str] = field(default_factory=list)
    """What the walk visited: the order, restricted under a plan to its seeds and executed nodes."""
    parents_of: dict[str, list[str]] = field(default_factory=dict)
    node_map: dict[str, GraphNode] = field(default_factory=dict)
    id_to_name: dict[str, str] = field(default_factory=dict)
    collected: dict[str, _Collected] = field(default_factory=dict)
    """Each collected node's DataFrame (per-frame for a bundle); ``None`` for a failed node."""
    errors: dict[str, str] = field(default_factory=dict)
    error_lines: dict[str, int] = field(default_factory=dict)
    timings: dict[str, float] = field(default_factory=dict)
    memory_bytes: dict[str, int] = field(default_factory=dict)
    available_columns: dict[str, _SchemaItems] = field(default_factory=dict)
    """A display walk's node schemas before the node's own selection and renames."""
    output_columns: dict[str, _SchemaItems] = field(default_factory=dict)
    frame_columns: dict[tuple[str, str], _SchemaItems] = field(default_factory=dict)
    """Per-frame schemas of a multi-frame producer, collected or not."""
    join_recipes: dict[str, JoinRecipe] = field(default_factory=dict)
    write_recipes: dict[str, WriteRecipe] = field(default_factory=dict)
    unshaped_frames: dict[str, pl.LazyFrame] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _WalkProjection:
    """The column demand a walk plans before building anything."""

    needed_by_node: Mapping[str, frozenset[str] | None]
    edge_demands: Mapping[projection_planner.ProjectionEdgeKey, frozenset[str] | None]
    projects_edges: bool
    """A sink walk narrows every edge to its planned demand; a display walk
    narrows an edge only to a demand its runtime schema proves."""
    runtime_plan: projection_planner.ProjectionPlan | None
    """Demand of the run itself, narrower than the plan's when a seed plan
    negotiated a broader capture demand."""
    collect_needed: Mapping[str, frozenset[str] | None]
    """The caller's own demand, which a display walk collects."""
    builder_needed: Mapping[str, frozenset[str] | None]
    api_port_columns: Mapping[str, Mapping[str, frozenset[str] | None]]
    strategy_required: Mapping[str, set[str] | projection_planner.AllExceptColumns]
    boundary_operators: Mapping[str, str]


@dataclass(slots=True)
class _NodeFrame:
    """A node's frame before any capture, with what the walk learnt building it."""

    frame: Any
    """A lazy frame, a display walk's collected DataFrame, or a multi-frame bundle."""
    boundary: NodeBoundary
    seeded: bool = False
    pass_through: bool = False
    output_names: list[str] | None = None
    """A display walk's output column names, after the node's own shaping."""
    scored: NodeSnapshotArtifact | None = None
    scored_write: tuple[bool, Mapping[str, str]] = (False, {})


class _UpstreamFailedError(Exception):
    """A node's input failed, and the walk recorded that against the node."""


@runtime_project_root_scoped
def walk_graph(
    graph: PipelineGraph,
    build_node_fn: Callable[..., Any],
    *,
    policy: CollectPolicy,
    target_node_id: str | None = None,
    preamble_ns: dict[str, Any] | None = None,
    source: str = "live",
    enforce_contracts: bool = True,
    required_columns_by_node: _RequiredColumns | None = None,
    execution_context: ExecutionContext | None = None,
    snapshot_plan: SeedPlan | None = None,
    source_by_node: Mapping[str, str] | None = None,
    preserve_node_ids: Iterable[str] = (),
    schema_only: bool = False,
    runtime_source_frames_by_node: Mapping[str, pl.DataFrame] | None = None,
    prepare_inputs: bool = True,
) -> WalkResult:
    """Walk *graph* once under *policy* and return what it built.

    ``target_node_id`` restricts the walk to that node's lineage. Under a
    leased ``snapshot_plan`` the walk runs exactly that plan: seeds read their
    generations, nothing above a seed is built, a pass-through node is its
    selected input, and every capture point is written to the shared store
    before anything below it reads it.
    """
    request = WalkRequest(
        graph=graph,
        build_node_fn=build_node_fn,
        target_node_id=target_node_id,
        preamble_ns=preamble_ns,
        source=source,
        enforce_contracts=enforce_contracts,
        required_columns_by_node=required_columns_by_node,
        execution_context=execution_context,
        snapshot_plan=snapshot_plan,
        source_by_node=dict(source_by_node or {}),
        preserve_node_ids=frozenset(preserve_node_ids),
        schema_only=schema_only,
        runtime_source_frames_by_node=runtime_source_frames_by_node,
        prepare_inputs=prepare_inputs,
    )
    return _Walk(request, policy).run()


@runtime_project_root_scoped
def prepare_walk(
    graph: PipelineGraph,
    build_node_fn: Callable[..., Any],
    *,
    policy: CollectPolicy,
    target_node_id: str,
    walk_node_ids: Iterable[str],
    output_demand: Mapping[str, frozenset[str] | None],
    preamble_ns: dict[str, Any] | None = None,
    source: str = "live",
    source_by_node: Mapping[str, str] | None = None,
    reuse_loaded_model_by_node: Mapping[str, bool] | None = None,
    execution_context: ExecutionContext | None = None,
) -> PreparedWalk:
    """Prepare a chunk walk once: its graph, demand and node functions.

    ``PreparedWalk.run`` then walks the nodes in *walk_node_ids*, starting
    from the frames it is given for the nodes above them.
    """
    request = WalkRequest(
        graph=graph,
        build_node_fn=build_node_fn,
        target_node_id=target_node_id,
        preamble_ns=preamble_ns,
        source=source,
        enforce_contracts=False,
        execution_context=execution_context,
        source_by_node=dict(source_by_node or {}),
        prepare_inputs=False,
        walk_node_ids=frozenset(walk_node_ids),
        output_demand=dict(output_demand),
        reuse_loaded_model_by_node=reuse_loaded_model_by_node,
    )
    walk = _Walk(request, policy)
    walk.prepare()
    return PreparedWalk(walk)


class PreparedWalk:
    """A walk whose node functions were built once, walked once per chunk."""

    def __init__(self, walk: _Walk) -> None:
        self._walk = walk

    def run(self, start_frames: Mapping[str, pl.LazyFrame]) -> WalkResult:
        """Walk the prepared nodes from *start_frames*, the frames of the nodes above them."""
        return self._walk.walk(start_frames)


class _Walk:
    """One walk's state; each method is one step of the walk."""

    def __init__(self, request: WalkRequest, policy: CollectPolicy) -> None:
        self.request = request
        self.policy = policy
        self.display = policy.purpose is WalkPurpose.DISPLAY
        self.chunk = policy.purpose is WalkPurpose.CHUNK
        self.context = request.execution_context
        self.plan = request.snapshot_plan
        self.decision: SeedPlanDecision | None = (
            self.plan.decision if self.plan is not None else None
        )
        self.prepared: PreparedExecution = _prepare_execution(
            PreparedExecutionRequest(
                graph=request.graph,
                target_node_id=request.target_node_id,
                source=request.source,
                required_columns_by_node=request.required_columns_by_node,
                profile=self.context.profile if self.context is not None else None,
            )
        )
        self.graph = self.prepared.graph
        self.graph_plan = self.prepared.graph_plan
        self.node_map = self.graph_plan.node_map
        self.order = self.graph_plan.order
        self.parents_of = self.graph_plan.parents_of
        self.selector_aliases = preamble_selector_aliases(self.graph.preamble or "")
        self.original_node_map = dict(self.graph.node_map)
        self.all_edges_by_target = {
            target: list(edges)
            for target, edges in self.prepared.all_incoming_edges_by_target.items()
        }
        self.profile = self._default_profile()
        self.run_order = self._run_order()
        self.preserved = request.preserve_node_ids | frozenset(
            self.decision.consumed_node_ids if self.decision is not None else ()
        )
        self.strategy: projection_planner.ExecutionStrategyResult | None = None
        self.seed_frames: dict[str, _Frame] = {}
        # A node's output columns in schema order, resolved once per chunk walk.
        self.output_order: dict[str, list[str]] = {}

    def _default_profile(self) -> ExecutionProfile:
        if self.context is not None:
            return self.context.profile
        return ExecutionProfile.PREVIEW_EAGER if self.display else ExecutionProfile.LAZY_SINK

    def _init_walk_state(self) -> None:
        # Each node's frame as its consumers read it (lazy, collected, or a bundle).
        self.frames: dict[str, Any] = {}
        self.prebuilt: dict[str, _NodeFrame | Exception] = {}
        self.column_cache: dict[tuple[str, str | None], frozenset[str]] = {}
        self.file_backed: set[str] = set(self.seed_frames)
        self.remaining = dict(self.prepared.children_count)
        self.join_recipes: dict[str, JoinRecipe] = {}
        self.write_recipes: dict[str, WriteRecipe] = {}
        self.unshaped_frames: dict[str, pl.LazyFrame] = {}
        self.materialisations_since_gc = 0
        self.store_failures: list[BaseException] = []
        # What a display walk reports.
        self.failed: set[str] = set()
        self.collected: dict[str, _Collected] = {}
        self.errors: dict[str, str] = {}
        self.error_lines: dict[str, int] = {}
        # The input frames of a node that failed while built, kept until its
        # failure is recorded so a stepped transform can name its failing step.
        self.failed_inputs: dict[str, list[_Frame]] = {}
        self.timings: dict[str, float] = {}
        self.memory_bytes: dict[str, int] = {}
        self.available_columns: dict[str, _SchemaItems] = {}
        self.output_columns: dict[str, _SchemaItems] = {}
        self.frame_columns: dict[tuple[str, str], _SchemaItems] = {}
        self.shared_inputs: dict[tuple[str, str | None], pl.LazyFrame] = {}
        self.fanout = self._fanout_counts() if self.display else {}
        self.runtime_demands: dict[projection_planner.ProjectionEdgeKey, frozenset[str]] = {}
        self.runtime_resolved: set[str] = set()

    def _fanout_counts(self) -> dict[tuple[str, str | None], int]:
        """How many edges read each producer frame, counted by the selected source frame."""
        counts: dict[tuple[str, str | None], int] = {}
        for edge in self.graph_plan.relevant_edges:
            key = (edge.source, edge.sourceHandle)
            counts[key] = counts.get(key, 0) + 1
        return counts

    # ------------------------------------------------------------------ run

    def run(self) -> WalkResult:
        self.prepare()
        return self.walk({})

    def prepare(self) -> None:
        """Everything a walk does once: check, prepare, seed, plan, and build."""
        self._check_plan()
        if self.policy.purpose is WalkPurpose.SINK:
            if self.context is not None:
                self.context.checkpoint(label="lazy_start")
            self._prepare_inputs()
        self._read_seeds()
        self.projection = self._plan_projection()
        self.boundaries = self._build_boundaries()
        self.captures = _PlannedCaptures(
            self.plan,
            self.request.graph,
            execution_context=self.context,
            incoming_edges_by_target=self.prepared.incoming_edges_by_target,
        )

    def walk(self, start_frames: Mapping[str, pl.LazyFrame]) -> WalkResult:
        """Visit the nodes once, from *start_frames* for the nodes above the walk."""
        self._init_walk_state()
        self.frames.update(start_frames)
        self._bind_plan_sources()
        self._start_step_progress()
        for node_id in self.run_order:
            self._walk_node(node_id)
        if self.display:
            self._replan_target_preview()
        return self._result()

    # ------------------------------------------------------- step progress

    def _start_step_progress(self) -> None:
        """Count a display walk's heavy steps: its planned captures and its collections.

        Every other node only builds a lazy plan, so these steps are where the
        time goes; each counts once, with equal weight.
        """
        self.steps_done: set[tuple[str, str]] = set()
        self.step_reporter = (
            self.context.step_progress if self.display and self.context is not None else None
        )
        self.steps_total = 0
        if self.step_reporter is None:
            return
        captures = (
            set(self.decision.captures) - set(self.decision.seeds)
            if self.decision is not None
            else set()
        )
        self.steps_total = sum(1 for node_id in self.run_order if node_id in captures) + sum(
            1 for node_id in self.run_order if self.policy.collects(node_id)
        )
        if self.steps_total:
            self.step_reporter(StepProgress(done=0, total=self.steps_total, label=""))

    def _step_label(self, kind: str, node_id: str) -> str:
        node = self.node_map.get(node_id)
        name = (node.data.label if node is not None else "") or node_id
        return f"{'Caching' if kind == 'capture' else 'Computing'} {name}"

    def _step_started(self, kind: str, node_id: str) -> None:
        if self.step_reporter is None:
            return
        self.step_reporter(
            StepProgress(
                done=len(self.steps_done),
                total=max(self.steps_total, len(self.steps_done) + 1),
                label=self._step_label(kind, node_id),
            )
        )

    def _step_finished(self, kind: str, node_id: str) -> None:
        if self.step_reporter is None:
            return
        self.steps_done.add((kind, node_id))
        done = len(self.steps_done)
        self.step_reporter(
            StepProgress(
                done=done,
                total=max(self.steps_total, done),
                label=self._step_label(kind, node_id),
            )
        )

    def _result(self) -> WalkResult:
        frames = (
            {node_id: _plan_of(frame) for node_id, frame in self.frames.items()}
            if self.display
            else self.frames
        )
        return WalkResult(
            frames=frames,
            order=self.order,
            run_order=self.run_order,
            parents_of=self.parents_of,
            node_map=self.node_map,
            id_to_name=self.graph_plan.id_to_name,
            collected=self.collected,
            errors=self.errors,
            error_lines=self.error_lines,
            timings=self.timings,
            memory_bytes=self.memory_bytes,
            available_columns=self.available_columns,
            output_columns=self.output_columns,
            frame_columns=self.frame_columns,
            join_recipes=self.join_recipes,
            write_recipes=self.write_recipes,
            unshaped_frames=self.unshaped_frames,
        )

    def _run_order(self) -> list[str]:
        """The prepared order, restricted under a plan to its seeds and executed nodes."""
        if self.request.walk_node_ids is not None:
            return [node_id for node_id in self.order if node_id in self.request.walk_node_ids]
        if self.decision is None:
            return list(self.order)
        planned = set(self.decision.executed_node_ids) | set(self.decision.seeds)
        return [node_id for node_id in self.order if node_id in planned]

    def _check_plan(self) -> None:
        if self.decision is not None:
            _check_snapshot_plan(
                self.decision,
                self.request.graph,
                target_node_id=self.request.target_node_id,
                source=self.request.source,
                profile=self.profile,
            )

    def _prepare_inputs(self) -> None:
        """Build or refresh snapshot-backed inputs before strategy planning reads them.

        A schema-only walk reads no data, and a seed plan was opened after
        preparing exactly the inputs it reads.
        """
        request = self.request
        prepare_input_snapshots(
            self.order,
            self.node_map,
            profile=self.context.profile if self.context is not None else None,
            execution_context=self.context,
            base_dir=preparation_base_dir(self.graph),
            schema_only=(
                request.schema_only or not request.prepare_inputs or self.decision is not None
            ),
        )

    def _read_seeds(self) -> None:
        """Bind each seed's leased generation and record the plan's evidence."""
        decision = self.decision
        if decision is None or self.plan is None:
            return
        from haute._seed_plans import SharedSnapshotCaptureSkipRecord, SharedSnapshotSeedRecord

        for node_id, seed in decision.seeds.items():
            self.seed_frames[node_id] = self.plan.seed_frame(node_id)
            if self.context is not None:
                self.context.record_shared_snapshot_seed(
                    SharedSnapshotSeedRecord(
                        node_id=node_id,
                        identity_digest=seed.identity.digest,
                        generation_id=seed.generation_id,
                        columns=seed.demand,
                    )
                )
        if self.context is not None:
            for node_id, reason in sorted(decision.skipped_captures.items()):
                self.context.record_shared_snapshot_capture_skip(
                    SharedSnapshotCaptureSkipRecord(node_id=node_id, reason=reason)
                )

    # ------------------------------------------------------------ planning

    def _planning_required_columns(
        self,
    ) -> dict[str, set[str] | projection_planner.AllExceptColumns]:
        """The caller's demand, or under a plan the demand it negotiated for its captures."""
        if self.decision is None:
            return dict(self.prepared.normalised_required_columns)
        return {
            node_id: (
                demand if isinstance(demand, projection_planner.AllExceptColumns) else set(demand)
            )
            for node_id, demand in self.decision.planning_required_columns.items()
        }

    def _prepared_plan(
        self, required: Mapping[str, set[str] | projection_planner.AllExceptColumns]
    ) -> projection_planner.ProjectionPlan:
        return projection_planner.compute_prepared_plan(
            self.order,
            self.prepared.children_of,
            self.node_map,
            required,
            relevant_edges=self.graph_plan.relevant_edges,
            submodels=self.graph.submodels,
            selector_aliases=self.selector_aliases,
        )

    def _plan_projection(self) -> _WalkProjection:
        if self.display:
            return self._plan_display_projection()
        if self.chunk:
            return self._plan_chunk_projection()
        return self._plan_sink_projection()

    def _plan_chunk_projection(self) -> _WalkProjection:
        """A chunk walk's demand is its chunk plan's: nothing is planned here."""
        demand = self.request.output_demand
        return _WalkProjection(
            needed_by_node=demand,
            edge_demands={},
            projects_edges=False,
            runtime_plan=None,
            collect_needed={},
            builder_needed=demand,
            api_port_columns={},
            strategy_required={},
            boundary_operators={},
        )

    def _plan_sink_projection(self) -> _WalkProjection:
        """Plan the execution strategy and the column demand of every edge and builder."""
        planning_required = self._planning_required_columns()
        planned_ids = self.decision.executed_node_ids if self.decision is not None else None
        operators = {
            node_id: operator
            for node_id, operator in projection_planner.materialising_operators_by_node(
                self.order,
                self.node_map,
                relevant_edges=self.graph_plan.relevant_edges,
                submodels=self.graph.submodels,
            ).items()
            if planned_ids is None or node_id in planned_ids
        }
        self.strategy = self._plan_strategy(planning_required, planned_ids, operators)
        plan = self.strategy.projection_plan
        normalised = self.prepared.normalised_required_columns
        broadened = planning_required != normalised
        return _WalkProjection(
            needed_by_node=plan.needed_by_node,
            edge_demands=plan.edge_demands,
            projects_edges=True,
            runtime_plan=self._prepared_plan(normalised) if broadened else plan,
            collect_needed={},
            builder_needed=self._builder_demand(plan.needed_by_node, broadened=broadened),
            api_port_columns=projection_planner.api_input_port_columns_by_node(
                self.node_map, self.graph_plan.relevant_edges, plan
            ),
            strategy_required=planning_required,
            boundary_operators=operators,
        )

    def _plan_display_projection(self) -> _WalkProjection:
        """Plan demand from the caller only; the caller already planned the strategy.

        Under a plan the negotiated demand is what the walk reads, builds and
        captures; what a collected node collects is still the caller's own.
        """
        planning_required = self._planning_required_columns()
        normalised = self.prepared.normalised_required_columns
        plan = self._prepared_plan(planning_required) if planning_required else None
        needed = plan.needed_by_node if plan is not None else {}
        collect_needed: Mapping[str, frozenset[str] | None] = needed
        if planning_required != normalised:
            collect_needed = self._prepared_plan(normalised).needed_by_node if normalised else {}
        strategy = self.context.projection_plan if self.context is not None else None
        if isinstance(strategy, projection_planner.ExecutionStrategyResult):
            self.strategy = strategy
        # The caller's strategy proves API-port demand only for the caller's
        # own request; under a plan the ports load the negotiated demand.
        port_plan = (
            self.strategy.projection_plan
            if self.decision is None and self.strategy is not None
            else plan
        )
        return _WalkProjection(
            needed_by_node=needed,
            edge_demands=plan.edge_demands if plan is not None else {},
            projects_edges=False,
            runtime_plan=None,
            collect_needed=collect_needed,
            builder_needed=projection_planner.builder_required_output_columns_by_node(
                self.node_map, needed, preserve_eager_model_score_inputs=True
            ),
            api_port_columns=(
                projection_planner.api_input_port_columns_by_node(
                    self.node_map, self.graph_plan.relevant_edges, port_plan
                )
                if port_plan is not None
                else {}
            ),
            strategy_required=normalised,
            boundary_operators=projection_planner.materialising_operators_by_node(
                self.order,
                self.node_map,
                relevant_edges=self.graph_plan.relevant_edges,
                submodels=self.graph.submodels,
            ),
        )

    def _plan_strategy(
        self,
        planning_required: Mapping[str, set[str] | projection_planner.AllExceptColumns],
        planned_ids: Iterable[str] | None,
        operators: Mapping[str, str],
    ) -> projection_planner.ExecutionStrategyResult:
        """Plan and admit the strategy; a materialising group-by needs the RAM estimate."""
        request = self.request
        if operators and not request.schema_only:
            return execution_facade.plan_execution_strategy(
                execution_facade.ProjectionRequest(
                    graph=self.graph,
                    target_node_id=request.target_node_id,
                    profile=self.profile,
                    required_columns_by_node=planning_required,
                    source=request.source,
                ),
                execution_context=self.context,
                runtime_source_frames_by_node=request.runtime_source_frames_by_node,
                materialising_node_ids=planned_ids,
                estimation_graph=(
                    self.plan.estimation_graph(self.graph) if self.plan is not None else None
                ),
            )
        return execution_facade.plan_prepared_execution_strategy(
            self.order,
            {node_id: list(children) for node_id, children in self.prepared.children_of.items()},
            self.node_map,
            profile=self.profile,
            required_columns_by_node=planning_required,
            execution_context=self.context,
            schema_only=request.schema_only,
            relevant_edges=self.graph_plan.relevant_edges,
            submodels=self.graph.submodels,
            selector_aliases=self.selector_aliases,
            materialising_node_ids=planned_ids,
        )

    def _builder_demand(
        self,
        needed_by_node: Mapping[str, frozenset[str] | None],
        *,
        broadened: bool,
    ) -> dict[str, frozenset[str] | None]:
        """Each builder's output demand.

        A source builder cannot validate a best-effort capture-only demand
        before its lazy schema exists, so under a broadened demand it scans
        broadly and the first edge projection narrows it.
        """
        demand = projection_planner.builder_required_output_columns_by_node(
            self.node_map,
            needed_by_node,
            preserve_eager_model_score_inputs=False,
        )
        if broadened:
            for node_id in self.order:
                if not self.parents_of.get(node_id):
                    demand[node_id] = None
        return demand

    def _build_boundaries(self) -> NodeBoundaryRunner:
        """Build every node the walk invokes: not seeds, and not pass-through nodes."""
        pass_through = self.decision.pass_through_edges if self.decision is not None else {}
        build_order = [
            node_id
            for node_id in self.run_order
            if node_id not in self.seed_frames and node_id not in pass_through
        ]
        request = self.request
        sink = self.policy.purpose is WalkPurpose.SINK
        with self._stage("lazy_build_functions" if sink else None):
            funcs = _build_funcs(
                build_order,
                self.node_map,
                self.graph_plan.id_to_name,
                self.prepared.all_parents,
                request.build_node_fn,
                incoming_edges_by_target=self.prepared.incoming_edges_by_target,
                all_incoming_edges_by_target=self.prepared.all_incoming_edges_by_target,
                all_node_map=self.graph.node_map,
                row_limit=self.policy.row_limit,
                preamble_ns=request.preamble_ns,
                source=request.source,
                source_by_node=request.source_by_node,
                required_output_columns_by_node=self.projection.builder_needed,
                required_output_columns_by_port_by_node=self.projection.api_port_columns,
                reuse_loaded_model_by_node=request.reuse_loaded_model_by_node,
                execution_profile=self.context.profile if self.context is not None else None,
                schema_only=request.schema_only,
                submodels=self.graph.submodels,
            )
        self.funcs = funcs
        return NodeBoundaryRunner(
            prepared=self.prepared,
            funcs=funcs,
            enforce_contracts=request.enforce_contracts,
            execution_context=self.context,
            needed_columns=self.projection.needed_by_node,
        )

    # ------------------------------------------------------------- walking

    def _stage(
        self, name: str | None, node_id: str | None = None
    ) -> contextlib.AbstractContextManager[Any]:
        if self.context is None or name is None:
            return contextlib.nullcontext()
        return self.context.stage(name, node_id=node_id)

    def _bind_plan_sources(self) -> None:
        """Under a plan, build every source, then prove the inputs are the planned ones.

        Nothing is collected or captured before the runtime inputs are
        checked against the fingerprint the plan was resolved with. A display
        walk holds a source's failure until the walk reaches the node, where
        it is recorded like any other.
        """
        if self.decision is None:
            return
        pass_through = self.decision.pass_through_edges
        for node_id in self.run_order:
            if (
                node_id in self.seed_frames
                or node_id in pass_through
                or self.parents_of.get(node_id)
            ):
                continue
            try:
                self.prebuilt[node_id] = self._build_node(node_id, self.boundaries.open(node_id))
            except Exception as exc:
                if not self.display:
                    raise
                self.prebuilt[node_id] = exc
        self.captures.verify_inputs()

    def _walk_node(self, node_id: str) -> None:
        started = time.perf_counter()
        if not self.policy.record_failures:
            self._visit(node_id)
        else:
            try:
                self._visit(node_id)
            except _UpstreamFailedError:
                return
            except Exception as exc:
                if type(exc) is MemoryError:
                    raise self._budget_error(exc) from exc
                if not self._recordable(exc):
                    raise
                self._record_failure(node_id, exc)
        if self.display:
            self.timings[node_id] = round((time.perf_counter() - started) * 1000, 1)

    def _visit(self, node_id: str) -> None:
        built = self._node_frame(node_id)
        captured = False
        frame = built.frame
        if self.decision is not None and not built.seeded:
            frame, captured = self._capture(node_id, built)
        if isinstance(frame, dict):
            self._finish_bundle(node_id, built)
        elif self.display:
            self._collect(node_id, built, frame)
        else:
            self.frames[node_id] = frame
            if captured or built.pass_through:
                self._release_consumed_parents(node_id)

    def _node_frame(self, node_id: str) -> _NodeFrame:
        """The node's frame before any capture: passed through, seeded, or built."""
        if self.decision is not None and node_id in self.decision.pass_through_edges:
            return self._pass_through(node_id, self.decision.pass_through_edges[node_id])
        seed = self.seed_frames.get(node_id)
        if seed is not None:
            return self._read_seed(node_id, seed)
        prebuilt = self.prebuilt.pop(node_id, None)
        if isinstance(prebuilt, Exception):
            raise prebuilt
        if prebuilt is not None:
            return prebuilt
        boundary = self.boundaries.open(node_id)
        return self._build_scored_node(node_id, boundary, self._stage_scored_capture(node_id))

    def _read_seed(self, node_id: str, seed: _Frame) -> _NodeFrame:
        """A seed's frame is its leased generation: nothing is built or checked for it."""
        self.column_cache[(node_id, None)] = _columns_of(seed)
        if not self.display:
            logger.info("lazy_seed_hit", node_id=node_id)
            if self.context is not None:
                self.context.checkpoint(label="lazy_seed_hit", node_id=node_id)
        node = self.node_map[node_id]
        boundary = NodeBoundary(
            node_id=node_id,
            node=node,
            fn=_passthrough_fn,
            is_source=True,
            parent_ids=(),
            incoming_edges=(),
            contract=None,
            check_contract=False,
            is_passthrough_runtime=False,
        )
        names = self._describe(node_id, node, seed, seed, seeded=True) if self.display else None
        return _NodeFrame(frame=seed, boundary=boundary, seeded=True, output_names=names)

    def _stage_scored_capture(self, node_id: str) -> NodeSnapshotArtifact | None:
        """A batch Model Score whose output is its scored file writes it into its capture.

        A display walk captures a Model Score only when a capture below drains
        its whole output; otherwise it scores just the rows it collects.
        """
        if self.decision is None:
            return None
        scenario = self.request.source_by_node.get(node_id, self.request.source or "live")
        return self.captures.stage_scored_output(node_id, self.node_map[node_id], scenario=scenario)

    def _build_scored_node(
        self,
        node_id: str,
        boundary: NodeBoundary,
        scored: NodeSnapshotArtifact | None,
    ) -> _NodeFrame:
        from haute._model_scorer import model_score_output_destination, model_score_whole_output

        # A captured Model Score's whole output is written, so it scores every
        # row a batch at a time even under a preview's row limit.
        whole_output = (
            model_score_whole_output()
            if self.decision is not None
            and node_id in self.decision.captures
            and self.node_map[node_id].data.nodeType == NodeType.MODEL_SCORE
            else contextlib.nullcontext()
        )
        if scored is None:
            with whole_output:
                return self._build_node(node_id, boundary)
        try:
            with whole_output, model_score_output_destination(scored.directory) as destination:
                built = self._build_node(node_id, boundary)
        except BaseException:
            scored.close()
            raise
        built.scored = scored
        built.scored_write = (destination.used, destination.digests)
        return built

    def _build_node(self, node_id: str, boundary: NodeBoundary) -> _NodeFrame:
        """Invoke one node and shape and check its output, inside its build stage.

        A chunk walk then narrows the output to its chunk plan's demand.
        """
        with self._stage(_NODE_STAGES.get(self.policy.purpose), node_id):
            inputs = [] if boundary.is_source else self._node_inputs(boundary)
            try:
                frame = self.boundaries.invoke(boundary, inputs)
                # A display walk resolves the node's schema here, so a lazy
                # plan's failure surfaces while the inputs are still in hand.
                built = self._shape_output(boundary, frame)
            except Exception:
                if inputs and self.policy.record_failures:
                    self.failed_inputs[node_id] = inputs
                raise
            if self.chunk:
                built.frame = project_output(
                    built.frame,
                    self.request.output_demand.get(node_id),
                    node=boundary.node,
                    ordering_cache=self.output_order,
                )
            return built

    def _check_parents(self, boundary: NodeBoundary) -> None:
        failed = [parent for parent in boundary.parent_ids if parent in self.failed]
        if failed:
            self._record_upstream_failure(boundary.node_id, failed)
            raise _UpstreamFailedError(boundary.node_id)
        missing = [parent for parent in boundary.parent_ids if parent not in self.frames]
        if missing:
            raise ValueError(
                f"Node '{boundary.node_id}' is missing input(s) from: {missing}. "
                "Upstream node(s) may have failed or not been registered."
            )

    def _input_frame(self, edge: GraphEdge) -> _Frame:
        """The frame *edge* delivers: its source frame, picked by port.

        A display walk reads a producer frame that feeds several edges
        through one ``cache()`` node, so separate collections below it share
        one computation of it.
        """
        picked = _pick_source_frame(self.frames[edge.source], edge)
        if not self.display:
            return picked
        key = (edge.source, edge.sourceHandle)
        if isinstance(picked, pl.LazyFrame) and self.fanout.get(key, 0) > 1:
            shared = self.shared_inputs.get(key)
            if shared is None:
                shared = picked.cache()
                self.shared_inputs[key] = shared
            return shared
        return _lazy(picked)

    def _node_inputs(self, boundary: NodeBoundary) -> list[_Frame]:
        """Route, project and contract-check the frames a node receives."""
        node_id = boundary.node_id
        self._check_parents(boundary)
        inputs = [self._input_frame(edge) for edge in boundary.incoming_edges]
        if not inputs:
            raise ValueError(f"No input data available for node '{node_id}'")
        # A chunk plan already proved every edge's demand; it infers nothing more.
        runtime_demands = {} if self.chunk else self._runtime_demands(boundary, inputs)
        projected: list[_Frame] = []
        known: list[frozenset[str] | None] = []
        for edge, frame in zip(boundary.incoming_edges, inputs, strict=True):
            demand = runtime_demands.get(projection_planner.ProjectionEdgeKey.from_edge(edge))
            projected_frame, columns = self._project_edge(edge, frame, runtime_demand=demand)
            projected.append(projected_frame)
            known.append(columns)
        if self.context is not None and all(columns is not None for columns in known):
            self.context.record_column_widths(
                node_id=node_id,
                input_width=sum(len(columns) for columns in known if columns is not None),
            )
        if boundary.check_contract and boundary.contract is not None:
            if boundary.contract.inputs is not None:
                self.boundaries.assert_inputs(
                    boundary, self._input_columns(boundary, projected, known)
                )
        self._record_recipes(boundary, projected)
        return projected

    def _input_columns(
        self,
        boundary: NodeBoundary,
        inputs: Sequence[_Frame],
        known: Sequence[frozenset[str] | None],
    ) -> frozenset[str]:
        """The union of a node's input columns, projected or read from each input's schema."""
        column_sets: list[frozenset[str]] = []
        for edge, frame, columns in zip(boundary.incoming_edges, inputs, known, strict=True):
            if columns is None:
                key = (edge.source, edge.sourceHandle)
                columns = self.column_cache.get(key)
                if columns is None:
                    columns = _columns_of(frame)
                    self.column_cache[key] = columns
            column_sets.append(columns)
        return frozenset().union(*column_sets)

    def _runtime_demands(
        self,
        boundary: NodeBoundary,
        inputs: Sequence[_Frame],
    ) -> dict[projection_planner.ProjectionEdgeKey, set[str]]:
        """Demands only the parents' built schemas prove, folded into the strategy."""
        demands = _runtime_lineage_demands(
            boundary.node,
            boundary.incoming_edges,
            inputs,
            self.projection.needed_by_node.get(boundary.node_id),
            self.projection.edge_demands,
            self.node_map,
            self.graph.submodels,
            self.selector_aliases,
        )
        if demands and self.context is not None and self.strategy is not None:
            self._refine_strategy(self.strategy, demands)
        return demands

    def _refine_strategy(
        self,
        previous: projection_planner.ExecutionStrategyResult,
        demands: Mapping[projection_planner.ProjectionEdgeKey, set[str]],
    ) -> None:
        """Record runtime-proven edge demands on the execution's strategy diagnostic."""
        assert self.context is not None
        resolved = _runtime_projectable_source_ids(demands, self.node_map)
        self.runtime_demands.update((key, frozenset(columns)) for key, columns in demands.items())
        self.runtime_resolved.update(resolved)
        refined_plan = projection_planner.with_runtime_inferred_streaming_edges(
            previous.projection_plan,
            demands_by_edge=demands,
            resolved_parent_ids=resolved,
            relevant_edges=self.graph_plan.relevant_edges,
        )
        diagnostic = previous.diagnostic
        self.strategy = projection_planner.build_execution_strategy_result(
            refined_plan,
            profile=self.context.profile,
            order=self.order,
            children_of=self.prepared.children_of,
            node_map=self.node_map,
            has_projection_seed=bool(self.projection.strategy_required),
            required_columns_by_node=self.projection.strategy_required,
            estimated_peak_bytes=diagnostic.estimated_peak_bytes,
            raw_estimated_peak_bytes=diagnostic.raw_estimated_peak_bytes,
            estimate_calibration_factor_basis_points=(
                diagnostic.estimate_calibration_factor_basis_points
            ),
            estimate_admission_basis=diagnostic.estimate_admission_basis,
            headroom_bytes=diagnostic.headroom_bytes,
            assumptions=diagnostic.assumptions,
            boundary_operators=self.projection.boundary_operators,
            **_conservative_strategy_passthrough(diagnostic),
        )
        self.context.projection_plan = self.strategy

    def _project_edge(
        self,
        edge: GraphEdge,
        frame: _Frame,
        *,
        runtime_demand: set[str] | None = None,
    ) -> tuple[_Frame, frozenset[str] | None]:
        """Narrow an input to its edge's demand; ``None`` columns when left whole."""
        demand: set[str] | frozenset[str] | None = runtime_demand
        if demand is None and self.projection.projects_edges:
            demand = self.projection.edge_demands.get(
                projection_planner.ProjectionEdgeKey.from_edge(edge)
            )
        if demand is None:
            return frame, None
        lazy_frame = frame if isinstance(frame, pl.LazyFrame) else frame.lazy()
        schema_columns = lazy_frame.collect_schema().names()
        missing = set(demand) - set(schema_columns)
        required = set(demand) if runtime_demand is not None else self._run_demand(edge)
        if missing & required:
            raise ContractMismatchError(
                "Columns required by a projection contract are missing from the parent frame.",
                node_id=edge.target,
                parent_id=edge.source,
                missing=sorted(missing & required),
                required_columns=sorted(demand),
                parent_columns=sorted(schema_columns),
            )
        if missing:
            # A seed plan's negotiated demand may be broader than this run's
            # own. A capture-only column the schema lacks is dropped from the
            # capture and must not fail an otherwise valid execution.
            logger.warning(
                "planned_projection_column_missing",
                node_id=edge.target,
                parent_id=edge.source,
                missing=sorted(missing),
            )
            demand = set(demand) - missing
        ordered = projected_or_carrier_columns(schema_columns, demand)
        return lazy_frame.select(ordered), frozenset(ordered)

    def _run_demand(self, edge: GraphEdge) -> set[str]:
        """The columns the run itself reads over *edge*, as opposed to a capture's."""
        runtime_plan = self.projection.runtime_plan
        assert runtime_plan is not None
        return set(runtime_plan.demand_for_edge(edge) or ())

    def _record_recipes(self, boundary: NodeBoundary, inputs: Sequence[_Frame]) -> None:
        """The recipes a full write of this node can be chunked by."""
        node = boundary.node
        if self.chunk:
            return
        join = _edge_join_recipe(boundary.fn, node, inputs)
        if join is not None:
            self.join_recipes[boundary.node_id] = join
        if self.display:
            return
        write = _write_recipe(
            boundary.fn,
            node,
            inputs,
            frame_names=self._input_names(boundary),
            orig_frame_names=resolve_orig_source_names(
                node, self.original_node_map, self.all_edges_by_target
            ),
            selector_aliases=self.selector_aliases,
        )
        if write is not None:
            self.write_recipes[boundary.node_id] = write

    def _input_names(self, boundary: NodeBoundary) -> list[str]:
        names: list[str] = []
        for edge in boundary.incoming_edges:
            try:
                names.append(
                    edge_input_name(
                        edge, self.node_map[edge.source], submodels=self.graph.submodels
                    )
                )
            except ValueError:
                # API-input null handle only; _build_funcs raises first for every other.
                pass
        return names

    def _shape_output(
        self, boundary: NodeBoundary, result: Any, *, pass_through: bool = False
    ) -> _NodeFrame:
        """Check the node's output contract, then apply its own column selection and renames."""
        node_id = boundary.node_id
        frame = self._as_frame(node_id, result)
        if isinstance(frame, dict):
            # A multi-frame source's bundle: consumers pick a frame per edge,
            # so the per-frame shaping and checks apply to what they pick.
            if not self.display:
                for port, port_frame in frame.items():
                    self.column_cache[(node_id, port)] = _columns_of(port_frame)
            return _NodeFrame(frame=frame, boundary=boundary)
        shapes = _shapes_output(boundary.node) and not pass_through
        if shapes:
            # Its columns before its own selection and renames, which a
            # snapshot records so a seeded preview can still report them.
            self.unshaped_frames[node_id] = frame
        config = boundary.node.data.config
        shaped = _lazy(_apply_column_renames(_lazy(_apply_selected_columns(frame, config)), config))
        names = self._describe(node_id, boundary.node, frame, shaped) if self.display else None
        self._check_output(boundary, shaped, names, created=frame if shapes else None)
        return _NodeFrame(frame=shaped, boundary=boundary, output_names=names)

    def _as_frame(self, node_id: str, result: Any) -> Any:
        """A node's result as a lazy frame, or a multi-frame source's bundle as returned."""
        sink = self.policy.purpose is WalkPurpose.SINK
        if isinstance(result, pl.DataFrame):
            if self.context is not None and sink:
                self.context.record_column_widths(node_id=node_id, output_width=result.width)
            return result.lazy()
        if isinstance(result, pl.LazyFrame | dict) or sink:
            return result
        raise TypeError(
            f"Node '{node_id}' returned {type(result).__name__}; expected a Polars frame."
        )

    def _check_output(
        self,
        boundary: NodeBoundary,
        shaped: pl.LazyFrame,
        names: list[str] | None,
        *,
        created: pl.LazyFrame | None,
    ) -> None:
        """Assert the output contract; record the shaped columns a sink walk resolved.

        The contract describes what the builder creates, so a node that
        shapes its output is checked against *created*, its frame before its
        own selection and renames: deselecting or renaming a column it
        creates is the author's choice, not a missing output.
        """
        contract = boundary.contract
        if (
            not boundary.check_contract
            or contract is None
            or contract.outputs is None
            or boundary.is_passthrough_runtime
        ):
            return
        if names is not None:
            columns = frozenset(names)
        else:
            columns = _columns_of(shaped)
            self.column_cache[(boundary.node_id, None)] = columns
            if self.context is not None:
                self.context.record_column_widths(
                    node_id=boundary.node_id, output_width=len(columns)
                )
        if created is not None:
            columns = _columns_of(created)
        self.boundaries.assert_outputs(boundary, columns)

    def _pass_through(self, node_id: str, edge: GraphEdge) -> _NodeFrame:
        """A pass-through node is its selected input: its builder is never called."""
        if self.context is not None:
            self.context.checkpoint(label="before_node", node_id=node_id)
        boundary = NodeBoundary(
            node_id=node_id,
            node=self.node_map[node_id],
            fn=_passthrough_fn,
            is_source=False,
            parent_ids=(edge.source,),
            incoming_edges=(edge,),
            contract=None,
            check_contract=False,
            is_passthrough_runtime=True,
        )
        self._check_parents(boundary)
        selected = self._input_frame(edge)
        if not self.display:
            self._pass_through_recipe(node_id, edge, selected)
        projected, _columns = self._project_edge(edge, selected)
        built = self._shape_output(boundary, projected, pass_through=True)
        built.pass_through = True
        return built

    def _pass_through_recipe(self, node_id: str, edge: GraphEdge, selected: _Frame) -> None:
        """Compose a pass-through's write recipe forward from its parent's."""
        parent_recipe = self.write_recipes.get(edge.source)
        if parent_recipe is None:
            return
        if parent_recipe.fn is None:
            self.write_recipes[node_id] = WriteRecipe(
                input=parent_recipe.input,
                fn=None,
                reason=parent_recipe.reason,
                blocking_operator=parent_recipe.blocking_operator,
            )
            return
        if not self._recipe_link_proved(edge, selected, parent_recipe):
            return
        config = self.node_map[node_id].data.config

        def project(lf: pl.LazyFrame) -> pl.LazyFrame:
            return _lazy(self._project_edge(edge, lf)[0])

        def column_step(lf: pl.LazyFrame) -> pl.LazyFrame:
            return _lazy(_apply_column_renames(_apply_selected_columns(lf, config), config))

        self.write_recipes[node_id] = parent_recipe.then(project).then(column_step)

    def _recipe_link_proved(
        self, edge: GraphEdge, selected: _Frame, parent_recipe: WriteRecipe
    ) -> bool:
        """Whether the parent's recipe still describes the frame the pass-through selected.

        The identity test is the multi-frame guard: the source pick returns
        the parent's own object for a single-frame parent and a different one
        for a sub-frame the parent's recipe does not describe. The second is
        the replacement test: every site that replaces a parent's frame after
        a capture or a seed records it as file-backed.
        """
        if selected is self.frames[edge.source] and edge.source not in self.file_backed:
            return True
        try:
            check_recipe_equivalence(parent_recipe, _lazy(selected))
        except RecipeEquivalenceError:
            return False
        return True

    def _capture(self, node_id: str, built: _NodeFrame) -> tuple[_Frame, bool]:
        """Record the node's closure and, at a capture point, sink it and continue from it.

        A capture's storage failure is the store's, never the node's: it
        propagates even from a walk that records node failures.
        """
        assert self.decision is not None
        closure = self.captures.record_closure(node_id)
        if node_id not in self.decision.captures:
            return built.frame, False
        prewritten, digests = built.scored_write
        unshaped = self.unshaped_frames.get(node_id)
        self._step_started("capture", node_id)
        try:
            captured = self.captures.capture(
                node_id,
                built.frame,
                closure,
                artifact=built.scored,
                prewritten=prewritten,
                prewritten_digests=digests,
                join=self.join_recipes.get(node_id),
                recipe=self.write_recipes.get(node_id),
                unshaped_columns=_schema_pairs(unshaped) if unshaped is not None else None,
            )
        except (SourceCacheError, OSError) as exc:
            self.store_failures.append(exc)
            raise
        self._step_finished("capture", node_id)
        if not self.display:
            self._after_sink_capture(node_id, captured)
        return captured, True

    def _after_sink_capture(self, node_id: str, captured: pl.LazyFrame) -> None:
        """A captured frame is a scan of the published file; free memory every few captures."""
        self.file_backed.add(node_id)
        self.column_cache[(node_id, None)] = _columns_of(captured)
        self.materialisations_since_gc += 1
        if self.materialisations_since_gc >= _GC_BATCH_INTERVAL:
            gc.collect()
            _malloc_trim()
            self.materialisations_since_gc = 0

    def _release_consumed_parents(self, node_id: str) -> None:
        """Drop parent frames no consumer still needs, so Polars can free their buffers.

        Sources (cheap scans callers may still read), preserved outputs and
        file-backed frames (scans a downstream plan composes from) are kept.
        """
        for parent_id in self.parents_of.get(node_id, []):
            self.remaining[parent_id] -= 1
            _fn, parent_is_source = self.funcs.get(parent_id, (None, False))
            if (
                self.remaining[parent_id] <= 0
                and parent_id in self.frames
                and not parent_is_source
                and parent_id not in self.preserved
                and parent_id not in self.file_backed
            ):
                del self.frames[parent_id]

    # -------------------------------------------------------- display walk

    def _describe(
        self,
        node_id: str,
        node: GraphNode,
        unshaped: pl.LazyFrame,
        shaped: pl.LazyFrame,
        *,
        seeded: bool = False,
    ) -> list[str]:
        """Report a node's schema before and after its own shaping; return its output names.

        A seed is its shaped output, so its columns before shaping come from
        what its generation recorded.
        """
        available = _schema_items(unshaped)
        if seeded and self.plan is not None:
            recorded = self.plan.seed_unshaped_columns(node_id)
            if recorded is not None:
                available = list(recorded)
        output = self._full_model_score_schema(node_id, node, _schema_items(shaped))
        if _is_plain_model_score(node):
            available = output
        self.available_columns[node_id] = available
        self.output_columns[node_id] = output
        if self.context is not None:
            self.context.record_column_widths(node_id=node_id, output_width=len(output))
        return [name for name, _dtype in output]

    def _full_model_score_schema(
        self, node_id: str, node: GraphNode, actual: _SchemaItems
    ) -> _SchemaItems:
        """A plain Model Score reports its parent's columns plus what it generates.

        Its collection may carry only the columns the preview asked for, but
        its schema is the whole scored frame.
        """
        parent_ids = self.parents_of.get(node_id, [])
        parent_columns = self.output_columns.get(parent_ids[0]) if parent_ids else None
        if not _is_plain_model_score(node) or parent_columns is None:
            return actual
        actual_by_name = dict(actual)
        generated = [str(node.data.config.get("output_column") or "prediction")]
        if f"{generated[0]}_proba" in actual_by_name:
            generated.append(f"{generated[0]}_proba")
        full = [(name, actual_by_name.get(name, dtype)) for name, dtype in parent_columns]
        seen = {name for name, _dtype in full}
        full.extend(
            (name, actual_by_name[name])
            for name in generated
            if name not in seen and name in actual_by_name
        )
        return full

    def _collect(self, node_id: str, built: _NodeFrame, frame: pl.LazyFrame) -> None:
        """Collect a node the policy names, under its limits; keep its plan either way.

        Consumers read the collection only when it holds every row and every
        column they need; a limited or narrowed collection never feeds them.
        """
        names = built.output_names or []
        projected = self._collect_projection(node_id, built.boundary.node, names)
        self.column_cache[(node_id, None)] = frozenset(names)
        if not self.policy.collects(node_id):
            self.frames[node_id] = frame
            return
        collect_frame = self._collect_frame(node_id, frame, names, projected)
        row_limit = self.policy.row_limit_for(node_id)
        if row_limit:
            collect_frame = collect_frame.head(row_limit)
        df = self._run_collect(node_id, collect_frame)
        self.collected[node_id] = df
        consumer_columns = self.projection.needed_by_node.get(node_id)
        covers = (
            consumer_columns <= set(df.columns)
            if consumer_columns is not None
            else df.width == len(names)
        )
        self.frames[node_id] = df if not row_limit and covers else frame
        self.memory_bytes[node_id] = int(df.estimated_size("b"))

    def _collect_projection(
        self, node_id: str, node: GraphNode, names: list[str]
    ) -> list[str] | None:
        """The columns a collection keeps when the caller named fewer than the node has."""
        projection = self.projection.collect_needed.get(node_id)
        if projection is None:
            return None
        missing = projection - set(names)
        if missing and node_id not in self.prepared.normalised_required_columns:
            raise ContractMismatchError(
                "Eager projection references columns missing from the node output schema.",
                node_id=node_id,
                node_type=node.data.nodeType.value,
                missing=sorted(missing),
                required_columns=sorted(projection),
                output_columns=sorted(names),
            )
        kept = [name for name in names if name in projection]
        return kept if len(kept) < len(names) else None

    def _collect_frame(
        self,
        node_id: str,
        frame: pl.LazyFrame,
        names: list[str],
        projected: list[str] | None,
    ) -> pl.LazyFrame:
        collect_frame = frame
        if projected is not None:
            logger.info(
                "eager_projection",
                node_id=node_id,
                total_cols=len(names),
                projected_cols=len(projected),
            )
            collect_frame = frame.select(projected)
        column_limit = self.policy.column_limits_by_node.get(node_id)
        if (
            column_limit is not None
            and self.projection.collect_needed.get(node_id) is None
            and len(names) > column_limit
        ):
            collect_frame = frame.select(names[:column_limit])
        return collect_frame

    def _run_collect(self, node_id: str, frame: pl.LazyFrame) -> pl.DataFrame:
        if self.context is None:
            return streaming_collect(frame)
        self.context.checkpoint(label="before_collect", node_id=node_id)
        self._step_started("collect", node_id)
        with self.context.stage("eager_collect", node_id=node_id):
            df = streaming_collect(frame, execution_context=self.context)
        self._step_finished("collect", node_id)
        self.context.checkpoint(label="after_collect", node_id=node_id)
        return df

    def _finish_bundle(self, node_id: str, built: _NodeFrame) -> None:
        """A multi-frame source's bundle: stored as returned, or reported frame by frame."""
        bundle = built.frame
        assert isinstance(bundle, dict)
        if not self.display:
            self.frames[node_id] = bundle
            return
        node = built.boundary.node
        declared = _declared_api_input_frame_schema_items(node)
        multi = len(declared) > 1 if declared else len(bundle) > 1
        if multi and declared:
            # Loading is demand-scoped, but a port's schema is a config
            # contract: every declared port is reported without opening it.
            for label, items in declared.items():
                self.frame_columns[(node_id, label)] = items
        plans = _bundle_plans(node_id, bundle)
        if self.policy.collects(node_id):
            self._collect_bundle(node_id, bundle, plans, multi=multi, declared=declared)
        else:
            self._describe_bundle(node_id, plans, multi=multi, declared=declared)
        self.available_columns.setdefault(node_id, [])
        self.output_columns.setdefault(node_id, [])
        if self.context is not None:
            self.context.checkpoint(label="after_node", node_id=node_id)

    def _collect_bundle(
        self,
        node_id: str,
        bundle: Mapping[str, pl.LazyFrame | pl.DataFrame],
        plans: dict[str, pl.LazyFrame],
        *,
        multi: bool,
        declared: Mapping[str, _SchemaItems],
    ) -> None:
        """Collect each frame of a bundle to the node's limit; consumers read the plans."""
        limit = self.policy.row_limit_for(node_id)
        collected: dict[str, pl.DataFrame] = {}
        self._step_started("collect", node_id)
        for label, port_frame in bundle.items():
            capped = port_frame.head(limit) if limit else port_frame
            collected[label] = (
                streaming_collect(capped, execution_context=self.context)
                if isinstance(capped, pl.LazyFrame)
                else capped
            )
        self._step_finished("collect", node_id)
        self.frames[node_id] = plans if limit else collected
        for label, df in collected.items():
            self.column_cache[(node_id, label)] = frozenset(df.columns)
            if multi and not declared:
                self.frame_columns[(node_id, label)] = _schema_items(df.lazy())
        if self.context is not None:
            self.context.record_column_widths(
                node_id=node_id, output_width=sum(df.width for df in collected.values())
            )
        self.collected[node_id] = collected
        if not multi and len(collected) == 1:
            port, df = next(iter(collected.items()))
            self._report_single_port(node_id, declared.get(port) or _schema_items(df.lazy()))

    def _describe_bundle(
        self,
        node_id: str,
        plans: dict[str, pl.LazyFrame],
        *,
        multi: bool,
        declared: Mapping[str, _SchemaItems],
    ) -> None:
        """An uncollected bundle keeps its per-frame plans; schemas come without collecting."""
        for label, plan in plans.items():
            self.column_cache[(node_id, label)] = frozenset(plan.collect_schema().names())
            if multi and not declared:
                self.frame_columns[(node_id, label)] = _schema_items(plan)
        self.frames[node_id] = plans
        if not multi and len(plans) == 1:
            port, plan = next(iter(plans.items()))
            self._report_single_port(node_id, declared.get(port) or _schema_items(plan))

    def _report_single_port(self, node_id: str, schema: _SchemaItems) -> None:
        """A one-frame bundle's schema is the node's ordinary schema."""
        self.available_columns[node_id] = schema
        self.output_columns[node_id] = schema

    def _recordable(self, exc: Exception) -> bool:
        """Whether a failure is the node's own, recorded rather than raised.

        Contract and schema mismatches, public contract errors, execution
        control signals and a capture's storage failure are run-level and
        always propagate.
        """
        if isinstance(
            exc,
            (
                ContractMismatchError,
                SchemaMismatchError,
                ExecutionCancelledError,
                ExecutionMemoryLimitExceededError,
            ),
        ):
            return False
        if is_public_contract_error(exc):
            return False
        return not any(exc is failure for failure in self.store_failures)

    def _budget_error(self, exc: MemoryError) -> MemoryError:
        """A native allocation failure as the run's memory-budget error.

        Native code (CatBoost's ``bad allocation``, for one) fails a request
        the process's memory cap refuses with a bare ``MemoryError``. That is
        the run exceeding its budget, not a fault of the node that happened to
        allocate, so it ends the run with the budget named, as a sampled
        overrun does.
        """
        context = self.context
        limit = context.memory_limit_bytes if context is not None else None
        rss = context.memory_sampler() if context is not None else None
        if context is None or limit is None or rss is None:
            return exc
        return ExecutionMemoryLimitExceededError(
            context.operation,
            job_id=context.job_id,
            rss_bytes=rss,
            limit_bytes=limit,
            baseline_rss_bytes=context.budget.memory_baseline_bytes,
            rss_limit_bytes=context.rss_limit_bytes,
        )

    def _record_failure(self, node_id: str, exc: Exception) -> None:
        logger.error("node_failed", node_id=node_id, error=str(exc))
        self.failed.add(node_id)
        self.frames.pop(node_id, None)
        self.collected[node_id] = None
        self.errors[node_id] = str(exc)
        error_line = _extract_error_line(exc)
        inputs = self.failed_inputs.pop(node_id, None)
        if error_line is None and inputs is not None:
            # A lazy plan fails after its code ran, so no line came with it.
            error_line = _located_step_line(self.funcs[node_id][0], inputs, exc)
        if error_line is not None:
            self.error_lines[node_id] = error_line

    def _record_upstream_failure(self, node_id: str, failed: Sequence[str]) -> None:
        self.failed.add(node_id)
        self.collected[node_id] = None
        self.errors[node_id] = "Upstream node(s) failed: " + "; ".join(
            f"{parent}: {self.errors[parent]}" if parent in self.errors else f"{parent}: failed"
            for parent in failed
        )
        for parent in failed:
            if parent in self.error_lines:
                self.error_lines[node_id] = self.error_lines[parent]
                break

    def _replan_target_preview(self) -> None:
        """Re-plan a target-only preview's diagnostic from the frames it built.

        Before execution an edge join cannot route demand to a parent whose
        schema is known only once built, so the executed strategy is planned
        again from the built schemas and the runtime-proven demands.
        """
        context = self.context
        target = self.request.target_node_id
        if (
            context is None
            or context.profile is not ExecutionProfile.PREVIEW_EAGER
            or target is None
            or self.policy.collect != frozenset({target})
            or not isinstance(context.projection_plan, projection_planner.ExecutionStrategyResult)
        ):
            return
        required = dict(self.prepared.normalised_required_columns)
        target_output = self.collected.get(target)
        if target not in required and isinstance(target_output, pl.DataFrame):
            # A preview that named no columns demands exactly what it collected.
            required[target] = set(target_output.columns)
        context.projection_plan = _replanned_target_preview_strategy(
            context.projection_plan,
            order=self.run_order,
            node_map=self.node_map,
            required_columns_by_node=required,
            relevant_edges=self.graph_plan.relevant_edges,
            graph=self.graph,
            known_output_columns={
                key: columns
                for key, columns in self.column_cache.items()
                if key[0] not in self.errors
            },
            runtime_edge_demands=self.runtime_demands,
            runtime_resolved_parent_ids=self.runtime_resolved,
            profile=context.profile,
            seeded_node_ids=frozenset(self.seed_frames),
        )


_NODE_STAGES = {WalkPurpose.SINK: "lazy_build", WalkPurpose.CHUNK: "chunk_node"}


def project_output(
    frame: pl.LazyFrame,
    columns: frozenset[str] | None,
    *,
    node: GraphNode,
    ordering_cache: dict[str, list[str]] | None = None,
) -> pl.LazyFrame:
    """Narrow a node's output to *columns*, in its schema's order.

    A node's output schema is chunk-invariant (identical transforms per
    chunk), so the ordered projection and its missing-column check are
    resolved once per node and reused for every later chunk instead of
    re-running ``collect_schema`` once per node per chunk.
    """
    if columns is None:
        return frame
    cached = None if ordering_cache is None else ordering_cache.get(node.id)
    if cached is None:
        schema_columns = frame.collect_schema().names()
        missing = set(columns) - set(schema_columns)
        if missing:
            raise ContractMismatchError(
                "Chunk projection references columns missing from the node output schema.",
                node_id=node.id,
                node_type=node.data.nodeType.value,
                missing=sorted(missing),
                required_columns=sorted(columns),
                output_columns=sorted(schema_columns),
            )
        cached = [column for column in schema_columns if column in columns]
        if ordering_cache is not None:
            ordering_cache[node.id] = cached
    return frame.select(cached)


def _lazy(frame: pl.LazyFrame | pl.DataFrame) -> pl.LazyFrame:
    return frame.lazy() if isinstance(frame, pl.DataFrame) else frame


def _columns_of(frame: pl.LazyFrame | pl.DataFrame) -> frozenset[str]:
    return frozenset(_lazy(frame).collect_schema().names())


def _schema_items(frame: pl.LazyFrame) -> _SchemaItems:
    schema = frame.collect_schema()
    return [(name, str(schema[name])) for name in schema.names()]


def _plan_of(frame: Any) -> Any:
    """A frame's uncapped plan, or a bundle's per-frame plans."""
    if isinstance(frame, dict):
        return {port: _lazy(port_frame) for port, port_frame in frame.items()}
    return _lazy(frame)


def _bundle_plans(node_id: str, bundle: Mapping[str, Any]) -> dict[str, pl.LazyFrame]:
    plans: dict[str, pl.LazyFrame] = {}
    for label, port_frame in bundle.items():
        if not isinstance(port_frame, pl.LazyFrame | pl.DataFrame):
            raise TypeError(
                f"Node '{node_id}' multi-frame output for frame {label!r} is not a Polars "
                f"frame (got {type(port_frame).__name__})."
            )
        plans[label] = _lazy(port_frame)
    return plans


__all__ = [
    "CollectPolicy",
    "PreparedWalk",
    "WalkPurpose",
    "WalkRequest",
    "WalkResult",
    "prepare_walk",
    "project_output",
    "walk_graph",
]
