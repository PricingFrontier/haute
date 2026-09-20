"""Polars streaming helpers shared across execution paths."""

from __future__ import annotations

import contextvars
import math
import shutil
import tempfile
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Collection, Generator, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any, Literal, TypeVar, cast

import polars as pl
from polars.io.plugins import register_io_source

from haute._execution_context import (
    ExecutionContext,
    ExecutionProfile,
    current_execution_context,
)
from haute._hashing import HashingWriter
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


def projected_or_carrier_columns(
    schema_columns: Sequence[str],
    demanded: Collection[str],
) -> list[str]:
    """Return schema-ordered demanded columns, keeping one carrier when empty.

    Polars collapses a zero-column select to zero rows. An exact empty logical
    demand means "rows only", so the first schema column is retained to
    preserve the frame's height.
    """
    selected = [column for column in schema_columns if column in demanded]
    if selected or demanded or not schema_columns:
        return selected
    return [schema_columns[0]]


def streaming_collect(
    lf: pl.LazyFrame,
    *,
    execution_context: ExecutionContext | None = None,
) -> pl.DataFrame:
    """Collect once through streaming, with polling when a context is active."""
    return execution_collect(
        lf,
        execution_context=execution_context,
        engine="streaming",
    )


def _cancellable_collect(
    lf: pl.LazyFrame,
    *,
    execution_context: ExecutionContext,
    engine: Literal["auto", "streaming", "in-memory"],
    poll_seconds: float = 0.01,
) -> pl.DataFrame:
    """Collect in the background while propagating request cancellation."""

    if (
        isinstance(poll_seconds, bool)
        or not isinstance(poll_seconds, (int, float))
        or not math.isfinite(poll_seconds)
        or poll_seconds <= 0
    ):
        raise ValueError("poll_seconds must be a positive finite number")

    checkpoint_label = (
        "streaming_collect_before_native" if engine == "streaming" else "auto_collect_before_native"
    )
    poll_label = "streaming_collect_poll" if engine == "streaming" else "auto_collect_poll"
    execution_context.checkpoint(label=checkpoint_label)
    execution_context.fault_point("collect_before_native")
    execution_context.record_collect()
    query = lf.collect(engine=engine, background=True)
    while True:
        try:
            result = query.fetch()
        except pl.exceptions.ComputeError as exc:
            _reraise_python_scan_failure(exc)
            raise
        if result is not None:
            return result
        try:
            execution_context.checkpoint(label=poll_label)
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


def cancellable_streaming_collect(
    lf: pl.LazyFrame,
    *,
    execution_context: ExecutionContext,
    poll_seconds: float = 0.01,
) -> pl.DataFrame:
    """Collect through Polars streaming while propagating native cancellation."""
    return _cancellable_collect(
        lf,
        execution_context=execution_context,
        engine="streaming",
        poll_seconds=poll_seconds,
    )


def execution_collect(
    lf: pl.LazyFrame,
    *,
    execution_context: ExecutionContext | None = None,
    engine: Literal["auto", "streaming", "in-memory"] = "auto",
    poll_seconds: float = 0.01,
) -> pl.DataFrame:
    """Collect with native cancellation whenever an execution context is active."""
    if engine not in {"auto", "streaming", "in-memory"}:
        raise ValueError("engine must be 'auto', 'streaming' or 'in-memory'")
    context = execution_context or current_execution_context()
    if context is None:
        try:
            return lf.collect(engine=engine)
        except pl.exceptions.ComputeError as exc:
            _reraise_python_scan_failure(exc)
            raise
    return _cancellable_collect(
        lf,
        execution_context=context,
        engine=engine,
        poll_seconds=poll_seconds,
    )


