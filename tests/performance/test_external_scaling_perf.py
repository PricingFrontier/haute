"""Reproducible scaling decisions for bounded external and thread-backed paths."""

from __future__ import annotations

import asyncio
import json
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import structlog.testing

from haute import _uc_transport
from haute.routes import mlflow as mlflow_routes
from haute.routes._supersession import SupersessionCoordinator
from haute.routes._timeouts import run_blocking_with_response_timeout

pytestmark = [pytest.mark.perf, pytest.mark.usefixtures("_widen_sandbox_root")]

_MIB = 1024 * 1024
_UC_URL = "uc://workspace.default.projects/performance/cx12"
_UC_ROOT = "/Volumes/workspace/default/projects/performance/cx12"


def _record_perf_evidence(request: pytest.FixtureRequest, **evidence: object) -> None:
    request.node.user_properties.append(("haute_perf_evidence", evidence))


def _event(logs: list[dict[str, Any]], name: str) -> dict[str, Any]:
    matches = [record for record in logs if record.get("event") == name]
    assert len(matches) == 1
    return matches[0]


class _MlflowSearch:
    def __init__(self, runs: list[SimpleNamespace]) -> None:
        self.runs = runs
        self.calls = 0

    def search_runs(self, **kwargs: Any) -> list[SimpleNamespace]:
        self.calls += 1
        assert kwargs == {
            "experiment_ids": ["representative"],
            "filter_string": "status = 'FINISHED'",
            "max_results": 100,
            "output_format": "list",
        }
        return self.runs


class _MlflowArtifacts:
    def __init__(self) -> None:
        self.calls = 0

    def list_artifacts(self, run_id: str) -> list[SimpleNamespace]:
        self.calls += 1
        return [SimpleNamespace(path=f"models/{run_id}.cbm")]


def _representative_runs(count: int) -> list[SimpleNamespace]:
    return [
        SimpleNamespace(
            info=SimpleNamespace(
                run_id=f"run-{index:03d}",
                run_name=f"representative-{index:03d}",
                status="FINISHED",
                start_time=1_700_000_000_000 + index,
            ),
            data=SimpleNamespace(
                metrics={"rmse": float(index) / 100.0},
                params={"segment": f"segment-{index % 10}"},
            ),
        )
        for index in range(count)
    ]


