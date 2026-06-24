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

from haute._builders import _passthrough_fn
from haute._contracts import Contract, get_column_contract
from haute._graph_utils import (
    _sanitize_func_name,
    build_parents_of,
    resolve_orig_source_names,
)
from haute._logging import get_logger
from haute._path_resolution import resolve_runtime_file_path
from haute._polars_utils import _malloc_trim, safe_sink
from haute._topo import ancestors, topo_sort_ids
from haute._types import (
    GraphEdge,
    GraphNode,
    NodeType,
    PipelineGraph,
    _Frame,
)
from haute.errors import ContractMismatchError

logger = get_logger(component="execute")


_PATH_CONFIG_BY_NODE_TYPE: dict[NodeType, str] = {
    NodeType.API_INPUT: "path",
    NodeType.DATA_SOURCE: "path",
    NodeType.EXTERNAL_FILE: "path",
    NodeType.DATA_SINK: "path",
}


def _resolve_graph_paths(graph: PipelineGraph) -> PipelineGraph:
    """Resolve project/pipeline-relative file paths before building node functions."""
    if not graph.source_file:
        return graph
    nodes: list[GraphNode] = []
    changed = False
    for node in graph.nodes:
        config = node.data.config
        key = _PATH_CONFIG_BY_NODE_TYPE.get(node.data.nodeType)
        if node.data.nodeType == NodeType.OPTIMISER_APPLY and config.get("sourceType") == "file":
            key = "artifact_path"
        if key is None:
            nodes.append(node)
            continue
        raw_path = config.get(key)
        if isinstance(raw_path, str) and raw_path:
            resolved = str(
                resolve_runtime_file_path(
                    raw_path,
                    source_file=graph.source_file,
                    prefer="project",
                )
            )
            if resolved != raw_path:
                data = node.data.model_copy(update={"config": {**config, key: resolved}})
                nodes.append(node.model_copy(update={"data": data}))
                changed = True
            else:
                nodes.append(node)
        else:
            nodes.append(node)
    if not changed:
        return graph
    return graph.model_copy(update={"nodes": nodes})


# ---------------------------------------------------------------------------
# Column contract enforcement
# ---------------------------------------------------------------------------


def _is_boundary_check_exception(exc: BaseException) -> bool:
    """Return whether *exc* should degrade contract checking to opaque."""
    from haute.errors import ConfigError

    if isinstance(exc, (ConfigError, OSError)):
        return True
    try:
        from mlflow.exceptions import MlflowException  # type: ignore[import-untyped]
    except ImportError:
        return False
    return isinstance(exc, MlflowException)


def _effective_contract(node: GraphNode) -> Contract:
    """Return the effective contract for a node at boundary-check time.

    Combines the builder-derived contract with any user-declared
    contract on the node's config so the executor has a single answer
    to "what columns does this node read / produce?".

    User-declared sides override the builder when they are concrete
    (non-None).  This lets a user tighten an opaque POLARS contract to
    a concrete set; the reverse — a user declaring opaque on top of a
    concrete builder contract — is accepted silently because the parser
    has already cross-checked against ``get_column_contract``.

    If the builder contract raises (MLflow unreachable, config mis-set
    in a way only the builder knows about), the executor treats the
    node as opaque rather than failing the whole run: the runtime path
    for such nodes is typically ``_passthrough_fn`` and the caller will
    still get the original error on the direct ``_model_score_columns``
    call path that the loud-errors suite exercises.  Silencing here is
    scoped strictly to the boundary check; it does not hide the
    configuration issue elsewhere in the system.
    """
    from haute.errors import ConfigError

    try:
        builder = Contract.from_tuple(get_column_contract(node.data.nodeType, node.data.config))
    except Exception as exc:
        if not _is_boundary_check_exception(exc):
            raise
        # Contract resolution for MODEL_SCORE etc. may touch MLflow /
        # external stores.  A transient or deploy-mode lookup failure
        # (ConfigError, OSError, MLflow REST) must not prevent the
        # pipeline from running — the fn builder path has its own
        # error reporting and will surface the real problem when the
        # node actually executes.  We fall back to opaque so the
        # boundary check is skipped for this node; the actual node
        # code path still runs and still fails loudly via whichever
        # error it has always produced.  Programmer errors
        # (AttributeError / TypeError / KeyError) propagate.
        if not isinstance(exc, ConfigError):
            logger.debug(
                "effective_contract_unresolved",
                node_id=node.id,
                node_type=node.data.nodeType.value,
                error=repr(exc),
            )
        builder = Contract.opaque()
    declared_raw = node.data.config.get("contract")
    if declared_raw is None:
        return builder
    try:
        declared = Contract.from_user_declared(declared_raw)
    except ValueError as exc:
        # Malformed contract on a user's graph should raise up so the
        # mistake is visible.  The executor is the wrong place to
        # silently drop it.
        raise ContractMismatchError(
            "Node contract annotation is malformed and cannot be interpreted.",
            node_id=node.id,
            node_type=node.data.nodeType.value,
            reason=str(exc),
        ) from exc
    if declared is None:
        return builder
    inputs = declared.inputs if declared.inputs is not None else builder.inputs
    outputs = declared.outputs if declared.outputs is not None else builder.outputs
    return Contract(inputs=inputs, outputs=outputs)


