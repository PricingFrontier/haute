"""The workbench's tables as the pipeline holds them (specs/workbench): the Workbench
Input's and Workbench Output's tables, each its name, one row per quote or many, and its
typed columns; the sample quote as a request holds it; and the tables the pipeline refuses."""

from __future__ import annotations

import re
from typing import Any

import polars as pl
import pytest

from haute._json_shred._shred import _POLARS_TYPE_MAP
from haute._workbench_form import FormSpec
from haute._workbench_input import workbench_request_frames, workbench_table_frames
from haute._workbench_tables import (
    COLUMN_DTYPES,
    WorkbenchTable,
    WorkbenchTablesError,
    input_tables,
    output_tables,
    parse_workbench_tables,
    port_tables,
    quote_schema,
    sample_quote,
    workbench_tables,
)


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


def dumped(tables: tuple[WorkbenchTable, ...]) -> list[dict[str, Any]]:
    return [table.model_dump() for table in tables]


def test_input_tables_become_the_pipelines_tables_in_schema_order() -> None:
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

    # Each table is its name, its rows per quote and its typed columns; the form's keys,
    # rules and allowed values stay in the form.
    assert dumped((equipment, policy)) == [
        {
            "name": "equipment",
            "rows": "many",
            "columns": [{"name": "item_id", "type": "str"}, {"name": "value", "type": "float"}],
        },
        {
            "name": "policy_details",
            "rows": "one",
            "columns": [
                {"name": "state", "type": "str"},
                {"name": "inception_date", "type": "date"},
            ],
        },
    ]
    assert (equipment.one_row, policy.one_row) == (False, True)
    assert policy.frame_schema == pl.Schema({"state": pl.String, "inception_date": pl.Date})
    # In a quote, a one-row table is an object under its name and a many-row table a list.
    assert quote_schema([equipment, policy]) == pl.Schema(
        {
            "equipment": pl.List(pl.Struct({"item_id": pl.String, "value": pl.Float64})),
            "policy_details": pl.Struct({"state": pl.String, "inception_date": pl.Date}),
        }
    )


def test_each_column_type_is_the_quote_inputs_dtype() -> None:
    assert set(COLUMN_DTYPES) == {"int", "float", "str", "bool", "date"}
    assert COLUMN_DTYPES == _POLARS_TYPE_MAP


def test_a_table_without_columns_is_not_a_port() -> None:
    tables = parse_workbench_tables(
        [
            {"name": "bare", "rows": "one"},
            {"name": "items", "rows": "many", "columns": [{"name": "value", "type": "int"}]},
        ],
        owner="Workbench Input",
    )

    assert [table.name for table in port_tables(tables)] == ["items"]
    assert tables[0].columns == [] and tables[0].frame_schema == pl.Schema({})


_REFUSED = {
    "not a list": (
        None,
        "The Workbench Input's tables are not as the workbench writes them (tables: Input "
        "should be a valid list). Open the pipeline in the editor and save it.",
    ),
    "a key the workbench does not write": (
        [{"name": "a", "rows": "one", "path": "$[:]"}],
        "(tables[0].path: Extra inputs are not permitted)",
    ),
    "a key missing": ([{"name": "a"}], "(tables[0].rows: Field required)"),
    "a type that is not a column's": (
        [{"name": "a", "rows": "one", "columns": [{"name": "x", "type": "text"}]}],
        "(tables[0].columns[0].type: Input should be 'int', 'float', 'str', 'bool' or 'date')",
    ),
    "a name that cannot be a port": (
        [{"name": "class", "rows": "one"}],
        "The Workbench Input's table 'class' cannot be a port: a table's name is letters, "
        "digits and underscores, not starting with a digit and not a Python keyword.",
    ),
    "a name used twice": (
        [{"name": "a", "rows": "one"}, {"name": "a", "rows": "many"}],
        "The Workbench Input's tables name 'a' twice.",
    ),
    "names differing in case": (
        [{"name": "a", "rows": "one"}, {"name": "A", "rows": "one"}],
        "The Workbench Input's tables 'a' and 'A' differ only in case, so they cannot both "
        "be ports.",
    ),
    "a column that cannot be a frame's": (
        [{"name": "a", "rows": "one", "columns": [{"name": "my col", "type": "int"}]}],
        "The Workbench Input's table 'a' has a column 'my col', which cannot name a frame's "
        "column: a column's name is letters, digits and underscores, not starting with a "
        "digit and not a Python keyword.",
    ),
    "a column named twice": (
        [
            {
                "name": "a",
                "rows": "one",
                "columns": [{"name": "x", "type": "int"}, {"name": "x", "type": "str"}],
            }
        ],
        "The Workbench Input's table 'a' names a column 'x' twice.",
    ),
}


