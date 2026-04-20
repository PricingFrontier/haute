"""Tests for haute.server - FastAPI API endpoint integration tests."""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def pipeline_dir(tmp_path: Path) -> Path:
    """Create a temporary project with a root-level pipeline and sample data."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    pl.DataFrame({"x": [1, 2, 3], "y": [10, 20, 30]}).write_parquet(data_dir / "input.parquet")

    # Use as_posix() to avoid Windows backslash escape issues in the
    # generated Python source (e.g. \U interpreted as unicode escape).
    data_path = (data_dir / "input.parquet").as_posix()

    code = f'''\
import polars as pl
import haute

pipeline = haute.Pipeline("test_pipeline", description="A test pipeline")


@pipeline.data_source(path="{data_path}")
def source() -> pl.DataFrame:
    """Read data."""
    return pl.scan_parquet("{data_path}")


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

    def test_preview_empty_graph_returns_400(self, client: TestClient):
        resp = client.post(
            "/api/pipeline/preview",
            json={
                "graph": {"nodes": [], "edges": []},
                "node_id": "x",
            },
        )
        assert resp.status_code == 400


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
                        "nodeType": "dataSource",
                        "config": {"path": "d.parquet"},
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
# POST /api/pipeline/sink
# ---------------------------------------------------------------------------


class TestExecuteSinkEndpoint:
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
                        "nodeType": "dataSource",
                        "config": {"path": str(data_path)},
                    },
                },
                {
                    "id": "sink",
                    "type": "pipelineNode",
                    "position": {"x": 300, "y": 0},
                    "data": {
                        "label": "sink",
                        "nodeType": "dataSink",
                        "config": {"path": str(out_path), "format": "parquet"},
                    },
                },
            ],
            "edges": [{"id": "e1", "source": "src", "target": "sink"}],
        }
        resp = client.post(
            "/api/pipeline/sink",
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
# Submodel routes — POST /api/submodel/create, GET /api/submodel/{name},
#                   POST /api/submodel/dissolve
# ---------------------------------------------------------------------------


@pytest.fixture()
def three_node_graph(pipeline_dir: Path) -> dict:
    """Parse the test pipeline and return its graph as a dict payload."""
    from haute.parser import parse_pipeline_file

    graph = parse_pipeline_file(pipeline_dir / "test_pipeline.py")
    return graph.model_dump()


class TestCreateSubmodel:
    def _create_payload(self, graph_dict: dict, node_ids: list[str]) -> dict:
        return {
            "name": "my_submodel",
            "node_ids": node_ids,
            "graph": graph_dict,
            "source_file": "test_pipeline.py",
            "pipeline_name": "test_pipeline",
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
        """Phase 1C #11: the handler now returns a sanitized 400 detail
        so raw ValueError text from ``create_submodel_graph`` (which
        may embed graph walk internals) never reaches the client.  The
        full "at least 2 nodes" message is recorded in the
        ``submodel_create_invalid`` structured log with ``exc_info=True``.
        """
        from haute.routes._helpers import _INTERNAL_ERROR_DETAIL

        # Only 1 node — must be at least 2
        node_ids = [three_node_graph["nodes"][0]["id"]]
        payload = self._create_payload(three_node_graph, node_ids)
        resp = client.post("/api/submodel/create", json=payload)
        assert resp.status_code == 400
        assert resp.json()["detail"] == _INTERNAL_ERROR_DETAIL

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
            },
        )
        assert create_resp.status_code == 200

        # Now fetch it
        resp = client.get("/api/submodel/lookup")
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
        resp = client.get("/api/submodel/nonexistent")
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
            },
        )
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# WebSocket — /ws/sync connect/disconnect and broadcast
# ---------------------------------------------------------------------------


class TestWebSocket:
    def test_connect_and_disconnect(self, client: TestClient):
        with client.websocket_connect("/ws/sync") as ws:
            # Connection should be accepted — sending a keep-alive message works
            ws.send_text("ping")
        # No error means connect + clean disconnect succeeded

    def test_broadcast_reaches_connected_client(
        self,
        client: TestClient,
        pipeline_dir: Path,
    ):
        """Save endpoint writes files and triggers sidecar — verify the
        full HTTP flow still works with an active WebSocket connection."""
        from haute.routes._helpers import ws_clients

        with client.websocket_connect("/ws/sync"):
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
                            "nodeType": "dataSource",
                            "config": {"path": "d.parquet"},
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
        ):
            from haute.server import _file_watcher

            async def _run() -> None:
                await _file_watcher()
                await asyncio.sleep(0.5)  # allow debounce task to complete

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        assert len(broadcast_calls) >= 1
        assert broadcast_calls[0]["type"] == "graph_update"
        assert "graph" in broadcast_calls[0]

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
        ):
            from haute.server import _file_watcher

            async def _run() -> None:
                await _file_watcher()
                await asyncio.sleep(0.5)

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
        ):
            from haute.server import _file_watcher

            async def _run() -> None:
                await _file_watcher()
                await asyncio.sleep(0.5)

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        assert len(broadcast_calls) == 0


# ---------------------------------------------------------------------------
# Phase 1C: Pipeline route timeout + exception paths
# ---------------------------------------------------------------------------


class TestPipelineTimeouts:
    """Timeout paths — mock asyncio.wait_for to raise TimeoutError."""

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
                        "nodeType": "dataSource",
                        "config": {"path": str(data_path)},
                    },
                },
                {
                    "id": "sink",
                    "type": "pipelineNode",
                    "position": {"x": 300, "y": 0},
                    "data": {
                        "label": "sink",
                        "nodeType": "dataSink",
                        "config": {"path": "/tmp/test_sink.parquet", "format": "parquet"},
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
            ("sink", False),
        ],
        ids=["trace_timeout", "preview_timeout", "sink_timeout"],
    )
    def test_timeout_returns_504(
        self, client: TestClient, pipeline_dir: Path, endpoint: str, use_parsed_graph: bool
    ):
        from unittest.mock import AsyncMock, patch

        if use_parsed_graph:
            from haute.parser import parse_pipeline_file

            graph = parse_pipeline_file(pipeline_dir / "test_pipeline.py")
            if endpoint == "trace":
                body = {"graph": graph.model_dump(), "row_index": 0}
            else:
                body = {"graph": graph.model_dump(), "node_id": graph.nodes[0].id}
        else:
            body = {"graph": self._sink_graph(pipeline_dir), "node_id": "sink"}

        with patch(
            "haute.routes.pipeline.asyncio.wait_for",
            new_callable=AsyncMock,
            side_effect=TimeoutError,
        ):
            resp = client.post(f"/api/pipeline/{endpoint}", json=body)
        assert resp.status_code == 504


class TestPipelineExceptions:
    """Exception paths — mock execute_graph to raise RuntimeError → 500."""

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
                        "nodeType": "dataSource",
                        "config": {"path": str(data_path)},
                    },
                },
                {
                    "id": "sink",
                    "type": "pipelineNode",
                    "position": {"x": 300, "y": 0},
                    "data": {
                        "label": "sink",
                        "nodeType": "dataSink",
                        "config": {"path": "/tmp/test_sink.parquet", "format": "parquet"},
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
            # be a no-op — the route still calls its own top-level
            # bindings.
            ("trace", "haute.routes.pipeline.execute_trace", "trace error", True),
            ("preview", "haute.routes.pipeline.execute_graph", "preview error", True),
            ("sink", "haute.routes.pipeline.execute_sink", "sink error", False),
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


class TestPreviewEdgeCases:
    """Preview edge cases: missing node in results."""

    def test_preview_node_not_in_results(self, client: TestClient, pipeline_dir: Path):
        """If the node_id is valid but not found in execute_graph results → 404."""
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
        from haute.routes._helpers import invalidate_pipeline_index

        invalidate_pipeline_index()
        from haute.server import app

        # Mock parse_pipeline_file to raise for a specific file
        original_parse = None

        def _patch_parse(f, **kw):
            if "test_pipeline" in str(f):
                return original_parse(f, **kw)
            raise RuntimeError("Simulated parse failure")

        from haute import parser
        from haute.routes import pipeline as pipeline_routes

        original_parse = parser.parse_pipeline_file

        # Create a second "pipeline" file that will fail
        (pipeline_dir / "bad_pipe.py").write_text(
            "import haute\npipeline = haute.Pipeline('bad_pipe')\n"
        )
        invalidate_pipeline_index()

        c = TestClient(app)
        # Patch the route-scoped binding — after the #101 import hoist
        # ``list_pipelines`` calls its own top-level ``parse_pipeline_file``
        # alias, so patching the ``haute.parser`` source module alone no
        # longer affects the code path.
        with patch.object(
            pipeline_routes, "parse_pipeline_file", side_effect=_patch_parse
        ):
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
            "/api/pipeline/sink",
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

    def test_request_id_header_passthrough(self, client: TestClient):
        """Middleware adds x-request-id header to successful responses."""
        resp = client.get("/api/pipelines")
        assert resp.status_code == 200
        assert "x-request-id" in resp.headers

    def test_custom_request_id_preserved(self, client: TestClient):
        """Client-supplied x-request-id is preserved in the response."""
        resp = client.get("/api/pipelines", headers={"x-request-id": "custom-123"})
        assert resp.headers["x-request-id"] == "custom-123"


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
        config_dir.mkdir()
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
        ):
            from haute.server import _file_watcher

            async def _run() -> None:
                await _file_watcher()
                await asyncio.sleep(0.5)

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
        ):
            from haute.server import _file_watcher

            async def _run() -> None:
                await _file_watcher()
                await asyncio.sleep(0.5)

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        assert len(broadcast_calls) == 0


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
        ):
            from haute.server import _file_watcher

            async def _run() -> None:
                await _file_watcher()
                await asyncio.sleep(0.5)

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        assert len(broadcast_calls) >= 1
        assert broadcast_calls[0]["type"] == "graph_update"


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
        ):
            from haute.server import _file_watcher

            async def _run() -> None:
                await _file_watcher()
                await asyncio.sleep(0.5)

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        assert len(broadcast_calls) >= 1
        assert broadcast_calls[0]["type"] == "parse_error"
        assert "bad syntax" in broadcast_calls[0]["error"]


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
            await asyncio.sleep(0.5)
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
        ):
            from haute.server import _file_watcher

            async def _run() -> None:
                await _file_watcher()
                await asyncio.sleep(1.0)

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_run())
            finally:
                loop.close()

        # First change broadcasts, second with same fingerprint is skipped
        graph_updates = [c for c in broadcast_calls if c["type"] == "graph_update"]
        assert len(graph_updates) == 1


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

        # Select only "source" — it has an edge to "transform" which is outside
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
        code = """\
import polars as pl
import haute

pipeline = haute.Pipeline("rewire_test", description="Rewire test")

@pipeline.data_source(path="data/input.parquet")
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

        # Select only "middle" and "source" (2 nodes) — "final" stays outside
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
        with client.websocket_connect("/ws/sync") as ws:
            ws.send_text("keep-alive")
            ws.send_text("ping")
            ws.send_text("")

    def test_dead_client_removed_from_set(self, client: TestClient):
        from haute.routes._helpers import ws_clients

        with client.websocket_connect("/ws/sync"):
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
