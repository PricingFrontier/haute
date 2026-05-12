"""Polars streaming helpers shared across execution paths."""

from __future__ import annotations

import threading
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

import polars as pl

from haute._execution_context import (
    ExecutionContext,
    ExecutionProfile,
    current_execution_context,
)
from haute._logging import get_logger
from haute.errors import BoundedMemoryUnsupportedError

logger = get_logger(component="polars_utils")
_STREAMING_CHUNK_SIZE_LOCK = threading.RLock()

DEFAULT_STREAMING_CHUNK_SIZE: int = 500_000

_POLARS_STREAMING_ERRORS = (
    pl.exceptions.ComputeError,
    pl.exceptions.InvalidOperationError,
    pl.exceptions.SchemaError,
)


_BROAD_COLLECT_PROFILES = frozenset({ExecutionProfile.PREVIEW_EAGER})


def _normalise_profile(profile: ExecutionProfile | str) -> ExecutionProfile:
    if isinstance(profile, ExecutionProfile):
        return profile
    return ExecutionProfile(profile)


def _is_streaming_compatibility_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return "stream" in message


def _is_streaming_sink_error(exc: BaseException) -> bool:
    return _is_streaming_compatibility_error(exc)


def streaming_collect(
    lf: pl.LazyFrame,
    *,
    profile: ExecutionProfile | str,
    allow_broad: bool = False,
    execution_context: ExecutionContext | None = None,
) -> pl.DataFrame:
    """Collect a LazyFrame through the profiled streaming collect contract.

    Bounded-memory callers leave ``allow_broad`` at the default and receive a
    typed Haute error if Polars cannot honour streaming execution. Callers that
    intentionally materialise small/interactive data must opt into
    ``allow_broad=True`` at the call site.
    """
    metrics_context = execution_context or current_execution_context()
    normalised_profile = (
        metrics_context.profile if metrics_context is not None else _normalise_profile(profile)
    )
    profile_name = normalised_profile.value
    if allow_broad and normalised_profile not in _BROAD_COLLECT_PROFILES:
        raise ValueError(f"allow_broad=True is not permitted for profile {profile_name!r}")
    try:
        if metrics_context is not None:
            metrics_context.record_collect()
        return lf.collect(engine="streaming")
    except _POLARS_STREAMING_ERRORS as exc:
        if not _is_streaming_compatibility_error(exc):
            raise
        if not allow_broad:
            raise BoundedMemoryUnsupportedError(
                "Bounded streaming collect failed",
                profile=profile_name,
                cause=type(exc).__name__,
            ) from exc
        logger.info(
            "collect_streaming_fallback",
            profile=profile_name,
            cause=type(exc).__name__,
        )
        if metrics_context is not None:
            metrics_context.record_collect()
        return lf.collect()


def bounded_collect_batches(
    lf: pl.LazyFrame,
    *,
    profile: ExecutionProfile | str,
    chunk_size: int,
    maintain_order: bool = False,
    execution_context: ExecutionContext | None = None,
    stage_name: str = "collect_batches",
    node_id: str | None = None,
) -> Iterator[pl.DataFrame]:
    """Yield streaming batches with the same fail-loud contract as collect.

    Native Polars batch iteration can fail during iterator construction or on
    any ``next()`` call.  This wrapper maps streaming-compatibility failures
    to :class:`BoundedMemoryUnsupportedError` and inserts cooperative
    cancellation/memory checkpoints between batches.
    """

    if not isinstance(chunk_size, int) or isinstance(chunk_size, bool) or chunk_size <= 0:
        raise ValueError("chunk_size must be a positive integer")
    metrics_context = execution_context or current_execution_context()
    normalised_profile = (
        metrics_context.profile if metrics_context is not None else _normalise_profile(profile)
    )
    profile_name = normalised_profile.value
    try:
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
    except _POLARS_STREAMING_ERRORS as exc:
        if not _is_streaming_compatibility_error(exc):
            raise
        raise BoundedMemoryUnsupportedError(
            "Bounded streaming batch collection failed",
            profile=profile_name,
            chunk_size=chunk_size,
            cause=type(exc).__name__,
        ) from exc


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


