"""Low-code Polars steps: expressions, reshaping, windows and constants.

Nested expressions, pivots and dtype selectors, window row filters and values, operand arguments,
division, branches, sums across columns and global constants as step operands render to the
expected code and evaluate to the expected frames.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from fastapi.testclient import TestClient

from haute._polars_steps import (
    MAX_EXPR_DEPTH,
    PolarsStepError,
    render_polars_steps,
)
from haute._types import (
    GlobalConstant,
    PipelineGraph,
)
from haute.errors import RequestRefusedError
from haute.executor import execute_graph
from tests import _polars_steps_helpers
from tests._polars_steps_helpers import (
    _capped_context,
    _extended_frame,
    _frames,
    _project_graph,
    _ready_source,
    _render_body,
    _run_steps,
    _save,
    _stepped,
    binary,
    branches,
    col,
    cond,
    fn,
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

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")

# Fixtures shared with the other modules split from one suite.
project_root = _polars_steps_helpers.project_root


# ---------------------------------------------------------------------------
# Nested expressions
# ---------------------------------------------------------------------------


def ex(expr: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "expr", "expr": expr}


@pytest.mark.parametrize(
    ("kind_step", "expected"),
    [
        (
            step(
                "x",
                "with_column",
                name="rate",
                expr=fn(
                    "round",
                    ex(binary(ex(binary(num(1000), "*", col("premium"))), "/", col("sum_insured"))),
                    num(3),
                ),
            ),
            "df = df.with_columns(\n"
            '    (pl.lit(1000) * pl.col("premium") / pl.col("sum_insured"))\n'
            "    .round(3)\n"
            '    .alias("rate")\n'
            ")",
        ),
        (
            step(
                "x",
                "with_column",
                name="dev",
                expr=binary(
                    col("premium"),
                    "-",
                    ex({"type": "window", "agg": "mean", "of": col("premium"), "over": ["region"]}),
                ),
            ),
            "df = df.with_columns(\n"
            '    (pl.col("premium") - pl.col("premium").mean().over(["region"])).alias("dev")\n'
            ")",
        ),
        (
            step(
                "x",
                "with_column",
                name="size",
                expr=fn(
                    "abs",
                    ex(
                        {
                            "type": "conditional",
                            "branches": [
                                {
                                    "match": "all",
                                    "conditions": [cond("region", "eq", value=text("north"))],
                                    "then": col("premium"),
                                }
                            ],
                            "otherwise": ex(binary(col("premium"), "*", num(-1))),
                        }
                    ),
                ),
            ),
            "df = df.with_columns(\n"
            '    pl.when(pl.col("region") == "north")\n'
            '    .then(pl.col("premium"))\n'
            '    .otherwise(pl.col("premium") * -1)\n'
            "    .abs()\n"
            '    .alias("size")\n'
            ")",
        ),
        (
            step(
                "x",
                "filter",
                match="all",
                conditions=[
                    cond("premium", "gt", value=ex(binary(col("sum_insured"), "*", num(0.01))))
                ],
            ),
            'df = df.filter(pl.col("premium") > pl.col("sum_insured") * 0.01)',
        ),
        (
            step(
                "x",
                "with_column",
                name="key",
                expr={
                    "type": "concat",
                    "parts": [ex(fn("upper", col("region"))), col("north")],
                    "separator": "-",
                },
            ),
            "df = df.with_columns(\n"
            "    pl.concat_str(\n"
            '        [pl.col("region").str.to_uppercase(), pl.col("north")],\n'
            '        separator="-",\n'
            '    ).alias("key")\n'
            ")",
        ),
        (
            step(
                "x",
                "fill_null",
                columns=["premium"],
                fill={"kind": "value", "value": ex(binary(col("sum_insured"), "*", num(0.02)))},
            ),
            'df = df.with_columns(pl.col("premium").fill_null(pl.col("sum_insured") * 0.02))',
        ),
        (
            step(
                "x",
                "with_column",
                name="v",
                expr={"type": "operand", "operand": ex({"type": "operand", "operand": num(2)})},
            ),
            'df = df.with_columns(pl.lit(2).alias("v"))',
        ),
    ],
)
def test_nested_expressions_render(kind_step: dict[str, Any], expected: str) -> None:
    rendered = render_polars_steps([source(), kind_step], ["quotes"], start="input")
    assert step_text(rendered, 1) == expected


def _nested(depth: int) -> dict[str, Any]:
    """*depth* formulas nested on the left, alternating ``+`` and ``-``.

    A chain of one operator would count as one level, so the operators alternate
    and each formula is a level of its own.
    """
    operand: dict[str, Any] = num(1)
    for level in range(depth):
        operand = ex(binary(operand, "+-"[level % 2], num(1)))
    return {"type": "operand", "operand": operand}


def _chain(terms: int, op: str = "+", *, side: str = "left") -> dict[str, Any]:
    """A formula of *terms* columns joined by *op*, nested on the left or the right."""
    expr = binary(col("c0"), op, col("c1"))
    for index in range(2, terms):
        nested = ex(expr)
        expr = (
            binary(nested, op, col(f"c{index}"))
            if side == "left"
            else binary(col(f"c{index}"), op, nested)
        )
    return expr


@pytest.mark.parametrize("op", ["+", "-", "*", "/", "//", "%"])
def test_a_chain_of_one_operator_is_one_level_however_long(op: str) -> None:
    steps = [source(), step("x", "with_column", name="total", expr=_chain(20, op))]
    rendered = render_polars_steps(steps, ["quotes"], start="input")
    # Twenty terms, written flat: the chain needs no brackets, and stays one tree.
    assert rendered.code.count(f" {op} ") == 19
    assert "(pl.col" not in rendered.code
    # One level, so the chain still fits under eleven more levels of nesting.
    deep: dict[str, Any] = _chain(20, op)
    for _ in range(MAX_EXPR_DEPTH - 1):
        deep = {"type": "function", "fn": "abs", "operand": ex(deep), "args": []}
    render_polars_steps([source(), step("x", "with_column", name="t", expr=deep)], start="input")


@pytest.mark.parametrize(
    ("expr", "why"),
    [
        (_nested(20)["operand"]["expr"], "a 20-deep mix of + and -"),
        (_chain(14, "+", side="right"), "a + (b + (c + ...)) keeps every group"),
        (_chain(14, "**"), "** chains are bracketed, so each counts a level"),
    ],
)
def test_nesting_that_is_not_one_chain_still_counts_each_level(
    expr: dict[str, Any], why: str
) -> None:
    with pytest.raises(PolarsStepError, match=f"nest more than {MAX_EXPR_DEPTH} levels"):
        render_polars_steps(
            [source(), step("x", "with_column", name="t", expr=expr)], start="input"
        )


@pytest.mark.parametrize(
    ("kind_step", "fragment"),
    [
        (step("x", "with_column", name="d", expr=_nested(12)), "nest more than 12 levels"),
        (
            step(
                "x",
                "filter",
                match="all",
                conditions=[cond("region", "is_in", values=[ex(binary(num(1), "+", num(1)))])],
            ),
            "must be plain values",
        ),
        (step("x", "variable", name="v", value=ex(binary(num(1), "+", num(1)))), "plain value"),
        (
            step(
                "x",
                "with_column",
                name="r",
                expr=fn("round", col("premium"), ex(binary(num(1), "+", num(1)))),
            ),
            "must be a plain value",
        ),
        (
            step(
                "x", "with_column", name="r", expr={"type": "operand", "operand": {"kind": "expr"}}
            ),
            "expression must be an object",
        ),
        (
            step(
                "x",
                "with_column",
                name="r",
                expr={"type": "operand", "operand": {"kind": "expr", "expr": {"type": "nope"}}},
            ),
            "Unknown expression type",
        ),
    ],
)
def test_nested_expressions_reject(kind_step: dict[str, Any], fragment: str) -> None:
    with pytest.raises(PolarsStepError) as info:
        render_polars_steps([source(), kind_step], ["quotes"], start="input")
    assert info.value.step_index == 1
    assert fragment in info.value.message


def test_formula_text_annotation_is_kept_but_never_rendered() -> None:
    expr = {**binary(col("a"), "+", col("b")), "text": "(a + b)"}
    rendered = render_polars_steps(
        [source(), step("x", "with_column", name="v", expr=expr)], ["quotes"], start="input"
    )
    assert (
        rendered.code.splitlines()[1]
        == 'df = df.with_columns((pl.col("a") + pl.col("b")).alias("v"))'
    )
    fn_expr = {**fn("abs", ex(binary(col("a"), "-", col("b")))), "text": "abs((a - b))"}
    rendered = render_polars_steps(
        [source(), step("x", "with_column", name="v", expr=fn_expr)], ["quotes"], start="input"
    )
    assert (
        rendered.code.splitlines()[1]
        == 'df = df.with_columns((pl.col("a") - pl.col("b")).abs().alias("v"))'
    )
    bare = {"type": "operand", "operand": col("a"), "text": "a"}
    rendered = render_polars_steps(
        [source(), step("x", "with_column", name="v", expr=bare)], ["quotes"], start="input"
    )
    assert rendered.code.splitlines()[1] == 'df = df.with_columns(pl.col("a").alias("v"))'
    with pytest.raises(PolarsStepError, match="formula text must be a string"):
        render_polars_steps(
            [
                source(),
                step(
                    "x",
                    "with_column",
                    name="v",
                    expr={**binary(col("a"), "+", col("b")), "text": 3},
                ),
            ],
            ["quotes"],
            start="input",
        )


def test_nesting_depth_counts_from_the_step_expression() -> None:
    # Depth 11 below the top-level expression is the deepest allowed.
    render_polars_steps(
        [source(), step("x", "with_column", name="d", expr=_nested(11))], ["quotes"], start="input"
    )


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        # a left operand binds first: brackets only when it is weaker
        (
            binary(ex(binary(col("a"), "+", col("b"))), "*", col("c")),
            '(pl.col("a") + pl.col("b")) * pl.col("c")',
        ),
        (
            binary(ex(binary(col("a"), "*", col("b"))), "+", col("c")),
            'pl.col("a") * pl.col("b") + pl.col("c")',
        ),
        (
            binary(ex(binary(col("a"), "+", col("b"))), "-", col("c")),
            'pl.col("a") + pl.col("b") - pl.col("c")',
        ),
        (
            binary(ex(binary(col("a"), "/", col("b"))), "*", col("c")),
            'pl.col("a") / pl.col("b") * pl.col("c")',
        ),
        # a right operand keeps its brackets: evaluation order must not change
        (
            binary(col("a"), "-", ex(binary(col("b"), "+", col("c")))),
            'pl.col("a") - (pl.col("b") + pl.col("c"))',
        ),
        (
            binary(col("a"), "*", ex(binary(col("b"), "//", col("c")))),
            'pl.col("a") * (pl.col("b") // pl.col("c"))',
        ),
        (
            binary(col("a"), "+", ex(binary(col("b"), "+", col("c")))),
            'pl.col("a") + (pl.col("b") + pl.col("c"))',
        ),
        # power binds tightest: brackets only around a weaker base
        (
            binary(ex(binary(col("a"), "**", num(2))), "*", col("c")),
            'pl.col("a") ** 2 * pl.col("c")',
        ),
        (
            binary(ex(binary(col("a"), "*", col("b"))), "**", num(2)),
            '(pl.col("a") * pl.col("b")) ** 2',
        ),
        # a long left-leaning chain reads flat
        (
            binary(ex(binary(ex(binary(col("a"), "+", col("b"))), "+", col("c"))), "+", col("d")),
            'pl.col("a") + pl.col("b") + pl.col("c") + pl.col("d")',
        ),
    ],
)
def test_nested_formulas_are_bracketed_only_where_evaluation_needs_it(
    expr: dict[str, Any], expected: str
) -> None:
    rendered = render_polars_steps(
        [source(), step("x", "with_column", name="v", expr=expr)], ["quotes"], start="input"
    )
    # Line breaks are layout; where the brackets fall is what this test is about.
    statement = step_text(rendered, 1).replace("\n    ", "").replace("\n", "")
    assert statement == f'df = df.with_columns(({expected}).alias("v"))'


def test_bracketing_preserves_values_on_execution(tmp_path: Path) -> None:
    policies = _extended_frame(tmp_path)
    steps = [
        source("policies"),
        step("f", "filter", match="all", conditions=[cond("premium", "is_not_null")]),
        step(
            "w",
            "with_column",
            name="v",
            # (premium + 1) * 2 - premium // 3 = premium * 2 + 2 - premium // 3
            expr=binary(
                ex(binary(ex(binary(col("premium"), "+", num(1))), "*", num(2))),
                "-",
                ex(binary(col("premium"), "//", num(3))),
            ),
        ),
    ]
    graph = PipelineGraph(
        nodes=[policies, _stepped("t", steps)], edges=[make_edge("policies", "t")]
    )
    result = execute_graph(graph, target_node_id="t", execution_context=_capped_context())["t"]
    assert result.status == "ok", result.error
    assert [row["v"] for row in result.preview] == [
        (p + 1) * 2 - p // 3 for p in (50.0, 800.0, 200.0, 800.0, 100.0)
    ]


def test_nested_expressions_execute(tmp_path: Path) -> None:
    policies = _extended_frame(tmp_path)
    steps = [
        source("policies"),
        step(
            "a",
            "with_column",
            name="dev",
            expr=binary(
                col("premium"),
                "-",
                ex({"type": "window", "agg": "mean", "of": col("premium"), "over": ["region"]}),
            ),
        ),
        step(
            "b",
            "with_column",
            name="per_mille",
            expr=fn(
                "round",
                ex(binary(ex(binary(num(1000), "*", col("premium"))), "/", num(3))),
                num(1),
            ),
        ),
        step(
            "c",
            "with_column",
            name="signed",
            expr=fn(
                "abs",
                ex(
                    {
                        "type": "conditional",
                        "branches": [
                            {
                                "match": "all",
                                "conditions": [cond("region", "eq", value=text("north"))],
                                "then": col("premium"),
                            }
                        ],
                        "otherwise": ex(binary(col("premium"), "*", num(-1))),
                    }
                ),
            ),
        ),
        step(
            "d",
            "filter",
            match="all",
            conditions=[cond("premium", "ge", value=ex(binary(col("dev"), "+", num(100))))],
        ),
    ]
    graph = PipelineGraph(
        nodes=[policies, _stepped("t", steps)], edges=[make_edge("policies", "t")]
    )
    result = execute_graph(graph, target_node_id="t", execution_context=_capped_context())["t"]
    assert result.status == "ok", result.error
    by_id = {row["qid"]: row for row in result.preview}
    # north mean is 550 (800, 50, 800); rows keep premium >= dev + 100
    assert set(by_id) == {"c1", "c2", "c3", "c6", "cx"}
    assert by_id["c1"]["dev"] == 250.0 and by_id["c1"]["per_mille"] == 266666.7
    assert by_id["c2"]["signed"] == 200.0 and by_id["c1"]["signed"] == 800.0


# ---------------------------------------------------------------------------
# Reshaping and dtype selectors
# ---------------------------------------------------------------------------


def pivot_column(value: dict[str, Any], name: str) -> dict[str, Any]:
    return {"value": value, "name": name}


@pytest.mark.parametrize(
    ("kind_step", "expected"),
    [
        (
            step("x", "select", columns=["region"], dtypes=["Float64", "Int64"]),
            "df = df.select(\n"
            '    "region",\n'
            '    pl.col(pl.Float64).exclude("region"),\n'
            '    pl.col(pl.Int64).exclude("region"),\n'
            ")",
        ),
        (
            step("x", "select", columns=[], dtypes=["Float64"]),
            "df = df.select(pl.col(pl.Float64))",
        ),
        (step("x", "select", columns=["a", "b"]), 'df = df.select("a", "b")'),
        (
            step("x", "drop", columns=["region"], dtypes=["String"]),
            'df = df.drop("region", pl.col(pl.String).exclude("region"))',
        ),
        (
            step("x", "drop", columns=[], dtypes=["String", "Date"]),
            "df = df.drop(pl.col(pl.String), pl.col(pl.Date))",
        ),
        (
            step(
                "x",
                "group_by",
                keys=["region"],
                aggregations=[
                    {"column": "", "agg": "len", "name": "n"},
                    {"dtype": "Float64", "agg": "mean", "suffix": "_mean"},
                    {"dtype": "Int64", "agg": "quantile", "quantile": 0.5, "suffix": "_q"},
                ],
            ),
            'df = df.group_by("region", maintain_order=True).agg(\n'
            '    pl.len().alias("n"),\n'
            '    pl.col(pl.Float64).mean().name.suffix("_mean"),\n'
            '    pl.col(pl.Int64).quantile(0.5, interpolation="linear").name.suffix("_q"),\n'
            ")",
        ),
        (
            step(
                "x",
                "pivot",
                index=["region"],
                on="channel",
                columns=[pivot_column(text("web"), "web"), pivot_column(text("phone"), "by_phone")],
                values="premium",
                agg="mean",
            ),
            'df = df.group_by("region", maintain_order=True).agg(\n'
            '    pl.col("premium").filter(pl.col("channel") == "web").mean().alias("web"),\n'
            '    pl.col("premium").filter(pl.col("channel") == "phone").mean().alias("by_phone"),\n'
            ")",
        ),
        (
            step(
                "x",
                "pivot",
                index=["region", "fuel"],
                on="year",
                columns=[pivot_column(num(2024), "y2024")],
                values="premium",
                agg="len",
            ),
            'df = df.group_by("region", "fuel", maintain_order=True).agg(\n'
            '    pl.col("premium").filter(pl.col("year") == 2024).len().alias("y2024")\n'
            ")",
        ),
        (
            step(
                "x",
                "unpivot",
                on=["premium", "sum_insured"],
                index=["quote_id"],
                variable_name="measure",
                value_name="value",
            ),
            "df = df.unpivot(\n"
            '    on=["premium", "sum_insured"],\n'
            '    index=["quote_id"],\n'
            '    variable_name="measure",\n'
            '    value_name="value",\n'
            ")",
        ),
        (
            step("x", "unpivot", on=["premium"], index=[], variable_name="m", value_name="v"),
            'df = df.unpivot(on=["premium"], index=[], variable_name="m", value_name="v")',
        ),
    ],
)
def test_reshaping_and_dtype_selectors_render(kind_step: dict[str, Any], expected: str) -> None:
    rendered = render_polars_steps([source(), kind_step], ["quotes"], start="input")
    assert step_text(rendered, 1) == expected


@pytest.mark.parametrize(
    ("kind_step", "fragment"),
    [
        (step("x", "select", columns=[]), "at least one column or column type"),
        (step("x", "drop", columns=[], dtypes=[]), "at least one column or column type"),
        (step("x", "select", columns=["a"], dtypes=["Float64", "Float64"]), "must not repeat"),
        (step("x", "select", columns=["a"], dtypes=["Enum"]), "must be one of"),
        (
            step(
                "x",
                "group_by",
                keys=[],
                aggregations=[{"dtype": "Float64", "agg": "mean", "name": "m"}],
            ),
            "takes no name",
        ),
        (
            step(
                "x",
                "group_by",
                keys=[],
                aggregations=[
                    {
                        "dtype": "Float64",
                        "agg": "mean",
                        "suffix": "_m",
                        "where": {"match": "all", "conditions": [cond("a", "is_null")]},
                    }
                ],
            ),
            "takes no where",
        ),
        (
            step(
                "x",
                "group_by",
                keys=[],
                aggregations=[{"dtype": "Float64", "agg": "len", "suffix": "_n"}],
            ),
            "counts rows",
        ),
        (
            step(
                "x",
                "group_by",
                keys=[],
                aggregations=[{"column": "a", "agg": "sum", "name": "s", "suffix": "_s"}],
            ),
            "takes a suffix only",
        ),
        (
            step(
                "x",
                "group_by",
                keys=["g"],
                aggregations=[
                    {
                        "column": "a",
                        "agg": "sum",
                        "name": "s",
                        "where": {
                            "match": "all",
                            "conditions": [
                                cond("a", "gt", value=ex(binary(col("a"), "*", num(2)))),
                            ],
                        },
                    }
                ],
            ),
            "plain value, column or variable here",
        ),
        (
            step(
                "x",
                "pivot",
                index=[],
                on="c",
                columns=[pivot_column(text("p"), "p")],
                values="a",
                agg="sum",
            ),
            "at least one column",
        ),
        (
            step("x", "pivot", index=["g"], on="c", columns=[], values="a", agg="sum"),
            "at least one pivot column",
        ),
        (
            step(
                "x",
                "pivot",
                index=["g"],
                on="c",
                columns=[pivot_column(text("p"), "p"), pivot_column(text("q"), "p")],
                values="a",
                agg="sum",
            ),
            "used more than once",
        ),
        (
            step(
                "x",
                "pivot",
                index=["g"],
                on="c",
                columns=[pivot_column(text("p"), "g")],
                values="a",
                agg="sum",
            ),
            "used more than once",
        ),
        (
            step(
                "x",
                "pivot",
                index=["g"],
                on="c",
                columns=[pivot_column(text("p"), "p"), pivot_column(text("p"), "again")],
                values="a",
                agg="sum",
            ),
            "repeats the value",
        ),
        (
            step(
                "x",
                "pivot",
                index=["g"],
                on="c",
                columns=[pivot_column(text("p"), "p"), pivot_column(num(1), "one")],
                values="a",
                agg="sum",
            ),
            "same type",
        ),
        (
            step(
                "x",
                "pivot",
                index=["g"],
                on="c",
                columns=[pivot_column(null(), "n")],
                values="a",
                agg="sum",
            ),
            "cannot be null",
        ),
        (
            step(
                "x",
                "pivot",
                index=["g"],
                on="c",
                columns=[pivot_column(col("c"), "n")],
                values="a",
                agg="sum",
            ),
            "plain value",
        ),
        (
            step(
                "x",
                "pivot",
                index=["g"],
                on="c",
                columns=[pivot_column(text("p"), "p")],
                values="a",
                agg="std",
            ),
            "must be one of",
        ),
        (
            step("x", "unpivot", on=[], index=[], variable_name="m", value_name="v"),
            "at least one column",
        ),
        (
            step("x", "unpivot", on=["a", "g"], index=["g"], variable_name="m", value_name="v"),
            "both unpivoted and kept as index",
        ),
        (
            step("x", "unpivot", on=["a"], index=["g"], variable_name="v", value_name="v"),
            "different names",
        ),
        (
            step("x", "unpivot", on=["a"], index=["g"], variable_name="g", value_name="v"),
            "already an index column",
        ),
        (
            step("x", "unpivot", on=["*"], index=[], variable_name="m", value_name="v"),
            "not the pattern '*'",
        ),
        (
            step("x", "unpivot", on=["^measure_.*$"], index=[], variable_name="m", value_name="v"),
            "not the pattern",
        ),
        (step("x", "select", columns=["*"]), "not the pattern '*'"),
        (
            step("x", "sort", keys=[{"column": "*", "descending": False}], nulls_last=False),
            "not the pattern '*'",
        ),
        (
            window("rn", "row_number", None, ["g"], [("^measure_.*$", False)]),
            "not the pattern",
        ),
        (
            step(
                "x",
                "pivot",
                index=["^g$"],
                on="c",
                columns=[pivot_column(text("p"), "p")],
                values="a",
                agg="sum",
            ),
            "not the pattern",
        ),
        (
            step(
                "x",
                "pivot",
                index=["g"],
                on="*",
                columns=[pivot_column(text("p"), "p")],
                values="a",
                agg="sum",
            ),
            "not the pattern '*'",
        ),
        (
            step(
                "x",
                "pivot",
                index=["g"],
                on="c",
                columns=[pivot_column(num(1), "one"), pivot_column(num(1.0), "also_one")],
                values="a",
                agg="sum",
            ),
            "repeats the value",
        ),
        (
            step(
                "x",
                "pivot",
                index=["g"],
                on="c",
                columns=[pivot_column(num(0.0), "zero"), pivot_column(num(-0.0), "neg_zero")],
                values="a",
                agg="sum",
            ),
            "repeats the value",
        ),
    ],
)
def test_reshaping_and_dtype_selectors_reject(kind_step: dict[str, Any], fragment: str) -> None:
    with pytest.raises(PolarsStepError) as info:
        render_polars_steps([source(), kind_step], ["quotes"], start="input")
    assert info.value.step_index == 1
    assert fragment in info.value.message


def test_pivot_values_are_compared_exactly() -> None:
    big = 2**53
    steps = [
        source("rows"),
        step(
            "p",
            "pivot",
            index=["g"],
            on="c",
            columns=[pivot_column(num(big), "a"), pivot_column(num(big + 1), "b")],
            values="a",
            agg="sum",
        ),
    ]
    line = step_text(render_polars_steps(steps, ["rows"], start="input"), 1)
    assert f"== {big})" in line and f"== {big + 1})" in line


def _pivot_frame() -> pl.LazyFrame:
    return pl.DataFrame(
        {
            "g": ["x", "x", "y", "y", "z"],
            "c": ["p", "q", "p", "p", "q"],
            "a": [1.0, None, 3.0, 4.0, None],
        }
    ).lazy()


@pytest.mark.parametrize(
    "agg", ["sum", "mean", "min", "max", "median", "first", "last", "count", "len"]
)
def test_pivot_lowering_matches_native_pivot_cell_for_cell(agg: str) -> None:
    """Every offered aggregate matches ``LazyFrame.pivot`` (row order aside)."""
    native_aggregate = {"count": pl.element().count(), "len": pl.element().len()}.get(
        agg, getattr(pl.element(), agg)()
    )
    columns = [pivot_column(text(v), v) for v in ("p", "q", "absent")]
    steps = [
        source("rows"),
        step("p", "pivot", index=["g"], on="c", columns=columns, values="a", agg=agg),
    ]
    code = render_polars_steps(steps, ["rows"], start="input").code
    for frame in (_pivot_frame(), _pivot_frame().head(0)):
        namespace: dict[str, object] = {"pl": pl, "rows": frame}
        exec(code, namespace, namespace)  # noqa: S102 - generated step code under test
        lowered = namespace["df"]
        assert isinstance(lowered, pl.LazyFrame)
        native = frame.pivot(
            on="c",
            on_columns=["p", "q", "absent"],
            index="g",
            values="a",
            aggregate_function=native_aggregate,
        ).collect()
        assert lowered.collect().sort("g").equals(native.sort("g")), agg


def test_reshaping_and_dtype_selectors_execute(tmp_path: Path) -> None:
    policies = _extended_frame(tmp_path)
    steps = [
        source("policies"),
        step(
            "w",
            "with_column",
            name="digit",
            expr=fn("extract", col("qid"), text("([0-9]+)"), num(1)),
        ),
        step("c", "cast", strict=True, casts=[{"column": "digit", "dtype": "Int64"}]),
        step("sel", "select", columns=["qid"], dtypes=["Float64", "Int64"]),
        step(
            "u",
            "unpivot",
            on=["premium", "digit"],
            index=["qid"],
            variable_name="measure",
            value_name="value",
        ),
        step(
            "o",
            "sort",
            keys=[
                {"column": "qid", "descending": False},
                {"column": "measure", "descending": False},
            ],
            nulls_last=False,
        ),
    ]
    graph = PipelineGraph(
        nodes=[policies, _stepped("t", steps)], edges=[make_edge("policies", "t")]
    )
    result = execute_graph(graph, target_node_id="t", execution_context=_capped_context())["t"]
    assert result.status == "ok", result.error
    assert [c.name for c in result.columns] == ["qid", "measure", "value"]
    assert {c.name: c.dtype for c in result.columns}["value"] == "Float64"
    rows = [(r["qid"], r["measure"], r["value"]) for r in result.preview]
    assert rows[:4] == [
        ("c1", "digit", 1.0),
        ("c1", "premium", 800.0),
        ("c2", "digit", 2.0),
        ("c2", "premium", 200.0),
    ]
    assert len(rows) == 14

    summary_steps = [
        source("policies"),
        step("d", "drop", columns=["qid"], dtypes=["String"]),
        step(
            "g",
            "group_by",
            keys=[],
            aggregations=[
                {"column": "", "agg": "len", "name": "n"},
                {"dtype": "Float64", "agg": "mean", "suffix": "_mean"},
            ],
        ),
    ]
    graph = PipelineGraph(
        nodes=[policies, _stepped("s", summary_steps)], edges=[make_edge("policies", "s")]
    )
    summary = execute_graph(graph, target_node_id="s", execution_context=_capped_context())["s"]
    assert summary.status == "ok", summary.error
    assert summary.preview == [{"n": 7, "premium_mean": 390.0}]

    pivot_steps = [
        source("policies"),
        step(
            "p",
            "pivot",
            index=["region"],
            on="qid",
            columns=[pivot_column(text("c1"), "c1"), pivot_column(text("c2"), "c2")],
            values="premium",
            agg="sum",
        ),
    ]
    graph = PipelineGraph(
        nodes=[policies, _stepped("p", pivot_steps)], edges=[make_edge("policies", "p")]
    )
    pivoted = execute_graph(graph, target_node_id="p", execution_context=_capped_context())["p"]
    assert pivoted.status == "ok", pivoted.error
    assert pivoted.preview == [
        {"region": "north", "c1": 800.0, "c2": 0.0},
        {"region": "south", "c1": 0.0, "c2": 200.0},
        {"region": "east", "c1": 0.0, "c2": 0.0},
    ]


def test_generated_reshaping_code_stays_inside_the_lineage_model() -> None:
    """The renderer's shapes are the ones the classifier proves or bounds."""
    from haute._column_lineage import analyze_polars_cardinality, analyze_polars_lineage

    schema = {"rows": frozenset({"g", "a", "i", "c"})}
    dtypes = {"rows": {"g": pl.String(), "a": pl.Float64(), "i": pl.Int64(), "c": pl.String()}}

    def code(*kind_steps: dict[str, Any]) -> str:
        return render_polars_steps([source("rows"), *kind_steps], ["rows"], start="input").code

    typed_select = code(step("sel", "select", columns=["g"], dtypes=["Float64", "Int64"]))
    proven = analyze_polars_lineage(typed_select, schema, input_dtypes=dtypes)
    assert proven.supported and proven.exact_output_columns == {"g", "a", "i"}
    assert proven.demands_by_input == {"rows": frozenset({"g", "a", "i"})}
    without_dtypes = analyze_polars_lineage(typed_select, schema)
    assert not without_dtypes.supported and without_dtypes.reason == "selector_dtypes_unknown"

    typed_drop = code(step("d", "drop", columns=["g"], dtypes=["String"]))
    dropped = analyze_polars_lineage(typed_drop, schema, input_dtypes=dtypes)
    assert dropped.supported and dropped.exact_output_columns == {"a", "i"}

    typed_agg = code(
        step(
            "g",
            "group_by",
            keys=["g"],
            aggregations=[{"dtype": "Float64", "agg": "mean", "suffix": "_mean"}],
        )
    )
    aggregated = analyze_polars_lineage(typed_agg, schema, input_dtypes=dtypes)
    assert aggregated.supported and aggregated.exact_output_columns == {"g", "a_mean"}
    assert aggregated.demands_by_input == {"rows": frozenset({"g", "a"})}
    # A computed column has no propagated dtype, so a later dtype selector fails closed.
    after_computed = code(
        step("w", "with_column", name="b", expr=binary(col("a"), "*", num(2))),
        step("sel", "select", columns=[], dtypes=["Float64"]),
    )
    assert (
        analyze_polars_lineage(after_computed, schema, input_dtypes=dtypes).reason
        == "selector_dtypes_unknown"
    )

    pivot_code = code(
        step(
            "p",
            "pivot",
            index=["g"],
            on="c",
            columns=[pivot_column(text("p"), "p")],
            values="a",
            agg="sum",
        )
    )
    pivot_lineage = analyze_polars_lineage(pivot_code, schema)
    assert pivot_lineage.supported and pivot_lineage.exact_output_columns == {"g", "p"}
    assert pivot_lineage.demands_by_input == {"rows": frozenset({"g", "c", "a"})}
    assert analyze_polars_cardinality(pivot_code, {"rows": 100}).output_upper_bound == 100

    unpivot_code = code(
        step("u", "unpivot", on=["a", "i"], index=["g"], variable_name="m", value_name="v"),
        step("f", "filter", match="all", conditions=[cond("v", "gt", value=num(0))]),
    )
    unpivoted = analyze_polars_lineage(unpivot_code, schema)
    assert unpivoted.supported and unpivoted.exact_output_columns == {"g", "m", "v"}
    bound = analyze_polars_cardinality(unpivot_code, {"rows": 100})
    assert bound.supported and bound.output_upper_bound == 200

    nested_window = code(
        step(
            "w",
            "with_column",
            name="dev",
            expr=binary(
                col("a"), "-", ex({"type": "window", "agg": "mean", "of": col("a"), "over": ["g"]})
            ),
        ),
        step("sel", "select", columns=["dev"]),
    )
    windowed = analyze_polars_lineage(nested_window, schema, demanded_output=["dev"])
    assert windowed.supported and windowed.demands_by_input == {"rows": frozenset({"a", "g"})}


