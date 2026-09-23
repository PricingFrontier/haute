"""Lazy and eager graph execution — shared by executor, trace, and scorer."""

from __future__ import annotations

import contextlib
import gc
import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal, NamedTuple, cast

import polars as pl

import haute.execution as execution_facade
import haute.projection as projection_planner
from haute._builders import _passthrough_fn
from haute._chunked_writes import (
    ChunkedWrite,
    JoinRecipe,
    RecipeEquivalenceError,
    WriteRecipe,
    check_recipe_equivalence,
    part_name,
    write_parts,
)
from haute._column_lineage import analyze_polars_lineage
from haute._contracts import Contract, get_column_contract
from haute._edge_join import (
    build_edge_join_kwargs,
    edge_join_key_columns_by_role,
    narrow_join_parent_demand,
    resolve_edge_join_role_indices,
)
from haute._execution_context import (
    ExecutionCancelledError,
    ExecutionContext,
    ExecutionMemoryLimitExceededError,
    ExecutionProfile,
)
from haute._graph_shape import validate_pipeline_graph_shape_contracts
from haute._graph_utils import (
    duplicate_input_names,
    edge_input_name,
    resolve_orig_source_names,
    select_edge_source_output,
    upstream_node_ids,
)
from haute._input_preparation import preparation_base_dir, prepare_input_snapshots
from haute._logging import get_logger
from haute._path_resolution import runtime_project_root_scoped
from haute._polars_selectors import preamble_selector_aliases
from haute._polars_utils import (
    _malloc_trim,
    current_streaming_chunk_size,
    projected_or_carrier_columns,
    streaming_collect,
)
from haute._source_cache import SourceCacheCorruptError, SourceCacheError
from haute._types import (
    GraphEdge,
    GraphNode,
    NodeType,
    PipelineGraph,
    _Frame,
)
from haute.chunking import classify_chunk_local_polars_code
from haute.errors import (
    ConfigError,
    ContractMismatchError,
    ContractResolutionError,
    SchemaMismatchError,
    is_public_contract_error,
)

logger = get_logger(component="execute")


def _edge_join_recipe(
    fn: Callable[..., Any],
    node: GraphNode,
    input_frames: Sequence[Any],
) -> JoinRecipe | None:
    """An edge join's chunkable recipe, from the exact frames its builder receives.

    The roles and the (instance-resolved) join config are the builder's own
    (``edge_join_roles`` / ``edge_join_config``), so the recipe joins exactly
    as the builder does; a function without them — not an edge join, or one a
    hook wrapped — has no recipe and is written natively. The recipe ends with
    the node's own column step (selected columns, then renames), which every
    engine applies after the builder.
    """
    roles = getattr(fn, "edge_join_roles", None)
    join_config = getattr(fn, "edge_join_config", None)
    if roles is None or join_config is None or len(input_frames) != 2:
        return None
    frames = [frame.lazy() if isinstance(frame, pl.DataFrame) else frame for frame in input_frames]
    if not all(isinstance(frame, pl.LazyFrame) for frame in frames):
        return None
    base_index, join_index = roles
    shaping = dict(node.data.config)

    def finish(lf: pl.LazyFrame) -> pl.LazyFrame:
        selected = _apply_selected_columns(lf, shaping)
        renamed = _apply_column_renames(selected, shaping)
        return renamed if isinstance(renamed, pl.LazyFrame) else renamed.lazy()

    return JoinRecipe(frames[base_index], frames[join_index], join_config, finish=finish)


def _write_recipe(
    fn: Callable[..., Any],
    node: GraphNode,
    input_frames: Sequence[Any],
    *,
    frame_names: Sequence[str] = (),
    orig_frame_names: Sequence[str] | None = None,
    selector_aliases: frozenset[str] = frozenset(),
) -> WriteRecipe | None:
    """A chunk-local single-input node's recipe, from the exact frame its builder receives.

    Only a Polars node with exactly one input frame qualifies; anything else
    has no recipe and is written natively without recording a decision. When
    eligible, the recipe carries the transform function and the single input
    frame; when ineligible, it carries the classifier's reason and blocking
    operator without a function. The recipe ends with the node's own column
    step (selected columns, then renames), matching how edge joins finish.
    """
    if node.data.nodeType != NodeType.POLARS or len(input_frames) != 1:
        return None
    frame = input_frames[0]
    input_lf = frame.lazy() if isinstance(frame, pl.DataFrame) else frame
    if not isinstance(input_lf, pl.LazyFrame):
        return None
    shaping = dict(node.data.config)
    decision = classify_chunk_local_polars_code(
        shaping.get("code"),
        frame_names=[*frame_names, *(orig_frame_names or ())],
        selector_aliases=selector_aliases,
    )

    def finish(lf: pl.LazyFrame) -> pl.LazyFrame:
        selected = _apply_selected_columns(lf, shaping)
        renamed = _apply_column_renames(selected, shaping)
        return renamed if isinstance(renamed, pl.LazyFrame) else renamed.lazy()

    if not decision.eligible:
        # Every refusal, not only the ones a write happens to report. A node
        # refused here takes the unbounded path however cheap its work is, and
        # which operations that costs us is a question about real graphs: the
        # allowlist should be widened on evidence, not on guesswork.
        logger.debug(
            "write_recipe_refused",
            node_id=node.id,
            reason=decision.reason,
            blocking_operator=decision.blocking_operator,
            line=decision.line,
            column=decision.column,
        )
        return WriteRecipe(
            input=input_lf,
            fn=None,
            finish=finish,
            reason=decision.reason,
            blocking_operator=decision.blocking_operator,
        )

    def node_fn(lf: pl.LazyFrame) -> pl.LazyFrame:
        result = fn(lf)
        return result if isinstance(result, pl.LazyFrame) else result.lazy()

    return WriteRecipe(
        input=input_lf,
        fn=node_fn,
        finish=finish,
    )


def _shapes_output(node: GraphNode) -> bool:
    """Whether a node's own config selects or renames its output columns."""
    config = node.data.config
    return isinstance(config, dict) and (
        bool(config.get("selected_columns")) or bool(config.get("column_renames"))
    )


def _schema_pairs(frame: pl.LazyFrame) -> list[tuple[str, str]]:
    schema = frame.collect_schema()
    return [(name, str(dtype)) for name, dtype in schema.items()]


def _pick_source_frame(
    source_output: Any,
    edge: GraphEdge,
) -> _Frame:
    """Pick the right frame from a source's output for *edge*.

    May actually return a ``dict[str, _Frame]`` when the source is a
    multi-frame single-edge case (sourceHandle is None, source_output is
    the whole bundle). The signature stays narrowed to ``_Frame`` because
    every downstream caller in this module passes the result through
    isinstance/narrowing before LazyFrame-only operations — see the
    ``isinstance(lf, dict)`` branches in ``_build_lazy_node`` and the
    eager path. ``# type: ignore`` on the dict-return sites captures
    this contract.

    Multi-frame sources (e.g. an apiInput with 2+ emit-true tables, commit 4)
    return a ``dict[port_name, LazyFrame]`` rather than a bare LazyFrame.
    The executor walks each outgoing edge from such a source and picks the
    frame the edge's ``sourceHandle`` names — that's the structural pick
    that makes per-frame routing work.

    Single-frame sources keep returning a bare LazyFrame; ``sourceHandle``
    is ignored (passthrough).

    Raises ``ValueError`` for a multi-frame source with a null
    ``sourceHandle`` (edge wasn't wired to a specific frame). Raises
    ``KeyError`` for an edge whose ``sourceHandle`` doesn't match any frame
    the source actually emits.
    """
    return cast(_Frame, select_edge_source_output(source_output, edge))


def _resolve_graph_paths(graph: PipelineGraph) -> PipelineGraph:
    """Resolve project/pipeline-relative file paths before building node functions."""
    return execution_facade.canonical_dataframe_execution_graph(graph)


# ---------------------------------------------------------------------------
# Column contract enforcement
# ---------------------------------------------------------------------------


def _is_boundary_check_exception(exc: BaseException) -> bool:
    """Return whether *exc* should degrade contract checking to opaque."""
    from haute.errors import ConfigError

    if isinstance(exc, (ConfigError, OSError)):
        return True
    try:
        from mlflow.exceptions import MlflowException
    except ImportError:
        return False
    return isinstance(exc, MlflowException)


def _boundary_failure_kind(exc: BaseException) -> str:
    """Classify a known boundary-resolution failure without exposing its text."""
    if isinstance(exc, ConfigError):
        return "configuration"
    if isinstance(exc, OSError):
        return "io"
    return "artifact_store"


@dataclass(frozen=True, slots=True)
class ContractResolution:
    """One canonical node contract-resolution result."""

    contract: Contract
    state: Literal["resolved", "degraded"]
    failure_kind: str | None = None


def _strict_contract_resolution(profile: ExecutionProfile | None) -> bool:
    """Return whether builder-contract resolution must fail loudly.

    Contract validity is independent of projection/materialisation policy:
    every execution except interactive eager preview is strict.
    """
    return profile != ExecutionProfile.PREVIEW_EAGER


def _resolve_effective_contract(
    node: GraphNode,
    *,
    strict: bool,
) -> ContractResolution:
    """Resolve the effective node contract under the active profile policy.

    User-declared sides fill the builder-derived contract's opaque sides. Known
    external/configuration failures fail strict execution with a typed,
    redacted error; interactive preview retains a diagnosed opaque degradation.
    Programmer errors always propagate unchanged.
    """
    try:
        builder = Contract.from_tuple(get_column_contract(node.data.nodeType, node.data.config))
    except Exception as exc:
        if not _is_boundary_check_exception(exc):
            raise
        failure_kind = _boundary_failure_kind(exc)
        if strict:
            raise ContractResolutionError(
                "Unable to resolve the node column contract.",
                node_id=node.id,
                node_type=node.data.nodeType.value,
                failure_kind=failure_kind,
            ) from exc
        logger.info(
            "effective_contract_degraded",
            node_id=node.id,
            node_type=node.data.nodeType.value,
            failure_kind=failure_kind,
        )
        return ContractResolution(
            contract=projection_planner.overlay_declared_contract(
                node,
                Contract.opaque(),
            ),
            state="degraded",
            failure_kind=failure_kind,
        )
    return ContractResolution(
        contract=projection_planner.overlay_declared_contract(node, builder),
        state="resolved",
    )


def _assert_inputs_satisfy_contract(
    node: GraphNode,
    contract: Contract,
    upstream_columns: frozenset[str],
) -> None:
    """Raise ``ContractMismatchError`` if *upstream_columns* is missing
    any column the node's contract says it reads.

    No-op when the contract's input side is opaque (``None``).
    """
    if contract.inputs is None:
        return
    missing = contract.inputs - upstream_columns
    if not missing:
        return
    raise ContractMismatchError(
        "Input columns required by the node's contract are missing from the upstream frame.",
        node_id=node.id,
        node_type=node.data.nodeType.value,
        missing=sorted(missing),
        extra=sorted(upstream_columns - contract.inputs),
        declared_inputs=sorted(contract.inputs),
        upstream_columns=sorted(upstream_columns),
    )


def _assert_outputs_satisfy_contract(
    node: GraphNode,
    contract: Contract,
    output_columns: frozenset[str],
) -> None:
    """Raise ``ContractMismatchError`` if *output_columns* is missing
    any column the node's contract promised to produce.

    We check ⊇ (outputs must be present) rather than == because
    pass-through style nodes legitimately carry additional columns
    through from their input.  A declared output that is absent is a
    bug (typo or buggy user code); an extra column is expected.

    No-op when the contract's output side is opaque (``None``).
    """
    if contract.outputs is None:
        return
    missing = contract.outputs - output_columns
    if not missing:
        return
    raise ContractMismatchError(
        "Output columns promised by the node's contract are missing from the node's result.",
        node_id=node.id,
        node_type=node.data.nodeType.value,
        missing=sorted(missing),
        extra=sorted(output_columns - contract.outputs),
        declared_outputs=sorted(contract.outputs),
        observed_columns=sorted(output_columns),
    )


def _should_check_contract(contract: Contract) -> bool:
    """Return ``True`` iff either side of *contract* is concrete.

    A fully-opaque contract cannot be disproven, so skipping the check
    saves the per-node column-set computation entirely.  This matters
    for the <5% overhead bound when a pipeline is dominated by opaque
    nodes (user polars transforms).
    """
    return contract.inputs is not None or contract.outputs is not None


def _normalise_required_columns_by_node(
    required_columns_by_node: Mapping[str, Iterable[str] | projection_planner.AllExceptColumns]
    | None,
    order: list[str],
) -> dict[str, set[str] | projection_planner.AllExceptColumns]:
    """Validate caller-provided projection seeds for concrete node outputs."""
    return projection_planner.normalise_required_columns_by_node(
        required_columns_by_node,
        order,
    )


@dataclass(frozen=True, slots=True)
class PreparedExecutionRequest:
    """The execution-independent inputs used to prepare a graph once."""

    graph: PipelineGraph
    target_node_id: str | None = None
    source: str = "live"
    required_columns_by_node: (
        Mapping[str, Iterable[str] | projection_planner.AllExceptColumns] | None
    ) = None
    profile: ExecutionProfile | None = None


@dataclass(frozen=True, slots=True)
class PreparedExecution:
    """Read-only graph facts shared by the eager and lazy engines."""

    graph: PipelineGraph
    graph_plan: projection_planner.PreparedGraph
    normalised_required_columns: Mapping[str, set[str] | projection_planner.AllExceptColumns]
    strict_contract_resolution: bool
    children_count: Mapping[str, int]
    children_of: Mapping[str, tuple[str, ...]]
    all_parents: Mapping[str, list[str]]
    incoming_edges_by_target: Mapping[str, tuple[GraphEdge, ...]]
    all_incoming_edges_by_target: Mapping[str, tuple[GraphEdge, ...]]


def _prepare_execution(request: PreparedExecutionRequest) -> PreparedExecution:
    """Resolve and index the graph facts that must agree across engines."""
    graph = _resolve_graph_paths(request.graph)
    graph_plan = projection_planner.prepare_graph(
        graph, request.target_node_id, source=request.source
    )
    validate_pipeline_graph_shape_contracts(
        graph,
        graph_label=graph.pipeline_name or "execution",
        node_ids_to_validate=(
            set(graph_plan.order) if request.target_node_id is not None else None
        ),
    )
    normalised = _normalise_required_columns_by_node(
        request.required_columns_by_node, graph_plan.order
    )
    children_count = dict.fromkeys(graph_plan.order, 0)
    children_of_lists: dict[str, list[str]] = {node_id: [] for node_id in graph_plan.order}
    for node_id, parent_ids in graph_plan.parents_of.items():
        for parent_id in parent_ids:
            children_count[parent_id] += 1
            children_of_lists[parent_id].append(node_id)

    def _edge_index(edges: Iterable[GraphEdge]) -> dict[str, tuple[GraphEdge, ...]]:
        indexed: dict[str, list[GraphEdge]] = {}
        for edge in edges:
            indexed.setdefault(edge.target, []).append(edge)
        return {target: tuple(target_edges) for target, target_edges in indexed.items()}

    return PreparedExecution(
        graph=graph,
        graph_plan=graph_plan,
        normalised_required_columns=normalised,
        strict_contract_resolution=_strict_contract_resolution(request.profile),
        children_count=children_count,
        children_of={key: tuple(value) for key, value in children_of_lists.items()},
        all_parents={node.id: graph.parents_of.get(node.id, []) for node in graph.nodes},
        incoming_edges_by_target=_edge_index(graph_plan.relevant_edges),
        all_incoming_edges_by_target=_edge_index(graph.edges),
    )


@dataclass(frozen=True, slots=True)
class NodeBoundary:
    """The resolved execution boundary for one graph node."""

    node_id: str
    node: GraphNode
    fn: Callable
    is_source: bool
    parent_ids: tuple[str, ...]
    incoming_edges: tuple[GraphEdge, ...]
    contract: Contract | None
    check_contract: bool
    is_passthrough_runtime: bool


