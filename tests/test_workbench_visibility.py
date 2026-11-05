"""Which sheets, sections and components show for the values typed into the sheets, and which
input columns a quote leaves out (specs/workbench), held to the cases the view is held to."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter

from haute._workbench_form import Condition, FormSpec, SingleCondition
from haute._workbench_visibility import condition_fits, visibility

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "frontend/src/utils/__tests__/fixtures/sheetVisibility.json"
)
CASES: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_fixtures_forms_are_canonical_so_the_cases_cannot_drift_from_the_files_shape() -> None:
    for form in CASES["forms"].values():
        assert FormSpec.model_validate(form).model_dump(mode="json") == form


@pytest.mark.parametrize("case", CASES["cases"], ids=[case["name"] for case in CASES["cases"]])
def test_what_shows_is_what_the_shared_cases_say(case: dict[str, Any]) -> None:
    form = copy.deepcopy(CASES["forms"][case["form"]])
    replaced = case.get("show_when", {})
    shown_by: dict[str, dict[str, Any]] = {
        item["id"]: item
        for page in form["pages"]
        for section in page["sections"]
        for item in (page, section, *section["widgets"])
    }
    assert set(replaced) <= set(shown_by), (
        "the case names a sheet, section or component the form lacks"
    )
    for item_id, show_when in replaced.items():
        shown_by[item_id]["show_when"] = show_when
    for field in case.get("field_show_when", []):
        looks = shown_by[field["widget"]]["column_looks"]
        looks.append({"table": field["table"], "column": field["column"]})
        looks[-1]["show_when"] = field["show_when"]
    spec = FormSpec.model_validate(form)

    result = visibility(spec, case["values"])

    placed = [
        widget for page in spec.pages for section in page.sections for widget in section.widgets
    ]
    pages = {page.id for page in spec.pages}
    sections = {section.id for page in spec.pages for section in page.sections}
    widgets = {widget.id for widget in placed}
    assert sorted(pages - result.pages) == case["hidden_pages"]
    assert sorted(sections - result.sections) == case["hidden_sections"]
    assert sorted(widgets - result.widgets) == case["hidden_widgets"]
    # The fields of the components that show, but those the case says hide.
    fields = {
        (widget.id, field.table, field.column)
        for widget in placed
        if widget.id in result.widgets and widget.type in ("tableInput", "collection")
        for field in widget.fields
    }
    hiding = {tuple(field) for field in case.get("hidden_fields", [])}
    assert hiding <= fields, "the case hides a field no component that shows has"
    assert result.fields == fields - hiding
    assert sorted([list(pair) for pair in result.hidden]) == case["hidden_columns"]


def every_type_form(**show_when: Any) -> FormSpec:
    """The fixture's form of every type, its second sheet shown as *show_when* says."""
    form = copy.deepcopy(CASES["forms"]["tests"])
    form["pages"][1]["show_when"] = show_when
    return FormSpec.model_validate(form)


@pytest.mark.parametrize("op", ["equals", "one_of", "at_least"])
def test_a_tick_in_a_column_no_longer_true_false_is_never_a_number_or_a_value(op: str) -> None:
    # Python counts True as 1; a box ticked before its column became an Integer is neither.
    condition = {"table": "inputs", "column": "count", "op": op, "values": ["1"]}
    spec = every_type_form(match="all", conditions=[condition])

    assert visibility(spec, {"inputs": [{"count": True}]}).pages == {"p_main"}
    assert visibility(spec, {"inputs": [{"count": "1"}]}).pages == {"p_main", "p_target"}


@pytest.mark.parametrize(
    ("column", "op", "values", "fits"),
    [
        ("choice", "not_one_of", ["A", "B"], True),
        ("choice", "not_one_of", ["A", " "], False),
        ("amount", "at_most", ["£1,000.50"], True),
        ("count", "equals", ["1e3"], True),
        ("count", "equals", ["nan"], False),
        ("day", "at_least", ["2024-02-29"], True),
        ("day", "at_least", ["2023-02-29"], False),
        ("day", "at_least", ["２０２６-01-01"], False),
        ("tick", "not_ticked", [], True),
    ],
)
def test_a_condition_fits_while_its_values_are_ones_its_column_could_hold(
    column: str, op: str, values: list[str], fits: bool
) -> None:
    condition = Condition.model_validate(
        {"table": "inputs", "column": column, "op": op, "values": values}
    )

    assert condition_fits(every_type_form(), condition) is fits


