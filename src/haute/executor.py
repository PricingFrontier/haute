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

import gc
import re
import shutil
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

import polars as pl

from haute._builders import (  # noqa: F401 — re-exported for backward compat
    _NODE_BUILDERS,
    NodeBuildContext,
    NodeBuilder,
    _apply_online,
    _apply_ratebook,
    _build_node_fn,
    _dispatch_apply,
    _passthrough_fn,
    resolve_instance_node,
)
from haute._fingerprint_cache import FingerprintCache
from haute._logging import get_logger
from haute._rating import _apply_banding  # noqa: F401 — re-exported for tests
from haute._sandbox import UnsafeCodeError, safe_globals, validate_user_code
from haute.graph_utils import (
    HauteError,
    NodeType,
    PipelineGraph,
    _execute_eager_core,
    _execute_lazy,
    _Frame,
    _resolve_sink_path,
    build_instance_mapping,
    graph_fingerprint,
)
from haute.schemas import ColumnInfo, NodeResult, SchemaWarning, SinkResponse

logger = get_logger(component="executor")

# ── Default constants ─────────────────────────────────────────────
_MAX_PREVIEW_ROWS = 10_000  # safety cap for execute_graph JSON payload

# Lock to prevent concurrent module eviction + re-import in _compile_preamble.
# Without this, two threads (e.g. preview + estimate) can race: one evicts
# "utility" from sys.modules while the other is mid-import, causing a KeyError
# inside importlib._bootstrap._load_unlocked.
_preamble_lock = threading.Lock()


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


# Cache compiled preamble results by content hash so unchanged preambles
# (common during training / optimiser runs where the preamble doesn't
# change between invocations) skip the expensive module eviction +
# re-import cycle.  The cache is invalidated when the preamble text
# changes (hash mismatch).
_PREAMBLE_CACHE_MAX = 64
_preamble_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()

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

_polars_config_lock = threading.Lock()


