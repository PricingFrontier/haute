"""Decorator-kwarg parsing contract tests.

``_parse_decorator_kwargs_regex`` in ``src/haute/_parser_regex.py`` powers
editor-only syntax recovery. It parses decorator arguments with
``ast.parse(f"f({inner})")`` and ``ast.literal_eval`` over the resulting
``Call.keywords`` list.

This file pins the *contract* of that function so that any extension
(for example, supporting non-literal expressions via
``ast.unparse``) cannot silently regress the existing literal-value policy.

Split:

* ``TestRegressionCurrentBehaviour`` — every kwarg shape the parser
  supports today.  These MUST stay green.
* ``TestPathologicalStillUnsupported`` — computed kwarg shapes the
  fallback must reject loudly (nested calls, f-strings, conditional
  expressions), because configs would otherwise be saved back as strings.
* ``TestValueParsingPolicyPin`` — the current policy is "literal
  Python value via ``ast.literal_eval``".  Asserts explicitly so a
  drift to raw-source-string semantics must update this test.
* ``TestRecoveredFragmentSmoke`` — neutral recovery succeeds
  for valid fragments and fails loudly for visible unrecoverable fragments.

The test file intentionally only touches:
  * ``_parse_decorator_kwargs_regex`` (unit surface)
  * ``recover_pipeline_fragments`` (integration surface)
so the dev is free to refactor all internal helpers.
"""

from __future__ import annotations

import pytest

from haute._parser_regex import _parse_decorator_kwargs_regex, recover_pipeline_fragments
from haute.errors import ParseError

# ---------------------------------------------------------------------------
# Part 1: Regression — kwarg shapes that currently work (must stay green)
# ---------------------------------------------------------------------------


