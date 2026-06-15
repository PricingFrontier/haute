"""RAM estimation for training — metadata-based approach.

Before materialising a full pipeline for model training, estimate the
memory footprint from parquet metadata alone:

1. Walk the graph backwards from the training node (respecting the
   active source and live-switch pruning) to find ancestor sources.
2. Read parquet row-group metadata — row count and column count — from
   those sources.  This is instant (reads only the file footer).
3. Estimate ``bytes_per_row`` from column count × dtype width, then
   apply an algorithm-specific overhead multiplier.
4. If the estimate exceeds available RAM, calculate a safe row limit.

This replaces the previous probe-based approach which ran a 1 000-row
sample through the pipeline.  The probe was fragile: inner joins with
no key overlap in small samples produced zero rows, breaking the
estimate.  Metadata is always available and always accurate.
"""

from __future__ import annotations

import math
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple, cast

from haute._edge_join import build_edge_join_kwargs, edge_join_key_columns_by_role
from haute._graph_utils import build_parents_of
from haute._logging import get_logger
from haute._polars_utils import read_parquet_metadata
from haute.graph_utils import GraphNode, NodeType, PipelineGraph

logger = get_logger(component="ram_estimate")

__all__ = [
    "available_ram_bytes",
    "available_vram_bytes",
    "estimate_gpu_vram_bytes",
    "estimate_source_rows",
    "estimate_safe_training_rows",
    "RamEstimate",
]


# ---------------------------------------------------------------------------
# System RAM
# ---------------------------------------------------------------------------


def available_ram_bytes() -> int:
    """Return available system RAM in bytes.

    - **Linux**: reads ``/proc/meminfo`` (most accurate).
    - **macOS / POSIX**: ``os.sysconf`` page-based query.
    - **Windows**: ``GlobalMemoryStatusEx`` via ctypes.
    - **Fallback**: conservative 4 GiB default.
    """
    proc_meminfo_error: str | None = None
    sysconf_error: str | None = None
    sysconf_pages: int | None = None
    sysconf_page_size: int | None = None
    windows_attempted = False
    windows_error: str | None = None

    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
        proc_meminfo_error = "MemAvailable not found"
    except (OSError, ValueError, IndexError) as exc:
        proc_meminfo_error = str(exc)

    try:
        import os

        sysconf = cast(Any, os).sysconf
        sysconf_pages = int(sysconf("SC_AVPHYS_PAGES"))
        sysconf_page_size = int(sysconf("SC_PAGE_SIZE"))
        if sysconf_pages > 0 and sysconf_page_size > 0:
            return sysconf_pages * sysconf_page_size
        sysconf_error = "non-positive sysconf memory values"
    except (AttributeError, OSError, ValueError) as exc:
        sysconf_error = str(exc)

    if sys.platform == "win32":
        windows_attempted = True
        try:
            import ctypes

            class MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            mem = MemoryStatusEx()
            mem.dwLength = ctypes.sizeof(MemoryStatusEx)
            kernel32 = cast(Any, ctypes).windll.kernel32
            if kernel32.GlobalMemoryStatusEx(ctypes.byref(mem)):
                return int(mem.ullAvailPhys)
            windows_error = "GlobalMemoryStatusEx returned false"
        except (OSError, AttributeError, ImportError) as exc:
            windows_error = str(exc)

    fallback_bytes = 4 * 1024**3
    logger.warning(
        "available_ram_fallback_4gib",
        fallback_bytes=fallback_bytes,
        platform=sys.platform,
        proc_meminfo_error=proc_meminfo_error,
        sysconf_error=sysconf_error,
        sysconf_pages=sysconf_pages,
        sysconf_page_size=sysconf_page_size,
        windows_attempted=windows_attempted,
        windows_error=windows_error,
    )
    return fallback_bytes


# ---------------------------------------------------------------------------
# GPU VRAM
# ---------------------------------------------------------------------------


