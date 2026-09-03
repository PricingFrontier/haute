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
import ctypes
import functools
import gc
import hashlib
import importlib as _importlib
import inspect
import os
import shutil
import signal
import stat as stat_module
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Mapping
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl

import haute.execution as execution_facade
from haute._builders import _build_node_fn
from haute._cache import (
    GraphFingerprintMemo,
    preamble_execution_fingerprint,
    preamble_imports_utility,
)
from haute._env import int_env
from haute._execution_admission import create_admitted_execution_context
from haute._execution_context import ExecutionContext, ExecutionProfile
from haute._input_preparation import preparation_base_dir, prepare_input_snapshots
from haute._logging import get_logger
from haute._lru_cache import LRUCache
from haute._output_assembler import render_output_document
from haute._path_resolution import (
    RuntimePathOutsideProjectError,
    _infer_project_root,
    _normalise_path_text,
    runtime_project_root_scope,
)
from haute._registry import ensure_registry_ready
from haute._sandbox import safe_globals, validate_user_code
from haute._types import NodeData
from haute._validation_error import HauteValidationError
from haute.errors import PreambleError
from haute.graph_utils import (
    NodeType,
    PipelineGraph,
    _execute_eager_core,
    _execute_lazy,
    _prune_live_switch_edges,
    ancestors,
)
from haute.schemas import (
    ColumnInfo,
    ExecutionMetricsPayload,
    NodeResult,
    SchemaWarning,
    WriteOutputResponse,
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


PREVIEW_CACHE_MAX_BYTES = int_env(
    "HAUTE_PREVIEW_CACHE_MAX_BYTES",
    64 * 1024 * 1024,
)
"""Maximum retained bytes for materialized preview DataFrames."""
PREVIEW_MAX_CELLS = int_env("HAUTE_PREVIEW_MAX_CELLS", 50_000)
"""Maximum cells converted to JSON rows for a single node preview."""
PREVIEW_INITIAL_COLUMN_LIMIT = int_env(
    "HAUTE_PREVIEW_INITIAL_COLUMN_LIMIT",
    200,
)
"""Maximum first-click preview columns when the frontend has no cached schema."""

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
#
# Hot cache hits must NOT pass through this lock (pinned by
# ``test_hot_cache_hit_does_not_wait_for_import_lock``): a slow preamble exec
# holding the lock for seconds must not stall unrelated hot-path scoring.
# At the same time, cache population must be single-flight (pinned by
# ``test_concurrent_compiles_do_not_double_evaluate``): ``functools.lru_cache``
# is thread-safe at the lookup level but does NOT serialise the wrapped
# function — two threads that both miss would each exec the preamble and each
# return their own distinct namespace dict (CPython returns the caller's own
# result rather than re-reading the cache).  Both invariants are delivered by
# storing per-key ``_PreambleCell`` slots in the ``lru_cache`` and computing
# the namespace into the cell under ``_preamble_lock`` with a double-check —
# see ``_compile_preamble``.


def _pipeline_dir(graph: PipelineGraph) -> Path | None:  # pragma: no mutate
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


class PreviewProjectionError(HauteValidationError):
    """Requested preview columns cannot be projected from the target DataFrame."""


class DataOutputDestinationExistsError(FileExistsError):
    """A Data Output destination exists and replacement was not authorised."""

    def __init__(self, display_path: str):
        self.display_path = display_path
        super().__init__(f"Output destination already exists: {display_path}")


class DataOutputPublicationError(RuntimeError):
    """A create-only Data Output publication could not be completed safely."""

    def __init__(self, display_path: str):
        self.display_path = display_path
        super().__init__(
            "Could not publish output without replacing an existing destination: "
            f"{display_path}. Choose a destination on a filesystem that supports "
            "atomic create-only publication."
        )


class DataOutputDurabilityError(RuntimeError):
    """A Data Output was published, but its directory durability could not be confirmed."""

    def __init__(self, display_path: str):
        self.display_path = display_path
        super().__init__(
            f"Output was published to {display_path}, but storage durability could "
            "not be confirmed. Verify the file before retrying."
        )


@dataclass(frozen=True, slots=True)
class PreparedDataOutput:
    """Pickle-safe file-stage manifest or completed transactional result."""

    response: WriteOutputResponse
    project_root: str
    display_path: str
    final_path: str | None  # pragma: no mutate
    staging_path: str | None  # pragma: no mutate
    overwrite: bool
    size_bytes: int | None = None  # pragma: no mutate
    sha256: str | None = None  # pragma: no mutate
    transactional: bool = False


def validate_prepared_data_output_identity(
    prepared: PreparedDataOutput,
    *,  # pragma: no mutate
    project_root: str | Path,  # pragma: no mutate
    display_path: str,
    final_path: str | Path | None,  # pragma: no mutate
    staging_path: str | Path | None,  # pragma: no mutate
    overwrite: bool,
    transactional: bool,
) -> None:
    """Require a worker manifest to match the parent's selected destination.

    This deliberately compares the serialized path fields before constructing
    ``Path`` instances from worker-controlled values. The parent has already
    canonicalised its own paths; a differing child manifest is invalid rather
    than an alternative destination to validate or clean up.
    """
    if not isinstance(prepared, PreparedDataOutput):
        raise TypeError("prepared must be a PreparedDataOutput")

    expected_root = str(Path(project_root).resolve())
    expected_final = None if final_path is None else str(Path(final_path).resolve())
    expected_staging = None if staging_path is None else str(Path(staging_path).resolve())
    expected_fields = (
        ("project_root", expected_root),
        ("display_path", display_path),
        ("final_path", expected_final),
        ("staging_path", expected_staging),
    )
    for field, expected in expected_fields:
        actual = getattr(prepared, field)
        if expected is None:
            if actual is not None:
                raise ValueError(f"prepared output {field} does not match the parent request")
        elif not isinstance(actual, str) or actual != expected:
            raise ValueError(f"prepared output {field} does not match the parent request")

    for field, expected_flag in (("overwrite", overwrite), ("transactional", transactional)):
        actual = getattr(prepared, field)
        if type(actual) is not bool or actual is not expected_flag:
            raise ValueError(f"prepared output {field} does not match the parent request")


_IS_WINDOWS = os.name == "nt"
_WINDOWS_OUTPUT_SYNC_RETRY_DELAYS = (0.05, 0.1, 0.2, 0.4, 0.8)


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


def _utility_module_candidates(pipeline_dir_str: str | None) -> list[Path]:  # pragma: no mutate
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


def _evict_utility_import_state(pipeline_dir_str: str | None) -> None:  # pragma: no mutate
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
    pipeline_dir_str: str | None,  # pragma: no mutate
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

        source_line: int | None = None  # pragma: no mutate
        source_file: str | None = None  # pragma: no mutate

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


class _PreambleCell:
    """Single-flight slot for one preamble cache key.

    The ``lru_cache`` stores one cell per key (so eviction, ``cache_info``
    and ``cache_clear`` bookkeeping stay stdlib); the compiled namespace is
    computed INTO the cell under ``_preamble_lock`` with a double-check, so
    all concurrent first-callers of a key observe the same dict.  ``ns`` is
    ``None`` until the first successful compile; a failed compile leaves it
    ``None`` so the next call retries (matching lru_cache's
    exceptions-are-not-cached semantics, at the cost of the failed key
    occupying a cache slot until evicted).
    """

    __slots__ = ("ns",)

    def __init__(self) -> None:
        self.ns: dict[str, Any] | None = None  # pragma: no mutate


# Guards cell creation only — held for a cache lookup / tiny allocation,
# never during validation or exec, so hot hits can't stall behind a slow
# compile.  Needed because lru_cache does not serialise the wrapped factory:
# without it, two threads missing the same key could each mint their own
# cell and the single-flight guarantee would be lost one level up.
_preamble_cells_guard = threading.Lock()


@functools.lru_cache(maxsize=128)
def _compile_preamble_cached(
    preamble: str,
    cwd: str,
    pipeline_dir_str: str | None,  # pragma: no mutate
    _execution_fingerprint: str,
) -> _PreambleCell:
    """Cache-facing worker — returns the per-key single-flight cell.

    Keyed on ``(preamble, cwd, pipeline_dir_str, _execution_fingerprint)``
    so different pipelines sharing an identical preamble text but different
    utility contents still get distinct cache slots when callers request
    dependency refresh.

    ``pipeline_dir_str`` is a normalised ``str`` (or ``None``) rather than a
    ``Path`` so that ``lru_cache``'s hash lookup produces the same key for
    ``Path("/x")`` and ``"/x"`` — normalisation happens at the public
    entry point.

    Callers must hold ``_preamble_cells_guard`` (see ``_compile_preamble``).
    """
    return _PreambleCell()


def _compile_preamble_into_cell(
    preamble: str,
    cwd: str,
    pipeline_dir_str: str | None,  # pragma: no mutate
) -> dict[str, Any]:
    """Compile preamble bytes into a namespace. Caller holds ``_preamble_lock``.

    The lock covers the entire path-prioritisation/import/exec window. Even
    preambles without literal import statements execute with
    ``allow_imports=True`` and can consult process-global import state via
    helpers such as ``__import__``.
    """
    validate_user_code(preamble, allow_imports=True)
    imports_utility = preamble_imports_utility(preamble)
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
    *,  # pragma: no mutate
    force_refresh: bool = True,
    pipeline_dir: str | Path | None = None,  # pragma: no mutate
    memo: GraphFingerprintMemo | None = None,  # pragma: no mutate
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
    pipeline_dir_str: str | None = None  # pragma: no mutate
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

    # Cell lookup under its own tiny guard (never held during exec), so a
    # hot hit returns without touching ``_preamble_lock`` — a slow compile
    # in another thread cannot stall it.
    with _preamble_cells_guard:
        cell = _compile_preamble_cached(
            preamble,
            cwd,
            pipeline_dir_str,
            execution_fingerprint,
        )
    ns = cell.ns
    if ns is not None:
        return ns

    # Single-flight population: first caller compiles under ``_preamble_lock``
    # (which also serialises the process-global sys.path / sys.modules
    # mutation); concurrent first-callers block, re-check, and return the
    # SAME dict the winner stored — never a second exec producing a
    # semantically-equal-but-distinct namespace.
    with _preamble_lock:
        if cell.ns is None:
            cell.ns = _compile_preamble_into_cell(preamble, cwd, pipeline_dir_str)
        return cell.ns


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

    Multi-frame emit (MULTI_FRAME_PLAN commit 4): an apiInput source can
    return ``dict[port_name, DataFrame]`` rather than a single DataFrame.
    The dict's values are accounted individually so the whole bundle's
    retained size lands in the cache budget.
    """
    outputs = entry.get("eager_outputs", {})
    if not isinstance(outputs, dict):
        raise TypeError("preview cache entry eager_outputs must be a dict")

    def _size_of_frame(value: Any, node_id: str) -> int:
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
        return size

    total = 0
    for node_id, value in outputs.items():
        if isinstance(value, dict):
            for port_label, port_frame in value.items():
                total += _size_of_frame(port_frame, f"{node_id}.{port_label}")
        else:
            total += _size_of_frame(value, node_id)
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
    requested_preview_columns: list[str] | None,  # pragma: no mutate
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
    requested_preview_columns: list[str] | None,  # pragma: no mutate
) -> list[str] | None:  # pragma: no mutate
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
    requested_preview_columns: list[str] | None,  # pragma: no mutate
) -> list[str] | None:  # pragma: no mutate
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
    target_node_id: str | None,  # pragma: no mutate
    requested_preview_columns: list[str] | None,  # pragma: no mutate
) -> dict[str, list[str]] | None:  # pragma: no mutate
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
    target_node_id: str | None,  # pragma: no mutate
    requested_preview_columns: list[str] | None,  # pragma: no mutate
    *,  # pragma: no mutate
    target_preview_only: bool = False,
    initial_column_limit: int | None = None,  # pragma: no mutate
    port_label: str | None = None,  # pragma: no mutate
) -> str:
    """Cache-key suffix for projected preview materialisations.

    ``port_label`` selects which frame of a multi-frame producer the flat
    ``columns`` / ``preview`` reflect. It changes the serialised result, not
    the underlying ``eager_outputs`` (which always holds every frame), so it
    enters the cache key.
    """
    parts: list[str] = []
    if target_preview_only and target_node_id is not None:
        parts.append(f":preview_target_only={target_node_id!r}")
        if requested_preview_columns is None:
            parts.append(f":initial_col_limit={initial_column_limit!r}")
    if target_node_id is not None and requested_preview_columns is not None:
        parts.append(f":preview_target={target_node_id!r}")
        parts.append(f":preview_cols={tuple(requested_preview_columns)!r}")
    if target_node_id is not None and port_label is not None:
        parts.append(f":preview_port={port_label!r}")
    return "".join(parts)


def _cache_has_required_materialization(
    *,  # pragma: no mutate
    graph: PipelineGraph,
    target_node_id: str | None,  # pragma: no mutate
    requested_preview_columns: list[str] | None,  # pragma: no mutate
    required_materialized_nodes: set[str],
    materialize_column_limits_by_node: dict[str, int] | None,  # pragma: no mutate
    cached_outputs: dict[str, pl.DataFrame | dict[str, pl.DataFrame]],  # pragma: no mutate
    cached_output_columns: dict[str, list[tuple[str, str]]],
) -> bool:
    node_map = graph.node_map
    column_limits = materialize_column_limits_by_node or {}
    for node_id in required_materialized_nodes:
        df = cached_outputs.get(node_id)
        if df is None:
            return False
        if isinstance(df, dict):
            # A multi-frame producer is cached as ``dict[label, DataFrame]`` —
            # every frame is fully materialised, with no flat column projection
            # to validate (the per-frame ``columns`` is empty and the requested
            # frame is selected at serialisation time). Its presence alone
            # satisfies the materialisation requirement; the column-subset
            # check below assumes a single DataFrame and would raise on a dict.
            continue
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


_preview_cache: LRUCache[str, dict[str, Any]] = LRUCache(
    max_size=8,
    max_bytes=PREVIEW_CACHE_MAX_BYTES,
    size_of=_estimate_preview_cache_entry_bytes,
)


def _extract_column_refs(
    config: dict[str, Any],
) -> set[str]:
    """Extract column names referenced in a node's config.

    Only includes columns that are READ from upstream (not created/output columns).
    Returns an empty set for configs with no column references.
    """
    refs: set[str] = set()

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
    target_node_id: str | None,  # pragma: no mutate
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


def _preview_preparation_order(
    graph: PipelineGraph,
    target_node_id: str | None,
    source: str,
) -> list[str]:
    """Node ids of the preview's executed lineage, for input preparation.

    The lineage is walked over the same live-switch-pruned edges the execution
    itself uses, so an inactive branch's inputs are never prepared.
    """
    if target_node_id is None or target_node_id not in graph.node_map:
        return [node.id for node in graph.nodes]
    all_ids = {node.id for node in graph.nodes}
    edges = _prune_live_switch_edges(graph.edges, graph.node_map, source)
    return sorted(ancestors(target_node_id, edges, all_ids))


def execute_graph(
    graph: PipelineGraph,
    target_node_id: str | None = None,  # pragma: no mutate
    row_limit: int | None = None,  # pragma: no mutate
    max_preview_rows: int = _MAX_PREVIEW_ROWS,
    source: str = "live",
    enforce_contracts: bool = True,  # pragma: no mutate
    *,  # pragma: no mutate
    target_preview_only: bool = False,
    requested_preview_columns: list[str] | None = None,  # pragma: no mutate
    include_schema_metadata: bool = False,
    port_label: str | None = None,  # pragma: no mutate
    execution_context: ExecutionContext | None = None,  # pragma: no mutate
) -> dict[str, NodeResult]:
    """Execute a graph and return per-node results.

    Uses eager single-pass execution with a bounded multi-entry lineage cache
    so switching between recently previewed nodes avoids redundant execution.

    Args:
        graph: React Flow graph with "nodes" and "edges".
        target_node_id: If set, only execute nodes up to (and including) this node.
        row_limit: If set, apply .head(row_limit) to source nodes so only
                   that many rows flow through the pipeline.
        max_preview_rows: Max rows to include in the JSON preview payload.
        enforce_contracts: If ``True`` (the default), every node's column
            contract is asserted at the input and output boundaries, so
            contract violations surface as ``ContractMismatchError``.
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
        port_label: For a multi-frame target (an apiInput emitting 2+ frames,
            stored as ``dict[label, DataFrame]`` in ``eager_outputs``), the
            frame whose rows/columns the flat ``columns`` / ``preview`` should
            reflect. Single-frame targets ignore it. Threaded into the preview
            cache key so each frame is a distinct cache entry.

    Returns:
        Dict mapping node_id → {
            "status": "ok" | "error",
            "row_count": int,
            "columns": [...],
            "preview": [...],
            "error": str | None,
        }
    """
    if not graph.nodes:
        return {}
    if execution_context is None:
        admitted_context = create_admitted_execution_context(
            operation="execute_graph",
            profile=ExecutionProfile.PREVIEW_EAGER,
        )
        try:
            return execute_graph(
                graph,
                target_node_id=target_node_id,
                row_limit=row_limit,
                max_preview_rows=max_preview_rows,
                source=source,
                enforce_contracts=enforce_contracts,
                target_preview_only=target_preview_only,
                requested_preview_columns=requested_preview_columns,
                include_schema_metadata=include_schema_metadata,
                port_label=port_label,
                execution_context=admitted_context,
            )
        finally:
            admitted_context.release_admission(preserve_primary_error=True)

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

    def _plan_current_request() -> None:
        execution_facade.plan_execution_strategy(
            execution_facade.ProjectionRequest(
                graph=graph,
                target_node_id=target_node_id,
                profile=execution_context.profile,
                required_columns_by_node=preview_required_columns or None,
                source=source,
            ),
            execution_context=execution_context,
        )

    def _current_execution_strategy() -> execution_facade.ExecutionStrategyResult:
        strategy = execution_context.projection_plan
        if not isinstance(strategy, execution_facade.ExecutionStrategyResult):
            raise RuntimeError("preview execution completed without an execution strategy")
        return strategy

    preview_materialize_node_ids: frozenset[str] | None = (  # pragma: no mutate
        frozenset({target_node_id}) if target_preview_only and target_node_id is not None else None
    )
    preview_initial_column_limit = (
        PREVIEW_INITIAL_COLUMN_LIMIT
        if target_preview_only and target_node_id is not None and requested_preview_columns is None
        else None
    )
    preview_materialize_column_limits: dict[str, int] | None = (  # pragma: no mutate
        {target_node_id: preview_initial_column_limit}
        if target_node_id is not None and preview_initial_column_limit is not None
        else None
    )
    # Preparation runs before the runtime identity is computed, so a refreshed
    # generation's pointer is the one this preview entry is keyed by.
    with runtime_project_root_scope(graph.source_file):
        prepare_input_snapshots(
            _preview_preparation_order(graph, target_node_id, source),
            graph.node_map,
            profile=execution_context.profile,
            execution_context=execution_context,
            base_dir=preparation_base_dir(graph),
            schema_only=False,
        )
    fp = execution_facade.preview_lineage_cache_key(
        graph,
        target_node_id=target_node_id,
        source=source,
        requested_columns=requested_preview_columns,
        initial_column_limit=preview_initial_column_limit,
        row_limit=row_limit,
        port_label=port_label,
        enforce_contracts=enforce_contracts,
        materialisation_scope="target_only" if target_preview_only else "full",
        memo=fingerprint_memo,
    )
    # Runtime inputs and source-selected lineage are fingerprinted inside
    # the shared factory, so unrelated graph state cannot invalidate this entry.
    errors: dict[str, str] = {}
    error_lines: dict[str, int] = {}
    avail_cols: dict[str, list[tuple[str, str]]] = {}
    output_cols: dict[str, list[tuple[str, str]]] = {}
    # Per-(node_id, port_label) name+dtype schema for multi-frame emitters,
    # carried so a non-materialised multi-frame ancestor exposes per-frame
    # columns without being collected. Survives a cache hit via the
    # ``frame_columns`` cache slot below.
    frame_cols: dict[tuple[str, str], list[tuple[str, str]]] = {}
    preview_entry_pinned = False

    # Check if we can extend the cache (same graph, new target is a superset)
    with execution_context.stage("preview_cache_lookup"):
        cached = _preview_cache.get(fp)
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
            with execution_context.stage("preview_cache_hit"):
                cached_strategy = cached.get("execution_strategy")
                if not isinstance(cached_strategy, execution_facade.ExecutionStrategyResult):
                    raise RuntimeError("preview cache entry is missing its execution strategy")
                execution_context.projection_plan = cached_strategy
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
                frame_cols = cached["frame_columns"]
        else:
            # Partial hit — extend with newly-needed nodes
            logger.debug(
                "preview_cache_extend",
                fingerprint=fp[:8],
                target=target_node_id,
                cached_nodes=len(prev_outputs),
            )
            _plan_current_request()
            with execution_context.stage("preview_cache_extend"):
                (
                    raw_outputs,
                    order,
                    errors,
                    timings,
                    memory_bytes,
                    error_lines,
                    avail_cols,
                    output_cols,
                    frame_cols,
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
            merged_frame_cols = {**cached["frame_columns"], **frame_cols}
            merged_order = list(dict.fromkeys(cached["order"] + order))
            # A node that re-executed successfully in the extend path must
            # clear any stale cached error from an earlier transient failure.
            for nid in eager_outputs:
                if nid not in errors:
                    merged_errors.pop(nid, None)
                    merged_error_lines.pop(nid, None)
            preview_store_retained = _preview_cache.put(
                fp,
                {
                    "eager_outputs": merged,
                    "errors": merged_errors,
                    "order": merged_order,
                    "timings": merged_timings,
                    "memory_bytes": merged_memory,
                    "error_lines": merged_error_lines,
                    "available_columns": merged_avail,
                    "output_columns": merged_output_cols,
                    "frame_columns": merged_frame_cols,
                    "execution_strategy": _current_execution_strategy(),
                },
            )
            if preview_store_retained:
                _preview_cache.pin(fp)
                preview_entry_pinned = True
            else:
                logger.info(
                    "preview_cache_store_skipped",
                    fingerprint=fp[:8],
                    reason="entry_exceeds_cache_budget",
                )
            eager_outputs = merged
            errors = merged_errors
            timings = merged_timings
            memory_bytes = merged_memory
            error_lines = merged_error_lines
            avail_cols = merged_avail
            output_cols = merged_output_cols
            frame_cols = merged_frame_cols
            order = merged_order
    else:
        # Complete cache miss — execute from scratch
        logger.debug(
            "preview_cache_miss",
            fingerprint=fp[:8],
            target=target_node_id,
            prev_fingerprint=(_preview_cache.most_recent_key or "")[:8],
        )
        _plan_current_request()
        with execution_context.stage("preview_cache_miss"):
            (
                raw_outputs,
                order,
                errors,
                timings,
                memory_bytes,
                error_lines,
                avail_cols,
                output_cols,
                frame_cols,
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
        preview_store_retained = _preview_cache.put(
            fp,
            {
                "eager_outputs": eager_outputs,
                "errors": errors,
                "order": order,
                "timings": timings,
                "memory_bytes": memory_bytes,
                "error_lines": error_lines,
                "available_columns": avail_cols,
                "output_columns": output_cols,
                "frame_columns": frame_cols,
                "execution_strategy": _current_execution_strategy(),
            },
        )
        # Pin this entry through result serialisation so it cannot be
        # evicted while the caller is still building the response. Full
        # preview entries may later be reused by trace; target-only
        # entries intentionally retain only the selected node.
        if preview_store_retained:
            _preview_cache.pin(fp)
            preview_entry_pinned = True
        else:
            logger.info(
                "preview_cache_store_skipped",
                fingerprint=fp[:8],
                reason="entry_exceeds_cache_budget",
            )

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
            df: pl.DataFrame | None,  # pragma: no mutate
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
            # Bundle 2 executor guard — pass node_type so the
            # selected_columns scoop is skipped for apiInput nodes
            # (v2 has no spec for it; per-column `selected` bool in
            # tables[].columns[] is the v2-native surface).
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

        execution_context.fault_point(
            "response_shaping",
            node_id=target_node_id,
        )
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
            # Per-frame column schema for multi-frame producers, keyed by
            # the emit-table label (the ``sourceHandle`` / port a downstream
            # edge binds to). Read from the executor's name+dtype schema
            # lookup (``frame_cols``), which is populated for BOTH a
            # materialised target (from its collected frames) AND a lazy
            # ancestor (from ``collect_schema()``, no collect) — so the
            # OUTPUT editor sees every incoming frame's columns without the
            # ancestor being materialised. Single-frame nodes leave this
            # empty; ``columns`` already carries their full schema.
            frame_columns: dict[str, list[ColumnInfo]] = {
                port_label: [ColumnInfo(name=n, dtype=d) for n, d in schema]
                for (fc_nid, port_label), schema in frame_cols.items()
                if fc_nid == nid
            }
            is_frame_bundle = isinstance(df, dict)
            is_multi_frame_output = is_frame_bundle and len(df) > 1
            if is_frame_bundle:
                if len(df) == 1:
                    # A singleton bundle has exactly one canonical flat frame.
                    # Preserve the ordinary preview for both the source target
                    # and an already-materialised source ancestor.
                    df = next(iter(df.values()))
                # Only a labelled target frame has a flat preview when there
                # are multiple frames. Ancestor schemas remain available
                # through frame_columns without choosing a representative.
                elif is_multi_frame_output and nid == target_node_id and port_label is not None:
                    df = df[port_label]
                else:
                    df = None
            columns, avail_col_infos = _column_infos_for_node(nid, df)
            node_warnings = _node_schema_warnings(nid, avail_col_infos)
            if df is None:
                # A non-materialised ancestor (single-frame or multi-frame)
                # is absent from eager_outputs by design. Report its schema
                # as ``ok``: single-frame ancestors carry it in ``columns``,
                # multi-frame ancestors in ``frame_columns`` (with an empty
                # flat ``columns``, mirroring the materialised multi-frame
                # target). Either one being present means we have real schema
                # to surface, not a genuine failure.
                if (columns or frame_columns) and (
                    nid not in preview_node_ids or is_multi_frame_output
                ):
                    results[nid] = NodeResult(
                        status="ok",
                        column_count=len(columns),
                        columns=columns,
                        available_columns=avail_col_infos,
                        frame_columns=frame_columns,
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
                preview_frame = df.select(preview_columns).head(preview_row_limit)
                if node_data.nodeType == NodeType.OUTPUT:
                    # The OUTPUT node carries the assembled response document
                    # (struct columns, ragged → null-filled). Render it as the
                    # pruned JSON so the canvas preview shows the real shape.
                    preview = render_output_document(preview_frame)
                else:
                    preview = preview_frame.to_dicts()
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
                frame_columns=frame_columns,
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
    target_node_id: str | None,  # pragma: no mutate
    row_limit: int | None,  # pragma: no mutate
    source: str = "live",
    enforce_contracts: bool = True,
    fingerprint_memo: GraphFingerprintMemo | None = None,  # pragma: no mutate
    required_columns_by_node: dict[str, list[str]] | None = None,  # pragma: no mutate
    materialize_node_ids: set[str] | frozenset[str] | None = None,  # pragma: no mutate
    materialize_column_limits_by_node: dict[str, int] | None = None,  # pragma: no mutate
    execution_context: ExecutionContext | None = None,  # pragma: no mutate
) -> tuple[
    # Mirrors EagerResult.outputs — may carry per-frame dict for multi-frame
    # apiInput sources.
    dict[str, pl.DataFrame | dict[str, pl.DataFrame] | None],  # pragma: no mutate
    list[str],
    dict[str, str],
    dict[str, float],
    dict[str, int],
    dict[str, int],
    dict[str, list[tuple[str, str]]],
    dict[str, list[tuple[str, str]]],
    dict[tuple[str, str], list[tuple[str, str]]],
]:
    """Execute the graph eagerly in topo order.

    Returns (outputs, order, errors, timings, memory_bytes, error_lines,
    available_columns, output_columns, frame_columns) where errors maps
    node_id → message for nodes that
    failed, timings maps node_id → execution milliseconds, memory_bytes maps
    node_id → output DataFrame size in bytes, error_lines maps
    node_id → 1-based line number in user code for the error, and
    available_columns maps node_id → list of (name, dtype) pairs before
    any selected_columns filtering. output_columns maps node_id → the full
    post-selected/post-renamed schema before any preview execution projection.
    frame_columns maps (node_id, port_label) → list of (name, dtype) pairs
    for multi-frame emitters, populated whether or not the producer was
    materialised (a lazy ancestor's schema comes from ``collect_schema()``).
    """
    preamble_error: str | None = None  # pragma: no mutate
    try:
        preamble_ns = _compile_preamble(
            graph.preamble or "",
            pipeline_dir=_pipeline_dir(graph),
            memo=fingerprint_memo,
        )
    except PreambleError as exc:
        active_profile = (
            execution_context.profile
            if execution_context is not None
            else ExecutionProfile.PREVIEW_EAGER
        )
        if active_profile != ExecutionProfile.PREVIEW_EAGER:
            raise
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
        result.frame_columns,
    )


def _resolve_batch_scenario(graph: PipelineGraph) -> str | None:  # pragma: no mutate
    """Find the non-live scenario from the graph's live_switch ISM values.

    Returns ``None`` if no live_switch nodes exist or all mapped scenarios
    are ``"live"``.

    Raises ``ValueError`` if multiple live_switch nodes define different
    non-live scenario names (ambiguous routing).
    """
    batch_scenario: str | None = None  # pragma: no mutate
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


def _contain_output_path(
    graph: PipelineGraph,
    resolved_path: str,
    *,  # pragma: no mutate
    project_root: str | Path,  # pragma: no mutate
) -> Path:
    """Containment + pipeline-dir anchoring for an already-normalised output path."""
    root = Path(project_root).resolve()
    raw = Path(_normalise_path_text(resolved_path))
    if raw.is_absolute():
        out = raw.resolve()
    else:
        base = root
        if graph.source_file:
            source = Path(_normalise_path_text(graph.source_file))
            if not source.is_absolute():
                source = root / source
            base = source.resolve().parent
        out = (base / raw).resolve()
    if not out.is_relative_to(root):
        raise RuntimePathOutsideProjectError(
            f"Sink path {resolved_path!r} resolves outside the project root"
        )
    return out


def _validate_output_publish_paths(
    final_path: Path,
    staging_path: Path,
    *,  # pragma: no mutate
    project_root: str | Path,  # pragma: no mutate
) -> None:
    """Validate both sides of an atomic output publish against the project root.

    This check is intentionally called immediately before the writer and again
    immediately before ``Path.replace``. Resolving both paths each time catches
    a parent-directory symlink swap between initial request validation and
    publication.
    """
    root = Path(project_root).resolve()
    final = Path(final_path)
    staging = Path(staging_path)
    if staging.parent != final.parent:
        raise ValueError("Data output staging path must be a sibling of the final target")
    if staging.suffix != final.suffix:
        raise ValueError("Data output staging path must preserve the final target extension")

    resolved_final = final.resolve()
    resolved_staging = staging.resolve()
    if not resolved_final.is_relative_to(root) or not resolved_staging.is_relative_to(root):
        raise ValueError("Data output path resolves outside the project root")


def new_data_output_staging_path(final_path: str | Path) -> Path:  # pragma: no mutate
    """Mint one parent-owned sibling path while preserving the writer suffix."""
    final = Path(final_path)
    return final.with_name(f".{final.stem}.haute-stage-{uuid.uuid4().hex}{final.suffix}")


def _validate_output_staging_identity(final_path: Path, staging_path: Path) -> None:
    expected_prefix = f".{final_path.stem}.haute-stage-"
    name_without_suffix = (
        staging_path.name[: -len(final_path.suffix)] if final_path.suffix else staging_path.name
    )
    token = name_without_suffix.removeprefix(expected_prefix)
    if (
        not name_without_suffix.startswith(expected_prefix)
        or len(token) != 32
        or any(character not in "0123456789abcdef" for character in token)
    ):
        raise ValueError("Data output staging path is not an exact private generation")


def _output_artifact_signature(path: Path) -> tuple[int, str]:
    initial = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as artifact:
        for chunk in iter(lambda: artifact.read(1 << 20), b""):
            digest.update(chunk)
    final = path.stat()
    if (initial.st_dev, initial.st_ino, initial.st_size, initial.st_mtime_ns) != (
        final.st_dev,
        final.st_ino,
        final.st_size,
        final.st_mtime_ns,
    ):
        raise OSError(f"Data output staging artifact changed while it was signed: {path}")
    return int(final.st_size), digest.hexdigest()


def _validate_plain_output_artifact(path: Path) -> None:
    observed = path.lstat()
    if (
        not stat_module.S_ISREG(observed.st_mode)
        or stat_module.S_ISLNK(observed.st_mode)
        or bool(getattr(observed, "st_file_attributes", 0) & 0x400)
    ):
        raise RuntimeError("Data output staging artifact is not a plain regular file")
    if observed.st_nlink != 1:
        raise RuntimeError("Data output staging artifact must not be hard-linked")


def _cleanup_output_staging_path(
    staging_path: Path,
    *,  # pragma: no mutate
    project_root: str | Path,  # pragma: no mutate
) -> None:
    """Remove a failed staging artefact without following a swapped path outside the project."""
    root = Path(project_root).resolve()
    if not staging_path.resolve().is_relative_to(root):
        logger.warning("data_output_stage_cleanup_blocked", path=str(staging_path))
        return
    try:
        staging_path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning(
            "data_output_stage_cleanup_failed",
            path=str(staging_path),
            error=repr(exc),
        )


def _sync_output_artifact(path: Path) -> None:
    """Flush a completed staged output artifact before it is published."""
    # Windows requires a write-capable descriptor for ``os.fsync``; POSIX
    # permits a read-only descriptor for an already-closed writer artifact.
    mode = "rb+" if _IS_WINDOWS else "rb"

    def sync_once() -> None:
        with path.open(mode) as artifact:
            os.fsync(artifact.fileno())

    if not _IS_WINDOWS:
        sync_once()
        return

    for delay in (*_WINDOWS_OUTPUT_SYNC_RETRY_DELAYS, None):
        try:
            sync_once()
            return
        except PermissionError:
            if delay is None:
                raise
            time.sleep(delay)


def _sync_output_directory(path: Path) -> None:
    """Flush a published output directory where directory fsync is supported."""
    if _IS_WINDOWS:
        return
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish_output_create_only(
    staging_path: Path,
    final_path: Path,
    display_path: str,
    *,  # pragma: no mutate
    project_root: str | Path,  # pragma: no mutate
) -> None:
    """Publish a staged artifact without replacing an existing destination."""
    try:
        if _IS_WINDOWS:
            # Windows ``rename`` is create-only, unlike POSIX ``rename``.
            os.rename(staging_path, final_path)
        else:
            os.link(staging_path, final_path)
    except FileExistsError as exc:
        raise DataOutputDestinationExistsError(display_path) from exc
    except OSError as exc:
        raise DataOutputPublicationError(display_path) from exc

    if not _IS_WINDOWS:
        # The final path is already a complete published hard link. Failure to
        # remove its staging sibling is cleanup residue, not a failed write.
        _cleanup_output_staging_path(staging_path, project_root=project_root)


def _output_row_count_scan_kwargs(
    config: Mapping[str, Any],
    scanner_name: str,
) -> dict[str, Any]:
    """Translate writer arguments that affect exact artifact row parsing."""
    if scanner_name != "scan_csv":
        return {}
    raw_arguments = config.get("arguments")
    arguments = raw_arguments if isinstance(raw_arguments, Mapping) else {}
    kwargs: dict[str, Any] = {"raise_if_empty": False}
    for shared_name in ("decimal_comma", "quote_char", "separator"):
        if shared_name in arguments:
            kwargs[shared_name] = arguments[shared_name]
    if "include_header" in arguments:
        kwargs["has_header"] = arguments["include_header"]
    if "line_terminator" in arguments:
        line_terminator = arguments["line_terminator"]
        # ``scan_csv.eol_char`` is one byte; the writer's common two-byte
        # CRLF spelling is parsed on its final newline byte so each row is
        # counted once (passing CRLF directly counts both bytes separately).
        kwargs["eol_char"] = "\n" if line_terminator == "\r\n" else line_terminator
    return kwargs


def resolve_data_output_path(
    graph: PipelineGraph,
    config: Mapping[str, Any],
    *,  # pragma: no mutate
    project_root: str | Path | None = None,  # pragma: no mutate
) -> tuple[Path | None, str]:  # pragma: no mutate
    """Resolve a dataOutput node's write target.

    Returns ``(filesystem_path, display_path)``; the filesystem path is
    ``None`` for database targets (which have no local file). Bare filenames
    land under ``outputs/``, and the default extension comes from the format registry
    instead of the csv/parquet ternary — a ``.jsonl`` target stays ``.jsonl``.
    """
    from haute._polars_io_registry import default_output_extension, format_for_config

    root = _infer_project_root(
        project_root=project_root,
        source_file=graph.source_file,
    )
    fmt_entry = format_for_config(config)
    if fmt_entry.source_kind == "database":
        raw_uri = config.get("uri")
        if isinstance(raw_uri, str) and raw_uri:
            from haute._database_io import validate_sqlite_project_path

            validate_sqlite_project_path(
                raw_uri,
                base_dir=_pipeline_dir(graph) or root,
                project_root=root,
            )
        return None, str(config.get("table", ""))
    raw = config.get("path", "")
    if not isinstance(raw, str) or not raw:
        raise ValueError("Data output node has no output path configured")
    path = raw
    if "/" not in path and "\\" not in path:
        path = f"outputs/{path}"
    ext = default_output_extension(fmt_entry)
    if ext is not None and not Path(path).suffix:
        path = f"{path}{ext}"
    return _contain_output_path(graph, path, project_root=root), path


def prepare_data_output(
    graph: PipelineGraph,
    output_node_id: str,
    source: str = "live",
    *,  # pragma: no mutate
    execution_context: ExecutionContext | None = None,  # pragma: no mutate
    streaming_chunk_size: int | None = None,  # pragma: no mutate
    project_root: str | Path | None = None,  # pragma: no mutate
    overwrite: bool = False,  # pragma: no mutate
    staging_path: str | Path | None = None,  # pragma: no mutate
) -> PreparedDataOutput:
    """Execute a Data Output, leaving file publication to the parent caller.

    Output writes are batch-only — they always run with a non-``"live"`` source
    so that model scoring uses the disk-batched path, keeping memory bounded.
    The *source* parameter is still accepted (and passed through for
    source-switch routing) but is coerced away from ``"live"`` for scoring.

    Uses Polars streaming sinks (``sink_parquet`` / ``sink_csv``) so the
    full dataset is never materialised in memory at once. If Polars cannot
    sink the plan in streaming mode, the sink fails loudly instead of
    broadening to an eager collect.

    File outputs are fully written, synced, and signed at an exact sibling
    staging path but remain invisible. Database/lakehouse writers retain their
    native transactional commit and return a transactional manifest.
    """
    if execution_context is None:
        admitted_context = create_admitted_execution_context(
            operation="pipeline_write_output",
            profile=ExecutionProfile.LAZY_SINK,
        )
        try:
            return prepare_data_output(
                graph,
                output_node_id,
                source,
                execution_context=admitted_context,
                streaming_chunk_size=streaming_chunk_size,
                project_root=project_root,
                overwrite=overwrite,
                staging_path=staging_path,
            )
        finally:
            admitted_context.release_admission(preserve_primary_error=True)

    output_node = graph.node_map.get(output_node_id)
    if output_node is None:
        raise ValueError(f"Data Output node '{output_node_id}' not found")
    if output_node.data.nodeType != NodeType.DATA_OUTPUT:
        raise ValueError(f"Node '{output_node_id}' is not a Data Output")

    from haute._polars_io_registry import validate_data_output_config

    config = validate_data_output_config(output_node.data.config)
    from haute._polars_io_registry import format_for_config, format_group

    root = _infer_project_root(
        project_root=project_root,
        source_file=graph.source_file,
    )
    out, path = resolve_data_output_path(graph, config, project_root=root)
    is_file_target = format_group(format_for_config(config)) == "file"
    if is_file_target and out is not None and out.exists() and not overwrite:
        raise DataOutputDestinationExistsError(path)

    selected_columns = config.get("selected_columns")

    required_columns_by_node: dict[str, frozenset[str]] | None = None  # pragma: no mutate
    if selected_columns:
        if isinstance(selected_columns, str | bytes):
            raise ValueError("Data Output selected_columns must be a list of column names")
        selected_seed: set[str] = set()
        for column in selected_columns:
            if not isinstance(column, str) or not column:
                raise ValueError("Data Output selected_columns must contain non-empty string names")
            selected_seed.add(column)
        required_columns_by_node = {output_node_id: frozenset(selected_seed)}

    staging_out: Path | None = None  # pragma: no mutate
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        if is_file_target:
            staging_out = (
                new_data_output_staging_path(out)
                if staging_path is None
                else Path(staging_path).resolve()
            )
            _validate_output_publish_paths(out, staging_out, project_root=root)
            _validate_output_staging_identity(out, staging_out)
            if staging_out.exists() or staging_out.is_symlink():
                raise FileExistsError(f"Data output staging path already exists: {staging_out}")
    if staging_path is not None and staging_out is None:
        raise ValueError("Only atomic file outputs accept a staging path")

    # Sinks are never used in live serving — model scoring must use the
    # disk-batched path (any scenario != "live").  But the scenario name
    # must match a value in the source-switch ISM so edge pruning routes
    # to the correct branch.  Resolve the first non-live ISM value from
    # the graph; fall back to "batch" if there are no live_switch nodes.
    if source == "live":
        output_scenario = _resolve_batch_scenario(graph) or "batch"
    else:
        output_scenario = source

    from haute._polars_utils import (
        DEFAULT_STREAMING_CHUNK_SIZE,
        _malloc_trim,
        streaming_collect,
    )

    # Create a temp directory for join checkpoints.  Multi-input nodes
    # are sunk to parquet here so Polars sees each join as an independent
    # plan, avoiding chained-join memory accumulation (#24206).
    # The directory (and all checkpoint files) is cleaned up in finally.
    tmp_dir = tempfile.mkdtemp(prefix="haute_sink_")
    checkpoint_path = Path(tmp_dir)
    retain_staging = False

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
                node_ids=[output_node_id],
                namespace="data_output",
                source=output_scenario,
                profile=execution_context.profile,
                input_fingerprint=execution_facade.dataframe_graph_input_fingerprint(
                    graph,
                    target_node_id=output_node_id,
                    source=output_scenario,
                ),
                target_node_id=output_node_id,
                required_columns_by_node=required_columns_by_node,
                enforce_contracts=True,
                preamble_ns_supplied=bool(preamble_ns),
                streaming_chunk_size=streaming_chunk_size or DEFAULT_STREAMING_CHUNK_SIZE,
            )
            lazy_outputs, _order, _parents, _names = _execute_lazy(
                graph,
                _build_node_fn,
                target_node_id=output_node_id,
                preamble_ns=preamble_ns or None,
                source=output_scenario,
                checkpoint_dir=checkpoint_path,
                enforce_contracts=True,
                required_columns_by_node=required_columns_by_node,
                execution_context=execution_context,
                dataframe_cache_request=dataframe_cache_request,
            )
            lf = lazy_outputs.get(output_node_id)
            if lf is None:
                raise RuntimeError("Failed to compute Data Output input")
            return lf

        lf = _run_lazy()

        # Log the lazy plan so we can diagnose streaming failures.
        try:
            plan = lf.explain()
            logger.info("sink_plan", path=path, plan=plan)
        except Exception:
            logger.debug("explain_failed", path=path)

        data_output_rows: int | None = None  # pragma: no mutate

        def _write(frame: pl.LazyFrame) -> None:
            nonlocal data_output_rows
            from haute._polars_io_registry import write_polars_output

            if staging_out is not None and out is not None:
                _validate_output_publish_paths(
                    out,
                    staging_out,
                    project_root=root,
                )
            data_output_rows = write_polars_output(
                frame,
                config,
                resolved_path=staging_out or out,
            )

        if execution_context is not None:
            execution_context.checkpoint(label="before_output_write", node_id=output_node_id)
            with execution_context.stage("output_write", node_id=output_node_id):
                _write(lf)
        else:
            _write(lf)
        del lf
        gc.collect()
        _malloc_trim()

        execution_context.checkpoint(label="before_output_row_count", node_id=output_node_id)
        with execution_context.stage("output_row_count", node_id=output_node_id):
            if data_output_rows is not None:
                # Eager writes and database writes report their own count.
                row_count = data_output_rows
            else:
                # Streaming sink: re-scan the written artefact through the
                # format's own scanner (every sinker-bearing format has one).
                from haute._polars_io_registry import format_for_config

                scanner_name = format_for_config(config).scanner
                # Registry invariants: sink implies scanner; only database
                # targets have no filesystem path, and they report their own
                # row count above.
                count_path = staging_out or out
                if scanner_name is None or count_path is None:  # pragma: no mutate
                    raise RuntimeError(
                        f"Format {config.get('format')!r} wrote via a streaming sink but "
                        "cannot be re-scanned to count rows"
                    )
                count_lf = getattr(pl, scanner_name)(
                    count_path,
                    **_output_row_count_scan_kwargs(config, scanner_name),
                ).select(pl.len())
                row_count = streaming_collect(
                    count_lf,
                    execution_context=execution_context,
                ).item()
        execution_context.checkpoint(label="after_output_row_count", node_id=output_node_id)
        execution_context.checkpoint(label="before_output_manifest", node_id=output_node_id)
        size_bytes: int | None = None  # pragma: no mutate
        sha256: str | None = None  # pragma: no mutate
        if staging_out is not None:
            if out is None:  # pragma: no mutate - staging implies a file target
                raise RuntimeError("Data output staging resolved no final target")
            _validate_output_publish_paths(
                out,
                staging_out,
                project_root=root,
            )
            _sync_output_artifact(staging_out)
            _validate_plain_output_artifact(staging_out)
            size_bytes, sha256 = _output_artifact_signature(staging_out)

        execution_context.fault_point(
            "response_shaping",
            node_id=output_node_id,
        )
        response = WriteOutputResponse(
            status="ok",
            message=f"Wrote {row_count:,} rows to {path}",
            row_count=row_count,
            path=path,
            format=str(config["format"]),
            execution_metrics=(
                ExecutionMetricsPayload.model_validate(
                    execution_context.metrics_payload(status="completed")
                )
                if execution_context is not None
                else None
            ),
        )
        prepared = PreparedDataOutput(
            response=response,
            project_root=str(root),
            display_path=path,
            final_path=None if out is None else str(out),
            staging_path=None if staging_out is None else str(staging_out),
            overwrite=overwrite,
            size_bytes=size_bytes,
            sha256=sha256,
            transactional=staging_out is None,
        )
        retain_staging = staging_out is not None
        return prepared
    finally:
        if staging_out is not None and not retain_staging:
            _cleanup_output_staging_path(
                staging_out,
                project_root=root,
            )
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _prepared_output_paths(prepared: PreparedDataOutput) -> tuple[Path, Path, Path]:
    if not isinstance(prepared, PreparedDataOutput):
        raise TypeError("prepared must be a PreparedDataOutput")
    if prepared.transactional:
        raise ValueError("transactional output does not have a staged file")
    if prepared.final_path is None or prepared.staging_path is None:
        raise ValueError("prepared file output omitted its final or staging path")
    root = Path(prepared.project_root).resolve()
    final = Path(prepared.final_path)
    staging = Path(prepared.staging_path)
    _validate_output_publish_paths(final, staging, project_root=root)
    _validate_output_staging_identity(final, staging)
    return root, final, staging


def commit_prepared_data_output(
    prepared: PreparedDataOutput,
    *,  # pragma: no mutate
    publication_guard: AbstractContextManager[None] | None = None,  # pragma: no mutate
) -> WriteOutputResponse:
    """Validate a worker manifest and publish its staged file atomically."""
    if not isinstance(prepared, PreparedDataOutput):
        raise TypeError("prepared must be a PreparedDataOutput")
    response = WriteOutputResponse.model_validate(prepared.response.model_dump(mode="python"))
    if response.path != prepared.display_path:
        raise ValueError("prepared output response does not match its destination")
    if prepared.transactional:
        if (
            prepared.staging_path is not None
            or prepared.size_bytes is not None
            or prepared.sha256 is not None
        ):
            raise ValueError("transactional output carried an unexpected file manifest")
        with publication_guard or nullcontext():
            logger.info("data_output_written", path=prepared.display_path, format=response.format)
            return response

    root, final, staging = _prepared_output_paths(prepared)
    if (
        isinstance(prepared.size_bytes, bool)
        or not isinstance(prepared.size_bytes, int)
        or prepared.size_bytes < 0
        or not isinstance(prepared.sha256, str)
        or len(prepared.sha256) != 64
        or any(character not in "0123456789abcdef" for character in prepared.sha256)
    ):
        raise ValueError("prepared output contains an invalid content signature")
    _validate_plain_output_artifact(staging)
    observed_size, observed_sha256 = _output_artifact_signature(staging)
    if (observed_size, observed_sha256) != (prepared.size_bytes, prepared.sha256):
        raise RuntimeError("prepared output content signature does not match its staging artifact")
    _validate_output_publish_paths(final, staging, project_root=root)
    _sync_output_artifact(staging)
    with publication_guard or nullcontext():
        if final.exists() and not prepared.overwrite:
            raise DataOutputDestinationExistsError(prepared.display_path)
        if prepared.overwrite:
            os.replace(staging, final)
        else:
            _publish_output_create_only(
                staging,
                final,
                prepared.display_path,
                project_root=root,
            )
        try:
            _sync_output_directory(final.parent)
        except OSError as exc:
            raise DataOutputDurabilityError(prepared.display_path) from exc
    logger.info("data_output_written", path=prepared.display_path, format=response.format)
    return response


def discard_prepared_data_output(prepared: PreparedDataOutput) -> None:
    """Remove only the exact unpublished plain staging file in *prepared*."""
    if prepared.transactional or prepared.staging_path is None:
        return
    _root, _final, staging = _prepared_output_paths(prepared)
    try:
        _validate_plain_output_artifact(staging)
    except FileNotFoundError:
        return
    staging.unlink()


def discard_data_output_staging_path(
    final_path: str | Path,  # pragma: no mutate
    staging_path: str | Path,  # pragma: no mutate
    *,  # pragma: no mutate
    project_root: str | Path,  # pragma: no mutate
) -> None:
    """Clean the exact parent-selected file stage after a worker dies."""
    root = Path(project_root).resolve()
    final = Path(final_path)
    staging = Path(staging_path)
    _validate_output_publish_paths(final, staging, project_root=root)
    _validate_output_staging_identity(final, staging)
    try:
        _validate_plain_output_artifact(staging)
    except FileNotFoundError:
        return
    staging.unlink()


def write_data_output(
    graph: PipelineGraph,
    output_node_id: str,
    source: str = "live",
    *,  # pragma: no mutate
    execution_context: ExecutionContext | None = None,  # pragma: no mutate
    streaming_chunk_size: int | None = None,  # pragma: no mutate
    project_root: str | Path | None = None,  # pragma: no mutate
    overwrite: bool = False,
) -> WriteOutputResponse:
    """Compatibility entry point using the same prepare/parent-commit contract."""
    if execution_context is None:
        admitted_context = create_admitted_execution_context(
            operation="pipeline_write_output",
            profile=ExecutionProfile.LAZY_SINK,
        )
        try:
            return write_data_output(
                graph,
                output_node_id,
                source,
                execution_context=admitted_context,
                streaming_chunk_size=streaming_chunk_size,
                project_root=project_root,
                overwrite=overwrite,
            )
        finally:
            admitted_context.release_admission(preserve_primary_error=True)

    prepared: PreparedDataOutput | None = None  # pragma: no mutate
    primary_error: BaseException | None = None  # pragma: no mutate
    try:
        prepared = prepare_data_output(
            graph,
            output_node_id,
            source,
            execution_context=execution_context,
            streaming_chunk_size=streaming_chunk_size,
            project_root=project_root,
            overwrite=overwrite,
        )
        return commit_prepared_data_output(prepared)
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        if prepared is not None:
            try:
                discard_prepared_data_output(prepared)
            except BaseException as cleanup_exc:
                if primary_error is None:
                    raise
                primary_error.add_note(f"Data output staging cleanup failed: {cleanup_exc}")
