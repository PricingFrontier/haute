"""Internal execution-engine facade.

Application layers should import execution helpers from this module instead
of reaching into ``haute._execute_lazy`` or underscore re-exports from
``haute.graph_utils``.  The implementation remains deliberately thin: it
keeps one stable internal boundary while the engine underneath continues to
evolve.
"""

from __future__ import annotations

import atexit
import json
import shutil
import tempfile
import threading
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from haute._cache import GraphFingerprintMemo, canonical_json
from haute._databricks_io import _cache_path_for as _databricks_table_cache_path
from haute._dataframe_execution_cache import (
    CacheArtifactCorruptError,
    CacheArtifactMissingError,
    CacheArtifactTooLargeError,
    DataFrameExecutionCache,
    DataFrameExecutionCacheEntry,
    DataFrameExecutionCacheError,
    DataFrameExecutionCacheKey,
    DataFrameExecutionCacheRequest,
    dataframe_execution_cache_key,
    dataframe_execution_cache_profile,
    dataframe_execution_policy_fingerprint,
    materialize_lazy_frame_with_cache,
)
from haute._execution_context import ExecutionContext, ExecutionProfile
from haute._graph_utils import upstream_node_ids
from haute._hashing import HASH_ALGO, content_hash, content_hash_bytes
from haute._json_flatten import cache_state_signature_for_graph
from haute._path_resolution import resolve_runtime_file_path
from haute._types import (
    MODEL_SCORE_CONFIG_KEYS,
    OPTIMISER_APPLY_CONFIG_KEYS,
    GraphEdge,
    GraphNode,
    NodeType,
    PipelineGraph,
    _Frame,
)
from haute.projection import (
    AllExceptColumns,
    ProjectionPlan,
    ProjectionRequest,
    compute_prepared_plan,
    ratebook_factor_required_columns,
    source_scan_projection,
    strict_projection_required,
)
from haute.projection import (
    plan as _plan_projection,
)

__all__ = [
    "AllExceptColumns",
    "CacheArtifactCorruptError",
    "CacheArtifactMissingError",
    "CacheArtifactTooLargeError",
    "DataFrameExecutionCache",
    "DataFrameExecutionCacheEntry",
    "DataFrameExecutionCacheError",
    "DataFrameExecutionCacheKey",
    "DataFrameExecutionCacheRequest",
    "dataframe_execution_policy_fingerprint",
    "dataframe_execution_cache_profile",
    "LazyExecutionResult",
    "ProjectionPlan",
    "ProjectionRequest",
    "build_linear_execution_chain_functions",
    "build_dataframe_execution_cache_request",
    "canonical_dataframe_execution_graph",
    "dataframe_frame_input_fingerprint",
    "dataframe_graph_input_fingerprint",
    "dataframe_paths_input_fingerprint",
    "default_dataframe_execution_cache",
    "dataframe_lazy_execution_policy",
    "dataframe_execution_cache_key",
    "execute_lazy_graph",
    "invalidate_dataframe_execution_cache",
    "materialize_lazy_frame_with_cache",
    "plan_prepared_execution_strategy",
    "plan_execution_strategy",
    "prune_source_switch_edges",
    "ratebook_factor_required_columns",
    "runtime_input_extra_keys",
    "source_scan_projection",
]

LazyExecutionResult = tuple[dict[str, _Frame], list[str], dict[str, list[str]], dict[str, str]]

_DEFAULT_DATAFRAME_EXECUTION_CACHE_ROOT: Path | None = None
_DEFAULT_DATAFRAME_EXECUTION_CACHE: DataFrameExecutionCache | None = None
_DEFAULT_DATAFRAME_EXECUTION_CACHE_LOCK = threading.Lock()

_GRAPH_PATH_CONFIG_BY_NODE_TYPE: dict[NodeType, str] = {
    NodeType.API_INPUT: "path",
    NodeType.DATA_SOURCE: "path",
    NodeType.EXTERNAL_FILE: "path",
    NodeType.DATA_SINK: "path",
}

_SOURCE_PATH_CONFIG_BY_NODE_TYPE: dict[NodeType, str] = {
    NodeType.API_INPUT: "path",
    NodeType.DATA_SOURCE: "path",
    NodeType.EXTERNAL_FILE: "path",
}

