"""Schema-aware JSON flattening for tabular data.

Provides tools to:

- **Infer** a flatten schema from sample JSON data
- **Flatten** nested JSON dicts into single-row tabular dicts
- **Load** samples from ``.json`` (single object or array) and ``.jsonl``

The schema describes the expected structure of the JSON data, including
nested objects and arrays with a maximum item count.  The :func:`flatten`
function walks the *schema* (not the data) to produce a consistent column
set regardless of what data is present.

Column names use dot-separated paths (e.g. ``proposer.licence.licence_type``).
Array indices are **1-based** (e.g. ``additional_drivers.1.first_name``).
"""

from __future__ import annotations

import gc
import hashlib
import os
import shutil
import threading
import time
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict, cast

import orjson

from haute._json_flatten_schema import (
    JsonFlattenSchemaError,
    _infer_schema_node,
    _infer_type,
    _merge_schema_nodes,
    _schema_leaf_types,
    _validate_flatten_schema,
    _wider_type,
    flatten,
    infer_schema,
    schema_columns,
)
from haute._logging import get_logger
from haute._polars_utils import _malloc_trim
from haute.errors import HauteError

if TYPE_CHECKING:
    import polars as pl
    import pyarrow as pa

logger = get_logger(component="json_flatten")

__all__ = [
    "JsonCacheCancelledError",
    "JsonFlattenDataError",
    "JsonFlattenSchemaError",
    "_infer_type",
    "_wider_type",
    "_schema_leaf_types",
    "flatten",
    "infer_schema",
    "schema_columns",
]

# ---------------------------------------------------------------------------
# Large-file threshold, streaming constants & progress tracking
# ---------------------------------------------------------------------------

_LARGE_FILE_THRESHOLD = 50 * 1024 * 1024  # 50 MB
_SCHEMA_SAMPLE_SIZE = 10_000  # rows sampled for streaming schema inference
_FLATTEN_CHUNK_SIZE = 50_000  # maximum rows per parquet row-group
_TARGET_CHUNK_BYTES = 256 * 1024 * 1024  # 256 MB target memory per chunk
_MIN_CHUNK_ROWS = 1_000
_BYTES_PER_CELL = 100  # rough estimate: avg bytes per cell in memory
_RAW_CHUNK_TARGET_BYTES = 128 * 1024 * 1024  # 128 MB raw JSON per chunk (step 1)

# Thread-safe progress tracking for active cache builds, keyed by data_path.
_flatten_progress: dict[str, dict[str, object]] = {}
_flatten_lock = threading.Lock()

# Cancellation tokens: one Event per active build, keyed by data_path.
_cancel_events: dict[str, threading.Event] = {}


# -- Test helpers for flatten progress state ---------------------------------


class JsonCacheCancelledError(HauteError):
    """Raised when a JSON cache build is cancelled by the user."""


class JsonFlattenDataError(HauteError):
    """Raised when JSON input data cannot be flattened safely."""


def cancel_json_cache(data_path: str) -> bool:
    """Signal cancellation for an active build. Returns True if a build was active."""
    with _flatten_lock:
        event = _cancel_events.get(data_path)
        if event is not None:
            event.set()
            _flatten_progress.pop(data_path, None)
            return True
        return False


def _check_cancelled(event: threading.Event | None, data_path: str) -> None:
    """Raise JsonCacheCancelledError if the event is set."""
    if event is not None and event.is_set():
        raise JsonCacheCancelledError(f"Cache build cancelled for {data_path}")


def _set_flatten_progress(data_path: str, data: dict[str, object]) -> None:
    """Set flatten progress for *data_path* (test helper)."""
    with _flatten_lock:
        _flatten_progress[data_path] = data


def _clear_flatten_progress() -> None:
    """Clear all flatten progress entries (test helper)."""
    with _flatten_lock:
        _flatten_progress.clear()


def _update_progress(
    key: str | None,
    t0: float | None,
    rows: int,
    phase: str = "",
) -> None:
    """Thread-safe update of the flatten progress dict for *key*."""
    if key is None or t0 is None:
        return
    entry: dict[str, object] = {
        "rows": rows,
        "elapsed": round(time.monotonic() - t0, 1),
    }
    if phase:
        entry["phase"] = phase
    with _flatten_lock:
        _flatten_progress[key] = entry


def _clear_cancel_events() -> None:
    """Clear all cancel events (test helper)."""
    with _flatten_lock:
        _cancel_events.clear()


def _adaptive_chunk_size(flatten_schema: dict[str, Any]) -> int:
    """Choose chunk size based on schema width to bound memory per chunk.

    Insurance JSON can flatten to 500–2000+ columns.  With a fixed 50k-row
    chunk, a 1000-column schema holds ~5 GB in memory per chunk.  This
    function targets ~256 MB per chunk by scaling rows inversely with
    column count.
    """
    n_cols = len(schema_columns(flatten_schema))
    if n_cols == 0:
        return _FLATTEN_CHUNK_SIZE
    rows = _TARGET_CHUNK_BYTES // (n_cols * _BYTES_PER_CELL)
    return max(_MIN_CHUNK_ROWS, min(_FLATTEN_CHUNK_SIZE, rows))


# ---------------------------------------------------------------------------
# Type inference helpers
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Schema inference
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Schema-aware flattening
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Streaming record iterator
# ---------------------------------------------------------------------------


def _iter_json_records(path: Path) -> Iterator[dict[str, Any]]:
    """Yield JSON dicts one at a time from a ``.json`` or ``.jsonl`` file.

    For ``.jsonl`` files this is truly streaming — only one line is held in
    memory at a time.  For ``.json`` files the full parse is unavoidable
    (standard JSON requires it), but records are *yielded* so downstream
    processing can still be chunked.
    """
    if path.suffix == ".jsonl":
        skipped = 0
        total = 0
        with open(path, "rb") as fh:
            for line in fh:
                stripped = line.strip()
                if not stripped:
                    continue
                total += 1
                try:
                    obj = orjson.loads(stripped)
                except orjson.JSONDecodeError:
                    skipped += 1
                    continue
                if isinstance(obj, dict):
                    yield obj
        if skipped:
            logger.warning("jsonl_lines_skipped", count=skipped, total=total)
    else:
        try:
            data = orjson.loads(path.read_bytes())
        except orjson.JSONDecodeError as exc:
            raise JsonFlattenDataError("Invalid JSON file", path=str(path)) from exc
        if isinstance(data, list):
            for index, item in enumerate(data):
                if not isinstance(item, dict):
                    raise JsonFlattenDataError(
                        "JSON array items must be objects",
                        path=str(path),
                        index=index,
                    )
                yield item
        elif isinstance(data, dict):
            yield data
        else:
            raise JsonFlattenDataError("JSON root must be an object or array", path=str(path))


def _infer_schema_streaming(
    path: Path,
    max_samples: int = _SCHEMA_SAMPLE_SIZE,
) -> dict[str, Any]:
    """Infer a flatten schema by sampling the first *max_samples* records.

    Unlike :func:`infer_schema` this never holds the full file in memory —
    it streams records and merges schemas on the fly, stopping after
    *max_samples* rows.
    """
    schema: dict[str, Any] | str = {}
    count = 0
    for record in _iter_json_records(path):
        schema = _merge_schema_nodes(schema, _infer_schema_node(record))
        count += 1
        if count >= max_samples:
            break
    logger.info("schema_inferred_streaming", path=str(path), samples=count)
    return schema if isinstance(schema, dict) else {}


# ---------------------------------------------------------------------------
# Arrow schema helpers
# ---------------------------------------------------------------------------


