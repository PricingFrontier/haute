"""Tests for haute._mlflow_utils — shared MLflow helpers."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import Any
from unittest.mock import MagicMock

import pytest

from haute._mlflow_utils import (
    ResolvedBackend,
    mlflow_fluent_operation,
    resolve_backend,
    resolve_mlflow_source,
    resolve_version,
    runtime_environment_inference,
    search_versions,
    set_tracking_uri_preserving_env,
    tracking_uri_from_environment,
)
from haute.errors import MlflowConfigError


@pytest.mark.parametrize("original_uri", [None, "https://user:secret@example.invalid/mlflow"])
def test_set_tracking_uri_preserving_env_keeps_user_uri_visible_to_setter(
    monkeypatch: pytest.MonkeyPatch,
    original_uri: str | None,
) -> None:
    if original_uri is None:
        monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    else:
        monkeypatch.setenv("MLFLOW_TRACKING_URI", original_uri)

    class FakeMlflow:
        def set_tracking_uri(self, tracking_uri: str) -> None:
            os.environ["MLFLOW_TRACKING_URI"] = tracking_uri
            assert tracking_uri_from_environment() == (original_uri or "")

    set_tracking_uri_preserving_env(FakeMlflow(), "http://runtime.example.invalid")

    if original_uri is None:
        assert "MLFLOW_TRACKING_URI" not in os.environ
    else:
        assert os.environ["MLFLOW_TRACKING_URI"] == original_uri


def test_mlflow_fluent_operation_serializes_writers_and_restores_state() -> None:
    import mlflow

    original_process_tracking = mlflow.get_tracking_uri()
    original_process_registry = mlflow.get_registry_uri()
    original_process_environment = {
        name: os.environ.get(name)
        for name in (
            "MLFLOW_TRACKING_URI",
            "MLFLOW_REGISTRY_URI",
            "MLFLOW_EXPERIMENT_ID",
        )
    }
    expected_tracking = "http://example.invalid/original-tracking"
    expected_registry = "http://example.invalid/original-registry"
    expected_environment = {
        "MLFLOW_TRACKING_URI": "http://user.example.invalid/tracking",
        "MLFLOW_REGISTRY_URI": "http://user.example.invalid/registry",
        "MLFLOW_EXPERIMENT_ID": "original-experiment",
    }
    first_entered = Event()
    release_first = Event()
    second_started = Event()
    second_entered = Event()

    def first_writer() -> None:
        with mlflow_fluent_operation():
            mlflow.set_tracking_uri("http://example.invalid/a")
            mlflow.set_registry_uri("http://example.invalid/a-registry")
            os.environ["MLFLOW_TRACKING_URI"] = "http://writer-a.invalid/tracking"
            os.environ["MLFLOW_REGISTRY_URI"] = "http://writer-a.invalid/registry"
            os.environ["MLFLOW_EXPERIMENT_ID"] = "writer-a-experiment"
            first_entered.set()
            if not release_first.wait(timeout=5):
                raise TimeoutError("first writer was not released")
            raise RuntimeError("first writer failed")

    def second_writer() -> None:
        second_started.set()
        with mlflow_fluent_operation():
            assert mlflow.get_tracking_uri() == expected_tracking
            assert mlflow.get_registry_uri() == expected_registry
            assert {
                name: os.environ.get(name) for name in expected_environment
            } == expected_environment
            second_entered.set()
            mlflow.set_tracking_uri("http://example.invalid/b")
            mlflow.set_registry_uri("http://example.invalid/b-registry")
            os.environ["MLFLOW_TRACKING_URI"] = "http://writer-b.invalid/tracking"
            os.environ["MLFLOW_REGISTRY_URI"] = "http://writer-b.invalid/registry"
            os.environ["MLFLOW_EXPERIMENT_ID"] = "writer-b-experiment"

    try:
        mlflow.set_tracking_uri(expected_tracking)
        mlflow.set_registry_uri(expected_registry)
        os.environ.update(expected_environment)
        with ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(first_writer)
            assert first_entered.wait(timeout=5)
            second_future = executor.submit(second_writer)
            assert second_started.wait(timeout=5)
            assert not second_entered.wait(timeout=0.1)
            release_first.set()
            with pytest.raises(RuntimeError, match="first writer failed"):
                first_future.result(timeout=5)
            second_future.result(timeout=5)

        assert mlflow.get_tracking_uri() == expected_tracking
        assert mlflow.get_registry_uri() == expected_registry
        assert {name: os.environ.get(name) for name in expected_environment} == expected_environment
    finally:
        release_first.set()
        mlflow.set_tracking_uri(original_process_tracking)
        mlflow.set_registry_uri(original_process_registry)
        for name, value in original_process_environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def test_source_resolution_uses_selected_registry_without_changing_globals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mlflow
    from mlflow.tracking import MlflowClient

    from haute._mlflow_io import _resolve_artifact_local
    from haute._mlflow_utils import resolve_mlflow_source
    from haute._sandbox import set_project_root
    from haute.modelling._mlflow_settings import MlflowSettings, save_mlflow_settings

    for v in ("DATABRICKS_HOST", "DATABRICKS_TOKEN", "MLFLOW_TRACKING_URI"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    monkeypatch.setattr("haute._mlflow_io._disk_cache_root", lambda: tmp_path / "model-cache")
    set_project_root(tmp_path)
    records = {}
    for name in ("a", "b"):
        uri = (tmp_path / name).as_uri()
        client = MlflowClient(tracking_uri=uri, registry_uri=uri)
        run = client.create_run(client.create_experiment("experiment"))
        client.create_registered_model("pricing-model")
        client.create_model_version(
            "pricing-model",
            source=run.info.artifact_uri + "/model.cbm",
            run_id=run.info.run_id,
        )
        artifact = tmp_path / "model.cbm"
        artifact.write_text(name, encoding="utf-8")
        client.log_artifact(run.info.run_id, str(artifact))
        records[name] = (uri, run.info.run_id)
    with mlflow_fluent_operation():
        mlflow.set_tracking_uri(records["a"][0])
        mlflow.set_registry_uri(records["a"][0])
        save_mlflow_settings(MlflowSettings(folder="b"), tmp_path)
        run_id, _, _, client, backend = resolve_mlflow_source(
            source_type="registered",
            registered_model="pricing-model",
            version="latest",
        )
        assert run_id == records["b"][1]
        assert client.get_run(run_id).info.run_id == run_id
        artifact_path = _resolve_artifact_local(mlflow, backend, run_id, "model.cbm")
        assert Path(artifact_path).read_text(encoding="utf-8") == "b"
        assert mlflow.get_tracking_uri() == records["a"][0]
        assert mlflow.get_registry_uri() == records["a"][0]


# ---------------------------------------------------------------------------
# search_versions
# ---------------------------------------------------------------------------


class TestSearchVersions:
    def test_calls_client_with_quoted_name(self):
        client = MagicMock()
        client.search_model_versions.return_value = []
        result = search_versions(client, "my_model")
        assert result == []
        client.search_model_versions.assert_called_once_with("name='my_model'")

    def test_escapes_single_quotes_in_model_name(self):
        client = MagicMock()
        client.search_model_versions.return_value = []
        result = search_versions(client, "model's_name")
        assert result == []
        call_arg = client.search_model_versions.call_args[0][0]
        assert "\\'" in call_arg

    def test_returns_client_result(self):
        client = MagicMock()
        v1 = MagicMock(version="1")
        client.search_model_versions.return_value = [v1]
        result = search_versions(client, "model")
        assert result == [v1]


# ---------------------------------------------------------------------------
# resolve_version
# ---------------------------------------------------------------------------


class TestResolveVersion:
    def test_explicit_version_returned_as_is(self):
        client = MagicMock()
        assert resolve_version(client, "model", "3") == "3"

    def test_explicit_version_does_not_call_search(self):
        client = MagicMock()
        result = resolve_version(client, "model", "3")
        assert result == "3"
        client.search_model_versions.assert_not_called()

    def test_latest_resolves_to_highest(self):
        client = MagicMock()
        v1 = MagicMock(version="1")
        v2 = MagicMock(version="2")
        v3 = MagicMock(version="3")
        client.search_model_versions.return_value = [v1, v3, v2]
        result = resolve_version(client, "model", "latest")
        assert result == "3"

    def test_empty_version_resolves_to_latest(self):
        client = MagicMock()
        v = MagicMock(version="5")
        client.search_model_versions.return_value = [v]
        result = resolve_version(client, "model", "")
        assert result == "5"

    def test_no_versions_raises(self):
        client = MagicMock()
        client.search_model_versions.return_value = []
        with pytest.raises(ValueError, match="No versions found"):
            resolve_version(client, "model", "latest")

    def test_no_versions_error_includes_model_name(self):
        client = MagicMock()
        client.search_model_versions.return_value = []
        with pytest.raises(ValueError, match="my-special-model"):
            resolve_version(client, "my-special-model", "")

    def test_sorts_by_integer_not_string(self):
        """Versions '10' and '9': string sort would put '9' > '10'."""
        client = MagicMock()
        v9 = MagicMock(version="9")
        v10 = MagicMock(version="10")
        client.search_model_versions.return_value = [v9, v10]
        result = resolve_version(client, "model", "latest")
        assert result == "10"


# ---------------------------------------------------------------------------
# resolve_backend
# ---------------------------------------------------------------------------


class TestResolveBackend:
    def test_local_identity_is_canonical_folder(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from haute._sandbox import set_project_root

        set_project_root(tmp_path)
        for v in ("DATABRICKS_HOST", "DATABRICKS_TOKEN", "MLFLOW_TRACKING_URI"):
            monkeypatch.delenv(v, raising=False)
        backend = resolve_backend("local")
        assert backend.mode == "local"
        assert backend.identity.startswith("local:")
        assert backend.identity.endswith("|registry=" + (tmp_path / "mlruns").as_uri())
        assert len(backend.digest) == 16 and all(c in "0123456789abcdef" for c in backend.digest)

    def test_two_local_folders_have_distinct_digests(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from haute._sandbox import set_project_root
        from haute.modelling._mlflow_settings import MlflowSettings, save_mlflow_settings

        for v in ("DATABRICKS_HOST", "DATABRICKS_TOKEN", "MLFLOW_TRACKING_URI"):
            monkeypatch.delenv(v, raising=False)
        set_project_root(tmp_path)
        save_mlflow_settings(MlflowSettings(folder="folder_a"), tmp_path)
        backend_a = resolve_backend("local")
        save_mlflow_settings(MlflowSettings(folder="folder_b"), tmp_path)
        backend_b = resolve_backend("local")
        assert backend_a.digest != backend_b.digest
        assert backend_a.identity != backend_b.identity

    def test_server_identity_redacts_credentials(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MLFLOW_TRACKING_URI", "https://alice:secret@mlflow.example.com/")
        backend = resolve_backend("server")
        assert backend.tracking_uri == "https://alice:secret@mlflow.example.com/"
        assert "secret" not in backend.identity
        assert (
            backend.identity
            == "server:https://mlflow.example.com|registry=https://mlflow.example.com/"
        )

    def test_databricks_profile_identity_includes_effective_host(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from unittest.mock import patch

        monkeypatch.setenv("MLFLOW_TRACKING_URI", "databricks://team")
        creds = MagicMock(host="https://adb-1.example.net")
        with patch("mlflow.utils.databricks_utils.get_databricks_host_creds", return_value=creds):
            backend = resolve_backend("databricks")
        assert (
            backend.identity
            == "databricks:https://adb-1.example.net|profile=team|registry=databricks-uc://team"
        )
        assert backend.registry_uri == "databricks-uc://team"

    def test_repointed_profile_changes_identity(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from unittest.mock import patch

        monkeypatch.setenv("MLFLOW_TRACKING_URI", "databricks://team")
        with patch(
            "mlflow.utils.databricks_utils.get_databricks_host_creds",
            return_value=MagicMock(host="https://adb-1.example.net"),
        ):
            backend_1 = resolve_backend("databricks")
        with patch(
            "mlflow.utils.databricks_utils.get_databricks_host_creds",
            return_value=MagicMock(host="https://adb-2.example.net"),
        ):
            backend_2 = resolve_backend("databricks")
        assert backend_1.identity != backend_2.identity
        assert backend_1.digest != backend_2.digest

    def test_profile_beats_conflicting_environment_credentials_for_identity_and_requests(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from unittest.mock import patch

        import requests
        from mlflow.tracking import MlflowClient

        cfg = tmp_path / "databrickscfg"
        cfg.write_text(
            "[team]\nhost = https://profile-host.example.net\ntoken = profile-token-value\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("DATABRICKS_CONFIG_FILE", str(cfg))
        monkeypatch.setenv("MLFLOW_TRACKING_URI", "databricks://team")
        monkeypatch.setenv("DATABRICKS_HOST", "https://env-host.example.net")
        monkeypatch.setenv("DATABRICKS_TOKEN", "env-token-value")
        monkeypatch.delenv("MLFLOW_ENABLE_DB_SDK", raising=False)
        backend = resolve_backend("databricks")
        assert backend.identity.startswith(
            "databricks:https://profile-host.example.net|profile=team"
        )
        assert os.environ["MLFLOW_ENABLE_DB_SDK"] == "false"

        captured: dict[str, Any] = {}

        def fake_request(self: Any, method: str, url: str, **kwargs: Any) -> requests.Response:
            captured["url"] = url
            captured["headers"] = dict(kwargs.get("headers") or {})
            resp = requests.Response()
            resp.status_code = 200
            resp._content = b'{"experiments": []}'
            resp.url = url
            return resp

        with patch("requests.Session.request", new=fake_request):
            MlflowClient(
                tracking_uri=backend.tracking_uri, registry_uri=backend.registry_uri
            ).search_experiments(max_results=1)
        assert captured["url"].startswith("https://profile-host.example.net/")
        assert captured["headers"].get("Authorization") == "Bearer profile-token-value"
        assert "env-token-value" not in repr(captured)

    def test_sdk_mode_is_rejected_before_any_identity_is_minted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MLFLOW_TRACKING_URI", "databricks://team")
        monkeypatch.setenv("MLFLOW_ENABLE_DB_SDK", "true")
        with pytest.raises(MlflowConfigError, match="MLFLOW_ENABLE_DB_SDK"):
            resolve_backend("databricks")
        assert os.environ["MLFLOW_ENABLE_DB_SDK"] == "true"

    @pytest.mark.parametrize("form", ["profile", "pair"])
    def test_repointed_workspace_changes_identity_and_request_target_without_cache_clear(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, form: str
    ) -> None:
        """Two requests on ONE MlflowClient object, MLflow caches untouched, across a repoint."""
        from unittest.mock import patch

        import requests
        from mlflow.tracking import MlflowClient

        monkeypatch.delenv("MLFLOW_ENABLE_DB_SDK", raising=False)
        captured: list[dict[str, Any]] = []

        def fake_request(self: Any, method: str, url: str, **kwargs: Any) -> requests.Response:
            captured.append(
                {
                    "url": url,
                    "auth": dict(kwargs.get("headers") or {}).get("Authorization"),
                }
            )
            resp = requests.Response()
            resp.status_code = 200
            resp._content = b'{"experiments": []}'
            resp.url = url
            return resp

        cfg = tmp_path / "databrickscfg"
        if form == "pair":
            monkeypatch.setenv("DATABRICKS_HOST", "https://host-a.example.net")
            monkeypatch.setenv("DATABRICKS_TOKEN", "token-a")
            monkeypatch.setenv("MLFLOW_TRACKING_URI", "databricks")
        else:
            cfg.write_text(
                "[team]\nhost = https://host-a.example.net\ntoken = token-a\n", encoding="utf-8"
            )
            monkeypatch.setenv("DATABRICKS_CONFIG_FILE", str(cfg))
            monkeypatch.setenv("MLFLOW_TRACKING_URI", "databricks://team")
            monkeypatch.delenv("DATABRICKS_HOST", raising=False)
            monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)

        backend_a = resolve_backend("databricks")
        client = MlflowClient(
            tracking_uri=backend_a.tracking_uri, registry_uri=backend_a.registry_uri
        )
        with patch("requests.Session.request", new=fake_request):
            client.search_experiments(max_results=1)
            assert captured[-1]["url"].startswith(
                backend_a.identity.split("|")[0][len("databricks:") :]
            )
            assert captured[-1]["auth"] == "Bearer token-a"

            # Repoint: pair -> set DATABRICKS_HOST/TOKEN = B; profile -> rewrite cfg file
            if form == "pair":
                monkeypatch.setenv("DATABRICKS_HOST", "https://host-b.example.net")
                monkeypatch.setenv("DATABRICKS_TOKEN", "token-b")
            else:
                cfg.write_text(
                    "[team]\nhost = https://host-b.example.net\ntoken = token-b\n", encoding="utf-8"
                )

            backend_b = resolve_backend("databricks")
            client.search_experiments(max_results=1)  # same client object
            assert backend_b.digest != backend_a.digest
            assert captured[-1]["url"].startswith("https://host-b.example.net/")
            assert backend_b.identity.startswith("databricks:https://host-b.example.net|")
            assert captured[-1]["auth"] == "Bearer token-b"

    def test_broken_profile_fails_secret_free(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from unittest.mock import patch

        monkeypatch.setenv("MLFLOW_TRACKING_URI", "databricks://missing")
        with patch(
            "mlflow.utils.databricks_utils.get_databricks_host_creds",
            side_effect=RuntimeError("token dapi-secret"),
        ):
            with pytest.raises(MlflowConfigError) as excinfo:
                resolve_backend("databricks")
        assert "missing" in str(excinfo.value) and "dapi-secret" not in str(excinfo.value)

    def test_plain_databricks_identity_uses_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from unittest.mock import patch

        monkeypatch.setenv("DATABRICKS_HOST", "https://adb.example.net")
        monkeypatch.setenv("DATABRICKS_TOKEN", "token-val")
        monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
        monkeypatch.delenv("MLFLOW_ENABLE_DB_SDK", raising=False)
        with patch(
            "mlflow.utils.databricks_utils.get_databricks_host_creds",
            return_value=MagicMock(host="https://adb.example.net"),
        ):
            backend = resolve_backend("databricks")
        assert (
            backend.identity == "databricks:https://adb.example.net|profile=|registry=databricks-uc"
        )

    def test_auto_follows_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from unittest.mock import patch

        from haute._sandbox import set_project_root

        set_project_root(tmp_path)
        for v in ("DATABRICKS_HOST", "DATABRICKS_TOKEN", "MLFLOW_TRACKING_URI"):
            monkeypatch.delenv(v, raising=False)
        backend = resolve_backend("")
        assert backend.mode == "local"

        monkeypatch.setenv("DATABRICKS_HOST", "https://adb.example.net")
        monkeypatch.setenv("DATABRICKS_TOKEN", "token-val")
        monkeypatch.delenv("MLFLOW_ENABLE_DB_SDK", raising=False)
        with patch(
            "mlflow.utils.databricks_utils.get_databricks_host_creds",
            return_value=MagicMock(host="https://adb.example.net"),
        ):
            backend_db = resolve_backend("")
        assert backend_db.mode == "databricks"

    def test_unknown_destination_rejected(self) -> None:
        with pytest.raises(MlflowConfigError, match="databricks, server, or local"):
            resolve_backend("managed")


# ---------------------------------------------------------------------------
# resolve_mlflow_source destination-aware
# ---------------------------------------------------------------------------


class TestResolveMlflowSourceDestination:
    def test_returns_backend_and_pins_client(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from unittest.mock import patch

        from haute._sandbox import set_project_root

        set_project_root(tmp_path)
        for v in ("DATABRICKS_HOST", "DATABRICKS_TOKEN", "MLFLOW_TRACKING_URI"):
            monkeypatch.delenv(v, raising=False)
        with patch("mlflow.tracking.MlflowClient") as client_cls:
            res = resolve_mlflow_source(source_type="run", run_id="run-123")
            assert len(res) == 5
            run_id, version, mod, client, backend = res
            assert run_id == "run-123"
            assert backend.mode == "local"
            assert client_cls.call_args.kwargs["tracking_uri"] == backend.tracking_uri
            assert client_cls.call_args.kwargs["registry_uri"] == backend.registry_uri

    def test_explicit_destination_overrides_auto(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from unittest.mock import patch

        from haute._sandbox import set_project_root

        set_project_root(tmp_path)
        monkeypatch.setenv("DATABRICKS_HOST", "https://adb.example.net")
        monkeypatch.setenv("DATABRICKS_TOKEN", "token-val")
        monkeypatch.delenv("MLFLOW_ENABLE_DB_SDK", raising=False)
        with patch("mlflow.tracking.MlflowClient"):
            _, _, _, _, backend = resolve_mlflow_source(
                source_type="run", run_id="run-123", destination="local"
            )
            assert backend.mode == "local"

    def test_prepared_backend_is_used_without_re_resolution(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from unittest.mock import patch

        prepared = ResolvedBackend(
            mode="local",
            tracking_uri="file:///test",
            registry_uri="file:///test",
            identity="local:test|registry=file:///test",
            digest="0123456789abcdef",
        )
        with (
            patch("haute._mlflow_utils.resolve_backend") as mock_res,
            patch("mlflow.tracking.MlflowClient"),
        ):
            _, _, _, _, backend = resolve_mlflow_source(
                source_type="run", run_id="run-123", backend=prepared
            )
            mock_res.assert_not_called()
            assert backend is prepared

    def test_backend_and_destination_together_is_an_error(self) -> None:
        prepared = ResolvedBackend(
            mode="local",
            tracking_uri="file:///test",
            registry_uri="file:///test",
            identity="local:test|registry=file:///test",
            digest="0123456789abcdef",
        )
        with pytest.raises(ValueError, match="destination"):
            resolve_mlflow_source(
                source_type="run", run_id="run-123", destination="local", backend=prepared
            )


# ---------------------------------------------------------------------------
# runtime_environment_inference
# ---------------------------------------------------------------------------


class TestRuntimeEnvironmentInference:
    """MLflow's uv-lock inference is off only while haute logs a model."""

    SWITCHES = ("MLFLOW_UV_AUTO_DETECT", "MLFLOW_LOG_UV_FILES")

    def test_switches_are_off_inside_and_absent_again_after(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in self.SWITCHES:
            monkeypatch.delenv(name, raising=False)
        with runtime_environment_inference():
            assert all(os.environ[name] == "false" for name in self.SWITCHES)
        assert all(name not in os.environ for name in self.SWITCHES)

    def test_previous_values_are_restored_even_when_the_body_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MLFLOW_UV_AUTO_DETECT", "true")
        monkeypatch.setenv("MLFLOW_LOG_UV_FILES", "1")
        with pytest.raises(RuntimeError, match="boom"):
            with runtime_environment_inference():
                assert all(os.environ[name] == "false" for name in self.SWITCHES)
                raise RuntimeError("boom")
        assert os.environ["MLFLOW_UV_AUTO_DETECT"] == "true"
        assert os.environ["MLFLOW_LOG_UV_FILES"] == "1"
