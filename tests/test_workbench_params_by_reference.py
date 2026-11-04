"""Sourced allowed values are stored only as a reference (READ-25).

Spec: specs/workbench/low-level.md, the form folder. A schema file writes a sourced column's
``options_source`` alone; reading the form fills its values from the params file at the
form's own revision, the working tree's or a past one's, never mixing the two.
"""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from haute._workbench_folder import form_at_ref, read_form_document
from haute._workbench_params import ParamFileError
from tests._workbench_helpers import enable, form_folder
from tests.test_workbench_folder import FORM, column_of, config, files_of, put, table_of

PARAMS = Path("rating") / "files" / "params"


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "p"
    enable(root)
    (root / PARAMS).mkdir(parents=True, exist_ok=True)
    return root


def _sourced(source: dict[str, str], kind: str = "str") -> dict[str, Any]:
    form = copy.deepcopy(FORM)
    column = column_of(form, "policy", "state")
    column.update(type=kind, options=[], options_source=source)
    return form


def _write(project: Path, form: dict[str, Any]) -> dict[str, bytes]:
    files = files_of(form, project)
    put(form_folder(project), files)
    return files


def _state_options(project: Path) -> list[str]:
    spec = read_form_document(config(project)).spec
    table = next(t for t in spec.data_schema.tables if t.name == "policy")
    return next(c for c in table.columns if c.name == "state").options


def test_a_schema_file_holds_the_reference_and_reading_fills_the_values(project: Path) -> None:
    (project / PARAMS / "states.csv").write_bytes(b"code,name\nCA,California\nNY,New York\n")
    files = _write(project, _sourced({"file": "states.csv", "column": "code"}))

    schema = json.loads(files["schema/policy.json"])
    state = next(c for c in schema["columns"] if c["name"] == "state")
    assert state["options_source"] == {"file": "states.csv", "column": "code"}
    assert "options" not in state
    assert _state_options(project) == ["CA", "NY"]


@pytest.mark.parametrize(("table", "name"), [("policy", "state"), ("quote_details", "broker")])
def test_a_loaded_forms_values_are_written_as_their_reference_alone(
    project: Path, table: str, name: str
) -> None:
    """The form the editor holds carries the values read from the params file; saving it
    writes the reference alone, a quote_details column's too, and saving what it reads back
    writes the same files."""
    (project / PARAMS / "states.csv").write_bytes(b"code,name\nCA,California\nNY,New York\n")
    form = copy.deepcopy(FORM)
    sourced = {"options": ["CA", "NY"], "options_source": {"file": "states.csv", "column": "code"}}
    if table == "quote_details":
        table_of(form, table)["columns"].append({"name": name, "type": "str", **sourced})
    else:
        column_of(form, table, name).update(sourced)

    files = _write(project, form)

    written = json.loads(files[f"schema/{table}.json"])
    entry = next(c for c in written["columns"] if c["name"] == name)
    assert entry["options_source"] == {"file": "states.csv", "column": "code"}
    assert "options" not in entry
    spec = read_form_document(config(project)).spec
    read = next(t for t in spec.data_schema.tables if t.name == table)
    assert next(c for c in read.columns if c.name == name).options == ["CA", "NY"]
    assert files_of(spec, project) == files


def test_a_form_read_at_an_older_revision_uses_that_revisions_params(project: Path) -> None:
    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
            cwd=project,
            check=True,
            capture_output=True,
        )

    (project / PARAMS / "states.csv").write_bytes(b"code\nCA\nNY\n")
    _write(project, _sourced({"file": "states.csv", "column": "code"}))
    git("init", "-q")
    git("add", "-A")
    git("commit", "-q", "-m", "form")
    (project / PARAMS / "states.csv").write_bytes(b"code\nTX\n")

    past = form_at_ref(config(project), "HEAD")

    assert past is not None
    table = next(t for t in past.data_schema.tables if t.name == "policy")
    assert next(c for c in table.columns if c.name == "state").options == ["CA", "NY"]
    assert _state_options(project) == ["TX"]


@pytest.mark.parametrize(
    ("csv", "kind", "named"),
    [
        (None, "str", "states.csv"),
        (b"name\nCA\n", "str", "code"),
        (b"code\n1\nlots\n", "int", "'lots'"),
    ],
    ids=["missing file", "missing column", "value not of the column's type"],
)
def test_a_params_file_that_cannot_give_the_values_is_refused(
    project: Path, csv: bytes | None, kind: str, named: str
) -> None:
    _write(project, _sourced({"file": "states.csv", "column": "code"}, kind))
    states = project / PARAMS / "states.csv"
    if csv is None:
        states.unlink(missing_ok=True)
    else:
        states.write_bytes(csv)

    with pytest.raises(ParamFileError, match=named):
        read_form_document(config(project))
