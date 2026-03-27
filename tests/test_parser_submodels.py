"""Tests for haute._parser_submodels — submodel parsing and merging."""

from __future__ import annotations

import ast

import pytest

from haute._parser_submodels import (
    extract_submodel_calls,
    merge_submodels,
    parse_submodel_source,
)
from haute.graph_utils import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph


# ---------------------------------------------------------------------------
# extract_submodel_calls
# ---------------------------------------------------------------------------


class TestExtractSubmodelCalls:
    def test_single_submodel_call(self) -> None:
        source = 'pipeline.submodel("modules/pricing.py")\n'
        tree = ast.parse(source)
        paths = extract_submodel_calls(tree)
        assert paths == ["modules/pricing.py"]

    def test_multiple_submodel_calls(self) -> None:
        source = 'pipeline.submodel("modules/a.py")\npipeline.submodel("modules/b.py")\n'
        tree = ast.parse(source)
        paths = extract_submodel_calls(tree)
        assert paths == ["modules/a.py", "modules/b.py"]

    def test_no_submodel_calls(self) -> None:
        source = "x = 1\nprint(x)\n"
        tree = ast.parse(source)
        assert extract_submodel_calls(tree) == []

    def test_ignores_non_pipeline_submodel(self) -> None:
        source = 'other.submodel("path.py")\n'
        tree = ast.parse(source)
        paths = extract_submodel_calls(tree)
        assert paths == []

    def test_ignores_chained_receiver_submodel(self) -> None:
        """module.pipeline.submodel("path") should not be picked up."""
        source = 'module.pipeline.submodel("path.py")\n'
        tree = ast.parse(source)
        paths = extract_submodel_calls(tree)
        assert paths == []

    def test_various_non_pipeline_receivers_rejected(self) -> None:
        """Ensure several different receiver names are all rejected."""
        for receiver in ["other", "submodel", "config", "self"]:
            source = f'{receiver}.submodel("path.py")\n'
            tree = ast.parse(source)
            paths = extract_submodel_calls(tree)
            assert paths == [], f"Expected empty for receiver={receiver}"

    def test_ignores_method_call_without_arg(self) -> None:
        source = "pipeline.submodel()\n"
        tree = ast.parse(source)
        assert extract_submodel_calls(tree) == []

    def test_ignores_non_constant_arg(self) -> None:
        source = "pipeline.submodel(some_var)\n"
        tree = ast.parse(source)
        assert extract_submodel_calls(tree) == []

    def test_ignores_non_call_expressions(self) -> None:
        source = 'x = pipeline.submodel("test.py")\n'
        tree = ast.parse(source)
        # This is an assignment, not a bare expression
        assert extract_submodel_calls(tree) == []


# ---------------------------------------------------------------------------
# parse_submodel_source
# ---------------------------------------------------------------------------

_VALID_SUBMODEL = '''\
import polars as pl
import haute

submodel = haute.Submodel("pricing", description="Pricing submodel")

@submodel.polars
def base_rate(df: pl.LazyFrame) -> pl.LazyFrame:
    """Calculate base rate."""
    return df.with_columns(pl.lit(100.0).alias("base"))

@submodel.polars
def adjust(base_rate: pl.LazyFrame) -> pl.LazyFrame:
    """Apply adjustment."""
    return base_rate.with_columns((pl.col("base") * 1.1).alias("adjusted"))

submodel.connect("base_rate", "adjust")
'''


class TestParseSubmodelSource:
    def test_parses_valid_submodel(self) -> None:
        graph = parse_submodel_source(_VALID_SUBMODEL, "modules/pricing.py")
        assert graph.pipeline_name == "pricing"
        assert graph.pipeline_description == "Pricing submodel"
        assert len(graph.nodes) == 2
        node_ids = [n.id for n in graph.nodes]
        assert "base_rate" in node_ids
        assert "adjust" in node_ids

    def test_edges_extracted(self) -> None:
        graph = parse_submodel_source(_VALID_SUBMODEL, "modules/pricing.py")
        edge_pairs = [(e.source, e.target) for e in graph.edges]
        assert ("base_rate", "adjust") in edge_pairs

    def test_source_file_stored(self) -> None:
        graph = parse_submodel_source(_VALID_SUBMODEL, "modules/pricing.py")
        assert graph.source_file == "modules/pricing.py"

    def test_syntax_error_returns_warning_graph(self) -> None:
        bad_source = "def broken(:\n    pass\n"
        graph = parse_submodel_source(bad_source, "broken.py")
        assert graph.warning is not None
        assert "syntax errors" in graph.warning
        assert graph.pipeline_name == "unnamed"

    def test_empty_source(self) -> None:
        graph = parse_submodel_source("", "empty.py")
        assert graph.nodes == []
        assert graph.edges == []

    def test_submodel_without_meta(self) -> None:
        source = """\
import polars as pl
import haute

submodel = haute.Submodel("unnamed")

@submodel.polars
def only_node(df: pl.LazyFrame) -> pl.LazyFrame:
    return df
"""
        graph = parse_submodel_source(source, "test.py")
        assert len(graph.nodes) == 1


