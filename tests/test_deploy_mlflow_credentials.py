"""Deploy's Databricks credential split.

Deploy's MLflow calls resolve the Databricks MLflow destination first — the
dedicated ``DATABRICKS_MLFLOW_HOST``/``DATABRICKS_MLFLOW_TOKEN`` pair or a
``databricks://<profile>`` tracking URI, never the general
``DATABRICKS_HOST``/``DATABRICKS_TOKEN`` pair — and a resolution failure raises
``DeployError`` before any MLflow call or HTTP request. The connectivity
pre-check and the Model Serving client keep the ``DATABRICKS_RATING_*`` pair.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, fields
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from haute._sandbox import set_project_root
from haute.deploy._config import DeployConfig
from haute.errors import DeployError
from tests._deploy_helpers import make_resolved_deploy

GENERAL_HOST = "https://general.example.invalid"
GENERAL_TOKEN = "general-token-000"
MLFLOW_HOST = "https://mlflow.example.invalid"
MLFLOW_TOKEN = "mlflow-token-000"
RATING_HOST = "https://rating.example.invalid"
RATING_TOKEN = "rating-token-000"


@pytest.fixture(autouse=True)
def _hermetic_databricks_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", GENERAL_HOST)
    monkeypatch.setenv("DATABRICKS_TOKEN", GENERAL_TOKEN)
    monkeypatch.setenv("DATABRICKS_MLFLOW_HOST", MLFLOW_HOST)
    monkeypatch.setenv("DATABRICKS_MLFLOW_TOKEN", MLFLOW_TOKEN)
    monkeypatch.setenv("DATABRICKS_RATING_HOST", RATING_HOST)
    monkeypatch.setenv("DATABRICKS_RATING_TOKEN", RATING_TOKEN)
    for name in (
        "MLFLOW_TRACKING_URI",
        "DATABRICKS_CONFIG_PROFILE",
        "DATABRICKS_CLIENT_ID",
        "DATABRICKS_CLIENT_SECRET",
        "MLFLOW_ENABLE_DB_SDK",
    ):
        # Record the prior value first, so teardown also removes a value the
        # binder sets with ``os.environ.setdefault`` during the test.
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    monkeypatch.setenv("DATABRICKS_CONFIG_FILE", str(tmp_path / "databrickscfg"))
    set_project_root(tmp_path)


@dataclass
class DeployCalls:
    """Every network and MLflow boundary deploy can reach."""

    urlopen: MagicMock
    session_request: MagicMock
    set_tracking_uri: MagicMock
    set_registry_uri: MagicMock
    get_experiment_by_name: MagicMock
    set_experiment: MagicMock
    start_run: MagicMock
    log_dict: MagicMock
    log_model: MagicMock
    mlflow_client: MagicMock
    workspace_client: MagicMock

    def assert_none_called(self) -> None:
        for boundary in fields(self):
            getattr(self, boundary.name).assert_not_called()


@pytest.fixture()
def calls() -> Iterator[DeployCalls]:
    with (
        patch("urllib.request.urlopen") as urlopen,
        patch("requests.Session.request") as session_request,
        patch("mlflow.set_tracking_uri") as set_tracking_uri,
        patch("mlflow.set_registry_uri") as set_registry_uri,
        patch("mlflow.get_experiment_by_name") as get_experiment_by_name,
        patch("mlflow.set_experiment") as set_experiment,
        patch("mlflow.start_run") as start_run,
        patch("mlflow.log_dict") as log_dict,
        patch("mlflow.pyfunc.log_model") as log_model,
        patch("mlflow.tracking.MlflowClient") as mlflow_client,
        patch("databricks.sdk.WorkspaceClient") as workspace_client,
    ):
        registered = MagicMock()
        registered.version = "1"
        mlflow_client.return_value.search_model_versions.return_value = [registered]
        start_run.return_value.__enter__ = MagicMock()
        start_run.return_value.__exit__ = MagicMock(return_value=False)
        yield DeployCalls(
            urlopen=urlopen,
            session_request=session_request,
            set_tracking_uri=set_tracking_uri,
            set_registry_uri=set_registry_uri,
            get_experiment_by_name=get_experiment_by_name,
            set_experiment=set_experiment,
            start_run=start_run,
            log_dict=log_dict,
            log_model=log_model,
            mlflow_client=mlflow_client,
            workspace_client=workspace_client,
        )


def _deploy(tmp_path: Path) -> None:
    from haute.deploy._mlflow import deploy_to_mlflow

    pipeline_file = tmp_path / "pipeline.py"
    pipeline_file.write_text("# mocked deploy pipeline\n", encoding="utf-8")
    config = DeployConfig(
        pipeline_file=pipeline_file, model_name="test-model", endpoint_name="pricing-endpoint"
    )
    deploy_to_mlflow(make_resolved_deploy(config=config))


def test_sdk_mode_is_rejected_before_any_mlflow_or_http_call(
    calls: DeployCalls, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MLFLOW_ENABLE_DB_SDK", "true")

    with pytest.raises(DeployError, match="MLFLOW_ENABLE_DB_SDK"):
        _deploy(tmp_path)

    calls.assert_none_called()


def test_general_pair_alone_is_rejected_before_any_mlflow_or_http_call(
    calls: DeployCalls, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DATABRICKS_MLFLOW_HOST")
    monkeypatch.delenv("DATABRICKS_MLFLOW_TOKEN")

    with pytest.raises(DeployError) as excinfo:
        _deploy(tmp_path)

    message = str(excinfo.value)
    assert "DATABRICKS_MLFLOW_HOST" in message
    assert "DATABRICKS_HOST/DATABRICKS_TOKEN are set but are never used for MLflow" in message
    calls.assert_none_called()


def test_ambient_profile_variable_is_rejected_before_any_mlflow_or_http_call(
    calls: DeployCalls, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABRICKS_CONFIG_PROFILE", "team")

    with pytest.raises(DeployError, match="DATABRICKS_CONFIG_PROFILE"):
        _deploy(tmp_path)

    calls.assert_none_called()


def test_mlflow_pair_drives_mlflow_while_rating_pair_drives_serving(
    calls: DeployCalls, tmp_path: Path
) -> None:
    _deploy(tmp_path)

    calls.set_tracking_uri.assert_called_once_with("databricks")
    calls.set_registry_uri.assert_called_once_with("databricks-uc")
    [connectivity_request] = [call.args[0] for call in calls.urlopen.call_args_list]
    assert connectivity_request.full_url.startswith(f"{RATING_HOST}/")
    assert connectivity_request.get_header("Authorization") == f"Bearer {RATING_TOKEN}"
    calls.workspace_client.assert_called_once_with(host=RATING_HOST, token=RATING_TOKEN)
    serving_calls = repr(calls.workspace_client.mock_calls)
    assert MLFLOW_TOKEN not in serving_calls
    assert GENERAL_TOKEN not in serving_calls
    calls.session_request.assert_not_called()


def test_profile_tracking_uri_selects_the_profile_for_tracking_and_registry(
    calls: DeployCalls, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "databricks://team")

    _deploy(tmp_path)

    calls.set_tracking_uri.assert_called_once_with("databricks://team")
    calls.set_registry_uri.assert_called_once_with("databricks-uc://team")
    calls.workspace_client.assert_called_once_with(host=RATING_HOST, token=RATING_TOKEN)


def test_deploy_status_requires_the_mlflow_pair_before_building_a_client(
    calls: DeployCalls, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute.deploy._mlflow import get_deploy_status

    monkeypatch.delenv("DATABRICKS_MLFLOW_HOST")
    monkeypatch.delenv("DATABRICKS_MLFLOW_TOKEN")

    with pytest.raises(DeployError, match="DATABRICKS_MLFLOW_HOST"):
        get_deploy_status("test-model")

    calls.mlflow_client.assert_not_called()


def test_deploy_status_builds_its_client_on_the_resolved_destination(
    calls: DeployCalls,
) -> None:
    from haute.deploy._mlflow import get_deploy_status

    status = get_deploy_status("test-model")

    calls.mlflow_client.assert_called_once_with(
        tracking_uri="databricks", registry_uri="databricks-uc"
    )
    calls.mlflow_client.return_value.search_model_versions.assert_called_once_with(
        "name='main.default.test-model'"
    )
    assert status["latest_version"] == 1
