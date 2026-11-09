"""Low-code Polars steps: materialisation and execution.

A stepped transform's code is materialised from its steps, every step kind runs through the
executor, a failed plan names the step that failed, and Polars 2 text dates, typed membership and
the operations Polars 2 refuses behave as the step contract states.
"""

from __future__ import annotations

import datetime as dt
import re
import warnings
from decimal import Decimal
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from haute._builders import _build_node_fn
from haute._code_extraction import (
    INCOMPLETE_TRANSFORM_MESSAGE,
)
from haute._polars_steps import (
    CAST_DTYPES,
    CAST_FAMILIES,
    CAST_REFUSALS,
    PolarsStepError,
    RenderedSteps,
    cast_family,
    render_polars_steps,
)
from haute._types import (
    PipelineGraph,
)
from haute.assistant._ops import apply_ops
from haute.assistant._wire_ops import UpdateNodeOp
from haute.chunking import classify_chunk_local_polars_code
from haute.codegen import graph_to_code
from haute.errors import ConfigError
from haute.executor import execute_graph
from tests._polars_steps_helpers import (
    GOLDEN_CODE,
    GOLDEN_STEPS,
    _capped_context,
    _frames,
    _ready_source,
    _run_steps,
    _stepped,
    _stepped_input,
    binary,
    col,
    cond,
    fn,
    num,
    source,
    step,
    text,
    window,
)
from tests.conftest import (
    make_edge,
)

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")


# ---------------------------------------------------------------------------
# Materialisation invariant
# ---------------------------------------------------------------------------


def test_node_data_materialises_code_from_steps() -> None:
    node = _stepped("t", GOLDEN_STEPS, code="df = stale")
    assert node.data.config["code"] == GOLDEN_CODE
    assert node.data.editor.steps_error is None

    broken = _stepped("t", [step("f", "filter", match="all", conditions=[])], code="df = stale")
    assert broken.data.config["code"] == ""
    assert (
        broken.data.editor.steps_error
        == "Step 1: The first step must choose the input to start from."
    )

    with pytest.raises(ValueError, match="must be a list"):
        _stepped("t", "not-a-list")  # type: ignore[arg-type]

    replaced = broken.with_config({"steps": [source()], "code": "zzz"})
    assert replaced.data.config["code"] == "df = quotes"
    assert replaced.data.editor.steps_error is None


