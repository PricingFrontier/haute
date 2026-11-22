"""READ-21: every Polars step kind is rebuilt from the code it renders.

``haute._polars_steps_lift`` reverses the step renderer. For any valid step
list ``s`` of a stepped node, two properties hold:

- rebuilding the rendered steps gives their canonical form,
  ``lift_steps(render(s)) == canonical_steps(s)``;
- rendering the rebuilt steps gives the same code,
  ``render(lift_steps(render(s))) == render(s)``.

The canonical form of a structured step comes from the rules in
``specs/codegen/structured-syntax-boundary.md`` (Rebuilding steps from code),
not from reading its code back, so the first property tests the reader against
those rules. The generators draw every step kind, function, aggregate,
operator and literal type from the renderer's own catalogue, so a new entry is
generated as soon as it exists. The examples at the end pin each ambiguous form
to its documented step.
"""

from __future__ import annotations

import ast
import datetime as dt
import json
from collections.abc import Callable
from typing import Any

import pytest
from hypothesis import given, reject
from hypothesis import strategies as st

from haute._polars_steps import (
    _CHAIN_OPERATORS,
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
    OPERATORS,
    PIVOT_AGGREGATIONS,
    STEP_KINDS,
    STEPPED_NODE_TYPES,
    WINDOW_AGGREGATIONS,
    PolarsStepError,
    js_held,
    referenced_step_inputs,
    render_node_steps,
    render_polars_steps,
)
from haute._polars_steps_lift import StepLiftError, canonical_steps, lift_steps
from haute._types import NodeType
from tests._property_budget import pr_budget
from tests._source_files import REPO_ROOT
from tests.test_polars_steps_corpus import TRANSLATIONS

Step = dict[str, Any]
Strategy = st.SearchStrategy[Any]

#: One surface of each kind: a transform (input, edges), a Data Input (frame,
#: no inputs) and an External File (frame, edges).
NODE_TYPES = (NodeType.POLARS, NodeType.DATA_INPUT, NodeType.EXTERNAL_FILE)
COLUMNS = ("a", "premium", "region", "it's", 'say "hi"')
OUTPUT_NAMES = ("gross", "band", "it's", "x y")
INPUTS = ("quotes", "rates", "policies")
VARIABLES = ("rate", "label", "flag")
CONSTANTS = ("cap", "floor_2")
FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%Y%m%d %H:%M")


def assert_rebuilds(node_type: NodeType, steps: list[Step]) -> list[Step]:
    """Both properties for one step list, for a node whose inputs are those the steps
    read; returns the rebuilt steps."""
    rendered = render_node_steps(node_type, steps).code
    inputs = referenced_step_inputs(steps)
    canonical = canonical_steps(node_type, steps, inputs=inputs)
    lifted = lift_steps(node_type, rendered, inputs=inputs)
    assert lifted == canonical, rendered
    assert render_node_steps(node_type, lifted).code == rendered
    return lifted


def numbered(steps: list[Step]) -> list[Step]:
    return [{"id": f"s{i}", **step} for i, step in enumerate(steps)]


def rebuild_or_reject(node_type: NodeType, steps: list[Step]) -> list[Step]:
    """Both properties, for a generated list the renderer accepts.

    A list without code also rebuilds step for step, so a step the reader
    missed cannot hide as code on both sides of the first property.
    """
    try:
        render_node_steps(node_type, steps)
    except PolarsStepError:
        reject()
    lifted = assert_rebuilds(node_type, steps)
    if all(step["kind"] != "free_code" for step in steps):
        assert len(lifted) == len(steps)
        for step, lifted_step in zip(steps, lifted, strict=True):
            assert lifted_step["kind"] == step["kind"]
    return lifted


# ---------------------------------------------------------------------------
# Literals and operands
# ---------------------------------------------------------------------------


def literal(literal_type: str, value: object) -> Step:
    return {"kind": "literal", "type": literal_type, "value": value}


def col(name: str) -> Step:
    return {"kind": "column", "name": name}


def var(name: str) -> Step:
    return {"kind": "variable", "name": name}


def const(name: str) -> Step:
    return {"kind": "constant", "name": name}


numbers = st.one_of(
    st.integers(-(10**20), 10**20),
    st.floats(allow_nan=False, allow_infinity=False),
    st.sampled_from([0.0, -0.0, 1e20, 1e-7, 2.0]),
)
texts = st.text(max_size=5)
dates = st.dates(min_value=dt.date(1000, 1, 1)).map(dt.date.isoformat)
nulls = st.sampled_from([{"kind": "literal", "type": "null"}, literal("null", None)])
LITERALS: dict[str, Strategy] = {
    "number": numbers.map(lambda value: literal("number", value)),
    "text": texts.map(lambda value: literal("text", value)),
    "boolean": st.booleans().map(lambda value: literal("boolean", value)),
    "date": dates.map(lambda value: literal("date", value)),
    "null": nulls,
}
columns = st.sampled_from(COLUMNS)
constants = st.sampled_from(CONSTANTS).map(const)


def literals(*types: str) -> Strategy:
    return st.one_of([LITERALS[literal_type] for literal_type in types or LITERAL_TYPES])


def operands(
    variables: tuple[str, ...],
    depth: int,
    *,
    literal_types: tuple[str, ...] = LITERAL_TYPES,
    nested: bool = True,
) -> Strategy:
    """Any operand: a column, literal, variable, constant or (above depth 0) an expression."""
    options = [columns.map(col), constants]
    if literal_types:
        options.append(literals(*literal_types))
    if variables:
        options.append(st.sampled_from(variables).map(var))
    if nested and depth > 0:
        options.append(expressions(variables, depth - 1).map(lambda e: {"kind": "expr", "expr": e}))
    return st.one_of(options)


def annotated(strategy: Strategy) -> Strategy:
    """An expression, sometimes with the formula text the editor keeps beside it."""
    return st.tuples(strategy, st.none() | st.text(max_size=4)).map(
        lambda pair: pair[0] if pair[1] is None else {**pair[0], "text": pair[1]}
    )


def distinct(elements: Strategy, low: int, high: int) -> Strategy:
    return st.lists(elements, min_size=low, max_size=high, unique=True)


# ---------------------------------------------------------------------------
# Conditions
# ---------------------------------------------------------------------------

MEMBER_VALUES: dict[str, tuple[Strategy, str | None]] = {
    "integer": (
        st.integers(-(10**12), 10**12) | st.sampled_from([2.0, -0.0, 1e20]),
        "integer",
    ),
    "float": (numbers, "float"),
    "decimal": (
        st.integers(-(10**6), 10**6) | st.floats(-1e6, 1e6) | st.sampled_from([-0.0, 1e-7]),
        "decimal",
    ),
    "text": (texts, None),
    "boolean": (st.booleans(), None),
    "date": (dates, None),
}
assert set(NUMBER_TYPES) <= set(MEMBER_VALUES)


@st.composite
def memberships(draw: st.DrawFn, column: str, operator: str) -> Step:
    kind = draw(st.sampled_from(sorted(MEMBER_VALUES)))
    values, number_type = MEMBER_VALUES[kind]
    literal_type = "number" if number_type else kind
    drawn = draw(st.lists(values, min_size=1, max_size=3))
    condition: Step = {
        "column": column,
        "operator": operator,
        "values": [literal(literal_type, value) for value in drawn],
    }
    if number_type:
        condition["number_type"] = number_type
    return condition


