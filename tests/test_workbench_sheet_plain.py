"""A sheet's and a component's gaps, conditions and appearance are written plainly (READ-31).

Spec: specs/workbench/low-level.md, the form folder's sheet and component files. A gap is
``null`` in ``fields`` at its place, a number of conditions is ``at_least``, and a field with
a look of its own is an object holding the field and its settings, in its place.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from haute._workbench_folder import read_form_document
from tests._workbench_helpers import enable, form_folder
from tests.test_workbench_folder import (
    FORM,
    POLICY_BOXES,
    config,
    files_of,
    put,
    spec_of,
    widget_of,
)

SHEET = "sheets/br_coverages.json"
#: The Collection of the policy's fields, in its own file.
COLLECTION = "components/policy.json"


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """A project whose workbench is enabled, its form folder ``rating/workbench``."""
    root = tmp_path / "p"
    enable(root)
    return root


def _round_trip(project: Path, form: dict[str, Any], path: str) -> dict[str, Any]:
    """*form* written and read back the same, and its file at *path* as written."""
    files = files_of(form, project)
    put(form_folder(project), files)
    document = read_form_document(config(project))
    assert document.spec.model_dump(mode="json") == spec_of(form).model_dump(mode="json")
    written: dict[str, Any] = json.loads(files[path])
    return written


@pytest.mark.parametrize(
    ("spacers", "shape"),
    [
        ([0], [None, "policy.state", "policy.exposure", "pricing.premium"]),
        ([3], ["policy.state", "policy.exposure", "pricing.premium", None]),
        ([1, 1], ["policy.state", None, None, "policy.exposure", "pricing.premium"]),
    ],
    ids=["first", "last", "doubled"],
)
def test_a_gap_is_null_at_its_place_in_fields(
    project: Path, spacers: list[int], shape: list[str | None]
) -> None:
    form = copy.deepcopy(FORM)
    collection = widget_of(form, POLICY_BOXES)
    collection["spacers"] = spacers
    collection["column_looks"] = []

    collection = _round_trip(project, form, COLLECTION)

    assert collection["type"] == "collection"
    assert collection["fields"] == shape
    assert "spacers" not in collection


def test_a_field_with_a_look_of_its_own_is_an_object_in_its_place(project: Path) -> None:
    collection = _round_trip(project, copy.deepcopy(FORM), COLLECTION)

    fields = collection["fields"]
    assert fields[0]["field"] == "policy.state" and fields[0]["bold"] is True
    assert fields[2] == "policy.exposure"
    assert "column_looks" not in collection


@pytest.mark.parametrize("match", ["all", "any", 2])
def test_match_is_all_any_or_at_least_on_a_show_when_and_a_group(
    project: Path, match: str | int
) -> None:
    form = copy.deepcopy(FORM)
    section = next(
        section
        for page in form["pages"]
        for section in page["sections"]
        if section["title"] == "Perils"
    )
    section["show_when"]["match"] = match
    section["show_when"]["conditions"][0]["match"] = match

    sheet = _round_trip(project, form, SHEET)

    rule = next(s for s in sheet["sections"] if s["title"] == "Perils")["show_when"]
    group = rule["conditions"][0]
    for held in (rule, group):
        if isinstance(match, int):
            assert held["at_least"] == match and "match" not in held
        else:
            assert held.get("match", "all") == match and "at_least" not in held
