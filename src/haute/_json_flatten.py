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
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

import orjson

from haute._logging import get_logger
from haute._polars_utils import _malloc_trim
from haute._types import HauteError

if TYPE_CHECKING:
    import polars as pl
    import pyarrow as pa

logger = get_logger(component="json_flatten")

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

_TYPE_PRIORITY: dict[str, int] = {"bool": 0, "int": 1, "float": 2, "str": 3}


def _infer_type(value: Any) -> str:
    """Return the schema type name for a scalar value."""
    # bool must be checked before int — bool is a subclass of int
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    return "str"


def _wider_type(a: str, b: str) -> str:
    """Return the wider of two scalar types (int < float < str)."""
    return a if _TYPE_PRIORITY.get(a, 3) >= _TYPE_PRIORITY.get(b, 3) else b


# ---------------------------------------------------------------------------
# Schema inference
# ---------------------------------------------------------------------------


def _infer_schema_node(value: Any) -> dict[str, Any] | str:
    """Build a schema node from a single JSON value."""
    if value is None:
        return "str"
    if isinstance(value, dict):
        return {k: _infer_schema_node(v) for k, v in value.items()}
    if isinstance(value, list):
        items: dict[str, Any] | str = {}
        for item in value:
            items = _merge_schema_nodes(items, _infer_schema_node(item))
        return {"$max": len(value), "$items": items if items != {} else {}}
    return _infer_type(value)


def _merge_schema_nodes(
    a: dict[str, Any] | str,
    b: dict[str, Any] | str,
) -> dict[str, Any] | str:
    """Merge two schema nodes, widening types and unioning fields."""
    # Identity: empty dict means "no schema yet"
    if a == {}:
        return b
    if b == {}:
        return a

    # Both scalars — widen
    if isinstance(a, str) and isinstance(b, str):
        return _wider_type(a, b)

    # Both dicts
    if isinstance(a, dict) and isinstance(b, dict):
        a_is_array = "$max" in a
        b_is_array = "$max" in b

        if a_is_array and b_is_array:
            return {
                "$max": max(a["$max"], b["$max"]),
                "$items": _merge_schema_nodes(
                    a.get("$items", {}),
                    b.get("$items", {}),
                ),
            }
        if not a_is_array and not b_is_array:
            merged = dict(a)
            for k, v in b.items():
                merged[k] = _merge_schema_nodes(merged[k], v) if k in merged else v
            return merged

    # Type conflict (scalar vs dict) — prefer the richer structure
    return a if isinstance(a, dict) else b


