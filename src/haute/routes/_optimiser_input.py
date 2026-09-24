"""Solve-input planning for the optimiser: projection, input resolution, validation, sizing.

What setup reads and from where (the column demand each node must produce,
the retained side inputs, the exact data-input edge), the value-contract
checks and their details, and how the solver input is sized and admitted as
a resident grid. Nothing here knows about jobs, solver results or artifact
publication; the solve service orchestrates these steps.
"""

from __future__ import annotations

import contextlib
import math
from collections.abc import Iterable, Iterator, Mapping
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException

from haute._execution_admission import (
    ExecutionAdmissionError,
    execution_budget_for_profile,
)
from haute._execution_context import (
    ExecutionCancelledError,
    ExecutionContext,
    ExecutionMemoryLimitExceededError,
    ExecutionProfile,
)
from haute._graph_utils import (
    incoming_edge_bindings,
    incoming_edge_for_name,
    select_edge_source_output,
)
from haute._logging import get_logger
from haute._polars_utils import (
    bounded_sink,
    read_parquet_metadata,
    streaming_collect,
)
from haute._types import (
    GraphEdge,
    GraphNode,
    PipelineGraph,
)
from haute.errors import BoundedMemoryUnsupportedError
from haute.execution import (
    ProjectionRequest,
    plan_projection,
    ratebook_factor_required_columns,
)
from haute.graph_utils import NodeType
from haute.routes import _optimiser_artifacts
from haute.routes._contract_errors import (
    PUBLIC_CONTRACT_ERROR_TYPES,
    contract_error_http_exception,
    contract_error_job_fields,
    contract_error_terminal_reason,
    memory_limit_http_exception,
)
from haute.routes._helpers import find_typed_node
from haute.routes._job_store import TerminalReason

if TYPE_CHECKING:
    from price_contour import QuoteGrid

logger = get_logger(component="server")

_DEFAULT_OPTIMISER_SETUP_TARGET_CHUNK_MIN_BYTES = 16 * 1024 * 1024
_DEFAULT_OPTIMISER_SETUP_TARGET_CHUNK_MAX_BYTES = 512 * 1024 * 1024
_DEFAULT_OPTIMISER_SETUP_TARGET_CHUNK_BUDGET_DIVISOR = 16

_NULL_QUOTE_ID_DETAIL_PREFIX = "Null quote_id values found in optimiser input"
_NON_FINITE_DETAIL_PREFIX = "Non-finite values found in optimiser input"
_NULL_VALUE_DETAIL_PREFIX = "Null values found in optimiser input"
_QUOTE_ID_NULL_COUNT_ALIAS = "__haute_quote_id_null_count"
_NON_FINITE_COUNT_ALIAS_PREFIX = "__haute_non_finite_count_"
_NULL_COUNT_ALIAS_PREFIX = "__haute_null_count_"


def _missing_columns_detail(
    required_cols: Iterable[str],
    available_cols: Iterable[str],
) -> str | None:
    missing_cols = sorted(set(required_cols) - set(available_cols))
    if not missing_cols:
        return None
    return f"Missing columns in scored data: {missing_cols}. Available: {sorted(available_cols)}"


def _invalid_quote_id_dtype_detail(schema: Any, qid_col: str) -> str | None:
    import polars as pl

    qid_dtype = schema[qid_col]
    if qid_dtype == pl.String or qid_dtype == pl.Categorical or isinstance(qid_dtype, pl.Enum):
        return None
    return (
        f"{qid_col} must be Utf8 (String), Categorical, or Enum, got {qid_dtype}. "
        "Numeric, binary, and other dtypes are not supported as quote_id columns."
    )


def _quote_id_null_detail(null_count: int) -> str:
    return (
        f"{_NULL_QUOTE_ID_DETAIL_PREFIX} ({null_count} rows). "
        "Every row must have a non-null quote_id; check upstream filters and joins."
    )


def _non_finite_check_columns(schema: Any, column_names: Iterable[str]) -> list[str]:
    return [
        cname
        for cname in dict.fromkeys(column_names)
        if cname in schema and schema[cname].is_float()
    ]


def _null_check_columns(schema: Any, column_names: Iterable[str]) -> list[str]:
    return [cname for cname in dict.fromkeys(column_names) if cname in schema]


def _value_contract_validation_exprs(
    *,
    quote_id_col: str,
    validate_quote_id_nulls: bool,
    non_finite_check_cols: list[str],
    null_check_cols: list[str],
    cast_to_float32_cols: set[str],
) -> list[Any]:
    import polars as pl

    validation_exprs: list[Any] = []
    if validate_quote_id_nulls:
        validation_exprs.append(pl.col(quote_id_col).null_count().alias(_QUOTE_ID_NULL_COUNT_ALIAS))
    for index, cname in enumerate(non_finite_check_cols):
        checked = pl.col(cname).cast(pl.Float32) if cname in cast_to_float32_cols else pl.col(cname)
        validation_exprs.append(
            checked.is_nan().sum().alias(f"{_NON_FINITE_COUNT_ALIAS_PREFIX}nan_{index}")
        )
        validation_exprs.append(
            checked.is_infinite().sum().alias(f"{_NON_FINITE_COUNT_ALIAS_PREFIX}inf_{index}")
        )
    # Nulls are checked on the source dtype: is_nan()/is_infinite() return
    # null for null inputs, so sum() skips them and the finite check alone
    # cannot see a genuinely-null value.
    for index, cname in enumerate(null_check_cols):
        validation_exprs.append(
            pl.col(cname).null_count().alias(f"{_NULL_COUNT_ALIAS_PREFIX}{index}")
        )
    return validation_exprs