def _arrow_schema_from_flatten(flatten_schema: dict[str, Any]) -> pa.Schema:
    """Build a PyArrow ``Schema`` from a flatten schema.

    JSON numbers are inherently ambiguous (``1`` could be int or float),
    so both ``"int"`` and ``"float"`` map to ``float64`` for safety.  This
    avoids mid-stream cast failures when a field inferred as int from the
    sample turns out to contain floats in later records.
    """
    import pyarrow as pa

    _dtype_map: dict[str, pa.DataType] = {
        "bool": pa.bool_(),
        "int": pa.float64(),
        "float": pa.float64(),
        "str": pa.string(),
    }
    return pa.schema(
        [
            pa.field(name, _dtype_map.get(dtype, pa.string()), nullable=True)
            for name, dtype in _schema_leaf_types(flatten_schema)
        ]
    )


# ---------------------------------------------------------------------------
# Chunked streaming flatten → parquet writer (fallback for Polars failures)
# ---------------------------------------------------------------------------


def _chunked(
    iterator: Iterator[dict[str, Any]],
    size: int,
) -> Iterator[list[dict[str, Any]]]:
    """Yield lists of up to *size* items from *iterator*."""
    chunk: list[dict[str, Any]] = []
    for item in iterator:
        chunk.append(item)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def _coerce_to_arrow(values: list[Any], target_type: pa.DataType) -> pa.Array:
    """Build an Arrow array, coercing values that don't match *target_type*.

    Fast path: ``pa.array(values, type=target_type)`` — handles the vast
    majority of data.  Slow path: per-value coercion for mixed-type edges.
    """
    import pyarrow as pa

    try:
        return pa.array(values, type=target_type)
    except (pa.ArrowInvalid, pa.ArrowTypeError, pa.ArrowNotImplementedError, TypeError, ValueError):
        pass

    if target_type == pa.float64():
        coerced: list[float | None] = []
        for v in values:
            if v is None:
                coerced.append(None)
            elif isinstance(v, (int, float)):
                coerced.append(float(v))
            else:
                try:
                    coerced.append(float(v))
                except (ValueError, TypeError):
                    coerced.append(None)
        return pa.array(coerced, type=pa.float64())

    if target_type == pa.bool_():
        return pa.array(
            [v if isinstance(v, bool) else None for v in values],
            type=pa.bool_(),
        )

    # String fallback — always succeeds
    return pa.array(
        [str(v) if v is not None else None for v in values],
        type=pa.string(),
    )


def _rows_to_batch(
    rows: list[dict[str, Any]],
    arrow_schema: pa.Schema,
) -> pa.RecordBatch:
    """Convert flattened row-dicts into an Arrow ``RecordBatch``."""
    import pyarrow as pa

    arrays = [
        _coerce_to_arrow(
            [row.get(field.name) for row in rows],
            field.type,
        )
        for field in arrow_schema
    ]
    return pa.record_batch(arrays, schema=arrow_schema)


# -- Core streaming writer (fallback) ----------------------------------------


