"""Lazy and eager graph execution — shared by executor, trace, and scorer."""

from __future__ import annotations

import gc
import re
import time
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import Any, NamedTuple

import polars as pl

from haute._builders import get_column_contract
from haute._logging import get_logger
from haute._polars_utils import _malloc_trim, safe_sink
from haute._topo import ancestors, topo_sort_ids
from haute._types import (
    GraphEdge,
    GraphNode,
    NodeType,
    PipelineGraph,
    _Frame,
    _sanitize_func_name,
    build_parents_of,
    resolve_orig_source_names,
)

logger = get_logger(component="execute")


# ---------------------------------------------------------------------------
# Checkpoint projection — backward column analysis
# ---------------------------------------------------------------------------


def _compute_needed_columns(
    order: list[str],
    children_of: dict[str, list[str]],
    node_map: dict[str, GraphNode],
) -> dict[str, set[str] | None]:
    """Backward pass: compute minimal columns needed from each node's output.

    Walks the graph in reverse topological order.  For each node *n*,
    ``needed[n]`` is the set of columns from *n*'s output that any
    downstream consumer actually uses.  ``None`` means "all columns"
    (cannot be determined — an opaque node is downstream).

    Column contracts (which columns each node creates and reads) are
    provided by the builder registry via ``get_column_contract``.  This
    keeps the column knowledge colocated with the builder that defines
    the node's runtime behaviour.
    """
    needed: dict[str, set[str] | None] = {}

    for nid in reversed(order):
        node = node_map[nid]
        children = children_of.get(nid, [])

        if not children:
            # Terminal node — determine what it needs from its input.
            if node.data.nodeType == NodeType.OUTPUT:
                fields = node.data.config.get("fields") or []
                needed[nid] = set(fields) if fields else None
            else:
                needed[nid] = None
            continue

        # Union of what all children need from this node's output.
        needed_by_children: set[str] | None = set()
        for cid in children:
            child_node = node_map[cid]
            child_needed = needed.get(cid)

            if child_needed is None:
                # Child needs all columns → we need all columns.
                needed_by_children = None
                break

            produced, referenced = get_column_contract(
                child_node.data.nodeType,
                child_node.data.config,
            )

            if produced is None or referenced is None:
                # Opaque child — can't determine what it needs.
                needed_by_children = None
                break

            # Child needs from this node: columns needed downstream
            # (minus what child creates) plus columns child reads.
            from_parent = (child_needed - produced) | referenced
            needed_by_children |= from_parent  # type: ignore[operator]

        needed[nid] = needed_by_children

    return needed


# ---------------------------------------------------------------------------
# Adaptive checkpoint strategy
# ---------------------------------------------------------------------------

# Number of checkpoints between gc.collect() + _malloc_trim() calls.
# Polars objects use Rust Arc refcounting and are freed immediately on
# ``del``; Python gc.collect() only helps with cyclic garbage (rare here).
# Batching avoids the overhead of scanning all Python objects per checkpoint.
_GC_BATCH_INTERVAL = 3


class _CheckpointAction(StrEnum):
    """What to do at a potential checkpoint boundary."""

    SKIP = "skip"
    """Keep the LazyFrame as-is — no materialization needed."""

    COLLECT_LAZY = "collect_lazy"
    """Materialize in RAM via ``collect().lazy()`` to break plan
    duplication without disk I/O.  Only used when the estimated
    intermediate fits comfortably in available memory."""

    PARQUET = "parquet"
    """Sink to a temp parquet file and replace with ``scan_parquet``.
    The safest option — frees RAM and isolates the query plan."""


