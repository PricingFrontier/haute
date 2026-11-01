"""Two real underwriter apps, production's and staging's, serving releases of different forms on
SQLite stores, reached as ``haute app impact`` reaches hosted apps: the host signs each request
in, as Azure's built-in authentication does, and the transport answers as ``fetch`` does."""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import Mapping
from pathlib import Path
from types import TracebackType
from typing import Any

import pytest
from fastapi.testclient import TestClient

from haute._quote_store import StoreLocation, store_url
from haute._sandbox import _get_project_root, set_project_root
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute._workbench_app import EASY_AUTH_NAME, create_app, read_release, write_release
from haute._workbench_config import StoreEnvironment
from haute._workbench_form import FormSpec, with_quote_details
from haute._workbench_rollout import AppState, HttpAnswer, _recorded
from haute._workbench_tables import input_tables, output_tables
from haute.deploy._utils import shipped_graph
from tests._store_servers import Target

#: The hosted environment each release declares, whose store its app opens.
TEST = "test"
PROD = "motor-prod.example.azurecontainerapps.io"
STAGING = "motor-staging.example.azurecontainerapps.io"
#: Who the host signs the CI service principal's requests in as.
SIGNED_IN = {EASY_AUTH_NAME: "ci@example.com"}
DETAILS = {"qd_quote_name": "Fleet", "qd_insured": "Acme", "qd_inception_date": "2026-11-01"}


def _column(name: str, type_: str = "str") -> dict[str, Any]:
    return {"id": f"c_{name}", "name": name, "type": type_}


def _form(policy: list[dict[str, Any]], pricing: list[dict[str, Any]]) -> dict[str, Any]:
    spec = {
        "version": 1,
        "name": "motor",
        "schema": {
            "tables": [
                {"id": "t1", "name": "policy_details", "role": "input", "columns": policy},
                {"id": "t2", "name": "pricing_output", "role": "output", "columns": pricing},
            ]
        },
        "pages": [
            {
                "id": "p",
                "title": "Sheet 1",
                "width": 1280,
                "sections": [{"id": "s", "title": "s", "height": 120, "widgets": []}],
            }
        ],
        "sample": {},
    }
    return with_quote_details(FormSpec.model_validate(spec)).model_dump(mode="json", by_alias=True)


#: Production keeps a note and takes the vehicles as text, pricing half the exposure.
PROD_FORM = _form(
    [_column("state"), _column("exposure", "float"), _column("vehicles"), _column("note")],
    [_column("premium", "float")],
)
PROD_CODE = "df = policy_details.select(premium=pl.col('exposure') / 2)"
#: Staging drops the note, adds an age, takes the vehicles as a number and prices a quarter
#: of the exposure, with a fee beside the premium.
STAGING_FORM = _form(
    [
        _column("state"),
        _column("exposure", "float"),
        _column("vehicles", "int"),
        _column("age", "int"),
    ],
    [_column("premium", "float"), _column("fee", "float")],
)
STAGING_CODE = "df = policy_details.select(premium=pl.col('exposure') / 4, fee=pl.lit(25.0))"


def _node(nid: str, node_type: NodeType, config: dict[str, Any]) -> GraphNode:
    return GraphNode(id=nid, data=NodeData(label=nid, nodeType=node_type, config=config))


def _graph(spec: dict[str, Any], code: str) -> PipelineGraph:
    form = FormSpec.model_validate(spec)
    return PipelineGraph(
        nodes=[
            _node(
                "request",
                NodeType.WORKBENCH_INPUT,
                {"tables": [t.model_dump(mode="json") for t in input_tables(form)], "sample": {}},
            ),
            _node("priced", NodeType.POLARS, {"code": code}),
            _node(
                "response",
                NodeType.WORKBENCH_OUTPUT,
                {"tables": [t.model_dump(mode="json") for t in output_tables(form)]},
            ),
        ],
        edges=[
            GraphEdge(id="e1", source="request", target="priced", sourceHandle="policy_details"),
            GraphEdge(id="e2", source="priced", target="response", targetHandle="pricing_output"),
        ],
    )


def _release(directory: Path, spec: dict[str, Any], code: str) -> Path:
    directory.mkdir()
    manifest = {
        "haute_version": "9.9.9",
        "pipeline_name": "motor",
        "created_at": "2026-11-01T09:00:00+00:00",
        "created_by": "analyst",
        "input_node_ids": ["request"],
        "output_node_id": "response",
        "output_fields": None,
        "artifacts": {},
        "pruned_graph": shipped_graph(_graph(spec, code)),
    }
    (directory / "deploy_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    write_release(
        directory,
        FormSpec.model_validate(spec),
        "azure-container-apps",
        runtime=[],
        environments=(StoreEnvironment(TEST, "sqlite"),),
    )
    return directory


