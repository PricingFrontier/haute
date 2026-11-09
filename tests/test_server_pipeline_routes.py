"""The server's pipeline, preview, trace, save, sink, file and schema routes.

``/api/pipelines`` and ``/api/pipeline`` list and load pipelines (keeping broken ones
discoverable), preview and trace run nodes, save writes once per operation, the sink writes
outputs, files and schemas are browsed, and the routes map timeouts, parse errors and exceptions to
their HTTP statuses.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import patch

import polars as pl
import pytest
from fastapi.testclient import TestClient

from tests import _server_helpers
from tests._project import project_app, use_project
from tests._server_helpers import (
    DOCUMENT_FINGERPRINT_HEADER,
    _file_input_config,
)
from tests.conftest import (
    current_source_revision,
    make_ready_file_input_config,
)

# Fixtures shared with the other modules split from one suite.
client = _server_helpers.client
pipeline_dir = _server_helpers.pipeline_dir


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _file_output_config(path: str) -> dict:
    return {
        "output_type": "file",
        "format": "parquet",
        "mode": "sink",
        "path": path,
        "arguments": {},
    }


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
    def test_names_the_returned_document_fingerprint(self, client: TestClient):
        from haute._pipeline_recovery import pipeline_document_fingerprint

        resp = client.get("/api/pipeline")

        assert resp.status_code == 200
        assert resp.headers[DOCUMENT_FINGERPRINT_HEADER] == pipeline_document_fingerprint(
            resp.json()
        )

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
        use_project(tmp_path)
        app = project_app()

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
        use_project(tmp_path)
        app = project_app()

        c = TestClient(app)
        resp = c.get("/api/pipeline")
        assert resp.status_code == 200
        graph = resp.json()
        assert graph["nodes"] == []
        assert graph["source_file"] == "main.py"
        assert graph["pipeline_name"] == "my_project"

    def test_readable_parse_failure_returns_source_only_instead_of_blank_canvas(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A broken discovered pipeline must never look like a new empty project."""
        broken = tmp_path / "broken.py"
        broken.write_text(
            'import haute\npipeline = haute.Pipeline("broken")\ndef invalid(:\n    pass\n',
            encoding="utf-8",
        )
        use_project(tmp_path)

        app = project_app()

        with patch(
            "haute.routes.pipeline.discover_pipelines",
            return_value=[broken],
        ):
            resp = TestClient(app).get("/api/pipeline")

        assert resp.status_code == 200
        assert resp.json()["load_status"] == "source_only"
        assert resp.json()["source_text"] == broken.read_text(encoding="utf-8")
        assert resp.json()["nodes"] == []


# ---------------------------------------------------------------------------
# GET /api/pipeline/{name}
# ---------------------------------------------------------------------------


