"""OptimiserSolveService — orchestrates optimisation solving, extracted from the route handler.

The route handler becomes a thin adapter that delegates to
``OptimiserSolveService.start()``.
"""

from __future__ import annotations

import ast
import gc
import os
import re
import shutil
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from fastapi import HTTPException

if TYPE_CHECKING:
    import polars as pl
    from price_contour import QuoteGrid

from haute._banding_config import normalise_banding_factors
from haute._logging import get_logger
from haute._types import (
    GraphNode,
    OnlineSolveResultLike,
    PipelineGraph,
    RatebookSolveResultLike,
    SolveResultLike,
)
from haute.errors import ContractMismatchError
from haute.graph_utils import NodeType, graph_fingerprint
from haute.routes._background_jobs import BackgroundJobStoppedError, CancellableJobRegistry
from haute.routes._helpers import find_typed_node
from haute.routes._job_store import JobStore, register_artifact_cleaner
from haute.routes._optimiser_limits import limited_frontier_payload
from haute.schemas import (
    OptimiserEstimateRequest,
    OptimiserFrontierAutoRangeRequest,
    OptimiserFrontierAutoRangeResponse,
    OptimiserFrontierAutoRangeStartResponse,
    OptimiserFrontierAutoRangeStatusResponse,
    OptimiserFrontierRange,
    OptimiserSolveRequest,
    OptimiserSolveResponse,
    _normalise_frontier_range_pair,
)

logger = get_logger(component="server.optimiser.solve")

# ── Default constants ─────────────────────────────────────────────
_DEFAULT_TIMEOUT = int(os.environ.get("HAUTE_SOLVER_TIMEOUT", "300"))
_DEFAULT_AUTO_RANGE_TIMEOUT = int(os.environ.get("HAUTE_AUTO_RANGE_TIMEOUT", "1800"))
_HISTOGRAM_BINS = 20  # bin count for scenario-value distribution histogram
_DEFAULT_MAX_ITER = 50  # max solver iterations (online & ratebook)
_DEFAULT_CHUNK_SIZE = 500_000  # rows per chunk for solver processing
_DEFAULT_AUTO_RANGE_CHUNK_SIZE = int(
    os.environ.get("HAUTE_AUTO_RANGE_CHUNK_SIZE", "2000000")
)
_DEFAULT_AUTO_RANGE_PARTITIONS = int(
    os.environ.get("HAUTE_AUTO_RANGE_PARTITIONS", "16")
)  # disk buckets for chunked auto-range aggregation
_DEFAULT_TOLERANCE = 1e-6  # convergence tolerance for solver
_DEFAULT_MAX_CD_ITERATIONS = 10  # max coordinate-descent iterations (ratebook)
_DEFAULT_CD_TOLERANCE = 1e-3  # coordinate-descent convergence tolerance (ratebook)
_APPLY_RESULT_HANDLE_KEY = "apply_result"
_APPLY_RESULT_HANDLE_KIND = "optimiser_apply_result"
_ARTIFACT_HANDLE_VERSION = 1
_APPLY_ARTIFACT_ROOT_NAME = "haute/optimiser_apply"
_APPLY_ARTIFACT_DIR_PREFIX = "apply_"
_APPLY_RESULT_FILENAME = "result.parquet"
_JOB_TYPE_KEY = "job_type"
_SOLVE_JOB_TYPE = "solve"
_ESTIMATE_JOB_TYPE = "estimate"
_FRONTIER_AUTO_RANGE_JOB_TYPE = "frontier_auto_range"
_NULL_QUOTE_ID_DETAIL_PREFIX = "Null quote_id values found in optimiser input"
_AUTO_RANGE_BUCKET_COLUMN = "__haute_frontier_auto_range_bucket"
_FRONTIER_AUTO_RANGE_CANCELLED_STATUS = "cancelled"
_FRONTIER_AUTO_RANGE_SUPERSEDED_STATUS = "superseded"
_FRONTIER_AUTO_RANGE_TERMINAL_STATUSES = frozenset(
    {
        "completed",
        "error",
        _FRONTIER_AUTO_RANGE_CANCELLED_STATUS,
        _FRONTIER_AUTO_RANGE_SUPERSEDED_STATUS,
    }
)
_NON_BLOCKING_RUNNING_JOB_TYPES = frozenset(
    {
        _ESTIMATE_JOB_TYPE,
        _FRONTIER_AUTO_RANGE_JOB_TYPE,
    }
)
_STREAMING_AUTO_RANGE_ALLOWED_NODE_TYPES = frozenset(
    {
        NodeType.SCENARIO_EXPANDER,
        NodeType.POLARS,
        NodeType.MODEL_SCORE,
    }
)
_STREAMING_AUTO_RANGE_GLOBAL_CODE_MARKERS = (
    ".group_by(",
    ".groupby(",
    ".agg(",
    ".join(",
    ".sort(",
    ".unique(",
    ".over(",
    ".rank(",
    ".sample(",
    ".head(",
    ".tail(",
    ".limit(",
    ".slice(",
    ".with_row_index(",
    ".with_row_count(",
    ".rolling(",
    ".rolling_",
    ".cum_",
)
_STREAMING_AUTO_RANGE_GLOBAL_METHOD_NAMES = frozenset(
    {
        "agg",
        "approx_n_unique",
        "collect",
        "count",
        "cum_count",
        "cum_max",
        "cum_min",
        "cum_prod",
        "cum_sum",
        "diff",
        "explode",
        "first",
        "group_by",
        "groupby",
        "head",
        "interpolate",
        "join",
        "last",
        "len",
        "limit",
        "map_batches",
        "map_elements",
        "max",
        "mean",
        "median",
        "min",
        "n_unique",
        "over",
        "product",
        "quantile",
        "rank",
        "rolling",
        "sample",
        "shift",
        "slice",
        "sort",
        "std",
        "sum",
        "tail",
        "unique",
        "var",
        "with_row_count",
        "with_row_index",
    }
)
_STREAMING_AUTO_RANGE_ROW_LOCAL_DF_METHOD_NAMES = frozenset(
    {"cast", "drop", "filter", "rename", "select", "with_columns"}
)
_STREAMING_AUTO_RANGE_ROW_LOCAL_EXPR_METHOD_NAMES = frozenset(
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
_STREAMING_AUTO_RANGE_ROW_LOCAL_POLARS_FUNCTIONS = frozenset(
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


@dataclass(frozen=True, slots=True)
class _StreamingAutoRangePlan:
    base_node_id: str
    scenario_node_id: str
    chain_node_ids: tuple[str, ...]
    required_output_columns_by_node: Mapping[str, set[str] | None]
    base_required_columns: frozenset[str] | None


def _positive_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer.")
    return int(value)


def _chunk_size_from_config(config: dict[str, Any]) -> int:
    return _positive_int(config.get("chunk_size", _DEFAULT_CHUNK_SIZE), field="chunk_size")


def _auto_range_chunk_size_from_config(config: dict[str, Any]) -> int:
    if "auto_range_chunk_size" in config:
        return _positive_int(
            config["auto_range_chunk_size"],
            field="auto_range_chunk_size",
        )
    return _positive_int(
        config.get("chunk_size", _DEFAULT_AUTO_RANGE_CHUNK_SIZE),
        field="chunk_size",
    )


def _job_elapsed_seconds(job: dict[str, Any], fallback: float = 0.0) -> float:
    """Return wall-clock elapsed seconds for a job when start_time is available."""
    start_time = job.get("start_time")
    fallback_elapsed = max(0.0, float(fallback))
    if isinstance(start_time, bool) or not isinstance(start_time, int | float):
        return fallback_elapsed
    return max(fallback_elapsed, time.monotonic() - float(start_time), 0.0)


def _optimiser_side_input_ids(graph: PipelineGraph, node_id: str) -> frozenset[str]:
    """Return optimiser side-input node ids the solver needs after graph execution."""
    node = _find_optimiser_node(graph, node_id)
    config = node.data.config
    if config.get("mode", "online") != "ratebook":
        return frozenset()
    banding_source = config.get("banding_source")
    if isinstance(banding_source, str) and banding_source:
        return frozenset({banding_source})
    return frozenset()


def _optimiser_input_required_columns(config: dict[str, Any]) -> frozenset[str]:
    """Return the columns needed to validate and consume optimiser input."""
    objective = str(config["objective"])
    qid_col = str(config.get("quote_id", "quote_id"))
    mult_col = str(config.get("scenario_value", "scenario_value"))
    step_col = str(config.get("scenario_index", "scenario_index"))
    constraints = config.get("constraints") or {}
    constraint_cols = [str(cname) for cname in constraints]
    return frozenset({qid_col, step_col, mult_col, objective, *constraint_cols})


def _ratebook_factor_required_columns(config: dict[str, Any]) -> frozenset[str]:
    """Return factor-side columns required from a ratebook banding source."""
    columns: set[str] = {str(config.get("quote_id", "quote_id"))}
    raw_factor_columns = config.get("factor_columns") or []
    for group in raw_factor_columns:
        if isinstance(group, str):
            raise ValueError("ratebook factor_columns must be lists of column names")
        if not isinstance(group, Iterable):
            raise ValueError("ratebook factor_columns must be lists of column names")
        for column in group:
            if not isinstance(column, str) or not column:
                raise ValueError("ratebook factor_columns must contain non-empty string names")
            columns.add(column)
    return frozenset(columns)


def _auto_range_input_required_columns(config: dict[str, Any]) -> frozenset[str]:
    """Return the optimiser input columns needed by auto-range only."""
    qid_col = str(config.get("quote_id", "quote_id"))
    constraints = config.get("constraints") or {}
    constraint_cols = [str(cname) for cname in constraints]
    return frozenset({qid_col, *constraint_cols})


def _auto_range_partition_count_from_config(config: dict[str, Any]) -> int:
    return _positive_int(
        config.get("auto_range_partition_count", _DEFAULT_AUTO_RANGE_PARTITIONS),
        field="auto_range_partition_count",
    )


def _auto_range_timeout_from_config(config: dict[str, Any]) -> int:
    return _positive_int(
        config.get("auto_range_timeout", _DEFAULT_AUTO_RANGE_TIMEOUT),
        field="auto_range_timeout",
    )


def _auto_range_required_columns_by_node(
    graph: PipelineGraph,
    node_id: str,
    config: dict[str, Any],
    *,
    mode: str,
) -> dict[str, frozenset[str]]:
    """Return lazy projection seeds for online frontier auto-range.

    Online auto-range consumes only the optimiser input frame.  When a
    configured ``data_input`` is a direct optimiser parent, seed that node so
    other optimiser parents do not inherit the solver-column projection.  In
    ratebook mode we leave projection unseeded because factor-source coupling
    needs a wider, mode-specific safety proof before pruning.
    """
    if mode != "online":
        return {}

    required = _auto_range_input_required_columns(config)
    data_input_id = _resolve_online_auto_range_data_input_id(graph, node_id, config)
    if isinstance(data_input_id, str) and data_input_id:
        return {data_input_id: required}
    return {node_id: required}


def _optimiser_solve_required_columns_by_node(
    graph: PipelineGraph,
    node_id: str,
    config: dict[str, Any],
) -> dict[str, frozenset[str]]:
    """Return lazy projection seeds for solve/estimate optimiser input.

    The optimiser node may also receive side inputs, for example ratebook
    banding factors.  Seed the proven data-input parent only, so side-input
    branches are not asked for solver columns they do not own.
    """
    required = _optimiser_input_required_columns(config)
    data_input_id = _resolve_optimiser_data_input_id(graph, node_id, config)
    if isinstance(data_input_id, str) and data_input_id:
        if (
            config.get("mode", "online") == "ratebook"
            and config.get("banding_source") == data_input_id
        ):
            required = frozenset(set(required) | set(_ratebook_factor_required_columns(config)))
        return {data_input_id: required}
    return {}


def _resolve_optimiser_data_input_id(
    graph: PipelineGraph,
    node_id: str,
    config: dict[str, Any],
) -> str | None:
    """Return the optimiser dataframe input when it can be proven."""
    data_input_id = config.get("data_input")
    direct_parents = list(graph.parents_of.get(node_id, []))
    if isinstance(data_input_id, str) and data_input_id:
        if data_input_id not in set(direct_parents):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Configured optimiser data_input {data_input_id!r} is not connected "
                    f"to optimiser node {node_id!r}."
                ),
            )
        return data_input_id
    if len(direct_parents) == 1:
        return direct_parents[0]
    return None


