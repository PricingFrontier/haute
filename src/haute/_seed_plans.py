"""Seed plans: what a bounded execution reads from and writes to shared snapshots.

A bounded execution (training preparation, optimiser setup, a Data Output run,
an explicit cache build) resolves one seed plan before it builds anything. The
plan names:

- **seeds** — node outputs whose fresh snapshot generation already holds every
  column the run needs there. The run reads them instead of computing them, and
  never builds a node it needs only for a seed;
- **captures** — the full-data materialisations the run still performs (joins,
  fan-outs, join feeders, materialising operations, batch Model Score output,
  and the producers the caller reads). The run writes each into the shared
  store and continues from what it wrote.

Seeds and captures are resolved together, before projection planning, so a run
that widens a capture also propagates the widened demand to every seed and
source above it, and one execution publishes the complete column union.

The walk follows *effective* edges. A pass-through node (Data Output,
modelling, Optimiser, submodel boundaries) is its selected input — one edge,
chosen by the node's own input selection — so its other inputs are never built
for it and it is never captured itself: its producer is.

A plan is a context manager. It leases every seed generation for its whole
life, owns every publication and staged artifact the run registers, and hands
itself to a spawned worker as ``(identity, generation_id)`` pairs.
"""

from __future__ import annotations

import contextlib
import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

import polars as pl

import haute.projection as projection_planner
from haute._builders import (
    PASS_THROUGH_NODE_TYPES,
    pass_through_selected_edge,
    resolve_instance_nodes,
)
from haute._cache import GraphFingerprintMemo, canonical_json, graph_fingerprint
from haute._data_points import DataPoint, DataPointResolver, point_kind
from haute._execution_context import ExecutionContext, ExecutionProfile
from haute._logging import get_logger
from haute._node_snapshots import (
    BOUNDED_SEMANTICS_CLASS,
    NodeSnapshotArtifact,
    NodeSnapshotColumns,
    NodeSnapshotGeneration,
    NodeSnapshotPublication,
    NodeSnapshotStore,
    snapshot_read_classes,
    snapshot_write_class,
)
from haute._source_cache import (
    SourceCacheGeneration,
    SourceCacheGenerationMissingError,
    SourceCacheIdentity,
    new_staging_token,
)
from haute._types import GraphEdge, GraphNode, NodeType, PipelineGraph

if TYPE_CHECKING:
    from haute._execute_lazy import PreparedExecution

logger = get_logger(component="seed_plans")

SEED_PLAN_FINGERPRINT_VERSION = 1
_LEASE_ATTEMPTS = 3
_PREVIEW_PREPARATION_ROUNDS = 3

AllExcept = projection_planner.AllExcept
Demand = frozenset[str] | None
"""A projection demand at one node: a column set, or ``None`` for all columns."""


class CaptureKind(StrEnum):
    """Why a node's output is captured."""

    STRUCTURAL = "structural"
    MATERIALISING = "materialising"
    MODEL_SCORE = "model_score"
    CONSUMED = "consumed"


@dataclass(frozen=True, slots=True)
class SeedPlanRequest:
    """One bounded execution, described before anything is built.

    ``target_node_id`` is the lineage target the lazy engine runs.
    ``consumed_node_ids`` (default: the target) are every node the caller reads
    afterwards, whatever their kind; they are always built or seeded, and
    capture eligibility is applied to them separately. ``capture_columns_by_node``
    is a best-effort extra demand for a capture. ``build_node_id`` is an explicit
    build's node: never seeded and never an automatic capture.
    ``best_effort_demand`` marks the caller's demand as a hint — a preview's
    requested columns, which may name a column the node no longer produces —
    so every capture writes what the node produces of it instead of failing.
    """

    graph: PipelineGraph
    target_node_id: str
    source: str
    profile: ExecutionProfile
    consumed_node_ids: tuple[str, ...] = ()
    required_columns_by_node: Mapping[str, Iterable[str] | AllExcept] | None = None
    capture_columns_by_node: Mapping[str, Iterable[str]] | None = None
    source_by_node: Mapping[str, str] | None = None
    refresh: bool = False
    build_node_id: str | None = None
    best_effort_demand: bool = False


@dataclass(frozen=True, slots=True)
class SeedDecision:
    """A node output the run reads from a snapshot generation."""

    node_id: str
    identity: SourceCacheIdentity
    generation_id: str
    columns: NodeSnapshotColumns
    demand: NodeSnapshotColumns
    dependencies: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class CaptureDecision:
    """A node output the run computes and writes to the shared store.

    ``columns`` is the negotiated planning demand the capture writes;
    ``strict_columns`` is what the run itself needs there. A negotiated column
    beyond the strict set that the node does not produce is best-effort.
    """

    node_id: str
    identity: SourceCacheIdentity
    kind: CaptureKind
    columns: NodeSnapshotColumns
    strict_columns: NodeSnapshotColumns


@dataclass(frozen=True, slots=True)
class SeedPlanDecision:
    """Everything the engine needs to execute under a plan. Pickle-safe."""

    target_node_id: str
    consumed_node_ids: tuple[str, ...]
    source: str
    profile: ExecutionProfile
    seeds: Mapping[str, SeedDecision]
    captures: Mapping[str, CaptureDecision]
    executed_node_ids: frozenset[str]
    pass_through_edges: Mapping[str, GraphEdge]
    planning_required_columns: Mapping[str, frozenset[str] | AllExcept]
    lineage_fingerprint: str
    runtime_input_fingerprint: str
    fingerprint: str

    @property
    def generations(self) -> tuple[tuple[SourceCacheIdentity, str], ...]:
        """The seed ``(identity, generation_id)`` pairs, in node order."""
        return tuple(
            (seed.identity, seed.generation_id) for _node, seed in sorted(self.seeds.items())
        )


