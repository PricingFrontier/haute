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
    spec = FormSpec.model_validate(form)

    result = visibility(spec, case["values"])

    pages = {page.id for page in spec.pages}
    sections = {section.id for page in spec.pages for section in page.sections}
    widgets = {
        widget.id for page in spec.pages for section in page.sections for widget in section.widgets
    }
    assert sorted(pages - result.pages) == case["hidden_pages"]
    assert sorted(sections - result.sections) == case["hidden_sections"]
    assert sorted(widgets - result.widgets) == case["hidden_widgets"]
    assert sorted([list(pair) for pair in result.hidden]) == case["hidden_columns"]


def every_type_form(**show_when: Any) -> FormSpec:
    """The fixture's form of every type, its second sheet shown as *show_when* says."""
    form = copy.deepcopy(CASES["forms"]["tests"])
    form["pages"][1]["show_when"] = show_when
    return FormSpec.model_validate(form)


@pytest.mark.parametrize("op", ["equals", "one_of", "at_least"])
def test_a_tick_in_a_column_no_longer_true_false_is_never_a_number_or_a_value(op: str) -> None:
    # Python counts True as 1; a box ticked before its column became an Integer is neither.
    condition = {"table": "t_in", "column": "c_int", "op": op, "values": ["1"]}
    spec = every_type_form(match="all", conditions=[condition])

    assert visibility(spec, {"t_in": [{"c_int": True}]}).pages == {"p_main"}
    assert visibility(spec, {"t_in": [{"c_int": "1"}]}).pages == {"p_main", "p_target"}


@pytest.mark.parametrize(
    ("column", "op", "values", "fits"),
    [
        ("c_choice", "not_one_of", ["A", "B"], True),
        ("c_choice", "not_one_of", ["A", " "], False),
        ("c_num", "at_most", ["£1,000.50"], True),
        ("c_int", "equals", ["1e3"], True),
        ("c_int", "equals", ["nan"], False),
        ("c_day", "at_least", ["2024-02-29"], True),
        ("c_day", "at_least", ["2023-02-29"], False),
        ("c_day", "at_least", ["２０２６-01-01"], False),
        ("c_tick", "not_ticked", [], True),
    ],
)
def test_a_condition_fits_while_its_values_are_ones_its_column_could_hold(
    column: str, op: str, values: list[str], fits: bool
) -> None:
    condition = Condition.model_validate(
        {"table": "t_in", "column": column, "op": op, "values": values}
    )

    assert condition_fits(every_type_form(), condition) is fits


FILLED_V = {"column": "c_v", "op": "filled"}


def total(**changed: Any) -> dict[str, Any]:
    """A sum of t_many's numbers above 1,000, *changed* as given."""
    condition = {"table": "t_many", "total": "sum", "column": "c_v", "where": []}
    return {**condition, "op": "above", "value": "£1,000", **changed}


@pytest.mark.parametrize(
    ("condition", "fits"),
    [
        ({"table": "t_many", "rows": "every", "where": [FILLED_V]}, True),
        ({"table": "t_in", "rows": "any", "where": [{"column": "c_num", "op": "filled"}]}, False),
        ({"table": "t_many", "column": "c_v", "op": "filled"}, False),
        ({"table": "t_many", "where": [], "op": "at_least", "count": 2}, True),
        (
            {
                "table": "t_many",
                "where": [{"column": "c_gone", "op": "filled"}],
                "op": "at_least",
                "count": 2,
            },
            False,
        ),
        ({"table": "t_in", "where": [], "op": "at_least", "count": 2}, False),
        ({"table": "t_out", "where": [], "op": "at_least", "count": 2}, False),
        (total(), True),
        (total(where=[FILLED_V, {"column": "c_key", "op": "equals", "values": ["A"]}]), True),
        (total(column="c_key"), False),
        (total(column="c_gone"), False),
        (total(value="lots"), False),
        (total(where=[{"column": "c_key", "op": "ticked"}]), False),
    ],
)
def test_a_condition_fits_while_its_table_has_the_rows_it_tests(
    condition: dict[str, Any], fits: bool
) -> None:
    single = TypeAdapter(SingleCondition).validate_python(condition)

    assert condition_fits(every_type_form(), single) is fits