def infer_schema(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Infer a flatten schema from one or more sample JSON dicts.

    Each sample should be a single JSON object (e.g. one quote).
    The returned schema merges all fields (union) and uses the maximum
    observed array length for ``$max``.
    """
    if not samples:
        return {}
    schema: dict[str, Any] | str = {}
    for sample in samples:
        schema = _merge_schema_nodes(schema, _infer_schema_node(sample))
    return schema if isinstance(schema, dict) else {}


# ---------------------------------------------------------------------------
# Schema-aware flattening
# ---------------------------------------------------------------------------


def flatten(
    data: dict[str, Any] | None,
    schema: dict[str, Any],
    *,
    _prefix: str = "",
) -> dict[str, Any]:
    """Flatten a nested JSON dict using a schema.

    Walks the *schema* tree (not the data) to produce a consistent column
    set.  Missing data becomes ``None``.

    Keys use ``"."`` as separator; array indices are **1-based**.
    """
    if data is None:
        data = {}
    result: dict[str, Any] = {}

    for key, spec in schema.items():
        full_key = f"{_prefix}.{key}" if _prefix else key
        value = data.get(key) if isinstance(data, dict) else None

        if isinstance(spec, str):
            # Leaf — emit value or None
            result[full_key] = value

        elif "$max" in spec:
            # Array
            max_items: int = spec["$max"]
            items_schema = spec.get("$items", {})
            if max_items == 0 or not items_schema:
                continue
            raw_list = value if isinstance(value, list) else []
            for i in range(max_items):
                idx_key = f"{full_key}.{i + 1}"
                element = raw_list[i] if i < len(raw_list) else None
                if isinstance(items_schema, str):
                    # Array of scalars
                    result[idx_key] = element
                else:
                    # Array of objects — recurse
                    child = element if isinstance(element, dict) else None
                    result.update(flatten(child, items_schema, _prefix=idx_key))

        else:
            # Nested object — recurse
            child = value if isinstance(value, dict) else None
            result.update(flatten(child, spec, _prefix=full_key))

    return result


def schema_columns(
    schema: dict[str, Any],
    *,
    _prefix: str = "",
) -> list[str]:
    """Return the flat column names a schema produces, in traversal order."""
    cols: list[str] = []
    for key, spec in schema.items():
        full_key = f"{_prefix}.{key}" if _prefix else key
        if isinstance(spec, str):
            cols.append(full_key)
        elif "$max" in spec:
            max_items: int = spec["$max"]
            items_schema = spec.get("$items", {})
            if max_items == 0 or not items_schema:
                continue
            for i in range(max_items):
                idx_key = f"{full_key}.{i + 1}"
                if isinstance(items_schema, str):
                    cols.append(idx_key)
                else:
                    cols.extend(schema_columns(items_schema, _prefix=idx_key))
        else:
            cols.extend(schema_columns(spec, _prefix=full_key))
    return cols


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
        data = orjson.loads(path.read_bytes())
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    yield item
        elif isinstance(data, dict):
            yield data


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


def _schema_leaf_types(
    schema: dict[str, Any],
    *,
    _prefix: str = "",
) -> list[tuple[str, str]]:
    """Return ``[(column_name, type_str), ...]`` from a flatten schema."""
    result: list[tuple[str, str]] = []
    for key, spec in schema.items():
        full_key = f"{_prefix}.{key}" if _prefix else key
        if isinstance(spec, str):
            result.append((full_key, spec))
        elif "$max" in spec:
            max_items: int = spec["$max"]
            items_schema = spec.get("$items", {})
            if max_items == 0 or not items_schema:
                continue
            for i in range(max_items):
                idx_key = f"{full_key}.{i + 1}"
                if isinstance(items_schema, str):
                    result.append((idx_key, items_schema))
                else:
                    result.extend(
                        _schema_leaf_types(items_schema, _prefix=idx_key),
                    )
        else:
            result.extend(_schema_leaf_types(spec, _prefix=full_key))
    return result


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
) -> int:
    """Stream JSON records through flatten → PyArrow ParquetWriter.

    Uses **column-oriented accumulation**: each record is flattened and its
    values are scattered into per-column lists immediately, so only the
    individual values survive — no list of row-dicts piling up.

    Peak memory per chunk ≈ ``n_cols × chunk_size × value_size``.
    Chunk size adapts to the schema width via :func:`_adaptive_chunk_size`.

    Returns the total number of rows written.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    if chunk_size is None:
        chunk_size = _adaptive_chunk_size(flatten_schema)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.with_suffix(".parquet.tmp")
    arrow_schema = _arrow_schema_from_flatten(flatten_schema)
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

    exprs: list[pl.Expr] = []
    for key, spec in schema.items():
        full_key = f"{_prefix}.{key}" if _prefix else key
        expr = _base.struct.field(key) if _base is not None else pl.col(key)

        if isinstance(spec, str):
            exprs.append(expr.alias(full_key))
        elif "$max" in spec:
            max_items: int = spec["$max"]
            items_schema = spec.get("$items", {})
            if max_items == 0 or not items_schema:
                continue
            for i in range(max_items):
                idx_key = f"{full_key}.{i + 1}"
                elem = expr.list.get(i, null_on_oob=True)
                if isinstance(items_schema, str):
                    exprs.append(elem.alias(idx_key))
                else:
                    exprs.extend(
                        _build_flatten_exprs(
                            items_schema,
                            _base=elem,
                            _prefix=idx_key,
                        )
                    )
        else:
            exprs.extend(_build_flatten_exprs(spec, _base=expr, _prefix=full_key))
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

    # Infer a consistent schema from a large sample so all chunks
    # parse with identical types (avoids ParquetWriter schema mismatches).
    # Empty or blank-line-only files cause Polars to raise — write an empty
    # parquet and return immediately.
    try:
        ndjson_schema = pl.scan_ndjson(
            path,
            infer_schema_length=_SCHEMA_SAMPLE_SIZE,
        ).collect_schema()
    except pl.exceptions.ComputeError:
        import pyarrow as pa

        pq.write_table(pa.table({}), str(tmp), compression="zstd")
        tmp.replace(dest)
        return 0

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
) -> int:
    """Step 2: Stream raw Parquet → flattened Parquet, one row group at a time.

    Tries Polars expression-based flatten first (fast, Arrow memory).
    Falls back to Python ``flatten()`` per row group if expressions fail
    (e.g. deeply nested mixed-type schemas).

    Returns total rows written.
    """
    import polars as pl
    import pyarrow.parquet as pq

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(dest) + ".tmp")

    exprs = _build_flatten_exprs(flatten_schema)
    pf = pq.ParquetFile(str(raw_path))
    n_groups = pf.metadata.num_row_groups

    if n_groups == 0 or not exprs:
        cols = schema_columns(flatten_schema)
        empty = pl.DataFrame({c: pl.Series([], dtype=pl.String) for c in cols})
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
        total_rows = len(flat0)
        del flat0
        writer = pq.ParquetWriter(str(tmp), at.schema, compression="zstd")
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
# Sample loading (kept for backward compat — UI schema preview, tests)
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
# Parquet cache for flattened JSON — avoids re-flattening on every run
# ---------------------------------------------------------------------------

