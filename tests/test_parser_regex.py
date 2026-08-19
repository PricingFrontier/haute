"""Tests for neutral syntax-recovery fragments in ``haute._parser_regex``."""

from __future__ import annotations

import pytest

from haute._parser_regex import (
    _find_connect_calls,
    _find_function_blocks,
    _parenthesized_wrapper_depth_before,
    _parenthesized_wrapper_tail_closes,
    _parse_decorator_kwargs_regex,
    recover_pipeline_fragments,
)
from haute.errors import ParseError


def test_terminal_comment_stops_parenthesis_scanners() -> None:
    source = "# terminal comment"
    assert _parenthesized_wrapper_depth_before(source, len(source)) == 0
    assert not _parenthesized_wrapper_tail_closes(source, 0, 1)


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
        source = "@pipeline.data_input(path='input.csv')\ndef api_input():\n    pass\n"
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
        source = '@pipeline.data_input(path="data.csv")\ndef load(df):\n    return df\n'
        blocks = _find_function_blocks(source)
        assert len(blocks) == 1
        assert 'path="data.csv"' in blocks[0]["decorator_text"]

    def test_unrecognised_method_is_conserved_for_recovery(self) -> None:
        """Every authored pipeline decorator is retained before support checks."""
        source = '@pipeline.connect("a", "b")\ndef not_a_node(df):\n    return df\n'
        blocks = _find_function_blocks(source)
        assert len(blocks) == 1
        assert blocks[0]["decorator_method"] == "connect"
        assert blocks[0]["explicit_node_type"] is None

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

    def test_nested_call_decorator_kwargs_keep_full_decorator_text(self) -> None:
        """Fallback decorator recovery must not stop at the first inner ``)``."""
        source = '@pipeline.polars(selected_columns=Path("x"))\ndef calc(df):\n    return df\n'
        blocks = _find_function_blocks(source)
        assert len(blocks) == 1
        assert blocks[0]["decorator_text"] == '@pipeline.polars(selected_columns=Path("x"))'

    def test_decorator_with_arguments_and_trailing_comment(self) -> None:
        source = (
            '@pipeline.polars(selected_columns=["x"])  # keep me\ndef calc(df):\n    return df\n'
        )
        blocks = _find_function_blocks(source)
        assert blocks[0]["decorator_text"] == '@pipeline.polars(selected_columns=["x"])'

    def test_bare_decorator_with_trailing_comment(self) -> None:
        source = "@pipeline.polars  # comment\ndef calc(df):\n    return df\n"
        blocks = _find_function_blocks(source)
        assert blocks[0]["decorator_text"] == "@pipeline.polars"

    def test_decorator_allows_blank_and_comment_before_def(self) -> None:
        source = "@pipeline.polars()\n\n# comment\ndef calc(df):\n    return df\n"
        blocks = _find_function_blocks(source)
        assert len(blocks) == 1
        assert blocks[0]["func_name"] == "calc"

    def test_decorator_inside_triple_quoted_string_is_skipped(self) -> None:
        source = 'note = """\n@pipeline.polars()\ndef fake(df):\n    return df\n"""\n'
        assert _find_function_blocks(source) == []

    def test_decorator_trailing_text_after_args_fails_loud(self) -> None:
        source = "@pipeline.polars() + other\ndef calc(df):\n    return df\n"
        with pytest.raises(ParseError, match="trailing text"):
            _find_function_blocks(source)

    def test_unclosed_decorator_args_fails_loud_with_decorator_context(self) -> None:
        source = '@pipeline.polars(selected_columns=["x"]\ndef calc(df):\n    return df\n'
        with pytest.raises(ParseError, match="pipeline decorator"):
            _find_function_blocks(source)

    def test_bare_decorator_trailing_text_fails_loud(self) -> None:
        source = "@pipeline.polars.extra\ndef calc(df):\n    return df\n"
        with pytest.raises(ParseError, match="malformed"):
            _find_function_blocks(source)

    def test_decorator_without_following_def_fails_loud(self) -> None:
        with pytest.raises(ParseError, match="function definition"):
            _find_function_blocks("@pipeline.polars()")

    def test_decorator_followed_by_non_def_fails_loud(self) -> None:
        source = "@pipeline.polars()\nx = 1\n"
        with pytest.raises(ParseError, match="function definition"):
            _find_function_blocks(source)


