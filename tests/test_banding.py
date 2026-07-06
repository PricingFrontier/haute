"""Tests for banding node type — continuous and categorical."""

from __future__ import annotations

import math
import re
from pathlib import Path

import polars as pl
import polars.testing as plt
import pytest

from haute._rating import _breakpoints_to_rules
from haute.executor import _apply_banding, _build_node_fn
from haute.graph_utils import GraphNode, NodeData, NodeType, PipelineGraph
from tests.conftest import write_node_config

# ---------------------------------------------------------------------------
# Helper builders
# ---------------------------------------------------------------------------


def _banding_node(
    nid: str,
    banding: str = "continuous",
    column: str = "",
    output_column: str = "",
    rules: list | None = None,
    default: str | None = None,
) -> GraphNode:
    """Single-factor banding node using the factors array format."""
    factor: dict = {
        "banding": banding,
        "column": column,
        "outputColumn": output_column,
        "rules": rules or [],
        "default": default,
    }
    return GraphNode(
        id=nid,
        data=NodeData(label=nid, nodeType="banding", config={"factors": [factor]}),
    )


def _multi_banding_node(nid: str, factors: list[dict]) -> GraphNode:
    """Multi-factor banding node."""
    return GraphNode(
        id=nid,
        data=NodeData(label=nid, nodeType="banding", config={"factors": factors}),
    )


# ---------------------------------------------------------------------------
# _apply_banding — unit tests
# ---------------------------------------------------------------------------


class TestApplyBandingContinuous:
    def test_single_upper_bound(self):
        lf = pl.DataFrame({"age": [0, 5, 10, 20]}).lazy()
        rules = [
            {"op1": "<=", "val1": 5, "assignment": "young"},
            {"op1": ">", "val1": 5, "op2": "<=", "val2": 15, "assignment": "mid"},
            {"op1": ">", "val1": 15, "assignment": "old"},
        ]
        result = _apply_banding(lf, "age", "age_band", "continuous", rules).collect()
        assert result["age_band"].to_list() == ["young", "young", "mid", "old"]

    def test_open_ended_ranges(self):
        lf = pl.DataFrame({"x": [-5, 0, 100]}).lazy()
        rules = [
            {"op1": "<", "val1": 0, "assignment": "negative"},
            {"op1": ">=", "val1": 0, "assignment": "non_negative"},
        ]
        result = _apply_banding(lf, "x", "band", "continuous", rules).collect()
        assert result["band"].to_list() == ["negative", "non_negative", "non_negative"]

    def test_default_value(self):
        lf = pl.DataFrame({"x": [1, 50]}).lazy()
        rules = [
            {"op1": "<=", "val1": 10, "assignment": "low"},
        ]
        result = _apply_banding(lf, "x", "band", "continuous", rules, default="other").collect()
        assert result["band"].to_list() == ["low", "other"]

    def test_null_default_when_unmatched(self):
        lf = pl.DataFrame({"x": [1, 50]}).lazy()
        rules = [
            {"op1": "<=", "val1": 10, "assignment": "low"},
        ]
        result = _apply_banding(lf, "x", "band", "continuous", rules).collect()
        assert result["band"].to_list() == ["low", None]

    def test_empty_rules_passthrough(self):
        lf = pl.DataFrame({"x": [1, 2]}).lazy()
        result = _apply_banding(lf, "x", "band", "continuous", []).collect()
        assert "band" not in result.columns

    def test_string_values_coerced(self):
        """val1/val2 may arrive as strings from the GUI."""
        lf = pl.DataFrame({"x": [3, 7]}).lazy()
        rules = [
            {"op1": "<=", "val1": "5", "assignment": "low"},
            {"op1": ">", "val1": "5", "assignment": "high"},
        ]
        result = _apply_banding(lf, "x", "band", "continuous", rules).collect()
        assert result["band"].to_list() == ["low", "high"]

    def test_all_rows_matched(self):
        """When every row matches a rule, no default values appear."""
        lf = pl.DataFrame({"x": [1, 5, 10]}).lazy()
        rules = [
            {"op1": "<=", "val1": 5, "assignment": "low"},
            {"op1": ">", "val1": 5, "assignment": "high"},
        ]
        result = _apply_banding(lf, "x", "band", "continuous", rules).collect()
        assert result["band"].null_count() == 0

    def test_single_row(self):
        """Banding works on a single-row DataFrame."""
        lf = pl.DataFrame({"x": [42]}).lazy()
        rules = [{"op1": ">=", "val1": 0, "assignment": "pos"}]
        result = _apply_banding(lf, "x", "band", "continuous", rules).collect()
        assert result["band"].to_list() == ["pos"]

    def test_column_with_spaces_in_name(self):
        """Column names with spaces should work in banding."""
        lf = pl.DataFrame({"age group": [10, 30]}).lazy()
        rules = [
            {"op1": "<=", "val1": 20, "assignment": "young"},
            {"op1": ">", "val1": 20, "assignment": "old"},
        ]
        result = _apply_banding(lf, "age group", "band", "continuous", rules).collect()
        assert result["band"].to_list() == ["young", "old"]

    def test_null_input_values(self):
        """Null values in input column should not match any rule."""
        lf = pl.DataFrame({"x": [1, None, 10]}).lazy()
        rules = [
            {"op1": "<=", "val1": 5, "assignment": "low"},
            {"op1": ">", "val1": 5, "assignment": "high"},
        ]
        result = _apply_banding(lf, "x", "band", "continuous", rules, default="dflt").collect()
        bands = result["band"].to_list()
        assert bands[0] == "low"
        assert bands[1] == "dflt", f"Null row should get default value, got {bands[1]!r}"
        assert bands[2] == "high"