def _flatten_and_write_streaming(
    records_iter: Iterator[dict[str, Any]],
    flatten_schema: dict[str, Any],
    cache_path: Path,
    *,
    chunk_size: int | None = None,
    progress_key: str | None = None,
    t0: float | None = None,
    cancel_event: threading.Event | None = None,
    parquet_metadata: Mapping[bytes, bytes] | None = None,
) -> int:
    """Stream JSON records through flatten → PyArrow ParquetWriter.

    Uses **column-oriented accumulation**: each record is flattened and its
    values are scattered into per-column lists immediately, so only the
    individual values survive — no list of row-dicts piling up.

    Peak memory per chunk ≈ ``n_cols × chunk_size × value_size``.
    Chunk size adapts to the schema width via :func:`_adaptive_chunk_size`.

    When *parquet_metadata* is supplied, it is embedded in the parquet
    footer KV-metadata for single-file robustness (DUAL_CACHE.md §3).

    Returns the total number of rows written.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    if chunk_size is None:
        chunk_size = _adaptive_chunk_size(flatten_schema)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.with_suffix(".parquet.tmp")
    arrow_schema = _arrow_schema_from_flatten(flatten_schema)
    if parquet_metadata:
        arrow_schema = arrow_schema.with_metadata(dict(parquet_metadata))
    col_names = [f.name for f in arrow_schema]
    n_cols = len(arrow_schema)

    writer: pq.ParquetWriter | None = None
    total_rows = 0
    # Column-oriented accumulation: one list per column
    columns: list[list[Any]] = [[] for _ in range(n_cols)]
    rows_in_chunk = 0

    def _flush() -> None:
        nonlocal writer, total_rows, columns, rows_in_chunk
        if rows_in_chunk == 0:
            return
        arrays = [_coerce_to_arrow(columns[i], arrow_schema.field(i).type) for i in range(n_cols)]
        batch = pa.record_batch(arrays, schema=arrow_schema)
        del arrays
        if writer is None:
            writer = pq.ParquetWriter(
                str(tmp_path),
                arrow_schema,
                compression="zstd",
            )
        writer.write_batch(batch)
        del batch
        total_rows += rows_in_chunk
        # Reset columns for next chunk
        columns = [[] for _ in range(n_cols)]
        rows_in_chunk = 0
        _update_progress(progress_key, t0, total_rows)

    try:
        _check_cancelled(cancel_event, progress_key or str(cache_path))
        for record in records_iter:
            flat = flatten(record, flatten_schema)
            for i, name in enumerate(col_names):
                columns[i].append(flat.get(name))
            rows_in_chunk += 1
            if rows_in_chunk >= chunk_size:
                _flush()
                _check_cancelled(cancel_event, progress_key or str(cache_path))

        _flush()  # remaining rows

        if writer is not None:
            writer.close()
            writer = None
            tmp_path.replace(cache_path)
        else:
            empty = pa.table(
                {f.name: pa.array([], type=f.type) for f in arrow_schema},
                schema=arrow_schema,
            )
            pq.write_table(empty, str(tmp_path), compression="zstd")
            tmp_path.replace(cache_path)

        logger.info(
            "json_cache_written",
            path=str(cache_path),
            rows=total_rows,
            size_bytes=cache_path.stat().st_size,
        )
        return total_rows

    except BaseException:
        if writer is not None:
            writer.close()
        tmp_path.unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------------------
# Polars-native flatten: expression-based (fast path)
# ---------------------------------------------------------------------------


def _build_flatten_exprs(
    schema: dict[str, Any],
    *,
    _base: pl.Expr | None = None,
    _prefix: str = "",
) -> list[pl.Expr]:
    """Build Polars expressions that replicate :func:`flatten` via native ops.

    Walks the flatten schema and produces ``struct.field`` / ``list.get``
    expression chains.  The result is a flat list of aliased expressions
    that can be passed to ``lf.select(exprs)`` to flatten nested
    structs and lists into dot-separated, 1-based-index column names.
    """
    import polars as pl

    if _base is None and not _prefix:
        _validate_flatten_schema(schema)

    exprs: list[pl.Expr] = []

    def _build_node(spec: dict[str, Any] | str, expr: pl.Expr, full_key: str) -> None:
        if isinstance(spec, str):
            exprs.append(expr.alias(full_key))
            return

        if "$max" in spec:
            max_items: int = spec["$max"]
            items_schema = spec.get("$items", {})
            if max_items == 0 or not items_schema:
                return
            for i in range(max_items):
                idx_key = f"{full_key}.{i + 1}"
                elem = expr.list.get(i, null_on_oob=True)
                _build_node(items_schema, elem, idx_key)
            return

        for key, child_spec in spec.items():
            child_key = f"{full_key}.{key}" if full_key else key
            _build_node(child_spec, expr.struct.field(key), child_key)

    if _base is not None:
        _build_node(schema, _base, _prefix)
    else:
        for key, spec in schema.items():
            full_key = f"{_prefix}.{key}" if _prefix else key
            _build_node(spec, pl.col(key), full_key)
    return exprs


def _iter_line_chunks(path: Path, chunk_lines: int) -> Iterator[bytes]:
    """Yield byte buffers of up to *chunk_lines* lines from a file.

    Reads one line at a time (streaming) so only the current chunk is
    held in memory.
    """
    with open(path, "rb") as fh:
        buf: list[bytes] = []
        for line in fh:
            buf.append(line)
            if len(buf) >= chunk_lines:
                chunk = b"".join(buf)
                buf = []  # free line refs before yielding
                yield chunk
        if buf:
            chunk = b"".join(buf)
            buf = []
            yield chunk


# ---------------------------------------------------------------------------
# Two-step streaming: JSONL → raw Parquet → flattened Parquet
#
# Step 1 (_jsonl_to_raw_parquet): parse JSONL in chunks via Polars/simd-json,
#   write nested structs/lists to an intermediate Parquet file.  All memory
#   lives in Arrow buffers (outside Python's heap) and is freed between chunks.
#
# Step 2 (_flatten_raw_parquet): read the intermediate Parquet one row group
#   at a time, flatten via Polars expressions (or Python fallback), and write
#   the final flat Parquet.
#
# This avoids the pymalloc fragmentation that occurs when millions of small
# Python dicts are created and freed during a single-step Python flatten.
# ---------------------------------------------------------------------------


def _release_memory() -> None:
    """Force the OS to reclaim freed pages.

    Runs ``gc.collect()`` then delegates to :func:`_malloc_trim` for
    platform-specific heap compaction (Linux ``malloc_trim``, Windows
    ``_heapmin``, macOS no-op).
    """
    gc.collect()
    _malloc_trim()


def _iter_byte_chunks(path: Path, buffer_size: int) -> Iterator[bytes]:
    """Yield complete-line byte buffers of approximately *buffer_size* bytes.

    Unlike :func:`_iter_line_chunks` (which creates one Python ``bytes``
    per line), this reads large blocks via ``file.read()``.  Blocks above
    ~512 KB are backed by ``mmap`` and returned to the OS on ``del``,
    avoiding pymalloc arena fragmentation on multi-million-row files.
    """
    with open(path, "rb") as fh:
        remainder = b""
        while True:
            block = fh.read(buffer_size)
            if not block:
                if remainder:
                    yield remainder
                break
            block = remainder + block
            last_nl = block.rfind(b"\n")
            if last_nl == -1:
                remainder = block
                continue
            yield block[: last_nl + 1]
            remainder = block[last_nl + 1 :]


def _jsonl_has_nonblank_bytes(path: Path) -> bool:
    """Return True once a JSONL file contains any non-whitespace byte."""
    with open(path, "rb") as fh:
        while block := fh.read(1024 * 1024):
            if block.strip():
                return True
    return False


def _iter_json_records_strict(path: Path) -> Iterator[dict[str, Any]]:
    """Yield JSONL records and fail loudly on malformed or non-object rows."""
    if path.suffix != ".jsonl":
        yield from _iter_json_records(path)
        return

    with open(path, "rb") as fh:
        for line_number, line in enumerate(fh, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                obj = orjson.loads(stripped)
            except orjson.JSONDecodeError as exc:
                raise JsonFlattenDataError(
                    "Invalid JSONL file",
                    path=str(path),
                    line=line_number,
                ) from exc
            if not isinstance(obj, dict):
                raise JsonFlattenDataError(
                    "JSONL records must be objects",
                    path=str(path),
                    line=line_number,
                )
            yield obj


def _validate_jsonl_records(path: Path) -> None:
    for _ in _iter_json_records_strict(path):
        pass


def _arrow_type_can_represent_flatten_node(
    arrow_type: Any | None,
    spec: dict[str, Any] | str,
) -> bool:
    import pyarrow as pa

    if isinstance(spec, str):
        return arrow_type is not None and not (
            pa.types.is_struct(arrow_type)
            or pa.types.is_list(arrow_type)
            or pa.types.is_large_list(arrow_type)
            or pa.types.is_fixed_size_list(arrow_type)
        )

    if "$max" in spec:
        max_items: int = spec["$max"]
        items_schema = spec.get("$items", {})
        if max_items == 0 or not items_schema:
            return True
        if arrow_type is None or not (
            pa.types.is_list(arrow_type)
            or pa.types.is_large_list(arrow_type)
            or pa.types.is_fixed_size_list(arrow_type)
        ):
            return False
        return _arrow_type_can_represent_flatten_node(arrow_type.value_type, items_schema)

    if arrow_type is None or not pa.types.is_struct(arrow_type):
        return False

    child_types = {field.name: field.type for field in arrow_type}
    return all(
        _arrow_type_can_represent_flatten_node(child_types.get(key), child_spec)
        for key, child_spec in spec.items()
    )


def _raw_parquet_can_represent_flatten_schema(
    raw_path: Path,
    flatten_schema: dict[str, Any],
) -> bool:
    import pyarrow.parquet as pq

    pf = pq.ParquetFile(str(raw_path))
    if pf.metadata.num_rows == 0:
        return True
    raw_types = {field.name: field.type for field in pf.schema_arrow}
    return all(
        _arrow_type_can_represent_flatten_node(raw_types.get(key), spec)
        for key, spec in flatten_schema.items()
    )


def _jsonl_to_raw_parquet(
    path: Path,
    dest: Path,
    *,
    progress_key: str | None = None,
    t0: float | None = None,
    cancel_event: threading.Event | None = None,
) -> int:
    """Step 1: Stream JSONL → raw (nested) Parquet via Polars chunks.

    All heavy memory (simd-json parse, Arrow buffers) lives outside
    Python's heap and is freed between chunks.

    Returns total rows written.
    """
    import io

    import polars as pl
    import pyarrow.parquet as pq

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(dest) + ".tmp")

    _check_cancelled(cancel_event, progress_key or str(dest))

    if not _jsonl_has_nonblank_bytes(path):
        import pyarrow as pa

        pq.write_table(pa.table({}), str(tmp), compression="zstd")
        tmp.replace(dest)
        return 0

    # Infer a consistent schema from a large sample so all chunks
    # parse with identical types (avoids ParquetWriter schema mismatches).
    # Malformed JSONL must fail loudly; otherwise an invalid upload can look
    # like a successful empty cache in the UI.
    try:
        ndjson_schema = pl.scan_ndjson(
            path,
            infer_schema_length=_SCHEMA_SAMPLE_SIZE,
        ).collect_schema()
    except pl.exceptions.ComputeError as exc:
        tmp.unlink(missing_ok=True)
        try:
            _validate_jsonl_records(path)
        except JsonFlattenDataError as data_exc:
            raise data_exc from exc
        raise

    writer: pq.ParquetWriter | None = None
    total_rows = 0

    try:
        for chunk_bytes in _iter_byte_chunks(path, _RAW_CHUNK_TARGET_BYTES):
            _check_cancelled(cancel_event, progress_key or str(dest))
            df = pl.read_ndjson(io.BytesIO(chunk_bytes), schema=ndjson_schema)
            del chunk_bytes
            at = df.to_arrow()
            n = len(df)
            del df

            if writer is None:
                writer = pq.ParquetWriter(str(tmp), at.schema, compression="zstd")
            writer.write_table(at)
            del at
            _release_memory()

            total_rows += n
            _update_progress(progress_key, t0, total_rows, phase="converting")

        if writer is not None:
            writer.close()
            writer = None
        else:
            import pyarrow as pa

            pq.write_table(pa.table({}), str(tmp), compression="zstd")
        tmp.replace(dest)
        return total_rows

    except pl.exceptions.ComputeError as exc:
        if writer is not None:
            writer.close()
        tmp.unlink(missing_ok=True)
        try:
            _validate_jsonl_records(path)
        except JsonFlattenDataError as data_exc:
            raise data_exc from exc
        raise
    except BaseException:
        if writer is not None:
            writer.close()
        tmp.unlink(missing_ok=True)
        raise


def _flatten_raw_parquet(
    raw_path: Path,
    flatten_schema: dict[str, Any],
    dest: Path,
    *,
    progress_key: str | None = None,
    t0: float | None = None,
    cancel_event: threading.Event | None = None,
    parquet_metadata: Mapping[bytes, bytes] | None = None,
) -> int:
    """Step 2: Stream raw Parquet → flattened Parquet, one row group at a time.

    Tries Polars expression-based flatten first (fast, Arrow memory).
    Falls back to Python ``flatten()`` per row group if expressions fail
    (e.g. deeply nested mixed-type schemas).

    When *parquet_metadata* is supplied, the schema-level KV-metadata is
    embedded in the parquet footer (DUAL_CACHE.md §3 single-file robustness).

    Returns total rows written.
    """
    import polars as pl
    import pyarrow.parquet as pq

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(dest) + ".tmp")

    _check_cancelled(cancel_event, progress_key or str(dest))

    exprs = _build_flatten_exprs(flatten_schema)
    pf = pq.ParquetFile(str(raw_path))
    n_groups = pf.metadata.num_row_groups

    if n_groups == 0 or not exprs:
        cols = schema_columns(flatten_schema)
        empty = pl.DataFrame({c: pl.Series([], dtype=pl.String) for c in cols})
        if parquet_metadata:
            empty_table = empty.to_arrow()
            empty_table = empty_table.replace_schema_metadata(dict(parquet_metadata))
            pq.write_table(empty_table, str(tmp), compression="zstd")
        else:
            empty.write_parquet(str(tmp), compression="zstd")
        tmp.replace(dest)
        return 0

    writer: pq.ParquetWriter | None = None
    total_rows = 0
    use_polars = True

    try:
        # Probe first row group to decide Polars vs Python fallback
        table0 = pf.read_row_group(0)
        df0 = pl.from_arrow(table0)
        assert isinstance(df0, pl.DataFrame)
        del table0
        try:
            flat0 = df0.select(exprs)
        except Exception:
            logger.info("polars_flatten_fallback", path=str(raw_path))
            use_polars = False
            rows = df0.to_dicts()
            flat_rows = [flatten(r, flatten_schema) for r in rows]
            flat0 = pl.from_dicts(flat_rows) if flat_rows else pl.DataFrame()
            del flat_rows, rows
        del df0

        at = flat0.to_arrow()
        if parquet_metadata:
            at = at.replace_schema_metadata(dict(parquet_metadata))
        total_rows = len(flat0)
        del flat0
        writer_schema = at.schema
        writer = pq.ParquetWriter(str(tmp), writer_schema, compression="zstd")
        writer.write_table(at)
        del at
        _release_memory()

        # Remaining row groups
        for i in range(1, n_groups):
            _check_cancelled(cancel_event, progress_key or str(dest))

            table = pf.read_row_group(i)
            df = pl.from_arrow(table)
            assert isinstance(df, pl.DataFrame)
            del table

            if use_polars:
                flat = df.select(exprs)
                del df
            else:
                rows = df.to_dicts()
                del df
                flat_rows = [flatten(r, flatten_schema) for r in rows]
                flat = pl.from_dicts(flat_rows) if flat_rows else pl.DataFrame()
                del flat_rows, rows

            at = flat.to_arrow()
            if parquet_metadata:
                at = at.replace_schema_metadata(dict(parquet_metadata))
            total_rows += len(flat)
            del flat
            writer.write_table(at)
            del at
            _release_memory()

            _update_progress(progress_key, t0, total_rows, phase="flattening")

        writer.close()
        writer = None
        tmp.replace(dest)
        return total_rows

    except BaseException:
        if writer is not None:
            writer.close()
        tmp.unlink(missing_ok=True)
        raise


def _polars_jsonl_limit_errors() -> tuple[type[BaseException], ...]:
    import polars as pl

    names = (
        "SchemaError",
        "ComputeError",
        "ColumnNotFoundError",
        "StructFieldNotFoundError",
        "InvalidOperationError",
    )
    return tuple(getattr(pl.exceptions, name) for name in names if hasattr(pl.exceptions, name))


def _flatten_jsonl_to_cache(
    path: Path,
    flatten_schema: dict[str, Any],
    cache_path: Path,
    *,
    progress_key: str | None = None,
    t0: float | None = None,
    cancel_event: threading.Event | None = None,
    parquet_metadata: Mapping[bytes, bytes] | None = None,
) -> int:
    """Flatten JSONL to cache, using Polars only when it preserves semantics."""
    raw_path = cache_path.with_suffix(".raw.parquet")
    try:
        try:
            _jsonl_to_raw_parquet(
                path,
                raw_path,
                progress_key=progress_key,
                t0=t0,
                cancel_event=cancel_event,
            )
            if not _raw_parquet_can_represent_flatten_schema(raw_path, flatten_schema):
                logger.info("jsonl_polars_raw_schema_unsafe", path=str(path))
                return _flatten_and_write_streaming(
                    _iter_json_records_strict(path),
                    flatten_schema,
                    cache_path,
                    progress_key=progress_key,
                    t0=t0,
                    cancel_event=cancel_event,
                    parquet_metadata=parquet_metadata,
                )
            return _flatten_raw_parquet(
                raw_path,
                flatten_schema,
                cache_path,
                progress_key=progress_key,
                t0=t0,
                cancel_event=cancel_event,
                parquet_metadata=parquet_metadata,
            )
        except JsonFlattenDataError:
            raise
        except JsonCacheCancelledError:
            raise
        except _polars_jsonl_limit_errors() as exc:
            logger.info(
                "jsonl_polars_limit_fallback",
                path=str(path),
                error=type(exc).__name__,
            )
            return _flatten_and_write_streaming(
                _iter_json_records_strict(path),
                flatten_schema,
                cache_path,
                progress_key=progress_key,
                t0=t0,
                cancel_event=cancel_event,
                parquet_metadata=parquet_metadata,
            )
    finally:
        raw_path.unlink(missing_ok=True)


def _polars_flatten_to_parquet(
    path: Path,
    flatten_schema: dict[str, Any],
    cache_path: Path,
    *,
    chunk_lines: int | None = None,
    progress_key: str | None = None,
    t0: float | None = None,
    cancel_event: threading.Event | None = None,
) -> int:
    """Fast path: Polars-native JSON parsing + expression-based flatten.

    For ``.jsonl`` files, reads lines in adaptive chunks (scaled to schema
    width — see :func:`_adaptive_chunk_size`), parses each chunk with
    ``pl.read_ndjson`` (simd-json, multi-threaded), flattens via Polars
    expressions, and writes incrementally through a PyArrow
    ``ParquetWriter``.  Memory usage is bounded to one chunk at a time.

    For ``.json`` files, uses ``pl.read_json`` (eager — JSON format requires
    full-file parsing) followed by ``write_parquet``.

    Raises on any error so the caller can fall back to
    :func:`_flatten_and_write_streaming`.
    """
    import io

    import polars as pl
    import pyarrow.parquet as pq

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.with_suffix(".parquet.tmp")

    exprs = _build_flatten_exprs(flatten_schema)
    if not exprs:
        pl.DataFrame().write_parquet(tmp_path, compression="zstd")
        tmp_path.replace(cache_path)
        return 0

    if chunk_lines is None:
        chunk_lines = _adaptive_chunk_size(flatten_schema)

    writer: pq.ParquetWriter | None = None
    try:
        if path.suffix == ".jsonl":
            total_rows = 0

            for chunk_bytes in _iter_line_chunks(path, chunk_lines):
                df = pl.read_ndjson(io.BytesIO(chunk_bytes))
                del chunk_bytes  # free raw JSON bytes
                flat = df.select(exprs)
                del df  # free nested DataFrame
                arrow_table = flat.to_arrow()
                chunk_len = len(flat)
                del flat  # free flattened DataFrame

                if writer is None:
                    writer = pq.ParquetWriter(
                        str(tmp_path),
                        arrow_table.schema,
                        compression="zstd",
                    )
                writer.write_table(arrow_table)
                del arrow_table  # free Arrow table

                total_rows += chunk_len
                _update_progress(progress_key, t0, total_rows)
                _check_cancelled(cancel_event, progress_key or str(cache_path))

            if writer is not None:
                writer.close()
                writer = None
            else:
                # Empty file — write empty parquet with correct columns
                cols = schema_columns(flatten_schema)
                empty = pl.DataFrame({c: pl.Series([], dtype=pl.String) for c in cols})
                empty.write_parquet(str(tmp_path), compression="zstd")
        else:
            # .json: must be fully loaded (JSON format constraint)
            df = pl.read_json(path)
            flat = df.select(exprs)
            flat.write_parquet(tmp_path, compression="zstd")
            total_rows = len(flat)

            _update_progress(progress_key, t0, total_rows)

        tmp_path.replace(cache_path)

        logger.info(
            "json_cache_written_polars",
            path=str(cache_path),
            rows=total_rows,
            size_bytes=cache_path.stat().st_size,
        )
        return total_rows

    except BaseException:
        if writer is not None:
            writer.close()
        tmp_path.unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------------------
# Sample loading for UI schema preview and tests.
# ---------------------------------------------------------------------------


def load_samples(
    path: str | Path,
    *,
    max_samples: int = 10_000,
) -> list[dict[str, Any]]:
    """Load sample data from a ``.json`` or ``.jsonl`` file.

    - ``.json``:  a single object ``{…}`` or an array of objects ``[{…}, …]``.
    - ``.jsonl``: one JSON object per line — only the first *max_samples*
      lines are read so that large files (tens of GB) don't blow up memory.

    Parameters
    ----------
    max_samples:
        Maximum number of records to return.  Defaults to 10 000.
        Only affects ``.jsonl`` files (streamed line-by-line).
        ``.json`` files are always loaded in full since the format
        requires parsing the entire document.
    """
    p = Path(path)

    if p.suffix == ".jsonl":
        samples: list[dict[str, Any]] = []
        with p.open("rb") as fh:
            for raw_line in fh:
                stripped = raw_line.strip()
                if stripped:
                    obj = orjson.loads(stripped)
                    if isinstance(obj, dict):
                        samples.append(obj)
                        if len(samples) >= max_samples:
                            break
        logger.info("samples_loaded", path=str(p), count=len(samples))
        return samples

    raw = p.read_bytes()
    data = orjson.loads(raw)
    if isinstance(data, list):
        result = [d for d in data if isinstance(d, dict)]
    elif isinstance(data, dict):
        result = [data]
    else:
        result = []

    logger.info("samples_loaded", path=str(p), count=len(result))
    return result


# ---------------------------------------------------------------------------
# Convenience: flatten → LazyFrame
# ---------------------------------------------------------------------------


def flatten_to_frame(
    data: dict[str, Any] | list[dict[str, Any]],
    schema: dict[str, Any],
) -> pl.LazyFrame:
    """Flatten one or more JSON dicts and return a Polars ``LazyFrame``."""
    import polars as pl

    if isinstance(data, dict):
        data = [data]
    rows = [flatten(d, schema) for d in data]
    return pl.from_dicts(rows).lazy()


# ---------------------------------------------------------------------------
# Parquet cache for flattened JSON — dual-layer (working / committed)
# ---------------------------------------------------------------------------
#
# The cache is split into two layers, each under `.haute_cache/<layer>/<hash>/`:
#
#  - `working/<hash>/` — written by the "Cache as Parquet" button. Volatile,
#    in-session. Reflects whatever the editor's in-memory schema was at click
#    time. Disposable.
#  - `committed/<hash>/` — written by Save, which mirrors `working/<hash>/`
#    into committed/ (including absence — if working/ doesn't exist, Save
#    ensures committed/ also doesn't exist). The durable contract that
#    survives a server restart.
#
# Each layer's `<hash>/` directory contains:
#   - `data.parquet` — the cached parquet bytes. The flatten_schema is
#     embedded under key `haute.flatten_schema` in the parquet footer
#     kv-metadata (single-file robustness — no schema-wrote / data-failed
#     race, since both land in the same file).
#   - `meta.json` — sidecar carrying `{schema_mode, schema_fingerprint}`.
#     The fingerprint backs the no-op trapdoors (cache: skip rebuild when
#     in-memory schema fingerprint == working/meta.json fingerprint; save:
#     skip mirror when working/meta.json fingerprint == committed/meta.json
#     fingerprint).
#
# Emitter precedence (DUAL_CACHE.md §4 + §11):
#   - If the current Python process has cached this data file (i.e. the
#     data-path hash is in `_session_consulted_hashes`), the emitter
#     prefers `working/` and falls through to `committed/` only if working/
#     is invalid for the requested schema. This is the "active editing
#     session" semantic.
#   - Otherwise (e.g. immediately post-restart), the emitter reads
#     `committed/` only. `working/` on disk is preserved untouched (no
#     server-startup cleanup) so a future recovery UX can offer to
#     reinstate it, but the running emitter does not consult it.

_CACHE_DIR = ".haute_cache"
_LAYER_WORKING = "working"
_LAYER_COMMITTED = "committed"
_DATA_FILENAME = "data.parquet"
_META_FILENAME = "meta.json"


# Module-level session tracking. Empty per Python process. The emitter
# consults `working/` only for data-file hashes in this set; otherwise it
# falls through to `committed/`. The set is populated when `working/` is
# written via `build_json_cache(layer="working")`, when `read_json_flat`
# auto-builds into working/, and when `mirror_cache_to_committed` runs
# (post-save the local process is still authoritative for working/).
_session_consulted_hashes: set[str] = set()


def _path_hash(data_path: str | Path) -> str:
    """SHA-256 (32-char) hash of the canonical absolute data file path.

    Identical for any pair of relative/absolute paths that resolve to the
    same file, so the cache identity is stable across cwd changes.
    """
    canonical_path = os.path.normcase(str(Path(data_path).expanduser().resolve()))
    return hashlib.sha256(canonical_path.encode()).hexdigest()[:32]


def _json_cache_dir(data_path: str | Path, layer: str) -> Path:
    """Return the `<layer>/<hash>/` directory for a JSON data file's cache."""
    if layer not in (_LAYER_WORKING, _LAYER_COMMITTED):
        raise ValueError(f"Unknown cache layer: {layer!r}")
    return Path.cwd() / _CACHE_DIR / layer / f"json_{_path_hash(data_path)}"


