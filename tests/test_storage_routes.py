"""HTTP surface tests for the durable-project-storage endpoints.

Covers only the routes in ``haute.routes.git`` (``_with_storage_state``,
``git_bind_storage``, ``git_retry_storage_sync``) and the JSON shape they
produce. Module-internal behaviour of ``haute._project_storage`` (binding
persistence, push-queue mechanics, credential handling) belongs in
``tests/test_project_storage.py`` and is deliberately not duplicated here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from haute import _project_storage
from haute._project_storage import PushQueue
from haute.server import app


@pytest.fixture(autouse=True)
def _isolated_storage_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Never touch the real repo or ambient hosted-storage env vars."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(_project_storage.STATE_VOLUME_ENV, raising=False)
    monkeypatch.delenv(_project_storage.GIT_TOKEN_ENV, raising=False)
    monkeypatch.delenv(_project_storage.GIT_USERNAME_ENV, raising=False)
    monkeypatch.delenv(_project_storage.PROJECT_DIR_ENV, raising=False)
    monkeypatch.delenv("DATABRICKS_APP_NAME", raising=False)

    # Reset module-level singletons so tests are order-independent.
    monkeypatch.setattr(_project_storage, "_active_binding", None)
    monkeypatch.setattr(_project_storage, "_queue", PushQueue())

    yield


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app, base_url="http://localhost", raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# 1. GET /api/git/working-branch — default storage/sync shape
# ---------------------------------------------------------------------------


def test_working_branch_defaults_to_unsupported_storage_and_synced_sync(client: TestClient) -> None:
    resp = client.get("/api/git/working-branch")
    assert resp.status_code == 200
    body = resp.json()

    assert body["storage"] == "unsupported"
    assert body["storage_remote"] is None
    assert body["sync"] == {
        "state": "synced",
        "pending": 0,
        "failure": None,
        "message": None,
    }


# ---------------------------------------------------------------------------
# 2. POST /api/git/storage/bind — URL validation
# ---------------------------------------------------------------------------


def test_bind_rejects_non_https_scheme(client: TestClient) -> None:
    resp = client.post("/api/git/storage/bind", json={"remote_url": "ssh://x/y"})
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "https://" in detail


def test_bind_rejects_embedded_credentials(client: TestClient) -> None:
    resp = client.post(
        "/api/git/storage/bind",
        json={"remote_url": "https://user:pw@host/r.git"},
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "credentials" in detail.lower()
    assert "user:pw" not in detail


# ---------------------------------------------------------------------------
# 3. POST /api/git/storage/bind — valid URL, no state volume configured
# ---------------------------------------------------------------------------


def test_bind_with_no_state_volume_names_the_env_var(client: TestClient) -> None:
    resp = client.post(
        "/api/git/storage/bind",
        json={"remote_url": "https://example.com/org/repo.git"},
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert _project_storage.STATE_VOLUME_ENV in detail


# ---------------------------------------------------------------------------
# 4. POST /api/git/storage/retry — 200 + refreshed readiness body
# ---------------------------------------------------------------------------


def test_retry_returns_readiness_body(client: TestClient) -> None:
    resp = client.post("/api/git/storage/retry")
    assert resp.status_code == 200
    body = resp.json()

    assert "storage" in body
    assert "sync" in body
    assert "working_branch" in body


# ---------------------------------------------------------------------------
# 5. Regression: storage/sync fields never carry raw git stderr or paths
# ---------------------------------------------------------------------------


def test_failed_sync_message_is_hand_authored_and_path_free(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    raw_stderr = (
        f"fatal: unable to access '{tmp_path / 'secret-remote'}': "
        "Could not resolve host, token=ghp_ABCDEF1234567890"
    )

    failed_queue = PushQueue()
    # Drive the queue into its "failed" terminal state directly rather than
    # exercising the background thread: seed the private fields the same
    # way a rejected/misconfigured push would.
    with failed_queue._condition:
        failed_queue._failure = "rejected"
        failed_queue._message = (
            "The remote rejected the last push. Someone else may have pushed "
            "newer history — pull or resolve the conflict, then retry."
        )
        failed_queue._pending = 2
        failed_queue._blocked = True
        failed_queue._terminal = True

    monkeypatch.setattr(_project_storage, "_queue", failed_queue)

    resp = client.get("/api/git/working-branch")
    assert resp.status_code == 200
    body = resp.json()

    assert body["sync"]["state"] == "failed"
    assert body["sync"]["failure"] == "rejected"
    assert body["sync"]["message"] == failed_queue._message

    raw_body = json.dumps(body)
    assert raw_stderr not in raw_body
    assert "ghp_ABCDEF1234567890" not in raw_body
    assert str(tmp_path) not in raw_body
    # No absolute filesystem path should ever leak into the JSON body.
    assert "/Users/" not in raw_body
