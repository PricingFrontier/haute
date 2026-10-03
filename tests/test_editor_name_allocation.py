"""The editor allocates free names and refuses colliding renames (NAME-04).

The editor identity request takes the document's naming context; with
``allocate`` each new node gets the first free name, and without it a node
whose name the naming rule refuses comes back with its collision.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from haute._types import GraphEdge, GraphNode, NodeData, PipelineGraph, SubmodelDefinition
from haute.routes._save_pipeline import SavePipelineService


def _node(node_id: str, label: str | None = None, node_type: str = "polars", **config: Any):
    return GraphNode(
        id=node_id, data=NodeData(label=label or node_id, nodeType=node_type, config=config)
    )


def _occurrence(node_id: str, alias: str) -> GraphNode:
    return _node(node_id, alias, "submodel", definitionId="rates", alias=alias)


def _rates_definition() -> SubmodelDefinition:
    return SubmodelDefinition(
        definitionId="rates",
        file="modules/rates.py",
        graph=PipelineGraph(nodes=[_node("child", "rate child")], edges=[]),
        inputPorts=[],
        outputPorts=[],
    )


def _candidate(node_id: str, label: str, node_type: str = "polars", alias: str | None = None):
    request = {"node_id": node_id, "label": label, "node_type": node_type, "source_handles": []}
    if alias is not None:
        request["alias"] = alias
    return request


def _post(client: TestClient, graph: PipelineGraph, candidates: list[dict], allocate: bool):
    response = client.post(
        "/api/pipeline/editor-identities",
        json={
            "nodes": candidates,
            "graph": graph.model_dump(mode="json", by_alias=True),
            "allocate": allocate,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("haute.routes.pipeline.pipeline_dir", lambda: tmp_path)
    return tmp_path


def test_duplicating_twice_gives_copy_then_copy_2(project: Path, client: TestClient) -> None:
    graph = PipelineGraph(nodes=[_node("x", "X")], edges=[])

    first = _post(client, graph, [_candidate("x_copy", "X copy")], allocate=True)
    with_first = PipelineGraph(nodes=[_node("x", "X"), _node("x_copy", "X copy")], edges=[])
    second = _post(client, with_first, [_candidate("x_copy_2", "X copy")], allocate=True)

    assert first["identities"][0]["label"] == "X copy"
    assert second["identities"][0]["label"] == "X copy 2"
    assert second["identities"][0]["function_name"] == "X_copy_2"


def test_two_copies_in_one_paste_get_distinct_labels(project: Path, client: TestClient) -> None:
    graph = PipelineGraph(nodes=[_node("x", "X")], edges=[])

    body = _post(
        client, graph, [_candidate("p1", "X copy"), _candidate("p2", "X copy")], allocate=True
    )

    assert [identity["label"] for identity in body["identities"]] == ["X copy", "X copy 2"]
    assert body["violations"] == []


def test_a_pasted_occurrence_gets_a_free_alias(project: Path, client: TestClient) -> None:
    graph = PipelineGraph(
        nodes=[_occurrence("rates", "rates")], edges=[], submodels={"rates": _rates_definition()}
    )

    body = _post(
        client, graph, [_candidate("pasted", "rates", "submodel", alias="rates")], allocate=True
    )

    assert (body["identities"][0]["label"], body["identities"][0]["alias"]) == (
        "rates_2",
        "rates_2",
    )


def test_allocation_skips_a_name_the_rule_refuses(project: Path, client: TestClient) -> None:
    graph = PipelineGraph(nodes=[], edges=[], preamble="def band(x):\n    return x\n")

    body = _post(client, graph, [_candidate("new", "max")], allocate=True)
    helper = _post(client, graph, [_candidate("new", "band")], allocate=True)

    assert body["identities"][0]["label"] == "max 2"
    assert helper["identities"][0]["label"] == "band 2"


@pytest.mark.parametrize(
    ("label", "message"),
    [
        pytest.param("Second", "take one name, `Second`", id="another-node-ignoring-case"),
        pytest.param("pl", "takes the name `pl`", id="reserved"),
        pytest.param("band", "which the preamble (line 1) binds", id="preamble-helper"),
    ],
)
def test_a_colliding_rename_comes_back_with_its_collision(
    project: Path, client: TestClient, label: str, message: str
) -> None:
    graph = PipelineGraph(
        nodes=[_node("first"), _node("second")],
        edges=[],
        preamble="def band(x):\n    return x\n",
    )

    body = _post(client, graph, [_candidate("first", label)], allocate=False)

    assert message in body["identities"][0]["collision"]


def test_a_free_rename_has_no_collision(project: Path, client: TestClient) -> None:
    graph = PipelineGraph(nodes=[_node("first"), _node("second")], edges=[])

    body = _post(client, graph, [_candidate("first", "renamed")], allocate=False)

    assert body["identities"][0]["collision"] is None
    assert body["identities"][0]["label"] == "renamed"


def test_saving_a_utility_turns_a_rename_from_accepted_to_refused(
    project: Path, client: TestClient
) -> None:
    (project / "utility").mkdir()
    (project / "utility" / "rates.py").write_text("import polars as pl\n", encoding="utf-8")
    graph = PipelineGraph(
        nodes=[_node("first")], edges=[], preamble="from utility.rates import *\n"
    )

    before = _post(client, graph, [_candidate("first", "band")], allocate=False)
    (project / "utility" / "rates.py").write_text("def band(x):\n    return x\n", encoding="utf-8")
    after = _post(client, graph, [_candidate("first", "band")], allocate=False)

    assert before["identities"][0]["collision"] is None
    assert "which utility/rates.py (line 1) binds" in after["identities"][0]["collision"]


def test_allocate_requires_a_naming_context(client: TestClient) -> None:
    response = client.post(
        "/api/pipeline/editor-identities",
        json={"nodes": [_candidate("x", "X")], "allocate": True},
    )

    assert response.status_code == 422


def _instance_mapping_to_pl() -> PipelineGraph:
    return PipelineGraph(
        nodes=[
            _node("claims"),
            _node("rated"),
            _node("rated_again", instanceOf="rated", inputMapping={"pl": "claims"}),
        ],
        edges=[
            GraphEdge(id="e1", source="claims", target="rated"),
            GraphEdge(id="e2", source="claims", target="rated_again"),
        ],
    )


_SHARED_FIXTURE = [
    pytest.param(PipelineGraph(nodes=[_node("a"), _node("b")], edges=[]), id="clean"),
    pytest.param(
        PipelineGraph(nodes=[_node("a", "Claims"), _node("b", "claims")], edges=[]), id="case"
    ),
    pytest.param(PipelineGraph(nodes=[_node("max")], edges=[]), id="builtin"),
    pytest.param(
        PipelineGraph(nodes=[_node("band")], edges=[], preamble="def band(x):\n    return x\n"),
        id="preamble-helper",
    ),
    pytest.param(_instance_mapping_to_pl(), id="instance-input-mapping-to-pl"),
]


@pytest.mark.parametrize("graph", _SHARED_FIXTURE)
def test_the_identity_endpoint_and_save_agree(
    project: Path, client: TestClient, graph: PipelineGraph
) -> None:
    body = _post(client, graph, [], allocate=False)
    try:
        SavePipelineService(project).validate_graph(graph, source_file="main.py")
    except HTTPException as exc:
        saved: str | None = str(exc.detail)
    else:
        saved = None

    assert (saved is None) == (body["violations"] == [])
    for violation in body["violations"]:
        assert saved is not None and violation["message"] in saved
