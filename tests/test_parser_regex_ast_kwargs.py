"""TDD gate for CODEBASE_REVIEW #54.

``_parse_decorator_kwargs_regex`` currently uses three hand-rolled regex
scans (string, boolean, numeric) to pull keyword arguments out of a
decorator.  That approach loses fidelity for every shape that isn't
"scalar = literal":

* lists silently dissolve ("depends=['a','b']" becomes "depends" dropped)
* dicts / nested structures drop out entirely
* booleans may be shadowed by the string regex capturing "=True" as "True"
* ``None`` is never detected
* ``percent=50%`` mis-parses as ``percent=50`` (silently wrong)

The fix is to delegate to the stdlib: ``ast.parse(f"f({kwargs_str})")``
then walk the resulting ``ast.Call.keywords`` list, evaluating each
``.value`` with ``ast.literal_eval``.  That gives booleans, floats,
ints, None, tuples, lists and dicts back as real Python objects, and
surfaces malformed input as a ``SyntaxError`` instead of silently
producing a wrong answer.

Every test in this file exercises the public entry point
``_parse_decorator_kwargs_regex`` only — no private helpers, no
implementation details — so the implementation is free to swap
internals wholesale.
"""

from __future__ import annotations

import pytest

from haute._parser_regex import _parse_decorator_kwargs_regex


# ---------------------------------------------------------------------------
# Scalars — strings, ints, floats, booleans, None
# ---------------------------------------------------------------------------


class TestScalarKwargs:
    def test_string_kwarg_double_quoted(self) -> None:
        result = _parse_decorator_kwargs_regex('@pipeline.polars(name="load_data")')
        assert result == {"name": "load_data"}

    def test_string_kwarg_single_quoted(self) -> None:
        result = _parse_decorator_kwargs_regex("@pipeline.polars(name='load_data')")
        assert result == {"name": "load_data"}

    def test_float_kwarg(self) -> None:
        result = _parse_decorator_kwargs_regex("@pipeline.polars(rate=0.05)")
        assert result == {"rate": 0.05}
        assert isinstance(result["rate"], float)

    def test_int_kwarg(self) -> None:
        result = _parse_decorator_kwargs_regex("@pipeline.polars(count=100)")
        assert result == {"count": 100}
        assert isinstance(result["count"], int)
        # Important: an int must remain an int, not become 100.0
        assert not isinstance(result["count"], bool)

    def test_bool_true(self) -> None:
        """Real boolean round-trip — not the string 'True'."""
        result = _parse_decorator_kwargs_regex("@pipeline.polars(enabled=True)")
        assert result == {"enabled": True}
        assert result["enabled"] is True

    def test_bool_false(self) -> None:
        result = _parse_decorator_kwargs_regex("@pipeline.polars(enabled=False)")
        assert result == {"enabled": False}
        assert result["enabled"] is False

    def test_none_value(self) -> None:
        """``None`` is a valid literal — it must survive the round-trip."""
        result = _parse_decorator_kwargs_regex("@pipeline.polars(none_val=None)")
        assert "none_val" in result, (
            "None kwargs must be preserved; the regex parser drops them silently"
        )
        assert result["none_val"] is None

    def test_negative_float(self) -> None:
        result = _parse_decorator_kwargs_regex("@pipeline.polars(negative=-1.5)")
        assert result == {"negative": -1.5}
        assert isinstance(result["negative"], float)

    def test_negative_int(self) -> None:
        result = _parse_decorator_kwargs_regex("@pipeline.polars(offset=-7)")
        assert result == {"offset": -7}
        assert isinstance(result["offset"], int)

    def test_scientific_notation(self) -> None:
        result = _parse_decorator_kwargs_regex("@pipeline.polars(scientific=1e-3)")
        assert result == {"scientific": 1e-3}
        assert isinstance(result["scientific"], float)


# ---------------------------------------------------------------------------
# Compound values — lists, dicts, tuples, nesting
# ---------------------------------------------------------------------------