def test_mlflow_run_discovery_maximum_cardinality_budget(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    """The capped N+1 path is cheap locally and never exceeds 101 provider calls."""
    search = _MlflowSearch(_representative_runs(100))
    artifacts = _MlflowArtifacts()
    monkeypatch.setattr(mlflow_routes, "_ensure_tracking", lambda: (search, artifacts))

    with structlog.testing.capture_logs() as logs:
        started_at = time.perf_counter()
        results = mlflow_routes.list_runs("representative", 100, "model")
        handler_ms = (time.perf_counter() - started_at) * 1000

    serialization_started_at = time.perf_counter()
    payload = json.dumps(
        [result.model_dump(mode="json") for result in results],
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    serialization_ms = (time.perf_counter() - serialization_started_at) * 1000

    measurement = _event(logs, "mlflow_run_discovery_completed")
    provider_ms = measurement["search_ms"] + measurement["artifact_ms"]
    local_handler_ms = max(0.0, handler_ms - provider_ms)

    assert len(results) == 100
    assert search.calls == 1
    assert artifacts.calls == 100
    assert measurement["search_calls"] == 1
    assert measurement["artifact_calls"] == 100
    assert measurement["runs_scanned"] == 100
    assert measurement["runs_returned"] == 100
    assert local_handler_ms < 500
    assert serialization_ms < 250
    assert len(payload) < _MIB

    _record_perf_evidence(
        request,
        scenario="external_scaling_mlflow_runs",
        candidates=100,
        provider_calls=search.calls + artifacts.calls,
        search_ms=round(measurement["search_ms"], 3),
        artifact_ms=round(measurement["artifact_ms"], 3),
        local_handler_ms=round(local_handler_ms, 3),
        serialization_ms=round(serialization_ms, 3),
        payload_bytes=len(payload),
    )


class _FakeNotFoundError(Exception):
    pass


class _FakeAlreadyExistsError(Exception):
    pass


class _Reader:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload


class _Response:
    def __init__(self, payload: bytes) -> None:
        self.contents = _Reader(payload)


class _CountingFiles:
    """In-memory Files API with a small, explicit upload latency."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.uploads: list[tuple[str, int]] = []

    def download(self, path: str) -> _Response:
        try:
            return _Response(self.store[path])
        except KeyError:
            raise _FakeNotFoundError(path) from None

    def upload(self, path: str, contents: Any, overwrite: bool = False) -> None:
        assert overwrite in (True, False)
        if not overwrite and path in self.store:
            raise _FakeAlreadyExistsError(path)
        time.sleep(0.002)
        payload = contents.read()
        self.store[path] = payload
        self.uploads.append((path, len(payload)))

    def delete(self, path: str) -> None:
        try:
            del self.store[path]
        except KeyError:
            raise _FakeNotFoundError(path) from None

    def list_directory_contents(self, directory: str):
        prefix = directory.rstrip("/") + "/"
        names = {
            path[len(prefix) :].split("/", 1)[0] for path in self.store if path.startswith(prefix)
        }
        return iter(SimpleNamespace(name=name) for name in sorted(names))


def _run_git(repo: Path, *args: str, input_bytes: bytes | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        input=input_bytes,
        capture_output=True,
        check=True,
    )
    return result.stdout.decode("utf-8", errors="replace").strip()


def _data_command(payload: bytes) -> list[bytes]:
    return [f"data {len(payload)}\n".encode(), payload, b"\n"]


def _append_fast_import_history(repo: Path, *, existing: int, add: int) -> None:
    """Append deterministic commits in one process rather than timing test setup."""
    stream: list[bytes] = []
    existing_tip = _run_git(repo, "rev-parse", "main") if existing else None
    for offset in range(add):
        commit_number = existing + offset + 1
        mark = offset + 1
        message = f"representative history {commit_number}".encode()
        content = (
            f"# pricing configuration revision {commit_number}\nrate = {commit_number % 97}\n"
        ).encode()
        stream.extend(
            [
                b"commit refs/heads/main\n",
                f"mark :{mark}\n".encode(),
                (
                    f"author Performance <performance@example.com> "
                    f"{1_700_000_000 + commit_number} +0000\n"
                ).encode(),
                (
                    f"committer Performance <performance@example.com> "
                    f"{1_700_000_000 + commit_number} +0000\n"
                ).encode(),
                *_data_command(message),
            ]
        )
        if offset == 0 and existing_tip is not None:
            stream.append(f"from {existing_tip}\n".encode())
        elif offset:
            stream.append(f"from :{mark - 1}\n".encode())
        stream.extend([b"M 100644 inline pricing.py\n", *_data_command(content), b"\n"])
    stream.append(b"done\n")
    _run_git(repo, "fast-import", "--quiet", input_bytes=b"".join(stream))


def _bundle_generations(files: _CountingFiles) -> list[int]:
    prefix = f"{_UC_ROOT}/bundles/"
    return sorted(
        int(path[len(prefix) :].split("-", 1)[0]) for path in files.store if path.startswith(prefix)
    )


def test_uc_full_bundle_history_scaling_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    """Real complete bundles remain bounded through 500 representative commits."""
    repo = tmp_path / "uc-history"
    repo.mkdir()
    _run_git(repo, "init", "--quiet", "-b", "main")

    files = _CountingFiles()
    monkeypatch.setattr(_uc_transport, "_files_api", lambda: files)
    monkeypatch.setattr(
        _uc_transport,
        "_is_not_found",
        lambda exc: isinstance(exc, _FakeNotFoundError),
    )
    monkeypatch.setattr(
        _uc_transport,
        "_is_already_exists",
        lambda exc: isinstance(exc, _FakeAlreadyExistsError),
    )
    monkeypatch.setattr(_uc_transport, "_writer", _uc_transport._WriterState())

    samples: list[dict[str, float | int]] = []
    existing = 0
    try:
        for target in (10, 100, 500):
            _append_fast_import_history(repo, existing=existing, add=target - existing)
            existing = target
            assert int(_run_git(repo, "rev-list", "--count", "main")) == target

            with structlog.testing.capture_logs() as logs:
                _uc_transport.publish_to_uc(_UC_URL, repo)
            measurement = _event(logs, "uc_publish_measurement")
            samples.append(
                {
                    "history_commits": target,
                    "bundle_bytes": measurement["bundle_bytes"],
                    "bundle_create_ms": round(measurement["bundle_create_ms"], 3),
                    "bundle_verify_ms": round(measurement["bundle_verify_ms"], 3),
                    "network_ms": round(measurement["network_ms"], 3),
                    "local_record_ms": round(measurement["local_record_ms"], 3),
                    "cleanup_ms": round(measurement["cleanup_ms"], 3),
                    "total_ms": round(measurement["total_ms"], 3),
                }
            )
    finally:
        _uc_transport._writer.heartbeat.stop()

    bundle_uploads = [item for item in files.uploads if "/bundles/" in item[0]]
    pointer_uploads = [item for item in files.uploads if "/pointers/" in item[0]]
    bundle_sizes = [sample["bundle_bytes"] for sample in samples]

    assert len(bundle_uploads) == 3
    assert len(pointer_uploads) == 3
    assert bundle_sizes == sorted(bundle_sizes)
    assert bundle_sizes[-1] < 25 * _MIB
    assert samples[-1]["bundle_create_ms"] < 5_000
    assert samples[-1]["bundle_verify_ms"] < 2_000
    assert all(sample["total_ms"] < 10_000 for sample in samples)
    assert len(_bundle_generations(files)) <= _uc_transport._UC_BUNDLE_RETAIN

    _record_perf_evidence(
        request,
        scenario="external_scaling_uc_complete_bundles",
        samples=samples,
        bundle_uploads=len(bundle_uploads),
        pointer_uploads=len(pointer_uploads),
        retained_generations=len(_bundle_generations(files)),
        retention_limit=_uc_transport._UC_BUNDLE_RETAIN,
    )


@pytest.mark.asyncio
async def test_cancelled_thread_retains_one_slot_until_cleanup(
    request: pytest.FixtureRequest,
) -> None:
    """Cancellation retains real occupancy, then releases it without a tail."""
    coordinator = SupersessionCoordinator()
    limiter = asyncio.Semaphore(1)
    first_started = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()
    first_finished_at: list[float] = []
    second_started_at: list[float] = []

    def first_callable() -> str:
        first_started.set()
        assert release_first.wait(2)
        first_finished_at.append(time.perf_counter())
        return "first"

    def second_callable() -> str:
        second_started_at.append(time.perf_counter())
        second_started.set()
        return "second"

    async def first_worker() -> str:
        return await run_blocking_with_response_timeout(
            first_callable,
            timeout=1,
            operation="cx12_cancelled",
        )

    async def second_worker() -> str:
        return await run_blocking_with_response_timeout(
            second_callable,
            timeout=1,
            operation="cx12_waiter",
        )

    with structlog.testing.capture_logs() as logs:
        first = asyncio.create_task(
            coordinator.run_latest(
                "first-key",
                first_worker,
                limiter=limiter,
                operation="cx12_cancelled",
            )
        )
        assert await asyncio.to_thread(first_started.wait, 2)

        cancelled_at = time.perf_counter()
        first.cancel()
        await asyncio.sleep(0.05)
        assert not first.done()

        second_submitted_at = time.perf_counter()
        second = asyncio.create_task(
            coordinator.run_latest(
                "second-key",
                second_worker,
                limiter=limiter,
                operation="cx12_waiter",
            )
        )
        await asyncio.sleep(0.05)
        assert not second_started.is_set()

        released_at = time.perf_counter()
        release_first.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert await second == "second"
        completed_at = time.perf_counter()

    assert first_finished_at and second_started_at
    retained_occupancy_ms = (first_finished_at[0] - cancelled_at) * 1000
    limiter_wait_ms = (second_started_at[0] - second_submitted_at) * 1000
    post_release_ms = (second_started_at[0] - released_at) * 1000
    bookkeeping_tail_ms = (completed_at - second_started_at[0]) * 1000
    cancelled_measurement = next(
        record
        for record in logs
        if record.get("event") == "route_blocking_work_completed"
        and record.get("operation") == "cx12_cancelled"
    )

    assert cancelled_measurement["outcome"] == "request_cancelled"
    assert cancelled_measurement["running_at_response"] == 1
    assert cancelled_measurement["cancellation_waiters_at_response"] == 1
    assert cancelled_measurement["queued_after_cleanup"] == 0
    assert cancelled_measurement["running_after_cleanup"] == 0
    assert cancelled_measurement["cancellation_waiters_after_cleanup"] == 0
    assert retained_occupancy_ms >= 90
    assert limiter_wait_ms >= 40
    assert post_release_ms < 250
    assert bookkeeping_tail_ms < 100
    assert limiter._value == 1  # noqa: SLF001 - certificate proves the owned permit converges

    _record_perf_evidence(
        request,
        scenario="external_scaling_thread_cancellation_occupancy",
        limiter_capacity=1,
        retained_occupancy_ms=round(retained_occupancy_ms, 3),
        limiter_wait_ms=round(limiter_wait_ms, 3),
        post_release_ms=round(post_release_ms, 3),
        bookkeeping_tail_ms=round(bookkeeping_tail_ms, 3),
        queue_ms=round(cancelled_measurement["queue_ms"], 3),
        execution_ms=round(cancelled_measurement["execution_ms"], 3),
        response_wait_ms=round(cancelled_measurement["response_wait_ms"], 3),
        cleanup_ms=round(cancelled_measurement["cleanup_ms"], 3),
    )