# Backwards-compatible private alias for older internal tests/imports.
_temporary_streaming_chunk_size = temporary_streaming_chunk_size


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
    """Sink a LazyFrame without any eager broadening fallback.

    This is the default for production/bounded-memory paths.  Polars sink
    incompatibilities are converted to a Haute typed error so API layers can
    explain that the current plan cannot be written in a bounded way instead
    of silently collecting a potentially huge frame.
    """
    path = Path(path)
    try:
        with temporary_streaming_chunk_size(streaming_chunk_size):
            streaming_sink(lf, path, fmt=fmt, fast_checkpoint=fast_checkpoint)
    except _POLARS_STREAMING_ERRORS as exc:
        if not _is_streaming_sink_error(exc):
            raise
        raise BoundedMemoryUnsupportedError(
            "Bounded streaming sink failed",
            path=str(path),
            fmt=fmt,
            fast_checkpoint=fast_checkpoint,
            cause=type(exc).__name__,
        ) from exc


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

    The returned mapping includes row/column counts, Arrow type strings by
    column name, file size, row-group compressed/uncompressed byte totals, and
    mtime.

    """
    import pyarrow.parquet as pq

    stat = path.stat()
    meta = pq.read_metadata(str(path))
    arrow_schema = pq.read_schema(str(path))
    columns = {name: str(arrow_schema.field(name).type) for name in arrow_schema.names}
    uncompressed_size_bytes = 0
    compressed_size_bytes = 0
    for row_group_index in range(meta.num_row_groups):
        row_group = meta.row_group(row_group_index)
        for column_index in range(row_group.num_columns):
            column = row_group.column(column_index)
            uncompressed_size_bytes += int(column.total_uncompressed_size)
            compressed_size_bytes += int(column.total_compressed_size)
    return {
        "row_count": meta.num_rows,
        "column_count": meta.num_columns,
        "columns": columns,
        "size_bytes": stat.st_size,
        "uncompressed_size_bytes": uncompressed_size_bytes,
        "compressed_size_bytes": compressed_size_bytes,
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


def best_effort_sink(
    lf: pl.LazyFrame,
    path: str | Path,
    *,
    fmt: str = "parquet",
    fast_checkpoint: bool = False,
    allow_broad: bool = False,
) -> None:
    """Sink a LazyFrame to file via streaming, with explicit eager fallback.

    Tries ``sink_parquet`` / ``sink_csv`` first (streaming, low memory).
    If Polars raises a streaming-incompatible error, falls back to
    ``collect(engine="streaming")`` + eager write.  Callers must pass
    ``allow_broad=True`` so any use of the high-memory path is deliberate
    at the call site.

    Only retries on Polars-specific errors (``ComputeError``,
    ``InvalidOperationError``, ``SchemaError``).  Real I/O errors
    (permissions, disk full) propagate immediately.

    When *fast_checkpoint* is ``True``, uses ``lz4`` compression instead
    of the default ``zstd``.  This is ~3× faster for write and ~2× faster
    for read — ideal for temporary checkpoint files that are consumed
    immediately and then deleted.
    """
    if not allow_broad:
        raise ValueError("best_effort_sink requires allow_broad=True")

    path = Path(path)
    compression = _checkpoint_compression(fast_checkpoint)

    def _do_sink(target: Path) -> None:
        try:
            _streaming_sink_to_path(lf, target, fmt=fmt, compression=compression)
        except _POLARS_STREAMING_ERRORS:
            logger.info("sink_streaming_fallback", path=str(path), fmt=fmt)
            context = current_execution_context()
            df = streaming_collect(
                lf,
                profile=context.profile if context is not None else ExecutionProfile.LAZY_SINK,
                execution_context=context,
            )
            try:
                _eager_write_to_path(df, target, fmt=fmt, compression=compression)
            finally:
                del df

    # Use atomic write only when parent directory already exists —
    # otherwise let the sink raise naturally on missing dirs.
    _write_atomically_if_possible(path, _do_sink)


def safe_sink(
    lf: pl.LazyFrame,
    path: str | Path,
    *,
    fmt: str = "parquet",
    fast_checkpoint: bool = False,
) -> None:
    """Compatibility wrapper for the legacy fallback-capable sink."""
    best_effort_sink(
        lf,
        path,
        fmt=fmt,
        fast_checkpoint=fast_checkpoint,
        allow_broad=True,
    )
