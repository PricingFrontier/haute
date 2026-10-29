"""The workbench's form (specs/workbench): one canonical shape, read whole as a save, a
release and the quote store hold it, and grown where the folder it is read from leaves a
component outside its section or sheet; the folder's own files are tested in
``tests/test_workbench_folder.py``."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from haute._explore_pivots import PIVOT_DECIMAL_PLACES_MAX
from haute._workbench_config import WorkbenchConfig
from haute._workbench_folder import read_form_document
from haute._workbench_form import (
    BLANK_SECTION_HEIGHT,
    BLANK_SHEET_WIDTH,
    COLLECTION_COLUMNS_MAX,
    HEADER_ROW_MAX,
    QUOTE_DETAILS_COLUMNS,
    QUOTE_DETAILS_MANAGED,
    QUOTE_DETAILS_REFUSAL,
    TABLE_ROWS_MAX,
    FormDocument,
    FormSpec,
    HeadingWidget,
    SchemaColumn,
    blank_form,
    form_revision,
    identity_clashes,
    quote_details_table,
    render_form,
    with_quote_details,
)
from tests._workbench_helpers import form_files, write_files


def column(name: str, type_: str = "str", **rest: Any) -> dict[str, Any]:
    return {
        "id": f"c_{name}",
        "name": name,
        "type": type_,
        "label": "",
        "group": "",
        "key": False,
        "required": False,
        "min": None,
        "max": None,
        "options": [],
        "options_source": None,
        "row_per_value": False,
        "index": False,
        "help": "",
        "placeholder": "",
        "default_value": "",
        "number_format": "general",
        "decimal_places": None,
        "use_grouping": True,
        **rest,
    }


#: The quote's reference and option, which every table but quote_details leads with.
IDENTITY: list[dict[str, Any]] = [
    {**column("quote_reference"), "id": "quote_reference"},
    {**column("quote_option", "int"), "id": "quote_option"},
]


# The test project's shape: one-row and many-row input tables, an index column, an output
# table, a Collection, a Table and a Chart in one section and a Heading in a second, the
# Table and the second section each shown while a condition holds, and a sample typed into
# the Collection.
ALWAYS: dict[str, Any] = {"match": "all", "conditions": []}
FORM: dict[str, Any] = {
    "version": 1,
    "name": "Contractors' equipment",
    # Its sheets blue on dark slate, the borders amber, the input cells red and the outputs
    # purple, the components' background left the editor's own.
    "theme": {
        "sheet_color": "#1E293B",
        "background_color": None,
        "border_color": "#D97706",
        "font_color": "#2563EB",
        "input_cell_color": "#DC2626",
        "output_cell_color": "#9333EA",
    },
    "schema": {
        "tables": [
            {
                "id": "t_policy",
                "name": "policy_details",
                "role": "input",
                "rows": "one",
                "columns": [
                    *IDENTITY,
                    column(
                        "state",
                        options=["CA", "NY"],
                        required=True,
                        label="State",
                        default_value="CA",
                    ),
                    column(
                        "exposure",
                        "float",
                        min=0.0,
                        help="Sum insured",
                        group="Cover",
                        placeholder="e.g. 250,000",
                        number_format="currency_gbp",
                        decimal_places=0,
                    ),
                ],
            },
            {
                "id": "t_layers",
                "name": "layers",
                "role": "input",
                "rows": "many",
                "columns": [
                    *IDENTITY,
                    column("layer", "int", key=True, index=True),
                    column("limit", "float"),
                    column("layer_name"),
                ],
            },
            {
                "id": "t_pricing",
                "name": "pricing_output",
                "role": "output",
                "rows": "one",
                "columns": [*IDENTITY, column("premium", "float")],
            },
        ]
    },
    "pages": [
        {
            "id": "page_1",
            "title": "Sheet 1",
            "width": 1280,
            "show_when": ALWAYS,
            "sections": [
                {
                    "id": "s_cover",
                    "title": "Cover",
                    "height": 376,
                    "show_when": ALWAYS,
                    "widgets": [
                        {
                            "id": "w_policy",
                            "type": "collection",
                            "x": 16,
                            "y": 16,
                            "w": 720,
                            "h": 120,
                            "title": "Policy",
                            "fields": [
                                {"table": "t_policy", "column": "c_state"},
                                {"table": "t_pricing", "column": "c_premium"},
                            ],
                            "show_when": ALWAYS,
                            "columns": 2,
                            # Its labels beside its boxes, compact and without a frame, a
                            # gap between its two fields.
                            "labels": "beside",
                            "density": "compact",
                            "frame": False,
                            # Its text larger, its labels in capitals, smaller, bold,
                            # wrapped, filled, coloured, centred and 60 percent of a field
                            # wide; its boxes' borders red, heavier and dotted, their values
                            # on the left, each box 96px wide; its frame black and heavier
                            # still; and its state's label coloured and on the left, and its
                            # box coloured, bold, on the right and narrower.
                            "font_size": 13,
                            "header": "caps",
                            "header_font_size": 11,
                            "header_bold": True,
                            "header_wrap": True,
                            "header_color": "#F1F5F9",
                            "header_font_color": "#1E293B",
                            "header_align": "center",
                            "label_width": 60,
                            "box_style": "dotted",
                            "box_weight": "medium",
                            "box_color": "#DC2626",
                            "box_align": "left",
                            "box_width": 96,
                            "frame_color": "#000000",
                            "frame_weight": "thick",
                            "column_looks": [
                                {
                                    "table": "t_policy",
                                    "column": "c_state",
                                    "header_color": "#2563EB",
                                    "header_font_color": "#FFFFFF",
                                    "header_align": "left",
                                    "cell_color": "#DBEAFE",
                                    "font_color": "#000000",
                                    "bold": True,
                                    "align": "right",
                                    "width": 64,
                                    "show_when": {
                                        "match": "all",
                                        "conditions": [
                                            {
                                                "table": "t_policy",
                                                "column": "c_exposure",
                                                "op": "filled",
                                                "values": [],
                                            }
                                        ],
                                    },
                                }
                            ],
                            "spacers": [1],
                        },
                        {
                            "id": "w_layers",
                            "type": "tableInput",
                            "x": 16,
                            "y": 160,
                            "w": 720,
                            "h": 200,
                            "title": "Layers",
                            "fields": [{"table": "t_layers", "column": "c_limit"}],
                            "show_when": {
                                "match": "any",
                                "conditions": [
                                    {
                                        "table": "t_policy",
                                        "column": "c_state",
                                        "op": "one_of",
                                        "values": ["CA", "NY"],
                                    }
                                ],
                            },
                            "rows": 5,
                            # The layer index numbers the rows, and the layers are a set structure.
                            "row_numbers": False,
                            "density": "comfortable",
                            "gridlines": "all",
                            "striped": False,
                            "fixed_rows": True,
                            # Its header in capitals, without a frame, transposed and showing
                            # its fields' groups.
                            "header": "caps",
                            "frame": False,
                            "transposed": True,
                            "groups": True,
                            # Its text and its header's larger, its labels plain, wrapped,
                            # turned upwards, centred and coloured; its lines red, heavy and
                            # dashed; its frame black and heavier still; and its limit's column
                            # filled, bold, its label on the right over its values centred,
                            # and a set width.
                            "font_size": 14,
                            "header_font_size": 16,
                            "header_bold": False,
                            "header_wrap": True,
                            "header_orientation": "up",
                            "header_color": "#1E293B",
                            "header_font_color": "#FFFFFF",
                            "header_align": "center",
                            "grid_color": "#DC2626",
                            "grid_weight": "medium",
                            "grid_style": "dashed",
                            "frame_color": "#000000",
                            "frame_weight": "thick",
                            "column_looks": [
                                {
                                    "table": "t_layers",
                                    "column": "c_limit",
                                    "header_color": "#2563EB",
                                    "header_font_color": "#FFFFFF",
                                    "header_align": "right",
                                    "cell_color": "#DBEAFE",
                                    "font_color": "#000000",
                                    "bold": True,
                                    "align": "center",
                                    "width": 120,
                                    "show_when": {"match": "all", "conditions": []},
                                }
                            ],
                        },
                        {
                            "id": "w_limits",
                            "type": "chart",
                            "x": 760,
                            "y": 16,
                            "w": 480,
                            "h": 280,
                            "title": "Limits",
                            "fields": [{"table": "t_layers", "column": "c_limit"}],
                            "show_when": ALWAYS,
                            # The layers' names label the rows, drawn as lines across, stacked, the
                            # legend on the right and each value labelled.
                            "category": {"table": "t_layers", "column": "c_layer_name"},
                            "chart_type": "line",
                            "orientation": "horizontal",
                            "stacked": True,
                            "legend": "right",
                            "data_labels": True,
                        },
                    ],
                },
                {
                    "id": "s_equipment",
                    "title": "Equipment",
                    "height": 64,
                    # Only for New York: its Heading hides with it.
                    "show_when": {
                        "match": "all",
                        "conditions": [
                            {
                                "table": "t_policy",
                                "column": "c_state",
                                "op": "equals",
                                "values": ["NY"],
                            }
                        ],
                    },
                    "widgets": [
                        {
                            "id": "w_title",
                            "type": "heading",
                            "x": 16,
                            "y": 8,
                            "w": 480,
                            "h": 40,
                            # Named as typed, outside ASCII too, large and centred: the
                            # section's title, with a line under it.
                            "text": "Équipement des entrepreneurs",
                            "description": "Plant and equipment, by layer",
                            "show_when": ALWAYS,
                            "size": "large",
                            "align": "center",
                        },
                    ],
                },
            ],
        }
    ],
    "sample": {"t_policy": [{"c_state": "NY", "c_exposure": "$100,000"}]},
}


def widgets(form: dict[str, Any]) -> list[dict[str, Any]]:
    """The test form's components, section by section: the Collection, the Table, the Chart
    and the Heading."""
    return [widget for section in form["pages"][0]["sections"] for widget in section["widgets"]]


def read_document(document: dict[str, Any]) -> FormSpec:
    """*document* read as the form is wherever it is held whole (a save, a release, the
    quote store): Haute's quote_details first."""
    return with_quote_details(FormSpec.model_validate(document))