def against(column: str, table: str = "inputs") -> dict[str, str]:
    return {"table": table, "column": column}


@pytest.mark.parametrize(
    ("condition", "fits"),
    [
        ({"column": "text", "op": "contains", "field": against("choice")}, True),
        ({"column": "day", "op": "above", "field": against("day"), "days": -30}, True),
        ({"column": "amount", "op": "at_most", "field": against("amount")}, True),
        # A field gives one value, in place of values typed, of the column's type.
        ({"column": "text", "op": "one_of", "field": against("choice")}, False),
        ({"column": "text", "op": "filled", "field": against("choice")}, False),
        (
            {"column": "text", "op": "equals", "values": ["A"], "field": against("choice")},
            False,
        ),
        ({"column": "amount", "op": "equals", "field": against("count")}, False),
        # The field is a one-row input table's.
        ({"column": "text", "op": "equals", "field": against("key", "lines")}, False),
        ({"column": "amount", "op": "equals", "field": against("o", "outputs")}, False),
        ({"column": "text", "op": "equals", "field": against("gone")}, False),
        # Days go to another Date alone.
        ({"column": "amount", "op": "above", "field": against("amount"), "days": 1}, False),
        ({"column": "day", "op": "above", "values": ["2026-01-01"], "days": 1}, False),
    ],
)
def test_a_condition_against_another_field_fits_while_that_field_can_give_its_value(
    condition: dict[str, Any], fits: bool
) -> None:
    single = Condition.model_validate({"table": "inputs", **condition})

    assert condition_fits(every_type_form(), single) is fits


@pytest.mark.parametrize(
    ("condition", "fits"),
    [
        # The price's columns are tested as typed values are, against values typed alone.
        ({"table": "outputs", "column": "o", "op": "above", "values": ["1"]}, True),
        ({"table": "outputs", "column": "o", "op": "ticked"}, False),
        ({"table": "outputs", "column": "o", "op": "above", "field": against("amount")}, False),
    ],
)
def test_a_condition_on_the_price_fits_as_a_typed_value_s_does_against_values_typed_alone(
    condition: dict[str, Any], fits: bool
) -> None:
    assert condition_fits(every_type_form(), Condition.model_validate(condition)) is fits


FILLED_V = {"column": "v", "op": "filled"}


def total(**changed: Any) -> dict[str, Any]:
    """A sum of the lines' numbers above 1,000, *changed* as given."""
    condition = {"table": "lines", "total": "sum", "column": "v", "where": []}
    return {**condition, "op": "above", "value": "£1,000", **changed}


@pytest.mark.parametrize(
    ("condition", "fits"),
    [
        ({"table": "lines", "rows": "every", "where": [FILLED_V]}, True),
        (
            {"table": "inputs", "rows": "any", "where": [{"column": "amount", "op": "filled"}]},
            False,
        ),
        ({"table": "lines", "column": "v", "op": "filled"}, False),
        ({"table": "lines", "where": [], "op": "at_least", "count": 2}, True),
        (
            {
                "table": "lines",
                "where": [{"column": "gone", "op": "filled"}],
                "op": "at_least",
                "count": 2,
            },
            False,
        ),
        ({"table": "inputs", "where": [], "op": "at_least", "count": 2}, False),
        ({"table": "outputs", "where": [], "op": "at_least", "count": 2}, False),
        (total(), True),
        (total(where=[FILLED_V, {"column": "key", "op": "equals", "values": ["A"]}]), True),
        (total(column="key"), False),
        (total(column="gone"), False),
        (total(value="lots"), False),
        (total(where=[{"column": "key", "op": "ticked"}]), False),
        # A test of a row against another column of the same row, of its type.
        (
            {
                "table": "lines",
                "rows": "any",
                "where": [{**FILLED_V, "op": "above", "field": "v"}],
            },
            True,
        ),
        (
            {
                "table": "lines",
                "rows": "any",
                "where": [{**FILLED_V, "op": "above", "field": "key"}],
            },
            False,
        ),
        (
            {
                "table": "lines",
                "rows": "any",
                "where": [{**FILLED_V, "op": "above", "field": "gone"}],
            },
            False,
        ),
    ],
)
def test_a_condition_fits_while_its_table_has_the_rows_it_tests(
    condition: dict[str, Any], fits: bool
) -> None:
    single = TypeAdapter(SingleCondition).validate_python(condition)

    assert condition_fits(every_type_form(), single) is fits
