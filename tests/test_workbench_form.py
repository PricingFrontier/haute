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
    DAYS_MAX,
    HEADER_ROW_MAX,
    IDENTITY_COLUMNS,
    QUOTE_DETAILS_COLUMNS,
    QUOTE_DETAILS_MANAGED,
    QUOTE_DETAILS_NAME,
    QUOTE_DETAILS_REFUSAL,
    TABLE_ROWS_MAX,
    FormDocument,
    FormSpec,
    HeadingWidget,
    SchemaColumn,
    SchemaTable,
    blank_form,
    form_revision,
    holds_identity,
    identity_clashes,
    quote_details_table,
    render_form,
    with_quote_details,
)
from tests._workbench_helpers import form_files, write_files


def column(name: str, type_: str = "str", **rest: Any) -> dict[str, Any]:
    """A column as a document holds it, every field written: known by its name in its
    table (READ-23)."""
    return {
        "name": name,
        "type": type_,
        "label": "",
        "group": "",
        "required": False,
        "min": None,
        "max": None,
        "options": [],
        "options_source": None,
        "index": None,
        "help": "",
        "placeholder": "",
        "default_value": "",
        "number_format": "general",
        "decimal_places": None,
        "use_grouping": True,
        **rest,
    }


#: The quote's reference and option, which every table but quote_details leads with.
IDENTITY: list[dict[str, Any]] = [column("quote_reference"), column("quote_option", "int")]


