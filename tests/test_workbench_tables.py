"""The workbench's tables as the pipeline holds them (specs/workbench): the Workbench
Input's and Workbench Output's tables, each its name, one row per quote or many, and its
typed columns; the sample quote as a request holds it, a table's rows as a column gives
them; and the tables the pipeline refuses."""

from __future__ import annotations

import copy
import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from haute._json_shred._shred import _POLARS_TYPE_MAP
from haute._workbench_form import FormSpec, SchemaColumn, SchemaTable, with_quote_details
from haute._workbench_input import (
    WorkbenchInputError,
    null_quote,
    read_quote,
    with_stand_in,
    workbench_request_frames,
    workbench_table_frames,
)
from haute._workbench_tables import (
    COLUMN_DTYPES,
    WorkbenchTable,
    WorkbenchTablesError,
    _filled,
    _typed,
    check_tables,
    holds_output,
    index_order,
    input_tables,
    key_collisions,
    key_text,
    null_index,
    numbered_value,
    output_misfit,
    output_tables,
    parse_workbench_tables,
    port_tables,
    preset_rows,
    quote_request,
    quote_schema,
    request_key_problem,
    request_schema,
    sample_quote,
    typed_index_problems,
    workbench_tables,
)

#: The quote's reference and option, which every table but quote_details leads with.
IDENTITY = [{"name": "quote_reference", "type": "str"}, {"name": "quote_option", "type": "int"}]
IDENTITY_DTYPES = {"quote_reference": pl.String, "quote_option": pl.Int64}
#: The identity a sample or the null quote is priced under.
_STAND_IN = {"quote_reference": "PREVIEW", "quote_option": 1}


def form(*tables: dict[str, Any], pages: list[dict[str, Any]] | None = None) -> FormSpec:
    return FormSpec.model_validate(
        {
            "name": "x",
            "schema": {"tables": list(tables)},
            "pages": pages
            or [{"id": "p", "title": "Sheet 1", "width": 1280, "sections": [section("s")]}],
        }
    )


def section(id_: str, *widgets: dict[str, Any]) -> dict[str, Any]:
    """A section holding *widgets*, each 120px tall at its top."""
    return {"id": id_, "title": id_, "height": 120, "widgets": list(widgets)}


def column(name: str, type_: str = "str", **rules: object) -> dict[str, Any]:
    return {"name": name, "type": type_, **rules}


def widget(id_: str, type_: str, *fields: str) -> dict[str, Any]:
    """A component showing *fields*, each ``table.column`` by name, named as its id."""
    refs = [dict(zip(("table", "column"), field.split("."), strict=True)) for field in fields]
    return {
        "id": id_,
        "name": id_,
        "type": type_,
        "x": 0,
        "y": 0,
        "w": 720,
        "h": 120,
        "fields": refs,
    }


def dumped(tables: tuple[WorkbenchTable, ...]) -> list[dict[str, Any]]:
    return [table.model_dump() for table in tables]


def index(name: str = "row_number") -> dict[str, Any]:
    """A many-row input table's index: an Integer numbering its rows, as the editor gives it."""
    return column(name, "int", index="numbered")


def side(
    name: str, rows: str = "one", *columns: dict[str, Any], of: str = "input", **rest: Any
) -> dict[str, Any]:
    """A node's table as the workbench gives it: a side of the schema table *name*, its port
    ``<name>_<of>``, its rows and its typed *columns*."""
    return {
        "name": name,
        "side": of,
        "port": f"{name}_{of}",
        "rows": rows,
        "columns": list(columns),
        **rest,
    }


def test_each_input_table_gives_an_input_side_and_each_output_table_an_output_side() -> None:
    spec = with_quote_details(
        form(
            # Keyed by its index, renamed: a table of both blocks.
            {
                "name": "equipment",
                "rows": "many",
                "inputs": [
                    {**index("item_number"), "label": "Item"},
                    column("value", "float", required=True),
                ],
                "outputs": [column("premium", "float")],
            },
            # Keyed by its index of values, a row for each peril.
            {
                "name": "perils",
                "rows": "many",
                "inputs": [
                    column(
                        "peril",
                        options=["Fire", "Flood"],
                        index="values",
                        options_source={"file": "perils.csv", "column": "peril"},
                    ),
                    column("rate", "float"),
                ],
                "outputs": [column("peril_premium", "float")],
            },
            # A table of outputs alone: its numbered index among its outputs, which the
            # Workbench Output numbers.
            {
                "name": "layers",
                "rows": "many",
                "outputs": [
                    index("layer_no"),
                    column("layer", "int"),
                    column("layer_premium", "float"),
                ],
            },
            # A table of inputs alone: no output side.
            {
                "name": "policy_details",
                "rows": "one",
                "inputs": [
                    column("state", options=["CA", "NY"], required=True),
                    column("inception_date", "date"),
                ],
            },
            # A one-row table of both blocks: its one row is its own, so it has no key.
            {
                "name": "pricing",
                "rows": "one",
                "inputs": [column("discount", "float")],
                "outputs": [column("charged", "float")],
            },
        )
    )

    details, equipment, perils, policy, pricing = input_tables(spec)

    # quote_details' only side is its input side, quote_details_input.
    assert (details.name, details.side, details.port) == (
        "quote_details",
        "input",
        "quote_details_input",
    )
    # Each input side is the identity, then the table's inputs, the index an Integer; the
    # form's labels, rules and allowed values stay in the form.
    assert dumped((equipment, perils, policy, pricing)) == [
        {
            "name": "equipment",
            "side": "input",
            "port": "equipment_input",
            "rows": "many",
            "columns": [
                *IDENTITY,
                {"name": "item_number", "type": "int"},
                {"name": "value", "type": "float"},
            ],
            "key": "item_number",
            "null_key": 1,
            "index_kind": None,
            "index_values": [],
        },
        {
            "name": "perils",
            "side": "input",
            "port": "perils_input",
            "rows": "many",
            "columns": [
                *IDENTITY,
                {"name": "peril", "type": "str"},
                {"name": "rate", "type": "float"},
            ],
            "key": "peril",
            "null_key": "Fire",
            "index_kind": None,
            "index_values": [],
        },
        {
            "name": "policy_details",
            "side": "input",
            "port": "policy_details_input",
            "rows": "one",
            "columns": [
                *IDENTITY,
                {"name": "state", "type": "str"},
                {"name": "inception_date", "type": "date"},
            ],
            "key": None,
            "null_key": None,
            "index_kind": None,
            "index_values": [],
        },
        {
            "name": "pricing",
            "side": "input",
            "port": "pricing_input",
            "rows": "one",
            "columns": [*IDENTITY, {"name": "discount", "type": "float"}],
            "key": None,
            "null_key": None,
            "index_kind": None,
            "index_values": [],
        },
    ]
    # Each output side is the identity, then a many-row input table's index, then the
    # table's outputs; a table of outputs alone keys its rows by its own index, and one of
    # inputs alone has no side.
    assert dumped(output_tables(spec)) == [
        {
            "name": "equipment",
            "side": "output",
            "port": "equipment_output",
            "rows": "many",
            "columns": [
                *IDENTITY,
                {"name": "item_number", "type": "int"},
                {"name": "premium", "type": "float"},
            ],
            "key": "item_number",
            "null_key": None,
            "index_kind": None,
            "index_values": [],
        },
        {
            "name": "perils",
            "side": "output",
            "port": "perils_output",
            "rows": "many",
            "columns": [
                *IDENTITY,
                {"name": "peril", "type": "str"},
                {"name": "peril_premium", "type": "float"},
            ],
            "key": "peril",
            "null_key": None,
            "index_kind": None,
            "index_values": [],
        },
        {
            "name": "layers",
            "side": "output",
            "port": "layers_output",
            "rows": "many",
            "columns": [
                *IDENTITY,
                {"name": "layer_no", "type": "int"},
                {"name": "layer", "type": "int"},
                {"name": "layer_premium", "type": "float"},
            ],
            "key": "layer_no",
            "null_key": None,
            "index_kind": "numbered",
            "index_values": [],
        },
        {
            "name": "pricing",
            "side": "output",
            "port": "pricing_output",
            "rows": "one",
            "columns": [*IDENTITY, {"name": "charged", "type": "float"}],
            "key": None,
            "null_key": None,
            "index_kind": None,
            "index_values": [],
        },
    ]
    assert (equipment.one_row, policy.one_row) == (False, True)
    assert policy.frame_schema == pl.Schema(
        {**IDENTITY_DTYPES, "state": pl.String, "inception_date": pl.Date}
    )
    # In a priced quote, a one-row table is an object under its table's name and a many-row
    # table a list, each leading with the quote's identity.
    assert quote_schema([equipment, policy]) == pl.Schema(
        {
            "equipment": pl.List(
                pl.Struct({**IDENTITY_DTYPES, "item_number": pl.Int64, "value": pl.Float64})
            ),
            "policy_details": pl.Struct(
                {**IDENTITY_DTYPES, "state": pl.String, "inception_date": pl.Date}
            ),
        }
    )
    # A request gives each its own columns under the table's name: the identity is
    # quote_details' alone.
    assert request_schema([equipment, policy]) == pl.Schema(
        {
            "equipment": pl.List(pl.Struct({"item_number": pl.Int64, "value": pl.Float64})),
            "policy_details": pl.Struct({"state": pl.String, "inception_date": pl.Date}),
        }
    )


