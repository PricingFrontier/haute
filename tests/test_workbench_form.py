"""The workbench's form file (specs/workbench): one canonical shape, read whole with the
file's revision and written atomically, with what is wrong named when it cannot be read."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from haute._explore_pivots import PIVOT_NUMBER_FORMATS
from haute._workbench_config import WorkbenchConfig
from haute._workbench_form import (
    QUOTE_DETAILS_COLUMNS,
    QUOTE_DETAILS_MANAGED,
    ColumnFormat,
    FormDocument,
    FormSpec,
    HeadingWidget,
    WorkbenchFormError,
    blank_form,
    form_file_revision,
    form_revision,
    quote_details_table,
    read_form_document,
    render_form,
    with_quote_details,
    write_form,
)


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
        "index": False,
        "help": "",
        "placeholder": "",
        "default_value": "",
        "number_format": "general",
        "decimal_places": None,
        "use_grouping": True,
        **rest,
    }


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
                "columns": [column("premium", "float")],
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
                            # Its labels beside its boxes, compact and without a frame.
                            "labels": "beside",
                            "density": "compact",
                            "frame": False,
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


def config(project: Path, form: str = "forms/form.json") -> WorkbenchConfig:
    return WorkbenchConfig(enabled=True, form=form, project_root=project)


def test_the_editor_names_the_quote_details_columns_the_server_does() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "frontend" / "src" / "utils" / "workbenchForm.ts"
    ).read_text(encoding="utf-8")
    columns = re.findall(r'\["(qd_\w+)", "(\w+)", "(\w+)"\]', source)
    managed = re.search(r"QUOTE_DETAILS_MANAGED[^=]*= new Set\(\[([^\]]*)\]\)", source)

    assert tuple(columns) == QUOTE_DETAILS_COLUMNS
    assert managed is not None
    assert set(re.findall(r'"(\w+)"', managed.group(1))) == QUOTE_DETAILS_MANAGED


def test_the_blank_form_is_one_empty_sheet_named_after_the_project() -> None:
    text = render_form(blank_form("motor"))

    # The whole sheet is one section until another is added, as tall as the view's least
    # sheet height less the room it leaves below the sections, on a sheet 1280 wide.
    section = {"id": "section_1", "title": "Section 1", "height": 560}
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
                "id": "page_1",
                "title": "Sheet 1",
                "width": 1280,
                "show_when": ALWAYS,
                "sections": [{**section, "show_when": ALWAYS, "widgets": []}],
            }
        ],
        "sample": {},
    }
    assert text.endswith("}\n")


def test_a_form_round_trips_through_write_and_read_with_the_file_s_revision(
    tmp_path: Path,
) -> None:
    spec = FormSpec.model_validate(FORM)

    revision = write_form(tmp_path / "forms" / "form.json", spec)
    document = read_form_document(config(tmp_path))

    # Read, it holds Haute's quote_details table first, which the file written without it
    # takes on at its next save.
    assert document == FormDocument(with_quote_details(spec), revision)
    assert document.spec.data_schema.tables[0].id == "quote_details"
    data = (tmp_path / "forms" / "form.json").read_bytes()
    assert json.loads(data) == FORM
    # The revision is the file's content hash, so the same bytes read as the same revision.
    assert revision == form_revision(data) == form_file_revision(config(tmp_path))
    assert len(revision) == 16


def test_an_edit_to_the_file_changes_its_revision(tmp_path: Path) -> None:
    path = tmp_path / "forms" / "form.json"
    first = write_form(path, blank_form("motor"))

    assert write_form(path, blank_form("motor")) == first
    path.write_bytes(path.read_bytes().replace(b'"motor"', b'"home"'))
    assert form_file_revision(config(tmp_path)) != first


def test_the_file_keeps_text_as_typed(tmp_path: Path) -> None:
    """A label or an allowed value outside ASCII is written as it is, as the node configs are."""
    spec = FormSpec.model_validate(FORM)
    spec.data_schema.tables[0].columns[0].label = "Zürich"

    text = render_form(spec)

    assert '"label": "Zürich"' in text
    assert "\\u00fc" not in text


@pytest.mark.parametrize("text", ["", "   ", "  Cover  "])
def test_a_heading_s_text_reads_and_writes_as_typed_blank_or_not(tmp_path: Path, text: str) -> None:
    # A Heading with no text is a problem the view names, never a refusal, and the spaces
    # around its text and its description are the builder's: the file keeps what was typed.
    form = json.loads(json.dumps(FORM))
    widgets(form)[3].update(text=text, description=text)
    (tmp_path / "forms").mkdir()
    (tmp_path / "forms" / "form.json").write_text(json.dumps(form), encoding="utf-8")

    spec = read_form_document(config(tmp_path)).spec

    heading = spec.pages[0].sections[1].widgets[0]
    assert isinstance(heading, HeadingWidget)
    assert (heading.text, heading.description) == (text, text)
    written = widgets(json.loads(render_form(spec)))[3]
    assert (written["text"], written["description"]) == (text, text)


def test_every_field_is_written_so_the_file_has_one_spelling(tmp_path: Path) -> None:
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
    )
    collection_options = ("labels", "density", "frame")
    chart_options = ("category", "chart_type", "orientation", "stacked", "legend", "data_labels")
    sparse = json.loads(json.dumps(FORM))
    del sparse["sample"]
    del sparse["theme"]
    for table in sparse["schema"]["tables"]:
        for col in table["columns"]:
            del col["index"]
            for field in display:
                del col[field]
    sparse["schema"]["tables"][1]["columns"][0]["index"] = True
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
    (tmp_path / "forms").mkdir()
    (tmp_path / "forms" / "form.json").write_text(json.dumps(sparse), encoding="utf-8")

    spec = read_form_document(config(tmp_path)).spec

    assert spec.sample == {}
    assert [c.index for c in spec.data_schema.tables[2].columns] == [True, False, False]
    written = json.loads(render_form(spec))
    assert written["sample"] == {}
    assert all("index" in c for t in written["schema"]["tables"] for c in t["columns"])
    assert written["schema"]["tables"][1]["columns"][1] == column("exposure", "float", min=0.0)
    assert written["schema"]["tables"][1]["columns"][0]["default_value"] == ""
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
    }
    # A Collection labelled above its boxes, comfortable and framed.
    boxes = widgets(written)[0]
    assert {field: boxes[field] for field in collection_options} == {
        "labels": "above",
        "density": "comfortable",
        "frame": True,
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
        (lambda f: widgets(f)[0].update(header="caps"), "header"),
        (lambda f: widgets(f)[1].update(labels="above"), "labels"),
        (lambda f: widgets(f)[0].update(labels="under"), "labels"),
        (lambda f: widgets(f)[0].update(density="roomy"), "density"),
        (lambda f: widgets(f)[1].update(header="bold"), "header"),
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
        (lambda f: f["pages"][0]["sections"][1].update(height=47), "pages.0.sections.1"),
        (lambda f: f["pages"][0]["sections"][1].update(height=47), "widget w_title reaches"),
        # A sheet has a width, at least a pixel, holding its components inside it: the Chart,
        # 760 across and 480 wide, needs 1240.
        (lambda f: f["pages"][0].pop("width"), "pages.0.width"),
        (lambda f: f["pages"][0].update(width=0), "pages.0.width"),
        (lambda f: f["pages"][0].update(width=1239), "widget w_limits reaches 1240px across"),
        (lambda f: f["pages"][0]["sections"][0].update(colour="red"), "colour"),
        (lambda f: f["pages"][0]["sections"][1]["show_when"].update(match="most"), "match"),
        (lambda f: widgets(f)[0].update(type="slider"), "type"),
        (lambda f: f["pages"][0]["show_when"].update(match="none"), "match"),
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
    ],
)
def test_an_unknown_field_or_a_value_outside_the_shape_is_refused(
    tmp_path: Path, change: Any, where: str
) -> None:
    broken = json.loads(json.dumps(FORM))
    change(broken)
    (tmp_path / "forms").mkdir()
    (tmp_path / "forms" / "form.json").write_text(json.dumps(broken), encoding="utf-8")

    with pytest.raises(
        WorkbenchFormError, match="forms/form.json is not a workbench form"
    ) as caught:
        read_form_document(config(tmp_path))
    assert where in str(caught.value)


def test_a_section_as_tall_as_its_components_reach_holds_them(tmp_path: Path) -> None:
    exact = json.loads(json.dumps(FORM))
    exact["pages"][0]["sections"][1]["height"] = 48
    (tmp_path / "forms").mkdir()
    (tmp_path / "forms" / "form.json").write_text(json.dumps(exact), encoding="utf-8")

    assert read_form_document(config(tmp_path)).spec.pages[0].sections[1].height == 48


def test_a_sheet_as_wide_as_its_components_reach_holds_them(tmp_path: Path) -> None:
    exact = json.loads(json.dumps(FORM))
    exact["pages"][0]["width"] = 1240
    (tmp_path / "forms").mkdir()
    (tmp_path / "forms" / "form.json").write_text(json.dumps(exact), encoding="utf-8")

    assert read_form_document(config(tmp_path)).spec.pages[0].width == 1240


def test_a_sheet_holding_its_own_components_is_refused_with_what_replaced_them(
    tmp_path: Path,
) -> None:
    # As sheets were written before sections: refused by name, never read as one section.
    old = json.loads(json.dumps(FORM))
    old["pages"][0]["widgets"] = widgets(old)
    del old["pages"][0]["sections"]
    (tmp_path / "forms").mkdir()
    (tmp_path / "forms" / "form.json").write_text(json.dumps(old), encoding="utf-8")

    message = (
        "forms/form.json is not a workbench form: pages.0.widgets was removed; a sheet's "
        "components now sit in its sections (pages.0.sections[].widgets)"
    )
    with pytest.raises(WorkbenchFormError, match=f"^{re.escape(message)}"):
        read_form_document(config(tmp_path))


def test_a_column_takes_the_explore_pivot_s_number_formats() -> None:
    """The sheets show a number as an Explore pivot does, and the Schema tab offers the
    pivot's formats, so the file must accept exactly those."""
    assert frozenset(get_args(ColumnFormat)) == PIVOT_NUMBER_FORMATS