# The test project's shape: one-row and many-row input tables, an index column, an output
# table, a Collection, a Table and a Chart in one section and a Heading in a second, the
# Table and the second section each shown while a condition holds, and a sample typed into
# the Collection; as a release ships it and the quote store keeps it, each table and column
# known by its name and each component by its own, with no id.
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
                "name": "policy_details",
                "rows": "one",
                "inputs": [
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
                "outputs": [],
            },
            {
                "name": "layers",
                "rows": "many",
                "inputs": [
                    *IDENTITY,
                    column("layer", "int", index="numbered"),
                    column("limit", "float"),
                    column("layer_name"),
                ],
                "outputs": [],
            },
            {
                "name": "pricing_output",
                "rows": "one",
                "inputs": [],
                "outputs": [*IDENTITY, column("premium", "float")],
            },
        ]
    },
    "pages": [
        {
            "title": "Sheet 1",
            "width": 1280,
            "show_when": ALWAYS,
            "sections": [
                {
                    "title": "Cover",
                    "height": 376,
                    "show_when": ALWAYS,
                    "widgets": [
                        {
                            "name": "policy",
                            "type": "collection",
                            "x": 16,
                            "y": 16,
                            "w": 720,
                            "h": 120,
                            "title": "Policy",
                            "fields": [
                                {"table": "policy_details", "column": "state"},
                                {"table": "pricing_output", "column": "premium"},
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
                                    "table": "policy_details",
                                    "column": "state",
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
                                                "table": "policy_details",
                                                "column": "exposure",
                                                "op": "filled",
                                                "values": [],
                                                "field": None,
                                                "days": 0,
                                            }
                                        ],
                                    },
                                }
                            ],
                            "spacers": [1],
                        },
                        {
                            "name": "layers",
                            "type": "tableInput",
                            "x": 16,
                            "y": 160,
                            "w": 720,
                            "h": 200,
                            "title": "Layers",
                            "fields": [{"table": "layers", "column": "limit"}],
                            "show_when": {
                                "match": "any",
                                "conditions": [
                                    {
                                        "table": "policy_details",
                                        "column": "state",
                                        "op": "one_of",
                                        "values": ["CA", "NY"],
                                        "field": None,
                                        "days": 0,
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
                                    "table": "layers",
                                    "column": "limit",
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
                            "name": "limits",
                            "type": "chart",
                            "x": 760,
                            "y": 16,
                            "w": 480,
                            "h": 280,
                            "title": "Limits",
                            "fields": [{"table": "layers", "column": "limit"}],
                            "show_when": ALWAYS,
                            # The layers' names label the rows, drawn as lines across, stacked, the
                            # legend on the right and each value labelled.
                            "category": {"table": "layers", "column": "layer_name"},
                            "chart_type": "line",
                            "orientation": "horizontal",
                            "stacked": True,
                            "legend": "right",
                            "data_labels": True,
                        },
                    ],
                },
                {
                    "title": "Equipment",
                    "height": 64,
                    # Only for New York: its Heading hides with it.
                    "show_when": {
                        "match": "all",
                        "conditions": [
                            {
                                "table": "policy_details",
                                "column": "state",
                                "op": "equals",
                                "values": ["NY"],
                                "field": None,
                                "days": 0,
                            }
                        ],
                    },
                    "widgets": [
                        {
                            "name": "equipment",
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
    "sample": {"policy_details": [{"state": "NY", "exposure": "$100,000"}]},
}


def widgets(form: dict[str, Any]) -> list[dict[str, Any]]:
    """The test form's components, section by section: the Collection, the Table, the Chart
    and the Heading."""
    return [widget for section in form["pages"][0]["sections"] for widget in section["widgets"]]


def read_document(document: dict[str, Any]) -> FormSpec:
    """*document* read as the form is wherever it is held whole (a save, a release, the
    quote store): Haute's quote_details first."""
    return with_quote_details(FormSpec.model_validate(document))


#: The sheet the test form's folder files, by its title "Sheet 1": its width, its sections'
#: heights and where each component sits.
SHEET = "sheets/sheet_1.json"


def read_after_sheet_edit(
    project: Path, form: dict[str, Any], change: Any
) -> tuple[FormDocument, bytes]:
    """*form* saved as its folder, its one sheet's file then changed by *change*, as a merge
    or a hand edit can, and the folder read back; with the sheet file's bytes."""
    folder = project / "rating" / "workbench"
    files = form_files(form, folder)
    sheet = json.loads(files[SHEET])
    change(sheet)
    files[SHEET] = (json.dumps(sheet, indent=2) + "\n").encode("utf-8")
    write_files(folder, files)
    config = WorkbenchConfig(enabled=True, project_root=project, form="rating/workbench")
    return read_form_document(config), files[SHEET]


@pytest.mark.meta
def test_the_editor_names_the_quote_details_columns_the_server_does() -> None:
    """The view's columns of quote_details, whose `QUOTE_DETAILS` map is how the app names
    each one it reads, and the columns the app sets, named through that map."""
    utils = Path(__file__).resolve().parents[1] / "frontend" / "src" / "utils"
    identity = (utils / "quoteIdentity.ts").read_text(encoding="utf-8")

    def pairs(name: str) -> tuple[tuple[str, str], ...]:
        """The view's list of columns *name*, each its name and its type."""
        held = re.search(rf"export const {name}\b[^=]*= \[(.*?)\n\]", identity, re.DOTALL)
        assert held is not None, f"quoteIdentity.ts has no {name}"
        return tuple(re.findall(r'\["(\w+)", "(\w+)"\]', held.group(1)))

    managed = re.search(r"QUOTE_DETAILS_MANAGED[^=]*= new Set\(\[([^\]]*)\]\)", identity)

    # Each column by its name and type, as the server holds them (READ-23), and the
    # quote's identity the same.
    assert pairs("QUOTE_DETAILS_COLUMNS") == QUOTE_DETAILS_COLUMNS
    assert pairs("IDENTITY_COLUMNS") == IDENTITY_COLUMNS
    assert managed is not None
    named = re.findall(r"QUOTE_DETAILS\.(\w+)", managed.group(1))
    assert set(named) == QUOTE_DETAILS_MANAGED
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
    assert int(constant("utils/sheetVisibility.ts", "DAYS_MAX")) == DAYS_MAX


def test_the_blank_form_is_one_empty_sheet_named_after_the_project() -> None:
    text = render_form(blank_form("motor"))

    # The whole sheet is one section until another is added, as tall as the view's least
    # sheet height less the room it leaves below the sections, on a sheet 1280 wide; neither
    # holds an id, which a read gives from the sheet's name and the section's title.
    section = {"title": "Section 1", "height": 560}
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
    # Read, it holds Haute's quote_details table first, as every form read whole does, and
    # each part takes the id its sheet's name, its section's title and its name give it.
    document = read_document(json.loads(data))
    assert document == with_quote_details(spec)
    assert document.data_schema.tables[0].name == QUOTE_DETAILS_NAME
    assert [widget.id for section in document.pages[0].sections for widget in section.widgets] == [
        "sheet_1/Cover/policy",
        "sheet_1/Cover/layers",
        "sheet_1/Cover/limits",
        "sheet_1/Equipment/equipment",
    ]
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
        for col in [*table["inputs"], *table["outputs"]]:
            del col["index"]
            for field in display:
                del col[field]
    sparse["schema"]["tables"][1]["inputs"][2]["index"] = "numbered"
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
        None,
        None,
        "numbered",
        None,
        None,
    ]
    written = json.loads(render_form(spec))
    assert written["sample"] == {}
    assert all(
        "index" in c for t in written["schema"]["tables"] for c in [*t["inputs"], *t["outputs"]]
    )
    assert written["schema"]["tables"][1]["inputs"][3] == column("exposure", "float", min=0.0)
    assert written["schema"]["tables"][1]["inputs"][2]["default_value"] == ""
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
    assert list(heading) == "name x y w h type text description show_when size align".split()
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
            lambda f: widgets(f)[0]["column_looks"][0].update(column="limit"),
            "has a look for policy_details.limit, which it does not show",
        ),
        (
            lambda f: widgets(f)[0]["column_looks"].append(dict(widgets(f)[0]["column_looks"][0])),
            "has two looks for policy_details.state",
        ),
        (
            lambda f: widgets(f)[0].update(
                column_looks=[{"table": "policy_details", "column": "state", "bold": False}]
            ),
            "look for policy_details.state changes nothing",
        ),
        (lambda f: widgets(f)[0]["column_looks"][0].update(width=23), "width"),
        # A column's look is for a column the Table shows, one for each, and changes something.
        (
            lambda f: widgets(f)[1]["column_looks"][0].update(column="state"),
            "has a look for layers.state, which it does not show",
        ),
        (
            lambda f: widgets(f)[1]["column_looks"].append(dict(widgets(f)[1]["column_looks"][0])),
            "has two looks for layers.limit",
        ),
        (
            lambda f: widgets(f)[1].update(
                column_looks=[{"table": "layers", "column": "limit", "align": "auto"}]
            ),
            "look for layers.limit changes nothing",
        ),
        # A column always shown with its Table changes nothing either, whatever its match;
        # its Show when is a component's.
        (
            lambda f: widgets(f)[1].update(
                column_looks=[
                    {
                        "table": "layers",
                        "column": "limit",
                        "show_when": {"match": "any", "conditions": []},
                    }
                ]
            ),
            "look for layers.limit changes nothing",
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
        (
            lambda f: widgets(f)[0].update(spacers=[2, 1]),
            "sheet_1/Cover/policy's spacers are out of order",
        ),
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
        (lambda f: widgets(f)[2].update(category={"table": "layers"}), "category"),
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
        (lambda f: f["schema"]["tables"][0]["inputs"][0].update(type="money"), "type"),
        (
            lambda f: f["schema"]["tables"][0]["inputs"][1].update(default_value=250000),
            "default_value",
        ),
        (
            lambda f: f["schema"]["tables"][0]["inputs"][1].update(default_value=None),
            "default_value",
        ),
        (
            lambda f: f["schema"]["tables"][0]["inputs"][1].update(number_format="yen"),
            "number_format",
        ),
        (
            lambda f: f["schema"]["tables"][0]["inputs"][1].update(decimal_places=11),
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
        # Days added to another Date are a whole number, a hundred years either way at most,
        # and the field a condition is tested against names a table and a column.
        (
            lambda f: widgets(f)[1]["show_when"]["conditions"][0].update(days=36_501),
            "days",
        ),
        (
            lambda f: widgets(f)[1]["show_when"]["conditions"][0].update(days=-36_501),
            "days",
        ),
        (
            lambda f: widgets(f)[1]["show_when"]["conditions"][0].update(days=1.5),
            "days",
        ),
        (
            lambda f: widgets(f)[1]["show_when"]["conditions"][0].update(
                field="policy_details.state"
            ),
            "field",
        ),
        # A cell is held as typed, text or a tick: a number is another client's shape.
        (lambda f: f.update(sample={"policy_details": [{"state": 1}]}), "sample"),
        (lambda f: f.update(sample={"policy_details": [{"state": None}]}), "sample"),
        # A sheet is known by its name, a section by its title on its sheet and a component
        # by its name (READ-24): a block copied by hand or by a merge, two of one, is
        # refused, naming it. Two of one name in two sections are the folder's to refuse,
        # their files being one (tests/test_workbench_folder.py).
        (
            lambda f: f["pages"].append(json.loads(json.dumps(f["pages"][0]))),
            "Two sheets have the id 'sheet_1': each needs one of its own",
        ),
        (
            lambda f: f["pages"][0]["sections"][1].update(title="Cover"),
            "Two sections have the id 'sheet_1/Cover'",
        ),
        (
            lambda f: widgets(f)[1].update(name="policy"),
            "Two components have the id 'sheet_1/Cover/policy'",
        ),
        # Every component has a name, the builder's, which names its file; version 1's
        # number is no field of the form.
        (lambda f: widgets(f)[3].pop("name"), "widgets.0.heading.name\n  Field required"),
        (lambda f: widgets(f)[0].update(number=1), "widgets.0.collection.number\n  Extra inputs"),
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
            {
                "table": "policy_details",
                "column": "state",
                "op": "equals",
                "values": ["NY"],
                "field": None,
                "days": 0,
            }
        ],
    }
    widgets(form)[1]["column_looks"] = [
        {"table": "layers", "column": "limit", "show_when": show_when}
    ]

    look = widgets(FormSpec.model_validate(form).model_dump(mode="json"))[1]["column_looks"]

    assert [(entry["column"], entry["show_when"], entry["bold"]) for entry in look] == [
        ("limit", show_when, False)
    ]


def test_a_section_or_sheet_too_small_for_a_component_is_grown_as_it_is_read_and_said(
    tmp_path: Path,
) -> None:
    """A merge or a hand edit can leave a component below its section or past its sheet's
    width: the form reads, each grown to hold it as a resize past the edge would, nothing
    moved, the file as it was."""

    def shrink(sheet: dict[str, Any]) -> None:
        sheet["sections"][1]["height"] = 40
        sheet["width"] = 1000

    document, written = read_after_sheet_edit(tmp_path, FORM, shrink)

    assert document.spec.pages[0].sections[1].height == 48
    assert document.spec.pages[0].width == 1240
    assert document.repairs == (
        'Section "Equipment" grew from 40px to 48px tall to hold the Heading '
        '"Équipement des entrepreneurs".',
        'Sheet "Sheet 1" grew from 1000px to 1240px wide to hold the Chart "Limits".',
    )
    assert (tmp_path / "rating" / "workbench" / SHEET).read_bytes() == written
    # The form's own checks still refuse such geometry, so a save of it is refused.
    merged = json.loads(json.dumps(FORM))
    merged["pages"][0]["sections"][1]["height"] = 40
    with pytest.raises(
        ValidationError, match="widget sheet_1/Equipment/equipment reaches 48px down"
    ):
        FormSpec.model_validate(merged)
    narrow = json.loads(json.dumps(FORM))
    narrow["pages"][0]["width"] = 1239
    with pytest.raises(ValidationError, match="widget sheet_1/Cover/limits reaches 1240px across"):
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
        "parameters": [{"name": "reference", "table": "policy_details", "column": "state"}],
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
        "table_names": [{"table": "layers", "name": "quote_layers"}],
    },
}


