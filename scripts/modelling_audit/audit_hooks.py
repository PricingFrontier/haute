"""Importable spawn-worker hooks used only by the opt-in profiling and timing servers."""

import cProfile
import os
import time
from pathlib import Path

from haute._worker_protocol import (
    WorkerFailurePayload,
    WorkerRequest,
    WorkerResultManifest,
    WorkerRuntime,
)


def profile_training_worker(
    runtime: WorkerRuntime, request: WorkerRequest
) -> WorkerResultManifest | WorkerFailurePayload:
    from haute.routes._training_worker import _run_training_process_job

    profile = cProfile.Profile()
    start = time.perf_counter()
    try:
        return profile.runcall(_run_training_process_job, runtime, request)
    finally:
        root = Path(os.environ["HAUTE_AUDIT_OUTPUT"])
        profile.dump_stats(str(root / f"worker-{request.request_id}.prof"))
        (root / f"worker-{request.request_id}.seconds").write_text(str(time.perf_counter() - start))


def timed_training_worker(
    runtime: WorkerRuntime, request: WorkerRequest
) -> WorkerResultManifest | WorkerFailurePayload:
    """Record the worker job function's wall-clock span (after spawn and imports)."""
    from haute.routes._training_worker import _run_training_process_job

    from .audit_timing import WORKER_TARGETS, install, record

    install(Path(os.environ["HAUTE_AUDIT_OUTPUT"]), WORKER_TARGETS)
    start = time.time()
    try:
        return _run_training_process_job(runtime, request)
    finally:
        record(Path(os.environ["HAUTE_AUDIT_OUTPUT"]), "worker.job", start, time.time())
