"""Tests for haute.pipeline - Pipeline and Node classes."""

from __future__ import annotations

import inspect

import polars as pl
import pytest

from haute._model_scorer import _scenario_ctx
from haute._topo import CycleError
from haute._types import NodeType
from haute.errors import ExecutionError
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

    def test_keyword_only_defaults_do_not_require_edges(self):
        def t(df: pl.DataFrame, *, factor: int = 2) -> pl.DataFrame:
            return df.with_columns(y=pl.col("x") * factor)

        n = Node(name="t", description="", fn=t, is_source=False)
        assert n.n_inputs == 1
        df = n(pl.DataFrame({"x": [10]}))
        assert df["y"].to_list() == [20]

    def test_optional_positional_inputs_can_use_defaults(self):
        def t(df: pl.DataFrame, factor: int = 2) -> pl.DataFrame:
            return df.with_columns(y=pl.col("x") * factor)

        n = Node(name="t", description="", fn=t, is_source=False)
        assert n.n_inputs == 1
        df = n(pl.DataFrame({"x": [10]}))
        assert df["y"].to_list() == [20]

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

        @p.data_input
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

    def test_connect_source_port_appears_as_source_handle(self):
        p = Pipeline("ports")

        @p.data_input
        def source() -> pl.DataFrame:
            return pl.DataFrame({"x": [1]})

        @p.polars
        def transform(source: pl.DataFrame) -> pl.DataFrame:
            return source

        p.connect("source", "transform", source_port="policies")
        g = p.to_graph()
        assert len(g["edges"]) == 1
        assert g["edges"][0]["sourceHandle"] == "policies"

    def test_connect_without_source_port_uses_null_source_handle(self):
        p = self._simple_pipeline()
        g = p.to_graph()
        assert g["edges"][0]["sourceHandle"] is None

    def test_connect_target_port_appears_as_target_handle(self):
        p = self._simple_pipeline()
        p.connect("source", "transform", target_port="base")
        g = p.to_graph()
        assert g["edges"][-1]["targetHandle"] == "base"

    def test_connect_source_port_makes_same_pair_edge_ids_distinct(self):
        p = self._simple_pipeline()
        p.connect("source", "transform", source_port="policies")
        p.connect("source", "transform", source_port="drivers")
        g = p.to_graph()
        edge_ids = [edge["id"] for edge in g["edges"]]
        assert len(edge_ids) == len(set(edge_ids))

    def test_connect_target_port_makes_same_pair_edge_ids_distinct(self):
        p = self._simple_pipeline()
        p.connect("source", "transform", target_port="base")
        p.connect("source", "transform", target_port="join")
        g = p.to_graph()
        edge_ids = [edge["id"] for edge in g["edges"]]
        assert len(edge_ids) == len(set(edge_ids))

    def test_connect_empty_source_port_raises(self):
        p = self._simple_pipeline()
        with pytest.raises(ValueError, match="source_port must be a non-empty string"):
            p.connect("source", "transform", source_port="")

    def test_connect_non_string_source_port_raises(self):
        p = self._simple_pipeline()
        with pytest.raises(TypeError, match="source_port must be a non-empty string"):
            p.connect("source", "transform", source_port=123)  # type: ignore[arg-type]

    def test_connect_empty_target_port_raises(self):
        p = self._simple_pipeline()
        with pytest.raises(ValueError, match="target_port must be a non-empty string"):
            p.connect("source", "transform", target_port="")

    def test_connect_non_string_target_port_raises(self):
        p = self._simple_pipeline()
        with pytest.raises(TypeError, match="target_port must be a non-empty string"):
            p.connect("source", "transform", target_port=123)  # type: ignore[arg-type]

    def test_connect_chaining(self):
        p = Pipeline("chain")

        @p.data_input
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

        @p.data_input(path="data.parquet")
        def read_data() -> pl.DataFrame:
            return pl.DataFrame({"x": [1]})

        assert p.nodes[0].config == {"path": "data.parquet", "_node_type": NodeType.DATA_INPUT}
        assert p.nodes[0].is_source is True

    def test_explore_decorator_registers_analysis_sink_node(self):
        p = Pipeline("explore")

        @p.explore
        def inspect(df: pl.DataFrame) -> pl.DataFrame:
            return df

        assert p.nodes[0].config == {"_node_type": NodeType.EXPLORE}
        assert p.nodes[0].is_source is False

    def test_empty_pipeline_raises(self):
        p = Pipeline("empty")
        with pytest.raises(ValueError, match="no nodes"):
            p.run()

    def test_topo_order_delegates_to_graph_utils(self):
        p = Pipeline("topo")

        @p.data_input
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

        @p.data_input
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

        @p.data_input
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

        @p.data_input
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

        @p.data_input
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
        assert node_map["source"]["data"]["nodeType"] == "dataInput"
        assert node_map["transform"]["data"]["nodeType"] == "polars"

    def test_to_graph_does_not_invent_edges(self):
        """Without explicit edges, to_graph() reports exactly zero edges.

        Regression for the registration-order chain invention: the canvas
        showed a fabricated a→b→c flow that run() itself would refuse to
        execute (unwired transforms fail loudly), so visualization and
        execution disagreed.
        """
        p = Pipeline("chain")

        @p.data_input
        def a() -> pl.DataFrame:
            return pl.DataFrame()

        @p.polars
        def b(df: pl.DataFrame) -> pl.DataFrame:
            return df

        @p.polars
        def c(df: pl.DataFrame) -> pl.DataFrame:
            return df

        g = p.to_graph()
        assert g["edges"] == []

    def test_run_sets_scenario_ctx_to_batch(self):
        """Pipeline.run() must set _scenario_ctx to 'batch' during execution."""
        captured: list[str] = []
        p = Pipeline("ctx_batch")

        @p.data_input
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

        @p.data_input
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

        @p.data_input
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

        @p.data_input
        def a() -> pl.DataFrame:
            return pl.DataFrame()

        @p.polars
        def b(df: pl.DataFrame) -> pl.DataFrame:
            return df

        g = p.to_graph()
        assert g["nodes"][0]["position"]["x"] == 0
        assert g["nodes"][1]["position"]["x"] > g["nodes"][0]["position"]["x"]