@dataclass(frozen=True, slots=True)
class SharedSnapshotSeedRecord:
    """Execution evidence: one node output read from a shared snapshot."""

    node_id: str
    identity_digest: str
    generation_id: str
    columns: NodeSnapshotColumns

    def to_dict(self) -> dict[str, object]:
        return {
            "node_id": self.node_id,
            "identity_digest": self.identity_digest,
            "generation_id": self.generation_id,
            "columns": self.columns.to_json(),
        }


CaptureOutcome = Literal["published", "superseded", "quota"]


@dataclass(frozen=True, slots=True)
class SharedSnapshotCaptureRecord:
    """Execution evidence: one full-data materialisation written to shared snapshots."""

    node_id: str
    identity_digest: str
    kind: CaptureKind
    outcome: CaptureOutcome
    generation_id: str | None
    columns: NodeSnapshotColumns
    # How the output was written (``haute._chunked_writes``), or
    # ``prewritten`` for a batch Model Score's own scored file, with the part
    # files it wrote and the inputs it had to stage first.
    write_strategy: str | None = None
    write_parts: int | None = None
    write_staged_inputs: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "node_id": self.node_id,
            "identity_digest": self.identity_digest,
            "kind": self.kind.value,
            "outcome": self.outcome,
            "generation_id": self.generation_id,
            "columns": self.columns.to_json(),
            "write_strategy": self.write_strategy,
            "write_parts": self.write_parts,
            "write_staged_inputs": self.write_staged_inputs,
        }


# ---------------------------------------------------------------------------
# Column helpers
# ---------------------------------------------------------------------------


def demand_columns(demand: Demand) -> NodeSnapshotColumns:
    """Return a projection demand as a snapshot column set."""
    return NodeSnapshotColumns.all() if demand is None else NodeSnapshotColumns.of(demand)


def _merge_required(
    existing: set[str] | AllExcept | None,
    demand: NodeSnapshotColumns,
) -> frozenset[str] | AllExcept:
    """Widen a node's required columns to cover *demand* (all columns: ``AllExcept()``).

    A caller's ``AllExcept`` demand is unresolved until a schema exists, so it
    plans as all columns and its capture demand is all columns too.
    """
    if demand.names is None or isinstance(existing, AllExcept):
        return AllExcept()
    return frozenset(existing or ()) | demand.names


# ---------------------------------------------------------------------------
# Fingerprints
# ---------------------------------------------------------------------------


def _canonical_graph(graph: PipelineGraph) -> PipelineGraph:
    from haute._builders import resolve_instance_nodes
    from haute.execution import canonical_dataframe_execution_graph

    return canonical_dataframe_execution_graph(resolve_instance_nodes(graph))


def seed_plan_lineage_fingerprint(
    graph: PipelineGraph,
    target_node_id: str,
    *,
    memo: GraphFingerprintMemo | None = None,
) -> str:
    """The graph identity a plan was resolved for: the target's upstream subgraph."""
    from haute._dataframe_execution_cache import _upstream_subgraph

    return graph_fingerprint(_upstream_subgraph(_canonical_graph(graph), target_node_id), memo=memo)


def seed_plan_input_fingerprint(
    graph: PipelineGraph,
    executed_node_ids: Iterable[str],
    *,
    source: str,
) -> str:
    """Runtime-input fingerprint of exactly the nodes a planned run builds.

    Seeds are excluded: their data is fixed by the generation the plan leases,
    so a later change to their inputs does not change what the run reads.
    """
    from haute.execution import dataframe_graph_input_fingerprint

    canonical = _canonical_graph(graph)
    executed = frozenset(executed_node_ids)
    subgraph = canonical.model_copy(
        update={
            "nodes": [node for node in canonical.nodes if node.id in executed],
            "edges": [
                edge
                for edge in canonical.edges
                if edge.source in executed and edge.target in executed
            ],
        }
    )
    return dataframe_graph_input_fingerprint(subgraph, target_node_id=None, source=source)


def seed_plan_fingerprint(seeds: Mapping[str, SeedDecision]) -> str:
    """``seed identity → generation_id`` over seeds only, order-independent."""
    pairs = sorted((seed.identity.digest, seed.generation_id) for seed in seeds.values())
    payload = {"schema_version": SEED_PLAN_FINGERPRINT_VERSION, "seeds": pairs}
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return f"seed-plan:v{SEED_PLAN_FINGERPRINT_VERSION}:{digest}"


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Round:
    seeds: dict[str, SeedDecision]
    executed: set[str]
    captures: dict[str, CaptureKind]
    required: dict[str, frozenset[str] | AllExcept]
    needed: Mapping[str, Demand]


