"""MLflow's Databricks credential binding, observed at request level.

The general ``DATABRICKS_HOST``/``DATABRICKS_TOKEN`` pair (data access) and the
dedicated ``DATABRICKS_MLFLOW_HOST``/``DATABRICKS_MLFLOW_TOKEN`` pair are set to
different hosts and tokens. Every HTTP request MLflow issues is captured at
``requests.Session.request`` — MLflow's REST calls and its signed-URL storage
transfers both reach the network through that method — and the Databricks SDK
client class is replaced by a mock that must never be called.

Unity Catalog model artifacts can legitimately use the Databricks SDK, which
attaches ``Authorization`` through a requests auth hook after
``Session.request`` has been entered. Those tests capture every request at
``requests.Session.send`` instead, where the prepared request carries its final
headers and body, and record the real SDK clients that are built.
"""

from __future__ import annotations

import importlib
import inspect
import json
import os
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit

import pytest
import requests

from haute import _mlflow_utils
from haute._mlflow_errors import MlflowRemoteError
from haute._mlflow_utils import (
    _BOUND_SYMBOLS,
    _restore_mlflow_databricks_credentials,
    bind_mlflow_databricks_credentials,
    resolve_backend,
)
from haute._sandbox import set_project_root
from haute.errors import MlflowConfigError

GENERAL_HOST = "https://general.example.invalid"
GENERAL_TOKEN = "general-token-000"
MLFLOW_HOST = "https://mlflow.example.invalid"
MLFLOW_TOKEN = "mlflow-token-000"
STORAGE_HOST = "https://storage.example.invalid"

EXPERIMENT_ID = "4242"
RUN_ID = "0123456789abcdef"
MODEL_ID = "m-0123456789abcdef"
RUN_ARTIFACT_URI = f"dbfs:/databricks/mlflow-tracking/{EXPERIMENT_ID}/{RUN_ID}/artifacts"
LOGGED_MODEL_ARTIFACT_URI = (
    f"dbfs:/databricks/mlflow-tracking/{EXPERIMENT_ID}/logged_models/{MODEL_ID}/artifacts"
)
ARTIFACT_NAME = "model.cbm"
STORED_BYTES = b"bytes served by the signed storage URL"
UPLOADED_BYTES = b"bytes written through the signed storage URL"
STORAGE_HEADERS = {"x-amz-server-side-encryption": "AES256"}

GENERAL_CLIENT_ID = "general-client-id"
GENERAL_CLIENT_SECRET = "general-client-secret-000"

_DELETED_ENVIRONMENT = (
    "MLFLOW_TRACKING_URI",
    "DATABRICKS_CONFIG_PROFILE",
    "DATABRICKS_CLIENT_ID",
    "DATABRICKS_CLIENT_SECRET",
    "DATABRICKS_AUTH_TYPE",
    "MLFLOW_ENABLE_DB_SDK",
    "MLFLOW_DISABLE_DATABRICKS_SDK_FOR_RUN_ARTIFACTS",
    "MLFLOW_USE_DATABRICKS_SDK_MODEL_ARTIFACTS_REPO_FOR_UC",
)


@pytest.fixture(autouse=True)
def _hermetic_databricks_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", GENERAL_HOST)
    monkeypatch.setenv("DATABRICKS_TOKEN", GENERAL_TOKEN)
    monkeypatch.setenv("DATABRICKS_MLFLOW_HOST", MLFLOW_HOST)
    monkeypatch.setenv("DATABRICKS_MLFLOW_TOKEN", MLFLOW_TOKEN)
    for name in _DELETED_ENVIRONMENT:
        # Record the prior value first, so teardown also removes a value the
        # binder sets with ``os.environ.setdefault`` during the test.
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    monkeypatch.setenv("DATABRICKS_CONFIG_FILE", str(tmp_path / "databrickscfg"))
    # A fluent tracking URI left by another test would override the environment.
    monkeypatch.setattr("mlflow.tracking._tracking_service.utils._tracking_uri", None)
    set_project_root(tmp_path)


@dataclass(frozen=True)
class CapturedRequest:
    method: str
    url: str
    authorization: str | None
    headers: dict[str, str]
    body: bytes | None
    options: str

    @property
    def endpoint(self) -> tuple[str, str]:
        return self.method, self.url


