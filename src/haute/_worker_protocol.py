"""Version-one transport for supervised spawn workers.

The protocol deliberately carries only immutable, plain-data DTOs between the
parent and child.  The parent remains responsible for artifact publication.
"""

from __future__ import annotations

import hashlib
import math
import multiprocessing as mp
import pickle
import queue
import re
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal, cast

from haute._worker_isolation import (
    IsolatedWorkerConfig,
    IsolatedWorkerCrashedError,
    IsolatedWorkerError,
    IsolatedWorkerMemoryLimitUnsupportedError,
    IsolatedWorkerRemoteError,
    IsolatedWorkerStartError,
    IsolatedWorkerStoppedError,
    IsolatedWorkerTimeoutError,
    WorkerTerminalReason,
    _apply_address_space_limit,
    _run_cleanup_callbacks,
    _terminate_process,
    process_memory_caps_supported,
)

SCHEMA_VERSION = 1
WORKER_EVENT_QUEUE_CAPACITY = 64
WORKER_MAX_EVENTS = 10_000
WORKER_MAX_EVENT_BYTES = 64 * 1024
WORKER_MAX_METADATA_BYTES = 4 * 1024 * 1024
WORKER_MAX_ARTIFACTS = 64
WORKER_MAX_MESSAGE_LENGTH = 512
WORKER_MAX_IDENTIFIER_LENGTH = 512
WORKER_MAX_PATH_LENGTH = 4_096
WORKER_MAX_PLAIN_DATA_DEPTH = 64
WORKER_MAX_TRACEBACK_LENGTH = 64 * 1024
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_LIFETIMES = frozenset(("staged", "job", "durable"))
_TERMINAL_REASONS = frozenset(
    (
        "completed",
        "superseded",
        "timed_out",
        "cancelled",
        "memory_limited",
        "contract_error",
        "error",
    )
)


class WorkerProtocolError(IsolatedWorkerError):
    """Raised when a worker violates the transport contract."""

    def __init__(self, message: str) -> None:
        super().__init__(message, terminal_reason="contract_error")


class WorkerRemoteFailureError(IsolatedWorkerRemoteError):
    """A validated child failure, preserving protocol terminal details."""

    def __init__(self, payload: WorkerFailurePayload) -> None:
        super().__init__(
            remote_type=payload.error_type,
            remote_message=payload.message,
            remote_traceback=payload.traceback,
        )
        self.terminal_reason = cast(WorkerTerminalReason, payload.terminal_reason)
        self.fields = payload.fields


class WorkerProcessTerminationError(IsolatedWorkerError):
    """Raised when a terminated worker cannot be confirmed dead."""

    def __init__(self) -> None:
        super().__init__("worker process remained alive after termination", terminal_reason="error")


@dataclass(frozen=True, slots=True)
class WorkerRequest:
    request_id: str
    kind: str
    payload: Any
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_version(self.schema_version)
        _require_identifier(self.request_id, "request_id")
        _require_identifier(self.kind, "kind")
        _validate_plain_data(self.payload, "payload")
        if (
            len(pickle.dumps(self.payload, protocol=pickle.HIGHEST_PROTOCOL))
            > WORKER_MAX_METADATA_BYTES
        ):
            raise WorkerProtocolError("serialized request payload exceeds byte limit")


@dataclass(frozen=True, slots=True)
class WorkerProgressEvent:
    sequence: int
    progress: float
    message: str
    kind: str
    fields: Any
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_version(self.schema_version)
        if type(self.sequence) is not int or self.sequence < 0:
            raise WorkerProtocolError("sequence must be a non-negative integer")
        if (
            type(self.progress) not in (int, float)
            or not math.isfinite(self.progress)
            or not 0 <= self.progress <= 1
        ):
            raise WorkerProtocolError("progress must be finite and in [0, 1]")
        _require_message(self.message, "message")
        _require_identifier(self.kind, "kind")
        _validate_plain_data(self.fields, "fields")
        if len(pickle.dumps(self, protocol=pickle.HIGHEST_PROTOCOL)) > WORKER_MAX_EVENT_BYTES:
            raise WorkerProtocolError("serialized progress event exceeds byte limit")