def _resolve_online_auto_range_data_input_id(
    graph: PipelineGraph,
    node_id: str,
    config: dict[str, Any],
) -> str | None:
    """Return the optimiser input node id that online auto-range consumes."""
    return _resolve_optimiser_data_input_id(graph, node_id, config)


def _row_local_polars_call_is_supported(call: ast.Call) -> bool:
    func = call.func
    if isinstance(func, ast.Attribute):
        method_name = func.attr
        if method_name in _STREAMING_AUTO_RANGE_GLOBAL_METHOD_NAMES:
            return False
        if isinstance(func.value, ast.Name) and func.value.id == "pl":
            if method_name not in _STREAMING_AUTO_RANGE_ROW_LOCAL_POLARS_FUNCTIONS:
                return False
        elif method_name not in (
            _STREAMING_AUTO_RANGE_ROW_LOCAL_DF_METHOD_NAMES
            | _STREAMING_AUTO_RANGE_ROW_LOCAL_EXPR_METHOD_NAMES
        ):
            return False
        elif not _row_local_polars_expr_is_supported(func.value):
            return False
        return all(
            _row_local_polars_expr_is_supported(value)
            for value in (*call.args, *(keyword.value for keyword in call.keywords))
        )
    return False


def _row_local_polars_expr_is_supported(node: ast.AST) -> bool:
    if isinstance(node, ast.Call):
        return _row_local_polars_call_is_supported(node)
    if isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Name) and node.value.id == "pl":
            return True
        return _row_local_polars_expr_is_supported(node.value)
    if isinstance(node, ast.Name | ast.Constant):
        return True
    if isinstance(node, ast.BinOp):
        return _row_local_polars_expr_is_supported(
            node.left
        ) and _row_local_polars_expr_is_supported(node.right)
    if isinstance(node, ast.UnaryOp):
        return _row_local_polars_expr_is_supported(node.operand)
    if isinstance(node, ast.BoolOp):
        return all(_row_local_polars_expr_is_supported(value) for value in node.values)
    if isinstance(node, ast.Compare):
        return _row_local_polars_expr_is_supported(node.left) and all(
            _row_local_polars_expr_is_supported(value) for value in node.comparators
        )
    if isinstance(node, ast.IfExp):
        return (
            _row_local_polars_expr_is_supported(node.test)
            and _row_local_polars_expr_is_supported(node.body)
            and _row_local_polars_expr_is_supported(node.orelse)
        )
    if isinstance(node, ast.List | ast.Tuple | ast.Set):
        return all(_row_local_polars_expr_is_supported(value) for value in node.elts)
    if isinstance(node, ast.Dict):
        return all(
            (key is None or _row_local_polars_expr_is_supported(key))
            and _row_local_polars_expr_is_supported(value)
            for key, value in zip(node.keys, node.values, strict=True)
        )
    if isinstance(node, ast.Subscript):
        return _row_local_polars_expr_is_supported(
            node.value
        ) and _row_local_polars_expr_is_supported(node.slice)
    if isinstance(node, ast.Slice):
        return (
            (node.lower is None or _row_local_polars_expr_is_supported(node.lower))
            and (node.upper is None or _row_local_polars_expr_is_supported(node.upper))
            and (node.step is None or _row_local_polars_expr_is_supported(node.step))
        )
    return False


def _row_local_polars_stmt_is_supported(stmt: ast.stmt) -> bool:
    if isinstance(stmt, ast.Assign):
        return all(isinstance(target, ast.Name) for target in stmt.targets) and (
            _row_local_polars_expr_is_supported(stmt.value)
        )
    if isinstance(stmt, ast.AnnAssign):
        return isinstance(stmt.target, ast.Name) and (
            stmt.value is not None and _row_local_polars_expr_is_supported(stmt.value)
        )
    if isinstance(stmt, ast.Expr):
        return _row_local_polars_expr_is_supported(stmt.value)
    return False


def _looks_chunk_local_user_code(code: object) -> bool:
    """Return whether user code is eligible for chunk-local execution.

    The streaming path only uses code that can be proven row-local by a small
    AST allow-list.  Anything global, order-sensitive, or custom falls back to
    the existing full lazy path where Polars can execute the graph as authored.
    """
    if not isinstance(code, str) or not code.strip():
        return True
    compact = re.sub(r"\s+", "", code).lower()
    if any(marker in compact for marker in _STREAMING_AUTO_RANGE_GLOBAL_CODE_MARKERS):
        return False
    try:
        module = ast.parse(code)
    except SyntaxError:
        return False
    return all(_row_local_polars_stmt_is_supported(stmt) for stmt in module.body)


def _streaming_auto_range_node_is_eligible(node: GraphNode) -> bool:
    node_type = node.data.nodeType
    config = node.data.config
    if node_type not in _STREAMING_AUTO_RANGE_ALLOWED_NODE_TYPES:
        return False
    if node_type == NodeType.MODEL_SCORE:
        # Model-score post-processing and column renames can be arbitrary
        # user-defined transforms; keep them on the full lazy path for now.
        return not (config.get("code") or "").strip() and not config.get("column_renames")
    return _looks_chunk_local_user_code(config.get("code"))


def _projection_plan_for_auto_range(
    graph: PipelineGraph,
    node_id: str,
    *,
    required_columns_by_node: Mapping[str, Iterable[str]],
) -> Any:
    from haute._execute_lazy import _compute_projection_plan
    from haute.graph_utils import _prepare_graph

    node_map, order, parents_of, _id_to_name = _prepare_graph(
        graph,
        node_id,
        source="batch",
    )
    children_of: dict[str, list[str]] = {nid: [] for nid in order}
    for child_id, parent_ids in parents_of.items():
        for parent_id in parent_ids:
            if parent_id in children_of:
                children_of[parent_id].append(child_id)
    return _compute_projection_plan(
        order,
        children_of,
        node_map,
        required_columns_by_node=required_columns_by_node,
    )


def _upstream_slice_contains_node_type(
    graph: PipelineGraph,
    node_id: str,
    node_type: NodeType,
    *,
    resolve_node: Callable[[GraphNode, dict[str, GraphNode]], GraphNode],
) -> bool:
    node_map = graph.node_map
    stack = [node_id]
    seen: set[str] = set()
    while stack:
        current_id = stack.pop()
        if current_id in seen:
            continue
        seen.add(current_id)
        raw_node = node_map.get(current_id)
        if raw_node is not None and resolve_node(raw_node, node_map).data.nodeType == node_type:
            return True
        stack.extend(graph.parents_of.get(current_id, []))
    return False


def _build_streaming_auto_range_plan(
    graph: PipelineGraph,
    node_id: str,
    config: dict[str, Any],
    *,
    mode: str,
    required_columns_by_node: Mapping[str, Iterable[str]],
) -> _StreamingAutoRangePlan | None:
    """Build a strict online auto-range plan that chunks before expansion.

    The first implementation intentionally handles only a single-parent chain
    from one scenario expander to the optimiser ``data_input``.  Unsupported
    graph shapes keep the existing general lazy execution path.
    """
    from haute._builders import resolve_instance_node

    if mode != "online":
        return None

    try:
        data_input_id = _resolve_online_auto_range_data_input_id(graph, node_id, config)
    except HTTPException:
        return None
    if not isinstance(data_input_id, str) or not data_input_id:
        return None

    node_map = graph.node_map
    downstream_to_upstream: list[str] = []
    current_id = data_input_id
    seen: set[str] = set()
    base_node_id: str | None = None
    scenario_node_id: str | None = None
    while True:
        if current_id in seen:
            return None
        seen.add(current_id)

        raw_node = node_map.get(current_id)
        if raw_node is None:
            return None
        node = resolve_instance_node(raw_node, node_map)
        if not _streaming_auto_range_node_is_eligible(node):
            return None
        parent_ids = graph.parents_of.get(current_id, [])
        if len(parent_ids) != 1:
            return None

        downstream_to_upstream.append(current_id)
        if node.data.nodeType == NodeType.SCENARIO_EXPANDER:
            base_node_id = parent_ids[0]
            scenario_node_id = current_id
            break
        current_id = parent_ids[0]

    if base_node_id is None or scenario_node_id is None:
        return None
    if _upstream_slice_contains_node_type(
        graph,
        base_node_id,
        NodeType.SCENARIO_EXPANDER,
        resolve_node=resolve_instance_node,
    ):
        return None

    projection_plan = _projection_plan_for_auto_range(
        graph,
        node_id,
        required_columns_by_node=required_columns_by_node,
    )
    base_needed = projection_plan.needed_by_node.get(base_node_id)

    chain_node_ids = tuple(reversed(downstream_to_upstream))
    required_output_columns_by_node = {
        chain_id: projection_plan.needed_by_node.get(chain_id)
        for chain_id in chain_node_ids
    }
    return _StreamingAutoRangePlan(
        base_node_id=base_node_id,
        scenario_node_id=scenario_node_id,
        chain_node_ids=chain_node_ids,
        required_output_columns_by_node=required_output_columns_by_node,
        base_required_columns=(frozenset(base_needed) if base_needed is not None else None),
    )


def _apply_artifact_root() -> Path:
    return (Path(tempfile.gettempdir()) / _APPLY_ARTIFACT_ROOT_NAME).resolve()


def _prepare_apply_artifact_root() -> Path:
    root = _apply_artifact_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _validate_apply_result_artifact_handle(handle: dict[str, Any]) -> tuple[Path, Path]:
    """Return validated ``(path, directory)`` for a server-owned apply artifact."""
    if handle.get("kind") != _APPLY_RESULT_HANDLE_KIND:
        raise ValueError("Invalid optimiser apply artifact handle.")
    if handle.get("version") != _ARTIFACT_HANDLE_VERSION:
        raise ValueError("Unsupported optimiser apply artifact handle.")
    if handle.get("format") != "parquet":
        raise ValueError("Unsupported optimiser apply artifact format.")

    raw_directory = handle.get("directory")
    if not isinstance(raw_directory, str) or not raw_directory:
        raise ValueError("Optimiser apply artifact handle has no directory.")
    raw_path = handle.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        raise ValueError("Optimiser apply artifact handle has no path.")
    if "\x00" in raw_directory or "\x00" in raw_path:
        raise ValueError("Optimiser apply artifact handle contains an invalid path.")

    directory_input = Path(raw_directory)
    path_input = Path(raw_path)
    if not directory_input.is_absolute() or not path_input.is_absolute():
        raise ValueError("Optimiser apply artifact handle must use absolute paths.")

    root = _apply_artifact_root()
    directory = directory_input.resolve(strict=directory_input.exists())
    artifact_path = path_input.resolve(strict=path_input.exists())

    if not directory.is_relative_to(root):
        raise ValueError("Optimiser apply artifact directory is outside the artifact root.")
    if directory.parent != root or not directory.name.startswith(_APPLY_ARTIFACT_DIR_PREFIX):
        raise ValueError("Optimiser apply artifact directory is invalid.")
    if artifact_path.parent != directory:
        raise ValueError("Optimiser apply artifact path is outside its directory.")
    if artifact_path.name != _APPLY_RESULT_FILENAME:
        raise ValueError("Optimiser apply artifact path is invalid.")
    return artifact_path, directory


def _persist_apply_result_artifact(solve_result: SolveResultLike) -> dict[str, Any] | None:
    """Persist the large apply/detail dataframe behind an explicit handle."""
    if not hasattr(solve_result, "dataframe"):
        return None

    import polars as pl

    df = solve_result.dataframe
    if not isinstance(df, pl.DataFrame):
        return None

    artifact_dir = Path(
        tempfile.mkdtemp(
            prefix=_APPLY_ARTIFACT_DIR_PREFIX,
            dir=_prepare_apply_artifact_root(),
        ),
    )
    artifact_path = artifact_dir / _APPLY_RESULT_FILENAME
    try:
        df.write_parquet(artifact_path)
        row_count = len(df)
    except BaseException:
        shutil.rmtree(artifact_dir, ignore_errors=True)
        raise

    return {
        "kind": _APPLY_RESULT_HANDLE_KIND,
        "version": _ARTIFACT_HANDLE_VERSION,
        "format": "parquet",
        "path": str(artifact_path),
        "directory": str(artifact_dir),
        "row_count": row_count,
    }


