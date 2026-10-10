"""The workbench's form file (specs/workbench): one canonical shape, read whole with the
file's revision and written atomically, with what is wrong named when it cannot be read."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from haute._workbench_config import WorkbenchConfig
from haute._workbench_form import (
    FormDocument,
    FormSpec,
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
        **rest,
    }


# The test project's shape: one-row and many-row input tables, an index column, an output
# table, a Collection and a Table, and a sample typed into the Collection.
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
                    column("exposure", "float", min=0.0),
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
                    "rows": 5,
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
        "pages": [{"id": "page_1", "title": "Sheet 1", "widgets": []}],
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


def test_every_field_is_written_so_the_file_has_one_spelling(tmp_path: Path) -> None:
    # A file written without the index flags and the sample, as a builder that leaves
    # defaults out would write it.
    sparse = json.loads(json.dumps(FORM))
    del sparse["sample"]
    for table in sparse["schema"]["tables"]:
        for col in table["columns"]:
            del col["index"]
    sparse["schema"]["tables"][1]["columns"][0]["index"] = True
    (tmp_path / "forms").mkdir()
    (tmp_path / "forms" / "form.json").write_text(json.dumps(sparse), encoding="utf-8")

    spec = read_form_document(config(tmp_path)).spec

    assert spec.sample == {}
    assert [c.index for c in spec.data_schema.tables[1].columns] == [True, False]
    written = json.loads(render_form(spec))
    assert written["sample"] == {}
    assert all("index" in c for t in written["schema"]["tables"] for c in t["columns"])


@pytest.mark.parametrize(
    ("change", "where"),
    [
        (lambda f: f.update(colour="red"), "colour"),
        (lambda f: f["pages"][0]["widgets"][0].update(rows=3), "rows"),
        (lambda f: f["pages"][0]["widgets"][1].update(rows=51), "rows"),
        (lambda f: f["schema"]["tables"][0]["columns"][0].update(type="money"), "type"),
        (lambda f: f.update(pages=[]), "pages"),
        (lambda f: f["pages"][0]["widgets"][0].update(type="chart"), "type"),
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
