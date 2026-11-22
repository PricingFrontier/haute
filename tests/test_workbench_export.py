"""An Export to an Excel workbook of cells (specs/workbench, Control flow 38): the workbook
read back as Excel would read it, each problem refused in the view's words, the sample
held to the one priced, and the route answering the file by its name."""

from __future__ import annotations

import copy
import datetime
import io
import zipfile
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from haute._json_safe import to_json_safe
from haute._workbench_export import (
    CELL_TEXT_LIMIT,
    XLSX_MEDIA_TYPE,
    ExportRefusedError,
    StaleExportError,
    cell_address,
    cells_problems,
    column_label,
    export_file_name,
    number_format,
    preview_export,
    sheet_name_problem,
)
from haute._workbench_form import FormSpec, SchemaColumn, with_quote_details
from haute._workbench_tables import sample_quote
from haute.routes._error_handlers import install_failure_answers
from haute.routes.workbench import router
from tests._project import use_project
from tests._workbench_helpers import enable, write_form

EXPORT_ID = "w_out"


def column(name: str, type_: str = "str", **rest: Any) -> dict[str, Any]:
    return {"name": name, "type": type_, **rest}


def cell(field: str, sheet: str, address: str, label: str = "") -> dict[str, str]:
    table, name = field.split(".")
    return {"table": table, "column": name, "sheet": sheet, "cell": address, "label_cell": label}


def block(
    table: str, columns: list[str], sheet: str, address: str, header: str = "labels"
) -> dict[str, Any]:
    return {"table": table, "columns": columns, "sheet": sheet, "cell": address, "header": header}


#: A form whose Export writes the insured's details, the policy's limit and tick, and its
#: price into a Slip and a Summary, the insured's name on both; a note the sheets hide; a
#: detail only the app sets; a schedule of many rows, which no cell may take, each item
#: priced; and the layers pricing gives, a table of outputs alone.
FORM: dict[str, Any] = {
    "version": 1,
    "name": "x",
    "schema": {
        "tables": [
            {
                "name": "quote_details",
                "rows": "one",
                "inputs": [column("insured"), column("inception_date", "date")],
            },
            {
                "name": "policy",
                "rows": "one",
                "inputs": [
                    column("limit", "float", number_format="currency_gbp"),
                    column("sprinklered", "bool"),
                    column("note"),
                    column("vehicles", "int"),
                    column("secret"),
                ],
            },
            {
                "name": "pricing",
                "rows": "one",
                "outputs": [
                    column("premium", "float", number_format="currency_gbp"),
                    column("rate", "float", number_format="percent"),
                    column("band", "int"),
                ],
            },
            {
                "name": "items",
                "rows": "many",
                "inputs": [column("item", "int", index="numbered"), column("value", "float")],
                "outputs": [column("item_premium", "float", number_format="currency_gbp")],
            },
            {
                "name": "layers",
                "rows": "many",
                "outputs": [
                    column("layer", "int", index="numbered"),
                    column("layer_rate", "float"),
                ],
            },
        ]
    },
    "pages": [
        {
            "id": "p",
            "title": "Sheet 1",
            "width": 1280,
            "sections": [
                {
                    "id": "s",
                    "title": "s",
                    "height": 240,
                    "widgets": [
                        # The secret shows only while the policy is not sprinklered.
                        {
                            "id": "w_secret",
                            "name": "secret",
                            "type": "collection",
                            "x": 0,
                            "y": 0,
                            "w": 400,
                            "h": 120,
                            "fields": [{"table": "policy", "column": "secret"}],
                            "show_when": {
                                "conditions": [
                                    {"table": "policy", "column": "sprinklered", "op": "not_ticked"}
                                ]
                            },
                        },
                        {
                            "id": EXPORT_ID,
                            "name": "slip",
                            "type": "export",
                            "x": 0,
                            "y": 160,
                            "w": 160,
                            "h": 40,
                            "label": "Slip",
                            "tables": [],
                            "target": {
                                "format": "excel_cells",
                                "file_name": "{insured} {quote_reference}-{quote_option}",
                                "cells": [
                                    cell("quote_details.insured", "Slip", "B3"),
                                    cell("quote_details.inception_date", "Slip", "B4"),
                                    cell("policy.limit", "Slip", "B5"),
                                    cell("policy.sprinklered", "Slip", "B6"),
                                    cell("policy.note", "Slip", "B7"),
                                    cell("policy.secret", "Slip", "B8"),
                                    cell("quote_details.stage", "Slip", "B9"),
                                    cell("pricing.premium", "Summary", "C2"),
                                    cell("pricing.rate", "Summary", "C3"),
                                    cell("quote_details.insured", "Summary", "A1"),
                                    cell("quote_details.quote_reference", "Summary", "A2"),
                                    cell("quote_details.quote_option", "Summary", "A3"),
                                ],
                                "blocks": [],
                            },
                        },
                        {
                            "id": "w_csv",
                            "name": "csv",
                            "type": "export",
                            "x": 200,
                            "y": 160,
                            "w": 160,
                            "h": 40,
                            "label": "CSV",
                            "tables": ["policy"],
                            "target": {
                                "format": "csv",
                                "file_name": "x",
                                "headers": "names",
                                "values": "raw",
                                "delimiter": "comma",
                            },
                        },
                    ],
                }
            ],
        }
    ],
    "sample": {
        "quote_details": [{"insured": "Acme Ltd", "inception_date": "2026-11-12"}],
        "policy": [{"limit": "£1,000,000", "sprinklered": True, "note": "=1+1", "secret": "hush"}],
    },
}
PRICED: dict[str, list[dict[str, Any]]] = {
    "pricing": [{"quote_reference": "PREVIEW", "quote_option": 1, "premium": 1234.5, "rate": 0.055}]
}