def _checkpoint_decision(
    nid: str,
    is_source: bool,
    n_parents: int,
    n_children: int,
    feeds_join: bool,
    node_map: dict[str, GraphNode],
    scenario: str,
) -> _CheckpointAction:
    """Decide whether and how to checkpoint a node's output.

    Uses the same three structural triggers as before (joins, fan-outs,
    join-feeders) but skips MODEL_SCORE nodes in batch mode because
    the batched scorer already sinks to temp parquet and returns
    ``scan_parquet(scored_path)`` — an implicit checkpoint.  Adding
    another parquet round-trip on top is pure waste.
    """
    if is_source:
        return _CheckpointAction.SKIP

    needs_checkpoint = n_parents > 1 or n_children > 1 or feeds_join
    if not needs_checkpoint:
        return _CheckpointAction.SKIP

    # MODEL_SCORE in batch mode already returns scan_parquet — skip.
    node = node_map.get(nid)
    if node is not None and node.data.nodeType == NodeType.MODEL_SCORE and scenario != "live":
        return _CheckpointAction.SKIP

    return _CheckpointAction.PARQUET


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _apply_column_renames(
    frame: pl.LazyFrame | pl.DataFrame,
    config: dict[str, Any],
) -> pl.LazyFrame | pl.DataFrame:
    """Apply column renames from *config*'s ``column_renames``.

    ``column_renames`` is a ``dict[str, str]`` mapping original column names
    to new names.  Only renames for columns that actually exist in the frame
    are applied.  A no-op when the dict is absent or empty.
    """
    renames: dict[str, str] | None = config.get("column_renames")
    if not renames:
        return frame

    if isinstance(frame, pl.LazyFrame):
        all_cols = set(frame.collect_schema().names())
    else:
        all_cols = set(frame.columns)

    valid = {old: new for old, new in renames.items() if old in all_cols and old != new}
    if valid:
        return frame.rename(valid)
    return frame


def _apply_selected_columns(
    frame: pl.LazyFrame | pl.DataFrame,
    config: dict[str, Any],
) -> pl.LazyFrame | pl.DataFrame:
    """Filter *frame* to only the columns listed in *config*'s ``selected_columns``.

    If ``selected_columns`` is absent, empty, or names no valid columns the
    frame is returned unchanged.  Only columns that actually exist in the
    frame are kept, and the filter is a no-op when every column is selected
    (avoids an unnecessary projection).
    """
    sel_cols: list[str] | None = config.get("selected_columns")
    if not sel_cols:
        return frame

    if isinstance(frame, pl.LazyFrame):
        all_cols = frame.collect_schema().names()
    else:
        all_cols = frame.columns

    seen: set[str] = set()
    valid = []
    for c in sel_cols:
        if c in all_cols and c not in seen:
            valid.append(c)
            seen.add(c)
    if valid and len(valid) < len(all_cols):
        return frame.select(valid)
    return frame


def _prune_live_switch_edges(
    edges: list[GraphEdge],
    node_map: dict[str, GraphNode],
    source: str,
) -> list[GraphEdge]:
    """Remove edges to live_switch nodes from inputs inactive for *source*.

    A live_switch node's config contains ``input_scenario_map`` which maps
    each input name to the scenario it serves.  Only edges from inputs
    matching the active source are kept; the unused branch is pruned so
    it is neither executed nor shown in profilers.
    """
    switch_nodes = {
        nid: node for nid, node in node_map.items() if node.data.nodeType == NodeType.LIVE_SWITCH
    }
    if not switch_nodes:
        return edges

    exclude: set[tuple[str, str]] = set()
    for nid, node in switch_nodes.items():
        ism: dict[str, str] = node.data.config.get(
            "input_scenario_map",
            {},
        )
        if not ism:
            continue
        # If no input matches the active source, keep all edges
        # so the runtime fallback in switch_fn still works.
        if source not in ism.values():
            continue
        # For each direct parent edge, check if its name maps to a
        # different source — if so, exclude the edge.
        for e in edges:
            if e.target != nid:
                continue
            parent = node_map.get(e.source)
            if parent is None:
                continue
            parent_name = _sanitize_func_name(parent.data.label)
            mapped = ism.get(parent_name)
            if mapped is not None and mapped != source:
                exclude.add((e.source, nid))

    if not exclude:
        return edges
    return [e for e in edges if (e.source, e.target) not in exclude]


