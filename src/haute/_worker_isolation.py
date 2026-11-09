"""Small process-isolation primitive for heavy execution workers."""

from __future__ import annotations

import multiprocessing as mp
import os
import pickle
import queue
import sys
import threading
import time
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from multiprocessing.process import BaseProcess
from typing import Any, Literal, TypeVar, cast

from haute._cpu_performance import configure_process_high_qos
from haute._file_ops import path_too_long_error
from haute._logging import get_logger
from haute._native_memory_limit import (
    NativeMemoryLease,
    cleanup_private_cgroups_for_pid,
    memory_error_for_thread_start_failure,
    native_memory_backend_scope,
    native_memory_caps_supported,
    start_polars_thread_pools,
)
from haute._parent_watch import PARENT_PID_ENV, exit_with_parent
from haute._polars_environment import release_polars_spill_dir
from haute._process_memory import (
    current_process_rss_bytes,
    current_process_thread_count,
    current_process_virtual_bytes,
    process_rss_bytes,
)
from haute._project_context import ProjectContext, current_project, project_scope
from haute._worker_failures import (
    WorkerCrashedError,
    WorkerHostError,
    WorkerMemoryLimitError,
    WorkerMemoryLimitUnsupportedError,
    WorkerRemoteError,
    WorkerStartError,
    WorkerStoppedError,
    WorkerTerminalReason,
    WorkerTerminationError,
    WorkerTimeoutError,
    exitcode_looks_memory_limited,
)

logger = get_logger(component="worker_isolation")

T = TypeVar("T")

WorkerMemoryEnforcement = Literal["best_effort", "required"]
_WORKER_MEMORY_ENFORCEMENT_ENV = "HAUTE_WORKER_MEMORY_ENFORCEMENT"


#: How long a spawned one-shot or protocol worker may take, from its start, to import what
#: it runs, start its Polars thread pools and attempt its native cap, before it reports
#: ready. The heaviest supported set (Polars with CatBoost, LightGBM, XGBoost and the
#: training worker) took 3.8 s on a development host; cold disks and hosted runtimes are
#: slower, so the deadline leaves a wide margin and only catches a child that hangs.
WORKER_STARTUP_TIMEOUT_SECONDS = 120.0
#: The resident memory such a worker may reach before it reports ready. That same set held
#: 212 MiB; a gibibyte is about five times it, and stops a startup that runs away.
WORKER_STARTUP_RSS_CEILING_BYTES = 1024 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class IsolatedWorkerConfig:
    """Runtime controls for one isolated worker process."""

    timeout_seconds: float | None = None
    memory_limit_bytes: int | None = None
    require_memory_limit: bool = False
    stop_reason: Callable[[], WorkerTerminalReason | None] | None = None
    stop_poll_interval_seconds: float = 0.1
    process_name: str = "haute-isolated-worker"
    # Widens only an RLIMIT_AS cap, for a model library's thread reservations.
    address_space_allowance_bytes: int = 0
    # Modules the protocol worker imports before its cap, so their mapped code and
    # data count in the baseline rather than the job's budget.
    preload_modules: tuple[str, ...] = ()
    # Variables set for the child at spawn only (for example a library's
    # import-time thread pool size); the parent's environment is unchanged.
    environment: Mapping[str, str] = field(default_factory=dict)
    # The supervision of a one-shot or protocol worker until it reports ready, apart
    # from the job's own growth allowance (``memory_limit_bytes``) after it.
    startup_timeout_seconds: float = WORKER_STARTUP_TIMEOUT_SECONDS
    startup_rss_ceiling_bytes: int = WORKER_STARTUP_RSS_CEILING_BYTES

    def __post_init__(self) -> None:
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.startup_timeout_seconds <= 0:
            raise ValueError("startup_timeout_seconds must be positive")
        if self.startup_rss_ceiling_bytes <= 0:
            raise ValueError("startup_rss_ceiling_bytes must be positive")
        if self.memory_limit_bytes is not None and self.memory_limit_bytes <= 0:
            raise ValueError("memory_limit_bytes must be positive")
        if self.require_memory_limit and self.memory_limit_bytes is None:
            raise ValueError("required memory enforcement needs a configured memory limit")
        if self.stop_poll_interval_seconds <= 0:
            raise ValueError("stop_poll_interval_seconds must be positive")
        if self.address_space_allowance_bytes < 0:
            raise ValueError("address_space_allowance_bytes must not be negative")