@dataclass(frozen=True, slots=True)
class WorkerArtifactManifest:
    kind: str
    relative_path: str
    size_bytes: int
    sha256: str
    lifetime: Literal["staged", "job", "durable"]
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_version(self.schema_version)
        _require_identifier(self.kind, "kind")
        _validate_relative_path(self.relative_path)
        if type(self.size_bytes) is not int or self.size_bytes < 0:
            raise WorkerProtocolError("size_bytes must be a non-negative integer")
        if type(self.sha256) is not str or _SHA256_RE.fullmatch(self.sha256) is None:
            raise WorkerProtocolError("sha256 must be a lowercase SHA-256 hex digest")
        if type(self.lifetime) is not str or self.lifetime not in _LIFETIMES:
            raise WorkerProtocolError("artifact lifetime is unknown")


@dataclass(frozen=True, slots=True)
class WorkerResultManifest:
    metadata: Any
    artifacts: tuple[WorkerArtifactManifest, ...] = ()
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_version(self.schema_version)
        _validate_plain_data(self.metadata, "metadata")
        if (
            len(pickle.dumps(self.metadata, protocol=pickle.HIGHEST_PROTOCOL))
            > WORKER_MAX_METADATA_BYTES
        ):
            raise WorkerProtocolError("serialized metadata exceeds byte limit")
        if not isinstance(self.artifacts, tuple) or len(self.artifacts) > WORKER_MAX_ARTIFACTS:
            raise WorkerProtocolError("artifacts must be a tuple within the artifact limit")
        if not all(isinstance(artifact, WorkerArtifactManifest) for artifact in self.artifacts):
            raise WorkerProtocolError("artifacts must contain WorkerArtifactManifest values")


@dataclass(frozen=True, slots=True)
class WorkerFailurePayload:
    terminal_reason: str
    error_type: str
    message: str
    traceback: str
    fields: Any
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_version(self.schema_version)
        if type(self.terminal_reason) is not str:
            raise WorkerProtocolError("failure terminal_reason is unknown")
        if self.terminal_reason not in _TERMINAL_REASONS - {"completed"}:
            raise WorkerProtocolError("failure terminal_reason is unknown")
        _require_identifier(self.error_type, "error_type")
        _require_message(self.message, "message")
        _require_bounded_text(self.traceback, "traceback", WORKER_MAX_TRACEBACK_LENGTH)
        _validate_plain_data(self.fields, "fields")
        if len(pickle.dumps(self, protocol=pickle.HIGHEST_PROTOCOL)) > WORKER_MAX_METADATA_BYTES:
            raise WorkerProtocolError("serialized failure payload exceeds byte limit")


class WorkerRuntime:
    """Child-only capability for emitting progress and allocating staged files."""

    def __init__(self, progress_queue: Any, artifact_root: str) -> None:
        self._progress_queue = progress_queue
        self._artifact_root = Path(artifact_root).resolve()
        self._sequence = 0

    def emit_progress(
        self, *, progress: float, message: str, kind: str, fields: Any = None
    ) -> None:
        if self._sequence >= WORKER_MAX_EVENTS:
            raise WorkerProtocolError("progress event limit exceeded")
        event = WorkerProgressEvent(self._sequence, progress, message, kind, fields)
        self._progress_queue.put(event)
        self._sequence += 1

    def staged_path(self, relative_path: str) -> Path:
        _validate_relative_path(relative_path)
        path = (self._artifact_root / relative_path).resolve()
        if not path.is_relative_to(self._artifact_root):
            raise WorkerProtocolError("staged path escapes artifact root")
        path.parent.mkdir(parents=True, exist_ok=True)
        return path


WorkerFunction = Callable[
    [WorkerRuntime, WorkerRequest], WorkerResultManifest | WorkerFailurePayload
]