# ---------------------------------------------------------------------------
# Window row filters and values, operand arguments, divide, branches, sum across
# ---------------------------------------------------------------------------

_WEB = [cond("channel", "eq", value=text("web"))]


_CAP = {"kind": "constant", "name": "cap"}


_NEGATIVE_CAP = ex(binary(num(0), "-", col("cap")))


def branch(
    conditions: list[dict[str, Any]], then: dict[str, Any], match: str = "all"
) -> dict[str, Any]:
    return {"match": match, "conditions": conditions, "then": then}


def sum_across(*parts: dict[str, Any]) -> dict[str, Any]:
    return {"type": "sum_horizontal", "parts": list(parts)}


#: The IRPM clip as an if-then: the cap above, its negative below, the value between.
_CAPPED = branches(
    branch([cond("irpm", "gt", value=col("cap"))], col("cap")),
    branch([cond("irpm", "lt", value=_NEGATIVE_CAP)], _NEGATIVE_CAP),
    otherwise=col("irpm"),
)


@pytest.mark.parametrize(
    ("kind_step", "expected"),
    [
        (
            window("web_total", "sum", "premium", ["region"], where=_WEB),
            "df = df.with_columns(\n"
            '    pl.col("premium")\n'
            '    .filter(pl.col("channel") == "web")\n'
            "    .sum()\n"
            '    .over(["region"])\n'
            '    .alias("web_total")\n'
            ")",
        ),
        (
            window("web_quotes", "len", None, ["region"], where=_WEB),
            "df = df.with_columns(\n"
            '    (pl.col("channel") == "web").sum().over(["region"]).alias("web_quotes")\n'
            ")",
        ),
        (
            window("first_web", "first", "premium", ["region"], [("qid", False)], where=_WEB),
            "df = df.with_columns(\n"
            '    pl.col("premium")\n'
            '    .filter(pl.col("channel") == "web")\n'
            "    .first()\n"
            '    .over(["region"], order_by=["qid"], descending=False)\n'
            '    .alias("first_web")\n'
            ")",
        ),
        (
            window("loaded", "sum", ex(binary(col("lim"), "*", col("load"))), ["q"], where=_WEB),
            "df = df.with_columns(\n"
            '    (pl.col("lim") * pl.col("load"))\n'
            '    .filter(pl.col("channel") == "web")\n'
            "    .sum()\n"
            '    .over(["q"])\n'
            '    .alias("loaded")\n'
            ")",
        ),
        (
            window("run", "cum_sum", ex(fn("abs", col("x"))), ["q"], [("qid", False)]),
            "df = df.with_columns(\n"
            '    pl.col("x")\n'
            "    .abs()\n"
            "    .cum_sum()\n"
            '    .over(["q"], order_by=["qid"], descending=False)\n'
            '    .alias("run")\n'
            ")",
        ),
        (
            step(
                "c",
                "with_column",
                name="t",
                expr=fn("clip", col("irpm"), ex(binary(num(0), "-", col("cap"))), col("cap")),
            ),
            "df = df.with_columns(\n"
            '    pl.col("irpm").clip(pl.lit(0) - pl.col("cap"), pl.col("cap")).alias("t")\n'
            ")",
        ),
        (
            step("c", "with_column", name="t", expr=fn("clip", col("x"), _CAP, var("top"))),
            'df = df.with_columns(pl.col("x").clip(global_constants.cap, top).alias("t"))',
        ),
        (
            step("f", "with_column", name="t", expr=fn("fill_null", col("a"), col("b"))),
            'df = df.with_columns(pl.col("a").fill_null(pl.col("b")).alias("t"))',
        ),
        (
            step("f", "with_column", name="t", expr=fn("clip", col("a"), num(-1), num(1))),
            'df = df.with_columns(pl.col("a").clip(-1, 1).alias("t"))',
        ),
        (
            step("d", "with_column", name="t", expr=fn("divide", col("premium"), col("lim"))),
            "df = df.with_columns(\n"
            '    pl.when(pl.col("lim") != 0).then(pl.col("premium") / pl.col("lim")).alias("t")\n'
            ")",
        ),
        (
            step(
                "d",
                "with_column",
                name="t",
                expr=fn("divide", col("p"), ex(binary(col("a"), "-", col("b")))),
            ),
            "df = df.with_columns(\n"
            '    pl.when(pl.col("a") - pl.col("b") != 0)\n'
            '    .then(pl.col("p") / (pl.col("a") - pl.col("b")))\n'
            '    .alias("t")\n'
            ")",
        ),
        (
            step("b", "with_column", name="t", expr=_CAPPED),
            "df = df.with_columns(\n"
            '    pl.when(pl.col("irpm") > pl.col("cap"))\n'
            '    .then(pl.col("cap"))\n'
            '    .when(pl.col("irpm") < pl.lit(0) - pl.col("cap"))\n'
            '    .then(pl.lit(0) - pl.col("cap"))\n'
            '    .otherwise(pl.col("irpm"))\n'
            '    .alias("t")\n'
            ")",
        ),
        (
            step(
                "sa",
                "with_column",
                name="t",
                expr=sum_across(col("a"), col("b"), ex(binary(col("c"), "*", num(2)))),
            ),
            "df = df.with_columns(\n"
            '    pl.sum_horizontal(pl.col("a"), pl.col("b"), pl.col("c") * 2).alias("t")\n'
            ")",
        ),
    ],
    ids=[
        "filtered-sum",
        "filtered-row-count",
        "filtered-ordered-first",
        "filtered-sum-of-a-formula",
        "running-total-of-a-function",
        "clip-between-column-bounds",
        "clip-by-a-constant-and-a-variable",
        "fill-null-from-a-column",
        "clip-between-plain-values",
        "divide-by-a-column",
        "divide-by-a-formula",
        "if-then-with-two-branches",
        "sum-across",
    ],
)
def test_expression_additions_render(kind_step: dict[str, Any], expected: str) -> None:
    top = step("v", "variable", name="top", value=num(2))
    rendered = render_polars_steps([source(), top, kind_step], ["quotes"], start="input")
    assert step_text(rendered, 2) == expected


