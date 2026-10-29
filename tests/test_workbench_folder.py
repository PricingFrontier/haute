"""The workbench's form folder (specs/workbench): the form as the files of the pipeline
folder's ``workbench/``, each written by name with only what is set, read whole under the
form folder's lock with the revision of the bytes the read took, and refused, naming the file
and the place, when the folder cannot be read as a form.

The save route's staged writes, rollback and link refusals are the routes module's to test
(``tests/test_workbench_routes.py``); this module tests what the folder module renders, reads
and refuses."""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from haute import _workbench_folder
from haute._workbench_config import WorkbenchConfig, read_workbench_config
from haute._workbench_folder import (
    FormFolderNameError,
    folder_files,
    folder_revision,
    form_folder_lock,
    is_unresolved,
    read_folder,
    read_form_document,
    render_folder_file,
    sheet_name,
    unresolved_references,
)
from haute._workbench_folder_files import SheetFile
from haute._workbench_form import (
    IDENTITY_IDS,
    QUOTE_DETAILS_COLUMNS,
    QUOTE_DETAILS_MANAGED,
    FormSpec,
    SchemaColumn,
    SchemaTable,
    WorkbenchFormError,
    blank_form,
    with_quote_details,
)
from haute._workbench_tables import sample_quote
from tests._source_files import source_files
from tests._workbench_helpers import FORM as FOLDER
from tests._workbench_helpers import enable, form_files, form_folder, write_files, write_form

#: The golden folder: the test project's form as the folder module writes it.
GOLDEN = Path(__file__).parent / "fixtures" / "workbench_folder"


def column(name: str, type_: str = "str", **rest: Any) -> dict[str, Any]:
    return {"id": f"c_{name}", "name": name, "type": type_, **rest}


def place(x: int, y: int, w: int, h: int) -> dict[str, int]:
    return {"x": x, "y": y, "w": w, "h": h}


def lines(*text: str) -> str:
    """A file's text, a line each, ending in a newline."""
    return "\n".join(text) + "\n"


# The test project: Haute's quote_details given a label, a rule and, on a column the app sets,
# a number format; a one-row input table, a many-row one with an index column, another named
# in capitals whose column gives it a row per value, and a one-row output table; two sheets,
# the second shown while a one-row condition holds, holding Collections, Tables, a Chart, a
# Heading, an Import and an Export, every kind of condition and column looks; the sheets'
# colour set; and a sample, an empty row first in each many-row table.
QUOTE_DETAILS = {
    "id": "quote_details",
    "name": "quote_details",
    "role": "input",
    "rows": "one",
    "columns": [
        {
            "id": "qd_option",
            "name": "quote_option",
            "type": "int",
            "number_format": "number",
            "decimal_places": 0,
        },
        {
            "id": "qd_insured",
            "name": "insured",
            "type": "str",
            "label": "Insured name",
            "required": True,
        },
    ],
}
POLICY = {
    "id": "t_policy",
    "name": "policy",
    "role": "input",
    "rows": "one",
    "columns": [
        column("state", options=["CA", "NY"], required=True, label="State", default_value="CA"),
        column(
            "exposure",
            "float",
            min=0.0,
            group="Cover",
            help="Sum insured",
            placeholder="e.g. 250,000",
            number_format="currency_gbp",
            decimal_places=0,
        ),
        column("renewal", "bool"),
    ],
}
LAYERS = {
    "id": "t_layers",
    "name": "layers",
    "role": "input",
    "rows": "many",
    "columns": [
        column("layer", "int", key=True, index=True),
        column("limit", "float"),
        column("layer_name"),
    ],
}
PERILS = {
    "id": "t_perils",
    "name": "BR_Perils",
    "role": "input",
    "rows": "many",
    "columns": [
        column("peril", options=["Fire", "Flood"], row_per_value=True, key=True),
        column("rate", "float"),
    ],
}
PRICING = {
    "id": "t_pricing",
    "name": "pricing",
    "role": "output",
    "rows": "one",
    "columns": [
        column("premium", "float", label="Premium", number_format="currency_gbp", decimal_places=2)
    ],
}

STATE = {"table": "t_policy", "column": "c_state"}
EXPOSURE = {"table": "t_policy", "column": "c_exposure"}
PREMIUM = {"table": "t_pricing", "column": "c_premium"}
LAYER = {"table": "t_layers", "column": "c_layer"}
LIMIT = {"table": "t_layers", "column": "c_limit"}
LAYER_NAME = {"table": "t_layers", "column": "c_layer_name"}
PERIL = {"table": "t_perils", "column": "c_peril"}
RATE = {"table": "t_perils", "column": "c_rate"}

COVER = {
    "id": "section_cv01",
    "title": "Cover",
    "height": 400,
    "widgets": [
        {
            "id": "collection_k3f9",
            "type": "collection",
            **place(16, 16, 720, 120),
            "title": "Policy",
            "fields": [STATE, EXPOSURE, PREMIUM],
            "columns": 2,
            "labels": "beside",
            "spacers": [1],
            # The state's label coloured and bold while a layer has a limit: a count.
            "column_looks": [
                {
                    **STATE,
                    "header_color": "#2563EB",
                    "bold": True,
                    "show_when": {
                        "match": "all",
                        "conditions": [
                            {
                                "table": "t_layers",
                                "where": [{"column": "c_limit", "op": "filled"}],
                                "op": "at_least",
                                "count": 1,
                            }
                        ],
                    },
                }
            ],
        },
        {
            "id": "table_l4y5",
            "type": "tableInput",
            **place(16, 152, 720, 200),
            "title": "Layers",
            "fields": [LAYER, LIMIT, LAYER_NAME],
            "rows": 5,
            # Shown while any layer's limit is above 0: a condition on rows.
            "show_when": {
                "match": "any",
                "conditions": [
                    {
                        "table": "t_layers",
                        "rows": "any",
                        "where": [{"column": "c_limit", "op": "above", "values": ["0"]}],
                    }
                ],
            },
            "column_looks": [{**LIMIT, "width": 120}],
        },
        {
            "id": "chart_c6h7",
            "type": "chart",
            **place(760, 16, 480, 280),
            "title": "Limits",
            "fields": [LIMIT],
            "category": LAYER_NAME,
            "chart_type": "line",
        },
    ],
}
PERILS_SECTION = {
    "id": "section_pr01",
    "title": "Perils",
    "height": 240,
    # Shown while at least one of a group holds: a one-row condition, or a total.
    "show_when": {
        "match": 1,
        "conditions": [
            {
                "match": "any",
                "conditions": [
                    {**STATE, "op": "one_of", "values": ["CA", "NY"]},
                    {
                        "table": "t_perils",
                        "total": "sum",
                        "column": "c_rate",
                        "where": [],
                        "op": "below",
                        "value": "10",
                    },
                ],
            }
        ],
    },
    "widgets": [
        {
            "id": "heading_h8d9",
            "type": "heading",
            **place(16, 8, 480, 40),
            # Typed outside ASCII.
            "text": "Équipement",
            "description": "By peril",
            "size": "large",
        },
        {
            "id": "table_p2r3",
            "type": "tableInput",
            **place(16, 56, 720, 160),
            "title": "Perils",
            "fields": [PERIL, RATE],
        },
        {
            "id": "import_i1m2",
            "type": "import",
            **place(760, 56, 160, 32),
            "label": "Import perils",
            "source": {
                "format": "sql",
                "connection": "rating_db",
                "query": "select * from perils where state = :state",
                "parameters": [{"name": "state", **STATE}],
            },
            "table": "t_perils",
            "mode": "update",
            "column_names": [{"column": "c_rate", "name": "Rate %"}],
        },
        {
            "id": "export_e3x4",
            "type": "export",
            **place(760, 104, 160, 32),
            "label": "Export",
            "target": {
                "format": "sql",
                "connection": "warehouse",
                "table_names": [{"table": "t_layers", "name": "quote_layers"}],
            },
            "tables": ["t_layers", "t_pricing"],
        },
    ],
}
FORM: dict[str, Any] = {
    "version": 1,
    "name": "Contractors' equipment",
    "theme": {"sheet_color": "#1E293B"},
    "schema": {"tables": [QUOTE_DETAILS, POLICY, LAYERS, PERILS, PRICING]},
    "pages": [
        {
            "id": "sheet_cv01",
            "title": "BR Coverages",
            "width": 1280,
            "sections": [COVER, PERILS_SECTION],
        },
        {
            "id": "sheet_qs01",
            "title": "Quote & Summary!",
            "width": 960,
            "show_when": {
                "match": "all",
                "conditions": [{**STATE, "op": "equals", "values": ["NY"]}],
            },
            "sections": [
                {
                    "id": "section_sm01",
                    "title": "Summary",
                    "height": 160,
                    "widgets": [
                        {
                            "id": "collection_s5m6",
                            "type": "collection",
                            **place(16, 16, 400, 96),
                            "fields": [PREMIUM],
                        }
                    ],
                }
            ],
        },
    ],
    "sample": {
        "t_policy": [{"c_state": "NY", "c_exposure": "250,000", "c_renewal": True}],
        # Each typed in another order than the table's columns.
        "t_layers": [{}, {"c_layer_name": "Primary", "c_limit": "1000000"}],
        "t_perils": [{}, {"c_rate": "2.5"}],
    },
}

#: Every file of the test project's folder.
FORM_FILES = [
    "form.json",
    "sample/br_perils.json",
    "sample/layers.json",
    "sample/policy.json",
    "schema/br_perils.json",
    "schema/layers.json",
    "schema/policy.json",
    "schema/pricing.json",
    "schema/quote_details.json",
    "sheets/br_coverages.json",
    "sheets/br_coverages.layout.json",
    "sheets/quote_summary.json",
    "sheets/quote_summary.layout.json",
]


# A form whose every setting is at its default: a table and a column given only what says
# what they are, and a component of each kind given nothing else but the settings that have
# no default.
MINIMAL: dict[str, Any] = {
    "name": "m",
    "schema": {
        "tables": [
            {"id": "t_a", "name": "a", "columns": [{"id": "c_x", "name": "x"}]},
            {"id": "t_b", "name": "b", "role": "output", "columns": [{"id": "c_y", "name": "y"}]},
        ]
    },
    "pages": [
        {
            "id": "sheet_m001",
            "title": "Sheet 1",
            "width": 1280,
            "sections": [
                {
                    "id": "section_m001",
                    "title": "Section 1",
                    "height": 560,
                    "widgets": [
                        {"id": "table_m001", "type": "tableInput", **place(0, 0, 100, 100)},
                        {"id": "collection_m001", "type": "collection", **place(100, 0, 100, 100)},
                        {"id": "chart_m001", "type": "chart", **place(200, 0, 100, 100)},
                        {"id": "heading_m001", "type": "heading", **place(300, 0, 100, 100)},
                        {
                            "id": "import_m001",
                            "type": "import",
                            **place(0, 100, 100, 40),
                            "label": "",
                            "source": {"format": "csv", "delimiter": "auto"},
                            "table": None,
                            "mode": "replace",
                            "column_names": [],
                        },
                        {
                            "id": "export_m001",
                            "type": "export",
                            **place(100, 100, 100, 40),
                            "label": "",
                            "target": {
                                "format": "excel",
                                "file_name": "",
                                "headers": "names",
                                "values": "raw",
                            },
                            "tables": [],
                        },
                    ],
                }
            ],
        }
    ],
}

