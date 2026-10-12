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
    create_app,
    read_release,
    write_release,
)
from haute._workbench_form import FormSpec, form_revision
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
    """A release directory holding a bundle as a deploy writes it; call it with a host."""
    original = _get_project_root()
    monkeypatch.chdir(tmp_path)
    set_project_root(tmp_path)

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

    # Built again later, by someone else, the same project is the same release.
    manifest_path = Path("release/deploy_manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(created_at="2026-10-13T09:00:00+00:00", created_by="other")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    write_release(Path("release"), FormSpec.model_validate(FORM), "azure-container-apps")
    assert read_release(Path("release"))[0].id == first

    # The sample is not shipped, so a different sample is the same release too.
    assert read_release(release(spec={**FORM, "sample": {}}))[0].id == first

    renamed = {**FORM, "name": "renamed"}
    assert read_release(release(spec=renamed))[0].id != first


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


def test_the_page_is_the_apps_own_built_page(
    release: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    static = tmp_path / "static"
    monkeypatch.setattr(workbench_app, "_STATIC", static)
    client = _client(release())

    unbuilt = client.get("/", headers=SIGNED_IN)
    assert unbuilt.status_code == 503
    assert "not built" in unbuilt.json()["detail"]

    (static / "assets").mkdir(parents=True)
    (static / "app.html").write_text("<div id=app></div>", encoding="utf-8")
    (static / "assets" / "app.js").write_text("export {}", encoding="utf-8")
    assert client.get("/", headers=SIGNED_IN).text == "<div id=app></div>"
    assert client.get("/assets/app.js", headers=SIGNED_IN).text == "export {}"
    assert client.get("/assets/app.js").status_code == 401
