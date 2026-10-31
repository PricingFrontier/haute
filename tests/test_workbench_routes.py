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
from haute._workbench_folder import folder_revision
from haute._workbench_form import QUOTE_DETAILS_COLUMNS, QUOTE_DETAILS_STORE_ONLY
from haute.routes._error_handlers import install_exception_handlers
from haute.routes.workbench import router
from tests._workbench_helpers import FORM, enable, form_files, write_form


def column(name: str, type_: str = "str", **rest: Any) -> dict[str, Any]:
    return {"id": f"c_{name}", "name": name, "type": type_, **rest}


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
    "id": "t1",
    "name": "policy_details",
    "role": "input",
    "rows": "one",
    "columns": [column("state", options=["CA", "NY"]), column("exposure", "float")],
}
PRICING = {
    "id": "t2",
    "name": "pricing_output",
    "role": "output",
    "rows": "one",
    "columns": [column("premium", "float")],
}


#: Haute's quote_details table as the tables route serves it, first in every form.
#: quote_details as the Workbench Input takes it: without the columns the store keeps alone.
DETAILS = {
    "name": "quote_details",
    "rows": "one",
    "columns": [
        {"name": name, "type": type_}
        for column_id, name, type_ in QUOTE_DETAILS_COLUMNS
        if column_id not in QUOTE_DETAILS_STORE_ONLY
    ],
}


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The working directory is the project, as it is for ``haute serve``."""
    monkeypatch.chdir(tmp_path)
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
        project, form(POLICY, PRICING, sample={"t1": [{"c_state": "NY", "c_exposure": "$1,000"}]})
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
            "rows": "one",
            "columns": [
                {"name": "quote_reference", "type": "str"},
                {"name": "quote_option", "type": "int"},
                {"name": "state", "type": "str"},
                {"name": "exposure", "type": "float"},
            ],
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


def test_the_form_is_served_with_its_revision_and_saved_against_it(project: Path) -> None:
    enable(project)
    write_form(project, form(POLICY, PRICING))

    body = served()
    # Every field is served, defaults included; Haute's quote_details table first.
    assert body["form"]["schema"]["tables"][0]["name"] == "quote_details"
    assert body["form"]["schema"]["tables"][1]["columns"][3] == {
        "id": "c_exposure",
        "name": "exposure",
        "type": "float",
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


def test_a_form_grown_to_open_says_what_grew_and_a_save_keeps_it(project: Path) -> None:
    """A section too short for a component in it, as a merge can leave it, is grown as the
    form is read and said; the files are as they were until a save writes the form as read."""
    enable(project)
    tall = form(POLICY)
    tall["pages"][0]["sections"][0]["widgets"] = [
        {
            "id": "heading_t1t1",
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
    layout_path = project / FORM / "sheets" / "sheet_1.layout.json"
    layout = json.loads(layout_path.read_bytes())
    layout["sections"]["s"]["height"] = 40
    layout_path.write_bytes((json.dumps(layout) + "\n").encode())

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
    labelled = {**policy["columns"][2], "label": "State"}
    tables = [
        body["form"]["schema"]["tables"][0],
        {**policy, "columns": [*policy["columns"][:2], labelled, *policy["columns"][3:]]},
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
    clashing = {**POLICY, "id": "t3", "name": "Policy_Details"}
    # A name the quote store keeps for itself, which it could not hold beside its own.
    reserved = {**POLICY, "id": "t4", "name": "haute_extras"}
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
    # A look kept for a name the schema did not hold, and the column renamed to that name
    # since: written, both looks would be that column's, which no read takes.
    enable(project)
    write_form(project, form(POLICY))
    body = served()
    before = files_of(project)
    policy = {
        **POLICY,
        "columns": [{**POLICY["columns"][0], "name": "region"}, POLICY["columns"][1]],
    }
    kept = {"table": "t1", "column": "?region"}
    state = {"table": "t1", "column": "c_state"}
    looks = {
        "id": "collection_c1c1",
        "type": "collection",
        "x": 0,
        "y": 0,
        "w": 720,
        "h": 120,
        "fields": [kept, state],
        "column_looks": [{**kept, "bold": True}, {**state, "header_color": "#2563EB"}],
    }
    unsaved = {
        **form(policy),
        "pages": [
            {"id": "p", "title": "Sheet 1", "width": 1280, "sections": [section("s", looks)]}
        ],
    }

    refused = save(unsaved, body["revision"])

    assert refused.status_code == 422
    detail = refused.json()["detail"]
    assert detail.startswith(
        "its files would not read back as a form: rating/workbench is not a workbench form: "
        "sheets/sheet_1.json: "
    ), detail
    assert "two looks for t1.c_state" in detail, detail
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
    # The analyst names a table of their own quote_details, which is Haute's.
    theirs = {"id": "t_mine", "name": "quote_details", "role": "input", "columns": []}
    sent = {**form(POLICY), "schema": {"tables": [POLICY, theirs]}}

    refused = save(sent, body["revision"])

    assert refused.status_code == 422
    [error] = refused.json()["detail"]
    assert error["loc"] == ["body", "form"]
    assert error["msg"] == (
        "Value error, quote_details is Haute's own table of a quote's details: name yours otherwise"
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

        prober = threading.Thread(target=probe)
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
    (project / FORM / "schema" / "policy_details.json").write_bytes(b'{"id": "\xff\xfe"}')

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
    write_form(project, form({**POLICY, "name": "class"}))

    response = client().get("/api/workbench/tables")

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "error_code": "workbench_tables_invalid",
        "message": "The Workbench Input's table 'class' cannot be a port: a table's name is "
        "letters, digits and underscores, not starting with a digit and not a Python keyword.",
    }


def test_the_tables_of_an_unsaved_form_are_served_without_writing_it(project: Path) -> None:
    enable(project)
    write_form(project, form(POLICY))
    before = files_of(project)
    unsaved = form({**POLICY, "name": "policy"}, PRICING, sample={"t1": [{"c_state": "CA"}]})

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
    policy = {**POLICY, "columns": [*POLICY["columns"], column("bi_cover", "bool")]}
    # The second sheet shows only while the box on the first is ticked.
    shown = {"id": "w_policy", "type": "collection", "x": 0, "y": 0, "w": 720, "h": 120}
    typed = {"c_state": "CA", "c_exposure": "1,000", "c_bi_cover": False}
    unsaved = {
        **form(policy, sample={"t1": [typed]}),
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
                                {"table": "t1", "column": "c_state"},
                                {"table": "t1", "column": "c_bi_cover"},
                            ],
                        },
                    )
                ],
            },
            {
                "id": "p_bi",
                "title": "Business interruption",
                "show_when": {
                    "conditions": [{"table": "t1", "column": "c_bi_cover", "op": "ticked"}]
                },
                "width": 1280,
                "sections": [
                    section(
                        "s_bi",
                        {
                            **shown,
                            "id": "w_bi",
                            "fields": [{"table": "t1", "column": "c_exposure"}],
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
        "/api/workbench/tables", json={"form": form({**POLICY, "name": "class"})}
    )
    misshapen = client().post("/api/workbench/tables", json={"form": {**form(), "pages": []}})

    assert refused.status_code == 422
    assert "table 'class' cannot be a port" in refused.json()["detail"]["message"]
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
    """POLICY with its exposure column, the same by id, under *name*."""
    return {**POLICY, "columns": [POLICY["columns"][0], column(name, "float", id="c_exposure")]}


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
    # The column renamed by its id, in each engine's own SQL; nothing made afresh.
    assert "ALTER TABLE policy_details RENAME exposure TO haute_tmp_" in test
    assert "EXEC sp_rename 'policy_details.exposure', haute_tmp_" in prod
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
    assert same["environments"][0]["sql"] == (
        "-- The tables already fit the form: nothing to change.\n"
    )


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
    hostile = {**POLICY, "columns": [column("a\nDELETE FROM policy_details;\n--", "float")]}
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
    return client().post("/api/workbench/store-catalogue", json={"environment": environment})


def _compare(spec: dict[str, Any], tables: Any, renames: Any = ()) -> Any:
    return client().post(
        "/api/workbench/store-compare",
        json={"form": spec, "environment": "dev", "tables": tables, "renames": list(renames)},
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


def test_the_form_compared_with_dev_s_tables_writes_the_sql_that_makes_them_match(
    project: Path,
) -> None:
    enable(project, "[workbench]\nenabled = true\n" + DEV_STORES)
    write_form(project, form(POLICY))
    database = _dev_database(project, form(POLICY))
    tables = _catalogue().json()["tables"]
    # The form's exposure is renamed: with no default branch, the analyst pairs the two.
    renamed = form(_exposure_named("value"))

    unpaired = _compare(renamed, tables).json()
    assert {"table": POLICY["id"], "column": "c_exposure", "kind": "missing"}.items() <= next(
        each for each in unpaired["differences"] if each["column"] == "c_exposure"
    ).items()
    assert {
        "table": "policy_details",
        "column": "exposure",
        "table_id": POLICY["id"],
        "quotes": 0,
    } in (unpaired["extras"])
    assert not unpaired["matches"]

    pairing = {"table": POLICY["id"], "column": "c_exposure", "was": "exposure"}
    paired = _compare(renamed, tables, [pairing])
    assert paired.status_code == 200, paired.text
    body = paired.json()
    assert body["renames"] == [pairing]
    assert body["extras"] == []
    assert "RENAME COLUMN exposure TO haute_tmp_" in body["sql"]

    import sqlite3

    raw = sqlite3.connect(database)
    try:
        raw.executescript(body["sql"])
    finally:
        raw.close()
    again = _compare(renamed, _catalogue().json()["tables"]).json()
    assert again["matches"], again["differences"]
    assert again["sql"] == "-- The tables already fit the form: nothing to change.\n"


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