_supervision_noted = threading.Event()


def resolve_worker_memory_enforcement() -> WorkerMemoryEnforcement:
    """Return the memory policy for isolated workers.

    ``HAUTE_WORKER_MEMORY_ENFORCEMENT`` decides when it is set. Unset, a host
    that can install a native cap requires one, and a host that cannot (macOS)
    supervises its workers instead: each still runs in its own process, which
    the parent's RSS watchdog stops past its limit, where requiring the cap
    would refuse every worker. That choice is logged once per process.
    """
    raw = os.environ.get(_WORKER_MEMORY_ENFORCEMENT_ENV)
    if raw is None:
        if process_memory_caps_supported():
            return "required"
        if not _supervision_noted.is_set():
            _supervision_noted.set()
            logger.warning(
                "worker_memory_supervised_without_cap",
                enforcement="best_effort",
                reason="this host cannot install a native per-process memory cap",
            )
        return "best_effort"
    policy = raw.strip().lower()
    if policy not in {"best_effort", "required"}:
        raise RuntimeError(f"{_WORKER_MEMORY_ENFORCEMENT_ENV} must be 'best_effort' or 'required'")
    return cast(WorkerMemoryEnforcement, policy)


def worker_config_for_memory_policy(
    *,
    memory_limit_bytes: int | None,
    timeout_seconds: float | None = None,
    stop_reason: Callable[[], WorkerTerminalReason | None] | None = None,
    stop_poll_interval_seconds: float = 0.1,
    process_name: str = "haute-isolated-worker",
    address_space_allowance_bytes: int = 0,
    preload_modules: tuple[str, ...] = (),
    environment: Mapping[str, str] | None = None,
) -> IsolatedWorkerConfig:
    """Build worker controls without implying a hard cap on unsupported hosts."""
    enforcement = resolve_worker_memory_enforcement()
    if enforcement == "required" and memory_limit_bytes is None:
        raise RuntimeError(
            f"{_WORKER_MEMORY_ENFORCEMENT_ENV}='required' requires a configured memory limit"
        )
    return IsolatedWorkerConfig(
        timeout_seconds=timeout_seconds,
        memory_limit_bytes=memory_limit_bytes,
        require_memory_limit=enforcement == "required",
        stop_reason=stop_reason,
        stop_poll_interval_seconds=stop_poll_interval_seconds,
        process_name=process_name,
        address_space_allowance_bytes=address_space_allowance_bytes,
        preload_modules=preload_modules,
        environment=dict(environment or {}),
    )


def process_memory_caps_supported() -> bool:
    """Return whether a native kernel hard cap is theoretically available."""
    return native_memory_caps_supported()


@dataclass(frozen=True, slots=True)
class _WorkerRssWatchdog:
    pid: int | None
    rss_limit_bytes: int | None
    require_memory_limit: bool
    address_space_cap_active: bool

    def checkpoint(self) -> None:
        if self.rss_limit_bytes is None:
            return
        if self.pid is None:  # pragma: no cover - constructor invariant
            raise RuntimeError("RSS watchdog has a limit without a process id")
        rss_bytes = process_rss_bytes(self.pid)
        if rss_bytes is None:
            if self.require_memory_limit and not self.address_space_cap_active:
                raise WorkerMemoryLimitUnsupportedError(memory_limit_bytes=self.rss_limit_bytes)
            return
        if rss_bytes > self.rss_limit_bytes:
            raise WorkerMemoryLimitError(
                rss_bytes=rss_bytes,
                rss_limit_bytes=self.rss_limit_bytes,
            )


WorkerStartup = Literal["ready", "result", "exited"]