# ---------------------------------------------------------------------------
# merge_submodels
# ---------------------------------------------------------------------------


def _make_parent_graph() -> PipelineGraph:
    """Build a simple parent graph with 2 nodes."""
    n1 = GraphNode(
        id="load",
        data=NodeData(label="load", nodeType="dataSource", config={"path": "data.csv"}),
    )
    n2 = GraphNode(
        id="output",
        data=NodeData(label="output", nodeType="output", config={}),
    )
    e = GraphEdge(id="e_load_output", source="load", target="output")
    return PipelineGraph(
        nodes=[n1, n2],
        edges=[e],
        pipeline_name="main",
    )


def _make_child_graph() -> PipelineGraph:
    """Build a simple submodel graph with 2 nodes."""
    cn1 = GraphNode(
        id="child_a",
        data=NodeData(label="child_a", nodeType="polars", config={"code": "pass"}),
    )
    cn2 = GraphNode(
        id="child_b",
        data=NodeData(label="child_b", nodeType="polars", config={"code": "pass"}),
    )
    ce = GraphEdge(id="e_child_a_child_b", source="child_a", target="child_b")
    return PipelineGraph(
        nodes=[cn1, cn2],
        edges=[ce],
        pipeline_name="sub",
        pipeline_description="A submodel",
    )


class TestMergeSubmodels:
    def test_no_submodels_returns_parent(self) -> None:
        parent = _make_parent_graph()
        result = merge_submodels(parent, {}, {}, [])
        assert result is parent

    def test_flatten_inlines_child_nodes(self) -> None:
        parent = _make_parent_graph()
        child = _make_child_graph()
        result = merge_submodels(
            parent,
            {"sub": child},
            {"sub": "modules/sub.py"},
            parent_edges=[("load", "child_a"), ("child_b", "output")],
            flatten=True,
        )
        node_ids = {n.id for n in result.nodes}
        assert "child_a" in node_ids
        assert "child_b" in node_ids
        assert "load" in node_ids

    def test_flatten_includes_child_edges(self) -> None:
        parent = _make_parent_graph()
        child = _make_child_graph()
        result = merge_submodels(
            parent,
            {"sub": child},
            {"sub": "modules/sub.py"},
            parent_edges=[("load", "child_a"), ("child_b", "output")],
            flatten=True,
        )
        edge_pairs = {(e.source, e.target) for e in result.edges}
        assert ("child_a", "child_b") in edge_pairs
        assert ("load", "child_a") in edge_pairs
        assert ("child_b", "output") in edge_pairs

    def test_hierarchical_creates_submodel_node(self) -> None:
        parent = _make_parent_graph()
        child = _make_child_graph()
        result = merge_submodels(
            parent,
            {"sub": child},
            {"sub": "modules/sub.py"},
            parent_edges=[("load", "child_a"), ("child_b", "output")],
            flatten=False,
        )
        node_ids = {n.id for n in result.nodes}
        assert "submodel__sub" in node_ids

    def test_hierarchical_rewires_edges(self) -> None:
        parent = _make_parent_graph()
        child = _make_child_graph()
        result = merge_submodels(
            parent,
            {"sub": child},
            {"sub": "modules/sub.py"},
            parent_edges=[("load", "child_a"), ("child_b", "output")],
            flatten=False,
        )
        edge_sources = {e.source for e in result.edges}
        edge_targets = {e.target for e in result.edges}
        # The submodel node should appear as source or target
        assert "submodel__sub" in edge_sources or "submodel__sub" in edge_targets

    def test_hierarchical_stores_submodels_meta(self) -> None:
        parent = _make_parent_graph()
        child = _make_child_graph()
        result = merge_submodels(
            parent,
            {"sub": child},
            {"sub": "modules/sub.py"},
            parent_edges=[("load", "child_a")],
            flatten=False,
        )
        assert result.submodels is not None
        assert "sub" in result.submodels
        meta = result.submodels["sub"]
        assert meta["file"] == "modules/sub.py"
        assert "child_a" in meta["childNodeIds"]
        assert "child_b" in meta["childNodeIds"]

    def test_multiple_submodels(self) -> None:
        parent = _make_parent_graph()
        child1 = _make_child_graph()
        cn3 = GraphNode(
            id="child_c",
            data=NodeData(label="child_c", nodeType="polars", config={}),
        )
        child2 = PipelineGraph(nodes=[cn3], edges=[], pipeline_name="sub2")

        result = merge_submodels(
            parent,
            {"sub": child1, "sub2": child2},
            {"sub": "modules/sub.py", "sub2": "modules/sub2.py"},
            parent_edges=[],
            flatten=True,
        )
        node_ids = {n.id for n in result.nodes}
        assert "child_a" in node_ids
        assert "child_c" in node_ids