# ---------------------------------------------------------------------------
# Standalone port-aware execution
# ---------------------------------------------------------------------------


class TestPipelinePortAwareExecution:
    @staticmethod
    def _one_port_score_pipeline() -> Pipeline:
        p = Pipeline("one_port_score")

        @p.api_input
        def api_source() -> dict[str, pl.DataFrame]:
            raise AssertionError("score() must seed the apiInput source")

        @p.polars
        def consume(quotes: pl.DataFrame) -> pl.DataFrame:
            return quotes

        p.connect("api_source", "consume", source_port="quotes")
        return p

    @staticmethod
    def _two_port_score_pipeline() -> Pipeline:
        p = Pipeline("two_port_score")

        @p.api_input
        def api_source() -> dict[str, pl.DataFrame]:
            raise AssertionError("score() must seed the apiInput source")

        @p.polars
        def combine(quotes: pl.DataFrame, drivers: pl.DataFrame) -> pl.DataFrame:
            if not isinstance(quotes, pl.DataFrame) or not isinstance(drivers, pl.DataFrame):
                return pl.DataFrame({"quote_id": [-1], "driver_id": [-1]})
            return pl.DataFrame(
                {
                    "quote_id": quotes["quote_id"],
                    "driver_id": drivers["driver_id"],
                }
            )

        p.connect("api_source", "combine", source_port="quotes")
        p.connect("api_source", "combine", source_port="drivers")
        return p

    def test_run_selects_the_frame_from_a_one_frame_api_input_dict(self):
        p = Pipeline("one_frame_run")
        expected = pl.DataFrame({"quote_id": [17]})

        @p.api_input
        def api_source() -> dict[str, pl.DataFrame]:
            return {"quotes": expected}

        @p.polars
        def consume(quotes: pl.DataFrame) -> pl.DataFrame:
            return quotes

        p.connect("api_source", "consume", source_port="quotes")

        result = p.run()
        assert isinstance(result, pl.DataFrame)
        assert result.to_dict(as_series=False) == {"quote_id": [17]}

    def test_run_selects_each_named_frame_from_a_many_frame_api_input_dict(self):
        p = Pipeline("many_frame_run")

        @p.api_input
        def api_source() -> dict[str, pl.DataFrame]:
            return {
                "quotes": pl.DataFrame({"quote_id": [17]}),
                "drivers": pl.DataFrame({"driver_id": [31]}),
            }

        @p.polars
        def combine(quotes: pl.DataFrame, drivers: pl.DataFrame) -> pl.DataFrame:
            if not isinstance(quotes, pl.DataFrame) or not isinstance(drivers, pl.DataFrame):
                return pl.DataFrame({"quote_id": [-1], "driver_id": [-1]})
            return pl.DataFrame(
                {
                    "quote_id": quotes["quote_id"],
                    "driver_id": drivers["driver_id"],
                }
            )

        p.connect("api_source", "combine", source_port="quotes")
        p.connect("api_source", "combine", source_port="drivers")

        assert p.run().to_dict(as_series=False) == {
            "quote_id": [17],
            "driver_id": [31],
        }

    def test_score_accepts_a_bare_frame_for_a_source_only_pipeline(self):
        p = Pipeline("source_only_score")

        @p.api_input
        def api_source() -> pl.DataFrame:
            raise AssertionError("score() must seed the apiInput source")

        seed = pl.DataFrame({"quote_id": [17]})
        assert p.score(seed).to_dict(as_series=False) == {"quote_id": [17]}

    def test_score_accepts_a_bare_frame_for_exactly_one_connected_port(self):
        p = self._one_port_score_pipeline()
        seed = pl.DataFrame({"quote_id": [17]})

        assert p.score(seed).to_dict(as_series=False) == {"quote_id": [17]}

    def test_score_treats_an_unnamed_edge_as_the_whole_output_channel(self):
        p = Pipeline("unnamed_port_score")

        @p.api_input
        def api_source() -> pl.DataFrame:
            raise AssertionError("score() must seed the apiInput source")

        @p.polars
        def consume(quotes: pl.DataFrame) -> pl.DataFrame:
            return quotes

        p.connect("api_source", "consume")
        seed = pl.DataFrame({"quote_id": [17]})

        assert p.score(seed).to_dict(as_series=False) == {"quote_id": [17]}
        with pytest.raises(ExecutionError, match="no connected ports"):
            p.score({"quotes": seed})

    def test_score_rejects_a_bare_frame_for_multiple_connected_ports(self):
        p = self._two_port_score_pipeline()

        with pytest.raises(ExecutionError) as exc_info:
            p.score(pl.DataFrame({"quote_id": [17], "driver_id": [31]}))

        message = str(exc_info.value)
        assert "quotes" in message
        assert "drivers" in message

    def test_score_accepts_an_exact_one_key_dict_for_one_connected_port(self):
        p = self._one_port_score_pipeline()

        result = p.score({"quotes": pl.DataFrame({"quote_id": [17]})})

        assert isinstance(result, pl.DataFrame)
        assert result.to_dict(as_series=False) == {"quote_id": [17]}

    def test_score_accepts_an_exact_dict_for_multiple_connected_ports(self):
        p = self._two_port_score_pipeline()

        result = p.score(
            {
                "quotes": pl.DataFrame({"quote_id": [17]}),
                "drivers": pl.DataFrame({"driver_id": [31]}),
            }
        )

        assert result.to_dict(as_series=False) == {
            "quote_id": [17],
            "driver_id": [31],
        }

    def test_score_reports_missing_and_unknown_ports_from_the_same_dict(self):
        p = self._two_port_score_pipeline()

        with pytest.raises(ExecutionError) as exc_info:
            p.score(
                {
                    "quotes": pl.DataFrame({"quote_id": [17]}),
                    "rogue": pl.DataFrame({"other_id": [99]}),
                }
            )

        message = str(exc_info.value)
        assert "missing" in message.lower()
        assert "drivers" in message
        assert "unknown" in message.lower()
        assert "rogue" in message

    def test_score_rejects_a_dict_with_an_unknown_extra_port(self):
        p = self._one_port_score_pipeline()

        with pytest.raises(ExecutionError) as exc_info:
            p.score(
                {
                    "quotes": pl.DataFrame({"quote_id": [17]}),
                    "drivers": pl.DataFrame({"driver_id": [31]}),
                }
            )

        message = str(exc_info.value)
        assert "unknown" in message.lower()
        assert "drivers" in message

    @pytest.mark.parametrize(
        "seed, unknown_port",
        [
            ({}, None),
            ({"quotes": pl.DataFrame({"quote_id": [17]})}, "quotes"),
        ],
        ids=["empty-dict", "non-empty-dict"],
    )
    def test_score_rejects_any_dict_for_a_source_with_zero_connected_ports(
        self,
        seed: dict[str, pl.DataFrame],
        unknown_port: str | None,
    ):
        p = Pipeline("source_only_dict_score")

        @p.api_input
        def api_source() -> pl.DataFrame:
            raise AssertionError("score() must seed the apiInput source")

        with pytest.raises(ExecutionError) as exc_info:
            p.score(seed)

        message = str(exc_info.value)
        assert "port" in message.lower()
        if unknown_port is not None:
            assert unknown_port in message


