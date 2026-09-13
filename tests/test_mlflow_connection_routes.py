"""API tests for the MLflow connection surface (routes/mlflow.py).

Covers GET /api/mlflow/destinations, GET/PUT /api/mlflow/settings, and
POST /api/mlflow/test-connection per
specs/mlflow-model-registry/low-level.md (Connection surface): per-entry
inventory truthfulness for all three destination keys, misconfiguration
reported as data (never a 5xx), the settings round trip with resolved-folder
persistence, probe error classification, and the removal of the old
/api/modelling/mlflow/check and /api/mlflow/status routes.
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


@pytest.mark.parametrize("response_type", ["LogExperimentResponse", "OptimiserMlflowLogResponse"])
def test_log_response_never_exposes_uri_credentials(response_type: str) -> None:
    from haute import schemas

    response = getattr(schemas, response_type)(
        status="ok",
        backend="server",
        tracking_uri="https://review:synthetic-secret@mlflow.example.test",
        run_url="https://review:synthetic-secret@mlflow.example.test/#/experiments/1/runs/2",
    )
    assert "synthetic-secret" not in response.model_dump_json()
    assert response.tracking_uri == "https://mlflow.example.test"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "MLFLOW_TRACKING_URI",
        "DATABRICKS_HOST",
        "DATABRICKS_TOKEN",
        "DATABRICKS_MLFLOW_HOST",
        "DATABRICKS_MLFLOW_TOKEN",
        "DATABRICKS_CONFIG_PROFILE",
    ):
        monkeypatch.delenv(var, raising=False)


_DATABRICKS_UNCONFIGURED_DETAIL = (
    "Databricks is not configured for MLflow: set MLFLOW_TRACKING_URI=databricks://<profile> "
    "or both DATABRICKS_MLFLOW_HOST and DATABRICKS_MLFLOW_TOKEN in the environment (.env)."
)


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    (tmp_path / "haute.toml").write_text(_TOML, encoding="utf-8")
    set_project_root(tmp_path)
    return tmp_path


def _write_mlflow_section(project_root: Path, body: str) -> None:
    with (project_root / "haute.toml").open("a", encoding="utf-8") as f:
        f.write("\n[mlflow]\n" + body)


# ---------------------------------------------------------------------------
# GET /api/mlflow/destinations
# ---------------------------------------------------------------------------


class TestDestinations:
    def test_status_route_is_gone(self, client, project_root: Path) -> None:
        assert client.get("/api/mlflow/status").status_code == 404

    def test_default_inventory_without_probe(self, client, project_root: Path) -> None:
        resp = client.get("/api/mlflow/destinations")
        assert resp.status_code == 200
        body = resp.json()
        assert body["mlflow_installed"] is True and body["mlflow_importable"] is True
        assert "auto" not in body
        assert [d["key"] for d in body["destinations"]] == ["databricks", "server", "local"]
        databricks, server, local = body["destinations"]
        assert databricks["configured"] is False
        assert databricks["detail"] == _DATABRICKS_UNCONFIGURED_DETAIL
        assert databricks["probed"] is False
        assert server["configured"] is False and "tracking_uri" in server["detail"]
        assert local["configured"] is True and local["probed"] is False
        assert Path(local["destination"]) == project_root / "mlruns"
        assert local["config_source"] == "default"

    def test_probe_touches_only_configured_remotes(
        self, client, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_mlflow_section(project_root, 'tracking_uri = "http://localhost:5000"\n')
        probed: list[str] = []

        def fake_probe(uri: str) -> None:
            probed.append(uri)

        with patch("haute.routes.mlflow._search_experiments_probe", side_effect=fake_probe):
            body = client.get("/api/mlflow/destinations?probe=true").json()
        assert probed == ["http://localhost:5000"]
        databricks, server, local = body["destinations"]
        assert server["probed"] is True and server["ok"] is True and server["category"] == ""
        assert databricks["probed"] is False and local["probed"] is False

    def test_failed_probe_reports_the_reason_on_its_entry(
        self, client, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MLFLOW_TRACKING_URI", "databricks://team")
        with patch(
            "haute.routes.mlflow._search_experiments_probe",
            side_effect=ConnectionError("dial tcp: refused dapi-secret"),
        ):
            body = client.get("/api/mlflow/destinations?probe=true").json()
        databricks = body["destinations"][0]
        assert databricks["configured"] is True
        assert databricks["destination"] == "databricks://team"
        assert databricks["probed"] is True and databricks["ok"] is False
        assert databricks["category"] == "connectivity"
        assert "dapi-secret" not in str(body)

    def test_probes_run_concurrently_within_one_budget(
        self, client, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import threading
        import time

        _write_mlflow_section(project_root, 'tracking_uri = "http://localhost:5000"\n')
        monkeypatch.setenv("DATABRICKS_MLFLOW_HOST", "https://adb.example.net")
        monkeypatch.setenv("DATABRICKS_MLFLOW_TOKEN", "t")
        barrier = threading.Barrier(2, timeout=5)

        def slow_probe(uri: str) -> None:
            barrier.wait()  # both probes must be in flight at once or this times out
            time.sleep(0.2)

        started = time.perf_counter()
        with patch("haute.routes.mlflow._search_experiments_probe", side_effect=slow_probe):
            body = client.get("/api/mlflow/destinations?probe=true").json()
        assert time.perf_counter() - started < 1.0
        assert all(d["ok"] for d in body["destinations"][:2])

    def test_malformed_toml_marks_toml_backed_entries(self, client, project_root: Path) -> None:
        _write_mlflow_section(project_root, 'mode = "local"\n')
        body = client.get("/api/mlflow/destinations?probe=true").json()
        databricks, server, local = body["destinations"]
        assert databricks["configured"] is False
        assert databricks["detail"] == _DATABRICKS_UNCONFIGURED_DETAIL
        assert server["configured"] is False and "mode" in server["detail"]
        assert local["configured"] is False and "mode" in local["detail"]
        assert body["detail"] == ""

    def test_rejected_databricks_sdk_mode_keeps_other_entries(
        self, client, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_mlflow_section(project_root, 'tracking_uri = "http://localhost:5000"\n')
        monkeypatch.setenv("MLFLOW_TRACKING_URI", "databricks://team")
        monkeypatch.setenv("MLFLOW_ENABLE_DB_SDK", "true")
        with patch("haute.routes.mlflow._search_experiments_probe") as probe:
            body = client.get("/api/mlflow/destinations?probe=true").json()
        databricks, server, local = body["destinations"]
        assert databricks["configured"] is False and "MLFLOW_ENABLE_DB_SDK" in databricks["detail"]
        assert databricks["probed"] is False
        assert server["configured"] is True and server["probed"] is True and server["ok"] is True
        assert local["configured"] is True
        probe.assert_called_once_with("http://localhost:5000")
        # A node without a destination browses the local folder; only a node that
        # chose Databricks sees the rejected configuration.
        assert client.get("/api/mlflow/experiments").status_code == 200
        resp = client.get("/api/mlflow/experiments?destination=databricks")
        assert resp.status_code == 502 and "MLFLOW_ENABLE_DB_SDK" in resp.json()["detail"]

    def test_inventory_is_reported_without_the_mlflow_package(
        self, client, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import sys

        monkeypatch.setenv("MLFLOW_TRACKING_URI", "databricks://team")
        monkeypatch.setitem(sys.modules, "mlflow", None)
        with (
            patch(
                "haute.routes.mlflow._mlflow_availability",
                return_value=(
                    False,
                    False,
                    "MLflow package is not installed. Install it with: pip install mlflow",
                ),
            ),
            patch("haute.routes.mlflow._search_experiments_probe") as probe,
        ):
            body = client.get("/api/mlflow/destinations?probe=true").json()
        assert body["mlflow_installed"] is False and "pip install mlflow" in body["detail"]
        assert [d["configured"] for d in body["destinations"]] == [True, False, True]
        probe.assert_not_called()

    def test_missing_package_reports_configuration_and_never_probes(
        self, client, project_root: Path
    ) -> None:
        _write_mlflow_section(project_root, 'tracking_uri = "http://localhost:5000"\n')
        with (
            patch(
                "haute.routes.mlflow._mlflow_availability",
                return_value=(
                    False,
                    False,
                    "MLflow package is not installed. Install it with: pip install mlflow",
                ),
            ),
            patch("haute.routes.mlflow._search_experiments_probe") as probe,
        ):
            body = client.get("/api/mlflow/destinations?probe=true").json()
        assert body["mlflow_installed"] is False
        assert "pip install mlflow" in body["detail"]
        assert body["destinations"][1]["configured"] is True
        probe.assert_not_called()

    def test_credentialed_env_uri_destination_is_redacted(
        self, client, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MLFLOW_TRACKING_URI", "https://alice:secret@mlflow.example.com")
        body = client.get("/api/mlflow/destinations").json()
        assert body["destinations"][1]["destination"] == "https://mlflow.example.com"
        assert "secret" not in str(body)


# ---------------------------------------------------------------------------
# GET / PUT /api/mlflow/settings
# ---------------------------------------------------------------------------


class TestSettings:
    def test_get_with_no_section(self, client, project_root: Path) -> None:
        body = client.get("/api/mlflow/settings").json()
        assert body["section_present"] is False
        assert body["tracking_uri"] == "" and body["folder"] == ""
        assert Path(body["resolved_folder"]) == project_root / "mlruns"

    def test_put_then_get_round_trips(self, client, project_root: Path) -> None:
        resp = client.put(
            "/api/mlflow/settings",
            json={"tracking_uri": "http://localhost:5000", "folder": "team-runs"},
        )
        assert resp.status_code == 200
        body = client.get("/api/mlflow/settings").json()
        assert body["section_present"] is True
        assert body["tracking_uri"] == "http://localhost:5000"
        assert body["folder"] == "team-runs"
        assert Path(body["resolved_folder"]) == project_root / "team-runs"

    def test_put_preserves_other_sections_and_comments(self, client, project_root: Path) -> None:
        client.put("/api/mlflow/settings", json={"tracking_uri": "http://localhost:5000"})
        text = (project_root / "haute.toml").read_text(encoding="utf-8")
        assert "# project comment survives settings writes" in text
        assert tomllib.loads(text)["project"]["pipeline"] == "rating/main.py"

    def test_put_empty_tracking_uri_clears_server(self, client, project_root: Path) -> None:
        client.put("/api/mlflow/settings", json={"tracking_uri": "http://localhost:5000"})
        client.put("/api/mlflow/settings", json={"tracking_uri": ""})
        assert client.get("/api/mlflow/settings").json()["tracking_uri"] == ""
        assert (
            client.get("/api/mlflow/destinations").json()["destinations"][1]["configured"] is False
        )

    def test_put_mode_is_422(self, client, project_root: Path) -> None:
        # ``mode`` is no longer a field; pydantic ignores unknown fields, so the
        # round trip simply never writes it.
        client.put("/api/mlflow/settings", json={"mode": "server", "tracking_uri": "http://x:5000"})
        assert "mode" not in tomllib.loads((project_root / "haute.toml").read_text())["mlflow"]

    def test_put_invalid_server_uri_is_400_and_writes_nothing(
        self, client, project_root: Path
    ) -> None:
        resp = client.put("/api/mlflow/settings", json={"tracking_uri": "sqlite:///x.db"})
        assert resp.status_code == 400
        assert "tracking_uri" in resp.json()["detail"]
        assert "mlflow" not in tomllib.loads((project_root / "haute.toml").read_text())

    def test_put_credentialed_uri_is_400_without_echoing_secret(
        self, client, project_root: Path
    ) -> None:
        resp = client.put(
            "/api/mlflow/settings", json={"tracking_uri": "https://a:hunter2xyz@h.example"}
        )
        assert resp.status_code == 400
        assert "hunter2xyz" not in resp.text

    def test_bare_save_preserves_env_derived_folder(
        self, client, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        custom = project_root / "custom-runs"
        monkeypatch.setenv("MLFLOW_TRACKING_URI", custom.as_uri())
        client.put("/api/mlflow/settings", json={})
        body = client.get("/api/mlflow/settings").json()
        assert Path(body["folder"]) == custom
        assert Path(body["resolved_folder"]) == custom

    def test_malformed_section_is_reported_not_5xx(self, client, project_root: Path) -> None:
        _write_mlflow_section(project_root, 'mode = "local"\n')
        resp = client.get("/api/mlflow/settings")
        assert resp.status_code == 200
        assert "mode" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# POST /api/mlflow/test-connection
# ---------------------------------------------------------------------------


class TestTestConnection:
    def test_local_folder_when_body_absent(self, client, project_root: Path) -> None:
        with patch("haute.routes.mlflow._search_experiments_probe") as probe:
            body = client.post("/api/mlflow/test-connection").json()
        assert body == {"ok": True, "category": "", "detail": ""}
        assert probe.call_args.args[0].startswith("file:")

    def test_explicit_destination_without_draft_probes_the_inventory(
        self, client, project_root: Path
    ) -> None:
        _write_mlflow_section(project_root, 'tracking_uri = "http://saved:5000"\n')
        with patch("haute.routes.mlflow._search_experiments_probe") as probe:
            client.post("/api/mlflow/test-connection", json={"destination": "server"})
        probe.assert_called_once_with("http://saved:5000")

    def test_env_seeded_server_is_probed_without_draft(
        self, client, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://env:5000")
        with patch("haute.routes.mlflow._search_experiments_probe") as probe:
            body = client.post("/api/mlflow/test-connection", json={"destination": "server"}).json()
        probe.assert_called_once_with("http://env:5000")
        assert body["ok"] is True

    def test_empty_server_draft_is_a_configuration_error(self, client, project_root: Path) -> None:
        _write_mlflow_section(project_root, 'tracking_uri = "http://saved:5000"\n')
        with patch("haute.routes.mlflow._search_experiments_probe") as probe:
            body = client.post(
                "/api/mlflow/test-connection", json={"destination": "server", "tracking_uri": ""}
            ).json()
        probe.assert_not_called()
        assert body["ok"] is False and body["category"] == "configuration"
        assert "tracking_uri" in body["detail"]

    def test_cold_process_probe_binds_the_selected_profile_not_the_environment(
        self, client, project_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Nothing has resolved a backend yet in this test; the probe alone must apply the pin."""
        import requests

        cfg = tmp_path / "databrickscfg"
        cfg.write_text(
            "[team]\nhost = https://profile-host.example.net\ntoken = profile-token-value\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("DATABRICKS_CONFIG_FILE", str(cfg))
        monkeypatch.setenv("MLFLOW_TRACKING_URI", "databricks://team")
        monkeypatch.setenv("DATABRICKS_MLFLOW_HOST", "https://env-host.example.net")
        monkeypatch.setenv("DATABRICKS_MLFLOW_TOKEN", "env-token-value")
        monkeypatch.delenv("MLFLOW_ENABLE_DB_SDK", raising=False)
        captured: dict[str, object] = {}

        def fake_request(self_, method, url, **kwargs):
            captured["url"] = url
            captured["auth"] = dict(kwargs.get("headers") or {}).get("Authorization")
            resp = requests.Response()
            resp.status_code = 200
            resp._content = b'{"experiments": []}'
            resp.url = url
            return resp

        with patch("requests.Session.request", new=fake_request):
            body = client.post(
                "/api/mlflow/test-connection", json={"destination": "databricks"}
            ).json()
        assert body["ok"] is True
        assert str(captured["url"]).startswith("https://profile-host.example.net/")
        assert captured["auth"] == "Bearer profile-token-value"

    def test_probe_failure_log_is_secret_free(self, client, project_root: Path) -> None:
        import structlog.testing

        with (
            patch(
                "haute.routes.mlflow._search_experiments_probe",
                side_effect=ConnectionError("refused; token dapi-synthetic-secret"),
            ),
            structlog.testing.capture_logs() as records,
        ):
            body = client.post("/api/mlflow/test-connection").json()
        assert body["ok"] is False and "dapi-synthetic-secret" not in str(body)
        assert "dapi-synthetic-secret" not in repr(records)
        assert any(
            r.get("category") == "connectivity" and r.get("error_type") == "ConnectionError"
            for r in records
        )

    def test_draft_server_uri_is_probed_instead_of_saved(self, client, project_root: Path) -> None:
        _write_mlflow_section(project_root, 'tracking_uri = "http://saved:5000"\n')
        with patch("haute.routes.mlflow._search_experiments_probe") as probe:
            client.post(
                "/api/mlflow/test-connection",
                json={"destination": "server", "tracking_uri": "http://draft:5000"},
            )
        probe.assert_called_once_with("http://draft:5000")

    def test_unconfigured_destination_is_configuration_category(
        self, client, project_root: Path
    ) -> None:
        body = client.post("/api/mlflow/test-connection", json={"destination": "databricks"}).json()
        assert body["ok"] is False and body["category"] == "configuration"
        assert body["detail"] == _DATABRICKS_UNCONFIGURED_DETAIL

    def test_unknown_destination_is_configuration_category(
        self, client, project_root: Path
    ) -> None:
        body = client.post("/api/mlflow/test-connection", json={"destination": "managed"}).json()
        assert body["ok"] is False and body["category"] == "configuration"
        assert "databricks, server, or local" in body["detail"]

    def test_local_draft_folder_is_probed(self, client, project_root: Path) -> None:
        with patch("haute.routes.mlflow._search_experiments_probe") as probe:
            client.post(
                "/api/mlflow/test-connection", json={"destination": "local", "folder": "draft-runs"}
            )
        assert probe.call_args.args[0] == (project_root / "draft-runs").as_uri()

    def test_broken_profile_stays_configured_and_reports_failure(
        self, client, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MLFLOW_TRACKING_URI", "databricks://missing-profile")
        monkeypatch.setenv("DATABRICKS_MLFLOW_HOST", "https://adb.example.net")
        monkeypatch.setenv("DATABRICKS_MLFLOW_TOKEN", "dapi-secret")
        with patch("haute.routes.mlflow._search_experiments_probe") as probe:
            probe.side_effect = RuntimeError("profile 'missing-profile' not found dapi-secret")
            body = client.post(
                "/api/mlflow/test-connection", json={"destination": "databricks"}
            ).json()
        probe.assert_called_once_with("databricks://missing-profile")
        assert body["ok"] is False
        assert "dapi-secret" not in str(body)

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
        import threading

        import haute.routes.mlflow as mlflow_routes

        fresh = threading.BoundedSemaphore(2)
        monkeypatch.setattr(mlflow_routes, "_PROBE_SLOTS", fresh)
        _write_mlflow_section(project_root, 'tracking_uri = "http://127.0.0.1:1"\n')
        body = client.post("/api/mlflow/test-connection", json={"destination": "server"}).json()
        assert body["ok"] is False
        assert body["category"] == "connectivity"

        held = 0
        try:
            for _ in range(2):
                assert fresh.acquire(timeout=2), "probe worker still holds its slot"
                held += 1
        finally:
            for _ in range(held):
                fresh.release()

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

    def test_timed_out_probes_hold_their_slots_until_workers_finish(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The full capacity lifecycle through real timeouts: a timed-out
        # probe's slot stays occupied while its worker still runs (the
        # resource-growth defect the bound exists for), and frees only when
        # the underlying call returns.
        import threading
        import time

        import haute.routes.mlflow as mlflow_routes
        from haute.routes.mlflow import _ProbeBusyError, _run_probe_bounded

        fresh = threading.BoundedSemaphore(2)
        monkeypatch.setattr(mlflow_routes, "_PROBE_SLOTS", fresh)
        gate_one, gate_two = threading.Event(), threading.Event()
        try:
            with pytest.raises(TimeoutError):
                _run_probe_bounded(lambda: gate_one.wait(30), timeout=0.1)
            with pytest.raises(TimeoutError):
                _run_probe_bounded(lambda: gate_two.wait(30), timeout=0.1)

            # Both abandoned workers still run, so capacity is exhausted.
            with pytest.raises(_ProbeBusyError):
                _run_probe_bounded(lambda: None, timeout=1)

            # Finishing one worker frees exactly one slot.
            gate_one.set()
            deadline = time.monotonic() + 5
            while True:
                try:
                    _run_probe_bounded(lambda: None, timeout=1)
                    break
                except _ProbeBusyError:
                    if time.monotonic() > deadline:
                        pytest.fail("slot was not released after its worker finished")
                    time.sleep(0.02)
        finally:
            gate_one.set()
            gate_two.set()
            # Deterministically drain: both slots must return to the fresh
            # semaphore (proving the workers finished their release) before
            # monkeypatch restores the module-level one.
            drained = 0
            deadline = time.monotonic() + 5
            while drained < 2 and time.monotonic() < deadline:
                if fresh.acquire(timeout=0.05):
                    drained += 1
            assert drained == 2, "probe workers did not release their slots"

    def test_server_probe_is_one_bounded_request_without_global_mutation(self) -> None:
        from unittest.mock import MagicMock

        from haute.routes.mlflow import _PROBE_TIMEOUT_SECONDS, _search_experiments_probe

        response = MagicMock()
        with (
            patch("mlflow.set_tracking_uri") as set_uri,
            patch("mlflow.tracking.MlflowClient") as client_cls,
            patch("mlflow.utils.rest_utils.http_request", return_value=response) as request,
            patch("mlflow.utils.rest_utils.verify_rest_response") as verify,
        ):
            _search_experiments_probe("http://mlflow.example.com:5000")
        # Probing must not mutate the process-global tracking URI, and must
        # not go through a default client whose retry policy outlives the
        # route's deadline.
        set_uri.assert_not_called()
        client_cls.assert_not_called()
        request.assert_called_once()
        creds, endpoint, method = request.call_args.args
        assert creds.host == "http://mlflow.example.com:5000"
        assert (endpoint, method) == ("/api/2.0/mlflow/experiments/search", "POST")
        assert request.call_args.kwargs == {
            "json": {"max_results": 1},
            "max_retries": 0,
            "timeout": _PROBE_TIMEOUT_SECONDS,
            "retry_timeout_seconds": _PROBE_TIMEOUT_SECONDS,
        }
        verify.assert_called_once_with(response, "/api/2.0/mlflow/experiments/search")

    def test_databricks_probe_uses_workspace_credentials_for_its_one_request(self) -> None:
        from unittest.mock import MagicMock

        from haute.routes.mlflow import _search_experiments_probe

        creds = MagicMock(name="databricks-host-creds")
        with (
            patch("mlflow.set_tracking_uri") as set_uri,
            patch("mlflow.tracking.MlflowClient") as client_cls,
            patch(
                "mlflow.utils.databricks_utils.get_databricks_host_creds", return_value=creds
            ) as host_creds,
            patch("mlflow.utils.rest_utils.http_request", return_value=MagicMock()) as request,
            patch("mlflow.utils.rest_utils.verify_rest_response"),
        ):
            _search_experiments_probe("databricks://team")
        set_uri.assert_not_called()
        client_cls.assert_not_called()
        host_creds.assert_called_once_with("databricks://team")
        assert request.call_args.args[0] is creds
        assert request.call_args.kwargs["max_retries"] == 0

    def test_local_probe_uses_the_file_store_client(self, tmp_path: Path) -> None:
        from unittest.mock import MagicMock

        from haute.routes.mlflow import _search_experiments_probe

        client_instance = MagicMock()
        with (
            patch("mlflow.set_tracking_uri") as set_uri,
            patch("mlflow.tracking.MlflowClient", return_value=client_instance) as client_cls,
            patch("mlflow.utils.rest_utils.http_request") as request,
        ):
            _search_experiments_probe(tmp_path.as_uri())
        set_uri.assert_not_called()
        request.assert_not_called()
        client_cls.assert_called_once_with(tracking_uri=tmp_path.as_uri())
        client_instance.search_experiments.assert_called_once_with(max_results=1)


# ---------------------------------------------------------------------------
# Saved env-derived folder keeps existing runs discoverable
# ---------------------------------------------------------------------------


class TestLocalRegistryDiscovery:
    def test_locally_registered_model_appears_in_the_models_route(
        self, client, project_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Registry parity pin: the mlflow 3.x file store supports the model
        # registry, so a locally registered model must surface through the
        # Model Score picker's discovery routes exactly like a Databricks
        # one — including the file store's int-typed versions, which the
        # routes must serialise to the string wire contract.
        import mlflow
        from mlflow.tracking import MlflowClient

        monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
        uri = (project_root / "mlruns").as_uri()
        registry = MlflowClient(tracking_uri=uri, registry_uri=uri)
        experiment_id = registry.create_experiment("registry-exp")
        run = registry.create_run(experiment_id)
        registry.create_registered_model("local-reg-model")
        registry.create_model_version(
            "local-reg-model",
            source=f"{run.info.artifact_uri}/model",
            run_id=run.info.run_id,
        )

        # Ambient process-global registry state from another destination
        # must not influence the discovery routes.
        mlflow.set_registry_uri("databricks-uc")
        try:
            resp = client.get("/api/mlflow/models")
            assert resp.status_code == 200
            by_name = {m["name"]: m for m in resp.json()}
            assert "local-reg-model" in by_name
            assert [v["version"] for v in by_name["local-reg-model"]["latest_versions"]] == ["1"]

            versions = client.get("/api/mlflow/model-versions?model_name=local-reg-model")
            assert versions.status_code == 200
            assert [v["version"] for v in versions.json()] == ["1"]
            assert versions.json()[0]["run_id"] == run.info.run_id
        finally:
            mlflow.set_registry_uri(None)


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

            assert client.put("/api/mlflow/settings", json={}).status_code == 200
            monkeypatch.delenv("MLFLOW_TRACKING_URI")
            # Discovery must find the runs from the saved settings alone —
            # clear the ambient global so it cannot mask a resolver defect.
            mlflow.set_tracking_uri(None)

            resp = client.get("/api/mlflow/experiments")
            assert resp.status_code == 200
            assert "team-exp" in [e["name"] for e in resp.json()]
        finally:
            mlflow.set_tracking_uri(None)
