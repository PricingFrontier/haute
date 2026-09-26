"""Low-code Polars steps: schema, rendering, materialisation, persistence and execution."""

from __future__ import annotations

import ast
import json
import pickle
import re
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from fastapi.testclient import TestClient

from haute._builders import _build_node_fn
from haute._code_extraction import (
    INCOMPLETE_STEPS_MESSAGE,
    INCOMPLETE_TRANSFORM_MESSAGE,
    extract_user_code,
)
from haute._config_io import collect_node_configs, node_emits_sidecar
from haute._execution_admission import create_admitted_execution_context
from haute._execution_context import ExecutionContext, ExecutionProfile
from haute._flatten import flatten_graph
from haute._graph_utils import _sanitize_func_name as _sanitize_label
from haute._polars_io_registry import validate_data_input_config
from haute._polars_steps import (
    PolarsStepError,
    RenderedSteps,
    SteppedSurface,
    is_stepped_config,
    rename_step_inputs,
    render_polars_steps,
    step_input_names,
    stepped_surface_for,
    validate_polars_steps,
)
from haute._polars_steps_layout import layout_statement, restyle_statement
from haute._types import (
    GraphEdge,
    GraphNode,
    NodeData,
    NodeType,
    PipelineGraph,
    SubmodelDefinition,
    SubmodelEndpoint,
    SubmodelInputPort,
    SubmodelOutputPort,
)
from haute.assistant._ops import apply_ops
from haute.assistant._wire_ops import UpdateNodeOp
from haute.chunking import classify_chunk_local_polars_code
from haute.codegen import graph_to_code, graph_to_code_multi
from haute.errors import ConfigError
from haute.executor import execute_graph
from haute.parser import parse_pipeline_file, parse_pipeline_source
from haute.routes._save_pipeline import SavePipelineService
from haute.schemas import SavePipelineRequest
from tests.conftest import (
    build_test_input_snapshot,
    current_source_revision,
    make_edge,
    make_file_input_config,
    make_graph,
    make_source_node,
)
from tests.test_codegen_execution_equivalence import _collect, _write_and_import
from tests.test_polars_steps_corpus import TRANSLATIONS as CORPUS_TRANSLATIONS

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")


# ---------------------------------------------------------------------------
# Step builders
# ---------------------------------------------------------------------------


def num(value: float) -> dict[str, Any]:
    return {"kind": "literal", "type": "number", "value": value}


def text(value: str) -> dict[str, Any]:
    return {"kind": "literal", "type": "text", "value": value}


def col(name: str) -> dict[str, Any]:
    return {"kind": "column", "name": name}


def var(name: str) -> dict[str, Any]:
    return {"kind": "variable", "name": name}


def cond(column: str, operator: str, **rest: Any) -> dict[str, Any]:
    return {"column": column, "operator": operator, **rest}


def source(name: str = "quotes", step_id: str = "s") -> dict[str, Any]:
    return {"id": step_id, "kind": "source", "input": name}


def step(step_id: str, kind: str, **fields: Any) -> dict[str, Any]:
    return {"id": step_id, "kind": kind, **fields}


GOLDEN_STEPS: list[dict[str, Any]] = [
    source(),
    step("v", "variable", name="ipt_rate", value=num(0.12)),
    step(
        "f",
        "filter",
        match="all",
        conditions=[
            cond("premium", "gt", value=num(100)),
            cond("region", "is_in", values=[text("north"), text("south")]),
        ],
    ),
    step(
        "w",
        "with_column",
        name="gross",
        expr={"type": "binary", "left": col("premium"), "op": "*", "right": var("ipt_rate")},
    ),
    step(
        "c",
        "with_column",
        name="band",
        expr={
            "type": "conditional",
            "match": "any",
            "conditions": [cond("gross", "ge", value=num(500))],
            "then": text("high"),
            "otherwise": text("low"),
        },
    ),
    step(
        "j",
        "join",
        input="rates",
        how="left",
        leftOn=["region"],
        rightOn=["region"],
        suffix="_rate",
    ),
    step(
        "g",
        "group_by",
        keys=["band"],
        aggregations=[
            {"column": "gross", "agg": "sum", "name": "gross_total"},
            {"column": "gross", "agg": "len", "name": "rows"},
        ],
    ),
]

GOLDEN_CODE = "\n".join(
    [
        "df = quotes",
        "ipt_rate = 0.12",
        "df = df.filter(",
        '    (pl.col("premium") > 100) & pl.col("region").is_in(["north", "south"])',
        ")",
        'df = df.with_columns((pl.col("premium") * ipt_rate).alias("gross"))',
        "df = df.with_columns(",
        '    pl.when(pl.col("gross") >= 500)',
        '    .then(pl.lit("high"))',
        '    .otherwise(pl.lit("low"))',
        '    .alias("band")',
        ")",
        "df = df.join(",
        "    rates,",
        '    left_on=["region"],',
        '    right_on=["region"],',
        '    how="left",',
        '    suffix="_rate",',
        ")",
        'df = df.group_by("band", maintain_order=True).agg(',
        '    pl.col("gross").sum().alias("gross_total"),',
        '    pl.len().alias("rows"),',
        ")",
    ]
)
#: Each golden step's inclusive line range: a statement that would pass 88
#: columns in the node body (84 at column 0) is laid out over several lines,
#: as ``ruff format`` lays it out there.
GOLDEN_STEP_LINES = ((1, 1), (2, 2), (3, 5), (6, 6), (7, 12), (13, 19), (20, 23))


def step_text(rendered: RenderedSteps, index: int) -> str:
    """The code step *index* rendered, read through its line range."""
    start, end = rendered.step_lines[index]
    return "\n".join(rendered.code.split("\n")[start - 1 : end])


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
        leftOn=["region"],
        rightOn=["region"],
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
        ")",
        "df = df.head(5)",
    ]
)


def test_long_statements_break_one_argument_per_line() -> None:
    rendered = render_polars_steps(LONG_STEPS, ["quotes", "rates"], start="input")
    assert rendered.code == LONG_CODE
    assert rendered.step_lines == ((1, 1), (2, 11), (12, 15), (16, 22), (23, 23))
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