class TestApplyBandingCategorical:
    def test_basic_grouping(self):
        lf = pl.DataFrame(
            {"prop": ["Semi-detached House", "Detached House", "Mid terrace", "Flat"]}
        ).lazy()
        rules = [
            {"value": "Semi-detached House", "assignment": "House"},
            {"value": "Detached House", "assignment": "House"},
            {"value": "Mid terrace", "assignment": "Terrace"},
        ]
        result = _apply_banding(lf, "prop", "prop_band", "categorical", rules).collect()
        assert result["prop_band"].to_list() == ["House", "House", "Terrace", None]

    def test_categorical_with_default(self):
        lf = pl.DataFrame({"prop": ["Villa", "Flat"]}).lazy()
        rules = [
            {"value": "Villa", "assignment": "House"},
        ]
        result = _apply_banding(lf, "prop", "band", "categorical", rules, default="Other").collect()
        assert result["band"].to_list() == ["House", "Other"]

    def test_empty_rules_passthrough(self):
        lf = pl.DataFrame({"x": ["a", "b"]}).lazy()
        result = _apply_banding(lf, "x", "band", "categorical", []).collect()
        assert "band" not in result.columns


# ---------------------------------------------------------------------------
# _build_node_fn — integration with executor
# ---------------------------------------------------------------------------


class TestBuildNodeFn:
    def test_banding_node_fn_continuous(self):
        node = _banding_node(
            "band_age",
            banding="continuous",
            column="age",
            output_column="age_band",
            rules=[
                {"op1": "<=", "val1": 25, "assignment": "young"},
                {"op1": ">", "val1": 25, "assignment": "older"},
            ],
        )
        func_name, fn, is_source = _build_node_fn(node)
        assert func_name == "band_age"
        assert not is_source

        lf = pl.DataFrame({"age": [20, 30]}).lazy()
        result = fn(lf).collect()
        assert result["age_band"].to_list() == ["young", "older"]

    def test_banding_node_fn_categorical(self):
        node = _banding_node(
            "band_prop",
            banding="categorical",
            column="type",
            output_column="type_band",
            rules=[
                {"value": "A", "assignment": "Group1"},
                {"value": "B", "assignment": "Group1"},
                {"value": "C", "assignment": "Group2"},
            ],
        )
        _, fn, _ = _build_node_fn(node)
        lf = pl.DataFrame({"type": ["A", "B", "C", "D"]}).lazy()
        result = fn(lf).collect()
        assert result["type_band"].to_list() == ["Group1", "Group1", "Group2", None]

    def test_banding_node_fn_accepts_compact_categorical_rules(self):
        node = _multi_banding_node(
            "band_prop",
            [
                {
                    "banding": "categorical",
                    "column": "type",
                    "outputColumn": "type_band",
                    "rules": {"A": "Group1", "B": "Group1", "C": "Group2"},
                }
            ],
        )

        _, fn, _ = _build_node_fn(node)
        lf = pl.DataFrame({"type": ["A", "B", "C", "D"]}).lazy()
        result = fn(lf).collect()

        assert result["type_band"].to_list() == ["Group1", "Group1", "Group2", None]

    def test_banding_node_empty_config_passthrough(self):
        node = _banding_node("empty", column="", output_column="", rules=[])
        _, fn, _ = _build_node_fn(node)
        lf = pl.DataFrame({"x": [1]}).lazy()
        result = fn(lf).collect()
        assert result.columns == ["x"]


# ---------------------------------------------------------------------------
# Parser round-trip
# ---------------------------------------------------------------------------


class TestBandingParser:
    def test_parse_banding_node(self, tmp_path):
        from haute.parser import parse_pipeline_source

        band_config = write_node_config(
            tmp_path,
            NodeType.BANDING,
            "band_age",
            {
                "factors": [
                    {
                        "banding": "continuous",
                        "column": "age",
                        "outputColumn": "age_band",
                        "rules": [{"op1": "<=", "val1": 25, "assignment": "young"}],
                    }
                ]
            },
        )
        code = f'''\
import polars as pl
import haute

pipeline = haute.Pipeline("test")

@pipeline.banding(config="{band_config}")
def band_age(df: pl.LazyFrame) -> pl.LazyFrame:
    """Band age into age_band"""
    return df
'''
        graph = parse_pipeline_source(code, _base_dir=tmp_path)
        assert len(graph.nodes) == 1
        node = graph.nodes[0]
        assert node.data.nodeType == "banding"
        factors = node.data.config["factors"]
        assert len(factors) == 1
        f = factors[0]
        assert f["banding"] == "continuous"
        assert f["column"] == "age"
        assert f["outputColumn"] == "age_band"
        assert len(f["rules"]) == 1
        assert f["rules"][0]["assignment"] == "young"

    def test_parse_categorical_banding(self, tmp_path):
        from haute.parser import parse_pipeline_source

        band_config = write_node_config(
            tmp_path,
            NodeType.BANDING,
            "band_prop",
            {
                "factors": [
                    {
                        "banding": "categorical",
                        "column": "prop",
                        "outputColumn": "prop_band",
                        "rules": [{"value": "House", "assignment": "Residential"}],
                    }
                ]
            },
        )
        code = f'''\
import polars as pl
import haute

pipeline = haute.Pipeline("test")

@pipeline.banding(config="{band_config}")
def band_prop(df: pl.LazyFrame) -> pl.LazyFrame:
    """Band property type"""
    return df
'''
        graph = parse_pipeline_source(code, _base_dir=tmp_path)
        node = graph.nodes[0]
        assert node.data.nodeType == "banding"
        assert node.data.config["factors"][0]["banding"] == "categorical"

    def test_parse_compact_categorical_banding_sidecar(self, tmp_path):
        from haute.parser import parse_pipeline_source

        band_config = write_node_config(
            tmp_path,
            NodeType.BANDING,
            "band_fuel",
            {
                "factors": [
                    {
                        "banding": "categorical",
                        "column": "fuel_type",
                        "outputColumn": "fuel_band",
                        "rules": {"Petrol": "Standard", "Electric": "Green"},
                    }
                ]
            },
        )
        code = f'''\
import polars as pl
import haute

pipeline = haute.Pipeline("test")

@pipeline.banding(config="{band_config}")
def band_fuel(df: pl.LazyFrame) -> pl.LazyFrame:
    """Band fuel type"""
    return df
'''
        graph = parse_pipeline_source(code, _base_dir=tmp_path)
        factor = graph.nodes[0].data.config["factors"][0]

        assert factor["rules"] == [
            {"value": "Petrol", "assignment": "Standard"},
            {"value": "Electric", "assignment": "Green"},
        ]

    def test_parse_compact_breakpoint_banding_sidecar(self, tmp_path):
        from haute.parser import parse_pipeline_source

        band_config = write_node_config(
            tmp_path,
            NodeType.BANDING,
            "band_age",
            {
                "factors": [
                    {
                        "banding": "breakpoints",
                        "column": "age",
                        "outputColumn": "age_band",
                        "rules": {"25": "young", "65": "adult", "": "senior"},
                    }
                ]
            },
        )
        code = f'''\
import polars as pl
import haute

pipeline = haute.Pipeline("test")

@pipeline.banding(config="{band_config}")
def band_age(df: pl.LazyFrame) -> pl.LazyFrame:
    """Band age"""
    return df
'''
        graph = parse_pipeline_source(code, _base_dir=tmp_path)
        factor = graph.nodes[0].data.config["factors"][0]

        assert factor["rules"] == [
            {"boundary": "25", "label": "young"},
            {"boundary": "65", "label": "adult"},
            {"boundary": "", "label": "senior"},
        ]


