"""Tests for haute.graph_utils - shared graph utilities."""

from __future__ import annotations

import polars as pl
import pytest

from haute.graph_utils import GraphNode, NodeData, PipelineGraph
from haute.graph_utils import (
    _execute_lazy,
    _object_cache,
    _prepare_graph,
    _resolve_sink_path,
    _sanitize_func_name,
    ancestors,
    load_external_object,
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

    def test_unicode_stripped(self):
        """Non-ASCII chars are stripped to stay in sync with the frontend."""
        assert _sanitize_func_name("café") == "caf"

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
# _resolve_sink_path
# ---------------------------------------------------------------------------


class TestResolveSinkPath:
    def test_bare_name_gets_outputs_dir_and_extension(self):
        assert _resolve_sink_path("modelling-data", "parquet") == "outputs/modelling-data.parquet"

    def test_bare_name_csv(self):
        assert _resolve_sink_path("results", "csv") == "outputs/results.csv"

    def test_already_has_directory_no_prepend(self):
        assert _resolve_sink_path("my-dir/data", "parquet") == "my-dir/data.parquet"

    def test_already_has_extension_no_append(self):
        assert (
            _resolve_sink_path("modelling-data.parquet", "parquet")
            == "outputs/modelling-data.parquet"
        )

    def test_full_path_unchanged(self):
        assert _resolve_sink_path("my-dir/data.parquet", "parquet") == "my-dir/data.parquet"

    def test_wrong_extension_gets_appended(self):
        assert _resolve_sink_path("data.csv", "parquet") == "outputs/data.csv.parquet"


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

    def test_no_edges_returns_sorted(self):
        result = topo_sort_ids(["c", "a", "b"], [])
        assert result == ["a", "b", "c"]

    def test_deterministic_ordering(self):
        """With equal in-degree, nodes should be sorted alphabetically."""
        ids = ["c", "b", "a"]
        edges = [_e("a", "c"), _e("b", "c")]
        result = topo_sort_ids(ids, edges)
        assert result == ["a", "b", "c"]
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
        """Edges referencing non-existent IDs should be ignored."""
        ids = ["a", "b"]
        edges = [_e("a", "b"), _e("x", "y")]
        result = topo_sort_ids(ids, edges)
        assert set(result) == {"a", "b"}

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


# ---------------------------------------------------------------------------
# _prepare_graph
# ---------------------------------------------------------------------------


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


class TestPrepareGraph:
    def test_returns_all_nodes_without_target(self):
        g = _make_graph([("a", "A"), ("b", "B")], [("a", "b")])
        node_map, order, parents, names = _prepare_graph(g)
        assert set(order) == {"a", "b"}

    def test_filters_to_ancestors_with_target(self):
        g = _make_graph(
            [("a", "A"), ("b", "B"), ("c", "C")],
            [("a", "b")],
        )
        _, order, _, _ = _prepare_graph(g, target_node_id="b")
        assert "c" not in order
        assert set(order) == {"a", "b"}

    def test_parents_of_correct(self):
        g = _make_graph(
            [("a", "A"), ("b", "B"), ("c", "C")],
            [("a", "c"), ("b", "c")],
        )
        _, _, parents, _ = _prepare_graph(g)
        assert set(parents["c"]) == {"a", "b"}
        assert parents["a"] == []


# ---------------------------------------------------------------------------
# _execute_lazy
# ---------------------------------------------------------------------------


class TestExecuteLazy:
    @staticmethod
    def _simple_build_fn(node, source_names=None, **kwargs):
        """Minimal build_node_fn for testing."""
        nid = node.id
        nt = node.data.nodeType
        name = node.data.label or nid

        if nt == "dataSource":

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
                GraphNode(id="src", data=NodeData(label="Source", nodeType="dataSource")),
                GraphNode(id="t", data=NodeData(label="Transform", nodeType="polars")),
            ],
            edges=g.edges,
        )

        outputs, order, _, _ = _execute_lazy(g, self._simple_build_fn)
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
                GraphNode(id="a", data=NodeData(label="A", nodeType="dataSource")),
                GraphNode(id="b", data=NodeData(label="B", nodeType="polars")),
                GraphNode(id="c", data=NodeData(label="C", nodeType="polars")),
            ],
            edges=g.edges,
        )

        outputs, order, _, _ = _execute_lazy(g, self._simple_build_fn, target_node_id="b")
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
                GraphNode(id="src", data=NodeData(label="Src", nodeType="dataSource")),
                GraphNode(id="t", data=NodeData(label="T", nodeType="polars")),
            ],
            edges=[_e("src", "t")],
        )

        outputs, _, _, _ = _execute_lazy(g, build_fn)
        assert isinstance(outputs["t"], pl.LazyFrame)

    def test_non_source_no_input_raises(self):
        """A non-source node with no parents and no prior outputs raises ValueError."""

        def build_fn(node, source_names=None, **kwargs):
            return node.id, lambda *dfs: dfs[0], False

        g = _make_graph([("lonely", "Lonely")], [])
        with pytest.raises(ValueError, match="No input data available"):
            _execute_lazy(g, build_fn)

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
                GraphNode(id="src", data=NodeData(label="Src", nodeType="dataSource")),
                GraphNode(id="t", data=NodeData(label="T", nodeType="polars")),
            ],
            edges=[],
        )

        with pytest.raises(ValueError, match="No input data available"):
            _execute_lazy(g, build_fn)