class TestRegressionCurrentBehaviour:
    """Every shape the AST-migrated parser supports today.

    Any future rewrite MUST keep these cases green; they define the
    decorator kwarg parsing contract.
    """

    # --- Empty / degenerate -------------------------------------------------

    def test_bare_decorator_no_parens_returns_empty(self) -> None:
        assert _parse_decorator_kwargs_regex("@pipeline.polars") == {}

    def test_empty_parens_returns_empty(self) -> None:
        assert _parse_decorator_kwargs_regex("@pipeline.polars()") == {}

    def test_whitespace_inside_empty_parens_returns_empty(self) -> None:
        assert _parse_decorator_kwargs_regex("@pipeline.polars(   )") == {}

    # --- Single scalar kwarg ------------------------------------------------

    def test_single_string_kwarg_double_quoted(self) -> None:
        result = _parse_decorator_kwargs_regex('@pipeline.polars(cache="parquet")')
        assert result == {"cache": "parquet"}

    def test_single_string_kwarg_single_quoted(self) -> None:
        result = _parse_decorator_kwargs_regex("@pipeline.polars(cache='parquet')")
        assert result == {"cache": "parquet"}

    def test_integer_kwarg(self) -> None:
        result = _parse_decorator_kwargs_regex("@pipeline.polars(cache_size=100)")
        assert result == {"cache_size": 100}
        assert type(result["cache_size"]) is int

    def test_float_kwarg(self) -> None:
        result = _parse_decorator_kwargs_regex("@pipeline.polars(rate=0.05)")
        assert result == {"rate": 0.05}
        assert type(result["rate"]) is float

    def test_bool_true_kwarg(self) -> None:
        result = _parse_decorator_kwargs_regex("@pipeline.polars(enabled=True)")
        assert result["enabled"] is True

    def test_bool_false_kwarg(self) -> None:
        result = _parse_decorator_kwargs_regex("@pipeline.polars(enabled=False)")
        assert result["enabled"] is False

    def test_none_kwarg(self) -> None:
        result = _parse_decorator_kwargs_regex("@pipeline.polars(fallback=None)")
        assert "fallback" in result
        assert result["fallback"] is None

    def test_negative_number(self) -> None:
        result = _parse_decorator_kwargs_regex("@pipeline.polars(offset=-42)")
        assert result == {"offset": -42}

    def test_scientific_notation(self) -> None:
        result = _parse_decorator_kwargs_regex("@pipeline.polars(tol=1e-9)")
        assert result["tol"] == 1e-9

    # --- Multiple kwargs ----------------------------------------------------

    def test_two_string_kwargs(self) -> None:
        result = _parse_decorator_kwargs_regex('@pipeline.polars(cache="parquet", key="id")')
        assert result == {"cache": "parquet", "key": "id"}

    def test_mixed_kwarg_types(self) -> None:
        result = _parse_decorator_kwargs_regex(
            '@pipeline.polars(name="n", count=10, rate=0.5, enabled=True, empty=None)'
        )
        assert result == {
            "name": "n",
            "count": 10,
            "rate": 0.5,
            "enabled": True,
            "empty": None,
        }

    # --- Shapes the pre-AST regex silently broke (now green) ---------------

    def test_list_value_of_strings(self) -> None:
        """Before Wave 4C this silently dropped the kwarg entirely."""
        result = _parse_decorator_kwargs_regex('@pipeline.polars(columns=["a", "b", "c"])')
        assert result == {"columns": ["a", "b", "c"]}

    def test_list_value_of_ints(self) -> None:
        result = _parse_decorator_kwargs_regex("@pipeline.polars(steps=[1, 2, 3])")
        assert result == {"steps": [1, 2, 3]}

    def test_empty_list(self) -> None:
        result = _parse_decorator_kwargs_regex("@pipeline.polars(cols=[])")
        assert result == {"cols": []}

    def test_dict_value(self) -> None:
        """Dict literals were dropped by the pre-AST regex."""
        result = _parse_decorator_kwargs_regex('@pipeline.polars(config={"key": "val"})')
        assert result == {"config": {"key": "val"}}

    def test_empty_dict(self) -> None:
        result = _parse_decorator_kwargs_regex("@pipeline.polars(config={})")
        assert result == {"config": {}}

    def test_tuple_value(self) -> None:
        result = _parse_decorator_kwargs_regex("@pipeline.polars(pair=(1, 2))")
        assert result == {"pair": (1, 2)}

    def test_string_containing_comma(self) -> None:
        """Pre-AST regex split on commas inside strings, corrupting values."""
        result = _parse_decorator_kwargs_regex('@pipeline.polars(msg="hello, world")')
        assert result == {"msg": "hello, world"}

    def test_string_containing_equals(self) -> None:
        """Equals inside a string must not be misread as a kwarg boundary."""
        result = _parse_decorator_kwargs_regex('@pipeline.polars(expr="x=y")')
        assert result == {"expr": "x=y"}

    def test_string_containing_parens(self) -> None:
        result = _parse_decorator_kwargs_regex('@pipeline.polars(label="func(x, y)")')
        assert result == {"label": "func(x, y)"}

    def test_string_containing_brackets(self) -> None:
        result = _parse_decorator_kwargs_regex('@pipeline.polars(pat="[a-z]+")')
        assert result == {"pat": "[a-z]+"}

    # --- Multi-line decorator bodies ---------------------------------------

    def test_multiline_kwargs_basic(self) -> None:
        """Pre-AST regex was strictly single-line; the AST parser handles both."""
        decorator = '@pipeline.polars(\n    cache="parquet",\n    key="id",\n)'
        result = _parse_decorator_kwargs_regex(decorator)
        assert result == {"cache": "parquet", "key": "id"}

    def test_multiline_kwargs_with_nested_list(self) -> None:
        decorator = (
            "@pipeline.polars(\n"
            '    name="multi",\n'
            "    depends=[\n"
            '        {"a": 1},\n'
            '        {"b": 2},\n'
            "    ],\n"
            ")"
        )
        result = _parse_decorator_kwargs_regex(decorator)
        assert result == {
            "name": "multi",
            "depends": [{"a": 1}, {"b": 2}],
        }

    def test_multiline_with_trailing_comma(self) -> None:
        decorator = '@pipeline.polars(\n    cache="parquet",\n)'
        result = _parse_decorator_kwargs_regex(decorator)
        assert result == {"cache": "parquet"}


# ---------------------------------------------------------------------------
# Part 2: Non-literal values the fallback intentionally rejects.
#
# Current behaviour: ``ast.literal_eval`` raises on anything that is not a
# pure literal (calls, f-strings, conditional expressions, etc.).  After the
# dev extends the impl to fall back to ``ast.unparse`` (or a similarly
# loss-less mechanism) for non-literal values, these tests must be updated
# explicitly.  The W5 parser contract rejects computed config values because
# returning source text would serialize them as plain strings on save.
# ---------------------------------------------------------------------------


class TestPathologicalStillUnsupported:
    """Non-literal expressions remain unsupported in fallback config data."""

    def test_nested_dict_call_rejected(self) -> None:
        with pytest.raises(ParseError, match="transform"):
            _parse_decorator_kwargs_regex("@pipeline.polars(transform=dict(a=1, b=2))")

    def test_nested_function_call_rejected(self) -> None:
        with pytest.raises(ParseError, match="seed"):
            _parse_decorator_kwargs_regex("@pipeline.polars(seed=compute_seed(42))")

    def test_dynamic_fstring_rejected(self) -> None:
        with pytest.raises(ParseError, match="label"):
            _parse_decorator_kwargs_regex('@pipeline.polars(label=f"row {i}")')

    def test_static_fstring_rejected(self) -> None:
        with pytest.raises(ParseError, match="label"):
            _parse_decorator_kwargs_regex('@pipeline.polars(label=f"row")')

    def test_conditional_expression_rejected(self) -> None:
        with pytest.raises(ParseError, match="threshold"):
            _parse_decorator_kwargs_regex("@pipeline.polars(threshold=0.5 if x else 1.0)")

    def test_attribute_chain_rejected(self) -> None:
        with pytest.raises(ParseError, match="mode"):
            _parse_decorator_kwargs_regex("@pipeline.polars(mode=pl.FlowMode.LAZY)")