# ---------------------------------------------------------------------------
# Codegen round-trip
# ---------------------------------------------------------------------------


class TestBandingCodegen:
    def test_codegen_banding_node(self):
        from haute.codegen import graph_to_code

        node = _banding_node(
            "band_age",
            banding="continuous",
            column="age",
            output_column="age_band",
            rules=[{"op1": "<=", "val1": 25, "assignment": "young"}],
        )
        graph = PipelineGraph(nodes=[node], edges=[])
        code = graph_to_code(graph, "test")
        assert 'config="config/banding/band_age.json"' in code

    def test_codegen_roundtrip(self, tmp_path):
        """Generate code → parse it back → same config."""
        from haute._config_io import collect_node_configs
        from haute.codegen import graph_to_code
        from haute.parser import parse_pipeline_source

        rules = [
            {"value": "A", "assignment": "Group1"},
            {"value": "B", "assignment": "Group2"},
        ]
        node = _banding_node(
            "band_cat",
            banding="categorical",
            column="code",
            output_column="code_band",
            rules=rules,
        )
        graph = PipelineGraph(nodes=[node], edges=[])
        code = graph_to_code(graph, "test")

        # Write config files so the parser can resolve them
        py_file = tmp_path / "pipeline.py"
        py_file.write_text(code)
        for rel_path, content in collect_node_configs(graph).items():
            cfg_file = tmp_path / rel_path
            cfg_file.parent.mkdir(parents=True, exist_ok=True)
            cfg_file.write_text(content)

        parsed = parse_pipeline_source(code, _base_dir=tmp_path)

        assert len(parsed.nodes) == 1
        pn = parsed.nodes[0]
        assert pn.data.nodeType == "banding"
        pf = pn.data.config["factors"][0]
        assert pf["banding"] == "categorical"
        assert pf["column"] == "code"
        assert pf["outputColumn"] == "code_band"
        assert len(pf["rules"]) == 2


# ---------------------------------------------------------------------------
# Standalone execution of the SAVED file — the .py file IS the pipeline.
#
# The GUI executor applies banding via `_build_banding`, so the canvas always
# banded correctly; but a generated body that just `return df`s silently skips
# banding when the saved file is run standalone (`pipeline.run()`), mis-pricing
# any deployment that executes the artifact directly.  These tests run the
# saved file for real and demand cross-surface equality with the executor.
# ---------------------------------------------------------------------------


def _banding_graph(factors: list[dict]) -> tuple[PipelineGraph, GraphNode]:
    """A dataSource -> banding graph with canvas-style ids != sanitized labels."""
    source = GraphNode(
        id="dataSource_7",
        data=NodeData(
            label="Quote Data",
            nodeType=NodeType.DATA_SOURCE,
            config={"path": "data.parquet", "sourceType": "flat_file"},
        ),
    )
    band = GraphNode(
        id="banding_3",
        data=NodeData(
            label="Risk Bands",
            nodeType=NodeType.BANDING,
            config={"factors": factors},
        ),
    )
    graph = PipelineGraph.model_validate(
        {
            "nodes": [source.model_dump(), band.model_dump()],
            "edges": [{"id": "e1", "source": "dataSource_7", "target": "banding_3"}],
        }
    )
    return graph, band


def _materialise_pipeline(tmp_path: Path, graph: PipelineGraph, input_df: pl.DataFrame) -> str:
    """Write the generated .py file, its config sidecars, and the input data."""
    from haute._config_io import collect_node_configs
    from haute.codegen import graph_to_code

    code = graph_to_code(graph, pipeline_name="banding_standalone")
    for rel_path, content in collect_node_configs(graph).items():
        cfg_file = tmp_path / rel_path
        cfg_file.parent.mkdir(parents=True, exist_ok=True)
        cfg_file.write_text(content, encoding="utf-8")
    input_df.write_parquet(tmp_path / "data.parquet")
    (tmp_path / "pipeline.py").write_text(code, encoding="utf-8")
    return code


def _run_saved_file(tmp_path: Path, code: str) -> pl.DataFrame:
    """Exec the saved file standalone and run the registered pipeline."""
    path = tmp_path / "pipeline.py"
    namespace: dict = {"__file__": str(path)}
    exec(compile(code, str(path), "exec"), namespace)
    result = namespace["pipeline"].run()
    if isinstance(result, pl.LazyFrame):
        result = result.collect()
    return result


