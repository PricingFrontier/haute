#!/usr/bin/env python3
"""Regenerate the step shapes the step editor is checked against.

``frontend/src/panels/editors/polarsSteps/__tests__/fixtures/stepShapes.json``
holds the steps the backend hands the editor when it reads a stepped node back
from its code: each case is a step list rendered by ``haute._polars_steps`` and
read back by ``haute._polars_steps_lift.lift_steps``, as ``_config_builder``
does when it builds a node's config. The editor and the backend each describe
a step's shape by hand, so the editor can refuse a step the backend reads back
(a row count's aggregation without a column, Nov 2026). The editor's
``stepShapes.test.ts`` requires it to accept, summarise and open every step
here; ``tests/test_polars_steps_catalogue.py`` requires this file to equal
``canonical(build_fixture())`` and every vocabulary entry to appear in it.

The cases loop over the renderer's own vocabularies, so a new aggregate,
function, operator, type or join setting gets a case without anyone writing
one; a new argument or literal type stops this script until it has a sample.
Each case states its settings as an editor form would, including the ones the
backend drops when it reads the code back. After a change to the renderer or
the reader:

    uv run python scripts/regen_step_shapes_fixture.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from haute._polars_steps import (  # noqa: E402
    _COMPARISON_OPERATORS,
    _MEMBERSHIP_OPERATORS,
    _NO_VALUE_OPERATORS,
    _STRING_OPERATORS,
    AGGREGATIONS,
    BINARY_OPERATORS,
    CAST_DTYPES,
    DATE_PARSE_TARGETS,
    FILL_STRATEGIES,
    FUNCTIONS,
    JOIN_HOW,
    JOIN_MAINTAIN_ORDER,
    JOIN_VALIDATE,
    JOIN_VALIDATED_HOW,
    LITERAL_TYPES,
    NUMBER_TYPES,
    PIVOT_AGGREGATIONS,
    WINDOW_AGGREGATIONS,
    render_node_steps,
)
from haute._polars_steps_lift import lift_steps  # noqa: E402
from haute._types import NodeType  # noqa: E402

FIXTURE = (
    ROOT
    / "frontend"
    / "src"
    / "panels"
    / "editors"
    / "polarsSteps"
    / "__tests__"
    / "fixtures"
    / "stepShapes.json"
)
TRANSLATIONS = ROOT / "tests" / "fixtures" / "polars_steps_corpus" / "translations.json"

Step = dict[str, Any]

#: The settings the renderer checks inline rather than as a named vocabulary.
MATCH_MODES = ("all", "any")
UNIQUE_KEEP = ("first", "last", "any", "none")
CONCAT_HOW = ("vertical", "diagonal")

#: A value of each literal type; a new type has no sample until one is added here.
LITERAL_SAMPLES: dict[str, object] = {
    "number": 1.5,
    "text": "north",
    "boolean": True,
    "date": "2024-01-31",
    "null": None,
}
#: A membership list of each kind, with the number type a number list records.
MEMBER_SAMPLES: dict[str, tuple[str, list[object]]] = {
    "integer": ("number", [1, 2]),
    "float": ("number", [1.5, 2.25]),
    "decimal": ("number", [10, 20.5]),
    "text": ("text", ["web", "phone"]),
    "boolean": ("boolean", [True]),
    "date": ("date", ["2024-01-31"]),
}


def lit(literal_type: str, value: object) -> Step:
    return {"kind": "literal", "type": literal_type, "value": value}


def col(name: str) -> Step:
    return {"kind": "column", "name": name}


def nested(expr: Step) -> Step:
    return {"kind": "expr", "expr": expr}


#: A value of each function argument type; a new type has no sample until one is added.
ARGUMENT_SAMPLES: dict[str, Step] = {
    "integer": lit("number", 2),
    "int": lit("number", 1),
    "number": lit("number", 0.5),
    "text": lit("text", "-"),
    "format": lit("text", "%Y-%m-%d"),
    "dtype": lit("text", "Float64"),
    "number_operand": col("sum_insured"),
    "scalar_operand": lit("number", 0),
}


def source() -> Step:
    return {"kind": "source", "input": "quotes"}


def program(*steps: Step) -> list[Step]:
    """A step list on a transform's input: the source, then *steps*."""
    return [source(), *steps]


