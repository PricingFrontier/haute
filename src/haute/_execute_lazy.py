"""Lazy and eager graph execution — shared by executor, trace, and scorer."""

from __future__ import annotations

import contextlib
import gc
import hashlib
import re
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, NamedTuple, cast

import polars as pl

import haute.execution as execution_facade
import haute.projection as projection_planner
from haute._builders import _passthrough_fn
from haute._column_lineage import analyze_polars_lineage
from haute._config_io import is_windows_reserved_filename
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
    upstream_node_ids,
)
from haute._logging import get_logger
from haute._path_resolution import runtime_project_root_scoped
from haute._polars_utils import _malloc_trim, bounded_sink, streaming_collect
from haute._types import (
    GraphEdge,
    GraphNode,
    NodeType,
    PipelineGraph,
    _Frame,
)
from haute.errors import (
    ConfigError,
    ContractMismatchError,
    ContractResolutionError,
    SchemaMismatchError,
    is_public_contract_error,
)

logger = get_logger(component="execute")

_CHECKPOINT_SAFE_NODE_ID = re.compile(r"\A[a-z0-9_][a-z0-9_.-]{0,199}\Z")


def _checkpoint_filename(node_id: str) -> str:
    """Return a single safe filename component for a graph node checkpoint.

    Existing ordinary node ids retain readable checkpoint names. Any id with
    path syntax, a platform-reserved name, or excessive length is represented
    by a deterministic digest instead of being interpolated into a path.
    """
    if _CHECKPOINT_SAFE_NODE_ID.fullmatch(node_id) and not is_windows_reserved_filename(node_id):
        return f"{node_id}.parquet"
    digest = hashlib.sha256(node_id.encode("utf-8")).hexdigest()
    # ``=`` is deliberately outside _CHECKPOINT_SAFE_NODE_ID, so an authored
    # safe id cannot collide with the digest namespace.
    return f"node={digest}.parquet"


def _lazy_frame_for_cache(lf: Any, node_id: str) -> pl.LazyFrame:
    """Coerce a node output to a LazyFrame for cache materialization.

    A multi-frame source emits a ``dict[label, LazyFrame]``; caching the whole
    bundle is undefined (the in-RAM cache is keyed per node, not per frame), so
    fail loud with a clear message rather than ``AttributeError`` on
    ``dict.lazy()``. Multi-frame sources are normally skipped by the parquet
    checkpoint path (sources aren't checkpointed), but the in-RAM cache path is
    gated only on the cache request, so this guard makes the unsupported
    combination explicit instead of crashing opaquely.
    """
    if isinstance(lf, dict):
        raise RuntimeError(
            "cannot cache-materialize a multi-frame source output "
            f"(node_id={node_id!r}); a multi-frame apiInput emits one frame per "
            "output — connect the specific frame downstream rather than caching "
            "the whole bundle",
        )
    return lf if isinstance(lf, pl.LazyFrame) else lf.lazy()


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
    if isinstance(source_output, dict):
        if not source_output:
            # Edge is intact; the source emitted no frames at all. Blaming
            # the edge ("expected one of: []") would mislead — flag the
            # source as the broken piece.
            raise RuntimeError(
                f"Source node {edge.source!r} emitted no frames. Check the "
                "node's configuration: at least one emit-true table with "
                "selected columns is required for a multi-frame apiInput.",
            )
        sh = edge.sourceHandle
        if sh is None:
            raise ValueError(
                f"Edge from multi-frame node {edge.source!r} has no sourceHandle. "
                f"Expected one of: {sorted(source_output.keys())}.",
            )
        if sh not in source_output:
            raise KeyError(
                f"Edge from {edge.source!r} references frame {sh!r}, "
                f"but the source emits: {sorted(source_output.keys())}.",
            )
        # source_output is `Any` (dict-of-frames); narrowing to `_Frame`
        # is correct at runtime — see function docstring.
        return cast(_Frame, source_output[sh])
    return cast(_Frame, source_output)


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

    User-declared concrete sides overlay the builder-derived contract. Known
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


def _strict_projection_for_context(
    execution_context: ExecutionContext | None,
    required_columns_by_node: Mapping[str, Iterable[str] | projection_planner.AllExceptColumns],
) -> bool:
    """Return whether projection-impossible cases should fail loudly."""
    return execution_context is not None and projection_planner.strict_projection_required(
        execution_context.profile,
        required_columns_by_node,
    )


# ---------------------------------------------------------------------------
# Adaptive checkpoint strategy
# ---------------------------------------------------------------------------

# Number of checkpoints between gc.collect() + _malloc_trim() calls.
# Polars objects use Rust Arc refcounting and are freed immediately on
# ``del``; Python gc.collect() only helps with cyclic garbage (rare here).
# Batching avoids the overhead of scanning all Python objects per checkpoint.
_GC_BATCH_INTERVAL = 3


class _CheckpointAction(StrEnum):
    """What to do at a potential checkpoint boundary."""

    SKIP = "skip"
    """Keep the LazyFrame as-is — no materialization needed."""

    PARQUET = "parquet"
    """Sink to a temp parquet file and replace with ``scan_parquet``.
    The safest option — frees RAM and isolates the query plan."""


