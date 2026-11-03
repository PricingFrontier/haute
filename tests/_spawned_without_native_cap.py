"""The heavy import on a host without a native memory cap: imported in a spawned worker, it
leaves the cap uninstalled, so the parent's RSS watchdog is the only bound. The test process
imports it for the job's functions only; the cap is left out in a spawned worker alone.

The job functions are this module's own: a function is pickled by its module, so one
imported from elsewhere would have the child import that module and not this one.
"""

from __future__ import annotations

import multiprocessing
from typing import Any

from haute._native_memory_limit import NativeMemoryLease
from haute._worker_protocol import WorkerResultManifest
from tests import _spawned_heavy_import as heavy

#: True in a spawned worker importing this module for its job, where the process already
#: carries the worker's name; the test process (and an xdist worker) is "MainProcess".
_IN_A_SPAWNED_WORKER = multiprocessing.current_process().name != "MainProcess"


def _no_native_cap(self: NativeMemoryLease, *_args: object, **_kwargs: object) -> bool:
    return False


if _IN_A_SPAWNED_WORKER:
    NativeMemoryLease.apply = _no_native_cap  # type: ignore[method-assign]


def allocate(mib: int, hold_seconds: float = 0.0) -> int:
    return heavy.allocate(mib, hold_seconds)


def allocate_in_protocol(runtime: Any, request: Any) -> WorkerResultManifest:
    return heavy.allocate_in_protocol(runtime, request)