def available_vram_bytes() -> int | None:
    """Return total GPU VRAM in bytes, or ``None`` if no GPU is detected."""
    try:
        import subprocess

        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            line = result.stdout.strip().split("\n")[0].strip()
            return int(line) * 1024 * 1024
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, OSError):
        pass
    return None


# CatBoost GPU stores per row:
#   float32 feature data  (4 bytes/feature)
#   + binarised features  (1 byte/feature)
#   + label/gradient/hessian (12 bytes)
# Plus histogram buffers that depend on border_count and tree depth,
# and ~500 MB CUDA runtime overhead.
# The 2× safety multiplier accounts for CUDA fragmentation,
# CatBoost-internal temporary buffers, and memory that grows during
# tree construction.  Empirically validated: 10M rows × 100 features
# OOM'd an 8 GB GPU at iteration 231/1000, confirming that the raw
# data footprint (~5 GB) roughly doubles during training.
_VRAM_SAFETY_MULTIPLIER = 2.0


def estimate_gpu_vram_bytes(
    n_rows: int,
    n_features: int,
    *,
    border_count: int = 128,
    depth: int = 6,
) -> int:
    """Estimate CatBoost GPU VRAM needed for *n_rows* × *n_features*."""
    feature_bytes = n_rows * n_features * 5  # float32 + binarised
    per_row_bytes = n_rows * 12  # label + gradient + hessian
    n_leaves = 2 ** min(depth, 10)
    histogram_bytes = n_features * border_count * n_leaves * 8

    raw = feature_bytes + per_row_bytes + histogram_bytes
    return int(raw * _VRAM_SAFETY_MULTIPLIER)


# ---------------------------------------------------------------------------
# Source metadata — source-aware
# ---------------------------------------------------------------------------


# Detailed metadata is deliberately internal.  The compatibility helpers still
# expose tuple shapes used by existing tests and callers.
class _DetailedSourceMetadata(NamedTuple):
    row_count: int
    column_count: int
    columns: Mapping[str, str]
    column_width_keys: Mapping[str, str]
    column_uncompressed_size_bytes: Mapping[str, int]
    uncompressed_size_bytes: int


class _AncestorSourceMetadata(NamedTuple):
    row_count: int | None
    column_count: int
    sources: tuple[_DetailedSourceMetadata, ...]


class _ResolvedTargetColumns(NamedTuple):
    columns: tuple[str, ...]
    width_columns: Mapping[str, str]


# Bytes per column for the analytical estimate.  Training features are
# cast to Float32 (4 bytes) in _build_pool, but the Polars DataFrame
def _parquet_metadata(path: str) -> tuple[int, int]:
    """Return (row_count, column_count) from parquet footer metadata."""
    meta = _detailed_parquet_metadata(path)
    return meta.row_count, meta.column_count


def _detailed_parquet_metadata(path: str) -> _DetailedSourceMetadata:
    """Return footer-only parquet metadata used by the RAM estimator."""
    meta = read_parquet_metadata(Path(path))
    return _DetailedSourceMetadata(
        row_count=int(meta["row_count"]),
        column_count=int(meta["column_count"]),
        columns=dict(meta.get("columns", {})),
        column_width_keys={str(column): str(column) for column in dict(meta.get("columns", {}))},
        column_uncompressed_size_bytes={
            str(name): int(size)
            for name, size in dict(meta.get("column_uncompressed_size_bytes", {})).items()
        },
        uncompressed_size_bytes=int(meta.get("uncompressed_size_bytes", 0)),
    )


def _source_scoped_metadata(
    meta: _DetailedSourceMetadata,
    node_id: str,
) -> _DetailedSourceMetadata:
    return meta._replace(
        column_width_keys={column: f"{node_id}\0{column}" for column in meta.columns},
    )


