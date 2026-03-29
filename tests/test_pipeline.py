"""Tests for haute.pipeline - Pipeline and Node classes."""

from __future__ import annotations

import polars as pl
import pytest

from haute._model_scorer import _scenario_ctx
from haute._topo import CycleError
from haute._types import NodeType
from haute.pipeline import Node, NodeRegistry, Pipeline, Submodel

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


# ---------------------------------------------------------------------------
# Node edge cases
# ---------------------------------------------------------------------------


class TestNodeProperties:
    def test_is_deploy_input_true(self):
        n = Node(
            name="inp", description="", fn=lambda: None,
            is_source=True, config={"api_input": True},
        )
        assert n.is_deploy_input is True

    def test_is_deploy_input_false_by_default(self):
        n = Node(name="inp", description="", fn=lambda: None, is_source=True)
        assert n.is_deploy_input is False

    def test_is_live_switch_true(self):
        n = Node(
            name="sw", description="", fn=lambda: None,
            is_source=False, config={"live_switch": True},
        )
        assert n.is_live_switch is True

    def test_is_live_switch_false_by_default(self):
        n = Node(name="sw", description="", fn=lambda: None, is_source=False)
        assert n.is_live_switch is False

    def test_multi_input_node_insufficient_dataframes_raises(self):
        def join(a: pl.DataFrame, b: pl.DataFrame) -> pl.DataFrame:
            return a.hstack(b)

        n = Node(name="join", description="", fn=join, is_source=False)
        with pytest.raises(Exception):
            n(pl.DataFrame({"x": [1]}))


# ---------------------------------------------------------------------------
# NodeRegistry decorator aliases
# ---------------------------------------------------------------------------


class TestDecoratorAliases:
    def test_api_input(self):
        reg = NodeRegistry("test")

        @reg.api_input
        def src() -> pl.DataFrame:
            return pl.DataFrame()

        assert reg.nodes[0].config["_node_type"] == NodeType.API_INPUT

    def test_banding(self):
        reg = NodeRegistry("test")

        @reg.banding
        def band(df: pl.DataFrame) -> pl.DataFrame:
            return df

        assert reg.nodes[0].config["_node_type"] == NodeType.BANDING

    def test_rating_step(self):
        reg = NodeRegistry("test")

        @reg.rating_step
        def rate(df: pl.DataFrame) -> pl.DataFrame:
            return df

        assert reg.nodes[0].config["_node_type"] == NodeType.RATING_STEP

    def test_data_sink(self):
        reg = NodeRegistry("test")

        @reg.data_sink
        def sink(df: pl.DataFrame) -> pl.DataFrame:
            return df

        assert reg.nodes[0].config["_node_type"] == NodeType.DATA_SINK

    def test_external_file(self):
        reg = NodeRegistry("test")

        @reg.external_file
        def ext() -> pl.DataFrame:
            return pl.DataFrame()

        assert reg.nodes[0].config["_node_type"] == NodeType.EXTERNAL_FILE

    def test_live_switch(self):
        reg = NodeRegistry("test")

        @reg.live_switch
        def sw(df: pl.DataFrame) -> pl.DataFrame:
            return df

        assert reg.nodes[0].config["_node_type"] == NodeType.LIVE_SWITCH

    def test_model_score(self):
        reg = NodeRegistry("test")

        @reg.model_score
        def score(df: pl.DataFrame) -> pl.DataFrame:
            return df

        assert reg.nodes[0].config["_node_type"] == NodeType.MODEL_SCORE

    def test_constant(self):
        reg = NodeRegistry("test")

        @reg.constant
        def c() -> pl.DataFrame:
            return pl.DataFrame()

        assert reg.nodes[0].config["_node_type"] == NodeType.CONSTANT

    def test_scenario_expander(self):
        reg = NodeRegistry("test")

        @reg.scenario_expander
        def expand(df: pl.DataFrame) -> pl.DataFrame:
            return df

        assert reg.nodes[0].config["_node_type"] == NodeType.SCENARIO_EXPANDER

    def test_modelling(self):
        reg = NodeRegistry("test")

        @reg.modelling
        def train(df: pl.DataFrame) -> pl.DataFrame:
            return df

        assert reg.nodes[0].config["_node_type"] == NodeType.MODELLING

    def test_optimiser(self):
        reg = NodeRegistry("test")

        @reg.optimiser
        def opt(df: pl.DataFrame) -> pl.DataFrame:
            return df

        assert reg.nodes[0].config["_node_type"] == NodeType.OPTIMISER

    def test_optimiser_apply(self):
        reg = NodeRegistry("test")

        @reg.optimiser_apply
        def opt_apply(df: pl.DataFrame) -> pl.DataFrame:
            return df

        assert reg.nodes[0].config["_node_type"] == NodeType.OPTIMISER_APPLY

    def test_instance(self):
        reg = NodeRegistry("test")

        @reg.instance
        def inst(df: pl.DataFrame) -> pl.DataFrame:
            return df

        assert reg.nodes[0].config["_node_type"] == NodeType.POLARS