def _assert_inputs_satisfy_contract(
    node: GraphNode,
    contract: Contract,
    upstream_columns: frozenset[str],
) -> None:
    """Raise ``ContractMismatchError`` if *upstream_columns* is missing
    any column the node's contract says it reads.

    No-op when the contract's input side is opaque (``None``).
    """
    if contract.inputs is None:
        return
    missing = contract.inputs - upstream_columns
    if not missing:
        return
    raise ContractMismatchError(
        "Input columns required by the node's contract are missing from the upstream frame.",
        node_id=node.id,
        node_type=node.data.nodeType.value,
        missing=sorted(missing),
        extra=sorted(upstream_columns - contract.inputs),
        declared_inputs=sorted(contract.inputs),
        upstream_columns=sorted(upstream_columns),
    )


def _assert_outputs_satisfy_contract(
    node: GraphNode,
    contract: Contract,
    output_columns: frozenset[str],
) -> None:
    """Raise ``ContractMismatchError`` if *output_columns* is missing
    any column the node's contract promised to produce.

    We check ⊇ (outputs must be present) rather than == because
    pass-through style nodes legitimately carry additional columns
    through from their input.  A declared output that is absent is a
    bug (typo or buggy user code); an extra column is expected.

    No-op when the contract's output side is opaque (``None``).
    """
    if contract.outputs is None:
        return
    missing = contract.outputs - output_columns
    if not missing:
        return
    raise ContractMismatchError(
        "Output columns promised by the node's contract are missing from the node's result.",
        node_id=node.id,
        node_type=node.data.nodeType.value,
        missing=sorted(missing),
        extra=sorted(output_columns - contract.outputs),
        declared_outputs=sorted(contract.outputs),
        observed_columns=sorted(output_columns),
    )


def _should_check_contract(contract: Contract) -> bool:
    """Return ``True`` iff either side of *contract* is concrete.

    A fully-opaque contract cannot be disproven, so skipping the check
    saves the per-node column-set computation entirely.  This matters
    for the <5% overhead bound when a pipeline is dominated by opaque
    nodes (user polars transforms).
    """
    return contract.inputs is not None or contract.outputs is not None


# ---------------------------------------------------------------------------
# Checkpoint projection — backward column analysis
# ---------------------------------------------------------------------------


