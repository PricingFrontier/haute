from __future__ import annotations

import hashlib
import os
import pickle
import queue
import time
from pathlib import Path

import pytest

from haute._worker_isolation import (
    IsolatedWorkerConfig,
    IsolatedWorkerCrashedError,
    IsolatedWorkerRemoteError,
    IsolatedWorkerStartError,
    IsolatedWorkerStoppedError,
    IsolatedWorkerTimeoutError,
    process_memory_caps_supported,
)
from haute._worker_protocol import (
    WORKER_MAX_EVENTS,
    WorkerArtifactManifest,
    WorkerFailurePayload,
    WorkerProgressEnd,
    WorkerProgressEvent,
    WorkerProtocolError,
    WorkerRemoteFailureError,
    WorkerRequest,
    WorkerResultManifest,
    _protocol_entrypoint,
    build_artifact_manifest,
    run_worker_protocol,
    validate_result_manifest,
)


def _worker_with_progress(runtime, request):
    runtime.emit_progress(
        progress=0.25, message="Starting", kind="phase", fields={"request": request.request_id}
    )
    runtime.emit_progress(progress=1.0, message="Done", kind="phase")
    return WorkerResultManifest(metadata={"ok": True})


def _thread_starved_worker(runtime, request):
    del runtime, request
    raise RuntimeError("can't start new thread")


def _worker_recording_pid(runtime, request):
    del runtime, request
    return WorkerResultManifest(metadata={"pid": os.getpid()})


def _failing_worker_recording_pid(runtime, request):
    del runtime, request
    raise ValueError(f"child {os.getpid()} failed")


def _failing_worker(runtime, request):
    del runtime, request
    raise ValueError("child failed")


def _declared_failure_worker(runtime, request):
    del runtime, request
    return WorkerFailurePayload(
        terminal_reason="cancelled",
        error_type="WorkerCancelled",
        message="cancelled by worker",
        traceback="worker-specific diagnostic",
        fields={"phase": "fit"},
    )


def _slow_worker(runtime, request):
    del request
    runtime.emit_progress(progress=0.1, message="Waiting", kind="phase")
    time.sleep(5)
    return WorkerResultManifest(metadata={})


def _large_result_worker(runtime, request):
    del runtime, request
    return WorkerResultManifest(metadata={"payload": "x" * (1024 * 1024)})


def _progress_budget_worker(runtime, request):
    del request
    for index in range(WORKER_MAX_EVENTS + 10):
        runtime.emit_progress(
            progress=index / (WORKER_MAX_EVENTS + 10),
            message="working",
            kind="iteration",
        )
    return WorkerResultManifest(metadata={"ok": True})


def _crash_worker(runtime, request):
    del runtime, request
    os._exit(23)


def _worker_reporting_native_cap(runtime, request):
    del runtime, request
    from haute._native_memory_limit import current_native_memory_backend

    return WorkerResultManifest(metadata={"backend": current_native_memory_backend()})


def _request() -> WorkerRequest:
    return WorkerRequest("request-1", "training", {"items": [1, True, None]})


@pytest.mark.parametrize(
    ("memory_limit", "required", "allowance", "expected"),
    [(None, True, 0, []), (128, False, 0, [(128, False, 0)]), (128, True, 64, [(128, True, 64)])],
)
def test_protocol_entrypoint_installs_the_shared_native_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    memory_limit: int | None,
    required: bool,
    allowance: int,
    expected: list[tuple[int, bool, int]],
) -> None:
    from haute._native_memory_limit import NativeMemoryLease

    results: queue.Queue[object] = queue.Queue()
    progress: queue.Queue[object] = queue.Queue()
    applied: list[tuple[int, bool, int]] = []

    def apply(
        self: NativeMemoryLease,
        growth_bytes: int,
        *,
        required: bool,
        address_space_allowance_bytes: int = 0,
    ) -> bool:
        applied.append((growth_bytes, required, address_space_allowance_bytes))
        return False

    monkeypatch.setattr(NativeMemoryLease, "apply", apply)

    _protocol_entrypoint(
        results,
        progress,
        _worker_with_progress,
        _request(),
        str(tmp_path),
        memory_limit,
        required,
        allowance,
    )

    status, result = results.get_nowait()  # type: ignore[misc]
    assert status == "ok"
    assert isinstance(result, WorkerResultManifest)
    assert applied == expected