_LOCAL_RUNTIME_INPUT_PATH_FIELDS_BY_NODE_TYPE: dict[NodeType, tuple[str, ...]] = {
    NodeType.API_INPUT: ("path",),
    NodeType.DATA_SOURCE: ("path",),
    NodeType.EXTERNAL_FILE: ("path",),
    NodeType.MODEL_SCORE: (
        "artifact_path",
        "feature_contract_path",
    ),
}


def default_dataframe_execution_cache() -> DataFrameExecutionCache:
    """Return the process-local backend dataframe execution cache.

    Created lazily on first use so that pure-import callers (CI smoke tests,
    metadata scanners) do not leave a temp directory behind.  The root is
    cleaned up at interpreter exit.
    """
    global _DEFAULT_DATAFRAME_EXECUTION_CACHE, _DEFAULT_DATAFRAME_EXECUTION_CACHE_ROOT
    with _DEFAULT_DATAFRAME_EXECUTION_CACHE_LOCK:
        if _DEFAULT_DATAFRAME_EXECUTION_CACHE is None:
            root = Path(tempfile.mkdtemp(prefix="haute_dfexec_cache_"))
            cache = DataFrameExecutionCache(root=root)
            atexit.register(lambda: shutil.rmtree(root, ignore_errors=True))
            _DEFAULT_DATAFRAME_EXECUTION_CACHE_ROOT = root
            _DEFAULT_DATAFRAME_EXECUTION_CACHE = cache
        return _DEFAULT_DATAFRAME_EXECUTION_CACHE


def invalidate_dataframe_execution_cache() -> None:
    """Clear every materialized backend dataframe artifact owned by this process."""

    with _DEFAULT_DATAFRAME_EXECUTION_CACHE_LOCK:
        cache = _DEFAULT_DATAFRAME_EXECUTION_CACHE
    if cache is not None:
        cache.invalidate()


def _graph_runtime_path_config_key(node: GraphNode) -> str | None:
    config = node.data.config
    if node.data.nodeType == NodeType.OPTIMISER_APPLY and config.get("sourceType") == "file":
        return "artifact_path"
    return _GRAPH_PATH_CONFIG_BY_NODE_TYPE.get(node.data.nodeType)


def canonical_dataframe_execution_graph(graph: PipelineGraph) -> PipelineGraph:
    """Return the graph shape used for lazy execution and dataframe cache keys."""

    if not graph.source_file:
        return graph
    nodes: list[GraphNode] = []
    changed = False
    for node in graph.nodes:
        config = node.data.config
        key = _graph_runtime_path_config_key(node)
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
                continue
        nodes.append(node)
    if not changed:
        return graph
    return graph.model_copy(update={"nodes": nodes})


def plan_execution_strategy(
    request: ProjectionRequest,
    *,
    execution_context: ExecutionContext | None = None,
) -> ProjectionPlan:
    """Plan projection/streaming strategy through the execution facade.

    This is intentionally thin today: the projection planner remains the
    source of truth, while application layers get one stable execution-engine
    entry point that can grow as strategy selection broadens.
    """
    projection_plan = _plan_projection(request)
    if execution_context is not None:
        execution_context.projection_plan = projection_plan
    return projection_plan


def plan_prepared_execution_strategy(
    order: list[str],
    children_of: Mapping[str, list[str]],
    node_map: Mapping[str, GraphNode],
    *,
    profile: ExecutionProfile,
    required_columns_by_node: Mapping[str, Iterable[str] | AllExceptColumns] | None = None,
    execution_context: ExecutionContext | None = None,
) -> ProjectionPlan:
    """Plan projection/streaming strategy for an already prepared graph."""
    projection_plan = compute_prepared_plan(
        order,
        children_of,
        dict(node_map),
        required_columns_by_node=required_columns_by_node,
        strict_projection=strict_projection_required(profile, required_columns_by_node),
    )
    if execution_context is not None:
        execution_context.projection_plan = projection_plan
    return projection_plan