@pytest.mark.parametrize(
    ("kind_step", "fragment"),
    [
        (
            window("cs", "cum_sum", "premium", ["region"], [("qid", False)], where=_WEB),
            "row filter needs an aggregate of the whole group, such as sum or mean; "
            "'cum_sum' gives a value per row",
        ),
        (
            window("rn", "row_number", None, ["region"], [("qid", False)], where=_WEB),
            "'row_number' gives a value per row",
        ),
        (
            window(
                "t",
                "sum",
                "premium",
                ["region"],
                where=[cond("premium", "gt", value=ex(binary(col("a"), "*", num(2))))],
            ),
            "must be a plain value, column or variable here",
        ),
        (window("n", "len", "premium", []), "'len' takes no value to aggregate; remove its 'of'"),
        (
            window("rn", "row_number", "qid", ["region"]),
            "'row_number' takes no value to aggregate; remove its 'of'",
        ),
        (window("t", "sum", None, []), "needs a value to aggregate: a column or an expression"),
        (window("t", "sum", num(1), []), "value must be a column or an expression"),
        (window("t", "mean", var("rate"), []), "value must be a column or an expression"),
        (
            step("c", "with_column", name="t", expr=fn("clip", col("a"), text("low"), num(1))),
            "clip argument 1 must be a number, a column, a variable, a constant or an expression",
        ),
        (
            step("c", "with_column", name="t", expr=fn("fill_null", col("a"), null())),
            "must be a number, text or true/false, a column, a variable, a constant or an "
            "expression",
        ),
        (
            step("r", "with_column", name="t", expr=fn("round", col("a"), col("places"))),
            "round argument 1 must be a plain value or a constant",
        ),
        (
            step("b", "with_column", name="t", expr=branches(otherwise=num(0))),
            "Expression needs at least one branch",
        ),
        (
            step("b", "with_column", name="t", expr=branches(branch([], num(1)), otherwise=num(0))),
            "Add at least one condition",
        ),
        (
            step(
                "b",
                "with_column",
                name="t",
                expr={
                    "type": "conditional",
                    "match": "all",
                    "conditions": [cond("a", "is_null")],
                    "then": num(1),
                    "otherwise": num(0),
                },
            ),
            "Unknown field(s) ['conditions', 'match', 'then'] in Expression",
        ),
        (
            step(
                "b",
                "with_column",
                name="t",
                expr=branches(
                    branch([cond("a", "is_null")], num(1)),
                    branch([cond("a", "nope")], num(2)),
                    otherwise=num(0),
                ),
            ),
            "Expression branch 2 condition 1 operator must be one of",
        ),
        (
            step("sa", "with_column", name="t", expr=sum_across(col("a"))),
            "Expression sum across needs at least two parts",
        ),
        (
            step(
                "sa",
                "with_column",
                name="t",
                expr={**sum_across(col("a"), col("b")), "text": 3},
            ),
            "Expression formula text must be a string",
        ),
    ],
    ids=[
        "positional-aggregate",
        "row-number",
        "nested-filter-operand",
        "row-count-with-a-value",
        "row-number-with-a-value",
        "sum-without-a-value",
        "literal-value",
        "variable-value",
        "clip-to-text",
        "fill-null-with-null",
        "round-to-a-column",
        "if-then-without-branches",
        "if-then-branch-without-conditions",
        "if-then-in-the-old-shape",
        "if-then-later-branch-names-itself",
        "sum-across-one-part",
        "sum-across-with-malformed-text",
    ],
)
def test_expression_additions_reject(kind_step: dict[str, Any], fragment: str) -> None:
    with pytest.raises(PolarsStepError) as exc_info:
        render_polars_steps([source(), kind_step], ["quotes"], start="input")
    assert exc_info.value.step_index == 1
    assert fragment in exc_info.value.message