def _checkpoint_decision(
    nid: str,
    is_source: bool,
    n_parents: int,
    n_children: int,
    feeds_join: bool,
    node_map: dict[str, GraphNode],
    scenario: str,
) -> _CheckpointAction:
    """Decide whether and how to checkpoint a node's output.

    Uses the same three structural triggers as before (joins, fan-outs,
    join-feeders) but skips MODEL_SCORE nodes in batch mode because
    the batched scorer already sinks to temp parquet and returns
    ``scan_parquet(scored_path)`` — an implicit checkpoint.  Adding
    another parquet round-trip on top is pure waste.
    """
    if is_source:
        return _CheckpointAction.SKIP

    needs_checkpoint = n_parents > 1 or n_children > 1 or feeds_join
    if not needs_checkpoint:
        return _CheckpointAction.SKIP

    # MODEL_SCORE in batch mode already returns scan_parquet — skip.
    node = node_map.get(nid)
    if node is not None and node.data.nodeType == NodeType.MODEL_SCORE and scenario != "live":
        return _CheckpointAction.SKIP

    return _CheckpointAction.PARQUET


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
    input_ids: list[str],
    input_lfs: list[_Frame],
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


def _runtime_join_demands(
    node: GraphNode,
    incoming_edges: list[GraphEdge],
    input_lfs: list[_Frame],
    projection: set[str] | frozenset[str] | None,
    existing_edge_demands: Mapping[
        projection_planner.ProjectionEdgeKey,
        set[str] | frozenset[str] | None,
    ],
    node_map: Mapping[str, GraphNode],
) -> dict[projection_planner.ProjectionEdgeKey, set[str]]:
    """Resolve a safe join projection from lazy parent schemas."""
    if projection is None or len(incoming_edges) < 2:
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
        base_index, join_index = resolve_edge_join_role_indices(node.data.config, input_ids)
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
                name = edge_input_name(edge, node_map[edge.source])
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
        analysis = analyze_polars_lineage(code, schemas, projection)
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
    parent_ids: Iterable[str],
    node_map: Mapping[str, GraphNode],
) -> frozenset[str]:
    """Return source parents whose lazy scans can absorb an edge projection."""
    source_types = {NodeType.API_INPUT, NodeType.DATA_INPUT, NodeType.EXTERNAL_FILE}
    projectable: set[str] = set()
    for parent_id in parent_ids:
        parent = node_map[parent_id]
        if parent.data.nodeType not in source_types:
            continue
        code = parent.data.config.get("code")
        if not isinstance(code, str) or not code.strip():
            projectable.add(parent_id)
        elif projection_planner.source_user_code_preserves_column_projection(code):
            projectable.add(parent_id)
    return frozenset(projectable)


def _prune_live_switch_edges(
    edges: list[GraphEdge],
    node_map: dict[str, GraphNode],
    source: str,
) -> list[GraphEdge]:
    """Remove edges to live_switch nodes from inputs inactive for *source*.

    A live_switch node's config contains ``input_scenario_map`` which maps
    each input name to the scenario it serves.  Only edges from inputs
    matching the active source are kept; the unused branch is pruned so
    it is neither executed nor shown in profilers.
    """
    return projection_planner.prune_live_switch_edges(edges, node_map, source)