def spec_of(form: dict[str, Any]) -> FormSpec:
    return FormSpec.model_validate(form)


def priced_sample(form: dict[str, Any]) -> dict[str, Any]:
    """The sample as the tables route types it for a pricing of *form*."""
    typed: dict[str, Any] = to_json_safe(sample_quote(with_quote_details(spec_of(form))))
    return typed


def export(
    form: dict[str, Any] = FORM, tables: dict[str, list[dict[str, Any]]] = PRICED
) -> tuple[str, bytes]:
    return preview_export(spec_of(form), EXPORT_ID, priced_sample(form), tables)


def target_of(form: dict[str, Any]) -> dict[str, Any]:
    widgets = form["pages"][0]["sections"][0]["widgets"]
    found: dict[str, Any] = next(w for w in widgets if w["id"] == EXPORT_ID)["target"]
    return found


def with_cells(
    *cells: dict[str, str], file_name: str = "slip", blocks: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    form = copy.deepcopy(FORM)
    target = target_of(form)
    target["cells"] = list(cells)
    target["file_name"] = file_name
    target["blocks"] = blocks or []
    return form


#: Two items typed and priced, the priced rows in another order than the typed ones, and
#: the layers pricing gives.
ITEMS_SAMPLE = [{"value": "100"}, {"value": "£250.50"}]
ITEMS_PRICED: dict[str, list[dict[str, Any]]] = {
    **PRICED,
    "items": [
        {"quote_reference": "PREVIEW", "quote_option": 1, "item": 2, "item_premium": 25.05},
        {"quote_reference": "PREVIEW", "quote_option": 1, "item": 1, "item_premium": 10.0},
    ],
    "layers": [
        {"quote_reference": "PREVIEW", "quote_option": 1, "layer": 1, "layer_rate": 0.5},
        {"quote_reference": "PREVIEW", "quote_option": 1, "layer": 2, "layer_rate": 0.25},
    ],
}


def with_blocks(*blocks: dict[str, Any], cells: tuple[dict[str, str], ...] = ()) -> dict[str, Any]:
    """FORM writing *blocks*, beside *cells*, with two items typed."""
    form = with_cells(*cells, blocks=list(blocks))
    form["sample"]["items"] = copy.deepcopy(ITEMS_SAMPLE)
    return form


def values_of(sheet: Any, cells: str) -> list[list[Any]]:
    return [[held.value for held in row] for row in sheet[cells]]


def test_a_workbook_of_cells_holds_each_value_typed_on_its_sheets_in_the_order_first_given() -> (
    None
):
    file_name, data = export()

    assert file_name == "Acme Ltd PREVIEW-1.xlsx"
    book = load_workbook(io.BytesIO(data))
    assert book.sheetnames == ["Slip", "Summary"]
    slip, summary = book["Slip"], book["Summary"]
    # An input as the server typed it: text, a day, a number read from what was typed, a tick.
    assert slip["B3"].value == "Acme Ltd"
    assert slip["B4"].value == datetime.datetime(2026, 11, 12)
    assert slip["B4"].number_format == "yyyy-mm-dd"
    assert slip["B5"].value == 1_000_000.0
    assert slip["B5"].number_format == '"£"#,##0.00'
    assert slip["B6"].value is True
    # Text is text, never a formula.
    assert (slip["B7"].value, slip["B7"].data_type) == ("=1+1", "s")
    # A hidden input the quote leaves out, and a detail only the app sets: empty.
    assert slip["B8"].value is None
    assert slip["B9"].value is None
    # An output as priced, in its column's format; the insured's name again; the stand-in
    # identity Preview prices under.
    assert (summary["C2"].value, summary["C2"].number_format) == (1234.5, '"£"#,##0.00')
    assert (summary["C3"].value, summary["C3"].number_format) == (0.055, "#,##0.0%")
    assert summary["A1"].value == "Acme Ltd"
    assert (summary["A2"].value, summary["A3"].value) == ("PREVIEW", 1)
    assert book.properties.creator == "Haute"


@pytest.mark.parametrize("text", ['=HYPERLINK("http://x")', "#N/A", "-2", "+1", "@SUM(A1)"])
def test_text_is_written_as_text_whatever_it_starts_with(text: str) -> None:
    form = with_cells(cell("policy.note", "Slip", "A1"))
    form["sample"]["policy"][0]["note"] = text

    _, data = export(form)

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert "<f>" not in sheet
    assert '<c r="A1" t="inlineStr">' in sheet
    read = load_workbook(io.BytesIO(data))["Slip"]["A1"]
    assert (read.value, read.data_type) == (text, "s")


def number_column(kind: str, places: int | None = None, grouping: bool = True) -> SchemaColumn:
    return SchemaColumn(
        name="n",
        type="float",
        number_format=kind,
        decimal_places=places,
        use_grouping=grouping,
    )


@pytest.mark.parametrize(
    ("kind", "places", "grouping", "value", "expected"),
    [
        # As it is, unless decimal places are set.
        ("general", None, True, 1234.5, "General"),
        ("general", 2, True, 1234.5, "#,##0.00"),
        ("general", 2, False, 1234.5, "0.00"),
        # A number with no places set shows its own, as the sheets show it exactly.
        ("number", None, True, 1234.5, "#,##0.0"),
        ("number", None, False, 1234, "0"),
        ("number", None, True, 1e-07, "#,##0.0000000"),
        ("number", 0, True, 1234.5, "#,##0"),
        # A percent up to two places, rounded half away from zero, none trailing.
        ("percent", None, True, 0.055, "#,##0.0%"),
        ("percent", None, True, 0.05, "#,##0%"),
        ("percent", None, True, 0.123456, "#,##0.00%"),
        # 0.005% rounds up to 0.01%, so it shows two places.
        ("percent", None, True, 0.00005, "#,##0.00%"),
        ("percent", None, True, 0.00004, "#,##0%"),
        ("percent", 1, False, 0.5, "0.0%"),
        # A currency two places unless set, its sign before it.
        ("currency_gbp", None, True, 3, '"£"#,##0.00'),
        ("currency_usd", 0, True, 3.5, '"US$"#,##0'),
        ("currency_eur", None, False, 3, '"€"0.00'),
    ],
)
def test_a_number_cell_takes_its_column_s_format_as_the_sheets_show_it(
    kind: str, places: int | None, grouping: bool, value: float, expected: str
) -> None:
    assert number_format(number_column(kind, places, grouping), value) == expected


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("A1", (1, 1)),
        ("B3", (2, 3)),
        ("AA10", (27, 10)),
        ("XFD1048576", (16_384, 1_048_576)),
        ("XFE1", None),
        ("A1048577", None),
        ("A0", None),
        ("B03", None),
        ("b3", None),
        ("$B$3", None),
        ("B", None),
        ("3", None),
        ("", None),
    ],
)
def test_an_address_is_a_cell_of_a_sheet_within_excel_s_limits(
    address: str, expected: tuple[int, int] | None
) -> None:
    assert cell_address(address) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Slip", None),
        ("A" * 31, None),
        ("A" * 32, "is longer than 31 characters"),
        ("Slip: final", "holds :"),
        ("a[1]", "holds ["),
        ("a/b", "holds /"),
        ("'Slip", "starts or ends with '"),
        ("Slip'", "starts or ends with '"),
        ("History", "is a name Excel keeps for itself"),
        ("history", "is a name Excel keeps for itself"),
        ("Histories", None),
    ],
)
def test_a_sheet_name_excel_refuses_says_why(name: str, expected: str | None) -> None:
    assert sheet_name_problem(name) == expected