def test_a_window_row_filter_aggregates_only_the_passing_rows() -> None:
    frame = pl.DataFrame(
        {
            "region": ["n", "n", "n", "s", "s"],
            "qid": [3, 1, 2, 4, 5],
            "channel": ["web", "broker", "web", "broker", None],
            "premium": [100.0, 50.0, 25.0, 30.0, 20.0],
        }
    )
    out = _run_steps(
        [
            source(),
            window("web_total", "sum", "premium", ["region"], where=_WEB),
            window("web_mean", "mean", "premium", ["region"], where=_WEB),
            window("web_quotes", "len", None, ["region"], where=_WEB),
            window("first_web", "first", "premium", ["region"], [("qid", False)], where=_WEB),
        ],
        frame,
    )
    # A partition with no passing row aggregates no rows: 0 for sum and the
    # counts, null otherwise; the missing channel never passes.
    assert out["web_total"].to_list() == [125.0, 125.0, 125.0, 0.0, 0.0]
    assert out["web_mean"].to_list() == [62.5, 62.5, 62.5, None, None]
    assert out["web_quotes"].to_list() == [2, 2, 2, 0, 0]
    assert out["first_web"].to_list() == [25.0, 25.0, 25.0, None, None]


def test_a_window_aggregates_an_expression() -> None:
    frame = pl.DataFrame(
        {
            "q": [1, 1, 2],
            "lim": [100.0, 200.0, 50.0],
            "load": [0.01, 0.02, 0.5],
            "channel": ["web", "broker", "web"],
        }
    )
    loaded = ex(binary(col("lim"), "*", col("load")))
    out = _run_steps(
        [
            source(),
            window("loaded", "sum", loaded, ["q"]),
            window("web_loaded", "sum", loaded, ["q"], where=_WEB),
        ],
        frame,
    )
    assert out["loaded"].to_list() == [5.0, 5.0, 25.0]
    assert out["web_loaded"].to_list() == [1.0, 1.0, 25.0]


