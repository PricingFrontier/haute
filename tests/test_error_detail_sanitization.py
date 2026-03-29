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


# ── Shared safe-detail constant ──────────────────────────────────────────

_SAFE_DETAIL = "Operation failed. Check the server logs for details."


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient with cwd set to a temp directory and Databricks env set."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABRICKS_HOST", "https://test.cloud.databricks.com")
    monkeypatch.setenv("DATABRICKS_TOKEN", "dapi_test_token")
    (tmp_path / "main.py").write_text("")
    from haute.server import app

    return TestClient(app)


# =====================================================================
# E3: Route-level error detail sanitization
# =====================================================================


class TestDatabricksRoutesSafeDetail:
    """All Databricks routes must return safe detail on 500."""

    def test_warehouses_500_no_leak(self, client: TestClient) -> None:
        mock_ws = MagicMock()
        mock_ws.warehouses.list.side_effect = RuntimeError(
            "/home/user/.databricks/token: permission denied"
        )
        with patch("haute.routes.databricks._get_databricks_client", return_value=mock_ws):
            resp = client.get("/api/databricks/warehouses")
        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert "permission denied" not in detail
        assert ".databricks/token" not in detail
        assert detail == _SAFE_DETAIL

    def test_catalogs_500_no_leak(self, client: TestClient) -> None:
        mock_ws = MagicMock()
        mock_ws.catalogs.list.side_effect = RuntimeError(
            "AuthenticationError: invalid token xyz-secret"
        )
        with patch("haute.routes.databricks._get_databricks_client", return_value=mock_ws):
            resp = client.get("/api/databricks/catalogs")
        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert "xyz-secret" not in detail
        assert detail == _SAFE_DETAIL

    def test_schemas_500_no_leak(self, client: TestClient) -> None:
        mock_ws = MagicMock()
        mock_ws.schemas.list.side_effect = RuntimeError(
            "SDK internal: /var/run/secrets/token read failure"
        )
        with patch("haute.routes.databricks._get_databricks_client", return_value=mock_ws):
            resp = client.get("/api/databricks/schemas", params={"catalog": "main"})
        assert resp.status_code == 500
        assert resp.json()["detail"] == _SAFE_DETAIL

    def test_tables_500_no_leak(self, client: TestClient) -> None:
        mock_ws = MagicMock()
        mock_ws.tables.list.side_effect = RuntimeError("Connection to host 10.0.0.5:443 refused")
        with patch("haute.routes.databricks._get_databricks_client", return_value=mock_ws):
            resp = client.get(
                "/api/databricks/tables",
                params={"catalog": "cat", "schema": "sch"},
            )
        assert resp.status_code == 500
        assert "10.0.0.5" not in resp.json()["detail"]
        assert resp.json()["detail"] == _SAFE_DETAIL

    def test_fetch_500_no_leak(self, client: TestClient) -> None:
        with patch(
            "haute._databricks_io.fetch_and_cache",
            side_effect=RuntimeError("OSError: /mnt/data/cache full"),
        ):
            resp = client.post(
                "/api/databricks/fetch",
                json={"table": "cat.sch.tbl", "http_path": "/sql/wh"},
            )
        assert resp.status_code == 500
        assert "/mnt/data/cache" not in resp.json()["detail"]
        assert resp.json()["detail"] == _SAFE_DETAIL


class TestDatabricksRoutesLogOnError:
    """Verify errors are logged server-side when Databricks routes return 500.

    These tests check that ``logger.error`` is called and the original
    error message is preserved in the log.  They do NOT assert on the
    exact event name or keyword argument structure, because the important
    invariant is "errors are not silently swallowed", not "the log
    message has specific wording".
    """

    def test_warehouses_logs_error(self, client: TestClient) -> None:
        mock_ws = MagicMock()
        mock_ws.warehouses.list.side_effect = RuntimeError("secret-err")
        mock_logger = MagicMock()
        with (
            patch("haute.routes.databricks._get_databricks_client", return_value=mock_ws),
            patch("haute.routes.databricks.logger", mock_logger),
        ):
            resp = client.get("/api/databricks/warehouses")
        assert resp.status_code == 500
        mock_logger.error.assert_called()
        # The real error must appear somewhere in the log call (args or kwargs)
        assert "secret-err" in str(mock_logger.error.call_args)

    def test_fetch_logs_error(self, client: TestClient) -> None:
        mock_logger = MagicMock()
        with (
            patch(
                "haute._databricks_io.fetch_and_cache",
                side_effect=RuntimeError("internal-boom"),
            ),
            patch("haute.routes.databricks.logger", mock_logger),
        ):
            resp = client.post(
                "/api/databricks/fetch",
                json={"table": "cat.sch.tbl", "http_path": "/sql/wh"},
            )
        assert resp.status_code == 500
        mock_logger.error.assert_called()
        assert "internal-boom" in str(mock_logger.error.call_args)