class TestCompoundKwargs:
    def test_list_of_strings(self) -> None:
        result = _parse_decorator_kwargs_regex(
            '@pipeline.polars(depends=["a", "b"])'
        )
        assert result == {"depends": ["a", "b"]}

    def test_list_of_ints(self) -> None:
        result = _parse_decorator_kwargs_regex(
            "@pipeline.polars(steps=[1, 2, 3])"
        )
        assert result == {"steps": [1, 2, 3]}

    def test_empty_list(self) -> None:
        result = _parse_decorator_kwargs_regex("@pipeline.polars(tags=[])")
        assert result == {"tags": []}

    def test_dict_value(self) -> None:
        result = _parse_decorator_kwargs_regex(
            '@pipeline.polars(config={"key": "val"})'
        )
        assert result == {"config": {"key": "val"}}

    def test_empty_dict(self) -> None:
        result = _parse_decorator_kwargs_regex("@pipeline.polars(config={})")
        assert result == {"config": {}}

    def test_nested_list_of_dicts(self) -> None:
        result = _parse_decorator_kwargs_regex(
            '@pipeline.polars(nested=[{"a": 1}, {"b": 2}])'
        )
        assert result == {"nested": [{"a": 1}, {"b": 2}]}

    def test_tuple_value(self) -> None:
        result = _parse_decorator_kwargs_regex("@pipeline.polars(tuple_val=(1, 2))")
        assert result == {"tuple_val": (1, 2)}

    def test_triple_nested_structure(self) -> None:
        """Dict of lists of dicts — proves the parser isn't line-bounded."""
        result = _parse_decorator_kwargs_regex(
            '@pipeline.polars(deep={"outer": [{"inner": [1, 2]}, {"inner": [3, 4]}]})'
        )
        assert result == {"deep": {"outer": [{"inner": [1, 2]}, {"inner": [3, 4]}]}}


# ---------------------------------------------------------------------------
# Multi-kwarg decorators — ordering, mixing, multiline
# ---------------------------------------------------------------------------


class TestMultipleKwargs:
    def test_string_and_bool(self) -> None:
        result = _parse_decorator_kwargs_regex(
            '@pipeline.polars(path="data.csv", output=True)'
        )
        assert result == {"path": "data.csv", "output": True}

    def test_all_scalar_types_together(self) -> None:
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

    def test_multiline_decorator(self) -> None:
        """Multi-line decorators must parse the full body, not just the first line."""
        decorator = (
            "@pipeline.polars(\n"
            '    name="multiline",\n'
            "    depends=[\n"
            '        {"a": 1},\n'
            '        {"b": 2},\n'
            "    ],\n"
            "    rate=0.1,\n"
            ")"
        )
        result = _parse_decorator_kwargs_regex(decorator)
        assert result == {
            "name": "multiline",
            "depends": [{"a": 1}, {"b": 2}],
            "rate": 0.1,
        }


# ---------------------------------------------------------------------------
# Degenerate / no-kwarg forms — must not crash, must return {}
# ---------------------------------------------------------------------------


class TestDegenerate:
    def test_bare_decorator_no_parens(self) -> None:
        assert _parse_decorator_kwargs_regex("@pipeline.polars") == {}

    def test_empty_parens(self) -> None:
        assert _parse_decorator_kwargs_regex("@pipeline.polars()") == {}


# ---------------------------------------------------------------------------
# Invalid syntax — must raise, not silently mis-parse
# ---------------------------------------------------------------------------


class TestInvalidSyntax:
    def test_percent_sign_invalid_literal(self) -> None:
        """``percent=50%`` is not valid Python; must surface as an exception.

        The current regex parser silently extracts ``percent=50``, producing
        a wrong-but-plausible result.  With ``ast.parse`` the malformed
        input is detected and raised — loudly, as ``CLAUDE.md`` requires.
        """
        with pytest.raises((SyntaxError, ValueError)):
            _parse_decorator_kwargs_regex("@pipeline.polars(percent=50%)")

    def test_unbalanced_bracket_raises(self) -> None:
        """Truncated kwargs list must raise, not silently drop everything."""
        with pytest.raises((SyntaxError, ValueError)):
            _parse_decorator_kwargs_regex('@pipeline.polars(depends=["a",)')

    def test_bare_name_reference_not_a_literal(self) -> None:
        """``depends=some_var`` cannot be resolved — it's not a literal.

        The AST-based parser must refuse to return an arbitrary ast-dump
        string for this; either surface it as a SyntaxError/ValueError,
        or omit the entry entirely — what it must not do is silently
        produce ``depends="some_var"`` via regex match.
        """
        with pytest.raises((SyntaxError, ValueError)):
            _parse_decorator_kwargs_regex("@pipeline.polars(depends=some_var)")
