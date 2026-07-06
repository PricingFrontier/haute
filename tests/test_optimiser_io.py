"""Comprehensive tests for haute._optimiser_io.

Covers:
  - load_optimiser_artifact  — caching, mtime invalidation, file errors
  - load_mlflow_optimiser_artifact — run/registered paths, validation, caching
  - _resolve_version — "latest", explicit version, empty versions list
"""

from __future__ import annotations

import json
import os
import time as _time
from unittest.mock import MagicMock, patch

import pytest

from haute._mlflow_utils import (
    resolve_mlflow_source,
)
from haute._mlflow_utils import (
    resolve_version as _resolve_version,
)
from haute._optimiser_io import (
    _load_artifact_cached,
    _load_mlflow_cached,
    load_mlflow_optimiser_artifact,
    load_optimiser_artifact,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_caches():
    """Clear caches before and after every test."""
    _load_artifact_cached.cache_clear()
    _load_mlflow_cached.cache_clear()
    yield
    _load_artifact_cached.cache_clear()
    _load_mlflow_cached.cache_clear()


@pytest.fixture()
def mlflow_mocks(tmp_path):
    """Common MLflow mock stack for load_mlflow_optimiser_artifact tests.

    Yields a namespace with mock_backend, mock_set_uri, mock_download,
    and a helper to write artifact JSON.
    """
    with (
        patch("mlflow.artifacts.download_artifacts") as mock_download,
        patch("mlflow.set_tracking_uri") as mock_set_uri,
        patch("haute.modelling._mlflow_log.resolve_tracking_backend") as mock_backend,
    ):
        mock_backend.return_value = ("http://localhost:5000", "local")

        def write_artifact(data: dict) -> str:
            path = tmp_path / "optimiser_result.json"
            path.write_text(json.dumps(data))
            mock_download.return_value = str(path)
            return str(path)

        yield type(
            "Mocks",
            (),
            {
                "download": mock_download,
                "set_uri": mock_set_uri,
                "backend": mock_backend,
                "write_artifact": staticmethod(write_artifact),
            },
        )()


# ===========================================================================
# load_optimiser_artifact
# ===========================================================================


class TestLoadOptimiserArtifact:
    def test_loads_valid_json(self, tmp_path):
        f = tmp_path / "artifact.json"
        data = {"mode": "online", "lambdas": {"x": 1.5}}
        f.write_text(json.dumps(data))

        result = load_optimiser_artifact(str(f))
        assert result["mode"] == "online"
        assert result["lambdas"]["x"] == 1.5

    def test_caches_on_second_call(self, tmp_path):
        f = tmp_path / "artifact.json"
        f.write_text(json.dumps({"mode": "ratebook"}))

        result1 = load_optimiser_artifact(str(f))
        result2 = load_optimiser_artifact(str(f))
        assert result1 == result2

    def test_mtime_change_invalidates_cache(self, tmp_path):
        f = tmp_path / "artifact.json"
        f.write_text(json.dumps({"version": 1}))

        result1 = load_optimiser_artifact(str(f))
        assert result1["version"] == 1

        # Write new content with a different mtime
        f.write_text(json.dumps({"version": 2}))
        future = _time.time() + 10
        os.utime(str(f), (future, future))

        result2 = load_optimiser_artifact(str(f))
        assert result2["version"] == 2
        assert result1 is not result2

    def test_file_not_found_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_optimiser_artifact(str(tmp_path / "nonexistent.json"))

    def test_invalid_json_raises(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("{bad json")
        with pytest.raises(json.JSONDecodeError):
            load_optimiser_artifact(str(f))

    def test_missing_file_mtime_is_zero(self, tmp_path):
        """When the file doesn't exist at mtime check, mtime defaults to 0.0,
        but the actual open() still raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_optimiser_artifact(str(tmp_path / "ghost.json"))


# ===========================================================================
# load_mlflow_optimiser_artifact — run source
# ===========================================================================


class TestLoadMlflowOptimiserArtifactRun:
    def test_local_backend_opts_into_mlflow_file_store(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("MLFLOW_ALLOW_FILE_STORE", raising=False)

        def _client_with_required_env(*_args, **_kwargs):
            assert os.environ["MLFLOW_ALLOW_FILE_STORE"] == "true"
            return MagicMock()

        with (
            patch("mlflow.set_tracking_uri"),
            patch("mlflow.tracking.MlflowClient", side_effect=_client_with_required_env),
            patch(
                "haute.modelling._mlflow_log.resolve_tracking_backend",
                return_value=("file:///tmp/mlruns", "local"),
            ),
        ):
            resolve_mlflow_source(source_type="run", run_id="run_abc123")

        assert os.environ["MLFLOW_ALLOW_FILE_STORE"] == "true"

    def test_run_source_loads_artifact(self, mlflow_mocks):
        mlflow_mocks.write_artifact({"mode": "online", "version": "v1"})

        result = load_mlflow_optimiser_artifact(
            source_type="run",
            run_id="run_abc123",
        )
        assert result["mode"] == "online"
        mlflow_mocks.download.assert_called_once()

    def test_run_source_caches(self, mlflow_mocks):
        mlflow_mocks.write_artifact({"mode": "online"})

        result1 = load_mlflow_optimiser_artifact(source_type="run", run_id="run_1")
        result2 = load_mlflow_optimiser_artifact(source_type="run", run_id="run_1")
        assert result1 == result2
        # download_artifacts called only once due to caching
        mlflow_mocks.download.assert_called_once()

    def test_run_without_run_id_raises(self):
        with pytest.raises(ValueError, match="run_id is required"):
            load_mlflow_optimiser_artifact(
                source_type="run",
                run_id="",
                tracking_uri="http://x",
            )

    def test_invalid_source_type_raises(self):
        with pytest.raises(ValueError, match="Invalid sourceType"):
            load_mlflow_optimiser_artifact(
                source_type="invalid",
                tracking_uri="http://x",
            )


# ===========================================================================
# load_mlflow_optimiser_artifact — registered model source
# ===========================================================================


class TestLoadMlflowOptimiserArtifactRegistered:
    def test_registered_source(self, mlflow_mocks):
        mlflow_mocks.write_artifact({"mode": "ratebook"})

        with (
            patch("mlflow.tracking.MlflowClient") as mock_client,
            patch("haute._mlflow_utils.resolve_version", return_value="2"),
        ):
            mv = MagicMock()
            mv.run_id = "resolved_run_id"
            mock_client.return_value.get_model_version.return_value = mv

            result = load_mlflow_optimiser_artifact(
                source_type="registered",
                registered_model="my_model",
                version="2",
            )
        assert result["mode"] == "ratebook"

    def test_registered_without_model_name_raises(self):
        with pytest.raises(ValueError, match="registered_model is required"):
            load_mlflow_optimiser_artifact(
                source_type="registered",
                registered_model="",
                tracking_uri="http://x",
            )

    def test_tracking_uri_auto_detected(self, mlflow_mocks):
        """When tracking_uri is empty, resolve_tracking_backend is called."""
        mlflow_mocks.write_artifact({"mode": "online"})

        load_mlflow_optimiser_artifact(
            source_type="run",
            run_id="run_1",
            tracking_uri="",  # empty => auto-detect
        )
        mlflow_mocks.backend.assert_called_once()

    def test_explicit_tracking_uri_skips_auto_detect(self, mlflow_mocks):
        """When tracking_uri is provided, resolve_tracking_backend is not called."""
        mlflow_mocks.write_artifact({"mode": "online"})

        load_mlflow_optimiser_artifact(
            source_type="run",
            run_id="run_1",
            tracking_uri="http://explicit:5000",
        )
        mlflow_mocks.backend.assert_not_called()


# ===========================================================================
# _resolve_version
# ===========================================================================


class TestResolveVersion:
    def test_explicit_version_returned(self):
        client = MagicMock()
        assert _resolve_version(client, "model", "3") == "3"

    def test_latest_resolves_to_newest(self):
        client = MagicMock()
        v1 = MagicMock()
        v1.version = "1"
        v2 = MagicMock()
        v2.version = "2"
        v3 = MagicMock()
        v3.version = "3"
        client.search_model_versions.return_value = [v1, v3, v2]

        result = _resolve_version(client, "model", "latest")
        assert result == "3"

    def test_empty_version_resolves_to_latest(self):
        client = MagicMock()
        v = MagicMock()
        v.version = "5"
        client.search_model_versions.return_value = [v]

        result = _resolve_version(client, "model", "")
        assert result == "5"

    def test_no_versions_raises(self):
        client = MagicMock()
        client.search_model_versions.return_value = []

        with pytest.raises(ValueError, match="No versions found"):
            _resolve_version(client, "model", "latest")

    def test_sql_injection_in_model_name_escaped(self):
        """Model names with quotes should be safely escaped."""
        client = MagicMock()
        v = MagicMock()
        v.version = "1"
        client.search_model_versions.return_value = [v]

        _resolve_version(client, "model's_name", "latest")
        call_arg = client.search_model_versions.call_args[0][0]
        # The single quote should be escaped
        assert "\\'" in call_arg
