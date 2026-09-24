"""Solve-input planning for the optimiser: projection, input resolution, validation, sizing.

What setup reads and from where (the column demand each node must produce,
the retained side inputs, the exact data-input edge), the value-contract
checks and their details, and how the solver input is sized and admitted as
a resident grid. Nothing here knows about jobs, solver results or artifact
publication; the solve service orchestrates these steps.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from haute._execution_admission import (
    ExecutionAdmissionError,
    execution_budget_for_profile,
)
from haute._execution_context import (
    ExecutionContext,
    ExecutionProfile,
)
from haute._graph_utils import (
    incoming_edge_bindings,
    incoming_edge_for_name,
)
from haute._polars_utils import (
    read_parquet_metadata,
    streaming_collect,
)
from haute._types import (
    GraphEdge,
    GraphNode,
    PipelineGraph,
)
from haute.execution import (
    ProjectionRequest,
    plan_projection,
)
from haute.graph_utils import NodeType
from haute.routes._helpers import find_typed_node

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