class _Resolver:
    """One resolution of one request against one store."""

    def __init__(self, request: SeedPlanRequest, store: NodeSnapshotStore) -> None:
        from haute._execute_lazy import PreparedExecutionRequest, _prepare_execution

        # A preview request is only ever built for a lineage that
        # ``preview_lineage_admitted`` accepted.
        if BOUNDED_SEMANTICS_CLASS not in snapshot_read_classes(request.profile) or (
            snapshot_write_class(request.profile, preview_admitted=True) != BOUNDED_SEMANTICS_CLASS
        ):
            raise ValueError(
                f"Execution profile {ExecutionProfile(request.profile).value!r} "
                "neither reads nor writes shared snapshots"
            )
        self.request = request
        self.preview = ExecutionProfile(request.profile) == ExecutionProfile.PREVIEW_EAGER
        self.store = store
        self.points = DataPointResolver(request.graph, source=request.source, store=store)
        self.prepared: PreparedExecution = _prepare_execution(
            PreparedExecutionRequest(
                graph=request.graph,
                target_node_id=request.target_node_id,
                source=request.source,
                required_columns_by_node=request.required_columns_by_node,
                profile=ExecutionProfile(request.profile),
            )
        )
        plan = self.prepared.graph_plan
        self.order: list[str] = list(plan.order)
        self.node_map: Mapping[str, GraphNode] = self.points.graph.node_map
        consumed = request.consumed_node_ids or (request.target_node_id,)
        unknown = sorted(node_id for node_id in consumed if node_id not in self.order)
        if unknown:
            raise ValueError(f"Consumed nodes are not in the target's lineage: {unknown}")
        if request.build_node_id is not None and request.build_node_id not in self.order:
            raise ValueError("The explicit build's node is not in the target's lineage")
        self.consumed: tuple[str, ...] = tuple(dict.fromkeys(consumed))
        self.pass_through_edges: dict[str, GraphEdge] = {}
        for node_id in self.order:
            edge = pass_through_selected_edge(
                self.node_map[node_id],
                list(self.prepared.incoming_edges_by_target.get(node_id, ())),
                self.node_map,
                submodels=plan.submodels,
            )
            if edge is not None:
                self.pass_through_edges[node_id] = edge
        # Instance nodes run their original's config; what a node does is read
        # from that, while projection stays on the graph the engine plans.
        self.effective_node_map: Mapping[str, GraphNode] = resolve_instance_nodes(
            self.prepared.graph
        ).node_map
        self.materialising = projection_planner.materialising_operators_by_node(
            self.order,
            self.effective_node_map,
            relevant_edges=plan.relevant_edges,
            submodels=plan.submodels,
        )
        self.caller_required = projection_planner.normalise_required_columns_by_node(
            request.required_columns_by_node, self.order
        )
        self.capture_columns: dict[str, frozenset[str]] = {
            node_id: frozenset(columns)
            for node_id, columns in (request.capture_columns_by_node or {}).items()
        }
        self._identities: dict[str, SourceCacheIdentity] = {}
        self._kinds: dict[str, str] = {}

    # ------------------------------------------------------------- facts

    def effective_edges(self, node_id: str) -> tuple[GraphEdge, ...]:
        edge = self.pass_through_edges.get(node_id)
        if edge is not None:
            return (edge,)
        return tuple(self.prepared.incoming_edges_by_target.get(node_id, ()))

    def is_node_output(self, node_id: str) -> bool:
        """Whether *node_id*'s output is a node-output snapshot point.

        An API input is never one, even read without a port: its tables live in
        the JSON cache, and a multi-port bundle is not one frame.
        """
        kind = self._kinds.get(node_id)
        if kind is None:
            if self.node_map[node_id].data.nodeType == NodeType.API_INPUT:
                kind = "api_input"
            else:
                kind = point_kind(self.points.graph, DataPoint(node_id, None))
            self._kinds[node_id] = kind
        return kind == "node_output"

    def identity(self, node_id: str) -> SourceCacheIdentity:
        identity = self._identities.get(node_id)
        if identity is None:
            identity = self.points.node_output_slot(node_id).identity(
                self.points.node_output_signature(node_id)
            )
            self._identities[node_id] = identity
        return identity

    def plan(self, required: Mapping[str, frozenset[str] | AllExcept]) -> Mapping[str, Demand]:
        """The engine's projection plan for *required*, exactly as it computes it."""
        from haute._polars_selectors import preamble_selector_aliases

        prepared = self.prepared
        graph_plan = prepared.graph_plan
        projection = projection_planner.compute_prepared_plan(
            self.order,
            {node_id: list(children) for node_id, children in prepared.children_of.items()},
            graph_plan.node_map,
            required_columns_by_node=dict(required),
            relevant_edges=graph_plan.relevant_edges,
            submodels=graph_plan.submodels,
            selector_aliases=preamble_selector_aliases(prepared.graph.preamble or ""),
        )
        projection = projection_planner.with_api_input_port_projection_boundaries(
            projection, graph_plan.node_map, graph_plan.relevant_edges
        )
        return {
            node_id: (None if demand is None else frozenset(demand))
            for node_id, demand in projection.needed_by_node.items()
        }

    def producer(self, node_id: str) -> tuple[str, str | None]:
        """The point a consumed node's data is: through pass-throughs to its producer."""
        point: tuple[str, str | None] = (node_id, None)
        seen: set[str] = set()
        while point[0] in self.pass_through_edges and point[0] not in seen:
            seen.add(point[0])
            edge = self.pass_through_edges[point[0]]
            point = (edge.source, edge.sourceHandle)
        return point

    def batch_model_score(self, node_id: str) -> bool:
        node = self.node_map[node_id]
        if node.data.nodeType != NodeType.MODEL_SCORE:
            return False
        scenario = (self.request.source_by_node or {}).get(node_id, self.request.source or "live")
        return scenario != "live"

    # ------------------------------------------------------------- walk

    def shapes_output(self, node_id: str) -> bool:
        """Whether the node's own config selects or renames its output columns."""
        return any(
            bool(config.get("selected_columns")) or bool(config.get("column_renames"))
            for config in (
                self.node_map[node_id].data.config,
                self.effective_node_map[node_id].data.config,
            )
            if isinstance(config, dict)
        )

    def seed_candidate(
        self,
        node_id: str,
        demand: Demand,
        *,
        dropped: set[str],
    ) -> SeedDecision | None:
        request = self.request
        if request.refresh or node_id == request.build_node_id or node_id in dropped:
            return None
        if not self.is_node_output(node_id):
            return None
        identity = self.identity(node_id)
        latest = self.store.latest_generation(identity)
        if latest is None or not latest.fresh:
            return None
        if self.preview and self.shapes_output(node_id) and latest.unshaped_columns is None:
            # A preview reports a node's columns before its own selection and
            # renames — what its Columns editor offers. A generation that did
            # not record them cannot say that, so the preview computes the
            # node (and may still capture it for everything below).
            return None
        wanted = demand_columns(demand)
        if not latest.columns.covers(wanted):
            return None
        return SeedDecision(
            node_id=node_id,
            identity=identity,
            generation_id=latest.generation_id,
            columns=latest.columns,
            demand=wanted,
            dependencies=dict(latest.dependencies),
        )

    def walk(
        self,
        needed: Mapping[str, Demand],
        *,
        dropped: set[str],
    ) -> tuple[dict[str, SeedDecision], set[str]]:
        """Seeds and the nodes still built, walking effective edges from every root."""
        seeds: dict[str, SeedDecision] = {}
        executed: set[str] = set()
        visited: set[str] = set()
        stack = [self.request.target_node_id, *self.consumed]
        while stack:
            node_id = stack.pop()
            if node_id in visited:
                continue
            visited.add(node_id)
            seed = self.seed_candidate(node_id, needed.get(node_id), dropped=dropped)
            if seed is not None:
                seeds[node_id] = seed
                continue
            executed.add(node_id)
            stack.extend(edge.source for edge in self.effective_edges(node_id))
        return seeds, executed

    def capture_points(self, executed: set[str]) -> dict[str, CaptureKind]:
        children: dict[str, set[str]] = {node_id: set() for node_id in executed}
        parents: dict[str, set[str]] = {}
        for node_id in executed:
            node_parents = {edge.source for edge in self.effective_edges(node_id)}
            parents[node_id] = node_parents
            for parent_id in node_parents:
                if parent_id in children:
                    children[parent_id].add(node_id)
        consumed_producers = {
            producer
            for producer, port in (self.producer(node_id) for node_id in self.consumed)
            if port is None
        }
        captures: dict[str, CaptureKind] = {}
        for node_id in self.order:
            if (
                node_id not in executed
                or node_id == self.request.build_node_id
                or self.node_map[node_id].data.nodeType in PASS_THROUGH_NODE_TYPES
                or not self.is_node_output(node_id)
            ):
                continue
            if self.preview:
                # A preview's row limit already stops every other read early;
                # only a join or a materialising operation reads its full input.
                edges = self.effective_edges(node_id)
                inputs = {(edge.source, edge.sourceHandle) for edge in edges}
                if node_id in self.materialising:
                    captures[node_id] = CaptureKind.MATERIALISING
                elif len(inputs) > 1:
                    captures[node_id] = CaptureKind.STRUCTURAL
                continue
            node_parents = parents[node_id]
            is_source = not self.prepared.incoming_edges_by_target.get(node_id)
            feeds_join = any(len(parents.get(child, ())) > 1 for child in children[node_id])
            structural = not is_source and (
                len(node_parents) > 1 or len(children[node_id]) > 1 or feeds_join
            )
            if node_id in consumed_producers:
                captures[node_id] = CaptureKind.CONSUMED
            elif self.batch_model_score(node_id):
                captures[node_id] = CaptureKind.MODEL_SCORE
            elif node_id in self.materialising:
                captures[node_id] = CaptureKind.MATERIALISING
            elif structural:
                captures[node_id] = CaptureKind.STRUCTURAL
        return captures

    # ------------------------------------------------------------- rounds

    def negotiate(
        self,
        needed0: Mapping[str, Demand],
        *,
        dropped: set[str],
    ) -> _Round:
        """Steps 1–4: seeds, captures, and column negotiation to a fixed point."""
        needed = needed0
        while True:
            seeds, executed = self.walk(needed, dropped=dropped)
            captures = self.capture_points(executed)
            required: dict[str, frozenset[str] | AllExcept] = {
                node_id: (demand if isinstance(demand, AllExcept) else frozenset(demand))
                for node_id, demand in self.caller_required.items()
            }
            for node_id in captures:
                demand = demand_columns(needed0.get(node_id))
                extra = self.capture_columns.get(node_id)
                if extra:
                    demand = demand.union(NodeSnapshotColumns.of(extra))
                latest = self.store.latest_generation(self.identity(node_id))
                if latest is not None:
                    demand = demand.union(latest.columns)
                existing = required.get(node_id)
                required[node_id] = _merge_required(
                    set(existing) if isinstance(existing, frozenset) else existing, demand
                )
            negotiated = self.plan(required)
            uncovered = {
                node_id
                for node_id, seed in seeds.items()
                if not seed.columns.covers(demand_columns(negotiated.get(node_id)))
            }
            if not uncovered:
                # The engine projects each seed to its demand, which is now the
                # negotiated one: a widened capture below needs those columns.
                seeds = {
                    node_id: replace(seed, demand=demand_columns(negotiated.get(node_id)))
                    for node_id, seed in seeds.items()
                }
                return _Round(seeds, executed, captures, required, negotiated)
            dropped |= uncovered
            needed = negotiated

    def ancestry(self, state: _Round) -> set[str]:
        """Step 5: every reader of a seed's recorded ancestor must read that generation.

        Returns the seeds to drop. Seeds recording different generations of one
        ancestor cannot be combined, nor can a seed with an ancestor seeded at a
        generation it did not record: generations are read one at a time, so an
        ancestor refreshed between two reads is a different generation even
        though each read found its node fresh. An ancestor the run still recomputes is one
        the walk could not seed — cleared, dropped, or not covering what its
        readers need — because a current, fresh, covering generation would
        have been seeded where the walk reached it; its recomputed readers
        would read other data than the seed was built from. An ancestor only
        the seed itself reads imposes nothing.
        """
        recorded: dict[str, dict[str, set[str]]] = {}
        for node_id, seed in state.seeds.items():
            for digest, generation_id in seed.dependencies.items():
                recorded.setdefault(digest, {}).setdefault(generation_id, set()).add(node_id)
        if not recorded:
            return set()
        node_by_digest = {
            self.identity(node_id).digest: node_id
            for node_id in self.order
            if self.is_node_output(node_id)
        }
        to_drop: set[str] = set()
        for digest, by_generation in recorded.items():
            recorders = set().union(*by_generation.values())
            ancestor = node_by_digest.get(digest)
            seeded = state.seeds.get(ancestor) if ancestor is not None else None
            if (
                len(by_generation) > 1
                or ancestor in state.executed
                or (seeded is not None and seeded.generation_id not in by_generation)
            ):
                to_drop |= recorders
        return to_drop

    def resolve(self) -> SeedPlanDecision:
        needed0 = self.plan(
            {
                node_id: (demand if isinstance(demand, AllExcept) else frozenset(demand))
                for node_id, demand in self.caller_required.items()
            }
        )
        dropped: set[str] = set()
        # Every round that does not finish drops at least one seed, and a
        # dropped seed is never re-added, so resolution ends.
        while True:
            state = self.negotiate(needed0, dropped=dropped)
            to_drop = self.ancestry(state)
            if not to_drop:
                return self.decision(state, needed0)
            dropped |= to_drop

    def decision(self, state: _Round, needed0: Mapping[str, Demand]) -> SeedPlanDecision:
        request = self.request
        captures = {
            node_id: CaptureDecision(
                node_id=node_id,
                identity=self.identity(node_id),
                kind=kind,
                columns=demand_columns(state.needed.get(node_id)),
                strict_columns=(
                    NodeSnapshotColumns.of(())
                    if request.best_effort_demand
                    else demand_columns(needed0.get(node_id))
                ),
            )
            for node_id, kind in state.captures.items()
        }
        pass_through_edges = {
            node_id: edge
            for node_id, edge in self.pass_through_edges.items()
            if node_id in state.executed
        }
        memo = GraphFingerprintMemo()
        return SeedPlanDecision(
            target_node_id=request.target_node_id,
            consumed_node_ids=self.consumed,
            source=request.source,
            profile=ExecutionProfile(request.profile),
            seeds=dict(state.seeds),
            captures=captures,
            executed_node_ids=frozenset(state.executed),
            pass_through_edges=pass_through_edges,
            planning_required_columns=dict(state.required),
            lineage_fingerprint=seed_plan_lineage_fingerprint(
                request.graph, request.target_node_id, memo=memo
            ),
            runtime_input_fingerprint=seed_plan_input_fingerprint(
                request.graph, state.executed, source=request.source
            ),
            fingerprint=seed_plan_fingerprint(state.seeds),
        )