class FakeDatabricks:
    """Scripted HTTP responses keyed by ``(method, URL path)``, recording every request.

    Routing ignores the host, so a request sent to the wrong host is still recorded
    and fails the host assertion rather than the routing.
    """

    def __init__(self, workspace_client: MagicMock | None = None) -> None:
        self.workspace_client = workspace_client
        self.requests: list[CapturedRequest] = []
        self._routes: dict[tuple[str, str], tuple[int, bytes, dict[str, str]]] = {}

    def respond_json(
        self, method: str, path: str, payload: dict[str, Any], status: int = 200
    ) -> None:
        content = json.dumps(payload).encode("utf-8")
        self._routes[(method, path)] = (status, content, {"Content-Type": "application/json"})

    def respond_bytes(
        self, method: str, path: str, content: bytes, headers: dict[str, str] | None = None
    ) -> None:
        self._routes[(method, path)] = (200, content, dict(headers or {}))

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        """``requests.Session.request`` stand-in: the caller's own headers and body."""
        options = repr({k: v for k, v in kwargs.items() if k not in {"data", "json", "headers"}})
        data = kwargs.get("data")
        if data is None and kwargs.get("json") is not None:
            data = json.dumps(kwargs["json"])
        return self._record_and_respond(method, url, kwargs.get("headers") or {}, data, options)

    def send(self, prepared: requests.PreparedRequest, **kwargs: Any) -> requests.Response:
        """``requests.Session.send`` stand-in: headers and body after auth hooks ran."""
        response = self._record_and_respond(
            str(prepared.method), str(prepared.url), prepared.headers, prepared.body, repr(kwargs)
        )
        response.request = prepared
        return response

    def _record_and_respond(
        self, method: str, url: str, raw_headers: Any, data: Any, options: str
    ) -> requests.Response:
        headers = {str(k): str(v) for k, v in raw_headers.items()}
        if hasattr(data, "read"):
            body: bytes | None = data.read()
        elif isinstance(data, str):
            body = data.encode("utf-8")
        else:
            body = data
        self.requests.append(
            CapturedRequest(
                method.upper(), url, headers.get("Authorization"), headers, body, options
            )
        )
        route = (method.upper(), urlsplit(url).path)
        if route not in self._routes:
            raise AssertionError(f"unscripted request: {method.upper()} {url}")
        status, content, response_headers = self._routes[route]
        response = requests.Response()
        response.status_code = status
        response.reason = "OK" if status == 200 else "Error"
        response.headers.update(response_headers)
        response._content = content
        response._content_consumed = True
        response.encoding = "utf-8"
        response.url = url
        return response

    def workspace_requests(self) -> list[CapturedRequest]:
        return [r for r in self.requests if not r.url.startswith(STORAGE_HOST)]

    def storage_requests(self) -> list[CapturedRequest]:
        return [r for r in self.requests if r.url.startswith(STORAGE_HOST)]

    def assert_bound_to_mlflow_pair(self) -> None:
        workspace = self.workspace_requests()
        assert workspace, "no Databricks API request was issued"
        for captured in workspace:
            assert captured.url.startswith(f"{MLFLOW_HOST}/api/"), captured
            assert captured.authorization == f"Bearer {MLFLOW_TOKEN}", captured
        assert GENERAL_TOKEN not in repr(self.requests)
        assert GENERAL_HOST not in repr(self.requests)
        assert self.workspace_client is not None
        self.workspace_client.assert_not_called()


@pytest.fixture()
def databricks(monkeypatch: pytest.MonkeyPatch) -> FakeDatabricks:
    fake = FakeDatabricks(MagicMock(name="WorkspaceClient"))

    def session_request(
        session: requests.Session, method: str, url: str, **kwargs: Any
    ) -> requests.Response:
        del session
        return fake.request(method, url, **kwargs)

    monkeypatch.setattr(requests.Session, "request", session_request)
    monkeypatch.setattr("databricks.sdk.WorkspaceClient", fake.workspace_client)
    return fake


def _bound_client(backend: _mlflow_utils.ResolvedBackend) -> Any:
    from mlflow.tracking import MlflowClient

    return MlflowClient(tracking_uri=backend.tracking_uri, registry_uri=backend.registry_uri)


_SEARCH_EXPERIMENTS = ("POST", "/api/2.0/mlflow/experiments/search")


# ---------------------------------------------------------------------------
# Tracking and Unity Catalog registry
# ---------------------------------------------------------------------------


def test_tracking_requests_reach_only_the_mlflow_host_with_the_mlflow_token(
    databricks: FakeDatabricks,
) -> None:
    databricks.respond_json(*_SEARCH_EXPERIMENTS, {"experiments": []})

    backend = resolve_backend("databricks")
    _bound_client(backend).search_experiments(max_results=1)

    assert backend.identity == f"databricks:{MLFLOW_HOST}|profile=|registry=databricks-uc"
    assert [r.endpoint for r in databricks.requests] == [
        ("POST", f"{MLFLOW_HOST}/api/2.0/mlflow/experiments/search")
    ]
    databricks.assert_bound_to_mlflow_pair()


def test_unity_catalog_registry_requests_reach_only_the_mlflow_host_with_the_mlflow_token(
    databricks: FakeDatabricks,
) -> None:
    databricks.respond_json(
        "GET", "/api/2.0/mlflow/unity-catalog/registered-models/search", {"registered_models": []}
    )

    backend = resolve_backend("databricks")
    assert backend.registry_uri == "databricks-uc"
    _bound_client(backend).search_registered_models(max_results=1)

    assert [r.endpoint for r in databricks.requests] == [
        ("GET", f"{MLFLOW_HOST}/api/2.0/mlflow/unity-catalog/registered-models/search")
    ]
    databricks.assert_bound_to_mlflow_pair()


# ---------------------------------------------------------------------------
# Experiment workspace folders
# ---------------------------------------------------------------------------

_GET_EXPERIMENT_BY_NAME = ("GET", "/api/2.0/mlflow/experiments/get-by-name")
_CREATE_EXPERIMENT = ("POST", "/api/2.0/mlflow/experiments/create")
_GET_EXPERIMENT = ("GET", "/api/2.0/mlflow/experiments/get")
_WORKSPACE_MKDIRS = ("POST", "/api/2.0/workspace/mkdirs")
_NEW_EXPERIMENT_NAME = "/Shared/haute/Model_Training_14"


