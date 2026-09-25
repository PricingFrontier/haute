"""Node-boundary machinery the graph walker uses.

Every execution walks through ``haute._graph_walker.walk_graph``; this module
holds the per-node pieces it composes.
"""

from __future__ import annotations

import contextlib
import difflib
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast

import polars as pl

import haute.execution as execution_facade
import haute.projection as projection_planner
from haute._builders import _passthrough_fn
from haute._chunked_writes import (
    ChunkedWrite,
    JoinRecipe,
    WriteRecipe,
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
    ExecutionContext,
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
from haute._logging import get_logger
from haute._polars_selectors import preamble_selector_aliases
from haute._polars_utils import (
    current_streaming_chunk_size,
    projected_or_carrier_columns,
)
from haute._source_cache import SourceCacheCorruptError
from haute._topo import ancestors
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
    ContractColumnsMissingError,
    ContractMismatchError,
    ContractResolutionError,
    SchemaMismatchError,
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
    missing = sorted(contract.inputs - upstream_columns)
    if not missing:
        return
    subject = f"{_node_display_name(node)} needs {_name_columns(missing)}"
    if not upstream_columns:
        message = f"{subject}, but its input has no columns."
    else:
        message = f"{subject}, which {'is' if len(missing) == 1 else 'are'} not in its input."
        similar = _similar_columns(missing, upstream_columns)
        if similar:
            noun = "a similar column" if len(similar) == 1 else "similar columns"
            message += f" Its input has {noun}: {', '.join(map(repr, similar))}."
    raise ContractColumnsMissingError(message, node_id=node.id, missing=missing)


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
    missing = sorted(contract.outputs - output_columns)
    if not missing:
        return
    raise ContractColumnsMissingError(
        f"{_node_display_name(node)} did not create {_name_columns(missing)}, "
        "which its contract says it outputs.",
        node_id=node.id,
        missing=missing,
    )


# The preview shows a contract error's text as it is, so it names only the
# missing columns (never the frame's other columns) and at most this many.
_MESSAGE_COLUMN_LIMIT = 5


def _node_display_name(node: GraphNode) -> str:
    return repr(node.data.label or node.id)


def _name_columns(columns: Sequence[str]) -> str:
    """``the column 'a'`` / ``the columns 'a' and 'b'`` / ``… and N more``."""
    shown = [repr(name) for name in columns[:_MESSAGE_COLUMN_LIMIT]]
    if len(columns) == 1:
        return f"the column {shown[0]}"
    if len(columns) > len(shown):
        return f"the columns {', '.join(shown)} and {len(columns) - len(shown)} more"
    return f"the columns {', '.join(shown[:-1])} and {shown[-1]}"


def _similar_columns(missing: Sequence[str], available: Iterable[str]) -> list[str]:
    """The closest spelling in *available* to each missing column, if any is close."""
    candidates = sorted(available)
    similar: list[str] = []
    for name in missing:
        for match in difflib.get_close_matches(name, candidates, n=1, cutoff=0.8):
            if match not in similar:
                similar.append(match)
    return similar


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


def lineage_preparation_order(
    graph: PipelineGraph,
    target_node_id: str | None,
    source: str,
) -> list[str]:
    """Node ids of a preview's or trace's executed lineage, for input preparation.

    The lineage is walked over the same live-switch-pruned edges the execution
    itself uses, so an inactive branch's inputs are never prepared. Without a known
    target every node is in the lineage.
    """
    if target_node_id is None or target_node_id not in graph.node_map:
        return [node.id for node in graph.nodes]
    all_ids = {node.id for node in graph.nodes}
    edges = _prune_live_switch_edges(
        graph.edges,
        graph.node_map,
        source,
        submodels=graph.submodels,
    )
    return sorted(ancestors(target_node_id, edges, all_ids))


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
        prewritten_digests: Mapping[str, str] | None = None,
        join: JoinRecipe | None = None,
        recipe: WriteRecipe | None = None,
        unshaped_columns: Sequence[tuple[str, str]] | None = None,
    ) -> pl.LazyFrame:
        """Write one capture point through the chunked writer and continue from it.

        With ``prewritten``, *artifact* already holds the node's output — a
        batch Model Score's scored parts — and is published without a second
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
            elif prewritten_digests:
                artifact.record_digests(prewritten_digests)
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