def test_clip_and_fill_null_take_columns_and_expressions() -> None:
    frame = pl.DataFrame(
        {
            "irpm": [0.4, -0.4, 0.1, None],
            "cap": [0.25, 0.25, 0.25, 0.25],
            "quoted": [None, 2.0, None, 4.0],
            "fallback": [1.0, 1.0, 3.0, 3.0],
        }
    )
    out = _run_steps(
        [
            source(),
            step(
                "c",
                "with_column",
                name="capped",
                expr=fn("clip", col("irpm"), ex(binary(num(0), "-", col("cap"))), col("cap")),
            ),
            step(
                "f",
                "with_column",
                name="filled",
                expr=fn("fill_null", col("quoted"), ex(binary(col("fallback"), "*", num(2)))),
            ),
        ],
        frame,
    )
    assert out["capped"].to_list() == [0.25, -0.25, 0.1, None]
    assert out["filled"].to_list() == [2.0, 2.0, 6.0, 4.0]


def test_divide_is_missing_where_the_divisor_is_zero_or_missing() -> None:
    frame = pl.DataFrame(
        {"premium": [10.0, 10.0, 10.0, None, 0.0], "lim": [4.0, 0.0, None, 2.0, 0.0]}
    )
    out = _run_steps(
        [
            source(),
            step("d", "with_column", name="rate", expr=fn("divide", col("premium"), col("lim"))),
            step("p", "with_column", name="plain", expr=binary(col("premium"), "/", col("lim"))),
        ],
        frame,
    )
    assert out["rate"].to_list() == [2.5, None, None, None, None]
    # `/` stays Polars' division: infinite for x / 0 and NaN for 0 / 0.
    plain = out["plain"].to_list()
    assert plain[:4] == [2.5, float("inf"), None, None] and math.isnan(plain[4])