def await_worker_ready(
    process: BaseProcess,
    result_queue: Any,
    *,
    is_ready: Callable[[Any], bool],
    config: IsolatedWorkerConfig,
    deadline: float | None,
) -> tuple[WorkerStartup, Any]:
    """Supervise a spawned worker until it reports ready (parent side).

    Until then the child only imports what it runs, starts its thread pools and
    attempts its native cap, so its resident memory is held under the startup
    ceiling and the wait ends at the startup deadline, the job's *deadline* when
    that is earlier, or its stop reason. Returns ``("ready", None)``; the first
    message when it is not the ready one (a failure the child reported before it
    was ready) as ``("result", message)``; or ``("exited", None)`` for a child
    that ended without a word, which the caller classifies as it would after the job.
    """
    pid = process.pid
    if pid is None:
        raise WorkerStartError(
            "Isolated worker did not expose a process id after startup",
            terminal_reason="error",
        )
    startup_deadline = time.monotonic() + config.startup_timeout_seconds
    ends_at = startup_deadline if deadline is None else min(deadline, startup_deadline)
    while True:
        try:
            message = result_queue.get(timeout=config.stop_poll_interval_seconds)
        except queue.Empty:
            message = None
        if message is not None and not is_ready(message):
            return "result", message
        if message is None and not process.is_alive():
            return "exited", None
        # Checked on the ready report too: the footprint it reports at becomes the
        # job's baseline, so one past the ceiling must not be accepted with it.
        rss_bytes = process_rss_bytes(pid)
        if rss_bytes is not None and rss_bytes > config.startup_rss_ceiling_bytes:
            _terminate_process(process)
            exceeded = WorkerMemoryLimitError(
                rss_bytes=rss_bytes, rss_limit_bytes=config.startup_rss_ceiling_bytes
            )
            exceeded.add_note("The worker exceeded its startup ceiling before it reported ready.")
            raise exceeded
        if message is not None:
            return "ready", None
        if config.stop_reason is not None and (reason := config.stop_reason()) is not None:
            _terminate_process(process)
            raise WorkerStoppedError(terminal_reason=reason)
        if time.monotonic() >= ends_at:
            _terminate_process(process)
            if deadline is not None and deadline <= startup_deadline:
                raise WorkerTimeoutError(timeout_seconds=cast(float, config.timeout_seconds))
            raise WorkerStartError(
                "Isolated worker did not report ready within "
                f"{config.startup_timeout_seconds:g} seconds",
                terminal_reason="error",
            )


def prepare_worker_for_job(memory_limit_bytes: int | None) -> None:
    """What a worker does before it reports ready (child side): start its Polars
    thread pools, so their threads and arenas are in the baseline its parent samples
    and its native cap measures, not in the job's growth."""
    if memory_limit_bytes is not None:
        start_polars_thread_pools()


def report_worker_ready(result_queue: Any, message: Any, go: Any) -> None:
    """Tell the parent this worker is ready, then wait until it has armed its watchdog
    (child side). The job runs only after, so no allocation of it is in the baseline."""
    result_queue.put(message)
    go.wait()


def _create_worker_rss_watchdog(
    process: BaseProcess,
    *,
    memory_limit_bytes: int | None,
    require_memory_limit: bool,
) -> _WorkerRssWatchdog:
    """Resolve a per-request RSS growth cap from a spawned child that reported ready.

    The baseline is the child's resident memory once it is ready: its imports,
    thread pools and native cap, but none of its job.
    """
    if memory_limit_bytes is None:
        return _WorkerRssWatchdog(None, None, require_memory_limit, False)
    pid = process.pid
    if pid is None:
        raise WorkerStartError(
            "Isolated worker did not expose a process id after startup",
            terminal_reason="error",
        )
    address_space_cap_active = process_memory_caps_supported()
    baseline_rss_bytes = process_rss_bytes(pid)
    if baseline_rss_bytes is None:
        if require_memory_limit and not address_space_cap_active:
            raise WorkerMemoryLimitUnsupportedError(memory_limit_bytes=memory_limit_bytes)
        return _WorkerRssWatchdog(
            pid,
            None,
            require_memory_limit,
            address_space_cap_active,
        )
    return _WorkerRssWatchdog(
        pid,
        baseline_rss_bytes + memory_limit_bytes,
        require_memory_limit,
        address_space_cap_active,
    )


