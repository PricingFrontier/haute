"""Tests for haute._parser_regex — regex-based fallback parser."""

from __future__ import annotations

import pytest

from haute._parser_regex import (
    _RE_CONNECT,
    _RE_DECORATOR,
    _RE_PIPELINE_META,
    _find_function_blocks,
    _parse_decorator_kwargs_regex,
    fallback_parse,
)
from haute.errors import ConfigError

# ---------------------------------------------------------------------------
# _find_function_blocks
# ---------------------------------------------------------------------------


class TestFindFunctionBlocks:
    def test_single_decorated_function(self) -> None:
        source = "@pipeline.polars()\ndef my_func(df):\n    return df\n"
        blocks = _find_function_blocks(source)
        assert len(blocks) == 1
        assert blocks[0]["func_name"] == "my_func"
        assert blocks[0]["param_names"] == ["df"]
        assert "return df" in blocks[0]["body_text"]

    def test_multiple_functions(self) -> None:
        source = (
            "@pipeline.polars()\n"
            "def alpha(df):\n"
            "    return df\n"
            "\n"
            "@pipeline.polars()\n"
            "def beta(df):\n"
            "    return df\n"
        )
        blocks = _find_function_blocks(source)
        assert len(blocks) == 2
        assert blocks[0]["func_name"] == "alpha"
        assert blocks[1]["func_name"] == "beta"

    def test_multiple_params(self) -> None:
        source = "@pipeline.polars()\ndef join(left, right):\n    return left\n"
        blocks = _find_function_blocks(source)
        assert blocks[0]["param_names"] == ["left", "right"]

    def test_typed_params_strips_annotations(self) -> None:
        source = (
            "@pipeline.polars()\ndef transform(df: pl.LazyFrame) -> pl.LazyFrame:\n    return df\n"
        )
        blocks = _find_function_blocks(source)
        assert blocks[0]["param_names"] == ["df"]

    def test_no_params(self) -> None:
        source = "@pipeline.data_source(path='input.csv')\ndef api_input():\n    pass\n"
        blocks = _find_function_blocks(source)
        assert blocks[0]["param_names"] == []

    def test_body_with_multiple_lines(self) -> None:
        source = "@pipeline.polars()\ndef calc(df):\n    x = 1\n    y = 2\n    return df\n"
        blocks = _find_function_blocks(source)
        assert "x = 1" in blocks[0]["body_text"]
        assert "y = 2" in blocks[0]["body_text"]
        assert "return df" in blocks[0]["body_text"]

    def test_records_def_start_line_and_stops_body_at_next_top_level_stmt(self) -> None:
        source = (
            "import haute\n"
            "\n"
            "# A comment before the decorator\n"
            "@pipeline.polars()\n"
            "def calc(df):\n"
            "    x = 1\n"
            "    return df\n"
            "\n"
            "next_value = 42\n"
        )
        blocks = _find_function_blocks(source)

        assert blocks[0]["start_line"] == 4
        assert blocks[0]["body_text"] == "    x = 1\n    return df"

    def test_empty_source(self) -> None:
        assert _find_function_blocks("") == []

    def test_no_decorated_functions(self) -> None:
        source = "def regular(x):\n    return x\n"
        assert _find_function_blocks(source) == []

    def test_decorator_with_kwargs(self) -> None:
        source = '@pipeline.data_source(path="data.csv")\ndef load(df):\n    return df\n'
        blocks = _find_function_blocks(source)
        assert len(blocks) == 1
        assert 'path="data.csv"' in blocks[0]["decorator_text"]

    def test_unrecognised_method_skipped(self) -> None:
        """@pipeline.connect(...) should not be matched as a node decorator."""
        source = '@pipeline.connect("a", "b")\ndef not_a_node(df):\n    return df\n'
        blocks = _find_function_blocks(source)
        assert blocks == []

    def test_bare_decorator(self) -> None:
        """@pipeline.polars (no parens) should be matched."""
        source = "@pipeline.polars\ndef my_func(df):\n    return df\n"
        blocks = _find_function_blocks(source)
        assert len(blocks) == 1
        assert blocks[0]["func_name"] == "my_func"

    def test_explicit_node_type_set(self) -> None:
        """Each block should carry the explicit NodeType from the decorator."""
        source = "@pipeline.banding()\ndef band(df):\n    return df\n"
        blocks = _find_function_blocks(source)
        assert len(blocks) == 1
        assert blocks[0]["explicit_node_type"] == "banding"


# ---------------------------------------------------------------------------
# _parse_decorator_kwargs_regex
# ---------------------------------------------------------------------------


class TestParseDecoratorKwargsRegex:
    def test_string_kwargs(self) -> None:
        text = '@pipeline.data_source(path="data.csv", name="load")'
        result = _parse_decorator_kwargs_regex(text)
        assert result["path"] == "data.csv"
        assert result["name"] == "load"

    def test_boolean_kwargs(self) -> None:
        text = "@pipeline.polars(api_input=True, output=False)"
        result = _parse_decorator_kwargs_regex(text)
        assert result["api_input"] is True
        assert result["output"] is False

    def test_mixed_kwargs(self) -> None:
        text = '@pipeline.data_source(path="x.csv", output=True)'
        result = _parse_decorator_kwargs_regex(text)
        assert result["path"] == "x.csv"
        assert result["output"] is True

    def test_bare_decorator_returns_empty(self) -> None:
        text = "@pipeline.polars"
        result = _parse_decorator_kwargs_regex(text)
        assert result == {}

    def test_empty_parens(self) -> None:
        text = "@pipeline.polars()"
        result = _parse_decorator_kwargs_regex(text)
        assert result == {}

    def test_single_quoted_values(self) -> None:
        text = "@pipeline.data_source(path='data.csv')"
        result = _parse_decorator_kwargs_regex(text)
        assert result["path"] == "data.csv"


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------


