"""Public chunk-planning and chunk-runner contracts.

The chunk runner is deliberately narrower than the lazy executor: it only
executes graphs that already have a proven :class:`ChunkPlan`.  Unsupported
shapes fail before execution rather than falling back to full materialisation.
"""

from __future__ import annotations

import ast
import contextlib
import math
import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol

import polars as pl

from haute._builders import _DEFAULT_SCENARIO_STEPS
from haute._execution_context import ExecutionProfile
from haute._io import read_data_source
from haute._logging import get_logger
from haute._polars_utils import DEFAULT_STREAMING_CHUNK_SIZE, streaming_collect
from haute._types import GraphNode, NodeType, PipelineGraph
from haute.errors import ChunkPlanUnsupportedError, ContractMismatchError
from haute.execution import ProjectionRequest, plan_execution_strategy
from haute.projection import prepare_graph

__all__ = [
    "BoundedChunkReducer",
    "ChunkBatch",
    "ChunkCapability",
    "ChunkCapabilityDeclaration",
    "ChunkCapabilityKind",
    "ChunkCapabilityStatus",
    "ChunkPlan",
    "ChunkPlanRequest",
    "ChunkRunnerRequest",
    "chunk_plan",
    "chunk_capability_declarations",
    "is_chunk_local_polars_code",
    "collect_chunked",
    "iter_chunked_frames",
    "run_chunked_reduce",
    "validate_chunk_capability_declarations",
]


logger = get_logger(component="chunking")


class ChunkCapabilityKind(StrEnum):
    """Coarse physical chunking semantics for a node."""

    MAP_ONLY = "map_only"
    BOUNDED_STATE = "bounded_state"


class ChunkCapabilityStatus(StrEnum):
    """Whether a node type participates in the V1 chunk contract."""

    SUPPORTED = "supported"
    CONDITIONAL = "conditional"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class ChunkCapability:
    """A node's chunk execution capability."""

    kind: ChunkCapabilityKind
    preserves_row_order: bool
    supports_fan_in: bool = False
    expands_rows: bool = False
    state_crosses_chunks: bool = False
    model_reuse_lifetime: str | None = None
    row_multiplier: int = 1


@dataclass(frozen=True, slots=True)
class ChunkCapabilityDeclaration:
    """Registry entry for a node type's chunk-planning contract."""

    node_type: NodeType
    status: ChunkCapabilityStatus
    rules: frozenset[str]
    note: str = ""


@dataclass(frozen=True, slots=True)
class ChunkPlanRequest:
    """Inputs required to build a chunk execution plan."""

    graph: PipelineGraph
    target_node_id: str
    chunk_size: int | None = None
    target_chunk_bytes: int | None = None
    chunk_start_node_id: str | None = None
    required_columns_by_node: Mapping[str, Iterable[str]] | None = None
    source: str = "batch"


@dataclass(frozen=True, slots=True)
class ChunkPlan:
    """A proven chunk-safe physical plan."""

    source_node_id: str | None
    chunk_start_node_id: str
    target_node_id: str
    node_ids: tuple[str, ...]
    pre_chunk_node_ids: tuple[str, ...]
    chunk_node_ids: tuple[str, ...]
    chunk_size: int
    source_chunk_size: int
    target_chunk_bytes: int | None
    estimated_source_row_bytes: int | None
    estimated_target_row_bytes: int | None
    chunk_size_policy: str
    row_expansion_factor: int
    capabilities: Mapping[str, ChunkCapability]
    required_columns_by_node: Mapping[str, frozenset[str] | None]
    edge_demands: Mapping[tuple[str, str], frozenset[str] | None]
    source: str = "batch"
    max_in_flight_chunks: int = 1
    serial: bool = True


@dataclass(frozen=True, slots=True)
class ChunkRunnerRequest:
    """Inputs for executing a previously proven chunk plan."""

    graph: PipelineGraph
    plan: ChunkPlan
    build_node_fn: Callable[..., tuple[str, Callable[..., Any], bool]]
    preamble_ns: dict[str, Any] | None = None
    execution_context: Any | None = None
    checkpoint_dir: Path | None = None
    start_frame: pl.LazyFrame | pl.DataFrame | None = None
    cleanup_checkpoints_on_error: bool = True
    streaming_chunk_size: int | None = None


@dataclass(frozen=True, slots=True)
class ChunkBatch:
    """One bounded target chunk emitted by the chunk runner."""

    index: int
    frame: pl.DataFrame
    source_rows: int
    output_rows: int
    checkpoint_path: Path | None = None


class BoundedChunkReducer(Protocol):
    """Protocol for reducers that can consume chunks without retaining rows."""

    bounded: bool

    def add(self, batch: ChunkBatch) -> None:
        """Consume one emitted chunk."""

    def finish(self) -> Any:
        """Return the reduced result."""


_DIRECT_FILE_SCAN_RULE_NAME = "direct_file_scan"
_ROW_LOCAL_POLARS_RULE_NAME = "row_local_polars"
_SINGLE_PARENT_SUFFIX_RULE_NAME = "single_parent_suffix"
_NO_RATING_STEP_CODE_RULE_NAME = "rating_step_without_user_code"
_SCENARIO_EXPANDER_RULE_NAME = "scenario_expander_row_multiplier"
_MODEL_SCORE_BATCH_REUSE_RULE_NAME = "model_score_batch_reuse"
_MAP_ONLY_RULE_NAME = "map_only"
_UNSUPPORTED_V1_RULE_NAME = "unsupported_v1"
_CHUNK_SIZE_POLICY_EXPLICIT_ROWS = "explicit_rows"
_CHUNK_SIZE_POLICY_TARGET_BYTES = "byte_budget"
_DEFAULT_PROJECTED_COLUMN_BYTES = 64
_ROW_BYTE_SAMPLE_SIZE = 128
_FIXED_DTYPE_BYTES: Mapping[Any, int] = MappingProxyType(
    {
        pl.Boolean: 1,
        pl.Int8: 1,
        pl.UInt8: 1,
        pl.Int16: 2,
        pl.UInt16: 2,
        pl.Int32: 4,
        pl.UInt32: 4,
        pl.Float32: 4,
        pl.Date: 4,
        pl.Int64: 8,
        pl.UInt64: 8,
        pl.Float64: 8,
        pl.Datetime: 8,
        pl.Duration: 8,
        pl.Time: 8,
    }
)
_VARIABLE_WIDTH_COLUMN_BYTES = 64
_UNKNOWN_WIDTH_COLUMN_BYTES = 64


