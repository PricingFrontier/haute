"""Tests for haute._parser_regex — regex-based fallback parser."""

from __future__ import annotations

import pytest

from haute._parser_regex import (
    _RE_DECORATOR,
    _RE_PIPELINE_META,
    _find_connect_calls,
    _find_function_blocks,
    _parse_decorator_kwargs_regex,
    fallback_parse,
)
from haute.errors import ConfigError, ParseError

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

    def test_explore_decorator_is_recognised(self) -> None:
        source = "@pipeline.explore\ndef inspect(df):\n    return df\n"
        blocks = _find_function_blocks(source)
        assert len(blocks) == 1
        assert blocks[0]["explicit_node_type"] == "explore"


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

    def test_bare_name_kwarg_rejected_loudly(self) -> None:
        """Tier-2 fallback policy: unresolved names are not serialized."""
        with pytest.raises(ValueError, match="unresolved name"):
            _parse_decorator_kwargs_regex("@pipeline.polars(selected_columns=COLS)")

    def test_non_literal_expression_kwarg_unparsed_prior_art(self) -> None:
        """Tier-3 prior art, intentionally deferred to the W5 audit."""
        result = _parse_decorator_kwargs_regex('@pipeline.data_source(path=Path("data.csv"))')
        assert result["path"] == "Path('data.csv')"


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
# _find_connect_calls — remediation 5.7
# ---------------------------------------------------------------------------


