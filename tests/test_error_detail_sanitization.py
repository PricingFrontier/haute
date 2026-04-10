"""Tests for E3 and E8 error handling fixes.

E3: Verify no route leaks raw Python exception messages via HTTP 500
    ``detail`` strings. All routes must return a safe, generic message
    and log the actual error server-side.

E8: Verify ``_execute_eager_core`` logs node failures at ``error`` level,
    not ``warning``.
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import polars as pl
import pytest
from fastapi.testclient import TestClient


# -- Shared constants and helpers ------------------------------------------

_SAFE_DETAIL = "Operation failed. Check the server logs for details."


def _minimal_source_graph(path: str = "/tmp/fake.parquet") -> dict:
    """A single-node graph with one dataSource node (reused in many tests)."""
    return {
        "nodes": [
            {
                "id": "src",
                "type": "pipelineNode",
                "position": {"x": 0, "y": 0},
                "data": {
                    "label": "src",
                    "nodeType": "dataSource",
                    "config": {"path": path},
                },
            },
        ],
        "edges": [],
    }


def _source_and_transform_graph(
    transform_id: str = "txn",
    transform_label: str = "bad_transform",
    code: str = "df",
) -> dict:
    """A two-node graph: dataSource -> polars transform."""
    return {
        "nodes": [
            {
                "id": "src",
                "type": "pipelineNode",
                "position": {"x": 0, "y": 0},
                "data": {
                    "label": "source",
                    "nodeType": "dataSource",
                    "config": {"path": "/tmp/fake.parquet"},
                },
            },
            {
                "id": transform_id,
                "type": "pipelineNode",
                "position": {"x": 300, "y": 0},
                "data": {
                    "label": transform_label,
                    "nodeType": "polars",
                    "config": {"code": code},
                },
            },
        ],
        "edges": [{"id": "e1", "source": "src", "target": transform_id}],
    }


# -- Fixtures -------------------------------------------------------------


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient with cwd set to a temp directory and Databricks env set."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABRICKS_HOST", "https://test.cloud.databricks.com")
    monkeypatch.setenv("DATABRICKS_TOKEN", "dapi_test_token")
    (tmp_path / "main.py").write_text("")
    from haute.server import app

    return TestClient(app)


@pytest.fixture()
def pipeline_graph(tmp_path: Path):
    """Create a minimal pipeline file and return its parsed graph."""
    from haute.parser import parse_pipeline_file

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    data_path = data_dir / "input.parquet"
    pl.DataFrame({"x": [1, 2, 3]}).write_parquet(data_path)

    code = f"""\
import haute
pipeline = haute.Pipeline("test")

@pipeline.data_source(path="{data_path}")
def source(config):
    pass
