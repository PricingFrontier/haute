"""Tests for haute.server - FastAPI API endpoint integration tests."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import polars as pl
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from watchfiles import Change

from haute._sandbox import set_project_root
from tests.conftest import (
    build_test_input_snapshot,
    make_ready_file_input_config,
    write_data_input_config,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _file_input_config(path: str) -> dict:
    return {
        "inputType": "file",
        "format": "parquet",
        "mode": "scan",
        "path": path,
        "arguments": {},
    }


def _file_output_config(path: str) -> dict:
    return {
        "outputType": "file",
        "format": "parquet",
        "mode": "sink",
        "path": path,
        "arguments": {},
    }


@pytest.fixture()
def pipeline_dir(tmp_path: Path) -> Path:
    """Create a temporary project with a root-level pipeline and sample data."""
    set_project_root(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    pl.DataFrame({"x": [1, 2, 3], "y": [10, 20, 30]}).write_parquet(data_dir / "input.parquet")

    # Use as_posix() to avoid Windows backslash escape issues in the
    # generated Python source (e.g. \U interpreted as unicode escape).
    data_path = (data_dir / "input.parquet").as_posix()
    source_config = write_data_input_config(tmp_path, "source", data_path)
    build_test_input_snapshot(_file_input_config(data_path), base_dir=tmp_path)

    code = f'''\
import polars as pl
import haute

pipeline = haute.Pipeline("test_pipeline", description="A test pipeline")


@pipeline.data_input(config="{source_config}")
def source() -> pl.LazyFrame:
    """Read data."""
    from pathlib import Path
    from haute.graph_utils import resolve_data_input_from_config
    df = resolve_data_input_from_config(
        "{source_config}", base_dir=Path(__file__).parent
    )
    return df


@pipeline.polars
def transform(source: pl.DataFrame) -> pl.DataFrame:
    """Transform."""
    return source


pipeline.connect("source", "transform")
'''
    (tmp_path / "test_pipeline.py").write_text(code)
    return tmp_path


@pytest.fixture()
def client(pipeline_dir: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient that runs with cwd set to the temp pipeline directory."""
    monkeypatch.chdir(pipeline_dir)
    # Re-import to pick up cwd change
    from haute.server import app

    return TestClient(app)


async def _run_file_watcher_and_drain() -> None:
    """Run the file watcher once and yield to scheduled broadcast tasks."""
    from haute.server import _file_watcher

    await _file_watcher()
    await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# GET /api/pipelines
# ---------------------------------------------------------------------------


class TestListPipelines:
    def test_returns_discovered_pipelines(self, client: TestClient):
        resp = client.get("/api/pipelines")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        pipe = data[0]
        assert pipe["name"] == "test_pipeline"
        assert pipe["node_count"] >= 2

    def test_includes_file_path(self, client: TestClient):
        resp = client.get("/api/pipelines")
        data = resp.json()
        assert any("test_pipeline.py" in p["file"] for p in data)


class TestSessionStatus:
    def test_session_status_returns_ok_for_valid_local_session(self, client: TestClient):
        resp = client.get("/api/session")

        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_session_status_rejects_missing_local_session_token(self, client: TestClient):
        resp = client.get(
            "/api/session",
            headers={
                "origin": "http://localhost",
                "cookie": "",
            },
        )

        assert resp.status_code == 403
        assert resp.json() == {"detail": "Missing or invalid Haute session token"}


# ---------------------------------------------------------------------------
# GET /api/pipeline
# ---------------------------------------------------------------------------


