"""Fresh-interpreter resilience workload for execution certification."""

from __future__ import annotations

import argparse
import ctypes
import errno
import json
import operator
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import orjson
import polars as pl

from haute._execution_context import ExecutionProfile
from haute._interactive_workers import InteractiveWorkerCrashedError, InteractiveWorkerPool
from haute._json_shred._snapshots import api_input_snapshot_source, build_api_input_tables
from haute._process_memory import process_rss_bytes
from haute._source_cache import SourceCacheIdentity, SourceCacheStore

_SCALES = {"ci": (120, 8, 4), "1m": (2_000, 100, 12), "10m": (10_000, 1_000, 32)}


def _rss_bytes() -> int:
    rss = process_rss_bytes(os.getpid())
    if rss is None:
        raise OSError("current-process RSS is unavailable")
    return rss


def _resource_count() -> int:
    if os.name == "nt":
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_current_process = kernel32.GetCurrentProcess
        get_current_process.argtypes = []
        get_current_process.restype = wintypes.HANDLE
        get_process_handle_count = kernel32.GetProcessHandleCount
        get_process_handle_count.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        get_process_handle_count.restype = wintypes.BOOL
        count = wintypes.DWORD()
        if not get_process_handle_count(get_current_process(), ctypes.byref(count)):
            raise ctypes.WinError(ctypes.get_last_error())
        return int(count.value)
    proc_fd = Path("/proc/self/fd")
    if proc_fd.is_dir():
        return len(list(proc_fd.iterdir()))
    # Darwin and generic POSIX: query each descriptor without allocating one.
    import fcntl

    maximum = int(ctypes.CDLL(None).getdtablesize())
    return sum(1 for descriptor in range(maximum) if _descriptor_open(fcntl, descriptor))


def _descriptor_open(fcntl_module: Any, descriptor: int) -> bool:
    try:
        fcntl_module.fcntl(descriptor, fcntl_module.F_GETFD)
    except OSError as exc:
        return exc.errno != errno.EBADF
    return True


def _config(source: Path, column: str) -> dict[str, Any]:
    return {
        "path": str(source),
        "tables": [
            {
                "path": "$[:]",
                "label": "rows",
                "emit": True,
                "row_id_column": None,
                "columns": [
                    {
                        "name": column,
                        "path": f"$[:].{column}",
                        "type": "int",
                        "status": "Confirmed",
                        "selected": True,
                        "levels": None,
                    }
                ],
            }
        ],
    }


def _identity(source: Path, column: str) -> SourceCacheIdentity:
    return api_input_snapshot_source(_config(source, column), source).table("rows").identity


def _build(root: Path, source: Path, column: str) -> str:
    """Build the one table of *column*'s API Input; return its new generation id."""
    snapshot_source = api_input_snapshot_source(_config(source, column), source)
    generations = build_api_input_tables(
        snapshot_source,
        ["rows"],
        store=SourceCacheStore(root),
        profile=ExecutionProfile.LAZY_SINK,
    )
    return generations[_identity(source, column).digest].generation_id


def _staging(root: Path) -> list[Path]:
    """Every store staging and shred scratch directory under *root*'s input store."""
    return sorted((root / ".haute_cache" / "inputs").glob("*/.staging-*"))


def _reclaim_abandoned_staging(root: Path) -> None:
    """Open the store once with no staging age, as a restart would after the grace period.

    Only this open reclaims at once: a concurrent build's live staging is
    protected by the ordinary age limit everywhere else.
    """
    name = "HAUTE_INPUT_CACHE_STAGING_MAX_AGE_SECONDS"
    os.environ[name] = "0.001"
    try:
        SourceCacheStore(root)
    finally:
        del os.environ[name]


def _current(root: Path, source: Path, column: str) -> str:
    """The current generation id of *column*'s table, proven readable and complete."""
    generation = SourceCacheStore(root).open_generation(_identity(source, column))
    values = pl.read_parquet(list(generation.data_paths))[column].to_list()
    if values != list(range(8)):
        raise RuntimeError(f"generation {generation.generation_id} is incomplete: {values}")
    return generation.generation_id