class TestGetPipelineByName:
    def test_found(self, client: TestClient):
        resp = client.get("/api/pipeline/test_pipeline")
        assert resp.status_code == 200
        graph = resp.json()
        assert graph["pipeline_name"] == "test_pipeline"

    def test_names_the_returned_document_fingerprint(self, client: TestClient):
        from haute._pipeline_recovery import pipeline_document_fingerprint

        resp = client.get("/api/pipeline/test_pipeline")

        assert resp.headers[DOCUMENT_FINGERPRINT_HEADER] == pipeline_document_fingerprint(
            resp.json()
        )

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

    @pytest.mark.in_process
    def test_preview_reports_a_failed_input_preparation_as_a_contract_error(
        self,
        client: TestClient,
        pipeline_dir: Path,
    ) -> None:
        """Automatic preparation's failure is a public 422 contract error.

        The input is snapshot-backed, so the preview prepares it: through its
        seed plan, which prepares only what it executes, or without one.
        """
        from unittest.mock import patch

        from haute.errors import InputPreparationError
        from haute.parser import parse_pipeline_file

        graph = parse_pipeline_file(pipeline_dir / "test_pipeline.py")
        csv_path = pipeline_dir / "data" / "input.csv"
        pl.DataFrame({"x": [1, 2, 3], "y": [10, 20, 30]}).write_csv(csv_path)
        graph.nodes[0].data.config = {
            "input_type": "file",
            "format": "csv",
            "mode": "scan",
            "path": csv_path.as_posix(),
            "arguments": {},
        }
        failure = InputPreparationError(
            "Preparing this Data Input's snapshot failed.",
            node_id=graph.nodes[0].id,
            identity_digest="a" * 64,
            build_class="bounded",
            reason_code="build_failed",
            remediation="Build this Data Input's snapshot and try again.",
        )

        with (
            patch("haute.executor.prepare_input_snapshots", side_effect=failure),
            patch("haute._input_preparation.prepare_input_snapshots", side_effect=failure),
        ):
            resp = client.post(
                "/api/pipeline/preview",
                json={
                    "graph": graph.model_dump(),
                    "node_id": graph.nodes[0].id,
                    "row_limit": 10,
                },
            )

        assert resp.status_code == 422
        assert resp.json()["detail"] == {
            "error_code": "input_preparation_failed",
            "message": "Preparing this Data Input's snapshot failed.",
            "node_id": graph.nodes[0].id,
            "identity_digest": "a" * 64,
            "build_class": "bounded",
            "reason_code": "build_failed",
            "remediation": "Build this Data Input's snapshot and try again.",
        }

    def test_preview_reports_a_rejected_node_config_as_a_contract_error(
        self,
        client: TestClient,
        pipeline_dir: Path,
    ) -> None:
        """A Scenario Expander without its grid size is a node config defect: the
        preview answers 422 naming the setting, not the internal-error 500."""
        from haute._types import GraphNode, NodeData
        from haute.parser import parse_pipeline_file
        from tests.conftest import make_edge

        graph = parse_pipeline_file(pipeline_dir / "test_pipeline.py")
        source_id = graph.nodes[0].id
        graph.nodes.append(
            GraphNode(
                id="expander",
                data=NodeData(
                    label="expander",
                    nodeType="scenarioExpander",
                    config={"column_name": "price", "min_value": 0.1, "max_value": 0.3},
                ),
            )
        )
        graph.edges.append(make_edge(source_id, "expander"))

        resp = client.post(
            "/api/pipeline/preview",
            json={"graph": graph.model_dump(), "node_id": "expander", "row_limit": 10},
        )

        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"] == {
            "error_code": "node_config_invalid",
            "message": "Scenario expander requires step_count (the number of grid values).",
            "setting": "step_count",
        }

    def test_preview_rejects_unassigned_submodel_input_draft(
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
                            "definition_id": "sm",
                            "alias": "sm",
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
                    "definitionId": "sm",
                    "file": "modules/sm.py",
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

        # The flattener refuses the draft before anything runs, and the preview shows
        # the refusal on the node, where it is read.
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "error"
        assert "missing or malformed public-port handle" in payload["error"]
        execute_graph.assert_not_called()

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
        # The browser knows a superseded preview by its code, not its wording.
        assert resp.json()["detail"] == {
            "error_code": "request_superseded",
            "message": "Preview request superseded by a newer request",
        }


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
                "seed_plan": [],
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
                "seed_plan": [],
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
                "seed_plan": [],
                "graph": graph.model_dump(),
                "row_index": 0,
            },
        )

        assert resp.status_code == 409
        assert resp.json()["detail"] == {
            "error_code": "request_superseded",
            "message": "Trace request superseded by a newer request",
        }


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
                "base_revision": current_source_revision(
                    pipeline_dir / "saved_pipe.py", pipeline_dir
                ),
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
        assert "import haute\n" in content
        # Polars is imported only when the module refers to it.
        assert "import polars as pl" not in content
        assert 'Pipeline("saved_pipe"' in content
        assert (
            '@pipeline.data_input(config="config/data_input/Source.json")\ndef Source(): ...\n'
        ) in content

        # Sidecar should exist too
        sidecar = py_file.with_suffix(".haute.json")
        assert sidecar.exists()