#: The sheet the test form's folder files, by its title "Sheet 1".
LAYOUT = "sheets/sheet_1.layout.json"


def read_after_layout_edit(
    project: Path, form: dict[str, Any], change: Any
) -> tuple[FormDocument, bytes]:
    """*form* saved as its folder, its one sheet's layout file then changed by *change*, as a
    merge or a hand edit can, and the folder read back; with the layout file's bytes."""
    folder = project / "rating" / "workbench"
    files = form_files(form, folder)
    layout = json.loads(files[LAYOUT])
    change(layout)
    files[LAYOUT] = (json.dumps(layout, indent=2) + "\n").encode("utf-8")
    write_files(folder, files)
    config = WorkbenchConfig(enabled=True, project_root=project, form="rating/workbench")
    return read_form_document(config), files[LAYOUT]


@pytest.mark.meta
def test_the_editor_names_the_quote_details_columns_the_server_does() -> None:
    """The view's columns of quote_details, whose `QUOTE_DETAILS` map is how the app names
    each one it reads, and the columns the app sets, named through that map."""
    utils = Path(__file__).resolve().parents[1] / "frontend" / "src" / "utils"
    identity = (utils / "quoteIdentity.ts").read_text(encoding="utf-8")
    columns = re.findall(r'\["(qd_\w+)", "(\w+)", "(\w+)"\]', identity)
    managed = re.search(r"QUOTE_DETAILS_MANAGED[^=]*= new Set\(\[([^\]]*)\]\)", identity)

    assert tuple(columns) == QUOTE_DETAILS_COLUMNS
    assert managed is not None
    by_name = {name: column_id for column_id, name, _ in QUOTE_DETAILS_COLUMNS}
    named = re.findall(r"QUOTE_DETAILS\.(\w+)", managed.group(1))
    assert {by_name[name] for name in named} == QUOTE_DETAILS_MANAGED
    assert len(named) == len(QUOTE_DETAILS_MANAGED)


