"""Low-code Polars steps: schema, rendering, materialisation, persistence and execution."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from fastapi.testclient import TestClient

from haute._builders import _build_node_fn
from haute._code_extraction import INCOMPLETE_TRANSFORM_MESSAGE, _extract_user_code
from haute._config_io import collect_node_configs, node_emits_sidecar
from haute._execution_admission import create_admitted_execution_context
from haute._execution_context import ExecutionContext, ExecutionProfile
from haute._flatten import flatten_graph
from haute._graph_utils import _sanitize_func_name as _sanitize_label
from haute._polars_steps import (
    PolarsStepError,
    rename_step_inputs,
    render_polars_steps,
    validate_polars_steps,
)
from haute._types import (
    GraphEdge,
    GraphNode,
    NodeData,
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
    make_source_node,
)

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
        "df = df.filter((pl.col('premium') > 100) & (pl.col('region').is_in(['north', 'south'])))",
        "df = df.with_columns((pl.col('premium') * ipt_rate).alias('gross'))",
        "df = df.with_columns((pl.when((pl.col('gross') >= 500)).then(pl.lit('high'))"
        ".otherwise(pl.lit('low'))).alias('band'))",
        "df = df.join(rates, left_on=['region'], right_on=['region'], how='left', suffix='_rate')",
        "df = df.group_by(['band'], maintain_order=True).agg([pl.col('gross').sum()"
        ".alias('gross_total'), pl.len().alias('rows')])",
    ]
)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_golden_payload_renders_exactly() -> None:
    rendered = render_polars_steps(GOLDEN_STEPS, ["quotes", "rates"])
    assert rendered.code == GOLDEN_CODE
    assert rendered.step_lines == tuple((i + 1, i + 1) for i in range(len(GOLDEN_STEPS)))


def test_rendered_code_is_a_fixpoint_of_user_code_extraction() -> None:
    code = render_polars_steps(GOLDEN_STEPS, ["quotes", "rates"]).code
    body = "\n".join(f"    {line}" for line in code.splitlines()) + "\n    return df"
    extracted = _extract_user_code(
        '    """doc"""\n    df: pl.LazyFrame\n' + body, ["quotes", "rates"]
    )
    assert extracted == code


@pytest.mark.parametrize(
    ("kind_step", "expected"),
    [
        (step("x", "select", columns=["a", "b"]), "df = df.select(['a', 'b'])"),
        (step("x", "drop", columns=["a"]), "df = df.drop(['a'])"),
        (step("x", "rename", renames=[{"from": "a", "to": "b"}]), "df = df.rename({'a': 'b'})"),
        (
            step(
                "x",
                "cast",
                casts=[{"column": "a", "dtype": "Int64"}, {"column": "b", "dtype": "String"}],
            ),
            "df = df.with_columns(pl.col('a').cast(pl.Int64), pl.col('b').cast(pl.String))",
        ),
        (
            step("x", "sort", keys=[{"column": "a", "descending": True}], nullsLast=True),
            "df = df.sort(['a'], descending=[True], nulls_last=True)",
        ),
        (
            step("x", "unique", columns=[], keep="any"),
            "df = df.unique(subset=None, keep='any', maintain_order=True)",
        ),
        (
            step("x", "unique", columns=["a"], keep="last"),
            "df = df.unique(subset=['a'], keep='last', maintain_order=True)",
        ),
        (
            step("x", "join", input="rates", how="cross", leftOn=[], rightOn=[], suffix="_r"),
            "df = df.join(rates, how='cross', suffix='_r')",
        ),
        (
            step("x", "concat", inputs=["rates"], how="diagonal"),
            "df = pl.concat([df, rates], how='diagonal')",
        ),
        (
            step("x", "fill_null", columns=["a"], fill={"kind": "value", "value": num(0)}),
            "df = df.with_columns(pl.col(['a']).fill_null(0))",
        ),
        (
            step("x", "fill_null", columns=[], fill={"kind": "strategy", "strategy": "forward"}),
            "df = df.with_columns(pl.all().fill_null(strategy='forward'))",
        ),
        (step("x", "limit", n=5), "df = df.head(5)"),
        (
            step(
                "x",
                "filter",
                match="any",
                conditions=[cond("a", "is_null"), cond("b", "not_in", values=[num(1), num(2)])],
            ),
            "df = df.filter((pl.col('a').is_null()) | (~pl.col('b').is_in([1, 2])))",
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
            "df = df.filter((pl.col('a').str.contains('x', literal=True)) & "
            "(pl.col('a').str.starts_with(pl.col('b'))))",
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
            "df = df.filter((pl.col('d') > pl.lit('2026-01-31').str.to_date()))",
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
            "df = df.filter((pl.col('d').is_in(pl.Series(['2026-01-01', '2026-02-01'])"
            ".str.to_date())))",
        ),
        (
            step("x", "with_column", name="n", expr={"type": "operand", "operand": num(2)}),
            "df = df.with_columns((pl.lit(2)).alias('n'))",
        ),
        (
            step(
                "x",
                "with_column",
                name="n",
                expr={"type": "binary", "left": num(2), "op": "*", "right": col("a")},
            ),
            "df = df.with_columns((pl.lit(2) * pl.col('a')).alias('n'))",
        ),
        (
            step(
                "x",
                "with_column",
                name="n",
                expr={"type": "function", "fn": "round", "operand": col("a"), "args": [num(2)]},
            ),
            "df = df.with_columns((pl.col('a').round(2)).alias('n'))",
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
            "df = df.with_columns((pl.col('a').cast(pl.Float64)).alias('n'))",
        ),
        (
            step(
                "x",
                "with_column",
                name="n",
                expr={"type": "function", "fn": "upper", "operand": text("ab"), "args": []},
            ),
            "df = df.with_columns((pl.lit('ab').str.to_uppercase()).alias('n'))",
        ),
        (
            step(
                "x",
                "with_column",
                name="n",
                expr={"type": "window", "agg": "sum", "column": "a", "over": ["k"]},
            ),
            "df = df.with_columns((pl.col('a').sum().over(['k'])).alias('n'))",
        ),
    ],
)
def test_each_step_kind_renders_its_statement(kind_step: dict[str, Any], expected: str) -> None:
    rendered = render_polars_steps([source(), kind_step], ["quotes", "rates"])
    assert rendered.code.splitlines()[1] == expected


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
    lines = render_polars_steps(steps, ["quotes"]).code.splitlines()
    assert lines[2] == "df = df.with_columns((pl.lit(rate)).alias('x'))"
    assert lines[3] == (
        "df = df.with_columns((pl.when((pl.col('premium') > rate)).then(pl.lit(rate))"
        ".otherwise(pl.col('premium'))).alias('y'))"
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
        render_polars_steps(steps, ["quotes", "rates"])
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
        data=NodeData(label="Consumer", nodeType="polars", config={"steps": [source("score")]}),
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
            config={"steps": [source("score"), step("s2", "select", columns=["premium"])]},
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
        json={"steps": GOLDEN_STEPS, "input_names": ["quotes", "rates"]},
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
