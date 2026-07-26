"""Polars streaming helpers shared across execution paths."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal, cast

import polars as pl

from haute._execution_context import (
    ExecutionContext,
    ExecutionProfile,
    current_execution_context,
)
from haute._logging import get_logger

logger = get_logger(component="polars_utils")
_STREAMING_CHUNK_SIZE_LOCK = threading.RLock()

DEFAULT_STREAMING_CHUNK_SIZE: int = 500_000
BOUNDED_MEMORY_EXEMPT_PROFILES = frozenset(
    {
        ExecutionProfile.PREVIEW_EAGER,
        ExecutionProfile.DEPLOY_LIVE,
    }
)


def normalise_execution_profile(
    profile: ExecutionProfile | str | None,
) -> ExecutionProfile | None:
    """Return the canonical profile enum used by every Polars I/O gate."""
    if profile is None or isinstance(profile, ExecutionProfile):
        return profile
    return ExecutionProfile(profile)


def is_bounded_execution_profile(profile: ExecutionProfile | str | None) -> bool:
    """Whether *profile* requires the bounded-memory I/O policy."""
    normalised = normalise_execution_profile(profile)
    return normalised is not None and normalised not in BOUNDED_MEMORY_EXEMPT_PROFILES


def streaming_collect(
    lf: pl.LazyFrame,
    *,
    execution_context: ExecutionContext | None = None,
) -> pl.DataFrame:
    """Collect a LazyFrame once through Polars' streaming engine."""
    metrics_context = execution_context or current_execution_context()
    if metrics_context is not None:
        metrics_context.fault_point("collect_before_native")
        metrics_context.record_collect()
    return lf.collect(engine="streaming")


def cancellable_streaming_collect(
    lf: pl.LazyFrame,
    *,
    execution_context: ExecutionContext,
    poll_seconds: float = 0.01,
) -> pl.DataFrame:
    """Collect through Polars streaming while propagating native cancellation."""

    if (
        isinstance(poll_seconds, bool)
        or not isinstance(poll_seconds, (int, float))
        or not math.isfinite(poll_seconds)
        or poll_seconds <= 0
    ):
        raise ValueError("poll_seconds must be a positive finite number")

    execution_context.checkpoint(label="streaming_collect_before_native")
    execution_context.fault_point("collect_before_native")
    execution_context.record_collect()
    query = lf.collect(engine="streaming", background=True)
    while True:
        result = query.fetch()
        if result is not None:
            return result
        try:
            execution_context.checkpoint(label="streaming_collect_poll")
        except BaseException:
            try:
                query.cancel()
            except Exception as cancel_exc:
                logger.warning(
                    "native_query_cancel_failed",
                    error=str(cancel_exc),
                    exc_info=True,
                )
            raise
        time.sleep(poll_seconds)


def bounded_collect_batches(
    lf: pl.LazyFrame,
    *,
    chunk_size: int,
    maintain_order: bool = False,
    execution_context: ExecutionContext | None = None,
    stage_name: str = "collect_batches",
    node_id: str | None = None,
) -> Iterator[pl.DataFrame]:
    """Yield native streaming batches with execution checkpoints."""

    if not isinstance(chunk_size, int) or isinstance(chunk_size, bool) or chunk_size <= 0:
        raise ValueError("chunk_size must be a positive integer")
    metrics_context = execution_context or current_execution_context()
    if metrics_context is not None:
        metrics_context.fault_point("collect_before_native", node_id=node_id)
    batches = lf.collect_batches(
        chunk_size=chunk_size,
        maintain_order=maintain_order,
        engine="streaming",
    )
    if metrics_context is not None:
        metrics_context.checkpoint(label="before_collect_batches", node_id=node_id)
    while True:
        try:
            if metrics_context is not None:
                with metrics_context.stage(
                    stage_name,
                    node_id=node_id,
                    skip_metric_on_exception=(StopIteration,),
                ):
                    batch = next(batches)
                    metrics_context.record_collect()
            else:
                batch = next(batches)
        except StopIteration:
            break
        if metrics_context is not None:
            metrics_context.checkpoint(label="after_collect_batch", node_id=node_id)
        yield batch


def _checkpoint_compression(fast_checkpoint: bool) -> Literal["lz4", "zstd"]:
    return "lz4" if fast_checkpoint else "zstd"


def _streaming_sink_to_path(
    lf: pl.LazyFrame,
    target: Path,
    *,
    fmt: str,
    compression: Literal["lz4", "zstd"],
) -> None:
    if fmt == "csv":
        lf.sink_csv(target)
    else:
        lf.sink_parquet(target, compression=compression)


def _eager_write_to_path(
    df: pl.DataFrame,
    target: Path,
    *,
    fmt: str,
    compression: Literal["lz4", "zstd"],
) -> None:
    if fmt == "csv":
        df.write_csv(target)
    else:
        df.write_parquet(target, compression=compression)


