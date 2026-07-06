"""W3 remediation regression tests for ``_expression_parser``.

Each numeric/logic fix is pinned against a tiny Polars oracle: the identical
expression is evaluated on a one-row frame and the trace evaluator must match.
Renderer/parser fixes assert the produced formula text or parse outcome.

The overarching contract (see CLAUDE.md): the trace evaluator must faithfully
reproduce Polars semantics and must NOT launder its own failures into a
fabricated-but-self-consistent value.
"""

from __future__ import annotations

import ast
import math

import polars as pl
import pytest

from haute._expression_parser import (
    _ExprEvaluator,
    _substitute_names_in_ast,
    evaluate_expression,
    parse_expression,
    parse_expression_chain,
)


def _trace_value(expr_text: str, row: dict) -> object:
    # Parenthesise so .alias binds to the whole expression (not a sub-node when
    # the outermost operator is prefix/binary, e.g. ``~x`` or ``a / b``).
    code = f'df = df.with_columns(({expr_text}).alias("out"))'
    return evaluate_expression(code, "out", dict(row)).result_value


def _polars_value(expr_text: str, row: dict) -> object:
    expr = eval(expr_text, {"pl": pl})  # test-local literal, never user input
    return pl.DataFrame([dict(row)]).select(expr.alias("out")).item()


def _eval_node(src: str, row: dict | None = None) -> object:
    node = ast.parse(src, mode="eval").body
    return _ExprEvaluator(row or {}).evaluate(node)


def _assert_matches_polars(expr_text: str, row: dict) -> object:
    trace = _trace_value(expr_text, row)
    polars = _polars_value(expr_text, row)
    if isinstance(polars, float) and math.isnan(polars):
        assert isinstance(trace, float) and math.isnan(trace), (
            f"{expr_text} on {row}: trace={trace!r} polars=nan"
        )
    else:
        assert trace == polars, f"{expr_text} on {row}: trace={trace!r} polars={polars!r}"
    return polars


# ---------------------------------------------------------------------------
# F680 — Kleene three-valued boolean logic for & / |
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expr_text,row,expected",
    [
        ('pl.col("a") & pl.col("b")', {"a": False, "b": None}, False),
        ('pl.col("a") & pl.col("b")', {"a": True, "b": None}, None),
        ('pl.col("a") & pl.col("b")', {"a": None, "b": False}, False),
        ('pl.col("a") | pl.col("b")', {"a": True, "b": None}, True),
        ('pl.col("a") | pl.col("b")', {"a": False, "b": None}, None),
        ('pl.col("a") | pl.col("b")', {"a": None, "b": True}, True),
        ('pl.col("a") & pl.col("b")', {"a": True, "b": True}, True),
        ('pl.col("a") & pl.col("b")', {"a": True, "b": False}, False),
        ('pl.col("a") | pl.col("b")', {"a": False, "b": False}, False),
    ],
)
def test_kleene_matches_polars(expr_text: str, row: dict, expected: object) -> None:
    trace = _trace_value(expr_text, row)
    # Cross-check against Polars (both operands boolean-typed here).
    polars = (
        pl.DataFrame([row], schema={"a": pl.Boolean, "b": pl.Boolean})
        .select(eval(expr_text, {"pl": pl}).alias("out"))
        .item()
    )
    assert trace == expected
    assert trace == polars


def test_kleene_false_and_null_was_masked_to_none() -> None:
    # The blanket ``left is None or right is None -> None`` guard previously
    # made ``False & null`` return None, mis-selecting when/then branches.
    assert _trace_value('pl.col("a") & pl.col("b")', {"a": False, "b": None}) is False
    assert _trace_value('pl.col("a") | pl.col("b")', {"a": True, "b": None}) is True


def test_bitand_on_integers_stays_bitwise() -> None:
    # Non-boolean & / | must remain bitwise (Kleene only applies to bools).
    assert _eval_node('pl.col("a") & pl.col("b")', {"a": 6, "b": 3}) == 2
    assert _eval_node('pl.col("a") | pl.col("b")', {"a": 4, "b": 1}) == 5