# ---------------------------------------------------------------------------
# Pipeline edge cases
# ---------------------------------------------------------------------------


class TestPipelineEdgeCases:
    def test_disconnected_graph_raises(self):
        p = Pipeline("disc")

        @p.data_source
        def a() -> pl.DataFrame:
            return pl.DataFrame({"x": [1]})

        @p.polars
        def b(df: pl.DataFrame) -> pl.DataFrame:
            return df

        @p.polars
        def c(df: pl.DataFrame) -> pl.DataFrame:
            return df

        p.connect("a", "b")
        with pytest.raises(ValueError, match="disconnected|no inbound edges"):
            p.run()

    def test_self_loop_detected(self):
        p = Pipeline("loop")

        @p.data_source
        def a() -> pl.DataFrame:
            return pl.DataFrame({"x": [1]})

        @p.polars
        def b(df: pl.DataFrame) -> pl.DataFrame:
            return df

        p.connect("a", "b").connect("b", "b")
        with pytest.raises(CycleError):
            p._topo_order()

    def test_diamond_dependency(self):
        p = Pipeline("diamond")

        @p.data_source
        def a() -> pl.DataFrame:
            return pl.DataFrame({"x": [1]})

        @p.polars
        def b(df: pl.DataFrame) -> pl.DataFrame:
            return df.with_columns(b=pl.col("x") + 10)

        @p.polars
        def c(df: pl.DataFrame) -> pl.DataFrame:
            return df.with_columns(c=pl.col("x") + 100)

        @p.polars
        def d(left: pl.DataFrame, right: pl.DataFrame) -> pl.DataFrame:
            return left.hstack(right.select("c"))

        p.connect("a", "b").connect("a", "c").connect("b", "d").connect("c", "d")
        result = p.run()
        assert result["b"].to_list() == [11]
        assert result["c"].to_list() == [101]

    def test_score_no_api_input_seeds_all_sources(self):
        p = Pipeline("seed_all")

        @p.data_source
        def src1() -> pl.DataFrame:
            return pl.DataFrame({"x": [999]})

        @p.data_source
        def src2() -> pl.DataFrame:
            return pl.DataFrame({"x": [888]})

        @p.polars
        def merge(a: pl.DataFrame, b: pl.DataFrame) -> pl.DataFrame:
            return a.hstack(b.rename({"x": "y"}))

        p.connect("src1", "merge").connect("src2", "merge")
        input_df = pl.DataFrame({"x": [42]})
        result = p.score(input_df)
        assert result["x"].to_list() == [42]
        assert result["y"].to_list() == [42]

    def test_score_with_api_input_seeds_only_marked(self):
        p = Pipeline("seed_api")

        @p.api_input(api_input=True)
        def live_src() -> pl.DataFrame:
            return pl.DataFrame({"x": [999]})

        @p.data_source
        def static_src() -> pl.DataFrame:
            return pl.DataFrame({"y": [77]})

        @p.polars
        def combine(a: pl.DataFrame, b: pl.DataFrame) -> pl.DataFrame:
            return a.hstack(b)

        p.connect("live_src", "combine").connect("static_src", "combine")
        input_df = pl.DataFrame({"x": [42]})
        result = p.score(input_df)
        assert result["x"].to_list() == [42]
        assert result["y"].to_list() == [77]

    def test_to_graph_single_node(self):
        p = Pipeline("single")

        @p.data_source
        def only() -> pl.DataFrame:
            return pl.DataFrame()

        g = p.to_graph()
        assert len(g["nodes"]) == 1
        assert len(g["edges"]) == 0
        assert g["nodes"][0]["id"] == "only"

    def test_to_graph_underscored_names_title_cased(self):
        p = Pipeline("title")

        @p.data_source
        def my_data_source() -> pl.DataFrame:
            return pl.DataFrame()

        g = p.to_graph()
        assert g["nodes"][0]["data"]["label"] == "My Data Source"

    def test_to_graph_underscore_config_keys_filtered(self):
        p = Pipeline("filter")

        @p.data_source(path="data.parquet")
        def src() -> pl.DataFrame:
            return pl.DataFrame()

        g = p.to_graph()
        config = g["nodes"][0]["data"]["config"]
        assert "path" in config
        assert "_node_type" not in config

    def test_score_all_sources_api_input(self):
        p = Pipeline("all_api")

        @p.api_input(api_input=True)
        def src1() -> pl.DataFrame:
            return pl.DataFrame({"x": [999]})

        @p.api_input(api_input=True)
        def src2() -> pl.DataFrame:
            return pl.DataFrame({"x": [888]})

        @p.polars
        def merge(a: pl.DataFrame, b: pl.DataFrame) -> pl.DataFrame:
            return a.hstack(b.rename({"x": "y"}))

        p.connect("src1", "merge").connect("src2", "merge")
        input_df = pl.DataFrame({"x": [42]})
        result = p.score(input_df)
        assert result["x"].to_list() == [42]
        assert result["y"].to_list() == [42]

    def test_score_mixed_sources(self):
        p = Pipeline("mixed")

        @p.api_input(api_input=True)
        def live() -> pl.DataFrame:
            return pl.DataFrame({"x": [999]})

        @p.data_source
        def static() -> pl.DataFrame:
            return pl.DataFrame({"y": [55]})

        @p.polars
        def combine(a: pl.DataFrame, b: pl.DataFrame) -> pl.DataFrame:
            return a.hstack(b)

        p.connect("live", "combine").connect("static", "combine")
        input_df = pl.DataFrame({"x": [10]})
        result = p.score(input_df)
        assert result["x"].to_list() == [10]
        assert result["y"].to_list() == [55]

    def test_empty_pipeline_run_raises(self):
        p = Pipeline("empty")
        with pytest.raises(ValueError, match="no nodes"):
            p.run()

    def test_no_edges_multiple_nodes_raises(self):
        p = Pipeline("no_edges")

        @p.data_source
        def src() -> pl.DataFrame:
            return pl.DataFrame({"x": [1]})

        @p.polars
        def unconnected(df: pl.DataFrame) -> pl.DataFrame:
            return df

        with pytest.raises(ValueError, match="no inbound edges"):
            p.run()

    def test_to_graph_config_filters_internal_keys(self):
        p = Pipeline("cfg_filter")

        @p.polars(path="/data", _internal="hidden")
        def step(df: pl.DataFrame) -> pl.DataFrame:
            return df

        g = p.to_graph()
        config = g["nodes"][0]["data"]["config"]
        assert "path" in config
        assert config["path"] == "/data"
        assert "_internal" not in config


# ---------------------------------------------------------------------------
# Submodel
# ---------------------------------------------------------------------------


class TestSubmodel:
    def test_basic_creation(self):
        s = Submodel("scoring", description="score sub")
        assert s.name == "scoring"
        assert s.description == "score sub"
        assert s.nodes == []
        assert s.edges == []

    def test_submodel_chaining(self):
        p = Pipeline("main")
        result = p.submodel("a.py").submodel("b.py")
        assert result is p
        assert p.submodel_files == ["a.py", "b.py"]

    def test_submodel_files_property(self):
        p = Pipeline("main")
        p.submodel("one.py").submodel("two.py").submodel("three.py")
        assert p.submodel_files == ["one.py", "two.py", "three.py"]
