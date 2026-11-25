"""The checks a quote must pass to be marked final (specs/workbench), held to the cases the
view's checks are held to, on each case's quote as the store writes it."""

from __future__ import annotations

import copy
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from haute._quote_store._rows import input_rows, output_rows, with_outputs
from haute._workbench_checks import Check, check_text, quote_checks
from haute._workbench_form import FormSpec, with_quote_details
from haute._workbench_visibility import visibility

FIXTURE = (
    Path(__file__).resolve().parents[1] / "frontend/src/utils/__tests__/fixtures/quoteChecks.json"
)
CASES: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
FORM = FormSpec.model_validate(CASES["form"])
IDENTITY = ("Q-1", 1)


def test_the_fixtures_form_is_canonical_so_the_cases_cannot_drift_from_the_files_shape() -> None:
    assert FORM.model_dump(mode="json") == CASES["form"]


def stored(values: dict[str, Any], tables: dict[str, list[Any]] | None) -> dict[str, Any]:
    """The rows the store writes for *values* typed and the *tables* their price gave, as a
    save makes them."""
    form = with_quote_details(FORM)
    document = None
    if tables is not None:
        one_row = {table.name for table in form.data_schema.tables if table.rows == "one"}
        document = {
            name: rows[0] if name in one_row else rows for name, rows in tables.items() if rows
        }
    return with_outputs(
        form,
        {**input_rows(form, values, IDENTITY), **output_rows(form, document, IDENTITY)},
        document,
    )


@pytest.mark.parametrize("case", CASES["cases"], ids=[case["name"] for case in CASES["cases"]])
def test_a_stored_quotes_checks_are_what_the_shared_cases_say(case: dict[str, Any]) -> None:
    rows = stored(case["values"], case["tables"])
    hidden = visibility(FORM, case["values"]).hidden

    checks = quote_checks(FORM, rows, hidden)

    assert [{**asdict(check), "rows": list(check.rows)} for check in checks] == case["expected"]


def test_a_hidden_inputs_rules_are_checked_once_it_shows() -> None:
    values = {"policy": [{"region": "north", "age": "40"}]}
    rows = stored(values, None)

    shown = quote_checks(FORM, rows, visibility(FORM, values).hidden)
    hidden = quote_checks(FORM, rows, {("policy", "trade")})

    assert Check("required", "policy", "trade", passed=False) in shown
    assert all(check.column != "trade" for check in hidden)


@pytest.mark.parametrize(
    ("check", "text"),
    [
        (Check("required", "policy", "age", False), "policy.Age needs a value"),
        (Check("range", "policy", "age", False), "policy.Age must be from 17 to 99"),
        (Check("range", "policy", "excess", False), "policy.Excess must be at least 0"),
        (
            Check("allowed", "policy", "region", False),
            "policy.Region must be one of its allowed values",
        ),
        (
            Check("validation", "policy", "premium_ok", False),
            "policy.Premium check: The premium is below the minimum",
        ),
        (
            Check("validation", "drivers", "licence_ok", False, ("2", "3")),
            "drivers rows 2, 3.Licence check does not pass",
        ),
        (Check("required", "drivers", "name", False, ("2",)), "drivers row 2.Name needs a value"),
    ],
)
def test_a_failing_check_says_what_it_asks_for_by_table_row_and_label(
    check: Check, text: str
) -> None:
    assert check_text(FORM, check) == text


def test_a_range_with_a_top_alone_says_at_most() -> None:
    capped = copy.deepcopy(CASES["form"])
    policy = next(table for table in capped["schema"]["tables"] if table["name"] == "policy")
    excess = next(column for column in policy["inputs"] if column["name"] == "excess")
    excess.update(min=None, max=1.5)
    form = FormSpec.model_validate(capped)

    assert check_text(form, Check("range", "policy", "excess", False)) == (
        "policy.Excess must be at most 1.5"
    )