class _RecordingFeederQueue:
    """An in-process result/progress queue that records when its feeder starts."""

    def __init__(self, events: list[str], name: str) -> None:
        self._events = events
        self._name = name
        self._thread: object | None = None
        self.items: list[object] = []

    def _start_thread(self) -> None:
        self._events.append(f"{self._name} feeder")
        self._thread = object()

    def put(self, item: object, *_args: object, **_kwargs: object) -> None:
        self.items.append(item)

    def put_nowait(self, item: object) -> None:
        self.items.append(item)


def test_protocol_worker_starts_its_queue_feeders_before_the_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A feeder started after the cap could fail to start at the first put."""
    from haute._native_memory_limit import NativeMemoryLease

    events: list[str] = []
    results = _RecordingFeederQueue(events, "result")
    progress = _RecordingFeederQueue(events, "progress")
    monkeypatch.setattr(
        NativeMemoryLease, "apply", lambda self, *_a, **_k: events.append("cap") or False
    )

    _protocol_entrypoint(
        results, progress, _worker_with_progress, _request(), str(tmp_path), 128, True, 0
    )

    assert events == ["result feeder", "progress feeder", "cap"]


def test_a_required_cap_the_host_cannot_install_is_a_contract_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._native_memory_limit import (
        NativeMemoryLease,
        NativeMemoryLimitUnsupportedError,
    )

    def refuse(self: NativeMemoryLease, *_args: object, **_kwargs: object) -> bool:
        raise NativeMemoryLimitUnsupportedError("no native cap on this host")

    results: queue.Queue[object] = queue.Queue()
    monkeypatch.setattr(NativeMemoryLease, "apply", refuse)

    _protocol_entrypoint(
        results, queue.Queue(), _worker_with_progress, _request(), str(tmp_path), 128, True, 0
    )

    status, failure = results.get_nowait()  # type: ignore[misc]
    assert status == "error"
    assert failure.terminal_reason == "contract_error"
    assert failure.error_type == "NativeMemoryLimitUnsupportedError"


def test_a_thread_that_cannot_start_under_the_cap_is_a_memory_limit_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute._native_memory_limit import NativeMemoryLease

    results: queue.Queue[object] = queue.Queue()
    progress: queue.Queue[object] = queue.Queue()
    monkeypatch.setattr(NativeMemoryLease, "apply", lambda self, *_a, **_k: True)

    _protocol_entrypoint(
        results,
        progress,
        _thread_starved_worker,
        _request(),
        str(tmp_path),
        128,
        True,
        0,
    )

    status, failure = results.get_nowait()  # type: ignore[misc]
    assert status == "error"
    assert failure.terminal_reason == "memory_limited"
    assert failure.error_type == "MemoryError"


def test_dtos_reject_non_plain_data_and_bounds() -> None:
    with pytest.raises(WorkerProtocolError, match="non-finite"):
        WorkerRequest("request", "kind", {"bad": float("nan")})
    with pytest.raises(WorkerProtocolError, match="mapping keys"):
        WorkerRequest("request", "kind", {1: "bad"})
    with pytest.raises(WorkerProtocolError, match="progress"):
        WorkerProgressEvent(0, 1.1, "message", "kind", {})
    with pytest.raises(WorkerProtocolError, match="schema"):
        WorkerRequest("request", "kind", {}, schema_version=2)
    with pytest.raises(WorkerProtocolError, match="schema"):
        WorkerRequest("request", "kind", {}, schema_version=True)
    with pytest.raises(WorkerProtocolError, match="request payload"):
        WorkerRequest("request", "kind", {"blob": "x" * (4 * 1024 * 1024)})

    class DictSubclass(dict):
        pass

    with pytest.raises(WorkerProtocolError, match="not plain"):
        WorkerRequest("request", "kind", DictSubclass())

    class StringSubclass(str):
        pass

    with pytest.raises(WorkerProtocolError, match="not plain"):
        WorkerRequest("request", "kind", {"value": StringSubclass("bad")})
    with pytest.raises(WorkerProtocolError, match="request_id"):
        WorkerRequest(StringSubclass("request"), "kind", {})


def test_protocol_bounds_identifiers_paths_and_plain_data_depth() -> None:
    with pytest.raises(WorkerProtocolError, match="request_id exceeds length"):
        WorkerRequest("r" * 513, "kind", {})
    with pytest.raises(WorkerProtocolError, match="relative_path exceeds length"):
        WorkerArtifactManifest("model", "p" * 4_097, 0, "0" * 64, "staged")

    nested: object = None
    for _ in range(65):
        nested = [nested]
    with pytest.raises(WorkerProtocolError, match="nesting depth"):
        WorkerRequest("request", "kind", nested)


def test_parent_revalidates_a_forged_request_before_spawning(tmp_path: Path) -> None:
    forged = object.__new__(WorkerRequest)
    object.__setattr__(forged, "request_id", "request")
    object.__setattr__(forged, "kind", "kind")
    object.__setattr__(
        forged,
        "_payload_bytes",
        pickle.dumps({}, protocol=pickle.HIGHEST_PROTOCOL),
    )
    object.__setattr__(forged, "schema_version", True)

    with pytest.raises(WorkerProtocolError, match="schema"):
        run_worker_protocol(
            _worker_with_progress,
            forged,
            artifact_root=tmp_path / "artifacts",
            artifact_kinds=frozenset({"model"}),
            max_artifact_size_bytes=100,
        )


def test_request_payload_is_serialized_once_before_spawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute import _worker_protocol

    payload = {"marker": "serialize-once"}
    real_dumps = pickle.dumps
    payload_serializations = 0

    def counting_dumps(value, *args, **kwargs):
        nonlocal payload_serializations
        if value is payload:
            payload_serializations += 1
        return real_dumps(value, *args, **kwargs)

    monkeypatch.setattr(_worker_protocol.pickle, "dumps", counting_dumps)
    request = WorkerRequest("request", "kind", payload)

    result = run_worker_protocol(
        _large_result_worker,
        request,
        artifact_root=tmp_path / "artifacts",
        artifact_kinds=frozenset(),
        max_artifact_size_bytes=0,
        config=IsolatedWorkerConfig(timeout_seconds=10),
    )

    assert result.metadata["payload"].startswith("x")
    assert payload_serializations == 1


def test_parent_rejects_non_monotonic_progress(tmp_path: Path) -> None:
    # The private queue reader is exercised with forged queue contents; a spawn
    # worker cannot be instructed to emit a duplicate through WorkerRuntime.
    first = WorkerProgressEvent(0, 0.1, "one", "phase", {})
    duplicate = WorkerProgressEvent(0, 0.2, "two", "phase", {})
    from haute._worker_protocol import _drain_progress

    class Queue:
        def __init__(self):
            self.values = [
                pickle.dumps(first, protocol=pickle.HIGHEST_PROTOCOL),
                pickle.dumps(duplicate, protocol=pickle.HIGHEST_PROTOCOL),
            ]

        def get_nowait(self):
            if not self.values:
                import queue

                raise queue.Empty
            return self.values.pop(0)

    with pytest.raises(WorkerProtocolError, match="does not match"):
        _drain_progress(Queue(), 0, None)


def test_parent_drain_batch_is_bounded() -> None:
    from haute._worker_protocol import WORKER_EVENT_QUEUE_CAPACITY, _drain_progress

    class Queue:
        def __init__(self) -> None:
            self.values = [
                pickle.dumps(
                    WorkerProgressEvent(index, 0.5, "event", "phase", {}),
                    protocol=pickle.HIGHEST_PROTOCOL,
                )
                for index in range(WORKER_EVENT_QUEUE_CAPACITY + 1)
            ]

        def get_nowait(self):
            if not self.values:
                raise queue.Empty
            return self.values.pop(0)

    progress_queue = Queue()
    received: list[WorkerProgressEvent] = []

    expected, ended = _drain_progress(progress_queue, 0, received.append)

    assert expected == WORKER_EVENT_QUEUE_CAPACITY
    assert ended is False
    assert len(received) == WORKER_EVENT_QUEUE_CAPACITY
    assert len(progress_queue.values) == 1


def test_post_exit_drain_validates_feeder_delayed_event() -> None:
    from haute._worker_protocol import _drain_progress_until_end

    event = WorkerProgressEvent(0, 1.0, "late", "phase", {})

    class Queue:
        def get_nowait(self):
            import queue

            raise queue.Empty

        def get(self, timeout):
            del timeout
            value = self.values.pop(0)
            return value

        values = [
            pickle.dumps(event, protocol=pickle.HIGHEST_PROTOCOL),
            pickle.dumps(WorkerProgressEnd(1, 0), protocol=pickle.HIGHEST_PROTOCOL),
        ]

    received: list[WorkerProgressEvent] = []
    _drain_progress_until_end(Queue(), 0, received.append)

    assert received == [event]


def test_progress_emission_is_non_blocking_and_reports_drops(tmp_path: Path) -> None:
    from haute._worker_protocol import WorkerRuntime

    class ToggleQueue:
        def __init__(self) -> None:
            self.full = True
            self.items: list[bytes] = []

        def put_nowait(self, item: bytes) -> None:
            if self.full:
                raise queue.Full
            self.items.append(item)

        def put(self, item: bytes) -> None:
            self.items.append(item)

    progress_queue = ToggleQueue()
    runtime = WorkerRuntime(progress_queue, str(tmp_path))

    assert runtime.emit_progress(progress=0.1, message="dropped", kind="iteration") is False
    progress_queue.full = False
    assert runtime.emit_progress(progress=0.2, message="delivered", kind="iteration") is True
    runtime.close()

    event, end = (pickle.loads(item) for item in progress_queue.items)
    assert isinstance(event, WorkerProgressEvent)
    assert event.sequence == 0
    assert event.dropped_events == 1
    assert isinstance(end, WorkerProgressEnd)
    assert end.sequence == 1
    assert end.dropped_events == 0
    with pytest.raises(WorkerProtocolError, match="closed"):
        runtime.emit_progress(progress=0.3, message="late", kind="iteration")


def test_progress_budget_exhaustion_drops_instead_of_failing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute import _worker_protocol

    class CollectingQueue:
        def __init__(self) -> None:
            self.items: list[bytes] = []

        def put_nowait(self, item: bytes) -> None:
            self.items.append(item)

        def put(self, item: bytes) -> None:
            self.items.append(item)

    monkeypatch.setattr(_worker_protocol, "WORKER_MAX_EVENTS", 2)
    progress_queue = CollectingQueue()
    runtime = _worker_protocol.WorkerRuntime(progress_queue, str(tmp_path))

    assert runtime.emit_progress(progress=0.1, message="one", kind="iteration") is True
    assert runtime.emit_progress(progress=0.2, message="two", kind="iteration") is True
    assert runtime.emit_progress(progress=0.3, message="three", kind="iteration") is False
    assert runtime.emit_progress(progress=0.4, message="four", kind="iteration") is False
    runtime.close()

    first, second, end = (pickle.loads(item) for item in progress_queue.items)
    assert isinstance(first, WorkerProgressEvent)
    assert isinstance(second, WorkerProgressEvent)
    assert isinstance(end, WorkerProgressEnd)
    assert [first.sequence, second.sequence] == [0, 1]
    assert end.sequence == 2
    assert end.dropped_events == 2


def test_progress_event_size_is_measured_only_at_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute import _worker_protocol

    real_dumps = pickle.dumps
    serialized: list[object] = []

    def counting_dumps(value, *args, **kwargs):
        serialized.append(value)
        return real_dumps(value, *args, **kwargs)

    monkeypatch.setattr(_worker_protocol.pickle, "dumps", counting_dumps)
    WorkerProgressEvent(0, 0.5, "constructed", "phase", {})
    assert serialized == []

    class Queue:
        def put_nowait(self, item: bytes) -> None:
            self.item = item

    runtime = _worker_protocol.WorkerRuntime(Queue(), str(tmp_path))
    assert runtime.emit_progress(progress=0.5, message="emitted", kind="phase") is True
    assert len(serialized) == 1
    assert isinstance(serialized[0], WorkerProgressEvent)


def test_oversized_progress_event_still_fails_at_transport(tmp_path: Path) -> None:
    from haute._worker_protocol import WorkerRuntime

    class Queue:
        def put_nowait(self, item: bytes) -> None:
            raise AssertionError("oversized events must not reach the queue")

    runtime = WorkerRuntime(Queue(), str(tmp_path))

    with pytest.raises(WorkerProtocolError, match="serialized progress event"):
        runtime.emit_progress(
            progress=0.5,
            message="large",
            kind="phase",
            fields={"blob": "x" * (64 * 1024)},
        )


def test_termination_helper_rejects_a_process_that_remains_alive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute import _worker_protocol

    class StuckProcess:
        def is_alive(self) -> bool:
            return True

    monkeypatch.setattr(_worker_protocol, "_terminate_process", lambda process: None)
    with pytest.raises(_worker_protocol.WorkerProcessTerminationError):
        _worker_protocol._terminate_and_confirm(StuckProcess())


def test_manifest_rejects_traversal_unknown_kind_symlink_and_tamper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(WorkerProtocolError, match="normalized"):
        WorkerArtifactManifest("model", "../outside", 0, "0" * 64, "staged")
    with pytest.raises(WorkerProtocolError, match="normalized"):
        WorkerArtifactManifest("model", "C:/outside", 0, "0" * 64, "staged")
    artifact = root / "model.bin"
    artifact.write_bytes(b"original")
    manifest = WorkerArtifactManifest(
        "unknown", "model.bin", 8, hashlib.sha256(b"original").hexdigest(), "staged"
    )
    with pytest.raises(WorkerProtocolError, match="unknown"):
        validate_result_manifest(
            WorkerResultManifest({}, (manifest,)),
            artifact_root=root,
            artifact_kinds=frozenset({"model"}),
            max_artifact_size_bytes=100,
        )
    valid = WorkerArtifactManifest(
        "model", "model.bin", 8, hashlib.sha256(b"original").hexdigest(), "staged"
    )
    artifact.write_bytes(b"tampered")
    with pytest.raises(WorkerProtocolError, match="digest"):
        validate_result_manifest(
            WorkerResultManifest({}, (valid,)),
            artifact_root=root,
            artifact_kinds=frozenset({"model"}),
            max_artifact_size_bytes=100,
        )
    target = tmp_path / "outside"
    target.write_bytes(b"outside")
    link = root / "link.bin"
    try:
        link.symlink_to(target)
    except OSError:
        # Unprivileged Windows runners may not create symlinks. Exercise the
        # same validation branch with a regular file reported as a symlink.
        link.write_bytes(b"outside")
        monkeypatch.setattr(Path, "is_symlink", lambda self: self == link)
    symlink_manifest = WorkerArtifactManifest(
        "model", "link.bin", 7, hashlib.sha256(b"outside").hexdigest(), "staged"
    )
    with pytest.raises(WorkerProtocolError, match="contained regular"):
        validate_result_manifest(
            WorkerResultManifest({}, (symlink_manifest,)),
            artifact_root=root,
            artifact_kinds=frozenset({"model"}),
            max_artifact_size_bytes=100,
        )


def test_build_artifact_manifest_requires_containment_and_is_valid(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    artifact = root / "nested" / "model.bin"
    artifact.parent.mkdir()
    artifact.write_bytes(b"model")

    manifest = build_artifact_manifest(
        artifact_root=root,
        path=artifact,
        kind="model",
        lifetime="staged",
    )

    assert manifest.relative_path == "nested/model.bin"
    validate_result_manifest(
        WorkerResultManifest({}, (manifest,)),
        artifact_root=root,
        artifact_kinds=frozenset({"model"}),
        max_artifact_size_bytes=100,
    )
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")
    with pytest.raises(WorkerProtocolError, match="contained"):
        build_artifact_manifest(
            artifact_root=root,
            path=outside,
            kind="model",
            lifetime="staged",
        )


@pytest.mark.skipif(
    not process_memory_caps_supported(), reason="this host has no native memory cap"
)
def test_real_spawn_training_worker_runs_under_a_native_cap(tmp_path: Path) -> None:
    """A required-limit protocol worker installs the same native cap as every other
    worker: a Job Object on Windows, a cgroup or RLIMIT_AS on Linux."""
    result = run_worker_protocol(
        _worker_reporting_native_cap,
        _request(),
        artifact_root=tmp_path / "artifacts",
        artifact_kinds=frozenset({"model"}),
        max_artifact_size_bytes=100,
        config=IsolatedWorkerConfig(
            memory_limit_bytes=1024 * 1024 * 1024,
            require_memory_limit=True,
            timeout_seconds=60,
        ),
    )

    assert result.metadata["backend"] in {"windows_job", "cgroup", "rlimit"}


@pytest.mark.parametrize("fails", [False, True])
def test_real_spawn_removes_the_workers_private_cgroup_after_it_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fails: bool,
) -> None:
    """Cleanup is for the spawned child itself, once that child has exited, on success
    and on failure: the parent's pid, or a still-running child, would leave the
    worker's cgroup behind."""
    import haute._worker_protocol as protocol_mod
    from haute._process_memory import process_is_alive

    cleaned: list[tuple[int, bool]] = []
    monkeypatch.setattr(
        protocol_mod,
        "cleanup_private_cgroups_for_pid",
        lambda pid: cleaned.append((pid, process_is_alive(pid))),
    )

    def run() -> WorkerResultManifest:
        return run_worker_protocol(
            _failing_worker_recording_pid if fails else _worker_recording_pid,
            _request(),
            artifact_root=tmp_path / "artifacts",
            artifact_kinds=frozenset({"model"}),
            max_artifact_size_bytes=100,
        )

    if fails:
        with pytest.raises(WorkerRemoteFailureError) as failure:
            run()
        child_pid = int(failure.value.remote_message.split()[1])
    else:
        child_pid = int(run().metadata["pid"])

    assert child_pid != os.getpid()
    assert cleaned == [(child_pid, False)]