class NodeBoundaryRunner:
    """Shared node-boundary mechanics; collection and cache policy stay outside."""

    def __init__(
        self,
        *,
        prepared: PreparedExecution,
        funcs: Mapping[str, tuple[Callable, bool]],
        enforce_contracts: bool,
        execution_context: ExecutionContext | None,
        needed_columns: Mapping[str, frozenset[str] | None],
    ) -> None:
        self.prepared = prepared
        self.funcs = funcs
        self.enforce_contracts = enforce_contracts
        self.execution_context = execution_context
        self.needed_columns = needed_columns

    def open(self, node_id: str) -> NodeBoundary:
        node = self.prepared.graph_plan.node_map[node_id]
        fn, is_source = self.funcs[node_id]
        requested = self.needed_columns.get(node_id)
        if self.execution_context is not None:
            self.execution_context.record_column_widths(
                node_id=node_id, requested_width=None if requested is None else len(requested)
            )
            self.execution_context.checkpoint(label="before_node", node_id=node_id)
        contract = (
            _resolve_effective_contract(
                node, strict=self.prepared.strict_contract_resolution
            ).contract
            if self.enforce_contracts
            else None
        )
        return NodeBoundary(
            node_id=node_id,
            node=node,
            fn=fn,
            is_source=is_source,
            parent_ids=tuple(self.prepared.graph_plan.parents_of.get(node_id, ())),
            incoming_edges=self.prepared.incoming_edges_by_target.get(node_id, ()),
            contract=contract,
            check_contract=contract is not None and _should_check_contract(contract),
            is_passthrough_runtime=fn is _passthrough_fn,
        )

    def input_frames(self, boundary: NodeBoundary, outputs: Mapping[str, Any]) -> list[_Frame]:
        return [_pick_source_frame(outputs[edge.source], edge) for edge in boundary.incoming_edges]

    def invoke(self, boundary: NodeBoundary, input_frames: Sequence[_Frame] = ()) -> Any:
        if boundary.is_source:
            return boundary.fn()
        if not input_frames:
            raise ValueError(f"No input data available for node '{boundary.node_id}'")
        if self.enforce_contracts:
            _assert_simple_join_key_dtypes_compatible(
                boundary.node, list(boundary.parent_ids), list(input_frames)
            )
        return boundary.fn(*input_frames)

    def assert_inputs(
        self,
        boundary: NodeBoundary,
        upstream_columns: frozenset[str],
    ) -> None:
        if (
            not boundary.check_contract
            or boundary.contract is None
            or boundary.contract.inputs is None
        ):
            return
        _assert_inputs_satisfy_contract(
            boundary.node,
            boundary.contract,
            upstream_columns,
        )

    def assert_outputs(
        self,
        boundary: NodeBoundary,
        output_columns: frozenset[str],
    ) -> None:
        if (
            not boundary.check_contract
            or boundary.contract is None
            or boundary.contract.outputs is None
            or boundary.is_passthrough_runtime
        ):
            return
        _assert_outputs_satisfy_contract(boundary.node, boundary.contract, output_columns)


if TYPE_CHECKING:
    from haute._node_snapshots import NodeSnapshotArtifact, NodeSnapshotColumns
    from haute._seed_plans import CaptureDecision, SeedPlan, SeedPlanDecision


def _snapshot_fault_point(name: str, node_id: str) -> None:
    """Deterministic pause point for interleaving tests of planned captures."""
    del name, node_id


# ---------------------------------------------------------------------------
# Materialisation housekeeping
# ---------------------------------------------------------------------------

# Number of materialisations between gc.collect() + _malloc_trim() calls.
# Polars objects use Rust Arc refcounting and are freed immediately on
# ``del``; Python gc.collect() only helps with cyclic garbage (rare here).
# Batching avoids the overhead of scanning all Python objects per
# materialisation.
_GC_BATCH_INTERVAL = 3


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _apply_column_renames(
    frame: pl.LazyFrame | pl.DataFrame,
    config: dict[str, Any],
) -> pl.LazyFrame | pl.DataFrame:
    """Apply column renames from *config*'s ``column_renames``.

    ``column_renames`` is a ``dict[str, str]`` mapping original column names
    to new names.  Only renames for columns that actually exist in the frame
    are applied.  A no-op when the dict is absent or empty.
    """
    renames: dict[str, str] | None = config.get("column_renames")
    if not renames:
        return frame

    if isinstance(frame, pl.LazyFrame):
        all_cols = set(frame.collect_schema().names())
    else:
        all_cols = set(frame.columns)

    valid = {old: new for old, new in renames.items() if old in all_cols and old != new}
    if valid:
        return frame.rename(valid)
    return frame


def _apply_selected_columns(
    frame: pl.LazyFrame | pl.DataFrame,
    config: dict[str, Any],
) -> pl.LazyFrame | pl.DataFrame:
    """Filter *frame* to only the columns listed in *config*'s ``selected_columns``.

    If ``selected_columns`` is absent, empty, or names no valid columns the
    frame is returned unchanged.  Only columns that actually exist in the
    frame are kept, and the filter is a no-op when every column is selected
    (avoids an unnecessary projection).
    """
    sel_cols: list[str] | None = config.get("selected_columns")
    if not sel_cols:
        return frame

    if isinstance(frame, pl.LazyFrame):
        all_cols = frame.collect_schema().names()
    else:
        all_cols = frame.columns

    seen: set[str] = set()
    valid = []
    for c in sel_cols:
        if c in all_cols and c not in seen:
            valid.append(c)
            seen.add(c)
    if valid and len(valid) < len(all_cols):
        return frame.select(valid)
    return frame


def _is_plain_model_score(node: GraphNode) -> bool:
    """Return whether a model-score node has no output-shaping overrides."""
    config = node.data.config
    return (
        node.data.nodeType == NodeType.MODEL_SCORE
        and not config.get("code")
        and not config.get("column_renames")
        and not config.get("selected_columns")
    )


def _assert_simple_join_key_dtypes_compatible(
    node: GraphNode,
    input_ids: Sequence[str],
    input_lfs: Sequence[_Frame],
) -> None:
    """Validate dtype parity for simple inferred multi-parent Polars joins."""
    if node.data.nodeType != NodeType.POLARS or len(input_ids) < 2:
        return
    joins = projection_planner.simple_join_calls_for_parent_inputs(node, input_ids)
    if not joins:
        return

    frame_by_parent = dict(zip(input_ids, input_lfs, strict=True))
    schema_by_parent: dict[str, pl.Schema] = {}

    def _schema(parent_id: str) -> pl.Schema:
        cached = schema_by_parent.get(parent_id)
        if cached is not None:
            return cached
        frame = frame_by_parent[parent_id]
        lazy_frame = frame if isinstance(frame, pl.LazyFrame) else frame.lazy()
        schema = lazy_frame.collect_schema()
        schema_by_parent[parent_id] = schema
        return schema

    for join in joins:
        left_schema = _schema(join.left_parent)
        right_schema = _schema(join.right_parent)
        left_columns = set(left_schema.names())
        right_columns = set(right_schema.names())
        for left_key, right_key in join.key_pairs:
            if left_key not in left_columns:
                raise ContractMismatchError(
                    "Join key is missing from the parent frame.",
                    node_id=node.id,
                    node_type=node.data.nodeType.value,
                    parent_id=join.left_parent,
                    missing=[left_key],
                    join_key=left_key,
                    parent_columns=sorted(left_columns),
                )
            if right_key not in right_columns:
                raise ContractMismatchError(
                    "Join key is missing from the parent frame.",
                    node_id=node.id,
                    node_type=node.data.nodeType.value,
                    parent_id=join.right_parent,
                    missing=[right_key],
                    join_key=right_key,
                    parent_columns=sorted(right_columns),
                )
            left_dtype = left_schema[left_key]
            right_dtype = right_schema[right_key]
            if left_dtype != right_dtype:
                raise SchemaMismatchError(
                    "Join key dtype mismatch between parent frames.",
                    node_id=node.id,
                    node_type=node.data.nodeType.value,
                    left_parent=join.left_parent,
                    right_parent=join.right_parent,
                    left_key=left_key,
                    right_key=right_key,
                    left_dtype=str(left_dtype),
                    right_dtype=str(right_dtype),
                )


def _conservative_strategy_passthrough(
    previous_diagnostic: projection_planner.ExecutionStrategyDiagnostic,
) -> dict[str, Any]:
    """Carry a conservative strategy through a runtime projection rebuild.

    ``full-width-conservative`` is decided once, at admission time, from the
    absence of an estimate plus the presence of a hard worker cap.  A refined
    plan cannot re-derive it, so the decision is passed through unchanged.
    """

    conservative = projection_planner.ExecutionStrategy.FULL_WIDTH_CONSERVATIVE
    if previous_diagnostic.strategy is not conservative:
        return {}
    return {
        "strategy": previous_diagnostic.strategy,
        "reason_code": previous_diagnostic.reason_code,
        "remediation": previous_diagnostic.remediation,
    }


def _replanned_target_preview_strategy(
    executed: projection_planner.ExecutionStrategyResult,
    *,
    order: list[str],
    node_map: Mapping[str, GraphNode],
    required_columns_by_node: Mapping[str, Iterable[str] | projection_planner.AllExceptColumns],
    relevant_edges: list[GraphEdge],
    graph: PipelineGraph,
    known_output_columns: Mapping[tuple[str, str | None], frozenset[str]],
    runtime_edge_demands: Mapping[projection_planner.ProjectionEdgeKey, frozenset[str]],
    runtime_resolved_parent_ids: Iterable[str],
    profile: ExecutionProfile,
    seeded_node_ids: frozenset[str],
) -> projection_planner.ExecutionStrategyResult:
    """Re-plan a target-only preview's diagnostic from the frames it built.

    Before execution an Edge Join cannot route demand to a parent whose schema
    is only known once built, so every node above it reads as unprojected even
    though the join's runtime demand is pushed through the lazy plan.

    *order* is the execution's own order, so under a seed plan it already stops
    at the seeds: a node above one is not planned here because this execution
    never reads it, and a seed's own incoming edges are dropped, because the
    frame came from its generation and those edges were never read.

    *executed* must have been planned for this same execution: admission and
    materialisation estimation see only the plan's executed nodes, so its
    materialisation boundaries are already the ones this run reaches, and a
    seed's own operator is never among them.
    """
    scope = frozenset(order)
    scoped_edges = [
        edge
        for edge in relevant_edges
        if edge.source in scope and edge.target in scope and edge.target not in seeded_node_ids
    ]
    # ``compute_prepared_plan`` takes adjacency from ``relevant_edges``; these
    # rank the plan. One rule, applied once, keeps the two from disagreeing.
    scoped_children: dict[str, list[str]] = {node_id: [] for node_id in order}
    for edge in scoped_edges:
        scoped_children[edge.source].append(edge.target)
    scoped_required_columns = {
        node_id: columns
        for node_id, columns in required_columns_by_node.items()
        if node_id in scope
    }
    replanned = projection_planner.compute_prepared_plan(
        order,
        scoped_children,
        node_map,
        scoped_required_columns,
        relevant_edges=scoped_edges,
        submodels=graph.submodels,
        selector_aliases=preamble_selector_aliases(graph.preamble or ""),
        known_output_columns=known_output_columns,
    )
    unreached = executed.projection_plan.materialisation_boundaries - (scope - seeded_node_ids)
    if unreached:
        raise RuntimeError(
            "execution strategy re-plan received materialisation boundaries this "
            f"execution never reached: {sorted(unreached)}"
        )
    replanned = projection_planner.with_materialisation_boundaries(
        replanned,
        executed.projection_plan.materialisation_boundaries,
    )
    if runtime_edge_demands:
        replanned = projection_planner.with_runtime_inferred_streaming_edges(
            replanned,
            demands_by_edge=runtime_edge_demands,
            resolved_parent_ids=runtime_resolved_parent_ids,
            relevant_edges=scoped_edges,
        )
    diagnostic = executed.diagnostic
    return projection_planner.build_execution_strategy_result(
        replanned,
        profile=profile,
        order=order,
        children_of=scoped_children,
        node_map=node_map,
        has_projection_seed=bool(scoped_required_columns),
        required_columns_by_node=scoped_required_columns,
        estimated_peak_bytes=diagnostic.estimated_peak_bytes,
        raw_estimated_peak_bytes=diagnostic.raw_estimated_peak_bytes,
        estimate_calibration_factor_basis_points=(
            diagnostic.estimate_calibration_factor_basis_points
        ),
        estimate_admission_basis=diagnostic.estimate_admission_basis,
        headroom_bytes=diagnostic.headroom_bytes,
        assumptions=diagnostic.assumptions,
        boundary_operators=projection_planner.materialising_operators_by_node(
            order,
            node_map,
            relevant_edges=scoped_edges,
            submodels=graph.submodels,
        ),
        **_admitted_strategy_passthrough(diagnostic),
    )


def _admitted_strategy_passthrough(
    diagnostic: projection_planner.ExecutionStrategyDiagnostic,
) -> dict[str, Any]:
    """Carry the strategy admission decided through a post-execution re-plan.

    A materialisation boundary's reason and remediation name the admitted
    operator, and a conservative run is decided from the missing estimate; the
    re-plan keeps both boundaries and must not replace their wording.
    """
    admitted = {
        projection_planner.ExecutionStrategy.MATERIALISATION_BOUNDARY,
        projection_planner.ExecutionStrategy.FULL_WIDTH_CONSERVATIVE,
    }
    if diagnostic.strategy not in admitted:
        return {}
    return {
        "strategy": diagnostic.strategy,
        "reason_code": diagnostic.reason_code,
        "remediation": diagnostic.remediation,
    }


_SELECTOR_SCHEMA_REASONS = frozenset({"selector_schema_unknown", "selector_dtypes_unknown"})


def _runtime_selector_demands(
    node: GraphNode,
    edge: GraphEdge,
    input_lf: _Frame,
    projection: set[str] | frozenset[str] | None,
    existing_edge_demands: Mapping[
        projection_planner.ProjectionEdgeKey,
        set[str] | frozenset[str] | None,
    ],
    node_map: Mapping[str, GraphNode],
    submodels: Mapping[str, Any] | None,
    selector_aliases: frozenset[str],
) -> dict[projection_planner.ProjectionEdgeKey, set[str]]:
    """Resolve a single-input Polars node whose lineage needs a selector's schema.

    Static planning knows no input schema for such a node, or no dtypes; its
    input frame's runtime schema supplies both. Nodes whose static lineage fails
    for any other reason are left to the static plan.
    """
    if node.data.nodeType is not NodeType.POLARS:
        return {}
    edge_key = projection_planner.ProjectionEdgeKey.from_edge(edge)
    if existing_edge_demands.get(edge_key) is not None:
        return {}
    if projection_planner.has_configured_column_renames(node):
        return {}
    code = node.data.config.get("code")
    if not isinstance(code, str) or not code.strip():
        return {}
    try:
        input_name = edge_input_name(edge, node_map[edge.source], submodels=submodels)
    except (KeyError, ValueError):
        return {}
    names = {input_name}
    raw_mapping = node.data.config.get("inputMapping")
    if raw_mapping:
        if not isinstance(raw_mapping, Mapping):
            return {}
        for alias, current_name in raw_mapping.items():
            if not isinstance(alias, str) or not alias or current_name != input_name:
                return {}
            names.add(alias)

    probe = analyze_polars_lineage(
        code, {name: None for name in names}, projection, selector_aliases=selector_aliases
    )
    if probe.supported or probe.reason not in _SELECTOR_SCHEMA_REASONS:
        return {}
    lazy_frame = input_lf if isinstance(input_lf, pl.LazyFrame) else input_lf.lazy()
    schema = lazy_frame.collect_schema()
    columns = frozenset(schema.names())
    analysis = analyze_polars_lineage(
        code,
        {name: columns for name in names},
        projection,
        input_dtypes={name: dict(schema) for name in names},
        selector_aliases=selector_aliases,
    )
    if not analysis.supported:
        return {}
    demand: set[str] = set()
    for name in names:
        demand.update(analysis.demands_by_input.get(name, ()))
    return {edge_key: demand}