def with_column(expr: Step, name: str = "out") -> Step:
    return {"kind": "with_column", "name": name, "expr": expr}


def filter_on(*conditions: Step, match: str = "all") -> Step:
    return {"kind": "filter", "match": match, "conditions": list(conditions)}


def group_by(*aggregations: Step, keys: tuple[str, ...] = ("region",)) -> Step:
    return {"kind": "group_by", "keys": list(keys), "aggregations": list(aggregations)}


def row_filter() -> Step:
    """A row filter; one comparing a column with `eq` alone would read back as a pivot."""
    return {
        "match": "all",
        "conditions": [{"column": "premium", "operator": "gt", "value": lit("number", 100)}],
    }


def condition(operator: str) -> list[Step]:
    """Conditions using *operator*, one per kind of value it takes."""
    if operator in _COMPARISON_OPERATORS:
        return [
            {"column": "premium", "operator": operator, "value": lit("number", 100)},
            {"column": "premium", "operator": operator, "value": col("sum_insured")},
        ]
    if operator in _STRING_OPERATORS:
        return [{"column": "postcode", "operator": operator, "value": lit("text", "AB")}]
    if operator in _NO_VALUE_OPERATORS:
        return [{"column": "premium", "operator": operator}]
    if operator not in _MEMBERSHIP_OPERATORS:
        raise ValueError(f"No sample condition for operator {operator!r}; add one.")
    entries = []
    for kind, (literal_type, values) in MEMBER_SAMPLES.items():
        entry: Step = {
            "column": "region",
            "operator": operator,
            "values": [lit(literal_type, v) for v in values],
        }
        if kind in NUMBER_TYPES:
            entry["number_type"] = kind
        entries.append(entry)
    return entries


def window(agg: str, **settings: Any) -> Step:
    expr: Step = {"type": "window", "agg": agg, "over": ["region"]}
    if agg not in ("len", "row_number"):
        expr["of"] = col("premium")
    if agg == "quantile":
        expr["quantile"] = 0.9
    return {**expr, **settings}


def aggregation(agg: str, **settings: Any) -> Step:
    """One aggregation as the form leaves it: every entry names a column, even a row count."""
    entry: Step = {"column": "premium", "agg": agg, "name": f"premium_{agg}"}
    if agg == "quantile":
        entry["quantile"] = 0.9
    return {**entry, **settings}