def _prepare_graph(
    graph: PipelineGraph,
    target_node_id: str | None = None,
    source: str = "live",
) -> tuple[
    dict[str, GraphNode],  # node_map
    list[str],  # order (topo-sorted node IDs)
    dict[str, list[str]],  # parents_of
    dict[str, str],  # id_to_name
]:
    """Shared graph preparation: filter, topo-sort, and build lookups.

    Returns (node_map, order, parents_of, id_to_name).
    """
    node_map = graph.node_map
    edges = _prune_live_switch_edges(graph.edges, node_map, source)
    all_ids = set(node_map.keys())

    if target_node_id:
        needed = ancestors(target_node_id, edges, all_ids)
    else:
        needed = all_ids

    relevant_edges = [e for e in edges if e.source in needed and e.target in needed]
    order = topo_sort_ids([nid for nid in all_ids if nid in needed], relevant_edges)

    parents_of = build_parents_of(relevant_edges, set(order))

    id_to_name: dict[str, str] = {}
    for nid in order:
        label = node_map[nid].data.label
        id_to_name[nid] = _sanitize_func_name(label)

    return node_map, order, parents_of, id_to_name


def _execute_lazy(
    graph: PipelineGraph,
    build_node_fn: Callable,
    target_node_id: str | None = None,
    preamble_ns: dict | None = None,
    source: str = "live",
    checkpoint_dir: Path | None = None,
) -> tuple[dict[str, _Frame], list[str], dict[str, list[str]], dict[str, str]]:
    """Execute a graph lazily and return per-node LazyFrames.

    Used by execute_sink (batch writes) and score_graph (deploy scoring)
    where Polars can optimise the full lazy plan end-to-end.
    Interactive paths (preview, trace) use eager execution with caching
    instead — see executor._eager_execute and trace.execute_trace.

    Args:
        graph: React Flow graph with "nodes" and "edges".
        build_node_fn: Function (node_dict, source_names) -> (name, fn, is_source).
        target_node_id: If set, only execute ancestors of this node.
        source: Active execution source (``"live"`` = eager scoring).
        checkpoint_dir: If set, multi-input nodes (joins) and fan-out
            nodes (>1 downstream consumer) are checkpointed to parquet
            files in this directory and replaced with ``scan_parquet``
            references.  This breaks both chained-join memory
            accumulation and plan duplication across branches
            (GitHub pola-rs/polars#24206).

    Returns:
        (lazy_outputs, order, parents_of, id_to_name)
    """
    node_map, order, parents_of, id_to_name = _prepare_graph(
        graph,
        target_node_id,
        source=source,
    )

    # Full parent lookup from ALL edges for instance resolution
    all_parents = graph.parents_of

    # Build executable functions — delegates to _build_funcs with
    # row_limit=None (lazy path never caps source output).
    funcs = _build_funcs(
        order,
        node_map,
        parents_of,
        id_to_name,
        all_parents,
        build_node_fn,
        row_limit=None,
        preamble_ns=preamble_ns,
        source=source,
    )

    # Execute - all intermediate results stay lazy
    lazy_outputs: dict[str, _Frame] = {}

    # Count downstream consumers per node so we can checkpoint fan-out
    # points (nodes whose output feeds >1 consumer).  Without this,
    # Polars duplicates the entire upstream plan for each branch —
    # e.g. a 38 GB JSONL scan runs twice when two siblings share a parent.
    children_count: dict[str, int] = {nid: 0 for nid in order}
    children_of: dict[str, list[str]] = {nid: [] for nid in order}
    for nid, pids in parents_of.items():
        for pid in pids:
            if pid in children_count:
                children_count[pid] += 1
                children_of[pid].append(nid)

    # Backward column analysis: compute the minimal set of columns
    # needed at each node's output so checkpoints can project away
    # unneeded columns before writing to parquet.  Only computed when
    # checkpointing is active — the analysis may trigger model loading
    # (cached) and is wasted work for non-checkpoint paths.
    needed_cols: dict[str, set[str] | None] = (
        _compute_needed_columns(order, children_of, node_map) if checkpoint_dir is not None else {}
    )

    # Separate mutable counter for tracking remaining downstream consumers.
    # Decremented at checkpoint time so we know when a parent's LazyFrame
    # can be safely deleted (freeing Polars/Rust Arrow buffers).
    remaining: dict[str, int] = dict(children_count)

    # Batch gc.collect() calls — Polars objects use Rust Arc refcounting
    # and are freed immediately on ``del``.  gc.collect() only helps with
    # cyclic Python garbage (rare here) and adds 50-200 ms per call.
    checkpoints_since_gc = 0

    for nid in order:
        fn, is_source = funcs[nid]
        if is_source:
            lf = fn()
        else:
            input_ids = parents_of.get(nid, [])
            missing = [pid for pid in input_ids if pid not in lazy_outputs]
            if missing:
                raise ValueError(
                    f"Node '{nid}' is missing input(s) from: {missing}. "
                    "Upstream node(s) may have failed or not been registered."
                )
            input_lfs = [lazy_outputs[pid] for pid in input_ids]
            if not input_lfs:
                raise ValueError(f"No input data available for node '{nid}'")
            lf = fn(*input_lfs)

        if isinstance(lf, pl.DataFrame):
            lf = lf.lazy()

        # Apply selected_columns filter first (uses pre-rename names),
        # then column renames on the surviving columns.
        lf = _apply_selected_columns(lf, node_map[nid].data.config)
        lf = _apply_column_renames(lf, node_map[nid].data.config)

        # Adaptive checkpoint to break Polars plan duplication and
        # chained-join memory accumulation (pola-rs/polars#24206).
        #
        # Three structural triggers (joins, fan-outs, join-feeders) are
        # evaluated by _checkpoint_decision which chooses the cheapest
        # safe strategy:
        #   PARQUET      — disk round-trip, safest, frees RAM
        #   COLLECT_LAZY — in-memory materialization, no I/O, breaks
        #                  plan duplication but holds data in RAM
        #   SKIP         — keep the LazyFrame as-is (source nodes,
        #                  batch MODEL_SCORE which already checkpoints
        #                  internally, or nodes that don't need it)
        n_parents = len(parents_of.get(nid, []))
        n_children = children_count.get(nid, 0)
        feeds_join = any(len(parents_of.get(cid, [])) > 1 for cid in children_of.get(nid, []))

        action = _checkpoint_decision(
            nid,
            is_source,
            n_parents,
            n_children,
            feeds_join,
            node_map,
            source or "live",
        )

        if checkpoint_dir is not None and action == _CheckpointAction.PARQUET:
            tmp = checkpoint_dir / f"{nid}.parquet"

            # Project to only the columns needed downstream before
            # writing the checkpoint.  This avoids writing (and later
            # re-reading) columns that no downstream node will use —
            # e.g. 100 source columns when the model only needs 8.
            sink_lf = lf if isinstance(lf, pl.LazyFrame) else lf.lazy()
            projection = needed_cols.get(nid)
            if projection is not None:
                schema_cols = sink_lf.collect_schema().names()
                valid = [c for c in schema_cols if c in projection]
                if valid and len(valid) < len(schema_cols):
                    logger.info(
                        "checkpoint_projection",
                        node_id=nid,
                        total_cols=len(schema_cols),
                        projected_cols=len(valid),
                    )
                    sink_lf = sink_lf.select(valid)

            safe_sink(sink_lf, tmp, fast_checkpoint=True)

            # Drop the old LazyFrame (and any cached Arrow buffers it
            # holds) before replacing with a fresh scan reference.
            del lf
            # Drop parent LazyFrame refs that have no remaining consumers
            # downstream — lets Polars/Rust release the backing buffers.
            # Source nodes are kept: they hold cheap scan_* references and
            # callers may need them (e.g. optimiser extracting banding factors).
            for pid in parents_of.get(nid, []):
                remaining[pid] -= 1
                _, pid_is_source = funcs.get(pid, (None, False))
                if remaining[pid] <= 0 and pid in lazy_outputs and not pid_is_source:
                    del lazy_outputs[pid]

            checkpoints_since_gc += 1
            if checkpoints_since_gc >= _GC_BATCH_INTERVAL:
                gc.collect()
                _malloc_trim()
                checkpoints_since_gc = 0

            lf = pl.scan_parquet(tmp)
            logger.info("checkpoint_parquet", node_id=nid, path=str(tmp))

        lazy_outputs[nid] = lf

    return lazy_outputs, order, parents_of, id_to_name