@st.composite
def conditions(
    draw: st.DrawFn,
    variables: tuple[str, ...],
    depth: int,
    plain: bool,
    operator: str | None = None,
) -> Step:
    """One condition; *plain* is a row filter inside an aggregate, which nests nothing."""
    operator = operator or draw(st.sampled_from(OPERATORS))
    column = draw(columns)
    nested = not plain
    if operator in _COMPARISON_OPERATORS:
        value = draw(operands(variables, depth, nested=nested))
        return {"column": column, "operator": operator, "value": value}
    if operator in _STRING_OPERATORS:
        value = draw(operands(variables, depth, literal_types=("text",), nested=nested))
        return {"column": column, "operator": operator, "value": value}
    if operator in _NO_VALUE_OPERATORS:
        return {"column": column, "operator": operator}
    assert operator in _MEMBERSHIP_OPERATORS, f"no generator for operator {operator!r}"
    return draw(memberships(column, operator))


def groups(variables: tuple[str, ...], depth: int, *, plain: bool = False) -> Strategy:
    return st.fixed_dictionaries(
        {
            "match": st.sampled_from(["all", "any"]),
            "conditions": st.lists(conditions(variables, depth, plain), min_size=1, max_size=3),
        }
    )


# ---------------------------------------------------------------------------
# Expressions
# ---------------------------------------------------------------------------


def arguments(argument_type: str, variables: tuple[str, ...], depth: int) -> Strategy:
    """A value for one function argument of *argument_type*."""
    value_operands = operands(variables, depth, literal_types=())
    table: dict[str, Strategy] = {
        "integer": st.integers(0, 10**6).map(lambda n: literal("number", n)) | constants,
        "int": st.integers(-(10**6), 10**6).map(lambda n: literal("number", n)) | constants,
        "number": literals("number") | constants,
        "text": literals("text") | constants,
        "format": st.sampled_from(FORMATS).map(lambda f: literal("text", f)) | constants,
        "dtype": st.sampled_from(CAST_DTYPES).map(lambda d: literal("text", d)),
        "number_operand": literals("number") | value_operands,
        "scalar_operand": literals("number", "text", "boolean") | value_operands,
    }
    return table[argument_type]


@st.composite
def functions(
    draw: st.DrawFn, variables: tuple[str, ...], depth: int, fn: str | None = None
) -> Step:
    name = fn or draw(st.sampled_from(sorted(FUNCTIONS)))
    argument_types, _ = FUNCTIONS[name]
    return {
        "type": "function",
        "fn": name,
        "operand": draw(operands(variables, depth)),
        "args": [draw(arguments(t, variables, depth)) for t in argument_types],
    }


@st.composite
def windows(
    draw: st.DrawFn, variables: tuple[str, ...], depth: int, agg: str | None = None
) -> Step:
    agg = agg or draw(st.sampled_from(WINDOW_AGGREGATIONS))
    window: Step = {"type": "window", "agg": agg, "over": draw(distinct(columns, 0, 2))}
    if agg not in ("len", "row_number"):
        value = columns.map(col)
        if depth > 0:
            nested = expressions(variables, depth - 1).map(lambda e: {"kind": "expr", "expr": e})
            value = value | nested
        window["of"] = draw(value)
    if agg == "quantile" or draw(st.booleans()):
        # A quantile off a quantile window is ignored by the renderer.
        window["quantile"] = draw(st.floats(0, 1) | st.sampled_from([0, 1]))
    if draw(st.booleans()):
        window["descending"] = draw(st.booleans())
    if window["over"] and draw(st.booleans()):
        flag = draw(st.booleans())
        order = draw(distinct(columns, 1, 2))
        window["order_by"] = [
            {"column": c, "descending": flag} if flag or draw(st.booleans()) else {"column": c}
            for c in order
        ]
    if agg in AGGREGATIONS and draw(st.booleans()):
        window["where"] = draw(groups(variables, depth, plain=True))
    return window


@st.composite
def conditionals(draw: st.DrawFn, variables: tuple[str, ...], depth: int) -> Step:
    branch = st.fixed_dictionaries(
        {
            "match": st.sampled_from(["all", "any"]),
            "conditions": st.lists(conditions(variables, depth, False), min_size=1, max_size=2),
            "then": operands(variables, depth),
        }
    )
    return {
        "type": "conditional",
        "branches": draw(st.lists(branch, min_size=1, max_size=2)),
        "otherwise": draw(operands(variables, depth)),
    }


@st.composite
def concats(draw: st.DrawFn, variables: tuple[str, ...], depth: int) -> Step:
    expr: Step = {
        "type": "concat",
        "parts": draw(st.lists(operands(variables, depth), min_size=2, max_size=3)),
    }
    if draw(st.booleans()):
        expr["separator"] = draw(texts)
    return expr


@st.composite
def chains(draw: st.DrawFn, variables: tuple[str, ...]) -> Step:
    """A chain of one operator, two to twenty terms long, nested on the left."""
    op = draw(st.sampled_from(sorted(_CHAIN_OPERATORS)))
    terms = draw(st.lists(operands(variables, 0), min_size=2, max_size=20))
    expr: Step = {"type": "binary", "left": terms[0], "op": op, "right": terms[1]}
    for term in terms[2:]:
        expr = {"type": "binary", "left": {"kind": "expr", "expr": expr}, "op": op, "right": term}
    return expr


def expressions(variables: tuple[str, ...], depth: int) -> Strategy:
    """Any expression; an operand nests a further expression only above depth 0."""
    return st.one_of(
        chains(variables),
        annotated(operands(variables, depth).map(lambda o: {"type": "operand", "operand": o})),
        annotated(
            st.fixed_dictionaries(
                {
                    "type": st.just("binary"),
                    "left": operands(variables, depth),
                    "op": st.sampled_from(BINARY_OPERATORS),
                    "right": operands(variables, depth),
                }
            )
        ),
        annotated(functions(variables, depth)),
        conditionals(variables, depth),
        windows(variables, depth),
        concats(variables, depth),
        annotated(
            st.fixed_dictionaries(
                {
                    "type": st.sampled_from(("sum_horizontal", "coalesce")),
                    "parts": st.lists(operands(variables, depth), min_size=2, max_size=3),
                }
            )
        ),
    )


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


@st.composite
def selections(draw: st.DrawFn, kind: str) -> Step:
    chosen = draw(distinct(columns, 0, 3))
    dtypes = draw(st.none() | distinct(st.sampled_from(CAST_DTYPES), 0, 2))
    if not chosen and not dtypes:
        chosen = [draw(columns)]
    step: Step = {"kind": kind, "columns": chosen}
    if dtypes is not None:
        step["dtypes"] = dtypes
    return step


@st.composite
def casts(draw: st.DrawFn) -> Step:
    entries = []
    for column in draw(distinct(columns, 1, 3)):
        dtype = draw(st.sampled_from(CAST_DTYPES))
        entry: Step = {"column": column, "dtype": dtype}
        if dtype in DATE_PARSE_TARGETS and draw(st.booleans()):
            entry["format"] = draw(st.sampled_from(FORMATS))
        entries.append(entry)
    return {"kind": "cast", "casts": entries, "strict": draw(st.booleans())}


@st.composite
def aggregations(draw: st.DrawFn, variables: tuple[str, ...], index: int) -> Step:
    agg = draw(st.sampled_from(AGGREGATIONS))
    if agg != "len" and draw(st.integers(0, 3)) == 0:
        entry: Step = {"dtype": draw(st.sampled_from(CAST_DTYPES)), "agg": agg, "suffix": "_s"}
    else:
        entry = {"agg": agg, "name": f"agg_{index}"}
        if agg != "len" or draw(st.booleans()):
            entry["column"] = draw(columns)
        if draw(st.booleans()):
            entry["where"] = draw(groups(variables, 0, plain=True))
    if agg == "quantile" or draw(st.integers(0, 4)) == 0:
        entry["quantile"] = draw(st.floats(0, 1))
    return entry