@pytest.mark.parametrize(
    ("index_column", "kind", "values"),
    [
        pytest.param(index("layer_no"), "numbered", [], id="numbered"),
        pytest.param(column("layer_no", "int", index="typed"), "typed", [], id="typed"),
        pytest.param(
            column(
                "layer_no",
                "int",
                index="values",
                options=["2", "1"],
                options_source={"file": "layers.csv", "column": "layer"},
            ),
            "values",
            [2, 1],
            id="of values",
        ),
    ],
)
def test_output_only_table_leads_with_identity_and_keys_its_rows_by_its_index(
    index_column: dict[str, Any], kind: str, values: list[Any]
) -> None:
    spec = form(
        {
            "name": "layer_premiums",
            "rows": "many",
            "outputs": [index_column, column("layer", "int"), column("premium", "float")],
        }
    )

    (table,) = spec.data_schema.tables
    # The identity heads its outputs, its first block, as it has no inputs.
    assert [item.name for item in table.inputs] == []
    assert [item.name for item in table.outputs[:2]] == ["quote_reference", "quote_option"]
    assert input_tables(spec) == ()
    # Its output side keeps its own columns, its index among them, which keys its rows and
    # says how the Workbench Output takes them.
    (layers,) = output_tables(spec)
    assert (
        layers.port,
        layers.key,
        layers.index_kind,
        layers.index_values,
        [item.name for item in layers.columns],
    ) == (
        "layer_premiums_output",
        "layer_no",
        kind,
        values,
        ["quote_reference", "quote_option", "layer_no", "layer", "premium"],
    )


@pytest.mark.parametrize(
    ("given", "says"),
    [
        pytest.param({"key": None}, "has many rows, so its key names its index", id="no key"),
        pytest.param(
            {"key": "premium"}, "has many rows, so its key names its index", id="a Decimal key"
        ),
        pytest.param(
            {"key": "line", "index_kind": "values"},
            "holds its index's values exactly when its index gives a row for each value",
            id="values without its values",
        ),
        pytest.param(
            {"key": "line", "index_kind": "typed", "index_values": [1]},
            "holds its index's values exactly when its index gives a row for each value",
            id="values on a typed index",
        ),
        pytest.param(
            {"key": "line", "null_key": 1},
            "holds a null key, which only an input side does",
            id="a null key on an output side",
        ),
    ],
)
def test_a_many_row_side_that_does_not_name_its_index_as_its_kind_takes_is_refused(
    given: dict[str, Any], says: str
) -> None:
    columns = [{"name": "line", "type": "int"}, {"name": "premium", "type": "float"}]
    with pytest.raises(WorkbenchTablesError, match=re.escape(says)):
        parse_workbench_tables(
            [side("t", "many", *columns, of="output", **given)], owner="Workbench Output"
        )


def test_an_input_side_says_nothing_of_how_its_index_gets_its_values() -> None:
    with pytest.raises(WorkbenchTablesError, match="only the output side of a table of outputs"):
        parse_workbench_tables(
            [side("t", "many", {"name": "line", "type": "int"}, key="line", index_kind="typed")],
            owner="Workbench Input",
        )


def test_a_one_row_side_has_no_index() -> None:
    with pytest.raises(WorkbenchTablesError, match="has one row, so it has no index"):
        parse_workbench_tables(
            [side("t", "one", {"name": "line", "type": "int"}, key="line")],
            owner="Workbench Input",
        )


@pytest.mark.parametrize(
    ("given", "value"),
    [
        pytest.param(index("item_number"), 1, id="a numbered Integer"),
        pytest.param(column("item_no", "str", index="numbered"), "1", id="a numbered Text"),
        pytest.param(column("site", "str", index="typed"), "1", id="a typed Text"),
        pytest.param(
            column(
                "peril",
                index="values",
                options=["Fire", "Flood"],
                options_source={"file": "perils.csv", "column": "peril"},
            ),
            "Fire",
            id="a Text of values",
        ),
        pytest.param(
            column(
                "layer",
                "int",
                index="values",
                options=["03", "1"],
                options_source={"file": "layers.csv", "column": "layer"},
            ),
            3,
            id="an Integer of values",
        ),
    ],
)
def test_the_null_quotes_index_is_the_first_value_else_the_first_rows_number(
    given: dict[str, Any], value: object
) -> None:
    assert null_index(SchemaColumn.model_validate(given)) == value


def test_a_numbered_index_holds_the_rows_number_as_its_type_holds_it() -> None:
    assert numbered_value(SchemaColumn.model_validate(index("n")), 3) == 3
    assert numbered_value(SchemaColumn.model_validate(column("n", index="numbered")), 3) == "3"


@pytest.mark.parametrize(
    ("given", "stored", "order"),
    [
        pytest.param(
            column("n", index="numbered"), ["10", "9", "x", "1"], [3, 1, 0, 2], id="numbered Text"
        ),
        pytest.param(index("n"), [3, 1, 2], [1, 2, 0], id="numbered Integer"),
        pytest.param(
            column("site", index="typed"), ["York", "Leeds", "Bath"], [2, 1, 0], id="typed Text"
        ),
        pytest.param(column("no", "int", index="typed"), [20, 3, 100], [1, 0, 2], id="typed int"),
        pytest.param(
            column(
                "peril",
                index="values",
                options=["Fire", "Flood", "Hail"],
                options_source={"file": "perils.csv", "column": "peril"},
            ),
            ["Hail", "Gone", "Fire"],
            [2, 0, 1],
            id="of values, a value no longer allowed after the rest",
        ),
    ],
)
def test_a_quotes_rows_open_in_their_indexs_order(
    given: dict[str, Any], stored: list[Any], order: list[int]
) -> None:
    assert index_order(SchemaColumn.model_validate(given), stored) == order


