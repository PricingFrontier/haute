"""Input/output schema inference for deployed pipelines."""

from __future__ import annotations

import json as _json
from pathlib import Path

import polars as pl

from haute._cache import graph_fingerprint
from haute._logging import get_logger
from haute.errors import ConfigError
from haute.graph_utils import GraphNode, NodeType, PipelineGraph, read_data_source, read_source

logger = get_logger(component="deploy.schema")

_SCHEMA_CACHE_FILE = ".haute_cache/output_schema.json"


def _resolve_pipeline_path(graph: PipelineGraph, path: str) -> str:
    """Resolve a graph config path against the pipeline file directory."""
    raw = Path(path)
    if raw.is_absolute() or not graph.source_file:
        return str(raw)
    return str((Path(graph.source_file).parent / raw).resolve())


def _read_input_source(graph: PipelineGraph, node: GraphNode, resolved_path: str) -> pl.LazyFrame:
    config = node.data.config
    if node.data.nodeType == NodeType.DATA_INPUT:
        from haute._input_providers import resolve_data_input
        from haute._sandbox import _get_project_root
        from haute._source_cache import SourceCacheStore

        base_dir = Path(graph.source_file).parent if graph.source_file else None
        return resolve_data_input(
            {**config, **({"path": resolved_path} if resolved_path else {})},
            store=SourceCacheStore(_get_project_root()),
            base_dir=base_dir,
        )
    if node.data.nodeType == NodeType.API_INPUT and not resolved_path.lower().endswith(
        (".json", ".jsonl")
    ):
        return read_data_source({**config, "path": resolved_path})
    return read_source(resolved_path)


def infer_input_schema(graph: PipelineGraph, input_node_id: str) -> dict[str, str]:
    """Infer the input schema by reading the input source node's data file.

    Reads the first 0 rows to get column names + types without loading data.

    Args:
        graph: Pruned graph with nodes and edges.
        input_node_id: The apiInput source node.

    Returns:
        Dict of column_name → polars dtype string (e.g. ``{"Area": "String"}``).

    Raises:
        ValueError: If the input node has no path or the file can't be read.
    """
    node = _find_node(graph, input_node_id)
    config = node.data.config
    path = config.get("path", "")

    if not path and node.data.nodeType != NodeType.DATA_INPUT:
        raise ValueError(
            f"Input node '{input_node_id}' has no path configured. Cannot infer schema."
        )

    try:
        resolved_path = _resolve_pipeline_path(graph, path) if path else ""
        lf = _read_input_source(graph, node, resolved_path)
        schema = lf.collect_schema()
    except Exception as exc:
        raise ValueError(
            f"Failed to read schema from '{path}' for input node '{input_node_id}': {exc}"
        ) from exc

    return {col: str(dtype) for col, dtype in schema.items()}


def infer_output_schema(
    graph: PipelineGraph,
    output_node_id: str,
    input_node_ids: list[str],
    artifact_paths: dict[str, str] | None = None,
) -> dict[str, str]:
    """Infer the output schema by dry-running one sample row.

    Executes the pruned graph with a single sample row injected at each
    input node, and reads the output node's columns + types.

    Results are cached in ``.haute_cache/output_schema.json`` keyed by
    graph fingerprint so unchanged pipelines skip the dry-run.  The bundled
    artifact identity (:func:`artifact_identity_fingerprint`) is mixed into
    the key so a model retrained in place — same graph config but different
    served bytes — busts the cache instead of baking a stale schema into the
    deploy manifest / MLflow ``ModelSignature``.

    The dry-run scores with the exact ``artifact_paths`` the deployed
    container serves, so the inferred schema reflects the pinned model rather
    than whatever a live MLflow lookup would resolve.

    Args:
        graph: Pruned graph.
        output_node_id: The output node to read results from.
        input_node_ids: Source nodes that receive the sample input.
        artifact_paths: The bundled artifacts (``artifact_name → local_path``)
            the container serves, threaded into the dry-run so validate-time
            scoring matches serve-time scoring byte-for-byte.

    Returns:
        Dict of column_name → polars dtype string.
    """
    from haute.deploy._scorer import artifact_identity_fingerprint

    artifact_fp = artifact_identity_fingerprint(artifact_paths)
    extra_keys = (output_node_id, *input_node_ids)
    if artifact_fp:
        extra_keys = (*extra_keys, artifact_fp)
    fp = graph_fingerprint(graph, *extra_keys)

    # Check cache
    cache_path = Path(_SCHEMA_CACHE_FILE)
    if cache_path.exists():
        try:
            cached = _json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("fingerprint") == fp:
                logger.info("output_schema_cache_hit", fingerprint=fp[:8])
                return dict(cached["schema"])
        except Exception as exc:
            logger.warning("corrupt_schema_cache", path=str(cache_path), error=str(exc))

    from haute.deploy._scorer import score_graph

    # Build a 1-row sample from the first input node's data
    if not input_node_ids:
        raise ValueError("No API input nodes found in the graph")
    node = _find_node(graph, input_node_ids[0])
    config = node.data.config
    path = config.get("path", "")

    if not path and node.data.nodeType != NodeType.DATA_INPUT:
        raise ValueError(
            f"Input node '{input_node_ids[0]}' has no path - cannot create sample row."
        )

    try:
        resolved_path = _resolve_pipeline_path(graph, path) if path else ""
        from haute._execution_context import ExecutionProfile
        from haute._polars_utils import streaming_collect

        sample = streaming_collect(
            _read_input_source(graph, node, resolved_path).head(1),
            profile=ExecutionProfile.DEPLOY_LIVE,
        )
    except Exception as exc:
        raise ValueError(f"Failed to read sample from '{path}': {exc}") from exc

    result = score_graph(
        graph=graph,
        input_df=sample,
        input_node_ids=input_node_ids,
        output_node_id=output_node_id,
        artifact_paths=artifact_paths,
    )

    schema = {col: str(result[col].dtype) for col in result.columns}

    # Write cache. A failed disk write (permissions, full disk) is genuinely
    # non-critical — the schema was computed truthfully and can be recomputed
    # next run — so only OSErrors are swallowed (with a warning). Any other
    # exception (e.g. a non-serialisable schema) is a real bug and must
    # propagate rather than be masked by a bare ``except: pass``.
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(_json.dumps({"fingerprint": fp, "schema": schema}), encoding="utf-8")
    except OSError as exc:
        logger.warning("output_schema_cache_write_failed", path=str(cache_path), error=str(exc))

    return schema


def _find_node(graph: PipelineGraph, node_id: str) -> GraphNode:
    """Find a node by ID in a graph using the cached node_map."""
    try:
        return graph.node_map[node_id]
    except KeyError:
        raise ConfigError("Node not found in graph", node_id=node_id) from None