def bounded_collect_batches(
    lf: pl.LazyFrame,
    *,
    chunk_size: int,
    maintain_order: bool = False,
    execution_context: ExecutionContext | None = None,
    stage_name: str = "collect_batches",
    node_id: str | None = None,
) -> Iterator[pl.DataFrame]:
    """Yield batches of at most ``chunk_size`` rows, never more than one ahead.

    Polars applies no backpressure to ``sink_batches``, ``collect_batches``,
    or a Python source: a consumer slower than the engine let it materialise
    the whole frame (8 GiB for a 10M-row, 60-column frame). Each batch is
    therefore its own query, issued only when the consumer asks for it:

    - sliced — ``lf`` can be sliced at its input (``haute._chunked_writes.
      sliceable``): each batch is ``lf.slice(offset, chunk_size)``. A failure
      in batch k raises after batches 0..k-1; closing early reads no more.
    - in-memory — every input is a frame the caller already holds: ``lf`` is
      collected once and sliced.
    - staged — otherwise ``lf`` is written once, by the chunked writer, into
      a private temporary directory, and its parts are sliced. A failure
      raises before any batch; the directory is removed on exhaustion, close,
      or failure.

    Batches follow the frame's order in every strategy (``maintain_order`` is
    always honoured). An engine failure always raises; it is never read as
    the end of the stream.
    """
    from haute._chunked_writes import (
        part_paths,
        reads_only_memory,
        scan_parts,
        sliceable,
        write_parts,
    )

    del maintain_order  # every strategy keeps the frame's order
    if not isinstance(chunk_size, int) or isinstance(chunk_size, bool) or chunk_size <= 0:
        raise ValueError("chunk_size must be a positive integer")
    metrics_context = execution_context or current_execution_context()
    if metrics_context is not None:
        metrics_context.fault_point("collect_before_native", node_id=node_id)
    staging: Path | None = None
    try:
        if metrics_context is not None:
            metrics_context.checkpoint(label="before_collect_batches", node_id=node_id)
        source: pl.LazyFrame | pl.DataFrame
        if sliceable(lf):
            source = lf
        elif reads_only_memory(lf):
            source = execution_collect(lf, execution_context=metrics_context)
        else:
            staging = Path(tempfile.mkdtemp(prefix="haute-batches-"))
            write_parts(
                staging,
                lf,
                chunk_rows=chunk_size,
                fast_checkpoint=True,
                execution_context=metrics_context,
                node_id=node_id,
            )
            source = scan_parts(part_paths(staging))
        if isinstance(source, pl.DataFrame):
            total = source.height
        else:
            total = int(
                execution_collect(source.select(pl.len()), execution_context=metrics_context).item()
            )
        for offset in range(0, total, chunk_size):
            if isinstance(source, pl.DataFrame):
                batch = source.slice(offset, chunk_size)
            elif metrics_context is not None:
                with metrics_context.stage(stage_name, node_id=node_id):
                    batch = execution_collect(
                        source.slice(offset, chunk_size), execution_context=metrics_context
                    )
            else:
                batch = execution_collect(source.slice(offset, chunk_size))
            if metrics_context is not None:
                metrics_context.checkpoint(label="after_collect_batch", node_id=node_id)
            yield batch
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)


# Polars reports an exception raised inside a Python scan source as a
# ``ComputeError`` carrying only its text, which would turn a typed Haute error
# (feature mismatch, cancellation, memory limit) into an anonymous engine error.
# The source parks the original under a token named in the message, and every
# collect helper above hands the original back to its caller.
_PYTHON_SCAN_FAILURE_MARKER = "haute-python-scan-failure:"
_PYTHON_SCAN_FAILURE_LIMIT = 64
_python_scan_failures: OrderedDict[str, BaseException] = OrderedDict()
_python_scan_failures_lock = threading.Lock()


def _park_python_scan_failure(exc: BaseException) -> str:
    token = uuid.uuid4().hex
    with _python_scan_failures_lock:
        _python_scan_failures[token] = exc
        while len(_python_scan_failures) > _PYTHON_SCAN_FAILURE_LIMIT:
            _python_scan_failures.popitem(last=False)
    return f"{_PYTHON_SCAN_FAILURE_MARKER}{token}"


def _reraise_python_scan_failure(exc: pl.exceptions.ComputeError) -> None:
    """Re-raise the original exception a Python scan source parked, if any."""
    message = str(exc)
    start = message.find(_PYTHON_SCAN_FAILURE_MARKER)
    if start < 0:
        return
    token_start = start + len(_PYTHON_SCAN_FAILURE_MARKER)
    token = message[token_start : token_start + 32]
    with _python_scan_failures_lock:
        original = _python_scan_failures.pop(token, None)
    if original is not None:
        raise original