def test_the_null_quote_gives_a_many_row_tables_row_its_index_and_nothing_else() -> None:
    spec = with_quote_details(
        form(
            {
                "name": "equipment",
                "rows": "many",
                "inputs": [index("item_number"), column("value", "float")],
                "outputs": [column("premium", "float")],
            },
            {
                "name": "coverage",
                "rows": "many",
                "inputs": [
                    column(
                        "layer",
                        "int",
                        options=["2", "1"],
                        index="values",
                        options_source={"file": "layers.csv", "column": "layer"},
                    ),
                    column("limit", "float"),
                ],
            },
            {"name": "policy", "rows": "one", "inputs": [column("state")]},
        )
    )

    quote = null_quote(input_tables(spec))

    # A numbered index numbers the one row 1; an index of values holds its first value,
    # typed as the index; the rest is null, and a one-row table holds no index.
    assert quote["equipment"] == [{"item_number": 1, "value": None}]
    assert quote["coverage"] == [{"layer": 2, "limit": None}]
    assert quote["policy"] == {"state": None}
    assert quote["quote_details"]["quote_reference"] == "PREVIEW"
    # Read, it is a quote whose rows each hold their own index value.
    frames = workbench_table_frames(
        {"tables": dumped(input_tables(spec)), "sample": {}},
    )
    assert frames["equipment_input"].collect().to_dicts() == [
        {**_STAND_IN, "item_number": 1, "value": None}
    ]
    assert frames["coverage_input"].collect().to_dicts() == [
        {**_STAND_IN, "layer": 2, "limit": None}
    ]


def test_a_tables_index_is_sent_though_the_sheets_hide_it() -> None:
    spec = form(
        {
            "name": "policy",
            "rows": "one",
            "inputs": [column("bi_cover", "bool"), column("assured")],
        },
        {
            "name": "perils",
            "rows": "many",
            "inputs": [
                column(
                    "peril",
                    options=["Fire", "Flood"],
                    index="values",
                    options_source={"file": "perils.csv", "column": "peril"},
                ),
                column("rate", "float"),
            ],
            "outputs": [column("premium", "float")],
        },
        pages=[
            {
                "id": "p_main",
                "title": "Main",
                "width": 1280,
                "sections": [
                    section(
                        "s_main",
                        widget("w_policy", "collection", "policy.bi_cover", "policy.assured"),
                    )
                ],
            },
            {
                "id": "p_perils",
                "title": "Perils",
                "show_when": {
                    "conditions": [{"table": "policy", "column": "bi_cover", "op": "ticked"}]
                },
                "width": 1280,
                "sections": [
                    section(
                        "s_perils",
                        widget("w_perils", "tableInput", "perils.peril", "perils.rate"),
                    )
                ],
            },
        ],
    )
    spec.sample = {
        "policy": [{"bi_cover": False, "assured": "Acme"}],
        "perils": [{"peril": "Flood", "rate": "2.5"}],
    }

    # The perils' sheet hides: the rate typed on it is left out, but each row keeps its
    # index, the peril giving it, so its outputs can be put back on it.
    assert sample_quote(spec)["perils"] == [{"peril": "Fire"}, {"peril": "Flood"}]
    spec.sample["policy"][0]["bi_cover"] = True
    assert sample_quote(spec)["perils"] == [{"peril": "Fire"}, {"peril": "Flood", "rate": 2.5}]


def _sites(*rows: dict[str, Any], kind: str = "str") -> FormSpec:
    spec = form(
        {
            "name": "sites",
            "rows": "many",
            "inputs": [column("site", kind, index="typed"), column("value", "float")],
        }
    )
    spec.sample = {"sites": list(rows)}
    return spec


@pytest.mark.parametrize("site", ["", "   "])
def test_a_request_refuses_a_blank_text_index(site: str) -> None:
    tables = input_tables(with_quote_details(_sites()))
    quote = with_stand_in(tables, {"sites": [{"site": site, "value": 1}]})

    with pytest.raises(WorkbenchInputError, match="sites's row 1 holds no site"):
        read_quote(tables, quote)


def test_a_typed_index_is_typing_and_held_as_typed() -> None:
    # A row holding its typed index alone is typed, and sent; a numbered Text index's
    # rows are numbered as text.
    assert sample_quote(_sites({"site": " Leeds "})) == {"sites": [{"site": "Leeds"}]}
    assert sample_quote(_sites({"site": "7"}, kind="int")) == {"sites": [{"site": 7}]}
    numbered = form(
        {"name": "items", "rows": "many", "inputs": [column("no", index="numbered"), column("v")]}
    )
    numbered.sample = {"items": [{"v": "a"}, {}, {"v": "b"}]}
    assert sample_quote(numbered) == {"items": [{"no": "1", "v": "a"}, {"no": "3", "v": "b"}]}


@pytest.mark.parametrize(
    ("rows", "says"),
    [
        pytest.param(
            [{"site": "Leeds", "value": "1"}, {}, {"site": "York"}],
            [],
            id="each its own, a blank row",
        ),
        pytest.param(
            [{"site": "Leeds"}, {"value": "5"}],
            ["sites's row 2 holds no site"],
            id="blank on a row holding something",
        ),
        pytest.param(
            [{"site": "Leeds"}, {"site": "York"}, {"site": " Leeds"}],
            ["sites's rows 1 and 3 hold site \"Leeds\"; each row's site is its own"],
            id="a value twice, compared as typed",
        ),
        pytest.param(
            [{"site": "x" * 256}],
            ["sites's row 1 holds a site longer than 255 characters"],
            id="too long",
        ),
    ],
)
def test_a_typed_indexs_rows_each_hold_their_own_value(
    rows: list[dict[str, Any]], says: list[str]
) -> None:
    spec = _sites(*rows)

    assert [problem.message for problem in typed_index_problems(spec, spec.sample)] == says


def test_an_integer_typed_index_is_compared_as_its_numbers() -> None:
    spec = _sites({"site": "7"}, {"site": "007"}, kind="int")

    (problem,) = typed_index_problems(spec, spec.sample)

    assert (problem.table, problem.column, problem.rows) == ("sites", "site", (1, 2))


def _keyed_side(key: str = "item_number", kind: str = "int") -> WorkbenchTable:
    (table,) = parse_workbench_tables(
        [side("ce_equipment", "many", {"name": key, "type": kind}, of="output", key=key)],
        owner="Workbench Output",
    )
    return table


def test_an_answer_of_no_rows_fits_and_a_numbered_index_of_outputs_alone_never_misfits() -> None:
    assert output_misfit(_keyed_side(), [], [1, 2]) is None
    (table,) = parse_workbench_tables(
        [
            side(
                "layers",
                "many",
                {"name": "layer", "type": "int"},
                of="output",
                key="layer",
                index_kind="numbered",
            )
        ],
        owner="Workbench Output",
    )

    assert output_misfit(table, [None, 1, 1], []) is None


#: Whether a pricing's answer fits its quote's rows, which the view's ``outputMisfit`` holds
#: itself to as well: each case's output side, its answer's keys and its request's, and the
#: message, null when the answer fits.
OUTPUT_MISFITS: list[dict[str, Any]] = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / "frontend/src/utils/__tests__/fixtures/outputMisfit.json"
    ).read_text(encoding="utf-8")
)["cases"]