SLIP_B3 = cell("quote_details.insured", "Slip", "B3")


@pytest.mark.parametrize(
    ("cells", "file_name", "problem"),
    [
        ([], "slip", "writes no cells or tables"),
        (
            [cell("policy.nope", "Slip", "B3")],
            "slip",
            "writes policy.nope, which is no longer in the schema",
        ),
        (
            [cell("nowhere.insured", "Slip", "B3")],
            "slip",
            "writes nowhere.insured, which is no longer in the schema",
        ),
        (
            [cell("items.value", "Slip", "B3")],
            "slip",
            "writes items.value, a column of a many-row table",
        ),
        ([cell("quote_details.insured", " ", "B3")], "slip", "writes to a sheet with no name"),
        (
            [cell("quote_details.insured", "A" * 32, "B3")],
            "slip",
            f'writes to the sheet "{"A" * 32}", which is longer than 31 characters',
        ),
        (
            [cell("quote_details.insured", "History", "B3")],
            "slip",
            'writes to the sheet "History", which is a name Excel keeps for itself',
        ),
        (
            [cell("quote_details.insured", "Slip", "B03")],
            "slip",
            'writes to "B03" on "Slip", which is not a cell like B3',
        ),
        (
            [SLIP_B3, cell("policy.note", "Slip", "B3")],
            "slip",
            'writes more than once to B3 on "Slip"',
        ),
        # A label's address is a cell too: typed in capitals, never the label's text.
        (
            [cell("quote_details.insured", "Slip", "B3", "a3")],
            "slip",
            'writes the label of quote_details.insured to "a3" on "Slip", which is not a cell '
            "like B3",
        ),
        (
            [cell("quote_details.insured", "Slip", "B3", "Insured")],
            "slip",
            'writes the label of quote_details.insured to "Insured" on "Slip", which is not a '
            "cell like B3",
        ),
        # A label takes its cell as a value does: on its own value, an earlier value or
        # label, or a later value on it.
        (
            [cell("quote_details.insured", "Slip", "B3", "B3")],
            "slip",
            'writes more than once to B3 on "Slip"',
        ),
        (
            [SLIP_B3, cell("policy.note", "Slip", "B4", "B3")],
            "slip",
            'writes more than once to B3 on "Slip"',
        ),
        (
            [cell("quote_details.insured", "Slip", "B3", "A3"), cell("policy.note", "Slip", "A3")],
            "slip",
            'writes more than once to A3 on "Slip"',
        ),
        (
            [
                cell("quote_details.insured", "Slip", "B3", "A3"),
                cell("policy.note", "Slip", "B4", "A3"),
            ],
            "slip",
            'writes more than once to A3 on "Slip"',
        ),
        (
            [SLIP_B3, cell("policy.note", "slip", "B4")],
            "slip",
            'names the sheets "Slip" and "slip", which Excel holds as one',
        ),
        ([SLIP_B3], " ", "has no file name"),
        (
            [SLIP_B3],
            "{broker}-{agent}",
            "names its file by {agent}, which is not a column of quote_details",
        ),
    ],
)
def test_each_problem_is_named_and_refuses_the_export(
    cells: list[dict[str, str]], file_name: str, problem: str
) -> None:
    form = with_cells(*cells, file_name=file_name)
    spec = with_quote_details(spec_of(form))

    widget = spec.pages[0].sections[0].widgets[1]
    assert cells_problems(spec, widget.target) == [problem]
    with pytest.raises(ExportRefusedError) as refused:
        export(form)
    assert refused.value.message == f'Export "Slip" cannot be written: it {problem}.'


