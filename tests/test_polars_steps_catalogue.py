"""The editor's step catalogue mirrors the renderer's closed vocabularies by hand.

``frontend/src/panels/editors/polarsSteps/catalogue.ts`` lists every kind,
operator, aggregate, type and function the forms offer; the renderer in
``haute._polars_steps`` is the source of truth for what it accepts. This test
reads the catalogue's declarations and fails as soon as either side gains,
loses or reorders an entry the other does not know.

The editor's shape check (``stepProblem``) is a second hand-written copy, of
what a step may hold. ``scripts/regen_step_shapes_fixture.py`` writes the steps
the backend reads back from code for every vocabulary entry; the tests below
keep that file current and complete, and the editor's ``stepShapes.test.ts``
requires it to take every one.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import polars as pl
import pytest

from haute import _polars_steps as steps

CATALOGUE = (
    Path(__file__).resolve().parents[1]
    / "frontend"
    / "src"
    / "panels"
    / "editors"
    / "polarsSteps"
    / "catalogue.ts"
)
SOURCE = CATALOGUE.read_text(encoding="utf-8")


def declaration(name: str) -> str:
    """The text of one top-level ``const`` declaration in the catalogue."""
    pattern = rf"^(?:export )?const {name}\b.*?(?=^(?:export |const |function |type |/\*\*|//))"
    match = re.search(pattern, SOURCE, re.S | re.M)
    assert match, f"catalogue.ts declares no {name}"
    return match.group(0)


def entries(name: str, key: str = "value") -> list[str]:
    """The ``key: "..."`` values of an option list, in order."""
    return re.findall(rf'\b{key}: "([^"]+)"', declaration(name))


def strings(name: str) -> list[str]:
    """Every quoted string on the right of a plain list declaration, in order."""
    return re.findall(r'"([^"]+)"', declaration(name).split("=", 1)[1])


def test_step_kinds_and_their_required_fields_agree() -> None:
    required = declaration("REQUIRED_FIELDS")
    kinds = re.findall(r"^\s+(\w+): \[", required, re.M)
    assert kinds == list(steps.STEP_KINDS)
    assert entries("STEP_CATALOGUE", "kind") == [k for k in steps.STEP_KINDS if k != "source"]
    for kind, fields in re.findall(r"^\s+(\w+): \[(.*)\],?$", required, re.M):
        editor_fields = set(re.findall(r'\["(\w+)", "\w+"\]', fields))
        renderer_fields = steps._STEP_KEYS[kind] - steps._OPTIONAL_STEP_KEYS.get(kind, frozenset())
        assert editor_fields == renderer_fields, kind


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("CONDITION_OPERATORS", steps.OPERATORS),
        ("BINARY_OPERATORS", steps.BINARY_OPERATORS),
        ("AGGREGATIONS", steps.AGGREGATIONS),
        ("WINDOW_ONLY_AGGREGATIONS", steps.WINDOW_ONLY_AGGREGATIONS),
        ("PIVOT_AGGREGATIONS", steps.PIVOT_AGGREGATIONS),
        ("JOIN_HOW_OPTIONS", steps.JOIN_HOW),
        ("JOIN_VALIDATE", steps.JOIN_VALIDATE),
        ("JOIN_MAINTAIN_ORDER", steps.JOIN_MAINTAIN_ORDER),
        ("LITERAL_TYPES", steps.LITERAL_TYPES),
        ("FILL_STRATEGY_OPTIONS", steps.FILL_STRATEGIES),
    ],
)
def test_option_lists_match_the_renderer(name: str, expected: tuple[str, ...]) -> None:
    assert entries(name) == list(expected)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("CAST_DTYPES", steps.CAST_DTYPES),
        ("JOIN_VALIDATED_HOW", steps.JOIN_VALIDATED_HOW),
    ],
)
def test_plain_lists_match_the_renderer(name: str, expected: tuple[str, ...]) -> None:
    assert strings(name) == list(expected)


def test_number_types_agree() -> None:
    assert entries("NUMBER_TYPES") == list(steps.NUMBER_TYPES)


def test_the_cast_table_agrees() -> None:
    """The targets Change types withholds per column type are the renderer's refusals."""
    editor = {
        family: frozenset(re.findall(r'"(\w+)"', targets))
        for family, targets in re.findall(
            r"^\s+(\w+): \[(.*)\],?$", declaration("CAST_REFUSALS"), re.M
        )
    }
    assert editor == steps.CAST_REFUSALS