#: The minimal form's files, as the folder format says to write them.
MINIMAL_FILES = {
    "form.json": lines(
        "{",
        '  "version": 1,',
        '  "name": "m",',
        '  "schema": [',
        '    "quote_details",',
        '    "a",',
        '    "b"',
        "  ],",
        '  "sheets": [',
        '    "sheet_1"',
        "  ]",
        "}",
    ),
    "schema/quote_details.json": lines("{", '  "columns": []', "}"),
    "schema/a.json": lines(
        "{",
        '  "id": "t_a",',
        '  "role": "input",',
        '  "rows": "one",',
        '  "columns": [',
        "    {",
        '      "id": "c_x",',
        '      "name": "x",',
        '      "type": "str"',
        "    }",
        "  ]",
        "}",
    ),
    "schema/b.json": lines(
        "{",
        '  "id": "t_b",',
        '  "role": "output",',
        '  "rows": "one",',
        '  "columns": [',
        "    {",
        '      "id": "c_y",',
        '      "name": "y",',
        '      "type": "str"',
        "    }",
        "  ]",
        "}",
    ),
    "sheets/sheet_1.json": lines(
        "{",
        '  "id": "sheet_m001",',
        '  "title": "Sheet 1",',
        '  "sections": [',
        "    {",
        '      "id": "section_m001",',
        '      "title": "Section 1",',
        '      "components": [',
        "        {",
        '          "id": "table_m001",',
        '          "type": "table"',
        "        },",
        "        {",
        '          "id": "collection_m001",',
        '          "type": "collection"',
        "        },",
        "        {",
        '          "id": "chart_m001",',
        '          "type": "chart"',
        "        },",
        "        {",
        '          "id": "heading_m001",',
        '          "type": "heading"',
        "        },",
        "        {",
        '          "id": "import_m001",',
        '          "type": "import",',
        '          "label": "",',
        '          "source": {',
        '            "format": "csv",',
        '            "delimiter": "auto"',
        "          },",
        '          "table": null,',
        '          "mode": "replace",',
        '          "column_names": []',
        "        },",
        "        {",
        '          "id": "export_m001",',
        '          "type": "export",',
        '          "label": "",',
        '          "target": {',
        '            "file_name": "",',
        '            "headers": "names",',
        '            "values": "raw",',
        '            "format": "excel"',
        "          },",
        '          "tables": []',
        "        }",
        "      ]",
        "    }",
        "  ]",
        "}",
    ),
    "sheets/sheet_1.layout.json": lines(
        "{",
        '  "width": 1280,',
        '  "sections": {',
        '    "section_m001": {',
        '      "height": 560,',
        '      "components": {',
        '        "table_m001": {"x": 0, "y": 0, "w": 100, "h": 100},',
        '        "collection_m001": {"x": 100, "y": 0, "w": 100, "h": 100},',
        '        "chart_m001": {"x": 200, "y": 0, "w": 100, "h": 100},',
        '        "heading_m001": {"x": 300, "y": 0, "w": 100, "h": 100},',
        '        "import_m001": {"x": 0, "y": 100, "w": 100, "h": 40},',
        '        "export_m001": {"x": 100, "y": 100, "w": 100, "h": 40}',
        "      }",
        "    }",
        "  }",
        "}",
    ),
}


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """A project whose workbench is enabled, its pipeline in ``rating/``, so its form folder
    is ``rating/workbench``, which nothing has written yet."""
    root = tmp_path / "p"
    enable(root)
    return root


def config(project: Path) -> WorkbenchConfig:
    return read_workbench_config(project)


def spec_of(form: dict[str, Any]) -> FormSpec:
    """*form* as the editor holds it: checked, Haute's quote_details and the quote's identity
    in it."""
    return with_quote_details(FormSpec.model_validate(form))


def files_of(form: dict[str, Any] | FormSpec, project: Path) -> dict[str, bytes]:
    return form_files(form, form_folder(project))


def on_disk(folder: Path) -> dict[str, bytes]:
    return {
        path.relative_to(folder).as_posix(): path.read_bytes()
        for path in source_files(folder, suffix=None)
    }


def put(folder: Path, files: dict[str, bytes]) -> None:
    """*folder* holding exactly *files*, as a save leaves it."""
    shutil.rmtree(folder, ignore_errors=True)
    write_files(folder, files)


def changes(before: dict[str, bytes], after: dict[str, bytes]) -> tuple[list[str], list[str]]:
    """The files a save from *before* to *after* writes, and those it deletes."""
    written = sorted(path for path, data in after.items() if before.get(path) != data)
    deleted = sorted(path for path in before if path not in after)
    return written, deleted


def text(files: dict[str, bytes], path: str) -> str:
    return files[path].decode("utf-8")


def replace_in(path: Path, old: str, new: str) -> None:
    """A hand edit of *path*: *old*, which it holds once, made *new*."""
    content = path.read_bytes().decode("utf-8")
    assert content.count(old) == 1, f"{path.name} holds {old!r} {content.count(old)} times"
    path.write_bytes(content.replace(old, new).encode("utf-8"))


def grow(path: Path) -> None:
    """A writer that takes no lock adds a byte to *path*, a space after its JSON."""
    path.write_bytes(path.read_bytes() + b" ")


def refusal(project: Path) -> WorkbenchFormError:
    with pytest.raises(WorkbenchFormError) as caught:
        read_form_document(config(project))
    return caught.value


def table_of(form: dict[str, Any], table_id: str) -> dict[str, Any]:
    return next(table for table in form["schema"]["tables"] if table["id"] == table_id)


def column_of(form: dict[str, Any], table_id: str, column_id: str) -> dict[str, Any]:
    return next(item for item in table_of(form, table_id)["columns"] if item["id"] == column_id)


def widget_of(form: dict[str, Any], widget_id: str) -> dict[str, Any]:
    return next(
        widget
        for page in form["pages"]
        for section in page["sections"]
        for widget in section["widgets"]
        if widget["id"] == widget_id
    )


def component(spec: FormSpec, component_id: str) -> Any:
    return next(
        widget
        for page in spec.pages
        for section in page.sections
        for widget in section.widgets
        if widget.id == component_id
    )


def ref(thing: Any) -> tuple[str, str]:
    return thing.table, thing.column


def renamed(form: dict[str, Any], table_id: str, name: str, column_id: str = "") -> dict[str, Any]:
    """*form* with a table, or a column of it, renamed as the editor renames it: the name
    changes, and every reference, held by id, follows."""
    form = copy.deepcopy(form)
    target = column_of(form, table_id, column_id) if column_id else table_of(form, table_id)
    target["name"] = name
    return form


def set_at(data: Any, path: tuple[Any, ...], value: Any) -> None:
    for key in path[:-1]:
        data = data[key]
    data[path[-1]] = value


# -- Writing -----------------------------------------------------------------------------


def test_a_form_round_trips_through_its_folder_with_the_folder_s_revision(project: Path) -> None:
    files = write_form(project, FORM)

    document = read_form_document(config(project))

    assert document.spec.model_dump(mode="json") == spec_of(FORM).model_dump(mode="json")
    assert document.spec == spec_of(FORM)
    assert document.revision == folder_revision(files)
    assert document.repairs == ()
    # Saved again as it was read, it is the same files.
    assert form_files(document.spec, form_folder(project)) == files


def test_the_blank_form_round_trips_through_its_folder(project: Path) -> None:
    spec = blank_form("motor")

    files = write_form(project, spec)

    # One sheet holding one section and Haute's quote_details alone: no sample.
    assert sorted(files) == [
        "form.json",
        "schema/quote_details.json",
        "sheets/sheet_1.json",
        "sheets/sheet_1.layout.json",
    ]
    assert read_form_document(config(project)).spec == spec


def test_the_folder_holds_its_index_a_file_per_table_two_per_sheet_and_a_sample_per_table_with_rows(
    project: Path,
) -> None:
    files = files_of(FORM, project)

    # quote_details' sample holds no rows and an output table has no sample, so neither has a
    # file; a table's files are its name in lower case and a sheet's its title made a name.
    assert sorted(files) == FORM_FILES
    index = json.loads(files["form.json"])
    # The index keeps the tables' names as spelt, quote_details first, and the sheets in their
    # tabs' order.
    assert index == {
        "version": 1,
        "name": "Contractors' equipment",
        "theme": {"sheet_color": "#1E293B"},
        "schema": ["quote_details", "policy", "layers", "BR_Perils", "pricing"],
        "sheets": ["br_coverages", "quote_summary"],
    }
    assert list(index) == ["version", "name", "theme", "schema", "sheets"]
    # A table whose sample holds no rows at all has no file.
    emptied = copy.deepcopy(FORM)
    emptied["sample"]["t_layers"] = []
    assert sorted(files_of(emptied, project)) == [
        path for path in FORM_FILES if path != "sample/layers.json"
    ]


def test_a_file_holds_the_keys_that_say_what_a_thing_is_and_what_is_set_and_nothing_else(
    project: Path,
) -> None:
    files = files_of(MINIMAL, project)

    # Every setting at its default is left out; the keys that say what a thing is are written
    # at their defaults too (a role of input, one row, a column of text, every layout number
    # down to 0), and so is a setting with no default (an Import's label, source, table and
    # column names, an Export's label, target and tables), empty or null as it is.
    assert {path: data.decode("utf-8") for path, data in files.items()} == MINIMAL_FILES


def test_the_keys_that_say_what_a_thing_is_are_written_at_their_default_value_too(
    project: Path,
) -> None:
    files = files_of(FORM, project)

    # FormSpec's own defaults, which a file written by hand could leave out but may not.
    assert SchemaTable.model_fields["role"].default == "input"
    assert SchemaTable.model_fields["rows"].default == "one"
    assert SchemaColumn.model_fields["type"].default == "str"
    assert FormSpec.model_fields["version"].default == 1
    policy = json.loads(files["schema/policy.json"])
    assert (policy["role"], policy["rows"], policy["columns"][0]["type"]) == ("input", "one", "str")
    assert json.loads(files["form.json"])["version"] == 1


def test_each_file_is_utf8_json_with_lf_line_ends_two_space_indents_and_one_item_a_line(
    project: Path,
) -> None:
    files = files_of(FORM, project)

    for path, data in files.items():
        assert data.endswith(b"\n"), path
        assert not data.endswith(b"\n\n"), path
        assert b"\r" not in data, path
        content = data.decode("utf-8")
        json.loads(content)
        for line in content.splitlines():
            assert (len(line) - len(line.lstrip(" "))) % 2 == 0, (path, line)
    # Text as typed, outside ASCII too.
    sheet = text(files, "sheets/br_coverages.json")
    assert '"text": "Équipement"'.encode() in files["sheets/br_coverages.json"]
    assert "\\u00c9" not in sheet
    # A list of numbers on one line, a list of text one item a line.
    assert '\n          "spacers": [1]\n' in sheet
    assert (
        '"values": [\n                  "CA",\n                  "NY"\n                ]' in sheet
    )
    assert '"options": [\n        "CA",\n        "NY"\n      ],' in text(
        files, "schema/policy.json"
    )
    # Every field a line of its own.
    assert (
        '"fields": [\n            "policy.state",\n            "policy.exposure",\n'
        '            "pricing.premium"\n          ],'
    ) in sheet
    # A layout entry on one line.
    assert '\n        "table_l4y5": {"x": 16, "y": 152, "w": 720, "h": 200},\n' in text(
        files, "sheets/br_coverages.layout.json"
    )


