"""Helpers for tests that drive a training request through ``TrainService``."""

from __future__ import annotations

import threading

#: Generous because ``-n auto`` runs one worker per core against one CPU pool:
#: a preparation thread that finishes well inside a second can still take far
#: longer under a loaded parallel run.
PREPARATION_JOIN_TIMEOUT_S = 120.0


def join_training_preparation(job_id: str, *, timeout: float = PREPARATION_JOIN_TIMEOUT_S) -> None:
    """Wait for the preparation thread a train request started for *job_id*.

    The service names that thread ``haute-<kind>-prep-<job id>``; one that has
    already finished is no longer listed and needs no wait.
    """
    suffix = f"-prep-{job_id}"
    for thread in threading.enumerate():
        if thread.name.startswith("haute-") and thread.name.endswith(suffix):
            thread.join(timeout=timeout)
            if thread.is_alive():
                raise AssertionError(f"Training preparation for job {job_id!r} is still running")
