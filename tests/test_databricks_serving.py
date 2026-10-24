"""The Databricks serving endpoint: one credential pair, and an update that is waited for.

Deploy, smoke and impact reach the endpoint with ``DATABRICKS_RATING_HOST`` and
``DATABRICKS_RATING_TOKEN`` (BUG-22: smoke and impact used the SDK's default
credentials, which CI does not set). Deploy waits until the endpoint serves the version
it registered, and an update Databricks fails reports why, with its build log.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from databricks.sdk.errors import NotFound
from databricks.sdk.service.serving import (
    EndpointStateConfigUpdate,
    EndpointStateReady,
    ServedModelStateDeployment,
)

from haute.deploy._databricks_serving import (
    RATING_HOST_ENV,
    RATING_TOKEN_ENV,
    ServingEndpointError,
    missing_serving_credentials,
    serving_credentials,
    serving_workspace_client,
    wait_for_endpoint,
)

_HOST = "https://adb-1.azuredatabricks.net"
_TOKEN = "dapi-serving-token"


@pytest.fixture
def serving_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(RATING_HOST_ENV, _HOST + "/")
    monkeypatch.setenv(RATING_TOKEN_ENV, _TOKEN)


def _entity(version: str, deployment: object = None, message: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        name=f"model-{version}",
        entity_version=version,
        state=SimpleNamespace(deployment=deployment, deployment_state_message=message),
    )


def _endpoint(
    ready: EndpointStateReady,
    update: EndpointStateConfigUpdate,
    *,
    serving: tuple[str, ...] = (),
    pending: tuple[SimpleNamespace, ...] = (),
) -> SimpleNamespace:
    return SimpleNamespace(
        state=SimpleNamespace(ready=ready, config_update=update),
        config=SimpleNamespace(served_entities=[_entity(v) for v in serving]),
        pending_config=SimpleNamespace(served_entities=list(pending)) if pending else None,
    )


def _workspace(*states: SimpleNamespace) -> MagicMock:
    ws = MagicMock()
    ws.serving_endpoints.get.side_effect = list(states)
    return ws


def _wait(ws: MagicMock, *, version: str | None, timeout: float = 600) -> list[str]:
    progress: list[str] = []
    wait_for_endpoint(
        ws,
        "pricing-staging",
        version=version,
        progress=progress.append,
        timeout_seconds=timeout,
        poll_seconds=15,
        sleep=lambda _seconds: None,
    )
    return progress


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


def test_the_serving_pair_is_read_from_the_environment(serving_pair: None) -> None:
    credentials = serving_credentials()

    assert (credentials.host, credentials.token) == (_HOST, _TOKEN)
    assert _TOKEN not in repr(credentials)


def test_every_missing_variable_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(RATING_HOST_ENV, raising=False)
    monkeypatch.setenv(RATING_TOKEN_ENV, "  ")

    assert missing_serving_credentials() == [RATING_HOST_ENV, RATING_TOKEN_ENV]
    with pytest.raises(ServingEndpointError) as excinfo:
        serving_credentials()
    assert f"{RATING_HOST_ENV} and {RATING_TOKEN_ENV} are not set" in str(excinfo.value)
    assert "haute ci secrets" in str(excinfo.value)


def test_the_client_uses_the_serving_pair_and_nothing_else(
    serving_pair: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The SDK's own defaults are the data-access pair; a client built from them in CI
    # failed with "cannot configure default credentials" (BUG-22).
    monkeypatch.setenv("DATABRICKS_HOST", "https://other.example")
    monkeypatch.setenv("DATABRICKS_TOKEN", "data-token")
    with patch("databricks.sdk.WorkspaceClient") as client:
        serving_workspace_client()

    client.assert_called_once_with(host=_HOST, token=_TOKEN, auth_type="pat", profile="")


# ---------------------------------------------------------------------------
# Waiting for an update
# ---------------------------------------------------------------------------


def test_the_wait_returns_once_the_endpoint_serves_the_version() -> None:
    ws = _workspace(
        _endpoint(
            EndpointStateReady.NOT_READY,
            EndpointStateConfigUpdate.IN_PROGRESS,
            pending=(_entity("3", ServedModelStateDeployment.DEPLOYMENT_CREATING, "Building"),),
        ),
        _endpoint(
            EndpointStateReady.NOT_READY,
            EndpointStateConfigUpdate.IN_PROGRESS,
            pending=(_entity("3", ServedModelStateDeployment.DEPLOYMENT_CREATING, "Building"),),
        ),
        _endpoint(EndpointStateReady.READY, EndpointStateConfigUpdate.NOT_UPDATING, serving=("3",)),
    )

    progress = _wait(ws, version="3")

    assert ws.serving_endpoints.get.call_count == 3
    # One line per change of state, not per poll.
    assert progress == [
        "Endpoint 'pricing-staging': ready=NOT_READY; config_update=IN_PROGRESS; "
        "model-3 DEPLOYMENT_CREATING: Building; waiting for version 3"
    ]


def test_an_endpoint_still_serving_the_old_version_is_waited_for() -> None:
    ws = _workspace(
        _endpoint(EndpointStateReady.READY, EndpointStateConfigUpdate.NOT_UPDATING, serving=("2",)),
        _endpoint(EndpointStateReady.READY, EndpointStateConfigUpdate.NOT_UPDATING, serving=("3",)),
    )

    _wait(ws, version="3")

    assert ws.serving_endpoints.get.call_count == 2


def test_any_ready_version_satisfies_a_wait_for_no_version() -> None:
    ws = _workspace(
        _endpoint(EndpointStateReady.READY, EndpointStateConfigUpdate.NOT_UPDATING, serving=("2",))
    )

    assert _wait(ws, version=None) == []


@pytest.mark.parametrize(
    "update", [EndpointStateConfigUpdate.UPDATE_FAILED, EndpointStateConfigUpdate.UPDATE_CANCELED]
)
def test_a_failed_update_reports_its_state_and_logs(update: EndpointStateConfigUpdate) -> None:
    failed = _entity("3", ServedModelStateDeployment.DEPLOYMENT_FAILED, "Container build failed")
    ws = _workspace(_endpoint(EndpointStateReady.READY, update, serving=("2",), pending=(failed,)))
    build_log = "\n".join(f"step {i}" for i in range(60)) + "\nERROR: No matching haute==9.9"
    ws.serving_endpoints.build_logs.return_value = SimpleNamespace(logs=build_log)
    ws.serving_endpoints.logs.side_effect = RuntimeError("no server started")

    with pytest.raises(ServingEndpointError) as excinfo:
        _wait(ws, version="3")

    message = str(excinfo.value)
    assert f"its update is {update.value}" in message
    assert "model-3 (version 3): DEPLOYMENT_FAILED Container build failed" in message
    assert "ERROR: No matching haute==9.9" in message
    assert "step 21" in message and "step 20" not in message  # the last 40 lines
    assert "(Server log unavailable: no server started)" in message
    ws.serving_endpoints.build_logs.assert_called_once_with(
        name="pricing-staging", served_model_name="model-3"
    )


def test_a_wait_that_runs_out_names_the_last_state() -> None:
    stuck = _endpoint(
        EndpointStateReady.NOT_READY,
        EndpointStateConfigUpdate.IN_PROGRESS,
        pending=(_entity("3", ServedModelStateDeployment.DEPLOYMENT_CREATING),),
    )
    ws = MagicMock()
    ws.serving_endpoints.get.return_value = stuck

    with pytest.raises(ServingEndpointError) as excinfo:
        _wait(ws, version="3", timeout=60)

    assert "was not serving version 3 after 1 minute (" in str(excinfo.value)
    assert "config_update=IN_PROGRESS" in str(excinfo.value)
    # Polled at 0, 15, 30, 45 and 60 seconds, then gave up.
    assert ws.serving_endpoints.get.call_count == 5


def test_a_missing_endpoint_is_the_callers_to_report() -> None:
    ws = MagicMock()
    ws.serving_endpoints.get.side_effect = NotFound("no endpoint")

    with pytest.raises(NotFound):
        _wait(ws, version=None)


# ---------------------------------------------------------------------------
# Deploy: create or update, then wait
# ---------------------------------------------------------------------------


def _deploy_config() -> Any:
    from haute.deploy._config import DeployConfig

    config = DeployConfig.from_cli_args(pipeline_file=__file__, model_name="pricing")
    return config.override(endpoint_suffix="-staging")


def _deploy_endpoint(ws: MagicMock) -> tuple[str | None, list[str]]:
    from haute.deploy import _mlflow

    progress: list[str] = []
    with (
        patch.object(_mlflow, "serving_workspace_client", return_value=ws),
        patch.object(_mlflow, "wait_for_endpoint") as wait,
    ):
        url = _mlflow._create_or_update_serving_endpoint(
            config=_deploy_config(),
            uc_model_name="main.pricing.pricing-staging",
            model_version=3,
            progress=progress.append,
        )
    wait.assert_called_once_with(ws, "pricing-staging", version="3", progress=progress.append)
    return url, progress


@pytest.mark.usefixtures("serving_pair")
def test_a_new_endpoint_is_created_and_waited_for() -> None:
    ws = MagicMock()
    ws.serving_endpoints.get.side_effect = NotFound("no endpoint")

    url, progress = _deploy_endpoint(ws)

    assert url == f"{_HOST}/serving-endpoints/pricing-staging/invocations"
    created = ws.serving_endpoints.create.call_args.kwargs
    assert created["name"] == "pricing-staging"
    [entity] = created["config"].served_entities
    assert (entity.entity_name, entity.entity_version) == ("main.pricing.pricing-staging", "3")
    ws.serving_endpoints.update_config.assert_not_called()
    assert progress[0] == "Creating serving endpoint pricing-staging for version 3..."


@pytest.mark.usefixtures("serving_pair")
def test_an_existing_endpoint_is_updated_and_waited_for() -> None:
    ws = MagicMock()
    ws.serving_endpoints.get.return_value = _endpoint(
        EndpointStateReady.READY, EndpointStateConfigUpdate.NOT_UPDATING, serving=("2",)
    )

    _deploy_endpoint(ws)

    [entity] = ws.serving_endpoints.update_config.call_args.kwargs["served_entities"]
    assert entity.entity_version == "3"
    ws.serving_endpoints.create.assert_not_called()


@pytest.mark.usefixtures("serving_pair")
def test_an_endpoint_already_updating_is_not_overridden() -> None:
    from haute.deploy import _mlflow

    ws = MagicMock()
    ws.serving_endpoints.get.return_value = _endpoint(
        EndpointStateReady.READY, EndpointStateConfigUpdate.IN_PROGRESS, serving=("2",)
    )

    with (
        patch.object(_mlflow, "serving_workspace_client", return_value=ws),
        pytest.raises(ServingEndpointError, match="already being updated"),
    ):
        _mlflow._create_or_update_serving_endpoint(
            config=_deploy_config(),
            uc_model_name="main.pricing.pricing-staging",
            model_version=3,
            progress=lambda _message: None,
        )
    ws.serving_endpoints.update_config.assert_not_called()


# ---------------------------------------------------------------------------
# Deploy refuses missing credentials before any work
# ---------------------------------------------------------------------------


@pytest.fixture
def no_databricks_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "MLFLOW_TRACKING_URI",
        "DATABRICKS_MLFLOW_HOST",
        "DATABRICKS_MLFLOW_TOKEN",
        "DATABRICKS_CONFIG_PROFILE",
        "MLFLOW_ENABLE_DB_SDK",
        RATING_HOST_ENV,
        RATING_TOKEN_ENV,
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.usefixtures("no_databricks_credentials")
def test_every_missing_deploy_credential_is_named_at_once() -> None:
    from haute.deploy._mlflow import check_databricks_credentials
    from haute.errors import DeployError

    with pytest.raises(DeployError) as excinfo:
        check_databricks_credentials()

    message = str(excinfo.value)
    assert "DATABRICKS_MLFLOW_HOST and DATABRICKS_MLFLOW_TOKEN" in message
    assert f"{RATING_HOST_ENV} and {RATING_TOKEN_ENV} are not set" in message


@pytest.mark.usefixtures("no_databricks_credentials")
def test_a_deploy_with_every_credential_passes_the_check(monkeypatch: pytest.MonkeyPatch) -> None:
    from haute.deploy._mlflow import check_databricks_credentials

    monkeypatch.setenv("DATABRICKS_MLFLOW_HOST", _HOST)
    monkeypatch.setenv("DATABRICKS_MLFLOW_TOKEN", "dapi-mlflow")
    monkeypatch.setenv(RATING_HOST_ENV, _HOST)
    monkeypatch.setenv(RATING_TOKEN_ENV, _TOKEN)

    check_databricks_credentials()


@pytest.mark.usefixtures("no_databricks_credentials")
def test_haute_deploy_refuses_missing_credentials_before_parsing(
    runner, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute.cli import cli

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    (tmp_path / "haute.toml").write_text(
        '[project]\nname = "p"\npipeline = "main.py"\n\n'
        '[deploy]\ntarget = "databricks"\nmodel_name = "p"\n',
        encoding="utf-8",
    )
    with patch("haute.deploy._config.resolve_config") as resolve:
        result = runner.invoke(cli, ["deploy"])

    assert result.exit_code == 1
    assert "credentials are not set" in result.output
    assert RATING_TOKEN_ENV in result.output
    resolve.assert_not_called()


def test_the_serving_client_ignores_ambient_profiles_and_service_principals(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real SDK constructor: an ambient profile the config file does not hold used to
    fail the client, and an ambient service principal would compete with the token."""
    from haute.deploy._databricks_serving import ServingCredentials

    config_file = tmp_path / "databrickscfg"
    config_file.write_text("[other]\nhost = https://other.example\ntoken = other\n")
    monkeypatch.setenv("DATABRICKS_CONFIG_FILE", str(config_file))
    monkeypatch.setenv("DATABRICKS_CONFIG_PROFILE", "missing")
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "service-principal")
    monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", "service-principal-secret")

    client = serving_workspace_client(ServingCredentials(host=_HOST, token=_TOKEN))

    assert client.config.host == _HOST
    assert client.config.authenticate()["Authorization"] == f"Bearer {_TOKEN}"