@runtime_project_root_scoped
def _execute_lazy(
    graph: PipelineGraph,
    build_node_fn: Callable,
    target_node_id: str | None = None,
    preamble_ns: dict | None = None,
    source: str = "live",
    checkpoint_dir: Path | None = None,
    enforce_contracts: bool = False,
    preserve_node_ids: set[str] | frozenset[str] | None = None,
    required_columns_by_node: Mapping[str, Iterable[str] | projection_planner.AllExceptColumns]
    | None = None,
    execution_context: ExecutionContext | None = None,
    source_by_node: Mapping[str, str] | None = None,
    dataframe_cache_request: execution_facade.DataFrameExecutionCacheRequest | None = None,
    schema_only: bool = False,
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
        checkpoint_dir: If set, multi-input nodes (joins) and fan-out
            nodes (>1 downstream consumer) are checkpointed to parquet
            files in this directory and replaced with ``scan_parquet``
            references.  This breaks both chained-join memory
            accumulation and plan duplication across branches
            (GitHub pola-rs/polars#24206).
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
        dataframe_cache_request: Optional request describing node outputs that
            may be materialized to and reused from the shared backend dataframe
            cache.  Cached hits seed the lazy output map, letting execution skip
            covered upstream lineage while still building any uncached downstream
            target nodes.
        enforce_contracts: When ``True``, assert declared column contracts at each
            node boundary via ``.collect_schema()``.  Polars computes
            schemas without executing the query, so this stays cheap.
            Production code paths (batch sink, deploy scoring, training,
            optimiser) run through here — enforcement on the lazy path
            is what makes contract coverage real end-to-end.
        schema_only: Declares that the caller reads ``collect_schema()`` and
            never collects a frame or invokes a sink.  Strategy planning then
            skips the group-by materialisation-admission gate, which bounds
            peak memory during materialisation only.

    Returns:
        (lazy_outputs, order, parents_of, id_to_name)
    """
    graph = _resolve_graph_paths(graph)
    preserved_outputs = frozenset(preserve_node_ids or ())
    node_source_overrides = dict(source_by_node or {})
    if execution_context is not None:
        execution_context.checkpoint(label="lazy_start")
    prepared = projection_planner.prepare_graph(
        graph,
        target_node_id,
        source=source,
    )
    node_map = prepared.node_map
    order = prepared.order
    parents_of = prepared.parents_of
    id_to_name = prepared.id_to_name
    relevant_edges = prepared.relevant_edges
    # Re-check graph-shape contracts for the nodes that will actually execute.
    # The parser already validates parse-time graphs, but routes can build raw
    # graphs from the frontend that bypass the parser; without this check, a
    # malformed explore node would surface as a confusing Polars TypeError
    # instead of a typed ParseError.
    validate_pipeline_graph_shape_contracts(
        graph,
        graph_label=graph.pipeline_name or "execution",
        node_ids_to_validate=set(order) if target_node_id is not None else None,
    )
    normalised_required_columns = _normalise_required_columns_by_node(
        required_columns_by_node,
        order,
    )
    planning_required_columns: dict[
        str,
        set[str] | projection_planner.AllExceptColumns,
    ] = dict(normalised_required_columns)
    strict_contract_resolution = _strict_contract_resolution(
        execution_context.profile if execution_context is not None else None
    )
    cache_request = dataframe_cache_request

    # Count downstream consumers per node so we can checkpoint fan-out
    # points (nodes whose output feeds >1 consumer).  Without this,
    # Polars duplicates the entire upstream plan for each branch —
    # e.g. a 38 GB JSONL scan runs twice when two siblings share a parent.
    children_count: dict[str, int] = {nid: 0 for nid in order}
    children_of: dict[str, list[str]] = {nid: [] for nid in order}
    for nid, pids in parents_of.items():
        for pid in pids:
            if pid in children_count:
                children_count[pid] += 1
                children_of[pid].append(nid)

    cached_seed_outputs: dict[str, _Frame] = {}
    skip_cache_covered_nodes: set[str] = set()
    cache_backed_node_ids: set[str] = set()
    cache_hit_rejected_node_ids: set[str] = set()
    if cache_request is not None:
        unknown_cache_nodes = sorted(
            node_id for node_id in cache_request.keys_by_node if node_id not in node_map
        )
        if unknown_cache_nodes:
            raise ValueError(
                "Dataframe cache request references node IDs that are not in the "
                f"prepared execution graph: {unknown_cache_nodes}"
            )

        effective_profile = (
            execution_context.profile
            if execution_context is not None
            else ExecutionProfile.LAZY_SINK
        )
        effective_cache_profile = execution_facade.dataframe_execution_cache_profile(
            effective_profile
        )

        def _merge_cache_required_columns(
            runtime_demand: set[str] | projection_planner.AllExceptColumns | None,
            cache_key: execution_facade.DataFrameExecutionCacheKey,
        ) -> set[str] | projection_planner.AllExceptColumns | None:
            if isinstance(runtime_demand, projection_planner.AllExceptColumns):
                return runtime_demand
            merged = set(runtime_demand or ())
            merged.update(cache_key.required_columns)
            return merged if merged else runtime_demand

        def _required_columns_for_cached_seed(
            demand: set[str] | projection_planner.AllExceptColumns | None,
        ) -> set[str]:
            if demand is None:
                return set()
            if isinstance(demand, projection_planner.AllExceptColumns):
                return set(demand.required_columns)
            return set(demand)

        cache_required_columns: dict[str, set[str] | projection_planner.AllExceptColumns] = dict(
            normalised_required_columns
        )
        for node_id, cache_key in cache_request.keys_by_node.items():
            merged_demand = _merge_cache_required_columns(
                cache_required_columns.get(node_id),
                cache_key,
            )
            if merged_demand is not None:
                cache_required_columns[node_id] = merged_demand
        # Cache materialisation is part of this physical execution, not merely
        # an identity check.  Plan from the union so a narrower immediate
        # request cannot prune passthrough dependencies needed by the artifact.
        planning_required_columns = cache_required_columns
        cache_policy = execution_facade.dataframe_lazy_execution_policy(
            target_node_id=target_node_id,
            source_by_node=node_source_overrides,
            required_columns_by_node=cache_required_columns,
            preserve_node_ids=preserved_outputs,
            enforce_contracts=enforce_contracts,
            preamble_ns_supplied=preamble_ns is not None,
        )
        cache_policy_fingerprint = execution_facade.dataframe_execution_policy_fingerprint(
            cache_policy
        )
        cache_key_memo = execution_facade.GraphFingerprintMemo()
        for node_id, cache_key in cache_request.keys_by_node.items():
            effective_source = source or "live"
            if cache_key.source != effective_source:
                raise ValueError(
                    "Dataframe cache key source does not match lazy execution source "
                    f"(node_id={node_id!r}, key.source={cache_key.source!r}, "
                    f"execution.source={effective_source!r})"
                )
            if cache_key.profile != effective_cache_profile:
                raise ValueError(
                    "Dataframe cache key profile does not match lazy execution profile "
                    f"(node_id={node_id!r}, key.profile={cache_key.profile!r}, "
                    f"execution.profile={effective_cache_profile!r})"
                )
            if cache_key.execution_policy_fingerprint != cache_policy_fingerprint:
                raise ValueError(
                    "Dataframe cache key execution policy does not match lazy execution "
                    f"policy (node_id={node_id!r})"
                )
            demand = cache_required_columns.get(node_id)
            required_columns = (
                None if isinstance(demand, projection_planner.AllExceptColumns) else demand
            )
            expected_key = execution_facade.dataframe_execution_cache_key(
                graph,
                node_id=node_id,
                namespace=cache_key.namespace,
                source=effective_source,
                profile=effective_cache_profile,
                input_fingerprint=cache_key.input_fingerprint,
                required_columns=required_columns,
                extra_keys=cache_key.extra_keys,
                execution_policy=cache_policy,
                memo=cache_key_memo,
            )
            if cache_key != expected_key:
                raise ValueError(
                    "Dataframe cache key does not match the current lazy execution "
                    f"graph and policy (node_id={node_id!r})"
                )
            # Broken/missing cache entries are auto-evicted by ``cache.get``
            # which then returns None; no explicit error handling needed.
            cached_entry = cache_request.cache.get(cache_key)
            if cached_entry is not None:
                required_for_seed = _required_columns_for_cached_seed(
                    cache_required_columns.get(node_id)
                )
                missing_for_seed = sorted(required_for_seed - set(cached_entry.columns))
                if missing_for_seed:
                    logger.warning(
                        "dataframe_execution_cache_hit_missing_required_columns",
                        node_id=node_id,
                        missing=missing_for_seed,
                    )
                    cache_hit_rejected_node_ids.add(node_id)
                else:
                    cached_lf = cache_request.cache.scan(cache_key)
                    if cached_lf is not None:
                        cached_seed_outputs[node_id] = cached_lf
                        cache_backed_node_ids.add(node_id)

        cache_covers_downstream: dict[str, bool] = {}
        for nid in reversed(order):
            if nid in cached_seed_outputs:
                cache_covers_downstream[nid] = True
            elif nid in preserved_outputs:
                cache_covers_downstream[nid] = False
            else:
                children = children_of.get(nid, [])
                cache_covers_downstream[nid] = bool(children) and all(
                    cache_covers_downstream.get(child_id, False) for child_id in children
                )
        skip_cache_covered_nodes = {
            node_id
            for node_id, covered in cache_covers_downstream.items()
            if covered and node_id not in cached_seed_outputs
        }

    # Backward column analysis: compute the minimal set of columns
    # needed at each node's output so checkpoints can project away
    # unneeded columns before writing to parquet.  Batch MODEL_SCORE
    # nodes also consume this demand locally so their internal temp
    # parquet write can avoid unused passthrough columns even when the
    # outer checkpoint layer skips model-score nodes.
    strategy_profile = (
        execution_context.profile if execution_context is not None else ExecutionProfile.LAZY_SINK
    )
    group_by_operators = projection_planner.group_by_operators_by_node(order, node_map)
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
        )
    else:
        public_strategy_result = execution_facade.plan_prepared_execution_strategy(
            order,
            children_of,
            node_map,
            profile=strategy_profile,
            required_columns_by_node=planning_required_columns,
            execution_context=execution_context,
            schema_only=schema_only,
            relevant_edges=relevant_edges,
        )
    public_projection_plan = public_strategy_result.projection_plan
    projection_plan = public_projection_plan
    needed_cols = projection_plan.needed_by_node
    edge_demands = projection_plan.edge_demands
    cache_broadens_projection = planning_required_columns != normalised_required_columns
    runtime_projection_plan = (
        projection_planner.compute_prepared_plan(
            order,
            children_of,
            node_map,
            normalised_required_columns,
            strict_projection=_strict_projection_for_context(
                execution_context,
                normalised_required_columns,
            ),
            relevant_edges=relevant_edges,
        )
        if cache_broadens_projection
        else projection_plan
    )
    api_port_columns_by_node = projection_planner.api_input_port_columns_by_node(
        node_map,
        relevant_edges,
        projection_plan,
    )

    # Full parent lookup from ALL edges for instance resolution
    all_parents = graph.parents_of

    # Per-target incoming-edge lookup, in edge-declaration order, so each
    # node's function-parameter binding key can be derived from
    # ``edge.sourceHandle or edge.source`` (MULTI_FRAME_PLAN §4b) without
    # re-scanning per node. Use ``relevant_edges`` (post-pruning,
    # ancestor-filtered) so live-switch-inactive edges don't surface here
    # — that would mismatch ``parents_of`` and break the binding-count
    # invariant on switch nodes.
    incoming_edges_by_target: dict[str, list[GraphEdge]] = {}
    for edge in relevant_edges:
        incoming_edges_by_target.setdefault(edge.target, []).append(edge)
    all_incoming_edges_by_target: dict[str, list[GraphEdge]] = {}
    for edge in graph.edges:
        all_incoming_edges_by_target.setdefault(edge.target, []).append(edge)

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
        if cache_broadens_projection:
            # Source builders cannot validate a best-effort cache-only demand
            # before their lazy schema exists.  Keep their scans lazy and broad;
            # the first edge projection below intersects cache-only columns with
            # the actual schema, so Parquet/NDJSON pushdown still occurs.
            for node_id in order:
                if not parents_of.get(node_id):
                    builder_needed_cols[node_id] = None
        build_order = [
            node_id
            for node_id in order
            if node_id not in skip_cache_covered_nodes and node_id not in cached_seed_outputs
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
        )

    # Execute - all intermediate results stay lazy
    lazy_outputs: dict[str, _Frame] = {}

    # Separate mutable counter for tracking remaining downstream consumers.
    # Decremented at checkpoint time so we know when a parent's LazyFrame
    # can be safely deleted (freeing Polars/Rust Arrow buffers).
    remaining: dict[str, int] = dict(children_count)

    # Batch gc.collect() calls — Polars objects use Rust Arc refcounting
    # and are freed immediately on ``del``.  gc.collect() only helps with
    # cyclic Python garbage (rare here) and adds 50-200 ms per call.
    checkpoints_since_gc = 0

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
                and pid not in cache_backed_node_ids
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
            # A cache key may deliberately be broader than this call's runtime
            # demand.  Cache population is an optimisation: an unavailable
            # cache-only column makes that artifact ineligible, but must not
            # fail an otherwise valid execution.  The materialisation gate will
            # observe the same missing column and skip the cache write.
            logger.warning(
                "dataframe_execution_cache_projection_column_missing",
                node_id=child_id,
                parent_id=parent_id,
                missing=sorted(missing),
            )
            demand = set(demand) - missing

        ordered = [column for column in schema_cols if column in demand]
        if not ordered and not demand and schema_cols:
            ordered = schema_cols[:1]
        return lazy_frame.select(ordered), frozenset(ordered)

    def _runtime_join_edge_demands(
        child_id: str,
        incoming_edges: list[GraphEdge],
        input_lfs: list[_Frame],
    ) -> dict[projection_planner.ProjectionEdgeKey, set[str]]:
        return _runtime_join_demands(
            node_map[child_id],
            incoming_edges,
            input_lfs,
            needed_cols.get(child_id),
            edge_demands,
            node_map,
        )

    def _build_lazy_node(nid: str) -> tuple[_Frame, bool, GraphNode]:
        # May actually return ``(dict[str, _Frame], bool, GraphNode)`` for
        # multi-frame apiInput sources. Signature stays narrowed because every
        # consumer in this function passes the result through
        # ``isinstance(lf, dict)`` narrowing before LazyFrame-only operations.
        # ``# type: ignore[return-value]`` on the dict-return site captures it.
        nonlocal public_projection_plan, public_strategy_result

        fn, is_source = funcs[nid]
        node = node_map[nid]
        requested_columns = needed_cols.get(nid)
        if execution_context is not None:
            execution_context.record_column_widths(
                node_id=nid,
                requested_width=(None if requested_columns is None else len(requested_columns)),
            )
        contract = (
            _resolve_effective_contract(
                node,
                strict=strict_contract_resolution,
            ).contract
            if enforce_contracts
            else None
        )
        check_here = bool(contract) and _should_check_contract(contract)  # type: ignore[arg-type]
        # Builder-wired ``_passthrough_fn`` means the node is in a stub
        # state (MODEL_SCORE without a model, OPTIMISER_APPLY without
        # an artifact).  Its declared contract describes the configured
        # shape the runtime does not produce yet; skip the output check
        # to preserve the "configure later" UX while still enforcing
        # contracts the moment a real function is wired.
        is_passthrough_runtime = fn is _passthrough_fn

        if is_source:
            lf = fn()
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
            incoming_edges = incoming_edges_by_target.get(nid, [])
            input_lfs = [_pick_source_frame(lazy_outputs[e.source], e) for e in incoming_edges]
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
                        (key.source for key in runtime_edge_demands),
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
                    headroom_bytes=previous_diagnostic.headroom_bytes,
                    assumptions=previous_diagnostic.assumptions,
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
                _assert_inputs_satisfy_contract(node, contract, upstream_cols)

            if enforce_contracts:
                _assert_simple_join_key_dtypes_compatible(node, input_ids, input_lfs)

            lf = fn(*input_lfs)

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
            _assert_outputs_satisfy_contract(node, contract, out_cols)

        return lf, is_source, node

    for nid in order:
        if nid in skip_cache_covered_nodes:
            continue
        cached_seed = cached_seed_outputs.get(nid)
        if cached_seed is not None:
            lazy_outputs[nid] = cached_seed
            column_cache[(nid, None)] = _columns_of(cached_seed)
            logger.info("dataframe_execution_cache_seed_hit", node_id=nid)
            if execution_context is not None:
                execution_context.checkpoint(label="lazy_dataframe_cache_seed_hit", node_id=nid)
            continue
        if execution_context is not None:
            execution_context.checkpoint(label="before_node", node_id=nid)
        with (
            execution_context.stage("lazy_build", node_id=nid)
            if execution_context is not None
            else contextlib.nullcontext()
        ):
            lf, is_source, node = _build_lazy_node(nid)

        cache_materialized = False
        if cache_request is not None and nid not in cache_hit_rejected_node_ids:
            materialize_cache_key = cache_request.keys_by_node.get(nid)
            if materialize_cache_key is not None:
                with (
                    execution_context.stage("lazy_dataframe_cache_materialize", node_id=nid)
                    if execution_context is not None
                    else contextlib.nullcontext()
                ):
                    try:
                        lazy_frame_for_cache = _lazy_frame_for_cache(lf, nid)
                        required_for_cache = sorted(materialize_cache_key.required_columns)
                        if required_for_cache:
                            cache_columns = set(_schema_names_of(lazy_frame_for_cache))
                            missing_for_cache = sorted(set(required_for_cache) - cache_columns)
                        else:
                            missing_for_cache = []
                        if missing_for_cache:
                            logger.warning(
                                "dataframe_execution_cache_required_columns_missing_skip",
                                node_id=nid,
                                missing=missing_for_cache,
                            )
                            cached_lf = None
                        else:
                            if required_for_cache:
                                lazy_frame_for_cache = lazy_frame_for_cache.select(
                                    required_for_cache
                                )
                            cached_lf = execution_facade.materialize_lazy_frame_with_cache(
                                lazy_frame_for_cache,
                                cache=cache_request.cache,
                                key=materialize_cache_key,
                                profile=(
                                    execution_context.profile
                                    if execution_context is not None
                                    else ExecutionProfile.LAZY_SINK
                                ),
                                streaming_chunk_size=cache_request.streaming_chunk_size,
                                fast_checkpoint=cache_request.fast_checkpoint,
                            )
                    except execution_facade.CacheArtifactTooLargeError as exc:
                        logger.warning(
                            "dataframe_execution_cache_artifact_too_large_skip",
                            node_id=nid,
                            error=str(exc),
                        )
                    else:
                        if cached_lf is not None:
                            lf = cached_lf
                            cache_materialized = True
                            cache_backed_node_ids.add(nid)
                            column_cache[(nid, None)] = _columns_of(lf)
                            _release_consumed_parents(nid)
                            checkpoints_since_gc += 1
                            if checkpoints_since_gc >= _GC_BATCH_INTERVAL:
                                gc.collect()
                                _malloc_trim()
                                checkpoints_since_gc = 0
                            logger.info("dataframe_execution_cache_materialized", node_id=nid)
                            if execution_context is not None:
                                execution_context.checkpoint(
                                    label="after_dataframe_cache_materialize",
                                    node_id=nid,
                                )

        # Adaptive checkpoint to break Polars plan duplication.
        #
        # Three structural triggers (joins, fan-outs, join-feeders) are
        # evaluated by _checkpoint_decision:
        #   PARQUET      — disk round-trip, safest, frees RAM
        #   SKIP         — keep the LazyFrame as-is (source nodes,
        #                  batch MODEL_SCORE which already checkpoints
        #                  internally, or nodes that don't need it)
        n_parents = len(parents_of.get(nid, []))
        n_children = children_count.get(nid, 0)
        feeds_join = any(len(parents_of.get(cid, [])) > 1 for cid in children_of.get(nid, []))

        action = _checkpoint_decision(
            nid,
            is_source,
            n_parents,
            n_children,
            feeds_join,
            node_map,
            node_source_overrides.get(nid, source or "live"),
        )

        if (
            not cache_materialized
            and checkpoint_dir is not None
            and action == _CheckpointAction.PARQUET
        ):
            tmp = checkpoint_dir / _checkpoint_filename(nid)

            # Project to only the columns needed downstream before
            # writing the checkpoint.  This avoids writing (and later
            # re-reading) columns that no downstream node will use —
            # e.g. 100 source columns when the model only needs 8.
            sink_lf = lf if isinstance(lf, pl.LazyFrame) else lf.lazy()
            projection = needed_cols.get(nid)
            if projection is not None:
                schema_cols = sink_lf.collect_schema().names()
                schema_set = set(schema_cols)
                missing = projection - schema_set
                runtime_projection = runtime_projection_plan.needed_by_node.get(nid)
                runtime_required = set(runtime_projection or ())
                runtime_missing = missing & runtime_required
                if runtime_missing:
                    raise ContractMismatchError(
                        "Checkpoint projection references columns missing "
                        "from the node output schema.",
                        node_id=nid,
                        node_type=node.data.nodeType.value,
                        missing=sorted(runtime_missing),
                        required_columns=sorted(runtime_required),
                        output_columns=sorted(schema_set),
                    )
                cache_only_missing = missing - runtime_missing
                if cache_only_missing:
                    logger.warning(
                        "dataframe_execution_cache_checkpoint_column_missing",
                        node_id=nid,
                        missing=sorted(cache_only_missing),
                    )
                effective_projection = set(projection) - cache_only_missing
                valid = [c for c in schema_cols if c in effective_projection]
                if not valid and not effective_projection and schema_cols:
                    valid = schema_cols[:1]
                if valid and len(valid) < len(schema_cols):
                    logger.info(
                        "checkpoint_projection",
                        node_id=nid,
                        total_cols=len(schema_cols),
                        projected_cols=len(valid),
                    )
                    sink_lf = sink_lf.select(valid)
                    column_cache[(nid, None)] = frozenset(valid)

            with (
                execution_context.stage("lazy_checkpoint_parquet", node_id=nid)
                if execution_context is not None
                else contextlib.nullcontext()
            ):
                bounded_sink(sink_lf, tmp, fast_checkpoint=True)

            # Drop the old LazyFrame (and any cached Arrow buffers it
            # holds) before replacing with a fresh scan reference.
            del lf
            _release_consumed_parents(nid)

            checkpoints_since_gc += 1
            if checkpoints_since_gc >= _GC_BATCH_INTERVAL:
                gc.collect()
                _malloc_trim()
                checkpoints_since_gc = 0

            lf = pl.scan_parquet(tmp)
            logger.info("checkpoint_parquet", node_id=nid, path=str(tmp))
            if execution_context is not None:
                execution_context.checkpoint(label="after_checkpoint", node_id=nid)

        lazy_outputs[nid] = lf

    return lazy_outputs, order, parents_of, id_to_name


# ---------------------------------------------------------------------------
# Eager execution core — shared by executor (preview) and trace
# ---------------------------------------------------------------------------


def _build_funcs(
    order: list[str],
    node_map: dict[str, GraphNode],
    id_to_name: dict[str, str],
    all_parents: dict[str, list[str]],
    build_node_fn: Callable,
    *,
    incoming_edges_by_target: Mapping[str, list[GraphEdge]],
    all_incoming_edges_by_target: Mapping[str, list[GraphEdge]],
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
                src_names.append(edge_input_name(edge, source_node))
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
            all_incoming_edges_by_target,
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


def _declared_api_input_frame_schema_items(
    node: GraphNode,
) -> dict[str, list[tuple[str, str]]]:
    """Return all declared emitting-port schemas without opening payloads."""
    if node.data.nodeType is not NodeType.API_INPUT or not isinstance(
        node.data.config.get("tables"),
        list,
    ):
        return {}
    from haute._json_shred import _declared_frame_schema, _emitting_table_specs

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
) -> EagerResult:
    """Execute the graph eagerly in topo order and collect DataFrames.

    Shared core for the preview executor and the trace engine.

    Args:
        graph: React Flow graph.
        build_node_fn: ``(node, source_names=..., ...) -> (name, fn, is_source)``.
        target_node_id: If set, only execute ancestors of this node.
        row_limit: Cap source-node output to this many rows.
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

    Returns:
        An ``EagerResult`` with named fields for outputs, order,
        parents_of, node_map, id_to_name, errors, timings, and
        memory_bytes.
    """
    graph = _resolve_graph_paths(graph)
    prepared = projection_planner.prepare_graph(
        graph,
        target_node_id,
        source=source,
    )
    node_map = prepared.node_map
    order = prepared.order
    parents_of = prepared.parents_of
    id_to_name = prepared.id_to_name
    relevant_edges = prepared.relevant_edges
    # See _execute_lazy: the parser is bypassed for frontend-built graphs, so
    # re-check graph-shape contracts on the executed subset to give a clean
    # ParseError instead of a Polars TypeError.
    validate_pipeline_graph_shape_contracts(
        graph,
        graph_label=graph.pipeline_name or "execution",
        node_ids_to_validate=set(order) if target_node_id is not None else None,
    )
    normalised_required_columns = _normalise_required_columns_by_node(
        required_columns_by_node,
        order,
    )
    strict_contract_resolution = _strict_contract_resolution(
        execution_context.profile if execution_context is not None else None
    )
    materialized_ids = None if materialize_node_ids is None else frozenset(materialize_node_ids)
    materialize_column_limits = dict(materialize_column_limits_by_node or {})
    for limit_node_id, limit in materialize_column_limits.items():
        if not isinstance(limit_node_id, str) or not limit_node_id:
            raise ValueError("materialize_column_limits_by_node keys must be node ids")
        if type(limit) is not int or limit < 1:
            raise ValueError("materialize column limits must be positive integers")

    # Full parent lookup from ALL edges for instance resolution
    all_parents = graph.parents_of

    # Per-target incoming-edge lookup (eager path). Use ``relevant_edges``
    # so live-switch pruning is honoured.
    incoming_edges_by_target: dict[str, list[GraphEdge]] = {}
    for edge in relevant_edges:
        incoming_edges_by_target.setdefault(edge.target, []).append(edge)
    all_incoming_edges_by_target: dict[str, list[GraphEdge]] = {}
    for edge in graph.edges:
        all_incoming_edges_by_target.setdefault(edge.target, []).append(edge)

    # Fan-out count per node — how many direct children consume this
    # node's output.  Used to add a Polars ``.cache()`` hint when the
    # parent feeds >1 consumer so the optimiser reuses one materialized
    # plan across branches (diamond graphs) instead of duplicating the
    # upstream work.  A parent may be either a concrete DataFrame
    # (traditional eager preview/trace) or a LazyFrame (target-only
    # preview), and both can carry the hint into downstream collection.
    children_count: dict[str, int] = dict.fromkeys(order, 0)
    children_of: dict[str, list[str]] = {nid: [] for nid in order}
    for _nid, _pids in parents_of.items():
        for _pid in _pids:
            if _pid in children_count:
                children_count[_pid] += 1
                children_of[_pid].append(_nid)

    # Count fan-out by the selected source frame, rather than only by node.
    # A multi-frame producer can expose multiple independent source ports.
    frame_fanout_count: dict[tuple[str, str | None], int] = {}
    for edge in relevant_edges:
        if edge.source in node_map and edge.target in node_map:
            frame_key = (edge.source, edge.sourceHandle)
            frame_fanout_count[frame_key] = frame_fanout_count.get(frame_key, 0) + 1

    context_strategy = execution_context.projection_plan if execution_context is not None else None
    if normalised_required_columns:
        projection_plan = projection_planner.compute_prepared_plan(
            order,
            children_of,
            node_map,
            required_columns_by_node=normalised_required_columns,
            strict_projection=_strict_projection_for_context(
                execution_context,
                normalised_required_columns,
            ),
            relevant_edges=relevant_edges,
        )
    else:
        projection_plan = None
    needed_cols: Mapping[str, frozenset[str] | None] = (
        projection_plan.needed_by_node if projection_plan is not None else {}
    )
    builder_needed_cols = projection_planner.builder_required_output_columns_by_node(
        node_map,
        needed_cols,
        preserve_eager_model_score_inputs=True,
    )
    # Public strategy planning also runs for an unseeded first-click preview.
    # Reuse that proof only at the API port-loading seam: applying its complete
    # node demands as eager output projections would change established output
    # and schema-reporting semantics for unrelated nodes.
    port_projection_plan = (
        context_strategy.projection_plan
        if isinstance(context_strategy, projection_planner.ExecutionStrategyResult)
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
        order,
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

    for nid in order:
        fn, is_source = funcs[nid]
        node = node_map[nid]
        if execution_context is not None:
            requested_columns = needed_cols.get(nid)
            execution_context.record_column_widths(
                node_id=nid,
                requested_width=(None if requested_columns is None else len(requested_columns)),
            )
        contract = (
            _resolve_effective_contract(
                node,
                strict=strict_contract_resolution,
            ).contract
            if enforce_contracts
            else None
        )
        check_here = bool(contract) and _should_check_contract(contract)  # type: ignore[arg-type]
        # A node that the builder chose to wire to ``_passthrough_fn`` is
        # running in a stub/unconfigured state (MODEL_SCORE without a
        # loaded model, OPTIMISER_APPLY without an artifact, etc.).  Its
        # contract describes the *configured* shape, which the runtime
        # intentionally does not produce yet.  Skip the output-side
        # check to preserve the "drag node onto canvas, configure later"
        # UX while still enforcing contracts the moment a real function
        # is wired in.
        is_passthrough_runtime = fn is _passthrough_fn
        if execution_context is not None:
            execution_context.checkpoint(label="before_node", node_id=nid)
        t0 = time.perf_counter()
        try:
            if is_source:
                result = fn()
                if row_limit and isinstance(result, (pl.LazyFrame, pl.DataFrame)):
                    result = result.head(row_limit)
            else:
                input_ids = parents_of.get(nid, [])
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
                incoming_edges_for_node = incoming_edges_by_target.get(nid, [])
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

                runtime_edge_demands = _runtime_join_demands(
                    node,
                    incoming_edges_for_node,
                    input_lfs,
                    needed_cols.get(nid),
                    projection_plan.edge_demands if projection_plan is not None else {},
                    node_map,
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
                        selected = [column for column in schema_names if column in demand]
                        if not selected and not demand and schema_names:
                            selected = schema_names[:1]
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
                        refined_plan = projection_planner.with_runtime_inferred_streaming_edges(
                            current_strategy.projection_plan,
                            demands_by_edge=runtime_edge_demands,
                            resolved_parent_ids=_runtime_projectable_source_ids(
                                (key.source for key in runtime_edge_demands),
                                node_map,
                            ),
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
                                headroom_bytes=previous_diagnostic.headroom_bytes,
                                assumptions=previous_diagnostic.assumptions,
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
                    _assert_inputs_satisfy_contract(node, contract, upstream_cols)  # type: ignore[arg-type]

                if enforce_contracts:
                    _assert_simple_join_key_dtypes_compatible(node, input_ids, input_lfs)

                result = fn(*input_lfs)

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
                # Head-cap each frame's lazy plan up front (before any
                # collect/schema) like the single-frame source path (the
                # ``row_limit`` head at the top of this loop): a preview
                # row_limit must reach the per-frame plans of a multi-frame
                # source too, or they collect in full while single-frame
                # sources cap. The cap is a no-op
                # for the schema-only ancestor path but keeps the lazy plan
                # consistent with what a downstream collect would see.
                capped_ports: dict[str, pl.LazyFrame | pl.DataFrame] = {}
                for port_label, port_frame in result.items():
                    if isinstance(port_frame, (pl.LazyFrame, pl.DataFrame)):
                        capped_ports[port_label] = (
                            port_frame.head(row_limit) if row_limit else port_frame
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
                    # consumers need a LazyFrame.
                    runtime_outputs[nid] = materialised
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
                    for port_label, capped in capped_ports.items():
                        port_lf = capped if isinstance(capped, pl.LazyFrame) else capped.lazy()
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
                _assert_outputs_satisfy_contract(node, contract, final_cols)  # type: ignore[arg-type]

            projection = needed_cols.get(nid)
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
                runtime_outputs[nid] = df
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

    return EagerResult(
        eager_outputs,
        order,
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
    )