@pytest.mark.parametrize(("value", "says"), list(_REFUSED.values()), ids=list(_REFUSED))
def test_tables_the_pipeline_cannot_take_are_refused_naming_the_problem(
    value: Any, says: str
) -> None:
    with pytest.raises(WorkbenchTablesError, match=re.escape(says)):
        parse_workbench_tables(value, owner="Workbench Input")


def test_a_forms_table_the_pipeline_cannot_take_is_refused_by_the_nodes_name() -> None:
    spec = form(
        {"id": "t1", "name": "class", "role": "input", "columns": [column("x")]},
        {"id": "t2", "name": "priced", "role": "output", "columns": [column("x"), column("x")]},
    )

    with pytest.raises(WorkbenchTablesError, match="The Workbench Input's table 'class'"):
        input_tables(spec)
    with pytest.raises(WorkbenchTablesError, match="The Workbench Output's table 'priced'"):
        output_tables(spec)


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
            {"c_item_id": "C", "c_value": "inf"},
        ],
        "t3": [{"c_premium": "9"}],
        "t4": [{}],
    }

    assert sample_quote(spec) == {
        # Typed as each column holds it; an unticked box in a filled row is false.
        "policy_details": {"state": "NY", "limit": 1000, "cover": False, "start": "2026-01-15"},
        # Empty rows are left out; a value that isn't a finite number stays as typed, to be
        # reported.
        "equipment": [
            {"item_id": "A", "value": 2500.5, "leased": True},
            {"item_id": "B", "value": "lots", "leased": False},
            {"item_id": "C", "value": "inf", "leased": False},
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


def test_a_number_is_typed_by_the_rule_the_view_applies() -> None:
    spec = form(
        {
            "id": "t1",
            "name": "policy",
            "role": "input",
            "rows": "one",
            "columns": [column("limit", "int"), column("value", "float")],
        }
    )

    def typed(limit: str, value: str) -> dict[str, Any]:
        spec.sample = {"t1": [{"c_limit": limit, "c_value": value}]}
        return sample_quote(spec)["policy"]

    assert typed("£1,000", " 2.5e2 ") == {"limit": 1000, "value": 250.0}
    assert typed("+7", "-.5") == {"limit": 7, "value": -0.5}
    # What the view marks as not a number, the server keeps as typed, even where Python's
    # float would read it: the two apply one rule.
    assert typed("1_000", "١٢") == {"limit": "1_000", "value": "١٢"}
    assert typed("inf", "nan") == {"limit": "inf", "value": "nan"}


def test_a_sample_with_an_empty_many_row_table_prices_as_its_quote_would_when_deployed() -> None:
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
            "name": "equipment",
            "role": "input",
            "rows": "many",
            "columns": [column("item_id", key=True), column("value", "float")],
        },
    )
    spec.sample = {"t1": [{"c_state": "TX"}], "t2": [{"c_item_id": "", "c_value": " "}]}
    tables = dumped(input_tables(spec))

    # A table with nothing typed in it is left out of the sample, as a quote may leave it
    # out of a request; the Workbench Input then gives it no rows on either path.
    quote = sample_quote(spec)
    assert quote == {"policy_details": {"state": "TX"}}
    sampled = workbench_table_frames({"tables": tables, "sample": quote})
    requested = workbench_request_frames({"tables": tables}, [quote])
    assert sampled["equipment"].collect().to_dicts() == [] == requested["equipment"].to_dicts()
    assert sampled["policy_details"].collect().to_dicts() == [{"state": "TX"}]
    assert requested["policy_details"].to_dicts() == [{"state": "TX"}]


def test_a_tables_index_is_an_integer_column_numbering_the_rows_as_they_stand() -> None:
    spec = form(
        {
            "id": "t1",
            "name": "equipment",
            "role": "input",
            "rows": "many",
            "columns": [
                {**column("line", "str", key=True), "index": True},
                column("item_id"),
                column("value", "float"),
            ],
        }
    )
    spec.sample = {"t1": [{"c_item_id": "A"}, {}, {"c_item_id": "B", "c_value": "5"}]}

    (table,) = input_tables(spec)

    # The index is an Integer, whatever type the form holds for it.
    assert table.columns[0].model_dump() == {"name": "line", "type": "int"}
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

    # The same shape as the input tables: in the response a one-row table is an object under
    # its name and a many-row table a list of objects under its name.
    assert dumped(output_tables(spec)) == [
        {
            "name": "layer_premiums",
            "rows": "many",
            "columns": [{"name": "layer", "type": "int"}, {"name": "premium", "type": "float"}],
        },
        {
            "name": "pricing_output",
            "rows": "one",
            "columns": [{"name": "charged_premium", "type": "float"}],
        },
    ]
    assert [table.name for table in input_tables(spec)] == ["policy_details"]


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