def _run_phase_child(phase: str, source: Path, root: Path) -> int:
    """Rebuild the ``a`` table and exit hard at *phase* of its build."""
    from haute import _source_cache
    from haute._json_shred import _writer

    if phase == "row_group_emission":
        original_flush = _writer._BoundedParquetRowGroupWriter.flush

        def crash_after_flush(writer: Any) -> None:
            original_flush(writer)
            os._exit(91)

        _writer._BoundedParquetRowGroupWriter.flush = crash_after_flush  # type: ignore[method-assign]
    else:
        original_write = _source_cache.atomic_write_text

        def crash_at_pointer(path: Path, *args: Any, **kwargs: Any) -> Any:
            if Path(path).name != "current.json":
                return original_write(path, *args, **kwargs)
            if phase == "after_pointer_published":
                original_write(path, *args, **kwargs)
            os._exit(91)

        _source_cache.atomic_write_text = crash_at_pointer  # type: ignore[assignment]
    _build(root, source, "a")
    return 0


def _write_jsonl_source(source: Path) -> None:
    """Write the bounded probe fixture beneath the caller-owned scratch root."""
    source.write_bytes(b"".join(orjson.dumps({"a": row, "b": row}) + b"\n" for row in range(8)))


# Each phase names the generation that must be current after the crash.
_CRASH_PHASES = {
    "row_group_emission": "old",
    "before_pointer_published": "old",
    "after_pointer_published": "new",
}


def _cache_resilience(root: Path, contenders: int) -> dict[str, Any]:
    """Crash, ENOSPC, and contention certification of an API Input table build.

    A build that dies at any phase leaves exactly one readable current
    generation (the old one until the pointer moves, the new one after), and
    the next store to open reclaims everything the dead build staged.
    """
    source = root / "rows.jsonl"
    _write_jsonl_source(source)
    phase_evidence: dict[str, Any] = {}
    for phase, expected in _CRASH_PHASES.items():
        old = _build(root, source, "a")
        completed = subprocess.run(
            [
                sys.executable,
                __file__,
                "--phase",
                phase,
                "--source",
                str(source),
                "--root",
                str(root),
            ],
            check=False,
        )
        if completed.returncode != 91:
            raise RuntimeError(f"phase {phase} exit={completed.returncode}, expected 91")
        _reclaim_abandoned_staging(root)
        current = _current(root, source, "a")
        leaked = _staging(root)
        if (current == old) != (expected == "old") or leaked:
            raise RuntimeError(
                f"recovery failed for {phase}: current={current} old={old} "
                f"expected={expected} staging={leaked}"
            )
        phase_evidence[phase] = {"current": expected, "staging_left": len(leaked)}

    from haute._json_shred import _writer

    old = _build(root, source, "a")
    original_flush = _writer._BoundedParquetRowGroupWriter.flush

    def full_disk(writer: Any) -> None:
        original_flush(writer)
        raise OSError(errno.ENOSPC, "simulated full disk")

    _writer._BoundedParquetRowGroupWriter.flush = full_disk  # type: ignore[method-assign]
    try:
        try:
            _build(root, source, "a")
        except OSError as exc:
            if exc.errno != errno.ENOSPC:
                raise
        else:
            raise RuntimeError("ENOSPC injection did not fail")
    finally:
        _writer._BoundedParquetRowGroupWriter.flush = original_flush  # type: ignore[method-assign]
    if _current(root, source, "a") != old or _staging(root):
        raise RuntimeError("ENOSPC changed the current generation or leaked staging")
    if _build(root, source, "a") == old or _current(root, source, "a") == old:
        raise RuntimeError("rebuild after ENOSPC did not publish a new generation")

    processes = [
        subprocess.Popen(
            [
                sys.executable,
                __file__,
                "--build",
                str(source),
                str(root),
                "a" if index % 2 == 0 else "b",
            ]
        )
        for index in range(contenders)
    ]
    exits = [process.wait(timeout=60) for process in processes]
    leaked = _staging(root)
    if any(exits) or leaked:
        raise RuntimeError(f"contention failed exits={exits}, staging={leaked}")
    winners = {column: _current(root, source, column) for column in ("a", "b")}
    return {
        "phases": phase_evidence,
        "enospc_preserved_old": True,
        "contenders": contenders,
        "contention_exits": exits,
        "winners": winners,
    }


