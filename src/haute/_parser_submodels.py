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
from haute.errors import ParseError

logger = get_logger(component="parser.submodels")


def _submodel_path_expr(link: ast.Call) -> ast.expr | None:
    """Return the path argument node of a ``submodel(...)`` link, if any.

    Accepts the positional form ``submodel("path")`` and the keyword form
    ``submodel(path="path")``. Returns ``None`` when the call carries no
    path argument at all (e.g. ``pipeline.submodel()``).
    """
    if link.args:
        return link.args[0]
    for kw in link.keywords:
        if kw.arg == "path":
            return kw.value
    return None


def extract_submodel_calls(tree: ast.Module) -> list[str]:
    """Find ``pipeline.submodel("path")`` calls and return the file paths.

    Mirrors :func:`haute._ast_helpers._extract_connect_calls`: the method
    chain is walked from the outermost call down to its base receiver, so
    chained ``pipeline.submodel("a").submodel("b")`` and keyword-form
    ``pipeline.submodel(path="a")`` calls contribute their submodels instead
    of being silently dropped. The terminal base must be a bare ``pipeline``
    name, so ``module.pipeline.submodel(...)`` and ``other.submodel(...)``
    stay rejected.

    A resolved ``pipeline.submodel(...)`` whose path is a non-literal
    expression raises :class:`ParseError`: the parser never executes the
    file, so the reference cannot be resolved, and silently dropping it
    would discard the entire submodel (and re-emit the file without it on
    the next save).
    """
    paths: list[str] = []
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.Expr):
            continue
        call = node.value
        if not isinstance(call, ast.Call):
            continue

        # Collect every .submodel link in the method chain, walking from the
        # outermost call down to the base receiver.
        links: list[ast.Call] = []
        cur: ast.expr = call
        while isinstance(cur, ast.Call) and isinstance(cur.func, ast.Attribute):
            if cur.func.attr == "submodel":
                links.append(cur)
            cur = cur.func.value
        if not links:
            continue
        if not (isinstance(cur, ast.Name) and cur.id == "pipeline"):
            continue

        # links were collected outermost-first; reverse for source order.
        for link in reversed(links):
            path_expr = _submodel_path_expr(link)
            if path_expr is None:
                continue
            if isinstance(path_expr, ast.Constant) and isinstance(path_expr.value, str):
                paths.append(path_expr.value)
            else:
                raise ParseError(
                    "pipeline.submodel() path must be a string literal; the "
                    f"non-literal expression {ast.unparse(path_expr)!r} cannot be "
                    "resolved at parse time and its submodel would be dropped.",
                    line=getattr(path_expr, "lineno", None),
                )
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

    # Nested submodels are capped at one level. A ``pipeline.submodel(...)``
    # call inside a submodel file would otherwise be silently ignored (its
    # nodes never appear anywhere). Surface it as a graph-level warning so the
    # drop is visible rather than silent.
    nested_paths = extract_submodel_calls(tree)
    nested_warning: str | None = None
    if nested_paths:
        nested_warning = (
            "Nested submodels are not supported; "
            f"ignored pipeline.submodel() reference(s): {', '.join(nested_paths)}"
        )
        logger.warning("nested_submodel_ignored", paths=nested_paths, file=source_file)

    return PipelineGraph(
        nodes=rf_nodes,
        edges=edges,
        pipeline_name=submodel_name,
        pipeline_description=submodel_desc,
        source_file=source_file,
        warning=nested_warning,
    )


def merge_submodels(
    parent_graph: PipelineGraph,
    submodel_graphs: dict[str, PipelineGraph],
    submodel_files: dict[str, str],
    parent_edges: (
        list[tuple[str, str, str | None, str | None]]
        | list[tuple[str, str, str | None]]
        | list[tuple[str, str]]
    ),
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
    # Each parent_edges entry may carry source/target ports. We preserve
    # those handles while reconstructing cross-boundary edges; later
    # rewiring replaces true submodel boundary handles with in__/out__
    # markers.
    existing_pairs = {(e.source, e.target) for e in parent_edge_list}
    for edge_tuple in parent_edges:
        # Tolerate pre-port 2-tuples, source-port 3-tuples, and the
        # current 4-tuple shape with source and target ports.
        if len(edge_tuple) == 4:
            src, tgt, source_port, target_port = edge_tuple
        elif len(edge_tuple) == 3:
            src, tgt, source_port = edge_tuple
            target_port = None
        else:
            src, tgt = edge_tuple
            source_port = None
            target_port = None
        if (src, tgt) in existing_pairs:
            continue
        if src in all_child_ids or tgt in all_child_ids:
            parent_edge_list.append(
                GraphEdge(
                    id=f"e_{src}_{tgt}",
                    source=src,
                    target=tgt,
                    sourceHandle=source_port,
                    targetHandle=target_port,
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

    # ``submodels_meta`` is always populated here: the early return above
    # guarantees ``submodel_graphs`` is non-empty, and the loop sets one
    # entry per submodel. No guard needed.
    update: dict[str, Any] = {
        "nodes": parent_nodes,
        "edges": parent_edge_list,
        "submodels": submodels_meta,
    }
    hierarchical = parent_graph.model_copy(update=update)

    if flatten:
        # Single source of truth for the flatten algorithm — the dedicated
        # flattener in ``_flatten.py`` dissolves every submodel at once,
        # rewiring the handle-decorated boundary edges back to direct
        # child→parent / parent→child edges.
        return flatten_graph(hierarchical)

    return hierarchical