def _executor_banding_output(band_node: GraphNode, input_df: pl.DataFrame) -> pl.DataFrame:
    """The GUI executor's output for the banding node over the same input."""
    _, fn, _ = _build_node_fn(band_node)
    return fn(input_df.lazy()).collect()


class TestBandingStandaloneExecution:
    """`pipeline.run()` of the SAVED file must band — not silently pass through."""

    def test_standalone_run_applies_continuous_banding(self, tmp_path):
        factors = [
            {
                "banding": "continuous",
                "column": "age",
                "outputColumn": "age_band",
                "rules": [
                    {"op1": "<=", "val1": 25, "assignment": "young"},
                    {"op1": ">", "val1": 25, "assignment": "older"},
                ],
                "default": "unknown",
            }
        ]
        graph, band_node = _banding_graph(factors)
        input_df = pl.DataFrame({"age": [18, 25, 40, None]})
        code = _materialise_pipeline(tmp_path, graph, input_df)

        result = _run_saved_file(tmp_path, code)

        # The silent skip made visible: a passthrough body drops the band.
        assert "age_band" in result.columns, (
            "standalone pipeline.run() of the saved file silently skipped banding"
        )
        assert result["age_band"].to_list() == ["young", "young", "older", "unknown"]

        # Cross-surface equality: saved-file run == GUI executor.
        plt.assert_frame_equal(result, _executor_banding_output(band_node, input_df))

    def test_standalone_run_applies_categorical_banding(self, tmp_path):
        factors = [
            {
                "banding": "categorical",
                "column": "prop",
                "outputColumn": "prop_band",
                "rules": [
                    {"value": "Semi-detached House", "assignment": "House"},
                    {"value": "Detached House", "assignment": "House"},
                    {"value": "Mid terrace", "assignment": "Terrace"},
                ],
                "default": "Other",
            }
        ]
        graph, band_node = _banding_graph(factors)
        input_df = pl.DataFrame(
            {"prop": ["Semi-detached House", "Detached House", "Mid terrace", "Flat"]}
        )
        code = _materialise_pipeline(tmp_path, graph, input_df)

        result = _run_saved_file(tmp_path, code)

        assert "prop_band" in result.columns
        assert result["prop_band"].to_list() == ["House", "House", "Terrace", "Other"]
        plt.assert_frame_equal(result, _executor_banding_output(band_node, input_df))

    def test_standalone_run_applies_breakpoints_with_right_closed_flag(self, tmp_path):
        """Breakpoint rules and the rightClosed ordering flag survive the sidecar."""
        factors = [
            {
                "banding": "breakpoints",
                "column": "x",
                "outputColumn": "x_band",
                "rules": [
                    {"boundary": "10", "label": "A"},
                    {"boundary": "20", "label": "B"},
                ],
                "rightClosed": False,
            }
        ]
        graph, band_node = _banding_graph(factors)
        input_df = pl.DataFrame({"x": [5, 10, 15, 20]})
        code = _materialise_pipeline(tmp_path, graph, input_df)

        result = _run_saved_file(tmp_path, code)

        assert "x_band" in result.columns
        # rightClosed=False means [lower, upper): 20 falls out (no open-ended rule).
        assert result["x_band"].to_list() == ["A", "B", "B", None]
        plt.assert_frame_equal(result, _executor_banding_output(band_node, input_df))

    def test_standalone_run_applies_all_factors_multi(self, tmp_path):
        """Multi-factor emission (factors= decorator) must also band standalone."""
        factors = [
            {
                "banding": "continuous",
                "column": "age",
                "outputColumn": "age_band",
                "rules": [
                    {"op1": "<=", "val1": 25, "assignment": "young"},
                    {"op1": ">", "val1": 25, "assignment": "older"},
                ],
            },
            {
                "banding": "categorical",
                "column": "prop",
                "outputColumn": "prop_band",
                "rules": [{"value": "House", "assignment": "Residential"}],
            },
        ]
        graph, band_node = _banding_graph(factors)
        input_df = pl.DataFrame({"age": [20, 40], "prop": ["House", "Office"]})
        code = _materialise_pipeline(tmp_path, graph, input_df)

        result = _run_saved_file(tmp_path, code)

        assert result["age_band"].to_list() == ["young", "older"]
        assert result["prop_band"].to_list() == ["Residential", None]
        plt.assert_frame_equal(result, _executor_banding_output(band_node, input_df))

    def test_standalone_run_empty_factors_is_noop_like_executor(self, tmp_path):
        """Empty banding config is a documented no-op on BOTH surfaces.

        The executor silently passes the frame through
        (`test_empty_factors_passthrough`); the saved file must do exactly the
        same — not crash, not invent columns.
        """
        graph, band_node = _banding_graph([])
        input_df = pl.DataFrame({"x": [1, 2]})
        code = _materialise_pipeline(tmp_path, graph, input_df)

        result = _run_saved_file(tmp_path, code)

        assert result.columns == ["x"]
        plt.assert_frame_equal(result, _executor_banding_output(band_node, input_df))

    def test_parse_back_round_trip_and_re_emission_byte_stable(self, tmp_path):
        """codegen -> file -> parse back == same banding node; re-emission byte-stable."""
        from haute.codegen import graph_to_code
        from haute.parser import parse_pipeline_file

        factors = [
            {
                "banding": "continuous",
                "column": "age",
                "outputColumn": "age_band",
                "rules": [
                    {"op1": "<=", "val1": 25, "assignment": "young"},
                    {"op1": ">", "val1": 25, "assignment": "older"},
                ],
                "default": "unknown",
            },
            {
                "banding": "categorical",
                "column": "prop",
                "outputColumn": "prop_band",
                "rules": [{"value": "House", "assignment": "Residential"}],
                "default": "Other",
            },
        ]
        graph, _band_node = _banding_graph(factors)
        input_df = pl.DataFrame({"age": [20], "prop": ["House"]})
        code = _materialise_pipeline(tmp_path, graph, input_df)

        # Codegen is idempotent: a second emission from the same graph is
        # byte-identical.
        assert graph_to_code(graph, pipeline_name="banding_standalone") == code

        parsed = parse_pipeline_file(tmp_path / "pipeline.py")
        parsed_band = parsed.node_map["Risk_Bands"]
        assert parsed_band.data.nodeType == NodeType.BANDING

        parsed_factors = parsed_band.data.config["factors"]
        assert len(parsed_factors) == 2
        assert parsed_factors[0]["column"] == "age"
        assert parsed_factors[0]["outputColumn"] == "age_band"
        assert parsed_factors[0]["default"] == "unknown"
        assert parsed_factors[0]["rules"] == factors[0]["rules"]
        assert parsed_factors[1]["banding"] == "categorical"
        assert parsed_factors[1]["rules"] == factors[1]["rules"]

        # Re-emitting from the parsed graph reproduces the banding function
        # byte-for-byte (parse -> codegen fixpoint for the banding node).
        # The injected contract kwarg is normalised out: first-save derives it
        # (`_format_contract_kwarg`, double-quoted keys) while re-save formats
        # the parsed declared contract via repr (single-quoted keys) — a
        # pre-existing, cross-node-type divergence outside banding emission.
        resaved = graph_to_code(parsed, pipeline_name="banding_standalone")
        assert _strip_contract_kwarg(_function_block(resaved, "Risk_Bands")) == (
            _strip_contract_kwarg(_function_block(code, "Risk_Bands"))
        )

        # And the re-saved file still bands when run standalone.
        result = _run_saved_file(tmp_path, resaved)
        assert result["age_band"].to_list() == ["young"]
        assert result["prop_band"].to_list() == ["Residential"]