@pytest.mark.parametrize("case", OUTPUT_MISFITS, ids=[case["name"] for case in OUTPUT_MISFITS])
def test_an_answer_misfitting_its_quotes_rows_is_said_by_the_rule_the_view_applies(
    case: dict[str, Any],
) -> None:
    (table,) = parse_workbench_tables([case["table"]], owner="Workbench Output")
    kind = next(column.type for column in table.columns if column.name == table.key)

    def typed(value: Any) -> Any:
        # JSON carries a Date as its ISO text; the pipeline's keys are typed as their column.
        return date.fromisoformat(value) if kind == "date" and value is not None else value

    answer = [typed(value) for value in case["answer"]]
    requested = [typed(value) for value in case["request"]]

    assert output_misfit(table, answer, requested) == case["message"]


@pytest.mark.parametrize(
    ("keys", "says"),
    [
        pytest.param([1, 2, 3], None, id="each row its own"),
        pytest.param([], None, id="no rows"),
        pytest.param(
            [1, None, 3], "ce_equipment's row 2 holds no item_number", id="a row holding none"
        ),
        pytest.param(
            [None, 2, None, None],
            "ce_equipment's rows 1, 3 and 4 hold no item_number",
            id="rows holding none",
        ),
        pytest.param(
            [1, 3, 2, 4, 3],
            "ce_equipment's rows 2 and 5 hold item_number 3; each row's item_number is its own",
            id="two rows of one key",
        ),
    ],
)
def test_a_requests_keyed_rows_each_hold_their_own_key(keys: list[Any], says: str | None) -> None:
    (table,) = parse_workbench_tables(
        [side("ce_equipment", "many", {"name": "item_number", "type": "int"}, key="item_number")],
        owner="Workbench Input",
    )

    assert request_key_problem(table, keys) == says


def test_a_requests_text_index_is_held_to_the_length_the_store_keys_a_row_by() -> None:
    (table,) = parse_workbench_tables(
        [side("sites", "many", {"name": "site", "type": "str"}, key="site")],
        owner="Workbench Input",
    )

    assert request_key_problem(table, ["Leeds", "x" * 255]) is None
    assert (
        request_key_problem(table, ["Leeds", "x" * 256])
        == "sites's row 2 holds a site longer than 255 characters"
    )


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (3, "3"),
        (3.0, "3"),
        (2.5, "2.5"),
        ("Fire", '"Fire"'),
        ('say "hi"', '"say \\"hi\\""'),
        ("Équipe", '"Équipe"'),
        (date(2026, 1, 31), "2026-01-31"),
        (True, "true"),
        # Any other value as its text.
        (Decimal("1.50"), "1.50"),
    ],
)
def test_a_key_is_said_as_a_message_says_it(value: object, text: str) -> None:
    assert key_text(value) == text


@pytest.mark.parametrize(
    ("value", "held"),
    [
        (None, False),
        (float("nan"), False),
        (float("inf"), False),
        (float("-inf"), False),
        (0, True),
        (0.0, True),
        (-1.5, True),
        ("", True),
        (False, True),
    ],
)
def test_an_output_value_is_held_unless_null_or_not_finite(value: object, held: bool) -> None:
    assert holds_output(value) is held


def _keyed_table(name: str, **table: Any) -> SchemaTable:
    return form({"name": name, **table}).data_schema.tables[0]


def _preset_table(kind: str, *options: str, values: bool = True) -> SchemaTable:
    layer = (
        column(
            "layer",
            kind,
            options=list(options),
            index="values",
            options_source={"file": "layers.csv", "column": "layer"},
        )
        if values
        else column("layer", kind, index="typed")
    )
    return _keyed_table("layers", rows="many", inputs=[layer, column("limit", "float")])


def test_allowed_values_reading_as_one_value_cannot_key_a_tables_rows() -> None:
    assert key_collisions(_preset_table("int", "1", "01", "2", "+2", "3")) == [
        "layers.layer's allowed values 1 and 01 are one Integer, so they cannot key its rows: "
        "correct the params file",
        "layers.layer's allowed values 2 and +2 are one Integer, so they cannot key its rows: "
        "correct the params file",
    ]
    # Text keeps each value its own, as do values each reading as their own number, and an
    # index giving no rows keys nothing by its values.
    assert key_collisions(_preset_table("str", "1", "01", "A", "a")) == []
    assert key_collisions(_preset_table("int", "1", "2", "10")) == []
    assert key_collisions(_preset_table("int", values=False)) == []


def test_each_column_type_is_the_quote_inputs_dtype() -> None:
    assert set(COLUMN_DTYPES) == {"int", "float", "str", "bool", "date"}
    assert COLUMN_DTYPES == _POLARS_TYPE_MAP


def test_a_table_without_columns_is_not_a_port() -> None:
    tables = parse_workbench_tables(
        [
            side("bare"),
            side("items", "many", {"name": "value", "type": "int"}, key="value"),
        ],
        owner="Workbench Input",
    )

    assert [table.port for table in port_tables(tables)] == ["items_input"]
    assert tables[0].columns == [] and tables[0].frame_schema == pl.Schema({})