def test_date_format_defaults_are_formats_the_renderer_takes() -> None:
    defaults = dict(
        re.findall(r'^\s+(\w+): "([^"]+)",?$', declaration("DATE_FORMAT_DEFAULTS"), re.M)
    )
    assert set(defaults) == set(steps.DATE_PARSE_TARGETS)
    for dtype, date_format in defaults.items():
        rendered = steps.render_polars_steps(
            [
                {"id": "s", "kind": "source", "input": "quotes"},
                {
                    "id": "c",
                    "kind": "cast",
                    "strict": True,
                    "casts": [{"column": "a", "dtype": dtype, "format": date_format}],
                },
            ],
            ["quotes"],
            start="input",
        )
        assert date_format in rendered.code


def test_functions_and_their_argument_shapes_agree() -> None:
    declared = re.findall(
        r'\{ value: "(\w+)", label: "[^"]*", args: \[([^\]]*)\] \}', declaration("FUNCTIONS")
    )
    editor = {name: tuple(re.findall(r'"(\w+)"', args)) for name, args in declared}
    renderer = {name: arg_types for name, (arg_types, _template) in steps.FUNCTIONS.items()}
    assert editor == renderer


def test_expression_depth_cap_agrees() -> None:
    match = re.search(r"^export const MAX_EXPR_DEPTH = (\d+)$", SOURCE, re.M)
    assert match
    assert int(match.group(1)) == steps.MAX_EXPR_DEPTH


def test_chain_operators_agree() -> None:
    """The operators whose left-nested chain counts as one level of nesting."""
    match = re.search(r"^export const CHAIN_OPERATORS\b.*= new Set\(\[(.*)\]\)$", SOURCE, re.M)
    assert match
    assert set(re.findall(r'"([^"]+)"', match.group(1))) == set(steps._CHAIN_OPERATORS)


def test_default_join_order_agrees() -> None:
    """A new join's row order, and the order a join type change moves to, match the renderer."""
    editor = dict(re.findall(r'^\s+(\w+): "(\w+)",?$', declaration("DEFAULT_JOIN_ORDER"), re.M))
    assert editor == steps.DEFAULT_JOIN_ORDER
    assert set(editor) == set(steps.JOIN_HOW)
    assert set(editor.values()) <= set(steps.JOIN_MAINTAIN_ORDER)


STEP_INPUTS = CATALOGUE.parents[3] / "utils" / "polarsStepInputs.ts"


def test_stepped_surface_table_agrees() -> None:
    """The browser mirror of ``STEPPED_NODE_TYPES`` names the same types, modes and eligibility."""
    source = STEP_INPUTS.read_text(encoding="utf-8")
    match = re.search(r"^export const STEPPED_NODE_TYPES[^=]*= \{\n(.*?)^\}", source, re.S | re.M)
    assert match, "polarsStepInputs.ts declares no STEPPED_NODE_TYPES"
    editor = {
        node_type: (start, inputs)
        for node_type, start, inputs in re.findall(
            r'^\s+(\w+): \{ start: "(\w+)", inputs: "(\w+)" \},?$', match.group(1), re.M
        )
    }
    renderer = {
        node_type.value: (surface.start, surface.inputs)
        for node_type, surface in steps.STEPPED_NODE_TYPES.items()
    }
    assert editor == renderer


JOIN_OUTPUTS = CATALOGUE.parent / "__tests__" / "fixtures" / "joinOutputs.json"


def _polars_dtype(name: str) -> pl.DataType:
    return getattr(pl, name)()


@pytest.mark.parametrize(
    "case",
    json.loads(JOIN_OUTPUTS.read_text(encoding="utf-8"))["cases"],
    ids=lambda case: f"{case['how']}-{case['keys']}",
)
def test_join_output_fixture_matches_polars(case: dict[str, object]) -> None:
    """The editor's join output columns (derivedColumns.ts) are the rendered join's real schema."""
    fixture = json.loads(JOIN_OUTPUTS.read_text(encoding="utf-8"))
    join = {
        "id": "j",
        "kind": "join",
        "input": "claims",
        "how": case["how"],
        "left_on": case["left_on"],
        "right_on": case["right_on"],
        "suffix": fixture["suffix"],
        "maintain_order": steps.DEFAULT_JOIN_ORDER[str(case["how"])],
    }
    code = steps.render_polars_steps(
        [{"id": "s", "kind": "source", "input": "quotes"}, join],
        ["quotes", "claims"],
        start="input",
    ).code
    scope: dict[str, object] = {
        "pl": pl,
        "quotes": pl.LazyFrame(
            schema={c["name"]: _polars_dtype(c["dtype"]) for c in fixture["left"]}
        ),
        "claims": pl.LazyFrame(
            schema={c["name"]: _polars_dtype(c["dtype"]) for c in fixture["right"]}
        ),
    }
    exec(code, scope)
    frame = scope["df"]
    assert isinstance(frame, pl.LazyFrame)
    assert frame.collect_schema().names() == case["output"]