def _function_block(code: str, func_name: str) -> str:
    """Extract the decorator + def block for *func_name* from generated code."""
    lines = code.splitlines()
    def_idx = next(i for i, line in enumerate(lines) if line.startswith(f"def {func_name}("))
    start = def_idx
    while start > 0 and lines[start - 1].startswith("@"):
        start -= 1
    end = def_idx + 1
    while end < len(lines) and (lines[end].startswith((" ", "\t")) or lines[end] == ""):
        end += 1
    return "\n".join(lines[start:end]).rstrip()


def _strip_contract_kwarg(block: str) -> str:
    """Drop the injected ``contract={...}`` kwarg from a decorator block."""
    return re.sub(r",?\s*contract=\{[^}]*\}", "", block)


# ---------------------------------------------------------------------------
# Multi-factor tests
# ---------------------------------------------------------------------------


class TestMultiFactor:
    def test_executor_applies_all_factors(self):
        node = _multi_banding_node(
            "multi",
            [
                {
                    "banding": "continuous",
                    "column": "age",
                    "outputColumn": "age_band",
                    "rules": [
                        {"op1": "<=", "val1": 25, "assignment": "young"},
                        {"op1": ">", "val1": 25, "assignment": "older"},
                    ],
                },
                {
                    "banding": "categorical",
                    "column": "prop",
                    "outputColumn": "prop_band",
                    "rules": [
                        {"value": "House", "assignment": "Residential"},
                        {"value": "Flat", "assignment": "Residential"},
                    ],
                },
            ],
        )
        _, fn, _ = _build_node_fn(node)
        lf = pl.DataFrame({"age": [20, 40], "prop": ["House", "Office"]}).lazy()
        result = fn(lf).collect()
        assert result["age_band"].to_list() == ["young", "older"]
        assert result["prop_band"].to_list() == ["Residential", None]

    def test_executor_skips_incomplete_factors(self):
        """Factors with missing column/output are silently skipped."""
        node = _multi_banding_node(
            "partial",
            [
                {
                    "banding": "continuous",
                    "column": "x",
                    "outputColumn": "x_band",
                    "rules": [{"op1": "<=", "val1": 10, "assignment": "low"}],
                },
                {
                    "banding": "continuous",
                    "column": "",
                    "outputColumn": "",
                    "rules": [],
                },
            ],
        )
        _, fn, _ = _build_node_fn(node)
        lf = pl.DataFrame({"x": [5]}).lazy()
        result = fn(lf).collect()
        assert "x_band" in result.columns
        assert result.columns == ["x", "x_band"]

    def test_codegen_multi_factor_uses_factors_kwarg(self):
        from haute.codegen import graph_to_code

        node = _multi_banding_node(
            "multi",
            [
                {
                    "banding": "continuous",
                    "column": "a",
                    "outputColumn": "a_band",
                    "rules": [{"op1": "<=", "val1": 5, "assignment": "low"}],
                },
                {
                    "banding": "categorical",
                    "column": "b",
                    "outputColumn": "b_band",
                    "rules": [{"value": "X", "assignment": "Y"}],
                },
            ],
        )
        graph = PipelineGraph(nodes=[node], edges=[])
        code = graph_to_code(graph, "test")
        assert 'config="config/banding/multi.json"' in code

    def test_codegen_multi_factor_roundtrip(self, tmp_path):
        from haute._config_io import collect_node_configs
        from haute.codegen import graph_to_code
        from haute.parser import parse_pipeline_source

        node = _multi_banding_node(
            "multi",
            [
                {
                    "banding": "continuous",
                    "column": "a",
                    "outputColumn": "a_band",
                    "rules": [{"op1": "<=", "val1": 5, "assignment": "low"}],
                },
                {
                    "banding": "categorical",
                    "column": "b",
                    "outputColumn": "b_band",
                    "rules": [{"value": "X", "assignment": "Y"}],
                },
            ],
        )
        graph = PipelineGraph(nodes=[node], edges=[])
        code = graph_to_code(graph, "test")

        # Write config files so the parser can resolve them
        for rel_path, content in collect_node_configs(graph).items():
            cfg_file = tmp_path / rel_path
            cfg_file.parent.mkdir(parents=True, exist_ok=True)
            cfg_file.write_text(content)

        parsed = parse_pipeline_source(code, _base_dir=tmp_path)

        pn = parsed.nodes[0]
        assert pn.data.nodeType == "banding"
        factors = pn.data.config["factors"]
        assert len(factors) == 2
        assert factors[0]["banding"] == "continuous"
        assert factors[0]["column"] == "a"
        assert factors[1]["banding"] == "categorical"
        assert factors[1]["column"] == "b"

    def test_empty_factors_passthrough(self):
        """A banding node with no factors passes through the DataFrame unchanged."""
        node = GraphNode(
            id="empty",
            data=NodeData(label="empty", nodeType="banding", config={"factors": []}),
        )
        _, fn, _ = _build_node_fn(node)
        lf = pl.DataFrame({"x": [5, 20]}).lazy()
        result = fn(lf).collect()
        assert result.columns == ["x"]