def _json_cache_data_path(cache_dir: Path) -> Path:
    """Return the `data.parquet` path inside a `<layer>/<hash>/` directory."""
    return cache_dir / _DATA_FILENAME


def _json_cache_meta_path(cache_dir: Path) -> Path:
    """Return the `meta.json` sidecar path inside a `<layer>/<hash>/` directory."""
    return cache_dir / _META_FILENAME


def _working_cache_data_path(data_path: str | Path) -> Path:
    """Shortcut for the working layer's `data.parquet` — used by tests and
    by callers that previously dealt in single-file cache paths.
    """
    return _json_cache_data_path(_json_cache_dir(data_path, _LAYER_WORKING))


def _committed_cache_data_path(data_path: str | Path) -> Path:
    """Shortcut for the committed layer's `data.parquet` — symmetrical helper
    used by tests that simulate a "saved cache" pre-existing on disk.
    """
    return _json_cache_data_path(_json_cache_dir(data_path, _LAYER_COMMITTED))


# Backwards-compatible alias for tests that predate the dual-layer split.
# Points at the working layer's data.parquet, which is where the
# Cache-as-Parquet button (and `build_json_cache(layer="working")`) writes.
# Tests that pre-create cache fixtures without going through
# `build_json_cache` should additionally invoke `_mark_working_consulted`
# so the emitter's precedence picks up the working layer.
_json_cache_path = _working_cache_data_path


