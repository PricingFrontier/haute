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
    monkeypatch.setattr(_project_storage, "_active_lineage", None)
    monkeypatch.setattr(_project_storage, "_queue", PushQueue())
    monkeypatch.setattr(_project_storage, "_bind_task", _project_storage.BindTask())

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
# 3b. POST /api/git/storage/bind — async: accepted now, outcome later
# ---------------------------------------------------------------------------


def _wait_for_bind(client: TestClient, state: str, timeout: float = 5.0) -> dict:
    """Poll readiness until the background bind reaches *state*."""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        bind = client.get("/api/git/working-branch").json()["storage_bind"]
        if bind["state"] == state:
            return bind
        time.sleep(0.02)
    raise AssertionError(f"bind never reached {state!r}")


def test_bind_returns_immediately_without_waiting_for_the_publish(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The session must stay usable while a whole project is published."""
    import threading

    release = threading.Event()
    started = threading.Event()

    def slow_bind(url: str, project_root: Path, bound_by: object = None) -> str:
        started.set()
        release.wait(timeout=5)
        return "adopted"

    monkeypatch.setattr(_project_storage, "bind_remote", slow_bind)
    monkeypatch.setenv(_project_storage.STATE_VOLUME_ENV, "workspace.default.haute_state")

    resp = client.post(
        "/api/git/storage/bind",
        json={"remote_url": "uc://workspace.default.projects/demo"},
    )
    # Returned while the publish is still blocked, not after it.
    assert resp.status_code == 200
    assert resp.json()["outcome"] == "pending"
    assert started.wait(timeout=5)

    assert _wait_for_bind(client, "running")["remote_url"].startswith("uc://")
    release.set()
    done = _wait_for_bind(client, "succeeded")
    assert done["outcome"] == "adopted"


def test_a_claimed_location_reports_the_holder_on_the_readiness_surface(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed bind names the holder so the dialog can offer to fork."""
    from haute._project_storage import StorageClaimedError, UCClaim

    claim = UCClaim(
        app_name="other-app",
        writer_id="w-other",
        nonce="n",
        user="colleague@example.com",
        refreshed_at="2026-08-04T17:00:00+00:00",
    )

    def claimed(*args: object, **kwargs: object):
        raise StorageClaimedError(
            "This storage location is in use by app 'other-app' (bound by "
            "colleague@example.com) — its last heartbeat was 12 seconds ago. "
            "Bind a different location, or fork this one to work on a copy.",
            claim,
        )

    monkeypatch.setattr(_project_storage, "bind_remote", claimed)
    monkeypatch.setenv(_project_storage.STATE_VOLUME_ENV, "workspace.default.haute_state")

    resp = client.post(
        "/api/git/storage/bind",
        json={"remote_url": "uc://workspace.default.projects/demo"},
    )
    assert resp.status_code == 200

    failed = _wait_for_bind(client, "failed")
    assert failed["claim"]["app_name"] == "other-app"
    assert failed["claim"]["user"] == "colleague@example.com"
    assert "fork" in failed["message"]

    # Acknowledging clears it, so the dialog isn't reopened forever.
    acked = client.post("/api/git/storage/bind/ack")
    assert acked.status_code == 200
    assert acked.json()["storage_bind"]["state"] == "idle"


def test_a_second_bind_while_one_runs_is_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    release = threading.Event()

    def slow_bind(url: str, project_root: Path, bound_by: object = None) -> str:
        release.wait(timeout=5)
        return "adopted"

    monkeypatch.setattr(_project_storage, "bind_remote", slow_bind)
    monkeypatch.setenv(_project_storage.STATE_VOLUME_ENV, "workspace.default.haute_state")

    first = client.post(
        "/api/git/storage/bind",
        json={"remote_url": "uc://workspace.default.projects/one"},
    )
    assert first.status_code == 200
    _wait_for_bind(client, "running")

    second = client.post(
        "/api/git/storage/bind",
        json={"remote_url": "uc://workspace.default.projects/two"},
    )
    assert second.status_code == 400
    assert "already being saved" in second.json()["detail"]
    release.set()


def test_a_malformed_url_is_still_rejected_synchronously(client: TestClient) -> None:
    """A typo's answer belongs beside the input field, not in a poll."""
    resp = client.post("/api/git/storage/bind", json={"remote_url": "ssh://x/y"})
    assert resp.status_code == 400
    assert "https://" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 3c. POST /api/git/storage/fork — copy a held location's published state
# ---------------------------------------------------------------------------


def test_fork_returns_the_lineage(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from haute._project_storage import UCLineage

    def forked(source_url: str, target_url: str, project_root: Path, forked_by=None) -> UCLineage:
        return UCLineage(
            parent_url="uc://workspace.default.projects/demo",
            parent_generation=7,
            parent_tip_sha="a" * 40,
        )

    monkeypatch.setattr(_project_storage, "fork_uc_location", forked)
    resp = client.post(
        "/api/git/storage/fork",
        json={
            "source_url": "uc://workspace.default.projects/demo",
            "target_url": "uc://workspace.default.projects/demo-fork",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["outcome"] == "forked"
    assert body["parent_url"] == "uc://workspace.default.projects/demo"
    assert body["parent_generation"] == 7
    assert body["target_url"] == "uc://workspace.default.projects/demo-fork"
    assert "Bind" in body["message"]


def test_fork_config_errors_surface_verbatim(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute._project_storage import StorageConfigError

    def refused(*args: object, **kwargs: object):
        raise StorageConfigError("The fork target already has a stored project.")

    monkeypatch.setattr(_project_storage, "fork_uc_location", refused)
    resp = client.post(
        "/api/git/storage/fork",
        json={
            "source_url": "uc://workspace.default.projects/demo",
            "target_url": "uc://workspace.default.projects/demo",
        },
    )
    assert resp.status_code == 400
    assert "already has a stored project" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 3d. Readiness — fork provenance surfaces without touching the Files API
# ---------------------------------------------------------------------------


def test_readiness_reports_fork_provenance(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haute._project_storage import UCLineage

    monkeypatch.setattr(
        _project_storage,
        "_active_lineage",
        UCLineage(
            parent_url="uc://workspace.default.projects/demo",
            parent_generation=3,
            parent_tip_sha="b" * 40,
        ),
    )
    resp = client.get("/api/git/working-branch")
    assert resp.status_code == 200
    assert resp.json()["storage_forked_from"] == "uc://workspace.default.projects/demo"


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
