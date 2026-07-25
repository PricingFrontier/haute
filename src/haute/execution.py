"""Internal execution-engine facade.

Application layers should import execution helpers from this module instead
of reaching into ``haute._execute_lazy`` or underscore re-exports from
``haute.graph_utils``.  The implementation remains deliberately thin: it
keeps one stable internal boundary while the engine underneath continues to
evolve.
"""

from __future__ import annotations

import atexit
import shutil
import tempfile
import threading
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from haute._cache import (
    GraphFingerprintMemo,
    LineageCacheKeyRequest,
    canonical_json,
    lineage_cache_key,
    preamble_execution_fingerprint,
    selected_live_switch_path,
)
from haute._cache import (
    _pipeline_dir as _cache_pipeline_dir,
)
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
from haute._path_resolution import _infer_project_root, resolve_runtime_file_path
from haute._ram_estimate import (
    MaterialisationEstimate,
    MaterialisationEstimateState,
    estimate_materialisation_boundary,
)
from haute._types import (
    MODEL_SCORE_CONFIG_KEYS,
    OPTIMISER_APPLY_CONFIG_KEYS,
    GraphEdge,
    GraphNode,
    NodeType,
    PipelineGraph,
    _Frame,
)
from haute.errors import GroupByExecutionUnsupportedError
from haute.projection import (
    AllExceptColumns,
    BoundedDiagnosticCollection,
    DiagnosticDetailState,
    ExecutionBoundedness,
    ExecutionStrategy,
    ExecutionStrategyDiagnostic,
    ExecutionStrategyResult,
    ExecutionStrategyStatus,
    PreparedGraph,
    ProjectionPlan,
    ProjectionRequest,
    build_execution_strategy_result,
    compute_prepared_plan,
    group_by_operators_by_node,
    normalise_required_columns_by_node,
    prepare_graph,
    ratebook_factor_required_columns,
    source_scan_projection,
    strict_projection_required,
    with_materialisation_boundaries,
)

__all__ = [
    "AllExceptColumns",
    "BoundedDiagnosticCollection",
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
    "DiagnosticDetailState",
    "ExecutionBoundedness",
    "ExecutionStrategy",
    "ExecutionStrategyDiagnostic",
    "ExecutionStrategyResult",
    "ExecutionStrategyStatus",
    "MaterialisationEstimate",
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
    "preview_lineage_cache_key",
    "prune_source_switch_edges",
    "ratebook_factor_required_columns",
    "runtime_input_extra_keys",
    "source_scan_projection",
]

PREVIEW_EXECUTION_SEMANTICS_VERSION = "preview-materialisation:v1"
_PREVIEW_CONTRACT_FINGERPRINT_VERSION = 1

LazyExecutionResult = tuple[dict[str, _Frame], list[str], dict[str, list[str]], dict[str, str]]

_DEFAULT_DATAFRAME_EXECUTION_CACHE_ROOT: Path | None = None
_DEFAULT_DATAFRAME_EXECUTION_CACHE: DataFrameExecutionCache | None = None
_DEFAULT_DATAFRAME_EXECUTION_CACHE_LOCK = threading.Lock()
_AUTO_MATERIALISATION_ESTIMATE = object()

_SOURCE_PATH_CONFIG_BY_NODE_TYPE: dict[NodeType, str] = {
    NodeType.API_INPUT: "path",
    NodeType.DATA_INPUT: "path",
    NodeType.EXTERNAL_FILE: "path",
}