def _normalise_policy_column_demand(
    demand: Iterable[str] | AllExceptColumns | None,
) -> object:
    if demand is None:
        return None
    if isinstance(demand, AllExceptColumns):
        return {
            "kind": "all_except",
            "required_columns": sorted(demand.required_columns),
            "excluded_columns": sorted(demand.excluded_columns),
        }
    if isinstance(demand, str | bytes):
        raise TypeError("required column policy demand must be an iterable of names")
    columns: set[str] = set()
    for column in demand:
        if not isinstance(column, str) or not column:
            raise ValueError("required column policy demand must contain non-empty strings")
        columns.add(column)
    return sorted(columns)


def dataframe_lazy_execution_policy(
    *,
    target_node_id: str | None,
    source_by_node: Mapping[str, str] | None = None,
    required_columns_by_node: Mapping[str, Iterable[str] | AllExceptColumns] | None = None,
    preserve_node_ids: Iterable[str] | None = None,
    enforce_contracts: bool = False,
    preamble_ns_supplied: bool = False,
) -> Mapping[str, object]:
    """Return the non-graph policy payload used for dataframe execution keys."""

    normalised_sources: dict[str, str] = {}
    for node_id, source in (source_by_node or {}).items():
        if not isinstance(node_id, str) or not node_id:
            raise ValueError("source_by_node keys must be non-empty strings")
        if not isinstance(source, str) or not source:
            raise ValueError("source_by_node values must be non-empty strings")
        normalised_sources[node_id] = source

    normalised_required: dict[str, object] = {}
    for node_id, demand in (required_columns_by_node or {}).items():
        if not isinstance(node_id, str) or not node_id:
            raise ValueError("required_columns_by_node keys must be non-empty strings")
        normalised_required[node_id] = _normalise_policy_column_demand(demand)

    preserved: set[str] = set()
    for node_id in preserve_node_ids or ():
        if not isinstance(node_id, str) or not node_id:
            raise ValueError("preserve_node_ids must contain non-empty strings")
        preserved.add(node_id)

    return {
        "target_node_id": target_node_id,
        "source_by_node": dict(sorted(normalised_sources.items())),
        "required_columns_by_node": dict(sorted(normalised_required.items())),
        "preserve_node_ids": sorted(preserved),
        "enforce_contracts": bool(enforce_contracts),
        "preamble_ns_supplied": bool(preamble_ns_supplied),
    }


