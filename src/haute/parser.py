"""Parser: .py pipeline file → React Flow graph JSON.

Uses Python's ast module to extract @pipeline.<type> decorated functions
and pipeline.connect() calls, producing the same graph JSON format
that the frontend expects.

libcst is reserved for surgical write-back (codegen edits) where
preserving formatting matters.
"""

from __future__ import annotations

import ast
from pathlib import Path

from haute._ast_helpers import (
    _extract_connect_calls,
    _extract_function_bodies,
    _extract_pipeline_meta,
    _extract_preamble,
    _extract_preserved_blocks,
    _is_pipeline_node_decorator,
)
from haute._graph_builders import (
    _build_edges,
    _build_rf_nodes,
    _extract_decorated_nodes,
)
from haute._graph_shape import validate_pipeline_graph_shape_contracts
from haute._io import read_user_text
from haute._logging import get_logger
from haute._parser_regex import fallback_parse as _fallback_parse
from haute._parser_submodels import extract_submodel_calls as _extract_submodel_calls
from haute._parser_submodels import merge_submodels as _merge_submodels
from haute._parser_submodels import parse_submodel_source as _parse_submodel_source
from haute._project import get_project_root
from haute._submodel_paths import resolve_submodel_reference
from haute.errors import ConfigError
from haute.graph_utils import PipelineGraph

logger = get_logger(component="parser")

__all__ = [
    "parse_pipeline_file",
    "parse_pipeline_source",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _infer_parse_base_dir(filepath: Path) -> Path:
    """Resolve config/submodel references from the Haute project root when available."""
    try:
        return get_project_root(filepath.parent)
    except ConfigError:
        return filepath.parent


def _format_load_error_warning(labels: list[str]) -> str | None:
    """Build a user-facing warning when config files failed to load."""
    if not labels:
        return None
    names = ", ".join(labels[:3])
    suffix = f" and {len(labels) - 3} more" if len(labels) > 3 else ""
    return (
        f"Config files could not be loaded for: {names}{suffix}. "
        "These configs will not be overwritten on save."
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_pipeline_file(filepath: str | Path, *, flatten: bool = False) -> PipelineGraph:
    """Parse a pipeline .py file and return a PipelineGraph.

    Args:
        flatten: If *True*, dissolve submodel groupings into a flat graph
            (for executor / trace / deploy).  If *False* (default), keep
            submodel metadata so the GUI can render collapsed submodel nodes.

    On syntax errors the file is still parsed via regex fallback so
    that valid nodes are returned alongside broken ones.
    """
    filepath = Path(filepath)
    source = read_user_text(filepath)
    submodel_base_dir = _infer_parse_base_dir(filepath)
    return parse_pipeline_source(
        source,
        source_file=str(filepath),
        flatten=flatten,
        _base_dir=filepath.parent,
        _submodel_base_dir=submodel_base_dir,
    )


def parse_submodel_file(
    filepath: str | Path,
    _base_dir: Path | None = None,
) -> PipelineGraph:
    """Parse a submodel .py file and return a PipelineGraph.

    The submodel name and description are stored in ``pipeline_name``
    and ``pipeline_description`` respectively.

    *_base_dir* is the project root for resolving config file references.
    Defaults to ``filepath.parent`` if not provided.
    """
    filepath = Path(filepath)
    source = read_user_text(filepath)
    return _parse_submodel_source(
        source,
        source_file=str(filepath),
        _base_dir=_base_dir or _infer_parse_base_dir(filepath),
    )


def parse_pipeline_source(
    source: str,
    source_file: str = "",
    *,
    flatten: bool = False,
    _base_dir: Path | None = None,
    _submodel_base_dir: Path | None = None,
) -> PipelineGraph:
    """Parse pipeline source code and return a PipelineGraph.

    Args:
        flatten: If True, dissolve submodels into flat graph.
        _base_dir: Directory to resolve relative config paths against.
        _submodel_base_dir: Directory to resolve relative submodel paths
            against. Defaults to ``_base_dir`` when omitted.
    """
    if not source_file and _base_dir is not None:
        source_file = str((_base_dir / "__source__.py").resolve())

    # Syntax check - fall back to regex if the file has errors
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        logger.warning("fallback_parse", file=source_file, line=e.lineno)
        return _fallback_parse(source, source_file, e)

    # Pipeline metadata
    pipeline_name, pipeline_desc = _extract_pipeline_meta(tree)

    # Find @pipeline.<type> decorated functions
    func_bodies = _extract_function_bodies(source, tree=tree)
    raw_nodes = _extract_decorated_nodes(
        tree,
        _is_pipeline_node_decorator,
        func_bodies,
        _base_dir,
    )

    explicit_connects = _extract_connect_calls(tree)
    edges = _build_edges(raw_nodes, explicit_connects) if raw_nodes else []
    rf_nodes = _build_rf_nodes(raw_nodes)
    preamble = _extract_preamble(source)
    preserved_blocks = _extract_preserved_blocks(source)

    # Surface config load errors as a graph-level warning for the GUI.
    load_error_labels = [n.data.label for n in rf_nodes if n.data.config.get("_load_error")]

    graph = PipelineGraph(
        nodes=rf_nodes,
        edges=edges,
        pipeline_name=pipeline_name,
        pipeline_description=pipeline_desc,
        preamble=preamble,
        preserved_blocks=preserved_blocks,
        source_file=source_file,
        warning=_format_load_error_warning(load_error_labels),
    )

    # --- Submodel handling ---------------------------------------------------
    submodel_paths = _extract_submodel_calls(tree)
    submodel_base_dir = _submodel_base_dir or _base_dir
    if submodel_paths and submodel_base_dir is not None:
        submodel_graphs: dict[str, PipelineGraph] = {}
        submodel_files: dict[str, str] = {}
        resolved_submodel_root = submodel_base_dir.resolve()

        for rel_path in submodel_paths:
            sm_filepath, sm_base_dir = resolve_submodel_reference(
                rel_path,
                pipeline_dir=_base_dir,
                project_root=resolved_submodel_root,
            )
            if not sm_filepath.is_file():
                continue
            sm_graph = parse_submodel_file(sm_filepath, _base_dir=sm_base_dir)
            sm_name = sm_graph.pipeline_name or sm_filepath.stem
            if sm_name in submodel_graphs:
                logger.warning("submodel_name_collision", name=sm_name)
            submodel_graphs[sm_name] = sm_graph
            submodel_files[sm_name] = rel_path

        if submodel_graphs:
            graph = _merge_submodels(
                graph,
                submodel_graphs,
                submodel_files,
                explicit_connects,
                flatten=flatten,
            )

    validate_pipeline_graph_shape_contracts(
        graph,
        graph_label=graph.pipeline_name or source_file or "pipeline",
    )

    logger.info(
        "pipeline_parsed",
        file=source_file,
        node_count=len(graph.nodes),
        edge_count=len(graph.edges),
        pipeline_name=graph.pipeline_name,
    )
    return graph