"""
    py = tmp_path / "test_pipeline.py"
    py.write_text(code)
    return parse_pipeline_file(py)


# =====================================================================
# E3: Route-level error detail sanitization  (parametrized)
# =====================================================================

# Each tuple: (http_method, url, patch_target_or_setup, error_message,
#               expected_status, forbidden_substrings)
# For "patch_target_or_setup":
#   - A string means: patch that target with side_effect=RuntimeError(error_message)
#   - A callable means: call it to get the context manager(s) for patching


def _databricks_client_patch(attr_chain: str, error_msg: str):
    """Return a context-manager that patches _get_databricks_client so that
    traversing *attr_chain* (e.g. 'warehouses.list') raises."""
    mock_ws = MagicMock()
    obj = mock_ws
    parts = attr_chain.split(".")
    for part in parts[:-1]:
        obj = getattr(obj, part)
    getattr(obj, parts[-1]).side_effect = RuntimeError(error_msg)
    return patch("haute.routes.databricks._get_databricks_client", return_value=mock_ws)


def _mlflow_tracking_patch(attr: str, error_msg: str, on_client: bool = False):
    """Return a context-manager that patches _ensure_tracking so that the
    mlflow client (or registry client) raises on *attr*."""
    mock_mlflow = MagicMock()
    mock_client = MagicMock()
    target = mock_client if on_client else mock_mlflow
    getattr(target, attr).side_effect = RuntimeError(error_msg)
    return patch(
        "haute.routes.mlflow._ensure_tracking",
        return_value=(mock_mlflow, mock_client),
    )


# -- Simple safe-detail endpoints (one patch target, no special fixture) --

_SIMPLE_SAFE_DETAIL_CASES: list[tuple] = [
    # Databricks routes
    pytest.param(
        "get",
        "/api/databricks/warehouses",
        None,
        lambda err: _databricks_client_patch("warehouses.list", err),
        "/home/user/.databricks/token: permission denied",
        500,
        ["permission denied", ".databricks/token"],
        id="databricks-warehouses",
    ),
    pytest.param(
        "get",
        "/api/databricks/catalogs",
        None,
        lambda err: _databricks_client_patch("catalogs.list", err),
        "AuthenticationError: invalid token xyz-secret",
        500,
        ["xyz-secret"],
        id="databricks-catalogs",
    ),
    pytest.param(
        "get",
        "/api/databricks/schemas",
        {"catalog": "main"},
        lambda err: _databricks_client_patch("schemas.list", err),
        "SDK internal: /var/run/secrets/token read failure",
        500,
        [],
        id="databricks-schemas",
    ),
    pytest.param(
        "get",
        "/api/databricks/tables",
        {"catalog": "cat", "schema": "sch"},
        lambda err: _databricks_client_patch("tables.list", err),
        "Connection to host 10.0.0.5:443 refused",
        500,
        ["10.0.0.5"],
        id="databricks-tables",
    ),
    pytest.param(
        "post",
        "/api/databricks/fetch",
        {"json": {"table": "cat.sch.tbl", "http_path": "/sql/wh"}},
        lambda err: patch("haute._databricks_io.fetch_and_cache", side_effect=RuntimeError(err)),
        "OSError: /mnt/data/cache full",
        500,
        ["/mnt/data/cache"],
        id="databricks-fetch",
    ),
    # JSON cache
    pytest.param(
        "post",
        "/api/json-cache/build",
        {"json": {"path": "data.jsonl"}},
        lambda err: patch("haute._json_flatten.build_json_cache", side_effect=RuntimeError(err)),
        "OSError: [Errno 28] No space left on device: '/tmp/x'",
        500,
        ["/tmp/x"],
        id="json-cache-build",
    ),
    # MLflow discovery routes (502)
    pytest.param(
        "get",
        "/api/mlflow/experiments",
        None,
        lambda err: _mlflow_tracking_patch("search_experiments", err),
        "ConnectionError: https://internal-mlflow.corp:5000/api refused",
        502,
        ["internal-mlflow.corp"],
        id="mlflow-experiments",
    ),
    pytest.param(
        "get",
        "/api/mlflow/runs",
        {"experiment_id": "1"},
        lambda err: _mlflow_tracking_patch("search_runs", err),
        "SSLError: certificate verify failed for host mlflow.internal",
        502,
        ["mlflow.internal"],
        id="mlflow-runs",
    ),
    pytest.param(
        "get",
        "/api/mlflow/models",
        None,
        lambda err: _mlflow_tracking_patch("search_registered_models", err, on_client=True),
        "PermissionDenied: access token for service-account@corp expired",
        502,
        ["service-account@corp"],
        id="mlflow-models",
    ),
]


class TestSafeDetailOnError:
    """All routes must return a safe detail message on internal errors and
    never leak sensitive information from exception messages."""

    @pytest.mark.parametrize(
        "method,url,req_kwargs,make_patch,error_msg,expected_status,forbidden",
        _SIMPLE_SAFE_DETAIL_CASES,
    )
    def test_returns_safe_detail(
        self,
        client: TestClient,
        method: str,
        url: str,
        req_kwargs: dict | None,
        make_patch,
        error_msg: str,
        expected_status: int,
        forbidden: list[str],
    ) -> None:
        req_kwargs = req_kwargs or {}
        # Separate params from other kwargs
        if "json" not in req_kwargs:
            # treat as query params
            call_kwargs = {"params": req_kwargs} if req_kwargs else {}
        else:
            call_kwargs = req_kwargs

        with make_patch(error_msg):
            resp = getattr(client, method)(url, **call_kwargs)

        assert resp.status_code == expected_status
        detail = resp.json()["detail"]
        for substr in forbidden:
            assert substr not in detail
        assert "Check the server logs" in detail

    # -- MLflow model-versions needs two patches --

    def test_mlflow_model_versions_502_no_leak(self, client: TestClient) -> None:
        mock_client = MagicMock()
        error_msg = "Databricks API error: workspace /Users/admin/secret"
        mock_client.search_model_versions.side_effect = RuntimeError(error_msg)
        with (
            patch(
                "haute.routes.mlflow._ensure_tracking",
                return_value=(MagicMock(), mock_client),
            ),
            patch(
                "haute.routes.mlflow.search_versions",
                side_effect=RuntimeError(error_msg),
            ),
        ):
            resp = client.get("/api/mlflow/model-versions", params={"model_name": "test"})
        assert resp.status_code == 502
        detail = resp.json()["detail"]
        assert "/Users/admin/secret" not in detail
        assert "Check the server logs" in detail

    # -- Pipeline routes need the pipeline_graph fixture --

    def test_pipeline_trace_500_no_leak(self, client: TestClient, pipeline_graph) -> None:
        with patch(
            "haute.trace.execute_trace",
            side_effect=RuntimeError("traceback: File /home/user/secret.py line 42"),
        ):
            resp = client.post(
                "/api/pipeline/trace",
                json={"graph": pipeline_graph.model_dump(), "row_index": 0},
            )
        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert "/home/user/secret.py" not in detail
        assert detail == _SAFE_DETAIL

    def test_pipeline_preview_500_no_leak(self, client: TestClient, pipeline_graph) -> None:
        node_id = pipeline_graph.nodes[0].id
        with patch(
            "haute.executor.execute_graph",
            side_effect=RuntimeError("MemoryError at 0x7fff5e3a1000"),
        ):
            resp = client.post(
                "/api/pipeline/preview",
                json={"graph": pipeline_graph.model_dump(), "node_id": node_id},
            )
        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert "0x7fff" not in detail
        assert detail == _SAFE_DETAIL

    def test_pipeline_sink_500_no_leak(self, client: TestClient, tmp_path: Path) -> None:
        data_path = tmp_path / "data" / "input.parquet"
        graph = _minimal_source_graph(str(data_path))
        # Add a sink node
        graph["nodes"].append(
            {
                "id": "sink",
                "type": "pipelineNode",
                "position": {"x": 300, "y": 0},
                "data": {
                    "label": "sink",
                    "nodeType": "dataSink",
                    "config": {"path": "/tmp/test_sink.parquet", "format": "parquet"},
                },
            }
        )
        graph["edges"] = [{"id": "e1", "source": "src", "target": "sink"}]
        with patch(
            "haute.executor.execute_sink",
            side_effect=RuntimeError("PermissionError: /secure/dir/output.parquet"),
        ):
            resp = client.post("/api/pipeline/sink", json={"graph": graph, "node_id": "sink"})
        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert "/secure/dir/" not in detail
        assert detail == _SAFE_DETAIL


# =====================================================================
# E3: Optimiser / Modelling routes safe detail  (need job store setup)
# =====================================================================


class TestOptimiserRoutesSafeDetail:
    """Optimiser apply/frontier/save/mlflow routes must not leak details."""

    @pytest.fixture()
    def clean_job_store(self):
        from haute.routes.optimiser import _store

        snapshot = dict(_store.jobs)
        yield _store
        _store.jobs.clear()
        _store.jobs.update(snapshot)

    def test_apply_500_no_leak(self, client: TestClient, clean_job_store) -> None:
        store = clean_job_store
        mock_solve_result = MagicMock()
        type(mock_solve_result).dataframe = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("numpy internal: segfault at 0xdead"))
        )
        store.jobs["test_apply_err"] = {
            "status": "completed",
            "solve_result": mock_solve_result,
            "created_at": time.time(),
        }
        resp = client.post("/api/optimiser/apply", json={"job_id": "test_apply_err"})
        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert "segfault" not in detail
        assert "0xdead" not in detail
        assert detail == _SAFE_DETAIL

    def test_frontier_500_no_leak(self, client: TestClient, clean_job_store) -> None:
        store = clean_job_store
        mock_solver = MagicMock()
        mock_solver.frontier.side_effect = RuntimeError(
            "Rust panic: thread 'solver' panicked at core/src/lib.rs:42"
        )
        store.jobs["test_frontier_err"] = {
            "status": "completed",
            "solver": mock_solver,
            "quote_grid": MagicMock(),
            "created_at": time.time(),
        }
        resp = client.post(
            "/api/optimiser/frontier",
            json={
                "job_id": "test_frontier_err",
                "threshold_ranges": {"volume": [0.9, 1.1]},
            },
        )
        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert "lib.rs:42" not in detail
        assert detail == _SAFE_DETAIL

    def test_save_oserror_no_path_leak(
        self, client: TestClient, clean_job_store, tmp_path, monkeypatch
    ) -> None:
        from haute._sandbox import set_project_root

        set_project_root(tmp_path)
        store = clean_job_store
        mock_solve_result = SimpleNamespace(
            lambdas={"x": 1.0},
            total_objective=100.0,
            total_constraints={"vol": 0.5},
            converged=True,
        )
        store.jobs["test_save_err"] = {
            "status": "completed",
            "solve_result": mock_solve_result,
            "solver": MagicMock(),
            "config": {},
            "created_at": time.time(),
        }
        with patch(
            "pathlib.Path.write_text",
            side_effect=OSError("Permission denied: '/secure/results/output.json'"),
        ):
            resp = client.post(
                "/api/optimiser/save",
                json={
                    "job_id": "test_save_err",
                    "output_path": "results/output.json",
                },
            )
        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert "/secure/results/" not in detail
        assert "Check the server logs" in detail

    def test_mlflow_log_500_no_leak(self, client: TestClient, clean_job_store) -> None:
        store = clean_job_store
        mock_solver = MagicMock()
        mock_solve_result = MagicMock()
        store.jobs["test_mlflow_err"] = {
            "status": "completed",
            "solver": mock_solver,
            "solve_result": mock_solve_result,
            "config": {},
            "node_label": "opt",
            "created_at": time.time(),
        }
        with patch.dict("sys.modules", {"mlflow": MagicMock()}):
            with patch(
                "haute.modelling._mlflow_log.resolve_tracking_backend",
                side_effect=RuntimeError(
                    "ConnectionError: https://internal-mlflow.corp:5000 refused"
                ),
            ):
                resp = client.post(
                    "/api/optimiser/mlflow/log",
                    json={"job_id": "test_mlflow_err"},
                )
        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert "internal-mlflow.corp" not in detail
        assert detail == _SAFE_DETAIL


class TestModellingRoutesSafeDetail:
    """Modelling mlflow log must not leak details."""

    def test_mlflow_log_500_no_leak(self, client: TestClient) -> None:
        from haute.routes.modelling import _store

        _store.jobs["test_err"] = {
            "status": "completed",
            "result": SimpleNamespace(
                metrics={},
                model_path=None,
                diagnostics={},
                metadata={},
            ),
            "config": {},
            "node_label": "model",
            "created_at": time.time(),
        }
        try:
            with patch(
                "haute.modelling._mlflow_log.log_experiment",
                side_effect=RuntimeError("ODBC driver not found at /opt/simba/lib"),
            ):
                resp = client.post(
                    "/api/modelling/mlflow/log",
                    json={"job_id": "test_err"},
                )
            assert resp.status_code == 500
            detail = resp.json()["detail"]
            assert "/opt/simba/lib" not in detail
            assert detail == _SAFE_DETAIL
        finally:
            _store.jobs.pop("test_err", None)


# =====================================================================
# E3: Route-level error logging  (parametrized)
# =====================================================================

# Each tuple: (http_method, url, req_kwargs, patch_target, logger_module,
#               error_msg, expected_status)

_LOG_ON_ERROR_CASES: list[tuple] = [
    pytest.param(
        "get",
        "/api/databricks/warehouses",
        None,
        lambda err: _databricks_client_patch("warehouses.list", err),
        "haute.routes.databricks",
        "secret-err",
        500,
        id="databricks-warehouses-log",
    ),
    pytest.param(
        "post",
        "/api/databricks/fetch",
        {"json": {"table": "cat.sch.tbl", "http_path": "/sql/wh"}},
        lambda err: patch("haute._databricks_io.fetch_and_cache", side_effect=RuntimeError(err)),
        "haute.routes.databricks",
        "internal-boom",
        500,
        id="databricks-fetch-log",
    ),
    pytest.param(
        "post",
        "/api/json-cache/build",
        {"json": {"path": "data.jsonl"}},
        lambda err: patch("haute._json_flatten.build_json_cache", side_effect=RuntimeError(err)),
        "haute.routes.json_cache",
        "internal-json-error",
        500,
        id="json-cache-build-log",
    ),
    pytest.param(
        "get",
        "/api/mlflow/experiments",
        None,
        lambda err: _mlflow_tracking_patch("search_experiments", err),
        "haute.routes.mlflow",
        "secret-mlflow-err",
        502,
        id="mlflow-experiments-log",
    ),
]


class TestLogOnError:
    """Verify errors are logged server-side when routes return 500/502.

    These tests check that ``logger.error`` is called and the original
    error message is preserved in the log.  They do NOT assert on exact
    event names or keyword argument structure.
    """

    @pytest.mark.parametrize(
        "method,url,req_kwargs,make_patch,logger_module,error_msg,expected_status",
        _LOG_ON_ERROR_CASES,
    )
    def test_logs_real_error(
        self,
        client: TestClient,
        method: str,
        url: str,
        req_kwargs: dict | None,
        make_patch,
        logger_module: str,
        error_msg: str,
        expected_status: int,
    ) -> None:
        req_kwargs = req_kwargs or {}
        if "json" not in req_kwargs:
            call_kwargs = {"params": req_kwargs} if req_kwargs else {}
        else:
            call_kwargs = req_kwargs

        mock_logger = MagicMock()
        with make_patch(error_msg), patch(f"{logger_module}.logger", mock_logger):
            resp = getattr(client, method)(url, **call_kwargs)

        assert resp.status_code == expected_status
        mock_logger.error.assert_called()
        assert error_msg in str(mock_logger.error.call_args)

    # -- Pipeline routes need the pipeline_graph fixture --

    def test_pipeline_trace_logs_error(self, client: TestClient, pipeline_graph) -> None:
        mock_logger = MagicMock()
        with (
            patch(
                "haute.trace.execute_trace",
                side_effect=RuntimeError("real-trace-error"),
            ),
            patch("haute.routes.pipeline.logger", mock_logger),
        ):
            resp = client.post(
                "/api/pipeline/trace",
                json={"graph": pipeline_graph.model_dump(), "row_index": 0},
            )
        assert resp.status_code == 500
        mock_logger.error.assert_called()
        assert "real-trace-error" in str(mock_logger.error.call_args)

    def test_pipeline_preview_logs_error(self, client: TestClient, pipeline_graph) -> None:
        mock_logger = MagicMock()
        node_id = pipeline_graph.nodes[0].id
        with (
            patch(
                "haute.executor.execute_graph",
                side_effect=RuntimeError("real-preview-error"),
            ),
            patch("haute.routes.pipeline.logger", mock_logger),
        ):
            resp = client.post(
                "/api/pipeline/preview",
                json={"graph": pipeline_graph.model_dump(), "node_id": node_id},
            )
        assert resp.status_code == 500
        mock_logger.error.assert_called()
        assert "real-preview-error" in str(mock_logger.error.call_args)

    def test_pipeline_sink_logs_error(self, client: TestClient) -> None:
        mock_logger = MagicMock()
        graph = _minimal_source_graph()
        with (
            patch(
                "haute.executor.execute_sink",
                side_effect=RuntimeError("real-sink-error"),
            ),
            patch("haute.routes.pipeline.logger", mock_logger),
        ):
            resp = client.post("/api/pipeline/sink", json={"graph": graph, "node_id": "src"})
        assert resp.status_code == 500
        mock_logger.error.assert_called()
        assert "real-sink-error" in str(mock_logger.error.call_args)


# =====================================================================
# E3: Verify intentional domain errors still pass through
# =====================================================================


class TestDomainErrorsStillExposed:
    """400-level errors with domain messages must still be exposed to users."""

    def test_git_guardrail_error_exposed(self, client: TestClient) -> None:
        from haute._git import GitGuardrailError

        with patch(
            "haute.routes.git.get_status",
            side_effect=GitGuardrailError("Cannot push to protected branch 'main'"),
        ):
            resp = client.get("/api/git/status")
        assert resp.status_code == 403
        assert "Cannot push to protected branch" in resp.json()["detail"]

    def test_git_error_exposed(self, client: TestClient) -> None:
        from haute._git import GitError

        with patch(
            "haute.routes.git.get_status",
            side_effect=GitError("No git repository found"),
        ):
            resp = client.get("/api/git/status")
        assert resp.status_code == 400
        assert "No git repository found" in resp.json()["detail"]

    def test_file_schema_value_error_exposed(self, client: TestClient, tmp_path: Path) -> None:
        target = tmp_path / "bad.csv"
        target.write_text("invalid")
        with patch(
            "haute.graph_utils.read_source",
            side_effect=ValueError("Unsupported file format: .xyz"),
        ):
            resp = client.get("/api/schema", params={"path": str(target)})
        assert resp.status_code == 400
        assert "Unsupported file format" in resp.json()["detail"]

    def test_databricks_missing_credentials_exposed(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DATABRICKS_HOST", raising=False)
        monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)
        from haute.server import app

        c = TestClient(app)
        resp = c.get("/api/databricks/warehouses")
        assert resp.status_code == 503
        assert "DATABRICKS_HOST" in resp.json()["detail"]


# =====================================================================
# E3: Constant consistency check
# =====================================================================


class TestInternalErrorDetailConstant:
    """Verify that each route module defines the safe error constant."""

    @pytest.mark.parametrize(
        "module_path",
        [
            "haute.routes.databricks",
            "haute.routes.pipeline",
            "haute.routes.json_cache",
            "haute.routes.optimiser",
            "haute.routes.modelling",
            "haute.routes.git",
            "haute.routes.mlflow",
        ],
    )
    def test_module_has_internal_error_detail(self, module_path: str) -> None:
        import importlib

        mod = importlib.import_module(module_path)
        assert hasattr(mod, "_INTERNAL_ERROR_DETAIL")
        assert "Check the server logs" in mod._INTERNAL_ERROR_DETAIL


# =====================================================================
# E8: Node execution failure log level
# =====================================================================


class TestNodeFailureLogLevel:
    """Verify that _execute_eager_core logs node failures at ERROR, not WARNING."""

    @staticmethod
    def _make_failing_graph():
        from tests.conftest import make_edge, make_source_node, make_transform_node
        from haute._types import PipelineGraph

        return PipelineGraph(
            nodes=[make_source_node("src"), make_transform_node("t")],
            edges=[make_edge("src", "t")],
        )

    @staticmethod
    def _build_fn(node, **kwargs):
        from haute._types import NodeType

        if node.data.nodeType == NodeType.DATA_SOURCE:
            return node.id, lambda: pl.DataFrame({"x": [1]}).lazy(), True

        def failing_fn(*dfs):
            raise RuntimeError("test node failure")

        return node.id, failing_fn, False

    def test_node_failure_logged_at_error_level(self) -> None:
        from haute._execute_lazy import _execute_eager_core

        g = self._make_failing_graph()
        mock_logger = MagicMock()
        with patch("haute._execute_lazy.logger", mock_logger):
            result = _execute_eager_core(g, self._build_fn, swallow_errors=True)

        assert "t" in result.errors
        assert "test node failure" in result.errors["t"]
        mock_logger.error.assert_called()
        assert "test node failure" in str(mock_logger.error.call_args)
        mock_logger.warning.assert_not_called()

    def test_node_failure_not_logged_at_warning(self) -> None:
        from haute._execute_lazy import _execute_eager_core

        g = self._make_failing_graph()
        mock_logger = MagicMock()
        with patch("haute._execute_lazy.logger", mock_logger):
            _execute_eager_core(g, self._build_fn, swallow_errors=True)

        for call in mock_logger.warning.call_args_list:
            assert "node_failed" not in str(call), (
                "node_failed should no longer be logged at WARNING level"
            )


# =====================================================================
# Status code inconsistency: missing MLflow returns 400 vs 503
# =====================================================================


class TestMlflowMissingStatusInconsistency:
    """Document that optimiser and mlflow routes disagree on HTTP status
    when MLflow is not installed.

    - ``routes/optimiser.py`` mlflow_log raises ``400`` (Bad Request)
    - ``routes/mlflow.py`` _ensure_tracking raises ``503`` (Service Unavailable)

    503 is semantically correct (a dependency is unavailable), while 400
    implies the client sent a bad request.  This test documents the
    inconsistency so it is caught if someone "fixes" only one side.

    When harmonising, update BOTH routes to the same status code and
    update both assertions below.
    """

    def test_optimiser_mlflow_log_returns_400_when_mlflow_missing(
        self,
        client: TestClient,
    ) -> None:
        from haute.routes.optimiser import _store

        snapshot = dict(_store.jobs)
        try:
            _store.jobs["inc_test"] = {
                "status": "completed",
                "solver": MagicMock(),
                "solve_result": MagicMock(),
                "config": {},
                "node_label": "opt",
                "created_at": time.time(),
            }
            with patch.dict("sys.modules", {"mlflow": None}):
                resp = client.post(
                    "/api/optimiser/mlflow/log",
                    json={"job_id": "inc_test"},
                )
            assert resp.status_code == 400, (
                "optimiser mlflow_log changed its missing-mlflow status code -- "
                "update this test AND harmonise with routes/mlflow.py"
            )
            assert "not installed" in resp.json()["detail"].lower()
        finally:
            _store.jobs.clear()
            _store.jobs.update(snapshot)

    def test_mlflow_routes_return_503_when_mlflow_missing(
        self,
        client: TestClient,
    ) -> None:
        with patch.dict("sys.modules", {"mlflow": None}):
            resp = client.get("/api/mlflow/experiments")
        assert resp.status_code == 503, (
            "mlflow routes changed their missing-mlflow status code -- "
            "update this test AND harmonise with routes/optimiser.py"
        )
        assert "not installed" in resp.json()["detail"].lower()


# =====================================================================
# GAP tests: Sensitive info leakage  (parametrized where possible)
# =====================================================================

# Each case: (http_method, url, req_kwargs, patch_target, error_msg,
#              expected_status, forbidden_substrings, detail_check)

_LEAKAGE_CASES: list[tuple] = [
    # GAP 1: File path leakage
    pytest.param(
        "get",
        "/api/schema",
        None,
        "haute.graph_utils.read_source",
        "ArrowInvalid: /srv/app/data/cache/data.csv: invalid magic bytes",
        500,
        ["/srv/app", "ArrowInvalid"],
        id="file-path-schema-read-oserror",
    ),
    pytest.param(
        "get",
        "/api/schema",
        None,
        "haute.graph_utils.read_source",
        "ImportError: /usr/local/lib/python3.11/lib-dynload/"
        "_csv.cpython-311-x86_64-linux-gnu.so: undefined symbol: PyFloat_Type",
        500,
        ["python3.11", "x86_64", "lib-dynload"],
        id="platform-info-schema-read",
    ),
    # GAP 2: Stack trace leakage -- git status
    pytest.param(
        "get",
        "/api/git/status",
        None,
        "haute.routes.git.get_status",
        "subprocess.CalledProcessError: Command git status returned "
        "non-zero exit status 128.\n"
        '  File "/usr/local/lib/python3.11/subprocess.py", line 571, in run',
        500,
        ["subprocess.py", "python3.11"],
        id="stack-trace-git-status",
    ),
    # GAP 3: Environment variable leakage
    pytest.param(
        "get",
        "/api/databricks/warehouses",
        None,
        lambda err: _databricks_client_patch("warehouses.list", err),
        "AuthenticationError: invalid token dapi_test_token "
        "for host https://test.cloud.databricks.com",
        500,
        ["dapi_test_token", "test.cloud.databricks.com"],
        id="env-var-warehouses",
    ),
    pytest.param(
        "post",
        "/api/databricks/fetch",
        {"json": {"table": "cat.sch.tbl", "http_path": "/sql/wh"}},
        lambda err: patch("haute._databricks_io.fetch_and_cache", side_effect=RuntimeError(err)),
        "ConnectionError: HTTPSConnectionPool(host=test.cloud.databricks.com, "
        "port=443): Max retries exceeded with url: /sql/1.0/warehouses/abc123",
        500,
        ["test.cloud.databricks.com", "abc123"],
        id="env-var-fetch-connection",
    ),
    # GAP 4: Database connection string / MLflow tracking URI leakage
    pytest.param(
        "get",
        "/api/mlflow/experiments",
        None,
        lambda err: _mlflow_tracking_patch("search_experiments", err),
        "OperationalError: unable to open database file: sqlite:////home/user/mlruns/mlflow.db",
        502,
        ["sqlite://", "/home/user", "mlflow.db"],
        id="db-conn-mlflow-tracking-uri",
    ),
]


class TestSensitiveInfoLeakage:
    """Verify that sensitive information (file paths, stack traces, env vars,
    connection strings, platform info) never appears in HTTP error responses."""

    @pytest.mark.parametrize(
        "method,url,req_kwargs,patch_target,error_msg,expected_status,forbidden",
        _LEAKAGE_CASES,
    )
    def test_no_leakage(
        self,
        client: TestClient,
        tmp_path: Path,
        method: str,
        url: str,
        req_kwargs: dict | None,
        patch_target,
        error_msg: str,
        expected_status: int,
        forbidden: list[str],
    ) -> None:
        req_kwargs = req_kwargs or {}

        # Some endpoints need a real file to exist for path param
        if url == "/api/schema" and "json" not in req_kwargs:
            target = tmp_path / "data.csv"
            target.write_text("a,b\n1,2\n")
            req_kwargs = {"path": str(target)}

        if "json" not in req_kwargs:
            call_kwargs = {"params": req_kwargs} if req_kwargs else {}
        else:
            call_kwargs = req_kwargs

        # patch_target can be a string or a callable returning a context manager
        if callable(patch_target) and not isinstance(patch_target, str):
            cm = patch_target(error_msg)
        else:
            cm = patch(patch_target, side_effect=RuntimeError(error_msg))

        with cm:
            resp = getattr(client, method)(url, **call_kwargs)

        assert resp.status_code == expected_status
        detail = resp.json()["detail"]
        for substr in forbidden:
            assert substr not in detail
        assert "Check the server logs" in detail

    # -- Cases that need special setup (not easily parametrizable) --

    def test_databricks_schema_read_path_leak(self, client: TestClient) -> None:
        """GET /api/schema/databricks -- parquet read failure must not leak cache path."""
        with patch(
            "haute._databricks_io.cached_path",
            return_value=Path("/home/deploy/.haute_cache/cat.sch.tbl.parquet"),
        ):
            with patch(
                "polars.scan_parquet",
                side_effect=OSError(
                    "No such file or directory: '/home/deploy/.haute_cache/cat.sch.tbl.parquet'"
                ),
            ):
                resp = client.get("/api/schema/databricks", params={"table": "cat.sch.tbl"})
        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert "/home/deploy" not in detail
        assert ".haute_cache" not in detail
        assert "Check the server logs" in detail

    @pytest.mark.xfail(
        reason="Known gap: list_pipelines passes raw str(e) into PipelineSummary.error "
        "(pipeline.py line 64). Absolute paths from parse exceptions leak to the client.",
        strict=True,
    )
    def test_list_pipelines_parse_error_no_absolute_path(
        self,
        client: TestClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        bad_py = tmp_path / "bad_pipeline.py"
        bad_py.write_text(
            "import haute\npipeline = haute.Pipeline('bad')\n"
            "@pipeline.data_source(path='/nonexistent')\ndef src(config): pass\n"
        )

        from haute.server import app

        c = TestClient(app)
        with patch(
            "haute.parser.parse_pipeline_file",
            side_effect=RuntimeError(
                f"SyntaxError in {tmp_path / 'bad_pipeline.py'}: invalid token"
            ),
        ):
            resp = c.get("/api/pipelines")
        assert resp.status_code == 200
        for item in resp.json():
            if item.get("error"):
                assert str(tmp_path) not in item["error"], (
                    f"Absolute path leaked in pipeline list error: {item['error']}"
                )

    def test_trace_deep_exception_no_traceback_frames(
        self, client: TestClient, pipeline_graph
    ) -> None:
        """POST /api/pipeline/trace -- deep exception must not leak stack frames."""
        deep_error = (
            "Traceback (most recent call last):\n"
            '  File "/usr/lib/python3.11/site-packages/polars/internals/frame.py", line 42\n'
            "    in _collect\n"
            "RuntimeError: out of memory"
        )
        with patch(
            "haute.trace.execute_trace",
            side_effect=RuntimeError(deep_error),
        ):
            resp = client.post(
                "/api/pipeline/trace",
                json={"graph": pipeline_graph.model_dump(), "row_index": 0},
            )
        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert "site-packages" not in detail
        assert "File " not in detail
        assert "Traceback" not in detail
        assert detail == _SAFE_DETAIL

    def test_preview_traceback_string_no_leak(self, client: TestClient, pipeline_graph) -> None:
        """POST /api/pipeline/preview -- traceback-containing error must not leak."""
        node_id = pipeline_graph.nodes[0].id
        with patch(
            "haute.executor.execute_graph",
            side_effect=RuntimeError(
                'File "/app/src/haute/executor.py", line 312, in _exec_user_code\n'
                "  exec(exec_code, safe_globals(pl=pl), local_ns)\n"
                "NameError: name 'undefined_var' is not defined"
            ),
        ):
            resp = client.post(
                "/api/pipeline/preview",
                json={"graph": pipeline_graph.model_dump(), "node_id": node_id},
            )
        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert "executor.py" not in detail
        assert "exec(" not in detail
        assert "safe_globals" not in detail
        assert detail == _SAFE_DETAIL

    def test_sink_cpython_error_no_leak(self, client: TestClient) -> None:
        """POST /api/pipeline/sink -- CPython info in error must not leak."""
        graph = _minimal_source_graph()
        with patch(
            "haute.executor.execute_sink",
            side_effect=RuntimeError(
                "SystemError: CPython 3.11.5 (default, Sep 11 2023) "
                "[GCC 12.2.0] on linux: frame object is garbage collected"
            ),
        ):
            resp = client.post("/api/pipeline/sink", json={"graph": graph, "node_id": "src"})
        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert "CPython" not in detail
        assert "3.11.5" not in detail
        assert "GCC" not in detail
        assert detail == _SAFE_DETAIL

    def test_optimiser_mlflow_log_tracking_uri_no_leak(self, client: TestClient) -> None:
        """POST /api/optimiser/mlflow/log -- databricks:// URI must not leak."""
        from haute.routes.optimiser import _store

        snapshot = dict(_store.jobs)
        try:
            _store.jobs["test_uri_leak"] = {
                "status": "completed",
                "solver": MagicMock(),
                "solve_result": MagicMock(),
                "config": {},
                "node_label": "opt",
                "created_at": time.time(),
            }
            with patch.dict("sys.modules", {"mlflow": MagicMock()}):
                with patch(
                    "haute.modelling._mlflow_log.resolve_tracking_backend",
                    side_effect=RuntimeError(
                        "ConnectionError: databricks://token:dapi_secret@"
                        "acme.cloud.databricks.com/tracking"
                    ),
                ):
                    resp = client.post(
                        "/api/optimiser/mlflow/log",
                        json={"job_id": "test_uri_leak"},
                    )
            assert resp.status_code == 500
            detail = resp.json()["detail"]
            assert "dapi_secret" not in detail
            assert "acme.cloud.databricks.com" not in detail
            assert detail == _SAFE_DETAIL
        finally:
            _store.jobs.clear()
            _store.jobs.update(snapshot)

    def test_modelling_mlflow_log_postgres_uri_no_leak(self, client: TestClient) -> None:
        """POST /api/modelling/mlflow/log -- postgres connection string must not leak."""
        from haute.routes.modelling import _store

        _store.jobs["test_pg_leak"] = {
            "status": "completed",
            "result": SimpleNamespace(
                metrics={},
                model_path=None,
                diagnostics={},
                metadata={},
                feature_importance=None,
                shap_summary=None,
                feature_importance_loss=None,
                double_lift=None,
                loss_history=None,
                cv_results=None,
                ave_per_feature=None,
                residuals_histogram=None,
                residuals_stats=None,
                actual_vs_predicted=None,
                lorenz_curve=None,
                lorenz_curve_perfect=None,
                pdp_data=None,
                holdout_metrics=None,
                diagnostics_set=None,
                train_rows=100,
                test_rows=20,
                holdout_rows=10,
                features=["x"],
                best_iteration=50,
            ),
            "config": {},
            "node_label": "model",
            "created_at": time.time(),
        }
        try:
            with patch(
                "haute.modelling._mlflow_log.log_experiment",
                side_effect=RuntimeError(
                    "OperationalError: FATAL: password authentication failed for user "
                    "mlflow_admin on host db.internal.corp:5432 database mlflow_prod"
                ),
            ):
                resp = client.post(
                    "/api/modelling/mlflow/log",
                    json={"job_id": "test_pg_leak"},
                )
            assert resp.status_code == 500
            detail = resp.json()["detail"]
            assert "mlflow_admin" not in detail
            assert "db.internal.corp" not in detail
            assert "5432" not in detail
            assert detail == _SAFE_DETAIL
        finally:
            _store.jobs.pop("test_pg_leak", None)