# ---------------------------------------------------------------------------
# Eager execution core — shared by executor (preview) and trace
# ---------------------------------------------------------------------------


def _build_funcs(
    order: list[str],
    node_map: dict[str, GraphNode],
    parents_of: dict[str, list[str]],
    id_to_name: dict[str, str],
    all_parents: dict[str, list[str]],
    build_node_fn: Callable,
    *,
    row_limit: int | None = None,
    preamble_ns: dict | None = None,
    source: str = "live",
) -> dict[str, tuple[Callable, bool]]:
    """Build per-node executable functions from the graph.

    Shared between eager and lazy paths.  ``row_limit`` is forwarded to
    ``build_node_fn`` so Databricks sources can push LIMIT into SQL.
    ``preamble_ns`` is a compiled namespace of user-defined helpers from
    the pipeline file's preamble section.
    ``source`` is the active execution source forwarded to build_node_fn.
    """
    funcs: dict[str, tuple[Callable, bool]] = {}
    for nid in order:
        src_names = [id_to_name[pid] for pid in parents_of.get(nid, []) if pid in id_to_name]
        orig_src_names = resolve_orig_source_names(
            node_map[nid],
            node_map,
            all_parents,
            id_to_name,
        )
        _, fn, is_source = build_node_fn(
            node_map[nid],
            source_names=src_names,
            row_limit=row_limit,
            node_map=node_map,
            orig_source_names=orig_src_names,
            preamble_ns=preamble_ns,
            source=source,
        )
        funcs[nid] = (fn, is_source)
    return funcs


