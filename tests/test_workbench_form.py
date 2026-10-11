"""The workbench's form file (specs/workbench): one canonical shape, read whole with the
file's revision and written atomically, with what is wrong named when it cannot be read."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, get_args

import pytest

from haute._explore_pivots import PIVOT_NUMBER_FORMATS
from haute._workbench_config import WorkbenchConfig
from haute._workbench_form import (
    ColumnFormat,
    FormDocument,
    FormSpec,
    HeadingWidget,
    WorkbenchFormError,
    blank_form,
    form_file_revision,
    form_revision,
    read_form_document,
    render_form,
    write_form,
)


def column(name: str, type_: str = "str", **rest: Any) -> dict[str, Any]:
    return {
        "id": f"c_{name}",
        "name": name,
        "type": type_,
        "label": "",
        "key": False,
        "required": False,
        "min": None,
        "max": None,
        "options": [],
        "index": False,
        "help": "",
        "placeholder": "",
        "number_format": "general",
        "decimal_places": None,
        "use_grouping": True,
        **rest,
    }


# The test project's shape: one-row and many-row input tables, an index column, an output
# table, a Collection, a Table, a Chart and a Heading, the Table shown while a condition
# holds, and a sample typed into the Collection.
ALWAYS: dict[str, Any] = {"match": "all", "conditions": []}
FORM: dict[str, Any] = {
    "version": 1,
    "name": "Contractors' equipment",
    "schema": {
        "tables": [
            {
                "id": "t_policy",
                "name": "policy_details",
                "role": "input",
                "rows": "one",
                "columns": [
                    column("state", options=["CA", "NY"], required=True, label="State"),
                    column(
                        "exposure",
                        "float",
                        min=0.0,
                        help="Sum insured",
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
                {
                    "id": "w_title",
                    "type": "heading",
                    "x": 16,
                    "y": 384,
                    "w": 480,
                    "h": 40,
                    # Named as typed, outside ASCII too, large and centred: the sheet's title,
                    # with a line under it.
                    "text": "Équipement des entrepreneurs",
                    "description": "Plant and equipment, by layer",
                    "show_when": ALWAYS,
                    "size": "large",
                    "align": "center",
                },
            ],
        }
    ],
    "sample": {"t_policy": [{"c_state": "NY", "c_exposure": "$100,000"}]},
}


def config(project: Path, form: str = "forms/form.json") -> WorkbenchConfig:
    return WorkbenchConfig(enabled=True, form=form, project_root=project)


def test_the_blank_form_is_one_empty_sheet_named_after_the_project() -> None:
    text = render_form(blank_form("motor"))

    assert json.loads(text) == {
        "version": 1,
        "name": "motor",
        "schema": {"tables": []},
        "pages": [{"id": "page_1", "title": "Sheet 1", "show_when": ALWAYS, "widgets": []}],
        "sample": {},
    }
    assert text.endswith("}\n")


def test_a_form_round_trips_through_write_and_read_with_the_file_s_revision(
    tmp_path: Path,
) -> None:
    spec = FormSpec.model_validate(FORM)

    revision = write_form(tmp_path / "forms" / "form.json", spec)
    document = read_form_document(config(tmp_path))

    assert document == FormDocument(spec, revision)
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
    form["pages"][0]["widgets"][3].update(text=text, description=text)
    (tmp_path / "forms").mkdir()
    (tmp_path / "forms" / "form.json").write_text(json.dumps(form), encoding="utf-8")

    spec = read_form_document(config(tmp_path)).spec

    heading = spec.pages[0].widgets[3]
    assert isinstance(heading, HeadingWidget)
    assert (heading.text, heading.description) == (text, text)
    written = json.loads(render_form(spec))["pages"][0]["widgets"][3]
    assert (written["text"], written["description"]) == (text, text)


def test_every_field_is_written_so_the_file_has_one_spelling(tmp_path: Path) -> None:
    # A file written without the index flags, the columns' display, the Table's and the
    # Chart's options, the Heading's text, description, size and alignment, the sheet's, the
    # Collection's and the Heading's show_when and the sample, as a builder that leaves
    # defaults out would write it.
    display = ("help", "placeholder", "number_format", "decimal_places", "use_grouping")
    options = ("row_numbers", "density", "gridlines", "striped", "fixed_rows")
    chart_options = ("category", "chart_type", "orientation", "stacked", "legend", "data_labels")
    sparse = json.loads(json.dumps(FORM))
    del sparse["sample"]
    for table in sparse["schema"]["tables"]:
        for col in table["columns"]:
            del col["index"]
            for field in display:
                del col[field]
    sparse["schema"]["tables"][1]["columns"][0]["index"] = True
    for field in options:
        del sparse["pages"][0]["widgets"][1][field]
    for field in chart_options:
        del sparse["pages"][0]["widgets"][2][field]
    for field in ("text", "description", "show_when", "size", "align"):
        del sparse["pages"][0]["widgets"][3][field]
    del sparse["pages"][0]["show_when"]
    del sparse["pages"][0]["widgets"][0]["show_when"]
    (tmp_path / "forms").mkdir()
    (tmp_path / "forms" / "form.json").write_text(json.dumps(sparse), encoding="utf-8")

    spec = read_form_document(config(tmp_path)).spec

    assert spec.sample == {}
    assert [c.index for c in spec.data_schema.tables[1].columns] == [True, False, False]
    written = json.loads(render_form(spec))
    assert written["sample"] == {}
    assert all("index" in c for t in written["schema"]["tables"] for c in t["columns"])
    assert written["schema"]["tables"][0]["columns"][1] == column("exposure", "float", min=0.0)
    grid = written["pages"][0]["widgets"][1]
    assert {field: grid[field] for field in options} == {
        "row_numbers": True,
        "density": "compact",
        "gridlines": "rows",
        "striped": True,
        "fixed_rows": False,
    }
    chart = written["pages"][0]["widgets"][2]
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
    heading = written["pages"][0]["widgets"][3]
    assert list(heading) == "id x y w h type text description show_when size align".split()
    assert [heading[field] for field in ("text", "description", "size", "align")] == [
        "",
        "",
        "medium",
        "left",
    ]
    # Always shown.
    assert written["pages"][0]["show_when"] == ALWAYS
    assert written["pages"][0]["widgets"][0]["show_when"] == ALWAYS
    assert heading["show_when"] == ALWAYS


@pytest.mark.parametrize(
    ("change", "where"),
    [
        (lambda f: f.update(colour="red"), "colour"),
        (lambda f: f["pages"][0]["widgets"][0].update(rows=3), "rows"),
        (lambda f: f["pages"][0]["widgets"][1].update(rows=51), "rows"),
        (lambda f: f["pages"][0]["widgets"][1].update(density="roomy"), "density"),
        (lambda f: f["pages"][0]["widgets"][1].update(gridlines="dotted"), "gridlines"),
        # A Collection has none of a Table's options.
        (lambda f: f["pages"][0]["widgets"][0].update(striped=True), "striped"),
        (lambda f: f["pages"][0]["widgets"][2].update(chart_type="bar"), "chart_type"),
        (lambda f: f["pages"][0]["widgets"][2].update(legend="inside"), "legend"),
        # A Chart has none of a Table's options, and a Table and a Collection none of a
        # Chart's.
        (lambda f: f["pages"][0]["widgets"][2].update(striped=True), "striped"),
        (lambda f: f["pages"][0]["widgets"][1].update(chart_type="line"), "chart_type"),
        (lambda f: f["pages"][0]["widgets"][0].update(legend="top"), "legend"),
        # What labels a Chart's rows is held as a field is: a table and a column.
        (lambda f: f["pages"][0]["widgets"][2].update(category={"table": "t_layers"}), "category"),
        (lambda f: f["pages"][0]["widgets"][3].update(size="huge"), "size"),
        (lambda f: f["pages"][0]["widgets"][3].update(align="justify"), "align"),
        # A Heading is its text: it has no title and shows no fields, and no other
        # component has its text, its description, its size or its alignment.
        (lambda f: f["pages"][0]["widgets"][3].update(title="Cover"), "title"),
        (lambda f: f["pages"][0]["widgets"][3].update(fields=[]), "fields"),
        (lambda f: f["pages"][0]["widgets"][1].update(text="Layers"), "text"),
        (lambda f: f["pages"][0]["widgets"][1].update(description="By layer"), "description"),
        (lambda f: f["pages"][0]["widgets"][2].update(size="large"), "size"),
        (lambda f: f["pages"][0]["widgets"][2].update(align="center"), "align"),
        (lambda f: f["schema"]["tables"][0]["columns"][0].update(type="money"), "type"),
        (
            lambda f: f["schema"]["tables"][0]["columns"][1].update(number_format="yen"),
            "number_format",
        ),
        (
            lambda f: f["schema"]["tables"][0]["columns"][1].update(decimal_places=11),
            "decimal_places",
        ),
        (lambda f: f.update(pages=[]), "pages"),
        (lambda f: f["pages"][0]["widgets"][0].update(type="slider"), "type"),
        (lambda f: f["pages"][0]["show_when"].update(match="none"), "match"),
        (
            lambda f: f["pages"][0]["widgets"][1]["show_when"]["conditions"][0].update(op="has"),
            "op",
        ),
        # A condition's values are text as typed, as the sample's cells are.
        (
            lambda f: f["pages"][0]["widgets"][1]["show_when"]["conditions"][0].update(values=[1]),
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