def test_the_identity_columns_and_quote_details_structure_are_never_written(
    project: Path,
) -> None:
    files = write_form(project, FORM)

    for path in ("schema/policy.json", "schema/layers.json", "schema/br_perils.json"):
        written = json.loads(files[path])["columns"]
        assert not {item["id"] for item in written} & IDENTITY_IDS, path
        assert not {item["name"] for item in written} & {"quote_reference", "quote_option"}
    assert not {item["id"] for item in json.loads(files["schema/pricing.json"])["columns"]} & (
        IDENTITY_IDS
    )
    # quote_details' file holds only what the project gives its columns, by name, in Haute's
    # order: never its id, role, rows, or a column's id or type.
    assert json.loads(files["schema/quote_details.json"]) == {
        "columns": [
            {"name": "quote_option", "number_format": "number", "decimal_places": 0},
            {"name": "insured", "label": "Insured name", "required": True},
        ]
    }
    # Read, the form holds them all, as Haute gives them.
    spec = read_form_document(config(project)).spec
    details, policy = spec.data_schema.tables[:2]
    assert [(item.id, item.name, item.type) for item in details.columns] == list(
        QUOTE_DETAILS_COLUMNS
    )
    assert [(item.id, item.name, item.type) for item in policy.columns[:2]] == [
        ("quote_reference", "quote_reference", "str"),
        ("quote_option", "quote_option", "int"),
    ]


def test_a_managed_quote_details_column_keeps_its_number_format(project: Path) -> None:
    # quote_option is the underwriter app's to set, and takes no rule; how it shows is the
    # project's.
    assert "qd_option" in QUOTE_DETAILS_MANAGED
    write_form(project, FORM)

    spec = read_form_document(config(project)).spec

    option = next(item for item in spec.data_schema.tables[0].columns if item.id == "qd_option")
    assert (option.number_format, option.decimal_places) == ("number", 0)


def test_an_output_column_s_file_holds_no_rule(project: Path) -> None:
    files = files_of(FORM, project)

    rules = {"required", "min", "max", "options", "options_source", "row_per_value"}
    for written in json.loads(files["schema/pricing.json"])["columns"]:
        assert not set(written) & (rules | {"default_value"})
    # A form whose output column holds a rule is one the folder cannot hold: refused naming
    # the column and the rule, rather than the rule dropped.
    ruled = copy.deepcopy(FORM)
    column_of(ruled, "t_pricing", "c_premium").update(required=True, min=0.0)
    with pytest.raises(FormFolderNameError) as caught:
        files_of(ruled, project)
    assert len(caught.value.problems) == 2
    assert all("pricing.premium" in problem for problem in caught.value.problems)
    assert "required" in caught.value.problems[0]
    assert "min" in caught.value.problems[1]


def test_files_name_tables_and_columns_by_name(project: Path) -> None:
    files = files_of(FORM, project)

    sheet = json.loads(files["sheets/br_coverages.json"])
    collection, table, chart = sheet["sections"][0]["components"]
    _heading, _perils, importer, exporter = sheet["sections"][1]["components"]
    # A column as table.column in a field, a Chart's category, a column look, a one-row
    # condition and a query parameter.
    assert collection["fields"] == ["policy.state", "policy.exposure", "pricing.premium"]
    assert chart["category"] == "layers.layer_name"
    assert table["column_looks"] == [{"column": "layers.limit", "width": 120}]
    assert sheet["sections"][1]["show_when"]["conditions"][0]["conditions"][0] == {
        "column": "policy.state",
        "op": "one_of",
        "values": ["CA", "NY"],
    }
    assert importer["source"]["parameters"] == [{"name": "state", "column": "policy.state"}]
    # Its bare name where its table is said: a rows condition's, a count's and a total's
    # tests and a total's column, and an Import's column names.
    assert table["show_when"]["conditions"][0] == {
        "table": "layers",
        "rows": "any",
        "where": [{"column": "limit", "op": "above", "values": ["0"]}],
    }
    assert collection["column_looks"][0]["show_when"]["conditions"][0] == {
        "table": "layers",
        "where": [{"column": "limit", "op": "filled"}],
        "op": "at_least",
        "count": 1,
    }
    assert sheet["sections"][1]["show_when"]["conditions"][0]["conditions"][1] == {
        "table": "BR_Perils",
        "total": "sum",
        "column": "rate",
        "where": [],
        "op": "below",
        "value": "10",
    }
    assert (importer["table"], importer["column_names"]) == (
        "BR_Perils",
        [{"column": "rate", "name": "Rate %"}],
    )
    # An Export names its tables by name, a database's table names too.
    assert exporter["tables"] == ["layers", "pricing"]
    assert exporter["target"]["table_names"] == [{"table": "layers", "name": "quote_layers"}]
    # No id of a table or column is written in a sheet.
    for identifier in ("t_policy", "t_layers", "t_perils", "t_pricing", "c_state", "c_limit"):
        assert identifier not in text(files, "sheets/br_coverages.json")
        assert identifier not in text(files, "sheets/quote_summary.json")


# -- The revision ------------------------------------------------------------------------


def test_the_same_files_read_as_the_same_revision(project: Path) -> None:
    files = write_form(project, FORM)
    revision = read_form_document(config(project)).revision

    write_files(form_folder(project), files)

    # A save that writes the same files has the same revision, which is the files' own.
    assert read_form_document(config(project)).revision == revision == folder_revision(files)
    assert read_folder(config(project)).revision == revision
    assert read_folder(config(project)).files == files


def _hand_edit(folder: Path) -> None:
    replace_in(
        folder / "schema/layers.json",
        '"name": "limit",\n',
        '"name": "limit",\n      "label": "Limit",\n',
    )


def _whitespace(folder: Path) -> None:
    replace_in(folder / "form.json", '"version": 1,', '"version":  1,')


def _added_file(folder: Path) -> None:
    write_files(folder, {"sample/quote_details.json": b'[\n  {\n    "insured": "Acme"\n  }\n]\n'})


def _deleted_file(folder: Path) -> None:
    (folder / "sample" / "policy.json").unlink()


@pytest.mark.parametrize(
    "change",
    [
        pytest.param(_hand_edit, id="a hand edit"),
        pytest.param(_whitespace, id="a whitespace change"),
        pytest.param(_added_file, id="an added file"),
        pytest.param(_deleted_file, id="a deleted file"),
    ],
)
def test_a_changed_added_or_deleted_file_reads_as_another_revision(
    project: Path, change: Callable[[Path], None]
) -> None:
    files = write_form(project, FORM)
    folder = form_folder(project)

    change(folder)

    document = read_form_document(config(project))
    assert document.revision != folder_revision(files)
    # The revision is the hash of every file's path and bytes as the read took them.
    assert document.revision == folder_revision(on_disk(folder))


def test_a_whitespace_change_reads_as_the_same_form_at_another_revision(project: Path) -> None:
    files = write_form(project, FORM)

    _whitespace(form_folder(project))

    document = read_form_document(config(project))
    assert document.spec == spec_of(FORM)
    assert document.revision != folder_revision(files)


def test_a_file_the_index_does_not_name_counts_in_the_revision(project: Path) -> None:
    files = write_form(project, FORM)
    folder = form_folder(project)

    write_files(folder, {"notes.md": b"Rates from the 2026 filing.\n"})

    assert read_folder(config(project)).revision == folder_revision(on_disk(folder))
    assert read_folder(config(project)).revision != folder_revision(files)


def test_the_revision_hashes_each_file_s_path_and_bytes_in_any_order() -> None:
    files = {"form.json": b"{}\n", "schema/a.json": b"[]\n"}

    assert folder_revision(files) == folder_revision(dict(reversed(list(files.items()))))
    assert folder_revision(files) != folder_revision(
        {"form.json": b"{}\n", "schema/b.json": b"[]\n"}
    )
    assert folder_revision(files) != folder_revision(
        {"form.json": b"{} \n", "schema/a.json": b"[]\n"}
    )
    assert folder_revision(files) != folder_revision({"form.json": b"{}\n"})


# -- What a save writes ------------------------------------------------------------------


def test_a_label_changed_writes_one_file_and_a_component_moved_one_layout_file(
    project: Path,
) -> None:
    before = files_of(FORM, project)

    relabelled = copy.deepcopy(FORM)
    column_of(relabelled, "t_layers", "c_limit")["label"] = "Limit"
    moved = copy.deepcopy(FORM)
    widget_of(moved, "chart_c6h7").update(x=744, y=24)
    widget_of(moved, "collection_k3f9").update(h=128)

    assert changes(before, files_of(relabelled, project)) == (["schema/layers.json"], [])
    assert changes(before, files_of(moved, project)) == (["sheets/br_coverages.layout.json"], [])


def test_a_column_renamed_changes_its_name_every_reference_and_its_sample_key_and_keeps_its_id(
    project: Path,
) -> None:
    before = files_of(FORM, project)
    region = renamed(FORM, "t_policy", "region", "c_state")

    after = files_of(region, project)

    assert changes(before, after) == (
        [
            "sample/policy.json",
            "schema/policy.json",
            "sheets/br_coverages.json",
            "sheets/quote_summary.json",
        ],
        [],
    )
    # The id stays beside the new name.
    assert text(after, "schema/policy.json") == text(before, "schema/policy.json").replace(
        '"id": "c_state",\n      "name": "state",', '"id": "c_state",\n      "name": "region",'
    )
    for sheet in ("sheets/br_coverages.json", "sheets/quote_summary.json"):
        assert text(after, sheet) == text(before, sheet).replace(
            '"policy.state"', '"policy.region"'
        )
    assert text(after, "sample/policy.json") == text(before, "sample/policy.json").replace(
        '"state":', '"region":'
    )
    # Read back, every reference holds the column's id still.
    put(form_folder(project), after)
    spec = read_form_document(config(project)).spec
    assert spec == spec_of(region)
    assert ref(component(spec, "collection_k3f9").fields[0]) == ("t_policy", "c_state")


def test_a_table_renamed_moves_its_schema_and_sample_files(project: Path) -> None:
    before = files_of(FORM, project)
    cover_layers = renamed(FORM, "t_layers", "cover_layers")

    after = files_of(cover_layers, project)

    assert changes(before, after) == (
        [
            "form.json",
            "sample/cover_layers.json",
            "schema/cover_layers.json",
            "sheets/br_coverages.json",
        ],
        ["sample/layers.json", "schema/layers.json"],
    )
    # The files move whole: a table's file holds its id, not its name, which is the index's.
    assert after["schema/cover_layers.json"] == before["schema/layers.json"]
    assert after["sample/cover_layers.json"] == before["sample/layers.json"]
    assert json.loads(after["form.json"])["schema"] == [
        "quote_details",
        "policy",
        "cover_layers",
        "BR_Perils",
        "pricing",
    ]
    assert text(after, "sheets/br_coverages.json") == (
        text(before, "sheets/br_coverages.json")
        .replace('"layers.', '"cover_layers.')
        .replace('"layers"', '"cover_layers"')
    )
    put(form_folder(project), after)
    assert read_form_document(config(project)).spec == spec_of(cover_layers)


