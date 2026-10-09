"""The form's tables as Haute takes them (specs/workbench): the Workbench Input's and
Workbench Output's tables in the Quote Input's v2 shape, and the sample quote as a request
holds it."""

from __future__ import annotations

from typing import Any

from haute._workbench_form import FormSpec
from haute._workbench_tables import input_tables, output_tables, sample_quote, workbench_tables


def form(*tables: dict[str, Any]) -> FormSpec:
    return FormSpec.model_validate(
        {
            "name": "x",
            "schema": {"tables": list(tables)},
            "pages": [{"id": "p", "title": "Sheet 1"}],
        }
    )


def column(name: str, type_: str = "str", **rules: object) -> dict[str, Any]:
    return {"id": f"c_{name}", "name": name, "type": type_, **rules}


def test_input_tables_become_the_quote_inputs_tables_in_schema_order() -> None:
    spec = form(
        {
            "id": "t1",
            "name": "equipment",
            "role": "input",
            "rows": "many",
            "columns": [column("item_id", key=True), column("value", "float")],
        },
        {
            "id": "t2",
            "name": "premiums",
            "role": "output",
            "rows": "many",
            "columns": [column("premium", "float")],
        },
        {
            "id": "t3",
            "name": "policy_details",
            "role": "input",
            "rows": "one",
            "columns": [
                column("state", options=["CA", "NY"], required=True),
                column("inception_date", "date"),
            ],
        },
    )

    equipment, policy = input_tables(spec)

    # A many-row table is an array of objects under its name, its single key the row id.
    assert equipment == {
        "path": "$[:].equipment[:]",
        "label": "equipment",
        "emit": True,
        "row_id_column": "item_id",
        "columns": [
            {
                "name": "item_id",
                "path": "$[:].equipment[:].item_id",
                "type": "str",
                "status": "Confirmed",
                "selected": True,
                "levels": None,
            },
            {
                "name": "value",
                "path": "$[:].equipment[:].value",
                "type": "float",
                "status": "Confirmed",
                "selected": True,
                "levels": None,
            },
        ],
    }
    # A one-row table is an object under its name, read at the quote's own level.
    assert policy["path"] == "$[:]"
    assert policy["row_id_column"] is None
    assert [(c["name"], c["path"], c["type"], c["levels"]) for c in policy["columns"]] == [
        ("state", "$[:].policy_details.state", "str", ["CA", "NY"]),
        ("inception_date", "$[:].policy_details.inception_date", "date", None),
    ]


def test_a_many_row_table_without_exactly_one_key_has_no_row_id() -> None:
    keyless = {"id": "t", "name": "drivers", "rows": "many", "columns": [column("age", "int")]}
    two_keys = {
        "id": "u",
        "name": "vehicles",
        "rows": "many",
        "columns": [column("make", key=True), column("model", key=True)],
    }

    assert [t["row_id_column"] for t in input_tables(form(keyless, two_keys))] == [None, None]


def test_the_sample_typed_while_building_is_one_quote_by_table_and_column_names() -> None:
    spec = form(
        {
            "id": "t1",
            "name": "policy_details",
            "role": "input",
            "rows": "one",
            "columns": [
                column("state"),
                column("limit", "int"),
                column("cover", "bool"),
                column("start", "date"),
            ],
        },
        {
            "id": "t2",
            "name": "equipment",
            "role": "input",
            "rows": "many",
            "columns": [
                column("item_id", key=True),
                column("value", "float"),
                column("leased", "bool"),
            ],
        },
        {
            "id": "t3",
            "name": "premiums",
            "role": "output",
            "rows": "many",
            "columns": [column("premium", "float")],
        },
        {"id": "t4", "name": "untouched", "role": "input", "rows": "one", "columns": [column("x")]},
    )
    spec.sample = {
        "t1": [{"c_state": " NY ", "c_limit": "1,000", "c_start": "2026-01-15"}],
        "t2": [
            {"c_item_id": "A", "c_value": "$2,500.50", "c_leased": True},
            {"c_item_id": "", "c_value": " "},
            {"c_item_id": "B", "c_value": "lots"},
        ],
        "t3": [{"c_premium": "9"}],
        "t4": [{}],
    }

    assert sample_quote(spec) == {
        # Typed as each column holds it; an unticked box in a filled row is false.
        "policy_details": {"state": "NY", "limit": 1000, "cover": False, "start": "2026-01-15"},
        # Empty rows are left out; a value that isn't a number stays as typed, to be reported.
        "equipment": [
            {"item_id": "A", "value": 2500.5, "leased": True},
            {"item_id": "B", "value": "lots", "leased": False},
        ],
    }