def _runtime_lineage_demands(
    node: GraphNode,
    incoming_edges: Sequence[GraphEdge],
    input_lfs: Sequence[_Frame],
    projection: set[str] | frozenset[str] | None,
    existing_edge_demands: Mapping[
        projection_planner.ProjectionEdgeKey,
        set[str] | frozenset[str] | None,
    ],
    node_map: Mapping[str, GraphNode],
    submodels: Mapping[str, Any] | None = None,
    selector_aliases: frozenset[str] = frozenset(),
) -> dict[projection_planner.ProjectionEdgeKey, set[str]]:
    """Resolve a safe input projection from lazy parent schemas.

    A join resolves its parents' demands from their column names. A single-input
    Polars node whose static lineage lacks only the schema a selector expands
    against resolves from its input frame's runtime names and dtypes.
    """
    if len(incoming_edges) == 1:
        return _runtime_selector_demands(
            node,
            incoming_edges[0],
            input_lfs[0],
            projection,
            existing_edge_demands,
            node_map,
            submodels,
            selector_aliases,
        )
    if projection is None or len(incoming_edges) < 2:
        return {}
    if projection_planner.has_configured_column_renames(node):
        return {}
    if any(
        existing_edge_demands.get(projection_planner.ProjectionEdgeKey.from_edge(edge)) is not None
        for edge in incoming_edges
    ):
        return {}

    schema_by_key: dict[projection_planner.ProjectionEdgeKey, set[str]] = {}
    for edge, frame in zip(incoming_edges, input_lfs, strict=True):
        lazy_frame = frame if isinstance(frame, pl.LazyFrame) else frame.lazy()
        schema_by_key[projection_planner.ProjectionEdgeKey.from_edge(edge)] = set(
            lazy_frame.collect_schema().names()
        )

    input_ids = [edge.source for edge in incoming_edges]

    left_keys: set[str]
    right_keys: set[str]
    if node.data.nodeType is NodeType.EDGE_JOIN:
        base_index, join_index = resolve_edge_join_role_indices(
            [edge.targetHandle for edge in incoming_edges]
        )
        left_edge = incoming_edges[base_index]
        right_edge = incoming_edges[join_index]
        base_keys, join_keys = edge_join_key_columns_by_role(node.data.config)
        left_keys = set(base_keys)
        right_keys = set(join_keys)
        kwargs = build_edge_join_kwargs(node.data.config)
        how = str(kwargs["how"])
        suffix = str(kwargs["suffix"])
    elif node.data.nodeType is NodeType.POLARS:
        # Preserve the engine's typed missing-key/dtype diagnostics before
        # asking lineage analysis for an optimisation.  This validator is a
        # no-op for port-distinct edges that share one source node; those are
        # still handled by the edge/name-aware analysis below and by Polars'
        # authoritative runtime validation.
        _assert_simple_join_key_dtypes_compatible(node, input_ids, input_lfs)
        by_name: dict[str, GraphEdge] = {}
        schemas: dict[str, frozenset[str]] = {}
        for edge in incoming_edges:
            try:
                name = edge_input_name(
                    edge,
                    node_map[edge.source],
                    submodels=submodels,
                )
            except (KeyError, ValueError):
                return {}
            if name in by_name:
                return {}
            by_name[name] = edge
            schemas[name] = frozenset(
                schema_by_key[projection_planner.ProjectionEdgeKey.from_edge(edge)]
            )
        raw_mapping = node.data.config.get("inputMapping")
        if raw_mapping:
            if not isinstance(raw_mapping, Mapping):
                return {}
            for alias, current_name in raw_mapping.items():
                if (
                    not isinstance(alias, str)
                    or not alias
                    or not isinstance(current_name, str)
                    or current_name not in by_name
                    or (alias in by_name and by_name[alias] != by_name[current_name])
                ):
                    return {}
                by_name[alias] = by_name[current_name]
                schemas[alias] = schemas[current_name]
        code = node.data.config.get("code")
        if not isinstance(code, str):
            return {}
        analysis = analyze_polars_lineage(
            code, schemas, projection, selector_aliases=selector_aliases
        )
        if not analysis.supported:
            return {}
        demands: dict[projection_planner.ProjectionEdgeKey, set[str]] = {}
        for input_name, columns in analysis.demands_by_input.items():
            edge_key = projection_planner.ProjectionEdgeKey.from_edge(by_name[input_name])
            demands.setdefault(edge_key, set()).update(columns)
        return demands
    else:
        return {}

    left_key = projection_planner.ProjectionEdgeKey.from_edge(left_edge)
    right_key = projection_planner.ProjectionEdgeKey.from_edge(right_edge)
    routed = narrow_join_parent_demand(
        projection,
        left_keys=left_keys,
        right_keys=right_keys,
        left_schema=schema_by_key[left_key],
        right_schema=schema_by_key[right_key],
        how=how,
        suffix=suffix,
    )
    if routed is None:
        return {}
    left_demand, right_demand = routed
    return {left_key: left_demand, right_key: right_demand}


def _runtime_projectable_source_ids(
    demands_by_edge: Mapping[projection_planner.ProjectionEdgeKey, Iterable[str]],
    node_map: Mapping[str, GraphNode],
) -> frozenset[str]:
    """Return source parents whose lazy scans can absorb their runtime edge demand."""
    source_types = {NodeType.API_INPUT, NodeType.DATA_INPUT, NodeType.EXTERNAL_FILE}
    demand_by_parent: dict[str, set[str]] = {}
    for edge_key, columns in demands_by_edge.items():
        demand_by_parent.setdefault(edge_key.source, set()).update(columns)
    projectable: set[str] = set()
    for parent_id, demand in demand_by_parent.items():
        parent = node_map[parent_id]
        if parent.data.nodeType not in source_types:
            continue
        code = parent.data.config.get("code")
        if not isinstance(code, str) or not code.strip():
            projectable.add(parent_id)
        elif (
            projection_planner.source_user_code_scan_columns(
                parent.data.config,
                code,
                demand,
                source_columns=None,
            )
            is not None
        ):
            projectable.add(parent_id)
    return frozenset(projectable)


def _prune_live_switch_edges(
    edges: list[GraphEdge],
    node_map: dict[str, GraphNode],
    source: str,
    *,
    submodels: Mapping[str, Any] | None = None,
) -> list[GraphEdge]:
    """Remove edges to live_switch nodes from inputs inactive for *source*.

    A live_switch node's config contains ``input_scenario_map`` which maps
    each input name to the scenario it serves.  Only edges from inputs
    matching the active source are kept; the unused branch is pruned so
    it is neither executed nor shown in profilers.
    """
    return projection_planner.prune_live_switch_edges(
        edges,
        node_map,
        source,
        submodels=submodels,
    )