ITEMS_A12 = block("items", ["item", "value"], "Slip", "A12")


@pytest.mark.parametrize(
    ("cells", "blocks", "problem"),
    [
        (
            [],
            [block("nowhere", ["item"], "Slip", "A12")],
            "writes the table nowhere, which is no longer in the schema",
        ),
        ([], [block("items", [], "Slip", "A12")], "writes the table items with no columns"),
        (
            [],
            [block("items", ["item", "nope"], "Slip", "A12")],
            "writes items.nope, which is no longer in the schema",
        ),
        (
            [],
            [block("items", ["value", "item", "value"], "Slip", "A12")],
            "writes items.value more than once in its table",
        ),
        ([], [block("items", ["item"], " ", "A12")], "writes to a sheet with no name"),
        (
            [],
            [block("items", ["item"], "History", "A12")],
            'writes to the sheet "History", which is a name Excel keeps for itself',
        ),
        (
            [SLIP_B3],
            [block("items", ["item"], "slip", "A12")],
            'names the sheets "Slip" and "slip", which Excel holds as one',
        ),
        (
            [],
            [block("items", ["item"], "Slip", "a12")],
            'writes the table items to "a12" on "Slip", which is not a cell like B3',
        ),
        (
            [],
            [block("items", ["item", "value"], "Slip", "XFD1")],
            'writes the table items from XFD1 on "Slip" past XFD, the last column',
        ),
        # A block takes its columns from its top row down: a value or a label there, or
        # another block's columns, is under it.
        (
            [SLIP_B3],
            [block("items", ["item", "value"], "Slip", "A1")],
            'writes the table items from A1 on "Slip" over B3, which is written already',
        ),
        (
            [SLIP_B3],
            [block("items", ["item", "value"], "Slip", "B3")],
            'writes the table items from B3 on "Slip" over B3, which is written already',
        ),
        (
            [cell("quote_details.insured", "Slip", "C3", "A900")],
            [ITEMS_A12],
            'writes the table items from A12 on "Slip" over A900, which is written already',
        ),
        (
            [],
            [ITEMS_A12, block("layers", ["layer"], "Slip", "B2")],
            'writes the table layers from B2 on "Slip" over the table items from A12',
        ),
        (
            [],
            [ITEMS_A12, block("items", ["value"], "Slip", "A900")],
            'writes the table items from A900 on "Slip" over the table items from A12',
        ),
    ],
)
def test_each_problem_of_a_block_is_named_and_refuses_the_export(
    cells: list[dict[str, str]], blocks: list[dict[str, Any]], problem: str
) -> None:
    form = with_blocks(*blocks, cells=tuple(cells))
    spec = with_quote_details(spec_of(form))

    assert cells_problems(spec, spec.pages[0].sections[0].widgets[1].target) == [problem]
    with pytest.raises(ExportRefusedError) as refused:
        export(form, ITEMS_PRICED)
    assert refused.value.message == f'Export "Slip" cannot be written: it {problem}.'