def test_chunk_local_classifier_sees_materialised_code() -> None:
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
        "cast": [
            source(),
            step("x", "cast", strict=True, casts=[{"column": "premium", "dtype": "Int64"}]),
        ],
        "sort": [
            source(),
            step("x", "sort", keys=[{"column": "premium", "descending": True}], nulls_last=True),
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
                    "branches": [
                        {
                            "match": "all",
                            "conditions": [cond("premium", "ge", value=num(500))],
                            "then": text("north"),
                        }
                    ],
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
                expr={"type": "window", "agg": "sum", "of": col("premium"), "over": ["region"]},
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


def test_stepped_original_rejects_input_mapping(tmp_path: Path) -> None:
    quotes, rates = _frames(tmp_path)
    node = _stepped("t", [source()], input_mapping={"quotes": "quotes"})
    graph = PipelineGraph(nodes=[quotes, rates, node], edges=[make_edge("quotes", "t")])
    with pytest.raises(ConfigError, match="cannot carry input_mapping"):
        graph_to_code(graph, pipeline_name="main")
    with pytest.raises(ConfigError, match="cannot carry input_mapping"):
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
    monkeypatch.setattr(_graph_walker, "_located_step", lambda *_args: None)
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
    located = fn.failed_step(frame)
    assert located is not None
    assert (located.line, located.remedy) == (7, None)
    # A strict cast meeting a bad value fails only on data: its plan resolves, and each
    # step's schema shows nothing Polars 2 refuses, so no step is named.
    cast = [
        source(),
        step("c", "cast", strict=True, casts=[{"column": "region", "dtype": "Int64"}]),
    ]
    _name, fn, _is_source = _build_node_fn(
        _stepped("c", cast), source_names=["quotes"], source_ids=["quotes"]
    )
    replays["rendered"].clear()
    replays["run"].clear()
    assert fn.failed_step(frame) is None
    assert replays["rendered"] == [len(cast), 1]
    assert replays["run"] == [
        render_polars_steps(cast, start="input").code,
        render_polars_steps(cast[:1], start="input").code,
    ]


def test_a_strict_cast_data_error_keeps_no_line(
    tmp_path: Path, replays: dict[str, list[Any]]
) -> None:
    quotes, _rates = _frames(tmp_path)
    steps = [
        source(),
        step("c", "cast", strict=True, casts=[{"column": "region", "dtype": "Int64"}]),
    ]
    graph = PipelineGraph(nodes=[quotes, _stepped("t", steps)], edges=[make_edge("quotes", "t")])
    result = execute_graph(graph, target_node_id="t", execution_context=_capped_context())["t"]
    assert result.status == "error"
    assert "region" in str(result.error)
    assert result.error_line is None
    # A data error: the locator finds nothing Polars 2 refuses, so the message is its own.
    assert str(result.error).startswith("conversion from `str` to `i64` failed")


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
# Polars 2: text dates, typed membership, and what Polars 2 refuses (PL2-06)
# ---------------------------------------------------------------------------


def _members(*values: float) -> list[dict[str, Any]]:
    return [num(value) for value in values]


def _filter(*conditions: dict[str, Any]) -> list[dict[str, Any]]:
    return [source(), step("f", "filter", match="all", conditions=list(conditions))]


def test_change_types_parses_text_dates_with_their_format() -> None:
    steps = [
        source(),
        step(
            "c",
            "cast",
            strict=True,
            casts=[
                {"column": "start", "dtype": "Date", "format": "%d/%m/%Y"},
                {"column": "stamp", "dtype": "Datetime", "format": "%Y-%m-%d %H:%M"},
            ],
        ),
    ]
    code = render_polars_steps(steps, ["quotes"], start="input").code

    assert 'pl.col("start").str.to_date("%d/%m/%Y")' in code
    assert 'pl.col("stamp").str.to_datetime("%Y-%m-%d %H:%M")' in code
    out = _run_steps(steps, pl.DataFrame({"start": ["02/01/2024"], "stamp": ["2024-01-02 03:04"]}))
    assert out["start"].to_list() == [dt.date(2024, 1, 2)]
    assert out["stamp"].to_list() == [dt.datetime(2024, 1, 2, 3, 4)]


def test_change_types_gives_missing_values_where_a_value_does_not_convert() -> None:
    lenient = step(
        "c",
        "cast",
        strict=False,
        casts=[
            {"column": "n", "dtype": "Int64"},
            {"column": "start", "dtype": "Date", "format": "%d/%m/%Y"},
            {"column": "stamp", "dtype": "Datetime", "format": "%Y-%m-%d %H:%M"},
        ],
    )
    code = render_polars_steps([source(), lenient], ["quotes"], start="input").code

    assert 'pl.col("n").cast(pl.Int64, strict=False)' in code
    assert 'pl.col("start").str.to_date("%d/%m/%Y", strict=False)' in code
    assert 'pl.col("stamp").str.to_datetime("%Y-%m-%d %H:%M", strict=False)' in code
    frame = pl.DataFrame(
        {"n": ["7", "seven"], "start": ["02/01/2024", "soon"], "stamp": ["2024-01-02 03:04", "-"]}
    )
    out = _run_steps([source(), lenient], frame)
    assert out["n"].to_list() == [7, None]
    assert out["start"].to_list() == [dt.date(2024, 1, 2), None]
    assert out["stamp"].to_list() == [dt.datetime(2024, 1, 2, 3, 4), None]
    with pytest.raises(pl.exceptions.PolarsError):
        _run_steps([source(), {**lenient, "strict": True}], frame)


@pytest.mark.parametrize(
    ("strict", "message"), [(None, r"Missing field\(s\) \['strict'\]"), ("no", "true or false")]
)
def test_change_types_states_whether_it_is_strict(strict: object, message: str) -> None:
    cast = step("c", "cast", casts=[{"column": "n", "dtype": "Int64"}])
    if strict is not None:
        cast["strict"] = strict
    with pytest.raises(PolarsStepError, match=message):
        render_polars_steps([source(), cast], ["quotes"], start="input")


def test_formulas_parse_text_dates_strictly_or_to_null() -> None:
    frame = pl.DataFrame({"start": ["2024-01-02", "not a date"]})
    lenient = [
        source(),
        step("w", "with_column", name="d", expr=fn("try_to_date", col("start"), text("%Y-%m-%d"))),
        step(
            "t", "with_column", name="t", expr=fn("try_to_datetime", col("start"), text("%Y-%m-%d"))
        ),
    ]
    out = _run_steps(lenient, frame)
    assert out["d"].to_list() == [dt.date(2024, 1, 2), None]
    assert out["t"].to_list() == [dt.datetime(2024, 1, 2), None]

    strict = [
        source(),
        step("w", "with_column", name="d", expr=fn("to_date", col("start"), text("%Y-%m-%d"))),
    ]
    with pytest.raises(pl.exceptions.PolarsError):
        _run_steps(strict, frame)
    assert _run_steps(strict, frame.head(1))["d"].to_list() == [dt.date(2024, 1, 2)]


@pytest.mark.parametrize(
    ("steps", "message"),
    [
        (
            [
                source(),
                step(
                    "c",
                    "cast",
                    strict=True,
                    casts=[{"column": "a", "dtype": "Int64", "format": "%Y"}],
                ),
            ],
            "only a Date or Datetime cast parses with",
        ),
        (
            [
                source(),
                step(
                    "c",
                    "cast",
                    strict=True,
                    casts=[{"column": "a", "dtype": "Date", "format": "Y-m-d"}],
                ),
            ],
            "must be a date format such as '%Y-%m-%d'",
        ),
        (
            [
                source(),
                step("w", "with_column", name="d", expr=fn("to_date", col("a"), text("2024"))),
            ],
            "must be a date format such as '%Y-%m-%d'",
        ),
        (_filter(cond("a", "is_in", values=_members(1))), "number type must be one of"),
        (
            _filter(cond("a", "is_in", values=_members(1, 2.5), number_type="integer")),
            "must be whole numbers to match a whole-number column",
        ),
        (
            _filter(cond("a", "is_in", values=[text("x")], number_type="float")),
            "are not numbers, so they take no number type",
        ),
        (
            _filter(cond("a", "gt", value=num(1), number_type="float")),
            "takes no number type",
        ),
    ],
    ids=[
        "format-on-a-number",
        "format-without-a-directive",
        "parse-function-without-a-directive",
        "numbers-without-their-type",
        "fraction-for-whole-numbers",
        "number-type-on-text",
        "number-type-on-a-comparison",
    ],
)
def test_steps_polars_2_cannot_run_as_written_are_refused(
    steps: list[dict[str, Any]], message: str
) -> None:
    with pytest.raises(PolarsStepError, match=re.escape(message)):
        render_polars_steps(steps, ["quotes"], start="input")


@pytest.mark.parametrize(
    ("dtype", "number_type", "data", "members", "kept"),
    [
        (pl.Int8, "integer", [1, 100, 3], (100, 3), [100, 3]),
        (pl.UInt16, "integer", [1, 100, 3], (100, 3), [100, 3]),
        (pl.Int64, "integer", [1, 100, 3], (100, 3.0), [100, 3]),
        (pl.Float32, "float", [1.0, 100.0, 2.5], (100, 2.5), [100.0, 2.5]),
        (pl.Float64, "float", [1.0, 100.0, 2.5], (100, 2.5), [100.0, 2.5]),
        (pl.Decimal(10, 2), "decimal", ["1.00", "100.00", "2.50"], (100, 2.5), ["100.00", "2.50"]),
        (
            pl.Decimal(38, 3),
            "decimal",
            ["1.000", "100.000", "2.500"],
            (100, 2.5),
            ["100.000", "2.500"],
        ),
    ],
    ids=["Int8", "UInt16", "Int64", "Float32", "Float64", "Decimal(10,2)", "Decimal(38,3)"],
)
def test_a_numeric_membership_matches_by_value_on_every_width(
    dtype: pl.DataType,
    number_type: str,
    data: list[Any],
    members: tuple[float, ...],
    kept: list[Any],
) -> None:
    if isinstance(dtype, pl.Decimal):
        column = pl.Series("x", data).str.to_decimal(scale=dtype.scale).cast(dtype)
        expected: list[Any] = [Decimal(value) for value in kept]
    else:
        column = pl.Series("x", data, dtype=dtype)
        expected = kept
    frame = pl.DataFrame({"x": column})

    def kept_by(operator: str) -> list[Any]:
        condition = cond("x", operator, values=_members(*members), number_type=number_type)
        return _run_steps(_filter(condition), frame)["x"].to_list()

    assert kept_by("is_in") == expected
    assert kept_by("not_in") == [value for value in frame["x"].to_list() if value not in expected]


def test_decimal_members_stay_exact() -> None:
    column = pl.Series("x", ["0.1", "0.2", "0.3", "0.0000001"]).str.to_decimal(scale=7)
    steps = _filter(cond("x", "is_in", values=_members(0.1, 0.3, 1e-07), number_type="decimal"))

    code = render_polars_steps(steps, ["quotes"], start="input").code
    assert 'pl.Series(["0.1", "0.3", "0.0000001"]).str.to_decimal(scale=7).implode()' in code
    assert _run_steps(steps, pl.DataFrame({"x": column}))["x"].to_list() == [
        Decimal("0.1000000"),
        Decimal("0.3000000"),
        Decimal("0.0000001"),
    ]


def test_date_members_pass_as_one_list_without_a_warning() -> None:
    values = [{"kind": "literal", "type": "date", "value": "2024-01-01"}]
    frame = pl.DataFrame({"d": [dt.date(2024, 1, 1), dt.date(2024, 2, 1)]})

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        out = _run_steps(_filter(cond("d", "is_in", values=values)), frame)

    assert out["d"].to_list() == [dt.date(2024, 1, 1)]


def test_the_cast_table_is_what_polars_refuses() -> None:
    samples = {
        "integer": pl.Series([1], dtype=pl.Int32),
        "float": pl.Series([1.0], dtype=pl.Float32),
        "decimal": pl.Series([Decimal("1.00")], dtype=pl.Decimal(10, 2)),
        "boolean": pl.Series([True]),
        "text": pl.Series(["1"]),
        "category": pl.Series(["1"], dtype=pl.Categorical),
        "date": pl.Series([dt.date(2024, 1, 2)]),
        "datetime": pl.Series([dt.datetime(2024, 1, 2, 3)]),
        "duration": pl.Series([dt.timedelta(days=1)]),
        "time": pl.Series([dt.time(3)]),
        "binary": pl.Series([b"1"]),
        "null": pl.Series([None], dtype=pl.Null),
    }
    assert tuple(samples) == CAST_FAMILIES
    assert cast_family(pl.Enum(["1"])) == "category"
    assert cast_family(pl.List(pl.Int64)) is None
    for family, series in samples.items():
        assert cast_family(series.dtype) == family
        refused = set()
        for target in CAST_DTYPES:
            try:
                pl.DataFrame({"x": series}).select(
                    pl.col("x").cast(getattr(pl, target), strict=False)
                )
            except pl.exceptions.PolarsError:
                refused.add(target)
        assert refused == CAST_REFUSALS[family], family


@pytest.mark.parametrize(
    ("steps", "frame", "remedy"),
    [
        (
            [
                source(),
                step("c", "cast", strict=True, casts=[{"column": "start", "dtype": "Date"}]),
            ],
            pl.LazyFrame({"start": ["2024-01-02"]}),
            "'start' is text, which Polars 2 does not cast to Date: give the Change types "
            "entry the Format its dates are written in.",
        ),
        (
            [
                source(),
                step("c", "cast", strict=True, casts=[{"column": "band", "dtype": "Int64"}]),
            ],
            pl.LazyFrame({"band": pl.Series(["1"], dtype=pl.Categorical)}),
            "'band' is a category, which Polars 2 casts only to text: change it to String "
            "first, then to Int64.",
        ),
        (
            [
                source(),
                step("w", "with_column", name="d", expr=fn("try_cast", col("start"), text("Date"))),
            ],
            pl.LazyFrame({"start": ["2024-01-02"]}),
            "'start' is text, which Polars 2 does not cast to Date: parse it with "
            "try_to_date(start, '%Y-%m-%d'), giving the format its dates are written in.",
        ),
        (
            [
                source(),
                step(
                    "g",
                    "group_by",
                    keys=["k"],
                    aggregations=[{"column": "wait", "agg": "std", "name": "spread"}],
                ),
            ],
            # Two rows: Polars answers one value with null before checking its type.
            pl.LazyFrame({"k": ["a", "a"], "wait": [dt.timedelta(days=1), dt.timedelta(days=3)]}),
            "std takes numbers, and 'wait' is a duration, which Polars 2 refuses: turn it "
            "into days first, as total_days(wait).",
        ),
        (
            [source(), window("spread", "var", "wait", ["k"])],
            pl.LazyFrame({"k": ["a", "a"], "wait": [dt.timedelta(days=1), dt.timedelta(days=3)]}),
            "var takes numbers, and 'wait' is a duration, which Polars 2 refuses: turn it "
            "into days first, as total_days(wait).",
        ),
    ],
    ids=[
        "text-to-date",
        "category-to-number",
        "formula-try-cast-to-date",
        "std-of-a-duration",
        "window-var-of-a-duration",
    ],
)
def test_a_refusal_polars_2_makes_only_on_rows_names_its_step(
    steps: list[dict[str, Any]], frame: pl.LazyFrame, remedy: str
) -> None:
    rendered = render_polars_steps(steps, ["quotes"], start="input")
    _name, transform, _is_source = _build_node_fn(
        _stepped("t", steps), source_names=["quotes"], source_ids=["quotes"]
    )
    # The plan resolves: Polars refuses only once rows arrive.
    transform(frame).collect_schema()
    with pytest.raises(pl.exceptions.PolarsError):
        transform(frame).collect(engine="streaming")

    located = transform.failed_step(frame)

    assert located is not None
    assert located.line == rendered.step_lines[1][0]
    assert located.remedy == remedy


def test_a_failed_run_starts_with_what_to_change(tmp_path: Path) -> None:
    path = tmp_path / "quotes.parquet"
    pl.DataFrame({"start": ["2024-01-02"], "premium": [1.0]}).write_parquet(path)
    quotes = _ready_source("quotes", path)
    steps = [
        source(),
        step("l", "limit", n=5),
        step("c", "cast", strict=True, casts=[{"column": "start", "dtype": "Date"}]),
    ]
    graph = PipelineGraph(nodes=[quotes, _stepped("t", steps)], edges=[make_edge("quotes", "t")])

    result = execute_graph(graph, target_node_id="t", execution_context=_capped_context())["t"]

    assert result.status == "error"
    first, rest = str(result.error).split("\n", 1)
    assert first.startswith("'start' is text, which Polars 2 does not cast to Date")
    assert "casting from string to date is not supported" in rest
    assert result.error_line == render_polars_steps(steps, start="input").step_lines[2][0]


def test_a_membership_whose_column_changes_kind_fails_naming_its_step(tmp_path: Path) -> None:
    path = tmp_path / "quotes.parquet"
    pl.DataFrame({"premium": [100.0, 2.5]}).write_parquet(path)
    quotes = _ready_source("quotes", path)
    # Recorded for whole numbers, the column is now a float column.
    steps = _filter(cond("premium", "is_in", values=_members(100), number_type="integer"))
    graph = PipelineGraph(nodes=[quotes, _stepped("t", steps)], edges=[make_edge("quotes", "t")])

    result = execute_graph(graph, target_node_id="t", execution_context=_capped_context())["t"]

    assert result.status == "error"
    assert "'is_in' cannot check for Float64 values in List(Int64) data" in str(result.error)
    assert result.error_line == render_polars_steps(steps, start="input").step_lines[1][0]