@st.composite
def group_bys(draw: st.DrawFn, variables: tuple[str, ...]) -> Step:
    keys = draw(distinct(columns, 0, 2))
    if keys and draw(st.integers(0, 3)) == 0:
        return draw(pivot_shaped_group_bys(keys))
    count = draw(st.integers(1, 3))
    entries = [draw(aggregations(variables, i)) for i in range(count)]
    return {"kind": "group_by", "keys": keys, "aggregations": entries}


@st.composite
def pivot_cells(draw: st.DrawFn) -> list[Step]:
    """One to three distinct non-null literals of one type, as pivot column values."""
    literal_type = draw(st.sampled_from(["number", "text", "boolean", "date"]))
    raw = {"number": numbers, "text": texts, "boolean": st.booleans(), "date": dates}[literal_type]
    values = draw(st.lists(raw, min_size=1, max_size=3, unique=True))
    return [literal(literal_type, value) for value in values]


@st.composite
def pivot_shaped_group_bys(draw: st.DrawFn, keys: list[str]) -> Step:
    """A group-by of one ``on`` value's cell per aggregate, which a pivot expresses too."""
    values, on = draw(columns), draw(columns)
    agg = draw(st.sampled_from([a for a in PIVOT_AGGREGATIONS if a != "len"]))
    entries = [
        {
            "column": values,
            "agg": agg,
            "name": f"cell_{i}",
            "where": {
                "match": draw(st.sampled_from(["all", "any"])),
                "conditions": [{"column": on, "operator": "eq", "value": cell}],
            },
        }
        for i, cell in enumerate(draw(pivot_cells()))
    ]
    return {"kind": "group_by", "keys": keys, "aggregations": entries}


@st.composite
def pivots(draw: st.DrawFn) -> Step:
    return {
        "kind": "pivot",
        "index": draw(distinct(columns, 1, 2)),
        "on": draw(columns),
        "values": draw(columns),
        "agg": draw(st.sampled_from(PIVOT_AGGREGATIONS)),
        "columns": [
            {"value": cell, "name": f"cell_{i}"} for i, cell in enumerate(draw(pivot_cells()))
        ],
    }


@st.composite
def unpivots(draw: st.DrawFn) -> Step:
    on = draw(distinct(columns, 1, 2))
    rest = [c for c in COLUMNS if c not in on]
    index = draw(distinct(st.sampled_from(rest), 0, 2)) if rest else []
    variable_name, value_name = draw(distinct(st.sampled_from(["variable", "value", "n"]), 2, 2))
    return {
        "kind": "unpivot",
        "on": on,
        "index": index,
        "variable_name": variable_name,
        "value_name": value_name,
    }


@st.composite
def joins(draw: st.DrawFn) -> Step:
    how = draw(st.sampled_from(JOIN_HOW))
    size = 0 if how == "cross" else draw(st.integers(1, 2))
    step: Step = {
        "kind": "join",
        "input": draw(st.sampled_from(INPUTS)),
        "how": how,
        "left_on": draw(st.lists(columns, min_size=size, max_size=size, unique=True)),
        "right_on": draw(st.lists(columns, min_size=size, max_size=size, unique=True)),
        "suffix": draw(st.sampled_from(["_right", "_r"])),
        "maintain_order": draw(st.sampled_from(JOIN_MAINTAIN_ORDER)),
    }
    if how in JOIN_VALIDATED_HOW and draw(st.booleans()):
        step["validate"] = draw(st.sampled_from(JOIN_VALIDATE))
    return step


@st.composite
def fill_nulls(draw: st.DrawFn, variables: tuple[str, ...]) -> Step:
    if draw(st.booleans()):
        fill: Step = {"kind": "strategy", "strategy": draw(st.sampled_from(FILL_STRATEGIES))}
    else:
        fill = {"kind": "value", "value": draw(operands(variables, 1))}
    return {"kind": "fill_null", "columns": draw(distinct(columns, 0, 2)), "fill": fill}


#: Authored statements no step renders: other calls, Haute's spelling changed,
#: compound statements, decorators, shared lines and comments inside them.
UNRECOGNISED = (
    'df = df.with_row_index("row")',
    "df = df.filter(pl.col('a') > 1)",
    "df = df.head( 3 )",
    "df = df.head(3)  # the first three",
    "first = 1; second = 2",
    "if True:\n    df = df.head(2)\nelse:\n    df = df.head(3)",
    "for _ in range(2):\n    df = df.head(5)\n    # still inside the loop",
    "def doubled(column):\n    return column * 2",
    "@staticmethod\ndef helper(value):\n    # inside the helper\n    return value",
    'note = """two\nlines"""',
    "df = (\n    df.head(2)\n)",
    "try:\n    df = df.head(1)\nexcept Exception:\n    pass",
    "total = 1 + 2",
    "df = df",
)
#: Authored statements exactly as Haute renders a step, which read back as that step.
RENDERED = (
    "df = df.head(3)",
    'df = df.drop("a")',
    "factor = 2",
    'df = df.with_columns(pl.col("a").cast(pl.Int64))',
)
TRIVIA = ("# a comment", "", "    # an indented comment", "#: a doc comment")
#: A step's comment: one to three comment or blank lines, each ending in a newline.
comments = st.lists(st.sampled_from(TRIVIA), min_size=1, max_size=3).map(
    lambda lines: "".join(f"{line}\n" for line in lines)
)


@st.composite
def free_codes(draw: st.DrawFn) -> Step:
    """Authored code, or (one time in four) only comments and blank lines."""
    if draw(st.integers(0, 3)) == 0:
        lines = draw(st.lists(st.sampled_from(TRIVIA), max_size=2))
        lines.insert(draw(st.integers(0, len(lines))), draw(st.sampled_from(TRIVIA[::2])))
        return {"kind": "free_code", "code": "\n".join(lines)}
    lines = []
    for _ in range(draw(st.integers(1, 3))):
        lines.extend(draw(st.lists(st.sampled_from(TRIVIA), max_size=2)))
        lines.append(draw(st.sampled_from(UNRECOGNISED + RENDERED)))
    lines.extend(draw(st.lists(st.sampled_from(TRIVIA[:1]), max_size=1)))
    return {"kind": "free_code", "code": "\n".join(lines)}


def commented(draw: Callable[[st.SearchStrategy[Any]], Any], step: Step) -> Step:
    """*step*, a structured one sometimes with a comment above it."""
    if step["kind"] == "free_code" or draw(st.integers(0, 2)):
        return step
    return {**step, "comment": draw(comments)}


def variables_step(name: str) -> Strategy:
    value = literals("number", "text", "boolean")
    return value.map(lambda v: {"kind": "variable", "name": name, "value": v})