@pytest.mark.parametrize(
    ("cells", "blocks"),
    [
        # Above a block, or beside it, a value or a label is no part of it.
        ([cell("quote_details.insured", "Slip", "B11", "A11")], [ITEMS_A12]),
        ([cell("quote_details.insured", "Slip", "C900", "C12")], [ITEMS_A12]),
        # Blocks side by side, or of one address on two sheets.
        ([], [ITEMS_A12, block("layers", ["layer"], "Slip", "C12")]),
        ([], [ITEMS_A12, block("items", ["item", "value"], "Summary", "A12")]),
        # The last column a block may take is XFD.
        ([], [block("items", ["item", "value"], "Slip", "XFC1")]),
        # A block alone, no cell written beside it.
        ([], [ITEMS_A12]),
    ],
)
def test_a_block_beside_or_under_what_else_is_written_has_no_problem(
    cells: list[dict[str, str]], blocks: list[dict[str, Any]]
) -> None:
    form = with_blocks(*blocks, cells=tuple(cells))
    spec = with_quote_details(spec_of(form))

    assert cells_problems(spec, spec.pages[0].sections[0].widgets[1].target) == []


def test_a_block_s_problems_follow_the_cells_in_the_view_s_order() -> None:
    form = with_blocks(
        block("items", ["nope", "value", "value"], "History", "A0"),
        cells=(cell("policy.nope", "Slip", "B3"),),
    )
    spec = with_quote_details(spec_of(form))

    # The same list `cellsProblems` gives in the view (workbenchForm.test.ts).
    assert cells_problems(spec, spec.pages[0].sections[0].widgets[1].target) == [
        "writes policy.nope, which is no longer in the schema",
        "writes items.nope, which is no longer in the schema",
        "writes items.value more than once in its table",
        'writes to the sheet "History", which is a name Excel keeps for itself',
        'writes the table items to "A0" on "History", which is not a cell like B3',
    ]


def test_a_block_writes_its_rows_under_its_header_lined_up_by_index_not_place() -> None:
    form = with_blocks(block("items", ["item", "item_premium", "value"], "Slip", "B12"))

    _, data = export(form, ITEMS_PRICED)

    slip = load_workbook(io.BytesIO(data))["Slip"]
    # The header of the columns' labels, then a row for each typed row in order, each with
    # its own priced output, in the block's order of columns.
    assert values_of(slip, "B12:D14") == [
        ["Item", "Item premium", "Value"],
        [1, 10.0, 100.0],
        [2, 25.05, 250.5],
    ]
    assert slip["B12"].data_type == "s"
    assert slip["C13"].number_format == '"£"#,##0.00'
    # Nothing past its rows or its columns.
    assert values_of(slip, "B15:D15") == [[None, None, None]]
    assert values_of(slip, "A12:A14") == [[None], [None], [None]]
    assert values_of(slip, "E12:E14") == [[None], [None], [None]]


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("labels", [["Item", "Value"], [1, 100.0], [2, 250.5]]),
        ("names", [["item", "value"], [1, 100.0], [2, 250.5]]),
        ("none", [[1, 100.0], [2, 250.5], [None, None]]),
    ],
)
def test_a_block_is_headed_by_its_columns_labels_their_names_or_nothing(
    header: str, expected: list[list[Any]]
) -> None:
    form = with_blocks(block("items", ["item", "value"], "Slip", "A1", header))
    form["schema"]["tables"][3]["inputs"][0]["label"] = "=Item"

    _, data = export(form, ITEMS_PRICED)

    slip = load_workbook(io.BytesIO(data))["Slip"]
    if header == "labels":
        expected[0][0] = "=Item"
    assert values_of(slip, "A1:B3") == expected
    assert slip["A1"].data_type == ("s" if header != "none" else "n")


def test_a_block_of_a_one_row_table_writes_its_row_and_of_outputs_alone_its_priced_rows() -> None:
    form = with_blocks(
        block("policy", ["note", "limit"], "Summary", "E1"),
        block("pricing", ["premium", "rate"], "Summary", "H1", "names"),
        block("layers", ["layer_rate", "layer"], "Layers", "A1"),
    )

    _, data = export(form, ITEMS_PRICED)

    book = load_workbook(io.BytesIO(data))
    assert book.sheetnames == ["Summary", "Layers"]
    summary, layers = book["Summary"], book["Layers"]
    assert values_of(summary, "E1:F3") == [["Note", "Limit"], ["=1+1", 1e6], [None, None]]
    assert summary["E2"].data_type == "s"
    assert values_of(summary, "H1:I2") == [["premium", "rate"], [1234.5, 0.055]]
    assert values_of(layers, "A1:B4") == [
        ["Layer rate", "Layer"],
        [0.5, 1],
        [0.25, 2],
        [None, None],
    ]