@pytest.mark.meta
def test_the_editor_mirrors_the_forms_limits_and_words() -> None:
    """The view's own copies of the server's limits and refusals: a change to either side
    alone fails here."""
    frontend = Path(__file__).resolve().parents[1] / "frontend" / "src"

    def constant(file: str, name: str) -> str:
        source = (frontend / file).read_text(encoding="utf-8")
        found = re.search(rf"export const {name} = (.+)", source)
        assert found is not None, f"{file} has no {name}"
        return found.group(1).strip()

    assert int(constant("utils/sheetGeometry.ts", "BLANK_SHEET_WIDTH")) == BLANK_SHEET_WIDTH
    assert int(constant("utils/sheetGeometry.ts", "BLANK_SECTION_HEIGHT")) == BLANK_SECTION_HEIGHT
    assert int(constant("utils/workbenchForm.ts", "TABLE_ROWS_MAX")) == TABLE_ROWS_MAX
    assert int(constant("utils/workbenchForm.ts", "COLLECTION_COLUMNS_MAX")) == (
        COLLECTION_COLUMNS_MAX
    )
    assert int(constant("panels/explore/pivotConfig.ts", "PIVOT_DECIMAL_PLACES_MAX")) == (
        PIVOT_DECIMAL_PLACES_MAX
    )
    assert json.loads(constant("utils/workbenchForm.ts", "QUOTE_DETAILS_REFUSAL")) == (
        QUOTE_DETAILS_REFUSAL
    )
    assert int(constant("utils/workbenchForm.ts", "HEADER_ROW_MAX")) == HEADER_ROW_MAX


def test_the_blank_form_is_one_empty_sheet_named_after_the_project() -> None:
    text = render_form(blank_form("motor"))

    # The whole sheet is one section until another is added, as tall as the view's least
    # sheet height less the room it leaves below the sections, on a sheet 1280 wide.
    section = {"id": "section_0001", "title": "Section 1", "height": 560}
    assert json.loads(text) == {
        "version": 1,
        "name": "motor",
        # The sheets in the editor's own colours.
        "theme": {
            "sheet_color": None,
            "background_color": None,
            "border_color": None,
            "font_color": None,
            "input_cell_color": None,
            "output_cell_color": None,
        },
        "schema": {"tables": [quote_details_table().model_dump(mode="json")]},
        "pages": [
            {
                "id": "sheet_0001",
                "title": "Sheet 1",
                "width": 1280,
                "show_when": ALWAYS,
                "sections": [{**section, "show_when": ALWAYS, "widgets": []}],
            }
        ],
        "sample": {},
    }
    assert text.endswith("}\n")


def test_a_form_round_trips_as_one_document_with_its_revision() -> None:
    """As a release ships it and the quote store keeps it: every field, read back the same."""
    spec = FormSpec.model_validate(FORM)

    data = render_form(spec).encode("utf-8")

    assert json.loads(data) == FORM
    # Read, it holds Haute's quote_details table first, as every form read whole does.
    document = read_document(json.loads(data))
    assert document == with_quote_details(spec)
    assert document.data_schema.tables[0].id == "quote_details"
    # The revision is the document's content hash: the same text, the same revision.
    assert form_revision(data) == form_revision(render_form(spec).encode("utf-8"))
    assert len(form_revision(data)) == 16


def test_another_document_has_another_revision() -> None:
    first = render_form(blank_form("motor")).encode("utf-8")

    assert form_revision(first.replace(b'"motor"', b'"home"')) != form_revision(first)


def test_the_file_keeps_text_as_typed(tmp_path: Path) -> None:
    """A label or an allowed value outside ASCII is written as it is, as the node configs are."""
    spec = FormSpec.model_validate(FORM)
    spec.data_schema.tables[0].columns[0].label = "Zürich"

    text = render_form(spec)

    assert '"label": "Zürich"' in text
    assert "\\u00fc" not in text


@pytest.mark.parametrize("text", ["", "   ", "  Cover  "])
def test_a_heading_s_text_reads_and_writes_as_typed_blank_or_not(text: str) -> None:
    # A Heading with no text is a problem the view names, never a refusal, and the spaces
    # around its text and its description are the builder's: the form keeps what was typed.
    form = json.loads(json.dumps(FORM))
    widgets(form)[3].update(text=text, description=text)

    spec = read_document(form)

    heading = spec.pages[0].sections[1].widgets[0]
    assert isinstance(heading, HeadingWidget)
    assert (heading.text, heading.description) == (text, text)
    written = widgets(json.loads(render_form(spec)))[3]
    assert (written["text"], written["description"]) == (text, text)


