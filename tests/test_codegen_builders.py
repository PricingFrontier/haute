"""Tests for codegen builder functions — _gen_api_input, _gen_banding, etc.

Follows the same pattern as test_codegen.py: build a node, call
_node_to_code (which dispatches to the type-specific builder), then
verify the generated function. A configured node without code is a
declaration (its inputs and a ``...`` body); one with code is a hook that
receives the configured result as ``df``. The exec tests run the generated
function the way a standalone ``pipeline.run()`` runs it: the decorator
performs the node's configured work.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import polars as pl
import pytest

import haute
from haute._api_input_schema import ApiInputSchemaError
from haute._codegen_builders import _build_extra_kwargs
from haute._config_io import collect_node_configs
from haute._mlflow_io import ScoringModel
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


def _load_generated(code: str, directory: Path) -> haute.Pipeline:
    """Exec generated node code as a pipeline module saved at *directory*/main.py.

    A decorator resolves its ``config=`` path against the directory of the
    file that defines the function, so sidecars are read from *directory*.
    """
    namespace: dict = {"__file__": str(directory / "main.py")}
    exec(
        f"import polars as pl\nimport haute\npipeline = haute.Pipeline('exec_test')\n\n{code}\n",
        namespace,
    )
    pipeline: haute.Pipeline = namespace["pipeline"]
    return pipeline


def _run_generated(code: str, *inputs: object, directory: Path) -> object:
    """Run the generated node as a standalone ``pipeline.run()`` runs it."""
    (node,) = _load_generated(code, directory).nodes
    return node(*inputs)


def _collect(frame: object) -> pl.DataFrame:
    """Collect a LazyFrame; pass a DataFrame through."""
    if isinstance(frame, pl.LazyFrame):
        return frame.collect()
    assert isinstance(frame, pl.DataFrame)
    return frame


def _stub_scoring_model() -> ScoringModel:
    """A CatBoost-flavoured model over a mock estimator predicting 0.5 per row."""
    model = MagicMock()
    model.feature_names_ = ["a", "b"]
    model.predict.side_effect = lambda frame: np.full(len(frame), 0.5)
    del model.predict_proba
    return ScoringModel(
        model=model,
        feature_names=["a", "b"],
        cat_feature_names=frozenset(),
        flavor="catboost",
    )


# ---------------------------------------------------------------------------
# _build_extra_kwargs helper
# ---------------------------------------------------------------------------


class TestBuildExtraKwargs:
    """Unit tests for the _build_extra_kwargs utility."""

    def test_includes_present_keys(self) -> None:
        config = {"a": 1, "b": "hello", "c": [1, 2]}
        result = _build_extra_kwargs(config, ("a", "b", "c"))
        assert result == [("a", 1), ("b", "hello"), ("c", [1, 2])]

    def test_skips_none_values(self) -> None:
        config = {"a": None, "b": 42}
        result = _build_extra_kwargs(config, ("a", "b"))
        assert result == [("b", 42)]

    def test_skips_empty_string(self) -> None:
        config = {"a": "", "b": "val"}
        result = _build_extra_kwargs(config, ("a", "b"))
        assert result == [("b", "val")]

    def test_skips_empty_list(self) -> None:
        config = {"a": [], "b": [1]}
        result = _build_extra_kwargs(config, ("a", "b"))
        assert result == [("b", [1])]

    def test_skips_missing_keys(self) -> None:
        config = {"a": 10}
        result = _build_extra_kwargs(config, ("a", "missing_key"))
        assert result == [("a", 10)]

    def test_keeps_falsy_values_that_are_not_empty(self) -> None:
        config = {"a": 0, "b": False, "c": {}}
        result = _build_extra_kwargs(config, ("a", "b", "c"))
        assert result == [("a", 0), ("b", False), ("c", {})]

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
        assert code == (
            '@pipeline.api_input(config="config/quote_input/PolicyData.json")\n'
            "def PolicyData(): ...\n"
        )
        assert "data/api_input.parquet" not in code
        _compile_node_code(code)

    def test_csv_api_input(self) -> None:
        node = _make_codegen_node(
            "apiInput",
            {"path": "data/input.csv"},
            label="CSVInput",
        )
        code = _node_to_code(node)
        assert code == (
            '@pipeline.api_input(config="config/quote_input/CSVInput.json")\ndef CSVInput(): ...\n'
        )
        assert "data/input.csv" not in code
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

        # The levels stay in the sidecar the shared reader loads; the
        # generated function only declares the node.
        assert code == (
            '@pipeline.api_input(config="config/quote_input/CategoricalInput.json")\n'
            "def CategoricalInput(): ...\n"
        )
        sidecar = collect_node_configs(_g({"nodes": [node.model_dump()], "edges": []}))
        assert json.loads(sidecar["config/quote_input/CategoricalInput.json"])[
            "categorical_levels"
        ] == {"region": ["north", "south"]}
        _compile_node_code(code)

    def test_json_api_input(self) -> None:
        node = _make_codegen_node(
            "apiInput",
            {"path": "data/quotes.json"},
            label="JSONInput",
        )
        code = _node_to_code(node)
        assert code == (
            '@pipeline.api_input(config="config/quote_input/JSONInput.json")\n'
            "def JSONInput(): ...\n"
        )
        assert "data/quotes.json" not in code
        _compile_node_code(code)

    def test_jsonl_api_input(self) -> None:
        node = _make_codegen_node(
            "apiInput",
            {"path": "data/quotes.jsonl"},
            label="JSONLInput",
        )
        code = _node_to_code(node)
        assert code == (
            '@pipeline.api_input(config="config/quote_input/JSONLInput.json")\n'
            "def JSONLInput(): ...\n"
        )
        assert "data/quotes.jsonl" not in code
        _compile_node_code(code)

    def test_api_input_with_row_id(self) -> None:
        """row_id_column lives in the sidecar; the decorator carries only its path."""
        node = _make_codegen_node(
            "apiInput",
            {"path": "data/api.parquet", "row_id_column": "policy_id"},
            label="WithRowID",
        )
        code = _node_to_code(node)
        assert 'config="config/quote_input/WithRowID.json"' in code
        assert "def WithRowID(): ...\n" in code
        assert "policy_id" not in code
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
        assert "def Empty(): ...\n" in code
        _compile_node_code(code)


# ---------------------------------------------------------------------------
# _gen_banding
# ---------------------------------------------------------------------------


class TestGenBanding:
    """Tests for banding code generation."""

    def test_single_breakpoints_factor(self) -> None:
        node = _make_codegen_node(
            "banding",
            {
                "factors": [
                    {
                        "column": "age",
                        "outputColumn": "age_band",
                        "banding": "breakpoints",
                        "rules": [
                            {"boundary": "25", "label": "young"},
                            {"boundary": "", "label": "adult"},
                        ],
                    }
                ],
            },
            label="AgeBanding",
        )
        code = _node_to_code(node, source_names=["data"])
        # A declaration: the decorator applies banding from the sidecar config
        # (the helper the executor calls), so a standalone run of the saved
        # file bands instead of passing the frame through. See
        # TestCodegenExecValidation.test_banding_exec_applies_sidecar_config.
        assert code == (
            '@pipeline.banding(config="config/banding/AgeBanding.json")\n'
            "def AgeBanding(data): ...\n"
        )
        assert "age_band" not in code
        _compile_node_code(code)

    def test_single_factor_with_default(self) -> None:
        node = _make_codegen_node(
            "banding",
            {
                "factors": [
                    {
                        "column": "age",
                        "outputColumn": "age_band",
                        "banding": "breakpoints",
                        "default": "unknown",
                        "rules": [{"boundary": "25", "label": "young"}],
                    }
                ],
            },
            label="WithDefault",
        )
        code = _node_to_code(node, source_names=["data"])
        assert 'config="config/banding/WithDefault.json"' in code
        assert "unknown" not in code
        _compile_node_code(code)

    def test_multi_factor_banding(self) -> None:
        node = _make_codegen_node(
            "banding",
            {
                "factors": [
                    {
                        "column": "age",
                        "outputColumn": "age_band",
                        "banding": "breakpoints",
                        "rules": [{"boundary": "50", "label": "u50"}],
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
        # Multi-factor emission is the same declaration as single-factor.
        assert code == (
            '@pipeline.banding(config="config/banding/MultiBand.json")\ndef MultiBand(data): ...\n'
        )
        _compile_node_code(code)

    def test_categorical_banding(self) -> None:
        """The categorical rules live in the sidecar; the decorator names it."""
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
        assert code == (
            '@pipeline.banding(config="config/banding/CatBand.json")\ndef CatBand(df_in): ...\n'
        )
        assert "categorical" not in code
        _compile_node_code(code)

    def test_empty_factors(self) -> None:
        node = _make_codegen_node("banding", {"factors": []}, label="Empty")
        code = _node_to_code(node, source_names=["data"])
        # Still generates a valid function
        assert "def Empty(data): ...\n" in code
        _compile_node_code(code)

    def test_no_sources_declares_no_parameters(self) -> None:
        node = _make_codegen_node(
            "banding",
            {
                "factors": [
                    {
                        "column": "x",
                        "outputColumn": "x_band",
                        "banding": "breakpoints",
                        "rules": [{"boundary": "10", "label": "low"}],
                    }
                ],
            },
            label="NoSrc",
        )
        code = _node_to_code(node, source_names=[])
        # A disconnected declaration has no inputs, so no parameters: there is
        # no phantom ``df`` input.
        assert "def NoSrc(): ...\n" in code
        assert "df" not in code
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
                "stepCount": 21,
                "step_column": "scenario_index",
            },
            label="Scenarios",
        )
        code = _node_to_code(node, source_names=["base_data"])
        # A declaration: the decorator expands the grid from the sidecar (not
        # a no-op passthrough), so a standalone pipeline.run() expands it.
        assert code == (
            '@pipeline.scenario_expander(config="config/expander/Scenarios.json")\n'
            "def Scenarios(base_data): ...\n"
        )
        _compile_node_code(code)

    def test_includes_extra_kwargs(self) -> None:
        node = _make_codegen_node(
            "scenarioExpander",
            {
                "quote_id": "policy_id",
                "column_name": "sv",
                "min_value": 0.5,
                "max_value": 1.5,
                "stepCount": 11,
            },
            label="Expand",
        )
        code = _node_to_code(node, source_names=["upstream"])
        assert 'config="config/expander/Expand.json"' in code
        _compile_node_code(code)

    def test_empty_config(self) -> None:
        node = _make_codegen_node("scenarioExpander", {}, label="EmptyExpand")
        code = _node_to_code(node, source_names=["data"])
        assert code == (
            '@pipeline.scenario_expander(config="config/expander/EmptyExpand.json")\n'
            "def EmptyExpand(data): ...\n"
        )
        _compile_node_code(code)

    def test_no_sources_declares_no_parameters(self) -> None:
        node = _make_codegen_node(
            "scenarioExpander",
            {"column_name": "sv", "stepCount": 5},
            label="NoSrcExpand",
        )
        code = _node_to_code(node, source_names=[])
        assert "def NoSrcExpand(): ...\n" in code
        assert "df" not in code
        _compile_node_code(code)

    def test_skips_empty_config_values(self) -> None:
        """Empty string and None values never reach the decorator: it names the sidecar only."""
        node = _make_codegen_node(
            "scenarioExpander",
            {
                "quote_id": "",
                "column_name": None,
                "stepCount": 21,
            },
            label="PartialExpand",
        )
        code = _node_to_code(node, source_names=["data"])
        assert code.startswith(
            '@pipeline.scenario_expander(config="config/expander/PartialExpand.json")\n'
        )
        _compile_node_code(code)

    def test_with_user_code_explicit(self) -> None:
        """Scenario expander with explicit assignment Polars code is a ``df`` hook."""
        node = _make_codegen_node(
            "scenarioExpander",
            {
                "column_name": "sv",
                "stepCount": 5,
                "code": 'df = df.filter(pl.col("sv") > 0.9)',
            },
            label="FilteredExpand",
        )
        code = _node_to_code(node, source_names=["upstream"])
        assert code == (
            '@pipeline.scenario_expander(config="config/expander/FilteredExpand.json")\n'
            "def FilteredExpand(df: pl.LazyFrame) -> pl.LazyFrame:\n"
            '    df = df.filter(pl.col("sv") > 0.9)\n'
            "    return df\n"
        )
        _compile_node_code(code)

    def test_with_user_code_assignment(self) -> None:
        """Assignment-style code runs on the expanded frame the decorator hands in as df."""
        node = _make_codegen_node(
            "scenarioExpander",
            {
                "column_name": "sv",
                "stepCount": 3,
                "code": 'df = df.with_columns(pl.col("sv").alias("factor"))',
            },
            label="AssignExpand",
        )
        code = _node_to_code(node, source_names=["data"])
        assert "def AssignExpand(df: pl.LazyFrame) -> pl.LazyFrame:\n" in code
        assert '    df = df.with_columns(pl.col("sv").alias("factor"))\n    return df\n' in code
        assert "expand_scenarios_from_config" not in code
        assert "data" not in code
        _compile_node_code(code)

    def test_empty_code_is_a_declaration(self) -> None:
        """An empty code string is no code: the node is a declaration, not a hook."""
        node = _make_codegen_node(
            "scenarioExpander",
            {"column_name": "sv", "stepCount": 5, "code": ""},
            label="PassExpand",
        )
        code = _node_to_code(node, source_names=["data"])
        assert code == (
            '@pipeline.scenario_expander(config="config/expander/PassExpand.json")\n'
            "def PassExpand(data): ...\n"
        )
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
        assert code == (
            '@pipeline.optimiser(config="config/optimisation/PriceOpt.json")\n'
            "def PriceOpt(scenarios): ...\n"
        )
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
        assert "max_iter" not in code
        _compile_node_code(code)

    def test_optimiser_empty_config(self) -> None:
        node = _make_codegen_node("optimiser", {}, label="EmptyOpt")
        code = _node_to_code(node, source_names=["data"])
        assert "def EmptyOpt(data): ...\n" in code
        _compile_node_code(code)

    def test_optimiser_no_sources(self) -> None:
        node = _make_codegen_node(
            "optimiser",
            {"mode": "online"},
            label="NoSrcOpt",
        )
        code = _node_to_code(node, source_names=[])
        assert "def NoSrcOpt(): ...\n" in code
        assert "df" not in code
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
        assert code.startswith('@pipeline.optimiser(config="config/optimisation/Sparse.json")\n')
        _compile_node_code(code)


# ---------------------------------------------------------------------------
# _gen_explore
# ---------------------------------------------------------------------------


class TestGenExplore:
    """Tests for explore code generation."""

    def test_basic_explore_is_passthrough_analysis_sink(self) -> None:
        node = _make_codegen_node("explore", {}, label="InspectClaims")

        code = _node_to_code(node, source_names=["claims"])

        # The decorator passes the input through; the function declares it.
        assert code == "@pipeline.explore\ndef InspectClaims(claims): ...\n"
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

        # A hook: the decorator hands the explored input in as ``df``, so the
        # body is the user's code verbatim and ``return df`` — no binding line.
        assert code == (
            "@pipeline.explore\n"
            "def InspectClaims(df: pl.LazyFrame) -> pl.LazyFrame:\n"
            "    df = df.filter(pl.col('premium') > 0)"
            ".with_columns((pl.col('premium') * 2).alias('double_premium'))\n"
            "    return df\n"
        )
        assert "claims" not in code
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

        # The decorator carries the overview keyword as a literal dict, and
        # nothing else: an Explore contract adds no information.
        assert code == (
            '@pipeline.explore(overview={"dataset_snapshot": True})\n'
            "def InspectClaims(claims): ...\n"
        )
        _compile_node_code(code)

    def test_explore_without_overview_emits_bare_decorator(self) -> None:
        node = _make_codegen_node("explore", {}, label="InspectClaims")

        code = _node_to_code(node, source_names=["claims"])

        # No overview = no keywords at all: the decorator is bare.
        assert code.startswith("@pipeline.explore\n")
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

        assert code.startswith(
            '@pipeline.explore(overview={"dataset_snapshot": True})\n'
            "def InspectClaims(df: pl.LazyFrame) -> pl.LazyFrame:\n"
        )
        assert "    df = df.filter(pl.col('premium') > 0)" in code
        assert code.endswith("    return df\n")
        _compile_node_code(code)

    def test_explore_with_empty_overview_omits_decorator_kwarg(self) -> None:
        node = _make_codegen_node(
            "explore",
            {"overview": {}},
            label="InspectClaims",
        )

        code = _node_to_code(node, source_names=["claims"])

        # Empty overview must NOT pollute the decorator.
        assert code.startswith("@pipeline.explore\n")
        assert "overview=" not in code
        _compile_node_code(code)

    def test_explore_with_schema_emits_decorator_kwarg(self) -> None:
        node = _make_codegen_node(
            "explore",
            {"overview": {"schema": True}},
            label="InspectClaims",
        )

        code = _node_to_code(node, source_names=["claims"])

        assert code == (
            '@pipeline.explore(overview={"schema": True})\ndef InspectClaims(claims): ...\n'
        )
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
# _gen_data_input
# ---------------------------------------------------------------------------


_PARQUET_INPUT = {
    "inputType": "file",
    "format": "parquet",
    "mode": "scan",
    "path": "data/policies.parquet",
    "arguments": {},
}


class TestGenDataInput:
    """A Data Input is a source: a declaration, or a ``df`` hook over the loaded data."""

    def test_data_input_without_code_is_a_declaration(self) -> None:
        node = _make_codegen_node("dataInput", _PARQUET_INPUT, label="load_policies")

        code = _node_to_code(node)

        assert code == (
            '@pipeline.data_input(config="config/data_input/load_policies.json")\n'
            "def load_policies(): ...\n"
        )
        assert "policies.parquet" not in code
        _compile_node_code(code)

    def test_data_input_with_code_is_a_df_hook(self) -> None:
        node = _make_codegen_node(
            "dataInput",
            {**_PARQUET_INPUT, "code": "df = df.filter(pl.col('policy_id') > 1)"},
            label="load_policies",
        )

        code = _node_to_code(node)

        # ``df`` is the loaded data; a Data Input has no other parameters.
        assert code == (
            '@pipeline.data_input(config="config/data_input/load_policies.json")\n'
            "def load_policies(df: pl.LazyFrame) -> pl.LazyFrame:\n"
            "    df = df.filter(pl.col('policy_id') > 1)\n"
            "    return df\n"
        )
        _compile_node_code(code)


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
                            "nodeType": "dataInput",
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
                                        "banding": "breakpoints",
                                        "rules": [{"boundary": "50", "label": "u50"}],
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
        assert "def Source(): ...\n" in code
        assert "def Banding(Source): ...\n" in code
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
                            "nodeType": "dataInput",
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
                                "stepCount": 5,
                            },
                        },
                    },
                ],
                "edges": [{"id": "e1", "source": "src", "target": "exp"}],
            }
        )
        code = graph_to_code(graph)
        assert "def Expand(Data): ...\n" in code
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
                            "nodeType": "dataInput",
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
        assert "def Optimise(Data): ...\n" in code
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
                            "nodeType": "dataInput",
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
        assert "@pipeline.explore\ndef Explore_Claims(Claims): ...\n" in code
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
                            "config": {
                                "path": "data/input.json",
                                "tables": [
                                    {
                                        "path": "$[:]",
                                        "label": "quotes",
                                        "emit": True,
                                        "row_id_column": None,
                                        "columns": [
                                            {
                                                "name": "id",
                                                "path": "$[:].id",
                                                "type": "int",
                                                "status": "Confirmed",
                                                "selected": True,
                                                "levels": None,
                                            }
                                        ],
                                    }
                                ],
                            },
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
                "edges": [
                    {
                        "id": "e1",
                        "source": "api",
                        "target": "t",
                        "sourceHandle": "quotes",
                    }
                ],
            }
        )
        code = graph_to_code(graph)
        assert "def API(): ...\n" in code
        assert "def Process(quotes: pl.LazyFrame) -> pl.LazyFrame:" in code
        assert 'pipeline.connect("API", "Process", source_port="quotes")' in code
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
        # A Constant is config-backed: the values live in its sidecar, which
        # the decorator reads when the file runs; nothing is copied into source.
        assert (
            '@pipeline.constant(config="config/constant/Params.json")\ndef Params(): ...\n' in code
        )
        assert '"rate"' not in code
        assert '"cap"' not in code
        sidecar = json.loads(collect_node_configs(graph)["config/constant/Params.json"])
        assert sidecar["values"] == [
            {"name": "rate", "value": "0.05"},
            {"name": "cap", "value": "1000"},
        ]
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
                            "nodeType": "dataInput",
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
                                        "banding": "breakpoints",
                                        "rules": [{"boundary": "50", "label": "u50"}],
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
                            "config": {"column_name": "sv", "stepCount": 5},
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
# Exec-based validation: run generated nodes against real data
# ---------------------------------------------------------------------------


class TestCodegenExecValidation:
    """Execute generated code against real DataFrames to verify it runs.

    Goes beyond ``compile()`` (syntax-only) to catch undefined names, wrong
    column references and type errors: each test registers the generated
    node on a pipeline and runs it the way a standalone ``pipeline.run()``
    does — the decorator's configured work, then the hook or transform body.
    """

    def test_data_source_exec_produces_lazyframe(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A dataInput declaration loads a real parquet file through its sidecar."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".git").mkdir()
        (tmp_path / "haute.toml").write_text('[project]\nname = "test"\n', encoding="utf-8")
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        pl.DataFrame({"policy_id": [1, 2]}).write_parquet(data_dir / "policies.parquet")
        node = _make_codegen_node("dataInput", _PARQUET_INPUT, label="load_policies")
        config_dir = tmp_path / "config" / "data_input"
        config_dir.mkdir(parents=True)
        (config_dir / "load_policies.json").write_text(json.dumps(_PARQUET_INPUT))
        code = _node_to_code(node)
        result = _run_generated(code, directory=tmp_path)
        assert isinstance(result, pl.LazyFrame)
        assert result.collect()["policy_id"].to_list() == [1, 2]

    def test_data_input_hook_exec_runs_code_on_the_loaded_frame(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A dataInput hook receives the loaded data as ``df`` and returns its result."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".git").mkdir()
        (tmp_path / "haute.toml").write_text('[project]\nname = "test"\n', encoding="utf-8")
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        pl.DataFrame({"policy_id": [1, 2]}).write_parquet(data_dir / "policies.parquet")
        config = {**_PARQUET_INPUT, "code": "df = df.filter(pl.col('policy_id') > 1)"}
        node = _make_codegen_node("dataInput", config, label="load_policies")
        config_dir = tmp_path / "config" / "data_input"
        config_dir.mkdir(parents=True)
        (config_dir / "load_policies.json").write_text(json.dumps(_PARQUET_INPUT))

        result = _run_generated(_node_to_code(node), directory=tmp_path)

        assert isinstance(result, pl.LazyFrame)
        assert result.collect()["policy_id"].to_list() == [2]

    @pytest.mark.usefixtures("_widen_sandbox_root")
    def test_data_source_exec_uses_declared_schema_boundary(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A generated dataInput honours the shared source schema config."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".git").mkdir()
        (tmp_path / "haute.toml").write_text('[project]\nname = "test"\n', encoding="utf-8")
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        csv_path = data_dir / "quotes.csv"
        csv_path.write_text("quote_id,premium\n001,10.5\n", encoding="utf-8")

        config = {
            "inputType": "file",
            "format": "csv",
            "mode": "scan",
            "path": "data/quotes.csv",
            "arguments": {
                "schema_overrides": {
                    "quote_id": "String",
                    "premium": "Float64",
                }
            },
        }
        node = _make_codegen_node("dataInput", config, label="load_quotes")
        config_dir = tmp_path / "config" / "data_input"
        config_dir.mkdir(parents=True)
        (config_dir / "load_quotes.json").write_text(json.dumps(config))
        from tests.conftest import build_test_input_snapshot

        build_test_input_snapshot(config, base_dir=tmp_path)

        code = _node_to_code(node)

        assert code == (
            '@pipeline.data_input(config="config/data_input/load_quotes.json")\n'
            "def load_quotes(): ...\n"
        )
        assert "scan_csv" not in code
        result = _run_generated(code, directory=tmp_path)
        assert isinstance(result, pl.LazyFrame)
        collected = result.collect()
        assert collected["quote_id"].to_list() == ["001"]
        assert collected.schema["quote_id"] == pl.String

    def test_api_input_exec_produces_lazyframe(self, haute_scratch: Path) -> None:
        """An apiInput declaration loads through its retained sidecar.

        The sidecar, not the codegen-time config, decides what is read:
        standalone direct-vs-cached execution is covered by
        ``test_codegen_execution_equivalence.py``.
        """
        pl.DataFrame({"quote_id": [7, 11]}).write_parquet(haute_scratch / "quotes.parquet")
        config_dir = haute_scratch / "config" / "quote_input"
        config_dir.mkdir(parents=True)
        (config_dir / "quotes.json").write_text(
            json.dumps({"path": "quotes.parquet"}), encoding="utf-8"
        )
        node = _make_codegen_node(
            "apiInput",
            {"path": "tests/fixtures/data/api_input.json"},
            label="quotes",
        )
        code = _node_to_code(node)
        assert code == (
            '@pipeline.api_input(config="config/quote_input/quotes.json")\ndef quotes(): ...\n'
        )
        assert "tests/fixtures/data/api_input.json" not in code

        result = _run_generated(code, directory=haute_scratch)

        assert isinstance(result, pl.LazyFrame)
        assert result.collect()["quote_id"].to_list() == [7, 11]

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

        with pytest.raises(ApiInputSchemaError, match="no v2 schema") as exc_info:
            _run_generated(code, directory=project)
        assert "Cache as Parquet" not in str(exc_info.value)

    def test_json_api_input_exec_fails_loudly_without_selected_columns(
        self,
        tmp_path: Path,
    ) -> None:
        """Generated JSON apiInput reports the config action, not a cache action."""
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

        with pytest.raises(
            RuntimeError, match="emit-true tables but none has any selected columns"
        ) as exc_info:
            _run_generated(code, directory=tmp_path)
        assert "Cache as Parquet" not in str(exc_info.value)

    def test_output_exec_assembles_document(self, tmp_path: Path) -> None:
        """The generated OUTPUT declaration assembles the response document.

        Its decorator routes through the shared ``assemble_output_from_config``
        — the same assembler the canvas executor calls — driven by the node's
        saved schema JSON.  A passthrough would leak the raw upstream frame
        (all columns, no document shape) from a standalone run.
        """
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
        assert code == (
            '@pipeline.output(config="config/quote_response/result.json")\n'
            "def result(upstream): ...\n"
        )
        cfg_file = tmp_path / "config" / "quote_response" / "result.json"
        cfg_file.parent.mkdir(parents=True)
        cfg_file.write_text(json.dumps(node.data.config))

        input_lf = pl.DataFrame(
            {
                "premium": [1.0],
                "Area": ["A"],
                "extra": [99],
            }
        ).lazy()
        result = _run_generated(code, input_lf, directory=tmp_path)
        assert isinstance(result, pl.LazyFrame)
        collected = result.collect()
        # The mapped columns survive as document fields; the unmapped one is
        # projected away — the raw frame did NOT pass through.
        assert set(collected.columns) == {"premium", "Area"}
        assert collected.to_dicts() == [{"premium": 1.0, "Area": "A"}]

    def test_multi_frame_output_uses_supplied_frame_params(self, tmp_path: Path) -> None:
        """OUTPUT's parameters are the distinct frame names already derived per edge.

        The decorator resolves each mapped ``source_port`` against those
        names, so the second declared frame feeds the ``drivers`` mapping.
        """
        config = {
            "outputMapping": [
                {
                    "source_port": "drivers",
                    "source_column": "driver_id",
                    "output_path": "$[:].driver_id",
                    "enabled": True,
                },
            ],
            "outputFormat": "json",
        }
        node = _make_codegen_node("output", config, label="Quote_Response")
        frame_names = ["quotes", "drivers", "licences", "vehicles"]
        code = _node_to_code(node, source_names=frame_names)
        assert "def Quote_Response(quotes, drivers, licences, vehicles): ...\n" in code
        _compile_node_code(code)
        cfg_file = tmp_path / "config" / "quote_response" / "Quote_Response.json"
        cfg_file.parent.mkdir(parents=True)
        cfg_file.write_text(json.dumps(config), encoding="utf-8")

        frames = [
            pl.LazyFrame({f"{name[:-1]}_id": [index]}) for index, name in enumerate(frame_names)
        ]
        result = _run_generated(code, *frames, directory=tmp_path)

        assert _collect(result).to_dicts() == [{"driver_id": 1}]

    def test_banding_exec_applies_sidecar_config(self, tmp_path: Path) -> None:
        """The generated banding declaration APPLIES the sidecar config when run.

        A passthrough would return the input unchanged — the saved file
        would silently skip banding on a standalone run.  Running the
        generated node must produce the banded column.
        """
        factors = [
            {
                "column": "age",
                "outputColumn": "age_band",
                "banding": "breakpoints",
                "rules": [{"boundary": "50", "label": "young"}],
            }
        ]
        config_dir = tmp_path / "config" / "banding"
        config_dir.mkdir(parents=True)
        (config_dir / "band_age.json").write_text(
            json.dumps({"factors": factors}),
            encoding="utf-8",
        )

        node = _make_codegen_node("banding", {"factors": factors}, label="band_age")
        code = _node_to_code(node, source_names=["policies"])
        input_lf = pl.DataFrame({"age": [25, 55]}).lazy()
        result = _run_generated(code, input_lf, directory=tmp_path)
        assert isinstance(result, pl.LazyFrame)
        collected = result.collect()
        assert collected["age_band"].to_list() == ["young", None], (
            "generated banding must apply the sidecar config, not pass through"
        )

    def test_model_score_declaration_scores_through_its_decorator(self, tmp_path: Path) -> None:
        """A modelScore declaration scores its input with the sidecar's model when run."""
        config = {
            "sourceType": "run",
            "task": "regression",
            "output_column": "prediction",
            "run_id": "abc123",
        }
        node = _make_codegen_node("modelScore", config, label="score")
        code = _node_to_code(node, source_names=["features"])
        assert code == (
            '@pipeline.model_score(config="config/model_scoring/score.json")\n'
            "def score(features): ...\n"
        )
        cfg_file = tmp_path / "config" / "model_scoring" / "score.json"
        cfg_file.parent.mkdir(parents=True)
        cfg_file.write_text(json.dumps(config), encoding="utf-8")

        features = pl.LazyFrame({"a": [1.0, 2.0], "b": [3.0, 4.0]})
        with patch(
            "haute._mlflow_io.load_mlflow_model", return_value=_stub_scoring_model()
        ) as load_model:
            scored = _collect(_run_generated(code, features, directory=tmp_path))

        assert scored["prediction"].to_list() == [0.5, 0.5]
        assert load_model.call_args.kwargs["run_id"] == "abc123"

    def test_transform_with_code_exec(self, tmp_path: Path) -> None:
        """transform code with real Polars expression executes correctly."""
        node = _make_codegen_node(
            "polars",
            {"code": 'df = src.with_columns(doubled=pl.col("x") * 2)'},
            label="double_it",
        )
        code = _node_to_code(node, source_names=["src"])
        input_lf = pl.DataFrame({"x": [1.0, 2.0, 3.0]}).lazy()
        result = _run_generated(code, input_lf, directory=tmp_path)
        collected = _collect(result)
        assert "doubled" in collected.columns
        assert collected["doubled"].to_list() == [2.0, 4.0, 6.0]