def _experiment_payload() -> dict[str, Any]:
    return {
        "experiment": {
            "experiment_id": EXPERIMENT_ID,
            "name": _NEW_EXPERIMENT_NAME,
            "artifact_location": f"dbfs:/databricks/mlflow-tracking/{EXPERIMENT_ID}",
            "lifecycle_stage": "active",
        }
    }


def _ensure_bound_databricks_experiment() -> str:
    from mlflow.tracking import MlflowClient

    tracking_uri = resolve_backend("databricks").tracking_uri
    return _mlflow_utils.ensure_experiment(
        MlflowClient(tracking_uri=tracking_uri), tracking_uri, _NEW_EXPERIMENT_NAME
    )


def test_client_bound_new_experiment_creates_its_missing_workspace_folder_first(
    databricks: FakeDatabricks,
) -> None:
    # Training and deploy select their experiment on a destination-bound client.
    from mlflow.tracking import MlflowClient

    databricks.respond_json(
        *_GET_EXPERIMENT_BY_NAME,
        {"error_code": "RESOURCE_DOES_NOT_EXIST", "message": "Node not found"},
        status=404,
    )
    databricks.respond_json(*_WORKSPACE_MKDIRS, {})
    databricks.respond_json(*_CREATE_EXPERIMENT, {"experiment_id": EXPERIMENT_ID})
    tracking_uri = resolve_backend("databricks").tracking_uri

    experiment_id = _mlflow_utils.ensure_experiment(
        MlflowClient(tracking_uri=tracking_uri), tracking_uri, _NEW_EXPERIMENT_NAME
    )

    assert experiment_id == EXPERIMENT_ID
    endpoints = [(r.method, urlsplit(r.url).path) for r in databricks.requests]
    assert endpoints.index(_WORKSPACE_MKDIRS) < endpoints.index(_CREATE_EXPERIMENT)
    mkdirs = next(r for r in databricks.requests if urlsplit(r.url).path == _WORKSPACE_MKDIRS[1])
    assert json.loads(mkdirs.body or b"{}") == {"path": "/Shared/haute"}
    databricks.assert_bound_to_mlflow_pair()


def test_existing_experiment_does_not_touch_workspace_folders(
    databricks: FakeDatabricks,
) -> None:
    databricks.respond_json(*_GET_EXPERIMENT_BY_NAME, _experiment_payload())

    assert _ensure_bound_databricks_experiment() == EXPERIMENT_ID
    assert _WORKSPACE_MKDIRS not in [(r.method, urlsplit(r.url).path) for r in databricks.requests]
    databricks.assert_bound_to_mlflow_pair()


def test_uncreatable_workspace_folder_names_the_folder_and_creates_no_experiment(
    databricks: FakeDatabricks,
) -> None:
    databricks.respond_json(
        *_GET_EXPERIMENT_BY_NAME,
        {"error_code": "RESOURCE_DOES_NOT_EXIST", "message": "Node not found"},
        status=404,
    )
    databricks.respond_json(
        *_WORKSPACE_MKDIRS,
        {"error_code": "PERMISSION_DENIED", "message": "User cannot write to /Shared"},
        status=403,
    )

    with pytest.raises(MlflowRemoteError, match="denied permission.*/Shared/haute") as raised:
        _ensure_bound_databricks_experiment()

    assert raised.value.category == "permission"
    assert "Model_Training_14" in str(raised.value)
    assert _CREATE_EXPERIMENT not in [(r.method, urlsplit(r.url).path) for r in databricks.requests]
    databricks.assert_bound_to_mlflow_pair()


def test_local_experiments_never_call_the_workspace_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mlflow.tracking import MlflowClient

    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    tracking_uri = (tmp_path / "mlruns").as_uri()
    http_request = MagicMock(name="http_request")
    monkeypatch.setattr("mlflow.utils.rest_utils.http_request", http_request)
    client = MlflowClient(tracking_uri=tracking_uri)

    experiment_id = _mlflow_utils.ensure_experiment(client, tracking_uri, "pricing/frequency")

    assert client.get_experiment(experiment_id).name == "pricing/frequency"
    http_request.assert_not_called()


# ---------------------------------------------------------------------------
# Run and logged-model artifacts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArtifactTarget:
    kind: str
    artifact_uri: str
    repository_class: str
    root: tuple[str, str]
    listing: tuple[str, str]
    read_credentials: tuple[str, str]
    write_credentials: tuple[str, str]
    storage_path: str

    def signed_url(self, access: str) -> str:
        return f"{STORAGE_HOST}{self.storage_path}?signature={access}"

    def script(self, databricks: FakeDatabricks) -> None:
        databricks.respond_json(*self.root, self._root_payload())
        databricks.respond_json(
            *self.listing,
            {"files": [{"path": ARTIFACT_NAME, "is_dir": False, "file_size": len(STORED_BYTES)}]},
        )
        databricks.respond_json(*self.read_credentials, self._credentials_payload("read"))
        databricks.respond_json(*self.write_credentials, self._credentials_payload("write"))
        databricks.respond_bytes("GET", self.storage_path, STORED_BYTES)
        databricks.respond_bytes("PUT", self.storage_path, b"")

    def _root_payload(self) -> dict[str, Any]:
        info = {"experiment_id": EXPERIMENT_ID, "artifact_uri": self.artifact_uri}
        if self.kind == "run":
            return {"run": {"info": {"run_id": RUN_ID, **info}}}
        return {"model": {"info": {"model_id": MODEL_ID, **info}}}

    def _credentials_payload(self, access: str) -> dict[str, Any]:
        credential = {
            "signed_uri": self.signed_url(access),
            "type": "AWS_PRESIGNED_URL",
            "headers": [{"name": k, "value": v} for k, v in STORAGE_HEADERS.items()],
        }
        if self.kind == "run":
            return {"credential_infos": [{"run_id": RUN_ID, "path": ARTIFACT_NAME, **credential}]}
        return {"credentials": [{"model_id": MODEL_ID, "credential_info": credential}]}