_REFUSED = {
    "not a list": (
        None,
        "The Workbench Input's tables are not as the workbench gives them (tables: Input "
        "should be a valid list).",
    ),
    "a key the workbench does not write": (
        [side("a", path="$[:]")],
        "(tables[0].path: Extra inputs are not permitted)",
    ),
    "a key missing": (
        [{"name": "a", "side": "input", "port": "a_input"}],
        "(tables[0].rows: Field required)",
    ),
    "a side missing": ([{"name": "a", "rows": "one"}], "(tables[0].side: Field required)"),
    "a port that is not its side's": (
        [{**side("a"), "port": "b_input"}],
        "(tables[0]: Value error, a's input side is the port a_input)",
    ),
    "a key that is none of its columns": (
        [side("a", "many", {"name": "x", "type": "int"}, key="y")],
        "(tables[0]: Value error, a_input has many rows, so its key names its index, an Integer "
        "or Text column it holds)",
    ),
    "a many-row side without a key": (
        [side("a", "many", {"name": "x", "type": "int"})],
        "(tables[0]: Value error, a_input has many rows, so its key names its index, an Integer "
        "or Text column it holds)",
    ),
    "a key of a Decimal": (
        [side("a", "many", {"name": "x", "type": "float"}, key="x")],
        "(tables[0]: Value error, a_input has many rows, so its key names its index, an Integer "
        "or Text column it holds)",
    ),
    "a key on a one-row table": (
        [side("a", "one", {"name": "x", "type": "int"}, key="x")],
        "(tables[0]: Value error, a_input has one row, so it has no index)",
    ),
    "a null key on an output side": (
        [side("a", "many", {"name": "x", "type": "int"}, of="output", key="x", null_key=1)],
        "(tables[0]: Value error, a_output holds a null key, which only an input side does)",
    ),
    # How the index gets its values is said on a table of outputs alone's output side alone.
    "an index kind on an input side": (
        [side("a", "many", {"name": "x", "type": "int"}, key="x", index_kind="numbered")],
        "(tables[0]: Value error, a_input says how its index gets its values, which only the "
        "output side of a table of outputs alone does)",
    ),
    "an index kind on a one-row output side": (
        [side("a", "one", {"name": "x", "type": "int"}, of="output", index_kind="numbered")],
        "(tables[0]: Value error, a_output has one row, so it has no index)",
    ),
    "an index of values without its values": (
        [
            side(
                "a", "many", {"name": "x", "type": "str"}, of="output", key="x", index_kind="values"
            )
        ],
        "(tables[0]: Value error, a_output holds its index's values exactly when its index "
        "gives a row for each value)",
    ),
    "the old index field": (
        [side("a", "many", {"name": "x", "type": "int"}, of="output", key="x", index="x")],
        "(tables[0].index: Extra inputs are not permitted)",
    ),
    "a type that is not a column's": (
        [side("a", "one", {"name": "x", "type": "text"})],
        "(tables[0].columns[0].type: Input should be 'int', 'float', 'str', 'bool' or 'date')",
    ),
    "a name that is no identifier": (
        [side("2nd")],
        "The Workbench Input's table '2nd' cannot be named so: a table's name is letters, "
        "digits and underscores, not starting with a digit.",
    ),
    "no name": (
        [side("")],
        "The Workbench Input's table '' cannot be named so: a table's name is letters, "
        "digits and underscores, not starting with a digit.",
    ),
    "a name used twice": (
        [side("a"), side("a", "many", {"name": "x", "type": "int"}, key="x")],
        "The Workbench Input's tables name 'a' twice.",
    ),
    # The editor binds a port by its name compared without case.
    "ports differing in case": (
        [side("a"), side("A")],
        "The Workbench Input's ports 'a_input' and 'A_input' differ only in case, so they "
        "cannot both be ports.",
    ),
    "a column that cannot be a frame's": (
        [side("a", "one", {"name": "my col", "type": "int"})],
        "The Workbench Input's table 'a' has a column 'my col', which cannot name a frame's "
        "column: a column's name is letters, digits and underscores, not starting with a "
        "digit and not a Python keyword.",
    ),
    "a column named twice": (
        [side("a", "one", {"name": "x", "type": "int"}, {"name": "x", "type": "str"})],
        "The Workbench Input's table 'a' names a column 'x' twice.",
    ),
    # The quote store keeps each table under its name, so its names are the store's rules too.
    "columns differing in case": (
        [side("a", "one", {"name": "x", "type": "int"}, {"name": "X", "type": "str"})],
        "The Workbench Input's table 'a' has columns 'x' and 'X', which differ only in case, "
        "so a database cannot hold both.",
    ),
    "a table name too long": (
        [side("t" * 64)],
        f"The Workbench Input's table {'t' * 64!r} has a name of 64 characters: a name is at "
        "most 63, the longest every database keeps whole.",
    ),
    "a column name too long": (
        [side("a", "one", {"name": "c" * 64, "type": "int"})],
        f"The Workbench Input's table 'a' has a column {'c' * 64!r} of 64 characters: a name "
        "is at most 63, the longest every database keeps whole.",
    ),
    "a table the store keeps": (
        [side("haute_x")],
        "The Workbench Input's table 'haute_x' starts haute_, which the quote store keeps for "
        "its own names.",
    ),
    "a column the store keeps": (
        [side("a", "one", {"name": "haute_x", "type": "int"})],
        "The Workbench Input's table 'a' has a column 'haute_x', and a name starting haute_ is "
        "the quote store's own.",
    ),
    "a table SQLite keeps": (
        [side("sqlite_x")],
        "The Workbench Input's table 'sqlite_x' starts sqlite_, which SQLite keeps for its own "
        "tables.",
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
        {"name": "2nd", "inputs": [column("x")]},
        # Two columns of one name, which a form holds until its folder refuses them on save.
        {"name": "priced", "outputs": [column("x"), column("x")]},
    )

    with pytest.raises(WorkbenchTablesError, match="The Workbench Input's table '2nd'"):
        input_tables(spec)
    with pytest.raises(WorkbenchTablesError, match="The Workbench Output's table 'priced'"):
        output_tables(spec)


def test_a_tables_name_is_its_requests_and_its_port_is_its_name_and_side() -> None:
    # A Python keyword names a table, as a request and the store hold it: its ports are
    # class_input and class_output, which a pipeline can take.
    spec = form({"name": "class", "inputs": [column("x")], "outputs": [column("y")]})

    assert [table.port for table in input_tables(spec)] == ["class_input"]
    assert [table.port for table in output_tables(spec)] == ["class_output"]


def test_check_tables_refuses_a_port_clash_across_the_nodes_tables() -> None:
    (one,) = parse_workbench_tables([side("a")], owner="Workbench Input")
    (other,) = parse_workbench_tables([side("A")], owner="Workbench Input")
    (output,) = parse_workbench_tables([side("a", of="output")], owner="Workbench Output")

    # One port twice, two ports one whatever their case, and a table's two sides on one node.
    with pytest.raises(WorkbenchTablesError, match="The Workbench Input's tables name 'a' twice"):
        check_tables([one, one], owner="Workbench Input")
    with pytest.raises(
        WorkbenchTablesError,
        match=re.escape("The Workbench Input's ports 'a_input' and 'A_input' differ only in case"),
    ):
        check_tables([one, other], owner="Workbench Input")
    with pytest.raises(WorkbenchTablesError, match="The Workbench Input's tables name 'a' twice"):
        check_tables([one, output], owner="Workbench Input")


def test_the_sample_typed_while_building_is_one_quote_by_table_and_column_names() -> None:
    spec = form(
        {
            "name": "policy_details",
            "rows": "one",
            "inputs": [
                column("state"),
                column("limit", "int"),
                column("cover", "bool"),
                column("start", "date"),
            ],
        },
        {
            "name": "equipment",
            "rows": "many",
            "inputs": [
                index(),
                column("item_id"),
                column("value", "float"),
                column("leased", "bool"),
            ],
        },
        {
            "name": "premiums",
            "rows": "many",
            "outputs": [index("line"), column("premium", "float")],
        },
        {"name": "untouched", "rows": "one", "inputs": [column("x")]},
    )
    spec.sample = {
        "policy_details": [{"state": " NY ", "limit": "1,000", "start": "2026-01-15"}],
        "equipment": [
            {"item_id": "A", "value": "$2,500.50", "leased": True},
            {"item_id": "", "value": " "},
            {"item_id": "B", "value": "lots"},
            {"item_id": "C", "value": "inf"},
        ],
        "premiums": [{"premium": "9"}],
        "untouched": [{}],
    }

    assert sample_quote(spec) == {
        # Typed as each column holds it; an unticked box in a filled row is false.
        "policy_details": {"state": "NY", "limit": 1000, "cover": False, "start": "2026-01-15"},
        # Empty rows are left out, each row numbered as it stands; a value that isn't a finite
        # number stays as typed, to be reported.
        "equipment": [
            {"row_number": 1, "item_id": "A", "value": 2500.5, "leased": True},
            {"row_number": 3, "item_id": "B", "value": "lots", "leased": False},
            {"row_number": 4, "item_id": "C", "value": "inf", "leased": False},
        ],
    }


def test_a_form_with_nothing_typed_has_an_empty_sample() -> None:
    spec = form(
        {
            "name": "policy_details",
            "rows": "one",
            "inputs": [column("state")],
        }
    )
    assert sample_quote(spec) == {}


def test_a_value_that_no_longer_fits_its_column_is_sent_as_it_is() -> None:
    # A text column holding "Y" became True/false; a ticked box's column became Integer.
    spec = form(
        {
            "name": "policy_details",
            "rows": "one",
            "inputs": [column("smoker", "bool"), column("count", "int"), column("note")],
        }
    )
    spec.sample = {"policy_details": [{"smoker": "Y", "count": True, "note": False}]}

    # Both reach the Workbench Input as values that are not of their column's type; an
    # unticked box in a column that is no longer True/false is nothing typed.
    assert sample_quote(spec) == {"policy_details": {"smoker": "Y", "count": True}}


def test_a_number_is_typed_by_the_rule_the_view_applies() -> None:
    spec = form(
        {
            "name": "policy",
            "rows": "one",
            "inputs": [column("limit", "int"), column("value", "float")],
        }
    )

    def typed(limit: str, value: str) -> dict[str, Any]:
        spec.sample = {"policy": [{"limit": limit, "value": value}]}
        return sample_quote(spec)["policy"]

    assert typed("£1,000", " 2.5e2 ") == {"limit": 1000, "value": 250.0}
    assert typed("+7", "-.5") == {"limit": 7, "value": -0.5}
    # What the view marks as not a number, the server keeps as typed, even where Python's
    # float would read it: the two apply one rule.
    assert typed("1_000", "١٢") == {"limit": "1_000", "value": "١٢"}
    assert typed("inf", "nan") == {"limit": "inf", "value": "nan"}
    # A number too large for a float is not a finite one: kept as typed, as the view keeps it.
    assert typed("1e999", "-1e999") == {"limit": "1e999", "value": "-1e999"}


def test_a_sample_with_an_empty_many_row_table_prices_as_its_quote_would_when_deployed() -> None:
    spec = with_quote_details(
        form(
            {
                "name": "policy_details",
                "rows": "one",
                "inputs": [column("state")],
            },
            {
                "name": "equipment",
                "rows": "many",
                "inputs": [index(), column("item_id"), column("value", "float")],
            },
        )
    )
    spec.sample = {
        "policy_details": [{"state": "TX"}],
        "equipment": [{"item_id": "", "value": " "}],
    }
    tables = dumped(input_tables(spec))

    # A table with nothing typed in it is left out of the sample, as a quote may leave it
    # out of a request; the Workbench Input then gives it no rows on either path.
    quote = sample_quote(spec)
    assert quote == {"policy_details": {"state": "TX"}}
    sampled = workbench_table_frames({"tables": tables, "sample": quote})
    request = {**quote, "quote_details": {"quote_reference": "Q-000001", "quote_option": 2}}
    requested = workbench_request_frames({"tables": tables}, [request])
    assert (
        sampled["equipment_input"].collect().to_dicts()
        == []
        == requested["equipment_input"].to_dicts()
    )
    # The sample is priced under the stand-in identity, a request under its own.
    assert sampled["policy_details_input"].collect().to_dicts() == [
        {"quote_reference": "PREVIEW", "quote_option": 1, "state": "TX"}
    ]
    assert requested["policy_details_input"].to_dicts() == [
        {"quote_reference": "Q-000001", "quote_option": 2, "state": "TX"}
    ]


@pytest.mark.parametrize(("kind", "first", "third"), [("int", 1, 3), ("str", "1", "3")])
def test_a_numbered_index_numbers_the_rows_as_they_stand_in_its_type(
    kind: str, first: object, third: object
) -> None:
    spec = form(
        {
            "name": "equipment",
            "rows": "many",
            "inputs": [
                {**column("line", kind), "index": "numbered"},
                column("item_id"),
                column("value", "float"),
            ],
        }
    )
    spec.sample = {"equipment": [{"item_id": "A"}, {}, {"item_id": "B", "value": "5"}]}

    (table,) = input_tables(spec)

    # The index is of the type the form holds for it, and the table's key.
    assert table.columns[2].model_dump() == {"name": "line", "type": kind}
    assert table.key == "line"
    # Each row's number as the rows stand: the empty second row is not sent.
    assert sample_quote(spec) == {
        "equipment": [
            {"line": first, "item_id": "A"},
            {"line": third, "item_id": "B", "value": 5.0},
        ]
    }


#: How typed text is read, which the view's ``parseTypedNumber`` and ``filled`` hold
#: themselves to as well.
TYPED_TEXT: dict[str, Any] = json.loads(
    (
        Path(__file__).resolve().parents[1] / "frontend/src/utils/__tests__/fixtures/typedText.json"
    ).read_text(encoding="utf-8")
)


@pytest.mark.parametrize(
    "case", TYPED_TEXT["numbers"], ids=[case["name"] for case in TYPED_TEXT["numbers"]]
)
def test_typed_text_is_read_by_the_rule_the_view_applies(case: dict[str, Any]) -> None:
    text, number = case["text"], case["number"]

    if number is None:
        assert (_typed("float", text), _typed("int", text)) == (text, text)
        return
    assert _typed("float", text) == number
    whole = _typed("int", text)
    assert whole == (int(case["integer"]) if "integer" in case else number)
    assert isinstance(whole, int) is case["whole"]


@pytest.mark.parametrize(
    "case", TYPED_TEXT["filled"], ids=[case["name"] for case in TYPED_TEXT["filled"]]
)
def test_a_value_counts_as_typed_by_the_rule_the_view_applies(case: dict[str, Any]) -> None:
    assert _filled(case["value"]) is case["filled"]


#: Whether a quote has anything typed, which the view's ``quoteEligible`` holds itself to.
TYPED_QUOTES: dict[str, Any] = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / "frontend/src/utils/__tests__/fixtures/typedQuotes.json"
    ).read_text(encoding="utf-8")
)