def expression_cases() -> list[tuple[str, list[Step]]]:
    cases: list[tuple[str, list[Step]]] = []
    for literal_type in LITERAL_TYPES:
        value = lit(literal_type, LITERAL_SAMPLES[literal_type])
        cases.append(
            (f"operand-{literal_type}", program(with_column({"type": "operand", "operand": value})))
        )
    cases.append(
        (
            "operand-null-without-value",
            program(
                with_column({"type": "operand", "operand": {"kind": "literal", "type": "null"}})
            ),
        )
    )
    cases.append(
        ("operand-column", program(with_column({"type": "operand", "operand": col("premium")})))
    )
    cases.append(
        (
            "operand-constant",
            program(
                with_column({"type": "operand", "operand": {"kind": "constant", "name": "cap"}})
            ),
        )
    )
    cases.append(
        (
            "operand-variable",
            program(
                {"kind": "variable", "name": "rate", "value": lit("number", 1.1)},
                with_column({"type": "operand", "operand": {"kind": "variable", "name": "rate"}}),
            ),
        )
    )
    for op in BINARY_OPERATORS:
        cases.append(
            (
                f"binary-{op}",
                program(
                    with_column(
                        {
                            "type": "binary",
                            "left": col("premium"),
                            "op": op,
                            "right": lit("number", 2),
                        }
                    )
                ),
            )
        )
    chain = {"type": "binary", "left": col("a"), "op": "+", "right": col("b")}
    chain = {"type": "binary", "left": nested(chain), "op": "+", "right": col("c")}
    cases.append(("binary-chain", program(with_column(chain))))
    cases.append(
        (
            "binary-nested-with-formula-text",
            program(
                with_column(
                    {
                        "type": "binary",
                        "left": nested(
                            {"type": "binary", "left": col("a"), "op": "*", "right": col("b")}
                        ),
                        "op": "**",
                        "right": lit("number", 2),
                        "text": "(a * b) ** 2",
                    }
                )
            ),
        )
    )
    for fn, (argument_types, _template) in FUNCTIONS.items():
        args = [ARGUMENT_SAMPLES[t] for t in argument_types]
        cases.append(
            (
                f"function-{fn}",
                program(
                    with_column(
                        {"type": "function", "fn": fn, "operand": col("premium"), "args": args}
                    )
                ),
            )
        )
    cases.append(
        (
            "function-with-constant-argument",
            program(
                with_column(
                    {
                        "type": "function",
                        "fn": "round",
                        "operand": col("premium"),
                        "args": [{"kind": "constant", "name": "places"}],
                    }
                )
            ),
        )
    )
    branch = {
        "match": "all",
        "conditions": [{"column": "premium", "operator": "gt", "value": lit("number", 100)}],
        "then": lit("text", "high"),
    }
    cases.append(
        (
            "conditional-one-branch",
            program(
                with_column(
                    {"type": "conditional", "branches": [branch], "otherwise": lit("text", "low")}
                )
            ),
        )
    )
    later = {
        "match": "any",
        "conditions": [{"column": "premium", "operator": "is_null"}],
        "then": lit("null", None),
    }
    cases.append(
        (
            "conditional-two-branches",
            program(
                with_column(
                    {"type": "conditional", "branches": [branch, later], "otherwise": col("band")}
                )
            ),
        )
    )
    for agg in WINDOW_AGGREGATIONS:
        cases.append((f"window-{agg}", program(with_column(window(agg)))))
    cases.append(("window-whole-frame", program(with_column(window("sum", over=[])))))
    cases.append(
        (
            "window-of-expression",
            program(
                with_column(
                    window(
                        "mean",
                        of=nested(
                            {"type": "binary", "left": col("a"), "op": "*", "right": col("b")}
                        ),
                    )
                )
            ),
        )
    )
    cases.append(
        (
            "window-ordered",
            program(
                with_column(
                    window(
                        "cum_sum",
                        order_by=[
                            {"column": "start", "descending": False},
                            {"column": "premium", "descending": False},
                        ],
                    )
                )
            ),
        )
    )
    cases.append(
        (
            "window-ordered-descending",
            program(
                with_column(window("shift", order_by=[{"column": "start", "descending": True}]))
            ),
        )
    )
    cases.append(("window-rank-descending", program(with_column(window("rank", descending=True)))))
    cases.append(("window-rank-ascending", program(with_column(window("rank", descending=False)))))
    cases.append(("window-row-filter", program(with_column(window("sum", where=row_filter())))))
    cases.append(
        ("window-row-count-filter", program(with_column(window("len", where=row_filter()))))
    )
    cases.append(
        (
            "concat-default-separator",
            program(
                with_column({"type": "concat", "parts": [col("a"), col("b")], "separator": ""})
            ),
        )
    )
    cases.append(
        (
            "concat-separator",
            program(
                with_column(
                    {
                        "type": "concat",
                        "parts": [col("a"), lit("text", "-"), col("b")],
                        "separator": " ",
                    }
                )
            ),
        )
    )
    for kind in ("sum_horizontal", "coalesce"):
        cases.append(
            (
                kind,
                program(
                    with_column({"type": kind, "parts": [col("a"), col("b"), lit("number", 0)]})
                ),
            )
        )
        cases.append(
            (
                f"{kind}-with-formula-text",
                program(
                    with_column(
                        {"type": kind, "parts": [col("a"), col("b")], "text": f"{kind}(a, b)"}
                    )
                ),
            )
        )
    return cases