def _mark_working_consulted(data_path: str | Path) -> None:
    """Record that working/ is authoritative for this data file in this process."""
    _session_consulted_hashes.add(_path_hash(data_path))


def _is_working_consulted(data_path: str | Path) -> bool:
    """True if working/ has been written (or read-built) for this data file in this process."""
    return _path_hash(data_path) in _session_consulted_hashes


def _clear_session() -> None:
    """Test-only hook: simulate a process restart by clearing the consulted-hashes set."""
    _session_consulted_hashes.clear()


def _wipe_legacy_flat_cache(data_path: str | Path) -> bool:
    """Remove legacy `.haute_cache/json_<hash>.parquet` flat-layout artifacts.

    Pre-dual-cache, the cache was a single parquet at
    `.haute_cache/json_<hash>.parquet` with a sidecar `.meta.json`. The
    dual-cache migration policy is wipe-on-first-run: on the first
    dual-cache operation for a given data file, legacy artifacts get
    unlinked and the user must rebuild via the Cache button.

    Returns True if anything was deleted (so callers can log).
    """
    cache_root = Path.cwd() / _CACHE_DIR
    legacy_stem = f"json_{_path_hash(data_path)}"
    artifacts = [
        cache_root / f"{legacy_stem}.parquet",
        cache_root / f"{legacy_stem}.parquet.meta.json",
        cache_root / f"{legacy_stem}.parquet.tmp",
        cache_root / f"{legacy_stem}.raw.parquet",
        cache_root / f"{legacy_stem}.raw.parquet.tmp",
    ]
    deleted = False
    for artifact in artifacts:
        if artifact.exists() and artifact.is_file():
            artifact.unlink()
            deleted = True
    if deleted:
        logger.info("legacy_flat_cache_wiped", data_path=str(data_path))
    return deleted