@pytest.mark.parametrize(
    "case", TYPED_QUOTES["cases"], ids=[case["name"] for case in TYPED_QUOTES["cases"]]
)
def test_a_quote_is_typed_by_the_rule_the_view_applies(case: dict[str, Any]) -> None:
    spec = FormSpec.model_validate(TYPED_QUOTES["form"])
    # The details the app sets decide what the sheets show, but are no typing, as its routes
    # count them.
    locked = {(table, column) for table, column in case["locked"]}
    sample = spec.model_copy(update={"sample": case["values"]})

    assert (sample_quote(sample, untyped=locked) != {}) is case["typed"]
    if "index_problems" in case:
        assert [problem.message for problem in typed_index_problems(spec, case["values"])] == case[
            "index_problems"
        ]


def test_a_whole_number_past_a_double_is_kept_exactly() -> None:
    assert _typed("int", "9007199254740993") == 9_007_199_254_740_993
    assert _typed("int", "-12,345,678,901,234,567,891") == -12_345_678_901_234_567_891


#: The cases the view's ``presetRows`` holds itself to as well.
PRESET_CASES: list[dict[str, Any]] = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / "frontend/src/utils/__tests__/fixtures/presetRows.json"
    ).read_text(encoding="utf-8")
)["cases"]


@pytest.mark.parametrize("case", PRESET_CASES, ids=[case["name"] for case in PRESET_CASES])
def test_the_rows_a_column_gives_a_table_are_read_by_the_rule_the_view_applies(
    case: dict[str, Any],
) -> None:
    table = SchemaTable.model_validate({"name": "coverage", **case["table"]})

    assert preset_rows(table, case["rows"]) == case["read"]


