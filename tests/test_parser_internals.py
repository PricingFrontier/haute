"""Tests for parser internals - unit tests for extraction and config building."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from haute._parser_helpers import (
    _build_node_config,
    _dedent,
    _extract_external_user_code,
    _extract_model_score_user_code,
    _extract_preamble,
    _extract_user_code,
    _strip_docstring,
)
from haute.parser import parse_pipeline_file, parse_pipeline_source


# ---------------------------------------------------------------------------
# _strip_docstring
# ---------------------------------------------------------------------------


class TestStripDocstring:
    def test_single_line_docstring(self):
        lines = ['    """This is a docstring."""', "    return df"]
        result = _strip_docstring(lines)
        assert result == ["    return df"]

    def test_multi_line_docstring(self):
        # Closing triple-quote shares line with content (common in codegen output)
        lines = [
            '    """First line.',
            '    Second line."""',
            "    return df",
        ]
        result = _strip_docstring(lines)
        assert result == ["    return df"]

    def test_standalone_closing_triple_quote(self):
        # Closing triple-quote on its own line (standard Python docstring style)
        lines = [
            '    """First line.',
            "    Second line.",
            '    """',
            "    return df",
        ]
        result = _strip_docstring(lines)
        assert result == ["    return df"]

    def test_no_docstring(self):
        lines = ["    x = 1", "    return x"]
        result = _strip_docstring(lines)
        assert result == ["    x = 1", "    return x"]

    def test_empty_input(self):
        assert _strip_docstring([]) == []


# ---------------------------------------------------------------------------
# _dedent
# ---------------------------------------------------------------------------


class TestDedent:
    def test_removes_common_indent(self):
        code = "    x = 1\n    y = 2"
        assert _dedent(code) == "x = 1\ny = 2"

    def test_preserves_relative_indent(self):
        code = "    if True:\n        x = 1"
        assert _dedent(code) == "if True:\n    x = 1"

    def test_empty_string(self):
        assert _dedent("") == ""

    def test_no_indent(self):
        assert _dedent("x = 1\ny = 2") == "x = 1\ny = 2"


# ---------------------------------------------------------------------------
# _extract_user_code
# ---------------------------------------------------------------------------


class TestExtractUserCode:
    def test_codegen_style_df_assignment(self):
        """Codegen produces: df = source.filter(...)\nreturn df"""
        body = '    """doc"""\n    df = source.filter(pl.col("x") > 0)\n    return df'
        result = _extract_user_code(body, ["source"])
        assert "source" in result
        assert ".filter" in result
        assert "return" not in result
        assert "df =" in result

    def test_single_return_expression(self):
        body = '    """doc"""\n    return source.with_columns(y=pl.lit(1))'
        result = _extract_user_code(body, ["source"])
        assert "source.with_columns" in result
        assert "return" not in result

    def test_explicit_assignment(self):
        body = '    """doc"""\n    df = df.filter(pl.col("x") > 0)\n    return df'
        result = _extract_user_code(body, ["df"])
        assert "df =" in result
        assert ".filter" in result

    def test_empty_body(self):
        assert _extract_user_code("", ["df"]) == ""

    def test_docstring_only(self):
        body = '    """Just a docstring."""'
        result = _extract_user_code(body, ["df"])
        # After stripping docstring, nothing left
        assert result == ""


# ---------------------------------------------------------------------------
# _extract_external_user_code
# ---------------------------------------------------------------------------


