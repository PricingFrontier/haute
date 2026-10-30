"""Tests for haute.graph_utils - shared graph utilities."""

from __future__ import annotations

import polars as pl
import pytest

from haute.execution import execute_lazy_graph
from haute.graph_utils import (
    GraphNode,
    NodeData,
    PipelineGraph,
    UnknownEdgeEndpointError,
    _sanitize_func_name,
    ancestors,
    topo_sort_ids,
)
from tests.conftest import make_edge as _e

# ---------------------------------------------------------------------------
# _sanitize_func_name
# ---------------------------------------------------------------------------


class TestSanitizeFuncName:
    def test_simple_label(self):
        assert _sanitize_func_name("Load Data") == "Load_Data"

    def test_hyphens_become_underscores(self):
        assert _sanitize_func_name("my-node") == "my_node"

    def test_strips_special_chars(self):
        assert _sanitize_func_name("node@#!1") == "node1"

    def test_leading_digit_gets_prefix(self):
        assert _sanitize_func_name("123abc") == "node_123abc"

    def test_empty_label_returns_unnamed(self):
        assert _sanitize_func_name("") == "unnamed_node"

    def test_whitespace_only_returns_unnamed(self):
        assert _sanitize_func_name("   ") == "unnamed_node"

    def test_preserves_case(self):
        assert _sanitize_func_name("MyNode") == "MyNode"

    def test_collisions_between_hyphens_and_underscores(self):
        """Different labels can produce the same sanitized name."""
        assert _sanitize_func_name("foo-bar") == _sanitize_func_name("foo_bar")

    def test_collisions_between_special_chars_and_plain(self):
        """Special characters are stripped, creating potential collisions."""
        assert _sanitize_func_name("foo@bar") == _sanitize_func_name("foobar")

    def test_unicode_encoded_reversibly(self):
        """Non-ASCII chars are reversibly encoded so distinct labels yield
        distinct identifiers (Wave 9D #123).  ``é`` (U+00E9) → ``_xe9_``.
        """
        assert _sanitize_func_name("café") == "caf_xe9_"
        # And the encoded form is distinct from the ASCII-stripped form.
        assert _sanitize_func_name("café") != _sanitize_func_name("caf")

    def test_all_special_chars_returns_unnamed(self):
        """Label of only special characters becomes unnamed_node."""
        assert _sanitize_func_name("@#$%") == "unnamed_node"

    def test_output_is_valid_python_identifier(self):
        """Sanitized name must always be a valid Python identifier."""
        labels = ["my node", "123", "foo-bar", "@!#", ""]
        for label in labels:
            name = _sanitize_func_name(label)
            assert name.isidentifier(), f"{label!r} -> {name!r} is not a valid identifier"


# ---------------------------------------------------------------------------
# topo_sort_ids
# ---------------------------------------------------------------------------


class TestTopoSort:
    def test_linear_chain(self):
        ids = ["a", "b", "c"]
        edges = [_e("a", "b"), _e("b", "c")]
        assert topo_sort_ids(ids, edges) == ["a", "b", "c"]

    def test_diamond(self):
        ids = ["a", "b", "c", "d"]
        edges = [_e("a", "b"), _e("a", "c"), _e("b", "d"), _e("c", "d")]
        result = topo_sort_ids(ids, edges)
        assert result[0] == "a"
        assert result[-1] == "d"
        assert set(result) == {"a", "b", "c", "d"}
        # Verify topological invariant
        idx = {nid: i for i, nid in enumerate(result)}
        for e in edges:
            assert idx[e.source] < idx[e.target]

    def test_single_node(self):
        assert topo_sort_ids(["x"], []) == ["x"]

    def test_no_edges_returns_insertion_order(self):
        """With no edges, ties break on insertion order (graphlib stdlib)."""
        result = topo_sort_ids(["c", "a", "b"], [])
        assert result == ["c", "a", "b"]

    def test_deterministic_ordering(self):
        """With equal in-degree, ties break on insertion order (graphlib)."""
        ids = ["c", "b", "a"]
        edges = [_e("a", "c"), _e("b", "c")]
        result = topo_sort_ids(ids, edges)
        # b was inserted before a in node_ids, so b precedes a among ties.
        assert result == ["b", "a", "c"]
        # Verify topological invariant: every parent before its child
        idx = {nid: i for i, nid in enumerate(result)}
        for e in edges:
            assert idx[e.source] < idx[e.target], f"{e.source} should come before {e.target}"

    def test_cycle_raises_error(self):
        """Cycle nodes raise CycleError instead of being silently dropped."""
        from haute._topo import CycleError

        ids = ["a", "b", "c"]
        edges = [_e("a", "b"), _e("b", "c"), _e("c", "a")]
        with pytest.raises(CycleError, match="Cycle detected"):
            topo_sort_ids(ids, edges)

    def test_edges_referencing_unknown_nodes(self):
        """The public default sorter fails loudly on unknown endpoints."""
        ids = ["a", "b"]
        edges = [_e("a", "b"), _e("x", "y")]
        with pytest.raises(UnknownEdgeEndpointError) as exc_info:
            topo_sort_ids(ids, edges)
        assert exc_info.value.unknown_node_ids == ("x", "y")

    def test_empty_input(self):
        assert topo_sort_ids([], []) == []