def _count_source_rows_for_node(node: GraphNode) -> int | None:
    """Row count for a single source node (parquet metadata or line count)."""
    config = node.data.config
    node_type = node.data.nodeType

    try:
        if node_type == NodeType.API_INPUT:
            path = config.get("path", "")
            if path.endswith((".json", ".jsonl")):
                # v2 per-port caches don't expose a single aggregate row
                # count; RAM estimation falls back to JSONL line count
                # (.json files yield None, treated as "unknown" upstream).
                if path.endswith(".jsonl") and Path(path).exists():
                    return _jsonl_row_count(path)
                return None
            if path and Path(path).exists():
                rows, _ = _parquet_metadata(path)
                return rows
            return None

        if node_type == NodeType.DATA_SOURCE:
            source_type = config.get("sourceType", "flat_file")
            if source_type == "databricks":
                return None
            path = config.get("path", "")
            if path and Path(path).exists():
                if path.endswith(".parquet"):
                    rows, _ = _parquet_metadata(path)
                    return rows
                if path.endswith(".csv"):
                    return _csv_row_count(path)
            return None
    except Exception as exc:
        logger.warning("source_row_count_failed", node_id=node.id, error=str(exc))
        return None

    return None


def _source_metadata_for_node(node: GraphNode) -> tuple[int, int] | None:
    """Return (row_count, column_count) for a source node, or None."""
    detail = _detailed_source_metadata_for_node(node)
    if detail is None:
        return None
    return detail.row_count, detail.column_count


def _detailed_source_metadata_for_node(node: GraphNode) -> _DetailedSourceMetadata | None:
    """Return detailed parquet source metadata for a source node, or None."""
    config = node.data.config
    node_type = node.data.nodeType

    try:
        path = config.get("path", "")
        if not path:
            return None

        if node_type == NodeType.API_INPUT:
            if path.endswith((".json", ".jsonl")):
                # v2 per-port caches are one parquet per emit-true table,
                # so there's no single (row_count, column_count) summary
                # to return. Conservative None lets the caller fall back
                # to its "unknown source size" branch.
                return None
            if Path(path).exists():
                return _source_scoped_metadata(_detailed_parquet_metadata(path), node.id)
            return None

        if node_type == NodeType.DATA_SOURCE:
            if config.get("sourceType", "flat_file") == "databricks":
                return None
            if Path(path).exists() and path.endswith(".parquet"):
                return _source_scoped_metadata(_detailed_parquet_metadata(path), node.id)
            return None
    except Exception as exc:
        logger.warning("source_metadata_failed", node_id=node.id, error=str(exc))
        return None

    return None


def _csv_row_count(path: str) -> int:
    count = 0
    with open(path, "rb") as f:
        for _ in f:
            count += 1
    return max(count - 1, 0)


def _jsonl_row_count(path: str) -> int:
    count = 0
    with open(path, "rb") as f:
        for _ in f:
            count += 1
    return count


def _ancestor_source_metadata(
    graph: PipelineGraph,
    target_node_id: str,
    source: str = "live",
) -> tuple[int | None, int]:
    """Row count and column count from ancestor sources of the target node.

    Prunes edges by source (respecting live-switch routing) then walks
    backwards from the target to find only the relevant source nodes.

    Returns ``(max_rows, max_columns)`` across ancestor sources.
    ``max_rows`` is ``None`` if no row count could be determined.
    """
    meta = _detailed_ancestor_source_metadata(graph, target_node_id, source)
    return meta.row_count, meta.column_count


def _detailed_ancestor_source_metadata(
    graph: PipelineGraph,
    target_node_id: str,
    source: str = "live",
) -> _AncestorSourceMetadata:
    from haute._execute_lazy import _prune_live_switch_edges
    from haute._topo import ancestors

    node_map = {n.id: n for n in graph.nodes}
    all_ids = set(node_map)

    pruned_edges = _prune_live_switch_edges(graph.edges, node_map, source)
    ancestor_ids = ancestors(target_node_id, pruned_edges, all_ids)

    max_rows: int | None = None
    max_cols: int = 0
    sources: list[_DetailedSourceMetadata] = []

    for nid in ancestor_ids:
        node = node_map.get(nid)
        if node is None:
            continue
        if node.data.nodeType not in (NodeType.API_INPUT, NodeType.DATA_SOURCE):
            continue
        meta = _detailed_source_metadata_for_node(node)
        if meta is not None:
            sources.append(meta)
            if max_rows is None or meta.row_count > max_rows:
                max_rows = meta.row_count
            max_cols = max(max_cols, meta.column_count)

    return _AncestorSourceMetadata(max_rows, max_cols, tuple(sources))