def test_a_row_takes_the_first_branch_whose_conditions_hold() -> None:
    frame = pl.DataFrame({"irpm": [0.4, -0.4, 0.1, None], "cap": [0.25] * 4})
    out = _run_steps([source(), step("b", "with_column", name="t", expr=_CAPPED)], frame)
    assert out["t"].to_list() == [0.25, -0.25, 0.1, None]
    overlapping = branches(
        branch([cond("irpm", "gt", value=num(0))], text("positive")),
        branch([cond("irpm", "gt", value=num(0.3))], text("large")),
        otherwise=text("other"),
    )
    out = _run_steps([source(), step("b", "with_column", name="t", expr=overlapping)], frame)
    assert out["t"].to_list() == ["positive", "other", "positive", "other"]


def test_a_sum_across_skips_missing_parts() -> None:
    frame = pl.DataFrame({"a": [1.0, None, None], "b": [2.0, None, 3.0]})
    out = _run_steps(
        [source(), step("sa", "with_column", name="t", expr=sum_across(col("a"), col("b")))],
        frame,
    )
    # A row whose parts are all missing sums to 0, as Fill missing (0) and a formula would.
    assert out["t"].to_list() == [3.0, 0.0, 3.0]


def test_a_constant_in_an_operand_argument_is_checked_against_the_argument_types() -> None:
    rendered = render_polars_steps(
        [source(), step("c", "with_column", name="t", expr=fn("fill_null", col("a"), _CAP))],
        ["quotes"],
        start="input",
    )
    [reference] = rendered.constant_references
    assert (reference.step_index, reference.name) == (1, "cap")
    assert reference.types == ("integer", "float", "text", "boolean")