def filter_cases() -> list[tuple[str, list[Step]]]:
    cases: list[tuple[str, list[Step]]] = []
    for operator in (
        *_COMPARISON_OPERATORS,
        *_NO_VALUE_OPERATORS,
        *sorted(_MEMBERSHIP_OPERATORS),
        *_STRING_OPERATORS,
    ):
        for index, entry in enumerate(condition(operator)):
            cases.append((f"filter-{operator}-{index + 1}", program(filter_on(entry))))
    for match in MATCH_MODES:
        both = [
            {"column": "premium", "operator": "gt", "value": lit("number", 1)},
            {"column": "region", "operator": "is_not_null"},
        ]
        cases.append((f"filter-match-{match}", program(filter_on(*both, match=match))))
    cases.append(
        (
            "filter-value-expression",
            program(
                filter_on(
                    {
                        "column": "premium",
                        "operator": "gt",
                        "value": nested(
                            {
                                "type": "binary",
                                "left": col("sum_insured"),
                                "op": "*",
                                "right": lit("number", 0.01),
                            }
                        ),
                    }
                )
            ),
        )
    )
    return cases


def reshaping_cases() -> list[tuple[str, list[Step]]]:
    cases: list[tuple[str, list[Step]]] = []
    for kind in ("select", "drop"):
        cases.append((f"{kind}-columns", program({"kind": kind, "columns": ["premium", "region"]})))
        cases.append(
            (
                f"{kind}-columns-and-types",
                program({"kind": kind, "columns": ["region"], "dtypes": ["Float64", "Int64"]}),
            )
        )
        cases.append(
            (f"{kind}-types-only", program({"kind": kind, "columns": [], "dtypes": ["String"]}))
        )
        cases.append(
            (f"{kind}-empty-types", program({"kind": kind, "columns": ["premium"], "dtypes": []}))
        )
    cases.append(
        (
            "rename",
            program(
                {
                    "kind": "rename",
                    "renames": [
                        {"from": "premium", "to": "gross"},
                        {"from": "region", "to": "area"},
                    ],
                }
            ),
        )
    )
    for strict in (True, False):
        casts = [{"column": f"c_{dtype.lower()}", "dtype": dtype} for dtype in CAST_DTYPES]
        cases.append(
            (
                f"cast-every-type-strict-{str(strict).lower()}",
                program({"kind": "cast", "casts": casts, "strict": strict}),
            )
        )
    for dtype in DATE_PARSE_TARGETS:
        cases.append(
            (
                f"cast-{dtype.lower()}-with-format",
                program(
                    {
                        "kind": "cast",
                        "casts": [{"column": "start", "dtype": dtype, "format": "%d/%m/%Y"}],
                        "strict": True,
                    }
                ),
            )
        )
    for nulls_last in (True, False):
        keys = [
            {"column": "premium", "descending": True},
            {"column": "region", "descending": False},
        ]
        cases.append(
            (
                f"sort-nulls-last-{str(nulls_last).lower()}",
                program({"kind": "sort", "keys": keys, "nulls_last": nulls_last}),
            )
        )
    for keep in UNIQUE_KEEP:
        cases.append(
            (f"unique-{keep}", program({"kind": "unique", "columns": ["quote_id"], "keep": keep}))
        )
    cases.append(
        ("unique-every-column", program({"kind": "unique", "columns": [], "keep": "first"}))
    )
    for agg in AGGREGATIONS:
        cases.append((f"group-by-{agg}", program(group_by(aggregation(agg)))))
        cases.append(
            (f"group-by-{agg}-row-filter", program(group_by(aggregation(agg, where=row_filter()))))
        )
        if agg != "len":
            entry: Step = {"dtype": "Float64", "agg": agg, "suffix": f"_{agg}"}
            if agg == "quantile":
                entry["quantile"] = 0.9
            cases.append((f"group-by-{agg}-every-column-of-a-type", program(group_by(entry))))
    cases.append(
        ("group-by-row-count-without-column", program(group_by({"agg": "len", "name": "n"})))
    )
    cases.append(
        (
            "group-by-whole-frame",
            program(
                group_by(
                    aggregation("sum"),
                    {"agg": "len", "name": "rows"},
                    {"dtype": "Int64", "agg": "max", "suffix": "_max"},
                    keys=(),
                )
            ),
        )
    )
    cases.append(
        (
            "group-by-several-aggregations",
            program(
                group_by(
                    aggregation("first", name="first_value"),
                    aggregation("sum", column="item_premium", name="items"),
                    aggregation("len", name="rows"),
                    aggregation("count", column="final_premium", name="priced"),
                    keys=("quote_reference", "quote_option"),
                )
            ),
        )
    )
    cells = [
        {
            "column": "premium",
            "agg": "sum",
            "name": f"cell_{v}",
            "where": {
                "match": "all",
                "conditions": [{"column": "channel", "operator": "eq", "value": lit("text", v)}],
            },
        }
        for v in ("web", "phone")
    ]
    cases.append(("group-by-read-back-as-pivot", program(group_by(*cells))))
    for agg in PIVOT_AGGREGATIONS:
        pivot_columns = [
            {"value": lit("text", "web"), "name": "web"},
            {"value": lit("text", "phone"), "name": "phone"},
        ]
        cases.append(
            (
                f"pivot-{agg}",
                program(
                    {
                        "kind": "pivot",
                        "index": ["region"],
                        "on": "channel",
                        "values": "premium",
                        "agg": agg,
                        "columns": pivot_columns,
                    }
                ),
            )
        )
    for literal_type in ("number", "boolean", "date"):
        value = LITERAL_SAMPLES[literal_type] if literal_type != "number" else 1
        pivot_columns = [{"value": lit(literal_type, value), "name": "cell"}]
        cases.append(
            (
                f"pivot-{literal_type}-cells",
                program(
                    {
                        "kind": "pivot",
                        "index": ["region"],
                        "on": "flag",
                        "values": "premium",
                        "agg": "mean",
                        "columns": pivot_columns,
                    }
                ),
            )
        )
    cases.append(
        (
            "unpivot-with-index",
            program(
                {
                    "kind": "unpivot",
                    "on": ["premium", "sum_insured"],
                    "index": ["quote_id"],
                    "variable_name": "measure",
                    "value_name": "value",
                }
            ),
        )
    )
    cases.append(
        (
            "unpivot-without-index",
            program(
                {
                    "kind": "unpivot",
                    "on": ["premium"],
                    "index": [],
                    "variable_name": "variable",
                    "value_name": "value",
                }
            ),
        )
    )
    return cases


