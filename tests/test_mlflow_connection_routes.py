"""API tests for the MLflow connection surface (routes/mlflow.py).

Covers GET /api/mlflow/status, GET/PUT /api/mlflow/settings, and
POST /api/mlflow/test-connection per
specs/mlflow-model-registry/low-level.md (Connection surface): status
truthfulness in all three modes, misconfiguration reported as data (never a
5xx), the settings round trip with per-mode validation and resolved-folder
persistence, probe error classification, and the removal of the old
/api/modelling/mlflow/check route.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from haute._sandbox import set_project_root

_TOML = """\
# project comment survives settings writes

[project]
name = "main"
pipeline = "rating/main.py"
"""


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("MLFLOW_TRACKING_URI", "DATABRICKS_HOST", "DATABRICKS_TOKEN"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    (tmp_path / "haute.toml").write_text(_TOML, encoding="utf-8")
    set_project_root(tmp_path)
    return tmp_path


def _write_mlflow_section(project_root: Path, body: str) -> None:
    with (project_root / "haute.toml").open("a", encoding="utf-8") as f:
        f.write("\n[mlflow]\n" + body)


# ---------------------------------------------------------------------------
# GET /api/mlflow/status
# ---------------------------------------------------------------------------


class TestStatus:
    def test_default_local_is_reported_truthfully(self, client, project_root: Path) -> None:
        resp = client.get("/api/mlflow/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["mlflow_installed"] is True
        assert body["mlflow_importable"] is True
        assert body["configured"] is True
        assert body["mode"] == "local"
        assert Path(body["destination"]) == project_root / "mlruns"
        assert body["config_source"] == "default"
        assert body["detail"] == ""

    def test_toml_server_mode(self, client, project_root: Path) -> None:
        _write_mlflow_section(
            project_root, 'mode = "server"\ntracking_uri = "http://localhost:5000"\n'
        )
        body = client.get("/api/mlflow/status").json()
        assert body["configured"] is True
        assert body["mode"] == "server"
        assert body["destination"] == "http://localhost:5000"
        assert body["config_source"] == "toml"

    def test_env_credentials_select_databricks(
        self, client, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATABRICKS_HOST", "https://adb.example.net")
        monkeypatch.setenv("DATABRICKS_TOKEN", "dapi-secret")
        body = client.get("/api/mlflow/status").json()
        assert body["configured"] is True
        assert body["mode"] == "databricks"
        assert body["destination"] == "https://adb.example.net"
        assert body["config_source"] == "env"
        assert "dapi-secret" not in str(body)

    def test_misconfiguration_is_data_not_5xx(self, client, project_root: Path) -> None:
        _write_mlflow_section(project_root, 'mode = "databricks"\n')
        resp = client.get("/api/mlflow/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["configured"] is False
        assert body["mode"] == ""
        assert "DATABRICKS_HOST" in body["detail"]

    def test_mlflow_not_installed_still_reports_configuration_independently(
        self, client, project_root: Path
    ) -> None:
        # Package presence and configuration are independent facts: the
        # default local resolution needs no mlflow package at all.
        with patch("importlib.util.find_spec", return_value=None):
            resp = client.get("/api/mlflow/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["mlflow_installed"] is False
        assert body["mlflow_importable"] is False
        assert body["configured"] is True
        assert body["mode"] == "local"
        assert "install" in body["detail"].lower()

    def test_import_failure_keeps_package_available(self, client, project_root: Path) -> None:
        from types import SimpleNamespace

        with (
            patch("importlib.util.find_spec", return_value=SimpleNamespace()),
            patch("importlib.import_module", side_effect=ImportError("broken dependency")),
        ):
            body = client.get("/api/mlflow/status").json()
        assert body["mlflow_installed"] is True
        assert body["mlflow_importable"] is False
        assert body["configured"] is True
        assert body["mode"] == "local"
        assert "broken dependency" in body["detail"]

    def test_credentialed_env_uri_destination_is_redacted(
        self, client, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MLFLOW_TRACKING_URI", "https://alice:hunter2xyz@mlflow.example.com")
        status = client.get("/api/mlflow/status").json()
        settings = client.get("/api/mlflow/settings").json()
        assert status["destination"] == "https://mlflow.example.com"
        assert "hunter2xyz" not in str(status)
        assert "hunter2xyz" not in str(settings)

    def test_old_modelling_check_route_is_gone(self, client) -> None:
        assert client.get("/api/modelling/mlflow/check").status_code == 404


# ---------------------------------------------------------------------------
# GET / PUT /api/mlflow/settings
# ---------------------------------------------------------------------------


class TestSettings:
    def test_get_with_no_section(self, client, project_root: Path) -> None:
        body = client.get("/api/mlflow/settings").json()
        assert body["section_present"] is False
        assert body["mode"] == ""
        assert body["tracking_uri"] == ""
        assert body["folder"] == ""
        assert body["resolved"]["mode"] == "local"
        assert body["resolved"]["config_source"] == "default"

    def test_put_server_then_get_round_trips(self, client, project_root: Path) -> None:
        resp = client.put(
            "/api/mlflow/settings",
            json={"mode": "server", "tracking_uri": "http://localhost:5000"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["section_present"] is True
        assert body["mode"] == "server"
        assert body["resolved"]["mode"] == "server"
        assert body["resolved"]["destination"] == "http://localhost:5000"
        assert body["resolved"]["config_source"] == "toml"

        again = client.get("/api/mlflow/settings").json()
        assert again == body

    def test_put_preserves_other_sections_and_comments(self, client, project_root: Path) -> None:
        client.put(
            "/api/mlflow/settings",
            json={"mode": "server", "tracking_uri": "http://localhost:5000"},
        )
        text = (project_root / "haute.toml").read_text(encoding="utf-8")
        assert "# project comment survives settings writes" in text
        parsed = tomllib.loads(text)
        assert parsed["project"]["pipeline"] == "rating/main.py"
        assert parsed["mlflow"] == {"mode": "server", "tracking_uri": "http://localhost:5000"}

    def test_put_invalid_server_uri_is_400_and_writes_nothing(
        self, client, project_root: Path
    ) -> None:
        resp = client.put(
            "/api/mlflow/settings",
            json={"mode": "server", "tracking_uri": "sqlite:///mlflow.db"},
        )
        assert resp.status_code == 400
        assert "tracking_uri" in resp.json()["detail"]
        assert "mlflow" not in tomllib.loads(
            (project_root / "haute.toml").read_text(encoding="utf-8")
        )

    def test_put_field_for_wrong_mode_is_400(self, client, project_root: Path) -> None:
        resp = client.put(
            "/api/mlflow/settings",
            json={"mode": "databricks", "folder": "mlruns"},
        )
        assert resp.status_code == 400
        assert "folder" in resp.json()["detail"]

    def test_put_unknown_mode_is_400_naming_the_field(self, client, project_root: Path) -> None:
        resp = client.put("/api/mlflow/settings", json={"mode": "filesystem"})
        assert resp.status_code == 400
        assert "mode" in resp.json()["detail"]

    def test_put_credentialed_server_uri_is_400_without_echoing_secret(
        self, client, project_root: Path
    ) -> None:
        resp = client.put(
            "/api/mlflow/settings",
            json={
                "mode": "server",
                "tracking_uri": "https://alice:hunter2xyz@mlflow.example.com",
            },
        )
        assert resp.status_code == 400
        assert "hunter2xyz" not in resp.json()["detail"]

    def test_put_local_preserves_env_derived_folder(
        self, client, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        team_runs = project_root / "team-runs"
        monkeypatch.setenv("MLFLOW_TRACKING_URI", team_runs.as_uri())
        resp = client.put("/api/mlflow/settings", json={"mode": "local"})
        assert resp.status_code == 200
        parsed = tomllib.loads((project_root / "haute.toml").read_text(encoding="utf-8"))
        assert Path(parsed["mlflow"]["folder"]) == team_runs
        assert Path(resp.json()["resolved"]["destination"]) == team_runs


# ---------------------------------------------------------------------------
# POST /api/mlflow/test-connection
# ---------------------------------------------------------------------------


class TestTestConnection:
    def test_ok_when_probe_succeeds(self, client, project_root: Path) -> None:
        with patch("haute.routes.mlflow._search_experiments_probe", return_value=None):
            body = client.post("/api/mlflow/test-connection").json()
        assert body == {"ok": True, "category": "", "detail": ""}

    def test_configuration_error_is_classified(self, client, project_root: Path) -> None:
        _write_mlflow_section(project_root, 'mode = "databricks"\n')
        body = client.post("/api/mlflow/test-connection").json()
        assert body["ok"] is False
        assert body["category"] == "configuration"
        assert "DATABRICKS_HOST" in body["detail"]

    @pytest.mark.parametrize(
        ("error_code", "category"),
        [
            ("UNAUTHENTICATED", "authentication"),
            ("PERMISSION_DENIED", "permission"),
            ("RESOURCE_DOES_NOT_EXIST", "missing_resource"),
        ],
    )
    def test_rest_exception_error_codes_are_classified(
        self, client, project_root: Path, error_code: str, category: str
    ) -> None:
        from mlflow.exceptions import RestException

        exc = RestException({"error_code": error_code, "message": "denied"})
        with patch("haute.routes.mlflow._search_experiments_probe", side_effect=exc):
            body = client.post("/api/mlflow/test-connection").json()
        assert body["ok"] is False
        assert body["category"] == category

    def test_transport_error_is_connectivity(self, client, project_root: Path) -> None:
        import requests

        exc = requests.exceptions.ConnectionError("refused")
        with patch("haute.routes.mlflow._search_experiments_probe", side_effect=exc):
            body = client.post("/api/mlflow/test-connection").json()
        assert body["ok"] is False
        assert body["category"] == "connectivity"

    def test_wrapped_transport_error_is_classified_as_connectivity(
        self, client, project_root: Path
    ) -> None:
        # MLflow's REST layer wraps transport failures in MlflowException;
        # classification must inspect the exception chain, not the outer type.
        import requests
        from mlflow.exceptions import MlflowException

        wrapped = MlflowException("API request to endpoint failed")
        wrapped.__cause__ = requests.exceptions.ConnectionError("refused")
        with patch("haute.routes.mlflow._search_experiments_probe", side_effect=wrapped):
            body = client.post("/api/mlflow/test-connection").json()
        assert body["ok"] is False
        assert body["category"] == "connectivity"

    def test_real_transport_boundary_dead_port_is_connectivity(
        self, client, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No probe patching: this exercises the real MLflow REST transport
        # against a closed local port, with retries/timeout bounded.
        monkeypatch.setenv("MLFLOW_HTTP_REQUEST_MAX_RETRIES", "0")
        monkeypatch.setenv("MLFLOW_HTTP_REQUEST_TIMEOUT", "2")
        _write_mlflow_section(
            project_root, 'mode = "server"\ntracking_uri = "http://127.0.0.1:1"\n'
        )
        body = client.post("/api/mlflow/test-connection").json()
        assert body["ok"] is False
        assert body["category"] == "connectivity"

    def test_unexpected_error_is_unknown_and_not_5xx(self, client, project_root: Path) -> None:
        with patch(
            "haute.routes.mlflow._search_experiments_probe",
            side_effect=RuntimeError("boom"),
        ):
            resp = client.post("/api/mlflow/test-connection")
        assert resp.status_code == 200
        assert resp.json()["ok"] is False
        assert resp.json()["category"] == "unknown"


# ---------------------------------------------------------------------------
# Probe mechanics: bounded timeout, bounded workers, request shape
# ---------------------------------------------------------------------------


class TestProbeMechanics:
    def test_run_probe_bounded_times_out_hanging_work(self) -> None:
        import threading

        from haute.routes.mlflow import _run_probe_bounded

        release = threading.Event()
        try:
            with pytest.raises(TimeoutError):
                _run_probe_bounded(lambda: release.wait(30), timeout=0.2)
        finally:
            release.set()  # let the abandoned worker finish promptly

    def test_run_probe_bounded_worker_is_daemon(self) -> None:
        import threading

        from haute.routes.mlflow import _run_probe_bounded

        captured: list[bool] = []
        _run_probe_bounded(lambda: captured.append(threading.current_thread().daemon), timeout=5)
        assert captured == [True]

    def test_probe_reports_busy_when_worker_slots_are_exhausted(
        self, client, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import threading

        import haute.routes.mlflow as mlflow_routes

        exhausted = threading.BoundedSemaphore(1)
        assert exhausted.acquire(blocking=False)
        monkeypatch.setattr(mlflow_routes, "_PROBE_SLOTS", exhausted)
        body = client.post("/api/mlflow/test-connection").json()
        assert body["ok"] is False
        assert body["category"] == "unknown"
        assert "still running" in body["detail"]

    def test_search_probe_request_shape_and_no_global_mutation(self) -> None:
        from unittest.mock import MagicMock

        from haute.routes.mlflow import _search_experiments_probe

        client_instance = MagicMock()
        client_cls = MagicMock(return_value=client_instance)
        with (
            patch("mlflow.set_tracking_uri") as set_uri,
            patch("mlflow.tracking.MlflowClient", client_cls),
        ):
            _search_experiments_probe("http://mlflow.example.com:5000")
        # Probing must not mutate the process-global tracking URI.
        set_uri.assert_not_called()
        client_cls.assert_called_once_with(tracking_uri="http://mlflow.example.com:5000")
        client_instance.search_experiments.assert_called_once_with(max_results=1)


# ---------------------------------------------------------------------------
# Saved env-derived folder keeps existing runs discoverable
# ---------------------------------------------------------------------------


class TestSavedFolderKeepsRunsDiscoverable:
    def test_env_logged_run_survives_unchanged_local_save(
        self, client, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import mlflow

        team_runs = project_root / "team-runs"
        monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
        monkeypatch.setenv("MLFLOW_TRACKING_URI", team_runs.as_uri())
        mlflow.set_tracking_uri(team_runs.as_uri())
        try:
            experiment_id = mlflow.create_experiment("team-exp")
            with mlflow.start_run(experiment_id=experiment_id):
                pass

            assert client.put("/api/mlflow/settings", json={"mode": "local"}).status_code == 200
            monkeypatch.delenv("MLFLOW_TRACKING_URI")

            resp = client.get("/api/mlflow/experiments")
            assert resp.status_code == 200
            assert "team-exp" in [e["name"] for e in resp.json()]
        finally:
            mlflow.set_tracking_uri(None)
