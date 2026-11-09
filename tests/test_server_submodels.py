"""The server's submodel routes.

Creating a submodel rewires its cross-boundary edges through output ports, getting one merges its
sidecar positions, and dissolving one restores its nodes, including the edge cases of each.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests import _server_helpers
from tests.conftest import (
    current_source_revision,
    write_data_input_config,
)

# Fixtures shared with the other modules split from one suite.
client = _server_helpers.client
pipeline_dir = _server_helpers.pipeline_dir


# ---------------------------------------------------------------------------
# Submodel routes -- POST /api/submodel/create, GET /api/submodel/{name},
#                   POST /api/submodel/dissolve
# ---------------------------------------------------------------------------


@pytest.fixture()
def three_node_graph(pipeline_dir: Path) -> dict:
    """Parse the test pipeline and return its graph as a dict payload."""
    from haute.parser import parse_pipeline_file

    parent = pipeline_dir / "test_pipeline.py"
    graph = parse_pipeline_file(parent)
    revision = _editor_document_revision(parent, project_root=pipeline_dir)
    graph = graph.model_copy(update={"source_revision": revision})
    return graph.model_dump()


def _editor_document_revision(parent: Path, *, project_root: Path) -> str:
    """Return the recovery-aware revision an editor client receives from GET."""
    from haute._pipeline_recovery import load_pipeline_editor_document

    document = load_pipeline_editor_document(parent, project_root=project_root)
    assert document.load_status == "ready"
    assert isinstance(document.source_revision, str) and document.source_revision
    return document.source_revision


def _submodel_instance_node(graph: dict, definition_id: str) -> dict:
    """Return the sole occurrence of one canonical submodel definition."""
    matches = [
        node
        for node in graph["nodes"]
        if node["data"]["nodeType"] == "submodel"
        and node["data"]["config"].get("definition_id") == definition_id
    ]
    assert len(matches) == 1
    instance = matches[0]
    assert instance["id"] == definition_id
    assert instance["data"]["config"]["alias"] == definition_id
    return instance


class TestCreateSubmodel:
    def _create_payload(self, graph_dict: dict, node_ids: list[str]) -> dict:
        return {
            "name": "my_submodel",
            "node_ids": node_ids,
            "graph": graph_dict,
            "source_file": "test_pipeline.py",
            "pipeline_name": "test_pipeline",
            "base_revision": graph_dict["source_revision"],
        }

    def test_create_submodel_success(
        self,
        client: TestClient,
        pipeline_dir: Path,
        three_node_graph: dict,
    ):
        # Select the two nodes (source + transform) for grouping
        node_ids = [n["id"] for n in three_node_graph["nodes"]]
        assert len(node_ids) >= 2
        selected = node_ids[:2]

        payload = self._create_payload(three_node_graph, selected)
        resp = client.post("/api/submodel/create", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["submodel_file"] == "modules/my_submodel.py"
        assert data["parent_file"] == "test_pipeline.py"
        assert "nodes" in data["graph"]

        # Verify a canonical, immutable occurrence exists in the returned graph.
        returned_node_ids = {n["id"] for n in data["graph"]["nodes"]}
        instance = _submodel_instance_node(data["graph"], "my_submodel")
        assert instance["id"] in returned_node_ids
        # Original selected nodes should be gone from parent
        for nid in selected:
            assert nid not in returned_node_ids

        # Create is an in-memory edit; explicit Save owns all persistence.
        assert not (pipeline_dir / "modules" / "my_submodel.py").exists()
        assert (pipeline_dir / "test_pipeline.py").exists()

    def test_create_submodel_too_few_nodes_returns_400(
        self,
        client: TestClient,
        three_node_graph: dict,
    ):
        """A structurally invalid selection returns an actionable safe detail."""

        # Only 1 node -- must be at least 2
        node_ids = [three_node_graph["nodes"][0]["id"]]
        payload = self._create_payload(three_node_graph, node_ids)
        resp = client.post("/api/submodel/create", json=payload)
        assert resp.status_code == 400
        assert resp.json()["detail"] == "A submodel must contain at least 2 nodes."

    def test_create_submodel_missing_source_file_returns_400(
        self,
        client: TestClient,
        three_node_graph: dict,
    ):
        node_ids = [n["id"] for n in three_node_graph["nodes"][:2]]
        payload = {
            "name": "my_submodel",
            "node_ids": node_ids,
            "graph": three_node_graph,
            "source_file": "",
            "pipeline_name": "test_pipeline",
            "base_revision": three_node_graph["source_revision"],
        }
        resp = client.post("/api/submodel/create", json=payload)
        assert resp.status_code == 400
        assert "source_file" in resp.json()["detail"]


class TestGetSubmodel:
    def test_get_submodel_success(
        self,
        client: TestClient,
        pipeline_dir: Path,
        three_node_graph: dict,
    ):
        # First, create a submodel
        node_ids = [n["id"] for n in three_node_graph["nodes"][:2]]
        create_resp = client.post(
            "/api/submodel/create",
            json={
                "name": "lookup",
                "node_ids": node_ids,
                "graph": three_node_graph,
                "source_file": "test_pipeline.py",
                "pipeline_name": "test_pipeline",
                "base_revision": three_node_graph["source_revision"],
            },
        )
        assert create_resp.status_code == 200
        created = create_resp.json()

        save_resp = client.post(
            "/api/pipeline/save",
            json={
                "name": "test_pipeline",
                "description": three_node_graph.get("pipeline_description") or "",
                "graph": created["graph"],
                "source_file": "test_pipeline.py",
                "base_revision": current_source_revision(
                    pipeline_dir / "test_pipeline.py", pipeline_dir
                ),
            },
        )
        assert save_resp.status_code == 200

        # Fetch the persisted definition by its canonical identity.
        resp = client.get("/api/submodel/lookup", params={"source_file": "test_pipeline.py"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["submodel_name"]
        assert "nodes" in data["graph"]
        # The internal graph should contain the grouped nodes
        internal_ids = {n["id"] for n in data["graph"]["nodes"]}
        for nid in node_ids:
            assert nid in internal_ids

    def test_get_submodel_not_found_returns_404(self, client: TestClient):
        resp = client.get("/api/submodel/nonexistent", params={"source_file": "test_pipeline.py"})
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()


class TestDissolveSubmodel:
    def test_dissolve_submodel_success(
        self,
        client: TestClient,
        pipeline_dir: Path,
        three_node_graph: dict,
    ):
        # Create a submodel first
        node_ids = [n["id"] for n in three_node_graph["nodes"][:2]]
        create_resp = client.post(
            "/api/submodel/create",
            json={
                "name": "temp_group",
                "node_ids": node_ids,
                "graph": three_node_graph,
                "source_file": "test_pipeline.py",
                "pipeline_name": "test_pipeline",
                "base_revision": three_node_graph["source_revision"],
            },
        )
        assert create_resp.status_code == 200
        created = create_resp.json()
        updated_graph = created["graph"]
        instance_id = _submodel_instance_node(updated_graph, "temp_group")["id"]

        # Dissolve it
        resp = client.post(
            "/api/submodel/dissolve",
            json={
                "instance_id": instance_id,
                "graph": updated_graph,
                "source_file": "test_pipeline.py",
                "pipeline_name": "test_pipeline",
                "base_revision": created["source_revision"],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

        # The flattened graph should have the original nodes back
        flat_ids = {n["id"] for n in data["graph"]["nodes"]}
        assert flat_ids == {f"submodel_runtime/temp_group/{node_id}" for node_id in node_ids}

        # Dissolve is in-memory; persistence artifacts remain untouched.
        assert "submodel_file_deleted" not in data
        assert "retained_submodel_file" not in data
        assert not (pipeline_dir / "modules" / "temp_group.py").exists()

    def test_dissolve_nonexistent_submodel_returns_404(
        self,
        client: TestClient,
        three_node_graph: dict,
    ):
        resp = client.post(
            "/api/submodel/dissolve",
            json={
                "instance_id": "ghost",
                "graph": three_node_graph,
                "source_file": "test_pipeline.py",
                "pipeline_name": "test_pipeline",
                "base_revision": three_node_graph["source_revision"],
            },
        )
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Phase 3: Submodel route edge cases
# ---------------------------------------------------------------------------


class TestSubmodelOutputPorts:
    """Cross-edge detection: output_ports from outgoing edges."""

    def test_create_with_outgoing_cross_edges(
        self,
        client: TestClient,
        pipeline_dir: Path,
    ):
        """When a selected node has edges going OUT to unselected nodes,
        those should become output ports on the submodel."""
        from haute.parser import parse_pipeline_file

        parent = pipeline_dir / "test_pipeline.py"
        graph = parse_pipeline_file(parent)
        revision = _editor_document_revision(parent, project_root=pipeline_dir)
        graph_dict = graph.model_copy(update={"source_revision": revision}).model_dump()

        # Select only "source" -- it has an edge to "transform" which is outside
        # Need at least 2 nodes for submodel creation
        nodes = graph_dict["nodes"]
        assert len(nodes) >= 2

        # Select the first two nodes (source + transform)
        selected = [n["id"] for n in nodes[:2]]

        resp = client.post(
            "/api/submodel/create",
            json={
                "name": "output_test",
                "node_ids": selected,
                "graph": graph_dict,
                "source_file": "test_pipeline.py",
                "pipeline_name": "test_pipeline",
                "base_revision": revision,
            },
        )
        assert resp.status_code == 200
        data = resp.json()

        # Verify the submodel node was created
        sm_node = _submodel_instance_node(data["graph"], "output_test")
        config = sm_node["data"]["config"]
        assert config["definition_id"] == "output_test"
        definition_nodes = data["graph"]["submodels"]["output_test"]["graph"]["nodes"]
        assert {node["id"] for node in definition_nodes} == set(selected)


class TestSubmodelEdgeRewiring:
    """Edge rewiring: both incoming and outgoing cross-boundary edges
    are rewired through the submodel node."""

    def test_cross_edges_rewired(
        self,
        client: TestClient,
        pipeline_dir: Path,
    ):
        """Add a third transform node so we can test outgoing edge rewiring."""
        # Create a 3-node pipeline: source -> transform -> transform2
        source_config = write_data_input_config(pipeline_dir, "source", "data/input.parquet")
        code = f"""\