def test_a_case_only_table_rename_changes_the_index_entry_and_no_file_s_name(
    project: Path,
) -> None:
    before = files_of(FORM, project)
    capitals = renamed(FORM, "t_layers", "Layers")

    after = files_of(capitals, project)

    assert sorted(after) == sorted(before)
    assert changes(before, after) == (["form.json", "sheets/br_coverages.json"], [])
    assert json.loads(after["form.json"])["schema"][2] == "Layers"
    put(form_folder(project), after)
    spec = read_form_document(config(project)).spec
    assert spec == spec_of(capitals)
    assert spec.data_schema.tables[2].name == "Layers"


def test_a_sheet_retitled_moves_its_two_files_and_its_index_entry_and_keeps_its_id(
    project: Path,
) -> None:
    before = files_of(FORM, project)
    retitled = copy.deepcopy(FORM)
    retitled["pages"][0]["title"] = "Coverages"

    after = files_of(retitled, project)

    assert changes(before, after) == (
        ["form.json", "sheets/coverages.json", "sheets/coverages.layout.json"],
        ["sheets/br_coverages.json", "sheets/br_coverages.layout.json"],
    )
    assert json.loads(after["form.json"])["sheets"] == ["coverages", "quote_summary"]
    assert after["sheets/coverages.layout.json"] == before["sheets/br_coverages.layout.json"]
    assert text(after, "sheets/coverages.json") == text(before, "sheets/br_coverages.json").replace(
        '"title": "BR Coverages"', '"title": "Coverages"'
    )
    put(form_folder(project), after)
    spec = read_form_document(config(project)).spec
    assert (spec.pages[0].id, spec.pages[0].title) == ("sheet_cv01", "Coverages")


def test_a_table_deleted_deletes_its_two_files(project: Path) -> None:
    before = files_of(FORM, project)
    # As the editor removes a table: what shows it, the conditions on it and what an Import
    # uses of it go with it, and so do its sample rows.
    deleted = copy.deepcopy(FORM)
    deleted["schema"]["tables"].remove(PERILS)
    widget_of(deleted, "table_p2r3")["fields"] = []
    widget_of(deleted, "import_i1m2").update(table=None, column_names=[])
    group = deleted["pages"][0]["sections"][1]["show_when"]["conditions"][0]
    group["conditions"] = group["conditions"][:1]
    del deleted["sample"]["t_perils"]

    after = files_of(deleted, project)

    assert changes(before, after) == (
        ["form.json", "sheets/br_coverages.json"],
        ["sample/br_perils.json", "schema/br_perils.json"],
    )
    put(form_folder(project), after)
    assert read_form_document(config(project)).spec == spec_of(deleted)


def test_a_column_deleted_and_made_again_under_its_name_takes_a_new_id(project: Path) -> None:
    write_form(project, FORM)
    # As the editor removes a column: what shows it and its sample values go with it. Made
    # again under the same name, the column is a new one with an id of its own.
    remade = copy.deepcopy(FORM)
    layers = table_of(remade, "t_layers")
    layers["columns"] = [item for item in layers["columns"] if item["id"] != "c_layer_name"]
    layers["columns"].append({**column("layer_name"), "id": "c_layer_name_2"})
    widget_of(remade, "table_l4y5")["fields"] = [LAYER, LIMIT]
    widget_of(remade, "chart_c6h7")["category"] = None
    del remade["sample"]["t_layers"][1]["c_layer_name"]

    after = files_of(remade, project)

    written = json.loads(after["schema/layers.json"])["columns"]
    assert [(item["id"], item["name"]) for item in written] == [
        ("c_layer", "layer"),
        ("c_limit", "limit"),
        ("c_layer_name_2", "layer_name"),
    ]
    put(form_folder(project), after)
    # The name written by hand again, as a Chart's category, binds the new column, never
    # the old id.
    replace_in(
        form_folder(project) / "sheets/br_coverages.json",
        '"chart_type": "line"\n',
        '"category": "layers.layer_name",\n          "chart_type": "line"\n',
    )
    spec = read_form_document(config(project)).spec
    assert ref(component(spec, "chart_c6h7").category) == ("t_layers", "c_layer_name_2")
    assert [item.id for item in spec.data_schema.tables[2].columns][-1] == "c_layer_name_2"


def test_a_default_written_by_hand_reads_as_that_setting_and_is_left_out_at_the_next_save(
    project: Path,
) -> None:
    files = write_form(project, FORM)
    folder = form_folder(project)

    replace_in(
        folder / "schema/layers.json",
        '"name": "limit",\n      "type": "float"\n',
        '"name": "limit",\n      "type": "float",\n      "required": false,\n'
        '      "use_grouping": true\n',
    )
    replace_in(
        folder / "sheets/br_coverages.json",
        '"chart_type": "line"\n',
        '"chart_type": "line",\n          "stacked": false\n',
    )
    replace_in(
        folder / "sheets/quote_summary.json",
        '"show_when": {\n    "conditions"',
        '"show_when": {\n    "match": "all",\n    "conditions"',
    )
    replace_in(
        folder / "form.json",
        '"sheet_color": "#1E293B"\n',
        '"sheet_color": "#1E293B",\n    "font_color": null\n',
    )

    spec = read_form_document(config(project)).spec

    assert spec == spec_of(FORM)
    assert form_files(spec, folder) == files


# -- The sample --------------------------------------------------------------------------


def test_a_sample_keeps_its_rows_places_so_an_index_and_a_preset_give_them_as_before(
    project: Path,
) -> None:
    files = write_form(project, FORM)

    # The empty row first is kept, and a row's values are written in the table's column
    # order, by name, whatever order they were typed in; a tick as a tick.
    assert text(files, "sample/layers.json") == lines(
        "[",
        "  {},",
        "  {",
        '    "limit": "1000000",',
        '    "layer_name": "Primary"',
        "  }",
        "]",
    )
    assert text(files, "sample/br_perils.json") == lines(
        "[", "  {},", "  {", '    "rate": "2.5"', "  }", "]"
    )
    assert json.loads(files["sample/policy.json"]) == [
        {"state": "NY", "exposure": "250,000", "renewal": True}
    ]
    spec = read_form_document(config(project)).spec
    assert spec.sample == spec_of(FORM).sample
    # The value after the empty row keeps its row's number in the indexed table, and its
    # row's preset value in the table a column gives a row for each value.
    priced = {
        "layers": [{"layer": 2, "limit": 1000000.0, "layer_name": "Primary"}],
        "BR_Perils": [{"peril": "Fire"}, {"peril": "Flood", "rate": 2.5}],
    }
    for quote in (sample_quote(spec_of(FORM)), sample_quote(spec)):
        assert {name: quote[name] for name in priced} == priced
    # Without the empty row, they would be the first row's.
    dropped = copy.deepcopy(FORM)
    for table_id in ("t_layers", "t_perils"):
        dropped["sample"][table_id] = dropped["sample"][table_id][1:]
    quote = sample_quote(spec_of(dropped))
    assert quote["layers"] == [{"layer": 1, "limit": 1000000.0, "layer_name": "Primary"}]
    assert quote["BR_Perils"] == [{"peril": "Fire", "rate": 2.5}, {"peril": "Flood"}]


def test_a_sample_value_under_an_unknown_column_is_kept_through_a_save_and_priced_nowhere(
    project: Path,
) -> None:
    write_form(project, FORM)
    folder = form_folder(project)
    replace_in(
        folder / "sample/policy.json",
        '"renewal": true\n',
        '"renewal": true,\n    "colour": "red"\n',
    )
    edited = (folder / "sample/policy.json").read_bytes()

    spec = read_form_document(config(project)).spec

    # Kept under an unresolved id, which no column of the schema binds to.
    assert spec.sample["t_policy"] == [
        {"c_state": "NY", "c_exposure": "250,000", "c_renewal": True, "?colour": "red"}
    ]
    assert sample_quote(spec) == sample_quote(spec_of(FORM))
    assert "colour" not in sample_quote(spec)["policy"]
    # It stops neither a build nor a deploy: nothing names it as a reference.
    assert unresolved_references(spec) == []
    # An unrelated save, a label changed, writes it back as it was.
    relabelled = spec.model_copy(deep=True)
    relabelled.data_schema.tables[2].columns[3].label = "Limit"
    assert relabelled.data_schema.tables[2].columns[3].id == "c_limit"
    after = form_files(relabelled, folder)
    assert after["sample/policy.json"] == edited
    assert changes(on_disk(folder), after) == (["schema/layers.json"], [])


def _colour_kept(project: Path) -> FormSpec:
    """The test project's form read with a value kept under ``colour``, a name the policy
    table does not hold, as a merge can leave a sample."""
    write_form(project, FORM)
    replace_in(
        form_folder(project) / "sample/policy.json",
        '"renewal": true\n',
        '"renewal": true,\n    "colour": "red"\n',
    )
    return read_form_document(config(project)).spec


def test_a_column_made_under_a_kept_sample_name_takes_its_value_at_the_next_read(
    project: Path,
) -> None:
    spec = _colour_kept(project)
    added = spec.model_copy(deep=True)
    policy = next(table for table in added.data_schema.tables if table.id == "t_policy")
    policy.columns.append(SchemaColumn(id="c_colour", name="colour", type="str"))

    put(form_folder(project), form_files(added, form_folder(project)))

    # Bound by its name, as a reference written to a name a column takes later is.
    assert read_form_document(config(project)).spec.sample["t_policy"] == [
        {"c_state": "NY", "c_exposure": "250,000", "c_renewal": True, "c_colour": "red"}
    ]


def test_a_row_that_would_hold_a_name_twice_is_refused_rather_than_one_value_lost(
    project: Path,
) -> None:
    # The column renamed to the kept name: its own value and the kept one, one name.
    spec = _colour_kept(project)
    renamed_state = spec.model_copy(deep=True)
    policy = next(table for table in renamed_state.data_schema.tables if table.id == "t_policy")
    next(column for column in policy.columns if column.id == "c_state").name = "colour"

    with pytest.raises(FormFolderNameError) as caught:
        form_files(renamed_state, form_folder(project))

    assert caught.value.problems == [
        "row 1 of sample/policy.json would hold colour twice: the column's value and one kept "
        "from when the schema had no column of that name; give the column another name, or "
        "take that value out of the file"
    ]


# -- References --------------------------------------------------------------------------


@dataclass(frozen=True)
class Reference:
    """A kind of reference in the test project's sheets: the file it is written in, what a
    hand edit writes where to name a column its known table does not hold or a table the
    schema does not hold, what the form read holds for it, and where it is."""

    file: str
    #: By case, each spot of the file a hand edit writes, and the name it writes there.
    edits: dict[str, dict[tuple[Any, ...], str]]
    held: Callable[[FormSpec], Any]
    #: By case, what the form read holds.
    expected: dict[str, Any]
    #: What names its place in the sheet: the title or label of the component or section
    #: it is in; None for the sheet's own Show when, which its file names.
    place: str | None


COVER_AT = ("sections", 0, "components")
PERILS_AT = ("sections", 1, "components")
#: The Table of layers' condition on its rows, the count in the Collection's look, and the
#: total in the Perils section's group.
ROWS_AT = (*COVER_AT, 1, "show_when", "conditions", 0)
COUNT_AT = (*COVER_AT, 0, "column_looks", 0, "show_when", "conditions", 0)
TOTAL_AT = ("sections", 1, "show_when", "conditions", 0, "conditions", 1)