class TestExtractExternalUserCode:
    def test_strips_import_and_with_block(self):
        body = (
            '    """doc"""\n'
            "    import pickle\n"
            '    with open("model.pkl", "rb") as _f:\n'
            "        obj = pickle.load(_f)\n"
            "    df = df.with_columns(pred=pl.lit(obj.predict()))\n"
            "    return df"
        )
        result = _extract_external_user_code(body, ["df"])
        assert "df = df.with_columns" in result
        assert "import pickle" not in result
        assert "with open" not in result
        assert "return df" not in result

    def test_strips_obj_assignment(self):
        body = (
            '    """doc"""\n'
            "    import joblib\n"
            '    obj = joblib.load("model.pkl")\n'
            "    df = df.with_columns(score=pl.lit(42))\n"
            "    return df"
        )
        result = _extract_external_user_code(body, ["df"])
        assert "score" in result
        assert "joblib" not in result

    def test_strips_load_external_object_boilerplate(self):
        body = (
            '    """doc"""\n'
            "    from haute.graph_utils import load_external_object\n"
            '    obj = load_external_object("model.cbm", "catboost", "regressor")\n'
            "    df = df.with_columns(pred=pl.lit(obj.predict()))\n"
            "    return df"
        )
        result = _extract_external_user_code(body, ["df"])
        assert "df = df.with_columns" in result
        assert "load_external_object" not in result
        assert "import" not in result

    def test_empty_body(self):
        assert _extract_external_user_code("", ["df"]) == ""

    def test_only_boilerplate_returns_empty(self):
        body = (
            '    """doc"""\n'
            "    import pickle\n"
            '    with open("m.pkl", "rb") as f:\n'
            "        obj = pickle.load(f)\n"
            "    return df"
        )
        result = _extract_external_user_code(body, ["df"])
        assert result == ""


# ---------------------------------------------------------------------------
# _extract_model_score_user_code
# ---------------------------------------------------------------------------


class TestExtractModelScoreUserCode:
    def test_no_sentinel_returns_empty(self):
        """Body without sentinel is entirely auto-generated → empty string."""
        body = (
            '    """doc"""\n'
            "    from haute.graph_utils import load_mlflow_model\n"
            '    model = load_mlflow_model(source_type="run", run_id="abc")\n'
            "    df_eager = df.collect()\n"
            "    result = df_eager.lazy()\n"
            "    return result"
        )
        assert _extract_model_score_user_code(body) == ""

    def test_extracts_code_after_sentinel(self):
        """User code after sentinel is extracted and dedented."""
        body = (
            '    """doc"""\n'
            "    df_eager = df.collect()\n"
            "    result = df_eager.lazy()\n"
            "    # -- user code --\n"
            '    df = df.with_columns(doubled=pl.col("prediction") * 2)\n'
            "    return result"
        )
        result = _extract_model_score_user_code(body)
        assert "doubled" in result
        assert "return result" not in result

    def test_sentinel_but_only_return(self):
        """Sentinel present but only 'return result' after → empty string."""
        body = "    result = df_eager.lazy()\n    # -- user code --\n    return result"
        assert _extract_model_score_user_code(body) == ""

    def test_empty_body(self):
        assert _extract_model_score_user_code("") == ""

    def test_multiline_user_code(self):
        """Multiple lines of user code are all extracted."""
        body = (
            "    result = df_eager.lazy()\n"
            "    # -- user code --\n"
            "    x = 1\n"
            "    y = x + 2\n"
            "    df = df.with_columns(z=pl.lit(y))\n"
            "    return result"
        )
        result = _extract_model_score_user_code(body)
        assert "x = 1" in result
        assert "y = x + 2" in result
        assert "z=pl.lit(y)" in result
        assert "return result" not in result


# ---------------------------------------------------------------------------
# _build_node_config
# ---------------------------------------------------------------------------