_CACHE_DIR = ".haute_cache"


def _json_cache_path(data_path: str | Path) -> Path:
    """Compute the parquet cache path for a JSON data file.

    Uses a SHA-256 hash of the stringified path to keep filenames short
    and avoid exceeding Windows MAX_PATH (260 chars).
    """
    import hashlib

    path_hash = hashlib.sha256(str(data_path).encode()).hexdigest()[:32]
    return Path.cwd() / _CACHE_DIR / f"json_{path_hash}.parquet"


def _is_cache_valid(cache_path: Path, *source_paths: Path) -> bool:
    """Return True if cache exists and is newer than all source files."""
    if not cache_path.exists():
        return False
    cache_mtime = cache_path.stat().st_mtime
    for src in source_paths:
        if src.exists() and src.stat().st_mtime > cache_mtime:
            return False
    return True


def _flatten_and_write(
    samples: list[dict[str, Any]],
    schema: dict[str, Any],
    cache_path: Path,
) -> None:
    """Flatten samples and write to a parquet cache file.

    Writes to a temporary file first and atomically renames on success
    so a failed flatten never leaves a corrupt cache behind.
    """
    import polars as pl

    from haute._polars_utils import atomic_write

    with atomic_write(cache_path) as tmp_path:
        rows = [flatten(d, schema) for d in samples]
        df = pl.from_dicts(rows) if rows else pl.DataFrame()
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

    On the first call, flattens the data and writes a parquet cache under
    ``.haute_cache/``.  Subsequent calls return ``pl.scan_parquet()`` directly
    — truly lazy with predicate pushdown.  The cache auto-invalidates when
    the source data file or config file is modified.

    For ``.jsonl`` files, uses the same two-step streaming pipeline as
    :func:`build_json_cache`:

    1. JSONL -> raw Parquet  (Polars/Arrow memory, freed between chunks)
    2. raw Parquet -> flat Parquet  (row-group streaming)

    For ``.json`` files, streams records through PyArrow ``ParquetWriter``
    in chunks (JSON format requires eager parsing anyway).
    """
    import polars as pl

    p = Path(data_path)
    cache_path = _json_cache_path(data_path)

    source_paths = [p]
    if config_path is not None:
        source_paths.append(Path(config_path))

    if _is_cache_valid(cache_path, *source_paths):
        logger.info("json_cache_hit", path=str(data_path), cache=str(cache_path))
        return pl.scan_parquet(cache_path)

    resolved = _resolve_flatten_schema(p, schema, config_path)

    if p.suffix == ".jsonl":
        raw_path = cache_path.with_suffix(".raw.parquet")
        try:
            _jsonl_to_raw_parquet(p, raw_path)
            _flatten_raw_parquet(raw_path, resolved, cache_path)
        finally:
            raw_path.unlink(missing_ok=True)
    else:
        _flatten_and_write_streaming(_iter_json_records(p), resolved, cache_path)

    return pl.scan_parquet(cache_path)


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


def json_cache_info(data_path: str | Path) -> JsonCacheInfoDict | None:
    """Return metadata about a cached JSON file, or ``None`` if not cached."""
    from haute._polars_utils import read_parquet_metadata

    cache_path = _json_cache_path(data_path)
    if not cache_path.exists():
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


def clear_json_cache(data_path: str | Path) -> bool:
    """Delete the cached parquet file for a JSON data file. Returns True if deleted."""
    cache_path = _json_cache_path(data_path)
    if cache_path.exists():
        cache_path.unlink()
        return True
    return False


class JsonBuildResultDict(JsonCacheInfoDict):
    cache_seconds: float


def build_json_cache(
    data_path: str | Path,
    schema: dict[str, Any] | None = None,
    config_path: str | Path | None = None,
) -> JsonBuildResultDict:
    """Build the parquet cache for a JSON/JSONL file with progress tracking.

    This is the explicit entry point for the "Cache as Parquet" button.

    For ``.jsonl`` files, uses a two-step streaming approach to avoid
    Python memory fragmentation:

    1. JSONL → raw Parquet  (Polars/Arrow memory, freed between chunks)
    2. raw Parquet → flat Parquet  (row-group streaming)

    For ``.json`` files, falls back to the Python streaming path (JSON
    format requires eager parsing anyway, and files are typically small).
    """
    p = Path(data_path)
    data_path_str = str(data_path)
    cache_path = _json_cache_path(data_path)

    event = threading.Event()
    t0 = time.monotonic()
    with _flatten_lock:
        _flatten_progress[data_path_str] = {"rows": 0, "elapsed": 0.0}
        _cancel_events[data_path_str] = event

    try:
        resolved = _resolve_flatten_schema(p, schema, config_path)

        if p.suffix == ".jsonl":
            raw_path = cache_path.with_suffix(".raw.parquet")
            try:
                _jsonl_to_raw_parquet(
                    p,
                    raw_path,
                    progress_key=data_path_str,
                    t0=t0,
                    cancel_event=event,
                )
                _flatten_raw_parquet(
                    raw_path,
                    resolved,
                    cache_path,
                    progress_key=data_path_str,
                    t0=t0,
                    cancel_event=event,
                )
            finally:
                raw_path.unlink(missing_ok=True)
        else:
            # .json: must be fully loaded (JSON format constraint)
            _flatten_and_write_streaming(
                _iter_json_records(p),
                resolved,
                cache_path,
                progress_key=data_path_str,
                t0=t0,
                cancel_event=event,
            )
    finally:
        with _flatten_lock:
            _flatten_progress.pop(data_path_str, None)
            _cancel_events.pop(data_path_str, None)

    elapsed = time.monotonic() - t0

    from haute._polars_utils import read_parquet_metadata

    meta = read_parquet_metadata(cache_path)
    return {
        "path": str(cache_path),
        "data_path": data_path_str,
        "row_count": meta["row_count"],
        "column_count": meta["column_count"],
        "columns": meta["columns"],
        "size_bytes": meta["size_bytes"],
        "cached_at": meta["mtime"],
        "cache_seconds": round(elapsed, 2),
    }
