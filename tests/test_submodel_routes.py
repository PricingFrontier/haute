"""Tests for haute.routes.submodel — create, get, dissolve endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

from haute.graph_utils import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every test in a temporary directory."""
    monkeypatch.chdir(tmp_path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _simple_graph() -> dict:
    """A minimal graph dict with two nodes and an edge."""
    return {
        "nodes": [
            {
                "id": "load",
                "data": {"label": "load", "nodeType": "dataSource", "config": {"path": "data.csv"}},
            },
            {
                "id": "calc",
                "data": {"label": "calc", "nodeType": "polars", "config": {"code": "return df"}},
            },
        ],
        "edges": [{"id": "e1", "source": "load", "target": "calc"}],
    }


def _graph_with_submodel() -> dict:
    """A graph dict that contains a submodel."""
    return {
        "nodes": [
            {
                "id": "load",
                "data": {"label": "load", "nodeType": "dataSource", "config": {"path": "d.csv"}},
            },
            {
                "id": "submodel__pricing",
                "type": "submodel",
                "data": {
                    "label": "pricing",
                    "nodeType": "submodel",
                    "config": {
                        "file": "modules/pricing.py",
                        "childNodeIds": ["base_rate"],
                        "inputPorts": [],
                        "outputPorts": [],
                    },
                },
            },
        ],
        "edges": [],
        "submodels": {
            "pricing": {
                "file": "modules/pricing.py",
                "childNodeIds": ["base_rate"],
                "inputPorts": [],
                "outputPorts": [],
                "graph": {
                    "nodes": [
                        {
                            "id": "base_rate",
                            "data": {
                                "label": "base_rate",
                                "nodeType": "polars",
                                "config": {"code": "return df"},
                            },
                        },
                    ],
                    "edges": [],
                    "pipeline_name": "pricing",
                },
            },
        },
    }


# ---------------------------------------------------------------------------
# POST /api/submodel/create
# ---------------------------------------------------------------------------


class TestCreateSubmodel:
    def test_invalid_node_ids(self, client: TestClient) -> None:
        """Requesting node IDs that don't exist should return 400."""
        body = {
            "name": "pricing",
            "node_ids": ["nonexistent"],
            "graph": _simple_graph(),
            "source_file": "pipeline.py",
        }
        resp = client.post("/api/submodel/create", json=body)
        assert resp.status_code == 400

    def test_too_few_nodes(self, client: TestClient) -> None:
        """A submodel must contain at least 2 nodes."""
        body = {
            "name": "pricing",
            "node_ids": ["calc"],
            "graph": _simple_graph(),
            "source_file": "",
        }
        resp = client.post("/api/submodel/create", json=body)
        assert resp.status_code == 400

    def test_successful_create(self, client: TestClient, tmp_path: Path) -> None:
        """Happy path: creates submodel file and returns updated graph."""
        mock_result = MagicMock()
        mock_result.sm_file = "modules/pricing.py"
        mock_result.graph = PipelineGraph(pipeline_name="main")

        with patch("haute.routes._submodel_ops.create_submodel_graph", return_value=mock_result):
            with patch("haute.codegen.graph_to_code_multi", return_value={}):
                body = {
                    "name": "pricing",
                    "node_ids": ["calc"],
                    "graph": _simple_graph(),
                    "source_file": "pipeline.py",
                    "pipeline_name": "main",
                }
                resp = client.post("/api/submodel/create", json=body)

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["submodel_file"] == "modules/pricing.py"


# ---------------------------------------------------------------------------
# GET /api/submodel/{name}
# ---------------------------------------------------------------------------


class TestGetSubmodel:
    def test_submodel_not_found(self, client: TestClient) -> None:
        resp = client.get("/api/submodel/nonexistent")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"]

    def test_successful_get(self, client: TestClient, tmp_path: Path) -> None:
        """Create a submodel file and fetch it."""
        modules_dir = tmp_path / "modules"
        modules_dir.mkdir()
        sm_file = modules_dir / "pricing.py"
        sm_file.write_text("""\
import polars as pl
import haute

submodel = haute.Submodel("pricing", description="Test submodel")

@submodel.polars
def base_rate(df: pl.LazyFrame) -> pl.LazyFrame:
    return df
""")
        resp = client.get("/api/submodel/pricing")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["submodel_name"] == "pricing"
        assert len(data["graph"]["nodes"]) >= 1

    def test_name_with_dots_returns_404(self, client: TestClient, tmp_path: Path) -> None:
        """A name like '..something' still resolves to modules/ and 404s if not found."""
        resp = client.get("/api/submodel/..something")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/submodel/dissolve
# ---------------------------------------------------------------------------


class TestDissolveSubmodel:
    def test_submodel_not_in_graph(self, client: TestClient) -> None:
        body = {
            "submodel_name": "nonexistent",
            "graph": _simple_graph(),
            "source_file": "pipeline.py",
        }
        resp = client.post("/api/submodel/dissolve", json=body)
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"]

    def test_missing_source_file(self, client: TestClient) -> None:
        body = {
            "submodel_name": "pricing",
            "graph": _graph_with_submodel(),
            "source_file": "",
        }
        resp = client.post("/api/submodel/dissolve", json=body)
        assert resp.status_code == 400
        assert "source_file" in resp.json()["detail"]

    def test_successful_dissolve(self, client: TestClient, tmp_path: Path) -> None:
        """Happy path: dissolves submodel, writes code, deletes file."""
        # Create the submodel file so it can be deleted
        modules_dir = tmp_path / "modules"
        modules_dir.mkdir()
        sm_file = modules_dir / "pricing.py"
        sm_file.write_text("# submodel code\n")

        # Create the main pipeline file path
        pipeline_file = tmp_path / "pipeline.py"
        pipeline_file.write_text("# main pipeline\n")

        flat_graph = PipelineGraph(pipeline_name="main")

        with patch("haute._flatten.flatten_graph", return_value=flat_graph):
            with patch("haute.codegen.graph_to_code", return_value="# code\n"):
                body = {
                    "submodel_name": "pricing",
                    "graph": _graph_with_submodel(),
                    "source_file": "pipeline.py",
                    "pipeline_name": "main",
                }
                resp = client.post("/api/submodel/dissolve", json=body)

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_dissolve_deletes_submodel_file(self, client: TestClient, tmp_path: Path) -> None:
        """After dissolve, the submodel .py file should be deleted."""
        modules_dir = tmp_path / "modules"
        modules_dir.mkdir()
        sm_file = modules_dir / "pricing.py"
        sm_file.write_text("# code\n")

        pipeline_file = tmp_path / "pipeline.py"
        pipeline_file.write_text("# main\n")

        flat_graph = PipelineGraph(pipeline_name="main")

        with patch("haute._flatten.flatten_graph", return_value=flat_graph):
            with patch("haute.codegen.graph_to_code", return_value="# code\n"):
                body = {
                    "submodel_name": "pricing",
                    "graph": _graph_with_submodel(),
                    "source_file": "pipeline.py",
                    "pipeline_name": "main",
                }
                client.post("/api/submodel/dissolve", json=body)

        assert not sm_file.exists()
