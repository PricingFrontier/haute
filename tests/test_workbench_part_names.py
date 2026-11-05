"""A sheet and its parts are identified by file name, title and name (READ-24).

Spec: specs/workbench/low-level.md, the form folder's sheet and component files. Neither holds
a random id: a section is known by its title on its sheet, a component by its name, the
builder's, which names its file and places it in its sheet's file.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from haute._workbench_config import WorkbenchError
from haute._workbench_folder import FormFolderNameError, read_form_document
from haute._workbench_form import FormSpec, render_form
from tests._workbench_helpers import enable, form_folder, write_files
from tests.test_workbench_folder import FORM, changes, config, files_of, put, spec_of, text

SHEET = "sheets/br_coverages.json"


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "p"
    enable(root)
    return root


def _read(project: Path, form: dict[str, Any] | FormSpec) -> tuple[FormSpec, dict[str, bytes]]:
    files = files_of(form, project)
    put(form_folder(project), files)
    return read_form_document(config(project)).spec, files


def test_a_sheet_and_its_components_hold_no_random_ids(project: Path) -> None:
    spec, files = _read(project, FORM)

    sheet = json.loads(files[SHEET])
    assert "id" not in sheet
    for section in sheet["sections"]:
        assert "id" not in section
        assert all("id" not in placed for placed in section["components"])
    for path in (path for path in files if path.startswith("components/")):
        assert not {"id", "name"} & set(json.loads(files[path])), path
    # Read, each part takes its id from its sheet's file name, its section's title and its
    # name, as the sheet's file places it.
    page = next(page for page in spec.pages if page.title == sheet["title"])
    assert page.id == "br_coverages"
    assert [[widget.id for widget in section.widgets] for section in page.sections] == [
        [
            f"br_coverages/{section['title']}/{placed['component']}"
            for placed in section["components"]
        ]
        for section in sheet["sections"]
    ]
    assert page.sections[0].widgets[0].id == "br_coverages/Cover/policy"


def test_a_release_holds_no_part_ids_and_builds_the_same_twice(project: Path) -> None:
    spec, _ = _read(project, FORM)
    again, _ = _read(project, spec)
    released = json.loads(render_form(spec))
    assert render_form(spec) == render_form(again)
    for page in released["pages"]:
        assert "id" not in page
        for section in page["sections"]:
            assert "id" not in section
            assert all("id" not in widget for widget in section["widgets"])
    assert FormSpec.model_validate(released).pages[0].id == spec.pages[0].id


def test_two_sections_titled_alike_are_refused_on_save_and_on_load(project: Path) -> None:
    form = copy.deepcopy(FORM)
    page = form["pages"][0]
    page["sections"].append({**copy.deepcopy(page["sections"][0]), "id": "other"})
    page["sections"][-1]["widgets"] = []
    with pytest.raises(FormFolderNameError, match="two sections titled"):
        files_of(spec_of(form), project)

    _read(project, FORM)
    sheet = json.loads((form_folder(project) / SHEET).read_bytes())
    # Its components sit in the first: the second places none.
    sheet["sections"].append({**copy.deepcopy(sheet["sections"][0]), "components": []})
    write_files(form_folder(project), {SHEET: json.dumps(sheet).encode()})
    with pytest.raises(WorkbenchError, match="two sections titled"):
        read_form_document(config(project))


def test_adding_a_component_adds_its_file_and_one_placement_line(project: Path) -> None:
    spec, before = _read(project, FORM)
    data = spec.model_dump(mode="json")
    section = data["pages"][0]["sections"][0]
    # Named as the editor names a copy: its original's name and the first free number.
    added = {**copy.deepcopy(section["widgets"][0]), "id": "new", "name": "policy_2"}
    added.update(x=760, y=312, w=480, h=80)
    section["widgets"].append(added)

    _after_spec, after = _read(project, data)

    assert changes(before, after) == (["components/policy_2.json", SHEET], [])
    # A copy shows what its original shows: its file is the original's.
    assert after["components/policy_2.json"] == before["components/policy.json"]
    placed = '        {"component": "policy_2", "x": 760, "y": 312, "w": 480, "h": 80}\n'
    last = '{"component": "limits", "x": 760, "y": 16, "w": 480, "h": 280}'
    assert text(after, SHEET) == text(before, SHEET).replace(f"{last}\n", f"{last},\n{placed}")


def test_retitling_a_section_changes_its_title_and_its_components_ids_alone(
    project: Path,
) -> None:
    spec, before = _read(project, FORM)
    data = spec.model_dump(mode="json")
    old = data["pages"][0]["sections"][0]["title"]
    data["pages"][0]["sections"][0]["title"] = "Renamed"

    renamed, after = _read(project, data)

    # The section's placements stay as they were, under its new title; no component's file
    # changes.
    assert changes(before, after) == ([SHEET], [])
    assert text(after, SHEET) == text(before, SHEET).replace(
        f'"title": "{old}"', '"title": "Renamed"'
    )
    assert [widget.id for widget in renamed.pages[0].sections[0].widgets] == [
        "br_coverages/Renamed/policy",
        "br_coverages/Renamed/layers",
        "br_coverages/Renamed/limits",
    ]