def _client(directory: Path, store: Path) -> TestClient:
    target = Target("sqlite", store_url(store))
    target.make(read_release(directory)[1])
    return TestClient(create_app(directory, store=StoreLocation(TEST, target.url)))


class Host:
    """A Container App as the impact reads it, its release recorded."""

    def __init__(self, address: str) -> None:
        self.address = address

    def state(self) -> AppState:
        tags = {"haute-image": "img@sha256:" + "0" * 64, "haute-release": "r", "haute-commit": "c"}
        return AppState(
            single_revision=True,
            images=(),
            address=self.address,
            latest_revision=None,
            active_revisions=(),
            client_id=self.address,
            tag_names=frozenset(tags),
            recorded=_recorded(tags),
        )

    def token(self, client_id: str) -> str:
        return f"token-for-{client_id}"


class TwoApps:
    """Production's app and staging's, entered as a context; ``final_quote`` saves a final
    quote on production through its routes."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._tmp = tmp_path
        self._monkeypatch = monkeypatch
        self._root = _get_project_root()
        self.prod_host = Host(PROD)
        self.staging_host = Host(STAGING)

    def __enter__(self) -> TwoApps:
        self._monkeypatch.chdir(self._tmp)
        prod = _release(self._tmp / "prod", PROD_FORM, PROD_CODE)
        staging = _release(self._tmp / "staging", STAGING_FORM, STAGING_CODE)
        self.prod = _client(prod, self._tmp / "prod.sqlite").__enter__()
        self.staging = _client(staging, self._tmp / "staging.sqlite").__enter__()
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        self.staging.__exit__(kind, error, trace)
        self.prod.__exit__(kind, error, trace)
        set_project_root(self._root)

    def final_quote(self, reference: str, *, vehicles: str, note: str) -> None:
        """A quote saved priced on production and marked final, as an underwriter makes one."""
        release = self.prod.get("/api/app/release", headers=SIGNED_IN).json()
        values = {
            "t1": [{"c_state": "NY", "c_exposure": "1000", "c_vehicles": vehicles, "c_note": note}],
            "quote_details": [DETAILS],
        }
        drawn = {"form_revision": release["form_revision"], "release": release["id"]}
        made = self.prod.post(
            "/api/app/quotes",
            json={"operation": f"make-{reference}", "reference": reference, "values": values}
            | drawn,
            headers=SIGNED_IN,
        )
        assert made.status_code == 200, made.text
        quote = made.json()["quote"]
        address = f"/api/app/quotes/{urllib.parse.quote(reference, safe='')}/{quote['option']}"
        saved = self.prod.post(
            f"{address}/save",
            json={
                "operation": f"save-{reference}",
                "version": quote["version"],
                "values": values,
                "shown": {"pricing_output": [{"premium": 500.0}]},
            }
            | drawn,
            headers=SIGNED_IN,
        )
        assert saved.status_code == 200, saved.text
        final = self.prod.post(
            f"{address}/final",
            json={"version": saved.json()["quote"]["version"]},
            headers=SIGNED_IN,
        )
        assert final.status_code == 200, final.text

    def _client_for(self, url: str, headers: Mapping[str, str]) -> tuple[TestClient, str]:
        parts = urllib.parse.urlsplit(url)
        assert headers["Authorization"] == f"Bearer token-for-{parts.netloc}"
        client = {PROD: self.prod, STAGING: self.staging}[parts.netloc]
        return client, parts.path + (f"?{parts.query}" if parts.query else "")

    @staticmethod
    def _answer(response: Any) -> HttpAnswer:
        return HttpAnswer(
            response.status_code,
            response.content,
            response.headers.get("location"),
            response.headers.get("retry-after"),
        )

    def get(self, url: str, headers: Mapping[str, str]) -> HttpAnswer:
        client, path = self._client_for(url, headers)
        return self._answer(client.get(path, headers=SIGNED_IN, follow_redirects=False))

    def send(self, url: str, headers: Mapping[str, str], body: bytes) -> HttpAnswer:
        client, path = self._client_for(url, headers)
        sent = {**SIGNED_IN, "Content-Type": headers.get("Content-Type", "application/json")}
        return self._answer(client.post(path, content=body, headers=sent))