class _ListedResolver(_Resolver):
    """Resolution over the generations a preview listed, for the trace that explains it.

    A listed generation is seeded where it covers the demand, whether or not it
    is still its slot's latest; nothing is captured. Ancestry agreement is the
    base resolver's, so a listed seed built from a point the trace recomputes
    is dropped with it.
    """

    def __init__(self, request: SeedPlanRequest, store: NodeSnapshotStore) -> None:
        super().__init__(request, store)
        self.listed: dict[str, NodeSnapshotGeneration] = {}

    def seed_candidate(
        self,
        node_id: str,
        demand: Demand,
        *,
        dropped: set[str],
    ) -> SeedDecision | None:
        described = self.listed.get(node_id)
        if described is None or node_id in dropped:
            return None
        wanted = demand_columns(demand)
        if not described.columns.covers(wanted):
            return None
        return SeedDecision(
            node_id=node_id,
            identity=described.identity,
            generation_id=described.generation_id,
            columns=described.columns,
            demand=wanted,
            dependencies=dict(described.dependencies),
        )

    def capture_points(self, executed: set[str]) -> dict[str, CaptureKind]:
        return {}


def resolve_seed_plan(request: SeedPlanRequest, *, store: NodeSnapshotStore) -> SeedPlanDecision:
    """Resolve which node outputs a run seeds and which it captures.

    Reads store metadata only: nothing is leased, built, or executed. A corrupt
    generation met on the way propagates as the store's error.
    """
    return _Resolver(request, store).resolve()


