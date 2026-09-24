"""The graph walker: every execution builds its frames in one walk.

A walk prepares the graph once (``_prepare_execution``), plans the column
demand it will read, builds each node's function, then visits the nodes in
topological order. At each node it routes the parents' frames along the
node's incoming edges, invokes the node through the shared
``NodeBoundaryRunner``, applies the node's own column shaping and contract
checks and, under a seed plan, captures the node into the shared snapshot
store. What differs between executions is the ``CollectPolicy`` the caller
passes.

No function in this module may exceed a cyclomatic complexity of 15; ruff's
C901 rule holds that, scoped to this module (``pyproject.toml``).
"""

from __future__ import annotations

import contextlib
import gc
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import polars as pl

import haute.execution as execution_facade
import haute.projection as projection_planner
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
    _edge_join_recipe,
    _PlannedCaptures,
    _prepare_execution,
    _runtime_lineage_demands,
    _runtime_projectable_source_ids,
    _schema_pairs,
    _shapes_output,
    _write_recipe,
)
from haute._execution_context import ExecutionContext, ExecutionProfile
from haute._graph_utils import (
    edge_input_name,
    resolve_orig_source_names,
    select_edge_source_output,
)
from haute._input_preparation import preparation_base_dir, prepare_input_snapshots
from haute._logging import get_logger
from haute._path_resolution import runtime_project_root_scoped
from haute._polars_selectors import preamble_selector_aliases
from haute._polars_utils import _malloc_trim, projected_or_carrier_columns
from haute._types import GraphEdge, PipelineGraph, _Frame
from haute.errors import ContractMismatchError

if TYPE_CHECKING:
    from haute._node_snapshots import NodeSnapshotArtifact
    from haute._seed_plans import SeedPlan, SeedPlanDecision

logger = get_logger(component="execute")

_RequiredColumns = Mapping[str, Iterable[str] | projection_planner.AllExceptColumns]


class WalkPurpose(StrEnum):
    """Why a walk runs, which fixes how it plans and shapes its frames."""

    SINK = "sink"
    """Hand each node's lazy frame to a sink: project every edge to its planned
    demand, plan the execution strategy, prepare inputs, build write recipes."""


@dataclass(frozen=True, slots=True)
class CollectPolicy:
    """What a walk collects, and how it treats each node's frame."""

    purpose: WalkPurpose = WalkPurpose.SINK

    @classmethod
    def sink(cls) -> CollectPolicy:
        """Collect nothing: the caller sinks or collects the lazy frames itself."""
        return cls(purpose=WalkPurpose.SINK)


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


@dataclass(frozen=True, slots=True)
class WalkResult:
    """Everything one walk produced."""

    frames: dict[str, _Frame]
    """Each built node's frame as its consumers read it (a frame bundle for a
    multi-frame source); a sink walk drops a frame once its consumers ran."""
    order: list[str] = field(default_factory=list)
    parents_of: dict[str, list[str]] = field(default_factory=dict)
    id_to_name: dict[str, str] = field(default_factory=dict)
    join_recipes: dict[str, JoinRecipe] = field(default_factory=dict)
    write_recipes: dict[str, WriteRecipe] = field(default_factory=dict)
    unshaped_frames: dict[str, pl.LazyFrame] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _WalkProjection:
    """The column demand a walk plans before building anything."""

    needed_by_node: Mapping[str, frozenset[str] | None]
    edge_demands: Mapping[projection_planner.ProjectionEdgeKey, frozenset[str] | None]
    runtime_plan: projection_planner.ProjectionPlan
    """Demand of the run itself, narrower than the plan's when a seed plan
    negotiated a broader capture demand."""
    builder_needed: Mapping[str, frozenset[str] | None]
    api_port_columns: Mapping[str, Mapping[str, frozenset[str] | None]]
    strategy_required: Mapping[str, set[str] | projection_planner.AllExceptColumns]
    boundary_operators: Mapping[str, str]
    broadened: bool


@dataclass(slots=True)
class _NodeFrame:
    """A node's frame before any capture, with what the walk learnt building it."""

    frame: _Frame
    boundary: NodeBoundary


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


