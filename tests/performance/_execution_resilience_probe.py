"""Fresh-interpreter resilience workload for execution certification."""

from __future__ import annotations

import argparse
import ctypes
import errno
import json
import operator
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import orjson

from haute._interactive_workers import InteractiveWorkerCrashedError, InteractiveWorkerPool
from haute._json_shred._cache import build_per_port_cache, is_per_port_cache_valid
from haute._json_shred._publication import _build_lock_for
from haute._json_shred._writer import _BoundedParquetRowGroupWriter
from haute._process_memory import process_rss_bytes

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


def _config(column: str) -> dict[str, Any]:
    return {
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
        ]
    }


def _siblings(cache: Path) -> list[Path]:
    return sorted(
        (
            *cache.parent.glob(f"{cache.name}.build-tmp-*"),
            *cache.parent.glob(f"{cache.name}.build-old-*"),
        )
    )


def _run_phase_child(phase: str, source: Path, cache: Path, config: dict[str, Any]) -> int:
    from haute._json_shred import _cache, _publication, _writer

    if phase == "row_group_emission":
        original = _writer._BoundedParquetRowGroupWriter.flush

        def crash_after_flush(writer: Any) -> None:
            original(writer)
            os._exit(91)

        _writer._BoundedParquetRowGroupWriter.flush = crash_after_flush
    elif phase == "after_private_staging":
        original = _cache.commit_prepared_per_port_cache

        def crash_after_staging(*args: Any, **kwargs: Any) -> Any:
            os._exit(91)

        _cache.commit_prepared_per_port_cache = crash_after_staging
    else:
        original = _publication._rename_dir_with_retry

        def crash_after_rename(source_dir: Path, target: Path) -> None:
            original(source_dir, target)
            if phase == "after_live_backup_rename" and source_dir == cache:
                os._exit(91)
            if phase == "after_staged_live_rename" and target == cache:
                os._exit(91)

        _publication._rename_dir_with_retry = crash_after_rename
        if phase == "obsolete_backup_cleanup":
            original_rmtree = shutil.rmtree

            def crash_on_backup_cleanup(path: Any, *args: Any, **kwargs: Any) -> Any:
                if Path(path).name.startswith(f"{cache.name}.build-old-"):
                    os._exit(91)
                return original_rmtree(path, *args, **kwargs)

            shutil.rmtree = crash_on_backup_cleanup
    build_per_port_cache(source, config, cache)
    return 0


def _write_jsonl_source(source: Path) -> None:
    """Write the bounded probe fixture beneath the caller-owned scratch root."""
    source.write_bytes(b"".join(orjson.dumps({"a": row, "b": row}) + b"\n" for row in range(8)))


def _cache_resilience(root: Path, contenders: int) -> dict[str, Any]:
    source, cache = root / "rows.jsonl", root / "cache"
    _write_jsonl_source(source)
    old, new = _config("a"), _config("b")
    build_per_port_cache(source, old, cache)
    phases = [
        "row_group_emission",
        "after_private_staging",
        "after_live_backup_rename",
        "after_staged_live_rename",
        "obsolete_backup_cleanup",
    ]
    phase_evidence: dict[str, Any] = {}
    for phase in phases:
        build_per_port_cache(source, old, cache)
        completed = subprocess.run(
            [
                sys.executable,
                __file__,
                "--phase",
                phase,
                "--source",
                str(source),
                "--cache",
                str(cache),
            ],
            check=False,
        )
        if completed.returncode != 91:
            raise RuntimeError(f"phase {phase} exit={completed.returncode}, expected 91")
        with _build_lock_for(cache):
            pass
        valid_old, valid_new = (
            is_per_port_cache_valid(cache, old, data_path=source),
            is_per_port_cache_valid(cache, new, data_path=source),
        )
        if valid_old == valid_new or _siblings(cache):
            raise RuntimeError(
                f"recovery failed for {phase}: old={valid_old}, new={valid_new}, "
                f"siblings={_siblings(cache)}"
            )
        phase_evidence[phase] = {"valid_old": valid_old, "valid_new": valid_new}
    build_per_port_cache(source, old, cache)
    old_bytes = {
        path.relative_to(cache).as_posix(): path.read_bytes()
        for path in cache.rglob("*")
        if path.is_file()
    }
    original_flush = _BoundedParquetRowGroupWriter.flush

    def full_disk(writer: Any) -> None:
        original_flush(writer)
        raise OSError(errno.ENOSPC, "simulated full disk")

    from haute._json_shred import _writer

    _writer._BoundedParquetRowGroupWriter.flush = full_disk
    try:
        try:
            build_per_port_cache(source, new, cache)
        except OSError as exc:
            if exc.errno != errno.ENOSPC:
                raise
        else:
            raise RuntimeError("ENOSPC injection did not fail")
    finally:
        _writer._BoundedParquetRowGroupWriter.flush = original_flush
    current = {
        path.relative_to(cache).as_posix(): path.read_bytes()
        for path in cache.rglob("*")
        if path.is_file()
    }
    if current != old_bytes or _siblings(cache):
        raise RuntimeError("ENOSPC changed live cache or leaked staging")
    build_per_port_cache(source, new, cache)
    if not is_per_port_cache_valid(cache, new, data_path=source):
        raise RuntimeError("rebuild after ENOSPC did not produce valid cache")
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                __file__,
                "--build",
                str(source),
                str(cache),
                "a" if index % 2 == 0 else "b",
            ]
        )
        for index in range(contenders)
    ]
    exits = [process.wait(timeout=30) for process in processes]
    if any(exits) or _siblings(cache):
        raise RuntimeError(f"contention failed exits={exits}, siblings={_siblings(cache)}")
    winners = [
        name
        for name, config in (("old", old), ("new", new))
        if is_per_port_cache_valid(cache, config, data_path=source)
    ]
    if len(winners) != 1:
        raise RuntimeError(f"contention did not leave one valid winner: {winners}")
    return {
        "phases": phase_evidence,
        "enospc_preserved_old": True,
        "contenders": contenders,
        "contention_exits": exits,
        "winner": winners[0],
    }


def _soak(calls: int, replacements: int) -> dict[str, Any]:
    pool = InteractiveWorkerPool(size=1, poll_interval_seconds=0.005)
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
    parser.add_argument("--cache")
    parser.add_argument("--build", nargs=3)
    args = parser.parse_args()
    if args.phase:
        raise SystemExit(
            _run_phase_child(args.phase, Path(args.source), Path(args.cache), _config("b"))
        )
    if args.build:
        source, cache, column = args.build
        build_per_port_cache(source, _config(column), cache)
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