# ---------------------------------------------------------------------------
# Backend hardening tests
# ---------------------------------------------------------------------------


class TestBandingHardening:
    def test_non_numeric_value_raises(self):
        """A rule with val1='abc' should raise ValueError, not be silently skipped."""
        lf = pl.DataFrame({"x": [1, 2, 3]}).lazy()
        rules = [{"op1": "<=", "val1": "abc", "assignment": "low"}]
        with pytest.raises(ValueError, match="non-numeric"):
            _apply_banding(lf, "x", "band", "continuous", rules)

    def test_nan_boundary_raises(self):
        """A rule with val1='nan' should raise ValueError (non-finite)."""
        lf = pl.DataFrame({"x": [1, 2, 3]}).lazy()
        rules = [{"op1": "<=", "val1": "nan", "assignment": "low"}]
        with pytest.raises(ValueError, match="non-finite"):
            _apply_banding(lf, "x", "band", "continuous", rules)

    def test_inf_boundary_raises(self):
        """A rule with val1='inf' should raise ValueError (non-finite)."""
        lf = pl.DataFrame({"x": [1, 2, 3]}).lazy()
        rules = [{"op1": "<=", "val1": "inf", "assignment": "low"}]
        with pytest.raises(ValueError, match="non-finite"):
            _apply_banding(lf, "x", "band", "continuous", rules)

    def test_neg_inf_boundary_raises(self):
        """A rule with val1='-inf' should raise ValueError (non-finite)."""
        lf = pl.DataFrame({"x": [1, 2, 3]}).lazy()
        rules = [{"op1": "<=", "val1": "-inf", "assignment": "low"}]
        with pytest.raises(ValueError, match="non-finite"):
            _apply_banding(lf, "x", "band", "continuous", rules)

    def test_empty_assignment_skipped(self):
        """A continuous rule with assignment='' should be skipped (no '' band created)."""
        lf = pl.DataFrame({"x": [1, 50]}).lazy()
        rules = [
            {"op1": "<=", "val1": 10, "assignment": ""},
            {"op1": ">", "val1": 10, "assignment": "high"},
        ]
        result = _apply_banding(lf, "x", "band", "continuous", rules, default="dflt").collect()
        bands = result["band"].to_list()
        # x=1 matches first rule but assignment is empty, so should fall through to default
        assert bands[0] == "dflt", f"Empty assignment should be skipped, got {bands[0]!r}"
        assert bands[1] == "high"

    def test_nan_input_falls_to_default(self):
        """NaN values in the input column should get the default, not match arbitrary rules."""
        lf = pl.DataFrame({"x": [1.0, float("nan"), 10.0]}).lazy()
        rules = [
            {"op1": "<=", "val1": 5, "assignment": "low"},
            {"op1": ">", "val1": 5, "assignment": "high"},
        ]
        result = _apply_banding(lf, "x", "band", "continuous", rules, default="dflt").collect()
        bands = result["band"].to_list()
        assert bands[0] == "low"
        assert bands[1] == "dflt", f"NaN should get default, got {bands[1]!r}"
        assert bands[2] == "high"
        # V025: the NaN/Inf sanitisation must NOT overwrite the source column —
        # only ``band`` is added.  The original NaN must survive for every
        # downstream node that reads ``x``.
        x_out = result["x"].to_list()
        assert x_out[0] == 1.0
        assert math.isnan(x_out[1]), f"source NaN must be preserved, got {x_out[1]!r}"
        assert x_out[2] == 10.0

    def test_nan_input_source_column_preserved_with_downstream_node(self):
        """The source float column must still band correctly at a LATER node
        (would fail if the first banding nulled its NaN in place)."""
        lf = pl.DataFrame({"x": [1.0, float("nan"), 10.0]}).lazy()
        rules = [
            {"op1": "<=", "val1": 5, "assignment": "low"},
            {"op1": ">", "val1": 5, "assignment": "high"},
        ]
        first = _apply_banding(lf, "x", "band1", "continuous", rules, default="d1")
        second = _apply_banding(first, "x", "band2", "continuous", rules, default="d2").collect()
        assert second["band1"].to_list() == ["low", "d1", "high"]
        # If the source column had been overwritten to null by the first
        # banding, the second banding would also see null -> "d2" at index 1,
        # which it correctly does, but index 0/2 must still band from real x.
        assert second["band2"].to_list() == ["low", "d2", "high"]
        assert math.isnan(second["x"].to_list()[1])

    def test_inf_input_falls_to_default(self):
        """Inf values in the input column should get the default, not match arbitrary rules."""
        lf = pl.DataFrame({"x": [1.0, float("inf"), float("-inf")]}).lazy()
        rules = [
            {"op1": "<=", "val1": 5, "assignment": "low"},
            {"op1": ">", "val1": 5, "assignment": "high"},
        ]
        result = _apply_banding(lf, "x", "band", "continuous", rules, default="dflt").collect()
        bands = result["band"].to_list()
        assert bands[0] == "low"
        assert bands[1] == "dflt", f"Inf should get default, got {bands[1]!r}"
        assert bands[2] == "dflt", f"-Inf should get default, got {bands[2]!r}"
        # V025: +/-Inf must survive in the source column (only ``band`` added).
        x_out = result["x"].to_list()
        assert x_out[0] == 1.0
        assert math.isinf(x_out[1]) and x_out[1] > 0
        assert math.isinf(x_out[2]) and x_out[2] < 0


