"""The workbench routes (specs/workbench): the status the editor reads, the tables as the
form defines them now, and the form itself with its folder's revision, read from the working
directory on each request, saved only against the revision it was read at, the files that
changed written and those the form no longer has deleted, and each save captured on the
clone's save ledger as the pipeline's saves are."""

from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from haute import _git
from haute._git_state import write_working_branch
from haute._git_transactions import commit_milestone
from haute._project_context import context_thread
from haute._workbench_folder import folder_revision
from haute._workbench_form import QUOTE_DETAILS_COLUMNS, QUOTE_DETAILS_STORE_ONLY
from haute.routes._error_handlers import install_exception_handlers
from haute.routes.workbench import router
from tests._project import use_project
from tests._workbench_helpers import FORM, enable, form_files, write_form


def column(name: str, type_: str = "str", **rest: Any) -> dict[str, Any]:
    return {"name": name, "type": type_, **rest}


def form(*tables: dict[str, Any], sample: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "version": 1,
        "name": "x",
        "schema": {"tables": list(tables)},
        "pages": [{"id": "p", "title": "Sheet 1", "width": 1280, "sections": [section("s")]}],
        "sample": sample or {},
    }


def section(id_: str, *widgets: dict[str, Any]) -> dict[str, Any]:
    """A section holding *widgets*, each 120px tall at its top."""
    return {"id": id_, "title": id_, "height": 120, "widgets": list(widgets)}


POLICY = {
    "name": "policy_details",
    "rows": "one",
    "inputs": [column("state", options=["CA", "NY"]), column("exposure", "float")],
}
PRICING = {
    "name": "pricing_output",
    "rows": "one",
    "outputs": [column("premium", "float")],
}


#: Haute's quote_details table as the tables route serves it, first in every form.
#: quote_details as the Workbench Input takes it: without the columns the store keeps alone.
DETAILS = {
    "name": "quote_details",
    "side": "input",
    "port": "quote_details_input",
    "rows": "one",
    "columns": [
        {"name": name, "type": type_}
        for name, type_ in QUOTE_DETAILS_COLUMNS
        if name not in QUOTE_DETAILS_STORE_ONLY
    ],
    "key": None,
    "null_key": None,
    "index_kind": None,
    "index_values": [],
}


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The working directory is the project, as it is for ``haute serve``."""
    use_project(tmp_path)
    return tmp_path


def files_of(project: Path) -> dict[str, bytes]:
    """Every file in the project's form folder, by its path from the folder."""
    folder = project / FORM
    return {
        path.relative_to(folder).as_posix(): path.read_bytes()
        for path in sorted(folder.rglob("*"))
        if path.is_file()
    }


def app() -> FastAPI:
    application = FastAPI()
    install_exception_handlers(application)
    application.include_router(router)
    return application


def client() -> TestClient:
    return TestClient(app())


def served() -> dict[str, Any]:
    response = client().get("/api/workbench/form")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def save(spec: dict[str, Any], revision: str) -> Any:
    return client().put("/api/workbench/form", json={"form": spec, "base_revision": revision})


WORKING = "pricing-dev"
LEDGER = f"{WORKING}-save"


def git(project: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=project, capture_output=True, text=True, check=True)
    return done.stdout.strip()


def init_repo(project: Path) -> None:
    """A repository with a working branch and its ledger, as the editor's git flows leave one."""
    git(project, "init", "-b", "main")
    git(project, "config", "user.name", "Test Actuary")
    git(project, "config", "user.email", "test@example.com")
    git(project, "add", "haute.toml")
    git(project, "commit", "-m", "initial")
    _git.set_working_branch(WORKING, project, create=True, cwd=project)


def capture(response: dict[str, Any]) -> dict[str, Any]:
    return {
        key: response[key] for key in ("git_sha", "warnings", "identity_required", "uncaptured")
    }


def _link_directory(target: Path, link: Path) -> None:
    """A directory link anyone can make: a junction on Windows, a symlink elsewhere."""
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


def test_a_project_without_the_table_has_no_workbench(project: Path) -> None:
    disabled = {"enabled": False, "form": None, "environments": []}
    assert client().get("/api/workbench").json() == disabled

    # A disabled workbench lists none of the environments its table declares.
    enable(project, "[workbench]\nenabled = false\n[workbench.store.dev]\nprovider = 'sqlite'\n")
    assert client().get("/api/workbench").json() == disabled

    tables = client().get("/api/workbench/tables")
    assert tables.status_code == 404
    assert tables.json()["detail"] == (
        "The workbench is not enabled: set [workbench] enabled = true in haute.toml."
    )


def test_the_tables_are_served_from_the_form_as_it_is_now(project: Path) -> None:
    enable(project)
    write_form(
        project,
        form(POLICY, PRICING, sample={"policy_details": [{"state": "NY", "exposure": "$1,000"}]}),
    )

    assert client().get("/api/workbench").json() == {
        "enabled": True,
        "form": FORM,
        "environments": [],
    }
    response = client().get("/api/workbench/tables")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tables"] == [
        DETAILS,
        {
            "name": "policy_details",
            "side": "input",
            "port": "policy_details_input",
            "rows": "one",
            "columns": [
                {"name": "quote_reference", "type": "str"},
                {"name": "quote_option", "type": "int"},
                {"name": "state", "type": "str"},
                {"name": "exposure", "type": "float"},
            ],
            "key": None,
            "null_key": None,
            "index_kind": None,
            "index_values": [],
        },
    ]
    assert body["sample"] == {"policy_details": {"state": "NY", "exposure": 1000.0}}
    assert [t["name"] for t in body["response_tables"]] == ["pricing_output"]
    # The quote store's own columns, which the schema editor shows locked: quote_details'
    # own here, the form holding no many-row table.
    assert list(body["store_columns"]) == ["quote_details"]
    assert body["store_columns"]["quote_details"][:3] == [
        {"name": "quote_version", "kind": "int"},
        {"name": "saved_at", "kind": "datetime"},
        {"name": "saved_by", "kind": "str"},
    ]

    # Nothing is cached: a form changed on disk is served on the next request.
    folder = project / FORM
    for path in list(folder.rglob("*.json")):
        path.unlink()
    write_form(project, form({**POLICY, "name": "policy"}, PRICING))
    tables = client().get("/api/workbench/tables").json()["tables"]
    assert [t["name"] for t in tables] == ["quote_details", "policy"]


def test_a_form_key_in_haute_toml_is_refused_naming_what_replaced_it(project: Path) -> None:
    enable(project, '[workbench]\nenabled = true\nform = "forms/form.json"\n')

    for route in ("/api/workbench", "/api/workbench/tables", "/api/workbench/form"):
        refused = client().get(route)
        assert refused.status_code == 409
        assert refused.json()["detail"] == (
            "[workbench].form was removed: the form is the pipeline folder's workbench/ folder; "
            "delete the key"
        )


def test_a_missing_folder_answers_409_pointing_to_version_history(project: Path) -> None:
    enable(project)

    for route in ("/api/workbench/tables", "/api/workbench/form"):
        response = client().get(route)
        assert response.status_code == 409
        assert response.json()["detail"] == (
            "rating/workbench does not exist: restore it from version history (a project "
            "starts its workbench with haute init --workbench)"
        )


def test_tables_serve_large_integer_indexes_exactly_without_retyping_pipeline_keys(
    project: Path,
) -> None:
    from haute._workbench_form import FormSpec
    from haute._workbench_tables import output_misfit, workbench_tables

    enable(project)
    keys = [9007199254740992, 9007199254740993]
    index = column(
        "product",
        "int",
        index="values",
        options=list(map(str, keys)),
        options_source={"file": "products.csv", "column": "id"},
    )
    spec = form(
        {"name": "inputs", "rows": "many", "inputs": [index, column("value")]},
        {"name": "products", "rows": "many", "outputs": [index, column("premium", "float")]},
        sample={
            "inputs": [
                {"product": str(keys[0]), "value": "a"},
                {"product": str(keys[1]), "value": "b"},
            ]
        },
    )
    response = client().post("/api/workbench/tables", json={"form": spec})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["response_tables"][0]["index_values"] == list(map(str, keys))
    assert next(t for t in body["tables"] if t["name"] == "inputs")["null_key"] == str(keys[0])
    assert [r["product"] for r in body["sample"]["inputs"]] == list(map(str, keys))
    internal = workbench_tables(FormSpec.model_validate(spec))
    side = internal.response_tables[0]
    assert side.index_values == keys
    assert output_misfit(side, keys, []) is None