def _schema_fingerprint(flatten_schema: dict[str, Any]) -> str:
    payload = orjson.dumps(flatten_schema, option=orjson.OPT_SORT_KEYS)
    return hashlib.sha256(payload).hexdigest()


def _build_parquet_metadata(
    flatten_schema: dict[str, Any],
    schema_mode: str,
) -> dict[bytes, bytes]:
    """KV-metadata embedded in the parquet footer for single-file robustness.

    Encodes the flatten_schema (canonical JSON, sorted keys) under
    `haute.flatten_schema` and the schema mode (explicit/config/inferred)
    under `haute.schema_mode`. Downstream consumers that get only the
    parquet file (no sidecar JSON) can still reconstruct schema + mode.
    """
    payload = orjson.dumps(flatten_schema, option=orjson.OPT_SORT_KEYS)
    return {
        b"haute.flatten_schema": payload,
        b"haute.schema_mode": schema_mode.encode(),
    }


def _schema_cache_mode(
    schema: dict[str, Any] | None,
    config_path: str | Path | None,
) -> str:
    if schema is not None:
        return "explicit"
    if config_path is not None:
        cp = Path(config_path)
        if cp.exists():
            cfg = orjson.loads(cp.read_bytes())
            if cfg.get("flattenSchema") is not None:
                return "config"
    return "inferred"


def _read_cache_meta(cache_dir: Path) -> dict[str, object] | None:
    """Read `meta.json` from a layer's `<hash>/` directory, or return None if absent."""
    meta_path = _json_cache_meta_path(cache_dir)
    if not meta_path.exists():
        return None
    payload = orjson.loads(meta_path.read_bytes())
    if not isinstance(payload, dict):
        raise ValueError(f"JSON cache metadata must be an object: {meta_path}")
    return cast(dict[str, object], payload)


def _maybe_meta_mtime_ms(p: Path) -> int:
    """Return the integer ms-precision mtime of *p*, or 0 if absent.

    Used by :func:`cache_state_signature_for_graph` to compose the
    preview-cache fingerprint key without raising on missing files.
    """
    try:
        return int(p.stat().st_mtime * 1000)
    except OSError:
        return 0


def cache_state_signature_for_graph(graph: Any) -> str:
    """Deterministic string capturing the JSON-cache state for every apiInput
    node in *graph*. Used as an extra fingerprint key so a JSON-cache mutation
    (build, delete, mirror to committed) invalidates affected preview-cache
    entries without thrashing unrelated ones.

    Per apiInput in the graph the signature contains: the node id, a short
    data-file-path hash, and the ms-precision mtimes of the working and
    committed layers' ``meta.json`` sidecars. A missing sidecar contributes
    ``0``. Entries are sorted by node id for stability.

    Returns ``""`` when the graph has no apiInputs; the caller should pass
    the empty string verbatim or skip including it in the key.
    """
    from haute._types import NodeType

    parts: list[str] = []
    for node in sorted(graph.nodes, key=lambda n: n.id):
        if node.data.nodeType != NodeType.API_INPUT:
            continue
        data_path = node.data.config.get("path")
        if not isinstance(data_path, str) or not data_path:
            continue
        try:
            cache_hash = _path_hash(data_path)
        except (OSError, ValueError):
            continue
        working_mtime = _maybe_meta_mtime_ms(
            _json_cache_meta_path(_json_cache_dir(data_path, _LAYER_WORKING)),
        )
        committed_mtime = _maybe_meta_mtime_ms(
            _json_cache_meta_path(_json_cache_dir(data_path, _LAYER_COMMITTED)),
        )
        parts.append(f"{node.id}={cache_hash[:8]}:{working_mtime}:{committed_mtime}")
    if not parts:
        return ""
    return "json_cache=" + "|".join(parts)


def _write_cache_meta(
    cache_dir: Path,
    *,
    schema_mode: str,
    flatten_schema: dict[str, Any],
) -> None:
    """Write `meta.json` atomically into a layer's `<hash>/` directory.

    Creates the directory if needed. The atomic-tmp-then-rename pattern
    means a failed write never leaves a corrupt sidecar behind.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    meta_path = _json_cache_meta_path(cache_dir)
    payload = {
        "schema_mode": schema_mode,
        "schema_fingerprint": _schema_fingerprint(flatten_schema),
    }
    tmp_path = Path(str(meta_path) + ".tmp")
    try:
        tmp_path.write_bytes(orjson.dumps(payload, option=orjson.OPT_SORT_KEYS))
        tmp_path.replace(meta_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _is_cache_schema_compatible(
    cache_dir: Path,
    *,
    schema_mode: str,
    flatten_schema: dict[str, Any] | None = None,
) -> bool:
    """Compare `meta.json` against expected mode+fingerprint."""
    meta = _read_cache_meta(cache_dir)
    if meta is None:
        return schema_mode == "inferred"
    if meta.get("schema_mode") != schema_mode:
        return False
    if flatten_schema is None:
        return True
    return meta.get("schema_fingerprint") == _schema_fingerprint(flatten_schema)


def cache_layer_if_valid(
    data_path: str | Path,
    *,
    schema: dict[str, Any] | None = None,
    config_path: str | Path | None = None,
) -> tuple[Path, str] | None:
    """Return `(data_parquet_path, layer)` for the first valid layer, else None.

    Layer precedence:
      1. If `_is_working_consulted(data_path)` (i.e. this process has cached
         the file via the cache button or read-build), try `working/` first.
      2. Always fall through to `committed/`.

    The session-set gate is what closes the cross-restart vulnerability:
    after a server restart, the set is empty, so the emitter skips `working/`
    even if it remains on disk from a previous session.

    Schema-compatibility match (preserves pre-dual-cache semantics):
    - If ``config_path`` is supplied, the cache's ``meta.json``
      ``schema_mode`` AND fingerprint must both match.
    - If only ``schema`` is supplied, fingerprint match alone is sufficient
      (the mode may differ if the cache was built via a config_path).
    - If neither is supplied, only inferred-mode caches are accepted.
    """
    if schema is not None:
        _validate_flatten_schema(schema)

    source_paths = [Path(data_path)]
    if config_path is not None:
        source_paths.append(Path(config_path))
    schema_mode = _schema_cache_mode(schema, config_path)

    layers_to_try: list[str] = []
    if _is_working_consulted(data_path):
        layers_to_try.append(_LAYER_WORKING)
    layers_to_try.append(_LAYER_COMMITTED)

    for layer in layers_to_try:
        cache_dir = _json_cache_dir(data_path, layer)
        if not _is_cache_valid(cache_dir, *source_paths):
            continue

        meta = _read_cache_meta(cache_dir)

        if config_path is not None:
            if schema_mode == "inferred":
                if _is_cache_schema_compatible(cache_dir, schema_mode=schema_mode):
                    return _json_cache_data_path(cache_dir), layer
                continue
            resolved = _resolve_flatten_schema(Path(data_path), schema, config_path)
            _validate_flatten_schema(resolved)
            if _is_cache_schema_compatible(
                cache_dir,
                schema_mode=schema_mode,
                flatten_schema=resolved,
            ):
                return _json_cache_data_path(cache_dir), layer
            continue

        if schema is None:
            if meta is None or meta.get("schema_mode") == "inferred":
                return _json_cache_data_path(cache_dir), layer
            continue

        if meta is None:
            continue
        if meta.get("schema_fingerprint") != _schema_fingerprint(schema):
            continue
        return _json_cache_data_path(cache_dir), layer
    return None


def json_cache_path_if_valid(
    data_path: str | Path,
    *,
    schema: dict[str, Any] | None = None,
    config_path: str | Path | None = None,
) -> Path | None:
    """Return the path to a valid `data.parquet`, or None if no layer is valid.

    Thin wrapper over `cache_layer_if_valid` for callers that don't need
    to know which layer produced the hit.
    """
    result = cache_layer_if_valid(
        data_path,
        schema=schema,
        config_path=config_path,
    )
    return result[0] if result is not None else None


def _is_cache_valid(cache_dir: Path, *source_paths: Path) -> bool:
    """Return True if a layer's `data.parquet` exists and is newer than all sources."""
    data_path = _json_cache_data_path(cache_dir)
    if not data_path.exists():
        return False
    cache_mtime = data_path.stat().st_mtime
    for src in source_paths:
        if not src.exists():
            return False
        if src.stat().st_mtime > cache_mtime:
            return False
    return True