def test_generated_expression_additions_stay_inside_the_lineage_model() -> None:
    """Each addition's generated shape is one the lineage and cardinality models prove."""
    from haute._column_lineage import analyze_polars_cardinality, analyze_polars_lineage

    schema = {"rows": frozenset({"region", "qid", "channel", "premium", "spare"})}
    cases = {
        "filtered window": (
            window("t", "sum", "premium", ["region"], where=_WEB),
            {"premium", "channel", "region"},
        ),
        "filtered row count": (
            window("t", "len", None, ["region"], where=_WEB),
            {"channel", "region"},
        ),
        "window of a formula": (
            window("t", "sum", ex(binary(col("premium"), "*", col("qid"))), ["region"]),
            {"premium", "qid", "region"},
        ),
        "divide": (
            step("d", "with_column", name="t", expr=fn("divide", col("premium"), col("qid"))),
            {"premium", "qid"},
        ),
        "if-then branches": (
            step(
                "b",
                "with_column",
                name="t",
                expr=branches(
                    branch([cond("premium", "gt", value=col("qid"))], col("qid")),
                    branch([cond("channel", "is_null")], num(0)),
                    otherwise=col("premium"),
                ),
            ),
            {"premium", "qid", "channel"},
        ),
        "sum across": (
            step(
                "sa",
                "with_column",
                name="t",
                expr=sum_across(col("premium"), ex(binary(col("qid"), "*", num(2)))),
            ),
            {"premium", "qid"},
        ),
        "clip between columns": (
            step(
                "c",
                "with_column",
                name="t",
                expr=fn("clip", col("premium"), ex(binary(num(0), "-", col("qid"))), col("qid")),
            ),
            {"premium", "qid"},
        ),
    }
    for name, (kind_step, demanded) in cases.items():
        code = render_polars_steps([source("rows"), kind_step], ["rows"], start="input").code
        lineage = analyze_polars_lineage(code, schema, demanded_output=["t"])
        assert lineage.supported, (name, lineage.reason)
        assert lineage.demands_by_input == {"rows": frozenset(demanded)}, name
        bound = analyze_polars_cardinality(code, {"rows": 100})
        assert bound.supported and bound.output_upper_bound == 100, (name, bound.reason)


def test_a_start_only_program_previews_the_input_unchanged(tmp_path: Path) -> None:
    policies = _extended_frame(tmp_path)
    graph = PipelineGraph(
        nodes=[policies, _stepped("t", [source("policies")])], edges=[make_edge("policies", "t")]
    )
    result = execute_graph(graph, target_node_id="t", execution_context=_capped_context())["t"]
    assert result.status == "ok", result.error
    assert [c.name for c in result.columns] == ["qid", "region", "premium"]
    assert [row["qid"] for row in result.preview] == ["c3", "c1", "c2", "c4", "c5", "c6", "cx"]