@pytest.mark.parametrize("fails", [False, True])
def test_a_failed_cgroup_cleanup_is_reported_not_swallowed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fails: bool,
) -> None:
    """A worker cgroup left behind is the failure when the job succeeded, and a note
    on the job's own failure when it did not."""
    import haute._worker_protocol as protocol_mod

    def refuse_cleanup(pid: int) -> None:
        raise OSError(f"cgroup for {pid} is busy")

    monkeypatch.setattr(protocol_mod, "cleanup_private_cgroups_for_pid", refuse_cleanup)

    if fails:
        with pytest.raises(WorkerRemoteFailureError) as failure:
            run_worker_protocol(
                _failing_worker_recording_pid,
                _request(),
                artifact_root=tmp_path / "artifacts",
                artifact_kinds=frozenset({"model"}),
                max_artifact_size_bytes=100,
            )
        notes = getattr(failure.value, "__notes__", [])
        assert any(
            note.startswith("native memory resource cleanup failed: cgroup for") for note in notes
        )
    else:
        with pytest.raises(OSError, match="is busy"):
            run_worker_protocol(
                _worker_recording_pid,
                _request(),
                artifact_root=tmp_path / "artifacts",
                artifact_kinds=frozenset({"model"}),
                max_artifact_size_bytes=100,
            )