@runtime_project_root_scoped
def _execute_lazy(
    graph: PipelineGraph,
    build_node_fn: Callable,
    target_node_id: str | None = None,
    preamble_ns: dict | None = None,
    source: str = "live",
    enforce_contracts: bool = False,
    preserve_node_ids: set[str] | frozenset[str] | None = None,
    required_columns_by_node: Mapping[str, Iterable[str] | projection_planner.AllExceptColumns]
    | None = None,
    execution_context: ExecutionContext | None = None,
    source_by_node: Mapping[str, str] | None = None,
    schema_only: bool = False,
    runtime_source_frames_by_node: Mapping[str, pl.DataFrame] | None = None,
    prepare_inputs: bool = True,
    snapshot_plan: SeedPlan | None = None,
    join_recipes: dict[str, JoinRecipe] | None = None,
    write_recipes: dict[str, WriteRecipe] | None = None,
    unshaped_frames: dict[str, pl.LazyFrame] | None = None,
) -> tuple[dict[str, _Frame], list[str], dict[str, list[str]], dict[str, str]]:
    """Execute a graph lazily and return per-node LazyFrames.

    Used by write_data_output (batch writes) and score_graph (deploy scoring)
    where Polars can optimise the full lazy plan end-to-end.
    Interactive paths (preview, trace) use eager execution with caching
    instead — see executor._eager_execute and trace.execute_trace.

    Args:
        graph: React Flow graph with "nodes" and "edges".
        build_node_fn: Function (node_dict, source_names) -> (name, fn, is_source).
        target_node_id: If set, only execute ancestors of this node.
        source: Active execution source (``"live"`` = eager scoring).
        preserve_node_ids: Non-source intermediate outputs that must remain
            available to the caller after their final downstream consumer has
            executed. Optimiser ratebook solves use this for the selected
            banding source side input.
        required_columns_by_node: Optional exact output-column demand for
            caller-consumed nodes.  These seeds supplement concrete
            descendant-derived projection for the named nodes, and replace
            opaque descendant demand so callers that consume a non-OUTPUT
            node directly can avoid terminal "all columns" propagation.
        source_by_node: Optional per-node source override passed only to node
            builders.  Graph pruning still uses ``source`` so live-switch
            routing remains stable while selected nodes, such as deploy
            modelScore, can opt into batch execution.
        enforce_contracts: When ``True``, assert declared column contracts at each
            node boundary via ``.collect_schema()``.  Polars computes
            schemas without executing the query, so this stays cheap.
            Production code paths (batch sink, deploy scoring, training,
            optimiser) run through here — enforcement on the lazy path
            is what makes contract coverage real end-to-end.
        schema_only: Declares that the caller reads ``collect_schema()`` and
            never collects a frame or invokes a sink.  Strategy planning then
            skips the group-by materialisation-admission gate, which bounds
            peak memory during materialisation only, and the declaration is
            forwarded to every node builder through
            ``NodeBuildContext.schema_only`` so a builder that would otherwise
            materialise while the graph is being built honours it: the OUTPUT
            node returns an empty frame under the document schema derived by
            ``_output_assembler.output_document_schema`` instead of assembling
            its document.
        runtime_source_frames_by_node: Request-local DataFrames injected at
            source nodes, used for group-by materialisation estimation.
        snapshot_plan: A resolved, leased seed plan for this exact execution
            (``haute._seed_plans``). Seeded nodes read their leased generation
            and nothing needed only by them is built; a pass-through node is
            its selected input; every capture point is written through the
            bounded sink into the shared snapshot store and execution continues
            from what was written — a join, fan-out, or join feeder is
            materialised this way, breaking chained-join memory accumulation
            and plan duplication across branches (pola-rs/polars#24206). Input
            preparation already ran when the plan was opened.

    Returns:
        (lazy_outputs, order, parents_of, id_to_name)
    """
    prepared_execution = _prepare_execution(
        PreparedExecutionRequest(
            graph=graph,
            target_node_id=target_node_id,
            source=source,
            required_columns_by_node=required_columns_by_node,
            profile=execution_context.profile if execution_context is not None else None,
        )
    )
    requested_graph = graph
    graph = prepared_execution.graph
    decision = snapshot_plan.decision if snapshot_plan is not None else None
    if decision is not None:
        _check_snapshot_plan(
            decision,
            requested_graph,
            target_node_id=target_node_id,
            source=source,
            profile=(
                execution_context.profile
                if execution_context is not None
                else ExecutionProfile.LAZY_SINK
            ),
        )
    preserved_outputs = frozenset(preserve_node_ids or ()) | frozenset(
        decision.consumed_node_ids if decision is not None else ()
    )
    node_source_overrides = dict(source_by_node or {})
    if execution_context is not None:
        execution_context.checkpoint(label="lazy_start")
    graph_plan = prepared_execution.graph_plan
    node_map = graph_plan.node_map
    order = graph_plan.order
    parents_of = graph_plan.parents_of
    id_to_name = graph_plan.id_to_name
    relevant_edges = graph_plan.relevant_edges
    # Automatic input preparation runs between graph preparation and strategy
    # planning, so the RAM estimator reads a published generation.
    prepare_input_snapshots(
        order,
        node_map,
        profile=execution_context.profile if execution_context is not None else None,
        execution_context=execution_context,
        base_dir=preparation_base_dir(graph),
        # Deploy scoring reads bundled artifacts through its own build_node_fn
        # intercept, so its canonical configs must never be prepared here. A
        # seed plan was opened after preparing exactly the inputs it reads.
        schema_only=schema_only or not prepare_inputs or decision is not None,
    )
    normalised_required_columns = prepared_execution.normalised_required_columns
    planning_required_columns: dict[
        str,
        set[str] | projection_planner.AllExceptColumns,
    ] = dict(normalised_required_columns)
    if decision is not None:
        # Captures widen demand before planning, exactly as the plan negotiated.
        planning_required_columns = {
            node_id: (
                demand if isinstance(demand, projection_planner.AllExceptColumns) else set(demand)
            )
            for node_id, demand in decision.planning_required_columns.items()
        }

    # Count downstream consumers per node so a parent's frame is released
    # once every consumer has been materialised. (A fan-out point is a capture
    # under a seed plan, so Polars does not duplicate its upstream plan per
    # branch — e.g. a 38 GB JSONL scan read twice for two sibling consumers.)
    children_count = dict(prepared_execution.children_count)
    children_of = prepared_execution.children_of

    seed_outputs: dict[str, _Frame] = {}
    plan_skipped_nodes: set[str] = set()
    # Nodes whose frame is a scan of a held file (a leased seed or a written
    # capture) rather than the lazy plan that built it.
    file_backed_node_ids: set[str] = set()
    if decision is not None and snapshot_plan is not None:
        from haute._seed_plans import SharedSnapshotCaptureSkipRecord, SharedSnapshotSeedRecord

        for node_id, seed in decision.seeds.items():
            seed_outputs[node_id] = snapshot_plan.seed_frame(node_id)
            file_backed_node_ids.add(node_id)
            if execution_context is not None:
                execution_context.record_shared_snapshot_seed(
                    SharedSnapshotSeedRecord(
                        node_id=node_id,
                        identity_digest=seed.identity.digest,
                        generation_id=seed.generation_id,
                        columns=seed.demand,
                    )
                )
        if execution_context is not None:
            for skip_node_id, reason in sorted(decision.skipped_captures.items()):
                execution_context.record_shared_snapshot_capture_skip(
                    SharedSnapshotCaptureSkipRecord(
                        node_id=skip_node_id,
                        reason=reason,
                    )
                )
        # The plan decided what runs: seeds, and the nodes still built below
        # them along effective edges. Nothing else is built.
        needed_by_plan = set(decision.executed_node_ids) | set(decision.seeds)
        plan_skipped_nodes = {node_id for node_id in order if node_id not in needed_by_plan}

    # Backward column analysis: compute the minimal set of columns
    # needed at each node's output so captures can project away unneeded
    # columns before writing.  Batch
    # MODEL_SCORE nodes also consume this demand locally so their scored
    # file carries no unused passthrough columns.
    strategy_profile = (
        execution_context.profile if execution_context is not None else ExecutionProfile.LAZY_SINK
    )
    # A planned execution admits and estimates only what it builds: nothing on
    # an unselected pass-through branch, nothing a seed covers.
    planned_node_ids = decision.executed_node_ids if decision is not None else None
    preamble_aliases = preamble_selector_aliases(graph.preamble or "")
    group_by_operators = {
        node_id: operator
        for node_id, operator in projection_planner.materialising_operators_by_node(
            order,
            node_map,
            relevant_edges=relevant_edges,
            submodels=graph.submodels,
        ).items()
        if planned_node_ids is None or node_id in planned_node_ids
    }
    if group_by_operators and not schema_only:
        # A materialising group-by needs the request planner's source-aware RAM
        # estimate. The prepared-only planner deliberately cannot derive one
        # because it no longer owns the complete graph/input metadata.
        public_strategy_result = execution_facade.plan_execution_strategy(
            execution_facade.ProjectionRequest(
                graph=graph,
                target_node_id=target_node_id,
                profile=strategy_profile,
                required_columns_by_node=planning_required_columns,
                source=source,
            ),
            execution_context=execution_context,
            runtime_source_frames_by_node=runtime_source_frames_by_node,
            materialising_node_ids=planned_node_ids,
            estimation_graph=(
                snapshot_plan.estimation_graph(graph) if snapshot_plan is not None else None
            ),
        )
    else:
        public_strategy_result = execution_facade.plan_prepared_execution_strategy(
            order,
            {node_id: list(children) for node_id, children in children_of.items()},
            node_map,
            profile=strategy_profile,
            required_columns_by_node=planning_required_columns,
            execution_context=execution_context,
            schema_only=schema_only,
            relevant_edges=relevant_edges,
            submodels=graph.submodels,
            selector_aliases=preamble_aliases,
            materialising_node_ids=planned_node_ids,
        )
    public_projection_plan = public_strategy_result.projection_plan
    projection_plan = public_projection_plan
    needed_cols = projection_plan.needed_by_node
    edge_demands = projection_plan.edge_demands
    # A seed plan's negotiated capture demand may be broader than the run's own.
    planning_broadens_projection = planning_required_columns != normalised_required_columns
    runtime_projection_plan = (
        projection_planner.compute_prepared_plan(
            order,
            children_of,
            node_map,
            normalised_required_columns,
            relevant_edges=relevant_edges,
            submodels=graph.submodels,
            selector_aliases=preamble_aliases,
        )
        if planning_broadens_projection
        else projection_plan
    )
    api_port_columns_by_node = projection_planner.api_input_port_columns_by_node(
        node_map,
        relevant_edges,
        projection_plan,
    )

    # Full parent lookup from ALL edges for instance resolution
    all_parents = prepared_execution.all_parents

    # Per-target incoming-edge lookup, in edge-declaration order, so each
    # node's function-parameter binding key can be derived from
    # ``edge.sourceHandle or edge.source`` (MULTI_FRAME_PLAN §4b) without
    # re-scanning per node. Use ``relevant_edges`` (post-pruning,
    # ancestor-filtered) so live-switch-inactive edges don't surface here
    # — that would mismatch ``parents_of`` and break the binding-count
    # invariant on switch nodes.
    incoming_edges_by_target = prepared_execution.incoming_edges_by_target
    all_incoming_edges_by_target = prepared_execution.all_incoming_edges_by_target
    original_node_map = dict(graph.node_map)
    all_edges_by_target = {
        target: list(edges) for target, edges in all_incoming_edges_by_target.items()
    }

    # Build executable functions — delegates to _build_funcs with
    # row_limit=None (lazy path never caps source output).
    with (
        execution_context.stage("lazy_build_functions")
        if execution_context is not None
        else contextlib.nullcontext()
    ):
        builder_needed_cols = projection_planner.builder_required_output_columns_by_node(
            node_map,
            needed_cols,
            preserve_eager_model_score_inputs=False,
        )
        if planning_broadens_projection:
            # Source builders cannot validate a best-effort capture-only demand
            # before their lazy schema exists.  Keep their scans lazy and broad;
            # the first edge projection below intersects capture-only columns with
            # the actual schema, so Parquet/NDJSON pushdown still occurs.
            for node_id in order:
                if not parents_of.get(node_id):
                    builder_needed_cols[node_id] = None
        build_order = [
            node_id
            for node_id in order
            if node_id not in plan_skipped_nodes
            and node_id not in seed_outputs
            and (decision is None or node_id not in decision.pass_through_edges)
        ]
        funcs = _build_funcs(
            build_order,
            node_map,
            id_to_name,
            all_parents,
            build_node_fn,
            incoming_edges_by_target=incoming_edges_by_target,
            all_incoming_edges_by_target=all_incoming_edges_by_target,
            all_node_map=graph.node_map,
            row_limit=None,
            preamble_ns=preamble_ns,
            source=source,
            source_by_node=node_source_overrides,
            required_output_columns_by_node=builder_needed_cols,
            required_output_columns_by_port_by_node=api_port_columns_by_node,
            execution_profile=(
                execution_context.profile if execution_context is not None else None
            ),
            schema_only=schema_only,
            submodels=graph.submodels,
        )

    boundary_runner = NodeBoundaryRunner(
        prepared=prepared_execution,
        funcs=funcs,
        enforce_contracts=enforce_contracts,
        execution_context=execution_context,
        needed_columns=needed_cols,
    )

    # Execute - all intermediate results stay lazy
    lazy_outputs: dict[str, _Frame] = {}

    # Separate mutable counter for tracking remaining downstream consumers.
    # Decremented when a consumer is materialised so we know when a parent's
    # LazyFrame can be safely deleted (freeing Polars/Rust Arrow buffers).
    remaining: dict[str, int] = dict(children_count)

    # Batch gc.collect() calls — Polars objects use Rust Arc refcounting
    # and are freed immediately on ``del``.  gc.collect() only helps with
    # cyclic Python garbage (rare here) and adds 50-200 ms per call.
    materialisations_since_gc = 0

    def _release_consumed_parents(nid: str) -> None:
        # Drop parent LazyFrame refs that have no remaining consumers
        # downstream — lets Polars/Rust release the backing buffers.
        # Source nodes are kept: they hold cheap scan_* references and
        # callers may need them (e.g. optimiser extracting banding factors).
        # Cache-backed nodes are likewise kept: they hold scan_parquet
        # references against artifacts the downstream plan composes from,
        # and dropping the LazyFrame would let the cache release the
        # underlying file before the downstream collect.
        for pid in parents_of.get(nid, []):
            remaining[pid] -= 1
            _, pid_is_source = funcs.get(pid, (None, False))
            if (
                remaining[pid] <= 0
                and pid in lazy_outputs
                and not pid_is_source
                and pid not in preserved_outputs
                and pid not in file_backed_node_ids
            ):
                del lazy_outputs[pid]

    # Per-node column sets used by the boundary contract checks.  Polars
    # computes schema without executing the query, so collect_schema()
    # is cheap; caching keeps repeated lookups free when the same
    # upstream feeds multiple consumers.
    #
    # column_cache is keyed by ``(producer_node_id, port_name_or_None)``.
    # Multi-frame apiInputs (commit 4) emit different columns per frame, so
    # consumers picking different frames of the same upstream must not
    # collide on a parent-id-only key. ``None`` is used for single-frame
    # outputs (the common case).
    column_cache: dict[tuple[str, str | None], frozenset[str]] = {}

    def _schema_names_of(frame: pl.LazyFrame | pl.DataFrame) -> list[str]:
        lazy_frame = frame if isinstance(frame, pl.LazyFrame) else frame.lazy()
        return lazy_frame.collect_schema().names()

    def _columns_of(frame: pl.LazyFrame | pl.DataFrame) -> frozenset[str]:
        return frozenset(_schema_names_of(frame))

    def _apply_edge_projection(
        edge: GraphEdge,
        frame: _Frame,
        *,
        runtime_demand: set[str] | None = None,
    ) -> tuple[_Frame, frozenset[str] | None]:
        child_id = edge.target
        parent_id = edge.source
        edge_key = projection_planner.ProjectionEdgeKey.from_edge(edge)
        demand: set[str] | frozenset[str] | None = runtime_demand
        if demand is None and edge_key not in edge_demands:
            return frame, None
        if demand is None:
            demand = edge_demands[edge_key]
        if demand is None:
            return frame, None

        lazy_frame = frame if isinstance(frame, pl.LazyFrame) else frame.lazy()
        schema_cols = _schema_names_of(lazy_frame)
        schema_set = set(schema_cols)
        missing = demand - schema_set
        runtime_required = (
            set(demand)
            if runtime_demand is not None
            else set(runtime_projection_plan.demand_for_edge(edge) or ())
        )
        runtime_missing = missing & runtime_required
        if runtime_missing:
            raise ContractMismatchError(
                "Columns required by a projection contract are missing from the parent frame.",
                node_id=child_id,
                parent_id=parent_id,
                missing=sorted(runtime_missing),
                required_columns=sorted(demand),
                parent_columns=sorted(schema_set),
            )
        if missing:
            # A seed plan's negotiated demand may be broader than this run's
            # own. A capture-only column the schema lacks is dropped from the
            # capture and must not fail an otherwise valid execution.
            logger.warning(
                "planned_projection_column_missing",
                node_id=child_id,
                parent_id=parent_id,
                missing=sorted(missing),
            )
            demand = set(demand) - missing

        ordered = projected_or_carrier_columns(schema_cols, demand)
        return lazy_frame.select(ordered), frozenset(ordered)

    def _runtime_join_edge_demands(
        child_id: str,
        incoming_edges: Sequence[GraphEdge],
        input_lfs: Sequence[_Frame],
    ) -> dict[projection_planner.ProjectionEdgeKey, set[str]]:
        return _runtime_lineage_demands(
            node_map[child_id],
            incoming_edges,
            input_lfs,
            needed_cols.get(child_id),
            edge_demands,
            node_map,
            graph.submodels,
            preamble_aliases,
        )

    def _build_lazy_node(boundary: NodeBoundary) -> tuple[_Frame, bool, GraphNode]:
        # May actually return ``(dict[str, _Frame], bool, GraphNode)`` for
        # multi-frame apiInput sources. Signature stays narrowed because every
        # consumer in this function passes the result through
        # ``isinstance(lf, dict)`` narrowing before LazyFrame-only operations.
        # ``# type: ignore[return-value]`` on the dict-return site captures it.
        nonlocal public_projection_plan, public_strategy_result

        nid = boundary.node_id
        is_source = boundary.is_source
        node = boundary.node
        contract = boundary.contract
        check_here = boundary.check_contract
        # Builder-wired ``_passthrough_fn`` means the node is in a stub
        # state (MODEL_SCORE without a model, OPTIMISER_APPLY without
        # an artifact).  Its declared contract describes the configured
        # shape the runtime does not produce yet; skip the output check
        # to preserve the "configure later" UX while still enforcing
        # contracts the moment a real function is wired.
        is_passthrough_runtime = boundary.is_passthrough_runtime

        if is_source:
            lf = boundary_runner.invoke(boundary)
        else:
            input_ids = parents_of.get(nid, [])
            missing = [pid for pid in input_ids if pid not in lazy_outputs]
            if missing:
                raise ValueError(
                    f"Node '{nid}' is missing input(s) from: {missing}. "
                    "Upstream node(s) may have failed or not been registered."
                )
            # Resolve each incoming edge's frame via ``_pick_source_frame``
            # so a multi-frame source (an apiInput emitting a per-frame dict,
            # commit 4) routes the right frame to each edge based on
            # ``edge.sourceHandle``. Single-frame sources pass through.
            incoming_edges = boundary.incoming_edges
            input_lfs = boundary_runner.input_frames(boundary, lazy_outputs)
            if not input_lfs:
                raise ValueError(f"No input data available for node '{nid}'")

            projected_input_lfs: list[_Frame] = []
            projected_input_columns: list[frozenset[str] | None] = []
            runtime_edge_demands = _runtime_join_edge_demands(
                nid,
                incoming_edges,
                input_lfs,
            )
            if (
                runtime_edge_demands
                and execution_context is not None
                and public_projection_plan is not None
            ):
                public_projection_plan = projection_planner.with_runtime_inferred_streaming_edges(
                    public_projection_plan,
                    demands_by_edge=runtime_edge_demands,
                    resolved_parent_ids=_runtime_projectable_source_ids(
                        runtime_edge_demands,
                        node_map,
                    ),
                    relevant_edges=relevant_edges,
                )
                previous_diagnostic = public_strategy_result.diagnostic
                public_strategy_result = projection_planner.build_execution_strategy_result(
                    public_projection_plan,
                    profile=execution_context.profile,
                    order=order,
                    children_of=children_of,
                    node_map=node_map,
                    has_projection_seed=bool(planning_required_columns),
                    required_columns_by_node=planning_required_columns,
                    estimated_peak_bytes=previous_diagnostic.estimated_peak_bytes,
                    raw_estimated_peak_bytes=(previous_diagnostic.raw_estimated_peak_bytes),
                    estimate_calibration_factor_basis_points=(
                        previous_diagnostic.estimate_calibration_factor_basis_points
                    ),
                    estimate_admission_basis=previous_diagnostic.estimate_admission_basis,
                    headroom_bytes=previous_diagnostic.headroom_bytes,
                    assumptions=previous_diagnostic.assumptions,
                    boundary_operators=group_by_operators,
                    **_conservative_strategy_passthrough(previous_diagnostic),
                )
                execution_context.projection_plan = public_strategy_result
            for incoming_edge, input_lf in zip(incoming_edges, input_lfs, strict=True):
                edge_key = projection_planner.ProjectionEdgeKey.from_edge(incoming_edge)
                projected_lf, projected_cols = _apply_edge_projection(
                    incoming_edge,
                    input_lf,
                    runtime_demand=runtime_edge_demands.get(edge_key),
                )
                projected_input_lfs.append(projected_lf)
                projected_input_columns.append(projected_cols)
            input_lfs = projected_input_lfs
            if execution_context is not None and all(
                columns is not None for columns in projected_input_columns
            ):
                execution_context.record_column_widths(
                    node_id=nid,
                    input_width=sum(
                        len(columns) for columns in projected_input_columns if columns is not None
                    ),
                )

            if check_here and contract is not None and contract.inputs is not None:
                upstream_col_sets: list[frozenset[str]] = []
                for upstream_edge, upstream_lf, projected_cols in zip(
                    incoming_edges,
                    input_lfs,
                    projected_input_columns,
                    strict=True,
                ):
                    # Key the cache by (parent_id, port_name) so two
                    # consumers picking different frames of the same
                    # multi-frame source see distinct cache entries.
                    cache_key = (upstream_edge.source, upstream_edge.sourceHandle)
                    upstream_cols: frozenset[str]
                    if projected_cols is not None:
                        upstream_cols = projected_cols
                    else:
                        cached_cols = column_cache.get(cache_key)
                        if cached_cols is None:
                            cached_cols = _columns_of(upstream_lf)
                            column_cache[cache_key] = cached_cols
                        upstream_cols = cached_cols
                    upstream_col_sets.append(upstream_cols)
                upstream_cols = frozenset().union(*upstream_col_sets)
                boundary_runner.assert_inputs(boundary, upstream_cols)

            recipe = _edge_join_recipe(boundary.fn, node, input_lfs)
            if recipe is not None:
                built_join_recipes[nid] = recipe
            write_names: list[str] = []
            for edge in boundary.incoming_edges:
                # Prepared boundaries must name existing source nodes.
                source_node = node_map[edge.source]
                try:
                    write_names.append(
                        edge_input_name(edge, source_node, submodels=graph.submodels)
                    )
                except ValueError:
                    # API-input null handle only; _build_funcs raises first for every other.
                    pass
            orig_names = resolve_orig_source_names(
                node,
                original_node_map,
                all_edges_by_target,
            )
            write_rec = _write_recipe(
                boundary.fn,
                node,
                input_lfs,
                frame_names=write_names,
                orig_frame_names=orig_names,
                selector_aliases=preamble_aliases,
            )
            if write_rec is not None:
                built_write_recipes[nid] = write_rec
            lf = boundary_runner.invoke(boundary, input_lfs)

        if isinstance(lf, pl.DataFrame):
            if execution_context is not None:
                execution_context.record_column_widths(
                    node_id=nid,
                    output_width=lf.width,
                )
            lf = lf.lazy()

        # Multi-frame emit: a source (currently only apiInput when v2 has
        # 2+ emit-true tables) may return a ``dict[port_name, LazyFrame]``.
        # The dict is stored in lazy_outputs[nid] and consumers pick a
        # frame from it per-edge via ``_pick_source_frame``. Single-frame
        # post-processing (selected_columns / column_renames / output
        # contract check) is bypassed because those transformations are
        # per-frame, not per-bundle. They'd apply naturally to whichever
        # frame the consumer picks if the consumer chooses to layer them
        # on top.
        #
        # Populate column_cache per-frame so downstream consumers' contract
        # checks find the right columns under ``(parent_id, port_name)``.
        if isinstance(lf, dict):
            for port_name, port_frame in lf.items():
                column_cache[(nid, port_name)] = _columns_of(port_frame)
            # Multi-frame apiInput: returning a dict-of-frames in the
            # ``_Frame`` slot is the runtime contract; see function docstring.
            return lf, is_source, node  # type: ignore[return-value]

        if _shapes_output(node_map[nid]):
            # Its columns before its own selection and renames, which a
            # snapshot records so a seeded preview can still report them.
            built_unshaped_frames[nid] = lf if isinstance(lf, pl.LazyFrame) else lf.lazy()
        # Apply selected_columns filter first (uses pre-rename names),
        # then column renames on the surviving columns.
        lf = _apply_selected_columns(lf, node_map[nid].data.config)
        if isinstance(lf, pl.DataFrame):
            lf = lf.lazy()
        lf = _apply_column_renames(lf, node_map[nid].data.config)
        if isinstance(lf, pl.DataFrame):
            lf = lf.lazy()

        if (
            check_here
            and contract is not None
            and contract.outputs is not None
            and not is_passthrough_runtime
        ):
            out_cols = _columns_of(lf)
            column_cache[(nid, None)] = out_cols
            if execution_context is not None:
                execution_context.record_column_widths(
                    node_id=nid,
                    output_width=len(out_cols),
                )
            boundary_runner.assert_outputs(boundary, out_cols)

        return lf, is_source, node

    execution_order = list(order)
    # Edge joins built in this run, so a full write of one can be chunked.
    built_join_recipes: dict[str, JoinRecipe] = join_recipes if join_recipes is not None else {}
    built_write_recipes: dict[str, WriteRecipe] = write_recipes if write_recipes is not None else {}
    built_unshaped_frames: dict[str, pl.LazyFrame] = (
        unshaped_frames if unshaped_frames is not None else {}
    )
    captures = _PlannedCaptures(
        snapshot_plan,
        requested_graph,
        execution_context=execution_context,
        incoming_edges_by_target=incoming_edges_by_target,
    )
    if decision is not None:
        # Sources first: they have no parents, so this stays topological, and
        # every input the run reads is bound before anything is collected.
        execution_order = [nid for nid in order if not parents_of.get(nid)] + [
            nid for nid in order if parents_of.get(nid)
        ]
    inputs_verified = decision is None
    deferred_source_captures: list[str] = []

    def _verify_then_capture_sources() -> None:
        # Every source is bound before this runs; nothing is collected until
        # the inputs are proven to be the ones the plan was resolved against.
        nonlocal inputs_verified
        captures.verify_inputs()
        inputs_verified = True
        for source_id in deferred_source_captures:
            captured = captures.capture(
                source_id, lazy_outputs[source_id], captures.record_closure(source_id)
            )
            lazy_outputs[source_id] = captured
            file_backed_node_ids.add(source_id)
            column_cache[(source_id, None)] = _columns_of(captured)
        deferred_source_captures.clear()

    for nid in execution_order:
        if nid in plan_skipped_nodes:
            continue
        if not inputs_verified and parents_of.get(nid):
            _verify_then_capture_sources()
        if decision is not None and nid in decision.pass_through_edges:
            if execution_context is not None:
                execution_context.checkpoint(label="before_node", node_id=nid)
            edge = decision.pass_through_edges[nid]
            selected = select_edge_source_output(lazy_outputs[edge.source], edge)
            parent_recipe = built_write_recipes.get(edge.source)
            if parent_recipe is not None:
                if parent_recipe.fn is None:
                    built_write_recipes[nid] = WriteRecipe(
                        input=parent_recipe.input,
                        fn=None,
                        reason=parent_recipe.reason,
                        blocking_operator=parent_recipe.blocking_operator,
                    )
                else:
                    # Two conditions, each carrying its own weight. The identity test is the
                    # multi-frame guard: ``select_edge_source_output`` returns the parent's own
                    # object for a single-frame parent, and a different one for a sub-frame the
                    # parent's recipe does not describe. The second is the replacement test,
                    # because ``lazy_outputs[parent]`` is written after a capture or a seed
                    # has already replaced the frame — every replacement site records the
                    # node in ``file_backed_node_ids``.
                    link_proved = (
                        selected is lazy_outputs[edge.source]
                        and edge.source not in file_backed_node_ids
                    )
                    if not link_proved:
                        selected_lf = (
                            selected if isinstance(selected, pl.LazyFrame) else selected.lazy()
                        )
                        try:
                            check_recipe_equivalence(parent_recipe, selected_lf)
                            link_proved = True
                        except RecipeEquivalenceError:
                            link_proved = False
                    if link_proved:
                        pass_through_edge = edge
                        pass_through_config = node_map[nid].data.config

                        def project(
                            lf: pl.LazyFrame,
                            target_edge: GraphEdge = pass_through_edge,
                        ) -> pl.LazyFrame:
                            res = _apply_edge_projection(target_edge, lf)[0]
                            return res if isinstance(res, pl.LazyFrame) else res.lazy()

                        def column_step(
                            lf: pl.LazyFrame,
                            config: dict[str, Any] = pass_through_config,
                        ) -> pl.LazyFrame:
                            out = _apply_selected_columns(lf, config)
                            out = _apply_column_renames(out, config)
                            return out if isinstance(out, pl.LazyFrame) else out.lazy()

                        built_write_recipes[nid] = parent_recipe.then(project).then(column_step)
            passed: pl.LazyFrame | pl.DataFrame
            passed, _projected_cols = _apply_edge_projection(edge, selected)
            passed = _apply_selected_columns(passed, node_map[nid].data.config)
            passed = _apply_column_renames(passed, node_map[nid].data.config)
            lazy_outputs[nid] = passed if isinstance(passed, pl.LazyFrame) else passed.lazy()
            captures.record_closure(nid)
            _release_consumed_parents(nid)
            continue
        seed_frame = seed_outputs.get(nid)
        if seed_frame is not None:
            lazy_outputs[nid] = seed_frame
            column_cache[(nid, None)] = _columns_of(seed_frame)
            logger.info("lazy_seed_hit", node_id=nid)
            if execution_context is not None:
                execution_context.checkpoint(label="lazy_seed_hit", node_id=nid)
            continue
        boundary = boundary_runner.open(nid)
        # A batch Model Score whose output is exactly its scored file writes
        # that file straight into the capture's staging directory.
        scored_capture = (
            captures.stage_scored_output(
                nid, node_map[nid], scenario=node_source_overrides.get(nid, source or "live")
            )
            if decision is not None and inputs_verified
            else None
        )
        with (
            execution_context.stage("lazy_build", node_id=nid)
            if execution_context is not None
            else contextlib.nullcontext()
        ):
            scored_prewritten = False
            scored_digest: str | None = None
            if scored_capture is None:
                lf, is_source, node = _build_lazy_node(boundary)
            else:
                from haute._model_scorer import model_score_output_destination

                try:
                    with model_score_output_destination(
                        scored_capture.part_path(0)
                    ) as score_destination:
                        lf, is_source, node = _build_lazy_node(boundary)
                    scored_prewritten = score_destination.used
                    scored_digest = score_destination.digest
                except BaseException:
                    scored_capture.close()
                    raise

        if decision is not None and not inputs_verified and nid in decision.captures:
            deferred_source_captures.append(nid)
        elif decision is not None:
            closure = captures.record_closure(nid)
            if nid in decision.captures:
                lf = captures.capture(
                    nid,
                    lf,
                    closure,
                    artifact=scored_capture,
                    prewritten=scored_prewritten,
                    prewritten_digest=scored_digest,
                    join=built_join_recipes.get(nid),
                    recipe=built_write_recipes.get(nid),
                    unshaped_columns=(
                        _schema_pairs(built_unshaped_frames[nid])
                        if nid in built_unshaped_frames
                        else None
                    ),
                )
                file_backed_node_ids.add(nid)
                column_cache[(nid, None)] = _columns_of(lf)
                _release_consumed_parents(nid)
                materialisations_since_gc += 1
                if materialisations_since_gc >= _GC_BATCH_INTERVAL:
                    gc.collect()
                    _malloc_trim()
                    materialisations_since_gc = 0

        lazy_outputs[nid] = lf

    if not inputs_verified:
        _verify_then_capture_sources()
    return lazy_outputs, order, parents_of, id_to_name