_LOGGED_MODEL_API = f"/api/2.0/mlflow/logged-models/{MODEL_ID}"
ARTIFACT_TARGETS = (
    ArtifactTarget(
        kind="run",
        artifact_uri=RUN_ARTIFACT_URI,
        repository_class=(
            "mlflow.store.artifact.databricks_run_artifact_repo.DatabricksRunArtifactRepository"
        ),
        root=("GET", "/api/2.0/mlflow/runs/get"),
        listing=("GET", "/api/2.0/mlflow/artifacts/list"),
        read_credentials=("POST", "/api/2.0/mlflow/artifacts/credentials-for-read"),
        write_credentials=("POST", "/api/2.0/mlflow/artifacts/credentials-for-write"),
        storage_path=f"/bucket/runs/{RUN_ID}/{ARTIFACT_NAME}",
    ),
    ArtifactTarget(
        kind="logged_model",
        artifact_uri=LOGGED_MODEL_ARTIFACT_URI,
        repository_class=(
            "mlflow.store.artifact.databricks_logged_model_artifact_repo."
            "DatabricksLoggedModelArtifactRepository"
        ),
        root=("GET", _LOGGED_MODEL_API),
        listing=("GET", f"{_LOGGED_MODEL_API}/artifacts/directories"),
        read_credentials=("POST", f"{_LOGGED_MODEL_API}/artifacts/credentials-for-download"),
        write_credentials=("POST", f"{_LOGGED_MODEL_API}/artifacts/credentials-for-upload"),
        storage_path=f"/bucket/logged-models/{MODEL_ID}/{ARTIFACT_NAME}",
    ),
)


@pytest.fixture(params=ARTIFACT_TARGETS, ids=lambda target: target.kind)
def artifact_target(
    request: pytest.FixtureRequest,
    databricks: FakeDatabricks,
    monkeypatch: pytest.MonkeyPatch,
) -> ArtifactTarget:
    target: ArtifactTarget = request.param
    target.script(databricks)
    # MLflow's REST artifact repository authenticates through the fluent tracking URI.
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "databricks")
    return target


def _repository(target: ArtifactTarget) -> Any:
    """Bind through the destination resolver, then let MLflow select the repository."""
    from mlflow.store.artifact.artifact_repository_registry import get_artifact_repository

    resolve_backend("databricks")
    repository = get_artifact_repository(target.artifact_uri)
    module_name, class_name = target.repository_class.rsplit(".", 1)
    assert type(repository) is getattr(importlib.import_module(module_name), class_name)
    return repository


def _api(target_route: tuple[str, str]) -> tuple[str, str]:
    method, path = target_route
    return method, f"{MLFLOW_HOST}{path}"


def test_artifact_listing_uses_only_mlflow_bound_rest_requests(
    databricks: FakeDatabricks, artifact_target: ArtifactTarget
) -> None:
    from mlflow.entities import FileInfo

    files = _repository(artifact_target).list_artifacts()

    assert files == [FileInfo(ARTIFACT_NAME, False, len(STORED_BYTES))]
    assert [r.endpoint for r in databricks.requests] == [
        _api(artifact_target.root),
        _api(artifact_target.listing),
    ]
    databricks.assert_bound_to_mlflow_pair()


def test_artifact_upload_sends_bytes_only_to_the_signed_storage_url(
    databricks: FakeDatabricks, artifact_target: ArtifactTarget, tmp_path: Path
) -> None:
    local_file = tmp_path / "upload" / ARTIFACT_NAME
    local_file.parent.mkdir()
    local_file.write_bytes(UPLOADED_BYTES)

    _repository(artifact_target).log_artifact(str(local_file))

    assert [r.endpoint for r in databricks.workspace_requests()] == [
        _api(artifact_target.root),
        _api(artifact_target.write_credentials),
    ]
    databricks.assert_bound_to_mlflow_pair()
    [transfer] = databricks.storage_requests()
    assert transfer.endpoint == ("PUT", artifact_target.signed_url("write"))
    assert transfer.body == UPLOADED_BYTES
    assert transfer.headers == STORAGE_HEADERS
    assert MLFLOW_TOKEN not in repr(transfer)
    assert GENERAL_TOKEN not in repr(transfer)


def test_artifact_download_reads_bytes_only_from_the_signed_storage_url(
    databricks: FakeDatabricks, artifact_target: ArtifactTarget, tmp_path: Path
) -> None:
    destination = tmp_path / "download"
    destination.mkdir()

    local_path = _repository(artifact_target).download_artifacts(
        ARTIFACT_NAME, dst_path=str(destination)
    )

    assert Path(local_path).read_bytes() == STORED_BYTES
    assert [r.endpoint for r in databricks.workspace_requests()] == [
        _api(artifact_target.root),
        _api(artifact_target.listing),
        _api(artifact_target.listing),
        _api(artifact_target.read_credentials),
    ]
    databricks.assert_bound_to_mlflow_pair()
    [transfer] = databricks.storage_requests()
    assert transfer.endpoint == ("GET", artifact_target.signed_url("read"))
    assert transfer.headers == STORAGE_HEADERS
    assert MLFLOW_TOKEN not in repr(transfer)
    assert GENERAL_TOKEN not in repr(transfer)


