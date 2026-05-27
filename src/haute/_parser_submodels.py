"""Submodel parsing and merging for the pipeline parser.

Handles:
- Extracting ``pipeline.submodel("path")`` calls from AST
- Parsing individual submodel .py files
- Merging submodel graphs into the parent pipeline graph
  (either flattened for execution or hierarchical for the GUI)
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from haute._ast_helpers import (
    _extract_connect_calls,
    _extract_function_bodies,
    _extract_submodel_meta,
    _is_submodel_node_decorator,
)
from haute._flatten import flatten_graph
from haute._graph_builders import (
    _build_edges,
    _build_rf_nodes,
    _extract_decorated_nodes,
)
from haute._logging import get_logger
from haute._submodel_graph import (
    build_submodel_placeholder,
    classify_ports,
    rewire_edges,
)
from haute._types import GraphEdge, GraphNode, PipelineGraph

logger = get_logger(component="parser.submodels")


def extract_submodel_calls(tree: ast.Module) -> list[str]:
    """Find pipeline.submodel("path") calls and return the file paths."""
    paths: list[str] = []
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.Expr):
            continue
        call = node.value
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "submodel"
            and isinstance(func.value, ast.Name)
            and func.value.id == "pipeline"
        ):
            if call.args and isinstance(call.args[0], ast.Constant):
                paths.append(str(call.args[0].value))
    return paths


def parse_submodel_source(
    source: str,
    source_file: str = "",
    _base_dir: Path | None = None,
) -> PipelineGraph:
    """Parse submodel source code and return a PipelineGraph.

    *_base_dir* is the project root for resolving ``config=`` references.
    """

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return PipelineGraph(
            pipeline_name="unnamed",
            pipeline_description="",
            source_file=source_file,
            warning="Submodel file has syntax errors",
        )

    submodel_name, submodel_desc = _extract_submodel_meta(tree)

    func_bodies = _extract_function_bodies(source, tree=tree)
    raw_nodes = _extract_decorated_nodes(
        tree,
        _is_submodel_node_decorator,
        func_bodies,
        _base_dir,
    )

    edges = _build_edges(raw_nodes, _extract_connect_calls(tree, receiver="submodel"))
    rf_nodes = _build_rf_nodes(raw_nodes)

    return PipelineGraph(
        nodes=rf_nodes,
        edges=edges,
        pipeline_name=submodel_name,
        pipeline_description=submodel_desc,
        source_file=source_file,
    )


def merge_submodels(
    parent_graph: PipelineGraph,
    submodel_graphs: dict[str, PipelineGraph],
    submodel_files: dict[str, str],
    parent_edges: (list[tuple[str, str, str | None]] | list[tuple[str, str]]),
    *,
    flatten: bool = False,
) -> PipelineGraph:
    """Merge parsed submodels into the parent graph.

    Always builds the hierarchical form first (a ``submodel__<name>``
    placeholder node for each child graph, with the full child graph
    stashed in ``PipelineGraph.submodels``).  When *flatten* is True,
    delegates to :func:`haute._flatten.flatten_graph` to dissolve the
    placeholders into their child nodes.  This keeps a single source of
    truth for the flattening algorithm — it used to live in two files
    and would silently drift.
    """
    if not submodel_graphs:
        return parent_graph

    parent_nodes: list[GraphNode] = list(parent_graph.nodes)
    parent_edge_list: list[GraphEdge] = list(parent_graph.edges)

    # Collect all child node IDs across all submodels
    all_child_ids: set[str] = set()
    for sm_graph in submodel_graphs.values():
        all_child_ids.update(n.id for n in sm_graph.nodes)

    # _build_edges drops edges where one endpoint is a submodel child node
    # (because it only knows about main-file nodes).  Reconstruct those
    # cross-boundary edges from the raw parent_edges tuples.
    #
    # Each parent_edges entry is a triple (src, tgt, source_port) per the
    # commit-6 codegen change; submodel cross-boundary edges don't carry
    # a user-facing port name (the `out__<id>` markers live on
    # sourceHandle on the GraphEdge serialisation, not in connect()).
    # We preserve `source_port` as `sourceHandle` so an apiInput edge
    # crossing into a submodel surfaces with the right port.
    existing_pairs = {(e.source, e.target) for e in parent_edge_list}
    for edge_tuple in parent_edges:
        # Tolerate both pre-commit-6 2-tuples and the new 3-tuples
        # (commit-6 codegen extends connect calls with `source_port`).
        if len(edge_tuple) == 3:
            src, tgt, source_port = edge_tuple
        else:
            src, tgt = edge_tuple
            source_port = None
        if (src, tgt) in existing_pairs:
            continue
        if src in all_child_ids or tgt in all_child_ids:
            parent_edge_list.append(
                GraphEdge(
                    id=f"e_{src}_{tgt}",
                    source=src,
                    target=tgt,
                    sourceHandle=source_port,
                )
            )
            existing_pairs.add((src, tgt))

    # Hierarchical mode: create submodel placeholder nodes
    submodels_meta: dict[str, dict] = {}

    for sm_name, sm_graph in submodel_graphs.items():
        child_node_ids = [n.id for n in sm_graph.nodes]
        child_node_names = set(child_node_ids)

        sm_file = submodel_files.get(sm_name, "")

        # Determine input and output ports from cross-boundary edges
        input_ports, output_ports = classify_ports(parent_edges, child_node_names)

        # Build the submodel placeholder node
        sm_node = build_submodel_placeholder(
            sm_name,
            sm_file,
            child_node_ids,
            input_ports,
            output_ports,
            description=sm_graph.pipeline_description or "",
        )
        parent_nodes.append(sm_node)

        # Rewire edges via shared helper
        parent_edge_list = rewire_edges(
            parent_edge_list,
            sm_node.id,
            child_node_names,
        )

        submodels_meta[sm_name] = {
            "file": sm_file,
            "childNodeIds": child_node_ids,
            "inputPorts": input_ports,
            "outputPorts": output_ports,
            "graph": sm_graph.model_dump(),
        }

    update: dict[str, Any] = {"nodes": parent_nodes, "edges": parent_edge_list}
    if submodels_meta:
        update["submodels"] = submodels_meta
    hierarchical = parent_graph.model_copy(update=update)

    if flatten:
        # Single source of truth for the flatten algorithm — the dedicated
        # flattener in ``_flatten.py`` dissolves every submodel at once,
        # rewiring the handle-decorated boundary edges back to direct
        # child→parent / parent→child edges.
        return flatten_graph(hierarchical)

    return hierarchical