# ---------------------------------------------------------------------------
# Host process machinery
# ---------------------------------------------------------------------------
#
# Before any worker can start, CPython creates a multiprocessing queue, which
# creates a semaphore, which must be registered with the `resource_tracker` —
# a helper process spawned on first use whose job is to clean up leaked
# semaphores. Writing to its pipe raises BrokenPipeError once that helper is
# dead. `ensure_running()` re-spawns a tracker it has already probed and found
# dead, but it does NOT probe one it has just spawned, so a tracker that dies
# immediately on spawn surfaces as a broken pipe on the very next write.
#
# Measured on Databricks Apps (5 August 2026): three occurrences, each on the
# first queue of a training job, while ordinary in-process execution kept
# working. The diagnostics below exist to answer WHY the tracker died there —
# the recovery is bounded and loud precisely so a silent retry cannot hide it.

#: Errors that mean the tracker's pipe is gone rather than the job being bad.
_DEAD_TRACKER_ERRORS = (BrokenPipeError, ConnectionResetError)


#: Set once ``ensure_spawnable_interpreter`` has had its say.
_interpreter_checked = False


def ensure_spawnable_interpreter() -> str | None:
    """Pin multiprocessing's interpreter to an absolute, runnable path.

    Databricks Apps launches the app with a RELATIVE ``sys.executable``
    (measured: ``.venv/bin/python``). multiprocessing spawns every helper
    — the resource tracker, and each worker — by exec'ing that path, so
    the moment anything changes the working directory those spawns
    exec-fail with status 255. The first symptom is a broken pipe when
    the resource tracker is registered, which reads as an unrelated
    internal error; the real cause is a path that stopped resolving.

    Resolving it once removes the dependency on the working directory
    entirely. Called before the hosted boot changes directory (where a
    relative path still resolves), and again as a safety net at first
    worker use, where ``/proc/self/exe`` names the running interpreter
    directly for a cwd that has already moved.

    Returns the path now in force, or ``None`` when nothing was changed.
    """
    global _interpreter_checked

    from multiprocessing import spawn

    _interpreter_checked = True
    current = spawn.get_executable()
    if isinstance(current, bytes):
        current = os.fsdecode(current)
    if current and os.path.isabs(current) and os.access(current, os.X_OK):
        return None  # Already absolute and runnable — the ordinary case.

    candidate = os.path.abspath(current) if current else ""
    if not (candidate and os.access(candidate, os.X_OK)):
        # A relative path cannot be resolved once the working directory has
        # moved. The kernel still knows which binary is running.
        try:
            candidate = os.readlink("/proc/self/exe")
        except OSError:  # pragma: no cover - non-Linux hosts
            logger.warning("worker_interpreter_unresolved", executable=current)
            return None
    if not os.access(candidate, os.X_OK):  # pragma: no cover - defensive
        logger.warning("worker_interpreter_unresolved", executable=current)
        return None

    spawn.set_executable(candidate)
    logger.info("worker_interpreter_resolved", was=current, now=candidate)
    return candidate