def test_a_form_that_is_not_utf8_names_the_file(tmp_path: Path) -> None:
    (tmp_path / "forms").mkdir()
    (tmp_path / "forms" / "form.json").write_bytes(b'{"name": "\xff\xfe"}')

    with pytest.raises(WorkbenchFormError, match="forms/form.json is not UTF-8 text"):
        read_form_document(config(tmp_path))


def test_a_missing_form_is_the_blank_form_that_was_never_saved(tmp_path: Path) -> None:
    project = tmp_path / "motor"
    project.mkdir()

    assert read_form_document(config(project)) == FormDocument(blank_form("motor"), None)
    assert form_file_revision(config(project)) is None


def test_a_form_that_is_not_json_or_cannot_be_read_names_the_file(tmp_path: Path) -> None:
    (tmp_path / "forms").mkdir()
    (tmp_path / "forms" / "form.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(WorkbenchFormError, match="forms/form.json is not JSON"):
        read_form_document(config(tmp_path))

    (tmp_path / "unreadable").mkdir()
    with pytest.raises(WorkbenchFormError, match="unreadable could not be read"):
        read_form_document(config(tmp_path, form="unreadable"))
    with pytest.raises(WorkbenchFormError, match="unreadable could not be read"):
        form_file_revision(config(tmp_path, form="unreadable"))


# ── Haute's quote_details table ───────────────────────────────────────────


def _with_details_table(change: Any = None) -> dict[str, Any]:
    form = json.loads(json.dumps(FORM))
    table = quote_details_table().model_dump(mode="json")
    if change is not None:
        change(table)
    form["schema"]["tables"].insert(0, table)
    return form


def test_a_form_holding_hautes_quote_details_reads_and_writes_as_it_is(tmp_path: Path) -> None:
    held = _with_details_table(
        lambda table: table["columns"][1].update(label="Quote", help="As the broker calls it")
    )
    held["schema"]["tables"][0]["columns"][7].update(options=["GBP", "USD"], default_value="GBP")
    held["schema"]["tables"][0]["columns"][0]["group"] = "Quote"
    held["schema"]["tables"][0]["columns"][10]["default_value"] = False
    (tmp_path / "forms").mkdir()
    (tmp_path / "forms" / "form.json").write_text(json.dumps(held), encoding="utf-8")

    spec = read_form_document(config(tmp_path)).spec

    assert json.loads(render_form(spec)) == held
    assert [c.name for c in spec.data_schema.tables[0].columns] == [
        name for _, name, _ in QUOTE_DETAILS_COLUMNS
    ]
    assert with_quote_details(spec) is spec


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda t: t.update(rows="many"), "is Haute's: a one-row input table"),
        (lambda t: t.update(role="output"), "is Haute's: a one-row input table"),
        (lambda t: t["columns"].pop(), "is Haute's: a one-row input table"),
        (lambda t: t["columns"][1].update(name="title"), "is Haute's: a one-row input table"),
        (lambda t: t["columns"][4].update(type="str"), "is Haute's: a one-row input table"),
        (
            lambda t: t["columns"][0].update(required=True),
            "reference is set by the underwriter app",
        ),
        (
            lambda t: t["columns"][8].update(options=["draft"]),
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
        "column-gone",
        "renamed",
        "retyped",
        "rule",
        "options",
        "default",
        "ticked-default",
        "own-id",
    ],
)
def test_a_quote_details_table_that_is_not_hautes_is_refused(change: Any, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        FormSpec.model_validate(_with_details_table(change))