def test_artifact_rest_failure_propagates_without_an_sdk_client(
    databricks: FakeDatabricks, artifact_target: ArtifactTarget, tmp_path: Path
) -> None:
    from mlflow.exceptions import RestException

    databricks.respond_json(
        *artifact_target.write_credentials,
        {"error_code": "PERMISSION_DENIED", "message": "scope denied"},
        status=403,
    )
    local_file = tmp_path / ARTIFACT_NAME
    local_file.write_bytes(UPLOADED_BYTES)
    repository = _repository(artifact_target)

    with pytest.raises(RestException) as excinfo:
        repository.log_artifact(str(local_file))

    assert excinfo.value.error_code == "PERMISSION_DENIED"
    assert [r.endpoint for r in databricks.requests] == [
        _api(artifact_target.root),
        _api(artifact_target.write_credentials),
    ]
    databricks.assert_bound_to_mlflow_pair()


# ---------------------------------------------------------------------------
# Unity Catalog model artifacts through MLflow's Databricks SDK repository
# ---------------------------------------------------------------------------

UC_MODEL = "cat.sch.model"
UC_VERSION = "1"
UC_FILES_ROOT = "/Models/cat/sch/model/1"
UC_SERVED_BYTES = b"MLmodel bytes served by the Files API"
UC_UPLOADS = {"MLmodel": b"MLmodel bytes uploaded", "model.cbm": b"catboost bytes uploaded"}
_UC_SDK_REPOSITORY_ENABLED = (
    "GET",
    "/api/2.0/mlflow/unity-catalog/registered-models"
    ":is-databricks-sdk-models-artifact-repository-enabled",
)
_UC_EMIT_LINEAGE = ("POST", "/api/2.0/mlflow/unity-catalog/model-versions/emit-lineage")
_FILES_DIRECTORY = f"/api/2.0/fs/directories{UC_FILES_ROOT}"
_FILES_CREATE_DOWNLOAD_URL = ("POST", "/api/2.0/fs/create-download-url")


@dataclass(frozen=True)
class UnityCatalogTraffic:
    http: FakeDatabricks
    sdk_clients: list[Any]

    def assert_bound_to_mlflow_pair(self) -> None:
        assert self.http.requests, "no request was issued"
        for captured in self.http.requests:
            assert captured.url.startswith(f"{MLFLOW_HOST}/api/"), captured
            assert captured.authorization == f"Bearer {MLFLOW_TOKEN}", captured
            assert "/oidc/" not in urlsplit(captured.url).path, captured
        wire = repr(self.http.requests)
        for foreign in (GENERAL_TOKEN, GENERAL_CLIENT_SECRET, GENERAL_CLIENT_ID, GENERAL_HOST):
            assert foreign not in wire, foreign
        assert [(c.config.host, c.config.auth_type) for c in self.sdk_clients] == [
            (MLFLOW_HOST, "pat")
        ]


@pytest.fixture()
def unity_catalog(monkeypatch: pytest.MonkeyPatch) -> UnityCatalogTraffic:
    """Capture at ``Session.send`` with a data-access service principal in the environment."""
    from databricks.sdk import WorkspaceClient

    monkeypatch.setenv("DATABRICKS_CLIENT_ID", GENERAL_CLIENT_ID)
    monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", GENERAL_CLIENT_SECRET)
    monkeypatch.setenv("DATABRICKS_AUTH_TYPE", "oauth-m2m")
    # MLflow's Unity Catalog repositories build their client from the fluent tracking URI.
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "databricks")

    traffic = UnityCatalogTraffic(FakeDatabricks(), [])

    class RecordingWorkspaceClient(WorkspaceClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            traffic.sdk_clients.append(self)

    def session_send(
        session: requests.Session, prepared: requests.PreparedRequest, **kwargs: Any
    ) -> requests.Response:
        del session
        return traffic.http.send(prepared, **kwargs)

    monkeypatch.setattr(requests.Session, "send", session_send)
    monkeypatch.setattr("databricks.sdk.WorkspaceClient", RecordingWorkspaceClient)
    traffic.http.respond_json(
        *_UC_SDK_REPOSITORY_ENABLED, {"is_databricks_sdk_models_artifact_repository_enabled": True}
    )
    return traffic


def _without_query(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def test_unity_catalog_sdk_download_sends_only_the_mlflow_token(
    unity_catalog: UnityCatalogTraffic, tmp_path: Path
) -> None:
    from mlflow.store.artifact.artifact_repository_registry import get_artifact_repository
    from mlflow.store.artifact.unity_catalog_models_artifact_repo import (
        UnityCatalogModelsArtifactRepository,
    )

    http = unity_catalog.http
    http.respond_json(*_UC_EMIT_LINEAGE, {})
    http.respond_bytes("HEAD", _FILES_DIRECTORY, b"")
    http.respond_json(
        "GET",
        _FILES_DIRECTORY,
        {
            "contents": [
                {
                    "path": f"{UC_FILES_ROOT}/MLmodel",
                    "name": "MLmodel",
                    "is_directory": False,
                    "file_size": len(UC_SERVED_BYTES),
                }
            ]
        },
    )
    # The workspace has presigned download URLs disabled, so the SDK downloads
    # through the Files API on the workspace host.
    http.respond_json(
        *_FILES_CREATE_DOWNLOAD_URL,
        {
            "error_code": "PERMISSION_DENIED",
            "message": "Presigned URLs are not enabled",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                    "reason": "FILES_API_API_IS_NOT_ENABLED",
                    "domain": "filesystem.databricks.com",
                }
            ],
        },
        status=403,
    )
    http.respond_bytes(
        "GET",
        f"/api/2.0/fs/files{UC_FILES_ROOT}/MLmodel",
        UC_SERVED_BYTES,
        headers={
            "Content-Type": "application/octet-stream",
            "Content-Length": str(len(UC_SERVED_BYTES)),
            "Last-Modified": "Sat, 12 Sep 2026 00:00:00 GMT",
        },
    )
    destination = tmp_path / "download"
    destination.mkdir()

    backend = resolve_backend("databricks")
    repository = get_artifact_repository(
        f"models:/{UC_MODEL}/{UC_VERSION}", registry_uri=backend.registry_uri
    )
    assert type(repository.repo) is UnityCatalogModelsArtifactRepository
    repository.download_artifacts("", dst_path=str(destination))

    assert (destination / "MLmodel").read_bytes() == UC_SERVED_BYTES
    listing = [
        ("HEAD", f"{MLFLOW_HOST}{_FILES_DIRECTORY}"),
        ("GET", f"{MLFLOW_HOST}{_FILES_DIRECTORY}"),
    ]
    assert [(r.method, _without_query(r.url)) for r in http.requests] == [
        (_UC_SDK_REPOSITORY_ENABLED[0], f"{MLFLOW_HOST}{_UC_SDK_REPOSITORY_ENABLED[1]}"),
        (_UC_EMIT_LINEAGE[0], f"{MLFLOW_HOST}{_UC_EMIT_LINEAGE[1]}"),
        *listing,
        *listing,
        *listing,
        (_FILES_CREATE_DOWNLOAD_URL[0], f"{MLFLOW_HOST}{_FILES_CREATE_DOWNLOAD_URL[1]}"),
        ("GET", f"{MLFLOW_HOST}/api/2.0/fs/files{UC_FILES_ROOT}/MLmodel"),
    ]
    unity_catalog.assert_bound_to_mlflow_pair()