def test_the_sample_sends_every_row_an_index_of_values_gives_once_anything_else_is_typed() -> None:
    spec = form(
        {
            "name": "coverage",
            "rows": "many",
            "inputs": [
                column(
                    "peril",
                    options=["AOP", "EQ", "NW"],
                    index="values",
                    options_source={"file": "perils.csv", "column": "peril"},
                ),
                column("limit", "float"),
            ],
        }
    )
    # A value typed for NW alone, its row held first, and a row for a peril no longer allowed.
    spec.sample = {"coverage": [{"peril": "NW", "limit": "3"}, {"peril": "WD", "limit": "9"}]}

    # Every peril's row is sent, a row holding its peril alone included, in the values' order.
    assert sample_quote(spec) == {
        "coverage": [{"peril": "AOP"}, {"peril": "EQ"}, {"peril": "NW", "limit": 3.0}]
    }
    # The perils are not typing, nor is a value in a row left out: such a quote is empty, as
    # a blank one is, so the app's pricing and saving refuse it.
    spec.sample = {}
    assert sample_quote(spec) == {}
    assert quote_request(spec, {}) == {}
    assert quote_request(spec, {"coverage": [{"peril": "WD", "limit": "9"}]}) == {}


def test_a_value_in_a_column_the_sheets_hide_is_left_out_of_the_sample() -> None:
    spec = form(
        {
            "name": "policy",
            "rows": "one",
            "inputs": [column("bi_cover", "bool"), column("assured")],
        },
        {
            "name": "interruption",
            "rows": "one",
            "inputs": [column("bi_limit", "float"), column("extended", "bool")],
        },
        {
            "name": "equipment",
            "rows": "many",
            "inputs": [
                {**column("line", "int"), "index": "numbered"},
                column("item_id"),
                column("value", "float"),
                column("leased", "bool"),
            ],
        },
        pages=[
            {
                "id": "p_main",
                "title": "Main",
                "width": 1280,
                "sections": [
                    section(
                        "s_main",
                        widget("w_policy", "collection", "policy.bi_cover", "policy.assured"),
                        widget("w_items", "tableInput", "equipment.line", "equipment.item_id"),
                    )
                ],
            },
            {
                "id": "p_bi",
                "title": "Business interruption",
                "show_when": {
                    "conditions": [{"table": "policy", "column": "bi_cover", "op": "ticked"}]
                },
                "width": 1280,
                "sections": [
                    section(
                        "s_bi",
                        widget(
                            "w_bi", "collection", "interruption.bi_limit", "interruption.extended"
                        ),
                        widget("w_values", "tableInput", "equipment.value", "equipment.leased"),
                    )
                ],
            },
        ],
    )
    spec.sample = {
        "policy": [{"bi_cover": False, "assured": "Acme"}],
        "interruption": [{"bi_limit": "500,000", "extended": True}],
        "equipment": [{"value": "10", "leased": True}, {"item_id": "B", "value": "5"}],
    }
    typed = copy.deepcopy(spec.sample)

    # The box is unticked, so the second sheet hides: its text and tick are left out, its
    # unticked box is not made false, the first item row, holding nothing else, is left
    # out as an empty row is, and the second keeps its number as the rows stand.
    assert sample_quote(spec) == {
        "policy": {"bi_cover": False, "assured": "Acme"},
        "equipment": [{"line": 2, "item_id": "B"}],
    }
    # The values are kept as typed, and are in the quote again once their sheet shows.
    assert spec.sample == typed
    spec.sample["policy"][0]["bi_cover"] = True
    assert sample_quote(spec) == {
        "policy": {"bi_cover": True, "assured": "Acme"},
        "interruption": {"bi_limit": 500000.0, "extended": True},
        "equipment": [
            {"line": 1, "value": 10.0, "leased": True},
            {"line": 2, "item_id": "B", "value": 5.0, "leased": False},
        ],
    }


def test_a_one_row_table_may_name_a_column_quote_row() -> None:
    # table_index is the store's number for a many-row table's rows alone.
    [table] = parse_workbench_tables(
        [side("a", "one", {"name": "table_index", "type": "int"})],
        owner="Workbench Input",
    )
    assert [column.name for column in table.columns] == ["table_index"]


def test_quote_details_reaches_the_pipeline_without_the_details_the_app_changes() -> None:
    # The details the app changes over a quote's life are the store's alone: no request
    # holds them and no frame has a column for them.
    [details] = input_tables(with_quote_details(form()))
    assert [column.name for column in details.columns] == [
        "quote_reference",
        "quote_option",
        "quote_name",
        "insured",
        "broker",
        "inception_date",
        "expiry_date",
        "is_renewal",
        "created_date",
        "created_by",
    ]


def test_quote_details_is_priced_whatever_the_sheets_hide_but_never_its_store_columns() -> None:
    spec = with_quote_details(
        form(
            {
                "name": "policy",
                "rows": "one",
                "inputs": [column("bi_cover", "bool"), column("assured")],
            },
            pages=[
                {
                    "id": "p_main",
                    "title": "Main",
                    "width": 1280,
                    "sections": [
                        section(
                            "s_main",
                            widget("w_policy", "collection", "policy.bi_cover", "policy.assured"),
                        )
                    ],
                },
                {
                    "id": "p_quote",
                    "title": "Quote",
                    "show_when": {
                        "conditions": [{"table": "policy", "column": "bi_cover", "op": "ticked"}]
                    },
                    "width": 1280,
                    "sections": [
                        section(
                            "s_quote",
                            widget(
                                "w_quote",
                                "collection",
                                "quote_details.quote_reference",
                                "quote_details.quote_option",
                                "quote_details.quote_name",
                                "quote_details.stage",
                                "quote_details.modified_date",
                                "quote_details.created_by",
                            ),
                        )
                    ],
                },
            ],
        )
    )
    values = {
        "quote_details": [
            {
                "quote_reference": "Q-000007",
                "quote_option": "2",
                "quote_name": "Fleet",
                "stage": "priced",
                "modified_date": "2026-10-12",
                "created_by": "ana@example.com",
            }
        ],
        "policy": [{"bi_cover": False, "assured": "Acme"}],
    }

    # The second sheet hides, and quote_details is priced whole all the same: the quote's
    # name stays with its reference, option and creation; its stage and the rest of its
    # administration are the store's and never priced.
    assert quote_request(spec, values) == {
        "quote_details": {
            "quote_reference": "Q-000007",
            "quote_option": 2,
            "quote_name": "Fleet",
            "is_renewal": False,
            "created_by": "ana@example.com",
        },
        "policy": {"bi_cover": False, "assured": "Acme"},
    }


def _identity_tables() -> list[dict[str, Any]]:
    return dumped(
        input_tables(
            with_quote_details(
                form(
                    {
                        "name": "policy",
                        "rows": "one",
                        "inputs": [column("state")],
                    },
                    {
                        "name": "equipment",
                        "rows": "many",
                        "inputs": [index(), column("item_id")],
                    },
                )
            )
        )
    )


def test_a_request_names_the_quote_in_quote_details_and_every_table_takes_it() -> None:
    request = {
        "quote_details": {"quote_reference": "Q-000004", "quote_option": 3},
        "policy": {"state": "TX"},
        "equipment": [{"row_number": 1, "item_id": "A"}, {"row_number": 2, "item_id": "B"}],
    }

    frames = workbench_request_frames({"tables": _identity_tables()}, [request])

    # A request holds each table under its name; the frames are its input sides, by port.
    assert frames["policy_input"].to_dicts() == [
        {"quote_reference": "Q-000004", "quote_option": 3, "state": "TX"}
    ]
    assert frames["equipment_input"].to_dicts() == [
        {"quote_reference": "Q-000004", "quote_option": 3, "row_number": 1, "item_id": "A"},
        {"quote_reference": "Q-000004", "quote_option": 3, "row_number": 2, "item_id": "B"},
    ]


