"""The quote store's tables, derived from the form (specs/workbench): each schema table a SQL
table of its name and its columns' types, keyed by the quote's identity and, in a many-row
table, by ``quote_row``, with the store's own columns on ``quote_details`` and Haute's
activity beside them."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import BigInteger, Boolean, Date, Double, String, Text

from haute._quote_store._schema import (
    ACTIVITY_TABLE,
    STORE_COLUMN_NAMES,
    STORE_COLUMNS,
    _named,
    store_columns,
    store_schema,
    structure,
)
from haute._workbench_form import FormSpec, with_quote_details


def form(*tables: dict[str, Any]) -> FormSpec:
    return with_quote_details(
        FormSpec.model_validate(
            {
                "name": "motor",
                "schema": {"tables": list(tables)},
                "pages": [
                    {
                        "id": "p",
                        "title": "Sheet 1",
                        "width": 1280,
                        "sections": [{"id": "s", "title": "s", "height": 120, "widgets": []}],
                    }
                ],
            }
        )
    )


def column(name: str, type_: str = "str", **rest: Any) -> dict[str, Any]:
    return {"name": name, "type": type_, **rest}


POLICY = {
    "name": "policy",
    "rows": "one",
    "columns": [column("region"), column("drivers", "int"), column("start", "date")],
}
VEHICLES = {
    "name": "vehicles",
    "rows": "many",
    "columns": [column("reg", key=True), column("value", "float"), column("garaged", "bool")],
}
PREMIUM = {
    "name": "premium",
    "role": "output",
    "rows": "one",
    "columns": [column("total_premium", "float")],
}


def test_each_schema_table_is_a_sql_table_of_its_name_and_its_columns_types() -> None:
    schema = store_schema(form(POLICY, VEHICLES, PREMIUM))

    assert [stored.table.name for stored in schema.tables] == [
        "quote_details",
        "policy",
        "vehicles",
        "premium",
    ]
    policy = schema.by_name("policy").table
    assert [column.name for column in policy.columns] == [
        "quote_reference",
        "quote_option",
        "region",
        "drivers",
        "start",
    ]
    kinds = {column.name: type(column.type) for column in policy.columns}
    assert kinds == {
        "quote_reference": String,
        "quote_option": BigInteger,
        "region": Text,
        "drivers": BigInteger,
        "start": Date,
    }
    vehicles = schema.by_name("vehicles").table
    assert {column.name: type(column.type) for column in vehicles.columns}["value"] is Double
    assert {column.name: type(column.type) for column in vehicles.columns}["garaged"] is Boolean
    # Every column of the project's own may be null, so a draft saves with it empty; the
    # identity and the row number never are.
    assert [column.name for column in vehicles.columns if not column.nullable] == [
        "quote_reference",
        "quote_option",
        "quote_row",
    ]


def test_tables_are_keyed_by_the_quotes_identity_and_refer_to_its_details() -> None:
    schema = store_schema(form(POLICY, VEHICLES, PREMIUM))

    def key(name: str) -> list[str]:
        return [column.name for column in schema.by_name(name).table.primary_key.columns]

    assert key("quote_details") == ["quote_reference", "quote_option"]
    assert key("policy") == ["quote_reference", "quote_option"]
    assert key("vehicles") == ["quote_reference", "quote_option", "quote_row"]
    assert key("premium") == ["quote_reference", "quote_option"]
    for name in ("policy", "vehicles", "premium"):
        [foreign] = schema.by_name(name).table.foreign_key_constraints
        assert [element.target_fullname for element in foreign.elements] == [
            "quote_details.quote_reference",
            "quote_details.quote_option",
        ]
    # Beside the form's tables, Haute's activity alone: nothing records the tables' shape.
    assert schema.activity.name == ACTIVITY_TABLE
    assert set(schema.metadata.tables) == {
        "quote_details",
        "policy",
        "vehicles",
        "premium",
        ACTIVITY_TABLE,
    }


def test_quote_details_holds_the_forms_columns_then_the_stores_own() -> None:
    details = store_schema(form()).details.table
    names = [column.name for column in details.columns]

    assert names[:2] == ["quote_reference", "quote_option"]
    assert set(names[-len(STORE_COLUMN_NAMES) :]) == STORE_COLUMN_NAMES
    # The details the app sets on every write are never null.
    required = {column.name for column in details.columns if not column.nullable}
    assert {"stage", "assignee", "archived", "created_date", "modified_by"} <= required
    assert "quote_name" not in required and "insured" not in required


def test_a_tables_constraints_are_named_from_its_name_when_it_is_made() -> None:
    vehicles = store_schema(form(VEHICLES)).by_name("vehicles").table
    # Renamed, it is another table, made under its new name (READ-23).
    cars = store_schema(form({**VEHICLES, "name": "cars"})).by_name("cars").table

    assert vehicles.primary_key.name == _named("pk", "vehicles")
    assert [c.name for c in vehicles.foreign_key_constraints] == [_named("fk", "vehicles")]
    assert cars.primary_key.name == _named("pk", "cars")
    assert [c.name for c in cars.foreign_key_constraints] == [_named("fk", "cars")]


def test_each_tables_columns_the_form_does_not_hold_are_named_with_what_they_hold() -> None:
    kept = store_columns(form(POLICY, VEHICLES, PREMIUM))

    assert list(kept) == ["quote_details", "vehicles"]
    assert kept["vehicles"] == [("quote_row", "int")]
    assert [name for name, _ in kept["quote_details"]] == [name for name, _, _ in STORE_COLUMNS]
    assert dict(kept["quote_details"])["note_at"] == "datetime"


_REG, _VALUE, _GARAGED = VEHICLES["columns"]


@pytest.mark.parametrize(
    ("change", "differs"),
    [
        ({"name": "cars"}, True),
        ({"rows": "one"}, True),
        ({"columns": [_REG, _VALUE, _GARAGED, column("seats", "int")]}, True),
        ({"columns": [_REG, {**_VALUE, "type": "int"}, _GARAGED]}, True),
        # A column's label and rules decide nothing in the store.
        ({"columns": [{**_REG, "label": "Registration"}, _VALUE, _GARAGED]}, False),
        ({"columns": [_REG, {**_VALUE, "required": True}, _GARAGED]}, False),
    ],
)
def test_the_structure_records_what_decides_the_tables_and_nothing_else(
    change: dict[str, Any], differs: bool
) -> None:
    assert (structure(form(VEHICLES)) != structure(form({**VEHICLES, **change}))) is differs