def _soak(calls: int, replacements: int) -> dict[str, Any]:
    pool = InteractiveWorkerPool(size=1, polars_threads=2, poll_interval_seconds=0.005)
    try:
        pool.start()
        baseline = {"rss_bytes": _rss_bytes(), "resources": _resource_count()}
        pids: list[int] = []
        crash_at = {((index + 1) * calls) // (replacements + 1) for index in range(replacements)}
        steady: dict[str, int] | None = None
        for value in range(calls):
            result = pool.run(
                operator.add,
                value,
                1,
                affinity_key="soak",
                timeout_seconds=10,
                memory_growth_limit_bytes=256 * 1024 * 1024,
                require_memory_limit=True,
            )
            if result != value + 1:
                raise RuntimeError("worker returned incorrect value")
            pids.append(pool._slots[0].process.pid)
            if value in crash_at:
                before = pool._slots[0].process.pid
                try:
                    pool.run(
                        os._exit,
                        87,
                        affinity_key="soak",
                        timeout_seconds=10,
                        memory_growth_limit_bytes=256 * 1024 * 1024,
                        require_memory_limit=True,
                    )
                except InteractiveWorkerCrashedError:
                    pass
                else:
                    raise RuntimeError("crashed worker did not report crash")
                after = pool._slots[0].process.pid
                if before == after:
                    raise RuntimeError("crashed worker PID was not replaced")
            if value == calls // 2:
                steady = {"rss_bytes": _rss_bytes(), "resources": _resource_count()}
        if steady is None:
            raise RuntimeError("steady-state boundary was not collected")
        end = {"rss_bytes": _rss_bytes(), "resources": _resource_count()}
    finally:
        pool.close()
    closed = {"rss_bytes": _rss_bytes(), "resources": _resource_count()}
    rss_growth = end["rss_bytes"] - steady["rss_bytes"]
    resource_growth = end["resources"] - steady["resources"]
    closed_resource_delta = closed["resources"] - baseline["resources"]
    unique_worker_pids = len(set(pids))
    if unique_worker_pids != replacements + 1:
        raise RuntimeError(
            "worker replacement contract failed: "
            f"expected={replacements + 1} actual={unique_worker_pids}"
        )
    if rss_growth > 32 * 1024 * 1024 or resource_growth > 4 or closed_resource_delta > 4:
        raise RuntimeError(
            f"resource growth contract failed: {baseline=} {steady=} {end=} {closed=}"
        )
    return {
        "calls": calls,
        "replacements": replacements,
        "unique_worker_pids": unique_worker_pids,
        "baseline": baseline,
        "steady": steady,
        "end_open": end,
        "after_close": closed,
        "plateau": {
            "rss_growth_bytes": rss_growth,
            "rss_growth_limit_bytes": 32 * 1024 * 1024,
            "resource_growth": resource_growth,
            "resource_growth_limit": 4,
            "after_close_resource_delta": closed_resource_delta,
            "after_close_resource_delta_limit": 4,
        },
    }


def _write_evidence(output: Path, evidence: dict[str, Any]) -> None:
    """Persist probe evidence to the parent-provided scratch destination."""
    output.write_text(json.dumps(evidence, sort_keys=True), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode")
    parser.add_argument("--root")
    parser.add_argument("--output")
    parser.add_argument("--phase")
    parser.add_argument("--source")
    parser.add_argument("--build", nargs=3)
    args = parser.parse_args()
    if args.phase:
        raise SystemExit(_run_phase_child(args.phase, Path(args.source), Path(args.root)))
    if args.build:
        source, root, column = args.build
        _build(Path(root), Path(source), column)
        return
    if args.mode not in _SCALES or not args.root or not args.output:
        raise ValueError("mode must be ci, 1m, or 10m and root/output are required")
    calls, replacements, contenders = _SCALES[args.mode]
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    evidence = {
        "mode": args.mode,
        "worker_soak": _soak(calls, replacements),
        "cache": _cache_resilience(root, contenders),
    }
    _write_evidence(Path(args.output), evidence)


if __name__ == "__main__":
    main()