def _cleanup_apply_result_artifact(handle: dict[str, Any]) -> None:
    """Remove a newly-created apply artifact that no job owns."""
    _artifact_path, artifact_dir = _validate_apply_result_artifact_handle(handle)
    if artifact_dir.exists():
        shutil.rmtree(artifact_dir)


def _load_apply_result_artifact(handle: dict[str, Any]) -> Any:
    """Load a persisted optimiser apply dataframe from a validated handle."""
    import polars as pl

    try:
        artifact_path, _artifact_dir = _validate_apply_result_artifact_handle(handle)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if not artifact_path.is_file():
        raise HTTPException(
            status_code=500,
            detail="Optimiser apply artifact is missing. Re-run the solve to regenerate it.",
        )

    try:
        return pl.read_parquet(artifact_path)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Optimiser apply artifact is corrupt. Re-run the solve to regenerate it.",
        ) from exc


register_artifact_cleaner(_APPLY_RESULT_HANDLE_KIND, _cleanup_apply_result_artifact)


def _cleanup_orphan_apply_result_artifact(
    handle: dict[str, Any],
    *,
    job_id: str,
    event: str,
) -> None:
    """Best-effort cleanup for apply artifacts that were never attached to a job."""
    try:
        _cleanup_apply_result_artifact(handle)
    except Exception as cleanup_exc:
        raw_path = handle.get("directory") or handle.get("path") or "<unknown>"
        logger.warning(
            event,
            job_id=job_id,
            path=str(raw_path),
            error=str(cleanup_exc),
            exc_info=True,
        )


def _find_optimiser_node(graph: PipelineGraph, node_id: str) -> GraphNode:
    """Find and validate an optimiser node in the graph."""
    return find_typed_node(graph, node_id, NodeType.OPTIMISER, "optimiser")


def _compute_scenario_value_stats(
    solve_result: SolveResultLike,
) -> tuple[
    dict[str, float] | None,
    dict[str, list[int] | list[float]] | None,
]:
    """Compute scenario value distribution statistics and histogram from solve result."""
    if not hasattr(solve_result, "dataframe"):
        return None, None
    df = solve_result.dataframe
    if "optimal_scenario_value" not in df.columns:
        return None, None

    col = df["optimal_scenario_value"]
    n = len(col)
    if n == 0:
        return None, None
    stats = {
        "mean": float(col.mean()),
        "std": float(col.std()),
        "min": float(col.min()),
        "max": float(col.max()),
        "p5": float(col.quantile(0.05)),
        "p25": float(col.quantile(0.25)),
        "p50": float(col.quantile(0.50)),
        "p75": float(col.quantile(0.75)),
        "p95": float(col.quantile(0.95)),
        "pct_increase": float((col > 1.0).sum() / n) if n else 0.0,
        "pct_decrease": float((col < 1.0).sum() / n) if n else 0.0,
    }

    vals = col.to_numpy()
    counts, edges = np.histogram(vals, bins=_HISTOGRAM_BINS)
    histogram: dict[str, list[int] | list[float]] = {
        "counts": [int(c) for c in counts],
        "edges": [float(e) for e in edges],
    }
    return stats, histogram


def _compute_frontier(
    solver: Any,
    quote_grid: QuoteGrid,
    *,
    mode: str,
    factors_df: pl.DataFrame | None,
    threshold_ranges: dict[str, tuple[float, float]],
    n_points_per_dim: int,
    factor_columns: list[list[str]] | None = None,
    initial_lambdas: dict[str, float] | None = None,
) -> Any:
    """Call the mode-specific frontier API."""
    if mode == "ratebook":
        if factors_df is None:
            raise RuntimeError("Ratebook frontier requires a factors dataframe.")
        frontier_kwargs: dict[str, Any] = {
            "threshold_ranges": threshold_ranges,
            "n_points_per_dim": n_points_per_dim,
        }
        if factor_columns is not None:
            frontier_kwargs["factor_columns"] = factor_columns
        if initial_lambdas is not None:
            frontier_kwargs["initial_lambdas"] = initial_lambdas
        return solver.frontier(
            quote_grid,
            factors_df,
            **frontier_kwargs,
        )
    frontier_kwargs = {
        "threshold_ranges": threshold_ranges,
        "n_points_per_dim": n_points_per_dim,
    }
    if initial_lambdas is not None:
        frontier_kwargs["initial_lambdas"] = initial_lambdas
    return solver.frontier(quote_grid, **frontier_kwargs)


# Public alias preserved for existing in-tree imports; canonical
# implementation lives in ``haute.schemas`` so request-body validators and
# the config-side path share one source of truth.
_normalise_frontier_range = _normalise_frontier_range_pair


def _auto_frontier_ranges_from_config(config: dict[str, Any]) -> dict[str, tuple[float, float]]:
    """Build absolute frontier ranges from optimiser config.

    Prefer per-constraint ``frontier_ranges``.  The legacy scalar
    ``frontier_min`` / ``frontier_max`` pair remains a compatibility path for
    existing single-range configs, but it is no longer treated as a multiplier
    on baseline values.
    """
    constraints = config.get("constraints") or {}
    if not constraints:
        return {}

    configured_ranges = config.get("frontier_ranges")
    if configured_ranges is not None:
        if not isinstance(configured_ranges, dict):
            raise ValueError("frontier_ranges must be an object keyed by constraint name.")
        ranges: dict[str, tuple[float, float]] = {}
        for cname in constraints:
            if cname not in configured_ranges:
                raise ValueError(f"frontier_ranges is missing a range for constraint {cname!r}.")
            ranges[str(cname)] = _normalise_frontier_range(
                configured_ranges[cname],
                field=f"frontier_ranges.{cname}",
            )
        return ranges

    if "frontier_min" not in config or "frontier_max" not in config:
        raise ValueError(
            "frontier_ranges must provide min and max for each constraint. "
            "Legacy frontier_min/frontier_max values are only used when both are "
            "explicitly configured."
        )

    frontier_min = float(config["frontier_min"])
    frontier_max = float(config["frontier_max"])
    if not np.isfinite(frontier_min) or not np.isfinite(frontier_max):
        raise ValueError("frontier_min and frontier_max must be finite values.")
    if frontier_min > frontier_max:
        raise ValueError("frontier_min must be less than or equal to frontier_max.")

    return {str(cname): (frontier_min, frontier_max) for cname in constraints}


class _ScenarioFrontierRangeAccumulator:
    """Accumulate per-quote scenario extrema through disk-backed buckets."""

    def __init__(
        self,
        *,
        quote_id_col: str,
        constraint_cols: list[str],
        partition_count: int,
        parts_root: Path,
    ) -> None:
        import polars as pl

        self.quote_id_col = quote_id_col
        self.constraint_cols = list(constraint_cols)
        self.partition_count = partition_count
        self.parts_root = parts_root
        self.bucket_files: dict[int, list[Path]] = {}
        self.row_count = 0
        self.null_quote_id_count = 0
        self.aliases: dict[str, tuple[str, str]] = {}
        self.aggregate_exprs = []
        self.combine_exprs = []
        self.bucket_total_exprs = []
        for idx, cname in enumerate(self.constraint_cols):
            min_alias = f"__haute_frontier_min_{idx}"
            max_alias = f"__haute_frontier_max_{idx}"
            self.aliases[cname] = (min_alias, max_alias)
            self.aggregate_exprs.extend(
                [
                    pl.col(cname).min().alias(min_alias),
                    pl.col(cname).max().alias(max_alias),
                ]
            )
            self.combine_exprs.extend(
                [
                    pl.col(min_alias).min().alias(min_alias),
                    pl.col(max_alias).max().alias(max_alias),
                ]
            )
            self.bucket_total_exprs.extend(
                [
                    pl.col(min_alias).sum().alias(min_alias),
                    pl.col(max_alias).sum().alias(max_alias),
                ]
            )

    def add_batch(self, batch: pl.DataFrame, *, batch_index: int) -> None:
        import polars as pl

        if batch.height == 0:
            return
        self.row_count += batch.height
        null_count = int(batch[self.quote_id_col].null_count())
        if null_count > 0:
            self.null_quote_id_count += null_count
            return

        partial = batch.group_by(self.quote_id_col).agg(self.aggregate_exprs).with_columns(
            (
                pl.col(self.quote_id_col).hash(seed=0) % self.partition_count
            ).cast(pl.UInt32).alias(_AUTO_RANGE_BUCKET_COLUMN)
        )
        bucket_ids = (
            partial.select(_AUTO_RANGE_BUCKET_COLUMN)
            .unique(maintain_order=False)
            .get_column(_AUTO_RANGE_BUCKET_COLUMN)
            .to_list()
        )
        for raw_bucket in bucket_ids:
            bucket = int(raw_bucket)
            bucket_df = partial.filter(
                pl.col(_AUTO_RANGE_BUCKET_COLUMN) == bucket
            ).drop(_AUTO_RANGE_BUCKET_COLUMN)
            bucket_dir = self.parts_root / f"bucket_{bucket:04d}"
            bucket_dir.mkdir(exist_ok=True)
            part_path = bucket_dir / f"part_{batch_index:08d}.parquet"
            bucket_df.write_parquet(part_path, compression="lz4")
            self.bucket_files.setdefault(bucket, []).append(part_path)

    def finish(
        self,
        *,
        check_cancelled: Callable[[], None] | None = None,
    ) -> dict[str, dict[str, float]]:
        import polars as pl

        if check_cancelled is not None:
            check_cancelled()
        if self.row_count == 0:
            raise ValueError("Unable to estimate frontier ranges from an empty scenario frame.")
        if self.null_quote_id_count > 0:
            detail = (
                f"{_NULL_QUOTE_ID_DETAIL_PREFIX} ({self.null_quote_id_count} rows). "
                "Every row must have a non-null quote_id; check upstream filters and joins."
            )
            raise ValueError(detail)

        range_totals = {
            cname: {"min": 0.0, "max": 0.0}
            for cname in self.constraint_cols
        }
        for paths in self.bucket_files.values():
            if check_cancelled is not None:
                check_cancelled()
            bucket_totals = (
                pl.scan_parquet([str(path) for path in paths])
                .group_by(self.quote_id_col)
                .agg(self.combine_exprs)
                .select(self.bucket_total_exprs)
                .collect(engine="streaming")
            )
            if bucket_totals.height != 1:
                raise ValueError("Unable to estimate frontier ranges from a scenario bucket.")
            row = bucket_totals.row(0, named=True)
            for cname, (min_alias, max_alias) in self.aliases.items():
                range_totals[cname]["min"] += float(row[min_alias])
                range_totals[cname]["max"] += float(row[max_alias])

        ranges: dict[str, dict[str, float]] = {}
        for cname, values in range_totals.items():
            min_value = values["min"]
            max_value = values["max"]
            if not np.isfinite(min_value) or not np.isfinite(max_value):
                raise ValueError(f"Estimated frontier range for {cname!r} is not finite.")
            if min_value > max_value:
                raise ValueError(f"Estimated frontier range for {cname!r} is invalid.")
            ranges[cname] = {"min": min_value, "max": max_value}
        return ranges


def _estimate_scenario_frontier_ranges(
    scored_lf: Any,
    *,
    quote_id_col: str,
    constraint_cols: list[str],
    chunk_size: int = _DEFAULT_AUTO_RANGE_CHUNK_SIZE,
    partition_count: int = _DEFAULT_AUTO_RANGE_PARTITIONS,
    check_cancelled: Callable[[], None] | None = None,
) -> dict[str, dict[str, float]]:
    """Return exact online achievable min/max totals from the scenario frame.

    For each constraint, each quote can independently choose the scenario that
    minimises or maximises that constraint total.  The input is read in bounded
    batches, reduced to per-batch quote extrema, then hash-partitioned to
    temporary parquet files so quotes split across read batches are recombined
    without one global per-quote aggregate table.
    """
    import polars as pl

    if not constraint_cols:
        return {}

    if check_cancelled is not None:
        check_cancelled()
    chunk_size = _positive_int(chunk_size, field="chunk_size")
    partition_count = _positive_int(partition_count, field="partition_count")
    selected_lf = scored_lf.select(
        [
            pl.col(quote_id_col).cast(pl.String).alias(quote_id_col),
            *[pl.col(cname) for cname in constraint_cols],
        ]
    )

    with tempfile.TemporaryDirectory(prefix="haute_frontier_range_parts_") as raw_dir:
        accumulator = _ScenarioFrontierRangeAccumulator(
            quote_id_col=quote_id_col,
            constraint_cols=constraint_cols,
            partition_count=partition_count,
            parts_root=Path(raw_dir),
        )
        for batch_index, batch in enumerate(
            selected_lf.collect_batches(
                chunk_size=chunk_size,
                maintain_order=False,
                engine="streaming",
            )
        ):
            if check_cancelled is not None:
                check_cancelled()
            accumulator.add_batch(batch, batch_index=batch_index)
        return accumulator.finish(check_cancelled=check_cancelled)


