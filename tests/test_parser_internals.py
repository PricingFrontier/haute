"""Tests for parser internals - unit tests for extraction and config building."""

from __future__ import annotations

from haute._parser_helpers import (
    _build_node_config,
    _dedent,
    _extract_external_user_code,
    _extract_model_score_user_code,
    _extract_preamble,
    _extract_user_code,
    _strip_docstring,
)

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
    def test_generated_scoring_body_returns_empty(self):
        """Body without post-processing is entirely auto-generated → empty string."""
        body = (
            '    """doc"""\n'
            "    from haute.graph_utils import score_from_config\n"
            '    result = score_from_config(df, config="config/model_scoring/m.json")\n'
            "    return result"
        )
        assert _extract_model_score_user_code(body) == ""

    def test_extracts_code_after_scoring_call(self):
        """User code after the scoring call is extracted and dedented."""
        body = (
            '    """doc"""\n'
            "    from haute.graph_utils import score_from_config\n"
            '    result = score_from_config(df, config="config/model_scoring/m.json")\n'
            '    df = df.with_columns(doubled=pl.col("prediction") * 2)\n'
            "    return result"
        )
        result = _extract_model_score_user_code(body)
        assert "doubled" in result
        assert "return result" not in result

    def test_empty_body(self):
        assert _extract_model_score_user_code("") == ""

    def test_multiline_user_code(self):
        """Multiple lines of user code are all extracted."""
        body = (
            "    from haute.graph_utils import score_from_config\n"
            '    result = score_from_config(df, config="config/model_scoring/m.json")\n'
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

    def test_api_input_v2_keys_pass_through(self):
        """Post-commit-5.5: top-level `row_id_column` decorator kwarg is no
        longer carried into config (it lives per-table inside `tables[]`
        in v2). The decorator-level surface for apiInput is reduced to
        `path` plus optional `contract` and `tables`.
        """
        config = _build_node_config(
            "apiInput",
            {"path": "d.json", "api_input": True, "contract": "opaque"},
            "",
            [],
        )
        assert config["path"] == "d.json"
        assert config["contract"] == "opaque"
        assert "row_id_column" not in config

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