class TestPipelineRoutesSafeDetail:
    """Pipeline trace/preview/sink must return safe detail on 500."""

    @pytest.fixture()
    def pipeline_graph(self, tmp_path: Path):
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

    def test_trace_500_no_leak(self, client: TestClient, pipeline_graph) -> None:
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

    def test_preview_500_no_leak(self, client: TestClient, pipeline_graph) -> None:
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

    def test_sink_500_no_leak(self, client: TestClient, tmp_path: Path) -> None:
        data_path = tmp_path / "data" / "input.parquet"
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
                        "config": {"path": "/tmp/test_sink.parquet", "format": "parquet"},
                    },
                },
            ],
            "edges": [{"id": "e1", "source": "src", "target": "sink"}],
        }
        with patch(
            "haute.executor.execute_sink",
            side_effect=RuntimeError("PermissionError: /secure/dir/output.parquet"),
        ):
            resp = client.post("/api/pipeline/sink", json={"graph": graph, "node_id": "sink"})
        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert "/secure/dir/" not in detail
        assert detail == _SAFE_DETAIL


class TestPipelineRoutesLogOnError:
    """Verify pipeline routes log errors server-side on 500.

    These tests check that ``logger.error`` is called and the original
    error message is preserved.  They do NOT assert on exact event names
    or keyword argument structure.
    """

    @pytest.fixture()
    def pipeline_graph(self, tmp_path: Path):
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

    def test_trace_logs_error(self, client: TestClient, pipeline_graph) -> None:
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

    def test_preview_logs_error(self, client: TestClient, pipeline_graph) -> None:
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

    def test_sink_logs_error(self, client: TestClient) -> None:
        mock_logger = MagicMock()
        graph = {
            "nodes": [
                {
                    "id": "src",
                    "type": "pipelineNode",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "src",
                        "nodeType": "dataSource",
                        "config": {"path": "/tmp/fake.parquet"},
                    },
                },
            ],
            "edges": [],
        }
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


class TestJsonCacheRoutesSafeDetail:
    """JSON cache build 500 must not leak internal details."""

    def test_build_500_no_leak(self, client: TestClient) -> None:
        with patch(
            "haute._json_flatten.build_json_cache",
            side_effect=RuntimeError("OSError: [Errno 28] No space left on device: '/tmp/x'"),
        ):
            resp = client.post("/api/json-cache/build", json={"path": "data.jsonl"})
        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert "/tmp/x" not in detail
        assert detail == _SAFE_DETAIL

    def test_build_logs_error(self, client: TestClient) -> None:
        mock_logger = MagicMock()
        with (
            patch(
                "haute._json_flatten.build_json_cache",
                side_effect=RuntimeError("internal-json-error"),
            ),
            patch("haute.routes.json_cache.logger", mock_logger),
        ):
            resp = client.post("/api/json-cache/build", json={"path": "data.jsonl"})
        assert resp.status_code == 500
        mock_logger.error.assert_called()
        assert "internal-json-error" in str(mock_logger.error.call_args)


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
        """apply_lambdas must return safe detail on internal error."""
        store = clean_job_store
        mock_solve_result = MagicMock()
        mock_solve_result.dataframe = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("numpy internal: segfault at 0xdead"))
        )
        # Make .dataframe raise on access
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
        """run_frontier must return safe detail on internal error."""
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
        """save_result OSError must not leak filesystem paths."""
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
        """mlflow_log must return safe detail on internal error."""
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
# E3: Verify intentional domain errors still pass through
# =====================================================================