def test_every_field_is_written_so_the_document_has_one_spelling() -> None:
    # A file written without the index flags, the columns' display, the Table's and the
    # Chart's options, the Heading's text, description, size and alignment, the sheet's, the
    # Collection's and the Heading's show_when and the sample, as a builder that leaves
    # defaults out would write it.
    display = (
        "group",
        "help",
        "placeholder",
        "default_value",
        "number_format",
        "decimal_places",
        "use_grouping",
    )
    options = (
        "row_numbers",
        "density",
        "gridlines",
        "striped",
        "fixed_rows",
        "header",
        "frame",
        "transposed",
        "groups",
        "font_size",
        "header_font_size",
        "header_bold",
        "header_wrap",
        "header_orientation",
        "header_color",
        "header_font_color",
        "header_align",
        "grid_color",
        "grid_weight",
        "grid_style",
        "frame_color",
        "frame_weight",
        "column_looks",
    )
    collection_options = (
        "labels",
        "density",
        "frame",
        "font_size",
        "header",
        "header_font_size",
        "header_bold",
        "header_wrap",
        "header_color",
        "header_font_color",
        "header_align",
        "label_width",
        "box_style",
        "box_weight",
        "box_color",
        "box_align",
        "box_width",
        "frame_color",
        "frame_weight",
        "column_looks",
        "spacers",
    )
    chart_options = ("category", "chart_type", "orientation", "stacked", "legend", "data_labels")
    sparse = json.loads(json.dumps(FORM))
    del sparse["sample"]
    del sparse["theme"]
    for table in sparse["schema"]["tables"]:
        for col in table["columns"]:
            del col["index"]
            for field in display:
                del col[field]
    sparse["schema"]["tables"][1]["columns"][2]["index"] = True
    for field in options:
        del widgets(sparse)[1][field]
    for field in collection_options:
        del widgets(sparse)[0][field]
    for field in chart_options:
        del widgets(sparse)[2][field]
    for field in ("text", "description", "show_when", "size", "align"):
        del widgets(sparse)[3][field]
    del sparse["pages"][0]["show_when"]
    del sparse["pages"][0]["sections"][0]["show_when"]
    del widgets(sparse)[0]["show_when"]

    spec = read_document(sparse)

    assert spec.sample == {}
    assert [c.index for c in spec.data_schema.tables[2].columns] == [
        False,
        False,
        True,
        False,
        False,
    ]
    written = json.loads(render_form(spec))
    assert written["sample"] == {}
    assert all("index" in c for t in written["schema"]["tables"] for c in t["columns"])
    assert written["schema"]["tables"][1]["columns"][3] == column("exposure", "float", min=0.0)
    assert written["schema"]["tables"][1]["columns"][2]["default_value"] == ""
    grid = widgets(written)[1]
    assert {field: grid[field] for field in options} == {
        "row_numbers": True,
        "density": "compact",
        "gridlines": "rows",
        "striped": True,
        "fixed_rows": False,
        "header": "plain",
        "frame": True,
        "transposed": False,
        "groups": False,
        # 12px text, its header the same size, bold, level, unwrapped and placed as each
        # column's look says, in the theme's colours, as its lines and its frame are, thin and
        # solid, and no column with a look.
        "font_size": 12,
        "header_font_size": None,
        "header_bold": True,
        "header_wrap": False,
        "header_orientation": "horizontal",
        "header_color": None,
        "header_font_color": None,
        "header_align": "auto",
        "grid_color": None,
        "grid_weight": "thin",
        "grid_style": "solid",
        "frame_color": None,
        "frame_weight": "thin",
        "column_looks": [],
    }
    # A Collection labelled above its boxes, comfortable and framed, with no spacers: 12px
    # text, its labels the same size, as written, neither bold nor wrapped, in the theme's
    # colours, at their fields' start and two fifths of a field wide beside their boxes; its
    # boxes' borders thin, solid and in the theme's colour, their values placed as their
    # types put them, each box as wide as its label leaves; a thin frame in the theme's
    # colour, and no field with a look.
    boxes = widgets(written)[0]
    assert {field: boxes[field] for field in collection_options} == {
        "labels": "above",
        "density": "comfortable",
        "frame": True,
        "font_size": 12,
        "header": "plain",
        "header_font_size": None,
        "header_bold": False,
        "header_wrap": False,
        "header_color": None,
        "header_font_color": None,
        "header_align": "auto",
        "label_width": None,
        "box_style": "solid",
        "box_weight": "thin",
        "box_color": None,
        "box_align": "auto",
        "box_width": None,
        "frame_color": None,
        "frame_weight": "thin",
        "column_looks": [],
        "spacers": [],
    }
    # The sheets in the editor's own colours.
    assert written["theme"] == {
        "sheet_color": None,
        "background_color": None,
        "border_color": None,
        "font_color": None,
        "input_cell_color": None,
        "output_cell_color": None,
    }
    chart = widgets(written)[2]
    # Its rows numbered, drawn as upright columns, unstacked, the legend at the bottom and
    # no data labels.
    assert {field: chart[field] for field in chart_options} == {
        "category": None,
        "chart_type": "column",
        "orientation": "vertical",
        "stacked": False,
        "legend": "bottom",
        "data_labels": False,
    }
    # No text or description yet, of medium size and aligned left, in the file's order.
    heading = widgets(written)[3]
    assert list(heading) == "id x y w h type text description show_when size align".split()
    assert [heading[field] for field in ("text", "description", "size", "align")] == [
        "",
        "",
        "medium",
        "left",
    ]
    # Always shown.
    assert written["pages"][0]["show_when"] == ALWAYS
    assert written["pages"][0]["sections"][0]["show_when"] == ALWAYS
    assert widgets(written)[0]["show_when"] == ALWAYS
    assert heading["show_when"] == ALWAYS