#: A generator for each step kind, given the variables defined before it.
STEPS: dict[str, Callable[[tuple[str, ...]], Strategy]] = {
    "source": lambda v: st.sampled_from(INPUTS).map(lambda i: {"kind": "source", "input": i}),
    "filter": lambda v: groups(v, 1).map(lambda g: {"kind": "filter", **g}),
    "with_column": lambda v: st.fixed_dictionaries(
        {
            "kind": st.just("with_column"),
            "name": st.sampled_from(OUTPUT_NAMES),
            "expr": expressions(v, 2),
        }
    ),
    "select": lambda v: selections("select"),
    "drop": lambda v: selections("drop"),
    "rename": lambda v: distinct(columns, 1, 3).flatmap(
        lambda sources: st.lists(
            st.sampled_from(OUTPUT_NAMES), min_size=len(sources), max_size=len(sources)
        ).map(
            lambda targets: {
                "kind": "rename",
                "renames": [{"from": s, "to": t} for s, t in zip(sources, targets, strict=True)],
            }
        )
    ),
    "cast": lambda v: casts(),
    "sort": lambda v: st.fixed_dictionaries(
        {
            "kind": st.just("sort"),
            "keys": st.lists(
                st.fixed_dictionaries({"column": columns, "descending": st.booleans()}),
                min_size=1,
                max_size=3,
            ),
            "nulls_last": st.booleans(),
        }
    ),
    "unique": lambda v: st.fixed_dictionaries(
        {
            "kind": st.just("unique"),
            "columns": distinct(columns, 0, 3),
            "keep": st.sampled_from(["first", "last", "any", "none"]),
        }
    ),
    "group_by": lambda v: group_bys(v),
    "join": lambda v: joins(),
    "concat": lambda v: st.fixed_dictionaries(
        {
            "kind": st.just("concat"),
            "inputs": distinct(st.sampled_from(INPUTS), 1, 2),
            "how": st.sampled_from(["vertical", "diagonal"]),
        }
    ),
    "fill_null": lambda v: fill_nulls(v),
    "limit": lambda v: st.integers(1, 10**9).map(lambda n: {"kind": "limit", "n": n}),
    "variable": lambda v: variables_step("level"),
    "pivot": lambda v: pivots(),
    "unpivot": lambda v: unpivots(),
    "free_code": lambda v: free_codes(),
}
#: Step kinds that name the node's other inputs, which only an edges surface has.
INPUT_KINDS = ("join", "concat")


def test_every_step_kind_has_a_generator() -> None:
    assert list(STEPS) == list(STEP_KINDS)
    assert set(INPUT_KINDS) <= set(STEP_KINDS)


def starts(node_type: NodeType) -> list[Step]:
    """What a step list on *node_type* starts with before its body."""
    return [{"kind": "source", "input": "quotes"}] if _input_mode(node_type) else []


def _input_mode(node_type: NodeType) -> bool:
    return STEPPED_NODE_TYPES[node_type].start == "input"


@st.composite
def programs(draw: st.DrawFn) -> tuple[NodeType, list[Step]]:
    """A chain: the start, some variables, then any mix of the other step kinds."""
    node_type = draw(st.sampled_from(NODE_TYPES))
    edges = STEPPED_NODE_TYPES[node_type].inputs == "edges"
    steps = [commented(draw, start) for start in starts(node_type)]
    names = tuple(draw(distinct(st.sampled_from(VARIABLES), 0, 2)))
    steps.extend(commented(draw, draw(variables_step(name))) for name in names)
    body = [
        kind
        for kind in STEP_KINDS
        if kind not in ("source", "variable") and (edges or kind not in INPUT_KINDS)
    ]
    # Code steps are weighted up so that code mixes with steps in most chains.
    kinds = st.sampled_from([*body, "free_code", "free_code", "free_code"])
    for _ in range(draw(st.integers(0, 5))):
        steps.append(commented(draw, draw(STEPS[draw(kinds)](names))))
    return node_type, numbered(steps)


# ---------------------------------------------------------------------------
# The two properties
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", STEP_KINDS)
@pr_budget(max_examples=30)
@given(data=st.data())
def test_each_step_kind_alone_rebuilds_from_its_code(kind: str, data: st.DataObject) -> None:
    for node_type in (NodeType.POLARS, NodeType.DATA_INPUT):
        if kind == "source" and not _input_mode(node_type):
            continue
        if kind in INPUT_KINDS and STEPPED_NODE_TYPES[node_type].inputs != "edges":
            continue
        body = [] if kind == "source" else [commented(data.draw, data.draw(STEPS[kind](())))]
        lead = [{"kind": "source", "input": "quotes"}] if kind == "source" else starts(node_type)
        lead = [commented(data.draw, start) for start in lead]
        rebuild_or_reject(node_type, numbered([*lead, *body]))


@pr_budget(max_examples=80)
@given(program=programs())
def test_step_chains_rebuild_from_their_code(program: tuple[NodeType, list[Step]]) -> None:
    rebuild_or_reject(*program)


@pytest.mark.parametrize("fn", sorted(FUNCTIONS))
@pr_budget(max_examples=8)
@given(data=st.data())
def test_every_function_rebuilds_from_its_code(fn: str, data: st.DataObject) -> None:
    expr = data.draw(functions(("rate",), 1, fn))
    steps: list[Step] = [
        {"kind": "source", "input": "quotes"},
        {"kind": "variable", "name": "rate", "value": literal("number", 2)},
        {"kind": "with_column", "name": "out", "expr": expr},
    ]
    rebuild_or_reject(NodeType.POLARS, numbered(steps))


@pytest.mark.parametrize("agg", WINDOW_AGGREGATIONS)
@pr_budget(max_examples=8)
@given(data=st.data())
def test_every_window_aggregate_rebuilds_from_its_code(agg: str, data: st.DataObject) -> None:
    expr = data.draw(windows((), 1, agg))
    steps: list[Step] = [
        {"kind": "source", "input": "quotes"},
        {"kind": "with_column", "name": "out", "expr": expr},
    ]
    rebuild_or_reject(NodeType.POLARS, numbered(steps))


@pytest.mark.parametrize("operator", OPERATORS)
@pr_budget(max_examples=8)
@given(data=st.data())
def test_every_condition_operator_rebuilds_from_its_code(
    operator: str, data: st.DataObject
) -> None:
    condition = data.draw(conditions((), 1, False, operator))
    steps: list[Step] = [
        {"kind": "source", "input": "quotes"},
        {"kind": "filter", "match": "all", "conditions": [condition]},
    ]
    rebuild_or_reject(NodeType.POLARS, numbered(steps))


def _corpus_programs() -> list[tuple[str, list[Step]]]:
    found: list[tuple[str, list[Step]]] = []
    for snippet, entry in sorted(TRANSLATIONS.items()):
        if entry.get("steps") is None:
            continue
        found.append((snippet, entry["steps"]))
        found.extend((f"{snippet}:{name}", aux) for name, aux in (entry.get("aux") or {}).items())
    return found


@pytest.mark.parametrize(("snippet", "steps"), _corpus_programs())
def test_every_corpus_translation_rebuilds_without_code(snippet: str, steps: list[Step]) -> None:
    """The analysts' corpus rebuilds step for step: none of its steps comes back as code."""
    lifted = assert_rebuilds(NodeType.POLARS, steps)
    assert [step["kind"] for step in lifted] == [step["kind"] for step in steps], snippet


# ---------------------------------------------------------------------------
# Each ambiguous form, and how comments and code are kept
# ---------------------------------------------------------------------------

SOURCE: Step = {"kind": "source", "input": "quotes"}

#: The step lists the backend hands the editor, one per vocabulary entry.
_STEP_SHAPES_CASES: list[dict[str, Any]] = json.loads(
    (
        REPO_ROOT / "frontend/src/panels/editors/polarsSteps/__tests__/fixtures/stepShapes.json"
    ).read_text(encoding="utf-8")
)["cases"]


def rebuilt(steps: list[Step], node_type: NodeType = NodeType.POLARS) -> list[Step]:
    """The rebuilt steps without their ids, after checking both properties."""
    lifted = assert_rebuilds(node_type, numbered(steps))
    return [{key: value for key, value in step.items() if key != "id"} for step in lifted]


