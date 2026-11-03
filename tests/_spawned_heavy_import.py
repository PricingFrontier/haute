"""A module a spawned worker imports that holds memory from its import, as a model library
does: the readiness tests' child and job (``tests/test_worker_isolation.py``,
``tests/test_worker_protocol.py``). The test process imports it for the job's function
only; the memory is held in a spawned worker alone."""

from __future__ import annotations

import multiprocessing
import time
from typing import Any

from haute._worker_protocol import WorkerResultManifest

#: True in a spawned worker importing this module for its job, where the process already
#: carries the worker's name; the test process (and an xdist worker) is "MainProcess".
_IN_A_SPAWNED_WORKER = multiprocessing.current_process().name != "MainProcess"

IMPORT_MIB = 160


def touched(mib: int) -> bytearray:
    """*mib* mebibytes, every page written so it is resident."""
    block = bytearray(mib * 1024 * 1024)
    for offset in range(0, len(block), 4096):
        block[offset] = 1
    return block


_HELD_SINCE_IMPORT = touched(IMPORT_MIB) if _IN_A_SPAWNED_WORKER else bytearray()


def allocate(mib: int, hold_seconds: float = 0.0) -> int:
    """The job: hold *mib* more mebibytes, beyond what the import holds, for *hold_seconds*
    (long enough for a parent sampling every tenth of a second to see it)."""
    held = touched(mib)
    time.sleep(hold_seconds)
    return len(held) // (1024 * 1024)


def allocate_in_protocol(_runtime: Any, request: Any) -> WorkerResultManifest:
    payload = request.payload
    held = allocate(int(payload["mib"]), float(payload.get("hold_seconds", 0.0)))
    return WorkerResultManifest(metadata={"mib": held})