@pytest.fixture
def profiles(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_file = tmp_path / "databrickscfg"
    config_file.write_text(
        "[team]\nhost = https://adb-7.azuredatabricks.net\ntoken = dapi-team\n\n"
        "[notoken]\nhost = https://adb-8.azuredatabricks.net\n"
    )
    monkeypatch.setenv("DATABRICKS_CONFIG_FILE", str(config_file))
    monkeypatch.setenv(RATING_HOST_ENV, _HOST)
    monkeypatch.setenv(RATING_TOKEN_ENV, _TOKEN)


@pytest.mark.usefixtures("no_databricks_credentials", "profiles")
@pytest.mark.parametrize("profile", ["missing", "notoken"])
def test_a_profile_mlflow_cannot_use_is_refused_before_any_work(
    monkeypatch: pytest.MonkeyPatch, profile: str
) -> None:
    from haute.deploy._mlflow import check_databricks_credentials
    from haute.errors import DeployError

    monkeypatch.setenv("MLFLOW_TRACKING_URI", f"databricks://{profile}")

    with pytest.raises(DeployError) as excinfo:
        check_databricks_credentials()

    assert f"selects the Databricks profile '{profile}'" in str(excinfo.value)


@pytest.mark.usefixtures("no_databricks_credentials", "profiles")
def test_a_profile_with_a_host_and_token_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    from haute.deploy._mlflow import check_databricks_credentials

    monkeypatch.setenv("MLFLOW_TRACKING_URI", "databricks://team")

    check_databricks_credentials()
