"""The workbench routes (specs/workbench): the status the editor reads, and the tables as
the form defines them now, read from the working directory on each request."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

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


def test_a_missing_form_answers_409_saying_how_to_create_it(project: Path) -> None:
    enable(project)

    response = client().get("/api/workbench/tables")

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "forms/form.json does not exist: create it, point [workbench].form at the form, "
        "or set [workbench] enabled = false in haute.toml."
    )


def test_a_form_that_is_not_utf8_answers_409_naming_the_file(project: Path) -> None:
    enable(project)
    (project / "forms").mkdir()
    (project / "forms" / "form.json").write_bytes(b'{"name": "\xff\xfe"}')

    response = client().get("/api/workbench/tables")

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
