"""Graph executor: run a pipeline graph JSON dynamically.

Takes a React Flow graph (nodes + edges) and executes it as a real
Polars pipeline, without needing a saved .py file.

Node functions produce LazyFrames.  Preview and trace use eager
single-pass execution with per-graph caching so repeated clicks
don't re-execute the pipeline.  Source nodes are capped at
row_limit rows.  Sink and CLI paths use lazy execution so Polars
can optimise the full plan end-to-end.
"""

from __future__ import annotations

import ast as _ast
import contextlib
import ctypes
import functools
import gc
import importlib as _importlib
import inspect
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

import polars as pl

from haute._builders import (  # noqa: F401
    NodeBuildContext,
    NodeBuilder,
    _apply_online,
    _apply_ratebook,
    _build_node_fn,
    _dispatch_apply,
    _passthrough_fn,
    resolve_instance_node,
)
from haute._cache import (
    GraphFingerprintMemo,
    preamble_execution_fingerprint,
    preamble_imports_utility,
)
from haute._execution_context import ExecutionContext, ExecutionProfile
from haute._fingerprint_cache import FingerprintCache
from haute._logging import get_logger
from haute._rating import _apply_banding  # noqa: F401 — re-exported for tests
from haute._registry import ensure_registry_ready
from haute._sandbox import safe_globals, validate_user_code
from haute._types import NodeData
from haute.graph_utils import (
    HauteError,
    NodeType,
    PipelineGraph,
    _execute_eager_core,
    _execute_lazy,
    _prune_live_switch_edges,
    _resolve_sink_path,
    ancestors,
    graph_fingerprint,
)
from haute.schemas import (
    ColumnInfo,
    ExecutionMetricsPayload,
    NodeResult,
    SchemaWarning,
    SinkResponse,
)

logger = get_logger(component="executor")

# Validate the registry at executor-import time so every production code
# path that executes a graph — preview, trace, deploy scoring, training,
# the optimiser — trips the missing-builder check, not just the codegen
# path.  ``ensure_registry_ready`` is idempotent: subsequent calls from
# ``haute.codegen`` hit the ``sys.modules`` cache and the validator
# short-circuits on an already-populated registry.
ensure_registry_ready()

# ── Default constants ─────────────────────────────────────────────
_MAX_PREVIEW_ROWS = 10_000  # safety cap for execute_graph JSON payload