@pytest.mark.parametrize(
    ("change", "where"),
    [
        (lambda f: f.update(colour="red"), "colour"),
        (lambda f: widgets(f)[0].update(rows=3), "rows"),
        (lambda f: widgets(f)[1].update(rows=51), "rows"),
        (lambda f: widgets(f)[1].update(density="roomy"), "density"),
        (lambda f: widgets(f)[1].update(gridlines="dotted"), "gridlines"),
        # A Collection has none of a Table's own options, and a Table none of a Collection's.
        (lambda f: widgets(f)[0].update(striped=True), "striped"),
        (lambda f: widgets(f)[0].update(gridlines="all"), "gridlines"),
        (lambda f: widgets(f)[0].update(header_orientation="up"), "header_orientation"),
        (lambda f: widgets(f)[1].update(labels="above"), "labels"),
        (lambda f: widgets(f)[1].update(label_width=60), "label_width"),
        (lambda f: widgets(f)[1].update(box_style="solid"), "box_style"),
        (lambda f: widgets(f)[1].update(box_width=96), "box_width"),
        (lambda f: widgets(f)[1].update(box_align="left"), "box_align"),
        (lambda f: widgets(f)[0].update(labels="under"), "labels"),
        (lambda f: widgets(f)[0].update(density="roomy"), "density"),
        (lambda f: widgets(f)[1].update(header="bold"), "header"),
        # A Table's text comes in set sizes, its labels turn three ways and sit four, and its
        # lines and frame come in three weights and its gridlines in three styles.
        (lambda f: widgets(f)[1].update(font_size=15), "font_size"),
        (lambda f: widgets(f)[1].update(header_font_size=9), "header_font_size"),
        (lambda f: widgets(f)[1].update(header_orientation="angled"), "header_orientation"),
        (lambda f: widgets(f)[1].update(header_align="justify"), "header_align"),
        (lambda f: widgets(f)[1].update(grid_weight="hairline"), "grid_weight"),
        (lambda f: widgets(f)[1].update(grid_style="double"), "grid_style"),
        (lambda f: widgets(f)[1].update(frame_weight=2), "frame_weight"),
        (lambda f: widgets(f)[1].update(grid_color="#dc2626"), "grid_color"),
        # Only a Table has them.
        (lambda f: widgets(f)[0].update(grid_color="#DC2626"), "grid_color"),
        # A Collection's boxes come in a Table's line weights and styles and are as wide as a
        # Table's columns can be, and its labels take from 1 to 99 percent of a field.
        (lambda f: widgets(f)[0].update(box_style="double"), "box_style"),
        (lambda f: widgets(f)[0].update(box_weight="hairline"), "box_weight"),
        (lambda f: widgets(f)[0].update(box_color="#dc2626"), "box_color"),
        (lambda f: widgets(f)[0].update(label_width=0), "label_width"),
        (lambda f: widgets(f)[0].update(label_width=100), "label_width"),
        (lambda f: widgets(f)[0].update(box_width=1001), "box_width"),
        (lambda f: widgets(f)[0].update(box_align="middle"), "box_align"),
        (lambda f: widgets(f)[0].update(header_align="justify"), "header_align"),
        (lambda f: widgets(f)[0]["column_looks"][0].update(header_align="middle"), "header_align"),
        (lambda f: widgets(f)[0].update(font_size=15), "font_size"),
        # A field's look on a Collection is held as a Table column's is.
        (
            lambda f: widgets(f)[0]["column_looks"][0].update(column="c_limit"),
            "has a look for t_policy.c_limit, which it does not show",
        ),
        (
            lambda f: widgets(f)[0]["column_looks"].append(dict(widgets(f)[0]["column_looks"][0])),
            "has two looks for t_policy.c_state",
        ),
        (
            lambda f: widgets(f)[0].update(
                column_looks=[{"table": "t_policy", "column": "c_state", "bold": False}]
            ),
            "look for t_policy.c_state changes nothing",
        ),
        (lambda f: widgets(f)[0]["column_looks"][0].update(width=23), "width"),
        # A column's look is for a column the Table shows, one for each, and changes something.
        (
            lambda f: widgets(f)[1]["column_looks"][0].update(column="c_state"),
            "has a look for t_layers.c_state, which it does not show",
        ),
        (
            lambda f: widgets(f)[1]["column_looks"].append(dict(widgets(f)[1]["column_looks"][0])),
            "has two looks for t_layers.c_limit",
        ),
        (
            lambda f: widgets(f)[1].update(
                column_looks=[{"table": "t_layers", "column": "c_limit", "align": "auto"}]
            ),
            "look for t_layers.c_limit changes nothing",
        ),
        # A column always shown with its Table changes nothing either, whatever its match;
        # its Show when is a component's.
        (
            lambda f: widgets(f)[1].update(
                column_looks=[
                    {
                        "table": "t_layers",
                        "column": "c_limit",
                        "show_when": {"match": "any", "conditions": []},
                    }
                ]
            ),
            "look for t_layers.c_limit changes nothing",
        ),
        (
            lambda f: widgets(f)[1]["column_looks"][0].update(show_when={"match": "none"}),
            "show_when.match",
        ),
        (lambda f: widgets(f)[1]["column_looks"][0].update(width=23), "width"),
        (lambda f: widgets(f)[1]["column_looks"][0].update(width=1001), "width"),
        (lambda f: widgets(f)[1]["column_looks"][0].update(align="justify"), "align"),
        (lambda f: widgets(f)[1]["column_looks"][0].update(cell_color="blue"), "cell_color"),
        (lambda f: widgets(f)[1]["column_looks"][0].update(shadow=True), "shadow"),
        # A Collection's spacers are each the number of its fields before it, in order and
        # none past its last field; only a Collection has them.
        (lambda f: widgets(f)[0].update(spacers=[2, 1]), "w_policy's spacers are out of order"),
        (lambda f: widgets(f)[0].update(spacers=[3]), "a spacer after 3 fields, but shows 2"),
        (lambda f: widgets(f)[0].update(spacers=[-1]), "spacers.0"),
        (lambda f: widgets(f)[1].update(spacers=[]), "spacers"),
        # Only a Table and a Collection have a frame and a density.
        (lambda f: widgets(f)[2].update(frame=False), "frame"),
        # The colours are the sheets', on the form: a component has none of its own.
        (lambda f: widgets(f)[1].update(font_color="#2563EB"), "font_color"),
        (lambda f: widgets(f)[0].update(background_color="#2563EB"), "background_color"),
        # A colour is #RRGGBB in capitals: one spelling, as the picker writes it.
        (lambda f: f["theme"].update(font_color="red"), "font_color"),
        (lambda f: f["theme"].update(input_cell_color="#ABC"), "input_cell_color"),
        (lambda f: f["theme"].update(sheet_color="#1e293b"), "sheet_color"),
        (lambda f: f["theme"].update(shadow_color="#000000"), "shadow_color"),
        (lambda f: widgets(f)[3].update(density="compact"), "density"),
        (lambda f: widgets(f)[2].update(chart_type="bar"), "chart_type"),
        (lambda f: widgets(f)[2].update(legend="inside"), "legend"),
        # A Chart has none of a Table's options, and a Table and a Collection none of a
        # Chart's.
        (lambda f: widgets(f)[2].update(striped=True), "striped"),
        (lambda f: widgets(f)[1].update(chart_type="line"), "chart_type"),
        (lambda f: widgets(f)[0].update(legend="top"), "legend"),
        # What labels a Chart's rows is held as a field is: a table and a column.
        (lambda f: widgets(f)[2].update(category={"table": "t_layers"}), "category"),
        (lambda f: widgets(f)[3].update(size="huge"), "size"),
        (lambda f: widgets(f)[3].update(align="justify"), "align"),
        # A Heading is its text: it has no title and shows no fields, and no other
        # component has its text, its description, its size or its alignment.
        (lambda f: widgets(f)[3].update(title="Cover"), "title"),
        (lambda f: widgets(f)[3].update(fields=[]), "fields"),
        (lambda f: widgets(f)[1].update(text="Layers"), "text"),
        (lambda f: widgets(f)[1].update(description="By layer"), "description"),
        (lambda f: widgets(f)[2].update(size="large"), "size"),
        (lambda f: widgets(f)[2].update(align="center"), "align"),
        (lambda f: f["schema"]["tables"][0]["columns"][0].update(type="money"), "type"),
        (
            lambda f: f["schema"]["tables"][0]["columns"][1].update(default_value=250000),
            "default_value",
        ),
        (
            lambda f: f["schema"]["tables"][0]["columns"][1].update(default_value=None),
            "default_value",
        ),
        (
            lambda f: f["schema"]["tables"][0]["columns"][1].update(number_format="yen"),
            "number_format",
        ),
        (
            lambda f: f["schema"]["tables"][0]["columns"][1].update(decimal_places=11),
            "decimal_places",
        ),
        (lambda f: f.update(pages=[]), "pages"),
        # A sheet is its sections, at least one, each at least a pixel tall, empty or not, and
        # holding its components inside it: a Heading 8px down and 40px tall needs 48px.
        (lambda f: f["pages"][0].update(sections=[]), "pages.0.sections"),
        (lambda f: f["pages"][0]["sections"][1].update(height=0), "pages.0.sections.1.height"),
        (
            lambda f: f["pages"][0]["sections"][1].update(widgets=[], height=0),
            "pages.0.sections.1.height",
        ),
        (
            lambda f: f["pages"][0]["sections"][1].update(widgets=[], height=-48),
            "pages.0.sections.1.height",
        ),
        # A sheet has a width, at least a pixel, holding its components inside it: the Chart,
        # 760 across and 480 wide, needs 1240.
        (lambda f: f["pages"][0].pop("width"), "pages.0.width"),
        (lambda f: f["pages"][0].update(width=0), "pages.0.width"),
        (lambda f: f["pages"][0]["sections"][0].update(colour="red"), "colour"),
        (lambda f: f["pages"][0]["sections"][1]["show_when"].update(match="most"), "match"),
        (lambda f: widgets(f)[0].update(type="slider"), "type"),
        (lambda f: f["pages"][0]["show_when"].update(match="none"), "match"),
        # At least a number of conditions is a whole number from 1, not a tick or text.
        (lambda f: f["pages"][0]["show_when"].update(match=0), "match"),
        (lambda f: f["pages"][0]["show_when"].update(match=True), "match"),
        (lambda f: f["pages"][0]["show_when"].update(match="2"), "match"),
        (lambda f: f["pages"][0]["show_when"].update(match=1.5), "match"),
        # A condition or a group is an object: anything else is refused as the shape is.
        (lambda f: f["pages"][0]["show_when"]["conditions"].append(None), "conditions"),
        (lambda f: f["pages"][0]["show_when"]["conditions"].append("x"), "conditions"),
        (
            lambda f: widgets(f)[1]["show_when"]["conditions"][0].update(op="has"),
            "op",
        ),
        # A condition's values are text as typed, as the sample's cells are.
        (
            lambda f: widgets(f)[1]["show_when"]["conditions"][0].update(values=[1]),
            "values",
        ),
        # A cell is held as typed, text or a tick: a number is another client's shape.
        (lambda f: f.update(sample={"t1": [{"c1": 1}]}), "sample"),
        (lambda f: f.update(sample={"t1": [{"c1": None}]}), "sample"),
        # Each sheet, section, component, table and column has an id of its own: a block
        # copied by hand or by a merge is refused, naming the id.
        (
            lambda f: f["pages"].append(json.loads(json.dumps(f["pages"][0]))),
            "Two sheets have the id 'page_1': each needs one of its own",
        ),
        (
            lambda f: f["pages"][0]["sections"][1].update(id="s_cover"),
            "Two sections have the id 's_cover'",
        ),
        (
            lambda f: widgets(f)[1].update(id="w_policy"),
            "Two components have the id 'w_policy'",
        ),
        (
            lambda f: f["schema"]["tables"][1].update(id="t_policy"),
            "Two tables have the id 't_policy'",
        ),
        (
            lambda f: f["schema"]["tables"][0]["columns"][3].update(
                id=f["schema"]["tables"][0]["columns"][2]["id"]
            ),
            "Two columns of policy_details have the id",
        ),
    ],
)
def test_an_unknown_field_or_a_value_outside_the_shape_is_refused(change: Any, where: str) -> None:
    broken = json.loads(json.dumps(FORM))
    change(broken)

    with pytest.raises(ValidationError) as caught:
        FormSpec.model_validate(broken)
    assert where in str(caught.value)


