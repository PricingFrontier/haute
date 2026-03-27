"""Tests for haute.pipeline - Pipeline and Node classes."""

from __future__ import annotations

import pytest
import polars as pl

from haute._model_scorer import _scenario_ctx
from haute.pipeline import Node, Pipeline


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------

class TestNode:
    def test_source_node_call(self):
        def src() -> pl.DataFrame:
            return pl.DataFrame({"x": [1]})

        n = Node(name="src", description="", fn=src, is_source=True)
        assert n.n_inputs == 0
        df = n()
        assert df["x"].to_list() == [1]

    def test_transform_node_call(self):
        def t(df: pl.DataFrame) -> pl.DataFrame:
            return df.with_columns(y=pl.col("x") + 1)

        n = Node(name="t", description="", fn=t, is_source=False)
        assert n.n_inputs == 1
        df = n(pl.DataFrame({"x": [10]}))
        assert df["y"].to_list() == [11]

    def test_multi_input_node(self):
        def join(a: pl.DataFrame, b: pl.DataFrame) -> pl.DataFrame:
            return a.hstack(b)

        n = Node(name="join", description="", fn=join, is_source=False)
        assert n.n_inputs == 2
        df = n(pl.DataFrame({"x": [1]}), pl.DataFrame({"y": [2]}))
        assert set(df.columns) == {"x", "y"}

    def test_transform_no_input_raises(self):
        n = Node(name="t", description="", fn=lambda df: df, is_source=False)
        with pytest.raises(ValueError, match="expects.*input.*received none"):
            n()


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class TestPipeline:
    def _simple_pipeline(self) -> Pipeline:
        p = Pipeline("test", description="test pipeline")

        @p.data_source
        def source() -> pl.DataFrame:
            return pl.DataFrame({"x": [1, 2, 3]})

        @p.polars
        def transform(source: pl.DataFrame) -> pl.DataFrame:
            return source.with_columns(y=pl.col("x") * 2)

        p.connect("source", "transform")
        return p

    def test_run(self):
        p = self._simple_pipeline()
        result = p.run()
        assert "y" in result.columns
        assert result["y"].to_list() == [2, 4, 6]

    def test_score(self):
        p = self._simple_pipeline()
        custom_df = pl.DataFrame({"x": [10, 20]})
        result = p.score(custom_df)
        assert result["y"].to_list() == [20, 40]

    def test_nodes_property(self):
        p = self._simple_pipeline()
        assert len(p.nodes) == 2
        assert p.nodes[0].name == "source"

    def test_edges_property(self):
        p = self._simple_pipeline()
        assert p.edges == [("source", "transform")]

    def test_connect_chaining(self):
        p = Pipeline("chain")

        @p.data_source
        def a() -> pl.DataFrame:
            return pl.DataFrame({"x": [1]})

        @p.polars
        def b(a: pl.DataFrame) -> pl.DataFrame:
            return a

        @p.polars
        def c(b: pl.DataFrame) -> pl.DataFrame:
            return b

        p.connect("a", "b").connect("b", "c")
        assert len(p.edges) == 2

    def test_node_decorator_with_config(self):
        p = Pipeline("cfg")

        @p.data_source(path="data.parquet")
        def read_data() -> pl.DataFrame:
            return pl.DataFrame({"x": [1]})

        from haute._types import NodeType
        assert p.nodes[0].config == {"path": "data.parquet", "_node_type": NodeType.DATA_SOURCE}
        assert p.nodes[0].is_source is True

    def test_empty_pipeline_raises(self):
        p = Pipeline("empty")
        with pytest.raises(ValueError, match="no nodes"):
            p.run()

    def test_topo_order_delegates_to_graph_utils(self):
        p = Pipeline("topo")

        @p.data_source
        def a() -> pl.DataFrame:
            return pl.DataFrame({"x": [1]})

        @p.polars
        def b(a: pl.DataFrame) -> pl.DataFrame:
            return a

        p.connect("a", "b")
        order = p._topo_order()
        assert [n.name for n in order] == ["a", "b"]

    def test_no_edges_falls_back_to_registration_order(self):
        p = Pipeline("no_edges")

        @p.data_source
        def first() -> pl.DataFrame:
            return pl.DataFrame({"x": [1]})

        @p.polars
        def second(df: pl.DataFrame) -> pl.DataFrame:
            return df

        order = p._topo_order()
        assert [n.name for n in order] == ["first", "second"]

    def test_topo_order_cycle_raises(self):
        """Cycle detection should raise CycleError."""
        from haute._topo import CycleError

        p = Pipeline("cycle")

        @p.data_source
        def a() -> pl.DataFrame:
            return pl.DataFrame({"x": [1]})

        @p.polars
        def b(a: pl.DataFrame) -> pl.DataFrame:
            return a

        p.connect("a", "b").connect("b", "a")
        with pytest.raises(CycleError, match="Cycle detected"):
            p._topo_order()

    def test_run_no_edges_raises(self):
        """Without explicit edges, run() raises rather than silently chaining."""
        p = Pipeline("implicit")

        @p.data_source
        def source() -> pl.DataFrame:
            return pl.DataFrame({"x": [1, 2]})

        @p.polars
        def transform(df: pl.DataFrame) -> pl.DataFrame:
            return df.with_columns(y=pl.col("x") * 3)

        # No connect() calls — must raise, not silently use wrong data
        with pytest.raises(ValueError, match="no inbound edges"):
            p.run()

    def test_score_no_edges_raises(self):
        """score() without edges raises rather than silently chaining."""
        p = Pipeline("score_implicit")

        @p.data_source
        def source() -> pl.DataFrame:
            return pl.DataFrame({"x": [0]})

        @p.polars
        def transform(df: pl.DataFrame) -> pl.DataFrame:
            return df.with_columns(y=pl.col("x") + 100)

        custom = pl.DataFrame({"x": [5]})
        with pytest.raises(ValueError, match="no inbound edges"):
            p.score(custom)

    def test_to_graph_with_explicit_edges(self):
        p = self._simple_pipeline()
        g = p.to_graph()
        assert len(g["nodes"]) == 2
        assert len(g["edges"]) == 1
        assert g["edges"][0]["source"] == "source"
        assert g["edges"][0]["target"] == "transform"
        # Verify node types
        node_map = {n["id"]: n for n in g["nodes"]}
        assert node_map["source"]["data"]["nodeType"] == "dataSource"
        assert node_map["transform"]["data"]["nodeType"] == "polars"

    def test_to_graph_inferred_linear_chain(self):
        """Without explicit edges, to_graph() infers a linear chain."""
        p = Pipeline("chain")

        @p.data_source
        def a() -> pl.DataFrame:
            return pl.DataFrame()

        @p.polars
        def b(df: pl.DataFrame) -> pl.DataFrame:
            return df

        @p.polars
        def c(df: pl.DataFrame) -> pl.DataFrame:
            return df

        g = p.to_graph()
        assert len(g["edges"]) == 2
        edge_pairs = [(e["source"], e["target"]) for e in g["edges"]]
        assert ("a", "b") in edge_pairs
        assert ("b", "c") in edge_pairs

    def test_run_sets_scenario_ctx_to_batch(self):
        """Pipeline.run() must set _scenario_ctx to 'batch' during execution."""
        captured: list[str] = []
        p = Pipeline("ctx_batch")

        @p.data_source
        def source() -> pl.DataFrame:
            captured.append(_scenario_ctx.get())
            return pl.DataFrame({"x": [1]})

        @p.polars
        def transform(df: pl.DataFrame) -> pl.DataFrame:
            captured.append(_scenario_ctx.get())
            return df

        p.connect("source", "transform")
        p.run()
        assert captured == ["batch", "batch"]
        # Context must be reset after run() completes
        assert _scenario_ctx.get() == "batch"  # default

    def test_score_sets_scenario_ctx_to_live(self):
        """Pipeline.score() must set _scenario_ctx to 'live' during execution.

        Note: score() seeds source nodes directly with the input df —
        the source function is NOT called — so we capture from transforms only.
        """
        captured: list[str] = []
        p = Pipeline("ctx_live")

        @p.data_source
        def source() -> pl.DataFrame:
            return pl.DataFrame({"x": [1]})

        @p.polars
        def transform(df: pl.DataFrame) -> pl.DataFrame:
            captured.append(_scenario_ctx.get())
            return df

        p.connect("source", "transform")
        p.score(pl.DataFrame({"x": [5]}))
        assert captured == ["live"]
        # Context must be reset after score() completes
        assert _scenario_ctx.get() == "batch"  # default

    def test_scenario_ctx_reset_on_error(self):
        """_scenario_ctx must be reset even if a node raises."""
        p = Pipeline("ctx_err")

        @p.data_source
        def source() -> pl.DataFrame:
            return pl.DataFrame({"x": [1]})

        @p.polars
        def boom(df: pl.DataFrame) -> pl.DataFrame:
            raise RuntimeError("kaboom")

        p.connect("source", "boom")
        with pytest.raises(RuntimeError, match="kaboom"):
            p.run()
        assert _scenario_ctx.get() == "batch"  # reset despite error

    def test_to_graph_positions_spaced(self):
        """Nodes should be positioned with x_spacing."""
        p = Pipeline("pos")

        @p.data_source
        def a() -> pl.DataFrame:
            return pl.DataFrame()

        @p.polars
        def b(df: pl.DataFrame) -> pl.DataFrame:
            return df

        g = p.to_graph()
        assert g["nodes"][0]["position"]["x"] == 0
        assert g["nodes"][1]["position"]["x"] > g["nodes"][0]["position"]["x"]