#: ``ruff format`` as the renderer's style targets it: its default line length
#: and quote style, with no project configuration.
RUFF_FORMAT = [sys.executable, "-m", "ruff", "format", "--isolated", "--line-length", "88", "-"]


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
        (" \n\t", "Python statement"),
        ("# Write code here\n", "Python statement"),
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
                casts=[{"column": "a", "dtype": "Int64"}, {"column": "b", "dtype": "String"}],
            ),
            'df = df.with_columns(pl.col("a").cast(pl.Int64), pl.col("b").cast(pl.String))',
        ),
        (
            step("x", "sort", keys=[{"column": "a", "descending": True}], nullsLast=True),
            'df = df.sort("a", descending=True, nulls_last=True)',
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
            step("x", "join", input="rates", how="cross", leftOn=[], rightOn=[], suffix="_r"),
            'df = df.join(rates, how="cross", suffix="_r")',
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
                conditions=[cond("a", "is_null"), cond("b", "not_in", values=[num(1), num(2)])],
            ),
            'df = df.filter(pl.col("a").is_null() | ~pl.col("b").is_in([1, 2]))',
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
            '    pl.col("d").is_in(pl.Series(["2026-01-01", "2026-02-01"]).str.to_date())\n'
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
                expr={"type": "window", "agg": "sum", "column": "a", "over": ["k"]},
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
                "match": "all",
                "conditions": [cond("premium", "gt", value=var("rate"))],
                "then": var("rate"),
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
            step("j", "join", input="rates", how="cross", leftOn=["a"], rightOn=[], suffix="_r"),
        ],
        1,
        "no key columns",
    ),
    (
        "unequal keys",
        [
            source(),
            step("j", "join", input="rates", how="left", leftOn=["a"], rightOn=[], suffix="_r"),
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
        step("j", "join", input="b", how="left", leftOn=["k"], rightOn=["k"], suffix="_b"),
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


def null() -> dict[str, Any]:
    return {"kind": "literal", "type": "null"}


def window(
    name: str,
    agg: str,
    column: str,
    over: list[str],
    order_by: list[tuple[str, bool]] | None = None,
    descending: bool | None = None,
) -> dict[str, Any]:
    expr: dict[str, Any] = {"type": "window", "agg": agg, "column": column, "over": over}
    if order_by is not None:
        expr["orderBy"] = [{"column": c, "descending": d} for c, d in order_by]
    if descending is not None:
        expr["descending"] = descending
    return step(f"w_{name}", "with_column", name=name, expr=expr)


@pytest.mark.parametrize(
    ("kind_step", "expected"),
    [
        (
            window(
                "rn",
                "row_number",
                "quote_id",
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
        (window("n", "len", "premium", []), 'df = df.with_columns(pl.len().alias("n"))'),
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
                    "match": "all",
                    "conditions": [cond("a", "eq", value=num(1))],
                    "then": col("b"),
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
                leftOn=["region"],
                rightOn=["region"],
                suffix="_r",
                validate="m:1",
                maintainOrder="left",
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
                "column": "premium",
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
            window("rn", "row_number", "q", ["r"], [("a", False), ("b", True)]),
            1,
            "share one direction",
        ),
        (window("rn", "row_number", "q", [], [("a", False)]), 1, "sort the frame instead"),
        (window("rn", "row_number", "q", ["r"], []), 1, "at least one column"),
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
                leftOn=[],
                rightOn=[],
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
                leftOn=["r"],
                rightOn=["r"],
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
                leftOn=["region"],
                rightOn=["region"],
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
                leftOn=[],
                rightOn=[],
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


def _extended_frame(tmp_path: Path) -> GraphNode:
    """Unsorted partitions, rank ties, interior nulls and an id without a digit."""
    path = tmp_path / "policies.parquet"
    pl.DataFrame(
        {
            "qid": ["c3", "c1", "c2", "c4", "c5", "c6", "cx"],
            "region": ["north", "north", "south", "east", "north", "north", "south"],
            "premium": [50.0, 800.0, 200.0, None, None, 800.0, 100.0],
        }
    ).write_parquet(path)
    return _ready_source("policies", path)


def test_extended_vocabulary_executes(tmp_path: Path) -> None:
    policies = _extended_frame(tmp_path)
    by_qid = [("qid", False)]
    steps = [
        source("policies"),
        window("rn", "row_number", "", ["region"], by_qid),
        window("rn_desc", "row_number", "", ["region"], [("qid", True)]),
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
                "column": "premium",
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
                "match": "all",
                "conditions": [cond("region", "eq", value=text("north"))],
                "then": col("premium"),
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
        step("srt", "sort", keys=[{"column": "region", "descending": False}], nullsLast=False),
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


# ---------------------------------------------------------------------------
# Nested expressions
# ---------------------------------------------------------------------------


def ex(expr: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "expr", "expr": expr}


def binary(left: dict[str, Any], op: str, right: dict[str, Any]) -> dict[str, Any]:
    return {"type": "binary", "left": left, "op": op, "right": right}


def fn(name: str, operand: dict[str, Any], *args: dict[str, Any]) -> dict[str, Any]:
    return {"type": "function", "fn": name, "operand": operand, "args": list(args)}


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
                    ex({"type": "window", "agg": "mean", "column": "premium", "over": ["region"]}),
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
                            "match": "all",
                            "conditions": [cond("region", "eq", value=text("north"))],
                            "then": col("premium"),
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
    operand: dict[str, Any] = num(1)
    for _ in range(depth):
        operand = ex(binary(operand, "+", num(1)))
    return {"type": "operand", "operand": operand}


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
                ex({"type": "window", "agg": "mean", "column": "premium", "over": ["region"]}),
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
                        "match": "all",
                        "conditions": [cond("region", "eq", value=text("north"))],
                        "then": col("premium"),
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
                variableName="measure",
                valueName="value",
            ),
            "df = df.unpivot(\n"
            '    on=["premium", "sum_insured"],\n'
            '    index=["quote_id"],\n'
            '    variable_name="measure",\n'
            '    value_name="value",\n'
            ")",
        ),
        (
            step("x", "unpivot", on=["premium"], index=[], variableName="m", valueName="v"),
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
            step("x", "unpivot", on=[], index=[], variableName="m", valueName="v"),
            "at least one column",
        ),
        (
            step("x", "unpivot", on=["a", "g"], index=["g"], variableName="m", valueName="v"),
            "both unpivoted and kept as index",
        ),
        (
            step("x", "unpivot", on=["a"], index=["g"], variableName="v", valueName="v"),
            "different names",
        ),
        (
            step("x", "unpivot", on=["a"], index=["g"], variableName="g", valueName="v"),
            "already an index column",
        ),
        (
            step("x", "unpivot", on=["*"], index=[], variableName="m", valueName="v"),
            "not the pattern '*'",
        ),
        (
            step("x", "unpivot", on=["^measure_.*$"], index=[], variableName="m", valueName="v"),
            "not the pattern",
        ),
        (step("x", "select", columns=["*"]), "not the pattern '*'"),
        (
            step("x", "sort", keys=[{"column": "*", "descending": False}], nullsLast=False),
            "not the pattern '*'",
        ),
        (
            window("rn", "row_number", "", ["g"], [("^measure_.*$", False)]),
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
        step("c", "cast", casts=[{"column": "digit", "dtype": "Int64"}]),
        step("sel", "select", columns=["qid"], dtypes=["Float64", "Int64"]),
        step(
            "u",
            "unpivot",
            on=["premium", "digit"],
            index=["qid"],
            variableName="measure",
            valueName="value",
        ),
        step(
            "o",
            "sort",
            keys=[
                {"column": "qid", "descending": False},
                {"column": "measure", "descending": False},
            ],
            nullsLast=False,
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
        step("u", "unpivot", on=["a", "i"], index=["g"], variableName="m", valueName="v"),
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
                col("a"), "-", ex({"type": "window", "agg": "mean", "column": "a", "over": ["g"]})
            ),
        ),
        step("sel", "select", columns=["dev"]),
    )
    windowed = analyze_polars_lineage(nested_window, schema, demanded_output=["dev"])
    assert windowed.supported and windowed.demands_by_input == {"rows": frozenset({"a", "g"})}


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
                leftOn=["region"],
                rightOn=["region"],
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
# Materialisation invariant
# ---------------------------------------------------------------------------


def _stepped(node_id: str, steps: list[dict[str, Any]], **extra: Any) -> GraphNode:
    return GraphNode(
        id=node_id,
        data=NodeData(label=node_id, nodeType="polars", config={"steps": steps, **extra}),
    )


def test_node_data_materialises_code_from_steps() -> None:
    node = _stepped("t", GOLDEN_STEPS, code="df = stale")
    assert node.data.config["code"] == GOLDEN_CODE
    assert "_steps_error" not in node.data.config

    broken = _stepped("t", [step("f", "filter", match="all", conditions=[])], code="df = stale")
    assert broken.data.config["code"] == ""
    assert (
        broken.data.config["_steps_error"]
        == "Step 1: The first step must choose the input to start from."
    )

    with pytest.raises(ValueError, match="must be a list"):
        _stepped("t", "not-a-list")  # type: ignore[arg-type]

    replaced = broken.with_config({"steps": [source()], "code": "zzz"})
    assert replaced.data.config["code"] == "df = quotes"
    assert "_steps_error" not in replaced.data.config


def test_chunk_planning_sees_materialised_code() -> None:
    stale = "df = quotes.with_columns((pl.col('a') * 2).alias('b'))"
    node = _stepped(
        "t",
        [
            source(),
            step(
                "g",
                "group_by",
                keys=["k"],
                aggregations=[{"column": "a", "agg": "sum", "name": "s"}],
            ),
        ],
        code=stale,
    )
    decision = classify_chunk_local_polars_code(node.data.config["code"], frame_names=("quotes",))
    assert not decision.eligible
    assert classify_chunk_local_polars_code(stale, frame_names=("quotes",)).eligible


def test_assistant_update_rematerialises_code() -> None:
    graph = PipelineGraph(nodes=[_stepped("t", [source()])], edges=[])
    new_steps = [source(), step("l", "limit", n=3)]
    out = apply_ops(graph, [UpdateNodeOp(node="t", config={"steps": new_steps})])
    assert out.nodes[0].data.config["code"] == "df = quotes\ndf = df.head(3)"


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def _capped_context() -> ExecutionContext:
    """An admitted preview context with a memory cap, so a join after a filter runs."""
    return create_admitted_execution_context(
        operation="polars-steps-test",
        profile=ExecutionProfile.PREVIEW_EAGER,
    )


def _ready_source(node_id: str, path: Path) -> GraphNode:
    node = make_source_node(node_id, str(path))
    node.data.config["format"] = "parquet"
    node.data.config["mode"] = "scan"
    build_test_input_snapshot(node.data.config)
    return node


def _frames(tmp_path: Path) -> tuple[GraphNode, GraphNode]:
    quotes = tmp_path / "quotes.parquet"
    rates = tmp_path / "rates.parquet"
    pl.DataFrame(
        {
            "premium": [50.0, 200.0, 800.0, None],
            "region": ["north", "south", "north", "east"],
            "north": ["c1", "c2", "c3", "c4"],
        }
    ).write_parquet(quotes)
    pl.DataFrame({"region": ["north", "south"], "rate": [1.1, 1.2]}).write_parquet(rates)
    return _ready_source("quotes", quotes), _ready_source("rates", rates)


def test_executor_runs_every_step_kind(tmp_path: Path) -> None:
    quotes, rates = _frames(tmp_path)
    # An ``is_in`` filter has no row-count estimate, so the in-process preview
    # profile refuses to materialise a join or group_by after it (the same
    # engine policy hand-written code meets). The golden program therefore
    # runs with a comparison filter here and ``is_in`` is covered on its own.
    executed_golden = [
        step("f", "filter", match="all", conditions=[cond("premium", "gt", value=num(100))])
        if s["kind"] == "filter"
        else s
        for s in GOLDEN_STEPS
    ]
    per_kind: dict[str, list[dict[str, Any]]] = {
        "golden": executed_golden,
        "filter_is_in": [source(), GOLDEN_STEPS[2]],
        "select": [source(), step("x", "select", columns=["premium"])],
        "drop": [source(), step("x", "drop", columns=["north"])],
        "rename": [source(), step("x", "rename", renames=[{"from": "premium", "to": "prem"}])],
        "cast": [source(), step("x", "cast", casts=[{"column": "premium", "dtype": "Int64"}])],
        "sort": [
            source(),
            step("x", "sort", keys=[{"column": "premium", "descending": True}], nullsLast=True),
        ],
        "unique": [source(), step("x", "unique", columns=["region"], keep="first")],
        "concat": [source(), step("x", "concat", inputs=["rates"], how="diagonal")],
        "fill_value": [
            source(),
            step("x", "fill_null", columns=["premium"], fill={"kind": "value", "value": num(0)}),
        ],
        "fill_strategy": [
            source(),
            step("x", "fill_null", columns=[], fill={"kind": "strategy", "strategy": "forward"}),
        ],
        "limit": [source(), step("x", "limit", n=2)],
        "conditional_text": [
            source(),
            step(
                "x",
                "with_column",
                name="band",
                expr={
                    "type": "conditional",
                    "match": "all",
                    "conditions": [cond("premium", "ge", value=num(500))],
                    "then": text("north"),
                    "otherwise": text("low"),
                },
            ),
        ],
        "window": [
            source(),
            step(
                "x",
                "with_column",
                name="tot",
                expr={"type": "window", "agg": "sum", "column": "premium", "over": ["region"]},
            ),
        ],
        "functions": [
            source(),
            step(
                "a",
                "with_column",
                name="r",
                expr={
                    "type": "function",
                    "fn": "round",
                    "operand": col("premium"),
                    "args": [num(0)],
                },
            ),
            step(
                "b",
                "with_column",
                name="u",
                expr={"type": "function", "fn": "upper", "operand": col("region"), "args": []},
            ),
            step(
                "c",
                "with_column",
                name="c",
                expr={
                    "type": "function",
                    "fn": "clip",
                    "operand": col("premium"),
                    "args": [num(100), num(300)],
                },
            ),
            step(
                "d",
                "with_column",
                name="f",
                expr={
                    "type": "function",
                    "fn": "fill_null",
                    "operand": col("premium"),
                    "args": [num(-1)],
                },
            ),
            step(
                "e",
                "with_column",
                name="third",
                expr={"type": "binary", "left": col("premium"), "op": "/", "right": num(3)},
            ),
            step(
                "g",
                "with_column",
                name="r2",
                expr={"type": "function", "fn": "round", "operand": col("third"), "args": [num(2)]},
            ),
        ],
    }
    nodes = [quotes, rates] + [_stepped(f"t_{name}", steps) for name, steps in per_kind.items()]
    edges = []
    for name in per_kind:
        edges.append(make_edge("quotes", f"t_{name}"))
        edges.append(make_edge("rates", f"t_{name}"))
    graph = PipelineGraph(nodes=nodes, edges=edges)

    # Each node is its own target so the planner admits every lineage on its
    # own merits (a cross join has no in-process estimate and stays a
    # rendering-only case).
    results = {}
    for name in per_kind:
        run = execute_graph(graph, target_node_id=f"t_{name}", execution_context=_capped_context())
        assert run[f"t_{name}"].status == "ok", (name, run[f"t_{name}"].error)
        results[f"t_{name}"] = run[f"t_{name}"]

    columns = lambda name: [c.name for c in results[name].columns]  # noqa: E731
    dtypes = lambda name: {c.name: c.dtype for c in results[name].columns}  # noqa: E731
    column = lambda name, col: [row[col] for row in results[name].preview]  # noqa: E731
    assert results["t_golden"].row_count == 1  # 200 and 800 both band "low"
    assert columns("t_golden") == ["band", "gross_total", "rows"]
    assert results["t_golden"].preview[0] == {"band": "low", "gross_total": 120.0, "rows": 2}
    assert results["t_filter_is_in"].row_count == 2  # premium > 100 and region in north/south
    assert columns("t_select") == ["premium"]
    assert "north" not in columns("t_drop")
    assert "prem" in columns("t_rename")
    assert column("t_rename", "prem") == [50.0, 200.0, 800.0, None]
    assert dtypes("t_cast")["premium"] == "Int64"
    assert column("t_cast", "premium") == [50, 200, 800, None]
    assert column("t_sort", "premium") == [800.0, 200.0, 50.0, None]
    assert column("t_unique", "region") == ["north", "south", "east"]
    assert results["t_unique"].row_count == 3
    assert column("t_fill_value", "premium") == [50.0, 200.0, 800.0, 0.0]
    assert column("t_fill_strategy", "premium") == [50.0, 200.0, 800.0, 800.0]
    assert list(zip(column("t_window", "region"), column("t_window", "tot"), strict=True)) == [
        ("north", 850.0),
        ("south", 200.0),
        ("north", 850.0),
        ("east", 0.0),
    ]
    assert column("t_functions", "r") == [50.0, 200.0, 800.0, None]
    assert column("t_functions", "u") == ["NORTH", "SOUTH", "NORTH", "EAST"]
    assert column("t_functions", "c") == [100.0, 200.0, 300.0, None]
    assert column("t_functions", "f") == [50.0, 200.0, 800.0, -1.0]
    assert column("t_functions", "r2") == [16.67, 66.67, 266.67, None]
    assert results["t_concat"].row_count == 6
    assert results["t_limit"].row_count == 2
    band = [row["band"] for row in results["t_conditional_text"].preview]
    assert band == ["low", "low", "north", "low"], "text literal must not read the 'north' column"


def test_incomplete_steps_fail_at_run_time_naming_the_step(tmp_path: Path) -> None:
    quotes, _rates = _frames(tmp_path)
    broken = _stepped("t", [source(), step("f", "filter", match="all", conditions=[])])
    graph = PipelineGraph(nodes=[quotes, broken], edges=[make_edge("quotes", "t")])
    results = execute_graph(graph)
    assert results["t"].status == "error"
    assert INCOMPLETE_TRANSFORM_MESSAGE in str(results["t"].error)
    assert "Step 2: Add at least one condition." in str(results["t"].error)

    unknown = _stepped("u", [source("policies")])
    graph = PipelineGraph(nodes=[quotes, unknown], edges=[make_edge("quotes", "u")])
    results = execute_graph(graph)
    assert results["u"].status == "error"
    assert "Step 1: Unknown input 'policies'; connected inputs: quotes." in str(results["u"].error)


def test_instance_of_stepped_transform_executes_with_implicit_mapping(tmp_path: Path) -> None:
    quotes, rates = _frames(tmp_path)
    alt_q = tmp_path / "alt_quotes.parquet"
    alt_r = tmp_path / "alt_rates.parquet"
    pl.DataFrame({"premium": [10000.0], "region": ["south"], "north": ["z"]}).write_parquet(alt_q)
    pl.DataFrame({"region": ["south"], "rate": [9.0]}).write_parquet(alt_r)
    original = _stepped("joiner", GOLDEN_STEPS)
    instance = GraphNode(
        id="joiner_inst",
        data=NodeData(label="joiner_inst", nodeType="polars", config={"instanceOf": "joiner"}),
    )
    graph = PipelineGraph(
        nodes=[
            quotes,
            rates,
            original,
            _ready_source("alt_quotes", alt_q),
            _ready_source("alt_rates", alt_r),
            instance,
        ],
        edges=[
            make_edge("quotes", "joiner"),
            make_edge("rates", "joiner"),
            make_edge("alt_quotes", "joiner_inst"),
            make_edge("alt_rates", "joiner_inst"),
        ],
    )
    results = execute_graph(
        graph, target_node_id="joiner_inst", execution_context=_capped_context()
    )
    assert results["joiner_inst"].status == "ok", results["joiner_inst"].error
    assert results["joiner_inst"].preview[0]["band"] == "high"


def test_explicit_instance_mapping_round_trips_and_executes(tmp_path: Path) -> None:
    quotes, rates = _frames(tmp_path)
    alt_q = tmp_path / "other_q.parquet"
    alt_r = tmp_path / "other_r.parquet"
    pl.DataFrame({"premium": [10000.0], "region": ["south"], "north": ["z"]}).write_parquet(alt_q)
    pl.DataFrame({"region": ["south"], "rate": [9.0]}).write_parquet(alt_r)
    original = _stepped("joiner", GOLDEN_STEPS)
    instance = GraphNode(
        id="joiner_inst",
        data=NodeData(
            label="joiner_inst",
            nodeType="polars",
            config={
                "instanceOf": "joiner",
                "inputMapping": {"quotes": "other_q", "rates": "other_r"},
            },
        ),
    )
    graph = PipelineGraph(
        nodes=[
            quotes,
            rates,
            original,
            _ready_source("other_q", alt_q),
            _ready_source("other_r", alt_r),
            instance,
        ],
        edges=[
            make_edge("quotes", "joiner"),
            make_edge("rates", "joiner"),
            make_edge("other_q", "joiner_inst"),
            make_edge("other_r", "joiner_inst"),
        ],
    )
    code = graph_to_code(graph, pipeline_name="main")
    _write_sidecars(tmp_path, graph)
    parsed = parse_pipeline_source(code, _base_dir=tmp_path)
    inst = next(n for n in parsed.nodes if n.id == "joiner_inst")
    assert inst.data.config["instanceOf"] == "joiner"
    assert inst.data.config["inputMapping"] == {"quotes": "other_q", "rates": "other_r"}
    orig = next(n for n in parsed.nodes if n.id == "joiner")
    assert orig.data.config["steps"] == GOLDEN_STEPS
    results = execute_graph(
        parsed, target_node_id="joiner_inst", execution_context=_capped_context()
    )
    assert results["joiner_inst"].status == "ok", results["joiner_inst"].error
    assert results["joiner_inst"].preview[0]["band"] == "high"


def test_stepped_original_rejects_input_mapping(tmp_path: Path) -> None:
    quotes, rates = _frames(tmp_path)
    node = _stepped("t", [source()], inputMapping={"quotes": "quotes"})
    graph = PipelineGraph(nodes=[quotes, rates, node], edges=[make_edge("quotes", "t")])
    with pytest.raises(ConfigError, match="cannot carry inputMapping"):
        graph_to_code(graph, pipeline_name="main")
    with pytest.raises(ConfigError, match="cannot carry inputMapping"):
        _build_node_fn(node, source_names=["quotes"], source_ids=["quotes"])


# ---------------------------------------------------------------------------
# A failed plan names its step
# ---------------------------------------------------------------------------


@pytest.fixture
def replays(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    """The length of every step list the builders render and every program they run."""
    import haute._builders as builders

    seen: dict[str, list[Any]] = {"rendered": [], "run": []}
    real_render, real_run = builders.render_polars_steps, builders._exec_user_code

    def render(steps: Any, *args: Any, **kwargs: Any) -> RenderedSteps:
        seen["rendered"].append(len(steps))
        return real_render(steps, *args, **kwargs)

    def run(code: str, *args: Any, **kwargs: Any) -> pl.LazyFrame:
        seen["run"].append(code)
        return real_run(code, *args, **kwargs)

    monkeypatch.setattr(builders, "render_polars_steps", render)
    monkeypatch.setattr(builders, "_exec_user_code", run)
    return seen


def _no_prefix_ran(replays: dict[str, list[Any]], steps: list[dict[str, Any]], code: str) -> bool:
    return all(n >= len(steps) for n in replays["rendered"]) and set(replays["run"]) <= {code}


#: A multi-line derived column, then a filter whose second condition names a
#: column the input does not have: the plan fails only when its schema resolves.
MISSING_COLUMN_STEPS: list[dict[str, Any]] = [
    source(),
    step(
        "w",
        "with_column",
        name="premium_including_insurance_premium_tax_at_twelve_percent",
        expr=binary(col("premium"), "*", num(1.12)),
    ),
    step(
        "f",
        "filter",
        match="all",
        conditions=[
            cond("premium", "gt", value=num(100)),
            cond("number_of_claims_in_the_last_five_years", "gt", value=num(2)),
        ],
    ),
    step("l", "limit", n=2),
]


def test_a_plan_error_is_located_on_the_step_that_caused_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replays: dict[str, list[Any]]
) -> None:
    from haute import _graph_walker
    from haute.executor import _preview_cache

    quotes, _rates = _frames(tmp_path)
    rendered = render_polars_steps(MISSING_COLUMN_STEPS, ["quotes"], start="input")
    first, last = rendered.step_lines[2]
    assert (first, last) == (7, 10)
    graph = PipelineGraph(
        nodes=[quotes, _stepped("t", MISSING_COLUMN_STEPS)], edges=[make_edge("quotes", "t")]
    )
    located = execute_graph(graph, target_node_id="t", execution_context=_capped_context())["t"]
    assert located.status == "error"
    assert "number_of_claims_in_the_last_five_years" in str(located.error)
    assert located.error_line == first
    assert any(n < len(MISSING_COLUMN_STEPS) for n in replays["rendered"])

    # Only the line is added: without the locator the run reports the same message.
    _preview_cache.clear()
    monkeypatch.setattr(_graph_walker, "_located_step_line", lambda *_args: None)
    unlocated = execute_graph(graph, target_node_id="t", execution_context=_capped_context())["t"]
    assert unlocated.error == located.error
    assert unlocated.error_line is None


def test_the_step_locator_resolves_the_whole_plan_before_any_prefix(
    replays: dict[str, list[Any]],
) -> None:
    frame = pl.LazyFrame({"premium": [150.0], "region": ["north"]})
    _name, fn, _is_source = _build_node_fn(
        _stepped("t", MISSING_COLUMN_STEPS), source_names=["quotes"], source_ids=["quotes"]
    )
    assert fn.failed_step_line(frame) == 7
    # A strict cast meeting a bad value fails only on data: its plan resolves.
    cast = [source(), step("c", "cast", casts=[{"column": "region", "dtype": "Int64"}])]
    _name, fn, _is_source = _build_node_fn(
        _stepped("c", cast), source_names=["quotes"], source_ids=["quotes"]
    )
    replays["rendered"].clear()
    replays["run"].clear()
    assert fn.failed_step_line(frame) is None
    assert replays["rendered"] == [len(cast)]
    assert replays["run"] == [render_polars_steps(cast, start="input").code]


def test_a_strict_cast_data_error_keeps_no_line(
    tmp_path: Path, replays: dict[str, list[Any]]
) -> None:
    quotes, _rates = _frames(tmp_path)
    steps = [source(), step("c", "cast", casts=[{"column": "region", "dtype": "Int64"}])]
    graph = PipelineGraph(nodes=[quotes, _stepped("t", steps)], edges=[make_edge("quotes", "t")])
    result = execute_graph(graph, target_node_id="t", execution_context=_capped_context())["t"]
    assert result.status == "error"
    assert "region" in str(result.error)
    assert result.error_line is None
    assert _no_prefix_ran(replays, steps, render_polars_steps(steps, start="input").code)


def test_a_free_code_step_keeps_the_failure_unlocated(
    tmp_path: Path, replays: dict[str, list[Any]]
) -> None:
    quotes, _rates = _frames(tmp_path)
    steps = [*MISSING_COLUMN_STEPS, step("c", "free_code", code="df = df.head(1)")]
    graph = PipelineGraph(nodes=[quotes, _stepped("t", steps)], edges=[make_edge("quotes", "t")])
    result = execute_graph(graph, target_node_id="t", execution_context=_capped_context())["t"]
    assert result.status == "error"
    assert "number_of_claims_in_the_last_five_years" in str(result.error)
    assert result.error_line is None
    assert _no_prefix_ran(replays, steps, render_polars_steps(steps, start="input").code)


def test_a_data_input_step_failure_stays_unlocated(
    tmp_path: Path, replays: dict[str, list[Any]]
) -> None:
    quotes, _rates = _frames(tmp_path)
    steps = MISSING_COLUMN_STEPS[1:]
    result = execute_graph(PipelineGraph(nodes=[_stepped_input(quotes, steps)], edges=[]))["quotes"]
    assert result.status == "error"
    assert "number_of_claims_in_the_last_five_years" in str(result.error)
    assert result.error_line is None
    assert _no_prefix_ran(replays, steps, render_polars_steps(steps, start="frame").code)


def test_a_successful_stepped_run_replays_nothing(
    tmp_path: Path, replays: dict[str, list[Any]]
) -> None:
    quotes, _rates = _frames(tmp_path)
    steps = [*MISSING_COLUMN_STEPS[:2], step("l", "limit", n=2)]
    code = render_polars_steps(steps, start="input").code
    graph = PipelineGraph(nodes=[quotes, _stepped("t", steps)], edges=[make_edge("quotes", "t")])
    result = execute_graph(graph, target_node_id="t", execution_context=_capped_context())["t"]
    assert result.status == "ok", result.error
    assert result.error_line is None
    assert replays["run"] == [code]
    assert _no_prefix_ran(replays, steps, code)


# ---------------------------------------------------------------------------
# Codegen, parse, sidecars
# ---------------------------------------------------------------------------


def _write_sidecars(root: Path, graph: PipelineGraph) -> None:
    for rel, content in collect_node_configs(graph).items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def test_codegen_parse_round_trip_reproduces_rendered_code(tmp_path: Path) -> None:
    quotes, rates = _frames(tmp_path)
    graph = PipelineGraph(
        nodes=[quotes, rates, _stepped("t", GOLDEN_STEPS)],
        edges=[make_edge("quotes", "t"), make_edge("rates", "t")],
    )
    code = graph_to_code(graph, pipeline_name="main")
    assert '@pipeline.polars(config="config/polars/t.json"' in code
    assert "\n    df = quotes\n" in code
    _write_sidecars(tmp_path, graph)
    assert (tmp_path / "config" / "polars" / "t.json").is_file()

    parsed = parse_pipeline_source(code, _base_dir=tmp_path)
    node = next(n for n in parsed.nodes if n.id == "t")
    assert node.data.config["steps"] == GOLDEN_STEPS
    assert node.data.config["code"] == GOLDEN_CODE
    assert "_steps_discarded" not in node.data.config


def test_free_code_executes_between_low_code_steps_and_round_trips(tmp_path: Path) -> None:
    quotes, _rates = _frames(tmp_path)
    snippet = 'df = df.with_columns(\n    (pl.col("premium") * factor).alias("adjusted")\n)\n'
    steps = [
        source(),
        step("v", "variable", name="factor", value=num(2)),
        step("c", "free_code", code=snippet),
        step("sel", "select", columns=["adjusted"]),
        step("l", "limit", n=2),
    ]
    graph = PipelineGraph(nodes=[quotes, _stepped("t", steps)], edges=[make_edge("quotes", "t")])
    expected_code = render_polars_steps(steps, ["quotes"], start="input").code
    generated = graph_to_code(graph, pipeline_name="main")
    _write_sidecars(tmp_path, graph)
    parsed = parse_pipeline_source(generated, _base_dir=tmp_path)
    node = next(n for n in parsed.nodes if n.id == "t")
    assert node.data.config["steps"] == steps
    assert node.data.config["code"] == expected_code
    assert "_steps_discarded" not in node.data.config
    result = execute_graph(parsed, target_node_id="t", execution_context=_capped_context())["t"]
    assert result.status == "ok", result.error
    assert [c.name for c in result.columns] == ["adjusted"]
    original = execute_graph(graph, target_node_id="quotes", execution_context=_capped_context())[
        "quotes"
    ]
    assert result.preview == [{"adjusted": row["premium"] * 2} for row in original.preview[:2]]


def test_free_code_round_trip_keeps_helper_returns_and_trailing_comments(tmp_path: Path) -> None:
    quotes, _rates = _frames(tmp_path)
    snippet = "def unchanged(frame):\n    return frame\n\ndf = unchanged(df)\n# End\n\n"
    steps = [source(), step("c", "free_code", code=snippet)]
    graph = PipelineGraph(nodes=[quotes, _stepped("t", steps)], edges=[make_edge("quotes", "t")])
    _write_sidecars(tmp_path, graph)
    parsed = parse_pipeline_source(graph_to_code(graph, pipeline_name="main"), _base_dir=tmp_path)
    node = next(n for n in parsed.nodes if n.id == "t")
    assert node.data.config["steps"] == steps
    assert node.data.config["code"] == render_polars_steps(steps, start="input").code


@pytest.mark.parametrize(
    ("snippet", "line"),
    [("df = df.head(2)\nraise ValueError('free code failed')", 3), ("# Invalid frame\ndf = 1", 4)],
)
def test_free_code_runtime_errors_use_rendered_lines(
    tmp_path: Path, snippet: str, line: int
) -> None:
    quotes, _rates = _frames(tmp_path)
    steps = [source(), step("c", "free_code", code=snippet), step("l", "limit", n=1)]
    graph = PipelineGraph(nodes=[quotes, _stepped("t", steps)], edges=[make_edge("quotes", "t")])
    result = execute_graph(graph, target_node_id="t", execution_context=_capped_context())["t"]
    assert result.status == "error"
    assert result.error_line == line


def test_parse_discards_steps_when_body_was_hand_edited(tmp_path: Path) -> None:
    quotes, rates = _frames(tmp_path)
    graph = PipelineGraph(
        nodes=[quotes, rates, _stepped("t", GOLDEN_STEPS)],
        edges=[make_edge("quotes", "t"), make_edge("rates", "t")],
    )
    code = graph_to_code(graph, pipeline_name="main")
    _write_sidecars(tmp_path, graph)
    edited = code.replace("df = df.head", "df = df.head").replace(
        "    ipt_rate = 0.12\n", "    ipt_rate = 0.2\n"
    )
    assert edited != code

    parsed = parse_pipeline_source(edited, _base_dir=tmp_path)
    node = next(n for n in parsed.nodes if n.id == "t")
    assert "steps" not in node.data.config
    assert node.data.config["code"] == GOLDEN_CODE.replace("0.12", "0.2")
    assert node.data.config["_steps_discarded"].startswith("Steps were discarded because")
    assert node.data.config["_discarded_sidecar"] == "config/polars/t.json"


def test_parse_keeps_steps_whose_body_is_the_same_program_in_an_older_style(
    tmp_path: Path,
) -> None:
    """A body saved with other quotes, brackets or line breaks still describes its steps."""
    quotes, _rates = _frames(tmp_path)
    steps = [
        source(),
        step("f", "filter", match="all", conditions=[cond("premium", "gt", value=num(100))]),
    ]
    graph = PipelineGraph(nodes=[quotes, _stepped("t", steps)], edges=[make_edge("quotes", "t")])
    code = graph_to_code(graph, pipeline_name="main")
    _write_sidecars(tmp_path, graph)
    styled = 'df = df.filter(pl.col("premium") > 100)'
    assert styled in code
    older = code.replace(styled, "df = df.filter((pl.col('premium') > 100))")

    parsed = parse_pipeline_source(older, _base_dir=tmp_path)
    node = next(n for n in parsed.nodes if n.id == "t")
    assert node.data.config["steps"] == steps
    assert "_steps_discarded" not in node.data.config
    assert node.data.config["code"] == render_polars_steps(steps, start="input").code


#: The golden steps as the renderer wrote them before its current layout and
#: style: one line per step, ``repr`` quotes, lists for keys and aggregations.
EARLIER_GOLDEN_CODE = "\n".join(
    [
        "df = quotes",
        "ipt_rate = 0.12",
        "df = df.filter((pl.col('premium') > 100) & (pl.col('region').is_in(['north', 'south'])))",
        "df = df.with_columns((pl.col('premium') * ipt_rate).alias('gross'))",
        "df = df.with_columns((pl.when((pl.col('gross') >= 500)).then(pl.lit('high'))"
        ".otherwise(pl.lit('low'))).alias('band'))",
        "df = df.join(rates, left_on=['region'], right_on=['region'], how='left', suffix='_rate')",
        "df = df.group_by(['band'], maintain_order=True).agg([pl.col('gross').sum()"
        ".alias('gross_total'), pl.len().alias('rows')])",
    ]
)


def test_parse_keeps_steps_saved_by_an_earlier_renderer(tmp_path: Path) -> None:
    quotes, rates = _frames(tmp_path)
    graph = PipelineGraph(
        nodes=[quotes, rates, _stepped("t", GOLDEN_STEPS)],
        edges=[make_edge("quotes", "t"), make_edge("rates", "t")],
    )
    code = graph_to_code(graph, pipeline_name="main")
    _write_sidecars(tmp_path, graph)
    current = textwrap.indent(GOLDEN_CODE, "    ")
    assert current in code
    earlier = code.replace(current, textwrap.indent(EARLIER_GOLDEN_CODE, "    "))

    parsed = parse_pipeline_source(earlier, _base_dir=tmp_path)
    node = next(n for n in parsed.nodes if n.id == "t")
    assert node.data.config["steps"] == GOLDEN_STEPS
    assert "_steps_discarded" not in node.data.config
    assert node.data.config["code"] == GOLDEN_CODE


@pytest.mark.parametrize(
    ("kind_step", "earlier"),
    [
        (step("x", "select", columns=["a", "b"]), "df = df.select(['a', 'b'])"),
        (
            step("x", "select", columns=["region"], dtypes=["Float64"]),
            "df = df.select(['region', pl.col(pl.Float64).exclude('region')])",
        ),
        (step("x", "drop", columns=["a"]), "df = df.drop(['a'])"),
        (
            step("x", "drop", columns=["region"], dtypes=["String"]),
            "df = df.drop(['region'], pl.col(pl.String).exclude('region'))",
        ),
        (
            step("x", "sort", keys=[{"column": "a", "descending": True}], nullsLast=True),
            "df = df.sort(['a'], descending=[True], nulls_last=True)",
        ),
        (
            step("x", "sort", keys=[{"column": "a", "descending": False}], nullsLast=False),
            "df = df.sort(['a'], descending=[False], nulls_last=False)",
        ),
        (
            step("x", "unique", columns=[], keep="any"),
            "df = df.unique(subset=None, keep='any', maintain_order=True)",
        ),
        (
            step("x", "fill_null", columns=["a"], fill={"kind": "value", "value": num(0)}),
            "df = df.with_columns(pl.col(['a']).fill_null(0))",
        ),
        (
            step(
                "x",
                "group_by",
                keys=["k"],
                aggregations=[{"column": "a", "agg": "sum", "name": "t"}],
            ),
            "df = df.group_by(['k'], maintain_order=True).agg([pl.col('a').sum().alias('t')])",
        ),
        (
            step(
                "x", "group_by", keys=[], aggregations=[{"column": "", "agg": "len", "name": "n"}]
            ),
            "df = df.select([pl.len().alias('n')])",
        ),
    ],
)
def test_the_earlier_spelling_is_the_renderer_s_previous_output(
    kind_step: dict[str, Any], earlier: str
) -> None:
    """The earlier spelling reproduces what the renderer wrote before, as a program."""
    rendered = render_polars_steps(
        [source(), kind_step], ["quotes"], start="input", spelling="earlier"
    )
    assert ast.dump(ast.parse(step_text(rendered, 1))) == ast.dump(ast.parse(earlier))
    current = render_polars_steps([source(), kind_step], ["quotes"], start="input")
    assert step_text(current, 1) != step_text(rendered, 1)


@pytest.mark.parametrize(
    ("steps", "edit"),
    [
        # A list beside another argument is not the same call as the names passed one by one.
        (
            [source(), step("x", "select", columns=["premium", "region"])],
            ('df = df.select("premium", "region")', "df = df.select(['premium'], 'region')"),
        ),
        # Authored code must match exactly, even where Polars would read it the same.
        (
            [source(), step("c", "free_code", code="df = df.select(['premium'])")],
            ("df = df.select(['premium'])", "df = df.select('premium')"),
        ),
    ],
)
def test_parse_discards_steps_for_a_body_no_spelling_of_them_renders(
    tmp_path: Path, steps: list[dict[str, Any]], edit: tuple[str, str]
) -> None:
    quotes, _rates = _frames(tmp_path)
    graph = PipelineGraph(nodes=[quotes, _stepped("t", steps)], edges=[make_edge("quotes", "t")])
    code = graph_to_code(graph, pipeline_name="main")
    _write_sidecars(tmp_path, graph)
    old, new = edit
    assert old in code
    parsed = parse_pipeline_source(code.replace(old, new), _base_dir=tmp_path)
    node = next(n for n in parsed.nodes if n.id == "t")
    assert "steps" not in node.data.config
    assert node.data.config["_steps_discarded"].startswith("Steps were discarded because")


def test_parse_discards_steps_when_only_a_free_code_comment_was_edited(tmp_path: Path) -> None:
    quotes, _rates = _frames(tmp_path)
    steps = [source(), step("c", "free_code", code="# keep two rows\ndf = df.head(2)")]
    graph = PipelineGraph(nodes=[quotes, _stepped("t", steps)], edges=[make_edge("quotes", "t")])
    code = graph_to_code(graph, pipeline_name="main")
    _write_sidecars(tmp_path, graph)
    edited = code.replace("# keep two rows", "# keep the first two rows")
    assert edited != code

    parsed = parse_pipeline_source(edited, _base_dir=tmp_path)
    node = next(n for n in parsed.nodes if n.id == "t")
    assert "steps" not in node.data.config
    assert node.data.config["_steps_discarded"].startswith("Steps were discarded because")


def test_parse_keeps_incomplete_steps_saved_through_the_placeholder(tmp_path: Path) -> None:
    quotes, _rates = _frames(tmp_path)
    incomplete = [source(), step("f", "filter", match="all", conditions=[])]
    graph = PipelineGraph(
        nodes=[quotes, _stepped("t", incomplete)], edges=[make_edge("quotes", "t")]
    )
    code = graph_to_code(graph, pipeline_name="main")
    assert INCOMPLETE_TRANSFORM_MESSAGE in code
    _write_sidecars(tmp_path, graph)

    parsed = parse_pipeline_source(code, _base_dir=tmp_path)
    node = next(n for n in parsed.nodes if n.id == "t")
    assert node.data.config["steps"] == incomplete
    assert node.data.config["code"] == ""
    assert node.data.config["_steps_error"] == "Step 2: Add at least one condition."


def test_parse_rejects_malformed_steps_sidecar(tmp_path: Path) -> None:
    quotes, _rates = _frames(tmp_path)
    graph = PipelineGraph(
        nodes=[quotes, _stepped("t", [source()])], edges=[make_edge("quotes", "t")]
    )
    code = graph_to_code(graph, pipeline_name="main")
    _write_sidecars(tmp_path, graph)
    sidecar = tmp_path / "config" / "polars" / "t.json"
    sidecar.write_text(json.dumps({"steps": {"kind": "source"}}), encoding="utf-8")
    with pytest.raises(ConfigError, match="must be a list"):
        parse_pipeline_source(code, _base_dir=tmp_path)


def test_collect_node_configs_writes_polars_sidecar_only_with_steps() -> None:
    stepped = _stepped("stepped", [source()])
    coded = GraphNode(
        id="coded", data=NodeData(label="coded", nodeType="polars", config={"code": "df = quotes"})
    )
    graph = PipelineGraph(nodes=[stepped, coded], edges=[])
    configs = collect_node_configs(graph)
    assert set(configs) == {"config/polars/stepped.json"}
    assert json.loads(configs["config/polars/stepped.json"]) == {"steps": [source()]}
    assert node_emits_sidecar(stepped) and not node_emits_sidecar(coded)


# ---------------------------------------------------------------------------
# Save service
# ---------------------------------------------------------------------------


@pytest.fixture()
def project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _save(root: Path, graph: PipelineGraph) -> list[str]:
    req = SavePipelineRequest(
        name="main",
        graph=graph,
        source_file="main.py",
        base_revision=current_source_revision(root / "main.py", root),
    )
    return list(SavePipelineService(root).save(req).warnings)


def _project_graph(
    root: Path, steps: list[dict[str, Any]] | None, *, code: str | None = None
) -> PipelineGraph:
    quotes, rates = _frames(root)
    config: dict[str, Any] = {"steps": steps} if steps is not None else {"code": code or ""}
    node = GraphNode(id="t", data=NodeData(label="t", nodeType="polars", config=config))
    return PipelineGraph(
        nodes=[quotes, rates, node], edges=[make_edge("quotes", "t"), make_edge("rates", "t")]
    )


def test_save_writes_then_retires_polars_sidecar_on_switch_to_code(project_root: Path) -> None:
    sidecar = project_root / "config" / "polars" / "t.json"
    assert _save(project_root, _project_graph(project_root, GOLDEN_STEPS)) == []
    assert json.loads(sidecar.read_text(encoding="utf-8"))["steps"] == GOLDEN_STEPS

    _save(project_root, _project_graph(project_root, None, code=GOLDEN_CODE))
    assert not sidecar.exists()
    assert 'config="config/polars' not in (project_root / "main.py").read_text(encoding="utf-8")


def test_hand_edit_then_load_then_save_retires_discarded_sidecar(project_root: Path) -> None:
    sidecar = project_root / "config" / "polars" / "t.json"
    _save(project_root, _project_graph(project_root, GOLDEN_STEPS))
    main = project_root / "main.py"
    main.write_text(
        main.read_text(encoding="utf-8").replace("ipt_rate = 0.12", "ipt_rate = 0.5"),
        encoding="utf-8",
    )

    loaded = parse_pipeline_file(main)
    node = next(n for n in loaded.nodes if n.id == "t")
    assert "steps" not in node.data.config
    assert node.data.config["_discarded_sidecar"] == "config/polars/t.json"
    assert sidecar.exists()

    _save(project_root, loaded)
    assert not sidecar.exists()
    assert "ipt_rate = 0.5" in main.read_text(encoding="utf-8")


def test_save_rejects_case_colliding_stepped_transforms(project_root: Path) -> None:
    quotes, _rates = _frames(project_root)
    graph = PipelineGraph(
        nodes=[quotes, _stepped("Foo", [source()]), _stepped("foo", [source()])],
        edges=[make_edge("quotes", "Foo"), make_edge("quotes", "foo")],
    )
    with pytest.raises(Exception, match="(?i)collid|conflict|same"):
        _save(project_root, graph)
    assert not (project_root / "config" / "polars").exists()


def test_save_warns_about_incomplete_step_list(project_root: Path) -> None:
    incomplete = [source(), step("f", "filter", match="all", conditions=[])]
    warnings = _save(project_root, _project_graph(project_root, incomplete))
    assert any("incomplete step list (Step 2: Add at least one condition.)" in w for w in warnings)
    unknown = _save(project_root, _project_graph(project_root, [source("policies")]))
    assert any("Unknown input 'policies'" in w for w in unknown)


def test_node_scoped_save_updates_the_polars_sidecar(tmp_path: Path) -> None:
    from haute._pipeline_recovery import load_pipeline_editor_document
    from haute._pipeline_repair_actions import apply_scoped_node_save
    from haute.schemas import PipelineNodeSaveRequest

    # Scoped saves exist for degraded documents: a broken input upstream makes
    # the stepped transform "blocked" and scoped-editable.
    (tmp_path / "haute.toml").write_text('[project]\nname="demo"\n', encoding="utf-8")
    (tmp_path / "a.json").write_text(
        json.dumps(
            {
                "inputType": "file",
                "format": "parquet",
                "mode": "scan",
                "path": "",
                "arguments": {},
                "code": "",
                "cacheMode": "snapshot",
            }
        ),
        encoding="utf-8",
    )
    steps = [source("source_a"), step("l", "limit", n=5)]
    sidecar = tmp_path / "config" / "polars" / "transform.json"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text(json.dumps({"steps": steps}), encoding="utf-8")
    main = tmp_path / "main.py"
    main.write_text(
        "import haute\nimport polars as pl\n"
        'pipeline = haute.Pipeline("demo")\n'
        '@pipeline.data_input(config="a.json")\ndef source_a():\n    return None\n'
        '@pipeline.polars(config="config/polars/transform.json")\n'
        "def transform(source_a: pl.LazyFrame) -> pl.LazyFrame:\n"
        "    df: pl.LazyFrame\n    df = source_a\n    df = df.head(5)\n    return df\n",
        encoding="utf-8",
    )
    document = load_pipeline_editor_document(main, project_root=tmp_path)
    target = next(node for node in document.nodes if node.authored_id == "transform")
    assert target.availability == "blocked"
    assert target.scoped_editable is True
    assert target.config["steps"] == steps

    new_steps = [source("source_a"), step("l", "limit", n=3)]
    request = PipelineNodeSaveRequest(
        source_file=document.source_file,
        source_revision=document.source_revision,
        target_source_file=target.source_file,
        target_recovery_id=target.recovery_id,
        config={**target.config, "steps": new_steps},
    )
    apply_scoped_node_save(project_root=tmp_path, request=request)

    assert json.loads(sidecar.read_text(encoding="utf-8"))["steps"] == new_steps
    assert "df = df.head(3)" in main.read_text(encoding="utf-8")
    reloaded = load_pipeline_editor_document(main, project_root=tmp_path)
    node = next(item for item in reloaded.nodes if item.authored_id == "transform")
    assert node.config["steps"] == new_steps
    assert "_steps_discarded" not in node.config


# ---------------------------------------------------------------------------
# Submodel flattening
# ---------------------------------------------------------------------------


def test_flatten_rewrites_stepped_consumer_inputs() -> None:
    child = PipelineGraph(
        nodes=[
            GraphNode(
                id="output", data=NodeData(label="Internal Output", nodeType="polars", config={})
            )
        ],
        edges=[],
    )
    definition = SubmodelDefinition(
        definition_id="definition_scoring",
        file="modules/scoring.py",
        graph=child,
        input_ports=[],
        output_ports=[
            SubmodelOutputPort(name="results", source=SubmodelEndpoint(node_id="output"))
        ],
    )
    instance = GraphNode(
        id="instance_a",
        data=NodeData(
            label="score",
            nodeType="submodel",
            config={"definitionId": "definition_scoring", "alias": "score"},
        ),
    )
    consumer = GraphNode(
        id="consumer",
        data=NodeData(label="Consumer", nodeType="polars", config={"steps": [source("results")]}),
    )
    graph = PipelineGraph(
        nodes=[instance, consumer],
        edges=[
            GraphEdge(
                id="output", source="instance_a", target="consumer", sourceHandle="out__results"
            )
        ],
        submodels={"definition_scoring": definition},
    )

    result = flatten_graph(graph)
    flat = result.node_map["consumer"].data.config
    assert flat["steps"] == [source("Internal_Output")]
    assert "inputMapping" not in flat
    assert flat["code"] == "df = Internal_Output"
    generated = graph_to_code_multi(result, pipeline_name="main")["main.py"]
    assert "def Consumer(Internal_Output: pl.LazyFrame)" in generated
    assert "df = Internal_Output" in generated


def test_flatten_rewrites_internal_stepped_consumer_and_executes(tmp_path: Path) -> None:
    quotes, _rates = _frames(tmp_path)
    inner = GraphNode(
        id="inner",
        data=NodeData(
            label="Inner",
            nodeType="polars",
            config={"steps": [source("policy"), step("l", "limit", n=2)]},
        ),
    )
    child = PipelineGraph(nodes=[inner], edges=[])
    definition = SubmodelDefinition(
        definition_id="definition_scoring",
        file="modules/scoring.py",
        graph=child,
        input_ports=[
            SubmodelInputPort(
                name="policy", targets=[SubmodelEndpoint(node_id="inner", handle_id="frame")]
            )
        ],
        output_ports=[
            SubmodelOutputPort(
                name="result", source=SubmodelEndpoint(node_id="inner", handle_id="scored")
            )
        ],
    )
    instance = GraphNode(
        id="instance_a",
        data=NodeData(
            label="score",
            nodeType="submodel",
            config={"definitionId": "definition_scoring", "alias": "score"},
        ),
    )
    consumer = GraphNode(
        id="consumer",
        data=NodeData(
            label="Consumer",
            nodeType="polars",
            config={"steps": [source("result"), step("s2", "select", columns=["premium"])]},
        ),
    )
    graph = PipelineGraph(
        nodes=[quotes, instance, consumer],
        edges=[
            GraphEdge(id="in", source="quotes", target="instance_a", targetHandle="in__policy"),
            GraphEdge(id="out", source="instance_a", target="consumer", sourceHandle="out__result"),
        ],
        submodels={"definition_scoring": definition},
    )

    flat = flatten_graph(graph)
    inner_flat = next(n for n in flat.nodes if n.data.label == "Inner")
    consumer_flat = flat.node_map["consumer"]
    assert inner_flat.data.config["steps"][0]["input"] == "quotes"
    assert "inputMapping" not in inner_flat.data.config
    assert consumer_flat.data.config["steps"][0]["input"] == _sanitize_label(inner_flat.data.label)
    assert "inputMapping" not in consumer_flat.data.config

    results = execute_graph(flat, target_node_id="consumer")
    assert results["consumer"].status == "ok", results["consumer"].error
    assert [c.name for c in results["consumer"].columns] == ["premium"]
    assert [row["premium"] for row in results["consumer"].preview] == [50.0, 200.0]


# ---------------------------------------------------------------------------
# Render endpoint
# ---------------------------------------------------------------------------


def test_render_endpoint_reports_invalid_step_as_data(client: TestClient) -> None:
    ok = client.post(
        "/api/pipeline/polars-steps/render",
        json={"steps": GOLDEN_STEPS, "input_names": ["quotes", "rates"], "start": "input"},
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["ok"] is True
    assert body["code"] == GOLDEN_CODE
    assert body["step_lines"][:2] == [[1, 1], [2, 2]]

    bad = client.post(
        "/api/pipeline/polars-steps/render",
        json={
            "steps": [source(), step("f", "filter", match="all", conditions=[])],
            "input_names": ["quotes"],
            "start": "input",
        },
    )
    assert bad.status_code == 200
    assert bad.json() == {
        "ok": False,
        "code": "",
        "step_lines": [],
        "step_index": 1,
        "message": "Add at least one condition.",
    }

    malformed = client.post("/api/pipeline/polars-steps/render", json={"steps": "nope"})
    assert malformed.status_code == 422


def test_render_endpoint_returns_free_code_line_ranges_and_errors(client: TestClient) -> None:
    steps = [
        source(),
        step("c", "free_code", code="df = df.with_columns(\n    pl.lit(1).alias('x')\n)"),
        step("l", "limit", n=2),
    ]
    response = client.post(
        "/api/pipeline/polars-steps/render",
        json={"steps": steps, "input_names": ["quotes"], "start": "input"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["step_lines"] == [[1, 1], [2, 4], [5, 5]]
    steps[1]["code"] = "return df"
    invalid = client.post(
        "/api/pipeline/polars-steps/render",
        json={"steps": steps, "input_names": ["quotes"], "start": "input"},
    )
    assert invalid.status_code == 200
    assert invalid.json()["ok"] is False
    assert invalid.json()["step_index"] == 1


# ---------------------------------------------------------------------------
# Frame-start surfaces: Data Input
# ---------------------------------------------------------------------------


def _stepped_input(node: GraphNode, steps: list[dict[str, Any]], **extra: Any) -> GraphNode:
    """A ready Data Input whose post-load code is authored as *steps*."""
    return node.with_config({**node.data.config, "steps": steps, **extra})


def _error_chain(exc: BaseException) -> str:
    parts: list[str] = []
    current: BaseException | None = exc
    while current is not None:
        parts.append(str(current))
        current = current.__cause__ or current.__context__
    return " | ".join(parts)


def test_frame_start_renders_without_a_source_step() -> None:
    assert render_polars_steps([], [], start="frame") == RenderedSteps(code="", step_lines=())
    rendered = render_polars_steps([step("l", "limit", n=3)], [], start="frame")
    assert rendered.code == "df = df.head(3)"
    assert rendered.step_lines == ((1, 1),)

    with pytest.raises(PolarsStepError) as refused:
        render_polars_steps([step("l", "limit", n=3), source()], [], start="frame")
    assert refused.value.step_index == 1
    assert refused.value.message == "This node starts from df; remove the start step."

    join = step(
        "j", "join", input="rates", how="inner", leftOn=["region"], rightOn=["region"], suffix="_r"
    )
    with pytest.raises(PolarsStepError, match="Unknown input 'rates'; connected inputs: none"):
        render_polars_steps([join], [], start="frame")

    # Every other kind renders byte-identically in both modes.
    frame = render_polars_steps(GOLDEN_STEPS[1:], ["quotes", "rates"], start="frame")
    assert frame.code == "\n".join(GOLDEN_CODE.splitlines()[1:])
    assert frame.step_lines == tuple((a - 1, b - 1) for a, b in GOLDEN_STEP_LINES[1:])

    with pytest.raises(TypeError):
        render_polars_steps(GOLDEN_STEPS)  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="Unknown step start"):
        render_polars_steps(GOLDEN_STEPS, start="nowhere")  # type: ignore[arg-type]
    with pytest.raises(PolarsStepError, match="Choose the input to start from"):
        render_polars_steps([], start="input")


def test_stepped_surface_table_and_input_eligibility() -> None:
    assert stepped_surface_for(NodeType.POLARS) == SteppedSurface(start="input", inputs="edges")
    assert stepped_surface_for(NodeType.DATA_INPUT) == SteppedSurface(start="frame", inputs="none")
    assert step_input_names(NodeType.POLARS, ["quotes", "rates"]) == ["quotes", "rates"]
    assert step_input_names(NodeType.DATA_INPUT, ["quotes"]) == []
    with pytest.raises(ValueError, match="does not author steps"):
        step_input_names(NodeType.OUTPUT, [])
    assert is_stepped_config(NodeType.DATA_INPUT, {"steps": []})
    assert not is_stepped_config(NodeType.DATA_INPUT, {"code": ""})
    assert not is_stepped_config(NodeType.SCENARIO_EXPANDER, {"steps": 3})


def test_node_data_materialises_data_input_steps() -> None:
    base = make_file_input_config("quotes.parquet")

    def node(steps: object) -> GraphNode:
        return GraphNode(
            id="d",
            data=NodeData(
                label="d", nodeType="dataInput", config={**base, "steps": steps, "code": "stale"}
            ),
        )

    assert node([]).data.config["code"] == ""
    assert "_steps_error" not in node([]).data.config
    assert node([step("l", "limit", n=2)]).data.config["code"] == "df = df.head(2)"
    broken = node([source()])
    assert broken.data.config["code"] == ""
    assert (
        broken.data.config["_steps_error"]
        == "Step 1: This node starts from df; remove the start step."
    )
    with pytest.raises(ValueError, match="must be a list"):
        node("not-a-list")

    # A Data Input has no eligible input names, so a reference to one is an
    # error at materialisation, not only at build time.
    join = step(
        "j", "join", input="rates", how="inner", leftOn=["region"], rightOn=["region"], suffix="_r"
    )
    joined = node([join])
    assert joined.data.config["code"] == ""
    assert (
        joined.data.config["_steps_error"]
        == "Step 1: Unknown input 'rates'; connected inputs: none."
    )
    stacked = node([step("c", "concat", inputs=["rates"], how="vertical")])
    assert stacked.data.config["code"] == ""
    assert stacked.data.config["_steps_error"].startswith("Step 1: Unknown input 'rates'")

    # A type outside the surface table keeps a `steps` key untouched; a
    # stepped type refuses anything but a list (the Scenario Expander's grid
    # size lives under `stepCount`).
    other = GraphNode(id="o", data=NodeData(label="o", nodeType="output", config={"steps": 5}))
    assert other.data.config == {"steps": 5}
    with pytest.raises(ValueError, match="must be a list"):
        GraphNode(
            id="s",
            data=NodeData(label="s", nodeType="scenarioExpander", config={"steps": 5}),
        )


def test_data_input_sidecar_and_validation_carry_steps() -> None:
    config = {**make_file_input_config("quotes.parquet"), "steps": [step("l", "limit", n=2)]}
    assert validate_data_input_config(config)["steps"] == config["steps"]
    databricks = {
        "inputType": "databricks",
        "http_path": "p",
        "table": "t",
        "arguments": {},
        "steps": [],
    }
    assert validate_data_input_config(databricks)["steps"] == []

    node = GraphNode(id="d", data=NodeData(label="d", nodeType="dataInput", config=config))
    assert node_emits_sidecar(node)
    written = json.loads(
        collect_node_configs(PipelineGraph(nodes=[node], edges=[]))["config/data_input/d.json"]
    )
    assert written["steps"] == config["steps"]
    assert "code" not in written


def test_data_input_steps_execute_and_round_trip(tmp_path: Path) -> None:
    quotes, _rates = _frames(tmp_path)
    stepped = _stepped_input(quotes, [step("l", "limit", n=2)])
    graph = PipelineGraph(nodes=[stepped], edges=[])
    result = execute_graph(graph)["quotes"]
    assert result.status == "ok", result.error
    assert len(result.preview) == 2

    code = graph_to_code(graph, pipeline_name="main")
    assert "@pipeline.data_input(" in code
    assert "config/data_input/quotes.json" in code
    assert "\n    df = df.head(2)\n    return df\n" in code
    _write_sidecars(tmp_path, graph)
    sidecar = json.loads(
        (tmp_path / "config" / "data_input" / "quotes.json").read_text(encoding="utf-8")
    )
    assert sidecar["steps"] == [step("l", "limit", n=2)]

    parsed = parse_pipeline_source(code, _base_dir=tmp_path)
    node = next(n for n in parsed.nodes if n.id == "quotes")
    assert node.data.config["steps"] == [step("l", "limit", n=2)]
    assert node.data.config["code"] == "df = df.head(2)"
    assert "_steps_discarded" not in node.data.config
    reloaded = execute_graph(parsed)["quotes"]
    assert reloaded.status == "ok", reloaded.error
    assert len(reloaded.preview) == 2


def test_data_input_incomplete_steps_fail_on_every_path(tmp_path: Path) -> None:
    quotes, _rates = _frames(tmp_path)
    join = step(
        "j", "join", input="rates", how="inner", leftOn=["region"], rightOn=["region"], suffix="_r"
    )
    unknown = _stepped_input(quotes, [join])
    # Materialisation already refuses the reference (no eligible inputs).
    assert unknown.data.config["code"] == ""
    assert (
        unknown.data.config["_steps_error"]
        == "Step 1: Unknown input 'rates'; connected inputs: none."
    )
    graph = PipelineGraph(nodes=[unknown], edges=[])

    # Builder path: the executor names the step.
    result = execute_graph(graph)["quotes"]
    assert result.status == "error"
    assert INCOMPLETE_STEPS_MESSAGE in str(result.error)
    assert "Step 1: Unknown input 'rates'; connected inputs: none." in str(result.error)

    # Generated module: the placeholder replaces the post-load lines and reloads as kept steps.
    code = graph_to_code(graph, pipeline_name="main")
    assert INCOMPLETE_STEPS_MESSAGE in code
    assert "df.join" not in code
    _write_sidecars(tmp_path, graph)
    parsed = parse_pipeline_source(code, _base_dir=tmp_path)
    node = next(n for n in parsed.nodes if n.id == "quotes")
    assert node.data.config["steps"] == [join]
    assert "_steps_discarded" not in node.data.config
    assert execute_graph(parsed)["quotes"].status == "error"

    broken = _stepped_input(quotes, [step("f", "filter", match="all", conditions=[])])
    assert broken.data.config["_steps_error"] == "Step 1: Add at least one condition."
    broken_graph = PipelineGraph(nodes=[broken], edges=[])
    assert "Step 1: Add at least one condition." in str(execute_graph(broken_graph)["quotes"].error)
    broken_code = graph_to_code(broken_graph, pipeline_name="main")
    _write_sidecars(tmp_path, broken_graph)
    reloaded = next(
        n for n in parse_pipeline_source(broken_code, _base_dir=tmp_path).nodes if n.id == "quotes"
    )
    assert reloaded.data.config["steps"] == broken.data.config["steps"]
    assert reloaded.data.config["code"] == ""
    assert reloaded.data.config["_steps_error"] == "Step 1: Add at least one condition."


def _bundled_scoring_graph(steps: list[dict[str, Any]]) -> PipelineGraph:
    return make_graph(
        {
            "nodes": [
                {
                    "id": "src",
                    "data": {"label": "src", "nodeType": "apiInput", "config": {"path": ""}},
                },
                {
                    "id": "static_ds",
                    "data": {
                        "label": "static_ds",
                        "nodeType": "dataInput",
                        "config": {
                            "inputType": "file",
                            "format": "csv",
                            "mode": "scan",
                            "path": "original/factors.csv",
                            "arguments": {},
                            "steps": steps,
                        },
                    },
                },
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": {
                            "outputMapping": [
                                {
                                    "source_port": "static_ds",
                                    "source_column": col,
                                    "output_path": f"$[:].{col}",
                                    "enabled": True,
                                }
                                for col in ("area", "factor")
                            ],
                            "outputFormat": "json",
                        },
                    },
                },
            ],
            "edges": [
                {"id": "e1", "source": "src", "target": "static_ds", "sourceHandle": "src"},
                {"id": "e2", "source": "static_ds", "target": "out"},
            ],
        }
    )


def test_data_input_steps_guard_the_deploy_bundled_snapshot_path(tmp_path: Path) -> None:
    from haute.deploy._scorer import score_graph

    ds_path = tmp_path / "factors.parquet"
    pl.DataFrame({"area": ["A", "B"], "factor": [1.1, 1.2]}).write_parquet(ds_path)
    # A bundled snapshot is its generation's parts, one artifact each, scanned
    # in part order — not a single file.
    remap = {"static_ds__snapshot.part-00000.parquet": str(ds_path)}
    input_df = pl.DataFrame({"x": [1.0]})

    scored = score_graph(
        graph=_bundled_scoring_graph([step("l", "limit", n=1)]),
        input_df=input_df,
        input_node_ids=["src"],
        output_node_id="out",
        artifact_paths=remap,
    )
    assert isinstance(scored, pl.DataFrame)
    assert scored["area"].to_list() == ["A"]

    with pytest.raises(Exception, match=r"Step 1: Add at least one condition") as failed:
        score_graph(
            graph=_bundled_scoring_graph([step("f", "filter", match="all", conditions=[])]),
            input_df=input_df,
            input_node_ids=["src"],
            output_node_id="out",
            artifact_paths=remap,
        )
    chain = _error_chain(failed.value)
    assert INCOMPLETE_STEPS_MESSAGE in chain
    assert "Step 1: Add at least one condition." in chain


def test_data_input_hand_edit_discards_steps_without_sidecar_marker(tmp_path: Path) -> None:
    quotes, _rates = _frames(tmp_path)
    graph = PipelineGraph(nodes=[_stepped_input(quotes, [step("l", "limit", n=2)])], edges=[])
    code = graph_to_code(graph, pipeline_name="main")
    _write_sidecars(tmp_path, graph)
    edited = code.replace("df = df.head(2)", "df = df.head(3)")
    assert edited != code

    parsed = parse_pipeline_source(edited, _base_dir=tmp_path)
    node = next(n for n in parsed.nodes if n.id == "quotes")
    assert "steps" not in node.data.config
    assert node.data.config["code"] == "df = df.head(3)"
    assert node.data.config["_steps_discarded"].startswith("Steps were discarded because")
    assert "_discarded_sidecar" not in node.data.config


def test_data_input_free_code_with_redundant_parentheses_reloads_in_step_mode(
    tmp_path: Path,
) -> None:
    quotes, _rates = _frames(tmp_path)
    steps = [step("c", "free_code", code="df = (df.head(2))")]
    graph = PipelineGraph(nodes=[_stepped_input(quotes, steps)], edges=[])
    code = graph_to_code(graph, pipeline_name="main")
    assert "\n    df = (df.head(2))\n" in code
    _write_sidecars(tmp_path, graph)

    parsed = parse_pipeline_source(code, _base_dir=tmp_path)
    node = next(n for n in parsed.nodes if n.id == "quotes")
    assert node.data.config["steps"] == steps
    assert node.data.config["code"] == "df = (df.head(2))"
    assert "_steps_discarded" not in node.data.config
    result = execute_graph(parsed)["quotes"]
    assert result.status == "ok", result.error
    assert len(result.preview) == 2


def test_save_warns_about_incomplete_data_input_steps(project_root: Path) -> None:
    quotes, rates = _frames(project_root)
    broken = _stepped_input(quotes, [step("f", "filter", match="all", conditions=[])])
    warnings = _save(project_root, PipelineGraph(nodes=[broken, rates], edges=[]))
    assert any(
        "Data Input node 'quotes' has an incomplete step list (Step 1: Add at least one condition.)"
        in w
        for w in warnings
    ), warnings
    complete = _stepped_input(quotes, [step("l", "limit", n=1)])
    assert _save(project_root, PipelineGraph(nodes=[complete, rates], edges=[])) == []


def test_render_endpoint_requires_start_and_renders_frame_mode(client: TestClient) -> None:
    missing = client.post(
        "/api/pipeline/polars-steps/render", json={"steps": [], "input_names": []}
    )
    assert missing.status_code == 422

    empty = client.post(
        "/api/pipeline/polars-steps/render", json={"steps": [], "input_names": [], "start": "frame"}
    )
    assert empty.status_code == 200
    assert empty.json() == {
        "ok": True,
        "code": "",
        "step_lines": [],
        "step_index": None,
        "message": "",
    }

    limited = client.post(
        "/api/pipeline/polars-steps/render",
        json={"steps": [step("l", "limit", n=2)], "input_names": [], "start": "frame"},
    )
    assert limited.json() == {
        "ok": True,
        "code": "df = df.head(2)",
        "step_lines": [[1, 1]],
        "step_index": None,
        "message": "",
    }

    refused = client.post(
        "/api/pipeline/polars-steps/render",
        json={"steps": [source()], "input_names": [], "start": "frame"},
    )
    assert refused.json() == {
        "ok": False,
        "code": "",
        "step_lines": [],
        "step_index": 0,
        "message": "This node starts from df; remove the start step.",
    }


def _corpus_translations_without_inputs() -> list[tuple[str, list[dict[str, Any]]]]:
    fixtures = Path(__file__).parent / "fixtures" / "polars_steps_corpus" / "translations.json"
    translations = json.loads(fixtures.read_text(encoding="utf-8"))
    cases: list[tuple[str, list[dict[str, Any]]]] = []
    for name, translation in translations.items():
        steps = translation["steps"]
        if not steps or steps[0]["kind"] != "source":
            continue
        rest = steps[1:]
        if any(s["kind"] in ("join", "concat") for s in rest):
            continue
        cases.append((name, rest))
    assert cases
    return cases


@pytest.mark.parametrize(("name", "steps"), _corpus_translations_without_inputs())
def test_corpus_translations_reload_unchanged_on_a_data_input(
    tmp_path: Path, name: str, steps: list[dict[str, Any]]
) -> None:
    quotes, _rates = _frames(tmp_path)
    graph = PipelineGraph(nodes=[_stepped_input(quotes, steps)], edges=[])
    expected = render_polars_steps(steps, [], start="frame").code
    code = graph_to_code(graph, pipeline_name="main")
    _write_sidecars(tmp_path, graph)
    node = next(
        n for n in parse_pipeline_source(code, _base_dir=tmp_path).nodes if n.id == "quotes"
    )
    assert node.data.config["steps"] == steps, name
    assert node.data.config["code"] == expected, name
    assert "_steps_discarded" not in node.data.config, name


def test_data_input_free_code_opening_with_a_string_reloads_in_step_mode(tmp_path: Path) -> None:
    """A rendering that starts with a string statement is not a docstring to the parser."""
    quotes, _rates = _frames(tmp_path)
    steps = [step("c", "free_code", code='"""Keep two rows."""\ndf = df.head(2)')]
    graph = PipelineGraph(nodes=[_stepped_input(quotes, steps)], edges=[])
    code = graph_to_code(graph, pipeline_name="main")
    assert '\n    """Keep two rows."""\n    df = df.head(2)\n' in code
    _write_sidecars(tmp_path, graph)

    node = next(
        n for n in parse_pipeline_source(code, _base_dir=tmp_path).nodes if n.id == "quotes"
    )
    assert node.data.config["steps"] == steps
    assert node.data.config["code"] == '"""Keep two rows."""\ndf = df.head(2)'
    assert "_steps_discarded" not in node.data.config


def test_data_input_generated_module_runs_standalone(tmp_path: Path) -> None:
    """The generated function itself applies the steps, and raises for an incomplete list."""
    quotes, _rates = _frames(tmp_path)
    # The module resolves its project root by walking up to a haute.toml inside a git repository.
    (tmp_path / "haute.toml").write_text("", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    module = _write_and_import(
        PipelineGraph(nodes=[_stepped_input(quotes, [step("l", "limit", n=2)])], edges=[]),
        tmp_path,
    )
    frame = _collect(module.pipeline.run())
    assert frame.height == 2
    assert frame["premium"].to_list() == [50.0, 200.0]

    broken = _write_and_import(
        PipelineGraph(
            nodes=[_stepped_input(quotes, [step("f", "filter", match="all", conditions=[])])],
            edges=[],
        ),
        tmp_path,
    )
    with pytest.raises(NotImplementedError, match=INCOMPLETE_STEPS_MESSAGE.split(".")[0]):
        _collect(broken.pipeline.run())


# ---------------------------------------------------------------------------
# Frame-start surfaces: External File, Rating Step, Model Score, Scenario Expander
# ---------------------------------------------------------------------------


def _external_file(tmp_path: Path, steps: list[dict[str, Any]], **extra: Any) -> GraphNode:
    """An External File over a pickled dict, with post-load steps."""
    obj_path = tmp_path / "factors.pkl"
    with obj_path.open("wb") as handle:
        pickle.dump({"factor": 2.0}, handle)
    config = {"path": str(obj_path), "fileType": "pickle", "steps": steps, **extra}
    return GraphNode(id="ext", data=NodeData(label="ext", nodeType="externalFile", config=config))


def _rating_step(steps: list[dict[str, Any]], **extra: Any) -> GraphNode:
    config = {
        "tables": [
            {
                "factors": ["region"],
                "outputColumn": "rate_factor",
                "defaultValue": "1.0",
                "entries": [{"region": "north", "value": "1.25"}],
            }
        ],
        "combinedOutputs": [],
        "steps": steps,
        **extra,
    }
    return GraphNode(id="rated", data=NodeData(label="rated", nodeType="ratingStep", config=config))


def _scenario_expander(steps: list[dict[str, Any]]) -> GraphNode:
    config = {
        "column_name": "scenario_value",
        "min_value": 0.0,
        "max_value": 1.0,
        "stepCount": 3,
        "step_column": "scenario_index",
        "steps": steps,
    }
    return GraphNode(
        id="grid", data=NodeData(label="grid", nodeType="scenarioExpander", config=config)
    )


def _model_score(steps: list[dict[str, Any]]) -> GraphNode:
    config = {
        "sourceType": "registered",
        "registered_model": "catalog.models.pricing",
        "version": "1",
        "task": "regression",
        "output_column": "prediction",
        "steps": steps,
    }
    return GraphNode(
        id="scored", data=NodeData(label="scored", nodeType="modelScore", config=config)
    )


def _reparsed(tmp_path: Path, graph: PipelineGraph, node_id: str) -> tuple[str, GraphNode]:
    code = graph_to_code(graph, pipeline_name="main")
    _write_sidecars(tmp_path, graph)
    parsed = parse_pipeline_source(code, _base_dir=tmp_path)
    return code, next(n for n in parsed.nodes if n.id == node_id)


def test_external_file_steps_reach_the_other_inputs_and_obj(tmp_path: Path) -> None:
    quotes, rates = _frames(tmp_path)
    join = step(
        "j", "join", input="rates", how="inner", leftOn=["region"], rightOn=["region"], suffix="_r"
    )
    steps = [join, step("l", "limit", n=2)]
    graph = PipelineGraph(
        nodes=[quotes, rates, _external_file(tmp_path, steps)],
        edges=[make_edge("quotes", "ext"), make_edge("rates", "ext")],
    )
    result = execute_graph(graph, target_node_id="ext", execution_context=_capped_context())["ext"]
    assert result.status == "ok", result.error
    assert "rate" in [c.name for c in result.columns]
    assert len(result.preview) == 2

    code, node = _reparsed(tmp_path, graph, "ext")
    assert (
        "def ext(quotes: pl.LazyFrame, rates: pl.LazyFrame, *, obj) -> pl.LazyFrame:\n"
        "    df = quotes\n"
        "    df = df.join(\n"
    ) in code
    assert node.data.config["steps"] == steps
    assert (
        node.data.config["code"]
        == render_polars_steps(steps, ["quotes", "rates"], start="frame").code
    )
    assert "_steps_discarded" not in node.data.config

    # Free code reaches the loaded object as `obj`, as the code box always could.
    reaching = [
        step("c", "free_code", code='df = df.with_columns(pl.lit(obj["factor"]).alias("factor"))')
    ]
    graph = PipelineGraph(
        nodes=[quotes, _external_file(tmp_path, reaching)], edges=[make_edge("quotes", "ext")]
    )
    result = execute_graph(graph, target_node_id="ext", execution_context=_capped_context())["ext"]
    assert result.status == "ok", result.error
    assert result.preview[0]["factor"] == 2.0


def test_external_file_unknown_input_and_input_mapping_fail_loudly(tmp_path: Path) -> None:
    quotes, _rates = _frames(tmp_path)
    join = step(
        "j",
        "join",
        input="policies",
        how="inner",
        leftOn=["region"],
        rightOn=["region"],
        suffix="_r",
    )
    unknown = _external_file(tmp_path, [join])
    # An edges surface renders without names at construction; the reference
    # is checked at build time against the connected edges.
    assert "_steps_error" not in unknown.data.config
    graph = PipelineGraph(nodes=[quotes, unknown], edges=[make_edge("quotes", "ext")])
    result = execute_graph(graph, target_node_id="ext", execution_context=_capped_context())["ext"]
    assert result.status == "error"
    assert INCOMPLETE_STEPS_MESSAGE in str(result.error)
    assert "Step 1: Unknown input 'policies'; connected inputs: quotes." in str(result.error)

    code, node = _reparsed(tmp_path, graph, "ext")
    assert "\n    df = quotes\n    raise NotImplementedError(" in code
    assert INCOMPLETE_STEPS_MESSAGE in code
    assert node.data.config["steps"] == [join]
    assert "_steps_discarded" not in node.data.config

    mapped = _external_file(tmp_path, [step("l", "limit", n=1)], inputMapping={"quotes": "quotes"})
    graph = PipelineGraph(nodes=[quotes, mapped], edges=[make_edge("quotes", "ext")])
    with pytest.raises(ConfigError, match="cannot carry inputMapping"):
        execute_graph(graph, target_node_id="ext", execution_context=_capped_context())


def test_instance_of_stepped_external_file_executes_with_mapping(tmp_path: Path) -> None:
    quotes, rates = _frames(tmp_path)
    alt_q = tmp_path / "alt_quotes.parquet"
    alt_r = tmp_path / "alt_rates.parquet"
    pl.DataFrame({"premium": [100.0], "region": ["north"]}).write_parquet(alt_q)
    pl.DataFrame({"region": ["north"], "rate": [5.0]}).write_parquet(alt_r)
    join = step(
        "j", "join", input="rates", how="inner", leftOn=["region"], rightOn=["region"], suffix="_r"
    )
    original = _external_file(tmp_path, [join, step("l", "limit", n=1)])
    instance = GraphNode(
        id="ext_inst",
        data=NodeData(
            label="ext_inst",
            nodeType="externalFile",
            config={
                "instanceOf": "ext",
                "inputMapping": {"quotes": "alt_quotes", "rates": "alt_rates"},
            },
        ),
    )
    graph = PipelineGraph(
        nodes=[
            quotes,
            rates,
            original,
            _ready_source("alt_quotes", alt_q),
            _ready_source("alt_rates", alt_r),
            instance,
        ],
        edges=[
            make_edge("quotes", "ext"),
            make_edge("rates", "ext"),
            make_edge("alt_quotes", "ext_inst"),
            make_edge("alt_rates", "ext_inst"),
        ],
    )
    results = execute_graph(graph, target_node_id="ext_inst", execution_context=_capped_context())
    assert results["ext_inst"].status == "ok", results["ext_inst"].error
    assert len(results["ext_inst"].preview) == 1
    assert results["ext_inst"].preview[0]["rate"] == 5.0


def test_flatten_rewrites_a_stepped_external_file_input() -> None:
    child = PipelineGraph(
        nodes=[
            GraphNode(
                id="output", data=NodeData(label="Internal Output", nodeType="polars", config={})
            )
        ],
        edges=[],
    )
    definition = SubmodelDefinition(
        definition_id="definition_scoring",
        file="modules/scoring.py",
        graph=child,
        input_ports=[],
        output_ports=[
            SubmodelOutputPort(name="results", source=SubmodelEndpoint(node_id="output"))
        ],
    )
    instance = GraphNode(
        id="instance_a",
        data=NodeData(
            label="score",
            nodeType="submodel",
            config={"definitionId": "definition_scoring", "alias": "score"},
        ),
    )
    join = step("j", "join", input="results", how="inner", leftOn=["k"], rightOn=["k"], suffix="_r")
    consumer = GraphNode(
        id="consumer",
        data=NodeData(
            label="Consumer",
            nodeType="externalFile",
            config={"path": "models/obj.pkl", "fileType": "pickle", "steps": [join]},
        ),
    )
    graph = PipelineGraph(
        nodes=[instance, consumer],
        edges=[
            GraphEdge(
                id="output", source="instance_a", target="consumer", sourceHandle="out__results"
            )
        ],
        submodels={"definition_scoring": definition},
    )
    flat = flatten_graph(graph).node_map["consumer"].data.config
    assert flat["steps"] == [{**join, "input": "Internal_Output"}]
    assert "inputMapping" not in flat


def test_rating_step_steps_execute_and_round_trip(tmp_path: Path) -> None:
    quotes, _rates = _frames(tmp_path)
    steps = [step("l", "limit", n=2)]
    graph = PipelineGraph(nodes=[quotes, _rating_step(steps)], edges=[make_edge("quotes", "rated")])
    result = execute_graph(graph, target_node_id="rated", execution_context=_capped_context())[
        "rated"
    ]
    assert result.status == "ok", result.error
    assert "rate_factor" in [c.name for c in result.columns]
    assert len(result.preview) == 2

    code, node = _reparsed(tmp_path, graph, "rated")
    assert "def rated(df: pl.LazyFrame) -> pl.LazyFrame:" in code
    assert "\n    df = df.head(2)\n    return df\n" in code
    assert node.data.config["steps"] == steps
    assert node.data.config["code"] == "df = df.head(2)"
    assert "_steps_discarded" not in node.data.config

    broken = _rating_step([step("f", "filter", match="all", conditions=[])])
    assert broken.data.config["_steps_error"] == "Step 1: Add at least one condition."
    graph = PipelineGraph(nodes=[quotes, broken], edges=[make_edge("quotes", "rated")])
    result = execute_graph(graph, target_node_id="rated", execution_context=_capped_context())[
        "rated"
    ]
    assert result.status == "error"
    assert INCOMPLETE_STEPS_MESSAGE in str(result.error)
    code, node = _reparsed(tmp_path, graph, "rated")
    assert INCOMPLETE_STEPS_MESSAGE in code
    assert node.data.config["steps"] == broken.data.config["steps"]
    assert node.data.config["_steps_error"] == "Step 1: Add at least one condition."


def test_scenario_expander_steps_execute_and_round_trip(tmp_path: Path) -> None:
    quotes, _rates = _frames(tmp_path)
    steps = [step("l", "limit", n=4)]
    graph = PipelineGraph(
        nodes=[quotes, _scenario_expander(steps)], edges=[make_edge("quotes", "grid")]
    )
    result = execute_graph(graph, target_node_id="grid", execution_context=_capped_context())[
        "grid"
    ]
    assert result.status == "ok", result.error
    assert "scenario_value" in [c.name for c in result.columns]
    assert len(result.preview) == 4  # 4 rows x 3 grid values, then the Limit

    code, node = _reparsed(tmp_path, graph, "grid")
    assert "def grid(df: pl.LazyFrame) -> pl.LazyFrame:" in code
    assert "\n    df = df.head(4)\n    return df\n" in code
    assert node.data.config["steps"] == steps
    assert node.data.config["stepCount"] == 3
    assert "_steps_discarded" not in node.data.config

    broken = _scenario_expander([source()])
    assert (
        broken.data.config["_steps_error"]
        == "Step 1: This node starts from df; remove the start step."
    )
    graph = PipelineGraph(nodes=[quotes, broken], edges=[make_edge("quotes", "grid")])
    assert (
        execute_graph(graph, target_node_id="grid", execution_context=_capped_context())[
            "grid"
        ].status
        == "error"
    )
    code, node = _reparsed(tmp_path, graph, "grid")
    assert INCOMPLETE_STEPS_MESSAGE in code
    assert node.data.config["steps"] == [source()]


def test_model_score_steps_round_trip_and_fail_before_any_model_loads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute.deploy import _scorer
    from haute.deploy._scorer import score_graph

    quotes, _rates = _frames(tmp_path)
    steps = [step("l", "limit", n=2)]
    graph = PipelineGraph(
        nodes=[quotes, _model_score(steps)], edges=[make_edge("quotes", "scored")]
    )
    code, node = _reparsed(tmp_path, graph, "scored")
    assert "def scored(df: pl.LazyFrame) -> pl.LazyFrame:" in code
    assert "\n    df = df.head(2)\n    return df\n" in code
    assert node.data.config["steps"] == steps
    assert node.data.config["code"] == "df = df.head(2)"
    assert "_steps_discarded" not in node.data.config

    broken = _model_score([step("f", "filter", match="all", conditions=[])])
    assert broken.data.config["_steps_error"] == "Step 1: Add at least one condition."
    graph = PipelineGraph(nodes=[quotes, broken], edges=[make_edge("quotes", "scored")])
    result = execute_graph(graph, target_node_id="scored", execution_context=_capped_context())[
        "scored"
    ]
    assert result.status == "error"
    assert INCOMPLETE_STEPS_MESSAGE in str(result.error)
    code, node = _reparsed(tmp_path, graph, "scored")
    assert INCOMPLETE_STEPS_MESSAGE in code
    assert node.data.config["steps"] == broken.data.config["steps"]

    # Deploy: the plan is rejected before any model loading or scoring is attempted.
    deploy_graph = make_graph(
        {
            "nodes": [
                {
                    "id": "src",
                    "data": {"label": "src", "nodeType": "apiInput", "config": {"path": ""}},
                },
                {
                    "id": "scored",
                    "data": {
                        "label": "scored",
                        "nodeType": "modelScore",
                        "config": broken.data.config,
                    },
                },
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": {
                            "outputMapping": [
                                {
                                    "source_port": "scored",
                                    "source_column": "prediction",
                                    "output_path": "$[:].prediction",
                                    "enabled": True,
                                }
                            ],
                            "outputFormat": "json",
                        },
                    },
                },
            ],
            "edges": [
                {"id": "e1", "source": "src", "target": "scored", "sourceHandle": "src"},
                {"id": "e2", "source": "scored", "target": "out"},
            ],
        }
    )
    # A remapped model WITHOUT a bundled feature contract is exactly the case
    # whose preparation pass (`_attach_bundled_model_contract_inputs`) loads the
    # local model, so a guard placed after it would load before rejecting.
    model_path = tmp_path / "score.cbm"
    model_path.write_bytes(b"not a real model")
    loaded: list[str] = []
    monkeypatch.setattr(_scorer, "_load_local_model_cached", lambda path, task: loaded.append(path))
    with pytest.raises(ConfigError, match=r"Step 1: Add at least one condition") as failed:
        score_graph(
            graph=deploy_graph,
            input_df=pl.DataFrame({"x": [1.0]}),
            input_node_ids=["src"],
            output_node_id="out",
            artifact_paths={"scored__score.cbm": str(model_path)},
        )
    assert INCOMPLETE_STEPS_MESSAGE in str(failed.value)
    assert loaded == [], "the deploy plan loaded a model before rejecting the incomplete steps"