def _compute_needed_columns(
    order: list[str],
    children_of: dict[str, list[str]],
    node_map: dict[str, GraphNode],
) -> dict[str, set[str] | None]:
    """Single reverse-topological sweep computing per-node column needs.

    For each node *n*, ``needed[n]`` is the set of columns from *n*'s
    output that any downstream consumer actually uses.  ``None`` means
    "all columns" (the requirement cannot be determined — an opaque
    node is downstream, or an OUTPUT asks for everything).

    Each node also has a *contribution* — the set a parent must union
    in for this node as a child, namely
    ``(needed[n] - produced_n) | referenced_n``.  Contributions are
    cached per node so a parent with fan-in ``k`` folds ``k``
    pre-computed sets instead of re-running contract lookup and set
    algebra ``k`` times.  This turns the pass from
    ``O(edges × contract_lookups)`` into ``O(V + E)`` with one contract
    lookup per node.

    Opaque contribution (``None``) from any child forces the parent's
    ``needed`` to ``None`` as a short-circuit, matching the previous
    backward-pass semantics byte-for-byte.
    """
    needed: dict[str, set[str] | None] = {}
    # Per-node contribution to parents.  ``None`` means "parent must
    # fall to None" — either this node is opaque or any of its
    # descendants is.  Each entry is written exactly once per node.
    contribution: dict[str, set[str] | None] = {}

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
        else:
            # Union of pre-computed child contributions.  Each
            # contribution was set in this same loop when the child
            # was visited (reverse topo order guarantees children are
            # processed before parents).  A single ``None`` child
            # contribution short-circuits the union to ``None``.
            acc: set[str] | None = set()
            for cid in children:
                child_contrib = contribution.get(cid)
                if child_contrib is None:
                    acc = None
                    break
                acc |= child_contrib  # type: ignore[operator]
            needed[nid] = acc

        # Cache this node's contribution to its parents.  Computed
        # once here; every parent that visits this node as a child
        # reads the cached value instead of re-fetching the contract
        # and re-doing the set algebra.
        my_needed = needed[nid]
        if my_needed is None:
            contribution[nid] = None
            continue
        produced, referenced = get_column_contract(
            node.data.nodeType,
            node.data.config,
        )
        if produced is None or referenced is None:
            contribution[nid] = None
        else:
            contribution[nid] = (my_needed - produced) | referenced

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

    # ``node_map`` is an insertion-ordered ``dict``; deriving the ID list
    # by iterating it preserves that order all the way into
    # ``topo_sort_ids``'s insertion-order tie-break.  Going through a
    # ``set`` would have introduced hash-randomisation into sibling
    # execution order.
    all_ids = set(node_map)
    if target_node_id:
        needed = ancestors(target_node_id, edges, all_ids)
    else:
        needed = all_ids

    relevant_edges = [e for e in edges if e.source in needed and e.target in needed]
    order = topo_sort_ids([nid for nid in node_map if nid in needed], relevant_edges)

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
    enforce_contracts: bool = False,
    preserve_node_ids: set[str] | frozenset[str] | None = None,
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
        preserve_node_ids: Non-source intermediate outputs that must remain
            available to the caller after their final downstream consumer has
            executed. Optimiser ratebook solves use this for the selected
            banding source side input.
        enforce_contracts: When ``True`` (see ``executor.ENFORCE_CONTRACTS``
            for the default), assert declared column contracts at each
            node boundary via ``.collect_schema()``.  Polars computes
            schemas without executing the query, so this stays cheap.
            Production code paths (batch sink, deploy scoring, training,
            optimiser) run through here — enforcement on the lazy path
            is what makes contract coverage real end-to-end.

    Returns:
        (lazy_outputs, order, parents_of, id_to_name)
    """
    graph = _resolve_graph_paths(graph)
    preserved_outputs = frozenset(preserve_node_ids or ())
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

    # Per-node column sets used by the boundary contract checks.  Polars
    # computes schema without executing the query, so collect_schema()
    # is cheap; caching keeps repeated lookups free when the same
    # upstream feeds multiple consumers.
    column_cache: dict[str, frozenset[str]] = {}

    def _columns_of(frame: pl.LazyFrame | pl.DataFrame) -> frozenset[str]:
        lazy_frame = frame if isinstance(frame, pl.LazyFrame) else frame.lazy()
        return frozenset(lazy_frame.collect_schema().names())

    for nid in order:
        fn, is_source = funcs[nid]
        node = node_map[nid]
        contract = _effective_contract(node) if enforce_contracts else None
        check_here = bool(contract) and _should_check_contract(contract)  # type: ignore[arg-type]
        # Builder-wired ``_passthrough_fn`` means the node is in a stub
        # state (MODEL_SCORE without a model, OPTIMISER_APPLY without
        # an artifact).  Its declared contract describes the configured
        # shape the runtime does not produce yet; skip the output check
        # to preserve the "configure later" UX while still enforcing
        # contracts the moment a real function is wired.
        is_passthrough_runtime = fn is _passthrough_fn

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

            if check_here and contract is not None and contract.inputs is not None:
                upstream_col_sets: list[frozenset[str]] = []
                for upstream_pid, upstream_lf in zip(input_ids, input_lfs, strict=True):
                    upstream_cols = column_cache.get(upstream_pid)
                    if upstream_cols is None:
                        upstream_cols = _columns_of(upstream_lf)
                        column_cache[upstream_pid] = upstream_cols
                    upstream_col_sets.append(upstream_cols)
                upstream_cols = frozenset().union(*upstream_col_sets)
                _assert_inputs_satisfy_contract(node, contract, upstream_cols)

            lf = fn(*input_lfs)

        if isinstance(lf, pl.DataFrame):
            lf = lf.lazy()

        # Apply selected_columns filter first (uses pre-rename names),
        # then column renames on the surviving columns.
        lf = _apply_selected_columns(lf, node_map[nid].data.config)
        lf = _apply_column_renames(lf, node_map[nid].data.config)

        if (
            check_here
            and contract is not None
            and contract.outputs is not None
            and not is_passthrough_runtime
        ):
            out_cols = _columns_of(lf)
            column_cache[nid] = out_cols
            _assert_outputs_satisfy_contract(node, contract, out_cols)

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
                if (
                    remaining[pid] <= 0
                    and pid in lazy_outputs
                    and not pid_is_source
                    and pid not in preserved_outputs
                ):
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
        src_ids = [pid for pid in parents_of.get(nid, []) if pid in id_to_name]
        src_names = [id_to_name[pid] for pid in src_ids]
        orig_src_names = resolve_orig_source_names(
            node_map[nid],
            node_map,
            all_parents,
            id_to_name,
        )
        _, fn, is_source = build_node_fn(
            node_map[nid],
            source_names=src_names,
            source_ids=src_ids,
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
    enforce_contracts: bool = True,
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
        enforce_contracts: If ``True`` (default), assert each node's
            column contract at its input and output boundaries.  A
            mismatch always raises ``ContractMismatchError`` regardless
            of *swallow_errors* — the contract is an API-level claim
            and a silent error would defeat the adoption effort.

    Returns:
        An ``EagerResult`` with named fields for outputs, order,
        parents_of, node_map, id_to_name, errors, timings, and
        memory_bytes.
    """
    graph = _resolve_graph_paths(graph)
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

    # Per-node column sets used by the boundary contract checks.  We
    # compute each frame's column set exactly once and reuse it — both
    # as an output check for the producing node and as an input check
    # for its consumer(s).  Polars' ``.columns`` is O(n) in the number
    # of columns, but frozenset construction dominates anyway; caching
    # keeps the contract-enforced path within the <5% budget.
    column_cache: dict[str, frozenset[str]] = {}

    # Fan-out count per node — how many direct children consume this
    # node's output.  Used to add a Polars ``.cache()`` hint when the
    # parent feeds >1 consumer so the optimiser reuses one materialized
    # plan across branches (diamond graphs) instead of duplicating the
    # upstream work.  Each eager_outputs entry is already a concrete
    # DataFrame, so the cache hint only matters once we re-enter a
    # LazyFrame via ``.lazy()`` for the next node's inputs.
    children_count: dict[str, int] = dict.fromkeys(order, 0)
    for _nid, _pids in parents_of.items():
        for _pid in _pids:
            if _pid in children_count:
                children_count[_pid] += 1

    for nid in order:
        fn, is_source = funcs[nid]
        node = node_map[nid]
        contract = _effective_contract(node) if enforce_contracts else None
        check_here = bool(contract) and _should_check_contract(contract)  # type: ignore[arg-type]
        # A node that the builder chose to wire to ``_passthrough_fn`` is
        # running in a stub/unconfigured state (MODEL_SCORE without a
        # loaded model, OPTIMISER_APPLY without an artifact, etc.).  Its
        # contract describes the *configured* shape, which the runtime
        # intentionally does not produce yet.  Skip the output-side
        # check to preserve the "drag node onto canvas, configure later"
        # UX while still enforcing contracts the moment a real function
        # is wired in.
        is_passthrough_runtime = fn is _passthrough_fn
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
                # Add ``.cache()`` on parents that feed >1 consumer so a
                # downstream ``.collect()`` re-uses the materialised plan
                # across branches instead of duplicating upstream work.
                # This is the diamond optimisation: src -> (left, right)
                # -> sink should compute src's plan once, not twice.
                # Parents with exactly one consumer skip the hint — it's
                # cheap but non-zero overhead and adds no value there.
                input_lfs = []
                for pid in input_ids:
                    if pid not in eager_outputs:
                        continue
                    parent_df = eager_outputs[pid]
                    if parent_df is None:
                        continue
                    parent_lf = parent_df.lazy()
                    if children_count.get(pid, 0) > 1:
                        parent_lf = parent_lf.cache()
                    input_lfs.append(parent_lf)
                if not input_lfs:
                    raise ValueError(
                        f"No input data available for node '{nid}'",
                    )

                # Input-side contract check: every column the node's
                # contract says it reads must be present upstream.
                # Using the union across all parents matches how the
                # node's function receives inputs — multi-input joins
                # combine them before the contract columns are read.
                if check_here and contract.inputs is not None:  # type: ignore[union-attr]
                    upstream_cols: frozenset[str] = frozenset().union(
                        *(column_cache[pid] for pid in input_ids if pid in column_cache)
                    )
                    _assert_inputs_satisfy_contract(node, contract, upstream_cols)  # type: ignore[arg-type]

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

            # Output-side contract check: every column the node promises
            # to produce must be present on the result.  We check the
            # post-rename/post-select frame because that's what
            # downstream consumers actually see.  Passthrough-runtime
            # nodes are exempt — see the ``is_passthrough_runtime``
            # note above.
            final_cols = frozenset(df.columns)
            if (
                check_here
                and contract.outputs is not None  # type: ignore[union-attr]
                and not is_passthrough_runtime
            ):
                _assert_outputs_satisfy_contract(node, contract, final_cols)  # type: ignore[arg-type]
            column_cache[nid] = final_cols

            eager_outputs[nid] = df
            memory_bytes[nid] = int(df.estimated_size("b"))
        except ContractMismatchError:
            # Contract errors are API-level — raise even in swallow mode
            # so GUI users see the crisp error instead of a silent
            # per-node "failed" status card.
            raise
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
