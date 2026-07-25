"""Submodel parsing and merging for the pipeline parser.

Handles:
- Extracting ``pipeline.submodel("path")`` calls from AST
- Parsing individual submodel .py files
- Merging submodel graphs into the parent pipeline graph
  (either flattened for execution or hierarchical for the GUI)
"""

from __future__ import annotations

import ast
from os.path import normcase
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
from haute._graph_utils import _edge_id
from haute._parser_conservation import assert_parser_structure_conserved
from haute._submodel_graph import (
    build_submodel_placeholder,
    classify_ports,
    rewire_edges,
)
from haute._types import GraphEdge, GraphNode, PipelineGraph
from haute.errors import ParseError

_Connect4 = tuple[str, str, str | None, str | None]


def build_unique_submodel_maps(
    parsed: list[tuple[str, PipelineGraph]],
) -> tuple[dict[str, PipelineGraph], dict[str, str]]:
    """Index parsed submodels by declared name, rejecting every collision.

    ``parsed`` preserves authored reference order as ``(path, graph)`` pairs.
    Resolved source files are grouped first so a repeated reference is not
    misreported as several files declaring one name. Grouping names before
    constructing the dictionaries then prevents the historical later-file-
    wins overwrite and lets one diagnostic name every genuinely distinct
    file involved in a declared-name collision.
    """
    by_source_file: dict[str, dict[str, Any]] = {}
    for rel_path, graph in parsed:
        source_file = graph.source_file or rel_path
        source_key = normcase(str(Path(source_file).resolve()))
        entry = by_source_file.setdefault(
            source_key,
            {
                "source_file": source_file,
                "references": [],
            },
        )
        entry["references"].append(rel_path)

    duplicate_files = [entry for entry in by_source_file.values() if len(entry["references"]) > 1]
    if duplicate_files:
        duplicate_context: dict[str, Any] = {"duplicate_files": duplicate_files}
        if len(duplicate_files) == 1:
            duplicate_file = duplicate_files[0]
            duplicate_context.update(
                source_file=duplicate_file["source_file"],
                references=duplicate_file["references"],
            )
        raise ParseError(
            "The same submodel file is referenced more than once.",
            **duplicate_context,
        )

    by_name: dict[str, list[tuple[str, PipelineGraph]]] = {}
    for rel_path, graph in parsed:
        name = graph.pipeline_name or Path(rel_path).stem
        by_name.setdefault(name, []).append((rel_path, graph))

    collisions = {
        name: [rel_path for rel_path, _graph in entries]
        for name, entries in by_name.items()
        if len(entries) > 1
    }
    if collisions:
        context: dict[str, Any] = {"collisions": collisions}
        if len(collisions) == 1:
            submodel_name, files = next(iter(collisions.items()))
            context.update(submodel_name=submodel_name, files=files)
        raise ParseError(
            "Multiple files declare the same submodel name.",
            **context,
        )

    submodel_graphs: dict[str, PipelineGraph] = {}
    submodel_files: dict[str, str] = {}
    for name, entries in by_name.items():
        rel_path, graph = entries[0]
        submodel_graphs[name] = graph
        submodel_files[name] = rel_path
    return submodel_graphs, submodel_files


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
    except SyntaxError as exc:
        raise ParseError(
            "Submodel file has syntax errors; its graph cannot be recovered.",
            source_file=source_file,
            line=exc.lineno,
            offset=exc.offset,
        ) from exc

    submodel_name, submodel_desc = _extract_submodel_meta(tree)

    # Nested submodels are capped at one level. Returning the outer child
    # graph while dropping these authored references would corrupt the source
    # on its next save, so the producer contract is enforced as a typed error.
    nested_paths = extract_submodel_calls(tree)
    if nested_paths:
        raise ParseError(
            "Nested submodels are not supported.",
            source_file=source_file,
            nested_paths=nested_paths,
        )

    func_bodies = _extract_function_bodies(source, tree=tree)
    raw_nodes = _extract_decorated_nodes(
        tree,
        _is_submodel_node_decorator,
        func_bodies,
        _base_dir,
    )

    explicit_connects = _extract_connect_calls(tree, receiver="submodel")
    edges = _build_edges(raw_nodes, explicit_connects)
    rf_nodes = _build_rf_nodes(raw_nodes)
    assert_parser_structure_conserved(
        raw_nodes=raw_nodes,
        explicit_connects=explicit_connects,
        root_nodes=rf_nodes,
        root_edges=edges,
    )

    graph = PipelineGraph(
        nodes=rf_nodes,
        edges=edges,
        pipeline_name=submodel_name,
        pipeline_description=submodel_desc,
        source_file=source_file,
    )
    graph._parser_parameter_names = {
        str(node["func_name"]): [str(name) for name in node.get("param_names", ())]
        for node in raw_nodes
    }
    return graph


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

    resolved_parent_edges: list[_Connect4] = []
    for edge_tuple in parent_edges:
        if len(edge_tuple) == 4:
            resolved_parent_edges.append(edge_tuple)
        elif len(edge_tuple) == 3:
            src, tgt, source_port = edge_tuple
            resolved_parent_edges.append((src, tgt, source_port, None))
        else:
            src, tgt = edge_tuple
            resolved_parent_edges.append((src, tgt, None, None))

    # A function parameter can name a node outside its own source file. Keep
    # parser-private signature metadata just long enough to infer those edges
    # after all root and child IDs are known.
    all_graphs = [parent_graph, *submodel_graphs.values()]
    known_node_ids = {node.id for candidate_graph in all_graphs for node in candidate_graph.nodes}
    occupied_pairs = {
        (source, target) for source, target, _source_port, _target_port in resolved_parent_edges
    }
    occupied_pairs.update(
        (edge.source, edge.target)
        for candidate_graph in all_graphs
        for edge in candidate_graph.edges
    )
    for candidate_graph in all_graphs:
        for target_id, parameter_names in candidate_graph._parser_parameter_names.items():
            for source_id in parameter_names:
                pair = (source_id, target_id)
                if (
                    source_id in known_node_ids
                    and source_id != target_id
                    and pair not in occupied_pairs
                ):
                    occupied_pairs.add(pair)
                    resolved_parent_edges.append((source_id, target_id, None, None))

    # _build_edges drops edges where one endpoint is a submodel child node
    # (because it only knows about main-file nodes).  Reconstruct those
    # cross-boundary edges from the raw parent_edges tuples.
    #
    # Each parent_edges entry may carry source/target ports. We preserve
    # those handles while reconstructing cross-boundary edges; later
    # rewiring replaces true submodel boundary handles with in__/out__
    # markers.
    existing_edges = {
        (e.source, e.target, e.sourceHandle, e.targetHandle) for e in parent_edge_list
    }
    for src, tgt, source_port, target_port in resolved_parent_edges:
        identity = (src, tgt, source_port, target_port)
        if identity in existing_edges:
            continue
        if src in all_child_ids or tgt in all_child_ids:
            parent_edge_list.append(
                GraphEdge(
                    id=_edge_id(src, tgt, source_port, target_port),
                    source=src,
                    target=tgt,
                    sourceHandle=source_port,
                    targetHandle=target_port,
                )
            )
            existing_edges.add(identity)

    # Hierarchical mode: create submodel placeholder nodes
    submodels_meta: dict[str, dict] = {}

    for sm_name, sm_graph in submodel_graphs.items():
        child_node_ids = [n.id for n in sm_graph.nodes]
        child_node_names = set(child_node_ids)

        sm_file = submodel_files.get(sm_name, "")

        # Determine input and output ports from cross-boundary edges
        input_ports, output_ports = classify_ports(
            resolved_parent_edges,
            child_node_names,
        )

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