def row_local_python_scan(
    input_lf: pl.LazyFrame,
    transform: Callable[[pl.DataFrame], pl.DataFrame],
    *,
    schema: pl.Schema,
    generated_columns: Collection[str],
    required_input_columns: Collection[str] | None,
    input_predicates_allowed: bool,
    elide_transform_when_unused: bool,
    input_schema: pl.Schema | None = None,
    execution_context: ExecutionContext | None = None,
    node_id: str | None = None,
) -> pl.LazyFrame:
    """Expose a row-local Python transform to Polars as a scan source.

    Polars treats a Python UDF (``map_batches``) as opaque: no slice passes
    below it, so a ``.head(n)`` downstream still computes the transform over
    every input row. A registered IO source is a scan instead, and the
    optimiser hands it the row limit, projection, and predicate it could push
    down — and only where doing so leaves the query result unchanged (a
    downstream filter, aggregation, window, or join lookup side withholds the
    limit). Polars' scan contract reads ``n_rows`` source rows, then applies
    the predicate, then the projection.

    ``transform`` must be row-local: every output row derives only from the
    input row at the same position, and the output keeps the input's height
    and order. That makes the first ``n_rows`` input rows exactly the source of
    the first ``n_rows`` output rows, so the limit always caps ``input_lf``.
    ``schema`` is the transform's exact output schema and ``generated_columns``
    the columns the transform adds or replaces; every other output column is an
    input column carried through unchanged.

    ``required_input_columns`` are the input columns the transform reads
    (``None``: every input column). A pushed projection narrows the input to
    the requested carried columns plus these. ``input_predicates_allowed``
    lets a pushed predicate that references no generated column filter the
    input before the transform; otherwise every read row is transformed and the
    predicate filters the transformed rows — required when the transform
    validates rows that a downstream filter must not hide.
    ``elide_transform_when_unused`` skips the transform for a read that needs no
    generated column (a count, or a projection of carried columns only); refuse
    it when the transform validates rows. ``input_schema`` lets a caller that
    already threads the input schema avoid re-planning ``input_lf``.
    """
    context = execution_context or current_execution_context()
    caller_context = contextvars.copy_context()
    generated = frozenset(generated_columns)
    if input_schema is None:
        input_schema = input_lf.collect_schema()
    mismatched_carried = sorted(
        name
        for name, dtype in schema.items()
        if name not in generated and input_schema.get(name) != dtype
    )
    if mismatched_carried:
        raise ValueError(
            f"non-generated scan columns must be input columns of the same dtype: "
            f"{mismatched_carried}"
        )
    unknown_generated = generated - set(schema.names())
    if unknown_generated:
        raise ValueError(
            f"generated columns are absent from the scan schema: {sorted(unknown_generated)}"
        )
    input_names = input_schema.names()
    required = frozenset(input_names if required_input_columns is None else required_input_columns)
    unknown_required = required - set(input_names)
    if unknown_required:
        raise ValueError(
            f"required input columns are absent from the input: {sorted(unknown_required)}"
        )

    def frames(
        with_columns: list[str] | None,
        predicate: pl.Expr | None,
        n_rows: int | None,
        batch_size: int | None,
    ) -> Iterator[pl.DataFrame]:
        source_lf = input_lf if n_rows is None else input_lf.head(n_rows)
        output_predicate = predicate
        if (
            predicate is not None
            and input_predicates_allowed
            and generated.isdisjoint(predicate.meta.root_names())
        ):
            source_lf = source_lf.filter(predicate)
            output_predicate = None
        output_roots = (
            set() if output_predicate is None else set(output_predicate.meta.root_names())
        )
        transform_needed = (
            not elide_transform_when_unused
            or with_columns is None
            or not generated.isdisjoint(with_columns)
            or not generated.isdisjoint(output_roots)
        )
        if with_columns is not None:
            keep = (set(with_columns) | output_roots) - generated
            if transform_needed:
                keep |= required
            source_lf = source_lf.select(projected_or_carrier_columns(input_names, keep))
        for batch in bounded_collect_batches(
            source_lf,
            chunk_size=batch_size or DEFAULT_STREAMING_CHUNK_SIZE,
            maintain_order=True,
            execution_context=context,
            stage_name="row_local_python_scan",
            node_id=node_id,
        ):
            frame = transform(batch) if transform_needed else batch
            if output_predicate is not None:
                frame = frame.filter(output_predicate)
            if with_columns is not None:
                frame = frame.select(with_columns)
            yield frame

    return _register_python_scan(frames, schema=schema, caller_context=caller_context)