def test_rebuilt_steps_carry_ids_from_kind_and_place_only() -> None:
    lifted = lift_steps(NodeType.POLARS, "df = quotes\ndf = df.head(5)", inputs=INPUTS)
    assert [step["id"] for step in lifted] == ["source-1", "limit-2"]


def test_a_pivot_and_the_group_by_it_lowers_to_each_read_back_as_themselves() -> None:
    def cell(value: str, name: str, match: str = "all") -> Step:
        condition = {"column": "region", "operator": "eq", "value": literal("text", value)}
        return {
            "column": "premium",
            "agg": "sum",
            "name": name,
            "where": {"match": match, "conditions": [condition]},
        }

    pivot = {
        "kind": "pivot",
        "index": ["channel"],
        "on": "region",
        "values": "premium",
        "agg": "sum",
        "columns": [
            {"value": literal("text", "north"), "name": "north"},
            {"value": literal("text", "south"), "name": "south"},
        ],
    }
    group_by: Step = {
        "kind": "group_by",
        "keys": ["channel"],
        "aggregations": [cell("north", "north"), cell("south", "south", "any")],
    }
    # The pivot names its index as one list and the group-by its keys one by one, so
    # the code tells them apart and neither becomes the other on a round trip.
    pivot_code = render_node_steps(NodeType.POLARS, numbered([SOURCE, pivot])).code
    group_by_code = render_node_steps(NodeType.POLARS, numbered([SOURCE, group_by])).code
    assert 'df.group_by(["channel"], maintain_order=True)' in pivot_code
    assert 'df.group_by("channel", maintain_order=True)' in group_by_code
    assert rebuilt([SOURCE, pivot])[1] == pivot
    one_condition = {
        "match": "all",
        "conditions": group_by["aggregations"][1]["where"]["conditions"],
    }
    assert rebuilt([SOURCE, group_by])[1] == {
        **group_by,
        "aggregations": [
            cell("north", "north"),
            {**cell("south", "south"), "where": one_condition},
        ],
    }


def test_adjacent_code_steps_rebuild_as_one() -> None:
    steps = [
        SOURCE,
        {"kind": "free_code", "code": 'df = df.with_row_index("row")'},
        {"kind": "free_code", "code": "total = 1 + 2"},
    ]
    assert rebuilt(steps)[1:] == [
        {"kind": "free_code", "code": 'df = df.with_row_index("row")\ntotal = 1 + 2'}
    ]


def test_a_run_of_unrecognised_statements_is_one_code_step_between_steps() -> None:
    code = "\n".join(
        [
            "df = quotes",
            "df = df.head(5)",
            'df = df.with_row_index("row")',
            "if True:",
            "    df = df.head(2)",
            "total = 1 + 2",
            'df = df.drop("row")',
        ]
    )
    lifted = lift_steps(NodeType.POLARS, code, inputs=INPUTS)
    assert [step["kind"] for step in lifted] == [
        "source",
        "limit",
        "free_code",
        "drop",
    ]
    assert lifted[2]["code"] == "\n".join(code.split("\n")[2:6])


def test_code_exactly_as_a_step_renders_rebuilds_as_that_step() -> None:
    steps = [
        SOURCE,
        {"kind": "free_code", "code": 'x = 1 + 2\ndf = df.head(3)\nfactor = 2\ndf = df.drop("a")'},
    ]
    assert rebuilt(steps)[1:] == [
        {"kind": "free_code", "code": "x = 1 + 2"},
        {"kind": "limit", "n": 3},
        {"kind": "variable", "name": "factor", "value": literal("number", 2)},
        {"kind": "drop", "columns": ["a"]},
    ]


def test_the_divide_pattern_is_the_divide_function() -> None:
    divide = {
        "type": "function",
        "fn": "divide",
        "operand": col("premium"),
        "args": [col("exposure")],
    }
    step = {"kind": "with_column", "name": "rate", "expr": divide}
    code = render_node_steps(NodeType.POLARS, numbered([SOURCE, step])).code
    assert (
        'pl.when(pl.col("exposure") != 0)\n    .then(pl.col("premium") / pl.col("exposure"))'
    ) in code
    assert rebuilt([SOURCE, step])[1] == step
    # The same test with an otherwise is an if-then, not a divide.
    nonzero = {"column": "exposure", "operator": "ne", "value": literal("number", 0)}
    conditional = {
        "type": "conditional",
        "branches": [
            {
                "match": "all",
                "conditions": [nonzero],
                "then": {
                    "kind": "expr",
                    "expr": {
                        "type": "binary",
                        "left": col("premium"),
                        "op": "/",
                        "right": col("exposure"),
                    },
                },
            }
        ],
        "otherwise": literal("null", None),
    }
    as_conditional = {"kind": "with_column", "name": "rate", "expr": conditional}
    assert rebuilt([SOURCE, as_conditional])[1] == as_conditional


def test_one_condition_reads_as_all() -> None:
    condition = {"column": "a", "operator": "is_null"}
    steps = [SOURCE, {"kind": "filter", "match": "any", "conditions": [condition]}]
    assert rebuilt(steps)[1] == {"kind": "filter", "match": "all", "conditions": [condition]}


def test_an_operand_holding_an_operand_expression_rebuilds_as_the_operand() -> None:
    def wrap(operand: Step) -> Step:
        return {"kind": "expr", "expr": {"type": "operand", "operand": operand}}

    expr = {
        "type": "binary",
        "left": wrap(literal("number", 2)),  # pl.lit(2) either way on the left
        "op": "*",
        "right": wrap(literal("number", 3)),  # pl.lit(3) on the right differs from a bare 3
        "text": "2 * 3",
    }
    outer = {"type": "operand", "operand": {"kind": "expr", "expr": expr}}
    steps = [SOURCE, {"kind": "with_column", "name": "n", "expr": outer}]
    assert rebuilt(steps)[1]["expr"] == {
        "type": "binary",
        "left": literal("number", 2),
        "op": "*",
        "right": wrap(literal("number", 3)),
    }


def test_settings_the_code_does_not_hold_are_left_out() -> None:
    steps = [
        SOURCE,
        {
            "kind": "group_by",
            "keys": ["k"],
            "aggregations": [
                {"column": "ignored", "agg": "len", "name": "rows"},
                {"column": "a", "agg": "sum", "name": "total", "quantile": 0.5},
            ],
        },
        {"kind": "select", "columns": ["k"], "dtypes": []},
        {
            "kind": "with_column",
            "name": "joined",
            "expr": {"type": "concat", "parts": [col("a"), col("b")]},
        },
        {
            "kind": "with_column",
            "name": "first",
            "expr": {
                "type": "window",
                "agg": "first",
                "of": col("a"),
                "over": ["k"],
                "descending": True,
                "order_by": [{"column": "d"}],
            },
        },
        {
            "kind": "fill_null",
            "columns": ["a"],
            "fill": {"kind": "value", "value": {"kind": "literal", "type": "null"}},
        },
    ]
    assert rebuilt(steps)[1:] == [
        {
            "kind": "group_by",
            "keys": ["k"],
            "aggregations": [
                {"agg": "len", "name": "rows"},
                {"column": "a", "agg": "sum", "name": "total"},
            ],
        },
        {"kind": "select", "columns": ["k"]},
        {
            "kind": "with_column",
            "name": "joined",
            "expr": {"type": "concat", "parts": [col("a"), col("b")], "separator": ""},
        },
        {
            "kind": "with_column",
            "name": "first",
            "expr": {
                "type": "window",
                "agg": "first",
                "of": col("a"),
                "over": ["k"],
                "order_by": [{"column": "d", "descending": False}],
            },
        },
        {
            "kind": "fill_null",
            "columns": ["a"],
            "fill": {"kind": "value", "value": literal("null", None)},
        },
    ]