def run_worker_protocol(
    function: WorkerFunction,
    request: WorkerRequest,
    *,
    artifact_root: Path,
    artifact_kinds: frozenset[str],
    max_artifact_size_bytes: int,
    on_progress: Callable[[WorkerProgressEvent], None] | None = None,
    config: IsolatedWorkerConfig | None = None,
) -> WorkerResultManifest:
    """Run a protocol worker, forwarding validated progress and validated result."""
    request = WorkerRequest(
        request.request_id,
        request.kind,
        request.payload,
        request.schema_version,
    )
    if type(max_artifact_size_bytes) is not int or max_artifact_size_bytes < 0:
        raise ValueError("max_artifact_size_bytes must be non-negative")
    if any(type(kind) is not str or not kind for kind in artifact_kinds):
        raise ValueError("artifact_kinds must contain only non-empty names")
    worker_config = config or IsolatedWorkerConfig()
    if (
        worker_config.require_memory_limit
        and worker_config.memory_limit_bytes is not None
        and not process_memory_caps_supported()
    ):
        raise IsolatedWorkerMemoryLimitUnsupportedError(
            memory_limit_bytes=worker_config.memory_limit_bytes
        )
    root = artifact_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    ctx = mp.get_context("spawn")
    result_queue: Any = ctx.Queue(maxsize=1)
    progress_queue: Any = ctx.Queue(maxsize=WORKER_EVENT_QUEUE_CAPACITY)
    process = ctx.Process(
        target=_protocol_entrypoint,
        name=worker_config.process_name,
        args=(
            result_queue,
            progress_queue,
            function,
            request,
            str(root),
            worker_config.memory_limit_bytes,
        ),
    )
    primary_error: BaseException | None = None
    result: WorkerResultManifest | None = None
    queued_result: tuple[Any, Any] | None = None
    expected_sequence = 0
    progress_ended = False
    deadline = (
        None
        if worker_config.timeout_seconds is None
        else time.monotonic() + worker_config.timeout_seconds
    )
    try:
        try:
            process.start()
        except Exception as exc:  # pragma: no cover - multiprocessing dependent
            raise IsolatedWorkerStartError(f"Failed to start isolated worker: {exc}") from exc
        while process.is_alive():
            if not progress_ended:
                expected_sequence, progress_ended = _drain_progress(
                    progress_queue,
                    expected_sequence,
                    on_progress,
                )
            if queued_result is None:
                try:
                    queued_result = _validate_result_envelope(result_queue.get_nowait())
                except queue.Empty:
                    pass
            if (
                worker_config.stop_reason is not None
                and (reason := worker_config.stop_reason()) is not None
            ):
                _terminate_and_confirm(process)
                raise IsolatedWorkerStoppedError(terminal_reason=reason)
            if deadline is not None and time.monotonic() >= deadline:
                _terminate_and_confirm(process)
                raise IsolatedWorkerTimeoutError(
                    timeout_seconds=cast(float, worker_config.timeout_seconds)
                )
            process.join(timeout=worker_config.stop_poll_interval_seconds)
        if process.exitcode not in (0, None):
            raise IsolatedWorkerCrashedError(
                exitcode=process.exitcode,
                memory_limit_bytes=worker_config.memory_limit_bytes,
            )
        if not progress_ended:
            _drain_progress_until_end(progress_queue, expected_sequence, on_progress)
        if queued_result is None:
            try:
                queued_result = _validate_result_envelope(result_queue.get(timeout=1.0))
            except queue.Empty as exc:
                raise IsolatedWorkerCrashedError(
                    exitcode=process.exitcode,
                    memory_limit_bytes=worker_config.memory_limit_bytes,
                ) from exc
        status, payload = queued_result
        if status == "ok":
            if not isinstance(payload, WorkerResultManifest):
                raise WorkerProtocolError("worker returned a non-manifest result")
            validate_result_manifest(
                payload,
                artifact_root=root,
                artifact_kinds=artifact_kinds,
                max_artifact_size_bytes=max_artifact_size_bytes,
            )
            result = payload
        elif status == "error":
            if not isinstance(payload, WorkerFailurePayload):
                raise WorkerProtocolError("worker returned a non-failure error payload")
            failure = WorkerFailurePayload(
                payload.terminal_reason,
                payload.error_type,
                payload.message,
                payload.traceback,
                payload.fields,
                payload.schema_version,
            )
            raise WorkerRemoteFailureError(failure)
        else:
            raise WorkerProtocolError(f"worker returned unknown result status {status!r}")
    except BaseException as exc:
        primary_error = exc
    finally:
        if process.is_alive():
            try:
                _terminate_and_confirm(process)
            except BaseException as exc:
                if primary_error is None:
                    primary_error = exc
                else:
                    primary_error.add_note(f"process cleanup failed: {exc}")
        try:
            process.join(timeout=2.0)
            progress_queue.close()
            progress_queue.join_thread()
            result_queue.close()
            result_queue.join_thread()
        except Exception:
            pass
    cleanup_error = _run_cleanup_callbacks(worker_config.cleanup_callbacks)
    if primary_error is not None:
        if cleanup_error is not None:
            primary_error.add_note(str(cleanup_error))
        raise primary_error
    if cleanup_error is not None:
        raise cleanup_error
    if result is None:
        raise IsolatedWorkerCrashedError(
            exitcode=process.exitcode, memory_limit_bytes=worker_config.memory_limit_bytes
        )
    return result


