from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

import pytest

from haute._worker_isolation import (
    IsolatedWorkerConfig,
    IsolatedWorkerCrashedError,
    IsolatedWorkerRemoteError,
    IsolatedWorkerStoppedError,
    IsolatedWorkerTimeoutError,
)
from haute._worker_protocol import (
    WorkerArtifactManifest,
    WorkerFailurePayload,
    WorkerProgressEvent,
    WorkerProtocolError,
    WorkerRemoteFailureError,
    WorkerRequest,
    WorkerResultManifest,
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


def _crash_worker(runtime, request):
    del runtime, request
    os._exit(23)


def _request() -> WorkerRequest:
    return WorkerRequest("request-1", "training", {"items": [1, True, None]})


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
    object.__setattr__(forged, "payload", {})
    object.__setattr__(forged, "schema_version", True)

    with pytest.raises(WorkerProtocolError, match="schema"):
        run_worker_protocol(
            _worker_with_progress,
            forged,
            artifact_root=tmp_path / "artifacts",
            artifact_kinds=frozenset({"model"}),
            max_artifact_size_bytes=100,
        )


def test_parent_rejects_non_monotonic_progress(tmp_path: Path) -> None:
    # The private queue reader is exercised with forged queue contents; a spawn
    # worker cannot be instructed to emit a duplicate through WorkerRuntime.
    first = WorkerProgressEvent(0, 0.1, "one", "phase", {})
    duplicate = WorkerProgressEvent(0, 0.2, "two", "phase", {})
    from haute._worker_protocol import _drain_progress

    class Queue:
        def __init__(self):
            self.values = [first, duplicate]

        def get_nowait(self):
            if not self.values:
                import queue

                raise queue.Empty
            return self.values.pop(0)

    with pytest.raises(WorkerProtocolError, match="does not match"):
        _drain_progress(Queue(), 0, None)


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

        values = [event, None]

    received: list[WorkerProgressEvent] = []
    _drain_progress_until_end(Queue(), 0, received.append)

    assert received == [event]


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