def _flatten_and_write(
    samples: list[dict[str, Any]],
    schema: dict[str, Any],
    cache_path: Path,
    *,
    parquet_metadata: Mapping[bytes, bytes] | None = None,
) -> None:
    """Flatten samples and write to a parquet cache file.

    Writes to a temporary file first and atomically renames on success
    so a failed flatten never leaves a corrupt cache behind. When
    *parquet_metadata* is supplied, the bytes are embedded in the parquet
    footer KV-metadata for single-file robustness.
    """
    import polars as pl
    import pyarrow.parquet as pq

    from haute._polars_utils import atomic_write

    with atomic_write(cache_path) as tmp_path:
        rows = [flatten(d, schema) for d in samples]
        df = pl.from_dicts(rows) if rows else pl.DataFrame()
        if parquet_metadata:
            table = df.to_arrow()
            table = table.replace_schema_metadata(dict(parquet_metadata))
            pq.write_table(table, str(tmp_path), compression="zstd")
        else:
            df.write_parquet(tmp_path, compression="zstd")

    logger.info(
        "json_cache_written",
        path=str(cache_path),
        rows=len(samples),
        size_bytes=cache_path.stat().st_size,
    )


def _resolve_flatten_schema(
    data_path: Path,
    schema: dict[str, Any] | None,
    config_path: str | Path | None,
) -> dict[str, Any]:
    """Resolve the flatten schema from explicit arg, config file, or inference."""
    if schema is not None:
        return schema

    if config_path is not None:
        cp = Path(config_path)
        if cp.exists():
            cfg = orjson.loads(cp.read_bytes())
            from_config: dict[str, Any] | None = cfg.get("flattenSchema")
            if from_config is not None:
                return from_config

    inferred = _infer_schema_streaming(data_path)
    logger.info(
        "schema_inferred",
        path=str(data_path),
        columns=len(schema_columns(inferred)),
    )
    return inferred


def read_json_flat(
    data_path: str | Path,
    *,
    schema: dict[str, Any] | None = None,
    config_path: str | Path | None = None,
) -> pl.LazyFrame:
    """Load JSON/JSONL, flatten to tabular, return a Polars ``LazyFrame``.

    Looks up the cache via the dual-layer precedence (`working/` first if
    this process has cached the file, else `committed/`). On a miss,
    auto-builds into `working/` — preserving the existing "auto-cache on
    read" affordance — and marks the data-file hash as consulted so the
    emitter prefers `working/` for subsequent reads in this process.

    Auto-cache-on-read mirrors the existing dev workflow. Deployed
    pipelines should have `committed/` pre-built and bundled; if a deploy
    auto-builds because the cache is missing, that signals a packaging
    issue but the runtime still works.

    For ``.jsonl`` files, uses the two-step streaming pipeline as
    :func:`build_json_cache`. For ``.json`` files, streams records through
    PyArrow ``ParquetWriter``.
    """
    import polars as pl

    p = Path(data_path)

    cache_path = json_cache_path_if_valid(data_path, schema=schema, config_path=config_path)
    if cache_path is not None:
        logger.info("json_cache_hit", path=str(data_path), cache=str(cache_path))
        return pl.scan_parquet(cache_path)

    schema_mode = _schema_cache_mode(schema, config_path)
    resolved = _resolve_flatten_schema(p, schema, config_path)
    _validate_flatten_schema(resolved)

    _wipe_legacy_flat_cache(data_path)
    cache_dir = _json_cache_dir(data_path, _LAYER_WORKING)
    cache_dir.mkdir(parents=True, exist_ok=True)
    data_parquet = _json_cache_data_path(cache_dir)
    parquet_metadata = _build_parquet_metadata(resolved, schema_mode)

    if p.suffix == ".jsonl":
        _flatten_jsonl_to_cache(p, resolved, data_parquet, parquet_metadata=parquet_metadata)
    else:
        _flatten_and_write_streaming(
            _iter_json_records(p),
            resolved,
            data_parquet,
            parquet_metadata=parquet_metadata,
        )

    _write_cache_meta(
        cache_dir,
        schema_mode=schema_mode,
        flatten_schema=resolved,
    )
    _mark_working_consulted(data_path)
    return pl.scan_parquet(data_parquet)


# ---------------------------------------------------------------------------
# Explicit cache management (mirrors _databricks_io cache helpers)
# ---------------------------------------------------------------------------


def is_large_json(data_path: str | Path) -> bool:
    """Return True if the file size is >= the large-file threshold (50 MB)."""
    p = Path(data_path)
    return p.exists() and p.stat().st_size >= _LARGE_FILE_THRESHOLD


def flatten_progress(data_path: str) -> dict[str, object] | None:
    """Return current flatten progress for *data_path*, or ``None`` if not active."""
    with _flatten_lock:
        return _flatten_progress.get(data_path)


class JsonCacheInfoDict(TypedDict):
    path: str
    data_path: str
    row_count: int
    column_count: int
    columns: dict[str, str]
    size_bytes: int
    cached_at: float


def json_cache_info(
    data_path: str | Path,
    *,
    schema: dict[str, Any] | None = None,
) -> JsonCacheInfoDict | None:
    """Return metadata about a cached JSON file, or ``None`` if not cached.

    Uses the precedence rule via :func:`json_cache_path_if_valid` so the
    info reflects the layer the emitter would read from.
    """
    from haute._polars_utils import read_parquet_metadata

    cache_path = json_cache_path_if_valid(data_path, schema=schema)
    if cache_path is None:
        return None
    meta = read_parquet_metadata(cache_path)
    return {
        "path": str(cache_path),
        "data_path": str(data_path),
        "row_count": meta["row_count"],
        "column_count": meta["column_count"],
        "columns": meta["columns"],
        "size_bytes": meta["size_bytes"],
        "cached_at": meta["mtime"],
    }