def test_a_block_of_a_table_with_a_row_for_each_value_writes_each_value_s_row_in_order() -> None:
    form = with_blocks(
        block("perils", ["peril", "covered", "limit", "peril_premium"], "Perils", "A1")
    )
    form["schema"]["tables"].append(
        {
            "name": "perils",
            "rows": "many",
            "inputs": [
                column(
                    "peril",
                    index="values",
                    options=["Fire", "Flood", "Storm"],
                    options_source={"file": "perils.csv", "column": "peril"},
                ),
                column("covered", "bool"),
                column("limit", "float"),
            ],
            "outputs": [column("peril_premium", "float")],
        }
    )
    form["sample"]["perils"] = [{"peril": "Flood", "covered": True, "limit": "500"}]
    priced = [("Storm", 3.0), ("Fire", 1.0), ("Flood", 2.0)]
    tables = {
        **ITEMS_PRICED,
        "perils": [
            {
                "quote_reference": "PREVIEW",
                "quote_option": 1,
                "peril": name,
                "peril_premium": premium,
            }
            for name, premium in priced
        ],
    }

    _, data = export(form, tables)

    perils = load_workbook(io.BytesIO(data))["Perils"]
    # Every value's row, typed in or not, in the values' order, as pricing prices them,
    # each with the priced row of its value.
    assert values_of(perils, "A1:D5") == [
        ["Peril", "Covered", "Limit", "Peril premium"],
        ["Fire", False, None, 1.0],
        ["Flood", True, 500.0, 2.0],
        ["Storm", False, None, 3.0],
        [None, None, None, None],
    ]


def test_a_block_of_a_table_with_no_rows_writes_its_header_alone() -> None:
    form = with_blocks(block("items", ["item", "value"], "Slip", "A1"))
    form["sample"]["items"] = []

    _, data = export(form, PRICED)

    slip = load_workbook(io.BytesIO(data))["Slip"]
    assert values_of(slip, "A1:B2") == [["Item", "Value"], [None, None]]


def test_a_block_whose_rows_run_past_the_sheet_s_last_row_is_refused_naming_it() -> None:
    form = with_blocks(block("items", ["item"], "Slip", "A1048575"))

    with pytest.raises(ExportRefusedError) as refused:
        export(form, ITEMS_PRICED)

    assert refused.value.message == (
        'The table items has 2 rows, more than fit on "Slip" from A1048575 down: place it higher.'
    )
    # Its last row on the sheet's last is written.
    form = with_blocks(block("items", ["item"], "Slip", "A1048574"))
    _, data = export(form, ITEMS_PRICED)
    assert load_workbook(io.BytesIO(data))["Slip"]["A1048576"].value == 2


def test_a_block_s_value_not_of_its_column_s_type_is_refused_naming_the_field() -> None:
    form = with_blocks(block("items", ["item_premium"], "Slip", "A1"))
    tables = copy.deepcopy(ITEMS_PRICED)
    tables["items"][0]["item_premium"] = "lots"

    with pytest.raises(ExportRefusedError) as refused:
        export(form, tables)

    assert refused.value.message == (
        "items.item_premium is 'lots', not 'float': price the sample again."
    )


def test_a_cell_s_problems_are_named_in_the_view_s_order() -> None:
    form = with_cells(cell("policy.nope", "History", "B03", "x"))
    spec = with_quote_details(spec_of(form))

    widget = spec.pages[0].sections[0].widgets[1]
    # The same list `cellsProblems` gives in the view (workbenchForm.test.ts).
    assert cells_problems(spec, widget.target) == [
        "writes policy.nope, which is no longer in the schema",
        'writes to the sheet "History", which is a name Excel keeps for itself',
        'writes to "B03" on "History", which is not a cell like B3',
        'writes the label of policy.nope to "x" on "History", which is not a cell like B3',
    ]


@pytest.mark.parametrize("blank", ["", " "])
def test_a_label_cell_left_blank_writes_no_label(blank: str) -> None:
    form = with_cells(cell("quote_details.insured", "Slip", "B3", blank))
    spec = with_quote_details(spec_of(form))

    assert cells_problems(spec, spec.pages[0].sections[0].widgets[1].target) == []
    _, data = export(form)
    slip = load_workbook(io.BytesIO(data))["Slip"]
    assert [[value for value in row if value is not None] for row in slip.values] == [
        [],
        [],
        ["Acme Ltd"],
    ]


def test_one_label_cell_on_two_sheets_is_two_cells() -> None:
    form = with_cells(
        cell("quote_details.insured", "Slip", "B3", "A3"),
        cell("policy.note", "Summary", "B3", "A3"),
    )
    spec = with_quote_details(spec_of(form))

    assert cells_problems(spec, spec.pages[0].sections[0].widgets[1].target) == []


