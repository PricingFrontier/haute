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


def _price(
    client: TestClient, values: dict[str, Any] = VALUES, revision: str = "", release_id: str = ""
) -> Any:
    revision = revision or client.get("/api/app/form", headers=SIGNED_IN).json()["revision"]
    release_id = release_id or client.get("/api/app/release", headers=SIGNED_IN).json()["id"]
    return client.post(
        "/api/app/price",
        json={"form_revision": revision, "release": release_id, "values": values},
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


def test_the_release_id_is_taken_from_the_form_and_the_bundle(release: Any, tmp_path: Path) -> None:
    first = read_release(release())[0].id
    directory = tmp_path / "release"

    def rebuilt(**changes: Any) -> str:
        manifest_path = tmp_path / "release" / "deploy_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.update(changes)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return write_release(directory, FormSpec.model_validate(FORM), None).id

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

    # Where the pipeline's file sat on the machine that built it prices nothing.
    graph = shipped_graph(_graph())
    here = rebuilt(pruned_graph={**graph, "source_file": "C:/checkouts/one/main.py"})
    assert rebuilt(pruned_graph={**graph, "source_file": "D:/two/main.py"}) == here == first

    # A model retrained under the same name is another release.
    release()
    model = tmp_path / "release" / "artifacts" / "model.cbm"
    model.parent.mkdir()
    model.write_bytes(b"trained")
    trained = rebuilt(artifacts={"model": "artifacts/model.cbm"})
    model.write_bytes(b"retrained")
    retrained = rebuilt()
    assert retrained not in (first, trained)

    # So is a helper of the utility code edited under the same name.
    helper = tmp_path / "release" / "utility.py"
    helper.write_text("RATE = 1\n", encoding="utf-8")
    helped = rebuilt()
    helper.write_text("RATE = 2\n", encoding="utf-8")
    assert rebuilt() not in (retrained, helped)


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
    client = _client(release())
    other_sheets = _price(client, revision="0" * 64)
    other_model = _price(client, release_id="another")

    for refused in (other_sheets, other_model):
        assert refused.status_code == 409
        assert "reload it" in refused.json()["detail"]


def test_an_empty_quote_is_refused(release: Any) -> None:
    refused = _price(_client(release()), values={})

    assert refused.status_code == 422
    assert refused.json()["detail"] == "Type the quote first"


def test_a_request_past_the_deploy_body_limit_is_refused_before_it_is_read_whole(
    release: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HAUTE_DEPLOY_QUOTE_REQUEST_BODY_LIMIT_BYTES", "200")
    client = _client(release())
    monkeypatch.setattr(workbench_app, "quote_request", _never)
    revision = client.get("/api/app/form", headers=SIGNED_IN).json()["revision"]
    oversized = {"t1": [{"c_state": "NY", "c_exposure": "1" * 400}]}

    refused = client.post(
        "/api/app/price",
        json={"form_revision": revision, "values": oversized},
        headers=SIGNED_IN,
    )

    assert refused.status_code == 413
    assert refused.json()["error_code"] == "request_body_too_large"
    malformed = client.post(
        "/api/app/price",
        content=b"{not json",
        headers={**SIGNED_IN, "content-type": "application/json"},
    )
    assert malformed.status_code == 422
    unshaped = client.post("/api/app/price", json={"values": {}}, headers=SIGNED_IN)
    assert unshaped.status_code == 422


def _never(*_: Any, **__: Any) -> Any:
    raise AssertionError("a refused request reached the pricing")


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


def test_a_project_naming_an_edit_role_builds_a_release_that_records_it(project: Path) -> None:
    toml = project / "haute.toml"
    toml.write_text(
        toml.read_text(encoding="utf-8").replace(
            'host = "azure-container-apps"\n',
            'host = "azure-container-apps"\nedit_role = "Quotes.Edit"\n',
        ),
        encoding="utf-8",
    )

    built = build_release(project, project / "build" / "app")

    assert built.edit_role == "Quotes.Edit"
    assert read_release(project / "build" / "app")[0].edit_role == "Quotes.Edit"


def test_the_same_project_checked_out_twice_builds_the_same_release(
    project: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    import shutil

    other = tmp_path_factory.mktemp("checkout")
    for name in ("haute.toml", "main.py", "forms", "config"):
        source = project / name
        if source.is_dir():
            shutil.copytree(source, other / name)
        elif source.is_file():
            shutil.copy2(source, other / name)

    here = build_release(project, project / "build" / "app")
    there = build_release(other, other / "build" / "app")

    assert there.id == here.id


_PRICE_FROM_THE_PROJECT = """
import json, sys
from pathlib import Path

from fastapi.testclient import TestClient

from haute._workbench_app import create_app

client = TestClient(create_app(Path(sys.argv[1]), local_principal="analyst@example.com"))
revision = client.get("/api/app/form").json()["revision"]
answered = client.post(
    "/api/app/price",
    json={
        "form_revision": revision,
        "release": client.get("/api/app/release").json()["id"],
        "values": json.loads(sys.argv[2]),
    },
)
print(json.dumps(answered.json()))
"""


def test_the_app_prices_on_the_utility_code_the_release_carries(project: Path) -> None:
    """Served from the project, as `haute app serve` serves it, the app imports the release's
    copy of the utility code: an edit to the project's afterwards changes no price."""
    import subprocess
    import sys

    from tests.test_workbench_output import _priced, _write

    graph = _priced()
    graph.preamble = "from utility import RATE\n"
    graph.node_map["priced"].data.config["code"] = (
        "df = policy.with_columns(premium=pl.col('limit') / 10 * RATE, referral=pl.lit('none'))"
    )
    _write(graph, project, "main")
    (project / "utility.py").write_text("RATE = 1\n", encoding="utf-8")
    release_dir = project / "build" / "app"
    build_release(project, release_dir)
    assert (release_dir / "utility.py").read_text(encoding="utf-8") == "RATE = 1\n"
    (project / "utility.py").write_text("RATE = 2\n", encoding="utf-8")
    child = project / "price_from_the_project.py"
    child.write_text(_PRICE_FROM_THE_PROJECT, encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(child), str(release_dir), json.dumps(PROJECT_VALUES)],
        cwd=project,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert completed.returncode == 0, completed.stderr[-4000:]
    priced = json.loads(completed.stdout.strip().splitlines()[-1])
    assert priced["tables"]["pricing"] == [{"premium": 100.0, "limit": 1000.0, "referral": "none"}]


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

    # Unless told otherwise, the release goes in the clone's state directory, which git
    # ignores, model files and all.
    assert CliRunner().invoke(cli, ["app", "build"]).exit_code == 0
    assert (project / ".haute" / "app" / "release" / "release.json").is_file()


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
    assert (project / ".haute" / "app" / "quotes.sqlite").is_file()
    assert "Quotes kept in .haute/app/quotes.sqlite" in result.output
    with TestClient(served["app"]) as client:
        signed_in = client.get("/api/app/release").json()
    assert (signed_in["principal"], signed_in["quotes"]) == ("ana@example.com", True)

    reading = CliRunner().invoke(cli, ["app", "serve", "--reader"])
    assert reading.exit_code == 0, reading.output
    assert "reading quotes only" in reading.output
    with TestClient(served["app"]) as client:
        assert client.get("/api/app/release").json()["can_edit"] is False

    monkeypatch.setattr(git_setup, "get_identity", lambda cwd=None: (None, None))
    unnamed = CliRunner().invoke(cli, ["app", "serve"])
    assert unnamed.exit_code != 0
    assert "Set your git identity first" in unnamed.output


# ── The release's image ───────────────────────────────────────────────────

PINNED = '\n[deploy.container]\nbase_image = "python:3.11.9-slim"\n'

_SLIM_RELEASE_CHILD = """
import importlib.abc, json, sys

blocked = set(json.loads(sys.argv[1]))


class _NotInTheImage(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in blocked:
            raise ModuleNotFoundError(f"{name} is not installed in the image", name=name)
        return None


sys.meta_path.insert(0, _NotInTheImage())
sys.path.insert(0, ".")
from fastapi.testclient import TestClient

import app

client = TestClient(app.app)
signed_in = {"x-ms-client-principal-name": "uw@example.com"}
revision = client.get("/api/app/form", headers=signed_in).json()["revision"]
release_id = client.get("/api/app/release", headers=signed_in).json()["id"]
values = json.loads(sys.argv[2])
response = client.post(
    "/api/app/price",
    json={"form_revision": revision, "release": release_id, "values": values},
    headers=signed_in,
)
saved = client.post(
    "/api/app/quotes",
    json={
        "operation": "operation-0001",
        "form_revision": revision,
        "release": release_id,
        "values": values,
        "shown": response.json()["tables"],
    },
    headers=signed_in,
)
print(
    json.dumps(
        {
            "status": response.status_code,
            "body": response.json(),
            "saved": saved.status_code,
            "quote": saved.json(),
        }
    )
)
"""


def _pin_base_image(project: Path) -> None:
    toml = project / "haute.toml"
    toml.write_text(toml.read_text(encoding="utf-8") + PINNED, encoding="utf-8")


def test_a_pinned_base_image_makes_the_release_the_images_build_context(project: Path) -> None:
    plain = build_release(project, project / "plain")
    assert not (project / "plain" / "Dockerfile").exists()
    _pin_base_image(project)

    built = build_release(project, project / "image")

    dockerfile = (project / "image" / "Dockerfile").read_text(encoding="utf-8")
    assert dockerfile.startswith("FROM python:3.11.9-slim")
    assert "COPY app.py .\nCOPY form.json .\nCOPY release.json .\n" in dockerfile
    # The release's entry point, not the scoring API's.
    assert (project / "image" / "app.py").read_text(encoding="utf-8") == APP_ENTRY
    # The image is how the release ships, not what it is.
    assert built.id == plain.id


def test_a_base_image_not_pinned_is_refused(project: Path) -> None:
    toml = project / "haute.toml"
    toml.write_text(
        toml.read_text(encoding="utf-8") + '\n[deploy.container]\nbase_image = "python:3.11"\n',
        encoding="utf-8",
    )

    with pytest.raises(WorkbenchAppError, match=r"\[deploy.container\] base_image: .*not pinned"):
        build_release(project, project / "build" / "app")


def test_haute_app_build_builds_the_image_with_docker(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from click.testing import CliRunner

    import haute.deploy._container as container
    from haute.cli import cli
    from haute.errors import DeployError

    built: list[tuple[Path, str]] = []
    monkeypatch.setattr(container, "_check_docker_available", lambda: None)
    monkeypatch.setattr(container, "_docker_build", lambda where, tag: built.append((where, tag)))

    unpinned = CliRunner().invoke(cli, ["app", "build", "out", "--image", "motor-app:1"])
    assert unpinned.exit_code != 0
    assert "has no Dockerfile: set [deploy.container] base_image" in unpinned.output
    assert built == []

    _pin_base_image(project)
    result = CliRunner().invoke(cli, ["app", "build", "out", "--image", "motor-app:1"])
    assert result.exit_code == 0, result.output
    assert "Image built: motor-app:1" in result.output
    assert built == [((project / "out").resolve(), "motor-app:1")]

    def no_docker() -> None:
        raise DeployError("Docker is not available.")

    monkeypatch.setattr(container, "_check_docker_available", no_docker)
    refused = CliRunner().invoke(cli, ["app", "build", "out", "--image", "motor-app:1"])
    assert refused.exit_code != 0
    assert "Docker is not available here" in refused.output


def test_the_release_prices_and_keeps_quotes_with_only_the_images_runtime_installed(
    project: Path,
) -> None:
    """The image installs haute --no-deps, the pinned scoring runtime and the quote store's
    packages: the release's app, given its store's URL as a host gives it, is imported and
    prices and saves a quote in a fresh interpreter that cannot import anything else haute
    depends on, as the container's scoring app is checked."""
    import os
    import subprocess
    import sys

    from haute._quote_store import migrate_store, store_url
    from tests.test_container_smoke_script import _modules_outside_the_image

    _pin_base_image(project)
    release_dir = project / "image"
    build_release(project, release_dir)
    manifest = json.loads((release_dir / "deploy_manifest.json").read_text(encoding="utf-8"))
    installed = manifest["container_dependencies"][1:]
    assert {requirement.split("==")[0] for requirement in installed} >= {"sqlalchemy", "alembic"}
    blocked = _modules_outside_the_image(installed)
    assert "mlflow" in blocked
    assert not {"sqlalchemy", "alembic"} & set(blocked)
    url = store_url(project / "quotes.sqlite")
    migrate_store(url)
    child = project / "price_one_quote.py"
    child.write_text(_SLIM_RELEASE_CHILD, encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(child), json.dumps(blocked), json.dumps(PROJECT_VALUES)],
        cwd=release_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        env={**os.environ, workbench_app.STORE_URL_ENV: url},
    )

    assert completed.returncode == 0, completed.stderr[-4000:]
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result["status"] == 200, result
    assert result["body"]["tables"]["pricing"] == [
        {"premium": 100.0, "limit": 1000.0, "referral": "none"}
    ]
    assert result["saved"] == 200, result
    assert result["quote"]["quote"]["reference"] == "Q-000001"


def test_a_release_without_a_quote_store_prices_without_its_packages(release: Any) -> None:
    """Without a store the app imports nothing of the store's: a scoring image without its
    packages serves the sheets and prices."""
    import subprocess
    import sys

    script = (
        "import importlib.abc, sys\n"
        "class Block(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name.split('.')[0] in ('sqlalchemy', 'alembic'):\n"
        "            raise ModuleNotFoundError(name)\n"
        "sys.meta_path.insert(0, Block())\n"
        "from pathlib import Path\n"
        "from haute._workbench_app import create_app\n"
        "create_app(Path(sys.argv[1]))\n"
        "print('served')\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script, str(release())],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert completed.returncode == 0, completed.stderr[-4000:]
    assert completed.stdout.strip().endswith("served")