def _chunk_declaration(
    node_type: NodeType,
    status: ChunkCapabilityStatus,
    *rules: str,
    note: str = "",
) -> ChunkCapabilityDeclaration:
    return ChunkCapabilityDeclaration(
        node_type=node_type,
        status=status,
        rules=frozenset(rules),
        note=note,
    )


_CHUNK_CAPABILITY_DECLARATIONS: Mapping[NodeType, ChunkCapabilityDeclaration] = MappingProxyType(
    {
        NodeType.API_INPUT: _chunk_declaration(
            NodeType.API_INPUT,
            ChunkCapabilityStatus.UNSUPPORTED,
            _UNSUPPORTED_V1_RULE_NAME,
            note=(
                "request-payload sources are bounded by admission, not the file-scan chunk runner"
            ),
        ),
        NodeType.DATA_SOURCE: _chunk_declaration(
            NodeType.DATA_SOURCE,
            ChunkCapabilityStatus.CONDITIONAL,
            _DIRECT_FILE_SCAN_RULE_NAME,
        ),
        NodeType.POLARS: _chunk_declaration(
            NodeType.POLARS,
            ChunkCapabilityStatus.CONDITIONAL,
            _SINGLE_PARENT_SUFFIX_RULE_NAME,
            _ROW_LOCAL_POLARS_RULE_NAME,
        ),
        NodeType.MODEL_SCORE: _chunk_declaration(
            NodeType.MODEL_SCORE,
            ChunkCapabilityStatus.CONDITIONAL,
            _MODEL_SCORE_BATCH_REUSE_RULE_NAME,
        ),
        NodeType.BANDING: _chunk_declaration(
            NodeType.BANDING,
            ChunkCapabilityStatus.SUPPORTED,
            _MAP_ONLY_RULE_NAME,
        ),
        NodeType.RATING_STEP: _chunk_declaration(
            NodeType.RATING_STEP,
            ChunkCapabilityStatus.CONDITIONAL,
            _NO_RATING_STEP_CODE_RULE_NAME,
        ),
        NodeType.OUTPUT: _chunk_declaration(
            NodeType.OUTPUT,
            ChunkCapabilityStatus.SUPPORTED,
            _MAP_ONLY_RULE_NAME,
        ),
        NodeType.DATA_SINK: _chunk_declaration(
            NodeType.DATA_SINK,
            ChunkCapabilityStatus.UNSUPPORTED,
            _UNSUPPORTED_V1_RULE_NAME,
            note=("sink writes use bounded sink contracts rather than the map-reduce chunk runner"),
        ),
        NodeType.EXTERNAL_FILE: _chunk_declaration(
            NodeType.EXTERNAL_FILE,
            ChunkCapabilityStatus.UNSUPPORTED,
            _UNSUPPORTED_V1_RULE_NAME,
            note=(
                "external file nodes are schema/artifact references, not chunk-runner source scans"
            ),
        ),
        NodeType.LIVE_SWITCH: _chunk_declaration(
            NodeType.LIVE_SWITCH,
            ChunkCapabilityStatus.UNSUPPORTED,
            _UNSUPPORTED_V1_RULE_NAME,
            note="live-switch branch selection has not declared chunk-local semantics",
        ),
        NodeType.MODELLING: _chunk_declaration(
            NodeType.MODELLING,
            ChunkCapabilityStatus.UNSUPPORTED,
            _UNSUPPORTED_V1_RULE_NAME,
            note="training preparation is governed by training memory contracts",
        ),
        NodeType.OPTIMISER: _chunk_declaration(
            NodeType.OPTIMISER,
            ChunkCapabilityStatus.UNSUPPORTED,
            _UNSUPPORTED_V1_RULE_NAME,
            note="optimiser solve state is handled by optimiser-specific reducers",
        ),
        NodeType.SCENARIO_EXPANDER: _chunk_declaration(
            NodeType.SCENARIO_EXPANDER,
            ChunkCapabilityStatus.CONDITIONAL,
            _SCENARIO_EXPANDER_RULE_NAME,
            _ROW_LOCAL_POLARS_RULE_NAME,
        ),
        NodeType.OPTIMISER_APPLY: _chunk_declaration(
            NodeType.OPTIMISER_APPLY,
            ChunkCapabilityStatus.SUPPORTED,
            _MAP_ONLY_RULE_NAME,
        ),
        NodeType.CONSTANT: _chunk_declaration(
            NodeType.CONSTANT,
            ChunkCapabilityStatus.UNSUPPORTED,
            _UNSUPPORTED_V1_RULE_NAME,
            note="constant nodes are not a bounded scan source in the V1 runner",
        ),
        NodeType.SUBMODEL: _chunk_declaration(
            NodeType.SUBMODEL,
            ChunkCapabilityStatus.UNSUPPORTED,
            _UNSUPPORTED_V1_RULE_NAME,
            note="submodels must be expanded before chunk contracts can be proven",
        ),
        NodeType.SUBMODEL_PORT: _chunk_declaration(
            NodeType.SUBMODEL_PORT,
            ChunkCapabilityStatus.UNSUPPORTED,
            _UNSUPPORTED_V1_RULE_NAME,
            note="submodel ports inherit semantics from their expanded concrete graph",
        ),
    }
)


def chunk_capability_declarations() -> Mapping[NodeType, ChunkCapabilityDeclaration]:
    """Return the immutable node-type chunk capability registry."""
    return _CHUNK_CAPABILITY_DECLARATIONS


def validate_chunk_capability_declarations(
    declarations: Mapping[NodeType, ChunkCapabilityDeclaration] | None = None,
) -> None:
    """Fail loudly if chunk capability declarations drift from known node types."""
    registry = declarations or _CHUNK_CAPABILITY_DECLARATIONS
    expected = set(NodeType)
    observed = set(registry)
    missing = expected - observed
    extra = observed - expected
    if missing or extra:
        raise RuntimeError(
            "Chunk capability declarations must mention every node type exactly once. "
            f"Missing={sorted(node.value for node in missing)}; "
            f"extra={sorted(node.value for node in extra)}"
        )

    for node_type, declaration in registry.items():
        if declaration.node_type != node_type:
            raise RuntimeError(
                "Chunk capability declaration is keyed under the wrong node type. "
                f"key={node_type.value!r}, entry={declaration.node_type.value!r}"
            )
        if not declaration.rules:
            raise RuntimeError(
                f"Chunk capability declaration for node type {node_type.value!r} has no rules."
            )
        if declaration.status == ChunkCapabilityStatus.UNSUPPORTED:
            if declaration.rules != frozenset({_UNSUPPORTED_V1_RULE_NAME}):
                raise RuntimeError(
                    f"Unsupported chunk declaration for node type {node_type.value!r} "
                    f"must use only {_UNSUPPORTED_V1_RULE_NAME!r}."
                )
            if not declaration.note:
                raise RuntimeError(
                    f"Unsupported chunk declaration for node type {node_type.value!r} "
                    "must explain why it is unsupported."
                )
        elif _UNSUPPORTED_V1_RULE_NAME in declaration.rules:
            raise RuntimeError(
                f"Chunk-capable node type {node_type.value!r} cannot include "
                f"{_UNSUPPORTED_V1_RULE_NAME!r}."
            )