# ---------------------------------------------------------------------------
# F686 — division / floor-division / modulo by zero
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expr_text,row",
    [
        ('pl.col("a") / pl.col("b")', {"a": 1.0, "b": 0.0}),
        ('pl.col("a") / pl.col("b")', {"a": -1.0, "b": 0.0}),
        ('pl.col("a") / pl.col("b")', {"a": 0.0, "b": 0.0}),
        ('pl.col("a") / pl.col("b")', {"a": 1, "b": 0}),
        ('pl.col("a") / pl.col("b")', {"a": -1, "b": 0}),
        ('pl.col("a") // pl.col("b")', {"a": 1, "b": 0}),
        ('pl.col("a") // pl.col("b")', {"a": 1.0, "b": 0.0}),
        ('pl.col("a") // pl.col("b")', {"a": -1.0, "b": 0.0}),
        ('pl.col("a") % pl.col("b")', {"a": 1, "b": 0}),
        ('pl.col("a") % pl.col("b")', {"a": 5.0, "b": 0.0}),
    ],
)
def test_divide_by_zero_matches_polars(expr_text: str, row: dict) -> None:
    _assert_matches_polars(expr_text, row)


def test_float_div_by_zero_not_masked_to_observed() -> None:
    # The catch-all previously swallowed ZeroDivisionError and returned the
    # observed row value; 1.0/0.0 must now be +inf like Polars.
    assert _trace_value('pl.col("a") / pl.col("b")', {"a": 1.0, "b": 0.0}) == math.inf
    assert _trace_value('pl.col("a") / pl.col("b")', {"a": -1.0, "b": 0.0}) == -math.inf


def test_int_floordiv_and_mod_by_zero_are_null() -> None:
    assert _trace_value('pl.col("a") // pl.col("b")', {"a": 1, "b": 0}) is None
    assert _trace_value('pl.col("a") % pl.col("b")', {"a": 1, "b": 0}) is None


# ---------------------------------------------------------------------------
# F681 — negative base ** fractional exponent -> NaN (not complex)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expr_text,row",
    [
        ('pl.col("a") ** pl.col("b")', {"a": -8.0, "b": 0.5}),
        ('pl.col("a") ** pl.col("b")', {"a": -8.0, "b": 1.0 / 3.0}),
        ('pl.col("a") ** pl.col("b")', {"a": 2.0, "b": 3.0}),
        ('pl.col("a") ** pl.col("b")', {"a": -8.0, "b": 2.0}),
    ],
)
def test_pow_matches_polars(expr_text: str, row: dict) -> None:
    _assert_matches_polars(expr_text, row)


def test_pow_negative_fractional_is_nan_not_complex() -> None:
    result = _trace_value('pl.col("a") ** pl.col("b")', {"a": -8.0, "b": 0.5})
    assert isinstance(result, float) and math.isnan(result)
    assert not isinstance(result, complex)


# ---------------------------------------------------------------------------
# F679 — integer overflow beyond int64 is uncomputable, not a bignum
# ---------------------------------------------------------------------------


def test_integer_overflow_reports_uncomputable() -> None:
    big = 9223372036854775807  # int64 max
    # Polars wraps (dtype-dependent); the dtype-unaware evaluator must not
    # display the unbounded Python bignum, so it reports None instead.
    assert _eval_node('pl.col("a") * pl.col("b")', {"a": big, "b": 2}) is None
    assert _eval_node('pl.col("a") + pl.col("b")', {"a": big, "b": 1}) is None
    assert _eval_node('pl.col("a") ** pl.col("b")', {"a": big, "b": 2}) is None


def test_in_range_integer_arithmetic_still_computes() -> None:
    assert _eval_node('pl.col("a") * pl.col("b")', {"a": 1000, "b": 1000}) == 1_000_000
    assert _eval_node('pl.col("a") + pl.col("b")', {"a": -5, "b": 2}) == -3


