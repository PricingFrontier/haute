"""Tests for haute._parser_submodels — submodel parsing and merging."""

from __future__ import annotations

import ast

import pytest

from haute._parser_submodels import (
    build_unique_submodel_maps,
    extract_submodel_calls,
    merge_submodels,
    parse_submodel_source,
)
from haute.errors import ParseError
from haute.graph_utils import GraphEdge, GraphNode, NodeData, PipelineGraph

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

    def test_non_constant_arg_raises(self) -> None:
        """A non-literal submodel path fails loud: it cannot be resolved
        offline and silently dropping it would discard the whole submodel."""
        source = "pipeline.submodel(some_var)\n"
        tree = ast.parse(source)
        with pytest.raises(ParseError, match="must be a string literal"):
            extract_submodel_calls(tree)

    def test_ignores_non_call_expressions(self) -> None:
        source = 'x = pipeline.submodel("test.py")\n'
        tree = ast.parse(source)
        # This is an assignment, not a bare expression
        assert extract_submodel_calls(tree) == []

    def test_keyword_form_submodel_recovered(self) -> None:
        """``pipeline.submodel(path="...")`` must not be silently dropped."""
        tree = ast.parse('pipeline.submodel(path="modules/a.py")\n')
        assert extract_submodel_calls(tree) == ["modules/a.py"]

    def test_chained_submodel_calls_recovered(self) -> None:
        """Chained ``.submodel(...).submodel(...)`` contributes both, in order."""
        tree = ast.parse('pipeline.submodel("modules/a.py").submodel("modules/b.py")\n')
        assert extract_submodel_calls(tree) == ["modules/a.py", "modules/b.py"]

    def test_chained_receiver_still_rejected(self) -> None:
        """The chain walk must still reject a non-``pipeline`` base receiver."""
        tree = ast.parse('module.pipeline.submodel("a.py").submodel("b.py")\n')
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

    def test_submodel_preamble_and_preserved_block_round_trip(self) -> None:
        source = """\
import polars as pl
import haute

HELPER = 1
# haute:preserve-start
KEPT = "yes"
# haute:preserve-end

submodel = haute.Submodel("pricing", description="Pricing submodel")

@submodel.polars
def base_rate(df: pl.LazyFrame) -> pl.LazyFrame:
    return df
"""
        graph = parse_submodel_source(source, "modules/pricing.py")
        assert graph.preamble == "HELPER = 1"
        assert graph.preserved_blocks == ['KEPT = "yes"']

    def test_syntax_error_raises_instead_of_returning_empty_graph(self) -> None:
        bad_source = "def broken(:\n    pass\n"
        with pytest.raises(ParseError, match="syntax errors") as exc_info:
            parse_submodel_source(bad_source, "broken.py")

        assert exc_info.value.context["source_file"] == "broken.py"

    def test_empty_source(self) -> None:
        graph = parse_submodel_source("", "empty.py")
        assert graph.nodes == []
        assert graph.edges == []

    def test_nested_submodel_calls_raise_with_every_path(self) -> None:
        """A submodel parser may not return a graph after dropping deeper references."""
        source = """\
import polars as pl
import haute

submodel = haute.Submodel("outer")

@submodel.polars
def base(df: pl.LazyFrame) -> pl.LazyFrame:
    return df

pipeline.submodel("modules/inner.py")
pipeline.submodel("modules/other.py")
"""
        with pytest.raises(ParseError, match="Nested submodels") as exc_info:
            parse_submodel_source(source, "modules/outer.py")

        assert exc_info.value.context == {
            "source_file": "modules/outer.py",
            "nested_paths": ["modules/inner.py", "modules/other.py"],
        }

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


class TestBuildUniqueSubmodelMaps:
    def test_repeated_resolved_file_has_specific_diagnostic(self) -> None:
        graph = PipelineGraph(
            pipeline_name="pricing",
            source_file="modules/pricing.py",
        )

        with pytest.raises(ParseError, match="same submodel file") as exc_info:
            build_unique_submodel_maps(
                [
                    ("modules/pricing.py", graph),
                    ("modules/pricing.py", graph),
                ]
            )

        assert exc_info.value.context["source_file"] == "modules/pricing.py"
        assert exc_info.value.context["references"] == [
            "modules/pricing.py",
            "modules/pricing.py",
        ]


# ---------------------------------------------------------------------------
# merge_submodels
# ---------------------------------------------------------------------------