# ---------------------------------------------------------------------------
# ancestors
# ---------------------------------------------------------------------------


class TestAncestors:
    def test_includes_self(self):
        result = ancestors("a", [], {"a", "b"})
        assert "a" in result

    def test_finds_parents(self):
        edges = [_e("a", "b"), _e("b", "c")]
        result = ancestors("c", edges, {"a", "b", "c"})
        assert result == {"a", "b", "c"}

    def test_excludes_unrelated(self):
        edges = [_e("a", "b"), _e("x", "y")]
        result = ancestors("b", edges, {"a", "b", "x", "y"})
        assert result == {"a", "b"}


def _make_graph(
    nodes_data: list[tuple[str, str]], edges_data: list[tuple[str, str]]
) -> PipelineGraph:
    """Helper to build a minimal PipelineGraph."""
    nodes = [
        GraphNode(id=nid, data=NodeData(label=label, nodeType="polars"))
        for nid, label in nodes_data
    ]
    edges = [_e(s, t) for s, t in edges_data]
    return PipelineGraph(nodes=nodes, edges=edges)


# ---------------------------------------------------------------------------
# execute_lazy_graph
# ---------------------------------------------------------------------------


class TestExecuteLazy:
    @staticmethod
    def _simple_build_fn(node, source_names=None, **kwargs):
        """Minimal build_node_fn for testing."""
        nid = node.id
        nt = node.data.nodeType
        name = node.data.label or nid

        if nt == "dataInput":

            def fn() -> pl.LazyFrame:
                return pl.DataFrame({"x": [1, 2, 3]}).lazy()

            return name, fn, True
        else:

            def fn(*dfs: pl.LazyFrame) -> pl.LazyFrame:
                return dfs[0].with_columns(y=pl.col("x") * 2)

            return name, fn, False

    def test_basic_execution(self):
        g = _make_graph(
            [("src", "Source"), ("t", "Transform")],
            [("src", "t")],
        )
        g = PipelineGraph(
            nodes=[
                GraphNode(id="src", data=NodeData(label="Source", nodeType="dataInput")),
                GraphNode(id="t", data=NodeData(label="Transform", nodeType="polars")),
            ],
            edges=g.edges,
        )

        outputs, order, _, _ = execute_lazy_graph(g, self._simple_build_fn)
        assert "src" in outputs
        assert "t" in outputs
        df = outputs["t"].collect()
        assert "y" in df.columns
        assert df["y"].to_list() == [2, 4, 6]

    def test_target_filters_execution(self):
        g = _make_graph(
            [("a", "A"), ("b", "B"), ("c", "C")],
            [("a", "b"), ("b", "c")],
        )
        g = PipelineGraph(
            nodes=[
                GraphNode(id="a", data=NodeData(label="A", nodeType="dataInput")),
                GraphNode(id="b", data=NodeData(label="B", nodeType="polars")),
                GraphNode(id="c", data=NodeData(label="C", nodeType="polars")),
            ],
            edges=g.edges,
        )

        outputs, order, _, _ = execute_lazy_graph(g, self._simple_build_fn, target_node_id="b")
        assert "b" in outputs
        assert "c" not in outputs

    def test_dataframe_converted_to_lazy(self):
        """If a node fn returns a DataFrame, it should be auto-converted to LazyFrame."""

        def build_fn(node, source_names=None, **kwargs):
            if node.id == "src":
                return "src", lambda: pl.DataFrame({"x": [1]}), True
            return "t", lambda *dfs: dfs[0], False

        g = PipelineGraph(
            nodes=[
                GraphNode(id="src", data=NodeData(label="Src", nodeType="dataInput")),
                GraphNode(id="t", data=NodeData(label="T", nodeType="polars")),
            ],
            edges=[_e("src", "t")],
        )

        outputs, _, _, _ = execute_lazy_graph(g, build_fn)
        assert isinstance(outputs["t"], pl.LazyFrame)

    def test_non_source_no_input_raises(self):
        """A non-source node with no parents and no prior outputs raises ValueError."""

        def build_fn(node, source_names=None, **kwargs):
            return node.id, lambda *dfs: dfs[0], False

        g = _make_graph([("lonely", "Lonely")], [])
        with pytest.raises(ValueError, match="No input data available"):
            execute_lazy_graph(g, build_fn)

    def test_no_edge_non_source_raises(self):
        """A non-source with no edges raises even when prior outputs exist."""

        def build_fn(node, source_names=None, **kwargs):
            nid = node.id
            if nid == "src":
                return nid, lambda: pl.DataFrame({"x": [1]}).lazy(), True
            return nid, lambda *dfs: dfs[0], False

        # Two nodes, no edge — "t" must not silently grab src's output
        g = PipelineGraph(
            nodes=[
                GraphNode(id="src", data=NodeData(label="Src", nodeType="dataInput")),
                GraphNode(id="t", data=NodeData(label="T", nodeType="polars")),
            ],
            edges=[],
        )

        with pytest.raises(ValueError, match="No input data available"):
            execute_lazy_graph(g, build_fn)