REPO_ROOT = CATALOGUE.parents[5]


def _load_step_shapes() -> ModuleType:
    """The regenerator, the single source of the step shapes fixture's cases."""
    path = REPO_ROOT / "scripts" / "regen_step_shapes_fixture.py"
    spec = importlib.util.spec_from_file_location("regen_step_shapes_fixture", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


step_shapes = _load_step_shapes()


def test_step_shapes_fixture_is_what_the_backend_reads_back() -> None:
    """The editor is checked against exactly the steps today's renderer and reader produce."""
    on_disk = step_shapes.FIXTURE.read_text(encoding="utf-8")
    assert on_disk == step_shapes.canonical(step_shapes.build_fixture()), (
        "The step shapes fixture is stale: the step renderer, reader or vocabulary changed. "
        "Run `uv run python scripts/regen_step_shapes_fixture.py`, then the editor's "
        "stepShapes.test.ts, and make the editor take any shape it refuses."
    )


#: The expression types the renderer dispatches on, read from the renderer itself.
EXPRESSION_TYPES = frozenset(
    re.findall(r'kind == "(\w+)"', inspect.getsource(steps._Renderer._expr_body))
)


def _objects(value: object) -> Iterator[dict[str, Any]]:
    """Every JSON object in *value*, outermost first."""
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _objects(child)


def _vocabulary(fixture: dict[str, Any]) -> dict[str, set[str]]:
    """Each vocabulary's entries as the fixture's steps use them, by where they appear."""
    seen: dict[str, set[str]] = {}

    def add(name: str, value: object) -> None:
        seen.setdefault(name, set()).add(str(value))

    for obj in _objects(fixture["cases"]):
        if "id" in obj and "kind" in obj:
            kind = obj["kind"]
            add("step kinds", kind)
            if kind == "group_by":
                for entry in obj["aggregations"]:
                    add("type-wide aggregates" if "dtype" in entry else "aggregates", entry["agg"])
                    if "where" in entry:
                        add("filtered aggregates", entry["agg"])
            elif kind == "join":
                add("join types", obj["how"])
                add("join row orders", obj["maintain_order"])
                if "validate" in obj:
                    add("join validations", obj["validate"])
            elif kind == "cast":
                for entry in obj["casts"]:
                    add("cast types", entry["dtype"])
            elif kind == "fill_null" and obj["fill"]["kind"] == "strategy":
                add("fill strategies", obj["fill"]["strategy"])
            elif kind == "pivot":
                add("pivot aggregates", obj["agg"])
        elif "type" in obj and "kind" not in obj:
            add("expression types", obj["type"])
            if obj["type"] == "window":
                add("window aggregates", obj["agg"])
            elif obj["type"] == "function":
                add("functions", obj["fn"])
            elif obj["type"] == "binary":
                add("operators", obj["op"])
        elif "operator" in obj:
            add("condition operators", obj["operator"])
            if "number_type" in obj:
                add("number types", obj["number_type"])
        elif obj.get("kind") in steps.OPERAND_KINDS:
            add("operand kinds", obj["kind"])
            if obj["kind"] == "literal":
                add("literal types", obj["type"])
    return seen


def test_step_shapes_fixture_uses_every_vocabulary_entry() -> None:
    """Every entry the renderer takes has a case, so the editor is checked against its shape."""
    seen = _vocabulary(json.loads(step_shapes.FIXTURE.read_text(encoding="utf-8")))
    expected = {
        "step kinds": steps.STEP_KINDS,
        "aggregates": steps.AGGREGATIONS,
        "filtered aggregates": steps.AGGREGATIONS,
        "type-wide aggregates": tuple(a for a in steps.AGGREGATIONS if a != "len"),
        "window aggregates": steps.WINDOW_AGGREGATIONS,
        "pivot aggregates": steps.PIVOT_AGGREGATIONS,
        "expression types": EXPRESSION_TYPES,
        "functions": tuple(steps.FUNCTIONS),
        "operators": steps.BINARY_OPERATORS,
        "condition operators": steps.OPERATORS,
        "number types": steps.NUMBER_TYPES,
        "operand kinds": steps.OPERAND_KINDS,
        "literal types": steps.LITERAL_TYPES,
        "cast types": steps.CAST_DTYPES,
        "fill strategies": steps.FILL_STRATEGIES,
        "join types": steps.JOIN_HOW,
        "join row orders": steps.JOIN_MAINTAIN_ORDER,
        "join validations": steps.JOIN_VALIDATE,
    }
    assert {name: set(values) for name, values in expected.items()} == {
        name: seen.get(name, set()) for name in expected
    }