def _rows(condition: Any) -> tuple[str, list[str]]:
    return condition.table, [test.column for test in condition.where]


def _import(spec: FormSpec) -> tuple[str | None, list[str]]:
    button = component(spec, "import_i1m2")
    return button.table, [named.column for named in button.column_names]


def _export(spec: FormSpec) -> tuple[list[str], list[str]]:
    button = component(spec, "export_e3x4")
    return button.tables, [named.table for named in button.target.table_names]


REFERENCES: dict[str, Reference] = {
    "a field": Reference(
        "sheets/br_coverages.json",
        {
            "unknown column": {(*COVER_AT, 0, "fields", 1): "policy.exposur"},
            "unknown table": {(*COVER_AT, 0, "fields", 1): "polcy.exposure"},
        },
        lambda spec: ref(component(spec, "collection_k3f9").fields[1]),
        {
            "known": ("t_policy", "c_exposure"),
            "unknown column": ("t_policy", "?exposur"),
            "unknown table": ("?polcy", "?exposure"),
        },
        "Policy",
    ),
    "a Chart's category": Reference(
        "sheets/br_coverages.json",
        {
            "unknown column": {(*COVER_AT, 2, "category"): "layers.layer_nam"},
            "unknown table": {(*COVER_AT, 2, "category"): "layerz.layer_name"},
        },
        lambda spec: ref(component(spec, "chart_c6h7").category),
        {
            "known": ("t_layers", "c_layer_name"),
            "unknown column": ("t_layers", "?layer_nam"),
            "unknown table": ("?layerz", "?layer_name"),
        },
        "Limits",
    ),
    "a one-row condition": Reference(
        "sheets/quote_summary.json",
        {
            "unknown column": {("show_when", "conditions", 0, "column"): "policy.stat"},
            "unknown table": {("show_when", "conditions", 0, "column"): "polcy.state"},
        },
        lambda spec: ref(spec.pages[1].show_when.conditions[0]),
        {
            "known": ("t_policy", "c_state"),
            "unknown column": ("t_policy", "?stat"),
            "unknown table": ("?polcy", "?state"),
        },
        None,
    ),
    # A look is for a column its component shows: the field and the look name it together.
    "a column look": Reference(
        "sheets/br_coverages.json",
        {
            "unknown column": {
                (*COVER_AT, 1, "fields", 1): "layers.limt",
                (*COVER_AT, 1, "column_looks", 0, "column"): "layers.limt",
            },
            "unknown table": {
                (*COVER_AT, 1, "fields", 1): "layerz.limit",
                (*COVER_AT, 1, "column_looks", 0, "column"): "layerz.limit",
            },
        },
        lambda spec: ref(component(spec, "table_l4y5").column_looks[0]),
        {
            "known": ("t_layers", "c_limit"),
            "unknown column": ("t_layers", "?limt"),
            "unknown table": ("?layerz", "?limit"),
        },
        "Layers",
    ),
    "a query parameter": Reference(
        "sheets/br_coverages.json",
        {
            "unknown column": {(*PERILS_AT, 2, "source", "parameters", 0, "column"): "policy.stat"},
            "unknown table": {(*PERILS_AT, 2, "source", "parameters", 0, "column"): "polcy.state"},
        },
        lambda spec: ref(component(spec, "import_i1m2").source.parameters[0]),
        {
            "known": ("t_policy", "c_state"),
            "unknown column": ("t_policy", "?stat"),
            "unknown table": ("?polcy", "?state"),
        },
        "Import perils",
    ),
    "a rows condition": Reference(
        "sheets/br_coverages.json",
        {
            "unknown column": {(*ROWS_AT, "where", 0, "column"): "limt"},
            "unknown table": {(*ROWS_AT, "table"): "layerz"},
        },
        lambda spec: _rows(component(spec, "table_l4y5").show_when.conditions[0]),
        {
            "known": ("t_layers", ["c_limit"]),
            "unknown column": ("t_layers", ["?limt"]),
            "unknown table": ("?layerz", ["?limit"]),
        },
        "Layers",
    ),
    "a count": Reference(
        "sheets/br_coverages.json",
        {
            "unknown column": {(*COUNT_AT, "where", 0, "column"): "limt"},
            "unknown table": {(*COUNT_AT, "table"): "layerz"},
        },
        lambda spec: _rows(
            component(spec, "collection_k3f9").column_looks[0].show_when.conditions[0]
        ),
        {
            "known": ("t_layers", ["c_limit"]),
            "unknown column": ("t_layers", ["?limt"]),
            "unknown table": ("?layerz", ["?limit"]),
        },
        "Policy",
    ),
    "a total": Reference(
        "sheets/br_coverages.json",
        {
            "unknown column": {(*TOTAL_AT, "column"): "rait"},
            "unknown table": {(*TOTAL_AT, "table"): "BR_Perilz"},
        },
        lambda spec: ref(spec.pages[0].sections[1].show_when.conditions[0].conditions[1]),
        {
            "known": ("t_perils", "c_rate"),
            "unknown column": ("t_perils", "?rait"),
            "unknown table": ("?BR_Perilz", "?rate"),
        },
        "Perils",
    ),
    "an Import's table and column names": Reference(
        "sheets/br_coverages.json",
        {
            "unknown column": {(*PERILS_AT, 2, "column_names", 0, "column"): "rait"},
            "unknown table": {(*PERILS_AT, 2, "table"): "BR_Perilz"},
        },
        _import,
        {
            "known": ("t_perils", ["c_rate"]),
            "unknown column": ("t_perils", ["?rait"]),
            "unknown table": ("?BR_Perilz", ["?rate"]),
        },
        "Import perils",
    ),
    # An Export names tables only, and a table it writes to a database is one it exports.
    "an Export's tables and table names": Reference(
        "sheets/br_coverages.json",
        {
            "unknown table": {
                (*PERILS_AT, 3, "tables", 0): "layerz",
                (*PERILS_AT, 3, "target", "table_names", 0, "table"): "layerz",
            },
        },
        _export,
        {
            "known": (["t_layers", "t_pricing"], ["t_layers"]),
            "unknown table": (["?layerz", "t_pricing"], ["?layerz"]),
        },
        "Export",
    ),
}


def _word(name: str) -> re.Pattern[str]:
    """*name* as said in a message, neither end running on into a longer word."""
    return re.compile(rf"(?<!\w){re.escape(name)}(?!\w)")