def test_unity_catalog_sdk_upload_through_the_registry_store_sends_only_the_mlflow_token(
    unity_catalog: UnityCatalogTraffic, tmp_path: Path
) -> None:
    from mlflow.protos.databricks_uc_registry_messages_pb2 import ModelVersion
    from mlflow.store._unity_catalog.registry.rest_store import UcModelRegistryStore

    http = unity_catalog.http
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    for name, content in UC_UPLOADS.items():
        (model_dir / name).write_bytes(content)
        http.respond_bytes("PUT", f"/api/2.0/fs/files{UC_FILES_ROOT}/{name}", b"")

    backend = resolve_backend("databricks")
    store = _bound_client(backend)._get_registry_client().store
    assert isinstance(store, UcModelRegistryStore)
    # The registry store's own upload step after it creates a model version.
    repository = store._get_artifact_repo(ModelVersion(name=UC_MODEL, version=UC_VERSION), UC_MODEL)
    repository.log_artifacts(local_dir=str(model_dir), artifact_path="")

    [enabled, *uploads] = http.requests
    assert enabled.endpoint == (
        _UC_SDK_REPOSITORY_ENABLED[0],
        f"{MLFLOW_HOST}{_UC_SDK_REPOSITORY_ENABLED[1]}",
    )
    # Uploads run on MLflow's thread pool, so their order is not fixed.
    assert sorted((r.method, _without_query(r.url), r.body) for r in uploads) == sorted(
        ("PUT", f"{MLFLOW_HOST}/api/2.0/fs/files{UC_FILES_ROOT}/{name}", content)
        for name, content in UC_UPLOADS.items()
    )
    unity_catalog.assert_bound_to_mlflow_pair()


