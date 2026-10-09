"""The workbench routes (specs/workbench): the status the editor reads, the tables as the
form defines them now, and the form itself with its revision, read from the working
directory on each request, saved only against the revision it was read at, and each save
captured on the clone's save ledger as the pipeline's saves are."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from haute import _git
from haute._git_state import write_working_branch
from haute._git_transactions import commit_milestone
from haute._workbench_form import FormSpec, blank_form, render_form
from haute.routes._error_handlers import install_exception_handlers
from haute.routes.workbench import router


def column(name: str, type_: str = "str", **rest: Any) -> dict[str, Any]:
    return {"id": f"c_{name}", "name": name, "type": type_, **rest}


def form(*tables: dict[str, Any], sample: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "version": 1,
        "name": "x",
        "schema": {"tables": list(tables)},
        "pages": [{"id": "p", "title": "Sheet 1", "widgets": []}],
        "sample": sample or {},
    }


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


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The working directory is the project, as it is for ``haute serve``."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def enable(project: Path, table: str = "[workbench]\nenabled = true\n") -> None:
    (project / "haute.toml").write_text(f'[project]\nname = "x"\n\n{table}', encoding="utf-8")


def write_form(project: Path, spec: dict[str, Any], at: str = "forms/form.json") -> None:
    path = project / at
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(spec), encoding="utf-8")


def client() -> TestClient:
    app = FastAPI()
    install_exception_handlers(app)
    app.include_router(router)
    return TestClient(app)


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
    return {key: response[key] for key in ("git_sha", "warnings", "identity_required")}


def test_a_project_without_the_table_has_no_workbench(project: Path) -> None:
    assert client().get("/api/workbench").json() == {"enabled": False, "form": None}

    enable(project, "[workbench]\nenabled = false\n")
    assert client().get("/api/workbench").json() == {"enabled": False, "form": None}

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

    assert client().get("/api/workbench").json() == {"enabled": True, "form": "forms/form.json"}
    served = client().get("/api/workbench/tables")
    assert served.status_code == 200, served.text
    body = served.json()
    assert [t["label"] for t in body["tables"]] == ["policy_details"]
    assert body["tables"][0]["columns"][0]["levels"] == ["CA", "NY"]
    assert body["sample"] == {"policy_details": {"state": "NY", "exposure": 1000.0}}
    assert [t["label"] for t in body["response_tables"]] == ["pricing_output"]

    # Nothing is cached: a form changed on disk is served on the next request.
    write_form(project, form({**POLICY, "name": "policy"}, PRICING))
    assert [t["label"] for t in client().get("/api/workbench/tables").json()["tables"]] == [
        "policy"
    ]


def test_the_form_is_read_from_where_the_table_says(project: Path) -> None:
    enable(project, '[workbench]\nenabled = true\nform = "workbench/quote.json"\n')
    write_form(project, form(POLICY), at="workbench/quote.json")

    assert client().get("/api/workbench").json()["form"] == "workbench/quote.json"
    assert client().get("/api/workbench/tables").json()["tables"][0]["label"] == "policy_details"


def test_a_missing_form_is_the_blank_form_with_no_tables(project: Path) -> None:
    enable(project)

    tables = client().get("/api/workbench/tables")
    served = client().get("/api/workbench/form")

    assert tables.json() == {"tables": [], "sample": {}, "response_tables": []}
    assert served.status_code == 200, served.text
    # Never saved, so there is no revision to quote: the first save creates the file.
    assert served.json() == {
        "form": json.loads(render_form(blank_form(project.name))),
        "revision": None,
    }


def test_the_form_is_served_with_its_revision_and_saved_against_it(project: Path) -> None:
    enable(project)
    write_form(project, form(POLICY, PRICING))

    served = client().get("/api/workbench/form")
    assert served.status_code == 200, served.text
    body = served.json()
    # Every field is served, defaults included, as the file is written.
    assert body["form"]["schema"]["tables"][0]["columns"][1] == {
        "id": "c_exposure",
        "name": "exposure",
        "type": "float",
        "label": "",
        "key": False,
        "required": False,
        "min": None,
        "max": None,
        "options": [],
        "index": False,
    }
    revision = body["revision"]
    assert isinstance(revision, str) and len(revision) == 16

    policy, pricing = body["form"]["schema"]["tables"]
    renamed = {**body["form"], "schema": {"tables": [{**policy, "name": "policy"}, pricing]}}
    saved = client().put("/api/workbench/form", json={"form": renamed, "base_revision": revision})

    assert saved.status_code == 200, saved.text
    assert saved.json()["form"] == renamed
    assert saved.json()["revision"] != revision
    # The file holds the form's canonical text, and the next read answers the new revision.
    text = (project / "forms" / "form.json").read_text(encoding="utf-8")
    assert text == render_form(FormSpec.model_validate(renamed))
    assert client().get("/api/workbench/form").json() == {
        "form": renamed,
        "revision": saved.json()["revision"],
    }
    assert [t["label"] for t in client().get("/api/workbench/tables").json()["tables"]] == [
        "policy"
    ]


def test_a_save_against_a_form_that_changed_on_disk_is_refused_writing_nothing(
    project: Path,
) -> None:
    enable(project)
    write_form(project, form(POLICY))
    served = client().get("/api/workbench/form").json()

    # Edited after the workbench read it: by hand, or by a branch switch.
    write_form(project, form({**POLICY, "name": "policy"}))
    stale = client().put(
        "/api/workbench/form", json={"form": served["form"], "base_revision": served["revision"]}
    )

    assert stale.status_code == 409
    assert stale.json()["detail"] == (
        "stale_document_revision: The workbench's form changed on disk after the workbench "
        "read it. Reload the workbench before saving."
    )
    on_disk = json.loads((project / "forms" / "form.json").read_text(encoding="utf-8"))
    assert on_disk["schema"]["tables"][0]["name"] == "policy"


def test_the_first_save_creates_the_form_unless_one_has_appeared(project: Path) -> None:
    enable(project)
    blank = client().get("/api/workbench/form").json()

    created = client().put(
        "/api/workbench/form", json={"form": blank["form"], "base_revision": None}
    )
    assert created.status_code == 200, created.text
    assert (project / "forms" / "form.json").is_file()
    assert client().get("/api/workbench/form").json()["revision"] == created.json()["revision"]

    # Another first save would overwrite the form the file now holds.
    again = client().put("/api/workbench/form", json={"form": blank["form"], "base_revision": None})
    assert again.status_code == 409
    assert again.json()["detail"].startswith("stale_document_revision")


def test_a_save_is_one_commit_on_the_ledger_which_commit_sweeps_from_then_on(
    project: Path,
) -> None:
    enable(project)
    init_repo(project)
    blank = client().get("/api/workbench/form").json()

    # The first save creates the file: a new file, captured all the same.
    created = client().put(
        "/api/workbench/form", json={"form": blank["form"], "base_revision": None}
    )

    assert created.status_code == 200, created.text
    body = created.json()
    assert body["warnings"] == []
    assert body["identity_required"] is False
    assert body["git_sha"] == git(project, "rev-parse", LEDGER)
    assert git(project, "show", "--name-only", "--format=", body["git_sha"]).splitlines() == [
        "forms/form.json"
    ]

    # The same form saved again changes nothing on disk, so there is no commit to make.
    again = client().put(
        "/api/workbench/form", json={"form": blank["form"], "base_revision": body["revision"]}
    )
    assert again.status_code == 200, again.text
    assert again.json()["git_sha"] is None
    assert git(project, "rev-parse", LEDGER) == body["git_sha"]

    # A milestone folds the save into the working branch. The file is tracked from then on,
    # so an edit made to it by hand is swept into the next milestone.
    commit_milestone("Form laid out", project, cwd=project)
    assert git(project, "diff", "--name-only", "main", WORKING).splitlines() == ["forms/form.json"]
    first = git(project, "rev-parse", WORKING)
    write_form(project, form(POLICY))
    commit_milestone("Policy table", project, cwd=project)
    assert git(project, "diff", "--name-only", first, WORKING).splitlines() == ["forms/form.json"]


def test_a_save_without_a_working_branch_is_written_and_captured_nowhere(project: Path) -> None:
    enable(project)
    git(project, "init", "-b", "main")

    saved = client().put("/api/workbench/form", json={"form": form(POLICY), "base_revision": None})

    assert saved.status_code == 200, saved.text
    assert capture(saved.json()) == {"git_sha": None, "warnings": [], "identity_required": False}
    assert (project / "forms" / "form.json").is_file()
    assert git(project, "status", "--porcelain", "--", "forms/form.json").startswith("??")


def test_a_save_the_ledger_cannot_capture_is_written_and_says_why(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    enable(project)
    init_repo(project)
    tip = git(project, "rev-parse", LEDGER)

    # A working branch the guardrails refuse: the save lands, the capture fails with its
    # reason, as any failure of the capture does.
    write_working_branch(project, "broken-save")
    failed = client().put("/api/workbench/form", json={"form": form(POLICY), "base_revision": None})

    assert failed.status_code == 200, failed.text
    assert failed.json()["git_sha"] is None
    assert failed.json()["identity_required"] is False
    assert len(failed.json()["warnings"]) == 1
    assert failed.json()["warnings"][0].startswith("Changes saved; version capture failed: ")
    assert (project / "forms" / "form.json").is_file()

    # No commit identity (a restored hosted container): the save lands, and the response
    # says the capture waits on one, so the editor can ask.
    write_working_branch(project, WORKING)
    monkeypatch.setattr(_git, "get_identity", lambda cwd=None: (None, None))
    uncaptured = client().put(
        "/api/workbench/form",
        json={"form": form(POLICY, PRICING), "base_revision": failed.json()["revision"]},
    )

    assert uncaptured.status_code == 200, uncaptured.text
    assert capture(uncaptured.json()) == {
        "git_sha": None,
        "warnings": [
            "Changes saved, but version capture needs a git identity. "
            "Set your name and email to keep version history."
        ],
        "identity_required": True,
    }
    assert git(project, "rev-parse", LEDGER) == tip
    assert client().get("/api/workbench/form").json()["revision"] == uncaptured.json()["revision"]


def test_a_body_that_is_not_a_form_is_refused_and_nothing_is_written(project: Path) -> None:
    enable(project)

    refused = client().put(
        "/api/workbench/form",
        json={"form": {**form(), "pages": [], "colour": "red"}, "base_revision": None},
    )

    assert refused.status_code == 422
    assert not (project / "forms").exists()


def test_the_form_routes_answer_404_while_the_workbench_is_not_enabled(project: Path) -> None:
    enable(project, "[workbench]\nenabled = false\n")
    message = "The workbench is not enabled: set [workbench] enabled = true in haute.toml."

    served = client().get("/api/workbench/form")
    saved = client().put("/api/workbench/form", json={"form": form(), "base_revision": None})
    tables = client().post("/api/workbench/tables", json={"form": form()})

    assert (served.status_code, served.json()["detail"]) == (404, message)
    assert (saved.status_code, saved.json()["detail"]) == (404, message)
    assert (tables.status_code, tables.json()["detail"]) == (404, message)
    assert not (project / "forms").exists()


def test_a_form_that_is_not_utf8_answers_409_naming_the_file(project: Path) -> None:
    enable(project)
    (project / "forms").mkdir()
    (project / "forms" / "form.json").write_bytes(b'{"name": "\xff\xfe"}')

    for route in ("/api/workbench/tables", "/api/workbench/form"):
        response = client().get(route)
        assert response.status_code == 409
        assert response.json()["detail"].startswith("forms/form.json is not UTF-8 text")


def test_a_misshapen_form_and_a_bad_table_answer_409_naming_what_to_fix(project: Path) -> None:
    enable(project)
    write_form(project, {**form(POLICY), "colour": "red"})
    misshapen = client().get("/api/workbench/tables")
    assert misshapen.status_code == 409
    assert misshapen.json()["detail"].startswith("forms/form.json is not a workbench form: colour")

    enable(project, '[workbench]\nenabled = "yes"\n')
    for route in ("/api/workbench", "/api/workbench/tables"):
        bad = client().get(route)
        assert bad.status_code == 409
        assert bad.json()["detail"] == "[workbench].enabled must be true or false"


def test_a_table_the_quote_input_s_rules_refuse_answers_the_structured_422(project: Path) -> None:
    enable(project)
    write_form(project, form({**POLICY, "name": "class"}))

    response = client().get("/api/workbench/tables")

    assert response.status_code == 422
    assert "class" in response.json()["detail"]


def test_the_tables_of_an_unsaved_form_are_served_without_writing_it(project: Path) -> None:
    enable(project)
    write_form(project, form(POLICY))
    before = (project / "forms" / "form.json").read_bytes()
    unsaved = form({**POLICY, "name": "policy"}, PRICING, sample={"t1": [{"c_state": "CA"}]})

    posted = client().post("/api/workbench/tables", json={"form": unsaved})

    assert posted.status_code == 200, posted.text
    body = posted.json()
    assert [t["label"] for t in body["tables"]] == ["policy"]
    assert body["sample"] == {"policy": {"state": "CA"}}
    assert [t["label"] for t in body["response_tables"]] == ["pricing_output"]
    # The file is as it was, and the saved form is what the GET still serves.
    assert (project / "forms" / "form.json").read_bytes() == before
    assert [t["label"] for t in client().get("/api/workbench/tables").json()["tables"]] == [
        "policy_details"
    ]


def test_an_unsaved_form_the_quote_input_s_rules_refuse_answers_the_structured_422(
    project: Path,
) -> None:
    enable(project)

    refused = client().post(
        "/api/workbench/tables", json={"form": form({**POLICY, "name": "class"})}
    )
    misshapen = client().post("/api/workbench/tables", json={"form": {**form(), "pages": []}})

    assert refused.status_code == 422
    assert "class" in refused.json()["detail"]
    assert misshapen.status_code == 422
    assert not (project / "forms").exists()


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