# ---------------------------------------------------------------------------
# _parse_decorator_kwargs_regex
# ---------------------------------------------------------------------------


class TestParseDecoratorKwargsRegex:
    def test_string_kwargs(self) -> None:
        text = '@pipeline.data_input(path="data.csv", name="load")'
        result = _parse_decorator_kwargs_regex(text)
        assert result["path"] == "data.csv"
        assert result["name"] == "load"

    def test_boolean_kwargs(self) -> None:
        text = "@pipeline.polars(api_input=True, output=False)"
        result = _parse_decorator_kwargs_regex(text)
        assert result["api_input"] is True
        assert result["output"] is False

    def test_mixed_kwargs(self) -> None:
        text = '@pipeline.data_input(path="x.csv", output=True)'
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
        text = "@pipeline.data_input(path='data.csv')"
        result = _parse_decorator_kwargs_regex(text)
        assert result["path"] == "data.csv"

    def test_bare_name_kwarg_rejected_loudly(self) -> None:
        """Tier-2 fallback policy: unresolved names are not serialized."""
        with pytest.raises(ParseError, match="selected_columns"):
            _parse_decorator_kwargs_regex("@pipeline.polars(selected_columns=COLS)")

    def test_non_literal_expression_kwarg_rejected_loudly(self) -> None:
        """Computed decorator values would be re-emitted as strings on save."""
        with pytest.raises(ParseError, match="path"):
            _parse_decorator_kwargs_regex('@pipeline.data_input(path=Path("data.csv"))')

    def test_contract_constructor_kwarg_is_lowered(self) -> None:
        result = _parse_decorator_kwargs_regex(
            '@pipeline.polars(contract=Contract(inputs=["a"], outputs=["b"]))'
        )
        assert result["contract"] == {"inputs": ["a"], "outputs": ["b"]}

    def test_star_kwargs_rejected_loudly(self) -> None:
        """Regex fallback must match the healthy parser: ``**cfg`` is not recoverable."""
        with pytest.raises(ParseError, match=r"\*\*"):
            _parse_decorator_kwargs_regex("@pipeline.polars(**cfg, selected_columns=['x'])")

    def test_malformed_kwargs_raise_parse_error(self) -> None:
        with pytest.raises(ParseError, match="decorator kwargs"):
            _parse_decorator_kwargs_regex("@pipeline.polars(percent=50%)")


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

    def test_indented_connect_inside_if_skipped(self) -> None:
        assert _find_connect_calls('if False:\n    pipeline.connect("a", "b")\n') == []

    def test_connect_inside_function_skipped(self) -> None:
        assert _find_connect_calls('def disabled():\n    pipeline.connect("a", "b")\n') == []

    def test_assigned_connect_skipped(self) -> None:
        assert _find_connect_calls('edge = pipeline.connect("a", "b")\n') == []

    def test_one_line_if_connect_skipped(self) -> None:
        assert _find_connect_calls('if False: pipeline.connect("a", "b")\n') == []

    def test_one_line_if_semicolon_connect_skipped(self) -> None:
        assert _find_connect_calls('if False: pass; pipeline.connect("a", "b")\n') == []

    def test_one_line_parenthesized_if_semicolon_connect_skipped(self) -> None:
        assert _find_connect_calls('if(True): pass; pipeline.connect("a", "b")\n') == []

    def test_one_line_parenthesized_while_semicolon_connect_skipped(self) -> None:
        assert _find_connect_calls('while(True): pass; pipeline.connect("a", "b")\n') == []

    def test_one_line_parenthesized_with_semicolon_connect_skipped(self) -> None:
        assert _find_connect_calls('with(open("x")): pass; pipeline.connect("a", "b")\n') == []

    def test_async_def_semicolon_connect_skipped(self) -> None:
        assert _find_connect_calls('async def disabled(): pass; pipeline.connect("a", "b")\n') == []

    def test_async_for_semicolon_connect_skipped(self) -> None:
        assert (
            _find_connect_calls('async for item in source: pass; pipeline.connect("a", "b")\n')
            == []
        )

    def test_multiline_list_assignment_connect_skipped(self) -> None:
        assert _find_connect_calls('disabled = [\npipeline.connect("a", "b")\n]\n') == []

    def test_multiline_parenthesized_assignment_connect_skipped(self) -> None:
        assert _find_connect_calls('disabled = (\npipeline.connect("a", "b")\n)\n') == []

    def test_top_level_parenthesized_connect_recovered(self) -> None:
        assert _find_connect_calls('(\npipeline.connect("a", "b")\n)\n') == [("a", "b", None, None)]

    def test_same_line_parenthesized_connect_recovered(self) -> None:
        assert _find_connect_calls('(pipeline.connect("a", "b"))') == [("a", "b", None, None)]

    def test_same_line_nested_parenthesized_connect_recovered(self) -> None:
        assert _find_connect_calls('((pipeline.connect("a", "b")))') == [("a", "b", None, None)]

    def test_top_level_parenthesized_connect_with_comment_recovered(self) -> None:
        assert _find_connect_calls('(\npipeline.connect("a", "b")  # keep\n)\n') == [
            ("a", "b", None, None)
        ]

    def test_top_level_parenthesized_tuple_connect_skipped(self) -> None:
        assert _find_connect_calls('(\npipeline.connect("a", "b"),\n)\n') == []

    def test_same_line_parenthesized_tuple_connect_skipped(self) -> None:
        assert _find_connect_calls('(pipeline.connect("a", "b"),)') == []

    def test_backslash_continuation_connect_skipped(self) -> None:
        assert _find_connect_calls('disabled = \\\npipeline.connect("a", "b")\n') == []

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
# Neutral fragment recovery (integration)
# ---------------------------------------------------------------------------