def test_recovery_and_save_report_incomplete_steps_on_every_surface(project_root: Path) -> None:
    from haute._node_config_recovery import validate_recovery_config

    pl.DataFrame({"premium": [1.0]}).write_parquet(project_root / "quotes.parquet")
    broken_steps = [step("f", "filter", match="all", conditions=[])]
    # Raw sidecar-shaped candidates: no derived `_steps_error`, so recovery
    # must render the steps itself to report the incomplete one.
    for node in (
        _rating_step(broken_steps),
        _scenario_expander(broken_steps),
        _model_score(broken_steps),
        _stepped_input(_ready_source("quotes", project_root / "quotes.parquet"), broken_steps),
    ):
        raw = {k: v for k, v in node.data.config.items() if not k.startswith("_") and k != "code"}
        assert "_steps_error" not in raw
        issues = validate_recovery_config(node.data.nodeType, raw, input_names=None)
        assert any(issue.path == "steps" and issue.code == "incomplete" for issue in issues), (
            node.data.nodeType
        )
        # A renderable list on the same raw shape reports nothing.
        ok_raw = {**raw, "steps": [step("l", "limit", n=2)]}
        assert not [
            issue
            for issue in validate_recovery_config(node.data.nodeType, ok_raw, input_names=None)
            if issue.path == "steps"
        ], node.data.nodeType

    quotes, rates = _frames(project_root)
    warnings = _save(
        project_root,
        PipelineGraph(
            nodes=[quotes, rates, _rating_step(broken_steps)], edges=[make_edge("quotes", "rated")]
        ),
    )
    assert any(
        "Rating Step node 'rated' has an incomplete step list (Step 1: Add at least one condition.)"
        in w
        for w in warnings
    ), warnings