def test_a_worker_that_never_started_has_no_cgroup_to_remove(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import multiprocessing.context

    import haute._worker_protocol as protocol_mod

    cleaned: list[int] = []
    monkeypatch.setattr(protocol_mod, "cleanup_private_cgroups_for_pid", cleaned.append)

    def refuse_start(self: object) -> None:
        raise OSError("spawn refused")

    monkeypatch.setattr(multiprocessing.context.SpawnProcess, "start", refuse_start)

    with pytest.raises(IsolatedWorkerStartError, match="spawn refused"):
        run_worker_protocol(
            _worker_recording_pid,
            _request(),
            artifact_root=tmp_path / "artifacts",
            artifact_kinds=frozenset({"model"}),
            max_artifact_size_bytes=100,
        )

    assert cleaned == []


def test_real_spawn_forwards_validated_progress_and_result(tmp_path: Path) -> None:
    events: list[WorkerProgressEvent] = []
    result = run_worker_protocol(
        _worker_with_progress,
        _request(),
        artifact_root=tmp_path / "artifacts",
        artifact_kinds=frozenset({"model"}),
        max_artifact_size_bytes=100,
        on_progress=events.append,
    )

    assert result.metadata == {"ok": True}
    assert [event.sequence for event in events] == [0, 1]


def test_real_spawn_progress_budget_exhaustion_does_not_fail(tmp_path: Path) -> None:
    events: list[WorkerProgressEvent] = []

    result = run_worker_protocol(
        _progress_budget_worker,
        _request(),
        artifact_root=tmp_path / "artifacts",
        artifact_kinds=frozenset(),
        max_artifact_size_bytes=0,
        on_progress=events.append,
        config=IsolatedWorkerConfig(timeout_seconds=20),
    )

    assert result.metadata == {"ok": True}
    assert 0 < len(events) <= WORKER_MAX_EVENTS
    assert [event.sequence for event in events] == list(range(len(events)))


def test_real_spawn_drains_large_result_before_joining_child(tmp_path: Path) -> None:
    result = run_worker_protocol(
        _large_result_worker,
        _request(),
        artifact_root=tmp_path / "artifacts",
        artifact_kinds=frozenset(),
        max_artifact_size_bytes=0,
        config=IsolatedWorkerConfig(timeout_seconds=10),
    )

    assert len(result.metadata["payload"]) == 1024 * 1024


def test_cleanup_runs_when_progress_callback_raises(tmp_path: Path) -> None:
    cleaned: list[str] = []

    def reject_progress(event: WorkerProgressEvent) -> None:
        del event
        raise RuntimeError("callback failed")

    with pytest.raises(RuntimeError, match="callback failed"):
        run_worker_protocol(
            _worker_with_progress,
            _request(),
            artifact_root=tmp_path / "artifacts",
            artifact_kinds=frozenset({"model"}),
            max_artifact_size_bytes=100,
            on_progress=reject_progress,
            config=IsolatedWorkerConfig(cleanup_callbacks=(lambda: cleaned.append("yes"),)),
        )

    assert cleaned == ["yes"]


def test_child_failure_is_typed(tmp_path: Path) -> None:
    with pytest.raises(IsolatedWorkerRemoteError, match="child failed"):
        run_worker_protocol(
            _failing_worker,
            _request(),
            artifact_root=tmp_path / "artifacts",
            artifact_kinds=frozenset({"model"}),
            max_artifact_size_bytes=100,
        )


def test_transported_failure_preserves_reason_and_fields(tmp_path: Path) -> None:
    with pytest.raises(WorkerRemoteFailureError) as exc_info:
        run_worker_protocol(
            _declared_failure_worker,
            _request(),
            artifact_root=tmp_path / "artifacts",
            artifact_kinds=frozenset({"model"}),
            max_artifact_size_bytes=100,
        )

    assert exc_info.value.terminal_reason == "cancelled"
    assert exc_info.value.fields == {"phase": "fit"}


def test_timeout_and_cancel_clean_up_process(tmp_path: Path) -> None:
    cleaned: list[str] = []
    with pytest.raises(IsolatedWorkerTimeoutError):
        run_worker_protocol(
            _slow_worker,
            _request(),
            artifact_root=tmp_path / "timeout",
            artifact_kinds=frozenset({"model"}),
            max_artifact_size_bytes=100,
            config=IsolatedWorkerConfig(
                timeout_seconds=0.1,
                stop_poll_interval_seconds=0.02,
                cleanup_callbacks=(lambda: cleaned.append("timeout"),),
            ),
        )
    with pytest.raises(IsolatedWorkerStoppedError) as exc_info:
        run_worker_protocol(
            _slow_worker,
            _request(),
            artifact_root=tmp_path / "cancel",
            artifact_kinds=frozenset({"model"}),
            max_artifact_size_bytes=100,
            config=IsolatedWorkerConfig(
                stop_reason=lambda: "cancelled",
                stop_poll_interval_seconds=0.02,
                cleanup_callbacks=(lambda: cleaned.append("cancel"),),
            ),
        )
    assert exc_info.value.terminal_reason == "cancelled"
    assert cleaned == ["timeout", "cancel"]


def test_worker_crash_is_contained_and_runs_parent_cleanup(tmp_path: Path) -> None:
    cleaned: list[str] = []

    with pytest.raises(IsolatedWorkerCrashedError) as exc_info:
        run_worker_protocol(
            _crash_worker,
            _request(),
            artifact_root=tmp_path / "crash",
            artifact_kinds=frozenset(),
            max_artifact_size_bytes=0,
            config=IsolatedWorkerConfig(
                cleanup_callbacks=(lambda: cleaned.append("crash"),),
            ),
        )

    assert exc_info.value.exitcode == 23
    assert cleaned == ["crash"]
    assert 1 + 1 == 2