@pytest.mark.usefixtures("_widen_sandbox_root")
class TestLoadExternalObjectCache:
    @pytest.fixture(autouse=True)
    def _clear_object_cache(self):
        _object_cache.clear()
        yield
        _object_cache.clear()

    def test_json_cached_on_second_call(self, tmp_path):
        p = tmp_path / "data.json"
        p.write_text('{"a": 1}')

        obj1 = load_external_object(str(p), "json")
        obj2 = load_external_object(str(p), "json")
        assert obj1 is obj2
        assert obj1 == {"a": 1}

    def test_mtime_change_invalidates_cache(self, tmp_path):
        import os
        import time as _time

        p = tmp_path / "data.json"
        p.write_text('{"v": 1}')

        obj1 = load_external_object(str(p), "json")
        assert obj1 == {"v": 1}

        p.write_text('{"v": 2}')
        # Force mtime 2 seconds in the future to avoid filesystem granularity issues
        future = _time.time() + 2
        os.utime(str(p), (future, future))

        obj2 = load_external_object(str(p), "json")
        assert obj2 == {"v": 2}
        assert obj1 is not obj2

    def test_pickle_cached(self, tmp_path):
        import pickle

        p = tmp_path / "obj.pkl"
        with open(p, "wb") as f:
            pickle.dump([1, 2, 3], f)

        obj1 = load_external_object(str(p), "pickle")
        obj2 = load_external_object(str(p), "pickle")
        assert obj1 is obj2
        assert obj1 == [1, 2, 3]


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
                GraphNode(id="a", data=NodeData(label="A", nodeType="dataSource")),
                GraphNode(id="b", data=NodeData(label="B", nodeType="dataSource")),
                GraphNode(id="c", data=NodeData(label="C", nodeType="polars")),
            ],
            edges=[_e("a", "c"), _e("b", "c")],
        )

        outputs, _, _, _ = _execute_lazy(g, build_fn)
        df = outputs["c"].collect()
        assert set(df.columns) == {"x", "y"}


# ---------------------------------------------------------------------------
# build_instance_mapping
# ---------------------------------------------------------------------------


class TestBuildInstanceMapping:
    def test_exact_match(self):
        from haute.graph_utils import build_instance_mapping

        result = build_instance_mapping(["a", "b"], ["b", "a"])
        assert result == {"a": "a", "b": "b"}

    def test_substring_match(self):
        from haute.graph_utils import build_instance_mapping

        result = build_instance_mapping(
            ["claims_aggregate"],
            ["claims_aggregate_instance"],
        )
        assert result == {"claims_aggregate": "claims_aggregate_instance"}

    def test_positional_fallback(self):
        """Regression: instance input named 'instance' must map to 'claims_aggregate'
        via positional fallback when no exact or substring match exists."""
        from haute.graph_utils import build_instance_mapping

        result = build_instance_mapping(
            ["policies", "exposure", "claims_aggregate"],
            ["exposure", "policies", "instance"],
        )
        assert result["policies"] == "policies"
        assert result["exposure"] == "exposure"
        assert result["claims_aggregate"] == "instance"

    def test_explicit_mapping_overrides_heuristic(self):
        from haute.graph_utils import build_instance_mapping

        result = build_instance_mapping(
            ["a", "b"],
            ["x", "y"],
            explicit={"a": "y", "b": "x"},
        )
        assert result == {"a": "y", "b": "x"}

    def test_explicit_mapping_filters_empty_values(self):
        from haute.graph_utils import build_instance_mapping

        result = build_instance_mapping(
            ["a", "b"],
            ["a", "b"],
            explicit={"a": "", "b": "b"},
        )
        assert result["a"] == "a"
        assert result["b"] == "b"


# ---------------------------------------------------------------------------
# resolve_orig_source_names
# ---------------------------------------------------------------------------


class TestResolveOrigSourceNames:
    def test_non_instance_returns_none(self):
        from haute.graph_utils import resolve_orig_source_names

        node = GraphNode(id="x", data=NodeData(label="x"))
        assert resolve_orig_source_names(node, {}, {}, {}) is None

    def test_resolves_from_full_edges(self):
        """Regression: original's parents must be resolved even when they
        are outside the execution subgraph (target_node_id filtering)."""
        from haute.graph_utils import resolve_orig_source_names

        node_map = {
            "freq_set": GraphNode(id="freq_set", data=NodeData(label="freq_set")),
            "policies": GraphNode(id="policies", data=NodeData(label="policies")),
            "claims_agg": GraphNode(id="claims_agg", data=NodeData(label="claims_agg")),
            "inst": GraphNode(
                id="inst", data=NodeData(label="inst", config={"instanceOf": "freq_set"})
            ),
        }
        all_parents = {"freq_set": ["policies", "claims_agg"]}
        # id_to_name only has nodes in the execution subgraph (inst's ancestors)
        id_to_name = {"policies": "policies", "inst": "inst"}

        result = resolve_orig_source_names(node_map["inst"], node_map, all_parents, id_to_name)
        assert result == ["policies", "claims_agg"]