def _surface_graph(
    tmp_path: Path, surface: str, steps: list[dict[str, Any]]
) -> tuple[PipelineGraph, str]:
    """A source plus one stepped frame surface, ready to generate and run."""
    quotes, _rates = _frames(tmp_path)
    if surface == "dataInput":
        return PipelineGraph(nodes=[_stepped_input(quotes, steps)], edges=[]), "quotes"
    node = {
        "externalFile": lambda: _external_file(tmp_path, steps),
        "ratingStep": lambda: _rating_step(steps),
        "scenarioExpander": lambda: _scenario_expander(steps),
    }[surface]()
    return (
        PipelineGraph(nodes=[quotes, node], edges=[make_edge("quotes", node.id)]),
        node.id,
    )


#: Every frame surface whose generated module runs without an external model.
_RUNNABLE_FRAME_SURFACES = ("dataInput", "externalFile", "ratingStep", "scenarioExpander")


@pytest.mark.parametrize("surface", _RUNNABLE_FRAME_SURFACES)
def test_frame_surface_generated_module_runs_standalone(tmp_path: Path, surface: str) -> None:
    """The generated function itself applies the steps, and raises for an incomplete list."""
    (tmp_path / "haute.toml").write_text("", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)

    graph, _node_id = _surface_graph(tmp_path, surface, [step("l", "limit", n=2)])
    module = _write_and_import(graph, tmp_path)
    frame = _collect(module.pipeline.run())
    assert frame.height == 2, surface

    broken, _ = _surface_graph(tmp_path, surface, [step("f", "filter", match="all", conditions=[])])
    broken_module = _write_and_import(broken, tmp_path)
    with pytest.raises(NotImplementedError, match=INCOMPLETE_STEPS_MESSAGE.split(".")[0]):
        _collect(broken_module.pipeline.run())