# ---------------------------------------------------------------------------
# Node edge cases
# ---------------------------------------------------------------------------


class TestNodeProperties:
    def test_is_deploy_input_true(self):
        n = Node(
            name="inp",
            description="",
            fn=lambda: None,
            is_source=True,
            config={"api_input": True},
        )
        assert n.is_deploy_input is True

    def test_is_deploy_input_false_by_default(self):
        n = Node(name="inp", description="", fn=lambda: None, is_source=True)
        assert n.is_deploy_input is False

    def test_is_live_switch_true(self):
        n = Node(
            name="sw",
            description="",
            fn=lambda: None,
            is_source=False,
            config={"live_switch": True},
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

    def test_data_input(self):
        reg = NodeRegistry("test")

        @reg.data_input
        def source() -> pl.DataFrame:
            return pl.DataFrame()

        assert reg.nodes[0].config["_node_type"] == NodeType.DATA_INPUT

    def test_data_output(self):
        reg = NodeRegistry("test")

        @reg.data_output
        def output(df: pl.DataFrame) -> pl.DataFrame:
            return df

        assert reg.nodes[0].config["_node_type"] == NodeType.DATA_OUTPUT

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

        @p.data_input
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

        @p.data_input
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

        @p.data_input
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

    def test_score_multiple_unmarked_sources_raises(self):
        """score() must not guess-seed every source with the same df (F514).

        With more than one source and none marked as the live input, which
        source receives the scored frame is ambiguous — fail loud instead of
        silently seeding a static rating table with quote data.
        """
        p = Pipeline("seed_all")

        @p.data_input
        def src1() -> pl.DataFrame:
            return pl.DataFrame({"x": [999]})

        @p.data_input
        def src2() -> pl.DataFrame:
            return pl.DataFrame({"x": [888]})

        @p.polars
        def merge(a: pl.DataFrame, b: pl.DataFrame) -> pl.DataFrame:
            return a.hstack(b.rename({"x": "y"}))

        p.connect("src1", "merge").connect("src2", "merge")
        with pytest.raises(ExecutionError, match="[Mm]ark exactly one source"):
            p.score(pl.DataFrame({"x": [42]}))

    def test_score_single_unmarked_source_is_seeded(self):
        """A lone source is unambiguous, so score() seeds it without a mark."""
        p = Pipeline("single_seed")

        @p.data_input
        def src() -> pl.DataFrame:
            return pl.DataFrame({"x": [999]})

        @p.polars
        def transform(df: pl.DataFrame) -> pl.DataFrame:
            return df.with_columns(y=pl.col("x") + 1)

        p.connect("src", "transform")
        result = p.score(pl.DataFrame({"x": [41]}))
        assert result["y"].to_list() == [42]

    def test_score_with_api_input_seeds_only_marked(self):
        p = Pipeline("seed_api")

        @p.api_input(api_input=True)
        def live_src() -> pl.DataFrame:
            return pl.DataFrame({"x": [999]})

        @p.data_input
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

        @p.data_input
        def only() -> pl.DataFrame:
            return pl.DataFrame()

        g = p.to_graph()
        assert len(g["nodes"]) == 1
        assert len(g["edges"]) == 0
        assert g["nodes"][0]["id"] == "only"

    def test_to_graph_uses_static_builder_label(self):
        p = Pipeline("title")

        @p.data_input
        def my_data_source() -> pl.DataFrame:
            return pl.DataFrame()

        g = p.to_graph()
        assert g["nodes"][0]["data"]["label"] == "my_data_source"

    def test_to_graph_underscore_config_keys_filtered(self):
        p = Pipeline("filter")

        @p.data_input(path="data.parquet")
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
        with pytest.raises(ExecutionError, match="multiple live input"):
            p.score(input_df)

    def test_score_mixed_sources(self):
        p = Pipeline("mixed")

        @p.api_input(api_input=True)
        def live() -> pl.DataFrame:
            return pl.DataFrame({"x": [999]})

        @p.data_input
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

        @p.data_input
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

    def test_declared_outputs_are_ordered_and_copy_on_read(self):
        s = Submodel("scoring", outputs=["score", "audit"])

        assert s.outputs == ["score", "audit"]
        returned = s.outputs
        returned.append("mutated")
        assert s.outputs == ["score", "audit"]

    @pytest.mark.parametrize(
        "outputs",
        [["score", "score"], [""], ["score", 1], "score"],
    )
    def test_invalid_declared_outputs_fail_loudly(self, outputs):
        with pytest.raises((TypeError, ValueError), match="output"):
            Submodel("scoring", outputs=outputs)

    def test_submodel_chaining(self):
        p = Pipeline("main")
        result = p.submodel("a.py").submodel("b.py")
        assert result is p
        assert p.submodel_files == ["a.py", "b.py"]

    def test_submodel_files_property(self):
        p = Pipeline("main")
        p.submodel("one.py").submodel("two.py").submodel("three.py")
        assert p.submodel_files == ["one.py", "two.py", "three.py"]


# ---------------------------------------------------------------------------
# Declared-output resolution (F510)
# ---------------------------------------------------------------------------


class TestOutputResolution:
    def test_run_returns_declared_output_not_last_topo_node(self):
        """run() must return the @pipeline.output node, not whatever sorts last."""
        p = Pipeline("declared_out")

        @p.data_input
        def src() -> pl.DataFrame:
            return pl.DataFrame({"x": [1]})

        @p.output
        def result(df: pl.DataFrame) -> pl.DataFrame:
            return df.with_columns(kept=pl.lit("output"))

        @p.data_output
        def audit(df: pl.DataFrame) -> pl.DataFrame:
            return df.with_columns(sink=pl.lit("side_effect"))

        # 'audit' is wired after 'result' so it sorts last in topo order, but
        # 'result' is the declared output that must be returned.
        p.connect("src", "result").connect("result", "audit")
        out = p.run()
        assert "kept" in out.columns
        assert "sink" not in out.columns

    def test_run_fan_out_multiple_leaves_raises(self):
        """A fan-out with several terminal nodes must fail loud, not guess."""
        p = Pipeline("fan_out")

        @p.data_input
        def src() -> pl.DataFrame:
            return pl.DataFrame({"x": [1]})

        @p.polars
        def left(df: pl.DataFrame) -> pl.DataFrame:
            return df

        @p.polars
        def right(df: pl.DataFrame) -> pl.DataFrame:
            return df

        p.connect("src", "left").connect("src", "right")
        with pytest.raises(ExecutionError, match="multiple terminal nodes"):
            p.run()

    def test_run_multiple_output_nodes_raises(self):
        p = Pipeline("two_outputs")

        @p.data_input
        def src() -> pl.DataFrame:
            return pl.DataFrame({"x": [1]})

        @p.output
        def out_a(df: pl.DataFrame) -> pl.DataFrame:
            return df

        @p.output
        def out_b(df: pl.DataFrame) -> pl.DataFrame:
            return df

        p.connect("src", "out_a").connect("src", "out_b")
        with pytest.raises(ExecutionError, match="multiple @pipeline.output"):
            p.run()

    def test_score_returns_declared_output(self):
        p = Pipeline("score_out")

        @p.api_input
        def live() -> pl.DataFrame:
            return pl.DataFrame({"x": [1]})

        @p.output
        def result(df: pl.DataFrame) -> pl.DataFrame:
            return df.with_columns(kept=pl.lit("output"))

        @p.data_output
        def audit(df: pl.DataFrame) -> pl.DataFrame:
            return df.with_columns(sink=pl.lit("side_effect"))

        p.connect("live", "result").connect("result", "audit")
        out = p.score(pl.DataFrame({"x": [7]}))
        assert "kept" in out.columns
        assert "sink" not in out.columns


# ---------------------------------------------------------------------------
# Arity validation (F511, F512)
# ---------------------------------------------------------------------------


class TestNodeArityValidation:
    def test_signature_is_inspected_once_at_node_construction(self, monkeypatch) -> None:
        calls = 0
        real_signature = inspect.signature

        def counting_signature(fn):
            nonlocal calls
            calls += 1
            return real_signature(fn)

        monkeypatch.setattr("haute.pipeline.inspect.signature", counting_signature)

        node = Node(name="cached", description="", fn=lambda df: df, is_source=False)
        assert node.n_inputs == 1
        assert node.input_arity.describe() == "1"
        node(pl.DataFrame({"x": [1]}))
        assert calls == 1

    def test_positional_only_and_defaulted_inputs_define_bounded_arity(self) -> None:
        def transform(
            required: pl.DataFrame,
            optional: pl.DataFrame | None = None,
            /,
            *,
            label: str = "ignored",
        ) -> pl.DataFrame:
            _ = label
            return required if optional is None else required.vstack(optional)

        node = Node(name="positional", description="", fn=transform, is_source=False)

        assert node.input_arity.min_inputs == 1
        assert node.input_arity.max_inputs == 2
        assert node.input_arity.accepts(1)
        assert node.input_arity.accepts(2)
        assert not node.input_arity.accepts(3)

    def test_varargs_are_the_supported_unbounded_input_form(self) -> None:
        def combine(first: pl.DataFrame, *rest: pl.DataFrame) -> pl.DataFrame:
            return pl.concat((first, *rest), how="vertical")

        node = Node(name="variadic", description="", fn=combine, is_source=False)

        assert node.input_arity.min_inputs == 1
        assert node.input_arity.max_inputs is None
        result = node(pl.DataFrame({"x": [1]}), pl.DataFrame({"x": [2]}))
        assert result["x"].to_list() == [1, 2]

    def test_single_param_node_fed_two_edges_raises(self):
        """A one-input node wired to two sources must not silently drop one."""
        p = Pipeline("over_wired")

        @p.data_input
        def a() -> pl.DataFrame:
            return pl.DataFrame({"x": [1]})

        @p.data_input
        def b() -> pl.DataFrame:
            return pl.DataFrame({"x": [2]})

        @p.polars
        def one_input(df: pl.DataFrame) -> pl.DataFrame:
            return df

        p.connect("a", "one_input").connect("b", "one_input")
        with pytest.raises(ExecutionError, match="accepts 1 input.*2 edge"):
            p.run()

    def test_multi_param_node_under_wired_raises_haute_error(self):
        """Under-wiring must raise an actionable HauteError, not a raw TypeError."""
        p = Pipeline("under_wired")

        @p.data_input
        def a() -> pl.DataFrame:
            return pl.DataFrame({"x": [1]})

        @p.polars
        def needs_two(left: pl.DataFrame, right: pl.DataFrame) -> pl.DataFrame:
            return left.hstack(right)

        p.connect("a", "needs_two")
        with pytest.raises(ExecutionError, match="accepts 2 input.*1 edge"):
            p.run()

    def test_node_call_extra_dfs_raises(self):
        def one(df: pl.DataFrame) -> pl.DataFrame:
            return df

        n = Node(name="one", description="", fn=one, is_source=False)
        with pytest.raises(ExecutionError, match="accepts 1 input.*2 edge"):
            n(pl.DataFrame({"x": [1]}), pl.DataFrame({"x": [2]}))


# ---------------------------------------------------------------------------
# api_input decorator marks the deploy seed (F513)
# ---------------------------------------------------------------------------


class TestApiInputDecoratorMarksSeed:
    def test_api_input_decorator_alone_marks_deploy_input(self):
        """@pipeline.api_input (no kwarg) must mark the node as the live seed."""
        p = Pipeline("decorated")

        @p.api_input
        def live() -> pl.DataFrame:
            raise AssertionError("api_input source must be seeded, not called")

        @p.data_input
        def static() -> pl.DataFrame:
            return pl.DataFrame({"y": [5]})

        @p.polars
        def combine(a: pl.DataFrame, b: pl.DataFrame) -> pl.DataFrame:
            return a.hstack(b)

        p.connect("live", "combine").connect("static", "combine")
        result = p.score(pl.DataFrame({"x": [9]}))
        assert result["x"].to_list() == [9]
        assert result["y"].to_list() == [5]

    def test_is_deploy_input_from_node_type(self):
        n = Node(
            name="live",
            description="",
            fn=lambda: None,
            is_source=True,
            config={"_node_type": NodeType.API_INPUT},
        )
        assert n.is_deploy_input is True


# ---------------------------------------------------------------------------
# Instance references cannot be silently ignored (F516)
# ---------------------------------------------------------------------------


class TestInstanceReferencesFailLoud:
    def test_instance_of_reference_raises_in_standalone_run(self):
        p = Pipeline("instance_run")

        @p.data_input
        def src() -> pl.DataFrame:
            return pl.DataFrame({"x": [1]})

        @p.instance(instanceOf="some_other_node")
        def inst(df: pl.DataFrame) -> pl.DataFrame:
            return df

        p.connect("src", "inst")
        with pytest.raises(ExecutionError, match="instanceOf.*inputMapping|cannot resolve"):
            p.run()

    def test_instance_decorator_without_reference_values_still_raises(self):
        p = Pipeline("instance_marker")

        @p.api_input
        def src() -> pl.DataFrame:
            return pl.DataFrame({"x": [1]})

        @p.instance
        def inst(df: pl.DataFrame) -> pl.DataFrame:
            return df

        p.connect("src", "inst")

        with pytest.raises(ExecutionError, match="instance"):
            p.run()
        with pytest.raises(ExecutionError, match="instance"):
            p.score(pl.DataFrame({"x": [2]}))

    def test_zero_parameter_instance_raises_in_run_and_score(self):
        p = Pipeline("zero_parameter_instance")

        @p.instance
        def inst() -> pl.DataFrame:
            return pl.DataFrame({"stub": [1]})

        with pytest.raises(ExecutionError, match="instance"):
            p.run()
        with pytest.raises(ExecutionError, match="instance"):
            p.score(pl.DataFrame({"x": [2]}))


# ---------------------------------------------------------------------------
# Duplicate node names fail loud at registration (F288)
# ---------------------------------------------------------------------------


class TestDuplicateNodeName:
    def test_duplicate_name_raises_at_registration(self):
        p = Pipeline("dupes")

        @p.data_input
        def src() -> pl.DataFrame:
            return pl.DataFrame({"x": [1]})

        with pytest.raises(ValueError, match="Duplicate node name 'src'"):

            @p.polars
            def src(df: pl.DataFrame) -> pl.DataFrame:  # noqa: F811 - intentional collision
                return df