class TestExecuteLazyMultiInput:
    def test_multi_input_node(self):
        """A node with two parents receives both LazyFrames."""

        def build_fn(node, source_names=None, **kwargs):
            nid = node.id
            if nid in ("a", "b"):
                data = {"x": [1]} if nid == "a" else {"y": [2]}
                return nid, lambda d=data: pl.DataFrame(d).lazy(), True
            else:

                def fn(*dfs):
                    return dfs[0].join(dfs[1], how="cross")

                return nid, fn, False

        g = PipelineGraph(
            nodes=[
                GraphNode(id="a", data=NodeData(label="A", nodeType="dataInput")),
                GraphNode(id="b", data=NodeData(label="B", nodeType="dataInput")),
                GraphNode(id="c", data=NodeData(label="C", nodeType="polars")),
            ],
            edges=[_e("a", "c"), _e("b", "c")],
        )

        outputs, _, _, _ = execute_lazy_graph(g, build_fn)
        df = outputs["c"].collect()
        assert set(df.columns) == {"x", "y"}


# ---------------------------------------------------------------------------
# resolve_orig_source_names
# ---------------------------------------------------------------------------


class TestResolveOrigSourceNames:
    def test_non_instance_returns_none(self):
        from haute.graph_utils import resolve_orig_source_names

        node = GraphNode(id="x", data=NodeData(label="x"))
        assert resolve_orig_source_names(node, {}, {}) is None

    def test_resolves_non_instance_polars_logical_names_in_edge_order(self):
        from haute.graph_utils import resolve_orig_source_names

        node_map = {
            "joined": GraphNode(id="joined", data=NodeData(label="Edge Join 1")),
            "other": GraphNode(id="other", data=NodeData(label="other")),
            "target": GraphNode(
                id="target",
                data=NodeData(
                    label="target",
                    nodeType="polars",
                    config={"inputMapping": {"raw_rows": "Edge_Join_1"}},
                ),
            ),
        }
        incoming_edges = {
            "target": [_e("joined", "target"), _e("other", "target")],
        }

        assert resolve_orig_source_names(node_map["target"], node_map, incoming_edges) == [
            "raw_rows",
            "other",
        ]

    @pytest.mark.parametrize(
        "mapping",
        [
            {"raw_rows": "missing"},
            {"raw_rows": "Edge_Join_1", "also_raw": "Edge_Join_1"},
            {"not valid": "Edge_Join_1"},
        ],
    )
    def test_rejects_invalid_non_instance_polars_mapping(self, mapping):
        from haute.errors import ConfigError
        from haute.graph_utils import resolve_orig_source_names

        node_map = {
            "joined": GraphNode(id="joined", data=NodeData(label="Edge Join 1")),
            "target": GraphNode(
                id="target",
                data=NodeData(
                    label="target",
                    nodeType="polars",
                    config={"inputMapping": mapping},
                ),
            ),
        }

        with pytest.raises(ConfigError, match="inputMapping"):
            resolve_orig_source_names(
                node_map["target"],
                node_map,
                {"target": [_e("joined", "target")]},
            )


class TestExecutableInputNameSubmodelOccurrence:
    def test_output_is_named_by_its_port_whatever_the_alias(self):
        from haute._graph_utils import executable_input_name
        from haute._types import NodeType

        name = executable_input_name(
            node_type=NodeType.SUBMODEL,
            label="pricing",
            source_handle="out__quotes",
        )
        assert name == "quotes"

    def test_every_port_of_a_multi_output_occurrence_keeps_its_own_name(self):
        from haute._graph_utils import executable_input_name
        from haute._types import NodeType

        first = executable_input_name(
            node_type=NodeType.SUBMODEL,
            label="pricing",
            source_handle="out__written_premium",
        )
        second = executable_input_name(
            node_type=NodeType.SUBMODEL,
            label="pricing",
            source_handle="out__loss_ratio",
        )
        assert first == "written_premium"
        assert second == "loss_ratio"

    def test_port_name_is_sanitised(self):
        from haute._graph_utils import _sanitize_func_name, executable_input_name
        from haute._types import NodeType

        name = executable_input_name(
            node_type=NodeType.SUBMODEL,
            label="pricing",
            source_handle="out__written premium",
        )
        assert name == _sanitize_func_name("written premium")

    def test_handle_must_use_the_out_prefix(self):
        from haute._graph_utils import executable_input_name
        from haute._types import NodeType

        with pytest.raises(ValueError, match="out__<name>"):
            executable_input_name(
                node_type=NodeType.SUBMODEL,
                label="Pricing",
                source_handle="quotes",
            )

    def test_ordinary_node_named_like_port_label_is_unaffected(self):
        from haute._graph_utils import executable_input_name
        from haute._types import NodeType

        name = executable_input_name(
            node_type=NodeType.POLARS,
            label="Quotes Frame",
            source_handle=None,
        )
        assert name == "Quotes_Frame"