@pytest.mark.parametrize("surface", (*_RUNNABLE_FRAME_SURFACES, "modelScore"))
def test_frame_surface_free_code_with_redundant_parentheses_reloads_in_step_mode(
    tmp_path: Path, surface: str
) -> None:
    """Each surface's extractor normalises its own rendering, so reconcile must not discard it."""
    steps = [step("c", "free_code", code="df = (df.head(2))")]
    if surface == "modelScore":
        quotes, _rates = _frames(tmp_path)
        graph = PipelineGraph(
            nodes=[quotes, _model_score(steps)], edges=[make_edge("quotes", "scored")]
        )
        node_id = "scored"
    else:
        graph, node_id = _surface_graph(tmp_path, surface, steps)

    code = graph_to_code(graph, pipeline_name="main")
    assert "df = (df.head(2))" in code
    _write_sidecars(tmp_path, graph)
    node = next(n for n in parse_pipeline_source(code, _base_dir=tmp_path).nodes if n.id == node_id)
    assert node.data.config["steps"] == steps, surface
    assert node.data.config["code"] == "df = (df.head(2))", surface
    assert "_steps_discarded" not in node.data.config, surface


def test_model_score_steps_run_after_scoring(tmp_path: Path) -> None:
    """A Model Score's steps apply to the scored frame, as its post-processing code does."""
    from unittest.mock import MagicMock, patch

    import numpy as np

    from haute._mlflow_io import ScoringModel

    raw = MagicMock()
    raw.feature_names_ = ["premium"]
    raw.predict.return_value = np.array([1.0, 2.0, 3.0, 4.0])
    raw.get_cat_feature_indices.return_value = []
    del raw.predict_proba
    model = ScoringModel(
        model=raw, feature_names=["premium"], cat_feature_names=frozenset(), flavor="catboost"
    )

    quotes, _rates = _frames(tmp_path)
    scored = _model_score([step("l", "limit", n=2)])
    graph = PipelineGraph(nodes=[quotes, scored], edges=[make_edge("quotes", "scored")])
    with patch("haute._mlflow_io.load_mlflow_model", return_value=model):
        result = execute_graph(graph, target_node_id="scored", execution_context=_capped_context())[
            "scored"
        ]
    assert result.status == "ok", result.error
    assert "prediction" in [c.name for c in result.columns]
    assert len(result.preview) == 2