def test_a_form_with_nothing_typed_has_an_empty_sample() -> None:
    spec = form(
        {
            "id": "t1",
            "name": "policy_details",
            "role": "input",
            "rows": "one",
            "columns": [column("state")],
        }
    )
    assert sample_quote(spec) == {}


def test_a_value_that_no_longer_fits_its_column_is_sent_as_it_is() -> None:
    # A text column holding "Y" became True/false; a ticked box's column became Integer.
    spec = form(
        {
            "id": "t1",
            "name": "policy_details",
            "role": "input",
            "rows": "one",
            "columns": [column("smoker", "bool"), column("count", "int"), column("note")],
        }
    )
    spec.sample = {"t1": [{"c_smoker": "Y", "c_count": True, "c_note": False}]}

    # Both reach the Workbench Input as values that are not of their column's type; an
    # unticked box in a column that is no longer True/false is nothing typed.
    assert sample_quote(spec) == {"policy_details": {"smoker": "Y", "count": True}}


def test_a_tables_index_is_an_integer_column_numbering_the_rows_as_they_stand() -> None:
    spec = form(
        {
            "id": "t1",
            "name": "equipment",
            "role": "input",
            "rows": "many",
            "columns": [
                {**column("line", "int", key=True), "index": True},
                column("item_id"),
                column("value", "float"),
            ],
        }
    )
    spec.sample = {"t1": [{"c_item_id": "A"}, {}, {"c_item_id": "B", "c_value": "5"}]}

    (table,) = input_tables(spec)

    # The index, made the key, is the row id; it is an Integer with no levels.
    assert table["row_id_column"] == "line"
    assert table["columns"][0] == {
        "name": "line",
        "path": "$[:].equipment[:].line",
        "type": "int",
        "status": "Confirmed",
        "selected": True,
        "levels": None,
    }
    # Each row's number as the rows stand: the empty second row is not sent.
    assert sample_quote(spec) == {
        "equipment": [{"line": 1, "item_id": "A"}, {"line": 3, "item_id": "B", "value": 5.0}]
    }


def test_output_tables_become_the_workbench_outputs_tables_in_schema_order() -> None:
    spec = form(
        {
            "id": "t1",
            "name": "policy_details",
            "role": "input",
            "rows": "one",
            "columns": [column("state")],
        },
        {
            "id": "t2",
            "name": "layer_premiums",
            "role": "output",
            "rows": "many",
            "columns": [column("layer", "int", key=True), column("premium", "float")],
        },
        {
            "id": "t3",
            "name": "pricing_output",
            "role": "output",
            "rows": "one",
            "columns": [column("charged_premium", "float")],
        },
    )

    layers, pricing = output_tables(spec)

    # The same shape as the input tables: in the response a one-row table is an object under
    # its name and a many-row table an array of objects under its name.
    assert (layers["path"], layers["label"], layers["row_id_column"]) == (
        "$[:].layer_premiums[:]",
        "layer_premiums",
        "layer",
    )
    assert [(c["name"], c["path"], c["type"]) for c in layers["columns"]] == [
        ("layer", "$[:].layer_premiums[:].layer", "int"),
        ("premium", "$[:].layer_premiums[:].premium", "float"),
    ]
    assert (pricing["path"], [c["path"] for c in pricing["columns"]]) == (
        "$[:]",
        ["$[:].pricing_output.charged_premium"],
    )
    assert [table["label"] for table in input_tables(spec)] == ["policy_details"]


def test_workbench_tables_gathers_the_three_as_the_route_serves_them() -> None:
    spec = form(
        {"id": "t1", "name": "policy_details", "role": "input", "columns": [column("state")]},
        {
            "id": "t2",
            "name": "pricing_output",
            "role": "output",
            "columns": [column("premium", "float")],
        },
    )
    spec.sample = {"t1": [{"c_state": "NY"}]}

    gathered = workbench_tables(spec)

    assert gathered.tables == input_tables(spec)
    assert gathered.sample == {"policy_details": {"state": "NY"}}
    assert gathered.response_tables == output_tables(spec)