# ---------------------------------------------------------------------------
# Breakpoints mode tests
# ---------------------------------------------------------------------------


class TestBreakpointsMode:
    def test_breakpoints_basic(self):
        """Basic breakpoint banding: ages [18, 25, 65] with labels."""
        breakpoints = [
            {"boundary": "18", "label": "Young"},
            {"boundary": "25", "label": "Adult"},
            {"boundary": "65", "label": "Senior"},
            {"boundary": "", "label": "Elderly"},
        ]
        rules = _breakpoints_to_rules(breakpoints, right_closed=True)
        lf = pl.DataFrame({"age": [10, 18, 20, 25, 30, 65, 80]}).lazy()
        result = _apply_banding(lf, "age", "age_band", "continuous", rules).collect()
        bands = result["age_band"].to_list()
        assert bands[0] == "Young"  # 10 <= 18
        assert bands[1] == "Young"  # 18 <= 18
        assert bands[2] == "Adult"  # 18 < 20 <= 25
        assert bands[3] == "Adult"  # 25 <= 25
        assert bands[4] == "Senior"  # 25 < 30 <= 65
        assert bands[5] == "Senior"  # 65 <= 65
        assert bands[6] == "Elderly"  # > 65

    def test_breakpoints_right_closed(self):
        """When right_closed=True, upper bound is inclusive: (lower, upper]."""
        breakpoints = [
            {"boundary": "10", "label": "A"},
            {"boundary": "20", "label": "B"},
        ]
        rules = _breakpoints_to_rules(breakpoints, right_closed=True)
        lf = pl.DataFrame({"x": [10, 15, 20]}).lazy()
        result = _apply_banding(lf, "x", "band", "continuous", rules).collect()
        bands = result["band"].to_list()
        assert bands[0] == "A"  # <= 10
        assert bands[1] == "B"  # 10 < 15 <= 20
        assert bands[2] == "B"  # 20 <= 20

    def test_breakpoints_left_closed(self):
        """When right_closed=False, intervals are [lower, upper): first rule < boundary."""
        breakpoints = [
            {"boundary": "10", "label": "A"},
            {"boundary": "20", "label": "B"},
        ]
        rules = _breakpoints_to_rules(breakpoints, right_closed=False)
        lf = pl.DataFrame({"x": [5, 10, 15, 20]}).lazy()
        result = _apply_banding(lf, "x", "band", "continuous", rules).collect()
        bands = result["band"].to_list()
        assert bands[0] == "A"  # 5 < 10
        assert bands[1] == "B"  # 10 >= 10 and 10 < 20
        assert bands[2] == "B"  # 15 >= 10 and 15 < 20
        assert bands[3] is None  # 20 >= 20 but no open-ended rule

    def test_breakpoints_open_ended_last(self):
        """The last breakpoint with empty boundary catches all remaining values."""
        breakpoints = [
            {"boundary": "100", "label": "Low"},
            {"boundary": "", "label": "High"},
        ]
        rules = _breakpoints_to_rules(breakpoints, right_closed=True)
        lf = pl.DataFrame({"x": [50, 100, 200, 1000]}).lazy()
        result = _apply_banding(lf, "x", "band", "continuous", rules).collect()
        bands = result["band"].to_list()
        assert bands[0] == "Low"
        assert bands[1] == "Low"
        assert bands[2] == "High"
        assert bands[3] == "High"

    def test_apply_banding_breakpoints_type(self):
        """_apply_banding with banding_type='breakpoints' should work end-to-end."""
        rules = [
            {"boundary": "18", "label": "Minor"},
            {"boundary": "65", "label": "Adult"},
            {"boundary": "", "label": "Senior"},
        ]
        lf = pl.DataFrame({"age": [10, 18, 30, 65, 80]}).lazy()
        result = _apply_banding(
            lf, "age", "age_band", "breakpoints", rules, default="Unknown"
        ).collect()
        bands = result["age_band"].to_list()
        assert bands[0] == "Minor"
        assert bands[1] == "Minor"
        assert bands[2] == "Adult"
        assert bands[3] == "Adult"
        assert bands[4] == "Senior"

    def test_breakpoints_unsorted_input(self):
        """Breakpoints should be sorted by boundary value regardless of input order."""
        breakpoints = [
            {"boundary": "65", "label": "Senior"},
            {"boundary": "18", "label": "Young"},
            {"boundary": "25", "label": "Adult"},
        ]
        rules = _breakpoints_to_rules(breakpoints, right_closed=True)
        lf = pl.DataFrame({"age": [10, 20, 30, 70]}).lazy()
        result = _apply_banding(lf, "age", "band", "continuous", rules).collect()
        bands = result["band"].to_list()
        assert bands[0] == "Young"  # <= 18
        assert bands[1] == "Adult"  # 18 < 20 <= 25
        assert bands[2] == "Senior"  # 25 < 30 <= 65
        assert bands[3] is None  # > 65 but no open-ended

    def test_breakpoints_non_numeric_boundary_raises(self):
        """A breakpoint with boundary='abc' should raise ValueError."""
        breakpoints = [{"boundary": "abc", "label": "Bad"}]
        with pytest.raises(ValueError, match="non-numeric"):
            _breakpoints_to_rules(breakpoints)

    def test_breakpoints_nan_boundary_raises(self):
        """A breakpoint with boundary='nan' should raise ValueError."""
        breakpoints = [{"boundary": "nan", "label": "Bad"}]
        with pytest.raises(ValueError, match="non-finite"):
            _breakpoints_to_rules(breakpoints)

    def test_breakpoints_inf_boundary_raises(self):
        """A breakpoint with boundary='inf' should raise ValueError."""
        breakpoints = [{"boundary": "inf", "label": "Bad"}]
        with pytest.raises(ValueError, match="non-finite"):
            _breakpoints_to_rules(breakpoints)

    def test_breakpoints_duplicate_boundary_raises(self):
        """Duplicate breakpoint boundaries should raise ValueError."""
        breakpoints = [
            {"boundary": "10", "label": "A"},
            {"boundary": "10", "label": "B"},
        ]
        with pytest.raises(ValueError, match="Duplicate breakpoint"):
            _breakpoints_to_rules(breakpoints)

    def test_breakpoints_empty_list(self):
        """Empty breakpoints list should return empty rules."""
        assert _breakpoints_to_rules([]) == []

    def test_breakpoints_only_open_ended_raises(self):
        """A sole open-ended breakpoint cannot anchor any interval; instead of
        silently producing no rules (and no output column) it must fail loud."""
        breakpoints = [{"boundary": "", "label": "All"}]
        with pytest.raises(ValueError, match="only an open-ended boundary"):
            _breakpoints_to_rules(breakpoints)

    def test_breakpoints_multiple_open_ended_raises(self):
        """More than one open-ended boundary would silently drop all but the
        last; reject it loudly like a duplicate bounded boundary."""
        breakpoints = [
            {"boundary": "10", "label": "A"},
            {"boundary": "", "label": "B"},
            {"boundary": "", "label": "C"},
        ]
        with pytest.raises(ValueError, match="at most one open-ended boundary"):
            _breakpoints_to_rules(breakpoints)

    def test_continuous_banding_integer_column(self):
        """Integer columns should work without NaN sanitization issues."""
        lf = pl.DataFrame({"x": [1, 5, 10]}).lazy()
        rules = [
            {"op1": "<=", "val1": 5, "assignment": "low"},
            {"op1": ">", "val1": 5, "assignment": "high"},
        ]
        result = _apply_banding(lf, "x", "band", "continuous", rules).collect()
        assert result["band"].to_list() == ["low", "low", "high"]

    def test_apply_banding_breakpoints_right_closed_passthrough(self):
        """_apply_banding should respect right_closed=False for breakpoints."""
        rules = [
            {"boundary": "10", "label": "A"},
            {"boundary": "20", "label": "B"},
        ]
        lf = pl.DataFrame({"x": [5, 10, 15, 20]}).lazy()
        result = _apply_banding(
            lf,
            "x",
            "band",
            "breakpoints",
            rules,
            right_closed=False,
        ).collect()
        bands = result["band"].to_list()
        assert bands[0] == "A"  # 5 < 10
        assert bands[1] == "B"  # 10 >= 10 and 10 < 20
        assert bands[2] == "B"  # 15 >= 10 and 15 < 20
        assert bands[3] is None  # 20 >= 20 but no open-ended rule

    def test_build_banding_passes_right_closed(self):
        """_build_banding should pass rightClosed from factor config to _apply_banding."""
        factor = {
            "banding": "breakpoints",
            "column": "x",
            "outputColumn": "band",
            "rules": [
                {"boundary": "10", "label": "A"},
                {"boundary": "20", "label": "B"},
            ],
            "rightClosed": False,
        }
        node = _multi_banding_node("rc_test", [factor])
        _, fn, _ = _build_node_fn(node)
        lf = pl.DataFrame({"x": [5, 10, 15, 20]}).lazy()
        result = fn(lf).collect()
        bands = result["band"].to_list()
        # With rightClosed=False: [lower, upper) intervals
        assert bands[0] == "A"  # < 10
        assert bands[1] == "B"  # >= 10, < 20
        assert bands[2] == "B"  # >= 10, < 20
        assert bands[3] is None  # >= 20 but no open-ended

    def test_build_banding_accepts_compact_breakpoint_rules(self):
        factor = {
            "banding": "breakpoints",
            "column": "age",
            "outputColumn": "age_band",
            "rules": {"25": "young", "65": "adult", "": "senior"},
        }
        node = _multi_banding_node("age_bands", [factor])

        _, fn, _ = _build_node_fn(node)
        lf = pl.DataFrame({"age": [20, 40, 80]}).lazy()
        result = fn(lf).collect()

        assert result["age_band"].to_list() == ["young", "adult", "senior"]


# ---------------------------------------------------------------------------
# Config I/O — _strip_internal_keys
# ---------------------------------------------------------------------------


class TestStripInternalKeys:
    def test_strips_nested_underscore_keys(self):
        """_strip_internal_keys removes _prevRules from nested factor objects."""
        from haute._config_io import _strip_internal_keys

        config = {
            "factors": [
                {
                    "banding": "continuous",
                    "column": "age",
                    "_prevRules": {"categorical": []},
                    "rules": [
                        {"op1": ">", "val1": "10", "_id": "rule_1", "assignment": "a"},
                    ],
                }
            ]
        }
        result = _strip_internal_keys(config)
        assert "_prevRules" not in result["factors"][0]
        assert "_id" not in result["factors"][0]["rules"][0]
        assert result["factors"][0]["banding"] == "continuous"
        assert result["factors"][0]["rules"][0]["op1"] == ">"

    def test_preserves_non_underscore_keys(self):
        from haute._config_io import _strip_internal_keys

        config = {"factors": [{"column": "x", "rules": []}]}
        result = _strip_internal_keys(config)
        assert result == config
