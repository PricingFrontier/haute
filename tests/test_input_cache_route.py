"""Shared provider-neutral input-cache HTTP lifecycle."""

from __future__ import annotations

import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient


def _file_config(path: str = "input.csv") -> dict[str, Any]:
    return {
        "inputType": "file",
        "format": "csv",
        "mode": "scan",
        "path": path,
        "arguments": {"schema": {"id": "int64", "value": "str"}},
    }


def _wait_for_terminal(client: TestClient, job_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get(f"/api/input-cache/jobs/{job_id}")
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] != "running":
            return payload
        time.sleep(0.01)
    raise AssertionError(f"input-cache job {job_id!r} did not finish")


@pytest.fixture()
def client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    from haute.routes import input_cache
    from haute.server import app

    monkeypatch.setattr(input_cache, "_project_root", lambda: tmp_path)
    monkeypatch.setattr(input_cache, "_pipeline_base_dir", lambda: tmp_path)
    input_cache._reset_for_tests()
    with TestClient(app) as test_client:
        yield test_client
    input_cache._reset_for_tests()


def test_build_job_publishes_snapshot_and_snapshot_status(
    client: TestClient,
    tmp_path: Path,
) -> None:
    (tmp_path / "input.csv").write_text("id,value\n1,a\n2,b\n", encoding="utf-8")

    started = client.post(
        "/api/input-cache/build",
        json={"schema_version": 1, "config": _file_config()},
    )

    assert started.status_code == 202
    start_payload = started.json()
    assert start_payload == {
        "schema_version": 1,
        "job_id": start_payload["job_id"],
        "identity_digest": start_payload["identity_digest"],
        "status": "running",
        "joined": False,
        "build_class": "bounded",
    }
    terminal = _wait_for_terminal(client, start_payload["job_id"])
    assert terminal["status"] == "completed"
    assert terminal["terminal_reason"] == "completed"
    assert terminal["snapshot"]["state"] == "ready"
    assert terminal["snapshot"]["freshness"] == "fresh"
    assert terminal["snapshot"]["generation"]["row_count"] == 2
    assert terminal["snapshot"]["generation"]["column_count"] == 2
    assert terminal["snapshot"]["generation"]["size_bytes"] > 0
    assert terminal["progress"]["phase"] == "completed"

    status = client.post(
        "/api/input-cache/status",
        json={"schema_version": 1, "config": _file_config()},
    )
    assert status.status_code == 200
    assert status.json()["identity_digest"] == start_payload["identity_digest"]
    assert status.json()["state"] == "ready"
    assert status.json()["freshness"] == "fresh"


@pytest.mark.parametrize(
    "path",
    ["../outside.csv", "../../../etc/passwd"],
)
def test_cache_status_rejects_file_paths_outside_project_root(
    client: TestClient,
    path: str,
) -> None:
    response = client.post(
        "/api/input-cache/status",
        json={"schema_version": 1, "config": _file_config(path)},
    )

    assert response.status_code == 403
    assert "outside the project root" in response.json()["detail"]