def test_a_label_cell_holds_its_column_s_label_as_text() -> None:
    form = with_cells(
        cell("quote_details.insured", "Slip", "B3", "A3"),
        cell("policy.limit", "Slip", "B4", "A4"),
        cell("pricing.premium", "Slip", "B5", "A5"),
        cell("policy.note", "Slip", "B6", "C6"),
    )
    tables = form["schema"]["tables"]
    tables[1]["inputs"][0]["label"] = "Sum insured"
    tables[1]["inputs"][2]["label"] = "=Total"

    _, data = export(form)

    slip = load_workbook(io.BytesIO(data))["Slip"]
    # The schema's label, else the column's name made readable; text, whatever it starts with.
    assert [(slip[at].value, slip[at].data_type) for at in ("A3", "A4", "A5", "C6")] == [
        ("Insured", "s"),
        ("Sum insured", "s"),
        ("Premium", "s"),
        ("=Total", "s"),
    ]
    # Each value in its own cell beside its label.
    assert (slip["B3"].value, slip["B4"].value, slip["B5"].value) == ("Acme Ltd", 1e6, 1234.5)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert "<f>" not in archive.read("xl/worksheets/sheet1.xml").decode("utf-8")


@pytest.mark.parametrize(
    ("name", "label", "expected"),
    [
        ("sum_insured", "", "Sum insured"),
        ("a__b", "", "A b"),
        ("_x_", "", "X"),
        ("sum_Insured", "", "Sum Insured"),
        ("premium", "", "Premium"),
        ("premium", "Annual premium", "Annual premium"),
    ],
)
def test_a_column_s_label_is_its_own_else_its_name_made_readable(
    name: str, label: str, expected: str
) -> None:
    # The same cases as `readableName` in the view (workbenchForm.test.ts).
    assert column_label(SchemaColumn(name=name, label=label)) == expected


def test_a_label_holding_a_character_excel_cannot_hold_is_refused_naming_it() -> None:
    form = with_cells(cell("policy.note", "Slip", "B3", "A3"))
    form["schema"]["tables"][1]["inputs"][2]["label"] = "bell\x07"

    with pytest.raises(ExportRefusedError) as refused:
        export(form)

    assert refused.value.message == (
        "The label of policy.note holds a character Excel cannot hold in a cell, such as a "
        "control character: take it out of the column's label in the schema."
    )


def test_a_cell_at_excel_s_last_address_on_a_sheet_of_the_longest_name_is_written() -> None:
    name = "S" * 31
    form = with_cells(cell("quote_details.insured", name, "XFD1048576"))

    _, data = export(form)

    assert load_workbook(io.BytesIO(data))[name]["XFD1048576"].value == "Acme Ltd"


def test_a_sample_other_than_the_one_priced_is_refused_as_stale() -> None:
    priced = priced_sample(FORM)
    priced["quote_details"]["insured"] = "Someone else"

    with pytest.raises(StaleExportError) as stale:
        preview_export(spec_of(FORM), EXPORT_ID, priced, PRICED)

    assert stale.value.message == (
        "The sample changed after it was priced: export once it is priced again."
    )


@pytest.mark.parametrize(
    ("component", "message"),
    [
        (
            "w_csv",
            "csv is not an Export to an Excel workbook of cells, the only export that runs.",
        ),
        (
            "w_secret",
            "secret is not an Export to an Excel workbook of cells, the only export that runs.",
        ),
        ("w_gone", "The form has no component 'w_gone' to export."),
    ],
)
def test_a_component_that_is_not_an_export_of_cells_is_refused(
    component: str, message: str
) -> None:
    with pytest.raises(ExportRefusedError) as refused:
        preview_export(spec_of(FORM), component, priced_sample(FORM), PRICED)

    assert refused.value.message == message


def test_a_priced_value_not_of_its_column_s_type_is_refused_naming_the_field() -> None:
    tables = {"pricing": [{**PRICED["pricing"][0], "premium": "lots"}]}

    with pytest.raises(ExportRefusedError) as refused:
        export(FORM, tables)

    assert (
        refused.value.message == "pricing.premium is 'lots', not 'float': price the sample again."
    )


def test_an_output_with_no_priced_row_is_left_empty() -> None:
    _, data = export(FORM, {})

    assert load_workbook(io.BytesIO(data))["Summary"]["C2"].value is None


def test_text_holding_a_character_excel_cannot_hold_is_refused_naming_the_field() -> None:
    form = copy.deepcopy(FORM)
    form["sample"]["policy"][0]["note"] = "bell\x07"

    with pytest.raises(ExportRefusedError) as refused:
        export(form)

    assert refused.value.message.startswith("policy.note holds a character Excel cannot hold")


def test_text_as_long_as_a_cell_holds_is_written_whole_and_longer_is_refused() -> None:
    # openpyxl would cut the longer text to the limit without a word.
    form = with_cells(cell("policy.note", "Slip", "A1"))
    form["sample"]["policy"][0]["note"] = "x" * CELL_TEXT_LIMIT
    _, data = export(form)
    assert load_workbook(io.BytesIO(data))["Slip"]["A1"].value == "x" * CELL_TEXT_LIMIT

    form["sample"]["policy"][0]["note"] = "x" * (CELL_TEXT_LIMIT + 1)
    with pytest.raises(ExportRefusedError) as refused:
        export(form)
    assert refused.value.message == (
        "policy.note is 32,768 characters long; an Excel cell holds at most 32,767: "
        "shorten it in the sample."
    )


