"""Tests for banding node type — continuous and categorical."""

from __future__ import annotations

import polars as pl
import pytest

from haute.graph_utils import GraphNode, NodeData, PipelineGraph
from haute.executor import _apply_banding, _build_node_fn, execute_graph
from tests.conftest import make_edge as _edge, make_source_node as _source_node


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
    def test_parse_banding_node(self):
        from haute.parser import parse_pipeline_source

        code = '''\
import polars as pl
import haute

pipeline = haute.Pipeline("test")

@pipeline.banding(banding="continuous", column="age", output_column="age_band", rules=[{"op1": "<=", "val1": 25, "assignment": "young"}])
def band_age(df: pl.LazyFrame) -> pl.LazyFrame:
    """Band age into age_band"""
    return df
'''
        graph = parse_pipeline_source(code)
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

    def test_parse_categorical_banding(self):
        from haute.parser import parse_pipeline_source

        code = '''\
import polars as pl
import haute

pipeline = haute.Pipeline("test")

@pipeline.banding(banding="categorical", column="prop", output_column="prop_band", rules=[{"value": "House", "assignment": "Residential"}])
def band_prop(df: pl.LazyFrame) -> pl.LazyFrame:
    """Band property type"""
    return df
'''
        graph = parse_pipeline_source(code)
        node = graph.nodes[0]
        assert node.data.nodeType == "banding"
        assert node.data.config["factors"][0]["banding"] == "categorical"


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