import haute
import polars as pl

pipeline = haute.Pipeline("rewire_test", description="Rewire test")

@pipeline.data_input(config="{source_config}")
def source(): ...

@pipeline.polars
def middle(source: pl.LazyFrame) -> pl.LazyFrame:
    return source

@pipeline.polars
def final(middle: pl.LazyFrame) -> pl.LazyFrame:
    return middle

pipeline.connect("source", "middle")
pipeline.connect("middle", "final")
"""
        (pipeline_dir / "rewire_test.py").write_text(code)
        from haute.parser import parse_pipeline_file

        parent = pipeline_dir / "rewire_test.py"
        graph = parse_pipeline_file(parent)
        revision = _editor_document_revision(parent, project_root=pipeline_dir)
        graph_dict = graph.model_copy(update={"source_revision": revision}).model_dump()

        # Select only "middle" and "source" (2 nodes) -- "final" stays outside
        selected = ["source", "middle"]

        resp = client.post(
            "/api/submodel/create",
            json={
                "name": "inner",
                "node_ids": selected,
                "graph": graph_dict,
                "source_file": "rewire_test.py",
                "pipeline_name": "rewire_test",
                "base_revision": revision,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        instance_id = _submodel_instance_node(data["graph"], "inner")["id"]
        assert instance_id == "inner"

        # Parent graph should have the immutable occurrence plus final.
        parent_ids = {n["id"] for n in data["graph"]["nodes"]}
        assert instance_id in parent_ids
        assert "final" in parent_ids
        assert "source" not in parent_ids
        assert "middle" not in parent_ids

        # There should be a rewired edge from the occurrence -> final.
        parent_edges = data["graph"]["edges"]
        outgoing = [e for e in parent_edges if e["source"] == instance_id]
        assert len(outgoing) >= 1
        assert any(e["target"] == "final" for e in outgoing)
        # Parent handles expose stable public ids; internal endpoints stay private.
        definition = data["graph"]["submodels"]["inner"]
        expected_handles = {f"out__{port['name']}" for port in definition["outputPorts"]}
        assert {edge.get("sourceHandle") for edge in outgoing} == expected_handles
        assert {port["source"]["nodeId"] for port in definition["outputPorts"]} == {"middle"}


# ---------------------------------------------------------------------------
# mark_self_write and is_self_write timing
# ---------------------------------------------------------------------------


class TestGetSubmodelSidecarPositions:
    """GET /api/submodel/{name} merges sidecar positions."""

    def test_sidecar_positions_applied(
        self,
        client: TestClient,
        pipeline_dir: Path,
        three_node_graph: dict,
    ):
        # Create submodel first
        node_ids = [n["id"] for n in three_node_graph["nodes"][:2]]
        create_resp = client.post(
            "/api/submodel/create",
            json={
                "name": "positioned",
                "node_ids": node_ids,
                "graph": three_node_graph,
                "source_file": "test_pipeline.py",
                "pipeline_name": "test_pipeline",
                "base_revision": three_node_graph["source_revision"],
            },
        )
        assert create_resp.status_code == 200
        created = create_resp.json()

        save_resp = client.post(
            "/api/pipeline/save",
            json={
                "name": "test_pipeline",
                "description": three_node_graph.get("pipeline_description") or "",
                "graph": created["graph"],
                "source_file": "test_pipeline.py",
                "base_revision": current_source_revision(
                    pipeline_dir / "test_pipeline.py", pipeline_dir
                ),
            },
        )
        assert save_resp.status_code == 200

        # Write a sidecar with custom positions
        sm_path = pipeline_dir / "modules" / "positioned.py"
        sidecar = sm_path.with_suffix(".haute.json")
        sidecar.write_text(
            json.dumps(
                {
                    "positions": {
                        node_ids[0]: {"x": 100, "y": 200},
                        node_ids[1]: {"x": 300, "y": 400},
                    },
                }
            )
        )

        # Fetch the submodel
        resp = client.get("/api/submodel/positioned", params={"source_file": "test_pipeline.py"})
        assert resp.status_code == 200
        data = resp.json()

        # Check positions were merged
        for node in data["graph"]["nodes"]:
            if node["id"] == node_ids[0]:
                assert node["position"]["x"] == 100
                assert node["position"]["y"] == 200
            elif node["id"] == node_ids[1]:
                assert node["position"]["x"] == 300
                assert node["position"]["y"] == 400


class TestDissolveEdgeCases:
    """Dissolve submodel edge cases."""

    def test_dissolve_missing_source_file_returns_400(
        self,
        client: TestClient,
        three_node_graph: dict,
    ):
        """Dissolve with empty source_file returns 400."""
        # First create a submodel to get a valid graph with submodels
        node_ids = [n["id"] for n in three_node_graph["nodes"][:2]]
        create_resp = client.post(
            "/api/submodel/create",
            json={
                "name": "will_dissolve",
                "node_ids": node_ids,
                "graph": three_node_graph,
                "source_file": "test_pipeline.py",
                "pipeline_name": "test_pipeline",
                "base_revision": three_node_graph["source_revision"],
            },
        )
        assert create_resp.status_code == 200
        created = create_resp.json()
        updated_graph = created["graph"]
        instance_id = _submodel_instance_node(updated_graph, "will_dissolve")["id"]

        resp = client.post(
            "/api/submodel/dissolve",
            json={
                "instance_id": instance_id,
                "graph": updated_graph,
                "source_file": "",
                "pipeline_name": "test_pipeline",
                "base_revision": created["source_revision"],
            },
        )
        assert resp.status_code == 400
        assert "source_file" in resp.json()["detail"]