class TestGetFirstPipeline:
    def test_returns_graph(self, client: TestClient):
        resp = client.get("/api/pipeline")
        assert resp.status_code == 200
        graph = resp.json()
        assert "nodes" in graph
        assert "edges" in graph
        assert len(graph["nodes"]) >= 2
        assert graph["pipeline_name"] == "test_pipeline"

    def test_empty_project_returns_empty_graph(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.chdir(tmp_path)
        from haute.server import app

        c = TestClient(app)
        resp = c.get("/api/pipeline")
        assert resp.status_code == 200
        graph = resp.json()
        assert graph["nodes"] == []

    def test_pipeline_with_no_nodes_returns_source_file(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """A starter pipeline with no nodes should still return source_file and metadata."""
        (tmp_path / "main.py").write_text(
            'import haute\n\npipeline = haute.Pipeline("my_project", description="")\n'
        )
        monkeypatch.chdir(tmp_path)
        from haute.server import app

        c = TestClient(app)
        resp = c.get("/api/pipeline")
        assert resp.status_code == 200
        graph = resp.json()
        assert graph["nodes"] == []
        assert graph["source_file"] == "main.py"
        assert graph["pipeline_name"] == "my_project"

    def test_parse_failure_returns_422_instead_of_blank_canvas(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A broken discovered pipeline must never look like a new empty project."""
        broken = tmp_path / "broken.py"
        broken.write_text("this is not a valid Haute pipeline")
        monkeypatch.chdir(tmp_path)

        from haute.server import app

        with (
            patch(
                "haute.routes.pipeline.discover_pipelines",
                return_value=[broken],
            ),
            patch(
                "haute.routes.pipeline.parse_pipeline_to_graph",
                side_effect=ValueError("pipeline structure is invalid"),
            ),
        ):
            resp = TestClient(app).get("/api/pipeline")

        assert resp.status_code == 422
        assert resp.json() == {"detail": "pipeline structure is invalid"}


# ---------------------------------------------------------------------------
# GET /api/pipeline/{name}
# ---------------------------------------------------------------------------


class TestGetPipelineByName:
    def test_found(self, client: TestClient):
        resp = client.get("/api/pipeline/test_pipeline")
        assert resp.status_code == 200
        graph = resp.json()
        assert graph["pipeline_name"] == "test_pipeline"

    def test_not_found(self, client: TestClient):
        resp = client.get("/api/pipeline/nonexistent")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/pipeline/preview
# ---------------------------------------------------------------------------


class TestPreviewNode:
    def test_preview_returns_node_data(self, client: TestClient, pipeline_dir: Path):
        from haute.parser import parse_pipeline_file

        graph = parse_pipeline_file(pipeline_dir / "test_pipeline.py")
        node_id = graph.nodes[0].id

        resp = client.post(
            "/api/pipeline/preview",
            json={
                "graph": graph.model_dump(),
                "node_id": node_id,
                "row_limit": 10,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["node_id"] == node_id
        assert data["status"] == "ok"
        assert data["row_count"] <= 10
        assert len(data["columns"]) > 0
        assert "node_statuses" in data
        assert node_id in data["node_statuses"]
        assert data["node_statuses"][node_id] == "ok"

    def test_preview_ignores_unassigned_submodel_input_draft(
        self,
        client: TestClient,
        pipeline_dir: Path,
    ) -> None:
        from unittest.mock import patch

        from haute.schemas import NodeResult

        graph = {
            "nodes": [
                {
                    "id": "nb_batch",
                    "type": "pipelineNode",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "nb_batch2",
                        "nodeType": "dataInput",
                        "config": {},
                    },
                },
                {
                    "id": "submodel__sm",
                    "type": "submodel",
                    "position": {"x": 300, "y": 0},
                    "data": {
                        "label": "sm",
                        "nodeType": "submodel",
                        "config": {
                            "childNodeIds": ["child_a", "child_b"],
                            "inputPorts": [],
                            "outputPorts": [],
                        },
                    },
                },
            ],
            "edges": [
                {
                    "id": "unassigned",
                    "source": "nb_batch",
                    "target": "submodel__sm",
                    "sourceHandle": None,
                    "targetHandle": None,
                }
            ],
            "pipeline_name": "draft_preview",
            "source_file": str(pipeline_dir / "test_pipeline.py"),
            "submodels": {
                "sm": {
                    "file": "modules/sm.py",
                    "childNodeIds": ["child_a", "child_b"],
                    "inputPorts": [],
                    "outputPorts": [],
                    "graph": {
                        "nodes": [
                            {
                                "id": child_id,
                                "type": "pipelineNode",
                                "position": {"x": 0, "y": 0},
                                "data": {
                                    "label": child_id,
                                    "nodeType": "polars",
                                    "config": {},
                                },
                            }
                            for child_id in ("child_a", "child_b")
                        ],
                        "edges": [],
                    },
                }
            },
        }

        with patch(
            "haute.routes.pipeline.execute_graph",
            return_value={"nb_batch": NodeResult(status="ok")},
        ) as execute_graph:
            response = client.post(
                "/api/pipeline/preview",
                json={"graph": graph, "node_id": "nb_batch"},
            )

        assert response.status_code == 200
        execution_graph = execute_graph.call_args.args[0]
        assert {node.id for node in execution_graph.nodes} == {
            "nb_batch",
            "child_a",
            "child_b",
        }
        assert execution_graph.edges == []

    def test_preview_returns_relevant_ancestor_node_schema_maps(
        self,
        client: TestClient,
        pipeline_dir: Path,
    ):
        from haute.parser import parse_pipeline_file

        graph = parse_pipeline_file(pipeline_dir / "test_pipeline.py")

        resp = client.post(
            "/api/pipeline/preview",
            json={
                "graph": graph.model_dump(),
                "node_id": "transform",
                "row_limit": 10,
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        node_columns = data["node_columns"]
        node_available_columns = data["node_available_columns"]
        node_schema_warnings = data["node_schema_warnings"]

        assert set(node_columns) == {"source", "transform"}
        assert node_columns["source"] == [
            {"name": "x", "dtype": "Int64"},
            {"name": "y", "dtype": "Int64"},
        ]
        assert node_columns["transform"] == [
            {"name": "x", "dtype": "Int64"},
            {"name": "y", "dtype": "Int64"},
        ]
        assert node_available_columns["source"] == node_columns["source"]
        assert node_available_columns["transform"] == node_columns["transform"]
        assert node_schema_warnings["source"] == []
        assert node_schema_warnings["transform"] == data["schema_warnings"]

    def test_preview_empty_graph_returns_400(self, client: TestClient):
        resp = client.post(
            "/api/pipeline/preview",
            json={
                "graph": {"nodes": [], "edges": []},
                "node_id": "x",
            },
        )
        assert resp.status_code == 400

    def test_preview_edge_join_missing_keys_returns_node_error(
        self,
        client: TestClient,
    ) -> None:
        """edgeJoin config errors should render in-preview, not as HTTP 500."""
        graph = {
            "nodes": [
                {
                    "id": "quotes",
                    "type": "pipelineNode",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "Quotes",
                        "nodeType": "constant",
                        "config": {"values": [{"name": "region", "value": "N"}]},
                    },
                },
                {
                    "id": "lookup",
                    "type": "pipelineNode",
                    "position": {"x": 0, "y": 120},
                    "data": {
                        "label": "Lookup",
                        "nodeType": "constant",
                        "config": {"values": [{"name": "region", "value": "N"}]},
                    },
                },
                {
                    "id": "join",
                    "type": "pipelineNode",
                    "position": {"x": 240, "y": 60},
                    "data": {
                        "label": "Join Rates",
                        "nodeType": "edgeJoin",
                        "config": {
                            "baseInput": "quotes",
                            "joinInput": "lookup",
                            "how": "left",
                        },
                    },
                },
            ],
            "edges": [
                {
                    "id": "e_quotes_join",
                    "source": "quotes",
                    "target": "join",
                    "targetHandle": "base",
                },
                {
                    "id": "e_lookup_join",
                    "source": "lookup",
                    "target": "join",
                    "targetHandle": "join",
                },
            ],
        }

        resp = client.post(
            "/api/pipeline/preview",
            json={
                "graph": graph,
                "node_id": "join",
                "row_limit": 10,
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["node_id"] == "join"
        assert data["status"] == "error"
        assert "edgeJoin non-cross joins require join keys" in data["error"]

    def test_preview_superseded_returns_409(
        self,
        client: TestClient,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from haute.parser import parse_pipeline_file
        from haute.routes._supersession import SupersededRequestError
        from haute.routes.pipeline import _preview_supersession

        graph = parse_pipeline_file(pipeline_dir / "test_pipeline.py")
        node_id = graph.nodes[0].id

        async def raise_superseded(*args, **kwargs):
            raise SupersededRequestError("Preview request superseded by a newer request")

        monkeypatch.setattr(_preview_supersession, "run_latest", raise_superseded)

        resp = client.post(
            "/api/pipeline/preview",
            json={
                "graph": graph.model_dump(),
                "node_id": node_id,
                "row_limit": 10,
            },
        )

        assert resp.status_code == 409
        assert resp.json()["detail"] == "Preview request superseded by a newer request"


# ---------------------------------------------------------------------------
# POST /api/pipeline/trace
# ---------------------------------------------------------------------------


class TestTraceRow:
    def test_trace_returns_steps(self, client: TestClient, pipeline_dir: Path):
        from haute.parser import parse_pipeline_file

        graph = parse_pipeline_file(pipeline_dir / "test_pipeline.py")

        resp = client.post(
            "/api/pipeline/trace",
            json={
                "graph": graph.model_dump(),
                "row_index": 0,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "trace" in data
        assert len(data["trace"]["steps"]) >= 2

    def test_trace_empty_graph_returns_400(self, client: TestClient):
        resp = client.post(
            "/api/pipeline/trace",
            json={
                "graph": {"nodes": [], "edges": []},
            },
        )
        assert resp.status_code == 400

    def test_trace_superseded_returns_409(
        self,
        client: TestClient,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from haute.parser import parse_pipeline_file
        from haute.routes._supersession import SupersededRequestError
        from haute.routes.pipeline import _trace_supersession

        graph = parse_pipeline_file(pipeline_dir / "test_pipeline.py")

        async def raise_superseded(*args, **kwargs):
            raise SupersededRequestError("Trace request superseded by a newer request")

        monkeypatch.setattr(_trace_supersession, "run_latest", raise_superseded)

        resp = client.post(
            "/api/pipeline/trace",
            json={
                "graph": graph.model_dump(),
                "row_index": 0,
            },
        )

        assert resp.status_code == 409
        assert resp.json()["detail"] == "Trace request superseded by a newer request"


# ---------------------------------------------------------------------------
# POST /api/pipeline/save
# ---------------------------------------------------------------------------


class TestSavePipeline:
    def test_save_creates_files(self, client: TestClient, pipeline_dir: Path):
        graph = {
            "nodes": [
                {
                    "id": "s",
                    "type": "pipelineNode",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "Source",
                        "nodeType": "dataInput",
                        "config": _file_input_config("d.parquet"),
                    },
                },
            ],
            "edges": [],
        }
        resp = client.post(
            "/api/pipeline/save",
            json={
                "name": "saved_pipe",
                "description": "Test save",
                "graph": graph,
                "source_file": "saved_pipe.py",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "saved"
        assert "saved_pipe.py" in data["file"]

        # Check files were created
        py_file = pipeline_dir / data["file"]
        assert py_file.exists()
        content = py_file.read_text()
        assert "import polars as pl" in content
        assert 'Pipeline("saved_pipe"' in content

        # Sidecar should exist too
        sidecar = py_file.with_suffix(".haute.json")
        assert sidecar.exists()


# ---------------------------------------------------------------------------
# POST /api/pipeline/output-destination and /api/pipeline/write-output
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_widen_sandbox_root")
class TestExecuteSinkEndpoint:
    def test_output_destination_uses_backend_resolution_without_writing(
        self, client: TestClient, pipeline_dir: Path
    ) -> None:
        graph = {
            "nodes": [
                {
                    "id": "sink",
                    "type": "pipelineNode",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "sink",
                        "nodeType": "dataOutput",
                        "config": _file_output_config("report"),
                    },
                },
            ],
            "edges": [],
        }

        response = client.post(
            "/api/pipeline/output-destination",
            json={"graph": graph, "node_id": "sink"},
        )

        assert response.status_code == 200
        assert response.json() == {
            "path": "outputs/report.parquet",
            "format": "parquet",
            "suffix_mismatch": False,
        }
        assert not (pipeline_dir / "outputs").exists()

        graph["nodes"][0]["data"]["config"]["path"] = "report.csv"
        mismatch = client.post(
            "/api/pipeline/output-destination",
            json={"graph": graph, "node_id": "sink"},
        )
        assert mismatch.status_code == 200
        assert mismatch.json() == {
            "path": "outputs/report.csv",
            "format": "parquet",
            "suffix_mismatch": True,
        }

    def test_output_destination_uses_selected_project_root_when_cwd_differs(
        self,
        client: TestClient,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        set_project_root(pipeline_dir)
        unrelated_cwd = pipeline_dir / "unrelated-cwd"
        unrelated_cwd.mkdir()
        monkeypatch.chdir(unrelated_cwd)
        graph = {
            "source_file": str(pipeline_dir / "test_pipeline.py"),
            "nodes": [
                {
                    "id": "sink",
                    "type": "pipelineNode",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "sink",
                        "nodeType": "dataOutput",
                        "config": _file_output_config("report"),
                    },
                },
            ],
            "edges": [],
        }

        response = client.post(
            "/api/pipeline/output-destination",
            json={"graph": graph, "node_id": "sink"},
        )

        assert response.status_code == 200
        assert response.json()["path"] == "outputs/report.parquet"
        assert not (unrelated_cwd / "outputs").exists()

    def test_sink_writes_output(self, client: TestClient, pipeline_dir: Path):
        out_path = pipeline_dir / "output" / "result.parquet"
        data_path = pipeline_dir / "data" / "input.parquet"

        graph = {
            "nodes": [
                {
                    "id": "src",
                    "type": "pipelineNode",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "src",
                        "nodeType": "dataInput",
                        "config": make_ready_file_input_config(str(data_path)),
                    },
                },
                {
                    "id": "sink",
                    "type": "pipelineNode",
                    "position": {"x": 300, "y": 0},
                    "data": {
                        "label": "sink",
                        "nodeType": "dataOutput",
                        "config": _file_output_config("output/result.parquet"),
                    },
                },
            ],
            "edges": [{"id": "e1", "source": "src", "target": "sink"}],
        }
        resp = client.post(
            "/api/pipeline/write-output",
            json={
                "graph": graph,
                "node_id": "sink",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["row_count"] == 3
        assert out_path.exists()

        collision = client.post(
            "/api/pipeline/write-output",
            json={"graph": graph, "node_id": "sink"},
        )
        assert collision.status_code == 409
        assert collision.json()["detail"] == (
            "Output destination already exists: output/result.parquet"
        )

        overwrite = client.post(
            "/api/pipeline/write-output",
            json={"graph": graph, "node_id": "sink", "overwrite": True},
        )
        assert overwrite.status_code == 200
        assert overwrite.json()["row_count"] == 3

        non_boolean = client.post(
            "/api/pipeline/write-output",
            json={"graph": graph, "node_id": "sink", "overwrite": "true"},
        )
        assert non_boolean.status_code == 422

    def test_invalid_output_config_returns_safe_400(
        self, client: TestClient, pipeline_dir: Path
    ) -> None:
        data_path = pipeline_dir / "data" / "input.parquet"
        graph = {
            "nodes": [
                {
                    "id": "src",
                    "type": "pipelineNode",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "src",
                        "nodeType": "dataInput",
                        "config": make_ready_file_input_config(str(data_path)),
                    },
                },
                {
                    "id": "sink",
                    "type": "pipelineNode",
                    "position": {"x": 300, "y": 0},
                    "data": {
                        "label": "sink",
                        "nodeType": "dataOutput",
                        "config": {
                            "outputType": "unsupported",
                            "format": "parquet",
                            "mode": "sink",
                            "path": "/secure/output.parquet",
                            "arguments": {},
                        },
                    },
                },
            ],
            "edges": [{"id": "e1", "source": "src", "target": "sink"}],
        }

        resp = client.post("/api/pipeline/write-output", json={"graph": graph, "node_id": "sink"})

        assert resp.status_code == 400
        assert "/secure/" not in resp.json()["detail"]


# ---------------------------------------------------------------------------
# GET /api/files
# ---------------------------------------------------------------------------


class TestBrowseFiles:
    def test_browse_project_root(self, client: TestClient, pipeline_dir: Path):
        resp = client.get("/api/files", params={"dir": ".", "extensions": ".parquet,.py"})
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        # Should find at least the data/ dir
        names = [item["name"] for item in data["items"]]
        assert "data" in names

    def test_browse_data_dir(self, client: TestClient, pipeline_dir: Path):
        resp = client.get("/api/files", params={"dir": "data", "extensions": ".parquet"})
        assert resp.status_code == 200
        data = resp.json()
        files = [i for i in data["items"] if i["type"] == "file"]
        assert len(files) == 1
        assert files[0]["name"] == "input.parquet"
        assert files[0]["size"] > 0

    def test_browse_outside_project_returns_403(self, client: TestClient):
        resp = client.get("/api/files", params={"dir": "../../../etc"})
        assert resp.status_code == 403

    def test_browse_nonexistent_dir_returns_404(self, client: TestClient):
        resp = client.get("/api/files", params={"dir": "no_such_dir"})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/schema
# ---------------------------------------------------------------------------


class TestGetSchema:
    def test_parquet_schema(self, client: TestClient, pipeline_dir: Path):
        resp = client.get("/api/schema", params={"path": "data/input.parquet"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["row_count"] == 3
        assert data["column_count"] == 2
        col_names = [c["name"] for c in data["columns"]]
        assert "x" in col_names
        assert "y" in col_names
        assert len(data["preview"]) <= 5

    def test_csv_schema(self, client: TestClient, pipeline_dir: Path):
        csv_path = pipeline_dir / "data" / "test.csv"
        pl.DataFrame({"a": [1, 2], "b": ["x", "y"]}).write_csv(csv_path)

        resp = client.get("/api/schema", params={"path": "data/test.csv"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["column_count"] == 2

    def test_schema_outside_project_returns_403(self, client: TestClient):
        resp = client.get("/api/schema", params={"path": "../../../etc/passwd"})
        assert resp.status_code == 403

    def test_schema_not_found_returns_404(self, client: TestClient):
        resp = client.get("/api/schema", params={"path": "no_such_file.parquet"})
        assert resp.status_code == 404

    def test_unsupported_type_returns_400(self, client: TestClient, pipeline_dir: Path):
        (pipeline_dir / "data" / "test.txt").write_text("hello")
        resp = client.get("/api/schema", params={"path": "data/test.txt"})
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Submodel routes -- POST /api/submodel/create, GET /api/submodel/{name},
#                   POST /api/submodel/dissolve
# ---------------------------------------------------------------------------


@pytest.fixture()
def three_node_graph(pipeline_dir: Path) -> dict:
    """Parse the test pipeline and return its graph as a dict payload."""
    from haute._pipeline_revision import pipeline_document_revision
    from haute.parser import parse_pipeline_file

    parent = pipeline_dir / "test_pipeline.py"
    graph = parse_pipeline_file(parent)
    revision = pipeline_document_revision(graph, pipeline_path=parent, project_root=pipeline_dir)
    graph = graph.model_copy(update={"source_revision": revision})
    return graph.model_dump()


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

        # Verify the submodel node exists in the returned graph
        returned_node_ids = {n["id"] for n in data["graph"]["nodes"]}
        assert "submodel__my_submodel" in returned_node_ids
        # Original selected nodes should be gone from parent
        for nid in selected:
            assert nid not in returned_node_ids

        # Verify files were written to disk
        assert (pipeline_dir / "modules" / "my_submodel.py").exists()
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

        # Now fetch it
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
        updated_graph = create_resp.json()["graph"]

        # Dissolve it
        resp = client.post(
            "/api/submodel/dissolve",
            json={
                "submodel_name": "temp_group",
                "graph": updated_graph,
                "source_file": "test_pipeline.py",
                "pipeline_name": "test_pipeline",
                "base_revision": create_resp.json()["source_revision"],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

        # The flattened graph should have the original nodes back
        flat_ids = {n["id"] for n in data["graph"]["nodes"]}
        assert "submodel__temp_group" not in flat_ids
        for nid in node_ids:
            assert nid in flat_ids

        # The submodel file should be deleted
        assert not (pipeline_dir / "modules" / "temp_group.py").exists()

    def test_dissolve_nonexistent_submodel_returns_404(
        self,
        client: TestClient,
        three_node_graph: dict,
    ):
        resp = client.post(
            "/api/submodel/dissolve",
            json={
                "submodel_name": "ghost",
                "graph": three_node_graph,
                "source_file": "test_pipeline.py",
                "pipeline_name": "test_pipeline",
                "base_revision": three_node_graph["source_revision"],
            },
        )
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# WebSocket -- /ws/sync connect/disconnect and broadcast
# ---------------------------------------------------------------------------


class TestWebSocket:
    def test_connect_and_disconnect(self, client: TestClient):
        with client.websocket_connect(
            "/ws/sync",
            headers={"origin": "http://localhost"},
        ) as ws:
            # Connection should be accepted -- sending a keep-alive message works
            ws.send_text("ping")
        # No error means connect + clean disconnect succeeded

    def test_broadcast_reaches_connected_client(
        self,
        client: TestClient,
        pipeline_dir: Path,
    ):
        """Save endpoint writes files and triggers sidecar -- verify the
        full HTTP flow still works with an active WebSocket connection."""
        from haute.routes._helpers import ws_clients

        with client.websocket_connect(
            "/ws/sync",
            headers={"origin": "http://localhost"},
        ):
            assert len(ws_clients) >= 1

            # Use a save call to exercise the full stack (which calls mark_self_write)
            graph = {
                "nodes": [
                    {
                        "id": "s",
                        "type": "pipelineNode",
                        "position": {"x": 0, "y": 0},
                        "data": {
                            "label": "S",
                            "nodeType": "dataInput",
                            "config": _file_input_config("d.parquet"),
                        },
                    },
                ],
                "edges": [],
            }
            resp = client.post(
                "/api/pipeline/save",
                json={
                    "name": "ws_test",
                    "description": "",
                    "graph": graph,
                    "source_file": "ws_test.py",
                },
            )
            assert resp.status_code == 200

        # After disconnect, client should be removed
        assert len(ws_clients) == 0


class TestWebSocketResync:
    """Client-requested reconnect resync over /ws/sync."""

    class _CollectingWebSocket:
        def __init__(self) -> None:
            from starlette.datastructures import Headers, QueryParams

            from haute._local_security import SESSION_TOKEN_COOKIE, local_session_token

            self.headers = Headers(
                {
                    "host": "localhost",
                    "origin": "http://localhost",
                    "cookie": f"{SESSION_TOKEN_COOKIE}={local_session_token()}",
                }
            )
            self.scope = {"scheme": "ws"}
            self.query_params = QueryParams("")
            self.frames: list[dict[str, object]] = []

        async def send_text(self, payload: str) -> None:
            self.frames.append(json.loads(payload))

        async def close(self, *, code: int, reason: str) -> None:
            self.frames.append({"type": "close", "code": code, "reason": reason})

    def test_resync_sends_graph_update_for_discovered_pipeline(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import asyncio
        from unittest.mock import patch

        from haute.server import _graph_payload_fingerprint, _handle_ws_sync_message
        from haute.server import parse_pipeline_to_graph as _real_parse

        monkeypatch.chdir(pipeline_dir)

        pipeline_file = pipeline_dir / "test_pipeline.py"
        parsed_paths: list[Path] = []

        def _recording_parse(path: Path):
            parsed_paths.append(path)
            return _real_parse(path)

        ws = self._CollectingWebSocket()
        with (
            patch("haute.server.discover_pipelines", return_value=[pipeline_file]),
            patch("haute.server.parse_pipeline_to_graph", side_effect=_recording_parse),
        ):
            asyncio.run(
                _handle_ws_sync_message(
                    ws,  # type: ignore[arg-type]
                    json.dumps({"type": "resync", "source_file": "test_pipeline.py"}),
                )
            )

        assert parsed_paths == [pipeline_file]
        assert len(ws.frames) == 1
        frame = ws.frames[0]
        assert frame["type"] == "graph_update"
        assert frame["source_file"] == "test_pipeline.py"
        assert isinstance(frame["graph"], dict)
        assert frame["graph_fingerprint"] == _graph_payload_fingerprint(frame["graph"])  # type: ignore[arg-type]

    def test_resync_skips_graph_update_when_client_fingerprint_unchanged(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import asyncio
        from unittest.mock import patch

        from haute.server import (
            _graph_payload_fingerprint,
            _handle_ws_sync_message,
        )
        from haute.server import parse_pipeline_to_graph as _real_parse

        monkeypatch.chdir(pipeline_dir)

        pipeline_file = pipeline_dir / "test_pipeline.py"
        graph_fingerprint = _graph_payload_fingerprint(_real_parse(pipeline_file).model_dump())

        parsed_paths: list[Path] = []

        def _recording_parse(path: Path):
            parsed_paths.append(path)
            return _real_parse(path)

        ws = self._CollectingWebSocket()
        with (
            patch("haute.server.discover_pipelines", return_value=[pipeline_file]),
            patch("haute.server.parse_pipeline_to_graph", side_effect=_recording_parse),
        ):
            asyncio.run(
                _handle_ws_sync_message(
                    ws,  # type: ignore[arg-type]
                    json.dumps(
                        {
                            "type": "resync",
                            "source_file": "test_pipeline.py",
                            "graph_fingerprint": graph_fingerprint,
                        }
                    ),
                )
            )

        assert parsed_paths == [pipeline_file]
        assert ws.frames == []

    def test_resync_stale_client_fingerprint_sends_graph_update(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import asyncio
        from unittest.mock import patch

        from haute.server import (
            _graph_payload_fingerprint,
            _handle_ws_sync_message,
        )
        from haute.server import parse_pipeline_to_graph as _real_parse

        monkeypatch.chdir(pipeline_dir)

        pipeline_file = pipeline_dir / "test_pipeline.py"
        stale_fingerprint = hashlib.sha256(b"stale client graph").hexdigest()

        parsed_paths: list[Path] = []

        def _recording_parse(path: Path):
            parsed_paths.append(path)
            return _real_parse(path)

        ws = self._CollectingWebSocket()
        with (
            patch("haute.server.discover_pipelines", return_value=[pipeline_file]),
            patch("haute.server.parse_pipeline_to_graph", side_effect=_recording_parse),
        ):
            asyncio.run(
                _handle_ws_sync_message(
                    ws,  # type: ignore[arg-type]
                    json.dumps(
                        {
                            "type": "resync",
                            "source_file": "test_pipeline.py",
                            "graph_fingerprint": stale_fingerprint,
                        }
                    ),
                )
            )

        assert parsed_paths == [pipeline_file]
        assert [frame["type"] for frame in ws.frames] == ["graph_update"]
        assert ws.frames[0]["graph_fingerprint"] == _graph_payload_fingerprint(
            ws.frames[0]["graph"]  # type: ignore[arg-type]
        )

    def test_resync_does_not_use_watcher_fingerprint_to_silence_stale_client(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import asyncio
        from unittest.mock import patch

        from haute.server import (
            _graph_payload_fingerprint,
            _handle_ws_sync_message,
        )
        from haute.server import parse_pipeline_to_graph as _real_parse

        monkeypatch.chdir(pipeline_dir)

        pipeline_file = pipeline_dir / "test_pipeline.py"
        current_graph_fingerprint = _graph_payload_fingerprint(
            _real_parse(pipeline_file).model_dump()
        )

        ws = self._CollectingWebSocket()
        with (
            patch("haute.server.discover_pipelines", return_value=[pipeline_file]),
            patch("haute.server.parse_pipeline_to_graph", side_effect=_real_parse),
        ):
            asyncio.run(
                _handle_ws_sync_message(
                    ws,  # type: ignore[arg-type]
                    json.dumps({"type": "resync", "source_file": "test_pipeline.py"}),
                )
            )

            assert [frame["type"] for frame in ws.frames] == ["graph_update"]
            ws.frames.clear()

            asyncio.run(
                _handle_ws_sync_message(
                    ws,  # type: ignore[arg-type]
                    json.dumps(
                        {
                            "type": "resync",
                            "source_file": "test_pipeline.py",
                            "graph_fingerprint": hashlib.sha256(
                                b"client missed broadcast"
                            ).hexdigest(),
                        }
                    ),
                )
            )

        assert [frame["type"] for frame in ws.frames] == ["graph_update"]
        assert ws.frames[0]["graph_fingerprint"] == current_graph_fingerprint

    def test_resync_sidecar_only_change_sends_graph_update(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import asyncio
        from unittest.mock import patch

        from haute.server import _graph_payload_fingerprint, _handle_ws_sync_message
        from haute.server import parse_pipeline_to_graph as _real_parse

        monkeypatch.chdir(pipeline_dir)

        pipeline_file = pipeline_dir / "test_pipeline.py"

        ws = self._CollectingWebSocket()
        with (
            patch("haute.server.discover_pipelines", return_value=[pipeline_file]),
            patch("haute.server.parse_pipeline_to_graph", side_effect=_real_parse),
        ):
            asyncio.run(
                _handle_ws_sync_message(
                    ws,  # type: ignore[arg-type]
                    json.dumps({"type": "resync", "source_file": "test_pipeline.py"}),
                )
            )

            assert len(ws.frames) == 1
            first_graph = ws.frames[0]["graph"]
            assert isinstance(first_graph, dict)
            first_fingerprint = _graph_payload_fingerprint(first_graph)
            first_node = first_graph["nodes"][0]  # type: ignore[index]
            assert isinstance(first_node, dict)
            node_id = first_node["id"]
            assert isinstance(node_id, str)

            pipeline_file.with_suffix(".haute.json").write_text(
                json.dumps({"positions": {node_id: {"x": 321, "y": 654}}}),
                encoding="utf-8",
            )

            ws.frames.clear()
            asyncio.run(
                _handle_ws_sync_message(
                    ws,  # type: ignore[arg-type]
                    json.dumps(
                        {
                            "type": "resync",
                            "source_file": "test_pipeline.py",
                            "graph_fingerprint": first_fingerprint,
                        }
                    ),
                )
            )

        assert [frame["type"] for frame in ws.frames] == ["graph_update"]
        updated_graph = ws.frames[0]["graph"]
        assert isinstance(updated_graph, dict)
        updated_node = next(
            node
            for node in updated_graph["nodes"]  # type: ignore[index]
            if isinstance(node, dict) and node.get("id") == node_id
        )
        assert updated_node["position"] == {"x": 321.0, "y": 654.0}

    def test_resync_offloads_discovery_and_parse_work(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import asyncio
        from unittest.mock import patch

        from haute.server import _handle_ws_sync_message, _last_broadcast_fp
        from haute.server import parse_pipeline_to_graph as _real_parse

        monkeypatch.chdir(pipeline_dir)

        pipeline_file = pipeline_dir / "test_pipeline.py"
        _last_broadcast_fp.clear()
        in_worker = False
        offload_calls = 0

        async def _recording_threadpool(func, *args, **kwargs):
            nonlocal in_worker, offload_calls
            offload_calls += 1
            in_worker = True
            try:
                return func(*args, **kwargs)
            finally:
                in_worker = False

        def _worker_only_discover() -> list[Path]:
            assert in_worker, "resync discovery must run outside the event loop"
            return [pipeline_file]

        def _worker_only_parse(path: Path):
            assert in_worker, "resync parse must run outside the event loop"
            return _real_parse(path)

        ws = self._CollectingWebSocket()
        with (
            patch("haute.server.run_in_threadpool", side_effect=_recording_threadpool, create=True),
            patch("haute.server.discover_pipelines", side_effect=_worker_only_discover),
            patch("haute.server.parse_pipeline_to_graph", side_effect=_worker_only_parse),
        ):
            asyncio.run(
                _handle_ws_sync_message(
                    ws,  # type: ignore[arg-type]
                    json.dumps({"type": "resync", "source_file": "test_pipeline.py"}),
                )
            )

        assert offload_calls == 1
        assert [frame["type"] for frame in ws.frames] == ["graph_update"]

    def test_resync_rejects_non_discovered_python_file_without_parsing(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import asyncio
        from unittest.mock import patch

        from haute.server import _handle_ws_sync_message

        monkeypatch.chdir(pipeline_dir)

        ordinary_file = pipeline_dir / "helper.py"
        ordinary_file.write_text("def helper():\n    return 1\n")

        parsed_paths: list[Path] = []

        def _recording_parse(path: Path):
            parsed_paths.append(path)
            raise AssertionError(f"unexpected parse for {path}")

        ws = self._CollectingWebSocket()
        with (
            patch(
                "haute.server.discover_pipelines",
                return_value=[pipeline_dir / "test_pipeline.py"],
            ),
            patch("haute.server.parse_pipeline_to_graph", side_effect=_recording_parse),
        ):
            asyncio.run(
                _handle_ws_sync_message(
                    ws,  # type: ignore[arg-type]
                    json.dumps({"type": "resync", "source_file": "helper.py"}),
                )
            )

        assert parsed_paths == []
        assert ws.frames == [
            {
                "type": "parse_error",
                "error": "Resync source is not a discovered pipeline",
                "source_file": "helper.py",
            }
        ]

    def test_resync_discovered_pipeline_parse_error_sends_parse_error(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import asyncio
        from unittest.mock import patch

        from haute.server import _handle_ws_sync_message

        monkeypatch.chdir(pipeline_dir)

        pipeline_file = pipeline_dir / "test_pipeline.py"
        ws = self._CollectingWebSocket()
        with (
            patch("haute.server.discover_pipelines", return_value=[pipeline_file]),
            patch(
                "haute.server.parse_pipeline_to_graph",
                side_effect=SyntaxError("bad syntax"),
            ),
        ):
            asyncio.run(
                _handle_ws_sync_message(
                    ws,  # type: ignore[arg-type]
                    json.dumps({"type": "resync", "source_file": "test_pipeline.py"}),
                )
            )

        assert ws.frames == [
            {
                "type": "parse_error",
                "error": "bad syntax",
                "source_file": "test_pipeline.py",
            }
        ]

    def test_ws_sync_dispatches_resync_message(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import asyncio
        from unittest.mock import patch

        from fastapi import WebSocketDisconnect

        from haute.server import parse_pipeline_to_graph as _real_parse
        from haute.server import ws_sync

        monkeypatch.chdir(pipeline_dir)

        pipeline_file = pipeline_dir / "test_pipeline.py"

        class _OneMessageWebSocket(self._CollectingWebSocket):
            def __init__(self) -> None:
                super().__init__()
                self.accepted = False
                self.messages = [json.dumps({"type": "resync", "source_file": "test_pipeline.py"})]

            async def accept(self) -> None:
                self.accepted = True

            async def receive_text(self) -> str:
                if self.messages:
                    return self.messages.pop(0)
                raise WebSocketDisconnect()

        ws = _OneMessageWebSocket()
        with (
            patch("haute.server.discover_pipelines", return_value=[pipeline_file]),
            patch("haute.server.parse_pipeline_to_graph", side_effect=_real_parse),
        ):
            asyncio.run(ws_sync(ws))  # type: ignore[arg-type]

        assert ws.accepted is True
        assert [frame["type"] for frame in ws.frames] == ["graph_update"]

    def test_keep_alive_text_is_ignored(self) -> None:
        import asyncio

        from haute.server import _handle_ws_sync_message

        ws = self._CollectingWebSocket()
        asyncio.run(_handle_ws_sync_message(ws, "ping"))  # type: ignore[arg-type]

        assert ws.frames == []

    def test_non_resync_json_message_is_ignored(self) -> None:
        import asyncio

        from haute.server import _handle_ws_sync_message

        ws = self._CollectingWebSocket()
        asyncio.run(
            _handle_ws_sync_message(  # type: ignore[arg-type]
                ws,
                json.dumps({"type": "heartbeat"}),
            )
        )

        assert ws.frames == []

    def test_resync_without_string_source_file_sends_parse_error(self) -> None:
        import asyncio

        from haute.server import _handle_ws_sync_message

        ws = self._CollectingWebSocket()
        asyncio.run(
            _handle_ws_sync_message(  # type: ignore[arg-type]
                ws,
                json.dumps({"type": "resync", "source_file": None}),
            )
        )

        assert ws.frames == [
            {
                "type": "parse_error",
                "error": "Resync request requires a source_file",
                "source_file": "",
            }
        ]


class TestServerPipelineDiscoveryHelpers:
    def test_discovered_pipeline_paths_filters_non_pipeline_python(self, tmp_path: Path) -> None:
        from haute.server import _discovered_pipeline_paths

        pipeline = tmp_path / "main.py"
        ignored_dunder = tmp_path / "__init__.py"
        ignored_text = tmp_path / "notes.txt"
        for path in (pipeline, ignored_dunder, ignored_text):
            path.write_text("", encoding="utf-8")

        with patch(
            "haute.server.discover_pipelines",
            return_value=[pipeline, ignored_dunder, ignored_text],
        ):
            discovered = _discovered_pipeline_paths()

        assert discovered == {str(pipeline.resolve()): pipeline}

    def test_client_source_file_resolution_rejects_non_strings_and_blank_strings(self) -> None:
        from haute.server import _resolve_client_source_file

        assert _resolve_client_source_file(None) is None
        assert _resolve_client_source_file("  ") is None

    def test_client_source_file_resolution_uses_project_relative_paths(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from haute.server import _resolve_client_source_file

        monkeypatch.chdir(tmp_path)

        assert (
            _resolve_client_source_file("pipelines/main.py")
            == (tmp_path / "pipelines" / "main.py").resolve()
        )

    def test_wire_source_file_uses_project_relative_root_main(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from haute.server import _wire_source_file

        monkeypatch.chdir(tmp_path)
        main = tmp_path / "main.py"
        main.write_text("import haute\n\npipeline = haute.Pipeline('main')\n")

        assert _wire_source_file(main) == "main.py"


class TestBroadcast:
    def test_broadcast_removes_dead_clients(self):
        """Dead WebSocket clients should be pruned during broadcast."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        from haute.routes._helpers import broadcast, ws_clients

        dead_ws = MagicMock()
        dead_ws.send_text = AsyncMock(side_effect=RuntimeError("closed"))

        ws_clients.add(dead_ws)
        try:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(broadcast({"type": "test"}))
            finally:
                loop.close()
        finally:
            ws_clients.discard(dead_ws)

        assert dead_ws not in ws_clients

    def test_broadcast_delivers_to_live_client(self):
        """A live mock client should receive the broadcast message."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        from haute.routes._helpers import broadcast, ws_clients

        live_ws = MagicMock()
        live_ws.send_text = AsyncMock()

        ws_clients.add(live_ws)
        try:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(broadcast({"type": "graph_update", "graph": {}}))
            finally:
                loop.close()

            live_ws.send_text.assert_called_once()
            payload = live_ws.send_text.call_args[0][0]
            assert '"type": "graph_update"' in payload
        finally:
            ws_clients.discard(live_ws)


class TestEventBusBroadcastBridge:
    def test_skips_cleanly_without_running_loop(self) -> None:
        from haute.server import _broadcast_event_as_ws_message

        with patch("haute.server.logger.debug") as mock_debug:
            _broadcast_event_as_ws_message("graph_update", {"graph": {}, "source_file": "x.py"})

        mock_debug.assert_called_once()
        assert mock_debug.call_args.args[0] == "event_bus_broadcast_skipped"

    def test_schedules_broadcast_on_running_loop(self) -> None:
        from unittest.mock import AsyncMock

        from haute.server import _broadcast_event_as_ws_message

        async def _run() -> None:
            fake_broadcast = AsyncMock()
            with patch("haute.server.broadcast", fake_broadcast):
                _broadcast_event_as_ws_message(
                    "graph_update",
                    {"graph": {"nodes": []}, "source_file": "x.py"},
                )
                await asyncio.sleep(0)

            fake_broadcast.assert_awaited_once_with(
                {"type": "graph_update", "graph": {"nodes": []}, "source_file": "x.py"}
            )

        asyncio.run(_run())

    def test_ws_message_frame_rejects_payload_type_collision(self) -> None:
        from haute.server import _ws_message_frame

        with pytest.raises(ValueError, match="reserved WebSocket frame key"):
            _ws_message_frame("graph_update", {"type": "parse_error"})

    def test_logs_failed_broadcast_task(self) -> None:
        from haute.server import _broadcast_event_as_ws_message

        async def _boom(frame: dict[str, object]) -> None:
            raise RuntimeError("ws boom")

        async def _run() -> None:
            with (
                patch("haute.server.broadcast", _boom),
                patch("haute.server.logger.error") as mock_error,
            ):
                _broadcast_event_as_ws_message(
                    "parse_error",
                    {"error": "bad syntax", "source_file": "x.py"},
                )
                await asyncio.sleep(0)
                await asyncio.sleep(0)

            mock_error.assert_called()
            assert any(
                call.args
                and call.args[0] == "event_bus_broadcast_failed"
                and call.kwargs.get("error") == "ws boom"
                for call in mock_error.call_args_list
            )

        asyncio.run(_run())

    def test_default_bus_graph_and_parse_events_reach_registered_ws_client(self) -> None:
        """Exercise the real event-bus subscribers through the broadcast layer."""
        from haute._event_bus import default_bus
        from haute.routes._helpers import ws_clients_add, ws_clients_discard

        class _FakeWebSocket:
            def __init__(self) -> None:
                self.frames: list[dict[str, object]] = []

            async def send_text(self, payload: str) -> None:
                self.frames.append(json.loads(payload))

        async def _run() -> list[dict[str, object]]:
            ws = _FakeWebSocket()
            ws_clients_add(ws)  # type: ignore[arg-type]
            try:
                default_bus.publish(
                    "graph.update",
                    {"graph": {"nodes": [], "edges": []}, "source_file": "x.py"},
                )
                default_bus.publish(
                    "parse.error",
                    {"error": "bad syntax", "source_file": "x.py"},
                )
                for _ in range(20):
                    if len(ws.frames) >= 2:
                        break
                    await asyncio.sleep(0)
                return ws.frames
            finally:
                ws_clients_discard(ws)  # type: ignore[arg-type]

        frames = asyncio.run(_run())

        assert frames == [
            {
                "type": "graph_update",
                "graph": {"nodes": [], "edges": []},
                "source_file": "x.py",
            },
            {
                "type": "parse_error",
                "error": "bad syntax",
                "source_file": "x.py",
            },
        ]


# ---------------------------------------------------------------------------
# Self-write tracking
# ---------------------------------------------------------------------------


class TestSelfWriteTracking:
    def test_mark_and_check(self):
        from haute.routes._helpers import is_self_write, mark_self_write

        mark_self_write()
        assert is_self_write() is True

    def test_expires_after_cooldown(self, monkeypatch: pytest.MonkeyPatch):
        import time as _time

        import haute.routes._helpers as helpers

        # Freeze time, mark, then advance past cooldown
        fake_time = [100.0]
        monkeypatch.setattr(_time, "monotonic", lambda: fake_time[0])

        helpers.mark_self_write()
        assert helpers.is_self_write() is True

        fake_time[0] = 102.5  # 2.5s later, past the 2.0s cooldown
        assert helpers.is_self_write() is False


# ---------------------------------------------------------------------------
# File watcher logic (unit test with mocked awatch)
# ---------------------------------------------------------------------------


class TestFileWatcher:
    def test_py_change_triggers_broadcast(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """A .py file change should parse and broadcast a graph_update."""
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        monkeypatch.chdir(pipeline_dir)

        py_file = str(pipeline_dir / "test_pipeline.py")
        fake_changes = [(Change.modified, py_file)]

        async def _fake_awatch(*dirs, **kw):
            yield fake_changes

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict) -> None:
            broadcast_calls.append(data)

        # awatch is imported locally inside _file_watcher via
        # ``from watchfiles import awatch``, so patch it on the watchfiles module.
        with (
            patch("watchfiles.awatch", _fake_awatch),
            patch("haute.server.broadcast", _capture_broadcast),
            patch("haute.server.is_self_write", return_value=False),
            patch("haute.server._DEBOUNCE_SECONDS", 0),
        ):

            async def _run() -> None:
                await _run_file_watcher_and_drain()

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        assert len(broadcast_calls) >= 1
        assert broadcast_calls[0]["type"] == "graph_update"
        assert "graph" in broadcast_calls[0]

    def test_paused_skips_broadcast(self, pipeline_dir: Path, monkeypatch: pytest.MonkeyPatch):
        """S30: while the watcher is paused (a git op in flight), a .py change
        is dropped -- the wholesale tree replacement of a move/checkout must not
        be broadcast as user edits."""
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        monkeypatch.chdir(pipeline_dir)

        fake_changes = [(Change.modified, str(pipeline_dir / "test_pipeline.py"))]

        async def _fake_awatch(*dirs, **kw):
            yield fake_changes

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict) -> None:
            broadcast_calls.append(data)

        with (
            patch("watchfiles.awatch", _fake_awatch),
            patch("haute.server.broadcast", _capture_broadcast),
            patch("haute.server.is_self_write", return_value=False),
            patch("haute.server.watcher_is_paused", return_value=True),
            patch("haute.server._DEBOUNCE_SECONDS", 0),
        ):

            async def _run() -> None:
                await _run_file_watcher_and_drain()

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        assert len(broadcast_calls) == 0

    def test_non_py_files_ignored(self, pipeline_dir: Path, monkeypatch: pytest.MonkeyPatch):
        """Non-.py files should be ignored by the watcher."""
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        monkeypatch.chdir(pipeline_dir)

        fake_changes = [(Change.modified, str(pipeline_dir / "readme.txt"))]

        async def _fake_awatch(*dirs, **kw):
            yield fake_changes

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict) -> None:
            broadcast_calls.append(data)

        with (
            patch("watchfiles.awatch", _fake_awatch),
            patch("haute.server.broadcast", _capture_broadcast),
            patch("haute.server.is_self_write", return_value=False),
            patch("haute.server._DEBOUNCE_SECONDS", 0),
        ):

            async def _run() -> None:
                await _run_file_watcher_and_drain()

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        assert len(broadcast_calls) == 0

    def test_self_write_skips_broadcast(self, pipeline_dir: Path, monkeypatch: pytest.MonkeyPatch):
        """Changes during self-write cooldown should be skipped."""
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        monkeypatch.chdir(pipeline_dir)

        fake_changes = [(Change.modified, str(pipeline_dir / "test_pipeline.py"))]

        async def _fake_awatch(*dirs, **kw):
            yield fake_changes

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict) -> None:
            broadcast_calls.append(data)

        with (
            patch("watchfiles.awatch", _fake_awatch),
            patch("haute.server.broadcast", _capture_broadcast),
            patch("haute.server.is_self_write", return_value=True),
            patch("haute.server._DEBOUNCE_SECONDS", 0),
        ):

            async def _run() -> None:
                await _run_file_watcher_and_drain()

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        assert len(broadcast_calls) == 0

    def test_path_specific_self_write_does_not_skip_unrelated_user_edit(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """A server write for one path must not suppress another file in the same batch."""
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        from haute.routes._helpers import mark_self_write

        monkeypatch.chdir(pipeline_dir)

        self_written = pipeline_dir / "server_saved.py"
        user_edited = pipeline_dir / "test_pipeline.py"
        self_written.write_text("import haute\n\npipeline = haute.Pipeline('server_saved')\n")
        mark_self_write(self_written)

        fake_changes = [
            (Change.modified, str(self_written)),
            (Change.modified, str(user_edited)),
        ]

        async def _fake_awatch(*dirs, **kw):
            yield fake_changes

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict) -> None:
            broadcast_calls.append(data)

        with (
            patch("watchfiles.awatch", _fake_awatch),
            patch("haute.server.broadcast", _capture_broadcast),
            patch("haute.server._DEBOUNCE_SECONDS", 0),
        ):

            async def _run() -> None:
                await _run_file_watcher_and_drain()

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        assert [call["source_file"] for call in broadcast_calls] == ["test_pipeline.py"]

    def test_direct_non_discovered_python_files_are_not_parsed_or_broadcast(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Only discovery-positive direct .py edits should enter the parser."""
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        monkeypatch.chdir(pipeline_dir)

        ordinary_file = pipeline_dir / "helper.py"
        ordinary_file.write_text("def helper():\n    return 1\n")
        utility_file = pipeline_dir / "utility" / "helper.py"
        utility_file.parent.mkdir()
        utility_file.write_text("def helper():\n    return 1\n")
        cache_file = pipeline_dir / "__pycache__" / "cached.py"
        cache_file.parent.mkdir()
        cache_file.write_text("def cached():\n    return 1\n")

        fake_changes = [
            (Change.modified, str(ordinary_file)),
            (Change.modified, str(utility_file)),
            (Change.modified, str(cache_file)),
        ]

        async def _fake_awatch(*dirs, **kw):
            yield fake_changes

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict) -> None:
            broadcast_calls.append(data)

        parsed_paths: list[Path] = []

        def _recording_parse(path: Path):
            parsed_paths.append(path)
            raise AssertionError(f"unexpected parse for {path}")

        with (
            patch("watchfiles.awatch", _fake_awatch),
            patch("haute.server.broadcast", _capture_broadcast),
            patch("haute.server.is_self_write", return_value=False),
            patch(
                "haute.server.discover_pipelines",
                return_value=[pipeline_dir / "test_pipeline.py"],
            ),
            patch("haute.server.parse_pipeline_to_graph", side_effect=_recording_parse),
            patch("haute.server._DEBOUNCE_SECONDS", 0),
        ):

            async def _run() -> None:
                await _run_file_watcher_and_drain()

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        assert parsed_paths == []
        assert broadcast_calls == []

    def test_direct_discovered_python_file_is_parsed_and_broadcast(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Direct changes to discovered pipeline files still live-sync."""
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        monkeypatch.chdir(pipeline_dir)

        pipeline_file = pipeline_dir / "test_pipeline.py"
        fake_changes = [(Change.modified, str(pipeline_file))]

        async def _fake_awatch(*dirs, **kw):
            yield fake_changes

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict) -> None:
            broadcast_calls.append(data)

        parsed_paths: list[Path] = []
        from haute.server import parse_pipeline_to_graph as _real_parse

        def _recording_parse(path: Path):
            parsed_paths.append(path)
            return _real_parse(path)

        with (
            patch("watchfiles.awatch", _fake_awatch),
            patch("haute.server.broadcast", _capture_broadcast),
            patch("haute.server.is_self_write", return_value=False),
            patch("haute.server.discover_pipelines", return_value=[pipeline_file]),
            patch("haute.server.parse_pipeline_to_graph", side_effect=_recording_parse),
            patch("haute.server._DEBOUNCE_SECONDS", 0),
        ):

            async def _run() -> None:
                await _run_file_watcher_and_drain()

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        assert parsed_paths == [pipeline_file]
        assert [call["type"] for call in broadcast_calls] == ["graph_update"]


# ---------------------------------------------------------------------------
# Phase 1C: Pipeline route timeout + exception paths
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_widen_sandbox_root")
class TestPipelineTimeouts:
    """Timeout paths -- let asyncio.wait_for cancel pending route work."""

    @staticmethod
    async def _never_finishes(*_args: object, **_kwargs: object) -> None:
        await asyncio.sleep(60)

    @staticmethod
    def _sink_graph(pipeline_dir: Path) -> dict:
        data_path = pipeline_dir / "data" / "input.parquet"
        return {
            "nodes": [
                {
                    "id": "src",
                    "type": "pipelineNode",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "src",
                        "nodeType": "dataInput",
                        "config": make_ready_file_input_config(str(data_path)),
                    },
                },
                {
                    "id": "sink",
                    "type": "pipelineNode",
                    "position": {"x": 300, "y": 0},
                    "data": {
                        "label": "sink",
                        "nodeType": "dataOutput",
                        "config": _file_output_config("output/test_sink.parquet"),
                    },
                },
            ],
            "edges": [{"id": "e1", "source": "src", "target": "sink"}],
        }

    @pytest.mark.parametrize(
        ("endpoint", "use_parsed_graph"),
        [
            ("trace", True),
            ("preview", True),
            ("write-output", False),
        ],
        ids=["trace_timeout", "preview_timeout", "sink_timeout"],
    )
    def test_timeout_returns_504(
        self, client: TestClient, pipeline_dir: Path, endpoint: str, use_parsed_graph: bool
    ):
        from unittest.mock import patch

        if use_parsed_graph:
            from haute.parser import parse_pipeline_file

            graph = parse_pipeline_file(pipeline_dir / "test_pipeline.py")
            if endpoint == "trace":
                body = {"graph": graph.model_dump(), "row_index": 0}
            else:
                body = {"graph": graph.model_dump(), "node_id": graph.nodes[0].id}
        else:
            body = {"graph": self._sink_graph(pipeline_dir), "node_id": "sink"}

        with (
            patch(
                "haute.routes._timeouts.asyncio.to_thread",
                self._never_finishes,
            ),
            patch.dict(
                os.environ,
                {
                    "HAUTE_TRACE_TIMEOUT": "0.001",
                    "HAUTE_PREVIEW_TIMEOUT": "0.001",
                    "HAUTE_SINK_TIMEOUT": "0.001",
                },
            ),
        ):
            resp = client.post(f"/api/pipeline/{endpoint}", json=body)
        assert resp.status_code == 504


@pytest.mark.usefixtures("_widen_sandbox_root")
class TestPipelineExceptions:
    """Exception paths -- mock execute_graph to raise RuntimeError -> 500."""

    @staticmethod
    def _sink_graph(pipeline_dir: Path) -> dict:
        data_path = pipeline_dir / "data" / "input.parquet"
        return {
            "nodes": [
                {
                    "id": "src",
                    "type": "pipelineNode",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "src",
                        "nodeType": "dataInput",
                        "config": make_ready_file_input_config(str(data_path)),
                    },
                },
                {
                    "id": "sink",
                    "type": "pipelineNode",
                    "position": {"x": 300, "y": 0},
                    "data": {
                        "label": "sink",
                        "nodeType": "dataOutput",
                        "config": _file_output_config("output/test_sink.parquet"),
                    },
                },
            ],
            "edges": [{"id": "e1", "source": "src", "target": "sink"}],
        }

    @pytest.mark.parametrize(
        ("endpoint", "patch_target", "error_msg", "use_parsed_graph"),
        [
            # Route-side patch targets: the route module imports these
            # functions at top level (post-#101 hoist), so patching the
            # source modules (``haute.trace`` / ``haute.executor``) would
            # be a no-op -- the route still calls its own top-level
            # bindings.
            ("trace", "haute.routes.pipeline.execute_trace", "trace error", True),
            ("preview", "haute.routes.pipeline.execute_graph", "preview error", True),
            ("write-output", "haute.routes.pipeline.write_data_output", "sink error", False),
        ],
        ids=["trace_exception", "preview_exception", "sink_exception"],
    )
    def test_exception_returns_500(
        self,
        client: TestClient,
        pipeline_dir: Path,
        endpoint: str,
        patch_target: str,
        error_msg: str,
        use_parsed_graph: bool,
    ):
        from unittest.mock import patch

        if use_parsed_graph:
            from haute.parser import parse_pipeline_file

            graph = parse_pipeline_file(pipeline_dir / "test_pipeline.py")
            if endpoint == "trace":
                body = {"graph": graph.model_dump(), "row_index": 0}
            else:
                body = {"graph": graph.model_dump(), "node_id": graph.nodes[0].id}
        else:
            body = {"graph": self._sink_graph(pipeline_dir), "node_id": "sink"}

        with patch(patch_target, side_effect=RuntimeError(error_msg)):
            resp = client.post(f"/api/pipeline/{endpoint}", json=body)
        assert resp.status_code == 500
        assert error_msg not in resp.json()["detail"]
        assert "Check the server logs" in resp.json()["detail"]

    @pytest.mark.parametrize(
        ("error_type", "detail_fragment"),
        [
            (
                "publication",
                "supports atomic create-only publication",
            ),
            (
                "durability",
                "Verify the file before retrying",
            ),
        ],
    )
    def test_output_publication_failures_return_safe_actionable_details(
        self,
        client: TestClient,
        pipeline_dir: Path,
        error_type: str,
        detail_fragment: str,
    ) -> None:
        from unittest.mock import patch

        from haute.executor import DataOutputDurabilityError, DataOutputPublicationError

        error = (
            DataOutputPublicationError("output/test_sink.parquet")
            if error_type == "publication"
            else DataOutputDurabilityError("output/test_sink.parquet")
        )
        error.__cause__ = OSError("secret raw filesystem failure")

        with patch("haute.routes.pipeline.write_data_output", side_effect=error):
            response = client.post(
                "/api/pipeline/write-output",
                json={"graph": self._sink_graph(pipeline_dir), "node_id": "sink"},
            )

        assert response.status_code == 500
        assert detail_fragment in response.json()["detail"]
        assert "secret raw filesystem failure" not in response.json()["detail"]

    def test_api_input_schema_error_is_a_structured_422(
        self,
        client: TestClient,
        pipeline_dir: Path,
    ) -> None:
        data_path = pipeline_dir / "data" / "input.json"
        data_path.write_text('{"items": []}', encoding="utf-8")
        graph = {
            "nodes": [
                {
                    "id": "api",
                    "type": "pipelineNode",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "api",
                        "nodeType": "apiInput",
                        "config": {"path": str(data_path)},
                    },
                }
            ],
            "edges": [],
        }

        response = client.post(
            "/api/pipeline/preview",
            json={"graph": graph, "node_id": "api"},
        )

        assert response.status_code == 422
        assert response.json()["detail"] == {
            "error_code": "api_input_schema_invalid",
            "message": (
                "API Input has no v2 schema (tables[]). Open the node and click "
                "'Infer Tables' to populate the schema mapping, then preview again."
            ),
        }


class TestPreviewEdgeCases:
    """Preview edge cases: missing node in results."""

    def test_preview_node_not_in_results(self, client: TestClient, pipeline_dir: Path):
        """If the node_id is valid but not found in execute_graph results -> 404."""
        from unittest.mock import patch

        from haute.parser import parse_pipeline_file

        graph = parse_pipeline_file(pipeline_dir / "test_pipeline.py")

        # Route-scoped patch target: ``routes/pipeline.py`` imports
        # ``execute_graph`` at module top-level post-#101.
        with patch(
            "haute.routes.pipeline.execute_graph",
            return_value={},  # empty results
        ):
            resp = client.post(
                "/api/pipeline/preview",
                json={
                    "graph": graph.model_dump(),
                    "node_id": "nonexistent_node",
                },
            )
        assert resp.status_code == 404
        assert "not found in results" in resp.json()["detail"]


class TestListPipelinesParseError:
    """Test that a broken pipeline file returns an entry with error field."""

    def test_broken_pipeline_in_list(self, pipeline_dir: Path, monkeypatch: pytest.MonkeyPatch):
        """A pipeline with a parse exception should appear with error field."""
        from unittest.mock import patch

        monkeypatch.chdir(pipeline_dir)
        from haute.errors import ParseError
        from haute.routes._helpers import invalidate_pipeline_index

        invalidate_pipeline_index()
        from haute.server import app

        # Mock parse_pipeline_file to raise for a specific file
        original_parse = None

        def _patch_parse(f, **kw):
            if "test_pipeline" in str(f):
                return original_parse(f, **kw)
            raise ParseError("Simulated parse failure")

        from haute import parser
        from haute.routes import pipeline as pipeline_routes

        original_parse = parser.parse_pipeline_file

        # Create a second "pipeline" file that will fail
        (pipeline_dir / "bad_pipe.py").write_text(
            "import haute\npipeline = haute.Pipeline('bad_pipe')\n"
        )
        invalidate_pipeline_index()

        c = TestClient(app)
        # Patch the route-scoped binding -- after the #101 import hoist
        # ``list_pipelines`` calls its own top-level ``parse_pipeline_file``
        # alias, so patching the ``haute.parser`` source module alone no
        # longer affects the code path.
        with patch.object(pipeline_routes, "parse_pipeline_file", side_effect=_patch_parse):
            resp = c.get("/api/pipelines")
        assert resp.status_code == 200
        data = resp.json()
        bad = [p for p in data if p["name"] == "bad_pipe"]
        assert len(bad) == 1
        assert bad[0]["error"] is not None
        assert "Simulated parse failure" in bad[0]["error"]


class TestGetPipelineParseError:
    """Test that get_pipeline handles parse failures on the indexed file."""

    def test_indexed_file_unparseable(self, pipeline_dir: Path, monkeypatch: pytest.MonkeyPatch):
        """If the indexed file becomes unparseable, fall back to linear scan."""
        monkeypatch.chdir(pipeline_dir)
        from haute.server import app

        c = TestClient(app)
        # Corrupt the file after it's been indexed
        original_path = pipeline_dir / "test_pipeline.py"
        original_content = original_path.read_text()

        # First request indexes the file
        resp = c.get("/api/pipeline/test_pipeline")
        assert resp.status_code == 200

        # Corrupt the file
        original_path.write_text("def (\n")
        # Invalidate cache
        from haute.routes._helpers import invalidate_pipeline_index

        invalidate_pipeline_index()

        resp = c.get("/api/pipeline/test_pipeline")
        # Should be 404 since the file is now broken and no fallback matches
        assert resp.status_code == 404

        # Restore
        original_path.write_text(original_content)


class TestSinkEmptyGraph:
    """Sink with empty graph returns 400."""

    def test_sink_empty_graph(self, client: TestClient):
        resp = client.post(
            "/api/pipeline/write-output",
            json={
                "graph": {"nodes": [], "edges": []},
                "node_id": "x",
            },
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Phase 3: Server infrastructure tests
# ---------------------------------------------------------------------------


class TestClearBytecache:
    """Test _clear_bytecache removes __pycache__ dirs."""

    def test_removes_pycache_directories(self, tmp_path: Path):
        from unittest.mock import patch

        # Create a fake source tree with __pycache__
        fake_src = tmp_path / "haute"
        fake_src.mkdir()
        pycache = fake_src / "__pycache__"
        pycache.mkdir()
        (pycache / "foo.cpython-312.pyc").write_bytes(b"\x00")
        nested = fake_src / "routes" / "__pycache__"
        nested.mkdir(parents=True)

        # Patch Path(__file__).resolve().parent to point at our fake dir
        import haute.server as _srv

        with patch.object(_srv, "__file__", str(fake_src / "server.py")):
            _srv._clear_bytecache()

        assert not pycache.exists()
        assert not nested.exists()

    def test_handles_missing_pycache(self):
        """_clear_bytecache should not raise even when there are no __pycache__ dirs."""
        from haute.server import _clear_bytecache

        _clear_bytecache()  # should not raise


class TestMiddleware500:
    """Middleware dispatch method: exception path returns JSON 500."""

    def test_dispatch_exception_returns_json_500(self):
        """Directly test _RequestIdMiddleware.dispatch when call_next raises."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        from haute.server import _RequestIdMiddleware

        middleware = _RequestIdMiddleware(app=MagicMock())

        # Build a fake Request
        request = MagicMock()
        request.headers = {}
        request.method = "GET"
        request.url.path = "/api/test"

        # call_next that raises
        call_next = AsyncMock(side_effect=RuntimeError("boom"))

        loop = asyncio.new_event_loop()
        try:
            resp = loop.run_until_complete(middleware.dispatch(request, call_next))
        finally:
            loop.close()

        assert resp.status_code == 500
        import json

        body = json.loads(resp.body)
        assert body == {"detail": "Internal server error"}
        assert 1 <= len(resp.headers["x-request-id"]) <= 64

    def test_request_id_header_passthrough(self, client: TestClient):
        """Middleware adds x-request-id header to successful responses."""
        resp = client.get("/api/pipelines")
        assert resp.status_code == 200
        assert "x-request-id" in resp.headers

    def test_custom_request_id_preserved(self, client: TestClient):
        """Client-supplied x-request-id is preserved in the response."""
        resp = client.get("/api/pipelines", headers={"x-request-id": "custom-123"})
        assert resp.headers["x-request-id"] == "custom-123"

    @pytest.mark.parametrize(
        "request_id",
        [
            "contains a space",
            "x" * 65,
            "line\x1fseparator",
            "-cannot-start-with-punctuation",
        ],
    )
    def test_invalid_request_id_is_replaced_before_reflection(
        self,
        client: TestClient,
        request_id: str,
    ):
        """Untrusted correlation metadata never reaches a response header."""
        if "\x1f" in request_id:
            from haute.server import _select_request_id

            selected, rejection = _select_request_id(request_id)
            assert selected != request_id
            assert rejection is not None
            return

        resp = client.get("/api/pipelines", headers={"x-request-id": request_id})
        assert resp.status_code == 200
        assert resp.headers["x-request-id"] != request_id
        assert 1 <= len(resp.headers["x-request-id"]) <= 64

    @pytest.mark.parametrize("length", [1, 64])
    def test_request_id_ascii_token_boundaries_are_preserved(
        self,
        client: TestClient,
        length: int,
    ):
        request_id = "a" * length
        resp = client.get("/api/pipelines", headers={"x-request-id": request_id})
        assert resp.headers["x-request-id"] == request_id

    def test_rejected_request_id_log_does_not_contain_rejected_value(self):
        from haute.server import _select_request_id

        rejected = "secret-" + ("z" * 200)
        selected, rejection = _select_request_id(rejected)

        assert selected != rejected
        assert rejection == {"reason": "too_long", "length": len(rejected)}
        assert rejected not in repr(rejection)


class TestFileWatcherJsonConfig:
    """JSON config changes in config/ re-parse all pipelines."""

    def test_json_config_change_triggers_full_reparse(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        monkeypatch.chdir(pipeline_dir)

        # Create a config directory with a JSON file
        config_dir = pipeline_dir / "config"
        config_dir.mkdir(exist_ok=True)
        (config_dir / "factors").mkdir(parents=True)
        (config_dir / "factors" / "test.json").write_text('{"key": "value"}')

        json_file = str(config_dir / "factors" / "test.json")
        fake_changes = [(Change.modified, json_file)]

        async def _fake_awatch(*dirs, **kw):
            yield fake_changes

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict) -> None:
            broadcast_calls.append(data)

        with (
            patch("watchfiles.awatch", _fake_awatch),
            patch("haute.server.broadcast", _capture_broadcast),
            patch("haute.server.is_self_write", return_value=False),
            patch("haute.server.pipeline_dir", return_value=pipeline_dir),
            patch("haute.server._DEBOUNCE_SECONDS", 0),
        ):

            async def _run() -> None:
                await _run_file_watcher_and_drain()

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        # JSON config change should trigger a graph_update broadcast
        assert len(broadcast_calls) >= 1
        assert broadcast_calls[0]["type"] == "graph_update"

    def test_json_config_without_pipeline_no_broadcast(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """If config JSON changes but there are no pipelines, no broadcast."""
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        monkeypatch.chdir(tmp_path)

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "test.json").write_text("{}")

        fake_changes = [(Change.modified, str(config_dir / "test.json"))]

        async def _fake_awatch(*dirs, **kw):
            yield fake_changes

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict) -> None:
            broadcast_calls.append(data)

        with (
            patch("watchfiles.awatch", _fake_awatch),
            patch("haute.server.broadcast", _capture_broadcast),
            patch("haute.server.is_self_write", return_value=False),
            patch("haute.server.pipeline_dir", return_value=tmp_path),
            patch("haute.server._DEBOUNCE_SECONDS", 0),
        ):

            async def _run() -> None:
                await _run_file_watcher_and_drain()

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        assert len(broadcast_calls) == 0

    def test_json_config_change_batched_with_py_change_still_reparses_all_pipelines(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Config changes affect any pipeline, even when batched with a .py edit."""
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        monkeypatch.chdir(pipeline_dir)

        other_pipeline = pipeline_dir / "other_pipeline.py"
        other_pipeline.write_text(
            'import haute\n\npipeline = haute.Pipeline("other_pipeline", description="")\n'
        )

        config_dir = pipeline_dir / "config"
        config_dir.mkdir(exist_ok=True)
        config_file = config_dir / "rates.json"
        config_file.write_text('{"factor": 1.2}')

        fake_changes = [
            (Change.modified, str(config_file)),
            (Change.modified, str(pipeline_dir / "test_pipeline.py")),
        ]

        async def _fake_awatch(*dirs, **kw):
            yield fake_changes

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict) -> None:
            broadcast_calls.append(data)

        with (
            patch("watchfiles.awatch", _fake_awatch),
            patch("haute.server.broadcast", _capture_broadcast),
            patch("haute.server.is_self_write", return_value=False),
            patch("haute.server.pipeline_dir", return_value=pipeline_dir),
            patch(
                "haute.server.discover_pipelines",
                return_value=[
                    pipeline_dir / "test_pipeline.py",
                    other_pipeline,
                ],
            ),
            patch("haute.server._DEBOUNCE_SECONDS", 0),
        ):

            async def _run() -> None:
                await _run_file_watcher_and_drain()

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        source_files = {call["source_file"] for call in broadcast_calls}
        assert source_files == {"test_pipeline.py", "other_pipeline.py"}

    def test_json_config_change_reparses_when_pipeline_file_bytes_are_unchanged(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Config-only edits can change graph shape even when pipeline.py bytes match."""
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        from haute.server import _last_broadcast_fp

        monkeypatch.chdir(pipeline_dir)

        py_path = pipeline_dir / "test_pipeline.py"
        fp_key = str(py_path.resolve())
        config_dir = pipeline_dir / "config"
        config_dir.mkdir(exist_ok=True)
        config_file = config_dir / "rates.json"
        config_file.write_text('{"factor": 1.2}')
        fake_changes = [(Change.modified, str(config_file))]

        _last_broadcast_fp[fp_key] = hashlib.sha256(py_path.read_bytes()).hexdigest()

        class _FakeGraph:
            nodes = [object()]

            def model_dump(self) -> dict[str, object]:
                return {"nodes": [{"id": "after-config"}], "edges": []}

        async def _fake_awatch(*dirs, **kw):
            yield fake_changes

        parsed_paths: list[Path] = []

        def _fake_parse(path: Path):
            parsed_paths.append(path)
            return _FakeGraph()

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict) -> None:
            broadcast_calls.append(data)

        try:
            with (
                patch("watchfiles.awatch", _fake_awatch),
                patch("haute.server.broadcast", _capture_broadcast),
                patch("haute.server.is_self_write", return_value=False),
                patch("haute.server.pipeline_dir", return_value=pipeline_dir),
                patch("haute.server.discover_pipelines", return_value=[py_path]),
                patch("haute.server.parse_pipeline_to_graph", side_effect=_fake_parse),
                patch("haute.server._DEBOUNCE_SECONDS", 0),
            ):

                async def _run() -> None:
                    await _run_file_watcher_and_drain()

                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(_run())
                finally:
                    loop.close()
        finally:
            _last_broadcast_fp.pop(fp_key, None)

        graph_updates = [call for call in broadcast_calls if call["type"] == "graph_update"]
        assert parsed_paths == [py_path]
        assert len(graph_updates) == 1
        assert graph_updates[0]["graph"]["nodes"] == [{"id": "after-config"}]

    def test_json_config_change_rebroadcasts_even_when_graph_payload_is_unchanged(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Config bytes can affect execution even when the graph payload is unchanged."""
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        from haute.server import _last_broadcast_fp

        monkeypatch.chdir(pipeline_dir)

        py_path = pipeline_dir / "test_pipeline.py"
        fp_key = str(py_path.resolve())
        _last_broadcast_fp.pop(fp_key, None)

        config_dir = pipeline_dir / "config"
        config_dir.mkdir(exist_ok=True)
        config_file = config_dir / "rates.json"
        config_file.write_text('{"factor": 1.2}')

        class _FakeGraph:
            nodes = [object()]

            def model_dump(self) -> dict[str, object]:
                return {"nodes": [{"id": "same-graph"}], "edges": []}

        async def _fake_awatch(*dirs, **kw):
            config_file.write_text('{"factor": 1.3}')
            yield [(Change.modified, str(config_file))]
            await asyncio.sleep(0.02)
            config_file.write_text('{"factor": 1.4}')
            yield [(Change.modified, str(config_file))]

        parsed_paths: list[Path] = []

        def _fake_parse(path: Path):
            parsed_paths.append(path)
            return _FakeGraph()

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict) -> None:
            broadcast_calls.append(data)

        try:
            with (
                patch("watchfiles.awatch", _fake_awatch),
                patch("haute.server.broadcast", _capture_broadcast),
                patch("haute.server.is_self_write", return_value=False),
                patch("haute.server.pipeline_dir", return_value=pipeline_dir),
                patch("haute.server.discover_pipelines", return_value=[py_path]),
                patch("haute.server.parse_pipeline_to_graph", side_effect=_fake_parse),
                patch("haute.server._DEBOUNCE_SECONDS", 0),
            ):

                async def _run() -> None:
                    await _run_file_watcher_and_drain()

                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(_run())
                finally:
                    loop.close()
        finally:
            _last_broadcast_fp.pop(fp_key, None)

        graph_updates = [call for call in broadcast_calls if call["type"] == "graph_update"]
        assert parsed_paths == [py_path, py_path]
        assert len(graph_updates) == 2


class TestFileWatcherModuleChange:
    """Module .py changes in modules/ only re-parse importing pipelines."""

    def test_module_change_triggers_importing_pipeline(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        monkeypatch.chdir(pipeline_dir)

        # Create a modules directory
        modules_dir = pipeline_dir / "modules"
        modules_dir.mkdir()
        (modules_dir / "helper.py").write_text("def helper(): pass")

        # Mock pipelines_importing_module to return our test pipeline
        test_py = pipeline_dir / "test_pipeline.py"

        fake_changes = [(Change.modified, str(modules_dir / "helper.py"))]

        async def _fake_awatch(*dirs, **kw):
            yield fake_changes

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict) -> None:
            broadcast_calls.append(data)

        with (
            patch("watchfiles.awatch", _fake_awatch),
            patch("haute.server.broadcast", _capture_broadcast),
            patch("haute.server.is_self_write", return_value=False),
            patch("haute.server.pipelines_importing_module", return_value=[test_py]),
            patch("haute.server._DEBOUNCE_SECONDS", 0),
        ):

            async def _run() -> None:
                await _run_file_watcher_and_drain()

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        assert len(broadcast_calls) >= 1
        assert broadcast_calls[0]["type"] == "graph_update"

    def test_nested_module_change_triggers_importing_pipeline_not_module_parse(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """A nested module file is dependency code, not a standalone pipeline."""
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        monkeypatch.chdir(pipeline_dir)

        nested_module = pipeline_dir / "modules" / "pricing" / "helper.py"
        nested_module.parent.mkdir(parents=True)
        nested_module.write_text("def helper(): pass")
        test_py = pipeline_dir / "test_pipeline.py"

        fake_changes = [(Change.modified, str(nested_module))]

        async def _fake_awatch(*dirs, **kw):
            yield fake_changes

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict) -> None:
            broadcast_calls.append(data)

        parsed_paths: list[Path] = []
        from haute.server import parse_pipeline_to_graph as _real_parse

        def _tracking_parse(path: Path):
            parsed_paths.append(path)
            return _real_parse(path)

        with (
            patch("watchfiles.awatch", _fake_awatch),
            patch("haute.server.broadcast", _capture_broadcast),
            patch("haute.server.is_self_write", return_value=False),
            patch("haute.server.pipelines_importing_module", return_value=[test_py]),
            patch("haute.server.parse_pipeline_to_graph", side_effect=_tracking_parse),
            patch("haute.server._DEBOUNCE_SECONDS", 0),
        ):

            async def _run() -> None:
                await _run_file_watcher_and_drain()

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        assert parsed_paths == [test_py]
        assert [call["source_file"] for call in broadcast_calls] == ["test_pipeline.py"]

    def test_module_change_reparses_when_pipeline_file_bytes_are_unchanged(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Module-only edits must not be deduped by the importing file's byte hash."""
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        from haute.server import _last_broadcast_fp

        monkeypatch.chdir(pipeline_dir)

        modules_dir = pipeline_dir / "modules"
        modules_dir.mkdir(exist_ok=True)
        module_path = modules_dir / "helper.py"
        module_path.write_text("def helper(): return 42")

        test_py = pipeline_dir / "test_pipeline.py"
        fp_key = str(test_py.resolve())
        _last_broadcast_fp[fp_key] = hashlib.sha256(test_py.read_bytes()).hexdigest()

        fake_changes = [(Change.modified, str(module_path))]

        class _FakeGraph:
            nodes = [object()]

            def model_dump(self) -> dict[str, object]:
                return {"nodes": [{"id": "after-module"}], "edges": []}

        async def _fake_awatch(*dirs, **kw):
            yield fake_changes

        parsed_paths: list[Path] = []

        def _fake_parse(path: Path):
            parsed_paths.append(path)
            return _FakeGraph()

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict) -> None:
            broadcast_calls.append(data)

        try:
            with (
                patch("watchfiles.awatch", _fake_awatch),
                patch("haute.server.broadcast", _capture_broadcast),
                patch("haute.server.is_self_write", return_value=False),
                patch("haute.server.pipelines_importing_module", return_value=[test_py]),
                patch("haute.server.parse_pipeline_to_graph", side_effect=_fake_parse),
                patch("haute.server._DEBOUNCE_SECONDS", 0),
            ):

                async def _run() -> None:
                    await _run_file_watcher_and_drain()

                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(_run())
                finally:
                    loop.close()
        finally:
            _last_broadcast_fp.pop(fp_key, None)

        graph_updates = [call for call in broadcast_calls if call["type"] == "graph_update"]
        assert parsed_paths == [test_py]
        assert len(graph_updates) == 1
        assert graph_updates[0]["graph"]["nodes"] == [{"id": "after-module"}]

    def test_module_change_rebroadcasts_even_when_graph_payload_is_unchanged(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Module bytes can affect execution even when the graph payload is unchanged."""
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        from haute.server import _last_broadcast_fp

        monkeypatch.chdir(pipeline_dir)

        modules_dir = pipeline_dir / "modules"
        modules_dir.mkdir(exist_ok=True)
        module_path = modules_dir / "helper.py"
        module_path.write_text("def helper(): return 1")

        test_py = pipeline_dir / "test_pipeline.py"
        fp_key = str(test_py.resolve())
        _last_broadcast_fp.pop(fp_key, None)

        class _FakeGraph:
            nodes = [object()]

            def model_dump(self) -> dict[str, object]:
                return {"nodes": [{"id": "same-graph"}], "edges": []}

        async def _fake_awatch(*dirs, **kw):
            module_path.write_text("def helper(): return 2")
            yield [(Change.modified, str(module_path))]
            await asyncio.sleep(0.02)
            module_path.write_text("def helper(): return 3")
            yield [(Change.modified, str(module_path))]

        parsed_paths: list[Path] = []

        def _fake_parse(path: Path):
            parsed_paths.append(path)
            return _FakeGraph()

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict) -> None:
            broadcast_calls.append(data)

        try:
            with (
                patch("watchfiles.awatch", _fake_awatch),
                patch("haute.server.broadcast", _capture_broadcast),
                patch("haute.server.is_self_write", return_value=False),
                patch("haute.server.pipelines_importing_module", return_value=[test_py]),
                patch("haute.server.parse_pipeline_to_graph", side_effect=_fake_parse),
                patch("haute.server._DEBOUNCE_SECONDS", 0),
            ):

                async def _run() -> None:
                    await _run_file_watcher_and_drain()

                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(_run())
                finally:
                    loop.close()
        finally:
            _last_broadcast_fp.pop(fp_key, None)

        graph_updates = [call for call in broadcast_calls if call["type"] == "graph_update"]
        assert parsed_paths == [test_py, test_py]
        assert len(graph_updates) == 2


class TestFileWatcherParseError:
    """Parse error broadcasts a parse_error message."""

    def test_parse_error_broadcasts_error(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        monkeypatch.chdir(pipeline_dir)

        py_file = str(pipeline_dir / "test_pipeline.py")
        fake_changes = [(Change.modified, py_file)]

        async def _fake_awatch(*dirs, **kw):
            yield fake_changes

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict) -> None:
            broadcast_calls.append(data)

        with (
            patch("watchfiles.awatch", _fake_awatch),
            patch("haute.server.broadcast", _capture_broadcast),
            patch("haute.server.is_self_write", return_value=False),
            patch(
                "haute.server.parse_pipeline_to_graph",
                side_effect=SyntaxError("bad syntax"),
            ),
            patch("haute.server._DEBOUNCE_SECONDS", 0),
        ):

            async def _run() -> None:
                await _run_file_watcher_and_drain()

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        assert len(broadcast_calls) >= 1
        assert broadcast_calls[0]["type"] == "parse_error"
        assert "bad syntax" in broadcast_calls[0]["error"]

    def test_parse_error_then_success_recovers_without_restart(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """A fixed file should recover inside the same watcher lifetime."""
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        monkeypatch.chdir(pipeline_dir)

        py_file = str(pipeline_dir / "test_pipeline.py")
        from haute.server import parse_pipeline_to_graph as _real_parse

        async def _fake_awatch(*dirs, **kw):
            yield [(Change.modified, py_file)]
            await asyncio.sleep(0.02)
            yield [(Change.modified, py_file)]

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict) -> None:
            broadcast_calls.append(data)

        failed = {"value": False}

        def _flaky_parse(path: Path):
            if not failed["value"]:
                failed["value"] = True
                raise SyntaxError("bad syntax")
            return _real_parse(path)

        with (
            patch("watchfiles.awatch", _fake_awatch),
            patch("haute.server.broadcast", _capture_broadcast),
            patch("haute.server.is_self_write", return_value=False),
            patch("haute.server.parse_pipeline_to_graph", side_effect=_flaky_parse),
            patch("haute.server._DEBOUNCE_SECONDS", 0),
        ):

            async def _run() -> None:
                await _run_file_watcher_and_drain()

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        event_types = [call["type"] for call in broadcast_calls]
        assert "parse_error" in event_types
        assert "graph_update" in event_types
        assert event_types.index("parse_error") < event_types.index("graph_update")

    def test_reverting_to_last_good_file_after_parse_error_rebroadcasts_graph(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """A parse-error banner must clear when the user reverts to the last good bytes."""
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        from haute.server import _last_broadcast_fp
        from haute.server import parse_pipeline_to_graph as _real_parse

        monkeypatch.chdir(pipeline_dir)

        py_path = pipeline_dir / "test_pipeline.py"
        good_bytes = py_path.read_bytes()
        fp_key = str(py_path.resolve())
        _last_broadcast_fp[fp_key] = hashlib.sha256(good_bytes).hexdigest()

        async def _fake_awatch(*dirs, **kw):
            py_path.write_text("this is not valid python syntax !!!")
            yield [(Change.modified, str(py_path))]
            await asyncio.sleep(0.02)
            py_path.write_bytes(good_bytes)
            yield [(Change.modified, str(py_path))]

        def _parse_or_raise(path: Path):
            if path.read_bytes() != good_bytes:
                raise SyntaxError("bad syntax")
            return _real_parse(path)

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict) -> None:
            broadcast_calls.append(data)

        with (
            patch("watchfiles.awatch", _fake_awatch),
            patch("haute.server.broadcast", _capture_broadcast),
            patch("haute.server.is_self_write", return_value=False),
            patch("haute.server.parse_pipeline_to_graph", side_effect=_parse_or_raise),
            patch("haute.server._DEBOUNCE_SECONDS", 0),
        ):

            async def _run() -> None:
                await _run_file_watcher_and_drain()

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        event_types = [call["type"] for call in broadcast_calls]
        assert event_types == ["parse_error", "graph_update"]


class TestFileWatcherFingerprintDedup:
    """Unchanged graph fingerprint skips re-broadcast."""

    def test_same_fingerprint_skips_second_broadcast(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        monkeypatch.chdir(pipeline_dir)

        py_file = str(pipeline_dir / "test_pipeline.py")

        async def _fake_awatch(*dirs, **kw):
            yield [(Change.modified, py_file)]
            # Allow first flush to complete before yielding second
            await asyncio.sleep(0)
            yield [(Change.modified, py_file)]

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict) -> None:
            broadcast_calls.append(data)

        # Pre-clear the fingerprint cache
        from haute.server import _last_broadcast_fp

        _last_broadcast_fp.clear()

        with (
            patch("watchfiles.awatch", _fake_awatch),
            patch("haute.server.broadcast", _capture_broadcast),
            patch("haute.server.is_self_write", return_value=False),
            patch("haute.server._DEBOUNCE_SECONDS", 0),
        ):

            async def _run() -> None:
                await _run_file_watcher_and_drain()

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        # First change broadcasts, second with same fingerprint is skipped
        graph_updates = [c for c in broadcast_calls if c["type"] == "graph_update"]
        assert len(graph_updates) == 1


class TestFileWatcherRecovery:
    def test_crashed_watcher_cancels_pending_debounce_flush(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A crashed watcher must not leave a stale debounce flush running."""
        from haute.server import _file_watcher

        monkeypatch.chdir(pipeline_dir)
        py_file = str(pipeline_dir / "test_pipeline.py")
        published: list[tuple[str, dict[str, object]]] = []

        class _FakeGraph:
            nodes: list[object] = []

            def model_dump(self) -> dict[str, object]:
                return {"nodes": [], "edges": []}

        async def _crashy_awatch(*dirs, **kw):
            yield [(Change.modified, py_file)]
            raise RuntimeError("awatch boom")

        class _BusStub:
            def publish(self, event: str, payload: dict[str, object]) -> None:
                published.append((event, payload))

        async def _run() -> None:
            with (
                patch("watchfiles.awatch", _crashy_awatch),
                patch("haute.server.parse_pipeline_to_graph", return_value=_FakeGraph()),
                patch("haute.server.default_bus", _BusStub()),
                patch("haute.server.is_self_write", return_value=False),
                patch("haute.server._DEBOUNCE_SECONDS", 0.05),
            ):
                with pytest.raises(RuntimeError, match="awatch boom"):
                    await _file_watcher()
                await asyncio.sleep(0.1)

        asyncio.run(_run())

        assert published == []

    def test_flush_error_requeues_same_batch_without_new_event(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A failed flush should retry the current batch even without a second edit."""
        from haute.server import _file_watcher

        monkeypatch.chdir(pipeline_dir)
        py_file = str(pipeline_dir / "test_pipeline.py")
        published: list[tuple[str, dict[str, object]]] = []

        class _BusStub:
            def publish(self, event: str, payload: dict[str, object]) -> None:
                published.append((event, payload))

        async def _single_change_awatch(*dirs, **kw):
            yield [(Change.modified, py_file)]
            await asyncio.sleep(0.02)

        async def _run() -> None:
            with (
                patch("watchfiles.awatch", _single_change_awatch),
                patch("haute.server.default_bus", _BusStub()),
                patch("haute.server.is_self_write", return_value=False),
                patch(
                    "haute.server.invalidate_pipeline_index",
                    side_effect=[RuntimeError("cache boom"), None],
                ),
                patch("haute.server._DEBOUNCE_SECONDS", 0),
            ):
                await _file_watcher()

        asyncio.run(_run())

        assert any(event == "graph.update" for event, _ in published)

    def test_module_only_flush_does_not_invalidate_pipeline_index(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A dependency edit changes graphs, not the pipeline name-to-path index."""
        from haute.server import _file_watcher

        monkeypatch.chdir(pipeline_dir)
        modules_dir = pipeline_dir / "modules"
        modules_dir.mkdir()
        module_file = modules_dir / "shared.py"
        module_file.write_text("VALUE = 2\n")

        async def _single_module_change(*dirs, **kw):
            yield [(Change.modified, str(module_file))]

        async def _run() -> None:
            with (
                patch("watchfiles.awatch", _single_module_change),
                patch("haute.server.is_self_write", return_value=False),
                patch("haute.server.pipelines_importing_module", return_value=[]),
                patch("haute.server.invalidate_pipeline_index") as invalidate,
                patch("haute.server._DEBOUNCE_SECONDS", 0),
            ):
                await _file_watcher()
                assert invalidate.call_count == 0

        asyncio.run(_run())

    def test_deleted_pipeline_invalidates_index_without_reparse(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A deleted pipeline must invalidate the index without a parse attempt."""
        from haute.server import _file_watcher

        monkeypatch.chdir(pipeline_dir)
        deleted_file = pipeline_dir / "deleted_pipeline.py"

        async def _single_deletion(*dirs, **kw):
            yield [(Change.deleted, str(deleted_file))]

        async def _run() -> None:
            with (
                patch("watchfiles.awatch", _single_deletion),
                patch("haute.server.is_self_write", return_value=False),
                patch("haute.server._known_pipeline_paths", return_value={}),
                patch("haute.server.invalidate_pipeline_index") as invalidate,
                patch("haute.server.parse_pipeline_to_graph") as parse,
                patch("haute.server._DEBOUNCE_SECONDS", 0),
            ):
                await _file_watcher()
                invalidate.assert_called_once_with()
                parse.assert_not_called()

        asyncio.run(_run())

    def test_cancelled_flush_requeues_snapshotted_batch(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Cancellation after snapshotting must not discard the interrupted change."""
        from haute.server import _file_watcher

        monkeypatch.chdir(pipeline_dir)
        interrupted_file = pipeline_dir / "interrupted.py"
        later_file = pipeline_dir / "later.py"
        interrupted_file.write_text("interrupted = True\n")
        later_file.write_text("later = True\n")
        known = {
            str(interrupted_file.resolve()): interrupted_file,
            str(later_file.resolve()): later_file,
        }

        class _FakeGraph:
            nodes: list[object] = []

            def model_dump(self) -> dict[str, object]:
                return {"nodes": [], "edges": []}

        class _BusStub:
            def publish(self, event: str, payload: dict[str, object]) -> None:
                del event, payload

        async def _run() -> list[Path]:
            cancellation_injected = asyncio.Event()
            original_read_bytes = Path.read_bytes
            interrupted_reads = 0

            def _read_bytes(path: Path) -> bytes:
                nonlocal interrupted_reads
                if path.resolve() == interrupted_file.resolve():
                    interrupted_reads += 1
                    if interrupted_reads == 1:
                        cancellation_injected.set()
                        raise asyncio.CancelledError
                return original_read_bytes(path)

            async def _two_changes(*dirs, **kw):
                del dirs, kw
                yield [(Change.modified, str(interrupted_file))]
                await cancellation_injected.wait()
                await asyncio.sleep(0)
                yield [(Change.modified, str(later_file))]

            with (
                patch("watchfiles.awatch", _two_changes),
                patch.object(Path, "read_bytes", _read_bytes),
                patch("haute.server.is_self_write", return_value=False),
                patch("haute.server._known_pipeline_paths", return_value=known),
                patch("haute.server._discovered_pipeline_paths", return_value=known),
                patch("haute.server.invalidate_pipeline_index"),
                patch(
                    "haute.server.parse_pipeline_to_graph",
                    return_value=_FakeGraph(),
                ) as parse,
                patch("haute.server.default_bus", _BusStub()),
                patch("haute.server._DEBOUNCE_SECONDS", 0),
            ):
                await _file_watcher()
                return [call.args[0] for call in parse.call_args_list]

        assert set(asyncio.run(_run())) == {interrupted_file, later_file}

    def test_persistently_failing_flush_stops_after_bounded_retries(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A poisoned change gets bounded batch retries plus one isolated attempt."""
        from haute.server import _file_watcher

        monkeypatch.chdir(pipeline_dir)
        py_file = str(pipeline_dir / "test_pipeline.py")

        async def _single_change(*dirs, **kw):
            yield [(Change.modified, py_file)]

        async def _run() -> int:
            with (
                patch("watchfiles.awatch", _single_change),
                patch("haute.server.is_self_write", return_value=False),
                patch(
                    "haute.server.invalidate_pipeline_index",
                    side_effect=RuntimeError("persistent cache failure"),
                ) as invalidate,
                patch("haute.server._DEBOUNCE_SECONDS", 0),
                patch("haute.server._WATCHER_FLUSH_RETRY_BASE_SECONDS", 0),
                patch("haute.server._WATCHER_FLUSH_MAX_RETRIES", 2),
            ):
                await _file_watcher()
                return invalidate.call_count

        assert asyncio.run(_run()) == 4

    def test_retry_exhaustion_isolates_poisoned_change_and_processes_healthy_change(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """One bad event must not remain queued or suppress a healthy sibling forever."""
        from haute.server import _file_watcher

        monkeypatch.chdir(pipeline_dir)
        healthy_file = pipeline_dir / "test_pipeline.py"
        poisoned_file = pipeline_dir / "poisoned.py"
        published: list[tuple[str, dict[str, object]]] = []

        class _FakeGraph:
            nodes: list[object] = []

            def model_dump(self) -> dict[str, object]:
                return {"nodes": [], "edges": []}

        class _BusStub:
            def publish(self, event: str, payload: dict[str, object]) -> None:
                published.append((event, payload))

        def _self_write(path: Path, *, consume: bool) -> bool:
            del consume
            if path.name == poisoned_file.name:
                raise RuntimeError("poisoned watcher event")
            return False

        async def _mixed_batch(*dirs, **kw):
            yield [
                (Change.modified, str(poisoned_file)),
                (Change.modified, str(healthy_file)),
            ]

        known = {
            str(poisoned_file.resolve()): poisoned_file,
            str(healthy_file.resolve()): healthy_file,
        }

        async def _run() -> None:
            with (
                patch("watchfiles.awatch", _mixed_batch),
                patch("haute.server.is_self_write", _self_write),
                patch("haute.server._known_pipeline_paths", return_value=known),
                patch("haute.server._discovered_pipeline_paths", return_value=known),
                patch("haute.server.invalidate_pipeline_index"),
                patch("haute.server.parse_pipeline_to_graph", return_value=_FakeGraph()),
                patch("haute.server.default_bus", _BusStub()),
                patch("haute.server._DEBOUNCE_SECONDS", 0),
                patch("haute.server._WATCHER_FLUSH_RETRY_BASE_SECONDS", 0),
                patch("haute.server._WATCHER_FLUSH_MAX_RETRIES", 1),
            ):
                await _file_watcher()

        asyncio.run(_run())

        assert [event for event, _ in published] == ["graph.update"]

    def test_flush_error_recovery_allows_later_change(
        self,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """One unexpected flush failure should not kill later watcher work."""
        import asyncio
        from unittest.mock import patch

        from watchfiles import Change

        monkeypatch.chdir(pipeline_dir)

        py_file = str(pipeline_dir / "test_pipeline.py")

        async def _fake_awatch(*dirs, **kw):
            yield [(Change.modified, py_file)]
            await asyncio.sleep(0.02)
            yield [(Change.modified, py_file)]

        broadcast_calls: list[dict] = []

        async def _capture_broadcast(data: dict) -> None:
            broadcast_calls.append(data)

        with (
            patch("watchfiles.awatch", _fake_awatch),
            patch("haute.server.broadcast", _capture_broadcast),
            patch("haute.server.is_self_write", return_value=False),
            patch(
                "haute.server.invalidate_pipeline_index",
                side_effect=[RuntimeError("cache boom"), None],
            ),
            patch("haute.server._DEBOUNCE_SECONDS", 0),
        ):

            async def _run() -> None:
                await _run_file_watcher_and_drain()

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        assert any(call["type"] == "graph_update" for call in broadcast_calls)

    def test_watcher_forever_restarts_after_crash(self):
        """The outer watcher loop should restart after an unexpected crash."""
        from haute.server import _watcher_forever

        async def _run() -> None:
            restarted = asyncio.Event()
            calls: list[str] = []

            async def _flaky_file_watcher() -> None:
                calls.append("call")
                if len(calls) == 1:
                    raise RuntimeError("watcher boom")
                restarted.set()
                await asyncio.Future()

            with (
                patch("haute.server._file_watcher", _flaky_file_watcher),
                patch("haute.server._WATCHER_RESTART_DELAY_SECONDS", 0),
            ):
                task = asyncio.create_task(_watcher_forever())
                try:
                    await asyncio.wait_for(restarted.wait(), timeout=1.0)
                finally:
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task

            assert len(calls) >= 2

        asyncio.run(_run())

    def test_watcher_forever_returns_when_file_watcher_exits_normally(self) -> None:
        """A clean watcher shutdown should not spin the restart loop."""
        from haute.server import _watcher_forever

        calls = 0

        async def _clean_file_watcher() -> None:
            nonlocal calls
            calls += 1

        with patch("haute.server._file_watcher", _clean_file_watcher):
            asyncio.run(_watcher_forever())

        assert calls == 1

    def test_file_watcher_returns_when_watchfiles_is_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Without watchfiles, live sync is disabled without crashing startup."""
        from haute.server import _file_watcher

        monkeypatch.chdir(tmp_path)

        with patch.dict(sys.modules, {"watchfiles": None}):
            asyncio.run(_file_watcher())


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

        graph = parse_pipeline_file(pipeline_dir / "test_pipeline.py")
        graph_dict = graph.model_dump()

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
            },
        )
        assert resp.status_code == 200
        data = resp.json()

        # Verify the submodel node was created
        sm_node = next(n for n in data["graph"]["nodes"] if n["id"] == "submodel__output_test")
        config = sm_node["data"]["config"]
        # childNodeIds should match selected
        assert set(config["childNodeIds"]) == set(selected)


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
import polars as pl
import haute

pipeline = haute.Pipeline("rewire_test", description="Rewire test")

@pipeline.data_input(config="{source_config}")
def source() -> pl.LazyFrame:
    return pl.scan_parquet("data/input.parquet")

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

        graph = parse_pipeline_file(pipeline_dir / "rewire_test.py")
        graph_dict = graph.model_dump()

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
            },
        )
        assert resp.status_code == 200
        data = resp.json()

        # Parent graph should have: submodel__inner + final
        parent_ids = {n["id"] for n in data["graph"]["nodes"]}
        assert "submodel__inner" in parent_ids
        assert "final" in parent_ids
        assert "source" not in parent_ids
        assert "middle" not in parent_ids

        # There should be a rewired edge from submodel__inner -> final
        parent_edges = data["graph"]["edges"]
        outgoing = [e for e in parent_edges if e["source"] == "submodel__inner"]
        assert len(outgoing) >= 1
        assert any(e["target"] == "final" for e in outgoing)
        # The outgoing edge should have a sourceHandle referencing "middle"
        assert any("middle" in (e.get("sourceHandle") or "") for e in outgoing)


# ---------------------------------------------------------------------------
# RequestIdMiddleware logging levels
# ---------------------------------------------------------------------------


class TestMiddlewareLogging:
    @pytest.mark.parametrize(
        ("url", "expected_status", "event_name", "log_level"),
        [
            ("/api/pipelines", 200, "request_ok", "info"),
            ("/api/pipeline/nonexistent_pipeline_xyz", 404, "request_client_error", "warning"),
        ],
        ids=["2xx_logs_info", "4xx_logs_warning"],
    )
    def test_status_logs_correct_level(
        self,
        client: TestClient,
        pipeline_dir: Path,
        url: str,
        expected_status: int,
        event_name: str,
        log_level: str,
    ):
        import structlog.testing

        with structlog.testing.capture_logs() as captured:
            resp = client.get(url)

        assert resp.status_code == expected_status
        matching = [e for e in captured if e.get("event") == event_name]
        assert len(matching) >= 1
        assert matching[0]["status"] == expected_status
        assert matching[0]["log_level"] == log_level

    def test_5xx_logs_error(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        import structlog.testing

        from haute.server import _RequestIdMiddleware

        middleware = _RequestIdMiddleware(app=MagicMock())
        request = MagicMock()
        request.headers = {}
        request.method = "POST"
        request.url.path = "/api/test"

        call_next = AsyncMock(side_effect=RuntimeError("boom"))

        loop = asyncio.new_event_loop()
        try:
            with structlog.testing.capture_logs() as captured:
                resp = loop.run_until_complete(middleware.dispatch(request, call_next))
        finally:
            loop.close()

        assert resp.status_code == 500
        error_events = [e for e in captured if e.get("event") == "unhandled_exception"]
        assert len(error_events) >= 1
        assert "traceback" in error_events[0]
        assert error_events[0]["log_level"] == "error"

    def test_500_response_no_stack_trace(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        from haute.server import _RequestIdMiddleware

        middleware = _RequestIdMiddleware(app=MagicMock())
        request = MagicMock()
        request.headers = {}
        request.method = "GET"
        request.url.path = "/api/explode"

        call_next = AsyncMock(side_effect=ValueError("secret internal detail"))

        loop = asyncio.new_event_loop()
        try:
            resp = loop.run_until_complete(middleware.dispatch(request, call_next))
        finally:
            loop.close()

        assert resp.status_code == 500
        body = json.loads(resp.body)
        assert body == {"detail": "Internal server error"}
        assert "secret internal detail" not in resp.body.decode()
        assert "Traceback" not in resp.body.decode()


# ---------------------------------------------------------------------------
# WebSocket keep-alive
# ---------------------------------------------------------------------------


class TestWebSocketKeepAlive:
    def test_keep_alive_messages_accepted(self, client: TestClient):
        with client.websocket_connect(
            "/ws/sync",
            headers={"origin": "http://localhost"},
        ) as ws:
            ws.send_text("keep-alive")
            ws.send_text("ping")
            ws.send_text("")

    def test_dead_client_removed_from_set(self, client: TestClient):
        from haute.routes._helpers import ws_clients

        with client.websocket_connect(
            "/ws/sync",
            headers={"origin": "http://localhost"},
        ):
            assert len(ws_clients) >= 1
        assert len(ws_clients) == 0


# ---------------------------------------------------------------------------
# validate_safe_path unit tests
# ---------------------------------------------------------------------------


class TestValidateSafePath:
    def test_valid_path_succeeds(self, tmp_path: Path):
        from haute.routes._helpers import validate_safe_path

        result = validate_safe_path(tmp_path, "subdir/file.txt")
        assert result == (tmp_path / "subdir" / "file.txt").resolve()

    def test_traversal_raises_403(self, tmp_path: Path):
        from haute.routes._helpers import validate_safe_path

        with pytest.raises(HTTPException) as exc_info:
            validate_safe_path(tmp_path, "../../etc/passwd")
        assert exc_info.value.status_code == 403

    def test_symlink_escape_raises_403(self, tmp_path: Path):
        import os

        from haute.routes._helpers import validate_safe_path

        outside = tmp_path.parent / "outside_target"
        outside.mkdir(exist_ok=True)
        link = tmp_path / "escape_link"
        try:
            os.symlink(outside, link)
        except OSError:
            pytest.skip("symlink creation not supported")
        with pytest.raises(HTTPException) as exc_info:
            validate_safe_path(tmp_path, "escape_link/secret.txt")
        assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# broadcast sends to all WebSocket clients
# ---------------------------------------------------------------------------


class TestBroadcastMultipleClients:
    def test_broadcast_sends_to_all_clients(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        from haute.routes._helpers import broadcast, ws_clients

        ws1 = MagicMock()
        ws1.send_text = AsyncMock()
        ws2 = MagicMock()
        ws2.send_text = AsyncMock()

        ws_clients.add(ws1)
        ws_clients.add(ws2)
        try:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(broadcast({"type": "test", "data": 42}))
            finally:
                loop.close()

            ws1.send_text.assert_called_once()
            ws2.send_text.assert_called_once()
            payload1 = json.loads(ws1.send_text.call_args[0][0])
            payload2 = json.loads(ws2.send_text.call_args[0][0])
            assert payload1 == {"type": "test", "data": 42}
            assert payload2 == {"type": "test", "data": 42}
        finally:
            ws_clients.discard(ws1)
            ws_clients.discard(ws2)


# ---------------------------------------------------------------------------
# load_sidecar unit tests
# ---------------------------------------------------------------------------


class TestLoadSidecar:
    def test_valid_json_returns_dict(self, tmp_path: Path):
        from haute.routes._helpers import load_sidecar

        py_path = tmp_path / "pipeline.py"
        py_path.write_text("")
        sidecar = py_path.with_suffix(".haute.json")
        sidecar.write_text(json.dumps({"positions": {"a": {"x": 1, "y": 2}}}))

        result = load_sidecar(py_path)
        assert result == {"positions": {"a": {"x": 1, "y": 2}}}

    def test_missing_file_returns_empty(self, tmp_path: Path):
        from haute.routes._helpers import load_sidecar

        py_path = tmp_path / "nonexistent.py"
        result = load_sidecar(py_path)
        assert result == {}

    def test_corrupt_json_returns_empty(self, tmp_path: Path):
        from haute.routes._helpers import load_sidecar

        py_path = tmp_path / "pipeline.py"
        py_path.write_text("")
        sidecar = py_path.with_suffix(".haute.json")
        sidecar.write_text("{invalid json content!!!")

        result = load_sidecar(py_path)
        assert result == {}


# ---------------------------------------------------------------------------
# mark_self_write and is_self_write timing
# ---------------------------------------------------------------------------


class TestSelfWriteTiming:
    def test_is_self_write_false_before_mark(self, monkeypatch: pytest.MonkeyPatch):
        import time as _time

        import haute.routes._helpers as helpers

        fake_time = [1000.0]
        monkeypatch.setattr(_time, "monotonic", lambda: fake_time[0])
        helpers._last_self_write = 0.0
        assert helpers.is_self_write() is False

    def test_is_self_write_true_within_cooldown(self, monkeypatch: pytest.MonkeyPatch):
        import time as _time

        import haute.routes._helpers as helpers

        fake_time = [100.0]
        monkeypatch.setattr(_time, "monotonic", lambda: fake_time[0])
        helpers.mark_self_write()
        fake_time[0] = 101.5  # 1.5s < 2.0s cooldown
        assert helpers.is_self_write() is True

    def test_is_self_write_false_after_cooldown(self, monkeypatch: pytest.MonkeyPatch):
        import time as _time

        import haute.routes._helpers as helpers

        fake_time = [100.0]
        monkeypatch.setattr(_time, "monotonic", lambda: fake_time[0])
        helpers.mark_self_write()
        fake_time[0] = 103.0  # 3.0s > 2.0s cooldown
        assert helpers.is_self_write() is False


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
            },
        )
        assert create_resp.status_code == 200

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
        resp = client.get("/api/submodel/positioned")
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
            },
        )
        assert create_resp.status_code == 200
        updated_graph = create_resp.json()["graph"]

        resp = client.post(
            "/api/submodel/dissolve",
            json={
                "submodel_name": "will_dissolve",
                "graph": updated_graph,
                "source_file": "",
                "pipeline_name": "test_pipeline",
            },
        )
        assert resp.status_code == 400
        assert "source_file" in resp.json()["detail"]


class TestStaticBuildReady:
    """``static_build_ready`` gates both static mounting and ``haute serve``."""

    def test_missing_dir_is_not_ready(self, tmp_path: Path) -> None:
        from haute.server import static_build_ready

        assert not static_build_ready(tmp_path / "nonexistent")

    def test_empty_dir_is_not_ready(self, tmp_path: Path) -> None:
        """A bare directory (fresh worktree, interrupted build) must not count."""
        from haute.server import static_build_ready

        static = tmp_path / "static"
        static.mkdir()
        assert not static_build_ready(static)

    def test_index_without_assets_is_not_ready(self, tmp_path: Path) -> None:
        """Mounting a missing assets/ raises at import -- require it up front."""
        from haute.server import static_build_ready

        static = tmp_path / "static"
        static.mkdir()
        (static / "index.html").write_text("<html></html>")
        assert not static_build_ready(static)

    def test_complete_build_is_ready(self, tmp_path: Path) -> None:
        from haute.server import static_build_ready

        static = tmp_path / "static"
        static.mkdir()
        (static / "index.html").write_text("<html></html>")
        (static / "assets").mkdir()
        assert static_build_ready(static)