def validate_result_manifest(
    manifest: WorkerResultManifest,
    *,
    artifact_root: Path,
    artifact_kinds: frozenset[str],
    max_artifact_size_bytes: int,
) -> None:
    """Validate artifact containment and integrity before parent publication."""
    # Reconstructing validates fields even if a hostile child bypassed __post_init__.
    WorkerResultManifest(manifest.metadata, manifest.artifacts, manifest.schema_version)
    root = artifact_root.resolve()
    for artifact in manifest.artifacts:
        WorkerArtifactManifest(
            artifact.kind,
            artifact.relative_path,
            artifact.size_bytes,
            artifact.sha256,
            artifact.lifetime,
            artifact.schema_version,
        )
        if artifact.kind not in artifact_kinds:
            raise WorkerProtocolError(f"unknown artifact kind: {artifact.kind}")
        if artifact.size_bytes > max_artifact_size_bytes:
            raise WorkerProtocolError("artifact exceeds declared maximum size")
        path = root / artifact.relative_path
        resolved = path.resolve()
        if not resolved.is_relative_to(root) or path.is_symlink() or not resolved.is_file():
            raise WorkerProtocolError("artifact is not a contained regular file")
        if resolved.stat().st_size != artifact.size_bytes:
            raise WorkerProtocolError("artifact size does not match manifest")
        if _sha256_file(resolved) != artifact.sha256:
            raise WorkerProtocolError("artifact digest does not match manifest")


def build_artifact_manifest(
    *,
    artifact_root: Path,
    path: Path,
    kind: str,
    lifetime: Literal["staged", "job", "durable"],
) -> WorkerArtifactManifest:
    """Build an integrity manifest for a contained, regular staged artifact."""
    root = artifact_root.resolve()
    resolved = path.resolve()
    if path.is_symlink() or not resolved.is_relative_to(root) or not resolved.is_file():
        raise WorkerProtocolError("artifact is not a contained regular file")
    relative_path = resolved.relative_to(root).as_posix()
    return WorkerArtifactManifest(
        kind=kind,
        relative_path=relative_path,
        size_bytes=resolved.stat().st_size,
        sha256=_sha256_file(resolved),
        lifetime=lifetime,
    )


def _protocol_entrypoint(
    result_queue: Any,
    progress_queue: Any,
    function: WorkerFunction,
    request: WorkerRequest,
    artifact_root: str,
    memory_limit_bytes: int | None,
) -> None:
    try:
        if memory_limit_bytes is not None and process_memory_caps_supported():
            _apply_address_space_limit(memory_limit_bytes)
        result = function(WorkerRuntime(progress_queue, artifact_root), request)
        if isinstance(result, WorkerFailurePayload):
            result_queue.put(("error", result))
            return
        if not isinstance(result, WorkerResultManifest):
            raise WorkerProtocolError("worker function must return WorkerResultManifest")
        result_queue.put(("ok", result))
    except BaseException as exc:
        result_queue.put(
            (
                "error",
                WorkerFailurePayload(
                    terminal_reason=_terminal_reason_for_exception(exc),
                    error_type=type(exc).__name__,
                    message=str(exc)[:WORKER_MAX_MESSAGE_LENGTH] or type(exc).__name__,
                    traceback="".join(
                        traceback.format_exception(type(exc), exc, exc.__traceback__)
                    )[:WORKER_MAX_TRACEBACK_LENGTH],
                    fields={},
                ),
            )
        )
    finally:
        progress_queue.put(None)


