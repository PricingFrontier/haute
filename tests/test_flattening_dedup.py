"""TDD gate for CODEBASE_REVIEW #56 — flattening duplication.

``_parser_submodels.merge_submodels(flatten=True)`` (lines 135-141)
implements its own child-node inlining loop that overlaps with the
authoritative flattener in ``_flatten.flatten_graph``.  Two flattening
paths can — and will — drift.

The fix is to route the parser's flatten path through
``_flatten.flatten_graph``: build the hierarchical form first
(submodel placeholder nodes + ``submodels`` meta), then dissolve it
via the shared flattener.

Tests below pin:

1. **Structural equivalence** — for 3 representative submodel shapes,
   ``merge_submodels(..., flatten=True)`` yields the same set of final
   nodes and edges as ``flatten_graph(merge_submodels(..., flatten=False))``.
2. **No duplicate flattening loop** — the inline loop at the original
   line 135 is gone; the source now references ``flatten_graph``.
3. **End-to-end regression** — a real parse → flatten round-trip over
   a nested submodel pipeline still produces the expected flat graph.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from haute import _parser_submodels
from haute._flatten import flatten_graph
from haute._parser_submodels import merge_submodels
from haute._types import GraphEdge, GraphNode, NodeData, PipelineGraph
from haute.parser import parse_pipeline_file

# ---------------------------------------------------------------------------
# Helpers — build parent + child graphs
# ---------------------------------------------------------------------------


def _node(nid: str, ntype: str = "polars", config: dict | None = None) -> GraphNode:
    return GraphNode(
        id=nid,
        data=NodeData(label=nid, nodeType=ntype, config=config or {}),
    )


def _edge(src: str, tgt: str) -> GraphEdge:
    return GraphEdge(id=f"e_{src}_{tgt}", source=src, target=tgt)


def _simple_parent() -> PipelineGraph:
    """Parent: data_src → output (with submodel in the middle)."""
    return PipelineGraph(
        nodes=[
            _node("data_src", ntype="dataSource", config={"path": "d.csv"}),
            _node("output", ntype="output"),
        ],
        edges=[],
        pipeline_name="main",
    )


def _two_node_child() -> PipelineGraph:
    """Submodel with two internal nodes connected in sequence."""
    return PipelineGraph(
        nodes=[_node("child_a"), _node("child_b")],
        edges=[_edge("child_a", "child_b")],
        pipeline_name="scoring",
    )


def _single_node_child() -> PipelineGraph:
    """Submodel with exactly one internal node."""
    return PipelineGraph(
        nodes=[_node("lone")],
        edges=[],
        pipeline_name="solo",
    )


def _chained_child() -> PipelineGraph:
    """Submodel with three internal nodes in sequence (a → b → c)."""
    return PipelineGraph(
        nodes=[_node("x"), _node("y"), _node("z")],
        edges=[_edge("x", "y"), _edge("y", "z")],
        pipeline_name="chain",
    )


def _canonical_edges(graph: PipelineGraph) -> set[tuple[str, str]]:
    """Edge pairs ignoring handles — for cross-flatten equivalence."""
    return {(e.source, e.target) for e in graph.edges}


def _canonical_nodes(graph: PipelineGraph) -> set[str]:
    return {n.id for n in graph.nodes}


# ---------------------------------------------------------------------------
# Deduplication assertion — inline loop must be gone
# ---------------------------------------------------------------------------


class TestFlattenInlineLoopRemoved:
    """``merge_submodels`` must not re-implement the flattener inline."""

    def test_merge_submodels_source_calls_flatten_graph(self) -> None:
        """The parser-side flatten path must delegate to ``_flatten.flatten_graph``.

        This guards against re-introducing the duplicate inline loop.
        """
        src = Path(_parser_submodels.__file__).read_text(encoding="utf-8")
        assert "flatten_graph" in src, (
            "_parser_submodels must delegate to _flatten.flatten_graph rather "
            "than duplicate the flattening logic inline"
        )

    def test_no_inline_extend_loop_in_flatten_branch(self) -> None:
        """The historical inline loop (``parent_nodes.extend(sm_graph.nodes)``
        followed by ``parent_edge_list.extend(sm_graph.edges)``) at the original
        line 135 must no longer exist.  Either one alone is fine as part of
        some other routine, but the back-to-back pair is the fingerprint
        of the duplicated flattener.
        """
        src = Path(_parser_submodels.__file__).read_text(encoding="utf-8")
        has_node_extend = "parent_nodes.extend(sm_graph.nodes)" in src
        has_edge_extend = "parent_edge_list.extend(sm_graph.edges)" in src
        assert not (has_node_extend and has_edge_extend), (
            "The inline flattener at _parser_submodels.py:135 must be removed; "
            "route the flatten branch through _flatten.flatten_graph instead"
        )


# ---------------------------------------------------------------------------
# Structural equivalence across 3 representative submodel shapes
# ---------------------------------------------------------------------------


class TestStructuralEquivalence:
    """``merge_submodels(flatten=True)`` must produce the same final
    flat graph as ``flatten_graph(merge_submodels(flatten=False))``.

    Three submodel shapes cover: 2-node internal chain, single node,
    longer (3-node) internal chain.
    """

    def test_two_node_child_equivalent(self) -> None:
        parent = _simple_parent()
        child = _two_node_child()
        parent_edges = [("data_src", "child_a"), ("child_b", "output")]

        via_parser_flatten = merge_submodels(
            parent,
            {"scoring": child},
            {"scoring": "modules/scoring.py"},
            parent_edges=parent_edges,
            flatten=True,
        )
        hierarchical = merge_submodels(
            parent,
            {"scoring": child},
            {"scoring": "modules/scoring.py"},
            parent_edges=parent_edges,
            flatten=False,
        )
        via_flatten_graph = flatten_graph(hierarchical)

        assert _canonical_nodes(via_parser_flatten) == _canonical_nodes(via_flatten_graph)
        assert _canonical_edges(via_parser_flatten) == _canonical_edges(via_flatten_graph)

    def test_single_node_child_equivalent(self) -> None:
        parent = _simple_parent()
        child = _single_node_child()
        parent_edges = [("data_src", "lone"), ("lone", "output")]

        via_parser_flatten = merge_submodels(
            parent,
            {"solo": child},
            {"solo": "modules/solo.py"},
            parent_edges=parent_edges,
            flatten=True,
        )
        hierarchical = merge_submodels(
            parent,
            {"solo": child},
            {"solo": "modules/solo.py"},
            parent_edges=parent_edges,
            flatten=False,
        )
        via_flatten_graph = flatten_graph(hierarchical)

        assert _canonical_nodes(via_parser_flatten) == _canonical_nodes(via_flatten_graph)
        assert _canonical_edges(via_parser_flatten) == _canonical_edges(via_flatten_graph)

    def test_chained_child_equivalent(self) -> None:
        parent = _simple_parent()
        child = _chained_child()
        parent_edges = [("data_src", "x"), ("z", "output")]

        via_parser_flatten = merge_submodels(
            parent,
            {"chain": child},
            {"chain": "modules/chain.py"},
            parent_edges=parent_edges,
            flatten=True,
        )
        hierarchical = merge_submodels(
            parent,
            {"chain": child},
            {"chain": "modules/chain.py"},
            parent_edges=parent_edges,
            flatten=False,
        )
        via_flatten_graph = flatten_graph(hierarchical)

        assert _canonical_nodes(via_parser_flatten) == _canonical_nodes(via_flatten_graph)
        assert _canonical_edges(via_parser_flatten) == _canonical_edges(via_flatten_graph)


# ---------------------------------------------------------------------------
# End-to-end parse regression — a real pipeline still flattens correctly
# ---------------------------------------------------------------------------


def _write_file(tmp_path: Path, rel: str, code: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(code))
    return p


class TestParsePipelineFlattenRegression:
    """End-to-end: a parse → flatten round-trip over a nested submodel
    pipeline still produces the same flat graph after the refactor.

    This is the "no regression" assertion from item #56.
    """

    def test_nested_submodel_pipeline_flattens_child_nodes(self, tmp_path: Path) -> None:
        _write_file(
            tmp_path,
            "modules/scoring.py",
            """\
            import polars as pl
            import haute

            submodel = haute.Submodel("scoring")

            @submodel.polars
            def Transform(Source: pl.LazyFrame) -> pl.LazyFrame:
                return Source.select("x")

            @submodel.polars
            def Finalise(Transform: pl.LazyFrame) -> pl.LazyFrame:
                return Transform

            submodel.connect("Transform", "Finalise")
            """,
        )
        _write_file(
            tmp_path,
            "main.py",
            """\
            import polars as pl
            import haute

            pipeline = haute.Pipeline("root")

            @pipeline.data_source(path="data/in.parquet")
            def Source() -> pl.LazyFrame:
                return pl.scan_parquet("data/in.parquet")

            pipeline.submodel("modules/scoring.py")

            pipeline.connect("Source", "Transform")
            pipeline.connect("Finalise", "Source")
            """,
        )

        flat_graph = parse_pipeline_file(tmp_path / "main.py", flatten=True)

        node_ids = _canonical_nodes(flat_graph)
        # Placeholder is gone in flattened mode
        assert "submodel__scoring" not in node_ids
        # Child nodes are inlined
        assert "Transform" in node_ids
        assert "Finalise" in node_ids
        # Source node survives
        assert "Source" in node_ids
        # No leftover submodels metadata in flat graph
        assert not flat_graph.submodels

    def test_hierarchical_then_flatten_equals_direct_flatten(self, tmp_path: Path) -> None:
        """Parsing with ``flatten=False`` and then applying ``flatten_graph``
        must produce the same node set (and edge-pair set) as parsing with
        ``flatten=True`` directly.  Proves the parser's internal flatten
        path is consistent with the authoritative flattener.
        """
        _write_file(
            tmp_path,
            "modules/scoring.py",
            """\
            import polars as pl
            import haute

            submodel = haute.Submodel("scoring")

            @submodel.polars
            def Transform(Source: pl.LazyFrame) -> pl.LazyFrame:
                return Source.select("x")
            """,
        )
        _write_file(
            tmp_path,
            "main.py",
            """\
            import polars as pl
            import haute

            pipeline = haute.Pipeline("root")

            @pipeline.data_source(path="data/in.parquet")
            def Source() -> pl.LazyFrame:
                return pl.scan_parquet("data/in.parquet")

            pipeline.submodel("modules/scoring.py")

            pipeline.connect("Source", "Transform")
            """,
        )

        direct_flat = parse_pipeline_file(tmp_path / "main.py", flatten=True)
        hierarchical = parse_pipeline_file(tmp_path / "main.py", flatten=False)
        flattened_after = flatten_graph(hierarchical)

        assert _canonical_nodes(direct_flat) == _canonical_nodes(flattened_after)
        assert _canonical_edges(direct_flat) == _canonical_edges(flattened_after)