def estimate_source_rows(graph: PipelineGraph) -> int | None:
    """Estimate total rows entering the pipeline from all source nodes.

    Returns the **maximum** row count across all source nodes.
    Prefer :func:`_ancestor_source_metadata` when target and source
    are known.
    """
    max_rows: int | None = None
    for node in graph.nodes:
        if node.data.nodeType in (NodeType.API_INPUT, NodeType.DATA_SOURCE):
            count = _count_source_rows_for_node(node)
            if count is not None:
                max_rows = max(max_rows or 0, count)
    return max_rows


# ---------------------------------------------------------------------------
# Estimation
# ---------------------------------------------------------------------------
#
# The training lifecycle has multiple memory-intensive phases:
#   0. Pipeline execution — _execute_and_sink collects the full lazy
#      plan to parquet.  Intermediate joins and transforms can hold
#      multiple large DataFrames simultaneously.
#   1. Split — reads the full dataset eagerly to add _partition column.
#   2. model.fit() — train + eval CatBoost Pools + internal buffers.
#   3. Diagnostics — SHAP, PDP, feature importance.
#   4. Cross-validation (if enabled).
#
# Pipeline execution is typically the peak because it holds join
# intermediates in memory.  Empirically validated: the peak is
# approximately 3× the raw dataset size at the training node.
#
# We use: N_rows × N_cols × 8 bytes (Float64) × 3.0

_RAM_SAFETY_FACTOR = 0.7
_MIN_SAFE_ROWS = 500

# Empirical overhead multiplier.  Covers pipeline execution (join
# intermediates), split, CatBoost Pool construction, and training
# buffers (gradients, hessians, histograms).  Validated against
# observed 25 GB peak for 10M × 101 cols (~8 GB raw data).
_OVERHEAD_MULTIPLIER = 3.0

_BYTES_PER_COL = 8  # Float64 in Polars


def _estimate_peak_bytes(
    n_rows: int,
    n_cols: int,
    *,
    base_bytes_per_row: float | None = None,
) -> int:
    """Estimate peak RAM for the full training lifecycle."""
    raw_bytes_per_row = (
        n_cols * _BYTES_PER_COL if base_bytes_per_row is None else base_bytes_per_row
    )
    return int(n_rows * raw_bytes_per_row * _OVERHEAD_MULTIPLIER)


class RamEstimate(NamedTuple):
    """Result of the RAM estimation."""

    safe_row_limit: int | None
    """Row limit that fits in RAM, or ``None`` if no limit is needed."""
    total_rows: int | None
    """Estimated total source rows, or ``None`` if unknown."""
    estimated_bytes: int
    """Estimated peak bytes across all training phases."""
    available_bytes: int
    """Available system RAM in bytes."""
    bytes_per_row: float
    """Estimated bytes per row (at peak phase)."""
    was_downsampled: bool
    """Whether a row limit was applied."""
    warning: str | None
    """Human-readable warning message if downsampled, else ``None``."""
    probe_columns: int = 0
    """Number of columns (from source metadata)."""


def _resolve_target_columns(
    graph: PipelineGraph,
    target_node_id: str,
    source: str,
) -> int | None:
    """Walk backwards from the target to determine column count.

    BFS from the target through ancestor nodes (respecting source
    pruning).  Returns the column count from the first node that has
    a definitive schema — either a ``selected_columns`` config or a
    source parquet file with readable metadata.
    """
    from collections import deque

    from haute._execute_lazy import _prune_live_switch_edges

    node_map = {n.id: n for n in graph.nodes}
    all_ids = set(node_map)
    pruned_edges = _prune_live_switch_edges(graph.edges, node_map, source)

    parents = build_parents_of(pruned_edges, all_ids)

    visited: set[str] = set()
    queue = deque([target_node_id])
    while queue:
        nid = queue.popleft()
        if nid in visited:
            continue
        visited.add(nid)
        node = node_map.get(nid)
        if node is None:
            continue

        # selected_columns on the node config gives an exact answer
        sel = node.data.config.get("selected_columns")
        if sel and isinstance(sel, list) and len(sel) > 0:
            return len(sel)

        # Source nodes — read parquet metadata
        if node.data.nodeType in (NodeType.API_INPUT, NodeType.DATA_SOURCE):
            meta = _source_metadata_for_node(node)
            if meta is not None:
                _rows, cols = meta
                return cols

        queue.extend(parents.get(nid, []))

    return None