def _non_finite_detail_from_counts(
    validation_counts: Any,
    non_finite_check_cols: list[str],
) -> str | None:
    non_finite_summaries = []
    for index, cname in enumerate(non_finite_check_cols):
        nan_count = int(
            validation_counts.get_column(f"{_NON_FINITE_COUNT_ALIAS_PREFIX}nan_{index}").item()
        )
        inf_count = int(
            validation_counts.get_column(f"{_NON_FINITE_COUNT_ALIAS_PREFIX}inf_{index}").item()
        )
        kinds = [
            f"{count} {kind} row{'s' if count != 1 else ''}"
            for count, kind in ((nan_count, "NaN"), (inf_count, "infinite"))
            if count > 0
        ]
        if kinds:
            non_finite_summaries.append(f"'{cname}' ({', '.join(kinds)})")
    if not non_finite_summaries:
        return None
    return (
        f"{_NON_FINITE_DETAIL_PREFIX}: {', '.join(non_finite_summaries)}. "
        "The optimiser requires finite objective, constraint, and scenario values; "
        "check upstream joins and calculations for division by zero or overflow."
    )


def _null_value_detail_from_counts(
    validation_counts: Any,
    null_check_cols: list[str],
) -> str | None:
    null_summaries = []
    for index, cname in enumerate(null_check_cols):
        null_count = int(validation_counts.get_column(f"{_NULL_COUNT_ALIAS_PREFIX}{index}").item())
        if null_count > 0:
            null_summaries.append(
                f"'{cname}' ({null_count} null row{'s' if null_count != 1 else ''})"
            )
    if not null_summaries:
        return None
    return (
        f"{_NULL_VALUE_DETAIL_PREFIX}: {', '.join(null_summaries)}. "
        "The optimiser requires non-null objective, constraint, and scenario values; "
        "check upstream joins and filters for rows with missing values."
    )


@dataclass(frozen=True, slots=True)
class _ChunkSizeDecision:
    chunk_size: int
    provenance: dict[str, int | str | None]


def _projected_parquet_input_path(frame: Any) -> Path | None:
    """Borrow only an unchanged single-file scan under the caller's input lease."""
    import json

    import polars as pl

    from haute._chunked_writes import _SUPPORTED_IR_MAJOR

    if not isinstance(frame, pl.LazyFrame):
        return None
    try:
        traverser = frame._ldf.visit()
        if traverser.version()[0] != _SUPPORTED_IR_MAJOR:
            return None
        node = traverser.view_current_node()
        if type(node).__name__ != "Scan" or node.scan_type[0] != "parquet":
            return None
        options = node.file_options
        if (
            len(node.paths) != 1
            or node.predicate is not None
            or node.hive_parts is not None
            or options.n_rows is not None
            or options.row_index is not None
            or options.include_file_paths is not None
            or options.column_mapping is not None
            or options.deletion_files is not None
            or json.loads(node.scan_type[1]).get("schema") is not None
        ):
            return None
        path = Path(node.paths[0])
        if not path.is_file():
            return None
        physical_schema = pl.read_parquet_schema(path)
        if any(
            physical_schema.get(name) != dtype for name, dtype in frame.collect_schema().items()
        ):
            return None
        return path
    except (AttributeError, NotImplementedError):
        # A changed optimiser IR loses this optional reuse path. Actual data
        # or filesystem errors still propagate through the ordinary setup path.
        return None


def _positive_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer.")
    return int(value)


def _admit_resident_grid(
    path: Path,
    columns: list[str],
    quote_id: str,
    constraint_count: int,
    chunk_rows: int,
    execution_context: ExecutionContext | None,
) -> None:
    if execution_context is None or execution_context.remaining_memory_bytes() is None:
        return
    import polars as pl

    from haute._polars_utils import cancellable_streaming_collect
    from haute._ram_estimate import (
        decoded_frame_row_width_bytes,
        estimate_optimiser_grid_peak_bytes,
    )

    row_count = int(read_parquet_metadata(path)["row_count"])
    sample = cancellable_streaming_collect(
        pl.scan_parquet(path).select(list(dict.fromkeys(columns))).head(512),
        execution_context=execution_context,
    )
    peak_bytes = estimate_optimiser_grid_peak_bytes(
        row_count=row_count,
        constraint_count=constraint_count,
        quote_id_width_bytes=decoded_frame_row_width_bytes(
            sample.select(pl.col(quote_id).cast(pl.String))
        ),
        input_row_width_bytes=decoded_frame_row_width_bytes(sample),
        chunk_rows=chunk_rows,
    )
    del sample
    remaining = execution_context.remaining_memory_bytes()
    assert remaining is not None
    if peak_bytes > remaining:
        raise ExecutionAdmissionError(
            execution_context.operation,
            profile=execution_context.profile,
            memory_limit_bytes=execution_context.memory_limit_bytes or remaining,
            rss_at_admission_bytes=execution_context.memory_sampler(),
            rss_limit_bytes=execution_context.rss_limit_bytes,
            reason=f"resident optimiser grid needs an estimated {peak_bytes} bytes; "
            f"{remaining} bytes remain in the execution allowance",
        )