def _explore(steps: list[dict[str, Any]], **extra: Any) -> GraphNode:
    """An Explore node whose analysis frame is authored as steps."""
    return GraphNode(
        id="report",
        data=NodeData(label="report", nodeType="explore", config={"steps": steps, **extra}),
    )


def test_explore_steps_execute_and_round_trip_through_the_decorator(tmp_path: Path) -> None:
    """Explore has no sidecar: its steps travel in the decorator beside its cards."""
    quotes, _rates = _frames(tmp_path)
    steps = [step("l", "limit", n=2)]
    graph = PipelineGraph(nodes=[quotes, _explore(steps)], edges=[make_edge("quotes", "report")])
    result = execute_graph(graph, target_node_id="report", execution_context=_capped_context())[
        "report"
    ]
    assert result.status == "ok", result.error
    assert len(result.preview) == 2

    code = graph_to_code(graph, pipeline_name="main")
    assert "@pipeline.explore(steps=[" in code
    assert "(df: pl.LazyFrame) -> pl.LazyFrame:\n    df = df.head(2)\n    return df\n" in code
    # No sidecar is written for an Explore node.
    assert "config/explore" not in code
    assert not [rel for rel in collect_node_configs(graph) if "report" in rel]

    _write_sidecars(tmp_path, graph)
    node = next(
        n for n in parse_pipeline_source(code, _base_dir=tmp_path).nodes if n.id == "report"
    )
    assert node.data.config["steps"] == steps
    assert node.data.config["code"] == "df = df.head(2)"
    assert "_steps_discarded" not in node.data.config