def _drain_progress(
    progress_queue: Any, expected: int, callback: Callable[[WorkerProgressEvent], None] | None
) -> tuple[int, bool]:
    while True:
        try:
            event = progress_queue.get_nowait()
        except queue.Empty:
            return expected, False
        if event is None:
            return expected, True
        if not isinstance(event, WorkerProgressEvent):
            raise WorkerProtocolError("progress queue contains a non-event payload")
        WorkerProgressEvent(
            event.sequence,
            event.progress,
            event.message,
            event.kind,
            event.fields,
            event.schema_version,
        )
        if event.sequence != expected:
            raise WorkerProtocolError(
                f"progress sequence {event.sequence} does not match expected {expected}"
            )
        if expected >= WORKER_MAX_EVENTS:
            raise WorkerProtocolError("progress event limit exceeded")
        if callback is not None:
            callback(event)
        expected += 1


def _drain_progress_until_end(
    progress_queue: Any,
    expected: int,
    callback: Callable[[WorkerProgressEvent], None] | None,
) -> None:
    """Consume the child end marker, including feeder-thread-delayed events."""
    while True:
        expected, ended = _drain_progress(progress_queue, expected, callback)
        if ended:
            return
        try:
            event = progress_queue.get(timeout=1.0)
        except queue.Empty as exc:
            raise WorkerProtocolError("worker progress stream ended without an end marker") from exc
        if event is None:
            return
        if not isinstance(event, WorkerProgressEvent):
            raise WorkerProtocolError("progress queue contains a non-event payload")
        WorkerProgressEvent(
            event.sequence,
            event.progress,
            event.message,
            event.kind,
            event.fields,
            event.schema_version,
        )
        if event.sequence != expected:
            raise WorkerProtocolError(
                f"progress sequence {event.sequence} does not match expected {expected}"
            )
        if expected >= WORKER_MAX_EVENTS:
            raise WorkerProtocolError("progress event limit exceeded")
        if callback is not None:
            callback(event)
        expected += 1


def _validate_plain_data(value: Any, name: str, *, depth: int = 0) -> None:
    if depth > WORKER_MAX_PLAIN_DATA_DEPTH:
        raise WorkerProtocolError(f"{name} exceeds plain-data nesting depth")
    if value is None or type(value) in (str, bool):
        return
    if type(value) in (int, float):
        if type(value) is float and not math.isfinite(value):
            raise WorkerProtocolError(f"{name} contains a non-finite number")
        return
    if type(value) in (list, tuple):
        for item in value:
            _validate_plain_data(item, name, depth=depth + 1)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise WorkerProtocolError(f"{name} mapping keys must be strings")
            _validate_plain_data(item, name, depth=depth + 1)
        return
    raise WorkerProtocolError(f"{name} is not plain data")


def _require_version(version: int) -> None:
    if type(version) is not int or version != SCHEMA_VERSION:
        raise WorkerProtocolError("unsupported schema version")


def _require_nonempty(value: str, name: str) -> None:
    if type(value) is not str or not value:
        raise WorkerProtocolError(f"{name} must be a non-empty string")


def _require_message(value: str, name: str) -> None:
    _require_bounded_text(value, name, WORKER_MAX_MESSAGE_LENGTH)


def _require_identifier(value: str, name: str) -> None:
    _require_bounded_text(value, name, WORKER_MAX_IDENTIFIER_LENGTH)


def _require_bounded_text(value: str, name: str, limit: int) -> None:
    _require_nonempty(value, name)
    if len(value) > limit:
        raise WorkerProtocolError(f"{name} exceeds length limit")


def _validate_relative_path(value: str) -> None:
    _require_bounded_text(value, "relative_path", WORKER_MAX_PATH_LENGTH)
    path = PurePosixPath(value)
    windows_path = PureWindowsPath(value)
    if (
        path.is_absolute()
        or windows_path.is_absolute()
        or bool(windows_path.drive)
        or "\\" in value
        or "\0" in value
        or any(part in (".", "..") for part in path.parts)
        or str(path) != value
    ):
        raise WorkerProtocolError("relative_path must be normalized and relative")


def _validate_result_envelope(value: Any) -> tuple[Any, Any]:
    if type(value) is not tuple or len(value) != 2:
        raise WorkerProtocolError("worker result queue contains a malformed envelope")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _terminal_reason_for_exception(exc: BaseException) -> str:
    if isinstance(exc, IsolatedWorkerError):
        return exc.terminal_reason
    if isinstance(exc, MemoryError):
        return "memory_limited"
    return "error"


def _terminate_and_confirm(process: Any) -> None:
    _terminate_process(process)
    if process.is_alive():
        raise WorkerProcessTerminationError()