def test_membership_numbers_rebuild_as_their_number_type_writes_them() -> None:
    def member(number_type: str, *values: float) -> Step:
        return {
            "column": "a",
            "operator": "is_in",
            "values": [literal("number", value) for value in values],
            "number_type": number_type,
        }

    steps = [
        SOURCE,
        {
            "kind": "filter",
            "match": "all",
            "conditions": [
                member("integer", 2.0, -0.0),
                member("float", 1, 2.5),
                member("decimal", 1.0, -0.0, 0.1),
            ],
        },
    ]
    values = [
        [item["value"] for item in condition["values"]]
        for condition in rebuilt(steps)[1]["conditions"]
    ]
    assert values == [[2, 0], [1.0, 2.5], [1, -0.0, 0.1]]
    assert [type(v) for v in values[0] + values[1]] == [int, int, float, float]


_REBINDS_ITS_INPUT = (
    "import polars as pl\nimport haute\n\n"
    'pipeline = haute.Pipeline("p")\n\n\n'
    "@pipeline.polars\n"
    "def exposure() -> pl.LazyFrame:\n"
    '    return pl.LazyFrame({"days": [365.0, 730.0]})\n\n\n'
    "@pipeline.polars\n"
    "def years(exposure: pl.LazyFrame) -> pl.LazyFrame:\n"
    "    df = exposure\n"
    "    exposure = 365\n"
    '    df = df.with_columns((pl.col("days") / exposure).alias("years"))\n'
    "    return df\n"
)


def test_a_transform_that_rebinds_its_input_s_name_loads_that_statement_as_code() -> None:
    """CGEN-10: the lift tried each step without the node's input names, so it read
    ``exposure = 365`` as a Variable that a render against the inputs refuses, and a save
    after any edit wrote the incomplete-steps placeholder in place of the code."""
    from haute.codegen import graph_to_code
    from haute.executor import execute_graph
    from haute.parser import parse_pipeline_source

    graph = parse_pipeline_source(_REBINDS_ITS_INPUT)
    steps = graph.node_map["years"].data.config["steps"]
    assert [step["kind"] for step in steps] == ["source", "free_code"]
    assert steps[1]["code"] == (
        'exposure = 365\ndf = df.with_columns((pl.col("days") / exposure).alias("years"))'
    )
    saved = graph_to_code(graph, pipeline_name="p")
    assert "    exposure = 365\n" in saved and "NotImplementedError" not in saved
    result = execute_graph(graph, target_node_id="years")["years"]
    assert result.status == "ok", result.error
    assert [row["years"] for row in result.preview] == [1.0, 2.0]


@pytest.mark.parametrize(
    "case", _STEP_SHAPES_CASES, ids=[case["id"] for case in _STEP_SHAPES_CASES]
)
def test_every_lifted_list_renders_its_source_against_its_input_names(
    case: dict[str, Any],
) -> None:
    """CGEN-10: what the lift accepts is what a render against the node's inputs accepts."""
    inputs = referenced_step_inputs(case["steps"])
    code = render_polars_steps(case["steps"], inputs, start="input").code
    lifted = lift_steps(NodeType.POLARS, code, inputs=inputs)
    assert render_polars_steps(lifted, inputs, start="input").code == code


def _as_a_browser_holds_it(value: Any) -> Any:
    """*value* after ``JSON.stringify`` and ``JSON.parse`` in a browser and back to Python:
    every number one double, which JavaScript writes as its shortest digits."""
    if isinstance(value, bool) or not isinstance(value, int | float | list | dict):
        return value
    if isinstance(value, list):
        return [_as_a_browser_holds_it(item) for item in value]
    if isinstance(value, dict):
        return {key: _as_a_browser_holds_it(item) for key, item in value.items()}
    return js_held(value)


#: Numbers a browser would write otherwise: whole-number decimals, exponent forms, negative
#: zero and integers of 16 to 19 digits, beside ones it keeps.
_SPELLED_NUMBERS = (
    "1.0",
    "-1.0",
    "0.0",
    "-0.0",
    "2.50",
    "1e20",
    "1e21",
    "1.5e-07",
    "1000000000000000.0",
    "9007199254740993",
    "-9007199254740993",
    "1234567890123456789",
    "365",
    "0.1",
)
_SPELLING_STATEMENTS = (
    'df = df.with_columns(pl.lit({n}).alias("x"))',
    'df = df.filter(pl.col("id") == {n})',
    'df = df.with_columns((pl.col("v") * {n}).alias("v"))',
    'df = df.with_columns(pl.col("v").fill_null({n}))',
)


@pytest.mark.parametrize("spelled", _SPELLED_NUMBERS)
@pytest.mark.parametrize("statement", _SPELLING_STATEMENTS)
def test_a_number_renders_as_it_was_written_after_a_browser_held_its_step(
    statement: str, spelled: str
) -> None:
    """CGEN-09: ``pl.lit(1.0)`` came back ``pl.lit(1)``, a 32-bit integer column, and an
    integer past 2**53 named another row."""
    code = f"df = quotes\n{statement.format(n=spelled)}"
    lifted = lift_steps(NodeType.POLARS, code, inputs=["quotes"])
    assert len(lifted) == 2 and lifted[1]["kind"] != "free_code", lifted
    held = _as_a_browser_holds_it(lifted)
    assert render_polars_steps(held, ["quotes"], start="input").code == code


@pytest.mark.parametrize(
    "statement",
    [
        'df = df.filter(pl.col("x").is_in([9007199254740993, 1]))',
        'df = df.filter(pl.col("x").is_in([1.0, 2.50, -0.0]))',
        "df = df.filter(\n"
        '    pl.col("x").is_in(pl.Series(["1.50", "2"]).str.to_decimal(scale=2).implode())\n'
        ")",
    ],
)
def test_membership_numbers_render_as_they_were_written_after_a_browser(statement: str) -> None:
    """CGEN-09: a whole number past 2**53 kept its digits, and a decimal list its scale."""
    code = f"df = quotes\n{statement}"
    lifted = lift_steps(NodeType.POLARS, code, inputs=["quotes"])
    assert lifted[1]["kind"] == "filter"
    held = _as_a_browser_holds_it(lifted)
    assert render_polars_steps(held, ["quotes"], start="input").code == code


def test_a_spelling_is_kept_only_where_a_browser_would_write_the_number_otherwise() -> None:
    lifted = lift_steps(
        NodeType.POLARS,
        "df = quotes\n"
        'df = df.with_columns(pl.lit(1.0).alias("a"))\n'
        'df = df.with_columns(pl.lit(0.5).alias("b"))',
        inputs=["quotes"],
    )
    spelled = {"kind": "literal", "type": "number", "value": 1.0, "text": "1.0"}
    assert lifted[1]["expr"]["operand"] == spelled
    assert lifted[2]["expr"]["operand"] == {"kind": "literal", "type": "number", "value": 0.5}
    variable = {"kind": "variable", "name": "rate", "value": literal("number", 1.0)}
    assert (
        canonical_steps(NodeType.POLARS, numbered([SOURCE, variable]), inputs=INPUTS)[1]["value"]
        == spelled
    )