def _explicit_chunk_size_from_config(config: Mapping[str, Any]) -> int | None:
    if "chunk_size" not in config:
        return None
    return _positive_int(config["chunk_size"], field="chunk_size")


def _optimiser_setup_target_chunk_bytes() -> int:
    budget = execution_budget_for_profile(ExecutionProfile.OPTIMISER_SETUP)
    budget_scaled = max(
        1,
        budget.memory_limit_bytes // _DEFAULT_OPTIMISER_SETUP_TARGET_CHUNK_BUDGET_DIVISOR,
    )
    return min(
        _DEFAULT_OPTIMISER_SETUP_TARGET_CHUNK_MAX_BYTES,
        max(_DEFAULT_OPTIMISER_SETUP_TARGET_CHUNK_MIN_BYTES, budget_scaled),
    )


def _chunk_size_decision_for_parquet(
    config: Mapping[str, Any],
    parquet_path: Path,
    *,
    source: str,
) -> _ChunkSizeDecision:
    import polars as pl

    from haute._ram_estimate import decoded_frame_row_width_bytes

    explicit_chunk_size = _explicit_chunk_size_from_config(config)
    if explicit_chunk_size is not None:
        return _ChunkSizeDecision(
            chunk_size=explicit_chunk_size,
            provenance={
                "policy": "explicit_rows",
                "chunk_size": explicit_chunk_size,
                "target_chunk_bytes": None,
                "estimated_row_bytes": None,
                "row_count": None,
                "size_bytes": None,
                "uncompressed_size_bytes": None,
                "source": source,
            },
        )

    metadata = read_parquet_metadata(parquet_path)
    row_count = _positive_int(int(metadata["row_count"]), field="parquet row_count")
    row_bytes_basis = int(metadata.get("uncompressed_size_bytes") or metadata["size_bytes"])
    row_bytes_basis = _positive_int(row_bytes_basis, field="parquet byte size")
    target_chunk_bytes = _optimiser_setup_target_chunk_bytes()
    sample = streaming_collect(pl.scan_parquet(parquet_path).head(512))
    estimated_row_bytes = max(
        1,
        math.ceil(row_bytes_basis / row_count),
        math.ceil(decoded_frame_row_width_bytes(sample)),
    )
    chunk_size = max(1, target_chunk_bytes // estimated_row_bytes)
    return _ChunkSizeDecision(
        chunk_size=chunk_size,
        provenance={
            "policy": "byte_budget",
            "chunk_size": chunk_size,
            "target_chunk_bytes": target_chunk_bytes,
            "estimated_row_bytes": estimated_row_bytes,
            "row_count": int(metadata["row_count"]),
            "size_bytes": int(metadata["size_bytes"]),
            "uncompressed_size_bytes": int(metadata.get("uncompressed_size_bytes") or 0),
            "source": source,
        },
    )


def _optimiser_side_input_ids(graph: PipelineGraph, node_id: str) -> frozenset[str]:
    """Return optimiser parent ids that are consumed after graph execution.

    The optimiser node may pass its input frame through, but solve/estimate
    setup resolves the configured ``data_input`` from the output map after the
    lazy executor has finished.  Treat that configured node as a retained
    setup input, just like ratebook's factor source, so checkpoint cleanup does
    not discard an intermediate parent once the optimiser node has consumed it.
    """
    node = _find_optimiser_node(graph, node_id)
    config = node.data.config
    preserved: set[str] = set()
    data_edge = _resolve_optimiser_input_edge(
        graph,
        node_id,
        config,
        field="data_input",
        infer_single=True,
    )
    if data_edge is not None:
        preserved.add(data_edge.source)
    if config.get("mode", "online") != "ratebook":
        return frozenset(preserved)
    banding_edge = _resolve_optimiser_input_edge(
        graph,
        node_id,
        config,
        field="banding_source",
    )
    if banding_edge is not None:
        preserved.add(banding_edge.source)
    return frozenset(preserved)


def _setup_execution_target_node_id(graph: PipelineGraph, node_id: str) -> str:
    """The node setup executes to: an online Optimiser's configured data input, else itself."""
    optimiser_node = _find_optimiser_node(graph, node_id)
    configured_data_input = optimiser_node.data.config.get("data_input")
    if (
        optimiser_node.data.config.get("mode", "online") == "online"
        and isinstance(configured_data_input, str)
        and configured_data_input
    ):
        data_input_id = _resolve_optimiser_data_input_id(
            graph,
            node_id,
            optimiser_node.data.config,
        )
        if isinstance(data_input_id, str) and data_input_id:
            return data_input_id
    return node_id


def _solve_columns_by_node(
    graph: PipelineGraph,
    node_id: str,
    config: dict[str, Any],
    *,
    source: str,
) -> dict[str, frozenset[str]]:
    """The columns the solve's setup reads at every node it executes.

    A node the solve reads whole (no concrete demand) is left out: a capture
    cannot promise it, so the solve recomputes that node as before.
    """
    projection = plan_projection(
        ProjectionRequest(
            graph=graph,
            target_node_id=_setup_execution_target_node_id(graph, node_id),
            profile=ExecutionProfile.OPTIMISER_SETUP,
            required_columns_by_node=_optimiser_solve_required_columns_by_node(
                graph, node_id, config
            ),
            source=source,
        )
    )
    return {
        needed_node_id: frozenset(columns)
        for needed_node_id, columns in projection.needed_by_node.items()
        if columns is not None
    }


def _optimiser_input_required_columns(config: dict[str, Any]) -> frozenset[str]:
    """Return the columns needed to validate and consume optimiser input."""
    objective = str(config["objective"])
    qid_col = str(config.get("quote_id", "quote_id"))
    mult_col = str(config.get("scenario_value", "scenario_value"))
    step_col = str(config.get("scenario_index", "scenario_index"))
    constraints = config.get("constraints") or {}
    constraint_cols = [str(cname) for cname in constraints]
    return frozenset({qid_col, step_col, mult_col, objective, *constraint_cols})


def _optimiser_solve_required_columns_by_node(
    graph: PipelineGraph,
    node_id: str,
    config: dict[str, Any],
) -> dict[str, frozenset[str]]:
    """Return lazy projection seeds for solve/estimate optimiser input.

    The optimiser node may also receive side inputs, for example ratebook
    banding factors.  Seed the proven data-input parent only, so side-input
    branches are not asked for solver columns they do not own.  When the data
    source also feeds the optimiser through a second physical edge (two frames
    of one multi-frame source), a node-keyed seed cannot describe one edge, so
    seed the optimiser itself and let the optimiser parent-demand rule keep
    those edges full-width.
    """
    required = _optimiser_input_required_columns(config)
    data_input_id = _resolve_optimiser_data_input_id(graph, node_id, config)
    if isinstance(data_input_id, str) and data_input_id:
        if _data_source_feeds_optimiser_through_parallel_edges(graph, node_id, data_input_id):
            return {node_id: required}
        return {data_input_id: required}
    return {}


def _resolve_optimiser_input_edge(
    graph: PipelineGraph,
    node_id: str,
    config: Mapping[str, Any],
    *,
    field: str,
    infer_single: bool = False,
) -> GraphEdge | None:
    """Resolve an optimiser selector as one exact incoming-edge name."""
    configured_name = config.get(field)
    if isinstance(configured_name, str) and configured_name:
        try:
            return incoming_edge_for_name(graph, node_id, configured_name)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Configured optimiser {field} {configured_name!r} is invalid: {exc}",
            ) from exc
    if configured_name not in (None, ""):
        raise HTTPException(
            status_code=400,
            detail=f"Configured optimiser {field} must be an exact input name.",
        )
    bindings = incoming_edge_bindings(graph, node_id)
    if infer_single and len(bindings) == 1:
        return bindings[0][0]
    if infer_single and len(bindings) > 1:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Optimiser {field} must name one exact connected input when the node "
                f"has multiple inputs: {[name for _edge, name in bindings]!r}."
            ),
        )
    return None