# ---------------------------------------------------------------------------
# F682 — clip lower-then-upper on contradictory bounds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "row_val",
    [0, 3, 5, 7, 8, 10, 12, 20],
)
def test_clip_contradictory_bounds_matches_polars(row_val: int) -> None:
    _assert_matches_polars('pl.col("a").clip(10, 5)', {"a": row_val})


def test_clip_normal_bounds_match_polars() -> None:
    for v in (-5, 0, 5, 10, 15):
        _assert_matches_polars('pl.col("a").clip(0, 10)', {"a": v})


def test_clip_lower_check_wins_on_contradiction() -> None:
    # lower=10 > upper=5: a value below 10 clamps UP to 10 (lower wins),
    # sequential min/max would instead give 5.
    assert _trace_value('pl.col("a").clip(10, 5)', {"a": 7}) == 10


# ---------------------------------------------------------------------------
# F683 — log/sqrt out-of-domain -> NaN / -inf, not None
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expr_text,row",
    [
        ('pl.col("a").log()', {"a": 0.0}),
        ('pl.col("a").log()', {"a": -1.0}),
        ('pl.col("a").log()', {"a": math.e}),
        ('pl.col("a").sqrt()', {"a": -4.0}),
        ('pl.col("a").sqrt()', {"a": 9.0}),
    ],
)
def test_log_sqrt_domain_matches_polars(expr_text: str, row: dict) -> None:
    _assert_matches_polars(expr_text, row)


def test_log_zero_is_negative_infinity() -> None:
    assert _trace_value('pl.col("a").log()', {"a": 0.0}) == -math.inf


def test_log_and_sqrt_null_input_stays_null() -> None:
    assert _eval_node('pl.col("a").log()', {"a": None}) is None
    assert _eval_node('pl.col("a").sqrt()', {"a": None}) is None


# ---------------------------------------------------------------------------
# F684 — max_horizontal / min_horizontal NaN ordering
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expr_text,row",
    [
        ('pl.max_horizontal(pl.col("a"), pl.col("b"))', {"a": 1.0, "b": float("nan")}),
        ('pl.max_horizontal(pl.col("a"), pl.col("b"))', {"a": float("nan"), "b": 1.0}),
        ('pl.min_horizontal(pl.col("a"), pl.col("b"))', {"a": 1.0, "b": float("nan")}),
        ('pl.min_horizontal(pl.col("a"), pl.col("b"))', {"a": float("nan"), "b": 1.0}),
        (
            'pl.max_horizontal(pl.col("a"), pl.col("b"), pl.col("c"))',
            {"a": 1.0, "b": 2.0, "c": None},
        ),
    ],
)
def test_horizontal_nan_matches_polars(expr_text: str, row: dict) -> None:
    _assert_matches_polars(expr_text, row)


def test_max_horizontal_nan_propagates_regardless_of_order() -> None:
    a = _trace_value('pl.max_horizontal(pl.col("a"), pl.col("b"))', {"a": 1.0, "b": float("nan")})
    b = _trace_value('pl.max_horizontal(pl.col("a"), pl.col("b"))', {"a": float("nan"), "b": 1.0})
    assert math.isnan(a) and math.isnan(b)


def test_min_horizontal_ignores_nan() -> None:
    assert (
        _trace_value('pl.min_horizontal(pl.col("a"), pl.col("b"))', {"a": float("nan"), "b": 1.0})
        == 1.0
    )