def input_cases() -> list[tuple[str, list[Step]]]:
    cases: list[tuple[str, list[Step]]] = []

    def join(how: str, **settings: Any) -> Step:
        keys = [] if how == "cross" else ["quote_id"]
        step: Step = {
            "kind": "join",
            "input": "rates",
            "how": how,
            "left_on": keys,
            "right_on": keys,
            "suffix": "_right",
            "maintain_order": "left_right",
        }
        return {**step, **settings}

    for how in JOIN_HOW:
        cases.append((f"join-{how}", program(join(how))))
    for order in JOIN_MAINTAIN_ORDER:
        cases.append((f"join-order-{order}", program(join("left", maintain_order=order))))
    for how in JOIN_VALIDATED_HOW:
        for validate in JOIN_VALIDATE:
            cases.append((f"join-{how}-validate-{validate}", program(join(how, validate=validate))))
    cases.append(
        (
            "join-two-keys-and-suffix",
            program(
                join(
                    "inner", left_on=["quote_id", "option"], right_on=["id", "option"], suffix="_r"
                )
            ),
        )
    )
    for how in CONCAT_HOW:
        cases.append(
            (
                f"concat-{how}",
                program({"kind": "concat", "inputs": ["rates", "policies"], "how": how}),
            )
        )
    return cases


