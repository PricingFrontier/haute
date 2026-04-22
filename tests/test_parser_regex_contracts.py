"""Decorator-kwarg parsing contract tests.

``_parse_decorator_kwargs_regex`` in ``src/haute/_parser_regex.py`` powers the
syntax-error fallback parser.  The AST-based migration (Wave 4C / commit
``eb967bf``) replaced three hand-rolled regex scans with
``ast.parse(f"f({inner})")`` + ``ast.literal_eval`` over the resulting
``Call.keywords`` list.

This file pins the *contract* of that function so that any further migration
(for example, extending support for non-literal expressions via
``ast.unparse``) cannot silently regress the existing literal-value policy.

Split:

* ``TestRegressionCurrentBehaviour`` — every kwarg shape the parser
  supports today.  These MUST stay green.
* ``TestPathologicalStillUnsupported`` — shapes the AST migration
  *should* handle but currently does not (nested calls, f-strings,
  conditional expressions).  Marked ``xfail(strict=True)`` so they
  flip to ``XPASS`` (a failure) the moment the dev extends the
  implementation — whoever extends it must remove the marker.
* ``TestValueParsingPolicyPin`` — the current policy is "literal
  Python value via ``ast.literal_eval``".  Asserts explicitly so a
  drift to raw-source-string semantics must update this test.
* ``TestFallbackParseSmoke`` — integration: pathological decorators
  round-trip through ``fallback_parse`` without dropping the node.

The test file intentionally only touches:
  * ``_parse_decorator_kwargs_regex`` (unit surface)
  * ``fallback_parse`` (integration surface)
so the dev is free to refactor all internal helpers.
"""

from __future__ import annotations

import pytest

from haute._parser_regex import _parse_decorator_kwargs_regex, fallback_parse
from haute.graph_utils import NodeType

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
# Part 2: Pathological cases that the AST migration does NOT yet handle.
#
# Current behaviour: ``ast.literal_eval`` raises on anything that is not a
# pure literal (calls, f-strings, conditional expressions, etc.).  After the
# dev extends the impl to fall back to ``ast.unparse`` (or a similarly
# loss-less mechanism) for non-literal values, these tests flip from
# ``xfail`` to ``XPASS``, flagging the fix so the marker can be removed.
#
# Assertions use *structural* checks — "the kwarg survived and is either
# a string containing the original source fragment, or the evaluated
# literal" — so the dev can pick either value-parsing policy without
# making the test wrong.
# ---------------------------------------------------------------------------


def _kwarg_value_matches(value: object, expected_fragment: str) -> bool:
    """Accept either a literal round-trip OR a raw source string containing the fragment.

    Lets the dev choose either value-parsing policy (literal_eval where
    possible + ast.unparse fallback, OR raw-source-only) without forcing
    the test to pick one.
    """
    if isinstance(value, str):
        return expected_fragment in value
    # Any non-string value is acceptable if it stringifies to contain the
    # expected fragment (covers dicts, lists, evaluated calls, etc.)
    return expected_fragment in repr(value)