def test_a_column_look_holding_only_a_show_when_is_kept() -> None:
    form = json.loads(json.dumps(FORM))
    show_when = {
        "match": "any",
        "conditions": [
            {"table": "t_policy", "column": "c_state", "op": "equals", "values": ["NY"]}
        ],
    }
    widgets(form)[1]["column_looks"] = [
        {"table": "t_layers", "column": "c_limit", "show_when": show_when}
    ]

    look = widgets(FormSpec.model_validate(form).model_dump(mode="json"))[1]["column_looks"]

    assert [(entry["column"], entry["show_when"], entry["bold"]) for entry in look] == [
        ("c_limit", show_when, False)
    ]


def test_a_section_or_sheet_too_small_for_a_component_is_grown_as_it_is_read_and_said(
    tmp_path: Path,
) -> None:
    """A merge or a hand edit can leave a component below its section or past its sheet's
    width: the form reads, each grown to hold it as a resize past the edge would, nothing
    moved, the file as it was."""

    def shrink(layout: dict[str, Any]) -> None:
        layout["sections"]["s_equipment"]["height"] = 40
        layout["width"] = 1000

    document, written = read_after_layout_edit(tmp_path, FORM, shrink)

    assert document.spec.pages[0].sections[1].height == 48
    assert document.spec.pages[0].width == 1240
    assert document.repairs == (
        'Section "Equipment" grew from 40px to 48px tall to hold the Heading '
        '"Équipement des entrepreneurs".',
        'Sheet "Sheet 1" grew from 1000px to 1240px wide to hold the Chart "Limits".',
    )
    assert (tmp_path / "rating" / "workbench" / LAYOUT).read_bytes() == written
    # The form's own checks still refuse such geometry, so a save of it is refused.
    merged = json.loads(json.dumps(FORM))
    merged["pages"][0]["sections"][1]["height"] = 40
    with pytest.raises(ValidationError, match="widget w_title reaches 48px down"):
        FormSpec.model_validate(merged)
    narrow = json.loads(json.dumps(FORM))
    narrow["pages"][0]["width"] = 1239
    with pytest.raises(ValidationError, match="widget w_limits reaches 1240px across"):
        FormSpec.model_validate(narrow)


def test_a_section_as_tall_as_its_components_reach_holds_them() -> None:
    exact = json.loads(json.dumps(FORM))
    exact["pages"][0]["sections"][1]["height"] = 48

    assert read_document(exact).pages[0].sections[1].height == 48


def test_a_sheet_as_wide_as_its_components_reach_holds_them() -> None:
    exact = json.loads(json.dumps(FORM))
    exact["pages"][0]["width"] = 1240

    assert read_document(exact).pages[0].width == 1240


def test_a_collection_s_spacers_at_one_place_or_at_either_end_read() -> None:
    # Two together are a wider gap; one before the first field or after the last, a gap there.
    spaced = json.loads(json.dumps(FORM))
    widgets(spaced)[0]["spacers"] = [0, 1, 1, 2]

    spec = read_document(spaced)

    assert widgets(json.loads(render_form(spec)))[0]["spacers"] == [0, 1, 1, 2]


