"""The editor's step catalogue mirrors the renderer's closed vocabularies by hand.

``frontend/src/panels/editors/polarsSteps/catalogue.ts`` lists every kind,
operator, aggregate, type and function the forms offer; the renderer in
``haute._polars_steps`` is the source of truth for what it accepts. This test
reads the catalogue's declarations and fails as soon as either side gains,
loses or reorders an entry the other does not know.
"""

from __future__ import annotations

import re
from pathlib import Path

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
        ("JOIN_VALIDATE", steps.JOIN_VALIDATE),
        ("JOIN_MAINTAIN_ORDER", steps.JOIN_MAINTAIN_ORDER),
        ("LITERAL_TYPES", steps.LITERAL_TYPES),
    ],
)
def test_option_lists_match_the_renderer(name: str, expected: tuple[str, ...]) -> None:
    assert entries(name) == list(expected)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("CAST_DTYPES", steps.CAST_DTYPES),
        ("JOIN_HOW", steps.JOIN_HOW),
        ("JOIN_VALIDATED_HOW", steps.JOIN_VALIDATED_HOW),
        ("FILL_STRATEGIES", steps.FILL_STRATEGIES),
    ],
)
def test_plain_lists_match_the_renderer(name: str, expected: tuple[str, ...]) -> None:
    assert strings(name) == list(expected)


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