def _resource_tracker_diagnostics() -> dict[str, Any]:
    """Facts about the host that explain a dead resource tracker.

    Every probe is individually guarded: this runs on a failure path and
    must never replace the original error with one of its own.
    """
    info: dict[str, Any] = {}
    try:
        from multiprocessing import resource_tracker

        tracker = resource_tracker._resource_tracker
        pid = getattr(tracker, "_pid", None)
        info["tracker_pid"] = pid
        info["tracker_fd"] = getattr(tracker, "_fd", None)
        if pid is not None:
            try:
                # Reaps the tracker if it has exited, which is what
                # ensure_running would do anyway, and reports how it died.
                reaped, status = os.waitpid(pid, int(getattr(os, "WNOHANG", 1)))
                info["tracker_reaped"] = reaped
                info["tracker_exit_status"] = status
            except OSError as exc:
                info["tracker_waitpid_error"] = str(exc)
    except Exception as exc:  # pragma: no cover - private-API shape guard
        info["tracker_probe_error"] = str(exc)

    # A tracker is spawned as `sys.executable -c ...`; an interpreter that has
    # gone missing (a replaced virtualenv, say) would exec-fail instantly.
    info["executable"] = sys.executable
    try:
        info["executable_usable"] = bool(sys.executable) and os.access(sys.executable, os.X_OK)
    except OSError:  # pragma: no cover - defensive
        info["executable_usable"] = False

    try:
        import resource as _resource

        resource_module = cast(Any, _resource)
        for name in ("RLIMIT_NPROC", "RLIMIT_NOFILE", "RLIMIT_AS"):
            limit = getattr(resource_module, name, None)
            if limit is not None:
                info[name.lower()] = resource_module.getrlimit(limit)
    except Exception:  # pragma: no cover - POSIX-only, best effort
        pass

    info["process_threads"] = current_process_thread_count()
    info["process_rss_bytes"] = current_process_rss_bytes()
    info["process_virtual_bytes"] = current_process_virtual_bytes()
    return info


def _reset_resource_tracker() -> None:
    """Forget a dead tracker so the next use spawns a replacement.

    ``ensure_running`` only re-spawns when its own probe fails; clearing
    the handle makes the next call take the spawn path unconditionally.
    """
    from multiprocessing import resource_tracker

    # Private CPython state by necessity: the stdlib exposes no supported way
    # to say "this tracker is gone, start another". Every attribute is read
    # defensively so a future layout change degrades to no recovery rather
    # than to a new crash on the failure path.
    tracker: Any = resource_tracker._resource_tracker
    lock = getattr(tracker, "_lock", None)
    if lock is None:  # pragma: no cover - shape guard for a future CPython
        logger.warning(
            "resource_tracker_reset_unsupported",
            missing_attribute="_lock",
            python=sys.version.split()[0],
            msg="dead resource tracker cannot be reset on this interpreter; no recovery",
        )
        return
    with lock:
        fd = getattr(tracker, "_fd", None)
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        tracker._fd = None
        tracker._pid = None


def start_worker_queue_feeder(worker_queue: Any) -> None:
    """Start a multiprocessing queue's feeder thread now.

    ``Queue.put`` starts it on first use. Under a native cap that first use can come
    after the cap has left no room for a thread, so a worker starts its reporting
    threads before installing the cap. An in-process queue has no feeder.
    """
    start = getattr(worker_queue, "_start_thread", None)
    if callable(start) and getattr(worker_queue, "_thread", None) is None:
        start()


def create_worker_queue(ctx: Any, maxsize: int) -> Any:
    """Create a worker queue, surviving one dead host resource tracker.

    The retry is deliberately bounded at one attempt and logs both times:
    a dead tracker is a host fault worth seeing, not something to paper
    over. If the replacement tracker also fails, the failure is raised as
    :class:`WorkerHostError` so the user is told the host cannot
    start workers rather than being handed an unexplained internal error.
    """
    if not _interpreter_checked:
        # Cheap, once per process, and it removes by far the likeliest
        # cause of the failure handled below.
        ensure_spawnable_interpreter()
    try:
        return ctx.Queue(maxsize=maxsize)
    except _DEAD_TRACKER_ERRORS as exc:
        logger.warning(
            "worker_resource_tracker_dead",
            error=str(exc),
            error_type=type(exc).__name__,
            **_resource_tracker_diagnostics(),
        )
        _reset_resource_tracker()
        try:
            return ctx.Queue(maxsize=maxsize)
        except _DEAD_TRACKER_ERRORS as retry_exc:
            logger.error(
                "worker_resource_tracker_unrecoverable",
                error=str(retry_exc),
                error_type=type(retry_exc).__name__,
                **_resource_tracker_diagnostics(),
            )
            raise WorkerHostError(
                "This server cannot start background work at the moment — the process "
                "tracker it relies on has stopped. Restart the app and try again."
            ) from retry_exc