def _data_source_feeds_optimiser_through_parallel_edges(
    graph: PipelineGraph,
    node_id: str,
    data_input_id: str,
) -> bool:
    """Return whether the data source reaches the optimiser via >1 physical edge.

    Node-keyed projection seeds and per-node caches cannot express one frame of
    a multi-frame source; callers must fall back to optimiser-level handling.
    """
    parallel_edges = sum(
        1 for edge in graph.edges if edge.target == node_id and edge.source == data_input_id
    )
    return parallel_edges > 1


def _resolve_optimiser_data_input_id(
    graph: PipelineGraph,
    node_id: str,
    config: dict[str, Any],
) -> str | None:
    """Return the source node for the optimiser's exact selected data edge."""
    edge = _resolve_optimiser_input_edge(
        graph,
        node_id,
        config,
        field="data_input",
        infer_single=True,
    )
    return edge.source if edge is not None else None


def _find_optimiser_node(graph: PipelineGraph, node_id: str) -> GraphNode:
    """Find and validate an optimiser node in the graph."""
    return find_typed_node(graph, node_id, NodeType.OPTIMISER, "optimiser")


# ---------------------------------------------------------------------------
# Setup steps
#
# Each step runs one part of solve or auto-range setup and raises
# ``OptimiserSetupError`` for a refusal. The steps never touch the job store:
# the service records a failure on the job, returns chunk provenance to it,
# and answers the request.
# ---------------------------------------------------------------------------