# ---------------------------------------------------------------------------
# F029 / F250 — horizontal funcs: mean(null), concat_str, all/any
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expr_text,row",
    [
        ('pl.mean_horizontal(pl.col("a"), pl.col("b"))', {"a": 10.0, "b": None}),
        ('pl.concat_str([pl.col("a"), pl.col("b")], separator="-")', {"a": "x", "b": "y"}),
        ('pl.concat_str([pl.col("a"), pl.col("b")], separator="-")', {"a": "x", "b": None}),
        ('pl.all_horizontal(pl.col("a"), pl.col("b"))', {"a": True, "b": True}),
        ('pl.all_horizontal(pl.col("a"), pl.col("b"))', {"a": True, "b": False}),
        ('pl.any_horizontal(pl.col("a"), pl.col("b"))', {"a": False, "b": True}),
        ('pl.any_horizontal(pl.col("a"), pl.col("b"))', {"a": False, "b": False}),
    ],
)
def test_horizontal_funcs_match_polars(expr_text: str, row: dict) -> None:
    _assert_matches_polars(expr_text, row)


def test_horizontal_bare_string_args_are_column_names() -> None:
    # pl.sum_horizontal("a", "b") reads columns a and b (not the literal strings)
    # — previously the resulting TypeError was masked by the catch-all.
    _assert_matches_polars('pl.sum_horizontal("a", "b")', {"a": 5, "b": 7})
    _assert_matches_polars('pl.max_horizontal("a", "b")', {"a": 5.0, "b": 7.0})


def test_all_any_horizontal_kleene_null() -> None:
    assert (
        _trace_value('pl.all_horizontal(pl.col("a"), pl.col("b"))', {"a": True, "b": None}) is None
    )
    assert (
        _trace_value('pl.all_horizontal(pl.col("a"), pl.col("b"))', {"a": False, "b": None})
        is False
    )
    assert (
        _trace_value('pl.any_horizontal(pl.col("a"), pl.col("b"))', {"a": False, "b": None}) is None
    )
    assert (
        _trace_value('pl.any_horizontal(pl.col("a"), pl.col("b"))', {"a": True, "b": None}) is True
    )


def test_concat_str_null_makes_result_null() -> None:
    assert (
        _trace_value(
            'pl.concat_str([pl.col("a"), pl.col("b")], separator="-")', {"a": "x", "b": None}
        )
        is None
    )


def test_concat_str_non_str_separator_raises() -> None:
    # A non-str separator is a malformed authored expression (Polars requires a
    # str). Fail loud rather than silently coercing to "".
    code = 'df = df.with_columns(pl.concat_str([pl.col("a"), pl.col("b")], separator=5).alias("r"))'
    with pytest.raises(ValueError, match="separator must be a str"):
        evaluate_expression(code, "r", {"a": "x", "b": "y"})


def test_concat_str_non_bool_ignore_nulls_raises() -> None:
    # ignore_nulls must be a bool; a non-bool must not be silently truthiness-coerced.
    code = (
        'df = df.with_columns(pl.concat_str([pl.col("a"), pl.col("b")], ignore_nulls=5).alias("r"))'
    )
    with pytest.raises(ValueError, match="ignore_nulls must be a bool"):
        evaluate_expression(code, "r", {"a": "x", "b": "y"})


def test_concat_str_none_ignore_nulls_raises() -> None:
    # An explicit None for ignore_nulls is not a bool and previously coerced to
    # the default False silently.
    code = (
        "df = df.with_columns("
        'pl.concat_str([pl.col("a"), pl.col("b")], ignore_nulls=None).alias("r"))'
    )
    with pytest.raises(ValueError, match="ignore_nulls must be a bool"):
        evaluate_expression(code, "r", {"a": "x", "b": "y"})


def test_concat_str_valid_kwargs_still_work() -> None:
    # Correctly typed kwargs are unaffected: separator joins, ignore_nulls drops.
    assert (
        _trace_value(
            'pl.concat_str([pl.col("a"), pl.col("b")], separator="-", ignore_nulls=True)',
            {"a": "x", "b": None},
        )
        == "x"
    )
    _assert_matches_polars(
        'pl.concat_str([pl.col("a"), pl.col("b")], separator="-", ignore_nulls=True)',
        {"a": "x", "b": "y"},
    )