def _check_snapshot_plan(
    decision: SeedPlanDecision,
    graph: PipelineGraph,
    *,
    target_node_id: str | None,
    source: str,
    profile: ExecutionProfile,
) -> None:
    """A plan runs only the execution it was resolved for."""
    from haute._seed_plans import seed_plan_lineage_fingerprint

    if (
        target_node_id != decision.target_node_id
        or (source or "live") != decision.source
        or ExecutionProfile(profile) != decision.profile
        or seed_plan_lineage_fingerprint(graph, decision.target_node_id)
        != decision.lineage_fingerprint
    ):
        raise ValueError("The seed plan was resolved for a different execution")


class _PlannedCaptures:
    """Dependency closures and captures of one planned lazy execution."""

    def __init__(
        self,
        plan: SeedPlan | None,
        graph: PipelineGraph,
        *,
        execution_context: ExecutionContext | None,
        incoming_edges_by_target: Mapping[str, Sequence[GraphEdge]],
    ) -> None:
        self.plan = plan
        self.graph = graph
        self.execution_context = execution_context
        self.incoming_edges_by_target = incoming_edges_by_target
        self.closures: dict[str, dict[str, str]] = {}
        self.published: dict[str, tuple[str, str]] = {}

    @property
    def _decision(self) -> SeedPlanDecision:
        assert self.plan is not None
        return self.plan.decision

    def _effective_edges(self, node_id: str) -> Sequence[GraphEdge]:
        edge = self._decision.pass_through_edges.get(node_id)
        if edge is not None:
            return (edge,)
        return self.incoming_edges_by_target.get(node_id, ())

    def record_closure(self, node_id: str) -> dict[str, str]:
        """The generations *node_id*'s frame is computed from, recorded on the plan.

        Each seed and published capture read upstream contributes itself and
        the generations it was built from; a capture that kept its own
        artifact contributes only what it was built from.
        """
        decision = self._decision
        closure: dict[str, str] = {}
        for edge in self._effective_edges(node_id):
            parent = edge.source
            seed = decision.seeds.get(parent)
            if seed is not None:
                closure[seed.identity.digest] = seed.generation_id
                closure.update(seed.dependencies)
                continue
            published = self.published.get(parent)
            if published is not None:
                digest, generation_id = published
                closure[digest] = generation_id
            closure.update(self.closures.get(parent, {}))
        self.closures[node_id] = closure
        assert self.plan is not None
        self.plan.record_closure(node_id, closure)
        return closure

    def _inputs_changed(self) -> bool:
        from haute._seed_plans import seed_plan_input_fingerprint

        decision = self._decision
        return (
            seed_plan_input_fingerprint(
                self.graph, decision.executed_node_ids, source=decision.source
            )
            != decision.runtime_input_fingerprint
        )

    def verify_inputs(self) -> None:
        """Fail before anything is collected if the run's inputs moved since planning."""
        from haute.errors import SnapshotPlanInputsChangedError

        if self._inputs_changed():
            raise SnapshotPlanInputsChangedError(target_node_id=self._decision.target_node_id)

    def stage_scored_output(
        self, node_id: str, node: GraphNode, *, scenario: str
    ) -> NodeSnapshotArtifact | None:
        """Stage a batch Model Score capture whose scored file is its whole output.

        Only for a captured Model Score scoring in batch (any scenario but
        ``live``) whose output is exactly what the scorer writes: no
        post-processing code, no selected columns, no renames. Anything else
        is sunk after the node is built, like every other capture.
        """
        assert self.plan is not None
        capture = self.plan.decision.captures.get(node_id)
        config = node.data.config
        if (
            capture is None
            or node.data.nodeType != NodeType.MODEL_SCORE
            or scenario == "live"
            or str(config.get("code") or "").strip()
            or config.get("selected_columns")
            or config.get("column_renames")
        ):
            return None
        return self.plan.store.stage_node_output(
            capture.identity, staging_token=self.plan.staging_token
        )

    def capture(
        self,
        node_id: str,
        frame: _Frame,
        closure: Mapping[str, str],
        *,
        artifact: NodeSnapshotArtifact | None = None,
        prewritten: bool = False,
        prewritten_digest: str | None = None,
        join: JoinRecipe | None = None,
        recipe: WriteRecipe | None = None,
        unshaped_columns: Sequence[tuple[str, str]] | None = None,
    ) -> pl.LazyFrame:
        """Write one capture point through the chunked writer and continue from it.

        With ``prewritten``, *artifact* already holds the node's output — a
        batch Model Score's scored file — and is published without a second
        write. With ``join``, the recipe *frame* was built from, an edge join
        is written a driving chunk at a time. With ``recipe``, the write recipe
        *frame* was built from, a chunk-local single-input node is written a
        slice of its input at a time.
        """
        from haute._node_snapshots import (
            NodeSnapshotColumns,
            NodeSnapshotMultiFrameUnsupportedError,
        )

        plan = self.plan
        assert plan is not None
        capture = plan.decision.captures[node_id]
        if isinstance(frame, dict):
            raise NodeSnapshotMultiFrameUnsupportedError(
                "A node that emits several frames cannot be captured as one snapshot."
            )
        store = plan.store
        if artifact is None:
            artifact = store.stage_node_output(capture.identity, staging_token=plan.staging_token)
        sink_lf = artifact.lazy_frame() if prewritten else frame
        if isinstance(sink_lf, pl.DataFrame):
            sink_lf = sink_lf.lazy()
        try:
            schema_cols = sink_lf.collect_schema().names()
        except BaseException:
            artifact.close()
            raise
        if capture.columns.names is None:
            columns = NodeSnapshotColumns.all()
        else:
            wanted = set(capture.columns.names)
            missing = wanted - set(schema_cols)
            strict = capture.strict_columns.names
            strict_missing = missing & set(strict) if strict is not None else missing
            if strict_missing:
                artifact.close()
                raise ContractMismatchError(
                    "A captured node's output lacks columns this run reads from it.",
                    node_id=node_id,
                    missing=sorted(strict_missing),
                    output_columns=sorted(schema_cols),
                )
            if missing:
                logger.warning(
                    "snapshot_capture_column_unavailable",
                    node_id=node_id,
                    missing=sorted(missing),
                )
            ordered = projected_or_carrier_columns(schema_cols, wanted - missing)
            sink_lf = sink_lf.select(ordered)
            if join is not None:
                join = join.then(lambda lf: lf.select(ordered))
            if recipe is not None:
                recipe = recipe.then(lambda lf: lf.select(ordered))
            # A scored file holds what the scorer was asked to write; it is
            # published as that whole file.
            columns = NodeSnapshotColumns.of(schema_cols if prewritten else ordered)
        context = self.execution_context
        written: ChunkedWrite | None = None
        try:
            if not prewritten:
                with (
                    context.stage("lazy_snapshot_capture", node_id=node_id)
                    if context is not None
                    else contextlib.nullcontext()
                ):
                    written = write_parts(
                        artifact.directory,
                        sink_lf,
                        join=join,
                        recipe=recipe,
                        chunk_rows=current_streaming_chunk_size(),
                        fast_checkpoint=True,
                        execution_context=context,
                        node_id=node_id,
                    )
                artifact.record_digests(written.digests)
            elif prewritten_digest is not None:
                artifact.record_digests({part_name(0): prewritten_digest})
            _snapshot_fault_point("snapshot_capture_before_publish", node_id)
            if self._inputs_changed():
                # The pre-run check exists to stop exactly this mix: seeds
                # computed from the old inputs read beside branches recomputed
                # from the new ones. Keeping this artifact and carrying on
                # produced that mix in the one case the check cannot cover,
                # because the change happened after it ran. Discard the
                # unfinished staging and stop; whatever published before the
                # change stays published under the identities it was computed
                # for, and nothing further is published.
                from haute.errors import SnapshotPlanInputsChangedError

                artifact.close()
                raise SnapshotPlanInputsChangedError(target_node_id=self._decision.target_node_id)
            publication = store.publish_node_output(
                capture.identity,
                artifact,
                columns=columns,
                dependencies=closure,
                explicit=False,
                profile=plan.decision.profile,
                unshaped_columns=unshaped_columns,
            )
        except SourceCacheCorruptError as exc:
            # The publication rule reports corruption rather than repairing it,
            # deliberately — only an explicit build replaces a corrupt
            # generation. It reports it against an identity, though, which tells
            # the user nothing they can act on, so name the node here where it
            # is known.
            from haute.errors import SnapshotCorruptError

            artifact.close()
            node = self.graph.node_map.get(node_id)
            raise SnapshotCorruptError(
                node_id=node_id,
                node_label=(node.data.label if node is not None else None),
            ) from exc
        except BaseException:
            artifact.close()
            raise
        plan.register_publication(publication)
        if publication.outcome == "published":
            assert publication.generation is not None
            self.published[node_id] = (
                capture.identity.digest,
                publication.generation.generation_id,
            )
            plan.record_published(node_id, publication.generation)
            self._record(
                capture,
                "published",
                publication.generation.generation_id,
                columns,
                written,
            )
        else:
            self._record(capture, "superseded", None, columns, written)
        return publication.lazy_frame

    def _record(
        self,
        capture: CaptureDecision,
        outcome: Literal["published", "superseded"],
        generation_id: str | None,
        columns: NodeSnapshotColumns,
        written: ChunkedWrite | None,
    ) -> None:
        """``written`` is the chunked write, or None for a prewritten scored file."""
        from haute._seed_plans import SharedSnapshotCaptureRecord

        logger.info(
            "shared_snapshot_capture",
            node_id=capture.node_id,
            outcome=outcome,
            generation_id=generation_id,
            write_strategy=written.strategy if written is not None else "prewritten",
            write_parts=written.chunks if written is not None else None,
            write_chunk_rows=written.chunk_rows if written is not None else None,
            write_input_slices=written.input_slices if written is not None else None,
            write_native_reason=written.native_reason if written is not None else None,
            write_blocking_operator=written.blocking_operator if written is not None else None,
        )
        context = self.execution_context
        if context is None:
            return
        context.record_shared_snapshot_capture(
            SharedSnapshotCaptureRecord(
                node_id=capture.node_id,
                identity_digest=capture.identity.digest,
                kind=capture.kind,
                outcome=outcome,
                generation_id=generation_id,
                columns=columns,
                write_strategy=written.strategy if written is not None else "prewritten",
                write_parts=written.chunks if written is not None else None,
                write_chunk_rows=written.chunk_rows if written is not None else None,
                write_staged_inputs=written.staged_inputs if written is not None else None,
                write_input_slices=written.input_slices if written is not None else None,
                write_native_reason=written.native_reason if written is not None else None,
                write_blocking_operator=written.blocking_operator if written is not None else None,
            )
        )
        if outcome == "superseded":
            context.record_execution_warning("snapshot_capture_superseded", node_id=capture.node_id)


