"""The underwriter app (specs/workbench): a release written beside a deploy's bundle, and the
app serving it, signed in by its host and pricing through the release's pipeline."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import haute._workbench_app as workbench_app
from haute._execution_admission import ExecutionAdmissionError
from haute._sandbox import _get_project_root, set_project_root
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute._workbench_app import (
    APP_ENTRY,
    EASY_AUTH_NAME,
    WorkbenchAppError,
    build_release,
    create_app,
    read_release,
    write_release,
)
from haute._workbench_form import FormSpec, form_revision, render_form
from haute.deploy._utils import shipped_graph
from haute.execution import ExecutionProfile
from tests.test_workbench_routes import POLICY, PRICING, form

FORM = form(POLICY, PRICING, sample={"t1": [{"c_state": "NY", "c_exposure": "1000"}]})
VALUES = {"t1": [{"c_state": "NY", "c_exposure": "$1,000"}]}
SIGNED_IN = {EASY_AUTH_NAME: "uw@example.com"}


def _node(nid: str, node_type: NodeType, config: dict[str, Any]) -> GraphNode:
    return GraphNode(id=nid, data=NodeData(label=nid, nodeType=node_type, config=config))


def _graph() -> PipelineGraph:
    """The policy priced: half its exposure, in the pricing table."""
    policy = {
        "name": "policy_details",
        "rows": "one",
        "columns": [{"name": "state", "type": "str"}, {"name": "exposure", "type": "float"}],
    }
    pricing = {
        "name": "pricing_output",
        "rows": "one",
        "columns": [{"name": "premium", "type": "float"}],
    }
    return PipelineGraph(
        nodes=[
            _node("request", NodeType.WORKBENCH_INPUT, {"tables": [policy], "sample": {}}),
            _node(
                "priced",
                NodeType.POLARS,
                {"code": "df = policy_details.select(premium=pl.col('exposure') / 2)"},
            ),
            _node("response", NodeType.WORKBENCH_OUTPUT, {"tables": [pricing]}),
        ],
        edges=[
            GraphEdge(id="e1", source="request", target="priced", sourceHandle="policy_details"),
            GraphEdge(id="e2", source="priced", target="response", targetHandle="pricing_output"),
        ],
    )


@pytest.fixture()
def release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """A release directory holding a bundle as a deploy writes it; call it with a host. The
    app sets the project root to the release; the root is put back after."""
    original = _get_project_root()
    monkeypatch.chdir(tmp_path)

    def build(host: str | None = "azure-container-apps", spec: dict[str, Any] = FORM) -> Path:
        directory = tmp_path / "release"
        directory.mkdir(exist_ok=True)
        manifest = {
            "haute_version": "9.9.9",
            "pipeline_name": "motor",
            "created_at": "2026-10-12T09:00:00+00:00",
            "created_by": "analyst",
            "input_node_ids": ["request"],
            "output_node_id": "response",
            "output_fields": None,
            "artifacts": {},
            "pruned_graph": shipped_graph(_graph()),
        }
        (directory / "deploy_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        write_release(directory, FormSpec.model_validate(spec), host)
        return directory

    yield build
    set_project_root(original)


def _client(directory: Path, **options: Any) -> TestClient:
    return TestClient(create_app(directory, **options))


def _price(client: TestClient, values: dict[str, Any] = VALUES, revision: str = "") -> Any:
    revision = revision or client.get("/api/app/form", headers=SIGNED_IN).json()["revision"]
    return client.post(
        "/api/app/price",
        json={"form_revision": revision, "values": values},
        headers=SIGNED_IN,
    )


# ── The release ───────────────────────────────────────────────────────────


def test_a_release_holds_the_form_without_its_sample_what_it_is_and_its_entry(release: Any) -> None:
    directory = release()

    shipped, spec, manifest = read_release(directory)

    assert spec.sample == {}
    assert spec.data_schema.tables[0].name == "policy_details"
    assert shipped.form_revision == form_revision((directory / "form.json").read_bytes())
    assert (shipped.pipeline_name, shipped.haute_version) == ("motor", "9.9.9")
    assert shipped.created_at == "2026-10-12T09:00:00+00:00"
    assert shipped.host == "azure-container-apps"
    assert manifest["output_node_id"] == "response"
    assert (directory / "app.py").read_text(encoding="utf-8") == APP_ENTRY


def test_the_release_id_is_taken_from_the_form_and_the_bundle(release: Any) -> None:
    first = read_release(release())[0].id

    def rebuilt(**changes: Any) -> str:
        manifest_path = Path("release/deploy_manifest.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.update(changes)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return write_release(Path("release"), FormSpec.model_validate(FORM), None).id

    # Built again later, by someone else, from another checkout and for another target, the
    # same project is the same release.
    assert (
        rebuilt(
            created_at="2026-10-13T09:00:00+00:00",
            created_by="other",
            pipeline_file="C:/elsewhere/main.py",
            target="container",
        )
        == first
    )

    # The sample is not shipped, so a different sample is the same release too.
    assert read_release(release(spec={**FORM, "sample": {}}))[0].id == first

    renamed = {**FORM, "name": "renamed"}
    assert read_release(release(spec=renamed))[0].id != first

    # A model retrained under the same name is another release.
    release()
    model = Path("release/artifacts/model.cbm")
    model.parent.mkdir()
    model.write_bytes(b"trained")
    trained = rebuilt(artifacts={"model": "artifacts/model.cbm"})
    model.write_bytes(b"retrained")
    assert rebuilt() not in (first, trained)


def test_the_app_reads_what_the_pipeline_ships_from_the_release(release: Any) -> None:
    directory = release()

    create_app(directory)

    assert _get_project_root() == directory.resolve()


# ── Sign-in ───────────────────────────────────────────────────────────────


def test_the_health_check_answers_without_sign_in(release: Any) -> None:
    directory = release(host=None)

    answered = _client(directory).get("/health")

    assert answered.status_code == 200
    assert answered.json() == {"status": "ok", "release": read_release(directory)[0].id}


def test_azure_signs_in_the_principal_its_authentication_sets(release: Any) -> None:
    client = _client(release())

    refused = client.get("/api/app/release")
    assert refused.status_code == 401
    assert "Azure's built-in authentication" in refused.json()["detail"]

    answered = client.get("/api/app/release", headers=SIGNED_IN)
    assert answered.status_code == 200, answered.text
    assert answered.json()["principal"] == "uw@example.com"
    assert answered.json()["pipeline_name"] == "motor"

    # The principal's id stands in when the host sets no name.
    by_id = client.get("/api/app/form", headers={"x-ms-client-principal-id": "0b1d"})
    assert by_id.status_code == 200


def test_a_release_with_no_host_refuses_everything_but_the_health_check(release: Any) -> None:
    client = _client(release(host=None))

    for path in ("/api/app/release", "/api/app/form", "/"):
        refused = client.get(path, headers=SIGNED_IN)
        assert refused.status_code == 401, path
        assert "[workbench.app] host" in refused.json()["detail"]


def test_serving_locally_signs_every_request_in_as_the_analyst(release: Any) -> None:
    client = _client(release(host=None), local_principal="analyst@example.com")

    answered = client.get("/api/app/release")

    assert answered.status_code == 200
    assert answered.json()["principal"] == "analyst@example.com"


# ── The form and the pricing ──────────────────────────────────────────────


def test_the_form_is_served_without_its_sample(release: Any) -> None:
    directory = release()

    answered = _client(directory).get("/api/app/form", headers=SIGNED_IN).json()

    assert answered["revision"] == read_release(directory)[0].form_revision
    assert answered["form"]["sample"] == {}
    assert [table["name"] for table in answered["form"]["schema"]["tables"]] == [
        "policy_details",
        "pricing_output",
    ]


def test_a_quote_is_priced_through_the_releases_pipeline(release: Any) -> None:
    priced = _price(_client(release()))

    assert priced.status_code == 200, priced.text
    # The number typed with a currency sign and a separator is read as Price reads it.
    assert priced.json() == {
        "tables": {"pricing_output": [{"premium": 500.0}]},
        "request": {"policy_details": {"state": "NY", "exposure": 1000.0}},
    }


def test_a_page_drawn_for_another_release_is_refused(release: Any) -> None:
    refused = _price(_client(release()), revision="0" * 64)

    assert refused.status_code == 409
    assert "reload it" in refused.json()["detail"]


def test_an_empty_quote_is_refused(release: Any) -> None:
    refused = _price(_client(release()), values={})

    assert refused.status_code == 422
    assert refused.json()["detail"] == "Type the quote first"


def test_a_pricing_that_runs_too_long_answers_that_it_timed_out(
    release: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(release())
    monkeypatch.setattr(workbench_app, "PRICE_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(workbench_app, "score_graph", lambda **_: time.sleep(0.5))

    answered = _price(client)

    assert answered.status_code == 504
    assert answered.json()["detail"] == "Pricing the quote took too long; try again."


def test_a_pricing_the_app_cannot_admit_answers_that_it_is_busy(
    release: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(**_: Any) -> None:
        raise ExecutionAdmissionError(
            "workbench_app_price",
            profile=ExecutionProfile.DEPLOY_LIVE,
            memory_limit_bytes=1,
            rss_at_admission_bytes=2,
            reason="over the limit",
        )

    monkeypatch.setattr(workbench_app, "admit_deploy_execution", refuse)

    answered = _price(_client(release()))

    assert answered.status_code == 503
    assert "busy pricing other quotes" in answered.json()["detail"]


# ── The page ──────────────────────────────────────────────────────────────


def test_the_page_is_the_apps_own_built_page(release: Any, tmp_path: Path) -> None:
    static = tmp_path / "static"
    client = _client(release(), static_dir=static)

    unbuilt = client.get("/", headers=SIGNED_IN)
    assert unbuilt.status_code == 503
    assert "not built" in unbuilt.json()["detail"]

    (static / "assets").mkdir(parents=True)
    (static / "app.html").write_text("<div id=app></div>", encoding="utf-8")
    (static / "assets" / "app.js").write_text("export {}", encoding="utf-8")
    assert client.get("/", headers=SIGNED_IN).text == "<div id=app></div>"
    assert client.get("/assets/app.js", headers=SIGNED_IN).text == "export {}"
    assert client.get("/assets/app.js").status_code == 401


# ── Building a project's release ──────────────────────────────────────────


def _form_table(
    tid: str, name: str, role: str, *columns: tuple[str, str], many: bool = False
) -> dict[str, Any]:
    return {
        "id": tid,
        "name": name,
        "role": role,
        "rows": "many" if many else "one",
        "columns": [
            {"id": f"c_{column}", "name": column, "type": kind} for column, kind in columns
        ],
    }


#: The sheets of the pipeline `tests/test_workbench_output.py` prices.
PROJECT_TABLES = [
    _form_table("t_policy", "policy", "input", ("state", "str"), ("limit", "int")),
    _form_table("t_items", "items", "input", ("item_id", "str"), ("value", "float"), many=True),
    _form_table(
        "t_pricing",
        "pricing",
        "output",
        ("premium", "float"),
        ("limit", "float"),
        ("referral", "str"),
    ),
    _form_table(
        "t_item_prices",
        "item_prices",
        "output",
        ("item_id", "str"),
        ("premium", "float"),
        many=True,
    ),
]
PROJECT_VALUES = {
    "t_policy": [{"c_state": "NY", "c_limit": "1,000"}],
    "t_items": [{"c_item_id": "A", "c_value": "10"}, {"c_item_id": "B", "c_value": "30"}],
}


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """A workbench project whose sheets price through a generated pipeline, its working
    directory; the project root is put back after."""
    from tests.test_workbench_output import _priced, _write

    original = _get_project_root()
    monkeypatch.chdir(tmp_path)
    set_project_root(tmp_path)
    (tmp_path / "haute.toml").write_text(
        '[project]\nname = "motor"\npipeline = "main.py"\n\n[workbench]\nenabled = true\n\n'
        '[workbench.app]\nhost = "azure-container-apps"\n',
        encoding="utf-8",
    )
    _write(_priced(), tmp_path, "main")
    (tmp_path / "forms").mkdir()
    write_sheets(tmp_path, PROJECT_TABLES)
    yield tmp_path
    set_project_root(original)


def write_sheets(project: Path, tables: list[dict[str, Any]]) -> None:
    spec = FormSpec.model_validate({**form(), "name": "motor", "schema": {"tables": tables}})
    (project / "forms" / "form.json").write_bytes(render_form(spec).encode("utf-8"))


def test_a_project_builds_a_release_that_prices_as_its_pipeline(project: Path) -> None:
    built = build_release(project, project / "build" / "app")

    assert built.host == "azure-container-apps"
    assert built.pipeline_name == "main"
    client = _client(project / "build" / "app", local_principal="analyst@example.com")
    priced = _price(client, PROJECT_VALUES)
    assert priced.status_code == 200, priced.text
    assert priced.json()["tables"] == {
        "pricing": [{"premium": 100.0, "limit": 1000.0, "referral": "none"}],
        "item_prices": [{"item_id": "A", "premium": 20.0}, {"item_id": "B", "premium": 60.0}],
    }

    # Built again, into the same directory or another, it is the same release.
    assert build_release(project, project / "build" / "app").id == built.id
    assert build_release(project, project / "elsewhere").id == built.id


def test_a_release_is_refused_for_what_the_project_cannot_ship(project: Path) -> None:
    def refused() -> str:
        with pytest.raises(WorkbenchAppError) as caught:
            build_release(project, project / "build" / "app")
        return str(caught.value)

    other = project / "build" / "app"
    other.mkdir(parents=True)
    (other / "notes.txt").write_text("mine", encoding="utf-8")
    assert "not a release" in refused()
    assert (other / "notes.txt").is_file()
    (other / "notes.txt").unlink()

    # A form disagreeing with the pipeline's Workbench nodes does not validate.
    pricing = _form_table("t_pricing", "pricing", "output", ("premium", "float"), ("tax", "float"))
    write_sheets(project, [*PROJECT_TABLES[:2], pricing, PROJECT_TABLES[3]])
    assert "The pipeline did not validate" in refused()
    assert "tax" in refused()

    (project / "forms" / "form.json").unlink()
    assert "has never been saved" in refused()

    (project / "haute.toml").write_text("[workbench]\nenabled = false\n", encoding="utf-8")
    assert "has no workbench" in refused()


def test_haute_app_build_writes_the_release(project: Path) -> None:
    from click.testing import CliRunner

    from haute.cli import cli

    result = CliRunner().invoke(cli, ["app", "build", "out"])

    assert result.exit_code == 0, result.output
    assert f"Release {read_release(project / 'out')[0].id} of main" in result.output
    assert (project / "out" / "app.py").is_file()


def test_haute_app_serve_serves_the_release_on_loopback_as_the_analyst(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import uvicorn
    from click.testing import CliRunner

    import haute._git_setup as git_setup
    from haute.cli import cli

    served: dict[str, Any] = {}
    monkeypatch.setattr(
        uvicorn, "run", lambda application, **options: served.update(app=application, **options)
    )
    monkeypatch.setattr(git_setup, "get_identity", lambda cwd=None: ("Ana", "ana@example.com"))

    result = CliRunner().invoke(cli, ["app", "serve", "--port", "8123"])

    assert result.exit_code == 0, result.output
    assert "Signed in as ana@example.com" in result.output
    assert (served["host"], served["port"]) == ("127.0.0.1", 8123)
    assert (project / ".haute" / "app" / "release" / "release.json").is_file()
    signed_in = TestClient(served["app"]).get("/api/app/release")
    assert signed_in.json()["principal"] == "ana@example.com"

    monkeypatch.setattr(git_setup, "get_identity", lambda cwd=None: (None, None))
    unnamed = CliRunner().invoke(cli, ["app", "serve"])
    assert unnamed.exit_code != 0
    assert "Set your git identity first" in unnamed.output