def test_join_validation_fails_loudly_on_duplicate_keys(tmp_path: Path) -> None:
    quotes, _rates = _frames(tmp_path)
    dup = tmp_path / "dup_rates.parquet"
    pl.DataFrame({"region": ["north", "north"], "rate": [1.0, 2.0]}).write_parquet(dup)
    node = _stepped(
        "t",
        [
            source(),
            step(
                "j",
                "join",
                input="dup",
                how="left",
                maintain_order="left_right",
                left_on=["region"],
                right_on=["region"],
                suffix="_r",
                validate="m:1",
            ),
        ],
    )
    graph = PipelineGraph(
        nodes=[quotes, _ready_source("dup", dup), node],
        edges=[make_edge("quotes", "t"), make_edge("dup", "t")],
    )
    result = execute_graph(graph, target_node_id="t", execution_context=_capped_context())["t"]
    assert result.status == "error"
    assert "m:1" in str(result.error)


# ---------------------------------------------------------------------------
# Global constants as step operands
# ---------------------------------------------------------------------------


def const(name: str) -> dict[str, Any]:
    return {"kind": "constant", "name": name}


ALL_CONSTANT_TYPES = ("integer", "float", "text", "boolean", "date")


def _round(argument: dict[str, Any]) -> dict[str, Any]:
    return {"type": "function", "fn": "round", "operand": col("x"), "args": [argument]}


def test_a_constant_renders_bare_in_value_position_and_wrapped_in_expression_position() -> None:
    rendered = render_polars_steps(
        [
            source(),
            step(
                "f", "filter", match="all", conditions=[cond("premium", "gt", value=const("floor"))]
            ),
            step(
                "w", "with_column", name="rate", expr={"type": "operand", "operand": const("rate")}
            ),
            step(
                "b",
                "with_column",
                name="gross",
                expr={"type": "binary", "left": const("rate"), "op": "*", "right": const("load")},
            ),
        ],
        start="input",
    )

    assert rendered.code.split("\n")[1:] == [
        'df = df.filter(pl.col("premium") > global_constants.floor)',
        'df = df.with_columns(pl.lit(global_constants.rate).alias("rate"))',
        "df = df.with_columns(",
        '    (pl.lit(global_constants.rate) * global_constants.load).alias("gross")',
        ")",
    ]
    assert [(r.step_index, r.name) for r in rendered.constant_references] == [
        (1, "floor"),
        (2, "rate"),
        (3, "rate"),
        (3, "load"),
    ]


@pytest.mark.parametrize(
    ("slot", "types"),
    [
        (cond("x", "eq", value=const("c")), ALL_CONSTANT_TYPES),
        (cond("x", "contains", value=const("c")), ("text",)),
    ],
    ids=["comparison", "string-operator"],
)
def test_a_condition_value_takes_the_constant_types_its_operator_does(
    slot: dict[str, Any], types: tuple[str, ...]
) -> None:
    rendered = render_polars_steps(
        [source(), step("f", "filter", match="all", conditions=[slot])], start="input"
    )
    [reference] = rendered.constant_references
    assert (reference.types, reference.non_negative) == (types, False)


@pytest.mark.parametrize(
    ("fn", "args", "types", "non_negative"),
    [
        ("round", [const("c")], ("integer",), True),
        ("slice", [const("c"), num(2)], ("integer",), False),
        ("clip", [const("c"), num(2)], ("integer", "float"), False),
        ("offset_by", [const("c")], ("text",), False),
        ("fill_null", [const("c")], ("integer", "float", "text", "boolean"), False),
    ],
)
def test_a_function_argument_takes_the_constant_types_of_its_expected_type(
    fn: str, args: list[dict[str, Any]], types: tuple[str, ...], non_negative: bool
) -> None:
    rendered = render_polars_steps(
        [
            source(),
            step(
                "w",
                "with_column",
                name="out",
                expr={"type": "function", "fn": fn, "operand": col("x"), "args": args},
            ),
        ],
        start="input",
    )
    [reference] = rendered.constant_references
    assert (reference.types, reference.non_negative) == (types, non_negative)
    assert "global_constants.c" in rendered.code


def test_fill_and_branch_values_take_every_constant_type() -> None:
    rendered = render_polars_steps(
        [
            source(),
            step("n", "fill_null", columns=["x"], fill={"kind": "value", "value": const("a")}),
            step(
                "c",
                "with_column",
                name="band",
                expr={
                    "type": "conditional",
                    "branches": [
                        {
                            "match": "all",
                            "conditions": [cond("x", "gt", value=num(1))],
                            "then": const("b"),
                        }
                    ],
                    "otherwise": text("low"),
                },
            ),
        ],
        start="input",
    )
    assert [r.types for r in rendered.constant_references] == [ALL_CONSTANT_TYPES] * 2


def test_a_dtype_argument_and_an_invalid_name_refuse_a_constant() -> None:
    cast = {"type": "function", "fn": "cast", "operand": col("x"), "args": [const("c")]}
    with pytest.raises(PolarsStepError, match="not a constant"):
        render_polars_steps(
            [source(), step("w", "with_column", name="o", expr=cast)], start="input"
        )
    bad = {"type": "operand", "operand": const("1rate")}
    with pytest.raises(PolarsStepError, match="not a valid constant name"):
        render_polars_steps([source(), step("w", "with_column", name="o", expr=bad)], start="input")


@pytest.mark.parametrize(
    ("constant", "problem"),
    [
        (None, "Global constant 'digits' is not defined."),
        (
            GlobalConstant(name="digits", type="float", value=2.0),
            "is float; this value takes integer.",
        ),
        (
            GlobalConstant(name="digits", type="integer", by_source={"live": 2, "nb_batch": -1}),
            "must be zero or more; it is -1 for source 'nb_batch'.",
        ),
        (GlobalConstant(name="digits", type="integer", by_source={"live": 2, "nb_batch": 0}), None),
    ],
    ids=["undefined", "wrong-type", "negative-source", "fits"],
)
def test_reference_problem_checks_existence_type_and_every_value(
    constant: GlobalConstant | None, problem: str | None
) -> None:
    from haute._global_constants import reference_problem

    rendered = render_polars_steps(
        [source(), step("w", "with_column", name="r", expr=_round(const("digits")))],
        start="input",
    )
    [reference] = rendered.constant_references
    found = reference_problem(reference, {} if constant is None else {constant.name: constant})
    if problem is None:
        assert found is None
    else:
        assert found is not None and problem in found


def test_render_route_reports_a_reference_the_given_constants_cannot_serve(
    client: TestClient,
) -> None:
    steps = [
        source(),
        step(
            "f", "filter", match="all", conditions=[cond("region", "contains", value=const("area"))]
        ),
    ]
    body = _render_body(steps, ["quotes"])

    without = client.post("/api/pipeline/polars-steps/render", json=body)
    assert without.json()["ok"] is True

    wrong = client.post(
        "/api/pipeline/polars-steps/render",
        json={**body, "global_constants": [{"name": "area", "type": "integer", "value": 1}]},
    )
    assert wrong.json() == {
        "ok": False,
        "code": "",
        "step_lines": [],
        "step_index": 1,
        "message": "Global constant 'area' is integer; this value takes text.",
    }


def test_save_refuses_a_step_constant_of_the_wrong_type(project_root: Path) -> None:

    steps = [
        source(),
        step("f", "filter", match="all", conditions=[cond("premium", "gt", value=const("floor"))]),
        step("w", "with_column", name="r", expr=_round(const("floor"))),
    ]
    graph = _project_graph(project_root, steps)
    graph.global_constants = [GlobalConstant(name="floor", type="float", value=100.0)]

    with pytest.raises(RequestRefusedError) as refused:
        _save(project_root, graph)

    assert refused.value.status_code == 400
    assert refused.value.detail == (
        "Node 't', step 3: Global constant 'floor' is float; this value takes integer."
    )
