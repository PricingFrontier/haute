"""Tests for haute._mlflow_utils — shared MLflow helpers."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import MagicMock

import pytest

from haute._mlflow_utils import (
    mlflow_fluent_operation,
    resolve_version,
    search_versions,
    set_tracking_uri_preserving_env,
    tracking_uri_from_environment,
)


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
        save_mlflow_settings(MlflowSettings(mode="local", folder="b"), tmp_path)
        run_id, _, _, client = resolve_mlflow_source(
            source_type="registered",
            registered_model="pricing-model",
            version="latest",
        )
        assert run_id == records["b"][1]
        assert client.get_run(run_id).info.run_id == run_id
        artifact_path = _resolve_artifact_local(
            mlflow,
            run_id,
            "model.cbm",
            tracking_uri=client.tracking_uri,
        )
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