_RATEBOOK_FACTOR_LEVEL_SEPARATOR = "\x1f"
_RATEBOOK_FACTOR_LEVEL_ORDER_KEY = "factor_level_order"


def _ratebook_factor_table_name(columns: list[str]) -> str:
    return ":".join(columns)


def _ratebook_factor_level_key(values: list[Any]) -> str:
    if any(value is None for value in values):
        raise ValueError("Ratebook factor counts cannot be computed with null factor levels.")
    return _RATEBOOK_FACTOR_LEVEL_SEPARATOR.join(str(value) for value in values)


def _append_unique_factor_level(levels: list[str], seen: set[str], value: object) -> None:
    if value is None or value == "":
        return
    level = str(value)
    if level in seen:
        return
    seen.add(level)
    levels.append(level)


def _banding_rule_output_level(rule: dict[str, Any]) -> object:
    """Return the rule's output level (``assignment`` or ``label``).

    A banding rule emitted by the parser always carries one of these keys; the
    ``None`` return is reserved for legacy fixtures with hand-written rules
    that omit both, in which case the level is dropped from the order.
    """
    if "assignment" in rule:
        return rule.get("assignment")
    if "label" in rule:
        return rule.get("label")
    return None


def _find_node_by_id(graph: PipelineGraph, node_id: str) -> GraphNode | None:
    """Return the graph node with the given id, or ``None`` if absent."""
    return next((node for node in graph.nodes if node.id == node_id), None)


def _ratebook_factor_level_order(
    graph: PipelineGraph,
    config: dict[str, Any],
) -> dict[str, list[str]]:
    """Extract factor-level display order from the configured banding source."""
    banding_source_id = config.get("banding_source")
    if not isinstance(banding_source_id, str) or not banding_source_id:
        return {}

    banding_node = _find_node_by_id(graph, banding_source_id)
    if banding_node is None or banding_node.data.nodeType != NodeType.BANDING:
        return {}

    order: dict[str, list[str]] = {}
    for factor in normalise_banding_factors(banding_node.data.config):
        output_column = factor.get("outputColumn")
        if not isinstance(output_column, str) or not output_column:
            continue

        levels: list[str] = []
        seen: set[str] = set()
        rules = factor.get("rules")
        if isinstance(rules, list):
            for rule in rules:
                if isinstance(rule, dict):
                    _append_unique_factor_level(levels, seen, _banding_rule_output_level(rule))
        _append_unique_factor_level(levels, seen, factor.get("default"))
        if levels:
            order[output_column] = levels
    return order


def _ratebook_factor_table_level_order(
    table_name: str,
    factor_level_order: dict[str, list[str]],
) -> list[str]:
    direct_order = factor_level_order.get(table_name)
    if direct_order is not None:
        return direct_order

    columns = table_name.split(":")
    if len(columns) <= 1:
        return []

    component_orders = [factor_level_order.get(column) for column in columns]
    if any(order is None for order in component_orders):
        return []
    populated_orders = [order for order in component_orders if order is not None]
    return [_RATEBOOK_FACTOR_LEVEL_SEPARATOR.join(values) for values in product(*populated_orders)]


def _ratebook_factor_table_position(
    table_name: str,
    factor_level_order: dict[str, list[str]],
) -> tuple[int, ...] | None:
    factor_positions = {name: index for index, name in enumerate(factor_level_order)}
    direct_position = factor_positions.get(table_name)
    if direct_position is not None:
        return (direct_position,)

    columns = table_name.split(":")
    if len(columns) <= 1:
        return None
    positions = [factor_positions.get(column) for column in columns]
    if any(position is None for position in positions):
        return None
    return tuple(position for position in positions if position is not None)


def _ratebook_factor_table_sort_key(
    index_and_item: tuple[int, tuple[Any, Any]],
    factor_level_order: dict[str, list[str]],
) -> tuple[int, tuple[int, ...]]:
    original_index, (name, _table) = index_and_item
    fallback = (1, (original_index,))
    if not isinstance(name, str):
        return fallback
    position = _ratebook_factor_table_position(name, factor_level_order)
    return (0, position) if position is not None else fallback


def _ratebook_factor_level_counts(
    factors_df: pl.DataFrame | None,
    factor_columns: list[list[str]] | None,
) -> dict[str, dict[str, int]]:
    """Count quote exposure for each ratebook factor level.

    The table and level keys mirror price-contour's factor table output:
    single-column groups use the column name, composite groups join column
    names with ":" and level values with the unit separator.
    """
    import polars as pl

    if factors_df is None:
        return {}

    counts: dict[str, dict[str, int]] = {}
    for columns in factor_columns or []:
        if not columns:
            continue
        missing = [column for column in columns if column not in factors_df.columns]
        if missing:
            raise ValueError(
                "Ratebook factor count columns are missing from aligned factors dataframe: "
                f"{missing}"
            )
        table_name = _ratebook_factor_table_name(columns)
        count_rows = factors_df.group_by(columns).agg(pl.len().alias("quote_count")).to_dicts()
        counts[table_name] = {
            _ratebook_factor_level_key([row[column] for column in columns]): int(row["quote_count"])
            for row in count_rows
        }
    return counts


def _sort_ratebook_factor_tables(
    factor_tables: dict[Any, Any],
    factor_level_order: dict[str, list[str]],
) -> list[tuple[Any, Any]]:
    """Order factor tables by the configured banding-rule order.

    Tables not present in ``factor_level_order`` retain their original
    insertion order behind the configured ones (fallback bucket ``1``).
    """
    return [
        item
        for _index, item in sorted(
            enumerate(factor_tables.items()),
            key=lambda item: _ratebook_factor_table_sort_key(item, factor_level_order),
        )
    ]


def _serialise_ratebook_factor_table_rows(
    name: str,
    table: dict[Any, Any],
    level_counts: dict[str, int],
    factor_level_order: dict[str, list[str]],
) -> list[dict[str, Any]]:
    """Serialise one factor table's rows, ordered by configured level order.

    Levels not present in the configured order fall through to insertion order
    behind the ordered ones, matching the table-level ordering convention.
    """
    configured_level_order = _ratebook_factor_table_level_order(name, factor_level_order)
    level_positions = {level: index for index, level in enumerate(configured_level_order)}
    ordered_rows: list[tuple[tuple[int, int], dict[str, Any]]] = []
    for original_index, (level, scenario_value) in enumerate(table.items()):
        level_key = str(level)
        quote_count = level_counts.get(level_key)
        if quote_count is None:
            raise ValueError(
                f"Ratebook factor counts missing for level {level_key!r} in factor table {name!r}."
            )
        scenario_float = float(scenario_value)
        if not np.isfinite(scenario_float):
            raise ValueError(f"Ratebook factor table {name!r} contains a non-finite rate.")
        sort_key = (
            (0, level_positions[level_key]) if level_key in level_positions else (1, original_index)
        )
        ordered_rows.append(
            (
                sort_key,
                {
                    "__factor_group__": level,
                    "optimal_scenario_value": scenario_float,
                    "quote_count": int(quote_count),
                },
            )
        )
    return [row for _sort_key, row in sorted(ordered_rows, key=lambda item: item[0])]


def _serialise_ratebook_factor_tables(
    factor_tables: Any,
    factor_level_counts: dict[str, dict[str, int]],
    factor_level_order: dict[str, list[str]],
) -> dict[str, list[dict[str, Any]]]:
    """Serialise ratebook factor tables for the API, ordered by banding rules."""
    if not isinstance(factor_tables, dict):
        raise ValueError("Ratebook factor tables are invalid")

    serialised: dict[str, list[dict[str, Any]]] = {}
    for name, table in _sort_ratebook_factor_tables(factor_tables, factor_level_order):
        if not isinstance(name, str) or not isinstance(table, dict):
            raise ValueError("Ratebook factor tables are invalid")
        level_counts = factor_level_counts.get(name)
        if level_counts is None:
            raise ValueError(f"Ratebook factor counts missing for factor table {name!r}.")
        serialised[name] = _serialise_ratebook_factor_table_rows(
            name, table, level_counts, factor_level_order
        )
    return serialised


def _compute_ratebook_factor_level_order(
    graph: PipelineGraph,
    config: dict[str, Any],
    mode: str,
) -> dict[str, list[str]]:
    """Return the banding-rule level order for ratebook mode, ``{}`` otherwise.

    Computed once at solve start from the graph and threaded through to the
    background solver as an explicit parameter — never injected into the
    user-facing config dict.
    """
    if mode != "ratebook":
        return {}
    return _ratebook_factor_level_order(graph, config)


def _finalize_solve_result(
    solve_result: SolveResultLike,
    *,
    mode: str,
    solver: Any,
    quote_grid: QuoteGrid,
    store: JobStore,
    job_id: str,
    elapsed: float,
    extra_fields: dict[str, Any] | None = None,
    extra_job_fields: dict[str, Any] | None = None,
    factors_df: pl.DataFrame | None = None,
    factor_columns: list[list[str]] | None = None,
) -> None:
    """Build the result dict and update the job with the solve outcome.

    Shared by ``_solve_online`` and ``_solve_ratebook`` to avoid duplicating
    the ~30 lines of result-dict construction, convergence warning, and
    store update boilerplate.

    Parameters
    ----------
    solve_result:
        The solver result object (online or ratebook).
    mode:
        ``"online"`` or ``"ratebook"``.
    solver:
        The solver instance (stored on the job for later use).
    quote_grid:
        The QuoteGrid (stored on the job for apply/frontier operations).
    store:
        The job store — updates are applied atomically via dict replacement.
    job_id:
        The job ID to update in the store.
    elapsed:
        Wall-clock seconds since the solve started.
    extra_fields:
        Mode-specific keys to merge into the result dict (e.g.
        ``iterations``, ``factor_tables``).
    """
    scenario_value_stats, scenario_value_histogram = _compute_scenario_value_stats(solve_result)

    result_dict: dict[str, Any] = {
        "mode": mode,
        "total_objective": solve_result.total_objective,
        "baseline_objective": solve_result.baseline_objective,
        "constraints": solve_result.total_constraints,
        "baseline_constraints": solve_result.baseline_constraints,
        "lambdas": solve_result.lambdas,
        "converged": solve_result.converged,
        "scenario_value_stats": scenario_value_stats,
        "scenario_value_histogram": scenario_value_histogram,
    }
    if extra_fields:
        result_dict.update(extra_fields)
    if not solve_result.converged:
        result_dict["warning"] = (
            "Solver did not converge. Consider increasing max_iter or relaxing tolerance."
        )

    # ── Compute efficient frontier when explicitly requested (non-fatal) ────
    frontier_data = None
    frontier_error = None
    # M6: Use direct dict access to avoid _evict_stale() from background thread
    job_snapshot = store.jobs.get(job_id, {})
    config = job_snapshot.get("config", {})
    constraints = config.get("constraints")
    if constraints and config.get("frontier_enabled") is True:
        try:
            frontier_steps = config.get("frontier_steps", 15)
            ranges = _auto_frontier_ranges_from_config(config)
            if ranges:
                progress_job = store.atomic_update(
                    job_id,
                    {
                        "message": "Computing efficient frontier",
                        "progress": 0.8,
                        "elapsed_seconds": _job_elapsed_seconds(job_snapshot, elapsed),
                    },
                    expected_status="running",
                )
                if progress_job is None:
                    logger.info(
                        "frontier_start_skipped",
                        job_id=job_id,
                        expected_status="running",
                    )
                    return
                job_snapshot = progress_job
                frontier_result = _compute_frontier(
                    solver,
                    quote_grid,
                    mode=mode,
                    factors_df=factors_df,
                    factor_columns=factor_columns,
                    threshold_ranges=ranges,
                    n_points_per_dim=frontier_steps,
                    initial_lambdas=solve_result.lambdas,
                )
                frontier_data = limited_frontier_payload(
                    frontier_result.points,
                    constraint_names=list(ranges.keys()),
                )
                logger.info(
                    "frontier_computed",
                    n_points=frontier_data["n_points"],
                    job_id=job_id,
                )
        except Exception as exc:
            frontier_error = f"Frontier unavailable: {exc}"
            logger.warning(
                "frontier_computation_failed",
                error=str(exc),
                job_id=job_id,
                exc_info=True,
            )

    result_dict["frontier"] = frontier_data
    if frontier_error is not None:
        result_dict["frontier_error"] = frontier_error
    artifact_handles: dict[str, Any] = {}
    apply_result_handle = _persist_apply_result_artifact(solve_result)
    if apply_result_handle is not None:
        artifact_handles[_APPLY_RESULT_HANDLE_KEY] = apply_result_handle

    # P7: Atomic update — replace the entire dict to avoid races with
    # status-polling reads on the main thread.
    # L5: result_dict["frontier"] is the frontend-serialised frontier payload
    # (consumed by OptimiserStatusResponse).  "frontier_data" is a top-level
    # job key used by internal endpoints (e.g. /frontier/select) to look up
    # raw frontier points without going through the result dict.
    updated_job = store.atomic_update(
        job_id,
        {
            "status": "completed",
            "progress": 1.0,
            "message": "Completed",
            "elapsed_seconds": _job_elapsed_seconds(
                store.jobs.get(job_id, job_snapshot),
                elapsed,
            ),
            "solver": solver,
            "solve_result": solve_result,
            "quote_grid": quote_grid,
            "factors_df": factors_df,
            "factor_columns_valid": factor_columns,
            "result": result_dict,
            "base_result": dict(result_dict),
            "frontier_data": frontier_data,
            "artifact_handles": artifact_handles,
            **(extra_job_fields or {}),
        },
        expected_status="running",
    )
    if updated_job is None:
        logger.info("solve_completion_skipped", job_id=job_id, expected_status="running")
        if apply_result_handle is not None:
            _cleanup_orphan_apply_result_artifact(
                apply_result_handle,
                job_id=job_id,
                event="solve_completion_orphan_apply_artifact_cleanup_failed",
            )
        return
    if (
        apply_result_handle is not None
        and updated_job.get("artifact_handles") is not artifact_handles
    ):
        _cleanup_orphan_apply_result_artifact(
            apply_result_handle,
            job_id=job_id,
            event="solve_completion_orphan_apply_artifact_cleanup_failed",
        )

    # The job store keeps these heavy runtime objects for its short
    # heavy-object retention window, then slims the completed job down to
    # API-facing summaries/metadata while preserving the 24h status record.