@pytest.mark.parametrize(
    ("operand", "fragment"),
    [
        pytest.param({"value": 2, "text": "1.0"}, "does not spell its value", id="stale"),
        pytest.param({"value": 1, "text": "one"}, "not a number as Python writes", id="words"),
        pytest.param({"value": 1, "text": "1_0"}, "not a number as Python writes", id="underscore"),
        pytest.param({"value": 1, "text": "1e400"}, "not a number as Python writes", id="infinite"),
    ],
)
def test_a_text_that_does_not_spell_its_number_is_refused(
    operand: dict[str, Any], fragment: str
) -> None:
    """The renderer writes a text into the code only once it is a number literal that spells
    the value the step holds: never a stale or a foreign one."""
    steps = [
        SOURCE,
        {
            "kind": "with_column",
            "name": "x",
            "expr": {
                "type": "operand",
                "operand": {"kind": "literal", "type": "number", **operand},
            },
        },
    ]
    with pytest.raises(PolarsStepError, match=fragment):
        render_polars_steps(numbered(steps), ["quotes"], start="input")


@pytest.mark.parametrize(
    ("number_type", "member", "fragment"),
    [
        pytest.param("integer", {"value": 1, "text": "1.0"}, "is not a whole number", id="integer"),
        pytest.param("float", {"value": 1, "text": "1"}, "a whole number, not a float", id="float"),
        pytest.param("decimal", {"value": 1e5, "text": "1e5"}, "has an exponent", id="decimal"),
    ],
)
def test_a_members_text_must_be_its_lists_kind(
    number_type: str, member: dict[str, Any], fragment: str
) -> None:
    condition = {
        "column": "x",
        "operator": "is_in",
        "number_type": number_type,
        "values": [{"kind": "literal", "type": "number", **member}],
    }
    steps = [SOURCE, {"kind": "filter", "match": "all", "conditions": [condition]}]
    with pytest.raises(PolarsStepError, match=fragment):
        render_polars_steps(numbered(steps), ["quotes"], start="input")


@pytest.mark.parametrize("text", ["2.0", "2e0", "2.", "0.2e1"])
def test_a_whole_number_argument_takes_only_a_whole_spelling(text: str) -> None:
    """Codex r1: Round's decimal places hold the whole number 2; a spelling that is not
    whole would write ``.round(2.0)``, which Polars refuses."""
    steps = [
        SOURCE,
        {
            "kind": "with_column",
            "name": "x",
            "expr": {
                "type": "function",
                "fn": "round",
                "operand": {"kind": "column", "name": "premium"},
                "args": [{"kind": "literal", "type": "number", "value": 2, "text": text}],
            },
        },
    ]
    with pytest.raises(PolarsStepError, match="is not a whole number"):
        render_polars_steps(numbered(steps), ["quotes"], start="input")
    whole = dict(steps[1])
    whole["expr"] = {
        **steps[1]["expr"],
        "args": [{"kind": "literal", "type": "number", "value": 2}],
    }
    rendered = render_polars_steps(numbered([SOURCE, whole]), ["quotes"], start="input")
    assert ".round(2)" in rendered.code


def test_a_text_on_a_value_that_is_no_number_is_refused() -> None:
    steps = [
        SOURCE,
        {
            "kind": "with_column",
            "name": "x",
            "expr": {
                "type": "operand",
                "operand": {"kind": "literal", "type": "text", "value": "a", "text": "1.0"},
            },
        },
    ]
    with pytest.raises(PolarsStepError, match="has no text"):
        render_polars_steps(numbered(steps), ["quotes"], start="input")


@pytest.mark.parametrize(
    "case", _STEP_SHAPES_CASES, ids=[case["id"] for case in _STEP_SHAPES_CASES]
)
def test_every_step_shape_renders_its_code_after_a_browser_held_it(case: dict[str, Any]) -> None:
    """CGEN-09: each lifted list, through a browser, renders the code it was lifted from."""
    inputs = referenced_step_inputs(case["steps"])
    code = render_polars_steps(case["steps"], inputs, start="input").code
    lifted = lift_steps(NodeType.POLARS, code, inputs=inputs)
    held = _as_a_browser_holds_it(lifted)
    assert render_polars_steps(held, inputs, start="input").code == code


def test_the_editors_case_file_of_number_renderings_is_the_servers() -> None:
    """CGEN-09: the editor decides when a typed number keeps its ``text`` by the server's
    own writing of the number it sends, held to this file by ``numberSpelling.test.ts``."""
    import importlib.util

    path = REPO_ROOT / "scripts" / "regen_number_renderings_fixture.py"
    spec = importlib.util.spec_from_file_location("regen_number_renderings_fixture", path)
    assert spec is not None and spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    on_disk = script.FIXTURE.read_text(encoding="utf-8")
    assert on_disk == script.render(script.build_fixture()), (
        "Run uv run python scripts/regen_number_renderings_fixture.py."
    )


@pytest.mark.parametrize("text", ["abc", "1e5", ""])
def test_a_decimal_membership_of_text_that_is_not_a_number_stays_code(text: str) -> None:
    """CGEN-02: the statement is kept as code, never a failure of the whole read."""
    statement = (
        f'df = df.filter(pl.col("x").is_in(pl.Series([{text!r}]).str.to_decimal(scale=2)'
        ".implode()))"
    )
    lifted = lift_steps(NodeType.POLARS, f"df = quotes\n{statement}", inputs=INPUTS)
    assert [step["kind"] for step in lifted] == ["source", "free_code"]
    assert lifted[1]["code"] == statement


#: What no step's reader expects where a number, a name or a setting stands.
_MALFORMED_LITERALS: tuple[str | int | float | bool | None, ...] = (
    "abc",
    "",
    "1e5",
    10**30,
    -0.0,
    True,
    None,
)


@pytest.mark.parametrize(
    "case", _STEP_SHAPES_CASES, ids=[case["id"] for case in _STEP_SHAPES_CASES]
)
def test_no_step_reader_raises_out_of_the_lift_on_a_malformed_literal(
    case: dict[str, Any],
) -> None:
    """CGEN-02: each statement every step renders, with each of its literals replaced by
    one no reader expects, is read as a step or kept as code; the lift never fails but
    with :class:`StepLiftError`."""
    code = render_node_steps(NodeType.POLARS, case["steps"]).code
    literals = sum(isinstance(node, ast.Constant) for node in ast.walk(ast.parse(code)))
    for index in range(literals):
        tree = ast.parse(code)
        constants = [node for node in ast.walk(tree) if isinstance(node, ast.Constant)]
        constants[index].value = _MALFORMED_LITERALS[index % len(_MALFORMED_LITERALS)]
        mutated = ast.unparse(tree)
        try:
            lift_steps(NodeType.POLARS, mutated, inputs=INPUTS)
        except StepLiftError:
            pass