#: An Import of each format and an Export of each, every field written, as the view makes them.
IMPORTS: dict[str, dict[str, Any]] = {
    "csv": {"format": "csv", "delimiter": "semicolon"},
    "excel": {"format": "excel", "sheet": "Schedule", "header_row": 3},
    "sql": {
        "format": "sql",
        "connection": "policy_db",
        "query": "select limit_ from layers where policy = :reference",
        "parameters": [{"name": "reference", "table": "t_policy", "column": "c_state"}],
    },
}
EXPORTS: dict[str, dict[str, Any]] = {
    "csv": {
        "file_name": "{quote_reference}",
        "headers": "labels",
        "values": "formatted",
        "format": "csv",
        "delimiter": "tab",
    },
    "excel": {
        "file_name": "{quote_reference}-{quote_option}",
        "headers": "names",
        "values": "raw",
        "format": "excel",
    },
    "sql": {
        "format": "sql",
        "connection": "warehouse",
        "table_names": [{"table": "t_layers", "name": "quote_layers"}],
    },
}


def _with_buttons(*buttons: dict[str, Any]) -> dict[str, Any]:
    """The test form with a third section holding *buttons*, side by side."""
    form = json.loads(json.dumps(FORM))
    placed = [
        {"id": f"w_button_{at}", "x": 16 + 176 * at, "y": 8, "w": 160, "h": 40, **button}
        for at, button in enumerate(buttons)
    ]
    form["pages"][0]["sections"].append(
        {
            "id": "s_buttons",
            "title": "Buttons",
            "height": 56,
            "show_when": ALWAYS,
            "widgets": placed,
        }
    )
    return form


def _import(source: dict[str, Any], **rest: Any) -> dict[str, Any]:
    return {
        "type": "import",
        "label": "Import layers",
        "show_when": ALWAYS,
        "source": source,
        "table": "t_layers",
        "mode": "update",
        "column_names": [{"column": "c_limit", "name": "Limit (GBP)"}],
        **rest,
    }


def _export(target: dict[str, Any], **rest: Any) -> dict[str, Any]:
    return {
        "type": "export",
        "label": "Send to rating",
        "show_when": ALWAYS,
        "target": target,
        "tables": ["t_layers", "t_policy"],
        **rest,
    }


@pytest.mark.parametrize("format_", ["csv", "excel", "sql"])
def test_an_import_and_an_export_of_each_format_read_and_write_as_they_are(
    format_: str,
) -> None:
    form = _with_buttons(_import(IMPORTS[format_]), _export(EXPORTS[format_]))

    written = json.loads(render_form(FormSpec.model_validate(form)))
    assert (
        written["pages"][0]["sections"][2]["widgets"] == form["pages"][0]["sections"][2]["widgets"]
    )


def _without(fields: dict[str, Any], key: str) -> dict[str, Any]:
    return {name: value for name, value in fields.items() if name != key}


# ── Haute's quote_details table ───────────────────────────────────────────


def _with_details_table(change: Any = None) -> dict[str, Any]:
    form = json.loads(json.dumps(FORM))
    table = quote_details_table().model_dump(mode="json")
    if change is not None:
        change(table)
    form["schema"]["tables"].insert(0, table)
    return form


def test_a_form_holding_hautes_quote_details_reads_and_writes_as_it_is() -> None:
    held = _with_details_table(
        lambda table: table["columns"][2].update(label="Quote", help="As the broker calls it")
    )
    # Its broker's allowed values read from a params file, as any column it types can.
    held["schema"]["tables"][0]["columns"][4].update(
        options=["Marsh", "Aon"],
        options_source={"file": "brokers.csv", "column": "name"},
        default_value="Marsh",
    )
    held["schema"]["tables"][0]["columns"][0]["group"] = "Quote"
    held["schema"]["tables"][0]["columns"][10]["default_value"] = False

    spec = read_document(held)

    assert json.loads(render_form(spec)) == held
    assert [c.name for c in spec.data_schema.tables[0].columns] == [
        name for _, name, _ in QUOTE_DETAILS_COLUMNS
    ]
    assert with_quote_details(spec) is spec


#: quote_details as an earlier Haute made it: its reference under the name it had then,
#: valid_until and currency, which Haute makes no more, and option last.
EARLIER_DETAILS = [
    ("qd_reference", "reference", "str"),
    ("qd_quote_name", "quote_name", "str"),
    ("qd_insured", "insured", "str"),
    ("qd_broker", "broker", "str"),
    ("qd_inception_date", "inception_date", "date"),
    ("qd_expiry_date", "expiry_date", "date"),
    ("qd_valid_until", "valid_until", "date"),
    ("qd_currency", "currency", "str"),
    ("qd_stage", "stage", "str"),
    ("qd_assignee", "assignee", "str"),
    ("qd_archived", "archived", "bool"),
    ("qd_is_renewal", "is_renewal", "bool"),
    ("qd_option", "quote_option", "int"),
]


def _earlier(count: int) -> dict[str, Any]:
    """A form holding the first *count* of an earlier Haute's quote_details columns, its
    insured labelled and its currency taking allowed values, as a project gave them."""
    columns = [
        {"id": column_id, "name": name, "type": kind}
        for column_id, name, kind in EARLIER_DETAILS[:count]
    ]
    columns[2]["label"] = "Insured name"
    columns[7].update(options=["GBP", "USD"], default_value="GBP")
    return _with_details_table(lambda table: table.update(columns=columns))


@pytest.mark.parametrize("count", [11, 12, 13], ids=["before-is-renewal", "before-option", "wb30"])
def test_a_form_saved_by_an_earlier_haute_takes_todays_quote_details_by_id(count: int) -> None:
    held = _earlier(count)
    as_saved = json.dumps(held)

    spec = read_document(json.loads(as_saved))

    # Today's columns in today's order: the reference renamed, valid_until and currency
    # dropped, the missing ones added, what the project gave the kept ones kept.
    details = spec.data_schema.tables[0]
    assert [(c.id, c.name, c.type) for c in details.columns] == list(QUOTE_DETAILS_COLUMNS)
    assert details.columns[3].label == "Insured name"
    assert details.columns[15] == SchemaColumn(
        id="qd_haute_version", name="haute_version", type="str"
    )
    written = json.loads(render_form(spec))["schema"]["tables"][0]["columns"]
    assert not {"valid_until", "currency"} & {column["name"] for column in written}
    # The data it was read from is left as it was.
    assert json.dumps(held) == as_saved