# ---------------------------------------------------------------------------
# Eager execution core — shared by executor (preview) and trace
# ---------------------------------------------------------------------------


def _build_funcs(
    order: list[str],
    node_map: dict[str, GraphNode],
    id_to_name: dict[str, str],
    all_parents: Mapping[str, list[str]],
    build_node_fn: Callable,
    *,
    incoming_edges_by_target: Mapping[str, Sequence[GraphEdge]],
    all_incoming_edges_by_target: Mapping[str, Sequence[GraphEdge]],
    all_node_map: Mapping[str, GraphNode],
    row_limit: int | None = None,
    preamble_ns: dict | None = None,
    source: str = "live",
    source_by_node: Mapping[str, str] | None = None,
    required_output_columns_by_node: Mapping[str, frozenset[str] | set[str] | None] | None = None,
    required_output_columns_by_port_by_node: Mapping[
        str,
        Mapping[str, frozenset[str] | None],
    ]
    | None = None,
    reuse_loaded_model_by_node: Mapping[str, bool] | None = None,
    execution_profile: ExecutionProfile | None = None,
    schema_only: bool = False,
    submodels: Mapping[str, Any] | None = None,
) -> dict[str, tuple[Callable, bool]]:
    """Build per-node executable functions from the graph.

    Shared between eager and lazy paths.  ``row_limit`` is forwarded to
    ``build_node_fn`` so Databricks sources can push LIMIT into SQL.
    ``preamble_ns`` is a compiled namespace of user-defined helpers from
    the pipeline file's preamble section.
    ``source`` is the active execution source forwarded to build_node_fn.
    ``source_by_node`` overrides that builder source for individual nodes
    without changing graph pruning/source-switch routing.
    ``reuse_loaded_model_by_node`` opts selected modelScore nodes into
    scorer-instance model reuse for chunked callers.
    ``schema_only`` forwards the caller's schema-only declaration to every
    builder, so a builder that would otherwise materialise at build time
    (OUTPUT) honours it.
    """
    funcs: dict[str, tuple[Callable, bool]] = {}
    node_source_overrides = source_by_node or {}
    original_node_map = dict(all_node_map)
    for nid in order:
        connected_edges = [
            edge for edge in incoming_edges_by_target.get(nid, []) if edge.source in id_to_name
        ]
        src_ids = [edge.source for edge in connected_edges]
        target_handles = [edge.targetHandle for edge in connected_edges]
        # OUTPUT uses each source port to distinguish frames from one apiInput.
        src_ports = [edge.sourceHandle or id_to_name[edge.source] for edge in connected_edges]
        src_names: list[str] = []
        for edge in connected_edges:
            source_node = node_map[edge.source]
            try:
                src_names.append(edge_input_name(edge, source_node, submodels=submodels))
            except ValueError:
                # Preview reports null-handle routing errors on the consumer.
                if not (
                    source_node.data.nodeType == NodeType.API_INPUT and edge.sourceHandle is None
                ):
                    raise
        duplicates = duplicate_input_names(src_names)
        if duplicates:
            raise ConfigError(
                f"Node {nid!r} has duplicate input name(s) derived from its "
                f"incoming edges: {duplicates!r}.",
                node_id=nid,
                duplicate_input_names=duplicates,
            )
        orig_src_names = resolve_orig_source_names(
            node_map[nid],
            original_node_map,
            {target: list(edges) for target, edges in all_incoming_edges_by_target.items()},
        )
        node_source = node_source_overrides.get(nid, source)
        _, fn, is_source = build_node_fn(
            node_map[nid],
            source_names=src_names,
            source_ids=src_ids,
            target_handles=target_handles,
            source_ports=src_ports,
            row_limit=row_limit,
            node_map=node_map,
            orig_source_names=orig_src_names,
            upstream_ids=upstream_node_ids(nid, all_parents),
            preamble_ns=preamble_ns,
            source=node_source,
            required_output_columns=(
                required_output_columns_by_node.get(nid)
                if required_output_columns_by_node is not None
                else None
            ),
            required_output_columns_by_port=(
                required_output_columns_by_port_by_node.get(nid)
                if required_output_columns_by_port_by_node is not None
                else None
            ),
            reuse_loaded_model=(
                bool(reuse_loaded_model_by_node.get(nid))
                if reuse_loaded_model_by_node is not None
                else False
            ),
            execution_profile=execution_profile.value if execution_profile is not None else None,
            schema_only=schema_only,
        )
        funcs[nid] = (fn, is_source)
    return funcs


def _extract_error_line(exc: Exception) -> int | None:
    """Extract user-code line number from an exception, if available.

    - SyntaxError: use .lineno (already adjusted by _exec_user_code).
    - _user_code_line attr: set by _exec_user_code from the traceback
      for runtime errors like NameError that don't embed line info
      in their message string.
    - Fallback: parse 'line N' from the error message
      (already adjusted by _exec_user_code's regex substitution).
    - Returns None when no line info is available.
    """
    if isinstance(exc, SyntaxError) and exc.lineno is not None:
        return exc.lineno
    user_line: int | None = getattr(exc, "_user_code_line", None)
    if user_line is not None:
        return int(user_line)
    match = re.search(r"\bline (\d+)\b", str(exc))
    if match:
        return int(match.group(1))
    return None


class EagerResult(NamedTuple):
    """Result of eager graph execution."""

    # ``outputs`` may carry a ``dict[port_label, DataFrame]`` for
    # multi-frame apiInput sources; non-apiInput nodes always emit a
    # single ``DataFrame`` or ``None`` on failure.
    outputs: dict[str, pl.DataFrame | dict[str, pl.DataFrame] | None]
    order: list[str]
    parents_of: dict[str, list[str]]
    node_map: dict[str, GraphNode]
    id_to_name: dict[str, str]
    errors: dict[str, str]
    timings: dict[str, float]
    memory_bytes: dict[str, int]
    error_lines: dict[str, int]
    available_columns: dict[str, list[tuple[str, str]]]
    output_columns: dict[str, list[tuple[str, str]]]
    # Per-(node_id, port_label) name+dtype schema for multi-frame emitters.
    # Populated from the collected frames for a materialised target and
    # from ``collect_schema()`` (no materialisation) for a lazy ancestor,
    # so per-frame columns are available WITHOUT collecting the ancestor.
    # Empty for single-frame nodes (their schema is in ``output_columns``).
    frame_columns: dict[tuple[str, str], list[tuple[str, str]]]
    # Uncapped runtime plan (or per-frame plans) of every successful node.
    # Row-limited collections never feed consumers, so these are the plans a
    # downstream node — or a trace lineage lookup — reads.
    plans: dict[str, pl.LazyFrame | dict[str, pl.LazyFrame]]


def _declared_api_input_frame_schema_items(
    node: GraphNode,
) -> dict[str, list[tuple[str, str]]]:
    """Return all declared emitting-port schemas without opening payloads."""
    if node.data.nodeType is not NodeType.API_INPUT or not isinstance(
        node.data.config.get("tables"),
        list,
    ):
        return {}
    from haute._json_shred._shred import _declared_frame_schema, _emitting_table_specs

    declared: dict[str, list[tuple[str, str]]] = {}
    for table_spec in _emitting_table_specs(node.data.config):
        schema = _declared_frame_schema(table_spec)
        declared[table_spec.label] = [(name, str(schema[name])) for name in schema.names()]
    return declared