def _flat(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    else:
        for item in value:
            yield from _flat(item)


@pytest.mark.parametrize(
    ("kind", "case"),
    [
        pytest.param(kind, case, id=f"{kind}, {case}")
        for kind, reference in REFERENCES.items()
        for case in reference.expected
    ],
)
def test_each_reference_is_written_by_name_and_read_by_id_an_unknown_name_unresolved(
    project: Path, kind: str, case: str
) -> None:
    reference = REFERENCES[kind]
    files = write_form(project, FORM)
    sheet = json.loads(files[reference.file])
    for spot, name in reference.edits.get(case, {}).items():
        set_at(sheet, spot, name)
    edited = render_folder_file(SheetFile.model_validate(sheet))
    if case == "known":
        assert edited == files[reference.file]
    write_files(form_folder(project), {reference.file: edited})

    spec = read_form_document(config(project)).spec

    held = reference.held(spec)
    assert held == reference.expected[case]
    # An unresolved id is the name as written after a "?", which binds to no column.
    unknown = sorted({identifier[1:] for identifier in _flat(held) if is_unresolved(identifier)})
    assert all(
        identifier.startswith("?") == is_unresolved(identifier) for identifier in _flat(held)
    )
    assert bool(unknown) == (case != "known")
    # Written back as it was: the name as the file spelt it.
    assert form_files(spec, form_folder(project))[reference.file] == edited
    # And named, with its file and its place, for a build or a deploy to refuse; nothing
    # else is.
    found = unresolved_references(spec)
    assert bool(found) == bool(unknown)
    for name in unknown:
        assert any(
            entry.startswith(reference.file)
            and (reference.place is None or reference.place in entry)
            and _word(name).search(entry)
            for entry in found
        ), (name, found)
    for entry in found:
        assert any(_word(name).search(entry) for name in unknown), entry


def test_a_test_against_another_field_is_written_by_name_with_its_days_and_read_back(
    project: Path,
) -> None:
    form = json.loads(json.dumps(FORM))
    table_of(form, "t_policy")["columns"] += [
        {"id": "c_start", "name": "start", "type": "date"},
        {"id": "c_end", "name": "end", "type": "date"},
    ]
    table_of(form, "t_layers")["columns"].append(
        {"id": "c_retention", "name": "retention", "type": "float"}
    )
    later = {
        **{"table": "t_policy", "column": "c_end", "op": "above", "values": []},
        "field": {"table": "t_policy", "column": "c_start"},
        "days": 365,
    }
    insured = {
        **{"table": "t_policy", "column": "c_state", "op": "contains", "values": []},
        "field": {"table": "quote_details", "column": "qd_insured"},
        "days": 0,
    }
    above = {"column": "c_limit", "op": "above", "values": [], "field": "c_retention", "days": 0}
    layers = {"table": "t_layers", "rows": "any", "where": [above]}
    form["pages"][0]["show_when"] = {"match": "any", "conditions": [later, insured, layers]}

    files = write_form(project, form)

    # The field by name, as a condition's column is, and its days, each only while it has one,
    # as every setting at its default is left out.
    written = json.loads(files["sheets/br_coverages.json"])["show_when"]["conditions"]
    assert written == [
        {"column": "policy.end", "op": "above", "field": "policy.start", "days": 365},
        {"column": "policy.state", "op": "contains", "field": "quote_details.insured"},
        {
            "table": "layers",
            "rows": "any",
            "where": [{"column": "limit", "op": "above", "field": "retention"}],
        },
    ]
    assert read_form_document(config(project)).spec == spec_of(form)

    # A field the schema does not hold is unresolved, and named for a build or a deploy.
    sheet = json.loads(files["sheets/br_coverages.json"])
    sheet["show_when"]["conditions"][0]["field"] = "policy.started"
    sheet["show_when"]["conditions"][2]["where"][0]["field"] = "retained"
    write_files(
        form_folder(project),
        {"sheets/br_coverages.json": render_folder_file(SheetFile.model_validate(sheet))},
    )
    spec = read_form_document(config(project)).spec
    found = unresolved_references(spec)
    assert any(_word("policy.started").search(entry) for entry in found), found
    assert any(_word("retained").search(entry) for entry in found), found


def test_a_reference_never_binds_through_a_column_s_old_name(project: Path) -> None:
    write_form(project, FORM)
    folder = form_folder(project)

    # Renamed by hand in its schema file alone, the column keeps its id; the sheets and the
    # sample still name it state, which the schema no longer holds.
    replace_in(folder / "schema/policy.json", '"name": "state"', '"name": "region"')
    spec = read_form_document(config(project)).spec

    policy = spec.data_schema.tables[1]
    assert [item.name for item in policy.columns if item.id == "c_state"] == ["region"]
    assert ref(component(spec, "collection_k3f9").fields[0]) == ("t_policy", "?state")
    assert ref(component(spec, "collection_k3f9").column_looks[0]) == ("t_policy", "?state")
    assert ref(spec.pages[1].show_when.conditions[0]) == ("t_policy", "?state")
    assert ref(component(spec, "import_i1m2").source.parameters[0]) == ("t_policy", "?state")
    assert spec.sample["t_policy"] == [{"?state": "NY", "c_exposure": "250,000", "c_renewal": True}]
    found = unresolved_references(spec)
    assert all(_word("state").search(entry) for entry in found), found
    for file, place in [
        ("sheets/br_coverages.json", "Policy"),
        ("sheets/br_coverages.json", "Perils"),
        ("sheets/br_coverages.json", "Import perils"),
        ("sheets/quote_summary.json", None),
    ]:
        assert any(
            entry.startswith(file) and (place is None or place in entry) for entry in found
        ), (file, place, found)
    # A column later named state, made anew, resolves them at the next read.
    replace_in(
        folder / "schema/policy.json",
        '"type": "bool"\n    }\n',
        '"type": "bool"\n    },\n    {\n      "id": "c_state_2",\n      "name": "state",\n'
        '      "type": "str"\n    }\n',
    )
    spec = read_form_document(config(project)).spec
    assert ref(component(spec, "collection_k3f9").fields[0]) == ("t_policy", "c_state_2")
    assert ref(spec.pages[1].show_when.conditions[0]) == ("t_policy", "c_state_2")
    assert spec.sample["t_policy"][0]["c_state_2"] == "NY"
    assert unresolved_references(spec) == []


@pytest.mark.parametrize("given", ["table", "column"])
def test_a_form_refuses_a_table_or_column_id_starting_with_a_question_mark(given: str) -> None:
    form = copy.deepcopy(MINIMAL)
    table = form["schema"]["tables"][0]
    if given == "table":
        table["id"] = "?a"
    else:
        table["columns"][0]["id"] = "?x"
    identifier = "?a" if given == "table" else "?x"

    with pytest.raises(ValidationError, match=re.escape(f"The id '{identifier}' starts with '?'")):
        FormSpec.model_validate(form)


# -- What a read refuses -----------------------------------------------------------------


def test_a_missing_folder_is_refused_pointing_to_version_history(project: Path) -> None:
    error = refusal(project)

    # Never taken for a blank form.
    assert error.message == (
        f"{FOLDER} does not exist: restore it from version history (a project starts its "
        "workbench with haute init --workbench)"
    )


def _edit(path: str, old: str, new: str) -> Callable[[Path], None]:
    return lambda folder: replace_in(folder / path, old, new)


def _write(path: str, data: bytes) -> Callable[[Path], None]:
    return lambda folder: write_files(folder, {path: data})


def _delete(path: str) -> Callable[[Path], None]:
    return lambda folder: (folder / path).unlink()


def _rename(path: str, to: str) -> Callable[[Path], None]:
    return lambda folder: (folder / path).rename(folder / to)


def _edit_json(path: str, change: Callable[[Any], None]) -> Callable[[Path], None]:
    def apply(folder: Path) -> None:
        data = json.loads((folder / path).read_bytes())
        change(data)
        write_files(folder, {path: json.dumps(data, indent=2).encode("utf-8")})

    return apply


def _both(*edits: Callable[[Path], None]) -> Callable[[Path], None]:
    def apply(folder: Path) -> None:
        for edit in edits:
            edit(folder)

    return apply


_NOT_PART = "is not part of the form: list it in form.json or remove it"
_NO_INPUT = "is the sample of no input table: rename it after its table or remove it"
_LAYOUT = "sheets/br_coverages.layout.json"
_CHART_AT = '"chart_c6h7": {"x": 760, "y": 16, "w": 480, "h": 280}'
_OUTPUT_RULES = [
    ("required", "true"),
    ("min", "0"),
    ("max", "1"),
    ("options", '["a"]'),
    ("options_source", '{"file": "rates.csv", "column": "premium"}'),
    ("row_per_value", "true"),
    ("default_value", '"1"'),
]

READ_REFUSALS = [
    pytest.param(_delete("form.json"), f"{FOLDER} ", ("form.json",), None, id="no form.json"),
    pytest.param(
        _delete("schema/layers.json"),
        f"{FOLDER}/form.json ",
        ("schema/layers.json",),
        None,
        id="a listed table's file missing",
    ),
    pytest.param(
        _delete("sheets/quote_summary.layout.json"),
        f"{FOLDER}/form.json ",
        ("sheets/quote_summary.layout.json",),
        None,
        id="a sheet without its layout file",
    ),
    pytest.param(
        _delete("sheets/quote_summary.json"),
        f"{FOLDER}/form.json ",
        ("sheets/quote_summary.json",),
        None,
        id="a layout file without its sheet",
    ),
    pytest.param(
        _write("sheets/old.layout.json", b"{}\n"),
        f"{FOLDER}/sheets/old.layout.json {_NOT_PART}",
        (),
        None,
        id="a layout file of no sheet",
    ),
    pytest.param(
        _write("notes.md", b"Rates from the 2026 filing.\n"),
        f"{FOLDER}/notes.md {_NOT_PART}",
        (),
        None,
        id="an unlisted file",
    ),
    pytest.param(
        _write("schema/claims.json", b'{\n  "id": "t_claims"\n}\n'),
        f"{FOLDER}/schema/claims.json {_NOT_PART}",
        (),
        None,
        id="an unlisted schema file",
    ),
    pytest.param(
        _edit("form.json", '"pricing"\n', '"pricing",\n    "layers"\n'),
        f"{FOLDER}/form.json ",
        ("layers", "twice"),
        None,
        id="a table listed twice",
    ),
    pytest.param(
        _edit("form.json", '"pricing"\n', '"pricing",\n    "Layers"\n'),
        f"{FOLDER}/form.json ",
        ("twice",),
        None,
        id="a table listed twice by case",
    ),
    pytest.param(
        _edit("form.json", '"quote_summary"\n', '"quote_summary",\n    "quote_summary"\n'),
        f"{FOLDER}/form.json ",
        ("quote_summary", "twice"),
        None,
        id="a sheet listed twice",
    ),
    pytest.param(
        _both(
            _edit("form.json", '"pricing"\n', '"pricing out"\n'),
            _rename("schema/pricing.json", "schema/pricing out.json"),
        ),
        f"{FOLDER}/form.json: schema.4: ",
        (),
        ValidationError,
        id="a table name not an identifier",
    ),
    pytest.param(
        _edit("schema/layers.json", '"name": "layer_name"', '"name": "layer name"'),
        f"{FOLDER}/schema/layers.json: ",
        ("columns.2",),
        None,
        id="a column name not an identifier",
    ),
    pytest.param(
        _edit("schema/layers.json", '"name": "layer_name"', '"name": "layer.name"'),
        f"{FOLDER}/schema/layers.json: ",
        ("columns.2",),
        None,
        id="a column name holding a dot",
    ),
    pytest.param(
        _rename("schema/br_perils.json", "schema/BR_Perils.json"),
        f"{FOLDER}/schema/BR_Perils.json ",
        ("lower case",),
        None,
        id="a schema file name not in lower case",
    ),
    pytest.param(
        _rename("sample/br_perils.json", "sample/BR_Perils.json"),
        f"{FOLDER}/sample/BR_Perils.json ",
        ("lower case",),
        None,
        id="a sample file name not in lower case",
    ),
    pytest.param(
        _rename("sheets/quote_summary.json", "sheets/Quote_Summary.json"),
        f"{FOLDER}/sheets/Quote_Summary.json ",
        ("lower case",),
        None,
        id="a sheet file name not in lower case",
    ),
    pytest.param(
        _write("schema/layers.json", b'{\n  "id": "t_l\xffayers"\n}\n'),
        f"{FOLDER}/schema/layers.json is not UTF-8",
        (),
        UnicodeDecodeError,
        id="a file not UTF-8",
    ),
    pytest.param(
        _write("schema/layers.json", b'{\n  "id": "t_layers",\n'),
        f"{FOLDER}/schema/layers.json is not JSON",
        (),
        json.JSONDecodeError,
        id="a file not JSON",
    ),
    pytest.param(
        _edit("schema/layers.json", '"rows": "many",\n', '"rows": "many",\n  "rows": "many",\n'),
        f"{FOLDER}/schema/layers.json ",
        ("rows", "twice"),
        None,
        id="a key given twice",
    ),
    pytest.param(
        _edit("form.json", '"version": 1,\n', '"version": 1,\n  "title": "Motor",\n'),
        f"{FOLDER}/form.json: title: ",
        (),
        ValidationError,
        id="an unknown key in the index",
    ),
    pytest.param(
        _edit(
            "schema/layers.json", '"type": "float"\n', '"type": "float",\n      "colour": "red"\n'
        ),
        f"{FOLDER}/schema/layers.json: columns.1.colour: ",
        (),
        ValidationError,
        id="an unknown key in a schema file",
    ),
    pytest.param(
        _edit(
            "sheets/br_coverages.json",
            '"chart_type": "line"\n',
            '"chart_type": "line",\n          "colour": "red"\n',
        ),
        f"{FOLDER}/sheets/br_coverages.json: sections.0.components.2.",
        ("colour",),
        ValidationError,
        id="an unknown key in a sheet file",
    ),
    pytest.param(
        _edit(_LAYOUT, '"height": 400,\n', '"height": 400,\n      "depth": 3,\n'),
        f"{FOLDER}/{_LAYOUT}: sections.section_cv01.depth: ",
        (),
        ValidationError,
        id="an unknown key in a layout file",
    ),
    pytest.param(
        _edit(
            "schema/layers.json",
            '"columns": [\n',
            '"columns": [\n    {\n      "id": "quote_reference",\n'
            '      "name": "quote_reference",\n      "type": "str"\n    },\n',
        ),
        f"{FOLDER}/schema/layers.json: ",
        ("quote_reference",),
        ValidationError,
        id="an identity column written",
    ),
    pytest.param(
        # As a merge keeping two branches' columns can: the sheets would name one of them.
        _edit(
            "schema/layers.json",
            '"columns": [\n',
            '"columns": [\n    {\n      "id": "c_limit_2",\n'
            '      "name": "limit",\n      "type": "float"\n    },\n',
        ),
        f"{FOLDER}/schema/layers.json: ",
        ("two columns are named limit",),
        ValidationError,
        id="two columns of one name",
    ),
    pytest.param(
        _edit(
            "schema/quote_details.json",
            '"name": "insured",\n',
            '"name": "insured",\n      "type": "int",\n',
        ),
        f"{FOLDER}/schema/quote_details.json: columns.1.type: ",
        (),
        ValidationError,
        id="quote_details given a column's type",
    ),
    pytest.param(
        _edit(
            "schema/quote_details.json",
            '"name": "insured",\n',
            '"name": "insured",\n      "id": "qd_insured",\n',
        ),
        f"{FOLDER}/schema/quote_details.json: columns.1.id: ",
        (),
        ValidationError,
        id="quote_details given a column's id",
    ),
    pytest.param(
        _edit(
            "schema/quote_details.json",
            '"required": true\n    }\n',
            '"required": true\n    },\n'
            '    {\n      "name": "stage",\n      "required": true\n    }\n',
        ),
        f"{FOLDER}/schema/quote_details.json: ",
        ("stage", "required"),
        ValidationError,
        id="quote_details given a rule on a column the app sets",
    ),
    pytest.param(
        _edit(
            "schema/quote_details.json",
            '"required": true\n    }\n',
            '"required": true\n    },\n'
            '    {\n      "name": "colour",\n      "label": "Colour"\n    }\n',
        ),
        f"{FOLDER}/schema/quote_details.json: columns.2.name: ",
        (),
        ValidationError,
        id="quote_details given a column Haute does not have",
    ),
    *[
        pytest.param(
            _edit(
                "schema/pricing.json",
                '"label": "Premium",\n',
                f'"label": "Premium",\n      "{key}": {value},\n',
            ),
            f"{FOLDER}/schema/pricing.json: ",
            ("premium", key),
            ValidationError,
            id=f"an output column holding {key}",
        )
        for key, value in _OUTPUT_RULES
    ],
    pytest.param(
        _edit(
            _LAYOUT,
            _CHART_AT,
            _CHART_AT + ',\n        "chart_zzzz": {"x": 0, "y": 0, "w": 10, "h": 10}',
        ),
        f"{FOLDER}/{_LAYOUT} ",
        ("chart_zzzz",),
        None,
        id="a layout entry for a component the section does not hold",
    ),
    pytest.param(
        _edit_json(
            _LAYOUT,
            lambda data: data["sections"].update(section_zz01={"height": 10, "components": {}}),
        ),
        f"{FOLDER}/{_LAYOUT} ",
        ("section_zz01",),
        None,
        id="a layout entry for a section the sheet does not hold",
    ),
    pytest.param(
        _edit(_LAYOUT, ",\n        " + _CHART_AT, ""),
        f"{FOLDER}/{_LAYOUT} ",
        ("chart_c6h7",),
        None,
        id="a component without a layout entry",
    ),
    pytest.param(
        # Named by its kind as the editor names it, though the files call a Table "table".
        _edit(_LAYOUT, '        "table_l4y5": {"x": 16, "y": 152, "w": 720, "h": 200},\n', ""),
        f"{FOLDER}/{_LAYOUT} ",
        ('leaves out the Table "Layers" (table_l4y5)',),
        None,
        id="a Table without a layout entry",
    ),
    pytest.param(
        _edit_json(_LAYOUT, lambda data: data["sections"].pop("section_pr01")),
        f"{FOLDER}/{_LAYOUT} ",
        ("section_pr01",),
        None,
        id="a section without a layout entry",
    ),
    pytest.param(
        _write("sample/pricing.json", b'[\n  {\n    "premium": "1"\n  }\n]\n'),
        f"{FOLDER}/sample/pricing.json {_NO_INPUT}",
        (),
        None,
        id="a sample file for an output table",
    ),
    pytest.param(
        _write("sample/claims.json", b'[\n  {\n    "paid": "1"\n  }\n]\n'),
        f"{FOLDER}/sample/claims.json {_NO_INPUT}",
        (),
        None,
        id="a sample file for a table the schema does not hold",
    ),
    pytest.param(
        _write("sample/layers.json", b"[]\n"),
        f"{FOLDER}/sample/layers.json: ",
        (),
        ValidationError,
        id="an empty sample file",
    ),
    pytest.param(
        _edit("sample/layers.json", '"limit": "1000000"', '"limit": 1000000'),
        f"{FOLDER}/sample/layers.json: 1.limit",
        (),
        ValidationError,
        id="a sample cell that is a number",
    ),
    pytest.param(
        _edit("sample/layers.json", '"limit": "1000000"', '"limit": null'),
        f"{FOLDER}/sample/layers.json: 1.limit",
        (),
        ValidationError,
        id="a sample cell that is null",
    ),
    pytest.param(
        _edit("form.json", '"version": 1,', '"version": 2,'),
        f"{FOLDER}/form.json: version: ",
        (),
        ValidationError,
        id="version 2",
    ),
    pytest.param(
        _edit(_LAYOUT, '"x": 760, "y": 16,', '"x": 760.5, "y": 16,'),
        f"{FOLDER}/{_LAYOUT}: sections.section_cv01.components.chart_c6h7.x: ",
        (),
        ValidationError,
        id="a layout number not whole",
    ),
    pytest.param(
        _edit("form.json", '"quote_details",\n    "policy",', '"policy",\n    "quote_details",'),
        f"{FOLDER}/form.json",
        ("quote_details",),
        None,
        id="an index not leading with quote_details",
    ),
    pytest.param(
        _edit("schema/layers.json", '"id": "t_layers",', '"id": "?layers",'),
        f"{FOLDER} ",
        ("?layers",),
        ValidationError,
        id="a table id starting with a question mark",
    ),
]


@pytest.mark.parametrize(("change", "starts", "says", "cause"), READ_REFUSALS)
def test_a_folder_that_cannot_be_read_as_a_form_is_refused_naming_the_file_and_the_place(
    project: Path,
    change: Callable[[Path], None],
    starts: str,
    says: tuple[str, ...],
    cause: type[BaseException] | None,
) -> None:
    write_form(project, FORM)
    change(form_folder(project))

    error = refusal(project)

    assert error.message.startswith(starts), error.message
    for fragment in says:
        assert fragment in error.message, error.message
    if cause is not None:
        assert isinstance(error.__cause__, cause)


def test_two_index_entries_differing_only_in_case_name_the_table(project: Path) -> None:
    write_form(project, FORM)
    _edit("form.json", '"pricing"\n', '"pricing",\n    "Layers"\n')(form_folder(project))

    assert "layers" in refusal(project).message.casefold()


#: Each key that says what a thing is, by file and place in it, and the place the refusal
#: names (a component's place takes its kind).
REQUIRED_KEYS = [
    ("form.json", ("version",), "version"),
    ("form.json", ("name",), "name"),
    ("form.json", ("schema",), "schema"),
    ("form.json", ("sheets",), "sheets"),
    ("schema/policy.json", ("id",), "id"),
    ("schema/policy.json", ("role",), "role"),
    ("schema/policy.json", ("rows",), "rows"),
    ("schema/policy.json", ("columns",), "columns"),
    ("schema/policy.json", ("columns", 0, "id"), "columns.0.id"),
    ("schema/policy.json", ("columns", 0, "name"), "columns.0.name"),
    ("schema/policy.json", ("columns", 0, "type"), "columns.0.type"),
    ("sheets/br_coverages.json", ("id",), "id"),
    ("sheets/br_coverages.json", ("title",), "title"),
    ("sheets/br_coverages.json", ("sections",), "sections"),
    ("sheets/br_coverages.json", ("sections", 0, "id"), "sections.0.id"),
    ("sheets/br_coverages.json", ("sections", 0, "title"), "sections.0.title"),
    ("sheets/br_coverages.json", ("sections", 0, "components"), "sections.0.components"),
    (
        "sheets/br_coverages.json",
        ("sections", 0, "components", 0, "id"),
        "sections.0.components.0.collection.id",
    ),
    (
        "sheets/br_coverages.json",
        ("sections", 0, "components", 0, "type"),
        "sections.0.components.0",
    ),
    (_LAYOUT, ("width",), "width"),
    (_LAYOUT, ("sections",), "sections"),
    (_LAYOUT, ("sections", "section_cv01", "height"), "sections.section_cv01.height"),
    (_LAYOUT, ("sections", "section_cv01", "components"), "sections.section_cv01.components"),
    *[
        (
            _LAYOUT,
            ("sections", "section_cv01", "components", "collection_k3f9", number),
            f"sections.section_cv01.components.collection_k3f9.{number}",
        )
        for number in ("x", "y", "w", "h")
    ],
]


@pytest.mark.parametrize(
    ("path", "key", "place"),
    [pytest.param(*entry, id=f"{entry[0]}: {entry[2]}") for entry in REQUIRED_KEYS],
)
def test_a_file_without_a_key_that_says_what_a_thing_is_is_refused(
    project: Path, path: str, key: tuple[Any, ...], place: str
) -> None:
    write_form(project, FORM)

    def without(data: Any) -> None:
        for step in key[:-1]:
            data = data[step]
        del data[key[-1]]

    _edit_json(path, without)(form_folder(project))
    error = refusal(project)

    assert error.message.startswith(f"{FOLDER}/{path}: {place}: "), error.message
    assert isinstance(error.__cause__, ValidationError)
    if key[-1] == "type" and "components" in key:
        assert "'type'" in error.message
    else:
        assert error.message.endswith("Field required"), error.message


def test_a_section_or_sheet_too_small_for_a_component_is_grown_on_read_and_said(
    project: Path,
) -> None:
    write_form(project, FORM)
    folder = form_folder(project)
    # A merge or a hand edit leaves the Chart below its section's foot and past its sheet.
    replace_in(
        folder / _LAYOUT, _CHART_AT, '"chart_c6h7": {"x": 900, "y": 200, "w": 480, "h": 280}'
    )
    edited = on_disk(folder)

    document = read_form_document(config(project))

    sheet = document.spec.pages[0]
    assert (sheet.sections[0].height, sheet.width) == (480, 1380)
    assert len(document.repairs) == 2
    for said, grown in zip(document.repairs, ("Cover", "BR Coverages"), strict=True):
        assert grown in said and "Limits" in said, said
    assert "400" in document.repairs[0] and "480" in document.repairs[0]
    assert "1280" in document.repairs[1] and "1380" in document.repairs[1]
    # The files are left as they were until the form is saved.
    assert on_disk(folder) == edited


@pytest.mark.parametrize(
    ("edits", "identifier"),
    [
        pytest.param(
            [("sheets/br_coverages.json", "table_p2r3"), (_LAYOUT, "table_p2r3")],
            "table_l4y5",
            id="two components",
        ),
        pytest.param(
            [
                ("sheets/quote_summary.json", "section_sm01"),
                ("sheets/quote_summary.layout.json", "section_sm01"),
            ],
            "section_cv01",
            id="two sections",
        ),
        pytest.param([("schema/br_perils.json", "t_perils")], "t_layers", id="two tables"),
        pytest.param([("schema/layers.json", "c_limit")], "c_layer", id="two columns of a table"),
    ],
)
def test_one_id_given_to_two_things_is_refused_naming_the_id(
    project: Path, edits: list[tuple[str, str]], identifier: str
) -> None:
    # A merge that keeps two branches' components, or a block copied by hand.
    write_form(project, FORM)
    for path, old in edits:
        replace_in(form_folder(project) / path, f'"{old}"', f'"{identifier}"')

    error = refusal(project)

    assert error.message.startswith(f"{FOLDER} "), error.message
    assert _word(identifier).search(error.message), error.message


def _link_directory(target: Path, link: Path) -> None:
    """A directory link anyone can make: a junction on Windows, a symlink elsewhere."""
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


def test_a_link_in_the_form_folder_to_a_folder_outside_the_project_is_refused(
    tmp_path: Path,
) -> None:
    project = tmp_path / "p"
    enable(project)
    write_form(project, FORM)
    outside = tmp_path / "o"
    shutil.move(form_folder(project) / "schema", outside)
    _link_directory(outside, form_folder(project) / "schema")

    error = refusal(project)

    assert error.message.startswith(f"{FOLDER}/schema "), error.message
    assert "link" in error.message or "junction" in error.message, error.message


# -- Names the folder cannot hold --------------------------------------------------------


NAME_REFUSALS = [
    pytest.param(
        lambda form: form["schema"]["tables"].append(
            {"id": "t_layers_2", "name": "Layers", "rows": "many", "columns": []}
        ),
        ("layers", "Layers"),
        id="two tables of one name by case",
    ),
    pytest.param(
        lambda form: table_of(form, "t_layers")["columns"].append(
            {**column("limit", "float"), "id": "c_limit_2"}
        ),
        ("layers", "limit"),
        id="two columns of one name",
    ),
    pytest.param(
        lambda form: table_of(form, "t_layers").update(name="cover layers"),
        ("cover layers",),
        id="a table name not an identifier",
    ),
    pytest.param(
        lambda form: column_of(form, "t_layers", "c_layer_name").update(name="2nd_name"),
        ("2nd_name",),
        id="a column name not an identifier",
    ),
    pytest.param(
        lambda form: table_of(form, "t_policy").update(name="con"), ("con",), id="con as a table"
    ),
    pytest.param(
        lambda form: table_of(form, "t_policy").update(name="COM1"), ("COM1",), id="COM1 as a table"
    ),
    pytest.param(
        lambda form: form["pages"][1].update(title="con"), ("con",), id="con as a sheet's name"
    ),
    pytest.param(
        lambda form: form["pages"][1].update(title="COM1"), ("COM1",), id="COM1 as a sheet's name"
    ),
    pytest.param(
        lambda form: form["pages"][1].update(title="!!!"),
        ("!!!",),
        id="a title of no letters or digits",
    ),
    pytest.param(
        lambda form: form["pages"][1].update(title="br-coverages"),
        ("BR Coverages", "br-coverages"),
        id="two titles of one name",
    ),
]


@pytest.mark.parametrize(("change", "named"), NAME_REFUSALS)
def test_a_name_the_folder_cannot_hold_is_refused_naming_it_and_nothing_is_written(
    tmp_path: Path, change: Callable[[dict[str, Any]], None], named: tuple[str, ...]
) -> None:
    form = copy.deepcopy(FORM)
    change(form)
    target = tmp_path / "w" / "rating" / "workbench"

    with pytest.raises(FormFolderNameError) as caught:
        folder_files(spec_of(form), target)

    error = caught.value
    assert isinstance(error, WorkbenchFormError)
    assert len(error.problems) == 1, error.problems
    for name in named:
        assert _word(name).search(error.problems[0]), error.problems
    assert error.problems[0] in error.message
    assert not (tmp_path / "w").exists()


def test_every_name_the_folder_cannot_hold_is_listed(tmp_path: Path) -> None:
    form = copy.deepcopy(FORM)
    table_of(form, "t_policy")["name"] = "con"
    table_of(form, "t_layers")["columns"].append({**column("limit", "float"), "id": "c_limit_2"})
    form["pages"][1]["title"] = "COM1"

    with pytest.raises(FormFolderNameError) as caught:
        folder_files(spec_of(form), tmp_path / "w")

    problems = caught.value.problems
    assert len(problems) == 3, problems
    for name in ("con", "limit", "COM1"):
        assert any(_word(name).search(problem) for problem in problems), (name, problems)
    for problem in problems:
        assert problem in caught.value.message


def test_a_file_whose_full_path_would_pass_240_characters_is_refused(tmp_path: Path) -> None:
    spec = blank_form("m")
    # The blank form's longest path is its sheet's layout file.
    longest = "sheets/sheet_1.layout.json"
    anchor = Path(tmp_path.anchor)
    overhead = len(str(anchor / "f" / longest)) - 1

    def folder(length: int) -> Path:
        """A folder, never made, in which *longest*'s full path is *length* characters."""
        return anchor / ("f" * (length - overhead))

    assert len(str(folder(240) / longest)) == 240
    assert sorted(folder_files(spec, folder(240))) == [
        "form.json",
        "schema/quote_details.json",
        "sheets/sheet_1.json",
        longest,
    ]
    with pytest.raises(FormFolderNameError) as caught:
        folder_files(spec, folder(241))
    assert len(caught.value.problems) == 1
    assert longest in caught.value.problems[0]
    assert "240" in caught.value.problems[0]


@pytest.mark.parametrize(
    ("title", "name"),
    [
        ("BR Coverages", "br_coverages"),
        ("Sheet 1", "sheet_1"),
        ("  Quote & Summary!  ", "quote_summary"),
        ("Motor--2026__rates", "motor_2026_rates"),
        ("Équipe 2", "quipe_2"),
        ("!!!", ""),
        ("a" * 70, "a" * 64),
        # At most 64 characters, and still none at either end.
        ("x" * 63 + " y", "x" * 63),
    ],
)
def test_a_sheet_s_name_is_its_title_in_lower_case_ascii_runs_of_anything_else_one_underscore(
    title: str, name: str
) -> None:
    assert sheet_name(title) == name


# -- The lock, and a writer that takes none -----------------------------------------------


#: A reader in another process: it says when it starts reading, then answers the revision
#: and the form it read.
READER = """
import json
import sys
from pathlib import Path

from haute._workbench_config import read_workbench_config
from haute._workbench_folder import read_form_document

config = read_workbench_config(Path(sys.argv[1]))
print("reading", flush=True)
document = read_form_document(config)
print(json.dumps({"revision": document.revision, "form": document.spec.model_dump(mode="json")}))
"""


def _first_line(process: subprocess.Popen[str], timeout: float) -> str:
    said: list[str] = []
    assert process.stdout is not None
    stdout = process.stdout
    listener = threading.Thread(target=lambda: said.append(stdout.readline()), daemon=True)
    listener.start()
    listener.join(timeout)
    return said[0] if said else ""


def test_a_reader_in_another_process_waits_on_a_save_and_reads_the_saved_form_whole(
    project: Path,
) -> None:
    folder = form_folder(project)
    before = write_form(project, FORM)
    region = renamed(FORM, "t_policy", "region", "c_state")
    after = files_of(region, project)
    # The rename writes four files; read between the first and the second, the form would
    # hold a sample naming a column the schema does not hold yet.
    order = changes(before, after)[0]
    assert order[0] == "sample/policy.json" and len(order) == 4
    first_written = threading.Event()
    finish = threading.Event()

    def save() -> None:
        with form_folder_lock(project):
            write_files(folder, {order[0]: after[order[0]]})
            first_written.set()
            finish.wait(timeout=60)
            write_files(folder, {path: after[path] for path in order[1:]})

    writer = threading.Thread(target=save)
    writer.start()
    reader: subprocess.Popen[str] | None = None
    try:
        assert first_written.wait(timeout=30)
        reader = subprocess.Popen(
            [sys.executable, "-c", READER, str(project)],
            cwd=project,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        started = _first_line(reader, timeout=60)
        if started.strip() != "reading":
            reader.kill()
            _, err = reader.communicate(timeout=30)
            pytest.fail(f"the reader did not start reading: {started!r} {err}")
        time.sleep(0.5)
        # Paused between the save's first and second file, the reader waits on the lock.
        assert reader.poll() is None
        finish.set()
        out, err = reader.communicate(timeout=120)
    finally:
        finish.set()
        writer.join(timeout=60)
        if reader is not None and reader.poll() is None:
            reader.kill()
            reader.communicate(timeout=30)

    assert reader.returncode == 0, err
    answer = json.loads(out)
    assert answer["revision"] == folder_revision(after)
    assert answer["form"] == spec_of(region).model_dump(mode="json")


def test_the_form_folder_s_lock_is_the_project_s_own_however_its_path_is_spelt(
    tmp_path: Path,
) -> None:
    project = tmp_path / "p"
    project.mkdir()
    other = tmp_path / "q"
    other.mkdir()
    held = threading.Event()
    done = threading.Event()

    def hold() -> None:
        with form_folder_lock(project):
            held.set()
            done.wait(timeout=30)

    holder = threading.Thread(target=hold)
    holder.start()
    try:
        assert held.wait(timeout=30)
        spellings = [project, tmp_path / "p" / ".." / "p"]
        if os.name == "nt":
            spellings.append(Path(str(project).upper()))
        for spelling in spellings:
            lock = form_folder_lock(spelling)
            assert lock.acquire(timeout=0.2) is False, spelling
        # Another project's lock is its own.
        lock = form_folder_lock(other)
        assert lock.acquire(timeout=5) is True
        lock.release()
    finally:
        done.set()
        holder.join(timeout=30)


def test_a_read_that_a_lockless_writer_changes_reads_again_and_answers_one_side_whole(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = form_folder(project)
    before = write_form(project, FORM)
    region = renamed(FORM, "t_policy", "region", "c_state")
    after = files_of(region, project)
    order = changes(before, after)[0]
    listings: list[dict[str, Any]] = []
    listing = _workbench_folder._listing

    def listed(config: WorkbenchConfig) -> dict[str, Any]:
        if len(listings) == 1:
            # ... and finishes before the read lists the folder again, the read having taken
            # the first file new and the rest old.
            write_files(folder, {path: after[path] for path in order[1:]})
        found = listing(config)
        listings.append(found)
        if len(listings) == 1:
            # A writer that takes no lock, git outside Haute say, starts once the read has
            # listed the folder ...
            write_files(folder, {order[0]: after[order[0]]})
        return found

    monkeypatch.setattr(_workbench_folder, "_listing", listed)
    document = read_form_document(config(project))

    assert len(listings) == 4
    assert document.revision == folder_revision(after)
    assert document.spec == spec_of(region)


@pytest.mark.parametrize("tries_changed", [2, 3])
def test_a_folder_that_changes_on_each_of_three_tries_is_refused(
    project: Path, monkeypatch: pytest.MonkeyPatch, tries_changed: int
) -> None:
    write_form(project, FORM)
    sample = form_folder(project) / "sample" / "policy.json"
    listings: list[dict[str, Any]] = []
    listing = _workbench_folder._listing

    def listed(config: WorkbenchConfig) -> dict[str, Any]:
        found = listing(config)
        listings.append(found)
        # Each try starts with a listing, after which a writer that takes no lock grows a file.
        if len(listings) % 2 == 1 and len(listings) // 2 < tries_changed:
            grow(sample)
        return found

    monkeypatch.setattr(_workbench_folder, "_listing", listed)

    if tries_changed == 3:
        error = refusal(project)
        assert error.message == f"{FOLDER} changed while it was being read: try again"
        assert len(listings) == 6
    else:
        # Changed on two tries, the third reads it.
        document = read_form_document(config(project))
        assert document.revision == folder_revision(on_disk(form_folder(project)))
        assert document.spec == spec_of(FORM)
        assert len(listings) == 6


# -- The golden folder -------------------------------------------------------------------


def golden_files() -> dict[str, bytes]:
    return on_disk(GOLDEN)


def test_the_golden_folder_is_the_test_project_s_form_byte_for_byte(project: Path) -> None:
    golden = golden_files()

    assert sorted(golden) == FORM_FILES
    for path, data in golden.items():
        assert b"\r" not in data, path
    assert files_of(FORM, project) == golden


def test_the_golden_folder_reads_as_the_test_project_s_form(project: Path) -> None:
    shutil.copytree(GOLDEN, project / FOLDER)

    document = read_form_document(config(project))

    assert document.spec.model_dump(mode="json") == spec_of(FORM).model_dump(mode="json")
    assert document.revision == folder_revision(golden_files())
    assert document.repairs == ()