def test_the_form_is_served_with_its_revision_and_saved_against_it(project: Path) -> None:
    enable(project)
    write_form(project, form(POLICY, PRICING))

    body = served()
    # Every field is served, defaults included; Haute's quote_details table first.
    assert body["form"]["schema"]["tables"][0]["name"] == "quote_details"
    # A table and a column are known by their names: neither holds an id (READ-23).
    assert "id" not in body["form"]["schema"]["tables"][1]
    assert body["form"]["schema"]["tables"][1]["inputs"][3] == {
        "name": "exposure",
        "type": "float",
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
    }
    revision = body["revision"]
    assert isinstance(revision, str) and len(revision) == 16

    details, policy, pricing = body["form"]["schema"]["tables"]
    renamed = {
        **body["form"],
        "schema": {"tables": [details, {**policy, "name": "policy"}, pricing]},
    }
    saved = save(renamed, revision)

    assert saved.status_code == 200, saved.text
    assert saved.json()["form"] == renamed
    assert saved.json()["revision"] != revision
    # The folder holds the form's files: the renamed table's file under its new name, the
    # old one gone, and the next read answers the new revision.
    assert files_of(project) == form_files(renamed, project / FORM)
    assert "schema/policy.json" in files_of(project)
    assert "schema/policy_details.json" not in files_of(project)
    assert served() == {"form": renamed, "revision": saved.json()["revision"], "repairs": []}
    assert [t["name"] for t in client().get("/api/workbench/tables").json()["tables"]] == [
        "quote_details",
        "policy",
    ]

    # A form saved without the table, as no view of Haute's sends one, is written with it.
    without = {**renamed, "schema": {"tables": [{**policy, "name": "policy"}, pricing]}}
    again = save(without, saved.json()["revision"])
    assert again.status_code == 200, again.text
    assert again.json()["form"]["schema"]["tables"][0]["name"] == "quote_details"


@pytest.mark.parametrize("change", ["edit", "add", "delete"])
def test_a_save_against_a_folder_that_changed_on_disk_is_refused_writing_nothing(
    project: Path, change: str
) -> None:
    enable(project)
    write_form(project, form(POLICY))
    body = served()
    folder = project / FORM

    # Changed after the workbench read it: by hand, or by a branch switch.
    if change == "edit":
        index = json.loads((folder / "form.json").read_bytes())
        (folder / "form.json").write_bytes((json.dumps({**index, "name": "home"}) + "\n").encode())
    elif change == "add":
        (folder / "notes.json").write_bytes(b"{}\n")
    else:
        (folder / "schema" / "policy_details.json").unlink()
    before = files_of(project)
    stale = save(body["form"], body["revision"])

    assert stale.status_code == 409
    assert stale.json()["detail"] == (
        "stale_document_revision: rating/workbench changed on disk after the workbench "
        "read it. Reload the workbench before saving."
    )
    assert files_of(project) == before


def test_a_save_with_no_folder_is_refused_as_stale(project: Path) -> None:
    enable(project)
    write_form(project, form(POLICY))
    body = served()
    for path in sorted((project / FORM).rglob("*"), reverse=True):
        path.unlink() if path.is_file() else path.rmdir()
    (project / FORM).rmdir()

    stale = save(body["form"], body["revision"])

    assert stale.status_code == 409
    assert stale.json()["detail"].startswith("stale_document_revision")
    assert not (project / FORM).exists()


def test_a_save_over_a_folder_holding_a_file_not_the_forms_deletes_nothing(
    project: Path,
) -> None:
    # No read serves such a folder's revision, so only a request made outside the editor
    # names it: a save deletes the form's own files, never another's.
    enable(project)
    write_form(project, form(POLICY))
    body = served()
    (project / FORM / "notes.json").write_bytes(b"{}\n")
    before = files_of(project)

    refused = save(body["form"], folder_revision(before))

    assert refused.status_code == 409
    assert refused.json()["detail"] == (
        "rating/workbench/notes.json is not part of the form: list it in form.json or remove it"
    )
    assert files_of(project) == before


def _heading(name: str) -> dict[str, Any]:
    return {
        "id": name,
        "name": name,
        "type": "heading",
        "x": 0,
        "y": 0,
        "w": 200,
        "h": 40,
        "text": "Cover",
    }


@pytest.mark.parametrize(
    ("change", "detail"),
    [
        pytest.param(
            lambda folder: (folder / "sheets" / "sheet_1.json").unlink(),
            "rating/workbench/form.json names sheets/sheet_1.json, but the folder does not hold "
            "sheets/sheet_1.json",
            id="a sheet file missing",
        ),
        pytest.param(
            lambda folder: (folder / "sheets" / "sheet_1.json").rename(
                folder / "sheets" / "Sheet_1.json"
            ),
            "rating/workbench/sheets/Sheet_1.json must be named in lower case: rename it "
            "sheet_1.json",
            id="a sheet file named in capitals",
        ),
        pytest.param(
            lambda folder: (folder / "components" / "cover.json").rename(
                folder / "components" / "Cover.json"
            ),
            "rating/workbench/components/Cover.json must be named in lower case: rename it "
            "cover.json",
            id="a component file named in capitals",
        ),
    ],
)
def test_a_save_over_a_folder_not_holding_a_named_file_as_named_changes_nothing(
    project: Path, change: Any, detail: str
) -> None:
    # A save writes each file by its name in lower case and deletes those the form does not
    # have: over a file named in capitals, on a disk that ignores case, it would delete the
    # file it had written. Refused as a read refuses the folder, nothing is written.
    enable(project)
    placed = form(POLICY)
    placed["pages"][0]["sections"][0]["widgets"] = [_heading("cover")]
    write_form(project, placed)
    body = served()
    change(project / FORM)
    before = files_of(project)

    refused = save(body["form"], folder_revision(before))

    assert refused.status_code == 409
    assert refused.json()["detail"] == detail
    assert client().get("/api/workbench/form").json()["detail"] == detail
    assert files_of(project) == before


def test_a_form_grown_to_open_says_what_grew_and_a_save_keeps_it(project: Path) -> None:
    """A section too short for a component in it, as a merge can leave it, is grown as the
    form is read and said; the files are as they were until a save writes the form as read."""
    enable(project)
    tall = form(POLICY)
    tall["pages"][0]["sections"][0]["widgets"] = [
        {
            "id": "heading_t1t1",
            "name": "heading_1",
            "type": "heading",
            "x": 0,
            "y": 0,
            "w": 200,
            "h": 48,
            "text": "Cover",
            "description": "",
            "size": "large",
            "align": "left",
            "show_when": {"match": "all", "conditions": []},
        }
    ]
    write_form(project, tall)
    sheet_path = project / FORM / "sheets" / "sheet_1.json"
    sheet = json.loads(sheet_path.read_bytes())
    sheet["sections"][0]["height"] = 40
    sheet_path.write_bytes((json.dumps(sheet) + "\n").encode())

    body = served()

    assert body["repairs"] == [
        'Section "s" grew from 40px to 48px tall to hold the Heading "Cover".'
    ]
    assert body["form"]["pages"][0]["sections"][0]["height"] == 48
    saved = save(body["form"], body["revision"])
    assert saved.status_code == 200, saved.text
    assert served()["repairs"] == []


def test_a_save_through_a_link_or_junction_on_the_way_is_refused(project: Path) -> None:
    """Every path a save writes is checked as the other writes' are: a junction at the form
    folder, which stays inside the project and so passes the config's check, is no ordinary
    folder of the project's, and nothing is written through it."""
    enable(project)
    kept = project / "rating" / "kept"
    kept.mkdir(parents=True)
    write_form(project, form(POLICY))
    (project / FORM).rename(kept / "workbench")
    _link_directory(kept / "workbench", project / FORM)
    body = served()
    before = files_of(project)

    renamed = {**body["form"], "name": "renamed"}
    refused = save(renamed, body["revision"])

    assert refused.status_code == 409
    assert refused.json()["detail"] == (
        "rating/workbench/form.json is reached through a link or junction: the form folder "
        "holds files of its own."
    )
    assert files_of(project) == before


def test_a_link_inside_the_folder_is_refused_as_it_is_read(project: Path) -> None:
    enable(project)
    write_form(project, form(POLICY))
    elsewhere = project / "elsewhere"
    elsewhere.mkdir()
    _link_directory(elsewhere, project / FORM / "sample")

    for route in ("/api/workbench/tables", "/api/workbench/form"):
        refused = client().get(route)
        assert refused.status_code == 409
        assert refused.json()["detail"] == (
            "rating/workbench/sample is a link: the form folder holds its own files"
        )