class OptimiserSetupError(Exception):
    """A setup step's refusal: what the job records and what the request answers.

    *fields* default to the HTTP status and detail (``http_status_code``,
    ``error_detail``); a public contract error carries the fields
    ``contract_error_job_fields`` supplies instead.
    """

    def __init__(
        self,
        status_code: int,
        detail: object,
        *,
        reason: TerminalReason = "contract_error",
        message: str | None = None,
        fields: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail
        self.reason: TerminalReason = reason
        self.message = str(detail) if message is None else message
        self.fields: dict[str, Any] = (
            dict(fields)
            if fields is not None
            else {"http_status_code": status_code, "error_detail": detail}
        )

    def http_exception(self) -> HTTPException:
        return HTTPException(status_code=self.status_code, detail=self.detail)


def _execution_stage(
    execution_context: ExecutionContext | None,
    name: str,
    *,
    node_id: str | None = None,
) -> Any:
    if execution_context is None:
        return nullcontext()
    return execution_context.stage(name, node_id=node_id)


def resolve_data_input_frame(
    lazy_outputs: dict[str, Any],
    graph: PipelineGraph,
    config: dict[str, Any],
    node_id: str,
) -> Any:
    """Pick the optimiser's data-input frame from the pipeline outputs."""
    data_edge = _resolve_optimiser_input_edge(
        graph,
        node_id,
        config,
        field="data_input",
        infer_single=True,
    )
    source_lf = None
    if data_edge is not None:
        source_output = lazy_outputs.get(data_edge.source)
        if source_output is None:
            configured_name = config.get("data_input")
            raise OptimiserSetupError(
                400,
                f"Configured optimiser data_input {configured_name!r} did not produce data. "
                "Make sure it is connected to the optimiser node and produces a dataframe.",
            )
        try:
            source_lf = select_edge_source_output(source_output, data_edge)
        except (KeyError, RuntimeError, ValueError) as exc:
            raise OptimiserSetupError(
                400, f"Configured optimiser data_input could not resolve its frame: {exc}"
            ) from exc
    if source_lf is None:
        raise OptimiserSetupError(
            400,
            "No data arrived at the optimiser node. "
            "Make sure an upstream data source is connected and producing data.",
        )
    return source_lf


def validate_input_value_contracts(
    source_lf: Any,
    schema: Any,
    *,
    quote_id_col: str,
    validate_quote_id_nulls: bool,
    finite_columns: Iterable[str],
    cast_to_float32_columns: Iterable[str],
    execution_context: ExecutionContext | None,
) -> None:
    """Reject null quote ids and non-finite or null values in one streaming pass."""
    finite_columns = list(finite_columns)
    non_finite_check_cols = _non_finite_check_columns(schema, finite_columns)
    null_check_cols = _null_check_columns(schema, finite_columns)
    validation_exprs = _value_contract_validation_exprs(
        quote_id_col=quote_id_col,
        validate_quote_id_nulls=validate_quote_id_nulls,
        non_finite_check_cols=non_finite_check_cols,
        null_check_cols=null_check_cols,
        cast_to_float32_cols=set(cast_to_float32_columns),
    )
    if not validation_exprs:
        return

    validation_counts = streaming_collect(
        source_lf.select(validation_exprs),
        execution_context=execution_context,
    )
    if validate_quote_id_nulls:
        null_count = int(validation_counts.get_column(_QUOTE_ID_NULL_COUNT_ALIAS).item())
        if null_count > 0:
            raise OptimiserSetupError(400, _quote_id_null_detail(null_count))

    non_finite_detail = _non_finite_detail_from_counts(validation_counts, non_finite_check_cols)
    if non_finite_detail is not None:
        raise OptimiserSetupError(400, non_finite_detail)

    null_value_detail = _null_value_detail_from_counts(validation_counts, null_check_cols)
    if null_value_detail is not None:
        raise OptimiserSetupError(400, null_value_detail)


def validate_and_project(
    source_lf: Any,
    config: dict[str, Any],
    *,
    validate_quote_id_nulls: bool = True,
    execution_context: ExecutionContext | None = None,
) -> tuple[list[str], Any]:
    """Validate the solver columns and project the solver input.

    Returns ``(constraint_cols, projected_lazy_frame)``.
    """
    import polars as pl

    objective = str(config["objective"])
    constraints = config["constraints"]
    qid_col = str(config.get("quote_id", "quote_id"))
    mult_col = str(config.get("scenario_value", "scenario_value"))
    step_col = str(config.get("scenario_index", "scenario_index"))

    schema = source_lf.collect_schema()
    available_cols = set(schema.names())
    required_cols = {objective, qid_col, mult_col, step_col}
    for cname in constraints:
        required_cols.add(cname)
    detail = _missing_columns_detail(required_cols, available_cols)
    if detail is not None:
        raise OptimiserSetupError(400, detail)

    constraint_cols = list(constraints.keys()) if isinstance(constraints, dict) else []
    qid_dtype = schema[qid_col]
    detail = _invalid_quote_id_dtype_detail(schema, qid_col)
    if detail is not None:
        raise OptimiserSetupError(400, detail)

    # Non-finite objective/constraint/scenario values fail here as an explicit
    # contract error naming the column; downstream library behaviour silently
    # accepts e.g. a NaN objective and "converges" on wrong totals (C7). Only
    # float-typed columns can carry NaN/inf; the solver consumes Float32 (see
    # cast_map below), so float columns are checked at that precision to also
    # reject Float64 values that overflow to +-inf on the cast. scenario_index
    # is cast to Int32 downstream, so its source values are checked.
    # Genuinely-null values (any dtype) are rejected in the same pass: the
    # external aggregation's treatment of null is undefined.
    validate_input_value_contracts(
        source_lf,
        schema,
        quote_id_col=qid_col,
        validate_quote_id_nulls=validate_quote_id_nulls,
        finite_columns=[objective, mult_col, step_col, *constraint_cols],
        cast_to_float32_columns={objective, mult_col, *constraint_cols},
        execution_context=execution_context,
    )

    solver_cols = [qid_col, step_col, mult_col, objective] + [
        c for c in constraint_cols if c in available_cols
    ]
    cast_map: dict[str, pl.DataType] = {
        step_col: pl.Int32(),
        mult_col: pl.Float32(),
        objective: pl.Float32(),
    }
    for c in constraint_cols:
        cast_map[c] = pl.Float32()
    cast_exprs = [pl.col(c).cast(t) for c, t in cast_map.items() if schema[c] != t]
    if qid_dtype == pl.String:
        cast_exprs.append(pl.col(qid_col).cast(pl.Categorical))

    return constraint_cols, source_lf.select(solver_cols).with_columns(cast_exprs)


def validate_and_project_auto_range(
    source_lf: Any,
    config: dict[str, Any],
    *,
    execution_context: ExecutionContext | None = None,
) -> tuple[list[str], Any]:
    """Validate and project only the columns auto-range needs.

    Auto-range computes per-quote extrema for configured constraints. When the
    projected input includes the configured objective, it validates the
    objective for parity with solver input contracts, but it never passes
    objective, scenario index, or scenario value columns to the range
    estimator.
    """
    import polars as pl

    constraints = config["constraints"]
    objective = str(config["objective"])
    qid_col = str(config.get("quote_id", "quote_id"))

    schema = source_lf.collect_schema()
    available_cols = set(schema.names())
    constraint_cols = list(constraints.keys()) if isinstance(constraints, dict) else []
    detail = _missing_columns_detail({qid_col, *constraint_cols}, available_cols)
    if detail is not None:
        raise OptimiserSetupError(400, detail)

    qid_dtype = schema[qid_col]
    detail = _invalid_quote_id_dtype_detail(schema, qid_col)
    if detail is not None:
        raise OptimiserSetupError(400, detail)

    value_check_cols = [*constraint_cols]
    if objective in available_cols:
        value_check_cols.insert(0, objective)
    validate_input_value_contracts(
        source_lf,
        schema,
        quote_id_col=qid_col,
        validate_quote_id_nulls=True,
        finite_columns=value_check_cols,
        cast_to_float32_columns=value_check_cols,
        execution_context=execution_context,
    )

    auto_range_cols = [qid_col, *constraint_cols]
    cast_exprs = [pl.col(c).cast(pl.Float32()) for c in constraint_cols]
    if qid_dtype == pl.String:
        cast_exprs.append(pl.col(qid_col).cast(pl.Categorical))
    return constraint_cols, source_lf.select(auto_range_cols).with_columns(cast_exprs)


def extract_ratebook_factors(
    lazy_outputs: dict[str, Any],
    graph: PipelineGraph,
    optimiser_node_id: str,
    config: dict[str, Any],
    mode: str,
    *,
    execution_context: ExecutionContext | None = None,
    artifact_dir: str | None = None,
) -> Any:
    """Persist the ratebook factor artifact and return its handle (None for online mode)."""
    import polars as pl

    if mode != "ratebook":
        return None
    banding_source = config.get("banding_source")
    banding_edge = _resolve_optimiser_input_edge(
        graph,
        optimiser_node_id,
        config,
        field="banding_source",
    )
    node_id = banding_edge.source if banding_edge is not None else None
    with _execution_stage(execution_context, "optimiser_extract_factors", node_id=node_id):
        if banding_edge is None:
            raise OptimiserSetupError(
                400,
                "Ratebook mode requires a configured banding_source. "
                "Select a banding node in the Rating Factor Source dropdown.",
            )
        if banding_edge.source not in lazy_outputs:
            raise OptimiserSetupError(
                400,
                f"Configured ratebook banding_source {banding_source!r} did not "
                "produce data. Make sure it is connected to the optimiser node.",
            )

        try:
            source = select_edge_source_output(lazy_outputs[banding_edge.source], banding_edge)
        except (KeyError, RuntimeError, ValueError) as exc:
            raise OptimiserSetupError(
                400, f"Configured ratebook banding_source could not resolve its frame: {exc}"
            ) from exc
        factors_lf = source.lazy() if isinstance(source, pl.DataFrame) else source
        schema = factors_lf.collect_schema()
        available_cols = set(schema.names())
        missing_cols = sorted(ratebook_factor_required_columns(config) - available_cols)
        if missing_cols:
            raise OptimiserSetupError(
                400,
                "Missing columns in ratebook banding source: "
                f"{missing_cols}. Available: {sorted(available_cols)}",
            )

        qid_col = str(config.get("quote_id", "quote_id"))
        raw_factor_columns = config.get("factor_columns") or []
        factor_cols = list(
            dict.fromkeys(
                column
                for group in raw_factor_columns
                for column in group
                if isinstance(column, str)
            )
        )
        ordered_cols = list(dict.fromkeys([qid_col, *factor_cols]))
        projected = factors_lf.select([pl.col(column) for column in ordered_cols])

        handle = _optimiser_artifacts._persist_ratebook_factors_lazy_artifact(
            projected,
            artifact_dir=Path(artifact_dir) if artifact_dir is not None else None,
        )
        if int(handle["row_count"]) == 0:
            _optimiser_artifacts._cleanup_orphan_apply_result_artifact(
                handle,
                job_id="<setup>",
                event="empty_ratebook_factor_artifact_cleanup_failed",
            )
            raise OptimiserSetupError(400, "Ratebook banding source is empty.")
        if execution_context is not None:
            # If checkpoint raises, the handle never returns and the caller's
            # finally cannot see it, so clean up here.
            try:
                execution_context.checkpoint(label="after_ratebook_factor_sink", node_id=node_id)
            except BaseException:
                _optimiser_artifacts._cleanup_orphan_apply_result_artifact(
                    handle,
                    job_id="<setup>",
                    event="extract_factors_post_sink_checkpoint_cleanup_failed",
                )
                raise
        return handle


@contextlib.contextmanager
def grid_construction_failures(node_id: str) -> Iterator[None]:
    """Translate a failure while writing the solver input or building its grid.

    Refusals already typed (``OptimiserSetupError``, ``HTTPException``) and
    execution admission, cancellation and memory errors pass through.
    """
    try:
        yield
    except (
        OptimiserSetupError,
        HTTPException,
        ExecutionAdmissionError,
        ExecutionCancelledError,
        ExecutionMemoryLimitExceededError,
    ):
        raise
    except PUBLIC_CONTRACT_ERROR_TYPES as exc:
        http_exc = contract_error_http_exception(exc)
        raise OptimiserSetupError(
            http_exc.status_code,
            http_exc.detail,
            reason=contract_error_terminal_reason(exc),
            message=str(exc),
            fields=contract_error_job_fields(exc),
        ) from None
    except BoundedMemoryUnsupportedError as exc:
        logger.warning("grid_bounded_streaming_unsupported", error=str(exc), node_id=node_id)
        raise OptimiserSetupError(
            422, f"Grid construction cannot run in bounded streaming mode: {exc}"
        ) from exc
    except Exception as exc:
        logger.error("grid_build_failed", error=str(exc), node_id=node_id, exc_info=True)
        raise OptimiserSetupError(
            500,
            "Grid construction failed. Check the server logs for details.",
            reason="error",
        ) from exc


def write_solver_input(
    scored_lf: Any,
    output_path: str,
    node_id: str,
    *,
    execution_context: ExecutionContext | None = None,
    allow_borrow: bool = True,
) -> str:
    """Write the projected solver input to *output_path*, or borrow its snapshot.

    With *allow_borrow*, an unchanged single-file parquet scan (read under the
    caller's still-open seed-plan lease) is returned instead of copied.
    """
    with (
        grid_construction_failures(node_id),
        _execution_stage(execution_context, "optimiser_write_solver_input", node_id=node_id),
    ):
        borrowed_path = _projected_parquet_input_path(scored_lf) if allow_borrow else None
        if borrowed_path is not None:
            return str(borrowed_path)
        bounded_sink(scored_lf, output_path)
        return output_path


def grid_chunk_decision(config: Mapping[str, Any], input_path: str) -> _ChunkSizeDecision:
    """The chunk size for building the quote grid from *input_path*, with its provenance."""
    try:
        return _chunk_size_decision_for_parquet(config, Path(input_path), source="optimiser_grid")
    except ValueError as exc:
        raise OptimiserSetupError(400, f"Grid construction failed: {exc}") from exc


def build_quote_grid(
    input_path: str,
    constraint_cols: list[str],
    config: Mapping[str, Any],
    chunk_size: int,
    *,
    execution_context: ExecutionContext | None,
) -> QuoteGrid:
    """Admit the resident grid, then build it from the solver-input parquet."""
    from price_contour import build_grid_from_parquet_chunked

    objective = config["objective"]
    qid_col = config.get("quote_id", "quote_id")
    mult_col = config.get("scenario_value", "scenario_value")
    step_col = config.get("scenario_index", "scenario_index")
    _admit_resident_grid(
        Path(input_path),
        [qid_col, step_col, mult_col, objective, *constraint_cols],
        qid_col,
        len(constraint_cols),
        chunk_size,
        execution_context,
    )
    return build_grid_from_parquet_chunked(
        input_path,
        constraint_cols,
        chunk_size,
        quote_id=qid_col,
        scenario_index=step_col,
        scenario_value=mult_col,
        objective=objective,
    )


# ---------------------------------------------------------------------------
# Input estimate
#
# The estimate counts the optimiser's projected input: the pipeline up to the
# data input, then exactly one streaming aggregation scan. The same code runs
# in the warm interactive worker (process mode) and in-process (thread mode).
# ---------------------------------------------------------------------------


def _estimate_quote_id_column_or_raise(source_lf: Any, config: dict[str, Any]) -> str:
    """Schema-only pre-flight for the estimate; returns the quote-id column.

    Mirrors the column-presence and quote-id dtype checks (and their exact
    messages) from ``validate_and_project`` WITHOUT its value-contract scans:
    solve-grade NaN/inf validation is the solve path's job, while the estimate
    only counts rows and must stay a single-scan operation.
    ``collect_schema()`` resolves the lazy schema without reading data.
    """
    import polars as pl

    objective = str(config["objective"])
    constraints = config.get("constraints") or {}
    qid_col = str(config.get("quote_id", "quote_id"))
    mult_col = str(config.get("scenario_value", "scenario_value"))
    step_col = str(config.get("scenario_index", "scenario_index"))

    schema = source_lf.collect_schema()
    available_cols = set(schema.names())
    required_cols = {objective, qid_col, mult_col, step_col, *constraints}
    missing_cols = sorted(required_cols - available_cols)
    if missing_cols:
        avail = sorted(available_cols)
        raise HTTPException(
            status_code=400,
            detail=f"Missing columns in scored data: {missing_cols}. Available: {avail}",
        )

    qid_dtype = schema[qid_col]
    if not (
        qid_dtype == pl.String or qid_dtype == pl.Categorical or isinstance(qid_dtype, pl.Enum)
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                f"{qid_col} must be Utf8 (String), Categorical, or Enum, got {qid_dtype}. "
                "Numeric, binary, and other dtypes are not supported as quote_id columns."
            ),
        )
    return qid_col