# ---------------------------------------------------------------------------
# Part 3: Value-parsing policy pin
#
# The current impl returns *literal Python values* (ast.literal_eval
# semantics).  These tests assert explicitly so any drift (for example,
# flipping to "always raw source strings") forces an update here and a
# human review.
# ---------------------------------------------------------------------------


class TestValueParsingPolicyPin:
    """Pin: current policy is **literal Python value** via ``ast.literal_eval``.

    A future rewrite that switches to raw-source-string return values would
    be a visible behaviour change that breaks every downstream config
    builder — these tests force that change to be deliberate.
    """

    def test_int_value_is_python_int_not_string(self) -> None:
        result = _parse_decorator_kwargs_regex("@pipeline.polars(count=100)")
        assert type(result["count"]) is int, (
            "Policy: literal values are returned as their Python types, not as raw source strings."
        )

    def test_float_value_is_python_float_not_string(self) -> None:
        result = _parse_decorator_kwargs_regex("@pipeline.polars(rate=0.05)")
        assert type(result["rate"]) is float

    def test_bool_value_is_python_bool_not_string(self) -> None:
        result = _parse_decorator_kwargs_regex("@pipeline.polars(enabled=True)")
        assert type(result["enabled"]) is bool

    def test_none_value_is_python_none_not_string(self) -> None:
        result = _parse_decorator_kwargs_regex("@pipeline.polars(fallback=None)")
        assert result["fallback"] is None

    def test_list_value_is_python_list_not_string(self) -> None:
        result = _parse_decorator_kwargs_regex('@pipeline.polars(cols=["a", "b"])')
        assert isinstance(result["cols"], list)
        assert result["cols"] == ["a", "b"]

    def test_dict_value_is_python_dict_not_string(self) -> None:
        result = _parse_decorator_kwargs_regex('@pipeline.polars(cfg={"k": 1})')
        assert isinstance(result["cfg"], dict)
        assert result["cfg"] == {"k": 1}


# ---------------------------------------------------------------------------
# Part 4: Fail-loud contract — malformed input must raise, not mis-parse
# ---------------------------------------------------------------------------


class TestFailLoudOnMalformedInput:
    """Malformed kwargs must raise — silent mis-parse is CLAUDE.md-forbidden.

    The pre-AST regex silently produced wrong-but-plausible values for
    inputs like ``percent=50%`` (regex matched ``percent=50``); the AST
    parser surfaces them as ``ParseError``.  These tests pin that behaviour
    so no future 'softening' can silently reintroduce lossy parsing.
    """

    def test_percent_sign_raises(self) -> None:
        with pytest.raises(ParseError, match="decorator kwargs could not be parsed"):
            _parse_decorator_kwargs_regex("@pipeline.polars(percent=50%)")

    def test_unbalanced_bracket_raises(self) -> None:
        with pytest.raises(ParseError, match="decorator kwargs could not be parsed"):
            _parse_decorator_kwargs_regex('@pipeline.polars(cols=["a",)')

    def test_dangling_equals_raises(self) -> None:
        with pytest.raises(ParseError, match="decorator kwargs could not be parsed"):
            _parse_decorator_kwargs_regex("@pipeline.polars(key=)")

    def test_bare_name_reference_raises(self) -> None:
        """`depends=some_var` is neither a literal nor a resolvable expression."""
        with pytest.raises(ParseError, match="depends"):
            _parse_decorator_kwargs_regex("@pipeline.polars(depends=some_var)")


# ---------------------------------------------------------------------------
# Part 5: Neutral fragment integration
# ---------------------------------------------------------------------------


class TestRecoveredFragmentSmoke:
    def test_pipeline_name_and_multiline_decorator_survive(self) -> None:
        source = """import haute
pipeline = haute.Pipeline("smoke")

@pipeline.polars(
    selected_columns=["premium", "claims"],
)
def transform(df):
    return df

def broken(:
    pass
"""

        fragments = recover_pipeline_fragments(source)

        assert fragments.pipeline_name == "smoke"
        assert len(fragments.functions) == 1
        kwargs = _parse_decorator_kwargs_regex(fragments.functions[0].decorator_text)
        assert kwargs == {"selected_columns": ["premium", "claims"]}

    def test_visible_unrecoverable_decorator_is_not_silently_dropped(self) -> None:
        source = """pipeline = haute.Pipeline("smoke")
@pipeline.polars(selected_columns=["premium"
def transform(df):
    return df
"""

        with pytest.raises(ParseError):
            recover_pipeline_fragments(source)