def _solve_online(
    quote_grid: QuoteGrid,
    config: dict[str, Any],
    store: JobStore,
    job_id: str,
    start_time: float,
) -> None:
    """Run the online optimiser solver on a pre-built QuoteGrid."""
    from price_contour import OnlineOptimiser

    solver = OnlineOptimiser(
        objective=config["objective"],
        constraints=config["constraints"] or None,
        max_iter=config.get("max_iter", _DEFAULT_MAX_ITER),
        tolerance=config.get("tolerance", _DEFAULT_TOLERANCE),
        record_history=config.get("record_history", False),
    )
    solve_result: OnlineSolveResultLike = solver.solve(quote_grid)
    elapsed = time.monotonic() - start_time
    logger.info(
        "solve_completed",
        mode="online",
        elapsed=f"{elapsed:.2f}s",
        converged=solve_result.converged,
    )

    _finalize_solve_result(
        solve_result,
        mode="online",
        solver=solver,
        quote_grid=solve_result.grid,
        factors_df=None,
        store=store,
        job_id=job_id,
        elapsed=elapsed,
        extra_fields={
            "iterations": solve_result.iterations,
            "n_quotes": solve_result.n_quotes,
            "n_steps": solve_result.n_steps,
            "history": solve_result.history if config.get("record_history") else None,
        },
    )


def _solve_ratebook(
    quote_grid: QuoteGrid,
    config: dict[str, Any],
    factors_df: pl.DataFrame | None,
    store: JobStore,
    job_id: str,
    start_time: float,
    *,
    factor_level_order: dict[str, list[str]] | None = None,
) -> None:
    """Run the ratebook optimiser solver on a pre-built QuoteGrid."""
    import polars as pl
    from price_contour import RatebookOptimiser

    if factors_df is None:
        raise RuntimeError(
            "Ratebook mode requires a banding source. "
            "Select a banding node in the Rating Factor Source dropdown."
        )

    constraints = config["constraints"]
    qid_col = config.get("quote_id", "quote_id")

    raw_factor_columns = config.get("factor_columns", [])
    available_cols = set(factors_df.columns)
    factor_columns_valid = [
        group for group in raw_factor_columns if all(c in available_cols for c in group)
    ]
    if not factor_columns_valid:
        missing = [c for group in raw_factor_columns for c in group if c not in available_cols]
        raise RuntimeError(
            f"No valid factor groups found. Missing columns in banding source: {missing}. "
            f"Available columns: {sorted(available_cols)}"
        )

    factor_cols_flat = list(dict.fromkeys(c for group in factor_columns_valid for c in group))
    avail = factors_df.columns
    if qid_col in avail:
        keep = [qid_col] + [c for c in factor_cols_flat if c in avail]
        factors_df = factors_df.select(keep)
        if qid_col != "quote_id":
            factors_df = factors_df.rename({qid_col: "quote_id"})
    elif "quote_id" in avail:
        keep = ["quote_id"] + [c for c in factor_cols_flat if c in avail]
        factors_df = factors_df.select(keep)
    else:
        raise RuntimeError(
            f"Ratebook banding source must include quote id column '{qid_col}' "
            "or a 'quote_id' column."
        )

    factors_df = factors_df.with_columns(pl.col("quote_id").cast(pl.Utf8))
    factors_df = factors_df.unique(subset=["quote_id"])

    quote_order = pl.DataFrame({"quote_id": quote_grid.quote_ids})
    quote_order = quote_order.unique(maintain_order=True)
    factors_df = quote_order.join(factors_df, on="quote_id", how="left")
    factors_df = factors_df.drop("quote_id")
    null_counts = factors_df.select(
        [pl.col(c).null_count().alias(c) for c in factor_cols_flat],
    ).row(0, named=True)
    null_factor_counts = {name: count for name, count in null_counts.items() if int(count) > 0}
    if null_factor_counts:
        formatted_counts = ", ".join(
            f"{name} ({count} {'row' if count == 1 else 'rows'})"
            for name, count in null_factor_counts.items()
        )
        raise ValueError(
            "Ratebook factor columns contain null values after aligning to quote grid: "
            f"{formatted_counts}. Configure non-null banding defaults, remove the "
            "affected factor columns, or ensure every quote_id has banding values."
        )

    solver = RatebookOptimiser(
        objective=config["objective"],
        constraints=constraints,
        factor_columns=factor_columns_valid,
        max_iter=config.get("max_iter", _DEFAULT_MAX_ITER),
        max_cd_iterations=config.get("max_cd_iterations", _DEFAULT_MAX_CD_ITERATIONS),
        cd_tolerance=config.get("cd_tolerance", _DEFAULT_CD_TOLERANCE),
        tolerance=config.get("tolerance", _DEFAULT_TOLERANCE),
    )

    solve_result: RatebookSolveResultLike = solver.solve(quote_grid, factors_df)
    elapsed = time.monotonic() - start_time
    converged = solve_result.converged
    logger.info("solve_completed", mode="ratebook", elapsed=f"{elapsed:.2f}s", converged=converged)

    factor_level_counts = _ratebook_factor_level_counts(factors_df, factor_columns_valid)
    resolved_level_order = factor_level_order or {}
    factor_tables_serialised = _serialise_ratebook_factor_tables(
        solve_result.factor_tables,
        factor_level_counts,
        resolved_level_order,
    )

    _finalize_solve_result(
        solve_result,
        mode="ratebook",
        solver=solver,
        quote_grid=quote_grid,
        factors_df=factors_df,
        factor_columns=factor_columns_valid,
        store=store,
        job_id=job_id,
        elapsed=elapsed,
        extra_fields={
            "cd_iterations": solve_result.cd_iterations,
            "factor_tables": factor_tables_serialised,
            "clamp_rate": getattr(solve_result, "clamp_rate", None),
            "history": None,
        },
        extra_job_fields={
            "factor_level_counts": factor_level_counts,
            _RATEBOOK_FACTOR_LEVEL_ORDER_KEY: resolved_level_order,
        },
    )