# ---------------------------------------------------------------------------
# Data Output codegen is side-effect free; writes are explicit API actions
# ---------------------------------------------------------------------------


_PARQUET_OUTPUT = {
    "outputType": "file",
    "path": "output/results.parquet",
    "format": "parquet",
    "mode": "sink",
    "arguments": {},
}


class TestGenDataOutput:
    """Data Output codegen carries config but never writes during graph execution."""

    def test_parquet_output_is_a_passthrough(self, tmp_path: Path) -> None:
        node = _make_codegen_node("dataOutput", _PARQUET_OUTPUT, label="WriteResults")
        code = _node_to_code(node, source_names=["scored"])
        assert code == (
            '@pipeline.data_output(config="config/data_output/WriteResults.json")\n'
            "def WriteResults(scored): ...\n"
        )
        assert "bounded_sink" not in code
        assert ".write_" not in code
        _compile_node_code(code)

        scored = pl.LazyFrame({"premium": [1.0]})
        assert _run_generated(code, scored, directory=tmp_path) is scored
        assert not (tmp_path / "output").exists()

    def test_csv_output_is_a_passthrough(self) -> None:
        node = _make_codegen_node(
            "dataOutput",
            {
                "outputType": "file",
                "path": "output/report.csv",
                "format": "csv",
                "mode": "sink",
                "arguments": {},
            },
            label="WriteCSV",
        )
        code = _node_to_code(node, source_names=["data"])
        assert code == (
            '@pipeline.data_output(config="config/data_output/WriteCSV.json")\n'
            "def WriteCSV(data): ...\n"
        )
        assert "bounded_sink" not in code
        _compile_node_code(code)

    def test_output_config_is_referenced_without_embedding_destination(self) -> None:
        node = _make_codegen_node(
            "dataOutput",
            {
                "outputType": "file",
                "path": "out.parquet",
                "format": "parquet",
                "mode": "sink",
                "arguments": {},
            },
            label="DefaultSink",
        )
        code = _node_to_code(node, source_names=["df"])
        assert 'config="config/data_output/DefaultSink.json"' in code
        assert "out.parquet" not in code
        _compile_node_code(code)

    def test_sink_returns_first_source(self, tmp_path: Path) -> None:
        """A standalone run hands the input frame on for downstream chaining."""
        node = _make_codegen_node("dataOutput", _PARQUET_OUTPUT, label="SinkNode")
        code = _node_to_code(node, source_names=["input_df"])
        assert "def SinkNode(input_df): ...\n" in code

        input_df = pl.LazyFrame({"x": [1]})
        assert _run_generated(code, input_df, directory=tmp_path) is input_df

    def test_sink_with_multiple_sources(self, tmp_path: Path) -> None:
        """Sink with multiple sources hands on the first one."""
        node = _make_codegen_node("dataOutput", _PARQUET_OUTPUT, label="MultiSink")
        code = _node_to_code(node, source_names=["a", "b", "c"])
        assert "bounded_sink" not in code
        assert "def MultiSink(a, b, c): ...\n" in code
        _compile_node_code(code)

        a, b, c = (pl.LazyFrame({"x": [value]}) for value in (1, 2, 3))
        assert _run_generated(code, a, b, c, directory=tmp_path) is a