def estimate_input_metrics(source_lf: Any, config: dict[str, Any]) -> dict[str, int | float | None]:
    """Quote and scenario counts for the projected optimiser input, in ONE scan.

    Counting only needs the quote-id column; selecting it first lets
    projection pushdown skip every other solver column. The null-quote_id
    contract check is folded into the same scan: null keys form their own
    ``group_by`` group, so their row count comes for free instead of costing a
    second full pass.
    """
    import polars as pl

    quote_id_col = _estimate_quote_id_column_or_raise(source_lf, config)
    scenario_counts = (
        source_lf.select(pl.col(quote_id_col))
        .group_by(quote_id_col)
        .agg(pl.len().alias("scenario_count"))
    )
    non_null_counts = pl.col("scenario_count").filter(pl.col(quote_id_col).is_not_null())
    row = streaming_collect(
        scenario_counts.select(
            pl.col("scenario_count")
            .filter(pl.col(quote_id_col).is_null())
            .sum()
            .alias("null_quote_id_row_count"),
            pl.col(quote_id_col).is_not_null().sum().alias("quote_count"),
            non_null_counts.min().alias("scenarios_per_quote_min"),
            non_null_counts.max().alias("scenarios_per_quote_max"),
            non_null_counts.mean().alias("scenarios_per_quote_mean"),
            non_null_counts.sum().alias("expanded_row_count"),
        ),
    ).row(0, named=True)
    null_quote_id_rows = int(row["null_quote_id_row_count"] or 0)
    if null_quote_id_rows > 0:
        # Same contract (status + message) as the solve path's standalone
        # null check in ``validate_and_project``.
        raise HTTPException(
            status_code=400,
            detail=(
                f"{_NULL_QUOTE_ID_DETAIL_PREFIX} ({null_quote_id_rows} rows). "
                "Every row must have a non-null quote_id; "
                "check upstream filters and joins."
            ),
        )
    return {
        "quote_count": row["quote_count"],
        "scenarios_per_quote_min": row["scenarios_per_quote_min"],
        "scenarios_per_quote_max": row["scenarios_per_quote_max"],
        "scenarios_per_quote_mean": row["scenarios_per_quote_mean"],
        "expanded_row_count": row["expanded_row_count"],
    }