@pytest.mark.parametrize(
    ("change", "refusal"),
    [
        # A quote written before the pipeline read a workbench's is told what to add.
        (
            lambda r: r.pop("quote_details"),
            "the request holds no quote_details: a quote names its quote_reference and "
            "quote_option there",
        ),
        (
            lambda r: r["quote_details"].pop("quote_reference"),
            "quote_details.quote_reference is None",
        ),
        (
            lambda r: r["quote_details"].update(quote_reference=" "),
            "quote_details.quote_reference is ' '",
        ),
        (lambda r: r["quote_details"].pop("quote_option"), "quote_details.quote_option is None"),
        (lambda r: r["quote_details"].update(quote_option=0), "quote_details.quote_option is 0"),
        (
            lambda r: r["quote_details"].update(quote_option=True),
            "quote_details.quote_option is True",
        ),
        (lambda r: r["policy"].update(quote_option=3), "policy.quote_option is given"),
        (
            lambda r: r["equipment"][1].update(quote_reference="X"),
            r"equipment\[1\].quote_reference is given",
        ),
    ],
)
def test_a_request_without_the_quotes_identity_or_giving_it_twice_is_refused(
    change: Any, refusal: str
) -> None:
    request: dict[str, Any] = {
        "quote_details": {"quote_reference": "Q-000004", "quote_option": 3},
        "policy": {"state": "TX"},
        "equipment": [{"row_number": 1, "item_id": "A"}, {"row_number": 2, "item_id": "B"}],
    }
    change(request)

    with pytest.raises(WorkbenchTablesError, match=refusal):
        workbench_request_frames({"tables": _identity_tables()}, [request])


def test_the_quote_of_nulls_is_a_request_priced_under_the_stand_in_identity() -> None:
    tables = _identity_tables()

    frames = workbench_table_frames({"tables": tables, "sample": {}})

    # Each table's own columns null but a many-row table's key, its identity the stand-in.
    assert frames["policy_input"].collect().to_dicts() == [
        {"quote_reference": "PREVIEW", "quote_option": 1, "state": None}
    ]
    assert frames["equipment_input"].collect().to_dicts() == [
        {"quote_reference": "PREVIEW", "quote_option": 1, "row_number": 1, "item_id": None}
    ]
    # Tables leading with the identity without quote_details to give it cannot be read.
    with pytest.raises(WorkbenchTablesError, match="the tables hold no quote_details"):
        workbench_table_frames({"tables": tables[1:], "sample": {}})


def test_a_value_a_chart_that_shows_plots_is_left_out_while_the_collection_taking_it_hides() -> (
    None
):
    spec = form(
        {
            "name": "policy",
            "rows": "one",
            "inputs": [column("bi_cover", "bool"), column("assured"), column("bi_limit", "float")],
        },
        pages=[
            {
                "id": "p_main",
                "title": "Main",
                "width": 1280,
                "sections": [
                    section(
                        "s_main",
                        widget("w_policy", "collection", "policy.bi_cover", "policy.assured"),
                        widget("w_chart", "chart", "policy.bi_limit"),
                    )
                ],
            },
            {
                "id": "p_bi",
                "title": "Business interruption",
                "show_when": {
                    "conditions": [{"table": "policy", "column": "bi_cover", "op": "ticked"}]
                },
                "width": 1280,
                "sections": [section("s_bi", widget("w_bi", "collection", "policy.bi_limit"))],
            },
        ],
    )
    spec.sample = {"policy": [{"bi_cover": False, "assured": "Acme", "bi_limit": "500,000"}]}
    typed = copy.deepcopy(spec.sample)

    # A Chart takes nothing typed, so the limit it plots goes with the Collection's sheet.
    assert sample_quote(spec) == {"policy": {"bi_cover": False, "assured": "Acme"}}
    assert spec.sample == typed
    spec.sample["policy"][0]["bi_cover"] = True
    assert sample_quote(spec) == {
        "policy": {"bi_cover": True, "assured": "Acme", "bi_limit": 500000.0}
    }


def test_a_value_in_a_hidden_section_is_left_out_and_back_once_the_section_shows() -> None:
    flood = {"conditions": [{"table": "policy", "column": "flood", "op": "ticked"}]}
    spec = form(
        {
            "name": "policy",
            "rows": "one",
            "inputs": [column("flood", "bool"), column("assured"), column("depth", "float")],
        },
        pages=[
            {
                "id": "p_main",
                "title": "Main",
                "width": 1280,
                "sections": [
                    section(
                        "s_main", widget("w_policy", "collection", "policy.flood", "policy.assured")
                    ),
                    {
                        **section("s_flood", widget("w_flood", "collection", "policy.depth")),
                        "show_when": flood,
                    },
                ],
            }
        ],
    )
    spec.sample = {"policy": [{"flood": False, "assured": "Acme", "depth": "2.5"}]}
    typed = copy.deepcopy(spec.sample)

    # The section hides on a sheet that shows, and takes the depth typed into it with it.
    assert sample_quote(spec) == {"policy": {"flood": False, "assured": "Acme"}}
    assert spec.sample == typed
    spec.sample["policy"][0]["flood"] = True
    assert sample_quote(spec) == {"policy": {"flood": True, "assured": "Acme", "depth": 2.5}}


def test_output_tables_become_the_workbench_outputs_tables_in_schema_order() -> None:
    spec = form(
        {
            "name": "policy_details",
            "rows": "one",
            "inputs": [column("state")],
        },
        {
            "name": "layer_premiums",
            "rows": "many",
            "outputs": [
                index("layer_no"),
                column("layer", "int"),
                column("premium", "float"),
            ],
        },
        {
            "name": "pricing_output",
            "rows": "one",
            "outputs": [column("charged_premium", "float")],
        },
    )

    # The same shape as the input sides: in the response a one-row table is an object under
    # its table's name and a many-row table a list of objects under its table's name. A
    # side's port is its table's name and its side, whatever the name ends in.
    assert dumped(output_tables(spec)) == [
        {
            "name": "layer_premiums",
            "side": "output",
            "port": "layer_premiums_output",
            "rows": "many",
            "columns": [
                *IDENTITY,
                {"name": "layer_no", "type": "int"},
                {"name": "layer", "type": "int"},
                {"name": "premium", "type": "float"},
            ],
            "key": "layer_no",
            "null_key": None,
            "index_kind": "numbered",
            "index_values": [],
        },
        {
            "name": "pricing_output",
            "side": "output",
            "port": "pricing_output_output",
            "rows": "one",
            "columns": [*IDENTITY, {"name": "charged_premium", "type": "float"}],
            "key": None,
            "null_key": None,
            "index_kind": None,
            "index_values": [],
        },
    ]
    assert [table.port for table in input_tables(spec)] == ["policy_details_input"]


def test_workbench_tables_gathers_the_three_as_the_route_serves_them() -> None:
    spec = form(
        {"name": "policy_details", "inputs": [column("state")]},
        {
            "name": "pricing_output",
            "outputs": [column("premium", "float")],
        },
    )
    spec.sample = {"policy_details": [{"state": "NY"}]}

    gathered = workbench_tables(spec)

    assert gathered.tables == input_tables(spec)
    assert gathered.sample == {"policy_details": {"state": "NY"}}
    assert gathered.response_tables == output_tables(spec)