def _edge_join_input_roles(
    node: GraphNode,
    parents: Mapping[str, Sequence[str]],
) -> tuple[str, str] | None:
    incoming = parents.get(node.id, ())
    base_input = node.data.config.get("baseInput")
    join_input = node.data.config.get("joinInput")
    if (
        len(incoming) != 2
        or not isinstance(base_input, str)
        or not isinstance(join_input, str)
        or base_input not in incoming
        or join_input not in incoming
        or base_input == join_input
    ):
        return None
    return base_input, join_input


def _dedupe_columns(columns: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for column in columns:
        if column in seen:
            continue
        seen.add(column)
        ordered.append(column)
    return tuple(ordered)


def _resolved_from_columns(columns: Iterable[str]) -> _ResolvedTargetColumns:
    deduped = _dedupe_columns(columns)
    return _ResolvedTargetColumns(
        columns=deduped,
        width_columns={column: column for column in deduped},
    )


def _resolved_from_source_metadata(
    meta: _DetailedSourceMetadata,
) -> _ResolvedTargetColumns:
    columns = _dedupe_columns(meta.columns)
    return _ResolvedTargetColumns(
        columns=columns,
        width_columns={column: meta.column_width_keys.get(column, column) for column in columns},
    )


def _dedupe_resolved_columns(
    columns: Iterable[tuple[str, str]],
) -> _ResolvedTargetColumns:
    seen: set[str] = set()
    ordered: list[str] = []
    width_columns: dict[str, str] = {}
    for output_column, width_column in columns:
        if output_column in seen:
            continue
        seen.add(output_column)
        ordered.append(output_column)
        width_columns[output_column] = width_column
    return _ResolvedTargetColumns(tuple(ordered), width_columns)


def _filter_resolved_columns(
    resolved: _ResolvedTargetColumns,
    selected: Iterable[str],
) -> _ResolvedTargetColumns:
    deduped = _dedupe_columns(selected)
    return _ResolvedTargetColumns(
        deduped,
        {column: resolved.width_columns.get(column, column) for column in deduped},
    )


def _has_selected_columns(config: Mapping[str, object]) -> bool:
    selected = config.get("selected_columns")
    return isinstance(selected, list) and len(selected) > 0


def _selected_column_names(config: Mapping[str, object]) -> tuple[str, ...] | None:
    selected = config.get("selected_columns")
    if not isinstance(selected, list) or len(selected) == 0:
        return None
    if not all(isinstance(column, str) for column in selected):
        return None
    return tuple(selected)


def _resolve_edge_join_columns(
    node: GraphNode,
    graph: PipelineGraph,
    source: str,
    parents: Mapping[str, Sequence[str]],
) -> _ResolvedTargetColumns | None:
    roles = _edge_join_input_roles(node, parents)
    if roles is None:
        return None
    base_input, join_input = roles
    kwargs = build_edge_join_kwargs(node.data.config)
    how = kwargs["how"]
    if how not in {"inner", "left"}:
        return None

    base_resolved = _resolve_target_columns_detail(graph, base_input, source)
    join_resolved = _resolve_target_columns_detail(graph, join_input, source)
    if base_resolved is None or join_resolved is None:
        return None

    base_columns = base_resolved.columns
    join_columns = join_resolved.columns
    _, join_key_columns = edge_join_key_columns_by_role(node.data.config)
    coalesce = kwargs.get("coalesce")
    if coalesce is not False:
        coalesced_join_keys = join_key_columns
    else:
        coalesced_join_keys = frozenset()

    suffix = str(kwargs["suffix"])
    output_columns = [
        (column, base_resolved.width_columns.get(column, column)) for column in base_columns
    ]
    base_set = set(base_columns)
    for column in join_columns:
        if column in coalesced_join_keys:
            continue
        width_column = join_resolved.width_columns.get(column, column)
        if column in base_set:
            output_columns.append((f"{column}{suffix}", width_column))
            continue
        output_columns.append((column, width_column))

    return _dedupe_resolved_columns(output_columns)


def _resolve_edge_join_column_names(
    node: GraphNode,
    graph: PipelineGraph,
    source: str,
    parents: Mapping[str, Sequence[str]],
) -> tuple[str, ...] | None:
    resolved = _resolve_edge_join_columns(node, graph, source, parents)
    return resolved.columns if resolved is not None else None


def _resolve_target_columns_detail(
    graph: PipelineGraph,
    target_node_id: str,
    source: str,
) -> _ResolvedTargetColumns | None:
    """Resolve target column names when config or parquet metadata exposes them."""
    from collections import deque

    from haute._execute_lazy import _prune_live_switch_edges

    node_map = {n.id: n for n in graph.nodes}
    all_ids = set(node_map)
    pruned_edges = _prune_live_switch_edges(graph.edges, node_map, source)

    parents = build_parents_of(pruned_edges, all_ids)

    visited: set[str] = set()
    queue = deque([target_node_id])
    while queue:
        nid = queue.popleft()
        if nid in visited:
            continue
        visited.add(nid)
        node = node_map.get(nid)
        if node is None:
            continue

        selected_columns = _selected_column_names(node.data.config)
        if _has_selected_columns(node.data.config) and selected_columns is None:
            return None

        if node.data.nodeType == NodeType.EDGE_JOIN:
            edge_join_columns = _resolve_edge_join_columns(node, graph, source, parents)
            if edge_join_columns is not None:
                if selected_columns is not None:
                    return _filter_resolved_columns(edge_join_columns, selected_columns)
                return edge_join_columns

        if selected_columns is not None:
            parent_ids = parents.get(nid, ())
            if len(parent_ids) == 1:
                parent_columns = _resolve_target_columns_detail(
                    graph,
                    parent_ids[0],
                    source,
                )
                if parent_columns is not None:
                    return _filter_resolved_columns(parent_columns, selected_columns)
            return _resolved_from_columns(selected_columns)

        if node.data.nodeType in (NodeType.API_INPUT, NodeType.DATA_SOURCE):
            meta = _detailed_source_metadata_for_node(node)
            if meta is not None:
                return _resolved_from_source_metadata(meta)

        queue.extend(parents.get(nid, []))

    return None


def _resolve_target_column_names(
    graph: PipelineGraph,
    target_node_id: str,
    source: str,
) -> tuple[str, ...] | None:
    """Resolve target column names when config or parquet metadata exposes them."""
    resolved = _resolve_target_columns_detail(graph, target_node_id, source)
    return resolved.columns if resolved is not None else None


def _edge_join_key_columns_on_path(
    graph: PipelineGraph,
    target_node_id: str,
    source: str,
) -> frozenset[str]:
    """Return materialized edgeJoin key output columns needed upstream."""
    from haute._execute_lazy import _prune_live_switch_edges
    from haute._topo import ancestors

    node_map = {n.id: n for n in graph.nodes}
    all_ids = set(node_map)
    pruned_edges = _prune_live_switch_edges(graph.edges, node_map, source)
    parents = build_parents_of(pruned_edges, all_ids)
    path_ids = ancestors(target_node_id, pruned_edges, all_ids) | {target_node_id}

    join_keys: set[str] = set()
    for nid in path_ids:
        node = node_map.get(nid)
        if node is None or node.data.nodeType != NodeType.EDGE_JOIN:
            continue
        base_keys, joined_keys = edge_join_key_columns_by_role(node.data.config)
        join_keys.update(base_keys)
        roles = _edge_join_input_roles(node, parents)
        if roles is None:
            join_keys.update(joined_keys)
            continue

        _, join_input = roles
        resolved = _resolve_target_columns_detail(graph, nid, source)
        join_resolved = _resolve_target_columns_detail(graph, join_input, source)
        if resolved is None or join_resolved is None:
            join_keys.update(joined_keys)
            continue

        joined_key_width_columns = {
            join_resolved.width_columns.get(column, column) for column in joined_keys
        }
        for column in resolved.columns:
            if resolved.width_columns.get(column, column) in joined_key_width_columns:
                join_keys.add(column)
    return frozenset(join_keys)


def _normalised_string_sequence(value: object) -> frozenset[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return frozenset()
    return frozenset(item for item in value if isinstance(item, str))


def _is_variable_width_arrow_type(arrow_type: str) -> bool:
    normalised = arrow_type.lower()
    return any(token in normalised for token in ("string", "utf8", "binary"))


def _source_column_base_widths(
    sources: tuple[_DetailedSourceMetadata, ...],
) -> dict[str, float]:
    widths: dict[str, float] = {}
    for meta in sources:
        for column, arrow_type in meta.columns.items():
            width = float(_BYTES_PER_COL)
            if meta.row_count > 0 and _is_variable_width_arrow_type(arrow_type):
                uncompressed_size = meta.column_uncompressed_size_bytes.get(column)
                if uncompressed_size is None and meta.column_count > 0:
                    uncompressed_size = int(meta.uncompressed_size_bytes / meta.column_count)
                if uncompressed_size is not None:
                    width = max(width, math.ceil(uncompressed_size / meta.row_count))
            width_key = meta.column_width_keys.get(column, column)
            widths[width_key] = max(widths.get(width_key, 0.0), width)
            widths[column] = max(widths.get(column, 0.0), width)
    return widths


def _estimate_base_bytes_per_row(
    n_columns: int,
    *,
    target_columns: tuple[str, ...] | None,
    target_width_columns: tuple[str, ...] | None,
    sources: tuple[_DetailedSourceMetadata, ...],
) -> float:
    if not target_columns:
        return float(n_columns * _BYTES_PER_COL)

    width_column_names = target_width_columns or target_columns
    if len(width_column_names) != len(target_columns):
        raise ValueError("target_width_columns must align with target_columns")

    widths = _source_column_base_widths(sources)
    return sum(widths.get(column, float(_BYTES_PER_COL)) for column in width_column_names)


def estimate_safe_training_rows(
    graph: PipelineGraph,
    target_node_id: str,
    build_node_fn: object | None = None,
    *,
    safety_factor: float = _RAM_SAFETY_FACTOR,
    preamble_ns: dict | None = None,
    source: str = "live",
) -> RamEstimate:
    """Estimate whether the full pipeline fits in RAM for training.

    1. Row count from source parquet metadata (source-aware).
    2. Column count resolved from the lazy plan at the training node
       (captures joins and transforms), minus excluded features.
    3. Peak memory estimate using the empirical 3× multiplier.

    Returns a :class:`RamEstimate` with the decision and warning message.
    """
    available = available_ram_bytes()

    # ── 1. Source metadata for row count ──────────────────────────────
    source_metadata = _detailed_ancestor_source_metadata(
        graph,
        target_node_id,
        source,
    )
    total_rows = source_metadata.row_count
    source_cols = source_metadata.column_count

    if total_rows is None:
        logger.info(
            "source_metadata_unavailable",
            total_rows=total_rows,
            target=target_node_id,
            source=source,
        )
        return RamEstimate(
            safe_row_limit=None,
            total_rows=None,
            estimated_bytes=0,
            available_bytes=available,
            bytes_per_row=0,
            was_downsampled=False,
            warning=None,
            probe_columns=0,
        )

    # ── 2. Column count at the training node ─────────────────────────
    #   Walk backwards through the graph from the target.  Returns the
    #   count from the first node with selected_columns or source metadata.
    n_columns = _resolve_target_columns(graph, target_node_id, source)

    if not n_columns:
        logger.info(
            "schema_unavailable",
            target=target_node_id,
            source=source,
        )
        return RamEstimate(
            safe_row_limit=None,
            total_rows=total_rows,
            estimated_bytes=0,
            available_bytes=available,
            bytes_per_row=0,
            was_downsampled=False,
            warning=None,
            probe_columns=0,
        )

    # Subtract excluded features — the pipeline now projects before
    # sinking, so excluded columns never enter the split or pools.
    node_map = {n.id: n for n in graph.nodes}
    target_node = node_map.get(target_node_id)
    excluded = (
        _normalised_string_sequence(target_node.data.config.get("exclude", []))
        if target_node
        else frozenset()
    )
    join_keys_on_path = _edge_join_key_columns_on_path(graph, target_node_id, source)
    preserved_excluded_join_keys = excluded & join_keys_on_path
    target_columns = _resolve_target_columns_detail(graph, target_node_id, source)
    if target_columns is not None:
        peak_columns = _filter_resolved_columns(
            target_columns,
            (
                column
                for column in target_columns.columns
                if column not in excluded or column in preserved_excluded_join_keys
            ),
        )
        peak_column_names = peak_columns.columns
        peak_width_column_names = tuple(
            peak_columns.width_columns.get(column, column) for column in peak_column_names
        )
        n_columns = max(len(peak_column_names), 1)
    else:
        n_columns = max(n_columns - len(excluded - preserved_excluded_join_keys), 1)
        peak_column_names = None
        peak_width_column_names = None

    logger.info(
        "schema_resolved",
        source_cols=source_cols,
        target_cols=n_columns,
        excluded=len(excluded),
        preserved_join_keys=sorted(preserved_excluded_join_keys),
    )

    # ── 3. Peak estimate ────────────────────────────────────────────
    base_bytes_per_row = _estimate_base_bytes_per_row(
        n_columns,
        target_columns=peak_column_names,
        target_width_columns=peak_width_column_names,
        sources=source_metadata.sources,
    )
    peak_bytes = _estimate_peak_bytes(
        total_rows,
        n_columns,
        base_bytes_per_row=base_bytes_per_row,
    )
    usable_ram = int(available * safety_factor)
    bytes_per_row = peak_bytes / total_rows if total_rows > 0 else 0

    logger.info(
        "ram_estimate",
        total_rows=total_rows,
        n_columns=n_columns,
        bytes_per_row=round(bytes_per_row, 1),
        peak_mb=round(peak_bytes / 1024**2, 1),
        available_mb=round(available / 1024**2, 1),
        usable_mb=round(usable_ram / 1024**2, 1),
    )

    # ── 5. Decision ──────────────────────────────────────────────────
    if peak_bytes <= usable_ram:
        return RamEstimate(
            safe_row_limit=None,
            total_rows=total_rows,
            estimated_bytes=peak_bytes,
            available_bytes=available,
            bytes_per_row=bytes_per_row,
            was_downsampled=False,
            warning=None,
            probe_columns=n_columns,
        )

    peak_per_row = peak_bytes / total_rows
    safe_rows = int(usable_ram / peak_per_row)
    safe_rows = max(safe_rows, _MIN_SAFE_ROWS)

    warning = (
        f"Dataset downsampled to {safe_rows:,} of {total_rows:,} rows to fit in "
        f"available RAM ({available / 1024**3:.1f} GB). "
        f"Estimated peak training memory: {peak_bytes / 1024**3:.1f} GB."
    )
    logger.warning("downsampling", safe_rows=safe_rows, total_rows=total_rows, warning=warning)

    return RamEstimate(
        safe_row_limit=safe_rows,
        total_rows=total_rows,
        estimated_bytes=peak_bytes,
        available_bytes=available,
        bytes_per_row=bytes_per_row,
        was_downsampled=True,
        warning=warning,
        probe_columns=n_columns,
    )