def other_cases() -> list[tuple[str, list[Step]]]:
    cases: list[tuple[str, list[Step]]] = []
    for strategy in FILL_STRATEGIES:
        cases.append(
            (
                f"fill-null-{strategy}",
                program(
                    {
                        "kind": "fill_null",
                        "columns": ["premium"],
                        "fill": {"kind": "strategy", "strategy": strategy},
                    }
                ),
            )
        )
    cases.append(
        (
            "fill-null-value",
            program(
                {
                    "kind": "fill_null",
                    "columns": ["premium", "sum_insured"],
                    "fill": {"kind": "value", "value": lit("number", 0)},
                }
            ),
        )
    )
    cases.append(
        (
            "fill-null-column",
            program(
                {
                    "kind": "fill_null",
                    "columns": [],
                    "fill": {"kind": "value", "value": col("default")},
                }
            ),
        )
    )
    cases.append(("limit", program({"kind": "limit", "n": 5})))
    for literal_type in ("number", "text", "boolean"):
        cases.append(
            (
                f"variable-{literal_type}",
                program(
                    {
                        "kind": "variable",
                        "name": "level",
                        "value": lit(literal_type, LITERAL_SAMPLES[literal_type]),
                    }
                ),
            )
        )
    cases.append(
        (
            "free-code",
            program({"kind": "free_code", "code": 'df = df.with_row_index("row")\ntotal = 1 + 2'}),
        )
    )
    cases.append(
        ("free-code-comment-only", program({"kind": "free_code", "code": "# nothing to do yet"}))
    )
    cases.append(
        (
            "comment-above-a-step",
            program({"kind": "limit", "n": 3, "comment": "# the first three\n"}),
        )
    )
    return cases


def corpus_cases() -> list[tuple[str, list[Step]]]:
    translations = json.loads(TRANSLATIONS.read_text(encoding="utf-8"))
    return [
        (f"corpus-{sid}", entry["steps"])
        for sid, entry in translations.items()
        if entry.get("steps")
    ]


def cases() -> list[tuple[str, list[Step]]]:
    built = [
        *expression_cases(),
        *filter_cases(),
        *reshaping_cases(),
        *input_cases(),
        *other_cases(),
        *corpus_cases(),
    ]
    ids = [case_id for case_id, _ in built]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValueError(f"Duplicate case ids: {duplicates}")
    return built


def read_back(steps: list[Step]) -> list[Step]:
    """*steps* as the editor receives them: rendered to code, then read back from it."""
    numbered = [{"id": f"s{index}", **step} for index, step in enumerate(steps)]
    code = render_node_steps(NodeType.POLARS, numbered).code
    return lift_steps(NodeType.POLARS, code)


def build_fixture() -> dict[str, Any]:
    built = []
    for case_id, steps in cases():
        try:
            built.append({"id": case_id, "steps": read_back(steps)})
        except Exception as exc:
            raise ValueError(f"Case {case_id!r} does not render and read back: {exc}") from exc
    return {"cases": built}


def canonical(fixture: dict[str, Any]) -> str:
    """One case per line, so a change reads as the cases it touches."""
    lines = [
        json.dumps(case, ensure_ascii=False, separators=(",", ":")) for case in fixture["cases"]
    ]
    return '{"cases":[\n' + ",\n".join(lines) + "\n]}\n"


def main() -> None:
    FIXTURE.write_bytes(canonical(build_fixture()).encode("utf-8"))
    print(f"wrote {FIXTURE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