@pytest.mark.parametrize("credential_form", ["pair_token_unset", "token_less_profile"])
def test_unity_catalog_sdk_client_refuses_missing_token_without_default_auth(
    unity_catalog: UnityCatalogTraffic,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    credential_form: str,
) -> None:
    from mlflow.exceptions import MlflowException
    from mlflow.store.artifact.databricks_sdk_models_artifact_repo import (
        DatabricksSDKModelsArtifactRepository,
    )

    if credential_form == "token_less_profile":
        (tmp_path / "databrickscfg").write_text(
            "[sp]\nhost = https://service-principal.example.invalid\n"
            "client_id = profile-client-id\nclient_secret = profile-client-secret\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("MLFLOW_TRACKING_URI", "databricks://sp")
    backend = resolve_backend("databricks")
    if credential_form == "pair_token_unset":
        monkeypatch.delenv("DATABRICKS_MLFLOW_TOKEN")

    with pytest.raises(MlflowException) as excinfo:
        DatabricksSDKModelsArtifactRepository(
            UC_MODEL, UC_VERSION, registry_uri=backend.registry_uri
        )

    expected = (
        "DATABRICKS_MLFLOW_TOKEN"
        if credential_form == "pair_token_unset"
        else "personal access token"
    )
    assert expected in excinfo.value.message
    assert unity_catalog.http.requests == []
    assert unity_catalog.sdk_clients == []


# ---------------------------------------------------------------------------
# Repointing, unset pair, and the profile form
# ---------------------------------------------------------------------------


def test_repointed_mlflow_pair_is_followed_by_the_next_request_on_the_same_client(
    databricks: FakeDatabricks, monkeypatch: pytest.MonkeyPatch
) -> None:
    repointed_host = "https://mlflow-repointed.example.invalid"
    repointed_token = "mlflow-token-111"
    databricks.respond_json(*_SEARCH_EXPERIMENTS, {"experiments": []})

    backend_a = resolve_backend("databricks")
    client = _bound_client(backend_a)
    client.search_experiments(max_results=1)
    monkeypatch.setenv("DATABRICKS_MLFLOW_HOST", repointed_host)
    monkeypatch.setenv("DATABRICKS_MLFLOW_TOKEN", repointed_token)
    backend_b = resolve_backend("databricks")
    client.search_experiments(max_results=1)

    assert backend_a.identity == f"databricks:{MLFLOW_HOST}|profile=|registry=databricks-uc"
    assert backend_b.identity == f"databricks:{repointed_host}|profile=|registry=databricks-uc"
    assert backend_b.digest != backend_a.digest
    assert [(r.url, r.authorization) for r in databricks.requests] == [
        (f"{MLFLOW_HOST}/api/2.0/mlflow/experiments/search", f"Bearer {MLFLOW_TOKEN}"),
        (f"{repointed_host}/api/2.0/mlflow/experiments/search", f"Bearer {repointed_token}"),
    ]
    assert GENERAL_TOKEN not in repr(databricks.requests)
    databricks.workspace_client.assert_not_called()


def test_bound_provider_with_the_pair_unset_raises_without_consulting_the_default_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mlflow.exceptions import MlflowException
    from mlflow.legacy_databricks_cli.configure.provider import ProfileConfigProvider
    from mlflow.utils.databricks_utils import get_databricks_host_creds

    resolve_backend("databricks")
    monkeypatch.delenv("DATABRICKS_MLFLOW_TOKEN")

    with pytest.raises(MlflowException) as unset:
        get_databricks_host_creds("databricks")
    assert "DATABRICKS_MLFLOW_HOST" in unset.value.message
    assert "DATABRICKS_MLFLOW_TOKEN" in unset.value.message

    default_host = "https://default-profile.example.invalid"
    (tmp_path / "databrickscfg").write_text(
        f"[DEFAULT]\nhost = {default_host}\ntoken = default-profile-token\n", encoding="utf-8"
    )
    # The profile file is valid: MLflow's DEFAULT-profile provider would accept it.
    assert ProfileConfigProvider(None).get_config().host == default_host

    with pytest.raises(MlflowException) as with_default_profile:
        get_databricks_host_creds("databricks")
    assert "DATABRICKS_MLFLOW_HOST" in with_default_profile.value.message
    assert "DATABRICKS_MLFLOW_TOKEN" in with_default_profile.value.message


def test_profile_uri_binds_the_profile_even_with_both_pairs_set(
    databricks: FakeDatabricks, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile_host = "https://profile.example.invalid"
    profile_token = "profile-token-000"
    (tmp_path / "databrickscfg").write_text(
        f"[team]\nhost = {profile_host}\ntoken = {profile_token}\n", encoding="utf-8"
    )
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "databricks://team")
    databricks.respond_json(*_SEARCH_EXPERIMENTS, {"experiments": []})

    backend = resolve_backend("databricks")
    _bound_client(backend).search_experiments(max_results=1)

    assert backend.tracking_uri == "databricks://team"
    assert [(r.url, r.authorization) for r in databricks.requests] == [
        (f"{profile_host}/api/2.0/mlflow/experiments/search", f"Bearer {profile_token}")
    ]
    assert MLFLOW_TOKEN not in repr(databricks.requests)
    assert GENERAL_TOKEN not in repr(databricks.requests)
    databricks.workspace_client.assert_not_called()


# ---------------------------------------------------------------------------
# Binder lifecycle and the MLflow upgrade guard
# ---------------------------------------------------------------------------


_EXPECTED_BOUND_SYMBOLS = (
    ("mlflow.utils.databricks_utils", "EnvironmentVariableConfigProvider"),
    ("mlflow.store.artifact.databricks_tracking_artifact_repo", "DatabricksSdkArtifactRepository"),
    (
        "mlflow.store.artifact.databricks_sdk_models_artifact_repo",
        "_get_databricks_workspace_client",
    ),
)


def _mlflow_globals() -> tuple[Any, ...]:
    return tuple(getattr(importlib.import_module(module), name) for module, name in _BOUND_SYMBOLS)


def test_binder_is_idempotent_restorable_and_reapplicable() -> None:
    assert _BOUND_SYMBOLS == _EXPECTED_BOUND_SYMBOLS
    _restore_mlflow_databricks_credentials()
    originals = _mlflow_globals()
    assert len(originals) == len(_EXPECTED_BOUND_SYMBOLS)

    bind_mlflow_databricks_credentials()
    first = _mlflow_globals()
    bind_mlflow_databricks_credentials()
    second = _mlflow_globals()

    assert all(bound is not original for bound, original in zip(first, originals, strict=True))
    assert all(again is bound for again, bound in zip(second, first, strict=True))
    assert _mlflow_utils._databricks_binding_originals == originals

    _restore_mlflow_databricks_credentials()
    restored = _mlflow_globals()
    assert all(value is original for value, original in zip(restored, originals, strict=True))
    assert _mlflow_utils._databricks_binding_originals is None

    bind_mlflow_databricks_credentials()
    reapplied = _mlflow_globals()
    assert all(value is not original for value, original in zip(reapplied, originals, strict=True))
    assert _mlflow_utils._databricks_binding_originals == originals


def test_binder_is_a_no_op_when_mlflow_is_absent() -> None:
    _restore_mlflow_databricks_credentials()
    originals = _mlflow_globals()

    with patch.dict(sys.modules, {"mlflow": None}):
        bind_mlflow_databricks_credentials()
        assert _mlflow_utils._databricks_binding_originals is None

    assert all(
        value is original for value, original in zip(_mlflow_globals(), originals, strict=True)
    )
    assert _mlflow_utils._databricks_binding_originals is None


def test_binder_refuses_an_mlflow_without_the_environment_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mlflow.utils.databricks_utils as databricks_utils

    _restore_mlflow_databricks_credentials()
    monkeypatch.delattr(databricks_utils, "EnvironmentVariableConfigProvider")

    with pytest.raises(MlflowConfigError, match="cannot bind Databricks MLflow credentials"):
        bind_mlflow_databricks_credentials()
    assert _mlflow_utils._databricks_binding_originals is None


def test_mlflow_still_resolves_the_bound_symbols_at_call_time() -> None:
    import mlflow.store.artifact.databricks_sdk_models_artifact_repo as sdk_models_repo
    import mlflow.utils.databricks_utils as databricks_utils
    from mlflow.store.artifact.databricks_tracking_artifact_repo import (
        DatabricksTrackingArtifactRepository,
    )

    assert _BOUND_SYMBOLS == _EXPECTED_BOUND_SYMBOLS
    for module_name, attribute in _BOUND_SYMBOLS:
        assert hasattr(importlib.import_module(module_name), attribute), (module_name, attribute)
    assert callable(sdk_models_repo._get_databricks_workspace_client)
    assert "EnvironmentVariableConfigProvider()" in inspect.getsource(
        databricks_utils._get_databricks_creds_config
    )
    assert "DatabricksSdkArtifactRepository(" in inspect.getsource(
        DatabricksTrackingArtifactRepository.__init__
    )
    assert "_get_databricks_workspace_client(" in inspect.getsource(
        sdk_models_repo.DatabricksSDKModelsArtifactRepository.__init__
    )


# ---------------------------------------------------------------------------
# Cold start
# ---------------------------------------------------------------------------

_COLD_START_SCRIPT = textwrap.dedent(
    """
    import json

    import requests

    captured = []

    def fake_request(session, method, url, **kwargs):
        captured.append([url, (kwargs.get("headers") or {}).get("Authorization")])
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"experiments": []}'
        response._content_consumed = True
        response.encoding = "utf-8"
        response.url = url
        return response

    requests.Session.request = fake_request

    from haute._mlflow_utils import resolve_backend

    backend = resolve_backend("databricks")

    from mlflow.tracking import MlflowClient

    client = MlflowClient(tracking_uri=backend.tracking_uri, registry_uri=backend.registry_uri)
    client.search_experiments(max_results=1)
    print("CAPTURED=" + json.dumps(captured))
    """
)


def test_cold_interpreter_binds_before_the_first_client_request(tmp_path: Path) -> None:
    env = dict(os.environ)
    for name in _DELETED_ENVIRONMENT:
        env.pop(name, None)
    env.update(
        {
            "DATABRICKS_HOST": GENERAL_HOST,
            "DATABRICKS_TOKEN": GENERAL_TOKEN,
            "DATABRICKS_MLFLOW_HOST": MLFLOW_HOST,
            "DATABRICKS_MLFLOW_TOKEN": MLFLOW_TOKEN,
            "DATABRICKS_CONFIG_FILE": str(tmp_path / "databrickscfg"),
        }
    )

    completed = subprocess.run(
        [sys.executable, "-c", _COLD_START_SCRIPT],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    [line] = [out for out in completed.stdout.splitlines() if out.startswith("CAPTURED=")]
    assert json.loads(line[len("CAPTURED=") :]) == [
        [f"{MLFLOW_HOST}/api/2.0/mlflow/experiments/search", f"Bearer {MLFLOW_TOKEN}"]
    ]


def test_local_and_server_resolution_never_import_or_bind_mlflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Binding happens only when a Databricks destination is resolved.

    A stand-in ``mlflow`` module without a spec (as many tests install) would make
    the binder's package probe raise, and a real import costs seconds, so neither
    may happen while resolving a server or local destination.
    """
    import sys
    from unittest.mock import MagicMock, patch

    from haute.modelling._mlflow_settings import (
        MlflowDestinationUnconfigured,
        resolve_destination,
    )

    monkeypatch.delenv("DATABRICKS_MLFLOW_HOST", raising=False)
    monkeypatch.delenv("DATABRICKS_MLFLOW_TOKEN", raising=False)
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://tracking.example.invalid:5000")
    _mlflow_utils._restore_mlflow_databricks_credentials()

    with patch.dict(sys.modules, {"mlflow": MagicMock(name="mlflow-without-spec")}):
        assert resolve_destination("server").mode == "server"
        assert resolve_destination("local").mode == "local"
        with pytest.raises(MlflowDestinationUnconfigured):
            resolve_destination("databricks")

    assert _mlflow_utils._databricks_binding_originals is None