def _with_buttons(*buttons: dict[str, Any]) -> dict[str, Any]:
    """The test form with a third section holding *buttons*, side by side, each named by its
    kind and its place in turn."""
    form = json.loads(json.dumps(FORM))
    placed = [
        {
            "name": f"{button['type']}_{at + 1}",
            "x": 16 + 176 * at,
            "y": 8,
            "w": 160,
            "h": 40,
            **button,
        }
        for at, button in enumerate(buttons)
    ]
    form["pages"][0]["sections"].append(
        {
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
        "table": "layers",
        "mode": "update",
        "column_names": [{"column": "limit", "name": "Limit (GBP)"}],
        **rest,
    }


def _export(target: dict[str, Any], **rest: Any) -> dict[str, Any]:
    return {
        "type": "export",
        "label": "Send to rating",
        "show_when": ALWAYS,
        "target": target,
        "tables": ["layers", "policy_details"],
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
        lambda table: table["inputs"][2].update(label="Quote", help="As the broker calls it")
    )
    # Its broker's allowed values read from a params file, as any column it types can.
    held["schema"]["tables"][0]["inputs"][4].update(
        options=["Marsh", "Aon"],
        options_source={"file": "brokers.csv", "column": "name"},
        default_value="Marsh",
    )
    held["schema"]["tables"][0]["inputs"][0]["group"] = "Quote"
    held["schema"]["tables"][0]["inputs"][10]["default_value"] = False

    spec = read_document(held)

    assert json.loads(render_form(spec)) == held
    assert [c.name for c in spec.data_schema.tables[0].columns] == [
        name for name, _ in QUOTE_DETAILS_COLUMNS
    ]
    assert with_quote_details(spec) is spec


#: quote_details as an earlier Haute made it: its reference under the name it had then,
#: valid_until and currency, which Haute makes no more, and option last.
EARLIER_DETAILS = [
    ("reference", "str"),
    ("quote_name", "str"),
    ("insured", "str"),
    ("broker", "str"),
    ("inception_date", "date"),
    ("expiry_date", "date"),
    ("valid_until", "date"),
    ("currency", "str"),
    ("stage", "str"),
    ("assignee", "str"),
    ("archived", "bool"),
    ("is_renewal", "bool"),
    ("quote_option", "int"),
]


def _earlier(count: int) -> dict[str, Any]:
    """A form holding the first *count* of an earlier Haute's quote_details columns, its
    insured labelled and its currency taking allowed values, as a project gave them."""
    columns = [{"name": name, "type": kind} for name, kind in EARLIER_DETAILS[:count]]
    columns[2]["label"] = "Insured name"
    columns[7].update(options=["GBP", "USD"], default_value="GBP")
    return _with_details_table(lambda table: table.update(inputs=columns))


@pytest.mark.parametrize("count", [11, 12, 13], ids=["before-is-renewal", "before-option", "wb30"])
def test_a_form_saved_by_an_earlier_haute_takes_todays_quote_details_by_name(count: int) -> None:
    held = _earlier(count)
    as_saved = json.dumps(held)

    spec = read_document(json.loads(as_saved))

    # Today's columns in today's order, each found by its name: the reference renamed,
    # valid_until and currency dropped, the missing ones added, what the project gave the kept
    # ones kept.
    details = spec.data_schema.tables[0]
    assert [(c.name, c.type) for c in details.columns] == list(QUOTE_DETAILS_COLUMNS)
    assert details.columns[3].label == "Insured name"
    assert details.columns[15] == SchemaColumn(name="haute_version", type="str")
    written = json.loads(render_form(spec))["schema"]["tables"][0]["inputs"]
    assert not {"valid_until", "currency"} & {column["name"] for column in written}
    # The data it was read from is left as it was.
    assert json.dumps(held) == as_saved


def _without_identity() -> dict[str, Any]:
    """The form as an earlier Haute wrote it: no table led by the quote's identity."""
    form = _with_details_table()
    for table in form["schema"]["tables"][1:]:
        for block in ("inputs", "outputs"):
            if [c["name"] for c in table[block][:2]] == ["quote_reference", "quote_option"]:
                table[block] = table[block][2:]
    return form


def test_every_table_but_quote_details_takes_the_quote_identity_at_its_head() -> None:
    held = _without_identity()
    as_saved = json.dumps(held)

    spec = FormSpec.model_validate(held)

    identity = [SchemaColumn(name=name, type=kind) for name, kind in IDENTITY_COLUMNS]
    details, *others = spec.data_schema.tables
    assert [table.columns[:2] for table in others] == [identity] * len(others)
    assert [c.name for c in others[0].columns[2:]] == ["state", "exposure"]
    # quote_details holds its own reference and option among its columns, and takes no
    # identity at its head besides.
    assert [(c.name, c.type) for c in details.columns] == list(QUOTE_DETAILS_COLUMNS)
    # The data it was read from is left as it was.
    assert json.dumps(held) == as_saved


@pytest.mark.parametrize(
    ("change", "clashing"),
    [
        (lambda columns: columns[0].update(name="ref"), ["quote_option"]),
        (lambda columns: columns[1].update(type="float"), ["quote_reference", "quote_option"]),
        (lambda columns: columns.insert(0, columns.pop(2)), ["quote_reference", "quote_option"]),
        (lambda columns: columns[0].update(label="Reference"), ["quote_reference", "quote_option"]),
        (
            lambda columns: columns[1].update(help="Which option"),
            ["quote_reference", "quote_option"],
        ),
        (lambda columns: columns[1].update(required=True), ["quote_reference", "quote_option"]),
        (
            lambda columns: columns[0].update(placeholder="Q-"),
            ["quote_reference", "quote_option"],
        ),
        (lambda columns: columns.pop(1), ["quote_reference"]),
    ],
    ids=[
        "renamed",
        "retyped",
        "moved",
        "labelled",
        "given-help",
        "required",
        "given-a-placeholder",
        "removed",
    ],
)
def test_a_quote_identity_changed_is_the_table_s_own_and_named_a_clash(
    change: Any, clashing: list[str]
) -> None:
    # The identity is a table's first two columns exactly as Haute makes them (READ-23):
    # one renamed, retyped, moved, removed or given anything of its own leaves the table's
    # columns of either name its own, kept as they are, which building the app and deploying
    # refuse until they are renamed.
    form = json.loads(json.dumps(FORM))
    change(form["schema"]["tables"][1]["inputs"])

    spec = FormSpec.model_validate(form)

    layers = spec.data_schema.tables[1]
    assert [c.name for c in layers.columns] == [
        c["name"] for c in form["schema"]["tables"][1]["inputs"]
    ]
    assert not holds_identity(layers)
    assert identity_clashes(spec) == [
        f"layers.{name} has a name Haute keeps for the quote's identity in every table: "
        "rename it, and the table takes quote_reference and quote_option at the next save"
        for name in clashing
    ]
    # The other tables lead with the identity as Haute makes it.
    assert [holds_identity(table) for table in spec.data_schema.tables] == [True, False, True]


def test_a_table_with_its_own_quote_reference_or_option_keeps_it_and_the_clash_is_named() -> None:
    held = _without_identity()
    policy = held["schema"]["tables"][1]
    # Its own quote_reference first, Text, as the identity's would be, and a quote_option
    # further on; a column of its own named reference or option is no clash.
    policy["inputs"].insert(0, column("quote_reference"))
    policy["inputs"].append(column("quote_option", "float"))
    policy["inputs"].append(column("option"))

    spec = FormSpec.model_validate(held)

    table = spec.data_schema.tables[1]
    assert [c.name for c in table.columns] == [
        "quote_reference",
        "state",
        "exposure",
        "quote_option",
        "option",
    ]
    assert not holds_identity(table)
    assert identity_clashes(spec) == [
        f"policy_details.{name} has a name Haute keeps for the quote's identity in every "
        "table: rename it, and the table takes quote_reference and quote_option at the next "
        "save"
        for name in ("quote_reference", "quote_option")
    ]
    # Renamed, the table takes the identity at its head when the form is next read or
    # saved, its own columns after it under their new names.
    policy["inputs"][0]["name"] = "broker_reference"
    policy["inputs"][-2]["name"] = "option_load"
    renamed = FormSpec.model_validate(held)
    assert [c.name for c in renamed.data_schema.tables[1].columns] == [
        "quote_reference",
        "quote_option",
        "broker_reference",
        "state",
        "exposure",
        "option_load",
        "option",
    ]
    assert holds_identity(renamed.data_schema.tables[1])
    assert identity_clashes(renamed) == []


def _one_table_form(table: dict[str, Any]) -> dict[str, Any]:
    """A form of one blank sheet whose schema holds *table*, named t."""
    return {
        "name": "x",
        "schema": {"tables": [{"name": "t", **table}]},
        "pages": [
            {
                "title": "Sheet 1",
                "width": 1280,
                "sections": [{"title": "Section 1", "height": 560, "widgets": []}],
            }
        ],
    }


def _blocks(table: SchemaTable) -> tuple[list[str], list[str]]:
    return [c.name for c in table.inputs], [c.name for c in table.outputs]


@pytest.mark.parametrize(
    ("table", "blocks"),
    [
        pytest.param(
            {"inputs": [column("state")], "outputs": [column("premium", "float")]},
            (["quote_reference", "quote_option", "state"], ["premium"]),
            id="a table of both blocks: its inputs",
        ),
        pytest.param(
            {"outputs": [column("premium", "float")]},
            ([], ["quote_reference", "quote_option", "premium"]),
            id="a table of outputs alone: its outputs",
        ),
        pytest.param(
            {"inputs": [column("state")]},
            (["quote_reference", "quote_option", "state"], []),
            id="a table of inputs alone: its inputs",
        ),
        pytest.param(
            {},
            (["quote_reference", "quote_option"], []),
            id="a table of neither: its inputs",
        ),
        # An edit gave a table of outputs alone its first input: the identity moves from the
        # head of its outputs to the head of its inputs.
        pytest.param(
            {"inputs": [column("state")], "outputs": [*IDENTITY, column("premium", "float")]},
            (["quote_reference", "quote_option", "state"], ["premium"]),
            id="moved to the inputs as a table gains its first input",
        ),
        # An edit took a table's last input: the identity moves to the head of its outputs.
        pytest.param(
            {"inputs": [*IDENTITY], "outputs": [column("premium", "float")]},
            ([], ["quote_reference", "quote_option", "premium"]),
            id="moved to the outputs as a table loses its last input",
        ),
        # Held at both heads, as a merge of the two edits can leave it: given once.
        pytest.param(
            {"inputs": [*IDENTITY, column("state")], "outputs": [*IDENTITY, column("premium")]},
            (["quote_reference", "quote_option", "state"], ["premium"]),
            id="at both heads given once",
        ),
    ],
)
def test_the_quote_identity_heads_a_tables_first_block(
    table: dict[str, Any], blocks: tuple[list[str], list[str]]
) -> None:
    data = _one_table_form(table)
    as_given = json.dumps(data)

    spec = FormSpec.model_validate(data)

    (held,) = spec.data_schema.tables
    assert _blocks(held) == blocks
    assert holds_identity(held)
    assert held.own_inputs + held.own_outputs == [
        c for c in held.columns if c.name not in ("quote_reference", "quote_option")
    ]
    # The data it was read from is left as it was, and what is read reads the same again.
    assert json.dumps(data) == as_given
    assert FormSpec.model_validate(spec.model_dump(mode="json")) == spec


#: A schema table's columns that are its key or its index where the table cannot have one,
#: and what the refusal says.
KEY_REFUSALS = [
    pytest.param(
        {"rows": "many", "inputs": [column("value", "float")]},
        "t has many rows, so it needs one index",
        id="a many-row input table with no index",
    ),
    pytest.param(
        {"rows": "many", "outputs": [column("premium", "float")]},
        "t has many rows, so it needs one index",
        id="a many-row table of outputs alone with no index",
    ),
    pytest.param(
        {"rows": "many"},
        "t has many rows, so it needs one index",
        id="an empty many-row table with no index",
    ),
    pytest.param(
        {
            "rows": "many",
            "inputs": [
                column("line", "int", index="numbered"),
                column("row", "int", index="numbered"),
                column("value", "float"),
            ],
        },
        "t has many rows, so it needs one index",
        id="a many-row input table with two",
    ),
    pytest.param(
        {
            "rows": "one",
            "inputs": [column("line", "int", index="numbered"), column("value", "float")],
        },
        "t.line is an index, which only a many-row table has",
        id="an index in a one-row table",
    ),
    pytest.param(
        {
            "rows": "many",
            "inputs": [column("value", "float")],
            "outputs": [column("line", "int", index="numbered")],
        },
        "t.line is an index among the outputs, which only a table of outputs alone holds its "
        "index among",
        id="an index among the outputs of a table with inputs",
    ),
    pytest.param(
        {"rows": "many", "outputs": [column("line", "int", index="numbered")]},
        "t.line is an index among the outputs, which only a table of outputs alone holds its "
        "index among",
        id="an index the only output",
    ),
    pytest.param(
        {
            "rows": "many",
            "inputs": [column("line", "int", index="numbered")],
            "outputs": [column("premium", "float")],
        },
        "t.line is an index among the inputs of a table of outputs alone, whose index is among "
        "its outputs",
        id="an index among the inputs of a table of outputs alone",
    ),
    pytest.param(
        {"rows": "many", "inputs": [column("line", "float", index="numbered"), column("v")]},
        "t.line is the index, an Integer or Text",
        id="an index of a Decimal type",
    ),
    pytest.param(
        {"rows": "many", "inputs": [column("line", "date", index="typed"), column("v")]},
        "t.line is the index, an Integer or Text",
        id="an index of a Date type",
    ),
    pytest.param(
        {
            "rows": "many",
            "inputs": [
                column("line", "int", index="numbered", required=True, min=1, default_value="1"),
                column("v"),
            ],
        },
        "t.line is the index, which takes no required, a range, a default",
        id="an index holding Required, a range and a default",
    ),
    pytest.param(
        {
            "rows": "many",
            "inputs": [column("line", "int", index="numbered", placeholder="No."), column("v")],
        },
        "t.line is an index nobody types, so it shows no placeholder",
        id="a placeholder on a numbered index",
    ),
    pytest.param(
        {
            "rows": "many",
            "inputs": [column("site", index="typed", options=["Leeds"]), column("v")],
        },
        "t.site is an index without a row for each value, so it takes no allowed values",
        id="allowed values on a typed index",
    ),
    pytest.param(
        {"rows": "many", "inputs": [column("peril", index="values"), column("v")]},
        "t.peril gives a row for each value, so it needs allowed values from a params file",
        id="an index of values without allowed values",
    ),
    pytest.param(
        {
            "rows": "many",
            "inputs": [column("peril", index="values", options=["Fire"]), column("v")],
        },
        "t.peril gives a row for each value, so it needs allowed values from a params file",
        id="an index of values whose values are typed in",
    ),
    pytest.param(
        {
            "rows": "many",
            "inputs": [
                column(
                    "peril",
                    index="values",
                    options=["\U00020000" * 184],
                    options_source={"file": "perils.csv", "column": "peril"},
                ),
                column("v"),
            ],
        },
        "is longer than 366 UTF-16 code units",
        id="an index of values exceeding the portable key byte budget",
    ),
    pytest.param(
        {
            "rows": "many",
            "inputs": [
                column(
                    "peril",
                    index="values",
                    options=["x" * 256],
                    options_source={"file": "perils.csv", "column": "peril"},
                ),
                column("v"),
            ],
        },
        "is longer than 255 characters, the longest a Text index holds",
        id="an index of values with a value too long",
    ),
    pytest.param(
        {"rows": "many", "inputs": [column("line", "int", index="numbered", key=True)]},
        "key",
        id="a column marked key, which no form holds now",
    ),
    pytest.param(
        {"rows": "many", "inputs": [column("line", "int", index=True)]},
        "index",
        id="an index flag of an earlier form",
    ),
]


@pytest.mark.parametrize(("table", "says"), KEY_REFUSALS)
def test_a_tables_key_is_never_typed_so_a_key_or_index_it_cannot_have_is_refused(
    table: dict[str, Any], says: str
) -> None:
    data = _one_table_form(table)

    with pytest.raises(ValidationError, match=re.escape(says)):
        FormSpec.model_validate(data)


@pytest.mark.parametrize(
    "table",
    [
        pytest.param(
            {"rows": "many", "inputs": [column("line", "int", index="numbered"), column("value")]},
            id="a many-row input table by its index",
        ),
        pytest.param(
            {
                "rows": "many",
                "inputs": [
                    column(
                        "peril",
                        options=["Fire"],
                        index="values",
                        options_source={"file": "perils.csv", "column": "peril"},
                    ),
                    column("limit", "float"),
                ],
                "outputs": [column("premium", "float")],
            },
            id="a table of both blocks by its index of values",
        ),
        pytest.param(
            {
                "rows": "many",
                "outputs": [
                    column("layer", "int", index="typed"),
                    column("premium", "float"),
                ],
            },
            id="a table of outputs alone by the typed index the pricing gives",
        ),
        pytest.param(
            {
                "rows": "many",
                "outputs": [
                    column(
                        "peril",
                        index="values",
                        options=["Fire", "Flood"],
                        options_source={"file": "perils.csv", "column": "peril"},
                    ),
                    column("premium", "float"),
                ],
            },
            id="a table of outputs alone by an index of values among its outputs",
        ),
        pytest.param(
            {
                "rows": "many",
                "inputs": [column("site", index="typed", placeholder="Site"), column("v")],
            },
            id="a typed Text index with a placeholder",
        ),
        pytest.param(
            {
                "rows": "many",
                "outputs": [column("line", "int", index="numbered"), column("premium", "float")],
            },
            id="a table of outputs alone marking none",
        ),
        pytest.param(
            {"rows": "many", "inputs": [column("line", "int", index="numbered")]},
            id="an empty many-row table, its index among its inputs",
        ),
    ],
)
def test_a_table_keyed_as_its_kind_is_read(table: dict[str, Any]) -> None:
    data = _one_table_form(table)

    assert FormSpec.model_validate(data).data_schema.tables[0].rows == "many"


@pytest.mark.parametrize(
    ("table", "priced", "alone"),
    [
        # An empty table switched to many rows, its index renamed: no output side.
        pytest.param({"inputs": [column("line", "int", index="numbered")]}, [], False, id="empty"),
        # Given its first output, the index goes with the identity to the outputs.
        pytest.param(
            {"outputs": [column("line", "int", index="numbered"), column("premium", "float")]},
            ["premium"],
            True,
            id="a first output",
        ),
        # That output removed, the index is back among the inputs, the table no output table.
        pytest.param(
            {"inputs": [column("line", "int", index="numbered")], "outputs": []},
            [],
            False,
            id="its output removed",
        ),
    ],
)
def test_the_index_never_makes_a_table_an_output_table(
    table: dict[str, Any], priced: list[str], alone: bool
) -> None:
    (held,) = FormSpec.model_validate(_one_table_form({"rows": "many", **table})).data_schema.tables

    assert [column.name for column in held.priced_outputs] == priced
    assert held.has_outputs is bool(priced)
    assert held.outputs_alone is alone
    assert held.index is not None and held.index.name == "line"
    assert held.is_output("line") is alone


def test_a_form_saved_by_any_earlier_haute_takes_on_the_columns_added_since(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Today's columns, some left out and some out of order, as a later Haute with one more
    # reads them: by name, in its order.
    today = _with_details_table()
    fewer = _with_details_table(lambda table: table.update(inputs=table["inputs"][:9]))
    swapped = _with_details_table(lambda table: table["inputs"].insert(3, table["inputs"].pop(9)))
    later = (*QUOTE_DETAILS_COLUMNS, ("renewal_of", "str"))
    monkeypatch.setattr("haute._workbench_form.QUOTE_DETAILS_COLUMNS", later)

    for held in (today, fewer, swapped):
        details = FormSpec.model_validate(held).data_schema.tables[0]
        assert [(c.name, c.type) for c in details.columns] == list(later)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        # Many rows, given the index a many-row table with inputs needs (alone, the table's
        # own check refuses it for having none).
        (
            lambda t: t.update(
                rows="many",
                inputs=[{"name": "row_number", "type": "int", "index": "numbered"}, *t["inputs"]],
            ),
            "is Haute's: a one-row input table",
        ),
        (
            lambda t: t.update(outputs=[{"name": "premium", "type": "float"}]),
            "is Haute's: a one-row input table",
        ),
        # A column Haute does not make is no Haute's: left as it is, and refused.
        (
            lambda t: t["inputs"].append({"name": "extra", "type": "str"}),
            "is Haute's: a one-row input table",
        ),
        (lambda t: t["inputs"][7].update(type="str"), "is Haute's: a one-row input table"),
        (lambda t: t["inputs"][2].update(name="title"), "is Haute's: a one-row input table"),
        (lambda t: t["inputs"][5].update(type="str"), "is Haute's: a one-row input table"),
        (
            lambda t: t["inputs"][2].update(index="typed"),
            "quote_details.quote_name is an index, which only a many-row table has",
        ),
        (
            lambda t: t["inputs"][0].update(required=True),
            "quote_details.quote_reference is set by the underwriter app",
        ),
        (
            lambda t: t["inputs"][12].update(default_value="ana@example.com"),
            "created_by is set by the underwriter app and takes no rules or default",
        ),
        (
            lambda t: t["inputs"][15].update(options=["1.0.0"]),
            "haute_version is set by the underwriter app",
        ),
        (
            lambda t: t["inputs"][8].update(options=["draft"]),
            "stage is set by the underwriter app",
        ),
        (
            lambda t: t["inputs"][8].update(
                options_source={"file": "stages.csv", "column": "stage"}
            ),
            "stage is set by the underwriter app",
        ),
        (
            lambda t: t["inputs"][9].update(default_value="Underwriting"),
            "assignee is set by the underwriter app and takes no rules or default",
        ),
        (
            lambda t: t["inputs"][10].update(default_value=True),
            "archived is set by the underwriter app and takes no rules or default",
        ),
    ],
    ids=[
        "many-rows",
        "output",
        "unknown-column",
        "is-renewal-retyped",
        "renamed",
        "retyped",
        "an-index",
        "rule",
        "created-by-default",
        "haute-version-options",
        "options",
        "options-source",
        "default",
        "ticked-default",
    ],
)
def test_a_quote_details_table_that_is_not_hautes_is_refused(change: Any, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        FormSpec.model_validate(_with_details_table(change))