class TestPathologicalStillUnsupported:
    """Shapes the AST fallback round-trips via ``ast.unparse``.

    Originally xfail-strict against the ``literal_eval``-only parser; the
    AST-unparse fallback (#136) resolves every case.  Preserved under the
    legacy class name to keep cross-repo references intact, and kept as
    part of the pinned contract so any future regression in the unparse
    fallback surfaces here.
    """

    def test_nested_dict_call_preserved(self) -> None:
        result = _parse_decorator_kwargs_regex("@pipeline.polars(transform=dict(a=1, b=2))")
        assert "transform" in result
        assert _kwarg_value_matches(result["transform"], "dict(")

    def test_nested_function_call_preserved(self) -> None:
        result = _parse_decorator_kwargs_regex("@pipeline.polars(seed=compute_seed(42))")
        assert "seed" in result
        assert _kwarg_value_matches(result["seed"], "compute_seed")

    def test_dynamic_fstring_preserved(self) -> None:
        result = _parse_decorator_kwargs_regex('@pipeline.polars(label=f"row {i}")')
        assert "label" in result
        assert _kwarg_value_matches(result["label"], "row")

    def test_static_fstring_preserved(self) -> None:
        result = _parse_decorator_kwargs_regex('@pipeline.polars(label=f"row")')
        assert "label" in result
        assert _kwarg_value_matches(result["label"], "row")

    def test_conditional_expression_preserved(self) -> None:
        result = _parse_decorator_kwargs_regex("@pipeline.polars(threshold=0.5 if x else 1.0)")
        assert "threshold" in result
        assert _kwarg_value_matches(result["threshold"], "0.5")

    def test_attribute_chain_preserved(self) -> None:
        result = _parse_decorator_kwargs_regex("@pipeline.polars(mode=pl.FlowMode.LAZY)")
        assert "mode" in result
        assert _kwarg_value_matches(result["mode"], "pl.FlowMode.LAZY")


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
    parser surfaces them as exceptions.  These tests pin that behaviour
    so no future 'softening' can silently reintroduce lossy parsing.
    """

    def test_percent_sign_raises(self) -> None:
        with pytest.raises((SyntaxError, ValueError)):
            _parse_decorator_kwargs_regex("@pipeline.polars(percent=50%)")

    def test_unbalanced_bracket_raises(self) -> None:
        with pytest.raises((SyntaxError, ValueError)):
            _parse_decorator_kwargs_regex('@pipeline.polars(cols=["a",)')

    def test_dangling_equals_raises(self) -> None:
        with pytest.raises((SyntaxError, ValueError)):
            _parse_decorator_kwargs_regex("@pipeline.polars(key=)")

    def test_bare_name_reference_raises(self) -> None:
        """`depends=some_var` is neither a literal nor a resolvable expression."""
        with pytest.raises((SyntaxError, ValueError)):
            _parse_decorator_kwargs_regex("@pipeline.polars(depends=some_var)")


# ---------------------------------------------------------------------------
# Part 5: Integration smoke — pathological decorators survive fallback_parse
# ---------------------------------------------------------------------------


class TestFallbackParseSmoke:
    """End-to-end: a pipeline file with a real syntax error + a valid
    decorator body must still produce a PipelineGraph with the correct
    node configs.

    This is the contract the GUI depends on — the fallback parser is
    invoked whenever ``ast.parse`` rejects a file, and the graph it
    returns must be best-effort-correct so the user can still see
    their pipeline alongside the error markers.
    """

    def test_fallback_parse_preserves_config_through_full_pipeline(self, tmp_path) -> None:
        """A valid data_source decorator survives even when another
        decorator in the same file is malformed."""
        cfg_dir = tmp_path / "config" / "data_source"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "load.json").write_text('{"path": "input.csv", "sourceType": "flat_file"}')
        source = (
            'pipeline = haute.Pipeline("smoke_test")\n'
            "\n"
            '@pipeline.data_source(config="config/data_source/load.json")\n'
            "def load():\n"
            '    return pl.scan_csv("input.csv")\n'
            "\n"
            "# Deliberately broken — this is what triggers the fallback\n"
            "@pipeline.polars(\n"
            "    this is not valid python\n"
        )
        graph = fallback_parse(source, str(tmp_path / "smoke.py"), SyntaxError("broken"))

        # The valid data_source node must be recovered with its sidecar config.
        load_nodes = [n for n in graph.nodes if n.id == "load"]
        assert len(load_nodes) == 1, "data_source node was lost in fallback"
        assert load_nodes[0].data.nodeType == NodeType.DATA_SOURCE
        assert load_nodes[0].data.config.get("path") == "input.csv"

    def test_fallback_parse_pipeline_name_survives(self) -> None:
        source = (
            'pipeline = haute.Pipeline("named", description="my pipeline")\n'
            "\n"
            "@pipeline.polars(\n"
            "    broken syntax\n"
        )
        graph = fallback_parse(source, "smoke.py", SyntaxError("broken"))
        assert graph.pipeline_name == "named"
        assert graph.pipeline_description == "my pipeline"

    def test_fallback_parse_multiline_kwarg_decorator(self) -> None:
        """Multi-line kwargs on a recovered decorator must parse correctly.

        The regex decorator finder is single-paren-only, so it won't pick
        up a genuinely multi-line decorator body — but if it does capture
        a decorator whose body spans lines, the kwarg parser must handle
        the multi-line form.  Exercise that by directly feeding a
        multi-line decorator text to the kwarg parser (the call path
        inside fallback_parse).
        """
        decorator_text = '@pipeline.data_source(\n    path="multi.csv",\n    table="t",\n)'
        result = _parse_decorator_kwargs_regex(decorator_text)
        assert result["path"] == "multi.csv"
        assert result["table"] == "t"