# ---------------------------------------------------------------------------
# F247 — unary ~ on a boolean is logical-not, not bitwise
# ---------------------------------------------------------------------------


def test_invert_boolean_matches_polars() -> None:
    _assert_matches_polars('~pl.col("a")', {"a": True})
    _assert_matches_polars('~pl.col("a")', {"a": False})


def test_invert_true_is_false_not_minus_two() -> None:
    assert _eval_node('~pl.col("a")', {"a": True}) is False
    assert _eval_node('~pl.col("a")', {"a": False}) is True


# ---------------------------------------------------------------------------
# F248 — unsupported comparison operators return None, not spurious True
# ---------------------------------------------------------------------------


def test_unsupported_comparison_returns_none() -> None:
    assert _eval_node('pl.col("a") is pl.col("b")', {"a": 1, "b": 1}) is None
    assert _eval_node('pl.col("a") in [1, 2, 3]', {"a": 2}) is None


def test_supported_comparison_still_works() -> None:
    assert _eval_node('pl.col("a") < pl.col("b")', {"a": 1, "b": 2}) is True
    assert _eval_node('pl.col("a") == pl.col("b")', {"a": 1, "b": 1}) is True


# ---------------------------------------------------------------------------
# F043 — replace_strict incomplete mapping fails loud
# ---------------------------------------------------------------------------


def test_replace_strict_incomplete_raises() -> None:
    code = 'df = df.with_columns(pl.col("x").replace_strict({"a": 1}).alias("r"))'
    with pytest.raises(ValueError, match="incomplete mapping"):
        evaluate_expression(code, "r", {"x": "b"})


def test_replace_strict_complete_and_default_ok() -> None:
    code = 'df = df.with_columns(pl.col("x").replace_strict({"a": 1, "b": 2}).alias("r"))'
    assert evaluate_expression(code, "r", {"x": "b"}).result_value == 2
    code_d = 'df = df.with_columns(pl.col("x").replace_strict({"a": 1}, default=0).alias("r"))'
    assert evaluate_expression(code_d, "r", {"x": "z"}).result_value == 0


def test_non_strict_replace_leaves_unmapped_unchanged() -> None:
    code = 'df = df.with_columns(pl.col("x").replace({"a": 1}).alias("r"))'
    assert evaluate_expression(code, "r", {"x": "z"}).result_value == "z"


# ---------------------------------------------------------------------------
# F030 — evaluator failures propagate; observed value is never laundered in
# ---------------------------------------------------------------------------


def test_malformed_code_argument_raises() -> None:
    with pytest.raises(Exception):
        evaluate_expression(None, "r", {"r": 42})  # type: ignore[arg-type]


def test_replace_strict_failure_propagates_out_of_evaluate() -> None:
    # A row Polars itself would reject must surface loudly, not as the row value.
    code = 'df = df.with_columns(pl.col("x").replace_strict({"a": 1}).alias("r"))'
    with pytest.raises(ValueError):
        evaluate_expression(code, "r", {"x": "b", "r": "LAUNDERED"})


# ---------------------------------------------------------------------------
# F148 — chained .with_columns(...).with_columns(...) last match wins
# ---------------------------------------------------------------------------


def test_chained_with_columns_selects_effective_definition() -> None:
    code = (
        'df = df.with_columns((pl.col("a") + 1).alias("r"))'
        '.with_columns((pl.col("a") + 100).alias("r"))'
    )
    parsed = parse_expression(code, "r")
    assert parsed is not None
    # The outer (last-applied) definition is the effective one.
    assert "100" in parsed.expression_text
    assert evaluate_expression(code, "r", {"a": 5}).result_value == 105


def test_separate_statements_last_definition_wins() -> None:
    code = (
        'df = df.with_columns((pl.col("a") + 1).alias("r"))\n'
        'df = df.with_columns((pl.col("a") + 100).alias("r"))'
    )
    assert evaluate_expression(code, "r", {"a": 5}).result_value == 105