def _extract_error_line(exc: Exception) -> int | None:
    """Extract user-code line number from an exception, if available.

    - SyntaxError: use .lineno (already adjusted by _exec_user_code).
    - _user_code_line attr: set by _exec_user_code from the traceback
      for runtime errors like NameError that don't embed line info
      in their message string.
    - Fallback: parse 'line N' from the error message
      (already adjusted by _exec_user_code's regex substitution).
    - Returns None when no line info is available.
    """
    if isinstance(exc, SyntaxError) and exc.lineno is not None:
        return exc.lineno
    user_line: int | None = getattr(exc, "_user_code_line", None)
    if user_line is not None:
        return int(user_line)
    match = re.search(r"\bline (\d+)\b", str(exc))
    if match:
        return int(match.group(1))
    return None


class EagerResult(NamedTuple):
    """Result of eager graph execution."""

    outputs: dict[str, pl.DataFrame | None]
    order: list[str]
    parents_of: dict[str, list[str]]
    node_map: dict[str, GraphNode]
    id_to_name: dict[str, str]
    errors: dict[str, str]
    timings: dict[str, float]
    memory_bytes: dict[str, int]
    error_lines: dict[str, int]
    available_columns: dict[str, list[tuple[str, str]]]


def _execute_eager_core(
    graph: PipelineGraph,
    build_node_fn: Callable,
    target_node_id: str | None = None,
    row_limit: int | None = None,
    swallow_errors: bool = False,
    preamble_ns: dict | None = None,
    source: str = "live",
) -> EagerResult:
    """Execute the graph eagerly in topo order and collect DataFrames.

    Shared core for the preview executor and the trace engine.

    Args:
        graph: React Flow graph.
        build_node_fn: ``(node, source_names=..., ...) -> (name, fn, is_source)``.
        target_node_id: If set, only execute ancestors of this node.
        row_limit: Cap source-node output to this many rows.
        swallow_errors: If ``True``, record per-node errors and continue
            (preview behaviour).  If ``False``, raise immediately (trace).
        source: Active execution source (``"live"`` = eager scoring).

    Returns:
        An ``EagerResult`` with named fields for outputs, order,
        parents_of, node_map, id_to_name, errors, timings, and
        memory_bytes.
    """
    node_map, order, parents_of, id_to_name = _prepare_graph(
        graph,
        target_node_id,
        source=source,
    )

    # Full parent lookup from ALL edges for instance resolution
    all_parents = graph.parents_of

    funcs = _build_funcs(
        order,
        node_map,
        parents_of,
        id_to_name,
        all_parents,
        build_node_fn,
        row_limit=row_limit,
        preamble_ns=preamble_ns,
        source=source,
    )

    eager_outputs: dict[str, pl.DataFrame | None] = {}
    errors: dict[str, str] = {}
    error_lines: dict[str, int] = {}
    timings: dict[str, float] = {}
    memory_bytes: dict[str, int] = {}
    available_columns: dict[str, list[tuple[str, str]]] = {}

    for nid in order:
        fn, is_source = funcs[nid]
        t0 = time.perf_counter()
        try:
            if is_source:
                result = fn()
                if row_limit and isinstance(result, (pl.LazyFrame, pl.DataFrame)):
                    result = result.head(row_limit)
            else:
                input_ids = parents_of.get(nid, [])
                missing_parents = [pid for pid in input_ids if pid not in eager_outputs]
                if missing_parents:
                    raise ValueError(
                        f"Node '{nid}' is missing input(s) from: {missing_parents}. "
                        "Upstream node(s) may not have been registered."
                    )
                failed_parents = [pid for pid in input_ids if eager_outputs[pid] is None]
                if failed_parents:
                    eager_outputs[nid] = None
                    continue
                input_lfs = [
                    df.lazy()
                    for pid in input_ids
                    if pid in eager_outputs and (df := eager_outputs[pid]) is not None
                ]
                if not input_lfs:
                    raise ValueError(
                        f"No input data available for node '{nid}'",
                    )
                result = fn(*input_lfs)

            df = result.collect(engine="streaming") if isinstance(result, pl.LazyFrame) else result

            # Capture full column set before selected_columns filtering
            available_columns[nid] = [(c, str(df[c].dtype)) for c in df.columns]

            # Apply selected_columns filter first (uses pre-rename names),
            # then column renames on the surviving columns.
            filtered = _apply_selected_columns(df, node_map[nid].data.config)
            df = filtered if isinstance(filtered, pl.DataFrame) else filtered.collect()
            renamed = _apply_column_renames(df, node_map[nid].data.config)
            df = renamed if isinstance(renamed, pl.DataFrame) else renamed.collect()

            eager_outputs[nid] = df
            memory_bytes[nid] = int(df.estimated_size("b"))
        except Exception as exc:
            if not swallow_errors:
                raise
            logger.error("node_failed", node_id=nid, error=str(exc))
            eager_outputs[nid] = None
            errors[nid] = str(exc)
            error_line = _extract_error_line(exc)
            if error_line is not None:
                error_lines[nid] = error_line
        timings[nid] = round((time.perf_counter() - t0) * 1000, 1)

    return EagerResult(
        eager_outputs,
        order,
        parents_of,
        node_map,
        id_to_name,
        errors,
        timings,
        memory_bytes,
        error_lines,
        available_columns,
    )