class TestBuildNodeConfig:
    def test_data_source_flat_file(self):
        config = _build_node_config("dataSource", {"path": "d.parquet"}, "", [])
        assert config["path"] == "d.parquet"
        assert config["sourceType"] == "flat_file"

    def test_api_input_with_row_id(self):
        config = _build_node_config(
            "apiInput",
            {"path": "d.parquet", "api_input": True, "row_id_column": "policy_id"},
            "",
            [],
        )
        assert config["row_id_column"] == "policy_id"
        assert config["path"] == "d.parquet"

    def test_live_switch(self):
        config = _build_node_config(
            "liveSwitch",
            {"live_switch": True, "input_scenario_map": {"live": "live", "nb": "test_batch"}},
            "",
            ["live", "nb", "rn"],
        )
        assert config["input_scenario_map"] == {"live": "live", "nb": "test_batch"}
        assert config["inputs"] == ["live", "nb", "rn"]

    def test_data_source_databricks(self):
        config = _build_node_config(
            "dataSource",
            {"table": "catalog.schema.tbl"},
            "",
            [],
        )
        assert config["sourceType"] == "databricks"
        assert config["table"] == "catalog.schema.tbl"

    def test_model_score(self):
        config = _build_node_config(
            "modelScore",
            {
                "model_score": True,
                "source_type": "run",
                "run_id": "abc123",
                "artifact_path": "model.cbm",
                "task": "regression",
                "output_column": "prediction",
            },
            "",
            ["df"],
        )
        assert config["sourceType"] == "run"
        assert config["run_id"] == "abc123"
        assert config["task"] == "regression"

    def test_rating_step(self):
        config = _build_node_config(
            "ratingStep",
            {
                "tables": [
                    {
                        "name": "T",
                        "factors": ["x"],
                        "output_column": "out",
                        "entries": [{"x": "a", "value": 1.0}],
                    }
                ]
            },
            "",
            ["df"],
        )
        assert len(config["tables"]) == 1
        assert config["tables"][0]["factors"] == ["x"]
        assert config["tables"][0]["outputColumn"] == "out"

    def test_data_sink(self):
        config = _build_node_config("dataSink", {"sink": "out.csv", "format": "csv"}, "", ["df"])
        assert config["path"] == "out.csv"
        assert config["format"] == "csv"

    def test_external_file(self):
        config = _build_node_config(
            "externalFile",
            {"external": "model.pkl", "file_type": "pickle"},
            "",
            ["df"],
        )
        assert config["path"] == "model.pkl"
        assert config["fileType"] == "pickle"

    def test_external_file_catboost(self):
        config = _build_node_config(
            "externalFile",
            {"external": "m.cbm", "file_type": "catboost", "model_class": "regressor"},
            "",
            ["df"],
        )
        assert config["fileType"] == "catboost"
        assert config["modelClass"] == "regressor"

    def test_output(self):
        config = _build_node_config("output", {"fields": ["a", "b"]}, "", ["df"])
        assert config["fields"] == ["a", "b"]

    def test_transform(self):
        body = '    """doc"""\n    return df'
        config = _build_node_config("polars", {}, body, ["df"])
        assert "code" in config

    def test_transform_with_selected_columns(self):
        """selected_columns in decorator kwargs must round-trip through the parser."""
        body = '    """doc"""\n    return df'
        sel = ["quote_id", "premium", "sale_flag"]
        config = _build_node_config("polars", {"selected_columns": sel}, body, ["df"])
        assert config["selected_columns"] == sel
        assert "code" in config

    def test_transform_without_selected_columns(self):
        """When no selected_columns kwarg, config should not contain the key."""
        body = '    """doc"""\n    return df'
        config = _build_node_config("polars", {}, body, ["df"])
        assert "selected_columns" not in config


# ---------------------------------------------------------------------------
# _extract_preamble
# ---------------------------------------------------------------------------


class TestExtractPreamble:
    def test_extracts_between_imports_and_pipeline(self):
        source = (
            "import polars as pl\n"
            "import haute\n"
            "\n"
            "from pathlib import Path\n"
            "DATA = 42\n"
            "\n"
            'pipeline = haute.Pipeline("test")\n'
        )
        preamble = _extract_preamble(source)
        assert "from pathlib import Path" in preamble
        assert "DATA = 42" in preamble
        assert "import polars" not in preamble
        assert "Pipeline" not in preamble

    def test_no_preamble(self):
        source = 'import polars as pl\nimport haute\n\npipeline = haute.Pipeline("test")\n'
        preamble = _extract_preamble(source)
        assert preamble == ""

    def test_no_standard_imports(self):
        source = 'pipeline = haute.Pipeline("test")\n'
        assert _extract_preamble(source) == ""


