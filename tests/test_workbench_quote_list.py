"""The quote list setting (specs/workbench): what the underwriter app's Quotes page shows of
each quote, checked against the sheets' schema, and a revision projected by it through the
form it was keyed into."""

from __future__ import annotations

from typing import Any

from haute._workbench_form import FormSpec, QuoteList, render_form
from haute._workbench_quote_list import project, projection_key, quote_list_problems
from tests.test_workbench_routes import POLICY, PRICING, form

TITLE = {"table": "t1", "column": "c_state"}
EXPOSURE = {"table": "t1", "column": "c_exposure"}
PREMIUM = {"table": "t2", "column": "c_premium"}
GRID = {
    "id": "t3",
    "name": "drivers",
    "role": "input",
    "rows": "many",
    "columns": [{"id": "c_age", "name": "age", "type": "int"}],
}


def _form(quote_list: dict[str, Any], *tables: dict[str, Any]) -> FormSpec:
    return FormSpec.model_validate(
        {**form(*(tables or (POLICY, PRICING, GRID))), "quote_list": quote_list}
    )


def test_a_form_without_a_quote_list_shows_nothing_and_writes_one_that_is_empty() -> None:
    spec = FormSpec.model_validate(form(POLICY, PRICING))

    assert spec.quote_list == QuoteList()
    assert '"quote_list": {\n    "title": null,\n    "fields": [],\n    "headline": null\n  }' in (
        render_form(spec)
    )
    assert quote_list_problems(spec) == []


def test_a_quote_list_of_one_row_columns_has_no_problems() -> None:
    spec = _form({"title": TITLE, "fields": [EXPOSURE], "headline": PREMIUM})

    assert quote_list_problems(spec) == []


def test_each_column_the_quote_list_cannot_show_is_named() -> None:
    spec = _form(
        {
            "title": {"table": "t1", "column": "gone"},
            "fields": [{"table": "t3", "column": "c_age"}, PREMIUM, PREMIUM],
            "headline": TITLE,
        }
    )

    problems = quote_list_problems(spec)

    assert problems == [
        "The quote list's title names column 'gone' of table 't1', which the schema does not have.",
        "The quote list's summary field 1, drivers.age, is in a many-row table: a quote's list "
        "row shows one value.",
        "The quote list's summary field 2, pricing_output.premium, is not in an input table.",
        "The quote list's summary field 3, pricing_output.premium, is not in an input table.",
        "The quote list names a summary field twice.",
        "The quote list's headline, policy_details.state, is not in an output table.",
        "The quote list's headline, policy_details.state, is not a number column.",
    ]


def test_a_revision_is_projected_through_the_form_it_was_keyed_into() -> None:
    setting = QuoteList.model_validate({"title": TITLE, "fields": [EXPOSURE], "headline": PREMIUM})
    renamed = [
        {**POLICY, "name": "policy"},
        {**PRICING, "name": "pricing", "columns": [{"id": "c_premium", "name": "charged"}]},
    ]
    saved = _form({}, *renamed)
    values = {"t1": [{"c_state": " NY ", "c_exposure": "1000"}]}

    projection = project(setting, saved, values, {"pricing": [{"charged": 512.4}]})

    assert (projection.title, projection.fields, projection.headline) == ("NY", ["1000"], 512.4)
    assert projection.key == projection_key(setting)


def test_what_a_revision_does_not_hold_projects_as_blank() -> None:
    setting = QuoteList.model_validate(
        {"title": TITLE, "fields": [EXPOSURE, {"table": "t9", "column": "c"}], "headline": PREMIUM}
    )
    saved = FormSpec.model_validate(form(POLICY, PRICING))

    projection = project(setting, saved, {"t1": [{"c_state": "", "c_exposure": "5"}]}, None)

    assert (projection.title, projection.fields, projection.headline) == (None, ["5", None], None)


def test_another_setting_has_another_key() -> None:
    first = QuoteList.model_validate({"title": TITLE})

    assert projection_key(first) == projection_key(QuoteList.model_validate({"title": TITLE}))
    assert projection_key(first) != projection_key(QuoteList())