def test_a_save_whose_files_cannot_all_be_written_puts_every_file_back(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute.routes import workbench as routes

    enable(project)
    write_form(project, form(POLICY))
    body = served()
    before = files_of(project)
    stage = routes._stage_artifact_write_bytes
    written: list[str] = []

    def second_write_fails(out_path: Path, payload: bytes, touched: list[Any]) -> None:
        if written:
            raise PermissionError(f"[Errno 13] Permission denied: {out_path.name}")
        written.append(out_path.name)
        stage(out_path, payload, touched)

    monkeypatch.setattr(routes, "_stage_artifact_write_bytes", second_write_fails)
    changed = {
        **body["form"],
        "name": "renamed",
        "schema": {
            "tables": [
                body["form"]["schema"]["tables"][0],
                {**body["form"]["schema"]["tables"][1], "name": "policy"},
            ]
        },
    }
    failed = save(changed, body["revision"])

    assert failed.status_code == 409
    assert failed.json()["detail"].startswith("rating/workbench could not be written: ")
    assert "Permission denied" in failed.json()["detail"]
    # The file it wrote first is put back, and the one it would have made is not there.
    assert files_of(project) == before


def test_a_file_the_rollback_cannot_put_back_is_named_by_its_path_in_the_folder(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute.routes import workbench as routes

    enable(project)
    write_form(project, form(POLICY))
    body = served()

    tried: list[Path] = []

    def write_fails(out_path: Path, payload: bytes, touched: list[Any]) -> None:
        tried.append(out_path)
        raise PermissionError("[Errno 13] Permission denied")

    monkeypatch.setattr(routes, "_stage_artifact_write_bytes", write_fails)
    monkeypatch.setattr(routes, "_rollback_artifacts", lambda touched: tried)
    policy = body["form"]["schema"]["tables"][1]
    labelled = {**policy["inputs"][2], "label": "State"}
    tables = [
        body["form"]["schema"]["tables"][0],
        {**policy, "inputs": [*policy["inputs"][:2], labelled, *policy["inputs"][3:]]},
    ]
    failed = save({**body["form"], "schema": {"tables": tables}}, body["revision"])

    assert failed.status_code == 409
    # schema/ and sample/ hold files of one name, so the bare name would not say which.
    assert failed.json()["detail"].endswith(
        "; rating/workbench/schema/policy_details.json could not be put back: restore them "
        "from version history"
    )


def test_a_form_the_folder_cannot_hold_is_refused_naming_each_reason(project: Path) -> None:
    enable(project)
    write_form(project, form(POLICY))
    body = served()
    before = files_of(project)
    clashing = {**POLICY, "name": "Policy_Details"}
    # A name the quote store keeps for itself, which it could not hold beside its own.
    reserved = {**POLICY, "name": "haute_extras"}
    unnamed_sheet = {"id": "p2", "title": "***", "width": 1280, "sections": [section("s2")]}
    unholdable = {
        **body["form"],
        "schema": {"tables": [*body["form"]["schema"]["tables"], clashing, reserved]},
        "pages": [*body["form"]["pages"], unnamed_sheet],
    }

    refused = save(unholdable, body["revision"])

    assert refused.status_code == 422
    detail = refused.json()["detail"]
    assert "the tables 'policy_details' and 'Policy_Details' share a name" in detail
    assert (
        "table 'haute_extras' starts haute_, which the quote store keeps for its own names"
        in detail
    )
    assert "the sheet '***' needs a title with a letter or a digit" in detail
    assert files_of(project) == before


def test_a_form_whose_files_would_not_read_back_is_refused_writing_nothing(
    project: Path,
) -> None:
    # A field shown twice in one component, with a look of its own: its file writes the
    # look at each place the field is, which reads back as two looks for one column, which no
    # read takes.
    enable(project)
    write_form(project, form(POLICY))
    body = served()
    before = files_of(project)
    state = {"table": "policy_details", "column": "state"}
    looks = {
        "id": "collection_c1c1",
        "name": "collection_1",
        "type": "collection",
        "x": 0,
        "y": 0,
        "w": 720,
        "h": 120,
        "fields": [state, state],
        "column_looks": [{**state, "bold": True}],
    }
    unsaved = {
        **form(POLICY),
        "pages": [
            {"id": "p", "title": "Sheet 1", "width": 1280, "sections": [section("s", looks)]}
        ],
    }

    refused = save(unsaved, body["revision"])

    assert refused.status_code == 422
    detail = refused.json()["detail"]
    assert detail.startswith(
        "its files would not read back as a form: rating/workbench is not a workbench form: "
        "components/collection_1.json: "
    ), detail
    assert "two looks for policy_details.state" in detail, detail
    assert files_of(project) == before


def test_a_save_is_one_commit_on_the_ledger_which_commit_sweeps_from_then_on(
    project: Path,
) -> None:
    enable(project)
    init_repo(project)
    write_form(project, form(POLICY))
    body = served()

    renamed = {**body["form"], "name": "renamed"}
    saved = save(renamed, body["revision"])

    assert saved.status_code == 200, saved.text
    result = saved.json()
    assert result["warnings"] == []
    assert result["identity_required"] is False
    assert result["git_sha"] == git(project, "rev-parse", LEDGER)
    # Only the file that changed is in the save, and its message names it.
    assert git(project, "show", "--name-only", "--format=", result["git_sha"]).splitlines() == [
        "rating/workbench/form.json"
    ]
    assert git(project, "log", "-1", "--format=%s", LEDGER) == "Updated workbench/form"

    # The same form saved again changes nothing on disk, so there is no commit to make.
    again = save(renamed, result["revision"])
    assert again.status_code == 200, again.text
    assert again.json()["git_sha"] is None
    assert git(project, "rev-parse", LEDGER) == result["git_sha"]

    # A milestone folds the save into the working branch, with every file the folder holds:
    # the pipeline folder is versioned whole. An edit made by hand, and a file added, are
    # swept into the next milestone.
    # As the editor's Commit does it, naming the pipeline folder whose new files it sweeps.
    commit_milestone("Form laid out", project, pipeline_folder="rating", cwd=project)
    swept = git(project, "diff", "--name-only", "main", WORKING).splitlines()
    assert sorted(path for path in swept if path.startswith("rating/workbench/")) == sorted(
        f"rating/workbench/{path}" for path in files_of(project)
    )
    first = git(project, "rev-parse", WORKING)
    index = project / FORM / "form.json"
    index.write_bytes(index.read_bytes().replace(b'"renamed"', b'"by hand"'))
    commit_milestone("Named by hand", project, pipeline_folder="rating", cwd=project)
    assert git(project, "diff", "--name-only", first, WORKING).splitlines() == [
        "rating/workbench/form.json"
    ]


def test_a_save_without_a_working_branch_is_written_and_names_what_it_left_off(
    project: Path,
) -> None:
    enable(project)
    write_form(project, form(POLICY))
    git(project, "init", "-b", "main")
    body = served()

    saved = save({**body["form"], "name": "renamed"}, body["revision"])

    assert saved.status_code == 200, saved.text
    # The repository has no working branch chosen yet: the form is owed to the ledger
    # once one is, and the save says so.
    assert capture(saved.json()) == {
        "git_sha": None,
        "warnings": [
            "Changes saved, but no working branch is chosen, so version history doesn't "
            "have them yet. Choose a working branch to keep them."
        ],
        "identity_required": False,
        "uncaptured": ["rating/workbench/form.json"],
    }
    assert json.loads((project / FORM / "form.json").read_bytes())["name"] == "renamed"


def test_a_save_the_ledger_cannot_capture_is_written_and_says_why(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    enable(project)
    init_repo(project)
    write_form(project, form(POLICY))
    tip = git(project, "rev-parse", LEDGER)
    body = served()

    # A working branch the guardrails refuse: the save lands, the capture fails with its
    # reason, as any failure of the capture does.
    write_working_branch(project, "broken-save")
    failed = save({**body["form"], "name": "first"}, body["revision"])

    assert failed.status_code == 200, failed.text
    assert failed.json()["git_sha"] is None
    assert failed.json()["identity_required"] is False
    assert len(failed.json()["warnings"]) == 1
    assert failed.json()["warnings"][0].startswith("Changes saved; version capture failed: ")
    assert failed.json()["uncaptured"] == ["rating/workbench/form.json"]

    # No commit identity (a restored hosted container): the save lands, and the response
    # says the capture waits on one, so the editor can ask.
    write_working_branch(project, WORKING)
    monkeypatch.setattr(_git, "get_identity", lambda cwd=None: (None, None))
    uncaptured = save({**body["form"], "name": "second"}, failed.json()["revision"])

    assert uncaptured.status_code == 200, uncaptured.text
    assert capture(uncaptured.json()) == {
        "git_sha": None,
        "warnings": [
            "Changes saved, but version capture needs a git identity. "
            "Set your name and email to keep version history."
        ],
        "identity_required": True,
        "uncaptured": ["rating/workbench/form.json"],
    }
    assert git(project, "rev-parse", LEDGER) == tip
    assert served()["revision"] == uncaptured.json()["revision"]


def test_a_body_that_is_not_a_form_is_refused_and_nothing_is_written(project: Path) -> None:
    enable(project)
    write_form(project, form(POLICY))
    body = served()
    before = files_of(project)

    refused = save({**form(), "pages": [], "colour": "red"}, body["revision"])
    unrevised = client().put("/api/workbench/form", json={"form": body["form"]})

    assert refused.status_code == 422
    assert unrevised.status_code == 422
    assert files_of(project) == before


def test_a_refused_form_is_answered_with_where_and_why_never_the_form_again(
    project: Path,
) -> None:
    enable(project)
    write_form(project, form(POLICY))
    body = served()
    before = files_of(project)
    # The analyst names a table of their own quote_details: a table is known by its name
    # (READ-23), so it is taken for Haute's, whose structure it does not have.
    theirs = {"name": "quote_details", "outputs": [column("premium", "float")]}
    sent = {**form(POLICY), "schema": {"tables": [POLICY, theirs]}}

    refused = save(sent, body["revision"])

    assert refused.status_code == 422
    [error] = refused.json()["detail"]
    assert error["loc"] == ["body", "form"]
    assert error["msg"] == (
        "Value error, quote_details is Haute's: a one-row input table of the columns "
        f"{', '.join(name for name, _ in QUOTE_DETAILS_COLUMNS)}, in that order, whose "
        "structure is not changed"
    )
    # Neither the form refused nor the context its message already says.
    assert "input" not in error and "ctx" not in error
    assert len(refused.content) < 400
    assert files_of(project) == before


def test_a_form_holding_an_id_twice_is_refused_naming_it(project: Path) -> None:
    enable(project)
    write_form(project, form(POLICY))
    body = served()
    twice = form(POLICY)
    twice["pages"].append(dict(twice["pages"][0]))

    refused = save(twice, body["revision"])

    assert refused.status_code == 422
    [error] = refused.json()["detail"]
    assert error["msg"].endswith("Two sheets have the id 'p': each needs one of its own")


def test_a_form_save_holds_the_repository_lock_from_its_check_to_its_capture(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute import _git_lock
    from haute.routes import workbench as routes

    enable(project)
    init_repo(project)
    write_form(project, form(POLICY))
    body = served()
    held: list[bool] = []
    stage = routes._stage_artifact_write_bytes

    def probed(out_path: Path, payload: bytes, touched: list[Any]) -> None:
        # Another thread, as a branch switch would be, cannot take the lock meanwhile.
        def probe() -> None:
            locks = _git_lock._locks_for(project)
            taken = [lock.acquire(blocking=False) for lock in locks]
            for lock, got in zip(locks, taken, strict=True):
                if got:
                    lock.release()
            held.append(not any(taken))

        prober = context_thread(probe)
        prober.start()
        prober.join()
        stage(out_path, payload, touched)

    monkeypatch.setattr(routes, "_stage_artifact_write_bytes", probed)

    saved = save({**body["form"], "name": "renamed"}, body["revision"])

    assert saved.status_code == 200, saved.text
    assert held == [True]


def test_the_form_routes_answer_404_while_the_workbench_is_not_enabled(project: Path) -> None:
    enable(project, "[workbench]\nenabled = false\n")
    message = "The workbench is not enabled: set [workbench] enabled = true in haute.toml."

    response = client().get("/api/workbench/form")
    saved = client().put("/api/workbench/form", json={"form": form(), "base_revision": "x"})
    tables = client().post("/api/workbench/tables", json={"form": form()})

    assert (response.status_code, response.json()["detail"]) == (404, message)
    assert (saved.status_code, saved.json()["detail"]) == (404, message)
    assert (tables.status_code, tables.json()["detail"]) == (404, message)
    assert not (project / FORM).exists()


def test_a_params_file_s_header_and_a_column_s_values_are_served(project: Path) -> None:
    # Served while the workbench is not enabled: Banding's Import from params reads them too.
    (project / "haute.toml").write_text(
        '[project]\nname = "x"\npipeline = "rating/main.py"\n\n[workbench]\nenabled = false\n',
        encoding="utf-8",
    )
    params = project / "rating" / "files" / "params"
    params.mkdir(parents=True)
    (params / "states.csv").write_text(
        "state,name\nNY,New York\nCA,California\nNY,New York\n", encoding="utf-8"
    )

    header = client().get("/api/workbench/params", params={"file": "states.csv"})
    values = client().get("/api/workbench/params", params={"file": "states.csv", "column": "state"})
    missing = client().get("/api/workbench/params", params={"file": "states.csv", "column": "code"})

    assert header.json() == {"columns": ["state", "name"], "values": []}
    assert values.json() == {"columns": ["state", "name"], "values": ["NY", "CA"]}
    # What stops a file or a column giving values answers 409 with why.
    assert (missing.status_code, missing.json()["detail"]) == (
        409,
        "rating/files/params/states.csv has no column code.",
    )


def test_a_file_that_is_not_utf8_answers_409_naming_it(project: Path) -> None:
    enable(project)
    write_form(project, form(POLICY))
    (project / FORM / "schema" / "policy_details.json").write_bytes(b'{"role": "\xff\xfe"}')

    for route in ("/api/workbench/tables", "/api/workbench/form"):
        response = client().get(route)
        assert response.status_code == 409
        assert response.json()["detail"].startswith(
            "rating/workbench/schema/policy_details.json is not UTF-8 text"
        )


def test_a_misshapen_file_and_a_bad_table_answer_409_naming_what_to_fix(project: Path) -> None:
    enable(project)
    write_form(project, form(POLICY))
    path = project / FORM / "schema" / "policy_details.json"
    table = json.loads(path.read_bytes())
    path.write_bytes((json.dumps({**table, "colour": "red"}) + "\n").encode())
    misshapen = client().get("/api/workbench/tables")
    assert misshapen.status_code == 409
    assert misshapen.json()["detail"].startswith(
        "rating/workbench/schema/policy_details.json: colour: "
    )

    enable(project, '[workbench]\nenabled = "yes"\n')
    for route in ("/api/workbench", "/api/workbench/tables"):
        bad = client().get(route)
        assert bad.status_code == 409
        assert bad.json()["detail"] == "[workbench].enabled must be true or false"


def test_a_table_the_pipeline_cannot_take_answers_the_structured_422(project: Path) -> None:
    enable(project)
    write_form(project, form(POLICY))
    # A save refuses every name the pipeline would; a hand edit of the folder can still give
    # a table a column whose name the quote store keeps for its own.
    path = project / FORM / "schema" / "policy_details.json"
    written = json.loads(path.read_bytes())
    written["inputs"].append({"name": "haute_x", "type": "int"})
    path.write_bytes((json.dumps(written, indent=2) + "\n").encode("utf-8"))

    response = client().get("/api/workbench/tables")

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "error_code": "workbench_tables_invalid",
        "message": "The Workbench Input's table 'policy_details' has a column 'haute_x', and a "
        "name starting haute_ is the quote store's own.",
    }


def test_the_tables_of_an_unsaved_form_are_served_without_writing_it(project: Path) -> None:
    enable(project)
    write_form(project, form(POLICY))
    before = files_of(project)
    unsaved = form({**POLICY, "name": "policy"}, PRICING, sample={"policy": [{"state": "CA"}]})

    posted = client().post("/api/workbench/tables", json={"form": unsaved})

    assert posted.status_code == 200, posted.text
    body = posted.json()
    assert [t["name"] for t in body["tables"]] == ["quote_details", "policy"]
    assert body["sample"] == {"policy": {"state": "CA"}}
    assert [t["name"] for t in body["response_tables"]] == ["pricing_output"]
    # The folder is as it was, and the saved form is what the GET still serves.
    assert files_of(project) == before
    assert [t["name"] for t in client().get("/api/workbench/tables").json()["tables"]] == [
        "quote_details",
        "policy_details",
    ]


def test_a_value_typed_on_a_sheet_that_does_not_show_is_left_out_of_the_sample(
    project: Path,
) -> None:
    enable(project)
    policy = {**POLICY, "inputs": [*POLICY["inputs"], column("bi_cover", "bool")]}
    # The second sheet shows only while the box on the first is ticked.
    shown = {
        "id": "w_policy",
        "name": "w_policy",
        "type": "collection",
        "x": 0,
        "y": 0,
        "w": 720,
        "h": 120,
    }
    typed = {"state": "CA", "exposure": "1,000", "bi_cover": False}
    unsaved = {
        **form(policy, sample={"policy_details": [typed]}),
        "pages": [
            {
                "id": "p_main",
                "title": "Main",
                "width": 1280,
                "sections": [
                    section(
                        "s_main",
                        {
                            **shown,
                            "fields": [
                                {"table": "policy_details", "column": "state"},
                                {"table": "policy_details", "column": "bi_cover"},
                            ],
                        },
                    )
                ],
            },
            {
                "id": "p_bi",
                "title": "Business interruption",
                "show_when": {
                    "conditions": [
                        {"table": "policy_details", "column": "bi_cover", "op": "ticked"}
                    ]
                },
                "width": 1280,
                "sections": [
                    section(
                        "s_bi",
                        {
                            **shown,
                            "id": "w_bi",
                            "name": "w_bi",
                            "fields": [{"table": "policy_details", "column": "exposure"}],
                        },
                    )
                ],
            },
        ],
    }

    posted = client().post("/api/workbench/tables", json={"form": unsaved})

    assert posted.status_code == 200, posted.text
    # The box is unticked, so it is false, and the exposure on the hidden sheet is left out.
    assert posted.json()["sample"] == {"policy_details": {"state": "CA", "bi_cover": False}}


def test_an_unsaved_form_the_pipeline_cannot_take_answers_the_structured_422(
    project: Path,
) -> None:
    enable(project)

    refused = client().post(
        "/api/workbench/tables", json={"form": form({**POLICY, "name": "haute_policy"})}
    )
    misshapen = client().post("/api/workbench/tables", json={"form": {**form(), "pages": []}})

    assert refused.status_code == 422
    assert "table 'haute_policy' starts haute_" in refused.json()["detail"]["message"]
    assert misshapen.status_code == 422
    assert not (project / FORM).exists()


def test_haute_server_answers_the_status_ahead_of_its_404_guard_behind_the_session(
    client: TestClient,
) -> None:
    # The route clients in this suite carry the session cookie unless one is given.
    rejected = client.get("/api/workbench", headers={"cookie": "haute_session=wrong"})
    answered = client.get("/api/workbench")

    assert rejected.status_code == 403
    # The /api 404 guard would answer {"detail": "No such route: ..."} instead.
    assert answered.status_code == 200
    assert isinstance(answered.json()["enabled"], bool)


async def test_two_saves_against_one_revision_take_turns_and_the_second_is_refused(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two saves quoting the same revision: the lock serialises them, so the second reads
    the first's revision and is refused, and the folder holds the first's form."""
    import asyncio

    import httpx

    from haute.routes import workbench as route

    enable(project)
    write_form(project, form(POLICY))
    reads = 0
    writes = 0
    go = threading.Event()
    read_folder = route.read_folder_unlocked
    stage = route._stage_artifact_write_bytes

    def counted_read(config: Any) -> Any:
        nonlocal reads
        reads += 1
        return read_folder(config)

    def gated_write(out_path: Path, payload: bytes, touched: list[Any]) -> None:
        # The first save stalls inside its write until the test lets it go.
        nonlocal writes
        writes += 1
        if writes == 1:
            go.wait(timeout=5)
        stage(out_path, payload, touched)

    monkeypatch.setattr(route, "read_folder_unlocked", counted_read)
    monkeypatch.setattr(route, "_stage_artifact_write_bytes", gated_write)
    # ``save_lock`` is the project's one module-level asyncio.Lock, and asyncio binds a
    # lock to the loop that first awaits it while held. An earlier test that contended it
    # left it bound to that test's loop, so the second save here would raise "bound to a
    # different event loop" instead of being refused as stale; and contending the shared
    # lock here would bind it for a later test. A fresh lock binds to this client's loop;
    # serialisation is what the route needs from it, through `project_write`.
    from haute.routes import _helpers

    monkeypatch.setattr(_helpers, "save_lock", asyncio.Lock())
    transport = httpx.ASGITransport(app=app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        body = (await http.get("/api/workbench/form")).json()
        reads = 0
        saves = [
            {"form": {**body["form"], "name": name}, "base_revision": body["revision"]}
            for name in ("first", "second")
        ]

        async def release() -> None:
            # Once the other save has read the revision too (which the lock never lets it
            # while the first holds it) or after a moment, let the stalled write go.
            for _ in range(100):
                if reads >= 2:
                    break
                await asyncio.sleep(0.01)
            go.set()

        one, other, _ = await asyncio.gather(
            http.put("/api/workbench/form", json=saves[0]),
            http.put("/api/workbench/form", json=saves[1]),
            release(),
        )

    assert sorted([one.status_code, other.status_code]) == [200, 409]
    winner = one if one.status_code == 200 else other
    on_disk = json.loads((project / FORM / "form.json").read_bytes())
    assert on_disk["name"] == winner.json()["form"]["name"]


# ── The tables' SQL ───────────────────────────────────────────────────────

#: Two hosted environments, each on its own engine.
STORES = (
    '\n[workbench.store.test]\nprovider = "postgresql"\nauth = "password"\n'
    'username_env = "STORE_USER"\npassword_env = "STORE_PASSWORD"\n'
    '\n[workbench.store.prod]\nprovider = "sqlserver"\nauth = "managed-identity"\n'
)


def _committed_on_main(project: Path) -> None:
    """The project as the default branch holds it, committed with nothing else in git."""
    git(project, "init", "-b", "main")
    git(project, "config", "user.name", "Test Actuary")
    git(project, "config", "user.email", "test@example.com")
    git(project, "add", "-A")
    git(project, "commit", "-m", "the form as it was merged")


def _store_sql(spec: dict[str, Any], start: str = "branch") -> Any:
    return client().post("/api/workbench/store-sql", json={"form": spec, "start": start})


def _exposure_named(name: str) -> dict[str, Any]:
    """POLICY with its exposure column renamed *name*: to the quote store, a column removed
    and another added, as a column is known by its name (READ-23)."""
    return {**POLICY, "inputs": [POLICY["inputs"][0], column(name, "float")]}


def test_the_tables_sql_starts_from_the_form_the_default_branch_holds(project: Path) -> None:
    enable(project, "[workbench]\nenabled = true\n" + STORES)
    write_form(project, form(POLICY))
    _committed_on_main(project)
    # A feature branch checked out, its own form committed, and another unsaved on disk: the
    # SQL starts from neither, only from the default branch's.
    git(project, "checkout", "-b", "feature")
    write_form(project, form(_exposure_named("exposure_feature")))
    git(project, "commit", "-am", "the feature's form")
    write_form(project, form(_exposure_named("exposure_disk")))
    on_disk = files_of(project)
    status = git(project, "status", "--porcelain")

    answered = _store_sql(form(_exposure_named("value")))

    assert answered.status_code == 200, answered.text
    body = answered.json()
    assert body["branch"] == "main"
    assert [(each["name"], each["provider"]) for each in body["environments"]] == [
        ("test", "postgresql"),
        ("prod", "sqlserver"),
    ]
    test, prod = (each["sql"] for each in body["environments"])
    # The column renamed is one added and one kept with its values (READ-23), in each
    # engine's own SQL, from the default branch's form; nothing renamed or made afresh.
    assert "ALTER TABLE policy_details ADD COLUMN value" in test
    assert "ALTER TABLE policy_details ADD value" in prod
    for sql in (test, prod):
        assert "Keep unused policy_details.exposure" in sql
        assert "DROP " not in sql
    assert "RENAME" not in test and "sp_rename" not in prod
    assert "CREATE TABLE" not in test + prod
    assert "exposure_feature" not in test + prod
    assert "exposure_disk" not in test + prod
    # Read from git with nothing checked out: the branch and the folder on disk are as they were.
    assert git(project, "branch", "--show-current") == "feature"
    assert files_of(project) == on_disk
    assert git(project, "status", "--porcelain") == status


def test_the_tables_sql_starts_from_none_when_asked_or_when_the_branch_holds_no_form(
    project: Path,
) -> None:
    enable(project, "[workbench]\nenabled = true\n" + STORES)
    write_form(project, form(POLICY))
    # The status names the environments the project declares, in its order.
    assert client().get("/api/workbench").json()["environments"] == ["test", "prod"]

    # No repository at all: the SQL makes every table.
    outside = _store_sql(form(POLICY)).json()
    assert outside["branch"] is None
    for each in outside["environments"]:
        assert "CREATE TABLE quote_details" in each["sql"]
        assert "CREATE TABLE haute_activity" in each["sql"]

    _committed_on_main(project)
    fresh = _store_sql(form(POLICY), start="none").json()
    assert fresh["branch"] is None
    assert "CREATE TABLE policy_details" in fresh["environments"][0]["sql"]
    # From the branch, a form it already holds has nothing to change.
    same = _store_sql(form(POLICY)).json()
    assert same["branch"] == "main"
    assert same["environments"][0]["sql"] == "-- No missing tables or columns.\n"


def test_the_tables_sql_with_no_environment_declared_is_none(project: Path) -> None:
    enable(project)
    write_form(project, form(POLICY))

    assert _store_sql(form(POLICY)).json() == {"branch": None, "environments": []}


def test_the_tables_sql_refuses_a_body_that_is_not_a_form_and_a_disabled_workbench(
    project: Path,
) -> None:
    enable(project)
    assert _store_sql({"schema": "no"}).status_code == 422
    assert client().post("/api/workbench/store-sql", json={"form": form(POLICY)}).status_code == 422
    # A name no table may hold is refused as the tables refuse it, never written into SQL.
    hostile = {**POLICY, "inputs": [column("a\nDELETE FROM policy_details;\n--", "float")]}
    refused = _store_sql(form(hostile))
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"]["error_code"] == "workbench_tables_invalid"

    enable(project, "")
    assert _store_sql(form(POLICY)).status_code == 404


# ── Comparing the form with an environment's tables ───────────────────────

#: dev on a SQLite file, which its connection places, and prod declared with no connection.
DEV_STORES = (
    '\n[workbench.store.dev]\nprovider = "sqlite"\n'
    '\n[workbench.store.prod]\nprovider = "sqlserver"\nauth = "managed-identity"\n'
)


def _dev_database(project: Path, spec: dict[str, Any]) -> Path:
    """dev's connection to a SQLite file of the project's, its tables made from the SQL Haute
    writes for *spec* from none, as their owner makes them."""
    from haute._quote_store import store_url
    from haute._workbench_form import FormSpec, with_quote_details
    from tests._store_servers import Target

    (project / "connections.toml").write_bytes(
        b'[dev]\nprovider = "sqlite"\ndatabase = "dev.sqlite"\n'
    )
    database = project / "dev.sqlite"
    Target("sqlite", store_url(database)).make(with_quote_details(FormSpec.model_validate(spec)))
    return database


def _catalogue(environment: str = "dev") -> Any:
    return client().post(
        "/api/workbench/store-catalogue", json={"environment": environment, "refresh": True}
    )


def _compare(spec: dict[str, Any], tables: Any, renames: Any = ()) -> Any:
    snapshot = client().post(
        "/api/workbench/store-catalogue", json={"environment": "dev", "refresh": False}
    )
    assert snapshot.status_code == 200, snapshot.text
    return client().post(
        "/api/workbench/store-compare",
        json={
            "form": spec,
            "environment": "dev",
            "tables": tables,
            "renames": list(renames),
            "target": snapshot.json()["target"],
        },
    )


def test_the_catalogue_is_read_through_the_environment_s_connection_changing_nothing(
    project: Path,
) -> None:
    enable(project, "[workbench]\nenabled = true\n" + DEV_STORES)
    write_form(project, form(POLICY))
    database = _dev_database(project, form(POLICY))
    before = database.read_bytes()

    answered = _catalogue()

    assert answered.status_code == 200, answered.text
    body = answered.json()
    assert body["environment"] == "dev"
    tables = {each["name"]: each for each in body["tables"]}
    assert set(tables) == {"quote_details", "policy_details", "haute_activity"}
    assert {each["name"] for each in tables["policy_details"]["columns"]} >= {
        "quote_reference",
        "quote_option",
        "exposure",
    }
    assert tables["policy_details"]["quotes"] == 0
    assert database.read_bytes() == before


def test_a_catalogue_request_must_say_whether_to_refresh(project: Path) -> None:
    enable(project, "[workbench]\nenabled = true\n" + DEV_STORES)
    write_form(project, form(POLICY))
    _dev_database(project, form(POLICY))

    response = client().post("/api/workbench/store-catalogue", json={"environment": "dev"})

    assert response.status_code == 422, response.text
    assert "refresh" in response.text
    assert not (project / ".haute_cache/workbench/catalogues/dev.json").exists()


def test_catalogue_snapshots_persist_until_refreshed_and_follow_the_connection(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sqlite3

    from haute._quote_store import _compare as compare_module

    enable(project, "[workbench]\nenabled = true\n" + DEV_STORES)
    write_form(project, form(POLICY))
    database = _dev_database(project, form(POLICY))
    first = _catalogue().json()
    assert first["checked_at"] > 0
    cache = project / ".haute_cache/workbench/catalogues/dev.json"
    saved = cache.read_bytes()
    assert str(project).encode() not in saved
    assert b"dev.sqlite" not in saved
    with sqlite3.connect(database) as connection:
        connection.execute("ALTER TABLE policy_details ADD COLUMN new_field TEXT")

    # A new client reads the persisted snapshot without reaching the database.
    with monkeypatch.context() as patch:

        def unavailable(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("The cached request must not read the database")

        patch.setattr(compare_module, "read_store_catalogue", unavailable)
        cached = client().post(
            "/api/workbench/store-catalogue", json={"environment": "dev", "refresh": False}
        )
        assert cached.status_code == 200, cached.text
        assert cached.json() == first
    refreshed = _catalogue().json()
    assert refreshed["checked_at"] >= first["checked_at"]
    assert "new_field" in {
        column["name"] for table in refreshed["tables"] for column in table["columns"]
    }
    assert cache.read_bytes() != saved

    # A changed database under the same environment name must not reuse old metadata.
    (project / "connections.toml").write_text(
        '[dev]\nprovider = "sqlite"\ndatabase = "other.sqlite"\n'
    )
    missing = client().post(
        "/api/workbench/store-catalogue", json={"environment": "dev", "refresh": False}
    )
    assert missing.status_code == 409, missing.text
    assert not (project / "other.sqlite").exists()


def test_comparison_refuses_a_snapshot_from_a_previous_connection(project: Path) -> None:
    enable(project, "[workbench]\nenabled = true\n" + DEV_STORES)
    write_form(project, form(POLICY))
    _dev_database(project, form(POLICY))
    snapshot = _catalogue().json()
    (project / "connections.toml").write_text(
        '[dev]\nprovider = "sqlite"\ndatabase = "other.sqlite"\n'
    )

    response = client().post(
        "/api/workbench/store-compare",
        json={
            "form": form(POLICY),
            "environment": "dev",
            "tables": snapshot["tables"],
            "target": snapshot["target"],
        },
    )

    assert response.status_code == 409, response.text
    assert "connection changed" in response.text
    assert "Refresh" in response.text
    assert "other.sqlite" not in response.text
    assert not (project / "other.sqlite").exists()


@pytest.mark.parametrize("changed", ["schema", "username"])
def test_comparison_checks_schema_and_sign_in_without_reading_the_database(
    project: Path, monkeypatch: pytest.MonkeyPatch, changed: str
) -> None:
    from haute._quote_store import _compare as compare_module
    from haute._quote_store._catalogue import Catalogue

    enable(
        project,
        '[workbench]\nenabled = true\n[workbench.store.dev]\nprovider = "postgresql"\n'
        'auth = "password"\nusername_env = "DEV_USER"\npassword_env = "DEV_PASSWORD"\n',
    )
    write_form(project, form(POLICY))
    connection = (
        '[dev]\nprovider = "postgresql"\nserver = "hidden-host.invalid"\ndatabase = "quotes"\n'
        'schema = "original"\nauth = "password"\nusername_env = "DEV_USER"\n'
        'password_env = "DEV_PASSWORD"\n'
    )
    (project / "connections.toml").write_text(connection)
    monkeypatch.setenv("DEV_USER", "hidden-user")
    monkeypatch.setenv("DEV_PASSWORD", "hidden-password")
    reads = []

    def read_catalogue(location: Any) -> Catalogue:
        reads.append(location)
        return Catalogue(tables=())

    monkeypatch.setattr(compare_module, "read_store_catalogue", read_catalogue)
    snapshot = _catalogue().json()
    body = {
        "form": form(POLICY),
        "environment": "dev",
        "tables": snapshot["tables"],
        "target": snapshot["target"],
    }
    assert client().post("/api/workbench/store-compare", json=body).status_code == 200
    if changed == "schema":
        (project / "connections.toml").write_text(
            connection.replace('schema = "original"', 'schema = "replacement"')
        )
    else:
        monkeypatch.setenv("DEV_USER", "replacement-user")
    response = client().post("/api/workbench/store-compare", json=body)
    assert response.status_code == 409, response.text
    assert "connection changed" in response.text
    assert "Refresh" in response.text
    assert len(reads) == 1
    assert "hidden" not in response.text and "replacement" not in response.text


def test_store_review_keeps_retention_separate_from_additions(project: Path) -> None:
    import sqlite3

    enable(project, "[workbench]\nenabled = true\n" + DEV_STORES)
    old = form(
        {**POLICY, "inputs": [*POLICY["inputs"], column("legacy")]},
        {**PRICING, "outputs": [*PRICING["outputs"], column("old_premium", "float")]},
        {
            "name": "locations",
            "rows": "many",
            "inputs": [column("row_number", "int", index="numbered"), column("old_location")],
        },
    )
    write_form(project, old)
    database = _dev_database(project, old)
    with sqlite3.connect(database) as raw:
        raw.execute("ALTER TABLE haute_activity DROP COLUMN actor")
        raw.execute("ALTER TABLE policy_details DROP COLUMN state")
        raw.execute("ALTER TABLE policy_details ADD COLUMN state TEXT NOT NULL DEFAULT ''")
    proposed = form(
        {**POLICY, "inputs": [column("territory"), column("exposure", "float")]},
        PRICING,
        {"name": "locations", "rows": "one", "inputs": [column("new_location")]},
    )
    tables = _catalogue().json()["tables"]
    renames = [{"table": "policy_details", "column": "territory", "was": "state"}]
    response = _compare(proposed, tables, renames)
    assert response.status_code == 200, response.text
    answer = response.json()
    extras = {(each["table"], each["column"]): each for each in answer["extras"]}
    assert "survive saves" in extras["policy_details", "legacy"]["retention"]
    assert "later saves clear" in extras["pricing_output", "old_premium"]["retention"]
    assert "manual migration" in extras["locations", "old_location"]["retention"]
    assert all(each["kind"] in {"add column", "create table"} for each in answer["changes"])
    assert any(
        each["table"] is None and "haute_activity.actor" in each["message"]
        for each in answer["differences"]
    )
    assert any(
        each["column"] == "territory" and each["kind"] == "null" for each in answer["differences"]
    )
    from haute._workbench_form import FormSpec, with_quote_details

    fixture = json.loads(
        (Path(__file__).parent / "fixtures/ui_contracts/workbench_store_review.json").read_text()
    )
    assert fixture == {
        "form": with_quote_details(FormSpec.model_validate(proposed)).model_dump(
            mode="json", by_alias=True
        ),
        "tables": tables,
        "renames": renames,
        "answer": answer,
    }


def test_catalogue_cache_errors_require_refresh_and_failed_refresh_keeps_the_snapshot(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute import _workbench_catalogue as catalogue_module
    from haute._quote_store import _compare as compare_module
    from haute._workbench_config import WorkbenchError

    enable(project, "[workbench]\nenabled = true\n" + DEV_STORES)
    write_form(project, form(POLICY))
    _dev_database(project, form(POLICY))
    assert _catalogue().status_code == 200
    cache = project / ".haute_cache/workbench/catalogues/dev.json"
    saved = cache.read_bytes()
    with monkeypatch.context() as patch:

        def unavailable(*args: Any, **kwargs: Any) -> Any:
            raise WorkbenchError("Database unavailable")

        patch.setattr(compare_module, "read_store_catalogue", unavailable)
        failed = _catalogue()
        assert failed.status_code == 409
        assert "Database unavailable" in failed.text
        assert cache.read_bytes() == saved
    with monkeypatch.context() as patch:

        def unwritable(*args: Any, **kwargs: Any) -> None:
            raise PermissionError("Read-only directory")

        patch.setattr(catalogue_module, "atomic_write_text", unwritable)
        failed = _catalogue()
        assert failed.status_code == 409
        assert "Check write access" in failed.text
        assert cache.read_bytes() == saved
    cache.write_text("broken json")
    invalid = client().post(
        "/api/workbench/store-catalogue", json={"environment": "dev", "refresh": False}
    )
    assert invalid.status_code == 409
    assert "Refresh" in invalid.text
    assert _catalogue().status_code == 200
    assert json.loads(cache.read_text())["snapshot"]["environment"] == "dev"


def test_catalogue_cache_cannot_follow_a_directory_link_outside_the_project(project: Path) -> None:
    enable(project, "[workbench]\nenabled = true\n" + DEV_STORES)
    write_form(project, form(POLICY))
    _dev_database(project, form(POLICY))
    outside = project.parent / "outside-catalogue-cache"
    outside.mkdir()
    _link_directory(outside, project / ".haute_cache")
    response = _catalogue()
    assert response.status_code == 403, response.text
    assert "outside the project root" in response.text
    assert not list(outside.iterdir())


def test_the_form_compared_with_dev_s_tables_writes_the_sql_that_makes_them_match(
    project: Path,
) -> None:
    enable(project, "[workbench]\nenabled = true\n" + DEV_STORES)
    write_form(project, form(POLICY))
    database = _dev_database(project, form(POLICY))
    tables = _catalogue().json()["tables"]
    # The form's exposure is renamed: compared by name, value is missing and exposure is
    # the database's own, until the analyst pairs the two.
    renamed = form(_exposure_named("value"))

    unpaired = _compare(renamed, tables).json()
    assert {"table": "policy_details", "column": "value", "kind": "missing"}.items() <= next(
        each for each in unpaired["differences"] if each["column"] == "value"
    ).items()
    assert {
        "table": "policy_details",
        "column": "exposure",
        "form_table": "policy_details",
        "quotes": 0,
        "retention": (
            "Retain this unused column; supported values survive saves until the row is deleted."
        ),
    } in (unpaired["extras"])
    assert not unpaired["matches"]
    assert "branch" not in unpaired

    pairing = {"table": "policy_details", "column": "value", "was": "exposure"}
    paired = _compare(renamed, tables, [pairing])
    assert paired.status_code == 200, paired.text
    body = paired.json()
    assert body["renames"] == [pairing]
    assert body["extras"] == []
    assert "ADD COLUMN value DOUBLE" in body["sql"]
    assert "RENAME" not in body["sql"] and "DROP " not in body["sql"]
    change = next(each for each in body["changes"] if each["column"] == "value")
    assert change["kind"] == "add column" and change["disposition"] == "sql"
    assert "initially empty" in change["description"]

    import sqlite3

    raw = sqlite3.connect(database)
    try:
        raw.executescript(body["sql"])
    finally:
        raw.close()
    again = _compare(renamed, _catalogue().json()["tables"]).json()
    assert again["matches"], again["differences"]
    assert "Keep unused" not in again["sql"]
    assert "ADD COLUMN" not in again["sql"]


def test_store_export_adds_new_names_without_moving_or_removing_old_values(
    project: Path,
) -> None:
    import sqlite3

    from haute._quote_store import open_book, store_url
    from haute._workbench_form import FormSpec, with_quote_details

    enable(project, "[workbench]\nenabled = true\n" + DEV_STORES)
    old = form(
        {**POLICY, "inputs": [*POLICY["inputs"], column("legacy")]},
        {**PRICING, "outputs": [*PRICING["outputs"], column("old_premium", "float")]},
    )
    write_form(project, old)
    database = _dev_database(project, old)
    with open_book(
        store_url(database),
        with_quote_details(FormSpec.model_validate(old)),
        environment="dev",
        haute_version="1",
    ) as book:
        book.create(
            operation="review-quote",
            actor="ana",
            at=book.now(),
            reference="Q1",
            values={"policy_details": [{"state": "CA", "exposure": "125", "legacy": "kept"}]},
        )
    proposed = form(
        {
            **POLICY,
            "inputs": [column("territory"), column("exposure", "float"), column("flood_zone")],
        },
        PRICING,
    )
    pairing = {"table": "policy_details", "column": "territory", "was": "state"}
    before = database.read_bytes()

    response = _compare(proposed, _catalogue().json()["tables"], [pairing])

    assert response.status_code == 200, response.text
    review = response.json()
    assert review["blocked"] == []
    assert review["manual"] == []
    changes = {(each["table"], each["column"]): each for each in review["changes"]}
    added = changes["policy_details", "flood_zone"]
    assert added["kind"] == "add column" and added["disposition"] == "sql"
    assert "initially empty" in added["description"]
    renamed = changes["policy_details", "territory"]
    assert renamed["kind"] == "add column" and "was" not in renamed
    assert renamed["disposition"] == "sql"
    assert set(changes) == {("policy_details", "flood_zone"), ("policy_details", "territory")}
    assert {(extra["table"], extra["column"]) for extra in review["extras"]} >= {
        ("policy_details", "legacy"),
        ("pricing_output", "old_premium"),
    }
    assert not review["matches"]
    assert database.read_bytes() == before

    with sqlite3.connect(database) as raw:
        raw.executescript(review["sql"])
        assert raw.execute(
            "SELECT state, territory, exposure, flood_zone, legacy FROM policy_details"
        ).fetchone() == ("CA", None, 125.0, None, "kept")

    # Comparing structure does not claim that values have been transferred.
    verified = _compare(proposed, _catalogue().json()["tables"]).json()
    assert verified["matches"]
    assert verified["blocked"] == []
    assert verified["changes"] == []


def test_store_export_ignores_an_unresolved_rename_when_adding_missing_columns(
    project: Path,
) -> None:
    enable(project, "[workbench]\nenabled = true\n" + DEV_STORES)
    write_form(project, form(POLICY))
    _dev_database(project, form(POLICY))
    pairing = {"table": "policy_details", "column": "value", "was": "missing_source"}

    response = _compare(form(_exposure_named("value")), _catalogue().json()["tables"], [pairing])

    assert response.status_code == 200, response.text
    review = response.json()
    diagnostic = (
        "Cannot rename policy_details.missing_source to value in this database. "
        "Check the source and destination before transferring data manually."
    )
    assert diagnostic not in review["sql"]
    assert "Cannot rename" not in review["sql"]
    assert review["blocked"] == [diagnostic]
    assert "ADD COLUMN value DOUBLE" in review["sql"]
    assert "RENAME" not in review["sql"] and "DROP " not in review["sql"]


@pytest.mark.parametrize("rows", ["one", "many"])
def test_store_export_adds_columns_despite_existing_index_or_row_differences(
    project: Path,
    rows: str,
) -> None:
    import sqlite3

    enable(project, "[workbench]\nenabled = true\n" + DEV_STORES)
    old = form(
        {
            "name": "perils",
            "rows": rows,
            "inputs": [
                column("quote_row", "int", **({"index": "numbered"} if rows == "many" else {})),
                column("peril", "int"),
            ],
        },
        POLICY,
    )
    write_form(project, old)
    database = _dev_database(project, old)
    with sqlite3.connect(database) as raw:
        raw.execute(
            "INSERT INTO perils (quote_reference, quote_option, quote_row, peril) "
            "VALUES ('Q1', 1, 1, 7)"
        )
        before_key = raw.execute("PRAGMA table_info(perils)").fetchall()
    proposed = form(
        {
            "name": "perils",
            "rows": "many",
            "inputs": [
                column("PERIL" if rows == "one" else "peril", "int", index="numbered"),
                column("new_cover", "float"),
            ],
        },
        {**POLICY, "inputs": [*POLICY["inputs"], column("flood_zone")]},
        PRICING,
    )

    response = _compare(proposed, _catalogue().json()["tables"])

    assert response.status_code == 200, response.text
    review = response.json()
    assert "ALTER TABLE perils ADD COLUMN new_cover DOUBLE" in review["sql"]
    assert "renamed" not in review["sql"] and "Keep unused" not in review["sql"]
    assert {change["kind"] for change in review["changes"]} <= {"create table", "add column"}
    assert "ALTER TABLE policy_details ADD COLUMN flood_zone" in review["sql"]
    assert "CREATE TABLE pricing_output" in review["sql"]
    assert "RENAME" not in review["sql"] and "DROP " not in review["sql"]
    assert not review["matches"]
    with sqlite3.connect(database) as raw:
        raw.executescript(review["sql"])
        assert raw.execute("SELECT quote_row, peril FROM perils").fetchall() == [(1, 7)]
        after = raw.execute("PRAGMA table_info(perils)").fetchall()
        assert after[: len(before_key)] == before_key
        assert after[-1][1] == "new_cover" and after[-1][5] == 0
        assert raw.execute("SELECT new_cover FROM perils").fetchall() == [(None,)]
        assert "flood_zone" in {row[1] for row in raw.execute("PRAGMA table_info(policy_details)")}

    again = _compare(proposed, _catalogue().json()["tables"]).json()
    assert again["changes"] == []
    assert again["sql"] == "-- No missing tables or columns.\n"


def test_store_review_requires_release_coordination_for_manual_key_repairs(project: Path) -> None:
    enable(project, "[workbench]\nenabled = true\n" + DEV_STORES)
    write_form(project, form(POLICY))
    _dev_database(project, form(POLICY))
    tables = _catalogue().json()["tables"]
    policy = next(table for table in tables if table["name"] == "policy_details")
    policy["primary_key"] = []

    response = _compare(form(POLICY), tables)

    assert response.status_code == 200, response.text
    review = response.json()
    assert not review["matches"]
    assert review["changes"] == []
    assert any("is keyed by nothing" in note for note in review["manual"])


def test_an_environment_the_editor_cannot_read_is_said_naming_what_to_set(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    enable(project, "[workbench]\nenabled = true\n" + DEV_STORES)
    write_form(project, form(POLICY))

    undeclared = _catalogue("staging")
    assert undeclared.status_code == 409
    assert "haute.toml declares no staging environment" in undeclared.text
    assert "it declares dev, prod" in undeclared.text
    unconnected = _catalogue("prod")
    assert unconnected.status_code == 409
    assert "connections.toml has no [prod] connection" in unconnected.text
    # A server no name resolves to: said naming the connection and its variables, never the
    # server, the user or the password.
    enable(
        project,
        "[workbench]\nenabled = true\n"
        '\n[workbench.store.dev]\nprovider = "postgresql"\nauth = "managed-identity"\n'
        'username_env = "DEV_ROLE"\n',
    )
    (project / "connections.toml").write_bytes(
        b'[dev]\nprovider = "postgresql"\nserver = "hidden-host.invalid:5432"\n'
        b'database = "quotes"\n'
        b'auth = "password"\nusername_env = "DEV_USER"\npassword_env = "DEV_PASSWORD"\n'
    )
    (project / ".env").write_bytes(b"DEV_USER=hidden-user\nDEV_PASSWORD=hidden-password\n")
    monkeypatch.delenv("DEV_USER", raising=False)
    monkeypatch.delenv("DEV_PASSWORD", raising=False)

    unreached = _catalogue()

    assert unreached.status_code == 409, unreached.text
    assert (
        "check connections.toml [dev] and the variables its sign-in reads (DEV_USER, DEV_PASSWORD)"
    ) in unreached.text
    assert "A database that pauses when idle refuses the first connection" in unreached.text
    assert "hidden" not in unreached.text
    # A secret put where its variable's name goes, or in auth or provider, is neither answered
    # nor logged.
    import structlog.testing

    for bad in (
        b'auth = "password"\nusername_env = "DEV_USER"\npassword_env = "s3cret!pa55"\n',
        b'auth = { password = "s3cret!pa55" }\n',
    ):
        (project / "connections.toml").write_bytes(
            b'[dev]\nprovider = "postgresql"\nserver = "hidden-host.invalid:5432"\n'
            b'database = "quotes"\n' + bad
        )
        with structlog.testing.capture_logs() as logs:
            refused = _catalogue()
        assert refused.status_code == 409
        assert "s3cret!pa55" not in refused.text
        assert "s3cret!pa55" not in repr(logs)