class TestRecoverPipelineFragments:
    def test_recovers_nodes_connections_metadata_and_preserved_blocks(self) -> None:
        source = """import haute
pipeline = haute.Pipeline("demo", description="Recovered")
# haute:preserve-start
CUSTOM = 1
# haute:preserve-end

@pipeline.polars()
def first(df):
    return df

@pipeline.polars()
def second(first):
    return first

pipeline.connect("first", "second")

def broken(:
    pass
"""

        fragments = recover_pipeline_fragments(source)

        assert fragments.pipeline_name == "demo"
        assert fragments.pipeline_description == "Recovered"
        assert [function.authored_id for function in fragments.functions] == [
            "first",
            "second",
        ]
        assert fragments.connections == (("first", "second", None, None),)
        assert fragments.preserved_blocks == ("CUSTOM = 1",)

    def test_empty_source_has_no_authored_fragments(self) -> None:
        fragments = recover_pipeline_fragments("")

        assert fragments.functions == ()
        assert fragments.connections == ()
        assert fragments.submodel_registrations == ()

    def test_recovers_constructor_alias_and_inline_comment(self) -> None:
        source = """from haute import Pipeline as P
pipeline = P("aliased")  # the editor-created pipeline
"""

        fragments = recover_pipeline_fragments(source)

        assert fragments.pipeline_name == "aliased"

    def test_ignores_pipeline_looking_text_inside_a_triple_quoted_string(self) -> None:
        source = '''"""
pipeline = haute.Pipeline("not-authored")
"""
pipeline = haute.Pipeline("authored")
'''

        fragments = recover_pipeline_fragments(source)

        assert fragments.pipeline_name == "authored"

    def test_visible_unrecognised_pipeline_assignment_fails_loudly(self) -> None:
        with pytest.raises(ParseError, match="cannot be recovered"):
            recover_pipeline_fragments('pipeline = CustomPipeline("x")')

    def test_visible_unrecoverable_decorator_fails_loudly(self) -> None:
        source = """pipeline = haute.Pipeline("demo")
@pipeline.polars(value=(
def node(df):
    return df

def broken(:
    pass
"""

        with pytest.raises(ParseError):
            recover_pipeline_fragments(source)
