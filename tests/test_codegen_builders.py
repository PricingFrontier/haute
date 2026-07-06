"""Tests for codegen builder functions — _gen_api_input, _gen_banding, etc.

Follows the same pattern as test_codegen.py: build a node, call
_node_to_code (which dispatches to the type-specific builder), then
verify the generated code compiles and contains expected fragments.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from haute._codegen_builders import _build_extra_kwargs
from haute.codegen import _node_to_code, graph_to_code
from haute.errors import ConfigError, ParseError
from tests.conftest import compile_node_code as _compile_node_code
from tests.conftest import make_graph as _g
from tests.conftest import make_node as _n
from tests.conftest import make_output_config

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_codegen_node(node_type: str, config: dict, label: str = "TestNode"):
    """Build a GraphNode for codegen testing."""
    return _n(
        {
            "id": "test_id",
            "data": {"label": label, "nodeType": node_type, "config": config},
        }
    )


# ---------------------------------------------------------------------------
# _build_extra_kwargs helper
# ---------------------------------------------------------------------------


class TestBuildExtraKwargs:
    """Unit tests for the _build_extra_kwargs utility."""

    def test_includes_present_keys(self) -> None:
        config = {"a": 1, "b": "hello", "c": [1, 2]}
        result = _build_extra_kwargs(config, ("a", "b", "c"))
        assert "a=1" in result
        assert "b='hello'" in result
        assert "c=[1, 2]" in result

    def test_skips_none_values(self) -> None:
        config = {"a": None, "b": 42}
        result = _build_extra_kwargs(config, ("a", "b"))
        assert len(result) == 1
        assert "b=42" in result

    def test_skips_empty_string(self) -> None:
        config = {"a": "", "b": "val"}
        result = _build_extra_kwargs(config, ("a", "b"))
        assert len(result) == 1
        assert "b='val'" in result

    def test_skips_empty_list(self) -> None:
        config = {"a": [], "b": [1]}
        result = _build_extra_kwargs(config, ("a", "b"))
        assert len(result) == 1
        assert "b=[1]" in result

    def test_skips_missing_keys(self) -> None:
        config = {"a": 10}
        result = _build_extra_kwargs(config, ("a", "missing_key"))
        assert len(result) == 1
        assert "a=10" in result

    def test_empty_config(self) -> None:
        result = _build_extra_kwargs({}, ("a", "b"))
        assert result == []


# ---------------------------------------------------------------------------
# _gen_api_input
# ---------------------------------------------------------------------------


class TestGenApiInput:
    """Tests for API input code generation."""

    def test_parquet_api_input(self) -> None:
        node = _make_codegen_node(
            "apiInput",
            {"path": "data/api_input.parquet"},
            label="PolicyData",
        )
        code = _node_to_code(node)
        assert 'config="config/quote_input/PolicyData.json"' in code
        assert "def PolicyData()" in code
        assert "read_data_source" in code
        assert 'Path(__file__).parent / "data/api_input.parquet"' in code
        _compile_node_code(code)

    def test_csv_api_input(self) -> None:
        node = _make_codegen_node(
            "apiInput",
            {"path": "data/input.csv"},
            label="CSVInput",
        )
        code = _node_to_code(node)
        assert 'config="config/quote_input/CSVInput.json"' in code
        assert "def CSVInput()" in code
        assert "read_data_source" in code
        assert 'Path(__file__).parent / "data/input.csv"' in code
        _compile_node_code(code)

    def test_api_input_preserves_categorical_levels_in_shared_reader(self) -> None:
        node = _make_codegen_node(
            "apiInput",
            {
                "path": "data/input.csv",
                "categorical_levels": {"region": ["north", "south"]},
            },
            label="CategoricalInput",
        )

        code = _node_to_code(node)

        assert "read_data_source" in code
        assert "'categorical_levels': {'region': ['north', 'south']}" in code
        _compile_node_code(code)

    def test_json_api_input(self) -> None:
        node = _make_codegen_node(
            "apiInput",
            {"path": "data/quotes.json"},
            label="JSONInput",
        )
        code = _node_to_code(node)
        assert 'config="config/quote_input/JSONInput.json"' in code
        assert "def JSONInput()" in code
        assert "load_v2_api_source" in code
        assert 'Path(__file__).parent / "config/quote_input/JSONInput.json"' in code
        _compile_node_code(code)

    def test_jsonl_api_input(self) -> None:
        node = _make_codegen_node(
            "apiInput",
            {"path": "data/quotes.jsonl"},
            label="JSONLInput",
        )
        code = _node_to_code(node)
        assert "load_v2_api_source" in code
        _compile_node_code(code)

    def test_api_input_with_row_id(self) -> None:
        """row_id_column is included in the inline decorator, but _node_to_code
        replaces the decorator with a config= ref.  Verify the function is
        still valid and the config path is present."""
        node = _make_codegen_node(
            "apiInput",
            {"path": "data/api.parquet", "row_id_column": "policy_id"},
            label="WithRowID",
        )
        code = _node_to_code(node)
        assert 'config="config/quote_input/WithRowID.json"' in code
        assert "def WithRowID()" in code
        _compile_node_code(code)

    def test_api_input_no_row_id(self) -> None:
        node = _make_codegen_node(
            "apiInput",
            {"path": "data/api.parquet"},
            label="NoRowID",
        )
        code = _node_to_code(node)
        assert "row_id_column" not in code
        _compile_node_code(code)

    def test_api_input_empty_path(self) -> None:
        node = _make_codegen_node("apiInput", {"path": ""}, label="Empty")
        code = _node_to_code(node)
        assert "def Empty()" in code
        _compile_node_code(code)


# ---------------------------------------------------------------------------
# _gen_banding
# ---------------------------------------------------------------------------


class TestGenBanding:
    """Tests for banding code generation."""

    def test_single_continuous_factor(self) -> None:
        node = _make_codegen_node(
            "banding",
            {
                "factors": [
                    {
                        "column": "age",
                        "outputColumn": "age_band",
                        "banding": "continuous",
                        "rules": [
                            {"op1": ">=", "val1": 0, "op2": "<", "val2": 25, "assignment": "young"},
                            {
                                "op1": ">=",
                                "val1": 25,
                                "op2": "<=",
                                "val2": 100,
                                "assignment": "adult",
                            },
                        ],
                    }
                ],
            },
            label="AgeBanding",
        )
        code = _node_to_code(node, source_names=["data"])
        assert 'config="config/banding/AgeBanding.json"' in code
        assert "def AgeBanding(data: pl.LazyFrame)" in code
        # The body must APPLY banding from the sidecar config — the same
        # pattern rating bodies use — so a standalone run of the saved file
        # actually bands instead of silently passing the frame through.
        assert "from haute.graph_utils import apply_banding_from_config" in code
        assert 'apply_banding_from_config(data, "config/banding/AgeBanding.json"' in code
        assert "base_dir=base" in code
        assert "return df" in code
        _compile_node_code(code)

    def test_single_factor_with_default(self) -> None:
        node = _make_codegen_node(
            "banding",
            {
                "factors": [
                    {
                        "column": "age",
                        "outputColumn": "age_band",
                        "banding": "continuous",
                        "default": "unknown",
                        "rules": [
                            {"op1": ">=", "val1": 0, "op2": "<", "val2": 25, "assignment": "young"},
                        ],
                    }
                ],
            },
            label="WithDefault",
        )
        code = _node_to_code(node, source_names=["data"])
        # The inline decorator should have default kwarg before config replacement
        assert 'config="config/banding/WithDefault.json"' in code
        _compile_node_code(code)

    def test_multi_factor_banding(self) -> None:
        node = _make_codegen_node(
            "banding",
            {
                "factors": [
                    {
                        "column": "age",
                        "outputColumn": "age_band",
                        "banding": "continuous",
                        "rules": [
                            {"op1": ">=", "val1": 0, "op2": "<", "val2": 50, "assignment": "u50"},
                        ],
                    },
                    {
                        "column": "region",
                        "outputColumn": "region_group",
                        "banding": "categorical",
                        "rules": [{"value": "north", "assignment": "N"}],
                    },
                ],
            },
            label="MultiBand",
        )
        code = _node_to_code(node, source_names=["data"])
        assert 'config="config/banding/MultiBand.json"' in code
        assert "def MultiBand(data: pl.LazyFrame)" in code
        # Multi-factor emission embeds the same apply call as single-factor.
        assert 'apply_banding_from_config(data, "config/banding/MultiBand.json"' in code
        _compile_node_code(code)

    def test_categorical_banding(self) -> None:
        """The inline decorator contains 'categorical' but _node_to_code
        replaces it with config=.  Verify the config path and function
        signature are correct."""
        node = _make_codegen_node(
            "banding",
            {
                "factors": [
                    {
                        "column": "vehicle",
                        "outputColumn": "vehicle_group",
                        "banding": "categorical",
                        "rules": [{"value": "car", "assignment": "auto"}],
                    }
                ],
            },
            label="CatBand",
        )
        code = _node_to_code(node, source_names=["df_in"])
        assert 'config="config/banding/CatBand.json"' in code
        assert "def CatBand(df_in: pl.LazyFrame)" in code
        _compile_node_code(code)

    def test_empty_factors(self) -> None:
        node = _make_codegen_node("banding", {"factors": []}, label="Empty")
        code = _node_to_code(node, source_names=["data"])
        # Still generates a valid function
        assert "def Empty(" in code
        _compile_node_code(code)

    def test_no_sources_uses_df_param(self) -> None:
        node = _make_codegen_node(
            "banding",
            {
                "factors": [
                    {
                        "column": "x",
                        "outputColumn": "x_band",
                        "banding": "continuous",
                        "rules": [
                            {"op1": ">=", "val1": 0, "op2": "<", "val2": 10, "assignment": "low"},
                        ],
                    }
                ],
            },
            label="NoSrc",
        )
        code = _node_to_code(node, source_names=[])
        assert "df: pl.LazyFrame" in code
        _compile_node_code(code)


# ---------------------------------------------------------------------------
# _gen_scenario_expander
# ---------------------------------------------------------------------------


class TestGenScenarioExpander:
    """Tests for scenario expander code generation."""

    def test_basic_scenario_expander(self) -> None:
        node = _make_codegen_node(
            "scenarioExpander",
            {
                "quote_id": "quote_id",
                "column_name": "scenario_value",
                "min_value": 0.8,
                "max_value": 1.2,
                "steps": 21,
                "step_column": "scenario_index",
            },
            label="Scenarios",
        )
        code = _node_to_code(node, source_names=["base_data"])
        assert 'config="config/expander/Scenarios.json"' in code
        assert "def Scenarios(base_data: pl.LazyFrame)" in code
        # Body applies the sidecar config via the shared helper (not a no-op
        # passthrough) so a standalone pipeline.run() expands the grid.
        assert "expand_scenarios_from_config(base_data" in code
        _compile_node_code(code)

    def test_includes_extra_kwargs(self) -> None:
        node = _make_codegen_node(
            "scenarioExpander",
            {
                "quote_id": "policy_id",
                "column_name": "sv",
                "min_value": 0.5,
                "max_value": 1.5,
                "steps": 11,
            },
            label="Expand",
        )
        code = _node_to_code(node, source_names=["upstream"])
        assert 'config="config/expander/Expand.json"' in code
        _compile_node_code(code)

    def test_empty_config(self) -> None:
        node = _make_codegen_node("scenarioExpander", {}, label="EmptyExpand")
        code = _node_to_code(node, source_names=["data"])
        assert "def EmptyExpand(data: pl.LazyFrame)" in code
        assert "expand_scenarios_from_config(data" in code
        _compile_node_code(code)

    def test_no_sources_uses_df_param(self) -> None:
        node = _make_codegen_node(
            "scenarioExpander",
            {"column_name": "sv", "steps": 5},
            label="NoSrcExpand",
        )
        code = _node_to_code(node, source_names=[])
        assert "df: pl.LazyFrame" in code
        assert "expand_scenarios_from_config(df" in code
        _compile_node_code(code)

    def test_skips_empty_config_values(self) -> None:
        """Empty string and None values should not appear as decorator kwargs."""
        node = _make_codegen_node(
            "scenarioExpander",
            {
                "quote_id": "",
                "column_name": None,
                "steps": 21,
            },
            label="PartialExpand",
        )
        code = _node_to_code(node, source_names=["data"])
        # The inline decorator (before config replacement) should NOT
        # emit empty kwargs.  But after config replacement the decorator
        # is just config="...".
        assert 'config="config/expander/PartialExpand.json"' in code
        _compile_node_code(code)

    def test_with_user_code_explicit(self) -> None:
        """Scenario expander with explicit assignment Polars code generates sentinel."""
        node = _make_codegen_node(
            "scenarioExpander",
            {
                "column_name": "sv",
                "steps": 5,
                "code": 'df = df.filter(pl.col("sv") > 0.9)',
            },
            label="FilteredExpand",
        )
        code = _node_to_code(node, source_names=["upstream"])
        assert '.filter(pl.col("sv") > 0.9)' in code
        assert "return df" in code
        _compile_node_code(code)

    def test_with_user_code_assignment(self) -> None:
        """Scenario expander with assignment-style user code."""
        node = _make_codegen_node(
            "scenarioExpander",
            {
                "column_name": "sv",
                "steps": 3,
                "code": 'df = df.with_columns(pl.col("sv").alias("factor"))',
            },
            label="AssignExpand",
        )
        code = _node_to_code(node, source_names=["data"])
        assert "df = expand_scenarios_from_config(data" in code
        assert "df = df.with_columns" in code
        _compile_node_code(code)

    def test_empty_code_uses_passthrough(self) -> None:
        """Empty code string produces passthrough (no sentinel)."""
        node = _make_codegen_node(
            "scenarioExpander",
            {"column_name": "sv", "steps": 5, "code": ""},
            label="PassExpand",
        )
        code = _node_to_code(node, source_names=["data"])
        assert "expand_scenarios_from_config(data" in code
        _compile_node_code(code)


# ---------------------------------------------------------------------------
# _gen_optimiser
# ---------------------------------------------------------------------------


class TestGenOptimiser:
    """Tests for optimiser code generation."""

    def test_basic_optimiser(self) -> None:
        node = _make_codegen_node(
            "optimiser",
            {
                "mode": "online",
                "quote_id": "quote_id",
                "objective": "expected_income",
                "constraints": {"loss_ratio": {"min": 0.5, "max": 0.7}},
            },
            label="PriceOpt",
        )
        code = _node_to_code(node, source_names=["scenarios"])
        assert 'config="config/optimisation/PriceOpt.json"' in code
        assert "def PriceOpt(scenarios: pl.LazyFrame)" in code
        assert "return scenarios" in code
        _compile_node_code(code)

    def test_optimiser_with_many_kwargs(self) -> None:
        node = _make_codegen_node(
            "optimiser",
            {
                "mode": "online",
                "quote_id": "qid",
                "scenario_index": "idx",
                "scenario_value": "sv",
                "objective": "profit",
                "max_iter": 100,
                "tolerance": 0.001,
            },
            label="Optimizer",
        )
        code = _node_to_code(node, source_names=["expanded"])
        assert 'config="config/optimisation/Optimizer.json"' in code
        _compile_node_code(code)

    def test_optimiser_empty_config(self) -> None:
        node = _make_codegen_node("optimiser", {}, label="EmptyOpt")
        code = _node_to_code(node, source_names=["data"])
        assert "def EmptyOpt(data: pl.LazyFrame)" in code
        assert "return data" in code
        _compile_node_code(code)

    def test_optimiser_no_sources(self) -> None:
        node = _make_codegen_node(
            "optimiser",
            {"mode": "online"},
            label="NoSrcOpt",
        )
        code = _node_to_code(node, source_names=[])
        assert "df: pl.LazyFrame" in code
        assert "return df" in code
        _compile_node_code(code)

    def test_optimiser_skips_none_kwargs(self) -> None:
        node = _make_codegen_node(
            "optimiser",
            {
                "mode": "online",
                "quote_id": None,
                "objective": "",
                "constraints": [],
            },
            label="Sparse",
        )
        code = _node_to_code(node, source_names=["data"])
        assert 'config="config/optimisation/Sparse.json"' in code
        _compile_node_code(code)


# ---------------------------------------------------------------------------
# _gen_explore
# ---------------------------------------------------------------------------


class TestGenExplore:
    """Tests for explore code generation."""

    def test_basic_explore_is_passthrough_analysis_sink(self) -> None:
        node = _make_codegen_node("explore", {}, label="InspectClaims")

        code = _node_to_code(node, source_names=["claims"])

        assert "@pipeline.explore(" in code
        assert "def InspectClaims(claims: pl.LazyFrame)" in code
        assert "return claims" in code
        assert "config/" not in code
        _compile_node_code(code)

    def test_explore_with_polars_code_generates_transform_body(self) -> None:
        node = _make_codegen_node(
            "explore",
            {
                "code": (
                    "df = df.filter(pl.col('premium') > 0)"
                    ".with_columns((pl.col('premium') * 2).alias('double_premium'))"
                )
            },
            label="InspectClaims",
        )

        code = _node_to_code(node, source_names=["claims"])

        assert "@pipeline.explore(" in code
        assert "def InspectClaims(claims: pl.LazyFrame)" in code
        assert "df = claims" in code
        assert ".filter(pl.col('premium') > 0)" in code
        assert "return df" in code
        assert "return claims" not in code
        assert "config/" not in code
        _compile_node_code(code)

    def test_no_sources_raise(self) -> None:
        node = _make_codegen_node("explore", {}, label="Inspect")

        with pytest.raises(ParseError, match="exactly one incoming edge"):
            _node_to_code(node, source_names=[])

    def test_multiple_sources_raise(self) -> None:
        node = _make_codegen_node("explore", {}, label="Inspect")

        with pytest.raises(ParseError, match="exactly one incoming edge"):
            _node_to_code(node, source_names=["left", "right"])

    def test_explore_with_overview_emits_decorator_kwarg(self) -> None:
        node = _make_codegen_node(
            "explore",
            {"overview": {"dataset_snapshot": True}},
            label="InspectClaims",
        )

        code = _node_to_code(node, source_names=["claims"])

        # Decorator must carry the overview kwarg as a literal dict.  Note that
        # ``_node_to_code`` post-injects ``contract=...`` into the same decorator
        # call, so we only assert on the overview substring (kwarg ordering is
        # an implementation detail of contract injection).
        assert "@pipeline.explore(" in code
        assert "overview={'dataset_snapshot': True}" in code
        assert "def InspectClaims(claims: pl.LazyFrame)" in code
        _compile_node_code(code)

    def test_explore_without_overview_emits_bare_decorator(self) -> None:
        node = _make_codegen_node("explore", {}, label="InspectClaims")

        code = _node_to_code(node, source_names=["claims"])

        # No overview = no overview kwarg.  We don't assert ``()`` literally
        # because ``_node_to_code`` injects ``contract=...`` into the same call.
        assert "@pipeline.explore(" in code
        assert "overview=" not in code
        _compile_node_code(code)

    def test_explore_with_code_and_overview_emits_both(self) -> None:
        node = _make_codegen_node(
            "explore",
            {
                "code": (
                    "df = df.filter(pl.col('premium') > 0)"
                    ".with_columns((pl.col('premium') * 2).alias('double_premium'))"
                ),
                "overview": {"dataset_snapshot": True},
            },
            label="InspectClaims",
        )

        code = _node_to_code(node, source_names=["claims"])

        assert "@pipeline.explore(" in code
        assert "overview={'dataset_snapshot': True}" in code
        assert "df = claims" in code
        assert ".filter(pl.col('premium') > 0)" in code
        assert "return df" in code
        _compile_node_code(code)

    def test_explore_with_empty_overview_omits_decorator_kwarg(self) -> None:
        node = _make_codegen_node(
            "explore",
            {"overview": {}},
            label="InspectClaims",
        )

        code = _node_to_code(node, source_names=["claims"])

        # Empty overview must NOT pollute the decorator.
        assert "@pipeline.explore(" in code
        assert "overview=" not in code
        _compile_node_code(code)

    def test_explore_with_schema_emits_decorator_kwarg(self) -> None:
        node = _make_codegen_node(
            "explore",
            {"overview": {"schema": True}},
            label="InspectClaims",
        )

        code = _node_to_code(node, source_names=["claims"])

        assert "@pipeline.explore(" in code
        assert "overview={'schema': True}" in code
        assert "def InspectClaims(claims: pl.LazyFrame)" in code
        _compile_node_code(code)

    def test_explore_with_both_overview_toggles_emits_decorator_kwarg(self) -> None:
        import ast

        node = _make_codegen_node(
            "explore",
            {"overview": {"dataset_snapshot": True, "schema": True}},
            label="InspectClaims",
        )

        code = _node_to_code(node, source_names=["claims"])

        # Decorator must carry both keys.  Parse the emitted module rather
        # than substring-asserting because dict-literal ordering inside the
        # decorator is an implementation detail.
        module = ast.parse(code)
        function_defs = [n for n in module.body if isinstance(n, ast.FunctionDef)]
        assert function_defs, "expected an explore function in emitted code"
        explore_decorator = next(
            d
            for d in function_defs[0].decorator_list
            if isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and d.func.attr == "explore"
        )
        overview_kwarg = next(kw for kw in explore_decorator.keywords if kw.arg == "overview")
        overview_value = ast.literal_eval(overview_kwarg.value)
        assert overview_value == {"dataset_snapshot": True, "schema": True}
        _compile_node_code(code)

    def test_explore_with_concise_overview_cards_emits_decorator_kwarg(self) -> None:
        import ast

        node = _make_codegen_node(
            "explore",
            {
                "overview": {
                    "dataset_snapshot": True,
                    "schema": True,
                    "numeric_summary": True,
                    "categorical_summary": True,
                    "data_quality": True,
                }
            },
            label="InspectClaims",
        )

        code = _node_to_code(node, source_names=["claims"])

        module = ast.parse(code)
        function_defs = [n for n in module.body if isinstance(n, ast.FunctionDef)]
        explore_decorator = next(
            d
            for d in function_defs[0].decorator_list
            if isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and d.func.attr == "explore"
        )
        overview_kwarg = next(kw for kw in explore_decorator.keywords if kw.arg == "overview")
        overview_value = ast.literal_eval(overview_kwarg.value)
        assert overview_value == {
            "dataset_snapshot": True,
            "schema": True,
            "numeric_summary": True,
            "categorical_summary": True,
            "data_quality": True,
        }
        _compile_node_code(code)

    def test_explore_with_invalid_overview_fails_loudly(self) -> None:
        node = _make_codegen_node(
            "explore",
            {"overview": {"schema": "yes"}},
            label="InspectClaims",
        )

        with pytest.raises(ConfigError, match="toggle values must be booleans"):
            _node_to_code(node, source_names=["claims"])

    @pytest.mark.parametrize("overview", ["", [], False, None])
    def test_explore_with_falsey_invalid_overview_fails_loudly(self, overview) -> None:
        node = _make_codegen_node(
            "explore",
            {"overview": overview},
            label="InspectClaims",
        )

        with pytest.raises(ConfigError, match="must be a dict"):
            _node_to_code(node, source_names=["claims"])


# ---------------------------------------------------------------------------
# Full graph round-trip with these node types
# ---------------------------------------------------------------------------


class TestGraphToCodeWithBuilders:
    """Integration tests: graph_to_code with specific builder node types."""

    def test_pipeline_with_banding_compiles(self) -> None:
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "Source",
                            "nodeType": "dataSource",
                            "config": {"path": "data.parquet"},
                        },
                    },
                    {
                        "id": "band",
                        "data": {
                            "label": "Banding",
                            "nodeType": "banding",
                            "config": {
                                "factors": [
                                    {
                                        "column": "age",
                                        "outputColumn": "age_band",
                                        "banding": "continuous",
                                        "rules": [
                                            {
                                                "op1": ">=",
                                                "val1": 0,
                                                "op2": "<",
                                                "val2": 50,
                                                "assignment": "u50",
                                            }
                                        ],
                                    }
                                ],
                            },
                        },
                    },
                ],
                "edges": [{"id": "e1", "source": "src", "target": "band"}],
            }
        )
        code = graph_to_code(graph)
        assert "def Source()" in code
        assert "def Banding(Source: pl.LazyFrame)" in code
        assert 'pipeline.connect("Source", "Banding")' in code
        compile(code, "<test>", "exec")

    def test_pipeline_with_scenario_expander_compiles(self) -> None:
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "Data",
                            "nodeType": "dataSource",
                            "config": {"path": "data.parquet"},
                        },
                    },
                    {
                        "id": "exp",
                        "data": {
                            "label": "Expand",
                            "nodeType": "scenarioExpander",
                            "config": {
                                "column_name": "sv",
                                "min_value": 0.8,
                                "max_value": 1.2,
                                "steps": 5,
                            },
                        },
                    },
                ],
                "edges": [{"id": "e1", "source": "src", "target": "exp"}],
            }
        )
        code = graph_to_code(graph)
        assert "def Expand(Data: pl.LazyFrame)" in code
        assert 'pipeline.connect("Data", "Expand")' in code
        compile(code, "<test>", "exec")

    def test_pipeline_with_optimiser_compiles(self) -> None:
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "Data",
                            "nodeType": "dataSource",
                            "config": {"path": "data.parquet"},
                        },
                    },
                    {
                        "id": "opt",
                        "data": {
                            "label": "Optimise",
                            "nodeType": "optimiser",
                            "config": {
                                "mode": "online",
                                "objective": "profit",
                            },
                        },
                    },
                ],
                "edges": [{"id": "e1", "source": "src", "target": "opt"}],
            }
        )
        code = graph_to_code(graph)
        assert "def Optimise(Data: pl.LazyFrame)" in code
        assert 'pipeline.connect("Data", "Optimise")' in code
        compile(code, "<test>", "exec")

    def test_pipeline_with_explore_compiles(self) -> None:
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "src",
                        "data": {
                            "label": "Claims",
                            "nodeType": "dataSource",
                            "config": {"path": "claims.parquet"},
                        },
                    },
                    {
                        "id": "explore",
                        "data": {
                            "label": "Explore Claims",
                            "nodeType": "explore",
                            "config": {},
                        },
                    },
                ],
                "edges": [{"id": "e1", "source": "src", "target": "explore"}],
            }
        )
        code = graph_to_code(graph)
        assert "def Explore_Claims(Claims: pl.LazyFrame)" in code
        assert 'pipeline.connect("Claims", "Explore_Claims")' in code
        compile(code, "<test>", "exec")

    def test_pipeline_with_api_input_compiles(self) -> None:
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "api",
                        "data": {
                            "label": "API",
                            "nodeType": "apiInput",
                            "config": {"path": "data/input.parquet"},
                        },
                    },
                    {
                        "id": "t",
                        "data": {
                            "label": "Process",
                            "nodeType": "polars",
                            "config": {"code": "df = df.with_columns(y=pl.lit(1))"},
                        },
                    },
                ],
                "edges": [{"id": "e1", "source": "api", "target": "t"}],
            }
        )
        code = graph_to_code(graph)
        assert "def API()" in code
        assert "def Process(API: pl.LazyFrame)" in code
        assert 'pipeline.connect("API", "Process")' in code
        compile(code, "<test>", "exec")

    def test_pipeline_with_constant_compiles(self) -> None:
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "c",
                        "data": {
                            "label": "Params",
                            "nodeType": "constant",
                            "config": {
                                "values": [
                                    {"name": "rate", "value": "0.05"},
                                    {"name": "cap", "value": "1000"},
                                ],
                            },
                        },
                    },
                ],
                "edges": [],
            }
        )
        code = graph_to_code(graph)
        assert "def Params()" in code
        # Constant nodes keep the inline decorator (no config folder)
        # so we check the LazyFrame data dict is present
        assert '"rate"' in code
        assert '"cap"' in code
        compile(code, "<test>", "exec")

    def test_full_pricing_pipeline_compiles(self) -> None:
        """A realistic multi-node pipeline: source -> banding -> expander -> optimiser -> output."""
        graph = _g(
            {
                "nodes": [
                    {
                        "id": "s",
                        "data": {
                            "label": "Source",
                            "nodeType": "dataSource",
                            "config": {"path": "d.parquet"},
                        },
                    },
                    {
                        "id": "b",
                        "data": {
                            "label": "Band",
                            "nodeType": "banding",
                            "config": {
                                "factors": [
                                    {
                                        "column": "age",
                                        "outputColumn": "age_band",
                                        "banding": "continuous",
                                        "rules": [
                                            {
                                                "op1": ">=",
                                                "val1": 0,
                                                "op2": "<",
                                                "val2": 50,
                                                "assignment": "u50",
                                            }
                                        ],
                                    }
                                ],
                            },
                        },
                    },
                    {
                        "id": "e",
                        "data": {
                            "label": "Expand",
                            "nodeType": "scenarioExpander",
                            "config": {"column_name": "sv", "steps": 5},
                        },
                    },
                    {
                        "id": "o",
                        "data": {
                            "label": "Opt",
                            "nodeType": "optimiser",
                            "config": {"mode": "online", "objective": "profit"},
                        },
                    },
                    {
                        "id": "out",
                        "data": {
                            "label": "Result",
                            "nodeType": "output",
                            "config": make_output_config(["age", "sv"]),
                        },
                    },
                ],
                "edges": [
                    {"id": "e1", "source": "s", "target": "b"},
                    {"id": "e2", "source": "b", "target": "e"},
                    {"id": "e3", "source": "e", "target": "o"},
                    {"id": "e4", "source": "o", "target": "out"},
                ],
            }
        )
        code = graph_to_code(graph, pipeline_name="pricing")
        compile(code, "<test>", "exec")
        # Verify correct edges
        assert 'pipeline.connect("Source", "Band")' in code
        assert 'pipeline.connect("Band", "Expand")' in code
        assert 'pipeline.connect("Expand", "Opt")' in code
        assert 'pipeline.connect("Opt", "Result")' in code


# ---------------------------------------------------------------------------
# Exec-based validation: run generated function bodies against real data
# ---------------------------------------------------------------------------


class TestCodegenExecValidation:
    """Execute generated code against real DataFrames to verify bodies work.

    Goes beyond ``compile()`` (syntax-only) to catch undefined names,
    wrong column references, and type errors in generated function bodies.
    """

    @staticmethod
    def _exec_generated(code: str, input_df=None):
        """Exec the pipeline code and call the last defined function.

        Returns the result of calling the function with *input_df*.
        """
        ns: dict = {"__file__": str(Path.cwd() / "__exec_test__.py")}
        exec(
            "import polars as pl\nimport haute\n"
            "from pathlib import Path\n"
            "pipeline = haute.Pipeline('exec_test')\n\n"
            f"{code}\n",
            ns,
        )
        # Find all functions defined via @pipeline.<type> decorators
        func_names = [
            name
            for name, obj in ns.items()
            if callable(obj)
            and not name.startswith("_")
            and name
            not in (
                "pl",
                "haute",
                "pipeline",
            )
        ]
        assert func_names, "No functions found in generated code"
        fn = ns[func_names[-1]]
        if input_df is not None:
            return fn(input_df)
        return fn()

    def test_data_source_exec_produces_lazyframe(self) -> None:
        """dataSource code that references a real parquet file executes."""
        import polars as pl

        node = _make_codegen_node(
            "dataSource",
            {"path": "tests/fixtures/data/policies.parquet", "sourceType": "flat_file"},
            label="load_policies",
        )
        code = _node_to_code(node)
        result = self._exec_generated(code)
        assert isinstance(result, pl.LazyFrame)
        assert len(result.collect()) > 0

    def test_data_source_exec_uses_declared_schema_boundary(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Generated dataSource code should honour shared source schema config."""
        monkeypatch.chdir(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        csv_path = data_dir / "quotes.csv"
        csv_path.write_text("quote_id,premium\n001,10.5\n", encoding="utf-8")

        node = _make_codegen_node(
            "dataSource",
            {
                "path": "data/quotes.csv",
                "sourceType": "flat_file",
                "schema_overrides": {"quote_id": "String", "premium": "Float64"},
            },
            label="load_quotes",
        )

        code = _node_to_code(node)

        assert "read_data_source" in code
        assert "scan_csv" not in code
        result = self._exec_generated(code)
        assert isinstance(result, pl.LazyFrame)
        collected = result.collect()
        assert collected["quote_id"].to_list() == ["001"]
        assert collected.schema["quote_id"] == pl.String

    def test_api_input_exec_produces_lazyframe(self) -> None:
        """apiInput code for a JSON file compiles and contains v2 shred markers.

        v2 generated code requires a live config file and pre-built per-port
        cache, so we verify compilation and v2 structural markers rather than
        executing the generated function directly.
        """
        node = _make_codegen_node(
            "apiInput",
            {"path": "tests/fixtures/data/api_input.json"},
            label="quotes",
        )
        code = _node_to_code(node)
        _compile_node_code(code)
        assert "load_v2_api_source" in code
        assert "validate_v2_schema" in code

    def test_json_api_input_exec_fails_loudly_without_v2_schema(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Generated JSON apiInput reads config beside the file and rejects drafts."""
        project = tmp_path / "project"
        config_dir = project / "config" / "quote_input"
        config_dir.mkdir(parents=True)
        (config_dir / "quotes.json").write_text('{"path": "data/quotes.json"}', encoding="utf-8")

        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        node = _make_codegen_node(
            "apiInput",
            {"path": "data/quotes.json"},
            label="quotes",
        )
        code = _node_to_code(node)
        ns: dict = {"__file__": str(project / "main.py")}
        exec(
            "import polars as pl\nimport haute\n"
            "from pathlib import Path\n"
            "pipeline = haute.Pipeline('exec_test')\n\n"
            f"{code}\n",
            ns,
        )

        with pytest.raises(RuntimeError, match="no v2 schema"):
            ns["quotes"]()

    def test_json_api_input_exec_fails_loudly_without_selected_columns(
        self,
        tmp_path: Path,
    ) -> None:
        """Generated JSON apiInput separates emit-without-columns from cache misses."""
        config_dir = tmp_path / "config" / "quote_input"
        config_dir.mkdir(parents=True)
        (config_dir / "quotes.json").write_text(
            """
            {
              "path": "data/quotes.json",
              "tables": [
                {
                  "path": "$[:]",
                  "label": "quotes",
                  "emit": true,
                  "columns": [
                    {
                      "path": "$[:].quote_id",
                      "name": "quote_id",
                      "type": "str",
                      "selected": false
                    }
                  ]
                }
              ]
            }
            """,
            encoding="utf-8",
        )

        node = _make_codegen_node(
            "apiInput",
            {"path": "data/quotes.json"},
            label="quotes",
        )
        code = _node_to_code(node)
        ns: dict = {"__file__": str(tmp_path / "main.py")}
        exec(
            "import polars as pl\nimport haute\n"
            "from pathlib import Path\n"
            "pipeline = haute.Pipeline('exec_test')\n\n"
            f"{code}\n",
            ns,
        )

        with pytest.raises(
            RuntimeError, match="emit-true tables but none has any selected columns"
        ):
            ns["quotes"]()

    def test_output_exec_is_passthrough(self) -> None:
        """v2: the generated OUTPUT body is a plain passthrough.

        Column selection / assembly no longer lives in the generated code — it
        moved to the runtime assembler driven by the sidecar ``outputMapping``.
        So executing the generated function body returns its input unchanged
        (all columns survive); the mapping-driven projection is exercised by the
        ``_build_output`` / assembler tests, not here.
        """
        import polars as pl

        node = _make_codegen_node(
            "output",
            {
                "outputMapping": [
                    {
                        "source_port": "upstream",
                        "source_column": "premium",
                        "output_path": "$[:].premium",
                        "enabled": True,
                    },
                    {
                        "source_port": "upstream",
                        "source_column": "Area",
                        "output_path": "$[:].Area",
                        "enabled": True,
                    },
                ],
                "outputFormat": "json",
            },
            label="result",
        )
        code = _node_to_code(node, source_names=["upstream"])
        input_lf = pl.DataFrame(
            {
                "premium": [1.0],
                "Area": ["A"],
                "extra": [99],
            }
        ).lazy()
        result = self._exec_generated(code, input_df=input_lf)
        assert isinstance(result, pl.LazyFrame)
        collected = result.collect()
        assert set(collected.columns) == {"premium", "Area", "extra"}

    def test_multi_frame_output_dedupes_duplicate_params(self) -> None:
        """A multi-frame OUTPUT (one apiInput feeding several edges) must codegen
        VALID Python. Duplicate parameter names are a compile-time SyntaxError —
        ast.parse tolerates them (so the canvas works) but the file can't be
        imported/deployed — so the params must be de-duplicated and the result
        must compile()."""
        node = _make_codegen_node(
            "output",
            {
                "outputMapping": [
                    {
                        "source_port": "quotes",
                        "source_column": "quote_id",
                        "output_path": "$[:].quote_id",
                        "enabled": True,
                    },
                ],
                "outputFormat": "json",
            },
            label="Quote_Response",
        )
        # Four edges, all from the same multi-frame source node `quotes`.
        code = _node_to_code(node, source_names=["quotes", "quotes", "quotes", "quotes"])
        # Distinct, valid params — not four bare `quotes`.
        assert "quotes: pl.LazyFrame, quotes_2: pl.LazyFrame" in code
        assert "quotes_3: pl.LazyFrame, quotes_4: pl.LazyFrame" in code
        # The passthrough body returns the (unchanged) first param.
        assert "return quotes\n" in code
        # compile() (unlike ast.parse) rejects duplicate arg names — must pass.
        _compile_node_code(code)

    def test_banding_exec_applies_sidecar_config(self, tmp_path: Path) -> None:
        """The generated banding body APPLIES the sidecar config when called.

        A passthrough body would return the input unchanged — the saved file
        would silently skip banding on a standalone run.  Calling the
        generated function directly must produce the banded column.
        """
        import json

        import polars as pl

        factors = [
            {
                "column": "age",
                "outputColumn": "age_band",
                "banding": "continuous",
                "rules": [
                    {"op1": ">=", "val1": 0, "op2": "<", "val2": 50, "assignment": "young"},
                ],
            }
        ]
        config_dir = tmp_path / "config" / "banding"
        config_dir.mkdir(parents=True)
        (config_dir / "band_age.json").write_text(
            json.dumps({"factors": factors}),
            encoding="utf-8",
        )

        node = _make_codegen_node("banding", {"factors": factors}, label="band_age")
        # No source_names → param is 'df'; the body applies the sidecar to it.
        code = _node_to_code(node, source_names=[])
        ns: dict = {"__file__": str(tmp_path / "main.py")}
        exec(
            "import polars as pl\nimport haute\n"
            "from pathlib import Path\n"
            "pipeline = haute.Pipeline('exec_test')\n\n"
            f"{code}\n",
            ns,
        )
        input_lf = pl.DataFrame({"age": [25, 55]}).lazy()
        result = ns["band_age"](input_lf)
        assert isinstance(result, pl.LazyFrame)
        collected = result.collect()
        assert collected["age_band"].to_list() == ["young", None], (
            "generated banding body must apply the sidecar config, not pass through"
        )

    def test_model_score_body_references_valid_names(self) -> None:
        """modelScore generated code compiles and defines a callable function.

        Full exec not possible without a live MLflow backend, but we verify
        the generated function is syntactically valid and defines the expected
        function name.
        """
        node = _make_codegen_node(
            "modelScore",
            {
                "sourceType": "run",
                "task": "regression",
                "output_column": "prediction",
                "run_id": "abc123",
            },
            label="score",
        )
        code = _node_to_code(node, source_names=["features"])
        _compile_node_code(code)
        assert "def score(features: pl.LazyFrame)" in code

    def test_transform_with_code_exec(self) -> None:
        """transform code with real Polars expression executes correctly."""
        import polars as pl

        node = _make_codegen_node(
            "polars",
            {"code": 'df = df.with_columns(doubled=pl.col("x") * 2)'},
            label="double_it",
        )
        code = _node_to_code(node, source_names=["src"])
        input_lf = pl.DataFrame({"x": [1.0, 2.0, 3.0]}).lazy()
        result = self._exec_generated(code, input_df=input_lf)
        collected = result.collect()
        assert "doubled" in collected.columns
        assert collected["doubled"].to_list() == [2.0, 4.0, 6.0]


# ---------------------------------------------------------------------------
# B19: Sink templates use bounded_sink instead of hardcoded collect+write
# ---------------------------------------------------------------------------


class TestGenDataSink:
    """Tests for data sink code generation - must delegate to bounded_sink."""

    def test_parquet_sink_uses_bounded_sink(self) -> None:
        """Parquet sink template should import and call bounded_sink."""
        node = _make_codegen_node(
            "dataSink",
            {"path": "output/results.parquet", "format": "parquet"},
            label="WriteResults",
        )
        code = _node_to_code(node, source_names=["scored"])
        assert "from haute._polars_utils import bounded_sink" in code
        assert 'bounded_sink(scored, Path(__file__).parent / "output/results.parquet")' in code
        # Must NOT contain the old hardcoded pattern
        assert ".collect(engine=" not in code
        assert ".write_parquet(" not in code
        _compile_node_code(code)

    def test_csv_sink_uses_bounded_sink(self) -> None:
        """CSV sink template should import and call bounded_sink with fmt='csv'."""
        node = _make_codegen_node(
            "dataSink",
            {"path": "output/report.csv", "format": "csv"},
            label="WriteCSV",
        )
        code = _node_to_code(node, source_names=["data"])
        assert "from haute._polars_utils import bounded_sink" in code
        assert 'bounded_sink(data, Path(__file__).parent / "output/report.csv", fmt="csv")' in code
        assert ".write_csv(" not in code
        _compile_node_code(code)

    def test_sink_default_format_is_parquet(self) -> None:
        """When no format is specified, default to parquet bounded_sink call."""
        node = _make_codegen_node(
            "dataSink",
            {"path": "out.parquet"},
            label="DefaultSink",
        )
        code = _node_to_code(node, source_names=["df"])
        assert "bounded_sink" in code
        # Default parquet call should not have fmt= kwarg
        assert 'fmt="csv"' not in code
        _compile_node_code(code)

    def test_sink_returns_first_source(self) -> None:
        """Sink should return the input LazyFrame for downstream chaining."""
        node = _make_codegen_node(
            "dataSink",
            {"path": "out.parquet", "format": "parquet"},
            label="SinkNode",
        )
        code = _node_to_code(node, source_names=["input_df"])
        assert "return input_df" in code

    def test_sink_with_multiple_sources(self) -> None:
        """Sink with multiple sources uses the first one."""
        node = _make_codegen_node(
            "dataSink",
            {"path": "combined.parquet", "format": "parquet"},
            label="MultiSink",
        )
        code = _node_to_code(node, source_names=["a", "b", "c"])
        assert "bounded_sink(a," in code
        assert "return a" in code
        _compile_node_code(code)
