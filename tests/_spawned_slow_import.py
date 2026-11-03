"""A module whose import hangs in a spawned worker, which therefore never reports ready.
The test process imports it for the function only, and does not wait."""

from __future__ import annotations

import multiprocessing
import time

#: True in a spawned worker importing this module for its job, where the process already
#: carries the worker's name; the test process (and an xdist worker) is "MainProcess".
_IN_A_SPAWNED_WORKER = multiprocessing.current_process().name != "MainProcess"

if _IN_A_SPAWNED_WORKER:
    time.sleep(120)


def never_runs() -> None:
    raise AssertionError("a worker whose import hangs never reaches its job")