_ScanFrames = Callable[
    [list[str] | None, pl.Expr | None, int | None, int | None],
    Iterator[pl.DataFrame],
]


def _register_python_scan(
    frames: _ScanFrames,
    *,
    schema: pl.Schema,
    caller_context: contextvars.Context,
) -> pl.LazyFrame:
    """Register *frames* as a Polars IO source run in the caller's context."""

    def io_source(
        with_columns: list[str] | None,
        predicate: pl.Expr | None,
        n_rows: int | None,
        batch_size: int | None,
    ) -> Iterator[pl.DataFrame]:
        # Polars may call the source on an engine thread; run it with the
        # caller's context variables (execution context, scenario, temp scope).
        run_context = caller_context.copy()
        iterator = run_context.run(frames, with_columns, predicate, n_rows, batch_size)
        while True:
            try:
                frame = run_context.run(next, iterator)
            except StopIteration:
                return
            except BaseException as exc:
                raise RuntimeError(
                    f"{_park_python_scan_failure(exc)} {type(exc).__name__}: {exc}"
                ) from exc
            yield frame

    return register_io_source(io_source, schema=schema)


def limited_python_scan(
    produce: Callable[[int | None], pl.DataFrame],
    *,
    schema: pl.Schema,
) -> pl.LazyFrame:
    """Expose a Python computation that can produce just its first rows.

    ``produce(n)`` must return a frame whose first ``n`` rows equal the first
    ``n`` rows of ``produce(None)``, reading only what those rows need; it may
    return fewer. Polars passes ``n`` only where a limit above the scan leaves
    the query result unchanged. Output columns are cast to ``schema``, so a
    computation that emits different columns or dtypes fails loudly.
    """
    caller_context = contextvars.copy_context()
    declared = [pl.col(name).cast(dtype, strict=True) for name, dtype in schema.items()]

    def frames(
        with_columns: list[str] | None,
        predicate: pl.Expr | None,
        n_rows: int | None,
        batch_size: int | None,
    ) -> Iterator[pl.DataFrame]:
        frame = produce(n_rows).select(declared)
        if n_rows is not None:
            frame = frame.head(n_rows)
        if predicate is not None:
            frame = frame.filter(predicate)
        if with_columns is not None:
            frame = frame.select(with_columns)
        yield frame

    return _register_python_scan(frames, schema=schema, caller_context=caller_context)


def key_prefix_python_scan(
    input_lf: pl.LazyFrame,
    apply: Callable[[pl.LazyFrame], pl.DataFrame],
    *,
    schema: pl.Schema,
    key_column: str,
    execution_context: ExecutionContext | None = None,
) -> pl.LazyFrame:
    """Expose a per-key Python computation to Polars as a scan source.

    ``apply`` turns the input rows of any set of keys into exactly one output
    row per non-null key, independently of every other key, ordered by the key
    cast to ``Utf8``. A pushed row limit ``n`` therefore reads only the key
    column to find the first ``n`` keys in that order and applies to the input
    rows of those keys alone; the key predicate reaches the input's own scans
    and row-local Python scans. Without a limit ``apply`` sees every input row.
    """
    context = execution_context or current_execution_context()
    key = pl.col(key_column).cast(pl.Utf8)

    def produce(n_rows: int | None) -> pl.DataFrame:
        if n_rows is None:
            return apply(input_lf)
        keys = streaming_collect(
            input_lf.select(key.alias(key_column))
            .drop_nulls()
            .unique()
            .sort(key_column)
            .head(n_rows),
            execution_context=context,
        )
        if keys.height == 0:
            return pl.DataFrame(schema=schema)
        return apply(input_lf.filter(key.is_in(keys[key_column].to_list())))

    return limited_python_scan(produce, schema=schema)


def _checkpoint_compression(fast_checkpoint: bool) -> Literal["lz4", "zstd"]:
    return "lz4" if fast_checkpoint else "zstd"