# =====================================================================
# GAP 6: User code error vs sandbox internals
# =====================================================================


class TestUserCodeErrorSanitization:
    """Verify that node execution errors show the user code error but NOT
    the wrapping exec()/sandbox internals."""

    def test_user_code_nameerror_no_exec_frame(self) -> None:
        from haute.executor import _exec_user_code

        with pytest.raises(NameError) as exc_info:
            _exec_user_code(
                code="df = df.filter(pl.col('x') > threshold)",
                src_names=["df"],
                dfs=(pl.DataFrame({"x": [1, 2, 3]}).lazy(),),
            )
        error_msg = str(exc_info.value)
        assert "threshold" in error_msg
        assert "safe_globals" not in error_msg
        assert "exec_code" not in error_msg
        assert "exec(" not in error_msg

    def test_user_code_typeerror_no_sandbox_path(self) -> None:
        from haute.executor import _exec_user_code

        with pytest.raises(TypeError) as exc_info:
            _exec_user_code(
                code="df + 'string'",
                src_names=["df"],
                dfs=(pl.DataFrame({"x": [1, 2, 3]}).lazy(),),
            )
        error_msg = str(exc_info.value)
        assert "_sandbox" not in error_msg
        assert "safe_globals" not in error_msg

    def test_preview_node_error_shows_user_msg_not_internals(self, client: TestClient) -> None:
        from haute.schemas import NodeResult

        graph = _source_and_transform_graph(
            code="df = df.filter(pl.col('x') > threshold)",
        )

        mock_results = {
            "src": NodeResult(status="ok", row_count=3, columns=[], preview=[]),
            "txn": NodeResult(
                status="error",
                error="NameError: name 'threshold' is not defined",
            ),
        }

        with patch("haute.executor.execute_graph", return_value=mock_results):
            resp = client.post(
                "/api/pipeline/preview",
                json={"graph": graph, "node_id": "txn"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] is not None
        assert "threshold" in body["error"]
        assert "_sandbox.py" not in body["error"]
        assert "exec(" not in body["error"]


# =====================================================================
# GAP 7: Preamble compilation errors
# =====================================================================


class TestPreambleErrorSanitization:
    """Verify that preamble compilation errors show the user syntax/import
    error but NOT the full exec() traceback or server paths."""

    def test_preamble_syntax_error_no_exec_traceback(self) -> None:
        from haute._sandbox import UnsafeCodeError
        from haute.executor import PreambleError, _compile_preamble

        with pytest.raises((PreambleError, UnsafeCodeError)) as exc_info:
            _compile_preamble("def broken(\n")

        error_msg = str(exc_info.value)
        assert "syntax" in error_msg.lower() or "line" in error_msg.lower()
        assert "exec(" not in error_msg
        assert "safe_globals" not in error_msg
        assert "executor.py" not in error_msg

    def test_preamble_nameerror_no_server_path(self, tmp_path: Path) -> None:
        from haute.executor import PreambleError, _compile_preamble

        utility_dir = tmp_path / "utility"
        utility_dir.mkdir()
        (utility_dir / "__init__.py").write_text("")
        (utility_dir / "broken.py").write_text("result = undefined_var + 1\n")

        import os
        import sys

        old_cwd = os.getcwd()
        old_path = sys.path[:]
        try:
            os.chdir(tmp_path)
            if str(tmp_path) not in sys.path:
                sys.path.insert(0, str(tmp_path))

            with pytest.raises(PreambleError) as exc_info:
                _compile_preamble("from utility.broken import result\n")

            error_msg = str(exc_info.value)
            assert "broken" in error_msg or "undefined_var" in error_msg
            assert str(tmp_path) not in error_msg or "utility" in error_msg
            assert "exec(" not in error_msg
            assert "safe_globals" not in error_msg
        finally:
            os.chdir(old_cwd)
            sys.path[:] = old_path
            for mod_name in [k for k in sys.modules if k.startswith("utility")]:
                del sys.modules[mod_name]

    def test_preamble_error_in_preview_no_exec_frame(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        from haute.schemas import NodeResult

        graph = _source_and_transform_graph(transform_label="my_transform")

        mock_results = {
            "src": NodeResult(status="ok", row_count=3, columns=[], preview=[]),
            "txn": NodeResult(
                status="error",
                error="Import/preamble error: NameError: name 'undefined' is not defined",
            ),
        }

        with patch("haute.executor.execute_graph", return_value=mock_results):
            resp = client.post(
                "/api/pipeline/preview",
                json={"graph": graph, "node_id": "txn"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] is not None
        assert "undefined" in body["error"]
        assert 'File "' not in body["error"]
        assert "exec(" not in body["error"]
        assert "site-packages" not in body["error"]


# =====================================================================
# GAP 2+5 combined: All Git routes with platform-info errors
# =====================================================================


class TestGitRoutesPlatformLeakage:
    """Verify that ALL git endpoints sanitize errors containing platform info."""

    @pytest.mark.parametrize(
        "method,path,kwargs",
        [
            ("get", "/api/git/branches", {}),
            ("post", "/api/git/save", {}),
            ("post", "/api/git/submit", {}),
            ("get", "/api/git/history", {}),
            ("post", "/api/git/pull", {}),
        ],
        ids=["branches", "save", "submit", "history", "pull"],
    )
    def test_git_route_no_platform_leak(
        self,
        client: TestClient,
        method: str,
        path: str,
        kwargs: dict,
    ) -> None:
        fn_map = {
            "/api/git/branches": "haute.routes.git.list_branches",
            "/api/git/save": "haute.routes.git.save_progress",
            "/api/git/submit": "haute.routes.git.submit_for_review",
            "/api/git/history": "haute.routes.git.get_history",
            "/api/git/pull": "haute.routes.git.pull_latest",
        }
        target_fn = fn_map[path]
        with patch(
            target_fn,
            side_effect=RuntimeError(
                "fatal: unable to access https://github.com/org/repo.git/: "
                "SSL certificate problem: unable to get local issuer certificate\n"
                "Python 3.11.5 on win32 / Git 2.42.0.windows.2"
            ),
        ):
            resp = getattr(client, method)(path, **kwargs)
        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert "Python 3.11" not in detail
        assert "win32" not in detail
        assert "Git 2.42" not in detail
        assert "github.com/org/repo" not in detail
        assert detail == _SAFE_DETAIL