def _write_atomically_if_possible(path: Path, writer: Any) -> None:
    if path.parent.exists():
        with atomic_write(path) as tmp:
            writer(tmp)
    else:
        writer(path)


@contextmanager
def temporary_streaming_chunk_size(chunk_size: int | None) -> Iterator[None]:
    """Temporarily set Polars' process-global streaming chunk size.

    Polars exposes this as process-global configuration, so production callers
    must use this locked scope rather than mutating ``pl.Config`` directly.
    """
    if chunk_size is None:
        yield
        return
    if not isinstance(chunk_size, int) or isinstance(chunk_size, bool) or chunk_size <= 0:
        raise ValueError("streaming_chunk_size must be a positive integer")
    with _STREAMING_CHUNK_SIZE_LOCK:
        saved_config = pl.Config.save()
        try:
            pl.Config.set_streaming_chunk_size(chunk_size)
            yield
        finally:
            pl.Config.load(saved_config)


def streaming_sink(
    lf: pl.LazyFrame,
    path: str | Path,
    *,
    fmt: str = "parquet",
    fast_checkpoint: bool = False,
) -> None:
    """Sink a LazyFrame with Polars streaming and no eager fallback."""
    path = Path(path)
    compression = _checkpoint_compression(fast_checkpoint)

    def _do_sink(target: Path) -> None:
        _streaming_sink_to_path(lf, target, fmt=fmt, compression=compression)

    _write_atomically_if_possible(path, _do_sink)


def bounded_sink(
    lf: pl.LazyFrame,
    path: str | Path,
    *,
    fmt: str = "parquet",
    fast_checkpoint: bool = False,
    streaming_chunk_size: int | None = None,
) -> None:
    """Sink a LazyFrame through the native streaming API."""
    path = Path(path)
    metrics_context = current_execution_context()
    if metrics_context is not None:
        metrics_context.fault_point("sink_before_native")
    with temporary_streaming_chunk_size(streaming_chunk_size):
        streaming_sink(lf, path, fmt=fmt, fast_checkpoint=fast_checkpoint)
    if metrics_context is not None:
        metrics_context.fault_point("sink_after_native")
    if metrics_context is not None:
        metrics_context.record_bytes_written(path.stat().st_size)


# ---------------------------------------------------------------------------
# Atomic write helper
# ---------------------------------------------------------------------------


@contextmanager
def atomic_write(dest: Path, *, ensure_parent: bool = True) -> Generator[Path, None, None]:
    """Context manager for atomic file writes via temp-then-rename.

    Yields a temporary path (``dest`` with ``.parquet.tmp`` suffix).
    On successful exit, atomically renames the temp file to *dest*.
    On exception, cleans up the temp file and re-raises.
    Callers that create a shared parent once before a write loop may pass
    ``ensure_parent=False`` to avoid repeating the directory operation.

    Usage::

        with atomic_write(cache_path) as tmp:
            df.write_parquet(tmp, compression="zstd")
        # cache_path now exists
    """
    if ensure_parent:
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

    The returned mapping includes row/column counts, Arrow type strings by
    column name, file size, total and per-column compressed/uncompressed byte
    totals, and mtime.

    """
    import pyarrow.parquet as pq

    stat = path.stat()
    meta = pq.read_metadata(str(path))
    arrow_schema = pq.read_schema(str(path))
    columns = {name: str(arrow_schema.field(name).type) for name in arrow_schema.names}
    uncompressed_size_bytes = 0
    compressed_size_bytes = 0
    column_uncompressed_size_bytes = dict.fromkeys(columns, 0)
    column_compressed_size_bytes = dict.fromkeys(columns, 0)
    for row_group_index in range(meta.num_row_groups):
        row_group = meta.row_group(row_group_index)
        for column_index in range(row_group.num_columns):
            column = row_group.column(column_index)
            column_uncompressed_size = int(column.total_uncompressed_size)
            column_compressed_size = int(column.total_compressed_size)
            uncompressed_size_bytes += column_uncompressed_size
            compressed_size_bytes += column_compressed_size
            column_name = str(column.path_in_schema)
            column_uncompressed_size_bytes[column_name] = (
                column_uncompressed_size_bytes.get(column_name, 0) + column_uncompressed_size
            )
            column_compressed_size_bytes[column_name] = (
                column_compressed_size_bytes.get(column_name, 0) + column_compressed_size
            )
    return {
        "row_count": meta.num_rows,
        "column_count": meta.num_columns,
        "columns": columns,
        "size_bytes": stat.st_size,
        "uncompressed_size_bytes": uncompressed_size_bytes,
        "compressed_size_bytes": compressed_size_bytes,
        "column_uncompressed_size_bytes": column_uncompressed_size_bytes,
        "column_compressed_size_bytes": column_compressed_size_bytes,
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

            kernel32 = cast(Any, ctypes).windll.kernel32
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
