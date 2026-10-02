"""Test-only helpers for the data check's process-mode tests.

Production code carries no fault points. A test that must slow a read inside
an interactive worker starts the pool with this module among its preload
modules and sets ``HAUTE_TEST_SLOW_HASH_SECONDS``: imported inside an
interactive worker, the module then makes every file content hash sleep that
long first. Imported anywhere else it changes nothing. It also holds the
plain functions the tests run in a worker, importable without the test
module's fixtures.
"""

from __future__ import annotations

import multiprocessing
import os
import time

SLOW_HASH_ENV = "HAUTE_TEST_SLOW_HASH_SECONDS"


def worker_pid() -> int:
    return os.getpid()


def hold_worker(seconds: float) -> int:
    time.sleep(seconds)
    return os.getpid()


def _install_slow_hash(seconds: float) -> None:
    from haute._json_shred import _source_proof

    original = _source_proof._hash_file

    def slow_hash(path: object) -> str:
        time.sleep(seconds)
        return original(path)  # type: ignore[arg-type]

    _source_proof._hash_file = slow_hash  # type: ignore[assignment]


if os.environ.get(SLOW_HASH_ENV) and multiprocessing.current_process().name.startswith(
    "haute-interactive-"
):
    _install_slow_hash(float(os.environ[SLOW_HASH_ENV]))