# The estimate failures with a typed answer; anything else is unexpected.
ESTIMATE_MAPPED_ERRORS: tuple[type[BaseException], ...] = (
    OptimiserSetupError,
    ExecutionAdmissionError,
    ExecutionMemoryLimitExceededError,
    BoundedMemoryUnsupportedError,
    *PUBLIC_CONTRACT_ERROR_TYPES,
)


def estimate_failure_http_exception(exc: BaseException, *, node_id: str) -> HTTPException:
    """The typed answer to one of ``ESTIMATE_MAPPED_ERRORS``."""
    if isinstance(exc, OptimiserSetupError):
        return exc.http_exception()
    if isinstance(exc, ExecutionAdmissionError | ExecutionMemoryLimitExceededError):
        return memory_limit_http_exception(exc, operation_noun="Optimiser estimate")
    if isinstance(exc, BoundedMemoryUnsupportedError):
        logger.warning(
            "optimiser_estimate_bounded_streaming_unsupported",
            error=str(exc),
            node_id=node_id,
        )
        return HTTPException(
            status_code=422,
            detail=f"Optimiser estimate cannot run in bounded streaming mode: {exc}",
        )
    if isinstance(exc, PUBLIC_CONTRACT_ERROR_TYPES):
        return contract_error_http_exception(exc)
    raise TypeError(f"{type(exc).__name__} has no typed estimate answer") from exc
