"""Diagnostic-only pytest plugin: per-test timing for the CI duration study.

Loaded with ``-p ci_timing_plugin`` (``PYTHONPATH`` points at this directory).
Each test process (every xdist worker, or the single process of a non-dist
run) appends one JSON line per test to ``$HAUTE_TIMING_DIR/<worker>-<pid>.jsonl``
with its wall-clock start and end, the setup/call/teardown durations and the
outcome, plus session events (plugin import, collection finished, session
finished) so collection cost and the tail of the run can be measured.
The xdist controller writes nothing: its hooks replay the workers' reports.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

_IMPORTED_AT = time.time()
_OUT_DIR = Path(os.environ.get("HAUTE_TIMING_DIR", ".cache/timing")).resolve()
_WORKER = os.environ.get("PYTEST_XDIST_WORKER", "main")

_enabled = True
_handle: Any = None
_current: dict[str, dict[str, Any]] = {}


def _write(record: dict[str, Any]) -> None:
    global _handle
    if not _enabled:
        return
    if _handle is None:
        _OUT_DIR.mkdir(parents=True, exist_ok=True)
        path = _OUT_DIR / f"{_WORKER}-{os.getpid()}.jsonl"
        _handle = open(path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    _handle.write(json.dumps(record) + "\n")


def pytest_configure(config: Any) -> None:
    global _enabled
    is_worker = "PYTEST_XDIST_WORKER" in os.environ
    numprocesses = getattr(config.option, "numprocesses", None)
    dist = getattr(config.option, "dist", "no")
    _enabled = is_worker or not (numprocesses and dist != "no")
    _write({"event": "configure", "t": time.time(), "imported": _IMPORTED_AT, "worker": _WORKER})


def pytest_collection_finish(session: Any) -> None:
    _write({"event": "collection_finish", "t": time.time(), "items": len(session.items)})


def pytest_runtest_logstart(nodeid: str, location: Any) -> None:
    _current[nodeid] = {"id": nodeid, "w": _WORKER, "start": time.time()}


def pytest_runtest_logreport(report: Any) -> None:
    row = _current.get(report.nodeid)
    if row is None:
        return
    row[report.when] = round(report.duration, 4)
    if report.when == "call" or report.outcome != "passed":
        row["outcome"] = report.outcome


def pytest_runtest_logfinish(nodeid: str, location: Any) -> None:
    row = _current.pop(nodeid, None)
    if row is None:
        return
    row["end"] = time.time()
    _write(row)


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    _write({"event": "sessionfinish", "t": time.time(), "exitstatus": int(exitstatus)})


def pytest_unconfigure(config: Any) -> None:
    global _handle
    if _handle is not None:
        _handle.close()
        _handle = None