@runtime_project_root_scoped
def _execute_eager_core(
    graph: PipelineGraph,
    build_node_fn: Callable,
    target_node_id: str | None = None,
    row_limit: int | None = None,
    swallow_errors: bool = False,
    preamble_ns: dict | None = None,
    source: str = "live",
    enforce_contracts: bool = True,
    required_columns_by_node: Mapping[str, Iterable[str] | projection_planner.AllExceptColumns]
    | None = None,
    materialize_node_ids: set[str] | frozenset[str] | None = None,
    materialize_column_limits_by_node: Mapping[str, int] | None = None,
    execution_context: ExecutionContext | None = None,
    row_limits_by_node: Mapping[str, int] | None = None,
    snapshot_plan: SeedPlan | None = None,
) -> EagerResult:
    """Execute the graph eagerly in topo order and collect DataFrames.

    Shared core for the preview executor and the trace engine.

    Args:
        graph: React Flow graph.
        build_node_fn: ``(node, source_names=..., ...) -> (name, fn, is_source)``.
        target_node_id: If set, only execute ancestors of this node.
        row_limit: Collect each materialised node limited to this many rows
            (SQL ``LIMIT`` semantics). Sources are never capped: a limited
            collection never feeds a consumer, which reads the node's uncapped
            plan, and Polars pushes each collection's slice upstream only where
            the result is unchanged. Builders also receive it as the
            interactive-execution signal.
        row_limits_by_node: Per-node collection limits overriding
            ``row_limit`` (trace collects each ancestor to its own prefix).
        swallow_errors: If ``True``, record per-node errors and continue
            (preview behaviour).  If ``False``, raise immediately (trace).
        source: Active execution source (``"live"`` = eager scoring).
        enforce_contracts: If ``True`` (default), assert each node's
            column contract at its input and output boundaries. Contract and
            schema mismatches always raise regardless of *swallow_errors* —
            they are API-level claims and a silent error would defeat the
            adoption effort.
        required_columns_by_node: Optional exact output-column demand for
            caller-consumed nodes.  Eager preview uses this to collect only
            the visible target columns while still reporting the full schema.
        materialize_node_ids: Optional set of nodes whose outputs should be
            collected into concrete DataFrames.  ``None`` preserves the
            traditional eager behaviour and materialises every executed node.
            Target-only preview passes ``{target_node_id}`` so ancestors stay
            lazy while still participating in schema, contract, and projection
            planning.
        materialize_column_limits_by_node: Optional per-node cap on the
            columns collected into materialised DataFrames.  The full output
            schema is still reported from ``collect_schema()`` before this
            cap is applied.  Used by first-click preview when the frontend
            has not yet sent explicit requested preview columns.
        snapshot_plan: A leased seed plan (``haute._seed_plans``) for this
            exact execution. Only its seeds and executed nodes run: a seeded
            node's frame is its generation, nothing above a seed is built, a
            pass-through node reads only its selected edge, and each capture
            point is sunk into the shared store before anything below it is
            collected, which then reads what was written. The row limit still
            applies only when a node is collected. A store failure while
            capturing propagates; it is never recorded as a node error.

    Returns:
        An ``EagerResult`` with named fields for outputs, order,
        parents_of, node_map, id_to_name, errors, timings, and
        memory_bytes.
    """
    prepared_execution = _prepare_execution(
        PreparedExecutionRequest(
            graph=graph,
            target_node_id=target_node_id,
            source=source,
            required_columns_by_node=required_columns_by_node,
            profile=execution_context.profile if execution_context is not None else None,
        )
    )
    requested_graph = graph
    graph = prepared_execution.graph
    graph_plan = prepared_execution.graph_plan
    node_map = graph_plan.node_map
    order = graph_plan.order
    parents_of = graph_plan.parents_of
    id_to_name = graph_plan.id_to_name
    relevant_edges = graph_plan.relevant_edges
    normalised_required_columns = prepared_execution.normalised_required_columns
    decision = snapshot_plan.decision if snapshot_plan is not None else None
    # The plan decided what runs: its seeds and the nodes still executed below
    # them. Projection is still planned over the whole lineage, as the plan was.
    run_order = order
    seeded_ids: frozenset[str] = frozenset()
    if decision is not None:
        _check_snapshot_plan(
            decision,
            requested_graph,
            target_node_id=target_node_id,
            source=source,
            profile=(
                execution_context.profile
                if execution_context is not None
                else ExecutionProfile.PREVIEW_EAGER
            ),
        )
        seeded_ids = frozenset(decision.seeds)
        planned_ids = set(decision.executed_node_ids) | seeded_ids
        run_order = [node_id for node_id in order if node_id in planned_ids]
    materialized_ids = None if materialize_node_ids is None else frozenset(materialize_node_ids)
    node_row_limits = dict(row_limits_by_node or {})
    for limit_node_id, node_limit in node_row_limits.items():
        if not isinstance(limit_node_id, str) or not limit_node_id:
            raise ValueError("row_limits_by_node keys must be node ids")
        if type(node_limit) is not int or node_limit < 1:
            raise ValueError("row limits must be positive integers")

    def collection_row_limit(node_id: str) -> int | None:
        return node_row_limits.get(node_id, row_limit or None)

    materialize_column_limits = dict(materialize_column_limits_by_node or {})
    for limit_node_id, limit in materialize_column_limits.items():
        if not isinstance(limit_node_id, str) or not limit_node_id:
            raise ValueError("materialize_column_limits_by_node keys must be node ids")
        if type(limit) is not int or limit < 1:
            raise ValueError("materialize column limits must be positive integers")

    # Full parent lookup from ALL edges for instance resolution
    all_parents = prepared_execution.all_parents

    # Per-target incoming-edge lookup (eager path). Use ``relevant_edges``
    # so live-switch pruning is honoured.
    incoming_edges_by_target = prepared_execution.incoming_edges_by_target
    all_incoming_edges_by_target = prepared_execution.all_incoming_edges_by_target

    # Fan-out count per node — how many direct children consume this
    # node's output.  Used to add a Polars ``.cache()`` hint when the
    # parent feeds >1 consumer so the optimiser reuses one materialized
    # plan across branches (diamond graphs) instead of duplicating the
    # upstream work.  A parent may be either a concrete DataFrame
    # (traditional eager preview/trace) or a LazyFrame (target-only
    # preview), and both can carry the hint into downstream collection.
    children_of = prepared_execution.children_of

    # Count fan-out by the selected source frame, rather than only by node.
    # A multi-frame producer can expose multiple independent source ports.
    frame_fanout_count: dict[tuple[str, str | None], int] = {}
    for edge in relevant_edges:
        if edge.source in node_map and edge.target in node_map:
            frame_key = (edge.source, edge.sourceHandle)
            frame_fanout_count[frame_key] = frame_fanout_count.get(frame_key, 0) + 1

    context_strategy = execution_context.projection_plan if execution_context is not None else None
    # Under a plan, captures widen demand before planning exactly as the plan
    # negotiated, so a capture writes every column its generation must keep;
    # what the caller collects is still its own demand.
    planning_required_columns: dict[str, set[str] | projection_planner.AllExceptColumns] = dict(
        normalised_required_columns
    )
    if decision is not None:
        planning_required_columns = {
            node_id: (
                demand if isinstance(demand, projection_planner.AllExceptColumns) else set(demand)
            )
            for node_id, demand in decision.planning_required_columns.items()
        }
    if planning_required_columns:
        projection_plan = projection_planner.compute_prepared_plan(
            order,
            children_of,
            node_map,
            required_columns_by_node=planning_required_columns,
            relevant_edges=relevant_edges,
            submodels=graph.submodels,
            selector_aliases=preamble_selector_aliases(graph.preamble or ""),
        )
    else:
        projection_plan = None
    needed_cols: Mapping[str, frozenset[str] | None] = (
        projection_plan.needed_by_node if projection_plan is not None else {}
    )
    # A collected node collects the caller's own demand: under a plan the
    # negotiated demand above is only what is read, built, and captured.
    collect_needed_cols: Mapping[str, frozenset[str] | None] = needed_cols
    if planning_required_columns != normalised_required_columns:
        collect_needed_cols = (
            projection_planner.compute_prepared_plan(
                order,
                children_of,
                node_map,
                required_columns_by_node=normalised_required_columns,
                relevant_edges=relevant_edges,
                submodels=graph.submodels,
                selector_aliases=preamble_selector_aliases(graph.preamble or ""),
            ).needed_by_node
            if normalised_required_columns
            else {}
        )
    builder_needed_cols = projection_planner.builder_required_output_columns_by_node(
        node_map,
        needed_cols,
        preserve_eager_model_score_inputs=True,
    )
    # Public strategy planning also runs for an unseeded first-click preview.
    # Reuse that proof only at the API port-loading seam: applying its complete
    # node demands as eager output projections would change established output
    # and schema-reporting semantics for unrelated nodes. Under a plan the
    # ports load the negotiated demand, which the caller's strategy never saw.
    port_projection_plan = (
        context_strategy.projection_plan
        if decision is None
        and isinstance(context_strategy, projection_planner.ExecutionStrategyResult)
        else projection_plan
    )
    api_port_columns_by_node = (
        projection_planner.api_input_port_columns_by_node(
            node_map,
            relevant_edges,
            port_projection_plan,
        )
        if port_projection_plan is not None
        else {}
    )

    funcs = _build_funcs(
        [node_id for node_id in run_order if node_id not in seeded_ids],
        node_map,
        id_to_name,
        all_parents,
        build_node_fn,
        incoming_edges_by_target=incoming_edges_by_target,
        all_incoming_edges_by_target=all_incoming_edges_by_target,
        all_node_map=graph.node_map,
        row_limit=row_limit,
        preamble_ns=preamble_ns,
        source=source,
        required_output_columns_by_node=builder_needed_cols,
        required_output_columns_by_port_by_node=api_port_columns_by_node,
        execution_profile=execution_context.profile if execution_context is not None else None,
        submodels=graph.submodels,
    )
    boundary_runner = NodeBoundaryRunner(
        prepared=prepared_execution,
        funcs=funcs,
        enforce_contracts=enforce_contracts,
        execution_context=execution_context,
        needed_columns=needed_cols,
    )

    # Value can be a single frame, None on failure, OR a per-frame dict
    # (multi-frame apiInput emits ``dict[port_label, DataFrame]`` — see
    # the ``materialised`` assignment in the dict-emit branch below).
    eager_outputs: dict[str, pl.DataFrame | dict[str, pl.DataFrame] | None] = {}
    runtime_outputs: dict[
        str,
        pl.LazyFrame | pl.DataFrame | dict[str, pl.DataFrame] | dict[str, pl.LazyFrame] | None,
    ] = {}
    # Reuse one cache node per lazy producer frame. DataFrames are already
    # materialised and deliberately never enter this mapping.
    cached_lazy_frames: dict[tuple[str, str | None], pl.LazyFrame] = {}
    errors: dict[str, str] = {}
    error_lines: dict[str, int] = {}
    timings: dict[str, float] = {}
    memory_bytes: dict[str, int] = {}
    available_columns: dict[str, list[tuple[str, str]]] = {}
    output_columns: dict[str, list[tuple[str, str]]] = {}

    # Per-node column sets used by the boundary contract checks.  We
    # compute each frame's column set exactly once and reuse it — both
    # as an output check for the producing node and as an input check
    # for its consumer(s).  Polars' ``.columns`` is O(n) in the number
    # of columns, but frozenset construction dominates anyway; caching
    # keeps the contract-enforced path within the <5% budget.
    #
    # Same shape change as the lazy path: keyed by
    # ``(producer_node_id, port_name_or_None)`` so multi-frame consumers
    # don't collide.
    column_cache: dict[tuple[str, str | None], frozenset[str]] = {}

    # Parallel to ``column_cache`` but dtype-carrying and per-frame: maps
    # ``(producer_node_id, port_label) -> list[(name, dtype)]`` for
    # multi-frame emitters (a multi-table apiInput today). column_cache
    # stays ``frozenset[str]`` for the contract checks; this lookup is the
    # additive name+dtype carrier that lets a NON-materialised multi-frame
    # ancestor expose its per-frame schema (via ``collect_schema()``, no
    # collect) exactly as a materialised target does (from the collected
    # frames). Single-frame nodes never populate this.
    frame_schema_cache: dict[tuple[str, str], list[tuple[str, str]]] = {}

    def _schema_items_of(frame: pl.LazyFrame | pl.DataFrame) -> list[tuple[str, str]]:
        lazy_frame = frame if isinstance(frame, pl.LazyFrame) else frame.lazy()
        schema = lazy_frame.collect_schema()
        return [(name, str(schema[name])) for name in schema.names()]

    def _full_model_score_schema(
        node_id: str,
        node: GraphNode,
        actual_columns: list[tuple[str, str]],
    ) -> list[tuple[str, str]]:
        config = node.data.config
        if not _is_plain_model_score(node):
            return actual_columns

        parent_ids = parents_of.get(node_id, [])
        if not parent_ids:
            return actual_columns
        parent_columns = output_columns.get(parent_ids[0])
        if parent_columns is None:
            return actual_columns

        actual_by_name = dict(actual_columns)
        generated_names = [str(config.get("output_column") or "prediction")]
        proba_col = f"{generated_names[0]}_proba"
        if proba_col in actual_by_name:
            generated_names.append(proba_col)

        seen: set[str] = set()
        full_columns: list[tuple[str, str]] = []
        for name, dtype in parent_columns:
            full_columns.append((name, actual_by_name.get(name, dtype)))
            seen.add(name)
        for name in generated_names:
            if name in seen or name not in actual_by_name:
                continue
            full_columns.append((name, actual_by_name[name]))
            seen.add(name)
        return full_columns

    recorded_runtime_edge_demands: dict[projection_planner.ProjectionEdgeKey, frozenset[str]] = {}
    recorded_runtime_resolved_parents: set[str] = set()

    planned = _PlannedCaptures(
        snapshot_plan,
        requested_graph,
        execution_context=execution_context,
        incoming_edges_by_target=incoming_edges_by_target,
    )
    # A capture's store failure, which must reach the caller as the store's
    # error rather than become the node's.
    capture_store_failures: list[BaseException] = []
    # Edge joins built here, so a capture of one can be written in chunks.
    eager_join_recipes: dict[str, JoinRecipe] = {}
    prebound_sources: dict[str, tuple[NodeBoundary, Any, BaseException | None]] = {}
    if decision is not None and snapshot_plan is not None:
        from haute._seed_plans import SharedSnapshotCaptureSkipRecord, SharedSnapshotSeedRecord

        for seed_node_id, seed in decision.seeds.items():
            if execution_context is not None:
                execution_context.record_shared_snapshot_seed(
                    SharedSnapshotSeedRecord(
                        node_id=seed_node_id,
                        identity_digest=seed.identity.digest,
                        generation_id=seed.generation_id,
                        columns=seed.demand,
                    )
                )
        if execution_context is not None:
            for skip_node_id, reason in sorted(decision.skipped_captures.items()):
                execution_context.record_shared_snapshot_capture_skip(
                    SharedSnapshotCaptureSkipRecord(
                        node_id=skip_node_id,
                        reason=reason,
                    )
                )
        # Every source is bound before anything is collected, and the inputs
        # are proven to be the ones the plan was resolved against.
        for nid in run_order:
            if nid in seeded_ids or parents_of.get(nid):
                continue
            source_boundary = boundary_runner.open(nid)
            if not source_boundary.is_source:
                continue
            try:
                bound = boundary_runner.invoke(source_boundary)
            except Exception as exc:  # recorded at the node, as an unplanned run would
                prebound_sources[nid] = (source_boundary, None, exc)
            else:
                prebound_sources[nid] = (source_boundary, bound, None)
        planned.verify_inputs()

    for nid in run_order:
        seeded = nid in seeded_ids
        prebound = prebound_sources.pop(nid, None)
        if seeded:
            # A seed's frame is its generation: nothing is built or checked
            # for it, and it is never selected, renamed, or captured again.
            boundary = NodeBoundary(
                node_id=nid,
                node=node_map[nid],
                fn=_passthrough_fn,
                is_source=True,
                parent_ids=(),
                incoming_edges=(),
                contract=None,
                check_contract=False,
                is_passthrough_runtime=False,
            )
        elif prebound is not None:
            boundary = prebound[0]
        else:
            boundary = boundary_runner.open(nid)
        if decision is not None and nid in decision.pass_through_edges:
            # A pass-through node is its selected input; its other inputs
            # were never built for it.
            selected_edge = decision.pass_through_edges[nid]
            boundary = replace(
                boundary,
                parent_ids=(selected_edge.source,),
                incoming_edges=(selected_edge,),
                check_contract=False,
                is_passthrough_runtime=True,
            )
        is_source = boundary.is_source
        node = boundary.node
        contract = boundary.contract
        check_here = boundary.check_contract
        # A node that the builder chose to wire to ``_passthrough_fn`` is
        # running in a stub/unconfigured state (MODEL_SCORE without a
        # loaded model, OPTIMISER_APPLY without an artifact, etc.).  Its
        # contract describes the *configured* shape, which the runtime
        # intentionally does not produce yet.  Skip the output-side
        # check to preserve the "drag node onto canvas, configure later"
        # UX while still enforcing contracts the moment a real function
        # is wired in.
        is_passthrough_runtime = boundary.is_passthrough_runtime
        t0 = time.perf_counter()
        try:
            if seeded:
                assert snapshot_plan is not None
                result = snapshot_plan.seed_frame(nid)
            elif prebound is not None:
                _, result, prebind_error = prebound
                if prebind_error is not None:
                    raise prebind_error
            elif is_source:
                result = boundary_runner.invoke(boundary)
            else:
                input_ids = list(boundary.parent_ids)
                missing_parents = [pid for pid in input_ids if pid not in runtime_outputs]
                if missing_parents:
                    raise ValueError(
                        f"Node '{nid}' is missing input(s) from: {missing_parents}. "
                        "Upstream node(s) may not have been registered."
                    )
                failed_parents = [pid for pid in input_ids if runtime_outputs[pid] is None]
                if failed_parents:
                    eager_outputs[nid] = None
                    runtime_outputs[nid] = None
                    parent_errors = [
                        f"{pid}: {errors[pid]}" if pid in errors else f"{pid}: failed"
                        for pid in failed_parents
                    ]
                    errors[nid] = "Upstream node(s) failed: " + "; ".join(parent_errors)
                    for pid in failed_parents:
                        if pid in error_lines:
                            error_lines[nid] = error_lines[pid]
                            break
                    continue
                # Add ``.cache()`` on parents that feed >1 consumer so a
                # downstream ``.collect()`` re-uses the materialised plan
                # across branches instead of duplicating upstream work.
                # This is the diamond optimisation: src -> (left, right)
                # -> sink should compute src's plan once, not twice.
                # Parents with exactly one consumer skip the hint — it's
                # cheap but non-zero overhead and adds no value there.
                # Eager path: resolve each incoming edge's frame via
                # ``_pick_source_frame`` so multi-frame sources (apiInput
                # emitting a per-frame dict) route per-edge by
                # ``edge.sourceHandle``. Single-frame sources pass through.
                input_lfs = []
                incoming_edges_for_node = boundary.incoming_edges
                for edge in incoming_edges_for_node:
                    pid = edge.source
                    if pid not in runtime_outputs:
                        continue
                    parent_frame = runtime_outputs[pid]
                    if parent_frame is None:
                        continue
                    picked = _pick_source_frame(parent_frame, edge)
                    frame_key = (pid, edge.sourceHandle)
                    if (
                        isinstance(picked, pl.LazyFrame)
                        and frame_fanout_count.get(frame_key, 0) > 1
                    ):
                        parent_lf = cached_lazy_frames.get(frame_key)
                        if parent_lf is None:
                            parent_lf = picked.cache()
                            cached_lazy_frames[frame_key] = parent_lf
                    else:
                        parent_lf = picked if isinstance(picked, pl.LazyFrame) else picked.lazy()
                    input_lfs.append(parent_lf)
                if not input_lfs:
                    raise ValueError(
                        f"No input data available for node '{nid}'",
                    )

                runtime_edge_demands = _runtime_lineage_demands(
                    node,
                    incoming_edges_for_node,
                    input_lfs,
                    needed_cols.get(nid),
                    projection_plan.edge_demands if projection_plan is not None else {},
                    node_map,
                    graph.submodels,
                    preamble_selector_aliases(graph.preamble or ""),
                )
                if runtime_edge_demands:
                    projected_inputs: list[pl.LazyFrame] = []
                    for incoming_edge, input_lf in zip(
                        incoming_edges_for_node,
                        input_lfs,
                        strict=True,
                    ):
                        demand = runtime_edge_demands.get(
                            projection_planner.ProjectionEdgeKey.from_edge(incoming_edge)
                        )
                        if demand is None:
                            projected_inputs.append(input_lf)
                            continue
                        schema_names = input_lf.collect_schema().names()
                        selected = projected_or_carrier_columns(schema_names, demand)
                        projected_inputs.append(input_lf.select(selected))
                    input_lfs = projected_inputs

                    current_strategy = (
                        execution_context.projection_plan if execution_context is not None else None
                    )
                    if isinstance(
                        current_strategy,
                        projection_planner.ExecutionStrategyResult,
                    ):
                        assert execution_context is not None
                        resolved_parent_ids = _runtime_projectable_source_ids(
                            runtime_edge_demands,
                            node_map,
                        )
                        recorded_runtime_edge_demands.update(
                            (key, frozenset(columns))
                            for key, columns in runtime_edge_demands.items()
                        )
                        recorded_runtime_resolved_parents.update(resolved_parent_ids)
                        refined_plan = projection_planner.with_runtime_inferred_streaming_edges(
                            current_strategy.projection_plan,
                            demands_by_edge=runtime_edge_demands,
                            resolved_parent_ids=resolved_parent_ids,
                            relevant_edges=relevant_edges,
                        )
                        previous_diagnostic = current_strategy.diagnostic
                        execution_context.projection_plan = (
                            projection_planner.build_execution_strategy_result(
                                refined_plan,
                                profile=execution_context.profile,
                                order=order,
                                children_of=children_of,
                                node_map=node_map,
                                has_projection_seed=bool(normalised_required_columns),
                                required_columns_by_node=normalised_required_columns,
                                estimated_peak_bytes=(previous_diagnostic.estimated_peak_bytes),
                                raw_estimated_peak_bytes=(
                                    previous_diagnostic.raw_estimated_peak_bytes
                                ),
                                estimate_calibration_factor_basis_points=(
                                    previous_diagnostic.estimate_calibration_factor_basis_points
                                ),
                                estimate_admission_basis=(
                                    previous_diagnostic.estimate_admission_basis
                                ),
                                headroom_bytes=previous_diagnostic.headroom_bytes,
                                assumptions=previous_diagnostic.assumptions,
                                boundary_operators=(
                                    projection_planner.materialising_operators_by_node(
                                        order,
                                        node_map,
                                        relevant_edges=relevant_edges,
                                        submodels=graph.submodels,
                                    )
                                ),
                                **_conservative_strategy_passthrough(previous_diagnostic),
                            )
                        )

                # Input-side contract check: every column the node's
                # contract says it reads must be present upstream.
                # Using the union across all parents matches how the
                # node's function receives inputs — multi-input joins
                # combine them before the contract columns are read.
                if check_here and contract.inputs is not None:  # type: ignore[union-attr]
                    # Key per-edge by (source, sourceHandle) so the union
                    # picks up the right frame's columns for multi-frame
                    # consumers (commit 4).
                    #
                    # A single-frame source stores its columns under
                    # ``(source, None)`` (see the ``column_cache[(nid, None)]``
                    # write below). When the consuming edge carries a non-null
                    # ``sourceHandle`` — an OUTPUT-editor edge names its
                    # ``source_port``, and a flat apiInput → OUTPUT edge is
                    # wired with the handle set — the ``(source, handle)`` key
                    # misses. Fall back to the actual input frame's schema
                    # (``collect_schema()``, no data collect) rather than
                    # treating the upstream as column-less, mirroring the lazy
                    # path's cache-miss fallback in ``_build_lazy_node``.
                    # ``input_lfs`` is edge-aligned here: every parent is
                    # present and non-None past the missing/failed guards above.
                    upstream_col_sets: list[frozenset[str]] = []
                    for edge, input_lf in zip(incoming_edges_for_node, input_lfs, strict=True):
                        cache_key = (edge.source, edge.sourceHandle)
                        cols = column_cache.get(cache_key)
                        if cols is None:
                            cols = frozenset(input_lf.collect_schema().names())
                            column_cache[cache_key] = cols
                        upstream_col_sets.append(cols)
                    # ``.union(*[])`` is ``frozenset()``, so the empty case
                    # (no incoming edges) needs no special handling — though
                    # it cannot occur here: an empty ``input_lfs`` raises above.
                    upstream_cols: frozenset[str] = frozenset().union(*upstream_col_sets)
                    if execution_context is not None:
                        execution_context.record_column_widths(
                            node_id=nid,
                            input_width=sum(len(columns) for columns in upstream_col_sets),
                        )
                    boundary_runner.assert_inputs(boundary, upstream_cols)

                if decision is not None and nid in decision.pass_through_edges:
                    # Its builder expects every input it was wired with; the
                    # plan built only the selected one, which is its output.
                    result = input_lfs[0]
                else:
                    recipe = _edge_join_recipe(boundary.fn, node, input_lfs)
                    if recipe is not None:
                        eager_join_recipes[nid] = recipe
                    result = boundary_runner.invoke(boundary, input_lfs)

            # Multi-frame emit: a source may return ``dict[port_name, frame]``.
            # Materialise each frame's LazyFrame to DataFrame so the preview
            # cache's size accounting (which assumes DataFrame-valued
            # outputs) works on each frame. Downstream edges pick per-edge
            # via ``_pick_source_frame`` from the runtime_outputs dict.
            #
            # Use ``streaming_collect`` (not bare ``.collect()``) so the
            # bounded-memory contract holds in profiled execution paths.
            # `test_bounded_collect_contracts` enforces that bounded
            # modules never call ``.collect()`` directly.
            if isinstance(result, dict):
                declared_frame_schemas = _declared_api_input_frame_schema_items(node)
                is_multi_frame_producer = (
                    len(declared_frame_schemas) > 1 if declared_frame_schemas else len(result) > 1
                )
                if is_multi_frame_producer and declared_frame_schemas:
                    # Loading is demand-scoped, but editor/schema metadata is
                    # a config contract. Surface every declared port without
                    # opening or collecting the unused parquet payloads.
                    for port_label, schema_items in declared_frame_schemas.items():
                        frame_schema_cache[(nid, port_label)] = schema_items
                # Gate the per-frame collect on the SAME materialize test
                # every other node uses (see ``should_materialize`` below
                # for single-frame nodes). A multi-frame ANCESTOR of a
                # target-only preview must stay lazy — schema only, no
                # collect — exactly like a single-frame ancestor, so per-frame
                # ``scan_parquet`` pushdown survives into its consumers.
                mp_should_materialize = materialized_ids is None or nid in materialized_ids
                # A limited node collects each frame to its limit, while its
                # consumers read the uncapped per-frame plans.
                frame_row_limit = collection_row_limit(nid)
                port_plans: dict[str, pl.LazyFrame] = {}
                capped_ports: dict[str, pl.LazyFrame | pl.DataFrame] = {}
                for port_label, port_frame in result.items():
                    if isinstance(port_frame, (pl.LazyFrame, pl.DataFrame)):
                        port_plans[port_label] = (
                            port_frame
                            if isinstance(port_frame, pl.LazyFrame)
                            else port_frame.lazy()
                        )
                        capped_ports[port_label] = (
                            port_frame.head(frame_row_limit) if frame_row_limit else port_frame
                        )
                    else:
                        raise TypeError(
                            f"Node '{nid}' multi-frame output for frame "
                            f"{port_label!r} is not a Polars frame "
                            f"(got {type(port_frame).__name__}).",
                        )

                if mp_should_materialize:
                    materialised: dict[str, pl.DataFrame] = {}
                    for port_label, capped in capped_ports.items():
                        if isinstance(capped, pl.LazyFrame):
                            port_df = streaming_collect(
                                capped,
                                execution_context=execution_context,
                            )
                        else:
                            port_df = capped
                        materialised[port_label] = port_df
                    # Store DataFrames for cache accounting; downstream
                    # _pick_source_frame + _to_lazy_if_needed will lazify when
                    # consumers need a LazyFrame. A limited collection is never
                    # a consumer's input.
                    runtime_outputs[nid] = port_plans if frame_row_limit else materialised
                    # Populate the per-port contract cache for every bundle,
                    # but expose frame_schema_cache only for genuinely
                    # multi-frame producers.  A one-frame API source now has
                    # the uniform dict runtime shape, while its ordinary
                    # preview schema remains in ``columns``.
                    for port_label, port_df in materialised.items():
                        column_cache[(nid, port_label)] = frozenset(port_df.columns)
                        if is_multi_frame_producer and not declared_frame_schemas:
                            port_schema = port_df.schema
                            frame_schema_cache[(nid, port_label)] = [
                                (name, str(port_schema[name])) for name in port_df.columns
                            ]
                    if execution_context is not None:
                        execution_context.record_column_widths(
                            node_id=nid,
                            output_width=sum(frame.width for frame in materialised.values()),
                        )
                    eager_outputs[nid] = materialised
                    if not is_multi_frame_producer and len(materialised) == 1:
                        only_port, only_frame = next(iter(materialised.items()))
                        only_schema = declared_frame_schemas.get(only_port) or [
                            (name, str(only_frame.schema[name])) for name in only_frame.columns
                        ]
                        available_columns[nid] = only_schema
                        output_columns[nid] = only_schema
                else:
                    # ANCESTOR: keep the per-frame LazyFrames in
                    # runtime_outputs for routing only; do NOT collect and do
                    # NOT write eager_outputs (mirrors the single-frame lazy
                    # ancestor — schema via collect_schema(), absent from
                    # eager_outputs). Schema is read without materialising.
                    lazy_ports: dict[str, pl.LazyFrame] = {}
                    for port_label, port_lf in port_plans.items():
                        lazy_ports[port_label] = port_lf
                        port_schema = port_lf.collect_schema()
                        column_cache[(nid, port_label)] = frozenset(port_schema.names())
                        if is_multi_frame_producer and not declared_frame_schemas:
                            frame_schema_cache[(nid, port_label)] = [
                                (name, str(port_schema[name])) for name in port_schema.names()
                            ]
                    runtime_outputs[nid] = lazy_ports
                    if not is_multi_frame_producer and len(lazy_ports) == 1:
                        only_port, only_lazy_frame = next(iter(lazy_ports.items()))
                        only_frame_schema = only_lazy_frame.collect_schema()
                        only_schema = declared_frame_schemas.get(only_port) or [
                            (name, str(only_frame_schema[name]))
                            for name in only_frame_schema.names()
                        ]
                        available_columns[nid] = only_schema
                        output_columns[nid] = only_schema
                t1 = time.perf_counter()
                timings[nid] = round((t1 - t0) * 1000, 1)
                available_columns.setdefault(nid, [])
                output_columns.setdefault(nid, [])
                if execution_context is not None:
                    execution_context.checkpoint(label="after_node", node_id=nid)
                continue

            if not isinstance(result, (pl.LazyFrame, pl.DataFrame)):
                raise TypeError(
                    f"Node '{nid}' returned {type(result).__name__}; expected a Polars frame."
                )

            result_lf = result if isinstance(result, pl.LazyFrame) else result.lazy()

            # Capture full column set before selected_columns filtering
            available_columns[nid] = _schema_items_of(result_lf)
            if seeded and snapshot_plan is not None:
                # A seed is its shaped output; the columns before its own
                # selection and renames come from what its generation recorded.
                recorded = snapshot_plan.seed_unshaped_columns(nid)
                if recorded is not None:
                    available_columns[nid] = list(recorded)

            if seeded:
                output_lf = result_lf
            else:
                # Apply selected_columns filter first (uses pre-rename names),
                # then column renames on the surviving columns.
                filtered = _apply_selected_columns(result_lf, node_map[nid].data.config)
                renamed = _apply_column_renames(filtered, node_map[nid].data.config)
                output_lf = renamed if isinstance(renamed, pl.LazyFrame) else renamed.lazy()
            full_output_columns = _schema_items_of(output_lf)
            full_output_columns = _full_model_score_schema(nid, node, full_output_columns)
            if _is_plain_model_score(node):
                available_columns[nid] = full_output_columns
            output_columns[nid] = full_output_columns
            output_column_names = [name for name, _dtype in full_output_columns]
            if execution_context is not None:
                execution_context.record_column_widths(
                    node_id=nid,
                    output_width=len(output_column_names),
                )
            output_column_set = set(output_column_names)

            # Output-side contract check: every column the node promises
            # to produce must be present on the result.  We check the
            # post-rename/post-select frame because that's what
            # downstream consumers actually see.  Passthrough-runtime
            # nodes are exempt — see the ``is_passthrough_runtime``
            # note above.
            final_cols = frozenset(output_column_names)
            if (
                check_here
                and contract.outputs is not None  # type: ignore[union-attr]
                and not is_passthrough_runtime
            ):
                boundary_runner.assert_outputs(boundary, final_cols)

            if decision is not None and not seeded:
                closure = planned.record_closure(nid)
                if nid in decision.captures:
                    # Sunk in full, before anything below it is collected;
                    # everything below reads what was written.
                    try:
                        output_lf = planned.capture(
                            nid,
                            output_lf,
                            closure,
                            join=eager_join_recipes.get(nid),
                            unshaped_columns=(
                                available_columns[nid] if _shapes_output(node_map[nid]) else None
                            ),
                        )
                    except (SourceCacheError, OSError) as exc:
                        capture_store_failures.append(exc)
                        raise

            projection = collect_needed_cols.get(nid)
            projected_columns: list[str] | None = None
            if projection is not None:
                missing = projection - output_column_set
                if missing and nid not in normalised_required_columns:
                    raise ContractMismatchError(
                        "Eager projection references columns missing from the node output schema.",
                        node_id=nid,
                        node_type=node.data.nodeType.value,
                        missing=sorted(missing),
                        required_columns=sorted(projection),
                        output_columns=sorted(output_column_set),
                    )
                candidate_columns = [c for c in output_column_names if c in projection]
                if len(candidate_columns) < len(output_column_names):
                    projected_columns = candidate_columns
            column_cache[(nid, None)] = final_cols

            should_materialize = materialized_ids is None or nid in materialized_ids
            if should_materialize:
                collect_lf = output_lf
                if projected_columns is not None:
                    logger.info(
                        "eager_projection",
                        node_id=nid,
                        total_cols=len(output_column_names),
                        projected_cols=len(projected_columns),
                    )
                    collect_lf = collect_lf.select(projected_columns)
                column_limit = materialize_column_limits.get(nid)
                if (
                    column_limit is not None
                    and projection is None
                    and len(output_column_names) > column_limit
                ):
                    collect_lf = output_lf.select(output_column_names[:column_limit])
                node_row_limit = collection_row_limit(nid)
                if node_row_limit:
                    collect_lf = collect_lf.head(node_row_limit)
                if execution_context is not None:
                    execution_context.checkpoint(label="before_collect", node_id=nid)
                    with execution_context.stage("eager_collect", node_id=nid):
                        df = streaming_collect(
                            collect_lf,
                            execution_context=execution_context,
                        )
                    execution_context.checkpoint(label="after_collect", node_id=nid)
                else:
                    df = streaming_collect(collect_lf)
                eager_outputs[nid] = df
                # Consumers read the collection only when it holds every row
                # and every column they need: a limited or column-narrowed
                # collection (the caller's demand below a negotiated one)
                # never feeds them.
                consumer_columns = needed_cols.get(nid)
                collected_covers = (
                    consumer_columns <= set(df.columns)
                    if consumer_columns is not None
                    else df.width == len(output_column_names)
                )
                runtime_outputs[nid] = df if not node_row_limit and collected_covers else output_lf
                memory_bytes[nid] = int(df.estimated_size("b"))
            else:
                runtime_outputs[nid] = output_lf
        except (ContractMismatchError, SchemaMismatchError):
            # Contract and schema mismatches are API-level — raise even in swallow mode
            # so GUI users see the crisp error instead of a silent
            # per-node "failed" status card.
            raise
        except (ExecutionCancelledError, ExecutionMemoryLimitExceededError):
            # Execution-control signals are run-level failures, not
            # user-code node errors. They must reach the route/job layer
            # so cancellation and memory-limit semantics stay consistent.
            raise
        except Exception as exc:
            if is_public_contract_error(exc):
                # Versioned public errors are run-level contract failures.
                # Preview's per-node swallow mode must never hide them.
                raise
            if any(exc is failure for failure in capture_store_failures):
                raise
            if not swallow_errors:
                raise
            logger.error("node_failed", node_id=nid, error=str(exc))
            eager_outputs[nid] = None
            runtime_outputs[nid] = None
            errors[nid] = str(exc)
            error_line = _extract_error_line(exc)
            if error_line is not None:
                error_lines[nid] = error_line
        timings[nid] = round((time.perf_counter() - t0) * 1000, 1)

    executed_strategy = execution_context.projection_plan if execution_context is not None else None
    target_output = eager_outputs.get(target_node_id) if target_node_id is not None else None
    if (
        execution_context is not None
        and execution_context.profile is ExecutionProfile.PREVIEW_EAGER
        and target_node_id is not None
        and materialize_node_ids is not None
        and set(materialize_node_ids) == {target_node_id}
        and isinstance(executed_strategy, projection_planner.ExecutionStrategyResult)
    ):
        replan_required_columns = dict(normalised_required_columns)
        if target_node_id not in replan_required_columns and isinstance(
            target_output, pl.DataFrame
        ):
            # A preview that named no columns demands exactly what it collected.
            replan_required_columns[target_node_id] = set(target_output.columns)
        execution_context.projection_plan = _replanned_target_preview_strategy(
            executed_strategy,
            order=run_order,
            node_map=node_map,
            required_columns_by_node=replan_required_columns,
            relevant_edges=relevant_edges,
            graph=graph,
            known_output_columns={
                key: columns for key, columns in column_cache.items() if key[0] not in errors
            },
            runtime_edge_demands=recorded_runtime_edge_demands,
            runtime_resolved_parent_ids=recorded_runtime_resolved_parents,
            profile=execution_context.profile,
            seeded_node_ids=seeded_ids,
        )

    plans: dict[str, pl.LazyFrame | dict[str, pl.LazyFrame]] = {}
    for plan_node_id, runtime_output in runtime_outputs.items():
        if isinstance(runtime_output, dict):
            plans[plan_node_id] = {
                port: frame if isinstance(frame, pl.LazyFrame) else frame.lazy()
                for port, frame in runtime_output.items()
            }
        elif runtime_output is not None:
            plans[plan_node_id] = (
                runtime_output
                if isinstance(runtime_output, pl.LazyFrame)
                else runtime_output.lazy()
            )

    return EagerResult(
        eager_outputs,
        run_order,
        parents_of,
        node_map,
        id_to_name,
        errors,
        timings,
        memory_bytes,
        error_lines,
        available_columns,
        output_columns,
        frame_schema_cache,
        plans,
    )