#: Serialises the parent-side environment overrides a spawned child inherits.
#: The overrides are necessarily process-wide while ``start()`` runs, so two
#: concurrent spawns must not be able to observe each other's values.
_SPAWN_ENVIRONMENT_LOCK = threading.Lock()


def start_process_with_environment(process: BaseProcess, environment: Mapping[str, str]) -> None:
    """Start ``process`` with ``environment`` added to the variables it inherits.

    A spawned child inherits the parent's ``os.environ`` exactly as it stands
    while ``start()`` runs, and that is the only window in which a variable a
    library reads at import time can be set for that child. The overrides are
    therefore applied to the parent globally, under a module-wide lock so no
    other spawn can inherit them, and every touched variable is restored to its
    prior value before the lock is released — including when ``start()`` raises.
    Callers with nothing to override pass an empty mapping so that every spawn
    takes the same serialised path. A streaming chunk-size cap active in this
    process stays in force here; the child is handed the setting it caps and
    applies it at entry (``apply_spawned_streaming_chunk_size``).
    """
    from haute._polars_utils import streaming_chunk_size_for_spawn

    with _SPAWN_ENVIRONMENT_LOCK, streaming_chunk_size_for_spawn() as chunk_environment:
        # Every worker learns its server's pid, so it can end when the server
        # does (see ``haute._parent_watch``).
        environment = {
            **chunk_environment,
            **environment,
            PARENT_PID_ENV: str(os.getpid()),
        }
        previous: dict[str, str | None] = {name: os.environ.get(name) for name in environment}
        try:
            os.environ.update(environment)
            process.start()
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