class _Walk:
    """One walk's state; each method is one step of the walk."""

    def __init__(self, request: WalkRequest, policy: CollectPolicy) -> None:
        self.request = request
        self.policy = policy
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
        self.profile = (
            self.context.profile if self.context is not None else ExecutionProfile.LAZY_SINK
        )
        self.frames: dict[str, _Frame] = {}
        self.seed_frames: dict[str, _Frame] = {}
        self.column_cache: dict[tuple[str, str | None], frozenset[str]] = {}
        self.file_backed: set[str] = set()
        self.remaining = dict(self.prepared.children_count)
        self.join_recipes: dict[str, JoinRecipe] = {}
        self.write_recipes: dict[str, WriteRecipe] = {}
        self.unshaped_frames: dict[str, pl.LazyFrame] = {}
        self.prebuilt: dict[str, _NodeFrame] = {}
        self.materialisations_since_gc = 0
        self.preserved = request.preserve_node_ids | frozenset(
            self.decision.consumed_node_ids if self.decision is not None else ()
        )
        self.run_order = self._run_order()

    # ------------------------------------------------------------------ run

    def run(self) -> WalkResult:
        self._check_plan()
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
        self._bind_plan_sources()
        for node_id in self.run_order:
            self._walk_node(node_id)
        return WalkResult(
            frames=self.frames,
            order=self.order,
            parents_of=self.parents_of,
            id_to_name=self.graph_plan.id_to_name,
            join_recipes=self.join_recipes,
            write_recipes=self.write_recipes,
            unshaped_frames=self.unshaped_frames,
        )

    def _run_order(self) -> list[str]:
        """The prepared order, restricted under a plan to its seeds and executed nodes."""
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
            self.file_backed.add(node_id)
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

    def _plan_projection(self) -> _WalkProjection:
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
        strategy = self._plan_strategy(planning_required, planned_ids, operators)
        self.strategy = strategy
        plan = strategy.projection_plan
        normalised = self.prepared.normalised_required_columns
        broadened = planning_required != normalised
        runtime_plan = (
            projection_planner.compute_prepared_plan(
                self.order,
                self.prepared.children_of,
                self.node_map,
                normalised,
                relevant_edges=self.graph_plan.relevant_edges,
                submodels=self.graph.submodels,
                selector_aliases=self.selector_aliases,
            )
            if broadened
            else plan
        )
        return _WalkProjection(
            needed_by_node=plan.needed_by_node,
            edge_demands=plan.edge_demands,
            runtime_plan=runtime_plan,
            builder_needed=self._builder_demand(plan.needed_by_node, broadened=broadened),
            api_port_columns=projection_planner.api_input_port_columns_by_node(
                self.node_map, self.graph_plan.relevant_edges, plan
            ),
            strategy_required=planning_required,
            boundary_operators=operators,
            broadened=broadened,
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
        with self._stage("lazy_build_functions"):
            funcs = _build_funcs(
                build_order,
                self.node_map,
                self.graph_plan.id_to_name,
                self.prepared.all_parents,
                request.build_node_fn,
                incoming_edges_by_target=self.prepared.incoming_edges_by_target,
                all_incoming_edges_by_target=self.prepared.all_incoming_edges_by_target,
                all_node_map=self.graph.node_map,
                row_limit=None,
                preamble_ns=request.preamble_ns,
                source=request.source,
                source_by_node=request.source_by_node,
                required_output_columns_by_node=self.projection.builder_needed,
                required_output_columns_by_port_by_node=self.projection.api_port_columns,
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
        self, name: str, node_id: str | None = None
    ) -> contextlib.AbstractContextManager[Any]:
        if self.context is None:
            return contextlib.nullcontext()
        return self.context.stage(name, node_id=node_id)

    def _bind_plan_sources(self) -> None:
        """Under a plan, build every source, then prove the inputs are the planned ones.

        Nothing is collected or captured before the runtime inputs are
        checked against the fingerprint the plan was resolved with.
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
            self.prebuilt[node_id] = self._build_node(node_id, self.boundaries.open(node_id))
        self.captures.verify_inputs()

    def _walk_node(self, node_id: str) -> None:
        if self.decision is not None and node_id in self.decision.pass_through_edges:
            self._pass_through(node_id, self.decision.pass_through_edges[node_id])
            return
        seed = self.seed_frames.get(node_id)
        if seed is not None:
            self._read_seed(node_id, seed)
            return
        built = self.prebuilt.pop(node_id, None)
        scored: NodeSnapshotArtifact | None = None
        scored_write: tuple[bool, str | None] = (False, None)
        if built is None:
            boundary = self.boundaries.open(node_id)
            scored = self._stage_scored_capture(node_id)
            built, scored_write = self._build_scored_node(node_id, boundary, scored)
        frame = built.frame
        if self.decision is not None:
            frame = self._capture(node_id, frame, scored, scored_write)
        self.frames[node_id] = frame

    def _read_seed(self, node_id: str, seed: _Frame) -> None:
        """A seed's frame is its leased generation: nothing is built or checked for it."""
        self.frames[node_id] = seed
        self.column_cache[(node_id, None)] = _columns_of(seed)
        logger.info("lazy_seed_hit", node_id=node_id)
        if self.context is not None:
            self.context.checkpoint(label="lazy_seed_hit", node_id=node_id)

    def _stage_scored_capture(self, node_id: str) -> NodeSnapshotArtifact | None:
        """A batch Model Score whose output is its scored file writes it into its capture."""
        if self.decision is None:
            return None
        scenario = self.request.source_by_node.get(node_id, self.request.source or "live")
        return self.captures.stage_scored_output(node_id, self.node_map[node_id], scenario=scenario)

    def _build_scored_node(
        self,
        node_id: str,
        boundary: NodeBoundary,
        scored: NodeSnapshotArtifact | None,
    ) -> tuple[_NodeFrame, tuple[bool, str | None]]:
        if scored is None:
            return self._build_node(node_id, boundary), (False, None)
        from haute._model_scorer import model_score_output_destination

        try:
            with model_score_output_destination(scored.part_path(0)) as destination:
                built = self._build_node(node_id, boundary)
        except BaseException:
            scored.close()
            raise
        return built, (destination.used, destination.digest)

    def _build_node(self, node_id: str, boundary: NodeBoundary) -> _NodeFrame:
        """Invoke one node and shape and check its output, inside its build stage."""
        with self._stage("lazy_build", node_id):
            if boundary.is_source:
                frame = self.boundaries.invoke(boundary)
            else:
                frame = self.boundaries.invoke(boundary, self._node_inputs(boundary))
            return _NodeFrame(frame=self._shape_output(boundary, frame), boundary=boundary)

    def _node_inputs(self, boundary: NodeBoundary) -> list[_Frame]:
        """Route, project and contract-check the frames a node receives."""
        node_id = boundary.node_id
        missing = [parent for parent in boundary.parent_ids if parent not in self.frames]
        if missing:
            raise ValueError(
                f"Node '{node_id}' is missing input(s) from: {missing}. "
                "Upstream node(s) may have failed or not been registered."
            )
        inputs = self.boundaries.input_frames(boundary, self.frames)
        if not inputs:
            raise ValueError(f"No input data available for node '{node_id}'")
        runtime_demands = self._runtime_demands(boundary, inputs)
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
        if demands and self.context is not None:
            self._refine_strategy(demands)
        return demands

    def _refine_strategy(
        self, demands: Mapping[projection_planner.ProjectionEdgeKey, set[str]]
    ) -> None:
        """Record runtime-proven edge demands on the execution's strategy diagnostic."""
        assert self.context is not None
        previous = self.strategy
        refined_plan = projection_planner.with_runtime_inferred_streaming_edges(
            previous.projection_plan,
            demands_by_edge=demands,
            resolved_parent_ids=_runtime_projectable_source_ids(demands, self.node_map),
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
        if demand is None:
            demand = self.projection.edge_demands.get(
                projection_planner.ProjectionEdgeKey.from_edge(edge)
            )
        if demand is None:
            return frame, None
        lazy_frame = frame if isinstance(frame, pl.LazyFrame) else frame.lazy()
        schema_columns = lazy_frame.collect_schema().names()
        missing = set(demand) - set(schema_columns)
        required = (
            set(demand)
            if runtime_demand is not None
            else set(self.projection.runtime_plan.demand_for_edge(edge) or ())
        )
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

    def _record_recipes(self, boundary: NodeBoundary, inputs: Sequence[_Frame]) -> None:
        """The recipes a full write of this node can be chunked by."""
        node = boundary.node
        join = _edge_join_recipe(boundary.fn, node, inputs)
        if join is not None:
            self.join_recipes[boundary.node_id] = join
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

    def _shape_output(self, boundary: NodeBoundary, frame: Any) -> _Frame:
        """Apply the node's own column selection and renames, then its output contract."""
        node_id = boundary.node_id
        if isinstance(frame, pl.DataFrame):
            if self.context is not None:
                self.context.record_column_widths(node_id=node_id, output_width=frame.width)
            frame = frame.lazy()
        if isinstance(frame, dict):
            # A multi-frame source's bundle: consumers pick a frame per edge,
            # so the per-frame shaping and checks apply to what they pick.
            for port, port_frame in frame.items():
                self.column_cache[(node_id, port)] = _columns_of(port_frame)
            return frame  # type: ignore[return-value]
        config = boundary.node.data.config
        if _shapes_output(boundary.node):
            # Its columns before its own selection and renames, which a
            # snapshot records so a seeded preview can still report them.
            self.unshaped_frames[node_id] = frame
        shaped = _lazy(_apply_column_renames(_lazy(_apply_selected_columns(frame, config)), config))
        contract = boundary.contract
        if (
            boundary.check_contract
            and contract is not None
            and contract.outputs is not None
            and not boundary.is_passthrough_runtime
        ):
            columns = _columns_of(shaped)
            self.column_cache[(node_id, None)] = columns
            if self.context is not None:
                self.context.record_column_widths(node_id=node_id, output_width=len(columns))
            self.boundaries.assert_outputs(boundary, columns)
        return shaped

    def _pass_through(self, node_id: str, edge: GraphEdge) -> None:
        """A pass-through node is its selected input: its builder is never called."""
        if self.context is not None:
            self.context.checkpoint(label="before_node", node_id=node_id)
        selected = select_edge_source_output(self.frames[edge.source], edge)
        self._pass_through_recipe(node_id, edge, selected)
        config = self.node_map[node_id].data.config
        projected, _columns = self._project_edge(edge, selected)
        shaped = _apply_column_renames(_apply_selected_columns(projected, config), config)
        self.frames[node_id] = _lazy(shaped)
        self.captures.record_closure(node_id)
        self._release_consumed_parents(node_id)

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

        The identity test is the multi-frame guard: ``select_edge_source_output``
        returns the parent's own object for a single-frame parent and a
        different one for a sub-frame the parent's recipe does not describe.
        The second is the replacement test: every site that replaces a
        parent's frame after a capture or a seed records it as file-backed.
        """
        if selected is self.frames[edge.source] and edge.source not in self.file_backed:
            return True
        try:
            check_recipe_equivalence(parent_recipe, _lazy(selected))
        except RecipeEquivalenceError:
            return False
        return True

    def _capture(
        self,
        node_id: str,
        frame: _Frame,
        scored: NodeSnapshotArtifact | None,
        scored_write: tuple[bool, str | None],
    ) -> _Frame:
        """Record the node's closure and, at a capture point, sink it and continue from it."""
        assert self.decision is not None
        closure = self.captures.record_closure(node_id)
        if node_id not in self.decision.captures:
            return frame
        prewritten, digest = scored_write
        unshaped = self.unshaped_frames.get(node_id)
        captured = self.captures.capture(
            node_id,
            frame,
            closure,
            artifact=scored,
            prewritten=prewritten,
            prewritten_digest=digest,
            join=self.join_recipes.get(node_id),
            recipe=self.write_recipes.get(node_id),
            unshaped_columns=_schema_pairs(unshaped) if unshaped is not None else None,
        )
        self.file_backed.add(node_id)
        self.column_cache[(node_id, None)] = _columns_of(captured)
        self._release_consumed_parents(node_id)
        self.materialisations_since_gc += 1
        if self.materialisations_since_gc >= _GC_BATCH_INTERVAL:
            gc.collect()
            _malloc_trim()
            self.materialisations_since_gc = 0
        return captured

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


def _lazy(frame: pl.LazyFrame | pl.DataFrame) -> pl.LazyFrame:
    return frame.lazy() if isinstance(frame, pl.DataFrame) else frame


def _columns_of(frame: pl.LazyFrame | pl.DataFrame) -> frozenset[str]:
    lazy_frame = frame if isinstance(frame, pl.LazyFrame) else frame.lazy()
    return frozenset(lazy_frame.collect_schema().names())


__all__ = [
    "CollectPolicy",
    "WalkPurpose",
    "WalkRequest",
    "WalkResult",
    "walk_graph",
]