def clear_json_cache(
    data_path: str | Path,
    *,
    layer: str = _LAYER_WORKING,
) -> bool:
    """Delete cached parquet artifacts for a JSON data file in one layer.

    Default is the volatile working/ layer — used by the DELETE endpoint
    (test 4: "delete only affects volatile"). Always wipes any legacy
    flat-layout artifacts too.

    The consulted-hashes flag is intentionally NOT cleared. The user is
    still in the same process, so they remain authoritative for this
    data file. The emitter precedence then resolves to: try working/
    (gone → invalid), fall through to committed/. And on the next save,
    ``mirror_cache_to_committed`` sees consulted=True + working/ absent
    and propagates the absence to committed/ — that's test 5.

    Returns True if anything was deleted.
    """
    _wipe_legacy_flat_cache(data_path)
    cache_dir = _json_cache_dir(data_path, layer)
    if not cache_dir.exists():
        return False
    shutil.rmtree(cache_dir)
    return True


class JsonBuildResultDict(JsonCacheInfoDict):
    cache_seconds: float


def build_json_cache(
    data_path: str | Path,
    schema: dict[str, Any] | None = None,
    config_path: str | Path | None = None,
    *,
    layer: str = _LAYER_WORKING,
) -> JsonBuildResultDict:
    """Build the parquet cache for a JSON/JSONL file with progress tracking.

    This is the explicit entry point for the "Cache as Parquet" button.
    By default targets the volatile ``working/`` layer; the ``committed/``
    layer is populated only via :func:`mirror_cache_to_committed` (invoked
    by Save).

    No-op trapdoor (DUAL_CACHE.md §6 cache trapdoor): if the target
    layer's ``meta.json`` fingerprint already matches the resolved
    in-memory schema fingerprint, skip the rebuild and return the
    existing summary. The session-set is still updated when
    ``layer="working"`` so the precedence flag persists across the no-op.

    For ``.jsonl`` files uses the two-step streaming approach:
    JSONL → raw Parquet → flat Parquet. For ``.json`` files streams
    records through PyArrow ``ParquetWriter``.
    """
    if layer not in (_LAYER_WORKING, _LAYER_COMMITTED):
        raise ValueError(f"Unknown cache layer: {layer!r}")

    p = Path(data_path)
    data_path_str = str(data_path)
    schema_mode = _schema_cache_mode(schema, config_path)

    # Wipe any legacy flat-layout artifacts before touching the new layout.
    _wipe_legacy_flat_cache(data_path)

    cache_dir = _json_cache_dir(data_path, layer)
    data_parquet = _json_cache_data_path(cache_dir)

    resolved = _resolve_flatten_schema(p, schema, config_path)
    _validate_flatten_schema(resolved)
    fingerprint = _schema_fingerprint(resolved)

    # No-op trapdoor: skip rebuild when target layer already has matching meta.
    existing_meta = _read_cache_meta(cache_dir)
    if (
        existing_meta is not None
        and existing_meta.get("schema_fingerprint") == fingerprint
        and existing_meta.get("schema_mode") == schema_mode
        and data_parquet.exists()
    ):
        from haute._polars_utils import read_parquet_metadata

        if layer == _LAYER_WORKING:
            _mark_working_consulted(data_path)
        meta = read_parquet_metadata(data_parquet)
        logger.info(
            "json_cache_build_noop",
            layer=layer,
            data_path=data_path_str,
            cache_dir=str(cache_dir),
        )
        return {
            "path": str(data_parquet),
            "data_path": data_path_str,
            "row_count": meta["row_count"],
            "column_count": meta["column_count"],
            "columns": meta["columns"],
            "size_bytes": meta["size_bytes"],
            "cached_at": meta["mtime"],
            "cache_seconds": 0.0,
        }

    parquet_metadata = _build_parquet_metadata(resolved, schema_mode)

    event = threading.Event()
    t0 = time.monotonic()
    with _flatten_lock:
        _flatten_progress[data_path_str] = {"rows": 0, "elapsed": 0.0}
        _cancel_events[data_path_str] = event

    try:
        if p.suffix == ".jsonl":
            _flatten_jsonl_to_cache(
                p,
                resolved,
                data_parquet,
                progress_key=data_path_str,
                t0=t0,
                cancel_event=event,
                parquet_metadata=parquet_metadata,
            )
        else:
            # .json: must be fully loaded (JSON format constraint)
            _flatten_and_write_streaming(
                _iter_json_records(p),
                resolved,
                data_parquet,
                progress_key=data_path_str,
                t0=t0,
                cancel_event=event,
                parquet_metadata=parquet_metadata,
            )
    finally:
        with _flatten_lock:
            _flatten_progress.pop(data_path_str, None)
            _cancel_events.pop(data_path_str, None)

    elapsed = time.monotonic() - t0

    from haute._polars_utils import read_parquet_metadata

    _write_cache_meta(
        cache_dir,
        schema_mode=schema_mode,
        flatten_schema=resolved,
    )
    if layer == _LAYER_WORKING:
        _mark_working_consulted(data_path)
    meta = read_parquet_metadata(data_parquet)
    return {
        "path": str(data_parquet),
        "data_path": data_path_str,
        "row_count": meta["row_count"],
        "column_count": meta["column_count"],
        "columns": meta["columns"],
        "size_bytes": meta["size_bytes"],
        "cached_at": meta["mtime"],
        "cache_seconds": round(elapsed, 2),
    }


def mirror_cache_to_committed(data_path: str | Path) -> bool:
    """Promote `working/<hash>/` → `committed/<hash>/` on Save (DUAL_CACHE.md §4).

    Behaviour (the user's test plan governs):
      - If the current process has NOT cached this data file (i.e. not in
        ``_session_consulted_hashes``), this is a no-op. This guards against
        save inadvertently promoting a stale on-disk working/ from a
        previous session (cross-restart vulnerability mitigation).
      - If working/ exists: mirror it byte-for-byte into committed/
        (test 3 — "save synchronises stable to volatile without changing
        volatile"). No-op trapdoor: if working/meta.json fingerprint ==
        committed/meta.json fingerprint, skip the copy.
      - If working/ does not exist: ensure committed/ also does not exist
        (test 5 — "save in cache-deleted state removes stable cache").

    Returns True if the on-disk committed/ state changed.
    """
    _wipe_legacy_flat_cache(data_path)
    if not _is_working_consulted(data_path):
        # Stale on-disk working/ from a previous session; or no cache ever.
        return False

    working_dir = _json_cache_dir(data_path, _LAYER_WORKING)
    committed_dir = _json_cache_dir(data_path, _LAYER_COMMITTED)

    if not working_dir.exists():
        if committed_dir.exists():
            shutil.rmtree(committed_dir)
            logger.info(
                "json_cache_committed_cleared",
                data_path=str(data_path),
                committed_dir=str(committed_dir),
            )
            return True
        return False

    working_meta = _read_cache_meta(working_dir)
    committed_meta = _read_cache_meta(committed_dir) if committed_dir.exists() else None
    if (
        working_meta is not None
        and committed_meta is not None
        and working_meta.get("schema_fingerprint") == committed_meta.get("schema_fingerprint")
        and working_meta.get("schema_mode") == committed_meta.get("schema_mode")
    ):
        logger.info(
            "json_cache_save_noop",
            data_path=str(data_path),
            committed_dir=str(committed_dir),
        )
        return False

    # Atomic replacement: copytree into a `.tmp` sibling then swap.
    committed_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = committed_dir.with_name(committed_dir.name + ".tmp")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    shutil.copytree(working_dir, tmp_dir)
    if committed_dir.exists():
        backup = committed_dir.with_name(committed_dir.name + ".old")
        if backup.exists():
            shutil.rmtree(backup)
        committed_dir.rename(backup)
        try:
            tmp_dir.rename(committed_dir)
        except BaseException:
            backup.rename(committed_dir)
            raise
        shutil.rmtree(backup, ignore_errors=True)
    else:
        tmp_dir.rename(committed_dir)
    logger.info(
        "json_cache_committed_mirrored",
        data_path=str(data_path),
        working_dir=str(working_dir),
        committed_dir=str(committed_dir),
    )
    return True
