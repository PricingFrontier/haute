"""A sheet and its parts are identified by file name, title and number (READ-24).

Spec: specs/workbench/low-level.md, the form folder's sheet and layout files. Neither holds a
random id: a section is known by its title, a component by its number in its section, one
more than the highest when it was made, which keys its box in the layout file.
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
from tests._workbench_helpers import enable, form_folder
from tests.test_workbench_folder import FORM, config, files_of, put, spec_of

SHEET = "sheets/br_coverages.json"
LAYOUT = "sheets/br_coverages.layout.json"


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "p"
    enable(root)
    return root


def _read(project: Path, form: dict[str, Any] | FormSpec) -> tuple[FormSpec, dict[str, bytes]]:
    files = files_of(form, project)
    put(form_folder(project), files)
    return read_form_document(config(project)).spec, files


def test_a_sheet_and_its_layout_hold_no_random_ids(project: Path) -> None:
    spec, files = _read(project, FORM)

    sheet, layout = json.loads(files[SHEET]), json.loads(files[LAYOUT])
    assert "id" not in sheet
    for section in sheet["sections"]:
        assert "id" not in section
        numbers = [component["number"] for component in section["components"]]
        assert numbers == list(range(1, len(numbers) + 1))
        assert all("id" not in component for component in section["components"])
        assert sorted(layout["sections"][section["title"]]["components"]) == sorted(
            str(number) for number in numbers
        )
    page = next(page for page in spec.pages if page.title == sheet["title"])
    assert page.id == "br_coverages"
    assert page.sections[0].widgets[0].id == f"br_coverages/{page.sections[0].title}/1"


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
    sheet_file = form_folder(project) / SHEET
    sheet = json.loads(sheet_file.read_bytes())
    sheet["sections"].append(copy.deepcopy(sheet["sections"][0]))
    sheet_file.write_bytes(json.dumps(sheet).encode())
    with pytest.raises(WorkbenchError, match="two sections titled"):
        read_form_document(config(project))


def test_two_components_numbered_alike_in_a_section_are_refused_on_load(project: Path) -> None:
    _read(project, FORM)
    sheet_file = form_folder(project) / SHEET
    sheet = json.loads(sheet_file.read_bytes())
    components = sheet["sections"][0]["components"]
    components[1]["number"] = components[0]["number"]
    sheet_file.write_bytes(json.dumps(sheet).encode())
    with pytest.raises(WorkbenchError, match="two components numbered 1"):
        read_form_document(config(project))


def test_adding_a_component_changes_one_entry_in_each_file(project: Path) -> None:
    spec, before = _read(project, FORM)
    data = spec.model_dump(mode="json")
    section = data["pages"][0]["sections"][0]
    added = {**copy.deepcopy(section["widgets"][0]), "id": "new", "number": None, "x": 0, "y": 0}
    section["widgets"].append(added)

    _after_spec, after = _read(project, data)

    layout_before = json.loads(before[LAYOUT])["sections"][section["title"]]["components"]
    layout_after = json.loads(after[LAYOUT])["sections"][section["title"]]["components"]
    assert set(layout_after) - set(layout_before) == {str(len(layout_before) + 1)}
    assert {k: v for k, v in layout_after.items() if k in layout_before} == layout_before


def test_renaming_a_section_keeps_its_layout(project: Path) -> None:
    spec, before = _read(project, FORM)
    data = spec.model_dump(mode="json")
    old = data["pages"][0]["sections"][0]["title"]
    data["pages"][0]["sections"][0]["title"] = "Renamed"

    _renamed, after = _read(project, data)

    assert (
        json.loads(after[LAYOUT])["sections"]["Renamed"]
        == json.loads(before[LAYOUT])["sections"][old]
    )


def test_a_component_moved_between_sections_keeps_its_box_and_takes_the_next_number(
    project: Path,
) -> None:
    spec, _ = _read(project, FORM)
    data = spec.model_dump(mode="json")
    page = data["pages"][0]
    if len(page["sections"]) < 2:
        pytest.skip("the test project's first sheet has one section")
    source, target = page["sections"][0], page["sections"][1]
    moved = source["widgets"].pop(0)
    box = {key: moved[key] for key in ("x", "y", "w", "h")}
    target["height"] = max(target["height"], moved["y"] + moved["h"])
    target["widgets"].append({**moved, "number": None})

    read, _ = _read(project, data)

    placed = read.pages[0].sections[1].widgets[-1]
    assert {key: getattr(placed, key) for key in box} == box
    assert placed.number == max(widget.number or 0 for widget in read.pages[0].sections[1].widgets)