# ---------------------------------------------------------------------------
# F149 / F246 — nested when/then branch tracking
# ---------------------------------------------------------------------------


def test_nested_when_in_otherwise_preserves_outer_metadata() -> None:
    code = (
        "df = df.with_columns(\n"
        '    pl.when(pl.col("x") > 100).then(pl.lit("big"))\n'
        "    .otherwise(\n"
        '        pl.when(pl.col("y") > 0).then(pl.lit("pos")).otherwise(pl.lit("neg"))\n'
        '    ).alias("r")\n'
        ")"
    )
    result = evaluate_expression(code, "r", {"x": 5, "y": 3})
    assert result.result_value == "pos"
    assert result.taken_branch == "otherwise"
    assert result.taken_branch_index == 1
    assert result.nested_branches == ["then"]
    # Cross-check the value against real Polars.
    got = (
        pl.DataFrame([{"x": 5, "y": 3}])
        .select(
            pl.when(pl.col("x") > 100)
            .then(pl.lit("big"))
            .otherwise(pl.when(pl.col("y") > 0).then(pl.lit("pos")).otherwise(pl.lit("neg")))
            .alias("r")
        )
        .item()
    )
    assert got == "pos"


def test_nested_when_in_then_records_actual_inner_branch() -> None:
    code = (
        "df = df.with_columns(\n"
        '    pl.when(pl.col("x") > 0).then(\n'
        '        pl.when(pl.col("y") > 10).then(pl.lit("a")).otherwise(pl.lit("b"))\n'
        '    ).otherwise(pl.lit("c")).alias("r")\n'
        ")"
    )
    result = evaluate_expression(code, "r", {"x": 5, "y": 3})
    assert result.result_value == "b"
    assert result.taken_branch == "then"
    assert result.taken_branch_index == 0
    # The inner branch actually fired is 'otherwise' (y>10 is False), not 'then'.
    assert result.nested_branches == ["otherwise"]


# ---------------------------------------------------------------------------
# F147 — renderer parenthesisation
# ---------------------------------------------------------------------------


def _text(expr_src: str, target: str = "r") -> str:
    code = f'df = df.with_columns(({expr_src}).alias("{target}"))'
    parsed = parse_expression(code, target)
    assert parsed is not None
    return parsed.expression_text


def test_compare_operand_gets_parens() -> None:
    assert _text('(pl.col("a") < pl.col("b")) * pl.col("c")') == "(a < b) * c"


def test_left_pow_gets_parens() -> None:
    assert _text('(pl.col("a") ** pl.col("b")) ** pl.col("c")') == "(a ** b) ** c"


def test_right_pow_stays_unparenthesised() -> None:
    # ** is right-associative, so a ** (b ** c) renders without parens.
    assert _text('pl.col("a") ** pl.col("b") ** pl.col("c")') == "a ** b ** c"


def test_unary_minus_base_of_pow_gets_parens() -> None:
    assert _text('(-pl.col("a")) ** pl.col("b")') == "(-a) ** b"


def test_boolop_or_inside_and_gets_parens() -> None:
    assert _text('(pl.col("a") or pl.col("b")) and pl.col("c")') == "(a or b) and c"


def test_boolop_and_inside_or_no_parens() -> None:
    assert _text('pl.col("a") and pl.col("b") or pl.col("c")') == "a and b or c"


# ---------------------------------------------------------------------------
# F242 — string constants escape embedded double quotes
# ---------------------------------------------------------------------------


def test_embedded_double_quote_is_escaped() -> None:
    code = 'df = df.with_columns(pl.lit(\'he said "hi"\').alias("r"))'
    parsed = parse_expression(code, "r")
    assert parsed is not None
    assert parsed.expression_text == r'"he said \"hi\""'


# ---------------------------------------------------------------------------
# F240 — f-string format specs / conversions preserved in alias resolution
# ---------------------------------------------------------------------------


