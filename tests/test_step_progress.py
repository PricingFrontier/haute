"""Step progress: the shared progress cell, the preview progress registry, and
the interactive pool carrying a worker's progress to its parent."""

from __future__ import annotations

import multiprocessing
import os
import threading
import time
from typing import Any

import pytest

from haute._interactive_workers import InteractiveWorkerCrashedError, InteractiveWorkerPool
from haute._step_progress import ProgressCell, StepProgress, current_job_progress_reporter
from haute.routes._preview_progress import PreviewProgressRegistry


def _cell() -> ProgressCell:
    return ProgressCell(multiprocessing.get_context("spawn"))


# ---------------------------------------------------------------- the cell


def test_a_cell_returns_only_the_job_it_was_written_for() -> None:
    cell = _cell()
    cell.write("job-a", 1, StepProgress(done=1, total=3, label="Caching join"))

    assert cell.try_read("job-a") == (1, StepProgress(done=1, total=3, label="Caching join"))
    assert cell.try_read("job-b") is None


def test_a_held_lock_skips_the_read_instead_of_waiting() -> None:
    cell = _cell()
    cell.write("job", 1, StepProgress(done=0, total=1, label=""))
    cell._lock.acquire()
    try:
        started = time.monotonic()
        assert cell.try_read("job") is None
        assert time.monotonic() - started < 1
    finally:
        cell._lock.release()


def test_every_accepted_reading_is_one_complete_update() -> None:
    cell = _cell()
    stop = threading.Event()

    def publish() -> None:
        sequence = 0
        while not stop.is_set():
            sequence += 1
            cell.write(
                "job",
                sequence,
                StepProgress(done=sequence, total=sequence, label=f"step {sequence}"),
            )

    writer = threading.Thread(target=publish)
    writer.start()
    readings = 0
    try:
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            reading = cell.try_read("job")
            if reading is None:
                continue
            sequence, progress = reading
            assert progress.done == progress.total == sequence
            assert progress.label == f"step {sequence}"
            readings += 1
    finally:
        stop.set()
        writer.join()
    assert readings > 0


# ------------------------------------------------------------ the registry


def test_the_registry_reports_a_request_from_open_until_it_settles() -> None:
    registry = PreviewProgressRegistry()
    registry.open("r1")
    assert registry.get("r1") is not None
    assert registry.get("r1").phase == "preparing"  # type: ignore[union-attr]

    registry.report("r1", StepProgress(done=1, total=2, label="Caching join"))
    progress = registry.get("r1")
    assert progress is not None
    assert (progress.phase, progress.done, progress.total, progress.label) == (
        "running",
        1,
        2,
        "Caching join",
    )

    registry.close("r1")
    assert registry.get("r1") is None


def test_a_late_report_never_recreates_a_settled_request() -> None:
    registry = PreviewProgressRegistry()
    registry.open("r1")
    registry.close("r1")

    registry.report("r1", StepProgress(done=2, total=2, label=""))
    registry.open("r1")

    assert registry.get("r1") is None


def test_a_report_for_an_unknown_request_is_ignored() -> None:
    registry = PreviewProgressRegistry()
    registry.report("unknown", StepProgress(done=1, total=1, label=""))
    assert registry.get("unknown") is None


# ---------------------------------------------------------- the real pool


def _report_steps(count: int) -> str:
    report = current_job_progress_reporter()
    assert report is not None
    for step in range(1, count + 1):
        report(StepProgress(done=step, total=count, label=f"step {step}"))
        time.sleep(0.1)
    return "done"


def _report_nothing() -> str:
    time.sleep(0.3)
    return "quiet"


def _die_holding_the_progress_lock() -> None:
    report: Any = current_job_progress_reporter()
    report.cell._lock.acquire()
    os._exit(3)


def test_a_workers_step_progress_reaches_its_parent() -> None:
    pool = InteractiveWorkerPool(size=1, polars_threads=2, poll_interval_seconds=0.01)
    seen: list[StepProgress] = []
    try:
        result = pool.run(
            _report_steps, 3, affinity_key="k", timeout_seconds=60, on_progress=seen.append
        )
    finally:
        pool.close()

    assert result == "done"
    assert seen, "the parent read no progress"
    assert [step.done for step in seen] == sorted({step.done for step in seen})
    assert all(step.total == 3 for step in seen)


def test_a_warm_workers_next_job_never_reads_the_previous_jobs_progress() -> None:
    pool = InteractiveWorkerPool(size=1, polars_threads=2, poll_interval_seconds=0.01)
    first: list[StepProgress] = []
    second: list[StepProgress] = []
    try:
        pool.run(_report_steps, 2, affinity_key="k", timeout_seconds=60, on_progress=first.append)
        assert (
            pool.run(
                _report_nothing, affinity_key="k", timeout_seconds=60, on_progress=second.append
            )
            == "quiet"
        )
    finally:
        pool.close()

    assert first
    assert second == []


def test_a_worker_killed_holding_the_progress_lock_settles_and_its_replacement_reports() -> None:
    pool = InteractiveWorkerPool(size=1, polars_threads=2, poll_interval_seconds=0.01)
    seen: list[StepProgress] = []
    try:
        started = time.monotonic()
        with pytest.raises(InteractiveWorkerCrashedError):
            pool.run(
                _die_holding_the_progress_lock,
                affinity_key="k",
                timeout_seconds=60,
                on_progress=seen.append,
            )
        assert time.monotonic() - started < 30

        result = pool.run(
            _report_steps, 2, affinity_key="k", timeout_seconds=60, on_progress=seen.append
        )
    finally:
        pool.close()

    assert result == "done"
    assert seen