def _compile_preamble(
    preamble: str,
    *,
    force_refresh: bool = True,
    pipeline_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Compile user-defined preamble code into a namespace dict.

    The preamble (helper functions, constants, lambdas) is defined at the
    top of a pipeline file between imports and the first ``@pipeline.<type>`` decorator.
    This compiles it once and returns a dict of bindings that can be
    injected into ``_exec_user_code`` via ``extra_ns``.

    Uses a single dict for globals/locals so preamble functions can call
    each other (they share the same ``__globals__``).

    When *force_refresh* is ``False`` (default), a cached result from a
    previous call with the same preamble text is returned immediately,
    skipping utility-module eviction and ``exec()``.  The preview path
    passes ``force_refresh=True`` so that edits to utility modules in the
    GUI are always picked up.

    Raises ``PreambleError`` with a human-readable message and optional
    source line number when the preamble fails to execute (e.g. a utility
    module has a NameError).
    """
    if not preamble or not preamble.strip():
        return {}

    import hashlib

    cache_key = hashlib.md5(preamble.encode()).hexdigest()

    if not force_refresh and cache_key in _preamble_cache:
        return _preamble_cache[cache_key]

    # Preamble may contain imports (e.g. from utility.features import …)
    # which are legitimate, but still validate against other dangerous
    # patterns (dunder access, eval, exec, etc.).
    validate_user_code(preamble, allow_imports=True)
    # Ensure project root is importable so `from utility.xxx import …` works
    # even when the server process was spawned by uvicorn reload.
    # We add *both* cwd and the pipeline's parent directory because the
    # utility/ folder may live next to the pipeline file (e.g. inside a
    # rating/ subfolder) rather than at the project root.
    import os  # noqa: E401
    import sys

    cwd = os.getcwd()
    if cwd not in sys.path:
        sys.path.insert(0, cwd)
    if pipeline_dir is not None:
        pdir = str(Path(pipeline_dir).resolve())
        if pdir not in sys.path:
            sys.path.insert(0, pdir)
    # Evict cached utility modules so edits in the GUI are picked up
    # on every run instead of serving stale bytecode from sys.modules.
    # The lock prevents a concurrent request from seeing partially-evicted
    # state (which causes KeyError inside importlib._load_unlocked).
    ns = safe_globals(pl=pl, allow_imports=True)
    base_keys = set(ns.keys())
    with _preamble_lock:
        for mod_name in [k for k in sys.modules if k == "utility" or k.startswith("utility.")]:
            del sys.modules[mod_name]
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

    result = {
        k: v
        for k, v in ns.items()
        if k not in base_keys
        and not (hasattr(v, "__name__") and getattr(v, "__name__", "") in _DANGEROUS_MODULES)
    }
    _preamble_cache[cache_key] = result
    if len(_preamble_cache) > _PREAMBLE_CACHE_MAX:
        _preamble_cache.popitem(last=False)
    return result


def _exec_user_code(
    code: str,
    src_names: list[str],
    dfs: tuple[_Frame, ...],
    extra_ns: dict[str, Any] | None = None,
    orig_source_names: list[str] | None = None,
    input_mapping: dict[str, str] | None = None,
) -> _Frame:
    """Wrap, execute, and return the result of user-provided code.

    Shared by transform and externalFile node types.
    - Injects ``pl``, input DataFrames by name, and ``df`` (first input).
    - Optionally merges *extra_ns* into the local namespace (e.g. ``obj``).
    - Handles the ``.chain`` / bare-expression wrapping and adjusts line
      numbers in error messages so they match the editor.
    """
    local_ns: dict[str, Any] = {"pl": pl}
    for i, d in enumerate(dfs):
        if i < len(src_names):
            local_ns[src_names[i]] = d
    # Instance alias injection: bind original source names so the original's
    # code can reference variables by their original upstream labels.
    if orig_source_names:
        mapping = build_instance_mapping(orig_source_names, src_names, input_mapping)
        for orig, inst in mapping.items():
            if orig not in local_ns and inst in local_ns:
                local_ns[orig] = local_ns[inst]
    if dfs:
        local_ns["df"] = dfs[0]
    if extra_ns:
        local_ns.update(extra_ns)

    exec_code = code
    line_offset = 0
    if code.startswith("."):
        first = src_names[0] if src_names else "df"
        exec_code = f"df = (\n    {first}\n    {code}\n)"
        line_offset = 2
    elif "df =" not in code and "df=" not in code:
        exec_code = f"df = (\n    {code}\n)"
        line_offset = 1

    # Validate the *wrapped* code at the AST level before exec().
    # We validate exec_code (not the raw snippet) because user code
    # fragments like chain syntax (".filter(...)") are not valid Python
    # on their own — only the wrapped version is parseable.
    # This blocks dunder access, imports, getattr, class defs, etc.
    # at the structural level — a stronger layer than restricted builtins.
    try:
        validate_user_code(exec_code)
    except UnsafeCodeError as uce:
        # If the sandbox rejection was caused by a SyntaxError (code we
        # couldn't parse), convert back to SyntaxError with adjusted line
        # numbers so callers see the same error type they'd get from exec().
        if isinstance(uce.__cause__, SyntaxError):
            syn = uce.__cause__
            if syn.lineno is not None:
                syn.lineno = max(1, syn.lineno - line_offset)
            raise syn from None
        raise

    try:
        exec(exec_code, safe_globals(pl=pl, **(extra_ns or {})), local_ns)
    except SyntaxError as exc:
        if exc.lineno is not None:
            exc.lineno = max(1, exc.lineno - line_offset)
        raise
    except Exception as exc:
        msg = str(exc)
        if line_offset and re.search(r"line \d+", msg):
            msg = re.sub(
                r"line (\d+)",
                lambda m: f"line {max(1, int(m.group(1)) - line_offset)}",
                msg,
            )
            raise type(exc)(msg) from None
        # For errors without "line N" in the message (e.g. NameError),
        # extract the line from the traceback and adjust for preamble offset.
        if exc.__traceback__:
            import traceback as _tb

            for frame in reversed(_tb.extract_tb(exc.__traceback__)):
                if frame.filename == "<string>" and frame.lineno is not None:
                    exc._user_code_line = max(1, frame.lineno - line_offset)  # type: ignore[attr-defined]
                    break
        raise

    result = local_ns.get("df", dfs[0] if dfs else pl.LazyFrame())
    if isinstance(result, pl.DataFrame):
        result = result.lazy()
    return result  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Preview cache — same principle as the trace cache in trace.py.
# The pipeline doesn't change between node clicks — only the target node
# changes.  Cache the materialized DataFrames so clicking different nodes
# is instant instead of re-executing model scoring on 678K rows each time.
# ---------------------------------------------------------------------------


_preview_cache = FingerprintCache(
    slots=(
        "eager_outputs",
        "errors",
        "order",
        "timings",
        "memory_bytes",
        "error_lines",
        "available_columns",
    ),
)


def execute_graph(
    graph: PipelineGraph,
    target_node_id: str | None = None,
    row_limit: int | None = None,
    max_preview_rows: int = _MAX_PREVIEW_ROWS,
    source: str = "live",
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

    fp = graph_fingerprint(graph, f"{row_limit}:{source}")

    errors: dict[str, str] = {}
    error_lines: dict[str, int] = {}
    avail_cols: dict[str, list[tuple[str, str]]] = {}

    # Check if we can extend the cache (same graph, new target is a superset)
    cached = _preview_cache.try_get(fp)
    if cached is not None:
        prev_outputs = cached["eager_outputs"]
        if target_node_id is None or target_node_id in prev_outputs:
            # Full cache hit — all required nodes already materialised
            logger.debug(
                "preview_cache_hit",
                fingerprint=fp[:8],
                target=target_node_id,
                cached_nodes=len(prev_outputs),
            )
            eager_outputs = prev_outputs
            order = cached["order"]
            errors = cached["errors"]
            timings = cached["timings"]
            memory_bytes = cached["memory_bytes"]
            error_lines = cached["error_lines"]
            avail_cols = cached["available_columns"]
        else:
            # Partial hit — extend with newly-needed nodes
            logger.debug(
                "preview_cache_extend",
                fingerprint=fp[:8],
                target=target_node_id,
                cached_nodes=len(prev_outputs),
            )
            (raw_outputs, order, errors, timings, memory_bytes, error_lines, avail_cols) = (
                _eager_execute(
                    graph,
                    target_node_id,
                    row_limit,
                    source=source,
                )
            )
            eager_outputs = {k: v for k, v in raw_outputs.items() if v is not None}
            merged = {**prev_outputs, **eager_outputs}
            merged_errors = {**cached["errors"], **errors}
            merged_timings = {**cached["timings"], **timings}
            merged_memory = {**cached["memory_bytes"], **memory_bytes}
            merged_error_lines = {**cached["error_lines"], **error_lines}
            merged_avail = {**cached["available_columns"], **avail_cols}
            merged_order = list(dict.fromkeys(cached["order"] + order))
            _preview_cache.store(
                fp,
                eager_outputs=merged,
                errors=merged_errors,
                order=merged_order,
                timings=merged_timings,
                memory_bytes=merged_memory,
                error_lines=merged_error_lines,
                available_columns=merged_avail,
            )
            eager_outputs = merged
            errors = merged_errors
            timings = merged_timings
            memory_bytes = merged_memory
            error_lines = merged_error_lines
            avail_cols = merged_avail
            order = merged_order
    else:
        # Complete cache miss — execute from scratch
        logger.debug(
            "preview_cache_miss",
            fingerprint=fp[:8],
            target=target_node_id,
            prev_fingerprint=(_preview_cache.fingerprint or "")[:8],
        )
        raw_outputs, order, errors, timings, memory_bytes, error_lines, avail_cols = _eager_execute(
            graph,
            target_node_id,
            row_limit,
            source=source,
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
        )

    # Pre-compute schema warnings for instance nodes by comparing the
    # columns available at the instance's inputs vs the original's inputs.
    node_map = graph.node_map
    parents_of = graph.parents_of

    schema_warnings: dict[str, list[SchemaWarning]] = {}
    for nid in order:
        ref = node_map[nid].data.config.get("instanceOf")
        if not ref or ref not in node_map:
            continue
        # Columns feeding into the original node
        orig_input_cols: set[str] = set()
        for pid in parents_of.get(ref, []):
            df = eager_outputs.get(pid)
            if df is not None:
                orig_input_cols.update(df.columns)
        # Columns feeding into the instance node
        inst_input_cols: set[str] = set()
        for pid in parents_of.get(nid, []):
            df = eager_outputs.get(pid)
            if df is not None:
                inst_input_cols.update(df.columns)
        missing = orig_input_cols - inst_input_cols
        if missing:
            schema_warnings[nid] = [
                SchemaWarning(column=c, status="missing") for c in sorted(missing)
            ]

    results: dict[str, NodeResult] = {}
    for nid in order:
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
        if df is None:
            results[nid] = NodeResult(
                status="error",
                error="No output",
                timing_ms=timings.get(nid, 0),
                memory_bytes=memory_bytes.get(nid, 0),
            )
            continue
        columns = [ColumnInfo(name=c, dtype=str(df[c].dtype)) for c in df.columns]
        # available_columns = full column set before selected_columns filtering
        avail = avail_cols.get(nid)
        avail_col_infos = [ColumnInfo(name=n, dtype=d) for n, d in avail] if avail else columns
        results[nid] = NodeResult(
            status="ok",
            row_count=len(df),
            column_count=len(df.columns),
            columns=columns,
            available_columns=avail_col_infos,
            preview=df.head(max_preview_rows).to_dicts(),
            timing_ms=timings.get(nid, 0),
            memory_bytes=memory_bytes.get(nid, 0),
            schema_warnings=schema_warnings.get(nid, []),
        )

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
) -> tuple[
    dict[str, pl.DataFrame | None],
    list[str],
    dict[str, str],
    dict[str, float],
    dict[str, int],
    dict[str, int],
    dict[str, list[tuple[str, str]]],
]:
    """Execute the graph eagerly in topo order.

    Returns (outputs, order, errors, timings, memory_bytes, error_lines,
    available_columns) where errors maps node_id → message for nodes that
    failed, timings maps node_id → execution milliseconds, memory_bytes maps
    node_id → output DataFrame size in bytes, error_lines maps
    node_id → 1-based line number in user code for the error, and
    available_columns maps node_id → list of (name, dtype) pairs before
    any selected_columns filtering.
    """
    preamble_error: str | None = None
    try:
        preamble_ns = _compile_preamble(
            graph.preamble or "",
            pipeline_dir=_pipeline_dir(graph),
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


def execute_sink(graph: PipelineGraph, sink_node_id: str, source: str = "live") -> SinkResponse:
    """Execute the pipeline up to a sink node and write its input to disk.

    Sinks are batch-only — they always run with a non-``"live"`` source
    so that model scoring uses the disk-batched path, keeping memory bounded.
    The *source* parameter is still accepted (and passed through for
    source-switch routing) but is coerced away from ``"live"`` for scoring.

    Uses Polars streaming sinks (``sink_parquet`` / ``sink_csv``) so the
    full dataset is never materialised in memory at once.  Falls back to
    ``collect(engine="streaming")`` + eager write if the streaming sink raises
    (e.g. when the plan contains an operation that doesn't support the
    streaming engine).

    This is called on-demand (not during normal run/preview).
    Returns a ``SinkResponse`` with row count and output path.
    """
    sink_node = graph.node_map.get(sink_node_id)
    if not sink_node:
        raise ValueError(f"Sink node '{sink_node_id}' not found")

    config = sink_node.data.config
    path = config.get("path", "")
    fmt = config.get("format", "parquet")

    if not path:
        raise ValueError("Sink node has no output path configured")

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

    from haute._polars_utils import _malloc_trim, safe_sink

    # Create a temp directory for join checkpoints.  Multi-input nodes
    # are sunk to parquet here so Polars sees each join as an independent
    # plan, avoiding chained-join memory accumulation (#24206).
    # The directory (and all checkpoint files) is cleaned up in finally.
    tmp_dir = tempfile.mkdtemp(prefix="haute_sink_")
    checkpoint_path = Path(tmp_dir)

    # Reduce streaming chunk size for sink operations to lower per-step
    # peak memory.  The default is auto-sized and can be too aggressive
    # for wide schemas (100+ columns).
    # NOTE: pl.Config is process-global — not thread-safe for concurrent
    # sinks.  This is acceptable because sinks run sequentially (GUI is
    # single-user, CLI `run` is sequential, background jobs don't use
    # execute_sink).
    _prev_chunk_size = pl.Config.state().get("POLARS_STREAMING_CHUNK_SIZE")
    pl.Config.set_streaming_chunk_size(50_000)

    try:
        # Sink path: use cached preamble (no GUI edits expected during
        # batch runs).  Saves 50-500 ms of utility module re-import.
        preamble_ns = _compile_preamble(
            graph.preamble or "",
            force_refresh=False,
            pipeline_dir=_pipeline_dir(graph),
        )

        def _run_lazy() -> pl.LazyFrame:
            lazy_outputs, _order, _parents, _names = _execute_lazy(
                graph,
                _build_node_fn,
                target_node_id=sink_node_id,
                preamble_ns=preamble_ns or None,
                source=sink_scenario,
                checkpoint_dir=checkpoint_path,
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

        safe_sink(lf, out, fmt=fmt)
        logger.info("sink_written", path=path, format=fmt)
        del lf
        gc.collect()
        _malloc_trim()

        # Read back row count cheaply from file metadata.
        if fmt == "csv":
            row_count = pl.scan_csv(out).select(pl.len()).collect().item()
        else:
            row_count = pl.scan_parquet(out).select(pl.len()).collect().item()

        return SinkResponse(
            status="ok",
            message=f"Wrote {row_count:,} rows to {path}",
            row_count=row_count,
            path=path,
            format=fmt,
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        # Restore previous streaming chunk size if one was explicitly set.
        # When _prev_chunk_size is None (Polars auto-default), skip the
        # restore — Polars does not accept 0 and has no "unset" API.
        if _prev_chunk_size is not None:
            pl.Config.set_streaming_chunk_size(int(_prev_chunk_size))