# ---------------------------------------------------------------------------
# Leased plans
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReadGeneration:
    """A snapshot generation an execution's frames were computed from.

    ``seeded``: the plan leased it and the execution read it instead of
    computing the node. ``captured``: the execution computed the node,
    published it, and everything below read the publication.
    """

    node_id: str
    identity: SourceCacheIdentity
    generation_id: str
    columns: NodeSnapshotColumns
    created_at: float
    kind: Literal["seeded", "captured"]


class _SeedMovedError(RuntimeError):
    """A seed stopped being current between resolution and its lease."""


@dataclass(frozen=True, slots=True)
class SeedPlanHandoff:
    """A plan as a spawned worker receives it: the decision and its staging token."""

    decision: SeedPlanDecision
    project_root: str
    staging_token: str


@dataclass(slots=True)
class SeedPlan:
    """A resolved plan whose seed generations are leased until it closes.

    The process that opened it owns the staging token: closing it discards any
    staging directory left under that token, so a supervising parent closes its
    plan only after its worker has exited. A worker's adopted plan never does.
    """

    decision: SeedPlanDecision
    store: NodeSnapshotStore
    staging_token: str
    owns_staging: bool
    _stack: contextlib.ExitStack = field(default_factory=contextlib.ExitStack)
    _closures: dict[str, dict[str, str]] = field(default_factory=dict)
    _generations: dict[str, SourceCacheGeneration] = field(default_factory=dict)
    _published: dict[str, NodeSnapshotGeneration] = field(default_factory=dict)
    _closed: bool = False

    def __enter__(self) -> SeedPlan:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @property
    def fingerprint(self) -> str:
        return self.decision.fingerprint

    def seed_unshaped_columns(self, node_id: str) -> tuple[tuple[str, str], ...] | None:
        """The seeded node's columns before its own selection and renames, if recorded."""
        from haute._node_snapshots import parse_unshaped_columns

        seed = self.decision.seeds[node_id]
        facts = self._generations[seed.identity.digest].metadata.node_output or {}
        return parse_unshaped_columns(facts.get("unshaped_columns"))

    def seed_frame(self, node_id: str) -> pl.LazyFrame:
        """The leased generation a seeded node reads, projected to its demand.

        An empty demand keeps one carrier column so the frame keeps its rows.
        """
        from haute._polars_utils import projected_or_carrier_columns

        seed = self.decision.seeds[node_id]
        frame = self._generations[seed.identity.digest].lazy_frame
        if seed.demand.names is None:
            return frame
        schema = frame.collect_schema().names()
        return frame.select(projected_or_carrier_columns(schema, seed.demand.names))

    def estimation_graph(self, graph: PipelineGraph) -> PipelineGraph:
        """*graph* with every seed read as the Parquet parts of its leased generation.

        Materialisation estimates then stop at a seed and read its row count
        and widths from the generation's metadata, instead of walking back
        through computation the run never performs. Projection is not planned
        on this graph: only estimates are.
        """
        seeds = self.decision.seeds
        nodes = [
            GraphNode(
                id=node.id,
                data=node.data.model_copy(
                    update={
                        "nodeType": NodeType.DATA_INPUT,
                        "config": {
                            "inputType": "file",
                            "format": "parquet",
                            "mode": "scan",
                            # Every part, so a seed's estimate counts all of
                            # its rows; this graph is only ever estimated.
                            "path": str(
                                self._generations[seeds[node.id].identity.digest].directory
                                / "part-*.parquet"
                            ),
                        },
                    }
                ),
            )
            if node.id in seeds
            else node
            for node in graph.nodes
        ]
        edges = [edge for edge in graph.edges if edge.target not in seeds]
        return graph.model_copy(update={"nodes": nodes, "edges": edges})

    def register_publication(self, publication: NodeSnapshotPublication) -> None:
        """Own one capture's publication until the plan closes."""
        self._stack.callback(publication.close)

    def register_artifact(self, artifact: NodeSnapshotArtifact) -> None:
        """Own one request-owned staged artifact until the plan closes."""
        self._stack.callback(artifact.close)

    def record_published(self, node_id: str, generation: NodeSnapshotGeneration) -> None:
        """Record the generation a capture published and its readers then read."""
        self._published[node_id] = generation

    def hold_generation(
        self, identity: SourceCacheIdentity, generation_id: str
    ) -> SourceCacheGeneration:
        """Lease one more generation until the plan closes."""
        return self._stack.enter_context(self.store.lease_generation(identity, generation_id))

    def read_generations(self, order: Iterable[str]) -> tuple[ReadGeneration, ...]:
        """Every generation this execution read, seeded or captured, in *order*.

        A capture that kept its own artifact — quota, or superseded — is not
        a generation anyone else can read, and is not listed.
        """
        reads: dict[str, ReadGeneration] = {}
        for node_id, seed in self.decision.seeds.items():
            described = self.store.describe_generation(
                seed.identity, self._generations[seed.identity.digest]
            )
            reads[node_id] = ReadGeneration(
                node_id=node_id,
                identity=seed.identity,
                generation_id=seed.generation_id,
                columns=described.columns,
                created_at=described.generation.metadata.created_at,
                kind="seeded",
            )
        for node_id, published in self._published.items():
            reads[node_id] = ReadGeneration(
                node_id=node_id,
                identity=published.identity,
                generation_id=published.generation_id,
                columns=published.columns,
                created_at=published.generation.metadata.created_at,
                kind="captured",
            )
        position = {node_id: index for index, node_id in enumerate(order)}
        return tuple(sorted(reads.values(), key=lambda read: position.get(read.node_id, -1)))

    def record_closure(self, node_id: str, dependencies: Mapping[str, str]) -> None:
        """Record the generations *node_id*'s frame was computed from."""
        self._closures[node_id] = dict(dependencies)

    def dependencies_for(self, node_id: str) -> dict[str, str]:
        """The recorded generations behind *node_id*'s frame in this run."""
        return dict(self._closures.get(node_id, {}))

    def handoff(self) -> SeedPlanHandoff:
        return SeedPlanHandoff(
            decision=self.decision,
            project_root=str(self.store.root),
            staging_token=self.staging_token,
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._stack.close()
        finally:
            if self.owns_staging:
                self.store.discard_node_output_staging(self.staging_token)

    @classmethod
    def _leased(
        cls,
        decision: SeedPlanDecision,
        store: NodeSnapshotStore,
        *,
        staging_token: str,
        owns_staging: bool,
        require_current: bool,
    ) -> SeedPlan:
        plan = cls(
            decision=decision,
            store=store,
            staging_token=staging_token,
            owns_staging=owns_staging,
        )
        try:
            for identity, generation_id in decision.generations:
                try:
                    plan._generations[identity.digest] = plan._stack.enter_context(
                        store.lease_generation(identity, generation_id)
                    )
                except SourceCacheGenerationMissingError:
                    if require_current:
                        raise _SeedMovedError(identity.digest) from None
                    raise
            if require_current:
                for identity, generation_id in decision.generations:
                    latest = store.latest_generation(identity)
                    if latest is None or latest.generation_id != generation_id or not latest.fresh:
                        raise _SeedMovedError(identity.digest)
        except BaseException:
            plan._stack.close()
            raise
        return plan

    @classmethod
    def adopt(cls, handoff: SeedPlanHandoff, store: NodeSnapshotStore | None = None) -> SeedPlan:
        """Lease, in a spawned worker, exactly the generations the parent leased."""
        return cls._leased(
            handoff.decision,
            store if store is not None else NodeSnapshotStore(handoff.project_root),
            staging_token=handoff.staging_token,
            owns_staging=False,
            require_current=False,
        )


def open_resolved_seed_plan(
    request: SeedPlanRequest,
    *,
    store: NodeSnapshotStore,
    staging_token: str | None = None,
) -> SeedPlan:
    """Resolve and lease; a seed that moved before its lease re-resolves.

    A caller that already owns a staging token (an explicit build names one so
    it can discard a killed worker's staging) passes it, and the plan's
    captures stage under the same token.
    """
    token = staging_token if staging_token is not None else new_staging_token()
    for attempt in range(_LEASE_ATTEMPTS):
        decision = resolve_seed_plan(request, store=store)
        try:
            return SeedPlan._leased(
                decision,
                store,
                staging_token=token,
                owns_staging=True,
                require_current=True,
            )
        except _SeedMovedError as exc:
            logger.info("seed_plan_seed_moved", identity_digest=str(exc), attempt=attempt + 1)
    raise SourceCacheGenerationMissingError(
        "A snapshot this run would read kept changing while the run started; run it again."
    )


def open_seed_plan(
    request: SeedPlanRequest,
    *,
    store: NodeSnapshotStore | None = None,
    execution_context: ExecutionContext | None = None,
    staging_token: str | None = None,
    deadline: float | None = None,
) -> SeedPlan:
    """Prepare the run's inputs, then resolve and lease its seed plan.

    A node's signature signs its snapshot-backed inputs' generations, so the
    plan is resolved only after automatic input preparation has published
    them. Only inputs the run can read are prepared: a branch reached solely
    through an unselected pass-through input is not. *deadline* is the run's
    monotonic deadline, which bounds that preparation.

    A preview prepares less: see :func:`_open_preview_seed_plan`.
    """
    from haute._input_preparation import preparation_base_dir, prepare_input_snapshots

    if store is None:
        store = _project_store()
    resolver = _Resolver(request, store)
    reachable = _readable_node_ids(resolver)
    if resolver.preview:
        return _open_preview_seed_plan(
            request,
            resolver,
            reachable,
            store=store,
            execution_context=execution_context,
            staging_token=staging_token,
            deadline=deadline,
        )
    prepare_input_snapshots(
        [node_id for node_id in resolver.order if node_id in reachable],
        resolver.effective_node_map,
        profile=ExecutionProfile(request.profile),
        execution_context=execution_context,
        base_dir=preparation_base_dir(resolver.prepared.graph),
        schema_only=False,
        deadline=deadline,
    )
    return open_resolved_seed_plan(request, store=store, staging_token=staging_token)


def _project_store() -> NodeSnapshotStore:
    from haute._sandbox import _get_project_root

    return NodeSnapshotStore(_get_project_root())


def _readable_node_ids(resolver: _Resolver) -> set[str]:
    """Every node the request can read: its lineage along effective edges."""
    reachable: set[str] = set()
    stack = [resolver.request.target_node_id, *resolver.consumed]
    while stack:
        node_id = stack.pop()
        if node_id in reachable:
            continue
        reachable.add(node_id)
        stack.extend(edge.source for edge in resolver.effective_edges(node_id))
    return reachable


def _snapshot_backed_input_ids(resolver: _Resolver, readable: set[str]) -> list[str]:
    """The snapshot-backed Data Inputs among *readable*, in execution order."""
    from haute._input_preparation import _snapshot_backed_data_inputs

    return [
        node_id
        for node_id, _config in _snapshot_backed_data_inputs(
            [node_id for node_id in resolver.order if node_id in readable],
            resolver.effective_node_map,
        )
    ]


def _open_preview_seed_plan(
    request: SeedPlanRequest,
    resolver: _Resolver,
    readable: set[str],
    *,
    store: NodeSnapshotStore,
    execution_context: ExecutionContext | None,
    staging_token: str | None,
    deadline: float | None,
) -> SeedPlan:
    """Resolve first, then prepare only the inputs the preview's execution reads.

    A node's signature signs each snapshot-backed input's generation pointer
    and current source signature, so a stale, missing, or cleared input already
    fails every seed below it before anything is prepared. Preparing an input
    can move its pointer, which changes the signatures below it, so the plan is
    resolved again after every preparation; after ``_PREVIEW_PREPARATION_ROUNDS``
    rounds every readable input is prepared, and the next resolution cannot
    read an unprepared one.
    """
    from haute._input_preparation import preparation_base_dir, prepare_input_snapshots

    snapshot_backed = _snapshot_backed_input_ids(resolver, readable)
    token = staging_token if staging_token is not None else new_staging_token()
    prepared: set[str] = set()
    rounds = 0
    while True:
        plan = open_resolved_seed_plan(request, store=store, staging_token=token)
        executed = plan.decision.executed_node_ids
        unprepared = [
            node_id
            for node_id in snapshot_backed
            if node_id in executed and node_id not in prepared
        ]
        if not unprepared:
            return plan
        plan.close()
        rounds += 1
        if rounds > _PREVIEW_PREPARATION_ROUNDS:
            unprepared = [node_id for node_id in snapshot_backed if node_id not in prepared]
        logger.info("preview_seed_plan_prepares_inputs", node_ids=unprepared, round=rounds)
        prepare_input_snapshots(
            unprepared,
            resolver.effective_node_map,
            profile=ExecutionProfile(request.profile),
            execution_context=execution_context,
            base_dir=preparation_base_dir(resolver.prepared.graph),
            schema_only=False,
            deadline=deadline,
        )
        prepared.update(unprepared)


# ---------------------------------------------------------------------------
# Previews
# ---------------------------------------------------------------------------


def preview_lineage_admitted(graph: PipelineGraph, target_node_id: str, *, source: str) -> bool:
    """Whether a preview of *target_node_id* may read and write shared snapshots.

    Decided per source of the target's lineage, before anything is prepared.
    A Data Input executes from a Parquet scan or from its prepared snapshot, and
    a structured (JSON, NDJSON, XML) API Input from its Parquet cache or a
    direct shred of its file, exactly as a bounded run reads them; an API Input
    reading a flat file is admitted when a schema-only bounded read of that file
    succeeds (for a CSV, its header, which must declare its dtypes).
    """
    from haute._execute_lazy import PreparedExecutionRequest, _prepare_execution

    canonical = _canonical_graph(graph)
    prepared = _prepare_execution(
        PreparedExecutionRequest(
            graph=canonical,
            target_node_id=target_node_id,
            source=source,
            profile=ExecutionProfile.PREVIEW_EAGER,
        )
    )
    node_map = prepared.graph_plan.node_map
    return all(
        _flat_file_api_input_is_bounded(node_map[node_id].data.config)
        for node_id in prepared.graph_plan.order
        if node_map[node_id].data.nodeType == NodeType.API_INPUT
    )


def _flat_file_api_input_is_bounded(config: Mapping[str, object]) -> bool:
    """A schema-only bounded read of a flat-file API Input's file.

    Only a bounded refusal decides admission. Any other failure — a missing
    file, a bad path — is the preview's own read's to report at the node, so
    the lineage is simply not admitted and the preview runs exactly as before.
    """
    from haute._api_input_schema import is_json_api_input_path
    from haute._builders import _configured_pipeline_dir
    from haute._node_apply import resolve_api_input_from_config
    from haute.errors import BoundedMemoryUnsupportedError, HauteError

    path = config.get("path")
    if isinstance(path, str) and is_json_api_input_path(path):
        return True
    try:
        frame = resolve_api_input_from_config(
            dict(config),
            base_dir=_configured_pipeline_dir(),
            profile=ExecutionProfile.LAZY_SINK.value,
        )
        if isinstance(frame, dict):
            for port_frame in frame.values():
                port_frame.collect_schema()
        else:
            frame.collect_schema()
    except BoundedMemoryUnsupportedError:
        return False
    except (OSError, ValueError, pl.exceptions.PolarsError, HauteError) as exc:
        logger.info(
            "preview_admission_probe_failed",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return False
    return True


def preview_input_node_ids(
    graph: PipelineGraph,
    target_node_id: str,
    *,
    source: str,
    required_columns_by_node: Mapping[str, Iterable[str] | AllExcept] | None = None,
    store: NodeSnapshotStore | None = None,
) -> tuple[str, ...]:
    """The inputs a preview's execution would read, for the browser to prepare.

    Snapshot-backed Data Inputs and structured API Inputs, in execution order.
    For an admitted lineage these are the ones the preview's first resolution
    executes, read without preparing or leasing anything; for any other
    lineage, every one the target can read. The answer is advisory: if seeds
    move before the preview runs, the preview prepares what it then reads.
    """
    from haute._api_input_schema import is_json_api_input_path

    request = SeedPlanRequest(
        graph=graph,
        target_node_id=target_node_id,
        source=source,
        profile=ExecutionProfile.PREVIEW_EAGER,
        required_columns_by_node=required_columns_by_node,
    )
    resolver = _Resolver(request, store if store is not None else _project_store())
    readable = _readable_node_ids(resolver)
    snapshot_backed = set(_snapshot_backed_input_ids(resolver, readable))
    node_map = resolver.effective_node_map
    candidates: list[str] = []
    for node_id in resolver.order:
        if node_id not in readable:
            continue
        config = node_map[node_id].data.config
        path = config.get("path")
        structured = (
            node_map[node_id].data.nodeType == NodeType.API_INPUT
            and isinstance(path, str)
            and is_json_api_input_path(path)
        )
        if node_id in snapshot_backed or structured:
            candidates.append(node_id)
    if not preview_lineage_admitted(graph, target_node_id, source=source):
        return tuple(candidates)
    executed = resolver.resolve().executed_node_ids
    return tuple(node_id for node_id in candidates if node_id in executed)


@dataclass(frozen=True, slots=True)
class ListedSeed:
    """One generation a preview read, as the trace that explains it names it."""

    node_id: str
    identity_digest: str
    generation_id: str


def open_listed_seed_plan(
    request: SeedPlanRequest,
    listed: Iterable[ListedSeed],
    *,
    store: NodeSnapshotStore | None = None,
    staging_token: str | None = None,
) -> SeedPlan:
    """Lease the generations a preview listed and seed the trace from them.

    Every listed generation is checked and leased first: a point no longer in
    the target's lineage, an identity the request's graph no longer produces
    there, or a generation that has been retired raises
    :class:`~haute.errors.SeedPlanExpiredError`, while corruption and any other
    storage failure propagate as the store's error. Only then is the plan
    resolved: a listed generation that does not cover the request's demand is
    not seeded, and a listed seed built from a point the request recomputes is
    dropped with it. The plan captures nothing. Every listed lease is held
    until the plan closes.
    """
    from haute.errors import SeedPlanExpiredError

    if store is None:
        store = _project_store()
    resolver = _ListedResolver(request, store)
    leases = contextlib.ExitStack()
    try:
        generations: dict[str, SourceCacheGeneration] = {}
        for entry in listed:
            node_id = entry.node_id
            if node_id in resolver.listed:
                raise ValueError(f"The seed plan lists node {node_id!r} twice")
            if node_id not in resolver.order or not resolver.is_node_output(node_id):
                raise SeedPlanExpiredError(node_id=node_id)
            identity = resolver.identity(node_id)
            if identity.digest != entry.identity_digest:
                raise SeedPlanExpiredError(node_id=node_id)
            try:
                generation = leases.enter_context(
                    store.lease_generation(identity, entry.generation_id)
                )
            except SourceCacheGenerationMissingError:
                raise SeedPlanExpiredError(node_id=node_id) from None
            resolver.listed[node_id] = store.describe_generation(identity, generation)
            generations[identity.digest] = generation
        decision = resolver.resolve()
        plan = SeedPlan(
            decision=decision,
            store=store,
            staging_token=staging_token if staging_token is not None else new_staging_token(),
            owns_staging=True,
        )
        for seed in decision.seeds.values():
            plan._generations[seed.identity.digest] = generations[seed.identity.digest]
        plan._stack.callback(leases.pop_all().close)
    except BaseException:
        leases.close()
        raise
    return plan