def _positive_int_from_env(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive integer") from exc
    if value < 1:
        raise RuntimeError(f"{name} must be a positive integer")
    return value


PREVIEW_CACHE_MAX_BYTES = _positive_int_from_env(
    "HAUTE_PREVIEW_CACHE_MAX_BYTES",
    64 * 1024 * 1024,
)
"""Maximum retained bytes for materialized preview DataFrames."""
PREVIEW_MAX_CELLS = _positive_int_from_env("HAUTE_PREVIEW_MAX_CELLS", 50_000)
"""Maximum cells converted to JSON rows for a single node preview."""
PREVIEW_INITIAL_COLUMN_LIMIT = _positive_int_from_env(
    "HAUTE_PREVIEW_INITIAL_COLUMN_LIMIT",
    200,
)
"""Maximum first-click preview columns when the frontend has no cached schema."""

# Module-level toggle for column-contract enforcement.  ``True`` by
# default: contract mismatches should fail loudly.  Benchmarks may
# temporarily flip this to ``False`` to measure overhead.
# ``execute_graph(..., enforce_contracts=...)`` is the
# preferred switch for normal code paths; the module flag is the
# fallback for callers (like the overhead benchmark in
# ``test_column_contracts_adoption.py``) that need to toggle it
# without threading the kwarg through their call chain.
ENFORCE_CONTRACTS: bool = True

# Lock to prevent concurrent module eviction + re-import in _compile_preamble.
# Without this, two threads (e.g. preview + estimate) can race: one evicts
# "utility" from sys.modules while the other is mid-import, causing a KeyError
# inside importlib._bootstrap._load_unlocked.
_preamble_lock = threading.Lock()

# ``sys.path`` and ``sys.modules`` are process-global.  Utility-importing
# preambles hold ``_preamble_lock`` for the full eviction/import/exec window.
# Non-utility preambles still take the same lock for their short path
# reprioritisation so they cannot change import precedence while a utility
# preamble is paused between path setup and ``exec``.


def _pipeline_dir(graph: PipelineGraph) -> Path | None:
    """Derive the pipeline file's parent directory from ``graph.source_file``.

    Returns *None* when the graph has no source file metadata (e.g.
    dynamically constructed graphs in tests).
    """
    sf = graph.source_file
    if not sf:
        return None
    p = Path(sf)
    if not p.is_absolute():
        p = Path.cwd() / p
    return p.resolve().parent


class PreambleError(HauteError):
    """Raised when the preamble (imports / utility code) fails to compile."""

    def __init__(self, message: str, source_line: int | None = None):
        super().__init__(message)
        self.source_line = source_line


class PreviewProjectionError(ValueError):
    """Requested preview columns cannot be projected from the target DataFrame."""


def _execution_stage(
    execution_context: ExecutionContext | None,
    name: str,
) -> contextlib.AbstractContextManager[None]:
    if execution_context is None:
        return contextlib.nullcontext()
    return execution_context.stage(name)


# Cache compiled preamble results by (content, pipeline_dir, execution
# fingerprint) so unchanged preambles (common during training / optimiser
# runs where the preamble doesn't change between invocations) skip the
# expensive module eviction + re-import cycle.  ``functools.lru_cache`` is
# C-implemented, gives O(1) eviction, and ships with ``cache_info()``
# diagnostics for free.

_DANGEROUS_MODULES = frozenset(
    {
        "os",
        "sys",
        "subprocess",
        "shutil",
        "signal",
        "ctypes",
        "importlib",
    }
)
_DANGEROUS_MODULE_OBJECTS = frozenset(
    {
        os,
        os.path,
        sys,
        subprocess,
        shutil,
        signal,
        ctypes,
        _importlib,
    }
)
_DANGEROUS_MODULE_NAMES = frozenset(m.__name__ for m in _DANGEROUS_MODULE_OBJECTS)
_PREAMBLE_NO_REFRESH_FINGERPRINT = "no-refresh"


def _is_dangerous_preamble_binding(value: Any) -> bool:
    if inspect.ismodule(value):
        module_name = value.__name__
        return (
            value in _DANGEROUS_MODULE_OBJECTS
            or module_name in _DANGEROUS_MODULE_NAMES
            or module_name.split(".", 1)[0] in _DANGEROUS_MODULES
        )

    module = inspect.getmodule(value)
    module_name = (
        module.__name__ if module is not None else getattr(value, "__module__", "")
    ) or ""
    return (
        module in _DANGEROUS_MODULE_OBJECTS
        or module_name in _DANGEROUS_MODULE_NAMES
        or module_name.split(".", 1)[0] in _DANGEROUS_MODULES
    )


_polars_config_lock = threading.Lock()


def _utility_module_candidates(pipeline_dir_str: str | None) -> list[Path]:
    bases: list[Path] = []
    if pipeline_dir_str is not None:
        bases.append(Path(pipeline_dir_str))
    bases.append(Path.cwd().resolve())

    seen: set[Path] = set()
    candidates: list[Path] = []
    for base in bases:
        for candidate in (base / "utility.py", base / "utility"):
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            candidates.append(resolved)
    return candidates


def _evict_utility_import_state(pipeline_dir_str: str | None) -> None:
    """Discard utility import state before compiling a changed preamble key."""
    _importlib.invalidate_caches()

    for mod_name in [k for k in sys.modules if k == "utility" or k.startswith("utility.")]:
        del sys.modules[mod_name]

    for candidate in _utility_module_candidates(pipeline_dir_str):
        if candidate.is_file():
            pycache_dir = candidate.parent / "__pycache__"
            if pycache_dir.is_dir():
                for pyc in pycache_dir.glob(f"{candidate.stem}.*.pyc"):
                    pyc.unlink()
        elif candidate.is_dir():
            for pycache_dir in candidate.rglob("__pycache__"):
                if pycache_dir.is_dir():
                    for pyc in pycache_dir.glob("*.pyc"):
                        pyc.unlink()


def _prioritise_preamble_import_paths(
    cwd: str,
    pipeline_dir_str: str | None,
) -> None:
    """Keep import resolution aligned with preamble dependency fingerprints."""
    desired = [cwd]
    if pipeline_dir_str is not None:
        desired.insert(0, pipeline_dir_str)

    for path in reversed(desired):
        sys.path[:] = [existing for existing in sys.path if existing != path]
        sys.path.insert(0, path)


def _preamble_has_imports(preamble: str) -> bool:
    """Return whether preamble execution can consult import resolution state.

    Kept as a small diagnostic/test helper. The hot compile path deliberately
    does not call this before the ``lru_cache`` lookup.
    """
    try:
        tree = _ast.parse(preamble)
    except SyntaxError:
        return True
    return any(isinstance(node, (_ast.Import, _ast.ImportFrom)) for node in _ast.walk(tree))


def _exec_preamble_namespace(preamble: str) -> dict[str, Any]:
    ns = safe_globals(pl=pl, allow_imports=True)
    base_keys = set(ns.keys())
    try:
        exec(preamble, ns)  # noqa: S102  — single dict = shared globals
    except Exception as exc:
        # Extract the most useful line number and source file from
        # the traceback or exception attributes.
        import traceback as _tb
        from pathlib import Path as _Path

        source_line: int | None = None
        source_file: str | None = None

        # SyntaxError carries .filename and .lineno directly
        if isinstance(exc, SyntaxError) and exc.filename:
            source_file = exc.filename
            source_line = exc.lineno

        # For runtime errors, walk the traceback to find the utility frame
        if source_file is None and exc.__traceback__:
            for frame in reversed(_tb.extract_tb(exc.__traceback__)):
                if "utility" in frame.filename:
                    source_line = frame.lineno
                    source_file = frame.filename
                    break
                if frame.filename == "<string>":
                    source_line = frame.lineno
                    break

        msg = f"Import/preamble error: {exc}"
        if source_file and source_file != "<string>":
            try:
                rel: str | Path = _Path(source_file).relative_to(_Path.cwd())
            except ValueError:
                rel = source_file
            msg = f"Error in {rel} line {source_line}: {exc}"
        elif source_line:
            msg = f"Preamble line {source_line}: {exc}"

        raise PreambleError(msg, source_line=source_line) from exc

    return {
        k: v for k, v in ns.items() if k not in base_keys and not _is_dangerous_preamble_binding(v)
    }


@functools.lru_cache(maxsize=128)
def _compile_preamble_cached(
    preamble: str,
    cwd: str,
    pipeline_dir_str: str | None,
    _execution_fingerprint: str,
) -> dict[str, Any]:
    """Pure cache-facing worker — compiles preamble bytes into a namespace.

    Keyed on ``(preamble, cwd, pipeline_dir_str, _execution_fingerprint)``
    so different pipelines sharing an identical preamble text but different
    utility contents still get distinct cache slots when callers request
    dependency refresh.

    ``pipeline_dir_str`` is a normalised ``str`` (or ``None``) rather than a
    ``Path`` so that ``lru_cache``'s hash lookup produces the same key for
    ``Path("/x")`` and ``"/x"`` — normalisation happens at the public
    entry point.

    All cache misses run under ``_preamble_lock`` for the entire
    path-prioritisation/import/exec window. Even preambles without literal
    import statements execute with ``allow_imports=True`` and can consult
    process-global import state via helpers such as ``__import__``.
    """
    validate_user_code(preamble, allow_imports=True)
    imports_utility = preamble_imports_utility(preamble)
    with _preamble_lock:
        _prioritise_preamble_import_paths(cwd, pipeline_dir_str)
        if imports_utility:
            # Evict cached utility modules so digest-keyed cache misses pick up
            # GUI edits. Clearing matching pyc files prevents same-size,
            # same-mtime edits from being hidden by timestamp-based bytecode
            # validation.
            _evict_utility_import_state(pipeline_dir_str)
        return _exec_preamble_namespace(preamble)


def _compile_preamble(
    preamble: str,
    *,
    force_refresh: bool = True,
    pipeline_dir: str | Path | None = None,
    memo: GraphFingerprintMemo | None = None,
) -> dict[str, Any]:
    """Compile user-defined preamble code into a namespace dict.

    The preamble (helper functions, constants, lambdas) is defined at the
    top of a pipeline file between imports and the first
    ``@pipeline.<type>`` decorator.  This compiles it once and returns a
    dict of bindings that can be injected into ``_exec_user_code`` via
    ``extra_ns``.

    Uses a single dict for globals/locals so preamble functions can call
    each other (they share the same ``__globals__``).

    When *force_refresh* is ``True`` (default), dependency fingerprints are
    recomputed before lookup so edits to utility modules in the GUI are
    picked up without clearing unrelated cached preambles. Unchanged
    preamble/utility inputs reuse the cached namespace. When
    *force_refresh* is ``False`` (e.g. optimiser / sink paths that run in
    tight loops), the caller is explicitly promising that imported helper
    files are stable for the loop; the cache key uses the preamble text,
    cwd, pipeline directory, and a stable no-refresh marker so hot hits
    skip validation, AST walking, and utility-file hashing entirely.

    Caching diagnostics are exposed directly on this function via
    ``_compile_preamble.cache_info()`` and ``_compile_preamble.cache_clear()``
    — delegating to the underlying ``functools.lru_cache`` wrapper.

    Raises ``PreambleError`` with a human-readable message and optional
    source line number when the preamble fails to execute (e.g. a utility
    module has a NameError).
    """
    # Short-circuit empty / whitespace preambles BEFORE touching the cache
    # — empty preambles are common and shouldn't evict cache entries.
    if not preamble or not preamble.strip():
        return {}

    # Ensure project root is importable so `from utility.xxx import …` works
    # even when the server process was spawned by uvicorn reload.  We add
    # both cwd and the pipeline's parent directory because the ``utility/``
    # folder may live next to the pipeline file (e.g. inside a ``rating/``
    # subfolder) rather than at the project root.  These inserts are
    # idempotent (gated on ``not in sys.path``) so the list doesn't grow
    # on every call.
    import os

    cwd = os.getcwd()

    # Normalise pipeline_dir to a string at the boundary so lru_cache's
    # argument-hashing treats ``Path("/x")`` and ``"/x"`` identically.
    pipeline_dir_str: str | None = None
    if pipeline_dir is not None:
        pipeline_dir_str = str(Path(pipeline_dir).resolve())

    if force_refresh:
        execution_fingerprint = preamble_execution_fingerprint(
            preamble,
            pipeline_dir=pipeline_dir_str,
            memo=memo,
        )
        if execution_fingerprint is None:
            raise RuntimeError("non-empty preamble did not produce an execution fingerprint")
    else:
        execution_fingerprint = _PREAMBLE_NO_REFRESH_FINGERPRINT

    return _compile_preamble_cached(
        preamble,
        cwd,
        pipeline_dir_str,
        execution_fingerprint,
    )


# Expose the cache diagnostics (``cache_info``, ``cache_clear``) directly on
# the public wrapper so callers and tests can inspect the cache without
# having to know about the internal ``_compile_preamble_cached`` symbol.
# Using ``cast`` keeps mypy happy about attaching non-standard attributes
# to a plain function.
_compile_preamble.cache_info = _compile_preamble_cached.cache_info  # type: ignore[attr-defined]
_compile_preamble.cache_clear = _compile_preamble_cached.cache_clear  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Preview cache — same principle as the trace cache in trace.py.
# The pipeline doesn't change between node clicks — only the target node
# changes.  Cache the materialized DataFrames so clicking different nodes
# is instant instead of re-executing model scoring on 678K rows each time.
# ---------------------------------------------------------------------------


def _estimate_preview_cache_entry_bytes(entry: dict[str, Any]) -> int:
    """Estimate retained bytes for a preview cache entry.

    Preview entries intentionally cache materialized Polars DataFrames.
    Polars exposes a deterministic retained-size estimate for those
    frames, and unexpected payload shapes fail loudly so accounting
    regressions cannot hide behind a default weight.
    """
    outputs = entry.get("eager_outputs", {})
    if not isinstance(outputs, dict):
        raise TypeError("preview cache entry eager_outputs must be a dict")

    total = 0
    for node_id, value in outputs.items():
        if not isinstance(value, pl.DataFrame):
            raise TypeError(
                "preview cache size accounting expected Polars DataFrame "
                f"for node {node_id!r}, got {type(value).__name__}"
            )
        size = value.estimated_size()
        if type(size) is not int or size < 0:
            raise ValueError(
                f"Polars estimated_size for node {node_id!r} returned invalid value {size!r}"
            )
        total += size
    return total


def _preview_row_limit_for_width(max_preview_rows: int, column_count: int) -> int:
    """Return the row limit that keeps preview JSON conversion bounded."""
    if max_preview_rows < 0:
        raise ValueError(f"max_preview_rows must be >= 0, got {max_preview_rows}")
    if column_count < 0:
        raise ValueError(f"column_count must be >= 0, got {column_count}")
    if PREVIEW_MAX_CELLS < 1:
        raise RuntimeError(f"PREVIEW_MAX_CELLS must be >= 1, got {PREVIEW_MAX_CELLS}")
    if column_count == 0:
        return max_preview_rows
    return min(max_preview_rows, PREVIEW_MAX_CELLS // column_count)


def _preview_projection_columns(
    df: pl.DataFrame,
    requested_preview_columns: list[str] | None,
) -> list[str]:
    if requested_preview_columns is None:
        return list(df.columns)
    if not requested_preview_columns:
        raise PreviewProjectionError("requested_preview_columns must contain at least one column")

    projected: list[str] = []
    seen: set[str] = set()
    for column in requested_preview_columns:
        if not column:
            raise PreviewProjectionError("requested_preview_columns cannot contain empty names")
        if column in seen:
            continue
        seen.add(column)
        projected.append(column)

    missing = [column for column in projected if column not in df.columns]
    if missing:
        raise PreviewProjectionError(
            "Requested preview column(s) not found on target: " + ", ".join(missing)
        )
    return projected


_OPTIMISER_APPLY_DEFAULT_VALUE_COLUMNS = frozenset({"optimal_scenario_value", "optimised_factor"})


def _normalise_requested_preview_columns(
    node_data: NodeData,
    df: pl.DataFrame,
    requested_preview_columns: list[str] | None,
) -> list[str] | None:
    if requested_preview_columns is None:
        return None
    if node_data.nodeType != NodeType.OPTIMISER_APPLY:
        return requested_preview_columns

    configured_column = node_data.config.get("optimised_value_column", "")
    if (
        not configured_column
        or configured_column in _OPTIMISER_APPLY_DEFAULT_VALUE_COLUMNS
        or configured_column not in df.columns
    ):
        return requested_preview_columns

    return [
        configured_column
        if column in _OPTIMISER_APPLY_DEFAULT_VALUE_COLUMNS and column not in df.columns
        else column
        for column in requested_preview_columns
    ]


def _normalise_requested_preview_columns_for_execution(
    node_data: NodeData,
    requested_preview_columns: list[str] | None,
) -> list[str] | None:
    """Normalise request aliases before eager projection has a DataFrame.

    ``_normalise_requested_preview_columns`` can inspect the collected
    target frame.  The eager executor needs an earlier, schema-only seed, so
    it applies only config-derived aliases whose target names are explicit.
    """
    if requested_preview_columns is None:
        return None
    if node_data.nodeType != NodeType.OPTIMISER_APPLY:
        return requested_preview_columns

    configured_column = node_data.config.get("optimised_value_column", "")
    if not configured_column or configured_column in _OPTIMISER_APPLY_DEFAULT_VALUE_COLUMNS:
        return requested_preview_columns

    columns: list[str] = []
    seen: set[str] = set()
    for column in requested_preview_columns:
        candidates = (
            (column, configured_column)
            if column in _OPTIMISER_APPLY_DEFAULT_VALUE_COLUMNS
            else (column,)
        )
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            columns.append(candidate)
    return columns


def _preview_required_columns_by_node(
    graph: PipelineGraph,
    target_node_id: str | None,
    requested_preview_columns: list[str] | None,
) -> dict[str, list[str]] | None:
    """Return eager projection seeds for a target preview-column request."""
    if target_node_id is None or requested_preview_columns is None:
        return None
    if not requested_preview_columns:
        raise PreviewProjectionError("requested_preview_columns must contain at least one column")
    if any(not isinstance(column, str) or not column for column in requested_preview_columns):
        raise PreviewProjectionError("requested_preview_columns cannot contain empty names")

    node = graph.node_map.get(target_node_id)
    if node is None:
        return None

    columns = _normalise_requested_preview_columns_for_execution(
        node.data,
        requested_preview_columns,
    )
    if not columns:
        return None
    return {target_node_id: columns}


def _preview_projection_cache_suffix(
    graph: PipelineGraph,
    target_node_id: str | None,
    requested_preview_columns: list[str] | None,
    *,
    target_preview_only: bool = False,
    initial_column_limit: int | None = None,
) -> str:
    """Cache-key suffix for projected preview materialisations."""
    parts: list[str] = []
    if target_preview_only and target_node_id is not None:
        parts.append(f":preview_target_only={target_node_id!r}")
        if requested_preview_columns is None:
            parts.append(f":initial_col_limit={initial_column_limit!r}")
    if target_node_id is not None and requested_preview_columns is not None:
        parts.append(f":preview_target={target_node_id!r}")
        parts.append(f":preview_cols={tuple(requested_preview_columns)!r}")
    return "".join(parts)


def _cache_has_required_materialization(
    *,
    graph: PipelineGraph,
    target_node_id: str | None,
    requested_preview_columns: list[str] | None,
    required_materialized_nodes: set[str],
    materialize_column_limits_by_node: dict[str, int] | None,
    cached_outputs: dict[str, pl.DataFrame],
    cached_output_columns: dict[str, list[tuple[str, str]]],
) -> bool:
    node_map = graph.node_map
    column_limits = materialize_column_limits_by_node or {}
    for node_id in required_materialized_nodes:
        df = cached_outputs.get(node_id)
        if df is None:
            return False
        full_columns = [name for name, _dtype in cached_output_columns.get(node_id, [])]

        if requested_preview_columns is not None and node_id == target_node_id:
            node = node_map.get(node_id)
            if node is None:
                return False
            try:
                required_columns = _preview_projection_columns(
                    df,
                    _normalise_requested_preview_columns(
                        node.data,
                        df,
                        requested_preview_columns,
                    ),
                )
            except PreviewProjectionError:
                return False
        elif node_id in column_limits and full_columns:
            required_columns = full_columns[: column_limits[node_id]]
        else:
            required_columns = full_columns or list(df.columns)

        if not set(required_columns) <= set(df.columns):
            return False
    return True


_preview_cache = FingerprintCache(
    slots=(
        "eager_outputs",
        "errors",
        "order",
        "timings",
        "memory_bytes",
        "error_lines",
        "available_columns",
        "output_columns",
    ),
    max_bytes=PREVIEW_CACHE_MAX_BYTES,
    size_of=_estimate_preview_cache_entry_bytes,
    size_sensitive_slots=("eager_outputs",),
)


def _extract_column_refs(config: dict[str, Any]) -> set[str]:
    """Extract column names referenced in a node's config.

    Only includes columns that are READ from upstream (not created/output columns).
    Returns an empty set for configs with no column references.
    """
    refs: set[str] = set()

    # selected_columns: list[str] — on any node type
    for col in config.get("selected_columns", []) or []:
        if isinstance(col, str) and col:
            refs.add(col)

    # target, weight, offset: str — on modelling nodes
    for key in ("target", "weight", "offset"):
        val = config.get(key, "")
        if isinstance(val, str) and val:
            refs.add(val)

    # exclude: list[str] — on modelling nodes
    for col in config.get("exclude", []) or []:
        if isinstance(col, str) and col:
            refs.add(col)

    # banding: factors is a list of dicts, each with a 'column' key
    for factor in config.get("factors", []) or []:
        col = factor.get("column", "") if isinstance(factor, dict) else ""
        if isinstance(col, str) and col:
            refs.add(col)

    # rating steps: tables is a list of dicts, each with a 'factors' key (list of str)
    for table in config.get("tables", []) or []:
        if not isinstance(table, dict):
            continue
        for col in table.get("factors", []) or []:
            if isinstance(col, str) and col:
                refs.add(col)

    # Exclude output columns — they are created, not read
    refs.discard(config.get("output_column", ""))
    refs.discard(config.get("outputColumn", ""))

    return refs


def _result_order_for_target(
    graph: PipelineGraph,
    order: list[str],
    target_node_id: str | None,
    source: str,
) -> list[str]:
    """Return node IDs whose result payloads are relevant to this request."""
    if target_node_id is None:
        return order
    if target_node_id not in graph.node_map:
        return []

    edges = _prune_live_switch_edges(graph.edges, graph.node_map, source)
    needed = ancestors(target_node_id, edges, set(graph.node_map))
    return [nid for nid in order if nid in needed]


def execute_graph(
    graph: PipelineGraph,
    target_node_id: str | None = None,
    row_limit: int | None = None,
    max_preview_rows: int = _MAX_PREVIEW_ROWS,
    source: str = "live",
    enforce_contracts: bool | None = None,
    *,
    target_preview_only: bool = False,
    requested_preview_columns: list[str] | None = None,
    include_schema_metadata: bool = False,
    execution_context: ExecutionContext | None = None,
) -> dict[str, NodeResult]:
    """Execute a graph and return per-node results.

    Uses eager single-pass execution with a single-entry cache so
    clicking different nodes doesn't re-execute the full pipeline.

    Args:
        graph: React Flow graph with "nodes" and "edges".
        target_node_id: If set, only execute nodes up to (and including) this node.
        row_limit: If set, apply .head(row_limit) to source nodes so only
                   that many rows flow through the pipeline.
        max_preview_rows: Max rows to include in the JSON preview payload.
        enforce_contracts: If ``True``, every node's column contract is
            asserted at the input and output boundaries.  Default
            (``None``) falls back to the module-level
            :data:`ENFORCE_CONTRACTS` flag (itself ``True`` by default),
            so contract violations surface as ``ContractMismatchError``.
            Pass ``False`` to run without the check — the benchmark
            uses this to measure overhead; production callers should
            leave it at the default.
        target_preview_only: If ``True`` and ``target_node_id`` is set,
            build JSON preview rows only for the requested target and omit
            downstream result payloads from broader cache hits. The GUI
            preview route uses this to avoid serialising unused cached
            DataFrames when switching from a downstream result panel back
            to an upstream table.
        include_schema_metadata: If ``True`` with ``target_preview_only``,
            include schema/status/timing metadata for relevant non-materialised
            ancestors while still building preview rows only for the target.

    Returns:
        Dict mapping node_id → {
            "status": "ok" | "error",
            "row_count": int,
            "columns": [...],
            "preview": [...],
            "error": str | None,
        }
    """
    if enforce_contracts is None:
        enforce_contracts = ENFORCE_CONTRACTS
    if not graph.nodes:
        return {}

    # Include enforce_contracts in the cache key so a toggle flips
    # between distinct cache slots instead of serving a stale entry
    # computed under a different enforcement mode.  Without this, the
    # contract-overhead benchmark measures cache-hit-vs-cache-hit.
    fingerprint_memo = GraphFingerprintMemo()
    preview_required_columns = _preview_required_columns_by_node(
        graph,
        target_node_id,
        requested_preview_columns,
    )
    preview_materialize_node_ids: frozenset[str] | None = (
        frozenset({target_node_id}) if target_preview_only and target_node_id is not None else None
    )
    preview_materialize_column_limits: dict[str, int] | None = (
        {target_node_id: PREVIEW_INITIAL_COLUMN_LIMIT}
        if (
            target_preview_only and target_node_id is not None and requested_preview_columns is None
        )
        else None
    )
    preview_cache_suffix = _preview_projection_cache_suffix(
        graph,
        target_node_id,
        requested_preview_columns,
        target_preview_only=target_preview_only,
        initial_column_limit=(
            PREVIEW_INITIAL_COLUMN_LIMIT
            if target_preview_only and requested_preview_columns is None
            else None
        ),
    )
    fp = graph_fingerprint(
        graph,
        (f"{row_limit}:{source}:contracts={int(enforce_contracts)}{preview_cache_suffix}"),
        memo=fingerprint_memo,
    )

    errors: dict[str, str] = {}
    error_lines: dict[str, int] = {}
    avail_cols: dict[str, list[tuple[str, str]]] = {}
    output_cols: dict[str, list[tuple[str, str]]] = {}
    preview_entry_pinned = False

    # Check if we can extend the cache (same graph, new target is a superset)
    with _execution_stage(execution_context, "preview_cache_lookup"):
        cached = _preview_cache.try_get(fp)
    if cached is not None:
        prev_outputs = cached["eager_outputs"]
        cached_order = cached["order"]
        required_materialized_nodes = (
            set(cached_order)
            if preview_materialize_node_ids is None
            else set(preview_materialize_node_ids)
        )
        cache_satisfies_request = (
            (target_node_id is None or target_node_id in prev_outputs)
            and required_materialized_nodes <= set(prev_outputs)
            and _cache_has_required_materialization(
                graph=graph,
                target_node_id=target_node_id,
                requested_preview_columns=requested_preview_columns,
                required_materialized_nodes=required_materialized_nodes,
                materialize_column_limits_by_node=preview_materialize_column_limits,
                cached_outputs=prev_outputs,
                cached_output_columns=cached["output_columns"],
            )
        )
        if cache_satisfies_request:
            # Full cache hit — all required nodes already materialised
            with _execution_stage(execution_context, "preview_cache_hit"):
                logger.debug(
                    "preview_cache_hit",
                    fingerprint=fp[:8],
                    target=target_node_id,
                    cached_nodes=len(prev_outputs),
                )
                eager_outputs = prev_outputs
                order = cached_order
                errors = cached["errors"]
                timings = cached["timings"]
                memory_bytes = cached["memory_bytes"]
                error_lines = cached["error_lines"]
                avail_cols = cached["available_columns"]
                output_cols = cached["output_columns"]
        else:
            # Partial hit — extend with newly-needed nodes
            logger.debug(
                "preview_cache_extend",
                fingerprint=fp[:8],
                target=target_node_id,
                cached_nodes=len(prev_outputs),
            )
            with _execution_stage(execution_context, "preview_cache_extend"):
                (
                    raw_outputs,
                    order,
                    errors,
                    timings,
                    memory_bytes,
                    error_lines,
                    avail_cols,
                    output_cols,
                ) = _eager_execute(
                    graph,
                    target_node_id,
                    row_limit,
                    source=source,
                    enforce_contracts=enforce_contracts,
                    fingerprint_memo=fingerprint_memo,
                    required_columns_by_node=preview_required_columns,
                    materialize_node_ids=preview_materialize_node_ids,
                    materialize_column_limits_by_node=preview_materialize_column_limits,
                    execution_context=execution_context,
                )
            eager_outputs = {k: v for k, v in raw_outputs.items() if v is not None}
            # Fresh eager outputs win over prev_outputs for any overlap:
            # the prev_outputs may contain stale entries for nodes that were
            # re-executed (e.g. delete-then-re-add with same id) and serving
            # the stale DataFrame would hide legitimate config changes from
            # the caller.  The extend-path only needs prev_outputs for nodes
            # the current execution did NOT recompute.
            merged = {**prev_outputs, **eager_outputs}
            merged_errors = {**cached["errors"], **errors}
            merged_timings = {**cached["timings"], **timings}
            merged_memory = {**cached["memory_bytes"], **memory_bytes}
            merged_error_lines = {**cached["error_lines"], **error_lines}
            merged_avail = {**cached["available_columns"], **avail_cols}
            merged_output_cols = {**cached["output_columns"], **output_cols}
            merged_order = list(dict.fromkeys(cached["order"] + order))
            # A node that re-executed successfully in the extend path must
            # clear any stale cached error from an earlier transient failure.
            for nid in eager_outputs:
                if nid not in errors:
                    merged_errors.pop(nid, None)
                    merged_error_lines.pop(nid, None)
            _preview_cache.store(
                fp,
                eager_outputs=merged,
                errors=merged_errors,
                order=merged_order,
                timings=merged_timings,
                memory_bytes=merged_memory,
                error_lines=merged_error_lines,
                available_columns=merged_avail,
                output_columns=merged_output_cols,
            )
            _preview_cache.pin(fp)
            preview_entry_pinned = True
            eager_outputs = merged
            errors = merged_errors
            timings = merged_timings
            memory_bytes = merged_memory
            error_lines = merged_error_lines
            avail_cols = merged_avail
            output_cols = merged_output_cols
            order = merged_order
    else:
        # Complete cache miss — execute from scratch
        logger.debug(
            "preview_cache_miss",
            fingerprint=fp[:8],
            target=target_node_id,
            prev_fingerprint=(_preview_cache.fingerprint or "")[:8],
        )
        with _execution_stage(execution_context, "preview_cache_miss"):
            (
                raw_outputs,
                order,
                errors,
                timings,
                memory_bytes,
                error_lines,
                avail_cols,
                output_cols,
            ) = _eager_execute(
                graph,
                target_node_id,
                row_limit,
                source=source,
                enforce_contracts=enforce_contracts,
                fingerprint_memo=fingerprint_memo,
                required_columns_by_node=preview_required_columns,
                materialize_node_ids=preview_materialize_node_ids,
                materialize_column_limits_by_node=preview_materialize_column_limits,
                execution_context=execution_context,
            )
        eager_outputs = {k: v for k, v in raw_outputs.items() if v is not None}
        _preview_cache.store(
            fp,
            eager_outputs=eager_outputs,
            errors=errors,
            order=order,
            timings=timings,
            memory_bytes=memory_bytes,
            error_lines=error_lines,
            available_columns=avail_cols,
            output_columns=output_cols,
        )
        # Pin this entry through result serialisation so it cannot be
        # evicted while the caller is still building the response. Full
        # preview entries may later be reused by trace; target-only
        # entries intentionally retain only the selected node.
        _preview_cache.pin(fp)
        preview_entry_pinned = True

    try:
        # Pre-compute schema warnings for instance nodes by comparing the
        # columns available at the instance's inputs vs the original's inputs.
        node_map = graph.node_map
        parents_of = graph.parents_of
        if target_preview_only and not include_schema_metadata:
            result_order = (
                [target_node_id]
                if target_node_id is not None and target_node_id in node_map
                else []
            )
        elif target_preview_only:
            result_order = _result_order_for_target(graph, order, target_node_id, source)
        else:
            result_order = order
        preview_node_ids = (
            {target_node_id}
            if target_preview_only and target_node_id is not None
            else set(result_order)
        )

        schema_warnings: dict[str, list[SchemaWarning]] = {}

        def _cached_output_names(node_id: str) -> set[str]:
            cached = output_cols.get(node_id)
            if cached:
                return {name for name, _dtype in cached}
            df = eager_outputs.get(node_id)
            return set(df.columns) if df is not None else set()

        for nid in result_order:
            ref = node_map[nid].data.config.get("instanceOf")
            if not ref or ref not in node_map:
                continue
            # Columns feeding into the original node
            orig_input_cols: set[str] = set()
            for pid in parents_of.get(ref, []):
                orig_input_cols.update(_cached_output_names(pid))
            # Columns feeding into the instance node
            inst_input_cols: set[str] = set()
            for pid in parents_of.get(nid, []):
                inst_input_cols.update(_cached_output_names(pid))
            missing = orig_input_cols - inst_input_cols
            if missing:
                schema_warnings[nid] = [
                    SchemaWarning(column=c, status="missing") for c in sorted(missing)
                ]

        def _column_infos_for_node(
            node_id: str,
            df: pl.DataFrame | None,
        ) -> tuple[list[ColumnInfo], list[ColumnInfo]]:
            full_output = output_cols.get(node_id)
            if full_output is not None:
                columns = [ColumnInfo(name=n, dtype=d) for n, d in full_output]
            elif df is not None:
                columns = [ColumnInfo(name=c, dtype=str(df[c].dtype)) for c in df.columns]
            else:
                columns = []

            # available_columns = full column set before selected_columns filtering
            avail = avail_cols.get(node_id)
            available = [ColumnInfo(name=n, dtype=d) for n, d in avail] if avail else columns
            return columns, available

        def _node_schema_warnings(
            node_id: str,
            available: list[ColumnInfo],
        ) -> list[SchemaWarning]:
            node_data = node_map[node_id].data
            config_refs = _extract_column_refs(node_data.config)
            node_warnings = list(schema_warnings.get(node_id, []))
            if config_refs and available:
                available_names = {c.name for c in available}
                stale = config_refs - available_names
                if stale:
                    node_warnings.extend(
                        SchemaWarning(column=c, status="stale") for c in sorted(stale)
                    )
            return node_warnings

        results: dict[str, NodeResult] = {}
        for nid in result_order:
            if nid in errors:
                results[nid] = NodeResult(
                    status="error",
                    error=errors[nid],
                    error_line=error_lines.get(nid),
                    timing_ms=timings.get(nid, 0),
                    memory_bytes=memory_bytes.get(nid, 0),
                    schema_warnings=schema_warnings.get(nid, []),
                )
                continue
            df = eager_outputs.get(nid)
            columns, avail_col_infos = _column_infos_for_node(nid, df)
            node_warnings = _node_schema_warnings(nid, avail_col_infos)
            if df is None:
                if columns and nid not in preview_node_ids:
                    results[nid] = NodeResult(
                        status="ok",
                        column_count=len(columns),
                        columns=columns,
                        available_columns=avail_col_infos,
                        timing_ms=timings.get(nid, 0),
                        memory_bytes=memory_bytes.get(nid, 0),
                        schema_warnings=node_warnings,
                    )
                    continue
                results[nid] = NodeResult(
                    status="error",
                    error="No output",
                    timing_ms=timings.get(nid, 0),
                    memory_bytes=memory_bytes.get(nid, 0),
                )
                continue
            node_data = node_map[nid].data

            if nid in preview_node_ids:
                preview_columns = _preview_projection_columns(
                    df,
                    _normalise_requested_preview_columns(
                        node_data,
                        df,
                        requested_preview_columns,
                    ),
                )
                preview_row_limit = _preview_row_limit_for_width(
                    max_preview_rows,
                    len(preview_columns),
                )
                preview = df.select(preview_columns).head(preview_row_limit).to_dicts()
            else:
                preview_columns = []
                preview_row_limit = None
                preview = []

            results[nid] = NodeResult(
                status="ok",
                row_count=len(df),
                column_count=len(columns),
                columns=columns,
                available_columns=avail_col_infos,
                preview=preview,
                preview_columns=preview_columns,
                preview_row_count=len(preview),
                preview_row_limit=preview_row_limit,
                preview_truncated=preview_row_limit is not None and len(df) > len(preview),
                timing_ms=timings.get(nid, 0),
                memory_bytes=memory_bytes.get(nid, 0),
                schema_warnings=node_warnings,
            )
    finally:
        # Release the pin even when result serialisation/projection fails.
        # The entry remains in the LRU cache but is no longer exempt from
        # eviction, preventing exception paths from leaking pinned frames.
        if preview_entry_pinned:
            _preview_cache.unpin(fp)

    error_count = sum(1 for r in results.values() if r.status == "error")
    logger.info(
        "graph_executed",
        node_count=len(results),
        error_count=error_count,
        target=target_node_id,
    )
    return results


def _eager_execute(
    graph: PipelineGraph,
    target_node_id: str | None,
    row_limit: int | None,
    source: str = "live",
    enforce_contracts: bool = True,
    fingerprint_memo: GraphFingerprintMemo | None = None,
    required_columns_by_node: dict[str, list[str]] | None = None,
    materialize_node_ids: set[str] | frozenset[str] | None = None,
    materialize_column_limits_by_node: dict[str, int] | None = None,
    execution_context: ExecutionContext | None = None,
) -> tuple[
    dict[str, pl.DataFrame | None],
    list[str],
    dict[str, str],
    dict[str, float],
    dict[str, int],
    dict[str, int],
    dict[str, list[tuple[str, str]]],
    dict[str, list[tuple[str, str]]],
]:
    """Execute the graph eagerly in topo order.

    Returns (outputs, order, errors, timings, memory_bytes, error_lines,
    available_columns, output_columns) where errors maps node_id → message for nodes that
    failed, timings maps node_id → execution milliseconds, memory_bytes maps
    node_id → output DataFrame size in bytes, error_lines maps
    node_id → 1-based line number in user code for the error, and
    available_columns maps node_id → list of (name, dtype) pairs before
    any selected_columns filtering. output_columns maps node_id → the full
    post-selected/post-renamed schema before any preview execution projection.
    """
    preamble_error: str | None = None
    try:
        preamble_ns = _compile_preamble(
            graph.preamble or "",
            pipeline_dir=_pipeline_dir(graph),
            memo=fingerprint_memo,
        )
    except PreambleError as exc:
        # Don't abort — let non-preamble nodes (data sources, model scoring,
        # etc.) execute normally.  The error will surface on transform /
        # source-switch nodes that actually need the preamble bindings.
        logger.warning("preamble_failed", error=str(exc))
        preamble_ns = {}
        preamble_error = str(exc)

    result = _execute_eager_core(
        graph,
        _build_node_fn,
        target_node_id=target_node_id,
        row_limit=row_limit,
        swallow_errors=True,
        preamble_ns=preamble_ns or None,
        source=source,
        enforce_contracts=enforce_contracts,
        required_columns_by_node=required_columns_by_node,
        materialize_node_ids=materialize_node_ids,
        materialize_column_limits_by_node=materialize_column_limits_by_node,
        execution_context=execution_context,
    )
    errors = result.errors
    if preamble_error:
        # Inject the preamble error only into nodes whose builders use it
        # (transforms and live-switch nodes), not data sources / model scores.
        preamble_types = {NodeType.POLARS, NodeType.LIVE_SWITCH}
        node_map = {n.id: n for n in graph.nodes}
        for nid in result.order:
            nd = node_map.get(nid)
            if nd and nd.data.nodeType in preamble_types and nid not in errors:
                errors[nid] = preamble_error
    return (
        result.outputs,
        result.order,
        errors,
        result.timings,
        result.memory_bytes,
        result.error_lines,
        result.available_columns,
        result.output_columns,
    )


def _resolve_batch_scenario(graph: PipelineGraph) -> str | None:
    """Find the non-live scenario from the graph's live_switch ISM values.

    Returns ``None`` if no live_switch nodes exist or all mapped scenarios
    are ``"live"``.

    Raises ``ValueError`` if multiple live_switch nodes define different
    non-live scenario names (ambiguous routing).
    """
    batch_scenario: str | None = None
    for node in graph.nodes:
        if node.data.nodeType != NodeType.LIVE_SWITCH:
            continue
        ism: dict[str, str] = node.data.config.get("input_scenario_map", {})
        for scn in ism.values():
            if scn != "live":
                if batch_scenario is not None and scn != batch_scenario:
                    raise ValueError(
                        f"Conflicting batch scenarios across live_switch nodes: "
                        f"'{batch_scenario}' vs '{scn}'. "
                        f"All live_switch nodes must use the same non-live scenario name."
                    )
                batch_scenario = scn
    return batch_scenario


def execute_sink(
    graph: PipelineGraph,
    sink_node_id: str,
    source: str = "live",
    *,
    execution_context: ExecutionContext | None = None,
    streaming_chunk_size: int | None = None,
) -> SinkResponse:
    """Execute the pipeline up to a sink node and write its input to disk.

    Sinks are batch-only — they always run with a non-``"live"`` source
    so that model scoring uses the disk-batched path, keeping memory bounded.
    The *source* parameter is still accepted (and passed through for
    source-switch routing) but is coerced away from ``"live"`` for scoring.

    Uses Polars streaming sinks (``sink_parquet`` / ``sink_csv``) so the
    full dataset is never materialised in memory at once. If Polars cannot
    sink the plan in streaming mode, the sink fails loudly instead of
    broadening to an eager collect.

    This is called on-demand (not during normal run/preview).
    Returns a ``SinkResponse`` with row count and output path.
    """
    sink_node = graph.node_map.get(sink_node_id)
    if not sink_node:
        raise ValueError(f"Sink node '{sink_node_id}' not found")

    config = sink_node.data.config
    path = config.get("path", "")
    fmt = config.get("format", "parquet")
    selected_columns = config.get("selected_columns")

    if not path:
        raise ValueError("Sink node has no output path configured")
    if execution_context is None:
        execution_context = ExecutionContext(
            operation="pipeline_sink",
            profile=ExecutionProfile.LAZY_SINK,
        )

    required_columns_by_node: dict[str, frozenset[str]] | None = None
    if selected_columns:
        if isinstance(selected_columns, str | bytes):
            raise ValueError("Sink selected_columns must be a list of column names")
        selected_seed: set[str] = set()
        for column in selected_columns:
            if not isinstance(column, str) or not column:
                raise ValueError("Sink selected_columns must contain non-empty string names")
            selected_seed.add(column)
        required_columns_by_node = {sink_node_id: frozenset(selected_seed)}

    path = _resolve_sink_path(path, fmt)

    # Sinks are never used in live serving — model scoring must use the
    # disk-batched path (any scenario != "live").  But the scenario name
    # must match a value in the source-switch ISM so edge pruning routes
    # to the correct branch.  Resolve the first non-live ISM value from
    # the graph; fall back to "batch" if there are no live_switch nodes.
    if source == "live":
        sink_scenario = _resolve_batch_scenario(graph) or "batch"
    else:
        sink_scenario = source

    from haute._polars_utils import (
        DEFAULT_STREAMING_CHUNK_SIZE,
        _malloc_trim,
        bounded_sink,
        streaming_collect,
    )

    # Create a temp directory for join checkpoints.  Multi-input nodes
    # are sunk to parquet here so Polars sees each join as an independent
    # plan, avoiding chained-join memory accumulation (#24206).
    # The directory (and all checkpoint files) is cleaned up in finally.
    tmp_dir = tempfile.mkdtemp(prefix="haute_sink_")
    checkpoint_path = Path(tmp_dir)

    try:
        # Sink path: use cached preamble (no GUI edits expected during
        # batch runs).  Saves 50-500 ms of utility module re-import.
        preamble_ns = _compile_preamble(
            graph.preamble or "",
            force_refresh=False,
            pipeline_dir=_pipeline_dir(graph),
        )

        def _run_lazy() -> pl.LazyFrame:
            import haute.execution as execution_facade

            dataframe_cache_request = execution_facade.build_dataframe_execution_cache_request(
                graph,
                node_ids=[sink_node_id],
                namespace="sink",
                source=sink_scenario,
                profile=execution_context.profile,
                input_fingerprint=execution_facade.dataframe_graph_input_fingerprint(
                    graph,
                    target_node_id=sink_node_id,
                    source=sink_scenario,
                ),
                target_node_id=sink_node_id,
                required_columns_by_node=required_columns_by_node,
                enforce_contracts=ENFORCE_CONTRACTS,
                preamble_ns_supplied=bool(preamble_ns),
                streaming_chunk_size=streaming_chunk_size or DEFAULT_STREAMING_CHUNK_SIZE,
            )
            lazy_outputs, _order, _parents, _names = _execute_lazy(
                graph,
                _build_node_fn,
                target_node_id=sink_node_id,
                preamble_ns=preamble_ns or None,
                source=sink_scenario,
                checkpoint_dir=checkpoint_path,
                enforce_contracts=ENFORCE_CONTRACTS,
                required_columns_by_node=required_columns_by_node,
                execution_context=execution_context,
                dataframe_cache_request=dataframe_cache_request,
            )
            lf = lazy_outputs.get(sink_node_id)
            if lf is None:
                raise RuntimeError("Failed to compute sink input")
            return lf

        lf = _run_lazy()

        # Resolve relative sink paths against the pipeline's directory so
        # outputs land next to the pipeline file, not in the server's CWD.
        out = Path(path)
        if not out.is_absolute():
            pdir = _pipeline_dir(graph)
            if pdir is not None:
                out = pdir / out
        out.parent.mkdir(parents=True, exist_ok=True)

        # Log the lazy plan so we can diagnose streaming failures.
        try:
            plan = lf.explain()
            logger.info("sink_plan", path=path, plan=plan)
        except Exception:
            logger.debug("explain_failed", path=path)

        if execution_context is not None:
            execution_context.checkpoint(label="before_sink_write", node_id=sink_node_id)
            with execution_context.stage("sink_write", node_id=sink_node_id):
                bounded_sink(
                    lf,
                    out,
                    fmt=fmt,
                    streaming_chunk_size=streaming_chunk_size or DEFAULT_STREAMING_CHUNK_SIZE,
                )
            execution_context.checkpoint(label="after_sink_write", node_id=sink_node_id)
        else:
            bounded_sink(
                lf,
                out,
                fmt=fmt,
                streaming_chunk_size=streaming_chunk_size or DEFAULT_STREAMING_CHUNK_SIZE,
            )
        logger.info("sink_written", path=path, format=fmt)
        del lf
        gc.collect()
        _malloc_trim()

        execution_context.checkpoint(label="before_sink_row_count", node_id=sink_node_id)
        with execution_context.stage("sink_row_count", node_id=sink_node_id):
            count_lf = (
                pl.scan_csv(out).select(pl.len())
                if fmt == "csv"
                else pl.scan_parquet(out).select(pl.len())
            )
            row_count = streaming_collect(
                count_lf,
                profile=execution_context.profile,
            ).item()
        execution_context.checkpoint(label="after_sink_row_count", node_id=sink_node_id)

        return SinkResponse(
            status="ok",
            message=f"Wrote {row_count:,} rows to {path}",
            row_count=row_count,
            path=path,
            format=fmt,
            execution_metrics=(
                ExecutionMetricsPayload.model_validate(
                    execution_context.metrics_payload(status="completed")
                )
                if execution_context is not None
                else None
            ),
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
