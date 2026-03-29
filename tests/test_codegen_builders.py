"""Tests for codegen builder functions — _gen_api_input, _gen_banding, etc.

Follows the same pattern as test_codegen.py: build a node, call
_node_to_code (which dispatches to the type-specific builder), then
verify the generated code compiles and contains expected fragments.
"""

from __future__ import annotations

from haute.codegen import _build_extra_kwargs, _node_to_code, graph_to_code
from tests.conftest import compile_node_code as _compile_node_code, make_graph as _g
from tests.conftest import make_node as _n

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
        assert 'scan_parquet("data/api_input.parquet")' in code
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
        assert 'scan_csv("data/input.csv")' in code
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
        assert "read_json_flat" in code
        _compile_node_code(code)

    def test_jsonl_api_input(self) -> None:
        node = _make_codegen_node(
            "apiInput",
            {"path": "data/quotes.jsonl"},
            label="JSONLInput",
        )
        code = _node_to_code(node)
        assert "read_json_flat" in code
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
        assert "return data" in code
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
        assert "return base_data" in code
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
        assert "return data" in code
        _compile_node_code(code)

    def test_no_sources_uses_df_param(self) -> None:
        node = _make_codegen_node(
            "scenarioExpander",
            {"column_name": "sv", "steps": 5},
            label="NoSrcExpand",
        )
        code = _node_to_code(node, source_names=[])
        assert "df: pl.LazyFrame" in code
        assert "return df" in code
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
        assert "df = data" in code
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
        assert "return data" in code
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
                            "config": {"fields": ["age", "sv"]},
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
        ns: dict = {}
        exec(
            "import polars as pl\nimport haute\n"
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

    def test_api_input_exec_produces_lazyframe(self) -> None:
        """apiInput code that references a real JSON file executes."""
        import polars as pl

        node = _make_codegen_node(
            "apiInput",
            {"path": "tests/fixtures/data/api_input.json"},
            label="quotes",
        )
        code = _node_to_code(node)
        result = self._exec_generated(code)
        assert isinstance(result, pl.LazyFrame)
        collected = result.collect()
        assert len(collected) > 0
        assert len(collected.columns) > 0

    def test_output_exec_selects_fields(self) -> None:
        """output code with fields actually filters columns."""
        import polars as pl

        node = _make_codegen_node(
            "output",
            {"fields": ["premium", "Area"]},
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
        assert set(collected.columns) == {"premium", "Area"}

    def test_banding_exec_returns_lazyframe(self) -> None:
        """banding code body is 'return df' — the runtime executor injects df.

        Here we verify the function can be called when the parameter name
        matches 'df' (no upstream source), confirming the body references
        the correct variable.
        """
        import polars as pl

        node = _make_codegen_node(
            "banding",
            {
                "factors": [
                    {
                        "column": "age",
                        "outputColumn": "age_band",
                        "banding": "continuous",
                        "rules": [
                            {"op1": ">=", "val1": 0, "op2": "<", "val2": 50, "assignment": "young"},
                        ],
                    }
                ],
            },
            label="band_age",
        )
        # No source_names → param is 'df', matching the body's `return df`
        code = _node_to_code(node, source_names=[])
        input_lf = pl.DataFrame({"age": [25, 55]}).lazy()
        result = self._exec_generated(code, input_df=input_lf)
        assert isinstance(result, pl.LazyFrame)
        assert len(result.collect()) == 2

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
# B19: Sink templates use safe_sink instead of hardcoded collect+write
# ---------------------------------------------------------------------------


class TestGenDataSink:
    """Tests for data sink code generation — must delegate to safe_sink."""

    def test_parquet_sink_uses_safe_sink(self) -> None:
        """Parquet sink template should import and call safe_sink."""
        node = _make_codegen_node(
            "dataSink",
            {"path": "output/results.parquet", "format": "parquet"},
            label="WriteResults",
        )
        code = _node_to_code(node, source_names=["scored"])
        assert "from haute._polars_utils import safe_sink" in code
        assert 'safe_sink(scored, "output/results.parquet")' in code
        # Must NOT contain the old hardcoded pattern
        assert ".collect(engine=" not in code
        assert ".write_parquet(" not in code
        _compile_node_code(code)

    def test_csv_sink_uses_safe_sink(self) -> None:
        """CSV sink template should import and call safe_sink with fmt='csv'."""
        node = _make_codegen_node(
            "dataSink",
            {"path": "output/report.csv", "format": "csv"},
            label="WriteCSV",
        )
        code = _node_to_code(node, source_names=["data"])
        assert "from haute._polars_utils import safe_sink" in code
        assert 'safe_sink(data, "output/report.csv", fmt="csv")' in code
        assert ".write_csv(" not in code
        _compile_node_code(code)

    def test_sink_default_format_is_parquet(self) -> None:
        """When no format is specified, default to parquet safe_sink call."""
        node = _make_codegen_node(
            "dataSink",
            {"path": "out.parquet"},
            label="DefaultSink",
        )
        code = _node_to_code(node, source_names=["df"])
        assert "safe_sink" in code
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
        assert "safe_sink(a," in code
        assert "return a" in code
        _compile_node_code(code)