class TestRegexPatterns:
    def test_pipeline_meta_basic(self) -> None:
        source = 'pipeline = haute.Pipeline("my_pipe")'
        m = _RE_PIPELINE_META.search(source)
        assert m is not None
        assert m.group(1) == "my_pipe"

    def test_pipeline_meta_with_description(self) -> None:
        source = 'pipeline = haute.Pipeline("my_pipe", description="A test pipeline")'
        m = _RE_PIPELINE_META.search(source)
        assert m is not None
        assert m.group(1) == "my_pipe"
        assert m.group(2) == "A test pipeline"

    def test_connect_pattern(self) -> None:
        line = 'pipeline.connect("node_a", "node_b")'
        matches = _RE_CONNECT.findall(line)
        assert matches == [("node_a", "node_b")]

    def test_connect_pattern_single_quotes(self) -> None:
        line = "pipeline.connect('x', 'y')"
        matches = _RE_CONNECT.findall(line)
        assert matches == [("x", "y")]

    def test_decorator_pattern_bare(self) -> None:
        source = "@pipeline.polars\ndef foo(df):\n    pass\n"
        matches = list(_RE_DECORATOR.finditer(source))
        assert len(matches) == 1

    def test_decorator_pattern_with_args(self) -> None:
        source = '@pipeline.data_source(path="x")\ndef bar(df):\n    pass\n'
        matches = list(_RE_DECORATOR.finditer(source))
        assert len(matches) == 1
        assert matches[0].group(3) == "bar"

    def test_decorator_pattern_does_not_match_connect(self) -> None:
        """The regex matches any @pipeline.<method>, but _find_function_blocks filters."""
        source = '@pipeline.connect("a", "b")\ndef not_a_node(df):\n    pass\n'
        # The regex itself matches (connect is \w+), but _find_function_blocks filters it
        matches = list(_RE_DECORATOR.finditer(source))
        assert len(matches) == 1
        assert matches[0].group(2) == "connect"


# ---------------------------------------------------------------------------
# fallback_parse (integration)
# ---------------------------------------------------------------------------


class TestFallbackParse:
    def test_basic_pipeline_with_syntax_error(self) -> None:
        source = """\
import polars as pl
import haute

pipeline = haute.Pipeline("test_pipe", description="A test")

@pipeline.polars()
def transform(df):
    return df

@pipeline.polars()
def output_node(transform):
    return transform

pipeline.connect("transform", "output_node")

# Syntax error below — fallback_parse should still work above
x = {unclosed
"""
        err = SyntaxError("invalid syntax")
        err.lineno = 17
        graph = fallback_parse(source, "test.py", err)

        assert graph.pipeline_name == "test_pipe"
        assert graph.pipeline_description == "A test"
        assert graph.warning is not None
        assert "syntax errors" in graph.warning
        assert len(graph.nodes) >= 2

    def test_empty_source_returns_graph(self) -> None:
        err = SyntaxError("empty")
        err.lineno = 1
        graph = fallback_parse("", "empty.py", err)
        assert graph.pipeline_name == "main"
        assert graph.nodes == []

    def test_pipeline_name_fallback(self) -> None:
        source = "@pipeline.polars()\ndef foo(df):\n    return df\n"
        err = SyntaxError("oops")
        err.lineno = 1
        graph = fallback_parse(source, "file.py", err)
        assert graph.pipeline_name == "main"  # default when no Pipeline() found

    def test_edges_extracted(self) -> None:
        source = """\
import haute
pipeline = haute.Pipeline("p")

@pipeline.polars()
def a(df):
    return df

@pipeline.polars()
def b(a):
    return a

pipeline.connect("a", "b")
"""
        err = SyntaxError("test")
        err.lineno = 99
        graph = fallback_parse(source, "f.py", err)
        edge_pairs = [(e.source, e.target) for e in graph.edges]
        assert ("a", "b") in edge_pairs

    def test_config_backed_node_without_sidecar_fails_loudly(self, tmp_path) -> None:
        source = (
            'pipeline = haute.Pipeline("broken")\n'
            '@pipeline.data_source(config="config/data_source/load.json")\n'
            "def load():\n"
            '    return pl.scan_csv("input.csv")\n'
        )
        err = SyntaxError("broken")
        err.lineno = 2

        with pytest.raises(ConfigError, match="Node config must be stored in a JSON sidecar"):
            fallback_parse(source, str(tmp_path / "broken.py"), err)

    def test_source_file_stored(self) -> None:
        err = SyntaxError("x")
        err.lineno = 1
        graph = fallback_parse("", "my_pipeline.py", err)
        assert graph.source_file == "my_pipeline.py"

    def test_node_with_syntax_error_in_body(self) -> None:
        """A function whose body has a syntax error should still produce a node."""
        source = """\
import haute
pipeline = haute.Pipeline("p")

@pipeline.polars()
def good(df):
    return df

@pipeline.polars()
def bad(df):
    x = {unclosed
"""
        err = SyntaxError("bad body")
        err.lineno = 10
        graph = fallback_parse(source, "f.py", err)
        node_ids = [n.id for n in graph.nodes]
        assert "good" in node_ids
        assert "bad" in node_ids