# ---------------------------------------------------------------------------
# _fallback_parse (syntax error path)
# ---------------------------------------------------------------------------


class TestFallbackParse:
    def test_parses_despite_syntax_error(self, tmp_path):
        """File with syntax error in one function should still parse other nodes."""
        code = '''\
import polars as pl
import haute

pipeline = haute.Pipeline("broken")


@pipeline.data_source(path="data.parquet")
def source() -> pl.DataFrame:
    """Good node."""
    return pl.scan_parquet("data.parquet")


@pipeline.polars
def bad_node(df: pl.DataFrame) -> pl.DataFrame:
    """This has a syntax error."""
    return df.with_columns(
        # missing closing paren


@pipeline.polars
def good_node(df: pl.DataFrame) -> pl.DataFrame:
    """This is fine."""
    return df
'''
        p = tmp_path / "broken.py"
        p.write_text(code)
        graph = parse_pipeline_file(p)

        # Should still extract nodes despite syntax error
        assert graph.pipeline_name == "broken"
        assert graph.warning is not None
        assert len(graph.nodes) == 3
        node_ids = {n.id for n in graph.nodes}
        assert {"source", "bad_node", "good_node"} == node_ids

    def test_fallback_extracts_edges(self, tmp_path):
        """Regex fallback should find pipeline.connect() calls."""
        code = """\
import polars as pl
import haute

pipeline = haute.Pipeline("edges")


@pipeline.data_source(path="data.parquet")
def a() -> pl.DataFrame:
    return pl.DataFrame(

@pipeline.polars
def b(a: pl.DataFrame) -> pl.DataFrame:
    return a

pipeline.connect("a", "b")
"""
        p = tmp_path / "edges.py"
        p.write_text(code)
        graph = parse_pipeline_file(p)
        edge_pairs = [(e.source, e.target) for e in graph.edges]
        assert ("a", "b") in edge_pairs


# ---------------------------------------------------------------------------
# Roundtrip: parse → codegen → parse (deep comparison)
# ---------------------------------------------------------------------------