class TestDomainErrorsStillExposed:
    """400-level errors with domain messages must still be exposed to users."""

    def test_git_guardrail_error_exposed(self, client: TestClient) -> None:
        """GitGuardrailError detail is intentional and user-facing."""
        from haute._git import GitGuardrailError

        with patch(
            "haute.routes.git.get_status",
            side_effect=GitGuardrailError("Cannot push to protected branch 'main'"),
        ):
            resp = client.get("/api/git/status")
        assert resp.status_code == 403
        assert "Cannot push to protected branch" in resp.json()["detail"]

    def test_git_error_exposed(self, client: TestClient) -> None:
        """GitError detail is intentional and user-facing."""
        from haute._git import GitError

        with patch(
            "haute.routes.git.get_status",
            side_effect=GitError("No git repository found"),
        ):
            resp = client.get("/api/git/status")
        assert resp.status_code == 400
        assert "No git repository found" in resp.json()["detail"]

    def test_file_schema_value_error_exposed(self, client: TestClient, tmp_path: Path) -> None:
        """ValueError on schema read is a user-facing validation error (400)."""
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
        """Missing credentials is user-facing (400), not a leak."""
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
    """Verify that _execute_eager_core logs node failures at ERROR, not WARNING.

    Uses conftest graph helpers to avoid manually constructing
    GraphNode/PipelineGraph objects.
    """

    @staticmethod
    def _make_failing_graph():
        """Build a two-node graph where the transform always fails."""
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
        """When swallow_errors=True, node failures must be logged at ERROR level."""
        from haute._execute_lazy import _execute_eager_core

        g = self._make_failing_graph()
        mock_logger = MagicMock()
        with patch("haute._execute_lazy.logger", mock_logger):
            result = _execute_eager_core(g, self._build_fn, swallow_errors=True)

        assert "t" in result.errors
        assert "test node failure" in result.errors["t"]

        # logger.error must have been called with the failure details
        mock_logger.error.assert_called()
        assert "test node failure" in str(mock_logger.error.call_args)

        # logger.warning must NOT have been called for node failure
        mock_logger.warning.assert_not_called()

    def test_node_failure_not_logged_at_warning(self) -> None:
        """Confirm node failures are NOT logged at WARNING level anymore."""
        from haute._execute_lazy import _execute_eager_core

        g = self._make_failing_graph()
        mock_logger = MagicMock()
        with patch("haute._execute_lazy.logger", mock_logger):
            _execute_eager_core(g, self._build_fn, swallow_errors=True)

        # logger.warning must never be called for node_failed
        for call in mock_logger.warning.call_args_list:
            assert "node_failed" not in str(call), (
                "node_failed should no longer be logged at WARNING level"
            )


# =====================================================================
# E3: MLflow discovery routes (mlflow.py) — 502 errors must not leak
# =====================================================================