class TestSaveOperation:
    """A save naming an operation writes once; its repeat is answered with what it did."""

    @staticmethod
    def _request(pipeline_dir: Path, operation: str, **changes: Any) -> dict[str, Any]:
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
        return {
            "name": "op_pipe",
            "description": "First",
            "graph": graph,
            "source_file": "op_pipe.py",
            "base_revision": current_source_revision(pipeline_dir / "op_pipe.py", pipeline_dir),
            "connection_id": "connection-before",
            "operation": operation,
            **changes,
        }

    def test_a_repeat_of_a_committed_save_is_answered_with_its_response_and_writes_nothing(
        self, client: TestClient, pipeline_dir: Path
    ) -> None:
        request = self._request(pipeline_dir, "op-repeat-0001")
        first = client.post("/api/pipeline/save", json=request)
        assert first.status_code == 200
        source = pipeline_dir / "op_pipe.py"
        # Another writer changes the file after the save committed.
        external = source.read_text(encoding="utf-8") + "\n# another writer\n"
        source.write_text(external, encoding="utf-8")

        # The tab reconnected its socket before repeating the save whose answer it lost.
        repeat = client.post(
            "/api/pipeline/save", json={**request, "connection_id": "connection-after"}
        )

        assert repeat.status_code == 200
        assert repeat.json() == first.json()
        assert source.read_text(encoding="utf-8") == external

    def test_an_operation_naming_a_different_save_is_refused(
        self, client: TestClient, pipeline_dir: Path
    ) -> None:
        request = self._request(pipeline_dir, "op-reused-0001")
        assert client.post("/api/pipeline/save", json=request).status_code == 200
        source = pipeline_dir / "op_pipe.py"
        saved = source.read_bytes()

        reused = client.post("/api/pipeline/save", json={**request, "description": "Second"})

        assert reused.status_code == 409
        assert reused.json()["detail"].startswith("save_operation_reused:")
        assert source.read_bytes() == saved

    def test_an_operation_the_server_does_not_know_is_saved_against_its_base_revision(
        self, client: TestClient, pipeline_dir: Path
    ) -> None:
        request = self._request(pipeline_dir, "op-unknown-0001")
        assert client.post("/api/pipeline/save", json=request).status_code == 200

        # A request that never arrived, repeated after the file moved on, is stale.
        stale = client.post("/api/pipeline/save", json={**request, "operation": "op-unknown-0002"})

        assert stale.status_code == 409
        assert stale.json()["detail"].startswith("stale_document_revision:")