class TestFindConnectCalls:
    """The regex fallback must recover every connect() form codegen emits.

    Codegen (`codegen.py::_format_connect`) emits exactly four shapes:

    * ``pipeline.connect("a", "b")``
    * ``pipeline.connect("a", "b", source_port="p")``
    * ``pipeline.connect("a", "b", target_port="q")``
    * ``pipeline.connect("a", "b", source_port="p", target_port="q")``

    with port values serialised via ``json.dumps`` (so they can carry
    ``\\"`` and ``\\uXXXX`` escapes).  The old ``_RE_CONNECT`` regex
    required the closing paren immediately after the second string and
    silently dropped every multi-arg form — losing edges on the recovery
    path.  Anything we can see but cannot parse must fail LOUD: a
    plausible-but-incomplete graph corrupts the file on the next save.
    """

    def test_bare_two_arg_form(self) -> None:
        assert _find_connect_calls('pipeline.connect("a", "b")') == [("a", "b", None, None)]

    def test_bare_two_arg_single_quotes(self) -> None:
        assert _find_connect_calls("pipeline.connect('x', 'y')") == [("x", "y", None, None)]

    def test_source_port_form(self) -> None:
        src = 'pipeline.connect("a", "b", source_port="p")'
        assert _find_connect_calls(src) == [("a", "b", "p", None)]

    def test_target_port_form(self) -> None:
        src = 'pipeline.connect("a", "b", target_port="base")'
        assert _find_connect_calls(src) == [("a", "b", None, "base")]

    def test_both_ports_form(self) -> None:
        src = 'pipeline.connect("quotes", "join", source_port="policies", target_port="base")'
        assert _find_connect_calls(src) == [("quotes", "join", "policies", "base")]

    def test_port_with_json_escaped_quote(self) -> None:
        """json.dumps emits ``\\"`` for user labels containing quotes."""
        src = 'pipeline.connect("a", "b", target_port="he said \\"hi\\"")'
        assert _find_connect_calls(src) == [("a", "b", None, 'he said "hi"')]

    def test_port_with_unicode_escape(self) -> None:
        """json.dumps emits ``\\uXXXX`` for non-ASCII labels."""
        src = 'pipeline.connect("a", "b", source_port="caf\\u00e9")'
        assert _find_connect_calls(src) == [("a", "b", "café", None)]

    def test_port_containing_paren(self) -> None:
        """A ``)`` inside a port string must not truncate the call span."""
        src = 'pipeline.connect("a", "b", target_port="base (v2)")'
        assert _find_connect_calls(src) == [("a", "b", None, "base (v2)")]

    def test_multiline_connect_call(self) -> None:
        src = 'pipeline.connect(\n    "a",\n    "b",\n    target_port="base",\n)'
        assert _find_connect_calls(src) == [("a", "b", None, "base")]

    def test_multiple_connects(self) -> None:
        src = 'pipeline.connect("a", "b")\npipeline.connect("b", "c", source_port="p")\n'
        assert _find_connect_calls(src) == [
            ("a", "b", None, None),
            ("b", "c", "p", None),
        ]

    def test_chained_connect_calls(self) -> None:
        src = 'pipeline.connect("a", "b").connect("b", "c")'
        assert _find_connect_calls(src) == [
            ("a", "b", None, None),
            ("b", "c", None, None),
        ]

    def test_backslash_continuation_chain(self) -> None:
        """A line-continuation chain is one logical statement — both edges
        must be recovered, exactly as the healthy parser would."""
        src = 'pipeline.connect("a", "b") \\\n    .connect("b", "c")'
        assert _find_connect_calls(src) == [
            ("a", "b", None, None),
            ("b", "c", None, None),
        ]

    def test_invalid_leading_dot_continuation_fails_loud(self) -> None:
        """``.connect(...)`` on the next line without a continuation is a
        syntax error at the connect chain itself — fail loud, never split
        the chain and silently keep only the first edge."""
        src = 'pipeline.connect("a", "b")\n.connect("b", "c")'
        with pytest.raises(ParseError, match="connect"):
            _find_connect_calls(src)

    def test_keyword_source_target_form(self) -> None:
        src = 'pipeline.connect(source="a", target="b")'
        assert _find_connect_calls(src) == [("a", "b", None, None)]

    def test_extra_positional_ignored_in_parity_with_healthy_parser(self) -> None:
        """Ports are keyword-only at runtime, so a third positional is a
        TypeError on import.  The healthy parser records the (a, b) edge and
        ignores the extra arg; the fallback must behave identically rather
        than be stricter on the recovery path."""
        src = 'pipeline.connect("a", "b", "c")'
        assert _find_connect_calls(src) == [("a", "b", None, None)]

    def test_whitespace_variants(self) -> None:
        src = 'pipeline . connect ( "a" , "b" )'
        assert _find_connect_calls(src) == [("a", "b", None, None)]

    def test_other_receiver_not_matched(self) -> None:
        """``mypipeline.connect`` must not be mistaken for ``pipeline.connect``."""
        assert _find_connect_calls('mypipeline.connect("a", "b")') == []

    def test_attribute_receiver_not_matched(self) -> None:
        assert _find_connect_calls('module.pipeline.connect("a", "b")') == []

    def test_commented_out_connect_skipped(self) -> None:
        """Commenting out a connect is the standard way to disable an edge."""
        assert _find_connect_calls('# pipeline.connect("a", "b")') == []

    def test_inline_comment_connect_skipped(self) -> None:
        assert _find_connect_calls('x = 1  # see pipeline.connect("a", "b")') == []

    def test_commented_broken_connect_does_not_raise(self) -> None:
        assert _find_connect_calls('# pipeline.connect("a",') == []

    def test_single_quoted_string_connect_text_skipped(self) -> None:
        """A connect-looking substring inside a string is not code."""
        src = 'note = \'pipeline.connect("a", "b")\''
        assert _find_connect_calls(src) == []

    def test_double_quoted_escaped_connect_text_skipped(self) -> None:
        """Escaped quotes inside the string must not expose a bogus anchor."""
        src = 'note = "pipeline.connect(\\"a\\", \\"b\\")"'
        assert _find_connect_calls(src) == []

    def test_triple_quoted_connect_text_skipped(self) -> None:
        src = 'note = """\npipeline.connect("a", "b")\n"""'
        assert _find_connect_calls(src) == []

    def test_prefixed_string_connect_text_skipped(self) -> None:
        src = 'note = r"pipeline.connect(\\"a\\", \\"b\\")"'
        assert _find_connect_calls(src) == []

    def test_unclosed_single_line_string_skips_only_to_newline(self) -> None:
        src = 'note = "pipeline.connect(a, b\npipeline.connect("a", "b")'
        assert _find_connect_calls(src) == [("a", "b", None, None)]

    def test_unclosed_triple_quoted_string_skips_to_eof(self) -> None:
        src = 'note = """\npipeline.connect("a", "b")'
        assert _find_connect_calls(src) == []

    def test_hash_inside_string_before_connect_still_found(self) -> None:
        """The comment check is quote-aware: a ``#`` inside a string literal
        on the same line must not hide a real connect call."""
        src = 's = "h#h"; pipeline.connect("a", "b")'
        assert _find_connect_calls(src) == [("a", "b", None, None)]

    def test_escaped_quote_in_string_before_connect_still_found(self) -> None:
        """Backslash escapes in the line prefix must not desync the
        quote tracking (``\\"`` does not close the string)."""
        src = 's = "a\\\\#b"; pipeline.connect("a", "b")'
        assert _find_connect_calls(src) == [("a", "b", None, None)]

    def test_nested_parens_in_call_span(self) -> None:
        """Parenthesised arguments must not truncate the balanced scan."""
        src = 'pipeline.connect(("a"), "b")'
        assert _find_connect_calls(src) == [("a", "b", None, None)]

    def test_unterminated_connect_raises_loud(self) -> None:
        """An edge we can see but cannot recover must fail LOUD — silently
        returning a graph without it would drop the edge on the next save."""
        with pytest.raises(ParseError, match="connect"):
            _find_connect_calls('pipeline.connect("a",')

    def test_syntax_error_inside_connect_raises_loud(self) -> None:
        with pytest.raises(ParseError, match="connect"):
            _find_connect_calls('pipeline.connect("a",, "b")')

    def test_error_names_the_line(self) -> None:
        src = 'x = 1\ny = 2\npipeline.connect("a",, "b")'
        with pytest.raises(ParseError, match="line=3"):
            _find_connect_calls(src)

    def test_non_literal_args_raise_loud(self) -> None:
        """Same policy as the healthy parser (remediation 5.5)."""
        with pytest.raises(ParseError, match="connect"):
            _find_connect_calls("pipeline.connect(a, b)")

    def test_non_string_literal_args_skipped(self) -> None:
        """Parity with the healthy parser: literal-but-not-string is skipped."""
        assert _find_connect_calls("pipeline.connect(1, 2)") == []

    def test_empty_source(self) -> None:
        assert _find_connect_calls("") == []


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

    # --- Remediation 5.7: multi-arg connect() forms survive the fallback --

    def test_multi_arg_connects_not_dropped(self) -> None:
        """RED for 5.7: the old regex silently dropped every connect() with
        port kwargs, losing edges exactly when the user needs recovery."""
        source = """\
import haute
pipeline = haute.Pipeline("p")

@pipeline.polars()
def a(df):
    return df

@pipeline.polars()
def b(df):
    return df

@pipeline.polars()
def c(df):
    return df

pipeline.connect("a", "b", target_port="base")
pipeline.connect("b", "c", source_port="p", target_port="q")

x = {unclosed
"""
        err = SyntaxError("bad")
        err.lineno = 19
        graph = fallback_parse(source, "f.py", err)

        edges = {(e.source, e.target): e for e in graph.edges}
        assert ("a", "b") in edges
        assert edges[("a", "b")].targetHandle == "base"
        assert ("b", "c") in edges
        assert edges[("b", "c")].sourceHandle == "p"
        assert edges[("b", "c")].targetHandle == "q"

    def test_chained_connects_recovered(self) -> None:
        source = """\
import haute
pipeline = haute.Pipeline("p")

@pipeline.polars()
def a(df):
    return df

@pipeline.polars()
def b(df):
    return df

@pipeline.polars()
def c(df):
    return df

pipeline.connect("a", "b").connect("b", "c")

x = {unclosed
"""
        err = SyntaxError("bad")
        err.lineno = 18
        graph = fallback_parse(source, "f.py", err)
        edge_pairs = {(e.source, e.target) for e in graph.edges}
        assert ("a", "b") in edge_pairs
        assert ("b", "c") in edge_pairs

    def test_unparseable_connect_fails_loud(self) -> None:
        """If the syntax error is inside a connect() call itself, the
        fallback cannot recover the edge — it must refuse to return a
        plausible-but-incomplete graph."""
        source = """\
import haute
pipeline = haute.Pipeline("p")

@pipeline.polars()
def a(df):
    return df

@pipeline.polars()
def b(df):
    return df

pipeline.connect("a",
"""
        err = SyntaxError("unexpected EOF")
        err.lineno = 12
        with pytest.raises(ParseError, match="connect"):
            fallback_parse(source, "f.py", err)

    def test_commented_broken_connect_does_not_block_recovery(self) -> None:
        """A connect inside a comment — even an unparseable one — must be
        ignored entirely.  If comment handling failed, the truncated call
        text would raise ParseError and kill the whole recovery parse."""
        source = """\
import haute
pipeline = haute.Pipeline("p")

@pipeline.polars()
def a(df):
    return df

@pipeline.polars()
def b(df):
    return df

# pipeline.connect("a",
pipeline.connect("a", "b", target_port="base")

x = {unclosed
"""
        err = SyntaxError("bad")
        err.lineno = 15
        graph = fallback_parse(source, "f.py", err)
        edges = {(e.source, e.target): e for e in graph.edges}
        assert ("a", "b") in edges
        assert edges[("a", "b")].targetHandle == "base"

    def test_connect_text_inside_string_does_not_create_phantom_edge(self) -> None:
        """Fallback recovery must match the healthy parser: strings are not edges."""
        source = """\
import haute
pipeline = haute.Pipeline("p")

@pipeline.polars()
def a(df):
    return df

@pipeline.polars()
def b(df):
    return df

@pipeline.polars()
def c(df):
    return df

note = '''
pipeline.connect("a", "b")
'''
pipeline.connect("b", "c")

x = {unclosed
"""
        err = SyntaxError("bad")
        err.lineno = 22
        graph = fallback_parse(source, "f.py", err)
        edge_pairs = {(e.source, e.target) for e in graph.edges}
        assert ("a", "b") not in edge_pairs
        assert ("b", "c") in edge_pairs