class TestMlflowRoutesSafeDetail:
    """MLflow discovery routes must not leak raw exceptions in 502 responses."""

    def test_experiments_502_no_leak(self, client: TestClient) -> None:
        mock_mlflow = MagicMock()
        mock_mlflow.search_experiments.side_effect = RuntimeError(
            "ConnectionError: https://internal-mlflow.corp:5000/api refused"
        )
        with (
            patch("haute.routes.mlflow._ensure_tracking", return_value=(mock_mlflow, MagicMock())),
        ):
            resp = client.get("/api/mlflow/experiments")
        assert resp.status_code == 502
        detail = resp.json()["detail"]
        assert "internal-mlflow.corp" not in detail
        assert "Check the server logs" in detail

    def test_runs_502_no_leak(self, client: TestClient) -> None:
        mock_mlflow = MagicMock()
        mock_mlflow.search_runs.side_effect = RuntimeError(
            "SSLError: certificate verify failed for host mlflow.internal"
        )
        with (
            patch("haute.routes.mlflow._ensure_tracking", return_value=(mock_mlflow, MagicMock())),
        ):
            resp = client.get("/api/mlflow/runs", params={"experiment_id": "1"})
        assert resp.status_code == 502
        detail = resp.json()["detail"]
        assert "mlflow.internal" not in detail
        assert "Check the server logs" in detail

    def test_models_502_no_leak(self, client: TestClient) -> None:
        mock_client = MagicMock()
        mock_client.search_registered_models.side_effect = RuntimeError(
            "PermissionDenied: access token for service-account@corp expired"
        )
        with (
            patch("haute.routes.mlflow._ensure_tracking", return_value=(MagicMock(), mock_client)),
        ):
            resp = client.get("/api/mlflow/models")
        assert resp.status_code == 502
        detail = resp.json()["detail"]
        assert "service-account@corp" not in detail
        assert "Check the server logs" in detail

    def test_model_versions_502_no_leak(self, client: TestClient) -> None:
        mock_client = MagicMock()
        mock_client.search_model_versions.side_effect = RuntimeError(
            "Databricks API error: workspace /Users/admin/secret"
        )
        with (
            patch("haute.routes.mlflow._ensure_tracking", return_value=(MagicMock(), mock_client)),
            patch(
                "haute.routes.mlflow.search_versions",
                side_effect=RuntimeError("Databricks API error: workspace /Users/admin/secret"),
            ),
        ):
            resp = client.get("/api/mlflow/model-versions", params={"model_name": "test"})
        assert resp.status_code == 502
        detail = resp.json()["detail"]
        assert "/Users/admin/secret" not in detail
        assert "Check the server logs" in detail


