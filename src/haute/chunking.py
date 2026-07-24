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
from haute._code_extraction import _strip_generated_boilerplate_from_code
from haute._execution_context import ExecutionProfile
from haute._input_providers import resolve_data_input
from haute._logging import get_logger
from haute._polars_io_registry import (
    PolarsIoConfigError,
    format_for_config,
    resolve_input_mode,
    validate_data_input_config,
)
from haute._polars_utils import DEFAULT_STREAMING_CHUNK_SIZE, streaming_collect
from haute._types import GraphEdge, GraphNode, NodeType, PipelineGraph
from haute.errors import ChunkPlanUnsupportedError, ContractMismatchError
from haute.execution import plan_prepared_execution_strategy
from haute.projection import _children_of, prepare_graph

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


_PROVIDER_INPUT_BATCH_RULE_NAME = "provider_input_batches"
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
        NodeType.POLARS: _chunk_declaration(
            NodeType.POLARS,
            ChunkCapabilityStatus.CONDITIONAL,
            _SINGLE_PARENT_SUFFIX_RULE_NAME,
            _ROW_LOCAL_POLARS_RULE_NAME,
        ),
        NodeType.EDGE_JOIN: _chunk_declaration(
            NodeType.EDGE_JOIN,
            ChunkCapabilityStatus.UNSUPPORTED,
            _UNSUPPORTED_V1_RULE_NAME,
            note=(
                "edge joins need an explicit two-input chunk/reduce contract before chunk execution"
            ),
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
        NodeType.EXPLORE: _chunk_declaration(
            NodeType.EXPLORE,
            ChunkCapabilityStatus.UNSUPPORTED,
            _UNSUPPORTED_V1_RULE_NAME,
            note="exploratory analysis needs explicit bounded reducers before chunk execution",
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
        NodeType.DATA_INPUT: _chunk_declaration(
            NodeType.DATA_INPUT,
            ChunkCapabilityStatus.CONDITIONAL,
            _PROVIDER_INPUT_BATCH_RULE_NAME,
            _ROW_LOCAL_POLARS_RULE_NAME,
        ),
        NodeType.DATA_OUTPUT: _chunk_declaration(
            NodeType.DATA_OUTPUT,
            ChunkCapabilityStatus.UNSUPPORTED,
            _UNSUPPORTED_V1_RULE_NAME,
            note=(
                "registry-driven output writes are whole-plan sinks; no chunk-local "
                "write semantics declared"
            ),
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
    registry = _CHUNK_CAPABILITY_DECLARATIONS if declarations is None else declarations
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
# ---------------------------------------------------------------------------
# Chunk-local user-code whitelist (PATH_TO_HIGHEST_STANDARD §A3).
#
# Every admitted construct below cites the chunked==full proof that keeps it
# admitted: a hypothesis property test in tests/test_chunk_whitelist_proofs.py
# (``test_whitelisted_construct_chunked_equals_full[<proof id>]``) that runs
# the construct through the REAL chunk runner against full lazy execution on
# randomized, boundary-heavy frames.  A construct without a passing proof is
# not whitelisted; rejected code fails chunk planning loudly and callers route
# to the existing full (non-chunked) executor, which is always correct.
# ---------------------------------------------------------------------------
_ROW_LOCAL_DF_METHOD_NAMES = frozenset(
    {
        "cast",  # proof: df_cast
        "drop",  # proof: df_drop
        "drop_nulls",  # proof: df_drop_nulls
        "filter",  # proof: df_filter
        "fill_nan",  # proof: df_fill_nan (value-only signature in polars)
        "fill_null",  # proof: df_fill_null_value (value form only; see shape validator)
        "rename",  # proof: df_rename
        "select",  # proof: df_select
        "with_columns",  # proof: df_with_columns
        "with_columns_seq",  # proof: df_with_columns_seq
    }
)
_ROW_LOCAL_EXPR_METHOD_NAMES = frozenset(
    {
        "abs",  # proof: expr_abs
        "alias",  # proof: expr_alias
        "cast",  # proof: expr_cast
        "ceil",  # proof: expr_ceil
        "clip",  # proof: expr_clip
        "exp",  # proof: expr_exp
        "fill_nan",  # proof: expr_fill_nan (value-only signature in polars)
        "fill_null",  # proof: expr_fill_null_value (value form only; see shape validator)
        "floor",  # proof: expr_floor
        "is_between",  # proof: expr_is_between
        "is_finite",  # proof: expr_is_finite
        "is_in",  # proof: expr_is_in_literal (literal collections only; see shape validator)
        "is_infinite",  # proof: expr_is_infinite
        "is_nan",  # proof: expr_is_nan
        "is_not_nan",  # proof: expr_is_not_nan
        "is_not_null",  # proof: expr_is_not_null
        "is_null",  # proof: expr_is_null
        "log",  # proof: expr_log
        "not_",  # proof: expr_not
        "otherwise",  # proof: expr_when_then_otherwise
        "round",  # proof: expr_round
        "sqrt",  # proof: expr_sqrt
        "then",  # proof: expr_when_then_otherwise
    }
)
_ROW_LOCAL_POLARS_FUNCTIONS = frozenset(
    {
        "all_horizontal",  # proof: fn_all_horizontal
        "any_horizontal",  # proof: fn_any_horizontal
        "coalesce",  # proof: fn_coalesce
        "col",  # proof: fn_col
        "concat_str",  # proof: fn_concat_str
        "lit",  # proof: fn_lit
        "max_horizontal",  # proof: fn_max_horizontal
        "when",  # proof: expr_when_then_otherwise
    }
)


def _is_literal_scalar(node: ast.expr) -> bool:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.UAdd | ast.USub):
        return isinstance(node.operand, ast.Constant)
    return isinstance(node, ast.Constant)


def _fill_null_call_is_chunk_local(call: ast.Call) -> bool:
    """Admit only the literal-value form: ``fill_null(<value>)`` / ``fill_null(value=...)``.

    Strategy fills (``forward``/``backward``/``min``/``max``/``mean``/...) read
    across rows, so a chunk boundary changes the result; ``limit`` and
    positional strategies ride along with them.  De-whitelist pin:
    ``test_fill_null_strategy_is_de_whitelisted_and_full_path_is_correct``.
    """
    if call.keywords:
        return not call.args and len(call.keywords) == 1 and call.keywords[0].arg == "value"
    return len(call.args) == 1


_CATEGORICAL_CAST_DTYPE_NAMES = frozenset({"Categorical", "Enum"})


def _cast_call_is_chunk_local(call: ast.Call) -> bool:
    """Reject ``cast`` to ``pl.Categorical``/``pl.Enum`` (including nested).

    A Categorical/Enum physical encoding depends on the ambient global string
    cache: a value whose first appearance lands in a later chunk can be assigned
    a different physical code than under full execution, so a downstream sort or
    join on the column silently diverges.  Rejecting the cast fails chunk
    planning loudly and routes to the always-correct full executor.
    De-whitelist pin: ``test_chunk_unsafe_constructs_are_not_whitelisted``
    (``cast-to-categorical*`` cases).
    """
    for argument in (*call.args, *(keyword.value for keyword in call.keywords)):
        for sub in ast.walk(argument):
            if isinstance(sub, ast.Attribute) and sub.attr in _CATEGORICAL_CAST_DTYPE_NAMES:
                return False
    return True


def _is_in_call_is_chunk_local(call: ast.Call) -> bool:
    """Admit only membership against a literal collection: ``is_in([<literals>])``.

    ``is_in(pl.col(...))`` / ``is_in(frame[...])`` use the FULL column as the
    haystack; inside a chunk the haystack silently shrinks to the chunk's rows.
    De-whitelist pin:
    ``test_is_in_column_haystack_is_de_whitelisted_and_full_path_is_correct``.
    """
    if call.keywords or len(call.args) != 1:
        return False
    collection = call.args[0]
    if not isinstance(collection, ast.List | ast.Tuple | ast.Set):
        return False
    return all(_is_literal_scalar(element) for element in collection.elts)


# Methods whose bare name is not enough to prove chunk-locality: the call
# SHAPE must also be constrained before the generic argument walk runs.
_CHUNK_LOCAL_CALL_SHAPE_VALIDATORS: Mapping[str, Callable[[ast.Call], bool]] = MappingProxyType(
    {
        "cast": _cast_call_is_chunk_local,
        "fill_null": _fill_null_call_is_chunk_local,
        "is_in": _is_in_call_is_chunk_local,
    }
)


def _row_local_subexprs_are_supported(
    values: Iterable[ast.expr | None],
    *,
    allowed_frames: set[str],
    local_frames: set[str],
) -> bool:
    """Return whether every sub-expression is row-local and frame-free.

    Frame references are only chunk-safe as method-chain receivers.  Embedded
    anywhere else (call argument, subscript, operand, collection element) they
    read the FULL frame under full execution but only the chunk under chunked
    execution, so they are rejected here.
    """
    for value in values:
        if value is None:
            continue
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
    chunk_start_node_id: str,
    row_expansion_factor: int,
    projection: Any,
) -> tuple[int, int, int | None, int | None]:
    expansion = max(1, row_expansion_factor)
    if request.chunk_size is not None:
        chunk_size = request.chunk_size
        return chunk_size, max(1, chunk_size // expansion), None, None

    assert request.target_chunk_bytes is not None
    target_row_bytes = _estimate_target_row_bytes(request, projection)
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


def _estimate_target_row_bytes(request: ChunkPlanRequest, projection: Any) -> int:
    """Cost one target row from the TARGET node's projected OUTPUT schema.

    Sizing the byte budget off the *source* schema silently undercounts any
    column the chunk suffix creates downstream (e.g. a wide ``String`` produced
    by a polars node): such columns are absent from the source schema and fall
    back to ~64 bytes, so the byte budget picks a chunk size many times larger
    than the real per-chunk footprint and the runner can OOM.

    The target frame is built lazily through the production engine (no eager
    materialisation of the full input); fixed-width dtypes are costed exactly and
    variable-width columns (``String``/``Binary``/nested/…) are sampled.  Any
    failure to derive the schema fails loudly rather than guessing a width.
    """
    projected_columns = projection.needed_by_node.get(request.target_node_id)
    if projected_columns is None:
        raise ChunkPlanUnsupportedError(
            "Byte-budgeted chunk planning requires concrete projected columns.",
            target_node_id=request.target_node_id,
        )
    if not projected_columns:
        return 1

    target_lf = _target_output_lazyframe(request)
    schema = target_lf.collect_schema()
    schema_by_name = dict(schema.items())
    missing = set(projected_columns) - set(schema_by_name)
    if missing:
        raise ChunkPlanUnsupportedError(
            "Byte-budgeted chunk planning target schema is missing projected columns.",
            target_node_id=request.target_node_id,
            missing=sorted(missing),
            output_columns=sorted(schema_by_name),
        )

    widths: dict[str, int] = {}
    variable_columns: list[str] = []
    for column in projected_columns:
        fixed_width = _FIXED_DTYPE_BYTES.get(schema_by_name[column].base_type())
        if fixed_width is None:
            variable_columns.append(column)
        else:
            widths[column] = fixed_width
    if variable_columns:
        widths.update(
            _sample_variable_column_widths(
                target_lf,
                schema,
                variable_columns,
                target_node_id=request.target_node_id,
            )
        )
    return max(1, sum(widths[column] for column in projected_columns))


def _target_output_lazyframe(request: ChunkPlanRequest) -> pl.LazyFrame:
    """Build the projected target-node output frame through the shared engine."""
    from haute.execution import execute_lazy_graph
    from haute.executor import _build_node_fn

    try:
        frames, _order, _parents_of, _id_to_name = execute_lazy_graph(
            request.graph,
            _build_node_fn,
            target_node_id=request.target_node_id,
            source=request.source,
            required_columns_by_node=request.required_columns_by_node,
        )
    except Exception as exc:
        # ``execute_lazy_graph`` is the full production engine, so a failure here
        # is ambiguous: it can mean the graph shape is not byte-budgetable OR
        # that a genuine engine defect was hit during planning.  Reclassifying to
        # ChunkPlanUnsupportedError routes callers to the full (non-chunked)
        # executor, which silently disables the byte-budget OOM guard -- so log
        # the swallowed exception at WARNING with the target node id to keep a
        # real defect distinguishable from an unsupported graph shape rather than
        # hiding it behind the reclassification.
        logger.warning(
            "chunk_plan_target_output_build_failed",
            target_node_id=request.target_node_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        raise ChunkPlanUnsupportedError(
            "Byte-budgeted chunk planning could not build the target output frame.",
            target_node_id=request.target_node_id,
        ) from exc
    frame = frames.get(request.target_node_id)
    if frame is None:
        raise ChunkPlanUnsupportedError(
            "Byte-budgeted chunk planning target output frame is unavailable.",
            target_node_id=request.target_node_id,
        )
    return _normalise_lazy_frame(frame)


def _sample_variable_column_widths(
    target_lf: pl.LazyFrame,
    schema: pl.Schema,
    variable_columns: list[str],
    *,
    target_node_id: str,
) -> dict[str, int]:
    """Measure per-row byte width for variable-width columns from a bounded sample."""
    variable_set = set(variable_columns)
    ordered = [column for column in schema.names() if column in variable_set]
    try:
        sample = streaming_collect(
            target_lf.select(ordered).limit(_ROW_BYTE_SAMPLE_SIZE),
            profile=ExecutionProfile.CHUNKED_MAP_REDUCE,
        )
    except Exception as exc:
        raise ChunkPlanUnsupportedError(
            "Byte-budgeted chunk planning could not sample variable-width target columns.",
            target_node_id=target_node_id,
            columns=sorted(variable_set),
        ) from exc
    widths: dict[str, int] = {}
    for column in variable_columns:
        if sample.height == 0:
            # No rows to measure: an empty input cannot OOM, so a nominal width
            # keeps chunk sizing finite without inventing a spurious large width.
            widths[column] = _DEFAULT_PROJECTED_COLUMN_BYTES
        else:
            widths[column] = max(1, math.ceil(sample[column].estimated_size() / sample.height))
    return widths


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
    if node.data.nodeType != NodeType.DATA_INPUT:
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
    # Strategy policy is authoritative and runs before capability-specific
    # chunk planning.  In particular, every group-by receives the stable
    # profile rejection and can never be mistaken for generic reducer support.
    projection = plan_prepared_execution_strategy(
        prepared.order,
        _children_of(prepared.order, prepared.parents_of),
        prepared.node_map,
        profile=ExecutionProfile.CHUNKED_MAP_REDUCE,
        required_columns_by_node=request.required_columns_by_node,
    )
    source_node_ids: list[str] = []
    for node_id in prepared.order:
        node = prepared.node_map[node_id]
        if node.data.nodeType == NodeType.DATA_INPUT:
            source_node_ids.append(node_id)

    if request.chunk_start_node_id is None and len(source_node_ids) != 1:
        raise ChunkPlanUnsupportedError(
            "Chunked execution currently requires exactly one Data Input root.",
            source_node_ids=source_node_ids,
            target_node_id=request.target_node_id,
        )
    source_node_id = source_node_ids[0] if len(source_node_ids) == 1 else None
    chunk_start_node_id = request.chunk_start_node_id or source_node_id
    if chunk_start_node_id is None:
        raise ChunkPlanUnsupportedError(
            "chunk_start_node_id is required when a target has multiple Data Input roots.",
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
    pre_chunk_node_id_set = set(pre_chunk_node_ids)
    chunk_node_ids = tuple(prepared.order[start_index:])
    capabilities: dict[str, ChunkCapability] = {}
    row_expansion_factor = 1
    for node_id in prepared.order:
        node = resolve_instance_node(prepared.node_map[node_id], prepared.node_map)
        parent_ids = prepared.parents_of.get(node_id, [])
        if node_id in pre_chunk_node_id_set or (
            node_id == chunk_start_node_id and chunk_start_node_id != source_node_id
        ):
            if node.data.nodeType == NodeType.DATA_INPUT:
                _validate_chunkable_input(node)
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

    chunk_size, source_chunk_size, estimated_source_row_bytes, estimated_target_row_bytes = (
        _plan_chunk_sizes(
            request,
            prepared=prepared,
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
        source_code = _data_input_code(source_node)
        if source_code:
            from haute._user_exec import _exec_user_code

            source_lf = _normalise_lazy_frame(
                _exec_user_code(
                    source_code,
                    ["df"],
                    (source_lf,),
                    extra_ns=request.preamble_ns,
                )
            )
        source_lf = _normalise_lazy_frame(
            _apply_selected_columns(source_lf, source_node.data.config)
        )
        source_lf = _normalise_lazy_frame(_apply_column_renames(source_lf, source_node.data.config))
    else:
        source_lf = _normalise_lazy_frame(request.start_frame)
    projection_ordering_cache: dict[str, list[str]] = {}
    source_lf = _project_frame(
        source_lf,
        plan.required_columns_by_node.get(plan.chunk_start_node_id),
        node=source_node,
        ordering_cache=projection_ordering_cache,
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
    incoming_edges_by_target: dict[str, list[GraphEdge]] = {}
    for edge in prepared.relevant_edges:
        incoming_edges_by_target.setdefault(edge.target, []).append(edge)
    all_incoming_edges_by_target: dict[str, list[GraphEdge]] = {}
    for edge in graph.edges:
        all_incoming_edges_by_target.setdefault(edge.target, []).append(edge)
    funcs = _build_funcs(
        list(plan.node_ids),
        node_map,
        parents_of,
        prepared.id_to_name,
        graph.parents_of,
        request.build_node_fn,
        incoming_edges_by_target=incoming_edges_by_target,
        all_incoming_edges_by_target=all_incoming_edges_by_target,
        all_node_map=graph.node_map,
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
        if checkpoint_dir is not None:
            checkpoint_dir.mkdir(parents=True, exist_ok=True)
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
                        ordering_cache=projection_ordering_cache,
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
            if context is not None:
                source_demand = plan.required_columns_by_node.get(plan.chunk_start_node_id)
                target_demand = plan.required_columns_by_node.get(plan.target_node_id)
                context.record_column_widths(
                    node_id=plan.chunk_start_node_id,
                    output_width=source_batch.width,
                    requested_width=None if source_demand is None else len(source_demand),
                    physically_scanned_width=source_batch.width,
                )
                context.record_column_widths(
                    node_id=plan.target_node_id,
                    output_width=frame.width,
                    requested_width=None if target_demand is None else len(target_demand),
                )
                context.record_chunk()
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
        raise
    finally:
        if not completed and request.cleanup_checkpoints_on_error:
            _cleanup_written_checkpoints(written_checkpoints, checkpoint_dir=checkpoint_dir)
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

    if node_type == NodeType.DATA_INPUT:
        _validate_chunkable_input(node)
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
        shape_validator = _CHUNK_LOCAL_CALL_SHAPE_VALIDATORS.get(method_name)
        if shape_validator is not None and not shape_validator(call):
            return False, False
        if isinstance(func.value, ast.Name) and func.value.id == "pl":
            if method_name not in _ROW_LOCAL_POLARS_FUNCTIONS:
                return False, False
            args_supported = _row_local_subexprs_are_supported(
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
        args_supported = _row_local_subexprs_are_supported(
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
    # Composite expressions must be row-local AND frame-free.  A frame name is
    # only chunk-safe as a method-chain receiver; embedded in a subscript
    # (``frame["col"]``), operand, or collection it reads the full frame under
    # full execution but only the current chunk under chunked execution, which
    # silently diverges — so _row_local_subexprs_are_supported rejects it.
    if isinstance(node, ast.BinOp):
        children: tuple[ast.expr | None, ...] = (node.left, node.right)
    elif isinstance(node, ast.UnaryOp):
        children = (node.operand,)
    elif isinstance(node, ast.BoolOp):
        children = tuple(node.values)
    elif isinstance(node, ast.Compare):
        children = (node.left, *node.comparators)
    elif isinstance(node, ast.IfExp):
        children = (node.test, node.body, node.orelse)
    elif isinstance(node, ast.List | ast.Tuple | ast.Set):
        children = tuple(node.elts)
    elif isinstance(node, ast.Dict):
        children = (*node.keys, *node.values)
    elif isinstance(node, ast.Subscript):
        children = (node.value, node.slice)
    elif isinstance(node, ast.Slice):
        children = (node.lower, node.upper, node.step)
    else:
        return False, False
    return _row_local_subexprs_are_supported(
        children,
        allowed_frames=allowed_frames,
        local_frames=local_frames,
    ), False


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


def _data_input_code(node: GraphNode) -> str:
    return _strip_generated_boilerplate_from_code(
        node.data.config.get("code") or "",
        kind="data_input",
    )


def _validate_chunkable_input(node: GraphNode) -> None:
    """Prove that a canonical Data Input can feed bounded record batches."""
    try:
        config = validate_data_input_config(node.data.config)
        code = _data_input_code(node)
        if code and not is_chunk_local_polars_code(code, frame_names=("df",)):
            raise ChunkPlanUnsupportedError(
                "Chunked Data Input editor code must be row-local.",
                node_id=node.id,
                node_type=node.data.nodeType.value,
            )

        # Every published snapshot has the same immutable Parquet execution
        # shape, regardless of the provider or original source format.
        if config["cacheMode"] == "snapshot":
            return

        fmt = format_for_config(config)
        mode = resolve_input_mode(fmt, config)
        if fmt.source_kind == "inline":
            return
        if mode != "scan" or not fmt.bounded_read:
            raise ChunkPlanUnsupportedError(
                "Direct chunked Data Input requires a bounded lazy scan; "
                "build a snapshot for eager-only formats.",
                node_id=node.id,
                node_type=node.data.nodeType.value,
                format=fmt.name,
                mode=mode,
            )
        arguments = config.get("arguments") or {}
        if fmt.needs_schema_when_bounded and "schema" not in arguments:
            raise ChunkPlanUnsupportedError(
                "Direct chunked Data Input requires a full declared schema for this format.",
                node_id=node.id,
                node_type=node.data.nodeType.value,
                format=fmt.name,
            )
    except ChunkPlanUnsupportedError:
        raise
    except (PolarsIoConfigError, TypeError, ValueError) as exc:
        raise ChunkPlanUnsupportedError(
            "Data Input configuration is not valid for bounded chunk execution.",
            node_id=node.id,
            node_type=node.data.nodeType.value,
        ) from exc


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
        if node.data.nodeType == NodeType.DATA_INPUT:
            source_count += 1
            if node_id != plan.chunk_start_node_id:
                raise ChunkPlanUnsupportedError(
                    "Chunk runner encountered a non-root Data Input node.",
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
            "Chunk runner requires exactly one Data Input root.",
            source_node_id=plan.source_node_id,
            source_count=source_count,
        )


def _source_lazy_frame(node: GraphNode) -> pl.LazyFrame:
    _validate_chunkable_input(node)
    try:
        return resolve_data_input(
            node.data.config,
            base_dir=Path.cwd(),
            profile=ExecutionProfile.CHUNKED_MAP_REDUCE,
        )
    except (PolarsIoConfigError, TypeError, ValueError) as exc:
        raise ChunkPlanUnsupportedError(
            "Data Input could not be resolved as a bounded chunk source.",
            node_id=node.id,
            node_type=node.data.nodeType.value,
        ) from exc


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
    ordering_cache: dict[str, list[str]] | None = None,
) -> pl.LazyFrame:
    if columns is None:
        return frame
    # A node's output schema is chunk-invariant (identical transforms per chunk),
    # so the ordered projection and its missing-column contract check are resolved
    # once and reused for every later chunk instead of re-running ``collect_schema``
    # O(nodes x chunks) times.
    cached = None if ordering_cache is None else ordering_cache.get(node.id)
    if cached is None:
        schema_cols = frame.collect_schema().names()
        missing = set(columns) - set(schema_cols)
        if missing:
            raise ContractMismatchError(
                "Chunk projection references columns missing from the node output schema.",
                node_id=node.id,
                node_type=node.data.nodeType.value,
                missing=sorted(missing),
                required_columns=sorted(columns),
                output_columns=sorted(schema_cols),
            )
        cached = [column for column in schema_cols if column in columns]
        if ordering_cache is not None:
            ordering_cache[node.id] = cached
    return frame.select(cached)


def _write_chunk_checkpoint(
    frame: pl.DataFrame,
    *,
    checkpoint_dir: Path | None,
    target_node_id: str,
    chunk_index: int,
) -> Path | None:
    if checkpoint_dir is None:
        return None
    path = checkpoint_dir / f"{target_node_id}_chunk_{chunk_index:08d}.parquet"
    from haute._polars_utils import atomic_write

    with atomic_write(path, ensure_parent=False) as tmp:
        frame.write_parquet(tmp, compression="lz4")
    return path


def _cleanup_written_checkpoints(paths: list[Path], *, checkpoint_dir: Path | None = None) -> None:
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
    directories = {path.parent for path in paths}
    if checkpoint_dir is not None:
        directories.add(checkpoint_dir)
    for parent in sorted(directories, key=lambda p: len(p.parts), reverse=True):
        if not parent.exists():
            continue
        try:
            parent.rmdir()
        except OSError as exc:
            logger.warning(
                "chunk_checkpoint_cleanup_failed",
                path=str(parent),
                error=str(exc),
            )