class TestDeepRoundtrip:
    def test_roundtrip_preserves_node_types_and_configs(self, tmp_path):
        from haute._config_io import collect_node_configs
        from haute.codegen import graph_to_code

        code = '''\
import polars as pl
import haute

pipeline = haute.Pipeline("roundtrip", description="Test pipeline")


@pipeline.data_source(path="data.parquet")
def source() -> pl.DataFrame:
    """Load data."""
    return pl.scan_parquet("data.parquet")


@pipeline.polars
def transform(source: pl.DataFrame) -> pl.DataFrame:
    """Clean data."""
    df = source.filter(pl.col("x") > 0)
    return df


@pipeline.output(fields=["x", "y"])
def output(transform: pl.DataFrame) -> pl.DataFrame:
    """Final output."""
    return transform.select("x", "y")


pipeline.connect("source", "transform")
pipeline.connect("transform", "output")
'''
        p = tmp_path / "original.py"
        p.write_text(code)
        graph1 = parse_pipeline_file(p)

        generated = graph_to_code(
            graph1,
            pipeline_name=graph1.pipeline_name,
            description=graph1.pipeline_description or "",
            preamble=graph1.preamble or "",
        )
        p2 = tmp_path / "generated.py"
        p2.write_text(generated)

        # Write config JSON sidecar files so the parser can resolve them
        for rel_path, json_content in collect_node_configs(graph1).items():
            cfg_file = tmp_path / rel_path
            cfg_file.parent.mkdir(parents=True, exist_ok=True)
            cfg_file.write_text(json_content)

        graph2 = parse_pipeline_file(p2)

        # Same node count
        assert len(graph1.nodes) == len(graph2.nodes)

        # Same node types
        types1 = {n.id: n.data.nodeType for n in graph1.nodes}
        types2 = {n.id: n.data.nodeType for n in graph2.nodes}
        assert types1 == types2

        # Same edge pairs
        edges1 = {(e.source, e.target) for e in graph1.edges}
        edges2 = {(e.source, e.target) for e in graph2.edges}
        assert edges1 == edges2

        # Source config preserved
        src1 = next(n for n in graph1.nodes if n.id == "source")
        src2 = next(n for n in graph2.nodes if n.id == "source")
        assert src1.data.config["path"] == src2.data.config["path"]

        # Output fields preserved
        out1 = next(n for n in graph1.nodes if n.id == "output")
        out2 = next(n for n in graph2.nodes if n.id == "output")
        assert out1.data.config.get("fields") == out2.data.config.get("fields")

        # Transform code preserved
        t1 = next(n for n in graph1.nodes if n.id == "transform")
        t2 = next(n for n in graph2.nodes if n.id == "transform")
        assert t1.data.config.get("code") == t2.data.config.get("code")

        # Pipeline name preserved
        assert graph1.pipeline_name == graph2.pipeline_name

    def test_roundtrip_selected_columns_on_transform(self, tmp_path):
        """selected_columns on a transform node must survive parse → codegen → parse."""
        from haute.codegen import graph_to_code

        code = '''\
import polars as pl
import haute

pipeline = haute.Pipeline("sel_test")


@pipeline.data_source(path="data.parquet")
def source() -> pl.LazyFrame:
    """source node"""
    return pl.scan_parquet("data.parquet")


@pipeline.polars(selected_columns=['quote_id', 'premium', 'sale_flag'])
def features(source: pl.LazyFrame) -> pl.LazyFrame:
    """features node"""
    return source


pipeline.connect("source", "features")
'''
        p = tmp_path / "sel.py"
        p.write_text(code)
        graph1 = parse_pipeline_file(p)

        feat1 = next(n for n in graph1.nodes if n.id == "features")
        assert feat1.data.config.get("selected_columns") == [
            "quote_id",
            "premium",
            "sale_flag",
        ]

        # Generate code from the parsed graph, then re-parse
        generated = graph_to_code(
            graph1,
            pipeline_name=graph1.pipeline_name,
            description=graph1.pipeline_description or "",
            preamble=graph1.preamble or "",
        )
        p2 = tmp_path / "sel_gen.py"
        p2.write_text(generated)
        graph2 = parse_pipeline_file(p2)

        feat2 = next(n for n in graph2.nodes if n.id == "features")
        assert feat2.data.config.get("selected_columns") == [
            "quote_id",
            "premium",
            "sale_flag",
        ]

    def test_roundtrip_transform_without_selected_columns(self, tmp_path):
        """A transform with no selected_columns should not gain one after round-trip."""
        from haute.codegen import graph_to_code

        code = '''\
import polars as pl
import haute

pipeline = haute.Pipeline("plain_test")


@pipeline.data_source(path="data.parquet")
def source() -> pl.LazyFrame:
    """source node"""
    return pl.scan_parquet("data.parquet")


@pipeline.polars
def clean(source: pl.LazyFrame) -> pl.LazyFrame:
    """clean node"""
    return source


pipeline.connect("source", "clean")
'''
        p = tmp_path / "plain.py"
        p.write_text(code)
        graph1 = parse_pipeline_file(p)

        clean1 = next(n for n in graph1.nodes if n.id == "clean")
        assert "selected_columns" not in clean1.data.config

        generated = graph_to_code(
            graph1,
            pipeline_name=graph1.pipeline_name,
            description=graph1.pipeline_description or "",
            preamble=graph1.preamble or "",
        )
        p2 = tmp_path / "plain_gen.py"
        p2.write_text(generated)
        graph2 = parse_pipeline_file(p2)

        clean2 = next(n for n in graph2.nodes if n.id == "clean")
        assert "selected_columns" not in clean2.data.config