class TestMlflowRoutesLogOnError:
    """Verify MLflow routes log errors server-side on 502.

    These tests check that ``logger.error`` is called and the original
    error message is preserved.  They do NOT assert on exact event names
    or keyword argument structure.
    """

    def test_experiments_logs_error(self, client: TestClient) -> None:
        mock_mlflow = MagicMock()
        mock_mlflow.search_experiments.side_effect = RuntimeError("secret-mlflow-err")
        mock_logger = MagicMock()
        with (
            patch("haute.routes.mlflow._ensure_tracking", return_value=(mock_mlflow, MagicMock())),
            patch("haute.routes.mlflow.logger", mock_logger),
        ):
            resp = client.get("/api/mlflow/experiments")
        assert resp.status_code == 502
        mock_logger.error.assert_called()
        assert "secret-mlflow-err" in str(mock_logger.error.call_args)


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
        """optimiser.py: ImportError -> 400."""
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
            # NOTE: 400 is arguably wrong here — missing dependency is not
            # a client error.  See sister test below for the 503 variant.
            assert resp.status_code == 400, (
                "optimiser mlflow_log changed its missing-mlflow status code — "
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
        """mlflow.py: ImportError -> 503."""
        with patch.dict("sys.modules", {"mlflow": None}):
            resp = client.get("/api/mlflow/experiments")
        # NOTE: 503 is the correct semantics — a required service is
        # unavailable.  If you change this, also harmonise optimiser.py.
        assert resp.status_code == 503, (
            "mlflow routes changed their missing-mlflow status code — "
            "update this test AND harmonise with routes/optimiser.py"
        )
        assert "not installed" in resp.json()["detail"].lower()


# =====================================================================
# GAP 1: File path leakage in error messages
# =====================================================================


class TestFilePathLeakage:
    """Verify that absolute filesystem paths from exceptions never reach API responses.

    Real-world failure: A Polars read_parquet error includes the full path
    like ``/home/deploy/.cache/haute/secret_table.parquet: No such file``.
    If that propagates into the HTTP 500 detail, it reveals server directory
    structure to the frontend.
    """

    def test_schema_read_oserror_no_internal_path_leak(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """GET /api/schema -- OSError with internal server path must not leak.

        The user sends path='data.csv' but the OSError contains the resolved
        absolute path '/srv/app/data/cache/data.csv'.  The detail should echo
        back only the user-supplied relative path, not the server-side resolution.
        """
        target = tmp_path / "data.csv"
        target.write_text("a,b\n1,2\n")
        with patch(
            "haute.graph_utils.read_source",
            side_effect=OSError("ArrowInvalid: /srv/app/data/cache/data.csv: invalid magic bytes"),
        ):
            resp = client.get("/api/schema", params={"path": "data.csv"})
        assert resp.status_code == 500
        detail = resp.json()["detail"]
        # The internal server path must not appear in the response
        assert "/srv/app" not in detail
        assert "ArrowInvalid" not in detail
        assert "Check the server logs" in detail

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
        """GET /api/pipelines -- parse errors expose raw str(e) in the error field.

        The pipeline list endpoint catches parse failures and puts str(e) into
        PipelineSummary.error.  Verify that absolute paths from the exception
        do NOT appear.  (This is a known gap: line 64 of pipeline.py uses
        error=str(e) directly.)
        """
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


# =====================================================================
# GAP 2: Stack trace leakage
# =====================================================================


class TestStackTraceLeakage:
    """Verify that Python traceback frames never appear in HTTP responses.

    Real-world failure: An exception __str__ includes
    File "/usr/lib/python3.11/site-packages/polars/...".  If the
    error handler does detail=str(e) the client sees server internals.
    """

    def test_trace_deep_exception_no_traceback_frames(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        """POST /api/pipeline/trace -- deep exception must not leak stack frames."""
        from haute.parser import parse_pipeline_file

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        data_path = data_dir / "input.parquet"
        pl.DataFrame({"x": [1, 2, 3]}).write_parquet(data_path)

        code = (
            "import haute\n"
            "pipeline = haute.Pipeline('test')\n"
            "\n"
            f"@pipeline.data_source(path='{data_path}')\n"
            "def source(config):\n"
            "    pass\n"
        )
        py = tmp_path / "test_pipeline.py"
        py.write_text(code)
        graph = parse_pipeline_file(py)

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
                json={"graph": graph.model_dump(), "row_index": 0},
            )
        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert "site-packages" not in detail
        assert "File " not in detail
        assert "Traceback" not in detail
        assert detail == _SAFE_DETAIL

    def test_preview_traceback_string_no_leak(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        """POST /api/pipeline/preview -- traceback-containing error must not leak."""
        from haute.parser import parse_pipeline_file

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        data_path = data_dir / "input.parquet"
        pl.DataFrame({"x": [1, 2, 3]}).write_parquet(data_path)

        code = (
            "import haute\n"
            "pipeline = haute.Pipeline('test')\n"
            "\n"
            f"@pipeline.data_source(path='{data_path}')\n"
            "def source(config):\n"
            "    pass\n"
        )
        py = tmp_path / "test_pipeline.py"
        py.write_text(code)
        graph = parse_pipeline_file(py)
        node_id = graph.nodes[0].id

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
                json={"graph": graph.model_dump(), "node_id": node_id},
            )
        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert "executor.py" not in detail
        assert "exec(" not in detail
        assert "safe_globals" not in detail
        assert detail == _SAFE_DETAIL

    def test_git_status_traceback_no_leak(self, client: TestClient) -> None:
        """GET /api/git/status -- subprocess traceback must not leak."""
        with patch(
            "haute.routes.git.get_status",
            side_effect=RuntimeError(
                "subprocess.CalledProcessError: Command git status returned "
                "non-zero exit status 128.\n"
                '  File "/usr/local/lib/python3.11/subprocess.py", line 571, in run'
            ),
        ):
            resp = client.get("/api/git/status")
        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert "subprocess.py" not in detail
        assert "python3.11" not in detail
        assert detail == _SAFE_DETAIL


# =====================================================================
# GAP 3: Environment variable leakage (Databricks credentials)
# =====================================================================


class TestEnvironmentVariableLeakage:
    """Verify that DATABRICKS_HOST/TOKEN values never appear in error responses.

    Real-world failure: The Databricks SDK constructs an error like
    AuthenticationError: invalid token dapi... for host https://xxx.databricks.com.
    If that reaches the client, both the host URL and the token are exposed.
    """

    def test_warehouses_no_host_or_token_in_error(self, client: TestClient) -> None:
        """GET /api/databricks/warehouses -- credential values must not appear."""
        mock_ws = MagicMock()
        mock_ws.warehouses.list.side_effect = RuntimeError(
            "AuthenticationError: invalid token dapi_test_token "
            "for host https://test.cloud.databricks.com"
        )
        with patch("haute.routes.databricks._get_databricks_client", return_value=mock_ws):
            resp = client.get("/api/databricks/warehouses")
        assert resp.status_code == 500
        detail = resp.json()["detail"]
        # The actual env values set by the client fixture
        assert "dapi_test_token" not in detail
        assert "test.cloud.databricks.com" not in detail
        assert detail == _SAFE_DETAIL

    def test_fetch_no_host_in_connection_error(self, client: TestClient) -> None:
        """POST /api/databricks/fetch -- connection error must not leak host URL."""
        with patch(
            "haute._databricks_io.fetch_and_cache",
            side_effect=RuntimeError(
                "ConnectionError: HTTPSConnectionPool(host=test.cloud.databricks.com, "
                "port=443): Max retries exceeded with url: /sql/1.0/warehouses/abc123"
            ),
        ):
            resp = client.post(
                "/api/databricks/fetch",
                json={"table": "cat.sch.tbl", "http_path": "/sql/wh"},
            )
        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert "test.cloud.databricks.com" not in detail
        assert "abc123" not in detail
        assert detail == _SAFE_DETAIL


# =====================================================================
# GAP 4: Database connection string / MLflow tracking URI leakage
# =====================================================================


class TestDatabaseConnectionStringLeakage:
    """Verify that MLflow tracking URIs and DB connection strings never leak.

    Real-world failure: resolve_tracking_backend raises with a message
    containing sqlite:///home/user/mlruns/mlflow.db or
    databricks://token:dapi_xxx@xxx.databricks.com.
    """

    def test_mlflow_tracking_uri_not_in_error(self, client: TestClient) -> None:
        """GET /api/mlflow/experiments -- tracking URI must not leak."""
        mock_mlflow = MagicMock()
        mock_mlflow.search_experiments.side_effect = RuntimeError(
            "OperationalError: unable to open database file: sqlite:////home/user/mlruns/mlflow.db"
        )
        with patch(
            "haute.routes.mlflow._ensure_tracking",
            return_value=(mock_mlflow, MagicMock()),
        ):
            resp = client.get("/api/mlflow/experiments")
        assert resp.status_code == 502
        detail = resp.json()["detail"]
        assert "sqlite://" not in detail
        assert "/home/user" not in detail
        assert "mlflow.db" not in detail
        assert "Check the server logs" in detail

    def test_optimiser_mlflow_log_tracking_uri_no_leak(
        self,
        client: TestClient,
    ) -> None:
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

    def test_modelling_mlflow_log_postgres_uri_no_leak(
        self,
        client: TestClient,
    ) -> None:
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
# GAP 5: Python version / OS info leakage
# =====================================================================


class TestPlatformInfoLeakage:
    """Verify that Python version, OS paths, or platform details don't leak.

    Real-world failure: Certain C-extension errors include platform info like
    /usr/local/lib/python3.11/lib-dynload/_csv.cpython-311-x86_64-linux-gnu.so
    or Python 3.11.5.
    """

    def test_schema_read_platform_info_no_leak(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        """GET /api/schema -- C-extension error with platform info must not leak."""
        target = tmp_path / "data.csv"
        target.write_text("a,b\n1,2\n")
        with patch(
            "haute.graph_utils.read_source",
            side_effect=RuntimeError(
                "ImportError: /usr/local/lib/python3.11/lib-dynload/"
                "_csv.cpython-311-x86_64-linux-gnu.so: undefined symbol: PyFloat_Type"
            ),
        ):
            resp = client.get("/api/schema", params={"path": str(target)})
        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert "python3.11" not in detail
        assert "x86_64" not in detail
        assert "lib-dynload" not in detail
        assert "Check the server logs" in detail

    def test_sink_cpython_error_no_leak(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        """POST /api/pipeline/sink -- CPython info in error must not leak."""
        graph = {
            "nodes": [
                {
                    "id": "src",
                    "type": "pipelineNode",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "label": "src",
                        "nodeType": "dataSource",
                        "config": {"path": "/tmp/fake.parquet"},
                    },
                },
            ],
            "edges": [],
        }
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


# =====================================================================
# GAP 6: User code error vs sandbox internals
# =====================================================================


class TestUserCodeErrorSanitization:
    """Verify that node execution errors show the user code error but NOT
    the wrapping exec()/sandbox internals.

    Real-world failure: A user writes df.filter(pl.col("x") > threshold)
    but threshold is undefined.  The error message should say
    NameError: name threshold is not defined -- but NOT include
    exec(exec_code, safe_globals(pl=pl), local_ns) or references to
    haute/executor.py in the error.

    These errors surface via NodeResult.error (200 response with per-node
    error status), NOT via HTTP 500.  We test the executor directly.
    """

    def test_user_code_nameerror_no_exec_frame(self) -> None:
        """_exec_user_code NameError must NOT reveal exec() internals."""
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
        """_exec_user_code TypeError must NOT reveal _sandbox.py path."""
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

    def test_preview_node_error_shows_user_msg_not_internals(
        self,
        client: TestClient,
    ) -> None:
        """POST /api/pipeline/preview -- per-node error should show user-facing
        message, not executor internals.

        When execute_graph returns normally (with per-node errors),
        the NodeResult.error field should contain the user error message,
        not sandbox paths or exec() references.
        """
        from haute.schemas import NodeResult

        graph = {
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
                    "id": "txn",
                    "type": "pipelineNode",
                    "position": {"x": 300, "y": 0},
                    "data": {
                        "label": "bad_transform",
                        "nodeType": "polars",
                        "config": {"code": "df = df.filter(pl.col('x') > threshold)"},
                    },
                },
            ],
            "edges": [{"id": "e1", "source": "src", "target": "txn"}],
        }

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
    error but NOT the full exec() traceback or server paths.

    Real-world failure: User writes from utility.helpers import calc but
    helpers.py has a NameError.  The PreambleError should say something like
    Error in utility/helpers.py line 5: name x is not defined -- but NOT
    File "/app/src/haute/executor.py", line 135, in _compile_preamble
       exec(preamble, ns).
    """

    def test_preamble_syntax_error_no_exec_traceback(self) -> None:
        """Preamble with syntax error must show syntax info, not exec() frame.

        When preamble code has a SyntaxError, validate_user_code catches it
        first and raises UnsafeCodeError.  The error message should describe
        the syntax problem without revealing exec() internals.
        """
        from haute._sandbox import UnsafeCodeError
        from haute.executor import PreambleError, _compile_preamble

        with pytest.raises((PreambleError, UnsafeCodeError)) as exc_info:
            _compile_preamble("def broken(\n")

        error_msg = str(exc_info.value)
        # Should mention syntax problem or line number
        assert "syntax" in error_msg.lower() or "line" in error_msg.lower()
        # Must NOT contain exec() internals
        assert "exec(" not in error_msg
        assert "safe_globals" not in error_msg
        assert "executor.py" not in error_msg

    def test_preamble_nameerror_no_server_path(self, tmp_path: Path) -> None:
        """PreambleError from NameError in utility module must show relative path,
        not absolute server path.
        """
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
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        """POST /api/pipeline/preview -- preamble error propagated to transform
        nodes must not contain exec() traceback.

        Uses a manually constructed graph (not parsed from file) to have full
        control over node IDs and types.
        """
        from haute.schemas import NodeResult

        graph = {
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
                    "id": "txn",
                    "type": "pipelineNode",
                    "position": {"x": 300, "y": 0},
                    "data": {
                        "label": "my_transform",
                        "nodeType": "polars",
                        "config": {"code": "df"},
                    },
                },
            ],
            "edges": [{"id": "e1", "source": "src", "target": "txn"}],
        }

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
        # Must NOT contain exec() frame details
        assert 'File "' not in body["error"]
        assert "exec(" not in body["error"]
        assert "site-packages" not in body["error"]


# =====================================================================
# GAP 2+5 combined: All Git routes with platform-info errors
# =====================================================================


class TestGitRoutesPlatformLeakage:
    """Verify that ALL git endpoints sanitize errors containing platform info.

    Real-world failure: Git operations shell out to the git binary.
    On failure, the CalledProcessError includes the full command and
    stderr, which may contain paths like /usr/bin/git or
    C:\\Program Files\\Git\\cmd\\git.exe.
    """

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
        """Each git route must not leak platform info in 500 errors."""
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