def _without_identity() -> dict[str, Any]:
    form = _with_details_table()
    for table in form["schema"]["tables"][1:]:
        table["columns"] = table["columns"][2:]
    return form


def test_every_table_but_quote_details_takes_the_quote_identity_at_its_head() -> None:
    held = _without_identity()
    as_saved = json.dumps(held)

    spec = FormSpec.model_validate(held)

    identity = [
        SchemaColumn(id="quote_reference", name="quote_reference", type="str"),
        SchemaColumn(id="quote_option", name="quote_option", type="int"),
    ]
    details, *others = spec.data_schema.tables
    assert [table.columns[:2] for table in others] == [identity] * len(others)
    assert [c.name for c in others[0].columns[2:]] == ["state", "exposure"]
    assert not any(c.id.startswith("quote_") for c in details.columns)
    # The data it was read from is left as it was.
    assert json.dumps(held) == as_saved


@pytest.mark.parametrize(
    "change",
    [
        lambda columns: columns[0].update(name="ref"),
        lambda columns: columns[1].update(type="float"),
        lambda columns: columns.insert(0, columns.pop(2)),
        lambda columns: columns[0].update(label="Reference"),
        lambda columns: columns[1].update(help="Which option"),
        lambda columns: columns[1].update(required=True),
        lambda columns: columns[0].update(key=True),
        lambda columns: columns.pop(1),
    ],
)
def test_the_quote_identity_of_a_table_is_haute_s_and_locked(change: Any) -> None:
    form = json.loads(json.dumps(FORM))
    change(form["schema"]["tables"][1]["columns"])

    with pytest.raises(
        ValidationError, match="layers leads with the quote's quote_reference and quote_option"
    ):
        FormSpec.model_validate(form)


def test_a_table_with_its_own_quote_reference_or_option_keeps_it_and_the_clash_is_named() -> None:
    held = _without_identity()
    policy = held["schema"]["tables"][1]
    # Its own quote_reference first, Text, as the identity's would be, and a quote_option
    # further on; a column of its own named reference or option is no clash.
    policy["columns"].insert(0, column("quote_reference"))
    policy["columns"].append(column("quote_option", "float"))
    policy["columns"].append(column("option"))

    spec = FormSpec.model_validate(held)

    table = spec.data_schema.tables[1]
    assert [c.id for c in table.columns] == [
        "c_quote_reference",
        "c_state",
        "c_exposure",
        "c_quote_option",
        "c_option",
    ]
    assert identity_clashes(spec) == [
        f"policy_details.{name} has a name Haute keeps for the quote's identity in every "
        "table: rename it, and the table takes quote_reference and quote_option at the next "
        "save"
        for name in ("quote_reference", "quote_option")
    ]
    # Renamed, the columns keep their ids, and so their values, and the table takes the
    # identity when the form is next read or saved.
    policy["columns"][0]["name"] = "broker_reference"
    policy["columns"][-2]["name"] = "option_load"
    renamed = FormSpec.model_validate(held)
    assert [c.id for c in renamed.data_schema.tables[1].columns] == [
        "quote_reference",
        "quote_option",
        "c_quote_reference",
        "c_state",
        "c_exposure",
        "c_quote_option",
        "c_option",
    ]
    assert identity_clashes(renamed) == []


def test_a_form_saved_by_any_earlier_haute_takes_on_the_columns_added_since(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Today's columns, some left out and some out of order, as a later Haute with one more
    # reads them: by id, in its order.
    today = _with_details_table()
    fewer = _with_details_table(lambda table: table.update(columns=table["columns"][:9]))
    swapped = _with_details_table(lambda table: table["columns"].insert(3, table["columns"].pop(9)))
    later = (*QUOTE_DETAILS_COLUMNS, ("qd_renewal_of", "renewal_of", "str"))
    monkeypatch.setattr("haute._workbench_form.QUOTE_DETAILS_COLUMNS", later)

    for held in (today, fewer, swapped):
        details = FormSpec.model_validate(held).data_schema.tables[0]
        assert [(c.id, c.name, c.type) for c in details.columns] == list(later)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda t: t.update(rows="many"), "is Haute's: a one-row input table"),
        (lambda t: t.update(role="output"), "is Haute's: a one-row input table"),
        # A column Haute does not make is no Haute's: left as it is, and refused.
        (
            lambda t: t["columns"].append({"id": "qd_extra", "name": "extra", "type": "str"}),
            "is Haute's: a one-row input table",
        ),
        (lambda t: t["columns"][7].update(type="str"), "is Haute's: a one-row input table"),
        (lambda t: t["columns"][2].update(name="title"), "is Haute's: a one-row input table"),
        (lambda t: t["columns"][5].update(type="str"), "is Haute's: a one-row input table"),
        (
            lambda t: t["columns"][2].update(row_per_value=True),
            "is Haute's: a one-row input table",
        ),
        (
            lambda t: t["columns"][0].update(required=True),
            "reference is set by the underwriter app",
        ),
        (
            lambda t: t["columns"][12].update(default_value="ana@example.com"),
            "created_by is set by the underwriter app and takes no rules or default",
        ),
        (
            lambda t: t["columns"][15].update(options=["1.0.0"]),
            "haute_version is set by the underwriter app",
        ),
        (
            lambda t: t["columns"][8].update(options=["draft"]),
            "stage is set by the underwriter app",
        ),
        (
            lambda t: t["columns"][8].update(
                options_source={"file": "stages.csv", "column": "stage"}
            ),
            "stage is set by the underwriter app",
        ),
        (
            lambda t: t["columns"][9].update(default_value="Underwriting"),
            "assignee is set by the underwriter app and takes no rules or default",
        ),
        (
            lambda t: t["columns"][10].update(default_value=True),
            "archived is set by the underwriter app and takes no rules or default",
        ),
        (lambda t: t.update(id="t_details"), "is Haute's own table of a quote's details"),
    ],
    ids=[
        "many-rows",
        "output",
        "unknown-column",
        "is-renewal-retyped",
        "renamed",
        "retyped",
        "row-per-value",
        "rule",
        "created-by-default",
        "haute-version-options",
        "options",
        "options-source",
        "default",
        "ticked-default",
        "own-id",
    ],
)
def test_a_quote_details_table_that_is_not_hautes_is_refused(change: Any, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        FormSpec.model_validate(_with_details_table(change))