validate_chunk_capability_declarations()


_GLOBAL_CODE_MARKERS = (
    ".collect(",
    ".collect_batches(",
    ".fetch(",
    ".group_by(",
    ".groupby(",
    ".join(",
    ".pivot(",
    ".sort(",
    ".unique(",
    ".upsample(",
    ".window(",
)
_GLOBAL_METHOD_NAMES = frozenset(
    {
        "agg",
        "collect",
        "collect_batches",
        "explode",
        "fetch",
        "group_by",
        "groupby",
        "join",
        "pivot",
        "rolling",
        "sort",
        "unique",
        "upsample",
    }
)
_ROW_LOCAL_DF_METHOD_NAMES = frozenset(
    {
        "cast",
        "drop",
        "drop_nulls",
        "filter",
        "fill_nan",
        "fill_null",
        "rename",
        "select",
        "with_columns",
        "with_columns_seq",
    }
)
_ROW_LOCAL_EXPR_METHOD_NAMES = frozenset(
    {
        "abs",
        "alias",
        "cast",
        "ceil",
        "clip",
        "exp",
        "fill_nan",
        "fill_null",
        "floor",
        "is_between",
        "is_finite",
        "is_in",
        "is_infinite",
        "is_nan",
        "is_not_nan",
        "is_not_null",
        "is_null",
        "log",
        "not_",
        "otherwise",
        "round",
        "sqrt",
        "then",
    }
)
_ROW_LOCAL_POLARS_FUNCTIONS = frozenset(
    {
        "all_horizontal",
        "any_horizontal",
        "coalesce",
        "col",
        "concat_str",
        "lit",
        "max_horizontal",
        "min_horizontal",
        "when",
    }
)


def _row_local_args_are_supported(
    values: Iterable[ast.AST],
    *,
    allowed_frames: set[str],
    local_frames: set[str],
) -> bool:
    for value in values:
        supported, derived_from_frame = _row_local_expr_is_supported(
            value,
            allowed_frames=allowed_frames,
            local_frames=local_frames,
        )
        if not supported or derived_from_frame:
            return False
    return True


def is_chunk_local_polars_code(
    code: object,
    *,
    frame_names: Iterable[str] | None = None,
) -> bool:
    """Return whether user Polars code is safe to apply independently per chunk."""

    if not isinstance(code, str) or not code.strip():
        return True
    allowed_frames = {name for name in (frame_names or ()) if name}
    if not allowed_frames:
        return False
    compact = re.sub(r"\s+", "", code).lower()
    if any(marker in compact for marker in _GLOBAL_CODE_MARKERS):
        return False
    try:
        module = ast.parse(code)
    except SyntaxError:
        return False
    local_frames: set[str] = set()
    return all(
        _row_local_stmt_is_supported(
            stmt,
            allowed_frames=allowed_frames,
            local_frames=local_frames,
        )
        for stmt in module.body
    )