def test_explore_steps_keep_their_cards_and_fail_loudly_when_incomplete(tmp_path: Path) -> None:
    quotes, _rates = _frames(tmp_path)
    from tests.test_explore_pivots import _pivot

    pivots = [_pivot()]
    steps = [step("l", "limit", n=2)]
    graph = PipelineGraph(
        nodes=[quotes, _explore(steps, pivots=pivots)], edges=[make_edge("quotes", "report")]
    )
    code = graph_to_code(graph, pipeline_name="main")
    _write_sidecars(tmp_path, graph)
    node = next(
        n for n in parse_pipeline_source(code, _base_dir=tmp_path).nodes if n.id == "report"
    )
    assert node.data.config["steps"] == steps
    assert [pivot["id"] for pivot in node.data.config["pivots"]] == [pivots[0]["id"]]

    broken = _explore([step("f", "filter", match="all", conditions=[])])
    assert broken.data.config["_steps_error"] == "Step 1: Add at least one condition."
    broken_graph = PipelineGraph(nodes=[quotes, broken], edges=[make_edge("quotes", "report")])
    result = execute_graph(
        broken_graph, target_node_id="report", execution_context=_capped_context()
    )["report"]
    assert result.status == "error"
    assert INCOMPLETE_STEPS_MESSAGE in str(result.error)

    broken_code = graph_to_code(broken_graph, pipeline_name="main")
    assert INCOMPLETE_STEPS_MESSAGE in broken_code
    _write_sidecars(tmp_path, broken_graph)
    reloaded = next(
        n for n in parse_pipeline_source(broken_code, _base_dir=tmp_path).nodes if n.id == "report"
    )
    assert reloaded.data.config["steps"] == broken.data.config["steps"]
    assert reloaded.data.config["code"] == ""
    assert reloaded.data.config["_steps_error"] == "Step 1: Add at least one condition."