def test_a_label_longer_than_a_cell_holds_is_refused_naming_it() -> None:
    form = with_cells(cell("policy.note", "Slip", "B3", "A3"))
    form["schema"]["tables"][1]["inputs"][2]["label"] = "x" * (CELL_TEXT_LIMIT + 1)

    with pytest.raises(ExportRefusedError) as refused:
        export(form)

    assert refused.value.message.startswith("The label of policy.note is 32,768 characters long")


def test_the_file_name_writes_each_character_a_file_name_cannot_hold_as_an_underscore() -> None:
    form = copy.deepcopy(FORM)
    form["sample"]["quote_details"][0]["insured"] = 'Acme/Ltd: "UK" <a|b>?*\\'

    file_name, _ = export(form)

    assert file_name == "Acme_Ltd_ _UK_ _a_b____ PREVIEW-1.xlsx"


#: A whole number past what JSON holds exactly, which the wire carries as text.
HUGE = 2**53 + 1


def test_a_whole_number_past_what_json_holds_exactly_is_written_whole_as_its_digits() -> None:
    form = with_cells(cell("policy.vehicles", "Slip", "A1"), cell("pricing.band", "Slip", "A2"))
    form["sample"]["policy"][0]["vehicles"] = str(HUGE)
    tables = {"pricing": [{**PRICED["pricing"][0], "band": str(HUGE)}]}

    # The sample priced, as the wire carries it, holds the number as text.
    assert priced_sample(form)["policy"]["vehicles"] == str(HUGE)
    _, data = export(form, tables)

    # Excel's numbers are doubles, which cannot hold it: its digits are written as text.
    slip = load_workbook(io.BytesIO(data))["Slip"]
    assert [(slip[at].value, slip[at].data_type) for at in ("A1", "A2")] == [(str(HUGE), "s")] * 2


def test_a_file_name_says_each_detail_as_text() -> None:
    details = {
        "is_renewal": True,
        "lapsed": False,
        "inception_date": datetime.date(2026, 11, 12),
        "quote_option": 2,
        "share": 0.5,
        "limit": 3.0,
        "broker": None,
    }

    named = export_file_name(
        "{is_renewal}-{lapsed}-{inception_date}-{quote_option}-{share}-{limit}-{broker}", details
    )

    assert named == "true-false-2026-11-12-2-0.5-3-.xlsx"


def test_a_file_name_blank_for_the_quote_is_refused() -> None:
    form = with_cells(SLIP_B3, cell("policy.note", "Slip", "B4"), file_name="{broker}")

    with pytest.raises(ExportRefusedError) as refused:
        export(form)

    assert refused.value.message == (
        'The file name "{broker}" is blank for this quote: fill in what it names, or give it '
        "text of its own."
    )


# ---------------------------------------------------------------------------
# The route
# ---------------------------------------------------------------------------


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    use_project(tmp_path)
    enable(tmp_path)
    write_form(tmp_path, FORM)
    return tmp_path


def client() -> TestClient:
    application = FastAPI()
    install_failure_answers(application)
    application.include_router(router)
    return TestClient(application)


def body(form: dict[str, Any] = FORM, **changed: Any) -> dict[str, Any]:
    return {
        "form": form,
        "component": EXPORT_ID,
        "sample": priced_sample(form),
        "tables": PRICED,
        **changed,
    }


@pytest.mark.usefixtures("project")
def test_the_route_answers_the_workbook_named_by_its_file_name_outside_ascii_too() -> None:
    form = copy.deepcopy(FORM)
    form["sample"]["quote_details"][0]["insured"] = "Café"

    response = client().post("/api/workbench/export", json=body(form))

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == XLSX_MEDIA_TYPE
    assert response.headers["content-disposition"] == (
        "attachment; filename=\"Caf_ PREVIEW-1.xlsx\"; filename*=UTF-8''Caf%C3%A9%20PREVIEW-1.xlsx"
    )
    assert load_workbook(io.BytesIO(response.content))["Slip"]["B3"].value == "Café"


@pytest.mark.usefixtures("project")
def test_the_route_answers_a_refusal_422_and_a_stale_sample_409() -> None:
    refused = client().post("/api/workbench/export", json=body(component="w_csv"))
    stale = client().post(
        "/api/workbench/export",
        json=body(sample={**priced_sample(FORM), "policy": {"note": "changed"}}),
    )

    assert refused.status_code == 422
    assert refused.json()["detail"].startswith("csv is not an Export to an Excel workbook")
    assert stale.status_code == 409
    assert stale.json()["detail"].startswith("The sample changed after it was priced")


def test_the_route_answers_404_while_the_workbench_is_not_enabled(tmp_path: Path) -> None:
    use_project(tmp_path)
    enable(tmp_path, table="")

    response = client().post("/api/workbench/export", json=body())

    assert response.status_code == 404