def _validate_positive_int(value: object, *, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")


def _plan_chunk_sizes(
    request: ChunkPlanRequest,
    *,
    prepared: Any,
    source_node_id: str | None,
    chunk_start_node_id: str,
    row_expansion_factor: int,
    projection: Any,
) -> tuple[int, int, int | None, int | None]:
    expansion = max(1, row_expansion_factor)
    if request.chunk_size is not None:
        chunk_size = request.chunk_size
        return chunk_size, max(1, chunk_size // expansion), None, None

    assert request.target_chunk_bytes is not None
    target_columns = projection.needed_by_node.get(request.target_node_id)
    target_row_bytes = _estimate_projected_row_bytes(
        target_columns,
        source_node=(prepared.node_map[source_node_id] if source_node_id is not None else None),
        target_node_id=request.target_node_id,
    )
    source_columns = projection.needed_by_node.get(chunk_start_node_id)
    source_row_bytes = (
        None
        if source_columns is None
        else _estimate_projected_row_bytes(
            source_columns,
            source_node=prepared.node_map[chunk_start_node_id],
            target_node_id=chunk_start_node_id,
        )
    )
    chunk_size = max(1, request.target_chunk_bytes // target_row_bytes)
    source_chunk_size = max(1, chunk_size // expansion)
    return chunk_size, source_chunk_size, source_row_bytes, target_row_bytes


def _estimate_projected_row_bytes(
    projected_columns: frozenset[str] | None,
    *,
    source_node: GraphNode | None,
    target_node_id: str,
) -> int:
    if projected_columns is None:
        raise ChunkPlanUnsupportedError(
            "Byte-budgeted chunk planning requires concrete projected columns.",
            target_node_id=target_node_id,
        )
    if not projected_columns:
        return 1

    source_widths = (
        _source_projected_column_widths(source_node, projected_columns)
        if source_node is not None
        else {}
    )
    estimated = sum(
        source_widths.get(column, _DEFAULT_PROJECTED_COLUMN_BYTES) for column in projected_columns
    )
    return max(1, estimated)


def _source_projected_column_widths(
    node: GraphNode,
    projected_columns: frozenset[str],
) -> dict[str, int]:
    if node.data.nodeType != NodeType.DATA_SOURCE:
        return {}

    try:
        lf = _source_lazy_frame(node)
        schema = lf.collect_schema()
        schema_by_name = dict(schema.items())
        columns = [column for column in schema.names() if column in projected_columns]
        widths = {column: _dtype_estimated_width(schema_by_name[column]) for column in columns}
        if not columns:
            return widths

        sample = streaming_collect(
            lf.select(columns).limit(_ROW_BYTE_SAMPLE_SIZE),
            profile=ExecutionProfile.CHUNKED_MAP_REDUCE,
        )
    except Exception as exc:
        raise ChunkPlanUnsupportedError(
            "Byte-budgeted chunk planning could not inspect source schema.",
            node_id=node.id,
            node_type=node.data.nodeType.value,
            path=node.data.config.get("path"),
        ) from exc

    if sample.height == 0:
        return widths
    for column in columns:
        sampled_width = math.ceil(sample[column].estimated_size() / sample.height)
        widths[column] = max(widths[column], sampled_width)
    return widths


def _dtype_estimated_width(dtype: pl.DataType) -> int:
    return _FIXED_DTYPE_BYTES.get(dtype.base_type(), _DEFAULT_PROJECTED_COLUMN_BYTES)


def chunk_plan(request: ChunkPlanRequest) -> ChunkPlan:
    """Build a fail-loud chunk plan for a target graph.

    This is the planning substrate for Slice 6. It deliberately proves
    eligibility before any runtime chunk runner can execute. Unsupported
    shapes raise :class:`ChunkPlanUnsupportedError` rather than falling back
    to a broad materialisation.
    """
    if request.chunk_size is None and request.target_chunk_bytes is None:
        raise ValueError("chunk_size or target_chunk_bytes must be provided")
    if request.chunk_size is not None and request.target_chunk_bytes is not None:
        raise ValueError("Specify either chunk_size or target_chunk_bytes, not both")
    if request.chunk_size is not None:
        _validate_positive_int(request.chunk_size, field_name="chunk_size")
    if request.target_chunk_bytes is not None:
        _validate_positive_int(
            request.target_chunk_bytes,
            field_name="target_chunk_bytes",
        )

    from haute._builders import resolve_instance_node

    prepared = prepare_graph(
        request.graph,
        request.target_node_id,
        source=request.source,
    )
    source_node_ids: list[str] = []
    for node_id in prepared.order:
        node = prepared.node_map[node_id]
        if node.data.nodeType == NodeType.DATA_SOURCE:
            source_node_ids.append(node_id)

    if request.chunk_start_node_id is None and len(source_node_ids) != 1:
        raise ChunkPlanUnsupportedError(
            "Chunked execution currently requires exactly one dataSource root.",
            source_node_ids=source_node_ids,
            target_node_id=request.target_node_id,
        )
    source_node_id = source_node_ids[0] if len(source_node_ids) == 1 else None
    chunk_start_node_id = request.chunk_start_node_id or source_node_id
    if chunk_start_node_id is None:
        raise ChunkPlanUnsupportedError(
            "chunk_start_node_id is required when a target has multiple dataSource roots.",
            source_node_ids=source_node_ids,
            target_node_id=request.target_node_id,
        )
    if chunk_start_node_id not in prepared.order:
        raise ChunkPlanUnsupportedError(
            "chunk_start_node_id is not an ancestor of the chunk target.",
            chunk_start_node_id=chunk_start_node_id,
            target_node_id=request.target_node_id,
        )

    start_index = prepared.order.index(chunk_start_node_id)
    pre_chunk_node_ids = tuple(prepared.order[:start_index])
    chunk_node_ids = tuple(prepared.order[start_index:])
    capabilities: dict[str, ChunkCapability] = {}
    row_expansion_factor = 1
    for node_id in prepared.order:
        node = resolve_instance_node(prepared.node_map[node_id], prepared.node_map)
        parent_ids = prepared.parents_of.get(node_id, [])
        if node_id in pre_chunk_node_ids or (
            node_id == chunk_start_node_id and chunk_start_node_id != source_node_id
        ):
            if node.data.nodeType == NodeType.DATA_SOURCE:
                _validate_chunkable_source(node)
            capability = ChunkCapability(
                kind=ChunkCapabilityKind.BOUNDED_STATE,
                preserves_row_order=True,
                state_crosses_chunks=True,
            )
        else:
            capability = _capability_for_node(
                node,
                parent_ids,
                frame_names=[
                    prepared.id_to_name[parent_id]
                    for parent_id in parent_ids
                    if parent_id in prepared.id_to_name
                ],
            )
            row_expansion_factor *= capability.row_multiplier
        capabilities[node_id] = capability
    _validate_chunk_suffix_is_single_parent(
        chunk_node_ids,
        chunk_start_node_id=chunk_start_node_id,
        parents_of=prepared.parents_of,
    )

    projection = plan_execution_strategy(
        ProjectionRequest(
            graph=request.graph,
            target_node_id=request.target_node_id,
            profile=ExecutionProfile.CHUNKED_MAP_REDUCE,
            required_columns_by_node=request.required_columns_by_node,
            source=request.source,
        )
    )
    chunk_size, source_chunk_size, estimated_source_row_bytes, estimated_target_row_bytes = (
        _plan_chunk_sizes(
            request,
            prepared=prepared,
            source_node_id=source_node_id,
            chunk_start_node_id=chunk_start_node_id,
            row_expansion_factor=row_expansion_factor,
            projection=projection,
        )
    )
    chunk_size_policy = (
        _CHUNK_SIZE_POLICY_EXPLICIT_ROWS
        if request.chunk_size is not None
        else _CHUNK_SIZE_POLICY_TARGET_BYTES
    )

    plan = ChunkPlan(
        source_node_id=source_node_id,
        chunk_start_node_id=chunk_start_node_id,
        target_node_id=request.target_node_id,
        node_ids=tuple(prepared.order),
        pre_chunk_node_ids=pre_chunk_node_ids,
        chunk_node_ids=chunk_node_ids,
        chunk_size=chunk_size,
        source_chunk_size=source_chunk_size,
        target_chunk_bytes=request.target_chunk_bytes,
        estimated_source_row_bytes=estimated_source_row_bytes,
        estimated_target_row_bytes=estimated_target_row_bytes,
        chunk_size_policy=chunk_size_policy,
        row_expansion_factor=row_expansion_factor,
        capabilities=MappingProxyType(capabilities),
        required_columns_by_node=projection.needed_by_node,
        edge_demands=projection.edge_demands,
        source=request.source,
    )
    logger.info(
        "chunk_plan_built",
        target_node_id=plan.target_node_id,
        chunk_start_node_id=plan.chunk_start_node_id,
        chunk_size=plan.chunk_size,
        source_chunk_size=plan.source_chunk_size,
        chunk_size_policy=plan.chunk_size_policy,
        row_expansion_factor=plan.row_expansion_factor,
        node_count=len(plan.node_ids),
    )
    return plan


def iter_chunked_frames(request: ChunkRunnerRequest) -> Iterator[ChunkBatch]:
    """Yield bounded target DataFrames for a proven map-only chunk plan.

    The runner honours the projection plan embedded in ``request.plan`` and
    applies the same node builder functions used by the lazy/eager executors.
    It intentionally executes serially with one chunk in flight.
    """

    plan = request.plan
    if not plan.serial or plan.max_in_flight_chunks != 1:
        raise ChunkPlanUnsupportedError(
            "Chunk runner currently supports only serial plans with one in-flight chunk.",
            target_node_id=plan.target_node_id,
            max_in_flight_chunks=plan.max_in_flight_chunks,
        )
    from haute._execute_lazy import (
        _apply_column_renames,
        _apply_selected_columns,
        _build_funcs,
        _resolve_graph_paths,
    )
    from haute._polars_utils import (
        bounded_collect_batches,
        streaming_collect,
        temporary_streaming_chunk_size,
    )

    graph = _resolve_graph_paths(request.graph)
    prepared = prepare_graph(graph, plan.target_node_id, source=plan.source)
    _assert_plan_matches_prepared_graph(plan, prepared.order)
    node_map = prepared.node_map
    parents_of = prepared.parents_of
    active_fan_in_edge_demands = {
        edge: columns
        for edge, columns in plan.edge_demands.items()
        if len(parents_of.get(edge[1], ())) > 1
        and not (
            request.start_frame is not None
            and (edge[1] in plan.pre_chunk_node_ids or edge[1] == plan.chunk_start_node_id)
        )
    }
    if active_fan_in_edge_demands:
        raise ChunkPlanUnsupportedError(
            "Chunk runner V1 does not execute fan-in edge projection plans.",
            target_node_id=plan.target_node_id,
            edge_count=len(active_fan_in_edge_demands),
        )
    _assert_runner_shape(plan, node_map, parents_of)

    source_node = node_map[plan.chunk_start_node_id]
    if request.start_frame is None:
        if plan.source_node_id is None or plan.chunk_start_node_id != plan.source_node_id:
            raise ChunkPlanUnsupportedError(
                "Chunk plans with a non-root chunk_start_node_id require start_frame.",
                chunk_start_node_id=plan.chunk_start_node_id,
                source_node_id=plan.source_node_id,
            )
        source_lf = _source_lazy_frame(source_node)
        source_lf = _normalise_lazy_frame(
            _apply_selected_columns(source_lf, source_node.data.config)
        )
        source_lf = _normalise_lazy_frame(_apply_column_renames(source_lf, source_node.data.config))
    else:
        source_lf = _normalise_lazy_frame(request.start_frame)
    source_lf = _project_frame(
        source_lf,
        plan.required_columns_by_node.get(plan.chunk_start_node_id),
        node=source_node,
    )

    builder_required = {
        node_id: (None if columns is None else frozenset(str(column) for column in columns))
        for node_id, columns in plan.required_columns_by_node.items()
    }
    reuse_loaded_model_by_node = {
        node_id: True
        for node_id, capability in plan.capabilities.items()
        if capability.model_reuse_lifetime == "batch"
    }
    funcs = _build_funcs(
        list(plan.node_ids),
        node_map,
        parents_of,
        prepared.id_to_name,
        graph.parents_of,
        request.build_node_fn,
        preamble_ns=request.preamble_ns,
        source="live",
        required_output_columns_by_node=builder_required,
        reuse_loaded_model_by_node=reuse_loaded_model_by_node,
    )

    context = request.execution_context
    checkpoint_dir = request.checkpoint_dir
    written_checkpoints: list[Path] = []
    completed = False
    yielded_chunks = 0
    chunk_size_stack = contextlib.ExitStack()
    chunk_size_stack.enter_context(
        temporary_streaming_chunk_size(request.streaming_chunk_size or DEFAULT_STREAMING_CHUNK_SIZE)
    )
    logger.info(
        "chunk_runner_start",
        target_node_id=plan.target_node_id,
        chunk_start_node_id=plan.chunk_start_node_id,
        chunk_size=plan.chunk_size,
        source_chunk_size=plan.source_chunk_size,
    )
    try:
        if context is not None:
            context.checkpoint(label="chunk_runner_start")
        source_batches = bounded_collect_batches(
            source_lf,
            profile=(
                context.profile if context is not None else ExecutionProfile.CHUNKED_MAP_REDUCE
            ),
            chunk_size=plan.source_chunk_size,
            maintain_order=True,
            execution_context=context,
            stage_name="chunk_source_collect_batch",
            node_id=plan.chunk_start_node_id,
        )
        for chunk_index, source_batch in enumerate(source_batches):
            if context is not None:
                context.checkpoint(
                    label="before_chunk",
                    node_id=plan.chunk_start_node_id,
                )
            if source_batch.height == 0:
                continue

            outputs: dict[str, pl.LazyFrame] = {
                plan.chunk_start_node_id: source_batch.lazy(),
            }
            for node_id in plan.chunk_node_ids:
                if node_id == plan.chunk_start_node_id:
                    continue
                if context is not None:
                    context.checkpoint(label="before_node", node_id=node_id)
                fn, is_source = funcs[node_id]
                if is_source:
                    raise ChunkPlanUnsupportedError(
                        "Chunk runner encountered a non-root source node.",
                        node_id=node_id,
                        target_node_id=plan.target_node_id,
                    )
                parent_ids = parents_of.get(node_id, [])
                if len(parent_ids) != 1:
                    raise ChunkPlanUnsupportedError(
                        "Chunk runner V1 executes single-parent chains only.",
                        node_id=node_id,
                        parent_ids=parent_ids,
                    )
                parent_id = parent_ids[0]
                if parent_id not in outputs:
                    raise ChunkPlanUnsupportedError(
                        "Chunk runner parent output is unavailable.",
                        node_id=node_id,
                        parent_id=parent_id,
                    )
                with (
                    context.stage("chunk_node", node_id=node_id)
                    if context is not None
                    else contextlib.nullcontext()
                ):
                    result = fn(outputs[parent_id])
                    lf = _normalise_lazy_frame(result)
                    node = node_map[node_id]
                    lf = _normalise_lazy_frame(_apply_selected_columns(lf, node.data.config))
                    lf = _normalise_lazy_frame(_apply_column_renames(lf, node.data.config))
                    lf = _project_frame(
                        lf,
                        plan.required_columns_by_node.get(node_id),
                        node=node,
                    )
                outputs[node_id] = lf

            target_lf = outputs.get(plan.target_node_id)
            if target_lf is None:
                raise ChunkPlanUnsupportedError(
                    "Chunk runner target output is unavailable.",
                    target_node_id=plan.target_node_id,
                )
            with (
                context.stage("chunk_collect", node_id=plan.target_node_id)
                if context is not None
                else contextlib.nullcontext()
            ):
                frame = streaming_collect(
                    target_lf,
                    profile=(
                        context.profile
                        if context is not None
                        else ExecutionProfile.CHUNKED_MAP_REDUCE
                    ),
                )
            checkpoint_path = _write_chunk_checkpoint(
                frame,
                checkpoint_dir=checkpoint_dir,
                target_node_id=plan.target_node_id,
                chunk_index=chunk_index,
            )
            if checkpoint_path is not None:
                written_checkpoints.append(checkpoint_path)
            yield ChunkBatch(
                index=chunk_index,
                frame=frame,
                source_rows=source_batch.height,
                output_rows=frame.height,
                checkpoint_path=checkpoint_path,
            )
            yielded_chunks += 1
        completed = True
        logger.info(
            "chunk_runner_complete",
            target_node_id=plan.target_node_id,
            chunk_count=yielded_chunks,
        )
    except BaseException as exc:
        logger.warning(
            "chunk_runner_failed",
            target_node_id=plan.target_node_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        if request.cleanup_checkpoints_on_error:
            _cleanup_written_checkpoints(written_checkpoints)
        raise
    finally:
        if not completed and request.cleanup_checkpoints_on_error:
            _cleanup_written_checkpoints(written_checkpoints)
        chunk_size_stack.close()


def run_chunked_reduce(
    request: ChunkRunnerRequest,
    reducer: BoundedChunkReducer,
) -> Any:
    """Run a chunk plan into a bounded reducer and return its result."""

    if not getattr(reducer, "bounded", False):
        raise ChunkPlanUnsupportedError(
            "Chunk reducers must declare bounded=True; unbounded row retention "
            "is not allowed on chunked_map_reduce paths.",
            target_node_id=request.plan.target_node_id,
        )
    for batch in iter_chunked_frames(request):
        reducer.add(batch)
    return reducer.finish()


def collect_chunked(
    request: ChunkRunnerRequest,
    *,
    allow_unbounded: bool = False,
) -> pl.DataFrame:
    """Collect all chunk outputs.

    This helper is intentionally opt-in because retaining every chunk defeats
    the bounded-memory contract.  It is useful for tests and small diagnostics.
    Production callers should prefer :func:`iter_chunked_frames` or
    :func:`run_chunked_reduce`.
    """

    if not allow_unbounded:
        raise ChunkPlanUnsupportedError(
            "collect_chunked retains all rows; pass allow_unbounded=True only "
            "for tests or explicitly small diagnostics.",
            target_node_id=request.plan.target_node_id,
        )
    frames = [batch.frame for batch in iter_chunked_frames(request)]
    return pl.concat(frames, how="vertical") if frames else pl.DataFrame()


def _capability_for_node(
    node: GraphNode,
    parent_ids: list[str],
    *,
    frame_names: Iterable[str] = (),
) -> ChunkCapability:
    node_type = node.data.nodeType
    declaration = _CHUNK_CAPABILITY_DECLARATIONS[node_type]
    if declaration.status == ChunkCapabilityStatus.UNSUPPORTED:
        raise ChunkPlanUnsupportedError(
            "Node type is explicitly unsupported in the V1 chunk contract.",
            node_id=node.id,
            node_type=node_type.value,
            rules=sorted(declaration.rules),
            note=declaration.note,
        )

    if node_type == NodeType.DATA_SOURCE:
        _validate_chunkable_source(node)
        return ChunkCapability(
            kind=ChunkCapabilityKind.MAP_ONLY,
            preserves_row_order=True,
        )
    if node_type == NodeType.OUTPUT:
        return ChunkCapability(
            kind=ChunkCapabilityKind.MAP_ONLY,
            preserves_row_order=True,
        )
    if node_type == NodeType.POLARS:
        if len(parent_ids) > 1:
            raise ChunkPlanUnsupportedError(
                "Chunked row-local polars nodes must have exactly one parent.",
                node_id=node.id,
                node_type=node_type.value,
                parent_ids=parent_ids,
            )
        if not is_chunk_local_polars_code(
            node.data.config.get("code"),
            frame_names=frame_names,
        ):
            raise ChunkPlanUnsupportedError(
                "Chunked polars user code must be row-local in V1.",
                node_id=node.id,
                node_type=node_type.value,
            )
        return ChunkCapability(
            kind=ChunkCapabilityKind.MAP_ONLY,
            preserves_row_order=True,
        )
    if node_type == NodeType.BANDING:
        return ChunkCapability(
            kind=ChunkCapabilityKind.MAP_ONLY,
            preserves_row_order=True,
        )
    if node_type == NodeType.SCENARIO_EXPANDER:
        if not is_chunk_local_polars_code(node.data.config.get("code"), frame_names=("df",)):
            raise ChunkPlanUnsupportedError(
                "Chunked scenarioExpander post-processing code must be row-local in V1.",
                node_id=node.id,
                node_type=node_type.value,
            )
        row_multiplier = _scenario_row_multiplier(node)
        return ChunkCapability(
            kind=ChunkCapabilityKind.MAP_ONLY,
            preserves_row_order=True,
            expands_rows=True,
            row_multiplier=row_multiplier,
        )
    if node_type == NodeType.OPTIMISER_APPLY:
        return ChunkCapability(
            kind=ChunkCapabilityKind.MAP_ONLY,
            preserves_row_order=True,
        )
    if node_type == NodeType.MODEL_SCORE:
        lifetime = node.data.config.get("model_reuse_lifetime")
        if lifetime != "batch":
            raise ChunkPlanUnsupportedError(
                "Chunked modelScore requires model_reuse_lifetime='batch'.",
                node_id=node.id,
                node_type=node_type.value,
                model_reuse_lifetime=lifetime,
            )
        if (node.data.config.get("code") or "").strip():
            raise ChunkPlanUnsupportedError(
                "Chunked modelScore post-processing code is not supported in V1.",
                node_id=node.id,
                node_type=node_type.value,
            )
        return ChunkCapability(
            kind=ChunkCapabilityKind.MAP_ONLY,
            preserves_row_order=True,
            model_reuse_lifetime="batch",
        )
    if node_type == NodeType.RATING_STEP:
        if node.data.config.get("code"):
            raise ChunkPlanUnsupportedError(
                "Chunked ratingStep user code is not supported in V1.",
                node_id=node.id,
                node_type=node_type.value,
            )
        return ChunkCapability(
            kind=ChunkCapabilityKind.MAP_ONLY,
            preserves_row_order=True,
        )

    if len(parent_ids) > 1:
        raise ChunkPlanUnsupportedError(
            "Chunked execution does not yet support fan-in nodes.",
            node_id=node.id,
            node_type=node_type.value,
            parent_ids=parent_ids,
        )
    raise ChunkPlanUnsupportedError(
        "Node type is not chunk-safe in the V1 chunk contract.",
        node_id=node.id,
        node_type=node_type.value,
    )


def _row_local_call_is_supported(
    call: ast.Call,
    *,
    allowed_frames: set[str],
    local_frames: set[str],
) -> tuple[bool, bool]:
    func = call.func
    if isinstance(func, ast.Attribute):
        method_name = func.attr
        if method_name in _GLOBAL_METHOD_NAMES:
            return False, False
        if isinstance(func.value, ast.Name) and func.value.id == "pl":
            if method_name not in _ROW_LOCAL_POLARS_FUNCTIONS:
                return False, False
            args_supported = _row_local_args_are_supported(
                (*call.args, *(keyword.value for keyword in call.keywords)),
                allowed_frames=allowed_frames,
                local_frames=local_frames,
            )
            return args_supported, False
        receiver_supported, receiver_derived = _row_local_expr_is_supported(
            func.value,
            allowed_frames=allowed_frames,
            local_frames=local_frames,
        )
        if not receiver_supported:
            return False, False
        if receiver_derived:
            if method_name not in _ROW_LOCAL_DF_METHOD_NAMES:
                return False, False
        elif method_name not in _ROW_LOCAL_EXPR_METHOD_NAMES:
            return False, False
        args_supported = _row_local_args_are_supported(
            (*call.args, *(keyword.value for keyword in call.keywords)),
            allowed_frames=allowed_frames,
            local_frames=local_frames,
        )
        return args_supported, receiver_derived
    return False, False


def _row_local_expr_is_supported(
    node: ast.AST,
    *,
    allowed_frames: set[str],
    local_frames: set[str],
) -> tuple[bool, bool]:
    if isinstance(node, ast.Call):
        return _row_local_call_is_supported(
            node,
            allowed_frames=allowed_frames,
            local_frames=local_frames,
        )
    if isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Name) and node.value.id == "pl":
            return True, False
        return False, False
    if isinstance(node, ast.Name | ast.Constant):
        if isinstance(node, ast.Name):
            is_frame = node.id in allowed_frames or node.id in local_frames
            return is_frame, is_frame
        return True, False
    if isinstance(node, ast.BinOp):
        left_supported, _left_derived = _row_local_expr_is_supported(
            node.left,
            allowed_frames=allowed_frames,
            local_frames=local_frames,
        )
        right_supported, _right_derived = _row_local_expr_is_supported(
            node.right,
            allowed_frames=allowed_frames,
            local_frames=local_frames,
        )
        return left_supported and right_supported, False
    if isinstance(node, ast.UnaryOp):
        supported, _derived = _row_local_expr_is_supported(
            node.operand,
            allowed_frames=allowed_frames,
            local_frames=local_frames,
        )
        return supported, False
    if isinstance(node, ast.BoolOp):
        return all(
            _row_local_expr_is_supported(
                value,
                allowed_frames=allowed_frames,
                local_frames=local_frames,
            )[0]
            for value in node.values
        ), False
    if isinstance(node, ast.Compare):
        left_supported, _left_derived = _row_local_expr_is_supported(
            node.left,
            allowed_frames=allowed_frames,
            local_frames=local_frames,
        )
        comparators_supported = all(
            _row_local_expr_is_supported(
                value,
                allowed_frames=allowed_frames,
                local_frames=local_frames,
            )[0]
            for value in node.comparators
        )
        return left_supported and comparators_supported, False
    if isinstance(node, ast.IfExp):
        return all(
            _row_local_expr_is_supported(
                value,
                allowed_frames=allowed_frames,
                local_frames=local_frames,
            )[0]
            for value in (node.test, node.body, node.orelse)
        ), False
    if isinstance(node, ast.List | ast.Tuple | ast.Set):
        return all(
            _row_local_expr_is_supported(
                value,
                allowed_frames=allowed_frames,
                local_frames=local_frames,
            )[0]
            for value in node.elts
        ), False
    if isinstance(node, ast.Dict):
        return all(
            (
                key is None
                or _row_local_expr_is_supported(
                    key,
                    allowed_frames=allowed_frames,
                    local_frames=local_frames,
                )[0]
            )
            and _row_local_expr_is_supported(
                value,
                allowed_frames=allowed_frames,
                local_frames=local_frames,
            )[0]
            for key, value in zip(node.keys, node.values, strict=True)
        ), False
    if isinstance(node, ast.Subscript):
        value_supported, _value_derived = _row_local_expr_is_supported(
            node.value,
            allowed_frames=allowed_frames,
            local_frames=local_frames,
        )
        slice_supported, _slice_derived = _row_local_expr_is_supported(
            node.slice,
            allowed_frames=allowed_frames,
            local_frames=local_frames,
        )
        return value_supported and slice_supported, False
    if isinstance(node, ast.Slice):
        return all(
            value is None
            or _row_local_expr_is_supported(
                value,
                allowed_frames=allowed_frames,
                local_frames=local_frames,
            )[0]
            for value in (node.lower, node.upper, node.step)
        ), False
    return False, False


def _row_local_stmt_is_supported(
    stmt: ast.stmt,
    *,
    allowed_frames: set[str],
    local_frames: set[str],
) -> bool:
    if isinstance(stmt, ast.Assign):
        if not all(isinstance(target, ast.Name) for target in stmt.targets):
            return False
        supported, derived_from_frame = _row_local_expr_is_supported(
            stmt.value,
            allowed_frames=allowed_frames,
            local_frames=local_frames,
        )
        if supported and derived_from_frame:
            local_frames.update(
                target.id for target in stmt.targets if isinstance(target, ast.Name)
            )
        return supported and derived_from_frame
    if isinstance(stmt, ast.AnnAssign):
        if not isinstance(stmt.target, ast.Name) or stmt.value is None:
            return False
        supported, derived_from_frame = _row_local_expr_is_supported(
            stmt.value,
            allowed_frames=allowed_frames,
            local_frames=local_frames,
        )
        if supported and derived_from_frame:
            local_frames.add(stmt.target.id)
        return supported and derived_from_frame
    if isinstance(stmt, ast.Expr):
        supported, derived_from_frame = _row_local_expr_is_supported(
            stmt.value,
            allowed_frames=allowed_frames,
            local_frames=local_frames,
        )
        return supported and derived_from_frame
    return False


def _validate_chunkable_source(node: GraphNode) -> None:
    config = node.data.config
    source_type = config.get("sourceType", "flat_file")
    if source_type != "flat_file":
        raise ChunkPlanUnsupportedError(
            "Chunked dataSource supports flat_file sources only.",
            node_id=node.id,
            node_type=node.data.nodeType.value,
            sourceType=source_type,
        )
    if (config.get("code") or "").strip():
        raise ChunkPlanUnsupportedError(
            "Chunked dataSource code is not supported; sources must be direct scans.",
            node_id=node.id,
            node_type=node.data.nodeType.value,
        )
    raw_path = node.data.config.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        raise ChunkPlanUnsupportedError(
            "Chunked dataSource requires a file path.",
            node_id=node.id,
            node_type=node.data.nodeType.value,
        )
    suffix = Path(raw_path).suffix.lower()
    if suffix not in {".parquet", ".csv"}:
        raise ChunkPlanUnsupportedError(
            "Chunked dataSource supports parquet or csv sources only.",
            node_id=node.id,
            node_type=node.data.nodeType.value,
            path=raw_path,
        )


def _scenario_row_multiplier(node: GraphNode) -> int:
    raw_steps = node.data.config.get("steps")
    steps = int(raw_steps) if raw_steps is not None else _DEFAULT_SCENARIO_STEPS
    if steps < 1:
        raise ChunkPlanUnsupportedError(
            "Chunked scenarioExpander requires steps >= 1.",
            node_id=node.id,
            node_type=node.data.nodeType.value,
            steps=steps,
        )
    return steps


def _assert_plan_matches_prepared_graph(plan: ChunkPlan, order: list[str]) -> None:
    if tuple(order) != plan.node_ids:
        raise ChunkPlanUnsupportedError(
            "Chunk plan does not match the prepared graph order.",
            target_node_id=plan.target_node_id,
            planned_node_ids=list(plan.node_ids),
            prepared_node_ids=list(order),
        )


def _validate_chunk_suffix_is_single_parent(
    chunk_node_ids: tuple[str, ...],
    *,
    chunk_start_node_id: str,
    parents_of: Mapping[str, list[str]],
) -> None:
    chunk_node_set = set(chunk_node_ids)
    for node_id in chunk_node_ids:
        if node_id == chunk_start_node_id:
            continue
        parent_ids = parents_of.get(node_id, [])
        if len(parent_ids) != 1 or parent_ids[0] not in chunk_node_set:
            raise ChunkPlanUnsupportedError(
                "Chunked execution V1 requires a single-parent chunk suffix.",
                node_id=node_id,
                parent_ids=parent_ids,
                chunk_start_node_id=chunk_start_node_id,
            )


def _assert_runner_shape(
    plan: ChunkPlan,
    node_map: Mapping[str, GraphNode],
    parents_of: Mapping[str, list[str]],
) -> None:
    source_count = 0
    chunk_node_set = set(plan.chunk_node_ids)
    for node_id in plan.chunk_node_ids:
        node = node_map[node_id]
        if node.data.nodeType == NodeType.DATA_SOURCE:
            source_count += 1
            if node_id != plan.chunk_start_node_id:
                raise ChunkPlanUnsupportedError(
                    "Chunk runner encountered a non-root dataSource node.",
                    node_id=node_id,
                    chunk_start_node_id=plan.chunk_start_node_id,
                )
            continue
        if node_id == plan.chunk_start_node_id:
            continue
        parent_ids = [
            parent_id for parent_id in parents_of.get(node_id, []) if parent_id in chunk_node_set
        ]
        if len(parent_ids) != 1:
            raise ChunkPlanUnsupportedError(
                "Chunk runner V1 executes single-parent chains only.",
                node_id=node_id,
                parent_ids=parents_of.get(node_id, []),
            )
    if plan.chunk_start_node_id == plan.source_node_id and source_count != 1:
        raise ChunkPlanUnsupportedError(
            "Chunk runner requires exactly one dataSource root.",
            source_node_id=plan.source_node_id,
            source_count=source_count,
        )


def _source_lazy_frame(node: GraphNode) -> pl.LazyFrame:
    raw_path = node.data.config.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        raise ChunkPlanUnsupportedError(
            "Chunked dataSource requires a file path.",
            node_id=node.id,
            node_type=node.data.nodeType.value,
        )
    suffix = Path(raw_path).suffix.lower()
    if suffix in {".parquet", ".csv"}:
        return read_data_source(
            node.data.config,
            profile=ExecutionProfile.CHUNKED_MAP_REDUCE,
        )
    raise ChunkPlanUnsupportedError(
        "Chunked dataSource supports parquet or csv sources only.",
        node_id=node.id,
        node_type=node.data.nodeType.value,
        path=raw_path,
    )


def _normalise_lazy_frame(frame: pl.LazyFrame | pl.DataFrame | Any) -> pl.LazyFrame:
    if isinstance(frame, pl.LazyFrame):
        return frame
    if isinstance(frame, pl.DataFrame):
        return frame.lazy()
    raise TypeError(f"Chunk node returned {type(frame).__name__}; expected a Polars frame.")


def _project_frame(
    frame: pl.LazyFrame,
    columns: frozenset[str] | None,
    *,
    node: GraphNode,
) -> pl.LazyFrame:
    if columns is None:
        return frame
    schema_cols = frame.collect_schema().names()
    schema_set = set(schema_cols)
    missing = set(columns) - schema_set
    if missing:
        raise ContractMismatchError(
            "Chunk projection references columns missing from the node output schema.",
            node_id=node.id,
            node_type=node.data.nodeType.value,
            missing=sorted(missing),
            required_columns=sorted(columns),
            output_columns=sorted(schema_set),
        )
    ordered = [column for column in schema_cols if column in columns]
    return frame.select(ordered)


def _write_chunk_checkpoint(
    frame: pl.DataFrame,
    *,
    checkpoint_dir: Path | None,
    target_node_id: str,
    chunk_index: int,
) -> Path | None:
    if checkpoint_dir is None:
        return None
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    path = checkpoint_dir / f"{target_node_id}_chunk_{chunk_index:08d}.parquet"
    from haute._polars_utils import atomic_write

    try:
        with atomic_write(path) as tmp:
            frame.write_parquet(tmp, compression="lz4")
    except BaseException:
        path.unlink(missing_ok=True)
        path.with_suffix(".parquet.tmp").unlink(missing_ok=True)
        try:
            checkpoint_dir.rmdir()
        except OSError as exc:
            logger.warning(
                "chunk_checkpoint_cleanup_failed",
                path=str(checkpoint_dir),
                error=str(exc),
            )
        raise
    return path


def _cleanup_written_checkpoints(paths: list[Path]) -> None:
    seen: set[Path] = set()
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning(
                "chunk_checkpoint_cleanup_failed",
                path=str(path),
                error=str(exc),
            )
    for parent in sorted({path.parent for path in paths}, key=lambda p: len(p.parts), reverse=True):
        try:
            parent.rmdir()
        except OSError as exc:
            logger.warning(
                "chunk_checkpoint_cleanup_failed",
                path=str(parent),
                error=str(exc),
            )