def run_isolated_worker(
    function: Callable[..., T],
    *args: Any,
    config: IsolatedWorkerConfig | None = None,
    **kwargs: Any,
) -> T:
    """Run ``function`` in a child process and return its pickled result.

    The child process is deliberately started via the ``spawn`` context so the
    worker does not inherit the host interpreter's large native heaps.
    """
    worker_config = config or IsolatedWorkerConfig()
    if (
        worker_config.require_memory_limit
        and worker_config.memory_limit_bytes is not None
        and not process_memory_caps_supported()
    ):
        raise WorkerMemoryLimitUnsupportedError(
            memory_limit_bytes=worker_config.memory_limit_bytes,
        )

    ctx = mp.get_context("spawn")
    result_queue: mp.Queue[bytes] = create_worker_queue(ctx, 1)
    go = ctx.Event()
    process = ctx.Process(
        target=_isolated_worker_entrypoint,
        name=worker_config.process_name,
        args=(
            result_queue,
            go,
            function,
            args,
            kwargs,
            worker_config.memory_limit_bytes,
            worker_config.require_memory_limit,
            # The caller's project, which the child installs for the function.
            current_project(),
        ),
    )
    primary_error: BaseException | None = None
    result: T | None = None
    has_result = False
    queued_payload: tuple[str, Any] | None = None
    process_started = False
    try:
        try:
            start_process_with_environment(process, worker_config.environment)
            process_started = True
        except Exception as exc:  # pragma: no cover - depends on multiprocessing internals
            raise WorkerStartError(
                f"Failed to start isolated worker: {exc}",
            ) from exc

        deadline = (
            None
            if worker_config.timeout_seconds is None
            else time.monotonic() + worker_config.timeout_seconds
        )
        startup, early = await_worker_ready(
            process,
            result_queue,
            is_ready=lambda raw: _is_ready_payload(process, raw),
            config=worker_config,
            deadline=deadline,
        )
        rss_watchdog = _WorkerRssWatchdog(None, None, worker_config.require_memory_limit, False)
        if startup == "ready":
            rss_watchdog = _create_worker_rss_watchdog(
                process,
                memory_limit_bytes=worker_config.memory_limit_bytes,
                require_memory_limit=worker_config.require_memory_limit,
            )
            go.set()
        queued_payload = _wait_for_worker(
            process,
            worker_config,
            result_queue,
            rss_watchdog,
            deadline=deadline,
            queued_payload=None if early is None else _decode_worker_payload(early),
        )

        # A feeder flush failure can leave a complete-looking payload in the
        # pipe before the child exits non-zero.  The child explicitly closes
        # and joins that feeder under its cap, so its exit status is the only
        # trustworthy confirmation that transport completed.
        if queued_payload is not None and process.exitcode not in {None, 0}:
            raise WorkerCrashedError(
                process.exitcode,
                memory_limited=exitcode_looks_memory_limited(
                    process.exitcode, worker_config.memory_limit_bytes
                ),
            )

        status, payload = (
            queued_payload
            if queued_payload is not None
            else _read_worker_payload(
                result_queue,
                exitcode=process.exitcode,
                memory_limit_bytes=worker_config.memory_limit_bytes,
            )
        )
        if status == "ok":
            result = cast(T, payload)
            has_result = True
        elif status == "error":
            remote_type, remote_message, remote_traceback, is_memory = cast(
                tuple[str, str, str, bool],
                payload,
            )
            raise WorkerRemoteError(
                remote_type=remote_type,
                remote_message=remote_message,
                remote_traceback=remote_traceback,
                is_memory=is_memory,
            )
        else:  # pragma: no cover - guarded by child entrypoint protocol
            raise WorkerCrashedError(
                process.exitcode,
                memory_limited=exitcode_looks_memory_limited(
                    process.exitcode, worker_config.memory_limit_bytes
                ),
            )
    except BaseException as exc:
        primary_error = exc
    finally:

        def record_finalization_error(exc: BaseException, *, step: str) -> None:
            nonlocal primary_error
            primary_error = note_teardown_failure(primary_error, exc, step=step)

        process_alive = False
        if process_started:
            try:
                process_alive = process.is_alive()
            except BaseException as exc:
                record_finalization_error(exc, step="process liveness check")
                process_alive = True
        if process_alive:
            try:
                _terminate_process(process)
            except BaseException as exc:
                record_finalization_error(exc, step="process termination")
        if process_started:
            try:
                process.join(timeout=2.0)
            except BaseException as exc:
                record_finalization_error(exc, step="process join")
            try:
                pid = getattr(process, "pid", None)
                if pid is not None and not process.is_alive():
                    cleanup_private_cgroups_for_pid(pid)
                    release_polars_spill_dir(pid)
            except BaseException as exc:
                record_finalization_error(exc, step="native memory resource cleanup")
        try:
            result_queue.close()
        except BaseException as exc:
            record_finalization_error(exc, step="result queue close")
        try:
            result_queue.join_thread()
        except BaseException as exc:
            record_finalization_error(exc, step="result queue feeder join")

    if primary_error is not None:
        raise primary_error
    if has_result:
        return cast(T, result)
    raise WorkerCrashedError(
        process.exitcode,
        memory_limited=exitcode_looks_memory_limited(
            process.exitcode, worker_config.memory_limit_bytes
        ),
    )


def _isolated_worker_entrypoint(
    result_queue: mp.Queue[bytes],
    go: Any,
    function: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    memory_limit_bytes: int | None,
    require_memory_limit: bool,
    project: ProjectContext,
) -> None:
    from haute._polars_utils import apply_spawned_streaming_chunk_size

    exit_with_parent()
    apply_spawned_streaming_chunk_size()
    lease = NativeMemoryLease()
    applied = False
    try:
        configure_process_high_qos()
        start_worker_queue_feeder(result_queue)
        prepare_worker_for_job(memory_limit_bytes)
        if memory_limit_bytes is not None:
            applied = lease.apply(memory_limit_bytes, required=require_memory_limit)
    except BaseException as exc:
        envelope = _worker_error_envelope(exc)
        payload = _serialise_worker_payload(envelope)
        result_queue.put(payload)
        result_queue.close()
        result_queue.join_thread()
        return
    report_worker_ready(
        result_queue, pickle.dumps(_ready_message(), protocol=pickle.HIGHEST_PROTOCOL), go
    )
    with native_memory_backend_scope(lease.backend if applied else None, lease):
        try:
            with project_scope(project):
                envelope = ("ok", function(*args, **kwargs))
        except BaseException as exc:
            envelope = _worker_error_envelope(
                memory_error_for_thread_start_failure(exc) if applied else exc
            )
        # The native limit deliberately remains active until the queue feeder has
        # flushed this possibly-large payload.  Do not restore or close the lease:
        # process teardown releases it, and the joined parent removes any private
        # native resource.  Widening it here would leave transport uncapped.
        payload = _serialise_worker_payload(envelope)
        result_queue.put(payload)
        result_queue.close()
        result_queue.join_thread()