def _streaming_sink_to_path(
    lf: pl.LazyFrame,
    target: Path | IO[bytes],
    *,
    fmt: str,
    compression: Literal["lz4", "zstd"],
) -> None:
    if fmt == "csv":
        sink_plan = lf.sink_csv(target, lazy=True, engine="streaming")
    else:
        sink_plan = lf.sink_parquet(
            target,
            compression=compression,
            lazy=True,
            engine="streaming",
        )
    if sink_plan is None:
        raise RuntimeError("Polars did not return the requested lazy sink plan")
    execution_collect(
        sink_plan,
        engine="streaming",
    )


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


_T = TypeVar("_T")


def _write_atomically_if_possible(path: Path, writer: Callable[[Path], _T]) -> _T:
    if path.parent.exists():
        with atomic_write(path) as tmp:
            return writer(tmp)
    return writer(path)


def current_streaming_chunk_size() -> int:
    """The chunk size the current request runs under, else the default.

    A request's ``streaming_chunk_size`` is applied through
    :func:`temporary_streaming_chunk_size`; chunked writes follow it.
    """
    raw = pl.Config.state(if_set=True).get("POLARS_STREAMING_CHUNK_SIZE")
    try:
        value = int(raw) if raw else 0
    except (TypeError, ValueError):
        value = 0
    return value if value > 0 else DEFAULT_STREAMING_CHUNK_SIZE


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
    atomic: bool = True,
) -> None:
    """Sink a LazyFrame with Polars streaming and no eager fallback.

    ``atomic=False`` writes straight to ``path``, for a caller already writing
    to a staging file it will rename or discard itself: a second temporary
    would leave a hidden sibling in a directory that caller governs and does
    not sweep.
    """
    path = Path(path)
    compression = _checkpoint_compression(fast_checkpoint)

    def _do_sink(target: Path) -> None:
        _streaming_sink_to_path(lf, target, fmt=fmt, compression=compression)

    if atomic:
        _write_atomically_if_possible(path, _do_sink)
    else:
        _do_sink(path)


def _bounded_sink_execute(
    path: Path,
    streaming_chunk_size: int | None,
    writer: Callable[[], _T],
) -> _T:
    metrics_context = current_execution_context()
    if metrics_context is not None:
        metrics_context.fault_point("sink_before_native")
    with temporary_streaming_chunk_size(streaming_chunk_size):
        result = writer()
    if metrics_context is not None:
        metrics_context.fault_point("sink_after_native")
        metrics_context.record_bytes_written(path.stat().st_size)
    return result


def bounded_sink(
    lf: pl.LazyFrame,
    path: str | Path,
    *,
    fmt: str = "parquet",
    fast_checkpoint: bool = False,
    streaming_chunk_size: int | None = None,
    atomic: bool = True,
) -> None:
    """Sink a LazyFrame through the native streaming API."""
    path = Path(path)
    _bounded_sink_execute(
        path,
        streaming_chunk_size,
        lambda: streaming_sink(lf, path, fmt=fmt, fast_checkpoint=fast_checkpoint, atomic=atomic),
    )


def _sink_parquet_hashing(
    lf: pl.LazyFrame,
    target: Path,
    *,
    compression: Literal["lz4", "zstd"],
) -> str:
    with HashingWriter(open(target, "wb")) as writer:
        _streaming_sink_to_path(
            lf, cast("IO[bytes]", writer), fmt="parquet", compression=compression
        )
    return writer.hexdigest()


def hashed_streaming_sink(
    lf: pl.LazyFrame,
    path: str | Path,
    *,
    fast_checkpoint: bool = False,
) -> str:
    """Sink a LazyFrame with write-time hashing and Polars streaming."""
    path = Path(path)
    compression = _checkpoint_compression(fast_checkpoint)
    return _write_atomically_if_possible(
        path,
        lambda target: _sink_parquet_hashing(lf, target, compression=compression),
    )


def bounded_hashed_sink(
    lf: pl.LazyFrame,
    path: str | Path,
    *,
    fast_checkpoint: bool = False,
    streaming_chunk_size: int | None = None,
) -> str:
    """Sink a LazyFrame through the native streaming API, hashing during write."""
    path = Path(path)
    return _bounded_sink_execute(
        path,
        streaming_chunk_size,
        lambda: hashed_streaming_sink(lf, path, fast_checkpoint=fast_checkpoint),
    )


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