def test_fstring_format_spec_in_alias_is_applied() -> None:
    code = 'i = 3\ndf = df.with_columns(pl.col("x").alias(f"col_{i:03d}"))'
    parsed = parse_expression(code, "col_003")
    assert parsed is not None
    assert parsed.expression_text == "x"


def test_fstring_conversion_in_render_preserved() -> None:
    code = 'df = df.with_columns(pl.lit(f"{pl.col(\'a\')!r}").alias("r"))'
    parsed = parse_expression(code, "r")
    assert parsed is not None
    assert "!r" in parsed.expression_text


# ---------------------------------------------------------------------------
# F241 — slice subscripts render readably, not as ast.dump garbage
# ---------------------------------------------------------------------------


def test_slice_subscript_renders() -> None:
    code = 'df = df.with_columns(pl.col("items")[1:2].alias("r"))'
    parsed = parse_expression(code, "r")
    assert parsed is not None
    assert "1:2" in parsed.expression_text
    assert "Slice" not in parsed.expression_text


# ---------------------------------------------------------------------------
# F239 — self-referential symbol resolution does not recurse forever
# ---------------------------------------------------------------------------


def test_self_referential_assignment_does_not_recurse() -> None:
    code = "x = x + 1\ndf = df.with_columns((pl.col('a') * x).alias('r'))"
    parsed = parse_expression(code, "r")  # must not raise RecursionError
    assert parsed is not None


# ---------------------------------------------------------------------------
# F089 — _substitute_names_in_ast copies positions onto the NEW node
# ---------------------------------------------------------------------------


def test_substitute_names_copies_location_to_new_node() -> None:
    tree = ast.parse("a + b", mode="eval").body
    table = {"b": ast.parse("c * 2", mode="eval").body}
    new = _substitute_names_in_ast(tree, table)
    assert new is not tree
    # copy_location(new, old): the substituted node inherits the original's
    # source position and compiles cleanly.
    assert getattr(new, "lineno", None) == tree.lineno
    ast.fix_missing_locations(new)
    compile(ast.Expression(body=new), "<test>", "eval")


# ---------------------------------------------------------------------------
# F243 — control-flow-reassigned var with a top-level binding is opaque
# ---------------------------------------------------------------------------


def test_control_flow_reassigned_var_is_opaque_despite_top_level_binding() -> None:
    code = (
        "factor = 2\n"
        "if cond:\n"
        "    factor = 3\n"
        'df = df.with_columns((pl.col("a") * factor).alias("r"))'
    )
    parsed = parse_expression(code, "r")
    assert parsed is not None
    assert parsed.expression_type == "opaque"


# ---------------------------------------------------------------------------
# F244 — value substitution is single-pass (inserted values not re-scanned)
# ---------------------------------------------------------------------------


def test_substitution_does_not_corrupt_inserted_string_value() -> None:
    # Column ``label`` holds a string that contains the shorter column name
    # ``a``; a sequential substitution would rewrite the ``a`` inside the
    # already-inserted "banana" value. Single-pass substitution must not.
    code = 'df = df.with_columns(pl.concat_str([pl.col("label"), pl.col("a")]).alias("r"))'
    result = evaluate_expression(code, "r", {"label": "banana", "a": "X"})
    assert "banana" in result.substituted_text
    # The inserted "banana" must be intact (its inner letters not replaced by X).
    assert "bXnXnX" not in result.substituted_text


# ---------------------------------------------------------------------------
# F704 / F705 — chain parse reuse still yields correct dependency chain
# ---------------------------------------------------------------------------


def test_expression_chain_dependencies_resolved() -> None:
    code = (
        'df = df.with_columns((pl.col("base") * 2).alias("mid"))\n'
        'df = df.with_columns((pl.col("mid") + 1).alias("top"))'
    )
    chain = parse_expression_chain(code, "top")
    assert chain is not None
    cols = [p.target_column for p in chain]
    assert cols == ["mid", "top"]
