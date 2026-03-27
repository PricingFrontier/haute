"""Polars streaming helpers shared across execution paths."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

import polars as pl

from haute._logging import get_logger

logger = get_logger(component="polars_utils")


# ---------------------------------------------------------------------------
# Atomic write helper
# ---------------------------------------------------------------------------


@contextmanager
def atomic_write(dest: Path) -> Generator[Path, None, None]:
    """Context manager for atomic file writes via temp-then-rename.

    Yields a temporary path (``dest`` with ``.parquet.tmp`` suffix).
    On successful exit, atomically renames the temp file to *dest*.
    On exception, cleans up the temp file and re-raises.

    Usage::

        with atomic_write(cache_path) as tmp:
            df.write_parquet(tmp, compression="zstd")
        # cache_path now exists
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".parquet.tmp")
    try:
        yield tmp
        tmp.replace(dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------------------
# Parquet metadata reader
# ---------------------------------------------------------------------------


def read_parquet_metadata(path: Path) -> dict[str, Any]:
    """Read lightweight schema info from a parquet file.

    Returns a dict with keys: ``row_count``, ``column_count``, ``columns``
    (name → Arrow type string), ``size_bytes``, and ``mtime``.
    """
    import pyarrow.parquet as pq

    stat = path.stat()
    meta = pq.read_metadata(str(path))
    arrow_schema = pq.read_schema(str(path))
    columns = {name: str(arrow_schema.field(name).type) for name in arrow_schema.names}
    return {
        "row_count": meta.num_rows,
        "column_count": meta.num_columns,
        "columns": columns,
        "size_bytes": stat.st_size,
        "mtime": stat.st_mtime,
    }


def _malloc_trim() -> None:
    """Ask the OS to return freed heap pages.

    After ``del df``, Python's allocator keeps the pages mapped — RSS
    stays high even though the memory is logically free.

    Platform strategies:

    - **Linux**: ``malloc_trim(0)`` via glibc forces arena release.
    - **Windows**: ``HeapCompact`` on the process default heap.  This
      compacts the heap where Rust/Polars allocations live (via
      ``HeapAlloc``).  The previous ``_heapmin()`` call only affected
      the CRT heap which Polars does not use.
    - **macOS**: no direct API — callers should ``gc.collect()`` beforehand.
    """
    import sys

    platform = sys.platform
    if platform == "linux":
        try:
            import ctypes

            ctypes.CDLL("libc.so.6").malloc_trim(0)
        except (OSError, AttributeError):
            logger.debug("malloc_trim_unavailable", platform=platform)
    elif platform == "win32":
        try:
            import ctypes
            import ctypes.wintypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            # GetProcessHeap returns a HANDLE (void*) — must declare
            # the return type explicitly or ctypes truncates it to
            # c_int (32-bit) on 64-bit Python, causing access violations.
            kernel32.GetProcessHeap.restype = ctypes.wintypes.HANDLE
            kernel32.HeapCompact.argtypes = [ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD]
            kernel32.HeapCompact.restype = ctypes.c_size_t
            heap = kernel32.GetProcessHeap()
            kernel32.HeapCompact(heap, 0)
        except (OSError, AttributeError):
            logger.debug("heap_compact_unavailable", platform=platform)
    # macOS / other: no native heap compaction API available


def safe_sink(
    lf: pl.LazyFrame,
    path: str | Path,
    *,
    fmt: str = "parquet",
    fast_checkpoint: bool = False,
) -> None:
    """Sink a LazyFrame to file via streaming, with fallback.

    Tries ``sink_parquet`` / ``sink_csv`` first (streaming, low memory).
    If Polars raises a streaming-incompatible error, falls back to
    ``collect(engine="streaming")`` + eager write.

    Only retries on Polars-specific errors (``ComputeError``,
    ``InvalidOperationError``, ``SchemaError``).  Real I/O errors
    (permissions, disk full) propagate immediately.

    When *fast_checkpoint* is ``True``, uses ``lz4`` compression instead
    of the default ``zstd``.  This is ~3× faster for write and ~2× faster
    for read — ideal for temporary checkpoint files that are consumed
    immediately and then deleted.
    """
    path = Path(path)
    compression: Literal["lz4", "zstd"] = "lz4" if fast_checkpoint else "zstd"

    def _do_sink(target: Path) -> None:
        try:
            if fmt == "csv":
                lf.sink_csv(target)
            else:
                lf.sink_parquet(target, compression=compression)
        except (
            pl.exceptions.ComputeError,
            pl.exceptions.InvalidOperationError,
            pl.exceptions.SchemaError,
        ):
            logger.info("sink_streaming_fallback", path=str(path), fmt=fmt)
            df = lf.collect(engine="streaming")
            if fmt == "csv":
                df.write_csv(target)
            else:
                df.write_parquet(target, compression=compression)
            del df

    # Use atomic write only when parent directory already exists —
    # otherwise let the sink raise naturally on missing dirs.
    if path.parent.exists():
        with atomic_write(path) as tmp:
            _do_sink(tmp)
    else:
        _do_sink(path)