def test_cache_status_rejects_absolute_file_path_outside_project_root(
    client: TestClient,
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / "outside.csv"

    response = client.post(
        "/api/input-cache/status",
        json={"schema_version": 1, "config": _file_config(str(outside))},
    )

    assert response.status_code == 403
    assert "outside the project root" in response.json()["detail"]


def test_cache_status_rejects_lakehouse_path_outside_project_root(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/input-cache/status",
        json={
            "schema_version": 1,
            "config": {
                "inputType": "lakehouse",
                "format": "delta",
                "mode": "scan",
                "path": "../outside-delta-table",
                "arguments": {},
            },
        },
    )

    assert response.status_code == 403
    assert "outside the project root" in response.json()["detail"]


@pytest.mark.parametrize(
    "uri",
    [
        "sqlite:///../outside.sqlite",
        "sqlite:////outside.sqlite",
    ],
)
def test_cache_status_rejects_raw_sqlite_paths_outside_project_root(
    client: TestClient,
    uri: str,
) -> None:
    response = client.post(
        "/api/input-cache/status",
        json={
            "schema_version": 1,
            "config": {
                "inputType": "database",
                "format": "database",
                "uri": uri,
                "query": "SELECT 1",
            },
        },
    )

    assert response.status_code == 403
    assert "outside the project root" in response.json()["detail"]


def test_same_identity_build_requests_join_one_active_job(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute.routes import input_cache

    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def blocking_build(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(timeout=5)
        raise RuntimeError("stop after join assertion")

    monkeypatch.setattr(input_cache, "build_input_snapshot", blocking_build)
    first = client.post(
        "/api/input-cache/build",
        json={"schema_version": 1, "config": _file_config()},
    )
    assert first.status_code == 202
    assert entered.wait(timeout=5)

    second = client.post(
        "/api/input-cache/build",
        json={"schema_version": 1, "config": _file_config()},
    )
    assert second.status_code == 202
    assert second.json()["job_id"] == first.json()["job_id"]
    assert second.json()["joined"] is True
    assert calls == 1
    release.set()
    assert _wait_for_terminal(client, first.json()["job_id"])["status"] == "error"


def test_provider_failure_message_is_logged_but_not_exposed(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute.routes import input_cache

    logger = input_cache.logger
    logged: list[dict[str, object]] = []

    def capture_error(_event: str, **fields: object) -> None:
        logged.append(fields)

    monkeypatch.setattr(logger, "error", capture_error)
    monkeypatch.setenv("DATABRICKS_TOKEN", "resolved-secret-token")
    monkeypatch.setattr(
        input_cache,
        "build_input_snapshot",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError(
                "warehouse unavailable token='inline secret' "
                "https://alice:password@workspace.example/query?sig=signed-value "
                "resolved-secret-token"
            )
        ),
    )

    started = client.post(
        "/api/input-cache/build",
        json={"schema_version": 1, "config": _file_config()},
    )
    terminal = _wait_for_terminal(client, started.json()["job_id"])

    assert terminal["message"] == "Input snapshot build failed."
    assert len(logged) == 1
    assert logged[0]["job_id"] == started.json()["job_id"]
    assert logged[0]["error_type"] == "RuntimeError"
    diagnostic = str(logged[0]["error"])
    assert "warehouse unavailable" in diagnostic
    assert "<redacted>" in diagnostic
    assert "inline secret" not in diagnostic
    assert "password" not in diagnostic
    assert "signed-value" not in diagnostic
    assert "resolved-secret-token" not in diagnostic


def test_different_identities_can_build_concurrently(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute.routes import input_cache

    both_entered = threading.Event()
    release = threading.Event()
    entered = 0
    lock = threading.Lock()

    def blocking_build(*args: Any, **kwargs: Any) -> Any:
        nonlocal entered
        with lock:
            entered += 1
            if entered == 2:
                both_entered.set()
        assert release.wait(timeout=5)
        raise RuntimeError("stop after concurrency assertion")

    monkeypatch.setattr(input_cache, "build_input_snapshot", blocking_build)
    first = client.post(
        "/api/input-cache/build",
        json={"schema_version": 1, "config": _file_config("one.csv")},
    )
    second = client.post(
        "/api/input-cache/build",
        json={"schema_version": 1, "config": _file_config("two.csv")},
    )
    assert first.status_code == second.status_code == 202
    assert first.json()["job_id"] != second.json()["job_id"]
    assert both_entered.wait(timeout=5)
    release.set()
    assert _wait_for_terminal(client, first.json()["job_id"])["status"] == "error"
    assert _wait_for_terminal(client, second.json()["job_id"])["status"] == "error"


def test_cancel_requests_cooperative_stop(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._source_cache import SourceCacheBuildError
    from haute.routes import input_cache

    entered = threading.Event()

    def cancellable_build(*args: Any, cancellation: threading.Event, **kwargs: Any) -> Any:
        entered.set()
        assert cancellation.wait(timeout=5)
        raise SourceCacheBuildError("source-cache build was cancelled")

    monkeypatch.setattr(input_cache, "build_input_snapshot", cancellable_build)
    started = client.post(
        "/api/input-cache/build",
        json={"schema_version": 1, "config": _file_config()},
    )
    assert started.status_code == 202
    assert entered.wait(timeout=5)

    cancelled = client.delete(
        f"/api/input-cache/jobs/{started.json()['job_id']}",
    )
    assert cancelled.status_code == 202
    assert cancelled.json()["cancellation_requested"] is True
    terminal = _wait_for_terminal(client, started.json()["job_id"])
    assert terminal["status"] == "cancelled"
    assert terminal["terminal_reason"] == "cancelled"


def test_build_deadline_owns_timeout_status_and_error_code(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._source_cache import SourceCacheBuildError
    from haute.routes import input_cache

    entered = threading.Event()

    def timed_build(*args: Any, cancellation: threading.Event, **kwargs: Any) -> Any:
        entered.set()
        assert cancellation.wait(timeout=5)
        raise SourceCacheBuildError("source-cache build exceeded its deadline")

    monkeypatch.setenv("HAUTE_BUILD_TIMEOUT", "0.01")
    monkeypatch.setattr(input_cache, "build_input_snapshot", timed_build)
    started = client.post(
        "/api/input-cache/build",
        json={"schema_version": 1, "config": _file_config()},
    )
    assert started.status_code == 202
    assert entered.wait(timeout=5)

    terminal = _wait_for_terminal(client, started.json()["job_id"])
    assert terminal["status"] == "timed_out"
    assert terminal["terminal_reason"] == "timed_out"
    assert terminal["error_code"] == "build_timed_out"


def test_write_failure_has_a_stable_safe_error_code(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute.routes import input_cache

    def write_failed(*args: Any, **kwargs: Any) -> Any:
        raise OSError("private cache path must not escape")

    monkeypatch.setattr(input_cache, "build_input_snapshot", write_failed)
    started = client.post(
        "/api/input-cache/build",
        json={"schema_version": 1, "config": _file_config()},
    )
    assert started.status_code == 202

    terminal = _wait_for_terminal(client, started.json()["job_id"])
    assert terminal["status"] == "error"
    assert terminal["error_code"] == "build_failed"
    assert "private cache path" not in terminal["message"]


class _AdmittedEagerHarness:
    """Force the admitted-eager class and stand in for the spawn worker.

    An admitted-eager explicit build must never materialise on the server
    thread: it runs through ``build_input_snapshot_worker`` in a hard-capped
    spawn worker. The fake spawn records what the parent hands the child —
    budget, generation id, staging token, and worker controls.
    """

    def __init__(self) -> None:
        self.events: list[Any] = []
        self.calls: list[dict[str, Any]] = []
        self.budget: Any = None

    def context(self) -> Any:
        harness = self

        class FakeExecutionContext:
            def release_admission(self) -> None:
                harness.events.append("released")

        return FakeExecutionContext


def _admitted_eager_harness(
    monkeypatch: pytest.MonkeyPatch,
    spawn: Any,
) -> _AdmittedEagerHarness:
    from haute._execution_admission import IsolatedExecutionBudget
    from haute._execution_context import ExecutionProfile
    from haute.routes import input_cache

    harness = _AdmittedEagerHarness()
    context_type = harness.context()
    harness.budget = IsolatedExecutionBudget(
        operation="input_snapshot_build",
        profile=ExecutionProfile.PREVIEW_EAGER,
        memory_limit_bytes=64 * 1024 * 1024,
        config_key="test-config-key",
        budget_policy="test",
    )

    def admit(**kwargs: Any) -> Any:
        harness.events.append(
            (
                "admitted",
                kwargs["operation"],
                kwargs["profile"],
                kwargs["job_id"],
                kwargs["cancellation_token"],
            )
        )
        return context_type()

    def record_spawn(function: Any, request: Any, budget: Any, **kwargs: Any) -> Any:
        harness.calls.append(
            {
                "function": function,
                "request": request,
                "budget": budget,
                "config": kwargs["config"],
            }
        )
        harness.events.append("spawned")
        return spawn(request, budget)

    def never_build(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("an admitted-eager build must not run on the server thread")

    monkeypatch.setattr(
        input_cache,
        "input_snapshot_build_class",
        lambda *args, **kwargs: "admitted_eager",
    )
    monkeypatch.setattr(input_cache, "create_admitted_execution_context", admit)
    monkeypatch.setattr(input_cache, "isolated_execution_budget", lambda _ctx: harness.budget)
    monkeypatch.setattr(input_cache, "run_isolated_worker", record_spawn)
    monkeypatch.setattr(input_cache, "build_input_snapshot", never_build)
    return harness


def _start_admitted_eager_build(client: TestClient, *, refresh: bool = False) -> str:
    started = client.post(
        "/api/input-cache/build",
        json={
            "schema_version": 1,
            "config": _file_config(),
            "refresh": refresh,
        },
    )
    assert started.status_code == 202
    return str(started.json()["job_id"])


def test_admitted_eager_build_runs_through_the_hard_capped_worker(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._execution_context import ExecutionProfile
    from haute._input_preparation import InputPreparationOutcome, build_input_snapshot_worker
    from haute._input_providers import build_input_snapshot
    from haute.routes import input_cache

    (tmp_path / "input.csv").write_text("id,value\n1,a\n2,b\n", encoding="utf-8")

    def child(request: Any, budget: Any) -> InputPreparationOutcome:
        # Stand-in for the child process: the same explicit build under the
        # parent-chosen generation id and staging token.
        generation = build_input_snapshot(
            request.config,
            store=input_cache._cache_store(),
            base_dir=request.base_dir,
            profile=request.profile,
            refresh=request.refresh,
            generation_id=request.generation_id,
            staging_token=request.staging_token,
            allow_admitted_eager=True,
        )
        return InputPreparationOutcome(
            generation_id=generation.generation_id,
            row_count=generation.metadata.row_count,
            size_bytes=generation.metadata.size_bytes,
        )

    harness = _admitted_eager_harness(monkeypatch, child)
    job_id = _start_admitted_eager_build(client)
    terminal = _wait_for_terminal(client, job_id)

    assert terminal["status"] == "completed"
    assert terminal["snapshot"]["state"] == "ready"
    assert terminal["snapshot"]["generation"]["row_count"] == 2
    assert terminal["progress"]["phase"] == "completed"
    assert terminal["progress"]["rows"] == 2

    assert len(harness.calls) == 1
    call = harness.calls[0]
    assert call["function"] is build_input_snapshot_worker
    assert call["budget"] is harness.budget
    assert terminal["snapshot"]["generation"]["generation_id"] == call["request"].generation_id
    assert len(call["request"].staging_token) == 8
    worker_config = call["config"]
    assert worker_config.require_memory_limit is True
    assert worker_config.memory_limit_bytes == harness.budget.memory_limit_bytes
    assert worker_config.timeout_seconds == input_cache._build_timeout()
    assert worker_config.stop_reason is not None
    assert worker_config.stop_reason() is None

    admitted = harness.events[0]
    assert admitted[:4] == (
        "admitted",
        "input_snapshot_build",
        ExecutionProfile.PREVIEW_EAGER,
        job_id,
    )
    assert admitted[4] is not None
    assert harness.events == [admitted, "spawned", "released"]


def test_admitted_eager_memory_limit_reconciles_and_keeps_the_previous_generation(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._input_providers import source_cache_identity
    from haute._worker_isolation import IsolatedWorkerMemoryLimitExceededError
    from haute.routes import input_cache

    (tmp_path / "input.csv").write_text("id,value\n1,a\n2,b\n", encoding="utf-8")
    first = _wait_for_terminal(
        client,
        client.post(
            "/api/input-cache/build",
            json={"schema_version": 1, "config": _file_config()},
        ).json()["job_id"],
    )
    assert first["status"] == "completed"
    previous_generation_id = first["snapshot"]["generation"]["generation_id"]

    identity = source_cache_identity(_file_config(), base_dir=tmp_path)
    identity_dir = input_cache._cache_store().identity_path(identity)
    staged: dict[str, Path] = {}

    def child(request: Any, budget: Any) -> Any:
        # A child that died mid-build leaves its private staging directory
        # behind under the parent-chosen token.
        staging = identity_dir / f".staging-{request.staging_token}"
        staging.mkdir(parents=True, exist_ok=True)
        (staging / "part.parquet").write_bytes(b"partial")
        staged["path"] = staging
        staged["generation_id"] = request.generation_id  # type: ignore[assignment]
        raise IsolatedWorkerMemoryLimitExceededError(
            rss_bytes=4096,
            rss_limit_bytes=1024,
        )

    harness = _admitted_eager_harness(monkeypatch, child)
    job_id = _start_admitted_eager_build(client, refresh=True)
    terminal = _wait_for_terminal(client, job_id)

    assert terminal["status"] == "memory_limited"
    assert terminal["terminal_reason"] == "memory_limited"
    assert terminal["error_code"] == "memory_limit"
    assert terminal["progress"]["phase"] == "failed"

    stored = input_cache._store.require_job(job_id)
    assert stored["error_detail"]["error_code"] == "memory_limit"
    assert stored["error_detail"]["operation"] == harness.budget.operation
    assert stored["error_detail"]["reason"] == "worker_rss_limit_exceeded"
    assert stored["error_detail"]["memory_limit_bytes"] == harness.budget.memory_limit_bytes

    # Both parent-chosen values are reconciled: the staging directory is gone,
    # the unpublished generation is gone, and the previous one is still current.
    assert not staged["path"].exists()
    assert not (identity_dir / "generations" / str(staged["generation_id"])).exists()
    status_response = client.post(
        "/api/input-cache/status",
        json={"schema_version": 1, "config": _file_config()},
    )
    assert status_response.json()["state"] == "ready"
    assert status_response.json()["generation"]["generation_id"] == previous_generation_id


@pytest.mark.parametrize(
    ("failure_factory", "expected_status", "expected_error_code"),
    [
        ("cancelled", "cancelled", "build_cancelled"),
        ("timed_out", "timed_out", "build_timed_out"),
        ("failed", "error", "build_failed"),
    ],
)
def test_admitted_eager_worker_failures_map_onto_the_job_lifecycle(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_factory: str,
    expected_status: str,
    expected_error_code: str,
) -> None:
    from haute._worker_isolation import (
        IsolatedWorkerStoppedError,
        IsolatedWorkerTimeoutError,
    )

    (tmp_path / "input.csv").write_text("id,value\n1,a\n2,b\n", encoding="utf-8")

    def child(_request: Any, _budget: Any) -> Any:
        if failure_factory == "cancelled":
            raise IsolatedWorkerStoppedError(terminal_reason="cancelled")
        if failure_factory == "timed_out":
            raise IsolatedWorkerTimeoutError(timeout_seconds=1.0)
        raise RuntimeError("forced worker build failure")

    _admitted_eager_harness(monkeypatch, child)
    terminal = _wait_for_terminal(client, _start_admitted_eager_build(client))

    assert terminal["status"] == expected_status
    assert terminal["error_code"] == expected_error_code
    assert terminal["snapshot"] is None
    assert "forced worker build failure" not in terminal["message"]


def test_bounded_build_still_runs_on_the_server_thread(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute.routes import input_cache

    (tmp_path / "input.csv").write_text("id,value\n1,a\n2,b\n", encoding="utf-8")

    def never_spawn(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("a bounded build must not spawn a worker")

    monkeypatch.setattr(input_cache, "run_isolated_worker", never_spawn)

    terminal = _wait_for_terminal(
        client,
        client.post(
            "/api/input-cache/build",
            json={"schema_version": 1, "config": _file_config()},
        ).json()["job_id"],
    )

    assert terminal["status"] == "completed"
    assert terminal["build_class"] == "bounded"
    assert terminal["snapshot"]["generation"]["row_count"] == 2


def test_admitted_eager_memory_refusal_has_a_stable_safe_status(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._execution_admission import ExecutionAdmissionError
    from haute.routes import input_cache

    def reject(**kwargs: Any) -> Any:
        raise ExecutionAdmissionError(
            kwargs["operation"],
            profile=kwargs["profile"],
            memory_limit_bytes=1024,
            rss_at_admission_bytes=2048,
            process_rss_limit_bytes=1024,
            reason="private admission detail",
        )

    monkeypatch.setattr(
        input_cache,
        "input_snapshot_build_class",
        lambda *args, **kwargs: "admitted_eager",
    )
    monkeypatch.setattr(input_cache, "create_admitted_execution_context", reject)

    started = client.post(
        "/api/input-cache/build",
        json={
            "schema_version": 1,
            "config": _file_config(),
        },
    )
    assert started.status_code == 202

    terminal = _wait_for_terminal(client, started.json()["job_id"])
    assert terminal["status"] == "memory_limited"
    assert terminal["terminal_reason"] == "memory_limited"
    assert terminal["error_code"] == "memory_limit"
    # The shared memory-message shape (see routes/_memory_messages.py): the
    # generic admission refusal, never the internal reason string; sizes are
    # rendered only for the reasons whose attributes are known comparable.
    assert terminal["message"] == (
        "The input snapshot build was not started because the server does not "
        "have enough free memory for it. Wait for other work to finish, reduce "
        "the data size, or run on a server with more memory, then try again."
    )
    assert "private admission detail" not in terminal["message"]


def test_clear_is_identity_addressed_and_never_accepts_a_cache_path(
    client: TestClient,
    tmp_path: Path,
) -> None:
    (tmp_path / "input.csv").write_text("id\n1\n", encoding="utf-8")
    started = client.post(
        "/api/input-cache/build",
        json={"schema_version": 1, "config": _file_config()},
    )
    _wait_for_terminal(client, started.json()["job_id"])

    cleared = client.post(
        "/api/input-cache/clear",
        json={"schema_version": 1, "config": _file_config()},
    )
    assert cleared.status_code == 200
    assert cleared.json()["state"] == "missing"
    assert "path" not in cleared.json()


def test_clear_and_build_admission_are_linearized(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute.routes import input_cache
    from haute.schemas import InputCacheBuildRequest, InputCacheSourceRequest

    store = input_cache._cache_store()
    original_clear = store.clear
    original_create_job = input_cache._store.create_job
    clear_entered = threading.Event()
    release_clear = threading.Event()
    job_admitted = threading.Event()

    def blocking_clear(identity: Any) -> None:
        clear_entered.set()
        assert release_clear.wait(timeout=5)
        original_clear(identity)

    def create_job(payload: dict[str, Any]) -> str:
        job_admitted.set()
        return original_create_job(payload)

    def fail_build(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("stop after admission ordering assertion")

    monkeypatch.setattr(store, "clear", blocking_clear)
    monkeypatch.setattr(
        input_cache,
        "input_snapshot_build_class",
        lambda *args, **kwargs: "bounded",
    )
    monkeypatch.setattr(input_cache._store, "create_job", create_job)
    monkeypatch.setattr(input_cache, "build_input_snapshot", fail_build)

    with ThreadPoolExecutor(max_workers=2) as pool:
        clearing = pool.submit(
            input_cache.clear_input_cache,
            InputCacheSourceRequest(config=_file_config()),
        )
        assert clear_entered.wait(timeout=5)
        building = pool.submit(
            input_cache.build_input_cache,
            InputCacheBuildRequest(config=_file_config()),
        )
        try:
            assert not job_admitted.wait(timeout=0.1)
        finally:
            release_clear.set()
        cleared = clearing.result(timeout=5)
        started = building.result(timeout=5)

    assert cleared.state == "missing"
    assert started.status == "running"
    assert job_admitted.is_set()
    assert _wait_for_terminal(client, started.job_id)["status"] == "error"


def test_secret_bearing_config_is_rejected_without_echo(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/input-cache/build",
        json={
            "schema_version": 1,
            "config": {
                "inputType": "database",
                "format": "database",
                "uri": "postgresql://alice:do-not-echo@db.example/pricing",
                "query": "SELECT 1",
            },
        },
    )

    assert response.status_code == 400
    body = response.text
    assert "do-not-echo" not in body
    assert response.json()["detail"].startswith("invalid_input_config:")


# Formats without a file extension, each in its canonical Data Input shape.
_EXTENSIONLESS_FORMAT_CONFIGS: dict[str, dict[str, Any]] = {
    "database": {
        "inputType": "database",
        "format": "database",
        "uri": "sqlite:///data.sqlite",
        "query": "SELECT 1",
    },
    "delta": {"inputType": "lakehouse", "format": "delta", "mode": "scan", "path": "table"},
    "iceberg": {"inputType": "lakehouse", "format": "iceberg", "mode": "scan", "path": "table"},
    "records": {"inputType": "inline", "format": "records", "records": [{"x": 1}]},
}


def _format_configs() -> list[Any]:
    """One snapshot-backed Data Input config per format in the registry."""
    from haute._polars_io_registry import FORMATS

    configs = []
    for fmt in FORMATS:
        if not fmt.extensions:
            configs.append(pytest.param(_EXTENSIONLESS_FORMAT_CONFIGS[fmt.name], id=fmt.name))
            continue
        # A Parquet scan is read directly; its eager read is snapshot-backed.
        mode = "read" if fmt.name == "parquet" else None
        config = {"inputType": "file", "format": fmt.name, "path": f"input{fmt.extensions[0]}"}
        if mode is not None:
            config["mode"] = mode
        configs.append(pytest.param(config, id=fmt.name))
    return configs


@pytest.mark.parametrize("config", _format_configs())
def test_the_server_chooses_each_formats_build_profile(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config: dict[str, Any],
) -> None:
    """The browser sends no profile: bounded formats stream, eager-only ones are admitted."""
    from haute._execution_context import ExecutionProfile
    from haute._polars_io_registry import format_for_config, snapshot_input_plan
    from haute.routes import input_cache
    from haute.schemas import InputCacheBuildResponse

    started: list[dict[str, Any]] = []

    def record_start(**kwargs: Any) -> InputCacheBuildResponse:
        started.append(kwargs)
        return InputCacheBuildResponse(
            job_id="job-1",
            identity_digest=kwargs["key"],
            status="running",
            joined=False,
            build_class=kwargs["build_class"],
        )

    monkeypatch.setattr(input_cache, "_start_build", record_start)
    if config["inputType"] == "database":
        import sqlite3

        # A SQLite snapshot source must be an existing on-disk database.
        connection = sqlite3.connect(tmp_path / "data.sqlite")
        connection.execute("CREATE TABLE t (x INTEGER)")
        connection.close()
    expected = snapshot_input_plan(format_for_config(config), config)[1]

    response = client.post("/api/input-cache/build", json={"schema_version": 1, "config": config})

    assert response.status_code == 202, response.json()
    assert response.json()["build_class"] == expected
    [start] = started
    assert start["build_class"] == expected
    assert start["target_kwargs"]["profile"] == (
        ExecutionProfile.LAZY_SINK if expected == "bounded" else ExecutionProfile.PREVIEW_EAGER
    )


def test_a_build_request_naming_a_profile_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/input-cache/build",
        json={"schema_version": 1, "config": _file_config(), "profile": "lazy_sink"},
    )

    assert response.status_code == 422


def test_unsupported_database_scheme_is_rejected_before_job_creation(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/input-cache/build",
        json={
            "schema_version": 1,
            "config": {
                "inputType": "database",
                "format": "database",
                "uri": "postgresql://db.example/pricing",
                "query": "SELECT 1",
            },
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"].startswith("snapshot_build_unsupported:")


def test_a_base_exception_after_the_child_published_is_never_swallowed(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An interrupt during the worker wait propagates, published child or not."""
    from haute._execution_admission import IsolatedExecutionBudget
    from haute._execution_context import ExecutionProfile
    from haute._input_providers import build_input_snapshot, source_cache_identity
    from haute.routes import input_cache

    (tmp_path / "input.csv").write_text("id,value\n1,a\n2,b\n", encoding="utf-8")
    config = _file_config()
    identity = source_cache_identity(config, base_dir=tmp_path)
    store = input_cache._cache_store()
    budget = IsolatedExecutionBudget(
        operation="input_snapshot_build",
        profile=ExecutionProfile.PREVIEW_EAGER,
        memory_limit_bytes=64 * 1024 * 1024,
        config_key="test-config-key",
        budget_policy="test",
    )
    monkeypatch.setattr(input_cache, "isolated_execution_budget", lambda _ctx: budget)
    published: list[str] = []

    def publish_then_interrupt(function: Any, request: Any, _budget: Any, **kwargs: Any) -> Any:
        generation = build_input_snapshot(
            request.config,
            store=store,
            base_dir=tmp_path,
            profile=ExecutionProfile.PREVIEW_EAGER,
            generation_id=request.generation_id,
            staging_token=request.staging_token,
            allow_admitted_eager=True,
            defer_retirement=True,
        )
        published.append(generation.generation_id)
        raise KeyboardInterrupt

    monkeypatch.setattr(input_cache, "run_isolated_worker", publish_then_interrupt)

    class _Token:
        cancelled = False
        terminal_reason = None

    with pytest.raises(KeyboardInterrupt):
        input_cache._supervise_admitted_eager_build(
            config=config,
            identity=identity,
            refresh=False,
            profile=ExecutionProfile.PREVIEW_EAGER,
            execution_context=object(),  # type: ignore[arg-type]
            token=_Token(),
        )

    assert store.open_generation(identity).generation_id == published[0]


def test_build_identity_names_the_file_execution_opens(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A root-relative locator must name one file at build and at execution.

    The pipeline lives in ``rating/`` while the data sits at the project root.
    Anchoring the locator to the pipeline directory alone builds a snapshot
    under an identity execution never looks up, from a stale copy execution
    never reads — and the freshness signature describes that other file too.
    """
    from haute._input_preparation import preparation_base_dir
    from haute._input_providers import source_cache_identity, source_signature
    from haute._sandbox import set_project_root
    from haute._types import GraphNode, NodeData, PipelineGraph
    from haute.execution import canonical_dataframe_execution_graph
    from haute.routes import input_cache
    from haute.schemas import InputCacheSourceRequest

    (tmp_path / "haute.toml").write_text(
        '[project]\nname = "t"\npipeline = "rating/main.py"\n',
        encoding="utf-8",
    )
    pipeline_dir = tmp_path / "rating"
    pipeline_dir.mkdir()
    main_py = pipeline_dir / "main.py"
    main_py.write_text("", encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "x.csv").write_text("id,value\n1,a\n", encoding="utf-8")
    (pipeline_dir / "data").mkdir()
    (pipeline_dir / "data" / "x.csv").write_text("id,value\n9,z\n10,y\n", encoding="utf-8")
    set_project_root(tmp_path)

    monkeypatch.setattr(input_cache, "_project_root", lambda: tmp_path)
    monkeypatch.setattr(input_cache, "_pipeline_base_dir", lambda: pipeline_dir)

    raw_config = _file_config("data/x.csv")
    node = GraphNode(
        id="src",
        type="custom",
        position={"x": 0, "y": 0},
        data=NodeData(label="x", nodeType="dataInput", config=dict(raw_config)),
    )
    graph = PipelineGraph(nodes=[node], edges=[], source_file=str(main_py))
    [executed_node] = canonical_dataframe_execution_graph(graph).nodes
    executed_config = dict(executed_node.data.config)
    execution_base_dir = preparation_base_dir(graph)

    built_config, built_identity = input_cache._safe_config(
        InputCacheSourceRequest(config=raw_config)
    )

    assert built_identity == source_cache_identity(
        executed_config,
        base_dir=execution_base_dir,
    )
    assert source_signature(
        built_config,
        base_dir=input_cache._pipeline_base_dir(),
    ) == source_signature(executed_config, base_dir=execution_base_dir)


# --------------------------------------------------------------------------
# Structured API Inputs: one snapshot per emitting table, acted on together
# --------------------------------------------------------------------------


def _api_input_config(path: str = "quotes.jsonl") -> dict[str, Any]:
    return {
        "path": path,
        "tables": [
            {
                "label": "quotes",
                "path": "$[:]",
                "emit": True,
                "columns": [{"name": "id", "path": "$[:].id", "type": "int", "selected": True}],
            },
            {
                "label": "drivers",
                "path": "$[:].drivers[:]",
                "emit": True,
                "columns": [
                    {"name": "age", "path": "$[:].drivers[:].age", "type": "int", "selected": True}
                ],
            },
        ],
    }


def _api_input_body(config: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "node_type": "apiInput",
        "config": config or _api_input_config(),
        **extra,
    }


def _write_quotes(tmp_path: Path, count: int = 2) -> Path:
    path = tmp_path / "quotes.jsonl"
    path.write_text(
        "\n".join(
            f'{{"id": {index}, "drivers": [{{"age": {30 + index}}}, {{"age": {40 + index}}}]}}'
            for index in range(count)
        ),
        encoding="utf-8",
    )
    return path


class _ApiInputWorker:
    """Runs the capped API-input build worker in-process and records the call."""

    def __init__(self, *, gate: threading.Event | None = None, failure: Exception | None = None):
        self.gate = gate
        self.failure = failure
        self.calls: list[dict[str, Any]] = []
        self.released = 0

    def install(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        import haute._execution_admission as admission
        from haute._execution_admission import IsolatedExecutionBudget
        from haute._execution_context import ExecutionProfile
        from haute._sandbox import set_project_root
        from haute.routes import input_cache

        set_project_root(tmp_path)
        worker = self
        budget = IsolatedExecutionBudget(
            operation="input_snapshot_build",
            profile=ExecutionProfile.LAZY_SINK,
            memory_limit_bytes=64 * 1024 * 1024,
            config_key="test-config-key",
            budget_policy="test",
        )

        class Context:
            def release_admission(self, **_kwargs: Any) -> None:
                worker.released += 1

            def checkpoint(self, *, label: str) -> None:
                del label

            def stage(self, name: str) -> Any:
                import contextlib

                del name
                return contextlib.nullcontext()

        def spawn(function: Any, request: Any, budget: Any, *, config: Any) -> Any:
            worker.calls.append({"request": request, "budget": budget, "config": config})
            if worker.gate is not None:
                assert worker.gate.wait(5)
            if worker.failure is not None:
                raise worker.failure
            return function(request, budget)

        monkeypatch.setattr(input_cache, "create_admitted_execution_context", lambda **_: Context())
        monkeypatch.setattr(input_cache, "isolated_execution_budget", lambda _ctx: budget)
        monkeypatch.setattr(input_cache, "run_isolated_worker", spawn)
        monkeypatch.setattr(admission, "create_isolated_execution_context", lambda _b: Context())


def test_api_input_status_lists_each_table_before_any_build(
    client: TestClient,
    tmp_path: Path,
) -> None:
    _write_quotes(tmp_path)

    response = client.post("/api/input-cache/status", json=_api_input_body())

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "missing"
    assert payload["freshness"] == "unknown"
    assert payload["generation"] is None
    assert [table["label"] for table in payload["tables"]] == ["quotes", "drivers"]
    assert {table["state"] for table in payload["tables"]} == {"missing"}
    assert len({table["identity_digest"] for table in payload["tables"]}) == 2
    assert payload["identity_digest"] not in {
        table["identity_digest"] for table in payload["tables"]
    }


def test_api_input_build_publishes_every_table_in_a_capped_worker(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_quotes(tmp_path)
    worker = _ApiInputWorker()
    worker.install(monkeypatch, tmp_path)

    started = client.post("/api/input-cache/build", json=_api_input_body())
    assert started.status_code == 202
    terminal = _wait_for_terminal(client, started.json()["job_id"])

    assert terminal["status"] == "completed", terminal
    assert terminal["build_class"] == "bounded"
    assert terminal["identity_digest"] == started.json()["identity_digest"]
    snapshot = terminal["snapshot"]
    assert snapshot["state"] == "ready"
    assert snapshot["freshness"] == "fresh"
    assert [table["generation"]["row_count"] for table in snapshot["tables"]] == [2, 4]
    assert terminal["progress"]["rows"] == 6
    assert terminal["progress"]["batches"] == 2
    [call] = worker.calls
    assert call["request"].labels == ("quotes", "drivers")
    assert call["config"].require_memory_limit is True
    assert worker.released == 2  # the worker's own context, then the job's admission

    status = client.post("/api/input-cache/status", json=_api_input_body()).json()
    assert status["state"] == "ready"
    assert status["identity_digest"] == started.json()["identity_digest"]

    # Everything is fresh, so a second build writes nothing.
    again = client.post("/api/input-cache/build", json=_api_input_body())
    assert _wait_for_terminal(client, again.json()["job_id"])["status"] == "completed"
    assert len(worker.calls) == 1

    # A refresh rebuilds every table.
    refresh = client.post("/api/input-cache/build", json=_api_input_body(refresh=True))
    assert _wait_for_terminal(client, refresh.json()["job_id"])["status"] == "completed"
    assert worker.calls[-1]["request"].labels == ("quotes", "drivers")


def test_api_input_status_turns_stale_when_the_source_changes(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_quotes(tmp_path)
    worker = _ApiInputWorker()
    worker.install(monkeypatch, tmp_path)
    started = client.post("/api/input-cache/build", json=_api_input_body())
    _wait_for_terminal(client, started.json()["job_id"])

    _write_quotes(tmp_path, count=3)
    status = client.post("/api/input-cache/status", json=_api_input_body()).json()

    assert status["state"] == "ready"
    assert status["freshness"] == "stale"
    assert {table["freshness"] for table in status["tables"]} == {"stale"}

    # Only the stale tables are rebuilt, without a refresh.
    rebuilt = client.post("/api/input-cache/build", json=_api_input_body())
    _wait_for_terminal(client, rebuilt.json()["job_id"])
    assert worker.calls[-1]["request"].labels == ("quotes", "drivers")
    after = client.post("/api/input-cache/status", json=_api_input_body()).json()
    assert after["freshness"] == "fresh"


def test_api_input_status_keeps_published_tables_when_the_source_is_removed(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write_quotes(tmp_path)
    worker = _ApiInputWorker()
    worker.install(monkeypatch, tmp_path)
    started = client.post("/api/input-cache/build", json=_api_input_body())
    _wait_for_terminal(client, started.json()["job_id"])

    source.unlink()
    status = client.post("/api/input-cache/status", json=_api_input_body()).json()

    # A missing source proves nothing about the published tables: they stay
    # ready with unknown freshness, so the browser serves them unbuilt.
    assert (status["state"], status["freshness"]) == ("ready", "unknown")
    assert {(table["state"], table["freshness"]) for table in status["tables"]} == {
        ("ready", "unknown")
    }


def test_api_input_clear_removes_every_table_but_not_while_building(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute.routes import input_cache

    _write_quotes(tmp_path)
    gate = threading.Event()
    worker = _ApiInputWorker(gate=gate)
    worker.install(monkeypatch, tmp_path)

    started = client.post("/api/input-cache/build", json=_api_input_body())
    job_id = started.json()["job_id"]
    deadline = time.monotonic() + 5
    while not worker.calls and time.monotonic() < deadline:
        time.sleep(0.01)
    status = client.post("/api/input-cache/status", json=_api_input_body()).json()
    assert status["state"] == "building"
    assert {table["state"] for table in status["tables"]} == {"building"}
    assert all(
        input_cache.api_input_table_build_running(table["identity_digest"])
        for table in status["tables"]
    )
    joined = client.post("/api/input-cache/build", json=_api_input_body())
    assert joined.json()["joined"] is True
    assert joined.json()["build_class"] == "bounded"
    assert joined.json()["job_id"] == job_id

    refused = client.post("/api/input-cache/clear", json=_api_input_body())
    assert refused.status_code == 409

    gate.set()
    assert _wait_for_terminal(client, job_id)["status"] == "completed"
    assert not any(
        input_cache.api_input_table_build_running(table["identity_digest"])
        for table in status["tables"]
    )

    cleared = client.post("/api/input-cache/clear", json=_api_input_body())
    assert cleared.status_code == 200
    assert cleared.json()["state"] == "missing"
    assert {table["state"] for table in cleared.json()["tables"]} == {"missing"}


def test_api_input_worker_failure_is_a_failed_job(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_quotes(tmp_path)
    worker = _ApiInputWorker(failure=RuntimeError("worker lost"))
    worker.install(monkeypatch, tmp_path)

    started = client.post("/api/input-cache/build", json=_api_input_body())
    terminal = _wait_for_terminal(client, started.json()["job_id"])

    assert terminal["status"] == "error"
    assert terminal["error_code"] == "build_failed"
    assert "worker lost" not in terminal["message"]
    status = client.post("/api/input-cache/status", json=_api_input_body()).json()
    assert status["state"] == "missing"


def _blocking_table_worker(request: Any, budget: Any) -> Any:
    """Stand-in capped worker: stage partial output where a real build would, then hang.

    It writes the shred scratch directory and each table's parent-chosen store
    staging directory, reports its pid beside the source, and waits to be
    killed, so the parent's stop and settlement are observed on a live child.
    """
    del budget
    inputs = Path(request.cache_root) / ".haute_cache" / "inputs"
    staged = [inputs / ".shred" / f".staging-{request.scratch_token}"]
    staged.extend(
        inputs / digest / f".staging-{plan.staging_token}" for digest, plan in request.plans.items()
    )
    for directory in staged:
        directory.mkdir(parents=True)
    Path(f"{request.data_path}.pid").write_text(str(os.getpid()), encoding="utf-8")
    time.sleep(120)
    raise AssertionError("the worker outlived its parent's cancellation or timeout")


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) != 0  # WAIT_OBJECT_0 == exited
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _poll(predicate: Any, *, seconds: float, message: str) -> Any:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError(message)


@pytest.mark.parametrize("stop", ["cancelled", "timed_out"])
def test_a_stopped_api_input_build_kills_its_worker_and_discards_its_staging(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stop: str,
) -> None:
    """A real capped worker, stopped by cancellation or its deadline, publishes nothing."""
    from haute._json_shred import _snapshots
    from haute._sandbox import set_project_root
    from haute._worker_isolation import process_memory_caps_supported

    if not process_memory_caps_supported():
        # Without a native cap the required policy refuses the spawn before a
        # child exists; termination and settlement do not depend on the cap.
        monkeypatch.setenv("HAUTE_WORKER_MEMORY_ENFORCEMENT", "best_effort")
    if stop == "timed_out":
        monkeypatch.setenv("HAUTE_BUILD_TIMEOUT", "6")
    set_project_root(tmp_path)
    source = _write_quotes(tmp_path)
    monkeypatch.setattr(_snapshots, "build_api_input_tables_worker", _blocking_table_worker)
    inputs = tmp_path / ".haute_cache" / "inputs"

    started = client.post("/api/input-cache/build", json=_api_input_body())
    assert started.status_code == 202
    job_id = started.json()["job_id"]
    pid_file = Path(f"{source}.pid")
    child_pid = int(
        _poll(
            lambda: pid_file.is_file() and pid_file.read_text(encoding="utf-8").strip(),
            seconds=60,
            message="the capped worker did not report its start",
        )
    )
    assert _pid_alive(child_pid)
    assert len(list(inputs.glob("*/.staging-*"))) == 3  # the scratch and both tables

    if stop == "cancelled":
        assert client.delete(f"/api/input-cache/jobs/{job_id}").status_code == 202
    terminal = _poll(
        lambda: (
            (payload := client.get(f"/api/input-cache/jobs/{job_id}").json())["status"] != "running"
            and payload
        ),
        seconds=60,
        message=f"input-cache job {job_id!r} did not finish",
    )

    assert terminal["status"] == stop
    _poll(lambda: not _pid_alive(child_pid), seconds=10, message="the worker survived")
    assert list(inputs.glob("*/.staging-*")) == []
    status = client.post("/api/input-cache/status", json=_api_input_body()).json()
    assert status["state"] == "missing"
    assert {table["state"] for table in status["tables"]} == {"missing"}


@pytest.mark.parametrize(
    ("config", "status_code", "detail"),
    [
        ({"path": "rows.csv", "tables": []}, 400, "invalid_input_config"),
        ({"path": "quotes.jsonl"}, 400, "invalid_input_config"),
        ({"path": "quotes.jsonl", "tables": [{"label": ""}]}, 400, "invalid_input_config"),
        (
            {
                **_api_input_config(),
                "tables": [{**_api_input_config()["tables"][0], "emit": False}],
            },
            400,
            "emits no table",
        ),
        (_api_input_config("../outside.jsonl"), 403, "outside the project root"),
    ],
)
def test_api_input_requests_validate_the_config(
    client: TestClient,
    tmp_path: Path,
    config: dict[str, Any],
    status_code: int,
    detail: str,
) -> None:
    _write_quotes(tmp_path)

    for route in ("status", "clear", "build"):
        response = client.post(f"/api/input-cache/{route}", json=_api_input_body(config))
        assert response.status_code == status_code, (route, response.text)
        assert detail in response.json()["detail"]