def _make_parent_graph() -> PipelineGraph:
    """Build a simple parent graph with 2 nodes."""
    n1 = GraphNode(
        id="load",
        data=NodeData(label="load", nodeType="dataInput", config={"path": "data.csv"}),
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
            parent_edges=[("load", "child_a", None, None), ("child_b", "output", None, None)],
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
            parent_edges=[("load", "child_a", None, None), ("child_b", "output", None, None)],
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
            parent_edges=[("load", "child_a", None, None), ("child_b", "output", None, None)],
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
            parent_edges=[("load", "child_a", None, None), ("child_b", "output", None, None)],
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
            parent_edges=[("load", "child_a", None, None)],
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


class TestMergeSubmodelsCrossBoundaryEdges:
    """Reconstruction of cross-boundary parent edges in ``merge_submodels``.

    ``_build_edges`` drops edges whose endpoints reference a submodel child
    (it only knows about main-file nodes), so ``merge_submodels`` rebuilds
    those edges from the raw ``parent_edges`` tuples. These tests pin down
    port handling and the de-duplication / membership guards.
    """

    def test_source_port_without_target_cross_edge_reconstructed(self) -> None:
        """A source-port-only cross-boundary edge is kept.

        The edge carries only a source port (no target port). The edge
        ``load -> child_a`` would otherwise
        be dropped; it must be reconstructed and then rewired to the
        submodel placeholder with an ``in__child_a`` target handle.
        """
        parent = _make_parent_graph()
        child = _make_child_graph()
        result = merge_submodels(
            parent,
            {"sub": child},
            {"sub": "modules/sub.py"},
            # Canonical four-tuple: source port with no target port.
            parent_edges=[
                ("load", "child_a", "some_port", None),
                ("child_b", "output", None, None),
            ],
            flatten=False,
        )
        # The cross-boundary edge survived reconstruction and was rewired to
        # the submodel placeholder via the in__ handle.
        boundary = [
            e
            for e in result.edges
            if e.source == "load"
            and e.target == "submodel__sub"
            and e.targetHandle == "in__child_a"
        ]
        assert len(boundary) == 1, "cross-boundary edge should be reconstructed and rewired"

    def test_four_tuple_source_and_target_port_cross_edge_reconstructed(self) -> None:
        """A 4-tuple ``(src, tgt, source_port, target_port)`` is reconstructed.

        This is the current port-aware codegen shape. The cross-boundary
        ``load -> child_a`` edge must survive and be rewired to the
        submodel placeholder (the ``in__`` target handle replaces the raw
        boundary target port).
        """
        parent = _make_parent_graph()
        child = _make_child_graph()
        result = merge_submodels(
            parent,
            {"sub": child},
            {"sub": "modules/sub.py"},
            # 4-tuple: both a source port and a target port.
            parent_edges=[
                ("load", "child_a", "src_port", "tgt_port"),
                ("child_b", "output", None, None),
            ],
            flatten=False,
        )
        boundary = [
            e
            for e in result.edges
            if e.source == "load"
            and e.target == "submodel__sub"
            and e.targetHandle == "in__child_a"
        ]
        assert len(boundary) == 1

    def test_handle_distinct_cross_edges_are_not_deduplicated_by_endpoint_pair(
        self,
    ) -> None:
        parent = _make_parent_graph()
        child = _make_child_graph()
        result = merge_submodels(
            parent,
            {"sub": child},
            {"sub": "modules/sub.py"},
            parent_edges=[
                ("load", "child_a", "quotes", None),
                ("load", "child_a", "claims", None),
                ("child_b", "output", None, "primary"),
                ("child_b", "output", None, "audit"),
            ],
            flatten=False,
        )

        inbound = [
            edge
            for edge in result.edges
            if edge.source == "load" and edge.target == "submodel__sub"
        ]
        outbound = [
            edge
            for edge in result.edges
            if edge.source == "submodel__sub" and edge.target == "output"
        ]

        assert {edge.sourceHandle for edge in inbound} == {"quotes", "claims"}
        assert {edge.targetHandle for edge in outbound} == {"primary", "audit"}
        assert len({edge.id for edge in [*inbound, *outbound]}) == 4

        flattened = merge_submodels(
            parent,
            {"sub": child},
            {"sub": "modules/sub.py"},
            parent_edges=[
                ("load", "child_a", "quotes", None),
                ("load", "child_a", "claims", None),
                ("child_b", "output", None, "primary"),
                ("child_b", "output", None, "audit"),
            ],
            flatten=True,
        )
        flat_boundary = [
            edge
            for edge in flattened.edges
            if (edge.source, edge.target) in {("load", "child_a"), ("child_b", "output")}
        ]

        assert len(flat_boundary) == 4
        assert len({edge.id for edge in flat_boundary}) == 4

    def test_output_side_source_port_reconstructed(self) -> None:
        """A source-port-only edge whose source is a child is reconstructed.

        Covers the output-port side: ``child_b -> output`` arrives as a
        source-port-only tuple and must become an ``out__child_b`` edge from the
        placeholder.
        """
        parent = _make_parent_graph()
        child = _make_child_graph()
        result = merge_submodels(
            parent,
            {"sub": child},
            {"sub": "modules/sub.py"},
            parent_edges=[("load", "child_a", None, None), ("child_b", "output", "result", None)],
            flatten=False,
        )
        out_edges = [
            e
            for e in result.edges
            if e.source == "submodel__sub"
            and e.target == "output"
            and e.sourceHandle == "out__child_b"
        ]
        assert len(out_edges) == 1

    def test_duplicate_cross_edge_pair_not_appended_twice(self) -> None:
        """A ``(src, tgt)`` pair already present is not reconstructed again.

        Here ``load -> child_a`` already exists as a parent graph edge, and
        is *also* listed in ``parent_edges``. The reconstruction must skip
        it (the ``if (src, tgt) in existing_pairs: continue`` guard) so the
        merged graph contains exactly one edge into ``child_a``.
        """
        n1 = GraphNode(
            id="load",
            data=NodeData(label="load", nodeType="dataInput", config={"path": "data.csv"}),
        )
        n2 = GraphNode(
            id="output",
            data=NodeData(label="output", nodeType="output", config={}),
        )
        # The parent graph ALREADY carries the load -> child_a edge.
        e_main = GraphEdge(id="e_load_output", source="load", target="output")
        e_pre = GraphEdge(id="e_load_child_a", source="load", target="child_a")
        parent = PipelineGraph(nodes=[n1, n2], edges=[e_main, e_pre], pipeline_name="main")
        child = _make_child_graph()

        result = merge_submodels(
            parent,
            {"sub": child},
            {"sub": "modules/sub.py"},
            # Same pair appears in parent_edges -> must be de-duplicated.
            parent_edges=[("load", "child_a", None, None), ("child_b", "output", None, None)],
            flatten=False,
        )
        into_child_a = [
            e
            for e in result.edges
            if e.target == "submodel__sub" and e.targetHandle == "in__child_a"
        ]
        assert len(into_child_a) == 1, "duplicate (src, tgt) pair must not be re-added"

    def test_parent_edge_between_non_child_nodes_left_untouched(self) -> None:
        """A parent edge whose endpoints are both non-child nodes is ignored.

        The reconstruction loop only rebuilds edges touching a submodel
        child (``if src in all_child_ids or tgt in all_child_ids``). An edge
        between two ordinary parent nodes must neither be re-appended nor
        rewired — it should pass straight through to the merged graph.
        """
        n1 = GraphNode(
            id="load",
            data=NodeData(label="load", nodeType="dataInput", config={"path": "data.csv"}),
        )
        n2 = GraphNode(
            id="output",
            data=NodeData(label="output", nodeType="output", config={}),
        )
        other = GraphNode(
            id="other",
            data=NodeData(label="other", nodeType="polars", config={}),
        )
        e_main = GraphEdge(id="e_load_output", source="load", target="output")
        parent = PipelineGraph(nodes=[n1, n2, other], edges=[e_main], pipeline_name="main")
        child = _make_child_graph()

        result = merge_submodels(
            parent,
            {"sub": child},
            {"sub": "modules/sub.py"},
            # (load, other): neither endpoint is a submodel child.
            parent_edges=[
                ("load", "child_a", None, None),
                ("load", "other", None, None),
                ("child_b", "output", None, None),
            ],
            flatten=False,
        )
        edge_pairs = [(e.source, e.target) for e in result.edges]
        # The non-child edge is NOT present (it was never an existing parent
        # edge and the reconstruction loop skipped it); only edges that
        # existed or touch a child are emitted.
        assert ("load", "other") not in edge_pairs
        # Sanity: the child-touching edges WERE reconstructed/rewired.
        assert ("load", "submodel__sub") in edge_pairs
        assert ("submodel__sub", "output") in edge_pairs

    def test_hierarchical_always_populates_submodels_meta(self) -> None:
        """With a non-empty submodel set, ``submodels`` metadata is always set.

        The early-return guard means the placeholder loop always runs and
        populates the metadata, so the merged hierarchical graph must expose
        a ``submodels`` mapping (no silently-empty case).
        """
        parent = _make_parent_graph()
        child = _make_child_graph()
        result = merge_submodels(
            parent,
            {"sub": child},
            {"sub": "modules/sub.py"},
            parent_edges=[("load", "child_a", None, None)],
            flatten=False,
        )
        assert result.submodels is not None
        assert set(result.submodels) == {"sub"}