def note_teardown_failure(
    primary: BaseException | None, exc: BaseException, *, step: str
) -> BaseException:
    """The error a worker's teardown ends with: the primary one with *exc* noted, or *exc*."""
    if primary is None:
        return exc
    primary.add_note(f"{step} failed: {exc}")
    return primary


def _ready_message() -> tuple[str, int]:
    return ("ready", os.getpid())


def _is_ready_payload(process: BaseProcess, raw: Any) -> bool:
    """Whether a one-shot worker's first message is its ready report."""
    try:
        return bool(pickle.loads(raw) == ("ready", process.pid))
    except BaseException:
        return False


def _worker_error_envelope(exc: BaseException) -> tuple[str, tuple[str, str, str, bool]]:
    # Windows reports a path too long for it as a missing file; name the cause, so the
    # parent can classify the failure, and keep the original traceback.
    reported: BaseException = path_too_long_error(exc) or exc
    return (
        "error",
        (
            type(reported).__name__,
            str(reported),
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            isinstance(exc, MemoryError),
        ),
    )


def _serialise_worker_payload(envelope: tuple[str, Any]) -> bytes:
    """Serialize the child result while its native cap is active."""
    try:
        return pickle.dumps(envelope, protocol=pickle.HIGHEST_PROTOCOL)
    except BaseException as exc:
        return pickle.dumps(
            _worker_error_envelope(RuntimeError(f"worker result was not serialisable: {exc}")),
            protocol=pickle.HIGHEST_PROTOCOL,
        )


def _read_worker_payload(
    result_queue: mp.Queue[bytes],
    *,
    exitcode: int | None,
    memory_limit_bytes: int | None,
) -> tuple[str, Any]:
    try:
        return _decode_worker_payload(result_queue.get(timeout=1.0))
    except queue.Empty as exc:
        raise WorkerCrashedError(
            exitcode, memory_limited=exitcode_looks_memory_limited(exitcode, memory_limit_bytes)
        ) from exc


def _terminate_process(process: BaseProcess) -> None:
    process.terminate()
    process.join(timeout=2.0)
    if process.is_alive():
        kill = getattr(process, "kill", None)
        if callable(kill):
            kill()
            process.join(timeout=2.0)
    if process.is_alive():
        raise WorkerTerminationError()


def _wait_for_worker(
    process: BaseProcess,
    config: IsolatedWorkerConfig,
    result_queue: mp.Queue[bytes],
    rss_watchdog: _WorkerRssWatchdog,
    *,
    deadline: float | None,
    queued_payload: tuple[str, Any] | None = None,
) -> tuple[str, Any] | None:
    while process.is_alive():
        rss_watchdog.checkpoint()
        if queued_payload is None:
            try:
                queued_payload = _decode_worker_payload(result_queue.get_nowait())
            except queue.Empty:
                pass
        if config.stop_reason is not None:
            reason = config.stop_reason()
            if reason is not None:
                _terminate_process(process)
                raise WorkerStoppedError(terminal_reason=reason)
        wait_seconds = config.stop_poll_interval_seconds
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_process(process)
                raise WorkerTimeoutError(
                    timeout_seconds=cast(float, config.timeout_seconds),
                )
            wait_seconds = min(wait_seconds, remaining)
        process.join(timeout=wait_seconds)
    return queued_payload


def _decode_worker_payload(raw_payload: Any) -> tuple[str, Any]:
    try:
        payload = pickle.loads(raw_payload)
    except BaseException as exc:
        raise WorkerCrashedError(None) from exc
    if not isinstance(payload, tuple) or len(payload) != 2 or payload[0] not in {"ok", "error"}:
        raise WorkerCrashedError(None)
    return cast(tuple[str, Any], payload)
