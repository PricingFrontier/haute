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

from pathlib import Path
from typing import NamedTuple

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
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass

    try:
        import os

        pages: int = os.sysconf("SC_AVPHYS_PAGES")  # type: ignore[attr-defined]
        page_size: int = os.sysconf("SC_PAGE_SIZE")  # type: ignore[attr-defined]
        if pages > 0 and page_size > 0:
            return pages * page_size
    except (AttributeError, ValueError):
        pass

    import sys

    if sys.platform == "win32":
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
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(  # type: ignore[attr-defined]
                ctypes.byref(mem)
            ):
                return int(mem.ullAvailPhys)
        except (OSError, AttributeError, ImportError):
            pass

    return 4 * 1024**3


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


# Bytes per column for the analytical estimate.  Training features are
# cast to Float32 (4 bytes) in _build_pool, but the Polars DataFrame
def _parquet_metadata(path: str) -> tuple[int, int]:
    """Return (row_count, column_count) from parquet footer metadata."""
    meta = read_parquet_metadata(Path(path))
    return meta["row_count"], meta["column_count"]


def _count_source_rows_for_node(node: GraphNode) -> int | None:
    """Row count for a single source node (parquet metadata or line count)."""
    config = node.data.config
    node_type = node.data.nodeType

    try:
        if node_type == NodeType.API_INPUT:
            path = config.get("path", "")
            if path.endswith((".json", ".jsonl")):
                from haute._json_flatten import json_cache_info

                info = json_cache_info(path, schema=config.get("flattenSchema"))
                if info is not None:
                    return info["row_count"]
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
    config = node.data.config
    node_type = node.data.nodeType

    try:
        path = config.get("path", "")
        if not path:
            return None

        if node_type == NodeType.API_INPUT:
            if path.endswith((".json", ".jsonl")):
                from haute._json_flatten import json_cache_info

                info = json_cache_info(path, schema=config.get("flattenSchema"))
                if info is not None:
                    return info["row_count"], info["column_count"]
                return None
            if Path(path).exists():
                return _parquet_metadata(path)
            return None

        if node_type == NodeType.DATA_SOURCE:
            if config.get("sourceType", "flat_file") == "databricks":
                return None
            if Path(path).exists() and path.endswith(".parquet"):
                return _parquet_metadata(path)
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
    from haute._execute_lazy import _prune_live_switch_edges
    from haute._topo import ancestors

    node_map = {n.id: n for n in graph.nodes}
    all_ids = set(node_map)

    pruned_edges = _prune_live_switch_edges(graph.edges, node_map, source)
    ancestor_ids = ancestors(target_node_id, pruned_edges, all_ids)

    max_rows: int | None = None
    max_cols: int = 0

    for nid in ancestor_ids:
        node = node_map.get(nid)
        if node is None:
            continue
        if node.data.nodeType not in (NodeType.API_INPUT, NodeType.DATA_SOURCE):
            continue
        meta = _source_metadata_for_node(node)
        if meta is not None:
            rows, cols = meta
            if max_rows is None or rows > max_rows:
                max_rows = rows
            max_cols = max(max_cols, cols)

    return max_rows, max_cols


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


def _estimate_peak_bytes(n_rows: int, n_cols: int) -> int:
    """Estimate peak RAM for the full training lifecycle."""
    return int(n_rows * n_cols * _BYTES_PER_COL * _OVERHEAD_MULTIPLIER)


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
    total_rows, source_cols = _ancestor_source_metadata(
        graph,
        target_node_id,
        source,
    )

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
    n_excluded = len(target_node.data.config.get("exclude", [])) if target_node else 0
    n_columns = max(n_columns - n_excluded, 1)

    logger.info(
        "schema_resolved",
        source_cols=source_cols,
        target_cols=n_columns,
        excluded=n_excluded,
    )

    # ── 3. Peak estimate ────────────────────────────────────────────
    peak_bytes = _estimate_peak_bytes(total_rows, n_columns)
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