class OptimiserSolveService:
    """Orchestrates the full optimisation solve lifecycle.

    Parameters
    ----------
    store:
        The in-memory job store used to track optimisation jobs.
    """

    def __init__(self, store: JobStore) -> None:
        self._store = store
        self._start_lock = threading.Lock()
        self._auto_range_jobs = CancellableJobRegistry()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def start(self, body: OptimiserSolveRequest) -> OptimiserSolveResponse:
        """Validate config, execute pipeline, build grid, and launch solver.

        Returns an ``OptimiserSolveResponse`` with status ``"started"``.
        Raises ``HTTPException`` on validation or pipeline failures.
        """
        node = _find_optimiser_node(body.graph, body.node_id)
        config = dict(node.data.config)

        mode = self._validate_config(config)
        factor_level_order = _compute_ratebook_factor_level_order(body.graph, config, mode)
        required_columns_by_node = _optimiser_solve_required_columns_by_node(
            body.graph,
            body.node_id,
            config,
        )

        with self._start_lock:
            self._check_no_concurrent_jobs()
            job_id = self._store.create_job(
                {
                    "status": "running",
                    _JOB_TYPE_KEY: _SOLVE_JOB_TYPE,
                    "progress": 0.0,
                    "message": "Starting",
                    "config": dict(config),
                    "node_label": node.data.label,
                }
            )
        logger.info("solve_started", node_id=body.node_id, mode=mode, job_id=job_id)

        # ``TemporaryDirectory`` removes the checkpoint dir even on signal/
        # crash; an interrupted long solve will not leak GBs of staging data.
        with tempfile.TemporaryDirectory(prefix="haute_opt_") as raw_dir:
            checkpoint_dir = Path(raw_dir)
            try:
                lazy_outputs = self._execute_pipeline(
                    body,
                    job_id,
                    checkpoint_dir,
                    required_columns_by_node=required_columns_by_node,
                )
                source_lf = self._resolve_data_source(
                    lazy_outputs,
                    config,
                    body.node_id,
                    job_id,
                )
                constraint_cols, scored_lf = self._validate_and_project(
                    source_lf,
                    config,
                    job_id,
                )
                factors_df = self._extract_factors(lazy_outputs, config, mode)
                del lazy_outputs
                gc.collect()

                quote_grid = self._build_grid(
                    scored_lf,
                    constraint_cols,
                    config,
                    body.node_id,
                    job_id,
                )
            except HTTPException as exc:
                self._store.atomic_update(
                    job_id,
                    {"status": "error", "message": str(exc.detail)},
                    expected_status="running",
                )
                raise
            except Exception as exc:
                detail = f"Optimiser setup failed: {exc}"
                logger.error(
                    "optimiser_setup_failed",
                    error=str(exc),
                    node_id=body.node_id,
                    job_id=job_id,
                    exc_info=True,
                )
                self._store.atomic_update(
                    job_id,
                    {"status": "error", "message": detail},
                    expected_status="running",
                )
                raise HTTPException(
                    status_code=500,
                    detail="Optimiser setup failed. Check the server logs for details.",
                ) from exc
        self._launch_background(
            job_id,
            body.node_id,
            config,
            mode,
            quote_grid,
            factors_df,
            factor_level_order=factor_level_order,
        )
        return OptimiserSolveResponse(status="started", job_id=job_id)

    def estimate_frontier_auto_range(
        self,
        body: OptimiserFrontierAutoRangeRequest,
    ) -> OptimiserFrontierAutoRangeResponse:
        """Estimate absolute frontier ranges from the scenario dataframe.

        The online optimiser can independently choose one scenario per quote,
        so summing per-quote extrema gives the exact achievable envelope for
        each constraint.  The calculation operates on the projected lazy frame
        and returns only tiny metadata.
        """
        prepared = self._prepare_frontier_auto_range(body)
        node = prepared["node"]
        config = prepared["config"]
        job_id = self._store.create_job(
            {
                "status": "running",
                _JOB_TYPE_KEY: _FRONTIER_AUTO_RANGE_JOB_TYPE,
                "progress": 0.0,
                "message": "Estimating frontier range",
                "config": dict(config),
                "node_label": node.data.label,
            }
        )
        try:
            return self._run_frontier_auto_range_job(body, job_id, **prepared)
        finally:
            self._store.delete_job(job_id)

    def start_frontier_auto_range(
        self,
        body: OptimiserFrontierAutoRangeRequest,
    ) -> OptimiserFrontierAutoRangeStartResponse:
        """Start auto-range in a background thread and return a pollable job."""
        prepared = self._prepare_frontier_auto_range(body)
        node = prepared["node"]
        config = prepared["config"]
        job_key = self._frontier_auto_range_job_key(body)
        with self._start_lock:
            job_id = self._store.create_job(
                {
                    "status": "running",
                    _JOB_TYPE_KEY: _FRONTIER_AUTO_RANGE_JOB_TYPE,
                    "progress": 0.0,
                    "message": "Estimating frontier range",
                    "config": dict(config),
                    "node_label": node.data.label,
                }
            )
            _token, previous_job_id = self._auto_range_jobs.register_latest(job_key, job_id)
            if previous_job_id is not None:
                self._stop_frontier_auto_range_job(
                    previous_job_id,
                    status=_FRONTIER_AUTO_RANGE_SUPERSEDED_STATUS,
                    message="Superseded by a newer auto-range request.",
                )
        self._launch_frontier_auto_range_background(body, job_id, **prepared)
        return OptimiserFrontierAutoRangeStartResponse(status="started", job_id=job_id)

    def frontier_auto_range_status(
        self,
        job_id: str,
    ) -> OptimiserFrontierAutoRangeStatusResponse:
        """Return status for a background auto-range job."""
        job = self._store.require_job(job_id)
        if job.get(_JOB_TYPE_KEY) != _FRONTIER_AUTO_RANGE_JOB_TYPE:
            raise HTTPException(status_code=404, detail=f"Auto-range job '{job_id}' not found")

        if job.get("status") == "running":
            start = job.get("start_time")
            timeout = job.get("timeout", _DEFAULT_AUTO_RANGE_TIMEOUT)
            if start and (time.monotonic() - start) > timeout:
                self._auto_range_jobs.cancel(job_id)
                updated_job = self._store.atomic_update(
                    job_id,
                    {
                        "status": "error",
                        "message": (
                            f"Auto range timed out after {timeout}s. "
                            "Reduce the input size or increase HAUTE_AUTO_RANGE_TIMEOUT."
                        ),
                        "elapsed_seconds": time.monotonic() - start,
                    },
                    expected_status="running",
                )
                job = updated_job if updated_job is not None else self._store.require_job(job_id)
                self._auto_range_jobs.release(job_id)

        return self._frontier_auto_range_status_response(job)

    def cancel_frontier_auto_range(
        self,
        job_id: str,
    ) -> OptimiserFrontierAutoRangeStatusResponse:
        """Cancel a running background auto-range job."""
        job = self._stop_frontier_auto_range_job(
            job_id,
            status=_FRONTIER_AUTO_RANGE_CANCELLED_STATUS,
            message="Cancelled",
        )
        return self._frontier_auto_range_status_response(job)

    def _frontier_auto_range_status_response(
        self,
        job: dict[str, Any],
    ) -> OptimiserFrontierAutoRangeStatusResponse:
        result = None
        if job.get("status") == "completed" and job.get("result") is not None:
            result = OptimiserFrontierAutoRangeResponse.model_validate(job["result"])
        elapsed_seconds = job.get("elapsed_seconds", 0.0)
        if job.get("status") == "running":
            elapsed_seconds = _job_elapsed_seconds(job, elapsed_seconds)
        return OptimiserFrontierAutoRangeStatusResponse(
            status=job.get("status", "running"),
            progress=job.get("progress", 0.0),
            message=job.get("message", ""),
            elapsed_seconds=elapsed_seconds,
            result=result,
        )

    @staticmethod
    def _frontier_auto_range_job_key(
        body: OptimiserFrontierAutoRangeRequest,
    ) -> tuple[str, str, str]:
        return (_FRONTIER_AUTO_RANGE_JOB_TYPE, body.node_id, graph_fingerprint(body.graph))

    def _stop_frontier_auto_range_job(
        self,
        job_id: str,
        *,
        status: str,
        message: str,
    ) -> dict[str, Any]:
        if status not in _FRONTIER_AUTO_RANGE_TERMINAL_STATUSES:
            raise ValueError(f"Unsupported auto-range stop status: {status!r}")
        job = self._store.require_job(job_id)
        if job.get(_JOB_TYPE_KEY) != _FRONTIER_AUTO_RANGE_JOB_TYPE:
            raise HTTPException(status_code=404, detail=f"Auto-range job '{job_id}' not found")
        if job.get("status") in _FRONTIER_AUTO_RANGE_TERMINAL_STATUSES:
            return job

        self._auto_range_jobs.cancel(job_id)
        updated_job = self._store.atomic_update(
            job_id,
            {
                "status": status,
                "message": message,
                "elapsed_seconds": _job_elapsed_seconds(job),
            },
            expected_status="running",
        )
        self._auto_range_jobs.release(job_id)
        return updated_job if updated_job is not None else self._store.require_job(job_id)

    def _raise_if_frontier_auto_range_stopped(self, job_id: str) -> None:
        job = self._store.require_job(job_id)
        status = str(job.get("status", "running"))
        if status != "running":
            raise BackgroundJobStoppedError(job_id, status)
        if self._auto_range_jobs.is_cancelled(job_id):
            raise BackgroundJobStoppedError(job_id, _FRONTIER_AUTO_RANGE_CANCELLED_STATUS)

    def _prepare_frontier_auto_range(
        self,
        body: OptimiserFrontierAutoRangeRequest,
    ) -> dict[str, Any]:
        node = _find_optimiser_node(body.graph, body.node_id)
        config = dict(node.data.config)
        mode = self._validate_config(config)
        try:
            chunk_size = _auto_range_chunk_size_from_config(config)
            partition_count = _auto_range_partition_count_from_config(config)
            timeout = _auto_range_timeout_from_config(config)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        required_columns_by_node = _auto_range_required_columns_by_node(
            body.graph,
            body.node_id,
            config,
            mode=mode,
        )
        streaming_plan = _build_streaming_auto_range_plan(
            body.graph,
            body.node_id,
            config,
            mode=mode,
            required_columns_by_node=required_columns_by_node,
        )
        return {
            "node": node,
            "config": config,
            "mode": mode,
            "chunk_size": chunk_size,
            "partition_count": partition_count,
            "timeout": timeout,
            "required_columns_by_node": required_columns_by_node,
            "streaming_plan": streaming_plan,
        }

    def _run_frontier_auto_range_job(
        self,
        body: OptimiserFrontierAutoRangeRequest,
        job_id: str,
        *,
        node: GraphNode,
        config: dict[str, Any],
        mode: str,
        chunk_size: int,
        partition_count: int,
        timeout: int,
        required_columns_by_node: Mapping[str, Iterable[str]],
        streaming_plan: _StreamingAutoRangePlan | None,
    ) -> OptimiserFrontierAutoRangeResponse:
        del node, mode, timeout
        self._raise_if_frontier_auto_range_stopped(job_id)
        if streaming_plan is not None:
            return self._run_streaming_frontier_auto_range_job(
                body,
                job_id,
                config=config,
                chunk_size=chunk_size,
                partition_count=partition_count,
                streaming_plan=streaming_plan,
            )

        # ``TemporaryDirectory`` ensures the checkpoint dir is removed even
        # on signal/abort, where ``mkdtemp`` + ``rmtree`` in finally would leak.
        try:
            with tempfile.TemporaryDirectory(prefix="haute_frontier_range_") as raw_dir:
                checkpoint_dir = Path(raw_dir)
                self._store.atomic_update(
                    job_id,
                    {
                        "message": "Executing pipeline",
                        "progress": 0.05,
                        "elapsed_seconds": _job_elapsed_seconds(self._store.jobs[job_id]),
                    },
                    expected_status="running",
                )
                self._raise_if_frontier_auto_range_stopped(job_id)
                lazy_outputs = self._execute_pipeline(
                    body,
                    job_id,
                    checkpoint_dir,
                    required_columns_by_node=required_columns_by_node,
                )
                self._raise_if_frontier_auto_range_stopped(job_id)
                self._store.atomic_update(
                    job_id,
                    {
                        "message": "Projecting auto-range columns",
                        "progress": 0.65,
                        "elapsed_seconds": _job_elapsed_seconds(self._store.jobs[job_id]),
                    },
                    expected_status="running",
                )
                self._raise_if_frontier_auto_range_stopped(job_id)
                source_lf = self._resolve_data_source(
                    lazy_outputs,
                    config,
                    body.node_id,
                    job_id,
                )
                constraint_cols, scored_lf = self._validate_and_project_auto_range(
                    source_lf,
                    config,
                    job_id,
                )
                self._raise_if_frontier_auto_range_stopped(job_id)
                del lazy_outputs
                gc.collect()

                self._store.atomic_update(
                    job_id,
                    {
                        "message": "Aggregating scenario envelope",
                        "progress": 0.75,
                        "elapsed_seconds": _job_elapsed_seconds(self._store.jobs[job_id]),
                    },
                    expected_status="running",
                )
                self._raise_if_frontier_auto_range_stopped(job_id)
                ranges = _estimate_scenario_frontier_ranges(
                    scored_lf,
                    quote_id_col=str(config.get("quote_id", "quote_id")),
                    constraint_cols=constraint_cols,
                    chunk_size=chunk_size,
                    partition_count=partition_count,
                    check_cancelled=lambda: self._raise_if_frontier_auto_range_stopped(job_id),
                )
                self._raise_if_frontier_auto_range_stopped(job_id)
                response_ranges = {
                    name: OptimiserFrontierRange(min=value["min"], max=value["max"])
                    for name, value in ranges.items()
                }
                response = OptimiserFrontierAutoRangeResponse(
                    status="ok",
                    ranges=response_ranges,
                    warning=None,
                )
                self._store.atomic_update(
                    job_id,
                    {
                        "status": "completed",
                        "progress": 1.0,
                        "message": "Completed",
                        "elapsed_seconds": _job_elapsed_seconds(self._store.jobs[job_id]),
                        "result": response.model_dump(),
                    },
                    expected_status="running",
                )
                return response
        except BackgroundJobStoppedError:
            raise
        except HTTPException as exc:
            self._store.atomic_update(
                job_id,
                {
                    "status": "error",
                    "message": str(exc.detail),
                    "elapsed_seconds": _job_elapsed_seconds(self._store.jobs[job_id]),
                },
                expected_status="running",
            )
            raise
        except ValueError as exc:
            detail = str(exc)
            self._store.atomic_update(
                job_id,
                {
                    "status": "error",
                    "message": detail,
                    "elapsed_seconds": _job_elapsed_seconds(self._store.jobs[job_id]),
                },
                expected_status="running",
            )
            raise HTTPException(status_code=400, detail=detail) from exc
        except Exception as exc:
            logger.error(
                "frontier_auto_range_failed",
                error=str(exc),
                node_id=body.node_id,
                exc_info=True,
            )
            self._store.atomic_update(
                job_id,
                {
                    "status": "error",
                    "message": f"Frontier auto range failed: {exc}",
                    "elapsed_seconds": _job_elapsed_seconds(self._store.jobs[job_id]),
                },
                expected_status="running",
            )
            raise HTTPException(
                status_code=500,
                detail="Frontier auto range failed. Check the server logs for details.",
            ) from exc

    def _run_streaming_frontier_auto_range_job(
        self,
        body: OptimiserFrontierAutoRangeRequest,
        job_id: str,
        *,
        config: dict[str, Any],
        chunk_size: int,
        partition_count: int,
        streaming_plan: _StreamingAutoRangePlan,
    ) -> OptimiserFrontierAutoRangeResponse:
        import polars as pl

        try:
            self._raise_if_frontier_auto_range_stopped(job_id)
            with (
                tempfile.TemporaryDirectory(prefix="haute_frontier_range_") as raw_dir,
                tempfile.TemporaryDirectory(prefix="haute_frontier_range_parts_") as parts_dir,
            ):
                checkpoint_dir = Path(raw_dir)
                self._store.atomic_update(
                    job_id,
                    {
                        "message": "Executing base pipeline",
                        "progress": 0.05,
                        "elapsed_seconds": _job_elapsed_seconds(self._store.jobs[job_id]),
                    },
                    expected_status="running",
                )
                self._raise_if_frontier_auto_range_stopped(job_id)
                base_required = None
                if streaming_plan.base_required_columns is not None:
                    base_required = {
                        streaming_plan.base_node_id: streaming_plan.base_required_columns,
                    }
                lazy_outputs = self._execute_pipeline(
                    body,
                    job_id,
                    checkpoint_dir,
                    required_columns_by_node=base_required,
                    target_node_id=streaming_plan.base_node_id,
                )
                self._raise_if_frontier_auto_range_stopped(job_id)
                base_lf = lazy_outputs.get(streaming_plan.base_node_id)
                if base_lf is None:
                    raise ValueError(
                        "Streaming auto-range base node did not produce a dataframe: "
                        f"{streaming_plan.base_node_id!r}."
                    )

                funcs = self._build_streaming_auto_range_chain_functions(
                    body,
                    streaming_plan,
                )
                scenario_steps = self._streaming_scenario_steps(body, streaming_plan)
                base_chunk_size = max(1, chunk_size // scenario_steps)
                qid_col = str(config.get("quote_id", "quote_id"))
                constraint_cols = (
                    list(config["constraints"].keys())
                    if isinstance(config.get("constraints"), dict)
                    else []
                )
                accumulator = _ScenarioFrontierRangeAccumulator(
                    quote_id_col=qid_col,
                    constraint_cols=constraint_cols,
                    partition_count=partition_count,
                    parts_root=Path(parts_dir),
                )

                self._store.atomic_update(
                    job_id,
                    {
                        "message": "Streaming scenario chunks",
                        "progress": 0.30,
                        "elapsed_seconds": _job_elapsed_seconds(self._store.jobs[job_id]),
                    },
                    expected_status="running",
                )
                self._raise_if_frontier_auto_range_stopped(job_id)
                chunk_index = 0
                base_lazy = base_lf if isinstance(base_lf, pl.LazyFrame) else base_lf.lazy()
                for base_batch in base_lazy.collect_batches(
                    chunk_size=base_chunk_size,
                    maintain_order=False,
                    engine="streaming",
                ):
                    self._raise_if_frontier_auto_range_stopped(job_id)
                    if base_batch.height == 0:
                        continue
                    streamed_lf: Any = base_batch.lazy()
                    for chain_id in streaming_plan.chain_node_ids:
                        self._raise_if_frontier_auto_range_stopped(job_id)
                        fn, _is_source = funcs[chain_id]
                        streamed_lf = fn(streamed_lf)
                        if not isinstance(streamed_lf, pl.LazyFrame):
                            streamed_lf = streamed_lf.lazy()

                    validated_constraints, scored_lf = self._validate_and_project_auto_range(
                        streamed_lf,
                        config,
                        job_id,
                    )
                    self._raise_if_frontier_auto_range_stopped(job_id)
                    if validated_constraints != constraint_cols:
                        raise ValueError("Streaming auto-range constraint columns changed.")
                    batch = (
                        scored_lf.select(
                            [
                                pl.col(qid_col).cast(pl.String).alias(qid_col),
                                *[pl.col(cname) for cname in constraint_cols],
                            ]
                        )
                        .collect(engine="streaming")
                    )
                    self._raise_if_frontier_auto_range_stopped(job_id)
                    accumulator.add_batch(batch, batch_index=chunk_index)
                    chunk_index += 1
                    if chunk_index % 10 == 0:
                        self._store.atomic_update(
                            job_id,
                            {
                                "message": f"Streaming scenario chunks ({chunk_index})",
                                "progress": 0.30,
                                "elapsed_seconds": _job_elapsed_seconds(
                                    self._store.jobs[job_id]
                                ),
                            },
                            expected_status="running",
                        )

                self._store.atomic_update(
                    job_id,
                    {
                        "message": "Combining scenario envelope",
                        "progress": 0.85,
                        "elapsed_seconds": _job_elapsed_seconds(self._store.jobs[job_id]),
                    },
                    expected_status="running",
                )
                self._raise_if_frontier_auto_range_stopped(job_id)
                ranges = accumulator.finish(
                    check_cancelled=lambda: self._raise_if_frontier_auto_range_stopped(job_id),
                )
                self._raise_if_frontier_auto_range_stopped(job_id)
                response_ranges = {
                    name: OptimiserFrontierRange(min=value["min"], max=value["max"])
                    for name, value in ranges.items()
                }
                response = OptimiserFrontierAutoRangeResponse(
                    status="ok",
                    ranges=response_ranges,
                    warning=None,
                )
                self._store.atomic_update(
                    job_id,
                    {
                        "status": "completed",
                        "progress": 1.0,
                        "message": "Completed",
                        "elapsed_seconds": _job_elapsed_seconds(self._store.jobs[job_id]),
                        "result": response.model_dump(),
                    },
                    expected_status="running",
                )
                return response
        except BackgroundJobStoppedError:
            raise
        except HTTPException as exc:
            self._store.atomic_update(
                job_id,
                {
                    "status": "error",
                    "message": str(exc.detail),
                    "elapsed_seconds": _job_elapsed_seconds(self._store.jobs[job_id]),
                },
                expected_status="running",
            )
            raise
        except ValueError as exc:
            detail = str(exc)
            self._store.atomic_update(
                job_id,
                {
                    "status": "error",
                    "message": detail,
                    "elapsed_seconds": _job_elapsed_seconds(self._store.jobs[job_id]),
                },
                expected_status="running",
            )
            raise HTTPException(status_code=400, detail=detail) from exc
        except Exception as exc:
            logger.error(
                "frontier_auto_range_streaming_failed",
                error=str(exc),
                node_id=body.node_id,
                exc_info=True,
            )
            self._store.atomic_update(
                job_id,
                {
                    "status": "error",
                    "message": f"Streaming frontier auto range failed: {exc}",
                    "elapsed_seconds": _job_elapsed_seconds(self._store.jobs[job_id]),
                },
                expected_status="running",
            )
            raise HTTPException(
                status_code=500,
                detail="Frontier auto range failed. Check the server logs for details.",
            ) from exc

    def _build_streaming_auto_range_chain_functions(
        self,
        body: OptimiserFrontierAutoRangeRequest,
        streaming_plan: _StreamingAutoRangePlan,
    ) -> dict[str, tuple[Callable, bool]]:
        from haute._execute_lazy import _build_funcs
        from haute.executor import _build_node_fn, _compile_preamble, _pipeline_dir
        from haute.graph_utils import _prepare_graph

        node_map, _order, _parents_of, id_to_name = _prepare_graph(
            body.graph,
            body.node_id,
            source="batch",
        )
        preamble_ns = (
            _compile_preamble(
                body.graph.preamble or "",
                force_refresh=False,
                pipeline_dir=_pipeline_dir(body.graph),
            )
            or None
        )
        chain_parents: dict[str, list[str]] = {}
        parent_id = streaming_plan.base_node_id
        for chain_id in streaming_plan.chain_node_ids:
            chain_parents[chain_id] = [parent_id]
            parent_id = chain_id
        reuse_loaded_model_by_node = {
            chain_id: True
            for chain_id in streaming_plan.chain_node_ids
            if node_map[chain_id].data.nodeType == NodeType.MODEL_SCORE
        }
        return _build_funcs(
            list(streaming_plan.chain_node_ids),
            node_map,
            chain_parents,
            id_to_name,
            body.graph.parents_of,
            _build_node_fn,
            preamble_ns=preamble_ns,
            source="live",
            required_output_columns_by_node=streaming_plan.required_output_columns_by_node,
            reuse_loaded_model_by_node=reuse_loaded_model_by_node,
        )

    @staticmethod
    def _streaming_scenario_steps(
        body: OptimiserFrontierAutoRangeRequest,
        streaming_plan: _StreamingAutoRangePlan,
    ) -> int:
        from haute._builders import _DEFAULT_SCENARIO_STEPS

        node = body.graph.node_map[streaming_plan.scenario_node_id]
        raw_steps = node.data.config.get("steps")
        steps = int(raw_steps) if raw_steps is not None else _DEFAULT_SCENARIO_STEPS
        if steps < 1:
            raise ValueError(f"Scenario expander requires steps >= 1, got {steps}")
        return steps

    def _launch_frontier_auto_range_background(
        self,
        body: OptimiserFrontierAutoRangeRequest,
        job_id: str,
        **prepared: Any,
    ) -> None:
        start_time = time.monotonic()
        self._store.atomic_update(
            job_id,
            {
                "start_time": start_time,
                "timeout": prepared["timeout"],
            },
        )

        def _auto_range_background() -> None:
            try:
                self._run_frontier_auto_range_job(body, job_id, **prepared)
            except BackgroundJobStoppedError:
                return
            except HTTPException:
                return
            except Exception as exc:
                logger.error(
                    "frontier_auto_range_worker_failed",
                    error=str(exc),
                    node_id=body.node_id,
                    exc_info=True,
                )
            finally:
                self._auto_range_jobs.release(job_id)

        thread = threading.Thread(target=_auto_range_background, daemon=True)
        try:
            thread.start()
        except Exception as exc:
            logger.error(
                "frontier_auto_range_worker_start_failed",
                error=str(exc),
                node_id=body.node_id,
                exc_info=True,
            )
            self._store.atomic_update(
                job_id,
                {
                    "status": "error",
                    "message": f"Failed to start auto-range worker: {exc}",
                    "elapsed_seconds": time.monotonic() - start_time,
                },
                expected_status="running",
            )
            self._auto_range_jobs.release(job_id)

    # ------------------------------------------------------------------
    # Private orchestration steps
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_config(config: dict[str, Any]) -> str:
        """Validate optimiser config; return the mode ('online' or 'ratebook')."""
        objective = config.get("objective")
        if not objective:
            raise HTTPException(
                status_code=400,
                detail="No objective column configured."
                " Open the config panel and set an objective.",
            )

        mode = config.get("mode", "online")
        if mode not in ("online", "ratebook"):
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported optimiser mode '{mode}'."
                " Currently supported: online, ratebook.",
            )

        if mode == "ratebook":
            factor_columns = config.get("factor_columns")
            if not factor_columns:
                raise HTTPException(
                    status_code=400,
                    detail="Ratebook mode requires factor_columns. Add at least one factor group.",
                )

        return str(mode)

    @staticmethod
    def _is_blocking_solve_job(job: dict[str, Any]) -> bool:
        """Return whether a job should reserve the real optimiser solve slot."""
        if job.get("status") != "running":
            return False
        return job.get(_JOB_TYPE_KEY, _SOLVE_JOB_TYPE) not in _NON_BLOCKING_RUNNING_JOB_TYPES

    def _has_running_solve_job(self) -> bool:
        return self._store.has_job_matching(self._is_blocking_solve_job)

    def _check_no_concurrent_jobs(self) -> None:
        """Reject if an optimisation solve job is already running."""
        if self._has_running_solve_job():
            raise HTTPException(
                status_code=409,
                detail="An optimisation job is already running. Please wait for it to finish.",
            )

    def _execute_pipeline(
        self,
        body: OptimiserSolveRequest | OptimiserEstimateRequest | OptimiserFrontierAutoRangeRequest,
        job_id: str,
        checkpoint_dir: Path,
        *,
        required_columns_by_node: Mapping[str, Iterable[str]] | None = None,
        target_node_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute the pipeline lazily up to the optimiser node.

        The caller owns *checkpoint_dir* lifecycle (creation + cleanup).
        """
        try:
            import polars as pl

            from haute.executor import (
                _build_node_fn,
                _compile_preamble,
                _pipeline_dir,
                _resolve_batch_scenario,
            )
            from haute.graph_utils import _execute_lazy

            # Resolve scenario: optimiser runs on batch data, not live.
            scenario = _resolve_batch_scenario(body.graph) or "batch"

            preamble_ns = (
                _compile_preamble(
                    body.graph.preamble or "",
                    force_refresh=False,
                    pipeline_dir=_pipeline_dir(body.graph),
                )
                or None
            )

            # Reduce streaming chunk size for the optimiser path (same
            # rationale as execute_sink — wide schemas with 100+ columns
            # can cause OOM with the default auto-sized chunk).
            _prev_chunk = pl.Config.state().get("POLARS_STREAMING_CHUNK_SIZE")
            pl.Config.set_streaming_chunk_size(50_000)
            try:
                from haute.executor import ENFORCE_CONTRACTS

                lazy_outputs, *_ = _execute_lazy(
                    body.graph,
                    _build_node_fn,
                    target_node_id=target_node_id or body.node_id,
                    preamble_ns=preamble_ns,
                    source=scenario,
                    checkpoint_dir=checkpoint_dir,
                    enforce_contracts=ENFORCE_CONTRACTS,
                    preserve_node_ids=_optimiser_side_input_ids(body.graph, body.node_id),
                    required_columns_by_node=required_columns_by_node,
                )
            finally:
                # Restore previous streaming chunk size if one was explicitly set.
                # When _prev_chunk is None (Polars auto-default), skip the restore
                # — Polars does not accept 0 and has no "unset" API.
                if _prev_chunk is not None:
                    pl.Config.set_streaming_chunk_size(int(_prev_chunk))
            return lazy_outputs
        except HTTPException:
            raise
        except ContractMismatchError as exc:
            error_msg = f"Pipeline execution failed: {exc}"
            logger.warning(
                "pipeline_contract_mismatch",
                error=str(exc),
                node_id=body.node_id,
                exc_info=True,
            )
            self._store.atomic_update(job_id, {"status": "error", "message": error_msg})
            raise HTTPException(status_code=400, detail=error_msg) from exc
        except Exception as exc:
            error_msg = f"Pipeline execution failed: {exc}"
            logger.error(
                "pipeline_exec_failed",
                error=str(exc),
                node_id=body.node_id,
                exc_info=True,
            )
            self._store.atomic_update(job_id, {"status": "error", "message": error_msg})
            raise HTTPException(
                status_code=500,
                detail="Pipeline execution failed. Check the server logs for details.",
            )

    def _resolve_data_source(
        self,
        lazy_outputs: dict[str, Any],
        config: dict[str, Any],
        node_id: str,
        job_id: str,
    ) -> Any:
        """Pick the correct lazy source from pipeline outputs."""
        data_input_id = config.get("data_input")
        if isinstance(data_input_id, str) and data_input_id:
            if data_input_id in lazy_outputs:
                source_lf = lazy_outputs[data_input_id]
            else:
                error_msg = (
                    f"Configured optimiser data_input {data_input_id!r} did not produce data. "
                    "Make sure it is connected to the optimiser node and produces a dataframe."
                )
                self._store.atomic_update(job_id, {"status": "error", "message": error_msg})
                raise HTTPException(status_code=400, detail=error_msg)
        else:
            source_lf = lazy_outputs.get(node_id)

        if source_lf is None:
            error_msg = (
                "No data arrived at the optimiser node. "
                "Make sure an upstream data source is connected and producing data."
            )
            self._store.atomic_update(job_id, {"status": "error", "message": error_msg})
            raise HTTPException(status_code=400, detail=error_msg)

        return source_lf

    def _validate_and_project(
        self,
        source_lf: Any,
        config: dict[str, Any],
        job_id: str,
        *,
        validate_quote_id_nulls: bool = True,
    ) -> tuple[list[str], Any]:
        """Validate columns and build the projection for the solver.

        Returns (constraint_cols, projected_lazy_frame).
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
        missing_cols = sorted(required_cols - available_cols)
        if missing_cols:
            avail = sorted(available_cols)
            detail = f"Missing columns in scored data: {missing_cols}. Available: {avail}"
            self._store.atomic_update(job_id, {"status": "error", "message": detail})
            raise HTTPException(status_code=400, detail=detail)

        constraint_cols = list(constraints.keys()) if isinstance(constraints, dict) else []
        qid_dtype = schema[qid_col]
        if not (
            qid_dtype == pl.String or qid_dtype == pl.Categorical or isinstance(qid_dtype, pl.Enum)
        ):
            detail = (
                f"{qid_col} must be Utf8 (String), Categorical, or Enum, got {qid_dtype}. "
                "Numeric, binary, and other dtypes are not supported as quote_id columns."
            )
            self._store.atomic_update(job_id, {"status": "error", "message": detail})
            raise HTTPException(status_code=400, detail=detail)

        if validate_quote_id_nulls:
            null_count = int(
                source_lf.select(pl.col(qid_col).null_count().alias("n"))
                .collect(engine="streaming")
                .item()
            )
            if null_count > 0:
                detail = (
                    f"{_NULL_QUOTE_ID_DETAIL_PREFIX} ({null_count} rows). "
                    "Every row must have a non-null quote_id; check upstream filters and joins."
                )
                self._store.atomic_update(job_id, {"status": "error", "message": detail})
                raise HTTPException(status_code=400, detail=detail)

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
        cast_exprs = [pl.col(c).cast(t) for c, t in cast_map.items()]
        if qid_dtype == pl.String:
            cast_exprs.append(pl.col(qid_col).cast(pl.Categorical))

        scored_lf = source_lf.select(solver_cols).with_columns(cast_exprs)
        return constraint_cols, scored_lf

    def _validate_and_project_auto_range(
        self,
        source_lf: Any,
        config: dict[str, Any],
        job_id: str,
    ) -> tuple[list[str], Any]:
        """Validate and project only the columns auto-range needs.

        Auto-range computes per-quote extrema for configured constraints. It
        does not need the objective, scenario index, or scenario value columns
        that the full solver needs to build a ``QuoteGrid``.
        """
        import polars as pl

        constraints = config["constraints"]
        qid_col = str(config.get("quote_id", "quote_id"))

        schema = source_lf.collect_schema()
        available_cols = set(schema.names())
        constraint_cols = list(constraints.keys()) if isinstance(constraints, dict) else []
        required_cols = {qid_col, *constraint_cols}
        missing_cols = sorted(required_cols - available_cols)
        if missing_cols:
            avail = sorted(available_cols)
            detail = f"Missing columns in scored data: {missing_cols}. Available: {avail}"
            self._store.atomic_update(job_id, {"status": "error", "message": detail})
            raise HTTPException(status_code=400, detail=detail)

        qid_dtype = schema[qid_col]
        if not (
            qid_dtype == pl.String or qid_dtype == pl.Categorical or isinstance(qid_dtype, pl.Enum)
        ):
            detail = (
                f"{qid_col} must be Utf8 (String), Categorical, or Enum, got {qid_dtype}. "
                "Numeric, binary, and other dtypes are not supported as quote_id columns."
            )
            self._store.atomic_update(job_id, {"status": "error", "message": detail})
            raise HTTPException(status_code=400, detail=detail)

        auto_range_cols = [qid_col, *constraint_cols]
        cast_exprs = [pl.col(c).cast(pl.Float32()) for c in constraint_cols]
        if qid_dtype == pl.String:
            cast_exprs.append(pl.col(qid_col).cast(pl.Categorical))
        scored_lf = source_lf.select(auto_range_cols).with_columns(cast_exprs)
        return constraint_cols, scored_lf

    @staticmethod
    def _extract_factors(
        lazy_outputs: dict[str, Any],
        config: dict[str, Any],
        mode: str,
    ) -> Any:
        """Extract ratebook factors DataFrame (None for online mode)."""
        if mode != "ratebook":
            return None
        banding_source_id = config.get("banding_source")
        if banding_source_id and banding_source_id in lazy_outputs:
            return lazy_outputs[banding_source_id].collect(engine="streaming")
        return None

    def _build_grid(
        self,
        scored_lf: Any,
        constraint_cols: list[str],
        config: dict[str, Any],
        node_id: str,
        job_id: str,
    ) -> QuoteGrid:
        """Sink scored data to parquet and build the QuoteGrid."""
        from price_contour import build_grid_from_parquet_chunked

        objective = config["objective"]
        qid_col = config.get("quote_id", "quote_id")
        mult_col = config.get("scenario_value", "scenario_value")
        step_col = config.get("scenario_index", "scenario_index")
        try:
            chunk_size = _chunk_size_from_config(config)
        except ValueError as exc:
            detail = f"Grid construction failed: {exc}"
            self._store.atomic_update(job_id, {"status": "error", "message": detail})
            raise HTTPException(status_code=400, detail=detail) from exc

        from haute._polars_utils import safe_sink

        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".parquet")
        os.close(tmp_fd)
        try:
            safe_sink(scored_lf, tmp_path)
            del scored_lf

            build_kwargs = {
                "quote_id": qid_col,
                "scenario_index": step_col,
                "scenario_value": mult_col,
                "objective": objective,
            }
            quote_grid = build_grid_from_parquet_chunked(
                tmp_path,
                constraint_cols,
                chunk_size,
                **build_kwargs,
            )
        except HTTPException:
            raise
        except Exception as exc:
            detail = f"Grid construction failed: {exc}"
            logger.error("grid_build_failed", error=str(exc), node_id=node_id, exc_info=True)
            self._store.atomic_update(job_id, {"status": "error", "message": detail})
            raise HTTPException(status_code=400, detail=detail) from exc
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except Exception as cleanup_exc:
                    logger.warning(
                        "optimiser_grid_temp_cleanup_failed",
                        path=tmp_path,
                        error=str(cleanup_exc),
                        exc_info=True,
                    )

        return quote_grid

    def _launch_background(
        self,
        job_id: str,
        node_id: str,
        config: dict[str, Any],
        mode: str,
        quote_grid: QuoteGrid,
        factors_df: Any,
        *,
        factor_level_order: dict[str, list[str]] | None = None,
    ) -> None:
        """Start the solver in a background thread."""
        start_time = time.monotonic()
        self._store.atomic_update(
            job_id,
            {
                "start_time": start_time,
                "timeout": config.get("timeout", _DEFAULT_TIMEOUT),
            },
        )

        def _solve_background() -> None:
            try:
                # P7: Use atomic_update instead of mutating the job dict
                # directly, so status-polling reads on the main thread
                # always see a consistent snapshot.
                progress_job = self._store.atomic_update(
                    job_id,
                    {
                        "message": "Solving",
                        "progress": 0.1,
                        "elapsed_seconds": time.monotonic() - start_time,
                    },
                    expected_status="running",
                )
                if progress_job is None:
                    logger.info("solve_start_skipped", job_id=job_id, expected_status="running")
                    return
                if mode == "ratebook":
                    _solve_ratebook(
                        quote_grid,
                        config,
                        factors_df,
                        self._store,
                        job_id,
                        start_time,
                        factor_level_order=factor_level_order,
                    )
                else:
                    _solve_online(
                        quote_grid,
                        config,
                        self._store,
                        job_id,
                        start_time,
                    )
            except Exception as exc:
                error_categories: dict[type, tuple[str, str]] = {
                    ValueError: ("Data error", "data"),
                    RuntimeError: ("Algorithm error", "algorithm"),
                }
                prefix, category = error_categories.get(
                    type(exc),
                    ("Unexpected error", "unexpected"),
                )
                error_msg = f"{prefix}: {exc}"
                logger.error(
                    "solve_failed",
                    error=str(exc),
                    node_id=node_id,
                    category=category,
                    exc_info=True,
                )
                error_job = self._store.atomic_update(
                    job_id,
                    {
                        "status": "error",
                        "message": error_msg,
                        "elapsed_seconds": time.monotonic() - start_time,
                    },
                    expected_status="running",
                )
                if error_job is None:
                    logger.info("solve_error_update_skipped", job_id=job_id)

        thread = threading.Thread(target=_solve_background, daemon=True)
        try:
            thread.start()
        except Exception as exc:
            logger.error(
                "solve_worker_start_failed",
                error=str(exc),
                node_id=node_id,
                exc_info=True,
            )
            self._store.atomic_update(
                job_id,
                {
                    "status": "error",
                    "message": f"Failed to start optimiser worker: {exc}",
                    "elapsed_seconds": time.monotonic() - start_time,
                },
            )
            raise HTTPException(
                status_code=500,
                detail="Optimiser worker failed to start. Check the server logs for details.",
            ) from exc