def _runtime_path_fingerprint(path: Path) -> Mapping[str, object]:
    resolved = path.resolve()
    if not resolved.exists():
        return {
            "path": str(resolved),
            "exists": False,
        }
    stat = resolved.stat()
    if not resolved.is_file():
        return {
            "path": str(resolved),
            "exists": True,
            "is_file": False,
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
    return {
        "path": str(resolved),
        "exists": True,
        "is_file": True,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "hash_algo": HASH_ALGO,
        "content_hash": content_hash(resolved),
    }


_RUNTIME_PATH_FINGERPRINT_MEMO_LOCK = threading.Lock()
# resolved path -> (mtime_ns, size, fingerprint payload).  One slot per
# path — replaced when the stat gate changes — so the memo stays bounded
# by the number of distinct file-backed inputs this process fingerprints.
_runtime_path_fingerprint_memo: dict[str, tuple[int, int, Mapping[str, object]]] = {}


def _stat_gated_runtime_path_fingerprint(path: Path) -> Mapping[str, object]:
    """Process-wide stat-gated memo over :func:`_runtime_path_fingerprint`.

    Preview/trace cache keys are recomputed on every request, so content-
    hashing every file-backed input per preview would scale request cost
    with data size instead of edit rate.  When ``(mtime_ns, size)`` is
    unchanged the memoised payload is reused; any metadata change re-hashes
    content, with the same double-stat race guard as
    ``haute._cache._utility_file_hash``.

    File metadata is not a complete correctness boundary: a rewrite that
    preserves both size and mtime while changing bytes is below the gate's
    resolution (the documented :class:`~haute._cache.GraphFingerprintMemo`
    trade).  Missing paths and directories are never memoised — their
    fingerprints are pure stat material already.  OS errors from stat or
    read propagate unchanged: an unreadable input must fail the request
    loudly rather than silently fingerprint as something it is not.
    """
    resolved = path.resolve()
    if not resolved.is_file():
        return _runtime_path_fingerprint(resolved)
    memo_key = str(resolved)
    for _ in range(2):
        stat = resolved.stat()
        stat_gate = (stat.st_mtime_ns, stat.st_size)
        with _RUNTIME_PATH_FINGERPRINT_MEMO_LOCK:
            memoised = _runtime_path_fingerprint_memo.get(memo_key)
        if memoised is not None and (memoised[0], memoised[1]) == stat_gate:
            return memoised[2]
        fingerprint = _runtime_path_fingerprint(resolved)
        after = resolved.stat()
        if (after.st_mtime_ns, after.st_size) == stat_gate:
            with _RUNTIME_PATH_FINGERPRINT_MEMO_LOCK:
                _runtime_path_fingerprint_memo[memo_key] = (*stat_gate, fingerprint)
            return fingerprint
    raise RuntimeError(f"Runtime input file changed while hashing: {resolved!s}")


def dataframe_paths_input_fingerprint(paths: Mapping[str, str]) -> Mapping[str, object]:
    """Return stable file-state fingerprints for named external path inputs."""

    payload: dict[str, object] = {}
    for key, raw_path in sorted(paths.items()):
        if not isinstance(key, str) or not key:
            raise ValueError("external path fingerprint keys must be non-empty strings")
        if not isinstance(raw_path, str) or not raw_path:
            raise ValueError("external path fingerprint values must be non-empty strings")
        path = Path(raw_path).resolve()
        payload[key] = _runtime_path_fingerprint(path)
    return payload


def dataframe_frame_input_fingerprint(input_df: Any) -> Mapping[str, object]:
    """Fingerprint an in-memory Polars input frame for dynamic lazy execution."""

    import polars as pl

    if not isinstance(input_df, pl.DataFrame):
        raise TypeError("input_df must be a polars DataFrame")
    schema = {name: str(dtype) for name, dtype in input_df.schema.items()}
    return {
        "height": input_df.height,
        "width": input_df.width,
        "schema": schema,
        "row_hash": content_hash_bytes(
            ",".join(str(value) for value in input_df.hash_rows(seed=0).to_list()).encode()
        ),
    }


def _runtime_path_from_graph_config(
    graph: PipelineGraph,
    raw_path: str,
) -> Path:
    return (
        resolve_runtime_file_path(
            raw_path,
            source_file=graph.source_file,
            prefer="project",
        )
        if graph.source_file
        else Path(raw_path)
    )


def _config_subset(config: Mapping[str, Any], keys: Iterable[str]) -> Mapping[str, object]:
    return {
        key: config[key]
        for key in sorted(keys)
        if key in config and isinstance(config[key], str | int | float | bool | type(None))
    }


def _runtime_input_config_fields(node_type: NodeType) -> tuple[str, ...]:
    if node_type in {NodeType.API_INPUT, NodeType.DATA_SOURCE}:
        return (
            "sourceType",
            "table",
            "catalog",
            "schema",
            "query",
            "http_path",
            "code",
        )
    if node_type == NodeType.EXTERNAL_FILE:
        return ("fileType", "modelClass", "code")
    if node_type == NodeType.MODEL_SCORE:
        return (*MODEL_SCORE_CONFIG_KEYS, "feature_contract_path")
    if node_type == NodeType.OPTIMISER_APPLY:
        return OPTIMISER_APPLY_CONFIG_KEYS
    return ()


def _runtime_input_path_fields(node: GraphNode) -> tuple[str, ...]:
    fields = list(_LOCAL_RUNTIME_INPUT_PATH_FIELDS_BY_NODE_TYPE.get(node.data.nodeType, ()))
    if (
        node.data.nodeType == NodeType.OPTIMISER_APPLY
        and node.data.config.get("sourceType") == "file"
    ):
        fields.append("artifact_path")
    return tuple(fields)


def _runtime_input_fingerprint_entry(
    graph: PipelineGraph,
    node: GraphNode,
) -> Mapping[str, object]:
    config = node.data.config
    payload: dict[str, object] = {
        "node_id": node.id,
        "node_type": node.data.nodeType.value,
        "config": _config_subset(config, _runtime_input_config_fields(node.data.nodeType)),
    }
    files = {
        path_field: _runtime_path_fingerprint(path)
        for path_field, path in _runtime_file_signature_paths(graph, node).items()
    }
    if files:
        payload["files"] = files
    return payload


def dataframe_graph_input_fingerprint(
    graph: PipelineGraph,
    *,
    target_node_id: str | None,
    source: str,
    extra_fingerprints: Mapping[str, object] | None = None,
    ignore_node_ids: Iterable[str] = (),
) -> str:
    """Fingerprint source-side inputs that sit outside the graph structure."""

    graph = canonical_dataframe_execution_graph(graph)
    if target_node_id is not None and target_node_id not in graph.node_map:
        raise ValueError(f"Cannot fingerprint inputs for unknown node {target_node_id!r}")
    ignored = set(ignore_node_ids)

    included_node_ids = (
        set(upstream_node_ids(target_node_id, graph.parents_of)) | {target_node_id}
        if target_node_id is not None
        else {node.id for node in graph.nodes}
    )
    runtime_input_node_types = set(_SOURCE_PATH_CONFIG_BY_NODE_TYPE) | {
        NodeType.MODEL_SCORE,
        NodeType.OPTIMISER_APPLY,
    }
    source_entries = [
        _runtime_input_fingerprint_entry(graph, node)
        for node in sorted(graph.nodes, key=lambda item: item.id)
        if node.id in included_node_ids
        and node.id not in ignored
        and node.data.nodeType in runtime_input_node_types
    ]
    payload = {
        "source": source,
        "sources": source_entries,
        "extra": dict(sorted((extra_fingerprints or {}).items())),
    }
    return content_hash_bytes(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())


def _runtime_file_signature_paths(graph: PipelineGraph, node: GraphNode) -> dict[str, Path]:
    """Resolved paths of every file *node* actually consumes at preview.

    The map mirrors each builder's runtime dispatch — sign exactly what
    gets read, nothing else:

    * **apiInput** - signs the configured raw path for both flat files
      and JSON/JSONL. JSON-shape inputs preview from the built per-frame
      parquet cache, but the raw file owns cache validity; signing it
      prevents serving a stale preview before execution can raise the
      stale-cache error.
    * **databricks dataSource** — preview consumes the LOCAL table-cache
      parquet (``read_cached_table``), which the GUI Fetch Data route
      rewrites in place; the derived cache path is signed (including
      absence, so the cached not-fetched error clears once a fetch
      lands).  Remote warehouse drift without a re-fetch is out of
      scope: the local parquet is the consumed input.
    * **everything else** — the per-node config path fields shared with
      the sink path (:func:`_runtime_input_path_fields`): flat-file
      dataSource / externalFile ``path``, ``modelScore``
      artifact/feature-contract paths, file-sourced ``optimiserApply``
      artifacts.
    """
    node_type = node.data.nodeType
    config = node.data.config
    if node_type == NodeType.API_INPUT:
        raw_path = config.get("path")
        if isinstance(raw_path, str) and raw_path:
            return {"path": _runtime_path_from_graph_config(graph, raw_path)}
        return {}
    if node_type == NodeType.DATA_SOURCE and config.get("sourceType") == "databricks":
        table = config.get("table")
        if isinstance(table, str) and table:
            return {"table_cache": _databricks_table_cache_path(table)}
        return {}
    paths: dict[str, Path] = {}
    for path_field in _runtime_input_path_fields(node):
        raw = config.get(path_field)
        if isinstance(raw, str) and raw:
            paths[path_field] = _runtime_path_from_graph_config(graph, raw)
    return paths


def _runtime_file_inputs_signature(graph: PipelineGraph) -> str:
    """Digest of every file-backed runtime input's state in *graph*.

    One entry per node with file inputs, enumerated by
    :func:`_runtime_file_signature_paths` (which mirrors the builders'
    runtime dispatch per node type).  Returns ``""`` when the graph has
    no file-backed inputs.

    A missing file is signed as ``exists: False`` — the changed key
    forces re-execution, which then surfaces the missing file through
    the node's normal execution error instead of a stale cached frame.
    """
    entries: list[Mapping[str, object]] = []
    for node in sorted(graph.nodes, key=lambda item: item.id):
        signature_paths = _runtime_file_signature_paths(graph, node)
        if not signature_paths:
            continue
        entries.append(
            {
                "node_id": node.id,
                "files": {
                    field: _stat_gated_runtime_path_fingerprint(path)
                    for field, path in signature_paths.items()
                },
            }
        )
    if not entries:
        return ""
    return "runtime_files=" + content_hash_bytes(canonical_json(entries).encode())


def runtime_input_extra_keys(graph: PipelineGraph) -> tuple[str, ...]:
    """Graph-fingerprint extra keys for runtime inputs outside the graph JSON.

    The single source of truth for the runtime-input key material shared
    by the preview cache (``executor.py``) and the trace cache
    (``trace.py``); both pass the result straight into
    :func:`haute._cache.graph_fingerprint` as extra keys.  Two
    components, each omitted when empty so graphs without that input
    class keep byte-identical keys:

    * ``runtime_files=…`` — file-backed input state
      (:func:`_runtime_file_inputs_signature`), so an out-of-band
      re-export of a dataSource or flat-file apiInput file, an external
      file, a model artifact, or a databricks table-cache refetch
      invalidates affected entries;
    * ``json_cache=…`` — the JSON-shape apiInput cache state
      (:func:`haute._json_flatten.cache_state_signature_for_graph`), so
      a cache build/clear/mirror invalidates affected entries.

    File access is stat-gated via
    :func:`_stat_gated_runtime_path_fingerprint`: unchanged inputs cost
    one ``stat`` per file per call, never a content re-hash.
    """
    keys: list[str] = []
    file_signature = _runtime_file_inputs_signature(graph)
    if file_signature:
        keys.append(file_signature)
    json_cache_signature = cache_state_signature_for_graph(graph)
    if json_cache_signature:
        keys.append(json_cache_signature)
    return tuple(keys)


def build_dataframe_execution_cache_request(
    graph: PipelineGraph,
    *,
    node_ids: Iterable[str],
    namespace: str,
    source: str,
    profile: ExecutionProfile | str,
    input_fingerprint: str,
    target_node_id: str | None,
    source_by_node: Mapping[str, str] | None = None,
    required_columns_by_node: Mapping[str, Iterable[str] | AllExceptColumns] | None = None,
    preserve_node_ids: Iterable[str] | None = None,
    enforce_contracts: bool = False,
    preamble_ns_supplied: bool = False,
    cache: DataFrameExecutionCache | None = None,
    streaming_chunk_size: int | None = None,
    fast_checkpoint: bool = True,
) -> DataFrameExecutionCacheRequest:
    """Build a validated cache request for one lazy execution run."""

    graph = canonical_dataframe_execution_graph(graph)
    node_id_list = list(node_ids)
    if not node_id_list:
        raise ValueError("node_ids must contain at least one node ID")
    policy = dataframe_lazy_execution_policy(
        target_node_id=target_node_id,
        source_by_node=source_by_node,
        required_columns_by_node=required_columns_by_node,
        preserve_node_ids=preserve_node_ids,
        enforce_contracts=enforce_contracts,
        preamble_ns_supplied=preamble_ns_supplied,
    )
    memo = GraphFingerprintMemo()
    keys_by_node: dict[str, DataFrameExecutionCacheKey] = {}
    for node_id in node_id_list:
        demand = (required_columns_by_node or {}).get(node_id)
        required_columns = None if isinstance(demand, AllExceptColumns) else demand
        keys_by_node[node_id] = dataframe_execution_cache_key(
            graph,
            node_id=node_id,
            namespace=namespace,
            source=source,
            profile=profile,
            input_fingerprint=input_fingerprint,
            required_columns=required_columns,
            execution_policy=policy,
            memo=memo,
        )
    return DataFrameExecutionCacheRequest(
        cache=cache if cache is not None else default_dataframe_execution_cache(),
        keys_by_node=keys_by_node,
        streaming_chunk_size=streaming_chunk_size,
        fast_checkpoint=fast_checkpoint,
    )


def execute_lazy_graph(
    graph: PipelineGraph,
    build_node_fn: Callable[..., Any],
    *,
    target_node_id: str | None = None,
    preamble_ns: dict[str, Any] | None = None,
    source: str = "live",
    checkpoint_dir: Path | None = None,
    enforce_contracts: bool = False,
    preserve_node_ids: set[str] | frozenset[str] | None = None,
    required_columns_by_node: Mapping[str, Iterable[str] | AllExceptColumns] | None = None,
    execution_context: ExecutionContext | None = None,
    source_by_node: Mapping[str, str] | None = None,
    dataframe_cache_request: DataFrameExecutionCacheRequest | None = None,
) -> LazyExecutionResult:
    """Execute a graph lazily through the shared production engine."""
    from haute._execute_lazy import _execute_lazy

    return _execute_lazy(
        graph,
        build_node_fn,
        target_node_id=target_node_id,
        preamble_ns=preamble_ns,
        source=source,
        checkpoint_dir=checkpoint_dir,
        enforce_contracts=enforce_contracts,
        preserve_node_ids=preserve_node_ids,
        required_columns_by_node=required_columns_by_node,
        execution_context=execution_context,
        source_by_node=source_by_node,
        dataframe_cache_request=dataframe_cache_request,
    )


def prune_source_switch_edges(
    edges: list[GraphEdge],
    node_map: dict[str, GraphNode],
    source: str,
) -> list[GraphEdge]:
    """Return graph edges pruned to the active source-switch branch."""
    from haute._execute_lazy import _prune_live_switch_edges

    return _prune_live_switch_edges(edges, node_map, source)


def build_linear_execution_chain_functions(
    graph: PipelineGraph,
    build_node_fn: Callable[..., Any],
    *,
    target_node_id: str,
    base_node_id: str,
    chain_node_ids: Iterable[str],
    preamble_ns: dict[str, Any] | None = None,
    routing_source: str = "batch",
    build_source: str = "live",
    required_output_columns_by_node: Mapping[str, frozenset[str] | set[str] | None] | None = None,
    reuse_model_score_functions: bool = False,
    execution_profile: ExecutionProfile | None = None,
) -> dict[str, tuple[Callable[..., Any], bool]]:
    """Build executable functions for a single-parent chain.

    Chunked auto-range executes the expensive scenario-expanded chain one
    chunk at a time.  This helper makes that shape explicit at the execution
    boundary: graph routing is prepared once, then the requested chain is
    rewired so its first node consumes the already-produced base chunk.
    """
    chain_ids = list(chain_node_ids)
    if not chain_ids:
        return {}

    from haute._execute_lazy import _build_funcs, _prepare_graph_with_edges

    node_map, _order, _parents_of, id_to_name, relevant_edges = _prepare_graph_with_edges(
        graph,
        target_node_id,
        source=routing_source,
    )
    missing = [node_id for node_id in (base_node_id, *chain_ids) if node_id not in node_map]
    if missing:
        raise ValueError(
            "Linear execution chain references node IDs that are not in the prepared graph: "
            f"{missing}"
        )

    chain_parents: dict[str, list[str]] = {}
    parent_id = base_node_id
    for chain_id in chain_ids:
        chain_parents[chain_id] = [parent_id]
        parent_id = chain_id

    incoming_edges_by_target: dict[str, list[GraphEdge]] = {}
    for edge in relevant_edges:
        incoming_edges_by_target.setdefault(edge.target, []).append(edge)
    all_incoming_edges_by_target: dict[str, list[GraphEdge]] = {}
    for edge in graph.edges:
        all_incoming_edges_by_target.setdefault(edge.target, []).append(edge)

    reuse_loaded_model_by_node = (
        {
            chain_id: True
            for chain_id in chain_ids
            if node_map[chain_id].data.nodeType == NodeType.MODEL_SCORE
        }
        if reuse_model_score_functions
        else None
    )
    return _build_funcs(
        chain_ids,
        node_map,
        chain_parents,
        id_to_name,
        graph.parents_of,
        build_node_fn,
        incoming_edges_by_target=incoming_edges_by_target,
        all_incoming_edges_by_target=all_incoming_edges_by_target,
        all_node_map=graph.node_map,
        preamble_ns=preamble_ns,
        source=build_source,
        required_output_columns_by_node=required_output_columns_by_node,
        reuse_loaded_model_by_node=reuse_loaded_model_by_node,
        execution_profile=execution_profile,
    )