def test_save_operations_forget_the_oldest_beyond_their_capacity() -> None:
    from haute.routes._save_operations import SaveOperations
    from haute.schemas import SavePipelineResponse

    operations = SaveOperations(capacity=2)
    answer = SavePipelineResponse(
        file="main.py", pipeline_name="main", source_revision="r", document_fingerprint="f"
    )
    for key in ("op-one", "op-two", "op-three"):
        operations.record(key, "digest", answer)

    assert operations.replay("op-one", "digest") is None
    assert operations.replay("op-three", "digest") == answer


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

    def test_output_destination_names_a_databricks_table(self, client: TestClient) -> None:
        config = {
            "output_type": "databricks",
            "http_path": "/sql/1.0/warehouses/abc",
            "table": "workspace.pricing.scored",
            "write_mode": "overwrite",
            "arguments": {},
        }
        graph = {
            "nodes": [
                {
                    "id": "sink",
                    "type": "pipelineNode",
                    "position": {"x": 0, "y": 0},
                    "data": {"label": "sink", "nodeType": "dataOutput", "config": config},
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
            "path": "workspace.pricing.scored",
            "format": "databricks",
            "suffix_mismatch": False,
        }

    def test_output_destination_uses_selected_project_root_when_cwd_differs(
        self,
        client: TestClient,
        pipeline_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        use_project(pipeline_dir)
        unrelated_cwd = pipeline_dir / "unrelated-cwd"
        unrelated_cwd.mkdir()
        use_project(unrelated_cwd)
        graph = {
            "source_file": str(pipeline_dir / "rating" / "test_pipeline.py"),
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
                            "output_type": "unsupported",
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
# WebSocket -- /ws/sync connect/disconnect and broadcast
# ---------------------------------------------------------------------------


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

        use_project(tmp_path)

        assert (
            _resolve_client_source_file("pipelines/main.py")
            == (tmp_path / "pipelines" / "main.py").resolve()
        )

    def test_wire_source_file_uses_project_relative_root_main(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from haute.routes._helpers import wire_source_file

        use_project(tmp_path)
        main = tmp_path / "main.py"
        main.write_text("import haute\n\npipeline = haute.Pipeline('main')\n")

        assert wire_source_file(main) == "main.py"


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

    @pytest.mark.in_process
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
        self,
        client: TestClient,
        pipeline_dir: Path,
        endpoint: str,
        use_parsed_graph: bool,
        pipeline_settings,
    ):
        from unittest.mock import patch

        pipeline_settings(pipeline_time_limit_minutes=0.001 / 60)

        if use_parsed_graph:
            from haute.parser import parse_pipeline_file

            graph = parse_pipeline_file(pipeline_dir / "test_pipeline.py")
            if endpoint == "trace":
                body = {"graph": graph.model_dump(), "row_index": 0, "seed_plan": []}
            else:
                body = {"graph": graph.model_dump(), "node_id": graph.nodes[0].id}
        else:
            body = {"graph": self._sink_graph(pipeline_dir), "node_id": "sink"}

        if endpoint == "write-output":
            from haute._worker_failures import WorkerTimeoutError

            timeout_patch = patch(
                "haute.routes.pipeline._output_write_transaction",
                side_effect=WorkerTimeoutError(timeout_seconds=0.001),
            )
        else:
            timeout_patch = patch(
                "haute.routes._timeouts.asyncio.to_thread",
                self._never_finishes,
            )

        with timeout_patch:
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

    @pytest.mark.in_process
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
            (
                "write-output",
                "haute.routes.pipeline._output_write_transaction",
                "sink error",
                False,
            ),
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
                body = {"graph": graph.model_dump(), "row_index": 0, "seed_plan": []}
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

        with patch("haute.routes.pipeline._output_write_transaction", side_effect=error):
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
    """Broken readable pipelines remain discoverable editor documents."""

    def test_broken_pipeline_in_list(self, pipeline_dir: Path, monkeypatch: pytest.MonkeyPatch):
        """A syntax-broken pipeline is listed as source-only instead of disappearing."""
        use_project(pipeline_dir)
        from haute.routes._helpers import invalidate_pipeline_index

        invalidate_pipeline_index()
        app = project_app()

        (pipeline_dir / "bad_pipe.py").write_text(
            """\
import haute
pipeline = haute.Pipeline("bad_pipe")

@pipeline.polars
def healthy():
    return None

def broken(:
    pass
""",
            encoding="utf-8",
        )
        invalidate_pipeline_index()

        resp = TestClient(app).get("/api/pipelines")
        assert resp.status_code == 200
        data = resp.json()
        bad = [p for p in data if p["name"] == "bad_pipe"]
        assert len(bad) == 1
        assert bad[0]["load_status"] == "source_only"
        assert bad[0]["node_count"] == 0
        assert bad[0]["diagnostic_count"] == 1


class TestGetPipelineParseError:
    """Test that get_pipeline handles parse failures on the indexed file."""

    def test_indexed_file_unparseable(self, pipeline_dir: Path, monkeypatch: pytest.MonkeyPatch):
        """If the indexed file becomes unparseable, fall back to linear scan."""
        use_project(pipeline_dir)
        app = project_app()

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
