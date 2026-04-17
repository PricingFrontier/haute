"""Input/output schema inference for deployed pipelines."""

from __future__ import annotations

import json as _json
from pathlib import Path

from haute._cache import graph_fingerprint
from haute._logging import get_logger
from haute.errors import ConfigError
from haute.graph_utils import GraphNode, PipelineGraph, read_source

logger = get_logger(component="deploy.schema")

_SCHEMA_CACHE_FILE = ".haute_cache/output_schema.json"


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

    if not path:
        raise ValueError(
            f"Input node '{input_node_id}' has no path configured. Cannot infer schema."
        )

    try:
        lf = read_source(path)
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
) -> dict[str, str]:
    """Infer the output schema by dry-running one sample row.

    Executes the pruned graph with a single sample row injected at each
    input node, and reads the output node's columns + types.

    Results are cached in ``.haute_cache/output_schema.json`` keyed by
    graph fingerprint so unchanged pipelines skip the dry-run.

    Args:
        graph: Pruned graph.
        output_node_id: The output node to read results from.
        input_node_ids: Source nodes that receive the sample input.

    Returns:
        Dict of column_name → polars dtype string.
    """
    fp = graph_fingerprint(graph, output_node_id, *input_node_ids)

    # Check cache
    cache_path = Path(_SCHEMA_CACHE_FILE)
    if cache_path.exists():
        try:
            cached = _json.loads(cache_path.read_text())
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

    if not path:
        raise ValueError(
            f"Input node '{input_node_ids[0]}' has no path - cannot create sample row."
        )

    try:
        sample = read_source(path).head(1).collect()
    except Exception as exc:
        raise ValueError(f"Failed to read sample from '{path}': {exc}") from exc

    result = score_graph(
        graph=graph,
        input_df=sample,
        input_node_ids=input_node_ids,
        output_node_id=output_node_id,
    )

    schema = {col: str(result[col].dtype) for col in result.columns}

    # Write cache
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(_json.dumps({"fingerprint": fp, "schema": schema}))
    except Exception:
        pass  # non-critical — cache write failure shouldn't block deploy

    return schema


def _find_node(graph: PipelineGraph, node_id: str) -> GraphNode:
    """Find a node by ID in a graph using the cached node_map."""
    try:
        return graph.node_map[node_id]
    except KeyError:
        raise ConfigError("Node not found in graph", node_id=node_id) from None