def test_explore_hand_edited_body_discards_its_steps(tmp_path: Path) -> None:
    quotes, _rates = _frames(tmp_path)
    graph = PipelineGraph(
        nodes=[quotes, _explore([step("l", "limit", n=2)])], edges=[make_edge("quotes", "report")]
    )
    _write_sidecars(tmp_path, graph)
    edited = graph_to_code(graph, pipeline_name="main").replace("df.head(2)", "df.head(3)")
    node = next(
        n for n in parse_pipeline_source(edited, _base_dir=tmp_path).nodes if n.id == "report"
    )
    assert "steps" not in node.data.config
    assert node.data.config["code"] == "df = df.head(3)"
    assert node.data.config["_steps_discarded"].startswith("Steps were discarded because")
    # Explore has no sidecar to retire.
    assert "_discarded_sidecar" not in node.data.config


# ---------------------------------------------------------------------------
# Recovery preserves a hand-edited body over stale steps
# ---------------------------------------------------------------------------


def _recovered_settings(root: Path, authored_id: str) -> tuple[dict[str, Any], list[Any]]:
    """The raw settings recovery would offer for one node, plus its change notes."""
    from haute._pipeline_recovery import load_pipeline_editor_document
    from haute._recovery_sources import read_raw_node_settings

    document = load_pipeline_editor_document(root / "main.py", project_root=root)
    target = next(node for node in document.nodes if node.authored_id == authored_id)
    _node_type, raw, changes = read_raw_node_settings(root, document, target)
    return raw, changes


@pytest.mark.parametrize("surface", ("explore", "dataInput"))
def test_recovery_keeps_a_hand_edited_body_over_stale_steps(
    project_root: Path, surface: str
) -> None:
    """Recovery is the ``.py``-is-truth path too: an edited body is never regenerated."""
    quotes, _rates = _frames(project_root)
    if surface == "explore":
        node, node_id = _explore([step("l", "limit", n=2)]), "report"
    else:
        node, node_id = _stepped_input(quotes, [step("l", "limit", n=2)]), "quotes"
    nodes = [node] if surface == "dataInput" else [quotes, node]
    edges = [] if surface == "dataInput" else [make_edge("quotes", "report")]
    graph = PipelineGraph(nodes=nodes, edges=edges)

    code = graph_to_code(graph, pipeline_name="main")
    _write_sidecars(project_root, graph)
    # The author edited the generated body by hand; the steps are now stale.
    (project_root / "main.py").write_text(
        code.replace("df.head(2)", "df.head(3)"), encoding="utf-8"
    )

    raw, changes = _recovered_settings(project_root, node_id)
    assert raw["code"] == "df = df.head(3)", surface
    assert "steps" not in raw, surface
    assert raw["_steps_discarded"].startswith("Steps were discarded because"), surface
    assert any(change.path == "/steps" and change.outcome == "removed" for change in changes), (
        surface
    )

    # The recovered candidate materialises to the edited body, not the steps.
    recovered = GraphNode(
        id=node_id,
        data=NodeData(label=node_id, nodeType=node.data.nodeType, config=raw),
    )
    assert recovered.data.config["code"] == "df = df.head(3)", surface


@pytest.mark.parametrize("surface", ("explore", "dataInput"))
def test_recovery_keeps_steps_when_the_body_still_matches(project_root: Path, surface: str) -> None:
    quotes, _rates = _frames(project_root)
    steps = [step("l", "limit", n=2)]
    if surface == "explore":
        node, node_id = _explore(steps), "report"
        graph = PipelineGraph(nodes=[quotes, node], edges=[make_edge("quotes", "report")])
    else:
        node, node_id = _stepped_input(quotes, steps), "quotes"
        graph = PipelineGraph(nodes=[node], edges=[])

    code = graph_to_code(graph, pipeline_name="main")
    _write_sidecars(project_root, graph)
    (project_root / "main.py").write_text(code, encoding="utf-8")

    raw, _changes = _recovered_settings(project_root, node_id)
    assert raw["steps"] == steps, surface
    assert "_steps_discarded" not in raw, surface


@pytest.mark.parametrize("surface", ("explore", "dataInput"))
def test_recovery_keeps_steps_when_it_drops_a_body_the_decorator_never_calls(
    project_root: Path, surface: str
) -> None:
    """A function that is not a hook loses its body, never its steps.

    Every generated body before node declarations took this form. The steps
    are the node's settings, so the regenerated hook still performs them.
    """
    from haute._pipeline_recovery import load_pipeline_editor_document
    from haute._pipeline_repair import build_recover_unavailable_node_plan
    from haute.schemas import PipelineRepairRecoverRequest

    quotes, _rates = _frames(project_root)
    steps = [step("l", "limit", n=2)]
    if surface == "explore":
        node, node_id, old_signature = _explore(steps), "report", "def report(quotes)"
        graph = PipelineGraph(nodes=[quotes, node], edges=[make_edge("quotes", "report")])
    else:
        node, node_id, old_signature = _stepped_input(quotes, steps), "quotes", "def quotes()"
        graph = PipelineGraph(nodes=[node], edges=[])
    code = graph_to_code(graph, pipeline_name="main")
    old_form, replaced = re.subn(rf"def {node_id}\(df[^)]*\)", old_signature, code)
    assert replaced == 1, code
    _write_sidecars(project_root, graph)
    (project_root / "main.py").write_text(old_form, encoding="utf-8", newline="\n")

    raw, changes = _recovered_settings(project_root, node_id)
    assert raw["steps"] == steps, surface
    assert "_steps_discarded" not in raw, surface
    reported = [(c.path, c.outcome) for c in changes if c.path in {"/code", "/steps"}]
    assert reported == [("/code", "removed")], surface
    recovered = GraphNode(
        id=node_id,
        data=NodeData(label=node_id, nodeType=node.data.nodeType, config=raw),
    )
    assert recovered.data.config["code"] == "df = df.head(2)", surface

    document = load_pipeline_editor_document(project_root / "main.py", project_root=project_root)
    target = next(item for item in document.nodes if item.authored_id == node_id)
    plan = build_recover_unavailable_node_plan(
        project_root=project_root,
        request=PipelineRepairRecoverRequest(
            source_file=document.source_file,
            source_revision=document.source_revision,
            target_source_file=target.source_file,
            target_recovery_id=target.recovery_id,
            action="recover",
        ),
    )
    source = next(edit for edit in plan.edits if edit.path.name == "main.py")
    assert source.after is not None
    regenerated = source.after.decode("utf-8")
    assert f"def {node_id}(df" in regenerated, surface
    assert "    df = df.head(2)\n    return df\n" in regenerated, surface


def test_recovery_plans_a_malformed_step_container_without_raising(project_root: Path) -> None:
    """Recovery never raises on a bad field: the unusable list goes, the body stays.

    A `ConfigError` here would escape the recovery route, which handles only
    `PipelineRepairError` and `OSError`, and the user would get an HTTP 500
    instead of a plan.
    """
    from haute._pipeline_recovery import load_pipeline_editor_document
    from haute._pipeline_repair import build_recover_unavailable_node_plan
    from haute.schemas import PipelineRepairRecoverRequest

    quotes, _rates = _frames(project_root)
    graph = PipelineGraph(nodes=[_stepped_input(quotes, [step("l", "limit", n=2)])], edges=[])
    (project_root / "main.py").write_text(
        graph_to_code(graph, pipeline_name="main"), encoding="utf-8"
    )
    _write_sidecars(project_root, graph)
    sidecar = project_root / "config" / "data_input" / "quotes.json"
    persisted = json.loads(sidecar.read_text(encoding="utf-8"))
    # A step list that is not a list at all, beside a readable rest of config.
    sidecar.write_text(json.dumps({**persisted, "steps": {"kind": "limit"}}), encoding="utf-8")

    document = load_pipeline_editor_document(project_root / "main.py", project_root=project_root)
    target = next(node for node in document.nodes if node.authored_id == "quotes")

    # The raw read degrades instead of raising.
    raw, changes = _recovered_settings(project_root, "quotes")
    assert "steps" not in raw
    assert raw["code"] == "df = df.head(2)"
    assert any(change.path == "/steps" and change.outcome == "removed" for change in changes)

    # And the route's own plan builder returns a plan rather than an exception.
    plan = build_recover_unavailable_node_plan(
        project_root=project_root,
        request=PipelineRepairRecoverRequest(
            source_file=document.source_file,
            source_revision=document.source_revision,
            target_source_file=target.source_file,
            target_recovery_id=target.recovery_id,
            action="recover",
        ),
    )
    assert plan.response.changes
    # The authored body must survive. The node regenerated from the recovered
    # config (code only) is exactly the hook already on disk, so the plan leaves
    # the source alone rather than rewriting it.
    assert not any(edit.path.name == "main.py" for edit in plan.edits)
    assert "    df = df.head(2)\n    return df\n" in (project_root / "main.py").read_text(
        encoding="utf-8"
    )
    sidecar_edit = next((edit for edit in plan.edits if edit.path.name == "quotes.json"), None)
    if sidecar_edit is not None and sidecar_edit.after is not None:
        assert "steps" not in json.loads(sidecar_edit.after.decode("utf-8"))


@pytest.mark.parametrize("authoring", ["code", "steps"])
@pytest.mark.parametrize("row_count", [1, 2])
def test_deploy_injected_data_input_applies_post_load_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authoring: str,
    row_count: int,
) -> None:
    from haute.deploy._scorer import score_graph

    parquet_file = tmp_path / "source.parquet"
    raw_frame = pl.DataFrame(
        {
            "x": [10, 20][:row_count],
            "unused": [100, 200][:row_count],
        }
    )
    raw_frame.write_parquet(parquet_file)
    authored_code = (
        'df = df.with_columns((pl.col("x") * scale).alias("x"))\n'
        'df = df.with_columns(pl.col("x").alias("doubled"))'
    )
    src_config: dict[str, Any] = {
        "inputType": "file",
        "format": "parquet",
        "mode": "scan",
        "path": str(parquet_file),
    }
    if authoring == "code":
        src_config["code"] = authored_code
    else:
        src_config["steps"] = [step("fc", "free_code", code=authored_code)]

    out_config: dict[str, Any] = {
        "outputMapping": [
            {
                "source_port": "src",
                "source_column": "doubled",
                "output_path": "$[:].doubled",
                "enabled": True,
            }
        ],
        "outputFormat": "json",
    }
    graph = PipelineGraph(
        nodes=[
            GraphNode(
                id="src",
                data=NodeData(label="src", nodeType=NodeType.DATA_INPUT, config=src_config),
            ),
            GraphNode(
                id="out",
                data=NodeData(label="out", nodeType=NodeType.OUTPUT, config=out_config),
            ),
        ],
        edges=[
            GraphEdge(id="e1", source="src", target="out"),
        ],
        preamble="scale = 2",
    )

    canvas_result = execute_graph(graph)["src"]
    assert canvas_result.status == "ok", canvas_result.error
    assert [row["doubled"] for row in canvas_result.preview] == [20, 40][:row_count]

    def _fail_resolve(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(
            "resolve_data_input must not be called during live-injected deployment"
        )

    monkeypatch.setattr("haute._input_providers.resolve_data_input", _fail_resolve)

    scored = score_graph(
        graph=graph,
        input_df=raw_frame,
        input_node_ids=["src"],
        output_node_id="out",
        output_fields=["doubled"],
    )
    assert isinstance(scored, pl.DataFrame)
    assert scored["doubled"].to_list() == [20, 40][:row_count]
    assert scored.columns == ["doubled"]


@pytest.mark.parametrize(
    "authoring_config",
    [{"code": ""}, {"steps": []}],
    ids=["code", "steps"],
)
def test_empty_model_score_steps_preserve_projection(
    authoring_config: dict[str, Any],
) -> None:
    import haute.projection as projection
    from haute._contracts import _DEPLOY_MODEL_INPUT_COLUMNS_CONFIG_KEY

    src_node = GraphNode(
        id="src",
        data=NodeData(
            label="src",
            nodeType=NodeType.DATA_INPUT,
            config={
                "inputType": "file",
                "format": "parquet",
                "mode": "scan",
                "path": "src.parquet",
            },
        ),
    )
    edge = GraphEdge(id="e1", source="src", target="model")

    model_data = NodeData(
        label="model",
        nodeType=NodeType.MODEL_SCORE,
        config={
            "sourceType": "registered",
            "registered_model": "test_model",
            "output_column": "prediction",
            _DEPLOY_MODEL_INPUT_COLUMNS_CONFIG_KEY: ["x"],
            **authoring_config,
        },
    )
    plan = projection.compute_prepared_plan(
        ["src", "model"],
        {"src": ["model"], "model": []},
        {"src": src_node, "model": GraphNode(id="model", data=model_data)},
        {"model": ["prediction"]},
        relevant_edges=[edge],
    )

    assert plan.needed_by_node["src"] == frozenset({"x"})
    assert not plan.opaque_boundaries


@pytest.mark.parametrize("preceding_limit", [False, True])
def test_frame_free_code_rejects_global_df_with_authored_line(preceding_limit: bool) -> None:
    snippet = "# a comment\nglobal df\ndf = df.head(2)"
    fc_step = step("fc", "free_code", code=snippet)
    if preceding_limit:
        steps = [step("l", "limit", n=2), fc_step]
        expected_index = 1
    else:
        steps = [fc_step]
        expected_index = 0
    with pytest.raises(PolarsStepError) as exc_info:
        render_polars_steps(steps, None, start="frame")
    assert exc_info.value.step_index == expected_index
    msg = str(exc_info.value)
    assert "Invalid Python on line 2" in msg
    assert (
        "name 'df' is assigned to before global declaration" in msg
        or "name 'df' is used prior to global declaration" in msg
    )


def test_frame_scope_validation_preserves_rendered_line_ranges() -> None:
    code = 'a = 1\ndf = df.with_columns(pl.lit(a).alias("a"))'
    steps = [step("fc", "free_code", code=code), step("l", "limit", n=2)]
    rendered = render_polars_steps(steps, None, start="frame")
    assert rendered.code == f"{code}\ndf = df.head(2)"
    assert rendered.step_lines == ((1, 2), (3, 3))


def test_frame_global_df_saves_as_incomplete_placeholder(tmp_path: Path) -> None:
    offending_steps = [step("fc", "free_code", code="# a comment\nglobal df\ndf = df.head(2)")]
    graph, node_id = _surface_graph(tmp_path, "dataInput", offending_steps)
    validated_node = next(n for n in graph.nodes if n.id == node_id)
    assert "_steps_error" in validated_node.data.config
    generated = graph_to_code(graph, pipeline_name="main")
    assert INCOMPLETE_STEPS_MESSAGE in generated
    compile(generated, "<generated>", "exec")
    _write_sidecars(tmp_path, graph)
    parsed = parse_pipeline_source(generated, _base_dir=tmp_path)
    parsed_node = next(n for n in parsed.nodes if n.id == node_id)
    assert parsed_node.data.config["steps"] == offending_steps