_LOCAL_RUNTIME_INPUT_PATH_FIELDS_BY_NODE_TYPE: dict[NodeType, tuple[str, ...]] = {
    NodeType.API_INPUT: ("path",),
    NodeType.EXTERNAL_FILE: ("path",),
    # ``artifact_path`` is an MLflow artifact identifier (for example
    # ``model/model.cbm``), not a path on the Haute project filesystem.
    NodeType.MODEL_SCORE: ("feature_contract_path",),
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


def canonical_dataframe_execution_graph(graph: PipelineGraph) -> PipelineGraph:
    """Resolve and contain every local runtime input before execution."""

    root = _infer_project_root(project_root=None, source_file=graph.source_file)
    pipeline_dir = _cache_pipeline_dir(graph)
    if pipeline_dir is None:
        from haute._project import _toml_configured_pipeline

        configured_pipeline = _toml_configured_pipeline(root)
        pipeline_dir = configured_pipeline.parent if configured_pipeline is not None else None
    nodes: list[GraphNode] = []
    changed = False
    for node in graph.nodes:
        config = node.data.config
        resolved_config = dict(config)
        node_changed = False
        for key in _local_runtime_input_path_fields(node):
            raw_path = config.get(key)
            if isinstance(raw_path, str) and raw_path:
                resolved = str(
                    resolve_runtime_file_path(
                        raw_path,
                        source_file=graph.source_file,
                        pipeline_dir=pipeline_dir,
                        project_root=root,
                        prefer="project",
                        enforce_project_root=True,
                    )
                )
                if resolved != raw_path:
                    resolved_config[key] = resolved
                    node_changed = True
        if node_changed:
            data = node.data.model_copy(update={"config": resolved_config})
            nodes.append(node.model_copy(update={"data": data}))
            changed = True
        else:
            nodes.append(node)
    if not changed:
        return graph
    return graph.model_copy(update={"nodes": nodes})


def plan_execution_strategy(
    request: ProjectionRequest,
    *,
    execution_context: ExecutionContext | None = None,
    materialisation_estimate: MaterialisationEstimate | None | object = (
        _AUTO_MATERIALISATION_ESTIMATE
    ),
) -> ExecutionStrategyResult:
    """Return the sole route-facing V1 execution-planning result."""
    prepared = prepare_graph(
        request.graph,
        request.target_node_id,
        source=request.source,
    )
    children_of = _children_of(prepared.order, prepared.parents_of)
    required_columns_by_node = normalise_required_columns_by_node(
        request.required_columns_by_node,
        prepared.order,
    )
    projection_plan = compute_prepared_plan(
        prepared.order,
        children_of,
        prepared.node_map,
        required_columns_by_node=required_columns_by_node,
        strict_projection=strict_projection_required(
            request.profile,
            required_columns_by_node,
        ),
    )
    group_by_operators = group_by_operators_by_node(prepared.order, prepared.node_map)
    resolved_estimate: MaterialisationEstimate | None
    if group_by_operators and request.profile in {
        ExecutionProfile.PREVIEW_EAGER,
        ExecutionProfile.DEPLOY_LIVE,
    }:
        if materialisation_estimate is _AUTO_MATERIALISATION_ESTIMATE:
            resolved_estimate = _estimate_group_by_boundaries(
                request.graph,
                group_by_operators,
                source=request.source,
            )
        elif materialisation_estimate is None:
            resolved_estimate = MaterialisationEstimate.unavailable(
                "materialisation_estimate_not_supplied"
            )
        elif isinstance(materialisation_estimate, MaterialisationEstimate):
            resolved_estimate = materialisation_estimate
        else:
            raise TypeError("materialisation_estimate must be a MaterialisationEstimate or None")
    else:
        resolved_estimate = None
    result = _finalise_execution_strategy(
        projection_plan,
        profile=request.profile,
        order=prepared.order,
        children_of=children_of,
        node_map=prepared.node_map,
        has_projection_seed=bool(required_columns_by_node),
        group_by_operators=group_by_operators,
        execution_context=execution_context,
        materialisation_estimate=resolved_estimate,
        required_columns_by_node=required_columns_by_node,
    )
    if execution_context is not None:
        execution_context.projection_plan = result
    return result


def _estimate_group_by_boundaries(
    graph: PipelineGraph,
    node_ids: Iterable[str],
    *,
    source: str,
) -> MaterialisationEstimate:
    """Return the conservative peak across every declared group-by boundary."""
    peak_bytes = 0
    assumptions: list[str] = []
    for node_id in node_ids:
        estimate = estimate_materialisation_boundary(graph, node_id, source=source)
        if estimate.state is MaterialisationEstimateState.UNAVAILABLE:
            reason = estimate.unavailable_reason or "unknown"
            return MaterialisationEstimate.unavailable(f"{node_id}:{reason}")
        assert estimate.estimated_peak_bytes is not None
        peak_bytes = max(peak_bytes, estimate.estimated_peak_bytes)
        assumptions.extend(f"{node_id}: {item}" for item in estimate.assumptions)
    return MaterialisationEstimate.available(peak_bytes, assumptions=assumptions)


def plan_prepared_execution_strategy(
    order: list[str],
    children_of: Mapping[str, list[str]],
    node_map: Mapping[str, GraphNode],
    *,
    profile: ExecutionProfile,
    required_columns_by_node: Mapping[str, Iterable[str] | AllExceptColumns] | None = None,
    execution_context: ExecutionContext | None = None,
    materialisation_estimate: MaterialisationEstimate | None = None,
) -> ExecutionStrategyResult:
    """Plan projection/streaming strategy for an already prepared graph."""
    required_columns_by_node = normalise_required_columns_by_node(
        required_columns_by_node,
        order,
    )
    projection_plan = compute_prepared_plan(
        order,
        children_of,
        dict(node_map),
        required_columns_by_node=required_columns_by_node,
        strict_projection=strict_projection_required(profile, required_columns_by_node),
    )
    group_by_operators = group_by_operators_by_node(order, node_map)
    result = _finalise_execution_strategy(
        projection_plan,
        profile=profile,
        order=order,
        children_of=children_of,
        node_map=node_map,
        has_projection_seed=bool(required_columns_by_node),
        group_by_operators=group_by_operators,
        execution_context=execution_context,
        materialisation_estimate=materialisation_estimate,
        required_columns_by_node=required_columns_by_node,
    )
    if execution_context is not None:
        execution_context.projection_plan = result
    return result


def _children_of(
    order: Iterable[str],
    parents_of: Mapping[str, Iterable[str]],
) -> dict[str, list[str]]:
    children: dict[str, list[str]] = {node_id: [] for node_id in order}
    for child_id, parent_ids in parents_of.items():
        for parent_id in parent_ids:
            if parent_id in children:
                children[parent_id].append(child_id)
    return children


def _group_by_rejection(
    *,
    node_id: str,
    operator: str,
    profile: ExecutionProfile,
    reason_code: str,
    estimated_peak_bytes: int | None,
    headroom_bytes: int | None,
) -> GroupByExecutionUnsupportedError:
    remediation = {
        "profile_requires_bounded_execution": (
            "Remove the group-by, pre-aggregate the source, or run it through an "
            "admitted preview/deploy-live materialisation boundary."
        ),
        "execution_admission_unavailable": (
            "Create an admitted execution context with positive memory-limit and "
            "headroom values before running this group-by."
        ),
        "materialisation_estimate_unavailable": (
            "Provide readable source/schema metadata so Haute can estimate the full "
            "group-by boundary before execution."
        ),
        "materialisation_exceeds_headroom": (
            "Increase the configured memory headroom, narrow the input, or pre-aggregate "
            "the source before this group-by."
        ),
    }[reason_code]
    return GroupByExecutionUnsupportedError(
        "Group-by execution is unsupported for the selected execution strategy.",
        node_id=node_id,
        operator=operator,
        profile=profile.value,
        reason_code=reason_code,
        remediation=remediation,
        estimated_peak_bytes=estimated_peak_bytes,
        headroom_bytes=headroom_bytes,
    )


def _finalise_execution_strategy(
    projection_plan: ProjectionPlan,
    *,
    profile: ExecutionProfile,
    order: Iterable[str],
    children_of: Mapping[str, Iterable[str]],
    node_map: Mapping[str, GraphNode],
    has_projection_seed: bool,
    group_by_operators: Mapping[str, str],
    execution_context: ExecutionContext | None,
    materialisation_estimate: MaterialisationEstimate | None,
    required_columns_by_node: Mapping[str, Iterable[str] | AllExceptColumns] | None,
) -> ExecutionStrategyResult:
    strategy: ExecutionStrategy | None = None
    reason_code: str | None = None
    remediation: str | None = None
    estimated_peak_bytes: int | None = None
    headroom_bytes: int | None = None
    assumptions: tuple[str, ...] = ()

    if group_by_operators:
        node_id, operator = next(iter(group_by_operators.items()))
        if profile not in {ExecutionProfile.PREVIEW_EAGER, ExecutionProfile.DEPLOY_LIVE}:
            raise _group_by_rejection(
                node_id=node_id,
                operator=operator,
                profile=profile,
                reason_code="profile_requires_bounded_execution",
                estimated_peak_bytes=None,
                headroom_bytes=None,
            )

        admission = execution_context.admission if execution_context is not None else None
        if (
            admission is None
            or not admission.admitted
            or not isinstance(admission.memory_limit_bytes, int)
            or isinstance(admission.memory_limit_bytes, bool)
            or admission.memory_limit_bytes <= 0
            or not isinstance(admission.headroom_bytes, int)
            or isinstance(admission.headroom_bytes, bool)
            or admission.headroom_bytes <= 0
        ):
            raise _group_by_rejection(
                node_id=node_id,
                operator=operator,
                profile=profile,
                reason_code="execution_admission_unavailable",
                estimated_peak_bytes=None,
                headroom_bytes=None,
            )
        headroom_bytes = min(admission.memory_limit_bytes, admission.headroom_bytes)
        if (
            materialisation_estimate is None
            or materialisation_estimate.state is MaterialisationEstimateState.UNAVAILABLE
        ):
            raise _group_by_rejection(
                node_id=node_id,
                operator=operator,
                profile=profile,
                reason_code="materialisation_estimate_unavailable",
                estimated_peak_bytes=None,
                headroom_bytes=headroom_bytes,
            )
        estimated_peak_bytes = materialisation_estimate.estimated_peak_bytes
        assert estimated_peak_bytes is not None
        if estimated_peak_bytes > headroom_bytes:
            raise _group_by_rejection(
                node_id=node_id,
                operator=operator,
                profile=profile,
                reason_code="materialisation_exceeds_headroom",
                estimated_peak_bytes=estimated_peak_bytes,
                headroom_bytes=headroom_bytes,
            )
        projection_plan = with_materialisation_boundaries(
            projection_plan,
            group_by_operators,
        )
        strategy = ExecutionStrategy.MATERIALISATION_BOUNDARY
        reason_code = "group_by_materialisation_admitted"
        remediation = "Keep the admitted boundary within its reported memory headroom."
        assumptions = materialisation_estimate.assumptions

    return build_execution_strategy_result(
        projection_plan,
        profile=profile,
        order=order,
        children_of=children_of,
        node_map=node_map,
        has_projection_seed=has_projection_seed,
        required_columns_by_node=required_columns_by_node,
        strategy=strategy,
        reason_code=reason_code,
        boundary_operators=group_by_operators,
        remediation=remediation,
        estimated_peak_bytes=estimated_peak_bytes,
        headroom_bytes=headroom_bytes,
        assumptions=assumptions,
    )


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
        payload[key] = _stat_gated_runtime_path_fingerprint(path)
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
    if node_type == NodeType.API_INPUT:
        return (
            "sourceType",
            "table",
            "catalog",
            "schema",
            "query",
            "http_path",
            "code",
        )
    if node_type == NodeType.DATA_INPUT:
        return (
            "inputType",
            "format",
            "cacheMode",
            "mode",
            "path",
            "connection",
            "uri",
            "query",
            "http_path",
            "table",
            "code",
        )
    if node_type == NodeType.EXTERNAL_FILE:
        return ("fileType", "modelClass", "code")
    if node_type == NodeType.MODEL_SCORE:
        return (*MODEL_SCORE_CONFIG_KEYS, "feature_contract_path")
    if node_type == NodeType.OPTIMISER_APPLY:
        return OPTIMISER_APPLY_CONFIG_KEYS
    return ()


def _local_runtime_input_path_fields(node: GraphNode) -> tuple[str, ...]:
    """Return config fields that cause reads from the local filesystem."""
    fields = list(_LOCAL_RUNTIME_INPUT_PATH_FIELDS_BY_NODE_TYPE.get(node.data.nodeType, ()))
    if node.data.nodeType == NodeType.DATA_INPUT and node.data.config.get("inputType") in {
        "file",
        "lakehouse",
    }:
        fields.append("path")
    if (
        node.data.nodeType == NodeType.OPTIMISER_APPLY
        and node.data.config.get("sourceType") == "file"
    ):
        fields.append("artifact_path")
    return tuple(fields)


def _runtime_input_path_fields(node: GraphNode) -> tuple[str, ...]:
    """Return path-shaped inputs that require request-boundary validation.

    This includes the MLflow ``modelScore.artifact_path`` identifier so route
    validation continues to reject traversal-shaped values, even though that
    identifier must never be resolved as a local project file by execution.
    """
    fields = list(_local_runtime_input_path_fields(node))
    if node.data.nodeType == NodeType.MODEL_SCORE:
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
        path_field: _stat_gated_runtime_path_fingerprint(path)
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
    return content_hash_bytes(canonical_json(payload).encode())


def _runtime_file_signature_paths(graph: PipelineGraph, node: GraphNode) -> dict[str, Path]:
    """Resolved paths of every file *node* actually consumes at preview.

    The map mirrors each builder's runtime dispatch — sign exactly what
    gets read, nothing else:

    * **apiInput** - signs the configured raw path for both flat files
      and JSON/JSONL. JSON-shape inputs prefer a valid per-frame parquet
      cache and otherwise shred that raw file directly; signing it prevents
      a stale preview from hiding either fresh direct data or a raw-file error.
    * **dataInput** — direct local file/lakehouse inputs sign the configured
      source path; snapshot inputs sign the active generation pointer, so only
      an explicit refresh invalidates execution caches.
    * **everything else** — the per-node config path fields shared with
      :func:`_local_runtime_input_path_fields`: ``externalFile`` paths,
      ``modelScore`` feature-contract paths, and file-sourced
      ``optimiserApply`` artifacts. MLflow artifact identifiers are config
      identity, not local files.
    """
    node_type = node.data.nodeType
    config = node.data.config
    if node_type == NodeType.API_INPUT:
        raw_path = config.get("path")
        if isinstance(raw_path, str) and raw_path:
            return {"path": _runtime_path_from_graph_config(graph, raw_path)}
        return {}
    if node_type == NodeType.DATA_INPUT:
        input_type = config.get("inputType")
        if config.get("cacheMode") == "snapshot":
            try:
                from haute._input_providers import source_cache_identity
                from haute._sandbox import _get_project_root
                from haute._source_cache import SourceCacheStore

                base_dir = (
                    Path(graph.source_file).resolve().parent
                    if graph.source_file
                    else _get_project_root()
                )
                identity = source_cache_identity(config, base_dir=base_dir)
                pointer = (
                    SourceCacheStore(_get_project_root()).identity_path(identity) / "current.json"
                )
                return {"snapshot_pointer": pointer}
            except (TypeError, ValueError):
                return {}
        if input_type in {"file", "lakehouse"}:
            raw_path = config.get("path")
            if isinstance(raw_path, str) and raw_path:
                return {"path": _runtime_path_from_graph_config(graph, raw_path)}
        return {}
    paths: dict[str, Path] = {}
    for path_field in _local_runtime_input_path_fields(node):
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
      change to a direct Data Input, active input snapshot, flat-file
      apiInput, external file, or model artifact invalidates affected entries;
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


def _lineage_runtime_graph(graph: PipelineGraph, prepared: PreparedGraph) -> PipelineGraph:
    """Return the source-pruned target lineage used for runtime-input hashing."""
    relevant_ids = set(prepared.order)
    return graph.model_copy(
        update={
            "nodes": [node for node in graph.nodes if node.id in relevant_ids],
            "edges": list(prepared.relevant_edges),
        }
    )


def _preview_contract_fingerprint(
    *,
    enforce_contracts: bool,
    materialisation_scope: str,
) -> str:
    if type(enforce_contracts) is not bool:
        raise TypeError("enforce_contracts must be a bool")
    if materialisation_scope not in {"full", "target_only"}:
        raise ValueError("materialisation_scope must be 'full' or 'target_only'")
    payload = {
        "schema_version": _PREVIEW_CONTRACT_FINGERPRINT_VERSION,
        "enforce_contracts": enforce_contracts,
        "materialisation_scope": materialisation_scope,
    }
    digest = content_hash_bytes(canonical_json(payload).encode())
    return f"preview-contract-v{_PREVIEW_CONTRACT_FINGERPRINT_VERSION}:{digest}"


def _lineage_runtime_input_fingerprint(
    graph: PipelineGraph,
    prepared: PreparedGraph,
    *,
    memo: GraphFingerprintMemo | None,
) -> str:
    relevant_graph = _lineage_runtime_graph(graph, prepared)
    payload = {
        "runtime_input_keys": list(runtime_input_extra_keys(relevant_graph)),
        "preamble_execution": preamble_execution_fingerprint(
            relevant_graph.preamble,
            pipeline_dir=_cache_pipeline_dir(relevant_graph),
            memo=memo,
        ),
    }
    return "runtime-input-v1:" + content_hash_bytes(canonical_json(payload).encode())


def preview_lineage_cache_key(
    graph: PipelineGraph,
    *,
    target_node_id: str | None,
    source: str,
    requested_columns: Iterable[str] | None,
    initial_column_limit: int | None,
    row_limit: int | None,
    port_label: str | None,
    enforce_contracts: bool,
    materialisation_scope: str,
    memo: GraphFingerprintMemo | None = None,
) -> str:
    """Return the sole preview/trace cache identity for one target lineage."""
    prepared = prepare_graph(graph, target_node_id, source=source)
    request = LineageCacheKeyRequest(
        graph=graph,
        prepared=prepared,
        target_node_id=target_node_id,
        source=source,
        requested_columns=None if requested_columns is None else tuple(requested_columns),
        initial_column_limit=initial_column_limit,
        row_limit=row_limit,
        port_label=port_label,
        contract_fingerprint=_preview_contract_fingerprint(
            enforce_contracts=enforce_contracts,
            materialisation_scope=materialisation_scope,
        ),
        selected_live_switch_path=selected_live_switch_path(prepared),
        runtime_input_fingerprint=_lineage_runtime_input_fingerprint(
            graph,
            prepared,
            memo=memo,
        ),
        execution_semantics_version=PREVIEW_EXECUTION_SEMANTICS_VERSION,
    )
    return lineage_cache_key(request)


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
