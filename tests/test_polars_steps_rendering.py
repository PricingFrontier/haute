"""Low-code Polars steps: schema validation and rendering.

A step payload renders to exactly the expected Polars code, the rendered code is a fixed point of
user-code extraction and of ruff format, long statements break one argument per line, every invalid
payload names its step, and the extended vocabulary (ordered windows, dates, strings, joins and
summaries) renders and runs.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from typing import Any

import pytest

from haute._code_extraction import (
    extract_user_code,
)
from haute._polars_steps import (
    PolarsStepError,
    rename_step_inputs,
    render_polars_steps,
    validate_polars_steps,
)
from haute._polars_steps_layout import layout_statement, restyle_statement
from haute._types import (
    PipelineGraph,
)
from haute.executor import execute_graph
from tests._polars_steps_helpers import (
    GOLDEN_CODE,
    GOLDEN_STEP_LINES,
    GOLDEN_STEPS,
    RUFF_FORMAT,
    _capped_context,
    _extended_frame,
    _stepped,
    col,
    cond,
    null,
    num,
    source,
    step,
    step_text,
    text,
    var,
    window,
)
from tests.conftest import (
    make_edge,
)
from tests.test_polars_steps_corpus import TRANSLATIONS as CORPUS_TRANSLATIONS

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_golden_payload_renders_exactly() -> None:
    rendered = render_polars_steps(GOLDEN_STEPS, ["quotes", "rates"], start="input")
    assert rendered.code == GOLDEN_CODE
    assert rendered.step_lines == GOLDEN_STEP_LINES


def test_rendered_code_is_a_fixpoint_of_user_code_extraction() -> None:
    code = render_polars_steps(GOLDEN_STEPS, ["quotes", "rates"], start="input").code
    body = "\n".join(f"    {line}" for line in code.splitlines()) + "\n    return df"
    extracted = extract_user_code(
        '    """doc"""\n    df: pl.LazyFrame\n' + body,
        kind="polars",
        param_names=["quotes", "rates"],
    )
    assert extracted == code


#: Steps whose statements would run past 88 columns in the node body: a
#: select, a two-aggregation group-by and a join.
LONG_STEPS: list[dict[str, Any]] = [
    source(),
    step(
        "sel",
        "select",
        columns=[
            "quote_id",
            "policy_id",
            "region",
            "channel",
            "premium",
            "sum_insured",
            "start_date",
            "age",
        ],
    ),
    step(
        "g",
        "group_by",
        keys=["region"],
        aggregations=[
            {"column": "premium", "agg": "sum", "name": "premium_total"},
            {"column": "sum_insured", "agg": "mean", "name": "average_sum_insured"},
        ],
    ),
    step(
        "j",
        "join",
        input="rates",
        how="left",
        maintain_order="left_right",
        left_on=["region"],
        right_on=["region"],
        suffix="_rates",
    ),
    step("l", "limit", n=5),
]


LONG_CODE = "\n".join(
    [
        "df = quotes",
        "df = df.select(",
        '    "quote_id",',
        '    "policy_id",',
        '    "region",',
        '    "channel",',
        '    "premium",',
        '    "sum_insured",',
        '    "start_date",',
        '    "age",',
        ")",
        'df = df.group_by("region", maintain_order=True).agg(',
        '    pl.col("premium").sum().alias("premium_total"),',
        '    pl.col("sum_insured").mean().alias("average_sum_insured"),',
        ")",
        "df = df.join(",
        "    rates,",
        '    left_on=["region"],',
        '    right_on=["region"],',
        '    how="left",',
        '    suffix="_rates",',
        '    maintain_order="left_right",',
        ")",
        "df = df.head(5)",
    ]
)


def test_long_statements_break_one_argument_per_line() -> None:
    rendered = render_polars_steps(LONG_STEPS, ["quotes", "rates"], start="input")
    assert rendered.code == LONG_CODE
    assert rendered.step_lines == ((1, 1), (2, 11), (12, 15), (16, 23), (24, 24))
    # The node body holds 88 columns four columns in: 84 at column 0 still fits,
    # 85 breaks.
    fits = f'df = df.filter(pl.col("{"a" * 54}") > 1)'
    assert len(fits) == 84
    assert layout_statement(fits) == fits
    breaks = f'df = df.filter(pl.col("{"a" * 55}") > 1)'
    assert layout_statement(breaks) == f'df = df.filter(\n    pl.col("{"a" * 55}") > 1\n)'
    body = "\n".join(f"    {line}" for line in rendered.code.splitlines()) + "\n    return df"
    assert extract_user_code(body, kind="polars", param_names=["quotes", "rates"]) == (
        rendered.code
    )


#: Text variables past 88 columns: the first fits once parenthesised, the second never does.
LONG_VARIABLE_STEPS: list[dict[str, Any]] = [
    source(),
    step("a", "variable", name="long_threshold_label", value=text("x" * 70)),
    step("b", "variable", name="longer_threshold_label", value=text("y" * 90)),
]


def test_a_long_variable_is_parenthesised_only_when_that_makes_it_fit() -> None:
    rendered = render_polars_steps(LONG_VARIABLE_STEPS, ["quotes"], start="input")
    x, y = "x" * 70, "y" * 90
    assert step_text(rendered, 1) == f'long_threshold_label = (\n    "{x}"\n)'
    assert step_text(rendered, 2) == f'longer_threshold_label = "{y}"'
    assert rendered.step_lines == ((1, 1), (2, 4), (5, 5))


def test_restyle_drops_brackets_precedence_does_not_need_and_prefers_double_quotes() -> None:
    assert (
        restyle_statement("df = df.filter((pl.col('a') == 0))")
        == 'df = df.filter(pl.col("a") == 0)'
    )
    assert (
        restyle_statement("df = df.filter(((pl.col('a') == 1) & (pl.col('b') == 2)))")
        == 'df = df.filter((pl.col("a") == 1) & (pl.col("b") == 2))'
    )
    assert (
        restyle_statement("df = df.with_columns((pl.col('a') - (pl.col('b') + 1)).alias('c'))")
        == 'df = df.with_columns((pl.col("a") - (pl.col("b") + 1)).alias("c"))'
    )
    # Single quotes stay where double ones would need more escapes.
    assert restyle_statement("x = 'say \"hi\"'") == "x = 'say \"hi\"'"
    for value in ["plain", "it's", 'say "hi"', "both ' and \"", "back\\slash", "tab\there", "\x00"]:
        literal = restyle_statement(f"x = {value!r}")[len("x = ") :]
        assert ast.literal_eval(literal) == value


def test_the_layout_refuses_a_statement_outside_the_renderer_vocabulary() -> None:
    for statement in ("df = df[0]", "df = lambda: df", "df.head(1)", "df = df.head(1"):
        with pytest.raises(ValueError, match="Cannot (lay out|restyle) the rendered statement"):
            layout_statement(statement)


def test_free_code_keeps_its_authored_layout_however_long() -> None:
    authored = (
        "df = df.with_columns((pl.col('premium') * 2)"
        ".alias('doubled_premium_for_the_quote_report'))"
    )
    rendered = render_polars_steps(
        [source(), step("c", "free_code", code=authored)], ["quotes"], start="input"
    )
    assert rendered.code == f"df = quotes\n{authored}"
    assert rendered.step_lines == ((1, 1), (2, 2))


def test_rendered_code_is_a_fixed_point_of_ruff_format() -> None:
    programs = [GOLDEN_STEPS, LONG_STEPS, LONG_VARIABLE_STEPS]
    for entry in CORPUS_TRANSLATIONS.values():
        if entry.get("steps") is not None:
            programs.extend([entry["steps"], *(entry.get("aux") or {}).values()])
    codes = [render_polars_steps(steps, start="input").code for steps in programs]
    # Not vacuous: many of these statements are laid out over several lines.
    assert sum(code.count("\n)") for code in codes) > 20
    # A rendering lands in a node function's body, four columns in; that is
    # where ruff must leave it unchanged.
    module = (
        "\n\n\n".join(
            "def node():\n"
            + "\n".join(f"    {line}" if line.strip() else "" for line in code.splitlines())
            for code in codes
        )
        + "\n"
    )
    formatted = subprocess.run(
        RUFF_FORMAT, input=module, capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout
    assert formatted == module


def test_free_code_renders_multiline_statements_and_tracks_following_steps() -> None:
    snippet = (
        "# Adjust the current frame\r\n"
        "def adjusted(column):\r\n"
        "    return column * 2\r\n"
        "\r\n"
        "df = df.with_columns(\r\n"
        '    adjusted(pl.col("premium")).alias("gross")\r\n'
        ")\r\n  "
    )
    steps = [source(), step("c", "free_code", code=snippet), step("l", "limit", n=2)]
    rendered = render_polars_steps(steps, ["quotes"], start="input")
    assert (
        rendered.code
        == "df = quotes\n" + snippet.replace("\r\n", "\n").rstrip() + "\ndf = df.head(2)"
    )
    assert rendered.step_lines == ((1, 1), (2, 8), (9, 9))
    assert steps[1]["code"] == snippet
    body = "\n".join(f"    {line}" for line in rendered.code.splitlines()) + "\n    return df"
    assert extract_user_code(body, kind="polars", param_names=["quotes"]) == rendered.code


@pytest.mark.parametrize(
    ("code", "fragment"),
    [
        (None, "non-empty string"),
        ("", "non-empty string"),
        (" \n\t", "Python statement or comment"),
        ("\n\n", "Python statement or comment"),
        ("df = (", "Invalid Python"),
        ("return df", "Assign the result to df"),
        ("if True:\n    return df", "Assign the result to df"),
        ("yield df", "Invalid Python"),
        ("await df", "Invalid Python"),
        ("break", "Invalid Python"),
        ("from __future__ import annotations", "Invalid Python"),
        ("from math import *", "Invalid Python"),
        ("global df\ndf = quotes", "Invalid Python"),
        ("df = df\x00", "Invalid Python"),
    ],
)
def test_free_code_rejects_invalid_snippets(code: object, fragment: str) -> None:
    with pytest.raises(PolarsStepError, match=fragment) as exc:
        render_polars_steps(
            [source(), step("c", "free_code", code=code)], ["quotes"], start="input"
        )
    assert exc.value.step_index == 1


def test_free_code_validation_never_executes_the_snippet(tmp_path: Path) -> None:
    marker = tmp_path / "must_not_exist.txt"
    code = f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')"
    assert render_polars_steps(
        [source(), step("c", "free_code", code=code)], start="input"
    ).code.endswith(code)
    assert not marker.exists()


@pytest.mark.parametrize("code", ["# Write code here\n", "\n# the end", "# one\n\n    # two"])
def test_a_code_step_of_only_comments_renders_as_written(code: str) -> None:
    rendered = render_polars_steps(
        [source(), step("l", "limit", n=2), step("c", "free_code", code=code)], start="input"
    )
    assert rendered.code == f"df = quotes\ndf = df.head(2)\n{code.rstrip()}"
    assert rendered.step_lines[2] == (3, 2 + len(code.rstrip().split("\n")))


def test_a_comment_renders_above_its_statement_outside_its_line_range() -> None:
    steps = [
        step("s", "source", input="quotes", comment="# Quotes, as priced\n"),
        step("l", "limit", n=5, comment="\n# keep five\n    # rows\n"),
        step("d", "drop", columns=["a"]),
    ]
    rendered = render_polars_steps(steps, ["quotes"], start="input")
    assert rendered.code == "\n".join(
        [
            "# Quotes, as priced",
            "df = quotes",
            "",
            "# keep five",
            "    # rows",
            "df = df.head(5)",
            'df = df.drop("a")',
        ]
    )
    # A failing step is reported at its statement, below its comment.
    assert rendered.step_lines == ((2, 2), (6, 6), (7, 7))
    # The comment is the step's own: validation keeps it and nothing else reads it.
    assert validate_polars_steps(steps)[1]["comment"] == "\n# keep five\n    # rows\n"


@pytest.mark.parametrize(
    ("comment", "fragment"),
    [
        (7, "non-empty text"),
        ("", "non-empty text"),
        ("# no newline", "end in a newline"),
        ("# a\n# b", "end in a newline"),
        ("# trailing  \n", "line 1 ends in whitespace"),
        ("# ok\n   \n", "line 2 ends in whitespace"),
        ("df = df.head(1)\n", "line 1 is neither a comment nor blank"),
        ("# a\nx = 1  # b\n", "line 2 is neither a comment nor blank"),
        ("# a\rdf = 1\n", "carriage return"),
        ("# a\x00\n", "null character"),
    ],
)
def test_a_comment_holds_only_comment_and_blank_lines(comment: object, fragment: str) -> None:
    with pytest.raises(PolarsStepError, match=fragment) as exc:
        render_polars_steps(
            [source(), step("l", "limit", n=1, comment=comment)], ["quotes"], start="input"
        )
    assert exc.value.step_index == 1


def test_a_code_step_keeps_its_comments_in_its_code_not_a_comment_field() -> None:
    with pytest.raises(PolarsStepError, match="Unknown field") as exc:
        validate_polars_steps(
            [source(), step("c", "free_code", code="x = 1", comment="# about x\n")]
        )
    assert exc.value.step_index == 1


@pytest.mark.parametrize(
    "code",
    [
        "rate = 2",
        "for rate in range(3):\n    pass",
        "import rate",
        "from math import pi as rate",
        "def rate():\n    return 1",
        "class rate:\n    pass",
        "with open('f') as rate:\n    pass",
        "try:\n    pass\nexcept Exception as rate:\n    pass",
        "values = [(rate := v) for v in range(3)]",
        "match df:\n    case [rate]:\n        pass",
    ],
)
def test_a_variable_is_refused_when_code_above_it_assigns_its_name(code: str) -> None:
    steps = [
        source(),
        step("c", "free_code", code=code),
        step("v", "variable", name="rate", value=num(3)),
    ]
    with pytest.raises(PolarsStepError, match="Variable 'rate' is already assigned") as exc:
        render_polars_steps(steps, ["quotes"], start="input")
    assert exc.value.step_index == 2


@pytest.mark.parametrize(
    "code",
    [
        "def helper():\n    rate = 2\n    return rate",
        "values = [rate for rate in range(3)]",
        "scale = lambda rate: rate * 2",
        "print(rate_table)",
    ],
)
def test_a_variable_may_take_a_name_the_code_binds_only_in_its_own_scope(code: str) -> None:
    steps = [
        source(),
        step("c", "free_code", code=code),
        step("v", "variable", name="rate", value=num(3)),
    ]
    assert render_polars_steps(steps, ["quotes"], start="input").code.endswith("rate = 3")
    # Code after the variable may reassign it, as before.
    later = [source(), steps[2], step("c", "free_code", code="rate = rate * 2")]
    render_polars_steps(later, ["quotes"], start="input")


@pytest.mark.parametrize(
    ("kind_step", "expected"),
    [
        (step("x", "select", columns=["a", "b"]), 'df = df.select("a", "b")'),
        (step("x", "drop", columns=["a"]), 'df = df.drop("a")'),
        (step("x", "rename", renames=[{"from": "a", "to": "b"}]), 'df = df.rename({"a": "b"})'),
        (
            step(
                "x",
                "cast",
                strict=True,
                casts=[{"column": "a", "dtype": "Int64"}, {"column": "b", "dtype": "String"}],
            ),
            'df = df.with_columns(pl.col("a").cast(pl.Int64), pl.col("b").cast(pl.String))',
        ),
        (
            step("x", "sort", keys=[{"column": "a", "descending": True}], nulls_last=True),
            'df = df.sort("a", descending=True, nulls_last=True, maintain_order=True)',
        ),
        (
            step("x", "unique", columns=[], keep="any"),
            'df = df.unique(keep="any", maintain_order=True)',
        ),
        (
            step("x", "unique", columns=["a"], keep="last"),
            'df = df.unique(subset=["a"], keep="last", maintain_order=True)',
        ),
        (
            step(
                "x",
                "join",
                input="rates",
                how="cross",
                maintain_order="left_right",
                left_on=[],
                right_on=[],
                suffix="_r",
            ),
            'df = df.join(rates, how="cross", suffix="_r", maintain_order="left_right")',
        ),
        (
            step("x", "concat", inputs=["rates"], how="diagonal"),
            'df = pl.concat([df, rates], how="diagonal")',
        ),
        (
            step("x", "fill_null", columns=["a"], fill={"kind": "value", "value": num(0)}),
            'df = df.with_columns(pl.col("a").fill_null(0))',
        ),
        (
            step("x", "fill_null", columns=[], fill={"kind": "strategy", "strategy": "forward"}),
            'df = df.with_columns(pl.all().fill_null(strategy="forward"))',
        ),
        (step("x", "limit", n=5), "df = df.head(5)"),
        (
            step(
                "x",
                "filter",
                match="any",
                conditions=[
                    cond("a", "is_null"),
                    cond("b", "not_in", values=[num(1), num(2)], number_type="integer"),
                ],
            ),
            'df = df.filter(pl.col("a").is_null() | ~pl.col("b").is_in([1, 2]))',
        ),
        (
            step(
                "x",
                "filter",
                match="all",
                conditions=[cond("p", "is_in", values=[num(100), num(300)], number_type="float")],
            ),
            'df = df.filter(pl.col("p").is_in([100.0, 300.0]))',
        ),
        (
            step(
                "x",
                "filter",
                match="all",
                conditions=[cond("m", "is_in", values=[num(1.5), num(2)], number_type="decimal")],
            ),
            "df = df.filter(\n"
            '    pl.col("m").is_in(pl.Series(["1.5", "2"]).str.to_decimal(scale=1).implode())\n'
            ")",
        ),
        (
            step(
                "x",
                "filter",
                match="all",
                conditions=[
                    cond("a", "contains", value=text("x")),
                    cond("a", "starts_with", value=col("b")),
                ],
            ),
            "df = df.filter(\n"
            '    pl.col("a").str.contains("x", literal=True)\n'
            '    & pl.col("a").str.starts_with(pl.col("b"))\n'
            ")",
        ),
        (
            step(
                "x",
                "filter",
                match="all",
                conditions=[
                    cond(
                        "d", "gt", value={"kind": "literal", "type": "date", "value": "2026-01-31"}
                    )
                ],
            ),
            'df = df.filter(pl.col("d") > pl.lit("2026-01-31").str.to_date())',
        ),
        (
            step(
                "x",
                "filter",
                match="all",
                conditions=[
                    cond(
                        "d",
                        "is_in",
                        values=[
                            {"kind": "literal", "type": "date", "value": "2026-01-01"},
                            {"kind": "literal", "type": "date", "value": "2026-02-01"},
                        ],
                    )
                ],
            ),
            "df = df.filter(\n"
            '    pl.col("d").is_in(\n'
            '        pl.Series(["2026-01-01", "2026-02-01"]).str.to_date().implode()\n'
            "    )\n"
            ")",
        ),
        (
            step("x", "with_column", name="n", expr={"type": "operand", "operand": num(2)}),
            'df = df.with_columns(pl.lit(2).alias("n"))',
        ),
        (
            step(
                "x",
                "with_column",
                name="n",
                expr={"type": "binary", "left": num(2), "op": "*", "right": col("a")},
            ),
            'df = df.with_columns((pl.lit(2) * pl.col("a")).alias("n"))',
        ),
        (
            step(
                "x",
                "with_column",
                name="n",
                expr={"type": "function", "fn": "round", "operand": col("a"), "args": [num(2)]},
            ),
            'df = df.with_columns(pl.col("a").round(2).alias("n"))',
        ),
        (
            step(
                "x",
                "with_column",
                name="n",
                expr={
                    "type": "function",
                    "fn": "cast",
                    "operand": col("a"),
                    "args": [text("Float64")],
                },
            ),
            'df = df.with_columns(pl.col("a").cast(pl.Float64).alias("n"))',
        ),
        (
            step(
                "x",
                "with_column",
                name="n",
                expr={"type": "function", "fn": "upper", "operand": text("ab"), "args": []},
            ),
            'df = df.with_columns(pl.lit("ab").str.to_uppercase().alias("n"))',
        ),
        (
            step(
                "x",
                "with_column",
                name="n",
                expr={"type": "window", "agg": "sum", "of": col("a"), "over": ["k"]},
            ),
            'df = df.with_columns(pl.col("a").sum().over(["k"]).alias("n"))',
        ),
    ],
)
def test_each_step_kind_renders_its_statement(kind_step: dict[str, Any], expected: str) -> None:
    rendered = render_polars_steps([source(), kind_step], ["quotes", "rates"], start="input")
    assert step_text(rendered, 1) == expected


def test_variable_receiver_and_then_branch_render_as_expressions() -> None:
    steps = [
        source(),
        step("v", "variable", name="rate", value=num(2)),
        step("a", "with_column", name="x", expr={"type": "operand", "operand": var("rate")}),
        step(
            "b",
            "with_column",
            name="y",
            expr={
                "type": "conditional",
                "branches": [
                    {
                        "match": "all",
                        "conditions": [cond("premium", "gt", value=var("rate"))],
                        "then": var("rate"),
                    }
                ],
                "otherwise": col("premium"),
            },
        ),
    ]
    rendered = render_polars_steps(steps, ["quotes"], start="input")
    assert step_text(rendered, 2) == 'df = df.with_columns(pl.lit(rate).alias("x"))'
    assert step_text(rendered, 3) == (
        "df = df.with_columns(\n"
        '    pl.when(pl.col("premium") > rate)\n'
        "    .then(pl.lit(rate))\n"
        '    .otherwise(pl.col("premium"))\n'
        '    .alias("y")\n'
        ")"
    )


INVALID_PAYLOADS: list[tuple[str, list[Any] | object, int | None, str]] = [
    ("not a list", {"kind": "source"}, None, "must be a list"),
    ("empty", [], None, "Choose the input"),
    (
        "first not source",
        [step("f", "filter", match="all", conditions=[cond("a", "is_null")])],
        0,
        "first step",
    ),
    (
        "second source",
        [source(), step("x", "limit", n=1), source("rates", "s2")],
        2,
        "Only the first step",
    ),
    ("unknown input", [source("policies")], 0, "Unknown input 'policies'"),
    ("unknown kind", [source(), step("x", "explode", columns=["a"])], 1, "Unknown step kind"),
    ("unknown key", [source(), step("x", "limit", n=1, foo=2)], 1, "Unknown field"),
    ("missing id", [source(), {"kind": "limit", "n": 1}], 1, "non-empty string id"),
    ("duplicate id", [source(), step("s", "limit", n=1)], 0, "unique"),
    (
        "empty conditions",
        [source(), step("x", "filter", match="all", conditions=[])],
        1,
        "at least one condition",
    ),
    (
        "empty membership",
        [source(), step("x", "filter", match="all", conditions=[cond("a", "is_in", values=[])])],
        1,
        "at least one value",
    ),
    (
        "mixed membership",
        [
            source(),
            step(
                "x",
                "filter",
                match="all",
                conditions=[cond("a", "is_in", values=[num(1), text("b")])],
            ),
        ],
        1,
        "same type",
    ),
    (
        "value for is_null",
        [
            source(),
            step("x", "filter", match="all", conditions=[cond("a", "is_null", value=num(1))]),
        ],
        1,
        "takes no value",
    ),
    (
        "no value for eq",
        [source(), step("x", "filter", match="all", conditions=[cond("a", "eq")])],
        1,
        "needs a single value",
    ),
    (
        "variable before definition",
        [
            source(),
            step("a", "with_column", name="x", expr={"type": "operand", "operand": var("rate")}),
            step("v", "variable", name="rate", value=num(1)),
        ],
        1,
        "not defined by an earlier step",
    ),
    (
        "variable named df",
        [source(), step("v", "variable", name="df", value=num(1))],
        1,
        "not a valid name",
    ),
    (
        "variable keyword",
        [source(), step("v", "variable", name="class", value=num(1))],
        1,
        "not a valid name",
    ),
    (
        "variable bad identifier",
        [source(), step("v", "variable", name="1x", value=num(1))],
        1,
        "not a valid name",
    ),
    (
        "variable shadows input",
        [source(), step("v", "variable", name="rates", value=num(1))],
        1,
        "already an input name",
    ),
    (
        "cross join with keys",
        [
            source(),
            step(
                "j",
                "join",
                input="rates",
                how="cross",
                maintain_order="left_right",
                left_on=["a"],
                right_on=[],
                suffix="_r",
            ),
        ],
        1,
        "no key columns",
    ),
    (
        "unequal keys",
        [
            source(),
            step(
                "j",
                "join",
                input="rates",
                how="left",
                maintain_order="left_right",
                left_on=["a"],
                right_on=[],
                suffix="_r",
            ),
        ],
        1,
        "same length",
    ),
    (
        "round without args",
        [
            source(),
            step(
                "x",
                "with_column",
                name="n",
                expr={"type": "function", "fn": "round", "operand": col("a"), "args": []},
            ),
        ],
        1,
        "takes 1 argument",
    ),
    (
        "clip one arg",
        [
            source(),
            step(
                "x",
                "with_column",
                name="n",
                expr={"type": "function", "fn": "clip", "operand": col("a"), "args": [num(1)]},
            ),
        ],
        1,
        "takes 2 argument",
    ),
    (
        "nan literal",
        [
            source(),
            step("x", "limit", n=1),
            step("f", "filter", match="all", conditions=[cond("a", "gt", value=num(float("nan")))]),
        ],
        2,
        "finite number",
    ),
    (
        "bad date",
        [
            source(),
            step(
                "f",
                "filter",
                match="all",
                conditions=[
                    cond(
                        "a", "gt", value={"kind": "literal", "type": "date", "value": "2026-13-40"}
                    )
                ],
            ),
        ],
        1,
        "not a real date",
    ),
    ("limit zero", [source(), step("x", "limit", n=0)], 1, "greater than zero"),
    (
        "number for contains",
        [
            source(),
            step("f", "filter", match="all", conditions=[cond("a", "contains", value=num(1))]),
        ],
        1,
        "needs a text value",
    ),
]


@pytest.mark.parametrize(
    ("label", "steps", "index", "fragment"), INVALID_PAYLOADS, ids=[p[0] for p in INVALID_PAYLOADS]
)
def test_invalid_payloads_name_their_step(
    label: str, steps: object, index: int | None, fragment: str
) -> None:
    with pytest.raises(PolarsStepError) as exc_info:
        render_polars_steps(steps, ["quotes", "rates"], start="input")
    assert exc_info.value.step_index == index
    assert fragment in exc_info.value.message
    if index is not None:
        assert str(exc_info.value).startswith(f"Step {index + 1}: ")


def test_validate_polars_steps_skips_input_name_checks() -> None:
    assert validate_polars_steps([source("policies")])[0]["input"] == "policies"


def test_rename_step_inputs_rewrites_every_reference_and_rejects_collisions() -> None:
    steps = [
        source("a"),
        step(
            "j",
            "join",
            input="b",
            how="left",
            maintain_order="left_right",
            left_on=["k"],
            right_on=["k"],
            suffix="_b",
        ),
        step("c", "concat", inputs=["b", "d"], how="vertical"),
    ]
    renamed = rename_step_inputs(steps, {"a": "A", "b": "B"})
    assert [s.get("input") or s.get("inputs") for s in renamed] == ["A", "B", ["B", "d"]]
    assert steps[0]["input"] == "a", "input list is copied, not mutated"
    with pytest.raises(PolarsStepError, match="more than one input"):
        rename_step_inputs(steps, {"a": "d"})


# ---------------------------------------------------------------------------
# Vocabulary extensions: ordered windows, dates, strings, joins, summaries
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind_step", "expected"),
    [
        (
            window(
                "rn",
                "row_number",
                None,
                ["region"],
                [("premium", False), ("quote_id", False)],
            ),
            "df = df.with_columns(\n"
            "    pl.int_range(1, pl.len() + 1)\n"
            '    .over(["region"], order_by=["premium", "quote_id"], descending=False)\n'
            '    .alias("rn")\n'
            ")",
        ),
        (
            window("cs", "cum_sum", "premium", ["region"], [("quote_id", True)]),
            "df = df.with_columns(\n"
            '    pl.col("premium")\n'
            "    .cum_sum()\n"
            '    .over(["region"], order_by=["quote_id"], descending=True)\n'
            '    .alias("cs")\n'
            ")",
        ),
        (
            window("prev", "shift", "premium", ["region"], [("quote_id", False)]),
            "df = df.with_columns(\n"
            '    pl.col("premium")\n'
            "    .shift(1)\n"
            '    .over(["region"], order_by=["quote_id"], descending=False)\n'
            '    .alias("prev")\n'
            ")",
        ),
        (
            window("rk", "dense_rank", "premium", ["region"], descending=True),
            "df = df.with_columns(\n"
            '    pl.col("premium")\n'
            '    .rank(method="dense", descending=True)\n'
            '    .over(["region"])\n'
            '    .alias("rk")\n'
            ")",
        ),
        (
            window("ff", "forward_fill", "premium", ["region"], [("quote_id", False)]),
            "df = df.with_columns(\n"
            '    pl.col("premium")\n'
            '    .fill_null(strategy="forward")\n'
            '    .over(["region"], order_by=["quote_id"], descending=False)\n'
            '    .alias("ff")\n'
            ")",
        ),
        (
            window("tot", "sum", "premium", []),
            'df = df.with_columns(pl.col("premium").sum().alias("tot"))',
        ),
        (window("n", "len", None, []), 'df = df.with_columns(pl.len().alias("n"))'),
        (
            step(
                "x",
                "with_column",
                name="wd",
                expr={"type": "function", "fn": "weekday", "operand": col("d"), "args": []},
            ),
            'df = df.with_columns(pl.col("d").dt.weekday().alias("wd"))',
        ),
        (
            step(
                "x",
                "with_column",
                name="r",
                expr={
                    "type": "function",
                    "fn": "offset_by",
                    "operand": col("d"),
                    "args": [text("-3y")],
                },
            ),
            'df = df.with_columns(pl.col("d").dt.offset_by("-3y").alias("r"))',
        ),
        (
            step(
                "x",
                "with_column",
                name="n",
                expr={"type": "function", "fn": "total_days", "operand": col("d"), "args": []},
            ),
            'df = df.with_columns(pl.col("d").dt.total_days().alias("n"))',
        ),
        (
            step(
                "x",
                "with_column",
                name="p",
                expr={
                    "type": "function",
                    "fn": "split_part",
                    "operand": col("pc"),
                    "args": [text(" "), num(0)],
                },
            ),
            "df = df.with_columns(\n"
            '    pl.col("pc").str.split(" ").list.get(0, null_on_oob=True).alias("p")\n'
            ")",
        ),
        (
            step(
                "x",
                "with_column",
                name="p",
                expr={
                    "type": "function",
                    "fn": "slice",
                    "operand": col("pc"),
                    "args": [num(-3), num(3)],
                },
            ),
            'df = df.with_columns(pl.col("pc").str.slice(-3, 3).alias("p"))',
        ),
        (
            step(
                "x",
                "with_column",
                name="p",
                expr={
                    "type": "function",
                    "fn": "replace_regex",
                    "operand": col("pc"),
                    "args": [text("\\s+"), text("")],
                },
            ),
            'df = df.with_columns(pl.col("pc").str.replace_all("\\\\s+", "").alias("p"))',
        ),
        (
            step(
                "x",
                "with_column",
                name="p",
                expr={
                    "type": "function",
                    "fn": "extract",
                    "operand": col("pc"),
                    "args": [text("^([A-Z]+)"), num(1)],
                },
            ),
            'df = df.with_columns(pl.col("pc").str.extract("^([A-Z]+)", 1).alias("p"))',
        ),
        (
            step(
                "x",
                "with_column",
                name="p",
                expr={
                    "type": "function",
                    "fn": "try_cast",
                    "operand": col("pc"),
                    "args": [text("Int32")],
                },
            ),
            'df = df.with_columns(pl.col("pc").cast(pl.Int32, strict=False).alias("p"))',
        ),
        (
            step(
                "x",
                "with_column",
                name="k",
                expr={"type": "concat", "parts": [col("a"), text("-"), col("b")], "separator": ""},
            ),
            "df = df.with_columns(\n"
            '    pl.concat_str([pl.col("a"), pl.lit("-"), pl.col("b")], separator="").alias("k")\n'
            ")",
        ),
        (
            step(
                "x",
                "with_column",
                name="k",
                expr={
                    "type": "conditional",
                    "branches": [
                        {
                            "match": "all",
                            "conditions": [cond("a", "eq", value=num(1))],
                            "then": col("b"),
                        }
                    ],
                    "otherwise": null(),
                },
            ),
            "df = df.with_columns(\n"
            '    pl.when(pl.col("a") == 1).then(pl.col("b")).otherwise(pl.lit(None)).alias("k")\n'
            ")",
        ),
        (
            step(
                "x",
                "filter",
                match="all",
                conditions=[cond("pc", "matches", value=text("^[A-Z]{2}"))],
            ),
            'df = df.filter(pl.col("pc").str.contains("^[A-Z]{2}"))',
        ),
        (
            step(
                "x",
                "group_by",
                keys=[],
                aggregations=[
                    {"column": "a", "agg": "quantile", "name": "p95", "quantile": 0.95},
                    {"column": "a", "agg": "len", "name": "n"},
                ],
            ),
            "df = df.select(\n"
            '    pl.col("a").quantile(0.95, interpolation="linear").alias("p95"),\n'
            '    pl.len().alias("n"),\n'
            ")",
        ),
        (
            step(
                "x",
                "group_by",
                keys=["k"],
                aggregations=[
                    {
                        "column": "a",
                        "agg": "sum",
                        "name": "open",
                        "where": {
                            "match": "all",
                            "conditions": [cond("s", "eq", value=text("open"))],
                        },
                    },
                    {
                        "column": "a",
                        "agg": "len",
                        "name": "big",
                        "where": {
                            "match": "any",
                            "conditions": [cond("a", "gt", value=num(9)), cond("s", "is_null")],
                        },
                    },
                ],
            ),
            'df = df.group_by("k", maintain_order=True).agg(\n'
            '    pl.col("a").filter(pl.col("s") == "open").sum().alias("open"),\n'
            '    ((pl.col("a") > 9) | pl.col("s").is_null()).sum().alias("big"),\n'
            ")",
        ),
        (
            step(
                "x",
                "join",
                input="rates",
                how="left",
                left_on=["region"],
                right_on=["region"],
                suffix="_r",
                validate="m:1",
                maintain_order="left",
            ),
            "df = df.join(\n"
            "    rates,\n"
            '    left_on=["region"],\n'
            '    right_on=["region"],\n'
            '    how="left",\n'
            '    suffix="_r",\n'
            '    validate="m:1",\n'
            '    maintain_order="left",\n'
            ")",
        ),
        (
            step("x", "unique", columns=["a"], keep="none"),
            'df = df.unique(subset=["a"], keep="none", maintain_order=True)',
        ),
        (
            step(
                "x",
                "cast",
                strict=True,
                casts=[{"column": "a", "dtype": "Int8"}, {"column": "b", "dtype": "Float32"}],
            ),
            'df = df.with_columns(pl.col("a").cast(pl.Int8), pl.col("b").cast(pl.Float32))',
        ),
    ],
)
def test_extended_vocabulary_renders(kind_step: dict[str, Any], expected: str) -> None:
    rendered = render_polars_steps([source(), kind_step], ["quotes", "rates"], start="input")
    assert step_text(rendered, 1) == expected


def test_window_quantile_renders() -> None:
    steps = [
        source(),
        step(
            "w",
            "with_column",
            name="p90",
            expr={
                "type": "window",
                "agg": "quantile",
                "of": col("premium"),
                "over": ["region"],
                "quantile": 0.9,
            },
        ),
    ]
    assert step_text(render_polars_steps(steps, ["quotes"], start="input"), 1) == (
        "df = df.with_columns(\n"
        '    pl.col("premium")\n'
        '    .quantile(0.9, interpolation="linear")\n'
        '    .over(["region"])\n'
        '    .alias("p90")\n'
        ")"
    )


@pytest.mark.parametrize(
    ("kind_step", "index", "fragment"),
    [
        (
            window("rn", "row_number", None, ["r"], [("a", False), ("b", True)]),
            1,
            "share one direction",
        ),
        (window("rn", "row_number", None, [], [("a", False)]), 1, "sort the frame instead"),
        (window("rn", "row_number", None, ["r"], []), 1, "at least one column"),
        (
            step(
                "x",
                "with_column",
                name="k",
                expr={"type": "concat", "parts": [col("a")], "separator": ""},
            ),
            1,
            "at least two parts",
        ),
        (
            step("x", "filter", match="all", conditions=[cond("a", "is_in", values=[null()])]),
            1,
            "cannot include null",
        ),
        (step("x", "variable", name="v", value=null()), 1, "number, text or true/false"),
        (
            step(
                "x",
                "group_by",
                keys=[],
                aggregations=[{"column": "a", "agg": "quantile", "name": "q", "quantile": 1.5}],
            ),
            1,
            "between 0 and 1",
        ),
        (
            step(
                "x",
                "group_by",
                keys=[],
                aggregations=[
                    {
                        "column": "a",
                        "agg": "sum",
                        "name": "q",
                        "where": cond("a", "gt", value=num(1)),
                    }
                ],
            ),
            1,
            "Unknown field",
        ),
        (
            step(
                "x",
                "join",
                input="rates",
                how="cross",
                maintain_order="left_right",
                left_on=[],
                right_on=[],
                suffix="_r",
                validate="m:1",
            ),
            1,
            "Only inner, left and full joins can validate",
        ),
        (
            step(
                "x",
                "join",
                input="rates",
                how="left",
                maintain_order="left_right",
                left_on=["r"],
                right_on=["r"],
                suffix="_r",
                validate="many",
            ),
            1,
            "Join validation",
        ),
        (
            step(
                "x",
                "with_column",
                name="p",
                expr={
                    "type": "function",
                    "fn": "slice",
                    "operand": col("pc"),
                    "args": [num(1.5), num(3)],
                },
            ),
            1,
            "whole number",
        ),
        (
            step(
                "x",
                "with_column",
                name="p",
                expr={"type": "function", "fn": "offset_by", "operand": col("d"), "args": [num(3)]},
            ),
            1,
            "must be text",
        ),
        (
            step(
                "x",
                "with_column",
                name="p",
                expr={"type": "function", "fn": "fill_null", "operand": col("d"), "args": [null()]},
            ),
            1,
            "must be a number, text or true/false",
        ),
        (
            step(
                "j",
                "join",
                input="rates",
                how="semi",
                maintain_order="left",
                left_on=["region"],
                right_on=["region"],
                suffix="_r",
                validate="m:1",
            ),
            1,
            "Only inner, left and full joins can validate",
        ),
        (
            step(
                "j",
                "join",
                input="rates",
                how="cross",
                maintain_order="left_right",
                left_on=[],
                right_on=[],
                suffix="_r",
                validate="m:1",
            ),
            1,
            "Only inner, left and full joins can validate",
        ),
    ],
)
def test_extended_vocabulary_rejects(kind_step: dict[str, Any], index: int, fragment: str) -> None:
    with pytest.raises(PolarsStepError) as exc_info:
        render_polars_steps([source(), kind_step], ["quotes", "rates"], start="input")
    assert exc_info.value.step_index == index
    assert fragment in exc_info.value.message


def test_extended_vocabulary_executes(tmp_path: Path) -> None:
    policies = _extended_frame(tmp_path)
    by_qid = [("qid", False)]
    steps = [
        source("policies"),
        window("rn", "row_number", None, ["region"], by_qid),
        window("rn_desc", "row_number", None, ["region"], [("qid", True)]),
        window("cs", "cum_sum", "premium", ["region"], by_qid),
        window("prev", "shift", "premium", ["region"], by_qid),
        window("rk", "dense_rank", "premium", ["region"], descending=True),
        window("rk_asc", "rank", "premium", ["region"]),
        window("ff", "forward_fill", "premium", ["region"], by_qid),
        window("bf", "backward_fill", "premium", ["region"], by_qid),
        window("tot", "sum", "premium", []),
        step(
            "w_q",
            "with_column",
            name="p25",
            expr={
                "type": "window",
                "agg": "quantile",
                "of": col("premium"),
                "over": ["region"],
                "quantile": 0.25,
            },
        ),
        step(
            "w0",
            "with_column",
            name="start_date",
            expr={
                "type": "operand",
                "operand": {"kind": "literal", "type": "date", "value": "2025-03-01"},
            },
        ),
        step(
            "w1",
            "with_column",
            name="wd",
            expr={"type": "function", "fn": "weekday", "operand": col("start_date"), "args": []},
        ),
        step(
            "w2",
            "with_column",
            name="renewal",
            expr={
                "type": "function",
                "fn": "offset_by",
                "operand": col("start_date"),
                "args": [text("1y")],
            },
        ),
        step(
            "w3",
            "with_column",
            name="_term",
            expr={"type": "binary", "left": col("renewal"), "op": "-", "right": col("start_date")},
        ),
        step(
            "w4",
            "with_column",
            name="term_days",
            expr={"type": "function", "fn": "total_days", "operand": col("_term"), "args": []},
        ),
        step(
            "w5",
            "with_column",
            name="key",
            expr={"type": "concat", "parts": [col("region"), col("qid")], "separator": "|"},
        ),
        step(
            "w5n",
            "with_column",
            name="null_key",
            expr={"type": "concat", "parts": [col("qid"), null()], "separator": "|"},
        ),
        step(
            "w6",
            "with_column",
            name="masked",
            expr={
                "type": "conditional",
                "branches": [
                    {
                        "match": "all",
                        "conditions": [cond("region", "eq", value=text("north"))],
                        "then": col("premium"),
                    }
                ],
                "otherwise": null(),
            },
        ),
        step(
            "w7",
            "with_column",
            name="tail",
            expr={
                "type": "function",
                "fn": "slice",
                "operand": col("qid"),
                "args": [num(-1), num(1)],
            },
        ),
        step(
            "w8",
            "with_column",
            name="part",
            expr={
                "type": "function",
                "fn": "split_part",
                "operand": col("key"),
                "args": [text("|"), num(1)],
            },
        ),
        step(
            "w8b",
            "with_column",
            name="oob",
            expr={
                "type": "function",
                "fn": "split_part",
                "operand": col("key"),
                "args": [text("|"), num(5)],
            },
        ),
        step(
            "w9",
            "with_column",
            name="digit",
            expr={
                "type": "function",
                "fn": "extract",
                "operand": col("qid"),
                "args": [text("([0-9]+)"), num(1)],
            },
        ),
        step(
            "w10",
            "with_column",
            name="digit_n",
            expr={
                "type": "function",
                "fn": "try_cast",
                "operand": col("digit"),
                "args": [text("Int32")],
            },
        ),
        step(
            "w10b",
            "with_column",
            name="tail_n",
            expr={
                "type": "function",
                "fn": "try_cast",
                "operand": col("tail"),
                "args": [text("Int32")],
            },
        ),
        step(
            "w11",
            "with_column",
            name="swapped",
            expr={
                "type": "function",
                "fn": "replace",
                "operand": col("key"),
                "args": [text("|"), text(".")],
            },
        ),
        step(
            "w12",
            "with_column",
            name="letters",
            expr={
                "type": "function",
                "fn": "replace_regex",
                "operand": col("qid"),
                "args": [text("[0-9]"), text("#")],
            },
        ),
        step(
            "f", "filter", match="all", conditions=[cond("qid", "matches", value=text("^c[0-9x]$"))]
        ),
    ]
    graph = PipelineGraph(
        nodes=[policies, _stepped("t", steps)],
        edges=[make_edge("policies", "t")],
    )
    result = execute_graph(graph, target_node_id="t", execution_context=_capped_context())["t"]
    assert result.status == "ok", result.error
    rows = result.preview
    assert [row["qid"] for row in rows] == ["c3", "c1", "c2", "c4", "c5", "c6", "cx"]
    by_id = {row["qid"]: row for row in rows}
    # north partition in qid order: c1 800, c3 50, c5 null, c6 800; south: c2 200, cx 100
    north = ["c1", "c3", "c5", "c6"]
    assert [by_id[i]["rn"] for i in north] == [1, 2, 3, 4]
    assert [by_id[i]["rn_desc"] for i in north] == [4, 3, 2, 1]
    assert [by_id[i]["rn"] for i in ("c2", "cx")] == [1, 2] and by_id["c4"]["rn"] == 1
    assert [by_id[i]["cs"] for i in north] == [800.0, 850.0, None, 1650.0]
    assert [by_id[i]["prev"] for i in north] == [None, 800.0, 50.0, None]
    assert [by_id[i]["rk"] for i in north] == [1, 2, None, 1]
    assert [by_id[i]["rk_asc"] for i in north] == [2, 1, None, 3]
    assert [by_id[i]["ff"] for i in north] == [800.0, 50.0, 50.0, 800.0]
    assert [by_id[i]["bf"] for i in north] == [800.0, 50.0, 800.0, 800.0]
    assert by_id["c4"]["ff"] is None and by_id["c4"]["bf"] is None
    assert all(row["tot"] == 1950.0 for row in rows)
    # linear interpolation between 50 and 800 (nearest would give one of them)
    assert all(by_id[i]["p25"] == 425.0 for i in north)
    assert all(row["term_days"] in (365, 366) for row in rows)
    assert by_id["c1"]["wd"] == 6  # 2025-03-01 is a Saturday
    assert by_id["c2"]["key"] == "south|c2" and by_id["c2"]["part"] == "c2"
    assert all(row["null_key"] is None for row in rows)
    assert all(row["oob"] is None for row in rows)
    assert by_id["c2"]["masked"] is None and by_id["c1"]["masked"] == 800.0
    assert by_id["c2"]["tail"] == "2" and by_id["cx"]["tail"] == "x"
    assert by_id["c2"]["swapped"] == "south.c2"
    assert by_id["c2"]["letters"] == "c#" and by_id["cx"]["letters"] == "cx"
    dtypes = {c.name: c.dtype for c in result.columns}
    assert dtypes["digit_n"] == "Int32" and by_id["c2"]["digit_n"] == 2
    assert by_id["cx"]["digit"] is None and by_id["cx"]["digit_n"] is None
    # a non-null string that is not a number becomes null instead of failing
    assert dtypes["tail_n"] == "Int32" and by_id["c2"]["tail_n"] == 2
    assert by_id["cx"]["tail"] == "x" and by_id["cx"]["tail_n"] is None

    summary_steps = [
        source("policies"),
        step(
            "g",
            "group_by",
            keys=[],
            aggregations=[
                {"column": "premium", "agg": "quantile", "name": "p25", "quantile": 0.25},
                {
                    "column": "premium",
                    "agg": "sum",
                    "name": "north_sum",
                    "where": {
                        "match": "all",
                        "conditions": [cond("region", "eq", value=text("north"))],
                    },
                },
                {
                    "column": "premium",
                    "agg": "len",
                    "name": "n_big",
                    "where": {
                        "match": "all",
                        "conditions": [cond("premium", "gt", value=num(100))],
                    },
                },
            ],
        ),
    ]
    grouped_steps = [
        source("policies"),
        step(
            "g",
            "group_by",
            keys=["region"],
            aggregations=[
                {
                    "column": "premium",
                    "agg": "len",
                    "name": "n_big",
                    "where": {
                        "match": "all",
                        "conditions": [cond("premium", "gt", value=num(100))],
                    },
                },
                {
                    "column": "premium",
                    "agg": "max",
                    "name": "small_max",
                    "where": {
                        "match": "all",
                        "conditions": [cond("premium", "le", value=num(100))],
                    },
                },
            ],
        ),
        step("srt", "sort", keys=[{"column": "region", "descending": False}], nulls_last=False),
    ]
    graph = PipelineGraph(
        nodes=[policies, _stepped("s", summary_steps), _stepped("r", grouped_steps)],
        edges=[make_edge("policies", "s"), make_edge("policies", "r")],
    )
    summary = execute_graph(graph, target_node_id="s", execution_context=_capped_context())["s"]
    assert summary.status == "ok", summary.error
    # premiums 50, 100, 200, 800, 800: 25th percentile interpolates between 50 and 100
    assert summary.preview == [{"p25": 100.0, "north_sum": 1650.0, "n_big": 3}]
    grouped = execute_graph(graph, target_node_id="r", execution_context=_capped_context())["r"]
    assert grouped.status == "ok", grouped.error
    assert grouped.preview == [
        {"region": "east", "n_big": 0, "small_max": None},
        {"region": "north", "n_big": 2, "small_max": 50.0},
        {"region": "south", "n_big": 1, "small_max": 100.0},
    ]

    unique_steps = [
        source("policies"),
        step("u", "unique", columns=["region"], keep="none"),
    ]
    graph = PipelineGraph(
        nodes=[policies, _stepped("u", unique_steps)], edges=[make_edge("policies", "u")]
    )
    unique = execute_graph(graph, target_node_id="u", execution_context=_capped_context())["u"]
    assert unique.status == "ok", unique.error
    assert [row["qid"] for row in unique.preview] == ["c4"]