def test_a_reader_that_fails_costs_its_statement_the_step_and_not_the_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CGEN-02: a defect in one step reader keeps the statement as code."""
    from haute import _polars_steps_lift

    def broken(self: object, call: object) -> Step:
        raise RuntimeError("a reader's defect")

    monkeypatch.setattr(_polars_steps_lift._Reader, "_limit", broken)
    lifted = lift_steps(NodeType.POLARS, "df = quotes\ndf = df.head(5)", inputs=INPUTS)
    assert [step["kind"] for step in lifted] == ["source", "free_code"]


def test_a_comment_belongs_to_the_step_below_it() -> None:
    """Comment lines above a step, wherever they were written, become its comment."""
    steps = [
        {**SOURCE, "comment": "# Quotes, as priced\n"},
        {"kind": "free_code", "code": "total = 1 + 2\n# keep five rows"},
        {"kind": "limit", "n": 5, "comment": "\n# then the first five\n"},
        {"kind": "limit", "n": 3},
    ]
    assert rebuilt(steps) == [
        {**SOURCE, "comment": "# Quotes, as priced\n"},
        {"kind": "free_code", "code": "total = 1 + 2"},
        {"kind": "limit", "n": 5, "comment": "# keep five rows\n\n# then the first five\n"},
        {"kind": "limit", "n": 3},
    ]


def test_a_hand_edit_to_one_statement_keeps_the_other_steps_and_comments() -> None:
    steps = [
        SOURCE,
        {"kind": "limit", "n": 5, "comment": "# keep five\n"},
        {"kind": "drop", "columns": ["a"], "comment": "# no a\n"},
        {"kind": "unique", "columns": [], "keep": "any"},
    ]
    code = render_node_steps(NodeType.POLARS, numbered(steps)).code
    edited = code.replace("df = df.head(5)", "df = df.head(5).with_row_index()")
    lifted = lift_steps(NodeType.POLARS, edited, inputs=INPUTS)
    assert [{k: v for k, v in s.items() if k != "id"} for s in lifted] == [
        SOURCE,
        {"kind": "free_code", "code": "# keep five\ndf = df.head(5).with_row_index()"},
        steps[2],
        steps[3],
    ]
    assert render_node_steps(NodeType.POLARS, lifted).code == edited


def test_a_comment_at_the_end_stays_at_the_end_and_the_last_step_stays_a_step() -> None:
    steps = [SOURCE, {"kind": "free_code", "code": "df = df.head(5)\n\n# the end"}]
    assert rebuilt(steps)[1:] == [
        {"kind": "limit", "n": 5},
        {"kind": "free_code", "code": "\n# the end"},
    ]
    # After code, the comment joins its code step, as adjacent code does.
    code_last = [SOURCE, {"kind": "free_code", "code": "total = 1 + 2\n# the end"}]
    assert rebuilt(code_last)[1:] == code_last[1:]


def test_a_comment_only_code_step_before_a_step_is_that_step_s_comment() -> None:
    steps = [
        SOURCE,
        {"kind": "free_code", "code": "# a note"},
        {"kind": "limit", "n": 5},
        {"kind": "free_code", "code": "# before code"},
        {"kind": "free_code", "code": "total = 1 + 2"},
    ]
    assert rebuilt(steps)[1:] == [
        {"kind": "limit", "n": 5, "comment": "# a note\n"},
        {"kind": "free_code", "code": "# before code\ntotal = 1 + 2"},
    ]


def test_a_blank_line_above_a_statement_is_its_comment() -> None:
    steps = [SOURCE, {"kind": "free_code", "code": "total = 1 + 2\n\ndf = df.head(5)"}]
    assert rebuilt(steps)[1:] == [
        {"kind": "free_code", "code": "total = 1 + 2"},
        {"kind": "limit", "n": 5, "comment": "\n"},
    ]


def test_a_comment_that_is_not_ruff_s_stays_code() -> None:
    """A comment line ending in whitespace is no step's comment; its statement stays code."""
    code = "df = quotes\n# trailing space \ndf = df.head(5)"
    with pytest.raises(StepLiftError, match="do not render the code exactly"):
        lift_steps(NodeType.POLARS, code, inputs=INPUTS)


def test_a_variable_whose_statement_stays_code_defines_nothing_after_it() -> None:
    code = 'df = quotes\nrate = 2  # two\ndf = df.with_columns(pl.lit(rate).alias("r"))'
    lifted = lift_steps(NodeType.POLARS, code, inputs=INPUTS)
    assert [step["kind"] for step in lifted] == ["source", "free_code"]


def test_a_variable_the_code_above_already_assigns_is_refused_and_rebuilt_as_code() -> None:
    steps = [
        SOURCE,
        {"kind": "free_code", "code": "rate = 2"},
        {"kind": "variable", "name": "rate", "value": literal("number", 3)},
    ]
    with pytest.raises(PolarsStepError, match="Variable 'rate' is already assigned"):
        canonical_steps(NodeType.POLARS, numbered(steps), inputs=INPUTS)
    # Written by hand, the second assignment is code either way.
    for first in ("rate = 2", "rate = round(2.5)", "for rate in range(2):\n    pass"):
        lifted = lift_steps(NodeType.POLARS, f"df = quotes\n{first}\nrate = 3", inputs=INPUTS)
        assert lifted[-1] == {
            "id": f"free_code-{len(lifted)}",
            "kind": "free_code",
            "code": "rate = 3" if first == "rate = 2" else f"{first}\nrate = 3",
        }


def test_a_twenty_term_sum_is_one_step_and_round_trips() -> None:
    expr: Step = {"type": "binary", "left": col("c0"), "op": "+", "right": col("c1")}
    for index in range(2, 20):
        nested = {"kind": "expr", "expr": expr}
        expr = {"type": "binary", "left": nested, "op": "+", "right": col(f"c{index}")}
    steps = [SOURCE, {"kind": "with_column", "name": "total", "expr": expr}]
    assert rebuilt(steps)[1] == steps[1]
    code = render_node_steps(NodeType.POLARS, numbered(steps)).code
    lifted = lift_steps(NodeType.POLARS, code, inputs=INPUTS)
    assert len(lifted) == 2
    assert render_node_steps(NodeType.POLARS, lifted).code == code


def test_a_twenty_deep_mix_of_operators_written_by_hand_stays_code() -> None:
    """The renderer refuses twenty levels of mixed operators, so their code is not a step."""
    formula = 'pl.col("c0")'
    for index in range(1, 21):
        formula = f'{formula} {"+-"[index % 2]} pl.col("c{index}")'
    code = f'df = quotes\ndf = df.with_columns(({formula}).alias("t"))'
    assert [step["kind"] for step in lift_steps(NodeType.POLARS, code, inputs=INPUTS)] == [
        "source",
        "free_code",
    ]


def test_a_body_ending_in_newlines_rebuilds_as_without_them() -> None:
    code = render_node_steps(NodeType.POLARS, numbered([SOURCE, {"kind": "limit", "n": 2}])).code
    assert lift_steps(NodeType.POLARS, code + "\n\n", inputs=INPUTS) == lift_steps(
        NodeType.POLARS, code, inputs=INPUTS
    )
    assert lift_steps(NodeType.DATA_INPUT, "\n", inputs=INPUTS) == []


def test_a_frame_surface_rebuilds_its_start_as_code() -> None:
    """A frame surface has no start step, so ``df = <name>`` there is code."""
    steps: list[Step] = [{"kind": "free_code", "code": "df = quotes"}, {"kind": "limit", "n": 2}]
    assert rebuilt(steps, NodeType.EXTERNAL_FILE) == steps
    assert lift_steps(NodeType.DATA_INPUT, "", inputs=INPUTS) == []


@pytest.mark.parametrize(
    ("node_type", "code", "fragment"),
    [
        (NodeType.POLARS, "df = (", "not valid Python"),
        (NodeType.POLARS, "", "does not rebuild"),
        (NodeType.POLARS, "x = 1\ndf = quotes", "does not rebuild"),
        (NodeType.DATA_INPUT, "df = df.head(1)  ", "do not render the code exactly"),
        (NodeType.DATA_INPUT, "return df", "does not rebuild"),
    ],
)
def test_code_that_does_not_render_back_is_refused(
    node_type: NodeType, code: str, fragment: str
) -> None:
    with pytest.raises(StepLiftError, match=fragment):
        lift_steps(node_type, code, inputs=INPUTS)
